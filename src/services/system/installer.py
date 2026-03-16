import subprocess
import logging
import shutil
import sys
import os
import time
import json
import threading
import difflib
import requests
from pathlib import Path
from datetime import datetime


# ── Logging helper ─────────────────────────────────────────────────────────────

def log_debug(msg):
    try:
        with open("/tmp/omni_install.log", "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"LOG DEBUG FAILED: {e}", file=sys.stderr)


# ── Homebrew binary ────────────────────────────────────────────────────────────

def _brew_path():
    """Find the full path to brew — handles Intel (/usr/local) and Apple Silicon (/opt/homebrew)."""
    for candidate in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("brew")
    return found or "brew"


BREW = _brew_path()


def _env():
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    return env


def _run(cmd, timeout=20):
    """Run command with brew path resolved and correct PATH env."""
    if isinstance(cmd, list) and cmd and cmd[0] == "brew":
        cmd = [BREW] + cmd[1:]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=_env(),
            preexec_fn=os.setsid if hasattr(os, 'setsid') else None
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        logging.warning(f"Command timed out after {timeout}s: {cmd}")
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


def _homebrew_available():
    return os.path.isfile(BREW)


# ── Homebrew catalog (cask + formula JSON API) ─────────────────────────────────

_CACHE_DIR = Path.home() / ".local" / "share" / "omni" / "brew_cache"
_CASK_CACHE_PATH = _CACHE_DIR / "casks.json"
_FORMULA_CACHE_PATH = _CACHE_DIR / "formulas.json"
_CACHE_MAX_AGE_S = 86400  # 24 hours

_CASK_API_URL = "https://formulae.brew.sh/api/cask.json"
_FORMULA_API_URL = "https://formulae.brew.sh/api/formula.json"

_catalog_lock = threading.Lock()
_cask_catalog: list | None = None
_formula_catalog: list | None = None
_catalog_ready = threading.Event()


def _cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_MAX_AGE_S


def _load_or_fetch(url: str, cache_path: Path, label: str) -> list:
    if _cache_fresh(cache_path):
        try:
            data = json.loads(cache_path.read_text())
            log_debug(f"Loaded {len(data)} {label} from disk cache")
            return data
        except Exception:
            pass
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            cache_path.write_text(r.text)
            log_debug(f"Fetched {len(data)} {label} from Homebrew API")
            return data
    except Exception as e:
        log_debug(f"Failed to fetch {label}: {e}")
    return []


def _fetch_catalog():
    global _cask_catalog, _formula_catalog
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    casks = _load_or_fetch(_CASK_API_URL, _CASK_CACHE_PATH, "casks")
    formulas = _load_or_fetch(_FORMULA_API_URL, _FORMULA_CACHE_PATH, "formulas")
    with _catalog_lock:
        _cask_catalog = casks
        _formula_catalog = formulas
    _catalog_ready.set()
    log_debug(f"Catalog ready: {len(casks)} casks, {len(formulas)} formulas")


# Start loading catalog in background immediately on module import
threading.Thread(target=_fetch_catalog, daemon=True, name="brew-catalog-loader").start()


def _ensure_catalog():
    """Block until catalog is ready (max 20s on first cold start)."""
    _catalog_ready.wait(timeout=20)


def _norm(s: str) -> str:
    return s.lower().strip().replace(" ", "-").replace("_", "-")


def _match_package(app_name: str):
    """
    Find best package match in Homebrew catalog.
    Returns (kind, token, homepage, desc) or None.
    Prefers casks (GUI apps) over formulas.
    """
    _ensure_catalog()
    with _catalog_lock:
        casks = _cask_catalog or []
        formulas = _formula_catalog or []

    query = _norm(app_name)

    def cask_result(c):
        return ("cask", c["token"], c.get("homepage", ""), c.get("desc", ""))

    def formula_result(f):
        return ("formula", f["name"], f.get("homepage", ""), f.get("desc", ""))

    # 1. Exact cask token
    for c in casks:
        if c["token"] == query:
            return cask_result(c)

    # 2. Exact cask name (names array)
    for c in casks:
        for n in c.get("name", []):
            if _norm(n) == query:
                return cask_result(c)

    # 3. Exact formula name / full_name
    for f in formulas:
        if f["name"] == query or _norm(f.get("full_name", "")) == query:
            return formula_result(f)

    # 4. Formula alias
    for f in formulas:
        for a in f.get("aliases", []):
            if _norm(a) == query:
                return formula_result(f)

    # 5. Fuzzy cask token (cutoff 0.82 avoids spurious matches)
    cask_tokens = [c["token"] for c in casks]
    close = difflib.get_close_matches(query, cask_tokens, n=1, cutoff=0.82)
    if close:
        for c in casks:
            if c["token"] == close[0]:
                return cask_result(c)

    # 6. Substring match in cask token (for short but distinctive queries)
    if len(query) >= 4:
        for c in casks:
            if query in c["token"] or any(query in _norm(n) for n in c.get("name", [])):
                return cask_result(c)

    # 7. Fuzzy formula name
    formula_names = [f["name"] for f in formulas]
    close = difflib.get_close_matches(query, formula_names, n=1, cutoff=0.82)
    if close:
        for f in formulas:
            if f["name"] == close[0]:
                return formula_result(f)

    return None


def get_package_metadata(app_name: str) -> dict | None:
    """
    Return Homebrew metadata for an app: {kind, token, homepage, desc}.
    Uses the cached catalog — no subprocess calls.
    Returns None if not found.
    """
    result = _match_package(app_name)
    if result:
        kind, token, homepage, desc = result
        return {"kind": kind, "token": token, "homepage": homepage, "desc": desc}
    return None


# ── Install plan ───────────────────────────────────────────────────────────────

def generate_install_plan(app_name: str) -> dict:
    """Generate a macOS-native install plan using Homebrew."""
    logging.info(f"Generating Install Plan for: {app_name}")
    t0 = time.monotonic()
    log_debug(f"generate_install_plan START: {app_name!r}")

    if sys.platform != "darwin":
        return {"method": "failed", "description": "Currently only macOS is supported.", "commands": []}

    if not _homebrew_available():
        return {
            "method": "failed",
            "description": "Homebrew is not installed. Please install it from brew.sh first.",
            "commands": []
        }

    # Fast path: catalog lookup (no subprocess, no network after initial load)
    match = _match_package(app_name)
    if match:
        kind, token, homepage, desc = match
        flag = "--cask " if kind == "cask" else ""
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (catalog hit: {kind}/{token})")
        return {
            "method": f"brew_{kind}",
            "description": desc or f"Install {token} via Homebrew",
            "homepage": homepage,
            "token": token,
            "commands": [f"{BREW} install {flag}{token}"]
        }

    # Slow fallback: brew search (handles taps and packages added after catalog was cached)
    log_debug(f"  Catalog miss — falling back to brew search")
    brew_name = _norm(app_name)
    rc, out, _ = _run(["brew", "search", "--cask", brew_name], timeout=20)
    if rc == 0 and out.strip():
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("==>")]
        if lines:
            token = lines[0]
            log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (brew search cask: {token})")
            return {
                "method": "brew_cask",
                "description": f"Installing '{token}' via Homebrew Cask",
                "homepage": "",
                "token": token,
                "commands": [f"{BREW} install --cask {token}"]
            }

    rc, out, _ = _run(["brew", "search", "--formula", brew_name], timeout=20)
    if rc == 0 and out.strip():
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("==>")]
        if lines:
            token = lines[0]
            log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (brew search formula: {token})")
            return {
                "method": "brew_formula",
                "description": f"Found '{token}' in Homebrew",
                "homepage": "",
                "token": token,
                "commands": [f"{BREW} install {token}"]
            }

    log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (not found)")
    return {"method": "failed", "description": f"Could not find '{app_name}' in Homebrew.", "commands": []}


