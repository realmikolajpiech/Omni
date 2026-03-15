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
INDEX_DONE_MARKER    = os.path.join(DB_PATH, ".indexed")
INDEX_PROGRESS_PATH  = os.path.join(DB_PATH, ".index_progress.json")
PERSONAL_MEM_PATH = os.path.join(PROJECT_ROOT, "data", "personal.mv2")
LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "omni.log")
INDEX_LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "indexing_history.log")

# Directories to skip during indexing / watching
IGNORE_DIRS = {
    ".cache", ".git", ".npm", "node_modules", ".node_modules",
    "venv", ".venv", "__pycache__",
    ".local", ".config", ".mozilla", ".thunderbird",
    "anaconda3", ".anaconda3",
    "go", ".cargo", ".rustup",
    "Library", "Applications", ".Trash",
    ".gemini", ".antigravity", ".vscode", ".idea",
    "target", "build", "dist", "out", "bin", "obj",
    # macOS user dirs unlikely to contain useful documents
    "Movies", "Music", "Pictures", "Public",
    # Dev tools / package caches
    ".docker", ".gradle", ".m2", ".ivy2", ".sbt",
    ".conda", "miniconda3", "miniforge3",
    ".gem", ".rbenv", ".pyenv", ".nvm",
    ".cocoapods", "Pods", ".dart_tool", ".pub-cache",
    # Other
    "Parallels", ".Spotlight-V100", ".fseventsd",
    # CMake build output (auto-generated, zero user value)
    "cmake-build-debug", "cmake-build-release", "CMakeFiles", ".cmake",
    # HuggingFace / model caches (model weights, tokenizer blobs — zero user value)
    "hf_cache", "hub", "blobs", "snapshots",
    # Unity
    "Library", "Temp", "Obj", "Logs", "MemoryCaptures",
    # Flutter/Dart
    "flutter", ".fvm",
    # macOS App Bundle internals (to avoid indexing inside .app)
    "Contents", "Frameworks", "MacOS", "Plugins", "Resources", "XPC Services",
    # Android / iOS / Mobile Assets
    "res", "flutter_assets", "xcassets", "lproj",
    # Gradle
    "gradle",
    # Unity project / user settings (binary .asset.private.0 blobs + boilerplate JSON)
    "ProjectSettings", "UserSettings",
    # Maven repositories (Firebase/Android binary deps — .srcaar, metadata XML)
    "m2repository",
    # Unity generated / deleted asset caches
    "GeneratedAssets_deleted",
    # Font library internals (PHP font metrics, ICC profiles, hyphenation patterns)
    "ttfontdata", "iccprofiles",
    # Unity StreamingAssets (runtime binary data, not user documents)
    "StreamingAssets",
}

