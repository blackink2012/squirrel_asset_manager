# -*- coding: utf-8 -*-
"""
ExportConnector — 资产导出引擎封装 v2.0

封装资产导出流程，v1.x 的 export_material() 保留兼容，
内部委托给 ExportOrchestrator 统一编排。

v2.0 新增:
  - export_asset(ExportConfig) → ExportResult    完整资产导出
  - export_material(...) 保留兼容，内部转调 ExportOrchestrator
"""

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squirrel_asset_manager.core.export_orchestrator import ExportConfig, ExportResult

_IN_MAYA = False
try:
    import maya.cmds as cmds
    _IN_MAYA = True
except ImportError:
    pass


class ExportConnector:
    """资产导出引擎封装层（v2.0 — 委托 ExportOrchestrator）"""

    # ── v2.0 API ──────────────────────────────────────────────

    @staticmethod
    def export_asset(config, skip_thumbnail=False):
        from squirrel_asset_manager.core.export_orchestrator import ExportOrchestrator
        orch = ExportOrchestrator(config.target_dir)
        return orch.export_single(config, skip_thumbnail=skip_thumbnail)

    # ── v1.x 兼容 API ────────────────────────────────────────

    @staticmethod
    def export_material(material_node, target_dir, meta):
        """
        导出单个 Maya 材质为资产预设（v1.x 兼容接口）。

        v2.0: 内部构造 ExportConfig 并委托 ExportOrchestrator.export_single()。

        Args:
            material_node: Maya 材质节点名（如 "standardSurface2"）
            target_dir: 目标分类文件夹
            meta: dict {"name", "name_cn", "category", "tags"}

        Returns:
            dict {"success": bool, "files": list, "error": str}
        """
        from squirrel_asset_manager.core.export_orchestrator import (
            ExportConfig,
            ExportOrchestrator,
        )

        name = meta.get("name", material_node) if isinstance(meta, dict) else material_node
        name_cn = meta.get("name_cn", "") if isinstance(meta, dict) else ""
        category = meta.get("category", "") if isinstance(meta, dict) else ""
        tags = meta.get("tags", []) if isinstance(meta, dict) else []

        config = ExportConfig(
            asset_name=name,
            name_cn=name_cn,
            category=category,
            tags=list(tags),
            target_dir=target_dir,
            material_node=material_node,
            export_zmetal=True,
        )

        orch = ExportOrchestrator(target_dir)
        result = orch.export_single(config)

        return {
            "success": result.success,
            "files": result.files,
            "error": result.error,
        }
