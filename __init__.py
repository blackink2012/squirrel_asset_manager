# Maya Material Library Pro
# UI-First 材质库管理工具

"""
MaterialManagementPro — Maya 材质/资产管理插件

模块级变量（可在 Maya 控制台直接访问）：
    manager:         分类库 (Category Library) 的 MaterialManager 实例
    project_manager: 工程库 (Project Library) 的 MaterialManager 实例

用法（Maya 控制台）：
    from squirrel_asset_manager import mgr
    mgr.get_materials()

    from squirrel_asset_manager import pmgr
    pmgr.get_materials()
"""

import sys as _sys
import importlib.machinery as _machinery


def _install_py_before_pyd():
    """让 .py 优先于 .pyd 加载（本机调试：py+pyd 共存时优先 py）

    Python 默认 import 顺序是 .pyd → .py → .pyc。本钩子把 .py 提到 .pyd 前，
    实现「同名 py 存在时优先用 py（方便调试），没有 py 时回退到 pyd」。
    - 本机源码目录（py+pyd 共存）→ 优先加载 py
    - 独立库（只有 pyd）→ 无 py 可匹配，自动回退加载 pyd
    """
    try:
        hook = _machinery.FileFinder.path_hook(
            (_machinery.SourceFileLoader, _machinery.SOURCE_SUFFIXES),
            (_machinery.ExtensionFileLoader, _machinery.EXTENSION_SUFFIXES),
            (_machinery.SourcelessFileLoader, _machinery.BYTECODE_SUFFIXES),
        )
        # 移除默认的 FileFinder 钩子（pyd 优先）
        from importlib import _bootstrap_external as _be
        default_hook = _be.FileFinder.path_hook(*_be._get_supported_file_loaders())
        removed = False
        while default_hook in _sys.path_hooks:
            _sys.path_hooks.remove(default_hook)
            removed = True
        if removed:
            # 清空已缓存的目录 importer，否则仍按旧顺序（pyd 优先）加载
            _be._path_importer_cache.clear()
        if hook not in _sys.path_hooks:
            _sys.path_hooks.insert(0, hook)
    except Exception:
        pass


_install_py_before_pyd()

from typing import Optional
from .core.manager import MaterialManager

# ── 全局管理器引用（由 main_window.py 在创建窗口时设置）──
manager: Optional[MaterialManager] = None          # Category 库管理器
project_manager: Optional[MaterialManager] = None   # Project 库管理器


class _ManagerProxy:
    """代理对象，将对 mgr.xxx 的调用委托给实际的 Manager 实例。

    用法：
        from squirrel_asset_manager import mgr
        mgr.get_materials()  # → manager.get_materials(...)
    """

    def __init__(self, name: str):
        self._target_name = name  # "manager" 或 "project_manager"

    @property
    def _target(self) -> Optional[MaterialManager]:
        if self._target_name == "manager":
            return manager
        return project_manager

    def __getattr__(self, name: str):
        inst = self._target
        if inst is None:
            raise RuntimeError(
                "MaterialManager 尚未初始化。请先启动插件窗口。\n"
                "If the plugin window is already open, restart Maya or run:\n"
                "  from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow\n"
                "  MaterialLibraryWindow.show_window()"
            )
        return getattr(inst, name)


# ── 便捷入口（Maya 控制台中最常用的快捷方式）──
mgr = _ManagerProxy("manager")       # → Category 库管理器
pmgr = _ManagerProxy("project_manager")  # → Project 库管理器
