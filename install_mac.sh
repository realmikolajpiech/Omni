#!/bin/bash
# install_mac.sh — Dev environment setup (run from source, no PyInstaller).
# End users should use the DMG installer instead.
set -e

echo "--- Omni Dev Setup for macOS ---"

# ── Homebrew ──────────────────────────────────────────────────────────────────
if ! command -v brew &> /dev/null; then
    echo "Error: Homebrew not found. Install it first: https://brew.sh/"
    exit 1
fi

# ── Python 3.12 ───────────────────────────────────────────────────────────────
echo "Step 0: Checking Python..."
if ! brew list python@3.12 &>/dev/null; then
    echo "Installing Python 3.12 via Homebrew..."
    brew install python@3.12
fi

PYTHON_CMD="python3.12"
if ! command -v $PYTHON_CMD &> /dev/null; then
    PYTHON_CMD=$(brew --prefix)/bin/python3.12
fi
echo "Using: $($PYTHON_CMD --version)"

# ── Virtual environment ───────────────────────────────────────────────────────
VENV_DIR="venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv $VENV_DIR
fi
source $VENV_DIR/bin/activate
PYTHON_CMD="python3"

# ── System dependencies ───────────────────────────────────────────────────────
echo "Step 1: Installing system dependencies..."
# portaudio: required by sounddevice (microphone input / wake word)
brew install portaudio

# ── Python dependencies ───────────────────────────────────────────────────────
echo "Step 2: Installing Python dependencies..."
$PYTHON_CMD -m pip install --upgrade pip
$PYTHON_CMD -m pip install -r requirements.txt

# ── Verify ────────────────────────────────────────────────────────────────────
echo "Step 3: Verifying..."
$PYTHON_CMD -c "
import embed_anything, lancedb, openai, flask, PyQt6
print('All core dependencies OK.')
"

echo ""
echo "Setup complete! Run the app with: ./run.sh"
