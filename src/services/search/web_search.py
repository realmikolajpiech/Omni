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


_local_client = httpx.Client(
    timeout=3.0,
    follow_redirects=True
)

# ---------------------------------------------------------------------------
# Result cache -- avoids duplicate API calls for the same query
# ---------------------------------------------------------------------------
_search_cache: dict[str, tuple[list, float]] = {}
_SEARCH_CACHE_TTL = 600  # seconds — 10 min reuse window

def _cache_key(query: str, categories: str) -> str:
    return f"{query.lower().strip()}|{categories}"

def _get_cached(query: str, categories: str):
    key = _cache_key(query, categories)
    now = time.time()

    # Exact match
    if key in _search_cache:
        results, ts = _search_cache[key]
        if now - ts < _SEARCH_CACHE_TTL:
            return results
        del _search_cache[key]

    # Prefix match: "apple stock" reuses cached "apple" results — but only if the cached
    # key is long enough (≥5 chars) to avoid false matches like "bar" → "barcelona".
    q_lower = query.lower().strip()
    for cached_key, (results, ts) in list(_search_cache.items()):
        if now - ts >= _SEARCH_CACHE_TTL:
            continue
        cached_q, _, cached_cat = cached_key.partition("|")
        if cached_cat != categories:
            continue
        if (q_lower.startswith(cached_q)
                and len(cached_q) >= 5
                and 0 < len(q_lower) - len(cached_q) <= 10):
            logging.info(f"Search cache prefix hit: '{q_lower}' matched cached '{cached_q}'")
            return results

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
# Web search via Omni Worker backend (Tavily)
# ---------------------------------------------------------------------------
_SEARCH_TYPE_MAP = {
    'general': '/search',
    'images': '/images',
    'videos': '/videos',
    'news': '/news',
    'map': '/maps',
}

_MAX_RETRIES = 2  # total attempts = _MAX_RETRIES (first try + 1 retry)

def _web_search(query: str, categories: str = 'general', count: int = 5, fast: bool = False) -> list:
    """
    Search via the Omni Worker backend (which forwards to Tavily).
    The real API key lives on the Worker — never in the app binary.
    """
    endpoint = _SEARCH_TYPE_MAP.get(categories, '/search')
    loc      = get_search_locale()
    timeout  = 3.0 if fast else 5.0

    payload = {
        "_endpoint": endpoint,
        "_fast":     fast,
        "q":   query,
        "num": count,
        "gl":  loc.split('-')[-1].lower() if loc and '-' in loc else "us",
        "hl":  loc.split('-')[0].lower() if loc and '-' in loc else (loc[:2] if loc else "en"),
    }

    extra_headers = {}
    try:
        from src.core import auth as _auth
        token = _auth.get_access_token()
        if token:
            extra_headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass

    data = None
    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            target_url = f"{_backend_client.base_url}v1/search"
            logging.warning(f"[SEARCH] >>> POST {target_url}  q={payload['q']!r}  fast={fast}  attempt={attempt+1}/{_MAX_RETRIES}")
            r = _backend_client.post("/v1/search", json=payload, timeout=timeout, headers=extra_headers)
            logging.warning(f"[SEARCH] <<< status={r.status_code}  body_len={len(r.text)}")
            r.raise_for_status()
            data = r.json()
            logging.warning(f"[SEARCH] JSON keys={list(data.keys())}  organic_count={len(data.get('organic', []))}")

            # If we got results, break out of retry loop
            if data.get('organic') or data.get('places') or data.get('images'):
                break
            # Empty result — retry if we have attempts left
            logging.warning(f"[SEARCH] Empty results on attempt {attempt+1}, retrying...")
        except Exception as e:
            last_err = e
            logging.warning(f"[SEARCH] Attempt {attempt+1} failed ({type(e).__name__}): {e}")

    if data is None:
        logging.warning(f"[SEARCH] !!! ALL ATTEMPTS FAILED: {last_err}")
        return []

    # Normalize response into uniform dicts
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
# Unified search_api -- Tavily via Worker, with cache
# ---------------------------------------------------------------------------
def search_api(query: str, categories: str = 'general', fast: bool = False) -> list:
    """
    Performs a web search via the Omni Worker backend (Tavily).
    """
    cached = _get_cached(query, categories)
    if cached is not None:
        logging.info(f"Search cache hit for: '{query}' [{categories}]")
        return cached

    t0 = time.time()

    results = _web_search(query, categories, fast=fast)
    dt = time.time() - t0
    logging.info(f"Search ({'fast' if fast else 'main'}): {len(results)} results for '{query}' in {dt:.3f}s")

    if not results:
        logging.warning(f"Search returned 0 results for '{query}'")

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

        results = search_api(search_query, categories, fast=True)

        if not results and categories == 'map':
            logging.info("Map search returned 0 results. Fallback to GENERAL.")
            results = search_api(search_query, 'general', fast=True)

        if not results and categories == 'videos':
            results = search_api(search_query, 'general', fast=True)

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


