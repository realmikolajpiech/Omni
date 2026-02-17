import sys
import os
import re
import queue
import sounddevice as sd
import numpy as np
import logging
import socket
import torch
import threading
import json
import time
import zipfile
import urllib.request
try:
    import scipy.signal
except ImportError:
    scipy = None
from typing import Optional, List

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

try:
    from src.core.config import (
        ASR_MODEL_ID, IPC_PORT, VOSK_MODEL_PATH, VOSK_MODEL_URL, 
        VOSK_MODEL_NAME, MODEL_DIR
    )
except ImportError:
    # Fallback if config not found (standalone run)
    IPC_PORT = 5556
    ASR_MODEL_ID = "Qwen/Qwen3-ASR-0.6B"
    MODEL_DIR = os.path.expanduser("~/.local/share/ai-models")
    VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
    VOSK_MODEL_PATH = os.path.join(MODEL_DIR, VOSK_MODEL_NAME)
    VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

# --- CONFIGURATION ---
WAKE_WORDS = ["hey omni"]
SAMPLE_RATE = 16000
BLOCK_SIZE = 4000  # 0.25s
SILENCE_THRESHOLD = 0.001 # Less sensitive to ignore background noise
SILENCE_DURATION = 2.0 # Wait 2s before cutting off
UDP_PORT = 5557
VAD_SENSITIVITY = 0.5 # For future use

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("VoiceListener")

