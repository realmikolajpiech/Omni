"""
macOS (and cross-platform where possible) system settings controller.
Handles brightness, volume, dark mode, DND, Night Shift, Wi-Fi, Bluetooth,
and Omni-own settings.
"""
import re
import sys
import logging
import subprocess
from typing import Any, Optional

# ── Omni settings ────────────────────────────────────────────────────────────
from src.core import settings_store

# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns for detecting settings commands (PL + EN)
# Each tuple: (compiled_pattern, setting_key, value_group_index_or_None)
# ─────────────────────────────────────────────────────────────────────────────

_BRIGHTNESS_PATTERNS = [
    re.compile(r'(?:ustaw|zmień|zwiększ|zmniejsz|set|change|increase|decrease|reduce|make|put|turn)\s+'
               r'(?:jasność|brightness)(?:\s+(?:ekranu|wyświetlacza|screen))?(?:\s+(?:do|na|to|at|=))?\s*(\d+)\s*%?', re.I),
    re.compile(r'(?:jasność|brightness)\s+(?:do|na|to|at|=)\s*(\d+)\s*%?', re.I),
    re.compile(r'(?:jasność|brightness)\s*[=:]\s*(\d+)\s*%?', re.I),
    re.compile(r'(\d+)\s*%?\s+(?:jasność|brightness)', re.I),
    re.compile(r'^(?:jasność|brightness)\s+(\d+)\s*%?$', re.I),
    re.compile(r'^(?:jasność|brightness)\s*(\d+)\s*%?$', re.I),
    re.compile(r'(?:max|maksymalna|maximum|full|pełna)\s+(?:jasność|brightness)', re.I),
    re.compile(r'(?:min|minimalna|minimum|zero)\s+(?:jasność|brightness)', re.I),
]

_VOLUME_PATTERNS = [
    re.compile(r'(?:ustaw|zmień|zwiększ|zmniejsz|set|change|increase|decrease|reduce|turn)\s+'
               r'(?:głośność|volume|sound|audio)(?:\s+(?:do|na|to|at|=))?\s*(\d+)\s*%?', re.I),
    re.compile(r'(?:głośność|volume)\s+(?:do|na|to|at|=)\s*(\d+)\s*%?', re.I),
    re.compile(r'(?:głośność|volume)\s*[=:]\s*(\d+)\s*%?', re.I),
    re.compile(r'(\d+)\s*%?\s+(?:głośność|volume)', re.I),
    re.compile(r'^(?:głośność|volume)\s+(\d+)\s*%?$', re.I),
    re.compile(r'^(?:głośność|volume)\s*(\d+)\s*%?$', re.I),
]

_MUTE_PATTERNS = [
    re.compile(r'(?:wycisz|mute|silence|quiet)\s*(?:dźwięk|sound|audio|volume)?', re.I),
    re.compile(r'(?:ustaw|set)\s+(?:głośność|volume)\s+(?:na|to)\s+0\s*%?', re.I),
]

_UNMUTE_PATTERNS = [
    re.compile(r'(?:odcisz|unmute|włącz dźwięk|turn on sound|restore sound)', re.I),
]

_DARK_MODE_OFF_PATTERNS = [
    re.compile(r'(?:wyłącz|turn\s*off|disable|deaktywuj)\s+'
               r'(?:tryb\s*ciemny|dark\s*mode|dark\s*theme|ciemny\s*motyw|dark)', re.I),
    re.compile(r'(?:włącz|turn\s*on|enable|switch\s*to|use)\s+'
               r'(?:tryb\s*jasny|light\s*mode|light\s*theme|jasny\s*motyw|light)', re.I),
    re.compile(r'^(?:tryb\s*jasny|light\s*mode)\s*$', re.I),
]

_DARK_MODE_ON_PATTERNS = [
    re.compile(r'(?:włącz|turn\s*on|enable|switch\s*to|use|aktywuj)\s+'
               r'(?:tryb\s*ciemny|dark\s*mode|dark\s*theme|ciemny\s*motyw|dark)', re.I),
    re.compile(r'^(?:tryb\s*ciemny|dark\s*mode)\s*$', re.I),
]

