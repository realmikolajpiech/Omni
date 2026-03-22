"""Activity Observer — background thread that polls the active window.

Tracks which app is in the foreground, reads the window title, extracts file
paths when possible, and feeds observations into the KnowledgeGraph.

Privacy: only metadata (app name, window title, file path).  No screenshots,
no keylogging, no file content capture.

Resource budget: <1% CPU idle.  Polls every 5 s, flushes to SQLite every 30 s.
Battery-aware: pauses when battery is below 20%.
"""

import logging
import os
import platform
import re
import threading
import time

from src.core.config import CONTEXT_POLL_INTERVAL, CONTEXT_FLUSH_INTERVAL, CONTEXT_PRUNE_DAYS
from src.services.context.knowledge_graph import KnowledgeGraph, get_knowledge_graph

_log = logging.getLogger(__name__)

# App-name patterns that hint at a file being open
# Maps app keyword → regex applied to window title to extract a file path or name
_FILE_EXTRACTORS: dict[str, re.Pattern] = {
    # VS Code / Cursor: "filename.py — ProjectFolder — Visual Studio Code"
    # Also matches just "ProjectFolder" (single segment = project name)
    "code": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    "cursor": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    # Xcode: "FileName.swift — ProjectName"
    "xcode": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    # Sublime Text: "filename.py • ~/path/to/dir"
    "sublime": re.compile(r"^(.+?)\s+[•·]\s+(.+)$"),
    # JetBrains IDEs: "filename.py – ProjectName"
    "jetbrains": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    "intellij": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    "pycharm": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    "webstorm": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    "clion": re.compile(r"^(.+?)\s+[—–-]\s+.+$"),
    # Terminal: "user@host: ~/path"
    "terminal": re.compile(r"^.+?:\s*(.+)$"),
    "iterm": re.compile(r"^.+?:\s*(.+)$"),
    "warp": re.compile(r"^.+?:\s*(.+)$"),
    # TextEdit / Preview / Pages / Numbers / Keynote
    "textedit": re.compile(r"^(.+?)(?:\s+[—–-]\s+.+)?$"),
    "preview": re.compile(r"^(.+?)(?:\s+[—–-]\s+.+)?$"),
    "pages": re.compile(r"^(.+?)(?:\s+[—–-]\s+.+)?$"),
    "numbers": re.compile(r"^(.+?)(?:\s+[—–-]\s+.+)?$"),
    "keynote": re.compile(r"^(.+?)(?:\s+[—–-]\s+.+)?$"),
    # Safari / Chrome / Firefox / Arc: window title = page title
    "safari": re.compile(r"^(.+)$"),
    "chrome": re.compile(r"^(.+)$"),
    "firefox": re.compile(r"^(.+)$"),
    "arc": re.compile(r"^(.+)$"),
    # Finder: window title is the folder name
    "finder": re.compile(r"^(.+)$"),
}

# Known IDE app names (lowercase fragments)
_IDE_APPS = {"code", "xcode", "sublime", "jetbrains", "intellij", "pycharm", "webstorm", "clion", "android studio", "cursor"}

# Idle detection — if the same (app, title) persists for this long, assume idle
_IDLE_THRESHOLD_S = 300  # 5 minutes of identical state


