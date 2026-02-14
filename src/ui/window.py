import os
import sys
import logging
import subprocess
import json
import time
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QFrame, QAbstractItemView,
                             QGraphicsDropShadowEffect, QLabel, QScrollArea, QProgressBar, QMessageBox, QGraphicsOpacityEffect)
from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect, QRectF, QEvent, QUrl, QParallelAnimationGroup, pyqtProperty, pyqtSignal, QThreadPool
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QPainterPath, QBrush, QLinearGradient, QDesktopServices, QCursor, QGuiApplication, QFontDatabase, QPen, QBitmap

from src.core.config import LOGO_PATH
from src.ui.styles import get_style_sheet, THEMES
from src.core.ipc import start_ipc_listener
from src.services.system.app_launcher import get_app_cache

from src.ui.widgets.action_widgets import (LinkActionWidget, InstallActionWidget, FileActionWidget, 
                                         PersonActionWidget, PlaceActionWidget, AppActionWidget)
from src.ui.widgets.install_widget import InstallProgressWidget
from src.ui.widgets.command_widget import CommandLogWidget
import socket
from src.ui.widgets.misc_widgets import (ThinkingWidget, SeparatorWidget, SmoothEntryWidget, 
                                       FollowUpWidget, AnswerWidget, StandardItemWidget, 
                                       RotatingLabel, GradientBorderFrame, ReplyActionWidget, IconLoader, MicWidget)
from src.ui.widgets.list_widget import SmoothScrollListWidget

from src.ui.workers.ai_worker import AIWorker
from src.ui.workers.search_worker import SearchWorker
from src.ui.workers.action_worker import ActionWorker
from src.ui.workers.screenshot_worker import ScreenshotWorker
from src.ui.workers.install_worker import InstallOrchestrator, InstallWorker
from src.ui.workers.file_search_worker import FileSearchWorker
from src.ui.workers.tts_worker import TTSWorker

try:
    from BlurWindow.blurWindow import blur
except ImportError:
    if sys.platform == "win32":
        logging.warning("BlurWindow library not found. Blur effect disabled.")
    blur = None

if sys.platform == "darwin":
    try:
        import objc
        from AppKit import NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow, \
                           NSVisualEffectMaterialHUDWindow, NSViewWidthSizable, NSViewHeightSizable, \
                           NSColor, NSApplication, NSAppearance, NSAppearanceNameVibrantLight, NSAppearanceNameVibrantDark
    except ImportError:
        logging.warning("PyObjC not found. MacOS blur disabled.")

    # Import Foundation separately to ensure it's available for theme detection
    # even if AppKit import had issues (though unlikely if blur works)
    try:
        from Foundation import NSUserDefaults
    except ImportError:
        logging.warning("PyObjC Foundation not found. Theme detection disabled.")

DEFAULT_WIDTH = 720

