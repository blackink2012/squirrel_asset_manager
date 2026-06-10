# -*- coding: utf-8 -*-
"""代理格式可扩展注册表。

定义 ProxyFormatEntry 数据类和 ProxyFormatRegistry 注册表，
管理 Arnold / V-Ray / Redshift 等第三方渲染器代理导出格式的元数据。

注册表遵循「可扩展预留」原则：
- enabled=True   → 当前版本支持，UI 中可见可勾选
- enabled=False  → 预留格式，UI 中隐藏，待后续版本激活

依赖:
    squirrel_asset_manager.utils.maya_plugin_checker.MayaPluginChecker
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProxyFormatEntry:
    """代理格式条目数据类。

    Attributes:
        key: 格式唯一标识符，如 "arnold"、"vray"、"redshift"
        name: 人类可读的格式名称，如 "Arnold .ass"
        extension: 文件扩展名（含点），如 ".ass"
        plugin: Maya 插件名，如 "mtoa"
        exporter: 导出器标识符，对应 ExportOrchestrator 中的方法名
        enabled: True=当前可用（UI 展示），False=预留（UI 隐藏）
    """

    key: str = ""
    name: str = ""
    extension: str = ""
    plugin: str = ""
    exporter: str = ""
    enabled: bool = False


# ── 全局代理格式注册表 ──────────────────────────────────────────
# 新增格式只需在此字典中添加条目即可：
#   1. ProxyFormatRegistry 自动感知
#   2. MayaPluginChecker 自动检测新格式的插件状态
#   3. UI 代理格式区域自动渲染新行
#
# 启用/禁用控制：
#   enabled=True  → UI 显示该格式的 checkbox + 插件状态指示器
#   enabled=False → UI 隐藏该行（预留，待后续版本激活）

PROXY_REGISTRY: Dict[str, ProxyFormatEntry] = {
    "arnold": ProxyFormatEntry(
        key="arnold",
        name="Arnold .ass",
        extension=".ass",
        plugin="mtoa",
        exporter="export_arnold_ass",
        enabled=True,
    ),
    "vray": ProxyFormatEntry(
        key="vray",
        name="V-Ray .vrscene",
        extension=".vrscene",
        plugin="vrayformaya",
        exporter="export_vray_vrscene",
        enabled=True,
    ),
    "vrmesh": ProxyFormatEntry(
        key="vrmesh",
        name="V-Ray .vrmesh",
        extension=".vrmesh",
        plugin="vrayformaya",
        exporter="export_vray_vrmesh",
        enabled=True,
    ),
    "redshift": ProxyFormatEntry(
        key="redshift",
        name="Redshift .rs",
        extension=".rs",
        plugin="redshift4maya",
        exporter="export_redshift_proxy",
        enabled=True,
    ),
}


class ProxyFormatRegistry:
    """代理格式注册表 — 静态工具类。

    提供对 PROXY_REGISTRY 全局字典的只读访问，并委托 MayaPluginChecker
    进行插件状态检测。

    用法::

        # 获取所有启用的格式
        enabled_formats = ProxyFormatRegistry.get_enabled()

        # 按 key 查询
        entry = ProxyFormatRegistry.get("arnold")

        # 检查指定格式的插件状态
        status = ProxyFormatRegistry.check_plugin("arnold")
    """

    @staticmethod
    def get_enabled() -> List[ProxyFormatEntry]:
        """返回所有 enabled=True 的代理格式条目。

        Returns:
            List[ProxyFormatEntry]: 启用的格式条目列表（按注册顺序）
        """
        return [entry for entry in PROXY_REGISTRY.values() if entry.enabled]

    @staticmethod
    def get(key: str) -> Optional[ProxyFormatEntry]:
        """按 key 获取代理格式条目。

        Args:
            key: 格式唯一标识符，如 "arnold"

        Returns:
            ProxyFormatEntry 或 None（key 不存在时）
        """
        if not key or not isinstance(key, str):
            return None
        return PROXY_REGISTRY.get(key)

    @staticmethod
    def check_plugin(key: str):
        """检测指定格式对应的 Maya 插件状态。

        委托给 MayaPluginChecker.check_plugin() 实现。

        Args:
            key: 格式唯一标识符，如 "arnold"

        Returns:
            PluginStatus 枚举值：
                - LOADED:     插件已加载，🟢
                - NOT_LOADED: 未加载但可尝试，🟡
                - UNAVAILABLE: 不可用 / key 不存在 / 非 Maya 环境，🔴
        """
        from squirrel_asset_manager.utils.maya_plugin_checker import (
            MayaPluginChecker,
            PluginStatus,
        )

        entry = PROXY_REGISTRY.get(key)
        if entry is None:
            return PluginStatus.UNAVAILABLE
        return MayaPluginChecker.check_plugin(entry.plugin)
