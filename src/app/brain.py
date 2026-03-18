import sys
import os
import logging
from flask import Flask

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.config import BRAIN_HOST, BRAIN_PORT
from src.core.logger import setup_logging
from src.api.routes import api_bp
from src.services.llm.model_manager import ensure_model_loaded, ensure_resources, ensure_fast_model

def create_app():
    setup_logging("brain")
    from src.core import auth as _auth
    _auth.load_saved_session()
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    from src.services.reminders.reminder_service import get_service as _get_reminder_svc
    _get_reminder_svc().start()

    return app

def load_models_background():
    """Load models in background with error handling."""
    try:
        ensure_fast_model()  # Fast model first — most queries need it immediately
        # Warm up the fast model with a tiny request so first real query is fast
        try:
            from src.services.llm.model_manager import fast_model
            if fast_model:
                fast_model.client.chat.completions.create(
                    model=fast_model.model if hasattr(fast_model, 'model') else "openai/gpt-oss-20b",
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
                logging.info("Fast model warmed up successfully")
        except Exception as e:
            logging.warning(f"Fast model warmup failed (non-critical): {e}")
        ensure_model_loaded()
        ensure_resources()
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
        # Increase timeout and disable threading to prevent race conditions during model load
        app.run(host=BRAIN_HOST, port=BRAIN_PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        logging.error(f"Flask app failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
