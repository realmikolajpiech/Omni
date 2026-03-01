"""
TrustPermissionPopup — overlay shown when the AI requests an action
that exceeds the current trust level.

Usage:
    popup = TrustPermissionPopup(
        required_level=2,
        description="click "Submit" at (924, 680)",
        theme="dark",
        parent=self.frame,
    )
    popup.allowed.connect(lambda: ...)
    popup.open_settings.connect(lambda: ...)
    popup.show_animated()
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPropertyAnimation, QEasingCurve,
    QRect, QParallelAnimationGroup, QTimer, QRectF
)
from PyQt6.QtGui import (
    QPainter, QColor, QPainterPath, QLinearGradient, QBrush, QPen,
    QFont, QFontMetrics
)

# ── Level metadata ────────────────────────────────────────────────────────────

_LEVEL_META = {
    2: {
        "icon":    "⌨",
        "label":   "Automation",
        "heading": "Automation required",
    },
    3: {
        "icon":    "⚡",
        "label":   "Full Control",
        "heading": "Full Control required",
    },
}

_LEVEL_NAMES = {1: "Assistant", 2: "Automation", 3: "Full Control"}

# Gradient colours matching GradientBorderFrame
_GRAD = ["#2E5CB8", "#6A0DAD", "#D92E87", "#FF8533"]


# ── Inner card widget ─────────────────────────────────────────────────────────

class _PopupCard(QWidget):
    """Glass card with animated gradient border."""

    def __init__(self, required_level: int, description: str, theme: str, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self._theme = theme
        self._level = required_level
        meta = _LEVEL_META.get(required_level, _LEVEL_META[2])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(0)

        # ── Icon + heading ────────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        icon_lbl = QLabel(meta["icon"])
        icon_lbl.setFont(QFont("", 20))
        icon_lbl.setStyleSheet("background: transparent; color: #C084FC;")
        icon_lbl.setFixedSize(28, 28)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        heading_lbl = QLabel(meta["heading"])
        heading_lbl.setFont(QFont("Instrument Serif", 16))
        tc = "#FFFFFF" if theme == "dark" else "#111111"
        heading_lbl.setStyleSheet(f"background: transparent; color: {tc};")

        top_row.addWidget(icon_lbl)
        top_row.addWidget(heading_lbl)
        top_row.addStretch()
        layout.addLayout(top_row)
        layout.addSpacing(10)

        # ── Description ───────────────────────────────────────────────────
        sc = "#AAAAAA" if theme == "dark" else "#666666"
        desc_lbl = QLabel(f"Omni wants to {description}")
        desc_lbl.setFont(QFont("Manrope", 10))
        desc_lbl.setStyleSheet(f"background: transparent; color: {sc};")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)
        layout.addSpacing(18)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.allow_btn = QPushButton("Allow once")
        self.allow_btn.setObjectName("TrustAllowBtn")
        self.allow_btn.setFixedHeight(36)
        self.allow_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("TrustCancelBtn")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        btn_row.addWidget(self.allow_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)
        layout.addSpacing(14)

        # ── Footer link ───────────────────────────────────────────────────
        self.settings_link = QLabel(
            f"Always allow  →  set Trust to <b>{meta['label']}</b> in Settings"
        )
        self.settings_link.setFont(QFont("Manrope", 9))
        self.settings_link.setStyleSheet(
            f"background: transparent; color: {'#888888' if theme == 'dark' else '#999999'};"
        )
        self.settings_link.setWordWrap(True)
        self.settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.settings_link)

        self.adjustSize()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = QRectF(self.rect())

        # Background fill
        if self._theme == "dark":
            bg = QColor(14, 12, 18, 235)
        else:
            bg = QColor(248, 246, 252, 240)

        bg_path = QPainterPath()
        bg_path.addRoundedRect(r, 18, 18)
        painter.fillPath(bg_path, bg)

        # Gradient border
        grad = QLinearGradient(0, 0, self.width(), self.height())
        stops = [(i / (len(_GRAD) - 1), QColor(c)) for i, c in enumerate(_GRAD)]
        for pos, col in stops:
            grad.setColorAt(pos, col)

        border_pen = QPen(QBrush(grad), 1.5)
        painter.setPen(border_pen)
        border_path = QPainterPath()
        border_path.addRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 17.5, 17.5)
        painter.drawPath(border_path)


# ── Main overlay ─────────────────────────────────────────────────────────────

class TrustPermissionPopup(QWidget):
    """
    Semi-transparent overlay displayed over the main window when an action
    exceeds the current trust level.

    Signals
    -------
    allowed       : user clicked "Allow once"
    denied        : user cancelled
    open_settings : user clicked the "Always allow" footer link
    """
    allowed       = pyqtSignal()
    denied        = pyqtSignal()
    open_settings = pyqtSignal()

    def __init__(self, required_level: int, description: str,
                 theme: str = "dark", parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        self._theme = theme
        self._opacity = 0.0

        # Build card
        self._card = _PopupCard(required_level, description, theme, self)
        self._card.allow_btn.clicked.connect(self._on_allow)
        self._card.cancel_btn.clicked.connect(self._on_deny)
        self._card.settings_link.mousePressEvent = lambda _: self._on_open_settings()

        # Style buttons via inline stylesheet (theme-aware)
        if theme == "dark":
            self._card.allow_btn.setStyleSheet("""
                QPushButton#TrustAllowBtn {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #5B21B6, stop:1 #D92E87);
                    color: #FFFFFF;
                    border: none;
                    border-radius: 10px;
                    font-family: Manrope;
                    font-size: 11px;
                    font-weight: 700;
                    padding: 0 16px;
                }
                QPushButton#TrustAllowBtn:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #6D28D9, stop:1 #E8409E);
                }
                QPushButton#TrustAllowBtn:pressed { opacity: 0.8; }
            """)
            self._card.cancel_btn.setStyleSheet("""
                QPushButton#TrustCancelBtn {
                    background: rgba(255, 255, 255, 0.09);
                    color: #CCCCCC;
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 10px;
                    font-family: Manrope;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 16px;
                }
                QPushButton#TrustCancelBtn:hover {
                    background: rgba(255, 255, 255, 0.14);
                }
            """)
        else:
            self._card.allow_btn.setStyleSheet("""
                QPushButton#TrustAllowBtn {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #5B21B6, stop:1 #D92E87);
                    color: #FFFFFF;
                    border: none;
                    border-radius: 10px;
                    font-family: Manrope;
                    font-size: 11px;
                    font-weight: 700;
                    padding: 0 16px;
                }
                QPushButton#TrustAllowBtn:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #6D28D9, stop:1 #E8409E);
                }
            """)
            self._card.cancel_btn.setStyleSheet("""
                QPushButton#TrustCancelBtn {
                    background: rgba(0, 0, 0, 0.07);
                    color: #555555;
                    border: 1px solid rgba(0, 0, 0, 0.15);
                    border-radius: 10px;
                    font-family: Manrope;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 16px;
                }
                QPushButton#TrustCancelBtn:hover {
                    background: rgba(0, 0, 0, 0.11);
                }
            """)

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _refit(self):
        """Resize overlay to cover parent and center card."""
        if self.parent():
            self.setGeometry(self.parent().rect())
        self._center_card()

    def _center_card(self):
        if not self._card:
            return
        cx = (self.width()  - self._card.width())  // 2
        cy = (self.height() - self._card.height()) // 2
        self._card.move(cx, cy)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_card()

    # ── Show / hide ───────────────────────────────────────────────────────────

    def show_animated(self):
        self._refit()
        self.raise_()
        self.show()

        card_w = self._card.width()
        card_h = self._card.height()
        cx = (self.width()  - card_w)  // 2
        cy = (self.height() - card_h) // 2

        # Opacity: fade in overlay
        self._opacity = 0.0
        self._fade_anim = QPropertyAnimation(self, b"_opacity_prop")
        self._fade_anim.setDuration(160)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        # Scale: card geometry from 90% → 100%
        shrink = 0.10
        dw = int(card_w * shrink / 2)
        dh = int(card_h * shrink / 2)
        start_rect = QRect(cx + dw, cy + dh, card_w - dw * 2, card_h - dh * 2)
        end_rect   = QRect(cx, cy, card_w, card_h)

        self._scale_anim = QPropertyAnimation(self._card, b"geometry")
        self._scale_anim.setDuration(340)
        self._scale_anim.setStartValue(start_rect)
        self._scale_anim.setEndValue(end_rect)
        self._scale_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        self._group = QParallelAnimationGroup()
        self._group.addAnimation(self._fade_anim)
        self._group.addAnimation(self._scale_anim)
        self._group.start()

    def _hide_animated(self, callback=None):
        self._fade_out = QPropertyAnimation(self, b"_opacity_prop")
        self._fade_out.setDuration(130)
        self._fade_out.setStartValue(self._opacity)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InQuad)
        if callback:
            self._fade_out.finished.connect(callback)
        self._fade_out.finished.connect(self.hide)
        self._fade_out.start()

    # ── Qt property for opacity animation ────────────────────────────────────

    def get_opacity(self):
        return self._opacity

    def set_opacity(self, val):
        self._opacity = val
        self.update()

    from PyQt6.QtCore import pyqtProperty as _pyqtProperty
    _opacity_prop = _pyqtProperty(float, fget=get_opacity, fset=set_opacity)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = int(self._opacity * 160)  # max 160/255 ≈ 63%
        painter.fillRect(self.rect(), QColor(0, 0, 0, alpha))

    # ── Input ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        # Click outside card → deny
        if not self._card.geometry().contains(event.pos()):
            self._on_deny()

    def keyPressEvent(self, event):
        from PyQt6.QtCore import Qt as _Qt
        if event.key() == _Qt.Key.Key_Escape:
            self._on_deny()
        else:
            super().keyPressEvent(event)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_allow(self):
        self._hide_animated(lambda: self.allowed.emit())

    def _on_deny(self):
        self._hide_animated(lambda: self.denied.emit())

    def _on_open_settings(self):
        self._hide_animated(lambda: self.open_settings.emit())
