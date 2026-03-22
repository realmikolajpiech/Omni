"""Local Knowledge Graph backed by SQLite.

Stores entities (files, people, events, emails, URLs, apps, work sessions),
relationships between them, and a rolling activity log.  All data stays local.
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid

from src.core.config import CONTEXT_DB_PATH, CONTEXT_PRUNE_DAYS

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,          -- file, person, event, email, url, app, work_session
    name         TEXT NOT NULL,
    uri          TEXT,                   -- file path, URL, email address, calendar ID, etc.
    metadata     TEXT DEFAULT '{}',      -- JSON blob for type-specific extras
    first_seen   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    access_count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_entities_type      ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_uri       ON entities(uri);
CREATE INDEX IF NOT EXISTS idx_entities_last_seen ON entities(last_seen);
CREATE INDEX IF NOT EXISTS idx_entities_name      ON entities(name);

CREATE TABLE IF NOT EXISTS relationships (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel_type   TEXT NOT NULL,           -- co_active, mentioned_in, attendee_of, sent_by, related_to
    weight     REAL DEFAULT 1.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_pair ON relationships(source_id, target_id, rel_type);

CREATE TABLE IF NOT EXISTS activity_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    REAL NOT NULL,
    app_name     TEXT NOT NULL,
    window_title TEXT,
    file_path    TEXT,
    duration_s   REAL DEFAULT 0,
    entity_id    TEXT REFERENCES entities(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_ts  ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_app ON activity_log(app_name);

CREATE TABLE IF NOT EXISTS work_sessions (
    id           TEXT PRIMARY KEY,
    start_time   REAL NOT NULL,
    end_time     REAL NOT NULL,
    summary      TEXT,
    entity_ids   TEXT DEFAULT '[]',     -- JSON array
    resume_state TEXT DEFAULT '{}'      -- JSON: {app_paths: [...], urls: [...]}
);
CREATE INDEX IF NOT EXISTS idx_ws_start ON work_sessions(start_time);

CREATE TABLE IF NOT EXISTS suggestions (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,           -- meeting_prep, file_suggestion, pattern
    content    TEXT NOT NULL,           -- JSON payload
    created_at REAL NOT NULL,
    shown_at   REAL,
    dismissed  INTEGER DEFAULT 0,
    acted_on   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_suggestions_date ON suggestions(created_at);
"""


