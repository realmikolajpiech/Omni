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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_theme = "dark"
        self._pages = {}  # name -> widget
        self._build_ui()

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
        """)
