import sys
print("DEBUG: Starting listener script...", file=sys.stderr)
import os
import queue
import sounddevice as sd
import numpy as np
import logging
import socket
import torch

# Add project root to path to allow imports from src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
# Add Qwen3_ASR library path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "Qwen3_ASR")))

from src.core.config import ASR_MODEL_ID, IPC_PORT

import threading
import json

import time
import zipfile
import urllib.request
import json

# --- CONFIG ---
WAKE_WORDS = ["hey omni", "hey army", "hey on me", "hey only", "hi omni", "okay omni", "hey", "omni"]
SAMPLE_RATE = 16000
BLOCK_SIZE = 4000
SILENCE_THRESHOLD = 0.00002 
SILENCE_DURATION = 1.0
UDP_PORT = 5557
PARTIAL_INTERVAL = 0.5 

VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "vosk-model-small-en-us-0.15"))

q = queue.Queue()
state = {
    "mode": "IDLE",  # IDLE (Waiting for wake word), LISTENING (Active dictation)
    "running": True
}

def udp_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', UDP_PORT))
        logging.info(f"UDP Control Listener started on port {UDP_PORT}")
        while state["running"]:
            data, addr = sock.recvfrom(1024)
            msg = data.decode('utf-8').strip()
            logging.info(f"UDP Received: {msg}")
            
            if msg == "START_LISTENING":
                # Force active listening
                state["mode"] = "LISTENING"
                send_ipc(b"STATUS:LISTENING")
                play_feedback_sound()
            elif msg == "STOP_LISTENING":
                # Finalize current utterance but don't necessarily go to IDLE (Wake Word)
                # This is "Mic Off" in UI -> Should go to PAUSED
                state["finalize"] = True
                state["mode"] = "PAUSED" 
                send_ipc(b"STATUS:PAUSED")
            elif msg == "SET_MODE:IDLE":
                # Window hidden -> Wait for wake word
                state["mode"] = "IDLE"
                send_ipc(b"STATUS:IDLE")
            elif msg == "SET_MODE:LISTENING":
                # Window visible (Voice triggered) -> Active dictation
                state["mode"] = "LISTENING"
                send_ipc(b"STATUS:LISTENING")
                play_feedback_sound()
            elif msg == "SET_MODE:PAUSED":
                # Window visible (Manual triggered) -> Mic off
                state["mode"] = "PAUSED"
                send_ipc(b"STATUS:PAUSED")
    except Exception as e:
        logging.error(f"UDP Listener Error: {e}")

def audio_callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    q.put(indata.copy())

def play_feedback_sound():
    if sys.platform == 'darwin':
        try:
            import subprocess
            subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])
        except: pass
    elif sys.platform == 'win32':
        import winsound
        try:
            winsound.PlaySound("SystemAsterisk", winsound.SND_ASYNC)
        except: pass

def send_ipc(msg):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', IPC_PORT))
        s.sendall(msg)
        s.close()
        logging.info(f"Sent IPC: {msg}")
        return True
    except Exception as e:
        logging.error(f"IPC Error: {e}")
        return False

def download_vosk_model():
    if not os.path.exists(VOSK_MODEL_DIR):
        logging.info(f"Downloading Vosk Model to {VOSK_MODEL_DIR}...")
        zip_path = os.path.join(os.path.dirname(__file__), "vosk_model.zip")
        try:
            urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(os.path.dirname(__file__))
            logging.info("Vosk Model Downloaded.")
        except Exception as e:
            logging.error(f"Failed to download Vosk model: {e}")
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)

