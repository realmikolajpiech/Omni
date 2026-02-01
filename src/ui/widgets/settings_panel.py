import sys
import os
import subprocess
import logging
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, 
                             QScrollArea, QGraphicsOpacityEffect, QGridLayout)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QColor, QPainter, QPainterPath, QBrush, QPen, QLinearGradient

from src.core.config import FAST_MODEL_HF_ID, MAIN_MODEL_FILENAME

class ModelCard(QFrame):
    def __init__(self, name, type_label, vram_req, is_active=False, is_recommended=False, parent=None):
        super().__init__(parent)
        self.name = name
        self.is_active = is_active
        self.is_recommended = is_recommended
        
        # ~20% smaller than standard cards (assuming standard was ~200x150, making this ~160x120 or similar)
        self.setFixedSize(170, 110)
        self.setObjectName("ModelCard")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        
        # Header
        top_layout = QHBoxLayout()
        self.lbl_name = QLabel(name)
        self.lbl_name.setFont(QFont("Manrope", 11, QFont.Weight.Bold))
        self.lbl_name.setStyleSheet("color: #FFFFFF;" if is_active else "color: #333333;")
        
        top_layout.addWidget(self.lbl_name)
        top_layout.addStretch()
        
        if is_recommended:
            rec_lbl = QLabel("REC")
            rec_lbl.setFont(QFont("Manrope", 7, QFont.Weight.Bold))
            rec_lbl.setStyleSheet("color: #00FF00; background: rgba(0, 255, 0, 0.2); padding: 2px 4px; border-radius: 4px;")
            top_layout.addWidget(rec_lbl)
            
        layout.addLayout(top_layout)
        
        # Type
        self.lbl_type = QLabel(type_label)
        self.lbl_type.setFont(QFont("Manrope", 9))
        self.lbl_type.setStyleSheet("color: rgba(255, 255, 255, 0.7);" if is_active else "color: #666666;")
        layout.addWidget(self.lbl_type)
        
        layout.addStretch()
        
        # Status / VRAM
        bottom_layout = QHBoxLayout()
        
        status_text = "Active" if is_active else "Inactive"
        self.lbl_status = QLabel(status_text)
        self.lbl_status.setFont(QFont("Manrope", 8))
        self.lbl_status.setStyleSheet("color: #4CAF50;" if is_active else "color: #888888;")
        
        bottom_layout.addWidget(self.lbl_status)
        bottom_layout.addStretch()
        
        self.lbl_vram = QLabel(f"{vram_req}GB")
        self.lbl_vram.setFont(QFont("Manrope", 8))
        self.lbl_vram.setStyleSheet("color: rgba(255, 255, 255, 0.6);" if is_active else "color: #888888;")
        bottom_layout.addWidget(self.lbl_vram)
        
        layout.addLayout(bottom_layout)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        
        if self.is_active:
            # Active Gradient
            grad = QLinearGradient(0, 0, self.width(), self.height())
            grad.setColorAt(0, QColor("#2E5CB8"))
            grad.setColorAt(1, QColor("#1a3c8a"))
            painter.fillPath(path, QBrush(grad))
            
            # Subtle border
            painter.strokePath(path, QPen(QColor(255, 255, 255, 50), 1))
        else:
            # Inactive
            painter.fillPath(path, QColor(255, 255, 255, 180))
            painter.strokePath(path, QPen(QColor(0, 0, 0, 10), 1))

