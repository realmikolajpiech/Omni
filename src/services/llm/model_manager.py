"""
LLM model manager: Groq (fast) + xAI (main) via OpenAI-compatible API.
No local LLMs. Input caching: static content first for Groq prompt caching.
"""
import os
import logging
import threading
import base64

import torch
import sys

# Suppress HuggingFace Hub Permission Warnings
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


class PermissionErrorFilter(logging.Filter):
    def filter(self, record):
        return "Permission denied" not in record.getMessage() and "Could not cache non-existence" not in record.getMessage()


logging.getLogger("huggingface_hub").addFilter(PermissionErrorFilter())
logging.getLogger("transformers").addFilter(PermissionErrorFilter())

from src.core.config import (
    FAST_MODEL_GROQ,
    GROQ_API_KEY,
    MAIN_MODEL_XAI,
    XAI_API_KEY,
    EMBED_MODEL_PATH,
    EMBED_MODEL_FILENAME,
    EMBED_MODEL_URL,
    EMBED_MODEL_HF_ID,
    DB_PATH,
)

# Re-export for API status
FAST_MODEL_FILENAME = FAST_MODEL_GROQ
MAIN_MODEL_FILENAME = MAIN_MODEL_XAI

# Global State
llm = None
fast_model = None
tts_model = None
embed_model = None
db_conn = None
vision_model = None
init_error = None

# Thread Locks
main_lock = threading.Lock()
fast_lock = threading.Lock()
tts_lock = threading.Lock()
search_lock = threading.Lock()
abort_fast_event = threading.Event()

# Fast model request queue for cancellation
current_fast_request_id = None
fast_request_queue = []
fast_queue_lock = threading.Lock()


def _convert_file_urls_to_base64(messages):
    """
    Convert file:// image URLs to data:image/png;base64,... for API compatibility.
    xAI/Groq APIs don't support file:// - they need base64 data URIs.
    """
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("file://"):
                        path = url[7:]
                        if os.path.exists(path):
                            with open(path, "rb") as f:
                                b64 = base64.b64encode(f.read()).decode("utf-8")
                            new_content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"}
                            })
                        else:
                            new_content.append(part)
                    else:
                        new_content.append(part)
                else:
                    new_content.append(part)
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result


def _messages_for_cache(messages):
    """
    Ensure messages are structured for optimal prompt caching:
    - Static content first (system prompt, tool defs, few-shot)
    - Dynamic content last (user query, timestamps)
    Groq caches by prefix; xAI benefits from same structure.
    """
    return messages  # Already structured in chat.py with system first


def unload_main_model():
    """No-op for API clients - no local resources to unload."""
    pass


def _create_groq_client():
    """Create Groq client (OpenAI-compatible)."""
    from openai import OpenAI
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not set. Set it in environment.")
    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )


def _create_xai_client():
    """Create xAI client (OpenAI-compatible)."""
    from openai import OpenAI
    if not XAI_API_KEY:
        raise ValueError("XAI_API_KEY not set. Set it in environment.")
    return OpenAI(
        api_key=XAI_API_KEY,
        base_url="https://api.x.ai/v1",
    )


class GroqFastWrapper:
    """Wrapper for Groq GPT-OSS 20B - fast model for intents/actions."""

    def __init__(self, client):
        self.client = client
        self.model = FAST_MODEL_GROQ

    def reset(self):
        pass

    def create_chat_completion(self, messages, max_tokens=128, temperature=0.0, request_id=None, **kwargs):
        global current_fast_request_id
        import time

        if abort_fast_event.is_set():
            return None
        if request_id is not None and current_fast_request_id != request_id:
            return None

        messages = _convert_file_urls_to_base64(messages)
        messages = _messages_for_cache(messages)
        eff_temp = temperature if temperature > 0 else 0.1

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=eff_temp,
                stream=False,
            )
        except Exception as e:
            logging.error(f"Groq fast model error: {e}")
            return None

        msg = response.choices[0].message
        content = msg.content or ""
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"completion_tokens": getattr(response.usage, "completion_tokens", 0)},
        }


