"""
SettingsPanel — Redesigned settings UI with sidebar navigation.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QScrollArea, QFrame, QSizePolicy,
    QButtonGroup, QListWidget, QListWidgetItem, QStackedWidget, QTextEdit,
    QApplication, QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtProperty, QTimer, QSize, QRectF, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QIcon, QColor, QPainter, QPainterPath, QLinearGradient, QBrush, QPen, QFontMetrics

import sys
import os

from src.ui.styles import THEMES
import src.core.settings_store as settings_store
import src.core.subscription as subscription
import src.core.auth as auth
import src.core.billing as billing
from src.core.config import BACKEND_URL, DEVICE_ID, OMNI_SECRET, INDEX_DONE_MARKER, INDEX_PROGRESS_PATH, SUPABASE_URL, SUPABASE_ANON_KEY, APP_VERSION

# ── Website Supabase (waitlist / referrals) ───────────────────────────────────
_WEBSITE_SB_URL  = "https://rfirkagyggkumbeqzxgf.supabase.co"
_WEBSITE_SB_KEY  = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJmaXJrYWd5Z2drdW1iZXF6eGdmIiwic"
    "m9sZSI6ImFub24iLCJpYXQiOjE3NjcyOTk0NTUsImV4cCI6MjA4Mjg3NTQ1NX0"
    ".n_cf788aLOsS27S-7XJ7phRH2csrs6MnxgF8dSeAWSE"
)

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

        dark      = self._theme == "dark"
        primary   = "#FFFFFF" if dark else "#111111"
        secondary = "#999999" if dark else "#777777"
        accent    = "#C084FC" if dark else "#7C3AED"
        div_col   = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.08)"
        badge_bg  = "rgba(192,132,252,0.15)" if dark else "rgba(124,58,237,0.10)"
        badge_bd  = "rgba(192,132,252,0.38)" if dark else "rgba(124,58,237,0.32)"

        def _lbl(text, family, size, color, bold=False):
            l = QLabel(text)
            f = QFont(family, size)
            f.setBold(bold)
            l.setFont(f)
            l.setStyleSheet(f"background: transparent; color: {color};")
            return l

        def _divider():
            f = QFrame()
            f.setFixedHeight(1)
            f.setStyleSheet(f"background: {div_col};")
            return f

        def _cap_row(text):
            w = QWidget()
            w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 3, 0, 3)
            h.setSpacing(10)
            check = _lbl("✓", "Manrope", 10, accent, bold=True)
            check.setFixedWidth(12)
            cap = _lbl(text, "Manrope", 10, primary)
            cap.setWordWrap(True)
            h.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
            h.addWidget(cap, 1)
            return w

        # ── Header row: level name + badge ───────────────────────────
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(10)
        hdr.addWidget(_lbl(_TRUST_NAMES[self._level], "Manrope", 13, primary, bold=True))
        hdr.addStretch()
        badge = _lbl(f"Level {self._level}", "Manrope", 9, accent, bold=True)
        badge.setStyleSheet(
            f"background: {badge_bg}; border: 1px solid {badge_bd}; "
            f"border-radius: 5px; color: {accent}; padding: 2px 8px;"
        )
        hdr.addWidget(badge)
        self._inner.addLayout(hdr)
        self._inner.addSpacing(18)

        # ── Capabilities grouped by level ─────────────────────────────
        _GROUP_NAMES = {1: "Basic", 2: "Automation", 3: "Privileged"}
        for l in range(1, self._level + 1):
            if l > 1:
                self._inner.addSpacing(14)
                self._inner.addWidget(_divider())
                self._inner.addSpacing(12)

            grp_lbl = _lbl(
                f"Level {l}  ·  {_GROUP_NAMES.get(l, '')}",
                "Manrope", 8, secondary, bold=True
            )
            self._inner.addWidget(grp_lbl)
            self._inner.addSpacing(8)

            for cap in _ALL_CAPS[l]:
                self._inner.addWidget(_cap_row(cap))
                self._inner.addSpacing(4)

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


def _section_header(text: str) -> QLabel:
    """Small uppercase section label for grouping related settings."""
    lbl = QLabel(text.upper())
    lbl.setObjectName("SectionLabel")
    lbl.setFont(_font("Manrope", 9, bold=True))
    return lbl


def _make_google_icon(size: int = 18) -> QIcon:
    """Render the Google 'G' logo into a QIcon (uses QtSvg if available)."""
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QPixmap
    _SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">
      <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844
               c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908
               c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258
               c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711
               H.957v2.332C2.438 15.983 5.482 18 9 18z" fill="#34A853"/>
      <path d="M3.964 10.707c-.18-.54-.282-1.117-.282-1.707s.102-1.167
               .282-1.707V4.961H.957C.347 6.175 0 7.548 0 9s.348 2.825
               .957 4.039l3.007-2.332z" fill="#FBBC05"/>
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58
               C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017
               .957 4.961L3.964 7.293C4.672 5.166 6.656 3.58 9 3.58z"
            fill="#EA4335"/>
    </svg>"""
    try:
        from PyQt6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(QByteArray(_SVG))
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        renderer.render(p)
        p.end()
        return QIcon(px)
    except Exception:
        pass
    # Fallback: plain blue circle with white "G"
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#4285F4")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("white"))
    f = QFont("Arial", size * 6 // 10)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    from PyQt6.QtCore import QRect
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "G")
    p.end()
    return QIcon(px)


_GOOGLE_BTN_SS = (
    "QPushButton { background: #ffffff; border: 1px solid #dadce0; border-radius: 10px; "
    "color: #3c4043; font-family: 'Manrope'; font-size: 13px; font-weight: 500; "
    "padding: 0 16px; text-align: center; } "
    "QPushButton:hover { background: #f8f9fa; border-color: #c6c9cc; } "
    "QPushButton:pressed { background: #f1f3f4; } "
    "QPushButton:disabled { color: #9aa0a6; background: #f8f9fa; }"
)


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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(0)
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
        lay.setContentsMargins(20, 18, 20, 18)
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


class _FeedbackFormCard(QWidget):
    """Neutral card container for the feedback form — theme-aware painted background."""

    def __init__(self, dark: bool = True, parent=None):
        super().__init__(parent)
        self._dark = dark
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(0)
        self._inner = lay

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        if self._dark:
            bg     = QColor(255, 255, 255, 12)
            border = QColor(255, 255, 255, 30)
        else:
            bg     = QColor(0, 0, 0, 10)
            border = QColor(0, 0, 0, 40)
        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border, 1))
        p.drawPath(path)


