from PyQt6.QtCore import QThread, pyqtSignal
import logging
import queue
import threading
from src.services.voice.tts import generate_audio_bytes, play_audio_bytes, _stop_event


class TTSWorker(QThread):
    finished_speaking = pyqtSignal()

    def __init__(self, text_generator=None):
        super().__init__()
        self.queue = queue.Queue()
        self._force_stop = False

        # Non-streaming mode: text provided upfront
        if isinstance(text_generator, str):
            self.queue.put(text_generator)
            self.queue.put(None)  # sentinel

    def add_text(self, text):
        if text and text.strip():
            self.queue.put(text)

    def stop(self):
        """Graceful stop: process all items already in the queue, then stop."""
        self.queue.put(None)

    def force_stop(self):
        """Immediate stop: drop remaining queue items."""
        self._force_stop = True
        self.queue.put(None)

    def run(self):
        """
        Pipeline: a generation thread pre-fetches audio bytes while the main
        thread plays the previous chunk. This eliminates the gap between
        consecutive TTS chunks.
        """
        audio_queue = queue.Queue()

        def generation_thread():
            while not self._force_stop:
                try:
                    text = self.queue.get(timeout=30)
                except queue.Empty:
                    audio_queue.put(None)
                    break

                if text is None:
                    audio_queue.put(None)
                    break

                try:
                    audio = generate_audio_bytes(text)
                    audio_queue.put(audio)
                    self.queue.task_done()
                except Exception as e:
                    logging.error(f"TTS generation error: {e}")
                    audio_queue.put(b"")

        gen = threading.Thread(target=generation_thread, daemon=True)
        gen.start()

        while not self._force_stop:
            try:
                audio = audio_queue.get(timeout=35)
            except queue.Empty:
                break

            if audio is None:
                break

            if audio:
                try:
                    _stop_event.clear()
                    play_audio_bytes(audio, _stop_event)
                except Exception as e:
                    logging.error(f"TTS playback error: {e}")

        gen.join(timeout=5)
        self.finished_speaking.emit()