_DND_ON_PATTERNS = [
    re.compile(r'(?:włącz|turn\s*on|enable|aktywuj|start)\s+'
               r'(?:nie\s*przeszkadzać|do\s*not\s*disturb|dnd|focus|skupienie)', re.I),
]

_DND_OFF_PATTERNS = [
    re.compile(r'(?:wyłącz|turn\s*off|disable|deaktywuj|stop)\s+'
               r'(?:nie\s*przeszkadzać|do\s*not\s*disturb|dnd|focus|skupienie)', re.I),
]

_NIGHT_SHIFT_ON_PATTERNS = [
    re.compile(r'(?:włącz|turn\s*on|enable)\s+(?:night\s*shift|nocna\s*zmiana|redukcja\s*niebieskiego)', re.I),
]

_NIGHT_SHIFT_OFF_PATTERNS = [
    re.compile(r'(?:wyłącz|turn\s*off|disable)\s+(?:night\s*shift|nocna\s*zmiana|redukcja\s*niebieskiego)', re.I),
]

_WIFI_ON_PATTERNS  = [re.compile(r'(?:włącz|turn\s*on|enable)\s+(?:wifi|wi-fi|sieć|internet)', re.I)]
_WIFI_OFF_PATTERNS = [re.compile(r'(?:wyłącz|turn\s*off|disable)\s+(?:wifi|wi-fi|sieć)', re.I)]

_BT_ON_PATTERNS  = [re.compile(r'(?:włącz|turn\s*on|enable)\s+(?:bluetooth|bt)', re.I)]
_BT_OFF_PATTERNS = [re.compile(r'(?:wyłącz|turn\s*off|disable)\s+(?:bluetooth|bt)', re.I)]

# Omni-own settings
_OMNI_LANG_PATTERNS = [
    re.compile(r'(?:zmień|set|change)\s+(?:język|language)\s+(?:omni\s+)?(?:na|to)\s+(\w+)', re.I),
    re.compile(r'(?:omni\s+)?(?:język|language)\s*[=:]\s*(\w+)', re.I),
]

def draw_brightness(p, cx, cy, sz, color):
    from PyQt6.QtGui import QPen, QBrush
    from PyQt6.QtCore import Qt, QPointF
    import math
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawEllipse(QPointF(cx, cy), sz * 0.45, sz * 0.45)
    p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for i in range(8):
        angle = math.radians(i * 45)
        x1 = cx + math.cos(angle) * (sz * 0.6)
        y1 = cy + math.sin(angle) * (sz * 0.6)
        x2 = cx + math.cos(angle) * (sz * 0.9)
        y2 = cy + math.sin(angle) * (sz * 0.9)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

def draw_volume(p, cx, cy, sz, color):
    from PyQt6.QtGui import QPainterPath, QPen, QBrush
    from PyQt6.QtCore import Qt, QPointF
    import math
    path = QPainterPath()
    path.moveTo(cx - sz*0.5, cy - sz*0.2)
    path.lineTo(cx - sz*0.1, cy - sz*0.2)
    path.lineTo(cx + sz*0.3, cy - sz*0.5)
    path.lineTo(cx + sz*0.3, cy + sz*0.5)
    path.lineTo(cx - sz*0.1, cy + sz*0.2)
    path.lineTo(cx - sz*0.5, cy + sz*0.2)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPath(path)
    p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(int(cx - sz*0.2), int(cy - sz*0.4), int(sz*0.8), int(sz*0.8), -30*16, 60*16)
    p.drawArc(int(cx - sz*0.4), int(cy - sz*0.6), int(sz*1.2), int(sz*1.2), -35*16, 70*16)

