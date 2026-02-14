from PyQt6.QtCore import QThread, pyqtSignal
import logging
import queue
from src.services.voice.tts import speak

class TTSWorker(QThread):
    finished_speaking = pyqtSignal()
    
    def __init__(self, text_generator=None):
        super().__init__()
        self.queue = queue.Queue()
        self.is_running = True
        
        # If text is provided immediately (non-streaming), add it
        if isinstance(text_generator, str):
            self.queue.put(text_generator)
            self.queue.put(None) # End immediately
        
    def add_text(self, text):
        if text and text.strip():
            self.queue.put(text)
            
    def stop(self):
        self.is_running = False
        self.queue.put(None) # Sentinel to break loop
        
    def run(self):
        while self.is_running:
            try:
                text = self.queue.get()
                if text is None: 
                    break
                
                # Speak handles logging and playing
                speak(text)
                
                self.queue.task_done()
            except Exception as e:
                logging.error(f"TTS Worker Error: {e}")
        
        self.finished_speaking.emit()
