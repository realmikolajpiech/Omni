import logging
import os
import sys
import time
from src.services.llm import model_manager

def should_search_files(query):
    """Uses Fast Model to decide if we need to search local files."""
    query_lower = query.lower()
    # High priority patterns that imply personal stuff
    personal_patterns = [
        "my", "dreams", "journal", "todo", "todo.txt", "notes", "diary",
        "private", "secrets", "finances", "budget", "personal", "local file",
        "search my", "find my", "on my computer", "on my disk", "in my files",
        "search for", "find code", "how do i", "example of"
    ]
    if any(pattern in query_lower for pattern in personal_patterns):
        logging.info(f"File Search Intent: YES (pattern match) for '{query}'")
        return True
    
    model_manager.ensure_fast_model()
    sys_prompt = (
        "Decide if this query requires searching the user's LOCAL FILES to answer.\n"
        "Output ONLY 'YES' or 'NO'.\n"
        "YES: Questions about 'my dreams', 'my notes', 'my personal files', specific documents on disk, contents of local txt/md/pdf files, code snippets, 'how to' in this project.\n"
        "NO: General Knowledge, current events, math, coding (general), philosophy, greetings.\n"
        "\n"
        "Examples:\n"
        "Query: what are my dreams? -> YES\n"
        "Query: find my notes on biology -> YES\n"
        "Query: how far is the moon -> NO\n"
        "Query: show me the code for the indexer -> YES\n"
        "\n"
        "(If unsure, say NO)."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Query: {query}"}
    ]
    try:
        with model_manager.fast_lock:
            if hasattr(model_manager.fast_model, 'reset'): model_manager.fast_model.reset()
            out = model_manager.fast_model.create_chat_completion(messages=messages, max_tokens=256, temperature=0.0)
        res = out['choices'][0]['message']['content'].strip().upper()
        return "YES" in res
    except: return False

def search_lancedb(query, limit=5):
    """
    Performs semantic search on LanceDB and returns structured results.
    Returns: List of dicts {'path': str, 'score': float, 'type': 'content'|'filename', 'text': str}
    """
    from src.services.llm.model_manager import ensure_main_model, db_conn, embed_model
    ensure_main_model()
    
    if db_conn is None or embed_model is None:
        logging.warning("Semantic Search: DB or Model not ready.")
        return []
        
    results = []
    seen_paths = set()
    
    # 1. Content Search
    try:
        if "file_chunks" in db_conn.table_names():
            chunks_tbl = db_conn.open_table("file_chunks")
            query_vec = embed_model.encode(query)
            res_chunks = chunks_tbl.search(query_vec).limit(limit).to_pandas()
            
            if not res_chunks.empty:
                for _, row in res_chunks.iterrows():
                    path = row['path']
                    if path in seen_paths: continue
                    
                    results.append({
                        'path': path,
                        'score': 1.0 - row.get('_distance', 0.5), # Convert distance to similarity roughly
                        'type': 'content',
                        'text': row['content']
                    })
                    seen_paths.add(path)
    except Exception as e:
        logging.error(f"Semantic content search failed: {e}")
        
    # 2. Filename Search
    try:
        if "files" in db_conn.table_names():
            files_tbl = db_conn.open_table("files")
            # Re-encode only if needed, but we can reuse query_vec if we passed it (not passed here so re-encode)
            # Actually embed_model cache might handle it, or just re-run
            res_files = files_tbl.search(embed_model.encode(query)).limit(limit).to_pandas()
            
            if not res_files.empty:
                for _, row in res_files.iterrows():
                    path = row['path']
                    if path in seen_paths: continue
                    
                    results.append({
                        'path': path,
                        'score': 1.0 - row.get('_distance', 0.5),
                        'type': 'filename',
                        'text': f"Filename match: {os.path.basename(path)}"
                    })
                    seen_paths.add(path)
    except Exception as e:
        logging.error(f"Semantic filename search failed: {e}")
        
    return results

def perform_file_search(query):
    """Searches LanceDB (Content & Filenames) and returns context."""
    # Ensure connection is ready (using main_model ensures embed_model is loaded)
    from src.services.llm.model_manager import ensure_main_model, db_conn, embed_model
    
    # ... (Keep existing logging/checks) ...
    ensure_main_model()
    if db_conn is None or embed_model is None:
        return "Search System Not Ready."

    logging.info(f"Performing File Search for: '{query}'")
    
    file_contexts = []
    
    # USE NEW FUNCTION
    semantic_results = search_lancedb(query, limit=5)
    
    for res in semantic_results:
        path = res['path']
        content = res['text']
        match_type = res['type']
        
        # For filename matches, we might want to read the content like before
        if match_type == 'filename':
             # Only read text-like files
            from src.services.search.utils import is_text_file, process_file_content
            if is_text_file(path) and "/node_modules/" not in path:
                try:
                    chunks = process_file_content(path, chunk_size=1000)
                    file_content = chunks[0] if chunks else ""
                    if file_content:
                        content = file_content
                except: pass
        
        file_contexts.append(f"--- File: {path} ({match_type.capitalize()} Match) ---\n{content}\n...")
    
    # 3. Fallback: OS-level search (mdfind/locate) if nothing found
    if not file_contexts:
        logging.info("LanceDB returned no results. Attempting fallback OS search...")
        try:
            import subprocess
            import os
            from src.services.search.utils import is_text_file, process_file_content
            
            # Simple keyword extraction (naive)
            # Remove "search", "find", "my" etc.
            keywords = query.replace("search", "").replace("find", "").replace("my", "").strip()
            if keywords and len(keywords) > 2:
                # Use mdfind on macOS
                if sys.platform == 'darwin':
                    cmd = ['mdfind', '-name', keywords]
                    # Limit to 3 results
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                    if proc.returncode == 0:
                        paths = proc.stdout.strip().split('\n')[:3]
                        for path in paths:
                            if not path or not os.path.exists(path): continue
                            if os.path.isdir(path): continue
                            if "/Library/" in path or "/." in path: continue # Skip system/hidden
                            
                            if is_text_file(path):
                                try:
                                    chunks = process_file_content(path, chunk_size=1000)
                                    content = chunks[0] if chunks else ""
                                    if content:
                                        file_contexts.append(f"--- File: {path} (OS Search Match) ---\n{content}\n...")
                                except: pass
        except Exception as e:
            logging.error(f"Fallback search failed: {e}")

    result_text = "\n\n".join(file_contexts) if file_contexts else "No relevant files found."
    logging.info(f"Final File Context Length: {len(result_text)} chars")
    return result_text
