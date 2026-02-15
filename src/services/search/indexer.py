import os
import lancedb
from sentence_transformers import SentenceTransformer
from PIL import Image
import logging
import sys
import torch
import gc

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.core.config import DB_PATH, HOME
from src.services.search.utils import process_file_content, is_text_file

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TABLE_NAME = "files"

# Directories to ignore
IGNORE_DIRS = {
    ".cache", ".git", ".npm", "node_modules", ".node_modules", "venv", ".venv", "__pycache__",
    ".local", ".config", ".mozilla", ".thunderbird", "anaconda3", ".anaconda3",
    "Downloads", "Music", "Videos", "go", ".cargo", ".rustup", "Library",
    ".gemini", ".antigravity", ".vscode", ".idea", "target", "build", "dist"
}

# Block specific extensions that are purely internal/developer noise
BLOCKED_EXTENSIONS = {
    ".pyi", ".pyc", ".pyo", ".pyd", ".o", ".so", ".dll", ".dylib", ".a", ".lib",
    ".class", ".jar", ".war", ".ear", ".min.js", ".min.css", ".map", ".log",
    ".tmp", ".temp", ".bak", ".swp", ".swo", ".ds_store", ".thumbs", ".db"
}

def main():
    # 1. Collect files (Streamed approach to save RAM)
    # Instead of collecting all files at once, we'll iterate
    
    # 2. Generate Embeddings for Filenames
    logging.info("Loading embedding model...")
    
    # Use Centralized Model Manager for consistency
    import src.services.llm.model_manager as model_manager
    
    # Ensure model is loaded
    model_manager.ensure_main_model()
    
    # Wait for embed_model to be populated
    if model_manager.embed_model is None:
        logging.error("Failed to load embedding model.")
        return
        
    model = model_manager.embed_model
    
    logging.info("Connecting to LanceDB at {DB_PATH}...")
    db = lancedb.connect(DB_PATH)
    
    # --- FILENAME INDEXING (Batched) ---
    logging.info("Indexing filenames (batched)...")
    
    # Re-scan for filenames but process in batches
    BATCH_SIZE = 128 # Reduced for safety with larger models
    current_batch = []
    
    # Initialize table
    try:
        db.drop_table(TABLE_NAME)
    except: pass
    table = None
    
    total_files = 0
    
    # We need a generator for files to avoid loading all into memory
    def file_generator(base_dir):
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
            for file in files:
                if file.startswith("."): continue
                _, ext = os.path.splitext(file)
                if ext.lower() in BLOCKED_EXTENSIONS: continue
                if not ext: continue
                yield os.path.join(root, file), file

    for full_path, filename in file_generator(HOME):
        current_batch.append({"filename": filename, "path": full_path})
        
        if len(current_batch) >= BATCH_SIZE:
            # Encode filenames
            names = [x['filename'] for x in current_batch]
            vectors = model.encode(names, batch_size=BATCH_SIZE, show_progress_bar=False)
            
            data = []
            for i, item in enumerate(current_batch):
                data.append({
                    "vector": vectors[i].tolist(),
                    "filename": item['filename'],
                    "path": item['path']
                })
                
            if table is None:
                table = db.create_table(TABLE_NAME, data=data)
            else:
                table.add(data)
                
            total_files += len(data)
            current_batch = []
            
            # Cleanup
            del vectors
            del data
            gc.collect()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()

    # Final filename batch
    if current_batch:
        names = [x['filename'] for x in current_batch]
        vectors = model.encode(names, batch_size=BATCH_SIZE, show_progress_bar=False)
        data = []
        for i, item in enumerate(current_batch):
            data.append({
                "vector": vectors[i].tolist(),
                "filename": item['filename'],
                "path": item['path']
            })
        if table is None:
            table = db.create_table(TABLE_NAME, data=data)
        else:
            table.add(data)
        total_files += len(data)
        del vectors
        del data
        gc.collect()
        
    logging.info(f"Filename indexing complete. Total files: {total_files}")
    
    # --- CONTENT INDEXING ---
    logging.info("Scanning for text files to index content...")
    
    CHUNKS_TABLE = "file_chunks"
    try:
        db.drop_table(CHUNKS_TABLE)
    except: pass
    chunk_table = None
    
    BATCH_SIZE = 16 # Reduced to 16 to keep memory low with BAAI/bge-m3 (1024 dim)
    current_batch_chunks = []
    current_batch_metadata = []
    total_chunks_indexed = 0
    
    # Reuse generator for content
    for full_path, filename in file_generator(HOME):
        if not is_text_file(full_path): continue
        
        try:
            # Check is handled in process_file_content
            # if os.path.getsize(full_path) > 500 * 1024: continue
                
            chunks = process_file_content(full_path, chunk_size=512)
            if not chunks: continue
            
            for i, chunk in enumerate(chunks):
                if len(chunk) > 512: chunk = chunk[:512]
                    
                current_batch_chunks.append(chunk)
                current_batch_metadata.append({
                    "filename": filename,
                    "path": full_path,
                    "chunk_id": i,
                    "content": chunk
                })
                
                if len(current_batch_chunks) >= BATCH_SIZE:
                    vectors = model.encode(current_batch_chunks, batch_size=BATCH_SIZE, show_progress_bar=False)
                    batch_data = []
                    for idx, meta in enumerate(current_batch_metadata):
                        batch_data.append({
                            "vector": vectors[idx].tolist(),
                            "filename": meta['filename'],
                            "path": meta['path'],
                            "chunk_id": meta['chunk_id'],
                            "content": meta['content']
                        })
                    
                    if chunk_table is None:
                        chunk_table = db.create_table(CHUNKS_TABLE, data=batch_data)
                    else:
                        chunk_table.add(batch_data)
                        
                    total_chunks_indexed += len(batch_data)
                    if total_chunks_indexed % 1000 == 0:
                        logging.info(f"Indexed {total_chunks_indexed} chunks...")
                    
                    current_batch_chunks = []
                    current_batch_metadata = []
                    del vectors
                    del batch_data
                    gc.collect()
                    if torch.backends.mps.is_available():
                        torch.mps.empty_cache()
                        
        except Exception as e:
            # logging.error(f"Error processing file {full_path}: {e}")
            pass

    # Final content batch
    if current_batch_chunks:
        vectors = model.encode(current_batch_chunks, batch_size=BATCH_SIZE, show_progress_bar=False)
        batch_data = []
        for idx, meta in enumerate(current_batch_metadata):
            batch_data.append({
                "vector": vectors[idx].tolist(),
                "filename": meta['filename'],
                "path": meta['path'],
                "chunk_id": meta['chunk_id'],
                "content": meta['content']
            })
        if chunk_table is None:
            chunk_table = db.create_table(CHUNKS_TABLE, data=batch_data)
        else:
            chunk_table.add(batch_data)
        total_chunks_indexed += len(batch_data)
        del vectors
        del batch_data
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    logging.info(f"Content indexing complete. Total chunks: {total_chunks_indexed}")
    
    # --- IMAGE INDEXING (Skipped for brevity/focus on RAM issue) ---
    # Can be re-added with similar generator pattern if needed
    logging.info("Indexing complete!")

if __name__ == "__main__":
    main()
