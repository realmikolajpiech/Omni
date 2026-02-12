#!/bin/bash
# Script to launch the llama.cpp server for Qwen3-VL
# This ensures we use the official binary with correct Qwen3 architecture support and Metal

MODEL_PATH="$HOME/.local/share/ai-models/Qwen3VL-4B-Thinking-Q4_K_M.gguf"
MMPROJ_PATH="$HOME/.local/share/ai-models/mmproj-Qwen3VL-4B-Thinking-F16.gguf"
SERVER_BIN="./llama_cpp_server/build/bin/llama-server"
PORT=8081

if [ ! -f "$SERVER_BIN" ]; then
    echo "Error: Server binary not found at $SERVER_BIN"
    exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
    echo "Error: Model not found at $MODEL_PATH"
    exit 1
fi

if [ ! -f "$MMPROJ_PATH" ]; then
    echo "Warning: MMPROJ not found at $MMPROJ_PATH. Vision will be disabled."
fi

echo "Starting Qwen3-VL Server on port $PORT..."
echo "Model: $MODEL_PATH"

# -ngl -1: Offload all layers to GPU (Metal)
# -c 32768: Context size
# --port: Port to listen on
# --host: Bind to localhost
# --nobrowser: Don't open browser
# -np 4: Parallel slots (optional, for concurrency)
# --mmproj: Vision adapter
$SERVER_BIN \
    -m "$MODEL_PATH" \
    --mmproj "$MMPROJ_PATH" \
    -ngl -1 \
    -c 8192 \
    --port $PORT \
    --host 127.0.0.1 \
    -np 1 \
    --log-disable \
    > "$HOME/.config/omni/qwen_server.log" 2>&1 &
PID=$!
echo $PID > "$HOME/.config/omni/qwen_server.pid"
echo "Server started with PID $PID"
