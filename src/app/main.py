import sys
import os
import logging
import signal
import keyboard
import subprocess
import atexit
import glob

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QFontDatabase
from src.core.logger import setup_logging, setup_exception_hook
from src.ui.window import OmniWindow
from src.core.config import LOGO_PATH, APP_VERSION

def load_fonts():
    """Load custom fonts from assets directory"""
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        assets_dir = os.path.join(project_root, "assets")
        
        font_files = []
        
        # Instrument Serif
        font_files.extend(glob.glob(os.path.join(assets_dir, "Instrument_Serif", "*.ttf")))
        
        # Manrope
        font_files.extend(glob.glob(os.path.join(assets_dir, "Manrope", "*.ttf")))
        font_files.extend(glob.glob(os.path.join(assets_dir, "Manrope", "static", "*.ttf")))
        
        loaded_families = set()
        for font_path in font_files:
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                loaded_families.update(families)
            else:
                logging.warning(f"Failed to load font: {os.path.basename(font_path)}")
                
        logging.info(f"Loaded custom font families: {loaded_families}")
        
    except Exception as e:
        logging.error(f"Error loading fonts: {e}")

def main():
    # Platform specific fixes
    if sys.platform == "linux":
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        os.environ["QT_STYLE_OVERRIDE"] = "kvantum"
    
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-logging --log-level=3"

    setup_logging("omni_ui")
    setup_exception_hook()

    app = QApplication(sys.argv)
    app.setApplicationName("Omni")
    app.setApplicationDisplayName("Omni")
    app.setDesktopFileName("Omni")
    app.setWindowIcon(QIcon(LOGO_PATH))
    app.setQuitOnLastWindowClosed(False)

    # Load custom fonts
    load_fonts()

    # macOS: Set Dock Icon manually if running from source
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication, NSImage
            import Foundation

            app_name = "Omni"

            # 1. Update Process Name
            process_info = Foundation.NSProcessInfo.processInfo()
            process_info.setProcessName_(app_name)

            # 2. Hide from Dock (Accessory Mode)
            # NSApplicationActivationPolicyAccessory = 1
            ns_app = NSApplication.sharedApplication()
            ns_app.setActivationPolicy_(1)

            # 3. Fix App Name in Menu Bar/Dock (Best effort for script)
            bundle = Foundation.NSBundle.mainBundle()
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            if info:
                info['CFBundleName'] = app_name

            ns_app = NSApplication.sharedApplication()
            
            # 3. Set Icon
            image = NSImage.alloc().initWithContentsOfFile_(LOGO_PATH)
            if image:
                ns_app.setApplicationIconImage_(image)

            # 4. Update Menu Bar Item (The bold application menu)
            # This replaces 'Python' with 'Omni' in the top menu bar
            # Note: This might only work if the menu bar has been initialized
            main_menu = ns_app.mainMenu()
            if main_menu:
                app_menu_item = main_menu.itemAtIndex_(0)
                if app_menu_item:
                    app_menu_item.setTitle_(app_name)
                    
                    # Also update the submenu items (About Python -> About Omni, Quit Python -> Quit Omni)
                    app_menu = app_menu_item.submenu()
                    if app_menu:
                        app_menu.setTitle_(app_name)
                        for item in app_menu.itemArray():
                            title = item.title()
                            if "Python" in title:
                                item.setTitle_(title.replace("Python", app_name))

        except ImportError:
            logging.warning("AppKit/Foundation not found, cannot set Dock icon/name")
        except Exception as e:
            logging.warning(f"Failed to set macOS Dock icon: {e}")
    
    # Allow Ctrl+C to interrupt the Qt Event Loop
    from PyQt6.QtCore import QTimer
    
    # Handle Ctrl+C gracefully
    def handle_sigint(signum, frame):
        print("\nReceived SIGINT (Ctrl+C). Quitting Omni...")
        if QApplication.instance():
            QApplication.instance().quit()
        else:
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(100)

    window = OmniWindow()
    window.show()
    window.center()

    # ── Background update check ───────────────────────────────────────────────
    from PyQt6.QtCore import QObject, pyqtSignal as _Signal

    class _UpdateSignal(QObject):
        found = _Signal(str, str, str)   # tag, zipball_url, changelog

    _upd = _UpdateSignal()

    def _show_update_dialog(tag, url, body):
        from src.ui.update_dialog import UpdateDialog
        dlg = UpdateDialog(APP_VERSION, tag, url, body)
        if dlg.exec():   # accepted → user clicked Update Now
            QApplication.instance().quit()

    _upd.found.connect(_show_update_dialog)

    def _bg_update_check():
        from src.core.updater import check_update
        try:
            tag, url, body = check_update(APP_VERSION)
            if tag:
                _upd.found.emit(tag, url, body)
        except Exception as e:
            logging.debug(f"Update check error: {e}")

    import threading as _threading
    _threading.Thread(target=_bg_update_check, daemon=True).start()

    # ── Auth: load saved session + start memory sync ──────────────────────────
    from src.core import auth as _auth
    from src.core import subscription as _subscription
    from src.services.sync import memory_sync as _memory_sync

    # Pass refresh_status as the on_done callback so it runs *after* the saved
    # token is refreshed, avoiding a race where the status fetch fires before
    # the session is loaded and gets a "free" response from the worker.
    _auth.load_saved_session(on_done=_subscription.refresh_status)
    _memory_sync.start(_auth.get_access_token)

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
    # Note: hotkey_state must be defined before use
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

        # Request Accessibility permission via pure ctypes — no PyObjC import
        # needed, so this always runs even if PyObjC bindings are incomplete.
        # Calling AXIsProcessTrustedWithOptions with kAXTrustedCheckOptionPrompt=True
        # registers this app in System Settings → Accessibility and shows the
        # "wants to control your computer" system prompt if not yet approved.
        def _request_ax_permission():
            import ctypes, ctypes.util
            try:
                libobjc = ctypes.CDLL(ctypes.util.find_library("objc"))
                libobjc.objc_getClass.restype   = ctypes.c_void_p
                libobjc.objc_getClass.argtypes  = [ctypes.c_char_p]
                libobjc.sel_registerName.restype  = ctypes.c_void_p
                libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
                msg = libobjc.objc_msgSend

                # [NSNumber numberWithBool:YES]
                msg.restype  = ctypes.c_void_p
                msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
                yes = msg(libobjc.objc_getClass(b"NSNumber"),
                          libobjc.sel_registerName(b"numberWithBool:"), True)

                # [NSString stringWithUTF8String:"AXTrustedCheckOptionPrompt"]
                msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
                key = msg(libobjc.objc_getClass(b"NSString"),
                          libobjc.sel_registerName(b"stringWithUTF8String:"),
                          b"AXTrustedCheckOptionPrompt")

                # [NSDictionary dictionaryWithObject:yes forKey:key]
                msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p]
                opts = msg(libobjc.objc_getClass(b"NSDictionary"),
                           libobjc.sel_registerName(b"dictionaryWithObject:forKey:"),
                           yes, key)

                ax = ctypes.CDLL(
                    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
                ax.AXIsProcessTrustedWithOptions.restype  = ctypes.c_bool
                ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
                ax.AXIsProcessTrustedWithOptions(opts)
            except Exception as e:
                logging.debug(f"[accessibility] {e}")

        QTimer.singleShot(500, _request_ax_permission)

        # macOS: Use Native NSEvent for reliable global hotkeys (requires Accessibility permissions)
        try:
            import AppKit
            from AppKit import NSEvent, NSKeyDownMask, NSFlagsChangedMask

            # 2. Hotkey Logic
            # We need to track state to detect "Empty" Cmd+Opt presses
            # Using a mutable container for closure access
            hotkey_state = {'armed': False}

            def check_flags(flags):
                # Check for Command + Option ONLY (ignoring CapsLock, etc if needed, but strict for now)
                cmd = (flags & AppKit.NSEventModifierFlagCommand)
                opt = (flags & AppKit.NSEventModifierFlagOption)
                
                # Ensure no other "heavy" modifiers are pressed
                ctrl = (flags & AppKit.NSEventModifierFlagControl)
                shift = (flags & AppKit.NSEventModifierFlagShift)
                
                return bool(cmd and opt and not ctrl and not shift)

            def on_flags_changed(event):
                try:
                    flags = event.modifierFlags()
                    is_target_combo = check_flags(flags)
                    
                    if is_target_combo:
                        hotkey_state['armed'] = True
                    else:
                        # If we were armed, and now we are NOT...
                        if hotkey_state['armed']:
                             # Check if we released a key (valid trigger) or pressed an extra one (invalid)
                             # If we lost Command OR Option, it's a valid release.
                             
                             now_cmd = (flags & AppKit.NSEventModifierFlagCommand)
                             now_opt = (flags & AppKit.NSEventModifierFlagOption)
                             
                             if (not now_cmd) or (not now_opt):
                                 logging.info("Global Hotkey Option+Command Triggered (Native)")
                                 toggle_omni()
                        
                        hotkey_state['armed'] = False
                except Exception as e:
                    logging.error(f"Error in on_flags_changed: {e}")

            def on_keydown(event):
                try:
                    # If any key is pressed, disarm the modifier-only trigger
                    hotkey_state['armed'] = False
                    
                    # Optional: Handle Option+Space
                    if event.keyCode() == 49: # Space
                        flags = event.modifierFlags()
                        if (flags & AppKit.NSEventModifierFlagOption) and not (flags & AppKit.NSEventModifierFlagCommand):
                             # Pure Option+Space (to avoid conflict with Cmd+Opt+Space if that's a thing)
                             logging.info("Global Hotkey Option+Space Triggered (Native)")
                             toggle_omni()
                             return None # Attempt to swallow (only works for Local)
                             
                except Exception as e:
                    logging.error(f"Error in on_keydown: {e}")
                return event

            # 3. Register Monitors
            # Global Monitor (Background - requires Accessibility)
            # Handlers must not return anything
            def global_flags_handler(event):
                on_flags_changed(event)
            
            def global_keydown_handler(event):
                on_keydown(event)

            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSFlagsChangedMask, global_flags_handler)
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSKeyDownMask, global_keydown_handler)

            # Local Monitor (Foreground)
            # Handlers must return the event (or None)
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSFlagsChangedMask, lambda e: (on_flags_changed(e), e)[1])
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSKeyDownMask, on_keydown)
            
            logging.info("Native macOS hotkey listeners registered (Option+Cmd, Option+Space).")
            
        except ImportError:
            logging.error("PyObjC not found. Global hotkeys disabled on macOS.")
        except Exception as e:
            logging.error(f"Failed to setup native macOS hotkeys: {e}")
            # Fallback to keyboard module if pynput fails
            try:
                keyboard.add_hotkey('alt+space', toggle_omni, suppress=True)
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
    # Native macOS ASR uses a compiled Swift binary (streaming_asr) that handles
    # TCC permissions natively. Falls back to Groq Whisper API if binary missing.
    voice_process = None
    try:
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        listener_script = os.path.join(_project_root, "src", "services", "voice", "listener.py")
        logging.info(f"Starting Voice Listener: {listener_script}")

        if os.environ.get("OMNI_LISTENER_LAUNCHED") != "1":
            voice_process = subprocess.Popen(
                [sys.executable, listener_script],
                cwd=_project_root,
                stdout=None,
                stderr=None,
                start_new_session=True
            )
            logging.info("Voice Listener started.")

        def kill_voice():
            if voice_process:
                try:
                    voice_process.terminate()
                except Exception:
                    pass
            if sys.platform == "darwin":
                try:
                    subprocess.run(["pkill", "-f", "services/voice/listener.py"], check=False)
                except Exception:
                    pass

        atexit.register(kill_voice)
        app.aboutToQuit.connect(kill_voice)

    except Exception as e:
        logging.error(f"Failed to start voice listener: {e}")

    # Pre-load Kokoro TTS in background to avoid delay on first speech
    try:
        import threading
        from src.services.llm.model_manager import ensure_tts_model

        def _preload_tts():
            logging.info("Pre-loading TTS Model in background...")
            try:
                ensure_tts_model()
                logging.info("TTS Model pre-loaded.")
            except Exception as e:
                logging.error(f"TTS Pre-load failed: {e}")

        threading.Thread(target=_preload_tts, daemon=True).start()
    except Exception as e:
        logging.error(f"Failed to start TTS pre-loader: {e}")

    # Start the application
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
