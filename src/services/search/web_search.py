import logging
import time
import concurrent.futures

import httpx

from src.core.config import SERPER_MAIN_API_KEY, SERPER_FAST_API_KEY
from src.services.system.location import get_system_location, get_ip_location, get_search_locale

# ---------------------------------------------------------------------------
# Persistent HTTP clients (connection reuse, HTTP/2 for Serper)
# ---------------------------------------------------------------------------
_serper_main_client = httpx.Client(
    base_url="https://google.serper.dev",
    timeout=4.0,
    http2=True,
) if SERPER_MAIN_API_KEY else None

_serper_fast_client = httpx.Client(
    base_url="https://google.serper.dev",
    timeout=2.0,
    http2=True,
) if SERPER_FAST_API_KEY else None

_local_client = httpx.Client(
    timeout=3.0,
    follow_redirects=True
)

# ---------------------------------------------------------------------------
# Result cache -- avoids duplicate API calls for the same query
# ---------------------------------------------------------------------------
_search_cache: dict[str, tuple[list, float]] = {}
_SEARCH_CACHE_TTL = 120  # seconds

def _cache_key(query: str, categories: str) -> str:
    return f"{query.lower().strip()}|{categories}"

def _get_cached(query: str, categories: str):
    key = _cache_key(query, categories)
    if key in _search_cache:
        results, ts = _search_cache[key]
        if time.time() - ts < _SEARCH_CACHE_TTL:
            return results
        del _search_cache[key]
    return None

def _set_cached(query: str, categories: str, results: list):
    _search_cache[_cache_key(query, categories)] = (results, time.time())

# ---------------------------------------------------------------------------
# Navigation cache (longer TTL for nav lookups)
# ---------------------------------------------------------------------------
_nav_cache: dict[str, tuple] = {}
_NAV_CACHE_TTL = 300

COMMON_APPS = {
    'spotify': 'https://spotify.com',
    'youtube': 'https://youtube.com',
    'netflix': 'https://netflix.com',
    'instagram': 'https://instagram.com',
    'facebook': 'https://facebook.com',
    'twitter': 'https://twitter.com',
    'x': 'https://x.com',
    'tiktok': 'https://tiktok.com',
    'discord': 'https://discord.com',
    'twitch': 'https://twitch.tv',
    'reddit': 'https://reddit.com',
    'gmail': 'https://gmail.com',
    'github': 'https://github.com',
    'linkedin': 'https://linkedin.com',
    'whatsapp': 'https://whatsapp.com',
    'telegram': 'https://telegram.org',
    'slack': 'https://slack.com',
    'notion': 'https://notion.so',
}

def _get_cache_key(query, fast=False):
    return f"{query.lower()}_{fast}"

def _get_cached_nav(query, fast=False):
    key = _get_cache_key(query, fast)
    if key in _nav_cache:
        cached_data, timestamp = _nav_cache[key]
        if time.time() - timestamp < _NAV_CACHE_TTL:
            return cached_data
        else:
            del _nav_cache[key]
    return None

def _set_cache_nav(query, result, fast=False):
    key = _get_cache_key(query, fast)
    _nav_cache[key] = (result, time.time())


# ---------------------------------------------------------------------------
# Serper.dev search (primary -- fast Google results)
# ---------------------------------------------------------------------------
_SERPER_TYPE_MAP = {
    'general': '/search',
    'images': '/images',
    'videos': '/videos',
    'news': '/news',
    'map': '/maps', # Changed from /places to /maps for richer data (thumbnail, rating, etc.)
}

