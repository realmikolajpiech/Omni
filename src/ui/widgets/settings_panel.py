"""
SettingsPanel — Redesigned settings UI with sidebar navigation.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QScrollArea, QFrame, QSizePolicy,
    QButtonGroup, QListWidget, QListWidgetItem, QStackedWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtProperty, QTimer, QSize, QRectF, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QIcon, QColor, QPainter, QPainterPath, QLinearGradient, QBrush, QPen, QFontMetrics

from src.ui.styles import THEMES
import src.core.settings_store as settings_store
import src.core.subscription as subscription
import src.core.auth as auth
import src.core.billing as billing
from src.core.config import BACKEND_URL, DEVICE_ID, OMNI_SECRET

LANGUAGES = [
    ("auto", "Auto — detect language"),
    ("en",   "English"),
    ("pl",   "Polish"),
    ("de",   "German"),
    ("fr",   "French"),
    ("es",   "Spanish"),
    ("it",   "Italian"),
    ("pt",   "Portuguese"),
    ("ja",   "Japanese"),
    ("zh",   "Chinese"),
    ("uk",   "Ukrainian"),
    ("ru",   "Russian"),
    ("ar",   "Arabic"),
    ("nl",   "Dutch"),
]

_PRIVACY_ITEMS = [
    (
        "Voice transcription",
        "Audio is sent to the transcription service solely to convert speech to text. "
        "Your audio data is not stored — processing is ephemeral "
        "and never feeds into any model training pipeline.",
    ),
    (
        "AI queries",
        "Query content is transmitted to xAI (Grok) or Groq over encrypted HTTPS. "
        "Neither company builds user profiles from API queries "
        "or shares your data with third parties.",
    ),
    (
        "Memory & history",
        "All conversational memory and history are stored exclusively on your local machine "
        "(~/.local/share/ai-memory-db). Nothing is synced to any cloud service.",
    ),
    (
        "Web search",
        "Search runs through a local SearXNG instance — queries never reach "
        "Google or any external search engine directly.",
    ),
    (
        "API keys",
        "Keys are stored locally in your .env file and ~/.config/omni/settings.json. "
        "They are only sent as authorization headers to their respective services "
        "— nowhere else.",
    ),
    (
        "No telemetry",
        "Omni collects zero usage data, sends no diagnostic reports, "
        "and contains no tracking code. "
        "The app is open-source — you can verify this yourself.",
    ),
]


# ── Trust level data ─────────────────────────────────────────────────────────

_TRUST_NAMES = {1: "Assistant", 2: "Automation", 3: "Full Control"}

# Cumulative capabilities introduced at each level
_ALL_CAPS = {
    1: [
        "AI chat & web search",
        "File & semantic search",
        "Read-only terminal  (battery, disk, memory…)",
        "Open files & apps",
        "Calculator, translate, weather & more",
    ],
    2: [
        "Create, copy & move files",
        "Computer control  (click, type, scroll)",
        "System modifications  (Dock, network, power…)",
    ],
    3: [
        "Install & uninstall apps",
        "Privileged commands  (sudo, rm, brew…)",
    ],
}

_GRAD_COLORS = ["#2E5CB8", "#6A0DAD", "#D92E87", "#FF8533"]


# ── Custom trust slider ───────────────────────────────────────────────────────

class _TrustSlider(QWidget):
    """
    3-stop custom slider drawn entirely in paintEvent.
    Animates the knob position smoothly between stops.
    """
    level_changed = pyqtSignal(int)

    _MARGIN   = 20   # px from each edge to first/last stop
    _TRACK_H  = 6    # track height
    _KNOB_R   = 10   # knob radius

    def __init__(self, level: int = 1, theme: str = "dark", parent=None):
        super().__init__(parent)
        self._level  = level
        self._theme  = theme
        self._knob_t = float(level - 1) / 2   # 0.0 → 0.5 → 1.0
        self._anim = None  # type: QPropertyAnimation | None
        self.setFixedHeight(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── Qt property for animation ─────────────────────────────────────────────

    @pyqtProperty(float)
    def knob_t(self) -> float:
        return self._knob_t

    @knob_t.setter
    def knob_t(self, val: float):
        self._knob_t = val
        self.update()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_level(self, level: int, animate: bool = True):
        if level == self._level and abs(self._knob_t - float(level - 1) / 2) < 0.01:
            return
        self._level = level
        target = float(level - 1) / 2
        if animate:
            if self._anim:
                self._anim.stop()
            self._anim = QPropertyAnimation(self, b"knob_t")
            self._anim.setDuration(300)
            self._anim.setStartValue(self._knob_t)
            self._anim.setEndValue(target)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.start()
        else:
            self._knob_t = target
            self.update()

    def set_theme(self, theme: str):
        self._theme = theme
        self.update()

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def _track_x0(self) -> float:
        return float(self._MARGIN)

    def _track_x1(self) -> float:
        return float(self.width() - self._MARGIN)

    def _track_y(self) -> float:          # center y of track
        return float(self._KNOB_R + 4)

    def _stop_x(self, level: int) -> float:
        return self._track_x0() + (level - 1) * (self._track_x1() - self._track_x0()) / 2

    def _current_knob_x(self) -> float:
        return self._track_x0() + self._knob_t * (self._track_x1() - self._track_x0())

    # ── Input ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            dists = [(abs(x - self._stop_x(l)), l) for l in [1, 2, 3]]
            new_level = min(dists)[1]
            if new_level != self._level:
                self.set_level(new_level)
                self.level_changed.emit(new_level)
        super().mousePressEvent(event)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dark = self._theme == "dark"
        x0   = self._track_x0()
        x1   = self._track_x1()
        ty   = self._track_y()
        th   = float(self._TRACK_H)
        kr   = float(self._KNOB_R)
        kx   = self._current_knob_x()

        # ── Track background ──────────────────────────────────────────
        track_rect = QRectF(x0, ty - th / 2, x1 - x0, th)
        track_path = QPainterPath()
        track_path.addRoundedRect(track_rect, th / 2, th / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(track_path, QColor(255, 255, 255, 30 if dark else 40))

        # ── Filled portion (gradient left → knob) ─────────────────────
        fill_w = kx - x0
        if fill_w > 1:
            grad = QLinearGradient(x0, 0, x1, 0)
            for i, c in enumerate(_GRAD_COLORS):
                grad.setColorAt(i / (len(_GRAD_COLORS) - 1), QColor(c))
            fill_path = QPainterPath()
            fill_path.addRoundedRect(QRectF(x0, ty - th / 2, fill_w, th), th / 2, th / 2)
            painter.fillPath(fill_path, QBrush(grad))

        # ── Stop dots ─────────────────────────────────────────────────
        for l in [1, 2, 3]:
            sx = self._stop_x(l)
            on_track = sx <= kx + 1
            dot_alpha = 220 if on_track else (60 if dark else 80)
            painter.setBrush(QColor(255, 255, 255, dot_alpha) if (on_track or dark)
                             else QColor(0, 0, 0, dot_alpha))
            painter.drawEllipse(QRectF(sx - 3, ty - 3, 6, 6))

        # ── Knob shadow ───────────────────────────────────────────────
        painter.setBrush(QColor(0, 0, 0, 35))
        painter.drawEllipse(QRectF(kx - kr + 1, ty - kr + 2, kr * 2, kr * 2))

        # ── Knob gradient ring ────────────────────────────────────────
        knob_grad = QLinearGradient(kx - kr, ty - kr, kx + kr, ty + kr)
        for i, c in enumerate(_GRAD_COLORS[:3]):
            knob_grad.setColorAt(i / 2, QColor(c))
        painter.setBrush(QBrush(knob_grad))
        painter.drawEllipse(QRectF(kx - kr, ty - kr, kr * 2, kr * 2))

        # ── Knob inner white circle ───────────────────────────────────
        ir = kr - 2.5
        painter.setBrush(QColor(255, 255, 255) if dark else QColor(252, 250, 255))
        painter.drawEllipse(QRectF(kx - ir, ty - ir, ir * 2, ir * 2))

        # ── Labels ────────────────────────────────────────────────────
        label_y = ty + kr + 8
        label_data = [(1, "1", "Assistant"), (2, "2", "Automation"), (3, "3", "Full Control")]

        for l, num, name in label_data:
            sx      = self._stop_x(l)
            active  = (l == self._level)
            tc      = QColor(255, 255, 255) if dark else QColor(17, 17, 17)
            sc      = QColor(130, 130, 130) if dark else QColor(140, 140, 140)
            col     = tc if active else sc

            # Number  (Instrument Serif, bolder when active)
            num_font = QFont("Instrument Serif", 11)
            num_font.setBold(active)
            painter.setFont(num_font)
            painter.setPen(col)
            fm = painter.fontMetrics()
            painter.drawText(int(sx - fm.horizontalAdvance(num) / 2),
                             int(label_y + fm.ascent()), num)

            # Name (Manrope, smaller)
            name_font = QFont("Manrope", 8)
            painter.setFont(name_font)
            fm2 = painter.fontMetrics()
            name_y = int(label_y + fm.height() + 2 + fm2.ascent())
            painter.drawText(int(sx - fm2.horizontalAdvance(name) / 2), name_y, name)


# ── Capability panel ─────────────────────────────────────────────────────────

class _TrustCapabilityPanel(QWidget):
    """
    Glass card showing what the AI can do at the current trust level,
    plus what the next level would unlock.
    Rebuilt dynamically when set_level() is called.
    """

    def __init__(self, level: int = 1, theme: str = "dark", parent=None):
        super().__init__(parent)
        self._level = level
        self._theme = theme
        self._inner = QVBoxLayout(self)
        self._inner.setContentsMargins(20, 16, 20, 16)
        self._inner.setSpacing(0)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._rebuild()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_level(self, level: int):
        self._level = level
        self._rebuild()

    def set_theme(self, theme: str):
        self._theme = theme
        self._rebuild()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild(self):
        # Clear
        while self._inner.count():
            item = self._inner.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        dark     = self._theme == "dark"
        primary  = "#FFFFFF" if dark else "#111111"
        secondary= "#AAAAAA" if dark else "#777777"
        accent   = "#C084FC" if dark else "#7C3AED"

        def _lbl(text, font_family, size, color, bold=False, italic=False, wrap=False):
            l = QLabel(text)
            f = QFont(font_family, size)
            f.setBold(bold)
            f.setItalic(italic)
            l.setFont(f)
            l.setStyleSheet(f"background: transparent; color: {color};")
            if wrap:
                l.setWordWrap(True)
            return l

        def _row(icon, text, icon_color, text_color, font_size=10):
            w = QWidget()
            w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(9)
            dot = _lbl(icon, "Manrope", font_size, icon_color, bold=True)
            dot.setFixedWidth(14)
            cap = _lbl(text, "Manrope", font_size, text_color)
            h.addWidget(dot)
            h.addWidget(cap)
            h.addStretch()
            return w

        def _divider():
            f = QFrame()
            f.setFixedHeight(1)
            f.setStyleSheet(
                f"background: {'rgba(255,255,255,0.10)' if dark else 'rgba(0,0,0,0.08)'};"
            )
            return f

        # Level name header
        self._inner.addWidget(_lbl(_TRUST_NAMES[self._level], "Instrument Serif", 15, primary))
        self._inner.addSpacing(12)

        # All enabled capabilities (cumulative)
        for l in range(1, self._level + 1):
            for cap in _ALL_CAPS[l]:
                self._inner.addWidget(_row("✓", cap, accent, primary))
                self._inner.addSpacing(5)

        self._inner.addStretch()
        self.adjustSize()
        self.update()

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self._theme == "dark"
        r    = QRectF(self.rect())

        bg = QColor(12, 10, 18, 210) if dark else QColor(248, 246, 252, 200)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(r, 14, 14)
        painter.fillPath(bg_path, bg)

        grad = QLinearGradient(0, 0, self.width(), self.height())
        for i, c in enumerate(_GRAD_COLORS):
            grad.setColorAt(i / (len(_GRAD_COLORS) - 1), QColor(c))
        painter.setPen(QPen(QBrush(grad), 1.0))
        bpath = QPainterPath()
        bpath.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 13.5, 13.5)
        painter.drawPath(bpath)


def _font(family: str, size: int, bold: bool = False, italic: bool = False) -> QFont:
    f = QFont(family, size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    if italic:
        f.setItalic(True)
    return f


# ---------------------------------------------------------------------------
# Usage Bar
# ---------------------------------------------------------------------------

class _UsageBar(QWidget):
    """Thin horizontal progress bar for daily usage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0   # 0–100
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._track_color  = QColor(255, 255, 255, 30)
        self._fill_color   = QColor(255, 255, 255, 180)

    def set_fraction(self, used: int, total: int):
        if total <= 0:
            self._value = 0
        else:
            self._value = max(0, min(100, int(used * 100 / total)))
        # colour: green → yellow at 80 % → red at 100 %
        if self._value >= 100:
            self._fill_color = QColor("#ff5f5f")
        elif self._value >= 80:
            self._fill_color = QColor("#f5c542")
        else:
            self._fill_color = QColor(255, 255, 255, 180)
        self.update()

    def set_dark(self, dark: bool):
        self._track_color = QColor(255, 255, 255, 30) if dark else QColor(0, 0, 0, 25)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = 3.0
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        p.fillPath(path, QBrush(self._track_color))
        fill_w = self.width() * self._value / 100
        if fill_w > 0:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(0, 0, fill_w, self.height()), r, r)
            p.fillPath(fill, QBrush(self._fill_color))


