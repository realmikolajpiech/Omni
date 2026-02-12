import os
import logging
import threading
import requests
import torch
import sys

# Prefer external libllama if present
try:
    from src.core.config import PROJECT_ROOT
    _ll_path = os.path.join(PROJECT_ROOT, ".deps", "llama.cpp", "build", "lib", "libllama.dylib")
    if os.path.exists(_ll_path):
        os.environ["LLAMA_CPP_LIB"] = _ll_path
        os.environ["LLAMA_CPP_LOG"] = "1"
except Exception:
    pass

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
            mps_ok = sys.platform == "darwin" and torch.backends.mps.is_available()
            
            # MPS Safety: Avoid meta tensor issues on M4 by loading directly to CPU first if needed
            # But AutoModel usually handles this. The error suggests "accelerate" library usage with device_map="auto" or similar might be failing on MPS.
            # We will force standard loading without fancy offloading if on MPS to be safe.
            
            if cuda_ok:
                logging.info(f"CUDA available: {torch.cuda.get_device_name(0)}")
            elif mps_ok:
                logging.info("MPS (Metal Performance Shaders) available.")

            dtype = torch.bfloat16 if cuda_ok else (torch.float16 if mps_ok else torch.float32)
            attn_implementation = "sdpa"  # sdpa is reliable with quantized models
            
            tokenizer = AutoTokenizer.from_pretrained(FAST_MODEL_HF_ID, trust_remote_code=True)

            try:
                # Explicitly disable device_map for MPS to avoid "meta tensor" copy errors
                # This forces simple direct loading which is more stable on Mac
                device_map = None 
                if cuda_ok: device_map = "auto"
                
                model = AutoModelForCausalLM.from_pretrained(
                    FAST_MODEL_HF_ID,
                    torch_dtype=dtype,
                    device_map=device_map, # None for MPS
                    trust_remote_code=True,
                    attn_implementation=attn_implementation,
                    low_cpu_mem_usage=True
                )
                
                if mps_ok:
                    model = model.to("mps")
                elif cuda_ok and not device_map:
                    model = model.to("cuda:0")
                
                model.eval()
                
                if cuda_ok or mps_ok:
                    logging.info("Warming up fast model...")
                    warmup_input = tokenizer("Hi", return_tensors="pt").to(model.device)
                    with torch.inference_mode():
                         model.generate(**warmup_input, max_new_tokens=2)
                    logging.info("Fast model warmup complete.")

            except Exception as e:
                logging.error(f"Fast Model Load Logic Error: {e}")
                raise e # Re-raise to trigger fallback or logging

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
            # Force CPU for embeddings to avoid Metal conflicts and meta tensor issues
            # We must be very careful about device placement on Mac
            os.environ["TOKENIZERS_PARALLELISM"] = "false"
            embed_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        except Exception as e: logging.error(f"Embeddings Error: {e}")

    # 2. Main Model
    if llm: return

    Llama = ensure_imports()
    if not Llama: return
    try:
        logging.info("llama.cpp system info follows:")
        from llama_cpp import llama_print_system_info as _sysinfo
        logging.info(_sysinfo().decode() if hasattr(_sysinfo(), "decode") else _sysinfo())
    except Exception:
        pass

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

                            # Reduce memory usage: clear cache if not using stream
                            if not stream and torch.cuda.is_available():
                                torch.cuda.empty_cache()
                            elif not stream and sys.platform == "darwin":
                                # torch.mps.empty_cache() # Not always available/effective, but good practice
                                pass

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
        logging.info(f"Main model not found: {MAIN_MODEL_FILENAME}. Downloading...")
        if not download_file(MAIN_MODEL_URL, MAIN_MODEL_PATH):
            init_error = "Failed to download main model."
            return

    # Ensure MMPROJ for Vision Support
    chat_handler = None
    # Check if we are loading a GGUF model that needs external vision adapter
    if MMPROJ_PATH and MMPROJ_URL and "gguf" in MAIN_MODEL_FILENAME.lower():
        if not os.path.exists(MMPROJ_PATH):
             logging.info(f"MMPROJ not found. Downloading {MMPROJ_FILENAME}...")
             if not download_file(MMPROJ_URL, MMPROJ_PATH):
                 logging.error("Failed to download MMPROJ. Vision might not work.")
        
        # NOTE: For Qwen3-VL server mode, we don't need a chat_handler in Python.
        # The server handles vision. But we ensure the file exists above.
        # We only need chat_handler if running local Llama() instance (not server).
        
        if os.path.exists(MMPROJ_PATH) and not "qwen3" in MAIN_MODEL_FILENAME.lower():
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
    


    # Qwen3-VL Special Handling: Use local llama.cpp server
    if "qwen3" in MAIN_MODEL_FILENAME.lower():
        logging.info("Using Qwen3-VL Local Server Mode...")
        # Start server if not running
        import subprocess
        import time
        import socket
        
        server_port = 8081
        server_url = f"http://127.0.0.1:{server_port}/v1"
        
        # Check if port is open
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', server_port))
        sock.close()
        
        if result != 0:
            logging.info("Starting local Qwen3-VL server...")
            subprocess.Popen(["./start_model_server.sh"], shell=True)
            # Wait for server to come up
            for i in range(30):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    if sock.connect_ex(('127.0.0.1', server_port)) == 0:
                        sock.close()
                        logging.info("Server is up!")
                        break
                    sock.close()
                except: pass
                time.sleep(1)
        
        # Create OpenAI client wrapper
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler # Placeholder if needed
        try:
            from openai import OpenAI
            client = OpenAI(base_url=server_url, api_key="sk-no-key-required")
            
            class OpenAIWrapper:
                def __init__(self, client):
                    self.client = client
                    self.device = "server" # Mock
                    
                def create_chat_completion(self, messages, max_tokens=1024, temperature=0.7, stream=False, **kwargs):
                    # Filter out unsupported params or adjust
                    
                    # Convert OpenAI image_url format to llama-server supported format if needed
                    # messages structure: [{'role': 'user', 'content': [{'type': 'text', ...}, {'type': 'image_url', ...}]}]
                    # Qwen3-VL server via OpenAI API usually expects standard OpenAI format.
                    # We ensure it's passed through correctly.
                    
                    # Ensure temperature is float
                    temperature = float(temperature)
                    
                    response = self.client.chat.completions.create(
                        model="qwen3vl", # Model name in server is often just alias
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=stream,
                        extra_body=kwargs
                    )
                    
                    if stream:
                        return response # It's already an iterator
                    
                    # Wrap non-stream response to match Llama object dict
                    return {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": response.choices[0].message.content
                            }
                        }]
                    }
                
                def reset(self): pass

            llm = OpenAIWrapper(client)
            
            # Reduce memory usage: clear cache if not using stream
            if sys.platform == "darwin":
                # Explicitly delete any large tensors if accessible
                import gc
                gc.collect()
            
            logging.info("Main Model Loaded (Local Server).")
            return
        except ImportError:
            logging.error("openai package missing. Please install openai.")
            init_error = "openai package missing"
            return

    with main_lock:
        if llm: return
        logging.info(f"Loading Main Model: {MAIN_MODEL_FILENAME}")
        try:
            from llama_cpp import Llama
            
            # If we have a chat_handler, use it. 
            # Note: Qwen2-VL usually requires n_ctx to be large enough for image tokens.
            # Reduced to 16384 to save RAM on standard Macs while still allowing reasonable context
            llm = Llama(
                model_path=MAIN_MODEL_PATH,
                chat_handler=chat_handler,
                n_ctx=16384, 
                n_threads=6, # Increase threads for M4
                n_gpu_layers=-1, # Metal Support
                verbose=True, # Enable verbose to see Metal usage
                # chat_format="qwen2" # Let auto-detection work or rely on handler
            )
            logging.info("Main Model Loaded (GGUF/Metal).")
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
