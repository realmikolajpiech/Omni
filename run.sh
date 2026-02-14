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

# Environment Variables
export TRANSFORMERS_VERBOSITY=error
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export PYTHONUTF8=1
LLAMA_LIB_PATH="$(pwd)/.deps/llama.cpp/build/lib/libllama.dylib"
if [ -f "$LLAMA_LIB_PATH" ]; then
  export LLAMA_CPP_LIB="$LLAMA_LIB_PATH"
  export LLAMA_CPP_LOG=1
fi

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