def draw_moon(p, cx, cy, sz, color):
    from PyQt6.QtGui import QPainterPath, QBrush
    from PyQt6.QtCore import Qt
    path = QPainterPath()
    path.moveTo(cx, cy - sz*0.8)
    path.arcTo(cx - sz*0.8, cy - sz*0.8, sz*1.6, sz*1.6, 90, 180)
    path.arcTo(cx - sz*0.3, cy - sz*0.8, sz*1.4, sz*1.4, 270, -180)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPath(path)

def draw_wifi(p, cx, cy, sz, color):
    from PyQt6.QtGui import QPen, QBrush
    from PyQt6.QtCore import Qt, QPointF
    p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(int(cx - sz), int(cy - sz), int(sz*2), int(sz*2), 45*16, 90*16)
    p.drawArc(int(cx - sz*0.6), int(cy - sz*0.6), int(sz*1.2), int(sz*1.2), 45*16, 90*16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawEllipse(QPointF(cx, cy + sz*0.5), sz*0.2, sz*0.2)

def draw_dnd(p, cx, cy, sz, color):
    from PyQt6.QtGui import QPainterPath, QBrush
    from PyQt6.QtCore import Qt
    path = QPainterPath()
    path.moveTo(cx, cy - sz*0.7)
    path.arcTo(cx - sz*0.8, cy - sz*0.7, sz*1.6, sz*1.6, 90, 180)
    path.arcTo(cx - sz*0.4, cy - sz*0.7, sz*1.4, sz*1.4, 270, -180)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPath(path)

_ICON_DRAW_FNS = {
    "brightness": draw_brightness,
    "volume": draw_volume,
    "moon": draw_moon,
    "wifi": draw_wifi,
    "dnd": draw_dnd
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd, timeout=5) :
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}"
    except Exception as e:
        return False, str(e)

# ─────────────────────────────────────────────────────────────────────────────
# Actual macOS setting functions
# ─────────────────────────────────────────────────────────────────────────────

import threading
import time

_current_brightness = None
_brightness_thread = None

def set_brightness(value: int):
    """Set screen brightness 0–100 smoothly."""
    global _current_brightness, _brightness_thread
    
    if _current_brightness is None:
        _current_brightness = get_brightness()

    target_val = max(0, min(100, value))
    target_float = target_val / 100.0

    if sys.platform != "darwin":
        logging.warning("[settings] set_brightness: not on macOS")
        return False, "Not on macOS"

    # Smooth animation logic
    def animate_brightness(start: float, end: float):
        global _current_brightness
        steps = 25
        delay = 0.015  # Total ~0.4s
        diff = end - start
        
        try:
            import ctypes
            
            # Use CoreGraphics to get the main display ID
            cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            display_id = cg.CGMainDisplayID()
            
            # CoreDisplay is the modern private framework for brightness (M1/M2/M3)
            cd = ctypes.CDLL("/System/Library/Frameworks/CoreDisplay.framework/CoreDisplay")
            cd.CoreDisplay_Display_SetUserBrightness.restype = None
            cd.CoreDisplay_Display_SetUserBrightness.argtypes = [ctypes.c_uint32, ctypes.c_double]
            
            for i in range(1, steps + 1):
                current = start + (diff * (i / steps))
                # Apply easing out
                progress = i / steps
                eased = start + diff * (1 - (1 - progress) * (1 - progress))
                
                # Apple Silicon handles this well
                cd.CoreDisplay_Display_SetUserBrightness(display_id, ctypes.c_double(eased))
                
                _current_brightness = eased
                time.sleep(delay)
                
            # Ensure final value is set exactly
            cd.CoreDisplay_Display_SetUserBrightness(display_id, ctypes.c_double(end))
            _current_brightness = end
            
            # CLI utility 'brightness' we can fall back to
            try:
                _run(['brightness', str(end)])
            except Exception:
                pass
            
            logging.info(f"[settings] Brightness animated to {target_val}% via CoreDisplay")
        except Exception as e:
            logging.warning(f"[settings] CoreDisplay brightness failed: {e}")
            # Fallback
            try:
                _run(['brightness', str(target_float)])
            except Exception:
                pass
            _current_brightness = target_float

    # Don't overlap threads
    t = threading.Thread(target=animate_brightness, args=(_current_brightness, target_float), daemon=True)
    _brightness_thread = t
    t.start()
    
    return True, f"Brightness setting to {target_val}%"


