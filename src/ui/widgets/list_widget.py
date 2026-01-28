from PyQt6.QtWidgets import QListWidget, QAbstractItemView
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

class SmoothScrollListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value")
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutQuart) # Premium feel
        self._scroll_anim.setDuration(600) # Longer glide
        self._target_val = 0

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
