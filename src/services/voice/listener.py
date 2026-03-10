import sys
import os
import re
import io
import json
import queue
import subprocess
import tempfile

# ── Ensure venv site-packages are importable when launched via Python.app ─────
# `open -na Python.app` runs the framework Python (no venv), so we manually
# insert the project venv's site-packages so all third-party deps are found.
import glob as _glob
_venv_site = next(
    iter(_glob.glob(os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")),
        "venv", "lib", "python*", "site-packages"))), None)
if _venv_site and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)
del _glob, _venv_site

try:
    import setproctitle
    setproctitle.setproctitle("Omni Helper (Voice)")
except ImportError:
    pass

import sounddevice as sd
import soundfile as sf
import numpy as np
import logging
import socket
import threading
import time
try:
    import scipy.signal
except ImportError:
    scipy = None
from typing import Optional

# Path to the compiled Swift streaming ASR binary
_SWIFT_ASR_BINARY = os.path.join(os.path.dirname(__file__), "streaming_asr")
NATIVE_ASR_AVAILABLE = os.path.isfile(_SWIFT_ASR_BINARY) and os.access(_SWIFT_ASR_BINARY, os.X_OK)

# Short language code → macOS locale identifier
_LANG_TO_LOCALE = {
    "en": "en-US",
    "pl": "pl-PL",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "pt": "pt-PT",
    "ja": "ja-JP",
    "zh": "zh-CN",
    "uk": "uk-UA",
    "ru": "ru-RU",
    "ar": "ar-SA",
    "nl": "nl-NL",
}

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

try:
    from src.core.config import (
        IPC_PORT,
        OWW_WAKE_WORD_MODEL, OWW_CUSTOM_MODEL_PATH, OWW_DETECTION_THRESHOLD,
        BACKEND_URL, OMNI_SECRET, DEVICE_ID,
    )
except ImportError:
    IPC_PORT = 5556
    OWW_WAKE_WORD_MODEL = "Hey_Omni"
    OWW_CUSTOM_MODEL_PATH = ""
    OWW_DETECTION_THRESHOLD = 0.5
    BACKEND_URL = "https://omni-backend.heyomni.workers.dev"
    OMNI_SECRET = ""
    DEVICE_ID = ""

# --- CONFIGURATION ---
SAMPLE_RATE = 16000
BLOCK_SIZE = 1280          # 80ms - optimal chunk size for openWakeWord
SILENCE_THRESHOLD = 0.003  # slightly higher so brief inter-syllable dips don't trigger silence
SILENCE_DURATION = 1.8   # seconds of sustained silence before triggering transcription
UDP_PORT = 5557

# Wake word trigger: two-tier detection
# Tier 1 (high confidence): single frame above primary threshold → instant trigger
# Tier 2 (lower confidence): multiple frames above secondary threshold → accumulated trigger
OWW_TRIGGER_FRAMES = 1       # how many frames must be above primary threshold
OWW_TRIGGER_WINDOW = 6       # sliding window size (6 × 80ms = 480ms)
OWW_SECONDARY_THRESHOLD_RATIO = 0.6  # secondary threshold = primary × this ratio
OWW_SECONDARY_TRIGGER_FRAMES = 3    # frames needed at secondary (lower) threshold
OWW_COOLDOWN_SECONDS = 3.0   # ignore re-triggers for this long after a detection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VoiceListener")