def get_brightness() -> float:
    """Return current brightness 0–1 (or 0.5 if unknown)."""
    if sys.platform == "darwin":
        # Let's try CoreGraphics or SkyLight first, otherwise fallback to `brightness`
        try:
            import ctypes
            cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            
            # Use CoreDisplay for getting brightness if available, or just fallback
        except Exception:
            pass
            
        ok, out = _run(['brightness', '-l'])
        if ok:
            import re
            m = re.search(r'brightness:\s*([\d.]+)', out)
            if m:
                return float(m.group(1))
    return 0.5


_current_volume = None
_volume_thread = None

def set_volume(value: int):
    """Set system output volume 0–100 smoothly."""
    global _current_volume, _volume_thread
    
    if _current_volume is None:
        _current_volume = get_volume()

    target_val = max(0, min(100, value))
    
    if sys.platform != "darwin":
        return False, "Not on macOS"

    def animate_volume(start: int, end: int):
        global _current_volume
        steps = 15
        delay = 0.02
        diff = end - start
        
        for i in range(1, steps + 1):
            progress = i / steps
            # linear interpolation for volume is fine
            current = int(start + diff * progress)
            _run(['osascript', '-e', f'set volume output volume {current}'])
            _current_volume = current
            import time
            time.sleep(delay)
            
        _run(['osascript', '-e', f'set volume output volume {end}'])
        _current_volume = end
        import logging
        logging.info(f"[settings] Volume animated to {end}%")

    import threading
    t = threading.Thread(target=animate_volume, args=(_current_volume, target_val), daemon=True)
    _volume_thread = t
    t.start()
    
    return True, f"Volume set to {target_val}%"


def get_volume() -> int:
    """Return current volume 0–100."""
    if sys.platform == "darwin":
        ok, out = _run(['osascript', '-e', 'output volume of (get volume settings)'])
        if ok:
            try:
                return int(out.strip())
            except ValueError:
                pass
    return 50


def set_mute(muted: bool) :
    """Mute or unmute system audio."""
    if sys.platform == "darwin":
        val = "true" if muted else "false"
        ok, msg = _run(['osascript', '-e', f'set volume output muted {val}'])
        if ok:
            return True, "Muted" if muted else "Unmuted"
        return False, msg
    return False, "Not on macOS"


def set_dark_mode(enabled: bool) :
    """Enable or disable dark mode."""
    if sys.platform == "darwin":
        val = "true" if enabled else "false"
        script = (
            f'tell app "System Events" to tell appearance preferences '
            f'to set dark mode to {val}'
        )
        ok, msg = _run(['osascript', '-e', script])
        if ok:
            return True, f"Dark mode {'enabled' if enabled else 'disabled'}"
        return False, msg
    return False, "Not on macOS"


def set_night_shift(enabled: bool) :
    """Enable or disable Night Shift."""
    if sys.platform == "darwin":
        # Requires macOS Shortcuts or CoreBrightness
        try:
            import objc  # type: ignore
            cb = objc.loadBundle(
                "CoreBrightness",
                bundle_path=(
                    "/System/Library/PrivateFrameworks/"
                    "CoreBrightness.framework"
                ),
                module_globals={}
            )
            client = cb.CBBlueLightClient.alloc().init()
            status = cb.CBBlueLightStatusData()
            client.getBlueLightStatus_(objc.byref(status))
            strength = 0.0 if not enabled else 0.5
            client.setStrength_commit_(strength, True)
            return True, f"Night Shift {'enabled' if enabled else 'disabled'}"
        except Exception as e:
            logging.warning(f"[settings] Night Shift CoreBrightness failed: {e}")

        # Fallback: toggle via Shortcuts app (macOS 12+)
        action = "Turn On Night Shift" if enabled else "Turn Off Night Shift"
        ok, msg = _run(['shortcuts', 'run', action], timeout=10)
        if ok:
            return True, f"Night Shift {'enabled' if enabled else 'disabled'}"
        return False, "Night Shift control requires CoreBrightness or Shortcuts"
    return False, "Not on macOS"


