import os
import lancedb
from PIL import Image
import logging
import sys
import gc
import json
import time
import urllib.request
import urllib.error

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.core.config import DB_PATH, HOME, BRAIN_HOST, BRAIN_PORT
from src.services.search.utils import process_file_content, is_text_file

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TABLE_NAME = "files"
EMBED_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/embed"

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


def _wait_for_brain(timeout: int = 60) -> bool:
    """Poll the brain's /health endpoint until it responds or timeout expires."""
    health_url = f"http://{BRAIN_HOST}:{BRAIN_PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _remote_encode(texts: list) -> list:
    """Call the brain's /embed endpoint and return a list of float vectors."""
    payload = json.dumps({"texts": texts}).encode("utf-8")
    req = urllib.request.Request(
        EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["vectors"]


def main():
    logging.info(f"Waiting for brain service at {EMBED_URL}...")
    if not _wait_for_brain(timeout=60):
        logging.error("Brain service did not become available in time. Exiting.")
        sys.exit(1)
    logging.info("Brain service is up. Starting indexing.")

    logging.info("Connecting to LanceDB at {DB_PATH}...")
    db = lancedb.connect(DB_PATH)

    # --- FILENAME INDEXING (Batched) ---
    logging.info("Indexing filenames (batched)...")

    BATCH_SIZE = 128
    current_batch = []

    try:
        db.drop_table(TABLE_NAME)
    except Exception as e:
        logging.warning(f"Could not drop table {TABLE_NAME}: {e}")
    table = None

    total_files = 0

    def file_generator(base_dir):
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
            for file in files:
                if file.startswith("."): continue
                _, ext = os.path.splitext(file)
                if ext.lower() in BLOCKED_EXTENSIONS: continue
                yield os.path.join(root, file), file

    for full_path, filename in file_generator(HOME):
        current_batch.append({"filename": filename, "path": full_path})

        if len(current_batch) >= BATCH_SIZE:
            names = [x['filename'] for x in current_batch]
            try:
                vectors = _remote_encode(names)
            except Exception as e:
                logging.error(f"Remote encode failed: {e}")
                current_batch = []
                continue

            data = [
                {"vector": vectors[i], "filename": item['filename'], "path": item['path']}
                for i, item in enumerate(current_batch)
            ]

            if table is None:
                table = db.create_table(TABLE_NAME, data=data)
            else:
                table.add(data)

            total_files += len(data)
            current_batch = []
            del vectors, data
            gc.collect()

    # Final filename batch
    if current_batch:
        names = [x['filename'] for x in current_batch]
        try:
            vectors = _remote_encode(names)
            data = [
                {"vector": vectors[i], "filename": item['filename'], "path": item['path']}
                for i, item in enumerate(current_batch)
            ]
            if table is None:
                table = db.create_table(TABLE_NAME, data=data)
            else:
                table.add(data)
            total_files += len(data)
            del vectors, data
            gc.collect()
        except Exception as e:
            logging.error(f"Remote encode failed for final batch: {e}")

    logging.info(f"Filename indexing complete. Total files: {total_files}")

    # --- CONTENT INDEXING ---
    logging.info("Scanning for text files to index content...")

    CHUNKS_TABLE = "file_chunks"
    try:
        db.drop_table(CHUNKS_TABLE)
    except: pass
    chunk_table = None

    CHUNK_BATCH_SIZE = 16
    current_batch_chunks = []
    current_batch_metadata = []
    total_chunks_indexed = 0

    for full_path, filename in file_generator(HOME):
        if not is_text_file(full_path): continue

        try:
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

                if len(current_batch_chunks) >= CHUNK_BATCH_SIZE:
                    try:
                        vectors = _remote_encode(current_batch_chunks)
                    except Exception as e:
                        logging.error(f"Remote encode failed: {e}")
                        current_batch_chunks = []
                        current_batch_metadata = []
                        continue

                    batch_data = [
                        {
                            "vector": vectors[idx],
                            "filename": meta['filename'],
                            "path": meta['path'],
                            "chunk_id": meta['chunk_id'],
                            "content": meta['content']
                        }
                        for idx, meta in enumerate(current_batch_metadata)
                    ]

                    if chunk_table is None:
                        chunk_table = db.create_table(CHUNKS_TABLE, data=batch_data)
                    else:
                        chunk_table.add(batch_data)

                    total_chunks_indexed += len(batch_data)
                    if total_chunks_indexed % 1000 == 0:
                        logging.info(f"Indexed {total_chunks_indexed} chunks...")

                    current_batch_chunks = []
                    current_batch_metadata = []
                    del vectors, batch_data
                    gc.collect()

        except Exception:
            pass

    # Final content batch
    if current_batch_chunks:
        try:
            vectors = _remote_encode(current_batch_chunks)
            batch_data = [
                {
                    "vector": vectors[idx],
                    "filename": meta['filename'],
                    "path": meta['path'],
                    "chunk_id": meta['chunk_id'],
                    "content": meta['content']
                }
                for idx, meta in enumerate(current_batch_metadata)
            ]
            if chunk_table is None:
                chunk_table = db.create_table(CHUNKS_TABLE, data=batch_data)
            else:
                chunk_table.add(batch_data)
            total_chunks_indexed += len(batch_data)
            del vectors, batch_data
            gc.collect()
        except Exception as e:
            logging.error(f"Remote encode failed for final chunk batch: {e}")

    logging.info(f"Content indexing complete. Total chunks: {total_chunks_indexed}")
    logging.info("Indexing complete!")


if __name__ == "__main__":
    main()
