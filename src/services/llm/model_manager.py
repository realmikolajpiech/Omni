import os
import logging
import threading
import requests
from src.core.config import (
    FAST_MODEL_PATH, FAST_MODEL_FILENAME, FAST_MODEL_URL,
    MAIN_MODEL_PATH, MAIN_MODEL_FILENAME, MAIN_MODEL_URL,
    MMPROJ_PATH, MMPROJ_URL, MMPROJ_FILENAME,
    DB_PATH
)

# Global State
llm = None
fast_model = None
embed_model = None
db_conn = None
vision_model = None
init_error = None

# Thread Locks
main_lock = threading.Lock()
fast_lock = threading.Lock()
abort_fast_event = threading.Event()

def download_file(url, dest_path):
    logging.info(f"Downloading {url} to {dest_path}...")
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        logging.info("Download complete.")
        return True
    except Exception as e:
        logging.error(f"Download failed: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

def ensure_imports():
    global init_error
    try:
        from llama_cpp import Llama
        return Llama
    except Exception as e:
        logging.error(f"Import Error: {e}")
        init_error = str(e)
        return None

def ensure_fast_model():
    """Loads the smaller, faster model for actions."""
    global fast_model, init_error
    if fast_model: return

    Llama = ensure_imports()
    if not Llama: return

    if not os.path.exists(FAST_MODEL_PATH):
        logging.info("Fast model not found. Downloading...")
        if not download_file(FAST_MODEL_URL, FAST_MODEL_PATH):
            init_error = f"Failed to download fast model from {FAST_MODEL_URL}"
            logging.error(init_error)
            return

    # Check if we should load (lazy loading optimization)
    # If the user hasn't typed anything yet, maybe we delay?
    # But current logic is "ensure_fast_model" is called by routes.
    
    with fast_lock:
        if fast_model: return
        logging.info(f"Loading Fast Model: {FAST_MODEL_FILENAME}")
        try:
            # Suppress stdout/stderr from llama.cpp
            # This is tricky in python, but we can try setting verbose=False which we already do.
            # We can also redirect C-level stdout if needed, but let's stick to verbose=False.
            
            fast_model = Llama(
                model_path=FAST_MODEL_PATH,
                chat_format="chatml", # Force standard ChatML to bypass Qwen3 thinking
                n_ctx=8192, # Increased context to avoid warnings (model supports 32k)
                n_threads=4,
                n_gpu_layers=-1,
                verbose=False
            )
            logging.info("Fast Model Loaded.")
        except Exception as e:
            logging.error(f"Fast Model Load Error: {e}")
            init_error = str(e)

def ensure_main_model():
    """Loads the larger, main model for chat."""
    global llm, init_error, embed_model, db_conn
    
    # 1. DB & Embeddings (Shared)
    if db_conn is None:
        try:
            if os.path.exists(DB_PATH):
                import lancedb
                db_conn = lancedb.connect(DB_PATH)
                logging.info(f"Connected to LanceDB at {DB_PATH}")
        except Exception as e: logging.error(f"DB Error: {e}")

    if embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            embed_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        except Exception as e: logging.error(f"Embeddings Error: {e}")

    # 2. Main Model
    if llm: return

    Llama = ensure_imports()
    if not Llama: return

    if not os.path.exists(MAIN_MODEL_PATH):
        # ... download logic ...
        pass

    # TRANSFORMERS PATH (If user requests Hugging Face native loading)
    # We will try to detect if we should use Transformers based on filename/config
    # But for now, let's keep GGUF as default unless it fails or is explicitly requested.
    # However, the user specifically asked for Transformers implementation for Qwen3-VL-4B.
    
    # Check if we are using the "Transformers" model (based on config variable name or content)
    # Since we can't easily change the global structure, we'll add a branch here.
    
    USE_TRANSFORMERS = "Qwen3-VL-4B-Instruct" in MAIN_MODEL_FILENAME and "gguf" not in MAIN_MODEL_FILENAME.lower()
    
    if USE_TRANSFORMERS:
        with main_lock:
            if llm: return
            logging.info(f"Loading Main Model via Transformers: {MAIN_MODEL_FILENAME}")
            try:
                from transformers import AutoProcessor
                import torch
                
                # Check for bitsandbytes
                try:
                    import bitsandbytes
                except ImportError:
                     logging.error("bitsandbytes not installed, falling back to non-quantized load (Risk of OOM)")
                     raise ImportError("Please install bitsandbytes>=0.46.1")

                # We need to use the right class. Qwen3-VL uses Qwen2_5_VL code in transformers usually
                # or Qwen3VLForConditionalGeneration if updated.
                
                try:
                    from transformers import Qwen3VLForConditionalGeneration
                    ModelClass = Qwen3VLForConditionalGeneration
                except ImportError:
                    # Fallback to Qwen2_5_VL if Qwen3 specific class not found
                    from transformers import Qwen2_5_VLForConditionalGeneration
                    ModelClass = Qwen2_5_VLForConditionalGeneration

                # Define quantization config for 4-bit loading (similar to GGUF Q4)
                # This drastically reduces VRAM usage (from ~8GB to ~3GB for 4B model)
                from transformers import BitsAndBytesConfig
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4"
                )

                model = ModelClass.from_pretrained(
                    MAIN_MODEL_FILENAME, 
                    quantization_config=quantization_config, # Apply 4-bit quantization
                    device_map="auto",
                    trust_remote_code=True,
                    low_cpu_mem_usage=True
                )
                processor = AutoProcessor.from_pretrained(MAIN_MODEL_FILENAME, trust_remote_code=True)
                
                # Wrap it in a class that mimics Llama.cpp interface for compatibility
                class TransformersWrapper:
                    def __init__(self, model, processor):
                        self.model = model
                        self.processor = processor
                        
                    def create_chat_completion(self, messages, max_tokens=1024, temperature=0.7, **kwargs):
                        # Convert messages to text/inputs
                        # This is a simplified wrapper.
                        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                        
                        # Handle images if present in messages (needs complex parsing)
                        # For now, let's assume text-only or simple image structure
                        
                        inputs = self.processor(
                            text=[text],
                            images=None, 
                            padding=True,
                            return_tensors="pt"
                        ).to(self.model.device)
                        
                        generated_ids = self.model.generate(**inputs, max_new_tokens=max_tokens)
                        output_text = self.processor.batch_decode(
                            generated_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
                        )[0]
                        
                        # Remove input text from output
                        # The model might return the input prompt in the output, so we should clean it.
                        if output_text.startswith(text):
                            output_text = output_text[len(text):]
                        elif "assistant\n" in output_text:
                            output_text = output_text.split("assistant\n", 1)[1]
                        
                        return {
                            "choices": [{
                                "message": {
                                    "role": "assistant",
                                    "content": output_text
                                }
                            }]
                        }

                llm = TransformersWrapper(model, processor)
                logging.info("Main Model Loaded (Transformers).")
                return

            except Exception as e:
                logging.error(f"Transformers Load Error: {e}")
                init_error = str(e)
                return

    # Standard GGUF Path
    if not os.path.exists(MAIN_MODEL_PATH):
        logging.info("Main model not found. Downloading...")
        if not download_file(MAIN_MODEL_URL, MAIN_MODEL_PATH):
            init_error = "Failed to download main model."
            return

    # Ensure MMPROJ for Vision Support
    chat_handler = None
    if MMPROJ_PATH and MMPROJ_URL:
        if not os.path.exists(MMPROJ_PATH):
             logging.info(f"MMPROJ not found. Downloading {MMPROJ_FILENAME}...")
             if not download_file(MMPROJ_URL, MMPROJ_PATH):
                 logging.error("Failed to download MMPROJ. Vision might not work.")
        
        if os.path.exists(MMPROJ_PATH):
            try:
                # Try Qwen2.5 VL Handler (Newer llama-cpp-python)
                from llama_cpp.llama_chat_format import Qwen25VLChatHandler
                chat_handler = Qwen25VLChatHandler(clip_model_path=MMPROJ_PATH)
                logging.info("Initialized Qwen25VLChatHandler for Vision Support.")
            except ImportError:
                try:
                    # Try Qwen2 VL Handler (Older)
                    from llama_cpp.llama_chat_format import Qwen2VLChatHandler
                    chat_handler = Qwen2VLChatHandler(clip_model_path=MMPROJ_PATH)
                    logging.info("Initialized Qwen2VLChatHandler for Vision Support.")
                except ImportError:
                    logging.warning("Qwen25VLChatHandler/Qwen2VLChatHandler not found. Trying generic/fallback.")
                    # Fallback: Try Llava15ChatHandler
                    try:
                        from llama_cpp.llama_chat_format import Llava15ChatHandler
                        chat_handler = Llava15ChatHandler(clip_model_path=MMPROJ_PATH)
                        logging.info("Initialized Llava15ChatHandler as fallback.")
                    except:
                        logging.error("Could not initialize any ChatHandler with MMPROJ.")

    with main_lock:
        if llm: return
        logging.info(f"Loading Main Model: {MAIN_MODEL_FILENAME}")
        try:
            from llama_cpp import Llama
            
            # If we have a chat_handler, use it. 
            # Note: Qwen2-VL usually requires n_ctx to be large enough for image tokens.
            llm = Llama(
                model_path=MAIN_MODEL_PATH,
                chat_handler=chat_handler,
                n_ctx=32768, # Increased for Vision/Long Context
                n_threads=4,
                n_gpu_layers=-1,
                verbose=False
            )
            logging.info("Main Model Loaded.")
        except Exception as e:
            logging.error(f"Main Model Load Error: {e}")
            init_error = str(e)

def ensure_model_loaded():
    ensure_fast_model()
    ensure_main_model()
