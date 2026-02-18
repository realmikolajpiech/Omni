
from fastembed import TextEmbedding
import logging
from typing import List

_embedder = None

def get_embedder():
    global _embedder
    if _embedder is None:
        try:
            logging.info("Initializing FastEmbed model...")
            # Use a small, fast model
            _embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        except Exception as e:
            logging.error(f"Failed to initialize embedder: {e}")
            return None
    return _embedder

def embed_text(text: str) -> List[float]:
    embedder = get_embedder()
    if not embedder:
        return []
    try:
        # embed returns a generator of numpy arrays
        embeddings = list(embedder.embed([text]))
        if embeddings:
            return embeddings[0].tolist()
    except Exception as e:
        logging.error(f"Embedding generation failed: {e}")
    return []

def embed_texts(texts: List[str]) -> List[List[float]]:
    embedder = get_embedder()
    if not embedder:
        return []
    try:
        embeddings = list(embedder.embed(texts))
        return [e.tolist() for e in embeddings]
    except Exception as e:
        logging.error(f"Batch embedding generation failed: {e}")
        return []
