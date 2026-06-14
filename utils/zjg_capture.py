"""
Maya 现代化截屏录屏工具 - 独立工具栏版本（可输入分辨率，可移动选区）
功能：屏幕选区截图、录屏（GIF 动图），默认分辨率 512x512
"""

import os
import ctypes
import tempfile
from datetime import datetime
from PIL import Image

try:
    from PySide6 import QtCore
    from PySide6 import QtGui
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtCore
    from PySide2 import QtGui
    from PySide2 import QtWidgets


class ToolBar(QtWidgets.QWidget):
    """独立置顶工具栏窗口"""
    drag_delta = QtCore.Signal(int, int)
    dragging_changed = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super(ToolBar, self).__init__(parent)
        self.setWindowTitle("截图工具")
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Window
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("""
            QWidget#toolbarWidget {
                background-color: #f8f8f8;
                border-radius: 10px;
                border: 2px solid #555555;
            }
            QPushButton {
                background-color: #f0f0f0;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
            QLineEdit {
                background-color: #f0f0f0;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
                font-family: monospace;
            }
            QWidget#dragHandle {
                background-color: transparent;
                border-left: 1px solid #cccccc;
                border-radius: 0px;
            }
            QWidget#dragHandle:hover {
                background-color: rgba(0, 0, 0, 40);
            }
            QWidget#dragHandleLeft {
                background-color: transparent;
                border-right: 1px solid #cccccc;
                border-radius: 0px;
            }
            QWidget#dragHandleLeft:hover {
                background-color: rgba(0, 0, 0, 40);
            }
            QCheckBox {
                color: #000000;
                font-size: 12px;
                spacing: 3px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #bbbbbb;
                border-radius: 3px;
                background-color: #ffffff;
            }
            QCheckBox::indicator:checked {
                background-color: #3b82f6;
                border-color: #2563eb;
            }
        """)
        self.setObjectName("toolbarWidget")

        self._drag_start = QtCore.QPoint()
        self._is_dragging = False
        self._drag_activated = False
        self._drag_pressed_widget = None
        self._fps_values = [60, 30, 25, 24, 15, 12, 10, 8, 5, 4, 2, 1]
        self._fps_index = 1

        layout = QtWidgets.QHBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 6, 12, 6)

        self.width_input = QtWidgets.QLineEdit("512")
        self.width_input.setPlaceholderText("宽")
        self.width_input.setAlignment(QtCore.Qt.AlignCenter)
        self.width_input.setToolTip("宽度，按回车应用")
        self.width_input.setFixedWidth(50)
        layout.addWidget(self.width_input)

        x_label = QtWidgets.QLabel("×")
        x_label.setFixedWidth(14)
        x_label.setAlignment(QtCore.Qt.AlignCenter)
        x_label.setStyleSheet("color: #666666; font-size: 13px; border: none; background: transparent;")
        layout.addWidget(x_label)

        self.height_input = QtWidgets.QLineEdit("512")
        self.height_input.setPlaceholderText("高")
        self.height_input.setAlignment(QtCore.Qt.AlignCenter)
        self.height_input.setToolTip("高度，按回车应用")
        self.height_input.setFixedWidth(50)
        layout.addWidget(self.height_input)

        self.force_res_cb = QtWidgets.QCheckBox("强制")
        self.force_res_cb.setChecked(True)
        self.force_res_cb.setToolTip("勾选时将截图缩放至输入分辨率")
        layout.addWidget(self.force_res_cb)

        self.lock_btn = QtWidgets.QPushButton("\U0001F513")
        self.lock_btn.setFixedSize(30, 30)
        self.lock_btn.setToolTip("锁定/解锁分辨率")
        self.lock_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        layout.addWidget(self.lock_btn)

        self.path_btn = QtWidgets.QPushButton("📁")
        self.path_btn.setFixedSize(30, 30)
        self.path_btn.setToolTip("选择保存路径")
        self.path_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 6px;
                font-size: 14px;
                padding: 0px;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        layout.addWidget(self.path_btn)

        layout.addStretch()

        self.screenshot_btn = QtWidgets.QPushButton("📸 截图")
        self.screenshot_btn.setMinimumWidth(120)

        self.record_btn = QtWidgets.QPushButton("🎥 录屏")
        self.record_btn.hide()  # 录屏功能保留代码但不显示

        self.fps_label = QtWidgets.QLabel("30 FPS")
        self.fps_label.setFixedWidth(60)
        self.fps_label.setAlignment(QtCore.Qt.AlignCenter)
        self.fps_label.setToolTip("滚轮调整帧率")
        self.fps_label.setStyleSheet("""
            color: #000000;
            font-size: 12px;
            background-color: #f0f0f0;
            border: 1px solid #cccccc;
            border-radius: 6px;
            padding: 4px 6px;
        """)
        self.fps_label.hide()  # 录屏已移除，FPS 不显示
        layout.addWidget(self.screenshot_btn)
        # record_btn 和 fps_label 保留代码但不显示（录屏功能鸡肋）
        # layout.addWidget(self.record_btn)
        # layout.addWidget(self.fps_label)

        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setToolTip("关闭")
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 6px;
                font-size: 14px;
                padding: 0px;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        layout.addWidget(self.close_btn)

        self.adjustSize()

        for child in self.findChildren(QtWidgets.QWidget):
            if child.objectName() not in ("dragHandle", "dragHandleLeft"):
                child.installEventFilter(self)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start = QtGui.QCursor.pos()
            self._is_dragging = True
            self._drag_activated = False
            self.dragging_changed.emit(True)

    def mouseMoveEvent(self, event):
        if self._is_dragging and event.buttons() & QtCore.Qt.LeftButton:
            current = QtGui.QCursor.pos()
            dx = current.x() - self._drag_start.x()
            dy = current.y() - self._drag_start.y()
            if not self._drag_activated:
                if abs(dx) > 4 or abs(dy) > 4:
                    self._drag_activated = True
            if self._drag_activated:
                self.drag_delta.emit(dx, dy)
                self._drag_start = current

    def mouseReleaseEvent(self, event):
        if self._is_dragging and self._drag_activated:
            self.releaseMouse()
        self._is_dragging = False
        self._drag_activated = False
        self._drag_pressed_widget = None
        self.dragging_changed.emit(False)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Wheel:
            # 分辨率输入框滚轮切换常用分辨率
            _resolutions = [360, 480, 512, 720, 768, 960, 1024, 1440, 1920, 2560]
            for input_widget, attr in [(self.width_input, '_width_ridx'),
                                        (self.height_input, '_height_ridx')]:
                if obj is input_widget:
                    cur_val = int(input_widget.text()) if input_widget.text().isdigit() else 512
                    delta = event.angleDelta().y()
                    idx = getattr(self, attr, 0)
                    # 找到当前值在列表中的位置
                    if cur_val in _resolutions:
                        idx = _resolutions.index(cur_val)
                    if delta > 0:
                        idx = (idx + 1) % len(_resolutions)
                    else:
                        idx = (idx - 1) % len(_resolutions)
                    setattr(self, attr, idx)
                    input_widget.setText(str(_resolutions[idx]))
                    input_widget.editingFinished.emit()
                    event.accept()
                    return True

        if event.type() == QtCore.QEvent.Wheel and obj is self.fps_label:
            delta = event.angleDelta().y()
            if delta > 0:
                idx = (self._fps_index - 1) % len(self._fps_values)
            else:
                idx = (self._fps_index + 1) % len(self._fps_values)
            self._fps_index = idx
            self.fps_label.setText(f"{self._fps_values[idx]} FPS")
            event.accept()
            return True

        if event.type() == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton:
                self._drag_start = QtGui.QCursor.pos()
                self._is_dragging = True
                self._drag_activated = False
                self._drag_pressed_widget = obj
            return False

        if event.type() == QtCore.QEvent.MouseMove:
            if self._is_dragging and not self._drag_activated:
                current = QtGui.QCursor.pos()
                if (current - self._drag_start).manhattanLength() > 4:
                    self._drag_activated = True
                    self.grabMouse()
                    if self._drag_pressed_widget is not None:
                        if isinstance(self._drag_pressed_widget, QtWidgets.QAbstractButton):
                            self._drag_pressed_widget.setDown(False)
                        self._drag_pressed_widget = None
            if self._is_dragging and self._drag_activated:
                current = QtGui.QCursor.pos()
                dx = current.x() - self._drag_start.x()
                dy = current.y() - self._drag_start.y()
                self.drag_delta.emit(dx, dy)
                self._drag_start = current
                return True
            return False

        if event.type() == QtCore.QEvent.MouseButtonRelease:
            if self._is_dragging and self._drag_activated:
                self.releaseMouse()
                self._is_dragging = False
                self._drag_activated = False
                self._drag_pressed_widget = None
                self.dragging_changed.emit(False)
                return True
            self._is_dragging = False
            self._drag_activated = False
            self._drag_pressed_widget = None
            self.dragging_changed.emit(False)
            return False

        return super(ToolBar, self).eventFilter(obj, event)


