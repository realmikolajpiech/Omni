import os
import logging
import threading
import requests
import torch
import sys

# Suppress HuggingFace Hub Permission Warnings
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
# Filter out the specific annoying permission error logs if they still appear via logging
class PermissionErrorFilter(logging.Filter):
    def filter(self, record):
        return "Permission denied" not in record.getMessage() and "Could not cache non-existence" not in record.getMessage()

logging.getLogger("huggingface_hub").addFilter(PermissionErrorFilter())
logging.getLogger("transformers").addFilter(PermissionErrorFilter())

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer, AutoProcessor, BitsAndBytesConfig, TextIteratorStreamer
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
tts_model = None
embed_model = None
db_conn = None
vision_model = None
init_error = None

# Thread Locks
main_lock = threading.Lock()
fast_lock = threading.Lock()
tts_lock = threading.Lock()
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
    """Loads the smaller, faster model for actions (transformers), quantized in-code for speed."""
    global fast_model, init_error
    if fast_model: return

    with fast_lock:
        if fast_model: return
        logging.info(f"Loading Fast Model (Transformers, 4-bit quantized): {FAST_MODEL_HF_ID}")
        try:
            cuda_ok = torch.cuda.is_available()
            if cuda_ok:
                logging.info(f"CUDA available: {torch.cuda.get_device_name(0)}")

            dtype = torch.bfloat16 if cuda_ok else torch.float32
            attn_implementation = "sdpa"  # sdpa is reliable with quantized models
            
            tokenizer = AutoTokenizer.from_pretrained(FAST_MODEL_HF_ID, trust_remote_code=True)

            try:
                if cuda_ok:
                    torch.backends.cuda.matmul.allow_tf32 = True
                    torch.backends.cudnn.allow_tf32 = True

                # 4-bit quantization in-code (faster inference, less VRAM than bf16)
                use_quant = cuda_ok
                try:
                    import bitsandbytes
                except ImportError:
                    use_quant = False
                    logging.warning("bitsandbytes not installed; fast model will load in bfloat16 (slower).")

                # 8-bit quantization (better quality than 4-bit for small models, still efficient)
                # 4-bit NF4 can be too aggressive for 0.6B model, causing garbage/Chinese output
                if use_quant:
                    quantization_config = BitsAndBytesConfig(
                        load_in_8bit=True,
                    )
                    logging.info("Loading fast model with 8-bit quantization...")
                    model = AutoModelForCausalLM.from_pretrained(
                        FAST_MODEL_HF_ID,
                        quantization_config=quantization_config,
                        device_map="cuda:0",
                        trust_remote_code=True,
                        attn_implementation=attn_implementation,
                        low_cpu_mem_usage=True
                    )
                    # Quantized model: do not call .to(dtype=...) 
                else:
                    model = AutoModelForCausalLM.from_pretrained(
                        FAST_MODEL_HF_ID,
                        torch_dtype=dtype,
                        device_map="cuda:0" if cuda_ok else "cpu",
                        trust_remote_code=True,
                        attn_implementation=attn_implementation,
                        low_cpu_mem_usage=True
                    )
                    if cuda_ok:
                        model = model.to(dtype=dtype, device="cuda:0")
                
                model.eval()
                
                if cuda_ok:
                    logging.info("Warming up fast model...")
                    warmup_input = tokenizer("Hi", return_tensors="pt").to(model.device)
                    with torch.inference_mode():
                        model.generate(**warmup_input, max_new_tokens=2)
                    torch.cuda.synchronize()
                    logging.info("Fast model warmup complete.")
            except Exception as oom:
                if cuda_ok and "out of memory" in str(oom).lower():
                    logging.warning("Fast model OOM, falling back to 8-bit or unquantized")
                    torch.cuda.empty_cache()
                    try:
                        quantization_config = BitsAndBytesConfig(
                            load_in_8bit=True,
                            bnb_8bit_compute_dtype=dtype
                        )
                        model = AutoModelForCausalLM.from_pretrained(
                            FAST_MODEL_HF_ID,
                            quantization_config=quantization_config,
                            device_map="auto",
                            trust_remote_code=True,
                            attn_implementation="sdpa",
                            low_cpu_mem_usage=True
                        )
                        model.eval()
                    except Exception:
                        model = AutoModelForCausalLM.from_pretrained(
                            FAST_MODEL_HF_ID,
                            torch_dtype=dtype,
                            device_map="auto",
                            trust_remote_code=True,
                            low_cpu_mem_usage=True
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
                        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
                    )
                    
                    logging.info(f"[DEBUG] Chat template input:\n{text[:200]}...")
                    
                    # Ensure inputs are on the same device as the model
                    inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
                    input_len = inputs.input_ids.shape[1]
                    
                    logging.info(f"[DEBUG] Input tokens: {input_len}, device: {inputs.input_ids.device}")

                    # Use light sampling for fast model to avoid repetition/garbage (Qwen3 4-bit tends to loop with greedy)
                    # When caller passes 0, use 0.5 so we still get diverse short outputs without loops
                    eff_temp = temperature if temperature > 0 else 0.5
                    do_sample = eff_temp > 0
                    
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
                        repetition_penalty=1.2,  # Reduce repetition/garbage from 4-bit fast model
                    )
                    
                    if do_sample:
                        gen_kw["temperature"] = eff_temp
                        gen_kw["top_p"] = 0.8
                        gen_kw["top_k"] = 20

                    with torch.inference_mode():
                        generated = self.model.generate(**inputs, **gen_kw)
                    
                    logging.info(f"[DEBUG] Generated shape: {generated.shape}")
                    
                    output_ids = generated[0][input_len:]
                    logging.info(f"[DEBUG] Output token IDs (first 20): {output_ids[:20]}")
                    
                    # First decode WITH special tokens to see what we got
                    output_text_with_special = self.tokenizer.decode(
                        output_ids, skip_special_tokens=False
                    )
                    logging.info(f"[DEBUG] Raw output WITH special tokens: {repr(output_text_with_special[:300])}")
                    
                    output_text = self.tokenizer.decode(
                        output_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
                    )
                    
                    logging.info(f"[DEBUG] Raw decoded output (special filtered): '{output_text[:200]}'")
                    
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
    USE_TRANSFORMERS = "Qwen3-VL-4B" in MAIN_MODEL_FILENAME and "gguf" not in MAIN_MODEL_FILENAME.lower()
    
    if USE_TRANSFORMERS:
        with main_lock:
            if llm: return
            logging.info(f"Loading Main Model via Transformers: {MAIN_MODEL_FILENAME}")
            try:
                # Clear cache before loading a large model
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                # Check for bitsandbytes
                use_bnb = False
                # Only use bitsandbytes if CUDA is available, as 4-bit loading usually requires it
                if torch.cuda.is_available():
                    try:
                        import bitsandbytes
                        use_bnb = True
                    except ImportError:
                        logging.warning("bitsandbytes not installed, falling back to non-quantized load")
                else:
                    logging.info("CUDA not available, skipping bitsandbytes quantization.")

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
                if use_bnb:
                    quantization_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.bfloat16,
                        bnb_4bit_use_double_quant=True,
                        bnb_4bit_quant_type="nf4"
                    )
                else:
                    quantization_config = None

                model = ModelClass.from_pretrained(
                    MAIN_MODEL_FILENAME, 
                    quantization_config=quantization_config, # Apply 4-bit quantization if available
                    device_map="auto",
                    trust_remote_code=True,
                    low_cpu_mem_usage=True,
                    torch_dtype=torch.float16 if torch.backends.mps.is_available() else "auto"
                )
                processor = AutoProcessor.from_pretrained(MAIN_MODEL_FILENAME, trust_remote_code=True)
                
                # Wrap it in a class that mimics Llama.cpp interface for compatibility
                class TransformersWrapper:
                    def __init__(self, model, processor):
                        self.model = model
                        self.processor = processor
                        
                    def create_chat_completion(self, messages, max_tokens=1024, temperature=0.7, stream=False, **kwargs):
                        # Convert messages to text/inputs
                        # This is a simplified wrapper.
                        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=True)

                        # Handle images if present in messages (needs complex parsing)
                        # For now, let's assume text-only or simple image structure

                        inputs = self.processor(
                            text=[text],
                            images=None,
                            padding=True,
                            return_tensors="pt"
                        ).to(self.model.device)

                        if stream:
                            # Streaming mode
                            from threading import Thread

                            # Set up streamer
                            streamer = TextIteratorStreamer(
                                self.processor.tokenizer,
                                skip_prompt=True,
                                skip_special_tokens=True,
                                clean_up_tokenization_spaces=False
                            )

                            # Generation parameters
                            gen_kwargs = {
                                **inputs,
                                "max_new_tokens": max_tokens,
                                "do_sample": temperature > 0,
                                "temperature": temperature if temperature > 0 else None,
                                "streamer": streamer,
                            }

                            # Start generation in background thread
                            thread = Thread(target=self.model.generate, kwargs=gen_kwargs)
                            thread.start()

                            return streamer
                        else:
                            # Non-streaming mode (original behavior)
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

def ensure_tts_model():
    global tts_model
    if tts_model: return

    from src.core.config import TTS_MODEL_ID
    
    with tts_lock:
        if tts_model: return
        logging.info(f"Loading TTS Model: {TTS_MODEL_ID}")
        try:
            from transformers import AutoTokenizer, AutoModel
            
            tokenizer = AutoTokenizer.from_pretrained(TTS_MODEL_ID, trust_remote_code=True)
            # Use AutoModel to handle custom architectures (like Qwen3-TTS if it differs from VitsModel)
            model = AutoModel.from_pretrained(TTS_MODEL_ID, trust_remote_code=True)
            
            if torch.cuda.is_available():
                model = model.to("cuda")
            elif sys.platform == "darwin" and torch.backends.mps.is_available():
                model = model.to("mps")
                
            tts_model = {"model": model, "tokenizer": tokenizer}
            logging.info("TTS Model Loaded.")
        except Exception as e:
            logging.error(f"TTS Model Load Error: {e}")
