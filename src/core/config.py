import os

# Load .env from project root (if present)
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    env_path = os.path.normpath(env_path)
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

_load_env()

# Assets
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set HF_HOME to a local directory to avoid permission issues
os.environ["HF_HOME"] = os.path.join(PROJECT_ROOT, "data", "hf_cache")

# Paths
HOME = os.path.expanduser("~")
MODEL_DIR = os.path.join(HOME, ".local/share/ai-models")
DB_PATH = os.path.join(HOME, ".local/share/ai-memory-db")
PERSONAL_MEM_PATH = os.path.join(PROJECT_ROOT, "data", "personal.mv2")
LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "omni.log")

# Ensure directories exist
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(PERSONAL_MEM_PATH), exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Models (API-based, no local LLMs)
# Fast model: Groq GPT-OSS 20B (low-latency intents/actions)
FAST_MODEL_GROQ = "openai/gpt-oss-20b"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Main model: xAI Grok (via OpenAI-compatible API)
MAIN_MODEL_XAI = "grok-4-1-fast-reasoning"
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")

# Embedding Model
EMBED_MODEL_HF_ID = "BAAI/bge-m3" # Multi-functionality embedding model (Dense, Sparse, ColBERT)
EMBED_MODEL_FILENAME = "bge-m3.onnx" # Placeholder
EMBED_MODEL_PATH = os.path.join(MODEL_DIR, EMBED_MODEL_FILENAME)
EMBED_MODEL_URL = "" 

# Voice Models
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_PATH = os.path.join(MODEL_DIR, VOSK_MODEL_NAME)
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

ASR_MODEL_ID = "openai/whisper-small" # Multilingual support
TTS_MODEL_ID = "hexgrad/Kokoro-82M" # Kokoro-82M for high quality and speed

# URLs
BRAIN_HOST = "127.0.0.1"
BRAIN_PORT = 5555
BRAIN_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/ask_llm"
SEARCH_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/search"
ACTION_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/action"
INSTALL_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/install_plan"
FIND_PACKAGE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/find_package"
PICK_PACKAGE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/pick_package"
VERIFY_PACKAGE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/verify_package"

SEARXNG_URL = "http://127.0.0.1:8080/search"

# Search API (Serper.dev -- fast Google results, SearXNG as fallback)
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")

# IPC
IPC_PORT = 5556

# Assets
LOGO_PATH = os.path.join(PROJECT_ROOT, "assets", "omni.png")

# Shortcuts
COMMON_SHORTCUTS = {
    "yt": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "gh": "https://github.com",
    "x": "https://x.com",
    "red": "https://reddit.com",
    "map": "https://www.google.com/maps",
    "chat": "https://chatgpt.com"
}