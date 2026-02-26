"""
Persistent user settings store — saved to ~/.config/omni/settings.json.
Uses a simple JSON file with in-process caching.
"""
import os
import json
import threading

_SETTINGS_PATH = os.path.expanduser("~/.config/omni/settings.json")

_DEFAULTS: dict = {
    "transcription_language": "auto",
    "custom_api_url": "",
    "custom_api_key": "",
    "custom_model": "",
    "personality_mode": "professional",
}

_lock = threading.Lock()
_cache = None  # type: dict | None


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)


def _read_file() -> dict:
    """Read raw JSON from disk, return empty dict on any error."""
    if not os.path.exists(_SETTINGS_PATH):
        return {}
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_settings() -> dict:
    """Return current settings, using in-process cache."""
    global _cache
    with _lock:
        if _cache is None:
            _ensure_dir()
            _cache = {**_DEFAULTS, **_read_file()}
        return dict(_cache)


def save_settings(data: dict) -> None:
    """Merge *data* into current settings and persist to disk."""
    global _cache
    with _lock:
        _ensure_dir()
        current = _read_file()
        merged = {**_DEFAULTS, **current, **data}
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        _cache = merged


def get(key: str, default=None):
    """Get a single setting value."""
    return load_settings().get(key, default)


def set(key: str, value) -> None:
    """Set a single setting value and persist."""
    save_settings({key: value})
