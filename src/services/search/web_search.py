import requests
import logging
from src.core.config import SEARXNG_URL
from src.services.system.location import get_system_location, get_ip_location
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Simple in-memory cache for navigation results (TTL: 5 minutes)
_nav_cache = {}
_cache_ttl = 300

# Common app/service mappings for instant lookup
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
    'pornhub': 'https://pornhub.com',
    'xvideos': 'https://xvideos.com',
    'amazon': 'https://amazon.com',
    'ebay': 'https://ebay.com',
    'google': 'https://google.com',
    'bing': 'https://bing.com',
    'chat': 'https://chatgpt.com',
    'chatgpt': 'https://chatgpt.com',
    'claude': 'https://claude.ai',
    'perplexity': 'https://perplexity.ai',
    'wikipedia': 'https://wikipedia.org',
}

def _get_cache_key(query, fast=False):
    return f"{query.lower()}_{fast}"

def _get_cached_nav(query, fast=False):
    """Get cached navigation result if available and not expired."""
    key = _get_cache_key(query, fast)
    if key in _nav_cache:
        cached_data, timestamp = _nav_cache[key]
        if time.time() - timestamp < _cache_ttl:
            logging.debug(f"Returning cached nav result for: '{query}'")
            return cached_data
        else:
            del _nav_cache[key]  # Expired
    return None

def _set_cache_nav(query, result, fast=False):
    """Cache navigation result."""
    key = _get_cache_key(query, fast)
    _nav_cache[key] = (result, time.time())

def _search_single_url(url, query, timeout, location='en'):
    """Search a single URL and return results."""
    try:
        if not url or not url.startswith("http"):
            return None
        
        params = {
            'q': query,
            'format': 'json',
            'categories': 'general',
            'language': location
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        }
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return data.get('results', [])
    except:
        pass
    return None

def search_api(query, categories='general', fast=False):
    """
    Performs a search using SearXNG (Local + Fallbacks).
    Returns a list of result dictionaries.
    When fast=True (action bar), uses parallel requests with very short timeout (0.8s).
    """
    loc = get_system_location()
    # Much shorter timeout for fast mode - we try multiple sources in parallel
    timeout = 0.8 if fast else 6.0

    # List of SearXNG instances to try
    # 1. Local (Priority)
    # 2. Public Fallbacks (in case local is down/not installed)
    urls = [SEARXNG_URL]
    fallback_urls = [
        "https://searx.be/search",
        "https://searx.daetalytica.io/search",
        "https://searx.tuxcloud.ua/search",
        "https://op.nx.is/search",
        "https://searx.web.cern.ch/search",
        "https://search.sapti.me/search",
        "https://searx.prvcy.eu/search",
        "https://searx.ng/search",
        "https://search.ononoki.org/search"
    ]
    urls.extend(fallback_urls)
    if fast:
        urls = urls[:3]  # Try local + 2 fallbacks in parallel
        # Use ThreadPoolExecutor to try multiple sources in parallel
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_search_single_url, url, query, timeout, loc) for url in urls]
            for future in as_completed(futures):
                try:
                    results = future.result()
                    if results:
                        if fast:
                            logging.debug(f"Fast search got results for: '{query}'")
                        return results
                except:
                    pass
        return []

    # Non-fast mode: sequential search (existing behavior)
    for url in urls:
        try:
            # Skip invalid URLs (e.g. if config is empty)
            if not url or not url.startswith("http"): continue

            if fast:
                logging.debug(f"Searching {url} for: '{query}' (fast)")
            else:
                logging.info(f"Searching {url} for: '{query}' (Loc: {loc}, Cats: {categories})")
            params = {
                'q': query,
                'format': 'json',
                'categories': categories,
                'language': loc
            }
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': url
            }
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('results', [])
                # If we got a valid response (even empty), we return it.
                # But if it's empty, maybe we should try next mirror? 
                # No, empty means no results found for query.
                return results
            else:
                if fast:
                    logging.debug(f"Search failed at {url} with status {resp.status_code}")
                else:
                    logging.warning(f"Search failed at {url} with status {resp.status_code}")
        except Exception as e:
            if fast:
                logging.debug(f"Search API Error ({url}): {e}")
            else:
                logging.error(f"Search API Error ({url}): {e}")
            continue
            
    return []

