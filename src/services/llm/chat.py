import logging
import json
import re
import time
import os
import uuid
from simpleeval import SimpleEval
from flask import jsonify

from src.services.llm import model_manager
from src.services.llm.model_manager import ensure_main_model, ensure_fast_model, fast_lock, main_lock, abort_fast_event
from src.services.llm.tools import TOOL_SCHEMAS, execute_tool, flush_pending_trust_requests
from src.services.search.web_search import perform_web_search
from src.services.search.local_search import perform_file_search, should_search_files
from src.services.search.image_search import perform_image_search_with_fallback, should_search_images
from src.services.memory.memvid_store import get_user_memory, remember_fact, remember_update, delete_memory
from src.services.system.location import get_ip_location
from src.services.system.app_launcher import get_app_cache, find_and_launch_app
from src.core.grid_locator import localize_target_from_b64
import src.core.settings_store as settings_store

# Session store for permission-paused requests (keyed by UUID)
_pending_sessions: dict = {}

# ── Tool call display helpers ─────────────────────────────────────────────────

_TOOL_META = {
    "search_web":    {"icon": "🌐", "label": "Web search"},
    "search_files":  {"icon": "📂", "label": "File search"},
    "calculate":     {"icon": "🧮", "label": "Calculate"},
    "search_images": {"icon": "🖼️", "label": "Image search"},
    "memory_recall": {"icon": "🧠", "label": "Memory recall"},
    "memory_save":   {"icon": "🧠", "label": "Remember"},
    "memory_delete": {"icon": "🧠", "label": "Forget"},
    "run_terminal":  {"icon": "🖥️", "label": "Terminal"},
    "install_app":   {"icon": "📦", "label": "Install"},
    "uninstall_app": {"icon": "🗑️", "label": "Uninstall"},
    "find_file":     {"icon": "🔍", "label": "Find file"},
    "read_file":     {"icon": "📖", "label": "Read file"},
    "create_file":   {"icon": "📝", "label": "Create file"},
    "edit_file":     {"icon": "✏️", "label": "Edit file"},
    "organize_folder": {"icon": "📁", "label": "Organize"},
    "compress":        {"icon": "🗜️", "label": "Compress"},
    "convert_file":    {"icon": "🔄", "label": "Convert file"},
    "set_reminder":          {"icon": "⏰", "label": "Set reminder"},
    "list_reminders":        {"icon": "⏰", "label": "Reminders"},
    "delete_reminder":       {"icon": "⏰", "label": "Cancel reminder"},
    "get_calendar_events":   {"icon": "📅", "label": "Calendar"},
    "create_calendar_event": {"icon": "📅", "label": "Create event"},
    "get_unread_emails":     {"icon": "📬", "label": "Emails"},
    "send_email":            {"icon": "📧", "label": "Send email"},
}


def _tool_invocation_line(tool_name: str, args: dict) -> str:
    """Return a one-liner header for a tool call, e.g. '🌐 Web search  "query..."'."""
    meta = _TOOL_META.get(tool_name, {"icon": "⚙", "label": tool_name})
    icon, label = meta["icon"], meta["label"]

    if tool_name in ("search_web", "search_files", "search_images", "memory_recall", "memory_delete"):
        q = args.get("query", "")
        if len(q) > 72:
            q = q[:69] + "…"
        arg_part = f'"{q}"'
    elif tool_name == "calculate":
        expr = args.get("expression", "")
        if len(expr) > 72:
            expr = expr[:69] + "…"
        arg_part = expr
    elif tool_name == "memory_save":
        fact = args.get("fact", "")
        if len(fact) > 72:
            fact = fact[:69] + "…"
        arg_part = f'"{fact}"'
    elif tool_name == "run_terminal":
        display = args.get("description", "") or args.get("command", "")
        if len(display) > 72:
            display = display[:69] + "…"
        arg_part = display
    elif tool_name in ("install_app", "uninstall_app"):
        arg_part = args.get("name", "")
    elif tool_name == "set_reminder":
        label = args.get("label", "")
        fire_at = args.get("fire_at_iso", "")
        # Trim to just time portion if it's a full datetime
        if "T" in fire_at:
            fire_at = fire_at.split("T")[1][:5]  # e.g. "14:30"
        interval = args.get("interval_seconds", 0)
        query = args.get("query", "")
        parts = [f'"{label}"']
        if fire_at:
            parts.append(f"at {fire_at}")
        if interval:
            parts.append(f"every {interval}s")
        if query:
            q = query if len(query) <= 50 else query[:47] + "…"
            parts.append(f"· {q}")
        arg_part = "  ".join(parts)
    elif tool_name == "delete_reminder":
        arg_part = f'"{args.get("query", "")}"'
    elif tool_name == "list_reminders":
        arg_part = ""
    else:
        arg_part = "  ".join(f"{k}: {v!r}" for k, v in args.items())

    return f"{icon}  {label}  {arg_part}"


def _tool_result_summary(tool_name: str, result: str) -> str:
    """One-line summary of what the tool returned."""
    if tool_name == "calculate":
        if "Result: " in result:
            val = result.split("Result: ")[1].split("\n")[0].strip()
            return f"= {val}"
        return result.strip()[:60]

    if tool_name == "search_web":
        n = result.count("Title:")
        if n > 0:
            return f"{n} result{'s' if n != 1 else ''} found"
        if "No results" in result or not result.strip():
            return "no results"

    if tool_name == "search_files":
        if "No relevant files" in result or "No local files" in result:
            return "nothing found"
        n = result.count("--- File:")
        if n > 0:
            return f"{n} file{'s' if n != 1 else ''} found"

    if tool_name == "search_images":
        if "No" in result and ("found" in result or "images" in result):
            return "nothing found"

    if tool_name == "memory_recall":
        if "No memories" in result or "No specific" in result or "No personal" in result:
            return "nothing found"
        lines = [l.strip() for l in result.strip().splitlines() if l.strip()]
        n = len(lines)
        return f"{n} memor{'y' if n == 1 else 'ies'} recalled"

    if tool_name == "memory_save":
        return "saved" if result.startswith("Saved:") else "failed to save"

    if tool_name == "memory_delete":
        return "deleted" if result.startswith("Deleted") else "not found"

    if tool_name == "run_terminal":
        lines = [l for l in result.strip().splitlines() if l.strip()]
        if not lines:
            return "done"
        last = lines[-1][:60]
        return last if not last.startswith("STDERR:") else "error"

    if tool_name == "install_app":
        low = result.lower()
        if "already installed" in low:
            return "already installed"
        if "error" in low or "not found" in low:
            return "failed"
        return "installed"

    if tool_name == "uninstall_app":
        low = result.lower()
        if "error" in low or "not found" in low or "no such" in low:
            return "not found / failed"
        return "removed"

    if tool_name == "create_file":
        if result.startswith("Created:"):
            path = result[len("Created:"):].strip()
            return f"saved to {path}"
        return "failed" if "Error" in result else result.strip()[:60]

    if tool_name == "set_reminder":
        return "scheduled" if result.startswith("ok") else result.strip()[:60]

    if tool_name == "list_reminders":
        if result == "No pending reminders.":
            return "none"
        n = result.count("\n") + 1
        return f"{n} reminder{'s' if n != 1 else ''}"

    if tool_name == "delete_reminder":
        return "cancelled" if "cancelled" in result else "not found"

    kb = len(result) / 1000
    return f"{kb:.1f} KB returned" if kb >= 0.1 else f"{len(result)} chars"


