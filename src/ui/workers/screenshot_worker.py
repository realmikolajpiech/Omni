import logging
import os
import shutil
import subprocess
import base64
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import QBuffer, QIODevice

class ScreenshotWorker(QThread):
    finished = pyqtSignal(str)
    failed = pyqtSignal()

    def run(self):
        try:
            # Platform-independent PyQt6 Screenshot (Works on Windows/X11, maybe not Wayland)
            screen = QGuiApplication.primaryScreen()
            if screen:
                pixmap = screen.grabWindow(0)
                if not pixmap.isNull():
                    ba = QBuffer()
                    ba.open(QIODevice.OpenModeFlag.WriteOnly)
                    pixmap.save(ba, "PNG")
                    b64_str = base64.b64encode(ba.data().data()).decode('utf-8')
                    self.finished.emit(b64_str)
                    return

            # Linux Fallbacks
            has_spectacle = shutil.which("spectacle")
            
            # Try Spectacle First
            if has_spectacle:
                shot_path = "/tmp/omni_screenshot.png"
                if os.path.exists(shot_path): os.remove(shot_path)
                subprocess.run(["spectacle", "-m", "-b", "-n", "-o", shot_path], timeout=10)
                
                if os.path.exists(shot_path):
                     with open(shot_path, "rb") as f:
                         b64_str = base64.b64encode(f.read()).decode('utf-8')
                         self.finished.emit(b64_str)
                         return

            # Fallback: gnome-screenshot
            has_gnome = shutil.which("gnome-screenshot")
            if has_gnome:
                shot_path = "/tmp/omni_screenshot.png"
                if os.path.exists(shot_path): os.remove(shot_path)
                # -f filename
                subprocess.run(["gnome-screenshot", "-f", shot_path], timeout=10)
                
                if os.path.exists(shot_path):
                     with open(shot_path, "rb") as f:
                         b64_str = base64.b64encode(f.read()).decode('utf-8')
                         self.finished.emit(b64_str)
                         return
            
            # If we get here, everything failed
            logging.error("All screenshot methods failed.")
            self.failed.emit()
        except Exception as e:
             logging.error(f"Screenshot Worker Failed: {e}")
             import traceback
             logging.error(traceback.format_exc())
             self.failed.emit()
