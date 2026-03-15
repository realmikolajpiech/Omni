import os
import sys
import subprocess
import logging
import requests
import plistlib
import re

APP_CACHE = None

def get_app_cache():
    global APP_CACHE
    if APP_CACHE is not None: return APP_CACHE
    
    apps = {}
    
    if os.name == 'nt':
        # Windows App Discovery
        dirs = [
            os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), r'Microsoft\Windows\Start Menu\Programs'),
            os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs')
        ]
        
        logging.info("Building App Cache (Windows)...")
        
        # Init shell for shortcut resolution
        shell = None
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
        except: pass
        
        for d in dirs:
            if not os.path.exists(d): continue
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        full_path = os.path.join(root, f)
                        name = f[:-4] # Remove .lnk
                        clean_name = name.lower()
                        
                        # Resolve icon from target to avoid shortcut arrow overlay
                        icon_path = full_path
                        if shell:
                            try:
                                shortcut = shell.CreateShortcut(full_path)
                                # Prefer explicit icon location
                                if shortcut.IconLocation and "," in shortcut.IconLocation:
                                    icon_path = shortcut.IconLocation.split(",")[0]
                                elif shortcut.TargetPath:
                                    icon_path = shortcut.TargetPath
                                    
                                # Expand env vars if needed (e.g. %SystemRoot%)
                                if "%" in icon_path:
                                    icon_path = os.path.expandvars(icon_path)
                                    
                                if not os.path.exists(icon_path):
                                    icon_path = full_path
                            except: pass

                        # Quote path to handle spaces
                        apps[clean_name] = {"cmd": f'"{full_path}"', "orig_name": name, "icon": icon_path}
    elif sys.platform == 'darwin':
        # macOS App Discovery
        dirs = [
            "/Applications",
            "/System/Applications",
            "/System/Applications/Utilities",
            os.path.expanduser("~/Applications")
        ]
        
        logging.info("Building App Cache (macOS)...")
        for d in dirs:
            if not os.path.exists(d): continue
            try:
                for f in os.listdir(d):
                    full_path = os.path.join(d, f)
                    if f.endswith(".app"):
                        name = f[:-4]
                        clean_name = name.lower()
                        apps[clean_name] = {"cmd": f'open "{full_path}"', "orig_name": name, "icon": full_path}
                    elif os.path.isdir(full_path) and not f.startswith('.'):
                        # Scan one level deeper for apps in subdirectories (e.g. DaVinci Resolve)
                        try:
                            for sub_f in os.listdir(full_path):
                                if sub_f.endswith(".app"):
                                    sub_name = sub_f[:-4]
                                    sub_clean = sub_name.lower()
                                    sub_full = os.path.join(full_path, sub_f)
                                    apps[sub_clean] = {"cmd": f'open "{sub_full}"', "orig_name": sub_name, "icon": sub_full}
                        except Exception:
                            pass
            except Exception as e:
                logging.error(f"Error scanning {d}: {e}")

        system_settings_app = "/System/Applications/System Settings.app"
        system_settings_icon = system_settings_app if os.path.exists(system_settings_app) else None

        # 1. Build a map of BundleID -> IconPath by scanning system extensions
        bundle_id_to_icon = {}
        
        def _scan_extensions_for_icons(directory):
            if not os.path.isdir(directory): return
            try:
                for entry in os.listdir(directory):
                    if entry.endswith(".appex"):
                        appex_path = os.path.join(directory, entry)
                        # Read Info.plist for Bundle ID
                        info_plist = os.path.join(appex_path, "Contents", "Info.plist")
                        if not os.path.exists(info_plist): continue
                        
                        try:
                            pl = _load_plist(info_plist)
                            if not pl: continue
                            bid = pl.get("CFBundleIdentifier")
                            if not bid: continue
                            
                            # Check for .icns in Resources
                            res_dir = os.path.join(appex_path, "Contents", "Resources")
                            if os.path.isdir(res_dir):
                                for f in os.listdir(res_dir):
                                    if f.endswith(".icns"):
                                        # Use the first .icns found
                                        bundle_id_to_icon[bid] = os.path.join(res_dir, f)
                                        break
                        except: pass
            except: pass

        _scan_extensions_for_icons("/System/Applications/System Settings.app/Contents/PlugIns")
        _scan_extensions_for_icons("/System/Library/ExtensionKit/Extensions")

        def _normalize_setting_key(s: str) -> str:
            s = (s or "").strip().lower()
            s = s.replace("&", " and ")
            s = re.sub(r"[^a-z0-9\s]+", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def _add_setting_entry(key: str, title: str, bundle_id: str):
            if not key:
                return
            
            # Use specific icon if found, else generic System Settings icon
            icon_path = bundle_id_to_icon.get(bundle_id) or system_settings_icon or system_settings_app
            
            url = f"x-apple.systempreferences:{bundle_id}"
            apps[key] = {
                "cmd": f'open "{url}"',
                "orig_name": f"Settings: {title}",
                "icon": icon_path
            }

        def _add_setting_aliases(title: str, bundle_id: str):
            raw = (title or "").strip()
            if not raw:
                return
            raw_key = raw.lower()
            norm = _normalize_setting_key(raw)

            for k in {raw_key, norm}:
                if k:
                    _add_setting_entry(k, raw, bundle_id)
                    _add_setting_entry(f"settings {k}", raw, bundle_id)
                    _add_setting_entry(f"system settings {k}", raw, bundle_id)
                    _add_setting_entry(f"preferences {k}", raw, bundle_id)

        def _load_plist(path: str):
            try:
                with open(path, "rb") as f:
                    return plistlib.load(f)
            except Exception:
                return None

        def _humanize_settings_title(bundle_id: str) -> str:
            s = (bundle_id or "").strip()
            if not s:
                return "System Settings"

            # Some identifiers end with a standalone ".extension" segment.
            # Strip it before selecting the final component.
            s = re.sub(r"\.extension$", "", s, flags=re.IGNORECASE)

            part = s.split(".")[-1]

            part = re.sub(r"-settings-extension$", "", part, flags=re.IGNORECASE)
            part = re.sub(r"-settings\.extension$", "", part, flags=re.IGNORECASE)
            part = re.sub(r"settings\.extension$", "", part, flags=re.IGNORECASE)
            part = re.sub(r"settings-extension$", "", part, flags=re.IGNORECASE)
            part = re.sub(r"SettingsExtension$", "", part)
            part = re.sub(r"SettingsUIExtension$", "", part)
            part = re.sub(r"Settings$", "", part)
            part = part.replace("_", " ").replace("-", " ")

            part = re.sub(r"([a-z])([A-Z])", r"\1 \2", part)
            part = re.sub(r"\s+", " ", part).strip()

            low = part.lower()
            if low in {"wifi", "wi fi"}:
                return "Wi‑Fi"
            if low in {"apple id", "appleid"}:
                return "Apple ID"
            if low == "touch id":
                return "Touch ID & Password"
            if low == "privacy security":
                return "Privacy & Security"
            if low == "users groups":
                return "Users & Groups"
            if low == "date time":
                return "Date & Time"
            if low == "internet accounts":
                return "Internet Accounts"
            if low == "print scan":
                return "Printers & Scanners"
            if low == "wallet":
                return "Wallet & Apple Pay"
            if low == "desktop":
                return "Desktop & Dock"
            if low == "siri":
                return "Apple Intelligence & Siri"
            if low == "control center":
                return "Control Center"  # Will also alias "Menu Bar" below
            if low == "cd dvd":
                return "CDs & DVDs"
            if low == "game controller":
                return "Game Controllers"

            return part.title()

        def _discover_settings_bundle_ids_from_sidebar():
            ids = []
            sidebar_plist = os.path.join(system_settings_app, "Contents", "Resources", "Sidebar.plist")
            pl = _load_plist(sidebar_plist)
            if not pl:
                return ids

            def walk(x):
                if isinstance(x, dict):
                    for v in x.values():
                        walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)
                elif isinstance(x, str):
                    s = x.strip()
                    if s.startswith("com.apple.") and "." in s:
                        ids.append(s)

            walk(pl)
            seen = set()
            out = []
            for bid in ids:
                if bid in seen:
                    continue
                seen.add(bid)
                out.append(bid)
            return out

        sidebar_ids = _discover_settings_bundle_ids_from_sidebar()
        if sidebar_ids:
            logging.info(f"Discovered {len(sidebar_ids)} System Settings deeplinks via Sidebar.plist")
            for bundle_id in sidebar_ids:
                title = _humanize_settings_title(bundle_id)
                _add_setting_aliases(title, bundle_id)
                
                # Extra Aliases for specific items
                if title == "Control Center":
                    _add_setting_aliases("Menu Bar", bundle_id)
                elif title == "Desktop & Dock":
                    _add_setting_aliases("Stage Manager", bundle_id)
        else:
            fallback = {
                "Wi‑Fi": "com.apple.wifi-settings-extension",
                "Bluetooth": "com.apple.BluetoothSettings",
                "Network": "com.apple.Network-Settings.extension",
                "Sound": "com.apple.Sound-Settings.extension",
                "Displays": "com.apple.Displays-Settings.extension",
                "Wallpaper": "com.apple.Wallpaper-Settings.extension",
                "Appearance": "com.apple.Appearance-Settings.extension",
                "Accessibility": "com.apple.Accessibility-Settings.extension",
                "Privacy & Security": "com.apple.settings.PrivacySecurity.extension",
                "Software Update": "com.apple.Software-Update-Settings.extension",
                "Users & Groups": "com.apple.Users-Groups-Settings.extension",
                "Battery": "com.apple.Battery-Settings.extension",
                "Mouse": "com.apple.Mouse-Settings.extension",
                "Trackpad": "com.apple.Trackpad-Settings.extension",
                "Keyboard": "com.apple.Keyboard-Settings.extension",
                "Date & Time": "com.apple.Date-Time-Settings.extension",
                "Notifications": "com.apple.Notifications-Settings.extension",
                "Focus": "com.apple.Focus-Settings.extension",
            }
            for title, bid in fallback.items():
                _add_setting_aliases(title, bid)

    else:
        # Common locations for .desktop files
        dirs = [
            "/usr/share/applications", 
            os.path.expanduser("~/.local/share/applications"),
            "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
            "/snap/gui"
        ]
        
        logging.info("Building App Cache...")
        for d in dirs:
            if not os.path.exists(d): continue
            for f in os.listdir(d):
                if f.endswith(".desktop"):
                    try:
                        path = os.path.join(d, f)
                        with open(path, 'r', errors='ignore') as file:
                            content = file.read()
                            
                            name = None
                            exec_cmd = None
                            icon_name = None
                            
                            # Basic INI parsing
                            for line in content.splitlines():
                                line = line.strip()
                                if line.startswith("Name=") and not name:
                                    name = line.split("=", 1)[1].strip()
                                if line.startswith("Exec=") and not exec_cmd:
                                    exec_cmd = line.split("=", 1)[1].strip()
                                if line.startswith("Icon=") and not icon_name:
                                    icon_name = line.split("=", 1)[1].strip()
                            
                            if name and exec_cmd:
                                # Clean Exec command
                                # Remove field codes like %u, %F, etc.
                                exec_cmd = re.sub(r'%[fFuUikc]', '', exec_cmd).strip()
                                
                                # Clean Name (lower case for searching)
                                clean_name = name.lower()
                                
                                # Store by name
                                apps[clean_name] = {"cmd": exec_cmd, "orig_name": name, "icon": icon_name}
                                
                                # Also store by filename for robust matching (e.g. 'code.desktop' -> 'code')
                                file_key = f.replace(".desktop", "").lower()
                                if file_key not in apps:
                                    apps[file_key] = {"cmd": exec_cmd, "orig_name": name, "icon": icon_name}
                                    
                    except: pass
    
    APP_CACHE = apps
    logging.info(f"App Cache Built. Found {len(apps)} apps.")
    return apps

