import logging
import os
import sys
from src.core.config import LOG_PATH

def setup_logging(name="omni"):
    # Use project directory for logs to avoid /tmp permission issues with sudo
    # If running as sudo, ~ might be /root depending on flags, so hardcode preferred user path or just handle it
    log_file = LOG_PATH
    if os.name == 'posix' and os.geteuid() == 0:
        # If root, force miki's dir if possible, or just use precise path
        # Assuming typical user path from config
        pass

    # Check if root logger already has handlers to prevent duplication
    if logging.getLogger('').hasHandlers():
        return logging.getLogger(name)

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Also log to stdout for dev
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)
    
    # Silence werkzeug
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    return logging.getLogger(name)

def setup_exception_hook():
    def exception_hook(exctype, value, tb):
        logging.critical("Uncaught exception:", exc_info=(exctype, value, tb))
        sys.__excepthook__(exctype, value, tb)
    sys.excepthook = exception_hook
