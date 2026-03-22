"""Suggestion Engine — proactive suggestions based on context triggers.

Background thread that checks for triggers every 60 seconds:
  - Calendar trigger: 15 min before a meeting, show context card
  - File trigger: suggest related files when opening a project
  - Pattern trigger: detect recurring workflows

Constraints:
  - Max 5 suggestions per day (configurable via CONTEXT_MAX_SUGGESTIONS_PER_DAY)
  - No interruption in full-screen
  - Learn from dismissals (reduce weight for dismissed suggestion types)
"""

import json
import logging
import platform
import re
import socket
import threading
import time
from datetime import datetime, timedelta

from src.core.config import (
    CONTEXT_MAX_SUGGESTIONS_PER_DAY,
    IPC_PORT,
)
from src.services.context.knowledge_graph import KnowledgeGraph, get_knowledge_graph
from src.services.context.context_matcher import ContextMatcher, get_matcher
from src.services.context.entity_builder import (
    build_entities_from_calendar,
    build_entities_from_emails,
)

_log = logging.getLogger(__name__)

_CHECK_INTERVAL = 60            # seconds between trigger checks
_ENTITY_REFRESH_INTERVAL = 600  # rebuild calendar/email entities every 10 min
_MEETING_LEAD_TIME_S = 900      # 15 minutes before meeting


