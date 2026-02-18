#!/bin/bash
set -e

echo "--- Omni Setup for macOS ---"

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "Error: Homebrew not found. Please install Homebrew first: https://brew.sh/"
    exit 1
fi

echo "Step 0: Checking Python Version..."
# Ensure we have a modern Python (3.10+) via Homebrew, as system python 3.9.6 is too old
if ! brew list python@3.12 &>/dev/null; then
    echo "Installing Python 3.12 via Homebrew (System Python 3.9 is too old)..."
    brew install python@3.12
fi

# Link it or find the path
PYTHON_CMD="python3.12"
if ! command -v $PYTHON_CMD &> /dev/null; then
    # Try finding where brew put it
    PYTHON_CMD=$(brew --prefix)/bin/python3.12
fi

echo "Using Python: $($PYTHON_CMD --version)"

# Create Virtual Environment to avoid PEP 668 errors
VENV_DIR="venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv $VENV_DIR
fi

# Activate Venv
source $VENV_DIR/bin/activate
PYTHON_CMD="python3" # Inside venv, python3 is the correct one

echo "Step 1: Installing system dependencies..."
# ffmpeg: required for audio processing (qwen-asr)
# portaudio: required for sounddevice
brew install ffmpeg portaudio

echo "Step 2: Installing Python dependencies..."
echo "Installing requirements..."
echo "NOTE: This may take a while (downloading PyTorch ~2GB+). Please be patient."
echo "Ignore any 'warning' messages from the build process."
# Use --no-deps for qwen-asr first to avoid conflict checks, then install everything else
$PYTHON_CMD -m pip install torch torchvision torchaudio accelerate transformers
$PYTHON_CMD -m pip install -r requirements.txt
echo "Installing qwen-asr directly from GitHub to avoid metadata issues..."
$PYTHON_CMD -m pip install git+https://github.com/QwenLM/Qwen3-ASR.git

echo "Step 3: Verifying Environment..."
$PYTHON_CMD -c "import torch; print(f'Torch MPS Available: {torch.backends.mps.is_available()}')"

echo "Setup complete! You can now run the app with ./run.sh"
