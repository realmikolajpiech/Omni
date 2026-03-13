"""
LLM model manager: Groq (fast) + xAI (main) via OpenAI-compatible API.
No local LLMs. Input caching: static content first for Groq prompt caching.
"""
import os
import logging
import threading
import base64
import time

# Suppress HuggingFace Hub Permission Warnings
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


class PermissionErrorFilter(logging.Filter):
    def filter(self, record):
        return "Permission denied" not in record.getMessage() and "Could not cache non-existence" not in record.getMessage()


logging.getLogger("huggingface_hub").addFilter(PermissionErrorFilter())

from src.core.config import (
    FAST_MODEL_GROQ,
    GROQ_API_KEY,
    MAIN_MODEL_XAI,
    XAI_API_KEY,
    EMBED_MODEL_HF_ID,
    CLIP_MODEL_HF_ID,
    DB_PATH,
    BACKEND_URL,
    OMNI_SECRET,
    DEVICE_ID,
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
embed_lock = threading.Lock()  # serialises all embed_model.encode() calls (not thread-safe on MPS)
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


def _backend_client():
    """OpenAI-compatible client pointed at the Omni Worker backend.

    The Worker holds the real API keys — the app only needs the shared
    OMNI_SECRET and the device ID, both embedded in the binary.
    """
    from openai import OpenAI
    return OpenAI(
        api_key="omni-proxy",          # dummy — Worker ignores this field
        base_url=BACKEND_URL + "/v1",
        default_headers={
            "X-Omni-Secret": OMNI_SECRET,
            "X-Device-ID":   DEVICE_ID,
        },
    )


def _create_groq_client():
    """Groq client — routed through the Omni Worker."""
    return _backend_client()


def _create_xai_client():
    """xAI client — routed through the Omni Worker."""
    return _backend_client()


def _get_custom_client_and_model():
    """Return (OpenAI client, model_name) from user settings, or (None, None) if not configured."""
    try:
        import src.core.settings_store as settings_store
        url = settings_store.get("custom_api_url", "")
        key = settings_store.get("custom_api_key", "")
        model = settings_store.get("custom_model", "")
        if url and key and model:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=url)
            return client, model
    except Exception as e:
        logging.warning(f"Could not load custom API settings: {e}")
    return None, None


class GroqFastWrapper:
    """Wrapper for Groq GPT-OSS 20B - fast model for intents/actions."""

    def __init__(self, client):
        self.client = client
        self.model = FAST_MODEL_GROQ

    def reset(self):
        pass

    def create_chat_completion(self, messages, max_tokens=128, temperature=0.0, request_id=None, tools=None, tool_choice=None, **kwargs):
        global current_fast_request_id

        if abort_fast_event.is_set():
            return None
        if request_id is not None and current_fast_request_id != request_id:
            return None

        messages = _convert_file_urls_to_base64(messages)
        messages = _messages_for_cache(messages)
        eff_temp = temperature if temperature > 0 else 0.1

        extra = {}
        if tools:
            extra["tools"] = tools
        if tool_choice:
            extra["tool_choice"] = tool_choice
        elif tools and not tool_choice:
             # Default to auto if tools are present but no choice specified
             extra["tool_choice"] = "auto"


        try:
            from src.core import auth as _auth
            token = _auth.get_access_token()
            auth_headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=eff_temp,
                stream=False,
                extra_headers=auth_headers,
                **extra
            )
        except Exception as e:
            logging.error(f"Groq fast model error: {e}")
            return None

        msg = response.choices[0].message
        content = msg.content or ""
        
        tool_calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        return {
            "choices": [{"message": {
                "role": "assistant", 
                "content": content,
                "tool_calls": tool_calls
            }}],
            "usage": {"completion_tokens": getattr(response.usage, "completion_tokens", 0)},
        }


