import sys
import os
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
WAKE_WORDS = ["hey omni", "omni", "computer", "hey computer"]
SAMPLE_RATE = 16000
BLOCK_SIZE = 4000  # 0.25s
SILENCE_THRESHOLD = 0.005 # Adjusted from 0.00002 which was too low
SILENCE_DURATION = 1.0 # Seconds of silence to consider end of utterance
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
        self.qwen_model = None
        self.audio_buffer = []
        self.is_speaking = False
        self.silence_frames = 0
        self.max_silence_frames = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
        self.udp_sock = None
        
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

        # 2. Setup Qwen ASR
        logger.info(f"Loading Qwen ASR: {ASR_MODEL_ID}...")
        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if sys.platform == "darwin" and torch.backends.mps.is_available():
                device = "mps"
            
            logger.info(f"Using device: {device}")
            
            from qwen_asr.inference.qwen3_asr import Qwen3ASRModel
            self.qwen_model = Qwen3ASRModel.from_pretrained(
                ASR_MODEL_ID,
                device_map=device,
                torch_dtype=torch.float16 if device != "cpu" else torch.float32,
                trust_remote_code=True
            )
            logger.info("Qwen3ASR initialized.")
        except Exception as e:
            logger.error(f"Qwen Init Failed: {e}")
            # Non-fatal? We can fall back to Vosk for everything if Qwen fails? 
            # For now, let's assume it's critical.
            
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
        try:
            fs = 44100
            duration = 0.15
            f = 880.0 if active else 440.0
            t = np.linspace(0, duration, int(fs * duration), False)
            # Sine wave with envelope to avoid clicking
            audio = np.sin(f * 2 * np.pi * t) * 0.3
            # Simple fade in/out
            fade_len = int(0.01 * fs)
            audio[:fade_len] *= np.linspace(0, 1, fade_len)
            audio[-fade_len:] *= np.linspace(1, 0, fade_len)
            
            sd.play(audio.astype(np.float32), fs)
            # Don't wait, async
        except Exception:
            pass

    def run(self):
        logger.info("Starting Audio Loop...")
        
        # Open stream
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, 
                            channels=1, callback=self.audio_callback):
            
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
                # Boost weak mic input slightly, but clip to avoid distortion
                audio_data = indata.flatten() * 3.0 
                
                if mode == "IDLE":
                    self.handle_idle(audio_data)
                elif mode == "LISTENING":
                    self.handle_listening(audio_data)

    def handle_idle(self, audio_data):
        if not self.vosk_rec_wake: return
        
        # Vosk expects int16 bytes
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
        
        # 2. VAD (Energy based for now, but cleaner)
        energy = np.sqrt(np.mean(audio_data**2))
        
        if energy > SILENCE_THRESHOLD:
            if not self.is_speaking:
                logger.info("Speech detected...")
                self.is_speaking = True
            self.silence_frames = 0
        else:
            if self.is_speaking:
                self.silence_frames += 1
        
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
        
        # Ignore short audio (< 0.5s)
        if len(full_audio) < SAMPLE_RATE * 0.5:
            return

        if self.qwen_model:
            try:
                # Transcribe
                # Qwen expects list of (audio, sr) tuples
                results = self.qwen_model.transcribe(
                    audio=[(full_audio, SAMPLE_RATE)], 
                    language="English" 
                )
                text = results[0].text.strip()
                logger.info(f"Transcribed: {text}")
                
                if text:
                    self.send_ipc(f"QUERY:{text}".encode('utf-8'))
                    self.play_cue(active=False) # Confirmation beep
                    self.set_mode("PAUSED") # Stop listening after command
            except Exception as e:
                logger.error(f"Transcription Failed: {e}")
        else:
            logger.error("Qwen Model not loaded!")

if __name__ == "__main__":
    service = VoiceService()
    try:
        service.run()
    except KeyboardInterrupt:
        pass
