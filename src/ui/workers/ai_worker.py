import requests
import json
import logging
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import BRAIN_URL

class AIWorker(QThread):
    finished = pyqtSignal(object)
    partial_response = pyqtSignal(object)
    stream_started = pyqtSignal(object)
    def __init__(self, query, history=[], screenshot=None):
        super().__init__()
        self.query = query
        self.history = history
        self.screenshot = screenshot
    def run(self):
        try:
            payload = {"query": self.query, "history": self.history, "stream": True}
            if self.screenshot: payload["screenshot"] = self.screenshot

            # Use streaming=True to get server-sent events
            r = requests.post(BRAIN_URL, json=payload, timeout=300, stream=True)
            r.raise_for_status()

            # Parse server-sent events
            accumulated_answer = ""
            for line in r.iter_lines():
                if line:
                    line = line.decode('utf-8') if isinstance(line, bytes) else line
                    if line.startswith('data: '):
                        try:
                            data = json.loads(line[6:])  # Remove 'data: ' prefix
                            if data.get("type") == "partial":
                                thinking = data.get("thinking", "")
                                answer = data.get("answer", "")
                                # if thinking or answer:
                                #     logging.info(f"[STREAM] client received partial (thinking={len(thinking)}, answer={len(answer)} chars)")
                                self.partial_response.emit({
                                    "answer": answer,
                                    "actions": [],
                                    "thinking": thinking,
                                    "is_partial": True
                                })
                            elif data.get("type") == "special":
                                # Special control messages (e.g. screenshot_required)
                                self.finished.emit({
                                    "special_action": data.get("special_action")
                                })
                                break
                            elif data.get("type") == "final":
                                self.finished.emit({
                                    "answer": data.get("answer", ""),
                                    "actions": data.get("actions", []),
                                    "thinking": data.get("thinking", "")
                                })
                                break
                            elif data.get("type") == "error":
                                self.finished.emit({"answer": f"Error: {data.get('error')}"})
                                break
                        except json.JSONDecodeError:
                            pass

        except requests.exceptions.Timeout:
            self.finished.emit({"answer": "The AI is taking longer than expected to respond. The model may still be loading. Please try again in a moment."})
        except requests.exceptions.ConnectionError:
            self.finished.emit({"answer": "Cannot connect to the Omni AI service. Please ensure it's running."})
        except Exception as e:
            self.finished.emit({"answer": f"System Error: {str(e)}"})
