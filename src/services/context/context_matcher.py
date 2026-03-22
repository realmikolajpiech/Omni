"""Context Matcher — scores entities by relevance and re-ranks search results.

Scoring formula:
    final_score = 0.5 * semantic_similarity + 0.3 * context_relevance + 0.2 * recency_score

Performance target: <200ms total (pure SQLite lookups, no LLM calls).
"""

import logging
import math
import threading
import time

from src.services.context.knowledge_graph import KnowledgeGraph, get_knowledge_graph

_log = logging.getLogger(__name__)

# Recency decay constants
_RECENCY_HALF_LIFE_DAYS = 7    # score halves every 7 days
_RECENCY_MAX_DAYS = 30         # beyond this, recency_score = 0
_RECENT_BOOST_DAYS = 7         # last 7 days weighted 3x


class ContextMatcher:
    """Scores entities by relevance to current activity context."""

    def __init__(self, kg: KnowledgeGraph | None = None):
        self._kg = kg or get_knowledge_graph()
        self._cache: dict[str, tuple[float, float]] = {}  # entity_id -> (score, timestamp)
        self._cache_ttl = 60  # seconds

    def get_context_score(self, entity_id: str) -> float:
        """Score [0, 1] based on relationship strength to currently active entities."""
        # Check cache
        now = time.time()
        cached = self._cache.get(entity_id)
        if cached and (now - cached[1]) < self._cache_ttl:
            return cached[0]

        active_ids = self._kg.get_active_entity_ids(window_seconds=300)
        if not active_ids:
            return 0.0

        # Sum relationship weights between this entity and all active entities
        total_weight = 0.0
        for active_id in active_ids:
            if active_id == entity_id:
                total_weight += 5.0  # entity itself is active — strong signal
                continue
            w = self._kg.get_relationship_weight(entity_id, active_id)
            total_weight += w

        # Normalize to [0, 1] — sigmoid-like curve
        score = min(1.0, total_weight / (total_weight + 5.0)) if total_weight > 0 else 0.0

        self._cache[entity_id] = (score, now)
        return score

    def get_recency_score(self, entity_id: str) -> float:
        """Score [0, 1] based on when the entity was last seen.
        Last 7 days weighted 3x (exponential decay with half-life of 7 days)."""
        entity = self._kg.get_entity(entity_id)
        if not entity:
            return 0.0

        last_seen = entity.get("last_seen", 0)
        if not last_seen:
            return 0.0

        now = time.time()
        age_days = (now - last_seen) / 86400

        if age_days > _RECENCY_MAX_DAYS:
            return 0.0

        # Exponential decay with half-life
        decay = math.exp(-0.693 * age_days / _RECENCY_HALF_LIFE_DAYS)

        # Boost for recent items (last 7 days get 3x weight, capped at 1.0)
        if age_days <= _RECENT_BOOST_DAYS:
            decay = min(1.0, decay * 3.0)

        return min(1.0, decay)

    def get_recency_score_from_timestamp(self, last_seen: float) -> float:
        """Score [0, 1] from a raw timestamp, without looking up an entity."""
        if not last_seen:
            return 0.0

        now = time.time()
        age_days = (now - last_seen) / 86400

        if age_days > _RECENCY_MAX_DAYS:
            return 0.0

        decay = math.exp(-0.693 * age_days / _RECENCY_HALF_LIFE_DAYS)
        if age_days <= _RECENT_BOOST_DAYS:
            decay = min(1.0, decay * 3.0)
        return min(1.0, decay)

    def rank_search_results(self, results: list[dict], query: str = "") -> list[dict]:
        """Re-rank search results using the context-aware formula.

        Each result dict should have at least:
          - 'path' or 'uri': str
          - 'score': float (semantic similarity, 0-1)
        Optional:
          - 'name': str
          - 'type': str

        Returns the same list, sorted by final_score descending, with
        'final_score', 'context_score', 'recency_score' added.
        """
        if not results:
            return results

        for r in results:
            uri = r.get("path") or r.get("uri") or ""
            semantic = r.get("score", 0.5)

            # Look up entity by URI to get context and recency
            entity = self._kg.get_entity_by_uri(uri) if uri else None

            if entity:
                ctx_score = self.get_context_score(entity["id"])
                rec_score = self.get_recency_score(entity["id"])
            else:
                ctx_score = 0.0
                # Fall back to file modification time for recency
                rec_score = self._file_recency(uri) if uri else 0.0

            final = 0.5 * semantic + 0.3 * ctx_score + 0.2 * rec_score

            r["semantic_score"] = round(semantic, 4)
            r["context_score"] = round(ctx_score, 4)
            r["recency_score"] = round(rec_score, 4)
            r["final_score"] = round(final, 4)

        results.sort(key=lambda r: r.get("final_score", 0), reverse=True)
        return results

    def get_relevant_entities(self, limit: int = 5) -> list[dict]:
        """Return the most relevant entities given current context.

        Combines active entities + recent entities, scored and sorted.
        """
        active_ids = self._kg.get_active_entity_ids(window_seconds=300)
        recent = self._kg.get_recent_entities(limit=30)

        scored = []
        seen = set()
        for entity in recent:
            eid = entity["id"]
            if eid in seen:
                continue
            seen.add(eid)

            ctx = self.get_context_score(eid)
            rec = self.get_recency_score(eid)
            # For relevance ranking (no semantic query), weight context higher
            final = 0.5 * ctx + 0.5 * rec
            entity["relevance_score"] = round(final, 4)
            scored.append(entity)

        scored.sort(key=lambda e: e.get("relevance_score", 0), reverse=True)
        return scored[:limit]

    @staticmethod
    def _file_recency(path: str) -> float:
        """Fallback recency from file's mtime when entity isn't in the graph."""
        try:
            import os
            mtime = os.path.getmtime(path)
            age_days = (time.time() - mtime) / 86400
            if age_days > _RECENCY_MAX_DAYS:
                return 0.0
            decay = math.exp(-0.693 * age_days / _RECENCY_HALF_LIFE_DAYS)
            if age_days <= _RECENT_BOOST_DAYS:
                decay = min(1.0, decay * 3.0)
            return min(1.0, decay)
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: ContextMatcher | None = None
_lock = threading.Lock()


def get_matcher() -> ContextMatcher:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ContextMatcher()
    return _instance
