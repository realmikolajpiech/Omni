"""Persistent reminder service for Omni.

Reminder types:
  - Simple:    label + fire_at  → macOS notification fires once
  - Recurring: + interval_seconds  → repeats on schedule
  - Agentic:   + query  → runs an AI query each time; LLM includes STOP_REMINDER to end
"""
import json
import logging
import os
import subprocess
import threading
import time
import uuid

_STORE_PATH = os.path.expanduser("~/.config/omni/reminders.json")
_lock = threading.Lock()


def _read_all() -> list:
    if not os.path.exists(_STORE_PATH):
        return []
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _write_all(reminders: list) -> None:
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    with open(_STORE_PATH, "w") as f:
        json.dump(reminders, f, indent=2)


def add_reminder(label: str, fire_at: float, interval_seconds: int = 0, query: str = "") -> str:
    """Add a reminder and return its ID."""
    rid = uuid.uuid4().hex[:8]
    entry = {
        "id": rid,
        "label": label,
        "fire_at": fire_at,
        "interval_seconds": interval_seconds,
        "query": query,
        "created_at": time.time(),
    }
    with _lock:
        reminders = _read_all()
        reminders.append(entry)
        _write_all(reminders)
    logging.info(f"[reminders] Added reminder {rid}: {label!r} at {fire_at}")
    return rid


def list_reminders() -> list:
    with _lock:
        return list(_read_all())


def delete_reminder(rid: str) -> bool:
    with _lock:
        reminders = _read_all()
        original_len = len(reminders)
        reminders = [r for r in reminders if r["id"] != rid]
        if len(reminders) == original_len:
            return False
        _write_all(reminders)
    return True


def _send_notification(title: str, message: str) -> None:
    try:
        script = (
            f'display notification {json.dumps(message)} '
            f'with title {json.dumps(title)} '
            f'sound name "Default"'
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5
        )
    except Exception as e:
        logging.error(f"[reminders] Notification failed: {e}")


def _run_query(query: str) -> str:
    """Run an agentic query via process_chat_request (deferred import to avoid circular)."""
    try:
        from src.services.llm.chat import process_chat_request
        chunks = []
        for event_type, payload in process_chat_request(query, history=[], stream=True):
            if event_type == "final":
                return payload.get("answer", "").strip()
            elif event_type == "partial":
                pass  # ignore streaming partials
        return "".join(chunks).strip()
    except Exception as e:
        logging.error(f"[reminders] Query failed: {e}")
        return f"Error running query: {e}"


class ReminderService(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="ReminderService")
        self._stop_event = threading.Event()

    def run(self):
        logging.info("[reminders] Service started")
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logging.error(f"[reminders] Tick error: {e}")
            self._stop_event.wait(10)

    def _tick(self):
        now = time.time()
        with _lock:
            reminders = _read_all()

        due = [r for r in reminders if r["fire_at"] <= now]
        if not due:
            return

        for reminder in due:
            rid = reminder["id"]
            label = reminder["label"]
            query = reminder.get("query", "")
            interval = reminder.get("interval_seconds", 0)

            if query:
                result = _run_query(query)
                _send_notification(label, result)
                stop = "STOP_REMINDER" in result
            else:
                _send_notification("Omni Reminder", label)
                stop = True  # simple reminders fire once

            with _lock:
                reminders = _read_all()
                reminders = [r for r in reminders if r["id"] != rid]

                if interval and not stop:
                    # Reschedule
                    updated = dict(reminder)
                    updated["fire_at"] = now + interval
                    reminders.append(updated)
                    logging.info(f"[reminders] Rescheduled {rid} in {interval}s")
                else:
                    logging.info(f"[reminders] Completed and removed {rid}")

                _write_all(reminders)


_service_instance: ReminderService | None = None
_service_lock = threading.Lock()


def get_service() -> ReminderService:
    global _service_instance
    with _service_lock:
        if _service_instance is None:
            _service_instance = ReminderService()
    return _service_instance
