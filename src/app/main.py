import sys
import os
import logging
import signal
import keyboard
import subprocess
import atexit

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
    window.show()
    window.center()

    # Global Hotkey Setup
    def toggle_omni():
        try:
            # Emit signal to handle UI update on main thread
            # Source is 'manual' because this comes from keyboard hotkey
            window.toggle_requested.emit("manual")
        except Exception as e:
            logging.error(f"Error in toggle_omni: {e}")

    # Smart Windows Key Handling: Suppress Start Menu, allow shortcuts
    # Strategy: Use Low Level Hook (WH_KEYBOARD_LL) via ctypes to intercept the key.
    hotkey_state = {'win_down': False, 'other_pressed': False}

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        WH_KEYBOARD_LL = 13
        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        WM_SYSKEYDOWN = 0x0104
        WM_SYSKEYUP = 0x0105
        
        LLKHF_INJECTED = 0x00000010

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD),
                        ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_ulonglong)]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT))
        
        hook_id = None

        def low_level_keyboard_handler(nCode, wParam, lParam):
            if nCode == 0:
                # Check if injected (prevent infinite loop and allow our synthetic events)
                if lParam.contents.flags & LLKHF_INJECTED:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                vk_code = lParam.contents.vkCode
                # VK_LWIN = 0x5B, VK_RWIN = 0x5C
                is_win = (vk_code == 0x5B or vk_code == 0x5C)
                
                if is_win:
                    if wParam == WM_KEYDOWN or wParam == WM_SYSKEYDOWN:
                        if not hotkey_state['win_down']:
                            hotkey_state['win_down'] = True
                            hotkey_state['other_pressed'] = False
                    
                    elif wParam == WM_KEYUP or wParam == WM_SYSKEYUP:
                        was_down = hotkey_state['win_down']
                        hotkey_state['win_down'] = False
                        
                        # If it was a lone press, toggle Omni
                        if was_down and not hotkey_state['other_pressed']:
                            try:
                                # 1. Toggle Omni
                                toggle_omni()
                                
                                # 2. Prevent Start Menu
                                # Suppress the original UP event so Start Menu doesn't trigger immediately.
                                # But we must ensure the OS knows the key is up.
                                # Wrap the synthetic Win Up in Ctrl to trick OS into thinking it was a shortcut.
                                
                                # 0x11 = VK_CONTROL
                                # 0x02 = KEYEVENTF_KEYUP
                                
                                user32.keybd_event(0x11, 0, 0, 0) # Ctrl Down
                                user32.keybd_event(vk_code, 0, 2, 0) # Win Up
                                user32.keybd_event(0x11, 0, 2, 0) # Ctrl Up
                                
                                return 1 # Suppress original event
                            except Exception as e:
                                logging.error(f"Error in hook callback: {e}")
                                # If error, fall through to default behavior
                            
                else:
                    # Other key
                    if wParam == WM_KEYDOWN or wParam == WM_SYSKEYDOWN:
                        if hotkey_state['win_down']:
                            hotkey_state['other_pressed'] = True
            
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        # Keep reference to callback to prevent GC
        pointer = HOOKPROC(low_level_keyboard_handler)

        def install_hook():
            global hook_id
            hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, pointer, kernel32.GetModuleHandleW(None), 0)
            if not hook_id:
                logging.error("Failed to install low-level keyboard hook")
        
        def uninstall_hook():
            if hook_id:
                user32.UnhookWindowsHookEx(hook_id)

        install_hook()
        atexit.register(uninstall_hook)

    else:
        # Fallback for non-Windows
        pass

    # Global Hotkey Registration
    if sys.platform == "darwin":
        # macOS: Use pynput for reliable global hotkeys
        try:
            from pynput import keyboard as pynput_keyboard
            
            def on_activate():
                logging.info("Global hotkey <ctrl>+<space> activated (pynput)")
                toggle_omni()

            # Non-blocking listener
            hotkey_listener = pynput_keyboard.GlobalHotKeys({
                '<ctrl>+<space>': on_activate
            })
            hotkey_listener.start()
            logging.info("Global hotkey 'ctrl+space' registered via pynput (macOS)")
        except Exception as e:
            logging.error(f"Failed to register pynput hotkey on macOS: {e}")
            # Fallback to keyboard module if pynput fails
            try:
                keyboard.add_hotkey('ctrl+space', toggle_omni, suppress=True)
            except Exception as e2:
                logging.error(f"Fallback keyboard hotkey also failed: {e2}")

    else:
        # Windows / Linux: Use keyboard module
        try:
            # Add backup hotkey for testing
            keyboard.add_hotkey('ctrl+space', toggle_omni, suppress=True)
            logging.info("Global hotkey 'ctrl+space' registered via keyboard module")
        except Exception as e:
            logging.error(f"Failed to register global hotkey: {e}")

    # Check for Admin privileges on Windows
    if sys.platform == "win32":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                logging.warning("App is NOT running as Administrator. 'Windows' key suppression might fail.")
        except: pass

    # Start Voice Listener
    voice_process = None
    try:
        listener_script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
                                       "src", "services", "voice", "listener.py")
        
        logging.info(f"Starting Voice Listener: {listener_script}")
        
        # Use stdout=None to see output in terminal for debugging
        voice_process = subprocess.Popen([sys.executable, listener_script], 
                                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                         stdout=None, 
                                         stderr=None)
        
        def kill_voice():
            if voice_process:
                voice_process.terminate()
        
        atexit.register(kill_voice)
        logging.info("Voice Listener started.")
        
    except Exception as e:
        logging.error(f"Failed to start voice listener: {e}")

    # Start the application
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