class ActivityObserver(threading.Thread):
    """Daemon thread that polls the active window and records transitions."""

    def __init__(self, kg: KnowledgeGraph | None = None):
        super().__init__(daemon=True, name="ActivityObserver")
        self._kg = kg or get_knowledge_graph()
        self._buffer: list[dict] = []
        self._paused = False
        self._stop_event = threading.Event()
        self._last_app = ""
        self._last_title = ""
        self._last_change_time = time.time()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pause(self) -> None:
        self._paused = True
        _log.info("[context] Observer paused")

    def resume(self) -> None:
        self._paused = False
        _log.info("[context] Observer resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        _log.info("[context] Activity Observer started (poll=%ds, flush=%ds)",
                  CONTEXT_POLL_INTERVAL, CONTEXT_FLUSH_INTERVAL)
        last_flush = time.time()
        last_prune = time.time()

        while not self._stop_event.is_set():
            try:
                if not self._paused and not self._is_low_battery():
                    self._poll_once()

                # Flush buffer to DB periodically
                now = time.time()
                if now - last_flush >= CONTEXT_FLUSH_INTERVAL and self._buffer:
                    self._flush()
                    last_flush = now

                # Prune old data once per hour
                if now - last_prune >= 3600:
                    self._kg.prune_activity_log(CONTEXT_PRUNE_DAYS)
                    last_prune = now

            except Exception as e:
                _log.warning("[context] Observer error: %s", e)

            self._stop_event.wait(CONTEXT_POLL_INTERVAL)

        # Final flush on shutdown
        if self._buffer:
            self._flush()
        _log.info("[context] Activity Observer stopped")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_once(self) -> None:
        app_name, window_title = self._get_active_window()
        if not app_name:
            return

        now = time.time()

        # Detect transition
        if app_name != self._last_app or window_title != self._last_title:
            # Record duration on the *previous* state
            duration = now - self._last_change_time
            if self._last_app and duration < _IDLE_THRESHOLD_S:
                file_path, _ = self._extract_file_and_project(self._last_app, self._last_title)
                entity_id = self._ensure_entities(self._last_app, self._last_title, file_path)
                self._buffer.append({
                    "timestamp": self._last_change_time,
                    "app_name": self._last_app,
                    "window_title": self._last_title,
                    "file_path": file_path,
                    "duration_s": round(duration, 1),
                    "entity_id": entity_id,
                })

            self._last_app = app_name
            self._last_title = window_title
            self._last_change_time = now

    def _get_active_window(self) -> tuple[str, str]:
        """Return (app_name, window_title).  macOS only for now.

        Uses a single AX API call chain so that the app name and window
        title always come from the same focused application — eliminates
        the TOCTOU race between NSWorkspace and AXFocusedApplication.
        """
        if platform.system() != "Darwin":
            return "", ""

        # Primary: AX API — single source of truth for both app + title
        try:
            app_name, window_title = self._get_active_window_ax()
            if app_name:
                return app_name, window_title
        except Exception:
            pass

        # Fallback: NSWorkspace (app name only, no window title)
        try:
            from AppKit import NSWorkspace
            active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if active_app:
                return active_app.localizedName() or "", ""
        except ImportError:
            pass

        return "", ""

    @staticmethod
    def _get_active_window_ax() -> tuple[str, str]:
        """Get both app name and window title from the AX API in one call.

        By reading app name from the *same* AXFocusedApplication element
        that provides the window title, we guarantee they refer to the
        same application at the same instant.
        """
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )

        system_wide = AXUIElementCreateSystemWide()

        # 1. Get the focused application element
        err, focused_app = AXUIElementCopyAttributeValue(
            system_wide, "AXFocusedApplication", None
        )
        if err or not focused_app:
            return "", ""

        # 2. Read the app name from that same element
        err, ax_title = AXUIElementCopyAttributeValue(focused_app, "AXTitle", None)
        app_name = str(ax_title) if not err and ax_title else ""

        # 3. Read the window title from the element's focused window
        window_title = ""
        err, focused_window = AXUIElementCopyAttributeValue(
            focused_app, "AXFocusedWindow", None
        )
        if not err and focused_window:
            err, title = AXUIElementCopyAttributeValue(
                focused_window, "AXTitle", None
            )
            if not err and title:
                window_title = str(title)

        return app_name, window_title

    # ------------------------------------------------------------------
    # Entity extraction
    # ------------------------------------------------------------------

    def _extract_file_and_project(self, app_name: str, window_title: str) -> tuple[str | None, str | None]:
        """Extract (file_path_or_name, project_name) from the window title.

        For IDEs like Cursor/VS Code with titles like "file.py — ProjectName",
        returns both parts.  For single-segment titles like "ProjectName",
        returns (None, project_name).
        """
        if not window_title:
            return None, None

        app_lower = app_name.lower()
        is_ide = any(ide in app_lower for ide in _IDE_APPS)

        # IDE separator pattern: "file — Project" or "file — Project — AppName"
        _SEP = re.compile(r'\s+[—–-]\s+')

        if is_ide:
            segments = _SEP.split(window_title.strip())
            # Filter out the app name itself from segments
            segments = [s.strip() for s in segments if s.strip().lower() not in (app_lower, app_name.lower())]

            if len(segments) >= 2:
                # "brain.log — OmniApp" → file="brain.log", project="OmniApp"
                file_name = segments[0]
                project_name = segments[1]
                # Validate file has an extension
                if "." in file_name and not file_name.startswith("http"):
                    return file_name, project_name
                else:
                    # First segment isn't a file, treat both as project context
                    return None, segments[0]
            elif len(segments) == 1:
                seg = segments[0]
                # Single segment with extension = file; without = NOT a project
                # (single-segment titles are often transient: Safari tabs, OS chrome)
                if "." in seg and not seg.startswith("http"):
                    return seg, None
                else:
                    return None, None
            return None, None

        # Non-IDE apps: try standard extractors for file path
        for key, pattern in _FILE_EXTRACTORS.items():
            if key in app_lower:
                m = pattern.match(window_title)
                if m:
                    candidate = m.group(1).strip()
                    if candidate.startswith("~") or candidate.startswith("/"):
                        expanded = os.path.expanduser(candidate)
                        if os.path.exists(expanded):
                            return expanded, None
                    if "." in candidate and not candidate.startswith("http"):
                        return candidate, None
                break

        return None, None

    def _ensure_entities(self, app_name: str, window_title: str, file_path: str | None) -> str | None:
        """Create or update entities for the observed app/file/project.
        Returns the primary entity ID."""

        app_id = self._kg.upsert_entity("app", app_name, uri=f"app:{app_name}")
        app_lower = app_name.lower()

        # Extract project name from the window title
        _, project_name = self._extract_file_and_project(app_name, window_title)

        # Track the project if we found one
        proj_id = None
        if project_name:
            proj_id = self._kg.upsert_entity(
                "file",
                project_name,
                uri=f"project:{app_name}:{project_name}",
                metadata={"app": app_name, "type": "project"},
            )
            self._kg.add_relationship(proj_id, app_id, "opened_in")

        # Track file if we extracted one
        if file_path:
            file_id = self._kg.upsert_entity(
                "file",
                os.path.basename(file_path),
                uri=file_path,
                metadata={"app": app_name, "project": project_name or ""},
            )
            self._kg.add_relationship(file_id, app_id, "opened_in")
            if proj_id:
                self._kg.add_relationship(file_id, proj_id, "part_of")
            return file_id

        if proj_id:
            return proj_id

        # For browsers, track the page title
        if window_title and any(b in app_lower for b in ("safari", "chrome", "firefox", "arc", "brave", "edge")):
            title = window_title.strip()
            if title and len(title) > 2:
                page_id = self._kg.upsert_entity(
                    "url", title,
                    uri=f"page:{app_name}:{title}",
                    metadata={"app": app_name},
                )
                self._kg.add_relationship(page_id, app_id, "opened_in")
                return page_id

        return app_id

    # ------------------------------------------------------------------
    # Flush & co-active relationships
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Write buffered activities to the knowledge graph and build
        co-active relationships between entities seen in the same 5-min window."""
        if not self._buffer:
            return

        entries = self._buffer[:]
        self._buffer.clear()

        count = self._kg.log_activity_batch(entries)

        # Build co-active relationships for entities in this batch
        entity_ids = [e["entity_id"] for e in entries if e.get("entity_id")]
        unique_ids = list(dict.fromkeys(entity_ids))  # preserve order, deduplicate
        for i, eid_a in enumerate(unique_ids):
            for eid_b in unique_ids[i + 1:]:
                self._kg.add_relationship(eid_a, eid_b, "co_active")

        _log.debug("[context] Flushed %d activity entries, %d entities", count, len(unique_ids))

    # ------------------------------------------------------------------
    # Battery awareness
    # ------------------------------------------------------------------

    @staticmethod
    def _is_low_battery() -> bool:
        """Return True if running on battery below 20%.  macOS only."""
        if platform.system() != "Darwin":
            return False
        try:
            import subprocess
            result = subprocess.run(
                ["pmset", "-g", "batt"],
                capture_output=True, text=True, timeout=2,
            )
            output = result.stdout
            if "Battery Power" not in output:
                return False  # plugged in
            # Parse percentage: "XX%"
            m = re.search(r"(\d+)%", output)
            if m and int(m.group(1)) < 20:
                return True
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_observer: ActivityObserver | None = None
_obs_lock = threading.Lock()


def get_observer() -> ActivityObserver:
    global _observer
    if _observer is None:
        with _obs_lock:
            if _observer is None:
                _observer = ActivityObserver()
    return _observer
