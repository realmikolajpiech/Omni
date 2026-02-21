from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QListWidget, QListWidgetItem, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush, QLinearGradient
import math


class AnimatedProgressBar(QWidget):
    """Custom animated progress bar with smooth gradient and pulse effect."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._indeterminate = True
        self._phase = 0.0
        self._success = False
        self._failure = False
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60fps

    def _tick(self):
        self._phase = (self._phase + 0.03) % (2 * math.pi)
        self.update()

    def set_value(self, val):
        self._value = max(0, min(100, val))
        self._indeterminate = False
        self.update()

    def set_success(self):
        self._success = True
        self._failure = False
        self._value = 100
        self._indeterminate = False
        self._timer.stop()
        self.update()

    def set_failure(self):
        self._failure = True
        self._success = False
        self._indeterminate = False
        self._timer.stop()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2

        # Track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawRoundedRect(0, 0, w, h, r, r)

        if self._success:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor("#34C759"))
            grad.setColorAt(1, QColor("#30D158"))
            p.setBrush(grad)
            p.drawRoundedRect(0, 0, w, h, r, r)
        elif self._failure:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor("#FF453A"))
            grad.setColorAt(1, QColor("#FF3B30"))
            p.setBrush(grad)
            p.drawRoundedRect(0, 0, w, h, r, r)
        elif self._indeterminate:
            # Shimmer sweep
            phase = self._phase
            center = (math.sin(phase) * 0.5 + 0.5) * w
            half = w * 0.35
            start = center - half
            end = center + half
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(max(0.0, (start - 20) / w), QColor(100, 130, 255, 0))
            grad.setColorAt(max(0.0, min(1.0, start / w)), QColor("#667EEA"))
            grad.setColorAt(max(0.0, min(1.0, center / w)), QColor("#A78BFA"))
            grad.setColorAt(max(0.0, min(1.0, end / w)), QColor("#667EEA"))
            grad.setColorAt(min(1.0, (end + 20) / w), QColor(100, 130, 255, 0))
            p.setBrush(grad)
            p.drawRoundedRect(0, 0, w, h, r, r)
        else:
            fill_w = int(w * self._value / 100)
            if fill_w > 0:
                grad = QLinearGradient(0, 0, fill_w, 0)
                grad.setColorAt(0, QColor("#667EEA"))
                grad.setColorAt(0.5, QColor("#A78BFA"))
                grad.setColorAt(1, QColor("#764BA2"))
                p.setBrush(grad)
                p.drawRoundedRect(0, 0, fill_w, h, r, r)

        p.end()


class InstallProgressWidget(QWidget):
    candidate_confirmed = pyqtSignal(object)

    def __init__(self, app_name, theme="dark", parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.current_theme = theme
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("InstallCard")

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        # ── Header row ──────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        self.icon_label = QLabel("📦")
        self.icon_label.setFont(QFont("Manrope", 22))
        self.icon_label.setStyleSheet("background: transparent;")
        self.icon_label.setFixedWidth(36)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        display_name = app_name.replace('-', ' ').title()

        self.title_label = QLabel(f"Installing {display_name}")
        self.title_label.setFont(QFont("Instrument Serif", 20, QFont.Weight.Normal))
        self.title_label.setStyleSheet("background: transparent;")

        self.status_label = QLabel("Preparing…")
        self.status_label.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        self.status_label.setStyleSheet("background: transparent; letter-spacing: 0.2px;")

        title_col.addWidget(self.title_label)
        title_col.addWidget(self.status_label)

        header_row.addWidget(self.icon_label)
        header_row.addLayout(title_col)
        header_row.addStretch()

        # ── Progress bar ─────────────────────────────────────────
        self.progress_bar = AnimatedProgressBar()

        # ── Candidate list (hidden unless multiple packages found) ─
        self.list_widget = QListWidget()
        self.list_widget.hide()
        self.list_widget.setFixedHeight(150)
        self.list_widget.itemClicked.connect(self.on_candidate_selected)

        card_layout.addLayout(header_row)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.progress_bar)

        card_layout.addWidget(self.list_widget)

        layout.addWidget(self.card)
        self._apply_theme()

    # ── Theme ────────────────────────────────────────────────────
    def _apply_theme(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "rgba(255,255,255,0.07)"
            border = "rgba(255,255,255,0.12)"
            title_c = "#FFFFFF"
            status_c = "rgba(255,255,255,0.55)"
        else:
            bg = "rgba(0,0,0,0.04)"
            border = "rgba(0,0,0,0.10)"
            title_c = "#111111"
            status_c = "#666666"

        self.card.setStyleSheet(f"""
            QWidget#InstallCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        self.title_label.setStyleSheet(f"color: {title_c}; background: transparent;")
        self.status_label.setStyleSheet(f"color: {status_c}; background: transparent; letter-spacing: 0.2px;")

    def set_theme(self, theme):
        self.current_theme = theme
        self._apply_theme()

    # ── Public API ───────────────────────────────────────────────
    def update_status(self, text):
        self.status_label.setText(text)

    def add_log(self, text):
        # Silently ignored — no log view shown to user
        pass

    def update_progress(self, val):
        self.progress_bar.set_value(val)

    def set_finished(self, success, message):
        self._finished = True
        display_name = self.app_name.replace('-', ' ').title()
        if success:
            self.progress_bar.set_success()
            self.icon_label.setText("✅")
            self.title_label.setText(f"Installed {display_name}")
            self.status_label.setText("Done!")
        else:
            self.progress_bar.set_failure()
            self.icon_label.setText("❌")
            self.title_label.setText("Installation failed")
            self.status_label.setText(message[:80] if message else "Something went wrong.")

        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def show_candidates(self, candidates):
        self.list_widget.clear()
        self.status_label.setText(f"Multiple packages found — select one:")
        self.list_widget.show()
        for c in candidates:
            details = c.get('description', c.get('name', ''))[:60]
            txt = f"{c.get('display_name', c.get('name'))} — {details}"
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.list_widget.addItem(item)
        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def on_candidate_selected(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        self.candidate_confirmed.emit(data)

    def sizeHint(self):
        return QSize(660, 110)


class UninstallProgressWidget(QWidget):
    """Progress widget for uninstallation — mirrors InstallProgressWidget but styled in red/orange."""

    def __init__(self, app_name, theme="dark", parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.current_theme = theme
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("UninstallCard")

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        # ── Header row ──────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        self.icon_label = QLabel("🗑️")
        self.icon_label.setFont(QFont("Manrope", 22))
        self.icon_label.setStyleSheet("background: transparent;")
        self.icon_label.setFixedWidth(36)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        display_name = app_name.replace('-', ' ').title()

        self.title_label = QLabel(f"Uninstalling {display_name}")
        self.title_label.setFont(QFont("Instrument Serif", 20, QFont.Weight.Normal))
        self.title_label.setStyleSheet("background: transparent;")

        self.status_label = QLabel("Preparing…")
        self.status_label.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        self.status_label.setStyleSheet("background: transparent; letter-spacing: 0.2px;")

        title_col.addWidget(self.title_label)
        title_col.addWidget(self.status_label)

        header_row.addWidget(self.icon_label)
        header_row.addLayout(title_col)
        header_row.addStretch()

        # ── Progress bar (red-tinted animated bar) ─────────────
        self.progress_bar = _UninstallProgressBar()

        card_layout.addLayout(header_row)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.progress_bar)

        layout.addWidget(self.card)
        self._apply_theme()

    # ── Theme ────────────────────────────────────────────────────
    def _apply_theme(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "rgba(255,255,255,0.07)"
            border = "rgba(255,80,80,0.18)"
            title_c = "#FFFFFF"
            status_c = "rgba(255,255,255,0.55)"
        else:
            bg = "rgba(255,60,60,0.04)"
            border = "rgba(255,60,60,0.15)"
            title_c = "#111111"
            status_c = "#666666"

        self.card.setStyleSheet(f"""
            QWidget#UninstallCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        self.title_label.setStyleSheet(f"color: {title_c}; background: transparent;")
        self.status_label.setStyleSheet(f"color: {status_c}; background: transparent; letter-spacing: 0.2px;")

    def set_theme(self, theme):
        self.current_theme = theme
        self._apply_theme()

    # ── Public API ───────────────────────────────────────────────
    def update_status(self, text):
        self.status_label.setText(text)

    def add_log(self, text):
        pass  # Silent — no log view shown to user

    def update_progress(self, val):
        self.progress_bar.set_value(val)

    def set_finished(self, success, message):
        self._finished = True
        display_name = self.app_name.replace('-', ' ').title()
        if success:
            self.progress_bar.set_success()
            self.icon_label.setText("✅")
            self.title_label.setText(f"Uninstalled {display_name}")
            self.status_label.setText("Done!")
        else:
            self.progress_bar.set_failure()
            self.icon_label.setText("❌")
            self.title_label.setText("Uninstallation failed")
            self.status_label.setText(message[:80] if message else "Something went wrong.")

        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def sizeHint(self):
        return QSize(660, 110)


class _UninstallProgressBar(QWidget):
    """Animated progress bar with red/orange gradient for uninstallation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._indeterminate = True
        self._phase = 0.0
        self._success = False
        self._failure = False
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self):
        self._phase = (self._phase + 0.03) % (2 * math.pi)
        self.update()

    def set_value(self, val):
        self._value = max(0, min(100, val))
        self._indeterminate = False
        self.update()

    def set_success(self):
        self._success = True
        self._failure = False
        self._value = 100
        self._indeterminate = False
        self._timer.stop()
        self.update()

    def set_failure(self):
        self._failure = True
        self._success = False
        self._indeterminate = False
        self._timer.stop()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2

        # Track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawRoundedRect(0, 0, w, h, r, r)

        if self._success:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor("#34C759"))
            grad.setColorAt(1, QColor("#30D158"))
            p.setBrush(grad)
            p.drawRoundedRect(0, 0, w, h, r, r)
        elif self._failure:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor("#FF453A"))
            grad.setColorAt(1, QColor("#FF3B30"))
            p.setBrush(grad)
            p.drawRoundedRect(0, 0, w, h, r, r)
        elif self._indeterminate:
            # Red/orange shimmer sweep
            phase = self._phase
            center = (math.sin(phase) * 0.5 + 0.5) * w
            half = w * 0.35
            start = center - half
            end = center + half
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(max(0.0, (start - 20) / w), QColor(255, 80, 0, 0))
            grad.setColorAt(max(0.0, min(1.0, start / w)), QColor("#FF6B35"))
            grad.setColorAt(max(0.0, min(1.0, center / w)), QColor("#FF453A"))
            grad.setColorAt(max(0.0, min(1.0, end / w)), QColor("#FF6B35"))
            grad.setColorAt(min(1.0, (end + 20) / w), QColor(255, 80, 0, 0))
            p.setBrush(grad)
            p.drawRoundedRect(0, 0, w, h, r, r)
        else:
            fill_w = int(w * self._value / 100)
            if fill_w > 0:
                grad = QLinearGradient(0, 0, fill_w, 0)
                grad.setColorAt(0, QColor("#FF6B35"))
                grad.setColorAt(0.5, QColor("#FF453A"))
                grad.setColorAt(1, QColor("#C0392B"))
                p.setBrush(grad)
                p.drawRoundedRect(0, 0, fill_w, h, r, r)

        p.end()

