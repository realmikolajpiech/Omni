import logging
import time
import concurrent.futures

import httpx

from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID
from src.services.system.location import get_system_location, get_ip_location, get_search_locale

# ---------------------------------------------------------------------------
# Persistent HTTP clients
# ---------------------------------------------------------------------------
# All search requests go through the Omni Worker backend — no API keys in-app.
_backend_client = httpx.Client(
    base_url=BACKEND_URL,
    timeout=4.0,
    headers={
        "X-Omni-Secret": OMNI_SECRET,
        "X-Device-ID":   DEVICE_ID,
        "Content-Type":  "application/json",
    },
)

# Keep a slow alias for non-fast calls (same client, different timeout passed per-call)
_serper_main_client = _backend_client
_serper_fast_client = _backend_client

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
    Search via the Omni Worker backend (which forwards to Serper.dev).
    The real Serper API key lives on the Worker — never in the app binary.
    """
    endpoint = _SERPER_TYPE_MAP.get(categories, '/search')
    loc      = get_search_locale()
    timeout  = 2.0 if fast else 4.0

    payload = {
        "_endpoint": endpoint,   # tells the Worker which Serper endpoint to hit
        "_fast":     fast,       # tells the Worker which API key to use
        "q":   query,
        "num": count,
        "gl":  loc.split('-')[-1].lower() if loc and '-' in loc else "us",
        "hl":  loc.split('-')[0].lower() if loc and '-' in loc else (loc[:2] if loc else "en"),
    }

    try:
        # Attach JWT if user is logged in (backend requires it for authenticated endpoints)
        extra_headers = {}
        try:
            from src.core import auth as _auth
            token = _auth.get_access_token()
            if token:
                extra_headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass

        target_url = f"{_backend_client.base_url}v1/search"
        logging.warning(f"[SEARCH] >>> POST {target_url}  q={payload['q']!r}  fast={fast}  timeout={timeout}s")
        r = _backend_client.post("/v1/search", json=payload, timeout=timeout, headers=extra_headers)
        logging.warning(f"[SEARCH] <<< status={r.status_code}  body_len={len(r.text)}  body={r.text!r}")
        r.raise_for_status()
        data = r.json()
        logging.warning(f"[SEARCH] JSON keys={list(data.keys())}  organic_count={len(data.get('organic', []))}")

        # Validate that Serper actually searched for the right query.
        # Sometimes the response contains results for a truncated/different query.
        returned_q = (data.get('searchParameters', {}).get('q') or '').strip().lower()
        sent_q = query.strip().lower()
        if returned_q and sent_q and returned_q != sent_q and not sent_q.startswith(returned_q[:3]):
            # Mismatch but the returned query is a prefix of what we sent — Serper truncated it.
            # Only retry if the returned query is significantly shorter (not just a minor normalization).
            pass  # fall through, check below
        if returned_q and sent_q and len(returned_q) < len(sent_q) * 0.6 and returned_q != sent_q:
            logging.warning(f"[SEARCH] Query mismatch! Sent q={sent_q!r} but got results for q={returned_q!r}. Retrying...")
            # Retry once with a fresh request
            r2 = _backend_client.post("/v1/search", json=payload, timeout=timeout, headers=extra_headers)
            r2.raise_for_status()
            data2 = r2.json()
            returned_q2 = (data2.get('searchParameters', {}).get('q') or '').strip().lower()
            logging.warning(f"[SEARCH] Retry got q={returned_q2!r}")
            if returned_q2 and len(returned_q2) >= len(returned_q):
                data = data2  # Use retry result if it's at least as good
    except Exception as e:
        logging.warning(f"[SEARCH] !!! FAILED ({type(e).__name__}): {e}")
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
        # General Search Normalization
        # 1. Knowledge Graph (right-side box in Google)
        kg = data.get('knowledgeGraph', {})
        if kg:
            # If we have a knowledge graph, it's often the best "single truth" result.
            # We inject it as the first result with high priority.
            results.append({
                'title': kg.get('title', ''),
                'url': kg.get('website', ''),
                'content': kg.get('description', '') + (' ' + kg.get('type', '') if kg.get('type') else ''),
                'img_src': kg.get('imageUrl', ''),
                'attributes': kg.get('attributes', {}), # e.g. Born, Spouse, Education
                'is_knowledge_graph': True
            })

        # 2. Organic Results
        for item in data.get('organic', [])[:count]:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('link', ''),
                'content': item.get('snippet', ''),
            })

    logging.warning(f"[SEARCH] normalized {len(results)} results for category={categories!r}")
    if results:
        logging.warning(f"[SEARCH] first result: title={results[0].get('title')!r}  url={results[0].get('url')!r}")
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
    logging.warning(f"[PERFORM_SEARCH] query={query!r}")
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
            logging.warning(f"[PERFORM_SEARCH] NO RESULTS for {query!r} (category={categories!r})")
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
        logging.warning(f"[PERFORM_SEARCH] returning {len(final_context)} chars: {final_context[:200]!r}")
        return final_context
    except Exception as e:
        msg = f"Search failed: {str(e)}"
        logging.warning(f"[PERFORM_SEARCH] EXCEPTION → returning {len(msg)} chars: {msg!r}")
        return msg


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
        
        # Helper to extract domain part
        from urllib.parse import urlparse
        
        info_sites = ["wikipedia.org", "wiktionary.org", "fandom.com", "dictionary.com", "britannica.com", "imdb.com", "filmweb.pl", "rotten tomatoes"]
        is_info_query = any(x in normalized_query for x in ["wiki", "define", "meaning", "what is"])

        for res in results[:5]:
            url = res.get('url', '').lower()
            title = res.get('title', '').lower()
            score = 0
            
            try:
                parsed = urlparse(url)
                netloc = parsed.netloc.replace("www.", "")
                # strict domain match: "z.ai" -> "z", "facebook.com" -> "facebook"
                domain_parts = netloc.split('.')
                domain_root = domain_parts[-2] if len(domain_parts) >= 2 else domain_parts[0]
                
                # Check for exact domain match (high confidence)
                # e.g. query="z", domain="z.ai" -> match
                # e.g. query="facebook", domain="facebook.com" -> match
                if domain_root == normalized_query:
                    score += 60
                elif normalized_query in domain_root:
                    # partial match (e.g. query="face", domain="facebook.com")
                    score += 10
            except:
                pass

            if res.get('title', '').lower().startswith(normalized_query):
                score += 10

            if not is_info_query and any(site in url for site in info_sites):
                score -= 30

            if "official" in title or "home" in title or "strona główna" in title:
                score += 5

            if score > best_score:
                best_score = score
                best_res = res

        # Strict thresholds
        # If query is short (<= 3 chars), we require a very high score (exact domain match)
        # Otherwise, we require a moderate score to avoid random "I feel lucky" results
        min_threshold = 50 if len(normalized_query) <= 3 else 20
        
        if best_score < min_threshold:
            # If we didn't meet the threshold, do NOT return a navigation result.
            # This allows the caller to fall back to a generic "Search Google" action.
            _set_cache_nav(query, None, fast)
            return None

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
        if existing_results:
            results = existing_results
            logging.info(f"Using {len(results)} existing results for person fallback")
            # Don't do a separate image search to save time.
            # We will try to extract an image from the existing results if possible,
            # or just return the card without one. The UI handles missing images gracefully.
        else:
            # If we must search, just do ONE general search. 
            # We avoid the parallel image search to reduce latency.
            results = search_api(name, categories='general')

        if results:
            best = results[0]
            
            # Prefer knowledge graph result if available
            kg_result = next((r for r in results if r.get('is_knowledge_graph')), None)
            if kg_result:
                best = kg_result
                logging.info(f"Using Knowledge Graph result for person: {best.get('title')}")

            title = best.get('title', name)
            # Improved cleaning: split by common separators but allow unicode characters
            # Specifically handle " – " (en dash) and " - " (hyphen) and " | "
            name_clean = title.split(' – ')[0].split(' - ')[0].split(' | ')[0].split('(')[0].strip()
            
            description = (best.get('content') or best.get('snippet', '')).strip()
            
            # Append KG attributes to description if available (e.g. "Born: 1990. Spouse: Jane Doe")
            if best.get('attributes'):
                attr_str = " ".join([f"{k}: {v}." for k, v in best.get('attributes').items()])
                if attr_str:
                    description += f" {attr_str}"

            if description.endswith('...'):
                description = description[:-3].strip()
            description = description[:400] # Increased limit slightly for richer KG data

            # Try to find an image in the general results first
            image_url = best.get('img_src') or best.get('thumbnail') or best.get('image')
            
            # If no image in first result, check others briefly
            if not image_url:
                for r in results[:5]: # Check slightly more results
                    potential = r.get('img_src') or r.get('thumbnail') or r.get('image')
                    if potential:
                        image_url = potential
                        break

            result = {
                "type": "person",
                "name": name,  # Trust the name passed in (which may be from the LLM or cleaned by the caller)
                "description": description,
                "url": best.get('url'),
                "image": image_url
            }

            if image_url:
                logging.info(f"Found image for {name_clean} in search results: {image_url[:80]}")
            else:
                # If no image found immediately, don't block.
                # The UI will handle a missing image gracefully (placeholder).
                # We skip the secondary image search to keep response fast.
                logging.info(f"No image in general results for {name_clean}, skipping dedicated image search to return fast.")
            
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
