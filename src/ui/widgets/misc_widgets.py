import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QFrame, QListWidget, QGraphicsOpacityEffect, QFileIconProvider)
from PyQt6.QtCore import (Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, 
                          QParallelAnimationGroup, pyqtProperty, QRectF, QFileInfo,
                          QThreadPool, QRunnable, QObject, pyqtSignal)
from PyQt6.QtGui import QFont, QColor, QPainter, QLinearGradient, QBrush, QPen, QIcon, QPixmap, QPainterPath

ICON_CACHE = {}

class IconLoaderSignals(QObject):
    icon_loaded = pyqtSignal(QIcon, str)

class IconLoader(QRunnable):
    def __init__(self, icon_name):
        super().__init__()
        self.icon_name = icon_name
        self.signals = IconLoaderSignals()

    def run(self):
        icon = QIcon()
        icon_name = self.icon_name
        
        try:
            if os.path.isabs(icon_name) and os.path.exists(icon_name):
                 # Try loading as standard image first
                 icon = QIcon(icon_name)
                 # If it fails or is a system file type (exe/lnk), use QFileIconProvider
                 if icon.isNull() or icon_name.lower().endswith(('.exe', '.lnk', '.bat', '.cmd')):
                     provider = QFileIconProvider()
                     icon = provider.icon(QFileInfo(icon_name))
            else:
                 icon = QIcon.fromTheme(icon_name)
                 if icon.isNull():
                     pixmap_path = f"/usr/share/pixmaps/{icon_name}.png"
                     if os.path.exists(pixmap_path):
                         icon = QIcon(pixmap_path)
                     elif os.path.exists(f"/usr/share/icons/hicolor/48x48/apps/{icon_name}.png"):
                         icon = QIcon(f"/usr/share/icons/hicolor/48x48/apps/{icon_name}.png")
                     
                     if icon.isNull():
                         flatpak_dirs = [
                             os.path.expanduser("~/.local/share/flatpak/exports/share/icons/hicolor"),
                             "/var/lib/flatpak/exports/share/icons/hicolor"
                         ]
                         sizes = ["256x256", "128x128", "64x64", "48x48", "32x32", "512x512", "scalable"]
                         
                         for d in flatpak_dirs:
                             if not icon.isNull(): break
                             if not os.path.exists(d): continue
                             for s in sizes:
                                 p = os.path.join(d, s, "apps", f"{icon_name}.png")
                                 if os.path.exists(p):
                                     icon = QIcon(p)
                                     break
                                 p_svg = os.path.join(d, s, "apps", f"{icon_name}.svg")
                                 if os.path.exists(p_svg):
                                     icon = QIcon(p_svg)
                                     break
        except Exception:
            pass
        
        self.signals.icon_loaded.emit(icon, self.icon_name)

class ReplyActionWidget(QWidget):
    def __init__(self, title, content, parent=None):
        super().__init__(parent)
        self.content = content
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Chip Container
        self.chip = QLabel(title)
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chip.setFont(QFont("Manrope", 13, QFont.Weight.Medium))
        self.chip.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 122, 255, 0.1); 
                color: #007AFF; 
                border: 1px solid rgba(0, 122, 255, 0.2);
                border-radius: 16px;
                padding: 6px 16px;
            }
            QLabel:hover {
                background-color: rgba(0, 122, 255, 0.15);
                border: 1px solid rgba(0, 122, 255, 0.3);
            }
        """)
        
        layout.addWidget(self.chip)
        layout.addStretch()

    def sizeHint(self):
        return QSize(660, 48)

class ThinkingWidget(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.is_expanded = False
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(24, 12, 24, 12)
        self.main_layout.setSpacing(8)

        self.header = QLabel("Thinking...")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setFont(QFont("Instrument Serif", 16, QFont.Weight.Normal))
        f = self.header.font(); f.setItalic(True); self.header.setFont(f)

        self.header.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #666666;
                padding-left: 0px;
            }
            QLabel:hover { color: #333333; }
        """)
        self.header.mousePressEvent = self.toggle_expand

        self.content_label = QLabel(text)
        self.content_label.setWordWrap(True)
        self.content_label.setFont(QFont("Manrope", 12))
        self.content_label.setStyleSheet("color: #888888; padding: 4px 0px 4px 12px; border-left: 2px solid #E0E0E0;")
        self.content_label.setHidden(True)

        self.main_layout.addWidget(self.header)
        self.main_layout.addWidget(self.content_label)

    def sizeHint(self):
        w = 616
        h = 64 # Increased to prevent clipping of descenders
        if self.is_expanded: 
            h += self.content_label.heightForWidth(580) + 16
        return QSize(w, h)

    def toggle_expand(self, event):
        self.is_expanded = not self.is_expanded
        self.content_label.setHidden(not self.is_expanded)
        self.header.setText("Thinking... (Click to collapse)" if self.is_expanded else "Thinking...")
        self.update_item_size()

    def update_item_size(self):
        list_widget = self.window().findChild(QListWidget)
        if list_widget:
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if list_widget.itemWidget(item) == self:
                    item.setSizeHint(self.sizeHint())
                    break
            if hasattr(self.window(), "adjust_window_height"):
                self.window().adjust_window_height()

class SeparatorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24) 
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        grad = QLinearGradient(40, 0, self.width() - 40, 0)
        c = QColor(0, 0, 0, 20)
        transparent = QColor(0, 0, 0, 0)
        grad.setColorAt(0, transparent)
        grad.setColorAt(0.2, c)
        grad.setColorAt(0.8, c)
        grad.setColorAt(1, transparent)
        
        pen = QPen(QBrush(grad), 1)
        painter.setPen(pen)
        
        y = self.height() // 2
        painter.drawLine(40, y, self.width() - 40, y)

class SmoothEntryWidget(QWidget):
    def __init__(self, content_widget, parent=None, animate=True):
        super().__init__(parent)
        self.content_widget = content_widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content_widget)
        
        self.opacity_eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_eff)
        
        if animate:
            self.opacity_eff.setOpacity(0)
            self.anim_group = QParallelAnimationGroup()
            
            self.op_anim = QPropertyAnimation(self.opacity_eff, b"opacity")
            self.op_anim.setStartValue(0)
            self.op_anim.setEndValue(1)
            self.op_anim.setDuration(400)
            self.op_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            
            self.anim_group.addAnimation(self.op_anim)
            QTimer.singleShot(10, self.anim_group.start)
        else:
            self.opacity_eff.setOpacity(1)

class FollowUpWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.hint = QLabel("Press Tab to follow up")
        self.hint.setFont(QFont("Manrope", 11, QFont.Weight.Normal))
        f = self.hint.font(); f.setItalic(True); self.hint.setFont(f)
        self.hint.setStyleSheet("color: rgba(0, 0, 0, 0.35);")

        layout.addStretch()
        layout.addWidget(self.hint)

        self.hide() 
    
    def sizeHint(self):
        return QSize(150, 32)
    
    def set_active(self, active):
        if active:
            self.hint.setText("Press Tab to exit follow up mode")
            self.show()
        else:
            self.hint.setText("Press Tab to follow up")

class UnscrollableTextEdit(QTextEdit):
    def wheelEvent(self, event):
        event.ignore()

class AnswerWidget(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 4, 24, 4)
        
        self.text_edit = UnscrollableTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFrameStyle(QFrame.Shape.NoFrame)
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setStyleSheet("background: transparent; color: #222222;")
        
        self.text_edit.setMarkdown(text)
        
        font = QFont("Manrope", 16, QFont.Weight.Normal)
        self.text_edit.setFont(font)
        
        self.text_edit.document().setTextWidth(660)
        
        self.layout.addWidget(self.text_edit)

    def sizeHint(self):
        self.text_edit.document().setTextWidth(660)
        h = self.text_edit.document().size().height()
        return QSize(660, int(h) + 36)

