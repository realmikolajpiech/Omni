"""Auto-update helper for Omni.

Flow:
  check_update(current)  → (tag, url, changelog) | (None, None, None)
  apply_update(url, cb)  → For DMG URLs: download → mount → rsync app → unmount → relaunch.
                           For ZIP URLs: download → extract → rsync source → relaunch.
  The caller is responsible for quitting the Qt app after apply_update returns.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from src.core.config import BACKEND_URL, OMNI_SECRET

INSTALL_DIR = Path.home() / "Library" / "Application Support" / "Omni"
_RELEASE_URL = f"{BACKEND_URL}/v1/release/latest"


def _vtuple(tag: str) -> tuple:
    """Parse 'v1.2.3' or '1.2.3' into (1, 2, 3)."""
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("."))
    except ValueError:
        return (0,)


def check_update(current_version: str):
    """
    Query the Omni worker for the latest GitHub release.
    Returns (latest_tag, update_url, changelog_body) if a newer release exists
    and a DMG is available, otherwise (None, None, None).
    If the release exists but the DMG hasn't been uploaded yet, returns
    (None, None, None) so the hourly timer retries later.
    """
    try:
        req = urllib.request.Request(
            _RELEASE_URL,
            headers={"User-Agent": "Omni-Updater/1.0", "X-Omni-Secret": OMNI_SECRET},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        tag     = data.get("tag_name", "")
        dmg_url = data.get("dmg_url", "")
        body    = data.get("body", "No release notes available.")

        # Only notify if a DMG is actually available — if the release was just
        # created and the DMG hasn't been uploaded yet, skip and retry next hour.
        if tag and dmg_url and _vtuple(tag) > _vtuple(current_version):
            return tag, dmg_url, body

    except Exception as e:
        logging.debug(f"[updater] check_update: {e}")

    return None, None, None


# ── Shared helpers ────────────────────────────────────────────────────────────

def _emit(on_progress, pct: int, msg: str):
    logging.info(f"[updater] {pct}% — {msg}")
    if on_progress:
        on_progress(pct, msg)


def _download(url: str, dest: str, on_progress, start_pct: int, end_pct: int):
    """Download url → dest, reporting progress in [start_pct, end_pct]."""
    req = urllib.request.Request(url, headers={"User-Agent": "Omni-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        total      = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        last_pct   = start_pct
        with open(dest, "wb") as f:
            while chunk := resp.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    new_pct = start_pct + int(downloaded / total * (end_pct - start_pct))
                    if new_pct > last_pct:
                        last_pct = new_pct
                        _emit(on_progress, new_pct, "Downloading update…")


# ── Public entry point ────────────────────────────────────────────────────────

def apply_update(url: str, on_progress=None):
    """
    Download and apply the update in-place.
      DMG URL  → mount, rsync .app → /Applications/Omni.app, unmount, relaunch.
      ZIP URL  → extract, rsync source → INSTALL_DIR via shell helper, relaunch.
    The caller must quit the Qt application afterwards.
    """
    if "/release/dmg" in url or url.lower().endswith(".dmg"):
        _apply_dmg(url, on_progress)
    else:
        _apply_zip(url, on_progress)


# ── DMG strategy (macOS packaged app) ────────────────────────────────────────

def _apply_dmg(url: str, on_progress=None):
    tmp_dir     = tempfile.mkdtemp(prefix="omni_update_")
    dmg_path    = os.path.join(tmp_dir, "omni_update.dmg")
    mount_point = os.path.join(tmp_dir, "mount")
    os.makedirs(mount_point)

    # ── 1. Download ───────────────────────────────────────────────────────────
    _emit(on_progress, 0, "Downloading update…")
    try:
        _download(url, dmg_path, on_progress, 0, 55)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Download failed: {e}")

    # ── 2. Mount ──────────────────────────────────────────────────────────────
    _emit(on_progress, 55, "Mounting disk image…")
    try:
        subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-quiet",
             "-mountpoint", mount_point, dmg_path],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Mount failed: {e.stderr.decode()}")

    try:
        # ── 3. Find .app in mounted volume ────────────────────────────────────
        _emit(on_progress, 60, "Installing update…")
        import glob as _glob
        app_sources = _glob.glob(os.path.join(mount_point, "*.app"))
        if not app_sources:
            raise RuntimeError("No .app bundle found in disk image")
        app_source = app_sources[0]

        # ── 4. Rsync .app → /Applications/Omni.app ───────────────────────────
        result = subprocess.run(
            ["rsync", "-a", "--delete",
             f"{app_source}/", "/Applications/Omni.app/"],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Copy failed: {result.stderr.decode()}")

    finally:
        # ── 5. Detach ─────────────────────────────────────────────────────────
        try:
            subprocess.run(
                ["hdiutil", "detach", mount_point, "-quiet"],
                check=False, capture_output=True,
            )
        except Exception:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── 6. Relaunch detached ─────────────────────────────────────────────────
    _emit(on_progress, 95, "Relaunching…")
    subprocess.Popen(
        ["open", "/Applications/Omni.app"],
        close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _emit(on_progress, 100, "Done — relaunching Omni…")


# ── ZIP strategy (source / dev install) ──────────────────────────────────────

def _apply_zip(url: str, on_progress=None):
    tmp_dir  = tempfile.mkdtemp(prefix="omni_update_")
    zip_path = os.path.join(tmp_dir, "omni_update.zip")

    # ── 1. Download ───────────────────────────────────────────────────────────
    _emit(on_progress, 0, "Downloading update…")
    try:
        _download(url, zip_path, on_progress, 0, 55)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Download failed: {e}")

    # ── 2. Extract ────────────────────────────────────────────────────────────
    _emit(on_progress, 55, "Extracting…")
    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir)
    try:
        shutil.unpack_archive(zip_path, extract_dir, "zip")
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Extraction failed: {e}")

    # GitHub zips have a single top-level directory: realmikolajpiech-Omni-<sha>/
    entries = os.listdir(extract_dir)
    source_dir = (
        os.path.join(extract_dir, entries[0])
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0]))
        else extract_dir
    )

    # ── 3. Write apply-and-relaunch shell helper ──────────────────────────────
    _emit(on_progress, 70, "Preparing…")
    install_dir = str(INSTALL_DIR)
    script_path = os.path.join(tmp_dir, "do_update.sh")
    script_body = f"""\
#!/bin/bash
sleep 2
rsync -a --delete \\
    --exclude='.env' \\
    --exclude='data/' \\
    --exclude='logs/' \\
    --exclude='venv/' \\
    --exclude='*.pyc' \\
    --exclude='__pycache__/' \\
    "{source_dir}/" "{install_dir}/"
open -a "/Applications/Omni.app"
rm -rf "{tmp_dir}"
"""
    with open(script_path, "w") as f:
        f.write(script_body)
    os.chmod(script_path, 0o755)

    # ── 4. Launch detached ────────────────────────────────────────────────────
    _emit(on_progress, 90, "Applying update…")
    subprocess.Popen(
        ["bash", script_path],
        close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _emit(on_progress, 100, "Done — relaunching Omni…")
