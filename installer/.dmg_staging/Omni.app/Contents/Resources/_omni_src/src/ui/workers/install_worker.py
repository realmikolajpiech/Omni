import time
import subprocess
import sys
import os
from PyQt6.QtCore import QThread, pyqtSignal
from src.services.system.installer import generate_install_plan, generate_uninstall_plan


class InstallOrchestrator(QThread):
    status_update = pyqtSignal(str)
    log_entry = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    candidates_found = pyqtSignal(list)

    def __init__(self, requested_app_name, forced_package=None):
        super().__init__()
        self.requested_name = requested_app_name
        self.forced_package = forced_package

    def run(self):
        try:
            app_name = self.forced_package['name'] if self.forced_package else self.requested_name
            display_name = app_name.replace('-', ' ').title()

            self.status_update.emit(f"Looking up '{display_name}'...")
            time.sleep(0.2)

            # Generate install plan via local logic (Homebrew on macOS)
            plan = generate_install_plan(app_name)

            if plan["method"] == "failed":
                self.finished.emit(False, plan["description"])
                return

            description = plan.get("description", f"Installing {display_name}")
            commands = plan.get("commands", [])

            self.status_update.emit(description)
            time.sleep(0.3)

            for cmd in commands:
                self.log_entry.emit(f"$ {cmd}")
                self.status_update.emit("Downloading…")

                env = os.environ.copy()
                env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")

                process = subprocess.Popen(
                    cmd, shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env
                )

                already_installed = False
                last_err = ""
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if not line.strip():
                        continue
                    self.log_entry.emit(line.rstrip())
                    lo = line.lower()
                    if "already installed" in lo or "not upgrading" in lo:
                        already_installed = True
                        self.status_update.emit("Already installed ✓")
                    elif "downloading" in lo:
                        self.status_update.emit("Downloading…")
                    elif "installing" in lo or "moving" in lo:
                        self.status_update.emit("Installing…")
                    elif "linking" in lo:
                        self.status_update.emit("Linking…")
                    elif "error" in lo or "failed" in lo:
                        last_err = line.strip()

                rc = process.returncode
                if rc != 0:
                    self.finished.emit(False, last_err or f"Installation failed (exit {rc}).")
                    return
                if already_installed:
                    self.finished.emit(True, f"{display_name} is already installed!")
                    return

            self.finished.emit(True, f"Successfully installed {display_name}!")


        except Exception as e:
            self.finished.emit(False, f"Error: {e}")


class InstallWorker(QThread):
    """Legacy worker kept for backwards compatibility."""
    progress_update = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, app_name):
        super().__init__()
        self.app_name = app_name

    def run(self):
        try:
            self.progress_update.emit(f"Checking packages for '{self.app_name}'...")
            plan = generate_install_plan(self.app_name)
            if plan["method"] == "failed" or not plan.get("commands"):
                self.finished.emit(False, plan.get("description", "Could not find a way to install this app."))
                return
            self.progress_update.emit(plan.get("description", "Installing..."))
            for cmd in plan["commands"]:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if res.returncode != 0:
                    self.finished.emit(False, f"Command failed: {cmd}\n{res.stderr}")
                    return
            self.finished.emit(True, f"Successfully installed {self.app_name}!")
        except Exception as e:
            self.finished.emit(False, f"Installation Error: {str(e)}")


class UninstallOrchestrator(QThread):
    """Drives the uninstallation of an application via Homebrew."""
    status_update = pyqtSignal(str)
    log_entry = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, requested_app_name):
        super().__init__()
        self.requested_name = requested_app_name

    def run(self):
        try:
            app_name = self.requested_name
            display_name = app_name.replace('-', ' ').title()

            self.status_update.emit(f"Looking up '{display_name}'...")
            time.sleep(0.2)

            plan = generate_uninstall_plan(app_name)

            if plan["method"] == "failed":
                self.finished.emit(False, plan["description"])
                return

            if plan["method"] == "not_installed":
                self.finished.emit(False, plan["description"])
                return

            description = plan.get("description", f"Uninstalling {display_name}")
            commands = plan.get("commands", [])

            self.status_update.emit(description)
            time.sleep(0.3)

            for cmd in commands:
                self.log_entry.emit(f"$ {cmd}")
                self.status_update.emit("Removing files…")

                env = os.environ.copy()
                env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")

                process = subprocess.Popen(
                    cmd, shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env
                )

                last_err = ""
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if not line.strip():
                        continue
                    self.log_entry.emit(line.rstrip())
                    lo = line.lower()
                    if "removing" in lo or "deleting" in lo or "pruning" in lo:
                        self.status_update.emit("Removing files…")
                    elif "uninstalling" in lo:
                        self.status_update.emit("Uninstalling…")
                    elif "error" in lo or "failed" in lo:
                        last_err = line.strip()

                rc = process.returncode
                if rc != 0:
                    self.finished.emit(False, last_err or f"Uninstallation failed (exit {rc}).")
                    return

            self.finished.emit(True, f"Successfully uninstalled {display_name}!")

        except Exception as e:
            self.finished.emit(False, f"Error: {e}")

