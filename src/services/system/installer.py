import subprocess
import logging
import shutil
import sys
import os
import time
from datetime import datetime


def log_debug(msg):
    try:
        with open("/tmp/omni_install.log", "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"LOG DEBUG FAILED: {e}", file=sys.stderr)


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
            # Ensure child processes are killed on timeout too
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


def _brew_search(app_name):
    """Search Homebrew — cask first (GUI apps), then formula (CLI tools)."""
    rc, out, _ = _run(["brew", "search", "--cask", app_name], timeout=20)
    if rc == 0 and out.strip():
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("==>")]
        if lines:
            return "cask", lines[0]
    rc, out, _ = _run(["brew", "search", "--formula", app_name], timeout=20)
    if rc == 0 and out.strip():
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("==>")]
        if lines:
            return "formula", lines[0]
    return None, None


# Well-known apps — checked FIRST to avoid slow network calls
KNOWN = {
    "steam": ("cask", "steam"),
    "discord": ("cask", "discord"),
    "spotify": ("cask", "spotify"),
    "chrome": ("cask", "google-chrome"),
    "google-chrome": ("cask", "google-chrome"),
    "vscode": ("cask", "visual-studio-code"),
    "visual-studio-code": ("cask", "visual-studio-code"),
    "firefox": ("cask", "firefox"),
    "slack": ("cask", "slack"),
    "zoom": ("cask", "zoom"),
    "vlc": ("cask", "vlc"),
    "libreoffice": ("cask", "libreoffice"),
    "notion": ("cask", "notion"),
    "figma": ("cask", "figma"),
    "telegram": ("cask", "telegram"),
    "whatsapp": ("cask", "whatsapp"),
    "1password": ("cask", "1password"),
    "alfred": ("cask", "alfred"),
    "iterm2": ("cask", "iterm2"),
    "git": ("formula", "git"),
    "node": ("formula", "node"),
    "python": ("formula", "python@3.12"),
    "ffmpeg": ("formula", "ffmpeg"),
    "wget": ("formula", "wget"),
    "cursor": ("cask", "cursor"),
    "arc": ("cask", "arc"),
    "obsidian": ("cask", "obsidian"),
    "raycast": ("cask", "raycast"),
    "warp": ("cask", "warp"),
}


def generate_install_plan(app_name):
    """Generate a macOS-native install plan using Homebrew."""
    logging.info(f"Generating Install Plan for: {app_name}")
    t0 = time.monotonic()
    log_debug(f"generate_install_plan START: {app_name!r}")

    if sys.platform != "darwin":
        return {"method": "failed", "description": "Currently only macOS is supported.", "commands": []}

    if not _homebrew_available():
        return {
            "method": "brew_install_required",
            "description": "Homebrew not found. Install Homebrew first.",
            "commands": [
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            ]
        }

    brew_name = app_name.lower().replace(" ", "-").replace("_", "-")

    # 1. Check KNOWN map first (fast, no network)
    t1 = time.monotonic()
    matched = KNOWN.get(brew_name) or KNOWN.get(app_name.lower())
    log_debug(f"  [step 1] KNOWN lookup: {time.monotonic()-t1:.3f}s → matched={matched}")
    if matched:
        kind, pkg = matched
        flag = "--cask " if kind == "cask" else ""
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (KNOWN hit)")
        return {
            "method": f"brew_{kind}",
            "description": f"Installing '{pkg}' via Homebrew",
            "commands": [f"{BREW} install {flag}{pkg}"]
        }

    # 2. Try exact cask match (reduced timeout to keep total latency reasonable)
    t2 = time.monotonic()
    rc, out, _ = _run(["brew", "info", "--cask", brew_name], timeout=10)
    log_debug(f"  [step 2] brew info --cask: {time.monotonic()-t2:.3f}s → rc={rc}")
    if rc == 0:
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (cask hit)")
        return {
            "method": "brew_cask",
            "description": f"Installing '{brew_name}' via Homebrew Cask",
            "commands": [f"{BREW} install --cask {brew_name}"]
        }

    # 3. Try exact formula match
    t3 = time.monotonic()
    rc, out, _ = _run(["brew", "info", "--formula", brew_name], timeout=10)
    log_debug(f"  [step 3] brew info --formula: {time.monotonic()-t3:.3f}s → rc={rc}")
    if rc == 0:
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (formula hit)")
        return {
            "method": "brew_formula",
            "description": f"Installing '{brew_name}' via Homebrew",
            "commands": [f"{BREW} install {brew_name}"]
        }

    # 4. Fuzzy search (last resort, can be slow)
    t4 = time.monotonic()
    kind, pkg = _brew_search(brew_name)
    log_debug(f"  [step 4] brew search (cask+formula): {time.monotonic()-t4:.3f}s → kind={kind}, pkg={pkg}")
    if pkg:
        flag = "--cask " if kind == "cask" else ""
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (search hit)")
        return {
            "method": f"brew_{kind}",
            "description": f"Found '{pkg}' in Homebrew",
            "commands": [f"{BREW} install {flag}{pkg}"]
        }

    log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (not found)")
    return {"method": "failed", "description": f"Could not find '{app_name}' in Homebrew.", "commands": []}