class OmniWindow(QWidget):
    # Signal for external triggers (e.g. global hotkey)
    toggle_requested = pyqtSignal(str) # Accepts source

    def setup_uinput(self):
        # Linux only
        if sys.platform != "linux":
            self.uinput_available = False
            return

        try:
            import uinput
            # 3840x1080 typically, but ideally we'd get this from screens
            WIDTH = 3840 
            HEIGHT = 1080
            events = (
                uinput.BTN_LEFT,
                uinput.ABS_X + (0, WIDTH, 0, 0),
                uinput.ABS_Y + (0, HEIGHT, 0, 0),
            )
            self.uinput_device = uinput.Device(events)
            logging.info("uinput device created successfully")
            self.uinput_available = True
        except ImportError:
            logging.warning("uinput module not found")
            self.uinput_available = False
        except PermissionError:
            logging.error("Permission denied for /dev/uinput")
            self.uinput_available = False
        except Exception as e:
            logging.error(f"uinput init failed: {e}")
            self.uinput_available = False

    def __init__(self):
        super().__init__()
        
        self.old_workers = []  # Keep references to running workers to prevent QThread destruction crash
        
        self.toggle_requested.connect(self.toggle_visibility_safe)
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("omni-search")
        self.setWindowIcon(QIcon(LOGO_PATH))
        self.resize(DEFAULT_WIDTH, 160) # Slightly larger initial size
        
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(450)
        self.anim.setEasingCurve(QEasingCurve.Type.OutExpo)
        
        self.setup_uinput()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.frame = GradientBorderFrame()
        self.frame.setObjectName("MainFrame")
        
        self._is_closing = False # Flag to track animation state

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        # Content Frame (Inner)
        self.content_frame = QWidget()
        self.content_frame.setObjectName("ContentFrame")
        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        frame_layout.addWidget(self.content_frame)

        self.input_container = QWidget()
        self.input_container.setFixedHeight(84) # Increased height to prevent clipping
        input_layout = QHBoxLayout(self.input_container)
        input_layout.setContentsMargins(24, 4, 12, 4) # Increased left margin
        input_layout.setSpacing(4)

        self.logo_label = RotatingLabel()
        self.logo_label.setFixedSize(50, 50)
        # self.logo_label.right_clicked.connect(self.enter_settings_mode)
        logo_pix = QPixmap(LOGO_PATH)
        if not logo_pix.isNull():
            self.logo_label.setPixmap(logo_pix.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Search or ask...")
        self.input_field.textChanged.connect(self.on_text_changed)
        self.input_field.returnPressed.connect(self.on_entered)
        self.input_field.installEventFilter(self)

        self.mic_widget = MicWidget()
        self.mic_widget.clicked.connect(self.toggle_listening)

        input_layout.addWidget(self.logo_label)
        input_layout.addWidget(self.input_field, 1)  # Stretch factor 1 = expand to fill space

        self.follow_up_widget = FollowUpWidget()
        input_layout.addWidget(self.follow_up_widget)
        
        # New: Computer Control Status Container (Hidden by default)
        self.cc_container = QWidget()
        cc_layout = QVBoxLayout(self.cc_container)
        cc_layout.setContentsMargins(0, 0, 0, 0)
        cc_layout.setSpacing(0)
        
        self.cc_title = QLabel("COMPUTER CONTROL")
        self.cc_title.setFont(QFont("Instrument Serif", 16, QFont.Weight.Bold))
        self.cc_title.setStyleSheet("color: #111111; letter-spacing: 0.5px;")
        
        self.cc_status = QLabel("Active")
        self.cc_status.setFont(QFont("Manrope", 11, QFont.Weight.Normal))
        self.cc_status.setStyleSheet("color: #888888;")
        
        cc_layout.addWidget(self.cc_title)
        cc_layout.addWidget(self.cc_status)
        
        self.cc_container.hide()
        input_layout.addWidget(self.cc_container)

        # Mic at the end (Right edge)
        input_layout.addWidget(self.mic_widget)

        self.divider = QFrame()
        self.divider.setObjectName("Divider")

        self.list_widget = SmoothScrollListWidget()
        self.list_widget.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.itemClicked.connect(self.on_entered)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Allow keyboard nav
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setMouseTracking(True)  # Enable mouse tracking for smooth hover
        self.list_widget.setStyleSheet("""
            QListWidget {
                outline: none;
                background: transparent;
                border: none;
            }
            QListWidget::item:selected {
                background-color: rgba(255, 255, 255, 0.15);
                border-radius: 16px;
            }
            QListWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.15);
                border-radius: 16px;
            }
            QListWidget::item:selected:hover {
                background-color: rgba(255, 255, 255, 0.15);
                border-radius: 16px;
            }
            QListWidget::item:focus {
                background-color: rgba(255, 255, 255, 0.15);
                outline: none;
            }
        """)

        content_layout.addWidget(self.input_container)
        content_layout.addWidget(self.divider)
        content_layout.addWidget(self.list_widget, 1) # Expand to fill available space
        # content_layout.addStretch() # Removed to prevent squashing list
        main_layout.addWidget(self.frame)

        # self.setStyleSheet(STYLE_SHEET) # Moved to set_theme

        self.chat_history = []  
        self.is_history_mode = False
        # self.is_settings_mode = False

        self.apps = self.load_apps()
        self.is_entry_animating = False
        self.is_installing = False 
        self.voice_triggered_query = False
        self.refresh_list("", animate=False)
        self.center()  

        # self.animate_entry() # Don't auto-show on init. Let the caller decide or hotkey trigger it.

        self.search_worker = None
        self.action_worker = None
        self.ai_worker = None
        self.file_search_worker = None
        self.tts_worker = None
        self.is_tts_playing = False

        self.current_theme = "dark" # Default
        
        # Detect and set initial theme
        initial_theme = self.detect_system_theme()
        self.set_theme(initial_theme)

        self.external_actions = []
        self.external_search_results = []
        self.local_file_results = []

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(300)  # FAST: Reduced from 650ms to 300ms
        self.debounce_timer.timeout.connect(self.trigger_async_searches)

        # Start IPC Listener
        start_ipc_listener(self)

        # Warm up IconLoader (initializes QFileIconProvider/CoInitialize/etc)
        # We use sys.executable to trigger the heavy path for EXE icons to prevent freeze on first type
        warmup_loader = IconLoader(sys.executable)
        QThreadPool.globalInstance().start(warmup_loader)

        # Apply initial blur
        self.apply_blur()

    def detect_system_theme(self):
        """Detect MacOS system theme."""
        if sys.platform == "darwin":
            # Method 1: NSUserDefaults (Standard)
            try:
                # Ensure we have the class imported
                from Foundation import NSUserDefaults
                defaults = NSUserDefaults.standardUserDefaults()
                defaults.synchronize() # Force update
                style = defaults.stringForKey_("AppleInterfaceStyle")
                
                # "Dark" returns "Dark", Light returns None (nil)
                if style == "Dark":
                    theme = "dark"
                else:
                    theme = "light"
                
                logging.info(f"System theme detected (NSUserDefaults): {theme}")
                return theme
            except Exception as e:
                logging.warning(f"NSUserDefaults theme detection failed: {e}")
            
            # Method 2: Fallback to subprocess (Slow but reliable)
            try:
                import subprocess
                # Read global domain for AppleInterfaceStyle
                # Use full path to defaults to avoid PATH issues
                result = subprocess.run(
                    ["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"], 
                    capture_output=True, 
                    text=True
                )
                
                # If command succeeds and prints "Dark", it's dark.
                # If command fails (exit code 1) it usually means key doesn't exist -> Light
                if result.returncode == 0 and "Dark" in result.stdout:
                    theme = "dark"
                else:
                    theme = "light"
                
                logging.info(f"System theme detected (subprocess): {theme}")
                return theme
            except Exception as e:
                logging.error(f"Subprocess theme detection failed: {e}")
                # If both methods fail, we can't be sure.
                # But if we are here, it means even 'defaults' command failed to run.
                
        theme = "dark"
        logging.info(f"System theme detected (default): {theme}")
        return theme # Default for other platforms/failure

    def set_theme(self, theme_name):
        """Apply theme to window and all children."""
        if theme_name not in THEMES:
            theme_name = "dark"
            
        self.current_theme = theme_name
        theme_data = THEMES[theme_name]
        
        # 1. Update Window Stylesheet
        self.setStyleSheet(get_style_sheet(theme_name))
        
        # 2. Update Frame (GradientBorderFrame)
        if hasattr(self, 'frame'):
            self.frame.set_theme(theme_name)
            
        # 3. Update specific widgets that need manual update
        if hasattr(self, 'follow_up_widget'):
            self.follow_up_widget.set_theme(theme_name)
            
        if hasattr(self, 'mic_widget'):
            self.mic_widget.set_theme(theme_name)
            
        # 4. Update List Items (Iterate and update)
        if hasattr(self, 'list_widget'):
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                widget_container = self.list_widget.itemWidget(item)
                if isinstance(widget_container, SmoothEntryWidget):
                    real_widget = widget_container.content_widget
                    if hasattr(real_widget, 'set_theme'):
                        real_widget.set_theme(theme_name)
                elif hasattr(widget_container, 'set_theme'):
                    widget_container.set_theme(theme_name)
                    
        # 5. Update Glass Effect
        self.update_glass_color()
        
    def update_glass_color(self):
        """Update the tint color of the glass effect view."""
        if sys.platform == "darwin" and hasattr(self, 'mac_blur_view'):
            try:
                # Check if it's the liquid glass view (NSGlassEffectView)
                # We can check by class name or if it has setTintColor_
                if hasattr(self.mac_blur_view, 'setTintColor_'):
                    t = THEMES[self.current_theme]
                    color = NSColor.colorWithCalibratedWhite_alpha_(t['glass_tint_white'], t['glass_tint_alpha'])
                    self.mac_blur_view.setTintColor_(color)
                elif hasattr(self.mac_blur_view, 'setMaterial_'):
                    # Standard NSVisualEffectView fallback
                    # 13 = HUDWindow (Dark), 9 = Popover (Adaptive/Light), 2 = UnderWindowBackground
                    if self.current_theme == "light":
                        # Use UnderWindowBackground (2) for standard light window look
                        self.mac_blur_view.setMaterial_(2) # NSVisualEffectMaterialUnderWindowBackground
                        
                        try:
                            appearance = NSAppearance.appearanceNamed_(NSAppearanceNameVibrantLight)
                            self.mac_blur_view.setAppearance_(appearance)
                        except: pass
                    else:
                        # Use HUDWindow for dark vibrant look
                        self.mac_blur_view.setMaterial_(13) # NSVisualEffectMaterialHUDWindow
                        
                        try:
                            appearance = NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark)
                            self.mac_blur_view.setAppearance_(appearance)
                        except: pass
            except Exception as e:
                logging.error(f"Error updating glass color: {e}")

    def load_apps(self):
        return get_app_cache()

    def apply_blur(self):
        # Apply blur effect based on platform
        if sys.platform == "win32":
            if blur:
                try:
                    blur(int(self.winId()))
                except Exception as e:
                    logging.error(f"BlurWindow Error: {e}")
        elif sys.platform == "linux":
            # Force KWin Blur on X11 window
            try:
                wid = self.winId()
                if not wid: return
                
                rect_args = f"0, 0, {self.width()}, {self.height()}"
                
                cmd = [
                    "xprop", 
                    "-id", str(wid), 
                    "-f", "_KDE_NET_WM_BLUR_BEHIND_REGION", "32c", 
                    "-set", "_KDE_NET_WM_BLUR_BEHIND_REGION", 
                    rect_args
                ]
                
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                logging.error(f"KWin Blur Error: {e}")
        elif sys.platform == "darwin":
            try:
                import ctypes
                
                # Get the NSView pointer
                view_ptr = int(self.winId())
                # objc.objc_object takes c_void_p argument
                ns_view = objc.objc_object(c_void_p=ctypes.c_void_p(view_ptr))
                
                # Check if we already have a blur view attached
                if hasattr(self, 'mac_blur_view'):
                    # Update frame to match Qt view (handles resize/move animations)
                    self.mac_blur_view.setFrame_(ns_view.frame())
                else:
                    # Strategy: Add blur view as a SIBLING behind the Qt view.
                    # This ensures it doesn't cover the Qt content (child covers parent)
                    # and allows Qt to draw on top of it.
                    
                    superview = ns_view.superview()
                    if superview:
                        # Create Visual Effect View with same frame as Qt view
                        
                        # Try NSGlassEffectView (Liquid Glass) first
                        GlassEffectView = None
                        try:
                            GlassEffectView = objc.lookUpClass("NSGlassEffectView")
                        except: pass

                        if GlassEffectView:
                            self.mac_blur_view = GlassEffectView.alloc().initWithFrame_(ns_view.frame())
                            # Configure Liquid Glass
                            # Use theme colors
                            t = THEMES[self.current_theme]
                            color = NSColor.colorWithCalibratedWhite_alpha_(t['glass_tint_white'], t['glass_tint_alpha']) 
                            self.mac_blur_view.setTintColor_(color)
                            self.mac_blur_view.setCornerRadius_(24.0)
                            self.mac_blur_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                            
                            # Ensure layer clipping is enabled to prevent artifacts
                            self.mac_blur_view.setWantsLayer_(True)
                            self.mac_blur_view.layer().setCornerRadius_(24.0)
                            self.mac_blur_view.layer().setMasksToBounds_(True)
                            
                            logging.info("MacOS Liquid Glass applied via NSGlassEffectView")
                        else:
                            # Fallback to NSVisualEffectView
                            self.mac_blur_view = NSVisualEffectView.alloc().initWithFrame_(ns_view.frame())
                            
                            # Configure based on theme
                            if self.current_theme == "light":
                                # Use UnderWindowBackground (2) for standard light window look
                                # Popover (9) is also good but UnderWindowBackground is safer
                                self.mac_blur_view.setMaterial_(2) # NSVisualEffectMaterialUnderWindowBackground
                                
                                # Force light appearance for consistent "liquid glass" look
                                try:
                                    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameVibrantLight)
                                    self.mac_blur_view.setAppearance_(appearance)
                                except Exception as e:
                                    logging.warning(f"Failed to set light appearance: {e}")
                                    
                                logging.info("Using NSVisualEffectMaterialUnderWindowBackground (Light)")
                            else:
                                # Use HUDWindow (13) for dark vibrant look
                                self.mac_blur_view.setMaterial_(13) # NSVisualEffectMaterialHUDWindow
                                
                                # Force dark appearance
                                try:
                                    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark)
                                    self.mac_blur_view.setAppearance_(appearance)
                                except: pass
                                
                                logging.info("Using NSVisualEffectMaterialHUDWindow (Dark)")
                                
                            self.mac_blur_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
                            self.mac_blur_view.setState_(1) # Active
                            self.mac_blur_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                            
                            # Apply rounded corners to the blur view's layer
                            self.mac_blur_view.setWantsLayer_(True)
                            self.mac_blur_view.layer().setCornerRadius_(24.0)
                        self.mac_blur_view.layer().setMasksToBounds_(True)

                        # Insert BEHIND the Qt view (-1 = NSWindowBelow)
                        superview.addSubview_positioned_relativeTo_(self.mac_blur_view, -1, ns_view)
                        
                        # logging.info("MacOS Blur applied via Sibling Strategy")
                    else:
                        # Fallback: If no superview (rare), try adding as subview but at bottom
                        # Note: This might obscure content if Qt draws in drawRect
                        logging.warning("MacOS Blur: No superview found. Falling back to subview (might obscure content).")
                        
                        self.mac_blur_view = NSVisualEffectView.alloc().initWithFrame_(ns_view.bounds())
                        self.mac_blur_view.setMaterial_(NSVisualEffectMaterialHUDWindow)
                        self.mac_blur_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
                        self.mac_blur_view.setState_(1)
                        self.mac_blur_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                        
                        ns_view.addSubview_positioned_relativeTo_(self.mac_blur_view, -1, None)
                        
                # Ensure color/material matches current theme
                self.update_glass_color()
                        
            except Exception as e:
                logging.error(f"MacOS Blur Error: {e}")
        
        # self.update_mask() # Disabled to fix corner artifacts (black corners)

    def update_mask(self):
        return # Disabled
        # Clip window to rounded corners to fix corner artifacts using QBitmap
        # This provides a 1-bit mask that clips the entire window surface, including blur
        mask = QBitmap(self.size())
        mask.fill(Qt.GlobalColor.color0) # Clear
        
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False) # Masks are 1-bit
        painter.setBrush(Qt.GlobalColor.color1) # Opaque
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Draw the rounded rect for the mask
        # Match border-radius of 24px from styles
        painter.drawRoundedRect(self.rect(), 24, 24)
        painter.end()
        
        self.setMask(mask)

    def showEvent(self, event):
        super().showEvent(event)
        
        # Check for theme change on show
        current_os_theme = self.detect_system_theme()
        if current_os_theme != self.current_theme:
            self.set_theme(current_os_theme)
            
        # Ensure minimal size on show if in search mode
        if not self.is_history_mode and not self.input_field.text():
             self.resize(self.width(), 84)
             
        QTimer.singleShot(100, self.apply_blur)
        # Focus immediately and again after a short delay (Windows often needs both)
        self.force_focus()
        QTimer.singleShot(50, self.force_focus)
        QTimer.singleShot(200, self.force_focus)

    def force_focus(self):
        self.activateWindow()
        self.raise_()
        if sys.platform == "darwin":
            try:
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception as e:
                logging.debug(f"MacOS activateIgnoringOtherApps failed: {e}")
        elif sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                if hwnd:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception as e:
                logging.debug(f"SetForegroundWindow failed: {e}")
        self.input_field.setFocus()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_blur()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.apply_blur()
                
    def center(self):
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            y = max(40, y - 150)
            self.move(int(x), int(y))
        else:
            self.move(100, 100)

    def reset_to_search_mode(self, animate=True, clear=True):
        if hasattr(self, 'anim'): self.anim.stop()
        if hasattr(self, 'anim_group'): self.anim_group.stop()
        if hasattr(self, 'anim_close_group'): self.anim_close_group.stop()
        
        self.is_history_mode = False
        self.follow_up_widget.set_active(False)
        self.frame.set_minimal_mode(True) # Minimal mode for search
        
        self.input_field.blockSignals(True)
        if clear:
            self.input_field.clear()
        self.input_field.blockSignals(False)
        
        # Force resize to minimal
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        # Always reset to DEFAULT_WIDTH to prevent shrinking over time
        self.resize(DEFAULT_WIDTH, 84)
        
        text = self.input_field.text() if not clear else ""
        self.refresh_list(text, animate=animate)

    def toggle_visibility_safe(self, source="manual"):
        logging.info(f"toggle_visibility_safe called. Current visibility: {self.isVisible()}, Source: {source}")
        
        if self.isVisible():
            # If already visible...
            if source == "voice":
                # If voice triggered it again, just ensure we are listening (don't close)
                logging.info("Window already visible, voice trigger -> ensuring LISTENING mode")
                self.send_udp_command("SET_MODE:LISTENING")
                self.mic_widget.set_active(True)
                # Maybe flash the logo or UI to acknowledge?
                self.logo_label.boost_speed()
            else:
                # Manual toggle (hotkey/tray) -> Close
                self.animate_close()
        else:
            self.reset_to_search_mode(animate=False)
            self.chat_history = [] # Start clean
            self.show()
            self.center()
            self.animate_entry()
            self.input_field.setFocus()
            
            # Handle Voice Logic based on source
            if source == "voice":
                # Opened via "Hey Omni" -> Start active listening
                self.send_udp_command("SET_MODE:LISTENING")
                self.mic_widget.set_active(True)
            else:
                # Opened manually -> Pause listening (wait for mic click)
                self.send_udp_command("SET_MODE:PAUSED")
                self.mic_widget.set_active(False)

    def send_udp_command(self, command):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(command.encode('utf-8'), ('127.0.0.1', 5557))
        except Exception as e:
            logging.error(f"UDP Error: {e}")

    def toggle_listening(self):
        if self.mic_widget.active:
            # Stop listening -> Commit Audio (Process what was said)
            self.send_udp_command("COMMIT_AUDIO")
            self.mic_widget.set_active(False)
            # Since user manually recorded, we treat this as a voice query for TTS purposes
            self.voice_triggered_query = True
        else:
            # Start listening -> Go to LISTENING
            self.send_udp_command("SET_MODE:LISTENING")
            self.mic_widget.set_active(True)

    def handle_voice_status(self, status):
        if status == "LISTENING":
            self.mic_widget.set_active(True)
            self.input_field.setPlaceholderText("Listening...")
        elif status == "PAUSED":
            self.mic_widget.set_active(False)
            self.input_field.setPlaceholderText("Search or ask...")
            # If we were expecting a voice query but it was just paused without query, maybe reset?
            # But handle_ipc_query will come later if query was found.
        elif status == "IDLE":
            # Should happen when window is hidden, but if it happens while visible,
            # it means we are waiting for wake word.
            self.mic_widget.set_active(False)
            self.input_field.setPlaceholderText("Search or ask...")

    def handle_partial_text(self, text):
        # Update input field with partial text without triggering search
        self.input_field.blockSignals(True)
        self.input_field.setText(text)
        self.input_field.blockSignals(False)
        # Maybe move cursor to end
        self.input_field.setCursorPosition(len(text))

    def handle_ipc_query(self, query):
        logging.info(f"IPC Query Received: {query}")
        
        # Ensure window is visible
        if not self.isVisible():
            self.reset_to_search_mode(animate=False)
            self.chat_history = []
            self.show()
            self.center()
            self.animate_entry()
        
        self.raise_()
        self.activateWindow()
        
        # Clean up query if it has VOICE: prefix
        if query.startswith("VOICE:"):
            query = query[6:]
            self.voice_triggered_query = True
        
        # Set text and submit
        self.input_field.setText(query)
        self.perform_ai_query(query)

    def animate_entry(self):
        self.is_entry_animating = True
        self.frame.boost_speed()
        
        # Use windowOpacity for smoother whole-window fade (including blur)
        self.setWindowOpacity(0.0)
        self.setGraphicsEffect(None)
        
        self.anim_group = QParallelAnimationGroup()
        
        def on_finished():
            self.is_entry_animating = False
        
        self.anim_group.finished.connect(on_finished)
        
        # Zoom In Animation
        target_geo = self.geometry()
        center = target_geo.center()
        
        # Start size: 92% (Subtle zoom)
        start_w = int(target_geo.width() * 0.92)
        start_h = int(target_geo.height() * 0.92)
        start_x = center.x() - start_w // 2
        start_y = center.y() - start_h // 2
        
        start_geo = QRect(start_x, start_y, start_w, start_h)
        
        self.setGeometry(start_geo)
        
        # Geometry Animation
        anim_geo = QPropertyAnimation(self, b"geometry")
        anim_geo.setDuration(350) # Slightly slower for elegance
        anim_geo.setStartValue(start_geo)
        anim_geo.setEndValue(target_geo)
        anim_geo.setEasingCurve(QEasingCurve.Type.OutBack) # The "Soul" - subtle pop
        
        # Opacity Animation
        anim_opa = QPropertyAnimation(self, b"windowOpacity")
        anim_opa.setDuration(250) # Fade in faster than zoom
        anim_opa.setStartValue(0.0)
        anim_opa.setEndValue(1.0)
        anim_opa.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self.anim_group.addAnimation(anim_geo)
        self.anim_group.addAnimation(anim_opa)
        self.anim_group.start()
        
        self.input_field.setFocus()
        self.activateWindow()
        self.raise_()

    def adjust_window_height(self, animate=True):
        # if self.is_settings_mode: return

        if hasattr(self, 'is_entry_animating') and self.is_entry_animating:
            # Don't interrupt entry animation
            return
        
        list_h = 0
        count = self.list_widget.count()
        
        if count > 0:
            self.divider.show()
            self.list_widget.show()
            for i in range(count):
                item = self.list_widget.item(i)
                list_h += item.sizeHint().height()
            
            base_h = 84 
            extra_padding = 30
            
            # Dynamic height calculation to prevent going behind taskbar
            screen = QGuiApplication.screenAt(self.pos()) or QApplication.primaryScreen()
            max_h_limit = 600
            
            if screen:
                avail_geo = screen.availableGeometry()
                # Calculate space below the window's top edge
                # avail_geo.bottom() is the y-coordinate of the dock/taskbar top edge
                space_below = avail_geo.bottom() - self.y() - 20 # 20px safety margin
                max_list_avail = space_below - base_h - extra_padding
                max_h_limit = max(100, max_list_avail)
            
            # Cap list height
            # We use 800 as a reasonable absolute max, but constrain by screen space
            list_h = min(list_h, 800, max_h_limit)
            
            # Add padding for list borders/margins (12px top + 12px bottom = 24px) + safety
            new_h = base_h + list_h + extra_padding 
        else:
            self.divider.hide()
            self.list_widget.hide()
            new_h = 84 # Just the input height

        current_h = self.height()
        
        if current_h != new_h:
            # If we are already animating, force animation to continue to avoid snapping
            if self.anim.state() == QPropertyAnimation.State.Running:
                animate = True
            
            # Force animation when expanding from base state (e.g. first character typed)
            if current_h <= 84 and new_h > 84:
                animate = True

            self.anim.stop() # Always stop existing animation
            if animate:
                self.anim.setStartValue(self.geometry())
                self.anim.setEndValue(QRect(self.x(), self.y(), self.width(), new_h))
                self.anim.start()
            else:
                self.setGeometry(self.x(), self.y(), self.width(), new_h)

    def on_deactivate(self):
        # Called when window loses focus
        # On macOS, clicking outside the window (e.g. on desktop) triggers this.
        # We want to close the window, but ensure we switch back to IDLE mode.
        
        # Prevent closing if Mic is active (User is speaking)
        if self.mic_widget.active:
            logging.info("Window deactivated but Mic is active - keeping window open.")
            return
            
        # Prevent closing if TTS is playing (Assistant is speaking)
        if self.is_tts_playing:
             logging.info("Window deactivated but TTS is playing - keeping window open.")
             return
             
        # Prevent closing if AI is thinking
        if self.ai_worker and self.ai_worker.isRunning():
             logging.info("Window deactivated but AI is thinking - keeping window open.")
             return

        if self.isVisible() and not self.is_entry_animating:
            self.animate_close()

    def event(self, event):
        # Detect deactivation (focus loss)
        if event.type() == QEvent.Type.WindowDeactivate:
             self.on_deactivate()
        return super().event(event)

    def eventFilter(self, obj, event):
        if obj == self.input_field and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Down:
                current_row = self.list_widget.currentRow()
                if current_row < 0 and self.list_widget.count() > 0:
                    # No current selection, select first
                    self.list_widget.setCurrentRow(0)
                elif current_row < self.list_widget.count() - 1:
                    # Move to next
                    self.list_widget.setCurrentRow(current_row + 1)
                return True
            elif event.key() == Qt.Key.Key_Up:
                # Navigate up in list
                current_row = self.list_widget.currentRow()
                if current_row > 0:
                    self.list_widget.setCurrentRow(current_row - 1)
                return True
            elif event.key() == Qt.Key.Key_S and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                # CTRL+S for preview on currently selected file
                logging.debug("CTRL+S pressed - attempting preview")
                current_item = self.list_widget.currentItem()
                if current_item:
                    data = current_item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and data.get('type') == 'open_file':
                        logging.debug(f"Showing preview for {data['path']}")
                        self.show_file_preview(data['path'])
                        return True
                logging.debug("No file selected for preview")
                return False
            elif event.key() == Qt.Key.Key_Return:
                # ENTER on input field - trigger selected item or AI query
                current_item = self.list_widget.currentItem()
                if current_item:
                    self.on_entered(current_item)
                    return True
                else:
                    query = self.input_field.text().strip()
                    if query:
                        self.perform_ai_query(query)
                        return True
            elif event.key() == Qt.Key.Key_Escape:
                logging.info("Escape key pressed (Input Field)")
                
                # Check if we are streaming response
                if hasattr(self, 'ai_worker') and self.ai_worker and self.ai_worker.isRunning():
                    logging.info("Stopping AI stream...")
                    # Abort the worker
                    import src.services.llm.model_manager as mm
                    mm.abort_fast_event.set()
                    
                    # Force stop worker thread safely
                    try:
                        self.ai_worker.finished.disconnect()
                        self.ai_worker.partial_response.disconnect()
                    except: pass
                    self.ai_worker.terminate()
                    self.ai_worker.wait()
                    self.ai_worker = None
                    self.logo_label.stop_spinning()
                    return True

                if self.is_history_mode:
                    self.reset_to_search_mode()
                else:
                    self.animate_close()
                return True
            elif event.key() == Qt.Key.Key_Tab:
                if self.is_history_mode:
                    self.reset_to_search_mode(clear=False)
                else:
                    self.enter_history_mode()
                return True
        return super().eventFilter(obj, event)

    def enter_history_mode(self):
        if self.is_history_mode: return
        self.is_history_mode = True
        self.follow_up_widget.set_active(True)
        self.frame.set_minimal_mode(False) # Colorful mode for chat
        
        self._rebuild_history_list()

    def _rebuild_history_list(self):
        self.list_widget.clear()
        first = True
        
        # Iterate backwards to get Newest first
        for i in range(len(self.chat_history) - 1, -1, -1):
            msg = self.chat_history[i]
            if msg.get('role') == 'assistant':
                content = msg.get('content', '')
                user_query = ""
                if i > 0 and self.chat_history[i-1].get('role') == 'user':
                    user_query = self.chat_history[i-1].get('content', '')
                
                # Add Separator BEFORE adding the item (since we are building top-down with add_list_item)
                # Wait, add_list_item appends.
                # If we iterate backwards:
                # 1. Newest Answer (first loop)
                # 2. Separator
                # 3. Older Answer
                # This matches "Separator between followup answer and old answer"
                
                if not first:
                     self.add_list_item(SeparatorWidget(), "separator")

                w = AnswerWidget(content, query_text=user_query)
                w.set_query_visible(True)
                self.add_list_item(w, "history_ai")
                
                first = False
        
        self.adjust_window_height()

    def _rebuild_history_list(self):
        self.list_widget.clear()
        first = True
        
        # Iterate backwards to get Newest first
        for i in range(len(self.chat_history) - 1, -1, -1):
            msg = self.chat_history[i]
            if msg.get('role') == 'assistant':
                content = msg.get('content', '')
                user_query = ""
                if i > 0 and self.chat_history[i-1].get('role') == 'user':
                    user_query = self.chat_history[i-1].get('content', '')
                
                if not first:
                    self.add_list_item(SeparatorWidget(), "separator")
                
                w = AnswerWidget(content, query_text=user_query)
                w.set_query_visible(True)
                self.add_list_item(w, "history_ai")
                first = False
        
        self.adjust_window_height()

    def on_tts_finished(self):
        self.is_tts_playing = False
        logging.info("TTS Finished")

    def animate_close(self):
        if self._is_closing: return
        
        # Always switch back to IDLE (Wake Word) mode when closing
        self.send_udp_command("SET_MODE:IDLE")
        
        # Stop geometry animation if running
        if hasattr(self, 'anim') and self.anim.state() == QPropertyAnimation.State.Running:
            self.anim.stop()

        # Stop entry animation if running
        if hasattr(self, 'anim_group') and self.anim_group.state() == QPropertyAnimation.State.Running:
            self.anim_group.stop()
            
        self._is_closing = True
        
        self.anim_close_group = QParallelAnimationGroup()
        
        # Zoom Out
        current_geo = self.geometry()
        center = current_geo.center()
        
        target_w = int(current_geo.width() * 0.95)
        target_h = int(current_geo.height() * 0.95)
        target_x = center.x() - target_w // 2
        target_y = center.y() - target_h // 2
        
        target_geo = QRect(target_x, target_y, target_w, target_h)
        
        anim_geo = QPropertyAnimation(self, b"geometry")
        anim_geo.setDuration(200) # Fast exit
        anim_geo.setStartValue(current_geo)
        anim_geo.setEndValue(target_geo)
        anim_geo.setEasingCurve(QEasingCurve.Type.InCubic)
        
        # Opacity
        anim_opa = QPropertyAnimation(self, b"windowOpacity")
        anim_opa.setDuration(150) # Fade out very fast
        anim_opa.setStartValue(1.0)
        anim_opa.setEndValue(0.0)
        anim_opa.setEasingCurve(QEasingCurve.Type.OutQuad)
        
        def on_close_finished():
             self.hide()
             self._is_closing = False
             self.setWindowOpacity(1.0)
             # Reset geometry for next show so we don't shrink every time
             self.setGeometry(current_geo) 
             
             # Reset MacOS blur view reference so it gets recreated on next show
             if sys.platform == 'darwin' and hasattr(self, 'mac_blur_view'):
                 # Try to remove it from superview if possible, though close() might do it
                 try:
                     self.mac_blur_view.removeFromSuperview()
                 except: pass
                 del self.mac_blur_view
        
        self.anim_close_group.finished.connect(on_close_finished)
        self.anim_close_group.addAnimation(anim_geo)
        self.anim_close_group.addAnimation(anim_opa)
        self.anim_close_group.start()

    def keyPressEvent(self, event):
        # Handle CTRL+S for preview on selected file (backup handler)
        if event.key() == Qt.Key.Key_S and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            logging.debug("keyPressEvent: CTRL+S detected")
            current_item = self.list_widget.currentItem()
            if current_item:
                data = current_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get('type') == 'open_file':
                    self.show_file_preview(data['path'])
                    event.accept()
                    return
        
        # Arrow key navigation for list items (if list has focus or is selected)
        if event.key() == Qt.Key.Key_Down:
            current_item = self.list_widget.currentItem()
            if not current_item and self.list_widget.count() > 0:
                # Select first item if none selected
                self.list_widget.setCurrentRow(0)
            else:
                current_row = self.list_widget.row(current_item)
                if current_row < self.list_widget.count() - 1:
                    self.list_widget.setCurrentRow(current_row + 1)
            event.accept()
            return
        
        if event.key() == Qt.Key.Key_Up:
            current_item = self.list_widget.currentItem()
            if current_item:
                current_row = self.list_widget.row(current_item)
                if current_row > 0:
                    self.list_widget.setCurrentRow(current_row - 1)
            event.accept()
            return
        
        if event.key() == Qt.Key.Key_Escape:
            logging.info("Escape key pressed (Global)")
            if self.is_history_mode:
                self.reset_to_search_mode()
            else:
                self.animate_close()
        super().keyPressEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                # Window lost focus - close it to maintain state sync
                if self.isVisible() and not self._is_closing:
                    self.animate_close()
        super().changeEvent(event)

    def closeEvent(self, event):
        self._is_closing = False
        self.setWindowOpacity(1.0) # Reset for next show
        super().closeEvent(event)

    def on_text_changed(self, text):
        if self.is_history_mode: 
            # If user types in history mode, allow modification without resetting state
            # self.is_history_mode = False  <-- REMOVED
            # self.follow_up_widget.set_active(False) <-- REMOVED
            return
        
        if not text.strip():
            self.external_actions = []
            self.external_search_results = []
            self.local_file_results = []
            self.refresh_list("", animate=True)
            self.frame.set_minimal_mode(True)
            return

        # Query changed, clear external results until new ones arrive
        self.external_actions = []
        self.external_search_results = []
        self.local_file_results = []
        
        self.frame.set_minimal_mode(True)
        self.refresh_list(text)
        self.debounce_timer.start()

    def refresh_list(self, query, animate=True):
        if not query:
            self.list_widget.clear()
            self.external_actions = []
            self.external_search_results = []
            self.local_file_results = []
            self.adjust_window_height(animate)
            return

        # Calculate new items to display
        new_items_data = [] # List of (key, data, widget_factory_func)

        # 1. External Actions (from LLM/Fast Search)
        for act in self.external_actions:
            key = self.get_item_key(act)
            if not key: continue
            
            def create_act_widget(a=act):
                if a.get('type') == 'link':
                    return LinkActionWidget(a['title'], a['url'], a['description'])
                elif a.get('type') == 'install':
                    return InstallActionWidget(a['name'], a.get('website'))
                elif a.get('type') == 'open_app':
                    return AppActionWidget(a['name'])
                elif a.get('type') == 'person':
                    return PersonActionWidget(a['name'], a['description'], a.get('image'), a.get('url'))
                elif a.get('type') == 'place':
                    return PlaceActionWidget(a['name'], a['address'], a.get('image'), a.get('url'), a.get('latitude'), a.get('longitude'))
                elif a.get('type') == 'status':
                    return StandardItemWidget(a['description'], icon_name="dialog-information")
                return StandardItemWidget(str(a))
            
            new_items_data.append((key, act, create_act_widget))

        # 2. Local Apps (Fast)
        query_lower = query.lower()
        matches = []
        for name, data in self.apps.items():
            if query_lower in name:
                matches.append((name, data))
        
        # Sort matches: exact/prefix first
        matches.sort(key=lambda x: 0 if x[0].startswith(query_lower) else 1)
        
        for name, data in matches[:5]:
            key = f"app:{data['orig_name']}"
            
            # Capture variables properly in lambda
            def create_app_widget(d=data, n=name):
                icon_path = d.get('icon') or n
                return StandardItemWidget(d['orig_name'], icon_name=icon_path)
            
            new_items_data.append((key, data, create_app_widget))

        # 3. File Results (Local + External)
        # Combine lists, prioritizing local results. Deduplicate by path.
        seen_paths = set()
        all_file_results = self.local_file_results + self.external_search_results
        
        for res in all_file_results:
            if res.get('type') in ('file', 'folder'):
                if res['path'] in seen_paths: continue
                seen_paths.add(res['path'])

                # Handle both files and folders from file search worker
                data = {"type": "open_file", "path": res['path'], "name": res['name'], "is_dir": res.get('is_dir', False)}
                key = self.get_item_key(data)
                
                def create_file_widget(r=res):
                    return FileActionWidget(r['name'], r['path'])
                
                new_items_data.append((key, data, create_file_widget))

        # Always add "Ask Omni" option at the end if there is a query
        if query:
            key = "ask_omni"
            data = {"type": "ask_omni", "query": query}
            
            def create_omni_widget(q=query):
                return StandardItemWidget(f"Ask Omni: {q}", icon_name=LOGO_PATH)
            
            new_items_data.append((key, data, create_omni_widget))

        self.sync_list_items(new_items_data)
        self.adjust_window_height(animate)

    def get_item_key(self, data):
        if not isinstance(data, dict): return None
        if data.get('type') == 'ask_omni': return 'ask_omni'
        if 'orig_name' in data and 'cmd' in data: return f"app:{data['orig_name']}" # App
        if data.get('type') == 'open_file': return f"file:{data.get('path')}"
        if data.get('type') == 'link': return f"link:{data.get('url')}"
        # Fallback for others
        return str(data)

    def sync_list_items(self, new_items_data):
        # 1. Index existing items
        existing = {} 
        
        # Snapshot current state
        # We iterate backwards to safely identify removals, 
        # but for indexing we can just walk once.
        # However, multiple items might have same key? (Shouldn't happen with our logic)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            key = self.get_item_key(item.data(Qt.ItemDataRole.UserRole))
            if key:
                existing[key] = item

        # 2. Remove items not in new list
        new_keys = set(k for k, _, _ in new_items_data)
        
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            key = self.get_item_key(item.data(Qt.ItemDataRole.UserRole))
            if key not in new_keys:
                self.list_widget.takeItem(i)

        # 3. Align items with new order
        for i, (key, data, factory) in enumerate(new_items_data):
            # Check item at current position i
            current_item = self.list_widget.item(i)
            current_key = self.get_item_key(current_item.data(Qt.ItemDataRole.UserRole)) if current_item else None
            
            if current_key == key:
                # MATCH: Update content if needed
                widget_container = self.list_widget.itemWidget(current_item)
                if isinstance(widget_container, SmoothEntryWidget):
                    real_widget = widget_container.content_widget
                    if hasattr(real_widget, 'set_text'):
                        # Specific logic for Ask Omni text update
                        if key == "ask_omni":
                            real_widget.set_text(f"Ask Omni: {data['query']}")
                        # Apps usually don't change text
                
                # Update data just in case
                current_item.setData(Qt.ItemDataRole.UserRole, data)
                
            else:
                # MISMATCH
                if key in existing:
                    # Exists elsewhere: Move it here (Slide effect by skipping animation)
                    old_item = existing[key]
                    row = self.list_widget.row(old_item)
                    self.list_widget.takeItem(row) # Remove from old pos
                    
                    # Re-insert at i
                    new_item = QListWidgetItem()
                    widget = factory() # Recreate widget
                    if hasattr(widget, 'set_theme'):
                        widget.set_theme(self.current_theme)
                    new_item.setSizeHint(widget.sizeHint())
                    new_item.setData(Qt.ItemDataRole.UserRole, data)
                    
                    self.list_widget.insertItem(i, new_item)
                    
                    # Wrap with NO animation
                    anim_w = SmoothEntryWidget(widget, animate=False)
                    self.list_widget.setItemWidget(new_item, anim_w)
                    
                    # Update map for future lookups in this loop?
                    # No need, we are linear scan.
                    
                else:
                    # New Item: Insert with Animation
                    new_item = QListWidgetItem()
                    widget = factory()
                    if hasattr(widget, 'set_theme'):
                        widget.set_theme(self.current_theme)
                    new_item.setSizeHint(widget.sizeHint())
                    new_item.setData(Qt.ItemDataRole.UserRole, data)
                    
                    self.list_widget.insertItem(i, new_item)
                    
                    anim_w = SmoothEntryWidget(widget, animate=True)
                    self.list_widget.setItemWidget(new_item, anim_w)

    def add_list_item(self, widget, data):
        if hasattr(widget, 'set_theme'):
            widget.set_theme(self.current_theme)
        item = QListWidgetItem()
        item.setSizeHint(widget.sizeHint())
        item.setData(Qt.ItemDataRole.UserRole, data)
        
        # Disable selection for thinking, answer, separator items
        if isinstance(data, str) and data in ["thinking", "answer", "separator", "history_ai"]:
             item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
             
        self.list_widget.addItem(item)
        
        # Wrap in SmoothEntryWidget for fade-in
        anim_w = SmoothEntryWidget(widget)
        self.list_widget.setItemWidget(item, anim_w)

    def insert_list_item(self, index, widget, data):
        if hasattr(widget, 'set_theme'):
            widget.set_theme(self.current_theme)
        item = QListWidgetItem()
        item.setSizeHint(widget.sizeHint())
        item.setData(Qt.ItemDataRole.UserRole, data)
        
        # Disable selection for thinking, answer, separator items
        if isinstance(data, str) and data in ["thinking", "answer", "separator", "history_ai"]:
             item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
             
        self.list_widget.insertItem(index, item)
        
        # Wrap in SmoothEntryWidget for fade-in
        anim_w = SmoothEntryWidget(widget)
        self.list_widget.setItemWidget(item, anim_w)

    def cleanup_worker(self, attr_name):
        """Safely cleanup a worker thread by keeping a reference if it's still running."""
        worker = getattr(self, attr_name, None)
        if worker:
            # Disconnect all signals to avoid side effects
            try: worker.disconnect()
            except: pass
            
            if worker.isRunning():
                self.old_workers.append(worker)
                # Connect finished signal to remove from old_workers list
                # Use default arg to capture 'worker' variable
                worker.finished.connect(lambda w=worker: self.old_workers.remove(w) if w in self.old_workers else None)
            
            setattr(self, attr_name, None)

    def trigger_async_searches(self):
        query = self.input_field.text().strip()
        if not query or self.is_history_mode: return

        # Start Search Worker
        self.cleanup_worker('search_worker')
        self.search_worker = SearchWorker(query)
        self.search_worker.results_found.connect(self.on_search_results)
        self.search_worker.start()

        # Start Action Worker
        self.cleanup_worker('action_worker')
        self.action_worker = ActionWorker(query)
        self.action_worker.action_found.connect(self.on_action_found)
        self.action_worker.start()
        
        # Start File Search Worker (NEW) - OPTIMIZED FOR SPEED
        self.cleanup_worker('file_search_worker')
        self.file_search_worker = FileSearchWorker(query, max_results=8)  # Reduced for speed
        self.file_search_worker.results_found.connect(self.on_file_search_results)
        self.file_search_worker.start()

    def on_search_results(self, results, query):
        if self.input_field.text().strip() != query: return
        
        self.external_search_results = results
        self.refresh_list(query, animate=False)

    def on_action_found(self, actions, query):
        if self.input_field.text().strip() != query: return
        
        self.external_actions = actions
        self.refresh_list(query, animate=False)
    
    def on_file_search_results(self, results, query):
        """Handle file search results from the file search worker."""
        if self.input_field.text().strip() != query: return
        
        # Store in separate list to avoid overwrite by slow search_worker
        self.local_file_results = results
        self.refresh_list(query, animate=False)

    def on_entered(self, item=None):
        self.voice_triggered_query = False
        if not item:
            item = self.list_widget.currentItem()
        
        # If no item selected (Enter in box), use text
        if not item:
            query = self.input_field.text().strip()
            if query:
                self.perform_ai_query(query)
            return

        data = item.data(Qt.ItemDataRole.UserRole)
        
        if isinstance(data, dict):
            if data.get('type') == 'link':
                QDesktopServices.openUrl(QUrl(data['url']))
                self.animate_close()
            elif data.get('type') == 'install':
                self.start_install(data['name'])
            elif data.get('type') == 'open_app':
                # Launch App (from Regex Shortcut)
                app_name = data.get('name')
                success, msg = find_and_launch_app(app_name)
                # We could show status, but typically we close or show notification
                self.animate_close()
            elif data.get('type') == 'open_file':
                # Open file in default application
                import subprocess
                try:
                    file_path = data['path']
                    import platform
                    if platform.system() == 'Windows':
                        # Windows: Use os.startfile
                        import os
                        os.startfile(file_path)
                    elif platform.system() == 'Darwin':
                        # macOS: Use open command
                        subprocess.Popen(['open', file_path])
                    else:
                        # Linux: Use xdg-open
                        subprocess.Popen(['xdg-open', file_path])
                    
                    # Close after a small delay to ensure file opens
                    QTimer.singleShot(500, self.animate_close)
                except Exception as e:
                    logging.error(f"Failed to open file {data['path']}: {e}")
                    self.animate_close()
            elif 'cmd' in data: # App from cache
                try:
                    subprocess.Popen(data['cmd'], shell=True, start_new_session=True)
                    self.animate_close()
                except: pass
            elif data.get('type') == 'ask_omni':
                self.perform_ai_query(data['query'])
        else:
            # Fallback
            query = self.input_field.text().strip()
            if query: self.perform_ai_query(query)
    
    def show_file_preview(self, file_path):
        """Toggle preview expansion for the selected file."""
        try:
            current_item = self.list_widget.currentItem()
            if not current_item:
                return
            
            widget_container = self.list_widget.itemWidget(current_item)
            if not widget_container:
                return
            
            # Extract the actual FileActionWidget
            if hasattr(widget_container, 'content_widget'):
                file_widget = widget_container.content_widget
            else:
                file_widget = widget_container
            
            # Toggle preview expansion
            if hasattr(file_widget, 'peek_label'):
                is_hidden = file_widget.peek_label.isHidden()
                
                if is_hidden:
                    # Expand: load and show preview
                    if hasattr(file_widget, 'get_file_preview'):
                        preview_content = file_widget.get_file_preview()
                        file_widget.peek_label.setText(preview_content)
                    file_widget.peek_label.setHidden(False)
                else:
                    # Collapse: hide preview
                    file_widget.peek_label.setHidden(True)
                
                # Adjust window height
                self.adjust_window_height(animate=False)
        except Exception as e:
            logging.error(f"Error toggling preview: {e}")

    def start_install(self, app_name):
        self.list_widget.clear()
        self.input_field.setDisabled(True)
        self.input_field.setText(f"Installing {app_name}...")
        
        # Add Progress Widget
        self.install_widget = InstallProgressWidget(app_name)
        self.install_widget.candidate_confirmed.connect(self.on_install_candidate_confirmed)
        
        item = QListWidgetItem()
        item.setSizeHint(self.install_widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, self.install_widget)
        self.adjust_window_height()
        
        # Start Orchestrator
        self.install_worker = InstallOrchestrator(app_name)
        self.install_worker.status_update.connect(self.install_widget.update_status)
        self.install_worker.log_entry.connect(self.install_widget.add_log)
        self.install_worker.finished.connect(self.install_widget.set_finished)
        self.install_worker.candidates_found.connect(self.install_widget.show_candidates)
        self.install_worker.start()

    def on_install_candidate_confirmed(self, pkg_data):
        # Restart orchestrator with forced package
        self.install_worker = InstallOrchestrator(pkg_data['name'], forced_package=pkg_data)
        self.install_worker.status_update.connect(self.install_widget.update_status)
        self.install_worker.log_entry.connect(self.install_widget.add_log)
        self.install_worker.finished.connect(self.install_widget.set_finished)
        self.install_worker.start()

    def perform_ai_query(self, query):
        # Stop debounce timer to prevent new fast searches from starting
        self.debounce_timer.stop()

        # Cancel any pending fast search/action requests to prevent race conditions and save resources
        self.cleanup_worker('search_worker')
        self.cleanup_worker('action_worker')
        self.cleanup_worker('file_search_worker')
        
        # Signal workers to abort if they support it
        import src.services.llm.model_manager as mm
        mm.abort_fast_event.set()

        is_followup = self.is_history_mode
        
        if not is_followup:
            self.list_widget.clear()
        
        self.frame.set_minimal_mode(False) # Active mode
        self.logo_label.boost_speed()
        
        # Add Thinking Widget
        # Only show query text if it's a followup
        thinking_text = query if is_followup else ""
        self.thinking_widget = ThinkingWidget(thinking_text)
        
        # Initialize streaming TTS state
        self.tts_buffer = ""
        self.tts_spoken_len = 0
        
        if is_followup:
            # For followup thinking, we want:
            # [Thinking Widget]
            # [Separator]
            # [Old Answer]
            # So insert Separator at 0, then Thinking at 0 (pushing Separator to 1).
            
            if self.list_widget.count() > 0:
                self.insert_list_item(0, SeparatorWidget(), "separator")
                
            self.insert_list_item(0, self.thinking_widget, "thinking")
            
            # Ensure we scroll to top to see the new thinking widget
            if self.list_widget.count() > 0:
                self.list_widget.scrollToItem(self.list_widget.item(0))
        else:
            self.add_list_item(self.thinking_widget, "thinking")
            # No separator for normal query

        self.adjust_window_height()
        
        # Screenshot?
        screenshot_b64 = None
        # Logic to decide if we need screenshot is now in AIWorker or Brain
        # But if we want to send it, we need to take it here.
        # Let's take it if query implies it, OR always?
        # Taking screenshot is expensive?
        # Let's try taking it if "screen" keyword or similar is in query
        if any(x in query.lower() for x in ["screen", "look", "see", "window", "display"]):
            self.screenshot_worker = ScreenshotWorker()
            self.screenshot_worker.finished.connect(lambda b64: self.start_ai_worker(query, b64))
            self.screenshot_worker.failed.connect(lambda: self.start_ai_worker(query, None))
            
            # Add a timeout timer to handle stuck screenshot operations (5 seconds max)
            self.screenshot_timeout_timer = QTimer()
            self.screenshot_timeout_timer.setSingleShot(True)
            self.screenshot_timeout_timer.setInterval(5000)  # 5 seconds
            self.screenshot_timeout_timer.timeout.connect(lambda: self._handle_screenshot_timeout())
            self.screenshot_timeout_timer.start()
            
            self.screenshot_worker.start()
            logging.info("Screenshot worker started with 5-second timeout")
        else:
            self.start_ai_worker(query, None)
    
    def _handle_screenshot_timeout(self):
        """Handle screenshot operation timeout."""
        if self.screenshot_worker and self.screenshot_worker.isRunning():
            logging.warning("Screenshot worker timeout - forcing fallback without screenshot")
            # Disconnect signals to prevent duplicate processing
            try:
                self.screenshot_worker.finished.disconnect()
                self.screenshot_worker.failed.disconnect()
            except:
                pass
            # Proceed without screenshot
            self.start_ai_worker(self.input_field.text(), None)

    def start_ai_worker(self, query, screenshot_b64):
        self.cleanup_worker('ai_worker')
        self.ai_worker = AIWorker(query, self.chat_history, screenshot_b64)
        self.ai_worker.finished.connect(self.on_ai_response)
        self.ai_worker.partial_response.connect(self.on_partial_response)
        self.ai_worker.start()



    def _unwrap_answer_widget(self, item):
        if not item: return None
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, SmoothEntryWidget):
            return widget.content_widget
        return widget

    def on_partial_response(self, data):
        """Handle partial streaming: show thinking in collapsible (gray), answer in main. Collapse thinking when answer starts."""
        self.logo_label.stop_spinning()

        thinking = data.get("thinking", "")
        answer = data.get("answer", "")
        
        # Streaming TTS Logic
        if self.voice_triggered_query and answer:
            if not self.tts_worker or not self.tts_worker.isRunning():
                self.cleanup_worker('tts_worker')
                self.tts_worker = TTSWorker() # Initialize empty streaming worker
                self.tts_worker.finished_speaking.connect(self.on_tts_finished)
                self.tts_worker.start()
                self.is_tts_playing = True
            
            # Get new content
            if len(answer) > self.tts_spoken_len:
                new_content = answer[self.tts_spoken_len:]
                self.tts_buffer += new_content
                self.tts_spoken_len = len(answer)
                
                # Check for sentence boundaries
                # Split by punctuation (. ! ?) followed by space or newline
                # We use regex capture group to keep the delimiter
                import re
                
                # More robust splitting to catch "Hello, how are you?" etc.
                # Don't split on comma alone as it breaks flow too much, but .!? is good.
                # User wants faster TTS start, so we include comma/colon/semicolon too.
                parts = re.split(r'([.,!?;:]+[\s\n]+)', self.tts_buffer)
                
                # If we have at least one delimiter, we can process the sentence
                # We need [Sentence, Delimiter, NextPart...]
                
                while len(parts) >= 2:
                    segment = parts.pop(0)
                    delimiter = parts.pop(0)
                    
                    full_sentence = segment + delimiter
                    
                    # Ignore short fragments that might be artifacts
                    if len(full_sentence.strip()) > 2:
                        self.tts_worker.add_text(full_sentence)
                
                # Keep the rest in buffer
                self.tts_buffer = "".join(parts)

        # Find existing answer widget or create one
        answer_widget = None
        answer_item = None
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "answer":
                answer_item = item
                answer_widget = self._unwrap_answer_widget(item)
                break

        if answer_widget is None:
            # First partial: Remove "Thinking..." widget first
            # We need to find the thinking widget index, and see if there is a separator below it.
            thinking_idx = -1
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == "thinking":
                    thinking_idx = i
                    break
            
            if thinking_idx != -1:
                # Check if next item is separator (thinking is at top/0 usually, so separator at 1)
                # First remove separator if it exists below thinking
                if thinking_idx + 1 < self.list_widget.count():
                    next_item = self.list_widget.item(thinking_idx + 1)
                    if next_item.data(Qt.ItemDataRole.UserRole) == "separator":
                        self.list_widget.takeItem(thinking_idx + 1)
                
                # Then remove thinking
                self.list_widget.takeItem(thinking_idx)

            # Create widget WITHOUT thinking in constructor, add it dynamically
            prepend = self.is_history_mode
            if prepend and self.list_widget.count() > 0:
                self.insert_list_item(0, SeparatorWidget(), "separator")

            current_query = self.input_field.text()
            # Create answer widget with empty answer and NO thinking_text (we'll add thinking dynamically)
            answer_widget = AnswerWidget("", query_text=current_query, thinking_text="")
            
            # Now add/update thinking dynamically
            if thinking:
                answer_widget.ensure_thinking_widget()
                answer_widget.update_thinking(thinking)
                # Keep thinking expanded while we're still thinking (no answer yet)
                answer_widget.set_thinking_collapsed(False)
            
            if prepend:
                answer_widget.set_query_visible(True)
                self.insert_list_item(0, answer_widget, "answer")
            else:
                self.add_list_item(answer_widget, "answer")
        else:
            # Update existing: stream thinking in collapsible, answer in main
            if thinking:
                answer_widget.ensure_thinking_widget()
                answer_widget.update_thinking(thinking)
            
            if answer:
                # When answer appears, collapse thinking and show answer in main text
                if thinking:
                    answer_widget.set_thinking_collapsed(True)
                if hasattr(answer_widget, 'text_edit'):
                    answer_widget.text_edit.setMarkdown(answer)
            # Don't force expand/collapse while streaming - let user control it or auto-expand only once on first update
            
            answer_widget.updateGeometry()
            if answer_item is not None:
                answer_item.setSizeHint(answer_widget.sizeHint())
                self.adjust_window_height(animate=False)

        self.list_widget.update()
        if hasattr(self, "adjust_window_height"):
            self.adjust_window_height(animate=False)

    def on_ai_response(self, data):
        self.logo_label.stop_spinning()

        # Check if we already have a streaming answer widget
        has_streaming_answer = False
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "answer":
                widget = self._unwrap_answer_widget(item)
                if widget and hasattr(widget, 'text_edit'):
                    # Update the existing streaming widget with final data
                    answer = data.get("answer", "")
                    thinking = data.get("thinking", "")
                    actions = data.get("actions", [])
                    
                    # Update thinking and answer
                    if thinking:
                        widget.ensure_thinking_widget()
                        widget.update_thinking(thinking)
                        widget.set_thinking_collapsed(True)  # Collapse thinking after final response
                    
                    widget.text_edit.setMarkdown(answer)

                    # Add any final actions that weren't added during streaming
                    if actions:
                        # Find where to insert actions (after the answer widget)
                        insert_pos = i + 1
                        for act in actions:
                            if isinstance(act, dict):
                                if act.get('type') == 'link':
                                    w = LinkActionWidget(act['title'], act['url'], act['description'])
                                    self.insert_list_item(insert_pos, w, act)
                                    insert_pos += 1
                                elif act.get('type') == 'install':
                                    w = InstallActionWidget(act['name'], act.get('website'))
                                    self.insert_list_item(insert_pos, w, act)
                                    insert_pos += 1
                                elif act.get('type') == 'status':
                                    w = StandardItemWidget(act['description'], icon_name="dialog-information")
                                    self.insert_list_item(insert_pos, w, act)
                                    insert_pos += 1

                    # Recalculate sizes after all updates
                    widget.updateGeometry()
                    # Force recalculation of sizeHint to get accurate collapsed/expanded height
                    new_size = widget.sizeHint()
                    item.setSizeHint(new_size)
                    
                    self.list_widget.update()
                    self.adjust_window_height(animate=False)
                    has_streaming_answer = True
                    break

        answer = data.get("answer", "")
        
        # TRIGGER TTS IF VOICE QUERY (Finalize Streaming)
        if self.voice_triggered_query:
            logging.info("Finalizing TTS for voice query response")
            
            # Send any remaining buffer
            if hasattr(self, 'tts_buffer') and self.tts_buffer.strip():
                 if not self.tts_worker or not self.tts_worker.isRunning():
                     # If it was a short response that came all at once (no partials triggered worker)
                     self.cleanup_worker('tts_worker')
                     self.tts_worker = TTSWorker(self.tts_buffer)
                     self.tts_worker.finished_speaking.connect(self.on_tts_finished)
                     self.tts_worker.start()
                     self.is_tts_playing = True
                 else:
                     self.tts_worker.add_text(self.tts_buffer)
            
            # Signal worker to stop when done with queue
            if self.tts_worker:
                 self.tts_worker.stop()
            
            # Reset flag
            self.voice_triggered_query = False

        if has_streaming_answer:
            return  # Already handled the response via streaming
        
        # Remove thinking widget and separator (iterate backwards)
        # Note: In perform_ai_query we removed the separator for followup, 
        # so there might not be one if it was a followup query.
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            role = item.data(Qt.ItemDataRole.UserRole)
            if role in ["thinking", "separator"]:
                # Only remove if it's the "thinking" separator, not a history separator
                # We identify thinking separator by checking if it's adjacent to thinking widget?
                # Or just rely on the role. We added it as "separator" in perform_ai_query.
                # But wait, we add "separator" between history items too.
                # We need to distinguish them.
                # In perform_ai_query, we only added thinking widget for followup. No separator.
                # So for followup, we only remove thinking.
                # For normal query, we add thinking (and maybe separator if we kept it? No we removed it in prev turn).
                # Wait, looking at perform_ai_query:
                # if is_followup: insert(thinking); NO SEPARATOR.
                # else: add(thinking); NO SEPARATOR.
                # So we only need to remove "thinking".
                # BUT, if there was a separator left over from previous logic bugs?
                # Let's be safe: "thinking" role is unique.
                # "separator" role is used for history separators too.
                # So we should ONLY remove "thinking" items here.
                pass

        # Updated cleanup: Remove 'thinking' AND the specific 'separator' added during thinking phase
        # The 'separator' added during perform_ai_query for followup is just below 'thinking'.
        # We need to remove it too, because we are about to re-add a separator in the correct place 
        # (between new answer and old answer).
        # Actually, we can just REUSE it if it exists, or remove and re-add. 
        # Simpler to remove and re-add to be consistent.
        
        # We need to find the thinking widget index, and see if there is a separator below it.
        thinking_idx = -1
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "thinking":
                thinking_idx = i
                break
        
        if thinking_idx != -1:
            # Check if next item is separator (thinking is at top/0 usually, so separator at 1)
            # But we iterate backwards to remove safely.
            
            # First remove separator if it exists below thinking
            if thinking_idx + 1 < self.list_widget.count():
                next_item = self.list_widget.item(thinking_idx + 1)
                if next_item.data(Qt.ItemDataRole.UserRole) == "separator":
                    self.list_widget.takeItem(thinking_idx + 1)
            
            # Then remove thinking
            self.list_widget.takeItem(thinking_idx)
            
        # Fallback cleanup for any other thinking widgets
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            role = item.data(Qt.ItemDataRole.UserRole)
            if role == "thinking":
                 self.list_widget.takeItem(i)

        answer = data.get("answer", "")
        actions = data.get("actions", [])
        thinking = data.get("thinking", "")
        special = data.get("special_action")

        if special == "screenshot_required":
            # Retry with screenshot, with timeout + failure fallback
            original_query = self.input_field.text()

            self.screenshot_worker = ScreenshotWorker()
            # On success, proceed with screenshot and stop any timeout timer
            def _on_screenshot_finished(b64):
                if hasattr(self, "screenshot_timeout_timer") and self.screenshot_timeout_timer:
                    self.screenshot_timeout_timer.stop()
                self.start_ai_worker(original_query, b64)

            # On failure, proceed without screenshot
            def _on_screenshot_failed():
                if hasattr(self, "screenshot_timeout_timer") and self.screenshot_timeout_timer:
                    self.screenshot_timeout_timer.stop()
                logging.warning("Screenshot worker failed after screenshot_required – falling back without screenshot")
                self.start_ai_worker(original_query, None)

            self.screenshot_worker.finished.connect(_on_screenshot_finished)
            self.screenshot_worker.failed.connect(_on_screenshot_failed)

            # Add a timeout timer to handle stuck screenshot operations (5 seconds max)
            self.screenshot_timeout_timer = QTimer()
            self.screenshot_timeout_timer.setSingleShot(True)
            self.screenshot_timeout_timer.setInterval(5000)  # 5 seconds
            self.screenshot_timeout_timer.timeout.connect(lambda: self._handle_screenshot_timeout())
            self.screenshot_timeout_timer.start()

            logging.info("Screenshot requested by backend (screenshot_required); worker started with 5-second timeout")
            self.screenshot_worker.start()
            return

        # Determine insertion method (Append vs Prepend)
        # If we are already in history mode (before this response), we prepend to top.
        # If this is the first response, we append (or prepend to empty list, same thing).
        # Actually, if is_history_mode is True, we definitely prepend.
        # If it is False, list was cleared, so appending is fine.
        
        prepend = self.is_history_mode
        insert_idx = 0

        # Helper to add item based on mode
        def add_item(w, d):
            nonlocal insert_idx
            if prepend:
                self.insert_list_item(insert_idx, w, d)
                insert_idx += 1
            else:
                self.add_list_item(w, d)

        # Update visibility of existing items if we are prepending (entering history)
        if prepend:
            # We are adding a new answer.
            # Existing items (the old answer) should now show their query labels if they weren't already.
            # Also, we need to insert a separator before the old answer (which is currently at index 0, before we insert new stuff).
            
            # Step 1: Insert Separator at top (pushing old answer down)
            # Only if there is an old answer
            # We want separator between NEW and OLD. 
            # So if we insert new at 0, the separator should be at 1.
            # But we are inserting separator NOW, before adding new item.
            # So separator goes to index 0, pushing old items to 1.
            # Then we insert new item at 0, pushing separator to 1, old items to 2.
            
            if self.list_widget.count() > 0:
                self.insert_list_item(0, SeparatorWidget(), "separator")
                # Do NOT increment insert_idx.
                # We want the NEW answer (and subsequent actions) to be inserted AT 0,
                # pushing the separator down to 1 (and old items to 2+).
                # This ensures: [New Answer] [Separator] [Old Answer]
                # insert_idx += 1  <-- REMOVED
            
            # Step 2: Iterate all existing items and set query visible
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                w = self.list_widget.itemWidget(item)
                if isinstance(w, SmoothEntryWidget):
                    w = w.content_widget
                if isinstance(w, AnswerWidget):
                    w.set_query_visible(True)

        # Add Answer
        if answer:
            self.chat_history.append({"role": "user", "content": self.input_field.text()})
            self.chat_history.append({"role": "assistant", "content": answer})
            
            # Pass query text to AnswerWidget
            # If prepend (follow-up), we show it.
            # If not prepend (first answer), we hide it (default is hidden in AnswerWidget, but we pass text).
            # Wait, user said "they are not shown if it's not followup!".
            # So if not prepend, visible=False.
            # If prepend, visible=True.
            
            current_query = self.input_field.text()
            w = AnswerWidget(answer, query_text=current_query, thinking_text=thinking)
            if prepend:
                w.set_query_visible(True)
            
            add_item(w, "answer")
        
        # Add Actions
        for act in actions:
            if isinstance(act, dict):
                if act.get('type') == 'link':
                    w = LinkActionWidget(act['title'], act['url'], act['description'])
                    add_item(w, act)
                elif act.get('type') == 'install':
                    w = InstallActionWidget(act['name'], act.get('website'))
                    add_item(w, act)
                elif act.get('type') == 'status':
                    # Maybe just a small notification or standard item
                    w = StandardItemWidget(act['description'], icon_name="dialog-information")
                    add_item(w, act)
        
        # Scroll to top if we prepended
        if prepend and self.list_widget.count() > 0:
            self.list_widget.scrollToItem(self.list_widget.item(0))

        # Manually set history mode state without re-clearing list
        if not self.is_history_mode:
            self.is_history_mode = True
            self.follow_up_widget.set_active(True)
            self.frame.set_minimal_mode(False)
            
        self.adjust_window_height()
