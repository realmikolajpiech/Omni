import requests
import logging
from src.core.config import SEARXNG_URL
from src.services.system.location import get_system_location, get_ip_location
import time

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

def search_api(query, categories='general', fast=False):
    """
    Performs a search using ONLY local SearXNG.
    When fast=True (action bar), uses shorter timeout but still reliable.
    """
    loc = get_system_location()
    # Give local SearXNG adequate time to respond
    timeout = 3.0 if fast else 6.0

    try:
        if fast:
            logging.info(f"Fast search: '{query}' (timeout={timeout}s)")
        else:
            logging.info(f"Standard search: '{query}' (Loc: {loc})")
        
        params = {
            'q': query,
            'format': 'json',
            'categories': categories,
            'language': loc
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        }
        
        # Only try local SearXNG
        r = requests.get(SEARXNG_URL, params=params, headers=headers, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            results = data.get('results', [])
            logging.info(f"Search got {len(results)} results for: '{query}'")
            return results
        else:
            logging.error(f"SearXNG returned status {r.status_code} for: '{query}'")
            return []
    
    except requests.Timeout:
        logging.error(f"SearXNG TIMEOUT for: '{query}' (timeout={timeout}s) - ensure SearXNG is running on port 8888")
        return []
    except ConnectionError as e:
        logging.error(f"Cannot connect to local SearXNG at {SEARXNG_URL} - is it running? Error: {e}")
        return []
    except Exception as e:
        logging.error(f"Search Error: {e}")
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
        # Fetch results from LOCAL SearXNG only
        results = search_api(query, categories='general', fast=fast)
        
        if not results:
            logging.error(f"No search results found for: '{query}' - SearXNG may not be running")
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
        
        logging.info(f"Navigation result for '{query}': {result['url']} (is_app={is_app}, fast={fast})")
        
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
                    "description": data.get('extract', '').strip()[:300],  # Clean description
                    "url": data.get('content_urls', {}).get('desktop', {}).get('page', ''),
                    "image": data.get('thumbnail', {}).get('source')
                }
    except Exception as e: 
        logging.warning(f"Wiki Person Error: {e}")

    # 2. Fallback: SearXNG general search
    try:
        results = search_api(name, categories='general')
        if results:
            best = results[0]
            # Extract name - remove common suffixes
            title = best.get('title', name)
            # Clean up title - remove " - Wikipedia", " | ...", etc.
            name_clean = title.split(' - ')[0].split(' | ')[0].split('(')[0].strip()
            
            # Clean description - remove trailing "..."
            description = (best.get('content') or best.get('snippet', '')).strip()
            if description.endswith('...'):
                description = description[:-3].strip()
            description = description[:300]  # Limit length
            
            result = {
                "type": "person",
                "name": name_clean or name,
                "description": description,
                "url": best.get('url'),
                "image": None
            }
            
            # Try to get image from image search (use original query, not long title)
            logging.info(f"Searching for image of: {name}")
            try:
                img_results = search_api(name, categories='images')
                if img_results:
                    img_url = img_results[0].get('img_src') or img_results[0].get('thumbnail') or img_results[0].get('url')
                    if img_url:
                        result['image'] = img_url
                        logging.info(f"Found image for {name_clean}: {img_url[:80]}")
            except:
                pass
            
            return result
    except Exception as e: 
        logging.warning(f"Person search error: {e}")

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
