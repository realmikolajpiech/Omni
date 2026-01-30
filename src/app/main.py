import sys
import os
import logging
import signal
import keyboard

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
    
    # Global Hotkey Setup
    try:
        def toggle_omni():
            # Emit signal to handle UI update on main thread
            window.toggle_requested.emit()

        # Bind to 'left windows' key. 
        # User requested to suppress default behavior (Start Menu)
        # using on_press_key is often more reliable for single keys, especially modifiers like Win
        keyboard.on_press_key('left windows', lambda _: toggle_omni(), suppress=True)
        keyboard.on_press_key('right windows', lambda _: toggle_omni(), suppress=True)
        
        # Add backup hotkey for testing
        keyboard.add_hotkey('ctrl+space', toggle_omni, suppress=True)
        logging.info("Global hotkey 'left/right windows' registered (suppressed) via on_press_key. Added 'ctrl+space' as backup.")
    except Exception as e:
        logging.error(f"Failed to register global hotkey: {e}")

    # Check for Admin privileges on Windows
    if sys.platform == "win32":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                logging.warning("App is NOT running as Administrator. 'Windows' key suppression might fail.")
        except: pass

    # Window starts hidden/animates in if IPC triggers it, or we show it initially?
    # Original main.py called `window.animate_entry()` at end of `__init__` if I recall correctly.
    # Let's check window.py logic.
    # Yes, `__init__` calls `self.animate_entry()`.
    
    # We should show it.
    # window.show()
    # BUT, if we are starting in background mode (via autostart), we might NOT want to show it immediately.
    # However, since run.py handles the mode, if we are here, we are in "ui" mode.
    # Does "ui" mode mean "show window" or "start ui process"?
    # If the user runs "Omni.exe ui" from desktop shortcut, they expect to SEE it.
    # If autostart runs "Omni.exe ui", we might want it hidden.
    
    # Current behavior of `OmniWindow` is that it hides itself on init? 
    # Actually `OmniWindow.__init__` calls `self.animate_entry()` IF `window.show()` is called?
    # No, `animate_entry` is called inside `toggle_visibility_safe` or manually.
    # `__init__` calls `self.animate_entry()` at line 180.
    
    # If we want to start hidden, we should remove `self.animate_entry()` from `__init__` 
    # OR we just don't call `window.show()`.
    # PyQt widgets are hidden by default.
    # But `OmniWindow.__init__` calls `self.animate_entry()` which does:
    # `self.move(...)`
    # `self.input_field.setFocus()`
    # `self.activateWindow()`
    # `self.raise_()`
    
    # So `__init__` DOES try to show/activate it.
    # We should fix `__init__` to NOT do that automatically, or control it.
    
    # Let's Modify `OmniWindow` to NOT animate entry on init.
    pass

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
