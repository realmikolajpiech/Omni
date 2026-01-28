import os
import time
import logging
import lancedb
import sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from sentence_transformers import SentenceTransformer
from PIL import Image

from src.core.config import DB_PATH, HOME

# Setup logging - Silent for production unless error
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("watcher")
logger.setLevel(logging.INFO)

TABLE_NAME = "files"

# Directories to strictly ignore to avoid feedback loops and noise
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

class IndexHandler(FileSystemEventHandler):
    def __init__(self, db_table, model, img_table, vision_model):
        self.table = db_table
        self.model = model
        self.img_table = img_table
        self.vision_model = vision_model

    def on_created(self, event):
        if event.is_directory or self._should_ignore(event.src_path): return
        self._index_file(event.src_path)

    def on_modified(self, event):
        if event.is_directory or self._should_ignore(event.src_path): return
        # Re-index for modified too
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
        
        # Check extensions on the filename part (last part)
        filename = parts[-1]
        _, ext = os.path.splitext(filename)
        
        if ext.lower() in BLOCKED_EXTENSIONS:
            return True
        
        return False
        

    def _index_file(self, path):
        # Double check existence and type
        if not os.path.exists(path) or os.path.isdir(path): return

        # EXTENSION & NO-EXTENSION CHECK
        _, ext = os.path.splitext(path)
        if ext.lower() in BLOCKED_EXTENSIONS: return
        if not ext: return # Skip files with no extensions
        
        # Check if IMAGE
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
        if ext.lower() in IMAGE_EXTS:
            try:
                logging.info(f"Indexing IMAGE: {path}")
                img = Image.open(path)
                vector = self.vision_model.encode(img).tolist()
                filename = os.path.basename(path)
                
                # Update images table
                self.img_table.delete(f'path = "{path}"')
                self.img_table.add([{
                    "vector": vector,
                    "filename": filename,
                    "path": path
                }])
            except Exception as e:
                logger.error(f"Error indexing image {path}: {e}")
            return

        filename = os.path.basename(path)
        if not filename: return
        
        try:
            logger.info(f"Indexing: {path}")
            vector = self.model.encode(filename).tolist()
            
            # Remove old entry if exists to avoid duplicates
            self.table.delete(f'path = "{path}"')
            
            # Add new entry
            self.table.add([{
                "vector": vector,
                "filename": filename,
                "path": path
            }])
        except Exception as e:
            logger.error(f"Error indexing {path}: {e}")

    def _remove_file(self, path):
        try:
            logger.info(f"Removing: {path}")
            self.table.delete(f'path = "{path}"')
            self.img_table.delete(f'path = "{path}"')
        except Exception as e:
            logger.error(f"Error removing {path}: {e}")

def main():
    if not os.path.exists(DB_PATH):
        # If DB doesn't exist, try to create it or exit?
        # Better to exit as indexer should run first
        logger.error(f"DB not found at {DB_PATH}. Run indexer first.")
        sys.exit(1)

    db = lancedb.connect(DB_PATH)
    try:
        table = db.open_table(TABLE_NAME)
        # Create or open images table
        img_table = db.create_table("images", schema=None, mode="overwrite") if "images" not in db.table_names() else db.open_table("images")
    except Exception as e:
        logger.error(f"Failed to open tables: {e}")
        sys.exit(1)

    # Force CPU
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    logging.info("Loading models...")
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    vision_model = SentenceTransformer('clip-ViT-B-32', device='cpu')

    event_handler = IndexHandler(table, model, img_table, vision_model)
    observer = Observer()
    observer.schedule(event_handler, HOME, recursive=True)
    observer.start()
    
    logging.info(f"Watching {HOME} for changes...")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
