import os
import re
import sys
import logging
import subprocess
import json
import time
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QFrame, QAbstractItemView,
                             QGraphicsDropShadowEffect, QLabel, QScrollArea, QProgressBar, QMessageBox, QGraphicsOpacityEffect,
                             QPushButton)
from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect, QRectF, QEvent, QUrl, QParallelAnimationGroup, pyqtProperty, pyqtSignal, QThreadPool
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QPainterPath, QBrush, QLinearGradient, QDesktopServices, QCursor, QGuiApplication, QFontDatabase, QPen, QBitmap

from src.core.config import LOGO_PATH
from src.ui.styles import get_style_sheet, THEMES
from src.core.ipc import start_ipc_listener
from src.services.system.app_launcher import get_app_cache

from src.ui.widgets.action_widgets import (LinkActionWidget, InstallActionWidget, UninstallActionWidget, FileActionWidget, PersonActionWidget, PlaceActionWidget, AppActionWidget, CalcActionWidget, SettingsActionWidget, SettingsAnimationWidget, TerminalActionWidget, OGPreviewWidget, QuickURLWidget,SearchActionWidget, MapNavigationWidget, TranslateActionWidget, CurrencyActionWidget, WeatherActionWidget, UnitActionWidget, ColorActionWidget, TimerActionWidget, PasswordActionWidget, QRActionWidget, PendingActionWidget, OptimizeSystemWidget, WorldTimeWidget, CalendarActionWidget, EmailActionWidget, AnswerActionWidget, SendEmailWidget, ToolDraftWidget)
from src.ui.widgets.install_widget import InstallProgressWidget, UninstallProgressWidget
from src.ui.widgets.command_widget import CommandLogWidget
import socket
from src.ui.widgets.misc_widgets import (ThinkingWidget, SeparatorWidget, SmoothEntryWidget, FollowUpWidget, AnswerWidget, StandardItemWidget, RotatingLabel, GradientBorderFrame, ReplyActionWidget, IconManager, MicWidget, TrustPermissionChatWidget, CommandPaletteItemWidget)
from src.ui.widgets.list_widget import SmoothScrollListWidget
from src.ui.widgets.settings_panel import SettingsPanel

from src.ui.workers.ai_worker import AIWorker
from src.ui.workers.search_worker import SearchWorker
from src.ui.workers.action_worker import ActionWorker, PlaceResolverWorker
from src.ui.workers.screenshot_worker import ScreenshotWorker
from src.ui.workers.install_worker import InstallOrchestrator, InstallWorker, UninstallOrchestrator
from src.ui.workers.file_search_worker import FileSearchWorker
from src.ui.workers.tts_worker import TTSWorker
from src.ui.workers.og_worker import OGWorker
from src.ui.workers.computer_control_worker import ComputerControlWorker
from src.ui.widgets.trust_permission_popup import TrustPermissionPopup
from src.ui.widgets.clipboard_widget import ClipboardItemWidget
from src.ui.clipboard_manager import ClipboardManager
import src.core.settings_store as settings_store

# ---------------------------------------------------------------------------
# Instant math-expression evaluator (client-side, zero latency)
# ---------------------------------------------------------------------------
import math as _math

_CALC_EXPR_RE = re.compile(r'^[\d\s\.\(\)\+\-\*\/\^\%]+$')
_SAFE_MATH_NS = {"__builtins__": {}, **{k: v for k, v in _math.__dict__.items() if not k.startswith('_')}}