class VoiceService:
    def __init__(self):
        self.state = {
            "mode": "IDLE", # IDLE, LISTENING, PAUSED, PROCESSING
            "running": True
        }
        self.audio_queue = queue.Queue()
        self.vosk_model = None
        self.vosk_rec_wake = None # Recognizer for Wake Word
        self.asr_pipeline = None
        self.audio_buffer = []
        self.is_speaking = False
        self.silence_frames = 0
        self.max_silence_frames = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
        self.energy_history = []
        self.udp_sock = None
        self.native_rate = SAMPLE_RATE
        
        self.setup_models()
        self.setup_udp()
        
    def setup_models(self):
        """Initialize Vosk (Wake Word) and Qwen (ASR)"""
        # 1. Setup Vosk
        if not os.path.exists(VOSK_MODEL_PATH):
            self.download_vosk()
            
        try:
            from vosk import Model, KaldiRecognizer
            if os.path.exists(VOSK_MODEL_PATH):
                self.vosk_model = Model(VOSK_MODEL_PATH)
                # Wake Word Grammar - significantly improves performance and reduces false positives
                grammar = json.dumps(WAKE_WORDS + ["[unk]"])
                self.vosk_rec_wake = KaldiRecognizer(self.vosk_model, SAMPLE_RATE, grammar)
                logger.info("Vosk Wake Word Engine initialized.")
            else:
                logger.error("Vosk model path invalid.")
        except Exception as e:
            logger.error(f"Vosk Init Failed: {e}")

        # 2. Setup ASR (Transformers / Whisper)
        logger.info(f"Loading ASR Model: {ASR_MODEL_ID}...")
        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if sys.platform == "darwin" and torch.backends.mps.is_available():
                device = "mps"
            
            logger.info(f"Using device: {device}")
            
            from transformers import pipeline
            self.asr_pipeline = pipeline(
                "automatic-speech-recognition",
                model=ASR_MODEL_ID,
                device=device,
                torch_dtype=torch.float16 if device != "cpu" else torch.float32
            )
            logger.info("ASR Model initialized.")
        except Exception as e:
            logger.error(f"ASR Init Failed: {e}")
            self.asr_pipeline = None
            
    def download_vosk(self):
        logger.info(f"Downloading Vosk Model to {VOSK_MODEL_PATH}...")
        os.makedirs(MODEL_DIR, exist_ok=True)
        zip_path = os.path.join(MODEL_DIR, "vosk.zip")
        try:
            urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(MODEL_DIR)
            logger.info("Vosk Model Downloaded.")
        except Exception as e:
            logger.error(f"Download Error: {e}")
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)

    def setup_udp(self):
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Allow address reuse to help with quick restarts
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind(('127.0.0.1', UDP_PORT))
            self.udp_sock.setblocking(False)
            logger.info(f"UDP Control listening on {UDP_PORT}")
        except Exception as e:
            logger.error(f"UDP Setup Error: {e}")
            self.udp_sock = None # Ensure it's None if failed

    def audio_callback(self, indata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def process_udp(self):
        if not self.udp_sock: return
        
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
            
            if mode == "IDLE":
                # Reset buffer
                self.audio_buffer = []
                self.is_speaking = False
                # Reset Vosk recognizer for fresh wake word detection
                if self.vosk_rec_wake:
                    self.vosk_rec_wake.Reset()

    def send_ipc(self, msg):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0) # Timeout for IPC
            s.connect(('127.0.0.1', IPC_PORT))
            s.sendall(msg)
            s.close()
            return True
        except Exception as e:
            # logger.error(f"IPC Error: {e}") # Reduce noise
            return False

    def play_cue(self, active=True):
        """Play a subtle synthesized beep instead of system sound"""
        pass

    def run(self):
        logger.info("Starting Audio Loop...")
        
        while self.state["running"]:
            try:
                # Open stream
                # Try opening stream with default 16kHz
                try:
                    stream = sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, 
                                        channels=1, callback=self.audio_callback)
                    self.native_rate = SAMPLE_RATE
                except Exception as e:
                    if "PortAudio" in str(e) or "PaErrorCode" in str(e):
                        logger.warning(f"Default 16kHz failed ({e}). Trying device native rate...")
                        try:
                            dev_info = sd.query_devices(kind='input')
                            self.native_rate = int(dev_info['default_samplerate'])
                            # Adjust block size to maintain duration roughly same (0.25s)
                            native_block_size = int(BLOCK_SIZE * self.native_rate / SAMPLE_RATE)
                            logger.info(f"Using native rate: {self.native_rate}Hz (Block: {native_block_size})")
                            
                            stream = sd.InputStream(samplerate=self.native_rate, blocksize=native_block_size, 
                                                channels=1, callback=self.audio_callback)
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
                        
                        # Check Mode
                        mode = self.state["mode"]
                        
                        if mode == "PAUSED":
                            continue
                        
                        # Pre-processing (Normalization / Boost)
                        # Boost removed/reduced to avoid distortion for Whisper
                        audio_data = indata.flatten()
                        
                        # Resample if needed
                        if self.native_rate != SAMPLE_RATE:
                            if scipy:
                                try:
                                    num_samples = int(len(audio_data) * SAMPLE_RATE / self.native_rate)
                                    audio_data = scipy.signal.resample(audio_data, num_samples)
                                except Exception as e:
                                    logger.error(f"Resampling failed: {e}")
                            else:
                                # Fallback: simple decimation if integer ratio, else fail gracefully
                                ratio = self.native_rate / SAMPLE_RATE
                                if ratio.is_integer():
                                    step = int(ratio)
                                    audio_data = audio_data[::step]
                        
                        if mode == "IDLE":
                            self.handle_idle(audio_data)
                        elif mode == "LISTENING":
                            self.handle_listening(audio_data)

            except Exception as e:
                logger.error(f"Audio Stream Error: {e}")
                # Don't spam restarts on permanent failures
                if "PortAudio" in str(e) or "PaErrorCode" in str(e):
                     logger.warning("PortAudio conflict detected. Waiting longer...")
                     time.sleep(5)
                logger.info("Restarting audio stream in 2 seconds...")
                time.sleep(2)

    def handle_idle(self, audio_data):
        if not self.vosk_rec_wake: return
        
        # Vosk expects int16 bytes
        # Audio is float32 normalized (-1 to 1) usually.
        # Scale to int16
        audio_int16 = (audio_data * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        
        if self.vosk_rec_wake.AcceptWaveform(audio_int16):
            res = json.loads(self.vosk_rec_wake.Result())
            text = res.get("text", "")
            if text:
                logger.info(f"Wake Word Logic Checked: '{text}'")
                # Since grammar is restricted, any result is likely a wake word
                if any(w in text for w in WAKE_WORDS):
                    logger.info("Wake Word Detected!")
                    self.set_mode("LISTENING")
                    self.play_cue(active=True)
                    self.send_ipc(b"TOGGLE") # Show UI

    def handle_listening(self, audio_data):
        # 1. Accumulate
        self.audio_buffer.append(audio_data)
        
        # 2. VAD (Energy based with rolling average)
        energy = np.sqrt(np.mean(audio_data**2))
        
        self.energy_history.append(energy)
        if len(self.energy_history) > 5: # 0.5s smoothing window
             self.energy_history.pop(0)
        
        avg_energy = sum(self.energy_history) / len(self.energy_history)
        
        if avg_energy > SILENCE_THRESHOLD:
            if not self.is_speaking:
                logger.info(f"Speech detected (Energy: {avg_energy:.5f})...")
                self.is_speaking = True
            self.silence_frames = 0
        else:
            if self.is_speaking:
                self.silence_frames += 1
            else:
                # We are in silence and NOT speaking yet.
                # Limit buffer to keep only recent history (e.g. 1s) to prevent "Max duration" on long silence
                # BLOCK_SIZE = 4000, SAMPLE_RATE = 16000. 1s = 4 blocks.
                max_pre_speech_blocks = int(1.0 * SAMPLE_RATE / BLOCK_SIZE)
                if len(self.audio_buffer) > max_pre_speech_blocks:
                    self.audio_buffer = self.audio_buffer[-max_pre_speech_blocks:]
        
        # 3. Check End of Utterance
        if self.is_speaking and self.silence_frames > self.max_silence_frames:
            logger.info("End of utterance detected. Transcribing...")
            self.process_buffer()
            
        # 4. Timeout / Buffer Limit (15s)
        if len(self.audio_buffer) * BLOCK_SIZE > 15 * SAMPLE_RATE:
             logger.warning("Max duration reached. Transcribing...")
             self.process_buffer()

    def process_buffer(self):
        if not self.audio_buffer: return
        
        full_audio = np.concatenate(self.audio_buffer)
        self.audio_buffer = []
        self.is_speaking = False
        self.silence_frames = 0
        
        # Ignore short audio (< 0.2s) - Reduced from 0.5s to capture short commands like "stop"
        if len(full_audio) < SAMPLE_RATE * 0.2:
            return

        if self.asr_pipeline:
            try:
                # Transcribe
                # Convert buffer to float32 numpy array as expected by transformers
                # full_audio is already float32 from sounddevice (default)
                
                # Smart Normalization (Avoid boosting noise)
                max_val = np.max(np.abs(full_audio))
                if max_val > 0:
                    # If peak is already decent (>0.5), leave it or just clamp
                    # If peak is low (but not silence), boost it
                    # If peak is noise floor (<0.02), DO NOT boost to 1.0
                    
                    if max_val < 0.9:
                        if max_val > 0.02: # Signal exists
                            target = 0.9
                            gain = min(target / max_val, 3.0) # Cap gain at 3x to avoid boosting noise
                            full_audio = full_audio * gain
                            logger.info(f"Audio boosted by {gain:.2f}x")
                        else:
                            logger.info("Audio too quiet/noise, skipping boost.")
                
                # Use generate_kwargs if needed.
                # Explicitly setting language to None to force auto-detection
                # and ensuring task is transcribe.
                # We can also try increasing beam size for better accuracy.
                result = self.asr_pipeline(
                    full_audio, 
                    generate_kwargs={
                        "task": "transcribe",
                        "num_beams": 1, # Faster, less hallucinations
                        "condition_on_prev_tokens": False,
                        "temperature": 0.0 # Greedy decoding for accuracy
                    }
                )
                text = result.get("text", "").strip()
                logger.info(f"Transcribed: {text}")
                
                if text:
                    # Clean up common hallucinations
                    hallucinations = [
                        "i", "i!", "i.", "you", "thanks", "thank you", "bye", ".", "...!", "...",
                        "..!", "...?", "?", "!", "you.", "thank you.", "subtitles by", "mbc",
                        "o!", "o.", "oh!", "oh.", "o", "oh", "a", "a!", "a.", "ok", "ok."
                    ]
                    
                    if text.lower() in hallucinations:
                        logger.info(f"Ignored hallucination: {text}")
                        return

                    # Ensure text contains at least one alphanumeric character
                    if not re.search(r'[a-zA-Z0-9]', text):
                        logger.info(f"Ignored non-alphanumeric text: {text}")
                        return

                    # Mark this query as coming from voice
                    # Only if text length > 1
                    if len(text) > 1 and not text.startswith("..."):
                        self.send_ipc(f"QUERY:VOICE:{text}".encode('utf-8'))
                        self.play_cue(active=False) # Confirmation beep
                        self.set_mode("PAUSED") # Stop listening after command
                    else:
                         logger.info(f"Ignored too short text: {text}")
            except Exception as e:
                logger.error(f"Transcription Failed: {e}")
        else:
            logger.error("ASR Model not loaded!")

if __name__ == "__main__":
    service = VoiceService()
    try:
        service.run()
    except KeyboardInterrupt:
        pass
