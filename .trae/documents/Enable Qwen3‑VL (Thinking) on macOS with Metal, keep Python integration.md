## Approach Overview
- Keep Python app intact; upgrade backend to latest libllama that supports Qwen3‑VL (Thinking) and link it from Python.
- No fallback to Qwen2.5; we will make Qwen3‑VL work.

## Steps
1. Build latest llama.cpp with Metal:
- git clone https://github.com/ggerganov/llama.cpp
- cmake -DGGML_METAL=ON -S . -B build
- cmake --build build -j
- Result: build/lib/libllama.dylib

2. Link Python binding to external libllama:
- Set env: LLAMA_CPP_BUILD=OFF
- Set env: LLAMA_CPP_LIB=/absolute/path/to/build/lib/libllama.dylib
- Reinstall: pip install --force-reinstall --no-cache-dir llama-cpp-python

3. Adjust loader:
- n_gpu_layers=-1 (full Metal offload)
- n_threads=6 (M4 Air sweet spot)
- Remove forced chat_format; rely on GGUF chat_template for Qwen3‑VL

4. Verify Qwen3‑VL load:
- Load Qwen3VL-4B-Thinking-Q4_K_M.gguf
- Confirm logs show architecture recognized (no unknown-arch error), Metal device line present.

5. Streaming performance check:
- Generate short prompt; verify fast token streaming
- Enable verbose logging temporarily to confirm device and kernel usage

## Contingency (still Qwen3‑VL)
- If Python binding fails to load despite latest libllama, run llama.cpp server:
- ./build/bin/server -m /path/Qwen3VL-4B-Thinking-Q4_K_M.gguf -ngl -1
- Point Python to HTTP server for generation; keep Qwen3‑VL and no fallback.

## Deliverables
- Updated installation script to build/attach latest libllama
- Verified Qwen3‑VL (Thinking) running on Metal with fast streaming