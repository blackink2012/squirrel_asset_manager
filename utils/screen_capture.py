# -*- coding: utf-8 -*-
"""
ScreenCapture — 通用屏幕截取工具

从 ZJG_截屏工具.py 提取核心截屏逻辑，纯 Qt 实现，无 Maya 依赖。
在 Maya 环境和独立 Python 环境中均可使用。

用法:
    from squirrel_asset_manager.utils.screen_capture import ScreenCapture

    pix = ScreenCapture.capture_rect(QRect(100, 100, 512, 512))
    ScreenCapture.force_resize(pix, 256, 256)
    ScreenCapture.save_pixmap(pix, "/path/to/thumb.png")
    ScreenCapture.copy_to_clipboard(pix)
"""

import os


class ScreenCapture:
    """屏幕区域截取工具集（全部静态方法）"""

    # ── 核心截屏 ──────────────────────────────────────────

    @staticmethod
    def capture_rect(qrect):
        """
        截取屏幕指定矩形区域。

        Args:
            qrect: QRect 屏幕坐标矩形

        Returns:
            QPixmap，失败返回 null QPixmap
        """
        # 延迟导入避免模块级依赖
        from PySide6 import QtWidgets, QtCore
        try:
            from PySide6 import QtWidgets as _qw
            from PySide6 import QtCore as _qc
        except ImportError:
            from PySide2 import QtWidgets as _qw
            from PySide2 import QtCore as _qc

        screen = _qw.QApplication.primaryScreen()
        if not screen:
            return _qw.QPixmap()

        full = screen.grabWindow(0)
        img = full.copy(qrect)
        return img

    @staticmethod
    def capture_fullscreen():
        """截取整个主屏幕"""
        try:
            from PySide6 import QtWidgets
        except ImportError:
            from PySide2 import QtWidgets

        screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            return QtWidgets.QPixmap()
        return screen.grabWindow(0)

    # ── 缩放 ──────────────────────────────────────────────

    @staticmethod
    def force_resize(pix, w, h):
        """
        缩放 QPixmap 到指定尺寸。

        Args:
            pix: QPixmap 原始图像
            w: 目标宽度
            h: 目标高度

        Returns:
            QPixmap 缩放后图像
        """
        try:
            from PySide6 import QtCore
        except ImportError:
            from PySide2 import QtCore

        if pix.isNull() or w <= 0 or h <= 0:
            return pix
        return pix.scaled(w, h,
                          QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                          QtCore.Qt.TransformationMode.SmoothTransformation)

    # ── 输出 ──────────────────────────────────────────────

    @staticmethod
    def save_pixmap(pix, path, fmt="PNG"):
        """
        保存 QPixmap 到文件，自动创建父目录。

        Args:
            pix: QPixmap
            path: 目标文件路径
            fmt: 图片格式，默认 PNG

        Returns:
            bool: 成功 True
        """
        if pix.isNull():
            return False
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        return pix.save(path, fmt)

    @staticmethod
    def copy_to_clipboard(pix):
        """复制 QPixmap 到系统剪贴板"""
        try:
            from PySide6 import QtWidgets
        except ImportError:
            from PySide2 import QtWidgets

        if not pix.isNull():
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setPixmap(pix)


# ── 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("ScreenCapture 自测:")
    print("  capture_rect / capture_fullscreen 需要 GUI 环境，跳过")
    print("  force_resize / save_pixmap / copy_to_clipboard 方法已定义")
    print("  ✓ 模块导入成功")
