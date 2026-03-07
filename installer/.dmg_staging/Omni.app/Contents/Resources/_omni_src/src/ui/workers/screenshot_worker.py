import logging
import os
import shutil
import subprocess
import base64
import sys
import threading
import time
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import QBuffer, QIODevice

class ScreenshotWorker(QThread):
    finished = pyqtSignal(str)
    failed = pyqtSignal()

    def run(self):
        try:
            # Set a timeout for the entire screenshot operation (5 seconds)
            screenshot_thread = threading.Thread(target=self._take_screenshot)
            screenshot_thread.daemon = True
            screenshot_thread.start()
            
            # Wait for thread with timeout
            screenshot_thread.join(timeout=5.0)
            
            if screenshot_thread.is_alive():
                logging.error("Screenshot operation timed out after 5 seconds")
                self.failed.emit()
                return
                
        except Exception as e:
            logging.error(f"Screenshot Worker Failed: {e}")
            import traceback
            logging.error(traceback.format_exc())
            self.failed.emit()

    def _take_screenshot(self):
        """Internal method that performs the actual screenshot."""
        try:
            # Windows/Mac: Use PyQt6 with timeout wrapper
            if sys.platform in ["win32", "darwin"]:
                try:
                    screen = QGuiApplication.primaryScreen()
                    if screen:
                        # Use grabWindow with explicit parameters
                        pixmap = screen.grabWindow(0)
                        if not pixmap.isNull():
                            ba = QBuffer()
                            ba.open(QIODevice.OpenModeFlag.WriteOnly)
                            pixmap.save(ba, "PNG")
                            b64_str = base64.b64encode(ba.data().data()).decode('utf-8')
                            self.finished.emit(b64_str)
                            return
                except Exception as e:
                    logging.warning(f"PyQt6 screenshot failed: {e}. Trying alternatives...")
            
            # Linux Fallbacks
            has_spectacle = shutil.which("spectacle")
            
            # Try Spectacle First
            if has_spectacle:
                shot_path = "/tmp/omni_screenshot.png"
                if os.path.exists(shot_path): os.remove(shot_path)
                try:
                    subprocess.run(["spectacle", "-m", "-b", "-n", "-o", shot_path], timeout=4)
                    
                    if os.path.exists(shot_path):
                         with open(shot_path, "rb") as f:
                             b64_str = base64.b64encode(f.read()).decode('utf-8')
                             self.finished.emit(b64_str)
                             return
                except subprocess.TimeoutExpired:
                    logging.warning("Spectacle screenshot timed out")
                except Exception as e:
                    logging.warning(f"Spectacle failed: {e}")

            # Fallback: gnome-screenshot
            has_gnome = shutil.which("gnome-screenshot")
            if has_gnome:
                shot_path = "/tmp/omni_screenshot.png"
                if os.path.exists(shot_path): os.remove(shot_path)
                try:
                    subprocess.run(["gnome-screenshot", "-f", shot_path], timeout=4)
                    
                    if os.path.exists(shot_path):
                         with open(shot_path, "rb") as f:
                             b64_str = base64.b64encode(f.read()).decode('utf-8')
                             self.finished.emit(b64_str)
                             return
                except subprocess.TimeoutExpired:
                    logging.warning("gnome-screenshot timed out")
                except Exception as e:
                    logging.warning(f"gnome-screenshot failed: {e}")
            
            # If we get here, everything failed
            logging.error("All screenshot methods failed.")
            self.failed.emit()
        except Exception as e:
             logging.error(f"Screenshot Worker Failed: {e}")
             import traceback
             logging.error(traceback.format_exc())
             self.failed.emit()
