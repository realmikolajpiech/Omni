#!/usr/bin/env bash

echo "Starting Omni..."

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Determine Python command
# Check if venv exists and use it
if [ -d "venv" ]; then
    PYTHON_CMD="./venv/bin/python3"
# Check for Python 3.12 (Homebrew) first
elif command -v python3.12 >/dev/null 2>&1; then
    PYTHON_CMD=python3.12
elif [ -f "$(brew --prefix)/bin/python3.12" ]; then
    PYTHON_CMD="$(brew --prefix)/bin/python3.12"
elif command_exists python3; then
    PYTHON_CMD=python3
elif command_exists python; then
    PYTHON_CMD=python
else
    echo "Error: Python is not installed."
    exit 1
fi

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

# Environment Variables
export TRANSFORMERS_VERBOSITY=error
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export PYTHONUTF8=1

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
