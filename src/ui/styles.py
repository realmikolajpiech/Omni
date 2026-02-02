STYLE_SHEET = """
/* Global Reset */
* {
    outline: none;
}

/* Main Window Container */
QWidget {
    background-color: transparent;
    color: #0F0F0F;
}

QFrame#MainFrame {
    /* Background handled by custom paintEvent for flowing gradient */
    border-radius: 24px;
    padding: 2px; /* The width of the border */
}

QWidget#ContentFrame {
    background-color: transparent; /* Handled by GradientBorderFrame for glass effect */
    border-radius: 22px; /* 24px - 2px padding */
}

/* Input Field - Editorial Style */
QLineEdit {
    background-color: transparent;
    border: none;
    padding: 12px 16px;
    font-family: "Instrument Serif";
    font-style: italic;
    font-size: 34px;
    color: #111111;
    selection-background-color: #E0E0E0;
    selection-color: #000000;
}

QLineEdit::placeholder {
    color: rgba(0, 0, 0, 0.25);
    font-family: "Instrument Serif";
    font-style: italic;
}

/* Divider Line - Barely There */
QFrame#Divider {
    background-color: rgba(0, 0, 0, 0.03);
    min-height: 1px;
    max-height: 1px;
    margin: 0px 32px;
}

/* Result List */
QListWidget {
    background-color: transparent;
    border: none;
    padding: 12px 16px;
}

QListWidget::item {
    padding: 0px;
    margin-bottom: 6px;
    border-radius: 16px;
    color: #333333;
    font-family: "Manrope";
    font-size: 15px;
}

QListWidget::item:selected {
    background-color: transparent;
}

QListWidget::item:selected:hover {
    background-color: transparent;
}

QListWidget::item:hover {
    background-color: transparent;
    font-weight: 500;
    border: 1px solid transparent;
    background-color: rgba(255, 255, 255, 0.0); /* Transparent by default */
    transition: background-color 0.2s ease;
}

/* Selected Item - Active State */
QListWidget::item:selected {
    background-color: rgba(0, 0, 0, 0.04); /* Very subtle selection */
    color: #000000;
    border: none;
}

/* Custom Scrollbar - Invisible but functional */
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 6px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: rgba(0, 0, 0, 0.15);
    min-height: 40px;
    border-radius: 3px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