def set_dnd(enabled: bool) :
    """Enable or disable Do Not Disturb (Focus)."""
    if sys.platform == "darwin":
        # macOS 15 / Sequoia approach using defaults + notificationcenter restart
        try:
            if enabled:
                # Write DND active flag
                subprocess.run(['defaults', 'write',
                                'com.apple.notificationcenterui', 'doNotDisturb',
                                '-bool', 'YES'], capture_output=True)
                subprocess.run(['defaults', 'write',
                                'com.apple.notificationcenterui', 'doNotDisturbFrom',
                                '-date', '2000-01-01 00:00:00 +0000'], capture_output=True)
                subprocess.run(['defaults', 'write',
                                'com.apple.notificationcenterui', 'doNotDisturbTo',
                                '-date', '2099-01-01 00:00:00 +0000'], capture_output=True)
            else:
                subprocess.run(['defaults', 'delete',
                                'com.apple.notificationcenterui', 'doNotDisturb'],
                               capture_output=True)
            subprocess.run(['killall', 'NotificationCenter'], capture_output=True)
            return True, f"Do Not Disturb {'enabled' if enabled else 'disabled'}"
        except Exception as e:
            return False, str(e)
    return False, "Not on macOS"


def set_wifi(enabled: bool) :
    """Enable or disable Wi-Fi."""
    if sys.platform == "darwin":
        state = "on" if enabled else "off"
        # Find the Wi-Fi interface
        ok, iface = _run(['networksetup', '-listallhardwareports'])
        wifi_dev = "en0"  # sane default
        if ok:
            lines = iface.splitlines()
            for i, line in enumerate(lines):
                if "Wi-Fi" in line or "AirPort" in line:
                    for j in range(i, min(i + 3, len(lines))):
                        m = re.search(r'Device:\s*(\w+)', lines[j])
                        if m:
                            wifi_dev = m.group(1)
                            break
                    break
        ok2, msg2 = _run(['networksetup', '-setairportpower', wifi_dev, state])
        if ok2:
            return True, f"Wi-Fi {'enabled' if enabled else 'disabled'}"
        return False, msg2
    return False, "Not on macOS"


def set_bluetooth(enabled: bool) :
    """Enable or disable Bluetooth."""
    if sys.platform == "darwin":
        state = "on" if enabled else "off"
        # blueutil (brew install blueutil) is the easiest approach
        ok, msg = _run(['blueutil', f'--{state}'])
        if ok:
            return True, f"Bluetooth {'enabled' if enabled else 'disabled'}"
        # Fallback: AppleScript via System Preferences
        action = "turn Bluetooth on" if enabled else "turn Bluetooth off"
        script = (
            f'tell application "System Preferences"\n'
            f'  reveal pane "com.apple.preferences.Bluetooth"\n'
            f'  activate\n'
            f'end tell\n'
            f'delay 1\n'
            f'tell application "System Events"\n'
            f'  tell process "System Preferences"\n'
            f'    click button "{action}" of window 1\n'
            f'  end tell\n'
            f'end tell'
        )
        ok2, msg2 = _run(['osascript', '-e', script], timeout=10)
        if ok2:
            return True, f"Bluetooth {'enabled' if enabled else 'disabled'}"
        return False, "Install 'blueutil' for Bluetooth control: brew install blueutil"
    return False, "Not on macOS"


# ─────────────────────────────────────────────────────────────────────────────
# Omni-own settings
# ─────────────────────────────────────────────────────────────────────────────

_LANG_MAP = {
    "polish": "pl", "polski": "pl", "pl": "pl",
    "english": "en", "angielski": "en", "en": "en",
    "auto": "auto", "automatyczny": "auto",
    "german": "de", "niemiecki": "de", "de": "de",
    "french": "fr", "francuski": "fr", "fr": "fr",
    "spanish": "es", "hiszpański": "es", "es": "es",
}


