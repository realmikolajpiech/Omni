import requests
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import ACTION_URL

class ActionWorker(QThread):
    action_found = pyqtSignal(object, str)
    def __init__(self, query):
        super().__init__()
        self.query = query
    def run(self):
        try:
            r = requests.post(ACTION_URL, json={"query": self.query}, timeout=5)
            data = r.json()
            actions = data.get("actions", [])
            if not actions and data.get("action"): actions = [data.get("action")]
            self.action_found.emit(actions, self.query)
        except:
            self.action_found.emit([], self.query)
