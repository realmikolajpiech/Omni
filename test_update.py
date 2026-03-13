#!/usr/bin/env python3
"""
Quick test for the update dialog + apply flow.
Run from project root:  python test_update.py

What it does:
  1. Fetches the latest release info from the Worker.
  2. Opens the UpdateDialog directly (skips the version comparison).
  3. Clicking "Update Now" runs the real apply_update → DMG download,
     mount, rsync to /Applications/Omni.app, unmount, relaunch.

Pass --dry-run to skip the rsync/relaunch step and just download+mount.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Minimal Qt bootstrap ─────────────────────────────────────────────────────
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(True)

# ── Fetch release info ────────────────────────────────────────────────────────
import json, urllib.request
from src.core.config import BACKEND_URL, OMNI_SECRET, APP_VERSION

print(f"Current APP_VERSION: {APP_VERSION}")
print("Fetching latest release from Worker…")

req = urllib.request.Request(
    f"{BACKEND_URL}/v1/release/latest",
    headers={"User-Agent": "Omni-Updater/1.0", "X-Omni-Secret": OMNI_SECRET},
)
with urllib.request.urlopen(req, timeout=8) as r:
    data = json.loads(r.read())

tag     = data.get("tag_name", "")
url     = data.get("dmg_url") or data.get("zipball_url", "")
body    = data.get("body", "No release notes.")

print(f"  tag:     {tag}")
print(f"  url:     {url}")
print(f"  body:    {body[:80]}…" if len(body) > 80 else f"  body:    {body}")

if "--dry-run" in sys.argv:
    # Monkey-patch apply_update to stop before rsync/relaunch
    import src.core.updater as _upd
    _real = _upd._apply_dmg
    def _dry_dmg(url, on_progress=None):
        import tempfile, os, subprocess, shutil
        tmp = tempfile.mkdtemp(prefix="omni_dryrun_")
        dmg = os.path.join(tmp, "omni_update.dmg")
        mp  = os.path.join(tmp, "mount")
        os.makedirs(mp)
        _upd._emit(on_progress, 0, "[DRY RUN] Downloading…")
        _upd._download(url, dmg, on_progress, 0, 55)
        _upd._emit(on_progress, 55, "[DRY RUN] Mounting…")
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-quiet",
                        "-mountpoint", mp, dmg], check=True, capture_output=True)
        import glob as _g
        apps = _g.glob(os.path.join(mp, "*.app"))
        _upd._emit(on_progress, 60, f"[DRY RUN] Found: {os.path.basename(apps[0]) if apps else 'none'}")
        _upd._emit(on_progress, 80, "[DRY RUN] Skipping rsync (dry run)")
        subprocess.run(["hdiutil", "detach", mp, "-quiet"], check=False, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        _upd._emit(on_progress, 100, "[DRY RUN] Done — would relaunch here")
    _upd._apply_dmg = _dry_dmg
    print("\n[DRY RUN] rsync and relaunch are disabled.\n")

# ── Show the dialog ───────────────────────────────────────────────────────────
from src.ui.update_dialog import UpdateDialog

dlg = UpdateDialog(APP_VERSION, tag or "v?.?.?", url, body)

def on_done(result):
    if dlg._accepted_update:
        print("Update applied — Qt will quit and app should relaunch.")
    else:
        print("Dialog dismissed.")
    app.quit()

dlg.finished.connect(on_done)
dlg.show()

sys.exit(app.exec())
