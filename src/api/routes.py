import os
import time
import json
import logging
import re
from typing import Optional
import torch
from flask import Blueprint, request, jsonify, Response

from src.core.config import COMMON_SHORTCUTS
from src.services.llm import model_manager
from src.services.llm.chat import process_chat_request, perform_calculation, should_see_screen
from src.services.search.web_search import get_navigation_result, get_person_result, get_place_result
from src.services.memory.memvid_store import remember_fact, remember_update, delete_memory
from src.services.system.app_launcher import find_and_launch_app, resolve_app_metadata, get_app_cache
from src.services.system.installer import generate_install_plan, log_debug

api_bp = Blueprint('api', __name__)


@api_bp.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint that doesn't require model loading."""
    return jsonify({"status": "ok", "service": "brain"})


@api_bp.route('/status', methods=['GET'])
def status_check():
    """Check if models are loaded."""
    main_loaded = model_manager.llm is not None
    fast_loaded = model_manager.fast_model is not None
    return jsonify({
        "main_model_loaded": main_loaded,
        "fast_model_loaded": fast_loaded,
        "main_model_name": getattr(model_manager, 'MAIN_MODEL_FILENAME', 'Unknown'),
        "fast_model_name": getattr(model_manager, 'FAST_MODEL_FILENAME', 'Unknown')
    })


def _sanitize_search_query(extracted: str, original: str) -> str:
    """Use extracted search term only if it looks valid; otherwise use original query."""
    if not extracted or not extracted.strip():
        return original.strip()
    s = extracted.strip()
    if len(s) > 200:
        return original.strip()
    # Reject if mostly non-alphanumeric (garbage like '::::))) person))')
    alnum_or_space = sum(1 for c in s if c.isalnum() or c.isspace())
    if alnum_or_space < len(s) * 0.5:
        return original.strip()
    return s


def _extract_person_candidate(query: str) -> Optional[str]:
    """Return a cleaned person-name candidate when the query looks person-oriented."""
    if not query:
        return None

    raw = query.strip()
    q = raw.lower()

    # Skip command-like queries that should follow other action paths.
    command_prefixes = (
        "open ", "go to ", "visit ", "install ", "run ", "launch ", "start ",
        "calculate ", "calc ", "solve ", "set brightness", "increase brightness",
        "reduce brightness", "search ", "find ", "near "
    )
    if q.startswith(command_prefixes):
        return None

    # Skip URLs / domains / handles.
    if re.search(r"https?://|www\.", q):
        return None
    if re.search(r"\b[\w-]+\.(com|net|org|io|app|dev|ai|gov|edu|co)\b", q):
        return None
    if "@" in raw:
        return None

    explicit_prefix = False
    for prefix in ("who is ", "who was ", "tell me about ", "biography of ", "bio of ", "about "):
        if q.startswith(prefix):
            raw = raw[len(prefix):].strip()
            explicit_prefix = True
            break

    if not raw:
        return None

    if any(ch.isdigit() for ch in raw):
        return None
    if any(ch in raw for ch in "/\\|#%&*=_+[]{}<>"):
        return None

    tokens = [t for t in raw.split() if t]
    if not tokens or len(tokens) > 4:
        return None

    # For implicit person queries (just typing a name), require at least two words.
    if not explicit_prefix and len(tokens) < 2:
        return None

    for token in tokens:
        cleaned = token.replace(".", "").replace("'", "").replace("-", "")
        if not cleaned or not cleaned.isalpha():
            return None

    return " ".join(tokens)


@api_bp.route('/ask_llm', methods=['POST'])
def ask_llm():
    # Signal Fast Model to abort any ongoing operations
    model_manager.abort_fast_event.set()
    
    try: req = request.get_json(force=True)
    except: return jsonify({"answer": "Error: Bad JSON"}), 400

    query = req.get('query', ' '.strip())
    history = req.get('history', []) 
    screenshot_b64 = req.get('screenshot')

    logging.info(f"Received /ask_llm request. Query: {query}")
    
    # Ensure model is loaded before processing
    try:
        model_manager.ensure_main_model()
    except Exception as e:
        logging.error(f"Failed to ensure main model: {e}")
        # Instead of 500, return a friendly error message
        return jsonify({"answer": f"I'm still waking up (Loading AI models...). Please try again in 30 seconds. Error: {str(e)}"}), 200

    if model_manager.init_error:
         return jsonify({"answer": f"I encountered an issue loading my AI brain: {model_manager.init_error}. Please check the logs."}), 200
    
    if not model_manager.llm:
         return jsonify({"answer": "I'm still loading my AI models. Please give me a moment."}), 200

    # Check if streaming is requested
    stream = req.get("stream", False)

    if stream:
        # Streaming response

        # First, check if we need a screenshot before calling the main chat pipeline.
        # This avoids hanging the streaming connection when the backend requests a screenshot.
        if not screenshot_b64 and should_see_screen(query):
            logging.info(f"[SCREENSHOT] Requesting Screenshot from Client for query: '{query}'")
            logging.info("[SCREENSHOT] Client has 5 seconds to capture and return the screenshot")

            def screenshot_stream():
                try:
                    # Send a special event so the client can trigger screenshot capture.
                    payload = {"type": "special", "special_action": "screenshot_required"}
                    yield f'data: {json.dumps(payload)}\n\n'
                except Exception as e:
                    yield f'data: {json.dumps({"type": "error", "error": str(e)})}\n\n'

            return Response(screenshot_stream(), mimetype="text/event-stream")

        def stream_generator():
            try:
                for msg_type, content in process_chat_request(query, history, screenshot_b64, stream=True):
                    if msg_type == "partial":
                        # content is dict with "thinking" and "answer"
                        thinking = content.get("thinking", "")
                        answer = content.get("answer", "")
                        # if thinking or answer:
                        #     logging.info(f"[STREAM] sending partial (thinking={len(thinking)} chars, answer={len(answer)} chars)")
                        yield f'data: {json.dumps({"type": "partial", "thinking": thinking, "answer": answer})}\n\n'
                    elif msg_type == "final":
                        # Send final response
                        yield f'data: {json.dumps({"type": "final", **content})}\n\n'
            except Exception as e:
                yield f'data: {json.dumps({"type": "error", "error": str(e)})}\n\n'

        return Response(stream_generator(), mimetype="text/event-stream")
    else:
        # Non-streaming response
        response = process_chat_request(query, history, screenshot_b64)
        return jsonify(response)

