import time
import json
import logging
from flask import Blueprint, request, jsonify

from src.core.config import COMMON_SHORTCUTS
from src.services.llm.model_manager import ensure_fast_model, ensure_main_model, fast_model, fast_lock, db_conn, embed_model
from src.services.llm.chat import process_chat_request, perform_calculation
from src.services.search.web_search import get_navigation_result, get_person_result, get_place_result
from src.services.memory.memvid_store import remember_fact, remember_update, delete_memory
from src.services.system.app_launcher import find_and_launch_app, resolve_app_metadata
from src.services.system.installer import generate_install_plan, log_debug

api_bp = Blueprint('api', __name__)

@api_bp.route('/ask_llm', methods=['POST'])
def ask_llm():
    try: req = request.get_json(force=True)
    except: return jsonify({"answer": "Error: Bad JSON"}), 400

    query = req.get('query', ' '.strip())
    history = req.get('history', []) 
    screenshot_b64 = req.get('screenshot')

    logging.info(f"Received /ask_llm request. Query: {query}")
    
    response = process_chat_request(query, history, screenshot_b64)
    return jsonify(response)

@api_bp.route('/search', methods=['POST'])
def search_endpoint():
    ensure_main_model()
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

@api_bp.route('/action', methods=['POST'])
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
            if hasattr(fast_model, 'reset'): fast_model.reset()
            out = fast_model.create_chat_completion(
                messages=messages, max_tokens=256, temperature=0.1
            )
            end_t = time.time()
            dur = end_t - start_t
            tok_count = out.get('usage', {}).get('completion_tokens', 0)
            tps = tok_count / dur if dur > 0 else 0
            logging.info(f"FastModel (Action): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
            result_text = out['choices'][0]['message']['content'].strip()
            
            # Remove thinking blocks from Qwen
            import re
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL).strip()

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
                q = line.split("SEARCH:")[1].strip()
                nav = get_navigation_result(q)
                if nav:
                    actions.append({"type": "link", "url": nav['url'], "title": nav['title'], "description": nav['description']})
                    if nav.get('is_likely_app') and not "wiki" in q.lower():
                         actions.append({
                            "type": "install",
                            "name": q,
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
