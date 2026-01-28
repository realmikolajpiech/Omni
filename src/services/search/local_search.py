import logging
import sys
import time
from src.services.llm.model_manager import ensure_main_model, ensure_fast_model, fast_model, fast_lock, db_conn, embed_model

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
        return "YES" in res
    except: return False

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
