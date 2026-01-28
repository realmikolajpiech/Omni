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

def run_flask():
    """Runs the Flask API server."""
    app = create_app()
    # Run properly
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

def main():
    setup_logging("launcher")
    
    print("Starting Brain Service...")
    # Start Flask in a daemon thread so it dies when main thread dies
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("Starting UI...")
    # Run the UI
    # Since src.app.main.main() calls sys.exit(), it will exit this script too.
    # This is fine.
    run_ui_main()

if __name__ == "__main__":
    main()
