import logging
import re
import unicodedata
from src.services.llm.model_manager import ensure_fast_model, fast_model, fast_lock, db_conn
from src.services.memory.memvid_store import get_user_memory
import src.services.llm.model_manager as model_manager

# Maximum CLIP cosine distance to count as a semantic match
CLIP_DISTANCE_THRESHOLD = 0.6


def should_search_images(query):
    """Uses Fast Model to decide if we need to search images."""
    query_lower = query.lower()
    img_patterns = [
        "photo", "image", "picture", "screenshot", "camera", "look like",
        "find photo", "search image", "draw", "generate",
        "wallpaper", "background"
    ]
    if "show me" in query_lower and not any(x in query_lower for x in ["video", "trailer", "movie", "youtube", "how to", "make", "recipe", "why", "who is", "where is"]):
        return True
    if any(pattern in query_lower for pattern in img_patterns):
        return True
    return False


def _images_table():
    """Return the 'images' LanceDB table, or None if it doesn't exist."""
    try:
        if not model_manager.db_conn:
            return None
        # table_names() returns a plain list of strings
        if "images" not in model_manager.db_conn.table_names():
            return None
        return model_manager.db_conn.open_table("images")
    except Exception as e:
        logging.error(f"Could not open images table: {e}")
        return None


def _rows_from_arrow(arrow_table):
    """Yield dicts with filename, path, _distance from an Arrow result."""
    names = arrow_table.schema.names
    for i in range(arrow_table.num_rows):
        row = {col: arrow_table.column(col)[i].as_py() for col in names}
        yield row


def perform_image_search(query):
    """CLIP semantic search. Returns list of result strings (empty = nothing found)."""
    model_manager.ensure_vision_model()
    if model_manager.vision_model is None:
        return []

    tbl = _images_table()
    if tbl is None:
        return []

    try:
        vector = model_manager.vision_model.encode(query).tolist()
        logging.info(f"[image-search] CLIP query: '{query}' (dim={len(vector)})")
        res = tbl.search(vector).limit(5).to_arrow()
        results = []
        for row in _rows_from_arrow(res):
            dist = row.get('_distance', 1.0)
            logging.info(f"[image-search] CLIP candidate: {row['filename']} dist={dist:.4f}")
            if dist < CLIP_DISTANCE_THRESHOLD:
                results.append(f"Found Image: {row['filename']}\nPath: {row['path']}")
        return results
    except Exception as e:
        logging.error(f"[image-search] CLIP search failed: {e}")
        return []


def _filename_keyword_search(keywords):
    """Exact filename substring search. Returns list of result strings."""
    tbl = _images_table()
    if tbl is None:
        return []

    results = []
    seen = set()
    for kw in keywords:
        if len(kw) < 2:
            continue
        try:
            matches = tbl.search().where(f"filename LIKE '%{kw}%'").limit(5).to_arrow()
            for row in _rows_from_arrow(matches):
                if row['path'] not in seen:
                    seen.add(row['path'])
                    results.append(f"Found Image (By Name): {row['filename']}\nPath: {row['path']}")
                    logging.info(f"[image-search] filename match: {row['filename']} (kw='{kw}')")
        except Exception as e:
            logging.error(f"[image-search] filename search failed for '{kw}': {e}")
    return results


def _name_variants(name_part):
    """Return ASCII/unicode variants of a name part for fuzzy filename matching."""
    replacements = {'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n',
                    'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z'}
    ascii_part = name_part
    for k, v in replacements.items():
        ascii_part = ascii_part.replace(k, v)
    normalized = unicodedata.normalize('NFKD', ascii_part).encode('ASCII', 'ignore').decode('utf-8')
    return {v for v in {name_part, ascii_part, normalized} if len(v) >= 3}


STOP_WORDS = {
    "photo", "image", "picture", "photos", "images", "pictures",
    "my", "me", "a", "an", "the", "of", "from", "find", "show",
    "search", "get", "look", "for", "please", "some", "any",
}


def perform_image_search_with_fallback(query):
    """CLIP semantic search with filename fallback."""
    # 1. Semantic search
    semantic = perform_image_search(query)

    # 2. If semantic found nothing, fall back to filename keyword search
    if not semantic:
        query_lower = query.lower()
        keywords = set()

        # Always extract plain query keywords
        for word in query.split():
            w = word.lower().strip("\"'.,!?")
            if w not in STOP_WORDS and len(w) > 1:
                keywords.add(w)

        # If "my"/"me", also try the user's name
        if "my" in query_lower or "me" in query_lower:
            mem_str = get_user_memory()
            name_match = re.search(r"user's name is ([^.\n]+)", mem_str, re.IGNORECASE)
            if name_match:
                for part in name_match.group(1).strip().lower().split():
                    keywords.update(_name_variants(part))

        if keywords:
            logging.info(f"[image-search] semantic found nothing — trying filename keywords: {keywords}")
            filename_results = _filename_keyword_search(list(keywords))
            if filename_results:
                return "\n\n".join(filename_results)

    return "\n\n".join(semantic)
