import socket
import logging
import os
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import IPC_PORT

class IPCWorker(QThread):
    toggle_requested = pyqtSignal()

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(('127.0.0.1', IPC_PORT))
            server.listen(1)
            logging.info(f"IPC Listener started on port {IPC_PORT}")
            while True:
                client, addr = server.accept()
                data = client.recv(1024)
                if data == b"TOGGLE":
                    self.toggle_requested.emit()
                client.close()
        except OSError:
            # Port in use -> Another instance is running
            # We are the second instance. Send signal and die.
            try:
                msg_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                msg_sock.connect(('127.0.0.1', IPC_PORT))
                msg_sock.sendall(b"TOGGLE")
                msg_sock.close()
                logging.info("Sent TOGGLE to existing instance. Exiting.")
            except Exception as e:
                logging.error(f"Failed to communicate with existing instance: {e}")
            
            # Use os._exit to ensure immediate termination without cleanup hooks interfering
            os._exit(0)

# Global ref
ipc_thread = None

def start_ipc_listener(window_instance):
    global ipc_thread
    ipc_thread = IPCWorker()
    # Connect the signal to a slot that toggles the window
    # We assume window_instance has a toggle_visibility_safe method
    ipc_thread.toggle_requested.connect(window_instance.toggle_visibility_safe)
    ipc_thread.start()
