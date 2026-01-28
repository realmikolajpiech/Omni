import os
import lancedb
from sentence_transformers import SentenceTransformer
from PIL import Image
import logging

from src.core.config import DB_PATH, HOME

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

def get_files_to_index(base_dir):
    logging.info(f"Scanning {base_dir} for files...")
    file_list = []
    for root, dirs, files in os.walk(base_dir):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        
        for file in files:
            if file.startswith("."): continue
            
            # 1. Check blocked extension
            _, ext = os.path.splitext(file)
            if ext.lower() in BLOCKED_EXTENSIONS: continue
            
            # 2. Check NO extension (as requested)
            if not ext: continue

            full_path = os.path.join(root, file)
            file_list.append({
                "filename": file,
                "path": full_path
            })
    logging.info(f"Found {len(file_list)} files.")
    return file_list

def main():
    # 1. Collect files
    files = get_files_to_index(HOME)
    if not files:
        logging.warning("No files found to index.")
        return

    # 2. Generate Embeddings
    logging.info("Loading embedding model...")
    model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    
    logging.info("Generating embeddings for filenames...")
    filenames = [f['filename'] for f in files]
    embeddings = model.encode(filenames, batch_size=32, show_progress_bar=True)
    
    # 3. Save to LanceDB
    logging.info(f"Connecting to LanceDB at {DB_PATH}...")
    db = lancedb.connect(DB_PATH)
    
    data = []
    for i, file_info in enumerate(files):
        data.append({
            "vector": embeddings[i].tolist(),
            "filename": file_info['filename'],
            "path": file_info['path']
        })
    
    logging.info(f"Creating/Replacing table '{TABLE_NAME}'...")
    db.create_table(TABLE_NAME, data=data, mode="overwrite")
    
    # --- IMAGE INDEXING ---
    logging.info("Scanning for images...")
    
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    image_files = []
    for f in files:
        _, ext = os.path.splitext(f['filename'])
        if ext.lower() in IMAGE_EXTS:
             image_files.append(f)
             
    if image_files:
        logging.info(f"Found {len(image_files)} images. Loading CLIP model...")
        vision_model = SentenceTransformer('clip-ViT-B-32', device='cpu')
        
        logging.info("Generating image embeddings...")
        img_objs = []
        valid_paths = []
        for img_f in image_files:
            try:
                img_objs.append(Image.open(img_f['path']))
                valid_paths.append(img_f)
            except Exception as e:
                logging.warning(f"Skipping bad image {img_f['filename']}: {e}")
        
        if img_objs:
            # Encoding images
            img_embeds = vision_model.encode(img_objs, batch_size=32, show_progress_bar=True)
            
            img_data = []
            for i, f_info in enumerate(valid_paths):
                img_data.append({
                    "vector": img_embeds[i].tolist(),
                    "filename": f_info['filename'],
                    "path": f_info['path']
                })
            
            IMG_TABLE = "images"
            logging.info(f"Creating/Replacing table '{IMG_TABLE}'...")
            db.create_table(IMG_TABLE, data=img_data, mode="overwrite")
            
    logging.info("Indexing complete!")

if __name__ == "__main__":
    main()
