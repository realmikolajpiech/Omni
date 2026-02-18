import sys
import logging
import subprocess
import queue
import threading
import src.services.llm.model_manager as model_manager

# Audio queue + background player for Kokoro (streams chunks in real time)
_audio_queue = queue.Queue()
_player_thread = None
_player_running = False


def _tts_player_worker():
    global _player_running
    import sounddevice as sd
    logging.info("TTS Player Worker Started")

    while _player_running:
        try:
            with sd.OutputStream(samplerate=24000, channels=1, dtype='float32') as stream:
                logging.info("TTS Audio Stream Opened")
                while _player_running:
                    try:
                        chunk = _audio_queue.get(timeout=0.5)
                        if chunk is None:
                            continue
                        stream.write(chunk)
                        _audio_queue.task_done()
                    except queue.Empty:
                        continue
                    except Exception as e:
                        logging.error(f"TTS Stream Write Error: {e}")
                        break
        except Exception as e:
            logging.error(f"TTS Player Init/Stream Error: {e}")
            if _player_running:
                import time
                time.sleep(1)

    logging.info("TTS Player Worker Stopped")


def _ensure_player_running():
    global _player_thread, _player_running
    if not _player_running or not _player_thread or not _player_thread.is_alive():
        _player_running = True
        _player_thread = threading.Thread(target=_tts_player_worker, daemon=True)
        _player_thread.start()


def stop_playback():
    """Clear the audio queue to interrupt current speech."""
    global _audio_queue
    with _audio_queue.mutex:
        _audio_queue.queue.clear()


def speak(text):
    if not text:
        return

    logging.info(f"Speaking: {text[:60]}...")

    # Lazy-load Kokoro on first use
    if not model_manager.tts_model:
        logging.info("TTS Model not loaded. Lazy-loading...")
        model_manager.ensure_tts_model()

    if model_manager.tts_model and model_manager.tts_model.get("type") == "kokoro":
        try:
            _ensure_player_running()
            pipeline = model_manager.tts_model["pipeline"]
            for _gs, _ps, audio in pipeline(text, voice='af_bella', speed=1):
                if audio is not None:
                    _audio_queue.put(audio)
            return
        except Exception as e:
            logging.error(f"Kokoro TTS Error: {e}")
            # fall through to system TTS

    # System TTS fallback
    if sys.platform == "darwin":
        try:
            subprocess.run(["say", text], check=False)
        except Exception as e:
            logging.error(f"macOS TTS error: {e}")
    elif sys.platform == "win32":
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(text)
        except Exception as e:
            logging.error(f"Win32 TTS error: {e}")
    else:
        logging.warning(f"No TTS available on platform: {sys.platform}")
