import requests
import time
import subprocess
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import INSTALL_URL, FIND_PACKAGE_URL, VERIFY_PACKAGE_URL, PICK_PACKAGE_URL

class InstallOrchestrator(QThread):
    status_update = pyqtSignal(str)
    log_entry = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    candidates_found = pyqtSignal(list) # New signal

    def __init__(self, requested_app_name, forced_package=None, fake_mode=True):
        super().__init__()
        self.requested_name = requested_app_name
        self.forced_package = forced_package # If set, skip search and install this immediate package
        self.fake_mode = fake_mode
        self.FIND_URL = FIND_PACKAGE_URL
        self.VERIFY_URL = VERIFY_PACKAGE_URL
        self.INSTALL_URL = INSTALL_URL
        self.PICK_URL = PICK_PACKAGE_URL

    def run(self):
        try:
            confirmed_pkg = self.forced_package
            
            if not confirmed_pkg and self.fake_mode:
                # Fake Search Bypass
                self.status_update.emit(f"Searching for '{self.requested_name}'...")
                time.sleep(0.1) # Instant search
                confirmed_pkg = {
                    "name": "google-chrome-stable" if "chrome" in self.requested_name.lower() else "fake-package",
                    "display_name": self.requested_name.replace('-', ' ').title(),
                    "description": "Simulated package for UI testing"
                }

            if not confirmed_pkg:
                # 1. SEARCH PHASE
                self.status_update.emit(f"Searching for '{self.requested_name}'...")
                r = requests.post(self.FIND_URL, json={"query": self.requested_name}, timeout=10)
                if r.status_code != 200:
                    self.finished.emit(False, "Search failed.")
                    return
                
                data = r.json()
                candidates = data.get("candidates", [])
                
                if not candidates:
                     self.finished.emit(False, f"No packages found for '{self.requested_name}'.")
                     return

                self.status_update.emit("Verifying matches...")
                r_pick = requests.post(
                    self.PICK_URL,
                    json={"app_name": self.requested_name, "candidates": candidates},
                    timeout=15
                )
                
                # If pick_package explicitly returns specific selection, use it.
                # But if it returns "ambiguous", we should ask user.
                if len(candidates) > 1:
                     # Filter for exact match
                     exact = [c for c in candidates if c['name'].lower() == self.requested_name.lower()]
                     if len(exact) == 1:
                         confirmed_pkg = exact[0]
                     else:
                         # Ambiguous: Ask User
                         self.candidates_found.emit(candidates)
                         return # Thread finishes, UI takes over
                else:
                     confirmed_pkg = candidates[0]

            # 2. INSTALL PHASE
            if not confirmed_pkg:
                 self.finished.emit(False, "No package selected.")
                 return

            pkg_display = confirmed_pkg.get('display_name', confirmed_pkg['name'])
            self.status_update.emit(f"Installing {pkg_display}...")
            time.sleep(0.5) 

            # 3. GET INSTALL COMMANDS
            r_plan = requests.post(self.INSTALL_URL, json={"app_name": confirmed_pkg['name']}, timeout=10)
            if r_plan.status_code != 200:
                 self.finished.emit(False, "Failed to get install plan.")
                 return
            
            plan = r_plan.json()
            commands = plan.get("commands", [])
            if not commands:
                 self.finished.emit(False, "No install commands available.")
                 return

            # 4. EXECUTE
            if self.fake_mode:
                # Simulation
                steps = [
                    f"Reading package lists...",
                    f"Building dependency tree...",
                    f"Reading state information...",
                    f"The following NEW packages will be installed: {confirmed_pkg['name']}",
                    f"0 upgraded, 1 newly installed, 0 to remove and 12 not upgraded.",
                    f"Need to get 102 MB of archives.",
                    f"After this operation, 350 MB of additional disk space will be used.",
                    f"Get:1 http://archive.ubuntu.com/ubuntu jammy/main amd64 {confirmed_pkg['name']} [102 MB]",
                    f"Fetched 102 MB in 1s (85.2 MB/s)",
                    f"Selecting previously unselected package {confirmed_pkg['name']}.",
                    f"Preparing to unpack .../{confirmed_pkg['name']}.deb ...",
                    f"Unpacking {confirmed_pkg['name']} ...",
                    f"Setting up {confirmed_pkg['name']} ...",
                    f"Processing triggers for man-db ...",
                    f"Processing triggers for desktop-file-utils ..."
                ]
                
                for step in steps:
                    self.log_entry.emit(step)
                    time.sleep(0.05) # Very fast updates (approx 1s total)
                
                self.finished.emit(True, f"Successfully installed {pkg_display}!")
                return

            for cmd in commands:
                self.log_entry.emit(f"$ {cmd}")
                
                # Use Popen to capture realtime?
                # subprocess.run is blocking but fine for this thread.
                # Merging stderr/stdout
                process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                
                # Stream output
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        self.log_entry.emit(line.strip())
                
                if process.returncode != 0:
                     self.finished.emit(False, f"Installation failed ({process.returncode}).")
                     return

            # Log check?
            # self.log_entry.emit("Installation Complete.")
            self.finished.emit(True, f"Successfully installed {pkg_display}!")

        except Exception as e:
            self.finished.emit(False, f"Error: {e}")

class InstallWorker(QThread):
    progress_update = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    def __init__(self, app_name):
        super().__init__()
        self.app_name = app_name
    def run(self):
        try:
            self.progress_update.emit(f"Checking Packages for '{self.app_name}'...")
            r = requests.post(INSTALL_URL, json={"app_name": self.app_name}, timeout=30)
            if r.status_code != 200:
                self.finished.emit(False, "Brain connection failed.")
                return
            plan = r.json()
            method = plan.get("method")
            desc = plan.get("description", "Installing...")
            commands = plan.get("commands", [])
            if method == "failed" or not commands:
                self.finished.emit(False, "Could not find a way to install this app.")
                return
            self.progress_update.emit(f"{desc}...")
            for cmd in commands:
                self.progress_update.emit("Installing (check popup)...")
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if res.returncode != 0:
                     self.finished.emit(False, f"Command failed: {cmd}\n{res.stderr}")
                     return
            self.finished.emit(True, f"Successfully installed {self.app_name}!")
        except Exception as e:
            self.finished.emit(False, f"Installation Error: {str(e)}")
