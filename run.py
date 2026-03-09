import sys
import os
import subprocess
import socket
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.logger import setup_logging
from src.core.config import BRAIN_PORT


def is_brain_running():
    """Checks if the Brain service is already running on the configured port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', BRAIN_PORT)) == 0


def run_flask():
    """Runs the Flask API server (called when mode == 'brain')."""
    from src.app.brain import create_app
    app = create_app()
    app.run(host='127.0.0.1', port=BRAIN_PORT, debug=False, use_reloader=False, threaded=True)


def main():
    setup_logging("launcher")

    try:
        import setproctitle
        setproctitle.setproctitle("Omni")
    except ImportError:
        pass

    # Check arguments
    mode = "all"
    extra_args = []
    for arg in sys.argv[1:]:
        if arg in ("brain", "ui"):
            mode = arg
        else:
            extra_args.append(arg)

    if mode == "brain":
        try:
            import setproctitle
            setproctitle.setproctitle("Omni Helper (Brain)")
        except ImportError:
            pass
        print("Starting Brain Service ONLY...")
        run_flask()
        return

    if mode == "ui":
        print("Starting UI ONLY...")
        from src.app.main import main as run_ui_main
        run_ui_main()
        return

    # Default "all" mode — launch brain as a separate process so its
    # heavy embedding/model work doesn't share the GIL with the Qt UI thread.
    # Start the UI immediately without waiting — onboarding doesn't need the
    # brain, and the window handles "brain not ready" gracefully (requests fail
    # silently until it comes up).
    if not is_brain_running():
        print("Starting Brain Service (subprocess)...")
        logs_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        brain_proc = subprocess.Popen(
            [sys.executable, __file__, "brain"],
            stdout=open(os.path.join(logs_dir, "brain_setup.log"), "a"),
            stderr=subprocess.STDOUT,
        )
    else:
        print("Brain Service already running.")
        brain_proc = None

    print("Starting UI...")
    from src.app.main import main as run_ui_main
    try:
        run_ui_main()
    finally:
        # When the UI exits, also terminate the brain we spawned.
        if brain_proc is not None and brain_proc.poll() is None:
            brain_proc.terminate()


if __name__ == "__main__":
    main()
