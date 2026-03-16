#!/usr/bin/env bash
# dev.sh — Development launcher for Omni.
# Starts every service individually with proper log files and clean shutdown.
# Simulates the fully-installed app environment.

cd "$(dirname "$0")" || exit 1

PYTHON_CMD="./venv/bin/python3"
mkdir -p logs

if [ ! -f "$PYTHON_CMD" ]; then
    echo "Error: venv not found. Run ./run.sh once first to create it."
    exit 1
fi

# ── Stop any stale instances ───────────────────────────────────────────────
echo "Stopping old instances..."
pkill -f "src/app/brain.py"                  2>/dev/null || true
pkill -f "run.py brain"                      2>/dev/null || true
pkill -f "src/services/search/watcher.py"    2>/dev/null || true
pkill -f "src/services/voice/listener.py"    2>/dev/null || true
lsof -ti :5555 | xargs kill -9               2>/dev/null || true
sleep 0.8

# ── Environment ───────────────────────────────────────────────────────────
export TRANSFORMERS_VERBOSITY=error
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export PYTHONUTF8=1
export OMNI_LISTENER_LAUNCHED=1   # we manage the listener below — prevent main.py double-starting it

# ── File Watcher ──────────────────────────────────────────────────────────
MARKER="$HOME/.local/share/ai-memory-db/.indexed"
WATCHER_PID=""
if [ -f "$MARKER" ]; then
    echo "Starting File Watcher...   → logs/watcher.log"
    $PYTHON_CMD src/services/search/watcher.py > logs/watcher.log 2>&1 &
    WATCHER_PID=$!
else
    echo "⚠  Watcher skipped — index DB not built yet."
    echo "   Run: $PYTHON_CMD src/services/search/indexer.py"
    echo "   Then restart dev.sh for full file search support."
fi

# ── Voice Listener ────────────────────────────────────────────────────────
LISTENER_PID=""
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Starting Voice Listener... → logs/listener.log"
    $PYTHON_CMD src/services/voice/listener.py > logs/listener.log 2>&1 &
    LISTENER_PID=$!
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────
_CLEANED=0
cleanup() {
    [ "$_CLEANED" -eq 1 ] && return
    _CLEANED=1
    echo ""
    echo "Shutting down all services..."
    [ -n "$WATCHER_PID"  ] && kill "$WATCHER_PID"  2>/dev/null || true
    [ -n "$LISTENER_PID" ] && kill "$LISTENER_PID" 2>/dev/null || true
    lsof -ti :5555 | xargs kill -9 2>/dev/null || true
    echo "Done."
}
trap cleanup INT TERM EXIT

# ── Start UI + Brain (foreground — brain is child subprocess, no Dock icon) ──
echo ""
echo "All services running. Starting Omni (brain + UI)..."
echo "  Brain log: logs/brain.log"
echo "  Ctrl+C to stop everything."
echo ""

$PYTHON_CMD run.py --dev
