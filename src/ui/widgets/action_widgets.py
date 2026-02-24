import os
import threading
import logging
import urllib.request
import requests
from urllib.parse import urlparse
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu,
                              QFileIconProvider, QSizePolicy, QPushButton)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QFileInfo, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QPainterPath, QGuiApplication, QCursor
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from src.ui.styles import THEMES
try:
    from src.ui.widgets.math_widget import MathWidget
except ImportError:
    MathWidget = QWidget

class LinkActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, title, url, description, parent=None):
        super().__init__(parent)
        self.url = url
        self.icon_downloaded.connect(self.update_icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        self.card.setStyleSheet("""
            QWidget#ActionCard {
                background-color: rgba(255, 255, 255, 0.25);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 12, 10) # Reduced vertical padding
        card_layout.setSpacing(2) # Tighter spacing

        # Top Row: Icon + Label
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel("↗")
        self.icon_label.setFixedSize(20, 20) # Smaller icon
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            background-color: transparent; 
            color: #333333; 
            font-size: 10px; 
            border: none;
        """)

        self.action_label = QLabel(f"WEBSITE")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #888888; letter-spacing: 0.5px;")

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()

        # Title (Supports limited formatting)
        # Parse markdown bold **text** to HTML <b>text</b>
        formatted_title = title.replace("**", "<b>", 1).replace("**", "</b>", 1)
        # Handle multiple occurrences if any
        import re
        formatted_title = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", title)
        
        self.title_label = QLabel(formatted_title)
        self.title_label.setWordWrap(True)
        self.title_label.setFont(QFont("Instrument Serif", 18, QFont.Weight.Normal)) # Slightly smaller font
        self.title_label.setStyleSheet("color: #050505; margin-top: 0px;")
        self.title_label.setTextFormat(Qt.TextFormat.RichText) # Enable HTML for bold tags

        # Description (URL)
        self.desc_label = QLabel(url)
        self.desc_label.setWordWrap(True)
        self.desc_label.setFont(QFont("Manrope", 11, QFont.Weight.Medium))
        if not url:
            self.desc_label.hide()

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.desc_label)

        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
        
        self.fetch_icon()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = self.current_theme == "dark"
        
        # Card Style
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.10)"
            hover_bg = "rgba(255, 255, 255, 0.10)"
            hover_border = "rgba(255, 255, 255, 0.2)"
            title_color = "#FFFFFF"
            desc_color = "#CCCCCC"
            action_color = "#CCCCCC"
            icon_bg = "transparent"
            icon_color = "#FFFFFF"
            icon_border = "none"
        else:
            bg = "rgba(255, 255, 255, 0.25)"
            border = "rgba(255, 255, 255, 0.4)"
            hover_bg = "rgba(255, 255, 255, 0.45)"
            hover_border = "rgba(255, 255, 255, 0.6)"
            title_color = "#050505"
            desc_color = "#555555"
            action_color = "#888888"
            icon_bg = "transparent"
            icon_color = "#333333"
            icon_border = "none"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.title_label.setStyleSheet(f"color: {title_color}; margin-top: 0px;")
        if hasattr(self, 'desc_label'):
            self.desc_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 0.5px;")
        
        self.icon_label.setStyleSheet(f"""
            background-color: {icon_bg}; 
            color: {icon_color}; 
            font-size: 10px; 
            border-radius: 5px; 
            border: {icon_border};
        """)

    def fetch_icon(self):
        try:
            if not self.url: return
            clean_url = self.url.strip().strip('<>').strip('"').strip("'")
            if not clean_url.startswith("http") and not clean_url.startswith("//"):
                clean_url = "https://" + clean_url
            parsed = urlparse(clean_url)
            domain = parsed.netloc
            if not domain and parsed.path:
                possible = parsed.path.split('/')[0]
                if '.' in possible: domain = possible
            if not domain: return
            if domain.startswith("www."): domain = domain[4:]
            icon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
            threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()
        except Exception: pass

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            # Add safety buffer for font metrics/shadows
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class SettingsActionWidget(QWidget):
    """
    Inline action widget for system settings changes.
    Visual style adapts to setting type:
      - circular  : brightness (arc progress + sun icon)
      - bar       : volume / mute (fill bar + speaker icon)
      - toggle    : all boolean settings (animated pill switch)
    """

    _CIRCULAR_SETTINGS = {"brightness"}
    _BAR_SETTINGS = {"volume"}

    def __init__(self, setting, value, label, unit, color_hex, icon_name, success, parent=None):
        super().__init__(parent)
        self.setting = setting
        self.value = value
        self.label_text = label
        self.unit = unit
        from PyQt6.QtGui import QColor
        self.color = QColor(color_hex)
        self.icon_name = icon_name
        self.success = success

        self.is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        self.bool_on = value if isinstance(value, bool) else True

        self._anim_value = 0.0
        self.current_theme = "light"

        if setting in self._CIRCULAR_SETTINGS:
            self.visual_type = "circular"
        elif setting in self._BAR_SETTINGS:
            self.visual_type = "bar"
        else:
            self.visual_type = "toggle"

        self.setup_ui()
        self.start_animation()

    def setup_ui(self):
        from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
        from PyQt6.QtGui import QFont
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("ActionCard")

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(16)

        self.anim_widget = QWidget()
        if self.visual_type == "toggle":
            self.anim_widget.setFixedSize(58, 32)
        else:
            self.anim_widget.setFixedSize(48, 48)
        self.anim_widget.paintEvent = self.paint_anim_widget

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 2, 0, 2)

        status_text = "OK" if self.success else "Failed"
        self.top_label = QLabel(f"SETTING • {status_text}")
        self.top_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.top_label.setStyleSheet("color: #888888; letter-spacing: 0.5px;")

        if self.is_numeric:
            label_html = f"<b>{self.label_text}</b>: {self.value}{self.unit}"
        else:
            label_html = f"<b>{self.label_text}</b>"

        self.main_label = QLabel(label_html)
        self.main_label.setWordWrap(True)
        self.main_label.setFont(QFont("Instrument Serif", 18, QFont.Weight.Normal))
        self.main_label.setStyleSheet("color: #050505; margin-top: 0px;")
        self.main_label.setTextFormat(Qt.TextFormat.RichText)

        text_layout.addWidget(self.top_label)
        text_layout.addWidget(self.main_label)
        text_layout.addStretch()

        card_layout.addWidget(self.anim_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        card_layout.addLayout(text_layout)
        card_layout.addStretch()

        layout.addWidget(self.card)
        self.update_style()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        title_color = "#FFFFFF" if is_dark else "#050505"
        action_color = "#AAAAAA" if is_dark else "#888888"

        self.card.setStyleSheet("QWidget#ActionCard { background-color: transparent; border: none; }")
        self.main_label.setStyleSheet(f"color: {title_color}; margin-top: 0px;")
        self.top_label.setStyleSheet(f"color: {action_color}; letter-spacing: 0.5px;")
        self.anim_widget.update()

    def _get_arc(self): return self._anim_value
    def _set_arc(self, v):
        self._anim_value = v
        self.anim_widget.update()

    from PyQt6.QtCore import pyqtProperty
    arcValue = pyqtProperty(float, _get_arc, _set_arc)

    def start_animation(self):
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        self.anim = QPropertyAnimation(self, b"arcValue", self)
        self.anim.setDuration(650)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        if self.is_numeric:
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(float(self.value) / 100.0)
        elif self.bool_on:
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
        else:
            self.anim.setStartValue(1.0)
            self.anim.setEndValue(0.0)

        self.anim.start()

    def paint_anim_widget(self, event):
        from PyQt6.QtGui import QPainter
        w = self.anim_widget
        p = QPainter(w)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_dark = self.current_theme == "dark"

        if self.visual_type == "toggle":
            self._paint_toggle(p, w, is_dark)
        elif self.visual_type == "bar":
            self._paint_bar(p, w, is_dark)
        else:
            self._paint_circular(p, w, is_dark)

        p.end()

    def _paint_toggle(self, p, w, is_dark):
        from PyQt6.QtGui import QBrush, QColor, QPainterPath
        from PyQt6.QtCore import Qt, QRectF, QPointF

        W, H = float(w.width()), float(w.height())
        pad = 1.5
        pill_w = W - pad * 2
        pill_h = H - pad * 2
        radius = pill_h / 2

        t = self._anim_value
        # Interpolate track: gray (off) → setting color (on)
        gray = (150, 152, 158)
        cr = int(gray[0] + (self.color.red()   - gray[0]) * t)
        cg = int(gray[1] + (self.color.green() - gray[1]) * t)
        cb = int(gray[2] + (self.color.blue()  - gray[2]) * t)
        track_color = QColor(max(0, min(255, cr)), max(0, min(255, cg)), max(0, min(255, cb)), 210)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(pad, pad, pill_w, pill_h), radius, radius)
        p.drawPath(pill)

        # Thumb
        margin = 3.0
        thumb_r = radius - margin
        x_off = pad + margin + thumb_r
        x_on  = pad + pill_w - margin - thumb_r
        thumb_x = x_off + (x_on - x_off) * t
        thumb_y = H / 2.0

        # Drop shadow
        p.setBrush(QBrush(QColor(0, 0, 0, 40)))
        p.drawEllipse(QPointF(thumb_x, thumb_y + 1.5), thumb_r, thumb_r)
        # White thumb
        p.setBrush(QBrush(QColor(255, 255, 255, 245)))
        p.drawEllipse(QPointF(thumb_x, thumb_y), thumb_r, thumb_r)

    def _paint_bar(self, p, w, is_dark):
        from PyQt6.QtGui import QBrush, QColor, QPainterPath
        from PyQt6.QtCore import Qt, QRectF

        W, H = float(w.width()), float(w.height())

        # Speaker icon (upper 40%)
        try:
            from src.services.system.macos_settings import draw_volume
            icon_color = self.color if self.setting != "mute" else QColor("#FF453A")
            draw_volume(p, W / 2, H * 0.30, 8.5, icon_color)
        except Exception:
            pass

        # Fill bar (lower 45%)
        bar_h = 9.0
        bar_pad = 5.0
        bar_y = H * 0.62
        bar_x = bar_pad
        bar_w = W - bar_pad * 2

        track_color = QColor(255, 255, 255, 22) if is_dark else QColor(0, 0, 0, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        track = QPainterPath()
        track.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h / 2, bar_h / 2)
        p.drawPath(track)

        fill_w = bar_w * self._anim_value
        if fill_w >= bar_h:
            fill_color = self.color
            p.setBrush(QBrush(fill_color))
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), bar_h / 2, bar_h / 2)
            p.drawPath(fill)

    def _paint_circular(self, p, w, is_dark):
        from PyQt6.QtGui import QPen, QColor
        from PyQt6.QtCore import Qt, QRectF

        W, H = w.width(), w.height()
        rect = QRectF(3, 3, W - 6, H - 6)
        cx, cy = rect.center().x(), rect.center().y()

        # Track ring
        track_color = QColor(255, 255, 255, 20) if is_dark else QColor(0, 0, 0, 15)
        p.setPen(QPen(track_color, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 225 * 16, -270 * 16)

        # Progress arc
        if self._anim_value > 0:
            p.setPen(QPen(self.color, 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 225 * 16, int(-270 * 16 * self._anim_value))

        # Icon in centre
        try:
            from src.services.system.macos_settings import _ICON_DRAW_FNS, draw_brightness
            draw_fn = _ICON_DRAW_FNS.get(self.icon_name) or draw_brightness
            draw_fn(p, cx, cy, 10, self.color)
        except Exception:
            pass

class AppActionWidget(QWidget):
    app_accepted = pyqtSignal(str, QWidget)
    
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.app_name = name
        self.current_theme = "light"
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("AppActionCard")
        
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(18, 18, 18, 18)
        self.card_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        
        # Load app icon
        icon = self._get_app_icon(name)
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(32, 32))
        else:
            # Fallback styling if icon not found
            self.icon_label.setText("🚀")
            self.icon_label.setFont(QFont("Manrope", 20))
        
        display_name = name.title()
        
        self.title_label = QLabel(f"Open {display_name}")
        self.title_label.setFont(QFont("Instrument Serif", 22, QFont.Weight.Normal))
        self.title_label.setStyleSheet("color: #111111; background: transparent;")
        
        header_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        
        self.desc_label = QLabel(f"Do you want to open {display_name}?")
        self.desc_label.setFont(QFont("Manrope", 11))
        self.desc_label.setStyleSheet("color: #555555; background: transparent;")
        self.desc_label.setWordWrap(True)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.btn_no = QPushButton("No, Cancel")
        self.btn_no.setFixedSize(120, 36)
        self.btn_no.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_no.clicked.connect(self.reject_open)
        
        self.btn_yes = QPushButton("Yes, Open")
        self.btn_yes.setFixedSize(120, 36)
        self.btn_yes.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_yes.clicked.connect(self.accept_open)
        
        button_layout.addWidget(self.btn_no)
        button_layout.addWidget(self.btn_yes)
        button_layout.addStretch()
        
        self.card_layout.addLayout(header_layout)
        self.card_layout.addWidget(self.desc_label)
        self.card_layout.addLayout(button_layout)
        
        layout.addWidget(self.card)
        self.update_style()
        
    def _get_app_icon(self, app_name):
        from PyQt6.QtWidgets import QFileIconProvider
        from PyQt6.QtCore import QFileInfo
        import os
        
        search_paths = [
            "/Applications",
            "/System/Applications",
            os.path.expanduser("~/Applications")
        ]
        
        name_clean = app_name.replace('-', ' ').lower()
        
        provider = QFileIconProvider()
        for base in search_paths:
            if not os.path.exists(base): continue
            
            # Exact match
            exact = os.path.join(base, f"{app_name}.app")
            if os.path.exists(exact):
                return provider.icon(QFileInfo(exact))
                
            exact_clean = os.path.join(base, f"{name_clean}.app")
            if os.path.exists(exact_clean):
                return provider.icon(QFileInfo(exact_clean))
            
            # Substring match
            for item in os.listdir(base):
                if item.endswith(".app") and name_clean in item.lower():
                    return provider.icon(QFileInfo(os.path.join(base, item)))
                    
        return QIcon()
        
    def accept_open(self):
        self.desc_label.setText("App launched.")
        self.desc_label.setStyleSheet("color: #34C759; background: transparent; font-weight: bold;")
        self.btn_no.hide()
        self.btn_yes.hide()
        self.app_accepted.emit(self.app_name, self)
        
    def reject_open(self):
        self.desc_label.setText("Cancelled.")
        self.desc_label.setStyleSheet("color: #FF453A; background: transparent; font-weight: bold;")
        self.btn_no.hide()
        self.btn_yes.hide()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.10)"
            title_color = "#FFFFFF"
            desc_color = "#AAAAAA"
            btn_no_bg = "rgba(255,255,255,0.08)"
            btn_no_col = "#FFFFFF"
            btn_no_hover = "rgba(255,255,255,0.15)"
            btn_yes_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10A37F, stop:1 #0E906F)"
            btn_yes_col = "#FFFFFF"
            btn_yes_hover = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #14B88F, stop:1 #10A37F)"
        else:
            bg = "rgba(255, 255, 255, 0.4)"
            border = "rgba(100, 100, 100, 0.2)"
            title_color = "#111111"
            desc_color = "#555555"
            btn_no_bg = "rgba(0,0,0,0.05)"
            btn_no_col = "#333333"
            btn_no_hover = "rgba(0,0,0,0.1)"
            btn_yes_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10A37F, stop:1 #0E906F)"
            btn_yes_col = "#FFFFFF"
            btn_yes_hover = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #14B88F, stop:1 #10A37F)"
            
        self.card.setStyleSheet(f"""
            QWidget#AppActionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        self.title_label.setStyleSheet(f"color: {title_color}; background: transparent;")
        if "cancelled" not in self.desc_label.text().lower() and "launched" not in self.desc_label.text().lower():
            self.desc_label.setStyleSheet(f"color: {desc_color}; background: transparent;")
            
        self.btn_no.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_no_bg};
                color: {btn_no_col};
                border: none;
                border-radius: 10px;
                font-family: 'Manrope';
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {btn_no_hover}; }}
        """)
        
        self.btn_yes.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_yes_bg};
                color: {btn_yes_col};
                border: none;
                border-radius: 10px;
                font-family: 'Manrope';
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {btn_yes_hover}; }}
        """)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def sizeHint(self):
        w = getattr(self.parent(), 'width', lambda: 660)()
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 16)
        return QSize(660, 120)
        
class InstallActionWidget(QWidget):
    install_accepted = pyqtSignal(str, QWidget)
    icon_downloaded = pyqtSignal(object)
    
    def __init__(self, name, website_url, parent=None):
        super().__init__(parent)
        self.app_name = name
        self.website_url = website_url
        self.current_theme = "light"
        self.icon_downloaded.connect(self.update_icon)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("InstallActionCard")
        
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(18, 18, 18, 18)
        self.card_layout.setSpacing(12)

        # -- Prompt Layout --
        self.prompt_widget = QWidget()
        prompt_layout = QVBoxLayout(self.prompt_widget)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(12)
        
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        
        # Determine fallback icon depending on brew vs general app
        # Try local first if installed, just in case
        icon = self._get_app_icon(name)
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(32, 32))
        else:
            # Fallback before download completes
            self.icon_label.setText("📦")
            self.icon_label.setFont(QFont("Manrope", 20))
        
        # Display name formatting
        display_name = title_case_name = name.replace('-', ' ').title()
        
        self.title_label = QLabel(f"Install {display_name}")
        self.title_label.setFont(QFont("Instrument Serif", 22, QFont.Weight.Normal))
        self.title_label.setStyleSheet("color: #111111; background: transparent;")
        
        header_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        
        self.desc_label = QLabel(f"Do you want to install {display_name} using the system package manager?")
        self.desc_label.setFont(QFont("Manrope", 11))
        self.desc_label.setStyleSheet("color: #555555; background: transparent;")
        self.desc_label.setWordWrap(True)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.btn_no = QPushButton("No, Cancel")
        self.btn_no.setFixedSize(120, 36)
        self.btn_no.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_no.clicked.connect(self.reject_install)
        
        self.btn_yes = QPushButton("Yes, Install")
        self.btn_yes.setFixedSize(120, 36)
        self.btn_yes.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_yes.clicked.connect(self.accept_install)
        
        button_layout.addWidget(self.btn_no)
        button_layout.addWidget(self.btn_yes)
        button_layout.addStretch()
        
        prompt_layout.addLayout(header_layout)
        prompt_layout.addWidget(self.desc_label)
        prompt_layout.addLayout(button_layout)
        
        self.card_layout.addWidget(self.prompt_widget)
        
        # Container for progress widget
        self.progress_container = QVBoxLayout()
        self.progress_container.setContentsMargins(0, 0, 0, 0)
        self.card_layout.addLayout(self.progress_container)
        
        layout.addWidget(self.card)
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        if not self.website_url: return
        try:
            clean_url = self.website_url.strip().strip('<>').strip('"').strip("'")
            if not clean_url.startswith("http") and not clean_url.startswith("//"):
                clean_url = "https://" + clean_url
            parsed = urlparse(clean_url)
            domain = parsed.netloc
            if not domain: return
            if domain.startswith("www."): domain = domain[4:]
            icon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
            threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()
        except: pass

    def _download_icon(self, url):
        try:
            # Check if this app is homebrew itself
            if "brew.sh" in url:
                pass # Brew icon might not look great directly from favicon
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            # Overwrite if we don't have a better high-res icon from local fs
            if self.icon_label.text() == "📦":
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    self.icon_label.setText("")
                    self.icon_label.setPixmap(pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass
        
    def _get_app_icon(self, app_name):
        from PyQt6.QtWidgets import QFileIconProvider
        from PyQt6.QtCore import QFileInfo
        import os
        
        search_paths = [
            "/Applications",
            "/System/Applications",
            os.path.expanduser("~/Applications")
        ]
        name_clean = app_name.replace('-', ' ').lower()
        
        provider = QFileIconProvider()
        for base in search_paths:
            if not os.path.exists(base): continue
            
            exact = os.path.join(base, f"{app_name}.app")
            if os.path.exists(exact):
                return provider.icon(QFileInfo(exact))
                
            exact_clean = os.path.join(base, f"{name_clean}.app")
            if os.path.exists(exact_clean):
                return provider.icon(QFileInfo(exact_clean))
            
            for item in os.listdir(base):
                if item.endswith(".app") and name_clean in item.lower():
                    return provider.icon(QFileInfo(os.path.join(base, item)))
        return QIcon()
        
    def accept_install(self):
        self.desc_label.setText("Installation started.")
        self.desc_label.setStyleSheet("color: #34C759; background: transparent; font-weight: bold;")
        self.btn_no.hide()
        self.btn_yes.hide()
        self.install_accepted.emit(self.app_name, self)
        
    def reject_install(self):
        self.desc_label.setText("Installation cancelled.")
        self.desc_label.setStyleSheet("color: #FF453A; background: transparent; font-weight: bold;")
        self.btn_no.hide()
        self.btn_yes.hide()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.10)"
            title_color = "#FFFFFF"
            desc_color = "#AAAAAA"
            btn_no_bg = "rgba(255,255,255,0.08)"
            btn_no_col = "#FFFFFF"
            btn_no_hover = "rgba(255,255,255,0.15)"
            btn_yes_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667EEA, stop:1 #764BA2)"
            btn_yes_col = "#FFFFFF"
            btn_yes_hover = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7A8FF0, stop:1 #8B57D6)"
        else:
            bg = "rgba(255, 255, 255, 0.6)"
            border = "rgba(100, 100, 100, 0.15)"
            title_color = "#111111"
            desc_color = "#555555"
            btn_no_bg = "rgba(0,0,0,0.05)"
            btn_no_col = "#333333"
            btn_no_hover = "rgba(0,0,0,0.1)"
            btn_yes_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667EEA, stop:1 #764BA2)"
            btn_yes_col = "#FFFFFF"
            btn_yes_hover = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7A8FF0, stop:1 #8B57D6)"
            
        self.card.setStyleSheet(f"""
            QWidget#InstallActionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        self.title_label.setStyleSheet(f"color: {title_color}; background: transparent;")
        if "cancelled" not in self.desc_label.text().lower() and "started" not in self.desc_label.text().lower():
            self.desc_label.setStyleSheet(f"color: {desc_color}; background: transparent;")
            
        self.btn_no.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_no_bg};
                color: {btn_no_col};
                border: none;
                border-radius: 10px;
                font-family: 'Manrope';
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {btn_no_hover}; }}
        """)
        
        self.btn_yes.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_yes_bg};
                color: {btn_yes_col};
                border: none;
                border-radius: 10px;
                font-family: 'Manrope';
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {btn_yes_hover}; }}
        """)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def sizeHint(self):
        w = getattr(self.parent(), 'width', lambda: 660)()
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 16)
        return QSize(660, 120)


