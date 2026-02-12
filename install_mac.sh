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
# cmake: required for building llama-cpp-python
brew install ffmpeg portaudio cmake

echo "Step 2: Installing Python dependencies..."

# Build latest llama.cpp (libllama) with Metal and link Python binding to it
echo "Building latest llama.cpp (libllama) with Metal..."
LLAMA_DIR=".deps/llama.cpp"
mkdir -p .deps
if [ ! -d "$LLAMA_DIR" ]; then
    git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
else
    echo "llama.cpp already present, pulling latest..."
    (cd "$LLAMA_DIR" && git pull --ff-only)
fi
cmake -DGGML_METAL=ON -S "$LLAMA_DIR" -B "$LLAMA_DIR/build"
cmake --build "$LLAMA_DIR/build" -j
LLAMA_LIB="$(pwd)/$LLAMA_DIR/build/lib/libllama.dylib"
echo "Using libllama at: $LLAMA_LIB"

echo "Installing llama-cpp-python bound to external libllama..."
LLAMA_CPP_BUILD=OFF LLAMA_CPP_LIB="$LLAMA_LIB" $PYTHON_CMD -m pip install --force-reinstall --no-cache-dir llama-cpp-python

# Install the rest of the requirements
# We skip llama-cpp-python here if it satisfies the requirement, but since we just installed latest, it should be fine.
# If requirements.txt has a pinned version, this might downgrade it. 
# Current requirements.txt does not pin llama-cpp-python version.
echo "Installing other requirements..."
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
