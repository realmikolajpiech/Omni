#!/usr/bin/env bash

echo "Starting Omni..."

# Check and install dependencies first (as current user, not root if possible, unless script started as root)
if [ "$EUID" -ne 0 ]; then
    echo "Checking dependencies..."
    python3 setup.py
else
    # If running as root, try to run setup as the SUDO_USER to avoid root-owned pip packages
    if [ -n "$SUDO_USER" ]; then
        echo "Running setup as $SUDO_USER..."
        sudo -u $SUDO_USER python3 setup.py
    else
        echo "Running setup as root (not recommended but necessary)..."
        python3 setup.py
    fi
fi

# Kill previous instances
echo "Cleaning up old instances..."
sudo pkill -f "run.py" || true
sudo pkill -f "src/services/voice/listener.py" || true
sudo pkill -f "src/app/brain.py" || true

# Set environment variables to suppress some warnings
export TRANSFORMERS_VERBOSITY=error
export HF_HUB_DISABLE_SYMLINKS_WARNING=1

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Requesting root privileges for global hotkey support (Ctrl+Space)..."
    # Ensure we use the python that has the packages installed
    PYTHON_CMD=$(which python3)
    sudo -E "$PYTHON_CMD" run.py
else
    python3 run.py
fi

read -p "Press Enter to continue..."
