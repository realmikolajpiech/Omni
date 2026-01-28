from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTextEdit, QListWidget, QListWidgetItem, QPushButton
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont

class InstallProgressWidget(QWidget):
    candidate_confirmed = pyqtSignal(object)

    def __init__(self, app_name, parent=None):
        super().__init__(parent)
        self.app_name = app_name
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Card Container
        self.card = QWidget()
        self.card.setObjectName("InstallCard")
        self.card.setStyleSheet("""
            QWidget#InstallCard {
                background-color: rgba(255, 255, 255, 0.25);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }
        """)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(16)

        # 1. Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 8, 0, 4) # Added top margin
        header_layout.setSpacing(0)
        
        title_col_layout = QVBoxLayout()
        title_col_layout.setSpacing(2) # Little bit of breathing room
        title_col_layout.setContentsMargins(0, 0, 0, 0)
        
        # Format display name nicely
        display_name = app_name.replace('-', ' ').title()
        
        self.title_label = QLabel(f"Installing {display_name}...")
        self.title_label.setWordWrap(False) 
        self.title_label.setFont(QFont("Instrument Serif", 22, QFont.Weight.Normal))
        # Padding for glyph, negative margin to pull visual alignment back. increased vertical padding.
        self.title_label.setStyleSheet("color: #111111; line-height: 1.5; padding: 8px 0px 8px 8px; margin-left: -4px; background: transparent;") 
        
        self.status_label = QLabel("Initializing...")
        self.status_label.setWordWrap(True)
        self.status_label.setFont(QFont("Manrope", 11, QFont.Weight.Medium))
        self.status_label.setStyleSheet("color: #666666; background: transparent;")
        
        title_col_layout.addWidget(self.title_label)
        title_col_layout.addWidget(self.status_label)
        
        header_layout.addLayout(title_col_layout)
        header_layout.addStretch()
        
        card_layout.addLayout(header_layout)

        # 2. Progress Bar
        self.progress_bar = QProgressBar() 
        self.progress_bar.setRange(0, 100) 
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4) 
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: rgba(0, 0, 0, 0.05);
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #667EEA, stop:1 #764BA2);
                border-radius: 2px;
            }
        """)

        # Sub Status (Removed "Searching for..." by default, use for errors/details)
        self.sub_status_label = QLabel("") 
        self.sub_status_label.setWordWrap(True)
        self.sub_status_label.setFont(QFont("Manrope", 11))
        self.sub_status_label.setStyleSheet("color: #888888; padding-top: 4px;")
        self.sub_status_label.hide()
        
        # Log View (Hidden by default, for verbose output)
        self.log_view = QTextEdit() 
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(120) 
        self.log_view.setStyleSheet("""
            QTextEdit {
                background: rgba(0,0,0,0.05);
                border-radius: 6px;
                padding: 8px;
                color: #444;
                font-family: monospace;
                font-size: 11px;
                border: none;
            }
        """)
        self.log_view.hide() 
        
        # Error/Action Container
        self.action_layout = QHBoxLayout()
        self.action_layout.setSpacing(12)
        
        card_layout.addWidget(self.progress_bar)
        card_layout.addWidget(self.sub_status_label)
        card_layout.addLayout(self.action_layout)
        card_layout.addWidget(self.log_view)

        # Selection List (New!)
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: 1px solid rgba(0,0,0,0.1);
                border-radius: 8px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid rgba(0,0,0,0.05);
                color: #333;
            }
            QListWidget::item:selected {
                background: #007AFF;
                color: white;
            }
        """)
        self.list_widget.hide()
        self.list_widget.setFixedHeight(150)
        self.list_widget.itemClicked.connect(self.on_candidate_selected)
        card_layout.addWidget(self.list_widget)

        layout.addWidget(self.card)

    def on_candidate_selected(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        self.candidate_confirmed.emit(data)

    def show_candidates(self, candidates):
        self.list_widget.clear()
        self.progress_bar.hide()
        self.status_label.setText(f"Found {len(candidates)} candidates. Please select one:")
        self.list_widget.show()
        
        for c in candidates:
            # Format: Display Name (Details)
            details = c.get('description', c.get('name'))[:60]
            txt = f"{c.get('display_name', c.get('name'))} - {details}..."
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.list_widget.addItem(item)
            
        if hasattr(self.window(), 'adjust_window_height'):
            self.window().adjust_window_height()

    def update_status(self, text):
        self.status_label.setText(text)
        if "Searching" in text:
             self.sub_status_label.setText(text)
             pass
        else:
             self.status_label.setText(text)

    def add_log(self, text):
        if self.log_view.isHidden():
             self.log_view.show()
             if hasattr(self.window(), 'adjust_window_height'):
                 self.window().adjust_window_height()
        self.log_view.append(text)
        # Auto scroll
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def update_progress(self, val):
        self.progress_bar.setValue(int(val)) 

    def set_finished(self, success, message):
        print(f"DEBUG: set_finished success={success} message={message}")
        if success:
            self.progress_bar.setValue(100)
            self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #34C759; border-radius: 4px; } QProgressBar { background: rgba(0,0,0,0.05); border-radius: 4px; text-align: center; }")
            
            self.title_label.setText(f"Installed {self.app_name.replace('-', ' ').title()}")
            self.status_label.setText("Success!") 
            self.status_label.setStyleSheet("color: #34C759; font-weight: bold; font-size: 16px;") 
            
            self.sub_status_label.setText(message)
            self.sub_status_label.show()
        else:
            self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #FF3B30; border-radius: 4px; } QProgressBar { background: rgba(0,0,0,0.05); border-radius: 4px; text-align: center; }")
            
            self.title_label.setText("Installation Failed")
            self.status_label.setText("Error Occurred")
            self.status_label.setStyleSheet("color: #FF3B30; font-weight: bold;") 
            
            self.sub_status_label.setText(message)
            self.sub_status_label.setStyleSheet("color: #FF3B30; font-weight: bold;")
            self.sub_status_label.show()
            
            if self.action_layout.count() == 0:
                btn = QPushButton("Close")
                btn.setFixedSize(60, 32)
                btn.setStyleSheet("""
                QPushButton { background-color: #FF3B30; color: white; border-radius: 8px; font-weight: bold; }
                QPushButton:hover { background-color: #D32F2F; }
                """)
                # lambda must capture self strongly?
                # Using lambda with window().animate_close()
                btn.clicked.connect(lambda: self.window().animate_close() if hasattr(self.window(), 'animate_close') else self.window().close())
                self.action_layout.addWidget(btn)

    def sizeHint(self):
        return QSize(660, 160) 
