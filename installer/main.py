"""
Omni Installer — PyQt6 multi-step wizard
Redesigned with premium aesthetics and compelling copy.
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import json
import platform
import stat
import tempfile
import threading
import ctypes
import ctypes.util
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve,
    QTimer, QPoint, QRect, QSize, pyqtProperty, QObject, QEvent,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPainterPath,
    QBrush, QPen, QPixmap, QLinearGradient, QRadialGradient, QConicalGradient, QIcon,
    QCursor, QMouseEvent, QPalette,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QProgressBar,
    QScrollArea, QCheckBox, QFrame, QSizePolicy, QSpacerItem,
)

# ── Constants ──────────────────────────────────────────────────────────────────

VERSION = "0.5.0"
WINDOW_W, WINDOW_H = 780, 540

# Palette — matches actual Omni app: deep dark, aurora/iridescent accent
BG         = "#0D0D10"
BG_CARD    = "#141418"
BG_CARD2   = "#18181D"
BORDER     = "#24242C"
# Aurora accent colours matching the app's gradient border
ACCENT      = "#8B5CF6"       # violet — primary CTA
ACCENT2     = "#A78BFA"       # lighter violet
ACCENT_BLUE = "#3B82F6"       # aurora blue
ACCENT_PINK = "#EC4899"       # aurora pink
TEXT_PRI   = "#EEEDF2"        # cool white
TEXT_SEC   = "#A8A7B2"        # cool grey — lifted for glass background
TEXT_HINT  = "#6E6D78"        # dim hint — was #3E3D48, now readable on glass
SUCCESS    = "#4ADE80"
WARN       = "#FBBF24"
ERROR      = "#F87171"
RADIUS     = 20

INSTALL_DIR      = Path.home() / "Library" / "Application Support" / "Omni"
INSTALL_MARKER   = INSTALL_DIR / ".installed"       # written after install step
SETUP_MARKER     = INSTALL_DIR / ".setup_complete"  # written after full wizard
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.omni.app.plist"
CONFIG_DIR   = Path.home() / ".config" / "omni"


# ── Resource helpers ───────────────────────────────────────────────────────────

def resource(rel: str) -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent.parent
    return str(base / rel)


def omni_src() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "_omni_src"
    return Path(__file__).parent.parent


def load_fonts():
    manrope = resource("assets/Manrope/Manrope-VariableFont_wght.ttf")
    instr   = resource("assets/Instrument_Serif/InstrumentSerif-Italic.ttf")
    for path in (manrope, instr):
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)


# ── Global stylesheet ──────────────────────────────────────────────────────────

def qss_base() -> str:
    return f"""
    QWidget {{
        background: transparent;
        color: {TEXT_PRI};
        font-family: 'Manrope', 'SF Pro Text', sans-serif;
    }}
    QLabel {{ background: transparent; }}

    QPushButton {{
        border: none;
        border-radius: 9px;
        padding: 0px 24px;
        font-size: 13px;
        font-family: 'Manrope', 'SF Pro Text', sans-serif;
        font-weight: 600;
        letter-spacing: 0.2px;
    }}
    QPushButton#primary {{
        background: {ACCENT};
        color: #fff;
    }}
    QPushButton#primary:hover  {{ background: {ACCENT2}; }}
    QPushButton#primary:pressed {{ background: #7C3AED; }}
    QPushButton#primary:disabled {{
        background: #1E1E26;
        color: {TEXT_HINT};
    }}

    QPushButton#secondary {{
        background: {BG_CARD2};
        color: {TEXT_SEC};
        border: 1px solid {BORDER};
    }}
    QPushButton#secondary:hover  {{ background: #222228; color: {TEXT_PRI}; }}
    QPushButton#secondary:pressed {{ background: #1E1E23; }}
    QPushButton#secondary:disabled {{ opacity: 0.4; }}

    QPushButton#ghost {{
        background: transparent;
        color: {TEXT_SEC};
        padding: 4px 10px;
        font-size: 12px;
    }}
    QPushButton#ghost:hover {{ color: {TEXT_PRI}; }}

    QPushButton#link {{
        background: transparent;
        color: {ACCENT2};
        padding: 4px 0px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton#link:hover {{ color: {TEXT_PRI}; }}

    QScrollBar:vertical {{
        width: 3px;
        background: transparent;
        border-radius: 1px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 1px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QCheckBox {{
        spacing: 10px;
        font-size: 13px;
        color: {TEXT_PRI};
        font-weight: 500;
    }}
    QCheckBox::indicator {{
        width: 17px; height: 17px;
        border-radius: 5px;
        border: 1.5px solid {BORDER};
        background: {BG_CARD};
    }}
    QCheckBox::indicator:hover {{
        border-color: {ACCENT};
    }}
    QCheckBox::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
    }}
    """


# ── Shared widgets ─────────────────────────────────────────────────────────────

class Card(QFrame):
    """Rounded card with subtle border."""
    def __init__(self, parent=None, color=None):
        super().__init__(parent)
        self._color = color or BG_CARD

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(self._color)))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        p.end()


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background: {BORDER};")


class StepDots(QWidget):
    """Minimal pill-shaped step indicator."""
    def __init__(self, total: int, parent=None):
        super().__init__(parent)
        self.total   = total
        self.current = 0
        self.setFixedHeight(24)

    def set_step(self, step: int):
        self.current = step
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot_h  = 3
        dot_gap = 5
        dot_active_w = 20
        dot_w  = 6
        total_w = self.total * dot_w + (self.total - 1) * dot_gap + (dot_active_w - dot_w)
        x = (self.width() - total_w) // 2
        y = (self.height() - dot_h) // 2
        for i in range(self.total):
            if i == self.current:
                p.setBrush(QBrush(QColor(ACCENT)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(x, y, dot_active_w, dot_h, dot_h//2, dot_h//2)
                x += dot_active_w + dot_gap
            elif i < self.current:
                p.setBrush(QBrush(QColor(ACCENT).darker(160)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(x, y, dot_w, dot_h, dot_h//2, dot_h//2)
                x += dot_w + dot_gap
            else:
                p.setBrush(QBrush(QColor(BORDER)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(x, y, dot_w, dot_h, dot_h//2, dot_h//2)
                x += dot_w + dot_gap
        p.end()


class SlimBar(QWidget):
    """2px gradient progress bar."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(2)
        self._value = 0

    def set_value(self, v: int):
        self._value = max(0, min(100, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(BORDER)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), 2, 1, 1)
        fill_w = int(self.width() * self._value / 100)
        if fill_w > 0:
            grad = QLinearGradient(0, 0, fill_w, 0)
            grad.setColorAt(0, QColor(ACCENT))
            grad.setColorAt(1, QColor(ACCENT2))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 0, fill_w, 2, 1, 1)
        p.end()


