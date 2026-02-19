
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QFrame, QListWidget, QGraphicsOpacityEffect, QFileIconProvider, QPushButton)
from PyQt6.QtCore import (Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, 
                          QParallelAnimationGroup, pyqtProperty, QRectF, QFileInfo,
                          QThreadPool, QRunnable, QObject, pyqtSignal, QPoint)
from PyQt6.QtGui import QFont, QColor, QPainter, QLinearGradient, QBrush, QPen, QIcon, QPixmap, QPainterPath, QImage

from src.ui.styles import THEMES

ICON_CACHE = {}

class IconManager(QObject):
    icon_loaded = pyqtSignal(str, QPixmap)
    _instance = None

    @classmethod
    def instance(cls):
        if not cls._instance:
            cls._instance = IconManager()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.queue = []
        self.is_processing = False

    def request(self, name):
        if name in ICON_CACHE:
            self.icon_loaded.emit(name, ICON_CACHE[name])
            return

        if name not in self.queue:
            self.queue.append(name)
            if not self.is_processing:
                self.process_next()

    def process_next(self):
        try:
            if not self.queue:
                self.is_processing = False
                return

            self.is_processing = True
            icon_name = self.queue.pop(0)

            # Loading Logic (Main Thread)
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except: pass

            icon = QIcon()
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

            if not icon.isNull():
                # Create pixmap directly on main thread
                pixmap = icon.pixmap(48, 48)
                pixmap.setDevicePixelRatio(2.0)
                ICON_CACHE[icon_name] = pixmap
                self.icon_loaded.emit(icon_name, pixmap)
            
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except: pass
        except Exception as e:
            # Prevent loop crash
            print(f"Error in IconManager: {e}")
        finally:
            # Schedule next item with a small delay to keep UI responsive
            QTimer.singleShot(5, self.process_next)

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
        self.is_expanded = bool(text)
        self.current_theme = "light"
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(24, 12, 24, 12)
        self.main_layout.setSpacing(8)

        self.header = QLabel("Thinking...")
        self.header.setFont(QFont("Instrument Serif", 24, QFont.Weight.Normal))
        f = self.header.font(); f.setItalic(True); self.header.setFont(f)

        # self.header.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True) # Entire widget ignores mouse
        # self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.content_label = QLabel(text if text else "")
        self.content_label.setWordWrap(True)
        self.content_label.setFont(QFont("Manrope", 12))
        self.content_label.setVisible(self.is_expanded)

        self.main_layout.addWidget(self.header)
        self.main_layout.addWidget(self.content_label)
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        self.header.setStyleSheet(f"""
            QLabel {{
                background-color: transparent;
                color: {t['text_secondary']};
                padding-left: 0px;
            }}
        """)
        self.content_label.setStyleSheet(f"color: {t['text_primary']}; padding: 4px 0px 4px 0px;")

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def sizeHint(self):
        w = 616
        h = 72 # Increased height for larger font
        if self.is_expanded: 
            h += self.content_label.heightForWidth(580) + 16
        return QSize(w, h)


