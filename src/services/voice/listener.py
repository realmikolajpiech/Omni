import sys
import os
import re
import io
import queue
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


def _check_speech_entitlement() -> bool:
    """Return True only when native macOS SFSpeechRecognizer is safe to use.

    macOS TCC enforces privacy entitlements against the *responsible process*
    (the app that spawned this process's coalition), NOT the running binary.
    When launched from Electron, Terminal, or any non-Python-signed app, the
    responsible process is that parent — which lacks
    NSSpeechRecognitionUsageDescription — causing an unrecoverable SIGABRT.

    Because reliably detecting the responsible process is fragile, we default
    to DISABLED native ASR and use the Groq Whisper API instead (works
    perfectly, no TCC required).  Set OMNI_NATIVE_ASR=1 to explicitly opt-in
    if you have a properly signed/entitled app bundle.
    """
    if os.environ.get("OMNI_NATIVE_ASR", "0") != "1":
        return False
    try:
        import plistlib
        from Foundation import NSBundle
        bundle_path = str(NSBundle.mainBundle().bundlePath())
        plist_path = os.path.join(bundle_path, "Contents", "Info.plist")
        if not os.path.exists(plist_path):
            return False
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
        return "NSSpeechRecognitionUsageDescription" in data
    except Exception:
        return False


# Native macOS Speech Recognition — disabled by default to avoid TCC SIGABRT crash.
# Falls back to Groq Whisper API for transcription (reliable, no permissions needed).
# Set OMNI_NATIVE_ASR=1 to explicitly enable native ASR.
NATIVE_ASR_AVAILABLE = False
SFSpeechRecognizer = None
SFSpeechURLRecognitionRequest = None
NSURL = None
NSLocale = None

if _check_speech_entitlement():
    try:
        from Speech import SFSpeechRecognizer, SFSpeechURLRecognitionRequest
        from Foundation import NSURL, NSLocale
        NATIVE_ASR_AVAILABLE = True
    except ImportError:
        pass

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
        GROQ_API_KEY,
    )
except ImportError:
    IPC_PORT = 5556
    OWW_WAKE_WORD_MODEL = "Hey_Omni"
    OWW_CUSTOM_MODEL_PATH = ""
    OWW_DETECTION_THRESHOLD = 0.5
    GROQ_API_KEY = ""

# --- CONFIGURATION ---
SAMPLE_RATE = 16000
BLOCK_SIZE = 1280          # 80ms - optimal chunk size for openWakeWord
SILENCE_THRESHOLD = 0.003  # slightly higher so brief inter-syllable dips don't trigger silence
SILENCE_DURATION = 1.8   # seconds of sustained silence before triggering transcription
UDP_PORT = 5557

