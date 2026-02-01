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
            import logging
            logging.info(f"ActionWorker: Requesting action for '{self.query}'")
            # Fast model can take 10–30+ s; use a long timeout so actions are not dropped
            r = requests.post(ACTION_URL, json={"query": self.query}, timeout=90)
            data = r.json()
            actions = data.get("actions", [])
            if not actions and data.get("action"): actions = [data.get("action")]
            logging.info(f"ActionWorker: Found {len(actions)} actions for '{self.query}'")
            self.action_found.emit(actions, self.query)
        except Exception as e:
            import logging
            logging.error(f"ActionWorker Error: {e}")
            self.action_found.emit([], self.query)
