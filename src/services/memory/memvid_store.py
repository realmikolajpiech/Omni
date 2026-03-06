
import os
import logging
import memvid_sdk
import datetime
import time
import hashlib
from src.core.config import PERSONAL_MEM_PATH
from src.services.llm.model_manager import llm, main_lock
from src.services.memory.embedding import embed_text

personal_mem = None

# Memory cache to avoid re-resolving identical facts every request
_memory_cache = {"result": None, "timestamp": 0, "facts_hash": None}
_MEMORY_CACHE_TTL = 60  # seconds


def invalidate_memory_cache():
    """Invalidate the memory cache (called after writes)."""
    _memory_cache["result"] = None
    _memory_cache["timestamp"] = 0
    _memory_cache["facts_hash"] = None
    logging.info("[MEMORY] Cache invalidated")


def _trigger_sync():
    """Trigger a background memory sync after a write."""
    try:
        from src.services.sync import memory_sync
        memory_sync.sync_now()
    except Exception:
        pass


def get_memvid_instance():
    """Lazily initializes and returns the Memvid instance."""
    global personal_mem
    if personal_mem:
        return personal_mem

    try:
        # Check if file exists to decide between create and use
        if not os.path.exists(PERSONAL_MEM_PATH):
            logging.info(f"Creating new Memvid memory at {PERSONAL_MEM_PATH}")
            # Ensure directory exists
            os.makedirs(os.path.dirname(PERSONAL_MEM_PATH), exist_ok=True)
            # Create new memory with vector search enabled
            personal_mem = memvid_sdk.create(PERSONAL_MEM_PATH, enable_vec=True)
        else:
            logging.info(f"Loading existing Memvid memory from {PERSONAL_MEM_PATH}")
            # Use existing memory with vector search enabled
            personal_mem = memvid_sdk.use('basic', PERSONAL_MEM_PATH, enable_vec=True)

        return personal_mem
    except Exception as e:
        logging.error(f"Failed to initialize Memvid: {e}")
        return None