class XAIMainWrapper:
    """Wrapper for the main model (xAI Grok by default, or custom API)."""

    def __init__(self, client, model: str = MAIN_MODEL_XAI):
        self.client = client
        self.model = model

    def reset(self):
        pass

    def create_chat_completion(self, messages, max_tokens=1024, temperature=0.7, stream=False, tools=None, model_override=None, **kwargs):
        messages = _convert_file_urls_to_base64(messages)
        messages = _messages_for_cache(messages)

        temperature = float(temperature)

        # Use override model if provided (for smart routing), otherwise default
        active_model = model_override or self.model

        # Filter unsupported kwargs for xAI / generic OpenAI-compatible APIs
        extra = {k: v for k, v in kwargs.items() if k not in ("chat_template_kwargs",)}

        if tools:
            extra["tools"] = tools

        # Limit reasoning effort for reasoning models to reduce thinking time
        # Note: xAI grok models don't support reasoning_effort parameter
        if "reasoning" in active_model and "grok" not in active_model:
            extra["reasoning_effort"] = "low"

        start_time = time.time()

        from src.core import auth as _auth
        token = _auth.get_access_token()
        auth_headers = {"Authorization": f"Bearer {token}"} if token else {}

        if stream:
            try:
                stream_response = self.client.chat.completions.create(
                    model=active_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    extra_headers=auth_headers,
                    **extra,
                )
                logging.info(f"[Main Model] Stream initiated in {time.time() - start_time:.4f}s")
                return stream_response
            except Exception as e:
                logging.error(f"[Main Model] Stream error after {time.time() - start_time:.4f}s: {e}")
                raise e

        try:
            response = self.client.chat.completions.create(
                model=active_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
                extra_headers=auth_headers,
                **extra,
            )
            duration = time.time() - start_time
            logging.info(f"[Main Model] Completion received in {duration:.4f}s")
        except Exception as e:
            logging.error(f"[Main Model] Completion error after {time.time() - start_time:.4f}s: {e}")
            raise e

        msg = response.choices[0].message
        content = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None) or ""

        # Extract tool calls if present
        tool_calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning,
                    "tool_calls": tool_calls,
                }
            }]
        }


def _detect_embed_device() -> str:
    """Return 'cuda', 'mps', or 'cpu' based on available hardware (no torch required)."""
    import platform, subprocess
    # Apple Silicon → Metal
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mps"
    # CUDA → check nvidia-smi
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=3)
        if r.returncode == 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _ensure_hf_shard_index(model_id: str):
    """embed-anything panics when model.safetensors.index.json is missing (single-file models).
    This creates a synthetic index pointing all weights to model.safetensors if needed."""
    try:
        import json
        from huggingface_hub import snapshot_download
        from safetensors import safe_open

        model_dir = snapshot_download(model_id)
        st_path = os.path.join(model_dir, "model.safetensors")
        index_path = os.path.join(model_dir, "model.safetensors.index.json")
        if not os.path.exists(st_path) or os.path.exists(index_path):
            return  # nothing to do
        file_size = os.path.getsize(st_path)
        with safe_open(st_path, framework="pt", device="cpu") as f:
            weight_map = {k: "model.safetensors" for k in f.keys()}
        with open(index_path, "w") as f:
            json.dump({"metadata": {"total_size": file_size}, "weight_map": weight_map}, f)
        logging.info(f"Created missing shard index for {model_id} ({len(weight_map)} weights)")
    except Exception as e:
        logging.warning(f"Could not create shard index for {model_id}: {e}")


