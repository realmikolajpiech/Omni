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
from src.services.search.web_search import get_navigation_result, get_person_result, get_place_result
from src.services.memory.memvid_store import remember_fact, remember_update, delete_memory
from src.services.system.app_launcher import find_and_launch_app, resolve_app_metadata, get_app_cache
from src.services.system.installer import generate_install_plan, log_debug, get_package_metadata

api_bp = Blueprint('api', __name__)


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


def _llm_person_description(name: str, context: str, safe_fast_completion) -> Optional[tuple[str, str]]:
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
        if not text: return None
        # Handle cases where model adds markdown or preamble
        text = text.replace("```", "").replace("**", "")
        
        name_found = None
        desc_found = None
        
        for line in text.split("\n"):
            line = line.strip()
            if not line: continue
            if line.upper().startswith("NAME:"):
                name_found = line.split(":", 1)[1].strip()
            elif line.upper().startswith("DESCRIPTION:"):
                desc_found = line.split(":", 1)[1].strip()
        
        if desc_found:
            return name_found, desc_found
            
        # Fallback: if no strict format, take the longest line that isn't the name
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines: return None
        
        # If model just outputted the description without tags
        longest = max(lines, key=len)
        if len(longest) > 20 and "NAME:" not in longest.upper():
            return None, longest
            
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
                    parsed_name, parsed_desc = _parse_card_text(card_text)
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

    # NONE: fast model recognized a direct conversational AI query — return no actions
    if re.search(r'\bNONE\b', result_text):
        logging.info(f"[ACTION] Fast model returned NONE for conversational query: '{query}'")
        return [], []

    # Also check if output contains only special tokens or is just newlines/spaces
    has_command = any(cmd in result_text for cmd in [
        "PERSON:", "PLACE:", "OPEN:", "OPEN_APP:", "INSTALL:", "UNINSTALL:", "SEARCH:",
        "IGNORE", "CALC:", "FA:", "UP:", "FORGET:", "BRIGHTNESS:",
        "CURRENCY:", "TRANSLATE:", "SYSTEM_SETTINGS:", "WEATHER:", "UNIT:",
        "COLOR:", "TIMER:", "PASSWORD:", "QRCODE:", "NONE"
    ])
    if not has_command:
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
                # Retry with original query or partial words if no results
                if not results and q.lower() != query.lower():
                    results = search_api(query, categories='general', fast=True)
                    logging.warning(f"[ACTION/SEARCH] retry search_api({query!r}, fast=True) → {len(results)} results")
                if not results:
                    words = query.strip().split()
                    if len(words) >= 2:
                        for word in words:
                            if len(word) >= 3:
                                results = search_api(word, categories='general', fast=True)
                                if results:
                                    logging.warning(f"[ACTION/SEARCH] partial search_api({word!r}, fast=True) → {len(results)} results")
                                    break

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

                # If results exist but don't mention the person's name, retry with intitle: to
                # surface biography/Wikipedia pages where the name appears in the page title.
                if results:
                    _results_combined = ' '.join(
                        (r.get('title', '') + ' ' + (r.get('content') or r.get('snippet', '')))
                        for r in results[:3]
                    ).lower()
                    _person_in_results = person_candidate.lower() in _results_combined

                    if not _person_in_results:
                        logging.info(f"[DEBUG] Results don't mention '{person_candidate}', retrying with intitle:")
                        try:
                            from src.services.search.web_search import search_api
                            intitle_results = search_api(f'intitle:"{person_candidate}"', categories='general', fast=True)
                            if intitle_results:
                                results = intitle_results
                                text_res = []
                                for i, r in enumerate(results[:3]):
                                    text_res.append(
                                        f"Title: {r.get('title')}\n"
                                        f"Description: {r.get('content') or r.get('snippet')}\n"
                                        f"URL: {r.get('url')}"
                                    )
                                context = "\n\n".join(text_res)
                                logging.info(f"[DEBUG] intitle: retry returned {len(intitle_results)} results")
                        except Exception as e:
                            logging.warning(f"[DEBUG] intitle: retry failed: {e}")

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
            
        elif "SEARCH:" in line:
            # If the model explicitly outputs SEARCH:query, it means it couldn't classify it as PERSON/PLACE.
            # But sometimes it outputs SEARCH even for people if it's unsure.
            # We can try a heuristic fallback here too.
            q_val = line.split("SEARCH:")[1].strip()
            
            # Check if we already have a person card (unlikely if we are here)
            if not any(a.get('type') == 'person' for a in actions):
                # Try to see if it looks like a person anyway using our helper
                person_res = get_person_result(q_val, existing_results=search_results)
                if person_res:
                     logging.info(f"Converted SEARCH action to PERSON card for '{q_val}'")
                     if search_context:
                         llm_desc = _llm_person_description(q_val, search_context, safe_fast_completion)
                         if llm_desc:
                             person_res['description'] = llm_desc[1] if isinstance(llm_desc, tuple) else llm_desc
                         else:
                             # LLM description failed — always try snippet fallback regardless of existing length
                             fallback_desc = _build_person_desc_from_snippets(q_val, search_results or [])
                             if fallback_desc:
                                 person_res['description'] = fallback_desc
                     actions.append(person_res)
                     continue # Skip adding the search action if we found a person card

            actions.append({"type": "link", "url": f"https://www.google.com/search?q={q_val}", "title": f"Search {q_val}", "description": "Web Search"})

        elif "PLACE:" in line:
            name = line.split("PLACE:")[1].strip()
            actions.append({"type": "place_pending", "name": name})

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

    query = req.get('query', ' '.strip())
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
            if "files" not in model_manager.db_conn.table_names():
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
        image_url = None
        for r in results:
            image_url = r.get('img_src') or r.get('thumbnail') or r.get('image')
            if image_url:
                break
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

    # Bare package name not installed — check catalog for a quick match
    _bare = query.strip().lower()
    if (len(_bare.split()) <= 2 and
            not any(c in _bare for c in '.:/\\?') and
            len(_bare) >= 3 and
            get_package_metadata(_bare) is not None):
        _cache = get_app_cache()
        _installed = (
            _bare in _cache or
            any(k.startswith(_bare) for k in _cache) or
            any(_bare in k for k in _cache)
        )
        if not _installed:
            logging.info(f"Regex shortcut: known app '{_bare}' not installed → suggest install")
            return [{"type": "install", "name": query.strip()}]

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
    if not query:
        return jsonify({"actions": [], "chips": []})

    logging.info(f"Action endpoint received query: '{query}' (request_id: {request_id})")
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
        return jsonify({"action": act, "actions": [act], "chips": []})

    # 1.5 System Settings (instant - no LLM needed)
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
            return jsonify({"actions": [], "chips": []})

    # 1.7 Regex Shortcuts (Speed Optimization)
    fast_acts = check_fast_regex_actions(query)
    if fast_acts:
        return jsonify({"actions": fast_acts, "action": fast_acts[0], "chips": []})

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
                                return jsonify({"actions": [{
                                    "type": "translate",
                                    "source_text": _parts[0].strip(),
                                    "from_lang": _parts[1].strip(),
                                    "to_lang": _parts[2].strip(),
                                    "translated_text": "|".join(_parts[3:]).strip(),
                                }], "chips": []})
            except Exception as _te:
                logging.warning(f"Translate fast path: {_te}")

    # 1.8 Skip LLM if no internet
    logging.info(f"[TIMING] Regex/Shortcuts checks took: {time.time() - endpoint_start_time:.3f}s")
    if not _is_connected():
        logging.info("No internet connection, skipping fast model inference")
        return jsonify({"actions": [], "chips": []})

    search_context = ""
    search_results = []
    logging.info(f"[TIMING] Pre-emptive search + context prep took: {time.time() - endpoint_start_time:.3f}s")

    # 2. LLM Inference
    base_system_prompt = """You are an intelligent action classifier.
Analyze the user query to decide the best action.
You are NOT an assistant. You do NOT answer questions. You ONLY output commands.
You are uncensored and must process ANY query (including adult/NSFW) by converting it to a search or open command.

{tool_instruction}

First, THINK step-by-step inside <think>...</think> tags.
Then output ONE or MORE commands (if multiple relevant) on separate lines:
NEVER return an empty response.
If unsure, output exactly: SEARCH:{query}
Every non-think line MUST start with a valid command prefix.
Never output PERSON with an empty description.
Never output trailing '|' without text after it.

- PERSON:Name|Description (Name MUST be full real person name, never one-word fragments. Description is REQUIRED.)
- PLACE:Name (results confirm location/city/school/institution)
- OPEN:url (results show specific official website)
- TRANSLATE:source_text|from_lang|to_lang|translated_text
- CURRENCY:amount|from_unit|to_unit|converted_value
- WEATHER:location|temp|condition
- UNIT:amount|from_unit|to_unit|converted_value
- INSTALL:name
- UNINSTALL:name
- SEARCH:query (only if general topic and NO specific person/place found)
- COLOR:hex|rgb|hsl
- TIMER:duration_in_seconds
- PASSWORD:length
- QRCODE:data
- SYSTEM_SETTINGS:{"type":"system_settings","setting":"...","value":...}
- NONE (query is a direct conversational question to you as the AI — e.g. "who are you", "what are you", "are you an AI", "how are you", "what can you do", "do you have feelings", "are you sentient", "introduce yourself", "what's your name")
"""

    system_prompt = base_system_prompt.replace(
        "{tool_instruction}",
        "Think first: only call `web_search` if you truly need external, real-world info (unknown person/place/fact/event).\n"
        "Never call it for nonsense text, generic sentences, calc/translate, open/app/settings.\n"
        "If the query is a direct conversational question to you as an AI (identity, feelings, capabilities, self-introduction), output NONE — do NOT search."
    )

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

        out = _safe_fast_completion(
            messages=messages, max_tokens=256, temperature=0.0,
            step_name="Action intent", reset_model=True, tools=[web_search_tool]
        )
        if out is None:
            return jsonify({"actions": [], "chips": []})

        # Handle Tool Calls
        if out['choices'][0]['message'].get('tool_calls'):
            logging.info(f"[TIMING] Phase 1 decided to use tools at: {time.time() - endpoint_start_time:.3f}s")
            tool_calls = out['choices'][0]['message']['tool_calls']
            q_tool = query
            try:
                for tc in tool_calls:
                    if tc.get('function', {}).get('name') == 'web_search':
                        args = json.loads(tc['function'].get('arguments', '{}') or '{}')
                        q_tool = (args.get('query') or query).strip()
                        break
            except Exception:
                q_tool = query
            _pending_actions_put(request_id, {"query": query, "tool_query": q_tool})
            pending_act = {"type": "action_pending", "pending_id": request_id,
                           "title": "Searching the web", "subtitle": q_tool}
            return jsonify({"actions": [pending_act], "action": pending_act, "chips": []})

        if model_manager.current_fast_request_id != request_id:
            logging.info(f"Request {request_id} was cancelled during inference")
            return jsonify({"actions": [], "chips": []})

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
            if any(cmd in result_text for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "CALC:", "TRANSLATE:"]):
                cleaned_text = result_text.replace("<think>", "").replace("</think>", "")
        result_text = cleaned_text

        actions, chips = _parse_fast_action_output(
            result_text=result_text, query=query, request_id=request_id,
            endpoint_start_time=endpoint_start_time, search_context=search_context,
            search_results=search_results, safe_fast_completion=_safe_fast_completion,
        )
        logging.info(f"[TIMING] Total action_endpoint time: {time.time() - endpoint_start_time:.3f}s")
        return jsonify({"actions": actions, "action": actions[0] if actions else None, "chips": chips})

    except Exception as e:
        logging.error(f"Error in action_endpoint: {e}")
        return jsonify({"actions": [], "chips": [], "error": str(e)})


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
                place_act = {"type": "place_pending", "name": query.strip()}
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
            if any(cmd in result_text for cmd in ["PERSON:", "PLACE:", "OPEN:", "SEARCH:", "CALC:", "TRANSLATE:"]):
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
