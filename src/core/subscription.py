"""Subscription / plan management — talks to the Omni Worker backend.

Usage:
    from src.core import subscription

    subscription.refresh_status()          # fire-and-forget background fetch
    status = subscription.get_status()     # { plan, daily_usage, daily_limit, loaded, error }
"""

import json
import logging
import threading
import urllib.request

from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID

# ── Shared state ─────────────────────────────────────────────────────────────

_lock  = threading.Lock()
_cache = {
    "plan":        "free",
    "daily_usage": 0,
    "daily_limit": 10,
    "loaded":      False,
    "error":       None,
}
_listeners: list = []


# ── Public API ────────────────────────────────────────────────────────────────

def get_status() -> dict:
    """Return a copy of the cached status (always instant, never blocks)."""
    with _lock:
        return dict(_cache)


def refresh_status(callback=None):
    """Fetch fresh status in a daemon thread.

    callback(status_dict) is called on the background thread — callers that
    update Qt widgets should bounce via QTimer.singleShot(0, ...).
    All registered listeners are also notified.
    """
    def _run():
        result = _fetch_status()
        with _lock:
            _cache.update(result)
            if not result.get("error"):
                _cache["loaded"] = True
        snapshot = get_status()
        for fn in list(_listeners):
            try:
                fn(snapshot)
            except Exception:
                pass
        if callback:
            try:
                callback(snapshot)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


def add_listener(fn):
    """Register a callable called whenever status is refreshed."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn):
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


# ── Internal ──────────────────────────────────────────────────────────────────

def _fetch_status() -> dict:
    from src.core import auth as _auth
    from src.core.config import SUPABASE_URL, SUPABASE_ANON_KEY
    token = _auth.get_access_token()

    # Primary check: query the subscriptions table directly with the user's JWT.
    # RLS ensures they can only read their own row. This is the most reliable
    # source of truth and doesn't depend on the worker or KV being correct.
    if token:
        try:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/subscriptions"
                f"?select=plan,status&limit=1",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey":        SUPABASE_ANON_KEY,
                },
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                rows = json.loads(r.read())
            if rows and rows[0].get("plan") == "pro" and rows[0].get("status") == "active":
                return {"plan": "pro", "daily_usage": 0, "daily_limit": 999, "error": None}
        except Exception as e:
            print(f"[subscription] supabase check failed: {e}")

    # Fallback: ask the worker (handles anonymous device-ID rate limiting too).
    try:
        headers = {
            "X-Omni-Secret": OMNI_SECRET,
            "X-Device-ID":   DEVICE_ID,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(
            f"{BACKEND_URL}/v1/status",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        return {
            "plan":        data.get("plan", "free"),
            "daily_usage": int(data.get("daily_usage", 0)),
            "daily_limit": int(data.get("daily_limit", 10)),
            "error":       None,
        }
    except Exception as e:
        logging.debug(f"[subscription] fetch failed: {e}")
        return {"error": str(e)}
