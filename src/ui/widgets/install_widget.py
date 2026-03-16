import os
import threading

import requests
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSizePolicy
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QColor, QLinearGradient, QPixmap, QIcon


# ── Animated progress bar ─────────────────────────────────────────────────────

class AnimatedProgressBar(QWidget):
    """Left-to-right sweeping glow — more natural for install/download progress."""

    def __init__(self, color_start="#667EEA", color_mid="#A78BFA", parent=None):
        super().__init__(parent)
        self._phase = 0.0          # 0..1, linear sweep position
        self._indeterminate = True
        self._success = False
        self._failure = False
        self._value = 0
        self._c_start = color_start
        self._c_mid = color_mid
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60 fps

    def _tick(self):
        # Linear sweep — complete one pass every ~2.5 s
        self._phase = (self._phase + 0.007) % 1.0
        self.update()

    def set_value(self, val):
        self._value = max(0, min(100, val))
        self._indeterminate = False
        self.update()

    def set_success(self):
        self._success = True
        self._indeterminate = False
        self._timer.stop()
        self.update()

    def set_failure(self):
        self._failure = True
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
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(0, 0, w, h, r, r)

        if self._success:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor("#34C759"))
            g.setColorAt(1, QColor("#30D158"))
            p.setBrush(g)
            p.drawRoundedRect(0, 0, w, h, r, r)

        elif self._failure:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor("#FF453A"))
            g.setColorAt(1, QColor("#FF3B30"))
            p.setBrush(g)
            p.drawRoundedRect(0, 0, w, h, r, r)

        elif self._indeterminate:
            # A glowing band sweeps left → right and loops.
            # band_w is the width of the bright spot relative to bar width.
            band_w = w * 0.55
            # start goes from -band_w (off left edge) to w (off right edge)
            start_x = -band_w + self._phase * (w + band_w)
            end_x = start_x + band_w

            g = QLinearGradient(start_x, 0, end_x, 0)
            g.setColorAt(0.0,  QColor(100, 120, 255, 0))
            g.setColorAt(0.25, QColor(self._c_start))
            g.setColorAt(0.5,  QColor(self._c_mid))
            g.setColorAt(0.75, QColor(self._c_start))
            g.setColorAt(1.0,  QColor(100, 120, 255, 0))
            p.setBrush(g)
            p.drawRoundedRect(0, 0, w, h, r, r)

        else:
            fill_w = int(w * self._value / 100)
            if fill_w > 0:
                g = QLinearGradient(0, 0, fill_w, 0)
                g.setColorAt(0,   QColor(self._c_start))
                g.setColorAt(0.5, QColor(self._c_mid))
                g.setColorAt(1,   QColor(self._c_start))
                p.setBrush(g)
                p.drawRoundedRect(0, 0, fill_w, h, r, r)

        p.end()


# ── Shared icon helpers ───────────────────────────────────────────────────────

def _get_local_app_icon(app_name: str) -> QPixmap | None:
    """Check /Applications for a .app bundle and return its icon as QPixmap, or None."""
    from PyQt6.QtWidgets import QFileIconProvider
    from PyQt6.QtCore import QFileInfo

    name_clean = app_name.replace('-', ' ').lower()
    search_paths = ["/Applications", "/System/Applications", os.path.expanduser("~/Applications")]
    provider = QFileIconProvider()

    for base in search_paths:
        if not os.path.exists(base):
            continue
        # Exact match
        for candidate in [app_name, name_clean, app_name.title()]:
            path = os.path.join(base, f"{candidate}.app")
            if os.path.exists(path):
                icon = provider.icon(QFileInfo(path))
                if not icon.isNull():
                    return icon.pixmap(32, 32)
        # Partial match
        try:
            for item in os.listdir(base):
                if item.endswith(".app") and name_clean in item.lower():
                    icon = provider.icon(QFileInfo(os.path.join(base, item)))
                    if not icon.isNull():
                        return icon.pixmap(32, 32)
        except OSError:
            pass
    return None


def _fetch_favicon(homepage: str, on_done):
    """Download a 64-px favicon via Google S2 in a daemon thread, call on_done(QPixmap)."""
    if not homepage:
        return

    def _worker():
        try:
            from urllib.parse import urlparse
            parsed = urlparse(homepage if homepage.startswith("http") else "https://" + homepage)
            domain = parsed.netloc.lstrip("www.") or parsed.path
            if not domain:
                return
            url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if r.status_code == 200:
                px = QPixmap()
                px.loadFromData(r.content)
                if not px.isNull():
                    on_done(px)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


# ── InstallProgressWidget ─────────────────────────────────────────────────────