def run_listener():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Init Vosk
    download_vosk_model()
    try:
        from vosk import Model, KaldiRecognizer
        if os.path.exists(VOSK_MODEL_DIR):
            vosk_model = Model(VOSK_MODEL_DIR)
            rec = KaldiRecognizer(vosk_model, SAMPLE_RATE)
            rec.SetWords(False) # Faster
            logging.info("Vosk Model Loaded.")
        else:
            logging.error("Vosk model directory not found.")
            rec = None
    except Exception as e:
        logging.error(f"Vosk Init Error: {e}")
        rec = None

    logging.info(f"Loading ASR Model: {ASR_MODEL_ID}...")
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if sys.platform == "darwin" and torch.backends.mps.is_available():
        device = "mps"
        
    logging.info(f"Using device: {device}")

    try:
        from qwen_asr.inference.qwen3_asr import Qwen3ASRModel
        
        # Load model using the official wrapper
        model = Qwen3ASRModel.from_pretrained(
            ASR_MODEL_ID,
            device_map=device,
            torch_dtype=torch.float16 if device != "cpu" else torch.float32,
            trust_remote_code=True
        )
        
        logging.info("Qwen3ASRModel loaded successfully via wrapper.")

    except Exception as e:
        logging.error(f"Failed to load Qwen ASR model: {e}")
        sys.exit(1)

    logging.info("Starting VAD loop...")
    
    # Start UDP Listener
    udp_thread = threading.Thread(target=udp_listener, daemon=True)
    udp_thread.start()

    # Audio Buffer for VAD
    audio_buffer = []
    is_speaking = False
    silence_frames = 0
    max_silence_frames = int(SILENCE_DURATION * SAMPLE_RATE / BLOCK_SIZE)
    
    # Flush queue
    while not q.empty(): q.get()
    
    # Explicitly select default device if None doesn't work well
    # or list devices to debug
    logging.info(f"Audio Devices:\n{sd.query_devices()}")
    
    # Increase block size for potentially better energy readings? No, smaller is usually better for responsiveness.
    # Try amplifying input?
    
    with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, device=None,
                        channels=1, callback=audio_callback):
        
        logging.info("Listening (SoundDevice InputStream started)...")
        last_partial_time = time.time()
        
        while True:
            try:
                indata = q.get(timeout=0.5)
            except queue.Empty:
                continue
            
            # Skip processing if PAUSED (Window visible but mic off)
            if state["mode"] == "PAUSED":
                # Clear buffer to avoid buildup
                if len(audio_buffer) > 0: audio_buffer = []
                is_speaking = False
                continue

            # DIGITAL AMPLIFICATION: Multiply signal to boost very quiet mic input
            indata = indata * 5.0 

            audio_buffer.append(indata)
            
            # Simple Energy VAD
            energy = np.linalg.norm(indata) / len(indata)
            
            # Dynamic threshold logging for debugging (print every ~50 blocks)
            if len(audio_buffer) % 50 == 0:
               logging.info(f"Energy: {energy:.6f} (Threshold: {SILENCE_THRESHOLD})")
            
            if energy > SILENCE_THRESHOLD:
                if not is_speaking:
                    is_speaking = True
                    logging.info(f"Speech detected (Energy: {energy:.4f})...")
                silence_frames = 0
            else:
                if is_speaking:
                    silence_frames += 1

            # Vosk Streaming (Real-time Feedback)
            if state["mode"] == "LISTENING" and rec is not None:
                try:
                    # Convert to int16
                    audio_int16 = (indata * 32767).astype(np.int16).tobytes()
                    rec.AcceptWaveform(audio_int16)
                    
                    if time.time() - last_partial_time > 0.2:
                        res = json.loads(rec.PartialResult())
                        text = res.get("partial", "")
                        if text:
                            # logging.info(f"Partial: {text}")
                            send_ipc(f"PARTIAL:{text}".encode('utf-8'))
                        last_partial_time = time.time()
                except Exception as e:
                    logging.error(f"Vosk Error: {e}")

            # Check for forced finalization (from UI Stop button)
            if state.get("finalize", False):
                state["finalize"] = False
                if len(audio_buffer) > 0:
                    logging.info("Forcing finalization of utterance...")
                    is_speaking = True
                    silence_frames = max_silence_frames + 100

            # End of utterance detection
            if is_speaking and silence_frames > max_silence_frames:
                logging.info("End of utterance. Transcribing...")
                
                # Reset Vosk for next time
                if rec: rec.Reset()
                is_speaking = False
                silence_frames = 0
                
                # Concatenate buffer
                full_audio = np.concatenate(audio_buffer, axis=0).flatten()
                audio_buffer = [] # Reset buffer
                
                # Verify length
                if len(full_audio) < SAMPLE_RATE * 0.5:
                    logging.info("Audio too short, ignoring.")
                    continue
                
                # Transcribe
                try:
                    logging.info(f"Transcribing {len(full_audio)/SAMPLE_RATE:.2f}s audio...")
                    
                    # Qwen3 inference
                    # Wrapper expects (waveform, sample_rate) tuple for raw audio
                    results = model.transcribe(audio=[(full_audio, SAMPLE_RATE)], language=None)
                    text = results[0].text.lower().strip()
                    
                    logging.info(f"Transcribed: '{text}'")
                    
                    if not text: continue

                    # Check Mode
                    if state["mode"] == "LISTENING":
                        # Direct Command Mode
                        logging.info(f"Command (Active Mode): {text}")
                        send_ipc(f"QUERY:{text}".encode('utf-8'))
                        
                        # Reset to PAUSED after command?
                        # User usually wants to continue conversation?
                        # But "Google Assistant" style usually stops listening after one query until prompted again?
                        # User said: "like google assistant". Usually after query it stops.
                        state["mode"] = "PAUSED"
                        send_ipc(b"STATUS:PAUSED")
                        
                    elif state["mode"] == "IDLE":
                        # Wake Word Mode
                        wake_found = any(w in text for w in WAKE_WORDS)
                        
                        if wake_found:
                            logging.info(f"WAKE WORD DETECTED: {text}")
                            play_feedback_sound()
                            send_ipc(b"TOGGLE")
                            
                            # Switch to LISTENING mode for next command
                            state["mode"] = "LISTENING"
                            send_ipc(b"STATUS:LISTENING")
                            
                            # Extract command if present in SAME utterance
                            command = text
                            for w in WAKE_WORDS:
                                command = command.replace(w, "")
                            command = command.strip()
                            
                            if command:
                                logging.info(f"Command (Immediate): {command}")
                                send_ipc(f"QUERY:{command}".encode('utf-8'))
                                # If immediate command found, maybe go back to PAUSED?
                                state["mode"] = "PAUSED"
                                send_ipc(b"STATUS:PAUSED")
                        else:
                             logging.info(f"No wake word in: '{text}'")
                            
                except Exception as e:
                    logging.error(f"Transcription error: {e}")
            
            # Limit buffer size to avoid OOM if VAD fails
            if len(audio_buffer) * BLOCK_SIZE > 15 * SAMPLE_RATE:
                logging.warning("Buffer full (15s), clearing...")
                audio_buffer = []
                is_speaking = False

if __name__ == "__main__":
    run_listener()
