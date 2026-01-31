import os
import threading
import requests
from urllib.parse import urlparse
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QPainterPath

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
            QWidget#ActionCard:hover {
                background-color: rgba(255, 255, 255, 0.45);
                border: 1px solid rgba(255, 255, 255, 0.6);
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
            background-color: #FFFFFF; 
            color: #333333; 
            font-size: 10px; 
            border-radius: 5px; 
            border: 1px solid rgba(0,0,0,0.05);
        """)

        self.action_label = QLabel(f"WEBSITE")
        self.action_label.setFont(QFont("Manrope", 8, QFont.Weight.Bold))
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

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.title_label)

        layout.addWidget(self.card)
        self.fetch_icon()

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

class AppActionWidget(LinkActionWidget):
    def __init__(self, name, parent=None):
        # We don't have a URL, so pass None or empty
        super().__init__(f"Open {name.title()}", "", "Application", parent)
        
        # Customize icons/text for App Launcher
        self.action_label.setText("LAUNCH")
        self.icon_label.setText("🚀")
        self.icon_label.setStyleSheet("""
            background-color: #333333; 
            color: #FFFFFF; 
            font-size: 12px; 
            border-radius: 8px;
        """)
        
        # Override layout for specific app styling if needed
        # Re-using LinkActionWidget layout is fine, but let's customize the right side
        
        # We can add a visual cue like "Press Enter"
        layout = self.card.layout() # It's a QVBoxLayout in LinkActionWidget
        # We want to access the top row or just append to bottom?
        # LinkActionWidget has:
        # - Top Row (Icon + Label)
        # - Title Label
        
        # Let's add a "Press Enter" hint at the bottom right?
        # Or just rely on standard look.
        
        # Let's customize the title color to be more distinct
        self.title_label.setStyleSheet("color: #111111; margin-top: 0px;")

class InstallActionWidget(LinkActionWidget):
    def __init__(self, name, website_url, parent=None):
        url_for_icon = website_url if website_url else f"https://google.com/search?q={name}"
        super().__init__(f"Install {name}", url_for_icon, "System Package Manager", parent)

        self.action_label.setText("")
        self.icon_label.setText("↓")
        self.icon_label.setStyleSheet("""
            background-color: #333333; 
            color: #FFFFFF; 
            font-size: 14px; 
            border-radius: 8px;
        """)

        layout = QHBoxLayout(self.action_label)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 3D Keycap Styling - Refined
        def create_key(text):
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("""
                background-color: #FFFFFF;
                border: 1px solid #D6D6D6;
                border-bottom: 2px solid #C0C0C0;
                border-radius: 5px;
                color: #333333;
                padding: 3px 8px;
                font-family: "Manrope";
                font-size: 10px;
                font-weight: 800;
                min-width: 24px;
            """)
            return lbl

        layout.addWidget(create_key("TAB"))

        lbl_install = QLabel("INSTALL APP")
        lbl_install.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        lbl_install.setStyleSheet("color: #000000; letter-spacing: 0.5px;")
        layout.addWidget(lbl_install)

        layout.addSpacing(12)
        layout.addWidget(create_key("↵"))

        lbl_web = QLabel("VISIT SITE")
        lbl_web.setFont(QFont("Manrope", 10, QFont.Weight.Bold))
        lbl_web.setStyleSheet("color: #999999; letter-spacing: 0.5px;")
        layout.addWidget(lbl_web)
        layout.addStretch()

class FileActionWidget(QWidget):
    def __init__(self, filename, path, icon_name=None, parent=None):
        super().__init__(parent)
        self.filename = filename
        self.path = path

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
            QWidget#ActionCard:hover {
                background-color: rgba(255, 255, 255, 0.45);
                border: 1px solid rgba(255, 255, 255, 0.6);
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(4)

        # Top Row: Icon + Label
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Load icon
        if not icon_name:
            icon_name = "folder" if os.path.isdir(path) else "text-x-generic"
        
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(18, 18))
        else:
            self.icon_label.setText("📄")

        self.icon_label.setStyleSheet("""
            background-color: #FFFFFF; 
            border-radius: 6px; 
            border: 1px solid rgba(0,0,0,0.05);
        """)

        self.action_label = QLabel("FILE")
        self.action_label.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #888888; letter-spacing: 1.0px;")

        top_layout.addWidget(self.icon_label)
        top_layout.addWidget(self.action_label)
        top_layout.addStretch()

        # Title
        self.title_label = QLabel(filename)
        self.title_label.setWordWrap(True)
        self.title_label.setFont(QFont("Instrument Serif", 20, QFont.Weight.Normal))
        self.title_label.setStyleSheet("color: #050505; margin-top: 2px;")

        # Description (Path)
        display_path = path.replace(os.path.expanduser("~"), "~")
        self.desc_label = QLabel(display_path)
        self.desc_label.setWordWrap(True)
        self.desc_label.setFont(QFont("Manrope", 11, QFont.Weight.Medium))
        self.desc_label.setStyleSheet("color: #555555;")

        card_layout.addWidget(top_row)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.desc_label)

        # Content Peek
        self.peek_label = QLabel()
        self.peek_label.setWordWrap(True)
        # self.peek_label.setFont(QFont("JetBrains Mono", 10)) # JetBrains Mono might not be installed
        self.peek_label.setFont(QFont("Consolas", 10))
        self.peek_label.setStyleSheet("color: #777777; background-color: rgba(0,0,0,0.03); border-radius: 8px; padding: 8px; margin-top: 4px;")
        self.peek_label.setHidden(True)
        card_layout.addWidget(self.peek_label)

        layout.addWidget(self.card)
        
        if not os.path.isdir(path):
            # Check for image extension
            _, ext = os.path.splitext(path)
            if ext.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
                self.load_image_preview()
            else:
                threading.Thread(target=self.peek_content, daemon=True).start()

    def load_image_preview(self):
        try:
            pix = QPixmap(self.path)
            if not pix.isNull():
                 # Scale comfortably (e.g. max height 300)
                 scaled = pix.scaledToHeight(250, Qt.TransformationMode.SmoothTransformation)
                 self.peek_label.setPixmap(scaled)
                 # Remove the "box" styling for images
                 self.peek_label.setStyleSheet("background: transparent; padding: 0; margin-top: 4px;")
                 self.peek_label.setHidden(False)
        except: pass

    def peek_content(self):
        try:
            # Only read first 300 chars
            with open(self.path, 'r', errors='ignore') as f:
                  content = f.read(300).strip()
                  if content:
                       # Clean up content (no tabs, multiple newlines)
                       lines = [l.strip() for l in content.split('\n') if l.strip()]
                       snippet = "\n".join(lines[:3]) # First 3 lines
                       if snippet:
                           from PyQt6.QtCore import QMetaObject, Q_ARG
                           QMetaObject.invokeMethod(self.peek_label, "setText", Qt.ConnectionType.QueuedConnection, Q_ARG(str, snippet))
                           QMetaObject.invokeMethod(self.peek_label, "show", Qt.ConnectionType.QueuedConnection)
                           # Force size update
                           if hasattr(self.window(), "adjust_window_height"):
                               QMetaObject.invokeMethod(self.window(), "adjust_window_height", Qt.ConnectionType.QueuedConnection)
        except: pass

    def sizeHint(self):
        w = getattr(self.parent(), 'width', lambda: 660)()
        if self.layout():
            h = self.layout().heightForWidth(w)
            if h > 0: return QSize(w, h + 35)
            # Basic fallback height
            return QSize(660, 96)
        return QSize(660, 96)

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
            QWidget#ActionCard:hover {
                background-color: rgba(255, 255, 255, 0.45);
                border: 1px solid rgba(255, 255, 255, 0.6);
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
        self.avatar.setStyleSheet("background-color: #F7F7F7; color: #CCCCCC; font-family: 'Instrument Serif'; font-size: 56px; border-radius: 8px; border: 1px solid #EDEDED;")

        if self.image_url:
            logging.info(f"Starting image download for {name}: {self.image_url}")
            threading.Thread(target=self._download_image, daemon=True).start()

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