class XAIMainWrapper:
    """Wrapper for xAI Grok - main model. Uses vision model when images present."""

    def __init__(self, client):
        self.client = client

    def reset(self):
        pass

    def create_chat_completion(self, messages, max_tokens=1024, temperature=0.7, stream=False, **kwargs):
        messages = _convert_file_urls_to_base64(messages)
        messages = _messages_for_cache(messages)

        temperature = float(temperature)

        # Filter unsupported kwargs for xAI
        extra = {k: v for k, v in kwargs.items() if k not in ("chat_template_kwargs",)}

        if stream:
            return self.client.chat.completions.create(
                model=MAIN_MODEL_XAI,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                **extra,
            )

        response = self.client.chat.completions.create(
            model=MAIN_MODEL_XAI,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            **extra,
        )
        msg = response.choices[0].message
        content = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None) or ""

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning,
                }
            }]
        }


def ensure_fast_model():
    """Load fast model (Groq GPT-OSS 20B)."""
    global fast_model, init_error
    if fast_model:
        return

    with fast_lock:
        if fast_model:
            return
        logging.info(f"Loading Fast Model (Groq): {FAST_MODEL_GROQ}")
        try:
            client = _create_groq_client()
            fast_model = GroqFastWrapper(client)
            init_error = None
            logging.info("Fast Model Loaded (Groq).")
        except Exception as e:
            logging.error(f"Fast Model Load Error: {e}")
            init_error = str(e)


def ensure_resources():
    """Load shared resources (DB & Embeddings) without loading Main LLM."""
    global embed_model, db_conn

    with search_lock:
        if db_conn is None:
            try:
                if os.path.exists(DB_PATH):
                    import lancedb
                    db_conn = lancedb.connect(DB_PATH)
                    logging.info(f"Connected to LanceDB at {DB_PATH}")
            except Exception as e:
                logging.error(f"DB Error: {e}")

        if embed_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                model_id = EMBED_MODEL_HF_ID
                device = "cpu"
                logging.info(f"Loading Embedding Model ({model_id}) on {device}...")
                os.environ["TOKENIZERS_PARALLELISM"] = "false"
                try:
                    embed_model = SentenceTransformer(model_id, device=device, trust_remote_code=True)
                except Exception as e:
                    logging.warning(f"Failed to load {model_id}: {e}")
                    embed_model = SentenceTransformer(model_id, device="cpu", trust_remote_code=True)
            except Exception as e:
                logging.error(f"Embeddings Error: {e}")


def ensure_main_model():
    """Load main model (xAI Grok)."""
    global llm, init_error

    ensure_resources()

    if llm:
        return

    with main_lock:
        if llm:
            return
        logging.info(f"Loading Main Model (xAI): {MAIN_MODEL_XAI}")
        try:
            client = _create_xai_client()
            llm = XAIMainWrapper(client)
            init_error = None
            logging.info("Main Model Loaded (xAI).")
        except Exception as e:
            logging.error(f"Main Model Load Error: {e}")
            init_error = str(e)


def ensure_model_loaded():
    ensure_fast_model()
    ensure_main_model()
    # TTS is loaded lazily on first use via ensure_tts_model() to save RAM at startup.


def ensure_tts_model():
    global tts_model

    from src.core.config import TTS_MODEL_ID, PROJECT_ROOT

    if not TTS_MODEL_ID:
        return

    with tts_lock:
        if tts_model:
            return
        logging.info(f"Loading TTS Model: {TTS_MODEL_ID}")
        try:
            if "kokoro" in TTS_MODEL_ID.lower():
                os.environ["HF_HOME"] = os.path.join(PROJECT_ROOT, "data", "hf_cache")
                from kokoro import KPipeline
                pipeline = KPipeline(lang_code="a")
                tts_model = {"pipeline": pipeline, "type": "kokoro"}
                logging.info("Kokoro TTS Model Loaded.")
            else:
                from transformers import SpeechT5Processor, SpeechT5ForTextToSpeech, SpeechT5HifiGan
                processor = SpeechT5Processor.from_pretrained(TTS_MODEL_ID)
                model = SpeechT5ForTextToSpeech.from_pretrained(TTS_MODEL_ID)
                vocoder = SpeechT5HifiGan.from_pretrained("microsoft/speecht5_hifigan")
                if torch.cuda.is_available():
                    model = model.to("cuda")
                    vocoder = vocoder.to("cuda")
                elif sys.platform == "darwin" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    model = model.to("mps")
                    vocoder = vocoder.to("mps")
                tts_model = {"model": model, "processor": processor, "vocoder": vocoder, "type": "transformers"}
                logging.info("TTS Model Loaded (Transformers).")
        except Exception as e:
            logging.error(f"TTS Model Load Error: {e}")
