"""
WikiWorker — fetches Wikipedia summary for a query and emits a structured result.
Uses the Wikipedia REST API (no auth, free, fast ~100-300ms).
"""
from __future__ import annotations
import re
import logging
import requests
from PyQt6.QtCore import QThread, pyqtSignal


# Queries that definitely aren't Wikipedia topics
_SKIP_STARTS = (
    "how ", "why ", "when ", "what is ", "what's ", "who is ", "who was ",
    "can you", "please ", "make ", "create ", "write ", "tell me", "explain ",
    "open ", "install ", "find ", "search ", "show me", "calculate ", "calc ",
    "help ", "can i ", "should i", "i want", "i need", "give me", "list ",
    "compare ", "difference ", "versus ", "vs ", "translate ", "define ",
    "meaning ", "synonym ", "antonym ", "weather ", "time ", "date ",
    "set ", "change ", "turn ", "go to ", "visit ", "run ", "launch ",
    "youtube ", "google ", "bing ", "duckduckgo ", "play ", "watch ",
    "download ", "get ", "buy ", "order ", "shop ", "amazon ", "ebay ",
)

_SKIP_CHARS = {"?", "!", "\n", "\r", "@", "#", "$", "%", "^", "&", "*", "(", ")", "=", "{", "}", "[", "]", "|", "\\", ";", ":", "<", ">", ",", "/"}

# Common websites/apps that shouldn't trigger Wiki unless explicitly asked
_SKIP_EXACT = {
    "youtube", "google", "facebook", "twitter", "instagram", "linkedin", "reddit", "amazon", "netflix",
    "gmail", "outlook", "yahoo", "bing", "duckduckgo", "twitch", "tiktok", "whatsapp", "messenger",
    "spotify", "discord", "zoom", "teams", "slack", "trello", "notion", "figma", "dropbox",
    "chatgpt", "openai", "claude", "bard", "gemini", "midjourney", "dalle",
    "weather", "news", "maps", "translate", "calculator", "calendar", "photos", "camera", "settings",
    "mail", "notes", "reminders", "music", "podcasts", "tv", "books", "stocks", "wallet", "health",
    "home", "files", "finder", "safari", "chrome", "edge", "firefox", "brave", "opera", "vivaldi",
    "timer", "alarm", "stopwatch", "clock", "date", "time", "dictionary", "thesaurus",
    "help", "menu", "exit", "quit", "close", "restart", "shutdown", "reboot",
    "login", "logout", "signin", "signout", "password", "wifi", "bluetooth",
    "blyat", "kurwa", "fuck", "shit", "bitch", "damn", "asshole",
}

# Known short-form aliases → Wikipedia article titles
_ALIASES = {
    "js": "JavaScript",
    "ts": "TypeScript",
    "py": "Python (programming language)",
    "ml": "Machine learning",
    "ai": "Artificial intelligence",
    "nlp": "Natural language processing",
    "llm": "Large language model",
    "gpt": "Generative pre-trained transformer",
    "html": "HTML",
    "css": "CSS",
    "sql": "SQL",
    "api": "API",
    "http": "HTTP",
    "url": "URL",
    "cv": "Computer vision",
    "gui": "Graphical user interface",
    "ux": "User experience",
    "ui": "User interface",
    "ide": "Integrated development environment",
    "usa": "United States",
    "uk": "United Kingdom",
    "eu": "European Union",
    "ussr": "Soviet Union",
    "nasa": "NASA",
    "un": "United Nations",
    "us": "United States",
}


def _should_query_wikipedia(query: str) -> bool:
    """Heuristic: is this query worth a Wikipedia lookup?"""
    q = query.strip().lower()
    if len(q) < 3: # Increased from 2 to 3 to avoid 2-letter noise (unless alias)
        # Check if it is a known alias like "ai" or "ux"
        if q not in _ALIASES:
            return False
            
    word_count = len(q.split())
    if word_count > 6:
        return False
    if any(c in q for c in _SKIP_CHARS):
        return False
    if any(q.startswith(s) for s in _SKIP_STARTS):
        return False
    if q in _SKIP_EXACT:
        return False
        
    # Skip pure URL-like input
    if re.search(r"https?://|www\.", q):
        return False
    # Skip if looks like a math expression
    if re.match(r"^[\d\s\+\-\*\/\^\(\)\.]+$", q):
        return False
    # Skip file paths
    if "/" in q or "\\" in q:
        return False
        
    return True