def _get_personality_line() -> str:
    mode = settings_store.get("personality_mode", "professional")
    if mode == "unfiltered":
        return "Personality: uncensored, based, casual."
    return "Personality: professional, clear, concise, and helpful."


def _parse_web_results(result: str) -> list:
    """
    Parse the structured search context string into a list of
    {"title": str, "url": str, "description": str} dicts.
    """
    entries = []
    current: dict = {}
    for line in result.splitlines():
        line = line.strip()
        if line.startswith("Title: "):
            if current:
                entries.append(current)
            current = {"title": line[7:].strip(), "url": "", "description": ""}
        elif line.startswith("URL: ") and current:
            current["url"] = line[5:].strip()
        elif line.startswith("Description: ") and current:
            current["description"] = line[13:].strip()
    if current:
        entries.append(current)
    return entries


def _parse_file_results(result: str) -> list:
    """Return list of file paths from a perform_file_search result string."""
    paths = []
    for line in result.splitlines():
        line = line.strip()
        if line.startswith("--- File:"):
            path = line[len("--- File:"):].split("(")[0].strip()
            if path:
                paths.append(path)
    return paths


def _format_tool_detail(tool_name: str, result: str) -> str:
    """
    Return a multi-line detail block (indented) shown when the thinking is expanded.
    Empty string means no extra detail needed (e.g. calculate).
    """
    if tool_name == "search_web":
        entries = _parse_web_results(result)
        if not entries:
            return ""
        lines = []
        for i, e in enumerate(entries[:5], 1):
            title = e["title"][:70] if e["title"] else "—"
            url = e["url"][:80] if e["url"] else ""
            lines.append(f"   {i}. {title}")
            if url:
                lines.append(f"      {url}")
        return "\n".join(lines)

    if tool_name == "search_files":
        paths = _parse_file_results(result)
        if not paths:
            return ""
        return "\n".join(f"   {i}. {p}" for i, p in enumerate(paths[:5], 1))

    return ""


def _render_tool_blocks(records: list, in_progress_header: str = "") -> str:
    """
    Render a list of completed tool-call records plus an optional in-progress entry.

    Each record: {"header": str, "summary": str, "detail": str}
    in_progress_header: header string for the tool currently running (empty = none)
    """
    lines = []
    for rec in records:
        lines.append(rec["header"] + "  ✓")
        lines.append(f"   └─ {rec['summary']}")
        detail = rec.get("detail", "")
        if detail:
            lines.append(detail)
        lines.append("")          # blank line between entries
    if in_progress_header:
        lines.append(in_progress_header)
        lines.append("   └─ running…")
    return "\n".join(lines).rstrip()


def _build_thinking(model_reasoning: str, tool_records: list, inline_thinking: str = "") -> str:
    """Combine tool blocks + model reasoning into a single thinking string, no duplication."""
    parts = []
    if tool_records:
        parts.append(_render_tool_blocks(tool_records))
    combined_reasoning = (model_reasoning + inline_thinking).strip()
    if combined_reasoning:
        parts.append(combined_reasoning)
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────

def perform_calculation(expression):
    try:
        lower_input = expression.lower()
        for prefix in ["calculate ", "what is ", "solve "]:
            if lower_input.startswith(prefix):
                expression = expression[len(prefix):]

        # Normalise ^ → ** so SimpleEval uses exponentiation, not bitwise XOR
        eval_expr = expression.replace('^', '**')
        s = SimpleEval()
        result = s.eval(eval_expr)

        # Normalise result string so it matches the client-side instant-calc format:
        # whole floats → int string ("25.0" → "25"), others → up to 10 sig-figs ("0.005").
        if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            result_str = str(int(result))
        elif isinstance(result, float):
            result_str = f"{result:.10g}"
        else:
            result_str = str(result)

        # Build LaTeX from the original expression.
        # Order matters: handle ** before replacing lone *.
        latex_expr = expression
        latex_expr = re.sub(r'\*\*(\w+)', r'^{\1}', latex_expr)   # 5**2  → 5^{2}
        latex_expr = re.sub(r'\^(\w+)', r'^{\1}', latex_expr)     # 5^2   → 5^{2}
        latex_expr = latex_expr.replace('*', r' \cdot ')
        latex_expr = latex_expr.replace('/', r' \div ')

        return (f"Expression: {expression}\nResult: {result_str}\nLaTeX: ${latex_expr} = {result_str}$")
    except Exception as e:
        return f"Error calculating '{expression}': {str(e)}"

