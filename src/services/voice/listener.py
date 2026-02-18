import sys
import os
import re
import io
import queue
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

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

try:
    from src.core.config import (
        IPC_PORT, GROQ_API_KEY, GROQ_WHISPER_MODEL,
        OWW_WAKE_WORD_MODEL, OWW_CUSTOM_MODEL_PATH, OWW_DETECTION_THRESHOLD
    )
except ImportError:
    IPC_PORT = 5556
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"
    OWW_WAKE_WORD_MODEL = "Hey_Omni"
    OWW_CUSTOM_MODEL_PATH = ""
    OWW_DETECTION_THRESHOLD = 0.5

# --- CONFIGURATION ---
SAMPLE_RATE = 16000
BLOCK_SIZE = 1280          # 80ms - optimal chunk size for openWakeWord
SILENCE_THRESHOLD = 0.003  # slightly higher so brief inter-syllable dips don't trigger silence
SILENCE_DURATION = 1.8   # seconds of sustained silence before triggering transcription
UDP_PORT = 5557

# Minimum consecutive frames above threshold before wake word fires (debounce)
OWW_TRIGGER_FRAMES = 2

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
        self._oww_last_log_time = 0.0  # for periodic score logging

        self.setup_models()
        self.setup_udp()

    def setup_models(self):
        """Initialize openWakeWord and Groq client."""
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

        # 2. Groq client for Whisper transcription
        if not GROQ_API_KEY:
            logger.error("GROQ_API_KEY not set - transcription will not work!")
        try:
            from groq import Groq
            self.groq_client = Groq(api_key=GROQ_API_KEY)
            logger.info(f"Groq client initialized (model: {GROQ_WHISPER_MODEL}).")
        except Exception as e:
            logger.error(f"Groq Init Failed: {e}")
            self.groq_client = None

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
                    logger.info("Manual Commit Requested")
                    if self.state["mode"] == "LISTENING":
                        self.process_buffer()
                    self.set_mode("PAUSED")
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

            if score >= OWW_DETECTION_THRESHOLD:
                self._oww_trigger_count += 1
                logger.info(f"[WakeWord] above threshold: {score:.3f} (frame {self._oww_trigger_count}/{OWW_TRIGGER_FRAMES})")
                if self._oww_trigger_count >= OWW_TRIGGER_FRAMES:
                    logger.info(f"Wake Word Detected! (score={score:.3f})")
                    self._oww_trigger_count = 0
                    self.set_mode("LISTENING")
                    self.play_cue(active=True)
                    self.send_ipc(b"TOGGLE")
            else:
                self._oww_trigger_count = 0

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
            logger.info("End of utterance detected. Transcribing...")
            self.process_buffer()

        # 4. Safety timeout at 15s
        if len(self.audio_buffer) * BLOCK_SIZE > 15 * SAMPLE_RATE:
            logger.warning("Max duration reached. Transcribing...")
            self.process_buffer()

    def process_buffer(self):
        if not self.audio_buffer:
            return

        full_audio = np.concatenate(self.audio_buffer)
        self.audio_buffer = []
        self.is_speaking = False
        self.silence_frames = 0

        # Ignore clips shorter than 0.2s
        if len(full_audio) < SAMPLE_RATE * 0.2:
            return

        if not self.groq_client:
            logger.error("Groq client not initialized - cannot transcribe.")
            return

        # Smart normalization: boost quiet signal but cap gain to avoid amplifying noise
        max_val = np.max(np.abs(full_audio))
        if max_val > 0:
            if 0.02 < max_val < 0.9:
                gain = min(0.9 / max_val, 3.0)
                full_audio = full_audio * gain
                logger.info(f"Audio boosted {gain:.2f}x")
            elif max_val <= 0.02:
                logger.info("Audio too quiet, skipping boost.")

        # Run transcription in a thread so audio loop stays responsive
        audio_snapshot = full_audio.copy()
        thread = threading.Thread(target=self._transcribe_and_send, args=(audio_snapshot,), daemon=True)
        thread.start()

    def _transcribe_and_send(self, full_audio: np.ndarray):
        """Send audio to Groq Whisper API and dispatch result via IPC."""
        try:
            # Encode audio as WAV in-memory (PCM 16-bit)
            wav_buffer = io.BytesIO()
            sf.write(wav_buffer, full_audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
            wav_buffer.seek(0)

            logger.info(f"Sending {len(full_audio)/SAMPLE_RATE:.2f}s audio to Groq Whisper ({GROQ_WHISPER_MODEL})...")
            transcription = self.groq_client.audio.transcriptions.create(
                file=("audio.wav", wav_buffer.read()),
                model=GROQ_WHISPER_MODEL,
                response_format="text",
            )

            # Groq returns the text directly as a string when response_format="text"
            text = transcription.strip() if isinstance(transcription, str) else (transcription.text or "").strip()
            logger.info(f"Transcribed: {text}")

            if not text:
                return

            # Filter common Whisper hallucinations
            hallucinations = {
                "i", "i!", "i.", "you", "thanks", "thank you", "bye", ".", "...!",
                "...", "..!", "...?", "?", "!", "you.", "thank you.", "subtitles by",
                "mbc", "o!", "o.", "oh!", "oh.", "o", "oh", "a", "a!", "a.", "ok", "ok."
            }
            if text.lower() in hallucinations:
                logger.info(f"Ignored hallucination: '{text}'")
                return

            if not re.search(r'[a-zA-Z0-9]', text):
                logger.info(f"Ignored non-alphanumeric: '{text}'")
                return

            if len(text) > 1 and not text.startswith("..."):
                self.send_ipc(f"QUERY:VOICE:{text}".encode('utf-8'))
                self.play_cue(active=False)
                self.set_mode("PAUSED")
            else:
                logger.info(f"Ignored too-short text: '{text}'")

        except Exception as e:
            logger.error(f"Transcription Failed: {e}")


if __name__ == "__main__":
    service = VoiceService()
    try:
        service.run()
    except KeyboardInterrupt:
        pass
