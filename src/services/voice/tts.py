import torch
import logging
import sounddevice as sd
import sys
import numpy as np
import src.services.llm.model_manager as model_manager

def speak(text):
    if not text: return
    
    logging.info(f"Speaking: {text[:50]}...")
    
    # Try using Vits Model first
    try:
        model_manager.ensure_tts_model()
        if model_manager.tts_model:
            with model_manager.tts_lock:
                tokenizer = model_manager.tts_model["tokenizer"]
                model = model_manager.tts_model["model"]
                
                inputs = tokenizer(text, return_tensors="pt")
                device = model.device
                inputs = inputs.to(device)
                
                with torch.no_grad():
                    output = model(**inputs).waveform
                
                # Convert to numpy
                audio_np = output.cpu().numpy().squeeze()
                
                # Play
                sd.play(audio_np, samplerate=model.config.sampling_rate)
                sd.wait()
                return
    except Exception as e:
        logging.error(f"TTS Error: {e}")
        # Fallback below
        
    # Fallback to System TTS
    logging.info("Falling back to system TTS")
    if sys.platform == "darwin":
        import subprocess
        # Use -r to speed up slightly if needed, or default
        subprocess.run(["say", text])
    elif sys.platform == "win32":
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(text)
        except Exception as e:
            logging.error(f"Win32 TTS Error: {e}")