def set_omni_language(lang: str) :
    code = _LANG_MAP.get(lang.lower(), lang.lower())
    settings_store.set("transcription_language", code)
    return True, f"Omni language set to {code}"


def set_omni_model(model_url: str, api_key: str = "", model_name: str = "") :
    settings_store.set("custom_api_url", model_url)
    if api_key:
        settings_store.set("custom_api_key", api_key)
    if model_name:
        settings_store.set("custom_model", model_name)
    return True, f"Omni model updated"


# ─────────────────────────────────────────────────────────────────────────────
# Master detector: parse query → return action dict or None
# ─────────────────────────────────────────────────────────────────────────────

# Icons for the toast widget
SETTING_META = {
    "brightness":    {"icon": "brightness", "color": "#FF9F0A", "label": "Brightness",   "unit": "%"},
    "volume":        {"icon": "volume",      "color": "#30D158", "label": "Volume",       "unit": "%"},
    "mute":          {"icon": "mute",        "color": "#FF453A", "label": "Mute",         "unit": ""},
    "unmute":        {"icon": "volume",      "color": "#30D158", "label": "Unmuted",      "unit": ""},
    "dark_mode":     {"icon": "dark_mode",   "color": "#BF5AF2", "label": "Dark Mode",    "unit": ""},
    "light_mode":    {"icon": "light_mode",  "color": "#FFD60A", "label": "Light Mode",   "unit": ""},
    "night_shift":   {"icon": "night_shift", "color": "#FF9F0A", "label": "Night Shift",  "unit": ""},
    "dnd":           {"icon": "dnd",         "color": "#FF453A", "label": "Do Not Disturb","unit": ""},
    "wifi":          {"icon": "wifi",        "color": "#32ADE6", "label": "Wi-Fi",        "unit": ""},
    "bluetooth":     {"icon": "bluetooth",   "color": "#0A84FF", "label": "Bluetooth",    "unit": ""},
    "omni_language": {"icon": "language",    "color": "#64D2FF", "label": "Language",     "unit": ""},
}


