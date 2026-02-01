import os
import logging
import threading
import requests
import torch
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer, AutoProcessor, BitsAndBytesConfig
except ImportError:
    logging.warning("transformers not fully installed, some models may not load.")

from src.core.config import (
    FAST_MODEL_PATH, FAST_MODEL_FILENAME, FAST_MODEL_URL,
    FAST_MODEL_HF_ID,
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

# Fast model request queue for cancellation
current_fast_request_id = None
fast_request_queue = []
fast_queue_lock = threading.Lock()

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
    """Loads the smaller, faster model for actions (transformers)."""
    global fast_model, init_error
    if fast_model: return

    with fast_lock:
        if fast_model: return
        logging.info(f"Loading Fast Model (Transformers): {FAST_MODEL_HF_ID}")
        try:
            cuda_ok = torch.cuda.is_available()
            if cuda_ok:
                logging.info(f"CUDA available: {torch.cuda.get_device_name(0)}")

            # Use bfloat16 for Ampere+ GPUs (RTX 30 series) for best speed
            if cuda_ok:
                props = torch.cuda.get_device_properties(0)
                if props.major >= 8: # Ampere or newer
                    dtype = torch.bfloat16
                else:
                    dtype = torch.float16
            else:
                dtype = torch.float32
                
            logging.info(f"Using dtype: {dtype}")
            
            # Use flash_attention_2 if available (fastest), fallback to sdpa
            attn_implementation = "flash_attention_2" if cuda_ok else "eager"
            
            tokenizer = AutoTokenizer.from_pretrained(FAST_MODEL_HF_ID, trust_remote_code=True)

            try:
                # Enable TF32 for better performance on Ampere (RTX 3060)
                if cuda_ok:
                    torch.backends.cuda.matmul.allow_tf32 = True
                    torch.backends.cudnn.allow_tf32 = True

                # Try flash_attention_2 first for maximum speed
                try:
                    model = AutoModelForCausalLM.from_pretrained(
                        FAST_MODEL_HF_ID,
                        torch_dtype=dtype,
                        device_map="cuda:0" if cuda_ok else "cpu",
                        trust_remote_code=True,
                        attn_implementation=attn_implementation,
                        low_cpu_mem_usage=True
                    )
                except Exception as attn_err:
                    # Fallback to sdpa if flash_attention_2 fails
                    logging.warning(f"flash_attention_2 failed ({attn_err}), falling back to sdpa")
                    model = AutoModelForCausalLM.from_pretrained(
                        FAST_MODEL_HF_ID,
                        torch_dtype=dtype,
                        device_map="cuda:0" if cuda_ok else "cpu",
                        trust_remote_code=True,
                        attn_implementation="sdpa",
                        low_cpu_mem_usage=True
                    )
                
                # Force conversion to dtype
                if cuda_ok:
                    model = model.to(dtype=dtype, device="cuda:0")
                
                model.eval()
                
                # Warmup inference to initialize CUDA kernels
                if cuda_ok:
                    logging.info("Warming up fast model...")
                    warmup_input = tokenizer("Hi", return_tensors="pt").to("cuda:0")
                    with torch.inference_mode():
                        model.generate(**warmup_input, max_new_tokens=2)
                    torch.cuda.synchronize()
                    logging.info("Fast model warmup complete.")
            except Exception as oom:
                if cuda_ok and "out of memory" in str(oom).lower():
                    logging.warning("Fast model OOM on GPU, falling back to device_map=auto")
                    torch.cuda.empty_cache()
                    model = AutoModelForCausalLM.from_pretrained(
                        FAST_MODEL_HF_ID,
                        torch_dtype=dtype,
                        device_map="auto",
                        trust_remote_code=True,
                        low_cpu_mem_usage=True,
                    )
                    model.eval()
                else:
                    raise

            # Log where the model actually ended up
            try:
                where = next(model.parameters()).device
                logging.info(f"Fast model device: {where} (dtype: {model.dtype})")
            except Exception:
                pass

            class FastTransformersWrapper:
                def __init__(self, model, tokenizer):
                    self.model = model
                    self.tokenizer = tokenizer

                def reset(self):
                    pass

                def create_chat_completion(self, messages, max_tokens=128, temperature=0.0, request_id=None, **kwargs):
                    """
                    Create chat completion. Will check abort_fast_event frequently to allow cancellation.
                    request_id: Used to cancel old requests when new ones arrive
                    """
                    text = self.tokenizer.apply_chat_template(
                        messages, tokenize=False, add_generation_prompt=True
                    )
                    
                    logging.info(f"[DEBUG] Chat template input:\n{text[:200]}...")
                    
                    # Ensure inputs are on the same device as the model
                    inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
                    input_len = inputs.input_ids.shape[1]
                    
                    logging.info(f"[DEBUG] Input tokens: {input_len}, device: {inputs.input_ids.device}")

                    # Always use greedy decoding for speed when temperature is 0
                    do_sample = temperature > 0
                    
                    # Abortion support via StoppingCriteria - checks on every token
                    from transformers import StoppingCriteria, StoppingCriteriaList
                    class AbortCriteria(StoppingCriteria):
                        def __init__(self, request_id):
                            self.request_id = request_id
                            self.check_count = 0
                        
                        def __call__(self, input_ids, scores, **kwargs):
                            self.check_count += 1
                            # Check abort every token
                            if abort_fast_event.is_set():
                                return True
                            # Also check if this request is still current
                            global current_fast_request_id
                            if self.request_id is not None and current_fast_request_id != self.request_id:
                                return True
                            return False

                    gen_kw = dict(
                        max_new_tokens=max_tokens,
                        do_sample=do_sample,
                        pad_token_id=self.tokenizer.eos_token_id,
                        use_cache=True,  # KV cache for speed
                        stopping_criteria=StoppingCriteriaList([AbortCriteria(request_id)]),
                    )
                    
                    if do_sample:
                        gen_kw["temperature"] = temperature

                    with torch.inference_mode():
                        generated = self.model.generate(**inputs, **gen_kw)
                    
                    logging.info(f"[DEBUG] Generated shape: {generated.shape}")
                    
                    output_ids = generated[0][input_len:]
                    logging.info(f"[DEBUG] Output token IDs (first 20): {output_ids[:20]}")
                    
                    output_text = self.tokenizer.decode(
                        output_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
                    )
                    
                    logging.info(f"[DEBUG] Raw decoded output: '{output_text[:200]}'")
                    
                    completion_tokens = len(output_ids)

                    # Strip input echo if present
                    if output_text.startswith(text[: min(80, len(text))]):
                        pass
                    if "assistant\n" in output_text:
                        output_text = output_text.split("assistant\n", 1)[-1].strip()
                    
                    logging.info(f"[DEBUG] Final output after processing: '{output_text[:200]}'")

                    return {
                        "choices": [{
                            "message": {"role": "assistant", "content": output_text}
                        }],
                        "usage": {"completion_tokens": completion_tokens},
                    }

            fast_model = FastTransformersWrapper(model, tokenizer)
            init_error = None
            logging.info("Fast Model Loaded (Transformers).")
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

    # Check if we are using the "Transformers" model (based on config variable name or content)
    USE_TRANSFORMERS = "Qwen3-VL-4B-Instruct" in MAIN_MODEL_FILENAME and "gguf" not in MAIN_MODEL_FILENAME.lower()
    
    if USE_TRANSFORMERS:
        with main_lock:
            if llm: return
            logging.info(f"Loading Main Model via Transformers: {MAIN_MODEL_FILENAME}")
            try:
                # Clear cache before loading a large model
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

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
                    try:
                        from transformers import Qwen2_5_VLForConditionalGeneration
                        ModelClass = Qwen2_5_VLForConditionalGeneration
                    except ImportError:
                        # Generic fallback
                        from transformers import AutoModelForVision2Seq
                        ModelClass = AutoModelForVision2Seq

                # Define quantization config for 4-bit loading (similar to GGUF Q4)
                # This drastically reduces VRAM usage (from ~8GB to ~3GB for 4B model)
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
