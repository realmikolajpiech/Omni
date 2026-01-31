
import logging
from transformers import AutoModelForVision2Seq

logging.basicConfig(level=logging.INFO)
model_name = "Qwen/Qwen3-VL-4B-Instruct"

print("Loading Model with AutoModelForVision2Seq...")
try:
    model = AutoModelForVision2Seq.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map="auto",
        trust_remote_code=True
    )
    print("Model Loaded.")
except Exception as e:
    print(f"Load Failed: {e}")
    import traceback
    traceback.print_exc()
