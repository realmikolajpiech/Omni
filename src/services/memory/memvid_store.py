import os
import logging
import memvid_sdk
import datetime
from src.core.config import PERSONAL_MEM_PATH
from src.services.llm.model_manager import llm, main_lock

personal_mem = None

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
             if not llm:
                 return "\n".join(unique_facts)
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
                            dt = datetime.datetime.fromtimestamp(ts)
                            date_str = f"[{dt.strftime('%Y-%m-%d')}] "
                        elif isinstance(ts, str):
                            # Try parsing basic ISO or just take first 10 chars
                            date_str = f"[{ts[:10]}] "
                    except: pass

                if text:
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
