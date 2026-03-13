"""Update available dialog for Omni."""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QBrush, QPainter, QPainterPath, QLinearGradient
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QWidget, QGraphicsDropShadowEffect,
)

ACCENT   = "#7C6EF5"
BG       = "#111114"
CARD_BG  = "#1A1A1E"
BORDER   = "#2A2A32"
TEXT_PRI = "#FFFFFF"
TEXT_SEC = "#9A9A9A"
GREEN    = "#4ade80"
RED      = "#f87171"


# ── Slim progress bar (reused from installer style) ───────────────────────────

class _ProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self._value = 0

    def set_value(self, v: int):
        self._value = max(0, min(100, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(255, 255, 255, 18)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), 4, 2, 2)
        fill_w = int(self.width() * self._value / 100)
        if fill_w > 0:
            grad = QLinearGradient(0, 0, fill_w, 0)
            grad.setColorAt(0, QColor(ACCENT))
            grad.setColorAt(1, QColor("#a89af8"))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 0, fill_w, 4, 2, 2)
        p.end()


# ── Background worker ─────────────────────────────────────────────────────────

class _ApplyWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok  = pyqtSignal()
    finished_err = pyqtSignal(str)

    def __init__(self, url: str, tag: str):
        super().__init__()
        self._url = url
        self._tag = tag

    def run(self):
        from src.core.updater import apply_update
        try:
            apply_update(self._url, self._tag, on_progress=lambda p, m: self.progress.emit(p, m))
            self.finished_ok.emit()
        except Exception as e:
            self.finished_err.emit(str(e))


# ── Dialog ────────────────────────────────────────────────────────────────────

class UpdateDialog(QDialog):
    """
    Dark-themed modal that informs the user a new Omni version is available.

    Usage:
        dlg = UpdateDialog(current_version, latest_tag, zipball_url, changelog)
        if dlg.exec():          # accepted → user clicked Update
            QApplication.quit()
    """

    def __init__(
        self,
        current: str,
        latest: str,
        zipball_url: str,
        changelog: str,
        parent=None,
    ):
        super().__init__(parent)
        self._url     = zipball_url
        self._tag     = latest
        self._worker  = None
        self._accepted_update = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(480)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 140))

        # Container
        self._container = QWidget(self)
        self._container.setObjectName("container")
        self._container.setGraphicsEffect(shadow)
        self._container.setStyleSheet(f"""
            QWidget#container {{
                background: {BG};
                border-radius: 18px;
                border: 1px solid {BORDER};
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)

        v = QVBoxLayout(self._container)
        v.setContentsMargins(28, 28, 28, 24)
        v.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        title = QLabel("Update Available")
        title.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {TEXT_PRI};")
        v.addWidget(title)
        v.addSpacing(6)

        ver_lbl = QLabel(f"Omni {current}  →  <b style='color:{ACCENT};'>{latest}</b>")
        ver_lbl.setStyleSheet(f"font-size: 14px; color: {TEXT_SEC};")
        ver_lbl.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(ver_lbl)
        v.addSpacing(20)

        # ── Changelog card ────────────────────────────────────────────────────
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        card_v = QVBoxLayout(card)
        card_v.setContentsMargins(14, 12, 14, 12)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(140)
        scroll.setStyleSheet("background: transparent;")

        notes_inner = QWidget()
        notes_inner.setStyleSheet("background: transparent;")
        notes_layout = QVBoxLayout(notes_inner)
        notes_layout.setContentsMargins(0, 0, 0, 0)

        notes_lbl = QLabel(changelog or "No release notes available.")
        notes_lbl.setWordWrap(True)
        notes_lbl.setStyleSheet(
            f"color: {TEXT_SEC}; font-size: 12px; font-family: 'Menlo', monospace;"
        )
        notes_layout.addWidget(notes_lbl)
        notes_layout.addStretch()
        scroll.setWidget(notes_inner)
        card_v.addWidget(scroll)
        v.addWidget(card)
        v.addSpacing(20)

        # ── Progress (hidden until update starts) ─────────────────────────────
        self._prog_bar = _ProgressBar()
        self._prog_bar.setVisible(False)
        v.addWidget(self._prog_bar)
        v.addSpacing(4)

        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 12px;")
        self._prog_lbl.setVisible(False)
        v.addWidget(self._prog_lbl)
        v.addSpacing(4)

        self._err_lbl = QLabel("")
        self._err_lbl.setStyleSheet(f"color: {RED}; font-size: 12px;")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setVisible(False)
        v.addWidget(self._err_lbl)
        v.addSpacing(16)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._later_btn = QPushButton("Remind Me Later")
        self._later_btn.setFixedHeight(38)
        self._later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_SEC};
                border: 1px solid {BORDER};
                border-radius: 10px;
                font-size: 13px;
                padding: 0 16px;
            }}
            QPushButton:hover {{ color: {TEXT_PRI}; border-color: rgba(255,255,255,0.25); }}
        """)
        self._later_btn.clicked.connect(self.reject)

        self._update_btn = QPushButton("Update Now")
        self._update_btn.setFixedHeight(38)
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENT};
                color: #fff;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 20px;
            }}
            QPushButton:hover {{ background: #9487f7; }}
            QPushButton:disabled {{ background: rgba(124,110,245,0.4); color: rgba(255,255,255,0.5); }}
        """)
        self._update_btn.clicked.connect(self._start_update)

        btn_row.addWidget(self._later_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._update_btn)
        v.addLayout(btn_row)

        self.adjustSize()

    # ── Update logic ──────────────────────────────────────────────────────────

    def _start_update(self):
        self._update_btn.setEnabled(False)
        self._later_btn.setEnabled(False)
        self._prog_bar.setVisible(True)
        self._prog_lbl.setVisible(True)
        self._err_lbl.setVisible(False)
        self.adjustSize()

        self._worker = _ApplyWorker(self._url, self._tag)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.finished_err.connect(self._on_err)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._prog_bar.set_value(pct)
        self._prog_lbl.setText(f"{pct}%  {msg}")

    def _on_ok(self):
        self._prog_bar.set_value(100)
        self._prog_lbl.setText("Update installed! Restart Omni to apply.")
        self._prog_lbl.setStyleSheet(f"color: {GREEN}; font-size: 12px;")
        self._accepted_update = True
        # Show a close button instead of quitting
        self._later_btn.setText("Close")
        self._later_btn.setEnabled(True)
        self._later_btn.setVisible(True)
        self._update_btn.setVisible(False)
        self.adjustSize()

    def _on_err(self, msg: str):
        self._err_lbl.setText(f"Update failed: {msg}")
        self._err_lbl.setVisible(True)
        self._prog_lbl.setVisible(False)
        self._prog_bar.setVisible(False)
        self._update_btn.setEnabled(True)
        self._later_btn.setEnabled(True)
        self.adjustSize()

    # ── Drag to move ──────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if hasattr(self, "_drag") and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 18, 18)
        p.fillPath(path, QBrush(QColor(BG)))
        p.end()