def _serper_search(query: str, categories: str = 'general', count: int = 5, fast: bool = False) -> list:
    """
    Hit Serper.dev and normalize results to the same shape as SearXNG
    (dicts with 'title', 'url', 'content' keys).
    Uses SERPER_FAST_API_KEY when fast=True, SERPER_MAIN_API_KEY otherwise.
    """
    if fast:
        client, api_key = _serper_fast_client, SERPER_FAST_API_KEY
    else:
        client, api_key = _serper_main_client, SERPER_MAIN_API_KEY

    if not client:
        return []

    endpoint = _SERPER_TYPE_MAP.get(categories, '/search')
    loc = get_search_locale()

    payload = {
        "q": query,
        "num": count,
        "gl": loc[:2] if loc else "us",
        "hl": loc[:2] if loc else "en"  # Use host language based on locale
    }

    try:
        r = client.post(
            endpoint,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logging.warning(f"Serper request failed: {e}")
        return []

    # Normalize different Serper response shapes into uniform dicts
    results = []

    if categories == 'map':
        for p in data.get('places', [])[:count]:
            results.append({
                'title': p.get('title', ''),
                'url': p.get('website') or p.get('link', ''),
                'content': p.get('address', ''),
                'latitude': p.get('latitude'),
                'longitude': p.get('longitude'),
                'rating': p.get('rating'),
                'ratingCount': p.get('ratingCount'),
                'thumbnail': p.get('thumbnailUrl'),
                'category': p.get('category') or p.get('type'),
                'phoneNumber': p.get('phoneNumber'),
                'openingHours': p.get('openingHours')
            })
    elif categories == 'images':
        for img in data.get('images', [])[:count]:
            results.append({
                'title': img.get('title', ''),
                'url': img.get('link', ''),
                'content': img.get('source', ''),
                'img_src': img.get('imageUrl', ''),
                'thumbnail': img.get('thumbnailUrl', ''),
            })
    elif categories == 'videos':
        for v in data.get('videos', [])[:count]:
            results.append({
                'title': v.get('title', ''),
                'url': v.get('link', ''),
                'content': v.get('snippet', ''),
            })
    else:
        for item in data.get('organic', [])[:count]:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('link', ''),
                'content': item.get('snippet', ''),
            })

    return results


# ---------------------------------------------------------------------------
# Unified search_api -- Serper only, with cache
# ---------------------------------------------------------------------------
def search_api(query: str, categories: str = 'general', fast: bool = False) -> list:
    """
    Performs a web search via Serper.dev (fast Google results).
    """
    cached = _get_cached(query, categories)
    if cached is not None:
        logging.info(f"Search cache hit for: '{query}' [{categories}]")
        return cached

    t0 = time.time()
    results = []

    if (fast and SERPER_FAST_API_KEY) or (not fast and SERPER_MAIN_API_KEY):
        results = _serper_search(query, categories, fast=fast)
        dt = time.time() - t0
        logging.info(f"Serper ({'fast' if fast else 'main'}): {len(results)} results for '{query}' in {dt:.3f}s")

    if not results:
        logging.warning(f"Serper returned 0 results for '{query}'")

    _set_cached(query, categories, results)
    return results