def perform_web_search(query):
    logging.info(f"Performing SearXNG Search for: {query}")
    try:
        # Check for Map/Location intent
        map_triggers = ["nearest", "find", "locate", "where is", "directions to"]
        is_map_query = any(x in query.lower() for x in map_triggers)
        
        categories = 'general'
        search_query = query

        # Check for Video/Music intent
        video_triggers = ["video", "watch", "youtube", "trailer", "movie", "clip", "music", "song", "listen"]
        is_video_query = any(t in query.lower() for t in video_triggers)

        if is_map_query:
            categories = 'map'
            # Clean query: Remove "find nearest" etc to help SearXNG
            clean_q = query.lower()
            for t in map_triggers:
                clean_q = clean_q.replace(t, "")
            clean_q = clean_q.strip()
            
            # Append City to query for better results
            loc_str = get_ip_location() # "City, Region, Country"
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
            # Fallback to general if map fails
            logging.info("Map search return 0 results. Fallback to GENERAL.")
            results = search_api(search_query, 'general')
        
        if not results and categories == 'videos':
            # Fallback to general
            results = search_api(search_query, 'general')

        if not results:
            logging.warning("SearXNG returned NO results.")
            return "No search results found."

        text_res = []
        for i, res in enumerate(results):
            if i >= 5: break # More results for maps
            title = res.get('title', 'No Title')
            url = res.get('url', ' ')
            
            # Map results specific fields
            address = res.get('address') or res.get('content')
            if isinstance(address, dict): # Sometimes address is a dict
                road = address.get('road', '')
                town = address.get('locality', '')
                address = f"{road}, {town}".strip(", ")
            
            content = address or res.get('content', ' '.strip()) or res.get('snippet', ' '.strip())
            
            if content:
                label = "Location" if categories == 'map' else "Title"
                info_label = "Address/Info" if categories == 'map' else "Description"
                text_res.append(f"{label}: {title}\n{info_label}: {content}\nURL: {url}")
            
            # Map Image Injection
            lat = res.get('latitude')
            lon = res.get('longitude')
            if lat and lon:
                # Use a public OSM static map service
                static_map = f"https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=16&size=600x300&maptype=mapnik"
                text_res.append(f"Map Image URL: {static_map}")

        final_context = "\n\n".join(text_res)
        logging.info(f"Context passed to LLM:\n{final_context}")
        return final_context
    except Exception as e:
        return f"Search failed: {str(e)}"