class _EmbedAnythingWrapper:
    """Wraps an embed-anything text model to match the .encode(texts) → np.ndarray interface.

    Returns L2-normalised vectors so the existing LanceDB distance threshold (~1.1) stays valid.
    """

    def __init__(self, model):
        import embed_anything as _ea
        self._ea = _ea
        self.model = model

    def encode(self, texts):
        import numpy as np
        single = isinstance(texts, str)
        if single:
            texts = [texts]
        results = self._ea.embed_query(texts, embedder=self.model)
        vecs = np.array([r.embedding for r in results], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normed = vecs / norms
        return normed[0] if single else normed


class _EmbedAnythingCLIPWrapper:
    """CLIP wrapper via embed-anything for image + text encoding.

    encode(path: str)  → image vector (512-dim, L2-normalised)
    encode(text: str)  → CLIP text vector (512-dim, L2-normalised)
    """

    def __init__(self, model):
        import embed_anything as _ea
        from embed_anything import ImageEmbedConfig
        self._ea = _ea
        self._ImageEmbedConfig = ImageEmbedConfig
        self.model = model

    def encode(self, input_):
        import numpy as np
        if isinstance(input_, str) and os.path.isfile(input_):
            cfg = self._ImageEmbedConfig(batch_size=1)
            results = self._ea.embed_file(input_, embedder=self.model, config=cfg)
        else:
            text = input_ if isinstance(input_, str) else str(input_)
            results = self._ea.embed_query([text], embedder=self.model)
        if not results:
            return np.zeros(512, dtype=np.float32)
        vec = np.array(results[0].embedding, dtype=np.float32)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec


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
                from embed_anything import EmbeddingModel
                import embed_anything as _ea
                device = _detect_embed_device()
                if device == "mps":
                    os.environ.setdefault("METAL_DEVICE", "1")
                    logging.info("Embedding: Metal (MPS) acceleration enabled.")
                elif device == "cuda":
                    logging.info("Embedding: CUDA acceleration enabled.")
                else:
                    logging.info("Embedding: Using CPU.")
                os.environ["TOKENIZERS_PARALLELISM"] = "false"
                _ensure_hf_shard_index(EMBED_MODEL_HF_ID)
                logging.info(f"Loading embedding model ({EMBED_MODEL_HF_ID})...")
                _raw = EmbeddingModel.from_pretrained_hf(model_id=EMBED_MODEL_HF_ID)
                embed_model = _EmbedAnythingWrapper(_raw)
                logging.info("Embedding model loaded.")
            except Exception as e:
                logging.error(f"Embeddings Error: {e}")


def ensure_main_model():
    """Load main model — custom API if configured in settings, otherwise xAI Grok."""
    global llm, init_error

    # Removed ensure_resources() to defer 15s embed model loading until strictly needed by a tool.

    if llm:
        return

    with main_lock:
        if llm:
            return
        # Prefer custom API from user settings
        custom_client, custom_model = _get_custom_client_and_model()
        if custom_client and custom_model:
            logging.info(f"Loading Main Model (custom API): {custom_model}")
            try:
                llm = XAIMainWrapper(custom_client, model=custom_model)
                init_error = None
                logging.info(f"Main Model Loaded (custom): {custom_model}")
                return
            except Exception as e:
                logging.error(f"Custom Model Load Error: {e}")
                init_error = str(e)
                return

        logging.info(f"Loading Main Model (xAI): {MAIN_MODEL_XAI}")
        try:
            client = _create_xai_client()
            llm = XAIMainWrapper(client, model=MAIN_MODEL_XAI)
            init_error = None
            logging.info("Main Model Loaded (xAI).")
        except Exception as e:
            logging.error(f"Main Model Load Error: {e}")
            init_error = str(e)


def ensure_model_loaded():
    ensure_fast_model()
    ensure_main_model()
    # TTS loaded lazily on first use to save RAM at startup


def ensure_tts_model():
    global tts_model
    # edge-tts is API-based — no local model to load
    with tts_lock:
        if not tts_model:
            tts_model = {"type": "edge-tts"}
            logging.info("TTS set to edge-tts (no local model required).")


def ensure_vision_model():
    """Load CLIP vision model via embed-anything (lazy, thread-safe)."""
    global vision_model
    if vision_model is not None:
        return
    with search_lock:
        if vision_model is not None:
            return
        try:
            from embed_anything import EmbeddingModel
            logging.info(f"Loading CLIP vision model ({CLIP_MODEL_HF_ID})...")
            _raw = EmbeddingModel.from_pretrained_hf(model_id=CLIP_MODEL_HF_ID)
            vision_model = _EmbedAnythingCLIPWrapper(_raw)
            logging.info("CLIP vision model loaded.")
        except Exception as e:
            logging.error(f"CLIP model load error: {e}")