@api_bp.route('/search', methods=['POST'])
def search_endpoint():
    # Only ensure resources (DB/Embeddings) are loaded, NOT the main LLM
    model_manager.ensure_resources()
    
    # Protect shared resource access with search_lock
    with model_manager.search_lock:
        if not model_manager.db_conn or not model_manager.embed_model:
            return jsonify({"results": []})

        try: req = request.get_json(force=True)
        except: return jsonify({"results": []}), 400

        query = req.get('query', "").strip()
        if not query: return jsonify({"results": []})

        results = []
        try:
            tbl = model_manager.db_conn.open_table("files")
            # Encoding and searching must be thread-safe (hence the lock)
            res = tbl.search(model_manager.embed_model.encode(query)).limit(3).to_pandas()
            if not res.empty:
                for _, row in res.iterrows():
                    if row.get('_distance', 0) < 1.1:
                        results.append({
                            "name": row['filename'],
                            "path": row['path'],
                            "score": float(row.get('_distance', 0)),
                            "type": "file"
                        })
            
            # Log results for debugging
            if results:
                logging.info(f"Search found {len(results)} files for '{query}':")
                for r in results:
                    logging.info(f" - {r['name']} ({r['score']:.4f})")
            else:
                logging.info(f"Search found NO files for '{query}'")

        except Exception as e:
            logging.error(f"Search error: {e}")

    return jsonify({"results": results})

def _chip_site_name(url: str) -> str:
    """'https://www.tesla.com/path' → 'Tesla'"""
    _KNOWN = {
        "wikipedia": "Wikipedia",
        "youtube": "YouTube",
        "youtu": "YouTube",
        "duckduckgo": "DuckDuckGo",
        "google": "Google",
        "google": "Google",
        "github": "GitHub",
        "reddit": "Reddit",
        "twitter": "Twitter",
        "x": "X",
        "instagram": "Instagram",
        "linkedin": "LinkedIn",
        "facebook": "Facebook",
        "amazon": "Amazon",
        "apple": "Apple",
        "microsoft": "Microsoft",
    }
    try:
        from urllib.parse import urlparse as _up
        host = _up(url).netloc.lower().replace("www.", "")
        # For subdomains like "en.wikipedia.org" → use second part as key
        parts = host.split(".")
        key = parts[-2] if len(parts) >= 2 else parts[0]
        return _KNOWN.get(key, key.title()) if key else "Site"
    except Exception:
        return "Site"


_SEARCH_ENGINE_HOSTS = frozenset({
    "duckduckgo.com", "google.com", "bing.com", "search.yahoo.com",
    "startpage.com", "brave.com", "search.brave.com", "perplexity.ai",
    "you.com", "kagi.com",
})


def _is_search_engine_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse as _up
        host = _up(url).netloc.lower().replace("www.", "")
        # also handle subdomains like search.google.com
        parts = host.split(".")
        root = ".".join(parts[-2:]) if len(parts) >= 2 else host
        return root in _SEARCH_ENGINE_HOSTS
    except Exception:
        return False




