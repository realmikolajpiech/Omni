#!/usr/bin/env bash

echo "Starting Omni..."

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Determine Python command
# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    # Try to find a good python3
    if command -v python3.12 >/dev/null 2>&1; then
        SYSTEM_PYTHON=python3.12
    elif command -v python3 >/dev/null 2>&1; then
        SYSTEM_PYTHON=python3
    else
        echo "Error: Python 3 is required but not found."
        exit 1
    fi
    
    $SYSTEM_PYTHON -m venv venv
    if [ $? -ne 0 ]; then
        echo "Error creating virtual environment."
        exit 1
    fi
fi

PYTHON_CMD="./venv/bin/python3"

# Check dependencies
echo "Checking dependencies..."
$PYTHON_CMD setup.py
if [ $? -ne 0 ]; then
    echo "Dependency check failed."
    exit 1
fi

# Cleanup old instances
echo "Cleaning up old instances..."
pkill -f "run.py" || true
pkill -f "src/services/voice/listener.py" || true
pkill -f "src/services/search/indexer.py" || true
pkill -f "src/services/search/watcher.py" || true

if [ -d "searxng_local" ] && [ -f "searxng/start_searxng.py" ]; then
    echo "Ensuring local SearXNG is running..."

    $PYTHON_CMD -c "import flask_babel" >/dev/null 2>&1 || $PYTHON_CMD -m pip install -r searxng_local/requirements.txt

    SEARXNG_STATUS="$(curl -s -m 1 -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/search?q=ping&format=json" -H "User-Agent: OmniOS/1.0" || true)"
    if [ "$SEARXNG_STATUS" != "200" ]; then
        mkdir -p logs
        nohup $PYTHON_CMD searxng/start_searxng.py > logs/searxng.log 2>&1 &
        STARTED=0
        i=0
        while [ $i -lt 10 ]; do
            sleep 0.5
            SEARXNG_STATUS="$(curl -s -m 2 -o /dev/null -w "%{http_code}" "http://127.0.0.1:8080/search?q=ping&format=json" -H "User-Agent: OmniOS/1.0" || true)"
            if [ "$SEARXNG_STATUS" = "200" ]; then
                STARTED=1
                break
            fi
            i=$((i + 1))
        done
        if [ "$STARTED" = "1" ]; then
            echo "Local SearXNG started."
        else
            echo "Warning: Local SearXNG did not start (see logs/searxng.log)."
        fi
    else
        echo "Local SearXNG already running."
    fi
fi

# Environment Variables
export TRANSFORMERS_VERBOSITY=error
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export PYTHONUTF8=1

# Check if initial indexing has been done
MARKER="$HOME/.local/share/ai-memory-db/.indexed"
if [ ! -f "$MARKER" ]; then
    echo "WARNING: Initial file index not found."
    echo "Run ./setup_index.sh once to build the search database."
    echo "File search will not work until indexing is complete."
    echo ""
fi

# Start File Watcher in background
echo "Starting File Watcher in background..."
mkdir -p logs
nohup $PYTHON_CMD src/services/search/watcher.py > logs/watcher.log 2>&1 &

# Run
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "macOS detected."
    # Check if we are running with sudo already?
    # Global hotkeys might need accessibility permissions rather than sudo.
    # Sudo is often needed for keyboard monitoring if not signed.
    if [ "$EUID" -ne 0 ]; then
        echo "Note: If global hotkeys don't work, grant Terminal accessibility permissions."
    fi
    $PYTHON_CMD run.py
else
    $PYTHON_CMD run.py
fi
