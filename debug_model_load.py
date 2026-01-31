
import sys
import os

# Add src to path if needed, but we just want to test llama_cpp directly
try:
    from llama_cpp import Llama
    print("Imported Llama successfully")
except ImportError:
    print("Failed to import Llama")
    sys.exit(1)

model_path = os.path.expanduser("~/.local/share/ai-models/Qwen3-VL-4B-Thinking-Q4_K_M.gguf")
mmproj_path = os.path.expanduser("~/.local/share/ai-models/mmproj-Qwen3-VL-4B-Thinking-F16.gguf")

print(f"Testing Model Path: {model_path}")
print(f"Exists: {os.path.exists(model_path)}")
print(f"Size: {os.path.getsize(model_path) if os.path.exists(model_path) else 'N/A'}")

print(f"Testing MMProj Path: {mmproj_path}")
print(f"Exists: {os.path.exists(mmproj_path)}")
print(f"Size: {os.path.getsize(mmproj_path) if os.path.exists(mmproj_path) else 'N/A'}")

print("\n--- Test 1: Load Model ONLY (No Chat Handler, No MMProj) ---")
try:
    llm = Llama(
        model_path=model_path,
        n_ctx=2048,
        verbose=True
    )
    print("SUCCESS: Model loaded standalone")
    del llm
except Exception as e:
    print(f"FAIL: Model standalone load failed: {e}")

print("\n--- Test 2: Load Model + MMProj (Generic Handler) ---")
try:
    from llama_cpp.llama_chat_format import Llava15ChatHandler
    ch = Llava15ChatHandler(clip_model_path=mmproj_path)
    llm = Llama(
        model_path=model_path,
        chat_handler=ch,
        n_ctx=2048,
        verbose=True
    )
    print("SUCCESS: Model + Llava15Handler loaded")
    del llm
except Exception as e:
    print(f"FAIL: Model + Llava15Handler failed: {e}")

print("\n--- Test 3: Load Model + MMProj (Qwen25VL Handler) ---")
try:
    from llama_cpp.llama_chat_format import Qwen25VLChatHandler
    ch = Qwen25VLChatHandler(clip_model_path=mmproj_path)
    llm = Llama(
        model_path=model_path,
        chat_handler=ch,
        n_ctx=2048,
        verbose=True
    )
    print("SUCCESS: Model + Qwen25VLHandler loaded")
    del llm
except ImportError:
    print("SKIP: Qwen25VLChatHandler not available")
except Exception as e:
    print(f"FAIL: Model + Qwen25VLHandler failed: {e}")
