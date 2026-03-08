#!/usr/bin/env python3
"""Write a .DS_Store file onto a mounted DMG volume.

Replaces AppleScript-based styling, which is unreliable on modern macOS.
Requires: ds_store, mac_alias (pip install ds_store).
"""
import sys
import plistlib
from pathlib import Path

from ds_store import DSStore
from mac_alias import Alias


def main():
    mount_dir = sys.argv[1]
    win_w = int(sys.argv[2]) if len(sys.argv) > 2 else 660
    win_h = int(sys.argv[3]) if len(sys.argv) > 3 else 420
    icon_size = int(sys.argv[4]) if len(sys.argv) > 4 else 128
    text_size = int(sys.argv[5]) if len(sys.argv) > 5 else 13
    app_x = int(sys.argv[6]) if len(sys.argv) > 6 else 178
    app_y = int(sys.argv[7]) if len(sys.argv) > 7 else 192
    apps_x = int(sys.argv[8]) if len(sys.argv) > 8 else 482
    apps_y = int(sys.argv[9]) if len(sys.argv) > 9 else 192

    bg_file = Path(mount_dir) / ".background" / "background.png"
    ds_path = Path(mount_dir) / ".DS_Store"

    # Create alias pointing to background image on the mounted volume
    alias = Alias.for_file(str(bg_file))

    # Icon view properties — binary plist that Finder reads from .DS_Store
    icvp = plistlib.dumps({
        "backgroundColorBlue": 1.0,
        "backgroundColorGreen": 1.0,
        "backgroundColorRed": 1.0,
        "backgroundImageAlias": alias.to_bytes(),
        "backgroundType": 2,  # 2 = picture
        "gridOffsetX": 0.0,
        "gridOffsetY": 0.0,
        "gridSpacing": 100.0,
        "iconSize": float(icon_size),
        "labelOnBottom": True,
        "showIconPreview": True,
        "showItemInfo": False,
        "textSize": float(text_size),
        "viewOptionsVersion": 1,
    }, fmt=plistlib.FMT_BINARY)

    # Browser window settings — binary plist
    bwsp = plistlib.dumps({
        "ShowPathbar": False,
        "ShowSidebar": False,
        "ShowStatusBar": False,
        "ShowTabView": False,
        "ShowToolbar": False,
        "SidebarWidth": 0,
        "WindowBounds": "{{200, 120}, {%d, %d}}" % (win_w, win_h),
    }, fmt=plistlib.FMT_BINARY)

    with DSStore.open(str(ds_path), "w+") as d:
        d["."]["bwsp"] = bwsp
        d["."]["icvp"] = icvp
        d["."]["vSrn"] = ("long", 1)
        d["Omni.app"]["Iloc"] = (app_x, app_y)
        d["Applications"]["Iloc"] = (apps_x, apps_y)
        d[".background"]["Iloc"] = (999, 999)

    print(f"[DS_Store] Written to {ds_path}")


if __name__ == "__main__":
    main()
