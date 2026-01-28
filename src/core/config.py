import os

# Paths
HOME = os.path.expanduser("~")
MODEL_DIR = os.path.join(HOME, ".local/share/ai-models")
DB_PATH = os.path.join(HOME, ".local/share/ai-memory-db")
PERSONAL_MEM_PATH = os.path.join(HOME, ".config/omni/personal.mv2")
LOG_PATH = os.path.expanduser("~/.config/omni/omni_debug.log")

# Ensure directories exist
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Models
FAST_MODEL_FILENAME = "gemma-3-1b-it-Q8_0.gguf"
FAST_MODEL_PATH = os.path.join(MODEL_DIR, FAST_MODEL_FILENAME)

MAIN_MODEL_FILENAME = "google_gemma-3-4b-it-Q4_K_M.gguf"
MAIN_MODEL_PATH = os.path.join(MODEL_DIR, MAIN_MODEL_FILENAME)
MAIN_MODEL_URL = "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/google_gemma-3-4b-it-Q4_K_M.gguf"

MMPROJ_FILENAME = "mmproj-google_gemma-3-4b-it-f16.gguf"
MMPROJ_PATH = os.path.join(MODEL_DIR, MMPROJ_FILENAME)
MMPROJ_URL = "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/mmproj-google_gemma-3-4b-it-f16.gguf"

# Voice Models
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_PATH = os.path.join(MODEL_DIR, VOSK_MODEL_NAME)
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

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

SEARXNG_URL = "http://127.0.0.1:8888/search"

# IPC
IPC_PORT = 5556

# Assets
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO_PATH = os.path.join(PROJECT_ROOT, "assets", "omni.png")

# Shortcuts
COMMON_SHORTCUTS = {
    "yt": "https://www.youtube.com",
    "gh": "https://github.com",
    "x": "https://x.com",
    "red": "https://reddit.com",
    "map": "https://www.google.com/maps",
    "chat": "https://chatgpt.com"
}