class SeparatorWidget(QWidget):
    """Thin divider between chat turns; extra height so follow-ups read clearly."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._height = 28
        self.setFixedHeight(self._height)
        self.current_theme = "light"

    def set_theme(self, theme):
        self.current_theme = theme
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = THEMES.get(self.current_theme, THEMES["light"])
        divider_color = QColor(t['divider'])

        grad = QLinearGradient(40, 0, self.width() - 40, 0)
        c = divider_color
        transparent = QColor(c.red(), c.green(), c.blue(), 0)
        grad.setColorAt(0, transparent)
        grad.setColorAt(0.2, c)
        grad.setColorAt(0.8, c)
        grad.setColorAt(1, transparent)

        pen = QPen(QBrush(grad), 1)
        painter.setPen(pen)

        y = self.height() // 2
        painter.drawLine(40, y, self.width() - 40, y)

    def sizeHint(self):
        return QSize(660, self._height)

class SmoothEntryWidget(QWidget):
    """
    Wrapper that animates a widget as it enters the list.

    animation values:
      "fade"    – gentle opacity 0→1 (350 ms, OutQuad)   — AI text / default
      "pop"     – snappy opacity 0→1 (130 ms, OutExpo)   — cards: links, persons, places, calc
      "slide"   – height 0→full + opacity 0→1 (220 ms)   — settings, terminal results
      "instant" – no animation                            — separators / reused items
    """
    def __init__(self, content_widget, parent=None, animate=True,
                 animation="fade", list_item=None, target_height=None):
        super().__init__(parent)
        self.content_widget = content_widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content_widget)

        self.opacity_eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_eff)

        if not animate or animation == "instant":
            self.opacity_eff.setOpacity(1)

        elif animation == "pop":
            self.opacity_eff.setOpacity(0)
            self.op_anim = QPropertyAnimation(self.opacity_eff, b"opacity")
            self.op_anim.setStartValue(0)
            self.op_anim.setEndValue(1)
            self.op_anim.setDuration(130)
            self.op_anim.setEasingCurve(QEasingCurve.Type.OutExpo)
            QTimer.singleShot(10, self.op_anim.start)

        elif animation == "slide":
            self.opacity_eff.setOpacity(0)
            final_h = target_height or content_widget.sizeHint().height() or 80
            self.setMaximumHeight(0)

            self.op_anim = QPropertyAnimation(self.opacity_eff, b"opacity")
            self.op_anim.setStartValue(0)
            self.op_anim.setEndValue(1)
            self.op_anim.setDuration(250)
            self.op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            self.h_anim = QPropertyAnimation(self, b"maximumHeight")
            self.h_anim.setStartValue(0)
            self.h_anim.setEndValue(final_h)
            self.h_anim.setDuration(220)
            self.h_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            if list_item is not None:
                _item = list_item
                self.h_anim.valueChanged.connect(
                    lambda v, it=_item: it.setSizeHint(QSize(-1, int(v)))
                )

            self.anim_group = QParallelAnimationGroup()
            self.anim_group.addAnimation(self.op_anim)
            self.anim_group.addAnimation(self.h_anim)
            QTimer.singleShot(10, self.anim_group.start)

        else:  # "fade" (default)
            self.opacity_eff.setOpacity(0)
            self.anim_group = QParallelAnimationGroup()

            self.op_anim = QPropertyAnimation(self.opacity_eff, b"opacity")
            self.op_anim.setStartValue(0)
            self.op_anim.setEndValue(1)
            self.op_anim.setDuration(350)
            self.op_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

            self.anim_group.addAnimation(self.op_anim)
            QTimer.singleShot(10, self.anim_group.start)
            
    def set_theme(self, theme):
        if hasattr(self.content_widget, 'set_theme'):
            self.content_widget.set_theme(theme)

    def enterEvent(self, event):
        super().enterEvent(event)
        parent = self.parent()
        while parent and not isinstance(parent, QListWidget):
            parent = parent.parent()
            
        if isinstance(parent, QListWidget):
            if hasattr(parent, '_keyboard_locked'):
                parent._keyboard_locked = False
                
            pos_in_viewport = self.mapTo(parent.viewport(), QPoint(0, 0))
            item = parent.itemAt(pos_in_viewport + QPoint(5, 5))
            
            if item:
                parent.setCurrentItem(item)

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
        self.current_theme = "light"
    
    def set_theme(self, theme):
        self.current_theme = theme
        t = THEMES.get(theme, THEMES["light"])
        self.hint.setStyleSheet(f"color: {t['text_secondary']}; opacity: 0.5;")

    def sizeHint(self):
        return QSize(150, 32)
    
    def set_active(self, active):
        if active:
            self.hint.setText("Press Tab to exit follow up mode")
            self.show()
        else:
            self.hint.setText("Press Tab to follow up")
            self.hide()

class UnscrollableTextEdit(QTextEdit):
    def wheelEvent(self, event):
        event.ignore()

class CollapsibleThinkingWidget(QWidget):
    size_changed = pyqtSignal()

    def __init__(self, thinking_text, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.current_theme = "light"
        
        # Track if this is the first time we're setting thinking (for auto-expand on first update)
        self._first_thinking_set = True

        # Header button - minimal, text-only design
        self.header_button = QPushButton("Reasoning process")
        self.header_button.setCheckable(True)
        self.header_button.setChecked(False)
        self.header_button.clicked.connect(self.toggle_content)
        self.header_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.layout.addWidget(self.header_button)

        # Content area
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 4, 0, 8)
        content_layout.setSpacing(0)

        self.thinking_text = UnscrollableTextEdit()
        self.thinking_text.setReadOnly(True)
        self.thinking_text.setFrameStyle(QFrame.Shape.NoFrame)
        self.thinking_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thinking_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thinking_text.setPlainText(thinking_text)

        font = QFont("Manrope", 12, QFont.Weight.Normal)
        self.thinking_text.setFont(font)
        self.thinking_text.document().setTextWidth(580)
        self.thinking_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        content_layout.addWidget(self.thinking_text)
        self.layout.addWidget(self.content_widget)

        # Initially hide content (collapsed)
        self.content_widget.setVisible(False)
        self.header_button.setText("▶ Reasoning process")
        
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        
        # Use a distinct gray for thinking process to differentiate from main content
        if self.current_theme == "dark":
            thinking_color = "#888888"
            hover_color = "#AAAAAA"
        else:
            thinking_color = "#666666"
            hover_color = "#333333"

        self.header_button.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                padding: 2px 0px;
                text-align: left;
                font-family: Manrope;
                font-size: 12px;
                font-weight: 500;
                color: {thinking_color};
            }}
            QPushButton:hover {{
                color: {hover_color};
            }}
            QPushButton:checked {{
                color: {hover_color};
            }}
        """)
        self.thinking_text.setStyleSheet(f"QTextEdit {{ background: transparent; color: {thinking_color}; padding: 0px; margin: 0px; line-height: 1.4; }}")

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def set_thinking_text(self, text):
        """Update the thinking text and ensure the widget is visible."""
        # Mark that we've set thinking at least once
        was_first = self._first_thinking_set
        self._first_thinking_set = False
        
        self.thinking_text.setPlainText(text)
        self.thinking_text.document().setTextWidth(580)
        
        # Force height update
        doc_height = self.thinking_text.document().size().height()
        self.thinking_text.setFixedHeight(int(doc_height + 24))
        
        # Ensure widget and button stay enabled and visible
        self.setVisible(True)
        self.setEnabled(True)
        self.header_button.setEnabled(True)
        
        # Auto-expand only on the very first thinking update (when user hasn't made a choice yet)
        if was_first and text.strip():
            self.set_collapsed(False)
        
        self.size_changed.emit()
        self.updateGeometry()
        parent = self.parent()
        while parent:
            if hasattr(parent, 'updateGeometry'):
                parent.updateGeometry()
            parent = parent.parent()

    def set_collapsed(self, collapsed):
        """Collapse (True) or expand (False) the thinking content. Widget stays visible either way."""
        self.setVisible(True)  # Make sure the widget itself is visible
        
        # Set the content visibility and button text based on collapsed state
        new_visibility = not collapsed
        self.content_widget.setVisible(new_visibility)
        
        # Update text if needed, or just keep it static. Let's use an arrow indicator.
        arrow = "▼" if new_visibility else "▶"
        self.header_button.setText(f"{arrow} Reasoning process")
        self.header_button.setChecked(new_visibility)
        
        if new_visibility:
            self.thinking_text.document().setTextWidth(580)
            doc_height = self.thinking_text.document().size().height()
            self.thinking_text.setFixedHeight(int(doc_height + 24))
            
        self.size_changed.emit()
        self.updateGeometry()
        parent = self.parent()
        while parent:
            if hasattr(parent, 'updateGeometry'):
                parent.updateGeometry()
            parent = parent.parent()

    def toggle_content(self):
        is_visible = self.content_widget.isVisible()
        self.set_collapsed(is_visible)

    def sizeHint(self):
        base_height = self.header_button.sizeHint().height()
        if self.content_widget.isVisible():
            # Recalculate text width every time for accurate height
            self.thinking_text.document().setTextWidth(580)
            content_height = self.thinking_text.document().size().height()
            # Add small padding for breathing room
            return QSize(660, int(base_height + content_height + 40))
        return QSize(660, base_height)


