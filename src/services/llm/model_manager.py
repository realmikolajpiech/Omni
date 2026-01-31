import os
import logging
import threading
import requests
from src.core.config import (
    FAST_MODEL_PATH, FAST_MODEL_FILENAME, FAST_MODEL_URL,
    MAIN_MODEL_PATH, MAIN_MODEL_FILENAME, MAIN_MODEL_URL,
    MMPROJ_PATH, MMPROJ_URL, MMPROJ_FILENAME,
    DB_PATH
)

# Global State
llm = None
fast_model = None
embed_model = None
db_conn = None
vision_model = None
init_error = None

# Thread Locks
main_lock = threading.Lock()
fast_lock = threading.Lock()
abort_fast_event = threading.Event()

def download_file(url, dest_path):
    logging.info(f"Downloading {url} to {dest_path}...")
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        logging.info("Download complete.")
        return True
    except Exception as e:
        logging.error(f"Download failed: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

def ensure_imports():
    global init_error
    try:
        from llama_cpp import Llama
        return Llama
    except Exception as e:
        logging.error(f"Import Error: {e}")
        init_error = str(e)
        return None

def ensure_fast_model():
    """Loads the smaller, faster model for actions."""
    global fast_model, init_error
    if fast_model: return

    Llama = ensure_imports()
    if not Llama: return

    if not os.path.exists(FAST_MODEL_PATH):
        logging.info("Fast model not found. Downloading...")
        if not download_file(FAST_MODEL_URL, FAST_MODEL_PATH):
            init_error = f"Failed to download fast model from {FAST_MODEL_URL}"
            logging.error(init_error)
            return

    # Check if we should load (lazy loading optimization)
    # If the user hasn't typed anything yet, maybe we delay?
    # But current logic is "ensure_fast_model" is called by routes.
    
    with fast_lock:
        if fast_model: return
        logging.info(f"Loading Fast Model: {FAST_MODEL_FILENAME}")
        try:
            # Suppress stdout/stderr from llama.cpp
            # This is tricky in python, but we can try setting verbose=False which we already do.
            # We can also redirect C-level stdout if needed, but let's stick to verbose=False.
            
            fast_model = Llama(
                model_path=FAST_MODEL_PATH,
                chat_format="chatml", # Force standard ChatML to bypass Qwen3 thinking
                n_ctx=8192, # Increased context to avoid warnings (model supports 32k)
                n_threads=4,
                n_gpu_layers=-1,
                verbose=False
            )
            logging.info("Fast Model Loaded.")
        except Exception as e:
            logging.error(f"Fast Model Load Error: {e}")
            init_error = str(e)

def ensure_main_model():
    """Loads the larger, main model for chat."""
    global llm, init_error, embed_model, db_conn
    
    # 1. DB & Embeddings (Shared)
    if db_conn is None:
        try:
            if os.path.exists(DB_PATH):
                import lancedb
                db_conn = lancedb.connect(DB_PATH)
                logging.info(f"Connected to LanceDB at {DB_PATH}")
        except Exception as e: logging.error(f"DB Error: {e}")

    if embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            embed_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        except Exception as e: logging.error(f"Embeddings Error: {e}")

    # 2. Main Model
    if llm: return

    Llama = ensure_imports()
    if not Llama: return

    if not os.path.exists(MAIN_MODEL_PATH):
        logging.info("Main model not found. Downloading...")
        if not download_file(MAIN_MODEL_URL, MAIN_MODEL_PATH):
            init_error = "Failed to download main model."
            return

    # MMPROJ handling removed as requested
    
    with main_lock:
        if llm: return
        logging.info(f"Loading Main Model: {MAIN_MODEL_FILENAME}")
        try:
            from llama_cpp import Llama
            
            llm = Llama(
                model_path=MAIN_MODEL_PATH,
                # chat_format="chatml", # Try explicit ChatML if Qwen3 defaults fail
                n_ctx=8192,
                n_threads=4,
                n_gpu_layers=-1,
                verbose=False
            )
            logging.info("Main Model Loaded.")
        except Exception as e:
            logging.error(f"Main Model Load Error: {e}")
            init_error = str(e)

def ensure_model_loaded():
    ensure_fast_model()
    ensure_main_model()
