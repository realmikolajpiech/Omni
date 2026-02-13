import os
import logging
import threading
import requests
import torch
import sys
import time
import signal
import subprocess
import socket

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

# Idle Shutdown State
last_main_activity = 0
monitor_started = False

def unload_main_model():
    global llm
    logging.info("Unloading Main Model due to inactivity...")
    with main_lock:
        if not llm: return

        # Check if server
        is_server = False
        try:
             if hasattr(llm, 'device') and llm.device == "server":
                 is_server = True
        except: pass

        if is_server:
            try:
                pid_path = os.path.expanduser("~/.config/omni/qwen_server.pid")
                if os.path.exists(pid_path):
                    with open(pid_path, 'r') as f:
                        pid = int(f.read().strip())
                    os.kill(pid, signal.SIGTERM)
                    logging.info(f"Killed llama-server (PID {pid})")
            except Exception as e:
                logging.error(f"Failed to kill server: {e}")

        llm = None
        
        # Cleanup
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif sys.platform == "darwin" and hasattr(torch, 'mps'):
            try: torch.mps.empty_cache()
            except: pass
            
        logging.info("Main Model Unloaded.")

def _monitor_idle():
    global last_main_activity
    while True:
        time.sleep(30) # Check every 30s
        if not llm: continue
        
        # 10 minutes (300s) timeout
        if time.time() - last_main_activity > 300:
             unload_main_model()

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
    """Loads the smaller, faster model for actions using llama.cpp (GGUF) for minimal memory usage."""
    global fast_model, init_error
    if fast_model: return

    with fast_lock:
        if fast_model: return
        logging.info(f"Loading Fast Model (llama.cpp): {FAST_MODEL_FILENAME}")
        
        # Ensure file exists
        if not os.path.exists(FAST_MODEL_PATH):
            logging.info(f"Fast model not found. Downloading from {FAST_MODEL_URL}...")
            if not download_file(FAST_MODEL_URL, FAST_MODEL_PATH):
                init_error = "Failed to download fast model."
                return

        try:
            from llama_cpp import Llama
            
            # Load GGUF - highly optimized, mmap enabled (low active RAM)
            model = Llama(
                model_path=FAST_MODEL_PATH,
                n_ctx=4096,        # Reasonable context for actions
                n_threads=4,       # Efficient for background tasks
                n_gpu_layers=-1,   # Use Metal if available
                verbose=False,
                embedding=False
            )
            
            class FastLlamaWrapper:
                def __init__(self, model):
                    self.model = model

                def reset(self):
                    self.model.reset()

                def create_chat_completion(self, messages, max_tokens=128, temperature=0.0, request_id=None, **kwargs):
                    """
                    Create chat completion with abort support via streaming.
                    """
                    global current_fast_request_id
                    
                    # Default params for action model
                    eff_temp = temperature if temperature > 0 else 0.1
                    
                    # Stream to allow abortion
                    stream = self.model.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=eff_temp,
                        stream=True,
                        **kwargs
                    )
                    
                    full_content = ""
                    completion_tokens = 0
                    
                    for chunk in stream:
                        # Check abortion
                        if abort_fast_event.is_set():
                            logging.info(f"Fast request {request_id} aborted.")
                            return None
                        if request_id is not None and current_fast_request_id != request_id:
                            return None
                            
                        try:
                            if not chunk or 'choices' not in chunk or not chunk['choices']:
                                continue
                                
                            delta = chunk['choices'][0]['delta'].get('content', '')
                            if delta:
                                full_content += delta
                                completion_tokens += 1
                        except Exception as e:
                            logging.warning(f"Error processing chunk: {e}")
                            continue

                            
                    return {
                        "choices": [{
                            "message": {"role": "assistant", "content": full_content}
                        }],
                        "usage": {"completion_tokens": completion_tokens},
                    }

            fast_model = FastLlamaWrapper(model)
            init_error = None
            logging.info("Fast Model Loaded (llama.cpp).")

        except Exception as e:
            logging.error(f"Fast Model Load Error: {e}")
            init_error = str(e)


def ensure_main_model():
    """Loads the larger, main model for chat."""
    global llm, init_error, embed_model, db_conn, last_main_activity, monitor_started
    
    # Update activity timestamp and start monitor if needed
    last_main_activity = time.time()
    if not monitor_started:
        threading.Thread(target=_monitor_idle, daemon=True).start()
        monitor_started = True
    
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
        with main_lock:
            # Re-check inside lock in case another thread started it
            server_port = 8081
            server_url = f"http://127.0.0.1:{server_port}/v1"
            
            # Check if port is already open (server running)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('127.0.0.1', server_port))
            sock.close()
            
            if result == 0:
                logging.info("Qwen3-VL server already running on port 8081.")
            else:
                logging.info("Starting local Qwen3-VL server...")
                # Check for stale PID file
                pid_path = os.path.expanduser("~/.config/omni/qwen_server.pid")
                if os.path.exists(pid_path):
                    try:
                        with open(pid_path, 'r') as f:
                            old_pid = int(f.read().strip())
                        # Check if process is running
                        os.kill(old_pid, 0) # This raises OSError if process not found
                        logging.warning(f"Found stale PID file {old_pid} but port closed. Killing it.")
                        os.kill(old_pid, signal.SIGTERM)
                        time.sleep(1)
                    except (OSError, ValueError):
                        # Process not running or invalid PID
                        pass
                    try:
                        os.remove(pid_path)
                    except: pass

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
                        msg = response.choices[0].message
                        content = msg.content
                        # Try to get reasoning_content (DeepSeek/Qwen style)
                        reasoning = getattr(msg, "reasoning_content", None)
                        
                        return {
                            "choices": [{
                                "message": {
                                    "role": "assistant",
                                    "content": content,
                                    "reasoning_content": reasoning
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