class VoiceService:
    def __init__(self):
        self.state = {
            "mode": "IDLE",  # IDLE, LISTENING, PAUSED, PROCESSING
            "running": True
        }
        self.audio_queue = queue.Queue()
        self.oww_model = None
        self._oww_key = None   # actual prediction key returned by the model
        self.groq_client = None
        self.audio_buffer = []
        self.is_speaking = False
        self.silence_frames = 0
        self.max_silence_frames = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
        self.energy_history = []
        self.udp_sock = None
        self.native_rate = SAMPLE_RATE
        self._oww_trigger_count = 0  # consecutive frames above threshold
        self._oww_score_window = []   # sliding window of recent scores
        self._oww_last_trigger_time = 0.0  # cooldown timer
        self._oww_last_log_time = 0.0  # for periodic score logging
        self._manual_listening = False  # True when user manually activated mic

        self.setup_models()
        self.setup_udp()

    def setup_models(self):
        """Initialize openWakeWord and set up Groq fallback."""
        if NATIVE_ASR_AVAILABLE:
            logger.info(f"Native streaming ASR available: {_SWIFT_ASR_BINARY}")
        else:
            logger.warning(f"Native ASR binary not found at {_SWIFT_ASR_BINARY} — will use Groq fallback.")

        # 1. openWakeWord
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models

            # Always download base preprocessing models (melspectrogram.onnx etc.)
            # These are required regardless of which wake word model is used.
            logger.info("Ensuring openWakeWord base models are present...")
            download_models()

            custom_path = OWW_CUSTOM_MODEL_PATH if OWW_CUSTOM_MODEL_PATH else ""
            if custom_path and os.path.exists(custom_path):
                logger.info(f"Loading openWakeWord from custom model: '{custom_path}'...")
                self.oww_model = Model(
                    wakeword_models=[custom_path],
                    inference_framework="onnx"
                )
            else:
                logger.info(f"Ensuring openWakeWord model '{OWW_WAKE_WORD_MODEL}' is downloaded...")
                download_models(model_names=[OWW_WAKE_WORD_MODEL])
                logger.info(f"Loading openWakeWord model: '{OWW_WAKE_WORD_MODEL}'...")
                self.oww_model = Model(
                    wakeword_models=[OWW_WAKE_WORD_MODEL],
                    inference_framework="onnx"
                )
            # Resolve the actual prediction key the model uses
            test_pred = self.oww_model.predict(np.zeros(1280, dtype=np.float32))
            self._oww_key = list(test_pred.keys())[0]
            logger.info(f"openWakeWord initialized. Prediction key: '{self._oww_key}'")
        except Exception as e:
            logger.error(f"openWakeWord Init Failed: {e}")
            self.oww_model = None
            self._oww_key = None

        # 2. Groq Whisper via backend proxy (fallback when native ASR is unavailable)
        if OMNI_SECRET:
            self.groq_client = True  # marker — we use requests-based proxy
            logger.info("Groq Whisper fallback transcription ready (via backend proxy).")
        else:
            self.groq_client = None
            logger.info("OMNI_SECRET not set — Groq transcription fallback disabled.")

    def _get_locale_id(self) -> str:
        """Read language preference from settings and return macOS locale ID."""
        try:
            sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
            import src.core.settings_store as settings_store
            lang_code = settings_store.get("transcription_language", "auto")
        except Exception:
            lang_code = "auto"
        if lang_code == "auto" or lang_code not in _LANG_TO_LOCALE:
            import locale as _locale
            system_locale = _locale.getdefaultlocale()[0] or "en_US"
            return system_locale.replace("_", "-")
        return _LANG_TO_LOCALE[lang_code]

    def transcribe_audio_native(self, audio_data: np.ndarray, sample_rate: int) -> Optional[str]:
        """Transcribe audio using the Swift streaming_asr binary with real-time partial results.

        Pipes raw 16-bit PCM audio to the Swift process via stdin. Reads JSON lines
        from stdout for partial/final results. Sends PARTIAL: IPC messages so the UI
        updates the input field in real-time.
        """
        if not NATIVE_ASR_AVAILABLE:
            return None

        locale_id = self._get_locale_id()
        try:
            proc = subprocess.Popen(
                [_SWIFT_ASR_BINARY, "--lang", locale_id],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            logger.error(f"Failed to launch streaming_asr: {e}")
            return None

        final_text = None
        try:
            # Convert float32 audio to int16 PCM bytes
            pcm_int16 = (audio_data * 32767.0).clip(-32768, 32767).astype(np.int16)
            audio_bytes = pcm_int16.tobytes()

            # Write all audio then close stdin to signal EOF
            proc.stdin.write(audio_bytes)
            proc.stdin.close()

            # Read JSON lines from stdout
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"streaming_asr non-JSON: {line}")
                    continue

                msg_type = msg.get("type", "")
                text = msg.get("text", "")

                if msg_type == "partial" and text:
                    logger.info(f"[ASR partial] {text}")
                    self.send_ipc(f"PARTIAL:{text}".encode("utf-8"))
                elif msg_type == "final":
                    final_text = text
                    logger.info(f"[ASR final] {text}")
                elif msg_type == "error":
                    logger.warning(f"[ASR error] {text}")
                elif msg_type == "end":
                    break

            proc.wait(timeout=5.0)
        except Exception as e:
            logger.error(f"streaming_asr error: {e}")
            try:
                proc.kill()
            except Exception:
                pass

        return final_text

    def transcribe_audio_groq(self, audio_data: np.ndarray, sample_rate: int) -> Optional[str]:
        """Transcribe audio using Groq Whisper API via backend proxy."""
        if not self.groq_client:
            return None
        try:
            import io as _io
            import urllib.request
            import urllib.error

            buf = _io.BytesIO()
            sf.write(buf, audio_data, sample_rate, format='wav')
            wav_bytes = buf.getvalue()

            # Build multipart/form-data manually (no external deps)
            boundary = "----OmniAudioBoundary"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="model"\r\n\r\n'
                f"whisper-large-v3-turbo\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="response_format"\r\n\r\n'
                f"text\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                f"Content-Type: audio/wav\r\n\r\n"
            ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()

            req = urllib.request.Request(
                f"{BACKEND_URL}/v1/audio/transcriptions",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "X-Omni-Secret": OMNI_SECRET,
                    "X-Device-ID": DEVICE_ID,
                    "User-Agent": "Omni/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode().strip()
            if not text:
                return None
            # Filter Whisper hallucinations on silence/noise
            _hallucinations = {
                "thank you.", "thanks for watching.", "thanks for watching!",
                "you", "bye.", "bye!", "the end.", "subtitle",
                "subtitles by the amara.org community",
            }
            if text.strip().lower() in _hallucinations:
                logger.info(f"Filtered Whisper hallucination: {text!r}")
                return None
            return text
        except Exception as e:
            logger.error(f"Groq transcription error: {e}")
            return None

    def _transcribe_and_send(self, audio_data: np.ndarray):
        """Run transcription in a background thread and send result via IPC."""
        def _worker():
            duration = len(audio_data) / SAMPLE_RATE
            logger.info(f"Transcribing {duration:.1f}s of audio...")
            text = None
            # Try native macOS ASR first
            if NATIVE_ASR_AVAILABLE:
                text = self.transcribe_audio_native(audio_data, SAMPLE_RATE)
            # Fall back to Groq Whisper
            if not text and self.groq_client:
                logger.info("Falling back to Groq Whisper transcription...")
                text = self.transcribe_audio_groq(audio_data, SAMPLE_RATE)
            if text and text.strip():
                logger.info(f"Transcription: {text!r}")
                self.send_ipc(f"QUERY:VOICE:{text.strip()}".encode('utf-8'))
            else:
                logger.warning("Transcription returned no text.")
        threading.Thread(target=_worker, daemon=True).start()

    def setup_udp(self):
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Do NOT use SO_REUSEADDR — if another listener already holds the
            # port we want the bind to fail so we detect the duplicate.
            self.udp_sock.bind(('127.0.0.1', UDP_PORT))
            self.udp_sock.setblocking(False)
            logger.info(f"UDP Control listening on {UDP_PORT}")
        except OSError as e:
            logger.warning(f"UDP port {UDP_PORT} already in use — another voice listener is running. Exiting.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"UDP Setup Error: {e}")
            self.udp_sock = None

    def audio_callback(self, indata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def process_udp(self):
        if not self.udp_sock:
            return
        try:
            while True:
                data, _ = self.udp_sock.recvfrom(1024)
                msg = data.decode('utf-8').strip()
                logger.info(f"UDP CMD: {msg}")

                if msg == "START_LISTENING":
                    self.set_mode("LISTENING")
                    self.play_cue(active=True)
                elif msg == "STOP_LISTENING":
                    self.set_mode("PAUSED")
                elif msg == "COMMIT_AUDIO":
                    logger.info("Manual Commit Requested — transcribing buffer...")
                    audio_to_transcribe = None
                    if self.audio_buffer:
                        audio_to_transcribe = np.concatenate(self.audio_buffer)
                    self._manual_listening = False
                    self.set_mode("PAUSED")
                    if audio_to_transcribe is not None and len(audio_to_transcribe) > 0:
                        self._transcribe_and_send(audio_to_transcribe)
                    else:
                        logger.warning("COMMIT_AUDIO: buffer was empty — no audio captured.")
                elif msg == "SET_MODE:IDLE":
                    self._manual_listening = False
                    self.set_mode("IDLE")
                elif msg == "SET_MODE:LISTENING":
                    self._manual_listening = True
                    self.set_mode("LISTENING")
                    self.play_cue(active=True)
                elif msg == "SET_MODE:PAUSED":
                    self.set_mode("PAUSED")
        except BlockingIOError:
            pass
        except Exception as e:
            logger.error(f"UDP Error: {e}")

    def set_mode(self, mode):
        if self.state["mode"] != mode:
            logger.info(f"State Change: {self.state['mode']} -> {mode}")
            self.state["mode"] = mode
            self.send_ipc(f"STATUS:{mode}".encode('utf-8'))

            if mode in ("IDLE", "PAUSED"):
                # Clear audio state so we don't replay stale speech into OWW / transcription
                self.audio_buffer = []
                self.is_speaking = False
                self.silence_frames = 0
                self.energy_history = []
                self._oww_trigger_count = 0
                self._oww_score_window = []
                # Reset OWW prediction ring-buffer so residual speech doesn't re-trigger wake word
                if self.oww_model:
                    try:
                        self.oww_model.reset()
                    except Exception:
                        pass

    def send_ipc(self, msg):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(('127.0.0.1', IPC_PORT))
            s.sendall(msg)
            s.close()
            return True
        except Exception:
            return False

    def play_cue(self, active=True):
        """Placeholder for audio cue."""
        pass

    def run(self):
        logger.info("Starting Audio Loop...")

        while self.state["running"]:
            try:
                try:
                    stream = sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        blocksize=BLOCK_SIZE,
                        channels=1,
                        callback=self.audio_callback
                    )
                    self.native_rate = SAMPLE_RATE
                except Exception as e:
                    if "PortAudio" in str(e) or "PaErrorCode" in str(e):
                        logger.warning(f"Default 16kHz failed ({e}). Trying device native rate...")
                        try:
                            dev_info = sd.query_devices(kind='input')
                            self.native_rate = int(dev_info['default_samplerate'])
                            native_block_size = int(BLOCK_SIZE * self.native_rate / SAMPLE_RATE)
                            logger.info(f"Using native rate: {self.native_rate}Hz (Block: {native_block_size})")
                            stream = sd.InputStream(
                                samplerate=self.native_rate,
                                blocksize=native_block_size,
                                channels=1,
                                callback=self.audio_callback
                            )
                        except Exception as e2:
                            logger.error(f"Native rate fallback failed: {e2}")
                            raise e
                    else:
                        raise e

                with stream:
                    logger.info(f"Audio Stream Started ({self.native_rate}Hz).")

                    while self.state["running"]:
                        self.process_udp()

                        try:
                            indata = self.audio_queue.get(timeout=0.1)
                        except queue.Empty:
                            continue

                        mode = self.state["mode"]

                        audio_data = indata.flatten()

                        # Resample if device runs at a different rate
                        if self.native_rate != SAMPLE_RATE:
                            if scipy:
                                try:
                                    num_samples = int(len(audio_data) * SAMPLE_RATE / self.native_rate)
                                    audio_data = scipy.signal.resample(audio_data, num_samples)
                                except Exception as e:
                                    logger.error(f"Resampling failed: {e}")
                            else:
                                ratio = self.native_rate / SAMPLE_RATE
                                if float(ratio).is_integer():
                                    audio_data = audio_data[::int(ratio)]

                        if mode == "IDLE":
                            self.handle_idle(audio_data)
                        elif mode == "LISTENING":
                            self.handle_listening(audio_data)
                        elif mode == "PAUSED":
                            # Wake word in PAUSED = follow-up while window is open
                            self.handle_idle(audio_data, paused_mode=True)

            except Exception as e:
                logger.error(f"Audio Stream Error: {e}")
                if "PortAudio" in str(e) or "PaErrorCode" in str(e):
                    logger.warning("PortAudio conflict detected. Waiting longer...")
                    time.sleep(5)
                logger.info("Restarting audio stream in 2 seconds...")
                time.sleep(2)

    def handle_idle(self, audio_data: np.ndarray, paused_mode: bool = False):
        """Wake word detection using openWakeWord with two-tier sensitivity."""
        if not self.oww_model or not self._oww_key:
            return

        try:
            # Normalize audio volume to compensate for quiet mics / varying gain
            peak = np.max(np.abs(audio_data))
            if peak > 0.001:
                # Normalize to ~70% of full range — avoids clipping while boosting quiet input
                audio_normalized = audio_data * (0.7 / peak)
            else:
                audio_normalized = audio_data

            # openWakeWord expects int16 PCM audio (as shown in all official examples)
            audio_int16 = (audio_normalized * 32767.0).clip(-32768, 32767).astype(np.int16)
            prediction = self.oww_model.predict(audio_int16)
            score = prediction.get(self._oww_key, 0.0)

            # Periodic score logging so we can see detection is working
            now = time.time()
            if now - self._oww_last_log_time >= 3.0:
                self._oww_last_log_time = now
                logger.info(f"[WakeWord] current score: {score:.4f} (threshold={OWW_DETECTION_THRESHOLD})")

            # Sliding window: track recent scores
            self._oww_score_window.append(score)
            if len(self._oww_score_window) > OWW_TRIGGER_WINDOW:
                self._oww_score_window.pop(0)

            # Two-tier detection:
            # Tier 1: primary threshold — high confidence, needs fewer hits
            primary_hits = sum(1 for s in self._oww_score_window if s >= OWW_DETECTION_THRESHOLD)
            # Tier 2: secondary (lower) threshold — lower confidence, needs more hits
            secondary_threshold = OWW_DETECTION_THRESHOLD * OWW_SECONDARY_THRESHOLD_RATIO
            secondary_hits = sum(1 for s in self._oww_score_window if s >= secondary_threshold)

            triggered = (primary_hits >= OWW_TRIGGER_FRAMES or
                         secondary_hits >= OWW_SECONDARY_TRIGGER_FRAMES)

            if score >= secondary_threshold:
                logger.info(f"[WakeWord] score: {score:.3f} | primary {primary_hits}/{OWW_TRIGGER_FRAMES} | secondary {secondary_hits}/{OWW_SECONDARY_TRIGGER_FRAMES}")

            if triggered:
                # Cooldown: don't re-trigger too quickly after a recent detection
                if now - self._oww_last_trigger_time < OWW_COOLDOWN_SECONDS:
                    return

                tier = "primary" if primary_hits >= OWW_TRIGGER_FRAMES else "secondary"
                logger.info(f"Wake Word Detected! ({tier}, score={score:.3f}, primary_hits={primary_hits}, secondary_hits={secondary_hits})")
                self._oww_last_trigger_time = now
                self._oww_score_window = []
                self._oww_trigger_count = 0
                self._manual_listening = False
                self.set_mode("LISTENING")
                self.play_cue(active=True)
                if paused_mode:
                    # Window already visible — just activate mic for follow-up, don't toggle
                    pass
                else:
                    # Window hidden — show it
                    self.send_ipc(b"TOGGLE")

        except Exception as e:
            logger.error(f"Wake word detection error: {e}")

    def handle_listening(self, audio_data: np.ndarray):
        # 1. Accumulate
        self.audio_buffer.append(audio_data)

        # 2. Energy-based VAD with rolling average
        energy = np.sqrt(np.mean(audio_data ** 2))
        self.energy_history.append(energy)
        if len(self.energy_history) > 6:  # ~480ms smoothing — bridges inter-syllable pauses
            self.energy_history.pop(0)
        avg_energy = sum(self.energy_history) / len(self.energy_history)

        if avg_energy > SILENCE_THRESHOLD:
            if not self.is_speaking:
                logger.info(f"Speech detected (energy={avg_energy:.5f})...")
                self.is_speaking = True
            self.silence_frames = 0
        else:
            if self.is_speaking:
                self.silence_frames += 1
            else:
                # When user manually activated the mic, keep all audio (don't trim).
                # For wake-word-activated listening, keep up to 3s of pre-speech context.
                if not self._manual_listening:
                    max_pre_speech_blocks = int(3.0 * SAMPLE_RATE / BLOCK_SIZE)
                    if len(self.audio_buffer) > max_pre_speech_blocks:
                        self.audio_buffer = self.audio_buffer[-max_pre_speech_blocks:]

        # 3. End-of-utterance detection
        if self.is_speaking and self.silence_frames > self.max_silence_frames:
            logger.info("End of utterance detected.")
            audio_to_transcribe = np.concatenate(self.audio_buffer) if self.audio_buffer else None
            self.audio_buffer = []
            self.is_speaking = False
            self.silence_frames = 0
            self.set_mode("PAUSED")
            if audio_to_transcribe is not None and len(audio_to_transcribe) > 0:
                self._transcribe_and_send(audio_to_transcribe)

        # 4. Safety timeout at 15s
        if len(self.audio_buffer) * BLOCK_SIZE > 15 * SAMPLE_RATE:
            logger.warning("Max duration reached.")
            audio_to_transcribe = np.concatenate(self.audio_buffer) if self.audio_buffer else None
            self.audio_buffer = []
            self.is_speaking = False
            self.silence_frames = 0
            self.set_mode("PAUSED")
            if audio_to_transcribe is not None and len(audio_to_transcribe) > 0:
                self._transcribe_and_send(audio_to_transcribe)



if __name__ == "__main__":
    service = VoiceService()
    try:
        service.run()
    except KeyboardInterrupt:
        pass
