#!/usr/bin/env bash

echo "Starting Omni..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Requesting root privileges for global hotkey support (Ctrl+Space)..."
    
    sudo -E "$(which python3)" run.py
else
    python3 run.py
fi

read -p "Press Enter to continue..."