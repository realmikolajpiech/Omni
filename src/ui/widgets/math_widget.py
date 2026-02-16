import os
import tempfile
import webbrowser
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView
from src.ui.styles import THEMES

class MathWidget(QWidget):
    """Widget that renders LaTeX math equations using KaTeX."""
    
    def __init__(self, result="", equation="", parent=None):
        super().__init__(parent)
        self.result = result
        self.equation = equation
        self.current_theme = "light"
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # Result label
        self.result_label = QLabel(result)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setFont(self.font())
        
        # Web view for LaTeX rendering
        self.web_view = QWebEngineView()
        self.web_view.setFixedHeight(40)  # Compact height for equation
        self.web_view.setStyleSheet("background: transparent; border: none;")
        
        layout.addWidget(self.result_label)
        layout.addWidget(self.web_view)
        
        self.update_math_display()
        self.set_theme("light")
    
    def set_result(self, result):
        """Set the calculation result."""
        self.result = result
        self.result_label.setText(result)
    
    def set_equation(self, equation):
        """Set the LaTeX equation."""
        self.equation = equation
        self.update_math_display()
    
    def update_math_display(self):
        """Update the HTML content with current result and equation."""
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
            <style>
                body {{
                    font-family: 'Manrope', sans-serif;
                    background: transparent;
                    margin: 0;
                    padding: 4px 0;
                    color: {self._get_text_color()};
                    text-align: center;
                }}
                .equation {{
                    font-size: 14px;
                    line-height: 1.2;
                }}
                .katex {{
                    font-size: 1em !important;
                    display: inline-block !important;
                }}
            </style>
        </head>
        <body>
            <div class="equation">{self.equation}</div>
            <script>
                document.addEventListener("DOMContentLoaded", function() {{
                    renderMathInElement(document.body, {{
                        delimiters: [
                            {{left: "$$", right: "$$", display: true}},
                            {{left: "$", right: "$", display: false}},
                            {{left: "\\(", right: "\\)", display: false}},
                            {{left: "\\[", right: "\\]", display: true}}
                        ]
                    }});
                }});
            </script>
        </body>
        </html>
        """
        
        self.web_view.setHtml(html_content)
    
    def _get_text_color(self):
        """Get text color based on current theme."""
        theme = THEMES.get(self.current_theme, THEMES["light"])
        return theme.get("text_secondary", "#555555")
    
    def set_theme(self, theme):
        """Set the theme for the widget."""
        self.current_theme = theme
        
        # Update result label color
        theme_data = THEMES.get(theme, THEMES["light"])
        text_color = theme_data.get("text_primary", "#050505")
        self.result_label.setStyleSheet(f"color: {text_color};")
        
        # Update equation display
        self.update_math_display()
    
    def sizeHint(self):
        return self.minimumSizeHint()
    
    def minimumSizeHint(self):
        return self.result_label.sizeHint().expandedTo(self.web_view.sizeHint()) + self.layout().spacing() * 2