def detect_settings_command(query: str) -> Optional[dict]:
    """
    Parse a natural-language query and return a system_settings action dict,
    or None if the query is not a settings command.

    Returned dict structure:
    {
        "type":        "system_settings",
        "setting":     str,        # e.g. "brightness", "volume", "dark_mode" ...
        "value":       int|bool,   # numeric 0-100 or True/False
        "description": str,
        "icon":        str,
        "color":       str,
        "label":       str,
        "unit":        str,
    }
    """
    q = query.strip()

    # ── Brightness ────────────────────────────────────────────────────────────
    for pat in _BRIGHTNESS_PATTERNS:
        m = pat.search(q)
        if m:
            groups = [g for g in m.groups() if g is not None]
            if "max" in q.lower() or "full" in q.lower() or "pełna" in q.lower():
                val = 100
            elif "min" in q.lower() or "zero" in q.lower():
                val = 0
            else:
                val = int(groups[0]) if groups else 80
            val = max(0, min(100, val))
            meta = SETTING_META["brightness"]
            return {
                "type": "system_settings", "setting": "brightness", "value": val,
                "description": f"Brightness set to {val}%",
                **meta,
            }

    # ── Volume ────────────────────────────────────────────────────────────────
    for pat in _VOLUME_PATTERNS:
        m = pat.search(q)
        if m:
            groups = [g for g in m.groups() if g is not None]
            val = int(groups[0]) if groups else 50
            val = max(0, min(100, val))
            meta = SETTING_META["volume"]
            return {
                "type": "system_settings", "setting": "volume", "value": val,
                "description": f"Volume set to {val}%",
                **meta,
            }

    for pat in _MUTE_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["mute"]
            return {
                "type": "system_settings", "setting": "mute", "value": True,
                "description": "Sound muted",
                **meta,
            }

    for pat in _UNMUTE_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["unmute"]
            return {
                "type": "system_settings", "setting": "unmute", "value": True,
                "description": "Sound unmuted",
                **meta,
            }

    # ── Dark / Light mode (check OFF first to avoid false positives) ─────────
    for pat in _DARK_MODE_OFF_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["light_mode"]
            return {
                "type": "system_settings", "setting": "dark_mode", "value": False,
                "description": "Light mode enabled",
                **meta,
            }

    for pat in _DARK_MODE_ON_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["dark_mode"]
            return {
                "type": "system_settings", "setting": "dark_mode", "value": True,
                "description": "Dark mode enabled",
                **meta,
            }

    # ── Night Shift ───────────────────────────────────────────────────────────
    for pat in _NIGHT_SHIFT_ON_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["night_shift"]
            return {
                "type": "system_settings", "setting": "night_shift", "value": True,
                "description": "Night Shift enabled",
                **meta,
            }

    for pat in _NIGHT_SHIFT_OFF_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["night_shift"]
            return {
                "type": "system_settings", "setting": "night_shift", "value": False,
                "description": "Night Shift disabled",
                **meta,
            }

    # ── DND ───────────────────────────────────────────────────────────────────
    for pat in _DND_ON_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["dnd"]
            return {
                "type": "system_settings", "setting": "dnd", "value": True,
                "description": "Do Not Disturb enabled",
                **meta,
            }

    for pat in _DND_OFF_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["dnd"]
            return {
                "type": "system_settings", "setting": "dnd", "value": False,
                "description": "Do Not Disturb disabled",
                **meta,
            }

    # ── Wi-Fi ─────────────────────────────────────────────────────────────────
    for pat in _WIFI_ON_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["wifi"]
            return {
                "type": "system_settings", "setting": "wifi", "value": True,
                "description": "Wi-Fi enabled",
                **meta,
            }

    for pat in _WIFI_OFF_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["wifi"]
            return {
                "type": "system_settings", "setting": "wifi", "value": False,
                "description": "Wi-Fi disabled",
                **meta,
            }

    # ── Bluetooth ─────────────────────────────────────────────────────────────
    for pat in _BT_ON_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["bluetooth"]
            return {
                "type": "system_settings", "setting": "bluetooth", "value": True,
                "description": "Bluetooth enabled",
                **meta,
            }

    for pat in _BT_OFF_PATTERNS:
        if pat.search(q):
            meta = SETTING_META["bluetooth"]
            return {
                "type": "system_settings", "setting": "bluetooth", "value": False,
                "description": "Bluetooth disabled",
                **meta,
            }

    # ── Omni language ─────────────────────────────────────────────────────────
    for pat in _OMNI_LANG_PATTERNS:
        m = pat.search(q)
        if m:
            lang = m.group(1)
            meta = SETTING_META["omni_language"]
            return {
                "type": "system_settings", "setting": "omni_language", "value": lang,
                "description": f"Omni language set to {lang}",
                **meta,
            }

    return None


def execute_setting(action: dict) :
    """
    Execute the actual system change described by a system_settings action dict.
    Returns (success, message).
    """
    setting = action.get("setting", "")
    value = action.get("value")

    dispatch: dict[str, Any] = {
        "brightness":    lambda: set_brightness(int(value)),
        "volume":        lambda: set_volume(int(value)),
        "mute":          lambda: set_mute(True),
        "unmute":        lambda: set_mute(False),
        "dark_mode":     lambda: set_dark_mode(bool(value)),
        "night_shift":   lambda: set_night_shift(bool(value)),
        "dnd":           lambda: set_dnd(bool(value)),
        "wifi":          lambda: set_wifi(bool(value)),
        "bluetooth":     lambda: set_bluetooth(bool(value)),
        "omni_language": lambda: set_omni_language(str(value)),
    }

    fn = dispatch.get(setting)
    if fn is None:
        return False, f"Unknown setting: {setting}"

    try:
        return fn()
    except Exception as e:
        logging.error(f"[settings] execute_setting({setting}) error: {e}")
        return False, str(e)
