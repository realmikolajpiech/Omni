
import logging
import sys
import transformers
print(f"Transformers version: {transformers.__version__}")
from transformers import AutoProcessor, AutoModelForCausalLM, AutoConfig

logging.basicConfig(level=logging.INFO)

model_name = "Qwen/Qwen3-VL-4B-Instruct"

print(f"Loading {model_name}...")

try:
    print("Loading Processor...")
    processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
    print("Processor Loaded.")
except Exception as e:
    print(f"Processor Load Failed: {e}")
    import traceback
    traceback.print_exc()

try:
    print("Loading Model...")
    from transformers import Qwen3VLForConditionalGeneration
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map="auto",
        trust_remote_code=True
    )
    print("Model Loaded.")
except Exception as e:
    print(f"Model Load Failed: {e}")
    import traceback
    traceback.print_exc()