def _get_system_username():
    """Return the OS display name or username, capitalized."""
    import os, pwd
    try:
        # macOS / Linux: try GECOS (full name)
        gecos = pwd.getpwuid(os.getuid()).pw_gecos
        name = gecos.split(",")[0].strip()
        if name:
            return name.split()[0]  # first name only
    except Exception:
        pass
    name = os.environ.get("USER") or os.environ.get("USERNAME") or os.environ.get("LOGNAME") or "You"
    return name.capitalize()

_CACHED_USERNAME = None

def _username():
    global _CACHED_USERNAME
    if _CACHED_USERNAME is None:
        _CACHED_USERNAME = _get_system_username()
    return _CACHED_USERNAME


class _BubbleWidget(QWidget):
    """A single rounded chat bubble (user or assistant)."""

    BUBBLE_RADIUS = 18
    MAX_BUBBLE_WIDTH_FRACTION = 0.78  # bubble uses at most 78% of available width

    def __init__(self, text, sender="user", is_markdown=False, parent=None):
        super().__init__(parent)
        self.sender = sender          # "user" or "ai"
        self.is_markdown = is_markdown
        self.current_theme = "light"
        self._text = text

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        # Name label above bubble
        self.name_label = QLabel(_username() if sender == "user" else "Omni")
        self.name_label.setFont(QFont("Manrope", 11, QFont.Weight.DemiBold))
        if sender == "user":
            self.name_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            self.name_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self.name_label)

        # Bubble row (align bubble left or right)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.bubble = _BubbleInner(sender, is_markdown)
        self.bubble.set_text(text)

        if sender == "user":
            row.addStretch()
            row.addWidget(self.bubble)
        else:
            row.addWidget(self.bubble)
            row.addStretch()

        outer.addLayout(row)
        self.update_style()

    def set_text(self, text):
        self._text = text
        self.bubble.set_text(text)

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        name_color = t["text_secondary"]
        self.name_label.setStyleSheet(f"color: {name_color}; background: transparent;")
        self.bubble.set_theme(self.current_theme)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def sizeHint(self):
        w = 660
        if self.parent() and self.parent().width() > 100:
            w = self.parent().width()
        name_h = self.name_label.sizeHint().height() + 3
        bubble_h = self.bubble.sizeHint_for_width(w).height()
        return QSize(w, name_h + bubble_h + 2)