def get_navigation_result(query, fast=False):
    # Check for common apps first (instant!)
    query_lower = query.lower().strip()
    if query_lower in COMMON_APPS:
        result = {
            "url": COMMON_APPS[query_lower],
            "title": query.title(),
            "description": f"Official {query.title()} website",
            "is_likely_app": True
        }
        logging.info(f"Found common app: {query}")
        _set_cache_nav(query, result, fast)
        return result
    
    # Check cache next
    cached = _get_cached_nav(query, fast)
    if cached is not None:
        return cached
    
    try:
        # Fetch more results to allow ranking. fast=True: short timeout, few URLs (for action bar).
        results = search_api(query, categories='general', fast=fast)
        
        if not results:
            _set_cache_nav(query, None, fast)
            return None
        
        # Ranking Logic
        best_score = -1
        best_res = None
        
        normalized_query = query.lower().strip()
        
        # penalize generic info sites unless explicitly asked for
        info_sites = ["wikipedia.org", "wiktionary.org", "fandom.com", "dictionary.com", "britannica.com"]
        is_info_query = any(x in normalized_query for x in ["wiki", "define", "meaning", "what is"])
        
        for res in results[:5]: # Check top 5
            url = res.get('url', '').lower()
            title = res.get('title', '').lower()
            score = 0
            
            # Base score: Position (earlier is better, but only slightly)
            # We want relevance to override position
            
            # 1. Official Validation (Domain matches query)
            # e.g. query "whatsapp" matches "whatsapp.com"
            domain_match = False
            if f"://{normalized_query}." in url or f".{normalized_query}." in url:
                score += 50
                domain_match = True
            
            # 2. Title Match
            if res.get('title', '').lower().startswith(normalized_query):
                score += 10
            
            # 3. Penalize Info Sites (if not asked for)
            if not is_info_query and any(site in url for site in info_sites):
                score -= 30
                
            # 4. Boost "Home" or "Official" pages
            if "official" in title or "home" in title:
                score += 5
            
            # Keep track of best
            if score > best_score:
                best_score = score
                best_res = res
        
        # Fallback to first if ranking didn't find a clear winner (or all negative)
        if not best_res and results:
            best_res = results[0]
        
        # Refined App Detection Logic:
        # Merely matching domain is not enough (e.g. tesla.com).
        # We need affirmative "software" signals in the title or snippet.
        is_app = False
        if best_score >= 20: # It is a relevant/official site
            text = (best_res.get('title', '') + " " + best_res.get('content', '') + " " + best_res.get('snippet', '')).lower()
            
            # Positive Signals
            app_keywords = [
                "download", "install", " get ", "software", "app", "desktop", "client", 
                "browser", "messenger", "chat", "ide ", "editor", "player", "game", 
                "protect", "antivirus", "vpn", "driver", "suite", "tool", "platform",
                "terminal", "compiler", "runtime", "sdk", "cli "
            ]
            
            # Weak Signals (require domain match)
            if any(k in text for k in app_keywords):
                is_app = True
            
            # Explicit exclusions for common non-app official sites
            neg_keywords = ["car ", "vehicle", "energy", "recipe", "hotel", "bank ", "news", "university", "resort"]
            if any(k in text for k in neg_keywords):
                is_app = False
                
        result = {
            "url": best_res.get('url'),
            "title": best_res.get('title', 'Link'),
            "description": best_res.get('content') or best_res.get('snippet', ' '.strip()),
            "is_likely_app": is_app
        }
        
        # Cache the result
        _set_cache_nav(query, result, fast)
        return result
    except Exception as e:
        logging.error(f"Nav Error: {e}")
    
    # Cache the None result too
    _set_cache_nav(query, None, fast)
    return None

def get_person_result(name):
    # 1. Try Wikipedia API first (Better images/summaries)
    try:
        wiki_name = name.strip().replace(" ", "_")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_name}"
        headers = {"User-Agent": "OmniOS/1.0"}
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code == 200:
            data = r.json()
            if data.get('type') == 'standard':
                logging.info(f"Wikipedia found person: {data.get('title')}")
                return {
                    "type": "person",
                    "name": data.get('title', name),
                    "description": data.get('extract', ' '),
                    "url": data.get('content_urls', {}).get('desktop', {}).get('page', ''),
                    "image": data.get('thumbnail', {}).get('source')
                }
    except Exception as e: 
        logging.warning(f"Wiki Person Error: {e}")

    # 2. Fallback: SearXNG
    try:
        results = search_api(name, categories='general')
        if results:
            best = results[0]
            # Try to find an image in the result if possible
            img = best.get('thumbnail') or best.get('img_src') or best.get('image')
            
            return {
                "type": "person",
                "name": best.get('title', name),
                "description": best.get('content') or best.get('snippet', ''),
                "url": best.get('url'),
                "image": img
            }
    except Exception as e: pass

    return None

def get_place_result(query):
    try:
        results = search_api(query, categories='map')
        if results:
            best = results[0]
            return {
                "type": "place",
                "name": best.get('title', query),
                "address": best.get('content', '') or best.get('address', {}).get('road', ''),
                "latitude": best.get('latitude'),
                "longitude": best.get('longitude'),
                "url": best.get('url'),
                "image": None
            }
    except: pass
    return None