class UninstallActionWidget(QWidget):
    """Confirmation card asking user to confirm uninstallation of an app."""
    uninstall_accepted = pyqtSignal(str, QWidget)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.app_name = name
        self.current_theme = "light"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("UninstallActionCard")

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(18, 18, 18, 18)
        self.card_layout.setSpacing(12)

        # -- Prompt Layout --
        self.prompt_widget = QWidget()
        prompt_layout = QVBoxLayout(self.prompt_widget)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent;")
        
        icon = self._get_app_icon(name)
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(32, 32))
        else:
            self.icon_label.setText("🗑️")
            self.icon_label.setFont(QFont("Manrope", 20))

        display_name = name.replace('-', ' ').title()

        self.title_label = QLabel(f"Uninstall {display_name}")
        self.title_label.setFont(QFont("Instrument Serif", 22, QFont.Weight.Normal))
        self.title_label.setStyleSheet("color: #111111; background: transparent;")

        header_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.desc_label = QLabel(f"Do you want to uninstall {display_name}? This will remove it via the system package manager.")
        self.desc_label.setFont(QFont("Manrope", 11))
        self.desc_label.setStyleSheet("color: #555555; background: transparent;")
        self.desc_label.setWordWrap(True)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        self.btn_no = QPushButton("No, Cancel")
        self.btn_no.setFixedSize(120, 36)
        self.btn_no.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_no.clicked.connect(self.reject_uninstall)

        self.btn_yes = QPushButton("Yes, Uninstall")
        self.btn_yes.setFixedSize(140, 36)
        self.btn_yes.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_yes.clicked.connect(self.accept_uninstall)

        button_layout.addWidget(self.btn_no)
        button_layout.addWidget(self.btn_yes)
        button_layout.addStretch()

        prompt_layout.addLayout(header_layout)
        prompt_layout.addWidget(self.desc_label)
        prompt_layout.addLayout(button_layout)

        self.card_layout.addWidget(self.prompt_widget)

        layout.addWidget(self.card)
        self.update_style()

    def _get_app_icon(self, app_name):
        from PyQt6.QtWidgets import QFileIconProvider
        from PyQt6.QtCore import QFileInfo
        import os
        
        search_paths = [
            "/Applications",
            "/System/Applications",
            os.path.expanduser("~/Applications")
        ]
        name_clean = app_name.replace('-', ' ').lower()
        
        provider = QFileIconProvider()
        for base in search_paths:
            if not os.path.exists(base): continue
            
            exact = os.path.join(base, f"{app_name}.app")
            if os.path.exists(exact):
                return provider.icon(QFileInfo(exact))
                
            exact_clean = os.path.join(base, f"{name_clean}.app")
            if os.path.exists(exact_clean):
                return provider.icon(QFileInfo(exact_clean))
            
            for item in os.listdir(base):
                if item.endswith(".app") and name_clean in item.lower():
                    return provider.icon(QFileInfo(os.path.join(base, item)))
        return QIcon()

    def accept_uninstall(self):
        self.desc_label.setText("Uninstallation started.")
        self.desc_label.setStyleSheet("color: #FF6B35; background: transparent; font-weight: bold;")
        self.btn_no.hide()
        self.btn_yes.hide()
        self.uninstall_accepted.emit(self.app_name, self)

    def reject_uninstall(self):
        self.desc_label.setText("Uninstallation cancelled.")
        self.desc_label.setStyleSheet("color: #FF453A; background: transparent; font-weight: bold;")
        self.btn_no.hide()
        self.btn_yes.hide()

    def update_style(self):
        is_dark = self.current_theme == "dark"

        if is_dark:
            bg = "rgba(255, 80, 60, 0.07)"
            border = "rgba(255, 80, 60, 0.18)"
            title_color = "#FFFFFF"
            desc_color = "#AAAAAA"
            btn_no_bg = "rgba(255,255,255,0.08)"
            btn_no_col = "#FFFFFF"
            btn_no_hover = "rgba(255,255,255,0.15)"
            btn_yes_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF6B35, stop:1 #FF453A)"
            btn_yes_col = "#FFFFFF"
            btn_yes_hover = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF7B4A, stop:1 #FF5A4F)"
        else:
            bg = "rgba(255, 80, 60, 0.04)"
            border = "rgba(255, 80, 60, 0.15)"
            title_color = "#111111"
            desc_color = "#555555"
            btn_no_bg = "rgba(0,0,0,0.05)"
            btn_no_col = "#333333"
            btn_no_hover = "rgba(0,0,0,0.1)"
            btn_yes_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF6B35, stop:1 #FF453A)"
            btn_yes_col = "#FFFFFF"
            btn_yes_hover = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF7B4A, stop:1 #FF5A4F)"

        self.card.setStyleSheet(f"""
            QWidget#UninstallActionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        self.title_label.setStyleSheet(f"color: {title_color}; background: transparent;")
        desc_text = self.desc_label.text().lower()
        if "cancelled" not in desc_text and "started" not in desc_text:
            self.desc_label.setStyleSheet(f"color: {desc_color}; background: transparent;")

        self.btn_no.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_no_bg};
                color: {btn_no_col};
                border: none;
                border-radius: 10px;
                font-family: 'Manrope';
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {btn_no_hover}; }}
        """)

        self.btn_yes.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_yes_bg};
                color: {btn_yes_col};
                border: none;
                border-radius: 10px;
                font-family: 'Manrope';
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {btn_yes_hover}; }}
        """)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def sizeHint(self):
        w = getattr(self.parent(), 'width', lambda: 660)()
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 16)
        return QSize(660, 130)


class CalcActionWidget(QWidget):
    def __init__(self, content, equation="", parent=None):
        super().__init__(parent)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        self.card.setStyleSheet("""
            QWidget#ActionCard {
                background-color: rgba(255, 255, 255, 0.25);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)
        
        # Top Row: Icon + Label
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        
        self.icon_label = QLabel("=")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            background-color: #FF8C00;
            color: #FFFFFF; 
            font-size: 14px; 
            font-weight: bold;
            border-radius: 10px;
        """)
        
        self.action_label = QLabel("CALCULATION")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #888888; letter-spacing: 0.5px;")
        
        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        
        # Math Widget for result and equation
        self.math_widget = MathWidget(content, equation)
        
        card_layout.addWidget(top_row)
        card_layout.addWidget(self.math_widget)
        
        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
    
    def set_theme(self, theme):
        self.current_theme = theme
        self.math_widget.set_theme(theme)
        self.update_style()
    
    def update_style(self):
        """Update the widget style based on current theme."""
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = self.current_theme == "dark"
        
        # Card Style
        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 140, 0, 0.12), stop:1 rgba(255, 255, 255, 0.04))"
            border = "rgba(255, 140, 0, 0.2)"
            icon_color = "#FF8C00"
            action_color = "#FF8C00"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 150, 0, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(255, 150, 0, 0.2)"
            icon_color = "#E67300"
            action_color = "#E67300"
        
        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.icon_label.setStyleSheet(f"""
            background-color: transparent; 
            color: {icon_color}; 
            font-size: 10px; 
            border: none;
        """)
        
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 0.5px;")
    
    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class SearchActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, query, parent=None):
        super().__init__(parent)
        self.query = query
        self.icon_downloaded.connect(self.update_icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        # Top Row: Icon + Text
        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        top_row.setContentsMargins(0, 0, 0, 0)

        # Search Icon (Google G)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent;")

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel(f'Search "{query}"')
        self.title_label.setFont(QFont("Manrope", 14, QFont.Weight.Medium))
        self.title_label.setWordWrap(True)

        self.desc_label = QLabel("Google Search")
        self.desc_label.setFont(QFont("Manrope", 11))
        
        text_col.addWidget(self.title_label)
        text_col.addWidget(self.desc_label)

        top_row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        top_row.addLayout(text_col)
        top_row.addStretch()
        
        card_layout.addLayout(top_row)

        # Bottom Row: Chips (Images, Maps, News, YouTube)
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        chips_row.setContentsMargins(48, 0, 0, 0) # Indent to align with text
        
        self.chips = []
        
        def add_chip(text, suffix):
            lbl = QLabel(text)
            lbl.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.setProperty("url", f"https://www.google.com/search?q={query.replace(' ', '+')}&{suffix}")
            lbl.mousePressEvent = lambda e, u=lbl.property("url"): QDesktopServices.openUrl(QUrl(u))
            # Hover effects need custom class or event filter, skipping for simplicity or using stylesheet hover
            lbl.setObjectName("SearchChip")
            self.chips.append(lbl)
            chips_row.addWidget(lbl)

        add_chip("Images", "tbm=isch")
        add_chip("Maps", "tbm=map") # Google Maps search via google.com? or maps.google.com
        # Actually maps.google.com search is better
        self.chips[-1].setProperty("url", f"https://www.google.com/maps/search/{query.replace(' ', '+')}")
        
        add_chip("News", "tbm=nws")
        add_chip("Videos", "tbm=vid")
        
        chips_row.addStretch()
        card_layout.addLayout(chips_row)

        layout.addWidget(self.card)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hovered = False

        self.current_theme = "light"
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        icon_url = "https://www.google.com/s2/favicons?domain=google.com&sz=64"
        threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = self.current_theme == "dark"

        if is_dark:
            bg   = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(80, 80, 255, 0.12), stop:1 rgba(255, 255, 255, 0.04))" if self._hovered else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(80, 80, 255, 0.08), stop:1 rgba(255, 255, 255, 0.02))"
            border = "rgba(80, 80, 255, 0.25)" if self._hovered else "rgba(80, 80, 255, 0.15)"
            title_color = "#FFFFFF"
            desc_color = "rgba(255,255,255,0.7)"
            chip_bg = "rgba(80, 80, 255, 0.15)"
            chip_col = "#E0E0FF"
            chip_border = "rgba(80, 80, 255, 0.3)"
        else:
            bg   = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(50, 50, 255, 0.08), stop:1 rgba(0, 0, 0, 0.02))" if self._hovered else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(50, 50, 255, 0.04), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(50, 50, 255, 0.2)" if self._hovered else "rgba(50, 50, 255, 0.1)"
            title_color = "#050505"
            desc_color = "rgba(0,0,0,0.6)"
            chip_bg = "rgba(50, 50, 255, 0.05)"
            chip_col = "#2222DD"
            chip_border = "rgba(50, 50, 255, 0.15)"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.title_label.setStyleSheet(f"color: {title_color};")
        self.desc_label.setStyleSheet(f"color: {desc_color};")
        
        for chip in self.chips:
            chip.setStyleSheet(f"""
                QLabel {{
                    background-color: {chip_bg};
                    color: {chip_col};
                    border: 1px solid {chip_border};
                    border-radius: 12px;
                    padding: 4px 12px;
                    font-weight: bold;
                }}
                QLabel:hover {{
                    background-color: {chip_bg.replace('0.05', '0.1').replace('0.15', '0.3')};
                    border: 1px solid {chip_border.replace('0.15', '0.3').replace('0.3', '0.5')};
                }}
            """)

    def enterEvent(self, event):
        self._hovered = True
        self.update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if clicked on chips is handled by chip mousePressEvent?
            # Yes, if child widget handles it, parent won't get it usually, or it propagates?
            # QLabel mousePressEvent consumes it?
            # We'll see. The main card click opens general search.
            # But if I click a chip, the chip's event handler should fire.
            pass
        
        # If the click wasn't on a chip (which consumes it), then open main search
        # But wait, `super().mousePressEvent(event)` calls QWidget's handler which does nothing.
        # I need to know if a child handled it.
        # Actually, if I assign mousePressEvent to the chip labels, they will capture it.
        # But if I click the background, I want this handler to run.
        # So I should move the main search logic here, but ensure it doesn't override chips.
        # If I click a chip, the chip's lambda runs. Does it stop propagation?
        # Standard PyQt event propagation: if child accepts it, parent doesn't get it.
        # So it should be fine.
        
        # However, the original code had:
        # if event.button() == Qt.MouseButton.LeftButton:
        #    query_enc = self.query.replace(" ", "+")
        #    QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={query_enc}"))
        
        # I'll keep it but wrap in try/except or check if child is under mouse?
        # Actually, if the child handles it, this won't be called if the child accepts the event.
        # But QLabel doesn't accept mouse events by default unless we subclass or install event filter?
        # I assigned `lbl.mousePressEvent = lambda ...` which overrides the method.
        # So it should work.
        
        query_enc = self.query.replace(" ", "+")
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={query_enc}"))
        
        super().mousePressEvent(event)

    def sizeHint(self):
        return QSize(660, 100) # Increased height for chips

class TranslateActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, source_text, from_lang, to_lang, translated_text, parent=None):
        super().__init__(parent)
        self.icon_downloaded.connect(self.update_icon)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        
        # Top Row: Icon + Label + Languages
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        
        self.icon_label = QLabel("文")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none;")
        
        self.action_label = QLabel("TRANSLATE")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        
        # Language Badge
        _from_display = "🌐" if from_lang.lower() in ("auto", "") else from_lang.upper()
        self.lang_badge = QLabel(f"{_from_display} ➝ {to_lang.upper()}")
        self.lang_badge.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        self.lang_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(self.lang_badge)
        
        # Source Text
        self.source_label = QLabel(source_text)
        self.source_label.setFont(QFont("Manrope", 12))
        self.source_label.setWordWrap(True)
        
        # Translated Text
        self.translated_label = QLabel(translated_text)
        self.translated_label.setFont(QFont("Instrument Serif", 32, QFont.Weight.Normal))
        self.translated_label.setWordWrap(True)
        self.translated_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        card_layout.addWidget(top_row)
        card_layout.addWidget(self.source_label)
        card_layout.addWidget(self.translated_label)
        
        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        icon_url = "https://www.google.com/s2/favicons?domain=translate.google.com&sz=64"
        threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass
        
    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()
        
    def update_style(self):
        is_dark = self.current_theme == "dark"
        
        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(66, 133, 244, 0.12), stop:1 rgba(255, 255, 255, 0.04))"
            border = "rgba(66, 133, 244, 0.2)"
            title_color = "#FFFFFF"
            desc_color = "rgba(255,255,255,0.7)"
            action_color = "#4285F4"
            badge_bg = "rgba(66, 133, 244, 0.15)"
            badge_color = "#8AB4F8"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(26, 115, 232, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(26, 115, 232, 0.2)"
            title_color = "#050505"
            desc_color = "rgba(0,0,0,0.6)"
            action_color = "#1A73E8"
            badge_bg = "rgba(26, 115, 232, 0.1)"
            badge_color = "#1A73E8"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.translated_label.setStyleSheet(f"color: {title_color}; margin-top: -4px;")
        self.source_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.icon_label.setStyleSheet(f"color: {action_color}; font-size: 14px;")
        self.lang_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 4px 10px; font-weight: bold;")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class CurrencyActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, amount, from_unit, to_unit, converted_value, parent=None):
        super().__init__(parent)
        self.icon_downloaded.connect(self.update_icon)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        
        # Top Row: Icon + Label + Units
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        
        self.icon_label = QLabel("$")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none; font-weight: bold;")
        
        self.action_label = QLabel("CONVERT")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        
        # Unit Badge
        self.unit_badge = QLabel(f"{from_unit.upper()} ➝ {to_unit.upper()}")
        self.unit_badge.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        self.unit_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(self.unit_badge)
        
        # Source Amount
        self.source_label = QLabel(f"{amount} {from_unit.upper()}")
        self.source_label.setFont(QFont("Manrope", 14, QFont.Weight.Medium))
        self.source_label.setWordWrap(True)
        
        # Converted Amount
        self.converted_label = QLabel(f"{converted_value} {to_unit.upper()}")
        self.converted_label.setFont(QFont("Instrument Serif", 36, QFont.Weight.Normal))
        self.converted_label.setWordWrap(True)
        self.converted_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        card_layout.addWidget(top_row)
        card_layout.addWidget(self.source_label)
        card_layout.addWidget(self.converted_label)
        
        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        icon_url = "https://www.google.com/s2/favicons?domain=google.com/finance&sz=64"
        threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass
        
    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()
        
    def update_style(self):
        is_dark = self.current_theme == "dark"
        
        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(30, 215, 96, 0.12), stop:1 rgba(255, 255, 255, 0.04))"
            border = "rgba(30, 215, 96, 0.2)"
            title_color = "#FFFFFF"
            desc_color = "rgba(255,255,255,0.7)"
            action_color = "#1ED760" 
            badge_bg = "rgba(30, 215, 96, 0.15)"
            badge_color = "#1ED760"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(0, 180, 70, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(0, 180, 70, 0.2)"
            title_color = "#050505"
            desc_color = "rgba(0,0,0,0.6)"
            action_color = "#00B446"
            badge_bg = "rgba(0, 180, 70, 0.15)"
            badge_color = "#00963C"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.converted_label.setStyleSheet(f"color: {title_color}; margin-top: -4px;")
        self.source_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.icon_label.setStyleSheet(f"color: {action_color}; font-size: 14px;")
        self.unit_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 4px 10px; font-weight: bold;")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class MapNavigationWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, place_name, address=None, parent=None):
        super().__init__(parent)
        self.place_name = place_name
        self.address = address
        self.icon_downloaded.connect(self.update_icon)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        # Top Row: Icon + Label
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent;")
        
        self.action_label = QLabel("GOOGLE MAPS")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()

        # Place Name
        self.name_label = QLabel(place_name)
        self.name_label.setFont(QFont("Instrument Serif", 32, QFont.Weight.Normal))
        self.name_label.setWordWrap(True)

        # Address/Desc
        self.desc_label = QLabel(address if address else "View on map")
        self.desc_label.setFont(QFont("Manrope", 12))
        self.desc_label.setWordWrap(True)
        
        card_layout.addWidget(top_row)
        card_layout.addWidget(self.name_label)
        card_layout.addWidget(self.desc_label)

        layout.addWidget(self.card)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hovered = False

        self.current_theme = "light"
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        icon_url = "https://www.google.com/s2/favicons?domain=maps.google.com&sz=64"
        threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = self.current_theme == "dark"

        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(234, 67, 53, 0.12), stop:1 rgba(255, 255, 255, 0.04))" if self._hovered else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(234, 67, 53, 0.08), stop:1 rgba(255, 255, 255, 0.02))"
            border = "rgba(234, 67, 53, 0.25)" if self._hovered else "rgba(234, 67, 53, 0.15)"
            title_color = "#FFFFFF"
            desc_color = "rgba(255,255,255,0.7)"
            action_color = "#EA4335"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(234, 67, 53, 0.08), stop:1 rgba(0, 0, 0, 0.02))" if self._hovered else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(234, 67, 53, 0.04), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(234, 67, 53, 0.2)" if self._hovered else "rgba(234, 67, 53, 0.1)"
            title_color = "#050505"
            desc_color = "rgba(0,0,0,0.6)"
            action_color = "#D93025"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.name_label.setStyleSheet(f"color: {title_color}; margin-top: -4px;")
        self.desc_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")

    def enterEvent(self, event):
        self._hovered = True
        self.update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            query_enc = self.place_name.replace(" ", "+")
            QDesktopServices.openUrl(QUrl(f"https://www.google.com/maps/search/{query_enc}"))
        super().mousePressEvent(event)

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class FileActionWidget(QWidget):
    """File action widget with space-to-preview functionality."""
    
    # Signal emitted when space is pressed to show full preview
    preview_requested = pyqtSignal(str, str)  # (path, content)
    
    def __init__(self, filename, path, icon_name=None, parent=None):
        super().__init__(parent)
        self.filename = filename
        self.path = path
        self.preview_expanded = False
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        self.card.setStyleSheet("""
            QWidget#ActionCard {
                background-color: rgba(255, 255, 255, 0.25);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(4)

        # Top Row: Icon + Label + SPACE Hint
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Load icon - Try to get actual file icon from system
        provider = QFileIconProvider()
        info = QFileInfo(path)
        icon = provider.icon(info)
        
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(24, 24))
        else:
            # Fallback to theme icon if system icon fails
            if not icon_name:
                icon_name = self._get_best_icon_name(path)
            
            icon = self._load_file_icon(icon_name, path)
            if not icon.isNull():
                self.icon_label.setPixmap(icon.pixmap(24, 24))
            else:
                self.icon_label.setText("📄")

        action_text = "FOLDER" if os.path.isdir(path) else "FILE"
        self.action_label = QLabel(action_text)
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        
        self.keys = []
        self.hint_labels = []

        def create_key(text):
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setProperty("class", "keycap")
            self.keys.append(lbl)
            return lbl
            
        # Keyboard hints (top right) - Similar to INSTALL widget
        if not os.path.isdir(path):
            # CTRL+S for preview
            ctrl_s_key = create_key("CTRL+S")
            ctrl_s_key.setFixedHeight(24)
            ctrl_s_key.setFixedWidth(50)
            
            preview_label = QLabel("PREVIEW")
            preview_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
            preview_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            preview_label.setFixedHeight(24)
            self.hint_labels.append(preview_label)
            
            top_layout.addWidget(ctrl_s_key)
            top_layout.addWidget(preview_label)
        
        # ENTER hint
        enter_key = create_key("↵")
        enter_key.setFixedHeight(24)
        enter_key.setFixedWidth(30)
        
        open_label = QLabel("OPEN")
        open_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        open_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        open_label.setFixedHeight(24)
        self.hint_labels.append(open_label)
        
        top_layout.addWidget(enter_key)
        top_layout.addWidget(open_label)

        # Title
        self.title_label = QLabel(filename)
        self.title_label.setWordWrap(True)
        self.title_label.setFont(QFont("Instrument Serif", 20, QFont.Weight.Normal))

        # Description (Path)
        display_path = path.replace(os.path.expanduser("~"), "~")
        self.desc_label = QLabel(display_path)
        self.desc_label.setWordWrap(True)
        self.desc_label.setFont(QFont("Manrope", 11, QFont.Weight.Medium))

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.desc_label)

        # Content Peek
        self.peek_label = QLabel()
        self.peek_label.setWordWrap(True)
        self.peek_label.setFont(QFont("Consolas", 10))
        self.peek_label.setHidden(True)
        card_layout.addWidget(self.peek_label)

        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
        
        # Load preview automatically for images
        if not os.path.isdir(path):
            _, ext = os.path.splitext(path)
            if ext.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
                self.load_image_preview()
            else:
                # Start loading content preview in background
                threading.Thread(target=self.load_preview_content, daemon=True).start()
    
    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = self.current_theme == "dark"

        # Colors
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.10)"
            hover_bg = "rgba(255, 255, 255, 0.10)"
            hover_border = "rgba(255, 255, 255, 0.2)"
            
            title_color = "#FFFFFF"
            desc_color = "#CCCCCC"
            action_color = "#AAAAAA"
            hint_color = "#AAAAAA"
            
            icon_bg = "#444444"
            icon_border = "rgba(255,255,255,0.2)"
            
            peek_color = "#DDDDDD"
            peek_bg = "rgba(255,255,255,0.05)"
            
            key_bg = "#444444"
            key_text = "#FFFFFF"
            key_border = "#666666"
            key_border_bottom = "#444444"
        else:
            bg = "rgba(255, 255, 255, 0.25)"
            border = "rgba(255, 255, 255, 0.4)"
            hover_bg = "rgba(255, 255, 255, 0.45)"
            hover_border = "rgba(255, 255, 255, 0.6)"
            
            title_color = "#050505"
            desc_color = "#555555"
            action_color = "#888888"
            hint_color = "#888888"
            
            icon_bg = "#FFFFFF"
            icon_border = "rgba(0,0,0,0.05)"
            
            peek_color = "#777777"
            peek_bg = "rgba(0,0,0,0.03)"
            
            key_bg = "#FFFFFF"
            key_text = "#333333"
            key_border = "#D6D6D6"
            key_border_bottom = "#C0C0C0"

        # Apply
        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.title_label.setStyleSheet(f"color: {title_color}; margin-top: 2px;")
        self.desc_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1.0px;")
        
        self.icon_label.setStyleSheet("""
            background-color: transparent; 
            border: none;
        """)
        
        self.peek_label.setStyleSheet(f"color: {peek_color}; background-color: {peek_bg}; border-radius: 8px; padding: 8px; margin-top: 4px;")
        
        for k in self.keys:
            fs = "12px" if "↵" in k.text() else "8px"
            line_height = "line-height: 24px;" if "↵" in k.text() else ""
            k.setStyleSheet(f"""
                background-color: {key_bg};
                border: 1px solid {key_border};
                border-bottom: 2px solid {key_border_bottom};
                border-radius: 5px;
                color: {key_text};
                padding: 0px;
                font-family: "Manrope";
                font-size: {fs};
                font-weight: 800;
                {line_height}
            """)

        for l in self.hint_labels:
            l.setStyleSheet(f"color: {hint_color}; letter-spacing: 0.5px;")

    def load_preview_content(self):
        """Load preview content for various file types."""
        try:
            _, ext = os.path.splitext(self.path)
            ext = ext.lower()
            
            # Text-based files
            text_extensions = {
                '.txt', '.md', '.py', '.js', '.html', '.css', '.json', 
                '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
                '.sh', '.bat', '.cmd', '.ps1', '.lua', '.rb', '.go',
                '.java', '.cpp', '.c', '.h', '.hpp', '.cs', '.swift',
                '.rs', '.ts', '.tsx', '.jsx', '.vue', '.sql', '.r',
                '.dockerfile', '.env', '.log'
            }
            
            # Image files
            image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.ico', '.svg'}
            
            # JSON/structured data
            data_extensions = {'.json', '.yaml', '.yml', '.toml', '.xml', '.csv'}
            
            if ext in text_extensions or ext in data_extensions:
                with open(self.path, 'r', errors='ignore') as f:
                    content = f.read(3000).strip()  # Read more for preview (3KB)
                    if content:
                        lines = content.split('\n')[:20]  # Show first 20 lines
                        snippet = "\n".join(lines)
                        from PyQt6.QtCore import QMetaObject, Q_ARG
                        QMetaObject.invokeMethod(
                            self.peek_label, "setText", 
                            Qt.ConnectionType.QueuedConnection, 
                            Q_ARG(str, snippet)
                        )
                        QMetaObject.invokeMethod(
                            self.peek_label, "show", 
                            Qt.ConnectionType.QueuedConnection
                        )
        except Exception as e:
            logging.debug(f"Could not load preview for {self.path}: {e}")

    def load_image_preview(self):
        """Load image preview."""
        try:
            pix = QPixmap(self.path)
            if not pix.isNull():
                 scaled = pix.scaledToHeight(250, Qt.TransformationMode.SmoothTransformation)
                 self.peek_label.setPixmap(scaled)
                 self.peek_label.setStyleSheet("background: transparent; padding: 0; margin-top: 4px;")
                 self.peek_label.setHidden(False)
        except: pass

    def get_file_preview(self) -> str:
        """Get file preview content for various file types."""
        try:
            _, ext = os.path.splitext(self.path)
            ext = ext.lower()
            
            # Text-based files
            text_extensions = {
                '.txt', '.md', '.py', '.js', '.html', '.css', '.json', 
                '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
                '.sh', '.bat', '.cmd', '.ps1', '.lua', '.rb', '.go',
                '.java', '.cpp', '.c', '.h', '.hpp', '.cs', '.swift',
                '.rs', '.ts', '.tsx', '.jsx', '.vue', '.sql', '.r',
                '.dockerfile', '.env', '.log', '.csv'
            }
            
            if ext in text_extensions:
                with open(self.path, 'r', errors='ignore') as f:
                    content = f.read(15000)  # Read up to 15KB for preview (much more content)
                    return content
            
            # Binary/media files
            elif ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico', '.svg'}:
                return f"[Image Preview]\nFile: {self.filename}\nType: Image ({ext[1:].upper()})"
            
            elif ext in {'.pdf', '.docx', '.xlsx', '.pptx'}:
                return f"[Document]\nFile: {self.filename}\nType: {ext[1:].upper()} Document"
            
            elif ext in {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'}:
                return f"[Archive]\nFile: {self.filename}\nType: Archive ({ext[1:].upper()})"
            
            elif ext in {'.mp3', '.mp4', '.avi', '.mov', '.mkv', '.flv', '.wav'}:
                return f"[Media File]\nFile: {self.filename}\nType: Media ({ext[1:].upper()})"
            
            elif ext in {'.exe', '.dll', '.so', '.dylib', '.bin'}:
                return f"[Executable]\nFile: {self.filename}\nType: Binary Executable"
            
            else:
                return f"[Binary File]\nFile: {self.filename}\nExtension: {ext}"
        
        except Exception as e:
            return f"[Error]\nCould not load preview: {str(e)}"

    def sizeHint(self):
        w = getattr(self.parent(), 'width', lambda: 660)()
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            # Basic fallback height
            return QSize(660, 96)
        return QSize(660, 96)
    
    def show_context_menu(self, position):
        """Show context menu with copy path option."""
        menu = QMenu(self)
        
        # Copy Path action
        copy_action = menu.addAction("Copy Path")
        copy_action.triggered.connect(self.copy_path_to_clipboard)
        
        menu.addSeparator()
        
        # Open in file explorer
        open_explorer_action = menu.addAction("Open in File Explorer")
        open_explorer_action.triggered.connect(self.open_in_explorer)
        
        # Show menu at cursor position
        menu.exec(self.mapToGlobal(position))
    
    def copy_path_to_clipboard(self):
        """Copy file path to clipboard."""
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.path)
    
    def open_in_explorer(self):
        """Open file in file explorer."""
        import subprocess
        import platform
        
        if platform.system() == 'Windows':
            # Windows: Open in File Explorer
            subprocess.Popen(f'explorer /select,"{self.path}"')
        elif platform.system() == 'Darwin':
            # macOS: Open in Finder
            subprocess.Popen(['open', '-R', self.path])
        else:
            # Linux: Open directory in file manager
            import os
            directory = os.path.dirname(self.path)
            subprocess.Popen(['xdg-open', directory])
    
    def _get_best_icon_name(self, path: str) -> str:
        """Get the best icon name for a file based on extension."""
        _, ext = os.path.splitext(path)
        ext = ext.lower()
        
        # Map file extensions to theme icon names
        icon_map = {
            # Code
            '.py': 'text-x-python',
            '.js': 'text-x-javascript',
            '.ts': 'text-x-typescript',
            '.jsx': 'text-x-javascript',
            '.tsx': 'text-x-typescript',
            '.java': 'text-x-java',
            '.cpp': 'text-x-cpp',
            '.c': 'text-x-c',
            '.h': 'text-x-header',
            '.go': 'text-x-go',
            '.rs': 'text-x-rust',
            '.rb': 'text-x-ruby',
            # Web
            '.html': 'text-html',
            '.css': 'text-css',
            '.xml': 'text-xml',
            # Data
            '.json': 'application-json',
            '.yaml': 'text-yaml',
            '.yml': 'text-yaml',
            '.csv': 'text-csv',
            '.sql': 'text-x-sql',
            # Documents
            '.pdf': 'application-pdf',
            '.txt': 'text-plain',
            '.md': 'text-markdown',
            '.doc': 'application-msword',
            '.docx': 'application-msword',
            '.xls': 'application-vnd.ms-excel',
            '.xlsx': 'application-vnd.ms-excel',
            '.ppt': 'application-vnd.ms-powerpoint',
            '.pptx': 'application-vnd.ms-powerpoint',
            # Images
            '.png': 'image-png',
            '.jpg': 'image-jpeg',
            '.jpeg': 'image-jpeg',
            '.gif': 'image-gif',
            '.svg': 'image-svg+xml',
            '.ico': 'image-x-icon',
            '.bmp': 'image-bmp',
            # Archives
            '.zip': 'application-zip',
            '.rar': 'application-x-rar',
            '.7z': 'application-x-7z-compressed',
            '.tar': 'application-x-tar',
            '.gz': 'application-gzip',
            '.bz2': 'application-x-bzip2',
            # Media
            '.mp3': 'audio-mpeg',
            '.wav': 'audio-wav',
            '.mp4': 'video-mp4',
            '.avi': 'video-avi',
            '.mkv': 'video-x-matroska',
            '.mov': 'video-quicktime',
            # Executable
            '.exe': 'application-x-executable',
            '.sh': 'text-x-shellscript',
            '.bat': 'application-x-bat',
            '.ps1': 'text-x-powershell',
        }
        
        if ext in icon_map:
            return icon_map[ext]
        
        # Fallback based on file type
        if os.path.isdir(path):
            return 'folder'
        return 'text-x-generic'
    
    def _load_file_icon(self, icon_name: str, path: str) -> QIcon:
        """Load file icon with fallback chain."""
        # Try the suggested icon name first
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon
        
        # Try generic fallbacks
        fallbacks = [
            'text-x-generic',
            'document',
            'application-octet-stream',
        ]
        
        for fallback in fallbacks:
            icon = QIcon.fromTheme(fallback)
            if not icon.isNull():
                return icon
        
        # Return empty icon if nothing works
        return QIcon()

