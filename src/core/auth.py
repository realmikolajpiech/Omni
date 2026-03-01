"""Supabase-based auth for Omni.

Usage:
    from src.core import auth

    auth.load_saved_session()           # call once on startup
    auth.sign_in(email, password)       # → (bool, str)
    auth.sign_up(email, password)       # → (bool, str)
    auth.start_oauth("google", cb)      # opens browser, cb(ok, msg)
    auth.sign_out()
    auth.get_access_token()             # → str | None
    auth.get_user()                     # → {"id", "email", "user_metadata"} | None
    auth.add_listener(fn)               # fn(session_snapshot) called on change
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from src.core.config import SUPABASE_URL, SUPABASE_ANON_KEY

_CONFIG_DIR  = os.path.expanduser("~/.config/omni")
_TOKEN_FILE  = os.path.join(_CONFIG_DIR, "auth.json")
_OAUTH_PORT  = 5579   # fixed localhost callback port; add http://localhost:5579 to Supabase allowed origins

_lock      = threading.Lock()
_session   = {"access_token": None, "refresh_token": None, "user": None}
_listeners: list = []

# ── Public API ─────────────────────────────────────────────────────────────────

def get_session() -> dict:
    with _lock:
        return dict(_session)


def get_access_token() -> Optional[str]:
    with _lock:
        return _session.get("access_token")


def get_user() -> Optional[dict]:
    with _lock:
        return _session.get("user")


def is_logged_in() -> bool:
    with _lock:
        return bool(_session.get("access_token"))


def add_listener(fn):
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn):
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


# ── Session persistence ────────────────────────────────────────────────────────

def load_saved_session():
    """Load stored tokens and refresh them in a background thread."""
    try:
        if not os.path.exists(_TOKEN_FILE):
            return
        with open(_TOKEN_FILE) as f:
            data = json.load(f)
        rt = data.get("refresh_token", "")
        if rt:
            threading.Thread(target=lambda: _refresh_tokens(rt), daemon=True).start()
    except Exception as e:
        logging.debug(f"[auth] load_saved_session: {e}")


def _save_session(access_token: str, refresh_token: str, user: dict):
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    try:
        with open(_TOKEN_FILE, "w") as f:
            json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
    except Exception as e:
        logging.debug(f"[auth] _save_session write: {e}")
    with _lock:
        _session["access_token"]  = access_token
        _session["refresh_token"] = refresh_token
        _session["user"]          = user
    _notify()


def _clear_session():
    if os.path.exists(_TOKEN_FILE):
        try:
            os.remove(_TOKEN_FILE)
        except Exception:
            pass
    with _lock:
        _session["access_token"]  = None
        _session["refresh_token"] = None
        _session["user"]          = None
    _notify()


def _notify():
    snapshot = get_session()
    for fn in list(_listeners):
        try:
            fn(snapshot)
        except Exception:
            pass


# ── Auth calls ────────────────────────────────────────────────────────────────

def sign_up(email: str, password: str) -> tuple[bool, str]:
    try:
        payload = json.dumps({"email": email.strip(), "password": password}).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/signup",
            data=payload,
            headers={"Content-Type": "application/json", "apikey": SUPABASE_ANON_KEY},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())

        if data.get("error"):
            return False, data["error"].get("message", "Sign-up failed.")

        # Email confirmation required
        if not data.get("access_token"):
            return True, "Check your email to confirm your account, then sign in."

        _save_session(data["access_token"], data.get("refresh_token", ""), data.get("user", {}))
        return True, "Account created!"
    except Exception as e:
        return False, f"Connection error: {e}"


def sign_in(email: str, password: str) -> tuple[bool, str]:
    try:
        payload = json.dumps({"email": email.strip(), "password": password}).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            data=payload,
            headers={"Content-Type": "application/json", "apikey": SUPABASE_ANON_KEY},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())

        if data.get("error"):
            return False, data.get("error_description", data.get("error", "Sign-in failed."))

        _save_session(data["access_token"], data.get("refresh_token", ""), data.get("user", {}))
        return True, "Signed in!"
    except Exception as e:
        return False, f"Connection error: {e}"


def sign_out():
    token = get_access_token()
    if token:
        try:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/auth/v1/logout",
                data=b"{}",
                headers={
                    "Content-Type":  "application/json",
                    "apikey":        SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception:
            pass
    _clear_session()


def start_oauth(provider: str, on_done) -> None:
    """Open browser OAuth flow (PKCE).  on_done(success: bool, msg: str) is called on background thread."""
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    redirect_uri = f"http://localhost:{_OAUTH_PORT}/callback"
    auth_url = (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider={provider}"
        f"&redirect_to={urllib.parse.quote(redirect_uri)}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )

    result = {"code": None, "error": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                result["code"] = params["code"][0]
                self._respond(b"<html><body><h2>Signed in! You can close this tab.</h2></body></html>")
            else:
                result["error"] = params.get("error", ["Unknown error"])[0]
                self._respond(b"<html><body><h2>Authentication failed. Close this tab.</h2></body></html>")

        def _respond(self, body: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def _run():
        try:
            httpd = HTTPServer(("localhost", _OAUTH_PORT), _Handler)
            httpd.timeout = 120
            webbrowser.open(auth_url)
            httpd.handle_request()
            httpd.server_close()
        except Exception as e:
            result["error"] = str(e)

        if result["code"]:
            _exchange_code(result["code"], verifier, redirect_uri, on_done)
        else:
            on_done(False, result.get("error") or "OAuth cancelled.")

    threading.Thread(target=_run, daemon=True).start()


# ── Internal ──────────────────────────────────────────────────────────────────

def _exchange_code(code: str, verifier: str, redirect_uri: str, on_done):
    try:
        payload = json.dumps({
            "auth_code":     code,
            "code_verifier": verifier,
            "redirect_uri":  redirect_uri,
        }).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=pkce",
            data=payload,
            headers={"Content-Type": "application/json", "apikey": SUPABASE_ANON_KEY},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())

        if data.get("error"):
            on_done(False, data.get("error_description", "OAuth exchange failed."))
            return

        _save_session(data["access_token"], data.get("refresh_token", ""), data.get("user", {}))
        on_done(True, "Signed in!")
    except Exception as e:
        on_done(False, f"Connection error: {e}")


def _refresh_tokens(refresh_token: str):
    try:
        payload = json.dumps({"refresh_token": refresh_token}).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            data=payload,
            headers={"Content-Type": "application/json", "apikey": SUPABASE_ANON_KEY},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())

        if data.get("error"):
            logging.debug(f"[auth] token refresh failed: {data}")
            _clear_session()
            return

        _save_session(data["access_token"], data.get("refresh_token", refresh_token), data.get("user", {}))
    except Exception as e:
        logging.debug(f"[auth] _refresh_tokens: {e}")
