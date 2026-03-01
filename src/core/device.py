"""Stable per-installation device ID.

Generated once on first run, persisted to ~/.config/omni/.env.
Sent as X-Device-ID with every request to the Omni backend so the
Worker can track free-tier usage and subscription status per device.
"""

import os
import uuid
from pathlib import Path

_ENV_FILE = Path.home() / ".config" / "omni" / ".env"
_KEY      = "OMNI_DEVICE_ID"


def get_device_id() -> str:
    """Return the stable device ID, generating and saving it if needed."""
    existing = os.environ.get(_KEY, "").strip()
    if existing:
        return existing

    # Check the .env file directly (may not be loaded into environ yet)
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{_KEY}="):
                val = line.split("=", 1)[1].strip()
                if val:
                    os.environ[_KEY] = val
                    return val

    # First run — generate and persist
    device_id = str(uuid.uuid4())
    os.environ[_KEY] = device_id
    _persist(device_id)
    return device_id


def _persist(device_id: str):
    try:
        _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = _ENV_FILE.read_text() if _ENV_FILE.exists() else ""
        if _KEY not in existing:
            with _ENV_FILE.open("a") as f:
                f.write(f"\n{_KEY}={device_id}\n")
    except Exception:
        pass  # non-fatal — ID is still usable for this session
