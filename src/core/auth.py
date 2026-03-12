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

# ── Auth callback HTML pages ──────────────────────────────────────────────────

_PAGE_STYLE = """
<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Omni</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#09090f;color:#f4f4f5;font-family:'Manrope',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0}
.logo{position:fixed;top:28px;left:50%;transform:translateX(-50%);
font-size:13px;font-weight:700;letter-spacing:2px;color:rgba(244,244,245,0.25);text-transform:uppercase}
.card{text-align:center;padding:0 32px;max-width:380px;width:100%}
.icon{width:68px;height:68px;border-radius:50%;display:flex;align-items:center;justify-content:center;
margin:0 auto 28px;font-size:30px}
.icon.success{background:rgba(52,199,89,0.10);border:1px solid rgba(52,199,89,0.22)}
.icon.error{background:rgba(255,80,80,0.10);border:1px solid rgba(255,80,80,0.22)}
h1{font-size:26px;font-weight:600;letter-spacing:-0.4px;margin-bottom:12px;line-height:1.2}
p{color:rgba(244,244,245,0.42);font-size:14px;font-weight:300;line-height:1.7}
.badge{display:inline-block;margin-top:32px;padding:8px 20px;border-radius:100px;
background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);
font-size:12px;color:rgba(244,244,245,0.30);letter-spacing:0.5px}
</style></head><body>
<div class="logo">Omni</div>
"""

_SUCCESS_HTML = (_PAGE_STYLE + """
<div class="card">
  <div class="icon success">✓</div>
  <h1>You're signed in</h1>
  <p>You can close this tab and return to Omni.</p>
  <div class="badge">Authentication complete</div>
</div>
</body></html>
""").encode("utf-8")

_ERROR_HTML = (_PAGE_STYLE + """
<div class="card">
  <div class="icon error">✕</div>
  <h1>Authentication failed</h1>
  <p>Something went wrong. Close this tab and try again.</p>
  <div class="badge">You can close this tab</div>
</div>
</body></html>
""").encode("utf-8")

_PENDING_HTML = (_PAGE_STYLE + """
<div class="card">
  <div class="icon" style="background:rgba(139,92,246,0.10);border:1px solid rgba(139,92,246,0.22);font-size:22px">⟳</div>
  <h1>Signing you in…</h1>
  <p>Just a moment.</p>
</div>
<script>
var h=window.location.hash.substring(1);
var params=new URLSearchParams(h);
var at=params.get('access_token');
var rt=params.get('refresh_token')||'';
if(at){
  fetch('/token',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({access_token:at,refresh_token:rt})})
  .then(function(){
    document.querySelector('h1').textContent='You\\'re signed in';
    document.querySelector('p').textContent='You can close this tab and return to Omni.';
    document.querySelector('.icon').className='icon success';
    document.querySelector('.icon').textContent='✓';
    document.querySelector('.badge').textContent='Authentication complete';
  });
}else{
  fetch('/error');
  document.querySelector('h1').textContent='Authentication failed';
  document.querySelector('p').textContent='Something went wrong. Close this tab and try again.';
  document.querySelector('.icon').className='icon error';
  document.querySelector('.icon').textContent='✕';
}
</script>
</body></html>
""").encode("utf-8")
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

def load_saved_session(on_done=None):
    """Load stored tokens and refresh them in a background thread.

    on_done() is called on the background thread after the refresh completes
    (whether it succeeds or fails), so callers can chain work that needs the
    session to be populated first.
    """
    def _run(rt):
        _refresh_tokens(rt)
        if on_done:
            try:
                on_done()
            except Exception:
                pass

    try:
        if not os.path.exists(_TOKEN_FILE):
            return
        with open(_TOKEN_FILE) as f:
            data = json.load(f)
        rt = data.get("refresh_token", "")
        if rt:
            threading.Thread(target=_run, args=(rt,), daemon=True).start()
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
    import urllib.error
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
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            msg = body.get("error_description") or body.get("msg") or body.get("error") or "Sign-in failed."
        except Exception:
            msg = "Invalid email or password."
        return False, msg
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
    # Reset cached subscription plan so the upgrade box shows immediately
    # after logout instead of keeping a stale "pro" plan from the old session.
    try:
        from src.core import subscription as _sub
        _sub.reset()
    except Exception:
        pass


def exchange_magic_link(url: str, on_done) -> None:
    """Exchange a Supabase magic link for a live session.

    Opens *url* in the browser (it redirects to localhost:5579/magic-callback
    with tokens in the URL fragment), serves a tiny JS page that extracts the
    fragment tokens and POSTs them back to the local server, then saves the
    session.  *on_done(success: bool, msg: str)* is called on a background thread.
    """
    result: dict = {"access_token": None, "refresh_token": None, "error": None}
    done_event = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/magic-callback", "/magic_callback"):
                html = _PENDING_HTML
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            elif parsed.path == "/error":
                result["error"] = "authentication failed"
                done_event.set()
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(204)
                self.end_headers()

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/token":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length))
                    result["access_token"]  = data.get("access_token")
                    result["refresh_token"] = data.get("refresh_token", "")
                except Exception:
                    result["error"] = "token parse error"
                done_event.set()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    def _run():
        import time
        try:
            httpd = HTTPServer(("localhost", _OAUTH_PORT), _Handler)
            webbrowser.open(url)
            start = time.time()
            while not done_event.is_set():
                if time.time() - start > 300:
                    result["error"] = "timed out"
                    break
                httpd.timeout = 1.0
                httpd.handle_request()
            httpd.server_close()
        except Exception as e:
            result["error"] = str(e)

        if result["access_token"]:
            try:
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/auth/v1/user",
                    headers={
                        "Authorization": f"Bearer {result['access_token']}",
                        "apikey":        SUPABASE_ANON_KEY,
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as r:
                    user = json.loads(r.read())
                _save_session(result["access_token"], result.get("refresh_token", ""), user)
                on_done(True, "Signed in!")
            except Exception as e:
                on_done(False, f"Failed to get user info: {e}")
        else:
            on_done(False, result.get("error") or "Magic link exchange failed.")

    threading.Thread(target=_run, daemon=True).start()


def send_password_reset(email: str) -> tuple[bool, str]:
    """Send a Supabase password-recovery email that redirects to the setup page."""
    import urllib.parse
    try:
        redirect = urllib.parse.quote("https://heyomni.app/setup", safe="")
        payload = json.dumps({"email": email.strip()}).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/recover?redirect_to={redirect}",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "apikey":       SUPABASE_ANON_KEY,
            },
        )
        with urllib.request.urlopen(req, timeout=12):
            pass
        return True, "Check your email for a password reset link."
    except Exception as e:
        return False, f"Could not send reset email: {e}"


def update_password(new_password: str) -> tuple[bool, str]:
    """Update the password for the currently signed-in user."""
    token = get_access_token()
    if not token:
        return False, "Not signed in."
    try:
        payload = json.dumps({"password": new_password}).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/auth/v1/user",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "apikey":        SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {token}",
            },
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
        if data.get("error"):
            return False, data["error"].get("message", "Failed to update password.")
        rt = get_session().get("refresh_token", "")
        _save_session(token, rt, data)
        return True, "Password set! You can now sign in with email + password."
    except Exception as e:
        return False, f"Connection error: {e}"


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
                self._respond(_SUCCESS_HTML)
            else:
                result["error"] = params.get("error", ["Unknown error"])[0]
                self._respond(_ERROR_HTML)

        def _respond(self, body: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