class _BubbleInner(QWidget):
    """Draws the rounded rect background and hosts the text inside."""

    PADDING_H = 14
    PADDING_V = 10
    RADIUS = 18
    MAX_FRACTION = 0.78

    def __init__(self, sender, is_markdown, parent=None):
        super().__init__(parent)
        self.sender = sender
        self.is_markdown = is_markdown
        self.current_theme = "light"
        self._bg = QColor("#000000")
        self._extra_top_widgets = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(self.PADDING_H, self.PADDING_V,
                               self.PADDING_H, self.PADDING_V)
        lay.setSpacing(0)

        if is_markdown:
            self.edit = UnscrollableTextEdit()
            self.edit.setReadOnly(True)
            self.edit.setFrameStyle(QFrame.Shape.NoFrame)
            self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self.edit.setFont(QFont("Manrope", 15, QFont.Weight.Normal))
            lay.addWidget(self.edit)
            self.label = None
        else:
            self.label = QLabel()
            self.label.setWordWrap(True)
            self.label.setFont(QFont("Manrope", 15, QFont.Weight.Normal))
            if sender == "user":
                self.label.setAlignment(Qt.AlignmentFlag.AlignRight)
            lay.addWidget(self.label)
            self.edit = None

        self.set_theme(self.current_theme)

    def insert_thinking_widget(self, widget):
        """Insert a thinking widget at the top of the bubble content (before answer text)."""
        self._extra_top_widgets.append(widget)
        self.layout().insertWidget(0, widget)

    def set_text(self, text):
        if self.edit is not None:
            self.edit.setVisible(bool(text and text.strip()))
            if text:
                self.edit.setMarkdown(text)
        elif self.label is not None:
            self.label.setText(text)

    def set_theme(self, theme):
        self.current_theme = theme
        t = THEMES.get(theme, THEMES["light"])
        if self.sender == "user":
            if theme == "dark":
                self._bg = QColor(255, 255, 255, 30)
                text_color = t["text_primary"]
            else:
                self._bg = QColor(0, 0, 0, 22)
                text_color = t["text_primary"]
        else:
            # AI bubble: subtle visible background so it reads as a proper chat bubble
            if theme == "dark":
                self._bg = QColor(255, 255, 255, 18)
            else:
                self._bg = QColor(0, 0, 0, 13)
            text_color = t["text_primary"]

        if self.edit is not None:
            self.edit.setStyleSheet(f"background: transparent; color: {text_color};")
        elif self.label is not None:
            self.label.setStyleSheet(f"color: {text_color}; background: transparent;")
        for w in self._extra_top_widgets:
            if hasattr(w, 'set_theme'):
                w.set_theme(theme)
        self.update()

    def paintEvent(self, event):
        if self._bg.alpha() == 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(self._bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), self.RADIUS, self.RADIUS)

    def _max_width(self):
        w = 660
        parent = self.parent()
        while parent:
            if hasattr(parent, 'width') and parent.width() > 100:
                w = parent.width()
                break
            parent = parent.parent() if hasattr(parent, 'parent') else None
        return int(w * self.MAX_FRACTION)

    def sizeHint_for_width(self, total_w):
        max_w = int(total_w * self.MAX_FRACTION)
        inner_w = max_w - 2 * self.PADDING_H

        if self.edit is not None:
            # AI bubble (markdown): full max width
            self.edit.document().setTextWidth(inner_w)
            has_text = bool(self.edit.toPlainText().strip())

            extra_h = sum(w.sizeHint().height() for w in self._extra_top_widgets if w.isVisible())

            if has_text:
                doc_h = int(self.edit.document().size().height())
                self.edit.setFixedHeight(doc_h)
                total_h = doc_h + extra_h + 2 * self.PADDING_V
                return QSize(max_w, max(total_h, 36))
            elif extra_h > 0:
                total_h = extra_h + 2 * self.PADDING_V
                return QSize(max_w, max(total_h, 36))
            else:
                return QSize(max_w, 0)
        else:
            # User bubble (label): shrink to fit text
            from PyQt6.QtGui import QFontMetrics
            text = self.label.text()
            fm = QFontMetrics(self.label.font())
            if text:
                single_line_w = fm.horizontalAdvance(text)
                natural_inner_w = min(single_line_w, inner_w)
                # Compute height at this natural width (may wrap for very long text)
                from PyQt6.QtCore import Qt as _Qt
                rect = fm.boundingRect(0, 0, natural_inner_w, 100000,
                                       _Qt.TextFlag.TextWordWrap, text)
                h = rect.height()
                use_inner_w = natural_inner_w
            else:
                use_inner_w = 60
                h = fm.height()

            use_w = use_inner_w + 2 * self.PADDING_H
            self.label.setFixedWidth(use_inner_w)
            self.setFixedWidth(use_w)
            total_h = h + 2 * self.PADDING_V
            return QSize(use_w, max(total_h, 36))

    def sizeHint(self):
        return self.sizeHint_for_width(660)