# ---------------------------------------------------------------------------
# perform_web_search -- formats results into text context for the LLM
# ---------------------------------------------------------------------------
def perform_web_search(query):
    logging.info(f"Performing web search for: {query}")
    try:
        map_triggers = ["nearest", "find", "locate", "where is", "directions to"]
        is_map_query = any(x in query.lower() for x in map_triggers)

        categories = 'general'
        search_query = query

        video_triggers = ["video", "watch", "youtube", "trailer", "movie", "clip", "music", "song", "listen"]
        is_video_query = any(t in query.lower() for t in video_triggers)

        if is_map_query:
            categories = 'map'
            clean_q = query.lower()
            for t in map_triggers:
                clean_q = clean_q.replace(t, "")
            clean_q = clean_q.strip()

            loc_str = get_ip_location()
            if loc_str != "Unknown Location":
                city = loc_str.split(',')[0].strip()
                if city.lower() not in clean_q:
                    search_query = f"{clean_q} {city}"
            else:
                search_query = clean_q
        elif is_video_query:
            categories = 'videos'
            search_query = query

        results = search_api(search_query, categories)

        if not results and categories == 'map':
            logging.info("Map search returned 0 results. Fallback to GENERAL.")
            results = search_api(search_query, 'general')

        if not results and categories == 'videos':
            results = search_api(search_query, 'general')

        if not results:
            logging.warning("Search returned NO results.")
            return "No search results found."

        text_res = []
        for i, res in enumerate(results):
            if i >= 5:
                break
            title = res.get('title', 'No Title')
            url = res.get('url', '')

            address = res.get('address') or res.get('content')
            if isinstance(address, dict):
                road = address.get('road', '')
                town = address.get('locality', '')
                address = f"{road}, {town}".strip(", ")

            content = address or res.get('content', '').strip() or res.get('snippet', '').strip()

            if content:
                label = "Location" if categories == 'map' else "Title"
                info_label = "Address/Info" if categories == 'map' else "Description"
                text_res.append(f"{label}: {title}\n{info_label}: {content}\nURL: {url}")

            lat = res.get('latitude')
            lon = res.get('longitude')
            if lat and lon:
                static_map = f"https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=16&size=600x300&maptype=mapnik"
                text_res.append(f"Map Image URL: {static_map}")

        final_context = "\n\n".join(text_res)
        logging.info(f"Context passed to LLM:\n{final_context}")
        return final_context
    except Exception as e:
        return f"Search failed: {str(e)}"


# ---------------------------------------------------------------------------
# Navigation result
# ---------------------------------------------------------------------------
def get_navigation_result(query, fast=False, existing_results=None):
    query_lower = query.lower().strip()
    if query_lower in COMMON_APPS:
        result = {
            "url": COMMON_APPS[query_lower],
            "title": query.title(),
            "description": f"Official {query.title()} website",
            "is_likely_app": True
        }
        _set_cache_nav(query, result, fast)
        return result

    cached = _get_cached_nav(query, fast)
    if cached is not None:
        return cached

    try:
        if existing_results:
            results = existing_results
            logging.info(f"Using {len(results)} existing results for navigation check")
        else:
            results = search_api(query, categories='general', fast=fast)

        if not results:
            _set_cache_nav(query, None, fast)
            return None

        best_score = -1
        best_res = None
        normalized_query = query.lower().strip()

        info_sites = ["wikipedia.org", "wiktionary.org", "fandom.com", "dictionary.com", "britannica.com"]
        is_info_query = any(x in normalized_query for x in ["wiki", "define", "meaning", "what is"])

        for res in results[:5]:
            url = res.get('url', '').lower()
            title = res.get('title', '').lower()
            score = 0

            domain_match = False
            if f"://{normalized_query}." in url or f".{normalized_query}." in url or f"/{normalized_query}." in url:
                score += 50
                domain_match = True
            elif url.split('://')[-1].startswith(normalized_query + '.'):
                score += 50
                domain_match = True

            if domain_match:
                if ".info" in url and not normalized_query.endswith("info"):
                    score -= 5
                if ".pl" in url:
                    score += 5

            if res.get('title', '').lower().startswith(normalized_query):
                score += 10

            if not is_info_query and any(site in url for site in info_sites):
                score -= 30

            if "official" in title or "home" in title or "strona główna" in title:
                score += 5

            if score > best_score:
                best_score = score
                best_res = res

        if not best_res and results:
            best_res = results[0]

        is_app = False
        if best_score >= 20:
            text = (best_res.get('title', '') + " " + best_res.get('content', '') + " " + best_res.get('snippet', '')).lower()
            app_keywords = [
                "download", "install", " get ", "software", "app", "desktop", "client",
                "browser", "messenger", "chat", "ide ", "editor", "player", "game",
                "protect", "antivirus", "vpn", "driver", "suite", "tool", "platform",
                "terminal", "compiler", "runtime", "sdk", "cli "
            ]
            if any(k in text for k in app_keywords):
                is_app = True
            neg_keywords = ["car ", "vehicle", "energy", "recipe", "hotel", "bank ", "news", "university", "resort"]
            if any(k in text for k in neg_keywords):
                is_app = False

        result = {
            "url": best_res.get('url'),
            "title": best_res.get('title', 'Link'),
            "description": best_res.get('content') or best_res.get('snippet', '').strip(),
            "is_likely_app": is_app
        }
        _set_cache_nav(query, result, fast)
        return result
    except Exception as e:
        logging.error(f"Nav Error: {e}")

    _set_cache_nav(query, None, fast)
    return None