# ── Uninstall plan ─────────────────────────────────────────────────────────────

def generate_uninstall_plan(app_name: str) -> dict:
    """Generate a macOS-native uninstall plan using Homebrew."""
    logging.info(f"Generating Uninstall Plan for: {app_name}")
    t0 = time.monotonic()
    log_debug(f"generate_uninstall_plan START: {app_name!r}")

    if sys.platform != "darwin":
        return {"method": "failed", "description": "Currently only macOS is supported.", "commands": []}

    if not _homebrew_available():
        return {"method": "failed", "description": "Homebrew not found. Cannot uninstall.", "commands": []}

    # Resolve canonical token from catalog
    match = _match_package(app_name)
    if match:
        kind, token, _, _ = match
        flag = "--cask " if kind == "cask" else ""
        list_flag = ["--cask"] if kind == "cask" else []
        rc, out, _ = _run(["brew", "list"] + list_flag + ["--versions", token], timeout=8)
        log_debug(f"  brew list check: rc={rc}, out={out!r}")
        if rc == 0 and out.strip():
            log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (catalog + installed)")
            return {
                "method": f"brew_{kind}_uninstall",
                "description": f"Uninstalling '{token}' via Homebrew",
                "commands": [f"{BREW} uninstall {flag}{token}"]
            }
        else:
            log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (catalog hit but not installed)")
            return {
                "method": "not_installed",
                "description": f"'{app_name}' does not appear to be installed via Homebrew.",
                "commands": []
            }

    # Fallback: check by normalized name directly
    brew_name = _norm(app_name)

    rc, out, _ = _run(["brew", "list", "--cask", "--versions", brew_name], timeout=8)
    log_debug(f"  brew list --cask: rc={rc}")
    if rc == 0 and out.strip():
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (cask installed)")
        return {
            "method": "brew_cask_uninstall",
            "description": f"Uninstalling '{brew_name}' cask via Homebrew",
            "commands": [f"{BREW} uninstall --cask {brew_name}"]
        }

    rc, out, _ = _run(["brew", "list", "--versions", brew_name], timeout=8)
    log_debug(f"  brew list: rc={rc}")
    if rc == 0 and out.strip():
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (formula installed)")
        return {
            "method": "brew_formula_uninstall",
            "description": f"Uninstalling '{brew_name}' formula via Homebrew",
            "commands": [f"{BREW} uninstall {brew_name}"]
        }

    log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (not found)")
    return {"method": "failed", "description": f"Could not find '{app_name}' installed via Homebrew.", "commands": []}
