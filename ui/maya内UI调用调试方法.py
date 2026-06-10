# -*- coding: utf-8 -*-
"""一键重载 UI（开发热更新）"""
import sys

def reload_ui():
    for mod in list(sys.modules.keys()):
        if "squirrel_asset_manager" in mod:
            del sys.modules[mod]
    sys.path.insert(0, r"F:\BaiduSyncdisk\WorkBuddy\MaterialManagementPro")
    from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow
    MaterialLibraryWindow.show_window()

if __name__ == "__main__":
    reload_ui()
