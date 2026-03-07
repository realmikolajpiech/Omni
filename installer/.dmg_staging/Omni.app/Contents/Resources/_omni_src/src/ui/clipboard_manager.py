"""
Clipboard history manager.

Uses NSPasteboard directly (via PyObjC) to read the native macOS clipboard.
Falls back to QGuiApplication.clipboard() on non-macOS platforms.

History is persisted to ~/.config/omni/clipboard_history.json.
"""

import os
import sys
import json
import time
import logging
from PyQt6.QtCore import QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QGuiApplication

_HISTORY_PATH = os.path.expanduser("~/.config/omni/clipboard_history.json")

# ---------------------------------------------------------------------------
# Platform clipboard reader
# ---------------------------------------------------------------------------

if sys.platform == "darwin":
    try:
        from AppKit import NSPasteboard

        # UTI for plain text — works with all modern apps
        _UTF8_TYPE = "public.utf8-plain-text"

        def _read_clipboard():
            """Read current string from the native macOS pasteboard."""
            try:
                pb = NSPasteboard.generalPasteboard()
                text = pb.stringForType_(_UTF8_TYPE)
                if text:
                    return str(text)
                # fallback: older NSStringPboardType
                from AppKit import NSStringPboardType
                text = pb.stringForType_(NSStringPboardType)
                return str(text) if text else None
            except Exception as e:
                logging.debug(f"NSPasteboard read error: {e}")
                return None

        def _change_count():
            """NSPasteboard change count — increments on every copy."""
            try:
                return NSPasteboard.generalPasteboard().changeCount()
            except Exception:
                return -1

    except ImportError:
        logging.warning("PyObjC AppKit not found — falling back to Qt clipboard")

        def _read_clipboard():
            try:
                return QGuiApplication.clipboard().text() or None
            except Exception:
                return None

        def _change_count():
            return -1

else:
    def _read_clipboard():
        try:
            return QGuiApplication.clipboard().text() or None
        except Exception:
            return None

    def _change_count():
        return -1


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class ClipboardEntry:
    __slots__ = ("text", "timestamp")

    def __init__(self, text, timestamp=None):
        self.text = text
        self.timestamp = timestamp if timestamp is not None else time.time()

    @property
    def preview(self):
        line = self.text.replace("\n", " ").strip()
        return line[:120] + "..." if len(line) > 120 else line

    def age_str(self):
        delta = int(time.time() - self.timestamp)
        if delta < 5:
            return "just now"
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"

    def to_dict(self):
        return {"text": self.text, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d):
        return cls(d["text"], d.get("timestamp", time.time()))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ClipboardManager(QObject):
    """Singleton — monitors the system clipboard and keeps a persistent history."""

    _instance = None
    MAX_HISTORY = 50

    new_entry = pyqtSignal(object)  # emitted with the new ClipboardEntry

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ClipboardManager()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._history = []
        self._last_text = None
        self._last_change_count = -1

        self._load()

        # Seed state so we don't re-add what's already on clipboard at startup
        try:
            current = _read_clipboard()
            if current:
                self._last_text = current
            self._last_change_count = _change_count()
        except Exception:
            pass

        # Poll every 500 ms
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        # Persist every 30 s
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(30_000)
        self._save_timer.timeout.connect(self._save)
        self._save_timer.start()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll(self):
        try:
            cc = _change_count()
            # changeCount returns -1 when not on macOS — always read in that case
            if cc != -1 and cc == self._last_change_count:
                return
            self._last_change_count = cc

            text = _read_clipboard()
            if not text or text == self._last_text:
                return

            self._last_text = text
            entry = ClipboardEntry(text)
            self._history.insert(0, entry)
            if len(self._history) > self.MAX_HISTORY:
                self._history.pop()

            logging.debug(f"Clipboard captured ({len(text)} chars)")
            self.new_entry.emit(entry)
        except Exception as e:
            logging.debug(f"Clipboard poll error: {e}")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        try:
            if not os.path.exists(_HISTORY_PATH):
                return
            with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._history = [
                ClipboardEntry.from_dict(d)
                for d in data
                if isinstance(d, dict) and "text" in d
            ]
            if self._history:
                self._last_text = self._history[0].text
            logging.debug(f"Clipboard history loaded: {len(self._history)} entries")
        except Exception as e:
            logging.debug(f"Clipboard history load error: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
            with open(_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    [e.to_dict() for e in self._history],
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            logging.debug(f"Clipboard history save error: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def history(self):
        return self._history

    def search(self, query):
        if not query:
            return self._history
        q = query.lower()
        return [e for e in self._history if q in e.text.lower()]

    def clear(self):
        self._history.clear()
        self._last_text = None
        self._save()
