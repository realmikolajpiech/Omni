import os
import requests
import zipfile
import io
import logging

from src.core.config import MODEL_DIR, VOSK_MODEL_PATH, VOSK_MODEL_URL

def download_and_extract():
    if os.path.exists(VOSK_MODEL_PATH):
        logging.info(f"Vosk model already exists at {VOSK_MODEL_PATH}")
        return

    os.makedirs(MODEL_DIR, exist_ok=True)
    logging.info(f"Downloading Vosk model from {VOSK_MODEL_URL}...")
    
    try:
        r = requests.get(VOSK_MODEL_URL)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        
        logging.info("Extracting...")
        z.extractall(MODEL_DIR)
        logging.info("Done.")
    except Exception as e:
        logging.error(f"Failed to download/extract Vosk model: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_and_extract()