def _detect_calc(text: str):
    """Return (result_str, equation_str) if *text* is a pure math expression, else None."""
    t = text.strip()
    if not t or (' ' not in t and len(t) < 2):
        return None
    if not re.search(r'\d', t):
        return None
    if not re.search(r'[\+\-\*\/\^\%]', t):
        return None
        
    # Treat comma as a decimal separator
    t_norm = t.replace(',', '.')
    
    if not _CALC_EXPR_RE.match(t_norm):
        return None
    try:
        result = eval(t_norm.replace('^', '**'), _SAFE_MATH_NS, {})  # nosec – fully sandboxed
        if not isinstance(result, (int, float)):
            return None
        if isinstance(result, float) and (result != result or result in (float('inf'), float('-inf'))):
            return None
        if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            val_str = str(int(result))
        else:
            val_str = f"{result:.10g}"
            
        # Optional: return value formatted with comma if input used comma? 
        # For uniformity, returning dot is usually fine, or standard format.
        # Let's just return the standard format computed above.
        return (val_str, f"{t} = {val_str}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# URL / domain instant-detection helper
# ---------------------------------------------------------------------------
_INSTANT_URL_RE = re.compile(
    r'^(?:https?://\S+|'
    r'(?:www\.)?[a-zA-Z0-9][a-zA-Z0-9\-]*'
    r'(?:\.[a-zA-Z0-9][a-zA-Z0-9\-]*)*'
    r'\.[a-zA-Z]{2,6}'
    r'(?:/\S*)?)$',
    re.IGNORECASE,
)
# Extensions that look like domains but are actually filenames
_FILE_EXT_RE = re.compile(
    r'\.(js|ts|py|css|html|jsx|tsx|md|txt|json|xml|yml|yaml|sh|env|svg|png|jpg|gif|mp4)$',
    re.IGNORECASE,
)


def _detect_url(text: str):
    """
    Return (normalised_url, domain) if *text* looks like a URL/domain, else None.
    Requires a proper TLD — rejects bare words like "tesla" or filenames like "app.js".
    """
    t = text.strip()
    if not t or ' ' in t:
        return None
    if _FILE_EXT_RE.search(t):
        return None
    if not _INSTANT_URL_RE.match(t):
        return None
    url = t if t.startswith('http') else f'https://{t}'
    try:
        from urllib.parse import urlparse as _up
        domain = _up(url).netloc.replace('www.', '')
        return (url, domain) if domain and '.' in domain else None
    except Exception:
        return None


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
    toggle_clipboard_requested = pyqtSignal()
    clipboard_mode_shortcut_requested = pyqtSignal()  # Cmd+4 while window visible
    _tool_draft_done_signal = pyqtSignal(object, str, bool)  # (widget, result, success)

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
        self.toggle_clipboard_requested.connect(self.toggle_clipboard)
        self.clipboard_mode_shortcut_requested.connect(self._on_clipboard_shortcut)
        self._tool_draft_done_signal.connect(self._on_tool_draft_done)
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("omni-search")
        self.setWindowIcon(QIcon(LOGO_PATH))
        self.resize(DEFAULT_WIDTH, 160)
        
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(450)
        self.anim.setEasingCurve(QEasingCurve.Type.OutExpo)
        
        self.setup_uinput()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.frame = GradientBorderFrame()
        self.frame.setObjectName("MainFrame")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        
        self._is_closing = False 

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        self.content_frame = QWidget()
        self.content_frame.setObjectName("ContentFrame")
        self.content_frame.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        frame_layout.addWidget(self.content_frame)

        self.input_container = QWidget()
        self.input_container.setFixedHeight(84) # Increased height to prevent clipping
        self.input_container.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.input_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        input_layout = QHBoxLayout(self.input_container)
        input_layout.setContentsMargins(18, 4, 10, 4)
        input_layout.setSpacing(12)

        self.logo_label = RotatingLabel()
        self.logo_label.setFixedSize(50, 50)
        self.logo_label.right_clicked.connect(self.enter_settings_mode)
        logo_pix = QPixmap(LOGO_PATH)
        if not logo_pix.isNull():
            self.logo_label.setPixmap(logo_pix.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        self.input_field = QLineEdit()
        self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        # Force no border/outline via style sheet and properties to kill blue ring
        self.input_field.setStyleSheet("""
            QLineEdit {
                border: 0px solid transparent;
                outline: none;
                background: transparent;
                margin: 0px;
                padding: 0px;
            }
            QLineEdit:focus {
                border: none;
                outline: none;
                background: transparent;
            }
        """)
        self.input_field.setFrame(False) # Important for Qt widgets to remove native frame
        self.input_field.setMaxLength(750)
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

        # Settings mode: title + close button (hidden in normal mode)
        self.settings_title = QLabel("Settings")
        self.settings_title.setFont(QFont("Instrument Serif", 34))
        self.settings_title.setStyleSheet("font-style: italic; color: rgba(255,255,255,0.6);")
        self.settings_title.hide()

        self.settings_close_btn = QPushButton("×")
        self.settings_close_btn.setFixedSize(36, 36)
        self.settings_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_close_btn.setObjectName("SettingsCloseBtn")
        self.settings_close_btn.clicked.connect(self.exit_settings_mode)
        self.settings_close_btn.hide()

        # Clipboard mode title (hidden in normal mode)
        self.clipboard_title = QLabel("Clipboard")
        self.clipboard_title.setFont(QFont("Instrument Serif", 34))
        self.clipboard_title.setStyleSheet("font-style: italic; color: rgba(255,255,255,0.6);")
        self.clipboard_title.hide()

        self.clipboard_close_btn = QPushButton("×")
        self.clipboard_close_btn.setFixedSize(36, 36)
        self.clipboard_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clipboard_close_btn.setObjectName("ClipboardCloseBtn")
        self.clipboard_close_btn.clicked.connect(self.exit_clipboard_mode)
        self.clipboard_close_btn.hide()

        input_layout.addWidget(self.settings_title, 1)
        input_layout.addWidget(self.settings_close_btn)
        input_layout.addWidget(self.clipboard_title, 1)
        input_layout.addWidget(self.clipboard_close_btn)

        # Mic at the end (Right edge)
        input_layout.addWidget(self.mic_widget)

        self.divider = QFrame()
        self.divider.setObjectName("Divider")

        self.list_widget = SmoothScrollListWidget()
        self.list_widget.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.itemClicked.connect(self.on_entered)
        self.list_widget.itemDoubleClicked.connect(self._on_clipboard_double_click)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Only take focus on click, never programmatically
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
            /* Hover style removed to prevent double-highlight during keyboard nav */
            QListWidget::item:focus {
                background-color: rgba(255, 255, 255, 0.15);
                outline: none;
            }
        """)

        self.settings_panel = SettingsPanel()
        self.settings_panel.hide()

        content_layout.addWidget(self.input_container)
        content_layout.addWidget(self.divider)
        content_layout.addWidget(self.list_widget, 1) # Expand to fill available space
        content_layout.addWidget(self.settings_panel, 1) # Shown in settings mode
        # content_layout.addStretch() # Removed to prevent squashing list
        main_layout.addWidget(self.frame)

        # self.setStyleSheet(STYLE_SHEET) # Moved to set_theme

        self.chat_history = []
        self.is_history_mode = False
        self._streaming_answer_widget = None  # tracks widget currently being streamed
        self._pending_partial_data = None  # buffered partial for throttled UI update
        self._partial_flush_timer = QTimer(self)
        self._partial_flush_timer.setSingleShot(True)
        self._partial_flush_timer.setInterval(50)  # ~20 FPS cap for layout updates
        self._partial_flush_timer.timeout.connect(self._flush_partial_update)
        self._continuation_thinking_prefix = ""  # thinking text prepended on request_permission re-run
        self._continuation_pending = False  # True while waiting for user to approve request_permission
        self._pending_open_file = None  # file path shown as "Enter to open" hint after AI response
        self.is_settings_mode = False
        self.is_clipboard_mode = False
        self._reset_on_next_show = False
        self.is_command_palette = False  # True when "/" command palette is shown
        self._closed_by_deactivation = False  # True when closed by focus-loss, False when closed by shortcut

        # Command palette definitions: (icon, name, description, template, category)
        # icon = single clean character, category = color group
        self._commands = [
            # --- Core ---
            ("✦",  "Ask Omni",             "Ask AI anything",                           "",                             "ai"),
            ("⌕",  "Search the web",       "Find anything on the internet",             "search for ",                  "search"),
            ("▶",  "Open application",     "Launch an app",                             "open ",                        "system"),
            ("◎",  "Find files",           "Locate files by name",                      "find ",                        "file"),
            # --- Quick actions ---
            ("文",  "Translate",            "e.g. \"hello\" to Spanish",                  "translate ... to ",            "convert"),
            ("=",  "Calculate",            "Math expressions and formulas",             "",                             "convert"),
            ("$",  "Convert currency",     "e.g. 100 usd to eur",                      "convert ",                     "convert"),
            ("↔",  "Convert units",        "e.g. 10 km to miles",                       "convert ",                     "convert"),
            ("☀",  "Weather",              "Current conditions for any city",            "weather in ",                  "search"),
            ("◷",  "Set a timer",          "Start a countdown",                         "set timer for ",               "tool"),
            ("⁂",  "Generate password",    "Create a secure random password",            "generate password",            "tool"),
            ("⊞",  "Generate QR code",     "Turn any text or URL into QR",              "generate qr code for ",        "tool"),
            ("◉",  "Color preview",        "Preview HEX, RGB or HSL values",            "#",                            "media"),
            # --- Communication ---
            ("✉",  "Send email",           "Compose and send via Mail",                 "send email to ",               "comms"),
            ("↓",  "Check emails",         "Read unread emails",                        "check my unread emails",       "comms"),
            ("▦",  "Calendar events",      "View upcoming schedule",                    "what are my upcoming events",  "comms"),
            ("+",  "Create event",         "Add a new calendar event",                  "create event ",                "comms"),
            # --- AI tools ---
            ("⌘",  "Run command",          "Execute a shell command",                   "run ",                         "system"),
            ("□",  "Create file",          "Create a new file with content",            "create a file called ",        "file"),
            ("✎",  "Edit file",            "Modify an existing file",                   "edit ",                        "file"),
            ("≋",  "Search in files",      "Semantic search through documents",         "search my files for ",         "search"),
            ("▣",  "Search images",        "Find photos by description",                "find photos of ",              "media"),
            ("◆",  "Remember this",        "Save a fact to memory",                     "remember that ",               "memory"),
            ("◇",  "Recall memory",        "What do I know about you",                  "what do you remember about ",  "memory"),
            ("⤓",  "Compress files",       "Zip files or folders",                      "compress ",                    "file"),
            ("⟲",  "Convert file",         "Change format — image, doc, audio, video",  "convert ",                     "convert"),
            ("⊟",  "Organize folder",      "Auto-sort files into subfolders",           "organize ",                    "file"),
            # --- Apps & system ---
            ("↧",  "Install app",          "Install via Homebrew",                      "install ",                     "system"),
            ("×",  "Uninstall app",        "Remove an application",                     "uninstall ",                   "system"),
            ("⇱",  "Computer control",  "Click, type, scroll on screen",              "click on ",                    "tool"),
            ("⚙",  "Optimize system",      "Clean up and speed up your Mac",            "optimize system",              "tool"),
            # --- Omni ---
            ("☰",  "Settings",             "Open Omni preferences",                     "/settings",                    "default"),
            ("▧",  "Clipboard history",    "Browse recent clipboard",                   "/clipboard",                   "default"),
        ]

        # Start clipboard history monitoring
        self._clipboard_manager = ClipboardManager.instance()
        self._clipboard_manager.new_entry.connect(self._on_clipboard_new_entry)

        self.apps = self.load_apps()
        self.is_entry_animating = False
        self.is_installing = False 
        self.voice_triggered_query = False
        self.last_action_time = 0
        self.refresh_list("", animate=False)
        self.center()  

        # self.animate_entry() # Don't auto-show on init. Let the caller decide or hotkey trigger it.

        self.search_worker = None
        self.action_worker = None
        self.ai_worker = None
        self.file_search_worker = None
        self.tts_worker = None
        self.og_worker = None
        self.is_tts_playing = False

        self.current_theme = "dark" # Default
        
        # Detect and set initial theme
        initial_theme = self.detect_system_theme()
        self.set_theme(initial_theme)

        self.external_actions = []
        self.external_search_results = []
        self.local_file_results = []
        self.og_data = None        # Open Graph website preview data
        self.instant_url = None    # (url, domain) when query is a URL/domain — shown instantly
        self.instant_calc = None   # (val_str, eq_str) when query is a pure math expression

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(300)  # FAST: Reduced from 650ms to 300ms
        self.debounce_timer.timeout.connect(self.trigger_async_searches)

        self.local_search_timer = QTimer()
        self.local_search_timer.setSingleShot(True)
        self.local_search_timer.setInterval(50) # 50ms debounce for local search to prevent UI lag on fast typing
        self.local_search_timer.timeout.connect(self.perform_local_search)

        # Start IPC Listener
        start_ipc_listener(self)

        # Warm up IconManager (initializes QFileIconProvider/CoInitialize/etc)
        # We use sys.executable to trigger the heavy path for EXE icons to prevent freeze on first type
        IconManager.instance().request(sys.executable)

        # Install an application-wide event filter to catch global shortcuts
        # (like Cmd+Option) regardless of which widget has focus (e.g. UnscrollableTextEdit)
        QApplication.instance().installEventFilter(self)

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
        
        # Ensure focus ring is disabled (sometimes reset by style change)
        if hasattr(self, 'input_field'):
            self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        
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
                    
        # 5. Update Settings Panel
        if hasattr(self, 'settings_panel'):
            self.settings_panel.set_theme(theme_name)
            self._apply_settings_close_btn_style()

        # 5b. Update Clipboard button style
        if hasattr(self, 'clipboard_close_btn'):
            self._apply_clipboard_close_btn_style()

        # 6. Update Glass Effect
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
                            
                            # logging.info("MacOS Liquid Glass applied via NSGlassEffectView")
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

        # macOS: PyQt6 resets the activation policy to Regular when a window is
        # first shown, making Python appear in the Dock. Re-apply Accessory here
        # so the Python icon is suppressed every time the window becomes visible.
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().setActivationPolicy_(1)
            except Exception:
                pass

        # Check for theme change on show
        current_os_theme = self.detect_system_theme()
        if current_os_theme != self.current_theme:
            self.set_theme(current_os_theme)
            
        # Ensure minimal size on show if in search mode (and NOT in settings mode)
        if not self.is_history_mode and not self.input_field.text() and not self.is_settings_mode:
             self.resize(self.width(), 84)
        elif self.is_settings_mode:
             self.resize(self.width(), self._SETTINGS_HEIGHT)
             # Clear any stale QGraphicsOpacityEffect (left from a partial fade-in animation)
             # then ensure the panel is visible before refreshing its content.
             self.settings_panel.setGraphicsEffect(None)
             self.settings_panel.show()
             # Refresh account/subscription state every time settings re-appears,
             # so stale "waiting for payment" messages are cleared and plan badges update.
             self.settings_panel.refresh_account()
             
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
                from AppKit import NSApplication
                ns_app = NSApplication.sharedApplication()
                ns_app.activateIgnoringOtherApps_(True)
                # Re-apply Accessory policy immediately after activation —
                # activateIgnoringOtherApps_ can cause macOS to promote the
                # app to Regular, briefly showing "Python" in the Dock.
                ns_app.setActivationPolicy_(1)
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
        
        # Aggressively kill the blue focus ring
        self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.input_field.setFocus()
        
        # Re-polish to ensure stylesheet is applied cleanly
        self.input_field.style().unpolish(self.input_field)
        self.input_field.style().polish(self.input_field)
        self.input_field.update()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_blur()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.apply_blur()
                
    def center(self):
        cursor_pos = QCursor.pos()
        
        # Robustly find the screen containing the cursor
        target_screen = None
        for screen in QGuiApplication.screens():
            if screen.geometry().contains(cursor_pos):
                target_screen = screen
                break
        
        # Fallback to screenAt if manual check fails
        if not target_screen:
            target_screen = QGuiApplication.screenAt(cursor_pos)
            
        # Fallback to primary
        if not target_screen:
            target_screen = QApplication.primaryScreen()
            
        if target_screen:
            geo = target_screen.availableGeometry()
            x = geo.x() + (geo.width() - DEFAULT_WIDTH) // 2
            # Always position based on collapsed height (84px) so that reopening with
            # chat history doesn't push the window to the top of the screen.
            y = geo.y() + (geo.height() - 84) // 2

            # Ensure y is relative to screen top!
            # We want it slightly higher than center (y-150), but at least 40px from top of screen
            y = max(geo.y() + 40, y - 150)
            
            self.move(int(x), int(y))
        else:
            self.move(100, 100)

    def reset_to_search_mode(self, animate=True, clear=True):
        if hasattr(self, 'anim'): self.anim.stop()
        if hasattr(self, 'anim_group'): self.anim_group.stop()
        if hasattr(self, 'anim_close_group'): self.anim_close_group.stop()

        self._is_closing = False # Reset closing flag in case we interrupted a close animation
        self.is_entry_animating = False  # Reset so adjust_window_height is not blocked

        # Stop voice listening if active (e.g. Escape during "Hey Omni" listening)
        self.send_udp_command("SET_MODE:PAUSED")
        self.mic_widget.set_active(False)
        self.voice_triggered_query = False
        self.logo_label.stop_spinning()

        # Stop TTS if playing
        if self.is_tts_playing:
            if self.tts_worker and self.tts_worker.isRunning():
                self.tts_worker.force_stop()
            self.is_tts_playing = False

        self.is_history_mode = False
        self.is_command_palette = False
        self.follow_up_widget.set_mode("hidden")
        self.frame.set_minimal_mode(True)
        self.input_field.setPlaceholderText("Search or ask...")

        self.input_field.blockSignals(True)
        if clear:
            self.input_field.clear()
        self.input_field.blockSignals(False)
        
        # Force resize limits
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        
        text = self.input_field.text() if not clear else ""
        
        # Ensure width is standard, but don't hard-reset the height!
        # Keeping current height allows refresh_list to animate the shrink.
        if self.width() != DEFAULT_WIDTH:
            self.resize(DEFAULT_WIDTH, self.height())

        self.refresh_list(text, animate=animate)

    def toggle_visibility_safe(self, source="manual"):
        import time
        now = time.time()
        # Debounce to prevent rapid double-triggers from native OS hooks
        if source == "manual" and (now - getattr(self, 'last_toggle_time', 0)) < 0.25:
            logging.info("toggle_visibility_safe ignored due to debounce")
            return
            
        self.last_toggle_time = now
        logging.info(f"toggle_visibility_safe called. Current visibility: {self.isVisible()}, Source: {source}")
        
        # Check if visible AND not currently closing.
        # If it's closing (animating out), we treat it as 'hidden' so we can immediately reopen it.
        if self.isVisible() and not self._is_closing:
            # If already visible...
            if source == "voice":
                # If voice triggered it again, just ensure we are listening (don't close)
                logging.info("Window already visible, voice trigger -> ensuring LISTENING mode")
                self.send_udp_command("SET_MODE:LISTENING")
                self.mic_widget.set_active(True)
                # Maybe flash the logo or UI to acknowledge?
                self.logo_label.boost_speed()
            else:
                # Manual toggle (hotkey/tray)
                # If window is visible, close it.
                self._closed_by_deactivation = False
                self.animate_close()
        else:
            # Window was hidden — show it.
            # If a close animation is still in progress, abort it and reopen immediately
            # so the shortcut always works on the first press.
            if self._is_closing:
                if hasattr(self, 'anim_close_group'):
                    self.anim_close_group.stop()
                self._is_closing = False
                self.setWindowOpacity(0.0)  # Stay invisible — animate_entry will fade in
                
                if self.is_settings_mode:
                    self.resize(DEFAULT_WIDTH, self._SETTINGS_HEIGHT)
                else:
                    self.resize(DEFAULT_WIDTH, 84)

            if source == "voice" and len(self.chat_history) > 0:
                # Same chat: user said wake word again while we had a conversation — keep context for follow-up
                logging.info("Reopening with existing chat (voice follow-up)")
                # Mark animating early so changeEvent can't fire a spurious close during processEvents
                self.is_entry_animating = True
                self.setWindowOpacity(0.0)
                self.show()
                self.center()
                if self.is_history_mode:
                    self._restore_history_ui()
                self.animate_entry()
                self.input_field.setFocus()
                self.send_udp_command("SET_MODE:LISTENING")
                self.mic_widget.set_active(True)
                # Ensure we're in history/follow-up mode so the next query is treated as follow-up
                if not self.is_history_mode:
                    self.is_history_mode = True
                    self.follow_up_widget.set_active(True)
                    self.frame.set_minimal_mode(False)
            else:
                # If closed via app launch, reset to a clean state instead of restoring old query
                if getattr(self, '_reset_on_next_show', False):
                    self._reset_on_next_show = False
                    self.reset_to_search_mode(clear=True, animate=False)

                # else: shortcut close — existing list/input preserved, just need height restore
                # Mark animating early so changeEvent can't fire a spurious close during processEvents
                self.is_entry_animating = True
                # Suppress macOS focus ring BEFORE show() so it never appears on the initial paint
                self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
                self.setWindowOpacity(0.0)
                self.show()
                self.center()
                
                # Expand the window to fit whatever is in the list (collapsed to 84px on close).
                if self.is_clipboard_mode:
                    # Restore clipboard UI — widgets were hidden when window was hidden
                    self.input_field.hide()
                    self.follow_up_widget.hide()
                    self.mic_widget.hide()
                    self.cc_container.hide()
                    self.clipboard_title.show()
                    self.clipboard_close_btn.show()
                    self._apply_clipboard_close_btn_style()
                    self._populate_clipboard_list()
                elif self.is_history_mode:
                    self._restore_history_ui()
                else:
                    # Restore active border if AI is still running (e.g. thinking on first query)
                    ai_active = hasattr(self, 'ai_worker') and self.ai_worker and self.ai_worker.isRunning()
                    if ai_active:
                        self.frame.set_minimal_mode(False)
                        self.logo_label.boost_speed()
                    self.adjust_window_height(animate=False, force=True)
                self.animate_entry()

                # Force focus and styling again in the SHOW path to catch the "Blue Border"
                self.input_field.setFocus()
                self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
                self.input_field.style().unpolish(self.input_field)
                self.input_field.style().polish(self.input_field)
                self.input_field.update()

                # Contextual pre-fetch based on active app
                self._trigger_prefetch()

                if source == "voice":
                    self.send_udp_command("SET_MODE:LISTENING")
                    self.mic_widget.set_active(True)
                else:
                    self.send_udp_command("SET_MODE:PAUSED")
                    self.mic_widget.set_active(False)

    def _restore_history_ui(self):
        """Restore the chat/history UI after the window is reshown (was resized to 84px on close)."""
        self.follow_up_widget.set_active(True)
        assistant_count = sum(1 for m in self.chat_history if m.get('role') == 'assistant')
        ai_active = bool(self.ai_worker and self.ai_worker.isRunning())
        # Active border when AI is generating OR when multiple exchanges exist
        if assistant_count > 1 or ai_active:
            self.frame.set_minimal_mode(False)
        else:
            self.frame.set_minimal_mode(True)
        self.input_field.setPlaceholderText("Ask a follow-up...")
        
        # Don't destroy the dynamically created action widgets/cards (like TrustPermissionChatWidget).
        # We just need to trigger a layout pass to ensure they fit properly after the window resize.
        QApplication.processEvents()
        for _i in range(self.list_widget.count()):
            _item = self.list_widget.item(_i)
            _w = self.list_widget.itemWidget(_item)
            if _w is not None:
                _w.updateGeometry()
                _item.setSizeHint(_w.sizeHint())
                
        self.adjust_window_height(animate=False, force=True)
        self.input_field.setFocus()

    def send_udp_command(self, command):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(command.encode('utf-8'), ('127.0.0.1', 5557))
        except Exception as e:
            logging.error(f"UDP Error: {e}")

    def _check_microphone_permission(self):
        """Check macOS microphone permission via AVFoundation.
        Returns 'authorized', 'denied', 'not_determined', or 'unknown'."""
        if sys.platform != "darwin":
            return "authorized"
        try:
            import ctypes, ctypes.util
            # Must load AVFoundation framework so AVCaptureDevice class is available
            ctypes.CDLL("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
            libobjc = ctypes.CDLL(ctypes.util.find_library("objc"))
            libobjc.objc_getClass.restype = ctypes.c_void_p
            libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
            libobjc.sel_registerName.restype = ctypes.c_void_p
            libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
            cls = libobjc.objc_getClass(b"AVCaptureDevice")
            if not cls:
                return "unknown"
            sel = libobjc.sel_registerName(b"authorizationStatusForMediaType:")
            # Build NSString for AVMediaTypeAudio ("soun")
            msg_str = libobjc.objc_msgSend
            msg_str.restype = ctypes.c_void_p
            msg_str.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            audio_type = msg_str(libobjc.objc_getClass(b"NSString"),
                                 libobjc.sel_registerName(b"stringWithUTF8String:"), b"soun")
            msg = libobjc.objc_msgSend
            msg.restype = ctypes.c_long
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            status = msg(cls, sel, audio_type)
            # 0=notDetermined, 1=restricted, 2=denied, 3=authorized
            return {3: "authorized", 0: "not_determined"}.get(status, "denied")
        except Exception as e:
            logging.debug(f"[mic permission check] {e}")
            return "unknown"

    def _try_open_microphone(self):
        """Actually try to open the microphone. Returns True if it works, False if denied.
        Uses InputStream constructor only (no start) — enough to trigger TCC prompt."""
        try:
            import sounddevice as sd
            stream = sd.InputStream(channels=1, samplerate=16000, blocksize=512)
            stream.close()
            return True
        except Exception as e:
            logging.warning(f"[mic test] Cannot open microphone: {e}")
            return False

    def _show_mic_permission_dialog(self):
        """Show a dialog guiding the user to enable microphone access in System Settings."""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("Microphone Access Required")
        msg.setText("Omni needs microphone access for voice input.\n\n"
                    "Please enable it in System Settings:\n"
                    "Privacy & Security → Microphone → enable Omni (or Python)")
        msg.setIcon(QMessageBox.Icon.Warning)
        open_btn = msg.addButton("Open Settings", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == open_btn:
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"])

    def _restart_voice_listener(self):
        """Kill and restart the voice listener subprocess (e.g. after mic permission granted)."""
        try:
            app = QApplication.instance()
            if hasattr(app, '_omni_voice') and app._omni_voice:
                try:
                    app._omni_voice.terminate()
                except Exception:
                    pass
            if sys.platform == "darwin":
                subprocess.run(["pkill", "-f", "services/voice/listener.py"], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            def _start_new_listener():
                try:
                    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    _listener_script = os.path.join(_project_root, "src", "services", "voice", "listener.py")
                    _log_dir = os.path.join(_project_root, "logs")
                    os.makedirs(_log_dir, exist_ok=True)
                    _voice_log = open(os.path.join(_log_dir, "listener.log"), "w")
                    proc = subprocess.Popen(
                        [sys.executable, _listener_script],
                        cwd=_project_root,
                        stdout=_voice_log, stderr=_voice_log,
                        start_new_session=True,
                    )
                    app._omni_voice = proc
                    logging.info("Voice listener restarted after mic permission change.")
                except Exception as e:
                    logging.error(f"Failed to start new voice listener: {e}")

            # Small delay for the old process to fully exit
            QTimer.singleShot(300, _start_new_listener)
        except Exception as e:
            logging.error(f"Failed to restart voice listener: {e}")

    def toggle_listening(self):
        if self.mic_widget.active:
            # Stop listening -> Commit Audio (Process what was said)
            self.send_udp_command("COMMIT_AUDIO")
            self.mic_widget.set_active(False)
            # Since user manually recorded, we treat this as a voice query for TTS purposes
            self.voice_triggered_query = True
        else:
            if sys.platform == "darwin":
                mic_status = self._check_microphone_permission()
                if mic_status == "denied":
                    self._show_mic_permission_dialog()
                    return
                if mic_status != "authorized":
                    # not_determined or unknown — try opening mic to trigger the system prompt
                    if not self._try_open_microphone():
                        self._show_mic_permission_dialog()
                        return
                # Check if the voice listener subprocess is alive; restart if dead
                app = QApplication.instance()
                voice_proc = getattr(app, '_omni_voice', None)
                if voice_proc and voice_proc.poll() is not None:
                    # Process has exited — restart it
                    logging.info("Voice listener process is dead, restarting...")
                    self._restart_voice_listener()
                    def _start_after_restart():
                        self.send_udp_command("SET_MODE:LISTENING")
                    QTimer.singleShot(1500, _start_after_restart)
                    self.mic_widget.set_active(True)
                    return
            # Start listening -> Go to LISTENING
            self.send_udp_command("SET_MODE:LISTENING")
            self.mic_widget.set_active(True)

    def _idle_placeholder(self):
        """Return the correct placeholder for the current mode."""
        return "Ask a follow-up..." if self.is_history_mode else "Search or ask..."

    def handle_voice_status(self, status):
        if status == "LISTENING":
            self.mic_widget.set_active(True)
            self.input_field.setPlaceholderText("Listening...")
        elif status == "TRANSCRIBING":
            self.mic_widget.set_active(False)
            self.input_field.setPlaceholderText("Transcribing...")
            self._start_transcribing_dots()
        elif status == "PAUSED":
            self._stop_transcribing_dots()
            self.mic_widget.set_active(False)
            self.input_field.setPlaceholderText(self._idle_placeholder())
        elif status == "IDLE":
            self._stop_transcribing_dots()
            self.mic_widget.set_active(False)
            self.input_field.setPlaceholderText(self._idle_placeholder())
        elif status == "MIC_DENIED":
            self.mic_widget.set_active(False)
            self._show_mic_permission_dialog()

    def _start_transcribing_dots(self):
        """Animate the placeholder text: Transcribing → Transcribing. → Transcribing.. → Transcribing..."""
        if not hasattr(self, '_transcribing_timer'):
            from PyQt6.QtCore import QTimer
            self._transcribing_timer = QTimer(self)
            self._transcribing_timer.setInterval(400)
            self._transcribing_timer.timeout.connect(self._tick_transcribing_dots)
            self._transcribing_dot_count = 0
        self._transcribing_dot_count = 0
        self._transcribing_timer.start()

    def _tick_transcribing_dots(self):
        self._transcribing_dot_count = (self._transcribing_dot_count + 1) % 4
        dots = "." * self._transcribing_dot_count
        self.input_field.setPlaceholderText(f"Transcribing{dots}")

    def _stop_transcribing_dots(self):
        if hasattr(self, '_transcribing_timer'):
            self._transcribing_timer.stop()

    def _get_asr_dispatcher(self):
        """Lazy-init a QObject dispatcher for cross-thread transcription callbacks."""
        if not hasattr(self, '_asr_dispatcher'):
            from PyQt6.QtCore import QObject, pyqtSignal

            class _Dispatcher(QObject):
                done = pyqtSignal(str)

            self._asr_dispatcher = _Dispatcher(self)
            self._asr_dispatcher.done.connect(self._on_transcription_done)
        return self._asr_dispatcher

    def handle_transcribe_file(self, wav_path: str):
        """Transcribe a WAV file using Groq Whisper API (whisper-large-v3).

        Whisper handles multilingual audio (Polish, English, etc.) with auto language
        detection when transcription_language is set to "auto".
        """
        import threading, os

        dispatcher = self._get_asr_dispatcher()

        def _worker():
            text = None
            try:
                from openai import OpenAI
                from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID
                import src.core.settings_store as _ss

                lc = _ss.get("transcription_language", "auto")
                # Whisper uses ISO-639-1 codes directly; None = auto-detect
                lang = None if (not lc or lc == "auto") else lc

                wav_size = os.path.getsize(wav_path) if os.path.exists(wav_path) else 0
                logging.info(f"[ASR] Groq Whisper transcribing {wav_path} ({wav_size} bytes), lang={lang or 'auto'}")

                client = OpenAI(
                    api_key="omni-proxy",
                    base_url=BACKEND_URL + "/v1",
                    default_headers={
                        "X-Omni-Secret": OMNI_SECRET,
                        "X-Device-ID":   DEVICE_ID,
                    },
                )
                with open(wav_path, "rb") as f:
                    kwargs = dict(
                        file=(os.path.basename(wav_path), f.read()),
                        model="whisper-large-v3-turbo",
                        response_format="text",
                        temperature=0.0,
                    )
                    if lang:
                        kwargs["language"] = lang
                    result = client.audio.transcriptions.create(**kwargs)

                text = str(result).strip() if result else None
                logging.info(f"[ASR] Groq result: {text!r}")

            except Exception as e:
                logging.error(f"[ASR] Groq Whisper error: {e}")
            finally:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

            if text and text.strip():
                logging.info(f"[ASR] Transcription: {text!r}")
                dispatcher.done.emit(text.strip())
            else:
                logging.warning("[ASR] No transcription result.")
                dispatcher.done.emit("")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_transcription_done(self, text: str):
        """Called on the main thread when transcription completes."""
        self._stop_transcribing_dots()
        if text:
            self.handle_ipc_query(f"VOICE:{text}")
        else:
            self.handle_voice_status("IDLE")

    def handle_partial_text(self, text):
        # Update input field with partial text without triggering search
        self.input_field.blockSignals(True)
        self.input_field.setText(text)
        self.input_field.blockSignals(False)
        # Maybe move cursor to end
        self.input_field.setCursorPosition(len(text))

    def handle_ipc_query(self, query):
        logging.info(f"IPC Query Received: {query}")

        # Clean up query if it has VOICE: prefix
        is_voice = query.startswith("VOICE:")
        if is_voice:
            query = query[6:]

        logging.info(f"[IPC] isVisible={self.isVisible()}, is_history_mode={self.is_history_mode}, "
                     f"is_entry_animating={getattr(self, 'is_entry_animating', 'N/A')}, "
                     f"list_count={self.list_widget.count()}")

        if not self.isVisible():
            # Fresh query from wake word — reset state and show window
            self.reset_to_search_mode(animate=False)
            self.chat_history = []
            self.show()
            self.center()
            # Skip animate_entry() — show at full opacity so ThinkingWidget
            # is visible immediately (entry animation conflicts with height resize)
            self.setWindowOpacity(1.0)
            self.is_entry_animating = False
            self.frame.boost_speed()
        # else: window already visible — keep current chat state for follow-ups

        # Always clear entry animation flag so adjust_window_height is never blocked
        self.is_entry_animating = False

        self.raise_()
        self.activateWindow()

        # Set text and trigger fast action search
        self.input_field.setText(query)
        self._trigger_immediate_fast_action(query)
        logging.info(f"[IPC] After fast action trigger: list_count={self.list_widget.count()}, "
                     f"list_visible={self.list_widget.isVisible()}, divider_visible={self.divider.isVisible()}, "
                     f"window_height={self.height()}")
        if is_voice:
            self.voice_triggered_query = True

    def animate_entry(self):
        self.is_entry_animating = True
        self.frame.boost_speed()
        
        # Use windowOpacity for smoother whole-window fade (including blur)
        self.setWindowOpacity(0.0)
        self.setGraphicsEffect(None)
        
        self.anim_group = QParallelAnimationGroup()
        
        def on_finished():
            self.is_entry_animating = False
            # If items were added during the entry animation (e.g. ThinkingWidget for voice),
            # now that the animation is done we can safely resize the window.
            QTimer.singleShot(0, lambda: self.adjust_window_height(animate=True))

        self.anim_group.finished.connect(on_finished)
        
        # Zoom In Animation
        # Use DEFAULT_WIDTH to prevent width from drifting on repeated show/hide cycles
        target_geo = self.geometry()
        target_geo.setWidth(DEFAULT_WIDTH)
        center = target_geo.center()

        # Start size: 92% (Subtle zoom)
        start_w = int(DEFAULT_WIDTH * 0.92)
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
        
        # Ensure focus is set correctly and border suppressed during entry
        self.input_field.setFocus()
        self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        # Re-polish just in case
        self.input_field.style().unpolish(self.input_field)
        self.input_field.style().polish(self.input_field)
        self.activateWindow()
        self.raise_()

    def adjust_window_height(self, animate=True, force=False):
        if self.is_settings_mode:
            return

        if not force and hasattr(self, 'is_entry_animating') and self.is_entry_animating:
            # Don't interrupt entry animation
            return

        list_h = 0
        count = self.list_widget.count()

        if count > 0:
            self.divider.show()
            self.list_widget.show()
            for i in range(count):
                item = self.list_widget.item(i)
                # Use already-set sizeHint (cached by update_item_size / setSizeHint)
                # instead of recomputing via the widget's sizeHint() method
                list_h += item.sizeHint().height() + 6 # Add margin-bottom from CSS

            base_h = 84
            # Account for QListWidget vertical padding (12px top + 12px bottom)
            # plus a tiny safety buffer to avoid "just barely" showing a scrollbar.
            extra_padding = 28
            
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
            
            # Add padding for list borders/margins + safety
            new_h = base_h + list_h + extra_padding
        else:
            self.divider.hide()
            self.list_widget.hide()
            new_h = 84 # Just the input height

        current_h = self.height()
        
        if current_h != new_h:
            # If we are already animating, force animation to continue to avoid snapping
            if self.anim.state() == QPropertyAnimation.State.Running:
                # OPTIMIZATION: If already animating to the exact same height, do nothing.
                # This prevents "jitters" when typing fast where height doesn't change but text does.
                current_end = self.anim.endValue()
                if isinstance(current_end, QRect) and current_end.height() == new_h:
                    return

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
        
        # If we are in the middle of executing an action (like a tool call that opens a popup),
        # we should NOT auto-close.
        if self.action_worker and self.action_worker.isRunning():
            logging.info("Window deactivated but action worker is running - keeping window open.")
            return

        # Prevent closing if OG worker is running (fetching rich previews)
        if hasattr(self, 'og_worker') and self.og_worker and self.og_worker.isRunning():
             logging.info("Window deactivated but OG worker is running - keeping window open.")
             return

        # Prevent closing if any place resolver worker is running (fetching map/image data)
        if hasattr(self, 'place_workers') and any(w.isRunning() for w in self.place_workers.values()):
            logging.info("Window deactivated but place resolver is running - keeping window open.")
            return

        # Grace period: if we just showed actions (within 1.5s), ignore deactivation
        # This handles race conditions where activateWindow() or UI updates cause transient focus loss
        if time.time() - getattr(self, 'last_action_time', 0) < 1.5:
             logging.info("Window deactivated but in action grace period - keeping window open.")
             return

        # In conversation/follow-up mode keep the window alive so the user can
        # read, copy, and interact with responses. The shortcut still closes it.
        if self.is_history_mode:
            return

        # Prevent closing if Mic is active (User is speaking)
        if self.mic_widget.active:
            logging.info("Window deactivated but Mic is active - keeping window open.")
            return
            
        # Prevent closing if TTS is playing (Assistant is speaking)
        if self.is_tts_playing:
             logging.info("Window deactivated but TTS is playing - keeping window open.")
             return
             
        # Prevent closing if AI is thinking - UNLESS we are opening a tool window
        # The user wants Omni to close if a tool (like Calendar/Mail) pops up, so we can focus on it.
        # But we don't know easily if a tool pop up is happening here.
        # However, if the user explicitly switches focus (clicks away), we should close.
        # The "AI thinking" check prevents this.
        # Let's relax it: if AI is running, we usually stay open to show the stream.
        # BUT if the OS focus changes, it means the user is doing something else.
        # If we keep it open, it stays on top (WindowStaysOnTopHint) and obscures the new window.
        # So we SHOULD close it on deactivate, even if AI is running.
        # Exception: Voice/TTS (handled above).
        
        # if self.ai_worker and self.ai_worker.isRunning():
        #      logging.info("Window deactivated but AI is thinking - keeping window open.")
        #      return

        # Prevent closing if screenshot worker is running
        if hasattr(self, 'screenshot_worker') and self.screenshot_worker and self.screenshot_worker.isRunning():
             logging.info("Window deactivated but screenshot taking - keeping window open.")
             return

        # Only trigger if we're not already in the middle of a shortcut-close.
        # Without this guard, the focus-loss side-effect of the shortcut animation
        # would overwrite _closed_by_deactivation=False with True.
        if self.isVisible() and not self.is_entry_animating and not self._is_closing:
            self._closed_by_deactivation = True
            self.animate_close()

    def event(self, event):
        # Detect deactivation (focus loss)
        if event.type() == QEvent.Type.WindowDeactivate:
             self.on_deactivate()
        return super().event(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            # Cmd+Option (macOS) → hide window globally across the app
            cmd_opt = Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier
            # Mask out keypads/etc just in case, but strictly require Cmd+Opt and no other main mods
            mask = Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier

            if (event.modifiers() & mask) == cmd_opt:
                # Ensure no other key is pressed (e.g. Cmd+Opt+C should not hide window)
                if event.key() in (Qt.Key.Key_Meta, Qt.Key.Key_Alt, Qt.Key.Key_Option):
                    if self.isVisible() and not self._is_closing:
                        self._closed_by_deactivation = False
                        self.animate_close()
                    return True

        if obj == self.input_field and event.type() == QEvent.Type.MouseButtonPress:
            self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
            self.input_field.style().unpolish(self.input_field)
            self.input_field.style().polish(self.input_field)

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
                # Clipboard mode: Enter copies + pastes
                if self.is_clipboard_mode:
                    current_item = self.list_widget.currentItem()
                    if current_item:
                        self._handle_clipboard_item_selected(current_item, paste=True)
                    return True

                # Command palette: Enter selects the current (or first) command
                if self.is_command_palette:
                    current_item = self.list_widget.currentItem()
                    if not current_item and self.list_widget.count() > 0:
                        current_item = self.list_widget.item(0)
                    if current_item:
                        self.on_entered(current_item)
                    return True

                # ENTER on input field - trigger selected item or AI query
                current_item = self.list_widget.currentItem()
                if current_item:
                    self.on_entered(current_item)
                    return True
                else:
                    # If the first visible item is an AppActionWidget, accept it directly
                    # so pressing Enter immediately launches the app without a second query.
                    if self.list_widget.count() > 0:
                        first_item = self.list_widget.item(0)
                        if first_item:
                            first_data = first_item.data(Qt.ItemDataRole.UserRole)
                            if isinstance(first_data, dict) and first_data.get('type') == 'open_app':
                                self.on_entered(first_item)
                                return True
                    query = self.input_field.text().strip()
                    if query:
                        self._activate_first_or_search(query)
                        return True
            elif event.key() == Qt.Key.Key_Escape:
                # logging.info("Escape key pressed (Input Field)")

                if self.is_command_palette:
                    self.is_command_palette = False
                    self.input_field.clear()
                    self.list_widget.clear()
                    self.frame.set_minimal_mode(True)
                    self.adjust_window_height(animate=True)
                    return True

                if self.is_clipboard_mode:
                    self.exit_clipboard_mode()
                    return True

                if self.is_settings_mode:
                    self.exit_settings_mode()
                    return True

                # Check if we are streaming response
                if hasattr(self, 'ai_worker') and self.ai_worker and self.ai_worker.isRunning():
                    self.abort_ai_generation()
                    return True

                if self.is_history_mode or self.input_field.text():
                    self.reset_to_search_mode()
                    self.chat_history = []
                else:
                    self.input_field.clear()
                    self.animate_close()
                return True
            elif event.key() == Qt.Key.Key_Tab:
                # Command palette: Tab selects the current (or first) command
                if self.is_command_palette:
                    current_item = self.list_widget.currentItem()
                    if not current_item and self.list_widget.count() > 0:
                        current_item = self.list_widget.item(0)
                    if current_item:
                        self.on_entered(current_item)
                    return True

                # Check for Place card interaction (Tab -> Open Website)
                current_item = self.list_widget.currentItem()
                if current_item:
                    data = current_item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and data.get('type') == 'place':
                        url = data.get('url')
                        if url:
                            QDesktopServices.openUrl(QUrl(url))
                            self.animate_close()
                            return True

                # Tab no longer triggers search/ask — only Enter does
                return True
        return super().eventFilter(obj, event)

    def enter_history_mode(self):
        if self.is_history_mode: return
        self.is_history_mode = True
        self.follow_up_widget.set_mode("followup")
        self.frame.set_minimal_mode(False)
        self.input_field.setPlaceholderText("Ask a follow-up...")
        self._rebuild_history_list()

    def _rebuild_history_list(self):
        self.list_widget.clear()
        first = True

        # Use conversation bubbles only when there are multiple exchanges.
        # A single Q&A is restored as the original simple full-width view so it
        # looks identical to how it appeared right after the response streamed in.
        assistant_count = sum(1 for m in self.chat_history if m.get('role') == 'assistant')
        use_chat_mode = assistant_count > 1

        # Iterate backwards: newest first (newest at top of list)
        for i in range(len(self.chat_history) - 1, -1, -1):
            msg = self.chat_history[i]
            if msg.get('role') == 'assistant':
                content = msg.get('content', '')
                user_query = ""
                if i > 0 and self.chat_history[i-1].get('role') == 'user':
                    user_query = self.chat_history[i-1].get('content', '')

                if not first and not use_chat_mode:
                    self.add_list_item(SeparatorWidget(), "separator", animation="instant")

                thinking_text = msg.get('thinking', '')
                # First turn (bottom of list = last in reversed iteration) shows names,
                # subsequent turns hide them — left/right alignment is enough.
                is_first_turn = first
                w = AnswerWidget(content, query_text=user_query, thinking_text=thinking_text,
                                 chat_mode=use_chat_mode,
                                 show_user_name=is_first_turn, show_ai_name=is_first_turn)
                w.set_query_visible(use_chat_mode)
                if thinking_text:
                    w.set_thinking_collapsed(True)
                # Re-attach any settings animation widgets stored in history
                for act in msg.get('settings_actions', []):
                    try:
                        sw = SettingsAnimationWidget(
                            setting=act.get("setting", ""),
                            value=act.get("value", 0),
                            label=act.get("label", ""),
                            unit=act.get("unit", ""),
                            color_hex=act.get("color", "#FFFFFF"),
                            icon_name=act.get("icon", ""),
                            success=True
                        )
                        w.append_settings_widget(sw)
                    except Exception:
                        pass
                self.add_list_item(w, "history_ai", animation="instant")
                first = False

        # Force a layout pass so widgets have a visible parent chain and isVisible()=True.
        # Without this, AnswerWidget.sizeHint() skips the document-height calculation
        # (text_edit.isVisible() == False) and returns the minimum 40px stub height.
        QApplication.processEvents()
        # Re-sync every item's sizeHint from its actual rendered widget size.
        for _i in range(self.list_widget.count()):
            _item = self.list_widget.item(_i)
            _w = self.list_widget.itemWidget(_item)
            if _w is not None:
                _item.setSizeHint(_w.sizeHint())
        # Use animate=False so geometry is set immediately. animate_entry() runs after
        # and uses self.geometry() as its target — it must see the full height, not 84px.
        self.adjust_window_height(animate=False, force=True)

    def on_tts_finished(self):
        self.is_tts_playing = False
        logging.info("TTS Finished")

    def _actions_include_file_open(self, actions):
        """True if any action is a terminal_command that opens a file (open /path, xdg-open, etc)."""
        if not actions:
            return False
        for act in actions:
            if isinstance(act, dict) and act.get('type') == 'terminal_command':
                cmd = (act.get('command') or '').strip()
                if cmd.startswith('open ') or cmd.startswith('xdg-open '):
                    return True
        return False

    def animate_close(self):
        if self._is_closing: return

        # Always switch back to IDLE (Wake Word) mode when closing
        self.send_udp_command("SET_MODE:IDLE")
        self.mic_widget.set_active(False)
        self.voice_triggered_query = False

        # Stop TTS immediately on close
        if self.is_tts_playing:
            if self.tts_worker and self.tts_worker.isRunning():
                self.tts_worker.force_stop()
            self.is_tts_playing = False

        # Instantly reset the gradient border so it doesn't flash on next open
        self.frame.mode_anim.stop()
        self.frame.timer.stop()
        self.frame.minimal_mode = True
        self.frame._mode_progress = 0.0
        
        # Stop geometry animation if running
        if hasattr(self, 'anim') and self.anim.state() == QPropertyAnimation.State.Running:
            self.anim.stop()

        # Stop entry animation if running; reset flag since on_finished won't fire after stop()
        if hasattr(self, 'anim_group') and self.anim_group.state() == QPropertyAnimation.State.Running:
            self.anim_group.stop()
        self.is_entry_animating = False
            
        self._is_closing = True
        
        self.anim_close_group = QParallelAnimationGroup()
        
        # Smooth Zoom and Slide Down
        current_geo = self.geometry()
        center = current_geo.center()
        
        target_w = int(current_geo.width() * 0.98)
        target_h = int(current_geo.height() * 0.98)
        target_x = center.x() - target_w // 2
        # Slide down slightly
        target_y = current_geo.y() + 15
        
        target_geo = QRect(target_x, target_y, target_w, target_h)
        
        anim_geo = QPropertyAnimation(self, b"geometry")
        anim_geo.setDuration(220) # Slightly slower for smoother look
        anim_geo.setStartValue(current_geo)
        anim_geo.setEndValue(target_geo)
        anim_geo.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        # Opacity
        anim_opa = QPropertyAnimation(self, b"windowOpacity")
        anim_opa.setDuration(200) # Match geo closely
        anim_opa.setStartValue(1.0)
        anim_opa.setEndValue(0.0)
        anim_opa.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        def on_close_finished():
             self.hide()
             self._is_closing = False
             self.is_entry_animating = False
             self.setWindowOpacity(1.0)
             # Keep is_clipboard_mode flag so reopen restores clipboard view
             # Reset to clean default geometry so the next show always starts from
             # the correct width and a minimal height. center() will re-position it.
             self.resize(DEFAULT_WIDTH, 84)
             
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

    # ------------------------------------------------------------------
    # Settings mode
    # ------------------------------------------------------------------

    _SETTINGS_HEIGHT = 600

    def enter_settings_mode(self):
        if self.is_settings_mode:
            return
        self.is_settings_mode = True
        self.input_container.layout().setContentsMargins(18, 4, 20, 4)

        # Apply current theme to settings panel before showing
        self.settings_panel.set_theme(self.current_theme)
        self._apply_settings_close_btn_style()

        # Swap input-bar widgets
        self.input_field.hide()
        self.follow_up_widget.hide()
        self.mic_widget.hide()
        self.cc_container.hide()
        self.settings_title.show()
        self.settings_close_btn.show()

        # Hide chat content
        self.divider.hide()
        self.list_widget.hide()

        # Fade in settings panel
        settings_effect = QGraphicsOpacityEffect(self.settings_panel)
        self.settings_panel.setGraphicsEffect(settings_effect)
        settings_effect.setOpacity(0.0)
        self.settings_panel.show()

        self._settings_fade_in = QPropertyAnimation(settings_effect, b"opacity")
        self._settings_fade_in.setDuration(220)
        self._settings_fade_in.setStartValue(0.0)
        self._settings_fade_in.setEndValue(1.0)
        self._settings_fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._settings_fade_in.start()

        # Resize window to settings height
        current_geo = self.geometry()
        target_geo = QRect(current_geo.x(), current_geo.y(), current_geo.width(), self._SETTINGS_HEIGHT)
        self._settings_resize_anim = QPropertyAnimation(self, b"geometry")
        self._settings_resize_anim.setDuration(280)
        self._settings_resize_anim.setStartValue(current_geo)
        self._settings_resize_anim.setEndValue(target_geo)
        self._settings_resize_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._settings_resize_anim.start()

    def exit_settings_mode(self):
        if not self.is_settings_mode:
            return
        self.is_settings_mode = False
        self.input_container.layout().setContentsMargins(18, 4, 10, 4)

        # Fade out settings panel
        current_effect = self.settings_panel.graphicsEffect()
        if isinstance(current_effect, QGraphicsOpacityEffect):
            fade_effect = current_effect
        else:
            fade_effect = QGraphicsOpacityEffect(self.settings_panel)
            self.settings_panel.setGraphicsEffect(fade_effect)

        self._settings_fade_out = QPropertyAnimation(fade_effect, b"opacity")
        self._settings_fade_out.setDuration(160)
        self._settings_fade_out.setStartValue(1.0)
        self._settings_fade_out.setEndValue(0.0)
        self._settings_fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        # Simultaneously shrink window back to the bare input-bar height
        current_geo = self.geometry()
        target_geo = QRect(current_geo.x(), current_geo.y(), current_geo.width(), 84)
        self._settings_shrink_anim = QPropertyAnimation(self, b"geometry")
        self._settings_shrink_anim.setDuration(260)
        self._settings_shrink_anim.setStartValue(current_geo)
        self._settings_shrink_anim.setEndValue(target_geo)
        self._settings_shrink_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _on_fade_out_done():
            self.settings_panel.hide()
            self.settings_panel.setGraphicsEffect(None)
            # Restore input bar
            self.settings_title.hide()
            self.settings_close_btn.hide()
            self.input_field.show()
            self.follow_up_widget.show()
            self.mic_widget.show()
            # Restore chat area and let refresh_list handle height
            self.divider.show()
            self.list_widget.show()
            self.setMaximumHeight(16777215)
            self.refresh_list(self.input_field.text(), animate=False)
            self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
            self.input_field.setFocus()
            self.input_field.style().unpolish(self.input_field)
            self.input_field.style().polish(self.input_field)

        self._settings_shrink_anim.start()

        self._settings_fade_out.finished.connect(_on_fade_out_done)
        self._settings_fade_out.start()

    def _apply_settings_close_btn_style(self):
        t = THEMES.get(self.current_theme, THEMES["dark"])
        primary = t["text_primary"]
        placeholder = t["placeholder"]
        border = t["border_color"]
        is_dark = self.current_theme == "dark"
        btn_bg = "rgba(255,255,255,0.08)" if is_dark else "rgba(0,0,0,0.05)"
        btn_hover = "rgba(255,255,255,0.15)" if is_dark else "rgba(0,0,0,0.09)"

        # Title matches the input field placeholder style
        self.settings_title.setStyleSheet(
            f"font-style: italic; color: {placeholder};"
        )

        self.settings_close_btn.setStyleSheet(f"""
            QPushButton#SettingsCloseBtn {{
                background: {btn_bg};
                border: 1px solid {border};
                border-radius: 18px;
                color: {primary};
                font-size: 20px;
                font-family: "Manrope";
            }}
            QPushButton#SettingsCloseBtn:hover {{
                background: {btn_hover};
            }}
        """)

    # ------------------------------------------------------------------
    # Clipboard mode
    # ------------------------------------------------------------------

    def _on_clipboard_new_entry(self, entry):
        """Called whenever a new item is copied — refresh list if clipboard mode is open."""
        logging.info(f"Clipboard new_entry signal: visible={self.isVisible()}, clipboard_mode={self.is_clipboard_mode}, preview={entry.preview[:40]!r}")
        if self.is_clipboard_mode and self.isVisible():
            self._populate_clipboard_list()

    def _on_clipboard_shortcut(self):
        """Cmd+4 pressed — only acts when Omni window is already visible."""
        if self.isVisible() and not self._is_closing:
            if self.is_clipboard_mode:
                self.exit_clipboard_mode()
            else:
                self.enter_clipboard_mode()

    def toggle_clipboard(self):
        """Called by global hotkey (Cmd+Option+V) or in-window Cmd+4."""
        if self.isVisible() and not self._is_closing:
            if self.is_clipboard_mode:
                self.animate_close()
            else:
                self.enter_clipboard_mode()
        else:
            # Window hidden → show and enter clipboard mode
            # Reuse the normal show path then switch mode
            if self._is_closing:
                if hasattr(self, 'anim_close_group'):
                    self.anim_close_group.stop()
                self._is_closing = False
                self.setWindowOpacity(0.0)
                self.resize(DEFAULT_WIDTH, 84)

            self.is_entry_animating = True
            self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
            self.setWindowOpacity(0.0)
            self.show()
            self.center()
            
            # Reset flag and cleanly enter clipboard mode so geometry targets are fully correct
            self.is_clipboard_mode = False
            self.enter_clipboard_mode()
            
            self.animate_entry()
            self.input_field.setFocus()
            self.send_udp_command("SET_MODE:PAUSED")
            self.mic_widget.set_active(False)

    def enter_clipboard_mode(self):
        if self.is_clipboard_mode:
            return
        # Exit other modes first
        if self.is_settings_mode:
            self.exit_settings_mode()
        if self.is_history_mode:
            self.reset_to_search_mode(animate=False, clear=True)

        self.is_clipboard_mode = True
        self.input_container.layout().setContentsMargins(18, 4, 20, 4)

        # Swap input bar to clipboard title
        self.input_field.hide()
        self.follow_up_widget.hide()
        self.mic_widget.hide()
        self.cc_container.hide()
        self.clipboard_title.show()
        self.clipboard_close_btn.show()

        self._apply_clipboard_close_btn_style()

        # Show list with clipboard items
        self.divider.show()
        self.list_widget.show()
        self._populate_clipboard_list()

    def exit_clipboard_mode(self):
        if not self.is_clipboard_mode:
            return
        self.is_clipboard_mode = False
        self.is_entry_animating = False  # ensure adjust_window_height isn't blocked
        self.input_container.layout().setContentsMargins(18, 4, 10, 4)

        if hasattr(self, 'anim_group') and self.anim_group.state() == QPropertyAnimation.State.Running:
            self.anim_group.stop()

        self.clipboard_title.hide()
        self.clipboard_close_btn.hide()

        # Restore normal input bar
        self.input_field.show()
        self.follow_up_widget.show()
        self.mic_widget.show()
        # Don't pre-show divider/list — refresh_list will hide them since query is empty
        self.input_field.blockSignals(True)
        self.input_field.clear()
        self.input_field.setPlaceholderText("Search or ask...")
        self.input_field.blockSignals(False)
        self.frame.set_minimal_mode(True)
        self.refresh_list("", animate=True)
        self.input_field.setFocus()

    def _populate_clipboard_list(self, filter_text=""):
        """Fill the list widget with clipboard history entries."""
        # Force a poll so we get the latest clipboard content before showing
        self._clipboard_manager._poll()
        self.list_widget.clear()
        entries = self._clipboard_manager.search(filter_text)
        logging.info(f"_populate_clipboard_list: {len(entries)} entries, filter={filter_text!r}")

        if not entries:
            # Show empty state
            empty = StandardItemWidget("No clipboard history yet")
            empty.set_theme(self.current_theme)
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(0, 52))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, empty)
            if self.is_entry_animating:
                self.adjust_window_height(animate=False, force=True)
            else:
                self.adjust_window_height(animate=True)
            return

        for entry in entries:
            widget = ClipboardItemWidget(entry)
            widget.set_theme(self.current_theme)

            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(0, 60))
            item.setData(Qt.ItemDataRole.UserRole, {'type': 'clipboard', 'text': entry.text})
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        if self.is_entry_animating:
            self.adjust_window_height(animate=False, force=True)
        else:
            self.adjust_window_height(animate=True)

    def _apply_clipboard_close_btn_style(self):
        t = THEMES.get(self.current_theme, THEMES["dark"])
        primary = t["text_primary"]
        placeholder = t["placeholder"]
        border = t["border_color"]
        is_dark = self.current_theme == "dark"
        btn_bg = "rgba(255,255,255,0.08)" if is_dark else "rgba(0,0,0,0.05)"
        btn_hover = "rgba(255,255,255,0.15)" if is_dark else "rgba(0,0,0,0.09)"

        self.clipboard_title.setStyleSheet(
            f"font-style: italic; color: {placeholder};"
        )

        self.clipboard_close_btn.setStyleSheet(f"""
            QPushButton#ClipboardCloseBtn {{
                background: {btn_bg};
                border: 1px solid {border};
                border-radius: 18px;
                color: {primary};
                font-size: 20px;
                font-family: "Manrope";
            }}
            QPushButton#ClipboardCloseBtn:hover {{
                background: {btn_hover};
            }}
        """)

    def _handle_clipboard_item_selected(self, item, paste=False):
        """Handle clipboard item selection. Single click = copy only, double click / Enter = copy + paste."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get('type') != 'clipboard':
            return

        text = data.get('text', '')
        if not text:
            return

        # Set clipboard and update manager state so the poll doesn't re-add this entry
        QGuiApplication.clipboard().setText(text)
        self._clipboard_manager._last_text = text

        if paste:
            # Double click / Enter: copy + paste
            self.animate_close()
            QTimer.singleShot(400, self._simulate_paste)
        else:
            # Single click: just copy, stay open — flash the item to confirm
            widget = self.list_widget.itemWidget(item)
            if widget:
                self._flash_clipboard_copied(widget)

    @staticmethod
    def _simulate_paste():
        """Simulate Cmd+V keystroke on macOS."""
        if sys.platform == "darwin":
            try:
                import subprocess
                subprocess.Popen([
                    'osascript', '-e',
                    'tell application "System Events" to keystroke "v" using command down'
                ])
            except Exception as e:
                logging.error(f"Failed to simulate paste: {e}")

    def _flash_clipboard_copied(self, widget):
        """Brief 'Copied!' flash on a clipboard item to confirm single-click copy."""
        if not hasattr(widget, 'preview_label'):
            return
        original = widget.preview_label.text()
        t = THEMES.get(self.current_theme, THEMES["dark"])
        widget.preview_label.setText("Copied!")
        widget.preview_label.setStyleSheet(f"color: #4CAF50;")
        def restore():
            try:
                widget.preview_label.setText(original)
                widget.preview_label.setStyleSheet(f"color: {t['text_primary']};")
            except RuntimeError:
                pass
        QTimer.singleShot(600, restore)

    def _on_clipboard_double_click(self, item):
        """Double click on clipboard item → copy + paste."""
        if self.is_clipboard_mode and item:
            self._handle_clipboard_item_selected(item, paste=True)

    def keyPressEvent(self, event):
        # Cmd+Option (macOS) → hide window
        cmd_opt = Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier
        mask = Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        if (event.modifiers() & mask) == cmd_opt and event.key() in (Qt.Key.Key_Meta, Qt.Key.Key_Alt, Qt.Key.Key_Option):
            if self.isVisible() and not self._is_closing:
                self.animate_close()
            event.accept()
            return

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
            # logging.info("Escape key pressed (Global)")

            if self.is_clipboard_mode:
                self.exit_clipboard_mode()
                return

            if self.is_settings_mode:
                self.exit_settings_mode()
                return

            # Check if we are streaming response (Global Handler)
            if hasattr(self, 'ai_worker') and self.ai_worker and self.ai_worker.isRunning():
                self.abort_ai_generation()
                return

            if self.is_history_mode or self.input_field.text():
                self.reset_to_search_mode()
                self.chat_history = []
            else:
                self.input_field.clear()
                self.animate_close()
        super().keyPressEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                # Window lost focus — schedule a deferred check instead of closing immediately.
                # This absorbs transient deactivation events caused by force_focus() timer cycles
                # and by processEvents() draining stale ActivationChange events during restore.
                if self.isVisible() and not self._is_closing and not self.is_entry_animating:
                    QTimer.singleShot(80, self._deactivation_close_check)
        super().changeEvent(event)

    def _deactivation_close_check(self):
        """Close only if the window is still genuinely inactive after a short settle period."""
        if not self.isActiveWindow() and self.isVisible() and not self._is_closing and not self.is_entry_animating:
            self._closed_by_deactivation = True
            self.animate_close()

    def closeEvent(self, event):
        self._is_closing = False
        self.setWindowOpacity(1.0) # Reset for next show
        self._clipboard_manager._save()
        super().closeEvent(event)

    def on_text_changed(self, text):
        # Clear "Enter to open" hint when user starts typing
        if self._pending_open_file and text.strip():
            self._pending_open_file = None
            for i in range(self.list_widget.count()):
                w = self._unwrap_answer_widget(self.list_widget.item(i))
                if w and hasattr(w, 'set_open_hint'):
                    w.set_open_hint("")

        if self.is_history_mode:
            # User started typing a new query — exit history mode, start fresh search
            self.reset_to_search_mode(clear=False)

        # --- Command Palette: "/" prefix triggers it ---
        if text.startswith("/"):
            self._show_command_palette(text[1:])
            return
        elif self.is_command_palette:
            self.is_command_palette = False

        if not text.strip():
            self.external_actions = []
            self.external_search_results = []
            self.local_file_results = []
            self.og_data = None
            self.instant_url = None
            self.instant_calc = None
            self.refresh_list("", animate=True)
            self.frame.set_minimal_mode(True)
            self.follow_up_widget.set_mode("hidden")
            return

        # Query changed, clear external results until new ones arrive
        self.external_actions = []
        self.external_search_results = []
        self.local_file_results = []
        self.fast_chips = []
        self.og_data = None
        self.instant_calc = _detect_calc(text.strip())

        # Detect URL/domain instantly — no debounce, no API call needed
        detected = _detect_url(text.strip())
        if detected != self.instant_url:
            self.instant_url = detected
            if detected:
                # Start OGWorker immediately for richer preview later
                url, _domain = detected
                self.cleanup_worker("og_worker")
                self.og_worker = OGWorker(url, text.strip())
                self.og_worker.og_result.connect(self.on_og_result)
                self.og_worker.no_result.connect(lambda _q: None)
                self.og_worker.start()

        self.frame.set_minimal_mode(True)
        # self.refresh_list(text, animate=True)
        self.follow_up_widget.set_mode("hidden")
        self.local_search_timer.start() # Debounce local search slightly
        self.debounce_timer.start()

    # ------------------------------------------------------------------
    # Command Palette ("/" menu)
    # ------------------------------------------------------------------
    def _show_command_palette(self, filter_text=""):
        """Show the command palette with fuzzy-filtered commands."""
        self.is_command_palette = True
        self.frame.set_minimal_mode(False)
        self.follow_up_widget.set_mode("hidden")

        # Stop any pending search/action workers
        self.debounce_timer.stop()
        self.local_search_timer.stop()

        ft = filter_text.lower().strip()
        new_items_data = []

        for icon, name, desc, template, cat in self._commands:
            # Fuzzy match: filter text must appear in name, description or template
            if ft and ft not in name.lower() and ft not in desc.lower() and ft not in template.lower():
                continue
            key = f"cmd:{name}"
            data = {"type": "command_palette", "name": name, "template": template}

            def _factory(ic=icon, nm=name, ds=desc, ct=cat):
                return CommandPaletteItemWidget(ic, nm, ds, category=ct)

            new_items_data.append((key, data, _factory))

        self.sync_list_items(new_items_data)
        self.adjust_window_height(animate=True)

    def _activate_command(self, data):
        """Activate a command palette item — insert its template into the input."""
        template = data.get("template", "")
        name = data.get("name", "")

        # Special commands
        if template == "/settings":
            self.input_field.clear()
            self.is_command_palette = False
            self.enter_settings_mode()
            return
        if template == "/clipboard":
            self.input_field.clear()
            self.is_command_palette = False
            self.enter_clipboard_mode()
            return

        # Normal template: replace input text, exit palette mode
        self.is_command_palette = False

        if not template:
            # Empty template (e.g. "Ask Omni") — just clear and focus for typing
            self.input_field.blockSignals(True)
            self.input_field.clear()
            self.input_field.blockSignals(False)
            self.list_widget.clear()
            self.frame.set_minimal_mode(True)
            self.adjust_window_height(animate=True)
            self.input_field.setFocus()
            return

        # Templates with "..." placeholder — select the placeholder so user types over it
        placeholder_pos = template.find("...")
        has_placeholder = placeholder_pos >= 0

        self.input_field.blockSignals(True)
        if has_placeholder:
            # Show the template with placeholder for visual guidance
            self.input_field.setText(template)
            # Select the "..." so user's next keystroke replaces it
            self.input_field.setSelection(placeholder_pos, 3)
        else:
            self.input_field.setText(template)
            self.input_field.setCursorPosition(len(template))
            self.input_field.deselect()
        self.input_field.blockSignals(False)
        self.input_field.setFocus()

        if has_placeholder:
            # Don't trigger search yet — wait for user to fill in the placeholder
            self.list_widget.clear()
            self.frame.set_minimal_mode(True)
            self.adjust_window_height(animate=True)
            return

        # If template is a complete command (no trailing space), submit it
        if not template.endswith(" "):
            self.on_text_changed(template)
            # Trigger action immediately
            QTimer.singleShot(50, lambda: self.on_text_changed(template))
        else:
            # Trigger normal search flow
            self.on_text_changed(template)

    def refresh_list(self, query, animate=True):
        if not query:
            self.list_widget.clear()
            self.external_actions = []
            self.external_search_results = []
            self.local_file_results = []
            self.og_data = None
            self.instant_url = None
            self.instant_calc = None
            self.adjust_window_height(animate)
            return

        # Calculate new items to display
        new_items_data = [] # List of (key, data, widget_factory_func)

        # -1. Quick URL card — shown instantly when user types a domain/URL.
        #     Replaced by OGPreviewWidget once OG data arrives (different key → smooth swap).
        if self.instant_url and not self.og_data:
            _iu_url, _iu_domain = self.instant_url
            _iu_theme = getattr(self, "current_theme", "dark")

            def _create_quick_url(u=_iu_url, d=_iu_domain, th=_iu_theme):
                return QuickURLWidget(url=u, domain=d, theme=th)

            _iu_data = {"type": "quick_url", "url": _iu_url, "domain": _iu_domain}
            new_items_data.append((f"quick_url:{_iu_url}", _iu_data, _create_quick_url))

        # 0.6. OG Website Preview — for link-type actions (shown when wiki not available)
        if self.og_data:
            og_url = self.og_data.get("source_url", "")
            og_key = f"og:{og_url}"
            og_snap = self.og_data
            og_theme = getattr(self, "current_theme", "dark")

            def create_og_widget(od=og_snap, url=og_url, theme=og_theme):
                return OGPreviewWidget(od, url=url, theme=theme)

            og_item_data = {"type": "og_preview", "url": og_url}
            new_items_data.append((og_key, og_item_data, create_og_widget))

        # 0.7. Instant calc — shown immediately from client-side eval, no server round-trip needed.
        #      Uses the same key as the server's calc action so the widget is reused (not recreated)
        #      when the server response arrives with the identical result.
        if self.instant_calc:
            _ic_val, _ic_eq = self.instant_calc
            # Only show if external_actions doesn't already have a calc (server result takes precedence)
            _has_server_calc = any(a.get('type') == 'calc' for a in self.external_actions)
            if not _has_server_calc:
                _ic_data = {"type": "calc", "content": _ic_val, "equation": _ic_eq}
                _ic_key = f"calc:{_ic_val}"

                def _create_ic_widget(val=_ic_val, eq=_ic_eq):
                    return CalcActionWidget(val, eq)

                new_items_data.append((_ic_key, _ic_data, _create_ic_widget))

        # 1. External Actions (from LLM/Fast Search) — always before Wikipedia
        # Collect URLs already shown via OG preview or QuickURL to avoid duplicates
        _shown_urls = set()
        if self.instant_url:
            _shown_urls.add(self.instant_url[0])
        if self.og_data:
            _shown_urls.add(self.og_data.get("source_url", ""))

        for act in self.external_actions:
            key = self.get_item_key(act)
            if not key: continue
            # Skip link actions that duplicate the OG/QuickURL widget
            if act.get('type') == 'link' and act.get('url') in _shown_urls:
                continue

            def create_act_widget(a=act):
                if a.get('type') == 'link':
                    # Instead of ignoring search links, we display SearchActionWidget
                    from urllib.parse import urlparse as _urlp
                    _host = _urlp(a.get("url", "")).netloc.lower().replace("www.", "")
                    _root = ".".join(_host.split(".")[-2:])
                    _SEARCH_HOSTS = {
                        "duckduckgo.com", "google.com", "bing.com",
                        "search.yahoo.com", "startpage.com", "perplexity.ai",
                        "you.com", "kagi.com",
                    }
                    if _root in _SEARCH_HOSTS:
                        # Extract query if possible, else use raw query
                        import urllib.parse
                        parsed = urllib.parse.urlparse(a.get("url", ""))
                        qs = urllib.parse.parse_qs(parsed.query)
                        q = qs.get("q", [a.get("title", "").replace("Search ", "")])[0]
                        return SearchActionWidget(q)
                    return LinkActionWidget(a['title'], a['url'], a['description'])
                elif a.get('type') == 'install':
                    w = InstallActionWidget(a['name'], a.get('website'), a.get('desc', ''))
                    def _make_install_fast_cb(name, url, _w):
                        def _cb(n, _widget):
                            self._check_trust_or_prompt(
                                3, f"install {name}",
                                lambda: self.start_install(name, source_widget=_w, website_url=url),
                            )
                        return _cb
                    w.install_accepted.connect(_make_install_fast_cb(a['name'], a.get('website', ''), w))
                    return w
                elif a.get('type') == 'uninstall':
                    w = UninstallActionWidget(a['name'])
                    def _make_uninstall_fast_cb(name, _w):
                        def _cb(n, _widget):
                            self._check_trust_or_prompt(
                                3, f"uninstall {name}",
                                lambda: self.start_uninstall(name, source_widget=_w),
                            )
                        return _cb
                    w.uninstall_accepted.connect(_make_uninstall_fast_cb(a['name'], w))
                    return w
                elif a.get('type') == 'open_app':
                    w = AppActionWidget(a['name'])
                    def _make_open_app_cb(_name=a['name']):
                        def _cb(name, _widget):
                            self._cancel_all_workers()
                            find_and_launch_app(name)
                            self._reset_on_next_show = True
                            self.animate_close()
                        return _cb
                    w.app_accepted.connect(_make_open_app_cb())
                    return w
                elif a.get('type') == 'person':
                    w = PersonActionWidget(a['name'], a['description'], a.get('image'), a.get('url'))
                    if not a.get('image'):
                        QTimer.singleShot(200, lambda _w=w, _n=a['name']: _w.fetch_image_for_name(_n))
                    return w
                elif a.get('type') == 'place':
                    # Use PlaceActionWidget for rich place cards (images + map)
                    return PlaceActionWidget(
                        a['name'], 
                        a.get('address'), 
                        a.get('image'), 
                        a.get('url'), 
                        a.get('latitude'), 
                        a.get('longitude'),
                        rating=a.get('rating'),
                        rating_count=a.get('rating_count'),
                        category=a.get('category'),
                        phone=a.get('phone'),
                        hours=a.get('hours')
                    )
                elif a.get('type') == 'status':
                    return StandardItemWidget(a['description'], icon_name="dialog-information")
                elif a.get('type') == 'calc':
                    return CalcActionWidget(a['content'], a.get('equation', ''))
                elif a.get('type') == 'currency':
                    return CurrencyActionWidget(a.get('amount', '0'), a.get('from_unit', ''), a.get('to_unit', ''), a.get('converted_value', ''))
                elif a.get('type') == 'world_time':
                    return WorldTimeWidget(a.get('city', ''), a.get('timezone', ''), a.get('current_time', ''), a.get('date', ''))
                elif a.get('type') == 'weather':
                    return WeatherActionWidget(a.get('location', ''), a.get('temp', ''), a.get('condition', ''))
                elif a.get('type') == 'unit':
                    return UnitActionWidget(a.get('amount', '0'), a.get('from_unit', ''), a.get('to_unit', ''), a.get('converted_value', ''))
                elif a.get('type') == 'translate':
                    return TranslateActionWidget(a.get('source_text', ''), a.get('from_lang', ''), a.get('to_lang', ''), a.get('translated_text', ''))
                elif a.get('type') == 'color_preview':
                    return ColorActionWidget(a.get('color_hex', ''), a.get('rgb_val', ''), a.get('hsl_val', ''))
                elif a.get('type') == 'timer':
                    return TimerActionWidget(a.get('duration', 0))
                elif a.get('type') == 'password':
                    return PasswordActionWidget(a.get('length', 16), a.get('pwd', None))
                elif a.get('type') == 'qrcode':
                    return QRActionWidget(a.get('data', ''))
                elif a.get('type') == 'calendar':
                    return CalendarActionWidget(a.get('events_text', ''))
                elif a.get('type') == 'emails':
                    return EmailActionWidget(a.get('emails_text', ''))
                elif a.get('type') == 'send_email_draft':
                    return SendEmailWidget(a.get('to', ''), a.get('subject', ''), a.get('body', ''), original_query=a.get('original_query', ''))
                elif a.get('type') == 'answer':
                    return AnswerActionWidget(a.get('text', ''))
                elif a.get('type') == 'tool_draft':
                    w = ToolDraftWidget(a.get('tool_name', ''), a.get('args', {}), original_query=a.get('original_query', ''))
                    def _make_draft_exec_cb2(_widget):
                        def _cb(tool_name, args):
                            self._execute_tool_draft(tool_name, args, _widget)
                        return _cb
                    w.execute_requested.connect(_make_draft_exec_cb2(w))
                    return w
                elif a.get('type') == 'organize_pending':
                    return PendingActionWidget(a.get('title', 'Organize folder'), a.get('path', ''), header_text="ORGANIZE")
                elif a.get('type') == 'action_pending':
                    return PendingActionWidget(a.get('title', 'Searching the web'), a.get('subtitle', ''), header_text="SEARCHING WEB")
                elif a.get('type') == 'place_pending':
                    return PendingActionWidget("Searching for place", a.get('name', ''), header_text="SEARCHING PLACE")
                return StandardItemWidget(str(a))
            
            new_items_data.append((key, act, create_act_widget))

        # 2. Local Apps (Fast)
        query_lower = query.lower()
        matches = []
        for name, data in self.apps.items():
            if query_lower in name:
                matches.append((name, data))
        
        # Sort matches: exact/prefix first, then stable by key
        matches.sort(key=lambda x: (0 if x[0].startswith(query_lower) else 1, x[0]))
        
        # Deduplicate based on orig_name to prevent "Settings: Appearance" appearing twice
        seen_orig_names = set()
        unique_matches = []
        for name, data in matches:
            if data['orig_name'] in seen_orig_names:
                continue
            seen_orig_names.add(data['orig_name'])
            unique_matches.append((name, data))
        
        settings_matches = []
        app_matches = []
        
        # Heuristic: Is the user explicitly asking for settings?
        # Check if query starts with "settings", "system settings", or "preferences"
        is_explicit_settings = (query_lower.startswith('settings') or 
                               query_lower.startswith('system settings') or 
                               query_lower.startswith('preferences'))
                               
        for name, data in unique_matches:
            is_settings = str(data.get('orig_name', '')).startswith('Settings:')
            
            if is_settings:
                # If explicit "settings" query, add all settings matches
                if is_explicit_settings:
                    settings_matches.append((name, data))
                else:
                    # If NOT explicit, only add if the match is a "strong" match for the section name
                    # e.g. "wifi" -> "Settings: Wi-Fi" is good.
                    # "a" -> "Settings: Accessibility" is weak/spammy.
                    # We define "strong" as: query is a prefix of the section name (stripped of "Settings: ")
                    section_name = data.get('orig_name', '').replace('Settings: ', '').lower()
                    if section_name.startswith(query_lower):
                        settings_matches.append((name, data))
            else:
                app_matches.append((name, data))

        max_app_results = 5
        if is_explicit_settings:
            max_app_results = 12
            # Prioritize Settings if explicit
            final_matches = settings_matches + app_matches
        else:
            # Prioritize Apps if implicit (e.g. "a" -> Arc, App Store, THEN Settings: Accessibility)
            final_matches = app_matches + settings_matches

        for name, data in final_matches[:max_app_results]:
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
        # if query:
        #     key = "ask_omni"
        #     is_only_item = (len(new_items_data) == 0)
        #     data = {"type": "ask_omni", "query": query, "is_only_item": is_only_item}
        #     
        #     def create_omni_widget(q=query):
        #         return StandardItemWidget(f"Ask Omni: {q}", icon_name=LOGO_PATH)
        #     
        #     new_items_data.append((key, data, create_omni_widget))

        self.sync_list_items(new_items_data)
        self.adjust_window_height(animate)

    def get_item_key(self, data):
        if not isinstance(data, dict): return None
        if data.get('type') == 'og_preview': return f"og:{data.get('url', '')}"
        if data.get('type') == 'quick_url': return f"quick_url:{data.get('url', '')}"
        if data.get('type') == 'ask_omni': return 'ask_omni'
        if data.get('type') == 'action_pending': return f"action_pending:{data.get('pending_id') or data.get('subtitle') or ''}"
        if 'orig_name' in data and 'cmd' in data: return f"app:{data['orig_name']}" # App
        if data.get('type') == 'open_file': return f"file:{data.get('path')}"
        if data.get('type') == 'link': return f"link:{data.get('url')}"
        if data.get('type') == 'person': return f"person:{data.get('url') or data.get('name')}"
        if data.get('type') == 'place': return f"place:{data.get('url') or data.get('name')}"
        if data.get('type') == 'calc': return f"calc:{data.get('content')}"
        if data.get('type') == 'currency': return f"currency:{data.get('amount')}_{data.get('from_unit')}"
        if data.get('type') == 'weather': return f"weather:{data.get('location')}"
        if data.get('type') == 'unit': return f"unit:{data.get('amount')}_{data.get('from_unit')}"
        if data.get('type') == 'translate': return f"translate:{data.get('source_text')}_{data.get('to_lang')}"
        if data.get('type') == 'color_preview': return 'color_preview'
        if data.get('type') == 'timer': return f"timer:{data.get('duration')}"
        if data.get('type') == 'password': return f"password:{data.get('length')}"
        if data.get('type') == 'qrcode': return f"qrcode:{data.get('data')}"
        if data.get('type') == 'install': return f"install:{data.get('name')}"
        if data.get('type') == 'uninstall': return f"uninstall:{data.get('name')}"
        if data.get('type') == 'command_palette': return f"cmd:{data.get('name')}"
        if data.get('type') == 'calendar': return 'calendar'
        if data.get('type') == 'emails': return 'emails'
        if data.get('type') == 'answer': return f"answer:{data.get('text', '')[:50]}"
        if data.get('type') == 'organize_pending': return f"organize:{data.get('path', '')}"
        if data.get('type') == 'send_email_draft': return 'send_email'
        if data.get('type') == 'tool_draft': return f"tool_draft:{data.get('tool_name', '')}:{str(data.get('args', ''))[:30]}"
        # Fallback for others
        return str(data)

    def sync_list_items(self, new_items_data):
        # Block signals to prevent selection jumping/flickering during updates
        self.list_widget.blockSignals(True)
        try:
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
                    taken = self.list_widget.takeItem(i)
                    if taken:
                        w = self.list_widget.itemWidget(taken)
                        if w: w.deleteLater()

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
                        elif hasattr(real_widget, 'update_content'):
                            real_widget.update_content(data)
                    
                    # Update data just in case
                    current_item.setData(Qt.ItemDataRole.UserRole, data)
                    
                else:
                    # MISMATCH
                    if key in existing:
                        # Exists elsewhere: Move it here (Slide effect by skipping animation)
                        old_item = existing[key]
                        row = self.list_widget.row(old_item)
                        taken = self.list_widget.takeItem(row) # Remove from old pos
                        if taken:
                            w = self.list_widget.itemWidget(taken)
                            if w: w.deleteLater()
                        
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

            # Always select the first item if there's a selection available
            if self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(0)
                
        finally:
            self.list_widget.blockSignals(False)

    def add_list_item(self, widget, data, animation="fade"):
        if hasattr(widget, 'set_theme'):
            widget.set_theme(self.current_theme)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, data)

        _NON_SELECTABLE_STR = {"thinking", "answer", "separator", "history_ai"}
        _NON_SELECTABLE_DICT_TYPES = {"system_settings"}
        if (isinstance(data, str) and data in _NON_SELECTABLE_STR) or \
           (isinstance(data, dict) and data.get('type') in _NON_SELECTABLE_DICT_TYPES):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        if animation == "slide":
            target_h = widget.sizeHint().height()
            item.setSizeHint(QSize(-1, 0))
            self.list_widget.addItem(item)
            anim_w = SmoothEntryWidget(widget, animation="slide",
                                       list_item=item, target_height=target_h)
        else:
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            anim_w = SmoothEntryWidget(widget, animation=animation)

        self.list_widget.setItemWidget(item, anim_w)

    def insert_list_item(self, index, widget, data, animation="fade"):
        if hasattr(widget, 'set_theme'):
            widget.set_theme(self.current_theme)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, data)

        _NON_SELECTABLE_STR = {"thinking", "answer", "separator", "history_ai"}
        _NON_SELECTABLE_DICT_TYPES = {"system_settings"}
        if (isinstance(data, str) and data in _NON_SELECTABLE_STR) or \
           (isinstance(data, dict) and data.get('type') in _NON_SELECTABLE_DICT_TYPES):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        if animation == "slide":
            target_h = widget.sizeHint().height()
            item.setSizeHint(QSize(-1, 0))
            self.list_widget.insertItem(index, item)
            anim_w = SmoothEntryWidget(widget, animation="slide",
                                       list_item=item, target_height=target_h)
        else:
            item.setSizeHint(widget.sizeHint())
            self.list_widget.insertItem(index, item)
            anim_w = SmoothEntryWidget(widget, animation=animation)

        self.list_widget.setItemWidget(item, anim_w)

    def _trigger_prefetch(self):
        """Pre-fetch contextual data based on the active app (runs in background thread)."""
        import threading

        def _do_prefetch():
            try:
                from src.services.system.context_detector import get_active_app_context
                from src.services.llm import model_manager

                hints = get_active_app_context()
                if not hints:
                    return

                for hint in hints:
                    # Skip if already cached and fresh
                    if model_manager.prefetch_get(hint) is not None:
                        continue

                    if hint == "calendar_events":
                        try:
                            from src.services.llm.tools import execute_tool
                            result = execute_tool("get_calendar_events", {"days": 3})
                            model_manager.prefetch_set("calendar_events", result)
                            logging.info("[PREFETCH] Cached calendar events")
                        except Exception as e:
                            logging.warning(f"[PREFETCH] Calendar failed: {e}")

                    elif hint == "unread_emails":
                        try:
                            from src.services.llm.tools import execute_tool
                            result = execute_tool("get_unread_emails", {"limit": 5})
                            model_manager.prefetch_set("unread_emails", result)
                            logging.info("[PREFETCH] Cached unread emails")
                        except Exception as e:
                            logging.warning(f"[PREFETCH] Emails failed: {e}")

            except Exception as e:
                logging.warning(f"[PREFETCH] Context detection failed: {e}")

        threading.Thread(target=_do_prefetch, daemon=True).start()

    def cleanup_worker(self, attr_name):
        """Safely cleanup a worker thread by keeping a reference if it's still running."""
        worker = getattr(self, attr_name, None)
        if worker:
            # Disconnect known signals to avoid side effects and C++ "destroyed signal" warnings
            signals = [
                'results_found', 'action_found', 'searching', 'wiki_result', 'no_result',
                'og_result', 'finished', 'failed', 'partial_response',
                'finished_speaking', 'error'
            ]
            for sig in signals:
                if hasattr(worker, sig):
                    try: 
                        getattr(worker, sig).disconnect()
                    except (TypeError, RuntimeError): 
                        pass
            
            if worker.isRunning():
                self.old_workers.append(worker)
                # Connect finished signal to remove from old_workers list
                # Use default arg to capture 'worker' variable
                worker.finished.connect(lambda w=worker: self.old_workers.remove(w) if w in self.old_workers else None)
            
            setattr(self, attr_name, None)

    def _cancel_all_workers(self):
        """Stop all in-flight workers and abort the fast model.
        Sets _reset_on_next_show so the query is cleared when the window reopens."""
        import src.services.llm.model_manager as mm
        mm.abort_fast_event.set()
        for w in ('action_worker', 'search_worker', 'file_search_worker', 'ai_worker', 'wiki_worker', 'og_worker'):
            self.cleanup_worker(w)
        self._reset_on_next_show = True

    def perform_local_search(self):
        """Debounced local search to prevent UI freezing while typing."""
        text = self.input_field.text()
        self.refresh_list(text, animate=True)

    def trigger_async_searches(self):
        query = self.input_field.text().strip()
        if not query: return

        # Start Search Worker
        self.cleanup_worker('search_worker')
        self.search_worker = SearchWorker(query)
        self.search_worker.results_found.connect(self.on_search_results)
        self.search_worker.start()

        # Start Action Worker (SSE streaming for progressive UI)
        self.cleanup_worker('action_worker')
        self.action_worker = ActionWorker(query, use_stream=True)
        self.action_worker.action_found.connect(self.on_action_found)
        self.action_worker.searching.connect(self.on_action_searching)
        self.action_worker.start()

        # Start File Search Worker - OPTIMIZED FOR SPEED
        self.cleanup_worker('file_search_worker')
        self.file_search_worker = FileSearchWorker(query, max_results=8)  # Reduced for speed
        self.file_search_worker.results_found.connect(self.on_file_search_results)
        self.file_search_worker.start()

    def on_search_results(self, results, query):
        if self.input_field.text().strip() != query: return

        self.external_search_results = results
        self.refresh_list(query, animate=False)

    def on_action_searching(self, search_query, original_query):
        """Handle intermediate 'searching' event from SSE — show skeleton immediately."""
        if self.input_field.text().strip() != original_query:
            return
        if self.ai_worker and self.ai_worker.isRunning():
            return
        # Show a searching skeleton as an intermediate action
        searching_action = {"type": "action_pending", "title": "Searching the web", "subtitle": search_query}
        self.external_actions = [searching_action]
        self.last_action_time = time.time()
        self.refresh_list(original_query, animate=False)

    def on_action_found(self, actions, chips, query):
        if self.input_field.text().strip() != query: return

        # --- NEW: Filter out pending places and start async workers ---
        final_actions = []
        has_pending_place = False
        for a in actions:
            if a.get('type') == 'place_pending':
                has_pending_place = True
                name = a.get('name')
                if not hasattr(self, 'place_workers'): self.place_workers = {}
                
                # If we already have a worker for this place, just add the pending action (skeleton)
                if name in self.place_workers:
                    final_actions.append(a)
                    continue
                
                worker = PlaceResolverWorker(name)
                worker.place_resolved.connect(self.on_place_resolved)
                worker.finished.connect(lambda n=name: self.cleanup_place_worker(n))
                self.place_workers[name] = worker
                worker.start()
                
                # Add the pending action to the list so we show a skeleton
                final_actions.append(a)
                continue
            final_actions.append(a)
        actions = final_actions
        # --------------------------------------------------------------

        # Sort external actions to prioritize interactive cards
        def action_priority(a):
            t = a.get('type')
            if t in ('currency', 'calc', 'translate', 'system_settings', 'status', 'weather', 'unit', 'color_preview', 'timer', 'password', 'qrcode', 'calendar', 'emails', 'answer', 'organize_pending', 'send_email_draft', 'tool_draft'): return 0
            if t == 'link':
                try:
                    from urllib.parse import urlparse as _urlp
                    _host = _urlp(a.get("url", "")).netloc.lower().replace("www.", "")
                    _root = ".".join(_host.split(".")[-2:])
                    _SEARCH_HOSTS = {"duckduckgo.com", "google.com", "bing.com", "search.yahoo.com", "startpage.com", "perplexity.ai", "you.com", "kagi.com"}
                    if _root in _SEARCH_HOSTS: return 0
                except: pass
            if t in ('place', 'person'): return 1
            return 2

        actions.sort(key=action_priority)
        self.external_actions = actions
        self.og_data = None   # reset previous OG data when action changes
        self.last_action_time = time.time()
        self.refresh_list(query, animate=False)

        # Ensure window is active when actions arrive to prevent accidental close on focus loss
        if not self.isActiveWindow():
            self.activateWindow()
            self.raise_()

    def on_place_resolved(self, action, original_name):
        """Handle async place resolution."""
        if not action or action.get('type') != 'place': return

        # Reset grace period so image-download focus blips don't close the window
        self.last_action_time = time.time()

        # Check if we should add this action
        if not hasattr(self, 'external_actions'): self.external_actions = []
        
        # Remove the pending action for this place if it exists
        self.external_actions = [a for a in self.external_actions 
                                 if not (a.get('type') == 'place_pending' and a.get('name') == original_name)]
        
        # Avoid duplicates
        for a in self.external_actions:
            if a.get('type') == 'place' and a.get('name') == action.get('name'):
                return

        self.external_actions.append(action)
        
        # Re-sort using same logic
        def action_priority(a):
            t = a.get('type')
            if t in ('currency', 'calc', 'translate', 'system_settings', 'status', 'weather', 'unit', 'color_preview', 'timer', 'password', 'qrcode', 'calendar', 'emails', 'answer', 'organize_pending', 'send_email_draft', 'tool_draft'): return 0
            if t == 'link':
                try:
                    from urllib.parse import urlparse as _urlp
                    _host = _urlp(a.get("url", "")).netloc.lower().replace("www.", "")
                    _root = ".".join(_host.split(".")[-2:])
                    _SEARCH_HOSTS = {"duckduckgo.com", "google.com", "bing.com", "search.yahoo.com", "startpage.com", "perplexity.ai", "you.com", "kagi.com"}
                    if _root in _SEARCH_HOSTS: return 0
                except: pass
            if t in ('place', 'person'): return 1
            return 2
            
        self.external_actions.sort(key=action_priority)
        self.refresh_list(self.input_field.text(), animate=False)

        # Bring window to front if it's already showing (don't force-show a hidden window)
        if self.isVisible() and not self.isActiveWindow():
            self.activateWindow()
            self.raise_()

    def cleanup_place_worker(self, name):
        if hasattr(self, 'place_workers') and name in self.place_workers:
            del self.place_workers[name]

    def on_og_result(self, og_data: dict, query: str):
        """Handle Open Graph metadata — show website preview card."""
        if self.input_field.text().strip() != query:
            return
        self.og_data = og_data
        self.refresh_list(query, animate=False)

    def on_file_search_results(self, results, query):
        """Handle file search results from the file search worker."""
        if self.input_field.text().strip() != query: return

        # Store in separate list to avoid overwrite by slow search_worker
        self.local_file_results = results
        self.refresh_list(query, animate=False)

    def _on_chip_clicked(self, action: dict):
        """Execute a fast action chip's action."""
        action_type = action.get("type")
        if action_type == "link":
            url = action.get("url", "")
            if url:
                QDesktopServices.openUrl(QUrl(url))
                self.animate_close()
        elif action_type == "copy_text":
            text = action.get("text", "")
            if text:
                QGuiApplication.clipboard().setText(text)
        elif action_type == "ask_omni":
            q = action.get("query", "")
            if q:
                self.input_field.setText(q)
                self._trigger_immediate_fast_action(q)
        else:
            logging.warning(f"Unknown chip action type: {action_type}")

    def on_entered(self, item=None):
        # We assume manual entry if on_entered is called
        # If it was voice, voice_triggered_query would be set by handle_ipc_query or toggle_listening
        # But if user types and hits enter, we should clear it.
        # Wait, handle_ipc_query calls perform_ai_query directly.
        # So on_entered is only for manual keyboard interaction.
        self.voice_triggered_query = False

        if not item:
            item = self.list_widget.currentItem()

        # Command palette mode: activate the selected command
        if self.is_command_palette and item:
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('type') == 'command_palette':
                self._activate_command(data)
                return

        # Clipboard mode: single click copies to clipboard (no paste, no close)
        # Double click is handled by _on_clipboard_double_click (copy + paste)
        if self.is_clipboard_mode and item:
            self._handle_clipboard_item_selected(item, paste=False)
            return
        
        # "Enter to open" — if input is empty and a file hint is pending, open it
        if not self.input_field.text().strip() and self._pending_open_file:
            try:
                import platform
                fp = self._pending_open_file
                self._pending_open_file = None
                if platform.system() == 'Darwin':
                    # Reveal archives in Finder instead of opening (extracting) them
                    if fp.lower().endswith(('.zip', '.tar', '.gz', '.bz2', '.7z', '.rar', '.xz')):
                        subprocess.Popen(['open', '-R', fp])
                    else:
                        subprocess.Popen(['open', fp])
                elif platform.system() == 'Windows':
                    os.startfile(fp)
                else:
                    subprocess.Popen(['xdg-open', fp])
                QTimer.singleShot(500, self.animate_close)
            except Exception as e:
                logging.error(f"Failed to open pending file: {e}")
            return

        # If no item selected (Enter in box), activate first result or trigger fast action
        if not item:
            query = self.input_field.text().strip()
            if query:
                self._activate_first_or_search(query)
            return

        data = item.data(Qt.ItemDataRole.UserRole)
        
        if isinstance(data, dict):
            if data.get('type') == 'quick_url':
                url = data.get('url', '')
                if url:
                    QDesktopServices.openUrl(QUrl(url))
                    self.animate_close()
                return
            elif data.get('type') == 'og_preview':
                url = data.get('url', '')
                if url:
                    QDesktopServices.openUrl(QUrl(url))
                    self.animate_close()
                return
            elif data.get('type') == 'wiki_card':
                # Enter on wiki card opens the Wikipedia article
                url = data.get('url', '')
                if url:
                    QDesktopServices.openUrl(QUrl(url))
                    self.animate_close()
                return
            elif data.get('type') == 'link':
                QDesktopServices.openUrl(QUrl(data['url']))
                self.animate_close()
            elif data.get('type') == 'person':
                url = data.get('url')
                if url:
                    QDesktopServices.openUrl(QUrl(url))
                self.animate_close()
            elif data.get('type') == 'place':
                # Open directions on Enter
                lat = data.get('latitude')
                lon = data.get('longitude')
                name = data.get('name', '')
                if lat and lon:
                    url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
                else:
                    query = name.replace(" ", "+")
                    url = f"https://www.google.com/maps/dir/?api=1&destination={query}"
                QDesktopServices.openUrl(QUrl(url))
                self.animate_close()
            elif data.get('type') == 'install':
                # Enter confirms the install — same as clicking "Yes, Install"
                container = self.list_widget.itemWidget(item)
                if hasattr(container, 'content_widget') and isinstance(container.content_widget, InstallActionWidget):
                    container.content_widget.accept_install()
                return
            elif data.get('type') == 'uninstall':
                # Uninstallation is handled interactively by the UninstallActionWidget's yes/no buttons.
                return
            elif data.get('type') == 'open_app':
                # Launch App (from Regex Shortcut)
                app_name = data.get('name')
                self._cancel_all_workers()
                find_and_launch_app(app_name)
                self._reset_on_next_show = True
                self.animate_close()
            elif data.get('type') == 'open_file':
                # Open file in default application
                try:
                    file_path = data['path']
                    import platform
                    if platform.system() == 'Windows':
                        # Windows: Use os.startfile
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
                    logging.info(f"Executing command: {data['cmd']}")
                    subprocess.Popen(data['cmd'], shell=True)
                    self._reset_on_next_show = True
                    self.animate_close()
                except Exception as e:
                    logging.error(f"Failed to execute command '{data['cmd']}': {e}")
            elif data.get('type') == 'ask_omni':
                q = data.get('query', '')
                if q:
                    self.input_field.setText(q)
                    self._trigger_immediate_fast_action(q)
            elif data.get('type') == 'translate':
                text = data.get('translated_text', '')
                if text:
                    QGuiApplication.clipboard().setText(text)
                self.animate_close()
            elif data.get('type') == 'currency':
                text = data.get('converted_value', '')
                if text:
                    QGuiApplication.clipboard().setText(text)
                self.animate_close()
            elif data.get('type') == 'answer':
                text = data.get('text', '')
                if text:
                    QGuiApplication.clipboard().setText(text)
                self.animate_close()
            elif data.get('type') == 'calendar':
                # Open Calendar app
                try:
                    subprocess.Popen(['open', '-a', 'Calendar'])
                except Exception:
                    pass
                self.animate_close()
            elif data.get('type') == 'emails':
                # Open Mail app
                try:
                    subprocess.Popen(['open', '-a', 'Mail'])
                except Exception:
                    pass
                self.animate_close()
            elif data.get('type') == 'send_email_draft':
                container = self.list_widget.itemWidget(item)
                w = getattr(container, 'content_widget', container)
                if isinstance(w, SendEmailWidget):
                    if w._composing:
                        pass  # composing in progress, wait
                    elif not w._compose_done and w._original_query:
                        # First Enter: start AI compose
                        w.start_compose()
                    else:
                        w._send()  # handles validation & field focus
                return
            elif data.get('type') == 'tool_draft':
                container = self.list_widget.itemWidget(item)
                w = getattr(container, 'content_widget', container)
                if isinstance(w, ToolDraftWidget) and not w._executed:
                    w._on_execute()
                return
            elif data.get('type') == 'organize_pending':
                # Trigger organize via the widget's confirm button
                container = self.list_widget.itemWidget(item)
                if hasattr(container, 'content_widget'):
                    w = container.content_widget
                    if hasattr(w, 'accept_install'):
                        w.accept_install()
                return
        else:
            # Fallback
            query = self.input_field.text().strip()
            if query: self._activate_first_or_search(query)
    
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

    # ── Trust level helpers ───────────────────────────────────────────────────

    def _check_trust_or_prompt(self, required_level: int, description: str, on_allow):
        """Show a permission popup if the current trust level is below *required_level*.

        If trust is sufficient, calls *on_allow* immediately.
        If not, shows TrustPermissionPopup; calls *on_allow* only if the user clicks
        "Allow once", and opens Trust settings on the "Always allow" link.
        """
        current = settings_store.get("trust_level", 1)
        if current >= required_level:
            on_allow()
            return

        theme = getattr(self, "current_theme", "dark")
        popup = TrustPermissionPopup(
            required_level=required_level,
            description=description,
            theme=theme,
            parent=self.frame,
        )
        popup.setGeometry(self.frame.rect())
        popup.allowed.connect(on_allow)
        popup.open_settings.connect(self._navigate_to_trust_settings)
        popup.show_animated()

    def _navigate_to_trust_settings(self):
        """Open settings panel and navigate to the Trust page."""
        if not self.is_settings_mode:
            self.enter_settings_mode()
        QTimer.singleShot(120, lambda: self._focus_settings_page("Trust"))

    def _focus_settings_page(self, page_name: str):
        """Select a page by name in the settings sidebar."""
        try:
            panel = self.settings_panel
            for i in range(panel.sidebar.count()):
                if panel.sidebar.item(i).text() == page_name:
                    panel.sidebar.setCurrentRow(i)
                    break
        except Exception:
            pass

    # ── Trusted terminal execution (one-time permission) ──────────────────────

    def _run_trusted_terminal(self, command: str, insert_pos: int = -1) -> str:
        """Run a terminal command granted one-time trust and return output."""
        import subprocess
        import logging
        try:
            proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            if proc.returncode != 0:
                logging.error(f"Trusted terminal command failed with exit code {proc.returncode}: {stderr}")
                return stderr or f"Command failed (exit code {proc.returncode})"
            return stdout or "Done."
        except subprocess.TimeoutExpired:
            logging.error(f"Trusted terminal command timed out: {command}")
            return "Command timed out."
        except Exception as e:
            logging.error(f"Error executing trusted terminal command: {e}")
            return f"Error: {e}"

    # ── Computer control execution ────────────────────────────────────────────

    def _execute_tool_draft(self, tool_name: str, args: dict, widget):
        """Execute a deferred tool draft in a background thread."""
        import threading
        def _run():
            try:
                from src.services.llm.tools import execute_tool_draft
                result = execute_tool_draft(tool_name, args)
                success = not result.startswith("Error")
                self._tool_draft_done_signal.emit(widget, result, success)
            except Exception as e:
                logging.error(f"[tool_draft] Execution error: {e}")
                self._tool_draft_done_signal.emit(widget, str(e), False)
        threading.Thread(target=_run, daemon=True).start()

    def _on_tool_draft_done(self, widget, result: str, success: bool):
        """Handle tool draft completion on the main thread."""
        try:
            widget.show_result(result, success)
            self.adjust_window_height()
        except RuntimeError:
            pass  # widget deleted

    def _execute_computer_control(self, act: dict):
        """Close the window then run a single computer_control action."""
        # Wrap single action in a list for the worker
        actions = [act]

        def _run():
            self._cc_worker = ComputerControlWorker(actions, delay_before_start=0.3)
            self._cc_worker.all_done.connect(lambda: logging.info("[CC] All actions done"))
            self._cc_worker.error.connect(lambda e: logging.error(f"[CC] Error: {e}"))
            self._cc_worker.start()

        # Close window first so it doesn't block the screen, then run
        self.animate_close()
        QTimer.singleShot(500, _run)

    def start_install(self, app_name, source_widget=None, website_url=""):
        if not source_widget:
            self.list_widget.clear()
        else:
            # Remove any stale install_progress items (e.g. from a previous failed attempt)
            for i in reversed(range(self.list_widget.count())):
                item = self.list_widget.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == "install_progress":
                    w = self.list_widget.itemWidget(item)
                    if hasattr(w, 'content_widget'):
                        w = w.content_widget
                    if w is not source_widget:
                        self.list_widget.takeItem(i)
        # Do NOT touch input_field text — it would create ghost user bubbles
        self.input_field.setDisabled(True)
        self._installing_app_name = app_name

        # Add Progress Widget
        current_theme = getattr(self, '_current_theme', 'dark')
        self.install_widget = InstallProgressWidget(app_name, website_url=website_url, theme=current_theme)
        self.install_widget.candidate_confirmed.connect(self.on_install_candidate_confirmed)
        
        if source_widget:
            # Try to find the list item containing the source_widget and replace it
            found_item = None
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                w = self.list_widget.itemWidget(item)
                # Unwrap SmoothEntryWidget if present
                if hasattr(w, 'content_widget'):
                    if w.content_widget is source_widget:
                        found_item = item
                        break
                elif w is source_widget:
                    found_item = item
                    break

            if found_item:
                found_item.setData(Qt.ItemDataRole.UserRole, "install_progress")
                found_item.setSizeHint(self.install_widget.sizeHint())
                anim_w = SmoothEntryWidget(self.install_widget, animate=True)
                self.list_widget.setItemWidget(found_item, anim_w)
                QTimer.singleShot(80, lambda: self.adjust_window_height(animate=True))
            else:
                if self.is_history_mode:
                    self.insert_list_item(0, self.install_widget, "install_progress", animation="fade")
                else:
                    self.add_list_item(self.install_widget, "install_progress", animation="fade")
                QTimer.singleShot(80, lambda: self.adjust_window_height(animate=True))
                if self.is_history_mode:
                    QTimer.singleShot(120, lambda: self.list_widget.scrollToItem(
                        self.list_widget.item(0), QAbstractItemView.ScrollHint.PositionAtTop
                    ))
        else:
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
        self.install_worker.finished.connect(self._on_install_finished)
        self.install_worker.start()

    def _on_install_finished(self, success, msg):
        self.input_field.setDisabled(False)
        self.input_field.setPlaceholderText("Ask a follow-up...")
        self.input_field.setFocus()
        app_name = getattr(self, '_installing_app_name', 'the app')
        display_name = app_name.replace('-', ' ').title()
        if success:
            # Refresh app cache so the newly installed app is discoverable immediately
            try:
                import src.services.system.app_launcher as _al
                _al.APP_CACHE = None
            except Exception:
                pass
            self.perform_silent_ai_query(
                f"[SYSTEM] Installation of {display_name} has just completed successfully. "
                f"Confirm this to the user in one short friendly sentence and suggest a next step (e.g. open the app)."
            )
        else:
            self.perform_silent_ai_query(
                f"[SYSTEM] Installation of {display_name} failed. Error: {msg}. "
                f"Inform the user briefly and suggest what they can do to fix the issue."
            )
        
    def on_install_candidate_confirmed(self, pkg_data):
        # Reset widget state before restarting (clears any previous failure styling)
        if hasattr(self, 'install_widget'):
            self.install_widget.reset()
        # Restart orchestrator with forced package
        self.install_worker = InstallOrchestrator(pkg_data['name'], forced_package=pkg_data)
        self.install_worker.status_update.connect(self.install_widget.update_status)
        self.install_worker.log_entry.connect(self.install_widget.add_log)
        self.install_worker.finished.connect(self.install_widget.set_finished)
        self.install_worker.finished.connect(self._on_install_finished)  # was missing — caused input to stay disabled
        self.install_worker.start()

    def start_uninstall(self, app_name, source_widget=None):
        if not source_widget:
            self.list_widget.clear()
        # Do NOT touch input_field text — it would create ghost user bubbles
        self.input_field.setDisabled(True)
        self._uninstalling_app_name = app_name

        # Add Progress Widget
        current_theme = getattr(self, '_current_theme', 'dark')
        self.uninstall_widget = UninstallProgressWidget(app_name, theme=current_theme)

        if source_widget:
            # Try to find the list item containing the source_widget and replace it
            found_item = None
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                w = self.list_widget.itemWidget(item)
                # Unwrap SmoothEntryWidget if present
                if hasattr(w, 'content_widget'):
                    if w.content_widget is source_widget:
                        found_item = item
                        break
                elif w is source_widget:
                    found_item = item
                    break

            if found_item:
                found_item.setData(Qt.ItemDataRole.UserRole, "uninstall_progress")
                found_item.setSizeHint(self.uninstall_widget.sizeHint())
                anim_w = SmoothEntryWidget(self.uninstall_widget, animate=True)
                self.list_widget.setItemWidget(found_item, anim_w)
                QTimer.singleShot(80, lambda: self.adjust_window_height(animate=True))
            else:
                if self.is_history_mode:
                    self.insert_list_item(0, self.uninstall_widget, "uninstall_progress", animation="fade")
                else:
                    self.add_list_item(self.uninstall_widget, "uninstall_progress", animation="fade")
                QTimer.singleShot(80, lambda: self.adjust_window_height(animate=True))
                if self.is_history_mode:
                    QTimer.singleShot(120, lambda: self.list_widget.scrollToItem(
                        self.list_widget.item(0), QAbstractItemView.ScrollHint.PositionAtTop
                    ))
        else:
            item = QListWidgetItem()
            item.setSizeHint(self.uninstall_widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, self.uninstall_widget)
            self.adjust_window_height()

        # Start Orchestrator
        self.uninstall_worker = UninstallOrchestrator(app_name)
        self.uninstall_worker.status_update.connect(self.uninstall_widget.update_status)
        self.uninstall_worker.log_entry.connect(self.uninstall_widget.add_log)
        self.uninstall_worker.finished.connect(self.uninstall_widget.set_finished)
        self.uninstall_worker.finished.connect(self._on_uninstall_finished)
        self.uninstall_worker.start()

    def _on_uninstall_finished(self, success, msg):
        self.input_field.setDisabled(False)
        self.input_field.setPlaceholderText("Ask a follow-up...")
        self.input_field.setFocus()
        app_name = getattr(self, '_uninstalling_app_name', 'the app')
        display_name = app_name.replace('-', ' ').title()
        if success:
            self.perform_silent_ai_query(
                f"[SYSTEM] Uninstallation of {display_name} has just completed successfully. "
                f"Confirm this to the user in one short friendly sentence."
            )
        else:
            self.perform_silent_ai_query(
                f"[SYSTEM] Uninstallation of {display_name} failed. Error: {msg}. "
                f"Inform the user briefly and suggest what they can try."
            )

    def abort_ai_generation(self):
        """Abort current AI generation and restore UI state."""
        if hasattr(self, 'ai_worker') and self.ai_worker and self.ai_worker.isRunning():
            logging.info("Aborting AI stream...")
            
            # Abort the worker
            import src.services.llm.model_manager as mm
            mm.abort_fast_event.set()
            
            # Force stop worker thread safely
            try:
                self.ai_worker.finished.disconnect()
                self.ai_worker.partial_response.disconnect()
            except: pass
            
            # Use terminate() but don't wait() immediately on main thread to avoid freeze
            # if the worker is stuck in a GIL-holding operation or I/O
            self.ai_worker.terminate()
            
            # Move cleanup to a short timer or just let it die
            self.ai_worker.wait(100) # Wait max 100ms
            
            self.ai_worker = None
            self.logo_label.stop_spinning()
            
            # Re-enable input (resets color)
            self.input_field.setReadOnly(False)
            
            # Remove "thinking" widgets
            for i in range(self.list_widget.count() - 1, -1, -1):
                item = self.list_widget.item(i)
                role = item.data(Qt.ItemDataRole.UserRole)
                if role == "thinking":
                    taken = self.list_widget.takeItem(i)
                    if taken:
                        w = self.list_widget.itemWidget(taken)
                        if w: w.deleteLater()

            # Restore UI state
            if not self.is_history_mode:
                # If we were not in history mode, go back to minimal/search mode
                self.frame.set_minimal_mode(True)
                # Restore search results for the current query
                self.refresh_list(self.input_field.text(), animate=True)
            else:
                # In history mode, just ensure height is correct after removing thinking
                self.adjust_window_height(animate=True)
            
            return True
        return False

    def _activate_first_or_search(self, query):
        """Unified Tab/Enter handler: activate the first fast-action result, or trigger a search."""
        # If there are items in the list, activate the first one
        if self.list_widget.count() > 0:
            first_item = self.list_widget.item(0)
            if first_item:
                self.on_entered(first_item)
                return
        # No items yet — trigger an immediate fast action search
        self._trigger_immediate_fast_action(query)

    def _trigger_immediate_fast_action(self, query):
        """Fire an immediate ActionWorker for the query (bypasses debounce)."""
        self.debounce_timer.stop()
        self.local_search_timer.stop()
        self.cleanup_worker('action_worker')
        self.cleanup_worker('search_worker')
        self.cleanup_worker('file_search_worker')
        self.action_worker = ActionWorker(query, use_stream=True)
        self.action_worker.action_found.connect(self.on_action_found)
        self.action_worker.searching.connect(self.on_action_searching)
        self.action_worker.start()
        self.logo_label.boost_speed()

    def perform_ai_query(self, query):
        """Legacy method — redirects to fast action system (unified flow)."""
        self.voice_triggered_query = False
        self._trigger_immediate_fast_action(query)

    def perform_silent_ai_query(self, system_query):
        """Send a query to AI and show only the AI response — no user bubble created."""
        self.debounce_timer.stop()
        self._current_query = system_query
        self.input_field.setReadOnly(True)
        self.cleanup_worker('search_worker')
        self.cleanup_worker('action_worker')

        import src.services.llm.model_manager as mm
        mm.abort_fast_event.set()

        self._streaming_answer_widget = None

        # In follow-up / chat mode: just add an answer widget with no user bubble
        if self.is_history_mode:
            answer_widget = AnswerWidget("", query_text=None, chat_mode=True,
                                         show_ai_name=False)
            answer_widget.set_query_visible(False)
            self._streaming_answer_widget = answer_widget
            self.insert_list_item(0, answer_widget, "answer", animation="instant")
            if self.list_widget.count() > 0:
                self.list_widget.scrollToItem(self.list_widget.item(0))
            QTimer.singleShot(0, answer_widget.update_item_size)
        else:
            # Create AnswerWidget immediately with "Thinking..." placeholder
            # instead of a standalone ThinkingWidget — avoids the jarring
            # remove-and-replace transition when the first streaming token arrives.
            answer_widget = AnswerWidget("", query_text=system_query, chat_mode=False)
            self._streaming_answer_widget = answer_widget
            self.add_list_item(answer_widget, "answer")

        self.frame.set_minimal_mode(False)
        self.logo_label.boost_speed()
        self.follow_up_widget.set_mode("hidden")
        self.adjust_window_height()
        self.start_ai_worker(system_query, None)

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

    def start_ai_worker(self, query, screenshot_b64, resume_session_id=None):
        self._last_ai_query = query  # stored so request_permission can re-run if user grants
        self.cleanup_worker('ai_worker')
        # Track usage locally so the settings panel counter stays current.
        # Skip continuations (resume_session_id) — those are tool-permission re-runs,
        # not new user requests.
        if not resume_session_id:
            from src.core import subscription as _sub
            _sub.increment_usage()
        self.ai_worker = AIWorker(query, self.chat_history, screenshot_b64, resume_session_id=resume_session_id)
        self.ai_worker.finished.connect(self.on_ai_response)
        self.ai_worker.partial_response.connect(self.on_partial_response)
        self.ai_worker.start()



    def _unwrap_answer_widget(self, item):
        if not item: return None
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, SmoothEntryWidget):
            return widget.content_widget
        return widget

    def _last_chat_sender(self):
        """Return the sender of the most recent chat bubble ('user' or 'ai'), or None.
        Used to decide whether to show name labels (only on sender change)."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self._unwrap_answer_widget(item)
            if isinstance(w, AnswerWidget) and w.chat_mode:
                # The top-most AnswerWidget has user bubble on top, AI bubble below.
                # The last visible sender in the conversation is the AI (since it replied last).
                return "ai"
        return None

    def on_partial_response(self, data):
        """Handle partial streaming: show thinking in collapsible (gray), answer in main. Collapse thinking when answer starts."""
        self.logo_label.stop_spinning()

        thinking = data.get("thinking", "")
        answer = data.get("answer", "")

        # Prepend thinking from the previous (pre-permission) AI turn so the
        # reasoning section appears as one continuous block after re-run.
        if self._continuation_thinking_prefix and thinking:
            thinking = self._continuation_thinking_prefix + "\n\n" + thinking
        elif self._continuation_thinking_prefix and not thinking:
            thinking = self._continuation_thinking_prefix

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

                import re
                parts = re.split(r'([.!?]+[\s\n]+)', self.tts_buffer)

                while len(parts) >= 2:
                    segment = parts.pop(0)
                    delimiter = parts.pop(0)

                    full_sentence = segment + delimiter

                    # Ignore short fragments that might be artifacts
                    if len(full_sentence.strip()) > 2:
                        logging.info(f"Queueing TTS chunk: {full_sentence[:30]}...")
                        self.tts_worker.add_text(full_sentence)

                # Keep the rest in buffer
                self.tts_buffer = "".join(parts)

        # ── First partial: create the widget (must happen immediately) ──
        if self._streaming_answer_widget is None:
            thinking_idx = -1
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == "thinking":
                    thinking_idx = i
                    break

            if thinking_idx != -1:
                if thinking_idx + 1 < self.list_widget.count():
                    next_item = self.list_widget.item(thinking_idx + 1)
                    if next_item.data(Qt.ItemDataRole.UserRole) == "separator":
                        taken_sep = self.list_widget.takeItem(thinking_idx + 1)
                        if taken_sep:
                            w = self.list_widget.itemWidget(taken_sep)
                            if w: w.deleteLater()

                taken_think = self.list_widget.takeItem(thinking_idx)
                if taken_think:
                    w = self.list_widget.itemWidget(taken_think)
                    if w: w.deleteLater()

            prepend = self.is_history_mode
            if prepend and self.list_widget.count() > 0:
                self.insert_list_item(0, SeparatorWidget(), "separator", animation="instant")

            current_query = getattr(self, '_current_query', self.input_field.text())
            answer_widget = AnswerWidget("", query_text=current_query, chat_mode=False)
            self._streaming_answer_widget = answer_widget

            if thinking and thinking.strip():
                answer_widget.ensure_thinking_widget()
                answer_widget.update_thinking(thinking)
                answer_widget.set_thinking_collapsed(False)

            if answer:
                answer_widget.set_answer(answer)

            if prepend:
                self.insert_list_item(0, answer_widget, "answer")
            else:
                self.add_list_item(answer_widget, "answer")
            # After inserting into the list the widget has a real parent chain so
            # isVisible() returns True — re-compute the item's sizeHint now.
            # Without this, a slow tool call (e.g. get_calendar_events ~5s) leaves
            # the item at the 40px stub height until the next partial arrives.
            QTimer.singleShot(0, answer_widget.update_item_size)
            return

        # ── Subsequent partials: buffer data and throttle UI updates ──
        self._pending_partial_data = data
        if not self._partial_flush_timer.isActive():
            self._partial_flush_timer.start()

    def _flush_partial_update(self):
        """Apply the latest buffered partial data to the streaming widget (throttled)."""
        data = self._pending_partial_data
        if data is None:
            return
        self._pending_partial_data = None

        answer_widget = self._streaming_answer_widget
        if answer_widget is None:
            return

        thinking = data.get("thinking", "")
        answer = data.get("answer", "")

        # Prepend continuation thinking prefix
        if self._continuation_thinking_prefix and thinking:
            thinking = self._continuation_thinking_prefix + "\n\n" + thinking
        elif self._continuation_thinking_prefix and not thinking:
            thinking = self._continuation_thinking_prefix

        # Update thinking (skip_resize — we do one layout pass at the end)
        if thinking and thinking.strip():
            answer_widget.ensure_thinking_widget()
            answer_widget.update_thinking(thinking, skip_resize=True)

        if answer:
            if thinking:
                answer_widget.set_thinking_collapsed(True, skip_resize=True)
            # skip_resize — single layout pass below
            answer_widget.set_answer(answer, skip_resize=True)

        # Update collapsible header label (from tool calls or terminal commands)
        thinking_header = data.get("thinking_header", "")
        if thinking_header:
            answer_widget.set_thinking_header(thinking_header, skip_resize=True)

        actions = data.get("actions", [])
        if actions:
            for act in actions:
                if isinstance(act, dict) and act.get("type") == "terminal_command" and act.get("description"):
                    action_label = str(act.get("description")).strip().capitalize()
                    answer_widget.set_thinking_header(action_label, skip_resize=True)
                    answer_widget.set_thinking_collapsed(True, skip_resize=True)
                    break

        # Single layout pass for all changes above
        answer_widget.update_item_size()

    def _upgrade_to_chat_bubbles(self):
        """Convert any remaining simple (chat_mode=False) AnswerWidgets to chat bubble layout.
        Called once on the first follow-up so the whole conversation looks consistent."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self._unwrap_answer_widget(item)
            if isinstance(w, AnswerWidget) and not w.chat_mode:
                answer_text = getattr(w, '_answer_text', '') or (w.text_edit.toPlainText() if w.text_edit else '')
                query_text = w._query_text
                thinking_text = (
                    w.thinking_widget.thinking_text.toPlainText()
                    if w.thinking_widget is not None else ''
                )
                new_w = AnswerWidget(answer_text, query_text=query_text, thinking_text=thinking_text, chat_mode=True)
                new_w._answer_text = answer_text
                new_w.set_query_visible(bool(query_text))
                if thinking_text:
                    new_w.set_thinking_collapsed(True)

                # Transfer settings widgets so they persist into follow-up mode
                for sw in list(getattr(w, '_settings_widgets', [])):
                    w.outer_layout.removeWidget(sw)
                    new_w.append_settings_widget(sw)

                self.list_widget.takeItem(i)
                self.insert_list_item(i, new_w, "answer", animation="instant")
                new_item = self.list_widget.item(i)
                new_item.setSizeHint(new_w.sizeHint())
                QTimer.singleShot(0, new_w.update_item_size)

    def _finalize_response_ui(self):
        """Called once after every AI response (streaming or non-streaming) to tidy the UI."""
        if self._continuation_pending:
            # A request_permission popup is waiting for user approval.
            # Keep the widget reference, input lock, and UI state intact until the user
            # either allows (→ continuation re-run) or denies (→ manual finalize).
            return
        self._streaming_answer_widget = None  # clear tracking after response completes
        self._continuation_thinking_prefix = ""  # clear any request_permission continuation prefix
        self.input_field.setReadOnly(False)
        self.input_field.blockSignals(True)
        self.input_field.clear()
        self.input_field.blockSignals(False)
        self.input_field.setPlaceholderText("Ask a follow-up...")
        # Only grab focus when window is actually on screen — calling setFocus() on a hidden
        # window causes macOS to pre-register a focus ring that appears as a blue border on
        # the next show, even after WA_MacShowFocusRect is suppressed.
        if self.isVisible():
            self.input_field.setFocus()
            self.input_field.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.follow_up_widget.set_mode("followup")

        if not self.is_history_mode:
            self.is_history_mode = True
            self.frame.set_minimal_mode(False)

    def on_ai_response(self, data):
        # Flush any buffered partial update before processing final response
        self._partial_flush_timer.stop()
        if self._pending_partial_data is not None:
            self._flush_partial_update()

        self.logo_label.stop_spinning()

        # Sync real backend usage count — each query may trigger multiple LLM
        # API calls (tool iterations), so the local +1 counter can lag behind.
        from src.core import subscription as _sub
        _sub.refresh_status()

        # Clear any temporary trust boost that was set for a request_permission re-run
        from src.services.llm.tools import clear_trust_boost
        clear_trust_boost()

        # Use the tracked streaming widget (set during on_partial_response)
        has_streaming_answer = False
        widget = self._streaming_answer_widget
        if widget is not None and hasattr(widget, 'text_edit'):
            answer = data.get("answer", "")
            thinking = data.get("thinking", "")
            actions = data.get("actions", [])
            if actions:
                logging.info(f"[UI] on_ai_response received {len(actions)} actions: {[a.get('type') if isinstance(a, dict) else str(a)[:30] for a in actions]}")

            # ── Find list item early (needed for permission suppression) ──────
            item = None
            insert_pos = 0
            for i in range(self.list_widget.count()):
                candidate = self.list_widget.item(i)
                if self._unwrap_answer_widget(candidate) is widget:
                    item = candidate
                    insert_pos = i + 1
                    break

            # ── Detect if any action will show a permission card ──────────────
            _trust = settings_store.get("trust_level", 1)

            # Normal flow: show thinking + finalize answer text
            if thinking:
                widget.ensure_thinking_widget()
                widget.update_thinking(thinking)
                widget.set_thinking_collapsed(True)

            # Pre-scan: if any trust_request needs permission, suppress the answer
            # so there's nothing to flash before the permission popup appears
            _has_pending_continuation = any(
                isinstance(a, dict) and a.get('type') == 'trust_request'
                and _trust < a.get('required_level', 2)
                for a in (actions or [])
            )
            widget.set_answer("" if _has_pending_continuation else answer)

            # Determine label for the collapsed thinking block
            action_label = data.get("thinking_header") or None
            if not action_label and actions:
                for act in actions:
                    if isinstance(act, dict) and act.get('type') == 'terminal_command' and act.get('description'):
                        action_label = str(act.get('description')).strip().capitalize()
                        break

            QTimer.singleShot(80, lambda w=widget, lbl=action_label: (
                w.hide_thinking_and_play_done(lbl)
                if hasattr(w, 'hide_thinking_and_play_done') and not self._continuation_pending
                else None
            ))

            if actions and item is not None:
                for act in actions:
                    if isinstance(act, dict):
                        if act.get('type') == 'link':
                            w = LinkActionWidget(act['title'], act['url'], act['description'])
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'person':
                            w = PersonActionWidget(act['name'], act.get('description', ''), act.get('image'), act.get('url'))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                            if not act.get('image'):
                                w.fetch_image_for_name(act['name'])
                        elif act.get('type') == 'place':
                            w = MapNavigationWidget(act['name'], act.get('address'))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'install':
                            w = InstallActionWidget(act['name'], act.get('website'), act.get('desc', ''))
                            def _make_install_cb(name, url, _w):
                                def _cb(n, _widget):
                                    self._check_trust_or_prompt(
                                        3,
                                        f"install {name}",
                                        lambda: self.start_install(name, source_widget=_w, website_url=url),
                                    )
                                return _cb
                            w.install_accepted.connect(_make_install_cb(act['name'], act.get('website', ''), w))
                            self.insert_list_item(insert_pos, w, act, animation="fade")
                            insert_pos += 1
                        elif act.get('type') == 'uninstall':
                            w = UninstallActionWidget(act['name'])
                            def _make_uninstall_cb(name, _w):
                                def _cb(n, _widget):
                                    self._check_trust_or_prompt(
                                        3,
                                        f"uninstall {name}",
                                        lambda: self.start_uninstall(name, source_widget=_w),
                                    )
                                return _cb
                            w.uninstall_accepted.connect(_make_uninstall_cb(act['name'], w))
                            self.insert_list_item(insert_pos, w, act, animation="fade")
                            insert_pos += 1
                        elif act.get('type') == 'computer_control':
                            cc_action  = act.get('action', '')
                            cc_desc    = act.get('description') or cc_action or 'control your computer'
                            cc_act_ref = dict(act)
                            if settings_store.get("trust_level", 1) >= 2:
                                self._execute_computer_control(cc_act_ref)
                            else:
                                perm = TrustPermissionChatWidget(2, cc_desc, getattr(self, "current_theme", "dark"))
                                self._perm_widget = perm  # keep strong ref
                                widget.set_answer_visible(False)
                                def _make_cc_allow_cb(_action_ref, _perm_widget, _w):
                                    def _cb():
                                        _w.set_answer_visible(True)
                                        self._execute_computer_control(_action_ref)
                                    return _cb
                                def _make_cc_deny_cb(_w):
                                    def _cb():
                                        _w.set_answer("Akcja anulowana.")
                                        _w.set_answer_visible(True)
                                    return _cb
                                perm.allowed.connect(_make_cc_allow_cb(cc_act_ref, perm, widget))
                                perm.denied.connect(_make_cc_deny_cb(widget))
                                perm.open_settings.connect(self._navigate_to_trust_settings)
                                self.insert_list_item(insert_pos, perm, {"type": "trust_permission"}, animation="pop")
                                insert_pos += 1
                        elif act.get('type') == 'trust_request':
                            req_level  = act.get('required_level', 2)
                            cmd        = act.get('command', '')
                            desc       = act.get('description', cmd)[:80]
                            session_id = act.get('session_id')
                            if settings_store.get("trust_level", 1) >= req_level:
                                if cmd:
                                    self._run_trusted_terminal(cmd, insert_pos)
                                    insert_pos += 1
                            else:
                                # Always pause finalization so the thinking widget doesn't collapse
                                self._continuation_pending = True
                                perm = TrustPermissionChatWidget(req_level, desc, getattr(self, "current_theme", "dark"))
                                self._perm_widget = perm  # keep strong ref
                                widget.set_answer_visible(False)
                                def _make_tr_allow_cb(_perm_widget, _w, _lvl, _prior_thinking, _was_voice, _session_id):
                                    def _cb():
                                        # Clear the flag — finalize runs normally after re-run
                                        self._continuation_pending = False
                                        # Remove the permission widget from the list
                                        for _i in range(self.list_widget.count() - 1, -1, -1):
                                            _itm = self.list_widget.item(_i)
                                            _w_itm = self.list_widget.itemWidget(_itm)
                                            if _w_itm and getattr(_w_itm, 'content_widget', _w_itm) is _perm_widget:
                                                taken = self.list_widget.takeItem(_i)
                                                if taken:
                                                    w_del = self.list_widget.itemWidget(taken)
                                                    if w_del: w_del.deleteLater()
                                                break
                                        # Pop the incomplete pre-permission exchange from history
                                        if (len(self.chat_history) >= 2 and
                                                self.chat_history[-1].get('role') == 'assistant' and
                                                self.chat_history[-2].get('role') == 'user'):
                                            self.chat_history.pop()
                                            self.chat_history.pop()
                                        # Reuse the existing answer widget in-place
                                        self._continuation_thinking_prefix = _prior_thinking
                                        _w.set_answer("")
                                        _w.set_answer_visible(True)
                                        _w.set_thinking_collapsed(False)
                                        # Restore voice flag so the re-run response also gets TTS
                                        if _was_voice:
                                            self.voice_triggered_query = True
                                        # Boost trust and resume from saved session (no full restart)
                                        from src.services.llm.tools import set_trust_boost
                                        set_trust_boost(_lvl)
                                        self.logo_label.boost_speed()
                                        _w.set_answer("Running...")
                                        QTimer.singleShot(100, lambda: self.start_ai_worker(
                                            getattr(self, '_last_ai_query', ''), None,
                                            resume_session_id=_session_id
                                        ))
                                    return _cb
                                def _make_tr_deny_cb(_w):
                                    def _cb():
                                        self._continuation_pending = False
                                        self._finalize_response_ui()
                                        _w.set_answer("Action cancelled.")
                                        _w.set_answer_visible(True)
                                    return _cb
                                def _make_tr_settings_cb():
                                    def _cb():
                                        self._continuation_pending = False
                                        self._finalize_response_ui()
                                        self._navigate_to_trust_settings()
                                    return _cb
                                perm.allowed.connect(_make_tr_allow_cb(perm, widget, req_level, data.get("thinking", ""), self.voice_triggered_query, session_id))
                                perm.denied.connect(_make_tr_deny_cb(widget))
                                perm.open_settings.connect(_make_tr_settings_cb())
                                self.insert_list_item(insert_pos, perm, {"type": "trust_permission"}, animation="pop")
                                insert_pos += 1
                        elif act.get('type') == 'open_app':
                            w = AppActionWidget(act['name'])
                            def launch_app(name, widget):
                                self._cancel_all_workers()
                                find_and_launch_app(name)
                                self.animate_close()
                            w.app_accepted.connect(launch_app)
                            self.insert_list_item(insert_pos, w, act, animation="fade")
                            insert_pos += 1
                        elif act.get('type') == 'weather':
                            w = WeatherActionWidget(act.get('location', ''), act.get('temp', ''), act.get('condition', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'unit':
                            w = UnitActionWidget(act.get('amount', '0'), act.get('from_unit', ''), act.get('to_unit', ''), act.get('converted_value', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'status':
                            w = StandardItemWidget(act.get('description', ''), icon_name="dialog-information")
                            self.insert_list_item(insert_pos, w, act, animation="fade")
                            insert_pos += 1
                        elif act.get('type') == 'currency':
                            w = CurrencyActionWidget(act.get('amount', '0'), act.get('from_unit', ''), act.get('to_unit', ''), act.get('converted_value', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'world_time':
                            w = WorldTimeWidget(act.get('city', ''), act.get('timezone', ''), act.get('current_time', ''), act.get('date', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'translate':
                            w = TranslateActionWidget(act.get('source_text', ''), act.get('from_lang', ''), act.get('to_lang', ''), act.get('translated_text', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'color_preview':
                            w = ColorActionWidget(act.get('color_hex', ''), act.get('rgb_val', ''), act.get('hsl_val', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'timer':
                            w = TimerActionWidget(act.get('duration', 0))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'password':
                            w = PasswordActionWidget(act.get('length', 16), act.get('pwd', None))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'qrcode':
                            w = QRActionWidget(act.get('data', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'system_settings':
                            try:
                                sw = SettingsAnimationWidget(
                                    setting=act.get("setting", ""),
                                    value=act.get("value", 0),
                                    label=act.get("label", ""),
                                    unit=act.get("unit", ""),
                                    color_hex=act.get("color", "#FFFFFF"),
                                    icon_name=act.get("icon", ""),
                                    success=True
                                )
                                # Inject directly into the AI bubble instead of a separate list item
                                widget.append_settings_widget(sw)
                                if item is not None:
                                    item.setSizeHint(widget.sizeHint())
                            except Exception as _te:
                                logging.warning(f"[settings] Failed to add settings widget: {_te}")
                        elif act.get('type') == 'optimize_system':
                            suggestions = act.get('suggestions', [])
                            if suggestions:
                                w = OptimizeSystemWidget(suggestions)
                                def _make_optimize_cb(_widget):
                                    def _cb(selected):
                                        import subprocess, logging
                                        trust = settings_store.get("trust_level", 1)
                                        cmds = [s.get("command", "") for s in selected if s.get("command")]
                                        if not cmds:
                                            return
                                        if trust < 2:
                                            perm = TrustPermissionChatWidget(
                                                2, f"run {len(cmds)} optimization(s)",
                                                getattr(self, "current_theme", "dark"),
                                            )
                                            self._perm_widget = perm
                                            def _on_allow():
                                                for cmd in cmds:
                                                    try:
                                                        subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
                                                    except Exception as e:
                                                        logging.error(f"[optimize] {cmd}: {e}")
                                                _widget._r_text.setText(f"{len(cmds)} optimization(s) applied")
                                                self.adjust_window_height()
                                            def _on_deny():
                                                _widget.show_error("Permission denied")
                                                self.adjust_window_height()
                                            perm.allowed.connect(_on_allow)
                                            perm.denied.connect(_on_deny)
                                            perm.open_settings.connect(self._navigate_to_trust_settings)
                                            self.insert_list_item(insert_pos, perm, {"type": "trust_permission"}, animation="pop")
                                        else:
                                            for cmd in cmds:
                                                try:
                                                    subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
                                                except Exception as e:
                                                    logging.error(f"[optimize] {cmd}: {e}")
                                            _widget._r_text.setText(f"{len(cmds)} optimization(s) applied")
                                    return _cb
                                w.apply_requested.connect(_make_optimize_cb(w))
                                self.insert_list_item(insert_pos, w, act, animation="pop")
                                insert_pos += 1
                        elif act.get('type') == 'terminal_command':
                            if not act.get('command', '').strip():
                                continue
                            w = TerminalActionWidget(
                                command=act.get('command', ''),
                                description=act.get('description', ''),
                                output=act.get('stdout', ''),
                                error=act.get('stderr', ''),
                                success=act.get('success', True)
                            )
                            self.insert_list_item(insert_pos, w, act, animation="slide")
                            insert_pos += 1
                        elif act.get('type') == 'send_email_draft':
                            w = SendEmailWidget(act.get('to', ''), act.get('subject', ''), act.get('body', ''), original_query=act.get('original_query', ''))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1
                        elif act.get('type') == 'tool_draft':
                            t_name = act.get('tool_name', '')
                            t_args = act.get('args', {})
                            w = ToolDraftWidget(t_name, t_args, original_query=act.get('original_query', ''))
                            def _make_draft_exec_cb(_widget):
                                def _cb(tool_name, args):
                                    self._execute_tool_draft(tool_name, args, _widget)
                                return _cb
                            w.execute_requested.connect(_make_draft_exec_cb(w))
                            self.insert_list_item(insert_pos, w, act, animation="pop")
                            insert_pos += 1

            answer_text = data.get("answer", "")
            widget._answer_text = answer_text  # store for potential future upgrade to chat bubbles

            widget.updateGeometry()
            if item is not None:
                item.setSizeHint(widget.sizeHint())

            self.list_widget.update()
            self.adjust_window_height(animate=False)
            has_streaming_answer = True

        # Always update chat history from streaming widget if we have one
        if has_streaming_answer:
            saved_query = getattr(self, '_current_query', "")
            answer_final = data.get("answer", "")
            thinking_final = data.get("thinking", "")
            if saved_query and answer_final:
                self.chat_history.append({"role": "user", "content": saved_query})
                settings_acts = [a for a in data.get("actions", []) if isinstance(a, dict) and a.get("type") == "system_settings"]
                self.chat_history.append({"role": "assistant", "content": answer_final, "thinking": thinking_final,
                                          "settings_actions": settings_acts})

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
            
            # Mic must not stay red when we're done (PAUSED) — sync UI with listener state
            self.mic_widget.set_active(False)

            # Reset flag
            self.voice_triggered_query = False

        if has_streaming_answer:
            # Detect file path for "↵ Enter to open" hint
            # Priority: file_hint from tool results, then regex from answer text
            self._pending_open_file = None
            _fh = data.get("file_hint")
            if _fh and os.path.exists(_fh) and widget is not None:
                self._pending_open_file = _fh
                widget.ensure_thinking_widget()
                widget.thinking_widget.set_open_hint("↵  Enter to open")
                logging.info(f"[open_hint] from tool file_hint={_fh}")
            elif widget is not None:
                answer_text = data.get("answer", "") or getattr(widget, '_answer_text', "")
                if answer_text:
                    import re
                    _fp_match = re.search(r'(?:~/[\w./ \-]+|/[\w./ \-]+\.[\w]+)', answer_text)
                    if _fp_match:
                        _fp = _fp_match.group(0).rstrip(' .,;:)`')
                        if _fp.startswith("~"):
                            _fp = os.path.expanduser(_fp)
                        if os.path.exists(_fp):
                            self._pending_open_file = _fp
                            widget.ensure_thinking_widget()
                            widget.thinking_widget.set_open_hint("↵  Enter to open")
                            logging.info(f"[open_hint] from answer text={_fp}")

            self._finalize_response_ui()
            # If AI opened a file via terminal_command, close like shortcut — preserves state
            if self._actions_include_file_open(data.get("actions", [])):
                QTimer.singleShot(500, self.animate_close)
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
                    taken_sep = self.list_widget.takeItem(thinking_idx + 1)
                    if taken_sep:
                        w = self.list_widget.itemWidget(taken_sep)
                        if w: w.deleteLater()
            
            # Then remove thinking
            taken_think = self.list_widget.takeItem(thinking_idx)
            if taken_think:
                w = self.list_widget.itemWidget(taken_think)
                if w: w.deleteLater()
            
        # Fallback cleanup for any other thinking widgets
        for i in range(self.list_widget.count() - 1, -1, -1):
            item = self.list_widget.item(i)
            role = item.data(Qt.ItemDataRole.UserRole)
            if role == "thinking":
                 taken = self.list_widget.takeItem(i)
                 if taken:
                     w = self.list_widget.itemWidget(taken)
                     if w: w.deleteLater()

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
        def add_item(w, d, anim="fade"):
            nonlocal insert_idx
            if prepend:
                self.insert_list_item(insert_idx, w, d, animation=anim)
                insert_idx += 1
            else:
                self.add_list_item(w, d, animation=anim)

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
                self.insert_list_item(0, SeparatorWidget(), "separator", animation="instant")
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

        # Use the query captured at the start of perform_ai_query (input may already be cleared)
        saved_query = getattr(self, '_current_query', self.input_field.text())

        # Add Answer (non-streaming path: always normal/first query → simple view)
        _answer_bubble = None   # reference kept so system_settings can inject into it
        settings_acts_for_history = [a for a in actions if isinstance(a, dict) and a.get("type") == "system_settings"]
        if answer:
            self.chat_history.append({"role": "user", "content": saved_query})
            self.chat_history.append({"role": "assistant", "content": answer, "thinking": thinking,
                                      "settings_actions": settings_acts_for_history})

            w = AnswerWidget(answer, query_text=saved_query, thinking_text=thinking, chat_mode=False)
            add_item(w, "answer")
            _answer_bubble = w

            # Determine if we should rename the Reasoning process label to an action name
            action_label = None
            if actions:
                for act in actions:
                    if isinstance(act, dict) and act.get('type') == 'terminal_command' and act.get('description'):
                        action_label = str(act.get('description')).strip().capitalize()
                        break
            if action_label:
                w.set_thinking_header(action_label)
            w.set_thinking_collapsed(True)

        # Add Actions
        for act in actions:
            if isinstance(act, dict):
                if act.get('type') == 'link':
                    w = LinkActionWidget(act['title'], act['url'], act['description'])
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'person':
                    w = PersonActionWidget(act['name'], act.get('description', ''), act.get('image'), act.get('url'))
                    add_item(w, act, anim="pop")
                    if not act.get('image'):
                        w.fetch_image_for_name(act['name'])
                elif act.get('type') == 'place':
                    w = PlaceActionWidget(
                        act['name'],
                        act.get('address', ''),
                        act.get('image'),
                        act.get('url'),
                        act.get('latitude'),
                        act.get('longitude')
                    )
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'install':
                    w = InstallActionWidget(act['name'], act.get('website'), act.get('desc', ''))
                    w.install_accepted.connect(lambda name, widget, _w=w, _u=act.get('website', ''): self.start_install(name, source_widget=_w, website_url=_u))
                    add_item(w, act, anim="fade")
                elif act.get('type') == 'uninstall':
                    w = UninstallActionWidget(act['name'])
                    w.uninstall_accepted.connect(lambda name, widget, _w=w: self.start_uninstall(name, source_widget=_w))
                    add_item(w, act, anim="fade")
                elif act.get('type') == 'trust_request':
                    req_level  = act.get('required_level', 2)
                    cmd        = act.get('command', '')
                    desc       = act.get('description', cmd)[:80]
                    session_id = act.get('session_id')
                    if settings_store.get("trust_level", 1) >= req_level:
                        if cmd:
                            self._run_trusted_terminal(cmd)
                    else:
                        # Always pause finalization and reuse the answer bubble for in-place re-run
                        self._continuation_pending = True
                        if _answer_bubble:
                            self._streaming_answer_widget = _answer_bubble
                        perm = TrustPermissionChatWidget(req_level, desc, getattr(self, "current_theme", "dark"))
                        if _answer_bubble:
                            _answer_bubble.set_answer_visible(False)
                        def _make_tr_allow_non_stream(_perm_widget, _w, _lvl, _prior_thinking, _was_voice, _session_id):
                            def _cb():
                                self._continuation_pending = False
                                for _i in range(self.list_widget.count() - 1, -1, -1):
                                    _itm = self.list_widget.item(_i)
                                    _w_itm = self.list_widget.itemWidget(_itm)
                                    if _w_itm and getattr(_w_itm, 'content_widget', _w_itm) is _perm_widget:
                                        self.list_widget.takeItem(_i)
                                        break
                                if (len(self.chat_history) >= 2 and
                                        self.chat_history[-1].get('role') == 'assistant' and
                                        self.chat_history[-2].get('role') == 'user'):
                                    self.chat_history.pop()
                                    self.chat_history.pop()
                                self._continuation_thinking_prefix = _prior_thinking
                                if _w:
                                    _w.set_answer("")
                                    _w.set_answer_visible(True)
                                    _w.set_thinking_collapsed(False)
                                # Restore voice flag so the re-run response also gets TTS
                                if _was_voice:
                                    self.voice_triggered_query = True
                                from src.services.llm.tools import set_trust_boost
                                set_trust_boost(_lvl)
                                self.logo_label.boost_speed()
                                if _w:
                                    _w.set_answer("Running...")
                                QTimer.singleShot(100, lambda: self.start_ai_worker(
                                    getattr(self, '_last_ai_query', ''), None,
                                    resume_session_id=_session_id
                                ))
                            return _cb
                        def _make_tr_deny_non_stream(_w):
                            def _cb():
                                self._continuation_pending = False
                                self._finalize_response_ui()
                                if _w:
                                    _w.set_answer("Action cancelled.")
                                    _w.set_answer_visible(True)
                            return _cb
                        def _make_tr_settings_ns_cb():
                            def _cb():
                                self._continuation_pending = False
                                self._finalize_response_ui()
                                self._navigate_to_trust_settings()
                            return _cb
                        perm.allowed.connect(_make_tr_allow_non_stream(perm, _answer_bubble, req_level, thinking, self.voice_triggered_query, session_id))
                        perm.denied.connect(_make_tr_deny_non_stream(_answer_bubble))
                        perm.open_settings.connect(_make_tr_settings_ns_cb())
                        add_item(perm, {"type": "trust_permission"}, anim="pop")
                elif act.get('type') == 'open_app':
                    w = AppActionWidget(act['name'])
                    def launch_app(name, widget):
                        self._cancel_all_workers()
                        find_and_launch_app(name)
                        self.animate_close()
                    w.app_accepted.connect(launch_app)
                    add_item(w, act, anim="fade")
                elif act.get('type') == 'weather':
                    w = WeatherActionWidget(act.get('location', ''), act.get('temp', ''), act.get('condition', ''))
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'unit':
                    w = UnitActionWidget(act.get('amount', '0'), act.get('from_unit', ''), act.get('to_unit', ''), act.get('converted_value', ''))
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'color_preview':
                    w = ColorActionWidget(act.get('color_hex', ''), act.get('rgb_val', ''), act.get('hsl_val', ''))
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'timer':
                    w = TimerActionWidget(act.get('duration', 0))
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'password':
                    w = PasswordActionWidget(act.get('length', 16), act.get('pwd', None))
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'qrcode':
                    w = QRActionWidget(act.get('data', ''))
                    add_item(w, act, anim="pop")
                elif act.get('type') == 'status':
                    w = StandardItemWidget(act['description'], icon_name="dialog-information")
                    add_item(w, act, anim="fade")
                elif act.get('type') == 'system_settings':
                    try:
                        sw = SettingsAnimationWidget(
                            setting=act.get("setting", ""),
                            value=act.get("value", 0),
                            label=act.get("label", ""),
                            unit=act.get("unit", ""),
                            color_hex=act.get("color", "#FFFFFF"),
                            icon_name=act.get("icon", ""),
                            success=True
                        )
                        if _answer_bubble is not None:
                            _answer_bubble.append_settings_widget(sw)
                            self.adjust_window_height()
                        else:
                            add_item(sw, act, anim="pop")
                    except Exception as _te:
                        logging.warning(f"[settings] Failed to show settings widget: {_te}")
                elif act.get('type') == 'terminal_command':
                    if not act.get('command', '').strip():
                        continue
                    w = TerminalActionWidget(
                        command=act.get('command', ''),
                        description=act.get('description', ''),
                        output=act.get('stdout', ''),
                        error=act.get('stderr', ''),
                        success=act.get('success', True)
                    )
                    add_item(w, act, anim="slide")

        # Scroll to top if we prepended
        if prepend and self.list_widget.count() > 0:
            self.list_widget.scrollToItem(self.list_widget.item(0))

        self._finalize_response_ui()
        self.adjust_window_height()
        # If AI opened a file via terminal_command, close like shortcut — preserves state
        if self._actions_include_file_open(actions):
            QTimer.singleShot(500, self.animate_close)
