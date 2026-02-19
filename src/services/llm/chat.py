import logging
import json
import re
import time
import os
import subprocess
from simpleeval import SimpleEval
from flask import jsonify

from src.services.llm import model_manager
from src.services.llm.model_manager import ensure_main_model, ensure_fast_model, fast_lock, main_lock, abort_fast_event
from src.services.search.web_search import perform_web_search
from src.services.search.local_search import perform_file_search, should_search_files
from src.services.search.image_search import perform_image_search_with_fallback, should_search_images
from src.services.memory.memvid_store import get_user_memory, remember_fact, remember_update, delete_memory
from src.services.system.location import get_ip_location
from src.services.system.app_launcher import get_app_cache, find_and_launch_app
from src.core.grid_locator import localize_target_from_b64

def perform_calculation(expression):
    try:
        lower_input = expression.lower()
        for prefix in ["calculate ", "what is ", "solve "]:
            if lower_input.startswith(prefix):
                expression = expression[len(prefix):]
        s = SimpleEval()
        result = s.eval(expression)
        # Return both the result and a LaTeX formatted version of the equation
        latex_expr = expression.replace("*", " \\cdot ").replace("/", " \\div ").replace("^", "^{").replace("**", "^{")
        # Add closing brace for powers if needed
        if "^{" in latex_expr and latex_expr.count("{") > latex_expr.count("}"):
            latex_expr += "}"
        return (f"Expression: {expression}\nResult: {result}\nLaTeX: ${latex_expr} = {result}$")
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
            logging.warning(f"Failed to parse JSON block: {e}")

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