# ---------------------------------------------------------------------------
# Person / Place results
# ---------------------------------------------------------------------------
def get_person_result(name, existing_results=None):
    # REMOVED: Direct Wikipedia API call. 
    # Reason: It was overriding better search results/LLM descriptions with raw Wiki summaries, 
    # often causing 403 errors or returning generic info.
    # We now rely purely on search results + LLM synthesis (handled in routes.py).

    try:
        results = []
        img_results = []
        if existing_results:
            results = existing_results
            logging.info(f"Using {len(results)} existing results for person fallback")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future_img = executor.submit(search_api, name, categories='images')
                img_results = future_img.result()
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_gen = executor.submit(search_api, name, categories='general')
                future_img = executor.submit(search_api, name, categories='images')
                results = future_gen.result()
                img_results = future_img.result()

        if results:
            best = results[0]
            title = best.get('title', name)
            name_clean = title.split(' - ')[0].split(' | ')[0].split('(')[0].strip()
            description = (best.get('content') or best.get('snippet', '')).strip()
            if description.endswith('...'):
                description = description[:-3].strip()
            description = description[:300]

            result = {
                "type": "person",
                "name": name_clean or name,
                "description": description,
                "url": best.get('url'),
                "image": None
            }

            if img_results:
                img_url = img_results[0].get('img_src') or img_results[0].get('thumbnail') or img_results[0].get('url')
                if img_url:
                    result['image'] = img_url
                    logging.info(f"Found image for {name_clean}: {img_url[:80]}")

            return result
    except Exception as e:
        logging.warning(f"Person search error: {e}")

    return None


def get_place_result(query, existing_results=None):
    try:
        results = []
        if existing_results:
            # Filter existing results for place-like content if possible, 
            # but usually 'map' category is better for coordinates.
            # If we have existing results, they are likely 'general'.
            # We might want to re-search with 'map' to get lat/lon if missing.
            results = existing_results
        
        # If no results or existing results don't look like places (no address/lat/lon),
        # force a map search.
        has_geo = any(r.get('latitude') for r in results)
        if not results or not has_geo:
             map_results = search_api(query, categories='map')
             if map_results:
                 results = map_results

        if results:
            best = results[0]
            # Try to find one with coordinates if the first doesn't have them
            for r in results:
                if r.get('latitude') and r.get('longitude'):
                    best = r
                    break
            
            # Use thumbnail from map result if available, otherwise try image search
            image_url = best.get('thumbnail')
            if not image_url:
                try:
                    # Quick image search
                    img_results = search_api(query, categories='images')
                    if img_results:
                         image_url = img_results[0].get('img_src') or img_results[0].get('thumbnail')
                except: pass

            return {
                "type": "place",
                "name": best.get('title', query),
                "address": best.get('content', '') or best.get('address', {}).get('road', ''),
                "latitude": best.get('latitude'),
                "longitude": best.get('longitude'),
                "url": best.get('url'),
                "rating": best.get('rating'),
                "rating_count": best.get('ratingCount'),
                "image": image_url,
                "category": best.get('category'),
                "phone": best.get('phoneNumber'),
                "hours": best.get('openingHours')
            }
    except Exception as e:
        logging.error(f"Place search error: {e}")
    return None
