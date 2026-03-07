"""
src/ui/onboarding.py — Beautiful step-by-step onboarding wizard.
Shown once on first launch; marks 'onboarding_shown' in settings when closed.
Inspired by the Omni website's feature sections with inline video demos.
"""
from __future__ import annotations

import math
import os
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QApplication, QGraphicsOpacityEffect, QSizePolicy,
    QLineEdit, QFrame,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QUrl, QRectF, QSize,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QLinearGradient, QRadialGradient,
    QBrush, QPen, QPainterPath, QPixmap,
)

from src.core.config import PROJECT_ROOT, LOGO_PATH

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _HAS_VIDEO = True
except Exception:
    _HAS_VIDEO = False
    logging.debug("[onboarding] QtMultimedia unavailable — using placeholders")

# ── Dimensions ─────────────────────────────────────────────────────────────────
_W, _H = 860, 540

# ── Step definitions ───────────────────────────────────────────────────────────
STEPS = [
    {
        "kind": "welcome",
        "title": "Welcome to Omni",
        "subtitle": "Find files, control your Mac, get answers, manage email — just ask.",
    },
    {
        "kind": "feature",
        "badge": "01",
        "title": "Does the work\nfor you",
        "body": "Organize your Downloads, send that email, compress a folder. Omni doesn't just find things — it gets them done.",
        "video": "organize-my-downloads.mov",
    },
    {
        "kind": "feature",
        "badge": "02",
        "title": "Knows your computer\ninside out",
        "body": "How many battery cycles? Is your model still getting security updates? Real answers, instantly, in plain English.",
        "video": "how-many-charge-cycles-i-have.mp4",
    },
    {
        "kind": "feature",
        "badge": "03",
        "title": "Control with\nnatural language",
        "body": "Switch to light mode, archive a folder, tweak any setting. No preferences pane to dig through, no menus to click.",
        "video": "change-theme-to-light.mov",
    },
    {
        "kind": "feature",
        "badge": "04",
        "title": "Instant answers,\nno Googling",
        "body": "Currency conversions, unit math, quick calculations — handled in milliseconds, without breaking focus to open a browser tab.",
        "video": "54usd-to-eur.mov",
    },
    {
        "kind": "feature",
        "badge": "05",
        "title": "Settings &\npreferences",
        "body": "Right-click the Omni logo in the menu bar to open Settings — adjust trust level, AI model, account, and more.",
        "video": "how-to-open-settings-visual-guide.mov",
    },
    {
        "kind": "privacy",
        "badge": "06",
        "title": "Private\nby design",
        "body": "Your files never leave your device — we never upload them. No tracking. Yours alone.",
    },
    {
        "kind": "paywall",
        "title": "Choose your plan",
    },
    {
        "kind": "login",
        "title": "Sign in",
    },
    {
        "kind": "finish",
        "title": "You're all set!",
        "body": "Just describe what you need — Omni will take care of the rest.",
    },
]


# ── Path helper ────────────────────────────────────────────────────────────────
def _video_path(filename: str) -> Optional[str]:
    """Resolve a video filename from OmniApp/assets/videos."""
    p = os.path.join(PROJECT_ROOT, "assets", "videos", filename)
    return p if os.path.exists(p) else None


# ── Sub-widgets ────────────────────────────────────────────────────────────────

class _StepDots(QWidget):
    """Animated pill/dot step indicator."""

    def __init__(self, n: int, parent=None):
        super().__init__(parent)
        self._n = n
        self._active = 0
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_active(self, i: int):
        self._active = i
        self.update()

    def sizeHint(self):
        # active pill=22, inactive dot=8, gap=8
        return QSize(self._n * 8 + (self._n - 1) * 8 + 14, 10)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = 0
        for i in range(self._n):
            if i == self._active:
                w = 22
                gr = QLinearGradient(x, 0, x + w, 0)
                gr.setColorAt(0.0, QColor("#8B5CF6"))
                gr.setColorAt(1.0, QColor("#A78BFA"))
                p.setBrush(QBrush(gr))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(x, 1, w, 8, 4, 4)
                x += w + 8
            else:
                p.setBrush(QBrush(QColor(255, 255, 255, 40)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(x, 1, 8, 8, 4, 4)
                x += 8 + 8
        p.end()


class _VideoWidget(QWidget):
    """Looping muted video player, or animated placeholder when video unavailable."""

    def __init__(self, video_file: str, parent=None):
        super().__init__(parent)
        self._path = _video_path(video_file) if video_file else None
        self._player: Optional[QMediaPlayer] = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        if _HAS_VIDEO and self._path:
            self._vw = QVideoWidget()
            self._vw.setStyleSheet("background: #000; border-radius: 14px;")
            lay.addWidget(self._vw)

            self._ao = QAudioOutput()
            self._ao.setVolume(0.0)
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._ao)
            self._player.setVideoOutput(self._vw)
            self._player.setSource(QUrl.fromLocalFile(self._path))
            self._player.mediaStatusChanged.connect(self._on_status)
        else:
            lay.addWidget(_VideoPlaceholder(video_file))

    def _on_status(self, status):
        if self._player and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    def play(self):
        if self._player:
            self._player.play()

    def stop(self):
        if self._player:
            self._player.pause()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14, 14)
        p.setClipPath(path)
        super().paintEvent(ev)
        p.end()