def process_chat_request(query, history, screenshot_b64=None, stream=False):
    import sys # Ensure sys is available
    abort_fast_event.set()
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

    context_text = ""
    source_type = "None"
    
    # Initialize thinking_content early to avoid UnboundLocalError
    thinking_content = ""
    answer = ""
    actions = []

    if any(x in query for x in ["+", "*", "/", "sqrt"]) and any(c.isdigit() for c in query):
         source_type = "Calculator"
         context_text = f"--- Calculation Result ---\n{perform_calculation(query)}\n"
    elif should_search_files(query):
         source_type = "Local Files"
         context_text = f"--- Local File Context ---\n{perform_file_search(query)}\n"

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
        
        # === LOGGING: Fast Model extraction start ===
        logging.info("[CHAT] Starting Fast Model memory extraction...")
        
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
                         auto_actions.append({"type": "remember", "fact": fact_to_save, "description": f"Remembered: {fact_to_save}"})
                 elif "UP:" in f_res:
                     fact_to_save = f_res.split("UP:")[1].strip()
                     if remember_update(fact_to_save):
                         auto_actions.append({"type": "remember", "fact": fact_to_save, "description": f"Updated: {fact_to_save}"})
                 elif "FO:" in f_res:
                     fact_to_forget = f_res.split("FO:")[1].strip()
                     if delete_memory(fact_to_forget):
                         auto_actions.append({"type": "forget", "fact": fact_to_forget, "description": f"Forgot: {fact_to_forget}"})
             else:
                 logging.warning("Memory Extraction: No response from fast_model.")

    except Exception as e: logging.error(f"Extraction Error: {e}")

    # PRIORITIES:
    
    # PRIORITY 0: SCREENSHOT (Highest)
    if screenshot_b64:
        source_type = "User Screen"
        logging.info("Processing with Screenshot Context")
    
    # PRIORITY 1: Web Search
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

    # === LOGGING: Context determined ===
    logging.info(f"[CHAT] Context determination finished. Source: {source_type}")

    user_loc = get_ip_location()
    user_personal_context = get_user_memory(query)
    
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

    system_prompt = f"""You are Omni — Mikołaj's personal AI companion running on macOS.
Personality: casual, direct, loyal friend. No formalities. Act first, explain briefly.

## Mikołaj
{user_personal_context}
Location: {user_loc} | Date: {current_date}

## Context data
{context_text.strip() if context_text.strip() else "(none)"}

---
## ACTIONS — output a ```json``` block for every action

**Open application**
{{"type": "open_app", "name": "google-chrome"}}
→ "browser"/"chrome" → "google-chrome". Respond in ≤3 words: "Opening Chrome." No questions, no choices.

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

**Computer control** (only when Mikołaj explicitly says click/type/scroll/press)
{{"type": "computer_control", "action": "type", "text": "hello world", "description": "typing text"}}
{{"type": "computer_control", "action": "scroll", "direction": "down", "description": "scrolling"}}
→ Click coordinates are handled automatically by the grid system. Just say "Clicking X."
→ NEVER use action "describe" — write the description in your text response instead.

---
## Rules
- Screenshot provided → you're seeing Mikołaj's screen right now. Don't ask to navigate anywhere.
- Never invent URLs. Only use links from Context data.
- "Describe screen" / "What do you see?" → text answer only, no computer_control action.
- For any command: just DO it. Never ask "would you like me to…?" — act immediately.
- If Mikołaj shares a new fact about himself, acknowledge it naturally.
- For questions about battery, CPU, system info — honestly say you can't access that right now.
- Always emit valid JSON in a ```json``` block for actions.
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

    try:
        abort_fast_event.clear()
        with main_lock:
            # === LOGGING: Main Lock Acquired ===
            logging.info("[CHAT] Main lock acquired. Starting generation...")
            
            if hasattr(model_manager.llm, 'reset'): model_manager.llm.reset()

            if stream:
                # Real streaming mode - get tokens as they're generated
                streamer = model_manager.llm.create_chat_completion(
                    messages=messages,
                    max_tokens=1536,
                    temperature=0.6,
                    stream=True,
                )

                # Yield tokens as they arrive
                accumulated_text = ""
                external_thinking = ""
                for chunk in streamer:
                    # Check abortion
                    if model_manager.abort_fast_event.is_set():
                        logging.info("Chat Request Aborted during streaming.")
                        break

                    # Handle both raw strings and ChatCompletionChunk objects
                    if hasattr(chunk, 'choices') and chunk.choices:
                        delta = chunk.choices[0].delta
                        
                        # Handle content
                        if hasattr(delta, 'content') and delta.content:
                            token = delta.content
                        else:
                            token = ""
                            
                        # Handle reasoning_content (DeepSeek/Qwen style)
                        reasoning_token = ""
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning_token = delta.reasoning_content
                        elif hasattr(delta, 'model_extra') and delta.model_extra and 'reasoning_content' in delta.model_extra:
                            reasoning_token = delta.model_extra['reasoning_content']
                            
                        if reasoning_token:
                            external_thinking += reasoning_token
                            
                    else:
                        # Fallback for simple string streaming or dictionary
                        token = str(chunk) if chunk else ""
                        reasoning_token = ""
                        if isinstance(chunk, dict):
                            delta_dict = chunk.get('choices', [{}])[0].get('delta', {})
                            token = delta_dict.get('content', '')
                            reasoning_token = delta_dict.get('reasoning_content', '')
                            if reasoning_token:
                                external_thinking += reasoning_token

                    accumulated_text += token
                    # Log streaming chunks so logs show raw response (last 100 chars per chunk)
                    if accumulated_text:
                        tail = accumulated_text[-100:] if len(accumulated_text) > 100 else accumulated_text
                        # logging.info(f"[STREAM] raw chunk: ...{tail!r}")
                        
                    # Split into thinking (collapsible, gray) and answer (main text) for UI
                    inline_thinking, answer_so_far = _split_thinking_and_answer(accumulated_text)
                    
                    # Combine external (field) and inline (tag) thinking
                    combined_thinking = external_thinking + inline_thinking
                    
                    # Yield partial whenever we have thinking or answer (so UI can stream both)
                    if combined_thinking or answer_so_far:
                        yield ("partial", {"thinking": combined_thinking, "answer": answer_so_far})

                # Final processing: use the split result and then extract actions from answer only
                inline_thinking, answer_text = _split_thinking_and_answer(accumulated_text)
                thinking_content = external_thinking + inline_thinking
                
                # Now extract actions/JSON from the answer text only (not from thinking)
                answer, actions, _ = extract_actions(answer_text) if answer_text else (answer_text, [], "")
                logging.info(f"[STREAM] final raw length={len(accumulated_text)}, thinking length={len(thinking_content)}, answer length={len(answer)}, actions count={len(actions)}")
                yield ("final", {"answer": answer, "actions": actions, "thinking": thinking_content})
            else:
                # Non-streaming mode (original behavior)
                output = model_manager.llm.create_chat_completion(
                    messages=messages,
                    max_tokens=1536,
                    temperature=0.6,
                )
                msg = output['choices'][0]['message']
                full_text = msg['content'].strip()
                
                # Extract reasoning_content if present
                external_thinking = msg.get('reasoning_content', '') or ""
                if not isinstance(external_thinking, str): external_thinking = ""
                
                if full_text.startswith(':'): full_text = full_text[1:].strip()
                logging.info(f"RAW LLM OUTPUT:\n{full_text}")

                # Use the same splitting logic for consistency
                inline_thinking, answer_text = _split_thinking_and_answer(full_text)
                thinking_content = external_thinking + inline_thinking
                
                # Extract actions from answer only (not from thinking)
                answer, actions, _ = extract_actions(answer_text) if answer_text else (answer_text, [], "")

                if not thinking_content and not answer:
                    answer = full_text

                return {"answer": answer, "actions": actions, "thinking": thinking_content}

        # VALIDATION: Filter out hallucinated computer_control actions
        valid_actions = []
        for act in actions:
            if isinstance(act, dict) and act.get('type') == 'computer_control':
                cmd = act.get('action')
                if not cmd or cmd == 'computer_control':
                    desc = act.get('description')
                    if desc and (not answer or len(answer) < 5):
                            answer = desc
                    continue 
            valid_actions.append(act)
        actions = valid_actions

    except Exception as e: 
        logging.error(f"Error in ask_llm: {e}")
        answer = f"Error: {e}"
        actions = []
        # Ensure thinking_content is defined even in error case
        if 'thinking_content' not in locals():
            thinking_content = ""

    if auto_actions: actions.extend(auto_actions)

    # Convert internal memory actions to UI-visible Status cards
    for act in actions:
        if not isinstance(act, dict): continue
        if act.get('type') in ['remember', 'forget']:
            act['type'] = 'status'
            act['status'] = 'success'
            act['content'] = act.get('description') or act.get('fact') or "Memory Updated"

    # PROCESS LOCAL APP LAUNCHES and OPEN_URL
    for act in actions:
        if not isinstance(act, dict): continue
        
        if act.get('type') == 'link' and any(x in answer.lower() for x in ["opening", "playing", "launching", "here is the video", "here is the trailer"]):
             url = act.get('url', '')
             if any(dom in url for dom in ["youtube.com", "youtu.be", "spotify.com", "vimeo.com"]):
                 act['type'] = 'open_url'

        if act.get('type') == 'open_url':
             url = act.get('url', '')
             if url:
                 logging.info(f"Opening URL: {url}")
                 try:
                     import subprocess
                     # Check platform
                     if sys.platform.startswith("win"):
                        os.startfile(url)
                     else:
                        subprocess.Popen(["xdg-open", url], start_new_session=True)
                     
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

        if act.get('type') == 'terminal_command':
            cmd = act.get('command', '').strip()
            if cmd:
                try:
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True, timeout=15
                    )
                    act['stdout'] = result.stdout.strip()
                    act['stderr'] = result.stderr.strip()
                    act['returncode'] = result.returncode
                    act['success'] = result.returncode == 0
                    logging.info(
                        f"[terminal] cmd={cmd!r} rc={result.returncode} "
                        f"out={result.stdout[:120]!r} err={result.stderr[:80]!r}"
                    )
                except subprocess.TimeoutExpired:
                    act['success'] = False
                    act['stdout'] = ''
                    act['stderr'] = 'Command timed out (15s)'
                    logging.warning(f"[terminal] timed out: {cmd!r}")
                except Exception as e:
                    act['success'] = False
                    act['stdout'] = ''
                    act['stderr'] = str(e)
                    logging.error(f"[terminal] error: {e}")

    return {"answer": answer, "actions": actions, "thinking": thinking_content}
