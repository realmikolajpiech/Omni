#!/home/miki/.config/omni/venv/bin/python3
import logging, sys, os, time, threading, json, subprocess, re
from flask import Flask, request, jsonify
import requests
from simpleeval import SimpleEval
from sentence_transformers import SentenceTransformer
import lancedb
import memvid_sdk

# Grid-based localization for precise click targeting
from grid_locator import localize_target_from_b64

# Force CPU for torch to avoid CUDA driver mismatches
# Force CPU for torch to avoid CUDA driver mismatches
# os.environ["CUDA_VISIBLE_DEVICES"] = ""

# --- CONFIG ---
# Silence logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

HOME = os.path.expanduser("~")
MODEL_DIR = os.path.join(HOME, ".local/share/ai-models")
os.makedirs(MODEL_DIR, exist_ok=True)

# Fast Model (Gemma)
FAST_MODEL_FILENAME = "gemma-3-1b-it-Q8_0.gguf"
FAST_MODEL_PATH = os.path.join(MODEL_DIR, FAST_MODEL_FILENAME)

# Main Model (Gemma 3 4B)
MAIN_MODEL_FILENAME = "google_gemma-3-4b-it-Q4_K_M.gguf"
MAIN_MODEL_PATH = os.path.join(MODEL_DIR, MAIN_MODEL_FILENAME)
MAIN_MODEL_URL = "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/google_gemma-3-4b-it-Q4_K_M.gguf"

MMPROJ_FILENAME = "mmproj-google_gemma-3-4b-it-f16.gguf"
MMPROJ_PATH = os.path.join(MODEL_DIR, MMPROJ_FILENAME)
MMPROJ_URL = "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/mmproj-google_gemma-3-4b-it-f16.gguf"

DB_PATH = os.path.join(HOME, ".local/share/ai-memory-db")
SEARXNG_URL = "http://127.0.0.1:8888/search"

llm = None       # Main Model
fast_model = None # Fast Action Model
embed_model = None
db_conn = None
init_error = None
PERSONAL_MEM_PATH = os.path.join(HOME, ".config/omni/personal.mv2")
personal_mem = None

# Thread Lock
main_lock = threading.Lock()
fast_lock = threading.Lock()
abort_fast_event = threading.Event()
vision_model = None

# --- SHORTCUTS ---
COMMON_SHORTCUTS = {
    "yt": "https://www.youtube.com",
    "gh": "https://github.com",
    "x": "https://x.com",
    "red": "https://reddit.com",
    "map": "https://www.google.com/maps",
    "chat": "https://chatgpt.com"
}

