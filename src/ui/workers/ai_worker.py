import requests
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import BRAIN_URL

class AIWorker(QThread):
    finished = pyqtSignal(object)
    def __init__(self, query, history=[], screenshot=None):
        super().__init__()
        self.query = query
        self.history = history
        self.screenshot = screenshot
    def run(self):
        try:
            payload = {"query": self.query, "history": self.history}
            if self.screenshot: payload["screenshot"] = self.screenshot
            r = requests.post(BRAIN_URL, json=payload, timeout=120)
            data = r.json()
            self.finished.emit(data)
        except requests.exceptions.ConnectionError:
            self.finished.emit({"answer": "The Omni AI hasn't loaded yet. Please try again in a moment."})
        except Exception as e:
            self.finished.emit({"answer": f"System Error: {str(e)}"})
