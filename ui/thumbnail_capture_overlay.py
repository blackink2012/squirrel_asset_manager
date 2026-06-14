# -*- coding: utf-8 -*-
"""
ThumbnailCaptureOverlay — ZJG_截屏工具的极薄集成层

子类化原版 CaptureTool，覆盖 ONLY 保存路径行为：
  - 模式 A（单次）: 截图 → emit pixmap → close（原有行为）
  - 模式 B（复用）: 截图 → 保存到指定路径 → emit → 窗口不关闭

其余全部继承（ToolBar / 录屏 / 选区 / 键盘 / 锁定 / 缩放 ... 完全不变）

用法:
    # 单次模式（缩略图对话框）
    overlay = ThumbnailCaptureOverlay()
    overlay.captured.connect(on_pixmap)
    overlay.show()

    # 复用模式（资产批量创建）
    overlay = ThumbnailCaptureOverlay(keep_alive=True)
    overlay.save_path_override = "/path/to/thumb.png"
    overlay.captured.connect(on_done)
    overlay.show()
    # 截图后窗口保持打开，手动关闭
"""

import os
import sys
import tempfile

from ..utils.zjg_capture import CaptureTool as _OriginalCaptureTool
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets


class ThumbnailCaptureOverlay(_OriginalCaptureTool):
    """
    ZJG_截屏工具的集成子类。

    截图结果去向：
      - 单次模式（keep_alive=False）: emit QPixmap → 关闭窗口
      - 复用模式（keep_alive=True） : 保存到 save_path_override → emit → 窗口保持打开
    """

    captured = QtCore.Signal(QtGui.QPixmap)
    cancelled = QtCore.Signal()
    recordingFinished = QtCore.Signal(str)  # GIF 文件路径
    skipRequested = QtCore.Signal()         # v2.0: 跳过截图（使用占位图）
    resetRectRequested = QtCore.Signal()    # v2.0: 重置选区坐标

    # 唯一标识符，用于跨插件重载时在 Qt 控件树中定位
    OBJECT_NAME = "MMP_ScreenshotOverlay"

    def __init__(self, keep_alive=False, parent=None):
        """
        Args:
            keep_alive: True 时截图后窗口保持打开（资产批量创建用）
        """
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self._keep_alive = keep_alive
        self.save_path_override = ""  # 复用模式下由外部设置
        self._progress_label = None   # v2.0: 批量进度文本
        self._skip_btn = None         # v2.0: 跳过截图按钮
        self._reset_btn = None        # v2.0: 重置选区按钮

    # ── 跨会话复用 ──────────────────────────────────

    @classmethod
    def find_existing(cls):
        """
        扫描 Maya Qt 控件树，查找已存在的截屏覆盖层实例。

        用于跨插件重载场景：用户关闭插件后 Maya 界面中仍保留的
        overlay 应当被复用，而不是创建一个新的。
        返回 ThumbnailCaptureOverlay 实例或 None。

        两阶段查找策略：
          1. 按 objectName 精确匹配（新建 overlay 有唯一标识符）
          2. 按 Qt 元对象类名匹配（解决插件重载后 Python 类对象改变的问题，
             同时兼容旧版 overlay 未设 objectName 的情况）
        
        注意：只返回可见且状态正常的实例，避免复用已关闭的窗口。
        """
        # 阶段1：objectName 精确匹配
        for w in QtWidgets.QApplication.topLevelWidgets():
            if w.objectName() == cls.OBJECT_NAME:
                if cls._is_window_valid(w):
                    return w

        # 阶段2：Qt 元对象类名匹配 —— 底层 C++ 元对象名不受 Python
        #        模块重载影响（删除 sys.modules 再 reimport 也不会变）
        class_name = cls.__name__
        for w in QtWidgets.QApplication.topLevelWidgets():
            if w.metaObject().className() == class_name:
                if cls._is_window_valid(w):
                    return w

        return None

    @classmethod
    def _is_window_valid(cls, window):
        """
        检查窗口是否有效且可用。
        
        Args:
            window: QWidget 实例
            
        Returns:
            bool: 窗口有效返回 True，否则 False
        """
        if not window:
            return False
        # 检查窗口是否可见
        if not window.isVisible():
            return False
        # 检查工具栏是否存在且可见（CaptureTool 的关键组件）
        if hasattr(window, 'toolbar') and window.toolbar:
            try:
                # 检查工具栏是否已销毁
                window.toolbar.winId()
                return True
            except RuntimeError:
                return False
        return True

    # ── 覆盖: 录屏 ────────────────────────────────────

    def _stop_recording(self):
        """录屏完成 → 保存 GIF → 复制到材质缩略图路径 → 发射信号"""
        if not self.is_recording:
            return
        self.record_timer.stop()
        self.is_recording = False
        self.toolbar.record_btn.setText("🎥 录屏")
        self.toolbar.record_btn.setStyleSheet("")
        self.update()

        if self.frame_counter == 0:
            try: os.rmdir(self.record_temp_dir)
            except: pass
            return

        # 生成 GIF（原版逻辑）
        time_str = os.path.basename(self.record_temp_dir).replace("MayaScreenRecord_", "")
        file_name = f"Recording_{time_str}.aicon"
        file_path = os.path.join(self.save_path, file_name)

        try:
            from PIL import Image
            frames = []
            for i in range(self.frame_counter):
                frame_path = os.path.join(self.record_temp_dir, f"frame_{i:06d}.png")
                frames.append(Image.open(frame_path).convert("P", palette=Image.Palette.ADAPTIVE))
            duration = int(self.record_timer.interval() * 3)
            frames[0].save(file_path, save_all=True, append_images=frames[1:],
                          duration=duration, loop=0, optimize=True, format="GIF")
            for f in frames: f.close()
        except Exception as e:
            print(f"[CaptureOverlay] 生成 GIF 失败: {e}")

        # 清理临时帧
        try:
            for f in os.listdir(self.record_temp_dir):
                os.remove(os.path.join(self.record_temp_dir, f))
            os.rmdir(self.record_temp_dir)
        except: pass

        # 将 GIF 移到缩略图路径，同时清理旧 PNG
        if self.save_path_override:
            gif_dest = os.path.splitext(self.save_path_override)[0] + ".aicon"
            try:
                import shutil
                os.makedirs(os.path.dirname(gif_dest), exist_ok=True)
                # 删除旧的 PNG/SICON 缩略图（保证唯一）
                old_png = self.save_path_override  # save_path_override 是以 .sicon 结尾的路径
                if old_png and os.path.isfile(old_png):
                    os.remove(old_png)
                    print(f"[CaptureOverlay] 已删除旧缩略图: {old_png}")
                shutil.move(file_path, gif_dest)
                print(f"[CaptureOverlay] GIF 缩略图已保存: {gif_dest}")
                self.recordingFinished.emit(gif_dest)
            except Exception as e:
                print(f"[CaptureOverlay] 移动 GIF 失败: {e}")

    # ── 覆盖: 截图 ────────────────────────────────────

    def _on_screenshot(self):
        """截取选区 → 保存/emit（复用父类多屏兼容的 _grab_screen_region）"""
        try:
            self.hide()
            QtWidgets.QApplication.processEvents()

            global_rect = QtCore.QRect(
                self.mapToGlobal(self.selection_rect.topLeft()),
                self.selection_rect.size()
            )
            img = self._grab_screen_region(global_rect)

            if img.isNull():
                self.show()
                self.cancelled.emit()
                self._maybe_close()
                return

            # 强制分辨率（与原版完全一致）
            if self.toolbar.force_res_cb.isChecked():
                try:
                    target_w = int(self.toolbar.width_input.text())
                    target_h = int(self.toolbar.height_input.text())
                    if target_w > 0 and target_h > 0:
                        img = img.scaled(target_w, target_h,
                                         QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                         QtCore.Qt.TransformationMode.SmoothTransformation)
                except ValueError:
                    pass

            # 复制到剪贴板
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setPixmap(img)

            # 复用模式：保存到指定路径
            if self._keep_alive and self.save_path_override:
                try:
                    parent_dir = os.path.dirname(self.save_path_override)
                    if parent_dir:
                        os.makedirs(parent_dir, exist_ok=True)
                    img.save(self.save_path_override, "PNG")
                    print(f"[CaptureOverlay] 缩略图已保存: {self.save_path_override}")
                except Exception as e:
                    print(f"[CaptureOverlay] 保存缩略图失败: {e}")

            self.show()
            self.captured.emit(img)

            if not self._keep_alive:
                self._cleanup_and_close()
        except RuntimeError as e:
            # 防御性保护：如果 C++ 对象在截图过程中已销毁，避免 Maya 崩溃
            print(f"[CaptureOverlay] 截图过程中 C++ 对象已销毁: {e}")
            try:
                self.show()
            except RuntimeError:
                pass
            try:
                self.cancelled.emit()
            except RuntimeError:
                pass
        except Exception as e:
            print(f"[CaptureOverlay] 截图异常: {e}")
            try:
                self.show()
                self.cancelled.emit()
            except RuntimeError:
                pass

    # ── 覆盖: 关闭 ────────────────────────────────────

    def _on_close(self):
        """关闭 → 发射 cancelled → 清理"""
        if self.is_recording:
            self.record_timer.stop()
            self.is_recording = False
        self.cancelled.emit()
        self._cleanup_and_close()

    # ── v2.0: 批量模式增强 ────────────────────────────
    # 注: skipRequested / resetRectRequested 信号由 main_window 侧处理
    #      不在父类 toolbar 中注入按钮以避免破坏 ZJG 截屏工具原有 UI

    # ── 内部 ──────────────────────────────────────────

    def _maybe_close(self):
        """非复用模式下才关闭"""
        if not self._keep_alive:
            self._cleanup_and_close()

    def _cleanup_and_close(self):
        """清理录屏临时文件并关闭"""
        if self.record_temp_dir and os.path.exists(self.record_temp_dir):
            try:
                for f in os.listdir(self.record_temp_dir):
                    os.remove(os.path.join(self.record_temp_dir, f))
                os.rmdir(self.record_temp_dir)
            except Exception:
                pass
        self.close()
