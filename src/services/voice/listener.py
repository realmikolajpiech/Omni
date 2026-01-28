import os
import sys
import vosk
import json
import subprocess
import time
import logging
import socket

from src.core.config import VOSK_MODEL_PATH, PROJECT_ROOT, IPC_PORT

# --- CONFIG ---
WAKE_WORDS = ["hey omni", "hey army", "hey on me", "hey only"]
LAUNCH_SCRIPT = os.path.join(PROJECT_ROOT, "run.py")
PYTHON_EXE = sys.executable

def run_listener():
    if not os.path.exists(VOSK_MODEL_PATH):
        logging.error(f"Model not found at {VOSK_MODEL_PATH}. Run 'download_model.py' first.")
        sys.exit(1)

    logging.info("Loading Vosk Model...")
    try:
        model = vosk.Model(VOSK_MODEL_PATH)
    except Exception as e:
        logging.error(f"Failed to load model: {e}")
        sys.exit(1)

    logging.info("Model loaded. Starting audio capture...")

    # Debounce mechanism
    last_trigger = 0
    COOLDOWN = 2.0 

    process = None
    
    if os.name == 'posix':
        # Start arecord process for Linux
        try:
            process = subprocess.Popen(
                ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-t", "raw"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=8000
            )
        except Exception as e:
            logging.error(f"Failed to start arecord: {e}")
            return
    else:
        logging.error("Windows audio capture not yet implemented (requires PyAudio).")
        return

    rec = vosk.KaldiRecognizer(model, 16000)
    
    logging.info(f"Listening...")

    try:
        while True:
            data = process.stdout.read(4000)
            if len(data) == 0:
                break
                
            if rec.AcceptWaveform(data):
                # Final result
                res = json.loads(rec.Result())
                text = res.get('text', '')
            else:
                # Partial result
                res = json.loads(rec.PartialResult())
                text = res.get('partial', '')

            if text:
                # Check existence
                found = any(w in text.lower() for w in WAKE_WORDS)
                
                if found and (time.time() - last_trigger > COOLDOWN):
                    logging.info(f"Wake Word Detected: '{text}'!")
                    last_trigger = time.time()
                    
                    # Reset Recognizer to clear buffer
                    rec.Reset()
                    
                    # IPC Toggle
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.connect(('127.0.0.1', IPC_PORT))
                        s.sendall(b"TOGGLE")
                        s.close()
                        logging.info("Sent TOGGLE via IPC.")
                    except:
                        # Fallback to launch if not running
                        try:
                            subprocess.Popen([PYTHON_EXE, LAUNCH_SCRIPT], 
                                             env=os.environ.copy())
                            logging.info("Launched Omni (Cold Start).")
                        except Exception as e:
                            logging.error(f"Failed to launch: {e}")
    finally:
        if process:
            process.terminate()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        run_listener()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error(f"Crashed: {e}")
