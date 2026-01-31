
from huggingface_hub import hf_hub_download
import json

path = hf_hub_download(repo_id="Qwen/Qwen3-VL-4B-Instruct", filename="config.json")
with open(path, 'r') as f:
    config = json.load(f)

print("Model Type:", config.get("model_type"))
print("Auto Map:", config.get("auto_map"))
