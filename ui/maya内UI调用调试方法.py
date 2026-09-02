# -*- coding: utf-8 -*-
"""一键重载 UI（开发热更新）"""
import os
import sys


def _project_root():
    """项目根目录：脚本文件方式运行按自身位置推导；脚本编辑器运行用已知路径"""
    try:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except NameError:
        return r"E:\TRAE_projects\MaterialManagementPro"


def reload_ui():
    for mod in list(sys.modules.keys()):
        if "squirrel_asset_manager" in mod:
            del sys.modules[mod]
    root = _project_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow
    MaterialLibraryWindow.show_window()

if __name__ == "__main__":
    reload_ui()
