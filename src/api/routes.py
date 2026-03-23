import os
import time
import json
import logging
import re
import socket
import threading
from typing import Optional
from flask import Blueprint, request, jsonify, Response

from src.core.config import COMMON_SHORTCUTS
from src.services.llm import model_manager
from src.services.llm.chat import process_chat_request, perform_calculation, should_see_screen
from src.services.search.web_search import get_navigation_result, get_person_result, get_place_result, search_api
from src.services.memory.memvid_store import remember_fact, remember_update, delete_memory
from src.services.system.app_launcher import find_and_launch_app, resolve_app_metadata, get_app_cache
from src.services.system.installer import generate_install_plan, log_debug, get_package_metadata

api_bp = Blueprint('api', __name__)


_BROWSER_APPS = {"safari", "chrome", "google chrome", "firefox", "arc", "brave", "edge", "microsoft edge"}
_OS_NOISE_TITLES = {"spotlight", "control center", "notification center",
                    "login window", "screen saver"}


def _build_context_parts(recent: list, sessions: list) -> list:
    """Build a rich context response from activity data and sessions."""
    parts = []
    _sep_re = re.compile(r'\s+[—–-]\s+')

    if recent:
        # Per-app accumulator
        app_data = {}  # {app: {"project": str|None, "files": set, "pages": set, "total_s": float}}
        for a in recent:
            app = a.get('app_name') or ''
            if not app:
                continue
            title = a.get('window_title') or ''
            dur = a.get('duration_s') or 0
            app_lower = app.lower()

            # Skip OS chrome noise
            if title.strip().lower() in _OS_NOISE_TITLES:
                continue

            if app not in app_data:
                app_data[app] = {"project": None, "files": set(), "pages": set(), "total_s": 0.0}

            app_data[app]["total_s"] += dur

            if not title:
                continue

            # Browser apps: collect page titles
            if app_lower in _BROWSER_APPS:
                page = title.strip()
                if page and len(page) > 1:
                    app_data[app]["pages"].add(page)
                continue

            # IDE / other apps: parse "file — Project" format
            segments = _sep_re.split(title)
            segments = [s.strip() for s in segments if s.strip().lower() != app_lower]

            if len(segments) >= 2 and '.' in segments[0]:
                # "brain.log — OmniApp" → file + project
                app_data[app]["files"].add(segments[0])
                if not app_data[app]["project"]:
                    app_data[app]["project"] = segments[1]
            elif len(segments) >= 2:
                # "SomeTab — OmniApp" → just project
                if not app_data[app]["project"]:
                    app_data[app]["project"] = segments[-1]
            elif len(segments) == 1:
                seg = segments[0]
                if '.' in seg and not seg.startswith('http'):
                    app_data[app]["files"].add(seg)

        # Format one line per app
        for app, data in app_data.items():
            mins = max(1, int(data["total_s"] / 60))
            app_lower = app.lower()

            if app_lower in _BROWSER_APPS:
                pages = sorted(data["pages"])[:3]
                if pages:
                    parts.append(f"**{app}** ({mins} min) — {', '.join(pages)}")
                elif mins >= 2:
                    parts.append(f"**{app}** ({mins} min)")
            elif data["project"]:
                files = sorted(data["files"])[:4]
                if files:
                    parts.append(f"**{data['project']}** project in {app} ({mins} min) — editing {', '.join(files)}")
                else:
                    parts.append(f"**{data['project']}** project in {app} ({mins} min)")
            elif data["files"]:
                parts.append(f"**{app}** ({mins} min) — {', '.join(sorted(data['files'])[:4])}")
            elif mins >= 1:
                parts.append(f"**{app}** ({mins} min)")

    if sessions:
        s = sessions[0]
        if s.get('summary'):
            parts.append(f"\nPrevious session: {s['summary']}")

    return parts


def _is_connected(host="8.8.8.8", port=53, timeout=1.5) -> bool:
    """Quick check for internet connectivity via DNS port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

# Pending fast actions (when fast model requests web_search)
_PENDING_ACTIONS_LOCK = threading.Lock()
_PENDING_ACTIONS: dict[str, dict] = {}
_PENDING_ACTIONS_TTL_S = 90


def _pending_actions_put(pending_id: str, payload: dict) -> None:
    now = time.time()
    with _PENDING_ACTIONS_LOCK:
        _PENDING_ACTIONS[pending_id] = {"created_at": now, **payload}
        # Best-effort cleanup
        for k, v in list(_PENDING_ACTIONS.items()):
            if now - float(v.get("created_at", now)) > _PENDING_ACTIONS_TTL_S:
                _PENDING_ACTIONS.pop(k, None)


def _pending_actions_pop(pending_id: str) -> Optional[dict]:
    with _PENDING_ACTIONS_LOCK:
        return _PENDING_ACTIONS.pop(pending_id, None)


def _pending_actions_get(pending_id: str) -> Optional[dict]:
    with _PENDING_ACTIONS_LOCK:
        return _PENDING_ACTIONS.get(pending_id)


def _llm_person_description(name: str, context: str, safe_fast_completion) -> Optional[tuple[Optional[str], str]]:
    """Ask the fast model to write a person description from search context.
    Returns (name, description), or None on failure."""

    system_content = (
        "You are an expert biographer. Write a concise Person Card based on the search results.\n"
        "You MUST always output both lines, even if information is limited — do your best with what is available.\n"
        "Strictly follow this format (no markdown, no preamble):\n"
        "NAME: [Full Name]\n"
        "DESCRIPTION: [1-2 sentences. Third-person. Role + organization/location. No social stats, no handles.]"
    )
    user_content = f"Search Context for '{name}':\n{context}\n\nGenerate the Person Card now. You must always write both NAME and DESCRIPTION lines:"

    def _parse_card_text(text):
        """Parse NAME/DESCRIPTION card text. Returns (name_or_none, desc) tuple, or None."""
        if not text: return None
        # Handle cases where model adds markdown or preamble
        text = text.replace("```", "").replace("**", "")

        name_found = None
        desc_found = None

        for card_line in text.split("\n"):
            card_line = card_line.strip()
            if not card_line: continue
            if card_line.upper().startswith("NAME:"):
                name_found = card_line.split(":", 1)[1].strip()
            elif card_line.upper().startswith("DESCRIPTION:"):
                desc_found = card_line.split(":", 1)[1].strip()

        if desc_found:
            return name_found, desc_found

        # Fallback: if no strict format, take the longest line that isn't the name
        all_lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not all_lines: return None

        # If model just outputted the description without tags
        longest = max(all_lines, key=len)
        if len(longest) > 20 and "NAME:" not in longest.upper():
            return name_found, longest  # name_found may be None — caller handles this

        return None

    # Try Fast Model (with retry)
    for i in range(2):
        try:
            out = safe_fast_completion(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=300, # Increased from 200 to prevent cutoff
                temperature=0.3, # Lower temp for more deterministic formatting
                step_name=f"Person card description (attempt {i+1})",
            )
            if out:
                card_text = out['choices'][0]['message']['content'].strip()
                if card_text:
                    logging.info(f"[DEBUG] Fast Model person desc output (try {i+1}):\n{card_text}")
                    _parsed = _parse_card_text(card_text)
                    if _parsed is not None:
                        parsed_name, parsed_desc = _parsed
                        if parsed_desc: return parsed_name, parsed_desc
                else:
                    logging.warning(f"[DEBUG] Fast Model returned empty text (try {i+1})")
        except Exception as e:
            logging.warning(f"Fast model person desc attempt {i+1} failed: {e}")

    return None


def _build_person_desc_from_snippets(name: str, search_results: list) -> Optional[str]:
    """Rule-based fallback: build a readable description from search snippets."""
    import re as _re
    # Try to find a snippet that mentions the person's name (case-insensitive)
    name_lower = name.lower()
    name_parts = [p for p in name_lower.split() if len(p) > 2]
    best_snippet = None
    for r in search_results[:5]:
        snippet = (r.get('content') or r.get('snippet', '')).strip()
        if not snippet:
            continue
        snippet_lower = snippet.lower()
        if any(p in snippet_lower for p in name_parts):
            best_snippet = snippet
            break
    if not best_snippet and search_results:
        best_snippet = (search_results[0].get('content') or search_results[0].get('snippet', '')).strip()
    if not best_snippet:
        return None
    # Clean up: remove trailing ellipsis, excess whitespace
    desc = _re.sub(r'\s+', ' ', best_snippet).strip()
    if desc.endswith('...'):
        desc = desc[:-3].strip()
    # Truncate to a reasonable length
    if len(desc) > 300:
        desc = desc[:300].rsplit(' ', 1)[0] + '.'
    return desc if len(desc) > 15 else None


def _heuristic_classify_search_results(query: str, results: list) -> Optional[dict]:
    """Fast heuristic to classify search results without a second LLM call.

    Returns a typed action dict if confident, or None to fall through to Phase 2 LLM.
    Handles ~60-70% of search queries (the obvious ones).
    """
    if not results:
        return None

    q_lower = query.lower().strip()
    q_words = q_lower.split()

    # Combine text from top results for keyword scanning
    combined = ' '.join(
        (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', '')))
        for r in results[:3]
    ).lower()

    # --- Place detection ---
    if len(q_words) <= 3:
        place_signals = ['capital', 'stolica', 'miasto', 'city', 'town', 'country',
                         'province', 'województw', 'located in', 'population',
                         'gmina', 'powiat', 'region', 'district', 'county',
                         'municipality', 'village', 'commune', 'landmark',
                         'monument', 'continent', 'island', 'river',
                         'situated', 'km²', 'km2', 'inhabitants', 'residents',
                         'metropolitan', 'urban', 'founded in', 'established in',
                         'geography', 'administrative']
        place_score = sum(1 for kw in place_signals if kw in combined)
        # Also check if none of the top results look like person pages
        person_signals_check = ['biography', 'born ', 'actor', 'singer', 'ceo', 'founder', 'politician']
        person_score_check = sum(1 for kw in person_signals_check if kw in combined)
        if place_score >= 1 and place_score > person_score_check:
            logging.info(f"[HEURISTIC] Place detected (score={place_score}) for '{query}'")
            place_res = get_place_result(query, existing_results=results)
            if place_res:
                return place_res
            else:
                import urllib.parse
                url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
                return {"type": "link", "url": url, "title": f"Search {query}", "description": "Web Search"}

    # --- Person detection via Knowledge Graph ---
    kg_result = next((r for r in results if r.get('is_knowledge_graph')), None)
    if kg_result:
        attrs = kg_result.get('attributes', {})
        person_attrs = {'born', 'died', 'spouse', 'children', 'education', 'height',
                        'nationality', 'occupation', 'parents', 'awards', 'alma mater',
                        'years active', 'known for', 'net worth'}
        attr_keys_lower = {k.lower() for k in attrs.keys()}
        person_attr_matches = person_attrs & attr_keys_lower
        if len(person_attr_matches) >= 1:
            name = kg_result.get('title', query).strip()
            desc = (kg_result.get('content') or '').strip()
            if attrs:
                attr_str = " ".join([f"{k}: {v}." for k, v in list(attrs.items())[:4]])
                if attr_str:
                    desc = (desc + " " + attr_str).strip()
            if desc:
                logging.info(f"[HEURISTIC] Person detected via KG attrs ({person_attr_matches}) for '{query}'")
                return {"type": "person", "name": name, "description": desc[:400],
                        "url": kg_result.get('url', ''), "image": kg_result.get('img_src')}

    # --- Person detection via URL patterns ---
    person_url_patterns = ['linkedin.com/in/', 'wikipedia.org/wiki/', 'imdb.com/name/']
    bio_keywords = ['biography', 'born ', 'is a ', 'was a ', 'actor', 'actress', 'singer',
                    'politician', 'director', 'ceo', 'founder', 'president', 'professor']
    person_url_hits = sum(1 for r in results[:3] if any(p in (r.get('url', '').lower()) for p in person_url_patterns))
    bio_keyword_hits = sum(1 for kw in bio_keywords if kw in combined)
    if person_url_hits >= 2 or (person_url_hits >= 1 and bio_keyword_hits >= 2):
        # Strong person signal — but we still need the LLM for name/description synthesis
        # Return None to let Phase 2 handle it with better formatting
        pass

    # --- Link detection: exact domain match (high confidence official site) ---
    from urllib.parse import urlparse
    for r in results[:2]:
        url = r.get('url', '')
        title = r.get('title', '')
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.replace("www.", "")
            domain_parts = netloc.split('.')
            domain_root = domain_parts[-2] if len(domain_parts) >= 2 else domain_parts[0]
            if domain_root.lower() == q_lower and len(q_lower) >= 2:
                desc = (r.get('content') or r.get('snippet', '')).strip()
                logging.info(f"[HEURISTIC] Exact domain match: {domain_root} == {q_lower}")
                return {"type": "link", "url": url, "title": title, "description": desc}
        except Exception:
            pass

    return None


def _parse_fast_action_output(
    *,
    result_text: str,
    query: str,
    request_id: str,
    endpoint_start_time: float,
    search_context: str,
    search_results: list,
    safe_fast_completion,
):
    """
    Parse the fast model's command output into typed action dicts.
    `safe_fast_completion` is a callable compatible with _safe_fast_completion in action_endpoint.
    """
    logging.info(f"\n=== FAST MODEL OUTPUT ===\n{result_text}\n=========================\n")

    # Fallback: if output is empty, default to search
    if not result_text or not result_text.strip():
        logging.info(f"Empty model output, defaulting to SEARCH for '{query}'")
        result_text = f"SEARCH:{query}"

    # NONE: legacy command — convert to a simple answer
    if re.search(r'\bNONE\b', result_text):
        logging.info(f"[ACTION] Fast model returned NONE for query: '{query}', converting to ANSWER")
        result_text = f"ANSWER:I'm Omni, your AI assistant. How can I help you?"

    # Also check if output contains only special tokens or is just newlines/spaces
    has_command = any(cmd in result_text for cmd in [
        "PERSON:", "PLACE:", "OPEN:", "OPEN_APP:", "INSTALL:", "UNINSTALL:", "SEARCH:",
        "IGNORE", "CALC:", "FA:", "UP:", "FORGET:", "BRIGHTNESS:",
        "CURRENCY:", "TRANSLATE:", "SYSTEM_SETTINGS:", "WEATHER:", "UNIT:",
        "COLOR:", "TIMER:", "PASSWORD:", "QRCODE:", "TERMINAL:",
        "CALENDAR", "EMAILS", "ANSWER:", "ORGANIZE:", "MEMORY:", "CONTEXT:"
    ])
    if not has_command:
        # No recognized command — treat the raw text as an answer rather than searching
        clean_text = result_text.strip()
        if clean_text and len(clean_text) > 5:
            logging.info(f"No recognized commands in output, treating as ANSWER for '{query}'")
            result_text = f"ANSWER:{clean_text}"
        else:
            logging.info(f"No recognized commands in output '{result_text[:100]}', defaulting to SEARCH for '{query}'")
            result_text = f"SEARCH:{query}"

    logging.info(f"[TIMING] Starting action parsing at: {time.time() - endpoint_start_time:.3f}s")
    actions = []
    for line in result_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        if "CALC:" in line:
            try:
                expr = line.split("CALC:")[1].strip()
                res = perform_calculation(expr)
                # Extract result and LaTeX
                val = res.split("Result: ")[1].strip() if "Result: " in res else res
                latex_match = re.search(r'LaTeX: \$(.*?)\$', res)
                latex_eq = latex_match.group(1) if latex_match else f"{expr} = {val}"
                actions.append({"type": "calc", "content": val, "equation": latex_eq})
            except Exception:
                pass

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
            except Exception:
                pass

        if "TIMER:" in line:
            try:
                val = line.split("TIMER:")[1].strip()
                actions.append({"type": "timer", "duration": int(val)})
            except Exception:
                pass

        if "PASSWORD:" in line:
            try:
                val = line.split("PASSWORD:")[1].strip()
                actions.append({"type": "password", "length": int(val)})
            except Exception:
                pass

        if "QRCODE:" in line:
            try:
                val = line.split("QRCODE:")[1].strip()
                actions.append({"type": "qrcode", "data": val})
            except Exception:
                pass

        if line.strip() == "CALENDAR":
            try:
                # Check prefetch cache first (populated on window toggle)
                cached = model_manager.prefetch_get("calendar_events")
                if cached is not None:
                    logging.info("[ACTION] Using prefetched calendar events")
                    cal_result = cached
                else:
                    from src.services.llm.tools import execute_tool
                    cal_result = execute_tool("get_calendar_events", {"days": 3})
                actions.append({"type": "calendar", "events_text": cal_result})
            except Exception as e:
                logging.error(f"Failed to execute CALENDAR tool: {e}")
                actions.append({"type": "calendar", "events_text": f"Error fetching calendar: {e}"})

        elif line.strip() == "EMAILS":
            try:
                # Check prefetch cache first (populated on window toggle)
                cached = model_manager.prefetch_get("unread_emails")
                if cached is not None:
                    logging.info("[ACTION] Using prefetched unread emails")
                    email_result = cached
                else:
                    from src.services.llm.tools import execute_tool
                    email_result = execute_tool("get_unread_emails", {"limit": 5})
                actions.append({"type": "emails", "emails_text": email_result})
            except Exception as e:
                logging.error(f"Failed to execute EMAILS tool: {e}")
                actions.append({"type": "emails", "emails_text": f"Error fetching emails: {e}"})

        elif line.startswith("CONTEXT:"):
            ctx_query = line[8:].strip().lower()
            logging.info(f"[ACTION] CONTEXT command: '{ctx_query}'")
            try:
                from src.services.context.knowledge_graph import get_knowledge_graph
                kg = get_knowledge_graph()
                parts = []

                if "resume" in ctx_query:
                    sessions = kg.get_recent_sessions(limit=1)
                    if sessions:
                        s = sessions[0]
                        if s.get('resume_state'):
                            from src.services.context.session_manager import get_session_manager
                            get_session_manager().resume_session(s)
                            parts.append(f"Resuming session: {s.get('summary', 'previous work')}")
                        else:
                            parts.append(f"Last session: {s.get('summary', 'No details')}. No files to reopen.")
                    else:
                        parts.append("No recent work sessions found to resume.")
                elif "session" in ctx_query:
                    sessions = kg.get_recent_sessions(limit=3)
                    if sessions:
                        for i, s in enumerate(sessions, 1):
                            parts.append(f"**Session {i}**: {s.get('summary', 'No summary')}")
                    else:
                        parts.append("No work sessions recorded yet.")
                else:
                    recent = kg.get_recent_activity(limit=20)
                    sessions = kg.get_recent_sessions(limit=3)
                    parts = _build_context_parts(recent, sessions)

                if not parts:
                    parts.append("I've just started tracking your activity — give me a few more minutes to learn what you're working on.")
                actions.append({"type": "answer", "text": "\n".join(parts)})
            except Exception as e:
                logging.error(f"Failed to execute CONTEXT command: {e}")
                actions.append({"type": "answer", "text": "Context tracking is starting up — please try again in a moment."})

        elif line.startswith("MEMORY:"):
            mem_query = line[7:].strip() or query
            try:
                from src.services.memory.memvid_store import get_user_memory as _get_mem
                mem_result = _get_mem(mem_query)
                if mem_result and mem_result.strip() and "No personal memory" not in mem_result and "No general" not in mem_result:
                    actions.append({"type": "answer", "text": mem_result.strip()})
                else:
                    actions.append({"type": "answer", "text": f"I couldn't find any info about '{mem_query}' in memory."})
            except Exception as _me:
                logging.warning(f"MEMORY command failed: {_me}")
                actions.append({"type": "answer", "text": "Failed to search memory."})

        elif line.startswith("TERMINAL:"):
            try:
                # Format: TERMINAL:command|description (optional description)
                parts = line[9:].split("|", 1)
                cmd = parts[0].strip()
                import subprocess
                logging.info(f"[TERMINAL ACTION] Executing: {cmd}")
                start_cmd = time.time()
                try:
                    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                    out = (proc.stdout or "").strip()
                    if not out:
                        out = (proc.stderr or "").strip()
                except subprocess.TimeoutExpired:
                    out = "Error: command timed out."
                except Exception as e:
                    out = f"Error: {e}"
                elapsed = time.time() - start_cmd
                logging.info(f"[TERMINAL ACTION] Done in {elapsed*1000:.0f}ms, output: {out[:200]!r}")
                
                if out:
                    actions.append({"type": "answer", "text": f"```\n{out}\n```"})
                else:
                    actions.append({"type": "answer", "text": "Command returned no output."})
            except Exception as e:
                logging.warning(f"TERMINAL command failed: {e}")

        elif line.startswith("ANSWER:"):
            # Capture everything after ANSWER: including subsequent non-command lines
            answer_start_idx = result_text.find("ANSWER:")
            if answer_start_idx >= 0:
                full_answer = result_text[answer_start_idx + 7:].strip()
                # Stop at the next command if there is one
                for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "CALC:", "INSTALL:", "TRANSLATE:", "CURRENCY:"]:
                    cmd_idx = full_answer.find(cmd)
                    if cmd_idx > 0:
                        full_answer = full_answer[:cmd_idx].strip()
                        break
                if full_answer:
                    actions.append({"type": "answer", "text": full_answer})
            else:
                text = line[7:].strip()
                if text:
                    actions.append({"type": "answer", "text": text})
            break  # ANSWER consumes the rest, stop parsing

        elif line.startswith("ORGANIZE:"):
            path = line[9:].strip()
            if path:
                actions.append({"type": "organize_pending", "path": path, "title": f"Organize {path}"})

        elif "FA:" in line:
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
                return [], []

            raw_q = line.split("SEARCH:")[1].strip()
            q = _sanitize_search_query(raw_q, query)
            if q != raw_q:
                logging.info(f"Fast model SEARCH query sanitized: '{raw_q[:80]}' -> '{q}'")

            results = []
            context = ""
            if q.lower() == query.lower() and search_results:
                logging.info(f"Reusing {len(search_results)} existing search results for SEARCH action")
                results = search_results
            else:
                logging.info(f"Refetching search results for new query: '{q}'")
                from src.services.search.web_search import search_api
                results = search_api(q, categories='general', fast=True)
                logging.warning(f"[ACTION/SEARCH] search_api({q!r}, fast=True) → {len(results)} results")
                # Retry with original query if the sanitized query differs and got no results
                if not results and q.lower() != query.lower():
                    results = search_api(query, categories='general', fast=True)
                    logging.warning(f"[ACTION/SEARCH] retry search_api({query!r}, fast=True) → {len(results)} results")

            if results:
                # Build rich context from top results
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

                classify_messages = [
                    {
                        "role": "system",
                        "content": """You are a search result classifier.