class PersonActionWidget(QWidget):
    image_downloaded = pyqtSignal(object)

    def __init__(self, name, description, image_url, url, parent=None):
        super().__init__(parent)
        self.image_url = image_url
        self.url = url or ""
        self.image_downloaded.connect(self.update_image)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        # Shared style with LinkActionWidget - we can duplicate or move to global, duplication is safer for now
        self.card.setStyleSheet("""
            QWidget#ActionCard {
                background-color: rgba(255, 255, 255, 0.25);
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
        """)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(28)

        # Avatar - Portrait Style
        self.avatar = QLabel()
        self.avatar.setFixedSize(110, 150)
        self.avatar.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.5);
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.4);
        """)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Info Layout
        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)
        info_layout.setContentsMargins(0, 6, 0, 6)

        display_name = name.replace(" - Wikipedia", "").strip()
        self.name_label = QLabel(display_name)
        self.name_label.setFont(QFont("Instrument Serif", 32, QFont.Weight.Normal))
        self.name_label.setStyleSheet("color: #111111;")
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.desc_label = QLabel(description)
        self.desc_label.setFont(QFont("Manrope", 14, QFont.Weight.Normal))
        self.desc_label.setStyleSheet("color: #555555; line-height: 1.5;")
        self.desc_label.setWordWrap(True)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Subtle Metadata
        domain = urlparse(url).netloc.replace("www.", "")
        self.link_label = QLabel(f"SOURCE: {domain}" if url else "")
        self.link_label.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        self.link_label.setStyleSheet("color: #999999; letter-spacing: 1px;")

        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.desc_label)
        info_layout.addStretch()
        info_layout.addWidget(self.link_label)

        card_layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(info_layout)

        layout.addWidget(self.card)

        # Initials fallback
        self.avatar.setText(display_name[0])
        
        self.current_theme = "light"
        self.update_style()

        if self.image_url:
            logging.info(f"Starting image download for {name}: {self.image_url}")
            threading.Thread(target=self._download_image, daemon=True).start()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = self.current_theme == "dark"
        
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.10)"
            hover_bg = "rgba(255, 255, 255, 0.10)"
            hover_border = "rgba(255, 255, 255, 0.2)"
            
            name_color = "#FFFFFF"
            desc_color = "#CCCCCC"
            link_color = "#AAAAAA"
            avatar_bg = "#444444"
            avatar_border = "#666666"
            avatar_text = "#FFFFFF"
        else:
            bg = "rgba(255, 255, 255, 0.25)"
            border = "rgba(255, 255, 255, 0.4)"
            hover_bg = "rgba(255, 255, 255, 0.45)"
            hover_border = "rgba(255, 255, 255, 0.6)"
            
            name_color = "#111111"
            desc_color = "#555555"
            link_color = "#999999"
            avatar_bg = "#F7F7F7"
            avatar_border = "#EDEDED"
            avatar_text = "#CCCCCC"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background-color: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        
        self.name_label.setStyleSheet(f"color: {name_color};")
        self.desc_label.setStyleSheet(f"color: {desc_color}; line-height: 1.5;")
        self.link_label.setStyleSheet(f"color: {link_color}; letter-spacing: 1px;")
        
        # Only update avatar style if it's text (not image)
        # We can check if pixmap is set? Or just update anyway?
        # If pixmap is set, we set bg transparent in update_image.
        # But if we update style, we might overwrite it.
        # Let's check if we have a pixmap?
        if not self.avatar.pixmap() or self.avatar.pixmap().isNull():
            self.avatar.setStyleSheet(f"background-color: {avatar_bg}; color: {avatar_text}; font-family: 'Instrument Serif'; font-size: 56px; border-radius: 8px; border: 1px solid {avatar_border};")
        else:
            # Maintain transparent bg for image
            self.avatar.setStyleSheet("background-color: transparent;")

    def _download_image(self):
        try:
            if self.image_url.startswith("data:"): return
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            logging.info(f"Requesting image: {self.image_url}")
            r = requests.get(self.image_url, headers=headers, timeout=10, verify=False)
            logging.info(f"Image download status: {r.status_code}, Content-Type: {r.headers.get('Content-Type')}, Size: {len(r.content)}")
            if r.status_code == 200: 
                self.image_downloaded.emit(r.content)
            else:
                logging.warning(f"Image download failed with status {r.status_code}")
        except Exception as e:
            logging.error(f"Image download exception: {e}")

    def update_image(self, data):
        try:
            if not self.avatar: return
            pixmap = QPixmap()
            success = pixmap.loadFromData(data)
            if not success:
                logging.warning("Failed to load pixmap from data")
                return
            
            if not pixmap.isNull():
                w, h = 110, 150
                rounded = QPixmap(w, h)
                rounded.fill(Qt.GlobalColor.transparent)
                painter = QPainter(rounded)
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    path = QPainterPath()
                    path.addRoundedRect(0, 0, w, h, 8, 8)
                    painter.setClipPath(path)
                    scaled = pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    x = (scaled.width() - w) // 2
                    y = (scaled.height() - h) // 2
                    painter.drawPixmap(-x, -y, scaled)
                finally:
                    painter.end()
                self.avatar.setPixmap(rounded)
                self.avatar.setStyleSheet("background-color: transparent;")
        except: pass

    def sizeHint(self):
        w = 600
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class PlaceActionWidget(PersonActionWidget):
    def __init__(self, name, description, image_url, url, lat, lon, rating=None, rating_count=None, category=None, phone=None, hours=None, parent=None):
        super().__init__(name, description, image_url, url, parent)
        self.lat = lat
        self.lon = lon
        self.rating = rating
        self.rating_count = rating_count
        self.category = category
        self.phone = phone
        self.hours = hours
        
        # ---------------------------------------------------------
        # COMPACT LAYOUT ADJUSTMENTS
        # ---------------------------------------------------------
        # Access the card widget and its layout
        card_layout = self.card.layout()
        if card_layout:
            card_layout.setContentsMargins(12, 12, 12, 12)
            card_layout.setSpacing(16)

        # Adjust Avatar Size for compactness
        self.avatar.setFixedSize(80, 100)
        self.avatar.setStyleSheet("background-color: #F0F0F0; border-radius: 6px; border: 1px solid #E0E0E0;")
        
        if not image_url and lat and lon:
            # Styled map fetch with smaller size
            self.image_url = f"https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=14&size=160x200&markers={lat},{lon},red-pushpin"
            threading.Thread(target=self._download_image, daemon=True).start()

        # Update description with category/address
        full_desc = ""
        if category: full_desc += f"{category} • "
        full_desc += description or ""
        self.desc_label.setText(full_desc.strip(" • "))
        
        # Adjust Fonts for compactness
        self.name_label.setStyleSheet("color: #111111;")
        self.name_label.setFont(QFont("Instrument Serif", 24, QFont.Weight.Normal)) 
        
        self.desc_label.setStyleSheet("color: #555555; line-height: 1.4;")
        self.desc_label.setFont(QFont("Manrope", 12, QFont.Weight.Normal)) 

        # Enhance layout with extra info
        try:
            # Access info_layout (2nd item in card layout)
            info_layout = self.card.layout().itemAt(1).layout()
            
            # Reduce spacing in info_layout
            info_layout.setSpacing(4)
            
            # ---------------------------------------------------------
            # KEY HINTS (Enter / Tab)
            # ---------------------------------------------------------
            hints_layout = QHBoxLayout()
            hints_layout.setSpacing(12)
            hints_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            hints_layout.setContentsMargins(0, 0, 0, 4) 
            
            def create_key_badge(text):
                lbl = QLabel(text)
                lbl.setStyleSheet("""
                    background-color: rgba(0, 0, 0, 0.08);
                    color: #555555;
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
                    font-size: 10px;
                    font-weight: bold;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                """)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                return lbl
            
            def create_action_label(text):
                lbl = QLabel(text)
                lbl.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
                lbl.setStyleSheet("color: #888888;")
                return lbl

            # Enter -> Open directions
            hints_layout.addWidget(create_key_badge("⏎ Return"))
            hints_layout.addWidget(create_action_label("Open directions"))
            
            # Tab -> Open website
            if url:
                hints_layout.addWidget(create_key_badge("⇥ Tab"))
                hints_layout.addWidget(create_action_label("Open website"))
            
            # Insert at the very top of info_layout (index 0)
            info_layout.insertLayout(0, hints_layout)

            # Rating Row
            if rating:
                rating_row = QHBoxLayout()
                rating_row.setSpacing(4)
                rating_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
                
                star_label = QLabel("★")
                star_label.setStyleSheet("color: #F5C518; font-size: 12px;")
                rating_text = f"{rating}"
                if rating_count: rating_text += f" ({rating_count})"
                val_label = QLabel(rating_text)
                val_label.setFont(QFont("Manrope", 11, QFont.Weight.Bold))
                val_label.setStyleSheet("color: #444444;")
                
                rating_row.addWidget(star_label)
                rating_row.addWidget(val_label)
                # Insert after name/desc (hints=0, name=1, desc=2) -> 3
                info_layout.insertLayout(3, rating_row)

            # Details (Phone / Hours)
            details_text = []
            if phone: details_text.append(f"📞 {phone}")
            
            # Check if open now
            if hours:
                import datetime
                today_map = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
                today_idx = datetime.datetime.now().weekday()
                today_name = today_map[today_idx]
                today_hours = hours.get(today_name, "")
                if today_hours:
                    if "Zamknięte" in today_hours:
                        details_text.append(f"🔴 Closed ({today_hours})")
                    else:
                        details_text.append(f"🟢 Open ({today_hours})")

            if details_text:
                details_label = QLabel("  ".join(details_text))
                details_label.setFont(QFont("Manrope", 11))
                details_label.setStyleSheet("color: #666666; margin-top: 2px;")
                info_layout.insertWidget(4, details_label)

            # Full Opening Hours
            if hours and isinstance(hours, dict):
                hours_text = "\n".join([f"{k.capitalize()}: {v}" for k, v in hours.items()])
                hours_label = QLabel(hours_text)
                hours_label.setFont(QFont("Manrope", 10))
                hours_label.setStyleSheet("color: #888888; margin-top: 4px;")
                hours_label.setVisible(True)
                info_layout.insertWidget(5, hours_label)
            
            # Hide the source label since we have the Tab hint
            if hasattr(self, 'link_label'):
                self.link_label.setVisible(False)

        except Exception as e:
            logging.error(f"Failed to add place details: {e}")

    def execute(self):
        # Default action when pressing Enter: Open Google Maps
        self.open_directions()

    def open_directions(self):
        if self.lat and self.lon:
            url = f"https://www.google.com/maps/dir/?api=1&destination={self.lat},{self.lon}"
        else:
            query = self.name_label.text().replace(" ", "+")
            url = f"https://www.google.com/maps/dir/?api=1&destination={query}"
        QDesktopServices.openUrl(QUrl(url))


class TerminalActionWidget(QWidget):
    """
    Displays the result of a terminal command executed by the AI.
    Shows command (monospace), status indicator, and collapsible output.
    """
    def __init__(self, command, description="", output="", error="", success=True, parent=None):
        super().__init__(parent)
        self.command = command
        self.output = output
        self.error = error
        self.success = success
        self._expanded = False
        self.current_theme = "light"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("TerminalCard")

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(4)

        # ── Header row ──────────────────────────────────────────────────────
        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(8)

        status_dot = QLabel("✓" if success else "✗")
        dot_color = "#30D158" if success else "#FF453A"
        status_dot.setStyleSheet(f"color: {dot_color}; font-size: 14px; font-weight: 800; background: transparent;")
        status_dot.setFixedWidth(14)
        status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_text = description.upper() if description else "TERMINAL ACTION"
        label = QLabel(title_text)
        label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        label.setStyleSheet("color: #888888; letter-spacing: 0.5px; background: transparent;")

        h_layout.addWidget(status_dot)
        h_layout.addWidget(label)
        h_layout.addStretch()

        # ── Command ─────────────────────────────────────────────────────────
        cmd_text = command if len(command) <= 72 else command[:69] + "…"
        self.cmd_label = QLabel(f"$ {cmd_text}")
        self.cmd_label.setFont(QFont("Menlo", 11, QFont.Weight.Normal))
        self.cmd_label.setWordWrap(True)
        self.cmd_label.setStyleSheet("color: #DDDDDD; background: transparent;")

        # ── Collapsible output ───────────────────────────────────────────────
        combined_out = ""
        if output:
            combined_out += output
        if error:
            combined_out += ("\n" if combined_out else "") + error
            
        combined_out = combined_out.strip()

        self.output_label = None
        self.hint_label = None
        if combined_out:
            self.output_label = QLabel(combined_out[:2000] + ("…" if len(combined_out) > 2000 else ""))
            self.output_label.setFont(QFont("Menlo", 10))
            self.output_label.setWordWrap(True)
            out_color = "#FF6B6B" if (error and not output) else "#AAAAAA"
            self.output_label.setStyleSheet(
                f"color: {out_color}; background: rgba(0,0,0,0.15); "
                f"border-radius: 6px; padding: 6px 8px;"
            )
            self.output_label.setVisible(False)
            self._expanded = False
            
            self.card.setCursor(Qt.CursorShape.PointingHandCursor)
            
            self.hint_label = QLabel("Click to view output")
            self.hint_label.setFont(QFont("Manrope", 9, QFont.Weight.Medium))
            self.hint_label.setStyleSheet("color: #888888; background: transparent; font-style: italic;")

        card_layout.addWidget(header)
        card_layout.addWidget(self.cmd_label)
        
        if combined_out:
            card_layout.addWidget(self.hint_label)
            card_layout.addWidget(self.output_label)

        layout.addWidget(self.card)
        self.update_style()

    def mousePressEvent(self, event):
        if self.output_label:
            self._expanded = not self._expanded
            self.output_label.setVisible(self._expanded)
            self.hint_label.setVisible(not self._expanded)
            self.updateGeometry()
            parent = self.parent()
            while parent:
                if hasattr(parent, 'updateGeometry'):
                    parent.updateGeometry()
                parent = parent.parent()
        super().mousePressEvent(event)

    def update_style(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "rgba(255,255,255,0.05)"
            border = "rgba(255,255,255,0.10)"
            cmd_color = "#E0E0E0"
        else:
            bg = "rgba(0, 0, 0, 0.04)"
            border = "rgba(0,0,0,0.10)"
            cmd_color = "#333333"
        self.card.setStyleSheet(
            f"QWidget#TerminalCard {{ background-color: {bg}; border-radius: 12px; "
            f"border: 1px solid {border}; }}"
        )
        self.cmd_label.setStyleSheet(f"color: {cmd_color}; background: transparent;")

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0:
                return QSize(w, h + 16)
            return self.layout().sizeHint()
        return super().sizeHint()


# ---------------------------------------------------------------------------
# WikiCardWidget — knowledge card powered by Wikipedia
# ---------------------------------------------------------------------------

_PAGE_TYPE_LABELS = {
    "person": "PERSON",
    "place":  "PLACE",
    "topic":  "WIKIPEDIA",
}

_PAGE_TYPE_COLORS_LIGHT = {
    "person": ("#5B4FCF", "rgba(91,79,207,0.10)", "rgba(91,79,207,0.20)"),   # purple
    "place":  ("#2563EB", "rgba(37,99,235,0.10)", "rgba(37,99,235,0.20)"),   # blue
    "topic":  ("#059669", "rgba(5,150,105,0.10)", "rgba(5,150,105,0.20)"),   # green
}
_PAGE_TYPE_COLORS_DARK = {
    "person": ("#A78BFA", "rgba(167,139,250,0.12)", "rgba(167,139,250,0.25)"),
    "place":  ("#60A5FA", "rgba(96,165,250,0.12)", "rgba(96,165,250,0.25)"),
    "topic":  ("#34D399", "rgba(52,211,153,0.12)", "rgba(52,211,153,0.25)"),
}

_WIKI_ACTIONS = {
    "person": [
        ("youtube", "YouTube",   lambda n, _: f"https://www.youtube.com/results?search_query={n.replace(' ', '+')}"),
        ("document", "In the News", lambda n, _: f"https://news.google.com/search?q={n.replace(' ', '+')}"),
        ("search", "More",      lambda n, _: f"https://duckduckgo.com/?q={n.replace(' ', '+')}"),
    ],
    "place": [
        ("map", "Maps",      lambda n, _: f"https://www.google.com/maps/search/{n.replace(' ', '+')}"),
        ("camera", "Photos",    lambda n, _: f"https://www.google.com/search?tbm=isch&q={n.replace(' ', '+')}"),
        ("calendar", "Travel",    lambda n, _: f"https://www.google.com/search?q=travel+to+{n.replace(' ', '+')}"),
    ],
    "topic": [
        ("youtube", "YouTube",   lambda n, _: f"https://www.youtube.com/results?search_query={n.replace(' ', '+')}"),
        ("search", "Search",   lambda n, _: f"https://duckduckgo.com/?q={n.replace(' ', '+')}"),
        ("document", "More",     lambda _, u: u),
    ],
}


class _WikiActionButton(QWidget):
    """Tiny pill button inside WikiCardWidget."""
    clicked = pyqtSignal(str)   # emits URL
    icon_downloaded = pyqtSignal(bytes)

    def __init__(self, icon_name: str, label: str, url: str, theme: str = "light", parent=None):
        super().__init__(parent)
        self._url = url
        self.current_theme = theme
        self._hovered = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        
        self.icon_downloaded.connect(self._on_icon_data)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(6)
        
        # Icon
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(16, 16)
        self.icon_lbl.setScaledContents(True)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        
        # Label
        self.text_lbl = QLabel(label)
        self.text_lbl.setFont(QFont("Manrope", 11, QFont.Weight.Medium))
        self.text_lbl.setStyleSheet("background: transparent; border: none;")
        
        layout.addWidget(self.icon_lbl)
        layout.addWidget(self.text_lbl)
        
        self._update_style()
        
        # Fetch icon
        if url:
             domain = urlparse(url).netloc
             # Special case for "document" or "search" if needed, but domain usually works
             if "youtube" in icon_name: domain = "youtube.com"
             elif "google" in domain: domain = "google.com"
             
             icon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
             threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def _on_icon_data(self, data):
        px = QPixmap()
        if px.loadFromData(data) and not px.isNull():
            self.icon_lbl.setPixmap(px)

    def _update_style(self):
        dark = self.current_theme == "dark"
        if self._hovered:
            bg   = "rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.10)"
            bdr  = "rgba(255,255,255,0.28)" if dark else "rgba(0,0,0,0.18)"
            col  = "#FFFFFF" if dark else "#111111"
        else:
            bg   = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.055)"
            bdr  = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.10)"
            col  = "#EEEEEE" if dark else "#333333"
            
        self.setStyleSheet(f"""
            QWidget {{
                background: {bg};
                border: 1px solid {bdr};
                border-radius: 14px;
            }}
        """)
        self.text_lbl.setStyleSheet(f"color: {col}; border: none; background: transparent;")
        self.icon_lbl.setStyleSheet("border: none; background: transparent;")

    def set_theme(self, theme: str):
        self.current_theme = theme
        self._update_style()

    def enterEvent(self, e):
        self._hovered = True
        self._update_style()

    def leaveEvent(self, e):
        self._hovered = False
        self._update_style()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._url:
            self.clicked.emit(self._url)


class WikiCardWidget(QWidget):
    """
    Rich knowledge card — shows Wikipedia info for a person, place, or topic.
    Layout:
        [BADGE]
        [thumbnail]  Title (large)
                     Short description (gray)
                     Extract text
                     [↗ Read on Wikipedia]
        — — — — — — — — — — — — — — — — — —
        [action btn]  [action btn]  [action btn]
    """
    image_downloaded = pyqtSignal(object)

    def __init__(self, wiki_data: dict, theme: str = "light", parent=None):
        super().__init__(parent)
        self.wiki_data = wiki_data
        self.current_theme = theme
        self._action_btns: list[_WikiActionButton] = []
        self.image_downloaded.connect(self._on_image_downloaded)

        self._build_ui()
        self._apply_theme()

        # Fetch thumbnail in background
        thumb_url = wiki_data.get("thumbnail", "")
        if thumb_url:
            threading.Thread(target=self._download_image, args=(thumb_url,), daemon=True).start()

    # ------------------------------------------------------------------ build

    def _build_ui(self):
        d = self.wiki_data
        page_type = d.get("page_type", "topic")
        title      = d.get("title", "")
        desc       = d.get("description", "")
        extract    = d.get("extract", "")
        wiki_url   = d.get("url", "")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- card container ----
        self.card = QWidget()
        self.card.setObjectName("WikiCard")
        card_v = QVBoxLayout(self.card)
        card_v.setContentsMargins(16, 14, 16, 14)
        card_v.setSpacing(0)

        # ---- top row: badge ----
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 8)
        self.badge = QLabel(_PAGE_TYPE_LABELS.get(page_type, "WIKIPEDIA"))
        self.badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.badge.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        badge_row.addWidget(self.badge)
        badge_row.addStretch()
        card_v.addLayout(badge_row)

        # ---- content row: thumbnail + text ----
        content_row = QHBoxLayout()
        content_row.setSpacing(16)
        content_row.setContentsMargins(0, 0, 0, 0)

        # Thumbnail
        is_portrait = (page_type == "person")
        thumb_w, thumb_h = (96, 130) if is_portrait else (90, 90)
        self.thumb = QLabel()
        self.thumb.setFixedSize(thumb_w, thumb_h)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setObjectName("WikiThumb")
        # Placeholder initial letter
        initial = title[0].upper() if title else "?"
        self.thumb.setText(initial)
        self.thumb.setFont(QFont("Instrument Serif", 36))
        content_row.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.setContentsMargins(0, 0, 0, 0)

        # Title
        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(QFont("Instrument Serif", 22, QFont.Weight.Normal))
        self.title_lbl.setWordWrap(True)

        # Description (short, e.g. "German theoretical physicist")
        self.desc_lbl = QLabel(desc)
        self.desc_lbl.setFont(QFont("Manrope", 11, QFont.Weight.Normal))
        self.desc_lbl.setWordWrap(True)
        if not desc:
            self.desc_lbl.hide()

        # Extract
        self.extract_lbl = QLabel(extract)
        self.extract_lbl.setFont(QFont("Manrope", 12, QFont.Weight.Normal))
        self.extract_lbl.setWordWrap(True)
        if not extract:
            self.extract_lbl.hide()

        # Source link
        domain = urlparse(wiki_url).netloc or "en.wikipedia.org"
        self.link_lbl = QLabel(f"↗  {domain}")
        self.link_lbl.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        self.link_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.link_lbl.mousePressEvent = lambda _: QDesktopServices.openUrl(QUrl(wiki_url)) if wiki_url else None

        text_col.addWidget(self.title_lbl)
        text_col.addWidget(self.desc_lbl)
        text_col.addSpacing(4)
        text_col.addWidget(self.extract_lbl)
        text_col.addSpacing(6)
        text_col.addWidget(self.link_lbl)
        text_col.addStretch()

        content_row.addLayout(text_col)
        card_v.addLayout(content_row)

        outer.addWidget(self.card)

    # ------------------------------------------------------------------ theme

    def set_theme(self, theme: str):
        self.current_theme = theme
        self._apply_theme()

    def _apply_theme(self):
        d = self.wiki_data
        page_type = d.get("page_type", "topic")
        dark = self.current_theme == "dark"

        color_map = _PAGE_TYPE_COLORS_DARK if dark else _PAGE_TYPE_COLORS_LIGHT
        accent, badge_bg, badge_bdr = color_map.get(page_type, color_map["topic"])

        card_bg     = "rgba(255,255,255,0.06)" if dark else "rgba(255,255,255,0.28)"
        card_border = "rgba(255,255,255,0.12)" if dark else "rgba(255,255,255,0.45)"
        title_col   = "#FFFFFF" if dark else "#0A0A0A"
        desc_col    = "#AAAAAA" if dark else "#666666"
        extract_col = "#C8C8C8" if dark else "#444444"
        link_col    = "#888888" if dark else "#999999"
        divider_col = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.07)"
        thumb_bg    = "#333333" if dark else "#F0F0F0"
        thumb_bdr   = "#555555" if dark else "#DEDEDE"
        thumb_col   = "#888888" if dark else "#BBBBBB"

        self.card.setStyleSheet(f"""
            QWidget#WikiCard {{
                background: {card_bg};
                border: 1px solid {card_border};
                border-radius: 18px;
            }}
        """)
        self.badge.setStyleSheet(f"""
            color: {accent};
            background: {badge_bg};
            border: 1px solid {badge_bdr};
            border-radius: 10px;
            padding: 2px 9px;
            letter-spacing: 0.8px;
        """)
        self.title_lbl.setStyleSheet(f"color: {title_col}; background: transparent;")
        self.desc_lbl.setStyleSheet(f"color: {desc_col}; background: transparent;")
        self.extract_lbl.setStyleSheet(f"color: {extract_col}; background: transparent; line-height: 1.5;")
        self.link_lbl.setStyleSheet(f"color: {link_col}; background: transparent;")

        # Thumb placeholder style (only when no image loaded)
        if not self.thumb.pixmap() or self.thumb.pixmap().isNull():
            r = self.thumb.width() // 2
            if self.wiki_data.get("page_type") == "person":
                r = 8
            self.thumb.setStyleSheet(f"""
                background: {thumb_bg};
                color: {thumb_col};
                border: 1px solid {thumb_bdr};
                border-radius: {r}px;
            """)

        for btn in self._action_btns:
            btn.set_theme(self.current_theme)

    # ------------------------------------------------------------------ image

    def _download_image(self, url: str):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=8, verify=False)
            if r.status_code == 200:
                self.image_downloaded.emit(r.content)
        except Exception as e:
            logging.debug(f"WikiCardWidget: image download failed: {e}")

    def _on_image_downloaded(self, data: bytes):
        try:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data) or pixmap.isNull():
                return
            w = self.thumb.width()
            h = self.thumb.height()
            rounded = QPixmap(w, h)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                path = QPainterPath()
                r = min(w, h) // 2 if self.wiki_data.get("page_type") != "person" else 10
                path.addRoundedRect(0, 0, w, h, r, r)
                painter.setClipPath(path)
                scaled = pixmap.scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter.drawPixmap(-(scaled.width() - w) // 2, -(scaled.height() - h) // 2, scaled)
            finally:
                painter.end()
            self.thumb.setPixmap(rounded)
            self.thumb.setText("")
            self.thumb.setStyleSheet("background: transparent; border: none;")
        except Exception as e:
            logging.debug(f"WikiCardWidget: image render failed: {e}")

    # ------------------------------------------------------------------ size

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0:
                return QSize(w, h + 28)
            return self.layout().sizeHint()
        return super().sizeHint()


# ---------------------------------------------------------------------------
# OGPreviewWidget — rich website preview card using Open Graph metadata
# ---------------------------------------------------------------------------

class OGPreviewWidget(QWidget):
    """
    Displays a website preview card using Open Graph metadata.
    Layout: OG image banner (optional) | favicon + title + domain | description
    """
    _image_downloaded = pyqtSignal(bytes, str)   # (data, role) role: "og" | "favicon"

    def __init__(self, og_data: dict, url: str, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.current_theme = theme
        self._og_data = og_data
        self._url = url
        self._image_downloaded.connect(self._on_image_data)

        from urllib.parse import urlparse as _up
        _p = _up(url)
        self._domain = _p.netloc.replace("www.", "")
        self._site_name = og_data.get("site_name") or self._domain.split(".")[0].title()

        self._build_ui()
        self._apply_theme()

        # Kick off image downloads in background
        og_img = og_data.get("og_image", "")
        fav_url = og_data.get("favicon_url", "")
        if og_img:
            threading.Thread(target=self._dl_image, args=(og_img, "og"), daemon=True).start()
        if fav_url:
            threading.Thread(target=self._dl_image, args=(fav_url, "favicon"), daemon=True).start()

    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("OGCard")
        outer.addWidget(self.card)

        card_v = QVBoxLayout(self.card)
        card_v.setContentsMargins(0, 0, 0, 0)
        card_v.setSpacing(0)

        # --- OG image banner (hidden until image loads) ---
        self.og_banner = QLabel()
        self.og_banner.setObjectName("OGBanner")
        self.og_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.og_banner.setFixedHeight(160)
        self.og_banner.setScaledContents(False)
        self.og_banner.hide()
        card_v.addWidget(self.og_banner)

        # --- Header row: favicon + title + domain ---
        header = QWidget()
        header.setObjectName("OGHeader")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 14, 16, 10)
        h_layout.setSpacing(10)

        self.favicon_lbl = QLabel()
        self.favicon_lbl.setFixedSize(20, 20)
        self.favicon_lbl.setScaledContents(True)
        self._set_placeholder_favicon()
        h_layout.addWidget(self.favicon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_str = (self._og_data.get("og_title") or self._site_name or self._domain)[:80]
        self.title_lbl = QLabel(title_str)
        self.title_lbl.setFont(QFont("Manrope", 14, QFont.Weight.DemiBold))
        self.title_lbl.setWordWrap(False)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_col.addWidget(self.title_lbl)

        self.domain_lbl = QLabel(self._domain)
        self.domain_lbl.setFont(QFont("Manrope", 10))
        text_col.addWidget(self.domain_lbl)

        h_layout.addLayout(text_col)
        h_layout.addStretch()

        # Small "Open ↗" button
        self.open_btn = QLabel(f"Open {self._domain}  ↗")
        self.open_btn.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.mousePressEvent = lambda _: QDesktopServices.openUrl(QUrl(self._url))
        h_layout.addWidget(self.open_btn)

        card_v.addWidget(header)

        # --- Description ---
        desc = (self._og_data.get("og_description") or "").strip()
        if desc:
            self.desc_lbl = QLabel(desc[:200] + ("…" if len(desc) > 200 else ""))
            self.desc_lbl.setFont(QFont("Manrope", 11))
            self.desc_lbl.setWordWrap(True)
            self.desc_lbl.setContentsMargins(16, 0, 16, 14)
            card_v.addWidget(self.desc_lbl)
        else:
            self.desc_lbl = None

    # ------------------------------------------------------------------
    def _apply_theme(self):
        dark = self.current_theme == "dark"
        if dark:
            card_bg    = "rgba(255,255,255,0.05)"
            card_bdr   = "rgba(255,255,255,0.10)"
            title_col  = "#FFFFFF"
            domain_col = "rgba(255,255,255,0.45)"
            desc_col   = "rgba(255,255,255,0.70)"
            open_col   = "rgba(255,255,255,0.55)"
            open_hov   = "#FFFFFF"
        else:
            card_bg    = "rgba(0,0,0,0.04)"
            card_bdr   = "rgba(0,0,0,0.10)"
            title_col  = "#111111"
            domain_col = "rgba(0,0,0,0.40)"
            desc_col   = "rgba(0,0,0,0.65)"
            open_col   = "rgba(0,0,0,0.45)"
            open_hov   = "#000000"

        self.card.setStyleSheet(f"""
            QWidget#OGCard {{
                background: {card_bg};
                border: 1px solid {card_bdr};
                border-radius: 16px;
            }}
            QWidget#OGHeader {{
                background: transparent;
            }}
            QLabel#OGBanner {{
                background: {card_bg};
                border-radius: 16px 16px 0px 0px;
            }}
        """)
        self.title_lbl.setStyleSheet(f"color: {title_col}; background: transparent;")
        self.domain_lbl.setStyleSheet(f"color: {domain_col}; background: transparent;")
        self.open_btn.setStyleSheet(
            f"color: {open_col}; background: transparent; padding: 4px 8px;"
            f" border: 1px solid {card_bdr}; border-radius: 10px;"
        )
        if self.desc_lbl:
            self.desc_lbl.setStyleSheet(f"color: {desc_col}; background: transparent;")

    def set_theme(self, theme: str):
        self.current_theme = theme
        self._apply_theme()

    # ------------------------------------------------------------------
    def _set_placeholder_favicon(self):
        dark = self.current_theme == "dark"
        col = "rgba(255,255,255,0.25)" if dark else "rgba(0,0,0,0.18)"
        self.favicon_lbl.setStyleSheet(
            f"background: {col}; border-radius: 4px; border: none;"
        )

    def _dl_image(self, url: str, role: str):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = resp.read()
            self._image_downloaded.emit(data, role)
        except Exception:
            pass

    def _on_image_data(self, data: bytes, role: str):
        px = QPixmap()
        if not px.loadFromData(data) or px.isNull():
            return
        if role == "favicon":
            scaled = px.scaled(
                20, 20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.favicon_lbl.setPixmap(scaled)
            self.favicon_lbl.setStyleSheet("background: transparent; border: none;")
        elif role == "og":
            w = self.og_banner.width() or 600
            scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
            if scaled.height() > 160:
                scaled = scaled.copy(0, 0, w, 160)
            self.og_banner.setPixmap(scaled)
            self.og_banner.show()
            self._update_list_item_size()

    def _update_list_item_size(self):
        """Force the QListWidget to recalculate item height after banner appears."""
        p = self.parent()
        while p:
            from PyQt6.QtWidgets import QListWidget
            if isinstance(p, QListWidget):
                # Find our item and update its sizeHint
                for i in range(p.count()):
                    item = p.item(i)
                    if p.itemWidget(item) is not None:
                        iw = p.itemWidget(item)
                        if hasattr(iw, "content_widget") and iw.content_widget is self:
                            item.setSizeHint(self.sizeHint())
                            break
                break
            p = p.parent()

    def sizeHint(self):
        h = 86
        if self.desc_lbl:
            h += 20 + self.desc_lbl.sizeHint().height()
        if not self.og_banner.isHidden():
            h += 160
        return QSize(660, h)


# ---------------------------------------------------------------------------
# QuickURLWidget — instant website open card shown as soon as URL is detected
# ---------------------------------------------------------------------------

class QuickURLWidget(QWidget):
    """
    Shown immediately when user types a URL/domain — no network call needed.
    Favicon loads asynchronously from Google's favicon service.
    Clicking anywhere opens the URL.
    """
    _favicon_ready = pyqtSignal(bytes)

    def __init__(self, url: str, domain: str, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.url = url
        self.domain = domain
        self.current_theme = theme
        self._hovered = False
        self._favicon_ready.connect(self._on_favicon)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._build_ui()
        self._apply_theme()

        # Favicon from Google's service — usually very fast (cached CDN)
        fav_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
        threading.Thread(target=self._fetch_fav, args=(fav_url,), daemon=True).start()

    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("QuickURLCard")
        outer.addWidget(self.card)

        row = QHBoxLayout(self.card)
        row.setContentsMargins(18, 16, 18, 16)
        row.setSpacing(13)

        # Favicon container
        fav_wrap = QWidget()
        fav_wrap.setFixedSize(36, 36)
        fav_wrap.setObjectName("FavWrap")
        fav_layout = QHBoxLayout(fav_wrap)
        fav_layout.setContentsMargins(0, 0, 0, 0)
        self.favicon_lbl = QLabel(fav_wrap)
        self.favicon_lbl.setFixedSize(22, 22)
        self.favicon_lbl.setScaledContents(True)
        self.favicon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fav_layout.addWidget(self.favicon_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        row.addWidget(fav_wrap)

        # Text column
        txt = QVBoxLayout()
        txt.setSpacing(3)
        txt.setContentsMargins(0, 0, 0, 0)

        self.title_lbl = QLabel(f"Open {self.domain}")
        self.title_lbl.setFont(QFont("Manrope", 14, QFont.Weight.DemiBold))
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        txt.addWidget(self.title_lbl)

        self.url_lbl = QLabel(self.url)
        self.url_lbl.setFont(QFont("Manrope", 10))
        self.url_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        txt.addWidget(self.url_lbl)

        row.addLayout(txt)
        row.addStretch()

        self.arrow_lbl = QLabel("↗")
        self.arrow_lbl.setFont(QFont("Manrope", 16))
        self.arrow_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.arrow_lbl)

    # ------------------------------------------------------------------
    def _apply_theme(self):
        dark = self.current_theme == "dark"
        if dark:
            card_bg  = "rgba(255,255,255,0.06)"
            card_bdr = "rgba(255,255,255,0.13)"
            fav_bg   = "rgba(255,255,255,0.10)"
            title    = "#FFFFFF"
            url_col  = "rgba(255,255,255,0.42)"
            arrow    = "rgba(255,255,255,0.35)"
            hover_bg = "rgba(255,255,255,0.10)"
        else:
            card_bg  = "rgba(0,0,0,0.04)"
            card_bdr = "rgba(0,0,0,0.10)"
            fav_bg   = "rgba(0,0,0,0.07)"
            title    = "#111111"
            url_col  = "rgba(0,0,0,0.40)"
            arrow    = "rgba(0,0,0,0.32)"
            hover_bg = "rgba(0,0,0,0.07)"

        bg = hover_bg if self._hovered else card_bg
        self.card.setStyleSheet(f"""
            QWidget#QuickURLCard {{
                background: {bg};
                border: 1px solid {card_bdr};
                border-radius: 16px;
            }}
            QWidget#FavWrap {{
                background: {fav_bg};
                border-radius: 10px;
                border: none;
            }}
        """)
        self.title_lbl.setStyleSheet(f"color: {title}; background: transparent;")
        self.url_lbl.setStyleSheet(f"color: {url_col}; background: transparent;")
        self.arrow_lbl.setStyleSheet(f"color: {arrow}; background: transparent;")
        # Favicon inherits FavWrap bg unless pixmap is loaded
        if not self.favicon_lbl.pixmap() or self.favicon_lbl.pixmap().isNull():
            self.favicon_lbl.setStyleSheet(f"background: transparent; border: none;")

    def set_theme(self, theme: str):
        self.current_theme = theme
        self._apply_theme()

    # ------------------------------------------------------------------
    def _fetch_fav(self, url: str):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = r.read()
            self._favicon_ready.emit(data)
        except Exception:
            pass

    def _on_favicon(self, data: bytes):
        px = QPixmap()
        if px.loadFromData(data) and not px.isNull():
            scaled = px.scaled(22, 22,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self.favicon_lbl.setPixmap(scaled)
            self.favicon_lbl.setStyleSheet("background: transparent; border: none;")

    # ------------------------------------------------------------------
    def enterEvent(self, event):
        self._hovered = True
        self._apply_theme()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_theme()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        # Don't open URL here — list's itemClicked → on_entered() opens it once.
        # Opening here would duplicate with on_entered and open the link twice.
        super().mousePressEvent(event)

    def sizeHint(self):
        return QSize(660, 74)

class WeatherActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, location, temp, condition, parent=None):
        super().__init__(parent)
        self.icon_downloaded.connect(self.update_icon)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none;")
        
        self.action_label = QLabel("WEATHER")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        
        self.loc_badge = QLabel(location.upper())
        self.loc_badge.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        self.loc_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(self.loc_badge)
        
        self.temp_label = QLabel(temp)
        self.temp_label.setFont(QFont("Instrument Serif", 36, QFont.Weight.Normal))
        self.temp_label.setWordWrap(True)
        self.temp_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        self.cond_label = QLabel(condition)
        self.cond_label.setFont(QFont("Manrope", 14, QFont.Weight.Medium))
        self.cond_label.setWordWrap(True)
        
        card_layout.addWidget(top_row)
        card_layout.addWidget(self.cond_label)
        card_layout.addWidget(self.temp_label)
        
        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        icon_url = "https://www.google.com/s2/favicons?domain=weather.com&sz=64"
        threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()
        
    def update_style(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(56, 189, 248, 0.12), stop:1 rgba(255, 255, 255, 0.04))"
            border = "rgba(56, 189, 248, 0.2)"
            title_color = "#FFFFFF"
            desc_color = "rgba(255,255,255,0.7)"
            action_color = "#38BDF8" 
            badge_bg = "rgba(56, 189, 248, 0.15)"
            badge_color = "#38BDF8"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(14, 165, 233, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(14, 165, 233, 0.2)"
            title_color = "#050505"
            desc_color = "rgba(0,0,0,0.6)"
            action_color = "#0EA5E9"
            badge_bg = "rgba(14, 165, 233, 0.15)"
            badge_color = "#0284C7"

        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.temp_label.setStyleSheet(f"color: {title_color}; margin-top: -4px;")
        self.cond_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.loc_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 4px 10px; font-weight: bold;")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class UnitActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)

    def __init__(self, amount, from_unit, to_unit, converted_value, parent=None):
        super().__init__(parent)
        self.icon_downloaded.connect(self.update_icon)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none;")
        
        self.action_label = QLabel("CONVERT")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        
        self.unit_badge = QLabel(f"{from_unit.upper()} ➝ {to_unit.upper()}")
        self.unit_badge.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        self.unit_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(self.unit_badge)
        
        self.source_label = QLabel(f"{amount} {from_unit.upper()}")
        self.source_label.setFont(QFont("Manrope", 14, QFont.Weight.Medium))
        self.source_label.setWordWrap(True)
        
        self.converted_label = QLabel(f"{converted_value} {to_unit.upper()}")
        self.converted_label.setFont(QFont("Instrument Serif", 36, QFont.Weight.Normal))
        self.converted_label.setWordWrap(True)
        self.converted_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        card_layout.addWidget(top_row)
        card_layout.addWidget(self.source_label)
        card_layout.addWidget(self.converted_label)
        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()
        self.fetch_icon()

    def fetch_icon(self):
        icon_url = "https://www.google.com/s2/favicons?domain=calculator.net&sz=64"
        threading.Thread(target=self._download_icon, args=(icon_url,), daemon=True).start()

    def _download_icon(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200: self.icon_downloaded.emit(r.content)
        except: pass

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()
        
    def update_style(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(168, 85, 247, 0.12), stop:1 rgba(255, 255, 255, 0.04))"
            border = "rgba(168, 85, 247, 0.2)"
            title_color = "#FFFFFF"
            desc_color = "rgba(255,255,255,0.7)"
            action_color = "#A855F7" 
            badge_bg = "rgba(168, 85, 247, 0.15)"
            badge_color = "#C084FC"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(147, 51, 234, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(147, 51, 234, 0.2)"
            title_color = "#050505"
            desc_color = "rgba(0,0,0,0.6)"
            action_color = "#9333EA"
            badge_bg = "rgba(147, 51, 234, 0.15)"
            badge_color = "#7E22CE"

        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.converted_label.setStyleSheet(f"color: {title_color}; margin-top: -4px;")
        self.source_label.setStyleSheet(f"color: {desc_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.unit_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 4px 10px; font-weight: bold;")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

import string
import random
import io
import qrcode
from PyQt6.QtGui import QColor

class ColorActionWidget(QWidget):
    def __init__(self, color_hex, rgb_val, hsl_val, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(16)
        
        self.color_preview = QWidget()
        self.color_preview.setFixedSize(60, 60)
        self.color_preview.setStyleSheet(f"background-color: {color_hex}; border-radius: 8px; border: 1px solid rgba(128,128,128,0.3);")
        
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        
        self.action_label = QLabel("COLOR PREVIEW")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #ec4899; letter-spacing: 1px;") # Pink
        
        self.hex_label = QLabel(color_hex.upper())
        self.hex_label.setFont(QFont("Instrument Serif", 24, QFont.Weight.Normal))
        self.hex_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        self.desc_label = QLabel(f"RGB: {rgb_val} • HSL: {hsl_val}")
        self.desc_label.setFont(QFont("Manrope", 12))
        self.desc_label.setStyleSheet("color: #888888;")
        self.desc_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        text_col.addWidget(self.action_label)
        text_col.addWidget(self.hex_label)
        text_col.addWidget(self.desc_label)
        
        card_layout.addWidget(self.color_preview)
        card_layout.addLayout(text_col)
        card_layout.addStretch()
        
        layout.addWidget(self.card)
        
        self.current_theme = "dark"
        self.set_theme(self.current_theme)

    def update_content(self, data: dict):
        color_hex = data.get('color_hex', '#000000')
        rgb_val = data.get('rgb_val', '')
        hsl_val = data.get('hsl_val', '')
        self.color_preview.setStyleSheet(f"background-color: {color_hex}; border-radius: 8px; border: 1px solid rgba(128,128,128,0.3);")
        self.hex_label.setText(color_hex.upper())
        self.desc_label.setText(f"RGB: {rgb_val} • HSL: {hsl_val}")

    def set_theme(self, theme):
        self.current_theme = theme
        is_dark = theme == "dark"
        bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(236, 72, 153, 0.12), stop:1 rgba(255, 255, 255, 0.04))" if is_dark else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(236, 72, 153, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
        border = "rgba(236, 72, 153, 0.2)"
        title_color = "#FFFFFF" if is_dark else "#050505"
        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.hex_label.setStyleSheet(f"color: {title_color};")


class TimerActionWidget(QWidget):
    def __init__(self, duration_sec, parent=None):
        super().__init__(parent)
        self.duration = max(1, int(duration_sec))
        self.remaining = self.duration
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        
        self.action_label = QLabel("TIMER")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #f97316; letter-spacing: 1px;")
        
        self.time_label = QLabel(self._format_time(self.remaining))
        self.time_label.setFont(QFont("Instrument Serif", 48, QFont.Weight.Normal))
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.btn = QPushButton("Pause")
        self.btn.setFixedSize(120, 36)
        self.btn.clicked.connect(self._toggle)
        
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.btn)
        btn_row.addStretch()
        
        card_layout.addWidget(self.action_label)
        card_layout.addWidget(self.time_label)
        card_layout.addLayout(btn_row)
        layout.addWidget(self.card)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        
        self.current_theme = "dark"
        self.set_theme(self.current_theme)

    def _format_time(self, s):
        m = s // 60
        sec = s % 60
        return f"{m:02d}:{sec:02d}"

    def _tick(self):
        if self.remaining > 0:
            self.remaining -= 1
            self.time_label.setText(self._format_time(self.remaining))
            if self.remaining == 0:
                self.timer.stop()
                self.time_label.setStyleSheet("color: #ef4444;")
                self.btn.setText("Reset")

    def _toggle(self):
        if self.remaining == 0:
            self.remaining = self.duration
            self.time_label.setStyleSheet(f"color: {'#FFFFFF' if self.current_theme=='dark' else '#000000'};")
            self.time_label.setText(self._format_time(self.remaining))
            self.btn.setText("Pause")
            self.timer.start(1000)
        elif self.timer.isActive():
            self.timer.stop()
            self.btn.setText("Resume")
        else:
            self.timer.start(1000)
            self.btn.setText("Pause")

    def set_theme(self, theme):
        self.current_theme = theme
        is_dark = theme == "dark"
        bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(249, 115, 22, 0.12), stop:1 rgba(255, 255, 255, 0.04))" if is_dark else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(249, 115, 22, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
        border = "rgba(249, 115, 22, 0.2)"
        title_color = "#FFFFFF" if is_dark else "#050505"
        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        if self.remaining > 0:
            self.time_label.setStyleSheet(f"color: {title_color};")
        
        btn_bg = "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.05)"
        btn_hover = "rgba(255,255,255,0.2)" if is_dark else "rgba(0,0,0,0.1)"
        self.btn.setStyleSheet(f"""
            QPushButton {{ background: {btn_bg}; border: 1px solid {border}; border-radius: 18px; color: {title_color}; font-family: 'Manrope'; font-size: 14px; }}
            QPushButton:hover {{ background: {btn_hover}; }}
        """)

class PasswordActionWidget(QWidget):
    def __init__(self, length=16, pwd=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        
        self.action_label = QLabel("PASSWORD GENERATOR")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #10b981; letter-spacing: 1px;")
        
        self.pwd = pwd or self._generate(length)
        self.pwd_label = QLabel(self.pwd)
        self.pwd_label.setFont(QFont("Courier", 24, QFont.Weight.Bold))
        self.pwd_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pwd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        self.btn = QPushButton("Copy")
        self.btn.setFixedSize(120, 36)
        self.btn.clicked.connect(self._copy)
        
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.btn)
        btn_row.addStretch()
        
        card_layout.addWidget(self.action_label)
        card_layout.addSpacing(10)
        card_layout.addWidget(self.pwd_label)
        card_layout.addSpacing(10)
        card_layout.addLayout(btn_row)
        layout.addWidget(self.card)
        
        self.set_theme("dark")

    def _generate(self, length):
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(random.SystemRandom().choice(chars) for _ in range(length))

    def _copy(self):
        QGuiApplication.clipboard().setText(self.pwd)
        self.btn.setText("Copied!")
        QTimer.singleShot(2000, lambda: self.btn.setText("Copy"))

    def set_theme(self, theme):
        is_dark = theme == "dark"
        bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(16, 185, 129, 0.12), stop:1 rgba(255, 255, 255, 0.04))" if is_dark else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(16, 185, 129, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
        border = "rgba(16, 185, 129, 0.2)"
        title_color = "#FFFFFF" if is_dark else "#050505"
        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.pwd_label.setStyleSheet(f"color: {title_color};")
        
        btn_bg = "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.05)"
        btn_hover = "rgba(255,255,255,0.2)" if is_dark else "rgba(0,0,0,0.1)"
        self.btn.setStyleSheet(f"""
            QPushButton {{ background: {btn_bg}; border: 1px solid {border}; border-radius: 18px; color: {title_color}; font-family: 'Manrope'; font-size: 14px; }}
            QPushButton:hover {{ background: {btn_hover}; }}
        """)

class QRActionWidget(QWidget):
    def __init__(self, data_str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(16)
        
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(80, 80)
        self.qr_label.setScaledContents(True)
        self.qr_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.data_str = data_str
        self._generate_qr(data_str)
        self.qr_label.mousePressEvent = self._on_qr_clicked
        
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        
        self.action_label = QLabel("QR CODE")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #3b82f6; letter-spacing: 1px;") # Blue
        
        self.data_label = QLabel(data_str)
        self.data_label.setFont(QFont("Manrope", 12))
        self.data_label.setWordWrap(True)
        self.data_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        text_col.addWidget(self.action_label)
        text_col.addWidget(self.data_label)
        text_col.addStretch()
        
        card_layout.addWidget(self.qr_label)
        card_layout.addLayout(text_col)
        card_layout.addStretch()
        layout.addWidget(self.card)
        self.set_theme("dark")

    def _generate_qr(self, data_str):
        qr = qrcode.QRCode(box_size=4, border=1)
        qr.add_data(data_str)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        px = QPixmap()
        px.loadFromData(buf.getvalue())
        self.qr_label.setPixmap(px)

    def _on_qr_clicked(self, event):
        from PyQt6.QtWidgets import QDialog
        diag = QDialog(self)
        diag.setWindowTitle("QR Code")
        diag.setFixedSize(400, 400)
        diag.setStyleSheet("background-color: white; border-radius: 12px;")
        l = QVBoxLayout(diag)
        lbl = QLabel()
        lbl.setScaledContents(True)
        lbl.setFixedSize(360, 360)
        
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(self.data_str)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        px = QPixmap()
        px.loadFromData(buf.getvalue())
        lbl.setPixmap(px)
        
        l.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        diag.exec()

    def set_theme(self, theme):
        is_dark = theme == "dark"
        bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(59, 130, 246, 0.12), stop:1 rgba(255, 255, 255, 0.04))" if is_dark else "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(59, 130, 246, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
        border = "rgba(59, 130, 246, 0.2)"
        title_color = "#FFFFFF" if is_dark else "#050505"
        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.data_label.setStyleSheet(f"color: {title_color};")

