"""
Clipboard item widget for the clipboard history list.
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from src.ui.styles import THEMES


class ClipboardItemWidget(QWidget):
    """Displays a single clipboard history entry with preview text and age."""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.current_theme = "dark"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(12)

        # Icon
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        # Text content
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.preview_label = QLabel(entry.preview)
        self.preview_label.setFont(QFont("Manrope", 14, QFont.Weight.Medium))
        self.preview_label.setWordWrap(False)
        text_layout.addWidget(self.preview_label)

        # Show char count for multi-line content
        lines = entry.text.count('\n') + 1
        chars = len(entry.text)
        if lines > 1:
            meta = f"{lines} lines · {chars} chars"
        else:
            meta = f"{chars} chars"

        self.meta_label = QLabel(meta)
        self.meta_label.setFont(QFont("Manrope", 11))
        text_layout.addWidget(self.meta_label)

        layout.addLayout(text_layout, 1)

        # Age label (right side)
        self.age_label = QLabel(entry.age_str())
        self.age_label.setFont(QFont("Manrope", 11))
        self.age_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.age_label)

        self._apply_theme()

    def _apply_theme(self):
        t = THEMES.get(self.current_theme, THEMES["dark"])
        self.setStyleSheet("background: transparent;")
        self.preview_label.setStyleSheet(f"color: {t['text_primary']};")
        self.meta_label.setStyleSheet(f"color: {t['text_secondary']};")
        self.age_label.setStyleSheet(f"color: {t['text_secondary']};")

        # Clipboard icon — simple text icon colored to theme
        icon_color = t['text_secondary']
        self.icon_label.setText("📋")
        self.icon_label.setStyleSheet(f"color: {icon_color}; font-size: 16px;")

    def set_theme(self, theme):
        self.current_theme = theme
        self._apply_theme()

    def refresh_age(self):
        """Update the age label (call periodically if needed)."""
        self.age_label.setText(self.entry.age_str())