# File extensions that are purely internal / developer noise
BLOCKED_EXTENSIONS = {
    ".pyi", ".pyc", ".pyo", ".pyd", ".o", ".so", ".dll", ".dylib", ".a", ".lib",
    ".class", ".jar", ".war", ".ear", ".min.js", ".min.css", ".map", ".log",
    ".tmp", ".temp", ".bak", ".swp", ".swo", ".ds_store", ".thumbs", ".db",
    # Archives & disk images
    ".whl", ".egg", ".iso", ".dmg", ".pkg", ".deb", ".rpm",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    # Media (indexed separately via image/CLIP phase if applicable)
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".flac", ".wav", ".webm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".ico", ".icns",
    # Databases
    ".sqlite3", ".hprof", ".rdb",
    # ML Models & Binaries
    ".pth", ".tflite", ".onnx", ".pb", ".binarypb", ".wasm", ".data", ".safetensors",
    ".pt", ".ckpt", ".bin", ".caffemodel", ".pbtxt", ".h5", ".keras",
    # Certificates & Keys
    ".pem", ".key", ".crt", ".cer", ".p12", ".pfx", ".jks", ".keystore",
    ".asc", ".gpg", ".ssh",
    # Unity / 3D
    ".meta", ".unity", ".prefab", ".mat", ".fbx", ".obj", ".blend", ".physicMaterial",
    ".anim", ".controller", ".mixer", ".flare", ".guiSkin", ".guiskin",
    # Flutter / Dart / Mobile
    ".dill", ".snapshot", ".symbols", ".xcframework", ".framework", ".dSYM",
    ".xcworkspacedata", ".xcscheme", ".xcsettings", ".xcconfig", ".plist",
    ".storyboard", ".xib", ".entitlements", ".mobileprovision",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic",

    # [UNITY & SHADERS] 
    ".asset", ".dwlt", ".index", ".shader", ".shadergraph", ".cginc", ".hlsl",
    
    # [JAVA/ANDROID BUILD]
    ".aar", ".pom", ".bundle", ".iml", ".manifest", ".kts", ".pro", ".properties",

    # [MACOS/APP BUNDLES]
    ".app", ".pak", ".dat", ".nib", ".icns", ".car", ".asar", ".prof", ".hprof",
    ".lock", ".log",
    # [XCODE/IOS/MACOS PROJECT]
    ".xcassets", ".imageset", ".appiconset", ".launchimage", ".xcodeproj", ".xcworkspace",
    ".playground", ".pbs", ".storyboardc", ".podspec", ".resolved",
    # [GENERATED JS BLOBS]
    "_bin.js", "_loader.js", ".worker.js",
    # [BINARY OUTPUT]
    ".out",
    # [ICC COLOR PROFILES]
    ".icc",
    # [WINDOWS RESOURCES & SHORTCUTS]
    ".rc", ".lnk",
    # [PYINSTALLER SPEC]
    ".spec",
    # [AFFINITY DESIGNER]
    ".afdesign",
    # [UNITY SPECIFIC]
    ".inputactions", ".asmdef",
    # [ANDROID / MAVEN BINARY]
    ".srcaar",
    # [XCODE FILE LISTS]
    ".xcfilelist",
}

# Files that are completely ignored (not indexed by name OR content)
BLOCKED_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Podfile.lock", "Gemfile.lock", "composer.lock",
    "poetry.lock", "Cargo.lock", "flake.lock",
    "pubspec.lock", "project-app.lockfile", "buildscript-gradle.lockfile",
    "compile.lockfile", "packages-lock.json",
    "Thumbs.db", ".DS_Store", ".env", "Desktop.ini",
    "npm-debug.log", "yarn-error.log",
    # [GENERATED / BOILERPLATE]
    "Contents.json", "flutter_export_environment.sh", "flutter_lldbinit", "flutter_lldb_helper.py",
    "GeneratedPluginRegistrant.swift", "GeneratedPluginRegistrant.m", "GeneratedPluginRegistrant.h",
    "generated_plugin_registrant.cc", "generated_plugin_registrant.h",
    "CMakeLists.txt", "Podfile", "Podfile.lock", "pubspec.lock",
    "gradlew", "gradlew.bat", "gradle-wrapper.properties", "local.properties", "key.properties",
    "Podfile.properties.json", "modules.json", "project.pbxproj",
    "sha_debug.txt", "errors.txt", "intercomlogs.txt", "braktlumaczn.txt",
    "braktlumaczn_mapping.csv", "firestore.indexes.json", "a.out",
    # [CONFIG]
    "tsconfig.tsbuildinfo",
    # [FIREBASE CREDENTIALS]
    "google-services.json", "google-services-desktop.json",
    # [DART / FLUTTER BOILERPLATE]
    "analysis_options.yaml",
    # [WINDOWS FLUTTER RUNNER BOILERPLATE]
    "Runner.rc", "resource.h",
    "flutter_window.cpp", "flutter_window.h",
    "win32_window.cpp", "win32_window.h",
    # [LINUX FLUTTER RUNNER BOILERPLATE]
    "my_application.cc", "my_application.h",
}

