"""
SettingsPanel — right-click-on-logo settings UI.
Three collapsible accordion sections: Transcription, AI Model, Privacy.
All collapsed by default.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont

from src.ui.styles import THEMES
import src.core.settings_store as settings_store

LANGUAGES = [
    ("auto", "Auto — detect language"),
    ("en",   "English"),
    ("pl",   "Polish"),
    ("de",   "German"),
    ("fr",   "French"),
    ("es",   "Spanish"),
    ("it",   "Italian"),
    ("pt",   "Portuguese"),
    ("ja",   "Japanese"),
    ("zh",   "Chinese"),
    ("uk",   "Ukrainian"),
    ("ru",   "Russian"),
    ("ar",   "Arabic"),
    ("nl",   "Dutch"),
]

_PRIVACY_ITEMS = [
    (
        "Voice transcription",
        "Audio is sent to the Groq Whisper API solely to convert speech to text. "
        "Groq does not store audio data for API users — processing is ephemeral "
        "and never feeds into any model training pipeline.",
    ),
    (
        "AI queries",
        "Query content is transmitted to xAI (Grok) or Groq over encrypted HTTPS. "
        "Neither company builds user profiles from API queries "
        "or shares your data with third parties.",
    ),
    (
        "Memory & history",
        "All conversational memory and history are stored exclusively on your local machine "
        "(~/.local/share/ai-memory-db). Nothing is synced to any cloud service.",
    ),
    (
        "Web search",
        "Search runs through a local SearXNG instance — queries never reach "
        "Google or any external search engine directly.",
    ),
    (
        "API keys",
        "Keys are stored locally in your .env file and ~/.config/omni/settings.json. "
        "They are only sent as authorization headers to their respective services "
        "(Groq, xAI) — nowhere else.",
    ),
    (
        "No telemetry",
        "Omni collects zero usage data, sends no diagnostic reports, "
        "and contains no tracking code. "
        "The app is open-source — you can verify this yourself.",
    ),
]


def _font(family: str, size: int, bold: bool = False, italic: bool = False) -> QFont:
    f = QFont(family, size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    if italic:
        f.setItalic(True)
    return f


# ---------------------------------------------------------------------------
# Collapsible section
# ---------------------------------------------------------------------------

class CollapsibleSection(QWidget):
    """
    Accordion row: clickable header that slides the content open/closed.
    Starts collapsed.
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._anim = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────
        self.header = QPushButton()
        self.header.setObjectName("CollapseHeader")
        self.header.setCheckable(True)
        self.header.setChecked(False)
        self.header.setFixedHeight(52)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.header.clicked.connect(self._toggle)

        h_inner = QHBoxLayout(self.header)
        h_inner.setContentsMargins(2, 0, 8, 0)
        h_inner.setSpacing(0)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("CollapseTitle")
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.chevron = QLabel("›")
        self.chevron.setObjectName("CollapseChevron")
        self.chevron.setFixedWidth(20)
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        h_inner.addWidget(self.title_lbl)
        h_inner.addStretch()
        h_inner.addWidget(self.chevron)

        # ── Content wrapper ──────────────────────────────────────────
        self.content_wrap = QWidget()
        self.content_wrap.setObjectName("CollapseContent")
        self._inner = QVBoxLayout(self.content_wrap)
        self._inner.setContentsMargins(2, 14, 2, 16)
        self._inner.setSpacing(10)

        # Start fully collapsed
        self.content_wrap.setMaximumHeight(0)
        self.content_wrap.hide()

        root.addWidget(self.header)
        root.addWidget(self.content_wrap)

    # public helpers ────────────────────────────────────────────────
    def add_widget(self, w: QWidget):
        self._inner.addWidget(w)

    def add_layout(self, lay):
        self._inner.addLayout(lay)

    # toggle ────────────────────────────────────────────────────────
    def _toggle(self, checked: bool):
        self._expanded = checked
        # Chevron: rotated › for open, straight › for closed
        self.chevron.setText("⌄" if checked else "›")

        if self._anim and self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()

        if checked:
            self.content_wrap.show()
            natural = self.content_wrap.sizeHint().height()
            self._anim = QPropertyAnimation(self.content_wrap, b"maximumHeight")
            self._anim.setDuration(240)
            self._anim.setStartValue(0)
            self._anim.setEndValue(max(natural, 80) + 24)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.start()
        else:
            self._anim = QPropertyAnimation(self.content_wrap, b"maximumHeight")
            self._anim.setDuration(200)
            self._anim.setStartValue(self.content_wrap.height())
            self._anim.setEndValue(0)
            self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
            self._anim.finished.connect(self.content_wrap.hide)
            self._anim.start()

    # theming ────────────────────────────────────────────────────────
    def apply_colors(self, primary: str, secondary: str, border: str,
                     hover_bg: str, press_bg: str):
        self.title_lbl.setStyleSheet(
            f"color: {primary}; font-family: 'Instrument Serif'; font-size: 18px;"
        )
        self.chevron.setStyleSheet(
            f"color: {secondary}; font-family: 'Manrope'; font-size: 15px;"
        )
        self.header.setStyleSheet(f"""
            QPushButton#CollapseHeader {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {border};
                border-radius: 0px;
            }}
            QPushButton#CollapseHeader:hover {{
                background: {hover_bg};
            }}
            QPushButton#CollapseHeader:pressed {{
                background: {press_bg};
            }}
        """)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class SettingsPanel(QWidget):
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_theme = "dark"
        self._sections = []
        self._build_ui()

    # ── Build ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.body = QWidget()
        self.body.setObjectName("SettingsBody")
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(22, 4, 22, 28)
        body_lay.setSpacing(0)
        body_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._sec_trans   = self._build_transcription()
        self._sec_model   = self._build_model()
        self._sec_privacy = self._build_privacy()

        for sec in [self._sec_trans, self._sec_model, self._sec_privacy]:
            body_lay.addWidget(sec)
            self._sections.append(sec)

        body_lay.addStretch()
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll)

    # ── Section builders ─────────────────────────────────────────────

    def _build_transcription(self) -> CollapsibleSection:
        sec = CollapsibleSection("Transcription")

        sec.add_widget(self._desc(
            "Default language for Whisper speech recognition. "
            "Picking a specific language makes transcription faster and more accurate."
        ))

        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName("SettingsCombo")
        self.lang_combo.setFixedHeight(40)
        self.lang_combo.setCursor(Qt.CursorShape.PointingHandCursor)

        saved = settings_store.get("transcription_language", "auto")
        for i, (code, name) in enumerate(LANGUAGES):
            self.lang_combo.addItem(name, code)
            if code == saved:
                self.lang_combo.setCurrentIndex(i)

        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        sec.add_widget(self.lang_combo)
        return sec

    def _build_model(self) -> CollapsibleSection:
        sec = CollapsibleSection("AI Model")

        sec.add_widget(self._desc(
            "Override the default model (xAI Grok) with any OpenAI-compatible API. "
            "Works with OpenAI, Ollama, LM Studio, Anthropic proxies, and more. "
            "Leave all fields empty to keep the default."
        ))

        sec.add_widget(self._lbl("API Base URL"))
        self.url_edit = self._edit("e.g. https://api.openai.com/v1")
        self.url_edit.setText(settings_store.get("custom_api_url", ""))
        sec.add_widget(self.url_edit)

        sec.add_widget(self._lbl("API Key"))
        self.key_edit = self._edit("sk-...", password=True)
        self.key_edit.setText(settings_store.get("custom_api_key", ""))
        sec.add_widget(self.key_edit)

        sec.add_widget(self._lbl("Model name"))
        self.model_edit = self._edit("e.g. gpt-4o, claude-3-5-sonnet")
        self.model_edit.setText(settings_store.get("custom_model", ""))
        sec.add_widget(self.model_edit)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(10)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("SaveBtn")
        self.save_btn.setFixedHeight(38)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._save_model)

        self.reset_btn = QPushButton("Reset to default")
        self.reset_btn.setObjectName("ResetBtn")
        self.reset_btn.setFixedHeight(38)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self._reset_model)

        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.reset_btn)
        sec.add_layout(btn_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("StatusLbl")
        self.status_lbl.setFont(_font("Manrope", 10))
        self.status_lbl.setWordWrap(True)
        sec.add_widget(self.status_lbl)

        return sec

    def _build_privacy(self) -> CollapsibleSection:
        sec = CollapsibleSection("Privacy")

        sec.add_widget(self._desc(
            "Omni is built with privacy as a first principle. "
            "Here's the full picture of what happens with your data."
        ))

        for heading, body_text in _PRIVACY_ITEMS:
            h = QLabel(heading)
            h.setObjectName("PrivacyHeading")
            h.setFont(_font("Manrope", 11, bold=True))
            h.setWordWrap(True)
            sec.add_widget(h)

            b = QLabel(body_text)
            b.setObjectName("PrivacyBody")
            b.setFont(_font("Manrope", 10))
            b.setWordWrap(True)
            sec.add_widget(b)

        return sec

    # ── Widget factories ─────────────────────────────────────────────

    def _desc(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("DescLbl")
        lbl.setFont(_font("Manrope", 10))
        lbl.setWordWrap(True)
        return lbl

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("FieldLbl")
        lbl.setFont(_font("Manrope", 10, bold=True))
        return lbl

    def _edit(self, placeholder: str, password: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName("SettingsEdit")
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(40)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        return edit

    # ── Slots ────────────────────────────────────────────────────────

    def _on_lang_changed(self, index: int):
        settings_store.set("transcription_language", self.lang_combo.itemData(index))

    def _save_model(self):
        url   = self.url_edit.text().strip()
        key   = self.key_edit.text().strip()
        model = self.model_edit.text().strip()

        if (url or key or model) and not (url and key and model):
            self._status("Fill in all three fields (URL, key, model) or leave them all empty.", error=True)
            return

        settings_store.save_settings({"custom_api_url": url, "custom_api_key": key, "custom_model": model})
        self._status(
            "Saved — changes take effect after restarting Omni." if url
            else "Reset to default model (xAI Grok) — restart required."
        )

    def _reset_model(self):
        self.url_edit.clear()
        self.key_edit.clear()
        self.model_edit.clear()
        settings_store.save_settings({"custom_api_url": "", "custom_api_key": "", "custom_model": ""})
        self._status("Default model restored — restart required.")

    def _status(self, msg: str, error: bool = False):
        self.status_lbl.setText(msg)
        self.status_lbl.setProperty("error", "true" if error else "false")
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)
        QTimer.singleShot(6000, lambda: self.status_lbl.setText(""))

    # ── Theming ──────────────────────────────────────────────────────

    def set_theme(self, theme_name: str):
        self.current_theme = theme_name
        t = THEMES.get(theme_name, THEMES["dark"])
        dark = theme_name == "dark"

        primary    = t["text_primary"]
        secondary  = t["text_secondary"]
        border     = t["border_color"]
        sel_border = t["selection_border"]
        selection  = t["selection_bg"]
        scrollbar  = t["scrollbar_handle"]

        field_bg       = "rgba(255,255,255,0.06)" if dark else "rgba(0,0,0,0.04)"
        field_bg_focus = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.07)"
        btn_bg         = "rgba(255,255,255,0.09)" if dark else "rgba(0,0,0,0.05)"
        btn_hover      = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.08)"
        btn_press      = "rgba(255,255,255,0.05)" if dark else "rgba(0,0,0,0.03)"
        hdr_hover      = "rgba(255,255,255,0.04)" if dark else "rgba(0,0,0,0.03)"
        hdr_press      = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.06)"
        reset_color    = secondary
        combo_popup_bg = "#1c1c1c" if dark else "#f3f3f3"

        for sec in self._sections:
            sec.apply_colors(primary, secondary, border, hdr_hover, hdr_press)

        self.body.setStyleSheet(f"""
            QWidget#SettingsBody {{ background: transparent; }}

            /* labels */
            QLabel {{ background: transparent; color: {primary}; }}
            QLabel#DescLbl, QLabel#FieldLbl, QLabel#PrivacyBody {{
                color: {secondary};
            }}
            QLabel#PrivacyHeading {{
                color: {primary};
                margin-top: 8px;
            }}
            QLabel#StatusLbl {{
                color: {secondary};
                font-family: "Manrope";
                font-size: 10px;
            }}
            QLabel#StatusLbl[error="true"] {{ color: #ff5f5f; }}

            /* inputs */
            QLineEdit#SettingsEdit {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 0px 13px;
                color: {primary};
                font-family: "Manrope";
                font-style: normal;
                font-size: 13px;
            }}
            QLineEdit#SettingsEdit:focus {{
                background: {field_bg_focus};
                border: 1px solid {sel_border};
            }}

            /* combo */
            QComboBox#SettingsCombo {{
                background: {field_bg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 0px 13px;
                color: {primary};
                font-family: "Manrope";
                font-style: normal;
                font-size: 13px;
            }}
            QComboBox#SettingsCombo:focus {{
                border: 1px solid {sel_border};
            }}
            QComboBox#SettingsCombo::drop-down {{
                border: none;
                width: 28px;
                subcontrol-position: right center;
            }}
            QComboBox#SettingsCombo::down-arrow {{
                width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {secondary};
            }}
            QComboBox#SettingsCombo QAbstractItemView {{
                background: {combo_popup_bg};
                color: {primary};
                border: 1px solid {border};
                border-radius: 8px;
                selection-background-color: {selection};
                outline: none;
                padding: 4px;
                font-family: "Manrope";
                font-size: 13px;
            }}

            /* buttons */
            QPushButton#SaveBtn {{
                background: {btn_bg};
                border: 1px solid {border};
                border-radius: 10px;
                color: {primary};
                font-family: "Manrope";
                font-size: 12px;
                padding: 0px 18px;
            }}
            QPushButton#SaveBtn:hover {{ background: {btn_hover}; }}
            QPushButton#SaveBtn:pressed {{ background: {btn_press}; }}

            QPushButton#ResetBtn {{
                background: transparent;
                border: 1px solid {border};
                border-radius: 10px;
                color: {reset_color};
                font-family: "Manrope";
                font-size: 12px;
                padding: 0px 18px;
            }}
            QPushButton#ResetBtn:hover {{
                background: {btn_hover};
                color: {primary};
            }}
            QPushButton#ResetBtn:pressed {{ background: {btn_press}; }}
        """)

        self.scroll.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{
                border: none; background: transparent; width: 5px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {scrollbar}; min-height: 32px; border-radius: 2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
