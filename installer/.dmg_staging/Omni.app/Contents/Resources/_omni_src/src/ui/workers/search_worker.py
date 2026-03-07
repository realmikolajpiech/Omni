import requests
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import SEARCH_URL

class SearchWorker(QThread):
    results_found = pyqtSignal(list, str)
    def __init__(self, query):
        super().__init__()
        self.query = query
    def run(self):
        try:
            r = requests.post(SEARCH_URL, json={"query": self.query}, timeout=5)
            data = r.json()
            self.results_found.emit(data.get("results", []), self.query)
        except: pass
