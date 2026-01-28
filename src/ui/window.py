import os
import sys
import logging
import subprocess
import json
import time
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QListWidget, QListWidgetItem, QFrame, QAbstractItemView,
                             QGraphicsDropShadowEffect, QLabel, QScrollArea, QProgressBar, QMessageBox, QGraphicsOpacityEffect)
from PyQt6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect, QRectF, QEvent, QUrl, QParallelAnimationGroup, pyqtProperty
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QPainterPath, QBrush, QLinearGradient, QDesktopServices, QCursor, QGuiApplication, QFontDatabase, QPen

from src.core.config import LOGO_PATH
from src.ui.styles import STYLE_SHEET
from src.core.ipc import start_ipc_listener
from src.services.system.app_launcher import get_app_cache

from src.ui.widgets.action_widgets import (LinkActionWidget, InstallActionWidget, FileActionWidget, 
                                         PersonActionWidget, PlaceActionWidget)
from src.ui.widgets.install_widget import InstallProgressWidget
from src.ui.widgets.command_widget import CommandLogWidget
from src.ui.widgets.misc_widgets import (ThinkingWidget, SeparatorWidget, SmoothEntryWidget, 
                                       FollowUpWidget, AnswerWidget, StandardItemWidget, 
                                       RotatingLabel, GradientBorderFrame, ReplyActionWidget)
from src.ui.widgets.list_widget import SmoothScrollListWidget

from src.ui.workers.ai_worker import AIWorker
from src.ui.workers.search_worker import SearchWorker
from src.ui.workers.action_worker import ActionWorker
from src.ui.workers.screenshot_worker import ScreenshotWorker
from src.ui.workers.install_worker import InstallOrchestrator, InstallWorker

