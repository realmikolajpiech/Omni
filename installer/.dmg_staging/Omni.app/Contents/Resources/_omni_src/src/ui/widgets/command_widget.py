from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel

class CommandLogWidget(QWidget):
    def __init__(self, command, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 2, 20, 2)
        
        lbl_icon = QLabel(" >_")
        lbl_icon.setStyleSheet("color: #999999; font-family: 'Fira Code', monospace; font-size: 11px;")
        
        lbl_cmd = QLabel(command)
        lbl_cmd.setStyleSheet("color: #BBBBBB; font-family: 'Fira Code', monospace; font-size: 11px;")
        lbl_cmd.setWordWrap(True)
        
        layout.addWidget(lbl_icon)
        layout.addWidget(lbl_cmd, 1)
