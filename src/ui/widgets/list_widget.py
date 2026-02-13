from PyQt6.QtWidgets import QListWidget, QAbstractItemView, QStyledItemDelegate, QStyle
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve

class SelectiveHoverDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_list = parent

    def paint(self, painter, option, index):
        # Get data to check type
        data = index.data(Qt.ItemDataRole.UserRole)
        role_type = None
        
        # Handle dict data (actions) vs string data (internal types)
        if isinstance(data, dict):
             role_type = data.get('type') # e.g. 'link', 'install'
        elif isinstance(data, str):
             role_type = data # 'answer', 'thinking', 'separator'
             
        if self._parent_list and getattr(self._parent_list, '_keyboard_locked', False):
            # If keyboard is locked, force remove MouseOver state from ALL items
            option.state &= ~QStyle.StateFlag.State_MouseOver

        # If it's thinking or answer, we clear the hover/selected state from option
        # This prevents the QListWidget stylesheet from applying the background
        if role_type in ['thinking', 'answer', 'separator', 'history_ai']:
            option.state &= ~QStyle.StateFlag.State_MouseOver
            option.state &= ~QStyle.StateFlag.State_Selected
            option.state &= ~QStyle.StateFlag.State_HasFocus
            
        super().paint(painter, option, index)

class SmoothScrollListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Use our custom delegate to control hover effects per-item
        self.setItemDelegate(SelectiveHoverDelegate(self))
        
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Prevent any horizontal scroll (range 0-0)
        self.horizontalScrollBar().setRange(0, 0)
        self._scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value")
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutQuart) # Premium feel
        self._scroll_anim.setDuration(600) # Longer glide
        self._target_val = 0
        
        # Track keyboard vs mouse selection
        self._keyboard_locked = False  # When True, ignore mouse hover
        self.itemEntered.connect(self._on_item_entered)
    
    def _on_item_entered(self, item):
        """Called when mouse hovers over an item - hover IS selection."""
        # If keyboard is locked (arrow keys were pressed), ignore mouse
        if self._keyboard_locked:
            return
        
        if item:
            row = self.row(item)
            if row >= 0 and self.currentRow() != row:
                # Hover IS selection - directly select the hovered item
                self.blockSignals(True)
                self.setCurrentRow(row)
                self.blockSignals(False)

    def mouseMoveEvent(self, event):
        """Handle mouse movement - unlocks keyboard lock."""
        # Only unlock if mouse actually moved significantly or entered new item
        # But here we just unlock to allow hover effects to resume
        if self._keyboard_locked:
            self._keyboard_locked = False
            # Force re-evaluation of hover state under cursor
            item = self.itemAt(event.position().toPoint())
            if item:
                self._on_item_entered(item)
                
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        # Calculate delta
        delta = -event.angleDelta().y() 
        
        # Scaling factor for scroll speed
        delta = int(delta * 0.8) # Reduce sensitivity
        
        current_val = self.verticalScrollBar().value()
        
        if self._scroll_anim.state() == QPropertyAnimation.State.Running:
             self._scroll_anim.stop()
             # Accumulate momentum: Start from current, aim for accumulated target
             self._target_val += delta
        else:
             self._target_val = current_val + delta
             
        # Clamp
        min_val = self.verticalScrollBar().minimum()
        max_val = self.verticalScrollBar().maximum()
        self._target_val = max(min_val, min(max_val, self._target_val))
        
        self._scroll_anim.setStartValue(current_val)
        self._scroll_anim.setEndValue(self._target_val)
        self._scroll_anim.start()
        
        event.accept()
    
    def keyPressEvent(self, event):
        """Handle keyboard navigation - locks out mouse cursor."""
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            # Keyboard navigation locks out the mouse cursor
            self._keyboard_locked = True
            
            # When using keyboard, we want to ensure the selection is visually clear
            # The style sheet handles selected:!active vs selected:active
            # But we need to ensure previous hover states are cleared if they persist
            
        super().keyPressEvent(event)
