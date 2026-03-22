"""Suggestion notification widget — slide-in notification for proactive suggestions.

Displays meeting prep cards, file suggestions, and other context-driven
notifications with Accept/Dismiss controls.
"""

import json
import logging
import os
import subprocess
import platform

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont


class SuggestionNotificationWidget(QWidget):
    """Slide-in notification widget for proactive suggestions.

    Shows a suggestion card with Accept/Dismiss buttons.
    Auto-dismisses after 15 seconds if no interaction.
    """

    dismissed = pyqtSignal(str)   # suggestion_id
    accepted = pyqtSignal(str)    # suggestion_id

    def __init__(self, suggestion: dict, parent=None):
        super().__init__(parent)
        self.suggestion = suggestion
        self.suggestion_id = suggestion.get("suggestion_id", "")
        self.suggestion_type = suggestion.get("type", "unknown")
        self.current_theme = "light"

        self.setObjectName("SuggestionNotification")
        self._build_ui()

        # Auto-dismiss after 15s
        self._auto_dismiss_timer = QTimer(self)
        self._auto_dismiss_timer.setSingleShot(True)
        self._auto_dismiss_timer.timeout.connect(self._on_dismiss)
        self._auto_dismiss_timer.start(15000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setObjectName("SuggestionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(8)

        if self.suggestion_type == "meeting_prep":
            self._build_meeting_prep(card_layout)
        elif self.suggestion_type == "file_suggestion":
            self._build_file_suggestion(card_layout)
        else:
            self._build_generic(card_layout)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setObjectName("SuggestionDismissBtn")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self._on_dismiss)

        accept_btn = QPushButton("Open")
        accept_btn.setObjectName("SuggestionAcceptBtn")
        accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        accept_btn.clicked.connect(self._on_accept)

        btn_layout.addWidget(dismiss_btn)
        btn_layout.addWidget(accept_btn)
        card_layout.addLayout(btn_layout)

        layout.addWidget(self.card)
        self.update_style()

    def _build_meeting_prep(self, layout: QVBoxLayout):
        event = self.suggestion.get("event", {})
        people = self.suggestion.get("people", [])
        files = self.suggestion.get("files", [])

        # Header
        header = QLabel("Upcoming Meeting")
        header.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        layout.addWidget(header)

        # Meeting title
        title = QLabel(event.get("name", "Meeting"))
        title.setFont(QFont("Instrument Serif", 22))
        title.setWordWrap(True)
        layout.addWidget(title)

        # Time
        start = event.get("start", "")
        if start:
            time_label = QLabel(f"Starts: {start}")
            time_label.setFont(QFont("Manrope", 12))
            layout.addWidget(time_label)

        # People
        if people:
            people_names = ", ".join(p.get("name", "") for p in people[:3])
            people_label = QLabel(f"With: {people_names}")
            people_label.setFont(QFont("Manrope", 12))
            people_label.setWordWrap(True)
            layout.addWidget(people_label)

        # Related files
        if files:
            files_header = QLabel("Related files:")
            files_header.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
            layout.addWidget(files_header)
            for f in files[:3]:
                fname = f.get("name", "")
                file_label = QLabel(f"  {fname}")
                file_label.setFont(QFont("Manrope", 11))
                file_label.setCursor(Qt.CursorShape.PointingHandCursor)
                fpath = f.get("path", "")
                if fpath:
                    file_label.mousePressEvent = lambda e, p=fpath: self._open_file(p)
                layout.addWidget(file_label)

    def _build_file_suggestion(self, layout: QVBoxLayout):
        context_file = self.suggestion.get("context_file", {})
        suggested = self.suggestion.get("suggested_files", [])

        header = QLabel("Related Files")
        header.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        layout.addWidget(header)

        ctx_name = context_file.get("name", "")
        desc = QLabel(f"While working on {ctx_name}, you might need:")
        desc.setFont(QFont("Manrope", 12))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        for f in suggested[:3]:
            fname = f.get("name", "")
            file_label = QLabel(f"  {fname}")
            file_label.setFont(QFont("Manrope", 11))
            file_label.setCursor(Qt.CursorShape.PointingHandCursor)
            fpath = f.get("path", "")
            if fpath:
                file_label.mousePressEvent = lambda e, p=fpath: self._open_file(p)
            layout.addWidget(file_label)

    def _build_generic(self, layout: QVBoxLayout):
        content = self.suggestion.get("content", {})
        if isinstance(content, str):
            label = QLabel(content)
        else:
            label = QLabel(json.dumps(content, indent=2))
        label.setFont(QFont("Manrope", 12))
        label.setWordWrap(True)
        layout.addWidget(label)

    def _on_dismiss(self):
        self._auto_dismiss_timer.stop()
        self.dismissed.emit(self.suggestion_id)
        self.hide()
        self.deleteLater()

    def _on_accept(self):
        self._auto_dismiss_timer.stop()
        self.accepted.emit(self.suggestion_id)

        # For meeting prep, open all related files
        if self.suggestion_type == "meeting_prep":
            for f in self.suggestion.get("files", [])[:3]:
                path = f.get("path", "")
                if path:
                    self._open_file(path)
        elif self.suggestion_type == "file_suggestion":
            for f in self.suggestion.get("suggested_files", [])[:3]:
                path = f.get("path", "")
                if path:
                    self._open_file(path)

        self.hide()
        self.deleteLater()

    @staticmethod
    def _open_file(path: str):
        """Open a file with the default application."""
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            logging.warning(f"Failed to open file {path}: {e}")

    def update_style(self):
        is_dark = self.current_theme == "dark"
        bg = "rgba(30, 30, 30, 0.95)" if is_dark else "rgba(255, 255, 255, 0.95)"
        text = "#FFFFFF" if is_dark else "#111111"
        secondary = "#AAAAAA" if is_dark else "#666666"
        btn_bg = "rgba(255, 255, 255, 0.15)" if is_dark else "rgba(0, 0, 0, 0.08)"
        accept_bg = "rgba(59, 130, 246, 0.9)"

        self.card.setStyleSheet(f"""
            QFrame#SuggestionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {'rgba(255,255,255,0.2)' if is_dark else 'rgba(0,0,0,0.1)'};
            }}
            QLabel {{
                color: {text};
                background: transparent;
            }}
            QPushButton#SuggestionDismissBtn {{
                background-color: {btn_bg};
                color: {secondary};
                border: none;
                border-radius: 8px;
                padding: 6px 16px;
                font-family: "Manrope";
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#SuggestionDismissBtn:hover {{
                background-color: {'rgba(255,255,255,0.25)' if is_dark else 'rgba(0,0,0,0.12)'};
            }}
            QPushButton#SuggestionAcceptBtn {{
                background-color: {accept_bg};
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 6px 16px;
                font-family: "Manrope";
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#SuggestionAcceptBtn:hover {{
                background-color: rgba(59, 130, 246, 1.0);
            }}
        """)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()
