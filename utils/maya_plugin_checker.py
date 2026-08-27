# -*- coding: utf-8 -*-
"""Maya 插件状态检测工具。

提供 PluginStatus 枚举和 MayaPluginChecker 静态工具类，
用于检测 Maya 中第三方渲染器插件的加载状态。

检测逻辑:
    - LOADED:      maya.cmds.pluginInfo(plugin, q=True, loaded=True) 返回 True
    - NOT_LOADED:  插件存在于 Maya 搜索路径但未加载
    - UNAVAILABLE: 插件不存在 / 非 Maya 环境 / 检测异常

在非 Maya 环境中，所有检测方法优雅降级返回 NOT_LOADED 或 UNAVAILABLE，
绝不抛出 ImportError 或运行时异常。

用法::

    from squirrel_asset_manager.utils.maya_plugin_checker import MayaPluginChecker, PluginStatus

    status = MayaPluginChecker.check_plugin("mtoa")
    if status == PluginStatus.LOADED:
        print("Arnold 插件已就绪")

    all_statuses = MayaPluginChecker.get_all_statuses()
    # → {"mtoa": LOADED, "vrayformaya": UNAVAILABLE, "redshift4maya": UNAVAILABLE}
"""

from __future__ import annotations

from enum import Enum
from typing import Dict

# ── Maya 环境检测 ──────────────────────────────────────────────
_IN_MAYA: bool = False
try:
    import maya.cmds as cmds  # noqa: F401

    _IN_MAYA = True
except ImportError:
    pass


class PluginStatus(Enum):
    """Maya 插件状态枚举。

    Values:
        LOADED:      插件已加载，可直接使用 — UI 显示 🟢
        NOT_LOADED:  插件存在但未加载，可尝试加载 — UI 显示 🟡
        UNAVAILABLE: 插件不可用或不存在 — UI 显示 🔴 / 置灰
    """

    LOADED = "loaded"
    NOT_LOADED = "not_loaded"
    UNAVAILABLE = "unavailable"


class MayaPluginChecker:
    """Maya 插件状态检测器 — 纯静态工具类。

    所有方法均为 @staticmethod，无需实例化。
    在非 Maya 环境中导入和使用都不会崩溃。

    检测实现:
        - check_plugin(): 调用 maya.cmds.pluginInfo(plugin, query=True, loaded=True)
        - get_all_statuses(): 遍历 PROXY_REGISTRY 中所有条目
        - is_plugin_loaded(): check_plugin() 的布尔快速通道
    """

    @staticmethod
    def check_plugin(plugin_name: str) -> PluginStatus:
        """检测指定 Maya 插件的加载状态。

        先尝试 pluginInfo 查询 loaded 状态 → LOADED
        再尝试 pluginInfo 查询是否存在 → NOT_LOADED
        都不行 → UNAVAILABLE

        Args:
            plugin_name: Maya 插件名称，如 "mtoa"、"vrayformaya"

        Returns:
            PluginStatus 枚举值
        """
        if not plugin_name or not isinstance(plugin_name, str):
            return PluginStatus.UNAVAILABLE

        if not _IN_MAYA:
            # 非 Maya 环境：优雅降级，不抛异常
            return PluginStatus.NOT_LOADED

        try:
            # Step 1: 检查是否已加载
            is_loaded = cmds.pluginInfo(plugin_name, query=True, loaded=True)
            if is_loaded:
                return PluginStatus.LOADED

            # Step 2: 检查插件是否存在（未加载但可尝试）
            # pluginInfo 查询插件名直接返回表示插件在搜索路径中
            exists = cmds.pluginInfo(plugin_name, query=True, registered=True)
            if exists:
                return PluginStatus.NOT_LOADED

            # 插件未注册 → 不可用
            return PluginStatus.UNAVAILABLE

        except Exception:
            # pluginInfo 对未知插件可能抛出 RuntimeError
            return PluginStatus.UNAVAILABLE

    @staticmethod
    def get_all_statuses() -> Dict[str, PluginStatus]:
        """获取 PROXY_REGISTRY 中所有格式对应插件的状态字典。

        遍历全局代理格式注册表，对每个条目调用 check_plugin()。

        Returns:
            Dict[str, PluginStatus]: 格式 key → 插件状态的映射
                示例: {"mtoa": LOADED, "vrayformaya": UNAVAILABLE, "redshift4maya": UNAVAILABLE}
        """
        from squirrel_asset_manager.core.proxy_registry import PROXY_REGISTRY

        result: Dict[str, PluginStatus] = {}
        for entry in PROXY_REGISTRY.values():
            status = MayaPluginChecker.check_plugin(entry.plugin)
            result[entry.plugin] = status
        return result

    @staticmethod
    def is_plugin_loaded(plugin_name: str) -> bool:
        """快速检查插件是否已加载。

        check_plugin() 的布尔快捷通道，不区分 NOT_LOADED 和 UNAVAILABLE。

        Args:
            plugin_name: Maya 插件名称

        Returns:
            True 表示插件已加载（LOADED），False 表示未就绪
        """
        return MayaPluginChecker.check_plugin(plugin_name) == PluginStatus.LOADED
