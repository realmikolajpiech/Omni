import sys
import os
import logging
import signal

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtWidgets import QApplication
from src.core.logger import setup_logging, setup_exception_hook
from src.ui.window import OmniWindow

def main():
    # Platform specific fixes
    if sys.platform == "linux":
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        os.environ["QT_STYLE_OVERRIDE"] = "kvantum"
    
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    setup_logging("omni_ui")
    setup_exception_hook()

    app = QApplication(sys.argv)
    
    # Allow Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = OmniWindow()
    # Window starts hidden/animates in if IPC triggers it, or we show it initially?
    # Original main.py called `window.animate_entry()` at end of `__init__` if I recall correctly.
    # Let's check window.py logic.
    # Yes, `__init__` calls `self.animate_entry()`.
    
    # We should show it.
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