# ---------------------------------------------------------------------------
# Plan card widget — painted background, immune to parent stylesheet cascade
# ---------------------------------------------------------------------------

class _PlanCard(QWidget):
    """
    Rounded card with a painted background so it renders correctly
    even when the parent has QWidget { background: transparent }.
    """
    _MONTHLY_BG     = QColor(255, 255, 255, 14)   # rgba(255,255,255,0.055)
    _MONTHLY_BORDER = QColor(255, 255, 255, 28)   # rgba(255,255,255,0.11)
    _YEARLY_BG      = QColor(99, 102, 241, 46)    # rgba(99,102,241,0.18)
    _YEARLY_BORDER  = QColor(99, 102, 241, 140)   # rgba(99,102,241,0.55)

    def __init__(self, featured: bool = False, parent=None):
        super().__init__(parent)
        self._featured = featured
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        self._inner = lay

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        bg     = self._YEARLY_BG     if self._featured else self._MONTHLY_BG
        border = self._YEARLY_BORDER if self._featured else self._MONTHLY_BORDER
        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border, 1))
        p.drawPath(path)


class _UpgradeBox(QWidget):
    """Outer upgrade card with an indigo-tinted painted background."""
    _BG     = QColor(99, 102, 241, 20)   # rgba(99,102,241,0.08)
    _BORDER = QColor(99, 102, 241, 82)   # rgba(99,102,241,0.32)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        self._inner = lay

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, QBrush(self._BG))
        p.setPen(QPen(self._BORDER, 1))
        p.drawPath(path)


# Button stylesheet constants — applied inline so parent cascade can't override
_PLAN_BTN_SS = (
    "QPushButton { background: #6366f1; border: none; border-radius: 8px; "
    "color: #ffffff; font-family: 'Manrope'; font-size: 12px; font-weight: 600; padding: 0px 14px; } "
    "QPushButton:hover { background: #818cf8; } "
    "QPushButton:pressed { background: #4f46e5; } "
    "QPushButton:disabled { background: rgba(99,102,241,0.3); color: rgba(255,255,255,0.4); }"
)
_PRIMARY_BTN_SS = (
    "QPushButton { background: #6366f1; border: none; border-radius: 10px; "
    "color: #ffffff; font-family: 'Manrope'; font-size: 12px; font-weight: 600; padding: 0px 18px; } "
    "QPushButton:hover { background: #818cf8; } "
    "QPushButton:pressed { background: #4f46e5; } "
    "QPushButton:disabled { background: rgba(99,102,241,0.3); color: rgba(255,255,255,0.4); }"
)


# ---------------------------------------------------------------------------
# Settings Page Base
# ---------------------------------------------------------------------------

