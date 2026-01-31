
import logging
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
import torch
import os

# Set blocking to debug the assert
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"

logging.basicConfig(level=logging.INFO)
model_name = "Qwen/Qwen3-VL-4B-Instruct"

print(f"Loading {model_name}...")

processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)

# Force BFloat16 explicitly if supported, or Float16
# RTX 3060 supports BFloat16.
model = Qwen3VLForConditionalGeneration.from_pretrained(
    model_name,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

print("Model loaded. Running test generation...")

messages = [
    {"role": "user", "content": "Hello, who are you?"}
]

# Qwen3-VL might default to expecting images in the template or processor
text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
print(f"Template output: {text}")

# Try passing empty list for images instead of None if it fails
inputs = processor(
    text=[text],
    images=None, 
    padding=True,
    return_tensors="pt"
).to(model.device)

print(f"Input keys: {inputs.keys()}")
if "input_ids" in inputs:
    print(f"Input IDs shape: {inputs['input_ids'].shape}")
    print(f"Max token ID: {inputs['input_ids'].max()}")
    print(f"Vocab size: {model.config.vocab_size}")

print("Inputs processed. Generating...")
generated_ids = model.generate(**inputs, max_new_tokens=50)
print("Generated.")
output_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
print("Output:", output_text)