class InstallProgressWidget(QWidget):
    candidate_confirmed = pyqtSignal(object)
    _icon_ready = pyqtSignal(object)  # QPixmap

    def __init__(self, app_name, website_url="", theme="dark", parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.website_url = website_url
        self.current_theme = theme
        self._finished = False
        self._icon_ready.connect(self._apply_icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("InstallCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        # Header row
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        self.icon_label.setText("📦")
        self.icon_label.setFont(QFont("Manrope", 20))

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

        self.progress_bar = AnimatedProgressBar()

        # Candidate list (hidden by default)
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
        self._load_icon()

    # ── Icon loading ──────────────────────────────────────────────

    def _load_icon(self):
        # 1. Try local /Applications first (instant)
        px = _get_local_app_icon(self.app_name)
        if px:
            self._apply_icon(px)
            return
        # 2. Fetch favicon from homepage in background
        homepage = self.website_url
        if not homepage:
            try:
                from src.services.system.installer import get_package_metadata
                meta = get_package_metadata(self.app_name)
                if meta:
                    homepage = meta.get("homepage", "")
            except Exception:
                pass
        _fetch_favicon(homepage, lambda px: self._icon_ready.emit(px))

    def _apply_icon(self, px: QPixmap):
        if isinstance(px, QPixmap) and not px.isNull():
            scaled = px.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.icon_label.setText("")
            self.icon_label.setPixmap(scaled)

    # ── Theme ─────────────────────────────────────────────────────

    def _apply_theme(self):
        is_dark = self.current_theme == "dark"
        bg = "rgba(255,255,255,0.07)" if is_dark else "rgba(0,0,0,0.04)"
        border = "rgba(255,255,255,0.12)" if is_dark else "rgba(0,0,0,0.10)"
        title_c = "#FFFFFF" if is_dark else "#111111"
        status_c = "rgba(255,255,255,0.55)" if is_dark else "#666666"

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

    # ── Public API ────────────────────────────────────────────────

    def update_status(self, text):
        self.status_label.setText(text)

    def add_log(self, text):
        pass  # no log view shown to user

    def update_progress(self, val):
        self.progress_bar.set_value(val)

    def set_finished(self, success, message):
        self._finished = True
        display_name = self.app_name.replace('-', ' ').title()
        if success:
            self.progress_bar.set_success()
            self.title_label.setText(f"Installed {display_name}")
            self.status_label.setText("Done!")
        else:
            self.progress_bar.set_failure()
            self.icon_label.setText("❌")
            self.icon_label.setPixmap(QPixmap())
            self.title_label.setText("Installation failed")
            self.status_label.setText(message[:80] if message else "Something went wrong.")

        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def show_candidates(self, candidates):
        self.list_widget.clear()
        self.status_label.setText("Multiple packages found — select one:")
        self.list_widget.show()
        for c in candidates:
            details = c.get('description', c.get('name', ''))[:60]
            txt = f"{c.get('display_name', c.get('name'))} — {details}"
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.list_widget.addItem(item)
        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def reset(self):
        self._finished = False
        display_name = self.app_name.replace('-', ' ').title()
        self.icon_label.setText("📦")
        self.icon_label.setPixmap(QPixmap())
        self.title_label.setText(f"Installing {display_name}")
        self.progress_bar._failure = False
        self.progress_bar._success = False
        self.progress_bar._indeterminate = True
        self.progress_bar._timer.start(16)
        self.progress_bar.update()
        self.list_widget.clear()
        self.list_widget.hide()
        self._apply_theme()
        self.status_label.setText("Preparing…")
        self._load_icon()

    def on_candidate_selected(self, item):
        self.candidate_confirmed.emit(item.data(Qt.ItemDataRole.UserRole))

    def sizeHint(self):
        return QSize(660, 110)


# ── UninstallProgressWidget ───────────────────────────────────────────────────

class UninstallProgressWidget(QWidget):
    _icon_ready = pyqtSignal(object)

    def __init__(self, app_name, theme="dark", parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.current_theme = theme
        self._finished = False
        self._icon_ready.connect(self._apply_icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("UninstallCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        self.icon_label.setText("🗑️")
        self.icon_label.setFont(QFont("Manrope", 20))

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

        # Red/orange sweep bar for uninstall
        self.progress_bar = AnimatedProgressBar(color_start="#FF6B35", color_mid="#FF453A")

        card_layout.addLayout(header_row)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.progress_bar)

        layout.addWidget(self.card)
        self._apply_theme()
        self._load_icon()

    def _load_icon(self):
        px = _get_local_app_icon(self.app_name)
        if px:
            self._apply_icon(px)
            return
        try:
            from src.services.system.installer import get_package_metadata
            meta = get_package_metadata(self.app_name)
            homepage = meta.get("homepage", "") if meta else ""
        except Exception:
            homepage = ""
        _fetch_favicon(homepage, lambda px: self._icon_ready.emit(px))

    def _apply_icon(self, px: QPixmap):
        if isinstance(px, QPixmap) and not px.isNull():
            scaled = px.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.icon_label.setText("")
            self.icon_label.setPixmap(scaled)

    def _apply_theme(self):
        is_dark = self.current_theme == "dark"
        bg = "rgba(255,255,255,0.07)" if is_dark else "rgba(255,60,60,0.04)"
        border = "rgba(255,80,80,0.18)" if is_dark else "rgba(255,60,60,0.15)"
        title_c = "#FFFFFF" if is_dark else "#111111"
        status_c = "rgba(255,255,255,0.55)" if is_dark else "#666666"

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

    def update_status(self, text):
        self.status_label.setText(text)

    def add_log(self, text):
        pass

    def update_progress(self, val):
        self.progress_bar.set_value(val)

    def set_finished(self, success, message):
        self._finished = True
        display_name = self.app_name.replace('-', ' ').title()
        if success:
            self.progress_bar.set_success()
            self.title_label.setText(f"Uninstalled {display_name}")
            self.status_label.setText("Done!")
        else:
            self.progress_bar.set_failure()
            self.icon_label.setText("❌")
            self.icon_label.setPixmap(QPixmap())
            self.title_label.setText("Uninstallation failed")
            self.status_label.setText(message[:80] if message else "Something went wrong.")

        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def sizeHint(self):
        return QSize(660, 110)
