import subprocess
import logging
import os
import sys
from datetime import datetime

def log_debug(msg):
    try:
        with open("/tmp/omni_install.log", "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"LOG DEBUG FAILED: {e}", file=sys.stderr)

def generate_install_plan(app_name):
    logging.info(f"Generating Install Plan for: {app_name}")

    # 1. APT CHECK
    try:
        cmd = ["apt-cache", "search", "--names-only", f"^{app_name}$"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            pkg_name = res.stdout.strip().split()[0]
            return {
                "method": "apt",
                "description": f"Found '{pkg_name}' in system repositories",
                "commands": [f"pkexec apt-get install -y {pkg_name}"]
            }
    except Exception as e:
        logging.error(f"Apt check failed: {e}")

    # 2. FLATPAK CHECK
    try:
        cmd = ["flatpak", "search", app_name]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().split('\n')
            if lines:
                parts = lines[0].split('\t')
                app_id = parts[2].strip() if len(parts) > 2 else next((p for p in lines[0].split() if '.' in p), None)

                if app_id:
                    return {
                        "method": "flatpak",
                        "description": f"Found '{app_id}' in Flatpak",
                        "commands": [f"flatpak install -y {app_id}"]
                    }
    except Exception as e:
        logging.error(f"Flatpak check failed: {e}")

    return {"method": "failed", "description": "Could not find package.", "commands": []}