class CaptureTool(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(CaptureTool, self).__init__(parent)
        self.setWindowTitle("截图工具")

        # 状态变量
        self.selection_rect = QtCore.QRect(0, 0, 512, 512)  # 默认 512x512
        self.drag_start_pos = QtCore.QPoint()
        self.resize_start_pos = QtCore.QPoint()
        self.resize_start_rect = QtCore.QRect()
        self.is_dragging = False
        self.is_resizing = False
        self.is_locked = False
        self._toolbar_is_dragging = False
        self._window_offset = QtCore.QPoint(0, 0)

        self.edge_margin = 16
        self.save_path = self._get_default_save_path()
        self.resize_left = self.resize_right = self.resize_top = self.resize_bottom = False

        # 录屏相关
        self.record_timer = None
        self.frame_counter = 0
        self.record_temp_dir = ""
        self.is_recording = False

        # 独立工具栏（作为子控件浮动在 CaptureTool 上方）
        self.toolbar = ToolBar(self)

        # 先初始化UI（创建定时器），再连接信号
        self._init_ui()
        self._init_connections()

        self._set_initial_rect()       # 居中设置选区
        self._update_resolution_input()

        # 启动工具栏位置更新定时器
        self.pos_update_timer = QtCore.QTimer(self)
        self.pos_update_timer.setInterval(50)
        self.pos_update_timer.timeout.connect(self._update_toolbar_position)
        self.pos_update_timer.start()

        # 显示窗口
        self.show()
        self.raise_()
        self.toolbar.show()
        self.toolbar.raise_()
        self._fix_window_mouse()

    def _get_default_save_path(self):
        return _get_pictures_folder()

    def _on_select_path(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择保存路径", self.save_path)
        if path:
            self.save_path = path

    def _init_ui(self):
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Window
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        screens = QtWidgets.QApplication.screens()
        if screens:
            union_rect = screens[0].geometry()
            for screen in screens[1:]:
                union_rect = union_rect.united(screen.geometry())
            self.setGeometry(union_rect)
            self._window_offset = union_rect.topLeft()
        else:
            self.setGeometry(0, 0, 1920, 1080)
            self._window_offset = QtCore.QPoint(0, 0)

        # 创建录屏定时器
        self.record_timer = QtCore.QTimer(self)
        self.record_timer.setInterval(33)

    def _fix_window_mouse(self):
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x20
            current = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if current & WS_EX_TRANSPARENT:
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current & ~WS_EX_TRANSPARENT)
        except Exception:
            pass

    def _init_connections(self):
        self.toolbar.width_input.editingFinished.connect(self._on_resolution_input)
        self.toolbar.height_input.editingFinished.connect(self._on_resolution_input)

        self.toolbar.lock_btn.clicked.connect(self._on_lock)
        self.toolbar.screenshot_btn.clicked.connect(self._on_screenshot)
        self.toolbar.record_btn.clicked.connect(self._on_record)
        self.toolbar.close_btn.clicked.connect(self._on_close)
        self.toolbar.path_btn.clicked.connect(self._on_select_path)
        self.toolbar.drag_delta.connect(self._on_toolbar_drag)
        self.toolbar.dragging_changed.connect(self._on_toolbar_dragging_changed)

        self.record_timer.timeout.connect(self._record_frame)

    def _set_initial_rect(self):
        """将选区初始化为 512x512 并居中在主屏幕"""
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            screen_rect = screen.geometry()
            w = 512
            h = 512
            x = screen_rect.center().x() - w // 2
            y = screen_rect.center().y() - h // 2
            self.selection_rect = QtCore.QRect(x, y, w, h)
        else:
            screen_rect = self.geometry()
            w = 512
            h = 512
            x = (screen_rect.width() - w) // 2
            y = (screen_rect.height() - h) // 2
            self.selection_rect = QtCore.QRect(x, y, w, h)

    def _update_resolution_input(self):
        self.toolbar.width_input.setText(str(self.selection_rect.width()))
        self.toolbar.height_input.setText(str(self.selection_rect.height()))

    def _apply_resolution(self, width, height):
        center = self.selection_rect.center()
        new_rect = QtCore.QRect(center.x() - width//2, center.y() - height//2, width, height)
        new_rect = self._constrain_rect(new_rect)
        self.selection_rect = new_rect
        self.update()

    def _constrain_rect(self, rect):
        g = self._screen_rect()
        r = rect.translated(self._window_offset).normalized()
        if r.width() > g.width():
            r.setWidth(g.width())
        if r.height() > g.height():
            r.setHeight(g.height())
        if r.width() < 50:
            r.setWidth(50)
        if r.height() < 50:
            r.setHeight(50)
        if r.left() < g.left():
            r.moveLeft(g.left())
        if r.top() < g.top():
            r.moveTop(g.top())
        if r.right() > g.right():
            r.moveRight(g.right())
        if r.bottom() > g.bottom():
            r.moveBottom(g.bottom())
        return r.translated(-self._window_offset)

    def _is_on_resize_edge(self, pos):
        self.resize_left = self.resize_right = self.resize_top = self.resize_bottom = False
        edge_rect = self.selection_rect.adjusted(-self.edge_margin, -self.edge_margin,
                                                  self.edge_margin, self.edge_margin)
        if not edge_rect.contains(pos):
            return False

        left_diff = abs(pos.x() - self.selection_rect.left())
        right_diff = abs(pos.x() - self.selection_rect.right())
        top_diff = abs(pos.y() - self.selection_rect.top())
        bottom_diff = abs(pos.y() - self.selection_rect.bottom())

        self.resize_left = left_diff <= self.edge_margin
        self.resize_right = right_diff <= self.edge_margin
        self.resize_top = top_diff <= self.edge_margin
        self.resize_bottom = bottom_diff <= self.edge_margin
        return self.resize_left or self.resize_right or self.resize_top or self.resize_bottom

    def _capture_selection(self, file_path):
        capture_global = QtCore.QRect(
            self.mapToGlobal(self.selection_rect.topLeft()),
            self.selection_rect.size()
        )
        self.hide()
        QtWidgets.QApplication.processEvents()
        img = self._grab_screen_region(capture_global)
        self.show()
        if img.isNull():
            return False
        if self.toolbar.force_res_cb.isChecked():
            try:
                target_w = int(self.toolbar.width_input.text())
                target_h = int(self.toolbar.height_input.text())
                if target_w > 0 and target_h > 0:
                    img = img.scaled(target_w, target_h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
            except ValueError:
                pass
        img.save(file_path, "PNG")
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setPixmap(img)
        return True

    def _start_recording(self):
        if self.is_recording:
            return
        time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.record_temp_dir = os.path.join(tempfile.gettempdir(), f"MayaScreenRecord_{time_str}")
        try:
            os.makedirs(self.record_temp_dir, exist_ok=True)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"无法创建临时录屏目录:\n{e}")
            return
        self.frame_counter = 0
        self.is_recording = True
        fps = int(self.toolbar.fps_label.text().split()[0])
        self.record_timer.setInterval(int(1000 / fps))
        self.record_timer.start()
        self.toolbar.record_btn.setText("⏹️ 停止录屏")
        self.toolbar.record_btn.setStyleSheet("QPushButton { background-color: #d0d0d0; color: #000000; }")
        self.update()

    def _stop_recording(self):
        if not self.is_recording:
            return
        self.record_timer.stop()
        self.is_recording = False
        self.toolbar.record_btn.setText("🎥 录屏")
        self.toolbar.record_btn.setStyleSheet("")
        self.update()

        if self.frame_counter == 0:
            try:
                os.rmdir(self.record_temp_dir)
            except:
                pass
            return

        time_str = os.path.basename(self.record_temp_dir).replace("MayaScreenRecord_", "")
        file_name = f"Recording_{time_str}.gif"
        file_path = os.path.join(self.save_path, file_name)

        try:
            frames = []
            for i in range(self.frame_counter):
                frame_path = os.path.join(self.record_temp_dir, f"frame_{i:06d}.png")
                frames.append(Image.open(frame_path).convert("P", palette=Image.Palette.ADAPTIVE))

            duration = int(self.record_timer.interval() * 3)
            frames[0].save(
                file_path,
                save_all=True,
                append_images=frames[1:],
                duration=duration,
                loop=0,
                optimize=True
            )
            for f in frames:
                f.close()
        except Exception as e:
            pass

        try:
            for f in os.listdir(self.record_temp_dir):
                os.remove(os.path.join(self.record_temp_dir, f))
            os.rmdir(self.record_temp_dir)
        except:
            pass

    def _record_frame(self):
        if not self.is_recording:
            return
        frame_global = QtCore.QRect(
            self.mapToGlobal(self.selection_rect.topLeft()),
            self.selection_rect.size()
        )
        frame = self._grab_screen_region(frame_global)
        if frame.isNull():
            return
        file_name = os.path.join(self.record_temp_dir, f"frame_{self.frame_counter:06d}.png")
        frame.save(file_name, "PNG")
        self.frame_counter += 1

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        # 蓝色虚线框
        pen = QtGui.QPen(QtGui.QColor(0, 120, 215, 230))
        pen.setWidth(4)
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(self.selection_rect)

        # 调整把手（未锁定时）
        if not self.is_locked:
            handle_size = 10
            handle_color = QtGui.QColor(0, 120, 215, 200)
            painter.setBrush(handle_color)
            painter.setPen(QtCore.Qt.NoPen)
            tl = self.selection_rect.topLeft()
            tr = self.selection_rect.topRight()
            bl = self.selection_rect.bottomLeft()
            br = self.selection_rect.bottomRight()
            painter.drawEllipse(tl, handle_size//2, handle_size//2)
            painter.drawEllipse(tr, handle_size//2, handle_size//2)
            painter.drawEllipse(bl, handle_size//2, handle_size//2)
            painter.drawEllipse(br, handle_size//2, handle_size//2)

        # 录制指示器
        if self.is_recording:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 50, 50, 200))
            painter.drawEllipse(self.selection_rect.topLeft() + QtCore.QPoint(10, 10), 8, 8)
            painter.setPen(QtGui.QColor(255, 255, 255))
            painter.setFont(QtGui.QFont("Segoe UI", 10))
            painter.drawText(self.selection_rect.topLeft() + QtCore.QPoint(25, 15), "REC")

    def mousePressEvent(self, event):
        if self.is_locked:
            return
        pos = event.pos()
        if self._is_on_resize_edge(pos):
            self.is_resizing = True
            self.resize_start_pos = QtGui.QCursor.pos()
            self.resize_start_rect = QtCore.QRect(self.selection_rect)
        elif self.selection_rect.contains(pos):
            self.is_dragging = True
            self.drag_start_pos = QtGui.QCursor.pos()
        else:
            super(CaptureTool, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if not self.is_locked and not self.is_dragging and not self.is_resizing:
            if self._is_on_resize_edge(pos):
                if (self.resize_left or self.resize_right) and (self.resize_top or self.resize_bottom):
                    self.setCursor(QtCore.Qt.SizeFDiagCursor)
                elif self.resize_left or self.resize_right:
                    self.setCursor(QtCore.Qt.SizeHorCursor)
                elif self.resize_top or self.resize_bottom:
                    self.setCursor(QtCore.Qt.SizeVerCursor)
                else:
                    self.setCursor(QtCore.Qt.ArrowCursor)
            elif self.selection_rect.contains(pos):
                self.setCursor(QtCore.Qt.SizeAllCursor)
            else:
                self.setCursor(QtCore.Qt.ArrowCursor)
            return

        cursor_pos = QtGui.QCursor.pos()
        if self.is_dragging:
            alt_held = bool(event.modifiers() & QtCore.Qt.AltModifier)
            if alt_held:
                center = self.selection_rect.center()
                dy = cursor_pos.y() - self.drag_start_pos.y()
                size = max(50, abs(dy) * 2)
                new_rect = QtCore.QRect(center.x() - size//2, center.y() - size//2, size, size)
                new_rect = self._constrain_rect(new_rect)
                self.selection_rect = new_rect
                self.drag_start_pos = cursor_pos
                self.update()
            else:
                delta = cursor_pos - self.drag_start_pos
                new_rect = self.selection_rect.translated(delta)
                new_rect = self._constrain_rect(new_rect)
                self.selection_rect = new_rect
                self.drag_start_pos = cursor_pos
                self.update()
        elif self.is_resizing:
            alt_held = bool(event.modifiers() & QtCore.Qt.AltModifier)
            ctrl_held = bool(event.modifiers() & QtCore.Qt.ControlModifier)
            new_rect = QtCore.QRect(self.resize_start_rect)
            dx = cursor_pos.x() - self.resize_start_pos.x()
            dy = cursor_pos.y() - self.resize_start_pos.y()
            if self.resize_left:
                new_rect.setLeft(self.resize_start_rect.left() + dx)
            if self.resize_right:
                new_rect.setRight(self.resize_start_rect.right() + dx)
            if self.resize_top:
                new_rect.setTop(self.resize_start_rect.top() + dy)
            if self.resize_bottom:
                new_rect.setBottom(self.resize_start_rect.bottom() + dy)
            if alt_held:
                size = max(new_rect.width(), new_rect.height())
                new_rect.setWidth(size)
                new_rect.setHeight(size)
            elif ctrl_held:
                aspect = self.resize_start_rect.width() / self.resize_start_rect.height()
                if (self.resize_left or self.resize_right) and not (self.resize_top or self.resize_bottom):
                    new_h = int(new_rect.width() / aspect)
                    new_rect.setHeight(new_h)
                elif (self.resize_top or self.resize_bottom) and not (self.resize_left or self.resize_right):
                    new_w = int(new_rect.height() * aspect)
                    new_rect.setWidth(new_w)
                elif abs(dx) >= abs(dy):
                    new_h = int(new_rect.width() / aspect)
                    if self.resize_top:
                        new_rect.setTop(new_rect.bottom() - new_h)
                    new_rect.setHeight(new_h)
                else:
                    new_w = int(new_rect.height() * aspect)
                    if self.resize_left:
                        new_rect.setLeft(new_rect.right() - new_w)
                    new_rect.setWidth(new_w)
            if new_rect.width() < 50:
                new_rect.setWidth(50)
            if new_rect.height() < 50:
                new_rect.setHeight(50)
            new_rect = self._constrain_rect(new_rect)
            self.selection_rect = new_rect
            self.update()

    def mouseReleaseEvent(self, event):
        self.is_dragging = False
        self.is_resizing = False

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self._on_close()
        elif event.key() == QtCore.Qt.Key_S and event.modifiers() == QtCore.Qt.ControlModifier:
            self._on_screenshot()
        else:
            super(CaptureTool, self).keyPressEvent(event)

    def _screen_rect(self):
        rect = QtCore.QRect()
        for screen in QtWidgets.QApplication.screens():
            rect = rect.united(screen.geometry())
        return rect

    def _grab_screen_region(self, global_rect):
        """截取屏幕区域（支持多屏）"""
        center = global_rect.center()
        target_screen = QtWidgets.QApplication.screenAt(center)
        if not target_screen:
            target_screen = QtWidgets.QApplication.primaryScreen()

        screen_geo = target_screen.geometry()
        # 转换到屏幕本地坐标
        local_x = global_rect.x() - screen_geo.x()
        local_y = global_rect.y() - screen_geo.y()
        local_w = global_rect.width()
        local_h = global_rect.height()

        # 直接用 grabWindow 指定区域（支持 DPR 缩放）
        pixmap = target_screen.grabWindow(
            0, local_x, local_y, local_w, local_h
        )
        return pixmap

    def _update_toolbar_position(self):
        """将工具栏放置在选区矩形下方中央"""
        if not self.toolbar or self._toolbar_is_dragging:
            return
        center_global = self.mapToGlobal(self.selection_rect.center())
        toolbar_width = self.toolbar.width()
        x = center_global.x() - toolbar_width // 2
        y = self.mapToGlobal(self.selection_rect.bottomLeft()).y() + 10
        screen_rect = self._screen_rect()
        if y + self.toolbar.height() > screen_rect.bottom():
            y = self.mapToGlobal(self.selection_rect.topLeft()).y() - self.toolbar.height() - 10
        if x < screen_rect.left():
            x = screen_rect.left() + 5
        if x + toolbar_width > screen_rect.right():
            x = screen_rect.right() - toolbar_width - 5
        # 将全局坐标转换为 CaptureTool 相对坐标
        parent_pos = self.mapToGlobal(QtCore.QPoint(0, 0))
        self.toolbar.move(x - parent_pos.x(), y - parent_pos.y())
        self.toolbar.raise_()

    def closeEvent(self, event):
        self.pos_update_timer.stop()
        self.toolbar.close()
        super(CaptureTool, self).closeEvent(event)

    # 按钮槽函数
    def _on_resolution_input(self):
        if self.is_locked:
            return
        try:
            w = int(self.toolbar.width_input.text())
            h = int(self.toolbar.height_input.text())
            if w <= 0 or h <= 0:
                self._update_resolution_input()
        except ValueError:
            self._update_resolution_input()

    def _on_lock(self):
        self.is_locked = not self.is_locked
        icon = "\U0001F512" if self.is_locked else "\U0001F513"
        self.toolbar.lock_btn.setText(icon)
        if self.is_locked:
            self.toolbar.lock_btn.setStyleSheet("""
                QPushButton {
                    background-color: #d0d0d0;
                    color: #000000;
                    border: 1px solid #cccccc;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 0px;
                }
                QPushButton:hover { background-color: #e0e0e0; }
            """)
        else:
            self.toolbar.lock_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f0f0f0;
                    color: #000000;
                    border: 1px solid #cccccc;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 0px;
                }
                QPushButton:hover { background-color: #e0e0e0; }
            """)
        self.update()

    def _on_screenshot(self):
        file_name = f"Screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        file_path = os.path.join(self.save_path, file_name)
        if self._capture_selection(file_path):
            clipboard = QtWidgets.QApplication.clipboard()
            img = QtGui.QPixmap(file_path)
            clipboard.setPixmap(img)

    def _on_record(self):
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _on_close(self):
        if self.is_recording:
            reply = QtWidgets.QMessageBox.question(
                self, "确认退出",
                "录屏正在进行中，退出将丢失录屏内容。确定退出吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
            self.record_timer.stop()
            self.is_recording = False
        if self.record_temp_dir and os.path.exists(self.record_temp_dir):
            try:
                for f in os.listdir(self.record_temp_dir):
                    os.remove(os.path.join(self.record_temp_dir, f))
                os.rmdir(self.record_temp_dir)
            except:
                pass
        self.close()

    def _on_toolbar_dragging_changed(self, is_dragging):
        self._toolbar_is_dragging = is_dragging

    def _on_toolbar_drag(self, dx, dy):
        new_rect = self.selection_rect.translated(dx, dy)
        new_rect = self._constrain_rect(new_rect)
        self.selection_rect = new_rect
        center_global = self.mapToGlobal(new_rect.center())
        toolbar_x = center_global.x() - self.toolbar.width() // 2
        toolbar_y = self.mapToGlobal(new_rect.bottomLeft()).y() + 10
        screen_rect = self._screen_rect()
        if toolbar_y + self.toolbar.height() > screen_rect.bottom():
            toolbar_y = self.mapToGlobal(new_rect.topLeft()).y() - self.toolbar.height() - 10
        if toolbar_x < screen_rect.left():
            toolbar_x = screen_rect.left() + 5
        if toolbar_x + self.toolbar.width() > screen_rect.right():
            toolbar_x = screen_rect.right() - self.toolbar.width() - 5
        self.toolbar.move(toolbar_x, toolbar_y)
        self.update()


def _get_pictures_folder():
    try:
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0027, None, 0, buf)
        if buf.value and os.path.exists(buf.value):
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Pictures")


def show_capture_tool():
    """在 Maya 中显示截屏工具窗口"""
    global _capture_tool_instance
    # 彻底销毁旧实例
    try:
        if _capture_tool_instance is not None:
            _capture_tool_instance.close()
            _capture_tool_instance.deleteLater()
            _capture_tool_instance = None
    except Exception:
        _capture_tool_instance = None
    _capture_tool_instance = CaptureTool()
    _capture_tool_instance.show()


if __name__ == "__main__":
    show_capture_tool()