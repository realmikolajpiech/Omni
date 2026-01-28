import os
import subprocess
import logging
import requests

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
                                import re
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