class SettingsPage(QWidget):
    """
    Base class for a settings page content area with built-in scrolling.
    """
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        
        # Main layout for the page
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Scroll Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Content Widget
        self.content_widget = QWidget()
        self.content_widget.setObjectName("SettingsPageContent")
        
        # Content Layout
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(30, 30, 30, 30)
        self.content_layout.setSpacing(15)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Title
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("PageTitle")
        self.title_lbl.setFont(_font("Instrument Serif", 24))
        self.content_layout.addWidget(self.title_lbl)
        
        # Spacer after title
        self.content_layout.addSpacing(10)
        
        self.scroll.setWidget(self.content_widget)
        main_layout.addWidget(self.scroll)

    def add_widget(self, w: QWidget):
        self.content_layout.addWidget(w)

    def add_layout(self, lay):
        self.content_layout.addLayout(lay)
        
    def add_stretch(self):
        self.content_layout.addStretch()
        
    def add_spacing(self, spacing: int):
        self.content_layout.addSpacing(spacing)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class SettingsPanel(QWidget):
    closed = pyqtSignal()
    # Signals used to marshal results from background threads to the main thread.
    _checkout_ready     = pyqtSignal(str)   # emitted with checkout URL on success
    _checkout_error_sig = pyqtSignal(str)   # emitted with error message on failure
    _payment_detected   = pyqtSignal(object)  # emitted with payment data dict
    _dispatch           = pyqtSignal(object)  # carries a callable to run on the main thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_theme = "dark"
        self._pages = {}  # name -> widget
        self._build_ui()
        # Connect cross-thread signals (must be after _build_ui so self is fully set up).
        self._checkout_ready.connect(self._on_checkout_ready)
        self._checkout_error_sig.connect(self._on_checkout_error_occurred)
        self._payment_detected.connect(self._handle_payment_complete)
        self._dispatch.connect(lambda fn: fn())

    # ── Build ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("SettingsSidebar")
        self.sidebar.setFixedWidth(200)
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sidebar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.itemSelectionChanged.connect(self._on_sidebar_changed)
        
        # ── Content Area ─────────────────────────────────────────────
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("SettingsContent")

        # ── Build Pages ──────────────────────────────────────────────
        self._add_page("Language", self._build_transcription())
        self._add_page("AI Model", self._build_model())
        self._add_page("Trust", self._build_trust())
        self._add_page("Privacy", self._build_privacy())
        self._add_page("Account", self._build_account())
        self._add_page("Developer", self._build_developer())

        root.addWidget(self.sidebar)
        root.addWidget(self.content_stack)
        
        # Select first item by default
        if self.sidebar.count() > 0:
            self.sidebar.setCurrentRow(0)

    def _add_page(self, name: str, widget: QWidget):
        item = QListWidgetItem(name)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        item.setSizeHint(QSize(0, 40))
        
        self.sidebar.addItem(item)
        self.content_stack.addWidget(widget)
        self._pages[name] = widget

    # ── Section builders ─────────────────────────────────────────────

    def _build_transcription(self) -> QWidget:
        page = SettingsPage("Language")

        page.add_widget(self._desc(
            "Default language for speech recognition. "
            "Picking a specific language makes transcription faster and more accurate."
        ))

        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName("SettingsCombo")
        self.lang_combo.setFixedHeight(40)
        self.lang_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        saved = settings_store.get("transcription_language", "auto")
        for i, (code, name) in enumerate(LANGUAGES):
            self.lang_combo.addItem(name, code)
            if code == saved:
                self.lang_combo.setCurrentIndex(i)

        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        page.add_widget(self.lang_combo)
        
        page.add_stretch()
        return page

    def _build_model(self) -> QWidget:
        page = SettingsPage("AI Model")

        page.add_widget(self._desc(
            "Override the default model (xAI Grok) with any OpenAI-compatible API. "
            "Works with OpenAI, Ollama, LM Studio, Anthropic proxies, and more. "
            "Leave all fields empty to keep the default."
        ))

        page.add_widget(self._lbl("Personality mode"))
        page.add_widget(self._desc(
            "Professional is polished and focused. Unfiltered is uncensored, based, casual."
        ))

        # Container for the toggle (acting as the track)
        self.mode_container = QFrame()
        self.mode_container.setObjectName("ModeContainer")
        self.mode_container.setFixedHeight(44)
        
        mode_layout = QHBoxLayout(self.mode_container)
        mode_layout.setContentsMargins(4, 4, 4, 4)
        mode_layout.setSpacing(0)

        self.personality_group = QButtonGroup(self)
        self.personality_prof_btn = QPushButton("Professional")
        self.personality_unf_btn = QPushButton("Unfiltered")

        for btn in (self.personality_prof_btn, self.personality_unf_btn):
            btn.setObjectName("ModeBtn")
            btn.setCheckable(True)
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            mode_layout.addWidget(btn)

        self.personality_prof_btn.setProperty("mode", "professional")
        self.personality_unf_btn.setProperty("mode", "unfiltered")

        self.personality_group.setExclusive(True)
        self.personality_group.addButton(self.personality_prof_btn)
        self.personality_group.addButton(self.personality_unf_btn)

        saved_mode = settings_store.get("personality_mode", "professional")
        if saved_mode == "unfiltered":
            self.personality_unf_btn.setChecked(True)
        else:
            self.personality_prof_btn.setChecked(True)

        self.personality_group.buttonClicked.connect(self._on_personality_changed)

        page.add_widget(self.mode_container)
        
        page.add_spacing(10)

        page.add_widget(self._lbl("API Base URL"))
        self.url_edit = self._edit("e.g. https://api.openai.com/v1")
        self.url_edit.setText(settings_store.get("custom_api_url", ""))
        page.add_widget(self.url_edit)

        page.add_widget(self._lbl("API Key"))
        self.key_edit = self._edit("sk-...", password=True)
        self.key_edit.setText(settings_store.get("custom_api_key", ""))
        page.add_widget(self.key_edit)

        page.add_widget(self._lbl("Model name"))
        self.model_edit = self._edit("e.g. gpt-4o, claude-3-5-sonnet")
        self.model_edit.setText(settings_store.get("custom_model", ""))
        page.add_widget(self.model_edit)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 10, 0, 0)
        btn_row.setSpacing(10)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("SaveBtn")
        self.save_btn.setFixedHeight(38)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._save_model)

        self.reset_btn = QPushButton("Reset to default")
        self.reset_btn.setObjectName("ResetBtn")
        self.reset_btn.setFixedHeight(38)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self._reset_model)

        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        page.add_layout(btn_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("StatusLbl")
        self.status_lbl.setFont(_font("Manrope", 10))
        self.status_lbl.setWordWrap(True)
        page.add_widget(self.status_lbl)
        
        page.add_stretch()
        return page

    def _build_privacy(self) -> QWidget:
        page = SettingsPage("Privacy")
        
        page.add_widget(self._desc(
            "Omni is built with privacy as a first principle. "
            "Here's the full picture of what happens with your data."
        ))

        for heading, body_text in _PRIVACY_ITEMS:
            h = QLabel(heading)
            h.setObjectName("PrivacyHeading")
            h.setFont(_font("Manrope", 11, bold=True))
            h.setWordWrap(True)
            page.add_widget(h)

            b = QLabel(body_text)
            b.setObjectName("PrivacyBody")
            b.setFont(_font("Manrope", 10))
            b.setWordWrap(True)
            page.add_widget(b)
            
        page.add_stretch()
        return page
    def _build_trust(self) -> QWidget:
        page = SettingsPage("Trust")

        page.add_widget(self._desc(
            "Control what Omni is allowed to do on your behalf. "
            "You can always grant one-time permission for a single action "
            "without changing the global level."
        ))
        page.add_spacing(20)

        saved_level = settings_store.get("trust_level", 1)

        self._trust_slider = _TrustSlider(level=saved_level, theme=self.current_theme)
        self._trust_slider.level_changed.connect(self._on_trust_changed)
        page.add_widget(self._trust_slider)

        page.add_spacing(20)

        self._trust_cap_panel = _TrustCapabilityPanel(level=saved_level, theme=self.current_theme)
        page.add_widget(self._trust_cap_panel)

        page.add_stretch()
        return page

    def _build_account(self) -> QWidget:
        page = SettingsPage("Account")

        # ── Auth stack (logged-out / logged-in) ───────────────────────
        self.account_stack = QStackedWidget()
        self.account_stack.addWidget(self._build_auth_form())    # index 0 — logged out
        self.account_stack.addWidget(self._build_account_info()) # index 1 — logged in
        page.add_widget(self.account_stack)

        # ── Shared status label ───────────────────────────────────────
        self.account_status_lbl = QLabel("")
        self.account_status_lbl.setObjectName("AccountStatusLbl")
        self.account_status_lbl.setFont(_font("Manrope", 10))
        self.account_status_lbl.setWordWrap(True)
        page.add_widget(self.account_status_lbl)

        page.add_stretch()
        return page

    # ── Developer page ────────────────────────────────────────────────

    def _build_developer(self) -> QWidget:
        page = SettingsPage("Developer")

        page.add_widget(self._desc(
            "Development tools — for local testing only. "
            "These settings are not synced and have no effect in production builds."
        ))
        page.add_spacing(12)

        # Pro override toggle row
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        lbl = QLabel("Pro override")
        lbl.setObjectName("FieldLbl")
        lbl.setFont(_font("Manrope", 11, bold=True))
        row.addWidget(lbl)
        row.addStretch()

        self._dev_pro_btn = QPushButton()
        self._dev_pro_btn.setCheckable(True)
        self._dev_pro_btn.setChecked(settings_store.get("dev_pro_override", False))
        self._dev_pro_btn.setFixedSize(52, 28)
        self._dev_pro_btn.setObjectName("ToggleBtn")
        self._dev_pro_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dev_pro_btn.toggled.connect(self._on_dev_pro_toggled)
        self._dev_update_toggle_text()
        row.addWidget(self._dev_pro_btn)

        page.add_layout(row)

        desc = QLabel(
            "When on, all subscription checks return Pro and daily limits are skipped. "
            "Restart is not required — takes effect on the next status refresh."
        )
        desc.setObjectName("DescLbl")
        desc.setFont(_font("Manrope", 10))
        desc.setWordWrap(True)
        page.add_widget(desc)

        page.add_stretch()
        return page

    def _dev_update_toggle_text(self):
        on = self._dev_pro_btn.isChecked()
        self._dev_pro_btn.setText("ON" if on else "OFF")

    def _on_dev_pro_toggled(self, checked: bool):
        settings_store.set("dev_pro_override", checked)
        self._dev_update_toggle_text()
        # Immediately re-fetch subscription so the Account tab reflects the change.
        subscription.refresh_status(
            callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s))
        )

    # ── Logged-out: sign-in / sign-up form ───────────────────────────

    def _build_auth_form(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ── Upgrade CTA (primary — shown first so it's the hero action) ──
        self.upgrade_box_login = _UpgradeBox()
        ub = self.upgrade_box_login._inner

        title = QLabel("Upgrade to Pro")
        title.setFont(_font("Manrope", 15, bold=True))
        ub.addWidget(title)

        tagline = QLabel("Unlimited AI queries, no daily limits.")
        tagline.setObjectName("DescLbl")
        tagline.setFont(_font("Manrope", 11))
        ub.addWidget(tagline)

        ub.addSpacing(2)

        # Monthly plan card
        monthly_card = _PlanCard(featured=False)
        mc = monthly_card._inner
        mc_info = QVBoxLayout()
        mc_info.setSpacing(2)
        mc_name = QLabel("Monthly")
        mc_name.setFont(_font("Manrope", 12, bold=True))
        mc_desc = QLabel("Billed month to month")
        mc_desc.setObjectName("PlanCardDesc")
        mc_info.addWidget(mc_name)
        mc_info.addWidget(mc_desc)
        mc.addLayout(mc_info)
        mc.addStretch()
        self.upgrade_monthly_btn_login = QPushButton("Choose  →")
        self.upgrade_monthly_btn_login.setFixedHeight(34)
        self.upgrade_monthly_btn_login.setFixedWidth(110)
        self.upgrade_monthly_btn_login.setStyleSheet(_PLAN_BTN_SS)
        self.upgrade_monthly_btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upgrade_monthly_btn_login.clicked.connect(lambda: self._start_checkout("monthly"))
        mc.addWidget(self.upgrade_monthly_btn_login)
        ub.addWidget(monthly_card)

        # Yearly plan card (featured)
        yearly_card = _PlanCard(featured=True)
        yc = yearly_card._inner
        yc_info = QVBoxLayout()
        yc_info.setSpacing(2)
        yc_name_row = QHBoxLayout()
        yc_name_row.setSpacing(6)
        yc_name_row.setContentsMargins(0, 0, 0, 0)
        yc_name = QLabel("Yearly")
        yc_name.setFont(_font("Manrope", 12, bold=True))
        yc_badge = QLabel("Best value")
        yc_badge.setObjectName("BestValueBadge")
        yc_badge.setFont(_font("Manrope", 9, bold=True))
        yc_name_row.addWidget(yc_name)
        yc_name_row.addWidget(yc_badge)
        yc_name_row.addStretch()
        yc_desc = QLabel("Billed annually")
        yc_desc.setObjectName("PlanCardDesc")
        yc_info.addLayout(yc_name_row)
        yc_info.addWidget(yc_desc)
        yc.addLayout(yc_info)
        yc.addStretch()
        self.upgrade_yearly_btn_login = QPushButton("Choose  →")
        self.upgrade_yearly_btn_login.setFixedHeight(34)
        self.upgrade_yearly_btn_login.setFixedWidth(110)
        self.upgrade_yearly_btn_login.setStyleSheet(_PLAN_BTN_SS)
        self.upgrade_yearly_btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upgrade_yearly_btn_login.clicked.connect(lambda: self._start_checkout("yearly"))
        yc.addWidget(self.upgrade_yearly_btn_login)
        ub.addWidget(yearly_card)

        self.upgrade_status_lbl_login = QLabel("")
        self.upgrade_status_lbl_login.setObjectName("DescLbl")
        self.upgrade_status_lbl_login.setFont(_font("Manrope", 10))
        self.upgrade_status_lbl_login.setWordWrap(True)
        ub.addWidget(self.upgrade_status_lbl_login)

        lay.addWidget(self.upgrade_box_login)

        # ── Divider ───────────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setObjectName("SepLine")
        lay.addWidget(div)

        # ── Sign-in / Sign-up form ────────────────────────────────────────
        lay.addWidget(self._desc(
            "Already paid? Sign in with your checkout email to activate Pro."
        ))

        self.auth_email_edit = self._edit("Email")
        self.auth_email_edit.setObjectName("SettingsEdit")
        lay.addWidget(self.auth_email_edit)

        self.auth_pass_edit = self._edit("Password", password=True)
        self.auth_pass_edit.setObjectName("SettingsEdit")
        self.auth_pass_edit.returnPressed.connect(self._do_sign_in)
        lay.addWidget(self.auth_pass_edit)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(8)

        self.sign_in_btn = QPushButton("Sign In")
        self.sign_in_btn.setFixedHeight(38)
        self.sign_in_btn.setStyleSheet(_PRIMARY_BTN_SS)
        self.sign_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_in_btn.clicked.connect(self._do_sign_in)

        self.sign_up_btn = QPushButton("Sign Up")
        self.sign_up_btn.setObjectName("ResetBtn")
        self.sign_up_btn.setFixedHeight(38)
        self.sign_up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_up_btn.clicked.connect(self._do_sign_up)

        btn_row.addWidget(self.sign_in_btn)
        btn_row.addWidget(self.sign_up_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        sep_row = QHBoxLayout()
        sep_row.setContentsMargins(0, 8, 0, 8)
        sep_l = QFrame(); sep_l.setFrameShape(QFrame.Shape.HLine)
        sep_l.setObjectName("SepLine")
        sep_r = QFrame(); sep_r.setFrameShape(QFrame.Shape.HLine)
        sep_r.setObjectName("SepLine")
        sep_lbl = QLabel("or")
        sep_lbl.setObjectName("DescLbl")
        sep_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep_lbl.setFixedWidth(24)
        sep_row.addWidget(sep_l)
        sep_row.addWidget(sep_lbl)
        sep_row.addWidget(sep_r)
        lay.addLayout(sep_row)

        self.google_btn = QPushButton("Continue with Google")
        self.google_btn.setObjectName("OAuthBtn")
        self.google_btn.setFixedHeight(38)
        self.google_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.google_btn.clicked.connect(lambda: self._do_oauth("google"))
        lay.addWidget(self.google_btn)

        self.github_btn = QPushButton("Continue with GitHub")
        self.github_btn.setObjectName("OAuthBtn")
        self.github_btn.setFixedHeight(38)
        self.github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.github_btn.clicked.connect(lambda: self._do_oauth("github"))
        lay.addWidget(self.github_btn)

        return w

    # ── Logged-in: account info ───────────────────────────────────────

    def _build_account_info(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # Email + plan badge row
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(10)

        self.account_email_lbl = QLabel("")
        self.account_email_lbl.setObjectName("FieldLbl")
        self.account_email_lbl.setFont(_font("Manrope", 11))

        self.plan_badge = QLabel("FREE")
        self.plan_badge.setObjectName("PlanBadgeFree")
        self.plan_badge.setFont(_font("Manrope", 10, bold=True))
        self.plan_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.plan_badge.setFixedHeight(28)
        self.plan_badge.setFixedWidth(60)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("RefreshBtn")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("Refresh plan status")
        self.refresh_btn.clicked.connect(self.refresh_account)

        info_row.addWidget(self.account_email_lbl)
        info_row.addStretch()
        info_row.addWidget(self.plan_badge)
        info_row.addWidget(self.refresh_btn)
        lay.addLayout(info_row)

        # Usage bar + count (hidden for pro)
        self.usage_bar = _UsageBar()
        lay.addWidget(self.usage_bar)

        self.usage_lbl = QLabel("0 / 10 requests today")
        self.usage_lbl.setObjectName("UsageLbl")
        self.usage_lbl.setFont(_font("Manrope", 10))
        lay.addWidget(self.usage_lbl)

        # Upgrade CTA (hidden for pro)
        self.upgrade_box = _UpgradeBox()
        ub = self.upgrade_box._inner

        title = QLabel("Upgrade to Pro")
        title.setFont(_font("Manrope", 15, bold=True))
        ub.addWidget(title)

        tagline = QLabel("Unlimited AI queries, no daily limits.")
        tagline.setObjectName("DescLbl")
        tagline.setFont(_font("Manrope", 11))
        ub.addWidget(tagline)

        ub.addSpacing(2)

        # Monthly plan card
        monthly_card = _PlanCard(featured=False)
        mc = monthly_card._inner
        mc_info = QVBoxLayout()
        mc_info.setSpacing(2)
        mc_name = QLabel("Monthly")
        mc_name.setFont(_font("Manrope", 12, bold=True))
        mc_desc = QLabel("Billed month to month")
        mc_desc.setObjectName("PlanCardDesc")
        mc_info.addWidget(mc_name)
        mc_info.addWidget(mc_desc)
        mc.addLayout(mc_info)
        mc.addStretch()
        self.upgrade_monthly_btn = QPushButton("Choose  →")
        self.upgrade_monthly_btn.setFixedHeight(34)
        self.upgrade_monthly_btn.setFixedWidth(110)
        self.upgrade_monthly_btn.setStyleSheet(_PLAN_BTN_SS)
        self.upgrade_monthly_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upgrade_monthly_btn.clicked.connect(lambda: self._start_checkout("monthly"))
        mc.addWidget(self.upgrade_monthly_btn)
        ub.addWidget(monthly_card)

        # Yearly plan card (featured)
        yearly_card = _PlanCard(featured=True)
        yc = yearly_card._inner
        yc_info = QVBoxLayout()
        yc_info.setSpacing(2)
        yc_name_row = QHBoxLayout()
        yc_name_row.setSpacing(6)
        yc_name_row.setContentsMargins(0, 0, 0, 0)
        yc_name = QLabel("Yearly")
        yc_name.setFont(_font("Manrope", 12, bold=True))
        yc_badge = QLabel("Best value")
        yc_badge.setObjectName("BestValueBadge")
        yc_badge.setFont(_font("Manrope", 9, bold=True))
        yc_name_row.addWidget(yc_name)
        yc_name_row.addWidget(yc_badge)
        yc_name_row.addStretch()
        yc_desc = QLabel("Billed annually")
        yc_desc.setObjectName("PlanCardDesc")
        yc_info.addLayout(yc_name_row)
        yc_info.addWidget(yc_desc)
        yc.addLayout(yc_info)
        yc.addStretch()
        self.upgrade_yearly_btn = QPushButton("Choose  →")
        self.upgrade_yearly_btn.setFixedHeight(34)
        self.upgrade_yearly_btn.setFixedWidth(110)
        self.upgrade_yearly_btn.setStyleSheet(_PLAN_BTN_SS)
        self.upgrade_yearly_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upgrade_yearly_btn.clicked.connect(lambda: self._start_checkout("yearly"))
        yc.addWidget(self.upgrade_yearly_btn)
        ub.addWidget(yearly_card)

        self.upgrade_status_lbl = QLabel("")
        self.upgrade_status_lbl.setObjectName("DescLbl")
        self.upgrade_status_lbl.setFont(_font("Manrope", 10))
        self.upgrade_status_lbl.setWordWrap(True)
        ub.addWidget(self.upgrade_status_lbl)

        lay.addWidget(self.upgrade_box)

        # Sync status row
        sync_row = QHBoxLayout()
        sync_row.setContentsMargins(0, 4, 0, 4)
        sync_row.setSpacing(8)

        self.sync_status_lbl = QLabel("Memory sync: —")
        self.sync_status_lbl.setObjectName("UsageLbl")
        self.sync_status_lbl.setFont(_font("Manrope", 10))

        self.sync_btn = QPushButton("Sync Now")
        self.sync_btn.setObjectName("ResetBtn")
        self.sync_btn.setFixedHeight(28)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.clicked.connect(self._do_sync_now)

        sync_row.addWidget(self.sync_status_lbl)
        sync_row.addStretch()
        sync_row.addWidget(self.sync_btn)
        lay.addLayout(sync_row)

        # Account Security — collapsible, hidden by default
        self._security_toggle_btn = QPushButton("Account Security  ▾")
        self._security_toggle_btn.setObjectName("ResetBtn")
        self._security_toggle_btn.setFixedHeight(32)
        self._security_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._security_toggle_btn.clicked.connect(self._toggle_security)
        lay.addWidget(self._security_toggle_btn)

        self.secure_box = QFrame()
        self.secure_box.setObjectName("UpgradeBox")
        self.secure_box.setVisible(False)
        sb = QVBoxLayout(self.secure_box)
        sb.setContentsMargins(12, 12, 12, 12)
        sb.setSpacing(8)

        secure_desc = QLabel("Set a password or connect Google so you can sign in without a magic link.")
        secure_desc.setObjectName("DescLbl")
        secure_desc.setFont(_font("Manrope", 10))
        secure_desc.setWordWrap(True)
        sb.addWidget(secure_desc)

        self.new_password_edit = QLineEdit()
        self.new_password_edit.setObjectName("SettingsEdit")
        self.new_password_edit.setPlaceholderText("New password (min. 6 chars)")
        self.new_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password_edit.setFixedHeight(38)
        self.new_password_edit.returnPressed.connect(self._do_set_password)
        sb.addWidget(self.new_password_edit)

        secure_btns = QHBoxLayout()
        secure_btns.setContentsMargins(0, 2, 0, 0)
        secure_btns.setSpacing(8)

        self.set_password_btn = QPushButton("Set Password")
        self.set_password_btn.setObjectName("SaveBtn")
        self.set_password_btn.setFixedHeight(36)
        self.set_password_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_password_btn.clicked.connect(self._do_set_password)

        self.link_google_btn = QPushButton("Connect Google")
        self.link_google_btn.setObjectName("OAuthBtn")
        self.link_google_btn.setFixedHeight(36)
        self.link_google_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_google_btn.clicked.connect(lambda: self._do_oauth("google"))

        secure_btns.addWidget(self.set_password_btn)
        secure_btns.addWidget(self.link_google_btn)
        secure_btns.addStretch()
        sb.addLayout(secure_btns)

        lay.addWidget(self.secure_box)

        # Sign out
        self.sign_out_btn = QPushButton("Sign Out")
        self.sign_out_btn.setObjectName("ResetBtn")
        self.sign_out_btn.setFixedHeight(38)
        self.sign_out_btn.setFixedWidth(100)
        self.sign_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_out_btn.clicked.connect(self._do_sign_out)
        lay.addWidget(self.sign_out_btn)

        return w
    # ── Widget factories ─────────────────────────────────────────────

    def _desc(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("DescLbl")
        lbl.setFont(_font("Manrope", 10))
        lbl.setWordWrap(True)
        return lbl

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("FieldLbl")
        lbl.setFont(_font("Manrope", 10, bold=True))
        return lbl

    def _edit(self, placeholder: str, password: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName("SettingsEdit")
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(40)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        return edit

    # ── Slots ────────────────────────────────────────────────────────

    def _on_sidebar_changed(self):
        row = self.sidebar.currentRow()
        if row >= 0:
            self.content_stack.setCurrentIndex(row)
            item = self.sidebar.item(row)
            if item and item.text() == "Account":
                self.refresh_account()

    def _on_lang_changed(self, index: int):
        settings_store.set("transcription_language", self.lang_combo.itemData(index))

    def _on_trust_changed(self, level: int):
        settings_store.set("trust_level", level)
        if hasattr(self, "_trust_slider"):
            self._trust_slider.set_level(level)
        if hasattr(self, "_trust_cap_panel"):
            self._trust_cap_panel.set_level(level)

    def _on_personality_changed(self):
        btn = self.personality_group.checkedButton()
        if btn is None:
            return
        mode = btn.property("mode") or "professional"
        settings_store.set("personality_mode", mode)

    def _save_model(self):
        url   = self.url_edit.text().strip()
        key   = self.key_edit.text().strip()
        model = self.model_edit.text().strip()

        if (url or key or model) and not (url and key and model):
            self._status("Fill in all three fields (URL, key, model) or leave them all empty.", error=True)
            return

        settings_store.save_settings({"custom_api_url": url, "custom_api_key": key, "custom_model": model})
        self._status(
            "Saved — changes take effect after restarting Omni." if url
            else "Reset to default model (xAI Grok) — restart required."
        )

    def _reset_model(self):
        self.url_edit.clear()
        self.key_edit.clear()
        self.model_edit.clear()
        settings_store.save_settings({"custom_api_url": "", "custom_api_key": "", "custom_model": ""})
        self._status("Default model restored — restart required.")

    def _status(self, msg: str, error: bool = False):
        self.status_lbl.setText(msg)
        self.status_lbl.setProperty("error", "true" if error else "false")
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)
        QTimer.singleShot(6000, lambda: self.status_lbl.setText(""))

    # ── Account tab ──────────────────────────────────────────────────

    def refresh_account(self):
        """Refresh both auth state and subscription status."""
        # Always clear stale checkout messages — the poller keeps running silently in
        # the background, and _handle_payment_complete will show "Payment confirmed!"
        # if/when it fires. Leaving the "waiting" message visible after the user
        # manually opens the app is confusing.
        _stale_msgs = {"Checkout opened — waiting for payment…", "Opening checkout…"}
        for _attr in ("upgrade_status_lbl", "upgrade_status_lbl_login"):
            _lbl = getattr(self, _attr, None)
            if _lbl and _lbl.text() in _stale_msgs:
                _lbl.setText("")

        # One-shot check for a pending confirmed payment — covers the case where the
        # user paid while the window was hidden / after restarting, so the continuous
        # poller never fired.  Run regardless of login state so logged-in users who
        # paid are also detected here.
        self._check_pending_payment()

        # Show correct stack page
        logged_in = auth.is_logged_in()
        self.account_stack.setCurrentIndex(1 if logged_in else 0)

        def _on_done(status):
            self._dispatch.emit(lambda: self._update_account_ui(status))

        if logged_in:
            user = auth.get_user() or {}
            self.account_email_lbl.setText(user.get("email", ""))
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("…")
            providers = (user.get("app_metadata") or {}).get("providers") or []
            if hasattr(self, "link_google_btn"):
                self.link_google_btn.setVisible("google" not in providers)

        # Always refresh subscription status — even when not logged in, so the device
        # subscription (keyed by device_id) is reflected in the UI.
        subscription.refresh_status(callback=_on_done)

        # Hook sync status updates
        from src.services.sync import memory_sync
        memory_sync.add_listener(lambda s: QTimer.singleShot(0, lambda: self._update_sync_ui(s)))
        self._update_sync_ui(memory_sync.get_state())

    def _update_account_ui(self, status: dict):
        print(f"[ui] _update_account_ui called: plan={status.get('plan')}")
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("↻")

        plan        = status.get("plan", "free")
        daily_usage = status.get("daily_usage", 0)
        daily_limit = status.get("daily_limit", 10)
        error       = status.get("error")
        is_pro      = (plan == "pro")

        if is_pro:
            self.plan_badge.setText("PRO")
            self.plan_badge.setObjectName("PlanBadgePro")
        else:
            self.plan_badge.setText("FREE")
            self.plan_badge.setObjectName("PlanBadgeFree")
        self.plan_badge.style().unpolish(self.plan_badge)
        self.plan_badge.style().polish(self.plan_badge)

        self.usage_bar.setVisible(not is_pro)
        self.usage_lbl.setVisible(not is_pro)
        if hasattr(self, "upgrade_box"):
            self.upgrade_box.setVisible(not is_pro)
        # Also update the not-logged-in upgrade box (device may be pro without account).
        if hasattr(self, "upgrade_box_login"):
            self.upgrade_box_login.setVisible(not is_pro)
        if not is_pro:
            self.usage_bar.set_fraction(daily_usage, daily_limit)
            self.usage_lbl.setText(f"{daily_usage} / {daily_limit} requests today")

        if error:
            self._account_status(f"Could not refresh: {error}", error=True)
        else:
            self.account_status_lbl.setText("")

    def _set_checkout_busy(self, busy: bool):
        for w in (
            getattr(self, "upgrade_monthly_btn", None),
            getattr(self, "upgrade_yearly_btn", None),
            getattr(self, "upgrade_monthly_btn_login", None),
            getattr(self, "upgrade_yearly_btn_login", None),
            getattr(self, "refresh_btn", None),
        ):
            if w is not None:
                w.setEnabled(not busy)
        if hasattr(self, "upgrade_status_lbl"):
            self.upgrade_status_lbl.setText("Opening checkout…" if busy else "")
        if hasattr(self, "upgrade_status_lbl_login"):
            self.upgrade_status_lbl_login.setText("Opening checkout…" if busy else "")

    def _start_checkout(self, interval: str):
        self._set_checkout_busy(True)

        token = auth.get_access_token()
        user = auth.get_user() or {}
        email = user.get("email") or None
        name = None
        um = user.get("user_metadata")
        if isinstance(um, dict):
            name = um.get("full_name") or um.get("name") or None
        if not email and hasattr(self, "auth_email_edit"):
            email = self.auth_email_edit.text().strip() or None

        import threading

        def _run():
            print(f"[Settings] Starting checkout for interval: {interval}")
            import webbrowser
            try:
                print("[Settings] Calling billing.create_pro_checkout_url...")
                url = billing.create_pro_checkout_url(
                    interval=interval,
                    access_token=token,
                    customer_email=email,
                    customer_name=name,
                )
                print(f"[Settings] Checkout URL generated: {url}")
                # Emit signal — guaranteed to be delivered on the main thread.
                self._checkout_ready.emit(url)
                print(f"[Settings] Opening browser: {url}")
                webbrowser.open(url)
                print("[Settings] Browser opened")
            except Exception as e:
                print(f"[Settings] Checkout failed with error: {e}")
                import traceback
                traceback.print_exc()
                self._checkout_error_sig.emit(f"Could not open checkout: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_checkout_ready(self, url: str):
        """Called on the main thread once the checkout URL is ready."""
        self._set_checkout_busy(False)
        self._payment_handled = False  # Reset guard so this checkout can be handled
        if hasattr(self, "upgrade_status_lbl"):
            self.upgrade_status_lbl.setText("After paying, press ↻ to activate your Pro plan.")
        if hasattr(self, "upgrade_status_lbl_login"):
            self.upgrade_status_lbl_login.setText("After paying, sign in below with your checkout email to activate Pro.")
        import threading as _threading
        self._checkout_stop = _threading.Event()

    def _on_checkout_error_occurred(self, err_msg: str):
        """Called on the main thread when checkout URL creation fails."""
        self._set_checkout_busy(False)
        if hasattr(self, "upgrade_status_lbl"):
            self.upgrade_status_lbl.setText(err_msg)
        if hasattr(self, "upgrade_status_lbl_login"):
            self.upgrade_status_lbl_login.setText(err_msg)

    def _check_pending_payment(self):
        """One-shot background check of session_status.
        If the worker has a confirmed payment for this device that we haven't
        processed yet (e.g. app was restarted after paying), process it now.
        """
        if getattr(self, "_payment_check_in_flight", False):
            return
        if getattr(self, "_payment_handled", False):
            return
        self._payment_check_in_flight = True

        import threading, urllib.request, json as _json

        token = auth.get_access_token()

        def _run():
            try:
                headers = {
                    "X-Omni-Secret": OMNI_SECRET,
                    "X-Device-ID":   DEVICE_ID,
                }
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                req = urllib.request.Request(
                    f"{BACKEND_URL}/v1/billing/session_status",
                    headers=headers,
                )
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = _json.loads(r.read())
                if data.get("paid"):
                    self._payment_detected.emit(data)
            except Exception:
                pass
            finally:
                self._payment_check_in_flight = False

        threading.Thread(target=_run, daemon=True).start()

    def _handle_payment_complete(self, data: dict):
        """Called on the main thread when the payment poller detects a successful payment."""
        # Guard against re-entry: the poller AND _check_pending_payment can both fire.
        if getattr(self, "_payment_handled", False):
            return
        self._payment_handled = True

        # Stop the poller (already stopped itself, but be safe)
        if hasattr(self, "_checkout_stop"):
            self._checkout_stop.set()

        # Bring the app window back to front and ensure settings/Account tab is visible.
        win = self.window()
        if win is not None:
            if not win.isVisible():
                # Show the window directly (avoid toggle_visibility_safe which could debounce).
                win.setWindowOpacity(0.0)
                win.show()
                win.center()
                if win.is_settings_mode:
                    win.resize(win.width(), win._SETTINGS_HEIGHT)
                win.animate_entry()
                win.force_focus()
            else:
                win.raise_()
                win.activateWindow()
                win.force_focus()

            # Ensure settings panel is visible — the QGraphicsOpacityEffect from a
            # partial enter_settings_mode animation can leave it at opacity 0.
            self.setGraphicsEffect(None)
            self.show()

            # Enter settings mode if not already in it (e.g. user closed settings before paying).
            if not win.is_settings_mode:
                win.enter_settings_mode()

            # Give animations time to settle before switching the sidebar tab.
            QTimer.singleShot(400, self._focus_account_tab)

        magic_link = data.get("magic_link") or ""
        email      = data.get("email") or ""

        if magic_link and not auth.is_logged_in():
            # Auto-login via the magic link the worker generated.
            msg = f"Payment confirmed! Signing in as {email}…" if email else "Payment confirmed! Signing you in…"
            if hasattr(self, "upgrade_status_lbl"):
                self.upgrade_status_lbl.setText(msg)
            if hasattr(self, "upgrade_status_lbl_login"):
                self.upgrade_status_lbl_login.setText(msg)

            def _on_login(ok, login_msg):
                def _done():
                    if ok:
                        self.refresh_account()
                        subscription.refresh_status(
                            callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s))
                        )
                        QTimer.singleShot(300, self._show_secure_account_prompt)
                    else:
                        # Still refresh subscription — device is already unlocked.
                        ok_msg = "Payment confirmed! Sign in to enable sync."
                        if hasattr(self, "upgrade_status_lbl"):
                            self.upgrade_status_lbl.setText(ok_msg)
                        if hasattr(self, "upgrade_status_lbl_login"):
                            self.upgrade_status_lbl_login.setText(ok_msg)
                        subscription.refresh_status(
                            callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s))
                        )
                QTimer.singleShot(0, _done)

            auth.exchange_magic_link(magic_link, _on_login)
        else:
            # User was already logged in, or no magic link available — just refresh.
            self.refresh_account()
            subscription.refresh_status(
                callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s))
            )
            if hasattr(self, "upgrade_status_lbl"):
                self.upgrade_status_lbl.setText("Payment confirmed! Plan updated.")
            if hasattr(self, "upgrade_status_lbl_login"):
                self.upgrade_status_lbl_login.setText("Payment confirmed! Plan updated.")

    def _focus_account_tab(self):
        """Switch the settings sidebar to the Account page."""
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).text() == "Account":
                self.sidebar.setCurrentRow(i)
                break

    def _toggle_security(self):
        """Expand / collapse the Account Security box."""
        visible = self.secure_box.isVisible()
        self.secure_box.setVisible(not visible)
        self._security_toggle_btn.setText(
            "Account Security  ▴" if not visible else "Account Security  ▾"
        )

    def _show_secure_account_prompt(self):
        """Reveal the 'Account Security' box after auto-login from checkout."""
        if hasattr(self, "secure_box"):
            self.secure_box.setVisible(True)
        if hasattr(self, "_security_toggle_btn"):
            self._security_toggle_btn.setText("Account Security  ▴")
        # Switch to the logged-in Account view if not already there.
        if auth.is_logged_in() and self.account_stack.currentIndex() != 1:
            self.account_stack.setCurrentIndex(1)

    def _update_sync_ui(self, state: dict):
        s = state.get("state", "idle")
        last = state.get("last_synced")
        err  = state.get("error")
        if s == "syncing":
            self.sync_status_lbl.setText("Memory sync: syncing…")
        elif s == "synced" and last:
            self.sync_status_lbl.setText(f"Memory sync: last synced {last}")
        elif s == "error":
            self.sync_status_lbl.setText(f"Memory sync: {err or 'error'}")
        else:
            self.sync_status_lbl.setText("Memory sync: —")

    # ── Auth actions ─────────────────────────────────────────────────

    def _set_auth_busy(self, busy: bool):
        for w in (self.sign_in_btn, self.sign_up_btn, self.google_btn, self.github_btn,
                  self.auth_email_edit, self.auth_pass_edit):
            w.setEnabled(not busy)
        for w in (
            getattr(self, "set_password_btn", None),
            getattr(self, "link_google_btn", None),
        ):
            if w is not None:
                w.setEnabled(not busy)
        if busy:
            self.sign_in_btn.setText("…")
        else:
            self.sign_in_btn.setText("Sign In")

    def _do_sign_in(self):
        email = self.auth_email_edit.text().strip()
        pw    = self.auth_pass_edit.text()
        if not email or not pw:
            return
        self._set_auth_busy(True)

        import threading
        def _run():
            ok, msg = auth.sign_in(email, pw)
            def _done():
                self._set_auth_busy(False)
                if ok:
                    self.auth_pass_edit.clear()
                    self.refresh_account()
                    subscription.refresh_status(callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s)))
                else:
                    self._account_status(msg, error=True)
            self._dispatch.emit(_done)
        threading.Thread(target=_run, daemon=True).start()

    def _do_sign_up(self):
        email = self.auth_email_edit.text().strip()
        pw    = self.auth_pass_edit.text()
        if not email or not pw:
            return
        self._set_auth_busy(True)

        import threading
        def _run():
            ok, msg = auth.sign_up(email, pw)
            def _done():
                self._set_auth_busy(False)
                self._account_status(msg, error=not ok)
                if ok and auth.is_logged_in():
                    self.refresh_account()
            self._dispatch.emit(_done)
        threading.Thread(target=_run, daemon=True).start()

    def _do_oauth(self, provider: str):
        self._set_auth_busy(True)
        self._account_status(f"Opening browser for {provider} sign-in…")

        def _on_done(ok, msg):
            def _finish():
                self._set_auth_busy(False)
                if ok:
                    self.refresh_account()
                    subscription.refresh_status(callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s)))
                else:
                    self._account_status(msg, error=True)
            self._dispatch.emit(_finish)

        auth.start_oauth(provider, _on_done)

    def _do_set_password(self):
        pw = self.new_password_edit.text()
        if len(pw) < 6:
            self._account_status("Password must be at least 6 characters.", error=True)
            return
        self.set_password_btn.setEnabled(False)
        self.set_password_btn.setText("…")

        import threading
        def _run():
            ok, msg = auth.update_password(pw)
            def _done():
                self.set_password_btn.setEnabled(True)
                self.set_password_btn.setText("Set Password")
                self._account_status(msg, error=not ok)
                if ok:
                    self.new_password_edit.clear()
            QTimer.singleShot(0, _done)
        threading.Thread(target=_run, daemon=True).start()

    def _do_sign_out(self):
        auth.sign_out()
        self.account_stack.setCurrentIndex(0)
        self.account_status_lbl.setText("")

    def _do_sync_now(self):
        from src.services.sync import memory_sync
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("…")

        def _on_sync(state):
            def _done():
                self.sync_btn.setEnabled(True)
                self.sync_btn.setText("Sync Now")
                self._update_sync_ui(state)
            self._dispatch.emit(_done)

        memory_sync.add_listener(_on_sync)
        memory_sync.sync_now()

    def _account_status(self, msg: str, error: bool = False):
        self.account_status_lbl.setText(msg)
        self.account_status_lbl.setProperty("error", "true" if error else "false")
        self.account_status_lbl.style().unpolish(self.account_status_lbl)
        self.account_status_lbl.style().polish(self.account_status_lbl)
        QTimer.singleShot(8000, lambda: self.account_status_lbl.setText(""))

    # ── Theming ──────────────────────────────────────────────────────

    def set_theme(self, theme_name: str):
        self.current_theme = theme_name
        t = THEMES.get(theme_name, THEMES["dark"])
        dark = theme_name == "dark"

        primary    = t["text_primary"]
        secondary  = t["text_secondary"]
        border     = t["border_color"]
        sel_border = t["selection_border"]
        selection  = t["selection_bg"]
        scrollbar  = t["scrollbar_handle"]

        field_bg       = "rgba(255,255,255,0.12)" if dark else "rgba(0,0,0,0.08)"
        field_bg_focus = "rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.12)"
        btn_bg         = "rgba(255,255,255,0.15)" if dark else "rgba(0,0,0,0.10)"
        btn_hover      = "rgba(255,255,255,0.20)" if dark else "rgba(0,0,0,0.15)"
        btn_press      = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.05)"
        
        sidebar_bg     = "rgba(0,0,0,0.3)" if dark else "rgba(0,0,0,0.05)"
        item_hover     = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.08)"
        item_selected  = "rgba(255,255,255,0.15)" if dark else "rgba(0,0,0,0.12)"
        
        toggle_track   = "rgba(0,0,0,0.2)" if dark else "rgba(0,0,0,0.05)"
        toggle_bg      = "rgba(255,255,255,0.1)" if dark else "#FFFFFF"
        toggle_text    = primary
        toggle_text_off= secondary

        reset_color    = secondary
        combo_popup_bg = "#1c1c1c" if dark else "#f3f3f3"

        # Propagate theme to trust widgets
        if hasattr(self, "_trust_slider"):
            self._trust_slider.set_theme(theme_name)
        if hasattr(self, "_trust_cap_panel"):
            self._trust_cap_panel.set_theme(theme_name)

        # Apply to pages
        for name, page in self._pages.items():
            page.title_lbl.setStyleSheet(f"color: {primary};")
            
        self.setStyleSheet(f"""
            QWidget {{ background: transparent; }}
            
            /* Sidebar */
            QListWidget#SettingsSidebar {{
                background: {sidebar_bg};
                border: none;
                border-right: 1px solid {border};
                outline: none;
                padding-top: 20px;
            }}
            QListWidget#SettingsSidebar::item {{
                height: 40px;
                padding-left: 15px;
                color: {secondary};
                border-radius: 6px;
                margin: 2px 10px;
            }}
            QListWidget#SettingsSidebar::item:hover {{
                background: {item_hover};
                color: {primary};
            }}
            QListWidget#SettingsSidebar::item:selected {{
                background: {item_selected};
                color: {primary};
                font-weight: bold;
            }}
            
            /* Content Area */
            QWidget#SettingsContent {{
                background: transparent;
            }}

            /* labels */
            QLabel {{ background: transparent; color: {primary}; }}
            QLabel#DescLbl, QLabel#FieldLbl, QLabel#PrivacyBody {{
                color: {secondary};
            }}
            QLabel#PrivacyHeading {{
                color: {primary};
                margin-top: 8px;
            }}
            QLabel#StatusLbl {{
                color: {secondary};
                font-family: "Manrope";
                font-size: 10px;
            }}
            QLabel#StatusLbl[error="true"] {{ color: #ff5f5f; }}

            /* inputs */
            QLineEdit#SettingsEdit {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 0px 13px;
                color: {primary};
                font-family: "Manrope";
                font-style: normal;
                font-size: 13px;
            }}
            QLineEdit#SettingsEdit:focus {{
                background: {field_bg_focus};
                border: 1px solid {sel_border};
            }}

            /* combo */
            QComboBox#SettingsCombo {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 0px 13px;
                color: {primary};
                font-family: "Manrope";
                font-style: normal;
                font-size: 13px;
            }}
            QComboBox#SettingsCombo:focus {{
                border: 1px solid {sel_border};
            }}
            QComboBox#SettingsCombo::drop-down {{
                border: none;
                width: 28px;
                subcontrol-position: right center;
            }}
            QComboBox#SettingsCombo::down-arrow {{
                width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {secondary};
            }}
            QComboBox#SettingsCombo QAbstractItemView {{
                background: {combo_popup_bg};
                color: {primary};
                border: 1px solid {border};
                border-radius: 8px;
                selection-background-color: {selection};
                outline: none;
                padding: 4px;
                font-family: "Manrope";
                font-size: 13px;
            }}

            /* buttons */
            QPushButton#SaveBtn {{
                background: {btn_bg};
                border: 1px solid {border};
                border-radius: 10px;
                color: {primary};
                font-family: "Manrope";
                font-size: 12px;
                padding: 0px 18px;
            }}
            QPushButton#SaveBtn:hover {{ background: {btn_hover}; }}
            QPushButton#SaveBtn:pressed {{ background: {btn_press}; }}

            QPushButton#ResetBtn {{
                background: transparent;
                border: 1px solid {border};
                border-radius: 10px;
                color: {reset_color};
                font-family: "Manrope";
                font-size: 12px;
                padding: 0px 18px;
            }}
            QPushButton#ResetBtn:hover {{
                background: {btn_hover};
                color: {primary};
            }}
            QPushButton#ResetBtn:pressed {{ background: {btn_press}; }}

            /* Toggle Switch */
            QFrame#ModeContainer {{
                background: {toggle_track};
                border-radius: 12px;
                border: 1px solid {border};
            }}
            
            QPushButton#ModeBtn {{
                background: transparent;
                border: none;
                border-radius: 10px;
                color: {toggle_text_off};
                font-family: "Manrope";
                font-size: 13px;
                padding: 0px 16px;
                margin: 2px;
            }}
            QPushButton#ModeBtn:hover {{
                color: {toggle_text};
                background: rgba(255,255,255,0.03);
            }}
            QPushButton#ModeBtn:checked {{
                background: {toggle_bg};
                color: {toggle_text};
                font-weight: 600;
                border: 1px solid {border};
            }}
            
            /* Scrollbars */
            QScrollBar:vertical {{
                border: none; background: transparent; width: 5px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {scrollbar}; min-height: 32px; border-radius: 2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

            /* Account tab */
            QLabel#PlanBadgeFree {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 6px;
                color: {secondary};
                font-family: "Manrope";
                font-size: 10px;
                font-weight: bold;
            }}
            QLabel#PlanBadgePro {{
                background: rgba(99,102,241,0.25);
                border: 1px solid rgba(99,102,241,0.6);
                border-radius: 6px;
                color: #a5b4fc;
                font-family: "Manrope";
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton#RefreshBtn {{
                background: transparent;
                border: 1px solid {border};
                border-radius: 6px;
                color: {secondary};
                font-size: 14px;
            }}
            QPushButton#RefreshBtn:hover {{
                background: {btn_hover};
                color: {primary};
            }}
            QLabel#UsageLbl {{
                color: {secondary};
                font-family: "Manrope";
                font-size: 10px;
                margin-top: 4px;
            }}
            QFrame#UpgradeBox {{
                background: rgba(99,102,241,0.08);
                border: 1px solid rgba(99,102,241,0.32);
                border-radius: 14px;
            }}
            QFrame#PlanCard {{
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 10px;
            }}
            QFrame#PlanCardFeatured {{
                background: rgba(99,102,241,0.18);
                border: 1px solid rgba(99,102,241,0.55);
                border-radius: 10px;
            }}
            QLabel#PlanCardDesc {{
                color: {secondary};
                font-family: "Manrope";
                font-size: 10px;
            }}
            QLabel#BestValueBadge {{
                background: rgba(99,102,241,0.4);
                border: 1px solid rgba(99,102,241,0.7);
                border-radius: 4px;
                color: #c7d2fe;
                padding: 1px 6px;
                font-family: "Manrope";
                font-size: 9px;
            }}
            QPushButton#ProPlanBtn {{
                background: #6366f1;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-family: "Manrope";
                font-size: 12px;
                font-weight: 600;
                padding: 0px 14px;
            }}
            QPushButton#ProPlanBtn:hover {{ background: #818cf8; }}
            QPushButton#ProPlanBtn:pressed {{ background: #4f46e5; }}
            QPushButton#ProPlanBtn:disabled {{
                background: rgba(99,102,241,0.3);
                color: rgba(255,255,255,0.4);
            }}
            QPushButton#PrimaryBtn {{
                background: #6366f1;
                border: none;
                border-radius: 10px;
                color: #ffffff;
                font-family: "Manrope";
                font-size: 12px;
                font-weight: 600;
                padding: 0px 18px;
            }}
            QPushButton#PrimaryBtn:hover {{ background: #818cf8; }}
            QPushButton#PrimaryBtn:pressed {{ background: #4f46e5; }}
            QPushButton#PrimaryBtn:disabled {{
                background: rgba(99,102,241,0.3);
                color: rgba(255,255,255,0.4);
            }}
            QPushButton#OAuthBtn {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 10px;
                color: {primary};
                font-family: "Manrope";
                font-size: 12px;
                padding: 0px 18px;
            }}
            QPushButton#OAuthBtn:hover {{ background: {btn_hover}; }}
            QPushButton#OAuthBtn:pressed {{ background: {btn_press}; }}
            QPushButton#OAuthBtn:disabled {{
                color: {secondary};
                border: 1px solid {border};
            }}
            QFrame#SepLine {{
                border: none;
                border-top: 1px solid {border};
            }}
            QLabel#AccountStatusLbl {{
                color: {secondary};
                font-family: "Manrope";
                font-size: 10px;
            }}
            QLabel#AccountStatusLbl[error="true"] {{ color: #ff5f5f; }}

            /* Developer toggle */
            QPushButton#ToggleBtn {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 8px;
                color: {secondary};
                font-family: "Manrope";
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#ToggleBtn:checked {{
                background: rgba(99,102,241,0.35);
                border: 1px solid rgba(99,102,241,0.7);
                color: #a5b4fc;
            }}
            QPushButton#ToggleBtn:hover {{ background: {btn_hover}; }}
        """)
        # Update usage bar dark mode
        if hasattr(self, "usage_bar"):
            self.usage_bar.set_dark(dark)
