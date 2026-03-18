"""Detect the active app context to enable pre-fetching of relevant data."""

import logging
import platform
import subprocess


def get_active_app_context() -> list[str]:
    """Return hints about what data to pre-fetch based on the active app.

    Returns a list of prefetch hints like ["calendar_events"], ["unread_emails"], etc.
    """
    if platform.system() != "Darwin":
        return []

    try:
        app_name = _get_frontmost_app_macos()
        if not app_name:
            return []

        app_lower = app_name.lower()

        # Calendar-related apps
        if any(kw in app_lower for kw in ["calendar", "fantastical", "busycal"]):
            return ["calendar_events"]

        # Email-related apps
        if any(kw in app_lower for kw in ["mail", "outlook", "spark", "airmail", "thunderbird"]):
            return ["unread_emails"]

        # File management
        if any(kw in app_lower for kw in ["finder", "path finder", "forklift"]):
            return ["directory_listing"]

        # Browser — user might search for something
        if any(kw in app_lower for kw in ["safari", "chrome", "firefox", "arc", "brave", "edge"]):
            return []  # No specific prefetch for browsers

    except Exception as e:
        logging.warning(f"Context detection failed: {e}")

    return []


def _get_frontmost_app_macos() -> str:
    """Get the name of the frontmost application on macOS using NSWorkspace."""
    try:
        # Try PyObjC first (faster, no subprocess)
        from AppKit import NSWorkspace
        active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if active_app:
            return active_app.localizedName() or ""
    except ImportError:
        pass

    # Fallback: osascript
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return ""
