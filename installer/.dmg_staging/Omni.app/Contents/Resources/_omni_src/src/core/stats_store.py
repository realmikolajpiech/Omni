"""
Daily tool-call statistics — saved to ~/.config/omni/stats.json.
Counts how many times each tool is called per day (UTC date).
"""
import json
import os
import threading
from datetime import datetime, timezone

_STATS_PATH = os.path.expanduser("~/.config/omni/stats.json")
_lock = threading.Lock()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read() -> dict:
    if not os.path.exists(_STATS_PATH):
        return {}
    try:
        with open(_STATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(_STATS_PATH), exist_ok=True)
    with open(_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def increment_tool(tool_name: str) -> None:
    """Increment the daily call count for *tool_name*."""
    with _lock:
        data = _read()
        today = _today()
        day = data.setdefault(today, {})
        day[tool_name] = day.get(tool_name, 0) + 1
        _write(data)


def get_today() -> dict:
    """Return {tool_name: count} for today (UTC)."""
    with _lock:
        data = _read()
        return dict(data.get(_today(), {}))


def get_all() -> dict:
    """Return the full {date: {tool: count}} history."""
    with _lock:
        return _read()