class CheckRow(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(12)

        self.icon_lbl = QLabel("·")
        self.icon_lbl.setFixedWidth(16)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet(f"color: {TEXT_HINT}; font-size: 18px;")

        self.text_lbl = QLabel(label)
        self.text_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 13px; font-weight: 500;")

        self.status_lbl = QLabel("—")
        self.status_lbl.setStyleSheet(f"color: {TEXT_HINT}; font-size: 12px;")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.fix_btn = QPushButton("Fix →")
        self.fix_btn.setObjectName("link")
        self.fix_btn.setFixedWidth(46)
        self.fix_btn.hide()

        row.addWidget(self.icon_lbl)
        row.addWidget(self.text_lbl, 1)
        row.addWidget(self.status_lbl)
        row.addWidget(self.fix_btn)
        self.setVisible(False)

    def show_row(self, delay=0):
        QTimer.singleShot(delay, lambda: self.setVisible(True))

    def set_ok(self, detail=""):
        self.icon_lbl.setText("✓")
        self.icon_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 13px; font-weight: 700;")
        self.text_lbl.setStyleSheet(f"color: {TEXT_PRI}; font-size: 13px; font-weight: 500;")
        self.status_lbl.setText(detail or "OK")
        self.status_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 12px;")
        self.fix_btn.hide()

    def set_fail(self, detail="", fix_cb: Callable | None = None):
        self.icon_lbl.setText("✗")
        self.icon_lbl.setStyleSheet(f"color: {ERROR}; font-size: 13px; font-weight: 700;")
        self.text_lbl.setStyleSheet(f"color: {TEXT_PRI}; font-size: 13px; font-weight: 500;")
        self.status_lbl.setText(detail or "Not found")
        self.status_lbl.setStyleSheet(f"color: {ERROR}; font-size: 12px;")
        if fix_cb:
            self.fix_btn.show()
            self.fix_btn.clicked.connect(fix_cb)

    def set_warn(self, detail=""):
        self.icon_lbl.setText("!")
        self.icon_lbl.setStyleSheet(f"color: {WARN}; font-size: 13px; font-weight: 700;")
        self.text_lbl.setStyleSheet(f"color: {TEXT_PRI}; font-size: 13px; font-weight: 500;")
        self.status_lbl.setText(detail or "Warning")
        self.status_lbl.setStyleSheet(f"color: {WARN}; font-size: 12px;")


def make_label(text, size=13, color=TEXT_PRI, weight=400, family=None) -> QLabel:
    lbl = QLabel(text)
    fam = family or "'Manrope', 'SF Pro Text', sans-serif"
    lbl.setStyleSheet(f"""
        color: {color};
        font-size: {size}px;
        font-weight: {weight};
        font-family: {fam};
    """)
    return lbl


def page_header(title: str, subtitle: str) -> tuple[QLabel, QLabel]:
    h = make_label(title, size=24, weight=700)
    s = make_label(subtitle, size=13, color=TEXT_SEC)
    s.setWordWrap(True)
    return h, s


# ── Pages ──────────────────────────────────────────────────────────────────────

class WelcomePage(QWidget):
    def __init__(self, on_next):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # LEFT panel — hero copy
        # RIGHT panel — feature list
        # We use a two-column layout for the main content area

        # Top spacer
        root.addSpacing(52)

        # Logo + name row
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(52, 0, 52, 0)
        logo_path = resource("assets/omni.png")
        if os.path.exists(logo_path):
            pm = QPixmap(logo_path).scaled(
                36, 36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            logo_img = QLabel()
            logo_img.setPixmap(pm)
            logo_row.addWidget(logo_img)
            logo_row.addSpacing(10)

        omni_word = make_label("Omni", size=15, weight=700)
        version_tag = make_label(f"v{VERSION}", size=11, color=TEXT_HINT)
        logo_row.addWidget(omni_word)
        logo_row.addSpacing(8)
        logo_row.addWidget(version_tag)
        logo_row.addStretch()
        root.addLayout(logo_row)

        root.addSpacing(44)

        # Hero headline
        headline_layout = QVBoxLayout()
        headline_layout.setContentsMargins(52, 0, 52, 0)
        headline_layout.setSpacing(0)

        # "Ask your computer anything."
        hero = QLabel("Ask your computer\nanything.")
        hero.setStyleSheet(f"""
            QLabel {{
                font-family: 'Instrument Serif', 'Georgia', serif;
                font-style: italic;
                font-size: 46px;
                color: {TEXT_PRI};
                line-height: 1.1;
            }}
        """)
        headline_layout.addWidget(hero)
        root.addLayout(headline_layout)

        root.addSpacing(16)

        # Subheadline
        sub_layout = QHBoxLayout()
        sub_layout.setContentsMargins(52, 0, 52, 0)
        sub = make_label(
            "Omni indexes everything on your Mac and remembers every\n"
            "conversation — so you can find anything, do anything,\n"
            "just by asking.",
            size=14, color=TEXT_SEC
        )
        sub.setLineWidth(0)
        sub_layout.addWidget(sub)
        sub_layout.addStretch()
        root.addLayout(sub_layout)

        root.addSpacing(36)

        # Feature pills row
        pills_layout = QHBoxLayout()
        pills_layout.setContentsMargins(52, 0, 52, 0)
        pills_layout.setSpacing(8)
        pills_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for label in ["Semantic file search", "AI that remembers you", "Control your computer"]:
            pill = QLabel(label)
            pill.setStyleSheet(f"""
                QLabel {{
                    background: {BG_CARD2};
                    color: {TEXT_SEC};
                    border: 1px solid {BORDER};
                    border-radius: 20px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: 600;
                    letter-spacing: 0.3px;
                }}
            """)
            pills_layout.addWidget(pill)
        root.addLayout(pills_layout)

        root.addStretch(1)

        # Bottom divider
        root.addWidget(Divider())
        root.addSpacing(0)

        # Footer row
        footer = QHBoxLayout()
        footer.setContentsMargins(52, 16, 52, 20)

        footer_note = make_label("Takes about 5 minutes  ·  macOS 12+", size=12, color=TEXT_HINT)
        footer.addWidget(footer_note)
        footer.addStretch()

        btn = QPushButton("Get Started →")
        btn.setObjectName("primary")
        btn.setFixedSize(148, 40)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.clicked.connect(on_next)
        footer.addWidget(btn)

        root.addLayout(footer)

    def paintEvent(self, event):
        """Subtle aurora glow top-left, matching the app's border."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QRadialGradient(80, 160, 260)
        grad.setColorAt(0, QColor(139, 92, 246, 22))   # violet glow
        grad.setColorAt(0.5, QColor(59, 130, 246, 10))  # blue fade
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()


class SystemCheckPage(QWidget):
    def __init__(self, on_next, on_back):
        super().__init__()
        self.on_next = on_next
        self._checks_passed = False

        v = QVBoxLayout(self)
        v.setContentsMargins(52, 44, 52, 0)
        v.setSpacing(0)

        h, sub = page_header(
            "Checking your Mac",
            "We'll make sure everything's in order before we begin."
        )
        v.addWidget(h)
        v.addSpacing(4)
        v.addWidget(sub)
        v.addSpacing(24)

        card = Card()
        card_v = QVBoxLayout(card)
        card_v.setContentsMargins(0, 6, 0, 6)
        card_v.setSpacing(0)

        self.row_macos  = CheckRow("macOS 12 Monterey or later")
        self.row_brew   = CheckRow("Homebrew package manager")
        self.row_python = CheckRow("Python 3.10 or newer")
        self.row_disk   = CheckRow("~10 GB free disk space")
        self.row_net    = CheckRow("Internet connection")

        for i, row in enumerate([self.row_macos, self.row_brew, self.row_python, self.row_disk, self.row_net]):
            card_v.addWidget(row)
            if i < 4:
                card_v.addWidget(Divider())

        v.addWidget(card)
        v.addStretch(1)

        self.status_msg = make_label("", size=12, color=TEXT_SEC)
        self.status_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.status_msg)
        v.addSpacing(12)

        v.addWidget(Divider())
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 16, 0, 20)

        self.back_btn = QPushButton("← Back")
        self.back_btn.setObjectName("secondary")
        self.back_btn.setFixedSize(96, 38)
        self.back_btn.clicked.connect(on_back)

        self.next_btn = QPushButton("Continue →")
        self.next_btn.setObjectName("primary")
        self.next_btn.setFixedSize(140, 38)
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(on_next)

        footer.addWidget(self.back_btn)
        footer.addStretch()
        footer.addWidget(self.next_btn)
        v.addLayout(footer)

        QTimer.singleShot(400, self.run_checks)

    def run_checks(self):
        delay = 0
        for row in [self.row_macos, self.row_brew, self.row_python, self.row_disk, self.row_net]:
            row.show_row(delay)
            delay += 160
        QTimer.singleShot(delay + 100, self._do_checks)

    def _do_checks(self):
        passed = True

        mac_ver = platform.mac_ver()[0]
        try:
            major = int(mac_ver.split(".")[0])
            if major >= 12:
                self.row_macos.set_ok(f"macOS {mac_ver}")
            else:
                self.row_macos.set_fail(f"macOS {mac_ver} — need 12+")
                passed = False
        except Exception:
            self.row_macos.set_fail("Unknown")
            passed = False

        # shutil.which only searches PATH, which is stripped inside .app bundles.
        # Probe the fixed Homebrew locations directly (Apple Silicon + Intel).
        brew = (
            shutil.which("brew")
            or (os.path.exists("/opt/homebrew/bin/brew") and "/opt/homebrew/bin/brew")
            or (os.path.exists("/usr/local/bin/brew") and "/usr/local/bin/brew")
        )
        if brew:
            self.row_brew.set_ok("Found")
        else:
            self.row_brew.set_warn("Will be installed automatically")

        import re as _re
        py_found, py_ver = None, ""

        # Build a broad candidate list.  shutil.which only searches PATH, which
        # is stripped to /usr/bin:/bin inside an .app bundle — so we also probe
        # Homebrew, MacPorts, pyenv, and the Framework locations directly.
        _brew_prefixes = ["/opt/homebrew", "/usr/local"]  # Apple Silicon / Intel
        _candidates = []
        for ver in ("3.13", "3.12", "3.11", "3.10"):
            # versioned binary names via PATH (works in terminal launches)
            _candidates.append((f"python{ver}", ver))
        for prefix in _brew_prefixes:
            for ver in ("3.13", "3.12", "3.11", "3.10"):
                _candidates.append((f"{prefix}/bin/python{ver}", ver))
        # pyenv shim and generic python3
        _candidates += [
            (f"{Path.home()}/.pyenv/shims/python3", None),
            ("/usr/bin/python3", None),
            ("/usr/local/bin/python3", None),
            ("/opt/homebrew/bin/python3", None),
        ]

        for path_or_name, hint_ver in _candidates:
            exe = shutil.which(path_or_name) or (
                path_or_name if os.path.isfile(path_or_name) and os.access(path_or_name, os.X_OK)
                else None
            )
            if not exe:
                continue
            if hint_ver:
                major, minor = int(hint_ver.split(".")[0]), int(hint_ver.split(".")[1])
                if (major, minor) >= (3, 10):
                    py_found, py_ver = exe, hint_ver
                    break
            else:
                res = subprocess.run([exe, "--version"], capture_output=True, text=True)
                m = _re.search(r"Python (\d+)\.(\d+)", res.stdout + res.stderr)
                if m and (int(m.group(1)), int(m.group(2))) >= (3, 10):
                    py_found, py_ver = exe, f"{m.group(1)}.{m.group(2)}"
                    break

        if py_found:
            self.row_python.set_ok(f"Python {py_ver}")
        else:
            self.row_python.set_warn("Will be installed via Homebrew")

        stat_info = shutil.disk_usage(Path.home())
        free_gb = stat_info.free / (1024 ** 3)
        if free_gb >= 8:
            self.row_disk.set_ok(f"{free_gb:.0f} GB free")
        else:
            self.row_disk.set_fail(f"{free_gb:.1f} GB free — need ~10 GB")
            passed = False

        try:
            import urllib.request
            urllib.request.urlopen("https://pypi.org", timeout=5)
            self.row_net.set_ok("Connected")
        except Exception:
            self.row_net.set_fail("No connection")
            passed = False

        if passed:
            self.status_msg.setText("All good — ready to install.")
            self.status_msg.setStyleSheet(f"color: {SUCCESS}; font-size: 12px;")
        else:
            self.status_msg.setText("Some issues found. You can still continue, but install may fail.")
            self.status_msg.setStyleSheet(f"color: {WARN}; font-size: 12px;")

        self.next_btn.setEnabled(True)
        self._checks_passed = passed


# ── Install worker (unchanged logic) ──────────────────────────────────────────

class InstallWorker(QThread):
    progress     = pyqtSignal(int, str)
    log_line     = pyqtSignal(str)
    finished_ok  = pyqtSignal()
    finished_err = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    def _run_cmd(self, cmd, env=None):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, env=env, timeout=600,
                start_new_session=True,  # detach from app's macOS activation context
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except Exception as e:
            return 1, "", str(e)

    def _emit_log(self, line):
        self.log_line.emit(line)

    def run(self):
        try:
            self._install()
        except Exception as e:
            self.finished_err.emit(str(e))

    def _install(self):
        env = os.environ.copy()
        for brew_path in ("/opt/homebrew/bin", "/usr/local/bin"):
            if brew_path not in env.get("PATH", ""):
                env["PATH"] = brew_path + ":" + env.get("PATH", "")

        self.progress.emit(0, "Starting…")

        # Quick pre-flight checks
        import urllib.request
        free_gb = shutil.disk_usage(Path.home()).free / 1e9
        if free_gb < 10:
            self.finished_err.emit(
                f"Not enough disk space ({free_gb:.1f} GB free). "
                f"Omni needs about 10 GB. Free up some space and try again."
            )
            return
        try:
            urllib.request.urlopen("https://github.com", timeout=5)
        except Exception:
            self.finished_err.emit(
                "No internet connection. Omni needs to download "
                "dependencies during installation. Check your connection and try again."
            )
            return

        self.progress.emit(5, "Checking Homebrew…")
        _brew_found = (
            shutil.which("brew", path=env.get("PATH"))
            or os.path.exists("/opt/homebrew/bin/brew")
            or os.path.exists("/usr/local/bin/brew")
        )
        if not _brew_found:
            self.progress.emit(5, "Installing Homebrew…")

            import getpass as _getpass, shlex as _shlex

            username = _getpass.getuser()
            home     = os.path.expanduser("~")

            # Homebrew install script (runs as the current user, not root)
            brew_script = (
                "#!/bin/bash\n"
                "export NONINTERACTIVE=1\n"
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"\n'
            )
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as tf:
                tf.write(brew_script)
                brew_path = tf.name
            os.chmod(brew_path, 0o755)

            # User command: sets env, then runs brew
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as uf:
                uf.write(
                    f"#!/bin/bash\n"
                    f"export NONINTERACTIVE=1\n"
                    f"export HOME={_shlex.quote(home)}\n"
                    f"export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n"
                    f"exec /bin/bash {_shlex.quote(brew_path)}\n"
                )
                user_cmd_path = uf.name
            os.chmod(user_cmd_path, 0o755)

            # Wrapper: runs as root via osascript.
            #   1. Writes a temporary sudoers entry granting the user
            #      passwordless sudo (Homebrew calls /usr/bin/sudo directly).
            #   2. su's to the current user so Homebrew doesn't see root.
            #   3. Always removes the sudoers entry on exit.
            sudoers_file = "/etc/sudoers.d/omni_brew"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as wf:
                wf.write(
                    f"#!/bin/bash\n"
                    f"printf '%s ALL=(ALL) NOPASSWD: ALL\\n' {_shlex.quote(username)}"
                    f" > {sudoers_file}\n"
                    f"chmod 440 {sudoers_file}\n"
                    f"su {_shlex.quote(username)} -c"
                    f" {_shlex.quote('/bin/bash ' + user_cmd_path)}\n"
                    f"exit_code=$?\n"
                    f"rm -f {sudoers_file}\n"
                    f"exit $exit_code\n"
                )
                wrapper_path = wf.name
            os.chmod(wrapper_path, 0o755)

            # AppleScript runs wrapper as root — shows the standard macOS
            # auth dialog once; no separate password collection needed.
            def _ase(s):
                return s.replace("\\", "\\\\").replace('"', '\\"')

            with tempfile.NamedTemporaryFile(mode="w", suffix=".applescript", delete=False) as af:
                af.write(
                    f'do shell script "/bin/bash {_ase(wrapper_path)}" '
                    f'with administrator privileges'
                )
                as_path = af.name

            rc, out, err = self._run_cmd(["osascript", as_path])

            for p in (brew_path, user_cmd_path, wrapper_path, as_path):
                try: os.unlink(p)
                except OSError: pass

            if rc != 0:
                self.finished_err.emit(f"Homebrew install failed:\n{err}")
                return

        self.progress.emit(15, "Checking Python…")
        import re as _re

        def _find_python(env_):
            search_path = env_.get("PATH", os.environ.get("PATH", ""))

            # Prefer the interpreter running the installer itself —
            # but NOT when frozen: sys.executable is the installer bundle,
            # not a Python interpreter; launching it would open a second window.
            if not getattr(sys, "frozen", False):
                cur = sys.version_info
                if cur >= (3, 10):
                    return sys.executable

            for ver in ("3.12", "3.13", "3.11", "3.10"):
                p = shutil.which(f"python{ver}", path=search_path)
                if p: return p

            brew_cmd = shutil.which("brew", path=search_path)
            if brew_cmd:
                prefix = subprocess.run([brew_cmd, "--prefix"], capture_output=True, text=True, env=env_).stdout.strip()
                for ver in ("3.12", "3.13", "3.11", "3.10"):
                    cand = Path(prefix) / "bin" / f"python{ver}"
                    if cand.exists(): return str(cand)

            p3 = shutil.which("python3", path=search_path)
            if p3:
                res = subprocess.run([p3, "--version"], capture_output=True, text=True)
                m = _re.search(r"Python (\d+)\.(\d+)", res.stdout + res.stderr)
                if m and (int(m.group(1)), int(m.group(2))) >= (3, 10): return p3
            return None

        py_cmd = _find_python(env)
        if not py_cmd:
            self.progress.emit(15, "Installing Python 3.12…")
            rc, out, err = self._run_cmd(["brew", "install", "python@3.12"], env=env)
            if rc != 0:
                self.finished_err.emit(f"Python install failed:\n{err}")
                return
            py_cmd = _find_python(env)

        if not py_cmd:
            self.finished_err.emit("Could not find Python 3.10+ after install.")
            return

        self.progress.emit(25, "Installing system dependencies…")
        self._run_cmd(["brew", "install", "ffmpeg", "portaudio"], env=env)

        self.progress.emit(35, "Copying Omni…")
        src_root = omni_src()
        if INSTALL_DIR.exists():
            for item in INSTALL_DIR.iterdir():
                if item.name == "venv": continue
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        for item in src_root.iterdir():
            dest = INSTALL_DIR / item.name
            if item.name == "venv": continue
            if item.is_dir():
                if dest.exists(): shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        run_sh = INSTALL_DIR / "run.sh"
        if run_sh.exists():
            # Ensure run.sh starts with a cd to its own directory.
            # When launched via open -a Omni.app the CWD is / (root), which
            # breaks every relative path (./venv/bin/python3, etc.).
            _cd_line = 'cd "$(dirname "$0")" || exit 1\n'
            _content = run_sh.read_text()
            if _cd_line not in _content:
                # Insert after the shebang line
                _lines = _content.splitlines(keepends=True)
                _insert_at = 1 if _lines and _lines[0].startswith("#!") else 0
                _lines.insert(_insert_at, "\n" + _cd_line)
                run_sh.write_text("".join(_lines))
            run_sh.chmod(run_sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        venv_dir = INSTALL_DIR / "venv"
        self.progress.emit(45, "Setting up Python environment…")
        if not venv_dir.exists():
            rc, out, err = self._run_cmd([py_cmd, "-m", "venv", str(venv_dir)], env=env)
            if rc != 0:
                self.finished_err.emit(f"Virtual env failed:\n{err}")
                return
        pip_cmd = str(venv_dir / "bin" / "pip")
        python_venv = str(venv_dir / "bin" / "python3")
        self._run_cmd([pip_cmd, "install", "--upgrade", "pip", "--quiet", "--no-cache-dir"], env=env)

        self.progress.emit(55, "Installing requirements…")
        req_file = INSTALL_DIR / "requirements.txt"
        self._run_cmd([pip_cmd, "install", "-r", str(req_file), "--quiet", "--no-cache-dir"], env=env)

        self.progress.emit(85, "Installing voice engine…")
        self._run_cmd([pip_cmd, "install", "git+https://github.com/QwenLM/Qwen3-ASR.git", "--quiet", "--no-cache-dir"], env=env)

        self.progress.emit(90, "Downloading voice activation models…")
        self._run_cmd([python_venv, "-c", "from openwakeword.utils import download_models; download_models()"], env=env)

        self.progress.emit(96, "Finalising…")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        settings_file = CONFIG_DIR / "settings.json"
        if settings_file.exists():
            # Reset onboarding flag so the onboarding wizard shows on first launch
            try:
                existing = json.loads(settings_file.read_text())
                existing.pop("onboarding_shown", None)
                settings_file.write_text(json.dumps(existing, indent=2))
            except Exception:
                pass
        else:
            settings_file.write_text(json.dumps({"theme": "dark", "hotkey": "cmd+option", "autostart": False, "voice_enabled": True}, indent=2))

        env_file = CONFIG_DIR / ".env"
        _BUNDLED_ENV = "OMNI_GITHUB_TOKEN=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\nOMNI_SECRET=928623c24271f389cb638ce1853f0386b3728c0c8d50c3ea08166186580b3c2f\n"
        if not env_file.exists():
            env_file.write_text(_BUNDLED_ENV)
        else:
            existing = env_file.read_text()
            additions = []
            if "OMNI_GITHUB_TOKEN" not in existing:
                additions.append("OMNI_GITHUB_TOKEN=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")
            if "OMNI_SECRET" not in existing:
                additions.append("OMNI_SECRET=928623c24271f389cb638ce1853f0386b3728c0c8d50c3ea08166186580b3c2f")
            if additions:
                env_file.write_text(existing.rstrip() + "\n" + "\n".join(additions) + "\n")

        INSTALL_MARKER.touch()
        self.progress.emit(100, "Done.")
        self.finished_ok.emit()


class IndexWorker(QThread):
    progress     = pyqtSignal(int, str)
    log_line     = pyqtSignal(str)
    finished_ok  = pyqtSignal()
    finished_err = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._brain_proc = None
        self._index_proc = None
        self._cancelled  = False

    def cancel(self):
        self._cancelled = True
        self._cleanup()

    def _cleanup(self):
        for proc in (self._index_proc, self._brain_proc):
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass

    def run(self):
        import re, time, urllib.request
        venv_py = str(INSTALL_DIR / "venv" / "bin" / "python")
        cwd     = str(INSTALL_DIR)
        env     = {**os.environ, "PYTHONUNBUFFERED": "1"}

        self.progress.emit(1, "Starting brain service…")
        try:
            self._brain_proc = subprocess.Popen(
                [venv_py, "-m", "src.app.brain"], cwd=cwd, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            if not self._cancelled: self.finished_err.emit(f"Could not start brain: {e}")
            return

        self.progress.emit(2, "Warming up…")
        health = "http://127.0.0.1:5555/health"
        deadline = time.time() + 120
        ready = False
        while time.time() < deadline:
            if self._cancelled:
                self._cleanup(); return
            try:
                with urllib.request.urlopen(health, timeout=2) as r:
                    if r.status == 200: ready = True; break
            except Exception: pass
            time.sleep(2)

        if not ready:
            self._cleanup()
            if not self._cancelled: self.finished_err.emit("Brain service did not start in time.")
            return

        self.progress.emit(5, "Phase 1 of 3 — Indexing file names…")
        try:
            self._index_proc = subprocess.Popen(
                [venv_py, "-m", "src.services.search.indexer"], cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except Exception as e:
            self._cleanup()
            if not self._cancelled: self.finished_err.emit(f"Could not start indexer: {e}")
            return

        _log_re = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ - \w+ - ')
        _fn_re  = re.compile(r'\[filenames\].*?\((\d+\.?\d*)%\)')
        _ct_re  = re.compile(r'\[content\].*?\((\d+\.?\d*)%\)')
        _im_re  = re.compile(r'\[images\].*?\((\d+\.?\d*)%\)')

        for raw in self._index_proc.stdout:
            if self._cancelled: break
            line = raw.strip()
            if not line: continue
            msg = _log_re.sub('', line)
            self.log_line.emit(msg)
            if "Phase 1/3" in msg: self.progress.emit(5, "Phase 1 of 3 — Indexing file names…")
            elif "Phase 2/3" in msg: self.progress.emit(40, "Phase 2 of 3 — Reading file content…")
            elif "Phase 3/3" in msg: self.progress.emit(85, "Phase 3 of 3 — Indexing images…")
            elif "All done!" in msg: self.progress.emit(100, "Index complete.")
            elif m := _fn_re.search(msg): self.progress.emit(min(int(float(m.group(1)) * 0.35 + 5), 39), "Phase 1 of 3 — Indexing file names…")
            elif m := _ct_re.search(msg): self.progress.emit(min(int(float(m.group(1)) * 0.44 + 40), 84), "Phase 2 of 3 — Reading file content…")
            elif m := _im_re.search(msg): self.progress.emit(min(int(float(m.group(1)) * 0.14 + 85), 99), "Phase 3 of 3 — Indexing images…")

        self._index_proc.wait()
        self._cleanup()
        if not self._cancelled: self.finished_ok.emit()


# ── Install page ───────────────────────────────────────────────────────────────

class InstallPage(QWidget):
    install_done = pyqtSignal()

    def __init__(self, on_next, on_back):
        super().__init__()
        self.on_next = on_next
        self.worker  = None

        v = QVBoxLayout(self)
        v.setContentsMargins(52, 44, 52, 0)
        v.setSpacing(0)

        h, sub = page_header("Installing Omni", "Setting up your environment. This usually takes 3–5 minutes.")
        v.addWidget(h)
        v.addSpacing(4)
        v.addWidget(sub)
        v.addSpacing(28)

        # Progress section
        self.prog_bar = SlimBar()
        v.addWidget(self.prog_bar)
        v.addSpacing(10)

        pct_row = QHBoxLayout()
        self.step_lbl = make_label("Starting…", size=13, color=TEXT_SEC)
        self.pct_lbl  = make_label("0%", size=12, color=TEXT_HINT)
        pct_row.addWidget(self.step_lbl, 1)
        pct_row.addWidget(self.pct_lbl)
        v.addLayout(pct_row)

        v.addSpacing(12)
        self.err_lbl = make_label("", size=12, color=ERROR)
        self.err_lbl.setWordWrap(True)
        v.addWidget(self.err_lbl)

        v.addStretch(1)
        v.addWidget(Divider())

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 16, 0, 20)

        self.back_btn = QPushButton("← Back")
        self.back_btn.setObjectName("secondary")
        self.back_btn.setFixedSize(96, 38)
        self.back_btn.clicked.connect(on_back)

        self.next_btn = QPushButton("Continue →")
        self.next_btn.setObjectName("primary")
        self.next_btn.setFixedSize(140, 38)
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(on_next)

        footer.addWidget(self.back_btn)
        footer.addStretch()
        footer.addWidget(self.next_btn)
        v.addLayout(footer)

    def start_install(self):
        self.back_btn.setEnabled(False)
        self.worker = InstallWorker()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_ok)
        self.worker.finished_err.connect(self._on_err)
        self.worker.start()

    def _on_progress(self, pct, msg):
        self.prog_bar.set_value(pct)
        self.pct_lbl.setText(f"{pct}%")
        self.step_lbl.setText(msg)

    def _on_ok(self):
        self.step_lbl.setText("Installation complete")
        self.step_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 13px;")
        self.prog_bar.set_value(100)
        self.pct_lbl.setText("100%")
        self.next_btn.setEnabled(True)
        self.install_done.emit()

    def _on_err(self, msg):
        self.err_lbl.setText(f"Error: {msg}")
        self.step_lbl.setText("Installation failed")
        self.step_lbl.setStyleSheet(f"color: {ERROR}; font-size: 13px;")
        self.back_btn.setEnabled(True)


# ── Indexing page ──────────────────────────────────────────────────────────────

class IndexingPage(QWidget):
    indexing_done = pyqtSignal()

    def __init__(self, on_next, on_skip=None):
        super().__init__()
        self.on_next = on_next
        self.on_skip_cb = on_skip or on_next
        self.worker  = None

        v = QVBoxLayout(self)
        v.setContentsMargins(52, 44, 52, 0)
        v.setSpacing(0)

        h, sub = page_header(
            "Building your index",
            "Omni reads your files once so it can find anything in milliseconds.\n"
            "This runs in the background and only happens once."
        )
        v.addWidget(h)
        v.addSpacing(4)
        v.addWidget(sub)
        v.addSpacing(28)

        self.prog_bar = SlimBar()
        v.addWidget(self.prog_bar)
        v.addSpacing(10)

        pct_row = QHBoxLayout()
        self.step_lbl  = make_label("Starting…", size=13, color=TEXT_SEC)
        self.phase_lbl = make_label("", size=12, color=TEXT_HINT)
        pct_row.addWidget(self.step_lbl, 1)
        pct_row.addWidget(self.phase_lbl)
        v.addLayout(pct_row)

        v.addSpacing(20)

        self.err_lbl = make_label("", size=12, color=ERROR)
        self.err_lbl.setWordWrap(True)
        v.addWidget(self.err_lbl)

        v.addStretch(1)
        v.addWidget(Divider())

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 16, 0, 20)

        self.skip_btn = QPushButton("Skip for now")
        self.skip_btn.setObjectName("secondary")
        self.skip_btn.setFixedSize(140, 38)
        self.skip_btn.clicked.connect(self._on_skip)

        self.next_btn = QPushButton("Continue →")
        self.next_btn.setObjectName("primary")
        self.next_btn.setFixedSize(140, 38)
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(on_next)

        footer.addWidget(self.skip_btn)
        footer.addStretch()
        footer.addWidget(self.next_btn)
        v.addLayout(footer)

    def start_indexing(self):
        self.worker = IndexWorker()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_ok)
        self.worker.finished_err.connect(self._on_err)
        self.worker.start()

    def _on_progress(self, pct, msg):
        self.prog_bar.set_value(pct)
        self.step_lbl.setText(msg)
        if "Phase 1" in msg:   self.phase_lbl.setText("1 / 3")
        elif "Phase 2" in msg: self.phase_lbl.setText("2 / 3")
        elif "Phase 3" in msg: self.phase_lbl.setText("3 / 3")
        elif pct == 100:       self.phase_lbl.setText("Done")

    def _on_ok(self):
        self.step_lbl.setText("Index complete — Omni knows your computer.")
        self.step_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 13px;")
        self.prog_bar.set_value(100)
        self.phase_lbl.setText("Done")
        self.skip_btn.setEnabled(False)
        self.next_btn.setEnabled(True)
        self.indexing_done.emit()

    def _on_err(self, msg):
        self.err_lbl.setText(f"Indexing failed: {msg}")
        self.step_lbl.setText("You can rebuild the index later from Omni settings.")
        self.next_btn.setEnabled(True)
        self.indexing_done.emit()

    def _on_skip(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            # Don't block the UI thread — worker will stop on its own
        self.on_skip_cb()


# ── Permission status badge ─────────────────────────────────────────────────────

class _PermBadge(QWidget):
    """Animated circle that turns green with a checkmark when permission granted."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._granted = False
        self._anim_t  = 0.0          # 0 → 1 fill animation
        self._timer   = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(28, 28)

    def set_granted(self, granted: bool):
        if granted == self._granted:
            return
        self._granted = granted
        if granted:
            self._anim_t = 0.0
            self._timer.start()
        else:
            self._timer.stop()
            self._anim_t = 0.0
            self.update()

    def _tick(self):
        self._anim_t = min(1.0, self._anim_t + 0.08)
        self.update()
        if self._anim_t >= 1.0:
            self._timer.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r = 14, 14, 11

        if self._granted:
            t = self._anim_t
            # Animated green fill
            green = QColor("#22C55E")
            if t < 1.0:
                idle = QColor(BORDER)
                red   = int(idle.red()   + (green.red()   - idle.red())   * t)
                gval  = int(idle.green() + (green.green() - idle.green()) * t)
                blue  = int(idle.blue()  + (green.blue()  - idle.blue())  * t)
                fill = QColor(red, gval, blue)
            else:
                fill = green

            p.setBrush(QBrush(fill))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

            # Checkmark (fades in after halfway)
            if t > 0.5:
                alpha = int(min(1.0, (t - 0.5) * 2) * 255)
                pen = QPen(QColor(255, 255, 255, alpha))
                pen.setWidth(2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                path = QPainterPath()
                path.moveTo(cx - 4.5, cy)
                path.lineTo(cx - 1,   cy + 3.5)
                path.lineTo(cx + 5,   cy - 4)
                p.drawPath(path)
        else:
            # Idle: subtle ring
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(BORDER), 2))
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)


