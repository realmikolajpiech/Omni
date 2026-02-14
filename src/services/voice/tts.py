import torch
import logging
import sounddevice as sd
import sys
import numpy as np
import threading
import queue
import src.services.llm.model_manager as model_manager

# Global audio queue and player thread
_audio_queue = queue.Queue()
_player_thread = None
_player_running = False

def _tts_player_worker():
    """Background worker to play audio chunks continuously."""
    global _player_running
    logging.info("TTS Player Worker Started")
    
    try:
        # Open stream once (Kokoro default: 24000Hz)
        with sd.OutputStream(samplerate=24000, channels=1, dtype='float32') as stream:
            while _player_running:
                try:
                    # Get chunk with timeout to check running flag
                    chunk = _audio_queue.get(timeout=0.5)
                    if chunk is None: continue
                    
                    # Write to stream (blocking if buffer full)
                    stream.write(chunk)
                    _audio_queue.task_done()
                except queue.Empty:
                    continue
                except Exception as e:
                    logging.error(f"TTS Stream Write Error: {e}")
                    
    except Exception as e:
        logging.error(f"TTS Player Init Error: {e}")
    finally:
        logging.info("TTS Player Worker Stopped")

def _ensure_player_running():
    global _player_thread, _player_running
    if not _player_running or not _player_thread or not _player_thread.is_alive():
        _player_running = True
        _player_thread = threading.Thread(target=_tts_player_worker, daemon=True)
        _player_thread.start()

def stop_playback():
    """Clears the audio queue to stop speaking immediately."""
    global _audio_queue
    with _audio_queue.mutex:
        _audio_queue.queue.clear()

def speak(text):
    if not text: return
    
    logging.info(f"Speaking: {text[:50]}...")
    
    # Lazy load if not present
    if not model_manager.tts_model:
         logging.info("TTS Model not loaded. Attempting lazy load...")
         model_manager.ensure_tts_model()

    # Check if we have a loaded TTS model
    if model_manager.tts_model:
        try:
            if model_manager.tts_model.get("type") == "kokoro":
                _ensure_player_running()
                pipeline = model_manager.tts_model["pipeline"]
                
                # Generate and queue audio chunks
                # This loop runs as fast as the model generates
                # while the player thread consumes at real-time speed
                for gs, ps, audio in pipeline(text, voice='af_bella', speed=1):
                    if audio is not None:
                        _audio_queue.put(audio)
                return

            elif model_manager.tts_model.get("type") == "transformers":
                # SpeechT5 implementation
                model = model_manager.tts_model["model"]
                processor = model_manager.tts_model["processor"]
                vocoder = model_manager.tts_model["vocoder"]
                
                inputs = processor(text=text, return_tensors="pt")
                if torch.cuda.is_available():
                    inputs = inputs.to("cuda")
                elif sys.platform == "darwin" and hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                    inputs = inputs.to("mps")
                    
                # Load speaker embeddings (needed for SpeechT5)
                # We use a default one from dataset or zeros if not available
                # For simplicity, we might skip or load a preset if available
                # But SpeechT5 requires speaker embeddings.
                # If we don't have them, we might crash. 
                # Assuming this path was experimental/placeholder.
                pass
                
        except Exception as e:
             logging.error(f"Model TTS Error: {e}")
             # Fallback to system TTS if model fails

    # Fallback to System TTS (Preferred for macOS if no model)
    if sys.platform == "darwin":
        logging.warning("Falling back to macOS System TTS ('say')")
        import subprocess
        try:
            subprocess.run(["say", text])
            return
        except Exception as e:
            logging.error(f"Mac TTS Error: {e}")
            
    elif sys.platform == "win32":
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(text)
        except Exception as e:
            logging.error(f"Win32 TTS Error: {e}")