class SettingsPanel(QWidget):
    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # Semi-transparent background container
        self.bg_container = QWidget()
        # Increased transparency to allow blur to show through (0.95 -> 0.3)
        self.bg_container.setStyleSheet("background-color: rgba(245, 245, 247, 0.3); border-radius: 24px;")
        
        bg_layout = QVBoxLayout(self.bg_container)
        bg_layout.setContentsMargins(24, 20, 24, 20)
        bg_layout.setSpacing(16)
        
        # 1. Header with Back Button and VRAM info
        header_layout = QHBoxLayout()
        
        # Back Button (Icon)
        self.back_btn = QLabel("←") # Simple arrow for now
        self.back_btn.setFont(QFont("Manrope", 16, QFont.Weight.Bold))
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setStyleSheet("color: #333333; padding: 4px;")
        self.back_btn.mousePressEvent = lambda e: self.back_requested.emit()
        
        title = QLabel("Omni Settings")
        title.setFont(QFont("Instrument Serif", 22, QFont.Weight.Normal))
        title.setStyleSheet("color: #111111;") # Ensure text is dark enough against transparent bg
        
        header_layout.addWidget(self.back_btn)
        header_layout.addSpacing(12)
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # Active Model Info
        model_name = "Unknown"
        if "Qwen" in MAIN_MODEL_FILENAME: model_name = "Qwen 3 VL"
        elif "Gemma" in MAIN_MODEL_FILENAME: model_name = "Gemma 2"
        
        model_info_container = QFrame()
        model_info_container.setStyleSheet("background: rgba(46, 92, 184, 0.2); border-radius: 8px; margin-right: 8px;")
        model_layout = QHBoxLayout(model_info_container)
        model_layout.setContentsMargins(12, 6, 12, 6)
        
        model_text = QLabel(model_name)
        model_text.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        model_text.setStyleSheet("color: #1a3c8a;") # Darker blue for contrast
        
        model_layout.addWidget(model_text)
        header_layout.addWidget(model_info_container)

        # VRAM Info
        self.vram_info = self.get_vram_info()
        hw_info_container = QFrame()
        # Increased opacity of HW container bg for legibility
        hw_info_container.setStyleSheet("background: rgba(0,0,0,0.08); border-radius: 8px;")
        hw_layout = QHBoxLayout(hw_info_container)
        hw_layout.setContentsMargins(12, 6, 12, 6)
        
        hw_icon = QLabel("GPU")
        hw_icon.setFont(QFont("Manrope", 9, QFont.Weight.Bold))
        hw_icon.setStyleSheet("color: #444;")
        
        hw_text = QLabel(self.vram_info)
        hw_text.setFont(QFont("Manrope", 9))
        hw_text.setStyleSheet("color: #222;")
        
        hw_layout.addWidget(hw_icon)
        hw_layout.addWidget(hw_text)
        
        header_layout.addWidget(hw_info_container)
        
        bg_layout.addLayout(header_layout)
        
        # 2. Scrollable Content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; }
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 6px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #CCCCCC;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)
        
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(24)
        content_layout.setContentsMargins(0, 10, 10, 10) # Right margin for scrollbar
        
        # -- Fast Models --
        lbl_fast = QLabel("Fast Models")
        lbl_fast.setFont(QFont("Manrope", 12, QFont.Weight.Bold))
        lbl_fast.setStyleSheet("color: #666; letter-spacing: 0.5px; text-transform: uppercase;")
        content_layout.addWidget(lbl_fast)
        
        fast_models_layout = QHBoxLayout()
        fast_models_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        fast_models_layout.setSpacing(12)
        
        # Active Check
        current_fast = FAST_MODEL_HF_ID
        
        # Qwen 3 0.6B
        is_active = "Qwen" in current_fast and "0.6" in current_fast
        card_qwen = ModelCard("Qwen3 0.6B", "Fast / Action", 1.5, is_active=is_active, is_recommended=True)
        fast_models_layout.addWidget(card_qwen)
        
        # Llama 3.2 1B (Example alternative)
        card_llama = ModelCard("Llama 3.2 1B", "Chat / Fast", 2.2, is_active=False, is_recommended=False)
        fast_models_layout.addWidget(card_llama)
        
        content_layout.addLayout(fast_models_layout)
        
        # -- Slow Models --
        lbl_slow = QLabel("Reasoning Models")
        lbl_slow.setFont(QFont("Manrope", 12, QFont.Weight.Bold))
        lbl_slow.setStyleSheet("color: #666; letter-spacing: 0.5px; text-transform: uppercase;")
        content_layout.addWidget(lbl_slow)
        
        slow_models_layout = QHBoxLayout()
        slow_models_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        slow_models_layout.setSpacing(12)
        
        current_main = MAIN_MODEL_FILENAME
        
        # Qwen 3 VL 4B
        is_active_main = "Qwen" in current_main and "VL" in current_main
        card_qwen_vl = ModelCard("Qwen3 VL 4B", "Vision / Reasoning", 4.5, is_active=is_active_main, is_recommended=True)
        slow_models_layout.addWidget(card_qwen_vl)

        # Gemma 2 2B
        is_active_gemma = "gemma" in current_main.lower()
        card_gemma = ModelCard("Gemma 2 2B", "Reasoning", 3.0, is_active=is_active_gemma, is_recommended=False)
        slow_models_layout.addWidget(card_gemma)
        
        content_layout.addLayout(slow_models_layout)
        content_layout.addStretch()
        
        scroll.setWidget(content_widget)
        bg_layout.addWidget(scroll)
        
        self.main_layout.addWidget(self.bg_container)
        
        # Animation support
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

    def get_vram_info(self):
        try:
            if sys.platform == "win32":
                # 1. Try nvidia-smi (Standard Path or PATH)
                try:
                    # Check common paths if not in PATH
                    nvidia_smi = "nvidia-smi"
                    possible_paths = [
                        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
                        r"C:\Windows\System32\nvidia-smi.exe"
                    ]
                    for p in possible_paths:
                        if os.path.exists(p):
                            nvidia_smi = f'"{p}"'
                            break
                            
                    cmd = f"{nvidia_smi} --query-gpu=memory.total,memory.used --format=csv,noheader,nounits"
                    # Use creationflags to hide console window
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    
                    output = subprocess.check_output(cmd, shell=True, startupinfo=startupinfo).decode().strip()
                    total, used = map(int, output.split(','))
                    free = total - used
                    return f"{free}MB Free / {total}MB"
                except:
                    pass

                # 2. Fallback: PowerShell Get-CimInstance (Any GPU)
                try:
                    cmd = 'powershell -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty AdapterRAM"'
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    
                    output = subprocess.check_output(cmd, shell=True, startupinfo=startupinfo).decode().strip()
                    # Output might be bytes, multiple lines if multiple GPUs
                    lines = output.split('\n')
                    best_vram = 0
                    for line in lines:
                        try:
                            vram = int(line.strip())
                            if vram > best_vram: best_vram = vram
                        except: pass
                    
                    if best_vram > 0:
                        total_mb = best_vram // (1024 * 1024)
                        return f"Total VRAM: {total_mb}MB"
                except:
                    pass
        except:
            pass
        return "VRAM: Unknown"

