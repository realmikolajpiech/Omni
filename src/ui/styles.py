
THEMES = {
    "dark": {
        "text_primary": "#FFFFFF",
        "text_secondary": "#AAAAAA", # Changed back to #AAAAAA for gray styling
        "text_input": "#FFFFFF",
        "placeholder": "rgba(255, 255, 255, 0.6)", # Increased opacity from 0.3
        "selection_bg": "#444444",
        "selection_text": "#FFFFFF",
        "divider": "#30FFFFFF", # Increased opacity from 0.1
        "list_item_hover": "rgba(255, 255, 255, 0.15)", # Increased opacity
        "list_item_selected": "rgba(255, 255, 255, 0.20)", # Increased opacity
        "scrollbar_handle": "#60FFFFFF", # Increased opacity
        "border_color": "#40FFFFFF", # Increased opacity
        "base_fill_color": "#0A000000", # rgba(0, 0, 0, 10/255=0.04) -> 0x0A=10. Pure black fill with low alpha.
        "glass_tint_white": 0.08, # Restored to original value
        "glass_tint_alpha": 0.40,  # Restored to original value
        "selection_border": "rgba(255, 255, 255, 0.3)" # Increased opacity
    },
    "light": {
        "text_primary": "#111111",
        "text_secondary": "#666666",
        "text_input": "#111111",
        "placeholder": "rgba(0, 0, 0, 0.3)",
        "selection_bg": "#E0E0E0",
        "selection_text": "#000000",
        "divider": "#0D000000", # rgba(0, 0, 0, 0.05)
        "list_item_hover": "rgba(0, 0, 0, 0.06)",
        "list_item_selected": "rgba(0, 0, 0, 0.10)",
        "scrollbar_handle": "#26000000", # rgba(0, 0, 0, 0.15)
        "border_color": "#1E000000", # rgba(0, 0, 0, 30/255=0.11) -> 0x1E=30
        "base_fill_color": "#40FFFFFF", # Semi-transparent white overlay (25%)
        "glass_tint_white": 0.90,
        "glass_tint_alpha": 0.20,
        "selection_border": "rgba(0, 0, 0, 0.15)"
    }
}

def get_style_sheet(theme="light"):
    t = THEMES.get(theme, THEMES["light"])
    return f"""
/* Global Reset */
* {{
    outline: none;
}}

/* Main Window Container */
QWidget {{
    background-color: transparent;
    color: {t['text_primary']};
}}

QFrame#MainFrame {{
    /* Background handled by custom paintEvent for flowing gradient */
    border-radius: 24px;
    padding: 2px; /* The width of the border */
}}

QWidget#ContentFrame {{
    background-color: transparent; /* Handled by GradientBorderFrame for glass effect */
    border-radius: 22px; /* 24px - 2px padding */
}}

/* Input Field - Editorial Style */
QLineEdit {{
    background-color: transparent;
    border: 1px solid transparent;
    outline: none;
    padding: 12px 16px;
    font-family: "Instrument Serif";
    font-style: italic;
    font-size: 34px;
    color: {t['text_input']};
    selection-background-color: {t['selection_bg']};
    selection-color: {t['selection_text']};
}}

QLineEdit:focus {{
    border: 1px solid transparent;
    outline: none;
}}

QLineEdit[readOnly="true"] {{
    color: {t['text_secondary']};
}}

QLineEdit::placeholder {{
    color: {t['placeholder']};
    font-family: "Instrument Serif";
    font-style: italic;
}}

/* Divider Line - Barely There */
QFrame#Divider {{
    background-color: {t['divider']};
    min-height: 1px;
    max-height: 1px;
    margin: 0px 32px;
}}

/* Result List */
QListWidget {{
    background-color: transparent;
    border: none;
    padding: 12px 16px;
    outline: none; /* Removes the dotted focus line */
}}

QListWidget::item {{
    padding: 0px;
    margin-bottom: 6px;
    border-radius: 16px;
    color: {t['text_primary']};
    font-family: "Manrope";
    font-size: 15px;
    /* Explicitly set border to none for non-selected items to avoid layout shift */
    border: 1px solid transparent; 
}}

QListWidget::item:selected {{
    background-color: {t['list_item_selected']};
    color: {t['text_primary']};
    border: 1px solid {t['selection_border']};
}}

QListWidget::item:selected:active {{
    background-color: {t['list_item_selected']};
    color: {t['text_primary']};
    border: 1px solid {t['selection_border']};
}}

QListWidget::item:selected:!active {{
    background-color: {t['list_item_selected']};
    color: {t['text_primary']};
    border: 1px solid {t['selection_border']};
}}

/* 
   UPDATE: We removed :hover style because "Hover IS Selection".
   If we have both :selected and :hover, and we move selection with keyboard,
   the item under mouse stays :hover but not :selected, causing double highlight.
   By removing :hover style, only the Selected item is highlighted.
   Since hovering an item triggers selection (via _on_item_entered), 
   mouse users still get the highlight.
*/
/*
QListWidget::item:hover {{
    background-color: {t['list_item_selected']};
    color: {t['text_primary']};
    border: 1px solid {t['selection_border']};
}}
*/

/* Custom Scrollbar - Invisible but functional */
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 6px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {t['scrollbar_handle']};
    min-height: 40px;
    border-radius: 3px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""