# Files indexed by name (Phase 1) but skipped for content embedding (Phase 2).
# Lock files are enormous and contain only dependency hashes / version noise.
# Config boilerplate adds little semantic value for file-finding.
CONTENT_SKIP_FILENAMES = {
    # Lock files (huge, pure noise)
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Podfile.lock", "Gemfile.lock", "composer.lock",
    "poetry.lock", "Cargo.lock", "flake.lock",
    "pubspec.lock", "project-app.lockfile", "buildscript-gradle.lockfile",
    "compile.lockfile", "packages-lock.json",
    # Config boilerplate
    "tsconfig.json", "tsconfig.node.json", "tsconfig.tsbuildinfo",
    "eslint.config.js", ".eslintrc.js", ".eslintrc.json",
    "postcss.config.js", "postcss.config.cjs",
    "babel.config.js", "babel.config.json", ".babelrc",
    "metro.config.js", "jest.config.js", "jest.config.ts", "jest.setup.js",
    "webpack.config.js", "vite.config.js", "vite.config.ts",
    "tailwind.config.js", "tailwind.config.ts",
    ".prettierrc", ".prettierrc.json", ".prettierrc.js",
    ".editorconfig", ".gitignore", ".gitattributes", ".dockerignore",
    # Build / iOS / Android boilerplate
    "gradlew", "gradlew.bat", "gradle-wrapper.properties",
    "local.properties", "key.properties",
    "Podfile", "Podfile.properties.json",
    "Contents.json",  # Xcode asset catalog metadata (repeated in every .xcassets dir)
    "PrivacyInfo.xcprivacy", "module.modulemap",
    # Expo / React Native project config (boilerplate, no user content)
    "app.json", "eas.json", "nodemon.json",
    # Deployment / cloud config (boilerplate)
    "vercel.json", "firebase.json", "components.json",
    # Credential / secrets files (sensitive + not useful for search)
    "credentials.json", "firebaseConfig.js",
    "service-account.json", "secrets.json",
    "google-services.json", "google-services-desktop.json",
    # ML model metadata (generated, zero user value)
    "tokenizer_config.json", "special_tokens_map.json",
    "config_sentence_transformers.json", "sentence_bert_config.json",
    "modules.json",
    # Standard web boilerplate (no meaningful content to search)
    "manifest.json", "robots.txt", "sitemap.xml",
    "index.css", "App.css", "global.css", "globals.css", "reset.css",
    # Flutter/Mobile boilerplate
    "GeneratedPluginRegistrant.swift", "GeneratedPluginRegistrant.m",
    "GeneratedPluginRegistrant.h", "GeneratedPluginRegistrant.java",
    "generated_plugin_registrant.cc", "generated_plugin_registrant.h",
    "generated_plugins.cmake", "flutter_export_environment.sh",
    "launch_background.xml", "ic_launcher.xml", "styles.xml",
    "colors.xml", "themes.xml", "strings.xml",
    # Unity
    "ProjectVersion.txt",
    # [IDE & GRADLE CONFIGS]
    "workspace.json", "local.properties", "settings.gradle.kts", "gradle.properties",
    
    # [MASSIVE WEB LIBS]
    "tailwindcss.js"
}

# Directory names that trigger content-skip for any file found inside them.
# Files in these dirs are still indexed by name (Phase 1) but not content-embedded (Phase 2).
# Translation files produce dozens of large chunks per app with no semantic value for file-finding.
# Xcode xcassets trees contain only generated JSON boilerplate.
CONTENT_SKIP_DIRS = {
    # i18n / locale directories
    "i18n", "locales", "locale", "translations", "l10n",
    # Xcode asset catalogs (only Contents.json boilerplate inside)
    "xcassets",
    # Android build directory (gradlew, generated xml, R files, etc.)
    "android", "ios", "macos", "linux", "windows", "web",
    # Lottie animations, SVGs, and other large binary-like asset blobs
    "assets", "static", "public", "res", "resources",
    # Flutter
    "flutter_assets", "fonts",
    # Flutter platform runner directories (windows/runner, linux/runner — C++ glue code)
    "runner",
}

# File extensions whose content is never worth semantic indexing.
# These are style sheets, compiled outputs, or schema files — not human-readable search targets.
CONTENT_SKIP_EXTENSIONS = {
    ".css", ".scss", ".sass", ".less",  # stylesheets
    ".ini", ".cfg", ".conf",            # low-signal config formats
    ".entitlements",                    # verbose config
    ".svg", ".eps",                     # vector graphics
    ".csv", ".tsv",                     # large data dumps
    ".sql", ".db",                      # database dumps
    # [MOBILE BOILERPLATE]
    ".strings"
}