Analyze the provided search results for the user's query.

Task: Determine if the user is looking for a PERSON, a PLACE, or just doing a general SEARCH.

Categories:
- PERSON: Real people, historical figures, celebrities, professionals.
  * Indicators: "biography", "born", "career", "profile", job titles (CEO, Director, Actor), social media profiles (LinkedIn, Facebook).
- PLACE: Physical locations, cities, schools, landmarks, addresses.
  * Indicators: "city", "country", "address", "map", "located in", "school", "university".
- SEARCH: Everything else (products, concepts, companies, websites, lyrics, definitions).

Instructions:
1. Read the user query and search snippets carefully.
2. If the results are predominantly about a specific person's life or career, choose PERSON.
3. If the results are about a specific physical location or institution, choose PLACE.
4. Otherwise, choose SEARCH.
5. Output ONLY the category name."""
                    },
                    {"role": "user", "content": f"User query: {query}\n\n{context}\n\nCategory (PERSON/PLACE/SEARCH):"}
                ]

                try:
                    classification = safe_fast_completion(
                        messages=classify_messages,
                        max_tokens=8,
                        temperature=0.0,
                        step_name="Search classification"
                    )
                    if classification is None:
                        raise Exception("Classification aborted")

                    classification_text = classification['choices'][0]['message']['content'].strip().upper()
                    normalized = re.sub(r'[^A-Za-z]', '', classification_text)
                    first_word = ""
                    if normalized.startswith('PERSON'):
                        first_word = "PERSON"
                    elif normalized.startswith('PLACE'):
                        first_word = "PLACE"
                    elif normalized.startswith('SEARCH'):
                        first_word = "SEARCH"
                    logging.info(f"[DEBUG] Fast model classification: '{classification_text[:60]}' -> normalized '{normalized[:20]}' -> '{first_word}'")

                    # If classification is empty, try heuristic detection from results
                    if not first_word and results:
                        _combined_text = ' '.join(
                            (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', '')))
                            for r in results[:3]
                        ).lower()
                        _place_keywords = ['city', 'capital', 'stolica', 'miasto', 'town', 'country',
                                          'province', 'województw', 'located', 'population', 'region',
                                          'district', 'county', 'gmina', 'powiat', 'strona główna']
                        if any(kw in _combined_text for kw in _place_keywords):
                            first_word = "PLACE"
                            logging.info(f"[DEBUG] Empty classification → heuristic detected PLACE from keywords")

                    if first_word == "PERSON":
                        logging.info(f"[DEBUG] Model chose PERSON - fast model will write the card from search results")
                        write_messages = [
                            {
                                "role": "system",
                                "content": "Based on the search results, write a concise biography for a person card.\n\nOutput exactly two lines:\nNAME: [person's full name only]\nDESCRIPTION: [A third-person summary in 1-2 sentences with specific context (role + organization/school/company/location if available). No handles, follower counts, phone numbers, or raw snippet fragments.]"
                            },
                            {"role": "user", "content": f"Search results about: {query}\n\n{context}\n\nWrite the person card:"}
                        ]
                        try:
                            write_out = safe_fast_completion(
                                messages=write_messages,
                                max_tokens=150,
                                temperature=0.5,
                                step_name="Person card generation"
                            )
                            if write_out:
                                card_text = write_out['choices'][0]['message']['content'].strip()
                                logging.info(f"[DEBUG] Fast model person card output:\n{card_text}")
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
                                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                                    if len(lines) >= 2:
                                        person_desc = " ".join(lines[1:])
                                    elif lines:
                                        person_desc = lines[0] if "NAME:" not in lines[0].upper() else ""

                                person_res_fallback = get_person_result(person_name, existing_results=results)
                                if person_res_fallback:
                                    if person_desc:
                                        person_res_fallback['description'] = person_desc
                                    else:
                                        llm_result = _llm_person_description(person_name, context, safe_fast_completion)
                                        llm_desc = llm_result[1] if isinstance(llm_result, tuple) else None
                                        if llm_desc:
                                            person_res_fallback['description'] = llm_desc
                                        else:
                                            fallback_desc = _build_person_desc_from_snippets(person_name, results or [])
                                            if fallback_desc:
                                                person_res_fallback['description'] = fallback_desc
                                    actions.append(person_res_fallback)
                                else:
                                    actions.append({
                                        "type": "person",
                                        "name": person_name or q,
                                        "description": person_desc,
                                        "url": results[0].get('url') if results else "",
                                        "image": None
                                    })
                                continue
                        except Exception as e:
                            logging.error(f"[DEBUG] Person card generation failed: {e}")

                    elif first_word == "PLACE":
                        logging.info(f"[DEBUG] Model chose PLACE for: {q}")
                        place_result = get_place_result(q, existing_results=results)
                        if place_result:
                            actions.append(place_result)
                            continue

                except Exception as e:
                    logging.error(f"[DEBUG] Classification failed: {e}")

            # Fallback: if search results seem irrelevant (don't mention the query),
            # try a direct map search — the query might be a place name.
            _q_lower_fb = q.lower().strip()
            _results_relevant = any(
                _q_lower_fb in (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', ''))).lower()
                for r in results[:3]
            ) if results else False
            if not _results_relevant and len(_q_lower_fb.split()) <= 2 and len(_q_lower_fb) >= 3:
                logging.info(f"[DEBUG] Search results irrelevant for '{q}', trying direct map search as place fallback")
                _place_fb = get_place_result(q, existing_results=None)
                if _place_fb and (_place_fb.get('latitude') or _place_fb.get('longitude')):
                    logging.info(f"[DEBUG] Direct map search found place: {_place_fb.get('name')}")
                    actions.append(_place_fb)
                    continue

            person_candidate = _extract_person_candidate(query) or _extract_person_candidate(q)
            if person_candidate:
                # If we don't have results yet (e.g. search failed for original query but might work for person name),
                # fetch them now to ensure we have context for the description.
                if not results:
                    try:
                        from src.services.search.web_search import search_api
                        logging.info(f"Fetching search results for Person card (heuristic): {person_candidate}")
                        results = search_api(person_candidate, categories='general', fast=True)
                        if results:
                            text_res = []
                            for i, r in enumerate(results[:3]):
                                text_res.append(f"Title: {r.get('title')}\nDescription: {r.get('content') or r.get('snippet')}\nURL: {r.get('url')}")
                            context = "\n\n".join(text_res)
                    except Exception: pass

                person_result = get_person_result(person_candidate, existing_results=results if results else None)
                if person_result:
                    logging.info(f"[DEBUG] Heuristic person card fallback for: {person_candidate}")
                    if context:
                        llm_name, llm_desc = _llm_person_description(person_candidate, context, safe_fast_completion) or (None, None)
                        if llm_desc:
                            person_result['description'] = llm_desc
                        else:
                            fallback_desc = _build_person_desc_from_snippets(person_candidate, results or [])
                            if fallback_desc:
                                person_result['description'] = fallback_desc
                        if llm_name:
                             # Same logic as above
                             if len(person_result['name']) < 5 or (llm_name.lower().startswith(person_result['name'].lower()) and len(llm_name) > len(person_result['name'])):
                                 person_result['name'] = llm_name
                    actions.append(person_result)
                    continue

            # If no search results at all, try to answer from the model's own knowledge
            if not results:
                logging.info(f"[SEARCH] No results for '{q}', trying direct answer from fast model")
                try:
                    _direct_ans = safe_fast_completion(
                        messages=[
                            {"role": "system", "content": (
                                "You are a helpful assistant. Answer the user's question concisely in 1-3 sentences. "
                                "Output ONLY the answer text, no prefixes, no commands, no markdown."
                            )},
                            {"role": "user", "content": query},
                        ],
                        max_tokens=200, temperature=0.3, step_name="Direct answer (SEARCH fallback)"
                    )
                    if _direct_ans:
                        _ans_text = _direct_ans['choices'][0]['message']['content'].strip()
                        _ans_text = re.sub(r'<think>.*?(?:</think>|$)', '', _ans_text, flags=re.DOTALL).strip()
                        if _ans_text and len(_ans_text) > 5:
                            actions.append({"type": "answer", "text": _ans_text})
                            continue
                except Exception as _e:
                    logging.warning(f"Direct answer fallback failed: {_e}")

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

                if len(parts) >= 4:
                    source = parts[0]
                    from_lang = parts[1]
                    to_lang = parts[2]
                    translated = "|".join(parts[3:])
                elif len(parts) == 3:
                    p1, p2, p3 = parts
                    if len(p2.strip()) <= 3:
                        source, from_lang, to_lang, translated = p1, "auto", p2, p3
                    else:
                        logging.warning(f"TRANSLATE: parsed 3 parts, ambiguous: {parts}")
                        continue
                elif len(parts) == 2:
                    source, translated = parts
                    from_lang = "auto"
                    to_lang = "en"
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
            content = line.split("PERSON:")[1].strip()
            person_desc = None

            if "|" in content:
                name, desc = content.split("|", 1)
                name = name.strip()
                person_desc = desc.strip()
            else:
                name = content.strip()

            already_have = any(a.get('type') == 'person' for a in actions)
            if not already_have:
                # Ensure we have search results/context to generate a good card
                # If the model output PERSON directly without a prior search, we might lack context.
                if not search_results:
                    try:
                        from src.services.search.web_search import search_api
                        logging.info(f"Fetching search results for Person card: {name}")
                        search_results = search_api(name, categories='general', fast=True)
                        if search_results:
                            text_res = []
                            for i, r in enumerate(search_results[:3]):
                                text_res.append(f"Title: {r.get('title')}\nDescription: {r.get('content') or r.get('snippet')}\nURL: {r.get('url')}")
                            search_context = "\n\n".join(text_res)
                    except Exception as e:
                        logging.error(f"Failed to fetch fallback results for person card: {e}")

                # --- Check if this is actually a PLACE, not a PERSON ---
                # The LLM sometimes outputs PERSON for cities/places. Detect and redirect.
                _place_signals = ['capital', 'stolica', 'miasto', 'city', 'town', 'country',
                                  'province', 'located in', 'population', 'region', 'district',
                                  'municipality', 'village', 'island', 'river', 'continent',
                                  'województw', 'gmina', 'powiat', 'county', 'landmark',
                                  'situated', 'km²', 'km2', 'square km', 'sq km',
                                  'inhabitants', 'residents', 'metropolitan', 'urban',
                                  'founded in', 'established in', 'located', 'geography']
                _combined_text = ''
                if person_desc:
                    _combined_text += person_desc.lower() + ' '
                if search_results:
                    _combined_text += ' '.join(
                        (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', '')))
                        for r in search_results[:3]
                    ).lower()
                _place_score = sum(1 for kw in _place_signals if kw in _combined_text)
                _person_signals = ['born', 'actor', 'actress', 'singer', 'musician', 'politician',
                                   'director', 'ceo', 'founder', 'president', 'professor',
                                   'author', 'athlete', 'player', 'coach', 'scientist',
                                   'entrepreneur', 'artist', 'writer', 'composer']
                _person_score = sum(1 for kw in _person_signals if kw in _combined_text)

                if _place_score >= 1 and _place_score > _person_score:
                    logging.info(f"[PERSON→PLACE] Redirecting '{name}' to PLACE (place_score={_place_score}, person_score={_person_score})")
                    place_res = get_place_result(name, existing_results=search_results)
                    if place_res:
                        actions.append(place_res)
                        continue
                    else:
                        import urllib.parse
                        url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(name)}"
                        actions.append({"type": "place", "name": name, "description": person_desc or "Location", "latitude": None, "longitude": None, "url": url})
                        continue

                # If we have a valid name, try to use it.
                # BUT, if the name is just a fragment (like "Miko"),
                # we should try to recover the full name from the search results context if possible.

                target_name = name
                if len(target_name) < 5:
                    # Heuristic: LLM truncated the name. Use the user's query or first result.
                    if len(query) > len(target_name):
                        target_name = query
                    elif search_results:
                        target_name = search_results[0].get('title', target_name)
                    
                    if not target_name:
                        target_name = query

                res = get_person_result(target_name, existing_results=search_results)
                if res:
                    if person_desc and len(person_desc) > 10:
                        res['description'] = person_desc
                    elif search_context:
                        llm_name, llm_desc = _llm_person_description(target_name, search_context, safe_fast_completion) or (None, None)
                        if llm_desc:
                            res['description'] = llm_desc
                        else:
                            fallback_desc = _build_person_desc_from_snippets(target_name, search_results or [])
                            if fallback_desc:
                                res['description'] = fallback_desc
                        if llm_name:
                             # If the model explicitly refined the name (e.g. from "Miko" to "Mikołaj Piech"), use it
                             # But keep the original if it was already good, to avoid "Mikołaj Piech - Omni" etc.
                             # Only update if the current name is very short or the LLM name is a superstring
                             if len(res['name']) < 5 or (llm_name.lower().startswith(res['name'].lower()) and len(llm_name) > len(res['name'])):
                                 res['name'] = llm_name
                    actions.append(res)
                elif person_desc and len(person_desc) > 10:
                    # get_person_result returned nothing (no web results / image not found),
                    # but the model already wrote a good description — use it directly.
                    logging.info(f"[PERSON] get_person_result returned None for '{target_name}', using model description as fallback")
                    actions.append({"type": "person", "name": target_name, "description": person_desc, "url": "", "image": None})
                else:
                    # get_person_result failed and the model did not provide a description.
                    # Fallback to a skeleton Person card instead of Web Search Link so the UI stays consistent
                    import urllib.parse
                    logging.info(f"[PERSON] No description and get_person_result failed for '{target_name}', returning skeleton Person card")
                    url = f"https://www.google.com/search?q={urllib.parse.quote_plus(target_name)}"
                    actions.append({"type": "person", "name": target_name, "description": "No information retrieved.", "url": url, "image": None})
            
        elif "PLACE:" in line:
            name = line.split("PLACE:")[1].strip()
            place_res = get_place_result(name, existing_results=search_results)
            if place_res:
                actions.append(place_res)
            else:
                import urllib.parse
                logging.info(f"[PLACE] get_place_result failed/empty for '{name}', returning skeleton Place card")
                url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(name)}"
                actions.append({"type": "place", "name": name, "description": "Location", "latitude": None, "longitude": None, "url": url})

        elif "UNINSTALL:" in line:
            app = line.split("UNINSTALL:")[1].strip()
            logging.info(f"Action: UNINSTALL {app}")
            actions.append({"type": "uninstall", "name": app})

        elif "INSTALL:" in line:
            app = line.split("INSTALL:")[1].strip()
            metadata = resolve_app_metadata(app)
            actions.append({
                "type": "install",
                "name": app,
                "website": metadata.get("website") if metadata else None,
                "image": metadata.get("image") if metadata else None,
                "desc": metadata.get("desc", "") if metadata else "",
            })

        elif "OPEN:" in line:
            url = line.split("OPEN:")[1].strip()
            if "http" not in url:
                url = "https://" + url

            display_url = url.replace("https://", "").replace("http://", "").replace("www.", "")
            if "/" in display_url:
                display_url = display_url.split('/')[0]
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
                logging.info(f"OPEN_APP: '{app}' not found, suggesting install")
                metadata = resolve_app_metadata(app)
                actions.append({
                    "type": "install",
                    "name": app,
                    "website": metadata.get("website") if metadata else None,
                    "image": metadata.get("image") if metadata else None,
                    "desc": metadata.get("desc", "") if metadata else "",
                })

        elif "SYSTEM_SETTINGS:" in line:
            try:
                from src.services.system.macos_settings import SETTING_META, execute_setting
                json_str = line.split("SYSTEM_SETTINGS:")[1].strip()
                settings_act = json.loads(json_str)

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

    # Post-processing: Convert ANSWER to PERSON when query looks like a person name and answer describes a person
    _question_words = {"who", "what", "when", "where", "why", "how", "which", "whose", "does", "did", "is", "are", "was", "were", "can", "could", "would", "should"}
    _query_words = query.strip().lower().split()
    _looks_like_name = (
        1 <= len(_query_words) <= 4
        and not any(w in _question_words for w in _query_words)
        and not any(c in query for c in ['?', '!', '=', '+', '/', '\\', '@', '#'])
        and all(w.isalpha() or "'" in w or "-" in w for w in _query_words)
    )
    if _looks_like_name and not any(a.get('type') == 'person' for a in actions):
        for a in actions:
            if a.get('type') == 'answer':
                answer_text = a.get('text', '')
                # Heuristic: if the answer text starts with the query name and describes a person (born, founder, etc.)
                _person_signals = ['born', 'founder', 'co-founder', 'ceo', 'actor', 'musician', 'politician', 'president', 'director', 'scientist', 'author', 'artist', '(19', '(18', '(20']
                if any(sig in answer_text.lower() for sig in _person_signals):
                    logging.info(f"[POST] Converting ANSWER to PERSON for name-like query '{query}'")
                    a['type'] = 'person'
                    a['name'] = query.title()
                    a['description'] = answer_text
                    a['url'] = ''
                    a['image'] = None
                    break

    # Post-processing: Remove redundant or unwanted actions
    final_actions = []
    has_person = any(a.get('type') == 'person' for a in actions)
    
    # NEW: If we have a person card, and its name is suspiciously short (e.g. just one letter "m"),
    # try to fix it using the user query if the query looks like a name.
    for a in actions:
        if a.get('type') == 'person':
            current_name = a.get('name', '')
            if len(current_name) < 2 and len(query) > 2:
                # Likely a parsing error or bad fallback.
                # If query is "mikołaj piech" and name is "m", swap it.
                logging.info(f"Fixing suspicious person name '{current_name}' -> '{query}'")
                a['name'] = query
                # Also try to re-fetch description if it's empty
                if not a.get('description'):
                     if search_context:
                         llm_desc = _llm_person_description(query, search_context, safe_fast_completion)
                         if llm_desc:
                             a['description'] = llm_desc
                     if not a.get('description'):
                         res = get_person_result(query, existing_results=search_results)
                         if res:
                             a['description'] = res.get('description')
                             a['image'] = res.get('image')

    for a in actions:
        if a.get('type') == 'link':
            url = a.get('url', '').lower()
            if has_person and 'wikipedia.org' in url:
                continue
            if len(query) <= 1 and 'wikipedia.org' in url:
                continue
            if has_person:
                person_card = next((p for p in actions if p.get('type') == 'person'), None)
                if person_card and person_card.get('url') == a.get('url'):
                    continue

        final_actions.append(a)

    # Post-processing: PERSON → PLACE safety net.
    # If the description or search context contains strong place signals and no person signals,
    # convert the person card to a place card. This catches cases where the inline redirect
    # didn't fire (e.g. empty search results at parse time).
    _pp_place_signals = ['capital', 'capital city', 'city of', 'largest city', 'city in',
                         'stolica', 'miasto', 'town in', 'country in', 'located in',
                         'population of', 'municipality', 'situated in', 'inhabitants',
                         'founded in', 'province of', 'region of', 'district of']
    _pp_person_signals = ['born in', 'born on', 'is a actor', 'is an actor', 'is a singer',
                          'is a musician', 'is a politician', 'is a director', 'is a ceo',
                          'is a founder', 'co-founded', 'founded by']
    for a in final_actions:
        if a.get('type') == 'person':
            _text = (a.get('description', '') + ' ' + a.get('name', '')).lower()
            _pp_place = sum(1 for kw in _pp_place_signals if kw in _text)
            _pp_person = sum(1 for kw in _pp_person_signals if kw in _text)
            if _pp_place >= 1 and _pp_place > _pp_person:
                logging.info(f"[POST→PLACE] Converting person→place: name={a.get('name')!r} (place={_pp_place}, person={_pp_person})")
                place_name = a.get('name', query)
                place_res = get_place_result(place_name, existing_results=search_results)
                if place_res:
                    a.clear()
                    a.update(place_res)
                else:
                    a['type'] = 'place'
                    _raw = a.get('description', '')
                    if len(_raw) > 160:
                        _cut = _raw[:160]
                        _dot = max(_cut.rfind('. '), _cut.rfind('! '))
                        _raw = (_cut[:_dot + 1] if _dot > 40 else _cut).rstrip(' ,;')
                    a['address'] = _raw
                    a['latitude'] = None
                    a['longitude'] = None

    return final_actions, chips


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

    query = req.get('query', '').strip()
    history = req.get('history', [])
    screenshot_b64 = req.get('screenshot')
    resume_session_id = req.get('resume_session_id')

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

    fast_acts = check_fast_regex_actions(query)
    if fast_acts:
        from src.services.llm.chat import _postprocess_actions
        _postprocess_actions(fast_acts, "")
        response = {"answer": "", "actions": fast_acts, "thinking": ""}
        if stream:
            def _fast_acts_stream():
                yield f'data: {json.dumps({"type": "final", **response})}\n\n'
            return Response(_fast_acts_stream(), mimetype="text/event-stream")
        return jsonify(response)

    if not _is_connected():
        no_internet_msg = "No internet connection. Please check your network and try again."
        if stream:
            def _no_internet_stream():
                yield f'data: {json.dumps({"type": "final", "answer": no_internet_msg, "actions": []})}\n\n'
            return Response(_no_internet_stream(), mimetype="text/event-stream")
        return jsonify({"answer": no_internet_msg})

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
                for msg_type, content in process_chat_request(query, history, screenshot_b64, stream=True, resume_session_id=resume_session_id):
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
        response = process_chat_request(query, history, screenshot_b64, resume_session_id=resume_session_id)
        return jsonify(response)

@api_bp.route('/compose_email', methods=['POST'])
def compose_email_endpoint():
    """Compose email subject + body via fast model. Returns JSON {subject, body}."""
    import re as _re
    req = request.json or {}
    query = req.get('query', '').strip()
    recipient = req.get('recipient', '').strip()
    if not query:
        return jsonify({"error": "query required"}), 400

    compose_prompt = (
        'You compose emails. Output ONLY valid JSON: {"subject":"...","body":"..."}\n'
        "Rules:\n"
        "- Specific subject line matching the topic\n"
        "- Body: natural, concise (3-5 sentences), no filler phrases like 'I hope this finds you well'\n"
        "- Sign off with 'Best,' or 'Thanks,' — no [Your Name] placeholder\n"
        "- No markdown, no code fences, only the JSON object"
    )
    user_content = (f"Recipient name: {recipient}\nRequest: {query}" if recipient
                    else f"Request: {query}")
    messages = [
        {"role": "system", "content": compose_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        import requests as _requests
        from src.core import auth as _auth
        from src.core.config import BACKEND_URL, OMNI_SECRET, DEVICE_ID, FAST_MODEL_GROQ

        headers = {
            "Content-Type": "application/json",
            "X-Omni-Secret": OMNI_SECRET,
            "X-Device-ID": DEVICE_ID,
        }
        token = _auth.get_access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = _requests.post(
            f"{BACKEND_URL}/v1/chat/completions",
            headers=headers,
            json={
                "model": FAST_MODEL_GROQ,
                "messages": messages,
                "max_tokens": 400,
                "temperature": 0.8,
                "stream": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        text = (data["choices"][0]["message"].get("content") or "").strip()
        logging.info(f"[/compose_email] raw: {text[:200]}")

        text = _re.sub(r'<think>.*?</think>', '', text, flags=_re.DOTALL).strip()
        text = _re.sub(r'^```(?:json)?\s*', '', text)
        text = _re.sub(r'\s*```$', '', text)
        json_match = _re.search(r'\{.*\}', text, _re.DOTALL)
        if json_match:
            text = json_match.group(0)
        parsed = json.loads(text)
        return jsonify({"subject": parsed.get("subject", ""), "body": parsed.get("body", "")})
    except Exception as e:
        logging.error(f"[/compose_email] error: {e}")
        return jsonify({"error": str(e)}), 500

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
            # Check if table exists before opening
            if "files" not in model_manager.db_conn.list_tables():
                # logging.warning("Search endpoint: 'files' table not found (indexer may not have run yet).")
                return jsonify({"results": []})

            tbl = model_manager.db_conn.open_table("files")
            # Encoding and searching must be thread-safe (hence the lock)
            with model_manager.embed_lock:
                query_vec = model_manager.embed_model.encode(query)
            res = tbl.search(query_vec).limit(3).to_pandas()
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

    # Image semantic search via CLIP — outside search_lock to avoid deadlock
    # (ensure_vision_model also acquires search_lock internally)
    try:
        if model_manager.db_conn and "images" in model_manager.db_conn.list_tables():
            model_manager.ensure_vision_model()
            if model_manager.vision_model is not None:
                clip_vec = model_manager.vision_model.encode(query)
                img_tbl = model_manager.db_conn.open_table("images")
                img_res = img_tbl.search(clip_vec).limit(3).to_arrow()
                for i in range(img_res.num_rows):
                    dist = img_res.column('_distance')[i].as_py() if '_distance' in img_res.schema.names else 1.0
                    if dist < 0.5:
                        results.append({
                            "name": img_res.column('filename')[i].as_py(),
                            "path": img_res.column('path')[i].as_py(),
                            "score": float(dist),
                            "type": "file",
                        })
    except Exception as e:
        logging.error(f"Image search error: {e}")

    # Context-aware re-ranking
    if results:
        try:
            from src.services.context.context_matcher import get_matcher
            matcher = get_matcher()
            # Convert LanceDB distances to similarity scores for the matcher
            for r in results:
                r["score"] = max(0.0, 1.0 - r.get("score", 0.0))
            results = matcher.rank_search_results(results, query)
        except Exception as e:
            logging.debug(f"Context re-ranking skipped: {e}")

    return jsonify({"results": results})

@api_bp.route('/person_image', methods=['POST'])
def person_image_endpoint():
    """Fetch an image URL for a person name (called after the card is already shown)."""
    try:
        req = request.get_json(force=True)
    except Exception:
        return jsonify({"image_url": None}), 400

    name = (req.get('name') or '').strip()
    if not name:
        return jsonify({"image_url": None})

    try:
        from src.services.search.web_search import search_api
        results = search_api(name, categories='images', fast=True)
        logging.info(f"[person_image] '{name}': got {len(results)} results")
        if results:
            logging.info(f"[person_image] first result keys: {list(results[0].keys())}, sample: {results[0]}")
        image_url = None
        for r in results:
            image_url = r.get('img_src') or r.get('thumbnail') or r.get('image')
            if image_url:
                break
        logging.info(f"[person_image] '{name}': resolved image_url={image_url!r}")
        return jsonify({"image_url": image_url})
    except Exception as e:
        logging.warning(f"[person_image] Failed for '{name}': {e}")
        return jsonify({"image_url": None})


def _chip_site_name(url: str) -> str:
    """'https://www.tesla.com/path' → 'Tesla'"""
    _KNOWN = {
        "wikipedia": "Wikipedia",
        "youtube": "YouTube",
        "youtu": "YouTube",
        "duckduckgo": "DuckDuckGo",
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




_CURRENCY_CODES = frozenset([
    'USD','EUR','GBP','PLN','JPY','CHF','AUD','CAD','CNY','SEK','NOK','DKK',
    'CZK','HUF','RON','BGN','RUB','TRY','BRL','MXN','INR','KRW','SGD','HKD',
    'THB','ZAR','NZD','ILS','AED','SAR','QAR','KWD','UAH','CLP','COP','PEN',
    'ARS','IDR','MYR','PHP','VND','PKR','BDT','DZD','MAD','EGP','NGN','KES',
    'GHS','TZS','UGX','ZMW','BWP','MUR','NAD','MZN','XOF','XAF','SCR','RWF',
    'BIF','DJF','GMD','SLL','GNF','LRD','TTD','JMD','BBD','XCD','BSD','BMD',
    'KYD','BZD','AWG','ANG','CUP','HTG','DOP','MOP','TWD','BHD','OMR','JOD',
    'TND','LYD','ETB','ISK','HRK','RSD','BAM','ALL','MDL','MKD','GEL','AZN',
    'KZT','AMD','CVE','MGA','SZL','LSL','NAD','ERN','DJF','KMF','MRO','STD',
    'FJD','PGK','SBD','WST','TOP','VUV','NIO','GTQ','HNL','CRC','PAB','BOB',
    'PYG','UYU','GYD','SRD','TTD',
])

_CURRENCY_ALIASES = {
    'dollar': 'USD', 'dollars': 'USD', 'bucks': 'USD',
    'dolar': 'USD', 'dolary': 'USD', 'dolarów': 'USD', 'dolara': 'USD',
    'euro': 'EUR', 'euros': 'EUR',
    'pound': 'GBP', 'pounds': 'GBP', 'sterling': 'GBP',
    'zloty': 'PLN', 'złoty': 'PLN', 'zł': 'PLN', 'złotych': 'PLN', 'zlotych': 'PLN',
    'yen': 'JPY', 'yens': 'JPY',
    'franc': 'CHF', 'francs': 'CHF',
    'yuan': 'CNY', 'renminbi': 'CNY',
    'ruble': 'RUB', 'rubles': 'RUB', 'rubel': 'RUB',
    'won': 'KRW',
    'rupee': 'INR', 'rupees': 'INR',
    'forint': 'HUF',
    'lira': 'TRY', 'lire': 'TRY',
    'rand': 'ZAR',
    'baht': 'THB',
    'ringgit': 'MYR',
    'real': 'BRL', 'reais': 'BRL',
    'shekel': 'ILS', 'shekels': 'ILS',
    'dirham': 'AED',
    'riyal': 'SAR',
    'hryvnia': 'UAH', 'hryvna': 'UAH',
    'dram': 'AMD',
    'lari': 'GEL',
    'tenge': 'KZT',
    'koruna': 'CZK', 'korona': 'CZK',
    'krona': 'SEK',
    'krone': 'NOK',
    'peso': 'MXN',
}

def _resolve_currency_code(s: str):
    if s.upper() in _CURRENCY_CODES:
        return s.upper()
    return _CURRENCY_ALIASES.get(s.lower())

_CURRENCY_RE = re.compile(
    r'^(\d+(?:[.,]\d+)?)\s*([A-Za-z\u00c0-\u024f]{2,10})\s+(?:to|in|na|w)\s+([A-Za-z\u00c0-\u024f]{2,10})$',
    re.IGNORECASE
)

# World Time
_CITY_TZ = {
    "tokyo": "Asia/Tokyo", "osaka": "Asia/Tokyo", "kyoto": "Asia/Tokyo",
    "london": "Europe/London",
    "paris": "Europe/Paris", "rome": "Europe/Rome", "berlin": "Europe/Berlin",
    "madrid": "Europe/Madrid", "amsterdam": "Europe/Amsterdam",
    "warsaw": "Europe/Warsaw", "vienna": "Europe/Vienna", "prague": "Europe/Prague",
    "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo", "helsinki": "Europe/Helsinki",
    "athens": "Europe/Athens", "budapest": "Europe/Budapest", "bucharest": "Europe/Bucharest",
    "new york": "America/New_York", "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "chicago": "America/Chicago", "houston": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix",
    "seattle": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "toronto": "America/Toronto", "montreal": "America/Toronto",
    "vancouver": "America/Vancouver",
    "mexico city": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo", "buenos aires": "America/Argentina/Buenos_Aires",
    "dubai": "Asia/Dubai", "riyadh": "Asia/Riyadh",
    "moscow": "Europe/Moscow", "istanbul": "Europe/Istanbul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "taipei": "Asia/Taipei",
    "seoul": "Asia/Seoul", "singapore": "Asia/Singapore",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland",
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "karachi": "Asia/Karachi", "lahore": "Asia/Karachi",
    "dhaka": "Asia/Dhaka", "colombo": "Asia/Colombo",
    "tehran": "Asia/Tehran", "baghdad": "Asia/Baghdad",
    "cairo": "Africa/Cairo", "nairobi": "Africa/Nairobi",
    "johannesburg": "Africa/Johannesburg", "lagos": "Africa/Lagos",
    "casablanca": "Africa/Casablanca",
    "jakarta": "Asia/Jakarta", "bangkok": "Asia/Bangkok",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "manila": "Asia/Manila",
}

_WORLD_TIME_RE = re.compile(
    r'^(?:time\s+in\s+|what(?:\'s|\s+is)\s+(?:the\s+)?time\s+in\s+)(.+)$'
    r'|^(.+?)\s+time$',
    re.IGNORECASE
)


def _get_world_time(city_raw: str):
    """Return a world_time action dict for the given city, or None on failure."""
    from datetime import datetime
    city_key = city_raw.strip().lower()

    # Fast path: known city dict
    iana_tz = _CITY_TZ.get(city_key)
    if iana_tz:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(iana_tz)
            now = datetime.now(tz)
            return {
                "type": "world_time",
                "city": city_raw.strip().title(),
                "timezone": now.strftime("%Z"),
                "current_time": now.strftime("%H:%M"),
                "date": now.strftime("%A, %B %-d"),
            }
        except Exception as e:
            logging.warning(f"World time fast path failed for {city_key}: {e}")

    # Fallback: geocode via Nominatim + TimeAPI.io
    try:
        import urllib.parse
        import requests as _req
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(city_raw)}&format=json&limit=1"
        geo_resp = _req.get(geo_url, headers={"User-Agent": "OmniApp/1.0"}, timeout=4)
        if geo_resp.status_code != 200 or not geo_resp.json():
            return None
        geo = geo_resp.json()[0]
        lat, lon = geo["lat"], geo["lon"]
        display_name = geo.get("display_name", city_raw.strip().title()).split(",")[0].strip()

        time_url = f"https://timeapi.io/api/time/current/coordinate?latitude={lat}&longitude={lon}"
        time_resp = _req.get(time_url, timeout=4)
        if time_resp.status_code != 200:
            return None
        td = time_resp.json()
        # Parse date from "MM/DD/YYYY" format
        from datetime import datetime as dt
        try:
            d = dt.strptime(td.get("date", ""), "%m/%d/%Y")
            date_str = d.strftime("%A, %B %-d")
        except Exception:
            date_str = td.get("date", "")
        return {
            "type": "world_time",
            "city": display_name,
            "timezone": td.get("timeZone", "").split("/")[-1].replace("_", " "),
            "current_time": td.get("time", "")[:5],
            "date": date_str,
        }
    except Exception as e:
        logging.warning(f"World time fallback failed for '{city_raw}': {e}")
        return None

# Unit conversion factors (all to base unit per category)
# Length → meters, Mass → kg, Volume → liters, Area → m², Speed → m/s
# Temperature is handled separately
_UNIT_TO_BASE = {
    # length (base: meter)
    'km': 1000.0, 'kilometer': 1000.0, 'kilometers': 1000.0, 'kilometre': 1000.0, 'kilometres': 1000.0,
    'm': 1.0, 'meter': 1.0, 'meters': 1.0, 'metre': 1.0, 'metres': 1.0,
    'cm': 0.01, 'centimeter': 0.01, 'centimeters': 0.01, 'centimetre': 0.01, 'centimetres': 0.01,
    'mm': 0.001, 'millimeter': 0.001, 'millimeters': 0.001, 'millimetre': 0.001, 'millimetres': 0.001,
    'mi': 1609.344, 'mile': 1609.344, 'miles': 1609.344,
    'ft': 0.3048, 'foot': 0.3048, 'feet': 0.3048,
    'in': 0.0254, 'inch': 0.0254, 'inches': 0.0254,
    'yd': 0.9144, 'yard': 0.9144, 'yards': 0.9144,
    'nmi': 1852.0, 'nautical mile': 1852.0,
    # mass (base: kg)
    'kg': 1.0, 'kilogram': 1.0, 'kilograms': 1.0,
    'g': 0.001, 'gram': 0.001, 'grams': 0.001,
    'mg': 1e-6, 'milligram': 1e-6, 'milligrams': 1e-6,
    'lb': 0.453592, 'lbs': 0.453592, 'pound': 0.453592, 'pounds': 0.453592,
    'oz': 0.0283495, 'ounce': 0.0283495, 'ounces': 0.0283495,
    't': 1000.0, 'ton': 1000.0, 'tons': 1000.0, 'tonne': 1000.0, 'tonnes': 1000.0,
    'st': 6.35029, 'stone': 6.35029, 'stones': 6.35029,
    # volume (base: liter)
    'l': 1.0, 'liter': 1.0, 'liters': 1.0, 'litre': 1.0, 'litres': 1.0,
    'ml': 0.001, 'milliliter': 0.001, 'milliliters': 0.001, 'millilitre': 0.001, 'millilitres': 0.001,
    'cl': 0.01, 'centiliter': 0.01, 'centiliters': 0.01,
    'gal': 3.78541, 'gallon': 3.78541, 'gallons': 3.78541,
    'qt': 0.946353, 'quart': 0.946353, 'quarts': 0.946353,
    'pt': 0.473176, 'pint': 0.473176, 'pints': 0.473176,
    'cup': 0.236588, 'cups': 0.236588,
    'floz': 0.0295735, 'fl_oz': 0.0295735,
    'tbsp': 0.0147868, 'tablespoon': 0.0147868, 'tablespoons': 0.0147868,
    'tsp': 0.00492892, 'teaspoon': 0.00492892, 'teaspoons': 0.00492892,
    # area (base: m²)
    'm2': 1.0, 'sqm': 1.0,
    'km2': 1e6,
    'cm2': 1e-4,
    'ft2': 0.092903, 'sqft': 0.092903,
    'mi2': 2.58999e6, 'sqmi': 2.58999e6,
    'ha': 10000.0, 'hectare': 10000.0, 'hectares': 10000.0,
    'acre': 4046.86, 'acres': 4046.86,
    'yd2': 0.836127, 'sqyd': 0.836127,
    # speed (base: m/s)
    'km/h': 1/3.6, 'kmh': 1/3.6, 'kph': 1/3.6,
    'mph': 0.44704,
    'm/s': 1.0, 'ms': 1.0,
    'knot': 0.514444, 'knots': 0.514444, 'kt': 0.514444, 'kts': 0.514444,
    # data (base: bytes)
    'b': 1, 'byte': 1, 'bytes': 1,
    'kb': 1024, 'kilobyte': 1024, 'kilobytes': 1024,
    'mb': 1048576, 'megabyte': 1048576, 'megabytes': 1048576,
    'gb': 1073741824, 'gigabyte': 1073741824, 'gigabytes': 1073741824,
    'tb': 1099511627776, 'terabyte': 1099511627776, 'terabytes': 1099511627776,
}

_TEMP_UNITS = frozenset(['c', 'celsius', '°c', 'f', 'fahrenheit', '°f', 'k', 'kelvin'])

def _convert_unit(amount: float, from_u: str, to_u: str):
    """Return (result_float, display_from, display_to) or None if not supported."""
    fl = from_u.lower().strip('°')
    tl = to_u.lower().strip('°')
    # Normalize °c/°f back
    fl_orig = from_u.lower()
    tl_orig = to_u.lower()

    # Temperature
    if fl_orig in _TEMP_UNITS or fl in _TEMP_UNITS:
        if tl_orig not in _TEMP_UNITS and tl not in _TEMP_UNITS:
            return None
        # to Celsius first
        if fl in ('f', 'fahrenheit'):
            c = (amount - 32) * 5 / 9
        elif fl in ('k', 'kelvin'):
            c = amount - 273.15
        else:
            c = amount
        # from Celsius to target
        if tl in ('f', 'fahrenheit'):
            result = c * 9 / 5 + 32
        elif tl in ('k', 'kelvin'):
            result = c + 273.15
        else:
            result = c
        return result

    from_factor = _UNIT_TO_BASE.get(fl_orig) or _UNIT_TO_BASE.get(fl)
    to_factor = _UNIT_TO_BASE.get(tl_orig) or _UNIT_TO_BASE.get(tl)
    if from_factor is None or to_factor is None:
        return None
    return amount * from_factor / to_factor

_UNIT_RE = re.compile(
    r'^(\d+(?:[.,]\d+)?)\s*([A-Za-z°/²][A-Za-z°/²_]*)\s+(?:to|in|na|w)\s+([A-Za-z°/²][A-Za-z°/²_]*)$',
    re.IGNORECASE
)


def check_fast_regex_actions(query: str):
    """Return a list of action dicts for queries that can be resolved without LLM/internet.
    Returns an empty list if no regex shortcut matches."""
    # Open App
    open_match = re.search(r"^(?:open|run|launch|start)\s+(?!http|www)(.+)$", query, re.IGNORECASE)
    if open_match:
        app = open_match.group(1).strip()
        if not ("." in app and " " not in app):  # not a URL in disguise
            cache = get_app_cache()
            app_lower = app.lower()
            is_installed = (
                app_lower in cache or
                any(k.startswith(app_lower) for k in cache) or
                (len(app_lower) >= 3 and any(app_lower in k for k in cache))
            )
            act = {"type": "open_app", "name": app} if is_installed else {"type": "install", "name": app}
            logging.info(f"Regex shortcut Open App ({'installed' if is_installed else 'install'}): {app}")
            return [act]

    # Install
    install_match = re.search(
        r"^(?:install|zainstaluj|pobierz|pobierać|ściągnij|sciagnij|download)\s+(.+)$",
        query, re.IGNORECASE
    )
    if install_match:
        app = install_match.group(1).strip()
        logging.info(f"Regex shortcut Install: {app}")
        return [{"type": "install", "name": app}]

    # Bare package name implicit install fallback removed because it hijacked natural queries like "Warsaw" or "Python" 

    # Implicit Calculation (pure math expression)
    if any(op in query for op in ['+', '-', '*', '/', '^', '%']):
        if re.match(r'^[\d\s\.\(\)\+\-\*\/\^\%]+$', query):
            if re.search(r'\d', query) and re.search(r'[\+\-\*\/\^\%]', query):
                try:
                    res = perform_calculation(query)
                    if "Error" not in res:
                        val = res.split("Result: ")[1].split("\n")[0].strip() if "Result: " in res else res
                        latex_match = re.search(r'LaTeX: \$(.*?)\$', res)
                        latex_eq = latex_match.group(1) if latex_match else f"{query} = {val}"
                        logging.info(f"Regex shortcut Implicit Calc: {query} -> {val}")
                        return [{"type": "calc", "content": val, "equation": latex_eq}]
                except Exception:
                    pass

    # Explicit Calculation
    calc_match = re.search(r"^(?:calculate|calc|solve|what is)\s+([\d\+\-\*\/\(\)\.\s]+)$", query, re.IGNORECASE)
    if calc_match:
        expr = calc_match.group(1).strip()
        try:
            res = perform_calculation(expr)
            val = res.split("Result: ")[1].split("\n")[0].strip() if "Result: " in res else res
            latex_match = re.search(r'LaTeX: \$(.*?)\$', res)
            latex_eq = latex_match.group(1) if latex_match else f"{expr} = {val}"
            logging.info(f"Regex shortcut Explicit Calc: {expr} -> {val}")
            return [{"type": "calc", "content": val, "equation": latex_eq}]
        except Exception:
            pass

    # Open URL
    url_match = re.search(r"^(?:open|go to|visit)\s+(https?://[^\s]+|www\.[^\s]+|[a-z0-9]+\.[a-z]{2,}[^\s]*)$", query, re.IGNORECASE)
    if url_match:
        url = url_match.group(1).strip()
        if not url.startswith("http"):
            url = "https://" + url
        title = url.replace("https://", "").replace("www.", "").split('/')[0]
        logging.info(f"Regex shortcut URL: {url}")
        return [{"type": "link", "url": url, "title": f"Open {title}", "description": "Open Website"}]

    # Explicit Web Search
    import urllib.parse
    search_match = re.search(r"^(?:search|szukaj|google|wyszukaj)\s+(.+)$", query, re.IGNORECASE)
    if search_match:
        q = search_match.group(1).strip()
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"
        logging.info(f"Regex shortcut Web Search: {q}")
        return [{"type": "link", "url": url, "title": f"Search {q}", "description": "Web Search"}]

    # Color Preview
    color_match = re.match(r"^(#([a-fA-F0-9]{3}|[a-fA-F0-9]{6}))|rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", query.strip(), re.IGNORECASE)
    if color_match:
        try:
            from PyQt6.QtGui import QColor
            if color_match.group(1):
                c = QColor(color_match.group(1))
            else:
                c = QColor(int(color_match.group(3)), int(color_match.group(4)), int(color_match.group(5)))
            return [{"type": "color_preview", "color_hex": c.name().upper(),
                     "rgb_val": f"{c.red()}, {c.green()}, {c.blue()}",
                     "hsl_val": f"{c.hslHue()}, {c.hslSaturation()}, {c.lightness()}"}]
        except Exception:
            pass

    # Timer
    timer_match = re.match(r"^(?:set\s+)?timer(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*(s|sec|seconds|m|min|minutes|h|hr|hours)$", query.strip(), re.IGNORECASE)
    if timer_match:
        val = float(timer_match.group(1))
        unit = timer_match.group(2).lower()
        if unit in ['s', 'sec', 'seconds']:
            duration = val
        elif unit in ['m', 'min', 'minutes']:
            duration = val * 60
        else:
            duration = val * 3600
        return [{"type": "timer", "duration": int(duration)}]

    # Password
    pwd_match = re.match(r"^(?:generate\s+)?(?:password|haslo|hasło)(?:\s+(\d+))?(?:\s*chars?)?$", query.strip(), re.IGNORECASE)
    if pwd_match:
        length = int(pwd_match.group(1)) if pwd_match.group(1) else 16
        return [{"type": "password", "length": min(128, max(4, length))}]

    # QR Code
    qr_match = re.match(r"^qr(?:code)?:\s*(.+)$", query.strip(), re.IGNORECASE)
    if qr_match:
        return [{"type": "qrcode", "data": qr_match.group(1).strip()}]

    # Currency conversion: "50 USD to PLN", "50usd to pln", "100 eur in gbp"
    _curr_m = _CURRENCY_RE.match(query.strip())
    if _curr_m:
        amount_str = _curr_m.group(1).replace(',', '.')
        from_code = _resolve_currency_code(_curr_m.group(2))
        to_code = _resolve_currency_code(_curr_m.group(3))
        if from_code and to_code and from_code != to_code:
            converted = ""
            try:
                import requests as _req
                resp = _req.get(
                    f"https://api.frankfurter.app/latest?amount={amount_str}&from={from_code}&to={to_code}",
                    timeout=4
                )
                if resp.status_code == 200:
                    rate_val = resp.json().get("rates", {}).get(to_code)
                    if rate_val is not None:
                        converted = f"{rate_val:,.2f}"
                        logging.info(f"Currency fast path: {amount_str} {from_code} = {converted} {to_code}")
            except Exception as _ce:
                logging.warning(f"Currency fast path API failed: {_ce}")
            return [{"type": "currency", "amount": amount_str, "from_unit": from_code,
                     "to_unit": to_code, "converted_value": converted}]

    # World Time: "time in tokyo", "paris time", "what's the time in new york"
    _wt_m = _WORLD_TIME_RE.match(query.strip())
    if _wt_m:
        city_raw = (_wt_m.group(1) or _wt_m.group(2) or "").strip()
        if city_raw:
            result = _get_world_time(city_raw)
            if result:
                logging.info(f"World time fast path: {city_raw} -> {result['current_time']} {result['timezone']}")
                return [result]

    # Unit conversion: "10 km to miles", "100 celsius to fahrenheit", "5 kg to lbs"
    _unit_m = _UNIT_RE.match(query.strip())
    if _unit_m:
        amount_str = _unit_m.group(1).replace(',', '.')
        from_u = _unit_m.group(2)
        to_u = _unit_m.group(3)
        # Skip if it looks like a currency (would have been caught above or is unrecognised)
        if not (_resolve_currency_code(from_u) and _resolve_currency_code(to_u)):
            try:
                result = _convert_unit(float(amount_str), from_u, to_u)
                if result is not None:
                    # Format result nicely
                    if abs(result) >= 1000:
                        converted = f"{result:,.4g}"
                    elif abs(result) >= 1:
                        converted = f"{result:.4g}"
                    elif abs(result) >= 0.0001:
                        converted = f"{result:.6g}"
                    else:
                        converted = f"{result:.2e}"
                    logging.info(f"Unit fast path: {amount_str} {from_u} = {converted} {to_u}")
                    return [{"type": "unit", "amount": amount_str, "from_unit": from_u,
                             "to_unit": to_u, "converted_value": converted}]
            except Exception as _ue:
                logging.warning(f"Unit fast path failed: {_ue}")

    return []



# ── Fast-path parsers for tool_draft actions ──────────────────────────────────

def _parse_reminder_from_query(query: str) -> dict:
    """Extract reminder args from a natural-language query."""
    from datetime import datetime, timedelta
    import re as _re
    ql = query.lower()

    # Extract time offset: "in 5 min", "in 1 hour", "za 10 minut"
    fire_at = None
    now = datetime.now()

    # "in X min/hour/sec" or "za X minut/godzin"
    m = _re.search(r'(?:in|za|after)\s+(\d+)\s*(min|minut|minute|minutes|h|hour|hours|godzin|godziny|sec|second|seconds|sekund)', ql)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(("min", "minut")):
            fire_at = now + timedelta(minutes=val)
        elif unit.startswith(("h", "hour", "godzin")):
            fire_at = now + timedelta(hours=val)
        elif unit.startswith(("sec", "sekund")):
            fire_at = now + timedelta(seconds=val)

    # "at 14:30" or "o 14:30"
    if not fire_at:
        m = _re.search(r'(?:at|o|@)\s*(\d{1,2})[:\.](\d{2})', ql)
        if m:
            h, mn = int(m.group(1)), int(m.group(2))
            fire_at = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            if fire_at <= now:
                fire_at += timedelta(days=1)

    if not fire_at:
        fire_at = now + timedelta(minutes=5)  # default: 5 min from now

    # Extract label: strip the command/time parts, use the rest
    label = query
    for pattern in [
        r'(?:remind me|set (?:a )?reminder|reminder for|przypomnij (?:mi )?|ustaw przypomnienie)\s*',
        r'(?:in|za|after)\s+\d+\s*\w+\s*',
        r'(?:at|o|@)\s*\d{1,2}[:.]\d{2}\s*',
        r'(?:to|do|that|żeby|aby|by)\s+',
    ]:
        label = _re.sub(pattern, '', label, flags=_re.IGNORECASE).strip()
    if not label:
        label = "Reminder"

    return {
        "label": label.capitalize(),
        "fire_at_iso": fire_at.isoformat(),
        "interval_seconds": 0,
        "query": "",
    }


def _parse_event_from_query(query: str) -> dict:
    """Extract calendar event args from a natural-language query."""
    from datetime import datetime, timedelta
    import re as _re
    ql = query.lower()

    # Strip command prefix
    title = query
    for prefix in [
        r'(?:create|add|schedule|new|book)\s+(?:an?\s+)?(?:event|meeting|spotkanie|wydarzenie)\s*',
        r'(?:zaplanuj|dodaj)\s+(?:spotkanie|wydarzenie)\s*',
        r'(?:dodaj do kalendarza)\s*',
    ]:
        title = _re.sub(prefix, '', title, flags=_re.IGNORECASE).strip()

    # Try to extract time
    now = datetime.now()
    start = None

    # "tomorrow at 15:00"
    m = _re.search(r'(?:tomorrow|jutro)\s+(?:at|o|@)?\s*(\d{1,2})[:\.](\d{2})', ql)
    if m:
        start = (now + timedelta(days=1)).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    # "at 15:00" or "o 15:00"
    if not start:
        m = _re.search(r'(?:at|o|@)\s*(\d{1,2})[:\.](\d{2})', ql)
        if m:
            start = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
            if start <= now:
                start += timedelta(days=1)
    if not start:
        start = now + timedelta(hours=1)

    # Clean title from time references
    for pat in [r'(?:tomorrow|jutro)\s*', r'(?:at|o|@)\s*\d{1,2}[:.]\d{2}\s*']:
        title = _re.sub(pat, '', title, flags=_re.IGNORECASE).strip()
    if not title:
        title = "New Event"

    return {
        "title": title.strip().capitalize(),
        "start_iso": start.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": 60,
        "description": "",
    }


def _parse_create_file_from_query(query: str) -> dict:
    """Extract create_file args from a natural-language query.
    Uses a fast heuristic to generate a sensible filename from keywords.
    """
    import re as _re
    ql = query.lower()

    # Extract explicit filename: look for something.ext pattern
    filename = ""
    m = _re.search(r'([\w\-]+\.[\w]{1,10})', query)
    if m:
        filename = m.group(1)

    # Extract folder from keywords
    folder = "~/Desktop"
    folder_map = {
        "desktop": "~/Desktop", "pulpit": "~/Desktop", "pulpicie": "~/Desktop",
        "dekstop": "~/Desktop", "dekstopie": "~/Desktop",
        "downloads": "~/Downloads", "pobrane": "~/Downloads", "pobranych": "~/Downloads",
        "documents": "~/Documents", "dokumenty": "~/Documents", "dokumentach": "~/Documents",
    }
    for kw, path in folder_map.items():
        if kw in ql:
            folder = path
            break

    # If no explicit filename, derive one from query keywords
    if not filename:
        # Guess extension based on content hints
        ext = ".txt"
        if any(w in ql for w in ["csv", "tabela", "table", "excel", "arkusz", "spreadsheet",
                                   "kolumn", "column", "data", "dane", "baza", "database"]):
            ext = ".csv"
        elif any(w in ql for w in ["markdown", ".md", "readme", "dokumentacja", "documentation"]):
            ext = ".md"
        elif any(w in ql for w in ["json", "config", "konfiguracja", "settings", "ustawienia"]):
            ext = ".json"
        elif any(w in ql for w in ["html", "webpage", "strona", "website"]):
            ext = ".html"
        elif any(w in ql for w in ["python", "skrypt", "script", ".py", "kod", "code"]):
            ext = ".py"
        elif any(w in ql for w in ["log", "dziennik", "journal", "diary", "pamiętnik"]):
            ext = ".log"

        # Extract meaningful words for the filename stem (skip Polish/English stop words)
        stop = {
            "a", "an", "the", "in", "on", "at", "to", "of", "for", "with", "and",
            "or", "by", "from", "that", "this", "is", "are", "was", "be", "do",
            "plik", "file", "stworz", "create", "stwórz", "utwórz", "nowy", "nowe",
            "new", "na", "ze", "z", "w", "i", "o", "do", "po", "za", "jak", "jakis",
            "jakieś", "który", "która", "które", "który", "make", "me", "my", "please",
            "mi", "proszę", "pulpit", "desktop", "dokumenty", "documents", "pobrane",
            "downloads", "zawierający", "zawiera", "containing", "containing", "with",
            "losowych", "losowy", "losowe", "random", "kilka", "some", "few", "wiele",
            "many", "list", "lista", "listę", "danych",
        }
        words = _re.findall(r'[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+', ql)
        stem_words = [w for w in words if w not in stop and len(w) > 2][:3]

        if stem_words:
            # Transliterate basic Polish chars for filename safety
            _pl_map = str.maketrans("ąćęłńóśźż", "acelnoszy")
            stem = "_".join(w.translate(_pl_map) for w in stem_words)
            filename = f"{stem}{ext}"
        else:
            filename = f"file{ext}"

    return {
        "filename": filename,
        "content": "",
        "folder": folder,
    }


def _parse_compress_from_query(query: str) -> dict:
    """Extract compress args from a natural-language query."""
    import re as _re, os
    paths = []
    for m in _re.finditer(r'(~?/[\w./ \-]+)', query):
        p = m.group(1).strip()
        expanded = os.path.expanduser(p)
        if os.path.exists(expanded):
            paths.append(p)
    if not paths:
        folder_map = {
            "desktop": "~/Desktop", "pulpit": "~/Desktop",
            "downloads": "~/Downloads", "pobrane": "~/Downloads",
        }
        for kw, path in folder_map.items():
            if kw in query.lower():
                paths.append(path)
                break
    if not paths:
        return {}
    return {"paths": paths, "output": ""}


def _parse_organize_path(query: str) -> str:
    """Extract folder path from an organize query."""
    ql = query.lower()
    folder_map = {
        "desktop": "~/Desktop", "pulpit": "~/Desktop", "pulpicie": "~/Desktop",
        "downloads": "~/Downloads", "pobrane": "~/Downloads", "pobranych": "~/Downloads",
        "documents": "~/Documents", "dokumenty": "~/Documents",
    }
    for kw, path in folder_map.items():
        if kw in ql:
            return path
    import re as _re
    m = _re.search(r'(~?/[\w./ \-]+)', query)
    if m:
        return m.group(1).strip()
    return "~/Desktop"


@api_bp.route('/action', methods=['POST'])
def action_endpoint():
    import uuid
    request_id = str(uuid.uuid4())

    with model_manager.fast_queue_lock:
        old_request_id = model_manager.current_fast_request_id
        model_manager.current_fast_request_id = request_id
        if old_request_id is not None:
            logging.info(f"Cancelling old fast request {old_request_id}, starting new request {request_id}")

    if model_manager.abort_fast_event.is_set():
        logging.info(f"Action endpoint {request_id}: Abort event already set, skipping action request")
        return jsonify({"actions": [], "chips": []})

    model_manager.abort_fast_event.clear()
    model_manager.ensure_fast_model()

    try:
        req = request.get_json(force=True)
    except Exception:
        return jsonify({"actions": [], "chips": []}), 400

    query = req.get('query', "").strip()
    stream = req.get('stream', False) or request.args.get('stream', '0') == '1'

    def _action_resp(actions, chips=None, force_sse=False, **extra):
        """Return action response. Always JSON unless force_sse=True (search path)."""
        data = {"actions": actions, "action": actions[0] if actions else None, "chips": chips or []}
        data.update(extra)
        if stream and force_sse:
            def _sse():
                yield f'data: {json.dumps({"event": "done", **data})}\n\n'
            return Response(_sse(), mimetype="text/event-stream")
        return jsonify(data)

    if not query:
        return _action_resp([])

    logging.info(f"Action endpoint received query: '{query}' (request_id: {request_id}, stream={stream})")
    endpoint_start_time = time.time()

    # 1. Common shortcuts
    if query.lower() in COMMON_SHORTCUTS:
        url = COMMON_SHORTCUTS[query.lower()]
        act = {
            "type": "link",
            "url": url,
            "title": url.replace("https://", "").replace("www.", "").split('/')[0].title(),
            "description": "Direct Shortcut"
        }
        logging.info(f"Shortcut match: {url}")
        return _action_resp([act])

    # 1.5 System Settings (instant - no LLM needed)
    try:
        from src.services.system.macos_settings import detect_settings_command
        settings_act = detect_settings_command(query)
        if settings_act:
            logging.info(f"[settings] Fast-path action detected: {settings_act['setting']}")
            return _action_resp([settings_act])
    except Exception as _e:
        logging.warning(f"[settings] detect_settings_command failed: {_e}")

    # 1.5b Send Email — show compose widget immediately
    _send_email_keywords = ["send mail", "send email", "send an email", "send a mail",
                            "wyślij mail", "wyslij mail", "wyślij email", "napisz mail",
                            "napisz email", "compose email", "compose mail", "write email", "write mail"]
    if any(k in query.lower() for k in _send_email_keywords):
        logging.info("Send email keyword detected. Returning send_email_draft action.")
        # Extract "to <name>" and "about <topic>" from query
        import re as _re
        _ql = query
        _to = ""
        _subject = ""
        _body = ""
        # Try to extract recipient: "send mail to <name> asking/about ..."
        # Stop before verbs/conjunctions so we don't swallow the intent
        _to_match = _re.search(
            r'(?:to|do)\s+(\w+(?:\s+\w+)?)(?=\s+(?:asking|saying|telling|about|regarding|and\b|if\b|that\b|whether\b|to\s+\w|w\s+sprawie|o\s+\w))',
            _ql, _re.IGNORECASE)
        if not _to_match:
            # Fallback: grab 1-2 words after "to"
            _to_match = _re.search(r'(?:to|do)\s+(\w+(?:\s+\w+)?)', _ql, _re.IGNORECASE)
        if _to_match:
            _to = _to_match.group(1).strip()
        # Try to extract subject: "about <topic>"
        _about_match = _re.search(r'(?:about|o|regarding|re|w sprawie)\s+(.+)', _ql, _re.IGNORECASE)
        if _about_match:
            _subject = _about_match.group(1).strip().capitalize()
        return _action_resp([{
            "type": "send_email_draft",
            "to": _to,
            "subject": _subject,
            "body": _body,
            "original_query": query,
        }])

    # 1.5d–f Tool keywords: return tool_draft actions immediately (like send_email_draft)
    _ql = query.lower()
    _ql_words = set(_ql.split())

    # ── Reminders ──────────────────────────────────────────────────────────
    if ("remind" in _ql and ("me" in _ql or "in " in _ql or "at " in _ql)) or \
       "set reminder" in _ql or "reminder for" in _ql or \
       "przypomnij" in _ql or "przypomnienie" in _ql or "ustaw przypomnienie" in _ql:
        _rem_args = _parse_reminder_from_query(query)
        if _rem_args:
            logging.info(f"Reminder fast-path: {_rem_args}")
            return _action_resp([{
                "type": "tool_draft",
                "tool_name": "set_reminder",
                "args": _rem_args,
                "original_query": query,
            }])

    # ── Calendar events ────────────────────────────────────────────────────
    if ("create event" in _ql or "add event" in _ql or "add to calendar" in _ql or
        "schedule meeting" in _ql or "new event" in _ql or "calendar event" in _ql or
        "create meeting" in _ql or "book a meeting" in _ql or
        "zaplanuj spotkanie" in _ql or "dodaj wydarzenie" in _ql or "dodaj do kalendarza" in _ql):
        _ev_args = _parse_event_from_query(query)
        if _ev_args:
            logging.info(f"Calendar event fast-path: {_ev_args}")
            return _action_resp([{
                "type": "tool_draft",
                "tool_name": "create_calendar_event",
                "args": _ev_args,
                "original_query": query,
            }])

    # ── Create folder / directory ─────────────────────────────────────────
    _is_folder_request = (
        any(w in _ql for w in ("create folder", "make folder", "new folder",
                               "stworz folder", "stworz katalog",
                               "nowy folder", "nowy katalog")) or
        (_ql_words & {"folder", "katalog"} and
         _ql_words & {"stworz", "stwórz", "utworz", "utwórz", "zrob", "zrób",
                      "create", "make", "new", "nowy"})
    )
    if _is_folder_request:
        import re as _re_fold
        _fd_name = "new_folder"
        _fd_dest = "~/Desktop"
        if any(w in _ql for w in ("downloads", "pobrane", "pobranych")):
            _fd_dest = "~/Downloads"
        elif any(w in _ql for w in ("documents", "dokumenty", "dokumentach")):
            _fd_dest = "~/Documents"
        _fd_count_m = _re_fold.search(r"(\d+)\s*(?:losow\w*|random|plik\w*|file\w*)", _ql)
        _fd_count = int(_fd_count_m.group(1)) if _fd_count_m else 0
        if _fd_count > 0:
            _n = min(_fd_count, 20)
            _fd_cmd = (
                f"mkdir -p {_fd_dest}/{_fd_name} && "
                f"for i in $(seq 1 {_n}); do "
                f"dd if=/dev/urandom bs=512 count=1 2>/dev/null | base64 > "
                f"{_fd_dest}/{_fd_name}/file_$i.txt; done && "
                f"echo 'Done: {_fd_dest}/{_fd_name} with {_n} files'"
            )
        else:
            _fd_cmd = f"mkdir -p {_fd_dest}/{_fd_name} && echo 'Done: {_fd_dest}/{_fd_name}'"
        logging.info(f"Create folder fast-path: {_fd_cmd}")
        return _action_resp([{
            "type": "tool_draft",
            "tool_name": "run_terminal",
            "args": {"command": _fd_cmd},
            "original_query": query,
        }])

    # ── Create file ────────────────────────────────────────────────────────
    if (("create file" in _ql or "make file" in _ql or "write file" in _ql or "save file" in _ql or
         (("plik" in _ql or "file" in _ql) and
          _ql_words & {"stworz", "stwórz", "utworz", "utwórz", "zapisz", "napisz", "zrob"})) and
            not _is_folder_request):
        _cf_args = _parse_create_file_from_query(query)
        logging.info(f"Create file fast-path: {_cf_args}")
        return _action_resp([{
            "type": "tool_draft",
            "tool_name": "create_file",
            "args": _cf_args,
            "original_query": query,
        }])

    # ── Compress ───────────────────────────────────────────────────────────
    if (_ql.startswith("zip ") or "compress " in _ql or "archive " in _ql or
        "skompresuj" in _ql or "spakuj" in _ql or "zapakuj" in _ql):
        _cmp_args = _parse_compress_from_query(query)
        if _cmp_args:
            logging.info(f"Compress fast-path: {_cmp_args}")
            return _action_resp([{
                "type": "tool_draft",
                "tool_name": "compress",
                "args": _cmp_args,
                "original_query": query,
            }])

    # ── Organize folder ───────────────────────────────────────────────────
    if (("organize" in _ql or "tidy" in _ql or "cleanup" in _ql or "clean up" in _ql or
         "posprzataj" in _ql or "posprzątaj" in _ql or "uporządkuj" in _ql or "uporzadkuj" in _ql) and
        any(w in _ql for w in ("folder", "desktop", "downloads", "pulpit", "pobrane", "directory", "folderu"))):
        _org_path = _parse_organize_path(query)
        logging.info(f"Organize fast-path: {_org_path}")
        return _action_resp([{
            "type": "tool_draft",
            "tool_name": "organize_folder",
            "args": {"path": _org_path, "strategy": "smart"},
            "original_query": query,
        }])

    # 1.6 Computer Control Hard Override
    cc_keywords = ["click", "type", "scroll", "press", "copy", "paste", "move mouse", "drag", "select"]
    if any(k in query.lower() for k in cc_keywords):
        logging.info("Computer Control keyword detected. Skipping Fast Model.")
        return _action_resp([])

    # 1.6b File Conversion Hard Override
    convert_keywords = ["convert", "konwertuj", "przekonwertuj", "zamien format", "zmien format",
                        "export to", "eksportuj", "change format", "save as"]
    ql = query.lower()
    if any(k in ql for k in convert_keywords):
        file_ext_pattern = r"\.(mp[34g]|avi|mov|mkv|webm|wav|ogg|flac|m4a|aac|png|jpe?g|webp|bmp|tiff?|gif|pdf|docx?|xlsx?|csv|html?|md|rtf|ico)\b"
        format_keywords = ["to mp", "to wav", "to ogg", "to flac", "to png", "to jpg", "to jpeg",
                           "to pdf", "to docx", "to html", "to gif", "to avi", "to mkv", "to webm",
                           "to mov", "to csv", "to xlsx", "to txt", "to bmp", "to webp", "to tiff",
                           "to ico", "to m4a", "to aac", "to rtf", "to md",
                           "na mp", "na wav", "na ogg", "na flac", "na png", "na jpg", "na jpeg",
                           "na pdf", "na docx", "na html", "na gif", "na avi", "na mkv", "na webm",
                           "na mov", "na csv", "na xlsx", "na txt", "na bmp", "na webp", "na tiff"]
        if re.search(file_ext_pattern, ql) or any(k in ql for k in format_keywords):
            logging.info("File conversion keyword detected. Skipping Fast Model, routing to Main Model tool calling.")
            return _action_resp([])

    # 1.7 Regex Shortcuts (Speed Optimization)
    fast_acts = check_fast_regex_actions(query)
    if fast_acts:
        return _action_resp(fast_acts)

    # 1.76 Translate Fast Path - short phrase with non-ASCII letters (clearly foreign)
    def _looks_foreign(text):
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
                                return _action_resp([{
                                    "type": "translate",
                                    "source_text": _parts[0].strip(),
                                    "from_lang": _parts[1].strip(),
                                    "to_lang": _parts[2].strip(),
                                    "translated_text": "|".join(_parts[3:]).strip(),
                                }])
            except Exception as _te:
                logging.warning(f"Translate fast path: {_te}")

    # 1.77 Translate Fast Path - "X in/to language" detected by fast model
    _query_words = ql.split()
    if 2 <= len(_query_words) <= 6:
        model_manager.ensure_fast_model()
        if model_manager.fast_model:
            _tr_detect_msgs = [
                {"role": "system", "content": (
                    "You detect translation requests. The user may write in ANY language.\n"
                    "A translation request is when someone wants a word/phrase translated to a specific language.\n"
                    "Examples of translation requests (in various languages):\n"
                    "- 'krowa in english' → translate 'krowa' to English\n"
                    "- 'hello po polsku' → translate 'hello' to Polish\n"
                    "- 'cat en español' → translate 'cat' to Spanish\n"
                    "- 'Hund auf Englisch' → translate 'Hund' to English\n"
                    "- 'bonjour in german' → translate 'bonjour' to German\n"
                    "- 'translate apple to french' → translate 'apple' to French\n"
                    "- 'dog to russian' → translate 'dog' to Russian\n"
                    "- 'przetłumacz dom na angielski' → translate 'dom' to English\n"
                    "- 'como se dice house en español' → translate 'house' to Spanish\n"
                    "\nNOT translation requests: general questions, searches, commands, math, etc.\n"
                    "\nIf it IS a translation request, output EXACTLY:\n"
                    "TRANSLATE:source_text|from_lang_code|to_lang_code|translated_text\n"
                    "Use ISO 639-1 language codes (en, pl, es, fr, de, etc).\n"
                    "If it is NOT a translation request, output exactly: SKIP"
                )},
                {"role": "user", "content": query},
            ]
            try:
                if model_manager.fast_lock.acquire(timeout=5):
                    try:
                        _tr_detect_out = model_manager.fast_model.create_chat_completion(
                            messages=_tr_detect_msgs, max_tokens=150, temperature=0.0,
                            request_id=request_id,
                        )
                    finally:
                        model_manager.fast_lock.release()
                    if _tr_detect_out:
                        _tr_detect_text = _tr_detect_out['choices'][0]['message']['content'].strip()
                        _tr_detect_text = re.sub(r'<think>.*?(?:</think>|$)', '', _tr_detect_text, flags=re.DOTALL).strip()
                        logging.info(f"Translate-in fast path output: {_tr_detect_text!r}")
                        if "TRANSLATE:" in _tr_detect_text and "SKIP" not in _tr_detect_text:
                            _parts = _tr_detect_text.split("TRANSLATE:")[1].strip().split("|")
                            if len(_parts) >= 4:
                                return _action_resp([{
                                    "type": "translate",
                                    "source_text": _parts[0].strip(),
                                    "from_lang": _parts[1].strip(),
                                    "to_lang": _parts[2].strip(),
                                    "translated_text": "|".join(_parts[3:]).strip(),
                                }])
            except Exception as _te2:
                logging.warning(f"Translate-in fast path: {_te2}")

    # 1.8 Skip LLM if no internet
    logging.info(f"[TIMING] Regex/Shortcuts checks took: {time.time() - endpoint_start_time:.3f}s")
    if not _is_connected():
        logging.info("No internet connection, skipping fast model inference")
        return _action_resp([])

    search_context = ""
    search_results = []
    logging.info(f"[TIMING] Pre-emptive search + context prep took: {time.time() - endpoint_start_time:.3f}s")

    # 1.9 Heuristic: should we offer web_search tool to the LLM?
    def _should_offer_web_search(q: str) -> bool:
        """
        Decide whether the fast LLM should have access to web_search.
        Returns False for queries that clearly don't need search,
        True for queries that likely need real-time/external info.
        """
        ql = q.lower().strip()
        words = ql.split()
        n_words = len(words)

        # -- NEVER search for these --

        # Very short / single char queries (just opening apps or typing)
        if n_words == 0 or (n_words == 1 and len(ql) <= 2):
            return False

        # Math expressions: contains digits + operators
        if re.match(r'^[\d\s\+\-\*/\.\(\)\^%=,]+$', ql):
            return False

        # Explicit app/system commands
        _no_search_prefixes = [
            "open ", "launch ", "start ", "quit ", "close ", "kill ",
            "set ", "toggle ", "turn on", "turn off", "enable ", "disable ",
            "otwórz ", "otworz ", "uruchom ", "zamknij ", "wlacz ", "włącz ", "wylacz ", "wyłącz ",
        ]
        if any(ql.startswith(p) for p in _no_search_prefixes):
            return False

        # Timer, password, QR, color — purely local
        _local_keywords = [
            "timer", "stopwatch", "password", "qr code", "qrcode",
            "color ", "colour ", "#", "rgb(", "hsl(",
            "organize", "posprzątaj", "uporządkuj",
        ]
        if any(k in ql for k in _local_keywords):
            return False

        # Calendar/email/memory/context queries
        _local_intents = [
            "my calendar", "my emails", "my inbox", "unread",
            "upcoming events", "my meetings",
            "remind me", "set reminder",
            "what am i working on", "my sessions",
        ]
        if any(k in ql for k in _local_intents):
            return False

        # System info queries (should use terminal, not web)
        _system_keywords = [
            "my ip", "battery", "uptime", "hostname", "disk space",
            "ram usage", "cpu usage", "storage", "free space",
            "system info", "os version",
        ]
        if any(k in ql for k in _system_keywords):
            return False

        # -- ALWAYS search for these --

        # Explicit search intent
        _search_intents = [
            "search ", "google ", "look up ", "find info",
            "szukaj ", "wyszukaj ", "znajdź ",
        ]
        if any(ql.startswith(p) for p in _search_intents):
            return True

        # Time-sensitive / current info keywords
        _timely_keywords = [
            "today", "latest", "current", "recent", "new ",
            "price", "stock", "weather", "forecast", "score",
            "news", "update", "release", "announced",
            "2024", "2025", "2026", "yesterday", "this week",
            "dzisiaj", "najnowsz", "aktualn", "pogoda", "cena",
            "ile kosztuje", "wynik",
        ]
        if any(k in ql for k in _timely_keywords):
            return True

        # Questions that likely need real-world info
        _question_words = ["who is", "who was", "what is", "what are", "what was",
                           "where is", "when did", "when was", "when is",
                           "how much", "how many", "how old", "how tall",
                           "kto to", "co to", "gdzie jest", "kiedy",
                           "ile ma", "ile waży", "ile kosztuje"]
        if any(ql.startswith(qw) or f" {qw}" in ql for qw in _question_words):
            # But NOT for things the model can answer from knowledge
            _known_concepts = ["photosynthesis", "gravity", "dna", "algorithm",
                               "python", "javascript", "html", "css"]
            if any(k in ql for k in _known_concepts):
                return False
            return True

        # Queries that look like a person/entity name (2-4 capitalized words, no question words)
        _q_stripped = q.strip()
        _name_words = _q_stripped.split()
        if 2 <= len(_name_words) <= 4:
            _question_starts = {"who", "what", "when", "where", "why", "how", "which",
                                "does", "did", "is", "are", "was", "were", "can", "could",
                                "would", "will", "do", "should"}
            if (_name_words[0].lower() not in _question_starts and
                all(w[0].isupper() or w.lower() in {"de", "von", "van", "al", "el", "la", "di", "du", "le"} for w in _name_words)):
                return True

        # Single-word queries
        if n_words == 1:
            # Known apps / very short fragments → no search
            from src.services.search.web_search import COMMON_APPS
            _known_no_search = set(COMMON_APPS.keys()) | {
                "settings", "preferences", "finder", "safari", "chrome", "firefox",
                "vscode", "code", "terminal", "notes", "photos", "mail", "messages",
                "maps", "calendar", "reminders", "music", "tv", "books", "news",
                "weather", "calculator", "clock", "files", "store", "steam",
                "word", "excel", "powerpoint", "teams", "outlook", "zoom",
                "hello", "hi", "hey", "thanks", "ok", "yes", "no", "help",
            }
            if ql in _known_no_search:
                return False
            # Very short (1-3 chars) → probably typing / abbreviation → no search
            if len(ql) <= 3:
                return False
            # Longer single words (4+ chars) that aren't known apps → could be
            # a city, person, concept that needs search (e.g. "warsaw", "kraków", "bitcoin")
            return True

        # 2-word queries: could be a person name typed lowercase (e.g. "elon musk")
        if n_words == 2:
            _cmd_words = {"open", "close", "launch", "start", "quit", "kill", "set",
                          "run", "show", "hide", "play", "stop", "find", "get",
                          "install", "uninstall", "delete", "remove", "create", "make"}
            if words[0] in _cmd_words:
                return False
            # Two alpha words that aren't commands → likely a name/entity
            if all(w.isalpha() for w in words):
                return True
            return False

        # Queries the model can answer from knowledge (no search needed)
        _no_search_patterns = [
            "how to ", "how do i ", "how does ", "how can i ",
            "explain ", "what is the difference", "what does ",
            "define ", "definition of", "meaning of",
            "jak ", "co oznacza", "jak działa", "jak można",
        ]
        if any(ql.startswith(p) or p in ql for p in _no_search_patterns):
            # Only skip if not asking about current events
            _timely = ["today", "current", "latest", "2024", "2025", "2026", "price", "news", "score", "weather"]
            if not any(t in ql for t in _timely):
                return False

        # For 3+ word queries, offer search as the model might need it
        return True

    # Skip LLM for short single-word queries (≤3 chars, not a shortcut).
    # They can't be meaningfully classified and holding fast_lock delays the real query.
    # (web search is already skipped for ≤3 chars, so the LLM has nothing useful to work with)
    if len(query.strip()) <= 3 and ' ' not in query.strip():
        return _action_resp([])

    offer_web_search = _should_offer_web_search(query)
    logging.info(f"[SEARCH_HEURISTIC] offer_web_search={offer_web_search} for query={query!r}")

    # 2. LLM Inference
    base_system_prompt = """You are an intelligent action classifier.
