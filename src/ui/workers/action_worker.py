import json
import logging
import requests
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import ACTION_URL, RESOLVE_PLACE_URL


class ActionWorker(QThread):
    # (actions, chips, query)
    action_found = pyqtSignal(object, object, str)
    # Intermediate signal for "searching" state (query being searched)
    searching = pyqtSignal(str, str)  # (search_query, original_query)

    def __init__(self, query, use_stream=True):
        super().__init__()
        self.query = query
        self.use_stream = use_stream

    def run(self):
        try:
            if self.use_stream:
                self._run_streaming()
            else:
                self._run_simple()
        except Exception as e:
            logging.error(f"ActionWorker Error: {e}")
            self.action_found.emit([], [], self.query)

    def _emit_from_data(self, data):
        """Extract actions/chips from response data and emit signal."""
        actions = data.get("actions", [])
        if not actions and data.get("action"):
            actions = [data.get("action")]
        chips = data.get("chips", [])
        logging.info(f"ActionWorker: {len(actions)} actions, {len(chips)} chips for '{self.query}'")
        self.action_found.emit(actions, chips, self.query)

    def _run_simple(self):
        r = requests.post(ACTION_URL, json={"query": self.query}, timeout=90)
        self._emit_from_data(r.json())

    def _run_streaming(self):
        """Send request with stream flag. Handles both SSE and JSON responses.

        - If the endpoint returns text/event-stream (search path): parse SSE events,
          emit intermediate "searching" signal for skeleton, then emit final result.
        - If the endpoint returns application/json (fast path like calc/translate/etc):
          parse JSON directly and emit result.
        """
        # Emit skeleton immediately — before the HTTP round-trip — so the UI
        # always shows feedback during LLM inference (1-4 seconds).
        # Fast-path responses (shortcuts, calc, etc.) arrive in < 50 ms and
        # replace it before it's even visually noticed.
        self.searching.emit(self.query, self.query)

        try:
            with requests.post(
                ACTION_URL,
                json={"query": self.query, "stream": True},
                timeout=90,
                stream=True,
            ) as r:
                r.raise_for_status()
                content_type = r.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    # SSE mode — parse events progressively
                    got_done = False
                    search_query_seen = None
                    for line in r.iter_lines(decode_unicode=True):
                        if not line or not line.startswith("data: "):
                            continue
                        try:
                            payload = json.loads(line[6:])
                        except (json.JSONDecodeError, ValueError):
                            continue

                        event = payload.get("event", "")

                        if event == "searching":
                            search_q = payload.get("query", self.query)
                            search_query_seen = search_q
                            logging.info(f"ActionWorker: Searching '{search_q}' for '{self.query}'")
                            self.searching.emit(search_q, self.query)

                        elif event == "done":
                            self._emit_from_data(payload)
                            got_done = True
                            return

                    # Generator finished without a "done" event — fall back to simple
                    if not got_done:
                        logging.warning(f"ActionWorker: SSE stream ended without 'done' for '{self.query}', falling back to simple")
                        self._run_simple()
                else:
                    # JSON response (fast path — calc, translate, shortcuts, etc.)
                    self._emit_from_data(r.json())

        except Exception as e:
            logging.warning(f"ActionWorker streaming failed ({e}), falling back to simple")
            try:
                self._run_simple()
            except Exception as e2:
                logging.error(f"ActionWorker simple fallback also failed: {e2}")
                self.action_found.emit([], [], self.query)


class PlaceResolverWorker(QThread):
    # (resolved_action, original_name)
    place_resolved = pyqtSignal(object, str)

    def __init__(self, name):
        super().__init__()
        self.name = name

    def run(self):
        try:
            r = requests.post(RESOLVE_PLACE_URL, json={"name": self.name}, timeout=20)
            action = r.json()
            if action and action.get("type") == "place":
                logging.info(f"PlaceResolverWorker: Resolved '{self.name}'")
                self.place_resolved.emit(action, self.name)
            else:
                logging.warning(f"PlaceResolverWorker: Failed to resolve '{self.name}'")
                self.place_resolved.emit(None, self.name)
        except Exception as e:
            logging.error(f"PlaceResolverWorker Error: {e}")
            self.place_resolved.emit(None, self.name)
