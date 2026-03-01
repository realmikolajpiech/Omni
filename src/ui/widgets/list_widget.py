from PyQt6.QtWidgets import QListWidget, QAbstractItemView, QStyledItemDelegate, QStyle
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer

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
            # Also clear the HasFocus state if it's not the selected item?
            # No, keep it simple first.
            # print(f"DEBUG: Keyboard Locked. Removing MouseOver from row {index.row()}. New state: {option.state}")

        # If it's thinking, answer, or a non-interactive action widget, clear hover/selected state
        # This prevents the QListWidget stylesheet from applying the background rectangle
        if role_type in ['thinking', 'answer', 'separator', 'history_ai', 'system_settings', 'trust_permission', 'terminal_command']:
            option.state &= ~QStyle.StateFlag.State_MouseOver
            option.state &= ~QStyle.StateFlag.State_Selected
            option.state &= ~QStyle.StateFlag.State_HasFocus
            
        if role_type == 'ask_omni' and isinstance(data, dict) and data.get('is_only_item'):
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
        self._last_mouse_pos = None
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
        curr_pos = event.position().toPoint()
        
        # If we haven't tracked a position yet (first move), assume this is the start
        if self._last_mouse_pos is None:
            self._last_mouse_pos = curr_pos
        
        if self._keyboard_locked:
            # Check for significant movement (jitter protection)
            if self._last_mouse_pos:
                dist = (curr_pos - self._last_mouse_pos).manhattanLength()
                if dist < 5: # Increased threshold slightly
                    return

            self._keyboard_locked = False
            self.viewport().update()
            
            # Force re-evaluation of hover state under cursor
            item = self.itemAt(curr_pos)
            if item:
                self._on_item_entered(item)
        
        self._last_mouse_pos = curr_pos
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
            
            # Capture current mouse position to prevent immediate unlock on accidental jitter
            from PyQt6.QtGui import QCursor
            # Map global cursor to local coordinates for consistency with mouseMoveEvent
            global_pos = QCursor.pos()
            local_pos = self.mapFromGlobal(global_pos)
            self._last_mouse_pos = local_pos
            
            # Force update of the viewport to apply the delegate change immediately
            # We schedule it for next loop to ensure state is propagated
            QTimer.singleShot(0, self.viewport().update)
            
            # Also update immediately
            self.viewport().update()
            
            # When using keyboard, we want to ensure the selection is visually clear
            # The style sheet handles selected:!active vs selected:active
            # But we need to ensure previous hover states are cleared if they persist
            
        super().keyPressEvent(event)
