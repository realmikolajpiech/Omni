import requests
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import ACTION_URL, RESOLVE_PLACE_URL

class ActionWorker(QThread):
    # (actions, chips, query)
    action_found = pyqtSignal(object, object, str)

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
            if not actions and data.get("action"):
                actions = [data.get("action")]
            chips = data.get("chips", [])
            logging.info(f"ActionWorker: Found {len(actions)} actions, {len(chips)} chips for '{self.query}'")
            self.action_found.emit(actions, chips, self.query)
        except Exception as e:
            import logging
            logging.error(f"ActionWorker Error: {e}")
            self.action_found.emit([], [], self.query)


class PlaceResolverWorker(QThread):
    # (resolved_action, original_name)
    place_resolved = pyqtSignal(object, str)

    def __init__(self, name):
        super().__init__()
        self.name = name

    def run(self):
        try:
            import logging
            logging.info(f"PlaceResolverWorker: Resolving place '{self.name}'")
            r = requests.post(RESOLVE_PLACE_URL, json={"name": self.name}, timeout=20)
            action = r.json()
            if action and action.get("type") == "place":
                logging.info(f"PlaceResolverWorker: Resolved '{self.name}'")
                self.place_resolved.emit(action, self.name)
            else:
                logging.warning(f"PlaceResolverWorker: Failed to resolve '{self.name}'")
                self.place_resolved.emit(None, self.name)
        except Exception as e:
            import logging
            logging.error(f"PlaceResolverWorker Error: {e}")
            self.place_resolved.emit(None, self.name)
