"""Session Manager — clusters activity into work sessions with summaries.

Detects session boundaries based on activity gaps (>15 min) or complete
context switches.  Generates LLM summaries using the fast model.
Supports one-click session resume (reopen files/apps).
"""

import json
import logging
import os
import platform
import subprocess
import threading
import time

from src.services.context.knowledge_graph import KnowledgeGraph, get_knowledge_graph

_log = logging.getLogger(__name__)

_SESSION_GAP_S = 900            # 15 min gap = new session
_CLUSTER_INTERVAL_S = 1800      # run clustering every 30 min
_MIN_SESSION_DURATION_S = 120   # ignore sessions shorter than 2 min
_MIN_SESSION_ACTIVITIES = 3     # need at least 3 activities for a session


class SessionManager(threading.Thread):
    """Daemon thread that periodically clusters activities into work sessions."""

    def __init__(self, kg: KnowledgeGraph | None = None):
        super().__init__(daemon=True, name="SessionManager")
        self._kg = kg or get_knowledge_graph()
        self._stop_event = threading.Event()
        self._last_cluster_ts: float = 0  # track what we already processed

    def stop(self):
        self._stop_event.set()

    def run(self):
        _log.info("[context] Session Manager started (interval=%ds)", _CLUSTER_INTERVAL_S)
        # Wait a bit on startup for some activity to accumulate
        self._stop_event.wait(60)

        while not self._stop_event.is_set():
            try:
                self.cluster_sessions()
            except Exception as e:
                _log.warning("[context] Session clustering error: %s", e)
            self._stop_event.wait(_CLUSTER_INTERVAL_S)

        _log.info("[context] Session Manager stopped")

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def cluster_sessions(self) -> list[dict]:
        """Analyze recent activity_log entries and create work_sessions.

        Returns list of newly created sessions (dicts with id, start_time, etc.).
        """
        # Get activities since last clustering
        since = self._last_cluster_ts or (time.time() - 86400)  # default: last 24h
        activities = self._kg.get_recent_activity(limit=500, since=since)

        if not activities:
            return []

        # Sort chronologically
        activities.sort(key=lambda a: a["timestamp"])

        # Split into sessions based on gaps
        sessions_raw: list[list[dict]] = []
        current_session: list[dict] = [activities[0]]

        for i in range(1, len(activities)):
            gap = activities[i]["timestamp"] - activities[i - 1]["timestamp"]
            if gap >= _SESSION_GAP_S:
                sessions_raw.append(current_session)
                current_session = [activities[i]]
            else:
                current_session.append(activities[i])

        if current_session:
            sessions_raw.append(current_session)

        # Create session records
        new_sessions = []
        for session_activities in sessions_raw:
            if len(session_activities) < _MIN_SESSION_ACTIVITIES:
                continue

            start_time = session_activities[0]["timestamp"]
            end_time = session_activities[-1]["timestamp"]
            duration = end_time - start_time + session_activities[-1].get("duration_s", 0)

            if duration < _MIN_SESSION_DURATION_S:
                continue

            # Collect entity IDs and build resume state
            entity_ids = list(dict.fromkeys(
                a["entity_id"] for a in session_activities if a.get("entity_id")
            ))

            # Build resume state: unique (app, file) pairs + standalone apps
            app_paths = []
            seen_paths = set()
            seen_apps = set()
            for a in session_activities:
                fp = a.get("file_path")
                app = a.get("app_name", "")
                if fp and fp not in seen_paths:
                    seen_paths.add(fp)
                    seen_apps.add(app)
                    app_paths.append({"app": app, "path": fp})
            # Also include apps without files so they can be relaunched
            for a in session_activities:
                app = a.get("app_name", "")
                if app and app not in seen_apps:
                    seen_apps.add(app)
                    app_paths.append({"app": app, "path": ""})

            resume_state = {"app_paths": app_paths[:10]}  # cap at 10

            # Generate summary from metadata
            summary = self._generate_summary_local(session_activities)

            sid = self._kg.create_work_session(
                start_time=start_time,
                end_time=end_time,
                summary=summary,
                entity_ids=entity_ids,
                resume_state=resume_state,
            )

            new_sessions.append({
                "id": sid,
                "start_time": start_time,
                "end_time": end_time,
                "summary": summary,
                "entity_ids": entity_ids,
                "resume_state": resume_state,
            })

        # Update watermark
        if activities:
            self._last_cluster_ts = activities[-1]["timestamp"]

        if new_sessions:
            _log.info("[context] Created %d work sessions", len(new_sessions))

            # Async LLM summary enhancement
            for session in new_sessions:
                try:
                    self._enhance_summary_llm(session)
                except Exception as e:
                    _log.debug("[context] LLM summary enhancement failed: %s", e)

        return new_sessions

    def _generate_summary_local(self, activities: list[dict]) -> str:
        """Generate a summary from metadata (no LLM call)."""
        apps = list(dict.fromkeys(a["app_name"] for a in activities if a.get("app_name")))
        files = list(dict.fromkeys(
            os.path.basename(a["file_path"]) for a in activities if a.get("file_path")
        ))
        # Also extract unique window titles for richer summaries when no files
        window_titles = list(dict.fromkeys(
            a["window_title"] for a in activities
            if a.get("window_title") and a["window_title"] not in ("", None)
        ))

        duration_s = activities[-1]["timestamp"] - activities[0]["timestamp"]
        duration_min = max(1, int(duration_s / 60))

        parts = []
        if files:
            parts.append(f"Worked on {', '.join(files[:4])}")
            if apps:
                parts.append(f"in {', '.join(apps[:3])}")
        elif window_titles:
            # Use window titles as context (project names, page titles)
            meaningful = [t for t in window_titles if len(t) > 2 and t.lower() not in {a.lower() for a in apps}]
            if meaningful:
                parts.append(f"Worked on {', '.join(meaningful[:4])}")
            if apps:
                parts.append(f"using {', '.join(apps[:3])}")
        elif apps:
            parts.append(f"Used {', '.join(apps[:3])}")
        parts.append(f"({duration_min} min)")

        return " ".join(parts) + "."

    def _enhance_summary_llm(self, session: dict) -> None:
        """Enhance session summary using the fast model (Groq)."""
        try:
            from src.services.llm.model_manager import fast_model, fast_lock

            if not fast_model:
                return

            resume = session.get("resume_state", {})
            app_paths = resume.get("app_paths", [])
            apps = list(dict.fromkeys(ap.get("app", "") for ap in app_paths))
            files = [os.path.basename(ap.get("path", "")) for ap in app_paths]

            duration_s = session["end_time"] - session["start_time"]
            duration_min = max(1, int(duration_s / 60))

            prompt = (
                f"Write a brief one-sentence summary of this work session "
                f"({duration_min} min): Apps used: {', '.join(apps[:5])}. "
                f"Files: {', '.join(files[:5])}. "
                f"Be concise, e.g. 'Worked on the payment module in VS Code and tested in Chrome.'"
            )

            with fast_lock:
                resp = fast_model.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=60,
                    temperature=0.3,
                )

            if resp and resp.choices:
                summary = resp.choices[0].message.content.strip()
                if summary:
                    self._kg.update_session_summary(session["id"], summary)
                    session["summary"] = summary
        except Exception as e:
            _log.debug("[context] LLM summary failed: %s", e)

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------

    @staticmethod
    def resume_session(session: dict) -> str:
        """Open all files/apps from a session's resume_state.

        Returns a status message.
        """
        resume_state = session.get("resume_state", {})
        app_paths = resume_state.get("app_paths", [])

        if not app_paths:
            return "No files to resume."

        opened = 0
        for item in app_paths:
            path = item.get("path", "")
            if not path:
                continue
            try:
                if platform.system() == "Darwin":
                    subprocess.Popen(["open", path])
                elif platform.system() == "Windows":
                    os.startfile(path)
                else:
                    subprocess.Popen(["xdg-open", path])
                opened += 1
            except Exception as e:
                _log.debug("[context] Failed to open %s: %s", path, e)

        return f"Resumed session: opened {opened} file(s)."

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        return self._kg.get_recent_sessions(limit=limit)

    def get_session(self, session_id: str) -> dict | None:
        sessions = self._kg.get_recent_sessions(limit=100)
        for s in sessions:
            if s["id"] == session_id:
                return s
        return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: SessionManager | None = None
_mgr_lock = threading.Lock()


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        with _mgr_lock:
            if _manager is None:
                _manager = SessionManager()
    return _manager
