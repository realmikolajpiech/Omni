import logging
import sys
import subprocess
import time
from PyQt6.QtCore import QThread, pyqtSignal


class ComputerControlWorker(QThread):
    """Executes computer control actions (click, type, scroll, key) in a background thread."""

    action_done = pyqtSignal(str)   # emitted after each individual action
    all_done = pyqtSignal()          # emitted when all actions are complete
    error = pyqtSignal(str)          # emitted on error

    def __init__(self, actions: list, delay_before_start: float = 0.0):
        super().__init__()
        self.actions = actions
        self.delay_before_start = delay_before_start

    def run(self):
        if self.delay_before_start > 0:
            time.sleep(self.delay_before_start)

        for act in self.actions:
            try:
                action_type = act.get("action", "")
                if action_type == "click":
                    coords = act.get("coordinate", [0, 0])
                    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                        self._click(int(coords[0]), int(coords[1]))
                elif action_type == "type":
                    self._type_text(act.get("text", ""))
                elif action_type == "scroll":
                    self._scroll(act.get("direction", "down"), int(act.get("amount", 3)))
                elif action_type == "key":
                    self._press_key(act.get("key", ""))
                else:
                    logging.warning(f"[CC] Unknown action type: {action_type}")

                desc = act.get("description", f"Executed {action_type}")
                self.action_done.emit(desc)
                time.sleep(0.15)

            except Exception as e:
                logging.error(f"[CC] Action failed ({act}): {e}")
                self.error.emit(str(e))

        self.all_done.emit()

    # ------------------------------------------------------------------ #
    # Platform-specific implementations                                   #
    # ------------------------------------------------------------------ #

    def _click(self, x: int, y: int):
        logging.info(f"[CC] Click at ({x}, {y})")
        if sys.platform == "darwin":
            try:
                import Quartz
                point = Quartz.CGPoint(x, y)
                ev_down = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventLeftMouseDown, point, Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev_down)
                time.sleep(0.07)
                ev_up = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventLeftMouseUp, point, Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev_up)
            except Exception as e:
                logging.warning(f"[CC] Quartz click failed ({e}), trying cliclick")
                try:
                    subprocess.run(["cliclick", f"c:{x},{y}"], capture_output=True, timeout=5)
                except Exception as e2:
                    logging.error(f"[CC] cliclick also failed: {e2}")
        elif sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetCursorPos(x, y)
                ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                time.sleep(0.05)
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            except Exception as e:
                logging.error(f"[CC] Win32 click failed: {e}")
        else:
            try:
                subprocess.run(
                    ["xdotool", "mousemove", str(x), str(y), "click", "1"],
                    capture_output=True, timeout=5
                )
            except Exception as e:
                logging.error(f"[CC] xdotool click failed: {e}")

    def _type_text(self, text: str):
        if not text:
            return
        logging.info(f"[CC] Type: {text[:60]!r}")
        if sys.platform == "darwin":
            # Escape quotes and backslashes for AppleScript
            safe = text.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{safe}"'
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
            except Exception as e:
                logging.error(f"[CC] osascript type failed: {e}")
        elif sys.platform == "win32":
            try:
                import ctypes
                for char in text:
                    vk = ctypes.windll.user32.VkKeyScanW(ord(char)) & 0xFF
                    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.02)
                    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            except Exception as e:
                logging.error(f"[CC] Win32 type failed: {e}")
        else:
            try:
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--delay", "50", text],
                    capture_output=True, timeout=30
                )
            except Exception as e:
                logging.error(f"[CC] xdotool type failed: {e}")

    def _scroll(self, direction: str, amount: int = 3):
        logging.info(f"[CC] Scroll {direction} x{amount}")
        if sys.platform == "darwin":
            try:
                import Quartz
                dy = -amount if direction == "down" else amount
                ev = Quartz.CGEventCreateScrollWheelEvent(
                    None, Quartz.kCGScrollEventUnitLine, 1, dy
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            except Exception as e:
                logging.warning(f"[CC] Quartz scroll failed: {e}")
        elif sys.platform == "win32":
            try:
                import ctypes
                delta = -120 * amount if direction == "down" else 120 * amount
                ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta, 0)
            except Exception as e:
                logging.error(f"[CC] Win32 scroll failed: {e}")
        else:
            btn = "5" if direction == "down" else "4"
            try:
                for _ in range(amount):
                    subprocess.run(["xdotool", "click", btn], capture_output=True, timeout=5)
                    time.sleep(0.05)
            except Exception as e:
                logging.error(f"[CC] xdotool scroll failed: {e}")

    def _press_key(self, key_name: str):
        logging.info(f"[CC] Key: {key_name}")
        if sys.platform == "darwin":
            KEY_CODES = {
                "return": 36, "enter": 36,
                "escape": 53, "esc": 53,
                "tab": 48,
                "space": 49,
                "delete": 51, "backspace": 51,
                "left": 123, "right": 124, "down": 125, "up": 126,
                "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
                "f1": 122, "f2": 120, "f3": 99,  "f4": 118,
                "f5": 96,  "f6": 97,  "f7": 98,  "f8": 100,
            }
            key_lower = key_name.lower().strip()
            if key_lower in KEY_CODES:
                code = KEY_CODES[key_lower]
                script = f'tell application "System Events" to key code {code}'
            else:
                safe = key_name.replace('"', '\\"')
                script = f'tell application "System Events" to keystroke "{safe}"'
            try:
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            except Exception as e:
                logging.error(f"[CC] osascript key failed: {e}")
        elif sys.platform == "win32":
            VK_MAP = {
                "enter": 0x0D, "return": 0x0D, "escape": 0x1B, "esc": 0x1B,
                "tab": 0x09, "space": 0x20, "delete": 0x08, "backspace": 0x08,
                "left": 0x25, "right": 0x27, "up": 0x26, "down": 0x28,
            }
            try:
                import ctypes
                vk = VK_MAP.get(key_name.lower(), 0)
                if vk:
                    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.05)
                    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            except Exception as e:
                logging.error(f"[CC] Win32 key failed: {e}")
        else:
            try:
                subprocess.run(["xdotool", "key", key_name], capture_output=True, timeout=5)
            except Exception as e:
                logging.error(f"[CC] xdotool key failed: {e}")
