"""Memory sync — upload/download personal.mv2 to Supabase Storage.

Pro users get their personal memory synced across devices.

Usage:
    from src.services.sync import memory_sync

    memory_sync.start(get_token_fn)   # start background periodic sync
    memory_sync.stop()
    memory_sync.sync_now()            # manual trigger
    memory_sync.add_listener(fn)      # fn({"state", "last_synced", "error"})
"""

import logging
import os
import threading
import urllib.request
from typing import Optional

from src.core.config import SUPABASE_URL, PERSONAL_MEM_PATH

_BUCKET    = "memory"
_SYNC_INTERVAL = 300   # seconds (5 minutes)

_lock      = threading.Lock()
_state     = {"state": "idle", "last_synced": None, "error": None}
_listeners: list = []
_stop_evt  = threading.Event()
_get_token = None   # callable → str | None


# ── Public API ─────────────────────────────────────────────────────────────────

def start(get_token_fn):
    """Start background periodic sync.  get_token_fn() must return a valid JWT or None."""
    global _get_token
    _get_token = get_token_fn
    _stop_evt.clear()
    threading.Thread(target=_sync_loop, daemon=True).start()


def stop():
    _stop_evt.set()


def sync_now():
    """Trigger an immediate sync in a background thread."""
    threading.Thread(target=_do_sync, daemon=True).start()


def add_listener(fn):
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn):
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def get_state() -> dict:
    with _lock:
        return dict(_state)


# ── Internal ──────────────────────────────────────────────────────────────────

def _notify(state: str, error=None):
    import datetime
    with _lock:
        _state["state"] = state
        _state["error"] = error
        if state == "synced":
            _state["last_synced"] = datetime.datetime.now().strftime("%H:%M")
    snapshot = get_state()
    for fn in list(_listeners):
        try:
            fn(snapshot)
        except Exception:
            pass


def _sync_loop():
    # On startup: download if cloud is newer
    _do_sync(startup=True)
    while not _stop_evt.wait(_SYNC_INTERVAL):
        _do_sync()


def _do_sync(startup: bool = False):
    if _get_token is None:
        return
    token = _get_token()
    if not token:
        return

    user_id = _user_id_from_token(token)
    if not user_id:
        return

    _notify("syncing")

    if startup:
        # Download if cloud version is newer (or local doesn't exist)
        try:
            _maybe_download(token, user_id)
        except Exception as e:
            logging.debug(f"[sync] startup download: {e}")
            _notify("error", str(e))
            return
    else:
        # Upload local changes
        try:
            _upload(token, user_id)
        except Exception as e:
            logging.debug(f"[sync] upload: {e}")
            _notify("error", str(e))
            return

    _notify("synced")


def _maybe_download(token: str, user_id: str):
    """Download cloud file if it is newer than the local copy."""
    cloud_mtime = _get_cloud_mtime(token, user_id)
    if cloud_mtime is None:
        # No cloud copy yet — upload local if it exists
        if os.path.exists(PERSONAL_MEM_PATH):
            _upload(token, user_id)
        return

    local_mtime = os.path.getmtime(PERSONAL_MEM_PATH) if os.path.exists(PERSONAL_MEM_PATH) else 0
    if cloud_mtime > local_mtime:
        _download(token, user_id)
    else:
        # Local is newer (or same) — upload
        if os.path.exists(PERSONAL_MEM_PATH):
            _upload(token, user_id)


def _get_cloud_mtime(token: str, user_id: str) -> Optional[float]:
    """Return cloud file last-modified as a Unix timestamp, or None if not found."""
    import json, datetime
    url = f"{SUPABASE_URL}/storage/v1/object/list/{_BUCKET}"
    payload = json.dumps({"prefix": f"{user_id}/", "limit": 1}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        items = json.loads(r.read())

    if not items:
        return None

    for item in items:
        if item.get("name") == "personal.mv2":
            updated = item.get("updated_at") or item.get("created_at") or ""
            if updated:
                # ISO 8601 → timestamp
                dt = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
                return dt.timestamp()
    return None


def _upload(token: str, user_id: str):
    if not os.path.exists(PERSONAL_MEM_PATH):
        return
    with open(PERSONAL_MEM_PATH, "rb") as f:
        data = f.read()
    url = f"{SUPABASE_URL}/storage/v1/object/{_BUCKET}/{user_id}/personal.mv2"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type":  "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "x-upsert":      "true",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    logging.debug("[sync] upload complete")


def _download(token: str, user_id: str):
    url = f"{SUPABASE_URL}/storage/v1/object/{_BUCKET}/{user_id}/personal.mv2"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    os.makedirs(os.path.dirname(PERSONAL_MEM_PATH), exist_ok=True)
    with open(PERSONAL_MEM_PATH, "wb") as f:
        f.write(data)
    logging.debug("[sync] download complete")


def _user_id_from_token(token: str) -> Optional[str]:
    """Decode the JWT payload (no verification — Worker verifies; here we just need the user_id)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        padding = 4 - len(parts[1]) % 4
        padded  = parts[1] + "=" * padding
        import json, base64
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("sub")
    except Exception:
        return None
