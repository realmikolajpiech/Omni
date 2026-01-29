import requests
import logging
from src.core.config import SEARXNG_URL
from src.services.system.location import get_system_location, get_ip_location

def search_api(query, categories='general'):
    """
    Performs a search using SearXNG (Local + Fallbacks).
    Returns a list of result dictionaries.
    """
    loc = get_system_location()
    
    # List of SearXNG instances to try
    # 1. Local (Priority)
    # 2. Public Fallbacks (in case local is down/not installed)
    urls = [SEARXNG_URL]
    fallback_urls = [
        "https://searx.be/search",
        "https://searx.ng/search",
        "https://search.ononoki.org/search"
    ]
    urls.extend(fallback_urls)

    for url in urls:
        try:
            # Skip invalid URLs (e.g. if config is empty)
            if not url or not url.startswith("http"): continue

            logging.info(f"Searching {url} for: '{query}' (Loc: {loc}, Cats: {categories})")
            params = {
                'q': query,
                'format': 'json',
                'categories': categories,
                'language': loc
            }    
            resp = requests.get(url, params=params, timeout=6.0)
            
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('results', [])
                # If we got a valid response (even empty), we return it.
                # But if it's empty, maybe we should try next mirror? 
                # No, empty means no results found for query.
                return results
            else:
                logging.warning(f"Search failed at {url} with status {resp.status_code}")
        except Exception as e:
            # Connection errors, timeouts, etc.
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

def get_navigation_result(query):
    try:
        # Fetch more results to allow ranking
        results = search_api(query, categories='general')
        
        if not results: return None
        
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
                
        return {
            "url": best_res.get('url'),
            "title": best_res.get('title', 'Link'),
            "description": best_res.get('content') or best_res.get('snippet', ' '.strip()),
            "is_likely_app": is_app
        }
    except Exception as e:
        logging.error(f"Nav Error: {e}")
    return None

def get_person_result(name):
    try:
        # Try SearXNG first (via search_api)
        results = search_api(name, categories='general')
        if results:
            best = results[0]
            return {
                "type": "person",
                "name": best.get('title', name),
                "description": best.get('content') or best.get('snippet', ''),
                "url": best.get('url'),
                "image": None
            }
    except Exception as e: pass

    # Fallback: Wikipedia API
    try:
        wiki_name = name.strip().replace(" ", "_")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_name}"
        headers = {"User-Agent": "OmniOS/1.0"}
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code == 200:
            data = r.json()
            if data.get('type') == 'standard':
                return {
                    "type": "person",
                    "name": data.get('title', name),
                    "description": data.get('extract', ' '),
                    "url": data.get('content_urls', {}).get('desktop', {}).get('page', ''),
                    "image": data.get('thumbnail', {}).get('source')
                }
    except: pass
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