class KnowledgeGraph:
    """Thread-safe SQLite knowledge graph.  Single instance per process."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or CONTEXT_DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        _log.info("[context] Knowledge graph initialised at %s", self._db_path)

    def _now(self) -> float:
        return time.time()

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        uri: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Insert or update an entity.  De-duplicates on (type, uri) if uri is
        set, otherwise on (type, name).  Returns the entity ID."""
        now = self._now()
        meta_json = json.dumps(metadata or {})
        with self._lock:
            # Try to find existing
            if uri:
                row = self._conn.execute(
                    "SELECT id, access_count FROM entities WHERE type=? AND uri=?",
                    (entity_type, uri),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT id, access_count FROM entities WHERE type=? AND name=?",
                    (entity_type, name),
                ).fetchone()

            if row:
                eid, count = row
                self._conn.execute(
                    "UPDATE entities SET last_seen=?, access_count=?, metadata=? WHERE id=?",
                    (now, count + 1, meta_json, eid),
                )
                self._conn.commit()
                return eid

            eid = self._new_id()
            self._conn.execute(
                "INSERT INTO entities (id, type, name, uri, metadata, first_seen, last_seen, access_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (eid, entity_type, name, uri, meta_json, now, now),
            )
            self._conn.commit()
            return eid

    def get_entity(self, entity_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, type, name, uri, metadata, first_seen, last_seen, access_count "
                "FROM entities WHERE id=?",
                (entity_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "type": row[1], "name": row[2], "uri": row[3],
            "metadata": json.loads(row[4] or "{}"),
            "first_seen": row[5], "last_seen": row[6], "access_count": row[7],
        }

    def get_entity_by_uri(self, uri: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, type, name, uri, metadata, first_seen, last_seen, access_count "
                "FROM entities WHERE uri=?",
                (uri,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "type": row[1], "name": row[2], "uri": row[3],
            "metadata": json.loads(row[4] or "{}"),
            "first_seen": row[5], "last_seen": row[6], "access_count": row[7],
        }

    def search_entities(self, query: str, entity_type: str | None = None, limit: int = 10) -> list[dict]:
        """Case-insensitive LIKE search on name and uri."""
        like = f"%{query}%"
        with self._lock:
            if entity_type:
                rows = self._conn.execute(
                    "SELECT id, type, name, uri, metadata, first_seen, last_seen, access_count "
                    "FROM entities WHERE type=? AND (name LIKE ? OR uri LIKE ?) "
                    "ORDER BY last_seen DESC LIMIT ?",
                    (entity_type, like, like, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, type, name, uri, metadata, first_seen, last_seen, access_count "
                    "FROM entities WHERE (name LIKE ? OR uri LIKE ?) "
                    "ORDER BY last_seen DESC LIMIT ?",
                    (like, like, limit),
                ).fetchall()
        return [
            {
                "id": r[0], "type": r[1], "name": r[2], "uri": r[3],
                "metadata": json.loads(r[4] or "{}"),
                "first_seen": r[5], "last_seen": r[6], "access_count": r[7],
            }
            for r in rows
        ]

    def get_recent_entities(self, entity_type: str | None = None, limit: int = 20) -> list[dict]:
        with self._lock:
            if entity_type:
                rows = self._conn.execute(
                    "SELECT id, type, name, uri, metadata, first_seen, last_seen, access_count "
                    "FROM entities WHERE type=? ORDER BY last_seen DESC LIMIT ?",
                    (entity_type, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, type, name, uri, metadata, first_seen, last_seen, access_count "
                    "FROM entities ORDER BY last_seen DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r[0], "type": r[1], "name": r[2], "uri": r[3],
                "metadata": json.loads(r[4] or "{}"),
                "first_seen": r[5], "last_seen": r[6], "access_count": r[7],
            }
            for r in rows
        ]

    def delete_entity(self, entity_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def add_relationship(self, source_id: str, target_id: str, rel_type: str) -> None:
        """Create or increment weight of a relationship."""
        now = self._now()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO relationships (source_id, target_id, rel_type, weight, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1.0, ?, ?)",
                    (source_id, target_id, rel_type, now, now),
                )
            except sqlite3.IntegrityError:
                self._conn.execute(
                    "UPDATE relationships SET weight = weight + 1.0, updated_at = ? "
                    "WHERE source_id=? AND target_id=? AND rel_type=?",
                    (now, source_id, target_id, rel_type),
                )
            self._conn.commit()

    def get_related_entities(self, entity_id: str, rel_type: str | None = None, limit: int = 10) -> list[dict]:
        """Get entities related to the given one, sorted by weight descending."""
        with self._lock:
            if rel_type:
                rows = self._conn.execute(
                    "SELECT e.id, e.type, e.name, e.uri, e.metadata, e.last_seen, e.access_count, "
                    "       r.rel_type, r.weight "
                    "FROM relationships r JOIN entities e ON e.id = r.target_id "
                    "WHERE r.source_id=? AND r.rel_type=? "
                    "ORDER BY r.weight DESC LIMIT ?",
                    (entity_id, rel_type, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT e.id, e.type, e.name, e.uri, e.metadata, e.last_seen, e.access_count, "
                    "       r.rel_type, r.weight "
                    "FROM relationships r JOIN entities e ON e.id = r.target_id "
                    "WHERE r.source_id=? "
                    "ORDER BY r.weight DESC LIMIT ?",
                    (entity_id, limit),
                ).fetchall()

            # Also check reverse direction
            if rel_type:
                rows2 = self._conn.execute(
                    "SELECT e.id, e.type, e.name, e.uri, e.metadata, e.last_seen, e.access_count, "
                    "       r.rel_type, r.weight "
                    "FROM relationships r JOIN entities e ON e.id = r.source_id "
                    "WHERE r.target_id=? AND r.rel_type=? "
                    "ORDER BY r.weight DESC LIMIT ?",
                    (entity_id, rel_type, limit),
                ).fetchall()
            else:
                rows2 = self._conn.execute(
                    "SELECT e.id, e.type, e.name, e.uri, e.metadata, e.last_seen, e.access_count, "
                    "       r.rel_type, r.weight "
                    "FROM relationships r JOIN entities e ON e.id = r.source_id "
                    "WHERE r.target_id=? "
                    "ORDER BY r.weight DESC LIMIT ?",
                    (entity_id, limit),
                ).fetchall()

        all_rows = rows + rows2
        seen = set()
        results = []
        for r in sorted(all_rows, key=lambda x: x[8], reverse=True):
            if r[0] not in seen:
                seen.add(r[0])
                results.append({
                    "id": r[0], "type": r[1], "name": r[2], "uri": r[3],
                    "metadata": json.loads(r[4] or "{}"),
                    "last_seen": r[5], "access_count": r[6],
                    "rel_type": r[7], "weight": r[8],
                })
        return results[:limit]

    def get_relationship_weight(self, source_id: str, target_id: str) -> float:
        """Get the total relationship weight between two entities (both directions)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(weight), 0) FROM relationships "
                "WHERE (source_id=? AND target_id=?) OR (source_id=? AND target_id=?)",
                (source_id, target_id, target_id, source_id),
            ).fetchone()
        return row[0] if row else 0.0

    # ------------------------------------------------------------------
    # Activity Log
    # ------------------------------------------------------------------

    def log_activity(
        self,
        app_name: str,
        window_title: str | None = None,
        file_path: str | None = None,
        duration_s: float = 0,
        entity_id: str | None = None,
    ) -> int:
        """Record a single activity observation.  Returns the log row ID."""
        now = self._now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO activity_log (timestamp, app_name, window_title, file_path, duration_s, entity_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, app_name, window_title, file_path, duration_s, entity_id),
            )
            self._conn.commit()
            return cur.lastrowid

    def log_activity_batch(self, entries: list[dict]) -> int:
        """Batch-insert activity entries.  Each dict must have at least 'app_name'.
        Returns count inserted."""
        if not entries:
            return 0
        now = self._now()
        with self._lock:
            self._conn.executemany(
                "INSERT INTO activity_log (timestamp, app_name, window_title, file_path, duration_s, entity_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.get("timestamp", now),
                        e["app_name"],
                        e.get("window_title"),
                        e.get("file_path"),
                        e.get("duration_s", 0),
                        e.get("entity_id"),
                    )
                    for e in entries
                ],
            )
            self._conn.commit()
        return len(entries)

    def get_recent_activity(self, limit: int = 50, since: float | None = None) -> list[dict]:
        with self._lock:
            if since:
                rows = self._conn.execute(
                    "SELECT id, timestamp, app_name, window_title, file_path, duration_s, entity_id "
                    "FROM activity_log WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                    (since, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, timestamp, app_name, window_title, file_path, duration_s, entity_id "
                    "FROM activity_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r[0], "timestamp": r[1], "app_name": r[2],
                "window_title": r[3], "file_path": r[4],
                "duration_s": r[5], "entity_id": r[6],
            }
            for r in rows
        ]

    def prune_activity_log(self, days: int | None = None) -> int:
        """Delete activity entries older than N days.  Returns count deleted."""
        days = days or CONTEXT_PRUNE_DAYS
        cutoff = self._now() - (days * 86400)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM activity_log WHERE timestamp < ?", (cutoff,)
            )
            self._conn.commit()
        count = cur.rowcount
        if count:
            _log.info("[context] Pruned %d activity log entries older than %d days", count, days)
        return count

    # ------------------------------------------------------------------
    # Work Sessions
    # ------------------------------------------------------------------

    def create_work_session(
        self,
        start_time: float,
        end_time: float,
        summary: str | None = None,
        entity_ids: list[str] | None = None,
        resume_state: dict | None = None,
    ) -> str:
        sid = self._new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO work_sessions (id, start_time, end_time, summary, entity_ids, resume_state) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    sid, start_time, end_time, summary,
                    json.dumps(entity_ids or []),
                    json.dumps(resume_state or {}),
                ),
            )
            self._conn.commit()
        return sid

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, start_time, end_time, summary, entity_ids, resume_state "
                "FROM work_sessions ORDER BY start_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0], "start_time": r[1], "end_time": r[2],
                "summary": r[3],
                "entity_ids": json.loads(r[4] or "[]"),
                "resume_state": json.loads(r[5] or "{}"),
            }
            for r in rows
        ]

    def update_session_summary(self, session_id: str, summary: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE work_sessions SET summary=? WHERE id=?",
                (summary, session_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    def create_suggestion(self, stype: str, content: dict) -> str:
        sid = self._new_id()
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO suggestions (id, type, content, created_at) VALUES (?, ?, ?, ?)",
                (sid, stype, json.dumps(content), now),
            )
            self._conn.commit()
        return sid

    def get_today_suggestion_count(self) -> int:
        today_start = self._now() - (self._now() % 86400)
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM suggestions WHERE shown_at IS NOT NULL AND shown_at >= ?",
                (today_start,),
            ).fetchone()
        return row[0] if row else 0

    def mark_suggestion_shown(self, suggestion_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE suggestions SET shown_at=? WHERE id=?",
                (self._now(), suggestion_id),
            )
            self._conn.commit()

    def mark_suggestion_dismissed(self, suggestion_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE suggestions SET dismissed=1 WHERE id=?", (suggestion_id,)
            )
            self._conn.commit()

    def mark_suggestion_acted(self, suggestion_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE suggestions SET acted_on=1 WHERE id=?", (suggestion_id,)
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Context queries
    # ------------------------------------------------------------------

    def get_context_for_entity(self, entity_id: str) -> dict | None:
        """Return entity + relationships + recent activity.  For context cards."""
        entity = self.get_entity(entity_id)
        if not entity:
            return None
        related = self.get_related_entities(entity_id, limit=10)
        with self._lock:
            activities = self._conn.execute(
                "SELECT timestamp, app_name, window_title, duration_s "
                "FROM activity_log WHERE entity_id=? ORDER BY timestamp DESC LIMIT 10",
                (entity_id,),
            ).fetchall()
        entity["related"] = related
        entity["recent_activity"] = [
            {"timestamp": a[0], "app_name": a[1], "window_title": a[2], "duration_s": a[3]}
            for a in activities
        ]
        return entity

    def get_active_entity_ids(self, window_seconds: int = 300) -> list[str]:
        """Return entity IDs from recent activity (default: last 5 minutes)."""
        since = self._now() - window_seconds
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT entity_id FROM activity_log "
                "WHERE entity_id IS NOT NULL AND timestamp >= ?",
                (since,),
            ).fetchall()
        return [r[0] for r in rows]

    def get_stats(self) -> dict:
        """Return counts of entities, relationships, and activity entries."""
        with self._lock:
            entities = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            rels = self._conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
            activities = self._conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
            sessions = self._conn.execute("SELECT COUNT(*) FROM work_sessions").fetchone()[0]
        return {
            "entities": entities,
            "relationships": rels,
            "activity_entries": activities,
            "work_sessions": sessions,
        }

    def clear_all(self) -> None:
        """Delete all data.  For privacy: user-triggered full wipe."""
        with self._lock:
            self._conn.executescript(
                "DELETE FROM suggestions; DELETE FROM work_sessions; "
                "DELETE FROM activity_log; DELETE FROM relationships; "
                "DELETE FROM entities;"
            )
        _log.info("[context] All context data cleared")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: KnowledgeGraph | None = None
_instance_lock = threading.Lock()


def get_knowledge_graph() -> KnowledgeGraph:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = KnowledgeGraph()
    return _instance