# Wake word trigger: require N frames above threshold within a sliding window
# (not necessarily consecutive — natural speech scores fluctuate frame-to-frame)
OWW_TRIGGER_FRAMES = 2       # how many frames must be above threshold
OWW_TRIGGER_WINDOW = 4       # within this many recent frames
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

        self.setup_models()
        self.setup_udp()

    def setup_models(self):
        """Initialize openWakeWord, request speech recognition authorization, and set up Groq fallback."""
        # 0. Request macOS speech recognition authorization early
        if NATIVE_ASR_AVAILABLE:
            self._request_speech_auth()
        else:
            logger.warning("Native ASR disabled — no NSSpeechRecognitionUsageDescription entitlement.")

        # 1. openWakeWord
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models

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

        # 2. Groq Whisper (fallback transcription when native ASR is unavailable)
        try:
            from groq import Groq as _GroqClient
            if GROQ_API_KEY:
                self.groq_client = _GroqClient(api_key=GROQ_API_KEY)
                logger.info("Groq Whisper fallback transcription ready.")
            else:
                logger.info("GROQ_API_KEY not set — Groq transcription fallback disabled.")
        except ImportError:
            logger.info("groq package not installed — Groq transcription fallback disabled.")

    def _request_speech_auth(self):
        """Ask macOS for SFSpeechRecognizer permission (shows system dialog on first run)."""
        try:
            auth_event = threading.Event()
            def _handler(status):
                # 3 = SFSpeechRecognizerAuthorizationStatusAuthorized
                if status == 3:
                    logger.info("Speech recognition: authorized.")
                else:
                    logger.warning(f"Speech recognition authorization status: {status}")
                auth_event.set()
            SFSpeechRecognizer.requestAuthorization_(_handler)
            auth_event.wait(timeout=30.0)
        except Exception as e:
            logger.error(f"Speech auth request error: {e}")

    def transcribe_audio_native(self, audio_data: np.ndarray, sample_rate: int) -> Optional[str]:
        """Transcribe audio array using native macOS SFSpeechRecognizer."""
        if not NATIVE_ASR_AVAILABLE:
            return None
        try:
            # Read language preference
            try:
                sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
                import src.core.settings_store as settings_store
                lang_code = settings_store.get("transcription_language", "auto")
            except Exception:
                lang_code = "auto"

            if lang_code == "auto" or lang_code not in _LANG_TO_LOCALE:
                # Use system locale as best proxy
                locale = NSLocale.currentLocale()
            else:
                locale_id = _LANG_TO_LOCALE[lang_code]
                locale = NSLocale.localeWithLocaleIdentifier_(locale_id)

            recognizer = SFSpeechRecognizer.alloc().initWithLocale_(locale)
            if not recognizer or not recognizer.isAvailable():
                # Fallback to English
                logger.warning("SFSpeechRecognizer not available for selected locale, falling back to en-US")
                en_locale = NSLocale.localeWithLocaleIdentifier_("en-US")
                recognizer = SFSpeechRecognizer.alloc().initWithLocale_(en_locale)
                if not recognizer or not recognizer.isAvailable():
                    logger.error("SFSpeechRecognizer not available at all.")
                    return None

            # Write audio to a temp WAV file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                temp_path = f.name
            sf.write(temp_path, audio_data, sample_rate)

            result_container = [None]
            done_event = threading.Event()

            url = NSURL.fileURLWithPath_(temp_path)
            request = SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)
            request.setShouldReportPartialResults_(False)

            def _result_handler(result, error):
                if error:
                    logger.error(f"SFSpeechRecognizer error: {error}")
                if result and result.isFinal():
                    result_container[0] = result.bestTranscription().formattedString()
                done_event.set()

            recognizer.recognitionTaskWithRequest_resultHandler_(request, _result_handler)
            done_event.wait(timeout=15.0)

            try:
                os.unlink(temp_path)
            except Exception:
                pass

            return result_container[0]

        except Exception as e:
            logger.error(f"Native macOS transcription error: {e}")
            return None

    def transcribe_audio_groq(self, audio_data: np.ndarray, sample_rate: int) -> Optional[str]:
        """Transcribe audio using Groq Whisper API (fallback when native ASR unavailable)."""
        if not self.groq_client:
            return None
        try:
            import io as _io
            buf = _io.BytesIO()
            sf.write(buf, audio_data, sample_rate, format='wav')
            buf.seek(0)
            response = self.groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.wav", buf),
                response_format="text",
            )
            return response.strip() if isinstance(response, str) else response.text.strip()
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
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind(('127.0.0.1', UDP_PORT))
            self.udp_sock.setblocking(False)
            logger.info(f"UDP Control listening on {UDP_PORT}")
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
                    self.set_mode("PAUSED")
                    if audio_to_transcribe is not None and len(audio_to_transcribe) > 0:
                        self._transcribe_and_send(audio_to_transcribe)
                elif msg == "SET_MODE:IDLE":
                    self.set_mode("IDLE")
                elif msg == "SET_MODE:LISTENING":
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
                            # Wake word also in PAUSED so "Alexa" works with window open (same-chat follow-up)
                            self.handle_idle(audio_data)

            except Exception as e:
                logger.error(f"Audio Stream Error: {e}")
                if "PortAudio" in str(e) or "PaErrorCode" in str(e):
                    logger.warning("PortAudio conflict detected. Waiting longer...")
                    time.sleep(5)
                logger.info("Restarting audio stream in 2 seconds...")
                time.sleep(2)

    def handle_idle(self, audio_data: np.ndarray):
        """Wake word detection using openWakeWord."""
        if not self.oww_model or not self._oww_key:
            return

        try:
            # openWakeWord expects int16 PCM audio (as shown in all official examples)
            audio_int16 = (audio_data * 32767.0).clip(-32768, 32767).astype(np.int16)
            prediction = self.oww_model.predict(audio_int16)
            score = prediction.get(self._oww_key, 0.0)

            # Periodic score logging so we can see detection is working
            now = time.time()
            if now - self._oww_last_log_time >= 3.0:
                self._oww_last_log_time = now
                logger.info(f"[WakeWord] current score: {score:.4f} (threshold={OWW_DETECTION_THRESHOLD})")

            # Sliding window: track recent scores and count how many exceed threshold
            self._oww_score_window.append(score)
            if len(self._oww_score_window) > OWW_TRIGGER_WINDOW:
                self._oww_score_window.pop(0)

            hits = sum(1 for s in self._oww_score_window if s >= OWW_DETECTION_THRESHOLD)

            if score >= OWW_DETECTION_THRESHOLD:
                logger.info(f"[WakeWord] above threshold: {score:.3f} (hits {hits}/{OWW_TRIGGER_FRAMES} in last {OWW_TRIGGER_WINDOW} frames)")

            if hits >= OWW_TRIGGER_FRAMES:
                # Cooldown: don't re-trigger too quickly after a recent detection
                if now - self._oww_last_trigger_time < OWW_COOLDOWN_SECONDS:
                    return

                logger.info(f"Wake Word Detected! (score={score:.3f}, hits={hits})")
                self._oww_last_trigger_time = now
                self._oww_score_window = []
                self._oww_trigger_count = 0
                self.set_mode("LISTENING")
                self.play_cue(active=True)
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
                # Keep only the last 1 second of pre-speech buffer
                max_pre_speech_blocks = int(1.0 * SAMPLE_RATE / BLOCK_SIZE)
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
