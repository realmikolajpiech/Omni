import sys
import os
import logging
from flask import Flask

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.config import BRAIN_HOST, BRAIN_PORT
from src.core.logger import setup_logging
from src.api.routes import api_bp
from src.services.llm.model_manager import ensure_model_loaded

def create_app():
    setup_logging("brain")
    app = Flask(__name__)
    app.register_blueprint(api_bp)
    return app

if __name__ == "__main__":
    app = create_app()
    logging.info(f"Starting Brain Service on {BRAIN_HOST}:{BRAIN_PORT}")
    
    # Preload models in background?
    # import threading
    # threading.Thread(target=ensure_model_loaded).start()
    
    app.run(host=BRAIN_HOST, port=BRAIN_PORT, debug=False, use_reloader=False)