class AnswerWidget(QWidget):
    """
    Dual-mode answer widget.

    chat_mode=True  → chat bubbles: user question right, AI answer left with name labels.
                       Used for follow-up queries (Tab mode).
    chat_mode=False → simple full-width answer text, no bubbles, no name labels.
                       Used for the initial (first) query.
    """

    def __init__(self, text, query_text=None, thinking_text=None, chat_mode=False, parent=None):
        super().__init__(parent)
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(16, 6, 16, 6)
        self.outer_layout.setSpacing(8)
        self.current_theme = "light"
        self.chat_mode = chat_mode
        self._query_text = query_text or ""

        # ── USER BUBBLE (chat mode only) ───────────────────────────────
        if chat_mode:
            self.user_bubble = _BubbleWidget(self._query_text, sender="user")
            self.user_bubble.setVisible(bool(self._query_text))
            self.outer_layout.addWidget(self.user_bubble)
        else:
            self.user_bubble = None

        # ── AI CONTENT (created before thinking so thinking can be inserted inside) ──
        if chat_mode:
            self.ai_bubble = _BubbleWidget("", sender="ai", is_markdown=True)
            self.outer_layout.addWidget(self.ai_bubble)
            self.text_edit = self.ai_bubble.bubble.edit
        else:
            self.ai_bubble = None
            self.text_edit = UnscrollableTextEdit()
            self.text_edit.setReadOnly(True)
            self.text_edit.setFrameStyle(QFrame.Shape.NoFrame)
            self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self.text_edit.setFont(QFont("Manrope", 15, QFont.Weight.Normal))
            self.outer_layout.addWidget(self.text_edit)

        # ── THINKING (collapsible, optional) ──────────────────────────
        self.thinking_widget = None
        if thinking_text and thinking_text.strip():
            self.thinking_widget = CollapsibleThinkingWidget(thinking_text)
            self.thinking_widget.size_changed.connect(self.update_item_size)
            if chat_mode and self.ai_bubble:
                # In chat mode: thinking lives inside the AI bubble (above answer text)
                self.ai_bubble.bubble.insert_thinking_widget(self.thinking_widget)
            else:
                # In simple mode: thinking sits above the answer text area
                self.outer_layout.addWidget(self.thinking_widget)

        # Legacy alias
        self.query_label = self.user_bubble.name_label if self.user_bubble else None

        self._answer_text = text or ""
        if text:
            self.text_edit.setVisible(True)
            self.text_edit.setMarkdown(text)

        self.update_style()

    # ── Styling ────────────────────────────────────────────────────────

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        if self.user_bubble:
            self.user_bubble.set_theme(self.current_theme)
        if self.ai_bubble:
            self.ai_bubble.set_theme(self.current_theme)
        else:
            text_color = t.get("text_primary", "#ffffff")
            self.text_edit.setStyleSheet(f"background: transparent; color: {text_color};")
        if self.thinking_widget:
            self.thinking_widget.set_theme(self.current_theme)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def set_query_visible(self, visible):
        if self.user_bubble and self.chat_mode:
            self.user_bubble.setVisible(visible and bool(self._query_text))

    # ── Thinking ───────────────────────────────────────────────────────

    def ensure_thinking_widget(self):
        if self.thinking_widget is None:
            self.thinking_widget = CollapsibleThinkingWidget("")
            self.thinking_widget.size_changed.connect(self.update_item_size)
            self.thinking_widget.set_theme(self.current_theme)
            if self.chat_mode and self.ai_bubble:
                # In chat mode: insert thinking inside AI bubble (before answer text)
                self.ai_bubble.bubble.insert_thinking_widget(self.thinking_widget)
            else:
                # In simple mode: insert before AI content
                insert_idx = 1 if (self.chat_mode and self.user_bubble) else 0
                self.outer_layout.insertWidget(insert_idx, self.thinking_widget)

    def update_thinking(self, text):
        self.ensure_thinking_widget()
        self.thinking_widget.set_thinking_text(text)
        self.update_item_size()
        has_answer = bool(self.text_edit and self.text_edit.toPlainText().strip())
        if self.text_edit:
            self.text_edit.setVisible(has_answer)

    def set_thinking_collapsed(self, collapsed):
        if self.thinking_widget is not None:
            self.thinking_widget.set_collapsed(collapsed)
            self.update_item_size()

    # ── Size ───────────────────────────────────────────────────────────

    def sizeHint(self):
        w = 660
        if self.parent() and self.parent().width() > 100:
            w = self.parent().width()

        margins = self.outer_layout.contentsMargins()
        h = margins.top() + margins.bottom()
        spacing = self.outer_layout.spacing()

        if self.user_bubble and self.user_bubble.isVisible():
            h += self.user_bubble.sizeHint().height() + spacing

        # Skip thinking here when in chat mode — it's inside ai_bubble and counted there
        if self.thinking_widget and self.thinking_widget.isVisible() and not (self.chat_mode and self.ai_bubble):
            h += self.thinking_widget.sizeHint().height() + spacing

        if self.ai_bubble:
            ai_h = self.ai_bubble.sizeHint().height()
            if ai_h > 0:
                h += ai_h
        else:
            if self.text_edit and self.text_edit.isVisible():
                inner_w = w - margins.left() - margins.right()
                self.text_edit.document().setTextWidth(inner_w)
                doc_h = int(self.text_edit.document().size().height())
                if doc_h > 0:
                    self.text_edit.setFixedHeight(doc_h)
                    h += doc_h

        return QSize(w, max(h, 40))

    def update_item_size(self):
        list_widget = None
        parent = self.parent()
        while parent:
            if isinstance(parent, QListWidget):
                list_widget = parent
                break
            parent = parent.parent()

        if list_widget:
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                widget = list_widget.itemWidget(item)
                real_widget = getattr(widget, 'content_widget', widget)
                if real_widget is self:
                    item.setSizeHint(self.sizeHint())
                    break

        if hasattr(self.window(), "adjust_window_height"):
            self.window().adjust_window_height()

