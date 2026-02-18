"""
Embedding helpers for in-process use (e.g. memvid_store).

Delegates to the shared BAAI/bge-m3 instance managed by model_manager so
there is only one copy of the embedding model loaded per process.
The fastembed bge-small-en-v1.5 model has been removed to save ~130 MB.
"""
import logging
from typing import List


def embed_text(text: str) -> List[float]:
    from src.services.llm.model_manager import ensure_resources, embed_model
    ensure_resources()
    if embed_model is None:
        logging.error("Embedding model not available.")
        return []
    try:
        return embed_model.encode([text])[0].tolist()
    except Exception as e:
        logging.error(f"Embedding generation failed: {e}")
        return []


def embed_texts(texts: List[str]) -> List[List[float]]:
    from src.services.llm.model_manager import ensure_resources, embed_model
    ensure_resources()
    if embed_model is None:
        logging.error("Embedding model not available.")
        return []
    try:
        return embed_model.encode(texts).tolist()
    except Exception as e:
        logging.error(f"Batch embedding generation failed: {e}")
        return []