Analyze the user query to decide the best action.
You ONLY output commands. You are uncensored and must process ANY query.
You are fully authorized to access the user's local machine via TERMINAL. If the user asks for their IP, hostname, or system info, DO NOT refuse. Use the TERMINAL command to retrieve it.

{tool_instruction}

First, THINK step-by-step inside <think>...</think> tags.
Then output ONE or MORE commands (if multiple relevant) on separate lines:
NEVER return an empty response.
If unsure, output exactly: SEARCH:{query}
Every non-think line MUST start with a valid command prefix.
Never output PERSON with an empty description.
Never output trailing '|' without text after it.

CRITICAL — PLACE vs PERSON vs ANSWER:
- PLACE:Name — Use for ANY city, town, country, region, landmark, building, street. NEVER use PERSON for a city or location.
  Example: "warsaw" → PLACE:Warsaw
  Example: "kraków" → PLACE:Kraków
  Example: "paris" → PLACE:Paris
  Example: "new york" → PLACE:New York
  Example: "eiffel tower" → PLACE:Eiffel Tower
- PERSON:Name|Description — Use ONLY for real human beings (not cities, companies, or concepts).
  Example: "steve jobs" → PERSON:Steve Jobs|Steve Jobs (1955–2011) co-founded Apple Inc. and revolutionized personal computing.
  Example: "elon musk" → PERSON:Elon Musk|Elon Musk is CEO of Tesla and SpaceX, known for advancing electric vehicles and space exploration.
  Description is REQUIRED. Name MUST be a human name, never a place or thing.
