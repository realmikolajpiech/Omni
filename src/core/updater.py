"""Auto-update helper for Omni.

Flow:
  check_update(current)  → (tag, download_url, changelog) | (None, None, None)
  apply_update(url, tag, cb) → downloads zip, rsyncs new source over the
                                running project root, saves installed version.
                                The caller does NOT need to quit the app.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from src.core.config import BACKEND_URL, OMNI_SECRET

_RELEASE_URL = f"{BACKEND_URL}/v1/release/latest"

# The actual project root where the app is running from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _vtuple(tag: str) -> tuple:
    """Parse 'v1.2.3' or '1.2.3' into (1, 2, 3)."""
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("."))
    except ValueError:
        return (0,)


def check_update(current_version: str):
    """
    Query the Omni worker for the latest GitHub release.
    Returns (latest_tag, download_url, changelog_body) if a newer release exists,
    otherwise (None, None, None).
    Skips if this version was already installed via a previous update.
    Network errors are swallowed and logged at DEBUG level.
    """
    try:
        # Skip if we already installed this update (pending restart)
        from src.core import settings_store
        already = settings_store.get("updated_to_version")
        if already and _vtuple(already) >= _vtuple(current_version):
            # We've already updated past our running version — don't nag
            pass

        req = urllib.request.Request(
            _RELEASE_URL,
            headers={"User-Agent": "Omni-Updater/1.0", "X-Omni-Secret": OMNI_SECRET},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        tag          = data.get("tag_name", "")
        download_url = data.get("download_url") or data.get("zipball_url", "")
        body         = data.get("body", "No release notes available.")

        if tag and download_url and _vtuple(tag) > _vtuple(current_version):
            # If we already applied this exact version, don't show it again
            if already and _vtuple(already) >= _vtuple(tag):
                logging.debug(f"[updater] skipping {tag}, already installed (pending restart)")
                return None, None, None
            return tag, download_url, body

    except Exception as e:
        logging.debug(f"[updater] check_update: {e}")

    return None, None, None


def apply_update(download_url: str, tag: str, on_progress=None):
    """
    Download the GitHub release zip, extract it, and rsync the new source
    directly over the running project root.  Saves the installed version
    to settings so the update dialog is not shown again.

    The caller does NOT need to quit the app — changes take effect on
    next restart.

    on_progress(pct: int, msg: str) — optional UI callback, called on the
    calling thread (run this in a QThread to keep the UI responsive).
    """

    def _prog(pct, msg):
        logging.info(f"[updater] {pct}% — {msg}")
        if on_progress:
            on_progress(pct, msg)

    tmp_dir  = tempfile.mkdtemp(prefix="omni_update_")
    zip_path = os.path.join(tmp_dir, "omni_update.zip")

    # ── 1. Download ───────────────────────────────────────────────────────────
    _prog(0, "Downloading update…")
    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "Omni-Updater/1.0", "X-Omni-Secret": OMNI_SECRET},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            total      = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        _prog(int(downloaded / total * 50), "Downloading update…")
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Download failed: {e}")

    # ── 2. Extract ────────────────────────────────────────────────────────────
    _prog(50, "Extracting…")
    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir)
    try:
        shutil.unpack_archive(zip_path, extract_dir, "zip")
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Extraction failed: {e}")

    # GitHub zips have a single top-level directory: user-Repo-<sha>/
    entries = os.listdir(extract_dir)
    source_dir = (
        os.path.join(extract_dir, entries[0])
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0]))
        else extract_dir
    )

    # ── 3. Rsync new source over the running project root ────────────────────
    _prog(65, "Applying update…")
    install_dir = str(_PROJECT_ROOT)
    try:
        result = subprocess.run(
            [
                "rsync", "-a",
                "--exclude=.env",
                "--exclude=data/",
                "--exclude=logs/",
                "--exclude=venv/",
                "--exclude=.venv/",
                "--exclude=*.pyc",
                "--exclude=__pycache__/",
                "--exclude=.git/",
                "--exclude=.claude/",
                f"{source_dir}/",
                f"{install_dir}/",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "rsync failed")
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("Update apply timed out")
    except RuntimeError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    # ── 4. Save installed version & clean up ─────────────────────────────────
    _prog(90, "Finishing up…")
    try:
        from src.core import settings_store
        settings_store.set("updated_to_version", tag.lstrip("v"))
    except Exception as e:
        logging.warning(f"[updater] could not save updated version: {e}")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    _prog(100, "Update complete!")


def restart_omni():
    """
    Kill every Omni-related Python process and relaunch via run.sh.

    Spawns a detached shell script so the relaunch survives the current
    process exiting.  Callers should call QApplication.quit() immediately
    after this returns.
    """
    run_sh = _PROJECT_ROOT / "run.sh"

    # Fall back to the installed location on macOS
    if not run_sh.exists():
        installed = Path.home() / "Library" / "Application Support" / "Omni" / "run.sh"
        if installed.exists():
            run_sh = installed

    kill_cmds = "\n".join([
        "pkill -f 'src/app/brain.py'          2>/dev/null || true",
        "pkill -f 'src/services/voice/listener.py' 2>/dev/null || true",
        "pkill -f 'src/services/search/watcher.py' 2>/dev/null || true",
        "pkill -f 'src/services/search/indexer.py' 2>/dev/null || true",
        "pkill -f 'run.py'                     2>/dev/null || true",
        "lsof -ti :5555 | xargs kill -9        2>/dev/null || true",
        "sleep 0.5",
    ])

    script = (
        "#!/usr/bin/env bash\n"
        "sleep 1\n"
        f"{kill_cmds}\n"
        f'exec bash "{run_sh}"\n'
    )

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", prefix="omni_restart_", delete=False
    )
    tmp.write(script)
    tmp.flush()
    tmp.close()
    os.chmod(tmp.name, 0o755)

    subprocess.Popen(
        ["bash", tmp.name],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
