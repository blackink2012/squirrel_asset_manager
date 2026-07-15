# -*- coding: utf-8 -*-
"""状态行快捷按钮 — 在 Maya 状态行末尾添加一个启动松鼠资产管理器的图标按钮。"""

import os
import maya.mel as mel

# 按钮引用（防止被 GC 回收）
_status_line_button = None


def _get_icon_path():
    """获取图标路径"""
    module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(module_dir, "Assets", "icon", "squirrel_asset_iconC.png")
    if os.path.exists(icon_path):
        return icon_path
    return None


def _get_command_code():
    return '''
from maya import cmds

try:
    from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow
    MaterialLibraryWindow.show_window()
except Exception as e:
    cmds.warning(f"松鼠资产管理器启动失败: {str(e)}")
'''


def add_status_line_button():
    """在 Maya 状态行添加一个图标按钮（适合在 userSetup.py 中通过 evalDeferred 调用）。"""
    global _status_line_button

    try:
        import maya.OpenMayaUI as omui

        # 兼容 PySide2 / PySide6
        try:
            import shiboken6
            wrap_instance = shiboken6.wrapInstance
        except ImportError:
            try:
                import shiboken2
                wrap_instance = shiboken2.wrapInstance
            except ImportError:
                print("[松鼠资产管理器] 无法导入 shiboken，跳过状态行按钮")
                return

        try:
            from PySide6 import QtWidgets, QtGui, QtCore
        except ImportError:
            try:
                from PySide2 import QtWidgets, QtGui, QtCore
            except ImportError:
                print("[松鼠资产管理器] 无法导入 PySide，跳过状态行按钮")
                return

        status_line_name = mel.eval('string $tempStr = $gStatusLine')
        status_line_ptr = omui.MQtUtil.findControl(status_line_name)

        if not status_line_ptr:
            print("[松鼠资产管理器] 无法找到状态行")
            return

        status_line_widget = wrap_instance(int(status_line_ptr), QtWidgets.QWidget)

        command_code = _get_command_code()
        icon_path = _get_icon_path()

        def _create_button():
            global _status_line_button

            # 查找状态行中已有的工具按钮，使用相同的图标大小（跟随 Maya 自身 DPI/显示设置）
            icon_sz = 24
            for child in status_line_widget.findChildren(QtWidgets.QToolButton):
                es = child.iconSize()
                if es.width() > 0 and es.height() > 0:
                    icon_sz = es.width()
                    break

            button = QtWidgets.QToolButton()
            button.setAutoRaise(True)
            button.setToolTip("松鼠资产管理器")
            button.setIconSize(QtCore.QSize(icon_sz, icon_sz))
            button.setFixedSize(icon_sz, icon_sz)
            button.setStyleSheet("QToolButton{margin:0;padding:0;border:none;background:transparent;}")

            if icon_path and os.path.exists(icon_path):
                button.setIcon(QtGui.QIcon(icon_path))
            else:
                button.setText("SQ")

            def _on_click(_checked=False):
                exec(command_code, {"__name__": "__main__"})

            button.clicked.connect(_on_click)

            layout = status_line_widget.layout()
            if layout:
                layout.addWidget(button)
                _status_line_button = button
                print("[松鼠资产管理器] 状态行按钮已添加")

        # 延迟 500ms 确保 Maya 状态行布局完成
        QtCore.QTimer.singleShot(500, _create_button)

    except Exception as e:
        print(f"[松鼠资产管理器] 添加状态行按钮失败: {e}")
