import os
import threading
import logging
import requests
from urllib.parse import urlparse
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu, QFileIconProvider
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QFileInfo
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QPainterPath, QGuiApplication
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

class AppActionWidget(LinkActionWidget):
    def __init__(self, name, parent=None):
        # We don't have a URL, so pass None or empty
        super().__init__(f"Open {name.title()}", "", "Application", parent)
        
        # Customize icons/text for App Launcher
        self.action_label.setText("LAUNCH")
        self.icon_label.setText("🚀")
        self.update_style()

    def update_style(self):
        super().update_style()
        self.icon_label.setStyleSheet("""
            background-color: #333333; 
            color: #FFFFFF; 
            font-size: 12px; 
            border-radius: 8px;
        """)
        
class InstallActionWidget(LinkActionWidget):
    def __init__(self, name, website_url, parent=None):
        url_for_icon = website_url if website_url else f"https://google.com/search?q={name}"
        super().__init__(f"Install {name}", url_for_icon, "System Package Manager", parent)

        self.action_label.setText("")
        self.icon_label.setText("↓")

        layout = QHBoxLayout(self.action_label)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 3D Keycap Styling - Refined
        def create_key(text):
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setProperty("class", "keycap") # For easier styling if using qss, but here we use inline
            # We'll style them in update_style, but need references?
            # Creating many keys... simpler to style them here and just update colors?
            # Or make a list of keys?
            return lbl
            
        self.keys = []
        def add_key(text):
            k = create_key(text)
            self.keys.append(k)
            layout.addWidget(k)
            return k

        add_key("TAB")

        self.lbl_install = QLabel("INSTALL APP")
        self.lbl_install.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        layout.addWidget(self.lbl_install)

        layout.addSpacing(12)
        add_key("↵")

        self.lbl_web = QLabel("VISIT SITE")
        self.lbl_web.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        layout.addWidget(self.lbl_web)
        layout.addStretch()
        
        self.update_style()

    def update_style(self):
        super().update_style()
        
        # Icon style override
        self.icon_label.setStyleSheet("""
            background-color: #333333; 
            color: #FFFFFF; 
            font-size: 14px; 
            border-radius: 8px;
        """)

        is_dark = self.current_theme == "dark"
        
        text_color = "#FFFFFF" if is_dark else "#000000"
        web_color = "#CCCCCC" if is_dark else "#999999"
        
        if hasattr(self, 'lbl_install'):
            self.lbl_install.setStyleSheet(f"color: {text_color}; letter-spacing: 0.5px;")
            self.lbl_web.setStyleSheet(f"color: {web_color}; letter-spacing: 0.5px;")
            
        # Update keys
        key_bg = "#444444" if is_dark else "#FFFFFF"
        key_text = "#FFFFFF" if is_dark else "#333333"
        key_border = "#666666" if is_dark else "#D6D6D6"
        key_border_bottom = "#444444" if is_dark else "#C0C0C0"
        
        if hasattr(self, 'keys'):
            for k in self.keys:
                k.setStyleSheet(f"""
                    background-color: {key_bg};
                    border: 1px solid {key_border};
                    border-bottom: 2px solid {key_border_bottom};
                    border-radius: 5px;
                    color: {key_text};
                    padding: 3px 8px;
                    font-family: "Manrope";
                    font-size: 10px;
                    font-weight: 800;
                    min-width: 24px;
                """)

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
            background-color: transparent; 
            color: #333333; 
            font-size: 10px; 
            border: none;
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
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.10)"
            icon_color = "#FFFFFF"
            action_color = "#CCCCCC"
        else:
            bg = "rgba(255, 255, 255, 0.25)"
            border = "rgba(255, 255, 255, 0.4)"
            icon_color = "#333333"
            action_color = "#888888"
        
        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background-color: {bg};
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

    def update_style(self):
        super().update_style()
        self.icon_label.setStyleSheet("""
            background-color: #333333; 
            color: #FFFFFF; 
            font-size: 14px; 
            border-radius: 8px;
        """)

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
    def __init__(self, name, description, image_url, url, lat, lon, parent=None):
        super().__init__(name, description, image_url, url, parent)
        if not image_url and lat and lon:
            # Styled map fetch
            self.image_url = f"https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=14&size=220x300&markers={lat},{lon},red-pushpin"
            threading.Thread(target=self._download_image, daemon=True).start()
        self.avatar.setStyleSheet("background-color: #F0F0F0; border-radius: 8px; border: 1px solid #E0E0E0;")


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

        status_dot = QLabel("●")
        dot_color = "#30D158" if success else "#FF453A"
        status_dot.setStyleSheet(f"color: {dot_color}; font-size: 10px; background: transparent;")
        status_dot.setFixedWidth(14)

        label = QLabel("TERMINAL")
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

        # ── Description (optional human-readable summary) ───────────────────
        if description:
            self.desc_label = QLabel(description)
            self.desc_label.setFont(QFont("Manrope", 11))
            self.desc_label.setWordWrap(True)
            self.desc_label.setStyleSheet("color: #AAAAAA; background: transparent;")
        else:
            self.desc_label = None

        # ── Collapsible output ───────────────────────────────────────────────
        combined_out = ""
        if output:
            combined_out += output
        if error:
            combined_out += ("\n" if combined_out else "") + error

        self.output_label = None
        if combined_out:
            self.output_label = QLabel(combined_out[:2000] + ("…" if len(combined_out) > 2000 else ""))
            self.output_label.setFont(QFont("Menlo", 10))
            self.output_label.setWordWrap(True)
            out_color = "#FF6B6B" if (error and not output) else "#AAAAAA"
            self.output_label.setStyleSheet(
                f"color: {out_color}; background: rgba(0,0,0,0.15); "
                f"border-radius: 6px; padding: 6px 8px;"
            )
            self.output_label.setVisible(True)
            self._expanded = True

        card_layout.addWidget(header)
        card_layout.addWidget(self.cmd_label)
        if self.desc_label:
            card_layout.addWidget(self.desc_label)
        if self.output_label:
            card_layout.addWidget(self.output_label)

        layout.addWidget(self.card)
        self.update_style()

    def mousePressEvent(self, event):
        if self.output_label:
            self._expanded = not self._expanded
            self.output_label.setVisible(self._expanded)
            self.updateGeometry()
        super().mousePressEvent(event)

    def update_style(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "rgba(255,255,255,0.05)"
            border = "rgba(255,255,255,0.10)"
            cmd_color = "#E0E0E0"
        else:
            bg = "rgba(30, 30, 30, 0.88)"
            border = "rgba(255,255,255,0.08)"
            cmd_color = "#E8E8E8"
        self.card.setStyleSheet(
            f"QWidget#TerminalCard {{ background-color: {bg}; border-radius: 14px; "
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