class StandardItemWidget(QWidget):
    def __init__(self, text, icon_name=None, font=None, color=None, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(24, 0, 24, 0)
        self.layout.setSpacing(12) # Increased from 4
        self.current_theme = "light"
        
        self.lbl_icon = QLabel()
        self.lbl_icon.setFixedSize(24, 24)
        
        if icon_name:
            self.icon_name_requested = icon_name
            self.layout.addWidget(self.lbl_icon)
            self.lbl_icon.setScaledContents(True) # Allow scaling of high-res icons
            
            if icon_name in ICON_CACHE:
                self.lbl_icon.setPixmap(ICON_CACHE[icon_name])
            else:
                # Load asynchronously via Main Thread Manager
                IconManager.instance().icon_loaded.connect(self.on_icon_loaded)
                IconManager.instance().request(icon_name)

        self.raw_text = text
        self.lbl_text = QLabel(text)
        if font: self.lbl_text.setFont(font)
        else: self.lbl_text.setFont(QFont("Manrope", 15, QFont.Weight.Medium))
        
        self.forced_color = color
        
        self.layout.addWidget(self.lbl_text)
        self.layout.addStretch()
        
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.update_style()

    def update_style(self):
        t = THEMES.get(self.current_theme, THEMES["light"])
        self.setStyleSheet("background: transparent;")
        
        primary = self.forced_color if self.forced_color else t['text_primary']
        
        if self.raw_text.startswith("Ask Omni:"):
            secondary = t['text_secondary']
            parts = self.raw_text.split(":", 1)
            label = parts[0] + ":"
            query = parts[1] if len(parts) > 1 else ""
            
            import html
            label = html.escape(label)
            query = html.escape(query)
            
            # Use spans for colors
            self.lbl_text.setText(f'<span style="color: {primary}">{label}</span><span style="color: {secondary}; font-weight: normal;">{query}</span>')
            # Clear stylesheet color to let HTML styling take precedence
            self.lbl_text.setStyleSheet("")
        else:
            self.lbl_text.setText(self.raw_text)
            self.lbl_text.setStyleSheet(f"color: {primary};")

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()

    def on_icon_loaded(self, name, pixmap):
        try:
            if hasattr(self, 'icon_name_requested') and self.icon_name_requested != name:
                 return
                 
            if not pixmap.isNull():
                ICON_CACHE[name] = pixmap
                self.lbl_icon.setPixmap(pixmap)
        except RuntimeError:
            # Widget likely deleted (wrapped C/C++ object has been deleted)
            pass

    def set_text(self, text):
        self.raw_text = text
        self.update_style()

    def sizeHint(self):
        return QSize(660, 72)

class RotatingLabel(QLabel):
    right_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rotation = 0
        self.base_speed = 1.5 
        self.current_speed = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.is_spinning = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
        super().mousePressEvent(event)

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

class MicWidget(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_theme = "light"
        
        # Load Mic Icon (ensure you have one or use text/unicode for now if needed, but we prefer icon)
        # Using a standard unicode mic for simplicity if icon fails, or better, draw it.
        # Let's try to load standard icon name "audio-input-microphone"
        self.icon_name = "audio-input-microphone"
        self.active = False
        
        IconManager.instance().icon_loaded.connect(self.on_icon_loaded)
        IconManager.instance().request(self.icon_name)
        
        self.setStyleSheet("""
            QLabel {
                background-color: transparent;
                border-radius: 16px;
            }
            QLabel:hover {
                background-color: rgba(0, 0, 0, 0.05);
            }
        """)
        
        # Pulse Animation
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(0.6) # Default idle opacity
        self.setGraphicsEffect(self.opacity_effect)
        
        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim.setDuration(800)
        self.anim.setLoopCount(-1)
        self.anim.setStartValue(0.4)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutSine)

    def set_theme(self, theme):
        self.current_theme = theme
        self.update_style()
        
    def update_style(self):
        # Update hover color based on theme
        t = THEMES.get(self.current_theme, THEMES["light"])
        hover_color = t['list_item_hover']
        if not self.active:
            self.setStyleSheet(f"""
                QLabel {{
                    background-color: transparent;
                    border-radius: 16px;
                    border: none;
                }}
                QLabel:hover {{
                    background-color: {hover_color};
                }}
            """)

    def on_icon_loaded(self, name, pixmap):
        try:
            if name != self.icon_name: return
            
            if not pixmap.isNull():
                target = int(min(self.width(), self.height()) * 0.7)
                scaled = pixmap.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.setPixmap(scaled)
            else:
                # Fallback text
                self.setText("🎤")
                self.setFont(QFont("Segoe UI Emoji", 16))
        except RuntimeError:
            pass

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_active(self, active):
        if self.active == active: return
        self.active = active
        
        if active:
            self.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 59, 48, 0.1); /* Red tint */
                    border-radius: 16px;
                    border: 1px solid rgba(255, 59, 48, 0.3);
                }
            """)
            self.anim.start()
        else:
            self.update_style()
            self.anim.stop()
            self.opacity_effect.setOpacity(0.6)

class GradientBorderFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MainFrame")
        self._hue_shift = 0.0
        self.base_speed = 0.005
        self.current_speed = self.base_speed
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.current_theme = "light"
        
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

    def set_theme(self, theme):
        self.current_theme = theme
        self.update()

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

        t = THEMES.get(self.current_theme, THEMES["light"])

        # Use theme base fill color (which includes opacity)
        base_fill = QColor(t['base_fill_color'])
        painter.fillPath(path, base_fill) 
        
        pen = painter.pen()
        pen.setColor(QColor(t['border_color']))
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
