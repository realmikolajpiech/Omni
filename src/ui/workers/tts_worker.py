from PyQt6.QtCore import QThread, pyqtSignal
import logging
import queue
from src.services.voice.tts import speak


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
        self.queue.put(None)  # sentinel — processed in order after queued text

    def force_stop(self):
        """Immediate stop: drop remaining queue items."""
        self._force_stop = True
        self.queue.put(None)

    def run(self):
        """Process text items until the None sentinel is encountered."""
        while not self._force_stop:
            try:
                text = self.queue.get(timeout=30)
            except queue.Empty:
                break  # Hung for 30s — bail out

            if text is None:
                break  # Graceful stop sentinel reached

            try:
                speak(text)
                self.queue.task_done()
            except Exception as e:
                logging.error(f"TTS Worker Error: {e}")

        self.finished_speaking.emit()