def resolve_contradictions(facts):
    """Uses Fast Model to resolve conflicting facts (was Main Model — too slow)."""
    if len(facts) <= 1: return "\n".join(facts)

    unique_facts = list(set(facts))

    # If all facts are already unique and few, skip LLM entirely
    if len(unique_facts) <= 3:
        # Quick check: no "FACT DELETED" markers that need processing
        has_deletions = any("FACT DELETED:" in f for f in unique_facts)
        if not has_deletions:
            logging.info(f"[MEMORY] Skipping LLM resolution — {len(unique_facts)} unique facts, no conflicts")
            return "\n".join([f"- {f}" for f in unique_facts])

    # Check cache
    facts_hash = hashlib.md5("||".join(sorted(unique_facts)).encode()).hexdigest()
    if (_memory_cache["facts_hash"] == facts_hash
            and _memory_cache["result"]
            and time.time() - _memory_cache["timestamp"] < _MEMORY_CACHE_TTL):
        logging.info("[MEMORY] Cache hit for resolve_contradictions")
        return _memory_cache["result"]

    facts_text = "\n".join([f"- {f}" for f in unique_facts])
    logging.info(f"Resolving Contradictions for:\n{facts_text}")

    prompt = f"""You are a Fact Resolver. The following list contains facts about a user. duplicates or contradictions may exist.
Task:
1. Identify contradictions.
2. Resolve them by trusting NEGATIVE assertions or EXPLICIT UPDATES over older positive ones.
3. If a fact says "FACT DELETED: X", it means X is FALSE and REMOVED. Do NOT include X in the output.
4. Remove duplicates.
5. REMOVE metadata artifacts like "title: Untitled", "title: ...", or "tags: ...". If a fact is ONLY metadata, remove it.
6. Output specific, singular, consistent facts.
7. NO conversational text. Output ONLY the facts.

Facts:
{facts_text}

resolved facts:"""

    try:
        from src.services.llm import model_manager
        from src.services.llm.model_manager import ensure_fast_model, fast_lock

        ensure_fast_model()
        if not model_manager.fast_model:
            return "\n".join(unique_facts)

        with fast_lock:
            if hasattr(model_manager.fast_model, 'reset'):
                model_manager.fast_model.reset()
            start_t = time.time()
            out = model_manager.fast_model.create_chat_completion(
                messages=[
                    {"role": "system", "content": "You are a logical consistency engine. Output ONLY the resolved facts list."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=256,
                temperature=0.0,
                chat_template_kwargs={"enable_thinking": False},
            )
            dur = time.time() - start_t
            logging.info(f"[MEMORY] Fact resolution via fast model in {dur:.2f}s")

        if out is None:
            return "\n".join(unique_facts)

        import re
        cleaned = out['choices'][0]['message']['content'].strip()
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
        logging.info(f"Resolved Facts Output:\n{cleaned}")

        # Update cache
        _memory_cache["result"] = cleaned
        _memory_cache["timestamp"] = time.time()
        _memory_cache["facts_hash"] = facts_hash

        return cleaned
    except Exception as e:
        logging.error(f"Fact Resolution Failed: {e}")
        return "\n".join(unique_facts)

def get_user_memory(query=None):
    """Retrieves relevant user memory from Memvid V2."""
    mem = get_memvid_instance()
    if not mem:
        return "No personal memory available."

    if not query or any(x in query.lower() for x in ["everything", "all information", "know about me", "who am i"]):
        # If no query or broad query, search for 'user' to get general facts
        try:
            logging.info("Broad memory query detected, searching for 'user' facts.")
            results = mem.find("user", k=10)
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

        # Disable embedding generation for real-time query to fix 3.5s delay
        # using lexical search instead.
        query_vec = None # embed_text(query)

        # Use find with query_embedding if available
        if query_vec:
            raw_results = mem.find(query, k=5, query_embedding=query_vec)
        else:
            raw_results = mem.find(query, k=10) # increase k slightly to compensate for lexical search

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
                            dt = datetime.datetime.fromtimestamp(ts)
                            date_str = f"[{dt.strftime('%Y-%m-%d')}] "
                        elif isinstance(ts, str):
                            # Try parsing basic ISO or just take first 10 chars
                            date_str = f"[{ts[:10]}] "
                    except: pass

                if text:
                    # Memvid snippets sometimes look like "The user's name is... \ntitle: ... \ntags: ..."
                    clean_text = text.split('\ntitle:')[0].split('\ntext:')[0].strip()
                    # Extra cleanup for inline title: artifacts
                    if " title: Untitled" in clean_text:
                        clean_text = clean_text.replace(" title: Untitled", "").strip()
                    if clean_text.endswith(" title:"):
                        clean_text = clean_text[:-7].strip()

                    facts.append(f"{date_str}{clean_text}")
            elif isinstance(h, str):
                facts.append(h)

        if facts:
            return resolve_contradictions(facts)

        # Fallback: if specific search failed, try general user search
        logging.info("Specific search yielded no results, falling back to general user search.")
        fallback_res = mem.find("user", k=5)
        f_hits = fallback_res.get('hits', []) if isinstance(fallback_res, dict) else []
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
    mem = get_memvid_instance()
    if not mem:
        return False

    try:
        # Deduplication: Check if this fact (or something very similar) is already known
        logging.info(f"Checking if fact is already known: {fact}")
        search_res = mem.find(fact, k=3)
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

        # Generate embedding manually
        vec = embed_text(fact)

        if vec:
            # Use put_many to inject manual embedding
            # mem.put_many takes a list of dicts and an optional list of embeddings
            item = {
                "title": "Untitled",
                "label": "text",
                "text": fact,
                "auto_tag": True,
                "extract_dates": True
            }
            # Note: put_many returns list of frame_ids
            mem.put_many([item], embeddings=[vec])
        else:
            # Fallback to lexical only if embedding fails
            mem.put(text=fact, enable_embedding=False)

        invalidate_memory_cache()
        _trigger_sync()
        return True
    except Exception as e:
        logging.error(f"Failed to remember fact: {e}")
        return False

def remember_update(fact):
    """Corrects an existing fact in Memvid V2."""
    mem = get_memvid_instance()
    if not mem:
        return False

    try:
        logging.info(f"Correcting Fact: {fact}")
        mem.correct(statement=fact, boost=3.0)
        invalidate_memory_cache()
        _trigger_sync()
        return True
    except Exception as e:
        logging.error(f"Failed to correct fact: {e}")
        return False

def delete_memory(query):
    """Deletes (hides) a fact from Memvid V2 based on semantic query."""
    mem = get_memvid_instance()
    if not mem:
        return False

    try:
        logging.info(f"Attempting to delete memory matching: {query}")
        # Search for the fact
        search_res = mem.find(query, k=5)
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
                    mem.correct(f"FACT DELETED: {clean_text}", boost=5.0)
                    deleted_count += 1
                except Exception as e:
                    logging.error(f"Correction failed: {e}")

        if deleted_count > 0:
            invalidate_memory_cache()
            _trigger_sync()
        return deleted_count > 0
    except Exception as e:
        logging.error(f"Failed to delete memory: {e}")
        return False
