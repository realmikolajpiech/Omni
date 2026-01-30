import sys
import os
import shutil
import subprocess
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, 
                             QPushButton, QProgressBar, QMessageBox, QFrame, QHBoxLayout, QCheckBox)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QFont

# Constants
APP_NAME = "Omni AI"
INSTALL_DIR_NAME = "Omni"
EXE_NAME = "Omni.exe"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Asset paths (assuming we run from project root for now, or packaged installer)
# For this script, we assume 'dist/Omni' exists relative to where we run.
SOURCE_DIR = os.path.join(PROJECT_ROOT, "dist", "Omni")
LOGO_PATH = os.path.join(PROJECT_ROOT, "assets", "omni.png")

class InstallWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, target_dir, create_desktop, create_autostart):
        super().__init__()
        self.target_dir = target_dir
        self.create_desktop = create_desktop
        self.create_autostart = create_autostart
        
    def run(self):
        try:
            if not os.path.exists(SOURCE_DIR):
                self.finished.emit(False, f"Source directory not found: {SOURCE_DIR}")
                return

            # 1. Clean Target
            self.progress.emit(10)
            if os.path.exists(self.target_dir):
                try:
                    shutil.rmtree(self.target_dir)
                except Exception as e:
                    # Might fail if app is running
                    self.finished.emit(False, f"Could not clean target directory. Is Omni running?\nError: {e}")
                    return
            
            self.progress.emit(20)
            
            # 2. Copy Files
            # shutil.copytree is simple but doesn't give granular progress easily without custom loop
            # For simplicity in this v1, we just copy.
            shutil.copytree(SOURCE_DIR, self.target_dir)
            self.progress.emit(80)
            
            exe_path = os.path.join(self.target_dir, EXE_NAME)
            
            # 3. Create Desktop Shortcut (UI Only)
            if self.create_desktop:
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                shortcut_path = os.path.join(desktop, f"{APP_NAME}.lnk")
                # Launch UI mode
                self.create_shortcut(exe_path, "ui", shortcut_path)
                
            self.progress.emit(90)

            # 4. Create Autostart Shortcut (Brain Only)
            if self.create_autostart:
                startup = os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs\Startup')
                shortcut_path = os.path.join(startup, f"{APP_NAME} Background Service.lnk")
                # Launch Brain mode
                self.create_shortcut(exe_path, "brain", shortcut_path)

            self.progress.emit(100)
            self.finished.emit(True, "Installation Complete!")
            
        except Exception as e:
            self.finished.emit(False, str(e))

    def create_shortcut(self, target, arguments, shortcut_path):
        import win32com.client
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(shortcut_path)
            shortcut.TargetPath = target
            shortcut.Arguments = arguments
            shortcut.WorkingDirectory = os.path.dirname(target)
            shortcut.IconLocation = target
            shortcut.Description = "Omni AI Assistant"
            shortcut.Save()
        except Exception as e:
            print(f"Failed to create shortcut: {e}")

class InstallerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(600, 400)
        
        self.init_ui()
        
    def init_ui(self):
        # Main Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Frame
        self.frame = QFrame()
        self.frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E0E0E0;
            }
        """)
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(40, 40, 40, 40)
        frame_layout.setSpacing(20)
        
        # Logo
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(LOGO_PATH)
        if not pix.isNull():
            logo_label.setPixmap(pix.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        frame_layout.addWidget(logo_label)
        
        # Title
        title = QLabel("Install Omni AI")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title.setStyleSheet("color: #333333; border: none;")
        frame_layout.addWidget(title)
        
        # Description
        desc = QLabel("Your personal AI workspace assistant.")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setFont(QFont("Segoe UI", 11))
        desc.setStyleSheet("color: #666666; border: none;")
        frame_layout.addWidget(desc)
        
        frame_layout.addSpacing(20)
        
        # Options
        self.chk_desktop = QCheckBox("Create Desktop Shortcut")
        self.chk_desktop.setChecked(True)
        self.chk_desktop.setStyleSheet("QCheckBox { font-size: 14px; color: #444; border: none; }")
        
        self.chk_autostart = QCheckBox("Launch on System Startup")
        self.chk_autostart.setChecked(True)
        self.chk_autostart.setStyleSheet("QCheckBox { font-size: 14px; color: #444; border: none; }")
        
        opts_layout = QVBoxLayout()
        opts_layout.addWidget(self.chk_desktop)
        opts_layout.addWidget(self.chk_autostart)
        opts_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addLayout(opts_layout)
        
        frame_layout.addStretch()
        
        # Progress Bar (Hidden initially)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: #F0F0F0;
                border-radius: 3px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #007AFF;
                border-radius: 3px;
            }
        """)
        self.progress.hide()
        frame_layout.addWidget(self.progress)
        
        # Buttons
        self.btn_layout = QHBoxLayout()
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.close)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #F5F5F5;
                color: #666666;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #E0E0E0; }
        """)
        
        self.btn_install = QPushButton("Install Omni")
        self.btn_install.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_install.clicked.connect(self.start_install)
        self.btn_install.setStyleSheet("""
            QPushButton {
                background-color: #007AFF;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #0062CC; }
        """)
        
        self.btn_layout.addStretch()
        self.btn_layout.addWidget(self.btn_cancel)
        self.btn_layout.addWidget(self.btn_install)
        self.btn_layout.addStretch()
        
        frame_layout.addLayout(self.btn_layout)
        
        layout.addWidget(self.frame)
        
        # Center on screen
        self.center()

    def center(self):
        qr = self.frameGeometry()
        cp = QApplication.primaryScreen().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())

    def start_install(self):
        self.btn_install.setDisabled(True)
        self.btn_cancel.setDisabled(True)
        self.chk_desktop.setDisabled(True)
        self.chk_autostart.setDisabled(True)
        self.progress.show()
        self.progress.setValue(0)
        
        target_dir = os.path.join(os.getenv('LOCALAPPDATA'), INSTALL_DIR_NAME)
        
        self.worker = InstallWorker(target_dir, self.chk_desktop.isChecked(), self.chk_autostart.isChecked())
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, success, message):
        if success:
            self.progress.setValue(100)
            self.btn_install.setText("Launch")
            self.btn_install.clicked.disconnect()
            self.btn_install.clicked.connect(self.launch_app)
            self.btn_install.setEnabled(True)
            self.btn_cancel.setText("Close")
            self.btn_cancel.setEnabled(True)
            QMessageBox.information(self, "Success", message)
        else:
            QMessageBox.critical(self, "Error", message)
            self.btn_install.setEnabled(True)
            self.btn_cancel.setEnabled(True)
            self.progress.hide()

    def launch_app(self):
        target_dir = os.path.join(os.getenv('LOCALAPPDATA'), INSTALL_DIR_NAME)
        exe_path = os.path.join(target_dir, EXE_NAME)
        try:
            # Launch UI mode
            subprocess.Popen(f'"{exe_path}" ui', shell=True)
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = InstallerWindow()
    window.show()
    sys.exit(app.exec())