class StandardItemWidget(QWidget):
    def __init__(self, text, icon_name=None, font=None, color=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(4)
        
        self.lbl_icon = QLabel()
        self.lbl_icon.setFixedSize(24, 24)
        
        if icon_name:
            layout.addWidget(self.lbl_icon)
            
            if icon_name in ICON_CACHE:
                self.lbl_icon.setPixmap(ICON_CACHE[icon_name].pixmap(24, 24))
            else:
                # Load asynchronously
                loader = IconLoader(icon_name)
                loader.signals.icon_loaded.connect(self.on_icon_loaded)
                QThreadPool.globalInstance().start(loader)

        self.lbl_text = QLabel(text)
        if font: self.lbl_text.setFont(font)
        else: self.lbl_text.setFont(QFont("Manrope", 15, QFont.Weight.Medium))
        
        if color: self.lbl_text.setStyleSheet(f"color: {color};")
        else: self.lbl_text.setStyleSheet("color: #333333;")
        
        layout.addWidget(self.lbl_text)
        layout.addStretch()
        
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent;")

    def on_icon_loaded(self, icon, name):
        if not icon.isNull():
            ICON_CACHE[name] = icon
            self.lbl_icon.setPixmap(icon.pixmap(24, 24))

    def set_text(self, text):
        self.lbl_text.setText(text)

    def sizeHint(self):
        return QSize(660, 72)

class RotatingLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rotation = 0
        self.base_speed = 1.5 
        self.current_speed = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.is_spinning = False

    def animate(self):
        self._rotation += self.current_speed
        self._rotation %= 360.0
        
        target = self.base_speed if self.is_spinning else 0.0
        
        if self.current_speed > target:
            self.current_speed = max(target, self.current_speed * 0.92) 
        elif self.current_speed < target:
            self.current_speed = min(target, self.current_speed + 0.2)
            
        self.update()
        
        if not self.is_spinning and self.current_speed < 0.1:
            self.timer.stop()

    def start_spinning(self):
        self.is_spinning = True
        self.current_speed = self.base_speed
        if not self.timer.isActive():
            self.timer.start(16)

    def stop_spinning(self):
        self.is_spinning = False

    def boost_speed(self):
        self.is_spinning = True
        self.current_speed = 15.0 
        if not self.timer.isActive():
            self.timer.start(16)
        
    def paintEvent(self, event):
        if not self.pixmap():
            return super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        w = self.width()
        h = self.height()
        
        painter.translate(w / 2, h / 2)
        painter.rotate(self._rotation)
        painter.translate(-w / 2, -h / 2)
        
        pm = self.pixmap()
        x = (w - pm.width()) // 2
        y = (h - pm.height()) // 2
        painter.drawPixmap(x, y, pm)

class GradientBorderFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MainFrame")
        self._hue_shift = 0.0
        self.base_speed = 0.005
        self.current_speed = self.base_speed
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        
        self.minimal_mode = True 
        self._mode_progress = 0.0
        self.timer.stop()

        self.mode_anim = QPropertyAnimation(self, b"mode_progress")
        self.mode_anim.setDuration(1000)
        self.mode_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.colors = [
            QColor("#2E5CB8"), # Deep Top Blue
            QColor("#6A0DAD"), # Dark Violet
            QColor("#D92E87"), # Magenta/Pink
            QColor("#FF8533"), # Warm Orange
            QColor("#66B2FF")  # Light Blue/Cyan
        ]

    @pyqtProperty(float)
    def mode_progress(self):
        return self._mode_progress

    @mode_progress.setter
    def mode_progress(self, value):
        self._mode_progress = value
        self.update()

    def set_minimal_mode(self, enabled):
        if self.minimal_mode == enabled: return
        self.minimal_mode = enabled
        
        self.mode_anim.stop()
        self.mode_anim.setStartValue(self._mode_progress)
        self.mode_anim.setEndValue(0.0 if enabled else 1.0)
        
        if not enabled:
            self.mode_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        else:
            self.mode_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.mode_anim.start()

        if not enabled:
            self.timer.start(30)

    def boost_speed(self):
        self.current_speed = 0.2

    def animate(self):
        self._hue_shift += self.current_speed
        if self._hue_shift > 1.0: self._hue_shift -= 1.0

        if self.current_speed > self.base_speed:
            self.current_speed = max(self.base_speed, self.current_speed * 0.85)

        self.update()

    def get_color_at(self, t):
        n = len(self.colors)
        pos = t * n
        idx1 = int(pos) % n
        idx2 = (idx1 + 1) % n
        rem = pos - int(pos)

        c1 = self.colors[idx1]
        c2 = self.colors[idx2]

        r = c1.red() * (1 - rem) + c2.red() * rem
        g = c1.green() * (1 - rem) + c2.green() * rem
        b = c1.blue() * (1 - rem) + c2.blue() * rem
        return QColor(int(r), int(g), int(b))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 24, 24)

        base_alpha = 125
        painter.fillPath(path, QColor(255, 255, 255, base_alpha)) 
        
        pen = painter.pen()
        pen.setColor(QColor(255, 255, 255, 30))
        pen.setWidth(1)
        painter.strokePath(path, pen)

        if self._mode_progress > 0.01:
            grad = QLinearGradient(0, 0, self.width(), self.height())
            stops = [0.0, 0.25, 0.5, 0.75, 1.0]
            for s in stops:
                t = (s - self._hue_shift) % 1.0
                grad.setColorAt(s, self.get_color_at(t))
            
            painter.setOpacity(self._mode_progress * 0.1)
            painter.fillPath(path, QBrush(grad))
            
            border_path = QPainterPath()
            border_path.addRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 22, 22)
            
            painter.setOpacity(self._mode_progress)
            painter.strokePath(border_path, QPen(QBrush(grad), 3))
            painter.setOpacity(1.0)
