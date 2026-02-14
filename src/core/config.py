import os

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

# Models
# Fast model: transformers (HF) for low-latency intents/actions
FAST_MODEL_HF_ID = "Qwen/Qwen3-0.6B-GGUF"
FAST_MODEL_FILENAME = "qwen3-0.6b-q8_0.gguf"
FAST_MODEL_PATH = os.path.join(MODEL_DIR, FAST_MODEL_FILENAME)
FAST_MODEL_URL = "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf"

MAIN_MODEL_FILENAME = "Qwen3VL-4B-Thinking-Q4_K_M.gguf"
MAIN_MODEL_PATH = os.path.join(MODEL_DIR, MAIN_MODEL_FILENAME)
MAIN_MODEL_URL = "https://huggingface.co/Qwen/Qwen3-VL-4B-Thinking-GGUF/resolve/main/Qwen3VL-4B-Thinking-Q4_K_M.gguf"

MMPROJ_FILENAME = "mmproj-Qwen3VL-4B-Thinking-F16.gguf"
MMPROJ_PATH = os.path.join(MODEL_DIR, MMPROJ_FILENAME)
MMPROJ_URL = "https://huggingface.co/Qwen/Qwen3-VL-4B-Thinking-GGUF/resolve/main/mmproj-Qwen3VL-4B-Thinking-F16.gguf"

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