class _FbTypeCard(QWidget):
    """Selectable type-option card for the feedback form (feature request / bug report)."""

    clicked_sig = pyqtSignal()

    _INDIGO_BORDER = QColor(99, 102, 241, 200)
    _INDIGO_BG     = QColor(99, 102, 241,  28)
    _ROSE_BORDER   = QColor(244,  63,  94, 200)
    _ROSE_BG       = QColor(244,  63,  94,  28)

    def __init__(self, icon: str, title: str, desc: str,
                 fb_type: str, dark: bool = True, parent=None):
        super().__init__(parent)
        self._dark = dark
        self._selected = False
        self._fb_type = fb_type
        if fb_type == "feature_request":
            self._sel_border, self._sel_bg = self._INDIGO_BORDER, self._INDIGO_BG
        else:
            self._sel_border, self._sel_bg = self._ROSE_BORDER, self._ROSE_BG

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(90)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(5)

        icon_lbl = QLabel(icon)
        icon_lbl.setFont(_font("Manrope", 20))
        icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        icon_lbl.setStyleSheet("background: transparent;")

        self._title_lbl = QLabel(title)
        self._title_lbl.setFont(_font("Manrope", 12, bold=True))
        self._title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._title_lbl.setStyleSheet("background: transparent;")

        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setFont(_font("Manrope", 10))
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._desc_lbl.setStyleSheet("background: transparent;")

        lay.addWidget(icon_lbl)
        lay.addWidget(self._title_lbl)
        lay.addWidget(self._desc_lbl)
        self._refresh_labels()

    def set_selected(self, sel: bool):
        self._selected = sel
        self._refresh_labels()
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self._refresh_labels()
        self.update()

    def _refresh_labels(self):
        if self._selected:
            tc = "#c7d2fe" if self._fb_type == "feature_request" else "#fda4af"
            dc = ("rgba(199,210,254,0.65)" if self._fb_type == "feature_request"
                  else "rgba(253,164,175,0.65)")
        else:
            tc = "rgba(255,255,255,0.85)" if self._dark else "rgba(17,17,17,0.80)"
            dc = "rgba(255,255,255,0.42)" if self._dark else "rgba(17,17,17,0.42)"
        self._title_lbl.setStyleSheet(f"color: {tc}; background: transparent;")
        self._desc_lbl.setStyleSheet(f"color: {dc}; background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked_sig.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        if self._selected:
            p.fillPath(path, QBrush(self._sel_bg))
            p.setPen(QPen(self._sel_border, 1.5))
        else:
            bg     = QColor(255, 255, 255,  9) if self._dark else QColor(0, 0, 0,  6)
            border = QColor(255, 255, 255, 24) if self._dark else QColor(0, 0, 0, 28)
            p.fillPath(path, QBrush(bg))
            p.setPen(QPen(border, 1))
        p.drawPath(path)


class _FbFormCard(QWidget):
    """Form card with a coloured top-accent strip that changes with feedback type."""

    _INDIGO = QColor(99, 102, 241)
    _ROSE   = QColor(244,  63,  94)

    def __init__(self, dark: bool = True, parent=None):
        super().__init__(parent)
        self._dark = dark
        self._accent = self._INDIGO
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._inner = lay

    def set_accent(self, fb_type: str):
        self._accent = self._INDIGO if fb_type == "feature_request" else self._ROSE
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        if self._dark:
            bg     = QColor(255, 255, 255, 13)
            border = QColor(255, 255, 255, 30)
        else:
            bg     = QColor(0, 0, 0,  8)
            border = QColor(0, 0, 0, 35)
        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        # coloured top accent strip
        accent = QColor(self._accent)
        accent.setAlpha(220)
        strip = QPainterPath()
        strip.addRoundedRect(QRectF(r.left() + 1, r.top() + 1, r.width() - 2, 4), 2, 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(strip, QBrush(accent))


class _ReferralCard(QWidget):
    """Warm amber-tinted card for the referral section — stands out from other cards."""
    _BG     = QColor(251, 191, 36, 18)   # amber tint
    _BORDER = QColor(251, 191, 36, 90)

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


class _MilestoneTrack(QWidget):
    """Horizontal milestone progress track: 0 → 1 → 3 → 5 confirmed referrals."""

    _MILESTONES = [1, 3, 5]
    _REWARDS    = ["1 month free", "2 months free", "3 months free"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._confirmed = 0
        self.setFixedHeight(56)

    def set_confirmed(self, n: int):
        self._confirmed = n
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        track_y   = 12
        track_h   = 3
        pad       = 24
        amber     = QColor(251, 191, 36)
        amber_dim = QColor(251, 191, 36, 50)
        white_dim = QColor(255, 255, 255, 28)
        dot_r     = 6

        # Milestone x-positions at 1/5, 3/5, 5/5 of track
        track_w = w - 2 * pad
        positions = [pad + (m / 5.0) * track_w for m in self._MILESTONES]

        # Background track
        p.setBrush(QBrush(white_dim))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(int(pad), track_y, int(track_w), track_h, 2, 2)

        # Amber fill up to current progress
        conf = self._confirmed
        if conf >= 5:
            fill_end = positions[2]
        elif conf >= 3:
            t = (conf - 3) / 2.0
            fill_end = positions[1] + (positions[2] - positions[1]) * min(t, 1.0)
        elif conf >= 1:
            t = (conf - 1) / 2.0
            fill_end = positions[0] + (positions[1] - positions[0]) * min(t, 1.0)
        else:
            fill_end = pad

        if fill_end > pad:
            p.setBrush(QBrush(amber))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(int(pad), track_y, int(fill_end - pad), track_h, 2, 2)

        # Dots and labels
        font = QFont("Manrope", 8)
        p.setFont(font)
        fm = QFontMetrics(font)
        center_y = track_y + track_h // 2

        for i, (x, reward) in enumerate(zip(positions, self._REWARDS)):
            milestone = self._MILESTONES[i]
            reached   = conf >= milestone

            if reached:
                p.setBrush(QBrush(amber))
                p.setPen(Qt.PenStyle.NoPen)
            else:
                p.setBrush(QBrush(QColor(30, 30, 40)))
                p.setPen(QPen(amber_dim, 1.2))
            p.drawEllipse(int(x - dot_r), center_y - dot_r, dot_r * 2, dot_r * 2)

            if reached:
                p.setBrush(QBrush(QColor(255, 255, 255, 210)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(int(x - 3), center_y - 3, 6, 6)

            label = f"{milestone} → {reward}"
            lw = fm.horizontalAdvance(label)
            text_x = int(x - lw / 2)
            text_y = center_y + dot_r + 13
            alpha  = 200 if reached else 90
            p.setPen(QPen(QColor(255, 255, 255, alpha)))
            p.drawText(text_x, text_y, label)

        p.end()


# Button stylesheet constants — applied inline so parent cascade can't override
_PLAN_BTN_SS = (
    "QPushButton { background: #6366f1; border: none; border-radius: 10px; "
    "color: #ffffff; font-family: 'Manrope'; font-size: 12px; font-weight: 700; padding: 0px 14px; } "
    "QPushButton:hover { background: #818cf8; } "
    "QPushButton:pressed { background: #4f46e5; } "
    "QPushButton:disabled { background: rgba(99,102,241,0.3); color: rgba(255,255,255,0.4); }"
)
_GHOST_PLAN_BTN_SS = (
    "QPushButton { background: transparent; border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; "
    "color: rgba(255,255,255,0.70); font-family: 'Manrope'; font-size: 12px; font-weight: 700; padding: 0px 14px; } "
    "QPushButton:hover { background: rgba(255,255,255,0.07); border-color: rgba(255,255,255,0.32); color: rgba(255,255,255,0.95); } "
    "QPushButton:pressed { background: rgba(255,255,255,0.04); }"
)
_PRIMARY_BTN_SS = (
    "QPushButton { background: #6366f1; border: none; border-radius: 10px; "
    "color: #ffffff; font-family: 'Manrope'; font-size: 12px; font-weight: 600; padding: 0px 18px; } "
    "QPushButton:hover { background: #818cf8; } "
    "QPushButton:pressed { background: #4f46e5; } "
    "QPushButton:disabled { background: rgba(99,102,241,0.3); color: rgba(255,255,255,0.4); }"
)


# ---------------------------------------------------------------------------
# Radio dot — painted selection indicator
# ---------------------------------------------------------------------------

class _RadioDot(QWidget):
    """Painted radio button dot: gradient-filled when selected, ring when not."""

    def __init__(self, dark: bool = True, parent=None):
        super().__init__(parent)
        self._selected = False
        self._dark = dark
        self.setFixedSize(20, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_selected(self, selected: bool, dark: bool = None):
        self._selected = selected
        if dark is not None:
            self._dark = dark
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r = 10.0, 10.0, 8.0
        if self._selected:
            grad = QLinearGradient(0, 0, 20, 20)
            grad.setColorAt(0, QColor("#6366f1"))
            grad.setColorAt(1, QColor("#8b5cf6"))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
        else:
            col = QColor(255, 255, 255, 55) if self._dark else QColor(0, 0, 0, 40)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 1.5))
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))


# ---------------------------------------------------------------------------
# Mode option row — selectable personality option
# ---------------------------------------------------------------------------

class _ModeOptionRow(QWidget):
    """Full-width selectable mode row: radio dot + name + description."""
    selected = pyqtSignal(str)

    def __init__(self, mode: str, name: str, desc: str, dark: bool = True, parent=None):
        super().__init__(parent)
        self._mode = mode
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 16, 2, 16)
        lay.setSpacing(14)

        self._radio = _RadioDot(dark=dark)
        lay.addWidget(self._radio, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.setContentsMargins(0, 0, 0, 0)

        self._name_lbl = QLabel(name)
        self._name_lbl.setFont(_font("Manrope", 12, bold=True))
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setObjectName("DescLbl")
        self._desc_lbl.setFont(_font("Manrope", 10))
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        text_col.addWidget(self._name_lbl)
        text_col.addWidget(self._desc_lbl)
        lay.addLayout(text_col, 1)

    def set_active(self, active: bool):
        self._radio.set_selected(active)

    def set_dark(self, dark: bool):
        self._radio.set_selected(self._radio._selected, dark)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._mode)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Page header separator — gradient rule drawn in paintEvent
# ---------------------------------------------------------------------------

class _PageSep(QWidget):
    """Thin gradient horizontal rule for the page header: indigo → purple → transparent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0,  QColor(99,  102, 241, 210))
        grad.setColorAt(0.40, QColor(139,  92, 246, 110))
        grad.setColorAt(1.0,  QColor(139,  92, 246,   0))
        p.fillRect(self.rect(), QBrush(grad))


# ---------------------------------------------------------------------------
# Personality Card — painted selectable card
# ---------------------------------------------------------------------------

class _PersonalityCard(QWidget):
    """Large painted card with icon, name, description. Selectable with indigo highlight."""
    clicked = pyqtSignal()

    def __init__(self, mode: str, icon: str, name: str, desc: str, dark: bool = True, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._icon = icon
        self._name = name
        self._desc = desc
        self._dark = dark
        self._selected = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(160)

    def set_selected(self, selected: bool):
        self._selected = selected
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self._dark
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)

        if self._selected:
            bg = QColor(99, 102, 241, 38)
            border_col = QColor(99, 102, 241, 178)
            border_w = 1.5
        else:
            bg = QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 13)
            border_col = QColor(255, 255, 255, 31) if dark else QColor(0, 0, 0, 31)
            border_w = 1.0

        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border_col, border_w))
        p.drawPath(path)

        # Check dot at top-right when selected
        if self._selected:
            dot_x = self.width() - 24
            dot_y = 20
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(99, 102, 241))
            p.drawEllipse(QRectF(dot_x - 8, dot_y - 8, 16, 16))
            # White checkmark
            pen = QPen(QColor(255, 255, 255), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(int(dot_x - 4), int(dot_y), int(dot_x - 1), int(dot_y + 3))
            p.drawLine(int(dot_x - 1), int(dot_y + 3), int(dot_x + 4), int(dot_y - 3))

        pad = 24
        # Icon
        icon_font = QFont("Manrope", 28)
        p.setFont(icon_font)
        p.setPen(QColor(255, 255, 255) if dark else QColor(40, 40, 40))
        fm = p.fontMetrics()
        p.drawText(pad, pad + fm.ascent(), self._icon)
        icon_bottom = pad + fm.height()

        # Name
        name_font = QFont("Manrope", 13)
        name_font.setWeight(QFont.Weight.Bold)
        p.setFont(name_font)
        name_color = QColor(255, 255, 255) if dark else QColor(17, 17, 17)
        p.setPen(name_color)
        fm2 = p.fontMetrics()
        name_y = icon_bottom + 10 + fm2.ascent()
        p.drawText(pad, name_y, self._name)

        # Description — word-wrapped
        desc_font = QFont("Manrope", 10)
        p.setFont(desc_font)
        desc_color = QColor(153, 153, 153) if dark else QColor(119, 119, 119)
        p.setPen(desc_color)
        desc_rect = QRectF(pad, name_y + 6, self.width() - pad * 2, self.height() - name_y - 12)
        p.drawText(desc_rect, Qt.TextFlag.TextWordWrap, self._desc)


# ---------------------------------------------------------------------------
# Trust Checkpoint Track — horizontal 3-stop selector with drag
# ---------------------------------------------------------------------------

_TRUST_DESCS = {
    1: "Chat, search & read-only access",
    2: "Files, automation & computer control",
    3: "Install apps & privileged commands",
}

class _TrustCheckpointWidget(QWidget):
    """
    Horizontal track with 3 checkpoint nodes. Click or drag to select a level.
    Single unified animation drives fill + node scale + glow simultaneously.
    """
    level_changed = pyqtSignal(int)

    _TRACK_H = 6
    _NODE_R  = 14
    _PAD_X   = 52
    _TRACK_Y = 36

    def __init__(self, level: int = 1, dark: bool = True, parent=None):
        super().__init__(parent)
        self._level    = level
        self._dark     = dark
        self._fill     = float(level - 1) / 2   # 0.0 … 1.0 animated fill
        self._hover    = 0
        self._dragging = False
        # Per-node animated state (driven by _tween helper)
        self._node_scale = {1: 1.0, 2: 1.0, 3: 1.0}
        self._node_glow  = {1: 0.0, 2: 0.0, 3: 0.0}
        self._hover_glow = {1: 0.0, 2: 0.0, 3: 0.0}
        self._node_glow[level] = 1.0
        self._tweens = {}  # anim_key → QVariantAnimation
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFixedHeight(110)

    # ── Tween engine (one system for everything) ──────────────────────

    def _tween(self, key: str, prop_dict: dict, prop_key,
               target: float, duration: int = 300,
               curve: QEasingCurve.Type = QEasingCurve.Type.OutCubic):
        """Smoothly animate prop_dict[prop_key] → target. Cancels any running
        tween on the same key. All tweens share one repaint path."""
        if key in self._tweens:
            old = self._tweens.pop(key)
            old.stop()
            old.deleteLater()

        start = prop_dict[prop_key]
        if abs(start - target) < 0.003:
            prop_dict[prop_key] = target
            self.update()
            return

        from PyQt6.QtCore import QVariantAnimation
        a = QVariantAnimation(self)
        a.setStartValue(float(start))
        a.setEndValue(float(target))
        a.setDuration(duration)
        a.setEasingCurve(QEasingCurve(curve))

        def on_val(v):
            prop_dict[prop_key] = v
            self.update()

        def on_done():
            prop_dict[prop_key] = target
            self._tweens.pop(key, None)
            self.update()

        a.valueChanged.connect(on_val)
        a.finished.connect(on_done)
        self._tweens[key] = a
        a.start()

    def _tween_fill(self, target: float, duration: int = 320):
        self._tween("fill", self.__dict__, "_fill", target, duration,
                    QEasingCurve.Type.InOutCubic)

    # ── Animate selection (all at once) ───────────────────────────────

    def _animate_to(self, new_level: int, old_level: int):
        """Single call drives fill + old-node fadeout + new-node pop. All start
        together so the motion reads as one gesture."""
        # ── Fill bar ──
        self._tween_fill(float(new_level - 1) / 2, 320)

        # ── Old node: glow out + scale settle ──
        if old_level and old_level != new_level:
            self._tween(f"glow_{old_level}", self._node_glow, old_level,
                        0.0, 250, QEasingCurve.Type.InOutCubic)
            self._tween(f"scale_{old_level}", self._node_scale, old_level,
                        1.0, 250, QEasingCurve.Type.OutCubic)

        # ── New node: elastic pop (1.0 → 1.18 → 1.0) + glow in ──
        self._node_scale[new_level] = 1.0          # start from current size
        self._tween(f"scale_{new_level}", self._node_scale, new_level,
                    1.18, 160, QEasingCurve.Type.OutCubic)
        # After the pop-up, settle back
        QTimer.singleShot(160, lambda nl=new_level: self._tween(
            f"scale_{nl}", self._node_scale, nl,
            1.0, 280, QEasingCurve.Type.OutBack))
        self._tween(f"glow_{new_level}", self._node_glow, new_level,
                    1.0, 260, QEasingCurve.Type.OutCubic)

    # ── Public API ────────────────────────────────────────────────────

    def set_level(self, level: int, animate: bool = True):
        if level == self._level and abs(self._fill - float(level - 1) / 2) < 0.01:
            return
        old = self._level
        self._level = level
        if animate and not self._dragging:
            self._animate_to(level, old)
        else:
            self._fill = float(level - 1) / 2
            self._node_glow = {l: (1.0 if l == level else 0.0) for l in [1, 2, 3]}
            self._node_scale = {l: 1.0 for l in [1, 2, 3]}
            self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    # ── Geometry ──────────────────────────────────────────────────────

    def _node_x(self, level: int) -> float:
        x0 = float(self._PAD_X)
        x1 = float(self.width() - self._PAD_X)
        return x0 + (level - 1) * (x1 - x0) / 2

    def _nearest(self, x: float) -> int:
        return min((abs(x - self._node_x(l)), l) for l in [1, 2, 3])[1]

    # ── Mouse events ─────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            lvl = self._nearest(event.position().x())
            if lvl != self._level:
                old = self._level
                self._level = lvl
                self._animate_to(lvl, old)
                self.level_changed.emit(lvl)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        hov = self._nearest(event.position().x())
        if hov != self._hover:
            old_hov = self._hover
            self._hover = hov
            if old_hov and old_hov != self._level:
                self._tween(f"hov_{old_hov}", self._hover_glow, old_hov,
                            0.0, 160, QEasingCurve.Type.OutCubic)
            if hov and hov != self._level:
                self._tween(f"hov_{hov}", self._hover_glow, hov,
                            1.0, 160, QEasingCurve.Type.OutCubic)
        if self._dragging:
            lvl = self._nearest(event.position().x())
            if lvl != self._level:
                old = self._level
                self._level = lvl
                self._animate_to(lvl, old)
                self.level_changed.emit(lvl)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        old_hov = self._hover
        self._hover = 0
        if old_hov:
            self._tween(f"hov_{old_hov}", self._hover_glow, old_hov,
                        0.0, 180, QEasingCurve.Type.OutCubic)
        super().leaveEvent(event)

    # ── Paint ─────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self._dark
        ty   = float(self._TRACK_Y)
        th   = float(self._TRACK_H)
        nr   = float(self._NODE_R)
        x0   = self._node_x(1)
        x3   = self._node_x(3)
        span = x3 - x0

        # ── Track background ───────────────────────────────────────────
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(x0, ty - th / 2, span, th), th / 2, th / 2)
        p.fillPath(bg_path, QColor(255, 255, 255, 28 if dark else 50))

        # ── Filled portion ─────────────────────────────────────────────
        fill_w = self._fill * span
        if fill_w > 1:
            fill_path = QPainterPath()
            fill_path.addRoundedRect(QRectF(x0, ty - th / 2, fill_w, th), th / 2, th / 2)
            grad = QLinearGradient(x0, 0, x3, 0)
            grad.setColorAt(0.0, QColor("#818cf8"))
            grad.setColorAt(1.0, QColor("#6366f1"))
            p.fillPath(fill_path, QBrush(grad))

        # ── Nodes ──────────────────────────────────────────────────────
        for l in [1, 2, 3]:
            nx     = self._node_x(l)
            active = l <= self._level
            sel    = l == self._level
            scale  = self._node_scale.get(l, 1.0)
            glow_a = self._node_glow.get(l, 1.0 if sel else 0.0)
            hov_a  = self._hover_glow.get(l, 0.0)
            r      = nr * scale

            # Glow ring (selection)
            if glow_a > 0.01:
                gr = r + 7
                gp = QPainterPath()
                gp.addEllipse(QRectF(nx - gr, ty - gr, gr * 2, gr * 2))
                p.fillPath(gp, QColor(99, 102, 241, int(35 * glow_a)))

            # Glow ring (hover)
            if hov_a > 0.01 and not sel:
                hr = r + 4
                hp = QPainterPath()
                hp.addEllipse(QRectF(nx - hr, ty - hr, hr * 2, hr * 2))
                p.fillPath(hp, QColor(99, 102, 241, int(20 * hov_a)))

            # Node body
            np_ = QPainterPath()
            np_.addEllipse(QRectF(nx - r, ty - r, r * 2, r * 2))
            if active:
                blend = hov_a if not sel else 0.0
                c = QColor(
                    int(99 + 30 * blend),
                    int(102 + 38 * blend),
                    int(241 + 7 * blend),
                )
                p.fillPath(np_, c)
            else:
                p.fillPath(np_, QColor(55, 55, 72) if dark else QColor(210, 210, 220))
                p.setPen(QPen(QColor(255, 255, 255, 55 if dark else 110), 1.5))
                p.drawEllipse(QRectF(nx - r + 0.75, ty - r + 0.75,
                                     (r - 0.75) * 2, (r - 0.75) * 2))
                p.setPen(Qt.PenStyle.NoPen)

            # White inner dot
            if sel and glow_a > 0.2:
                ir = r * 0.36
                ip = QPainterPath()
                ip.addEllipse(QRectF(nx - ir, ty - ir, ir * 2, ir * 2))
                p.fillPath(ip, QColor(255, 255, 255, int(255 * min(1.0, glow_a))))

        # ── Labels ─────────────────────────────────────────────────────
        label_top = ty + nr + 12
        max_w     = 148.0
        pad       = 4.0

        for l in [1, 2, 3]:
            nx     = self._node_x(l)
            active = l <= self._level
            sel    = l == self._level

            nf = QFont("Manrope", 12)
            nf.setWeight(QFont.Weight.Bold if sel else QFont.Weight.DemiBold)
            p.setFont(nf)
            p.setPen(
                (QColor(255, 255, 255) if active else QColor(100, 100, 118)) if dark
                else (QColor(20, 20, 30) if active else QColor(150, 150, 162))
            )
            fm = p.fontMetrics()
            name = _TRUST_NAMES[l]
            fw   = fm.horizontalAdvance(name)
            name_x = max(pad, min(nx - fw / 2, self.width() - fw - pad))
            p.drawText(int(name_x), int(label_top + fm.ascent()), name)

            df = QFont("Manrope", 10)
            p.setFont(df)
            p.setPen(
                QColor(129, 140, 248, 230) if sel
                else (QColor(115, 115, 132) if dark else QColor(135, 135, 150))
            )
            fm2  = p.fontMetrics()
            dx   = max(pad, min(nx - max_w / 2, self.width() - max_w - pad))
            desc_rect = QRectF(dx, label_top + fm.height() + 4, max_w, fm2.height() * 3)
            align = Qt.AlignmentFlag.AlignLeft if l == 1 else \
                    Qt.AlignmentFlag.AlignRight if l == 3 else \
                    Qt.AlignmentFlag.AlignHCenter
            p.drawText(desc_rect, align | Qt.TextFlag.TextWordWrap, _TRUST_DESCS[l])


# ---------------------------------------------------------------------------
# Trust Level Card — painted selectable card for trust levels
# ---------------------------------------------------------------------------

class _TrustLevelCard(QWidget):
    """Painted card for trust level selection with level indicator and capability bullets."""
    clicked = pyqtSignal()

    _BULLETS = {
        1: ["Chat & web search", "File & semantic search", "Open files & apps"],
        2: ["Create & move files", "Computer control", "System modifications"],
        3: ["Install/uninstall apps", "Privileged commands", "Full sudo access"],
    }

    def __init__(self, level: int, name: str, dark: bool = True, parent=None):
        super().__init__(parent)
        self._level = level
        self._name = name
        self._dark = dark
        self._selected = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(150)

    def set_selected(self, selected: bool):
        self._selected = selected
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self._dark
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)

        if self._selected:
            bg = QColor(99, 102, 241, 38)
            border_col = QColor(99, 102, 241, 178)
            border_w = 1.5
        else:
            bg = QColor(255, 255, 255, 18) if dark else QColor(0, 0, 0, 13)
            border_col = QColor(255, 255, 255, 31) if dark else QColor(0, 0, 0, 31)
            border_w = 1.0

        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border_col, border_w))
        p.drawPath(path)

        # Small indigo dot at top-right when selected
        if self._selected:
            dot_x = self.width() - 20
            dot_y = 18
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(99, 102, 241))
            p.drawEllipse(QRectF(dot_x - 5, dot_y - 5, 10, 10))

        pad = 20
        y = pad

        # "Level N" small label at top
        level_font = QFont("Manrope", 8)
        level_font.setWeight(QFont.Weight.DemiBold)
        p.setFont(level_font)
        muted = QColor(120, 120, 120) if dark else QColor(130, 130, 130)
        p.setPen(muted)
        fm = p.fontMetrics()
        p.drawText(pad, y + fm.ascent(), f"Level {self._level}")
        y += fm.height() + 6

        # Name large
        name_font = QFont("Manrope", 14)
        name_font.setWeight(QFont.Weight.Bold)
        p.setFont(name_font)
        name_color = QColor(255, 255, 255) if dark else QColor(17, 17, 17)
        p.setPen(name_color)
        fm2 = p.fontMetrics()
        p.drawText(pad, y + fm2.ascent(), self._name)
        y += fm2.height() + 12

        # Bullets
        blt_font = QFont("Manrope", 9)
        p.setFont(blt_font)
        p.setPen(muted)
        fm3 = p.fontMetrics()
        bullets = self._BULLETS.get(self._level, [])
        for bullet in bullets:
            p.drawText(pad, y + fm3.ascent(), f"·  {bullet}")
            y += fm3.height() + 3


# ---------------------------------------------------------------------------
# Settings Row — label left, control(s) right, subtle bottom border
# ---------------------------------------------------------------------------

class _SettingsRow(QWidget):
    """A settings row with a label column on the left and a right control area."""

    def __init__(self, label: str, sublabel: str = None, dark: bool = True, parent=None):
        super().__init__(parent)
        self._dark = dark
        self.setObjectName("SettingsRow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 10, 0, 10)
        outer.setSpacing(12)

        label_col = QVBoxLayout()
        label_col.setSpacing(2)
        label_col.setContentsMargins(0, 0, 0, 0)

        self._label_w = QLabel(label)
        self._label_w.setFont(_font("Manrope", 12, bold=True))
        self._label_w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        label_col.addWidget(self._label_w)

        if sublabel:
            self._sublabel_w = QLabel(sublabel)
            self._sublabel_w.setFont(_font("Manrope", 10))
            self._sublabel_w.setObjectName("DescLbl")
            self._sublabel_w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            label_col.addWidget(self._sublabel_w)
        else:
            self._sublabel_w = None

        outer.addLayout(label_col, 1)

        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        self.right.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.right)

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        dark = self._dark
        div_col = QColor(255, 255, 255, 15) if dark else QColor(0, 0, 0, 13)
        p.setPen(QPen(div_col, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


# ---------------------------------------------------------------------------
# Avatar Widget — painted indigo circle with initial letter
# ---------------------------------------------------------------------------

class _AvatarWidget(QWidget):
    """48px diameter indigo circle with white initial letter."""

    def __init__(self, initial: str = "?", parent=None):
        super().__init__(parent)
        self._initial = initial
        self.setObjectName("AvatarWidget")
        self.setFixedSize(48, 48)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_initial(self, initial: str):
        self._initial = initial[:1].upper() if initial else "?"
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, 48, 48)
        path = QPainterPath()
        path.addEllipse(r)
        p.fillPath(path, QBrush(QColor(99, 102, 241, 50)))
        p.setPen(QPen(QColor(99, 102, 241, 100), 1))
        p.drawPath(path)
        font = QFont("Manrope", 18)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        p.setPen(QColor(160, 170, 255))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._initial)


# ---------------------------------------------------------------------------
# Settings Page Base
# ---------------------------------------------------------------------------

class SettingsPage(QWidget):
    """
    Base class for a settings page: fixed title header + scrollable content area.
    """
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Scrollable content area (title scrolls with content) ──────
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.content_widget.setObjectName("SettingsPageContent")

        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(32, 28, 32, 32)
        self.content_layout.setSpacing(0)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Page header (scrolls with content) ───────────────────────
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("PageTitle")
        self.title_lbl.setFont(_font("Instrument Serif", 26))
        self.content_layout.addWidget(self.title_lbl)

        if subtitle:
            self.content_layout.addSpacing(5)
            self.subtitle_lbl = QLabel(subtitle)
            self.subtitle_lbl.setObjectName("PageSubtitle")
            self.subtitle_lbl.setFont(_font("Manrope", 11))
            self.subtitle_lbl.setWordWrap(True)
            self.content_layout.addWidget(self.subtitle_lbl)
            self.content_layout.addSpacing(18)
        else:
            self.content_layout.addSpacing(15)

        self.content_layout.addWidget(_PageSep())
        self.content_layout.addSpacing(20)

        # Switch to per-item spacing from here on
        self.content_layout.setSpacing(16)

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
        # Keep account UI in sync with background subscription refreshes
        # (e.g. the startup fetch that runs after load_saved_session).
        subscription.add_listener(
            lambda s: self._dispatch.emit(lambda: self._update_account_ui(s))
        )
        # Kill indexer subprocess on app quit so it doesn't orphan and hammer the
        # next brain instance with embed requests, making the re-launched app unresponsive.
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(self._kill_indexer)

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
        self._add_page("AI Model", self._build_model())
        self._add_page("Trust", self._build_trust())
        self._add_page("Files", self._build_files())
        self._add_page("Account", self._build_account())
        self._referral_page = self._build_referral()  # hidden — referral system WIP
        self._add_page("Feedback", self._build_feedback())

        sidebar_col = QVBoxLayout()
        sidebar_col.setContentsMargins(0, 0, 0, 0)
        sidebar_col.setSpacing(0)
        sidebar_col.addWidget(self.sidebar)

        ver_widget = QWidget()
        ver_widget.setFixedWidth(200)
        ver_widget.setObjectName("SidebarFooter")
        ver_col = QVBoxLayout(ver_widget)
        ver_col.setContentsMargins(0, 0, 0, 0)
        ver_col.setSpacing(0)

        footer_sep = QFrame()
        footer_sep.setFrameShape(QFrame.Shape.HLine)
        footer_sep.setObjectName("SidebarFooterSep")
        ver_col.addWidget(footer_sep)

        ver_inner = QVBoxLayout()
        ver_inner.setContentsMargins(16, 8, 16, 12)
        ver_inner.setSpacing(3)

        self._version_label = QLabel(f"v{APP_VERSION}")
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._version_label.setFont(_font("Manrope", 10))
        ver_inner.addWidget(self._version_label)

        self._check_update_btn = QPushButton("Check for updates")
        self._check_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_update_btn.setFlat(True)
        self._check_update_btn.setFont(_font("Manrope", 10))
        self._check_update_btn.clicked.connect(self._on_check_update)
        ver_inner.addWidget(self._check_update_btn)

        self._restart_btn = QPushButton("Restart Omni")
        self._restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restart_btn.setFlat(True)
        self._restart_btn.setFont(_font("Manrope", 10))
        self._restart_btn.clicked.connect(self._on_restart)
        ver_inner.addWidget(self._restart_btn)

        ver_col.addLayout(ver_inner)
        sidebar_col.addWidget(ver_widget)

        root.addLayout(sidebar_col)
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

    def _build_model(self) -> QWidget:
        page = SettingsPage("AI Model", "Choose how Omni communicates with you.")

        page.add_spacing(16)
        page.add_widget(_section_header("Personality Mode"))
        page.add_spacing(12)

        dark = self.current_theme == "dark"
        saved_mode = settings_store.get("personality_mode", "professional")

        _MODES = [
            ("professional", "✦", "Professional",
             "Polished, focused, and precise. Best for work and productivity."),
            ("unfiltered",   "⚡", "Unfiltered",
             "Direct, unfiltered, and uncensored. No guardrails, no sugarcoating."),
        ]

        cards_row = QHBoxLayout()
        cards_row.setContentsMargins(0, 0, 0, 0)
        cards_row.setSpacing(12)

        self._mode_options: dict[str, _PersonalityCard] = {}
        for mode, icon, name, desc in _MODES:
            card = _PersonalityCard(mode=mode, icon=icon, name=name, desc=desc, dark=dark)
            card.set_selected(saved_mode == mode)
            card.clicked.connect(lambda m=mode: self._on_personality_mode_selected(m))
            cards_row.addWidget(card)
            self._mode_options[mode] = card

        page.add_layout(cards_row)
        page.add_stretch()
        return page

    def _build_trust(self) -> QWidget:
        page = SettingsPage("Trust",
            "Control what Omni is allowed to do on your behalf.")

        saved_level = settings_store.get("trust_level", 1)
        dark = self.current_theme == "dark"

        # ── Checkpoint track ──────────────────────────────────────────
        track_card = _FeedbackFormCard(dark=dark)
        tc = track_card._inner
        tc.setContentsMargins(16, 20, 16, 20)
        tc.setSpacing(0)

        self._trust_track = _TrustCheckpointWidget(level=saved_level, dark=dark)
        self._trust_track.level_changed.connect(self._on_trust_changed)
        tc.addWidget(self._trust_track)
        page.add_widget(track_card)

        # ── Capabilities card ─────────────────────────────────────────
        self._trust_cap_card = _FeedbackFormCard(dark=dark)
        cap_inner = self._trust_cap_card._inner
        cap_inner.setContentsMargins(20, 14, 20, 8)
        cap_inner.setSpacing(0)

        cap_hdr = _section_header("UNLOCKED CAPABILITIES")
        cap_inner.addWidget(cap_hdr)
        cap_inner.addSpacing(10)

        self._cap_rows_layout = QVBoxLayout()
        self._cap_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._cap_rows_layout.setSpacing(0)
        cap_inner.addLayout(self._cap_rows_layout)

        page.add_widget(self._trust_cap_card)
        self._rebuild_cap_rows(saved_level)

        # Compat stubs
        self._trust_slider     = None
        self._trust_cap_panel  = None
        self._trust_level_cards = {}

        page.add_stretch()
        return page

    def _rebuild_cap_rows(self, level: int):
        """Clear and rebuild capability rows for the given trust level."""
        if not hasattr(self, "_cap_rows_layout"):
            return
        lay = self._cap_rows_layout
        dark = self.current_theme == "dark"
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        primary = "#FFFFFF" if dark else "#111111"
        accent = "#6366f1"

        for lvl in range(1, level + 1):
            for cap in _ALL_CAPS[lvl]:
                row_w = QWidget()
                row_w.setObjectName("CapRow")
                row_w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                row_h = QHBoxLayout(row_w)
                row_h.setContentsMargins(0, 8, 0, 8)
                row_h.setSpacing(8)

                check_lbl = QLabel("✓")
                check_lbl.setFont(_font("Manrope", 10, bold=True))
                check_lbl.setStyleSheet(f"color: {accent}; background: transparent;")
                check_lbl.setFixedWidth(14)
                row_h.addWidget(check_lbl, 0, Qt.AlignmentFlag.AlignTop)

                cap_lbl = QLabel(cap)
                cap_lbl.setFont(_font("Manrope", 10))
                cap_lbl.setStyleSheet(f"color: {primary}; background: transparent;")
                cap_lbl.setWordWrap(True)
                row_h.addWidget(cap_lbl, 1)

                # Subtle bottom border via a thin frame
                col_wrap = QWidget()
                col_wrap.setObjectName("CapRow")
                col_wrap.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                col_vlay = QVBoxLayout(col_wrap)
                col_vlay.setContentsMargins(0, 0, 0, 0)
                col_vlay.setSpacing(0)
                col_vlay.addWidget(row_w)

                div_col = "rgba(255,255,255,0.06)" if dark else "rgba(0,0,0,0.05)"
                divider = QFrame()
                divider.setObjectName("CapRowDivider")
                divider.setFixedHeight(1)
                divider.setStyleSheet(f"background: {div_col}; border: none;")
                col_vlay.addWidget(divider)

                lay.addWidget(col_wrap)

    def _rebuild_cap_rows_animated(self, level: int):
        """Rebuild capabilities with a smooth height crossfade on the whole card."""
        if not hasattr(self, "_cap_rows_layout") or not hasattr(self, "_trust_cap_card"):
            return
        current = settings_store.get("trust_level", 1)
        if current != level:
            return
        card = self._trust_cap_card
        old_h = card.height()
        self._rebuild_cap_rows(level)
        # Force layout recalc to get the new target height
        card.adjustSize()
        new_h = card.sizeHint().height()
        if old_h == new_h or old_h <= 0:
            return
        # Animate the card height from old → new so no jump
        from PyQt6.QtCore import QVariantAnimation
        card.setFixedHeight(old_h)
        anim = QVariantAnimation(card)
        anim.setStartValue(old_h)
        anim.setEndValue(new_h)
        anim.setDuration(280)
        anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        anim.valueChanged.connect(lambda v: card.setFixedHeight(int(v)))
        anim.finished.connect(lambda: card.setMinimumHeight(0) or card.setMaximumHeight(16777215))
        anim.start()
        # prevent gc
        card._h_anim = anim

    def _on_trust_level_label_update(self, level: int):
        """Update the trust level name/desc labels in the slider card header."""
        _level_descs = {
            1: "Read-only · Chat, search, open files and apps.",
            2: "Automation · Create files, control the computer, modify system settings.",
            3: "Full Control · Install apps, run privileged commands.",
        }
        if hasattr(self, "_trust_level_name_lbl"):
            self._trust_level_name_lbl.setText(_TRUST_NAMES[level])
        if hasattr(self, "_trust_level_desc_lbl"):
            self._trust_level_desc_lbl.setText(_level_descs[level])
        if hasattr(self, "_trust_level_badge"):
            self._trust_level_badge.setText(f"Level {level}")

    def _build_files(self) -> QWidget:
        import json, subprocess, sys, os
        page = SettingsPage("Files",
            "Omni indexes your files to enable semantic search. "
            "Indexing runs once in the background and resumes automatically if interrupted.")

        page.add_spacing(16)
        page.add_widget(_section_header("Index Status"))
        page.add_spacing(16)

        # ── Big status row: dot + label ───────────────────────────────
        status_top_row = QHBoxLayout()
        status_top_row.setContentsMargins(0, 0, 0, 0)
        status_top_row.setSpacing(12)

        self._files_dot = QLabel("●")
        self._files_dot.setFont(_font("Manrope", 24))
        self._files_dot.setFixedWidth(30)
        self._files_dot.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._files_status_lbl = QLabel()
        self._files_status_lbl.setFont(_font("Manrope", 20, bold=True))

        status_top_row.addWidget(self._files_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        status_top_row.addWidget(self._files_status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        status_top_row.addStretch()
        page.add_layout(status_top_row)
        page.add_spacing(8)

        # ── Detail label ──────────────────────────────────────────────
        self._files_detail_lbl = QLabel()
        self._files_detail_lbl.setObjectName("DescLbl")
        self._files_detail_lbl.setFont(_font("Manrope", 10))
        self._files_detail_lbl.setWordWrap(True)
        page.add_widget(self._files_detail_lbl)
        page.add_spacing(16)

        # ── Progress bar row ──────────────────────────────────────────
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(10)

        self._files_progress_bar = QProgressBar()
        self._files_progress_bar.setRange(0, 100)
        self._files_progress_bar.setValue(0)
        self._files_progress_bar.setFixedHeight(6)
        self._files_progress_bar.setTextVisible(False)
        self._files_progress_bar.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,0.08);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:0.5 #8b5cf6, stop:1 #3b82f6);
                border-radius: 3px;
            }
        """)
        self._files_progress_bar.hide()

        self._files_pct_lbl = QLabel("")
        self._files_pct_lbl.setFont(_font("Manrope", 10))
        self._files_pct_lbl.setObjectName("DescLbl")
        self._files_pct_lbl.setFixedWidth(36)
        self._files_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        progress_row.addWidget(self._files_progress_bar, 1)
        progress_row.addWidget(self._files_pct_lbl)
        page.add_layout(progress_row)
        page.add_spacing(20)

        # ── Gradient divider ──────────────────────────────────────────
        page.add_widget(_PageSep())
        page.add_spacing(20)

        # ── Button row ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)

        self._files_btn = QPushButton()
        self._files_btn.setFixedHeight(42)
        self._files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._files_btn.setFont(_font("Manrope", 11, bold=True))
        self._files_btn.setObjectName("FilesActionBtn")
        self._files_btn.setStyleSheet(_PRIMARY_BTN_SS)
        self._files_btn.clicked.connect(self._on_files_btn_clicked)

        self._files_pause_btn = QPushButton("Pause")
        self._files_pause_btn.setFixedHeight(42)
        self._files_pause_btn.setFixedWidth(96)
        self._files_pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._files_pause_btn.setFont(_font("Manrope", 11, bold=True))
        self._files_pause_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); "
            "border-radius: 10px; color: rgba(255,255,255,0.75); padding: 0px 18px; } "
            "QPushButton:hover { background: rgba(255,255,255,0.14); color: #ffffff; } "
            "QPushButton:pressed { background: rgba(255,255,255,0.06); }"
        )
        self._files_pause_btn.clicked.connect(self._on_files_pause_clicked)
        self._files_pause_btn.hide()

        btn_row.addWidget(self._files_btn)
        btn_row.addWidget(self._files_pause_btn)
        btn_row.addStretch()
        page.add_layout(btn_row)

        page.add_stretch()

        # State
        self._indexer_proc = None   # type: subprocess.Popen | None
        self._files_poll_timer = QTimer(self)
        self._files_poll_timer.setInterval(1500)
        self._files_poll_timer.timeout.connect(self._refresh_files_status)

        # Initial refresh
        self._refresh_files_status()
        return page

    def _index_state(self):
        """Return ('done'|'running'|'paused'|'not_started', detail_str, overall_pct)."""
        import json, os
        if os.path.exists(INDEX_DONE_MARKER):
            return "done", "", 100

        is_running = self._indexer_proc is not None and self._indexer_proc.poll() is None

        if is_running or os.path.exists(INDEX_PROGRESS_PATH):
            try:
                prog = json.loads(open(INDEX_PROGRESS_PATH).read())

                if is_running and prog.get("preparing"):
                    return "preparing", "Preparing…", 0

                phase      = prog.get("current_phase", 1)
                phase_pct  = float(prog.get("phase_pct", 0))
                eta        = prog.get("eta", "")
                label      = prog.get("phase_label", f"Phase {phase} of 3")
                phases_done = sum(1 for k in ("phase1_complete", "phase2_complete", "phase3_complete") if prog.get(k))

                # Overall progress: each phase is 1/3 of the total
                overall = (phases_done * 100 + phase_pct) / 3
                overall_pct = int(overall)

                detail = f"Phase {phase}/3 — {label}  ·  {overall_pct}%"
                if eta and eta != "?":
                    detail += f"  ·  ETA {eta}"

                state = "running" if is_running else "paused"
                return state, detail, overall_pct
            except Exception:
                pass

            if is_running:
                return "preparing", "Preparing…", 0
            return "paused", "Paused", 0

        return "not_started", "", 0

    def _refresh_files_status(self):
        state, detail, overall_pct = self._index_state()

        dot_color  = {"done": "#34c759", "running": "#007aff", "preparing": "#007aff", "paused": "#ff9500", "not_started": "#8e8e93"}[state]
        label_text = {"done": "Indexed", "running": "Indexing…", "preparing": "Indexing…", "paused": "Paused", "not_started": "Not indexed"}[state]
        btn_text   = {"done": "Re-index", "running": "Running…", "preparing": "Preparing…", "paused": "Resume Indexing", "not_started": "Start Indexing"}[state]

        is_active = state in ("running", "preparing")

        self._files_dot.setStyleSheet(f"color: {dot_color};")
        self._files_status_lbl.setText(label_text)
        self._files_detail_lbl.setText(detail)
        self._files_detail_lbl.setVisible(bool(detail))
        self._files_btn.setText(btn_text)
        self._files_btn.setEnabled(not is_active)
        self._files_pause_btn.setVisible(is_active)

        if state == "running":
            self._files_progress_bar.setValue(overall_pct)
            self._files_progress_bar.show()
            if hasattr(self, "_files_pct_lbl"):
                self._files_pct_lbl.setText(f"{overall_pct}%")
                self._files_pct_lbl.show()
        else:
            self._files_progress_bar.hide()
            if hasattr(self, "_files_pct_lbl"):
                self._files_pct_lbl.hide()
            if state not in ("preparing",):
                self._files_poll_timer.stop()

    def _kill_indexer(self):
        """Terminate the indexer subprocess. Called on app quit to prevent orphaned processes."""
        if self._indexer_proc is not None and self._indexer_proc.poll() is None:
            try:
                self._indexer_proc.terminate()
            except Exception:
                pass

    def _on_files_pause_clicked(self):
        """Pause indexing by terminating the subprocess (progress is preserved on disk)."""
        if self._indexer_proc is not None and self._indexer_proc.poll() is None:
            try:
                self._indexer_proc.terminate()
            except Exception:
                pass
        self._refresh_files_status()

    def _on_files_btn_clicked(self):
        import subprocess, sys, os
        state, _detail, _pct = self._index_state()
        if state == "running":
            return

        # Remove done marker so indexer rebuilds (re-index case)
        if state == "done":
            try:
                os.remove(INDEX_DONE_MARKER)
            except FileNotFoundError:
                pass

        # Find indexer script relative to this file
        this_dir = os.path.dirname(os.path.abspath(__file__))
        indexer = os.path.join(this_dir, "..", "..", "services", "search", "indexer.py")
        indexer = os.path.normpath(indexer)

        self._indexer_proc = subprocess.Popen(
            [sys.executable, indexer],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=lambda: os.nice(10),
        )
        self._refresh_files_status()
        self._files_poll_timer.start()

    def _build_account(self) -> QWidget:
        page = SettingsPage("Account", "Manage your Omni account and subscription.")

        # ── Auth stack (logged-out / logged-in) ───────────────────────
        self.account_stack = QStackedWidget()
        self.account_stack.addWidget(self._build_auth_form())    # index 0 — logged out
        self.account_stack.addWidget(self._build_account_info()) # index 1 — logged in

        def _sync_stack_size(index: int):
            for i in range(self.account_stack.count()):
                sp = (QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred) \
                     if i == index else \
                     (QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
                self.account_stack.widget(i).setSizePolicy(*sp)
            self.account_stack.adjustSize()

        self.account_stack.currentChanged.connect(_sync_stack_size)
        _sync_stack_size(self.account_stack.currentIndex())
        page.add_widget(self.account_stack)

        # ── Shared status label ───────────────────────────────────────
        self.account_status_lbl = QLabel("")
        self.account_status_lbl.setObjectName("AccountStatusLbl")
        self.account_status_lbl.setFont(_font("Manrope", 10))
        self.account_status_lbl.setWordWrap(True)
        page.add_widget(self.account_status_lbl)

        page.add_stretch()
        return page

    # ── Referrals page ────────────────────────────────────────────────

    def _build_referral(self) -> QWidget:
        page = SettingsPage("Referrals")
        lay = page.content_layout

        self._referral_card = _ReferralCard()
        rc = self._referral_card._inner

        # Header
        ref_hdr_row = QHBoxLayout()
        ref_hdr_row.setContentsMargins(0, 0, 0, 0)
        ref_hdr_row.setSpacing(0)
        ref_title = QLabel("Invite friends")
        ref_title.setFont(_font("Manrope", 15, bold=True))
        ref_title.setStyleSheet("color: rgba(255,255,255,0.95);")
        ref_hdr_row.addWidget(ref_title)
        ref_hdr_row.addStretch()
        ref_star = QLabel("✦")
        ref_star.setFont(_font("Manrope", 13))
        ref_star.setStyleSheet("color: rgba(251,191,36,0.60);")
        ref_hdr_row.addWidget(ref_star)
        rc.addLayout(ref_hdr_row)

        # Benefits — two rows: referrer and referred
        benefits_widget = QWidget()
        benefits_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        bv = QVBoxLayout(benefits_widget)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(5)

        def _section_label(text, color):
            lbl = QLabel(text)
            lbl.setFont(_font("Manrope", 8, bold=True))
            lbl.setStyleSheet(f"color: {color}; background: transparent;")
            return lbl

        def _tier_row(milestone, reward):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            left = QLabel(milestone)
            left.setFont(_font("Manrope", 10))
            left.setStyleSheet("color: rgba(255,255,255,0.55); background: transparent;")
            left.setFixedWidth(100)
            right = QLabel(reward)
            right.setFont(_font("Manrope", 10, bold=True))
            right.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent;")
            row.addWidget(left)
            row.addWidget(right)
            row.addStretch()
            return row

        # YOU section
        bv.addWidget(_section_label("YOU EARN", "#FBBF24"))
        bv.addSpacing(2)
        bv.addLayout(_tier_row("1 referral",   "1 month free"))
        bv.addLayout(_tier_row("3 referrals",  "2 months free"))
        bv.addLayout(_tier_row("5 referrals",  "3 months free"))
        bv.addLayout(_tier_row("5+ active",    "Omni free forever"))

        bv.addSpacing(8)

        # THEY section
        bv.addWidget(_section_label("THEY GET", "rgba(255,255,255,0.30)"))
        bv.addSpacing(2)
        they_lbl = QLabel("1 month free trial when signing up for Pro with your link")
        they_lbl.setFont(_font("Manrope", 10))
        they_lbl.setStyleSheet("color: rgba(255,255,255,0.55); background: transparent;")
        they_lbl.setWordWrap(True)
        bv.addWidget(they_lbl)
        rc.addWidget(benefits_widget)

        rc.addSpacing(6)

        # Stats row — clean large numbers with thin dividers
        stats_widget = QWidget()
        stats_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        stats_h = QHBoxLayout(stats_widget)
        stats_h.setContentsMargins(0, 0, 0, 0)
        stats_h.setSpacing(0)

        def _stat_col(label, value_attr):
            col = QWidget()
            col.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            cl = QVBoxLayout(col)
            cl.setContentsMargins(0, 4, 0, 4)
            cl.setSpacing(2)
            val = QLabel("—")
            val.setFont(_font("Manrope", 22, bold=True))
            val.setStyleSheet("color: rgba(255,255,255,0.90); background: transparent;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl = QLabel(label)
            lbl.setFont(_font("Manrope", 9))
            lbl.setStyleSheet("color: rgba(255,255,255,0.32); background: transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(val)
            cl.addWidget(lbl)
            setattr(self, value_attr, val)
            return col

        def _stat_divider():
            d = QFrame()
            d.setFrameShape(QFrame.Shape.VLine)
            d.setFixedWidth(1)
            d.setStyleSheet("background: rgba(255,255,255,0.09); border: none;")
            return d

        stats_h.addWidget(_stat_col("Confirmed Pro",  "_ref_stat_confirmed"), 1)
        stats_h.addWidget(_stat_divider())
        stats_h.addWidget(_stat_col("Active",         "_ref_stat_active"),    1)
        stats_h.addWidget(_stat_divider())
        stats_h.addWidget(_stat_col("Free months",    "_ref_stat_months"),    1)
        rc.addWidget(stats_widget)

        rc.addSpacing(2)

        # Milestone progress track
        self._milestone_track = _MilestoneTrack()
        rc.addWidget(self._milestone_track)

        rc.addSpacing(4)

        # Permanently free badge (hidden until 5 active)
        self._ref_free_badge = QLabel("✦  Permanently Free  —  5 active referrals maintained")
        self._ref_free_badge.setFont(_font("Manrope", 10, bold=True))
        self._ref_free_badge.setStyleSheet(
            "color: #FBBF24; background: rgba(251,191,36,0.12); border: 1px solid rgba(251,191,36,0.35); "
            "border-radius: 8px; padding: 6px 14px;"
        )
        self._ref_free_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ref_free_badge.setVisible(False)
        rc.addWidget(self._ref_free_badge)

        # Link container — dark inner box
        link_container = QWidget()
        link_container.setObjectName("RefLinkContainer")
        link_container.setStyleSheet(
            "#RefLinkContainer { background: rgba(0,0,0,0.28); border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; }"
        )
        link_lay = QHBoxLayout(link_container)
        link_lay.setContentsMargins(12, 0, 8, 0)
        link_lay.setSpacing(8)

        self._ref_link_edit = QLineEdit()
        self._ref_link_edit.setReadOnly(True)
        self._ref_link_edit.setPlaceholderText("Sign in to see your link…")
        self._ref_link_edit.setFixedHeight(40)
        self._ref_link_edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; color: rgba(255,255,255,0.72); "
            "font-family: Menlo, 'SF Mono', monospace; font-size: 10px; "
            "selection-background-color: rgba(251,191,36,0.25); }"
        )
        link_lay.addWidget(self._ref_link_edit, 1)

        self._ref_copy_btn = QPushButton("Copy ↗")
        self._ref_copy_btn.setFixedHeight(28)
        self._ref_copy_btn.setFixedWidth(72)
        self._ref_copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ref_copy_btn.setStyleSheet(
            "QPushButton { background: rgba(251,191,36,0.16); border: 1px solid rgba(251,191,36,0.38); "
            "border-radius: 7px; color: #FBBF24; font-family: Manrope; font-size: 10px; font-weight: 700; } "
            "QPushButton:hover { background: rgba(251,191,36,0.28); border-color: rgba(251,191,36,0.60); } "
            "QPushButton:disabled { opacity: 0.28; }"
        )
        self._ref_copy_btn.setEnabled(False)
        self._ref_copy_btn.clicked.connect(self._copy_referral_link)
        link_lay.addWidget(self._ref_copy_btn)

        rc.addWidget(link_container)

        self._ref_status_lbl = QLabel("")
        self._ref_status_lbl.setFont(_font("Manrope", 9))
        self._ref_status_lbl.setStyleSheet("color: rgba(251,191,36,0.65);")
        self._ref_status_lbl.setMaximumHeight(0)
        rc.addWidget(self._ref_status_lbl)

        lay.addWidget(self._referral_card)
        lay.addStretch()
        return page

    # ── Developer page ────────────────────────────────────────────────

    def _build_feedback(self) -> QWidget:
        page = SettingsPage("Feedback",
            "Your input shapes what Omni becomes. We read every submission personally.")

        dark = self.current_theme == "dark"
        self._feedback_type = "feature_request"

        # ── Type selection cards ──────────────────────────────────────
        self._fb_feature_card = _FbTypeCard(
            "💡", "Feature request",
            "Share an idea or improvement you'd love to see.",
            "feature_request", dark,
        )
        self._fb_bug_card = _FbTypeCard(
            "🐛", "Bug report",
            "Tell us about something that isn't working right.",
            "bug_report", dark,
        )
        self._fb_feature_card.set_selected(True)
        self._fb_feature_card.clicked_sig.connect(
            lambda: self._on_feedback_card_selected("feature_request"))
        self._fb_bug_card.clicked_sig.connect(
            lambda: self._on_feedback_card_selected("bug_report"))

        type_row = QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(10)
        type_row.addWidget(self._fb_feature_card)
        type_row.addWidget(self._fb_bug_card)
        page.add_layout(type_row)
        page.add_spacing(14)

        # ── Form card with accent top strip ──────────────────────────
        form_card = _FbFormCard(dark=dark)
        self._fb_form_card = form_card
        fc = form_card._inner
        fc.setContentsMargins(0, 0, 0, 0)
        fc.setSpacing(0)

        # Padded content wrapper (sits below the 4 px accent strip)
        content_w = QWidget()
        content_w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        cw = QVBoxLayout(content_w)
        cw.setContentsMargins(20, 14, 20, 16)
        cw.setSpacing(0)
        fc.addWidget(content_w)

        def _field_tag(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setFont(_font("Manrope", 9, bold=True))
            lbl.setStyleSheet(
                "color: rgba(255,255,255,0.32); letter-spacing: 1px; background: transparent;"
                if dark else
                "color: rgba(0,0,0,0.38); letter-spacing: 1px; background: transparent;"
            )
            return lbl

        # -- TITLE field label + input
        self._fb_title_tag = _field_tag("TITLE")
        cw.addWidget(self._fb_title_tag)
        cw.addSpacing(3)

        self._fb_title = QLineEdit()
        self._fb_title.setPlaceholderText("Give it a brief title…")
        self._fb_title.setFont(_font("Manrope", 13, bold=True))
        self._fb_title.setFixedHeight(36)
        self._fb_title.setStyleSheet(
            "QLineEdit { background: transparent; border: none; "
            "color: rgba(255,255,255,0.90); font-family: 'Manrope'; font-size: 13px; font-weight: 700; padding: 0 2px; } "
            "QLineEdit::placeholder { color: rgba(255,255,255,0.22); }"
            if dark else
            "QLineEdit { background: transparent; border: none; "
            "color: rgba(17,17,17,0.90); font-family: 'Manrope'; font-size: 13px; font-weight: 700; padding: 0 2px; } "
            "QLineEdit::placeholder { color: rgba(17,17,17,0.28); }"
        )
        cw.addWidget(self._fb_title)
        cw.addSpacing(8)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setObjectName("SepLine")
        cw.addWidget(sep1)
        cw.addSpacing(10)

        # -- DESCRIPTION field label + textarea
        self._fb_desc_tag = _field_tag("DESCRIPTION")
        cw.addWidget(self._fb_desc_tag)
        cw.addSpacing(3)

        self._fb_body = QTextEdit()
        self._fb_body.setPlaceholderText("Describe your idea or bug in as much detail as you like…")
        self._fb_body.setFont(_font("Manrope", 11))
        self._fb_body.setMinimumHeight(110)
        self._fb_body.setMaximumHeight(200)
        self._fb_body.setObjectName("FeedbackBody")
        self._fb_body.setStyleSheet(
            "QTextEdit { background: transparent; border: none; "
            "color: rgba(255,255,255,0.85); font-family: 'Manrope'; font-size: 11px; padding: 2px; } "
            "QTextEdit::placeholder { color: rgba(255,255,255,0.22); }"
            if dark else
            "QTextEdit { background: transparent; border: none; "
            "color: rgba(17,17,17,0.85); font-family: 'Manrope'; font-size: 11px; padding: 2px; } "
            "QTextEdit::placeholder { color: rgba(17,17,17,0.28); }"
        )
        cw.addWidget(self._fb_body)
        cw.addSpacing(10)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setObjectName("SepLine")
        cw.addWidget(sep2)
        cw.addSpacing(10)

        # -- Bottom row: status label + submit button
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(12)

        self._fb_status_lbl = QLabel("")
        self._fb_status_lbl.setObjectName("AccountStatusLbl")
        self._fb_status_lbl.setFont(_font("Manrope", 10))
        self._fb_status_lbl.setWordWrap(True)
        bottom_row.addWidget(self._fb_status_lbl, 1)

        self._fb_submit_btn = QPushButton("Send feedback  →")
        self._fb_submit_btn.setFixedHeight(36)
        self._fb_submit_btn.setFixedWidth(162)
        self._fb_submit_btn.setStyleSheet(_PRIMARY_BTN_SS)
        self._fb_submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fb_submit_btn.setFont(_font("Manrope", 11, bold=True))
        self._fb_submit_btn.clicked.connect(self._submit_feedback)
        bottom_row.addWidget(self._fb_submit_btn)

        cw.addLayout(bottom_row)

        page.add_widget(form_card)
        page.add_stretch()
        return page

    def _on_feedback_card_selected(self, fb_type: str):
        self._feedback_type = fb_type
        if hasattr(self, "_fb_feature_card"):
            self._fb_feature_card.set_selected(fb_type == "feature_request")
        if hasattr(self, "_fb_bug_card"):
            self._fb_bug_card.set_selected(fb_type == "bug_report")
        if hasattr(self, "_fb_form_card") and isinstance(self._fb_form_card, _FbFormCard):
            self._fb_form_card.set_accent(fb_type)

    def _apply_personality_toggle_theme(self, dark: bool):
        """Propagate dark/light theme to personality cards."""
        if not hasattr(self, "_mode_options"):
            return
        for card in self._mode_options.values():
            card.set_dark(dark)

    def _apply_fb_pill_theme(self, dark: bool):
        """Propagate dark/light theme to feedback type cards and form card."""
        for attr in ("_fb_feature_card", "_fb_bug_card"):
            card = getattr(self, attr, None)
            if card:
                card.set_dark(dark)
        if hasattr(self, "_fb_form_card") and isinstance(self._fb_form_card, _FbFormCard):
            self._fb_form_card.set_dark(dark)
        tag_color = ("color: rgba(255,255,255,0.32); letter-spacing: 1px; background: transparent;"
                     if dark else
                     "color: rgba(0,0,0,0.38); letter-spacing: 1px; background: transparent;")
        for attr in ("_fb_title_tag", "_fb_desc_tag"):
            lbl = getattr(self, attr, None)
            if lbl:
                lbl.setStyleSheet(tag_color)
        title_ss = (
            "QLineEdit { background: transparent; border: none; "
            "color: rgba(255,255,255,0.90); font-family: 'Manrope'; font-size: 13px; font-weight: 700; padding: 0 2px; } "
            "QLineEdit::placeholder { color: rgba(255,255,255,0.22); }"
            if dark else
            "QLineEdit { background: transparent; border: none; "
            "color: rgba(17,17,17,0.90); font-family: 'Manrope'; font-size: 13px; font-weight: 700; padding: 0 2px; } "
            "QLineEdit::placeholder { color: rgba(17,17,17,0.28); }"
        )
        if hasattr(self, "_fb_title"):
            self._fb_title.setStyleSheet(title_ss)
        body_ss = (
            "QTextEdit { background: transparent; border: none; "
            "color: rgba(255,255,255,0.85); font-family: 'Manrope'; font-size: 11px; padding: 2px; } "
            "QTextEdit::placeholder { color: rgba(255,255,255,0.22); }"
            if dark else
            "QTextEdit { background: transparent; border: none; "
            "color: rgba(17,17,17,0.85); font-family: 'Manrope'; font-size: 11px; padding: 2px; } "
            "QTextEdit::placeholder { color: rgba(17,17,17,0.28); }"
        )
        if hasattr(self, "_fb_body"):
            self._fb_body.setStyleSheet(body_ss)

    def _on_feedback_type_changed(self, btn_id: int, checked: bool):
        if checked:
            self._feedback_type = "feature_request" if btn_id == 0 else "bug_report"

    def _submit_feedback(self):
        title = self._fb_title.text().strip()
        body  = self._fb_body.toPlainText().strip()

        if not title:
            self._set_feedback_status("Please enter a title.", error=True)
            return
        if not body:
            self._set_feedback_status("Please enter a description.", error=True)
            return

        self._fb_submit_btn.setEnabled(False)
        self._fb_submit_btn.setText("Sending…")
        self._set_feedback_status("")

        import threading, urllib.request, json as _json
        from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID

        user    = auth.get_user() or {}
        fb_type = self._feedback_type
        payload = {"type": fb_type, "title": title, "description": body}
        if user.get("id"):
            payload["user_id"] = user["id"]

        def _run():
            try:
                headers = {
                    "Content-Type": "application/json",
                    "X-Omni-Secret": OMNI_SECRET,
                    "X-Device-ID":   DEVICE_ID,
                    "User-Agent":    "OmniApp/1.0",
                }
                token = auth.get_access_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                req = urllib.request.Request(
                    f"{BACKEND_URL}/v1/feedback",
                    data=_json.dumps(payload).encode(),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
                self._dispatch.emit(lambda: self._on_feedback_sent())
            except Exception as e:
                self._dispatch.emit(lambda err=e: self._set_feedback_status(f"Failed to send: {err}", error=True))
                self._dispatch.emit(lambda: self._reset_feedback_btn())

        threading.Thread(target=_run, daemon=True).start()

    def _on_feedback_sent(self):
        self._fb_title.clear()
        self._fb_body.clear()
        self._reset_feedback_btn()
        self._set_feedback_status("✓  Sent! Thanks for your feedback.", error=False)

    def _reset_feedback_btn(self):
        self._fb_submit_btn.setEnabled(True)
        self._fb_submit_btn.setText("Send feedback  →")

    def _set_feedback_status(self, msg: str, error: bool = False):
        self._fb_status_lbl.setText(msg)
        self._fb_status_lbl.setProperty("error", "true" if error else "false")
        self._fb_status_lbl.style().unpolish(self._fb_status_lbl)
        self._fb_status_lbl.style().polish(self._fb_status_lbl)
        if msg:
            QTimer.singleShot(8000, lambda: self._fb_status_lbl.setText(""))

    # ── Plan card factory ─────────────────────────────────────────────

    def _make_plan_card(self, featured: bool) -> tuple:
        """Build a professional plan card. Returns (card_widget, cta_button)."""
        card = _PlanCard(featured=featured)
        inner = card._inner

        # Name + badge row
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("Yearly" if featured else "Monthly")
        name_lbl.setFont(_font("Manrope", 11, bold=True))
        name_row.addWidget(name_lbl)
        if featured:
            badge = QLabel("Best value")
            badge.setObjectName("BestValueBadge")
            badge.setFont(_font("Manrope", 8, bold=True))
            badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            name_row.addWidget(badge)
        name_row.addStretch()
        inner.addLayout(name_row)
        inner.addSpacing(10)

        # Price display
        price_row = QHBoxLayout()
        price_row.setSpacing(3)
        price_row.setContentsMargins(0, 0, 0, 0)
        price_val = QLabel("$6" if featured else "$9")
        price_val.setFont(_font("Manrope", 26, bold=True))
        per_lbl = QLabel("/mo")
        per_lbl.setFont(_font("Manrope", 11))
        per_lbl.setObjectName("PlanCardDesc")
        per_lbl.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        price_row.addWidget(price_val)
        price_row.addWidget(per_lbl)
        price_row.addStretch()
        inner.addLayout(price_row)

        billing_lbl = QLabel("$72 billed yearly" if featured else "billed monthly")
        billing_lbl.setFont(_font("Manrope", 9))
        billing_lbl.setObjectName("PlanCardDesc")
        inner.addWidget(billing_lbl)
        inner.addSpacing(12)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("SepLine")
        inner.addWidget(sep)
        inner.addSpacing(10)

        # Features
        features = ["Unlimited AI queries", "No daily limits", "Sync across devices"]
        if featured:
            features.append("Save 33% vs monthly")
        for feat in features:
            feat_row = QHBoxLayout()
            feat_row.setSpacing(6)
            feat_row.setContentsMargins(0, 0, 0, 0)
            check = QLabel("✓")
            check.setFont(_font("Manrope", 10))
            check.setStyleSheet("color: #818cf8; background: transparent;")
            check.setFixedWidth(14)
            feat_lbl = QLabel(feat)
            feat_lbl.setFont(_font("Manrope", 10))
            feat_lbl.setWordWrap(True)
            feat_row.addWidget(check)
            feat_row.addWidget(feat_lbl, 1)
            inner.addLayout(feat_row)
            inner.addSpacing(4)

        inner.addStretch()
        inner.addSpacing(12)

        # CTA button
        cta = QPushButton("Get Yearly" if featured else "Get Monthly")
        cta.setFixedHeight(36)
        cta.setFont(_font("Manrope", 11, bold=True))
        cta.setStyleSheet(_PLAN_BTN_SS if featured else _GHOST_PLAN_BTN_SS)
        cta.setCursor(Qt.CursorShape.PointingHandCursor)
        inner.addWidget(cta)

        return card, cta

    # ── Logged-out: sign-in / sign-up form ───────────────────────────

    def _build_auth_form(self) -> QWidget:
        dark = self.current_theme == "dark"
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(14)

        # ── Sign-in card ─────────────────────────────────────────────
        self._auth_card = _FeedbackFormCard(dark=dark)
        ac = self._auth_card._inner
        ac.setContentsMargins(28, 28, 28, 28)
        ac.setSpacing(0)

        # Header — logo mark + title
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(10)
        logo_mark = QLabel("✦")
        logo_mark.setFont(_font("Manrope", 16))
        logo_mark.setStyleSheet("color: #818cf8; background: transparent;")
        ac_title = QLabel("Sign in to Omni")
        ac_title.setFont(_font("Manrope", 16, bold=True))
        hdr_row.addWidget(logo_mark)
        hdr_row.addWidget(ac_title)
        hdr_row.addStretch()
        ac.addLayout(hdr_row)
        ac.addSpacing(4)

        ac_sub = QLabel("Access your account and manage your subscription.")
        ac_sub.setObjectName("DescLbl")
        ac_sub.setFont(_font("Manrope", 10))
        ac_sub.setWordWrap(True)
        ac.addWidget(ac_sub)
        ac.addSpacing(20)

        # Google OAuth — primary action at top
        self.google_btn = QPushButton("  Continue with Google")
        self.google_btn.setFixedHeight(42)
        self.google_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.google_btn.setIcon(_make_google_icon(18))
        self.google_btn.setIconSize(QSize(18, 18))
        self.google_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.google_btn.setFont(_font("Manrope", 12))
        self.google_btn.setStyleSheet(_GOOGLE_BTN_SS)
        self.google_btn.clicked.connect(lambda: self._do_oauth("google"))
        ac.addWidget(self.google_btn)
        ac.addSpacing(18)

        # "or sign in with email" divider
        sep_row = QHBoxLayout()
        sep_row.setContentsMargins(0, 0, 0, 0)
        sep_row.setSpacing(10)
        sep_l = QFrame()
        sep_l.setFrameShape(QFrame.Shape.HLine)
        sep_l.setObjectName("SepLine")
        sep_r = QFrame()
        sep_r.setFrameShape(QFrame.Shape.HLine)
        sep_r.setObjectName("SepLine")
        sep_lbl = QLabel("or sign in with email")
        sep_lbl.setObjectName("DescLbl")
        sep_lbl.setFont(_font("Manrope", 9))
        sep_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep_row.addWidget(sep_l, 1)
        sep_row.addWidget(sep_lbl)
        sep_row.addWidget(sep_r, 1)
        ac.addLayout(sep_row)
        ac.addSpacing(16)

        # Email field (placeholder only — no label needed)
        self.auth_email_edit = self._edit("your@email.com")
        self.auth_email_edit.textChanged.connect(lambda: self._set_auth_error(""))
        ac.addWidget(self.auth_email_edit)
        ac.addSpacing(8)

        # Password field
        self.auth_pass_edit = self._edit("••••••••", password=True)
        self.auth_pass_edit.textChanged.connect(lambda: self._set_auth_error(""))
        self.auth_pass_edit.returnPressed.connect(self._do_sign_in)
        ac.addWidget(self.auth_pass_edit)
        ac.addSpacing(6)

        # Forgot password — right-aligned link
        forgot_row = QHBoxLayout()
        forgot_row.setContentsMargins(0, 0, 0, 0)
        forgot_row.addStretch()
        self.forgot_pw_btn = QPushButton("Forgot password?")
        self.forgot_pw_btn.setFlat(True)
        self.forgot_pw_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_pw_btn.setStyleSheet(
            "QPushButton { color: rgba(255,255,255,0.35); background: transparent; border: none; "
            "font-family: Manrope; font-size: 10px; text-align: right; padding: 0; } "
            "QPushButton:hover { color: #818cf8; }"
        )
        self.forgot_pw_btn.clicked.connect(self._do_forgot_password)
        forgot_row.addWidget(self.forgot_pw_btn)
        ac.addLayout(forgot_row)
        ac.addSpacing(14)

        # Error label
        self.auth_error_lbl = QLabel("")
        self.auth_error_lbl.setFont(_font("Manrope", 10))
        self.auth_error_lbl.setStyleSheet("color: #f87171; background: transparent;")
        self.auth_error_lbl.setWordWrap(True)
        self.auth_error_lbl.setVisible(False)
        ac.addWidget(self.auth_error_lbl)

        # Sign In — full-width primary
        self.sign_in_btn = QPushButton("Sign In")
        self.sign_in_btn.setFixedHeight(42)
        self.sign_in_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.sign_in_btn.setStyleSheet(_PRIMARY_BTN_SS)
        self.sign_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_in_btn.setFont(_font("Manrope", 12, bold=True))
        self.sign_in_btn.clicked.connect(self._do_sign_in)
        ac.addWidget(self.sign_in_btn)
        ac.addSpacing(14)

        # Create account — inline text link row
        signup_row = QHBoxLayout()
        signup_row.setContentsMargins(0, 0, 0, 0)
        signup_row.setSpacing(0)
        signup_row.addStretch()
        no_acct_lbl = QLabel("Don't have an account?")
        no_acct_lbl.setFont(_font("Manrope", 10))
        no_acct_lbl.setObjectName("DescLbl")
        signup_row.addWidget(no_acct_lbl)
        signup_row.addSpacing(5)
        self.sign_up_btn = QPushButton("Create one →")
        self.sign_up_btn.setObjectName("ResetBtn")
        self.sign_up_btn.setFlat(True)
        self.sign_up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_up_btn.setFont(_font("Manrope", 10, bold=True))
        self.sign_up_btn.setStyleSheet(
            "QPushButton { color: #818cf8; background: transparent; border: none; padding: 0; } "
            "QPushButton:hover { color: #a5b4fc; }"
        )
        self.sign_up_btn.clicked.connect(self._do_sign_up)
        signup_row.addWidget(self.sign_up_btn)
        signup_row.addStretch()
        ac.addLayout(signup_row)

        # Hidden compat widgets
        self.github_btn = QPushButton("Continue with GitHub")
        self.github_btn.setObjectName("OAuthBtn")
        self.github_btn.setFixedHeight(38)
        self.github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.github_btn.clicked.connect(lambda: self._do_oauth("github"))
        self.github_btn.setVisible(False)

        # Placeholder spacer so the card layout doesn't shift — real usage card is below
        _spacer = QWidget()
        _spacer.setVisible(False)
        ac.addWidget(_spacer)

        lay.addWidget(self._auth_card)

        # ── Usage card (shown for free logged-out users) ───────────────
        self._login_usage_section = _FeedbackFormCard(dark=dark)
        self._login_usage_section.setVisible(False)
        lus = self._login_usage_section._inner
        lus.setContentsMargins(20, 14, 20, 14)
        lus.setSpacing(6)

        usage_hdr_row = QHBoxLayout()
        usage_hdr_lbl = QLabel("DAILY USAGE")
        usage_hdr_lbl.setObjectName("SectionHeader")
        usage_hdr_lbl.setFont(_font("Manrope", 9, bold=True))
        usage_hdr_row.addWidget(usage_hdr_lbl)
        usage_hdr_row.addStretch()
        self.login_usage_lbl = QLabel("0 / 10 requests today")
        self.login_usage_lbl.setObjectName("UsageLbl")
        self.login_usage_lbl.setFont(_font("Manrope", 10))
        usage_hdr_row.addWidget(self.login_usage_lbl)
        lus.addLayout(usage_hdr_row)

        self.login_usage_bar = _UsageBar()
        lus.addWidget(self.login_usage_bar)
        lay.addWidget(self._login_usage_section)

        # ── Plans section ─────────────────────────────────────────────
        self.upgrade_box_login = _UpgradeBox()
        ub_login = self.upgrade_box_login._inner
        ub_login.setContentsMargins(20, 18, 20, 18)
        ub_login.setSpacing(0)

        ub_hdr = QHBoxLayout()
        ub_hdr.setSpacing(8)
        ub_icon = QLabel("✦")
        ub_icon.setFont(_font("Manrope", 13))
        ub_icon.setStyleSheet("color: #818cf8; background: transparent;")
        ub_title = QLabel("Upgrade to Pro")
        ub_title.setFont(_font("Manrope", 13, bold=True))
        ub_hdr.addWidget(ub_icon)
        ub_hdr.addWidget(ub_title)
        ub_hdr.addStretch()
        ub_login.addLayout(ub_hdr)
        ub_login.addSpacing(4)

        ub_tagline = QLabel("Unlimited AI queries, no daily limits. Cancel anytime.")
        ub_tagline.setObjectName("DescLbl")
        ub_tagline.setFont(_font("Manrope", 10))
        ub_tagline.setWordWrap(True)
        ub_login.addWidget(ub_tagline)
        ub_login.addSpacing(14)

        plan_cards_row = QHBoxLayout()
        plan_cards_row.setContentsMargins(0, 0, 0, 0)
        plan_cards_row.setSpacing(10)

        monthly_plan, self.upgrade_monthly_btn_login = self._make_plan_card(featured=False)
        self.upgrade_monthly_btn_login.clicked.connect(lambda: self._start_checkout("monthly"))
        plan_cards_row.addWidget(monthly_plan)

        yearly_plan, self.upgrade_yearly_btn_login = self._make_plan_card(featured=True)
        self.upgrade_yearly_btn_login.clicked.connect(lambda: self._start_checkout("yearly"))
        plan_cards_row.addWidget(yearly_plan)

        ub_login.addLayout(plan_cards_row)
        ub_login.addSpacing(6)

        self.upgrade_status_lbl_login = QLabel("")
        self.upgrade_status_lbl_login.setObjectName("DescLbl")
        self.upgrade_status_lbl_login.setFont(_font("Manrope", 10))
        self.upgrade_status_lbl_login.setWordWrap(True)
        ub_login.addWidget(self.upgrade_status_lbl_login)

        lay.addWidget(self.upgrade_box_login)

        return w

    # ── Logged-in: account info ───────────────────────────────────────

    def _sep(self) -> QFrame:
        f = QFrame(); f.setFrameShape(QFrame.Shape.HLine); f.setObjectName("SepLine")
        return f

    def _build_account_info(self) -> QWidget:
        dark = self.current_theme == "dark"
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Profile hero card ─────────────────────────────────────────
        self._profile_card = _FeedbackFormCard(dark=dark)
        pc = self._profile_card._inner
        pc.setContentsMargins(16, 14, 16, 14)
        pc.setSpacing(0)

        # Single flat row: avatar | email | plan badge | stretch | refresh
        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.setSpacing(10)
        profile_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._profile_avatar = _AvatarWidget(initial="?")
        profile_row.addWidget(self._profile_avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        self.account_email_lbl = QLabel("")
        self.account_email_lbl.setFont(_font("Manrope", 13, bold=True))
        self.account_email_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        profile_row.addWidget(self.account_email_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.plan_badge = QLabel("FREE")
        self.plan_badge.setObjectName("PlanBadgeFree")
        self.plan_badge.setFont(_font("Manrope", 9, bold=True))
        self.plan_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.plan_badge.setFixedHeight(18)
        self.plan_badge.setContentsMargins(7, 0, 7, 0)
        self.plan_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        profile_row.addWidget(self.plan_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        profile_row.addStretch(1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("RefreshBtn")
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("Refresh plan status")
        self.refresh_btn.clicked.connect(self.refresh_account)
        profile_row.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        pc.addLayout(profile_row)
        lay.addWidget(self._profile_card)

        # Legacy refs kept for other methods
        self._security_card = self._profile_card
        self.secure_box = self._profile_card

        # ── Usage card (hidden for Pro) ───────────────────────────────
        self._usage_section = _FeedbackFormCard(dark=dark)
        us_inner = self._usage_section._inner
        us_inner.setContentsMargins(20, 14, 20, 14)
        us_inner.setSpacing(8)

        usage_hdr_row = QHBoxLayout()
        usage_hdr_row.setContentsMargins(0, 0, 0, 0)
        usage_hdr_row.addWidget(_section_header("DAILY USAGE"))
        usage_hdr_row.addStretch()
        self.usage_lbl = QLabel("0 / 10 requests today")
        self.usage_lbl.setObjectName("UsageLbl")
        self.usage_lbl.setFont(_font("Manrope", 10))
        self.usage_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        usage_hdr_row.addWidget(self.usage_lbl)
        us_inner.addLayout(usage_hdr_row)

        self.usage_bar = _UsageBar()
        us_inner.addWidget(self.usage_bar)
        lay.addWidget(self._usage_section)

        # ── Upgrade CTA card (hidden for Pro) ─────────────────────────
        self.upgrade_box = _UpgradeBox()
        ub = self.upgrade_box._inner
        ub.setContentsMargins(20, 18, 20, 18)
        ub.setSpacing(0)

        ub_hdr_row = QHBoxLayout()
        ub_hdr_row.setContentsMargins(0, 0, 0, 0)
        ub_hdr_row.setSpacing(8)
        ub_icon2 = QLabel("✦")
        ub_icon2.setFont(_font("Manrope", 14))
        ub_icon2.setStyleSheet("color: #818cf8; background: transparent;")
        ub_title = QLabel("Upgrade to Pro")
        ub_title.setFont(_font("Manrope", 14, bold=True))
        ub_hdr_row.addWidget(ub_icon2)
        ub_hdr_row.addWidget(ub_title)
        ub_hdr_row.addStretch()
        ub.addLayout(ub_hdr_row)
        ub.addSpacing(4)

        tagline = QLabel("Unlimited AI queries, no daily limits. Cancel anytime.")
        tagline.setObjectName("DescLbl")
        tagline.setFont(_font("Manrope", 10))
        tagline.setWordWrap(True)
        ub.addWidget(tagline)
        ub.addSpacing(14)

        plan_row2 = QHBoxLayout()
        plan_row2.setSpacing(10)
        plan_row2.setContentsMargins(0, 0, 0, 0)

        mc2, self.upgrade_monthly_btn = self._make_plan_card(featured=False)
        self.upgrade_monthly_btn.clicked.connect(lambda: self._start_checkout("monthly"))
        plan_row2.addWidget(mc2)

        yc2, self.upgrade_yearly_btn = self._make_plan_card(featured=True)
        self.upgrade_yearly_btn.clicked.connect(lambda: self._start_checkout("yearly"))
        plan_row2.addWidget(yc2)

        ub.addLayout(plan_row2)
        ub.addSpacing(6)

        self.upgrade_status_lbl = QLabel("")
        self.upgrade_status_lbl.setObjectName("DescLbl")
        self.upgrade_status_lbl.setFont(_font("Manrope", 10))
        self.upgrade_status_lbl.setWordWrap(True)
        ub.addWidget(self.upgrade_status_lbl)

        lay.addWidget(self.upgrade_box)

        # ── Account settings card ─────────────────────────────────────
        settings_card = _FeedbackFormCard(dark=dark)
        sc = settings_card._inner
        sc.setContentsMargins(20, 16, 20, 16)
        sc.setSpacing(0)

        sc.addWidget(_section_header("ACCOUNT SETTINGS"))
        sc.addSpacing(8)

        # Memory sync row
        sync_row = _SettingsRow("Memory sync", dark=dark)
        self.sync_status_lbl = QLabel("—")
        self.sync_status_lbl.setObjectName("UsageLbl")
        self.sync_status_lbl.setFont(_font("Manrope", 10))
        sync_row.right.addWidget(self.sync_status_lbl)
        self.sync_btn = QPushButton("Sync Now")
        self.sync_btn.setObjectName("ResetBtn")
        self.sync_btn.setFixedHeight(30)
        self.sync_btn.setFixedWidth(100)
        self.sync_btn.setFont(_font("Manrope", 10))
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.clicked.connect(self._do_sync_now)
        sync_row.right.addWidget(self.sync_btn)
        sc.addWidget(sync_row)

        # Change password row
        pw_row = _SettingsRow("Change password", dark=dark)
        self.new_password_edit = self._edit("New password", password=True)
        self.new_password_edit.setFixedHeight(32)
        self.new_password_edit.setFixedWidth(172)
        self.new_password_edit.returnPressed.connect(self._do_set_password)
        pw_row.right.addWidget(self.new_password_edit)
        self.set_password_btn = QPushButton("Update")
        self.set_password_btn.setObjectName("SaveBtn")
        self.set_password_btn.setFixedHeight(32)
        self.set_password_btn.setFont(_font("Manrope", 10))
        self.set_password_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_password_btn.clicked.connect(self._do_set_password)
        pw_row.right.addWidget(self.set_password_btn)
        sc.addWidget(pw_row)

        # Connected accounts row
        conn_row = _SettingsRow("Google account", dark=dark)
        self.link_google_btn = QPushButton("  Connect Google")
        self.link_google_btn.setFixedHeight(32)
        self.link_google_btn.setIcon(_make_google_icon(15))
        self.link_google_btn.setIconSize(QSize(15, 15))
        self.link_google_btn.setFont(_font("Manrope", 10))
        self.link_google_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_google_btn.setStyleSheet(_GOOGLE_BTN_SS)
        self.link_google_btn.clicked.connect(lambda: self._do_oauth("google"))
        conn_row.right.addWidget(self.link_google_btn)
        sc.addWidget(conn_row)

        lay.addWidget(settings_card)

        # ── Sign out ──────────────────────────────────────────────────
        sign_out_outer = QHBoxLayout()
        sign_out_outer.setContentsMargins(0, 4, 0, 0)
        sign_out_outer.addStretch()
        self.sign_out_btn = QPushButton("Sign Out")
        self.sign_out_btn.setObjectName("ResetBtn")
        self.sign_out_btn.setFixedHeight(32)
        self.sign_out_btn.setFixedWidth(88)
        self.sign_out_btn.setFont(_font("Manrope", 10))
        self.sign_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_out_btn.clicked.connect(self._do_sign_out)
        sign_out_outer.addWidget(self.sign_out_btn)
        lay.addLayout(sign_out_outer)

        return w
    # ── Widget factories ─────────────────────────────────────────────

    def _desc(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("DescLbl")
        lbl.setFont(_font("Manrope", 11))
        lbl.setWordWrap(True)
        return lbl

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("FieldLbl")
        lbl.setFont(_font("Manrope", 10, bold=True))
        return lbl

    def _edit(self, placeholder: str, password: bool = False) -> QLineEdit:
        dark = self.current_theme == "dark"
        bg       = "#2e2e3e" if dark else "#ebebf0"
        bg_focus = "#363648" if dark else "#e0e0e8"
        border   = "rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.15)"
        color    = "rgba(255,255,255,0.90)" if dark else "rgba(0,0,0,0.85)"
        ph_color = "rgba(255,255,255,0.35)" if dark else "rgba(0,0,0,0.35)"
        edit = QLineEdit()
        edit.setObjectName("SettingsEdit")
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(40)
        edit.setStyleSheet(f"""
            QLineEdit {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 0px 13px;
                color: {color};
                font-family: "Manrope";
                font-size: 13px;
            }}
            QLineEdit:focus {{
                background: {bg_focus};
                border: 1px solid #818cf8;
            }}
            QLineEdit::placeholder {{
                color: {ph_color};
            }}
        """)
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
            elif item and item.text() == "Referrals":
                user = auth.get_user()
                if user and user.get("email"):
                    self._refresh_referral(user["email"])
            elif item and item.text() == "Files":
                self._refresh_files_status()

    def _on_trust_changed(self, level: int):
        settings_store.set("trust_level", level)
        if hasattr(self, "_trust_track") and self._trust_track is not None:
            self._trust_track.set_level(level)
        if hasattr(self, "_cap_rows_layout"):
            self._rebuild_cap_rows_animated(level)

    def _on_personality_changed(self):
        # Legacy — kept for safety; new path goes through _on_personality_mode_selected
        pass

    def _on_personality_mode_selected(self, mode: str):
        settings_store.set("personality_mode", mode)
        for m, card in self._mode_options.items():
            if isinstance(card, _PersonalityCard):
                card.set_selected(m == mode)
            else:
                card.set_active(m == mode)

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
            email = user.get("email", "")
            self.account_email_lbl.setText(email)
            if hasattr(self, "_profile_avatar") and isinstance(self._profile_avatar, _AvatarWidget):
                self._profile_avatar.set_initial(email[:1].upper() if email else "?")
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("…")
            providers = (user.get("app_metadata") or {}).get("providers") or []
            if hasattr(self, "link_google_btn"):
                self.link_google_btn.setVisible("google" not in providers)

        # If status was already fetched (e.g. by the startup refresh), show it
        # immediately so the UI isn't blank while the background call is in flight.
        cached = subscription.get_status()
        if cached.get("loaded"):
            self._update_account_ui(cached)

        # Always refresh in background to get the latest status.
        subscription.refresh_status(callback=_on_done)

        # Hook sync status updates
        from src.services.sync import memory_sync
        memory_sync.add_listener(lambda s: QTimer.singleShot(0, lambda: self._update_sync_ui(s)))
        self._update_sync_ui(memory_sync.get_state())

    def _update_account_ui(self, status: dict):
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

        if hasattr(self, "_usage_section"):
            self._usage_section.setVisible(not is_pro)
        else:
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
        if hasattr(self, "login_usage_bar"):
            self._login_usage_section.setVisible(not is_pro)
            if not is_pro:
                self.login_usage_bar.set_fraction(daily_usage, daily_limit)
                self.login_usage_lbl.setText(f"{daily_usage} / {daily_limit} requests today")

        if error:
            self._account_status(f"Could not refresh: {error}", error=True)
        else:
            self.account_status_lbl.setText("")

        # Refresh referral data whenever account state updates
        user = auth.get_user()
        if user and user.get("email"):
            self._refresh_referral(user["email"])

    # ── Referral ──────────────────────────────────────────────────────

    def _refresh_referral(self, email: str):
        """Resolve referral code for this user, creating one locally if needed."""
        import threading

        # Show cached code immediately while the network fetch runs
        cached_code = settings_store.get("referral_code", "")
        if cached_code:
            self._apply_referral_ui(cached_code, 0)

        def _fetch():
            user         = auth.get_user() or {}
            user_id      = user.get("id", "")
            access_token = auth.get_access_token() or ""

            # 1. Fast path: already stored in Omni app Supabase (has full stats)
            code, stats = self._fetch_referral_from_app_sb(user_id, access_token)
            if stats is None:
                # Network/auth error — don't fall through and risk corrupting server data;
                # the cached code was already shown by the fast-path above.
                return
            if code:
                settings_store.save_settings({"referral_code": code})
                self._dispatch.emit(lambda c=code, s=stats: self._apply_referral_ui(c, s))
                return

            # stats == {} means server responded with no row yet — safe to create one.

            # 2. User was on the website waitlist — sync their existing code
            ws_code, ws_count, _ = self._read_waitlist_code(email)
            if ws_code:
                stats = {"referral_count": ws_count, "confirmed_count": 0, "active_count": 0, "free_months_due": 0}
                self._save_referral_to_app_sb(user_id, access_token, ws_code, ws_count)
                settings_store.save_settings({"referral_code": ws_code})
                self._dispatch.emit(lambda c=ws_code, s=stats: self._apply_referral_ui(c, s))
                return

            # 3. Not on waitlist — generate a code locally, save to Omni app SB only
            new_code = self._generate_referral_code()
            stats = {"referral_count": 0, "confirmed_count": 0, "active_count": 0, "free_months_due": 0}
            self._save_referral_to_app_sb(user_id, access_token, new_code, 0)
            settings_store.save_settings({"referral_code": new_code})
            self._dispatch.emit(lambda c=new_code, s=stats: self._apply_referral_ui(c, s))

        threading.Thread(target=_fetch, daemon=True).start()

    def _read_waitlist_code(self, email: str):
        """Read-only lookup on website Supabase.

        Returns (own_code, count, referred_by_code) or (None, 0, None).
        referred_by_code is the referral code used when this user signed up —
        saved as used_referral_code so it's passed on checkout.
        """
        import urllib.request
        import urllib.parse
        import json as _json
        try:
            params = urllib.parse.urlencode({
                "email": f"eq.{email}",
                "select": "referral_code,referral_count,referred_by",
            })
            req = urllib.request.Request(
                f"{_WEBSITE_SB_URL}/rest/v1/waitlist?{params}",
                headers={
                    "apikey": _WEBSITE_SB_KEY,
                    "Authorization": f"Bearer {_WEBSITE_SB_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                rows = _json.loads(resp.read())
            if rows:
                row = rows[0]
                # If this user was referred and we haven't stored the code yet, save it now.
                referred_by = row.get("referred_by") or None
                if referred_by and not settings_store.get("used_referral_code"):
                    settings_store.set("used_referral_code", referred_by)
                if row.get("referral_code"):
                    return row["referral_code"], row.get("referral_count", 0), referred_by
        except Exception:
            pass
        return None, 0, None

    @staticmethod
    def _generate_referral_code() -> str:
        """Generate an 8-char referral code matching the website's format."""
        import hashlib, random
        return hashlib.md5(str(random.random()).encode()).hexdigest()[:8]

    def _fetch_referral_from_app_sb(self, user_id: str, access_token: str):
        """Read referral data from the Omni app's Supabase referral_codes table.

        Returns:
            (code, stats_dict) — row found
            (None, {})         — server responded but no row exists yet
            (None, None)       — network/parse error; caller should NOT fall through
        """
        import urllib.request
        import urllib.parse
        import json as _json
        if not user_id or not access_token:
            return None, None
        try:
            params = urllib.parse.urlencode({
                "user_id": f"eq.{user_id}",
                "select": "referral_code,referral_count,confirmed_count,active_count,free_months_due",
            })
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/referral_codes?{params}",
                headers={
                    "apikey": SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {access_token}",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                rows = _json.loads(resp.read())
            if rows:
                r = rows[0]
                return r.get("referral_code") or "", {
                    "referral_count":  r.get("referral_count",  0),
                    "confirmed_count": r.get("confirmed_count", 0),
                    "active_count":    r.get("active_count",    0),
                    "free_months_due": r.get("free_months_due", 0),
                }
            # Server responded OK — user just has no row yet
            return None, {}
        except Exception:
            return None, None

    def _save_referral_to_app_sb(self, user_id: str, access_token: str, code: str, count: int):
        """Upsert referral code into the Omni app's Supabase referral_codes table."""
        import urllib.request
        import json as _json
        from datetime import datetime, timezone
        if not user_id or not access_token:
            return
        try:
            payload = _json.dumps({
                "user_id": user_id,
                "referral_code": code,
                "referral_count": count,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }).encode()
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/referral_codes",
                data=payload,
                headers={
                    "apikey": SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
            )
            with urllib.request.urlopen(req, timeout=6):
                pass
        except Exception:
            pass

    def _apply_referral_ui(self, code: str, stats):
        if not hasattr(self, "_ref_link_edit"):
            return
        if isinstance(stats, int):
            # Legacy: plain count passed (cached from old settings_store entry)
            stats = {"confirmed_count": 0, "active_count": 0, "free_months_due": 0}

        confirmed = stats.get("confirmed_count", 0)
        active    = stats.get("active_count",    0)
        months    = stats.get("free_months_due", 0)

        link = f"https://heyomni.app?ref={code}"
        self._ref_link_edit.setText(link)
        self._ref_copy_btn.setEnabled(True)

        # Stat labels
        if hasattr(self, "_ref_stat_confirmed"):
            self._ref_stat_confirmed.setText(str(confirmed) if confirmed else "—")
        if hasattr(self, "_ref_stat_active"):
            self._ref_stat_active.setText(str(active) if active else "—")
        if hasattr(self, "_ref_stat_months"):
            self._ref_stat_months.setText("∞" if active >= 5 else (str(months) if months else "—"))

        # Milestone track
        if hasattr(self, "_milestone_track"):
            self._milestone_track.set_confirmed(confirmed)

        # Permanently free badge
        if hasattr(self, "_ref_free_badge"):
            self._ref_free_badge.setVisible(active >= 5)

    def _copy_referral_link(self):
        link = self._ref_link_edit.text()
        if link:
            QApplication.clipboard().setText(link)
            self._ref_copy_btn.setText("✓")
            QTimer.singleShot(1800, lambda: self._ref_copy_btn.setText("Copy ↗"))

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
                    referred_by=settings_store.get("used_referral_code") or None,
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
        pass  # security card is always visible

    def _show_secure_account_prompt(self):
        """Ensure the Account view is visible after auto-login from checkout."""
        if auth.is_logged_in() and self.account_stack.currentIndex() != 1:
            self.account_stack.setCurrentIndex(1)

    def _update_sync_ui(self, state: dict):
        s = state.get("state", "idle")
        last = state.get("last_synced")
        err  = state.get("error")
        if s == "syncing":
            self.sync_status_lbl.setText("Syncing…")
        elif s == "synced" and last:
            self.sync_status_lbl.setText(f"Last synced {last}")
        elif s == "error":
            self.sync_status_lbl.setText(err or "Error")
        else:
            self.sync_status_lbl.setText("—")

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

    def _do_forgot_password(self):
        email = self.auth_email_edit.text().strip()
        if not email:
            self._set_auth_error("Enter your email address first.")
            return
        self._set_auth_error("")
        self.forgot_pw_btn.setEnabled(False)
        self.forgot_pw_btn.setText("Sending…")

        import threading
        def _run():
            ok, msg = auth.send_password_reset(email)
            def _done():
                self.forgot_pw_btn.setEnabled(True)
                self.forgot_pw_btn.setText("Forgot password?")
                if ok:
                    self._set_auth_error("")
                    self._account_status(msg)
                else:
                    self._set_auth_error(msg)
            self._dispatch.emit(_done)
        threading.Thread(target=_run, daemon=True).start()

    def _set_auth_error(self, msg: str, success: bool = False):
        """Show a message inline in the login form, just below the buttons."""
        if not hasattr(self, "auth_error_lbl"):
            return
        if msg:
            self.auth_error_lbl.setText(msg)
            color = "#4ade80" if success else "#f87171"
            self.auth_error_lbl.setStyleSheet(f"color: {color}; background: transparent;")
            self.auth_error_lbl.setVisible(True)
        else:
            self.auth_error_lbl.setText("")
            self.auth_error_lbl.setVisible(False)

    def _do_sign_in(self):
        email = self.auth_email_edit.text().strip()
        pw    = self.auth_pass_edit.text()
        if not email or not pw:
            return
        self._set_auth_error("")
        self._set_auth_busy(True)

        import threading
        def _run():
            ok, msg = auth.sign_in(email, pw)
            def _done():
                self._set_auth_busy(False)
                if ok:
                    self.auth_pass_edit.clear()
                    self._set_auth_error("")
                    self.refresh_account()
                    subscription.refresh_status(callback=lambda s: self._dispatch.emit(lambda: self._update_account_ui(s)))
                else:
                    self._set_auth_error(msg)
            self._dispatch.emit(_done)
        threading.Thread(target=_run, daemon=True).start()

    def _do_sign_up(self):
        email = self.auth_email_edit.text().strip()
        pw    = self.auth_pass_edit.text()
        if not email or not pw:
            return
        self._set_auth_error("")
        self._set_auth_busy(True)

        import threading
        def _run():
            ok, msg = auth.sign_up(email, pw)
            def _done():
                self._set_auth_busy(False)
                if ok:
                    if auth.is_logged_in():
                        self._set_auth_error("")
                        self.refresh_account()
                    else:
                        # Email confirmation required — show the message in green
                        self._set_auth_error(msg, success=True)
                else:
                    self._set_auth_error(msg)
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

        field_bg       = "#2e2e3e" if dark else "#ebebf0"
        field_bg_focus = "#363648" if dark else "#e0e0e8"
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
        cap_div_col    = "rgba(255,255,255,0.06)" if dark else "rgba(0,0,0,0.05)"

        # Propagate theme to trust widgets
        if hasattr(self, "_trust_track") and self._trust_track is not None:
            self._trust_track.set_dark(dark)
        if hasattr(self, "_model_card"):
            self._model_card.set_dark(dark)
        self._apply_personality_toggle_theme(dark)
        if hasattr(self, "_fb_card"):
            self._fb_card.set_dark(dark)
        self._apply_fb_pill_theme(dark)
        # Rebuild cap rows to pick up new colors
        if hasattr(self, "_cap_rows_layout"):
            saved_level = settings_store.get("trust_level", 1)
            self._rebuild_cap_rows(saved_level)
        # Update _SettingsRow dark state
        if hasattr(self, "_profile_card"):
            self._profile_card.set_dark(dark)
        if hasattr(self, "_version_label"):
            self._version_label.setStyleSheet(
                f"color: {'rgba(255,255,255,0.25)' if dark else 'rgba(0,0,0,0.28)'}; background: transparent;"
            )
        if hasattr(self, "_check_update_btn"):
            self._check_update_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {'rgba(99,102,241,0.65)' if dark else 'rgba(79,70,229,0.7)'};
                    font-family: 'Manrope';
                    font-size: 10px;
                    background: transparent;
                    border: none;
                    padding: 0;
                    text-align: center;
                }}
                QPushButton:hover {{
                    color: {'#818cf8' if dark else '#4f46e5'};
                }}
                QPushButton:pressed {{
                    color: {'rgba(99,102,241,0.5)' if dark else 'rgba(79,70,229,0.5)'};
                }}
            """)
        if hasattr(self, "_restart_btn"):
            self._restart_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {'rgba(239,68,68,0.65)' if dark else 'rgba(220,38,38,0.7)'};
                    font-family: 'Manrope';
                    font-size: 10px;
                    background: transparent;
                    border: none;
                    padding: 0;
                    text-align: center;
                }}
                QPushButton:hover {{
                    color: {'#f87171' if dark else '#dc2626'};
                }}
                QPushButton:pressed {{
                    color: {'rgba(239,68,68,0.5)' if dark else 'rgba(220,38,38,0.5)'};
                }}
            """)
        if hasattr(self, "_profile_card"):
            self._profile_card.set_dark(dark)
        if hasattr(self, "_security_card"):
            self._security_card.set_dark(dark)
        if hasattr(self, "_auth_card"):
            self._auth_card.set_dark(dark)
        self._apply_fb_pill_theme(dark)

        # Apply to pages
        for name, page in self._pages.items():
            page.title_lbl.setStyleSheet(f"color: {primary};")
            if hasattr(page, "subtitle_lbl"):
                page.subtitle_lbl.setStyleSheet(f"color: {secondary}; background: transparent;")
            
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
            
            /* Sidebar footer */
            QWidget#SidebarFooter {{
                background: transparent;
            }}
            QFrame#SidebarFooterSep {{
                border: none;
                border-top: 1px solid {border};
                margin: 0;
            }}

            /* Content Area */
            QWidget#SettingsContent {{
                background: transparent;
            }}

            /* labels */
            QLabel {{ background: transparent; color: {primary}; }}
            QLabel#PageSubtitle {{
                color: {secondary};
            }}
            QLabel#SectionLabel {{
                color: {secondary};
            }}
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
            
            /* Feedback body text area */
            QTextEdit#FeedbackBody {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 8px 13px;
                color: {primary};
                font-family: "Manrope";
                font-size: 12px;
            }}
            QTextEdit#FeedbackBody:focus {{
                background: {field_bg_focus};
                border: 1px solid {sel_border};
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

            /* Capability rows */
            QWidget#CapRow {{ background: transparent; }}
            QFrame#CapRowDivider {{ background: {cap_div_col}; border: none; }}

            /* Settings rows */
            QWidget#SettingsRow {{ background: transparent; }}

            /* Avatar */
            QWidget#AvatarWidget {{ background: transparent; }}

            /* Plan card buttons */
            QPushButton#PlanChooseBtn {{
                background: #6366f1;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-family: "Manrope";
                font-size: 12px;
                font-weight: 600;
                padding: 0px 14px;
            }}
            QPushButton#PlanChooseBtn:hover {{ background: #818cf8; }}
            QPushButton#PlanChooseBtn:pressed {{ background: #4f46e5; }}
            QPushButton#PlanChooseBtn:disabled {{
                background: rgba(99,102,241,0.3);
                color: rgba(255,255,255,0.4);
            }}
        """)
        # Update usage bar dark mode
        if hasattr(self, "usage_bar"):
            self.usage_bar.set_dark(dark)

    def _on_check_update(self):
        btn = self._check_update_btn
        btn.setEnabled(False)
        btn.setText("Checking…")

        from PyQt6.QtCore import QThread

        class _Worker(QThread):
            done = pyqtSignal(object, object, object)

            def run(self):
                from src.core.updater import check_update
                try:
                    tag, url, body = check_update(APP_VERSION)
                except Exception:
                    tag, url, body = None, None, None
                self.done.emit(tag, url, body)

        worker = _Worker(self)

        def _on_done(tag, url, body):
            btn.setEnabled(True)
            btn.setText("Check for Updates")
            if tag:
                from src.ui.update_dialog import UpdateDialog
                dlg = UpdateDialog(APP_VERSION, tag, url, body, parent=self)
                dlg.exec()
            else:
                btn.setText("Up to date!")
                QTimer.singleShot(2500, lambda: btn.setText("Check for Updates"))
            worker.deleteLater()

        worker.done.connect(_on_done)
        worker.start()
        self._update_worker = worker  # prevent GC

    def _on_restart(self):
        import subprocess
        import signal

        self._restart_btn.setEnabled(False)
        self._restart_btn.setText("Restarting…")

        # Kill brain service
        subprocess.run(["pkill", "-f", "src/app/brain.py"], check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Kill voice listener
        subprocess.run(["pkill", "-f", "services/voice/listener.py"], check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Kill file watcher
        subprocess.run(["pkill", "-f", "services/search/watcher.py"], check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Relaunch the app
        python = sys.executable
        script = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "main.py"
        ))
        subprocess.Popen([python, script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Quit current process
        QTimer.singleShot(300, lambda: QApplication.instance().quit())
