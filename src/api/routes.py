import time
import json
import logging
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
        "fast_model_name": getattr(model_manager, 'FAST_MODEL_HF_ID', 'Unknown')
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
    model_manager.ensure_main_model()
    
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
        return jsonify({"actions": []})
    
    # Clear abort event for this new request to proceed
    # (set() was only for cancelling the old request)
    model_manager.abort_fast_event.clear()
    
    model_manager.ensure_fast_model()

    try: req = request.get_json(force=True)
    except: return jsonify({"actions": []}), 400

    query = req.get('query', "").strip()
    if not query: return jsonify({"actions": []})

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
        return jsonify({"action": act, "actions": [act]})

    # 1.5 Brightness Regex
    import re
    bright_match = re.search(r"(?:set|reduce|increase|max|min|make|screen)?\s*brightness\s*(?:to|of)?\s*(\d+)%?", query.lower())
    if bright_match:
        val = int(bright_match.group(1))
        logging.info(f"Brightness command detected: {val}%")
        act = {
            "type": "system_control",
            "control": "brightness",
            "value": val,
            "description": f"Set Brightness to {val}%"
        }
        return jsonify({"actions": [act], "action": act})

    # 1.6 Computer Control Hard Override
    cc_keywords = ["click", "type", "scroll", "press", "copy", "paste", "move mouse", "drag", "select"]
    if any(k in query.lower() for k in cc_keywords):
        logging.info("Computer Control keyword detected. Skipping Fast Model.")
        return jsonify({"actions": []})

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
             # We just RETURN the action, UI handles execution on Enter
             return jsonify({"actions": [{"type": "open_app", "name": app}]})
    
    # Install
    install_match = re.search(r"^install\s+(.+)$", query, re.IGNORECASE)
    if install_match:
        app = install_match.group(1).strip()
        logging.info(f"Regex Install: {app}")
        return jsonify({"actions": [{"type": "install", "name": app}]})

    # Calculate
    calc_match = re.search(r"^(?:calculate|calc|solve|what is)\s+([\d\+\-\*\/\(\)\.\s]+)$", query, re.IGNORECASE)
    if calc_match:
        expr = calc_match.group(1).strip()
        res = perform_calculation(expr)
        val = res.split("Result: ")[1].strip() if "Result: " in res else res
        logging.info(f"Regex Calc: {expr} -> {val}")
        return jsonify({"actions": [{"type": "calc", "content": val}]})
        
    # Open URL
    url_match = re.search(r"^(?:open|go to|visit)\s+(https?://[^\s]+|www\.[^\s]+|[a-z0-9]+\.[a-z]{2,}[^\s]*)$", query, re.IGNORECASE)
    if url_match:
        url = url_match.group(1).strip()
        if not url.startswith("http"): url = "https://" + url
        logging.info(f"Regex URL: {url}")
        title = url.replace("https://", "").replace("www.", "").split('/')[0]
        return jsonify({"actions": [{"type": "link", "url": url, "title": f"Open {title}", "description": "Open Website"}]})

    # 2. LLM Inference
    # Better prompt with more examples for different command types
    system_prompt = """Extract the user's intent and output ONE command:
- PERSON:Name (for "who is", "biography of", names)
- PLACE:Name (for "where is", "find", locations)  
- OPEN:url (for "open", "go to", URLs)
- OPEN_APP:name (for "run", "launch", desktop apps)
- INSTALL:name (for "install", "download", software)
- SEARCH:query (default for general queries)

Examples:
'who is elon' → PERSON:Elon Musk
'where is paris' → PLACE:Paris
'open google' → OPEN:google.com
'install chrome' → INSTALL:Chrome
'youtube' → SEARCH:youtube"""
    
    user_prompt = f"Query: {query}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        logging.info(f"Starting Fast Model inference for: '{query}' (request_id: {request_id})")
        
        # FAIL FAST: Check if we are already obsolete before waiting for the lock
        if model_manager.current_fast_request_id != request_id:
            logging.info(f"Request {request_id} superseded before lock acquisition.")
            return jsonify({"actions": []})

        # Use a timeout for the lock to prevent permanent deadlocks
        # If we can't get the lock in 5 seconds, it's likely stuck or very busy.
        if not model_manager.fast_lock.acquire(timeout=5.0):
             logging.error("Failed to acquire fast_lock after 5 seconds.")
             return jsonify({"actions": [], "error": "Fast model busy"})

        try:
            # Check if this request was already cancelled
            if model_manager.current_fast_request_id != request_id:
                logging.info(f"Request {request_id} was cancelled before starting")
                return jsonify({"actions": []})
            
            if not model_manager.fast_model:
                 logging.error("Fast model is not loaded after ensure_fast_model() call.")
                 return jsonify({"actions": [], "error": "Fast model not loaded"})

            start_t = time.time()
            if hasattr(model_manager.fast_model, 'reset'): model_manager.fast_model.reset()
            
            # Debug: Check device and ensure GPU
            try:
                dev = model_manager.fast_model.model.device
                if "cpu" in str(dev).lower() and torch.cuda.is_available():
                    logging.warning(f"Fast model is on CPU ({dev})! Attempting to move to GPU...")
                    model_manager.fast_model.model.to("cuda:0")
                    torch.cuda.synchronize()
            except: pass

            out = model_manager.fast_model.create_chat_completion(
                messages=messages, max_tokens=64, temperature=0.0, request_id=request_id
            )
            
            if out is None:
                logging.info(f"Fast request {request_id} returned None (aborted).")
                return jsonify({"actions": []})
            
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            
            # Check if this request was cancelled during inference
            if model_manager.current_fast_request_id != request_id:
                logging.info(f"Request {request_id} was cancelled during inference")
                return jsonify({"actions": []})
            
            end_t = time.time()
            dur = end_t - start_t
            tok_count = out.get('usage', {}).get('completion_tokens', 0)
            tps = tok_count / dur if dur > 0 else 0
            logging.info(f"FastModel (Action): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
            result_text = out['choices'][0]['message']['content'].strip()
            
            # Remove thinking blocks from Qwen (Handle unclosed tags too)
            import re
            result_text = re.sub(r'<think>.*?(?:</think>|$)', '', result_text, flags=re.DOTALL).strip()
            
            logging.info(f"\n=== FAST MODEL OUTPUT ===\n{result_text}\n=========================\n")
        finally:
            model_manager.fast_lock.release()

        # Fallback: if output is empty, default to search
        if not result_text or not result_text.strip():
            logging.info(f"Empty model output, defaulting to SEARCH for '{query}'")
            result_text = f"SEARCH:{query}"
        
        # Also check if output contains only special tokens or is just newlines/spaces
        # by looking for actual keyword patterns
        has_command = any(cmd in result_text for cmd in ["PERSON:", "PLACE:", "OPEN:", "OPEN_APP:", "INSTALL:", "SEARCH:", "IGNORE", "CALC:", "FA:", "UP:", "FORGET:", "BRIGHTNESS:"])
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
                    val = res.split("Result: ")[1].strip() if "Result: " in res else res
                    actions.append({"type": "calc", "content": val})
                except: pass

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
                    return jsonify({"actions": []})

                raw_q = line.split("SEARCH:")[1].strip()
                q = _sanitize_search_query(raw_q, query)
                if q != raw_q:
                    logging.info(f"Fast model SEARCH query sanitized: '{raw_q[:80]}' -> '{q}'")
                
                # Get search results to analyze
                from src.services.search.web_search import search_api
                results = search_api(q, categories='general', fast=True)
                logging.info(f"[DEBUG] Got {len(results) if results else 0} raw search results for: '{q}'")
                
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
                        classification = model_manager.fast_model.create_chat_completion(
                            messages=classify_messages, max_tokens=8, temperature=0.0, request_id=request_id
                        )
                        if classification is None: raise Exception("Classification aborted")
                        
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
                                write_out = model_manager.fast_model.create_chat_completion(
                                    messages=write_messages, max_tokens=120, temperature=0.3, request_id=request_id
                                )
                                if write_out is None: raise Exception("Writing aborted")
                                
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
                                    img_results = search_api(person_name, categories='images')
                                    if img_results:
                                        img_url = img_results[0].get('img_src') or img_results[0].get('thumbnail') or img_results[0].get('url')
                                except Exception:
                                    pass
                                actions.append({
                                    "type": "person",
                                    "name": person_name or q,
                                    "description": person_desc or results[0].get('content', results[0].get('snippet', ''))[:200],
                                    "url": first_url,
                                    "image": img_url
                                })
                                continue
                            except Exception as e:
                                logging.error(f"[DEBUG] Person card generation failed: {e}, falling back to get_person_result")
                                person_result = get_person_result(q)
                                if person_result:
                                    actions.append(person_result)
                                    continue
                        
                        elif first_word == "PLACE":
                            logging.info(f"[DEBUG] Model chose PLACE for: {q}")
                            place_result = get_place_result(q)
                            if place_result:
                                actions.append(place_result)
                                continue
                    
                    except Exception as e:
                        logging.error(f"[DEBUG] Classification failed: {e}")
                
                # Fallback: treat as website search
                nav = get_navigation_result(q, fast=True)
                if nav:
                    logging.info(f"[DEBUG] Using navigation result (website): {nav['url']}")
                    actions.append({"type": "link", "url": nav['url'], "title": nav['title'], "description": nav['description']})
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
        logging.error(f"Error in action_endpoint: {e}")
        return jsonify({"actions": [], "error": str(e)})

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