def should_search(query):
    """Uses Fast Model to decide if we need to Google Search."""
    # Pre-check: Certain patterns always require search
    query_lower = query.lower()
    always_search_patterns = [
        "phone", "telefon", "numer telefonu", "contact", "kontakt",
        "address", "adres", "hours", "godziny", "email", "website",
        "video", "music", "song", "movie", "trailer", "youtube", 
        "listen", "watch", "clip", "how to", "recipe", "show me",
        "find", "search", "show", "how much", "net worth", "price",
        "cost", "today", "now", "news", "current", "weather", "pogoda"
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
            if hasattr(model_manager.fast_model, 'reset'): model_manager.fast_model.reset()
            start_t = time.time()
            out = model_manager.fast_model.create_chat_completion(
                messages=messages,
                max_tokens=5,
                temperature=0.0,
                chat_template_kwargs={"enable_thinking": False},
            )
            dur = time.time() - start_t
            tok_count = out.get('usage', {}).get('completion_tokens', 0) if out else 0
            tps = tok_count / dur if dur > 0 else 0
            logging.info(f"FastModel (Intent): {tok_count} tokens in {dur:.2f}s ({tps:.2f} t/s)")
        if out is None:
            return False
        res = out['choices'][0]['message']['content'].strip()
        res = re.sub(r'<think>.*?</think>', '', res, flags=re.DOTALL | re.IGNORECASE).strip().upper()
        logging.info(f"Search Intent: {res} for '{query}'")
        return "YES" in res
    except Exception as e:
        logging.error(f"Intent check failed: {e}")
        return False


def should_see_screen(query):
    """Uses Fast Model to decide if we need to see the screen."""
    # TEMPORARY DISABLE: Always return False
    return False

    query_lower = query.lower()
    
    # Questions about identity/system information should NOT require screenshots
    identity_patterns = [
        "who are you", "what are you", "how are you", "about you",
        "your name", "tell me about", "what do you do", "what's your name",
        "describe yourself"
    ]
    if any(pattern in query_lower for pattern in identity_patterns):
        logging.info(f"Screen Intent: NO (identity question) for '{query}'")
        return False
    
    # High priority patterns for screen intent
    screen_patterns = [
        "screen", "look at this", "read this", "screenshot", "what's on", "what is on",
        "visible", "window", "monitor", "display", "capture",
        "what do you see", "what can you see", "describe this",
        "which button", "click", "interface", "ui", "what you see", "describe",
        "this page", "on the page", "webpage", "website"
    ]
    if any(pattern in query_lower for pattern in screen_patterns):
         logging.info(f"Screen Intent: YES (pattern match) for '{query}'")
         return True
    
    ensure_fast_model()
    sys_prompt = (
        "Decide if this query requires SEEING the user's SCREEN (taking a screenshot) to answer.\n"
        "Output ONLY 'YES' or 'NO'.\n"
        "YES: 'what is on my screen?', 'summarize this page', 'who is in this video?', 'look at this code', 'explain this error', 'read this', 'which button should i click?', 'what do you see?'.\n"
        "NO: 'hello', 'hey', 'hi', 'who are you?', 'how are you?', 'generate an image', 'find a photo of cats', 'what time is it'.\n"
        "\n"
        "Examples:\n"
        "Query: what is this website? -> YES\n"
        "Query: hello -> NO (greeting)\n"
        "Query: hey -> NO (greeting)\n"
        "Query: who are you? -> NO (identity question)\n"
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
            if hasattr(model_manager.fast_model, 'reset'): model_manager.fast_model.reset()
            # Add timeout to prevent hanging on model inference
            out = model_manager.fast_model.create_chat_completion(
                messages=messages,
                max_tokens=5,
                temperature=0.0,
                chat_template_kwargs={"enable_thinking": False},
            )
        res = out['choices'][0]['message']['content'].strip()
        # Clean up <THINK> blocks
        res = re.sub(r'<THINK>.*?</THINK>', '', res, flags=re.DOTALL | re.IGNORECASE).strip().upper()
        logging.info(f"Screen Intent: {res} for '{query}'")
        return "YES" in res
    except TimeoutError:
        logging.warning(f"Screen Intent check timed out - defaulting to NO for '{query}'")
        return False
    except Exception as e:
        logging.error(f"Screen Intent check failed: {e}")
        return False


def _should_use_reasoning(query):
    """Uses Fast Model to decide if the query needs reasoning (deep thinking) or can be answered directly.
    Returns the model name to use."""
    from src.core.config import MAIN_MODEL_XAI, MAIN_MODEL_XAI_NONREASONING

    query_lower = query.lower()

    # Quick patterns that never need reasoning
    no_reasoning_patterns = [
        "hello", "hi ", "hey", "good morning", "good night", "thanks", "thank you",
        "cześć", "hej", "dzięki", "siema", "elo",
        "translate", "przetłumacz", "tłumacz",
        "what time", "what date", "która godzina",
        "who are you", "what are you", "your name",
        "open ", "launch ", "otwórz ", "uruchom ",
        "click ", "type ", "scroll ", "press ", "swipe ",
    ]
    if any(pattern in query_lower for pattern in no_reasoning_patterns):
        logging.info(f"[ROUTING] Non-reasoning (pattern match) for '{query[:60]}'")
        return MAIN_MODEL_XAI_NONREASONING

    # Quick patterns that always need reasoning
    reasoning_patterns = [
        "explain", "why ", "analyze", "compare", "debug", "fix ",
        "write code", "implement", "algorithm", "step by step",
        "how does", "how do", "what if", "prove", "solve",
    ]
    if any(pattern in query_lower for pattern in reasoning_patterns):
        logging.info(f"[ROUTING] Reasoning (pattern match) for '{query[:60]}'")
        return MAIN_MODEL_XAI

    # Ask fast model for ambiguous queries
    ensure_fast_model()
    sys_prompt = (
        "Decide if this query requires DEEP THINKING (reasoning, analysis, multi-step logic) or can be answered DIRECTLY.\n"
        "Output ONLY 'THINK' or 'DIRECT'.\n"
        "THINK: math problems, coding, debugging, analysis, comparisons, complex explanations, planning, logic puzzles, problem solving.\n"
        "DIRECT: greetings, translations, simple facts, definitions, small talk, commands, simple questions, creative writing, summaries.\n"
        "\n"
        "Examples:\n"
        "Query: write a python function to sort a list -> THINK\n"
        "Query: what is the capital of France -> DIRECT\n"
        "Query: explain how transformers work -> THINK\n"
        "Query: translate hello to spanish -> DIRECT\n"
        "Query: hi how are you -> DIRECT\n"
        "Query: why is the sky blue -> THINK\n"
        "Query: summarize this text -> DIRECT\n"
        "Query: find the bug in this code -> THINK\n"
        "\n"
        "(If unsure, say THINK)."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Query: {query}"},
    ]
    try:
        with fast_lock:
            if hasattr(model_manager.fast_model, 'reset'):
                model_manager.fast_model.reset()
            start_t = time.time()
            out = model_manager.fast_model.create_chat_completion(
                messages=messages,
                max_tokens=5,
                temperature=0.0,
                chat_template_kwargs={"enable_thinking": False},
            )
            dur = time.time() - start_t
            logging.info(f"[ROUTING] Fast model decision in {dur:.2f}s")
        if out is None:
            return MAIN_MODEL_XAI  # default to reasoning
        res = out['choices'][0]['message']['content'].strip()
        res = re.sub(r'<think>.*?</think>', '', res, flags=re.DOTALL | re.IGNORECASE).strip().upper()
        use_reasoning = "THINK" in res
        chosen = MAIN_MODEL_XAI if use_reasoning else MAIN_MODEL_XAI_NONREASONING
        logging.info(f"[ROUTING] {'Reasoning' if use_reasoning else 'Non-reasoning'} for '{query[:60]}' (model={chosen})")
        return chosen
    except Exception as e:
        logging.error(f"[ROUTING] Classification failed: {e}, defaulting to reasoning")
        return MAIN_MODEL_XAI


def extract_actions(text):
    if not text: return "", [], ""

    actions = []
    clean_text = text
    thinking_content = ""
    
    # First, extract thinking content from <think> tags
    # This MUST be done BEFORE other processing to avoid contaminating the answer
    think_match = re.search(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
    if think_match:
        thinking_content = think_match.group(1).strip()
        # Remove the ENTIRE think block from text
        clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    else:
        # Unclosed <think>: treat everything from <think> to end as thinking
        unclosed = re.search(r'<think>(.*)$', text, re.DOTALL | re.IGNORECASE)
        if unclosed:
            thinking_content = unclosed.group(1).strip()
            clean_text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # Now process the clean_text (without thinking) for actions and JSON
    # Try to find JSON block
    json_block = None
    
    match = re.search(r"```json\s*(.*?)($|```)", clean_text, re.DOTALL | re.IGNORECASE)
    if match:
        json_block = match.group(1).strip()
        clean_text = clean_text.replace(match.group(0), "").strip()
    elif "{" in clean_text:
        # Fallback: find { ... }
        match = re.search(r"(\{.*\})", clean_text, re.DOTALL)
        if match:
            json_block = match.group(1).strip()
            clean_text = clean_text.replace(json_block, "").strip()

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
                # Extract 'answer' if present in JSON
                if "answer" in parsed and isinstance(parsed["answer"], str):
                    if not clean_text.strip():
                        clean_text = parsed["answer"]
                
                actions = parsed.get("actions", [])
                if not actions and "type" in parsed:
                    actions = [parsed]
            elif isinstance(parsed, list):
                actions = parsed
        except Exception as e:
            # Common during streaming (incomplete JSON) - log as debug to avoid noise
            logging.debug(f"Failed to parse JSON block (partial?): {e}")

    # Remove trailing cleanup markers
    clean_text = re.sub(r"(?i)(JSON block for actions|Actions|JSON|Here is the JSON):\s*$", "", clean_text).strip()

    return clean_text, actions, thinking_content


def _split_thinking_and_answer(text):
    """Split streamed text into (thinking_so_far, answer_so_far) for UI.
    
    DEFAULT: Everything is thinking until </think> is seen.
    RULE: When </think> appears, everything before it (after <think>) is thinking, everything after is answer.
    """
    if not text:
        return "", ""
    text = text.strip()
    
    # If </think> tag is found, split there
    if '</think>' in text.lower():
        # Extract thinking content (between <think> and </think>)
        think_match = re.search(r'<think>(.*?)</think>\s*(.*)$', text, re.DOTALL | re.IGNORECASE)
        if think_match:
            thinking = think_match.group(1).strip()
            answer = think_match.group(2).strip()
            return thinking, answer
        else:
            # Fallback: if no <think> found but </think> exists, take everything before </think> as thinking
            match = re.search(r'(.*?)</think>\s*(.*)$', text, re.DOTALL | re.IGNORECASE)
            if match:
                thinking = match.group(1).strip()
                answer = match.group(2).strip()
                return thinking, answer
    
    # No </think> found: check if we have opened <think> tag
    if '<think>' in text.lower():
        # Extract everything after <think> as thinking so far (not closed yet)
        think_match = re.search(r'<think>(.*?)$', text, re.DOTALL | re.IGNORECASE)
        if think_match:
            thinking = think_match.group(1).strip()
            return thinking, ""
            
    # CRITICAL FIX: The server might NOT be outputting <think> tags at all in the stream 
    # if it treats them as special tokens or if the chat template hides them.
    # However, for Qwen3-Thinking, the thinking content usually comes FIRST.
    # If we are streaming and haven't seen an end tag yet, and the text is getting long,
    # it might ALL be thinking content if the model just "thinks" by default.
    
    # BUT, looking at the raw curl output, the model DOES separate `reasoning_content` in the JSON response
    # if using the OpenAI API format correctly!
    # The `llama-server` returns `reasoning_content` field in the delta for thinking models?
    # NO, standard OpenAI API doesn't have `reasoning_content` in delta usually, unless it's DeepSeek style.
    # Wait, the curl output above showed: "reasoning_content": "First, the question is..." in the final JSON.
    
    # If we are streaming, we need to check if `chunk.choices[0].delta` has `reasoning_content`.
    
    return "", text



def _extract_facts_background(query, prev_ctx_msg):
    """Run fact extraction in a background thread to avoid blocking the main response."""
    try:
        fact_prompt = f"""You are a memory extractor. Extract FACTS (FA) and UPDATES (UP) about the user.
Rules:
1. FA: [New Fact]
2. UP: [Correction]
3. NO_INFO: [No personal info]
4. NO_INFO: [No personal info]
5. BE DECISIVE. If user says "I think so", assume it is a fact.
6. IGNORE commands or immediate requests (e.g. "Open app"). Output NO_INFO.

Context: {prev_ctx_msg}
Input: {query}
Output:"""
        
        logging.info("[CHAT] Starting Fast Model memory extraction (BACKGROUND)...")
        
        ensure_fast_model()
        with fast_lock:
             if hasattr(model_manager.fast_model, 'reset'): model_manager.fast_model.reset()
             f_out = model_manager.fast_model.create_chat_completion(
                 messages=[{"role": "system", "content": "You are a memory extractor."}, {"role": "user", "content": fact_prompt}],
                 max_tokens=64,
                 temperature=0.0,
                 chat_template_kwargs={"enable_thinking": False},
             )
             if f_out and 'choices' in f_out and len(f_out['choices']) > 0:
                 f_res = f_out['choices'][0]['message']['content'].strip()
                 
                 # Clean up Qwen thinking blocks
                 f_res = re.sub(r'<think>.*?</think>', '', f_res, flags=re.DOTALL | re.IGNORECASE).strip()
                 
                 logging.info(f"Memory Extraction RAW: {f_res}")
                 
                 if "FA:" in f_res:
                     fact_to_save = f_res.split("FA:")[1].strip()
                     if remember_fact(fact_to_save):
                         logging.info(f"Background Remembered: {fact_to_save}")
                 elif "UP:" in f_res:
                     fact_to_save = f_res.split("UP:")[1].strip()
                     if remember_update(fact_to_save):
                         logging.info(f"Background Updated: {fact_to_save}")
                 elif "FO:" in f_res:
                     fact_to_forget = f_res.split("FO:")[1].strip()
                     if delete_memory(fact_to_forget):
                         logging.info(f"Background Forgot: {fact_to_forget}")
             else:
                 logging.warning("Memory Extraction: No response from fast_model.")

    except Exception as e: logging.error(f"Extraction Error: {e}")


def process_chat_request(query, history, screenshot_b64=None, stream=False, resume_session_id=None):
    import sys # Ensure sys is available
    abort_fast_event.set()
    model_manager.current_fast_request_id = None
    ensure_main_model()

    if not model_manager.llm:
        return {"answer": f"Error: Model failed to load."}

    # === LOGGING: Confirm model entry ===
    logging.info(f"[CHAT] Starting process_chat_request for query: '{query}' (Stream: {stream})")

    # CHECK SCREEN INTENT
    if not screenshot_b64 and should_see_screen(query):
        logging.info(f"[SCREENSHOT] Requesting Screenshot from Client for query: '{query}'")
        logging.info("[SCREENSHOT] Client has 5 seconds to capture and return the screenshot")
        return {"special_action": "screenshot_required"}

    # Initialize early to avoid UnboundLocalError
    thinking_content = ""
    answer = ""
    actions = []

    # HARDCODED: App Launcher (Deterministic Bypass)
    app_match = re.search(r"^(?:open|run|launch|start)\s+(.+)$", query.strip(), re.IGNORECASE)
    if app_match and len(query.split()) < 10:
        target_app = app_match.group(1).strip().lower()
        if target_app in ["browser", "web browser", "internet"]: target_app = "google-chrome"
        if target_app == "chrome": target_app = "google-chrome"
        
        cache = get_app_cache()
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
                return {
                    "answer": f"Opening {msg}...",
                    "actions": [{
                        "type": "status",
                        "status": "success",
                        "description": f"Launched {msg}"
                    }]
                }

    # Background: Fact Extraction
    # DISABLED: We now rely on the Main Model's function calling ("memory_save") 
    # to be smarter about what to remember, instead of running a parallel heuristic model.
    # This optimizes resource usage and simplifies the architecture.
    
    # prev_ctx_msg = "None"
    # if history and len(history) > 0:
    #    last_item = history[-1]
    #    if isinstance(last_item, dict):
    #        prev_ctx_msg = last_item.get('content', 'None')

    # Launch in background thread
    # import threading
    # threading.Thread(target=_extract_facts_background, args=(query, prev_ctx_msg), daemon=True).start()
    
    # Auto-actions are no longer available immediately
    auto_actions = []

    if screenshot_b64:
        logging.info("[CHAT] Screenshot provided — skipping tool calls for this request.")

    import concurrent.futures
    _prefetch_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as _pool:
        _loc_fut = _pool.submit(get_ip_location)
        _mem_fut = _pool.submit(get_user_memory, query)
        _routing_fut = _pool.submit(_should_use_reasoning, query)
        user_loc = _loc_fut.result()
        user_personal_context = _mem_fut.result()
        routed_model = _routing_fut.result()
    logging.info(f"[CHAT] Pre-fetch done in {time.time() - _prefetch_start:.3f}s (loc+memory+routing)")

    # NOTE: user_personal_context will NOT have the "Just Learned" fact from this turn,
    # because it is being extracted in the background. This is a trade-off for speed.
    
    from datetime import datetime
    now = datetime.now()
    current_date = now.strftime('%Y-%m-%d')
    current_datetime = now.strftime('%Y-%m-%dT%H:%M:%S')

    personality_line = _get_personality_line()

    system_prompt = f"""You are Omni — user's personal AI companion running on his macOS.
{personality_line}

## User context (private notes — ONLY reference when DIRECTLY relevant to the current query)
{user_personal_context}
IMPORTANT: Only reference facts above that are directly relevant to the user's question. Do NOT volunteer unrelated personal information.
Location: {user_loc} | Date: {current_date} | Current datetime (ISO): {current_datetime}

## Tools available (use via function calling when needed)
- **search_web** — current events, news, prices, weather, people, any up-to-date info
- **search_files** — user's local documents, notes, code files, PDFs, certificates, official documents, invoices on this machine. Use proactively when query implies finding/locating a document, even in non-English languages.
- **calculate** — precise arithmetic or algebraic expressions
- **search_images** — photos/images stored locally
- **memory_recall** — retrieve facts/preferences/details you've remembered about this user from past conversations
- **memory_save** — permanently store a new fact or preference about this user for future conversations
- **memory_delete** — forget/remove a memory when user asks you to or when info is outdated
- **find_file** — locate a file or folder by name on the user's Mac; returns exact paths; use BEFORE deleting, moving, or opening a file to get its precise path
- **read_file** — read the full content of any file: PDF, DOCX, XLSX, CSV, PPTX, code, plain text, etc.; use whenever the user asks you to read, summarise, analyse, or answer questions about a specific file; ALWAYS prefer this over run_terminal with cat; for PDFs and Office files this is the ONLY way to get the text
- **create_file** — create a new text file with content; use for any "create/write/save a file" request; defaults to ~/Desktop; ALWAYS prefer this over run_terminal for file creation
- **edit_file** — edit an existing file by replacing a specific text snippet with new text; use for any "edit/update/modify/change/fix" file request; first find the file and read its content with read_file, then call this with the exact old_text and new_text; ALWAYS prefer this over run_terminal for file edits
- **organize_folder** — auto-sort files into smart subfolders (Images/Photos, Code/Python, Documents/PDFs, etc.); use for any "organize/cleanup/tidy" request; supports 100+ file types; unrecognized files go to "Other"; just summarize the result to the user — NO extra tool calls needed
- **compress** — compress files or folders into a ZIP archive; use for any "compress/zip/archive/bundle" request; accepts multiple paths; ALWAYS prefer this over run_terminal for compression
- **convert_file** — convert a file to a different format (images, documents, audio, video); use for any "convert/export/change format" request; supports PNG, JPG, PDF, DOCX, MP3, MP4, and many more; ALWAYS prefer this over run_terminal for file conversions
- **run_terminal** — execute any shell command on macOS (defaults write, pmset, diskutil, etc.); NEVER tell user to open Terminal manually; NEVER use run_terminal for tasks that have a dedicated tool (send_email, get_unread_emails, get_calendar_events, create_calendar_event, install_app, uninstall_app, create_file, edit_file, compress, find_file, read_file, organize_folder)
- **install_app** — install an app via Homebrew; use for any install/download request; tries cask then formula
- **uninstall_app** — remove an app via Homebrew; use for any uninstall/remove request
- **send_email** — send an email via macOS Mail app; params: to (address), subject, body; email is sent automatically — do NOT tell user to click Send; ALWAYS use this instead of run_terminal for sending emails
- **get_unread_emails** — get recent unread emails from macOS Mail app; optional param: limit (default 5); SLOW (~10s) — only call when user explicitly asks about emails; ALWAYS use this instead of run_terminal for reading emails
- **get_calendar_events** — SLOW (~10s) — only call when user explicitly asks about calendar/schedule; ALWAYS use this instead of run_terminal for calendar queries
- **create_calendar_event** — create a new calendar event; params: title, start_iso (YYYY-MM-DD HH:MM:SS), duration_minutes (default 60), description; ALWAYS use this instead of run_terminal for creating events
- **set_reminder** — schedule a macOS notification reminder; use for any "remind me in X / remind me at Y" request; fire_at_iso must be computed from Current datetime above; ALWAYS use this for reminders instead of calendar events or run_terminal
- **list_reminders** — list all pending reminders the user has set
- **delete_reminder** — cancel a pending reminder by natural language description

Memory usage rules:
- Call **memory_recall** proactively when the answer might depend on something the user told you before.
- Call **memory_save** IMMEDIATELY when the user shares something personal or important that should persist, a new fact, name, preference, or corrects you.
- Call **memory_delete** when the user says "forget that", "that's wrong", or info is confirmed outdated.
- Never tell the user you "can't remember" without first calling memory_recall.
- CRITICAL: Do NOT just say "I will remember that" — you MUST call the memory_save tool.
- Privacy: Use memory naturally. If asked "what do you know about me?", summarize key facts conversationally. Do NOT list email addresses, passwords, or sensitive data unless explicitly asked.
- RELEVANCE FILTER: Only use user context facts that directly pertain to answering the current query. Do NOT mention unrelated personal details.

File search heuristic: If the user mentions a specific document type (certificate, invoice, contract, resume, tax form, etc.) or uses words implying they want to find/locate something on their computer, call search_files FIRST before answering. This applies regardless of query language.

Use tools proactively. Do NOT pretend to search or recall — actually call the tool.
IMPORTANT efficiency rules:
- Do NOT call more than 3 tools in a single iteration. Pick the most relevant ones.
- If memory_recall returns nothing for a person's contact info, ASK the user instead of searching files/emails repeatedly.
- When sending email: if you don't know the recipient's address after one memory_recall, ask the user. Do NOT waste time searching files, emails, and calendar.
- Match the email subject/body to the user's EXACT request. Read the query carefully.
- **After gathering context** (memory_recall, get_calendar_events, etc.), you MUST proceed to call the action tool (send_email, run_terminal, set_reminder, etc.) in the next iteration. Do NOT stop and write a response describing what you "did" — actually do it.

---
## ACTIONS — output a ```json``` block for every action

**Open application**
{{"type": "open_app", "name": "google-chrome"}}
→ "browser"/"chrome" → "google-chrome". This will prompt the user if they want to launch the app.
→ NEVER output open_app for Calendar, Reminders, Mail, or Focus after calling get_calendar_events, get_unread_emails, or any read-only query — those tools already fetch the data; just answer in text.

**Open URL** (ONLY links from Context data above — never invent)
{{"type": "open_url", "url": "https://..."}}
→ Use for: play music, watch video, open website. If no URL in results, say so — don't fabricate.

**System setting**
{{"type": "system_settings", "setting": "brightness", "value": 80}}
Available settings and values:
  brightness   → 0–100
  volume       → 0–100
  mute         → true / false
  dark_mode    → true (dark) / false (light)
  night_shift  → true / false
  dnd          → true / false
  wifi         → true / false
  bluetooth    → true / false

**Fast Actions** (display visual cards instead of full text)
{{"type": "timer", "duration": 300}} (duration in seconds)
{{"type": "color_preview", "color_hex": "#FF00FF", "rgb_val": "255,0,255", "hsl_val": "300,100,50"}}
{{"type": "password", "length": 16}}
{{"type": "qrcode", "data": "https://..."}}

**Open a local file** — when user asks to find, open, show, or locate a file:
→ Call search_files to find it, then IMMEDIATELY use the run_terminal tool to `open "/path/to/file"`
→ Always pick the most relevant result (prefer PDFs/documents over source code for document queries).
→ NEVER just report the path or save it to memory — always open the file right away.

**Delete a file or folder** — when user asks to delete, remove, or trash a file/folder:
→ ALWAYS call find_file first to get the exact path(s). Do NOT guess or invent paths.
→ After finding the path, call run_terminal with `rm "/exact/path"` (file) or `rm -r "/exact/path"` (folder).
→ If find_file returns multiple matches, pick the most obvious one or briefly list them and ask the user to confirm before deleting.
→ Prefer moving to trash: `osascript -e 'tell app "Finder" to delete POSIX file "/exact/path"'` when trust < 3.

**System optimization** (when user asks to optimize, speed up, clean up, or improve system performance)
→ First use run_terminal to gather system info: running processes (ps aux), login items, memory/disk usage.
→ Analyze results and output an optimize_system action with concrete suggestions:
{{"type": "optimize_system", "suggestions": [
  {{"title": "Quit Safari (620 MB RAM)", "description": "Safari is idle in the background", "command": "killall Safari", "impact": "medium"}},
  {{"title": "Remove Spotify from login items", "description": "Spotify auto-launches at startup, slowing boot time", "command": "osascript -e 'tell application \\"System Events\\" to delete login item \\"Spotify\\"'", "impact": "low"}}
]}}
→ Each suggestion needs: title (short, include metric like RAM/CPU), description (why), command (shell), impact (low/medium/high).
→ Be smart: only suggest killing apps the user isn't actively using. Never suggest killing Finder, SystemUIServer, WindowServer, Dock, or Omni itself.
→ Prioritize: high-memory idle apps, unnecessary login items, heavy background processes.
→ Write a brief intro sentence before the action (e.g. "Here's what I found").

**Computer control** (only when user explicitly says click/type/scroll/press)
{{"type": "computer_control", "action": "type", "text": "hello world", "description": "typing text"}}
{{"type": "computer_control", "action": "scroll", "direction": "down", "description": "scrolling"}}
→ Click coordinates are handled automatically by the grid system. Just say "Clicking X."
→ NEVER use action "describe" — write the description in your text response instead.

---
## Rules
- Screenshot provided → you're seeing user's screen right now. Don't ask to navigate anywhere.
- Never invent URLs. Only use links from Context data.
- "Describe screen" / "What do you see?" → text answer only, no computer_control action.
- For any command: just DO it. Never ask "would you like me to…?" — act immediately.
- NEVER instruct user to open Terminal or manually run commands. Always use run_terminal tool.
- NEVER tell user to install anything manually. Always use install_app / uninstall_app tools.
- **CRITICAL**: NEVER claim to have sent an email, run a command, set a reminder, created a file, or performed ANY action unless you actually called the corresponding tool in this response. Writing "I sent the email" without calling send_email is a hallucination and strictly forbidden. If you have gathered enough context (e.g. after memory_recall or get_calendar_events), proceed immediately to call the action tool in the very next step.
- If user shares a new fact about himself, acknowledge it naturally.
- Always emit valid JSON in a ```json``` block for actions.
- "Find / open / show me [file]" → search_files THEN immediately open the best match with the run_terminal tool. Never just report the path.
- **CRITICAL PERMISSION RULE**: If a tool returns `[Permission required]`, DO NOT write texts like "Please grant permission", "I created it, accept the popup", or mention "Automation trust". The UI handles this seamlessly. Simply state your intended action in the present continuous tense (e.g. "Creating the file", "Running the command") or write a concise, elegant response as if the action is executing perfectly. Do not mention the existence of permissions or popups.
"""
    
    messages = [{"role": "system", "content": system_prompt}]

    for msg in history:
        role = msg.get('role', 'user')
        # Keep only roles accepted by OpenAI-compatible APIs (xAI, Groq)
        if role not in ('system', 'user', 'assistant', 'tool', 'function'):
            role = 'assistant'
        content = msg.get('content', '')
        messages.append({"role": role, "content": content})
    
    # Handle Multimodal Input
    if screenshot_b64:
        try:
            temp_img_path = "/tmp/omni_context.png"
            if sys.platform.startswith("win"):
                 import tempfile
                 temp_img_path = os.path.join(tempfile.gettempdir(), "omni_context.png")
            
            import base64
            with open(temp_img_path, "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            
            logging.info(f"Screenshot saved to {temp_img_path}. Attaching to LLM request.")

            # Format for llama-cpp-python / Qwen2.5-VL
            query_lower = query.lower()
            is_click_intent = any(x in query_lower for x in ["click", "type", "press", "select", "right click", "double click"])
            
            if is_click_intent:
                # === GRID-BASED LOCALIZATION ===
                target_match = re.search(r'(?:click|press|select|type|right click|double click)\s+(?:on\s+)?(?:the\s+)?(.+)', query, re.IGNORECASE)
                raw_target = target_match.group(1).strip() if target_match else query
                target_description = re.sub(r'\s+(?:button|icon|link|menu item)$', '', raw_target, flags=re.IGNORECASE).strip()
                
                logging.info(f"Grid Localization: Target = '{target_description}' (raw: '{raw_target}')")
                
                try:
                    click_x, click_y = localize_target_from_b64(
                        screenshot_b64,
                        target_description,
                        model_manager.llm,
                        max_iterations=5,
                        grid_size=3
                    )
                    
                    if click_x > 0 and click_y > 0:
                        logging.info(f"Grid Localization: Found at ({click_x}, {click_y})")
                        
                        return {
                            "answer": f"Clicking {target_description}.",
                            "actions": [{
                                "type": "computer_control",
                                "action": "click",
                                "coordinate": [click_x, click_y],
                                "description": f"Clicking {target_description}"
                            }]
                        }
                    else:
                        logging.warning("Grid Localization: Failed to find target, falling back to LLM")
                except Exception as e:
                    logging.error(f"Grid Localization Error: {e}")
                
                # Fallback: Ask LLM to describe what it sees
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

    # Tools are disabled for screenshot queries (model sees the screen directly)
    active_tools = None if screenshot_b64 else TOOL_SCHEMAS

    # Session resumption: override messages and model with saved state (skips re-running all LLM iterations)
    _resume_state = None
    if resume_session_id and resume_session_id in _pending_sessions:
        _resume_state = _pending_sessions.pop(resume_session_id)
        messages = _resume_state["messages"]
        routed_model = _resume_state["routed_model"]
        auto_actions = _resume_state.get("auto_actions", [])
        active_tools = _resume_state.get("active_tools", TOOL_SCHEMAS)
        logging.info(f"[CHAT] Resuming session {resume_session_id} at iter {_resume_state['tool_iter']}")

    try:
        abort_fast_event.clear()

        # routed_model already resolved from parallel ThreadPoolExecutor above

        # Start total request timer
        request_start_time = time.time()
        logging.info(f"[CHAT] Processing request at {request_start_time:.2f} (model={routed_model})")

        _lock_wait_start = time.time()
        with main_lock:
            logging.info(f"[CHAT] Main lock acquired after {time.time() - _lock_wait_start:.3f}s wait. Starting generation...")

            if stream:
                # ── Streaming with tool-calling loop ────────────────────────
                # IMPORTANT: keep model_reasoning (only LLM tokens) separate from
                # tool_records (structured tool log) to avoid double-rendering.
                max_tool_iters = 5
                tool_iter = 0
                model_reasoning = ""   # Accumulated LLM reasoning_content tokens (all iterations)
                tool_records: list = []  # Completed tool-call entries for display
                all_answer_text = ""   # Accumulated answer text across iterations
                has_pending_trust = False  # Track if any tool needs permission
                _tool_file_paths: list = []  # File paths produced by tools (for "Enter to open" hint)

                # Restore loop state when resuming a permission-paused session
                if _resume_state:
                    tool_iter      = _resume_state["tool_iter"]
                    max_tool_iters = _resume_state["max_tool_iters"]
                    model_reasoning = _resume_state["model_reasoning"]
                    tool_records   = list(_resume_state["tool_records"])
                    all_answer_text = _resume_state["all_answer_text"]
                    _tool_file_paths = list(_resume_state["_tool_file_paths"])

                while tool_iter < max_tool_iters:
                    iter_start_time = time.time()
                    tool_iter += 1
                    logging.info(f"[CHAT] Iteration {tool_iter} started at {iter_start_time:.2f}")

                    if hasattr(model_manager.llm, 'reset'):
                        model_manager.llm.reset()

                    _llm_call_start = time.time()
                    streamer = model_manager.llm.create_chat_completion(
                        messages=messages,
                        max_tokens=1536,
                        temperature=0.6,
                        stream=True,
                        tools=active_tools,
                        model_override=routed_model,
                    )

                    accumulated_text = ""
                    iter_reasoning = ""   # reasoning tokens from THIS iteration only
                    accumulated_tc: dict = {}  # index → tool call dict
                    _first_token_logged = False

                    for chunk in streamer:
                        if model_manager.abort_fast_event.is_set():
                            logging.info("Chat Request Aborted during streaming.")
                            return

                        if hasattr(chunk, 'choices') and chunk.choices:
                            delta = chunk.choices[0].delta

                            # Accumulate streaming tool call deltas
                            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                                for tc_delta in delta.tool_calls:
                                    idx = tc_delta.index
                                    if idx not in accumulated_tc:
                                        accumulated_tc[idx] = {
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    if tc_delta.id:
                                        accumulated_tc[idx]["id"] = tc_delta.id
                                    if tc_delta.function:
                                        if tc_delta.function.name:
                                            accumulated_tc[idx]["function"]["name"] += tc_delta.function.name
                                        if tc_delta.function.arguments:
                                            accumulated_tc[idx]["function"]["arguments"] += tc_delta.function.arguments

                            token = (delta.content or "") if hasattr(delta, 'content') else ""
                            reasoning_token = ""
                            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                                reasoning_token = delta.reasoning_content
                            elif hasattr(delta, 'model_extra') and delta.model_extra and 'reasoning_content' in delta.model_extra:
                                reasoning_token = delta.model_extra['reasoning_content']
                            if reasoning_token:
                                iter_reasoning += reasoning_token
                        else:
                            token = ""
                            reasoning_token = ""
                            if isinstance(chunk, dict):
                                delta_dict = chunk.get('choices', [{}])[0].get('delta', {})
                                token = delta_dict.get('content', '')
                                reasoning_token = delta_dict.get('reasoning_content', '')
                                if reasoning_token:
                                    iter_reasoning += reasoning_token

                        accumulated_text += token

                        if not _first_token_logged and (token or reasoning_token):
                            logging.info(f"[CHAT] First token at {time.time() - _llm_call_start:.3f}s after LLM call (iter {tool_iter})")
                            _first_token_logged = True

                        # Yield partial — combine tool log + all model reasoning so far
                        if token or reasoning_token:
                            inline_thinking, answer_so_far = _split_thinking_and_answer(accumulated_text)
                            ans_clean, _, _ = extract_actions(answer_so_far) if answer_so_far else ("", [], "")
                            
                            # Combine previous iterations' text with current
                            full_display_answer = (all_answer_text + ans_clean).strip()
                            
                            combined_thinking = _build_thinking(
                                model_reasoning + iter_reasoning, tool_records, inline_thinking
                            )
                            if combined_thinking or full_display_answer:
                                yield ("partial", {
                                    "thinking": combined_thinking, 
                                    "answer": "" if has_pending_trust else full_display_answer
                                })

                    # Persist this iteration's reasoning tokens
                    model_reasoning += iter_reasoning

                    # ── After the stream: did the model request tool calls? ──
                    if accumulated_tc:
                        tool_calls = [accumulated_tc[i] for i in sorted(accumulated_tc)]

                        messages.append({
                            "role": "assistant",
                            "content": accumulated_text or None,
                            "tool_calls": tool_calls,
                        })

                        # Execute each tool and show real-time progress
                        for tc in tool_calls:
                            # Check abort before each tool execution
                            if model_manager.abort_fast_event.is_set():
                                logging.info("Chat Request Aborted before tool execution.")
                                return
                            tool_start = time.time()
                            tool_name = tc["function"]["name"]
                            try:
                                args = json.loads(tc["function"]["arguments"])
                            except Exception:
                                args = {}

                            header = _tool_invocation_line(tool_name, args)
                            n_total = len(tool_records) + len(tool_calls)
                            th_label = f"Using {n_total} tool{'s' if n_total != 1 else ''}…"

                            # "running…" state — show immediately
                            # Render completed records + the new in-progress entry
                            in_prog_display = _render_tool_blocks(tool_records, in_progress_header=header)
                            thinking_running = (
                                (model_reasoning.strip() + "\n\n" + in_prog_display).strip()
                                if model_reasoning.strip() else in_prog_display
                            )
                            yield ("partial", {
                                "thinking": thinking_running,
                                "answer": "",
                                "thinking_header": th_label,
                            })

                            result = execute_tool(tool_name, args)
                            # Check abort after tool execution (tool may have been slow)
                            if model_manager.abort_fast_event.is_set():
                                logging.info("Chat Request Aborted after tool execution.")
                                return
                            if result and "[Permission required]" in str(result):
                                has_pending_trust = True
                            tool_dur = time.time() - tool_start
                            logging.info(f"[tool:{tool_name}] result length={len(result)} (took {tool_dur:.4f}s)")

                            summary = _tool_result_summary(tool_name, result)
                            detail = _format_tool_detail(tool_name, result)
                            tool_records.append({"header": header, "summary": summary, "detail": detail})

                            # Track file paths from tool results for "Enter to open" hint
                            if tool_name in ("create_file", "compress") and result.startswith("Created:"):
                                # Strip size suffix like " (1.2 MB)" from compress results
                                _raw = result[len("Created:"):].strip()
                                _size_m = re.search(r'\s+\([^)]+\)\s*$', _raw)
                                _tool_file_paths.append(_raw[:_size_m.start()] if _size_m else _raw)
                            elif tool_name == "find_file" and not result.startswith(("No files", "Error")):
                                for _line in result.splitlines()[:1]:
                                    _m = re.match(r'\[(?:file|dir)\]\s+(.+?)(?:\s+\(|$)', _line)
                                    if _m:
                                        _tool_file_paths.append(_m.group(1).strip())

                            # "done" state update
                            n_done = len(tool_records)
                            th_label = f"Used {n_done} tool{'s' if n_done != 1 else ''}"
                            yield ("partial", {
                                "thinking": _build_thinking(model_reasoning, tool_records),
                                "answer": "",
                                "thinking_header": th_label,
                            })

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result,
                            })
                            
                        # Discard intermediate text from tool-calling iterations;
                        # the LLM's final response (after all tools) is always complete.

                        logging.info(f"[CHAT] Iteration {tool_iter} finished in {time.time() - iter_start_time:.4f}s")

                        # If any tool blocked on permission, stop the loop now.
                        # The UI will show the popup; when the user approves, the
                        # query is re-sent with elevated trust — no wasted LLM iteration.
                        if has_pending_trust:
                            pending = flush_pending_trust_requests()
                            # Save the full conversation state so the UI can resume from here
                            # instead of restarting all LLM iterations from scratch.
                            _sid = str(uuid.uuid4())
                            _pending_sessions[_sid] = {
                                "messages":       list(messages),
                                "tool_iter":      tool_iter,
                                "max_tool_iters": max_tool_iters,
                                "model_reasoning": model_reasoning,
                                "tool_records":   list(tool_records),
                                "all_answer_text": all_answer_text,
                                "_tool_file_paths": list(_tool_file_paths),
                                "routed_model":   routed_model,
                                "auto_actions":   list(auto_actions),
                                "active_tools":   active_tools,
                            }
                            for req in pending:
                                req["session_id"] = _sid
                            logging.info(f"[CHAT] Stopping early — permission required ({len(pending)} trust_request(s)), session={_sid}")
                            yield ("final", {
                                "answer": "",
                                "actions": pending,
                                "thinking": _build_thinking(model_reasoning, tool_records),
                            })
                            return

                        continue  # Next iteration with tool results in messages

                    # ── No tool calls: this is the final response ────────────
                    _iter_dur = time.time() - iter_start_time
                    _total_dur = time.time() - request_start_time
                    logging.info(f"[CHAT] Iter {tool_iter} LLM generation: {_iter_dur:.3f}s | total so far: {_total_dur:.3f}s")
                    inline_thinking, answer_text = _split_thinking_and_answer(accumulated_text)
                    # model_reasoning already includes all iterations; inline_thinking from <think> tags
                    thinking_content = _build_thinking(model_reasoning, tool_records, inline_thinking)
                    answer, actions, _ = extract_actions(answer_text) if answer_text else (answer_text, [], "")
                    
                    final_full_answer = answer.strip()
                    
                    if auto_actions:
                        actions.extend(auto_actions)
                    actions.extend(flush_pending_trust_requests())
                    _postprocess_actions(actions, final_full_answer)
                    logging.info(f"[STREAM] final: thinking={len(thinking_content)}, answer={len(final_full_answer)}, actions={len(actions)}, total={time.time() - request_start_time:.3f}s")

                    final_header = f"Used {len(tool_records)} tool{'s' if len(tool_records) != 1 else ''}" if tool_records else None
                    # Pass first openable file path from tools (for "Enter to open" hint)
                    _fh = _tool_file_paths[0] if _tool_file_paths else None
                    yield ("final", {
                        "answer": final_full_answer,
                        "actions": actions,
                        "thinking": thinking_content,
                        **({"file_hint": _fh} if _fh else {}),
                        **({"thinking_header": final_header} if final_header else {}),
                    })
                    return

                # Max tool iterations reached
                logging.warning("[tool] Max tool iterations reached in streaming mode")
                yield ("final", {"answer": "I got stuck calling tools. Please try again.", "actions": [], "thinking": thinking_content})

            else:
                # ── Non-streaming with tool-calling loop ─────────────────────
                max_tool_iters = 5
                full_text = ""
                external_thinking = ""

                for tool_iter in range(max_tool_iters):
                    iter_start_time = time.time()
                    logging.info(f"[CHAT-NS] Iteration {tool_iter+1} started")

                    if hasattr(model_manager.llm, 'reset'):
                        model_manager.llm.reset()

                    output = model_manager.llm.create_chat_completion(
                        messages=messages,
                        max_tokens=1536,
                        temperature=0.6,
                        tools=active_tools,
                        model_override=routed_model,
                    )
                    msg = output['choices'][0]['message']
                    full_text = (msg.get('content') or "").strip()
                    external_thinking = msg.get('reasoning_content', '') or ""
                    if not isinstance(external_thinking, str):
                        external_thinking = ""
                    tool_calls = msg.get('tool_calls', [])

                    if not tool_calls:
                        logging.info(f"[CHAT-NS] Final response received after {time.time() - request_start_time:.4f}s total")
                        break  # Final answer — no more tool calls

                    messages.append({
                        "role": "assistant",
                        "content": full_text or None,
                        "tool_calls": tool_calls,
                    })
                    permission_blocked = False
                    for tc in tool_calls:
                        tool_start = time.time()
                        tool_name = tc["function"]["name"]
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except Exception:
                            args = {}
                        result = execute_tool(tool_name, args)
                        if result and "[Permission required]" in str(result):
                            permission_blocked = True
                        logging.info(f"[tool-ns:{tool_name}] result length={len(result)} (took {time.time() - tool_start:.4f}s)")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })

                    logging.info(f"[CHAT-NS] Iteration {tool_iter+1} finished in {time.time() - iter_start_time:.4f}s")

                    if permission_blocked:
                        pending = flush_pending_trust_requests()
                        logging.info(f"[CHAT-NS] Stopping early — permission required")
                        return {"answer": "", "actions": pending, "thinking": ""}

                if full_text.startswith(':'):
                    full_text = full_text[1:].strip()
                logging.info(f"RAW LLM OUTPUT:\n{full_text}")

                inline_thinking, answer_text = _split_thinking_and_answer(full_text)
                thinking_content = external_thinking + inline_thinking
                answer, actions, _ = extract_actions(answer_text) if answer_text else (answer_text, [], "")

                if not thinking_content and not answer:
                    answer = full_text

                if auto_actions:
                    actions.extend(auto_actions)
                actions.extend(flush_pending_trust_requests())
                _postprocess_actions(actions, answer)

                return {"answer": answer, "actions": actions, "thinking": thinking_content}

    except Exception as e:
        logging.error(f"Error in process_chat_request: {e}", exc_info=True)
        answer = f"Error: {e}"
        actions = []
        if 'thinking_content' not in locals():
            thinking_content = ""

    return {"answer": answer, "actions": actions, "thinking": thinking_content}


def _postprocess_actions(actions: list, answer: str):
    """Execute side-effectful actions in-place (open_url, open_app, system_settings, memory)."""
    for act in actions:
        if not isinstance(act, dict):
            continue

        if act.get('type') in ['remember', 'forget']:
            act['type'] = 'status'
            act['status'] = 'success'
            act['content'] = act.get('description') or act.get('fact') or "Memory Updated"

        if act.get('type') == 'link' and any(x in answer.lower() for x in ["opening", "playing", "launching", "here is the video", "here is the trailer"]):
            url = act.get('url', '')
            if any(dom in url for dom in ["youtube.com", "youtu.be", "spotify.com", "vimeo.com"]):
                act['type'] = 'open_url'

        if act.get('type') == 'open_url':
            url = act.get('url', '')
            if url:
                logging.info(f"Opening URL: {url}")
                try:
                    import subprocess as _sp
                    import sys as _sys
                    if _sys.platform.startswith("win"):
                        os.startfile(url)
                    else:
                        _sp.Popen(["xdg-open", url], start_new_session=True)
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

        if act.get('type') == 'system_settings':
            try:
                from src.services.system.macos_settings import SETTING_META, execute_setting
                setting_name = act.get("setting", "")
                meta = SETTING_META.get(setting_name, {})
                act.update(meta)
                if "label" not in act:
                    act["label"] = setting_name.replace("_", " ").title()
                execute_setting(act)
            except Exception as e:
                logging.error(f"Failed to execute system settings: {e}")