def _geocode_nominatim(query: str):
    """Get lat/lon from Nominatim (OpenStreetMap) — free, no API key needed."""
    try:
        import urllib.parse
        r = _local_client.get(
            f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote_plus(query)}&format=json&limit=1",
            headers={"User-Agent": "OmniApp/1.0 (contact@omni.app)"},
            timeout=3.0,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        logging.debug(f"Nominatim geocoding failed for '{query}': {e}")
    return None, None


def get_place_result(query, existing_results=None):
    try:
        results = []
        if existing_results:
            results = existing_results

        has_geo = any(r.get('latitude') for r in results)

        if not results:
            # No existing results — run Nominatim + general search in parallel
            # (faster than a sequential map search which goes through the backend)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _pool:
                _nom_f = _pool.submit(_geocode_nominatim, query)
                _gen_f = _pool.submit(search_api, query, 'general', True)
                try:
                    nom_lat, nom_lon = _nom_f.result(timeout=4.0)
                except Exception:
                    nom_lat, nom_lon = None, None
                try:
                    gen_results = _gen_f.result(timeout=4.0)
                except Exception:
                    gen_results = []

            if nom_lat and nom_lon and gen_results:
                best = gen_results[0]
                raw_desc = best.get('content', '') or ''
                if isinstance(raw_desc, str) and len(raw_desc) > 160:
                    cut = raw_desc[:160]
                    last_dot = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
                    raw_desc = (cut[:last_dot + 1] if last_dot > 40 else cut).rstrip(' ,;')
                logging.info(f"[PLACE] Parallel fast-path for '{query}': lat={nom_lat}, lon={nom_lon}")
                return {
                    "type": "place",
                    "name": best.get('title', query),
                    "address": raw_desc,
                    "latitude": nom_lat,
                    "longitude": nom_lon,
                    "url": best.get('url'),
                    "rating": None,
                    "rating_count": None,
                    "image": best.get('img_src') or best.get('thumbnail'),
                    "category": None,
                    "phone": None,
                    "hours": None,
                }
            elif gen_results:
                results = gen_results
            else:
                # Both failed — fall back to map search
                map_results = search_api(query, categories='map')
                if map_results:
                    results = map_results
        elif not has_geo:
            # We already have general results but no coordinates.
            # Try Nominatim first (~300ms) to get coordinates without a full map search.
            nom_lat, nom_lon = _geocode_nominatim(query)
            if nom_lat and nom_lon:
                logging.info(f"[PLACE] Nominatim fast-path for '{query}': lat={nom_lat}, lon={nom_lon}")
                best = results[0]
                raw_desc = best.get('content', '') or ''
                if isinstance(raw_desc, str) and len(raw_desc) > 160:
                    cut = raw_desc[:160]
                    last_dot = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
                    raw_desc = (cut[:last_dot + 1] if last_dot > 40 else cut).rstrip(' ,;')
                return {
                    "type": "place",
                    "name": best.get('title', query),
                    "address": raw_desc,
                    "latitude": nom_lat,
                    "longitude": nom_lon,
                    "url": best.get('url'),
                    "rating": None,
                    "rating_count": None,
                    "image": best.get('img_src') or best.get('thumbnail'),
                    "category": None,
                    "phone": None,
                    "hours": None,
                }
            else:
                # Nominatim failed — fall back to map search for rich data
                map_results = search_api(query, categories='map')
                if map_results:
                    results = map_results

        if results:
            best = results[0]
            # Prefer a result that already has coordinates
            for r in results:
                if r.get('latitude') and r.get('longitude'):
                    best = r
                    break

            lat = best.get('latitude')
            lon = best.get('longitude')

            # If still no coordinates, try Nominatim geocoding as a last resort
            if not lat or not lon:
                lat, lon = _geocode_nominatim(query)
                logging.info(f"[PLACE] Nominatim geocoding for '{query}': lat={lat}, lon={lon}")

            # Image: prefer map thumbnail (skip separate image search to save API calls)
            image_url = best.get('thumbnail')

            # Keep address/description short — 1-2 sentences max
            raw_desc = best.get('content', '') or best.get('address', {}).get('road', '') or ''
            if isinstance(raw_desc, str) and len(raw_desc) > 160:
                # Cut at sentence boundary within first 160 chars
                cut = raw_desc[:160]
                last_dot = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
                raw_desc = (cut[:last_dot + 1] if last_dot > 40 else cut).rstrip(' ,;')

            return {
                "type": "place",
                "name": best.get('title', query),
                "address": raw_desc,
                "latitude": lat,
                "longitude": lon,
                "url": best.get('url'),
                "rating": best.get('rating'),
                "rating_count": best.get('ratingCount'),
                "image": image_url,
                "category": best.get('category'),
                "phone": best.get('phoneNumber'),
                "hours": best.get('openingHours')
            }
        else:
            logging.warning(f"Place search failed: No results found at all for '{query}'")
    except Exception as e:
        logging.error(f"Place search error: {e}", exc_info=True)
    return None