@api_bp.route('/action', methods=['POST'])
def action_endpoint():
    # Create a unique request ID
    import uuid
    request_id = str(uuid.uuid4())
    
    global current_fast_request_id
    
    # Set this as the current request and signal any old requests to abort
    with model_manager.fast_queue_lock:
        old_request_id = model_manager.current_fast_request_id
        model_manager.current_fast_request_id = request_id
        
        # Log cancellation but rely on ID check in model loop to abort old request
        if old_request_id is not None:
            logging.info(f"Cancelling old fast request {old_request_id}, starting new request {request_id}")
            # Do NOT set abort_fast_event here, as it kills the *current* request too in the check below.
            # model_manager.abort_fast_event.set()
    
    # Check if abort was already set (meaning main AI model is starting)
    # If so, this action request should be aborted immediately
    if model_manager.abort_fast_event.is_set():
        logging.info(f"Action endpoint {request_id}: Abort event already set, skipping action request")
        return jsonify({"actions": [], "chips": []})
    
    # Clear abort event for this new request to proceed
    # (set() was only for cancelling the old request)
    model_manager.abort_fast_event.clear()
    
    model_manager.ensure_fast_model()

    try: req = request.get_json(force=True)
    except: return jsonify({"actions": [], "chips": []}), 400

    query = req.get('query', "").strip()
    if not query: return jsonify({"actions": [], "chips": []})

    logging.info(f"Action endpoint received query: '{query}' (request_id: {request_id})")
    
    # 1. Shortcuts
    if query.lower() in COMMON_SHORTCUTS:
        url = COMMON_SHORTCUTS[query.lower()]
        act = {
                "type": "link",
                "url": url,
                "title": url.replace("https://", "").replace("www.", "").split('/')[0].title(),
                "description": f"Direct Shortcut"
            }
        logging.info(f"Shortcut match: {url}")
        chips = []
        return jsonify({"action": act, "actions": [act], "chips": chips})

    # 1.5 System Settings (instant – no LLM needed)
    import re
    try:
        from src.services.system.macos_settings import detect_settings_command
        settings_act = detect_settings_command(query)
        if settings_act:
            logging.info(f"[settings] Fast-path action detected: {settings_act['setting']}")
            return jsonify({"actions": [settings_act], "action": settings_act, "chips": []})
    except Exception as _e:
        logging.warning(f"[settings] detect_settings_command failed: {_e}")

    # 1.6 Computer Control Hard Override
    cc_keywords = ["click", "type", "scroll", "press", "copy", "paste", "move mouse", "drag", "select"]
    if any(k in query.lower() for k in cc_keywords):
        logging.info("Computer Control keyword detected. Skipping Fast Model.")
        return jsonify({"actions": [], "chips": []})

    # 1.7 Regex Shortcuts (Speed Optimization)
    # Open App
    open_match = re.search(r"^(?:open|run|launch|start)\s+(?!http|www)(.+)$", query, re.IGNORECASE)
    if open_match:
        app = open_match.group(1).strip()
        # Check if it's a website in disguise
        if "." in app and " " not in app:
             pass # Let LLM handle it as OPEN:url
        else:
             logging.info(f"Regex Open App: {app}")
             act = {"type": "open_app", "name": app}
             return jsonify({"actions": [act], "chips": []})
    
    # Install
    install_match = re.search(
        r"^(?:install|zainstaluj|pobierz|pobierać|ściągnij|sciagnij|download)\s+(.+)$",
        query, re.IGNORECASE
    )
    if install_match:
        app = install_match.group(1).strip()
        logging.info(f"Regex Install: {app}")
        act = {"type": "install", "name": app}
        return jsonify({"actions": [act], "chips": []})

    # 1.7.5 Implicit Calculation
    # Check for simple math expressions like "12*3", "100/4", "5+5", "10-2"
    # We want to avoid matching dates or phone numbers if possible, but strict math is usually fine.
    # Regex: Start with digits/parens, contains at least one operator, ends with digits/parens.
    # Allowed chars: 0-9 . ( ) + - * / ^ % space
    if any(op in query for op in ['+', '-', '*', '/', '^', '%']):
         # clean check
         import re
         # Allow digits, operators, parens, spaces, dots
         if re.match(r'^[\d\s\.\(\)\+\-\*\/\^\%]+$', query):
             # Ensure at least one digit and one operator
             if re.search(r'\d', query) and re.search(r'[\+\-\*\/\^\%]', query):
                 try:
                     res = perform_calculation(query)
                     if "Error" not in res:
                         val = res.split("Result: ")[1].split("\n")[0].strip() if "Result: " in res else res
                         latex_match = re.search(r'LaTeX: \$(.*?)\$', res)
                         latex_eq = latex_match.group(1) if latex_match else f"{query} = {val}"
                         logging.info(f"Implicit Calc: {query} -> {val}")
                         # Return immediately to avoid search
                         calc_act = {"type": "calc", "content": val, "equation": latex_eq}
                         return jsonify({"actions": [calc_act], "chips": []})
                 except: pass

    # Calculate (Explicit)
    calc_match = re.search(r"^(?:calculate|calc|solve|what is)\s+([\d\+\-\*\/\(\)\.\s]+)$", query, re.IGNORECASE)
    if calc_match:
        expr = calc_match.group(1).strip()
        res = perform_calculation(expr)
        val = res.split("Result: ")[1].split("\n")[0].strip() if "Result: " in res else res
        latex_match = re.search(r'LaTeX: \$(.*?)\$', res)
        latex_eq = latex_match.group(1) if latex_match else f"{expr} = {val}"
        logging.info(f"Regex Calc: {expr} -> {val}")
        calc_act2 = {"type": "calc", "content": val, "equation": latex_eq}
        return jsonify({"actions": [calc_act2], "chips": []})

    # Open URL
    url_match = re.search(r"^(?:open|go to|visit)\s+(https?://[^\s]+|www\.[^\s]+|[a-z0-9]+\.[a-z]{2,}[^\s]*)$", query, re.IGNORECASE)
    if url_match:
        url = url_match.group(1).strip()
        if not url.startswith("http"): url = "https://" + url
        logging.info(f"Regex URL: {url}")
        title = url.replace("https://", "").replace("www.", "").split('/')[0]
        link_act = {"type": "link", "url": url, "title": f"Open {title}", "description": "Open Website"}
        return jsonify({"actions": [link_act], "chips": []})

    # New Fast Actions (Color, Timer, Password, QR)
    # 1. Color Preview
    color_match = re.match(r"^(#([a-fA-F0-9]{3}|[a-fA-F0-9]{6}))|rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", query.strip(), re.IGNORECASE)
    if color_match:
        from PyQt6.QtGui import QColor
        if color_match.group(1): # HEX
            c = QColor(color_match.group(1))
        else: # RGB
            c = QColor(int(color_match.group(3)), int(color_match.group(4)), int(color_match.group(5)))
        
        hex_val = c.name().upper()
        rgb_val = f"{c.red()}, {c.green()}, {c.blue()}"
        hsl_val = f"{c.hslHue()}, {c.hslSaturation()}, {c.lightness()}"
        act = {"type": "color_preview", "color_hex": hex_val, "rgb_val": rgb_val, "hsl_val": hsl_val}
        return jsonify({"actions": [act], "chips": []})

    # 2. Timer
    timer_match = re.match(r"^(?:set\s+)?timer(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*(s|sec|seconds|m|min|minutes|h|hr|hours)$", query.strip(), re.IGNORECASE)
    if timer_match:
        val = float(timer_match.group(1))
        unit = timer_match.group(2).lower()
        if unit in ['s', 'sec', 'seconds']: duration = val
        elif unit in ['m', 'min', 'minutes']: duration = val * 60
        else: duration = val * 3600
        act = {"type": "timer", "duration": int(duration)}
        return jsonify({"actions": [act], "chips": []})

    # 3. Password
    pwd_match = re.match(r"^(?:generate\s+)?(?:password|haslo|hasło)(?:\s+(\d+))?(?:\s*chars?)?$", query.strip(), re.IGNORECASE)
    if pwd_match:
        l = int(pwd_match.group(1)) if pwd_match.group(1) else 16
        act = {"type": "password", "length": min(128, max(4, l))}
        return jsonify({"actions": [act], "chips": []})

    # 4. QR Code
    qr_match = re.match(r"^qr(?:code)?:\s*(.+)$", query.strip(), re.IGNORECASE)
    if qr_match:
        data = qr_match.group(1).strip()
        act = {"type": "qrcode", "data": data}
        return jsonify({"actions": [act], "chips": []})

    # 1.75 Currency Conversion Fast Path (regex → live rate, no LLM needed)
    _CURRENCY_RE = re.compile(
        r'^(?:convert\s+)?(\d+(?:[.,]\d+)?)\s*([a-zA-Z]{2,4})\s+(?:to|in|na|w|do|auf|en|à)\s+([a-zA-Z]{2,4})$',
        re.IGNORECASE
    )
    curr_m = _CURRENCY_RE.match(query.strip())
    if curr_m:
        amount_raw = curr_m.group(1).replace(',', '.')
        from_unit = curr_m.group(2).upper()
        to_unit = curr_m.group(3).upper()
        converted = ""
        try:
            import requests as _req
            resp = _req.get(
                f"https://api.frankfurter.app/latest?amount={amount_raw}&from={from_unit}&to={to_unit}",
                timeout=4
            )
            if resp.status_code == 200:
                rv = resp.json().get("rates", {}).get(to_unit)
                if rv is not None:
                    converted = f"{rv:,.2f}"
        except Exception as _ce:
            logging.warning(f"Currency API: {_ce}")
        if converted:
            logging.info(f"Regex Currency: {amount_raw} {from_unit} -> {converted} {to_unit}")
            return jsonify({"actions": [{"type": "currency", "amount": amount_raw,
                                         "from_unit": from_unit, "to_unit": to_unit,
                                         "converted_value": converted}], "chips": []})

    # 1.76 Translate Fast Path — short phrase with non-ASCII letters (clearly foreign)
    def _looks_foreign(text: str) -> bool:
        words = text.strip().split()
        return 1 <= len(words) <= 4 and any(ord(c) > 127 and c.isalpha() for c in text)

    if _looks_foreign(query):
        try:
            import locale as _locale
            _lc, _ = _locale.getdefaultlocale()
            _target_lang = _lc.split('_')[0].lower() if _lc else 'en'
        except Exception:
            _target_lang = 'en'
        model_manager.ensure_fast_model()
        if model_manager.fast_model:
            _tr_messages = [
                {"role": "system", "content": (
                    f"First, THINK inside <think>...</think> tags if the user's text actually needs translation to '{_target_lang}', "
                    f"or if it is already in '{_target_lang}', a proper noun, or a regular search query. "
                    "If it clearly needs translation because it's in a different foreign language, output exactly:\n"
                    f"TRANSLATE:original|from_lang|{_target_lang}|translation\n"
                    "Otherwise, output exactly: SKIP"
                )},
                {"role": "user", "content": query},
            ]
            try:
                if model_manager.fast_lock.acquire(timeout=5):
                    try:
                        _tr_out = model_manager.fast_model.create_chat_completion(
                            messages=_tr_messages, max_tokens=150, temperature=0.0,
                            request_id=request_id,
                        )
                    finally:
                        model_manager.fast_lock.release()
                    if _tr_out:
                        _tr_text = _tr_out['choices'][0]['message']['content'].strip()
                        _tr_text = re.sub(r'<think>.*?(?:</think>|$)', '', _tr_text, flags=re.DOTALL).strip()
                        logging.info(f"Translate fast path output: {_tr_text!r}")
                        if "TRANSLATE:" in _tr_text:
                            _parts = _tr_text.split("TRANSLATE:")[1].strip().split("|")
                            if len(_parts) >= 4:
                                return jsonify({"actions": [{
                                    "type": "translate",
                                    "source_text": _parts[0].strip(),
                                    "from_lang": _parts[1].strip(),
                                    "to_lang": _parts[2].strip(),
                                    "translated_text": "|".join(_parts[3:]).strip(),
                                }], "chips": []})
            except Exception as _te:
                logging.warning(f"Translate fast path: {_te}")

    # 1.8 SEARCH FIRST (Workflow Optimization)
    # Perform general search immediately to provide context for the LLM.
    # This avoids the "LLM guesses -> LLM says SEARCH -> Backend searches" round trip.
    from src.services.search.web_search import search_api
    
    logging.info(f"Performing pre-emptive search for: '{query}'")
    search_results = search_api(query, categories='general', fast=True)
    
    # Build search context for LLM
    search_context = ""
    if search_results:
        search_context = "Search results:\n"
        for i, res in enumerate(search_results[:4], 1): # Top 4 results
            title = res.get('title', 'N/A')
            content = (res.get('content') or res.get('snippet', '') or 'N/A')
            if len(content) > 300: content = content[:300] + "..."
            url = res.get('url', 'N/A')
            search_context += f"\n--- Result {i} ---\nTitle: {title}\nDescription: {content}\nURL: {url}\n"
    else:
        search_context = "Search results: No results found."

    logging.info(f"Search Context prepared ({len(search_context)} chars)")

    # 2. LLM Inference
    # Better prompt with more examples for different command types
    system_prompt = """You are an intelligent action classifier.
Analyze the user query and the provided search results to decide the best action.

First, THINK step-by-step inside <think>...</think> tags. Evaluate:
1. What is the user's EXACT core intent?
2. Are they EXPLICITLY asking for Weather, Translation, Currency, or Unit conversion? (Do not trigger these if it's just a casual message or a name).
3. ONLY trigger a fast action if you are highly confident it's the primary intent.
4. If it's a casual message, a greeting, or you are unsure, default to SEARCH:query.

After thinking, output ONE command only on a new line:
- TRANSLATE:source_text|from_lang|to_lang|translated_text (only if explicitly asking to translate, or typing a purely foreign phrase expecting translation)
- CURRENCY:amount|from_unit|to_unit|converted_value (e.g. "22usd to pln")
- WEATHER:location|temp|condition (only if explicitly asking for weather)
- UNIT:amount|from_unit|to_unit|converted_value
- PERSON:Name (search results strongly confirm real person/biography)
- PLACE:Name (results confirm location/city)
- OPEN:url (results show specific official website)
- INSTALL:name (results show downloadable software/app)
- UNINSTALL:name (user explicitly wants to remove software)
- SEARCH:query (general topic, unclear, or conversational)
- COLOR:hex|rgb|hsl (e.g. COLOR:#FF0000|255,0,0|0,100,50)
- TIMER:duration_in_seconds (e.g. TIMER:300 for 5 minutes)
- PASSWORD:length (e.g. PASSWORD:16)
- QRCODE:data (e.g. QRCODE:https://google.com)
- SYSTEM_SETTINGS:{"type":"system_settings","setting":"dark_mode|brightness|volume|mute|night_shift|dnd|wifi|bluetooth","value":true/false or 0-100}

Examples:
<think>The user said "amor", a simple foreign word. They likely want a translation.</think>
TRANSLATE:amor|es|pl|miłość

<think>User asked "co tam". This is a conversational greeting, no fast action needed.</think>
SEARCH:co tam

<think>User wants weather in London. Results say 15C and Cloudy.</think>
WEATHER:London|15°C|Partly Cloudy

<think>User wants to open safelabs. Result 1 is the official site.</think>
OPEN:https://safelabs.info

<think>User typed "zmień tryb na ciemny". This is a system setting request.</think>
SYSTEM_SETTINGS:{"type":"system_settings","setting":"dark_mode","value":true}
"""
    
    user_prompt = f"Query: {query}\n\n{search_context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    def _safe_fast_completion(messages, max_tokens, temperature, step_name, reset_model=False):
        """Run fast model inference under lock with request-abort checks."""
        if model_manager.current_fast_request_id != request_id:
            logging.info(f"{step_name}: request {request_id} superseded before lock.")
            return None

        if not model_manager.fast_lock.acquire(timeout=5.0):
            logging.error(f"{step_name}: failed to acquire fast_lock after 5 seconds.")
            return None

        try:
            if model_manager.current_fast_request_id != request_id:
                logging.info(f"{step_name}: request {request_id} cancelled before inference.")
                return None
            if not model_manager.fast_model:
                logging.error(f"{step_name}: fast model is not loaded.")
                return None
            if reset_model and hasattr(model_manager.fast_model, 'reset'):
                model_manager.fast_model.reset()

            out = model_manager.fast_model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id
            )
            if out is None:
                logging.info(f"{step_name}: completion aborted/empty for request {request_id}.")
                return None
            return out
        except Exception as e:
            logging.error(f"{step_name}: fast model inference failed: {e}")
            return None
        finally:
            model_manager.fast_lock.release()

    try:
        logging.info(f"Starting Fast Model inference for: '{query}' (request_id: {request_id})")
        start_t = time.time()

        out = _safe_fast_completion(
            messages=messages,
            max_tokens=256,
            temperature=0.0,
            step_name="Action intent",
            reset_model=True
        )
        if out is None:
            return jsonify({"actions": [], "chips": []})

        if torch.cuda.is_available():
            torch.cuda.synchronize()

        # Check if this request was cancelled during inference
        if model_manager.current_fast_request_id != request_id:
            logging.info(f"Request {request_id} was cancelled during inference")
            return jsonify({"actions": [], "chips": []})

        end_t = time.time()
        dur = end_t - start_t
        tok_count = out.get('usage', {}).get('completion_tokens', 0)
        tps = tok_count / dur if dur > 0 else 0
        logging.info(f"FastModel (Action): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
        result_text = out['choices'][0]['message']['content'].strip()

        # Remove thinking blocks from Qwen (Handle unclosed tags too)
        result_text = re.sub(r'<think>.*?(?:</think>|$)', '', result_text, flags=re.DOTALL).strip()

        logging.info(f"\n=== FAST MODEL OUTPUT ===\n{result_text}\n=========================\n")

        # Fallback: if output is empty, default to search
        if not result_text or not result_text.strip():
            logging.info(f"Empty model output, defaulting to SEARCH for '{query}'")
            result_text = f"SEARCH:{query}"
        
        # Also check if output contains only special tokens or is just newlines/spaces
        # by looking for actual keyword patterns
        has_command = any(cmd in result_text for cmd in [
            "PERSON:", "PLACE:", "OPEN:", "OPEN_APP:", "INSTALL:", "UNINSTALL:", "SEARCH:",
            "IGNORE", "CALC:", "FA:", "UP:", "FORGET:", "BRIGHTNESS:",
            "CURRENCY:", "TRANSLATE:", "SYSTEM_SETTINGS:", "WEATHER:", "UNIT:",
            "COLOR:", "TIMER:", "PASSWORD:", "QRCODE:"
        ])
        if not has_command:
            logging.info(f"No recognized commands in output '{result_text[:100]}', defaulting to SEARCH for '{query}'")
            result_text = f"SEARCH:{query}"

        actions = []
        for line in result_text.split('\n'):
            line = line.strip()
            if not line: continue

            if "CALC:" in line:
                try:
                    expr = line.split("CALC:")[1].strip()
                    res = perform_calculation(expr)
                    # Extract result and LaTeX
                    val = res.split("Result: ")[1].strip() if "Result: " in res else res
                    latex_match = re.search(r'LaTeX: \$(.*?)\$', res)
                    latex_eq = latex_match.group(1) if latex_match else f"{expr} = {val}"
                    actions.append({"type": "calc", "content": val, "equation": latex_eq})
                except: pass

            if "CURRENCY:" in line:
                try:
                    raw_content = line.split("CURRENCY:")[1].strip()
                    parts = raw_content.split("|")
                    if len(parts) >= 3:
                        amount = parts[0].strip()
                        from_unit = parts[1].strip().upper()
                        to_unit = parts[2].strip().upper()
                        llm_converted = parts[3].strip() if len(parts) >= 4 else ""

                        # Try live exchange rate via Frankfurter (free, no API key)
                        converted = llm_converted
                        try:
                            import requests as _req
                            resp = _req.get(
                                f"https://api.frankfurter.app/latest?amount={amount}&from={from_unit}&to={to_unit}",
                                timeout=4
                            )
                            if resp.status_code == 200:
                                rate_data = resp.json()
                                rate_val = rate_data.get("rates", {}).get(to_unit)
                                if rate_val is not None:
                                    converted = f"{rate_val:,.2f}"
                                    logging.info(f"Live rate: {amount} {from_unit} = {converted} {to_unit}")
                        except Exception as _re:
                            logging.warning(f"Exchange rate API failed ({_re}), using LLM estimate")

                        actions.append({
                            "type": "currency",
                            "amount": amount,
                            "from_unit": from_unit,
                            "to_unit": to_unit,
                            "converted_value": converted
                        })
                except Exception as e:
                    logging.error(f"Failed to parse CURRENCY action: {e}")

            if "WEATHER:" in line:
                try:
                    parts = line.split("WEATHER:")[1].strip().split("|")
                    if len(parts) >= 3:
                        actions.append({
                            "type": "weather",
                            "location": parts[0].strip(),
                            "temp": parts[1].strip(),
                            "condition": parts[2].strip()
                        })
                except Exception as e:
                    logging.error(f"Failed to parse WEATHER action: {e}")

            if "UNIT:" in line:
                try:
                    parts = line.split("UNIT:")[1].strip().split("|")
                    if len(parts) >= 4:
                        actions.append({
                            "type": "unit",
                            "amount": parts[0].strip(),
                            "from_unit": parts[1].strip(),
                            "to_unit": parts[2].strip(),
                            "converted_value": parts[3].strip()
                        })
                except Exception as e:
                    logging.error(f"Failed to parse UNIT action: {e}")

            if "COLOR:" in line:
                try:
                    parts = line.split("COLOR:")[1].strip().split("|")
                    actions.append({
                        "type": "color_preview",
                        "color_hex": parts[0].strip() if len(parts) > 0 else "#FFFFFF",
                        "rgb_val": parts[1].strip() if len(parts) > 1 else "",
                        "hsl_val": parts[2].strip() if len(parts) > 2 else ""
                    })
                except Exception as e: pass

            if "TIMER:" in line:
                try:
                    val = line.split("TIMER:")[1].strip()
                    actions.append({"type": "timer", "duration": int(val)})
                except Exception as e: pass

            if "PASSWORD:" in line:
                try:
                    val = line.split("PASSWORD:")[1].strip()
                    actions.append({"type": "password", "length": int(val)})
                except Exception as e: pass

            if "QRCODE:" in line:
                try:
                    val = line.split("QRCODE:")[1].strip()
                    actions.append({"type": "qrcode", "data": val})
                except Exception as e: pass

            if "FA:" in line:
                fact = line.split("FA:")[1].strip()
                if fact and "[Unknown]" not in fact:
                    logging.info(f"Extracted Fact: {fact}")
                    remember_fact(fact)
            elif "UP:" in line:
                fact = line.split("UP:")[1].strip()
                if fact and "[Unknown]" not in fact:
                    logging.info(f"Extracted Update: {fact}")
                    remember_update(fact)
            elif "FORGET:" in line:
                fact = line.split("FORGET:")[1].strip()
                delete_memory(fact)
            elif "SEARCH:" in line:
                if model_manager.current_fast_request_id != request_id:
                    logging.info(f"Fast Action Aborted (request {request_id} cancelled by newer request).")
                    return jsonify({"actions": [], "chips": []})

                raw_q = line.split("SEARCH:")[1].strip()
                q = _sanitize_search_query(raw_q, query)
                if q != raw_q:
                    logging.info(f"Fast model SEARCH query sanitized: '{raw_q[:80]}' -> '{q}'")
                
                # Use EXISTING results if query matches (or if LLM kept original query)
                # If LLM changed query significantly, we might need new search, 
                # but usually it's just "SEARCH:original_query".
                # To be safe: if q is similar to original query, reuse.
                # Since we already searched for 'query', if q == query, we reuse.
                
                results = []
                if q.lower() == query.lower() and search_results:
                     logging.info(f"Reusing {len(search_results)} existing search results for SEARCH action")
                     results = search_results
                else:
                     logging.info(f"Refetching search results for new query: '{q}'")
                     from src.services.search.web_search import search_api
                     results = search_api(q, categories='general', fast=True)
                
                if results:
                    # Build rich context from top 5 results - full descriptions for model to decide
                    context = "Search results:\n"
                    for i, res in enumerate(results[:3], 1):
                        title = res.get('title', 'N/A')
                        content = (res.get('content') or res.get('snippet', '') or 'N/A')
                        if len(content) > 400:
                            content = content[:400] + "..."
                        url = res.get('url', 'N/A')
                        context += f"\n--- Result {i} ---\n"
                        context += f"Title: {title}\n"
                        context += f"Description: {content}\n"
                        context += f"URL: {url}\n"
                    
                    logging.info(f"[DEBUG] Search results sent to fast model ({len(context)} chars):\n{context}")
                    
                    # Use fast model to classify based on the descriptions only
                    classify_messages = [
                        {
                            "role": "system",
                            "content": """Analyze search results and classify what the user is looking for.

Reply with ONLY ONE WORD: PERSON, PLACE, or SEARCH

PERSON: Real people, biography, historical figure, celebrity (e.g., "Albert Einstein", "Napoleon")
PLACE: Cities, countries, landmarks, addresses, geographic locations (e.g., "Paris", "Tokyo Tower", "France")
SEARCH: Companies, products, websites, brands, topics, services (e.g., "YouTube", "Spotify", "how to cook")

Look at the result titles and descriptions to decide. If results mention "city", "capital", "country", "address", "landmark", "monument", it's PLACE. If they mention a real person's life/biography, it's PERSON. Otherwise SEARCH.

Output exactly one word."""
                        },
                        {
                            "role": "user",
                            "content": f"User query: {query}\n\n{context}\n\nAnswer with one word only: PERSON, PLACE, or SEARCH"
                        }
                    ]
                    
                    try:
                        classification = _safe_fast_completion(
                            messages=classify_messages,
                            max_tokens=8,
                            temperature=0.0,
                            step_name="Search classification"
                        )
                        if classification is None:
                            raise Exception("Classification aborted")
                        
                        classification_text = classification['choices'][0]['message']['content'].strip().upper()
                        # Strip non-ASCII (Qwen can emit Chinese/garbage when confused); then match PERSON/PLACE/SEARCH
                        import re
                        normalized = re.sub(r'[^A-Za-z]', '', classification_text)
                        first_word = ""
                        if normalized.startswith('PERSON'):
                            first_word = "PERSON"
                        elif normalized.startswith('PLACE'):
                            first_word = "PLACE"
                        elif normalized.startswith('SEARCH'):
                            first_word = "SEARCH"
                        logging.info(f"[DEBUG] Fast model classification: '{classification_text[:60]}' -> normalized '{normalized[:20]}' -> '{first_word}'")
                        
                        # Act on model decision only - let the model decide
                        if first_word == "PERSON":
                            logging.info(f"[DEBUG] Model chose PERSON - fast model will write the card from search results")
                            # Have fast model write the card (name + description) from search results, not raw copy-paste
                            write_messages = [
                                {
                                    "role": "system",
                                    "content": "Based on the search results, write a short person card. Output exactly two lines:\nNAME: [person's full name only, nothing else]\nDESCRIPTION: [1-2 sentences summarizing who they are, in the same language as the results. No URLs, no 'source:', no raw snippets.]"
                                },
                                {
                                    "role": "user",
                                    "content": f"Search results about: {query}\n\n{context}\n\nWrite the person card:"
                                }
                            ]
                            try:
                                write_out = _safe_fast_completion(
                                    messages=write_messages,
                                    max_tokens=120,
                                    temperature=0.3,
                                    step_name="Person card generation"
                                )
                                if write_out is None:
                                    raise Exception("Writing aborted")
                                
                                card_text = write_out['choices'][0]['message']['content'].strip()
                                logging.info(f"[DEBUG] Fast model person card output:\n{card_text}")
                                # Parse NAME: and DESCRIPTION:
                                person_name = q
                                person_desc = ""
                                for part in card_text.split("\n"):
                                    part = part.strip()
                                    if not part:
                                        continue
                                    if part.upper().startswith("NAME:"):
                                        person_name = part.split(":", 1)[1].strip()
                                    elif part.upper().startswith("DESCRIPTION:"):
                                        person_desc = part.split(":", 1)[1].strip()
                                if not person_desc:
                                    # Fallback: use rest of card as description (skip first line if it looks like name)
                                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                                    if len(lines) >= 2:
                                        person_desc = " ".join(lines[1:])
                                    elif lines:
                                        person_desc = lines[0] if "NAME:" not in lines[0].upper() else ""
                                # Treat garbage (e.g. "::.") or non-name as missing
                                if not person_name or person_name == q or len(person_name.strip()) < 2 or not any(c.isalpha() for c in person_name):
                                    # Use first result title to extract name if model didn't
                                    first_title = results[0].get('title', '')
                                    person_name = first_title.split('|')[0].split('–')[0].strip() or q
                                # URL from first result
                                first_url = results[0].get('url', '')
                                # Optional: get image
                                img_url = None
                                try:
                                    # Use existing results for image if possible?
                                    # No, search_api needs explicit categories='images'.
                                    # But we can try to find image in current results?
                                    # Usually general results don't have good image URLs unless enriched.
                                    # Let's check existing results first if we have them.
                                    pass
                                except Exception:
                                    pass
                                
                                # Use get_person_result with fallback to avoid re-search
                                person_res_fallback = get_person_result(person_name, existing_results=results)
                                if person_res_fallback:
                                     actions.append(person_res_fallback)
                                else:
                                     actions.append({
                                        "type": "person",
                                        "name": person_name or q,
                                        "description": person_desc or results[0].get('content', results[0].get('snippet', ''))[:200],
                                        "url": first_url,
                                        "image": None
                                    })
                                continue
                            except Exception as e:
                                logging.error(f"[DEBUG] Person card generation failed: {e}, falling back to get_person_result")
                                person_result = get_person_result(q, existing_results=results)
                                if person_result:
                                    actions.append(person_result)
                                    continue
                        
                        elif first_word == "PLACE":
                            logging.info(f"[DEBUG] Model chose PLACE for: {q}")
                            place_result = get_place_result(q, existing_results=results)
                            if place_result:
                                actions.append(place_result)
                                continue
                    
                    except Exception as e:
                        logging.error(f"[DEBUG] Classification failed: {e}")
                
                # If query still looks like a person, prefer person card over website link.
                person_candidate = _extract_person_candidate(query) or _extract_person_candidate(q)
                if person_candidate:
                    person_result = get_person_result(person_candidate, existing_results=results)
                    if person_result:
                        logging.info(f"[DEBUG] Heuristic person card fallback for: {person_candidate}")
                        actions.append(person_result)
                        continue

                # Fallback: treat as website search
                nav = get_navigation_result(q, fast=True, existing_results=results)
                if nav:
                    logging.info(f"[DEBUG] Using navigation result (website): {nav['url']}")
                    actions.append({"type": "link", "url": nav['url'], "title": nav['title'], "description": nav['description']})
                else:
                    url = f"https://www.google.com/search?q={q}"
                    actions.append({"type": "link", "url": url, "title": f"Search {q}", "description": "Web Search"})

            elif "TRANSLATE:" in line:
                try:
                    raw_content = line.split("TRANSLATE:")[1].strip()
                    parts = raw_content.split("|")
                    
                    # Robust parsing:
                    if len(parts) >= 4:
                        source = parts[0]
                        from_lang = parts[1]
                        to_lang = parts[2]
                        translated = "|".join(parts[3:]) # Rejoin in case text contained |
                    elif len(parts) == 3:
                        # Common error: source|to_lang|translated (missing from_lang)
                        p1, p2, p3 = parts
                        # If p2 looks like a lang code (2-3 chars)
                        if len(p2.strip()) <= 3:
                             source, from_lang, to_lang, translated = p1, "auto", p2, p3
                        else:
                             # Fallback: assume source|from|translated ?? 
                             # Or just fail gracefully
                             logging.warning(f"TRANSLATE: parsed 3 parts, ambiguous: {parts}")
                             continue
                    elif len(parts) == 2:
                        # source|translated
                        source, translated = parts
                        from_lang = "auto"
                        to_lang = "en" # Safe default?
                    else:
                        logging.warning(f"TRANSLATE: expected 4 parts, got {len(parts)}: {parts}")
                        continue

                    actions.append({
                        "type": "translate",
                        "source_text": source.strip(),
                        "from_lang": from_lang.strip(),
                        "to_lang": to_lang.strip(),
                        "translated_text": translated.strip()
                    })
                except Exception as e:
                    logging.error(f"Failed to parse TRANSLATE action: {e}")

            elif "PERSON:" in line:
                name = line.split("PERSON:")[1].strip()
                res = get_person_result(name, existing_results=search_results if name.lower() in query.lower() else None)
                if res: actions.append(res)

            elif "PLACE:" in line:
                name = line.split("PLACE:")[1].strip()
                res = get_place_result(name, existing_results=search_results if name.lower() in query.lower() else None)
                if res: actions.append(res)

            elif "UNINSTALL:" in line:
                app = line.split("UNINSTALL:")[1].strip()
                logging.info(f"Action: UNINSTALL {app}")
                actions.append({"type": "uninstall", "name": app})

            elif "INSTALL:" in line:
                app = line.split("INSTALL:")[1].strip()
                metadata = resolve_app_metadata(app)
                if metadata:
                    actions.append({
                        "type": "install",
                        "name": app,
                        "website": metadata.get("website"),
                        "image": metadata.get("image")
                    })
                else:
                    actions.append({"type": "install", "name": app})

            elif "OPEN:" in line:
                url = line.split("OPEN:")[1].strip()
                if "http" not in url: url = "https://" + url
                
                # Generate a better title
                display_url = url.replace("https://", "").replace("http://", "").replace("www.", "")
                if "/" in display_url: display_url = display_url.split('/')[0]
                title = f"Open {display_url}"

                act = {"type": "link", "url": url, "title": title, "description": "Open Website"}
                logging.info(f"Generated Action: {act}")
                actions.append(act)

            elif "OPEN_APP:" in line:
                app = line.split("OPEN_APP:")[1].strip()
                success, msg = find_and_launch_app(app)
                if success:
                    actions.append({"type": "status", "status": "success", "description": f"Opened {msg}"})
                else:
                    actions.append({"type": "status", "status": "error", "description": f"Could not find app '{app}'"})

            elif "SYSTEM_SETTINGS:" in line:
                try:
                    import json
                    from src.services.system.macos_settings import SETTING_META, execute_setting
                    json_str = line.split("SYSTEM_SETTINGS:")[1].strip()
                    settings_act = json.loads(json_str)
                    
                    # Augment with meta data (icon, color, etc)
                    setting_name = settings_act.get("setting", "")
                    meta = SETTING_META.get(setting_name, {})
                    settings_act.update(meta)
                    if "label" not in settings_act:
                        settings_act["label"] = setting_name.replace("_", " ").title()
                        
                    execute_setting(settings_act)
                    actions.append(settings_act)
                except Exception as e:
                    logging.error(f"Failed to parse system_settings action: {e}")

        chips = []
        logging.info(f"Chips ({len(chips)}): {[c['label'] for c in chips]}")

        return jsonify({"actions": actions, "action": actions[0] if actions else None, "chips": chips})

    except Exception as e:
        logging.error(f"Error in action_endpoint: {e}")
        return jsonify({"actions": [], "chips": [], "error": str(e)})

@api_bp.route('/install_plan', methods=['POST'])
def install_plan_endpoint():
    try: req = request.get_json(force=True)
    except: return jsonify({"error": "Bad JSON"}), 400

    app_name = req.get('app_name', '').strip()
    if not app_name: return jsonify({"error": "No app name"}), 400

    plan = generate_install_plan(app_name)
    return jsonify(plan)

@api_bp.route('/find_package', methods=['POST'])
def find_package_endpoint():
    try: req = request.get_json(force=True)
    except: return jsonify({"error": "Bad JSON"}), 400
    
    query = req.get('query', '').strip()
    log_debug(f"FIND_PACKAGE: Query='{query}'")
    
    # Aliases
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
    
    search_queries = [query]
    if query.lower() in COMMON_INSTALL_ALIASES:
        search_queries.extend(COMMON_INSTALL_ALIASES[query.lower()])
        
    candidates = []
    seen = set()
    
    # 1. APT Search
    import subprocess
    for q in search_queries:
        try:
            cmd = ["apt-cache", "search", q]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.strip().split('\n')[:5]: # Top 5 per query
                    if not line: continue
                    parts = line.split(' - ', 1)
                    if len(parts) == 2:
                        pkg, desc = parts
                        if pkg in seen: continue
                        seen.add(pkg)
                        candidates.append({
                            "name": pkg,
                            "display_name": pkg,
                            "description": desc,
                            "source": "apt"
                        })
        except: pass

    # 2. Flatpak Search
    for q in search_queries:
        try:
            cmd = ["flatpak", "search", q]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                # Skip header
                lines = res.stdout.strip().split('\n')
                for line in lines:
                    if "Description" in line and "Application" in line: continue
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        # Name, Description, AppID, Version, Branch, Remotes
                        name = parts[0].strip()
                        desc = parts[1].strip()
                        app_id = parts[2].strip()
                        
                        if app_id in seen: continue
                        seen.add(app_id)
                        
                        candidates.append({
                            "name": app_id,
                            "display_name": name,
                            "description": desc,
                            "source": "flatpak"
                        })
        except: pass

    log_debug(f"Found {len(candidates)} candidates.")
    return jsonify({"candidates": candidates})

@api_bp.route('/pick_package', methods=['POST'])
def pick_package_endpoint():
    # Placeholder for LLM based picking logic if needed
    # For now returns ambiguous or first
    return jsonify({"selection": "ambiguous"})

@api_bp.route('/verify_package', methods=['POST'])
def verify_package_endpoint():
    return jsonify({"verified": True})


@api_bp.route('/classify_files', methods=['POST'])
def classify_files_endpoint():
    """Batch-classify files for content indexing using the fast model.

    Accepts: {"files": [{"filename": "foo.json", "path": "/a/b/c/foo.json"}, ...]}
    Returns: {"decisions": [1, 0, 1, ...]}  — 1 = index, 0 = skip

    Used by the indexer (Phase 2) to decide which files are worth semantic
    content embedding.  Routing through the brain reuses the already-
    authenticated Groq client instead of making raw HTTP calls.
    """
    model_manager.ensure_fast_model()
    if model_manager.fast_model is None:
        return jsonify({"error": "Fast model not available"}), 503

    data = request.get_json(silent=True) or {}
    files = data.get("files", [])
    if not files:
        return jsonify({"decisions": []})

    lines = []
    for i, f in enumerate(files):
        full_path = f.get("path", "")
        filename = f.get("filename", "")
        parts = [p for p in full_path.split(os.sep) if p]
        context = "/".join(parts[-3:-1]) if len(parts) >= 3 else "/".join(parts[:-1])
        lines.append(f"{i}: {filename}  (in: {context}/)")

    prompt = (
        "You are a strict filter for a personal desktop file search index.\n"
        "Decide which files are worth embedding for semantic search — i.e. a user could "
        "reasonably search for this file by describing its content.\n\n"
        "DEFAULT TO 0 (skip). Only output 1 if the file clearly contains unique, "
        "human-authored content a user would search for.\n\n"
        "Always output 0 for:\n"
        "- package.json / package files (npm/pip metadata, not user content)\n"
        "- Any CSS / stylesheet files\n"
        "- HTML entry-point shells (index.html, _document.tsx used as SPA roots)\n"
        "- robots.txt, manifest.json, .env, sitemap.xml\n"
        "- Auto-generated or boilerplate config (nodemon, babel, eslint, tailwind, etc.)\n"
        "- Test files (*test*, *spec*, *__tests__*)\n"
        "- Files in public/, static/, dist/, or build/ directories\n"
        "- Short utility/helper files under 20 meaningful lines\n\n"
        "Output 1 for:\n"
        "- README, docs, notes, .txt, .rtf, .docx (actual writing)\n"
        "- Core application source code with real logic (server.js, main.py, App.tsx, etc.)\n"
        "- Data files with real user-created content (not configs or generated output)\n"
        "- Scripts the user wrote (.sh, .py with actual logic)\n\n"
        "Reply with ONLY a JSON array of 0s and 1s, one per file, in order. "
        "No explanation.\n\n"
        "Files:\n" + "\n".join(lines)
    )

    try:
        result = model_manager.fast_model.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=len(files) * 4 + 20,
            temperature=0.0,
        )
        if result is None:
            return jsonify({"error": "Fast model returned no response"}), 503

        raw = result["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()

        decisions = json.loads(raw)
        # Clamp to valid 0/1 and pad/trim to match input length
        decisions = [int(bool(d)) for d in decisions]
        while len(decisions) < len(files):
            decisions.append(1)  # fail-open: include if response is short
        decisions = decisions[:len(files)]

        return jsonify({"decisions": decisions})

    except Exception as e:
        logging.warning(f"/classify_files error: {e}")
        return jsonify({"decisions": [1] * len(files)})  # fail-open


@api_bp.route('/embed', methods=['POST'])
def embed_endpoint():
    """Encode texts into vectors using the shared embedding model.

    Accepts: {"texts": ["text1", "text2", ...]}
    Returns: {"vectors": [[float, ...], ...]}

    Exposing this as an HTTP endpoint lets watcher/indexer reuse the
    already-loaded bge-m3 instance instead of each loading their own copy.
    """
    model_manager.ensure_resources()
    if model_manager.embed_model is None:
        return jsonify({"error": "Embedding model not available"}), 503

    data = request.get_json(silent=True) or {}
    texts = data.get("texts", [])
    if not texts:
        return jsonify({"vectors": []})

    try:
        vectors = model_manager.embed_model.encode(texts).tolist()
        return jsonify({"vectors": vectors})
    except Exception as e:
        logging.error(f"Embed endpoint error: {e}")
        return jsonify({"error": str(e)}), 500