class SuggestionEngine(threading.Thread):
    """Daemon thread that generates proactive suggestions."""

    def __init__(self, kg: KnowledgeGraph | None = None, matcher: ContextMatcher | None = None):
        super().__init__(daemon=True, name="SuggestionEngine")
        self._kg = kg or get_knowledge_graph()
        self._matcher = matcher or get_matcher()
        self._stop_event = threading.Event()
        self._shown_meetings: set[str] = set()  # event URIs already notified

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        _log.info("[context] Suggestion Engine started")
        last_entity_refresh = 0

        while not self._stop_event.is_set():
            try:
                now = time.time()

                # Periodically refresh calendar/email entities
                if now - last_entity_refresh >= _ENTITY_REFRESH_INTERVAL:
                    try:
                        build_entities_from_calendar(self._kg)
                        build_entities_from_emails(self._kg)
                    except Exception as e:
                        _log.debug("[context] Entity refresh error: %s", e)
                    last_entity_refresh = now

                # Check daily quota
                if self._kg.get_today_suggestion_count() >= CONTEXT_MAX_SUGGESTIONS_PER_DAY:
                    self._stop_event.wait(_CHECK_INTERVAL)
                    continue

                # Don't interrupt full-screen apps
                if self._is_fullscreen():
                    self._stop_event.wait(_CHECK_INTERVAL)
                    continue

                # Check triggers
                suggestions = []
                suggestions.extend(self._check_meeting_triggers())
                suggestions.extend(self._check_file_triggers())

                for suggestion in suggestions:
                    if self._kg.get_today_suggestion_count() >= CONTEXT_MAX_SUGGESTIONS_PER_DAY:
                        break
                    self._emit_suggestion(suggestion)

            except Exception as e:
                _log.warning("[context] Suggestion engine error: %s", e)

            self._stop_event.wait(_CHECK_INTERVAL)

        _log.info("[context] Suggestion Engine stopped")

    # ------------------------------------------------------------------
    # Meeting triggers
    # ------------------------------------------------------------------

    def _check_meeting_triggers(self) -> list[dict]:
        """Check if any meeting is starting within 15 minutes."""
        suggestions = []

        # Get upcoming events from the knowledge graph
        events = self._kg.search_entities("", entity_type="event", limit=20)
        now = time.time()

        for event in events:
            meta = event.get("metadata", {})
            start_str = meta.get("start", "")
            event_uri = event.get("uri", "")

            if not start_str or event_uri in self._shown_meetings:
                continue

            # Parse the start time
            start_ts = self._parse_event_time(start_str)
            if not start_ts:
                continue

            # Check if within lead time window
            time_until = start_ts - now
            if 0 < time_until <= _MEETING_LEAD_TIME_S:
                # Build meeting context
                context = self._build_meeting_context(event)
                if context:
                    self._shown_meetings.add(event_uri)
                    suggestions.append(context)

        return suggestions

    def _build_meeting_context(self, event: dict) -> dict | None:
        """Build a meeting prep context card."""
        event_id = event["id"]
        event_name = event.get("name", "Meeting")
        meta = event.get("metadata", {})

        # Find related people (attendees)
        related = self._kg.get_related_entities(event_id, rel_type="attendee_of", limit=10)
        people = [r for r in related if r.get("type") == "person"]

        # Find files related to those people (or to the event)
        related_files = []
        for person in people:
            person_files = self._kg.get_related_entities(
                person["id"], rel_type="co_active", limit=5
            )
            for f in person_files:
                if f.get("type") == "file" and f not in related_files:
                    related_files.append(f)

        # Also get files directly related to the event
        event_files = self._kg.get_related_entities(event_id, limit=5)
        for f in event_files:
            if f.get("type") == "file" and f not in related_files:
                related_files.append(f)

        # Find recent emails from attendees
        related_emails = []
        for person in people:
            person_emails = self._kg.get_related_entities(
                person["id"], rel_type="sent_by", limit=3
            )
            for e in person_emails:
                if e.get("type") == "email" and e not in related_emails:
                    related_emails.append(e)

        return {
            "type": "meeting_prep",
            "event": {
                "name": event_name,
                "start": meta.get("start", ""),
                "end": meta.get("end", ""),
                "calendar": meta.get("calendar", ""),
                "description": meta.get("description", ""),
            },
            "people": [
                {"name": p.get("name", ""), "email": p.get("uri", "")}
                for p in people[:5]
            ],
            "files": [
                {"name": f.get("name", ""), "path": f.get("uri", "")}
                for f in related_files[:5]
            ],
            "emails": [
                {"subject": e.get("name", ""), "sender": e.get("metadata", {}).get("sender", "")}
                for e in related_emails[:5]
            ],
        }

    # ------------------------------------------------------------------
    # File triggers
    # ------------------------------------------------------------------

    def _check_file_triggers(self) -> list[dict]:
        """When user is working in a project, suggest related files."""
        suggestions = []

        # Get currently active entities
        active_ids = self._kg.get_active_entity_ids(window_seconds=300)
        if not active_ids:
            return suggestions

        # Find active file entities
        active_files = []
        for eid in active_ids:
            entity = self._kg.get_entity(eid)
            if entity and entity.get("type") == "file":
                active_files.append(entity)

        if not active_files:
            return suggestions

        # For each active file, find related files the user hasn't opened recently
        for active_file in active_files[:2]:  # limit to avoid spam
            related = self._kg.get_related_entities(active_file["id"], limit=5)
            related_files = [
                r for r in related
                if r.get("type") == "file"
                and r["id"] not in active_ids
                and r.get("weight", 0) >= 3  # only suggest strongly related files
            ]

            if related_files:
                suggestions.append({
                    "type": "file_suggestion",
                    "context_file": {
                        "name": active_file.get("name", ""),
                        "path": active_file.get("uri", ""),
                    },
                    "suggested_files": [
                        {"name": f.get("name", ""), "path": f.get("uri", "")}
                        for f in related_files[:3]
                    ],
                })
                break  # one file suggestion per cycle is enough

        return suggestions

    # ------------------------------------------------------------------
    # Emit suggestions
    # ------------------------------------------------------------------

    def _emit_suggestion(self, suggestion: dict) -> None:
        """Store and send suggestion to the UI via IPC."""
        stype = suggestion.get("type", "unknown")
        sid = self._kg.create_suggestion(stype, suggestion)
        self._kg.mark_suggestion_shown(sid)
        suggestion["suggestion_id"] = sid

        # Send via IPC
        try:
            payload = json.dumps(suggestion, default=str)
            msg = f"SUGGESTION:{payload}"
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(("127.0.0.1", IPC_PORT))
            sock.sendall(msg.encode("utf-8"))
            sock.close()
            _log.info("[context] Emitted %s suggestion: %s", stype, sid)
        except Exception as e:
            _log.debug("[context] IPC send failed (UI may not be running): %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_event_time(time_str: str) -> float | None:
        """Try to parse a calendar event time string into a unix timestamp."""
        # macOS Calendar app returns dates like "Saturday, March 21, 2026 at 2:00:00 PM"
        formats = [
            "%A, %B %d, %Y at %I:%M:%S %p",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%B %d, %Y at %I:%M %p",
            "%d/%m/%Y %H:%M",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(time_str.strip(), fmt)
                return dt.timestamp()
            except ValueError:
                continue
        return None

    @staticmethod
    def _is_fullscreen() -> bool:
        """Check if the frontmost app is in fullscreen mode (macOS only)."""
        if platform.system() != "Darwin":
            return False
        try:
            import subprocess
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get value of attribute "AXFullScreen" of window 1 of (first application process whose frontmost is true)'],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip().lower() == "true"
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: SuggestionEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> SuggestionEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = SuggestionEngine()
    return _engine
