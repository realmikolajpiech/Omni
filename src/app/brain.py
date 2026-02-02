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

def load_models_background():
    """Load models in background with error handling."""
    try:
        ensure_model_loaded()
        logging.info("Model loading completed successfully")
    except Exception as e:
        logging.error(f"Model loading failed: {e}")
        import traceback
        logging.error(traceback.format_exc())

if __name__ == "__main__":
    app = create_app()
    logging.info(f"Starting Brain Service on {BRAIN_HOST}:{BRAIN_PORT}")

    # Preload models in background with error handling
    import threading
    model_thread = threading.Thread(target=load_models_background, daemon=True)
    model_thread.start()

    try:
        app.run(host=BRAIN_HOST, port=BRAIN_PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        logging.error(f"Flask app failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