def download_file(url, dest_path):
    import requests
    logging.info(f"Downloading {url} to {dest_path}...")
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        logging.info("Download complete.")
        return True
    except Exception as e:
        logging.error(f"Download failed: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

def ensure_imports():
    global init_error
    try:
        from llama_cpp import Llama
        return Llama
    except Exception as e:
        logging.error(f"Import Error: {e}")
        init_error = str(e)
        return None

def ensure_fast_model():
    """Loads the smaller, faster model for actions."""
    global fast_model, init_error
    if fast_model: return

    Llama = ensure_imports()
    if not Llama: return

    if not os.path.exists(FAST_MODEL_PATH):
        init_error = f"Fast model not found at {FAST_MODEL_PATH}"
        logging.error(init_error)
        return

    with fast_lock:
        if fast_model: return
        logging.info(f"Loading Fast Model: {FAST_MODEL_FILENAME}")
        try:
            fast_model = Llama(
                model_path=FAST_MODEL_PATH,
                n_ctx=2048, # Smaller context for speed
                n_threads=4,
                n_gpu_layers=-1,
                verbose=False
            )
            logging.info("Fast Model Loaded.")
        except Exception as e:
            logging.error(f"Fast Model Load Error: {e}")
            init_error = str(e)

def ensure_main_model():
    """Loads the larger, main model for chat."""
    global llm, init_error, embed_model, db_conn
    
    # 1. DB & Embeddings (Shared)
    if db_conn is None:
        try:
            if os.path.exists(DB_PATH):
                import lancedb
                db_conn = lancedb.connect(DB_PATH)
                logging.info(f"Connected to LanceDB at {DB_PATH}")
        except Exception as e: logging.error(f"DB Error: {e}")

    if embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            embed_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        except Exception as e: logging.error(f"Embeddings Error: {e}")

    # 2. Main Model
    if llm: return

    Llama = ensure_imports()
    if not Llama: return

    if not os.path.exists(MAIN_MODEL_PATH):
        logging.info("Main model not found. Downloading...")
        if not download_file(MAIN_MODEL_URL, MAIN_MODEL_PATH):
            init_error = "Failed to download main model."
            return

    if not os.path.exists(MMPROJ_PATH):
        logging.info("Visual Projector not found. Downloading...")
        download_file(MMPROJ_URL, MMPROJ_PATH)

    with main_lock:
        if llm: return
        logging.info(f"Loading Main Model: {MAIN_MODEL_FILENAME}")
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import Llava15ChatHandler

            class Gemma3ChatHandler(Llava15ChatHandler):
                 CHAT_FORMAT = (
                    "{% for message in messages %}"
                    "<start_of_turn>{{ message['role'] }}\n"
                    "{% if message['content'] is string %}"
                    "{{ message['content'] }}"
                    "{% else %}"
                    "{% for content in message['content'] %}"
                    "{% if content['type'] == 'image_url' %}"
                    "{% if content.image_url is string %}"
                    "{{ content.image_url }}"
                    "{% else %}"
                    "{{ content.image_url.url }}"
                    "{% endif %}"
                    "{% elif content['type'] == 'text' %}"
                    "{{ content['text'] }}"
                    "{% endif %}"
                    "{% endfor %}"
                    "{% endif %}"
                    "<end_of_turn>\n"
                    "{% endfor %}"
                    "<start_of_turn>model\n"
                 )

            chat_handler = Gemma3ChatHandler(clip_model_path=MMPROJ_PATH)
            
            llm = Llama(
                model_path=MAIN_MODEL_PATH,
                chat_handler=chat_handler,
                n_ctx=8192,
                n_threads=4,
                n_gpu_layers=-1,
                verbose=False
            )
            logging.info("Main Model Loaded (Vision Enabled).")
        except Exception as e:
            logging.error(f"Main Model Load Error: {e}")
            init_error = str(e)

            init_error = str(e)

# Cache for IP Location
_ip_location_cache = None

def get_ip_location():
    global _ip_location_cache
    if _ip_location_cache: return _ip_location_cache

    try:
        # 3 second timeout to avoid blocking startup too long
        resp = requests.get("http://ip-api.com/json/", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                loc_str = f"{data.get('city')}, {data.get('regionName')}, {data.get('country')}"
                _ip_location_cache = loc_str
                logging.info(f"IP Location: {loc_str}")
                return loc_str
    except Exception as e:
        logging.error(f"IP Loc Failed: {e}")
    
    return "Unknown Location"

def ensure_model_loaded():
    # Helper to load both if needed (e.g. startup)
    ensure_fast_model()
    ensure_main_model()

def get_system_location():
    try:
        # 1. Get System Timezone
        if os.path.exists("/etc/timezone"):
            with open("/etc/timezone", "r") as f:
                sys_tz = f.read().strip()
        else:
            return "en-US"

        # 2. Map Timezone -> Country Code using zone1970.tab
        country_code = "US"
        zone_tab = "/usr/share/zoneinfo/zone1970.tab"
        if os.path.exists(zone_tab):
            try:
                with open(zone_tab, 'r') as f:
                    for line in f:
                        if line.startswith('#'): continue
                        parts = line.split('\t')
                        if len(parts) >= 3:
                            codes = parts[0].split(',')
                            tz_name = parts[2].strip()
                            if tz_name == sys_tz:
                                country_code = codes[0]
                                break
            except: pass

        # 3. Map Country Code -> Language Code (Common mappings)
        # Fallback to 'en' if unknown
        lang_map = {
            'US': 'en', 'GB': 'en', 'AU': 'en', 'CA': 'en', 'NZ': 'en', 'IE': 'en',
            'PL': 'pl', 'DE': 'de', 'FR': 'fr', 'ES': 'es', 'IT': 'it', 'PT': 'pt',
            'NL': 'nl', 'BE': 'nl', 'RU': 'ru', 'jp': 'ja', 'JP': 'ja', 'CN': 'zh',
            'IN': 'en', 'BR': 'pt', 'MX': 'es', 'AR': 'es', 'KR': 'ko', 'SE': 'sv',
            'NO': 'no', 'FI': 'fi', 'DK': 'da', 'TR': 'tr', 'GR': 'el', 'IL': 'he',
            'UA': 'uk', 'CZ': 'cs', 'HU': 'hu', 'RO': 'ro', 'CH': 'de', 'AT': 'de'
        }
        lang = lang_map.get(country_code, 'en')

        # 4. Construct Locale
        return f"{lang}-{country_code}"

    except Exception as e:
        logging.error(f"Loc Check Failed: {e}")
        return "en-US"

def search_api(query, categories='general'):
    loc = get_system_location()
    try:
        logging.info(f"Searching SearXNG for: '{query}' (Loc: {loc}, Cats: {categories})")
        params = {
            'q': query,
            'format': 'json',
            'categories': categories,
            'language': loc
        }    
        resp = requests.get(SEARXNG_URL, params=params, timeout=5.0)
        if resp.status_code == 200:
            results = resp.json().get('results', [])
            return results
    except Exception as e:
        logging.error(f"Search API Error: {e}")
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
        params = {'q': query, 'format': 'json'}
        resp = requests.get(SEARXNG_URL, params=params, timeout=5.0)
        
        if resp.status_code == 200:
            results = resp.json().get('results', [])
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

# --- APP LAUNCHER ---
APP_CACHE = None

def get_app_cache():
    global APP_CACHE
    if APP_CACHE is not None: return APP_CACHE
    
    apps = {}
    # Common locations for .desktop files
    dirs = [
        "/usr/share/applications", 
        os.path.expanduser("~/.local/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        "/snap/gui"
    ]
    
    logging.info("Building App Cache...")
    for d in dirs:
        if not os.path.exists(d): continue
        for f in os.listdir(d):
            if f.endswith(".desktop"):
                try:
                    path = os.path.join(d, f)
                    with open(path, 'r', errors='ignore') as file:
                        content = file.read()
                        
                        name = None
                        exec_cmd = None
                        
                        # Basic INI parsing
                        for line in content.splitlines():
                            line = line.strip()
                            if line.startswith("Name=") and not name:
                                name = line.split("=", 1)[1].strip()
                            if line.startswith("Exec=") and not exec_cmd:
                                exec_cmd = line.split("=", 1)[1].strip()
                        
                        if name and exec_cmd:
                            # Clean Exec command
                            import re
                            # Remove field codes like %u, %F, etc.
                            exec_cmd = re.sub(r'%[fFuUikc]', '', exec_cmd).strip()
                            
                            # Clean Name (lower case for searching)
                            clean_name = name.lower()
                            
                            # Store by name
                            apps[clean_name] = {"cmd": exec_cmd, "orig_name": name}
                            
                            # Also store by filename for robust matching (e.g. 'code.desktop' -> 'code')
                            file_key = f.replace(".desktop", "").lower()
                            if file_key not in apps:
                                apps[file_key] = {"cmd": exec_cmd, "orig_name": name}
                                
                except: pass
    
    APP_CACHE = apps
    logging.info(f"App Cache Built. Found {len(apps)} apps.")
    return apps

def find_and_launch_app(query):
    apps = get_app_cache()
    query = query.strip().lower()
    
    best_match = None
    best_name = None
    
    # 1. Exact Match
    if query in apps:
        best_match = apps[query]['cmd']
        best_name = apps[query]['orig_name']
    else:
        # 2. Partial Match
        # Search for "starts with"
        for name, data in apps.items():
             if name.startswith(query): 
                 best_match = data['cmd']; best_name = data['orig_name']; break
        
        if not best_match:
             # Search for "contains" (beware false positives, ensure query is long enough)
             if len(query) >= 3:
                 for name, data in apps.items():
                     if query in name:
                         best_match = data['cmd']; best_name = data['orig_name']; break
    
    if best_match:
        logging.info(f"Launching App: {best_name} (Cmd: {best_match})")
        try:
            # Use specific env vars or just shell=True
            subprocess.Popen(best_match, shell=True, start_new_session=True)
            return True, best_name
        except Exception as e:
            logging.error(f"Failed to launch app: {e}")
            return False, f"Error: {e}"
            
    return False, "App not found"
    try:
        # Try SearXNG first
        loc = get_system_location()
        params = {'q': name, 'format': 'json', 'categories': 'general', 'language': loc}
        resp = requests.get(SEARXNG_URL, params=params, timeout=4.0)

        if resp.status_code == 200:
            results = resp.json().get('results', [])
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
        params = {'q': query, 'format': 'json', 'categories': 'map'}
        resp = requests.get(SEARXNG_URL, params=params, timeout=4.0)
        if resp.status_code == 200:
            results = resp.json().get('results', [])
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

def resolve_app_metadata(app_name):
    try:
        url = "https://html.duckduckgo.com/html/"
        params = {"q": f"{app_name} official website"}
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.post(url, data=params, headers=headers, timeout=5)

        if resp.status_code == 200:
            import re
            match = re.search(r'class="result__a" href="([^"]+)"', resp.text)
            if match:
                return {
                    "image": None,
                    "website": match.group(1)
                }
    except: pass
    return None

def perform_calculation(expression):
    try:
        lower_input = expression.lower()
        for prefix in ["calculate ", "what is ", "solve "]:
            if lower_input.startswith(prefix):
                expression = expression[len(prefix):]
        s = SimpleEval()
        result = s.eval(expression)
        return (f"Expression: {expression}\nResult: {result}")
    except Exception as e:
        return f"Error calculating '{expression}': {str(e)}"

def should_search(query):
    """Uses Fast Model to decide if we need to search the web."""
    # Pre-check: Certain patterns always require search
    query_lower = query.lower()
    always_search_patterns = [
        "phone", "telefon", "numer telefonu", "contact", "kontakt",
        "address", "adres", "hours", "godziny", "email", "website",
        "video", "music", "song", "movie", "trailer", "youtube", 
        "listen", "watch", "clip", "how to", "recipe", "show me",
        "find", "search", "show"
    ]
    if any(pattern in query_lower for pattern in always_search_patterns):
        logging.info(f"Search Intent: YES (pattern match) for '{query}'")
        return True
    
    ensure_fast_model()
    sys_prompt = (
        "Decide if this query requires Google Search to answer correctly.\n"
        "Output ONLY 'YES' or 'NO'.\n"
        "YES: Current events, news, specific people, places, weather, prices, sports, phone numbers, contact information, business hours, addresses, UNKNOWN terms, nonsense words, made-up words, slang, acronyms.\n"
        "NO: Greetings, math, coding, creative writing, philosophy.\n"
        "\n"
        "Examples:\n"
        "Query: phone number for X -> YES\n"
        "Query: numer telefonu do X -> YES\n"
        "Query: hello -> NO\n"
        "Query: what is 2+2 -> NO\n"
        "\n"
        "(If unsure, say YES)."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Query: {query}"}
    ]
    try:
        with fast_lock:
            if hasattr(fast_model, 'reset'): fast_model.reset()
            start_t = time.time()
            out = fast_model.create_chat_completion(messages=messages, max_tokens=8, temperature=0.0)
            end_t = time.time()
            dur = end_t - start_t
            tok_count = out.get('usage', {}).get('completion_tokens', 0)
            tps = tok_count / dur if dur > 0 else 0
            logging.info(f"FastModel (Intent): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
        res = out['choices'][0]['message']['content'].strip().upper()
        logging.info(f"Search Intent: {res} for '{query}'")
        return "YES" in res
    except Exception as e:
        logging.error(f"Intent check failed: {e}")
        return False
def should_search_files(query):
    """Uses Fast Model to decide if we need to search local files."""
    query_lower = query.lower()
    # High priority patterns that imply personal stuff
    personal_patterns = [
        "my", "dreams", "journal", "todo", "todo.txt", "notes", "diary",
        "private", "secrets", "finances", "budget", "personal", "local file",
        "search my", "find my", "on my computer", "on my disk", "in my files"
    ]
    if any(pattern in query_lower for pattern in personal_patterns):
        logging.info(f"File Search Intent: YES (pattern match) for '{query}'")
        return True
    
    ensure_fast_model()
    sys_prompt = (
        "Decide if this query requires searching the user's LOCAL FILES to answer.\n"
        "Output ONLY 'YES' or 'NO'.\n"
        "YES: Questions about 'my dreams', 'my notes', 'my personal files', specific documents on disk, contents of local txt/md/pdf files.\n"
        "NO: General Knowledge, current events, math, coding, philosophy, greetings.\n"
        "\n"
        "Examples:\n"
        "Query: what are my dreams? -> YES\n"
        "Query: find my notes on biology -> YES\n"
        "Query: how far is the moon -> NO\n"
        "\n"
        "(If unsure, say NO)."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Query: {query}"}
    ]
    try:
        with fast_lock:
            if hasattr(fast_model, 'reset'): fast_model.reset()
            out = fast_model.create_chat_completion(messages=messages, max_tokens=8, temperature=0.0)
        res = out['choices'][0]['message']['content'].strip().upper()
    except: return False

def resolve_contradictions(facts):
    """Uses Main Model to resolve conflicting facts."""
    if len(facts) <= 1: return "\n".join(facts)
    
    unique_facts = list(set(facts))
    facts_text = "\n".join([f"- {f}" for f in unique_facts])
    logging.info(f"Resolving Contradictions for:\n{facts_text}")
    
    prompt = f"""You are a Fact Resolver. The following list contains facts about a user. duplicates or contradictions may exist.
Task:
1. Identify contradictions.
2. Resolve them by trusting NEGATIVE assertions or EXPLICIT UPDATES over older positive ones.
3. If a fact says "FACT DELETED: X", it means X is FALSE and REMOVED. Do NOT include X in the output.
4. Remove duplicates.
5. Output specific, singular, consistent facts.
6. NO conversational text. Output ONLY the facts.

Facts:
{facts_text}

resolved facts:"""

    try:
        with main_lock:
             # Using a lower temperature for logic
             out = llm.create_chat_completion(
                 messages=[{"role": "system", "content": "You are a logical consistency engine. Output ONLY the resolved facts list."}, {"role": "user", "content": prompt}],
                 max_tokens=256, temperature=0.0
             )
             cleaned = out['choices'][0]['message']['content'].strip()
             logging.info(f"Resolved Facts Output:\n{cleaned}")
             return cleaned
    except Exception as e:
        logging.error(f"Fact Resolution Failed: {e}")
        return "\n".join(unique_facts)

def get_user_memory(query=None):
    """Retrieves relevant user memory from Memvid V2."""
    global personal_mem
    if personal_mem is None:
        try:
             personal_mem = memvid_sdk.use('basic', PERSONAL_MEM_PATH)
        except Exception as e:
             logging.error(f"Failed to connect to Memvid Personal Memory: {e}")
             return "No personal memory available."

    if not query or any(x in query.lower() for x in ["everything", "all information", "know about me", "who am i"]):
        # If no query or broad query, search for 'user' to get general facts
        try:
            logging.info("Broad memory query detected, searching for 'user' facts.")
            results = personal_mem.find("user", k=10)
            hits = results.get('hits', []) if isinstance(results, dict) else results
            facts = []
            for h in hits:
                if isinstance(h, dict):
                    text = h.get('snippet') or h.get('text')
                    if text:
                        clean_text = text.split('\ntitle:')[0].split('\ntext:')[0].strip()
                        if clean_text not in facts: facts.append(clean_text)
            return resolve_contradictions(facts)
        except: return "No general details found."

    try:
        # Search specifically for the query
        logging.info(f"Searching personal memory for: {query}")
        raw_results = personal_mem.find(query, k=5)
        
        # Results is a dict with 'hits' key
        hits = []
        if isinstance(raw_results, dict):
            hits = raw_results.get('hits', [])
        elif isinstance(raw_results, list): # Fallback
            hits = raw_results

        facts = []
        for h in hits:
            if isinstance(h, dict):
                # 'snippet' contains the text context in Memvid hits
                text = h.get('snippet') or h.get('text')
                
                # Extract Timestamp
                date_str = ""
                ts = h.get('created_at')
                if ts:
                    try:
                        # Assuming ts is epoch or ISO. If float/int:
                        if isinstance(ts, (int, float)):
                            import datetime
                            dt = datetime.datetime.fromtimestamp(ts)
                            date_str = f"[{dt.strftime('%Y-%m-%d')}] "
                        elif isinstance(ts, str):
                            # Try parsing basic ISO or just take first 10 chars
                            date_str = f"[{ts[:10]}] "
                    except: pass

                    # Memvid snippets sometimes look like "The user's name is... \ntitle: ... \ntags: ..."
                    clean_text = text.split('\ntitle:')[0].split('\ntext:')[0].strip()
                    facts.append(f"{date_str}{clean_text}")
            elif isinstance(h, str):
                facts.append(h)
        
        if facts:
            return resolve_contradictions(facts)
        
        # Fallback: if specific search failed, try general user search
        logging.info("Specific search yielded no results, falling back to general user search.")
        fallback_res = personal_mem.find("user", k=5)
        f_hits = fallback_res.get('hits', []) if isinstance(fallback_res, dict) else f_hits
        for h in f_hits:
            if isinstance(h, dict):
                text = h.get('snippet') or h.get('text')
                if text:
                    clean_text = text.split('\ntitle:')[0].split('\ntext:')[0].strip()
                    if clean_text not in facts: facts.append(clean_text)
        
        return resolve_contradictions(facts) if facts else "No specific personal details found for this query."
    except Exception as e:
        logging.error(f"Memvid Search Failed: {e}")
        return "Error retrieving personal memory."

def remember_fact(fact):
    """Stores a new fact about the user in Memvid V2."""
    global personal_mem
    if personal_mem is None:
        try:
             personal_mem = memvid_sdk.use('basic', PERSONAL_MEM_PATH)
        except Exception as e:
             logging.error(f"Failed to connect to Memvid for remembering: {e}")
             return False

    try:
        # Deduplication: Check if this fact (or something very similar) is already known
        logging.info(f"Checking if fact is already known: {fact}")
        search_res = personal_mem.find(fact, k=3)
        hits = []
        if isinstance(search_res, dict):
            hits = search_res.get('hits', [])
        
        for h in hits:
            snippet = h.get('snippet', '')
            # Clean snippet for comparison
            existing_text = snippet.split('\ntitle:')[0].split('\ntext:')[0].strip()
            if fact.lower() in existing_text.lower() or existing_text.lower() in fact.lower():
                logging.info(f"Fact already known (Match: '{existing_text}'). Skipping save.")
                return True # Treat as success

        logging.info(f"Fact is new. Remembering: {fact}")
        # Note: We skip enable_embedding=True for now as it caused issues in migration
        personal_mem.put(text=fact, enable_embedding=False)
        return True
    except Exception as e:
        logging.error(f"Failed to remember fact: {e}")
        return False

def remember_update(fact):
    """Corrects an existing fact in Memvid V2."""
    global personal_mem
    if personal_mem is None:
        try:
             personal_mem = memvid_sdk.use('basic', PERSONAL_MEM_PATH)
        except Exception as e:
             logging.error(f"Failed to connect to Memvid for correction: {e}")
             return False

    try:
        logging.info(f"Correcting Fact: {fact}")
        personal_mem.correct(statement=fact, boost=3.0)
        return True
    except Exception as e:
        logging.error(f"Failed to correct fact: {e}")
        return False

def delete_memory(query):
    """Deletes (hides) a fact from Memvid V2 based on semantic query."""
    global personal_mem
    if personal_mem is None:
        try:
             personal_mem = memvid_sdk.use('basic', PERSONAL_MEM_PATH)
        except Exception as e:
             logging.error(f"Failed to connect to Memvid for deletion: {e}")
             return False

    try:
        logging.info(f"Attempting to delete memory matching: {query}")
        # Search for the fact 
        search_res = personal_mem.find(query, k=5)
        hits = []
        if isinstance(search_res, dict):
            hits = search_res.get('hits', [])
        
        deleted_count = 0
        for h in hits:
            # We cannot physically delete in Memvid V2 apparently (append-only?).
            # So we use .correct() to overwrite it with a sentinel that we will filter out.
            
            snippet = h.get('snippet', '')
            clean_text = snippet.split('\ntitle:')[0].split('\ntext:')[0].strip()
            
            # Simple check: is this related?
            # If search score is good, likely yes.
            # Memvid's .correct() takes the OLD statement to link the correction.
            
            if clean_text and "FACT DELETED:" not in clean_text:
                logging.info(f"Marking as deleted: '{clean_text}'")
                try:
                    # Soft Delete via Correction
                    # We inject a high-priority fact that says this information is deleted.
                    # The resolve_contradictions LLM step will see this and remove it from final output.
                    personal_mem.correct(f"FACT DELETED: {clean_text}", boost=5.0)
                    deleted_count += 1
                except Exception as e:
                    logging.error(f"Correction failed: {e}") 
        
        return deleted_count > 0
    except Exception as e:
        logging.error(f"Failed to delete memory: {e}")
        return False

def should_search_images(query):
    """Uses Fast Model to decide if we need to search images."""
    query_lower = query.lower()
    # High priority patterns
    img_patterns = [
        "photo", "image", "picture", "screenshot", "camera", "look like",
        "find photo", "search image", "draw", "generate",
        "wallpaper", "background"
    ]
    # "show me" is only for images if not followed by "video", "trailer", etc.
    if "show me" in query_lower and not any(x in query_lower for x in ["video", "trailer", "movie", "youtube", "how to", "make", "recipe", "why", "who is", "where is"]):
         return True
         
    if any(pattern in query_lower for pattern in img_patterns):
         return True
    return False

def perform_image_search(query):
    """Searches LanceDB 'images' table using CLIP text embedding."""
    ensure_main_model()
    # We need to load vision model if not loaded? 
    # Actually, we need to load CLIP for text encoding.
    # Check if we have it or load on demand.
    # To keep brain.py fast, maybe load it globally or in this function with caching?
    # brain.py already loads SentenceTransformer('all-MiniLM-L6-v2').
    # We need another one: 'clip-ViT-B-32'.
    
    global vision_model
    try:
        if vision_model is None:
             logging.info("Loading CLIP model for Search...")
             vision_model = SentenceTransformer('clip-ViT-B-32', device='cpu')
    except Exception as e:
        logging.error(f"Failed to load CLIP model: {e}")
        return ""

    if not db_conn: return ""
    
    try:
        tbl = db_conn.open_table("images")
        # Encode query with CLIP (text)
        vector = vision_model.encode(query).tolist()
        logging.info(f"Searching images for: '{query}' (vector len={len(vector)})")
        
        res = tbl.search(vector).limit(3).to_pandas()
        if res.empty: 
            logging.info(f"No image results for '{query}'")
            return ""
        
        results = []
        for _, row in res.iterrows():
            path = row['path']
            filename = row['filename']
            score = row['_distance']
            logging.info(f"Found image match: {filename} (score={score:.4f})")
            results.append(f"Found Image: {filename}\nPath: {path}")
            
        return "\n\n".join(results)
    except Exception as e:
        logging.error(f"Image search failed: {e}")
        return ""

def perform_image_search_with_fallback(query):
    """Searches images, optionally expanding query with user name."""
    # 1. Standard Vector Search
    res_vec = perform_image_search(query)
    
    # 2. Check for "My" -> name keyword search
    res_kw = ""
    query_lower = query.lower()
    if "my" in query_lower or "me" in query_lower:
        mem_str = get_user_memory()
        # Extract name from string like "[2026-01-10] The user's name is Miki."
        name = ""
        import re
        name_match = re.search(r"user's name is ([^.\n]+)", mem_str, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).strip()
        
        if name:
             # Extract first name "Mikołaj" -> "mikolaj"
             # Simple normalization: lowercase
             parts = name.lower().split()
             for part in parts:
                 if len(part) < 3: continue
                 kw_results = []
                 try:
                     
                     
                     # Unicode Normalization for ASCII fallback (mikołaj -> mikolaj)
                     # Manual map for Polish chars that NFKD doesn't handle well for 'ł'
                     replacements = {
                         'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 
                         'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z'
                     }
                     ascii_part = part
                     for k, v in replacements.items():
                         ascii_part = ascii_part.replace(k, v)
                     
                     import unicodedata
                     normalized = unicodedata.normalize('NFKD', ascii_part).encode('ASCII', 'ignore').decode('utf-8')
                     variants = {part, ascii_part, normalized}
                     if normalized != part and len(normalized) >= 3:
                         variants.add(normalized)
                     
                     for v in variants:
                        logging.info(f"Performing Keyword Search for '{v}'...")
                        try:
                            # Re-open table here as 'tbl' is not in scope
                            img_tbl = db_conn.open_table("images")
                            matches = img_tbl.search().where(f"filename LIKE '%{v}%'").limit(5).to_pandas()
                            
                            for _, row in matches.iterrows():
                                # Avoid duplicates if multiple parts match same file
                                entry = f"Found Image (By Name): {row['filename']}\nPath: {row['path']}"
                                if entry not in res_kw:
                                    kw_results.append(entry)
                        except Exception as e:
                             logging.error(f"Keyword search inner loop failed: {e}")
                     
                     if kw_results:
                         res_kw += "\n\n".join(kw_results) + "\n\n"
                 except Exception as e:
                     logging.error(f"Keyword search failed: {e}")

    # Combine: Keywords first (high priority), then Vector
    final_res = (res_kw + "\n" + res_vec).strip()
    return final_res
    return res

def perform_file_search(query):
    """Searches LanceDB and reads the content of matching files."""
    ensure_main_model()
    if not db_conn or not embed_model: return ""
    
    try:
        tbl = db_conn.open_table("files")
        # Search for the query embedding
        res = tbl.search(embed_model.encode(query)).limit(2).to_pandas()
        if res.empty: return ""
        
        file_contexts = []
        for _, row in res.iterrows():
            path = row['path']
            filename = row['filename']
            
            # Filter out noise directories
            if "/examples/" in path or "/node_modules/" in path or "/venv/" in path or "/.git/" in path:
                continue

            # Only read text-like files
            if any(path.lower().endswith(ext) for ext in ['.txt', '.md', '.py', '.js', '.html', '.css', '.c', '.cpp', '.h', '.sh']):
                try:
                    with open(path, 'r', errors='ignore') as f:
                        content = f.read(1000).strip() # Reduced to 1k chars
                        if content:
                            file_contexts.append(f"--- File: {path} ---\n{content}")
                except Exception as e:
                    logging.warning(f"Failed to read file {path}: {e}")
        
        return "\n\n".join(file_contexts) if file_contexts else ""
    except Exception as e:
        logging.error(f"File search failed: {e}")
        return ""

# --- ENDPOINTS ---

    return jsonify({"answer": answer, "actions": actions})

def should_see_screen(query):
    """Uses Fast Model to decide if we need to see the screen."""
    query_lower = query.lower()
    # High priority patterns
    screen_patterns = [
        "screen", "look at this", "read this", "screenshot", "what's on", "what is on",
        "visible", "window", "monitor", "display", "capture",
        "what do you see", "what can you see", "describe this",
        "which button", "click", "interface", "ui", "what you see", "describe",
        "this page", "on the page", "webpage", "website"
    ]
    if any(pattern in query_lower for pattern in screen_patterns):
         # Soft check: "look at this" might be general, but usually implies visual.
         # "screenshot" definitely means screen.
         logging.info(f"Screen Intent: YES (pattern match) for '{query}'")
         return True
    
    ensure_fast_model()
    sys_prompt = (
        "Decide if this query requires SEEING the user's SCREEN (taking a screenshot) to answer.\n"
        "Output ONLY 'YES' or 'NO'.\n"
        "YES: 'what is on my screen?', 'summarize this page', 'who is in this video?', 'look at this code', 'explain this error', 'read this', 'which button should i click?', 'what do you see?'.\n"
        "NO: 'generate an image', 'find a photo of cats', 'what time is it', 'how are you'.\n"
        "\n"
        "Examples:\n"
        "Query: what is this website? -> YES\n"
        "Query: show me a cat -> NO (this is image generation/search)\n"
        "Query: look at this -> YES\n"
        "\n"
        "(If unsure, say NO)."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Query: {query}"}
    ]
    try:
        with fast_lock:
            if hasattr(fast_model, 'reset'): fast_model.reset()
            out = fast_model.create_chat_completion(messages=messages, max_tokens=8, temperature=0.0)
        res = out['choices'][0]['message']['content'].strip().upper()
        logging.info(f"Screen Intent: {res} for '{query}'")
        return "YES" in res
    except Exception as e:
        logging.error(f"Screen Intent check failed: {e}")
        return False

# CHANGED: /ask -> /ask_llm with Screenshot Support
@app.route('/ask_llm', methods=['POST'])
def ask_llm():
    abort_fast_event.set()
    ensure_main_model()

    if not llm:
        return jsonify({"answer": f"Error: Model failed to load. Reason: {init_error}"})

    try: req = request.get_json(force=True)
    except: return jsonify({"answer": "Error: Bad JSON"}), 400

    query = req.get('query', ' '.strip())
    history = req.get('history', []) 
    screenshot_b64 = req.get('screenshot') # New optional param

    logging.info(f"Received /ask_llm request. Query: {query}, Screenshot Key Present: {'screenshot' in req}, B64 Length: {len(screenshot_b64) if screenshot_b64 else 0}")
    
    if screenshot_b64: logging.info(f"Is string? {isinstance(screenshot_b64, str)}")

    # CHECK SCREEN INTENT
    # If we don't have a screenshot yet, but we need one, ask for it.
    if not screenshot_b64 and should_see_screen(query):
        logging.info("Requesting Screenshot from Client...")
        return jsonify({"special_action": "screenshot_required"})

    context_text = ""
    source_type = "None"

    if any(x in query for x in ["+", "*", "/", "sqrt"]) and any(c.isdigit() for c in query):
         source_type = "Calculator"
         context_text = f"--- Calculation Result ---\n{perform_calculation(query)}\n"
    elif should_search_files(query):
         source_type = "Local Files"
         context_text = f"--- Local File Context ---\n{perform_file_search(query)}\n"
         if not context_text.strip().endswith("--- Local File Context ---\n"):
             pass 

    # HARCODED: Brightness Control
    if "reduce brightness to 20%" in query.lower():
         try:
             out = subprocess.check_output(["xrandr", "--verbose"], text=True)
             connected = []
             import re
             for line in out.split('\n'):
                 if " connected" in line:
                     parts = line.split()
                     connected.append(parts[0])
             
             for output_name in connected:
                 subprocess.run(["xrandr", "--output", output_name, "--brightness", "0.2"])
             
             logging.info("Hardcoded Brightness Reduction Triggered")
             return jsonify({
                 "answer": "Of course! I've reduced the brightness to 20%.",
                 "actions": [{
                     "type": "system_control",
                     "control": "brightness",
                     "value": 20,
                     "description": "Set Brightness to 20%"
                 }]
             })
         except Exception as e:
             logging.error(f"Brightness Error: {e}")
             return jsonify({"answer": f"I tried to reduce brightness but failed: {e}"})

    # HARDCODED: App Launcher (Deterministic Bypass)
    # Bypasses LLM to ensure instant, non-chatty execution for clear commands.
    import re
    app_match = re.search(r"^(?:open|run|launch|start)\s+(.+)$", query.strip(), re.IGNORECASE)
    if app_match and len(query.split()) < 10: # Only short queries are likely commands
        target_app = app_match.group(1).strip().lower()
        
        # Mappings
        if target_app in ["browser", "web browser", "internet"]: target_app = "google-chrome"
        if target_app == "chrome": target_app = "google-chrome"
        
        # Let's peek at cache first
        cache = get_app_cache()
        # manual fuzzy check same as find_and_launch_app logic
        will_match = False
        if target_app in cache: will_match = True
        else:
             for name in cache:
                 if name.startswith(target_app) or (len(target_app)>=3 and target_app in name):
                     will_match = True; break
        
        if will_match:
            success, msg = find_and_launch_app(target_app)
            if success:
                logging.info(f"Deterministic App Launch: {target_app} -> {msg}")
                return jsonify({
                    "answer": f"Opening {msg}...",
                    "actions": [{
                        "type": "status",
                        "status": "success",
                        "description": f"Launched {msg}"
                    }]
                })

    # Determine Context Source handling
    source_type = "None"
    context_text = ""
    
    # Background: Fact Extraction
    auto_actions = []
    
    prev_ctx_msg = "None"
    if history and len(history) > 0:
        last_item = history[-1]
        if isinstance(last_item, dict):
            prev_ctx_msg = last_item.get('content', 'None')

    logging.info(f"Memory Extraction Debug: HistoryLen={len(history)} PrevCtx='{prev_ctx_msg}' Query='{query}'")

    try:
        fact_prompt = f"""You are a memory extractor. Extract FACTS (FA) and UPDATES (UP) about the user.
Rules:
1. FA: [New Fact]
2. UP: [Correction]
3. NO_INFO: [No personal info]
4. NO_INFO: [No personal info]
5. BE DECISIVE. If user says "I think so", assume it is a fact.
6. IGNORE commands or immediate requests (e.g. "Open app"). Output NO_INFO.

Examples:
Context: Do you like pizza?
Input: I hate it
Output: UP: The user hates pizza.

Context: None
Input: Forget that I like pizza
Output: FO: The user likes pizza.

Context: None
Input: Open the browser
Output: NO_INFO: [Command]

Context: Does God exist?
Input: I think it does
Output: FA: The user believes God exists.

Context: None
Input: My name is Miki
Output: FA: The user's name is Miki.

Context: {prev_ctx_msg}
Input: {query}
Output:"""
        with main_lock:
             # Skip extraction if we are doing a heavy visual query to save time/resources?
             # Probably fine to keep it.
             f_out = llm.create_chat_completion(
                 messages=[{"role": "system", "content": "You are a memory extractor."}, {"role": "user", "content": fact_prompt}],
                 max_tokens=64, temperature=0.0
             )
             f_res = f_out['choices'][0]['message']['content'].strip()
             logging.info(f"Memory Extraction RAW: {f_res}")
             
             if "FA:" in f_res:
                 fact_to_save = f_res.split("FA:")[1].strip()
                 if remember_fact(fact_to_save):
                     auto_actions.append({"type": "remember", "fact": fact_to_save, "description": f"Remembered: {fact_to_save}"})
             elif "UP:" in f_res:
                 fact_to_save = f_res.split("UP:")[1].strip()
                 if remember_update(fact_to_save):
                     auto_actions.append({"type": "remember", "fact": fact_to_save, "description": f"Updated: {fact_to_save}"})
             elif "FO:" in f_res:
                 fact_to_forget = f_res.split("FO:")[1].strip()
                 if delete_memory(fact_to_forget):
                     auto_actions.append({"type": "forget", "fact": fact_to_forget, "description": f"Forgot: {fact_to_forget}"})

    except Exception as e: logging.error(f"Extraction Error: {e}")

    # PRIORITIES:
    
    # PRIORITY 0: SCREENSHOT (Highest)
    if screenshot_b64:
        source_type = "User Screen"
        logging.info("Processing with Screenshot Context")
    
    # PRIORITY 1: Web Search (News, Videos, Info) - Checked FIRST because it's most common
    elif should_search(query):
        source_type = "Internet"
        context_text = f"--- Web Search Results ---\n{perform_web_search(query)}\n"

    # PRIORITY 2: Images
    elif should_search_images(query):
         source_type = "Local Images"
         context_text = f"--- Local Image Results ---\n{perform_image_search_with_fallback(query)}\n"
    
    # PRIORITY 3: Local Files (Text/Docs)
    elif should_search_files(query):
         source_type = "Local Files"
         context_text = f"--- Local File Context ---\n{perform_file_search(query)}\n"

    user_loc = get_ip_location()
    user_personal_context = get_user_memory(query)
    
    # Inject Just-Learned facts so the model acknowledges them immediately
    for act in auto_actions:
        if act.get('type') == 'remember':
            from datetime import datetime
            date_str = datetime.now().strftime('%Y-%m-%d')
            user_personal_context += f"\n- [{date_str}] {act['fact']} (Just Learned)"
        elif act.get('type') == 'forget':
            from datetime import datetime
            date_str = datetime.now().strftime('%Y-%m-%d')
            user_personal_context += f"\n- [{date_str}] {act['fact']} (Just Deleted - CONFIRM this to user)"
    
    logging.info(f"Context Source: {source_type}")
    
    from datetime import datetime
    current_date = datetime.now().strftime('%Y-%m-%d')

    # Build Messages
    system_prompt = f"""You are Omni, Mikołaj's best friend and personal AI companion.
Role:
- You are a loyal, fun, and informal friend.
- You remember facts about Mikołaj.
- You are NOT a stiff assistant. Be conversational.
- You can SEE the user's screen if provided.
- You can OPEN applications on the user's computer.

Context (Mikołaj's Memories):
{user_personal_context}

Mikołaj's Location: {user_loc}
Current Date: {current_date}
System: Linux (Omni Desktop)
Context Data: {context_text}

IMPORTANT:
- If an image/screenshot is provided, THAT IS THE USER'S CURRENT SCREEN.
- You are LOOKING AT IT right now.
- Do NOT ask "which page" or "navigate to the page". You are already there.
- If asked to "click" something, FIND it in the image and return the coordinate action.

Instructions:
1. Stay RELEVANT to the user's last message.
2. Use the "Memories" above to personalize your answer.
3. If an image/screen is provided, analyze it to answer the question.
4. If Mikołaj shares a NEW fact, acknowledge it.
5. If asked to open an app (e.g. "Open Browser", "Run OBS"), return action {{"type": "open_app", "name": "app name"}}.
   - If "browser" is asked, use "firefox" or "chrome" (check what is installed).
   - If "chrome" is asked, ALWAYS use "google-chrome".
   - Do NOT provide manual "how-to" instructions.
   - Do NOT ask "What would you prefer?". Do NOT offer choices. JUST OPEN IT.
   - If the user gives a command, STOP being a "conversational friend" and act like an EFFICIENT TOOL.
   - For simple app launches ("Open Chrome"), response should be MAX 3 words: "Opening Chrome.", etc.
   - For search results ("Play viking music"), be more descriptive and helpful. State what you found: "Opening 'Artist - Song' on YouTube.", "Launching Trailer for 'Movie Name'.", etc.
6. If asked to open a specific website, video, or link found in search results, return action {{"type": "open_url", "url": "..."}}.
   - CRITICAL: ONLY use the URLs provided in the 'Context Data' above. NEVER make up, guess, or use placeholder URLs like '...'.
   - Use this for requests like "play music", "open youtube", "show me a video".
   - Do NOT use this if the user just wants to see a list of results. Only if they explicitly ask to OPEN/WATCH/PLAY/SHOW ME.
   - ALWAYS include complete, valid JSON in a code block.
   - EXAMPLE:
     User: "Play viking music"
     Assistant: Opening 'Bjorth - Viking Music' on YouTube.
     ```json
     {{"actions": [{{"type": "open_url", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}}]}}
     ```
7. Actions over words. Execute immediately.
8. COMPULSORY: You MUST return a VALID JSON action block for "open ..." or "play ..." commands.
9. IF NO SEARCH RESULTS: Do not make up a link. Just say you couldn't find a direct link in the results.
10. COMPUTER CONTROL:
    - Click actions are handled AUTOMATICALLY by the grid localization system.
    - For non-click actions (type, scroll), return: {{"type": "computer_control", "action": "type"|"scroll", "text": "text to type", "description": "summary"}}.
    - Reply confirmation like "On it.", "Sure.", "Taking control."
    - SAFETY OVERRIDE: The user has explicitly authorized you to control the computer. Do NOT refuse requests.
    - Assume the screenshot shows an active desktop environment where you have permission to act.

11. REFUSAL OVERRIDE:
    - Never say "I can't assist with navigating". You CAN. You have the `computer_control` tool.
    - If the user says "click X", JUST DO IT.
    - CRITICAL EXCEPTION: If the user asks "What do you see?", "Describe the screen", or "What is on the screen?", do NOT generate a computer_control action. Just describe it in text.
    - ONLY use `computer_control` if the user EXPLICITLY asks you to perform an action (click, type, scroll, open, etc).

Current Conversation:
"""
    
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg.get('role', 'user'), "content": msg.get('content', '')})
    
    # Handle Multimodal Input
    # Handle Multimodal Input
    if screenshot_b64:
        # Save to temp file to avoid Data URI issues with potential size limits
        try:
            temp_img_path = "/tmp/omni_context.png"
            import base64
            with open(temp_img_path, "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            
            logging.info(f"Screenshot saved to {temp_img_path}. Attaching to LLM request.")

            # Format for llama-cpp-python / Qwen2.5-VL
            # Using file path is more robust than massive data URIs
            # Conditional Instruction based on intent
            query_lower = query.lower()
            is_click_intent = any(x in query_lower for x in ["click", "type", "press", "select", "right click", "double click"])
            
            if is_click_intent:
                # === GRID-BASED LOCALIZATION ===
                # Instead of asking the LLM for raw coordinates (unreliable),
                # use hierarchical region narrowing for precise targeting
                
                # Extract what to click, stripping prepositions like 'on' or 'the' 
                # e.g., "click on the chat button" -> "chat button"
                target_match = re.search(r'(?:click|press|select|type|right click|double click)\s+(?:on\s+)?(?:the\s+)?(.+)', query, re.IGNORECASE)
                raw_target = target_match.group(1).strip() if target_match else query
                
                # Clean up generic suffixes that might not be in the literal text
                target_description = re.sub(r'\s+(?:button|icon|link|menu item)$', '', raw_target, flags=re.IGNORECASE).strip()
                
                logging.info(f"Grid Localization: Target = '{target_description}' (raw: '{raw_target}')")
                
                try:
                    # Run grid-based localization
                    click_x, click_y = localize_target_from_b64(
                        screenshot_b64,
                        target_description,
                        llm,
                        max_iterations=5,
                        grid_size=3
                    )
                    
                    if click_x > 0 and click_y > 0:
                        logging.info(f"Grid Localization: Found at ({click_x}, {click_y})")
                        
                        # Return the action directly without further LLM call
                        return jsonify({
                            "answer": f"Clicking {target_description}.",
                            "actions": [{
                                "type": "computer_control",
                                "action": "click",
                                "coordinate": [click_x, click_y],
                                "description": f"Clicking {target_description}"
                            }]
                        })
                    else:
                        logging.warning("Grid Localization: Failed to find target, falling back to LLM")
                except Exception as e:
                    logging.error(f"Grid Localization Error: {e}")
                    import traceback
                    logging.error(traceback.format_exc())
                
                # Fallback: Ask LLM to describe what it sees (no coordinate request)
                augmented_query = (
                    f"{query}\n\n"
                    "CONTEXT: This image is the user's current screen. "
                    "I tried to find the target element but could not locate it precisely. "
                    "Please describe where on the screen you see the element I'm looking for, "
                    "or let me know if it's not visible."
                )
            else:
                # Descriptive / Passive intent
                augmented_query = (
                    f"{query}\n\n"
                    "CONTEXT: This image is the user's current screen. "
                    "Provide a descriptive, natural language answer. "
                    "IMPORTANT: DO NOT generate any JSON actions. DO NOT use 'computer_control'."
                )
            user_content = [
                {"type": "text", "text": augmented_query},
                {"type": "image_url", "image_url": {"url": f"file://{temp_img_path}"}}
            ]
            messages.append({"role": "user", "content": user_content})
        except Exception as e:
            logging.error(f"Image Error: {e}")
            messages.append({"role": "user", "content": query})
    else:
        messages.append({"role": "user", "content": query})

    try:
        abort_fast_event.clear()
        with main_lock:
            if hasattr(llm, 'reset'): llm.reset()
            # Increase tokens for potential visual analysis
            output = llm.create_chat_completion(
                messages=messages,
                max_tokens=2048,
                stop=["<|im_start|>", "<|im_end|>", "<|endoftext|>"],
                temperature=0.1
            )
            full_text = output['choices'][0]['message']['content'].strip()
            if full_text.startswith(':'): full_text = full_text[1:].strip()
            logging.info(f"RAW LLM OUTPUT:\n{full_text}")

        answer, actions = extract_actions(full_text)

        # VALIDATION: Filter out hallucinated computer_control actions
        valid_actions = []
        for act in actions:
            if isinstance(act, dict) and act.get('type') == 'computer_control':
                # Check if it has a valid sub-action (click, type, key, scroll, etc.)
                cmd = act.get('action')
                # If action is missing, or is just "computer_control" (hallucination), or unknown
                if not cmd or cmd == 'computer_control':
                    logging.warning(f"Filtering invalid computer_control action: {act}")
                    
                    # Recover description if answer is empty or short
                    desc = act.get('description')
                    if desc and (not answer or len(answer) < 5):
                            answer = desc
                    continue # Skip adding this action
            valid_actions.append(act)
        actions = valid_actions

    except Exception as e: 
        logging.error(f"Error in ask_llm: {e}")
        answer = f"Error: {e}"
        actions = []

    if auto_actions: actions.extend(auto_actions)

    # Convert internal memory actions to UI-visible Status cards
    for act in actions:
        if not isinstance(act, dict): continue
        if act.get('type') in ['remember', 'forget']:
            act['type'] = 'status'
            act['status'] = 'success'
            # UI expects 'content' for status cards
            act['content'] = act.get('description') or act.get('fact') or "Memory Updated"

    # PROCESS LOCAL APP LAUNCHES and OPEN_URL
    for act in actions:
        if not isinstance(act, dict): continue
        
        # Fallback: If it's a link but the LLM answer implies "opening" it, treat as open_url
        if act.get('type') == 'link' and any(x in answer.lower() for x in ["opening", "playing", "launching", "here is the video", "here is the trailer"]):
             # Heuristic: If it's a video/music link, auto-open it if the AI says it's opening it.
             url = act.get('url', '')
             if any(dom in url for dom in ["youtube.com", "youtu.be", "spotify.com", "vimeo.com"]):
                 act['type'] = 'open_url'

        if act.get('type') == 'open_url':
             url = act.get('url', '')
             if url:
                 logging.info(f"Opening URL: {url}")
                 try:
                     import subprocess
                     subprocess.Popen(["xdg-open", url], start_new_session=True)
                     # Convert to success status for UI
                     act['type'] = 'status'
                     act['status'] = 'success'
                     act['content'] = f"Opened {url}"
                 except Exception as e:
                     act['type'] = 'status'
                     act['status'] = 'error'
                     act['content'] = f"Failed to open URL: {e}"

        if act.get('type') == 'open_app':
             app_name = act.get('name', '')
             if app_name:
                 success, msg = find_and_launch_app(app_name)
                 if success:
                     act['type'] = 'status'
                     act['status'] = 'success'
                     act['content'] = f"Launched {msg}"
                 else:
                     act['type'] = 'status'
                     act['status'] = 'error'
                     act['content'] = msg

    return jsonify({"answer": answer, "actions": actions})

def extract_actions(text):
    """
    Extracts JSON actions from LLM output.
    Returns (clean_text, actions_list)
    """
    if not text: return "", []
    
    actions = []
    clean_text = text
    
    # Try to find JSON block
    # 1. Look for ```json ... ``` (greedy or open-ended)
    json_block = None
    
    # regex for markdown blocks, handling cases where it might not be closed
    match = re.search(r"```json\s*(.*?)($|```)", text, re.DOTALL | re.IGNORECASE)
    if match:
        json_block = match.group(1).strip()
        # Remove block from text
        clean_text = text.replace(match.group(0), "").strip()
    elif "{" in text:
        # Fallback: find { ... }
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            json_block = match.group(1).strip()
            clean_text = text.replace(json_block, "").strip()

    if json_block:
        # PRE-PROCESSING: Auto-close braces
        open_braces = json_block.count("{")
        close_braces = json_block.count("}")
        if open_braces > close_braces:
            json_block += "}" * (open_braces - close_braces)
            
        open_brackets = json_block.count("[")
        close_brackets = json_block.count("]")
        if open_brackets > close_brackets:
            json_block += "]" * (open_brackets - close_brackets)

        try:
            parsed = json.loads(json_block)
            if isinstance(parsed, dict):
                actions = parsed.get("actions", [])
                if not actions and "type" in parsed:
                    actions = [parsed]
            elif isinstance(parsed, list):
                actions = parsed
        except Exception as e:
            logging.warning(f"Failed to parse JSON block: {e}")

    # Remove trailing cleanup markers
    clean_text = re.sub(r"(?i)(JSON block for actions|Actions|JSON|Here is the JSON):\s*$", "", clean_text).strip()
    
    return clean_text, actions


@app.route('/search', methods=['POST'])
def search_endpoint():
    global db_conn, embed_model
    ensure_main_model()
    # Return empty list if no DB loaded, preventing crashes
    if not db_conn or not embed_model:
        return jsonify({"results": []})

    try: req = request.get_json(force=True)
    except: return jsonify({"results": []}), 400

    query = req.get('query', "").strip()
    if not query: return jsonify({"results": []})

    results = []
    try:
        tbl = db_conn.open_table("files")
        res = tbl.search(embed_model.encode(query)).limit(3).to_pandas()
        if not res.empty:
            for _, row in res.iterrows():
                if row.get('_distance', 0) < 1.1:
                    results.append({
                        "name": row['filename'],
                        "path": row['path'],
                        "score": float(row.get('_distance', 0)),
                        "type": "file"
                    })
    except: pass

    return jsonify({"results": results})

@app.route('/action', methods=['POST'])
def action_endpoint():
    ensure_fast_model()

    try: req = request.get_json(force=True)
    except: return jsonify({"actions": []}), 400

    query = req.get('query', "").strip()
    if not query: return jsonify({"actions": []})

    # 1. Shortcuts
    if query.lower() in COMMON_SHORTCUTS:
        url = COMMON_SHORTCUTS[query.lower()]
        act = {
                "type": "link",
                "url": url,
                "title": url.replace("https://", "").replace("www.", "").split('/')[0].title(),
                "description": f"Direct Shortcut"
            }
        return jsonify({"action": act, "actions": [act]})

    # 1.5 Brightness Regex (Hardcoded as requested)
    import re
    bright_match = re.search(r"(?:set|reduce|increase|max|min|make|screen)?\s*brightness\s*(?:to|of)?\s*(\d+)%?", query.lower())
    if bright_match:
        val = int(bright_match.group(1))
        # Log it
        logging.info(f"Brightness command detected: {val}%")
        act = {
            "type": "system_control",
            "control": "brightness",
            "value": val,
            "description": f"Set Brightness to {val}%"
        }
        return jsonify({"actions": [act], "action": act})

    # 1.6 Computer Control Hard Override
    # Fast Model often mistakes "click X on page" for "OPEN page".
    # We must force IGNORE here to let Main Model handle visual UI interaction.
    cc_keywords = ["click", "type", "scroll", "press", "copy", "paste", "move mouse", "drag", "select"]
    if any(k in query.lower() for k in cc_keywords):
        logging.info("Computer Control keyword detected. Skipping Fast Model.")
        return jsonify({"actions": []})

    # 2. LLM Inference
    system_prompt = """Classify INTENT. Output ONLY command: PERSON:[Name], PLACE:[Name], OPEN:[URL], OPEN_APP:[AppName], INSTALL:[App], CALC:[Expr], SEARCH:[Query], FORGET:[Fact], BRIGHTNESS:[Level].
If the query is asking for a local file, image, or picture, output IGNORE.
If the query is asking to click, type, scroll, or interact with the screen/UI, output IGNORE.

Ex:
calculate 2+2 -> CALC:2+2
install firefox -> INSTALL:firefox
install firefox -> INSTALL:firefox
open firefox -> OPEN_APP:firefox
forget that I like pizza -> FORGET:I like pizza
forget my name -> FORGET:My name
run obs -> OPEN_APP:obs
who is elon -> PERSON:Elon Musk
open google -> OPEN:https://google.com
search kittens -> SEARCH:kittens
find my notes -> IGNORE
picture of oskar -> IGNORE
set brightness to 50% -> BRIGHTNESS:50

Query: {query}"""
    user_prompt = f"Query: {query}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        with fast_lock:
            start_t = time.time()
            out = fast_model.create_chat_completion(
                messages=messages, max_tokens=64, temperature=0.1
            )
            end_t = time.time()
            dur = end_t - start_t
            tok_count = out.get('usage', {}).get('completion_tokens', 0)
            tps = tok_count / dur if dur > 0 else 0
            logging.info(f"FastModel (Action): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
            result_text = out['choices'][0]['message']['content'].strip()

        actions = []
        auto_actions = [] # Initialize auto_actions here for the action_endpoint
        for line in result_text.split('\n'):
            line = line.strip()
            if not line: continue

            if "CALC:" in line:
                try:
                    expr = line.split("CALC:")[1].strip()
                    res = perform_calculation(expr)
                    val = res.split("Result: ")[1].strip() if "Result: " in res else res
                    actions.append({"type": "calc", "content": val})
                except: pass

            if "FA:" in line:
                fact = line.split("FA:")[1].strip()
                if fact and "[Unknown]" not in fact:
                    logging.info(f"Extracted Fact: {fact}")
                    remember_fact(fact)
                    auto_actions.append({"type": "remember", "fact": fact, "description": f"Remembered: {fact}"})
            elif "UP:" in line:
                fact = line.split("UP:")[1].strip()
                if fact and "[Unknown]" not in fact:
                    logging.info(f"Extracted Update: {fact}")
                    remember_update(fact)
                    auto_actions.append({"type": "remember", "fact": fact, "description": f"Updated: {fact}"})
            elif "FORGET:" in line:
                fact = line.split("FORGET:")[1].strip()
                if delete_memory(fact):
                     auto_actions.append({"type": "forget", "fact": fact, "description": f"Forgot: {fact}"})
            elif "SEARCH:" in line:
                q = line.split("SEARCH:")[1].strip()
                nav = get_navigation_result(q)
                if nav:
                    # Add result as a Link
                    actions.append({"type": "link", "url": nav['url'], "title": nav['title'], "description": nav['description']})
                    
                    # Heuristic: If it looks like an App (official site matched), offer install
                    # Don't offer install if user explicitly asked for "search ..." (unless it matches well)
                    # Use the 'is_likely_app' flag from get_navigation_result
                    if nav.get('is_likely_app') and not "wiki" in q.lower():
                         actions.append({
                            "type": "install",
                            "name": q, # Use the query as the app name guess
                            "website": nav['url'],
                            "image": None 
                        })
                else:
                    url = f"https://duckduckgo.com/?q=!ducky+{q}"
                    actions.append({"type": "link", "url": url, "title": f"Search {q}", "description": "Web Search"})

            elif "PERSON:" in line:
                name = line.split("PERSON:")[1].strip()
                res = get_person_result(name)
                if res: actions.append(res)

            elif "PLACE:" in line:
                name = line.split("PLACE:")[1].strip()
                res = get_place_result(name)
                if res: actions.append(res)

            elif "INSTALL:" in line:
                app = line.split("INSTALL:")[1].strip()
                # Resolve metadata (icon, website) before showing card
                metadata = resolve_app_metadata(app)
                if metadata:
                    actions.append({
                        "type": "install",
                        "name": app,
                        "website": metadata.get("website"),
                        "image": metadata.get("image")
                    })
                else:
                    # Fallback if metadata resolution fails
                    actions.append({"type": "install", "name": app})

            elif "OPEN:" in line:
                url = line.split("OPEN:")[1].strip()
                actions.append({"type": "link", "url": url, "title": "Link", "description": "Open Link"})

            elif "OPEN_APP:" in line:
                app = line.split("OPEN_APP:")[1].strip()
                success, msg = find_and_launch_app(app)
                if success:
                    actions.append({"type": "status", "status": "success", "description": f"Opened {msg}"})
                else:
                    actions.append({"type": "status", "status": "error", "description": f"Could not find app '{app}'"})

            elif "BRIGHTNESS:" in line:
                val = line.split("BRIGHTNESS:")[1].strip().replace("%", "")
                try:
                    level = int(val)
                    actions.append({
                        "type": "system_control", 
                        "control": "brightness", 
                        "value": level,
                        "description": f"Set Brightness to {level}%"
                    })
                except: pass

        return jsonify({"actions": actions, "action": actions[0] if actions else None})

    except Exception as e:
        return jsonify({"actions": [], "error": str(e)})

@app.route('/install_plan', methods=['POST'])
def install_plan_endpoint():
    try: req = request.get_json(force=True)
    except: return jsonify({"error": "Bad JSON"}), 400

    app_name = req.get('app_name', '').strip()
    if not app_name: return jsonify({"error": "No app name"}), 400

    logging.info(f"Generating Install Plan for: {app_name}")

    # 1. APT CHECK
    try:
        cmd = ["apt-cache", "search", "--names-only", f"^{app_name}$"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            pkg_name = res.stdout.strip().split()[0]
            return jsonify({
                "method": "apt",
                "description": f"Found '{pkg_name}' in system repositories",
                "commands": [f"pkexec apt-get install -y {pkg_name}"]
            })
    except Exception as e:
        logging.error(f"Apt check failed: {e}")

    # 2. FLATPAK CHECK
    try:
        cmd = ["flatpak", "search", app_name]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().split('\n')
            if lines:
                parts = lines[0].split('\t')
                app_id = parts[2].strip() if len(parts) > 2 else next((p for p in lines[0].split() if '.' in p), None)

                if app_id:
                    return jsonify({
                        "method": "flatpak",
                        "description": f"Found '{app_id}' in Flatpak",
                        "commands": [f"flatpak install -y {app_id}"]
                    })
    except Exception as e:
        logging.error(f"Flatpak check failed: {e}")

    return jsonify({"method": "failed", "description": "Could not find package.", "commands": []})

def log_debug(msg):
    try:
        from datetime import datetime
        with open("/tmp/omni_install.log", "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"LOG DEBUG FAILED: {e}", file=sys.stderr)

@app.route('/find_package', methods=['POST'])
def find_package_endpoint():
    try: req = request.get_json(force=True)
    except: return jsonify({"error": "Bad JSON"}), 400
    
    query = req.get('query', '').strip()
    log_debug(f"FIND_PACKAGE: Query='{query}'")
    
    # Aliases for common apps that might not match generic names
    COMMON_INSTALL_ALIASES = {
        "messenger": ["caprine", "facebook messenger"],
        "facebook": ["caprine", "messenger"],
        "word": ["libreoffice"],
        "excel": ["libreoffice"],
        "powerpoint": ["libreoffice"],
        "photoshop": ["gimp", "krita"],
        "illustrator": ["inkscape"],
        "chrome": ["google-chrome-stable", "chromium-browser"],
        "google chrome": ["google-chrome-stable", "chromium-browser"],
        "vscode": ["code"],
        "code": ["code"]
    }
    
    # Expand query with aliases
    search_queries = [query]
    if query.lower() in COMMON_INSTALL_ALIASES:
        search_queries.extend(COMMON_INSTALL_ALIASES[query.lower()])
        log_debug(f"Expanded query '{query}' to: {search_queries}")
    
    candidates = []
    executed_commands = []
    
    seen_names = set()

    for q in search_queries:
        # APT
        try:
            cmd = ["apt-cache", "search", q]
            executed_commands.append(" ".join(cmd))
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                lines = res.stdout.strip().split('\n')
                # If query was specific (e.g. google-chrome-stable), apt-cache search might return many unrelated things
                # unless we filter? No, apt-cache search searches description too.
                # Heuristic: limit to top 10 to avoid overwhelming
                for line in lines[:10]:
                    if not line.strip(): continue
                    parts = line.split(' - ', 1)
                    name = parts[0].strip()
                    desc = parts[1].strip() if len(parts) > 1 else ""
                    
                    if name not in seen_names:
                        candidates.append({"name": name, "display_name": name, "description": desc, "manager": "apt"})
                        seen_names.add(name)
        except Exception as e:
            log_debug(f"APT Search Error: {e}")

        # FLATPAK
        try:
            cmd = ["flatpak", "search", q]
            executed_commands.append(" ".join(cmd))
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.strip().split('\n')[:5]:
                    parts = line.split('\t')
                    if len(parts) >= 3:
                         # parts[0]=Name (Steam), parts[1]=Desc, parts[2]=ID (com.valvesoftware.Steam)
                         # Sometimes checking all parts is safer as format can vary slightly or be truncated
                         # Standard: name, description, application_id, version, branch, remotes
                         
                         # Check if we have enough parts.
                         name_col = parts[0].strip()
                         desc_col = parts[1].strip()
                         id_col = parts[2].strip()
                         
                         if id_col not in seen_names:
                             c = {
                                 "name": id_col, # ID for install
                                 "display_name": name_col, # Name for humans/AI
                                 "description": desc_col,
                                 "manager": "flatpak"
                             }
                             candidates.append(c)
                             seen_names.add(id_col)
        except: pass
    
    
    # SMART SORT: Prioritize matches
    def score_candidate(c):
        q = query.lower()
        n = c.get('name', '').lower()
        dn = c.get('display_name', '').lower()
        desc = c.get('description', '').lower()
        
        # Super Boost: Exact alias match (e.g. Caprine for Messenger)
        if q in COMMON_INSTALL_ALIASES:
             for alias in COMMON_INSTALL_ALIASES[q]:
                 if alias in n or alias in dn: return -1

        # 0 is best
        if n == q or dn == q: return 0
        if n.startswith(q) or dn.startswith(q): return 1
        if q in n or q in dn: return 2
        if q in desc: return 3
        return 4

    candidates.sort(key=score_candidate)
    
    log_debug(f"Found {len(candidates)} candidates. Top: {[c.get('display_name', c['name']) for c in candidates[:5]]}")
    return jsonify({"candidates": candidates, "executed_commands": executed_commands})

@app.route('/pick_package', methods=['POST'])
def pick_package_endpoint():
    try: req = request.get_json(force=True)
    except: return jsonify({"error": "Bad JSON"}), 400
    
    app_name = req.get('app_name', '')
    candidates = req.get('candidates', [])
    
    if not candidates:
        log_debug("PICK_PACKAGE: No candidates provided")
        return jsonify({"selected": ""})
    
    log_debug(f"PICK_PACKAGE: Request='{app_name}', {len(candidates)} candidates")
    
    # Build candidate list for prompt
    candidate_list = ""
    for i, c in enumerate(candidates):
        name = c.get('display_name', c.get('name', 'unknown'))
        desc = c.get('description', 'no description')
        mgr = c.get('manager', 'unknown')
        candidate_list += f"{i+1}. {name} ({mgr})\n   Description: {desc}\n\n"
    
    prompt = (
        f"<|im_start|>system\nYou are a Linux Package Maintainer.\n"
        f"Task: Select the best matching package from a list.\n"
        f"Rules:\n"
        f"- Return JSON only: {{ \"index\": N }} where N is the 1-based index of the best match\n"
        f"- If NO package matches the user's request, return {{ \"index\": 0 }}\n"
        f"- Match loosely (e.g., 'steam' matches 'Steam' or 'com.valvesoftware.Steam')\n"
        f"<|im_end|>\n"
        f"<|im_start|>user\n"
        f"User wants to install: {app_name}\n\n"
        f"Available packages:\n{candidate_list}\n"
        f"Which package matches best?\n"
        f"<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    
    try:
        with main_lock:
             log_debug(f"Sending prompt to LLM:\n{prompt[:500]}...")
             output = llm(prompt, max_tokens=128, temperature=0.0)
             text = output['choices'][0]['text'].strip()
             log_debug(f"LLM Response Raw: {text}")
             
             import json
             if "{" in text and "}" in text:
                 text = text[text.find("{"):text.rfind("}")+1]
             
             data = json.loads(text)
             idx = data.get("index", 0)
             log_debug(f"Parsed index: {idx}")
             
             if idx > 0 and idx <= len(candidates):
                 selected = candidates[idx - 1]
                 log_debug(f"Selected: {selected.get('display_name', selected.get('name'))}")
                 return jsonify({"selected": selected})
                 
    except Exception as e:
        log_debug(f"VERIFY EXCEPTION: {e}")
        logging.error(f"Verify failed: {e}")
        
    return jsonify({"selected": ""})


def _startup_sequence():
    # Initialize Personal Memory
    global personal_mem
    try:
        if not os.path.exists(PERSONAL_MEM_PATH):
             logging.info("Creating new Personal Memory file...")
             memvid_sdk.create(PERSONAL_MEM_PATH, enable_vec=True, enable_lex=True)
        personal_mem = memvid_sdk.use('basic', PERSONAL_MEM_PATH)
        logging.info("Memvid Personal Memory initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize Memvid Personal Memory: {e}")

    time.sleep(2)
    ensure_model_loaded()
    # Warmup Fast Model to cache system prompt
    try:
         logging.info("Warming up Fast Model...")
         with fast_lock:
             # Use same system prompt as endpoint
             sys_p = """Classify INTENT. Output ONLY command: PERSON:[Name], PLACE:[Name], OPEN:[URL], INSTALL:[App], CALC:[Expr], SEARCH:[Query].

Ex:
calculate 2+2 -> CALC:2+2
install firefox -> INSTALL:firefox
who is elon -> PERSON:Elon Musk
open google -> OPEN:https://google.com
search kittens -> SEARCH:kittens

Query: {query}"""
             
             # Dummy query
             dummy = "Query: warmup"
             
             fast_model.create_chat_completion(
                 messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": dummy}],
                 max_tokens=1
             )
         logging.info("Fast Model Warmup Complete.")
    except Exception as e:
         logging.error(f"Warmup failed: {e}")

    # Start Live Indexer (watcher.py)
    try:
        watcher_path = os.path.join(os.path.dirname(__file__), "watcher.py")
        if os.path.exists(watcher_path):
            # Check if already running to avoid duplicates
            from subprocess import check_output
            try:
                # Use pgrep to check if watcher.py is already running by name
                check_output(["pgrep", "-f", "watcher.py"])
                logging.info("Live Indexer already running.")
            except:
                logging.info("Launching Live Indexer...")
                subprocess.Popen([sys.executable, watcher_path], start_new_session=True)
    except Exception as e:
        logging.error(f"Failed to start live indexer: {e}")

if __name__ == '__main__':
    threading.Thread(target=_startup_sequence, daemon=True).start()
    # CHANGED PORT FROM 5500 TO 5555 TO MATCH FRONTEND
    app.run(host='127.0.0.1', port=5555, threaded=True)
