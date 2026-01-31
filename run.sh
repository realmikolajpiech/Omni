#!/usr/bin/env bash

echo "Starting Omni..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Requesting root privileges for global hotkey support (Ctrl+Space)..."
    # Preserve environment (-E) and use full path to python3 to support venvs
    sudo -E "$(which python3)" run.py
else
    python3 run.py
fi

read -p "Press Enter to continue..."