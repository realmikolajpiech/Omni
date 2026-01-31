
from transformers import AutoModelForVision2Seq, AutoConfig
from transformers.models.qwen3_vl import Qwen3VLConfig

print("Checking Qwen3VLConfig in AutoModelForVision2Seq...")
try:
    # This is a bit hacky to check mapping directly, but let's see if we can load it via AutoModel
    print("Is registered?", Qwen3VLConfig in AutoModelForVision2Seq._model_mapping.keys())
except:
    print("Could not check mapping directly")