def generate_uninstall_plan(app_name):
    """Generate a macOS-native uninstall plan using Homebrew."""
    logging.info(f"Generating Uninstall Plan for: {app_name}")
    t0 = time.monotonic()
    log_debug(f"generate_uninstall_plan START: {app_name!r}")

    if sys.platform != "darwin":
        return {"method": "failed", "description": "Currently only macOS is supported.", "commands": []}

    if not _homebrew_available():
        return {"method": "failed", "description": "Homebrew not found. Cannot uninstall.", "commands": []}

    brew_name = app_name.lower().replace(" ", "-").replace("_", "-")

    # 1. Resolve canonical name via KNOWN map, then verify it's actually installed
    t1 = time.monotonic()
    matched = KNOWN.get(brew_name) or KNOWN.get(app_name.lower())
    log_debug(f"  [step 1] KNOWN lookup: {time.monotonic()-t1:.3f}s → matched={matched}")
    if matched:
        kind, pkg = matched
        flag = "--cask " if kind == "cask" else ""
        list_flag = ["--cask"] if kind == "cask" else []
        t1b = time.monotonic()
        rc, out, _ = _run(["brew", "list"] + list_flag + ["--versions", pkg], timeout=8)
        log_debug(f"  [step 1b] brew list --versions: {time.monotonic()-t1b:.3f}s → rc={rc}, installed={bool(rc==0 and out.strip())}")
        if rc == 0 and out.strip():
            log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (KNOWN+installed)")
            return {
                "method": f"brew_{kind}_uninstall",
                "description": f"Uninstalling '{pkg}' via Homebrew",
                "commands": [f"{BREW} uninstall {flag}{pkg}"]
            }
        else:
            log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (KNOWN but not installed)")
            return {
                "method": "not_installed",
                "description": f"'{app_name}' does not appear to be installed via Homebrew.",
                "commands": []
            }

    # 2. Check if installed as cask
    t2 = time.monotonic()
    rc, out, _ = _run(["brew", "list", "--cask", "--versions", brew_name], timeout=8)
    log_debug(f"  [step 2] brew list --cask --versions: {time.monotonic()-t2:.3f}s → rc={rc}")
    if rc == 0 and out.strip():
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (cask installed)")
        return {
            "method": "brew_cask_uninstall",
            "description": f"Uninstalling '{brew_name}' cask via Homebrew",
            "commands": [f"{BREW} uninstall --cask {brew_name}"]
        }

    # 3. Check if installed as formula
    t3 = time.monotonic()
    rc, out, _ = _run(["brew", "list", "--versions", brew_name], timeout=8)
    log_debug(f"  [step 3] brew list --versions: {time.monotonic()-t3:.3f}s → rc={rc}")
    if rc == 0 and out.strip():
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (formula installed)")
        return {
            "method": "brew_formula_uninstall",
            "description": f"Uninstalling '{brew_name}' formula via Homebrew",
            "commands": [f"{BREW} uninstall {brew_name}"]
        }

    # 4. Try cask info (exists in brew but may not be installed)
    t4 = time.monotonic()
    rc, _, _ = _run(["brew", "info", "--cask", brew_name], timeout=10)
    log_debug(f"  [step 4] brew info --cask: {time.monotonic()-t4:.3f}s → rc={rc}")
    if rc == 0:
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (cask exists, not installed)")
        return {
            "method": "not_installed",
            "description": f"'{app_name}' does not appear to be installed via Homebrew.",
            "commands": []
        }

    # 5. Try formula info
    t5 = time.monotonic()
    rc, _, _ = _run(["brew", "info", "--formula", brew_name], timeout=10)
    log_debug(f"  [step 5] brew info --formula: {time.monotonic()-t5:.3f}s → rc={rc}")
    if rc == 0:
        log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (formula exists, not installed)")
        return {
            "method": "not_installed",
            "description": f"'{app_name}' does not appear to be installed via Homebrew.",
            "commands": []
        }

    log_debug(f"  TOTAL: {time.monotonic()-t0:.3f}s (not found)")
    return {"method": "failed", "description": f"Could not find '{app_name}' in Homebrew.", "commands": []}