def find_and_launch_app(query):
    apps = get_app_cache()
    query = query.strip().lower()
    
    best_match = None
    best_name = None
    
    # 1. Exact Match
    if query in apps:
        best_match = apps[query]['cmd']
        best_name = apps[query]['orig_name']
    else:
        # 2. Partial Match
        # Search for "starts with"
        for name, data in apps.items():
             if name.startswith(query): 
                 best_match = data['cmd']; best_name = data['orig_name']; break
        
        if not best_match:
             # Search for "contains" (beware false positives, ensure query is long enough)
             if len(query) >= 3:
                 for name, data in apps.items():
                     if query in name:
                         best_match = data['cmd']; best_name = data['orig_name']; break
    
    if best_match:
        logging.info(f"Launching App: {best_name} (Cmd: {best_match})")
        try:
            # Use specific env vars or just shell=True
            kwargs = {}
            if os.name == 'posix':
                kwargs['start_new_session'] = True
            subprocess.Popen(best_match, shell=True, **kwargs)
            return True, best_name
        except Exception as e:
            logging.error(f"Failed to launch app: {e}")
            return False, f"Error: {e}"
            
    # Fallback: Check if it's a known system executable (e.g. 'calc', 'notepad')
    # This helps when the shortcut name is different from the executable name
    if os.name == 'nt':
        import shutil
        if shutil.which(query):
             try:
                 subprocess.Popen(query, shell=True)
                 return True, query
             except: pass
            
    return False, "App not found"

def resolve_app_metadata(app_name):
    try:
        url = "https://html.duckduckgo.com/html/"
        params = {"q": f"{app_name} official website"}
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.post(url, data=params, headers=headers, timeout=5)

        if resp.status_code == 200:
            import re
            match = re.search(r'class="result__a" href="([^"]+)"', resp.text)
            if match:
                return {
                    "image": None,
                    "website": match.group(1)
                }
    except: pass
    return None