class _VideoPlaceholder(QWidget):
    """Animated shimmer placeholder shown when a video file is not found."""

    def __init__(self, filename: str = "", parent=None):
        super().__init__(parent)
        name = filename.replace("-2", "").replace("-", " ").rsplit(".", 1)[0].title()
        self._label = name or "Demo"
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self):
        self._phase = (self._phase + 0.018) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 14, 14)
        p.setClipPath(path)

        p.fillRect(0, 0, w, h, QColor(16, 16, 26))

        # Moving glow blob
        cx = w * (0.5 + 0.22 * math.sin(self._phase))
        cy = h * (0.5 + 0.12 * math.cos(self._phase * 0.75))
        grad = QRadialGradient(cx, cy, max(w, h) * 0.5)
        grad.setColorAt(0, QColor(139, 92, 246, 42))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)

        # Border
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 16), 1))
        p.drawPath(path)

        # Label
        p.setPen(QColor(255, 255, 255, 55))
        p.setFont(QFont("Manrope", 11))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"▶  {self._label}")
        p.end()


class _PrivacyVisual(QWidget):
    """Animated rings + shield visual for the privacy step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self):
        self._phase = (self._phase + 0.010) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ph = self._phase

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 14, 14)
        p.setClipPath(path)
        p.fillRect(0, 0, w, h, QColor(11, 18, 13))

        cx, cy = w / 2, h / 2 - 18

        # Concentric rings
        for r, a in [(132, 9), (94, 16), (58, 26)]:
            pulse = a + int(6 * math.sin(ph + r * 0.01))
            p.setPen(QPen(QColor(52, 199, 89, pulse), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(int(cx - r), int(cy - r), r * 2, r * 2)

        # Orbiting particles
        for orb_r, sz, speed, offset in [(94, 4.5, 1.0, 0.0), (132, 3.0, -0.6, 2.1)]:
            angle = ph * speed + offset
            dx = cx + orb_r * math.cos(angle)
            dy = cy + orb_r * math.sin(angle)
            p.setBrush(QBrush(QColor(52, 199, 89, 170)))
            p.setPen(Qt.PenStyle.NoPen)
            s = int(sz)
            p.drawEllipse(int(dx - s), int(dy - s), s * 2, s * 2)

        # Center disc
        sr = 44
        cg = QRadialGradient(cx, cy - 6, sr)
        cg.setColorAt(0, QColor(26, 40, 28))
        cg.setColorAt(1, QColor(14, 22, 16))
        p.setBrush(QBrush(cg))
        p.setPen(QPen(QColor(52, 199, 89, 50), 1))
        p.drawEllipse(int(cx - sr), int(cy - sr), sr * 2, sr * 2)

        # Shield icon
        pen = QPen(QColor(52, 199, 89, 210), 2.2,
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        shield = QPainterPath()
        shield.moveTo(cx, cy - 20)
        shield.lineTo(cx + 15, cy - 12)
        shield.lineTo(cx + 15, cy + 5)
        shield.quadTo(cx + 15, cy + 18, cx, cy + 24)
        shield.quadTo(cx - 15, cy + 18, cx - 15, cy + 5)
        shield.lineTo(cx - 15, cy - 12)
        shield.closeSubpath()
        p.drawPath(shield)

        # Checkmark
        ck = QPainterPath()
        ck.moveTo(cx - 7, cy + 2)
        ck.lineTo(cx - 1, cy + 8)
        ck.lineTo(cx + 9, cy - 5)
        p.drawPath(ck)

        # "Personal / Private / Secure" pills
        labels = ["Personal", "Private", "Secure"]
        lw, lh, lgap = 70, 20, 6
        total = len(labels) * lw + (len(labels) - 1) * lgap
        lx = cx - total / 2
        ly = cy + 76
        p.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
        for lbl in labels:
            pill = QPainterPath()
            pill.addRoundedRect(QRectF(lx, ly, lw, lh), 10, 10)
            p.setBrush(QBrush(QColor(52, 199, 89, 18)))
            p.setPen(QPen(QColor(52, 199, 89, 40), 0.8))
            p.drawPath(pill)
            p.setPen(QColor(52, 199, 89, 150))
            p.drawText(int(lx), int(ly), int(lw), int(lh), Qt.AlignmentFlag.AlignCenter, lbl)
            lx += lw + lgap

        p.end()


class _ToggleSwitch(QWidget):
    """iOS-style pill toggle with smooth thumb animation."""
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = True
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = 24
        self._thumb_x = float(44 - h + 2)  # start at "on" position
        self._target_x = self._thumb_x
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(12)
        self._anim_timer.timeout.connect(self._tick)

    def _tick(self):
        diff = self._target_x - self._thumb_x
        if abs(diff) < 0.5:
            self._thumb_x = self._target_x
            self._anim_timer.stop()
        else:
            self._thumb_x += diff * 0.25
        self.update()

    def mousePressEvent(self, _):
        self._on = not self._on
        h = self.height()
        self._target_x = float(self.width() - h + 2) if self._on else 2.0
        self._anim_timer.start()
        self.toggled.emit(self._on)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Interpolate track colour based on thumb position
        t = (self._thumb_x - 2) / max(w - h, 1)
        t = max(0.0, min(1.0, t))
        alpha = int(30 + t * 170)
        track = QColor(139, 92, 246, alpha)
        p.setBrush(QBrush(track))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, h // 2, h // 2)
        p.setBrush(QBrush(QColor(255, 255, 255, 230)))
        p.drawEllipse(int(self._thumb_x), 2, h - 4, h - 4)
        p.end()


# ── Main dialog ────────────────────────────────────────────────────────────────

class OnboardingDialog(QDialog):
    """Step-by-step onboarding wizard dialog."""

    _dispatch        = pyqtSignal(object)
    _checkout_ready  = pyqtSignal(str)
    _checkout_error  = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._step = 0
        self._total = len(STEPS)
        self._panels: list[Optional[_VideoWidget]] = []
        self._fade_anim: Optional[QPropertyAnimation] = None
        self._drag_pos = None
        self._chose_pro = False

        self._dispatch.connect(lambda fn: fn())
        self._checkout_ready.connect(self._on_checkout_ready)
        self._checkout_error.connect(self._on_checkout_error)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(_W, _H)

        self._build_ui()
        self._go_to(0, animate=False)
        self._center()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._card = QWidget()
        self._card.setObjectName("OnboardingCard")
        self._card.setStyleSheet("""
            QWidget#OnboardingCard {
                background: #0d0d14;
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.08);
            }
        """)
        root.addWidget(self._card)

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        # Pages
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        for i, step in enumerate(STEPS):
            self._stack.addWidget(self._build_page(step, i))
        card_lay.addWidget(self._stack, 1)

        # Nav bar
        card_lay.addWidget(self._build_nav())

        # Opacity effect for fade transitions
        self._effect = QGraphicsOpacityEffect()
        self._effect.setOpacity(1.0)
        self._stack.setGraphicsEffect(self._effect)

    def _build_page(self, step: dict, idx: int) -> QWidget:
        kind = step["kind"]
        if kind == "welcome":
            return self._welcome_page(step)
        elif kind == "feature":
            return self._feature_page(step)
        elif kind == "privacy":
            return self._privacy_page(step)
        elif kind == "paywall":
            return self._paywall_page(step)
        elif kind == "login":
            return self._login_page(step)
        else:
            return self._finish_page(step)

    def _welcome_page(self, step: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(80, 0, 80, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo
        logo = QLabel()
        pix = QPixmap(LOGO_PATH)
        if not pix.isNull():
            logo.setPixmap(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("background: transparent;")

        title = QLabel(step["title"])
        title.setFont(QFont("Instrument Serif", 40, QFont.Weight.Normal))
        title.setStyleSheet("color: #F4F4F5; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel(step["subtitle"])
        subtitle.setFont(QFont("Manrope", 15, QFont.Weight.Light))
        subtitle.setStyleSheet("color: rgba(255,255,255,0.45); background: transparent;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)

        lay.addStretch(2)
        lay.addWidget(logo)
        lay.addSpacing(22)
        lay.addWidget(title)
        lay.addSpacing(12)
        lay.addWidget(subtitle)
        lay.addStretch(3)

        self._panels.append(None)
        return w

    def _feature_page(self, step: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(48, 44, 32, 28)
        lay.setSpacing(36)

        # Left: text
        left = self._text_column(step, accent="#A78BFA", accent_bg="rgba(139,92,246,0.14)",
                                 accent_border="rgba(139,92,246,0.28)")
        left.setFixedWidth(295)

        # Right: video
        vid = _VideoWidget(step.get("video", ""))
        vid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._panels.append(vid)

        lay.addWidget(left)
        lay.addWidget(vid)
        return w

    def _privacy_page(self, step: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(48, 44, 32, 28)
        lay.setSpacing(36)

        left = self._text_column(step, accent="rgba(52,199,89,0.90)", accent_bg="rgba(52,199,89,0.12)",
                                 accent_border="rgba(52,199,89,0.25)")
        left.setFixedWidth(295)

        vis = _PrivacyVisual()
        vis.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._panels.append(None)
        lay.addWidget(left)
        lay.addWidget(vis)
        return w

    def _paywall_page(self, step: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(48, 32, 48, 20)
        lay.setSpacing(0)

        # Header
        title = QLabel("Choose your plan")
        title.setFont(QFont("Instrument Serif", 30, QFont.Weight.Normal))
        title.setStyleSheet("color: #F4F4F5; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Start free, upgrade anytime.")
        sub.setFont(QFont("Manrope", 13, QFont.Weight.Light))
        sub.setStyleSheet("color: rgba(255,255,255,0.40); background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(title)
        lay.addSpacing(4)
        lay.addWidget(sub)
        lay.addSpacing(20)

        # Plan cards row — stretch so both cards fill the remaining height
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)

        _card_ss_free = """
            QWidget#PlanCard {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 16px;
            }
        """
        _card_ss_pro = """
            QWidget#PlanCard {
                background: rgba(99,102,241,0.11);
                border: 1px solid rgba(139,92,246,0.50);
                border-radius: 16px;
            }
        """

        def _feat_row_free(text):
            row = QHBoxLayout(); row.setSpacing(8); row.setContentsMargins(0, 0, 0, 0)
            dot = QLabel("·"); dot.setStyleSheet("color: rgba(255,255,255,0.28); background: transparent; border: none;")
            dot.setFont(QFont("Manrope", 16)); dot.setFixedWidth(12)
            lbl = QLabel(text); lbl.setFont(QFont("Manrope", 12, QFont.Weight.Light))
            lbl.setStyleSheet("color: rgba(255,255,255,0.52); background: transparent; border: none;")
            row.addWidget(dot); row.addWidget(lbl); row.addStretch()
            return row

        def _feat_row_pro(text):
            row = QHBoxLayout(); row.setSpacing(10); row.setContentsMargins(0, 0, 0, 0)
            chk = QLabel("✓"); chk.setStyleSheet("color: #A78BFA; background: transparent; border: none;")
            chk.setFont(QFont("Manrope", 12)); chk.setFixedWidth(16)
            lbl = QLabel(text); lbl.setFont(QFont("Manrope", 12, QFont.Weight.Light))
            lbl.setStyleSheet("color: rgba(255,255,255,0.72); background: transparent; border: none;")
            row.addWidget(chk); row.addWidget(lbl); row.addStretch()
            return row

        # ── Free card ─────────────────────────────────────────────────
        free_card = QWidget(); free_card.setObjectName("PlanCard")
        free_card.setStyleSheet(_card_ss_free)
        fc = QVBoxLayout(free_card)
        fc.setContentsMargins(24, 22, 24, 22)
        fc.setSpacing(0)

        fc_name = QLabel("Free")
        fc_name.setFont(QFont("Manrope", 16, QFont.Weight.Bold))
        fc_name.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
        fc.addWidget(fc_name)
        fc.addSpacing(6)

        fc_price = QLabel("$0")
        fc_price.setFont(QFont("Manrope", 28, QFont.Weight.Bold))
        fc_price.setStyleSheet("color: rgba(255,255,255,0.55); background: transparent; border: none;")
        fc_price_sub = QLabel("per month")
        fc_price_sub.setFont(QFont("Manrope", 11, QFont.Weight.Light))
        fc_price_sub.setStyleSheet("color: rgba(255,255,255,0.28); background: transparent; border: none;")
        fc.addWidget(fc_price)
        fc.addWidget(fc_price_sub)
        fc.addSpacing(18)

        for feat in ["10 AI queries per day", "File & semantic search", "App control & automation", "All core features"]:
            fc.addLayout(_feat_row_free(feat))
            fc.addSpacing(7)

        fc.addStretch()  # pushes button to bottom

        free_btn = QPushButton("Continue for free  →")
        free_btn.setFont(QFont("Manrope", 12, QFont.Weight.Medium))
        free_btn.setFixedHeight(42)
        free_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        free_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,0.55);
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
            }
            QPushButton:hover {
                color: rgba(255,255,255,0.88);
                background: rgba(255,255,255,0.10);
            }
        """)
        free_btn.clicked.connect(self._on_choose_free)
        fc.addWidget(free_btn)

        # ── Pro card ──────────────────────────────────────────────────
        pro_card = QWidget(); pro_card.setObjectName("PlanCard")
        pro_card.setStyleSheet(_card_ss_pro)
        pc = QVBoxLayout(pro_card)
        pc.setContentsMargins(24, 22, 24, 22)
        pc.setSpacing(0)

        pro_header = QHBoxLayout(); pro_header.setSpacing(8); pro_header.setContentsMargins(0, 0, 0, 0)
        pc_name = QLabel("Pro")
        pc_name.setFont(QFont("Manrope", 16, QFont.Weight.Bold))
        pc_name.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
        badge = QLabel("✦ Most popular")
        badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        badge.setStyleSheet("color: #A78BFA; background: rgba(139,92,246,0.22); border: 1px solid rgba(139,92,246,0.38); border-radius: 8px; padding: 2px 8px;")
        pro_header.addWidget(pc_name); pro_header.addWidget(badge); pro_header.addStretch()
        pc.addLayout(pro_header)
        pc.addSpacing(6)

        # Price — big, updates with toggle
        self._paywall_price_lbl = QLabel("$6")
        self._paywall_price_lbl.setFont(QFont("Manrope", 28, QFont.Weight.Bold))
        self._paywall_price_lbl.setStyleSheet("color: #A78BFA; background: transparent; border: none;")
        self._paywall_price_sub = QLabel("per month · billed $72/yr")
        self._paywall_price_sub.setFont(QFont("Manrope", 11, QFont.Weight.Light))
        self._paywall_price_sub.setStyleSheet("color: rgba(167,139,250,0.55); background: transparent; border: none;")
        pc.addWidget(self._paywall_price_lbl)
        pc.addWidget(self._paywall_price_sub)
        pc.addSpacing(18)

        for feat in ["Unlimited AI queries", "Memory sync across devices", "Cloud vector embeddings", "Priority support"]:
            pc.addLayout(_feat_row_pro(feat))
            pc.addSpacing(7)

        pc.addStretch()  # pushes toggle + button to bottom

        # ── Toggle: Monthly  [switch]  Yearly  [33% off] ──────────────
        self._interval = "yearly"

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        toggle_row.setContentsMargins(0, 0, 0, 0)

        _lbl_ss_active   = "color: rgba(255,255,255,0.85); background: transparent; border: none;"
        _lbl_ss_inactive = "color: rgba(255,255,255,0.30); background: transparent; border: none;"

        self._lbl_monthly = QLabel("Monthly")
        self._lbl_monthly.setFont(QFont("Manrope", 11))

        self._toggle_sw = _ToggleSwitch()  # True = yearly

        self._lbl_yearly = QLabel("Yearly")
        self._lbl_yearly.setFont(QFont("Manrope", 11, QFont.Weight.DemiBold))

        self._save_badge = QLabel("33% off")
        self._save_badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self._save_badge.setStyleSheet(
            "color: #A78BFA; background: rgba(139,92,246,0.22); border: 1px solid rgba(139,92,246,0.38); border-radius: 7px; padding: 2px 7px;"
        )

        toggle_row.addWidget(self._lbl_monthly)
        toggle_row.addWidget(self._toggle_sw)
        toggle_row.addWidget(self._lbl_yearly)
        toggle_row.addSpacing(4)
        toggle_row.addWidget(self._save_badge)
        toggle_row.addStretch()

        def _select_interval(yearly: bool):
            self._interval = "yearly" if yearly else "monthly"
            self._lbl_monthly.setStyleSheet(_lbl_ss_inactive if yearly else _lbl_ss_active)
            self._lbl_yearly.setStyleSheet(_lbl_ss_active if yearly else _lbl_ss_inactive)
            self._save_badge.setVisible(yearly)
            if yearly:
                self._paywall_price_lbl.setText("$6")
                self._paywall_price_sub.setText("per month · billed $72/yr")
            else:
                self._paywall_price_lbl.setText("$9")
                self._paywall_price_sub.setText("per month · billed monthly")

        self._toggle_sw.toggled.connect(_select_interval)
        _select_interval(True)  # default: yearly

        # ── Referral code input ───────────────────────────────────────
        self._referral_code_edit = QLineEdit()
        self._referral_code_edit.setPlaceholderText("Referral code (optional — get 1 month free)")
        self._referral_code_edit.setFixedHeight(30)
        self._referral_code_edit.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                color: rgba(255,255,255,0.65);
                font-family: Manrope; font-size: 10px;
                padding: 0 10px;
            }
            QLineEdit:focus { border-color: rgba(167,139,250,0.40); }
        """)
        pc.addWidget(self._referral_code_edit)
        pc.addSpacing(8)

        pc.addLayout(toggle_row)
        pc.addSpacing(10)

        self._go_pro_btn = QPushButton("Go Pro  →")
        self._go_pro_btn.setFont(QFont("Manrope", 13, QFont.Weight.DemiBold))
        self._go_pro_btn.setFixedHeight(42)
        self._go_pro_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._go_pro_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7C3AED, stop:1 #A855F7);
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6D28D9, stop:1 #9333EA);
            }
            QPushButton:disabled { background: rgba(139,92,246,0.30); }
        """)
        self._go_pro_btn.clicked.connect(self._on_choose_pro)
        pc.addWidget(self._go_pro_btn)

        self._paywall_status = QLabel("")
        self._paywall_status.setFont(QFont("Manrope", 9, QFont.Weight.Light))
        self._paywall_status.setStyleSheet("color: rgba(255,255,255,0.38); background: transparent; border: none;")
        self._paywall_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._paywall_status.setWordWrap(True)
        self._paywall_status.setMaximumHeight(0)  # hidden until needed
        pc.addWidget(self._paywall_status)

        cards_row.addWidget(free_card)
        cards_row.addWidget(pro_card)
        lay.addLayout(cards_row, 1)  # stretch=1 so cards fill remaining height

        self._panels.append(None)
        return w

    def _login_page(self, step: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(220, 32, 220, 20)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("Sign in to your account")
        title.setFont(QFont("Instrument Serif", 26, QFont.Weight.Normal))
        title.setStyleSheet("color: #F4F4F5; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._login_sub = QLabel("Optional — required to activate Pro.")
        self._login_sub.setFont(QFont("Manrope", 12, QFont.Weight.Light))
        self._login_sub.setStyleSheet("color: rgba(255,255,255,0.38); background: transparent;")
        self._login_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._login_sub.setWordWrap(True)

        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(self._login_sub)
        lay.addSpacing(22)

        # Email + password
        self._login_email = QLineEdit()
        self._login_email.setPlaceholderText("Email")
        self._login_email.setFixedHeight(40)
        self._login_email.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
                color: #F4F4F5;
                font-family: Manrope;
                font-size: 13px;
                padding: 0 14px;
            }
            QLineEdit:focus { border-color: rgba(139,92,246,0.60); }
        """)

        self._login_pass = QLineEdit()
        self._login_pass.setPlaceholderText("Password")
        self._login_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._login_pass.setFixedHeight(40)
        self._login_pass.setStyleSheet(self._login_email.styleSheet())
        self._login_pass.returnPressed.connect(self._do_login_sign_in)

        lay.addWidget(self._login_email)
        lay.addSpacing(8)
        lay.addWidget(self._login_pass)
        lay.addSpacing(8)

        # Status label — right below fields, hidden until needed
        self._login_status = QLabel("")
        self._login_status.setFont(QFont("Manrope", 11, QFont.Weight.Medium))
        self._login_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._login_status.setWordWrap(True)
        self._login_status.setVisible(False)
        self._login_status.setFixedHeight(32)
        self._login_status.setStyleSheet("""
            color: #FF6B6B;
            background: rgba(255,80,80,0.12);
            border: 1px solid rgba(255,80,80,0.25);
            border-radius: 8px;
            padding: 0 10px;
        """)
        lay.addWidget(self._login_status)
        lay.addSpacing(10)

        # Sign in / Sign up row
        btn_row = QHBoxLayout(); btn_row.setSpacing(8); btn_row.setContentsMargins(0, 0, 0, 0)

        self._signin_btn = QPushButton("Sign In")
        self._signin_btn.setFixedHeight(38)
        self._signin_btn.setFont(QFont("Manrope", 12, QFont.Weight.Medium))
        self._signin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._signin_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7C3AED, stop:1 #A855F7);
                border: none; border-radius: 10px;
            }
            QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6D28D9, stop:1 #9333EA); }
        """)
        self._signin_btn.clicked.connect(self._do_login_sign_in)

        self._signup_btn = QPushButton("Sign Up")
        self._signup_btn.setFixedHeight(38)
        self._signup_btn.setFont(QFont("Manrope", 12, QFont.Weight.Medium))
        self._signup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._signup_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,0.65);
                background: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
            }
            QPushButton:hover { color: rgba(255,255,255,0.90); background: rgba(255,255,255,0.11); }
        """)
        self._signup_btn.clicked.connect(self._do_login_sign_up)

        btn_row.addWidget(self._signin_btn)
        btn_row.addWidget(self._signup_btn)
        lay.addLayout(btn_row)
        lay.addSpacing(14)

        # Divider
        sep_row = QHBoxLayout(); sep_row.setSpacing(8); sep_row.setContentsMargins(0, 0, 0, 0)
        for _ in range(2):
            line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("border: none; border-top: 1px solid rgba(255,255,255,0.10);")
        sep_l = QFrame(); sep_l.setFrameShape(QFrame.Shape.HLine)
        sep_l.setStyleSheet("border: none; border-top: 1px solid rgba(255,255,255,0.10);")
        sep_r = QFrame(); sep_r.setFrameShape(QFrame.Shape.HLine)
        sep_r.setStyleSheet("border: none; border-top: 1px solid rgba(255,255,255,0.10);")
        sep_lbl = QLabel("or")
        sep_lbl.setFont(QFont("Manrope", 10)); sep_lbl.setFixedWidth(24)
        sep_lbl.setStyleSheet("color: rgba(255,255,255,0.25); background: transparent;")
        sep_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep_row.addWidget(sep_l); sep_row.addWidget(sep_lbl); sep_row.addWidget(sep_r)
        lay.addLayout(sep_row)
        lay.addSpacing(12)

        def _oauth_btn(text):
            b = QPushButton(text)
            b.setFixedHeight(38)
            b.setFont(QFont("Manrope", 12, QFont.Weight.Light))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    color: rgba(255,255,255,0.70);
                    background: rgba(255,255,255,0.05);
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 10px;
                }
                QPushButton:hover { color: rgba(255,255,255,0.92); background: rgba(255,255,255,0.09); }
            """)
            return b

        self._google_btn = _oauth_btn("Continue with Google")
        self._google_btn.clicked.connect(lambda: self._do_login_oauth("google"))
        self._github_btn = _oauth_btn("Continue with GitHub")
        self._github_btn.clicked.connect(lambda: self._do_login_oauth("github"))
        self._github_btn.setVisible(False)

        lay.addWidget(self._google_btn)

        self._panels.append(None)
        return w

    def _finish_page(self, step: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(80, 0, 80, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Checkmark circle
        check_lbl = QLabel("✓")
        check_lbl.setFont(QFont("Manrope", 32))
        check_lbl.setStyleSheet("""
            color: #34C759;
            background: rgba(52,199,89,0.12);
            border: 1px solid rgba(52,199,89,0.25);
            border-radius: 36px;
            min-width: 72px;
            max-width: 72px;
            min-height: 72px;
            max-height: 72px;
        """)
        check_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(step["title"])
        title.setFont(QFont("Instrument Serif", 38, QFont.Weight.Normal))
        title.setStyleSheet("color: #F4F4F5; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Hotkey badge
        hotkey_card = QWidget()
        hotkey_card.setStyleSheet("""
            background: rgba(139,92,246,0.10);
            border: 1px solid rgba(139,92,246,0.22);
            border-radius: 14px;
        """)
        hk_lay = QHBoxLayout(hotkey_card)
        hk_lay.setContentsMargins(24, 14, 24, 14)
        hk_lay.setSpacing(10)
        hk_lay.addStretch()

        def _key(text: str) -> QLabel:
            k = QLabel(text)
            k.setFont(QFont("Manrope", 16, QFont.Weight.Medium))
            k.setStyleSheet("""
                color: rgba(255,255,255,0.88);
                background: rgba(255,255,255,0.10);
                border: 1px solid rgba(255,255,255,0.16);
                border-radius: 8px;
                padding: 4px 12px;
            """)
            k.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return k

        plus = QLabel("+")
        plus.setStyleSheet("color: rgba(255,255,255,0.35); background: transparent;")
        plus.setFont(QFont("Manrope", 15))

        hk_desc = QLabel("  Open Omni anytime")
        hk_desc.setFont(QFont("Manrope", 13, QFont.Weight.Light))
        hk_desc.setStyleSheet("color: rgba(255,255,255,0.50); background: transparent;")

        hk_lay.addWidget(_key("⌘"))
        hk_lay.addWidget(plus)
        hk_lay.addWidget(_key("⌥"))
        hk_lay.addWidget(hk_desc)
        hk_lay.addStretch()

        body = QLabel(step["body"])
        body.setFont(QFont("Manrope", 13, QFont.Weight.Light))
        body.setStyleSheet("color: rgba(255,255,255,0.38); background: transparent;")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)

        lay.addStretch(2)
        lay.addWidget(check_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(16)
        lay.addWidget(title)
        lay.addSpacing(28)
        lay.addWidget(hotkey_card)
        lay.addSpacing(18)
        lay.addWidget(body)
        lay.addStretch(3)

        self._panels.append(None)
        return w

    def _text_column(self, step: dict, accent: str, accent_bg: str, accent_border: str) -> QWidget:
        col = QWidget()
        col.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        badge = QLabel(f"  {step.get('badge', '')}  ")
        badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        badge.setStyleSheet(f"""
            color: {accent};
            background: {accent_bg};
            border: 1px solid {accent_border};
            border-radius: 10px;
            letter-spacing: 2px;
            padding: 2px 0px;
        """)
        badge.setFixedHeight(24)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        badge.adjustSize()

        title = QLabel(step.get("title", ""))
        title.setFont(QFont("Instrument Serif", 30, QFont.Weight.Normal))
        title.setStyleSheet("color: #F4F4F5; background: transparent;")
        title.setWordWrap(True)

        body = QLabel(step.get("body", ""))
        body.setFont(QFont("Manrope", 13, QFont.Weight.Light))
        body.setStyleSheet("color: rgba(255,255,255,0.50); background: transparent;")
        body.setWordWrap(True)

        lay.addWidget(badge)
        lay.addSpacing(16)
        lay.addWidget(title)
        lay.addSpacing(16)
        lay.addWidget(body)
        lay.addStretch()
        return col

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setFixedHeight(68)
        nav.setStyleSheet("""
            background: rgba(255,255,255,0.028);
            border-top: 1px solid rgba(255,255,255,0.07);
            border-bottom-left-radius: 20px;
            border-bottom-right-radius: 20px;
        """)
        lay = QHBoxLayout(nav)
        lay.setContentsMargins(32, 0, 32, 0)

        self._dots = _StepDots(self._total)
        lay.addWidget(self._dots)
        lay.addStretch()

        # Skip
        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setFont(QFont("Manrope", 12, QFont.Weight.Light))
        self._skip_btn.setStyleSheet("""
            QPushButton { color: rgba(255,255,255,0.30); background: transparent; border: none; padding: 8px 14px; }
            QPushButton:hover { color: rgba(255,255,255,0.55); }
        """)
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)

        # Back
        self._back_btn = QPushButton("← Back")
        self._back_btn.setFont(QFont("Manrope", 12, QFont.Weight.Medium))
        self._back_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255,255,255,0.45);
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 10px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                color: rgba(255,255,255,0.75);
                background: rgba(255,255,255,0.10);
            }
        """)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self._on_back)

        # Continue / Done
        self._cont_btn = QPushButton("Get Started  →")
        self._cont_btn.setFont(QFont("Manrope", 13, QFont.Weight.DemiBold))
        self._cont_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7C3AED, stop:1 #A855F7);
                border: none;
                border-radius: 10px;
                padding: 10px 28px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6D28D9, stop:1 #9333EA);
            }
        """)
        self._cont_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cont_btn.setFixedHeight(42)
        self._cont_btn.clicked.connect(self._on_continue)

        lay.addWidget(self._skip_btn)
        lay.addSpacing(6)
        lay.addWidget(self._back_btn)
        lay.addSpacing(8)
        lay.addWidget(self._cont_btn)
        return nav

    # ── Navigation logic ───────────────────────────────────────────────────────

    def _on_continue(self):
        kind = STEPS[self._step]["kind"]
        if kind == "paywall":
            return  # handled by embedded plan buttons
        if self._step < self._total - 1:
            self._go_to(self._step + 1)
        else:
            self._finish()

    def _on_back(self):
        if self._step > 0:
            self._go_to(self._step - 1)

    def _on_skip(self):
        # Jump to the paywall step instead of closing
        paywall_idx = next(
            (i for i, s in enumerate(STEPS) if s["kind"] == "paywall"), None
        )
        if paywall_idx is not None and self._step < paywall_idx:
            self._go_to(paywall_idx)
        else:
            self._finish()

    def _finish(self):
        for panel in self._panels:
            if panel:
                panel.stop()
        self.accept()

    def _go_to(self, idx: int, animate: bool = True):
        # Stop current video
        cur = self._panels[self._step] if self._step < len(self._panels) else None
        if cur:
            cur.stop()

        def _switch():
            self._step = idx
            self._stack.setCurrentIndex(idx)
            self._dots.set_active(idx)
            self._update_nav(idx)
            new = self._panels[idx] if idx < len(self._panels) else None
            if new:
                new.play()

        if animate:
            out = QPropertyAnimation(self._effect, b"opacity")
            out.setDuration(110)
            out.setStartValue(1.0)
            out.setEndValue(0.0)
            out.setEasingCurve(QEasingCurve.Type.OutQuad)

            def _on_out_done():
                _switch()
                inn = QPropertyAnimation(self._effect, b"opacity")
                inn.setDuration(160)
                inn.setStartValue(0.0)
                inn.setEndValue(1.0)
                inn.setEasingCurve(QEasingCurve.Type.InQuad)
                inn.start()
                self._fade_anim = inn

            out.finished.connect(_on_out_done)
            out.start()
            self._fade_anim = out
        else:
            _switch()

    def _update_nav(self, idx: int):
        is_last  = idx == self._total - 1
        is_first = idx == 0
        kind = STEPS[idx]["kind"]

        self._back_btn.setVisible(idx > 0)
        self._skip_btn.setVisible(0 < idx < self._total - 1 and kind not in ("paywall", "login"))

        if kind == "paywall":
            self._cont_btn.setVisible(False)
        elif kind == "login":
            self._cont_btn.setVisible(True)
            self._cont_btn.setText("Skip for now  →")
        elif is_last:
            self._cont_btn.setVisible(True)
            self._cont_btn.setText("Start using Omni  ✓")
        elif is_first:
            self._cont_btn.setVisible(True)
            self._cont_btn.setText("Get Started  →")
        else:
            self._cont_btn.setVisible(True)
            self._cont_btn.setText("Continue  →")

    # ── Paywall actions ────────────────────────────────────────────────────────

    def _go_to_kind(self, kind: str):
        for i, s in enumerate(STEPS):
            if s["kind"] == kind:
                self._go_to(i)
                return

    def _on_choose_free(self):
        self._chose_pro = False
        self._go_to_kind("login")

    def _on_choose_pro(self):
        self._chose_pro = True
        self._go_pro_btn.setEnabled(False)
        self._go_pro_btn.setText("Opening checkout…")
        self._paywall_status.setText("")

        import threading, src.core.auth as auth, src.core.billing as billing
        import src.core.settings_store as settings_store

        token = auth.get_access_token()
        user  = auth.get_user() or {}
        email = user.get("email") or None
        interval = getattr(self, "_interval", "yearly")
        referred_by = settings_store.get("used_referral_code") or None
        # Also pick up any code typed into the referral input on the paywall
        if hasattr(self, "_referral_code_edit"):
            typed = self._referral_code_edit.text().strip()
            if typed:
                referred_by = typed
                settings_store.set("used_referral_code", typed)

        def _run():
            import webbrowser
            try:
                url = billing.create_pro_checkout_url(
                    interval=interval,
                    access_token=token,
                    customer_email=email,
                    referred_by=referred_by,
                )
                self._checkout_ready.emit(url)
                webbrowser.open(url)
            except Exception as e:
                self._checkout_error.emit(f"Could not open checkout: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _on_checkout_ready(self, url: str):
        self._go_pro_btn.setEnabled(True)
        self._go_pro_btn.setText("Go Pro  →")
        # Advance to login step with a helpful message
        self._go_to_kind("login")
        self._login_sub.setText("After paying, sign in with your checkout email to activate Pro.")
        self._login_status.setText("")

    def _on_checkout_error(self, msg: str):
        self._go_pro_btn.setEnabled(True)
        self._go_pro_btn.setText("Go Pro  →")
        self._paywall_status.setMaximumHeight(16777215)
        self._paywall_status.setText(msg)

    # ── Login actions ──────────────────────────────────────────────────────────

    def _set_login_status(self, msg: str, error: bool = True):
        if msg:
            self._login_status.setText(msg)
            self._login_status.setVisible(True)
            if error:
                self._login_status.setStyleSheet("""
                    color: #FF6B6B;
                    background: rgba(255,80,80,0.12);
                    border: 1px solid rgba(255,80,80,0.25);
                    border-radius: 8px;
                    padding: 0 10px;
                """)
            else:
                self._login_status.setStyleSheet("""
                    color: rgba(255,255,255,0.55);
                    background: rgba(255,255,255,0.06);
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 8px;
                    padding: 0 10px;
                """)
        else:
            self._login_status.setVisible(False)

    def _set_login_busy(self, busy: bool):
        for b in (self._signin_btn, self._signup_btn, self._google_btn, self._github_btn):
            b.setEnabled(not busy)
        self._cont_btn.setEnabled(not busy)

    def _login_advance(self):
        self._go_to_kind("finish")
        # Auto-detect referred_by from the website waitlist in the background.
        # This ensures used_referral_code is populated before the user hits "Go Pro".
        if not settings_store.get("used_referral_code"):
            import threading as _t
            def _fetch_referred_by():
                try:
                    import urllib.request, urllib.parse, json as _json
                    user = auth.get_user() or {}
                    email = user.get("email") or ""
                    if not email:
                        return
                    params = urllib.parse.urlencode({
                        "email": f"eq.{email}",
                        "select": "referred_by",
                    })
                    req = urllib.request.Request(
                        f"https://rfirkagyggkumbeqzxgf.supabase.co/rest/v1/waitlist?{params}",
                        headers={
                            "apikey": (
                                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
                                ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJmaXJrYWd5Z2drdW1iZXF6eGdmIiwic"
                                "m9sZSI6ImFub24iLCJpYXQiOjE3NjcyOTk0NTUsImV4cCI6MjA4Mjg3NTQ1NX0"
                                ".n_cf788aLOsS27S-7XJ7phRH2csrs6MnxgF8dSeAWSE"
                            ),
                            "Authorization": (
                                "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
                                ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJmaXJrYWd5Z2drdW1iZXF6eGdmIiwic"
                                "m9sZSI6ImFub24iLCJpYXQiOjE3NjcyOTk0NTUsImV4cCI6MjA4Mjg3NTQ1NX0"
                                ".n_cf788aLOsS27S-7XJ7phRH2csrs6MnxgF8dSeAWSE"
                            ),
                        },
                    )
                    with urllib.request.urlopen(req, timeout=6) as r:
                        rows = _json.loads(r.read())
                    if rows:
                        code = rows[0].get("referred_by") or ""
                        if code:
                            settings_store.set("used_referral_code", code)
                except Exception:
                    pass
            _t.Thread(target=_fetch_referred_by, daemon=True).start()

    def _do_login_sign_in(self):
        import threading, src.core.auth as auth
        email = self._login_email.text().strip()
        pw    = self._login_pass.text()
        if not email or not pw:
            self._set_login_status("Please enter your email and password.")
            return
        self._set_login_busy(True)
        self._set_login_status("Signing in…", error=False)

        def _run():
            ok, msg = auth.sign_in(email, pw)
            def _done():
                self._set_login_busy(False)
                if ok:
                    self._login_pass.clear()
                    self._login_advance()
                else:
                    self._set_login_status(msg)
            self._dispatch.emit(_done)
        threading.Thread(target=_run, daemon=True).start()

    def _do_login_sign_up(self):
        import threading, src.core.auth as auth
        email = self._login_email.text().strip()
        pw    = self._login_pass.text()
        if not email or not pw:
            self._set_login_status("Please enter your email and password.")
            return
        self._set_login_busy(True)
        self._set_login_status("Creating account…", error=False)

        def _run():
            ok, msg = auth.sign_up(email, pw)
            def _done():
                self._set_login_busy(False)
                self._set_login_status(msg, error=not ok)
                if ok and auth.is_logged_in():
                    self._login_advance()
            self._dispatch.emit(_done)
        threading.Thread(target=_run, daemon=True).start()

    def _do_login_oauth(self, provider: str):
        import src.core.auth as auth
        self._set_login_busy(True)
        self._set_login_status(f"Opening browser for {provider} sign-in…", error=False)

        def _on_done(ok, msg):
            def _finish():
                self._set_login_busy(False)
                if ok:
                    self._login_advance()
                else:
                    self._set_login_status(msg)
            self._dispatch.emit(_finish)

        auth.start_oauth(provider, _on_done)

    # ── Background ─────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 20, 20)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor("#0d0d14"))
        p.end()

    # ── Drag to move ───────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # ── Centering ──────────────────────────────────────────────────────────────

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move(sg.center() - self.rect().center())
