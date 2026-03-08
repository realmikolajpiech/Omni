from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from src.ui.styles import THEMES

class MathWidget(QWidget):
    """Plain-text implementation of the MathWidget to prevent QWebEngineView from breaking macOS window transparency."""
    
    def __init__(self, result="", equation="", parent=None):
        super().__init__(parent)
        self.result = str(result)
        self.equation = str(equation)
        self.current_theme = "light"
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        self.result_label = QLabel(self.result)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.result_label.setFont(QFont("Instrument Serif", 32, QFont.Weight.Normal))
        
        self.eq_label = QLabel(self.equation)
        self.eq_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.eq_label.setFont(QFont("Manrope", 14, QFont.Weight.Medium))
        self.eq_label.setWordWrap(True)
        
        layout.addWidget(self.result_label)
        layout.addWidget(self.eq_label)
        
        self.set_theme("light")

    def set_result(self, result):
        self.result = str(result)
        self.result_label.setText(self.result)
        
    def set_equation(self, equation):
        self.equation = str(equation)
        self.eq_label.setText(self.equation)
        
    def set_theme(self, theme):
        self.current_theme = theme
        theme_data = THEMES.get(theme, THEMES["light"])
        
        text_color = theme_data.get("text_primary", "#050505")
        eq_color = theme_data.get("text_secondary", "#888888")
        
        self.result_label.setStyleSheet(f"color: {text_color};")
        self.eq_label.setStyleSheet(f"color: {eq_color};")