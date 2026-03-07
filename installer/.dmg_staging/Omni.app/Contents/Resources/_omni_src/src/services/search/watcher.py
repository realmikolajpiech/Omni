import os
import time
import logging
import lancedb
import sys
import json
import urllib.request
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.core.config import DB_PATH, HOME, BRAIN_HOST, BRAIN_PORT, IGNORE_DIRS, BLOCKED_EXTENSIONS
from src.services.search.utils import process_file_content, is_text_file, is_image_file

# Setup logging - Silent for production unless error
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("watcher")
logger.setLevel(logging.INFO)

TABLE_NAME = "files"
CHUNKS_TABLE_NAME = "file_chunks"
EMBED_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/embed"

# Lazy-loaded CLIP vision model (only instantiated on first image event)
_vision_model = None


def _get_vision_model():
    """Load clip-ViT-B-32 on first use instead of at startup."""
    global _vision_model
    if _vision_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading CLIP vision model (lazy)...")
        _vision_model = SentenceTransformer('clip-ViT-B-32', device='cpu')
        logger.info("CLIP vision model loaded.")
    return _vision_model


def _remote_encode(texts: list) -> list:
    """Call the brain's /embed endpoint and return a list of float vectors."""
    payload = json.dumps({"texts": texts}).encode("utf-8")
    req = urllib.request.Request(
        EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["vectors"]


class IndexHandler(FileSystemEventHandler):
    def __init__(self, db_table, img_table, chunk_table):
        self.table = db_table
        self.img_table = img_table
        self.chunk_table = chunk_table

    def on_created(self, event):
        if event.is_directory or self._should_ignore(event.src_path): return
        self._index_file(event.src_path)

    def on_modified(self, event):
        if event.is_directory or self._should_ignore(event.src_path): return
        self._index_file(event.src_path)

    def on_deleted(self, event):
        if event.is_directory or self._should_ignore(event.src_path): return
        self._remove_file(event.src_path)

    def on_moved(self, event):
        if event.is_directory: return
        if not self._should_ignore(event.src_path):
            self._remove_file(event.src_path)
        if not self._should_ignore(event.dest_path):
            self._index_file(event.dest_path)

    def _should_ignore(self, path):
        parts = path.split(os.sep)
        for p in parts:
            if p in IGNORE_DIRS or (p.startswith(".") and len(p) > 1 and p not in {".", ".."}):
                return True

        filename = parts[-1]
        _, ext = os.path.splitext(filename)
        if ext.lower() in BLOCKED_EXTENSIONS:
            return True
        return False

    def _index_file(self, path):
        if not os.path.exists(path) or os.path.isdir(path): return

        _, ext = os.path.splitext(path)
        if ext.lower() in BLOCKED_EXTENSIONS: return
        if not ext: return

        if is_image_file(path):
            try:
                logging.info(f"Indexing IMAGE: {path}")
                vision_model = _get_vision_model()
                img = Image.open(path)
                vector = vision_model.encode(img).tolist()
                filename = os.path.basename(path)
                self.img_table.delete(f'path = "{path}"')
                self.img_table.add([{"vector": vector, "filename": filename, "path": path}])
            except Exception as e:
                logger.error(f"Error indexing image {path}: {e}")
            return

        filename = os.path.basename(path)
        if not filename: return

        try:
            logger.info(f"Indexing Filename: {path}")
            vectors = _remote_encode([filename])
            self.table.delete(f'path = "{path}"')
            self.table.add([{"vector": vectors[0], "filename": filename, "path": path}])
        except Exception as e:
            logger.error(f"Error indexing filename {path}: {e}")

        if self.chunk_table and is_text_file(path):
            try:
                chunks = process_file_content(path)
                if not chunks: return

                logger.info(f"Indexing Content: {path} ({len(chunks)} chunks)")
                vectors = _remote_encode(chunks)

                self.chunk_table.delete(f'path = "{path}"')
                chunk_data = []
                for i, chunk in enumerate(chunks):
                    chunk_data.append({
                        "vector": vectors[i],
                        "filename": filename,
                        "path": path,
                        "chunk_id": i,
                        "content": chunk
                    })
                self.chunk_table.add(chunk_data)
            except Exception as e:
                logger.error(f"Error indexing content for {path}: {e}")

    def _remove_file(self, path):
        try:
            logger.info(f"Removing: {path}")
            self.table.delete(f'path = "{path}"')
            self.img_table.delete(f'path = "{path}"')
            if self.chunk_table:
                self.chunk_table.delete(f'path = "{path}"')
        except Exception as e:
            logger.error(f"Error removing {path}: {e}")


def main():
    if not os.path.exists(DB_PATH):
        logger.error(f"DB not found at {DB_PATH}. Run indexer first.")
        sys.exit(1)

    db = lancedb.connect(DB_PATH)
    try:
        if TABLE_NAME not in db.table_names():
            logger.warning(f"Table '{TABLE_NAME}' not found. Creating empty table.")
            # We can't easily create it with correct schema without vector size knowledge
            # But to prevent crash, we just wait or exit gracefully
            logger.error("Watcher cannot start without 'files' table. Run indexer first.")
            sys.exit(1)
        else:
            table = db.open_table(TABLE_NAME)

        img_table = (
            db.create_table("images", schema=None, mode="overwrite")
            if "images" not in db.table_names()
            else db.open_table("images")
        )

        chunk_table = None
        if CHUNKS_TABLE_NAME in db.table_names():
            chunk_table = db.open_table(CHUNKS_TABLE_NAME)
        else:
            logger.warning(f"{CHUNKS_TABLE_NAME} table not found. Content indexing skipped until indexer runs.")

    except Exception as e:
        logger.error(f"Failed to open tables: {e}")
        sys.exit(1)

    logger.info(f"Watcher ready. Embedding via brain at {EMBED_URL}")

    event_handler = IndexHandler(table, img_table, chunk_table)
    observer = Observer()
    observer.schedule(event_handler, HOME, recursive=True)
    observer.start()

    logger.info(f"Watching {HOME} for changes...")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
