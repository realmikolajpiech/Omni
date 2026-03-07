"""Auto-update helper for Omni.

Flow:
  check_update(current)  → (tag, zipball_url, changelog) | (None, None, None)
  apply_update(url, cb)  → downloads zip, writes a shell helper that rsync's
                           the new source over INSTALL_DIR and relaunches Omni,
                           then launches that helper detached.
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

GITHUB_API    = "https://api.github.com/repos/realmikolajpiech/Omni/releases/latest"
INSTALL_DIR   = Path.home() / "Library" / "Application Support" / "Omni"
_USER_AGENT   = "Omni-Updater/1.0"

# Fine-grained PAT with read-only access to Contents + Releases.
# Keep this out of source if the repo is ever made public.
GITHUB_TOKEN  = os.environ.get("OMNI_GITHUB_TOKEN", "")


def _auth_headers() -> dict:
    h = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _vtuple(tag: str) -> tuple:
    """Parse 'v1.2.3' or '1.2.3' into (1, 2, 3)."""
    try:
        return tuple(int(x) for x in tag.lstrip("v").split("."))
    except ValueError:
        return (0,)


def check_update(current_version: str):
    """
    Query GitHub releases API.
    Returns (latest_tag, zipball_url, changelog_body) if a newer release exists,
    otherwise (None, None, None).
    Network errors are swallowed and logged at DEBUG level.
    """
    try:
        req = urllib.request.Request(GITHUB_API, headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        tag         = data.get("tag_name", "")
        zipball_url = data.get("zipball_url", "")
        body        = data.get("body", "No release notes available.")

        if tag and zipball_url and _vtuple(tag) > _vtuple(current_version):
            return tag, zipball_url, body

    except Exception as e:
        logging.debug(f"[updater] check_update: {e}")

    return None, None, None


def apply_update(zipball_url: str, on_progress=None):
    """
    Download the GitHub release zip, extract it, write a shell helper that
    rsyncs the new source over INSTALL_DIR and relaunches Omni, then launches
    that helper detached.  The caller must quit the Qt application afterwards.

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
        req = urllib.request.Request(zipball_url, headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=300) as resp:
            total      = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        _prog(int(downloaded / total * 55), "Downloading update…")
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Download failed: {e}")

    # ── 2. Extract ────────────────────────────────────────────────────────────
    _prog(55, "Extracting…")
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

    # ── 3. Write the apply-and-relaunch helper script ────────────────────────
    _prog(70, "Preparing…")
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
"""
    with open(script_path, "w") as f:
        f.write(script_body)
    os.chmod(script_path, 0o755)

    # ── 4. Launch detached ────────────────────────────────────────────────────
    _prog(90, "Applying update…")
    subprocess.Popen(
        ["bash", script_path],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _prog(100, "Done — relaunching Omni…")
