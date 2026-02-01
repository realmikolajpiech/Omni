import sys
import os
import threading
import time
import subprocess
from PyQt6.QtWidgets import QApplication

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.app.brain import create_app
from src.app.main import main as run_ui_main
from src.core.logger import setup_logging
import socket
from src.core.config import BRAIN_PORT

def is_brain_running():
    """Checks if the Brain service is already running on the configured port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', BRAIN_PORT)) == 0

def run_flask():
    """Runs the Flask API server."""
    app = create_app()
    # Run properly
    app.run(host='127.0.0.1', port=BRAIN_PORT, debug=False, use_reloader=False, threaded=True)

def main():
    setup_logging("launcher")
    
    # Check arguments
    mode = "all"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    
    if mode == "brain":
        print("Starting Brain Service ONLY...")
        run_flask()
        return

    if mode == "ui":
        print("Starting UI ONLY...")
        run_ui_main()
        return

    # Default "all" mode (Legacy behavior)
    if is_brain_running():
        print("Brain Service is already running. Skipping startup.")
    else:
        print("Starting Brain Service...")
        # Start Flask in a daemon thread so it dies when main thread dies
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
    
    print("Starting UI...")
    # Run the UI
    run_ui_main()

if __name__ == "__main__":
    main()