# ── Permissions page ───────────────────────────────────────────────────────────

class PermissionsPage(QWidget):
    _file_access_done = pyqtSignal(bool)

    def __init__(self, on_next, on_back):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(52, 36, 52, 0)
        outer.setSpacing(0)

        h, sub = page_header(
            "Grant permissions",
            "A few quick permissions — Omni needs them to work properly."
        )
        outer.addWidget(h)
        outer.addSpacing(4)
        outer.addWidget(sub)
        outer.addSpacing(14)

        # Scrollable area for permission cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }"
                             "QScrollBar:vertical { width: 4px; background: transparent; }"
                             "QScrollBar::handle:vertical { background: rgba(255,255,255,0.15); border-radius: 2px; }"
                             "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        v = QVBoxLayout(scroll_content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        # ── Accessibility card ──────────────────────────────────────────────────
        ax_card  = Card()
        ax_v     = QVBoxLayout(ax_card)
        ax_v.setContentsMargins(20, 14, 20, 14)
        ax_v.setSpacing(8)

        ax_top = QHBoxLayout()
        ax_top.setSpacing(0)
        ax_txt = QVBoxLayout()
        ax_txt.setSpacing(2)
        ax_txt.addWidget(make_label("Keyboard Control", size=13, weight=600))
        ax_desc = make_label(
            "Lets Omni detect your shortcut from any app.",
            size=12, color=TEXT_SEC)
        ax_txt.addWidget(ax_desc)
        self._ax_badge = _PermBadge()
        ax_top.addLayout(ax_txt, 1)
        ax_top.addWidget(self._ax_badge, 0,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        ax_v.addLayout(ax_top)

        ax_btn = QPushButton("Open Accessibility Settings →")
        ax_btn.setObjectName("secondary")
        ax_btn.setFixedHeight(34)
        ax_btn.clicked.connect(self._open_accessibility)
        ax_v.addWidget(ax_btn)
        v.addWidget(ax_card)

        v.addSpacing(8)

        # ── Full Disk Access card ───────────────────────────────────────────────
        fda_card = Card()
        fda_v    = QVBoxLayout(fda_card)
        fda_v.setContentsMargins(20, 14, 20, 14)
        fda_v.setSpacing(8)

        fda_top = QHBoxLayout()
        fda_top.setSpacing(0)
        fda_txt = QVBoxLayout()
        fda_txt.setSpacing(2)
        fda_txt.addWidget(make_label("Full Disk Access", size=13, weight=600))
        fda_desc = make_label(
            "Lets Omni index and search files across your entire Mac.",
            size=12, color=TEXT_SEC)
        fda_txt.addWidget(fda_desc)
        self._fda_badge = _PermBadge()
        fda_top.addLayout(fda_txt, 1)
        fda_top.addWidget(self._fda_badge, 0,
                          Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        fda_v.addLayout(fda_top)

        fda_btn = QPushButton("Open Full Disk Access →")
        fda_btn.setObjectName("secondary")
        fda_btn.setFixedHeight(34)
        fda_btn.clicked.connect(self._open_fda)
        fda_v.addWidget(fda_btn)
        v.addWidget(fda_card)

        v.addSpacing(8)

        # ── File Indexing card ──────────────────────────────────────────────────
        files_card = Card()
        files_v    = QVBoxLayout(files_card)
        files_v.setContentsMargins(20, 14, 20, 14)
        files_v.setSpacing(8)

        files_top = QHBoxLayout()
        files_top.setSpacing(0)
        files_txt = QVBoxLayout()
        files_txt.setSpacing(2)
        files_txt.addWidget(make_label("Folder Indexing", size=13, weight=600))
        files_desc = make_label(
            "Grants access to Desktop, Documents and Downloads for search.",
            size=12, color=TEXT_SEC)
        files_txt.addWidget(files_desc)
        self._files_badge = _PermBadge()
        files_top.addLayout(files_txt, 1)
        files_top.addWidget(self._files_badge, 0,
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        files_v.addLayout(files_top)

        self._files_btn = QPushButton("Grant Folder Access →")
        self._files_btn.setObjectName("secondary")
        self._files_btn.setFixedHeight(34)
        self._files_btn.clicked.connect(self._open_file_access)
        files_v.addWidget(self._files_btn)
        v.addWidget(files_card)

        v.addSpacing(8)

        # ── Microphone card ───────────────────────────────────────────────────
        mic_card = Card()
        mic_v    = QVBoxLayout(mic_card)
        mic_v.setContentsMargins(20, 14, 20, 14)
        mic_v.setSpacing(8)

        mic_top = QHBoxLayout()
        mic_top.setSpacing(0)
        mic_txt = QVBoxLayout()
        mic_txt.setSpacing(2)
        mic_txt.addWidget(make_label("Microphone", size=13, weight=600))
        mic_desc = make_label(
            "Required for voice commands and \"Hey Omni\" activation.",
            size=12, color=TEXT_SEC)
        mic_txt.addWidget(mic_desc)
        self._mic_badge = _PermBadge()
        mic_top.addLayout(mic_txt, 1)
        mic_top.addWidget(self._mic_badge, 0,
                          Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        mic_v.addLayout(mic_top)

        mic_btn = QPushButton("Grant Microphone Access →")
        mic_btn.setObjectName("secondary")
        mic_btn.setFixedHeight(34)
        mic_btn.clicked.connect(self._request_microphone)
        mic_v.addWidget(mic_btn)
        v.addWidget(mic_card)

        v.addSpacing(8)

        # ── Speech Recognition card ───────────────────────────────────────────
        sr_card = Card()
        sr_v    = QVBoxLayout(sr_card)
        sr_v.setContentsMargins(20, 14, 20, 14)
        sr_v.setSpacing(8)

        sr_top = QHBoxLayout()
        sr_top.setSpacing(0)
        sr_txt = QVBoxLayout()
        sr_txt.setSpacing(2)
        sr_txt.addWidget(make_label("Speech Recognition", size=13, weight=600))
        sr_desc = make_label(
            "Lets Omni transcribe your voice into text on-device.",
            size=12, color=TEXT_SEC)
        sr_txt.addWidget(sr_desc)
        self._sr_badge = _PermBadge()
        sr_top.addLayout(sr_txt, 1)
        sr_top.addWidget(self._sr_badge, 0,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        sr_v.addLayout(sr_top)

        sr_btn = QPushButton("Grant Speech Recognition →")
        sr_btn.setObjectName("secondary")
        sr_btn.setFixedHeight(34)
        sr_btn.clicked.connect(self._request_speech_recognition)
        sr_v.addWidget(sr_btn)
        v.addWidget(sr_card)

        v.addSpacing(8)

        outer.addWidget(Divider())

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 14, 0, 18)

        back_btn = QPushButton("← Back")
        back_btn.setObjectName("secondary")
        back_btn.setFixedSize(96, 38)
        back_btn.clicked.connect(on_back)

        next_btn = QPushButton("Continue →")
        next_btn.setObjectName("primary")
        next_btn.setFixedSize(140, 38)
        next_btn.clicked.connect(on_next)

        footer.addWidget(back_btn)
        footer.addStretch()
        footer.addWidget(next_btn)
        outer.addLayout(footer)

        self._file_access_done.connect(self._on_file_access_done)
        self._file_access_triggered = False

        # Timer is created but NOT started here — start_polling() is called
        # by OmniInstallerWindow only when this page becomes visible (step 3).
        # This prevents the Omni launcher binary from being invoked (and any
        # TCC prompt from appearing) before the user reaches the permissions step.
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._check_permissions)

    def start_polling(self):
        """Called by the main window when this page becomes the active step."""
        if not self._poll.isActive():
            self._poll.start()
        self._check_permissions()

    # ── Open Accessibility ───────────────────────────────────────────────────────

    def _open_accessibility(self):
        # Trigger AXIsProcessTrustedWithOptions(prompt=YES) so that macOS
        # registers this process (com.omni.app) in the Accessibility list.
        # Without this call the app never appears in System Settings and the
        # user cannot grant the permission, so _check_ax() would always return
        # False even after the user opens the pane.
        try:
            import ctypes.util
            libobjc = ctypes.CDLL(ctypes.util.find_library("objc"))
            libobjc.objc_getClass.restype   = ctypes.c_void_p
            libobjc.objc_getClass.argtypes  = [ctypes.c_char_p]
            libobjc.sel_registerName.restype  = ctypes.c_void_p
            libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
            msg = libobjc.objc_msgSend

            msg.restype  = ctypes.c_void_p
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            yes = msg(libobjc.objc_getClass(b"NSNumber"),
                      libobjc.sel_registerName(b"numberWithBool:"), True)

            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            key = msg(libobjc.objc_getClass(b"NSString"),
                      libobjc.sel_registerName(b"stringWithUTF8String:"),
                      b"AXTrustedCheckOptionPrompt")

            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p]
            opts = msg(libobjc.objc_getClass(b"NSDictionary"),
                       libobjc.sel_registerName(b"dictionaryWithObject:forKey:"),
                       yes, key)

            _appserv = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            _appserv.AXIsProcessTrustedWithOptions.restype  = ctypes.c_bool
            _appserv.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
            _appserv.AXIsProcessTrustedWithOptions(opts)
        except Exception:
            pass
        subprocess.Popen([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        ])

    def _open_fda(self):
        # Probe ~/Library/Mail from our own process — we are com.omni.app, so
        # this registers Omni in the Full Disk Access list in System Settings.
        threading.Thread(target=self._probe_fda, daemon=True).start()
        subprocess.Popen([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
        ])

    def _probe_fda(self):
        try:
            os.listdir(os.path.expanduser("~/Library/Mail"))
        except Exception:
            pass

    def _open_file_access(self):
        """Trigger macOS folder-access TCC dialogs for Desktop/Documents/Downloads.

        Each listdir() call on a protected folder will prompt the user once.
        Running in a background thread so the UI stays responsive while
        macOS shows the sequential permission dialogs.
        """
        self._files_btn.setEnabled(False)
        self._files_btn.setText("Waiting for permission…")
        self._file_access_triggered = True
        threading.Thread(target=self._probe_file_access, daemon=True).start()

    def _probe_file_access(self):
        """Background thread: probe each protected folder to trigger TCC dialogs."""
        granted = True
        for folder in ("Desktop", "Documents", "Downloads"):
            try:
                os.listdir(str(Path.home() / folder))
            except (PermissionError, OSError):
                granted = False
        self._file_access_done.emit(granted)

    def _on_file_access_done(self, granted: bool):
        self._files_badge.set_granted(granted)
        self._files_btn.setEnabled(True)
        self._files_btn.setText("Grant Folder Access →")

    # ── Microphone ──────────────────────────────────────────────────────────────

    def _request_microphone(self):
        """Trigger macOS TCC microphone prompt and open settings."""
        threading.Thread(target=self._probe_microphone, daemon=True).start()
        subprocess.Popen([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        ])

    def _probe_microphone(self):
        """Open a brief audio stream to trigger the TCC microphone prompt."""
        try:
            import sounddevice as sd
            with sd.InputStream(samplerate=16000, blocksize=1280, channels=1):
                import time
                time.sleep(0.2)
        except Exception:
            pass

    def _check_microphone(self) -> bool:
        """Return True if microphone permission is granted."""
        try:
            from AVFoundation import AVCaptureDevice
            # authorizationStatusForMediaType: 0=notDetermined, 1=restricted, 2=denied, 3=authorized
            status = AVCaptureDevice.authorizationStatusForMediaType_("soun")
            return status == 3
        except Exception:
            pass
        # Fallback: try via ctypes
        try:
            libobjc = ctypes.CDLL(ctypes.util.find_library("objc"))
            libobjc.objc_getClass.restype  = ctypes.c_void_p
            libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
            libobjc.sel_registerName.restype  = ctypes.c_void_p
            libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
            msg = libobjc.objc_msgSend

            # NSString *mediaType = @"soun" (AVMediaTypeAudio internal value)
            msg.restype  = ctypes.c_void_p
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            media_type = msg(
                libobjc.objc_getClass(b"NSString"),
                libobjc.sel_registerName(b"stringWithUTF8String:"),
                b"soun")

            # [AVCaptureDevice authorizationStatusForMediaType:]
            msg.restype  = ctypes.c_long
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            status = msg(
                libobjc.objc_getClass(b"AVCaptureDevice"),
                libobjc.sel_registerName(b"authorizationStatusForMediaType:"),
                media_type)
            return status == 3
        except Exception:
            return False

    # ── Speech Recognition ──────────────────────────────────────────────────────

    def _request_speech_recognition(self):
        """Trigger macOS TCC speech recognition prompt and open settings."""
        threading.Thread(target=self._probe_speech_recognition, daemon=True).start()
        subprocess.Popen([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition"
        ])

    def _probe_speech_recognition(self):
        """Run the streaming_asr binary briefly to trigger TCC speech prompt."""
        try:
            # Locate the streaming_asr binary relative to the installed source
            for base in (
                Path(__file__).resolve().parent,                          # installer dir
                Path.home() / ".config" / "omni" / "src" / "services" / "voice",  # installed
            ):
                asr = base / "src" / "services" / "voice" / "streaming_asr"
                if not asr.is_file():
                    asr = base / "streaming_asr"
                if asr.is_file() and os.access(str(asr), os.X_OK):
                    proc = subprocess.Popen(
                        [str(asr), "--lang", "en-US"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    proc.stdin.close()
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return
        except Exception:
            pass

    def _check_speech_recognition(self) -> bool:
        """Return True if speech recognition permission is granted."""
        try:
            from Speech import SFSpeechRecognizer
            status = SFSpeechRecognizer.authorizationStatus()
            return status == 3  # .authorized
        except Exception:
            pass
        # Fallback: try via ctypes
        try:
            libobjc = ctypes.CDLL(ctypes.util.find_library("objc"))
            libobjc.objc_getClass.restype  = ctypes.c_void_p
            libobjc.objc_getClass.argtypes = [ctypes.c_char_p]
            libobjc.sel_registerName.restype  = ctypes.c_void_p
            libobjc.sel_registerName.argtypes = [ctypes.c_char_p]
            msg = libobjc.objc_msgSend

            # [SFSpeechRecognizer authorizationStatus]
            speech_fw = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/Speech.framework/Speech"
            )
            msg.restype  = ctypes.c_long
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            status = msg(
                libobjc.objc_getClass(b"SFSpeechRecognizer"),
                libobjc.sel_registerName(b"authorizationStatus"))
            return status == 3
        except Exception:
            return False

    # ── Permission checks ───────────────────────────────────────────────────────
    # AX and FDA are checked via ctypes — we ARE com.omni.app (the PyInstaller
    # bundle carries bundle ID com.omni.app), so TCC attributes these calls to
    # Omni directly, exactly like Raycast or any other native app.
    # File access is probed with listdir() as before.

    def _check_ax(self) -> bool:
        """Return True if Omni (com.omni.app) has Accessibility permission."""
        try:
            _appserv = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            _appserv.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
            _appserv.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
            return bool(_appserv.AXIsProcessTrustedWithOptions(None))
        except Exception:
            return False

    def _check_fda_native(self) -> bool:
        """Return True if Omni (com.omni.app) has Full Disk Access."""
        try:
            os.listdir(os.path.expanduser("~/Library/Mail"))
            return True
        except PermissionError:
            return False
        except OSError:
            # ENOENT or similar — TCC allowed the call, folder just doesn't exist
            return True

    def _check_file_access(self) -> bool:
        """Return True if Desktop, Documents and Downloads are all readable.

        Safe to call after _file_access_triggered is set — by then TCC has
        already recorded the user's decision and listdir() returns instantly.
        """
        for folder in ("Desktop", "Documents", "Downloads"):
            try:
                os.listdir(str(Path.home() / folder))
            except (PermissionError, OSError):
                return False
        return True

    def _check_permissions(self):
        self._ax_badge.set_granted(self._check_ax())
        self._fda_badge.set_granted(self._check_fda_native())
        # Always check folder access — show granted state even if user never clicked
        self._files_badge.set_granted(self._check_file_access())
        self._mic_badge.set_granted(self._check_microphone())
        self._sr_badge.set_granted(self._check_speech_recognition())



# ── Done page ──────────────────────────────────────────────────────────────────

class DonePage(QWidget):
    launch_signal = pyqtSignal()

    def __init__(self, on_launch):
        super().__init__()
        self._on_launch = on_launch
        self.launch_signal.connect(self._launch)

        v = QVBoxLayout(self)
        v.setContentsMargins(52, 52, 52, 0)
        v.setSpacing(0)

        # Done mark
        done_row = QHBoxLayout()
        done_mark = QLabel("✓")
        done_mark.setFixedSize(36, 36)
        done_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        done_mark.setStyleSheet(f"""
            background: rgba(74, 222, 128, 0.12);
            color: {SUCCESS};
            border: 1px solid rgba(74, 222, 128, 0.25);
            border-radius: 18px;
            font-size: 16px;
            font-weight: 700;
        """)
        done_row.addWidget(done_mark)
        done_row.addStretch()
        v.addLayout(done_row)
        v.addSpacing(20)

        title = make_label("You're all set.", size=28, weight=700)
        v.addWidget(title)
        v.addSpacing(6)

        sub = make_label(
            "Omni is ready. Press Cmd+Option from any app to open it.",
            size=13, color=TEXT_SEC
        )
        sub.setWordWrap(True)
        v.addWidget(sub)

        v.addStretch(1)
        v.addWidget(Divider())

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 16, 0, 20)

        launch_btn = QPushButton("Launch Omni →")
        launch_btn.setObjectName("primary")
        launch_btn.setFixedSize(148, 38)
        launch_btn.clicked.connect(self._launch)

        footer.addStretch()
        footer.addWidget(launch_btn)
        v.addLayout(footer)

    def auto_launch(self):
        """Called when this page becomes visible — install login item and launch."""
        self._install_launch_agent()
        # Small delay so the page renders before launching
        QTimer.singleShot(300, self.launch_signal.emit)

    def _install_launch_agent(self):
        run_sh = str(INSTALL_DIR / "run.sh")
        try:
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            # Unload any existing agent first (ignore errors if not loaded)
            subprocess.run(
                ["launchctl", "unload", str(LAUNCH_AGENT)],
                capture_output=True,
            )
            LAUNCH_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.omni.app</string>
<key>ProgramArguments</key><array>
<string>/bin/bash</string>
<string>{run_sh}</string>
</array>
<key>WorkingDirectory</key><string>{str(INSTALL_DIR)}</string>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><false/>
</dict></plist>""")
            subprocess.run(
                ["launchctl", "load", str(LAUNCH_AGENT)],
                capture_output=True,
            )
        except Exception as e:
            print(f"LaunchAgent error: {e}")

    def _launch(self):
        self._on_launch()


# ── Installer frame — owns the background fill and the animated aurora border ──
# The border must be painted by the central widget itself so it appears on top
# of its own surface.  Painting on QMainWindow.paintEvent puts it *behind* child
# widgets, which then cover it with their solid backgrounds.

class _InstallerFrame(QWidget):
    """Central widget that draws the dark background + animated aurora border."""

    _AURORA = [
        QColor("#2E5CB8"),  # deep blue
        QColor("#6A0DAD"),  # dark violet
        QColor("#D92E87"),  # magenta/pink
        QColor("#FF8533"),  # warm orange
        QColor("#66B2FF"),  # light cyan
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue_shift     = 0.0
        self._base_speed    = 0.006   # ~one full rotation every ~5 s at 30 ms tick
        self._cur_speed     = self._base_speed
        self._border_prog   = 0.0

        # Always-running tick: only repaints when border is visible so CPU cost is
        # negligible when idle, but starting the timer once avoids the race where
        # _tick() sees _border_prog == 0 and immediately kills the timer before
        # the QPropertyAnimation has had a chance to push the value above 0.01.
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)  # resist App Nap throttling
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._anim = QPropertyAnimation(self, b"border_prog")
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # ── Animatable property ───────────────────────────────────────────────────

    @pyqtProperty(float)
    def border_prog(self):
        return self._border_prog

    @border_prog.setter
    def border_prog(self, value):
        self._border_prog = value
        self.update()

    # ── Public API called by OmniInstallerWindow ──────────────────────────────

    def start_border_animation(self):
        self._cur_speed = 0.012  # burst speed on entry, decays to _base_speed
        self._anim.stop()
        self._anim.setStartValue(self._border_prog)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(600)
        self._anim.start()

    def stop_border_animation(self):
        self._anim.stop()
        self._anim.setStartValue(self._border_prog)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setDuration(900)
        self._anim.start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _tick(self):
        if self._border_prog > 0.01:
            self._hue_shift = (self._hue_shift + self._cur_speed) % 1.0
            if self._cur_speed > self._base_speed:
                self._cur_speed = max(self._base_speed, self._cur_speed * 0.92)
            self.update()

    def _aurora_at(self, t: float) -> QColor:
        n = len(self._AURORA)
        pos = t * n
        i1 = int(pos) % n
        i2 = (i1 + 1) % n
        f = pos - int(pos)
        c1, c2 = self._AURORA[i1], self._AURORA[i2]
        return QColor(
            int(c1.red()   * (1 - f) + c2.red()   * f),
            int(c1.green() * (1 - f) + c2.green() * f),
            int(c1.blue()  * (1 - f) + c2.blue()  * f),
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, RADIUS, RADIUS)

        # Dark tint over the glass blur — enough to keep text readable
        p.fillPath(path, QBrush(QColor(13, 13, 16, 80)))

        if self._border_prog > 0.01:
            # Conical gradient centered on window — rotates as _hue_shift advances,
            # so colors visually sweep around the border perimeter each tick.
            cx, cy = w / 2.0, h / 2.0
            angle = self._hue_shift * 360.0
            grad = QConicalGradient(cx, cy, angle)
            n = len(self._AURORA)
            for i in range(n + 1):
                grad.setColorAt(i / n, self._AURORA[i % n])

            # Animated border stroke
            bp = QPainterPath()
            bp.addRoundedRect(1.5, 1.5, w - 3, h - 3, RADIUS - 1, RADIUS - 1)
            p.setOpacity(self._border_prog)
            p.strokePath(bp, QPen(QBrush(grad), 2.5))
            p.setOpacity(1.0)
        else:
            # Plain 1 px border on idle pages — no gradient
            bp = QPainterPath()
            bp.addRoundedRect(0.5, 0.5, w - 1, h - 1, RADIUS, RADIUS)
            p.setPen(QPen(QColor(BORDER), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(bp)

        p.end()


# ── Main window ────────────────────────────────────────────────────────────────

class OmniInstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Omni")
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None

        # ── Central frame — paints its own background + aurora border ─────────
        self.central = _InstallerFrame(self)
        self.central.setObjectName("central")
        self.central.setGeometry(0, 0, WINDOW_W, WINDOW_H)

        main_layout = QVBoxLayout(self.central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Step indicator at top
        self.dots = StepDots(5, self.central)
        self.dots.setContentsMargins(0, 10, 0, 0)
        main_layout.addWidget(self.dots)

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        self.page_welcome  = WelcomePage(on_next=lambda: self.go_to(1))
        self.page_install  = InstallPage(on_next=lambda: self.go_to(2), on_back=lambda: self.go_to(0))
        self.page_perms    = PermissionsPage(on_next=lambda: self.go_to(3), on_back=lambda: self.go_to(1))
        self.page_index    = IndexingPage(on_next=lambda: self.go_to(4),
                                                on_skip=lambda: self.go_to(4, auto_launch=False))
        self.page_done     = DonePage(on_launch=self._launch_omni)

        for page in (
            self.page_welcome, self.page_install,
            self.page_perms, self.page_index, self.page_done,
        ):
            self.stack.addWidget(page)

        self.page_install.install_done.connect(self.stop_border_animation)
        self.page_index.indexing_done.connect(self.stop_border_animation)

        self.stack.setCurrentIndex(0)
        self.setCentralWidget(self.central)
        self.center_on_screen()

    # ── Liquid-glass background (macOS only) ──────────────────────────────────

    def apply_blur(self):
        if sys.platform != "darwin":
            return
        try:
            import objc
            from AppKit import (
                NSVisualEffectView, NSVisualEffectBlendingModeBehindWindow,
                NSViewWidthSizable, NSViewHeightSizable, NSColor,
            )

            view_ptr = int(self.winId())
            ns_view = objc.objc_object(c_void_p=ctypes.c_void_p(view_ptr))

            # Get the NSWindow and make it transparent at the native level
            ns_window = ns_view.window()
            if ns_window:
                ns_window.setOpaque_(False)
                ns_window.setBackgroundColor_(NSColor.clearColor())

            # Sync frame if blur view already exists
            if hasattr(self, "_mac_blur_view"):
                self._mac_blur_view.setFrame_(ns_view.frame())
                return

            # Sibling strategy: insert blur view BEHIND Qt's NSView in the shared
            # parent (NSThemeFrame / window frame view).  Subviews always composite
            # ON TOP of the parent's drawRect content, so we must be a sibling.
            superview = ns_view.superview()
            if not superview:
                return

            # Try Liquid Glass (macOS 26+) first
            GlassEffectView = None
            try:
                GlassEffectView = objc.lookUpClass("NSGlassEffectView")
            except Exception:
                pass

            if GlassEffectView:
                self._mac_blur_view = GlassEffectView.alloc().initWithFrame_(ns_view.frame())
                self._mac_blur_view.setTintColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.40)
                )
                self._mac_blur_view.setCornerRadius_(float(RADIUS))
                self._mac_blur_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                self._mac_blur_view.setWantsLayer_(True)
                self._mac_blur_view.layer().setCornerRadius_(float(RADIUS))
                self._mac_blur_view.layer().setMasksToBounds_(True)
            else:
                # Fallback: NSVisualEffectView dark HUD material
                self._mac_blur_view = NSVisualEffectView.alloc().initWithFrame_(ns_view.frame())
                self._mac_blur_view.setMaterial_(13)  # NSVisualEffectMaterialHUDWindow
                self._mac_blur_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
                self._mac_blur_view.setState_(1)
                self._mac_blur_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                self._mac_blur_view.setWantsLayer_(True)
                self._mac_blur_view.layer().setCornerRadius_(float(RADIUS))
                self._mac_blur_view.layer().setMasksToBounds_(True)

            # Insert as sibling BEHIND Qt's NSView (-1 = NSWindowBelow)
            superview.addSubview_positioned_relativeTo_(self._mac_blur_view, -1, ns_view)

            # Re-enable native shadow (removed QGraphicsDropShadowEffect above)
            if ns_window:
                ns_window.setHasShadow_(True)
                ns_window.invalidateShadow()

            # Prevent macOS App Nap from throttling the animation timer when
            # a system dialog (TCC permission popup) briefly occludes the window
            try:
                from Foundation import NSProcessInfo
                self._ns_activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                    0x00FFFFFF,  # NSActivityUserInitiated — prevents all throttling
                    "Omni installer animation"
                )
            except Exception:
                pass

        except Exception:
            pass  # PyObjC not available — degrade gracefully

    def closeEvent(self, event):
        if hasattr(self, "_ns_activity") and self._ns_activity:
            try:
                from Foundation import NSProcessInfo
                NSProcessInfo.processInfo().endActivity_(self._ns_activity)
            except Exception:
                pass
        super().closeEvent(event)

    # ── Border animation — delegated to _InstallerFrame ──────────────────────

    def start_border_animation(self):
        self.central.start_border_animation()

    def stop_border_animation(self):
        self.central.stop_border_animation()

    # ── Navigation ───────────────────────────────────────────────────────────

    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - WINDOW_W) // 2, (screen.height() - WINDOW_H) // 2)

    def showEvent(self, event):
        super().showEvent(event)
        existing = [
            w for w in QApplication.topLevelWidgets()
            if isinstance(w, OmniInstallerWindow) and w is not self and w.isVisible()
        ]
        if existing:
            self.close()
            existing[0].raise_()
            existing[0].activateWindow()
            return
        QTimer.singleShot(100, self.apply_blur)

    def go_to(self, idx, auto_launch=True):
        if idx == 1 and self.stack.currentIndex() == 0:
            self.page_install.start_install()
            self.start_border_animation()
        elif idx == 2:
            self.page_perms.start_polling()
            self.stop_border_animation()
        elif idx == 3 and self.stack.currentIndex() == 2:
            self.page_index.start_indexing()
            self.start_border_animation()
        elif idx == 4:
            self.stop_border_animation()
            self.stack.setCurrentIndex(idx)
            self.dots.set_step(idx)
            if auto_launch:
                self.page_done.auto_launch()
            return
        else:
            self.stop_border_animation()
        self.stack.setCurrentIndex(idx)
        self.dots.set_step(idx)

    def _launch_omni(self):
        SETUP_MARKER.touch()
        # Hide the wizard, then keep com.omni.app alive as the parent process
        # while the Omni app runs.  macOS TCC traces the "responsible app" by
        # walking up the parent chain — python3 must have com.omni.app as an
        # ancestor, or global hotkeys (Accessibility permission) won't work.
        # subprocess.Popen uses posix_spawn (Cocoa-safe, unlike os.fork after
        # Cocoa init) and still gives us a direct parent-child relationship.
        self.hide()
        # Remove the installer from the Dock while it stays alive as a parent
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
            except Exception:
                pass
        run_sh = INSTALL_DIR / "run.sh"
        try:
            # Strip PyInstaller env vars so the venv Python isn't confused by
            # frozen PYTHONHOME/PYTHONPATH pointing at the bundle's stdlib.
            child_env = {
                k: v for k, v in os.environ.items()
                if not k.startswith(("DYLD_", "QT_", "_MEIPASS", "PYTHON"))
            }
            proc = subprocess.Popen(
                ["/bin/bash", str(run_sh)],
                cwd=str(INSTALL_DIR),
                env=child_env,
            )
            # Background thread blocks until Omni quits, then exits the wizard.
            def _wait():
                try:
                    proc.wait()
                except Exception:
                    pass
                QApplication.quit()
            threading.Thread(target=_wait, daemon=True).start()
        except Exception:
            QApplication.quit()

    # ── Drag to move ─────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


# ── Entry point ────────────────────────────────────────────────────────────────

def _setup_complete() -> bool:
    """Return True only if the full onboarding wizard has finished."""
    return SETUP_MARKER.exists()


def _install_done() -> bool:
    """Return True if the install step finished (but wizard may be incomplete)."""
    return INSTALL_MARKER.exists()


def main():
    # Subclass QApplication to suppress macOS "reopen" events.
    # When pip/venv spawns Python subprocesses, macOS sends
    # applicationShouldHandleReopen to the running Qt app, which
    # Qt handles by creating a new top-level window.  We swallow it.
    class _App(QApplication):
        def event(self, e):
            if e.type() in (
                QEvent.Type.ApplicationActivate,
                QEvent.Type.ApplicationActivated,
            ):
                return True  # suppress — window already visible
            return super().event(e)

    # ── Routing: if already installed, supervise the main app and exit when done ─
    # subprocess.Popen (posix_spawn internally on macOS) keeps com.omni.app
    # alive as the direct parent process while python3 runs.  macOS TCC traces
    # the "responsible app" up the parent chain — without a live com.omni.app
    # ancestor, global hotkeys (Accessibility permission) break at runtime.
    if _setup_complete():
        # Sync source files from the bundle so the installed version
        # always matches what was shipped, without touching the venv.
        try:
            src_root = omni_src()
            for item in src_root.iterdir():
                if item.name == "venv":
                    continue
                dest = INSTALL_DIR / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        except Exception:
            pass

        run_sh = INSTALL_DIR / "run.sh"
        try:
            # Strip PyInstaller's DYLD_*/QT_*/PYTHON* env vars so the child
            # process uses only the venv's Qt/Python, avoiding conflicts.
            child_env = {
                k: v for k, v in os.environ.items()
                if not k.startswith(("DYLD_", "QT_", "_MEIPASS", "PYTHON"))
            }
            proc = subprocess.Popen(
                ["/bin/bash", str(run_sh)],
                cwd=str(INSTALL_DIR),
                env=child_env,
            )
            proc.wait()   # block; exits when user quits Omni
        except Exception:
            pass
        sys.exit(0)

    app = _App(sys.argv)
    app.setApplicationName("Omni")
    app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,        QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText,    QColor(TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Base,          QColor(BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(20, 20, 24))
    palette.setColor(QPalette.ColorRole.Text,          QColor(TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Button,        QColor(30, 30, 36))
    palette.setColor(QPalette.ColorRole.ButtonText,    QColor(TEXT_PRI))
    palette.setColor(QPalette.ColorRole.Highlight,     QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    app.setPalette(palette)

    load_fonts()
    app.setStyleSheet(qss_base())

    win = OmniInstallerWindow()
    # If the install step already finished (e.g. app was restarted for
    # permissions), resume at the permissions page instead of starting over.
    if _install_done():
        win.go_to(2)  # permissions page
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()