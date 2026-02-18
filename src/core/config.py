import os

def _load_env_file(path: str):
    """Parse and load a single .env file into os.environ (setdefault — never overwrites)."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _load_env():
    """
    Load .env with a multi-location search strategy:
      1. Walk up from this file's directory until a .env is found (covers any clone location).
      2. Also load ~/.config/omni/.env as a per-user override/fallback (never committed to git).
    Using setdefault so that already-set env vars are always respected.
    """
    # 1. Walk up the directory tree from src/core/ towards filesystem root
    search_dir = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidate = os.path.join(search_dir, ".env")
        if os.path.exists(candidate):
            _load_env_file(candidate)
            break
        parent = os.path.dirname(search_dir)
        if parent == search_dir:  # reached filesystem root
            break
        search_dir = parent

    # 2. User-level fallback: ~/.config/omni/.env (safe place for personal keys on any machine)
    user_env = os.path.expanduser("~/.config/omni/.env")
    if os.path.exists(user_env):
        _load_env_file(user_env)


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
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"  # Fast + accurate; alternatives: whisper-large-v3, distil-whisper-large-v3-en
OWW_WAKE_WORD_MODEL = "Hey_Omni"  # Custom wake word; set OWW_CUSTOM_MODEL_PATH to load local .onnx
OWW_CUSTOM_MODEL_PATH = os.path.join(PROJECT_ROOT, "assets", "Voice_Activation", "Hey_Omni.onnx")
OWW_DETECTION_THRESHOLD = 0.3  # Wake word confidence threshold (0.0 - 1.0)

TTS_MODEL_ID = "hexgrad/Kokoro-82M"

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