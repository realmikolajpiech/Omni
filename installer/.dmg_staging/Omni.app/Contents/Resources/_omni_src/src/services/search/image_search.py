import logging
import time
from sentence_transformers import SentenceTransformer
from src.services.llm.model_manager import ensure_main_model, ensure_fast_model, fast_model, fast_lock, db_conn, vision_model
from src.services.memory.memvid_store import get_user_memory

# We need to update the global vision_model in model_manager if we load it here
import src.services.llm.model_manager as model_manager

def should_search_images(query):
    """Uses Fast Model to decide if we need to search images."""
    query_lower = query.lower()
    # High priority patterns
    img_patterns = [
        "photo", "image", "picture", "screenshot", "camera", "look like",
        "find photo", "search image", "draw", "generate",
        "wallpaper", "background"
    ]
    # "show me" is only for images if not followed by "video", "trailer", etc.
    if "show me" in query_lower and not any(x in query_lower for x in ["video", "trailer", "movie", "youtube", "how to", "make", "recipe", "why", "who is", "where is"]):
         return True
         
    if any(pattern in query_lower for pattern in img_patterns):
         return True
    return False

def ensure_vision_model():
    if model_manager.vision_model is None:
         logging.info("Loading CLIP model for Search...")
         try:
            model_manager.vision_model = SentenceTransformer('clip-ViT-B-32', device='cpu')
         except Exception as e:
            logging.error(f"Failed to load CLIP model: {e}")

def perform_image_search(query):
    """Searches LanceDB 'images' table using CLIP text embedding."""
    ensure_main_model()
    ensure_vision_model()
    
    if model_manager.vision_model is None: return ""
    if not db_conn: return ""
    
    try:
        if "images" not in db_conn.table_names():
            return ""

        tbl = db_conn.open_table("images")
        # Encode query with CLIP (text)
        vector = model_manager.vision_model.encode(query).tolist()
        logging.info(f"Searching images for: '{query}' (vector len={len(vector)})")
        
        res = tbl.search(vector).limit(3).to_pandas()
        if res.empty: 
            logging.info(f"No image results for '{query}'")
            return ""
        
        results = []
        for _, row in res.iterrows():
            path = row['path']
            filename = row['filename']
            score = row['_distance']
            logging.info(f"Found image match: {filename} (score={score:.4f})")
            results.append(f"Found Image: {filename}\nPath: {path}")
            
        return "\n\n".join(results)
    except Exception as e:
        logging.error(f"Image search failed: {e}")
        return ""

def perform_image_search_with_fallback(query):
    """Searches images, optionally expanding query with user name."""
    # 1. Standard Vector Search
    res_vec = perform_image_search(query)
    
    # 2. Check for "My" -> name keyword search
    res_kw = ""
    query_lower = query.lower()
    if "my" in query_lower or "me" in query_lower:
        mem_str = get_user_memory()
        # Extract name from string like "[2026-01-10] The user's name is Miki."
        name = ""
        import re
        name_match = re.search(r"user's name is ([^.\n]+)", mem_str, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).strip()
        
        if name:
             # Extract first name "Mikołaj" -> "mikolaj"
             # Simple normalization: lowercase
             parts = name.lower().split()
             for part in parts:
                 if len(part) < 3: continue
                 kw_results = []
                 try:
                     # Unicode Normalization for ASCII fallback (mikołaj -> mikolaj)
                     # Manual map for Polish chars that NFKD doesn't handle well for 'ł'
                     replacements = {
                         'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 
                         'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z'
                     }
                     ascii_part = part
                     for k, v in replacements.items():
                         ascii_part = ascii_part.replace(k, v)
                     
                     import unicodedata
                     normalized = unicodedata.normalize('NFKD', ascii_part).encode('ASCII', 'ignore').decode('utf-8')
                     variants = {part, ascii_part, normalized}
                     if normalized != part and len(normalized) >= 3:
                         variants.add(normalized)
                     
                     for v in variants:
                        logging.info(f"Performing Keyword Search for '{v}'...")
                        try:
                            # Re-open table here as 'tbl' is not in scope
                            if "images" in model_manager.db_conn.table_names():
                                img_tbl = model_manager.db_conn.open_table("images")
                                matches = img_tbl.search().where(f"filename LIKE '%{v}%'").limit(5).to_pandas()
                                
                                for _, row in matches.iterrows():
                                    # Avoid duplicates if multiple parts match same file
                                    entry = f"Found Image (By Name): {row['filename']}\nPath: {row['path']}"
                                    if entry not in res_kw:
                                        kw_results.append(entry)
                        except Exception as e:
                             logging.error(f"Keyword search inner loop failed: {e}")
                     
                     if kw_results:
                         res_kw += "\n\n".join(kw_results) + "\n\n"
                 except Exception as e:
                     logging.error(f"Keyword search failed: {e}")

    # Combine: Keywords first (high priority), then Vector
    final_res = (res_kw + "\n" + res_vec).strip()
    return final_res