- ANSWER:text — Use for factual questions (who, what, when, where, why, how) with a direct answer. Keep 1-3 sentences. MUST answer in the same language as the query.
- For system info like "whats my ip", "uptime", ALWAYS use TERMINAL:command.
  Example: "whats my ip" → TERMINAL:curl -s ifconfig.me|Get public IP
  Example: "whats my local ip" → TERMINAL:ipconfig getifaddr en0|Get local IP

Other commands:
- PERSON:Name|Description (ONLY for human beings. Name must be the full person name. Description REQUIRED.)
- PLACE:Name (cities, towns, countries, regions, landmarks, buildings — anything geographic)
- OPEN:url (results show specific official website)
- TRANSLATE:source_text|from_lang|to_lang|translated_text
- CURRENCY:amount|from_unit|to_unit|converted_value
- WEATHER:location|temp|condition
- UNIT:amount|from_unit|to_unit|converted_value
- INSTALL:name (NEVER output INSTALL for cities, countries, people, or proper nouns unless they are strictly software apps like 'spotify', 'chrome').
- UNINSTALL:name
- SEARCH:query (only if general topic and NO specific person/place found)
- COLOR:hex|rgb|hsl
- TIMER:duration_in_seconds
- PASSWORD:length
- QRCODE:data
- SYSTEM_SETTINGS:{"type":"system_settings","setting":"...","value":...}
- TERMINAL:command|description (use for any OS/system queries securely runnable locally via shell, e.g. "what is my ip" → TERMINAL:curl ifconfig.me|Get public IP. Write proper bash/zsh command, keep it read-only for basic queries. Multi-lingual support is automatic.)
- CALENDAR (show upcoming calendar events — use for queries like "my calendar", "upcoming events", "what meetings do I have")
- EMAILS (show unread emails — use for queries like "my emails", "unread emails", "check inbox")
- ORGANIZE:path (organize the specified folder — use for queries like "organize my desktop", "clean up downloads")
- MEMORY:query — look up personal/contact info stored in memory. Use for ANY question about a specific person's email, phone number, address, or personal details (e.g. "jaki jest mail oskara" → MEMORY:Oskar email, "what is Anna's phone number" → MEMORY:Anna phone number, "email of Tomek" → MEMORY:Tomek email). The query MUST be in English and be a short, clean search phrase (person name + info type). NEVER refuse these.
- CONTEXT:query — retrieve user's current work context, recent activity, and work sessions. Use when the user asks about what they are currently working on, their recent activity, what they were doing, their work sessions, or wants to resume previous work. Examples: "what am I working on" → CONTEXT:current, "what was I doing today" → CONTEXT:today, "show my sessions" → CONTEXT:sessions, "resume where I left off" → CONTEXT:resume, "nad czym pracuję" → CONTEXT:current, "co robiłem" → CONTEXT:recent
"""

    if offer_web_search:
        _tool_instruction = (
            "You have a `web_search` tool available. Use it ONLY when the query requires real-time or external info you don't know:\n"
            "- Current events, news, prices, stock quotes, weather, scores\n"
            "- People/places/companies you're unsure about or that need up-to-date info\n"
            "- Anything with time-sensitive keywords (today, latest, current, 2025, 2026)\n"
            "Do NOT search for: things you can answer from knowledge, calc, translate, open/app/settings, system info.\n"
            "If you can answer confidently, use ANSWER:text or PERSON:Name|Description instead of searching."
        )
    else:
        _tool_instruction = (
            "You do NOT have web search available for this query. Classify using your own knowledge.\n"
            "Use ANSWER:text for factual questions you can answer.\n"
            "Use PERSON:Name|Description for person name lookups (description is REQUIRED).\n"
            "If the user asks for system info (IP, battery, etc.), use TERMINAL:command|description."
        )
    system_prompt = base_system_prompt.replace("{tool_instruction}", _tool_instruction)

    user_prompt = f"Query: {query}\n\n{search_context}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    web_search_tool = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information about people, places, companies, or facts.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query"}},
                "required": ["query"]
            }
        }
    }

    run_terminal_tool = {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Execute a safe local bash/zsh command to get system info (IP, battery, RAM, uptime). Use this when the user asks for local hardware/network info.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The unix command to run (e.g. 'curl ifconfig.me', 'pmset -g batt', 'ipconfig getifaddr en0')"}},
                "required": ["command"]
            }
        }
    }

    get_context_tool = {
        "type": "function",
        "function": {
            "name": "get_context",
            "description": "Get the user's current work context — what apps and files they're using, recent activity, and work sessions. Use when the user asks what they are working on, their recent activity, what they were doing, their work sessions, or wants to resume previous work.",
            "parameters": {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["current", "sessions", "resume"], "description": "What to retrieve: 'current' for active apps/files/activity, 'sessions' for work session history, 'resume' to resume last session"}},
                "required": ["mode"]
            }
        }
    }

    def _safe_fast_completion(messages, max_tokens, temperature, step_name, reset_model=False, tools=None, tool_choice=None):
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
                messages=messages, max_tokens=max_tokens, temperature=temperature,
                request_id=request_id, tools=tools, tool_choice=tool_choice
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
        llm1_ms = 0.0
        serper_ms = 0.0

        # Build tool list based on heuristic — only offer web_search when likely needed
        _action_tools = [run_terminal_tool, get_context_tool]
        if offer_web_search:
            _action_tools.insert(0, web_search_tool)

        out = _safe_fast_completion(
            messages=messages, max_tokens=256, temperature=0.0,
            step_name="Action intent", reset_model=True, tools=_action_tools
        )
        if out is None:
            return _action_resp([])

        # Handle Tool Calls — resolve inline instead of returning pending
        if out['choices'][0]['message'].get('tool_calls'):
            llm1_ms = (time.time() - start_t) * 1000
            logging.info(f"[TIMING] Phase 1 decided to use tools at: {time.time() - endpoint_start_time:.3f}s")
            tool_calls = out['choices'][0]['message']['tool_calls']
            q_tool = query
            use_terminal = False
            use_context = False
            context_mode = "current"
            term_cmd = ""
            try:
                for tc in tool_calls:
                    if tc.get('function', {}).get('name') == 'run_terminal':
                        args = json.loads(tc['function'].get('arguments', '{}') or '{}')
                        term_cmd = args.get('command')
                        use_terminal = True
                        break
                    elif tc.get('function', {}).get('name') == 'get_context':
                        args = json.loads(tc['function'].get('arguments', '{}') or '{}')
                        context_mode = args.get('mode', 'current')
                        use_context = True
                        break
                    elif tc.get('function', {}).get('name') == 'web_search':
                        args = json.loads(tc['function'].get('arguments', '{}') or '{}')
                        q_tool = (args.get('query') or query).strip()
                        break
            except Exception:
                q_tool = query

            # Check if request is still active before expensive operations
            if model_manager.current_fast_request_id != request_id:
                return _action_resp([])

            # If the tool call was run_terminal, bypass search entirely and reply with system info
            if use_terminal and term_cmd:
                logging.info(f"[TERMINAL TOOL] Executing: {term_cmd}")
                start_cmd = time.time()
                import subprocess
                try:
                    proc = subprocess.run(term_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    out_text = (proc.stdout or "").strip()
                    if not out_text:
                        out_text = (proc.stderr or "").strip()
                except subprocess.TimeoutExpired:
                    out_text = "Error: command timed out."
                except Exception as e:
                    out_text = f"Error: {e}"
                elapsed = time.time() - start_cmd
                logging.info(f"[TERMINAL TOOL] Done in {elapsed*1000:.0f}ms, output: {out_text[:200]!r}")
                
                if out_text:
                    if '\n' in out_text:
                        ans_text = f"```\n{out_text}\n```"
                    else:
                        ans_text = f"**{out_text}**"
                else:
                    ans_text = "Command returned no output."
                
                _act = {"type": "answer", "text": ans_text}
                
                if stream:
                    def _stream_term():
                        yield f'data: {json.dumps({"event": "done", "actions": [_act], "action": _act, "chips": []})}\n\n'
                    return Response(_stream_term(), content_type='text/event-stream')
                else:
                    return _action_resp([_act])

            # If the tool call was get_context, query the Context Engine
            if use_context:
                logging.info(f"[CONTEXT TOOL] Mode: {context_mode}")
                try:
                    from src.services.context.knowledge_graph import get_knowledge_graph
                    kg = get_knowledge_graph()

                    parts = []
                    if context_mode == "resume":
                        sessions = kg.get_recent_sessions(limit=1)
                        if sessions:
                            s = sessions[0]
                            if s.get('resume_state'):
                                from src.services.context.session_manager import get_session_manager
                                sm = get_session_manager()
                                sm.resume_session(s)
                                parts.append(f"Resuming session: {s.get('summary', 'previous work')}")
                            else:
                                parts.append(f"Last session: {s.get('summary', 'No details available')}. No files to reopen.")
                        else:
                            parts.append("No recent work sessions found to resume.")
                    elif context_mode == "sessions":
                        sessions = kg.get_recent_sessions(limit=3)
                        if sessions:
                            for i, s in enumerate(sessions, 1):
                                parts.append(f"**Session {i}**: {s.get('summary', 'No summary')}")
                        else:
                            parts.append("No work sessions recorded yet.")
                    else:
                        recent = kg.get_recent_activity(limit=20)
                        sessions = kg.get_recent_sessions(limit=3)
                        parts = _build_context_parts(recent, sessions)

                    if not parts:
                        parts.append("I've just started tracking your activity — give me a few more minutes to learn what you're working on.")

                    _act = {"type": "answer", "text": "\n".join(parts)}
                except Exception as e:
                    logging.error(f"[CONTEXT TOOL] Failed: {e}")
                    _act = {"type": "answer", "text": "Context tracking is starting up — please try again in a moment."}

                if stream:
                    def _stream_ctx():
                        yield f'data: {json.dumps({"event": "done", "actions": [_act], "action": _act, "chips": []})}\n\n'
                    return Response(_stream_ctx(), content_type='text/event-stream')
                else:
                    return _action_resp([_act])

            # For SSE mode, wrap the rest of inline resolution in a generator
            if stream:
                def _stream_inline_resolution():
                    try:
                        # Yield "searching" event immediately so UI shows skeleton
                        yield f'data: {json.dumps({"event": "searching", "query": q_tool})}\n\n'

                        # Execute Serper
                        _serper_t0 = time.time()
                        _tool_results = search_api(q_tool, categories='general', fast=True)
                        if not _tool_results and q_tool != query:
                            _tool_results = search_api(query, categories='general', fast=True)
                        _serper_ms = (time.time() - _serper_t0) * 1000

                        if model_manager.current_fast_request_id != request_id:
                            yield f'data: {json.dumps({"event": "done", "actions": [], "chips": []})}\n\n'
                            return

                        _search_results_local = list(_tool_results) if _tool_results else []

                        # If web search returned 0 results, ask the fast model to answer from its own knowledge
                        if not _tool_results:
                            logging.info(f"[SSE] Web search returned 0 results for '{q_tool}', asking fast model to answer directly")
                            _answer_out = _safe_fast_completion(
                                messages=[
                                    {"role": "system", "content": (
                                        "You are a helpful assistant. Answer the user's question concisely in 1-3 sentences. YOU MUST answer in the same language as the user's query. "
                                        "Output ONLY the answer text, no prefixes, no commands, no markdown."
                                    )},
                                    {"role": "user", "content": query},
                                ],
                                max_tokens=200, temperature=0.3, step_name="Direct answer (no search results)"
                            )
                            if _answer_out:
                                _answer_text = _answer_out['choices'][0]['message']['content'].strip()
                                _answer_text = re.sub(r'<think>.*?(?:</think>|$)', '', _answer_text, flags=re.DOTALL).strip()
                                if _answer_text and len(_answer_text) > 5:
                                    _qw = query.strip().lower().split()
                                    _qwords = {"who", "what", "when", "where", "why", "how", "which", "does", "did", "is", "are", "was", "were", "can", "could", "would"}
                                    _is_name_q = (1 <= len(_qw) <= 4 and not any(w in _qwords for w in _qw) and all(w.isalpha() or "'" in w or "-" in w for w in _qw))
                                    _place_sigs = ['city', 'capital', 'country', 'town', 'village', 'located', 'population', 'municipality', 'situated', 'province', 'region', 'district']
                                    _person_sigs = ['born', 'founder', 'co-founder', 'ceo', 'actor', 'musician', 'politician', 'president', 'director', 'scientist', 'author', 'artist', '(19', '(18', '(20']
                                    _ans_lower = _answer_text.lower()
                                    if _is_name_q and any(sig in _ans_lower for sig in _place_sigs):
                                        _act = {"type": "place", "name": query.title(), "address": _answer_text[:120].rsplit(' ', 1)[0] if len(_answer_text) > 120 else _answer_text, "latitude": None, "longitude": None, "url": "", "image": None}
                                    elif _is_name_q and any(sig in _ans_lower for sig in _person_sigs):
                                        _act = {"type": "person", "name": query.title(), "description": _answer_text, "url": "", "image": None}
                                    else:
                                        _act = {"type": "answer", "text": _answer_text}
                                    yield f'data: {json.dumps({"event": "done", "actions": [_act], "action": _act, "chips": []})}\n\n'
                                    return

                        _tool_content = "No results found."
                        if _tool_results:
                            _tool_content = ""
                            for _i, _res in enumerate(_tool_results[:3], 1):
                                _tool_content += f"Result {_i}: {_res.get('title')} - {_res.get('content') or _res.get('snippet')}\n"
                        _search_context_local = f"Tool Search Results for '{q_tool}':\n{_tool_content}".strip()

                        # Try heuristic first
                        _heuristic = _heuristic_classify_search_results(query, _tool_results)
                        if _heuristic:
                            yield f'data: {json.dumps({"event": "done", "actions": [_heuristic], "action": _heuristic, "chips": []})}\n\n'
                            return

                        # Phase 2 LLM
                        _phase2_system = (
                            "You are an intelligent action classifier. You ONLY output commands.\n"
                            "Use the web search results below to decide the best action(s).\n"
                            "Do NOT call any tools.\nNEVER return empty output.\n"
                            "If uncertain, output exactly one fallback command: SEARCH:{query}.\n"
                            "Every output line must start with a valid prefix.\n"
                            "Never output PERSON without a description after the '|' separator.\n"
                            "If the result is a physical location, ALWAYS output a PLACE: command.\n"
                            "If it is a person, output PERSON:FullName|Description — description is MANDATORY.\n"
                            "If the query is a question (who, what, when, where, why, how, is, are, was, were, do, does, did, can, could, will, would, etc.) "
                            "and the search results contain a clear answer, output ANSWER:text with a concise 1-3 sentence answer.\n\n"
                            "Commands: PLACE:Name, PERSON:Name|Desc, OPEN:url, SEARCH:query, INSTALL/UNINSTALL:name, "
                            "ANSWER:text (for questions that can be answered from search results)\n"
                            "Do not explain."
                        )
                        _p2_out = _safe_fast_completion(
                            messages=[{"role": "system", "content": _phase2_system},
                                      {"role": "user", "content": f"Query: {query}\n\n{_search_context_local}"}],
                            max_tokens=350, temperature=0.0, step_name="Action intent (stream phase2)"
                        )
                        if _p2_out is None:
                            _rt = f"SEARCH:{query}"
                        else:
                            _rt = _p2_out['choices'][0]['message']['content'].strip()
                            _ct = re.sub(r'<think>.*?(?:</think>|$)', '', _rt, flags=re.DOTALL).strip()
                            if not _ct and _rt:
                                if any(cmd in _rt for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "ANSWER:"]):
                                    _ct = _rt.replace("<think>", "").replace("</think>", "")
                            _rt = _ct

                        _actions, _chips = _parse_fast_action_output(
                            result_text=_rt, query=query, request_id=request_id,
                            endpoint_start_time=endpoint_start_time, search_context=_search_context_local,
                            search_results=_search_results_local, safe_fast_completion=_safe_fast_completion,
                        )
                        yield f'data: {json.dumps({"event": "done", "actions": _actions, "action": _actions[0] if _actions else None, "chips": _chips})}\n\n'
                    except Exception as _sse_err:
                        logging.error(f"SSE generator error for '{query}': {_sse_err}")
                        yield f'data: {json.dumps({"event": "done", "actions": [], "chips": []})}\n\n'

                return Response(_stream_inline_resolution(), mimetype="text/event-stream")

            # Execute Serper inline (previously done in /action_pending)
            serper_t0 = time.time()
            tool_results = search_api(q_tool, categories='general', fast=True)
            logging.warning(f"[ACTION/INLINE] search_api({q_tool!r}, fast=True) → {len(tool_results)} results")

            # Retry with original query if tool query differs and got no results
            if not tool_results and q_tool != query:
                logging.info(f"[ACTION/INLINE] Retrying search with original query: {query!r}")
                tool_results = search_api(query, categories='general', fast=True)

            serper_ms = (time.time() - serper_t0) * 1000

            # Check again after Serper (user may have typed something new)
            if model_manager.current_fast_request_id != request_id:
                return _action_resp([])

            if tool_results:
                search_results.extend(tool_results)

            # If web search returned 0 results, ask the fast model to answer from its own knowledge
            if not tool_results:
                logging.info(f"[INLINE] Web search returned 0 results for '{q_tool}', asking fast model to answer directly")
                _answer_out = _safe_fast_completion(
                    messages=[
                        {"role": "system", "content": (
                            "You are a helpful assistant. Answer the user's question concisely in 1-3 sentences. YOU MUST answer in the same language as the user's query. "
                            "Output ONLY the answer text, no prefixes, no commands, no markdown."
                        )},
                        {"role": "user", "content": query},
                    ],
                    max_tokens=200, temperature=0.3, step_name="Direct answer (no search results)"
                )
                if _answer_out:
                    _answer_text = _answer_out['choices'][0]['message']['content'].strip()
                    _answer_text = re.sub(r'<think>.*?(?:</think>|$)', '', _answer_text, flags=re.DOTALL).strip()
                    if _answer_text and len(_answer_text) > 5:
                        total_ms = (time.time() - endpoint_start_time) * 1000
                        logging.info(f"[TIMING] LLM1={llm1_ms:.0f}ms Serper={serper_ms:.0f}ms direct_answer=True total={total_ms:.0f}ms")
                        _qw = query.strip().lower().split()
                        _qwords = {"who", "what", "when", "where", "why", "how", "which", "does", "did", "is", "are", "was", "were", "can", "could", "would"}
                        _is_name_q = (1 <= len(_qw) <= 4 and not any(w in _qwords for w in _qw) and all(w.isalpha() or "'" in w or "-" in w for w in _qw))
                        _ans_lower = _answer_text.lower()
                        _place_sigs = ['city', 'capital', 'country', 'town', 'village', 'located', 'population', 'municipality', 'situated', 'province', 'region', 'district']
                        _person_sigs = ['born', 'founder', 'co-founder', 'ceo', 'actor', 'musician', 'politician', 'director', 'scientist', 'author', 'artist']
                        if _is_name_q and any(sig in _ans_lower for sig in _place_sigs):
                            return _action_resp([{"type": "place", "name": query.title(), "address": _answer_text[:120].rsplit(' ', 1)[0] if len(_answer_text) > 120 else _answer_text, "latitude": None, "longitude": None, "url": "", "image": None}])
                        elif _is_name_q and any(sig in _ans_lower for sig in _person_sigs):
                            return _action_resp([{"type": "person", "name": query.title(), "description": _answer_text, "url": "", "image": None}])
                        return _action_resp([{"type": "answer", "text": _answer_text}])

            tool_content = "No results found."
            if tool_results:
                tool_content = ""
                for i, res in enumerate(tool_results[:3], 1):
                    tool_content += f"Result {i}: {res.get('title')} - {res.get('content') or res.get('snippet')}\n"

            search_context = f"Tool Search Results for '{q_tool}':\n{tool_content}".strip()

            # Fast heuristic classification (skip Phase 2 LLM when possible)
            heuristic_t0 = time.time()
            heuristic_result = _heuristic_classify_search_results(query, tool_results)
            heuristic_hit = heuristic_result is not None
            if heuristic_result:
                total_ms = (time.time() - endpoint_start_time) * 1000
                logging.info(f"[TIMING] LLM1={llm1_ms:.0f}ms Serper={serper_ms:.0f}ms heuristic_hit=True LLM2=0ms total={total_ms:.0f}ms")
                return _action_resp([heuristic_result])

            # Fallback: Phase 2 LLM classification (only when heuristic is uncertain)
            llm2_t0 = time.time()
            phase2_system = (
                "You are an intelligent action classifier. You ONLY output commands.\n"
                "Use the web search results below to decide the best action(s).\n"
                "Do NOT call any tools.\n"
                "NEVER return empty output.\n"
                "If uncertain, output exactly one fallback command: SEARCH:{query}.\n"
                "Every output line must start with a valid prefix.\n"
                "Never output PERSON without a description after the '|' separator.\n"
                "Never output trailing '|' without text after it.\n"
                "If the result is a physical location (school, restaurant, monument, city), ALWAYS output a PLACE: command.\n"
                "If it also has an official website, output OPEN: as well.\n"
                "If it is a person, output PERSON:FullName|Description — the description is MANDATORY.\n"
                "If the query is a question (who, what, when, where, why, how, is, are, was, were, do, does, did, can, could, will, would, etc.) "
                "and the search results contain a clear answer, output ANSWER:text with a concise 1-3 sentence answer based on the search results.\n\n"
                "Output one or more commands, one per line:\n"
                "- ANSWER:text (for questions that can be answered from search results — concise 1-3 sentences. YOU MUST answer in the same language as the user's query)\n"
                "- PLACE:Name (for ANY physical location/institution)\n"
                "- PERSON:Name|Description (Name MUST be the full person name, without suffixes like '| LinkedIn', '- Omni', '@handle'. Description MUST be 1-2 sentences synthesized from the search results: role + organization/school/company/location. NEVER omit the description — if you truly cannot write one, use SEARCH:query instead.)\n"
                "- OPEN:url (for websites)\n"
                "- INSTALL/UNINSTALL:name\n"
                "- SEARCH:query\n"
                "- CALC/TRANSLATE/CURRENCY/WEATHER/UNIT/COLOR/TIMER/PASSWORD/QRCODE\n"
                "- SYSTEM_SETTINGS:{...}\n"
                "- TERMINAL:command|description\n\n"
                "Examples:\n"
                "Query: 'who founded google'\n"
                "Search result: 'Google was founded on September 4, 1998, by Larry Page and Sergey Brin while they were PhD students at Stanford University.'\n"
                "ANSWER:Google was founded by Larry Page and Sergey Brin on September 4, 1998, while they were PhD students at Stanford University.\n\n"
                "Search result: 'ZSTiB Brzesko school website zstib.edu.pl'\n"
                "PLACE:ZSTiB Brzesko\n"
                "OPEN:https://zstib.edu.pl\n\n"
                "Search result: 'Mikołaj Piech – Omni'\n"
                "PERSON:Mikołaj Piech|He is a Polish app developer associated with Omni and focused on AI-powered software.\n\n"
                "Search result: 'Anna Kowalska – wicedyrektor. Szkoła Podstawowa nr 5, Kraków'\n"
                "PERSON:Anna Kowalska|She is a vice-principal at Szkoła Podstawowa nr 5 in Kraków, Poland.\n\n"
                "Do not explain."
            )
            phase2_user = f"Query: {query}\n\n{search_context}"
            phase2_out = _safe_fast_completion(
                messages=[{"role": "system", "content": phase2_system}, {"role": "user", "content": phase2_user}],
                max_tokens=350,
                temperature=0.0,
                step_name="Action intent (inline phase2)"
            )
            llm2_ms = (time.time() - llm2_t0) * 1000

            if phase2_out is None:
                # LLM2 failed/aborted — parse as SEARCH fallback
                result_text = f"SEARCH:{query}"
            else:
                result_text = phase2_out['choices'][0]['message']['content'].strip()
                cleaned_text = re.sub(r'<think>.*?(?:</think>|$)', '', result_text, flags=re.DOTALL).strip()
                if not cleaned_text and result_text:
                    if any(cmd in result_text for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "CALC:", "TRANSLATE:", "ANSWER:"]):
                        cleaned_text = result_text.replace("<think>", "").replace("</think>", "")
                result_text = cleaned_text

            actions, chips = _parse_fast_action_output(
                result_text=result_text,
                query=query,
                request_id=request_id,
                endpoint_start_time=endpoint_start_time,
                search_context=search_context,
                search_results=search_results,
                safe_fast_completion=_safe_fast_completion,
            )
            total_ms = (time.time() - endpoint_start_time) * 1000
            logging.info(f"[TIMING] LLM1={llm1_ms:.0f}ms Serper={serper_ms:.0f}ms heuristic_hit={heuristic_hit} LLM2={llm2_ms:.0f}ms total={total_ms:.0f}ms")
            return _action_resp(actions, chips)

        if model_manager.current_fast_request_id != request_id:
            logging.info(f"Request {request_id} was cancelled during inference")
            return _action_resp([])

        end_t = time.time()
        dur = end_t - start_t
        tok_count = out.get('usage', {}).get('completion_tokens', 0)
        tps = tok_count / dur if dur > 0 else 0
        logging.info(f"FastModel (Action): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
        logging.info(f"[TIMING] Fast Model total inference time: {time.time() - endpoint_start_time:.3f}s")
        result_text = out['choices'][0]['message']['content'].strip()
        logging.info(f"Raw Fast Model Output: {result_text!r}")

        cleaned_text = re.sub(r'<think>.*?(?:</think>|$)', '', result_text, flags=re.DOTALL).strip()
        if not cleaned_text and result_text:
            logging.warning("Regex stripped everything. Checking raw text for commands...")
            if any(cmd in result_text for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "CALC:", "TRANSLATE:", "ANSWER:"]):
                cleaned_text = result_text.replace("<think>", "").replace("</think>", "")
        result_text = cleaned_text

        actions, chips = _parse_fast_action_output(
            result_text=result_text, query=query, request_id=request_id,
            endpoint_start_time=endpoint_start_time, search_context=search_context,
            search_results=search_results, safe_fast_completion=_safe_fast_completion,
        )
        logging.info(f"[TIMING] Total action_endpoint time: {time.time() - endpoint_start_time:.3f}s")
        return _action_resp(actions, chips)

    except Exception as e:
        logging.error(f"Error in action_endpoint: {e}")
        return _action_resp([], error=str(e))


@api_bp.route('/action_pending', methods=['POST'])
def action_pending_endpoint():
    """
    Continue a pending fast action that required web_search.
    Expects: {"pending_id": "<request_id>"}
    """
    model_manager.ensure_fast_model()
    try:
        req = request.get_json(force=True)
    except Exception:
        return jsonify({"actions": [], "chips": []}), 400

    pending_id = (req.get("pending_id") or "").strip()
    if not pending_id:
        return jsonify({"actions": [], "chips": []}), 400

    pending = _pending_actions_get(pending_id)
    if not pending:
        return jsonify({"actions": [], "chips": []})

    # If user already typed something else, don't waste cycles.
    if model_manager.current_fast_request_id != pending_id:
        _pending_actions_pop(pending_id)
        return jsonify({"actions": [], "chips": []})

    query = pending.get("query", "")
    tool_q = pending.get("tool_query", query) or query
    endpoint_start_time = time.time()
    search_context = ""
    search_results = []

    def _safe_fast_completion(messages, max_tokens, temperature, step_name, reset_model=False, tools=None, tool_choice=None):
        if model_manager.current_fast_request_id != pending_id:
            logging.info(f"{step_name}: request {pending_id} superseded before lock.")
            return None

        if not model_manager.fast_lock.acquire(timeout=5.0):
            logging.error(f"{step_name}: failed to acquire fast_lock after 5 seconds.")
            return None

        try:
            if model_manager.current_fast_request_id != pending_id:
                logging.info(f"{step_name}: request {pending_id} cancelled before inference.")
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
                request_id=pending_id,
                tools=tools,
                tool_choice=tool_choice
            )
            if out is None:
                logging.info(f"{step_name}: completion aborted/empty for request {pending_id}.")
                return None
            return out
        except Exception as e:
            logging.error(f"{step_name}: fast model inference failed: {e}")
            return None
        finally:
            model_manager.fast_lock.release()

    try:
        from src.services.search.web_search import search_api
        tool_results = search_api(tool_q, categories='general', fast=True)
        logging.warning(f"[ACTION/PENDING] search_api({tool_q!r}, fast=True) → {len(tool_results)} results")

        # If no results, retry with the original user query (may differ from tool_q)
        if not tool_results and tool_q != query:
            logging.info(f"[ACTION/PENDING] Retrying search with original query: {query!r}")
            tool_results = search_api(query, categories='general', fast=True)
            logging.warning(f"[ACTION/PENDING] search_api({query!r}, fast=True) → {len(tool_results)} results")

        # If still no results and query has multiple words, try individual words
        if not tool_results:
            words = query.strip().split()
            if len(words) >= 2:
                for word in words:
                    if len(word) >= 3:
                        logging.info(f"[ACTION/PENDING] Retrying with partial query: {word!r}")
                        tool_results = search_api(word, categories='general', fast=True)
                        if tool_results:
                            logging.warning(f"[ACTION/PENDING] search_api({word!r}, fast=True) → {len(tool_results)} results")
                            break

        # Validate that search results are actually relevant to the query.
        # If none of the top results mention the query, they're likely stale/wrong.
        if tool_results and tool_q:
            _q_lower = tool_q.lower().strip()
            _any_relevant = any(
                _q_lower in (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', ''))).lower()
                for r in tool_results[:3]
            )
            if not _any_relevant and len(_q_lower) >= 3:
                logging.warning(f"[ACTION/PENDING] Search results don't mention '{tool_q}' — results may be stale. Retrying...")
                retry_results = search_api(tool_q, categories='general', fast=False)
                if retry_results:
                    _any_relevant2 = any(
                        _q_lower in (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', ''))).lower()
                        for r in retry_results[:3]
                    )
                    if _any_relevant2:
                        tool_results = retry_results
                        logging.warning(f"[ACTION/PENDING] Retry got {len(retry_results)} relevant results")

        if tool_results:
            search_results.extend(tool_results)

        tool_content = "No results found."
        if tool_results:
            tool_content = ""
            for i, res in enumerate(tool_results[:3], 1):
                tool_content += f"Result {i}: {res.get('title')} - {res.get('content') or res.get('snippet')}\n"

        search_context = f"Tool Search Results for '{tool_q}':\n{tool_content}".strip()
        logging.warning(f"[ACTION/PENDING] search_context len={len(search_context)}: {search_context[:120]!r}")

        # Fast heuristic: if search results strongly suggest a place, skip LLM entirely.
        # This saves 2 LLM calls (phase 2 + classification) and ~4s of latency.
        if tool_results and len(query.split()) <= 3:
            _combined = ' '.join(
                (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', '')))
                for r in tool_results[:3]
            ).lower()
            _place_signals = ['capital', 'stolica', 'miasto', 'city', 'town', 'country',
                              'province', 'województw', 'located in', 'population',
                              'gmina', 'powiat', 'region', 'district', 'county',
                              'municipality', 'village', 'commune', 'landmark',
                              'monument', 'continent', 'island', 'river']
            _place_score = sum(1 for kw in _place_signals if kw in _combined)
            if _place_score >= 2:
                logging.info(f"[ACTION/PENDING] Heuristic: place detected (score={_place_score}) for '{query}', skipping LLM")
                place_res = get_place_result(query, existing_results=tool_results)
                if place_res:
                    place_act = place_res
                else:
                    import urllib.parse
                    url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
                    place_act = {"type": "link", "url": url, "title": f"Search {query}", "description": "Web Search"}
                
                _pending_actions_pop(pending_id)
                return jsonify({"actions": [place_act], "action": place_act, "chips": []})

        phase2_system = (
            "You are an intelligent action classifier. You ONLY output commands.\n"
            "Use the web search results below to decide the best action(s).\n"
            "Do NOT call any tools.\n"
            "NEVER return empty output.\n"
            "If uncertain, output exactly one fallback command: SEARCH:{query}.\n"
            "Every output line must start with a valid prefix.\n"
            "Never output PERSON without a description after the '|' separator.\n"
            "Never output trailing '|' without text after it.\n"
            "If the result is a physical location (school, restaurant, monument, city), ALWAYS output a PLACE: command.\n"
            "If it also has an official website, output OPEN: as well.\n"
            "If it is a person, output PERSON:FullName|Description — the description is MANDATORY.\n\n"
            "Output one or more commands, one per line:\n"
            "- PLACE:Name (for ANY physical location/institution)\n"
            "- PERSON:Name|Description (Name MUST be the full person name, without suffixes like '| LinkedIn', '- Omni', '@handle'. Description MUST be 1-2 sentences synthesized from the search results: role + organization/school/company/location. NEVER omit the description — if you truly cannot write one, use SEARCH:query instead.)\n"
            "- OPEN:url (for websites)\n"
            "- INSTALL/UNINSTALL:name\n"
            "- SEARCH:query\n"
            "- CALC/TRANSLATE/CURRENCY/WEATHER/UNIT/COLOR/TIMER/PASSWORD/QRCODE\n"
            "- SYSTEM_SETTINGS:{...}\n\n"
            "Examples:\n"
            "Search result: 'ZSTiB Brzesko school website zstib.edu.pl'\n"
            "PLACE:ZSTiB Brzesko\n"
            "OPEN:https://zstib.edu.pl\n\n"
            "Search result: 'Mikołaj Piech – Omni'\n"
            "PERSON:Mikołaj Piech|He is a Polish app developer associated with Omni and focused on AI-powered software.\n\n"
            "Search result: 'Anna Kowalska – wicedyrektor. Szkoła Podstawowa nr 5, Kraków'\n"
            "PERSON:Anna Kowalska|She is a vice-principal at Szkoła Podstawowa nr 5 in Kraków, Poland.\n\n"
            "Do not explain."
        )
        phase2_user = f"Query: {query}\n\n{search_context}"
        out = _safe_fast_completion(
            messages=[{"role": "system", "content": phase2_system}, {"role": "user", "content": phase2_user}],
            max_tokens=350,
            temperature=0.0,
            step_name="Action intent (pending)"
        )
        if out is None:
            actions, chips = _parse_fast_action_output(
                result_text=f"SEARCH:{query}",
                query=query,
                request_id=pending_id,
                endpoint_start_time=endpoint_start_time,
                search_context=search_context,
                search_results=search_results,
                safe_fast_completion=_safe_fast_completion,
            )
            _pending_actions_pop(pending_id)
            return jsonify({"actions": actions, "action": actions[0] if actions else None, "chips": chips})

        result_text = out['choices'][0]['message']['content'].strip()
        cleaned_text = re.sub(r'<think>.*?(?:</think>|$)', '', result_text, flags=re.DOTALL).strip()
        if not cleaned_text and result_text:
            if any(cmd in result_text for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "CALC:", "TRANSLATE:", "ANSWER:"]):
                cleaned_text = result_text.replace("<think>", "").replace("</think>", "")
        result_text = cleaned_text

        actions, chips = _parse_fast_action_output(
            result_text=result_text,
            query=query,
            request_id=pending_id,
            endpoint_start_time=endpoint_start_time,
            search_context=search_context,
            search_results=search_results,
            safe_fast_completion=_safe_fast_completion,
        )
        _pending_actions_pop(pending_id)
        return jsonify({"actions": actions, "action": actions[0] if actions else None, "chips": chips})

    except Exception as e:
        logging.error(f"Error in action_pending_endpoint: {e}")
        _pending_actions_pop(pending_id)
        return jsonify({"actions": [], "chips": [], "error": str(e)})

@api_bp.route('/resolve_place', methods=['POST'])
def resolve_place_endpoint():
    try: req = request.get_json(force=True)
    except: return jsonify({"error": "Bad JSON"}), 400
    
    name = req.get('name', '').strip()
    if not name: return jsonify({})
    
    # We don't have the previous search context, but get_place_result handles that by doing a fresh map search
    res = get_place_result(name, existing_results=None)
    return jsonify(res if res else {})

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
        "You are a smart filter for a desktop file search index.\n"
        "Your task: Decide which files are worth embedding for semantic search.\n"
        "The goal is to index ONLY human-written content that a user would search for by meaning.\n"
        "We want to SKIP all machine-generated files, binaries, logs, lockfiles, and boilerplate.\n\n"
        "RULES - Output 0 (SKIP) for:\n"
        "1. BINARIES & MODELS: .exe, .dll, .so, .dylib, .bin, .pkl, .pth, .tflite, .onnx, .wasm, .hprof, .blend, .jks, .keystore, .srcaar\n"
        "2. MEDIA: Images (.png, .jpg), Audio (.mp3, .wav), Video (.mp4), Fonts (.ttf, .otf, .icc) — indexed separately.\n"
        "3. LOCKFILES: package-lock.json, yarn.lock, Podfile.lock, pubspec.lock, Cargo.lock, poetry.lock\n"
        "4. LOGS & DUMPS: .log, .out, .err, .dump, .stacktrace, .tsbuildinfo, sha_debug.txt, errors.txt\n"
        "5. CONFIG METADATA: .pbxproj, .xcworkspacedata, .plist, .storyboard, .xib, .entitlements, analysis_options.yaml, .idea/, .vscode/\n"
        "6. BUILD ARTIFACTS: gradlew, google-services.json, GeneratedPluginRegistrant.*, build/, dist/, target/, out/\n"
        "7. COMPILED/MINIFIED: .min.js, .min.css, .map, .d.ts (unless it has docs), .class, .pyc\n"
        "8. BOILERPLATE: .eslintrc, .prettierrc, tsconfig.json, .gitignore, manifest.json, license.txt, .csproj, .sln\n"
        "9. UNITY/GAME ASSETS: .meta, .prefab, .unity, .mat, .asset, .inputactions, .asmdef\n"
        "10. APP BUNDLES: Files inside .app, .framework, .asar, Contents/Info.plist\n"
        "11. LIBRARY VENDOR DATA: PHP files in font dirs (ttfontdata/, patterns/, font/), Firebase *Dependencies.xml, *_manifest.txt, maven-metadata.xml\n"
        "12. PLATFORM RUNNER BOILERPLATE: flutter_window.cpp/h, win32_window.cpp/h, resource.h, Runner.rc, my_application.cc/h (Flutter platform glue)\n\n"
        "RULES - Output 1 (INDEX) for:\n"
        "1. SOURCE CODE: .py, .js, .ts, .rs, .go, .c, .cpp, .java, .swift, .kt, .cs (containing LOGIC, not platform boilerplate)\n"
        "2. DOCUMENTS: .md, .txt (notes), .docx, .pdf, .rtf\n"
        "3. CONFIG WITH LOGIC: specific config files that contain custom logic (not just key-value pairs)\n\n"
        "EXAMPLES:\n"
        "File: src/main.py -> 1 (Source code)\n"
        "File: README.md -> 1 (Documentation)\n"
        "File: package-lock.json -> 0 (Lockfile)\n"
        "File: dist/bundle.js -> 0 (Build artifact)\n"
        "File: assets/logo.png -> 0 (Image)\n"
        "File: notes/ideas.txt -> 1 (User notes)\n"
        "File: logs/error.log -> 0 (Log file)\n"
        "File: ttfontdata/dejavusans.cw127.php (in: mpdf57/ttfontdata/) -> 0 (Font metric data)\n"
        "File: AuthDependencies.xml (in: Firebase/Editor/) -> 0 (Library build metadata)\n"
        "File: Runner.rc (in: windows/runner/) -> 0 (Platform boilerplate)\n"
        "File: GameManager.cs (in: Assets/Scripts/) -> 1 (Game source code)\n\n"
        "Reply with ONLY a JSON array of 0s and 1s, one per file, in order.\n"
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
        if not raw:
            return jsonify({"decisions": [1] * len(files)})
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
        with model_manager.embed_lock:
            vectors = model_manager.embed_model.encode(texts).tolist()
        return jsonify({"vectors": vectors})
    except Exception as e:
        logging.error(f"Embed endpoint error: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Context Engine endpoints
# ---------------------------------------------------------------------------

@api_bp.route('/context', methods=['GET'])
def context_endpoint():
    """Return current context: recent entities, active entities, stats."""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        from src.services.context.activity_observer import get_observer
        kg = get_knowledge_graph()
        obs = get_observer()

        recent = kg.get_recent_entities(limit=20)
        active_ids = kg.get_active_entity_ids(window_seconds=300)
        active_entities = [kg.get_entity(eid) for eid in active_ids if eid]
        active_entities = [e for e in active_entities if e]
        stats = kg.get_stats()

        return jsonify({
            "recent_entities": recent,
            "active_entities": active_entities,
            "stats": stats,
            "observer_paused": obs.is_paused,
        })
    except Exception as e:
        logging.error(f"/context error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/entities', methods=['POST'])
def context_entities_endpoint():
    """Search or list entities.  Body: {"query": str, "type": str, "limit": int}"""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        data = request.get_json(silent=True) or {}
        query = data.get("query", "")
        entity_type = data.get("type")
        limit = data.get("limit", 20)

        if query:
            results = kg.search_entities(query, entity_type=entity_type, limit=limit)
        else:
            results = kg.get_recent_entities(entity_type=entity_type, limit=limit)

        return jsonify({"entities": results})
    except Exception as e:
        logging.error(f"/context/entities error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/entity/<entity_id>', methods=['GET'])
def context_entity_detail(entity_id):
    """Return full context for a single entity (relationships, activity)."""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        ctx = kg.get_context_for_entity(entity_id)
        if not ctx:
            return jsonify({"error": "Entity not found"}), 404
        return jsonify(ctx)
    except Exception as e:
        logging.error(f"/context/entity error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/pause', methods=['POST'])
def context_pause_endpoint():
    """Pause or resume the activity observer.  Body: {"paused": bool}"""
    try:
        from src.services.context.activity_observer import get_observer
        obs = get_observer()
        data = request.get_json(silent=True) or {}
        should_pause = data.get("paused", True)

        if should_pause:
            obs.pause()
        else:
            obs.resume()

        return jsonify({"paused": obs.is_paused})
    except Exception as e:
        logging.error(f"/context/pause error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/suggestions', methods=['GET'])
def context_suggestions_endpoint():
    """List recent suggestions."""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        # Return recent suggestions (last 7 days)
        cutoff = time.time() - 7 * 86400
        with kg._lock:
            rows = kg._conn.execute(
                "SELECT id, type, content, created_at, shown_at, dismissed, acted_on "
                "FROM suggestions WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20",
                (cutoff,),
            ).fetchall()
        results = [
            {
                "id": r[0], "type": r[1], "content": json.loads(r[2]),
                "created_at": r[3], "shown_at": r[4],
                "dismissed": bool(r[5]), "acted_on": bool(r[6]),
            }
            for r in rows
        ]
        return jsonify({"suggestions": results})
    except Exception as e:
        logging.error(f"/context/suggestions error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/suggestion/dismiss', methods=['POST'])
def context_suggestion_dismiss():
    """Mark a suggestion as dismissed."""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        data = request.get_json(silent=True) or {}
        sid = data.get("suggestion_id", "")
        if sid:
            kg.mark_suggestion_dismissed(sid)
        return jsonify({"status": "ok"})
    except Exception as e:
        logging.error(f"/context/suggestion/dismiss error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/suggestion/accept', methods=['POST'])
def context_suggestion_accept():
    """Mark a suggestion as acted on."""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        data = request.get_json(silent=True) or {}
        sid = data.get("suggestion_id", "")
        if sid:
            kg.mark_suggestion_acted(sid)
        return jsonify({"status": "ok"})
    except Exception as e:
        logging.error(f"/context/suggestion/accept error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/sessions', methods=['GET'])
def context_sessions_endpoint():
    """List recent work sessions."""
    try:
        from src.services.context.session_manager import get_session_manager
        mgr = get_session_manager()
        limit = request.args.get("limit", 10, type=int)
        sessions = mgr.get_recent_sessions(limit=limit)
        return jsonify({"sessions": sessions})
    except Exception as e:
        logging.error(f"/context/sessions error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/sessions/<session_id>/resume', methods=['POST'])
def context_session_resume(session_id):
    """Resume a work session (reopen files/apps)."""
    try:
        from src.services.context.session_manager import get_session_manager, SessionManager
        mgr = get_session_manager()
        session = mgr.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        result = SessionManager.resume_session(session)
        return jsonify({"status": result})
    except Exception as e:
        logging.error(f"/context/sessions/resume error: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/context/clear', methods=['POST'])
def context_clear_endpoint():
    """Delete all context data (privacy wipe)."""
    try:
        from src.services.context.knowledge_graph import get_knowledge_graph
        kg = get_knowledge_graph()
        kg.clear_all()
        return jsonify({"status": "cleared"})
    except Exception as e:
        logging.error(f"/context/clear error: {e}")
        return jsonify({"error": str(e)}), 500