def _classify_page_type(data: dict) -> str:
    """Classify the page as person / place / topic based on Wikipedia data."""
    description = (data.get("description") or "").lower()
    cats = data.get("categories", [])

    person_keywords = (
        "born", "died", "politician", "actor", "actress", "musician",
        "philosopher", "physicist", "mathematician", "scientist", "writer",
        "author", "artist", "composer", "director", "athlete", "player",
        "footballer", "singer", "rapper", "entrepreneur", "ceo", "founder",
    )
    place_keywords = (
        "city", "town", "village", "country", "capital", "municipality",
        "district", "province", "region", "county", "island", "river",
        "mountain", "lake", "ocean", "sea", "continent", "state", "republic",
    )
    if any(k in description for k in person_keywords):
        return "person"
    if any(k in description for k in place_keywords):
        return "place"
    return "topic"


def _fetch_summary(title: str) -> dict | None:
    """Call the Wikipedia REST summary endpoint for a title."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}"
    headers = {"User-Agent": "Omni/1.0 (desktop assistant; contact@example.com)"}
    try:
        r = requests.get(url, headers=headers, timeout=6)
        if r.status_code == 200:
            d = r.json()
            if d.get("type") == "disambiguation":
                return None
            thumb = d.get("thumbnail") or d.get("originalimage") or {}
            wiki_url = (
                d.get("content_urls", {}).get("desktop", {}).get("page")
                or f"https://en.wikipedia.org/wiki/{requests.utils.quote(title)}"
            )
            extract = d.get("extract", "")
            # Truncate to a readable length
            if len(extract) > 400:
                # Break at sentence boundary
                sentences = re.split(r"(?<=[.!?])\s+", extract)
                trimmed = ""
                for s in sentences:
                    if len(trimmed) + len(s) > 400:
                        break
                    trimmed += (" " if trimmed else "") + s
                extract = trimmed or extract[:400]

            return {
                "title": d.get("title", title),
                "description": d.get("description", ""),
                "extract": extract,
                "thumbnail": thumb.get("source", ""),
                "url": wiki_url,
                "page_type": _classify_page_type(d),
                "lang": "en",
            }
    except Exception as e:
        logging.debug(f"WikiWorker: summary fetch failed for '{title}': {e}")
    return None


def _search_wikipedia(query: str) -> dict | None:
    """Use Wikipedia search API to find the best matching article, then fetch summary."""
    search_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": 1,
        "srprop": "snippet",
    }
    headers = {"User-Agent": "Omni/1.0"}
    try:
        r = requests.get(search_url, params=params, headers=headers, timeout=5)
        if r.status_code == 200:
            results = r.json().get("query", {}).get("search", [])
            if results:
                best_title = results[0]["title"]
                return _fetch_summary(best_title)
    except Exception as e:
        logging.debug(f"WikiWorker: search failed for '{query}': {e}")
    return None


class WikiWorker(QThread):
    """Background worker that fetches a Wikipedia summary for the given query."""

    wiki_result = pyqtSignal(dict, str)   # (result_dict, original_query)
    no_result = pyqtSignal(str)            # original_query

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        query = self.query
        logging.info(f"WikiWorker: looking up '{query}'")

        if not _should_query_wikipedia(query):
            self.no_result.emit(query)
            return

        q_lower = query.strip().lower()

        # 1. Check alias table
        resolved = _ALIASES.get(q_lower)
        if resolved:
            result = _fetch_summary(resolved)
            if result:
                logging.info(f"WikiWorker: alias hit '{q_lower}' → '{resolved}'")
                self.wiki_result.emit(result, query)
                return

        # 2. Try exact query as Wikipedia title
        result = _fetch_summary(query.strip())
        if result:
            logging.info(f"WikiWorker: exact match for '{query}'")
            self.wiki_result.emit(result, query)
            return

        # 3. Fallback: Wikipedia search
        result = _search_wikipedia(query)
        if result:
            logging.info(f"WikiWorker: search match for '{query}' → '{result['title']}'")
            self.wiki_result.emit(result, query)
            return

        logging.info(f"WikiWorker: no result for '{query}'")
        self.no_result.emit(query)