# Filename suffix patterns for content-skip (for variable-name files that can't be matched exactly).
CONTENT_SKIP_SUFFIXES = {
    "-Bridging-Header.h",  # Xcode Swift/ObjC bridge stubs (AppName-Bridging-Header.h)
    ".test.js", ".spec.js", ".test.ts", ".spec.ts", ".test.jsx", ".spec.jsx", ".test.tsx", ".spec.tsx", # test files
    ".min.js", ".min.css",  # minified (already in BLOCKED_EXTENSIONS but belt-and-suspenders)
    ".g.dart", ".freezed.dart", ".config.dart", # Dart generated files
    # [WASM & COMPILED BLOBS]
    "_bin.js", "loader.js", "worker.js",
    # [UNITY ENCRYPTED/PRIVATE ASSET VARIANTS]
    ".private.0",
}

# Ensure directories exist
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(PERSONAL_MEM_PATH), exist_ok=True)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# App version (used for update checks)
APP_VERSION = "0.6.1"

# Backend (Cloudflare Worker) — all AI/search calls are proxied through here
BACKEND_URL = os.environ.get("OMNI_BACKEND_URL", "https://omni-backend.heyomni.workers.dev")
# OMNI_SECRET = os.environ.get("OMNI_SECRET", "928623c24271f389cb638ce1853f0386b3728c0c8d50c3ea08166186580b3c2f")
# We need to make sure we are not hardcoding the secret here for security, but for now let's keep it consistent
OMNI_SECRET = "928623c24271f389cb638ce1853f0386b3728c0c8d50c3ea08166186580b3c2f"

from src.core.device import get_device_id
DEVICE_ID = get_device_id()

# Supabase — auth + storage
SUPABASE_URL      = os.environ.get("SUPABASE_URL",      "https://jfefwydquwvwuaeegbic.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpmZWZ3eWRxdXd2d3VhZWVnYmljIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE4NjQ5ODAsImV4cCI6MjA4NzQ0MDk4MH0.EVzcMxAMkGlJAJPz4PP75Cfn-QgooJIbi8BCv0FT-0s")

# Models (API-based, no local LLMs)
# Fast model: Groq GPT-OSS 20B (low-latency intents/actions)
FAST_MODEL_GROQ = "openai/gpt-oss-20b"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Main model: xAI Grok (via OpenAI-compatible API)
MAIN_MODEL_XAI = "grok-4-1-fast-reasoning"
MAIN_MODEL_XAI_NONREASONING = "grok-4-1-fast-non-reasoning"
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")

# Embedding Model
EMBED_MODEL_HF_ID = "Qwen/Qwen3-Embedding-0.6B"  # 1024-dim, multilingual, causal LM
EMBED_MODEL_FILENAME = "Qwen3-Embedding-0.6B"
EMBED_MODEL_PATH = os.path.join(MODEL_DIR, EMBED_MODEL_FILENAME)
EMBED_MODEL_URL = ""
CLIP_MODEL_HF_ID = "openai/clip-vit-base-patch32"  # 512-dim, image+text via CLIP

# Voice Models
OWW_WAKE_WORD_MODEL = "Hey_Omni"  # Custom wake word; set OWW_CUSTOM_MODEL_PATH to load local .onnx
OWW_CUSTOM_MODEL_PATH = os.path.join(PROJECT_ROOT, "assets", "Voice_Activation", "Hey_Omni.onnx")
OWW_DETECTION_THRESHOLD = 0.3  # Wake word confidence threshold (0.0 - 1.0)

TTS_MODEL_ID = "edge-tts"
TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-AriaNeural")

# URLs
BRAIN_HOST = "127.0.0.1"
BRAIN_PORT = 5555
BRAIN_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/ask_llm"
SEARCH_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/search"
ACTION_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/action"
ACTION_PENDING_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/action_pending"
RESOLVE_PLACE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/resolve_place"
INSTALL_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/install_plan"
FIND_PACKAGE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/find_package"
PICK_PACKAGE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/pick_package"
VERIFY_PACKAGE_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/verify_package"

# Billing (Dodo via Worker)
CHECKOUT_SESSION_URL = f"{BACKEND_URL}/v1/billing/checkout_session"

# Search API (Serper.dev -- fast Google results)
SERPER_MAIN_API_KEY = os.environ.get("SERPER_MAIN_API_KEY", "")  # used by main LLM tool calls
SERPER_FAST_API_KEY = os.environ.get("SERPER_FAST_API_KEY", "")  # used by fast action classifier

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