class OmniWindow(QWidget):
    def setup_uinput(self):
        # Linux only
        if sys.platform != "linux":
            self.uinput_available = False
            return

        try:
            import uinput
            # 3840x1080 typically, but ideally we'd get this from screens
            WIDTH = 3840 
            HEIGHT = 1080
            events = (
                uinput.BTN_LEFT,
                uinput.ABS_X + (0, WIDTH, 0, 0),
                uinput.ABS_Y + (0, HEIGHT, 0, 0),
            )
            self.uinput_device = uinput.Device(events)
            logging.info("uinput device created successfully")
            self.uinput_available = True
        except ImportError:
            logging.warning("uinput module not found")
            self.uinput_available = False
        except PermissionError:
            logging.error("Permission denied for /dev/uinput")
            self.uinput_available = False
        except Exception as e:
            logging.error(f"uinput init failed: {e}")
            self.uinput_available = False

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("omni-search")
        self.setWindowIcon(QIcon(LOGO_PATH))
        self.resize(720, 160) # Slightly larger initial size
        
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(450)
        self.anim.setEasingCurve(QEasingCurve.Type.OutExpo)
        
        self.setup_uinput()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.frame = GradientBorderFrame()
        self.frame.setObjectName("MainFrame")
        
        self._is_closing = False # Flag to track animation state

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        # Content Frame (Inner)
        self.content_frame = QWidget()
        self.content_frame.setObjectName("ContentFrame")
        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        frame_layout.addWidget(self.content_frame)

        self.input_container = QWidget()
        input_layout = QHBoxLayout(self.input_container)
        input_layout.setContentsMargins(20, 4, 12, 4)
        input_layout.setSpacing(4)

        self.logo_label = RotatingLabel()
        self.logo_label.setFixedSize(50, 50)
        logo_pix = QPixmap(LOGO_PATH)
        if not logo_pix.isNull():
            self.logo_label.setPixmap(logo_pix.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Search or ask...")
        self.input_field.textChanged.connect(self.on_text_changed)
        self.input_field.returnPressed.connect(self.on_entered)
        self.input_field.installEventFilter(self)

        input_layout.addWidget(self.logo_label)
        input_layout.addWidget(self.input_field, 1)  # Stretch factor 1 = expand to fill space

        self.follow_up_widget = FollowUpWidget()
        input_layout.addWidget(self.follow_up_widget)
        
        # New: Computer Control Status Container (Hidden by default)
        self.cc_container = QWidget()
        cc_layout = QVBoxLayout(self.cc_container)
        cc_layout.setContentsMargins(0, 0, 0, 0)
        cc_layout.setSpacing(0)
        
        self.cc_title = QLabel("COMPUTER CONTROL")
        self.cc_title.setFont(QFont("Instrument Serif", 16, QFont.Weight.Bold))
        self.cc_title.setStyleSheet("color: #111111; letter-spacing: 0.5px;")
        
        self.cc_status = QLabel("Active")
        self.cc_status.setFont(QFont("Manrope", 11, QFont.Weight.Normal))
        self.cc_status.setStyleSheet("color: #888888;")
        
        cc_layout.addWidget(self.cc_title)
        cc_layout.addWidget(self.cc_status)
        
        self.cc_container.hide()
        input_layout.addWidget(self.cc_container)

        self.divider = QFrame()
        self.divider.setObjectName("Divider")

        self.list_widget = SmoothScrollListWidget()
        self.list_widget.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.itemClicked.connect(self.on_entered)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setStyleSheet("outline: none;")

        content_layout.addWidget(self.input_container)
        content_layout.addWidget(self.divider)
        content_layout.addWidget(self.list_widget)
        main_layout.addWidget(self.frame)

        self.setStyleSheet(STYLE_SHEET)

        self.chat_history = [] 
        self.is_history_mode = False

        self.apps = self.load_apps()
        self.is_entry_animating = False
        self.is_installing = False 
        self.refresh_list("", animate=False)
        self.center()  

        self.animate_entry()

        self.search_worker = None
        self.action_worker = None
        self.ai_worker = None

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(400)
        self.debounce_timer.timeout.connect(self.trigger_async_searches)

        # Start IPC Listener
        start_ipc_listener(self)

    def load_apps(self):
        return get_app_cache()

    def apply_kwin_blur(self):
        # Force KWin Blur on X11 window
        # Only for Linux
        if sys.platform != "linux": return
        try:
            wid = self.winId()
            if not wid: return
            
            rect_args = f"0, 0, {self.width()}, {self.height()}"
            
            cmd = [
                "xprop", 
                "-id", str(wid), 
                "-f", "_KDE_NET_WM_BLUR_BEHIND_REGION", "32c", 
                "-set", "_KDE_NET_WM_BLUR_BEHIND_REGION", 
                rect_args
            ]
            
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logging.error(f"KWin Blur Error: {e}")

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self.apply_kwin_blur)
        QTimer.singleShot(10, self.force_focus)

    def force_focus(self):
        self.activateWindow()
        self.raise_()
        self.input_field.setFocus()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_kwin_blur()
                
    def center(self):
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            y = max(40, y - 150)
            self.move(int(x), int(y))
        else:
            self.move(100, 100)

    def animate_entry(self):
        self.is_entry_animating = True
        self.frame.boost_speed()
        
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(0)
        self.setGraphicsEffect(self.opacity_effect)
        
        self.anim_group = QParallelAnimationGroup()
        
        def on_finished():
            self.is_entry_animating = False
            self.setGraphicsEffect(None) 
        
        self.anim_group.finished.connect(on_finished)
        
        current_y = self.y()
        start_y = current_y + 15 
        end_y = current_y
        
        self.move(self.x(), start_y)
        
        self.entry_anim_group = QParallelAnimationGroup()
        
        anim_pos = QPropertyAnimation(self, b"pos")
        anim_pos.setDuration(300)
        anim_pos.setStartValue(QPoint(self.x(), start_y))
        anim_pos.setEndValue(QPoint(self.x(), end_y))
        anim_pos.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        anim_opa = QPropertyAnimation(self.opacity_effect, b"opacity")
        anim_opa.setDuration(300)
        anim_opa.setStartValue(0.0)
        anim_opa.setEndValue(1.0)
        anim_opa.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self.anim_group.addAnimation(anim_pos)
        self.anim_group.addAnimation(anim_opa)
        self.anim_group.start()
        
        self.input_field.setFocus()
        self.activateWindow()
        self.raise_()

    def adjust_window_height(self, animate=True):
        if hasattr(self, 'is_entry_animating') and self.is_entry_animating:
            if hasattr(self, 'anim_group') and self.anim_group:
                self.anim_group.stop()
            self.is_entry_animating = False
            if hasattr(self, 'opacity_effect'):
                self.setGraphicsEffect(None)
        
        list_h = 0
        count = self.list_widget.count()
        if count > 0:
            for i in range(count):
                item = self.list_widget.item(i)
                list_h += item.sizeHint().height()
            # Cap list height
            list_h = min(list_h, 600)
        
        base_h = 70 
        new_h = base_h + list_h + 20 
        
        if self.height() != new_h:
            if animate:
                self.anim.stop()
                self.anim.setStartValue(self.geometry())
                self.anim.setEndValue(QRect(self.x(), self.y(), self.width(), new_h))
                self.anim.start()
            else:
                self.resize(self.width(), new_h)

    def eventFilter(self, obj, event):
        if obj == self.input_field and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Down:
                self.list_widget.setCurrentRow(min(self.list_widget.count()-1, self.list_widget.currentRow()+1))
                return True
            elif event.key() == Qt.Key.Key_Up:
                self.list_widget.setCurrentRow(max(0, self.list_widget.currentRow()-1))
                return True
            elif event.key() == Qt.Key.Key_Escape:
                if self.is_history_mode:
                    self.reset_to_search_mode()
                else:
                    self.animate_close()
                return True
            elif event.key() == Qt.Key.Key_Tab:
                if self.is_history_mode:
                    self.reset_to_search_mode()
                else:
                    self.enter_history_mode()
                return True
        return super().eventFilter(obj, event)

    def enter_history_mode(self):
        if self.is_history_mode: return
        self.is_history_mode = True
        self.follow_up_widget.set_active(True)
        self.frame.set_minimal_mode(False) # Colorful mode for chat
        
        # Clear list but keep history if we want to show it?
        # Actually, if we just entered history mode, we might want to show previous chat
        # For now, just clear visual list and let user type follow-up
        self.list_widget.clear()
        
        # Re-populate with chat history if any?
        for msg in self.chat_history:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if role == 'user':
                self.add_list_item(StandardItemWidget(f"You: {content}", icon_name="user-available"), "history_user")
            else:
                self.add_list_item(AnswerWidget(content), "history_ai")
        
        self.adjust_window_height()

    def reset_to_search_mode(self):
        self.is_history_mode = False
        self.follow_up_widget.set_active(False)
        self.frame.set_minimal_mode(True) # Minimal mode for search
        self.input_field.clear()
        self.refresh_list("")

    def toggle_visibility_safe(self):
        if self.isVisible():
            self.animate_close()
        else:
            self.show()
            self.center()
            self.animate_entry()
            self.input_field.setFocus()
            self.input_field.selectAll()

    def animate_close(self):
        if self._is_closing: return
        self._is_closing = True
        
        self.anim_close = QPropertyAnimation(self, b"windowOpacity")
        self.anim_close.setDuration(200)
        self.anim_close.setStartValue(1.0)
        self.anim_close.setEndValue(0.0)
        self.anim_close.finished.connect(self.close)
        self.anim_close.start()

    def closeEvent(self, event):
        self._is_closing = False
        self.setWindowOpacity(1.0) # Reset for next show
        super().closeEvent(event)

    def on_text_changed(self, text):
        if self.is_history_mode: return # Don't auto-search in chat mode
        
        if not text.strip():
            self.refresh_list("", animate=False)
            self.frame.set_minimal_mode(True)
            return

        self.frame.set_minimal_mode(True)
        self.refresh_list(text)
        self.debounce_timer.start()

    def refresh_list(self, query, animate=True):
        self.list_widget.clear()
        
        if not query:
            self.adjust_window_height(animate)
            return

        # 1. Local Apps (Fast)
        query = query.lower()
        matches = []
        for name, data in self.apps.items():
            if query in name:
                matches.append((name, data))
        
        # Sort matches: exact/prefix first
        matches.sort(key=lambda x: 0 if x[0].startswith(query) else 1)
        
        for name, data in matches[:5]:
            # Use SmoothEntryWidget for animation
            icon_path = data.get('icon') or name
            w = StandardItemWidget(data['orig_name'], icon_name=icon_path)
            self.add_list_item(w, data)

        self.adjust_window_height(animate)

    def add_list_item(self, widget, data):
        item = QListWidgetItem()
        item.setSizeHint(widget.sizeHint())
        item.setData(Qt.ItemDataRole.UserRole, data)
        self.list_widget.addItem(item)
        
        # Wrap in SmoothEntryWidget for fade-in
        anim_w = SmoothEntryWidget(widget)
        self.list_widget.setItemWidget(item, anim_w)

    def trigger_async_searches(self):
        query = self.input_field.text().strip()
        if not query or self.is_history_mode: return

        # Start Workers
        if self.search_worker and self.search_worker.isRunning(): self.search_worker.terminate()
        self.search_worker = SearchWorker(query)
        self.search_worker.results_found.connect(self.on_search_results)
        self.search_worker.start()

        if self.action_worker and self.action_worker.isRunning(): self.action_worker.terminate()
        self.action_worker = ActionWorker(query)
        self.action_worker.action_found.connect(self.on_action_found)
        self.action_worker.start()

    def on_search_results(self, results, query):
        if self.input_field.text().strip() != query: return
        
        # Append results
        for res in results:
            if res.get('type') == 'file':
                w = FileActionWidget(res['name'], res['path'])
                self.add_list_item(w, {"type": "open_file", "path": res['path']})
        
        self.adjust_window_height()

    def on_action_found(self, actions, query):
        if self.input_field.text().strip() != query: return
        
        for act in actions:
            if act.get('type') == 'link':
                w = LinkActionWidget(act['title'], act['url'], act['description'])
                self.add_list_item(w, act)
            elif act.get('type') == 'install':
                w = InstallActionWidget(act['name'], act.get('website'))
                self.add_list_item(w, act)
            elif act.get('type') == 'person':
                w = PersonActionWidget(act['name'], act['description'], act.get('image'), act.get('url'))
                self.add_list_item(w, act)
            elif act.get('type') == 'place':
                w = PlaceActionWidget(act['name'], act['address'], act.get('image'), act.get('url'), act.get('latitude'), act.get('longitude'))
                self.add_list_item(w, act)
        
        self.adjust_window_height()

    def on_entered(self, item=None):
        if not item:
            item = self.list_widget.currentItem()
        
        # If no item selected (Enter in box), use text
        if not item:
            query = self.input_field.text().strip()
            if query:
                self.perform_ai_query(query)
            return

        data = item.data(Qt.ItemDataRole.UserRole)
        
        if isinstance(data, dict):
            if data.get('type') == 'link':
                QDesktopServices.openUrl(QUrl(data['url']))
                self.animate_close()
            elif data.get('type') == 'install':
                self.start_install(data['name'])
            elif data.get('type') == 'open_file':
                QDesktopServices.openUrl(QUrl(f"file://{data['path']}"))
                self.animate_close()
            elif 'cmd' in data: # App from cache
                try:
                    subprocess.Popen(data['cmd'], shell=True, start_new_session=True)
                    self.animate_close()
                except: pass
        else:
            # Fallback
            query = self.input_field.text().strip()
            if query: self.perform_ai_query(query)

    def start_install(self, app_name):
        self.list_widget.clear()
        self.input_field.setDisabled(True)
        self.input_field.setText(f"Installing {app_name}...")
        
        # Add Progress Widget
        self.install_widget = InstallProgressWidget(app_name)
        self.install_widget.candidate_confirmed.connect(self.on_install_candidate_confirmed)
        
        item = QListWidgetItem()
        item.setSizeHint(self.install_widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, self.install_widget)
        self.adjust_window_height()
        
        # Start Orchestrator
        self.install_worker = InstallOrchestrator(app_name)
        self.install_worker.status_update.connect(self.install_widget.update_status)
        self.install_worker.log_entry.connect(self.install_widget.add_log)
        self.install_worker.finished.connect(self.install_widget.set_finished)
        self.install_worker.candidates_found.connect(self.install_widget.show_candidates)
        self.install_worker.start()

    def on_install_candidate_confirmed(self, pkg_data):
        # Restart orchestrator with forced package
        self.install_worker = InstallOrchestrator(pkg_data['name'], forced_package=pkg_data)
        self.install_worker.status_update.connect(self.install_widget.update_status)
        self.install_worker.log_entry.connect(self.install_widget.add_log)
        self.install_worker.finished.connect(self.install_widget.set_finished)
        self.install_worker.start()

    def perform_ai_query(self, query):
        self.list_widget.clear()
        self.frame.set_minimal_mode(False) # Active mode
        self.logo_label.boost_speed()
        
        # Add Thinking Widget
        self.thinking_widget = ThinkingWidget(f"Thinking about '{query}'...")
        self.add_list_item(self.thinking_widget, "thinking")
        self.adjust_window_height()
        
        # Screenshot?
        screenshot_b64 = None
        # Logic to decide if we need screenshot is now in AIWorker or Brain
        # But if we want to send it, we need to take it here.
        # Let's take it if query implies it, OR always?
        # Taking screenshot is expensive?
        # Let's try taking it if "screen" keyword or similar is in query
        if any(x in query.lower() for x in ["screen", "look", "see", "window", "display"]):
            self.screenshot_worker = ScreenshotWorker()
            self.screenshot_worker.finished.connect(lambda b64: self.start_ai_worker(query, b64))
            self.screenshot_worker.failed.connect(lambda: self.start_ai_worker(query, None))
            self.screenshot_worker.start()
        else:
            self.start_ai_worker(query, None)

    def start_ai_worker(self, query, screenshot_b64):
        self.ai_worker = AIWorker(query, self.chat_history, screenshot_b64)
        self.ai_worker.finished.connect(self.on_ai_response)
        self.ai_worker.start()

    def on_ai_response(self, data):
        self.logo_label.stop_spinning()
        
        # Remove thinking widget? Or update it?
        # Let's clear list for the answer
        self.list_widget.clear()
        
        answer = data.get("answer", "")
        actions = data.get("actions", [])
        special = data.get("special_action")

        if special == "screenshot_required":
            # Retry with screenshot
            self.screenshot_worker = ScreenshotWorker()
            self.screenshot_worker.finished.connect(lambda b64: self.start_ai_worker(self.input_field.text(), b64))
            self.screenshot_worker.start()
            return

        # Add Answer
        if answer:
            self.chat_history.append({"role": "user", "content": self.input_field.text()})
            self.chat_history.append({"role": "assistant", "content": answer})
            
            w = AnswerWidget(answer)
            self.add_list_item(w, "answer")
        
        # Add Actions
        for act in actions:
            if isinstance(act, dict):
                if act.get('type') == 'link':
                    w = LinkActionWidget(act['title'], act['url'], act['description'])
                    self.add_list_item(w, act)
                elif act.get('type') == 'install':
                    w = InstallActionWidget(act['name'], act.get('website'))
                    self.add_list_item(w, act)
                elif act.get('type') == 'status':
                    # Maybe just a small notification or standard item
                    w = StandardItemWidget(act['description'], icon_name="dialog-information")
                    self.add_list_item(w, act)
        
        self.enter_history_mode()
        self.adjust_window_height()
