import logging
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

def perform_file_search(query):
    """Searches LanceDB (Content & Filenames) and returns context."""
    # Ensure connection is ready (using main_model ensures embed_model is loaded)
    from src.services.llm.model_manager import ensure_main_model, db_conn, embed_model
    ensure_main_model()
    
    if db_conn is None or embed_model is None:
        logging.error("Database or Embed Model not initialized for file search.")
        return "Search System Not Ready."

    logging.info(f"Performing File Search for: '{query}'")
    
    file_contexts = []
    seen_paths = set()
    
    # 1. Search CONTENT (file_chunks) - "Search by Meaning"
    try:
        # Check if table exists
        existing_tables = db_conn.table_names()
        if "file_chunks" in existing_tables:
            chunks_tbl = db_conn.open_table("file_chunks")
            
            # Search for query embedding
            query_vec = embed_model.encode(query)
            # Use limit 5 and print scores
            res_chunks = chunks_tbl.search(query_vec).limit(5).to_pandas()
            
            if not res_chunks.empty:
                logging.info(f"Found {len(res_chunks)} content matches.")
                for _, row in res_chunks.iterrows():
                    path = row['path']
                    content = row['content']
                    score = row.get('_distance', 0) # LanceDB returns distance (lower is better)
                    
                    logging.info(f" - Content Match: {path} (dist={score:.4f})")
                    
                    # Threshold check (LanceDB default is L2 distance, so < 1.0 is usually good for normalized vectors)
                    # But let's be lenient for now to debug
                    
                    seen_paths.add(path)
                    file_contexts.append(f"--- File: {path} (Content Match) ---\n{content}\n...")
            else:
                logging.info("No content matches found in 'file_chunks'.")
        else:
            logging.warning("'file_chunks' table does not exist yet.")
            
    except Exception as e:
        logging.error(f"Content search failed: {e}")

    # 2. Search FILENAMES (files)
    try:
        existing_tables = db_conn.table_names()
        if "files" in existing_tables:
            tbl = db_conn.open_table("files")
            res = tbl.search(embed_model.encode(query)).limit(5).to_pandas()
            
            if not res.empty:
                logging.info(f"Found {len(res)} filename matches.")
                for _, row in res.iterrows():
                    path = row['path']
                    score = row.get('_distance', 0)
                    
                    logging.info(f" - Filename Match: {path} (dist={score:.4f})")
                    
                    # Skip if we already found content for this file
                    if path in seen_paths: continue
                    
                    # Filter out noise directories
                    if "/examples/" in path or "/node_modules/" in path or "/venv/" in path or "/.git/" in path:
                        continue

                    # Only read text-like files (updated list to include RTF)
                    # Reuse is_text_file from utils which now includes empty extensions
                    from src.services.search.utils import is_text_file
                    if is_text_file(path):
                        try:
                            # Use utils to process content (handles RTF stripping)
                            from src.services.search.utils import process_file_content
                            # Just get first chunk
                            chunks = process_file_content(path, chunk_size=1000)
                            content = chunks[0] if chunks else ""
                            
                            if content:
                                file_contexts.append(f"--- File: {path} (Filename Match) ---\n{content}\n...")
                        except Exception as e:
                            logging.warning(f"Failed to read file {path}: {e}")
            else:
                logging.info("No filename matches found in 'files'.")
        else:
             logging.warning("'files' table does not exist yet.")

    except Exception as e:
        logging.error(f"Filename search failed: {e}")
    
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
