import socket
import logging
import os
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.config import IPC_PORT

class IPCWorker(QThread):
    toggle_requested = pyqtSignal(str) # Now carries source info
    query_requested = pyqtSignal(str)
    status_update = pyqtSignal(str)
    partial_update = pyqtSignal(str)
    show_requested = pyqtSignal()

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(('127.0.0.1', IPC_PORT))
            server.listen(1)
            logging.info(f"IPC Listener started on port {IPC_PORT}")
            while True:
                client, addr = server.accept()
                data = client.recv(4096)
                if data == b"SHOW":
                    self.show_requested.emit()
                elif data == b"TOGGLE":
                    self.toggle_requested.emit("voice")
                elif data == b"TOGGLE_MANUAL":
                     self.toggle_requested.emit("manual")
                elif data.startswith(b"QUERY:"):
                    try:
                        query = data[6:].decode('utf-8')
                        self.query_requested.emit(query)
                    except Exception as e:
                        logging.error(f"IPC Query Decode Error: {e}")
                elif data.startswith(b"PARTIAL:"):
                    try:
                        text = data[8:].decode('utf-8')
                        self.partial_update.emit(text)
                    except Exception as e:
                        logging.error(f"IPC Partial Decode Error: {e}")
                elif data.startswith(b"STATUS:"):
                    try:
                        status = data[7:].decode('utf-8')
                        self.status_update.emit(status)
                    except Exception as e:
                        logging.error(f"IPC Status Decode Error: {e}")
                client.close()
        except OSError:
            # Port in use -> Another instance is running
            # We are the second instance. Send signal and die.
            try:
                msg_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                msg_sock.connect(('127.0.0.1', IPC_PORT))
                msg_sock.sendall(b"TOGGLE_MANUAL")
                msg_sock.close()
                logging.info("Sent TOGGLE_MANUAL to existing instance. Exiting.")
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

    if hasattr(window_instance, 'handle_ipc_show'):
        ipc_thread.show_requested.connect(window_instance.handle_ipc_show)

    # Check if window_instance has handle_ipc_query
    if hasattr(window_instance, 'handle_ipc_query'):
        ipc_thread.query_requested.connect(window_instance.handle_ipc_query)
        
    if hasattr(window_instance, 'handle_voice_status'):
        ipc_thread.status_update.connect(window_instance.handle_voice_status)

    if hasattr(window_instance, 'handle_partial_text'):
        ipc_thread.partial_update.connect(window_instance.handle_partial_text)
        
    ipc_thread.start()
