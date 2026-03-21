import os
import threading
import logging
import urllib.request
import requests
from urllib.parse import urlparse
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu,
                              QFileIconProvider, QSizePolicy, QPushButton, QProgressBar,
                              QLineEdit, QTextEdit)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QFileInfo, QTimer, QUrl, QPropertyAnimation, pyqtProperty, QRectF, QEasingCurve
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QPainterPath, QGuiApplication, QCursor, QDesktopServices, QColor, QBrush, QPen
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from src.ui.styles import THEMES
try:
    from src.ui.widgets.math_widget import MathWidget
except ImportError:
    class MathWidget(QWidget):
        """Plain-text fallback when QWebEngineWidgets is unavailable."""
        def __init__(self, result="", equation="", parent=None):
            super().__init__(parent)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            self.result_label = QLabel(str(result))
            self.result_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.result_label.setFont(QFont("Instrument Serif", 32, QFont.Weight.Normal))
            self.eq_label = QLabel(str(equation))
            self.eq_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.eq_label.setStyleSheet("color: #888888; font-size: 12px;")
            layout.addWidget(self.result_label)
            layout.addWidget(self.eq_label)
        def set_theme(self, theme): pass

class LinkActionWidget(QWidget):
    icon_downloaded = pyqtSignal(object)
    description_fetched = pyqtSignal(str)

    def __init__(self, title, url, description, parent=None):
        super().__init__(parent)
        self.url = url
        self.icon_downloaded.connect(self.update_icon)
        self.description_fetched.connect(self._on_description_fetched)

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

        # One-line description (filled async from page meta)
        self.description_label = QLabel("")
        self.description_label.setWordWrap(False)
        self.description_label.setFont(QFont("Manrope", 11, QFont.Weight.Normal))
        self.description_label.setStyleSheet("color: #555555;")
        self.description_label.hide()

        # Description (URL)
        self.desc_label = QLabel(url)
        self.desc_label.setWordWrap(True)
        self.desc_label.setFont(QFont("Manrope", 11, QFont.Weight.Medium))
        if not url:
            self.desc_label.hide()

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.description_label)
        card_layout.addWidget(self.desc_label)

        layout.addWidget(self.card)
        
        self.current_theme = "light"
        self.update_style()

        self.nam = QNetworkAccessManager(self)
        self.fetch_icon()
        self.fetch_description()

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
        if hasattr(self, 'description_label'):
            self.description_label.setStyleSheet(f"color: {desc_color};")
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
            
            req = QNetworkRequest(QUrl(icon_url))
            req.setRawHeader(b"User-Agent", b"Mozilla/5.0")
            
            # Asynchronous request does not block UI
            reply = self.nam.get(req)
            reply.finished.connect(lambda: self._on_icon_reply(reply))
        except Exception: pass

    def _on_icon_reply(self, reply):
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = reply.readAll()
                self.update_icon(data)
        except: pass
        finally:
            reply.deleteLater()

    def update_icon(self, data):
        try:
            if not self.icon_label: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.icon_label.setText("")
                self.icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except: pass

    def fetch_description(self):
        try:
            if not self.url: return
            clean_url = self.url.strip().strip('<>').strip('"').strip("'")
            if not clean_url.startswith("http"):
                clean_url = "https://" + clean_url
            req = QNetworkRequest(QUrl(clean_url))
            req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
            req.setRawHeader(b"Accept", b"text/html")
            reply = self.nam.get(req)
            reply.finished.connect(lambda: self._on_description_reply(reply))
        except Exception: pass

    def _on_description_reply(self, reply):
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                raw = bytes(reply.readAll())
                # Only parse the first 8 KB — the <head> is always near the top
                chunk = raw[:8192].decode("utf-8", errors="ignore")
                desc = self._parse_meta_description(chunk)
                if desc:
                    self.description_fetched.emit(desc)
        except Exception: pass
        finally:
            reply.deleteLater()

    def _parse_meta_description(self, html):
        import re
        # og:description first, then name="description"
        for pattern in [
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{5,300})["\']',
            r'<meta[^>]+content=["\']([^"\']{5,300})["\'][^>]+property=["\']og:description["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{5,300})["\']',
            r'<meta[^>]+content=["\']([^"\']{5,300})["\'][^>]+name=["\']description["\']',
        ]:
            m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if m:
                text = m.group(1).strip()
                # Collapse whitespace
                text = re.sub(r'\s+', ' ', text)
                return text
        return ""

    def _on_description_fetched(self, text):
        try:
            # Truncate to one line
            if len(text) > 120:
                text = text[:117] + "…"
            self.description_label.setText(text)
            self.description_label.show()
        except Exception: pass

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
    Compact confirmation card for a system setting change.
    Fixed height = 72 px → reliable sizeHint, no layout surprises.

    Layout:  [icon square 42×42]  [name / status text]  [animated indicator]
    Visual types:
      toggle   – bool settings  (animated pill switch)
      bar      – volume         (fill bar + speaker icon)
      circular – brightness     (arc progress + sun icon)
    """

    _CIRCULAR = {"brightness"}
    _BAR      = {"volume"}
    _H        = 72  # fixed card height

    def __init__(self, setting, value, label, unit, color_hex, icon_name, success, parent=None):
        super().__init__(parent)
        from PyQt6.QtGui import QColor
        self.setting    = setting
        self.value      = value
        self.label_text = label
        self.unit       = unit
        self.accent     = QColor(color_hex) if color_hex else QColor("#5B8DEF")
        self.icon_name  = icon_name
        self.success    = success
        self.current_theme = "light"
        self._av        = 0.0  # animated value 0.0 → 1.0

        self.is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        self.bool_on    = value if isinstance(value, bool) else True

        if   setting in self._CIRCULAR: self.visual_type = "circular"
        elif setting in self._BAR:      self.visual_type = "bar"
        else:                           self.visual_type = "toggle"

        self._build()
        self._animate()

    # ────────────────────────────── UI build ──────────────────────────────────

    def _build(self):
        from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QFrame
        from PyQt6.QtGui import QFont

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)

        self.card = QFrame()
        self.card.setObjectName("SettingsCard")
        self.card.setFixedHeight(self._H)

        row = QHBoxLayout(self.card)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(14)

        # ── left: coloured icon square ──────────────────────────────────────
        parent_ref = self

        class _IconBox(QWidget):
            def __init__(self):
                super().__init__()
                self.setFixedSize(42, 42)
            def paintEvent(self, _e):
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                parent_ref._draw_icon_box(p, self.width(), self.height())
                p.end()

        self.icon_box = _IconBox()

        # ── centre: name + status ──────────────────────────────────────────
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        self.name_lbl = QLabel(self.label_text)
        self.name_lbl.setFont(QFont("Instrument Serif", 17))

        if self.is_numeric:
            status_str = f"{self.value}{self.unit}"
        elif not self.success:
            status_str = "Failed"
        else:
            status_str = "Enabled" if self.bool_on else "Disabled"

        self.status_lbl = QLabel(status_str)
        self.status_lbl.setFont(QFont("Manrope", 10))

        col.addStretch()
        col.addWidget(self.name_lbl)
        col.addWidget(self.status_lbl)
        col.addStretch()

        # ── right: animated indicator ───────────────────────────────────────
        class _Indicator(QWidget):
            def __init__(self):
                super().__init__()
            def paintEvent(self, _e):
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                parent_ref._draw_indicator(p, self.width(), self.height())
                p.end()

        self.indicator = _Indicator()
        if self.visual_type == "toggle":
            self.indicator.setFixedSize(52, 30)
        else:
            self.indicator.setFixedSize(42, 42)

        row.addWidget(self.icon_box,  0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(col, 1)
        row.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addWidget(self.card)
        self._apply_style()

    # ────────────────────────────── theming ───────────────────────────────────

    def set_theme(self, theme):
        self.current_theme = theme
        self._apply_style()

    def _apply_style(self):
        is_dark = self.current_theme == "dark"
        r, g, b = self.accent.red(), self.accent.green(), self.accent.blue()

        if is_dark:
            bg     = f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba({r},{g},{b},0.16), stop:1 rgba(255,255,255,0.04))"
            border = f"rgba({r},{g},{b},0.28)"
            nc     = "#FFFFFF"
            sc     = "rgba(255,255,255,0.50)"
        else:
            bg     = f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba({r},{g},{b},0.09), stop:1 rgba(255,255,255,0.55))"
            border = f"rgba({r},{g},{b},0.22)"
            nc     = "#111111"
            sc     = "rgba(0,0,0,0.42)"

        self.card.setStyleSheet(f"""
            QFrame#SettingsCard {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 18px;
            }}
        """)
        self.name_lbl.setStyleSheet(f"color: {nc}; background: transparent;")
        self.status_lbl.setStyleSheet(f"color: {sc}; background: transparent;")
        self.icon_box.update()
        self.indicator.update()

    # ─────────────────────────── custom painting ──────────────────────────────

    def _draw_icon_box(self, p, w, h):
        from PyQt6.QtGui import QBrush, QColor, QPainterPath
        from PyQt6.QtCore import QRectF
        # Rounded-square background filled with accent colour
        c = QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 210)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c))
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, w - 2, h - 2), 11, 11)
        p.drawPath(path)
        # White icon on top
        try:
            from src.services.system.macos_settings import _ICON_DRAW_FNS, draw_brightness
            fn = _ICON_DRAW_FNS.get(self.icon_name) or draw_brightness
            from PyQt6.QtGui import QColor as _QC
            fn(p, w / 2, h / 2, 9, _QC(255, 255, 255, 230))
        except Exception:
            pass

    def _draw_indicator(self, p, w, h):
        is_dark = self.current_theme == "dark"
        if   self.visual_type == "toggle":   self._draw_toggle(p, w, h)
        elif self.visual_type == "bar":      self._draw_bar(p, w, h, is_dark)
        else:                                self._draw_arc(p, w, h, is_dark)

    def _draw_toggle(self, p, w, h):
        from PyQt6.QtGui import QBrush, QColor, QPainterPath
        from PyQt6.QtCore import QRectF, QPointF
        t     = self._av
        pad   = 1.5
        radius = (h - pad * 2) / 2

        # track colour: grey → accent
        gray = (155, 158, 165)
        rc = int(gray[0] + (self.accent.red()   - gray[0]) * t)
        gc = int(gray[1] + (self.accent.green() - gray[1]) * t)
        bc = int(gray[2] + (self.accent.blue()  - gray[2]) * t)
        track = QColor(max(0, min(255, rc)), max(0, min(255, gc)), max(0, min(255, bc)), 220)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track))
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(pad, pad, w - pad * 2, h - pad * 2), radius, radius)
        p.drawPath(pill)

        # thumb
        m      = 3.0
        tr     = radius - m
        x_off  = pad + m + tr
        x_on   = pad + (w - pad * 2) - m - tr
        tx     = x_off + (x_on - x_off) * t
        ty     = h / 2.0
        p.setBrush(QBrush(QColor(0, 0, 0, 35)))
        p.drawEllipse(QPointF(tx, ty + 1.2), tr, tr)   # shadow
        p.setBrush(QBrush(QColor(255, 255, 255, 248)))
        p.drawEllipse(QPointF(tx, ty), tr, tr)

    def _draw_bar(self, p, w, h, is_dark):
        from PyQt6.QtGui import QBrush, QColor, QPainterPath
        from PyQt6.QtCore import QRectF
        try:
            from src.services.system.macos_settings import draw_volume
            draw_volume(p, w / 2, h * 0.30, 8, self.accent)
        except Exception:
            pass
        bar_h  = 8.0;  pad = 4.0
        bar_y  = h * 0.64;  bar_w = w - pad * 2
        track  = QColor(255, 255, 255, 20) if is_dark else QColor(0, 0, 0, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track))
        tp = QPainterPath()
        tp.addRoundedRect(QRectF(pad, bar_y, bar_w, bar_h), bar_h / 2, bar_h / 2)
        p.drawPath(tp)
        fw = bar_w * self._av
        if fw >= bar_h:
            p.setBrush(QBrush(self.accent))
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(pad, bar_y, fw, bar_h), bar_h / 2, bar_h / 2)
            p.drawPath(fp)

    def _draw_arc(self, p, w, h, is_dark):
        from PyQt6.QtGui import QPen, QColor
        from PyQt6.QtCore import QRectF
        rect = QRectF(3, 3, w - 6, h - 6)
        track = QColor(255, 255, 255, 18) if is_dark else QColor(0, 0, 0, 12)
        p.setPen(QPen(track, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 225 * 16, -270 * 16)
        if self._av > 0:
            p.setPen(QPen(self.accent, 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 225 * 16, int(-270 * 16 * self._av))
        try:
            from src.services.system.macos_settings import _ICON_DRAW_FNS, draw_brightness
            fn = _ICON_DRAW_FNS.get(self.icon_name) or draw_brightness
            cx, cy = rect.center().x(), rect.center().y()
            fn(p, cx, cy, 9, self.accent)
        except Exception:
            pass

    # ────────────────────────────── animation ─────────────────────────────────

    def _get_av(self): return self._av
    def _set_av(self, v):
        self._av = v
        self.indicator.update()

    from PyQt6.QtCore import pyqtProperty
    animValue = pyqtProperty(float, _get_av, _set_av)

    def _animate(self):
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        self._pa = QPropertyAnimation(self, b"animValue", self)
        self._pa.setDuration(650)
        self._pa.setEasingCurve(QEasingCurve.Type.OutCubic)
        if self.is_numeric:
            self._pa.setStartValue(0.0)
            self._pa.setEndValue(float(self.value) / 100.0)
        elif self.bool_on:
            self._pa.setStartValue(0.0)
            self._pa.setEndValue(1.0)
        else:
            self._pa.setStartValue(1.0)
            self._pa.setEndValue(0.0)
        self._pa.start()

    # ──────────────────────────────── size ────────────────────────────────────

    def sizeHint(self):
        return QSize(660, self._H + 4)


class SettingsAnimationWidget(QWidget):
    """
    Compact, borderless animated confirmation for system setting changes.
    Designed to live INSIDE an AI response bubble (appended below the text).

    Layout:  ── separator ──
             [52×52 canvas]  [Name label]
                             [Status label]

    The 52×52 canvas draws the icon-specific animation:
      dark_mode  → sun ↔ moon morph with stars
      brightness → sun with rays proportional to level
      volume     → speaker with growing wave arcs
      mute       → speaker + X drawing in / fading out
      wifi       → arcs drawing in sequentially
      bluetooth  → bluetooth symbol + pulse rings
      dnd        → moon + stars
      generic    → animated checkmark
    """
    _H = 68   # compact height for use inside a bubble

    def __init__(self, setting, value, label, unit, color_hex, icon_name, success, parent=None):
        super().__init__(parent)
        from PyQt6.QtGui import QColor
        self.setting    = setting
        self.value      = value
        self.label_text = label
        self.unit       = unit
        self.icon_name  = icon_name
        self.success    = success
        self.current_theme = "light"
        self._av          = 0.0
        self._opacity     = 1.0   # always fully opaque; animation handles icon only
        self._anim_started = False
        self.is_numeric   = isinstance(value, (int, float)) and not isinstance(value, bool)
        self.bool_on      = value if isinstance(value, bool) else True
        self._intro       = 0.0

        s = (setting  or "").lower()
        n = (icon_name or "").lower()
        if   "dark"      in s or n == "moon":               self.anim_type = "dark_mode"
        elif "bright"    in s or n == "brightness":          self.anim_type = "brightness"
        elif "mute"      in s:                               self.anim_type = "mute"
        elif "volume"    in s or n == "volume":              self.anim_type = "volume"
        elif "wifi"      in s or "wi-fi" in s or n == "wifi": self.anim_type = "wifi"
        elif "bluetooth" in s or n == "bluetooth":           self.anim_type = "bluetooth"
        elif "dnd"       in s or "disturb" in s or n == "dnd": self.anim_type = "dnd"
        elif "night"     in s:                               self.anim_type = "night_shift"
        else:                                                self.anim_type = "generic"

        self.accent = self._semantic_color()
        self._build()

    # ── accent color ──────────────────────────────────────────────────────────

    def _semantic_color(self):
        from PyQt6.QtGui import QColor
        at = self.anim_type
        if at == "dark_mode":   return QColor("#8A8FE0") if self.bool_on else QColor("#F5C842")
        if at == "brightness":  return QColor("#F5A623")
        if at == "volume":      return QColor("#4BD37B")
        if at == "mute":        return QColor("#E05252") if self.bool_on else QColor("#4BD37B")
        if at == "wifi":        return QColor("#3A9BD5") if self.bool_on else QColor("#888888")
        if at == "bluetooth":   return QColor("#4A90D9") if self.bool_on else QColor("#888888")
        if at == "dnd":         return QColor("#A070E0")
        if at == "night_shift": return QColor("#F5826E")
        return QColor("#5B8DEF")

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
        from PyQt6.QtGui import QFont
        self_ref = self

        # 56×56 transparent canvas — icon floats directly, no container
        _CS = 56

        class _Canvas(QWidget):
            def __init__(self):
                super().__init__()
                self.setFixedSize(_CS, _CS)
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                self.setAutoFillBackground(False)

            def paintEvent(self, _e):
                from PyQt6.QtGui import QPainter, QPixmap
                dpr = self.devicePixelRatio()
                pm  = QPixmap(int(_CS * dpr), int(_CS * dpr))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.GlobalColor.transparent)
                p2  = QPainter(pm)
                p2.setRenderHint(QPainter.RenderHint.Antialiasing)
                self_ref._paint(p2, _CS, _CS)
                p2.end()
                p = QPainter(self)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                p.drawPixmap(0, 0, pm)
                p.end()

        self.canvas = _Canvas()

        # Text: name + status stacked
        if self.is_numeric:
            name_txt   = self.label_text
            status_txt = f"{self.value}{self.unit}"
        elif not self.success:
            name_txt   = self.label_text
            status_txt = "Failed"
        else:
            name_txt   = self.label_text
            status_txt = "On" if self.bool_on else "Off"

        self.name_lbl = QLabel(name_txt)
        self.name_lbl.setFont(QFont("Manrope", 13, QFont.Weight.DemiBold))

        self.status_lbl = QLabel(status_txt)
        self.status_lbl.setFont(QFont("Manrope", 11, QFont.Weight.Normal))

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        text_col.addStretch()
        text_col.addWidget(self.name_lbl)
        text_col.addWidget(self.status_lbl)
        text_col.addStretch()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(text_col, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 10, 4, 6)
        outer.setSpacing(0)
        outer.addLayout(row)

        self.setAutoFillBackground(False)
        self._apply_label_style()

    def set_theme(self, theme):
        self.current_theme = theme
        self._apply_label_style()
        self.canvas.update()

    def _apply_label_style(self):
        dk = self.current_theme == "dark"
        nc = "rgba(255,255,255,0.85)" if dk else "rgba(0,0,0,0.78)"
        sc = "rgba(255,255,255,0.40)" if dk else "rgba(0,0,0,0.38)"
        self.name_lbl.setStyleSheet(  f"color: {nc}; background: transparent;")
        self.status_lbl.setStyleSheet(f"color: {sc}; background: transparent;")

    # ── painting dispatcher ───────────────────────────────────────────────────

    def _paint(self, p, w, h):
        cx, cy = w / 2, h / 2
        t  = self._av
        intro = self._intro
        dk = self.current_theme == "dark"
        at = self.anim_type

        # Draw a beautiful minimalist squircle background
        p.save()
        p.translate(cx, cy)
        # intro scaling for a slight pop effect
        scale = 0.85 + 0.15 * intro
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        bg_alpha = 35 if dk else 20
        c = self.accent
        from PyQt6.QtGui import QColor, QPainterPath
        from PyQt6.QtCore import QRectF
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.red(), c.green(), c.blue(), bg_alpha))
        pad = 4
        # draw a squircle (rounded rect with high border radius)
        p.drawRoundedRect(QRectF(pad, pad, w - pad*2, h - pad*2), 14, 14)

        # Draw icon
        if   at == "dark_mode":   self._p_dark(p, cx, cy, t, intro)
        elif at == "brightness":  self._p_bright(p, cx, cy, t, intro)
        elif at == "volume":      self._p_volume(p, cx, cy, t, intro)
        elif at == "mute":        self._p_mute(p, cx, cy, t, intro)
        elif at == "wifi":        self._p_wifi(p, cx, cy, t, intro)
        elif at == "bluetooth":   self._p_bt(p, cx, cy, t, intro)
        elif at == "dnd":         self._p_dnd(p, cx, cy, t, intro)
        elif at == "night_shift": self._p_night(p, cx, cy, t, intro)
        else:                     self._p_generic(p, cx, cy, t, intro)

        p.restore()

    # ── sun ↔ moon ────────────────────────────────────────────────────────────

    def _p_dark(self, p, cx, cy, t, intro):
        import math
        from PyQt6.QtGui import QColor, QBrush, QPen, QPainterPath
        from PyQt6.QtCore import QPointF
        sz = 14
        def _clamp(v): return max(0.0, min(1.0, v))
        if self.bool_on:
            sun_a  = _clamp(1.0 - t * 2.2)
            moon_a = _clamp(t * 2.0 - 0.8) * intro
            star_a = _clamp(t * 2.5 - 1.5) * intro
        else:
            moon_a = _clamp(1.0 - t * 2.2)
            sun_a  = _clamp(t * 2.0 - 0.8) * intro
            star_a = 0.0

        if sun_a > 0.02:
            sc = QColor(255, 180, 50, int(255 * sun_a))
            p.save()
            scale = 0.5 + 0.5 * sun_a
            p.translate(cx, cy); p.scale(scale, scale); p.translate(-cx, -cy)
            p.setPen(QPen(sc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            ri, ro = sz * 0.55, sz * 0.9
            for i in range(8):
                a = math.radians(i * 45)
                p.drawLine(QPointF(cx + math.cos(a)*ri, cy + math.sin(a)*ri),
                           QPointF(cx + math.cos(a)*ro, cy + math.sin(a)*ro))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc))
            p.drawEllipse(QPointF(cx, cy), sz * 0.38, sz * 0.38)
            p.restore()

        if moon_a > 0.02:
            mc = QColor(140, 160, 255, int(255 * moon_a))
            p.save()
            scale = 0.5 + 0.5 * moon_a
            p.translate(cx, cy); p.scale(scale, scale); p.translate(-cx, -cy)
            path = QPainterPath()
            path.moveTo(cx, cy - sz*0.8)
            path.arcTo(cx - sz*0.8, cy - sz*0.8, sz*1.6, sz*1.6, 90, 180)
            path.arcTo(cx - sz*0.2, cy - sz*0.8, sz*1.4, sz*1.6, 270, -180)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(mc))
            p.drawPath(path)
            p.restore()

        if star_a > 0.02:
            sc2 = QColor(200, 215, 255, int(180 * star_a))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc2))
            for sx, sy, sr in [(cx + sz*0.6, cy - sz*0.5, 1.5),
                               (cx + sz*0.9, cy + sz*0.1, 1.0)]:
                p.drawEllipse(QPointF(sx, sy), sr, sr)

    # ── brightness ────────────────────────────────────────────────────────────

    def _p_bright(self, p, cx, cy, t, intro):
        import math
        from PyQt6.QtGui import QColor, QBrush, QPen
        from PyQt6.QtCore import QPointF
        lv  = t * intro
        sz  = 14
        sc  = QColor(self.accent)
        sc.setAlpha(255)
        ri  = sz * 0.45
        ro  = sz * (0.60 + 0.40 * lv)
        pen = QPen(sc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(cx + math.cos(a)*ri, cy + math.sin(a)*ri),
                       QPointF(cx + math.cos(a)*ro, cy + math.sin(a)*ro))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc))
        r = sz * (0.28 + 0.10 * lv)
        p.drawEllipse(QPointF(cx, cy), r, r)

    # ── volume ────────────────────────────────────────────────────────────────

    def _p_volume(self, p, cx, cy, t, intro):
        from PyQt6.QtGui import QColor, QBrush, QPen, QPainterPath
        from PyQt6.QtCore import QPointF, QRectF
        lv  = t * intro
        sc  = QColor(self.accent)
        sc.setAlpha(255)
        bx  = cx - 8
        sz  = 10
        body = QPainterPath()
        body.moveTo(bx - sz*0.8,    cy - sz*0.4)
        body.lineTo(bx - sz*0.2,    cy - sz*0.4)
        body.lineTo(bx + sz*0.6,    cy - sz*0.9)
        body.lineTo(bx + sz*0.6,    cy + sz*0.9)
        body.lineTo(bx - sz*0.2,    cy + sz*0.4)
        body.lineTo(bx - sz*0.8,    cy + sz*0.4)
        body.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc))
        p.drawPath(body)
        
        wx = bx + sz*0.6
        p.setPen(QPen(sc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        
        for i, (arc_r, spread, min_lv) in enumerate([(sz*0.6, 45, 0.05), (sz*1.1, 40, 0.35), (sz*1.6, 35, 0.65)]):
            if lv < min_lv:
                continue
            alp = int(255 * min(1.0, (lv - min_lv) / 0.2))
            wc = QColor(sc.red(), sc.green(), sc.blue(), alp)
            p.setPen(QPen(wc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            half = int(spread * 16)
            p.drawArc(QRectF(wx - arc_r, cy - arc_r, arc_r*2, arc_r*2), -half, half*2)

    # ── mute ──────────────────────────────────────────────────────────────────

    def _p_mute(self, p, cx, cy, t, intro):
        from PyQt6.QtGui import QColor, QBrush, QPen, QPainterPath
        from PyQt6.QtCore import QPointF
        x_t = t if self.bool_on else (1.0 - t)
        sc  = QColor(self.accent)
        sc.setAlpha(255)
        bx = cx - 6
        sz = 10
        body = QPainterPath()
        body.moveTo(bx - sz*0.8,   cy - sz*0.4)
        body.lineTo(bx - sz*0.2,   cy - sz*0.4)
        body.lineTo(bx + sz*0.6,   cy - sz*0.9)
        body.lineTo(bx + sz*0.6,   cy + sz*0.9)
        body.lineTo(bx - sz*0.2,   cy + sz*0.4)
        body.lineTo(bx - sz*0.8,   cy + sz*0.4)
        body.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc))
        p.drawPath(body)
        
        if x_t > 0:
            xc  = bx + sz * 1.6
            xr  = sz * 0.5
            xc2 = QColor(sc.red(), sc.green(), sc.blue(), int(x_t * 255))
            pen = QPen(xc2, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            # draw X
            if x_t <= 0.5:
                sub = x_t / 0.5
                p.drawLine(QPointF(xc - xr, cy - xr),
                           QPointF(xc - xr + xr*2*sub, cy - xr + xr*2*sub))
            else:
                p.drawLine(QPointF(xc - xr, cy - xr), QPointF(xc + xr, cy + xr))
                sub = (x_t - 0.5) / 0.5
                p.drawLine(QPointF(xc + xr, cy - xr),
                           QPointF(xc + xr - xr*2*sub, cy - xr + xr*2*sub))

    # ── wifi ──────────────────────────────────────────────────────────────────

    def _p_wifi(self, p, cx, cy, t, intro):
        from PyQt6.QtGui import QColor, QBrush, QPen
        from PyQt6.QtCore import QPointF, QRectF
        base_c = self.accent
        oy = cy + 14 
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(base_c))
        p.drawEllipse(QPointF(cx, oy), 3.0, 3.0)
        
        for radius, start_t, fade_dur in [(10, 0.1, 0.3), (18, 0.4, 0.3), (26, 0.7, 0.3)]:
            if t < start_t:
                continue
            arc_t = min(1.0, (t - start_t) / fade_dur)
            alp   = int(255 * arc_t) * intro
            c     = QColor(base_c.red(), base_c.green(), base_c.blue(), int(alp))
            
            spread = 40
            p.setPen(QPen(c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - radius, oy - radius, radius*2, radius*2),
                      (90 + spread) * 16, -spread * 2 * 16)
                      
        if not self.bool_on and t > 0.70:
            xt  = min(1.0, (t - 0.70) / 0.30)
            xr  = 16
            p.setPen(QPen(QColor(185, 80, 80, int(255 * xt)), 2.0,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - xr, cy - xr), QPointF(cx + xr, cy + xr))

    # ── bluetooth ─────────────────────────────────────────────────────────────

    def _p_bt(self, p, cx, cy, t, intro):
        from PyQt6.QtGui import QColor, QPen
        from PyQt6.QtCore import QPointF
        bc = QColor(self.accent)
        al = int(255 * intro * min(1.0, t / 0.40))
        bc.setAlpha(al)
        sz = 14
        pen = QPen(bc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx,          cy - sz),          QPointF(cx,          cy + sz))
        p.drawLine(QPointF(cx,          cy - sz),          QPointF(cx + sz*0.68, cy - sz*0.38))
        p.drawLine(QPointF(cx + sz*0.68, cy - sz*0.38),   QPointF(cx,          cy))
        p.drawLine(QPointF(cx,          cy),               QPointF(cx + sz*0.68, cy + sz*0.38))
        p.drawLine(QPointF(cx + sz*0.68, cy + sz*0.38),   QPointF(cx,          cy + sz))
        if self.bool_on and t > 0.35:
            rt = (t - 0.35) / 0.65
            for i in range(2):
                rr  = 22 + i*10 + rt*8
                ral = int(120 * (1 - rt) * max(0.0, 1 - i*0.4))
                if ral > 0:
                    p.setPen(QPen(QColor(bc.red(), bc.green(), bc.blue(), ral), 1.5))
                    p.drawEllipse(QPointF(cx, cy), rr, rr)

    # ── do not disturb ────────────────────────────────────────────────────────

    def _p_dnd(self, p, cx, cy, t, intro):
        from PyQt6.QtGui import QColor, QBrush, QPainterPath
        from PyQt6.QtCore import QPointF
        sz  = 14
        mc  = QColor(self.accent)
        mc.setAlpha(int(255 * intro))
        scale = 0.65 + 0.35 * t
        p.save()
        p.translate(cx, cy); p.scale(scale, scale); p.translate(-cx, -cy)
        path = QPainterPath()
        path.moveTo(cx, cy - sz*0.8)
        path.arcTo(cx - sz*0.8, cy - sz*0.8, sz*1.6, sz*1.6, 90, 180)
        path.arcTo(cx - sz*0.2, cy - sz*0.8, sz*1.4, sz*1.6, 270, -180)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(mc))
        p.drawPath(path)
        p.restore()
        
        if t > 0.50:
            sa  = int(200 * min(1.0, (t - 0.50) * 2)) * intro
            sc2 = QColor(mc.red(), mc.green(), min(255, mc.blue()+30), int(sa))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc2))
            for sx, sy, sr in [(cx + sz*0.6, cy - sz*0.5, 1.8),
                               (cx + sz*0.9, cy + sz*0.1, 1.2)]:
                p.drawEllipse(QPointF(sx, sy), sr, sr)

    # ── night shift ───────────────────────────────────────────────────────────

    def _p_night(self, p, cx, cy, t, intro):
        import math
        from PyQt6.QtGui import QColor, QBrush, QPen
        from PyQt6.QtCore import QPointF
        lv  = t * intro
        sc  = QColor(self.accent)
        sc.setAlpha(255)
        sz  = 16
        ri, ro = sz*0.55, sz*(0.65 + 0.25*lv)
        pen = QPen(sc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(cx + math.cos(a)*ri, cy + math.sin(a)*ri),
                       QPointF(cx + math.cos(a)*ro, cy + math.sin(a)*ro))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sc))
        r = sz * (0.3 + 0.12*lv)
        p.drawEllipse(QPointF(cx, cy), r, r)

    # ── generic checkmark ─────────────────────────────────────────────────────

    def _p_generic(self, p, cx, cy, t, intro):
        from PyQt6.QtGui import QColor, QPen
        from PyQt6.QtCore import QPointF
        c   = self.accent
        gc  = QColor(c.red(), c.green(), c.blue(), int(255 * intro))
        sz  = 12
        pen = QPen(gc, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        pts = [(cx - sz*0.62, cy + sz*0.12),
               (cx - sz*0.08, cy + sz*0.58),
               (cx + sz*0.62, cy - sz*0.52)]
        if t < 0.5:
            sub = t / 0.5
            p.drawLine(QPointF(*pts[0]),
                       QPointF(pts[0][0] + (pts[1][0]-pts[0][0])*sub,
                               pts[0][1] + (pts[1][1]-pts[0][1])*sub))
        else:
            p.drawLine(QPointF(*pts[0]), QPointF(*pts[1]))
            sub = (t - 0.5) / 0.5
            p.drawLine(QPointF(*pts[1]),
                       QPointF(pts[1][0] + (pts[2][0]-pts[1][0])*sub,
                               pts[1][1] + (pts[2][1]-pts[1][1])*sub))

    # ── Qt animated properties ────────────────────────────────────────────────

    from PyQt6.QtCore import pyqtProperty
    
    def _get_av(self):    return self._av
    def _set_av(self, v):
        self._av = v
        self.canvas.update()
    animValue = pyqtProperty(float, _get_av, _set_av)

    def _get_intro(self): return self._intro
    def _set_intro(self, v):
        self._intro = v
        self.canvas.update()
    introValue = pyqtProperty(float, _get_intro, _set_intro)

    def showEvent(self, event):
        """Start animation the first time the widget becomes visible."""
        super().showEvent(event)
        if not self._anim_started:
            self._anim_started = True
            self._start_animation()
        else:
            self.canvas.update()

    def _start_animation(self):
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
        self._anim_group = QParallelAnimationGroup(self)
        
        intro_anim = QPropertyAnimation(self, b"introValue", self)
        intro_anim.setDuration(450)
        intro_anim.setStartValue(0.0)
        intro_anim.setEndValue(1.0)
        intro_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        
        xfm = QPropertyAnimation(self, b"animValue", self)
        xfm.setDuration(750)
        xfm.setStartValue(0.0)
        xfm.setEndValue(float(self.value) / 100.0 if self.is_numeric else 1.0)
        xfm.setEasingCurve(QEasingCurve.Type.OutExpo)
        
        self._anim_group.addAnimation(intro_anim)
        self._anim_group.addAnimation(xfm)
        self._anim_group.start()

    def sizeHint(self):
        return QSize(400, self._H + 16)


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
    
    def __init__(self, name, website_url, desc="", parent=None):
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
        
        desc_text = desc if desc else f"Install {display_name} using Homebrew."
        self.desc_label = QLabel(desc_text)
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
            background-color: transparent;
            color: #888888; 
            font-size: 14px; 
            font-weight: bold;
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
            border = "rgba(255, 255, 255, 0.1)"
            icon_color = "#FFFFFF"
            action_color = "#AAAAAA"
        else:
            bg = "rgba(255, 255, 255, 0.25)"
            border = "rgba(0, 0, 0, 0.1)"
            icon_color = "#111111"
            action_color = "#666666"
        
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
            font-size: 16px; 
            font-weight: bold;
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

        self.card = QWidget()
        self.card.setObjectName("ActionCard")

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)

        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.icon_label = QLabel("$")
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none; font-weight: bold;")
        top_layout.addWidget(self.icon_label)

        self.action_label = QLabel("CONVERT")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        top_layout.addWidget(self.action_label)

        self.unit_badge = QLabel(f"{from_unit.upper()} → {to_unit.upper()}")
        self.unit_badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.unit_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_layout.addWidget(self.unit_badge)

        top_layout.addStretch()

        self.converted_label = QLabel(f"{amount} {from_unit.upper()}  =  {converted_value} {to_unit.upper()}")
        self.converted_label.setFont(QFont("Manrope", 17, QFont.Weight.Medium))
        self.converted_label.setWordWrap(True)
        self.converted_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(top_row)
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
                self.icon_label.setPixmap(pixmap.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
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
            action_color = "#1ED760"
            badge_bg = "rgba(30, 215, 96, 0.15)"
            badge_color = "#1ED760"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(0, 180, 70, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(0, 180, 70, 0.2)"
            title_color = "#050505"
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

        self.converted_label.setStyleSheet(f"color: {title_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.icon_label.setStyleSheet(f"color: {action_color}; font-size: 13px;")
        self.unit_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 3px 8px; font-weight: bold;")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()

class WorldTimeWidget(QWidget):
    def __init__(self, city, timezone, current_time, date="", parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("ActionCard")

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)

        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.icon_label = QLabel("🕐")
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none;")
        top_layout.addWidget(self.icon_label)

        self.action_label = QLabel("WORLD TIME")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        top_layout.addWidget(self.action_label)

        self.tz_badge = QLabel(timezone)
        self.tz_badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.tz_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_layout.addWidget(self.tz_badge)

        top_layout.addStretch()

        self.time_label = QLabel(current_time)
        self.time_label.setFont(QFont("Instrument Serif", 40, QFont.Weight.Normal))
        self.time_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.city_label = QLabel(f"{city}  ·  {date}" if date else city)
        self.city_label.setFont(QFont("Manrope", 11, QFont.Weight.Normal))

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.time_label)
        card_layout.addWidget(self.city_label)

        layout.addWidget(self.card)

        self.current_theme = "light"
        self.update_style()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        is_dark = self.current_theme == "dark"

        if is_dark:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(0, 150, 200, 0.12), stop:1 rgba(255, 255, 255, 0.04))"
            border = "rgba(0, 150, 200, 0.2)"
            title_color = "#FFFFFF"
            action_color = "#00BFFF"
            badge_bg = "rgba(0, 150, 200, 0.15)"
            badge_color = "#00BFFF"
            sub_color = "#AAAAAA"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(0, 150, 200, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(0, 150, 200, 0.2)"
            title_color = "#050505"
            action_color = "#0096C8"
            badge_bg = "rgba(0, 150, 200, 0.12)"
            badge_color = "#007AA3"
            sub_color = "#666666"

        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background: {bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)

        self.time_label.setStyleSheet(f"color: {title_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.icon_label.setStyleSheet(f"color: {action_color}; font-size: 13px;")
        self.tz_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 3px 8px; font-weight: bold;")
        self.city_label.setStyleSheet(f"color: {sub_color};")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0:
                return QSize(w, h + 35)
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
        info_layout.setContentsMargins(0, 6, 0, 0)

        display_name = name.replace(" - Wikipedia", "").strip()
        # Ensure title case for display if it looks like a name (not already uppercase/mixed)
        if display_name.islower():
            display_name = display_name.title()
            
        logging.info(f"[UI] Displaying Person Card: Name='{display_name}', Desc='{description[:50]}...'")
        
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
        if not url:
            self.link_label.hide()

        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.desc_label)
        info_layout.addWidget(self.link_label)

        card_layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(info_layout)

        layout.addWidget(self.card)

        # Initials fallback
        self.avatar.setText(display_name[0])
        
        self.current_theme = "light"
        self.update_style()
        
        self.nam = QNetworkAccessManager(self)

        if self.image_url:
            QTimer.singleShot(500, self._download_image)

    def fetch_image_for_name(self, name: str):
        """Async: ask the brain for an image URL for this person, then download it."""
        logging.info(f"[PersonCard] fetch_image_for_name called for '{name}'")
        import json as _json
        body = _json.dumps({"name": name}).encode('utf-8')
        req = QNetworkRequest(QUrl("http://127.0.0.1:5555/person_image"))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.nam.post(req, body)
        reply.finished.connect(lambda: self._on_image_search_reply(reply))

    def _on_image_search_reply(self, reply):
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                import json as _json
                data = _json.loads(bytes(reply.readAll()))
                url = data.get('image_url')
                logging.info(f"[PersonCard] person_image response: image_url={url!r}")
                if url:
                    self.image_url = url
                    self._download_image()
            else:
                logging.warning(f"[PersonCard] person_image request failed: {reply.errorString()}")
        except Exception as e:
            logging.warning(f"PersonActionWidget image search reply error: {e}")
        finally:
            reply.deleteLater()

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
        
        self.name_label.setStyleSheet(f"color: {name_color}; margin-bottom: -5px;") # Tighten name-desc gap
        self.desc_label.setStyleSheet(f"color: {desc_color}; line-height: 1.4; margin-bottom: 5px;") # Tighter line height
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
        if not self.image_url or self.image_url.startswith("data:"): return
        
        req = QNetworkRequest(QUrl(self.image_url))
        req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        reply = self.nam.get(req)
        reply.finished.connect(lambda: self._on_reply_finished(reply))

    def _on_reply_finished(self, reply):
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = reply.readAll()
                self.update_image(data)
            else:
                logging.warning(f"Image download failed: {reply.errorString()}")
        except Exception as e:
            logging.error(f"Image download exception: {e}")
        finally:
            reply.deleteLater()

    def update_image(self, data):
        try:
            if not self.avatar: return
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            
            if not pixmap.isNull():
                # Use current size of avatar widget instead of hardcoded 110x150
                w, h = self.avatar.width(), self.avatar.height()
                if w <= 0 or h <= 0: w, h = 110, 150 # Fallback
                
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
                self.avatar.setText("") # Clear text if any
        except Exception as e:
            logging.error(f"Error updating image: {e}")

    def sizeHint(self):
        w = 600
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h)
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
            self._download_image()

        # Update description with category/address
        full_desc = ""
        if category: full_desc += f"{category} • "
        full_desc += description or ""
        self.desc_label.setText(full_desc.strip(" • "))
        
        # Adjust Fonts for compactness (colors handled by update_style)
        self.name_label.setFont(QFont("Instrument Serif", 24, QFont.Weight.Normal)) 
        self.desc_label.setFont(QFont("Manrope", 12, QFont.Weight.Normal)) 

        # Track widgets for styling
        self.hint_badges = []
        self.hint_labels = []
        self.rating_label = None
        self.details_label = None
        self.hours_label = None

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
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.hint_badges.append(lbl)
                return lbl
            
            def create_action_label(text):
                lbl = QLabel(text)
                lbl.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
                self.hint_labels.append(lbl)
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
                self.rating_label = QLabel(rating_text)
                self.rating_label.setFont(QFont("Manrope", 11, QFont.Weight.Bold))
                
                rating_row.addWidget(star_label)
                rating_row.addWidget(self.rating_label)
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
                        details_text.append(f"({today_hours})") # Removed 'Green Open' text, just showing hours

            if details_text:
                self.details_label = QLabel("  ".join(details_text))
                self.details_label.setFont(QFont("Manrope", 11))
                info_layout.insertWidget(4, self.details_label)

            # Full Opening Hours
            if hours and isinstance(hours, dict):
                hours_text = "\n".join([f"{k.capitalize()}: {v}" for k, v in hours.items()])
                self.hours_label = QLabel(hours_text)
                self.hours_label.setFont(QFont("Manrope", 10))
                self.hours_label.setVisible(True)
                info_layout.insertWidget(5, self.hours_label)
            
            # Hide the source label since we have the Tab hint
            if hasattr(self, 'link_label'):
                self.link_label.setVisible(False)

        except Exception as e:
            logging.error(f"Failed to add place details: {e}")

        # Apply theme-aware styles
        self.update_style()

    def update_style(self):
        super().update_style()
        is_dark = self.current_theme == "dark"
        
        if is_dark:
            badge_bg = "rgba(255, 255, 255, 0.15)"
            badge_text = "#EEEEEE"
            badge_border = "rgba(255, 255, 255, 0.2)"
            
            action_text = "#AAAAAA"
            rating_text = "#EEEEEE"
            details_text = "#DDDDDD"
            hours_text = "#BBBBBB"
        else:
            badge_bg = "rgba(0, 0, 0, 0.15)"
            badge_text = "#333333"
            badge_border = "rgba(0, 0, 0, 0.2)"
            
            action_text = "#666666"
            rating_text = "#444444"
            details_text = "#333333"
            hours_text = "#888888"

        # Apply to tracked widgets
        if hasattr(self, 'hint_badges'):
            for badge in self.hint_badges:
                badge.setStyleSheet(f"""
                    background-color: {badge_bg};
                    color: {badge_text};
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
                    font-size: 10px;
                    font-weight: bold;
                    border: 1px solid {badge_border};
                """)
        
        if hasattr(self, 'hint_labels'):
            for lbl in self.hint_labels:
                lbl.setStyleSheet(f"color: {action_text};")
        
        if getattr(self, 'rating_label', None):
            self.rating_label.setStyleSheet(f"color: {rating_text};")
            
        if getattr(self, 'details_label', None):
            self.details_label.setStyleSheet(f"color: {details_text}; margin-top: 2px;")
            
        if getattr(self, 'hours_label', None):
            self.hours_label.setStyleSheet(f"color: {hours_text}; margin-top: 4px;")

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
        self.card.setCursor(Qt.CursorShape.PointingHandCursor)
        outer.addWidget(self.card)

        card_v = QVBoxLayout(self.card)
        card_v.setContentsMargins(0, 0, 0, 0)
        card_v.setSpacing(0)

        # --- OG image banner (hidden until image loads) ---
        self.og_banner = QLabel()
        self.og_banner.setObjectName("OGBanner")
        self.og_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.og_banner.setFixedHeight(180)
        self.og_banner.setScaledContents(False)
        self.og_banner.hide()
        card_v.addWidget(self.og_banner)

        # --- Content area ---
        content = QWidget()
        content.setObjectName("OGContent")
        content_v = QVBoxLayout(content)
        content_v.setContentsMargins(18, 16, 18, 16)
        content_v.setSpacing(8)

        # Site name / domain row with favicon
        site_row = QHBoxLayout()
        site_row.setSpacing(8)

        # Favicon container
        fav_wrap = QWidget()
        fav_wrap.setFixedSize(28, 28)
        fav_wrap.setObjectName("OGFavWrap")
        fav_layout = QHBoxLayout(fav_wrap)
        fav_layout.setContentsMargins(0, 0, 0, 0)
        self.favicon_lbl = QLabel(fav_wrap)
        self.favicon_lbl.setFixedSize(16, 16)
        self.favicon_lbl.setScaledContents(True)
        self.favicon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_placeholder_favicon()
        fav_layout.addWidget(self.favicon_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        site_row.addWidget(fav_wrap)

        self.domain_lbl = QLabel(self._site_name + "  ·  " + self._domain)
        self.domain_lbl.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        site_row.addWidget(self.domain_lbl)
        site_row.addStretch()

        # Arrow indicator
        self.arrow_lbl = QLabel("↗")
        self.arrow_lbl.setFont(QFont("Manrope", 14))
        site_row.addWidget(self.arrow_lbl)

        content_v.addLayout(site_row)

        # Title
        title_str = (self._og_data.get("og_title") or self._site_name or self._domain)[:90]
        self.title_lbl = QLabel(title_str)
        self.title_lbl.setFont(QFont("Manrope", 14, QFont.Weight.DemiBold))
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content_v.addWidget(self.title_lbl)

        # Description
        desc = (self._og_data.get("og_description") or "").strip()
        if desc:
            self.desc_lbl = QLabel(desc[:180] + ("…" if len(desc) > 180 else ""))
            self.desc_lbl.setFont(QFont("Manrope", 11))
            self.desc_lbl.setWordWrap(True)
            content_v.addWidget(self.desc_lbl)
        else:
            self.desc_lbl = None

        card_v.addWidget(content)

        # Remove old open_btn reference
        self.open_btn = None

    # ------------------------------------------------------------------
    def _apply_theme(self):
        dark = self.current_theme == "dark"
        if dark:
            card_bg    = "rgba(255,255,255,0.06)"
            card_bdr   = "rgba(255,255,255,0.12)"
            fav_bg     = "rgba(255,255,255,0.10)"
            title_col  = "#FFFFFF"
            domain_col = "rgba(255,255,255,0.45)"
            desc_col   = "rgba(255,255,255,0.60)"
            arrow_col  = "rgba(255,255,255,0.30)"
            banner_bg  = "rgba(255,255,255,0.04)"
        else:
            card_bg    = "rgba(0,0,0,0.04)"
            card_bdr   = "rgba(0,0,0,0.10)"
            fav_bg     = "rgba(0,0,0,0.07)"
            title_col  = "#111111"
            domain_col = "rgba(0,0,0,0.42)"
            desc_col   = "rgba(0,0,0,0.58)"
            arrow_col  = "rgba(0,0,0,0.28)"
            banner_bg  = "rgba(0,0,0,0.03)"

        self.card.setStyleSheet(f"""
            QWidget#OGCard {{
                background: {card_bg};
                border: 1px solid {card_bdr};
                border-radius: 16px;
            }}
            QWidget#OGContent {{
                background: transparent;
            }}
            QWidget#OGFavWrap {{
                background: {fav_bg};
                border-radius: 8px;
                border: none;
            }}
            QLabel#OGBanner {{
                background: {banner_bg};
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
            }}
        """)
        self.title_lbl.setStyleSheet(f"color: {title_col}; background: transparent;")
        self.domain_lbl.setStyleSheet(f"color: {domain_col}; background: transparent;")
        self.arrow_lbl.setStyleSheet(f"color: {arrow_col}; background: transparent;")
        if self.desc_lbl:
            self.desc_lbl.setStyleSheet(f"color: {desc_col}; background: transparent;")

    def set_theme(self, theme: str):
        self.current_theme = theme
        self._apply_theme()

    # ------------------------------------------------------------------
    def _set_placeholder_favicon(self):
        self.favicon_lbl.setStyleSheet("background: transparent; border: none;")

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
                16, 16,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.favicon_lbl.setPixmap(scaled)
            self.favicon_lbl.setStyleSheet("background: transparent; border: none;")
        elif role == "og":
            w = self.og_banner.width() or 600
            scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
            if scaled.height() > 180:
                scaled = scaled.copy(0, 0, w, 180)
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
        h = 90  # base: site row + title + padding
        if self.desc_lbl:
            h += 8 + self.desc_lbl.sizeHint().height()
        if not self.og_banner.isHidden():
            h += 180
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
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)

        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: transparent; border: none;")
        top_layout.addWidget(self.icon_label)

        self.action_label = QLabel("CONVERT")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        top_layout.addWidget(self.action_label)

        self.unit_badge = QLabel(f"{from_unit.upper()} → {to_unit.upper()}")
        self.unit_badge.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.unit_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_layout.addWidget(self.unit_badge)

        top_layout.addStretch()

        self.conversion_label = QLabel(f"{amount} {from_unit.upper()}  =  {converted_value} {to_unit.upper()}")
        self.conversion_label.setFont(QFont("Manrope", 17, QFont.Weight.Medium))
        self.conversion_label.setWordWrap(True)
        self.conversion_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.conversion_label)

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
                self.icon_label.setPixmap(pixmap.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
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
            action_color = "#A855F7"
            badge_bg = "rgba(168, 85, 247, 0.15)"
            badge_color = "#C084FC"
        else:
            bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(147, 51, 234, 0.08), stop:1 rgba(255, 255, 255, 0.5))"
            border = "rgba(147, 51, 234, 0.2)"
            title_color = "#050505"
            action_color = "#9333EA"
            badge_bg = "rgba(147, 51, 234, 0.15)"
            badge_color = "#7E22CE"

        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.conversion_label.setStyleSheet(f"color: {title_color};")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 1px;")
        self.icon_label.setStyleSheet(f"background-color: transparent; border: none;")
        self.unit_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_color}; border-radius: 8px; padding: 3px 8px; font-weight: bold;")

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
        self.color_preview.update()
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


class SkeletonLine(QWidget):
    """A rounded rectangle that pulses/shimmers to indicate loading."""
    def __init__(self, width_fraction=1.0, height=14, radius=4, parent=None):
        super().__init__(parent)
        self.width_fraction = width_fraction
        self.setFixedHeight(height)
        self.radius = radius
        self.current_theme = "light"
        self._pulse_value = 0.0

        # Animation
        self._anim = QPropertyAnimation(self, b"pulse_value")
        self._anim.setStartValue(0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.0)
        self._anim.setDuration(1500)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.start()

    @pyqtProperty(float)
    def pulse_value(self):
        return self._pulse_value

    @pulse_value.setter
    def pulse_value(self, value):
        self._pulse_value = value
        self.update()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Calculate width based on fraction of parent
        w = self.width()
        if self.width_fraction < 1.0:
            w = int(w * self.width_fraction)

        rect = QRectF(0, 0, w, self.height())

        # Determine colors based on theme
        is_dark = (self.current_theme == "dark")
        base_alpha = 30 if is_dark else 15
        highlight_alpha = 50 if is_dark else 30
        
        color_base = QColor(255, 255, 255, base_alpha) if is_dark else QColor(0, 0, 0, base_alpha)
        color_highlight = QColor(255, 255, 255, highlight_alpha) if is_dark else QColor(0, 0, 0, highlight_alpha)

        # Interpolate color
        r = color_base.red() + (color_highlight.red() - color_base.red()) * self._pulse_value
        g = color_base.green() + (color_highlight.green() - color_base.green()) * self._pulse_value
        b = color_base.blue() + (color_highlight.blue() - color_base.blue()) * self._pulse_value
        a = color_base.alpha() + (color_highlight.alpha() - color_base.alpha()) * self._pulse_value
        
        final_color = QColor(int(r), int(g), int(b), int(a))

        painter.setBrush(QBrush(final_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, self.radius, self.radius)


class PendingActionWidget(QWidget):
    """Skeleton card shown while web search is running."""
    def __init__(self, title="Searching the web", subtitle="", header_text="SEARCHING WEB", parent=None):
        super().__init__(parent)
        self.current_theme = "light"
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        # Header Row: Icon + "WEB SEARCH" badge
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel("⌕")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.action_label = QLabel(header_text)
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()

        # Skeleton Content
        # Replaces Title
        self.skel_title = SkeletonLine(width_fraction=0.6, height=20, radius=6)
        
        # Replaces Subtitle / Description (2 lines)
        self.skel_body1 = SkeletonLine(width_fraction=0.9, height=14, radius=4)
        self.skel_body2 = SkeletonLine(width_fraction=0.75, height=14, radius=4)

        card_layout.addWidget(top_row)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.skel_title)
        card_layout.addSpacing(2)
        card_layout.addWidget(self.skel_body1)
        card_layout.addWidget(self.skel_body2)
        
        layout.addWidget(self.card)

        self.update_style()

    def set_theme(self, theme):
        self.current_theme = theme
        self.skel_title.set_theme(theme)
        self.skel_body1.set_theme(theme)
        self.skel_body2.set_theme(theme)
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = (self.current_theme == "dark")
        card_bg = "rgba(0, 0, 0, 0.22)" if is_dark else "rgba(255, 255, 255, 0.25)"
        border = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(255, 255, 255, 0.40)"
        
        self.card.setStyleSheet(f"""
            QWidget#ActionCard {{
                background-color: {card_bg};
                border-radius: 16px;
                border: 1px solid {border};
            }}
        """)
        self.icon_label.setStyleSheet(f"background: transparent; color: {t['text_secondary']}; font-size: 14px;")
        self.action_label.setStyleSheet(f"color: {t['text_secondary']}; letter-spacing: 0.5px;")


# ---------------------------------------------------------------------------
# OptimizeSystemWidget — system optimization suggestions card
# ---------------------------------------------------------------------------

class OptimizeSystemWidget(QWidget):
    """
    Displays a list of system optimization suggestions with checkboxes.
    User can select which optimizations to apply, then click "Apply selected".
    Each optimization command runs with trust-level permission checking.
    """
    apply_requested = pyqtSignal(list)  # emits list of selected suggestion dicts

    def __init__(self, suggestions: list, parent=None):
        super().__init__(parent)
        self.suggestions = suggestions
        self.current_theme = "light"
        self._checkboxes = []
        self._applied = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("OptimizeCard")

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(10)

        icon_lbl = QLabel("⚡")
        icon_lbl.setFont(QFont("", 18))
        icon_lbl.setStyleSheet("background: transparent; color: #C084FC;")
        icon_lbl.setFixedWidth(24)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel("System Optimization")
        title_lbl.setFont(QFont("Instrument Serif", 18, QFont.Weight.Normal))
        title_lbl.setStyleSheet("color: #FFFFFF; background: transparent;")
        self._title_lbl = title_lbl

        header.addWidget(icon_lbl)
        header.addWidget(title_lbl)
        header.addStretch()
        card_layout.addLayout(header)
        card_layout.addSpacing(12)

        # ── Suggestion rows ────────────────────────────────────────────────
        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        rows_layout = QVBoxLayout(self._rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)

        for i, s in enumerate(suggestions):
            row = self._build_suggestion_row(i, s)
            rows_layout.addWidget(row)

        card_layout.addWidget(self._rows_widget)
        card_layout.addSpacing(14)

        # ── Buttons ────────────────────────────────────────────────────────
        self._btn_row = QWidget()
        self._btn_row.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(self._btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)

        self.apply_btn = QPushButton("Apply selected")
        self.apply_btn.setObjectName("OptimizeApplyBtn")
        self.apply_btn.setFixedHeight(34)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self._on_apply)

        self.cancel_btn = QPushButton("Dismiss")
        self.cancel_btn.setObjectName("OptimizeCancelBtn")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_dismiss)

        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        card_layout.addWidget(self._btn_row)

        # ── Result row (shown after apply) ─────────────────────────────────
        self._result_widget = QWidget()
        self._result_widget.setStyleSheet("background: transparent;")
        self._result_widget.hide()
        rl = QHBoxLayout(self._result_widget)
        rl.setContentsMargins(0, 8, 0, 4)
        rl.setSpacing(8)
        self._r_icon = QLabel("✓")
        self._r_icon.setFont(QFont("", 13))
        self._r_icon.setStyleSheet("background: transparent; color: #4ADE80;")
        self._r_icon.setFixedWidth(20)
        self._r_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._r_text = QLabel("Optimizations applied")
        self._r_text.setFont(QFont("Manrope", 10, QFont.Weight.DemiBold))
        self._r_text.setStyleSheet("background: transparent; color: #4ADE80;")
        rl.addWidget(self._r_icon)
        rl.addWidget(self._r_text, 1)
        card_layout.addWidget(self._result_widget)

        layout.addWidget(self.card)
        self.update_style()

    def _build_suggestion_row(self, index: int, suggestion: dict) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(4, 6, 4, 6)
        rl.setSpacing(10)

        # Custom checkbox using QPushButton
        cb = QPushButton()
        cb.setObjectName(f"OptCB_{index}")
        cb.setCheckable(True)
        cb.setChecked(True)
        cb.setFixedSize(20, 20)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checkboxes.append(cb)

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)

        title = QLabel(suggestion.get("title", "Optimization"))
        title.setFont(QFont("Manrope", 11, QFont.Weight.DemiBold))
        title.setStyleSheet("background: transparent; color: #FFFFFF;")
        title.setWordWrap(True)
        self._style_title = getattr(self, '_style_titles', [])
        if not hasattr(self, '_style_titles'):
            self._style_titles = []
        self._style_titles.append(title)

        desc_text = suggestion.get("description", "")
        if desc_text:
            desc = QLabel(desc_text)
            desc.setFont(QFont("Manrope", 9))
            desc.setStyleSheet("background: transparent; color: #888888;")
            desc.setWordWrap(True)
            if not hasattr(self, '_style_descs'):
                self._style_descs = []
            self._style_descs.append(desc)

        # Impact badge
        impact = suggestion.get("impact", "low")
        impact_colors = {
            "low": "#4ADE80",
            "medium": "#FBBF24",
            "high": "#F87171",
        }
        badge_color = impact_colors.get(impact, "#888888")

        rl.addWidget(cb)
        text_w = QWidget()
        text_w.setStyleSheet("background: transparent;")
        text_w.setLayout(text_col)
        text_col.addWidget(title)
        if desc_text:
            text_col.addWidget(desc)
        rl.addWidget(text_w, 1)

        # Small impact dot
        dot = QLabel("●")
        dot.setFont(QFont("", 8))
        dot.setStyleSheet(f"background: transparent; color: {badge_color};")
        dot.setFixedWidth(14)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setToolTip(f"{impact} impact")
        rl.addWidget(dot)

        return row

    def _on_apply(self):
        if self._applied:
            return
        selected = []
        for i, cb in enumerate(self._checkboxes):
            if cb.isChecked() and i < len(self.suggestions):
                selected.append(self.suggestions[i])
        if not selected:
            return
        self._applied = True
        self._rows_widget.hide()
        self._btn_row.hide()
        self._result_widget.show()
        self.apply_requested.emit(selected)
        self._resize_parent()

    def _on_dismiss(self):
        self._rows_widget.hide()
        self._btn_row.hide()
        self._r_icon.setText("—")
        self._r_icon.setStyleSheet("background: transparent; color: #888888;")
        self._r_text.setText("Dismissed")
        self._r_text.setStyleSheet("background: transparent; color: #888888;")
        self._result_widget.show()
        self._resize_parent()

    def _resize_parent(self):
        self.updateGeometry()
        QTimer.singleShot(40, self._do_resize)

    def _do_resize(self):
        try:
            win = self.window()
            if win is None:
                return
            lw = getattr(win, 'list_widget', None)
            if lw is not None:
                for i in range(lw.count()):
                    iw = lw.itemWidget(lw.item(i))
                    actual = getattr(iw, 'content_widget', iw)
                    if actual is self:
                        lw.item(i).setSizeHint(self.sizeHint())
                        break
            if hasattr(win, 'adjust_window_height'):
                win.adjust_window_height()
        except RuntimeError:
            pass

    def show_error(self, msg: str):
        self._r_icon.setText("✕")
        self._r_icon.setStyleSheet("background: transparent; color: #F87171;")
        self._r_text.setText(msg)
        self._r_text.setStyleSheet("background: transparent; color: #F87171;")

    def update_style(self):
        is_dark = self.current_theme == "dark"
        t = THEMES.get(self.current_theme, THEMES["light"])

        if is_dark:
            card_bg = "rgba(255,255,255,0.04)"
            border = "rgba(255,255,255,0.10)"
        else:
            card_bg = "rgba(0,0,0,0.03)"
            border = "rgba(0,0,0,0.08)"

        self.card.setStyleSheet(
            f"QWidget#OptimizeCard {{ background-color: {card_bg}; "
            f"border-radius: 14px; border: 1px solid {border}; }}"
        )

        tc = "#FFFFFF" if is_dark else "#111111"
        sc = "#888888" if is_dark else "#999999"
        self._title_lbl.setStyleSheet(f"color: {tc}; background: transparent;")

        for title in getattr(self, '_style_titles', []):
            title.setStyleSheet(f"background: transparent; color: {tc};")
        for desc in getattr(self, '_style_descs', []):
            desc.setStyleSheet(f"background: transparent; color: {sc};")

        # Checkbox style
        checked_bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5B21B6,stop:1 #C026D3)"
        unchecked_bg = "rgba(255,255,255,0.08)" if is_dark else "rgba(0,0,0,0.06)"
        unchecked_border = "rgba(255,255,255,0.20)" if is_dark else "rgba(0,0,0,0.15)"
        for cb in self._checkboxes:
            cb.setStyleSheet(f"""
                QPushButton {{
                    background: {unchecked_bg};
                    border: 1px solid {unchecked_border};
                    border-radius: 5px;
                    font-size: 11px;
                    color: transparent;
                }}
                QPushButton:checked {{
                    background: {checked_bg};
                    border: none;
                    color: #FFFFFF;
                }}
                QPushButton:checked::after {{ content: "✓"; }}
            """)
            # Set text for checked state visual
            cb.setText("✓" if cb.isChecked() else "")
            cb.toggled.connect(lambda checked, b=cb: b.setText("✓" if checked else ""))

        # Button styles
        self.apply_btn.setStyleSheet(f"""
            QPushButton#OptimizeApplyBtn {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #5B21B6, stop:1 #C026D3);
                color: #FFFFFF; border: none; border-radius: 9px;
                font-family: Manrope; font-size: 11px; font-weight: 700;
                padding: 0 18px;
            }}
            QPushButton#OptimizeApplyBtn:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #6D28D9, stop:1 #D946EF);
            }}
        """)
        cc = "#AAAAAA" if is_dark else "#666666"
        ch = "#FFFFFF" if is_dark else "#111111"
        self.cancel_btn.setStyleSheet(f"""
            QPushButton#OptimizeCancelBtn {{
                background: transparent; color: {cc};
                border: none; border-radius: 9px;
                font-family: Manrope; font-size: 11px; font-weight: 500;
                padding: 0 14px;
            }}
            QPushButton#OptimizeCancelBtn:hover {{
                color: {ch};
            }}
        """)

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
# CalendarActionWidget — shows upcoming calendar events
# ---------------------------------------------------------------------------

class CalendarActionWidget(QWidget):
    def __init__(self, events_text, parent=None):
        super().__init__(parent)
        self.current_theme = "light"
        self.events_text = events_text or "No upcoming events."

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Header
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.action_label = QLabel("CALENDAR")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        open_btn = QPushButton("Open Calendar")
        open_btn.setObjectName("CalendarOpenBtn")
        open_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_btn.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        open_btn.clicked.connect(self._open_calendar)

        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(open_btn)

        # Events content
        self.content_label = QLabel(self.events_text)
        self.content_label.setFont(QFont("Manrope", 12))
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.content_label)
        layout.addWidget(self.card)
        self.update_style()

    def _open_calendar(self):
        import subprocess
        subprocess.Popen(["open", "-a", "Calendar"])

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        bg = "rgba(0, 0, 0, 0.22)" if is_dark else "rgba(255, 255, 255, 0.25)"
        border = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(255, 255, 255, 0.40)"
        text_color = "#FFFFFF" if is_dark else "#050505"
        accent = "#38BDF8" if is_dark else "#0EA5E9"
        btn_bg = "rgba(56,189,248,0.15)" if is_dark else "rgba(14,165,233,0.12)"

        self.card.setStyleSheet(f"QWidget#ActionCard {{ background-color: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.action_label.setStyleSheet(f"color: {accent}; letter-spacing: 1px;")
        self.content_label.setStyleSheet(f"color: {text_color}; line-height: 1.5;")
        btn = self.findChild(QPushButton, "CalendarOpenBtn")
        if btn:
            btn.setStyleSheet(
                f"QPushButton#CalendarOpenBtn {{ background: {btn_bg}; color: {accent}; border: none; border-radius: 8px; padding: 4px 12px; }}"
                f"QPushButton#CalendarOpenBtn:hover {{ background: {accent}; color: white; }}"
            )

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()


# ---------------------------------------------------------------------------
# EmailActionWidget — shows unread emails
# ---------------------------------------------------------------------------

class EmailActionWidget(QWidget):
    def __init__(self, emails_text, parent=None):
        super().__init__(parent)
        self.current_theme = "light"
        self.emails_text = emails_text or "No unread emails."

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Header
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.action_label = QLabel("EMAILS")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        open_btn = QPushButton("Open Mail")
        open_btn.setObjectName("EmailOpenBtn")
        open_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_btn.setFont(QFont("Manrope", 10, QFont.Weight.Medium))
        open_btn.clicked.connect(self._open_mail)

        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(open_btn)

        # Email content
        self.content_label = QLabel(self.emails_text)
        self.content_label.setFont(QFont("Manrope", 12))
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.content_label)
        layout.addWidget(self.card)
        self.update_style()

    def _open_mail(self):
        import subprocess
        subprocess.Popen(["open", "-a", "Mail"])

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        bg = "rgba(0, 0, 0, 0.22)" if is_dark else "rgba(255, 255, 255, 0.25)"
        border = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(255, 255, 255, 0.40)"
        text_color = "#FFFFFF" if is_dark else "#050505"
        accent = "#A78BFA" if is_dark else "#7C3AED"
        btn_bg = "rgba(167,139,250,0.15)" if is_dark else "rgba(124,58,237,0.12)"

        self.card.setStyleSheet(f"QWidget#ActionCard {{ background-color: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.action_label.setStyleSheet(f"color: {accent}; letter-spacing: 1px;")
        self.content_label.setStyleSheet(f"color: {text_color}; line-height: 1.5;")
        btn = self.findChild(QPushButton, "EmailOpenBtn")
        if btn:
            btn.setStyleSheet(
                f"QPushButton#EmailOpenBtn {{ background: {btn_bg}; color: {accent}; border: none; border-radius: 8px; padding: 4px 12px; }}"
                f"QPushButton#EmailOpenBtn:hover {{ background: {accent}; color: white; }}"
            )

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()


# ---------------------------------------------------------------------------
# AnswerActionWidget — simple text card for ANSWER: responses
# ---------------------------------------------------------------------------

class AnswerActionWidget(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.current_theme = "light"
        self.answer_text = text or ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("ActionCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        # Top Row: Icon + Label (matches CalcActionWidget style)
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel("\u2728")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.action_label = QLabel("ANSWER")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()

        # Answer text
        self.text_label = QLabel(self.answer_text)
        self.text_label.setFont(QFont("Manrope", 13))
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.text_label)
        layout.addWidget(self.card)
        self.update_style()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.1)"
            icon_color = "#FFFFFF"
            action_color = "#AAAAAA"
            text_color = "#FFFFFF"
        else:
            bg = "rgba(255, 255, 255, 0.25)"
            border = "rgba(0, 0, 0, 0.1)"
            icon_color = "#111111"
            action_color = "#666666"
            text_color = "#050505"

        self.card.setStyleSheet(f"QWidget#ActionCard {{ background: {bg}; border-radius: 16px; border: 1px solid {border}; }}")
        self.icon_label.setStyleSheet(f"background-color: transparent; color: {icon_color}; font-size: 14px; border: none;")
        self.action_label.setStyleSheet(f"color: {action_color}; letter-spacing: 0.5px;")
        self.text_label.setStyleSheet(f"color: {text_color}; line-height: 1.4;")

    def sizeHint(self):
        w = 660
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            return self.layout().sizeHint()
        return super().sizeHint()


# ---------------------------------------------------------------------------
# SendEmailWidget — AI-powered email compose card
# ---------------------------------------------------------------------------

class SendEmailWidget(QWidget):
    """Email compose widget. AI composes subject+body via main model (Grok)."""
    email_sent = pyqtSignal()
    _compose_result_ready = pyqtSignal(dict)  # thread-safe signal for compose results
    _send_result_ready = pyqtSignal(str)      # thread-safe signal for send results

    def __init__(self, to="", subject="", body="", original_query="", parent=None):
        super().__init__(parent)
        self.current_theme = "light"
        self._sent = False
        self._composing = False
        self._compose_done = False
        self._original_query = original_query

        # Connect thread-safe signals
        self._compose_result_ready.connect(self._on_compose_done)
        self._send_result_ready.connect(self._on_sent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("SendEmailCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Header row
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel("\u2709")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.action_label = QLabel("COMPOSE EMAIL")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        self.status_label = QLabel("")
        self.status_label.setFont(QFont("Manrope", 9))
        self.status_label.setVisible(False)

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(self.status_label)

        card_layout.addWidget(top_row)

        # To field
        to_row = QWidget()
        to_layout = QHBoxLayout(to_row)
        to_layout.setContentsMargins(0, 0, 0, 0)
        to_layout.setSpacing(8)
        to_label = QLabel("To")
        to_label.setFont(QFont("Manrope", 11))
        to_label.setFixedWidth(55)
        to_label.setObjectName("SendEmailFieldLabel")
        self.to_edit = QLineEdit(to)
        self.to_edit.setFont(QFont("Manrope", 12))
        self.to_edit.setObjectName("SendEmailInput")
        self.to_edit.setPlaceholderText("recipient@example.com")
        to_layout.addWidget(to_label)
        to_layout.addWidget(self.to_edit)
        card_layout.addWidget(to_row)

        # Subject field
        subj_row = QWidget()
        subj_layout = QHBoxLayout(subj_row)
        subj_layout.setContentsMargins(0, 0, 0, 0)
        subj_layout.setSpacing(8)
        subj_label = QLabel("Subject")
        subj_label.setFont(QFont("Manrope", 11))
        subj_label.setFixedWidth(55)
        subj_label.setObjectName("SendEmailFieldLabel")
        self.subject_edit = QLineEdit(subject)
        self.subject_edit.setFont(QFont("Manrope", 12))
        self.subject_edit.setObjectName("SendEmailInput")
        self.subject_edit.setPlaceholderText("Subject")
        subj_layout.addWidget(subj_label)
        subj_layout.addWidget(self.subject_edit)
        card_layout.addWidget(subj_row)

        # Body field
        self.body_edit = QTextEdit()
        self.body_edit.setFont(QFont("Manrope", 12))
        self.body_edit.setObjectName("SendEmailBody")
        self.body_edit.setPlaceholderText("Press Enter to compose with AI...")
        self.body_edit.setPlainText(body)
        self.body_edit.setMinimumHeight(80)
        self.body_edit.setMaximumHeight(180)
        card_layout.addWidget(self.body_edit)

        # Bottom row: hint + send button
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)

        self.hint_label = QLabel("")
        self.hint_label.setFont(QFont("Manrope", 9))
        self.hint_label.setObjectName("SendEmailHint")
        btn_layout.addWidget(self.hint_label)
        btn_layout.addStretch()

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("SendEmailBtn")
        self.send_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.send_btn.setFont(QFont("Manrope", 11, QFont.Weight.DemiBold))
        self.send_btn.setFixedHeight(34)
        self.send_btn.setMinimumWidth(100)
        self.send_btn.clicked.connect(self._send)

        btn_layout.addWidget(self.send_btn)
        card_layout.addWidget(btn_row)

        layout.addWidget(self.card)
        self.update_style()

        # Auto-compose after a delay — only if the query has enough content
        # (delay ensures the user has stopped typing before we fire an API call)
        if self._original_query and not body.strip() and self._query_is_complete(self._original_query):
            self.hint_label.setText("Composing...")
            QTimer.singleShot(1500, self.start_compose)
        else:
            self.hint_label.setText("Enter to compose with AI" if self._original_query else "")

    @staticmethod
    def _query_is_complete(query: str) -> bool:
        """Return True if the query has enough intent to compose a real email draft."""
        import re as _re
        # Strip the email command prefix (multilingual)
        stripped = _re.sub(
            r'^(?:send|write|compose|wyślij|wyslij|napisz)\s+(?:an?\s+)?(?:e?mail|maila?)\s+',
            '', query.strip(), flags=_re.IGNORECASE
        )
        # Strip recipient name (1-2 words after "to/do/do/oskarowi" etc.)
        stripped = _re.sub(r'^(?:to|do)\s+\w+(?:\s+\w+)?\s*', '', stripped, flags=_re.IGNORECASE)
        # What remains should have some meaningful content (≥3 words)
        words = [w for w in stripped.split() if len(w) > 1]
        return len(words) >= 3

    def start_compose(self):
        """Trigger AI compose — can also be called externally."""
        if self._composing or self._sent:
            return
        if self._original_query and not self.body_edit.toPlainText().strip():
            self._start_ai_compose(self._original_query, self.to_edit.text().strip())

    def _start_ai_compose(self, query, recipient_hint):
        """Use main model (Grok) + memory to compose the email in the background."""
        self._composing = True
        self.send_btn.setEnabled(False)
        self.status_label.setText("Composing...")
        self.status_label.setVisible(True)
        self.hint_label.setText("Enter to send when ready")
        is_dark = self.current_theme == "dark"
        self.status_label.setStyleSheet(f"color: {'#60A5FA' if is_dark else '#2563EB'};")
        self.hint_label.setStyleSheet(f"color: {'#6B7280' if is_dark else '#9CA3AF'};")

        import threading

        def _compose():
            try:
                import re as _re
                # Step 1: Memory lookup first (fast, ~0.1s) — provides context for compose
                memory_context = ""
                found_email = ""
                if recipient_hint and "@" not in recipient_hint:
                    try:
                        from src.services.memory.memvid_store import get_user_memory
                        memory = get_user_memory(f"{recipient_hint} email contact info")
                        if memory and "no " not in memory.lower()[:20]:
                            memory_context = memory
                            emails_found = _re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', memory)
                            if emails_found:
                                found_email = emails_found[0]
                    except Exception:
                        pass

                # Step 2: AI compose with memory context
                result = self._ai_compose(query, recipient_hint, memory_context)

                # Merge found email
                if found_email and not result.get("to"):
                    result["to"] = found_email

                try:
                    self._compose_result_ready.emit(result)
                except RuntimeError:
                    pass  # widget deleted before compose finished
            except Exception as e:
                try:
                    self._compose_result_ready.emit({"error": str(e)})
                except RuntimeError:
                    pass  # widget deleted before compose finished

        threading.Thread(target=_compose, daemon=True).start()

    def _ai_compose(self, query, recipient_hint, memory_context=""):
        """Compose email subject + body via direct call to the Omni backend."""
        import json, re, logging, requests
        result = {}
        try:
            from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID, FAST_MODEL_GROQ
            from src.core import auth as _auth

            compose_prompt = (
                'You compose emails. Output ONLY valid JSON: {"subject":"...","body":"..."}\n'
                "Rules:\n"
                "- Specific subject line matching the topic\n"
                "- Body: natural and personal, concise (3-5 sentences), no filler phrases\n"
                "- Sign off with 'Best,' or 'Thanks,' (no [Your Name] placeholder)\n"
                "- No markdown, no code fences, only the JSON object"
            )
            context_parts = []
            if recipient_hint:
                context_parts.append(f"Recipient name: {recipient_hint}")
            if memory_context:
                context_parts.append(f"What I know about this person:\n{memory_context}")
            user_content = "\n".join(context_parts + [f"Request: {query}"])

            headers = {
                "Content-Type": "application/json",
                "X-Omni-Secret": OMNI_SECRET,
                "X-Device-ID": DEVICE_ID,
            }
            token = _auth.get_access_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

            for attempt in range(2):
                try:
                    resp = requests.post(
                        f"{BACKEND_URL}/v1/chat/completions",
                        headers=headers,
                        json={
                            "model": FAST_MODEL_GROQ,
                            "messages": [
                                {"role": "system", "content": compose_prompt},
                                {"role": "user", "content": user_content},
                            ],
                            "max_tokens": 400,
                            "temperature": 0.8,
                            "stream": False,
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    break
                except Exception as e:
                    if attempt == 1:
                        raise
                    logging.warning(f"[SendEmailWidget] Compose attempt {attempt+1} failed: {e}, retrying...")

            data = resp.json()
            text = (data["choices"][0]["message"].get("content") or "").strip()
            logging.info(f"[SendEmailWidget] AI compose raw: {text[:200]}")

            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                text = json_match.group(0)
            parsed = json.loads(text)
            if parsed.get("subject"):
                result["subject"] = parsed["subject"]
            if parsed.get("body"):
                result["body"] = parsed["body"]
            logging.info(f"[SendEmailWidget] Compose OK: subject='{result.get('subject', '')[:50]}'")
        except Exception as e:
            logging.warning(f"[SendEmailWidget] AI compose failed: {e}")
            result["error"] = str(e)
        return result

    def _on_compose_done(self, result):
        """Update fields with AI-composed content. Called on main thread via signal."""
        import logging
        logging.info(f"[SendEmailWidget] _on_compose_done called. visible={self.isVisible()}, subject='{result.get('subject', '')[:40]}', body_len={len(result.get('body', ''))}")

        self._composing = False
        self._compose_done = True
        self.send_btn.setEnabled(True)

        if result.get("error") or (not result.get("subject") and not result.get("body")):
            logging.warning(f"[SendEmailWidget] Compose error: {result.get('error', 'empty result')}")
            self.status_label.setText("Compose failed — type manually")
            self.status_label.setStyleSheet("color: #F59E0B;")
            self.status_label.setVisible(True)
            self.body_edit.setPlaceholderText("Type your email...")
            self.hint_label.setText("Enter to send")
        else:
            self.status_label.setText("Draft ready")
            is_dark = self.current_theme == "dark"
            self.status_label.setStyleSheet(f"color: {'#34D399' if is_dark else '#059669'};")
            QTimer.singleShot(2000, lambda: self.status_label.setVisible(False) if not self._sent else None)

        # Fill fields — AI subject always overrides regex-extracted one
        if result.get("to") and not self.to_edit.text().strip():
            self.to_edit.setText(result["to"])
            logging.info(f"[SendEmailWidget] Set to: {result['to']}")

        if result.get("subject"):
            self.subject_edit.setText(result["subject"])
            logging.info(f"[SendEmailWidget] Set subject: {result['subject']}")

        if result.get("body") and not self.body_edit.toPlainText().strip():
            self.body_edit.setPlainText(result["body"])
            logging.info(f"[SendEmailWidget] Set body ({len(result['body'])} chars)")

        # Update hint based on what's missing
        if not self.to_edit.text().strip():
            self.hint_label.setText("Fill in recipient, then Enter to send")
        else:
            self.hint_label.setText("Enter to send")

        # Trigger parent layout update
        self.updateGeometry()
        parent = self.parent()
        while parent:
            if hasattr(parent, 'adjust_window_height'):
                parent.adjust_window_height()
                break
            parent = parent.parent()

    def _send(self):
        if self._sent or self._composing:
            return
        to = self.to_edit.text().strip()
        subject = self.subject_edit.text().strip()
        body = self.body_edit.toPlainText().strip()
        if not to:
            self.to_edit.setFocus()
            self.status_label.setText("Enter recipient address")
            self.status_label.setStyleSheet("color: #F59E0B;")
            self.status_label.setVisible(True)
            return
        if not subject:
            self.subject_edit.setFocus()
            self.status_label.setText("Enter subject")
            self.status_label.setStyleSheet("color: #F59E0B;")
            self.status_label.setVisible(True)
            return

        self.send_btn.setEnabled(False)
        self.send_btn.setText("Sending...")
        self.hint_label.setText("")
        self.status_label.setVisible(False)
        self.to_edit.setReadOnly(True)
        self.subject_edit.setReadOnly(True)
        self.body_edit.setReadOnly(True)

        self._send_to = to  # store for memory save
        self._send_recipient_name = self._original_query  # for context

        import threading
        def _do_send():
            from src.services.system.productivity import send_email
            res = send_email(to, subject, body)
            # Save email address to memory after successful send
            if "successfully" in res.lower() or "sent" in res.lower():
                try:
                    from src.services.memory.memvid_store import remember_fact
                    # Extract name from original query if available
                    import re as _re
                    name_match = _re.search(r'(?:to|do)\s+(\w+)', self._send_recipient_name or "", _re.IGNORECASE)
                    name = name_match.group(1) if name_match else ""
                    if name and "@" in to:
                        remember_fact(f"{name}'s email address is {to}")
                except Exception:
                    pass
            self._send_result_ready.emit(res)
        threading.Thread(target=_do_send, daemon=True).start()

    def _on_sent(self, result):
        import logging
        logging.info(f"[SendEmailWidget] _on_sent: {result[:80]}")
        self._sent = True
        is_dark = self.current_theme == "dark"
        if "successfully" in result.lower() or "sent" in result.lower():
            self.send_btn.setText("Sent!")
            self.status_label.setText("Email sent successfully")
            self.status_label.setStyleSheet(f"color: {'#34D399' if is_dark else '#059669'};")
            self.status_label.setVisible(True)
            self.hint_label.setText("")
        else:
            self.send_btn.setText("Retry")
            self.send_btn.setEnabled(True)
            self._sent = False
            self.to_edit.setReadOnly(False)
            self.subject_edit.setReadOnly(False)
            self.body_edit.setReadOnly(False)
            self.status_label.setText(result[:60])
            self.status_label.setStyleSheet("color: #EF4444;")
            self.status_label.setVisible(True)
        self.email_sent.emit()

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        is_dark = self.current_theme == "dark"
        bg = "rgba(0, 0, 0, 0.22)" if is_dark else "rgba(255, 255, 255, 0.25)"
        border = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(255, 255, 255, 0.40)"
        text_color = "#FFFFFF" if is_dark else "#050505"
        accent = "#60A5FA" if is_dark else "#2563EB"
        label_color = "#9CA3AF" if is_dark else "#6B7280"
        input_bg = "rgba(255, 255, 255, 0.06)" if is_dark else "rgba(0, 0, 0, 0.04)"
        input_border = "rgba(255, 255, 255, 0.12)" if is_dark else "rgba(0, 0, 0, 0.10)"
        btn_bg = accent
        btn_hover = "#3B82F6" if is_dark else "#1D4ED8"

        self.card.setStyleSheet(
            f"QWidget#SendEmailCard {{ background-color: {bg}; border-radius: 16px; border: 1px solid {border}; }}"
        )
        self.icon_label.setStyleSheet(f"background: transparent; color: {accent}; font-size: 14px; border: none;")
        self.action_label.setStyleSheet(f"color: {accent}; letter-spacing: 1px;")

        input_style = (
            f"QLineEdit#SendEmailInput {{ "
            f"  background: {input_bg}; color: {text_color}; "
            f"  border: 1px solid {input_border}; border-radius: 8px; "
            f"  padding: 6px 10px; "
            f"}}"
            f"QLineEdit#SendEmailInput:focus {{ border: 1px solid {accent}; }}"
        )
        self.to_edit.setStyleSheet(input_style)
        self.subject_edit.setStyleSheet(input_style)

        self.body_edit.setStyleSheet(
            f"QTextEdit#SendEmailBody {{ "
            f"  background: {input_bg}; color: {text_color}; "
            f"  border: 1px solid {input_border}; border-radius: 8px; "
            f"  padding: 6px 10px; "
            f"}}"
            f"QTextEdit#SendEmailBody:focus {{ border: 1px solid {accent}; }}"
        )

        for lbl in self.card.findChildren(QLabel, "SendEmailFieldLabel"):
            lbl.setStyleSheet(f"color: {label_color}; border: none; background: transparent;")

        self.hint_label.setStyleSheet(f"color: {label_color}; border: none; background: transparent;")

        self.send_btn.setStyleSheet(
            f"QPushButton#SendEmailBtn {{ "
            f"  background: {btn_bg}; color: white; border: none; "
            f"  border-radius: 8px; padding: 6px 20px; "
            f"}}"
            f"QPushButton#SendEmailBtn:hover {{ background: {btn_hover}; }}"
            f"QPushButton#SendEmailBtn:disabled {{ background: {label_color}; color: rgba(255,255,255,0.6); }}"
        )

    def sizeHint(self):
        return QSize(660, 300)


# ---------------------------------------------------------------------------
# ToolDraftWidget — generic clickable proposal card for AI tool calls
# ---------------------------------------------------------------------------

# Metadata for each draft-capable tool
_TOOL_DRAFT_META = {
    "set_reminder":          {"icon": "\u23F0",   "header": "SET REMINDER",    "btn": "Set"},
    "create_calendar_event": {"icon": "\U0001F4C5", "header": "CREATE EVENT",  "btn": "Create"},
    "create_file":           {"icon": "\U0001F4DD", "header": "CREATE FILE",   "btn": "Create"},
    "edit_file":             {"icon": "\u270F\uFE0F", "header": "EDIT FILE",   "btn": "Apply"},
    "compress":              {"icon": "\U0001F5DC\uFE0F", "header": "COMPRESS","btn": "Compress"},
    "convert_file":          {"icon": "\U0001F504", "header": "CONVERT FILE",  "btn": "Convert"},
    "organize_folder":       {"icon": "\U0001F4C1", "header": "ORGANIZE FOLDER","btn": "Organize"},
    "run_terminal":          {"icon": "\U0001F5A5\uFE0F", "header": "TERMINAL","btn": "Run"},
    "install_app":           {"icon": "\U0001F4E6", "header": "INSTALL APP",   "btn": "Install"},
    "uninstall_app":         {"icon": "\U0001F5D1\uFE0F", "header": "UNINSTALL","btn": "Uninstall"},
}


def _tool_draft_description(tool_name: str, args: dict) -> str:
    """Build a human-readable one-liner describing what the tool will do."""
    if tool_name == "set_reminder":
        lbl = args.get("label", "")
        at = args.get("fire_at_iso", "")
        if "T" in at:
            at = at.split("T")[1][:5]
        return f'"{lbl}" at {at}' if at else f'"{lbl}"'
    if tool_name == "create_calendar_event":
        title = args.get("title", "")
        start = args.get("start_iso", "")
        dur = args.get("duration_minutes", 60)
        return f'"{title}" — {start} ({dur} min)'
    if tool_name == "create_file":
        fn = args.get("filename", "")
        folder = args.get("folder", "~/Desktop")
        return f'{fn} in {folder}'
    if tool_name == "edit_file":
        path = args.get("path", "")
        return os.path.basename(path) if path else "file"
    if tool_name == "compress":
        paths = args.get("paths", [])
        return f'{len(paths)} item{"s" if len(paths) != 1 else ""}'
    if tool_name == "convert_file":
        inp = args.get("input_path", "")
        fmt = args.get("output_format", "")
        return f'{os.path.basename(inp)} \u2192 .{fmt}'
    if tool_name == "organize_folder":
        return args.get("path", "")
    if tool_name == "run_terminal":
        return args.get("description", "") or args.get("command", "")[:60]
    if tool_name == "install_app":
        return args.get("name", "")
    if tool_name == "uninstall_app":
        return args.get("name", "")
    return str(args)[:80]


class ToolDraftWidget(QWidget):
    """Generic proposal card for AI tool calls. Shows details + Execute button.

    Emits ``execute_requested`` when the user clicks the action button.
    The signal carries (tool_name: str, args: dict).
    """
    execute_requested = pyqtSignal(str, dict)
    _compose_done = pyqtSignal(str)  # content generated by AI

    def __init__(self, tool_name: str, args: dict, original_query: str = "", parent=None):
        super().__init__(parent)
        self.current_theme = "light"
        self._tool_name = tool_name
        self._args = dict(args)
        self._executed = False
        self._original_query = original_query
        self._composing = False
        self._execute_after_compose = False

        meta = _TOOL_DRAFT_META.get(tool_name, {"icon": "\u2699", "header": tool_name.upper(), "btn": "Run"})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("ToolDraftCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Header row: icon + label + status
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel(meta["icon"])
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.action_label = QLabel(meta["header"])
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))

        self.status_label = QLabel("")
        self.status_label.setFont(QFont("Manrope", 9))
        self.status_label.setVisible(False)

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()
        top_layout.addWidget(self.status_label)

        card_layout.addWidget(top_row)

        # Description line
        desc = _tool_draft_description(tool_name, args)
        self.desc_label = QLabel(desc)
        self.desc_label.setFont(QFont("Manrope", 12))
        self.desc_label.setWordWrap(True)
        card_layout.addWidget(self.desc_label)

        # Detail line (optional — e.g. file content preview, command text)
        detail = self._build_detail()
        self.detail_label = QLabel(detail)
        self.detail_label.setFont(QFont("Manrope", 10))
        self.detail_label.setWordWrap(True)
        self.detail_label.setMaximumHeight(60)
        if detail:
            card_layout.addWidget(self.detail_label)
        else:
            self.detail_label.setVisible(False)

        # Bottom row: execute button
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        self.exec_btn = QPushButton(meta["btn"])
        self.exec_btn.setObjectName("ToolDraftBtn")
        self.exec_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.exec_btn.setFont(QFont("Manrope", 11, QFont.Weight.DemiBold))
        self.exec_btn.setFixedHeight(34)
        self.exec_btn.setMinimumWidth(100)
        self.exec_btn.clicked.connect(self._on_execute)
        btn_layout.addWidget(self.exec_btn)

        card_layout.addWidget(btn_row)
        layout.addWidget(self.card)
        self.update_style()

        # Connect compose signal (compose starts on explicit user action, not on init)
        self._compose_done.connect(self._on_compose_done)
        self._needs_compose = (tool_name == "create_file" and not args.get("content") and bool(original_query))

    def _start_compose(self, query: str):
        """Use fast model to generate file content from the user's query."""
        import threading
        self._composing = True
        self.exec_btn.setEnabled(False)
        self.detail_label.setText("Generating content\u2026")
        self.detail_label.setVisible(True)

        def _run():
            try:
                import json, re, requests, logging
                from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID, FAST_MODEL_GROQ
                from src.core import auth as _auth

                filename = self._args.get("filename", "file.txt")
                sys_prompt = (
                    f"Generate the content for a file named '{filename}'. "
                    "Output ONLY the raw file content — no markdown fences, no explanation, no preamble. "
                    "Just the exact text that should go inside the file."
                )
                headers = {
                    "Content-Type": "application/json",
                    "X-Omni-Secret": OMNI_SECRET,
                    "X-Device-ID": DEVICE_ID,
                }
                token = _auth.get_access_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"

                resp = requests.post(
                    f"{BACKEND_URL}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": FAST_MODEL_GROQ,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": query},
                        ],
                        "max_tokens": 4096,
                        "temperature": 0.3,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
                # Strip markdown code fences if model added them
                content = re.sub(r'^```[\w]*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
                self._compose_done.emit(content)
            except Exception as e:
                logging.error(f"[ToolDraft] compose error: {e}")
                self._compose_done.emit("")  # empty = let user execute with no content

        threading.Thread(target=_run, daemon=True).start()

    def _on_compose_done(self, content: str):
        self._composing = False
        if content:
            self._args["content"] = content
            preview = content if len(content) <= 120 else content[:117] + "\u2026"
            self.detail_label.setText(preview)
            self.detail_label.setVisible(True)
        else:
            self.detail_label.setText("")
            self.detail_label.setVisible(False)
        # If compose was triggered by clicking Execute, proceed to execute now
        if getattr(self, '_execute_after_compose', False):
            self._execute_after_compose = False
            self._executed = True
            self.exec_btn.setEnabled(False)
            self.exec_btn.setText("Running\u2026")
            self.execute_requested.emit(self._tool_name, self._args)
        else:
            self.exec_btn.setEnabled(True)

    def _build_detail(self) -> str:
        n = self._tool_name
        a = self._args
        if n == "create_file":
            content = a.get("content", "")
            if len(content) > 120:
                content = content[:117] + "\u2026"
            return content
        if n == "edit_file":
            old = a.get("old_text", "")
            new = a.get("new_text", "")
            if len(old) > 50:
                old = old[:47] + "\u2026"
            if len(new) > 50:
                new = new[:47] + "\u2026"
            return f'"{old}" \u2192 "{new}"'
        if n == "run_terminal":
            return a.get("command", "")[:120]
        if n == "compress":
            paths = a.get("paths", [])
            return ", ".join(os.path.basename(p) for p in paths[:5])
        return ""

    def _on_execute(self):
        if self._executed or self._composing:
            return
        # If content needs to be generated first, start compose and execute after
        if self._needs_compose:
            self._needs_compose = False
            self._execute_after_compose = True
            self._start_compose(self._original_query)
            return
        self._executed = True
        self.exec_btn.setEnabled(False)
        self.exec_btn.setText("Running\u2026")
        self.execute_requested.emit(self._tool_name, self._args)

    def show_result(self, text: str, success: bool = True):
        """Called after execution to show the result."""
        self.status_label.setText("\u2713 Done" if success else "\u2717 Failed")
        self.status_label.setVisible(True)
        is_dark = self.current_theme == "dark"
        color = "#34D399" if success else "#F87171"
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.exec_btn.setText("Done" if success else "Failed")
        if text:
            self.detail_label.setText(text[:200])
            self.detail_label.setVisible(True)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        is_dark = (self.current_theme == "dark")
        card_bg = "rgba(0, 0, 0, 0.22)" if is_dark else "rgba(255, 255, 255, 0.25)"
        border = "rgba(255, 255, 255, 0.10)" if is_dark else "rgba(255, 255, 255, 0.40)"
        text_color = t["text_primary"]
        text_sec = t["text_secondary"]
        accent = "#60A5FA" if is_dark else "#2563EB"
        btn_bg = accent
        btn_hover = "#3B82F6" if is_dark else "#1D4ED8"

        self.card.setStyleSheet(
            f"QWidget#ToolDraftCard {{ "
            f"  background-color: {card_bg}; border-radius: 16px; "
            f"  border: 1px solid {border}; "
            f"}}"
        )
        self.icon_label.setStyleSheet(f"background: transparent; color: {text_sec}; font-size: 14px;")
        self.action_label.setStyleSheet(f"color: {text_sec}; letter-spacing: 0.5px; background: transparent;")
        self.desc_label.setStyleSheet(f"color: {text_color}; background: transparent;")
        self.detail_label.setStyleSheet(f"color: {text_sec}; background: transparent; font-size: 10px;")
        self.exec_btn.setStyleSheet(
            f"QPushButton#ToolDraftBtn {{ "
            f"  background: {btn_bg}; color: white; border: none; "
            f"  border-radius: 8px; padding: 6px 20px; "
            f"}}"
            f"QPushButton#ToolDraftBtn:hover {{ background: {btn_hover}; }}"
            f"QPushButton#ToolDraftBtn:disabled {{ background: {text_sec}; color: rgba(255,255,255,0.6); }}"
        )

    def sizeHint(self):
        return QSize(660, 120)

