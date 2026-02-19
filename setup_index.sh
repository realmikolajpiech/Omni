#!/usr/bin/env bash
# One-time initial file indexing.
# Run this once after installation to build the search database.
# After that, the watcher (started by run.sh) keeps it up to date.
#
# Options:
#   --skip-filenames   Skip Phase 1 (filename indexing) and start from Phase 2.
#                      Useful if filenames are already indexed and you only need
#                      to (re)build content/image indexes.
#   --dry-run          Preview what files would be indexed without indexing.

set -e

EXTRA_ARGS=""
for arg in "$@"; do
    case "$arg" in
        --skip-filenames|--dry-run) EXTRA_ARGS="$EXTRA_ARGS $arg" ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

MARKER="$HOME/.local/share/ai-memory-db/.indexed"

# Allow --dry-run and --skip-filenames to bypass the marker check
if [ -z "$EXTRA_ARGS" ] && [ -f "$MARKER" ]; then
    echo "Index already exists ($MARKER)."
    echo "To force a full re-index, delete the marker and run again:"
    echo "  rm \"$MARKER\" && ./setup_index.sh"
    exit 0
fi

PYTHON_CMD="./venv/bin/python3"
if [ ! -f "$PYTHON_CMD" ]; then
    echo "Error: Virtual environment not found. Run install_mac.sh first."
    exit 1
fi

export TRANSFORMERS_VERBOSITY=error
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export PYTHONUTF8=1

echo "Starting brain service for embedding..."
mkdir -p logs
$PYTHON_CMD run.py brain > logs/brain_setup.log 2>&1 &
BRAIN_PID=$!

cleanup() {
    echo "Stopping brain service (pid $BRAIN_PID)..."
    kill "$BRAIN_PID" 2>/dev/null || true
    wait "$BRAIN_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Waiting for brain service to become available..."
for i in $(seq 1 60); do
    if curl -s -o /dev/null -w '' http://127.0.0.1:5555/health 2>/dev/null; then
        echo "Brain service is up."
        break
    fi
    if ! kill -0 "$BRAIN_PID" 2>/dev/null; then
        echo "Error: Brain service exited unexpectedly. Check logs/brain_setup.log"
        exit 1
    fi
    sleep 2
done

echo ""
echo "=== Starting initial file indexing ==="
echo "This may take a while depending on the number of files in your home directory."
echo ""

$PYTHON_CMD src/services/search/indexer.py $EXTRA_ARGS

echo ""
echo "=== Indexing complete ==="
echo "You can now start the app normally with ./run.sh"
