# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Omni is a Spotlight-like launcher with AI capabilities, structured as two processes:

### 1. Brain Service (`src/app/brain.py`) — Flask API on port 5555
A local HTTP server that handles all heavy computation. Key endpoints in `src/api/routes.py`:
- `POST /action` — Fast intent classification (Groq LLM) + web search to decide what widget to show
- `POST /ask_llm` — Full AI chat (xAI Grok) with streaming SSE support
- `POST /search` — Semantic file search via LanceDB + BGE-M3 embeddings
- `POST /embed` — Encode texts using the shared embedding model

**Model manager** (`src/services/llm/model_manager.py`) holds global singletons for the LLM clients, embedding model, and LanceDB connection. Thread locks (`main_lock`, `fast_lock`, `search_lock`) protect concurrent access. The `abort_fast_event` and `current_fast_request_id` pattern cancels stale fast-model requests when a new one arrives.

**Action classification pipeline** (`/action` endpoint): regex shortcuts → macOS settings detection → Groq fast-model intent classification (with pre-emptive web search context) → result parsing into typed action dicts (`link`, `open_app`, `install`, `person`, `place`, `calc`, `translate`, `currency`, `system_settings`).

### 2. UI (`src/app/main.py`) — PyQt6 window
- `OmniWindow` (`src/ui/window.py`) is the single main window — a floating, always-on-top search bar
- Toggle hotkey: **Cmd+Option** or **Option+Space** (macOS), **Win key** (Windows), **Ctrl+Space** (Linux/fallback)
- The window talks to the Brain via HTTP; each request type maps to a QThread worker

**Worker → Widget flow**: When the user types, `OmniWindow` fires workers in QThreadPool/QThread. Workers POST to Brain endpoints, receive typed action dicts, and emit Qt signals. The window's slots then instantiate the appropriate widget from `src/ui/widgets/`:
| Worker | Brain endpoint | Widget rendered |
|---|---|---|
| `ActionWorker` | `/action` | `LinkActionWidget`, `PersonActionWidget`, `PlaceActionWidget`, `AppActionWidget`, `CalcActionWidget`, `TranslateActionWidget`, `CurrencyActionWidget`, `SettingsActionWidget`, `InstallActionWidget`, … |
| `AIWorker` | `/ask_llm` (SSE) | `AnswerWidget`, `ThinkingWidget` |
| `SearchWorker` | `/search` | `FileActionWidget` items in `SmoothScrollListWidget` |
| `WikiWorker` | Wikipedia API | `WikiCardWidget` |
| `OGWorker` | Open Graph fetch | `OGPreviewWidget` |

### 3. Voice Listener (`src/services/voice/listener.py`) — subprocess
Launched as a separate subprocess from `main.py`. Continuously listens for the wake word "Hey Omni" (OpenWakeWord + custom ONNX model at `assets/Voice_Activation/Hey_Omni.onnx`), and toggles the UI via the IPC socket (port 5556). Transcription is currently unused.

### IPC (`src/core/ipc.py`)
TCP socket on port 5556. Commands: `TOGGLE`, `TOGGLE_MANUAL`, `QUERY:<text>`, `PARTIAL:<text>`, `STATUS:<text>`. A second instance of the app sends `TOGGLE_MANUAL` to the first and exits — mimicking Spotlight's toggle-on-relaunch behavior.

### File Search (`src/services/search/`)
- `indexer.py` — One-time index build: walks the filesystem, extracts text from PDF/DOCX/XLSX/PPTX, embeds via BGE-M3, stores in LanceDB at `~/.local/share/ai-memory-db/`
- `watcher.py` — Background process that watches for filesystem changes and incrementally updates the index
- The Brain's `/search` endpoint queries LanceDB with semantic search (distance threshold < 1.1)

### Key Config (`src/core/config.py`)
- All API keys, model names, ports, paths, and ignored directories for indexing are centralized here
- `.env` is loaded from the project root (walks up 6 dirs) and from `~/.config/omni/.env`
- **Fast model**: Groq `openai/gpt-oss-20b` — used for action classification
- **Main model**: xAI `grok-4-1-fast-reasoning` — used for full AI chat
- **Embedding model**: `BAAI/bge-m3` via fastembed
- **TTS**: `hexgrad/Kokoro-82M`
- **ASR**: (Currently unused)

### Styles & Theming (`src/ui/styles.py`)
Themes (light/dark) are defined in `THEMES` dict. The window detects macOS system appearance via `NSUserDefaults` and applies the corresponding theme. `get_style_sheet()` generates the full Qt stylesheet.
