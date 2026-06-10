# -*- coding: utf-8 -*-
"""ExportOrchestrator — 资产导出编排器 v2.0

统一6阶段资产导出管线（staging 目录 → ZassetBuilder.build() → .zasset）：
  Stage 1 — 元数据 meta.json（始终）
  Stage 2 — 贴图收集 textures/（始终）
  Stage 3 — 材质预设 .zmetal / .mcm（按配置）
  Stage 4 — 几何体 .ma/.mb/.fbx/.obj/.usd/.glb（按配置）
  Stage 5 — 缩略图 .sicon（始终 / 可占位）
  Stage 6 — 代理 .ass/.vrscene/.rs（按配置）

设计原则:
  - 所有 maya.cmds 调用包裹 try/except，异常不抛，记录到 ExportResult.error
  - 非 Maya 环境优雅降级返回失败结果
  - 复用现有 zjg_exporter.py 底层函数，不重写导出逻辑
  - 数据类使用 @dataclass 提高类型安全

依赖:
    squirrel_asset_manager.core.proxy_registry
    squirrel_asset_manager.utils.maya_plugin_checker
    squirrel_asset_manager.integration.zjg_exporter（Maya 环境运行时）
"""

from __future__ import annotations

import os
import re
import sys
import uuid
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# ── Maya 环境检测 ──────────────────────────────────────────────
_IN_MAYA: bool = False
try:
    import maya.cmds as cmds  # noqa: F401
    from maya import OpenMaya as om
    _IN_MAYA = True
except ImportError:
    pass


def _set_maya_string_attr(node_attr: str, value: str) -> None:
    """纯 OpenMaya API 设置字符串属性，避免 MEL style type='string'"""
    sel = om.MSelectionList()
    sel.add(node_attr)
    plug = om.MPlug()
    sel.getPlug(0, plug)
    plug.setString(value)


# ═══════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class ExportConfig:
    """单次资产导出的完整配置。

    由 UI 层（AssetCreateDialogV2）构建，传递给 ExportOrchestrator。
    """
    asset_name: str = ""                               # 资产英文名（文件命名用）
    name_cn: str = ""                                  # 中文易读名
    category: str = ""                                 # 分类 ID
    tags: List[str] = field(default_factory=list)      # 标签列表
    asset_type: str = "materials"                      # materials/models/lights/textures/scenes/hdr
    # ── 材质格式 ──
    export_zmetal: bool = True                         # .zmetal 材质预设
    merge_zmetal: bool = False                         # True=全部材质写入一个ZMETAL
    export_mcm: bool = False                           # .mcm 材质→模型映射（多材质自动设True）
    # ── 几何体格式 ──
    export_ma: bool = False
    export_mb: bool = False
    export_fbx: bool = False
    export_obj: bool = False
    export_usd: bool = False
    # ── 缓存格式 ──
    export_abc: bool = False                           # Alembic 缓存
    ani_frame_mode: str = "current"                    # "current" | "timeline" | "keyframe"
    export_material_only: bool = False    # True=仅导出材质，跳过几何体/代理，截图取当前视口
    export_textures: bool = True         # True=收集并打包贴图，False=跳过贴图
    # ── 代理格式 ──
    proxy_formats: List[str] = field(default_factory=list)  # ["arnold", "vray"]
    # ── 批量/截图 ──
    export_mode: str = "single"  # "single" | "batch_auto" | "batch_semi"
    target_dir: str = ""                               # 导出目标目录
    skip_thumbnail: bool = False
    thumb_source: str = "screenshot"  # "screenshot" | "playblast" | "render"
    screenshot_rect: Optional[Tuple[float, float, float, float]] = None  # (rx,ry,rw,rh) 归一化0~1
    # ── Maya 上下文 ──
    material_node: Optional[str] = None                # Maya 材质节点名
    associated_objects: List[str] = field(default_factory=list)  # 关联物体列表
    delay_ms: int = 2000                                 # 截图延迟毫秒（默认2秒）
    # ── 变体导出 ──
    variant_mode: str = ""                               # "" | "add_lod" | "new_version" | "overwrite"
    variant_target_zasset: str = ""                      # 已有 .zasset 路径（追加/覆盖时使用）
    variant_target_version: str = ""                     # 目标版本 id（如 "v1"）
    variant_lod_level: int = 0                           # LOD 级别编号
    variant_lod_label: str = ""                          # LOD 显示标签
    variant_version_id: str = ""                         # 新版本 id（如 "v2"）
    variant_version_tag: str = ""                        # 新版本号（如 "2.0"）
    variant_version_label: str = ""                      # 新版本显示名
    variant_version_notes: str = ""                      # 新版本说明
    detect_maya_lod_group: bool = False                  # 自动检测 Maya LOD Group


@dataclass
class ExportResult:
    """单次导出操作的结果。"""
    asset_name: str = ""
    success: bool = False
    files: List[str] = field(default_factory=list)     # 生成的文件路径
    error: str = ""                                    # 失败原因
    thumbnail_path: str = ""


@dataclass
class BatchSummary:
    """批量导出的汇总结果。"""
    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    results: List[ExportResult] = field(default_factory=list)

    @property
    def failed_items(self) -> List[ExportResult]:
        """仅失败项列表。"""
        return [r for r in self.results if not r.success]

    def has_failures(self) -> bool:
        """是否有失败项。"""
        return self.failed_count > 0


# ═══════════════════════════════════════════════════════════════
# ViewportState — 上下文管理器
# ═══════════════════════════════════════════════════════════════

def _get_model_panel() -> str:
    """动态查找可用的 Maya 模型面板名称。

    Returns:
        面板名称，若都无法找到则回退到 'modelPanel4'
    """
    if not _IN_MAYA:
        return "modelPanel4"
    try:
        # 优先：当前拥有键盘焦点的面板
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.getPanel(typeOf=panel) == 'modelPanel':
            return panel
        # 次优先：第一个 modelPanel
        panels = cmds.getPanel(type='modelPanel')
        if panels:
            return panels[0]
    except Exception:
        pass
    return "modelPanel4"


class ViewportState:
    """保存/恢复 Maya viewport 状态的上下文管理器。

    用法::

        with ViewportState() as vs:
            cmds.select(some_objects)
            cmds.isolateSelect(some_objects, state=True)
            # ... viewport 操作 ...
        # 自动恢复原始状态

    在非 Maya 环境中为 no-op，不抛异常。
    """

    def __init__(self):
        self._selection: List[str] = []
        self._panel: str = "modelPanel4"
        self._was_isolated: bool = False

    def __enter__(self) -> "ViewportState":
        self.save()
        return self

    def __exit__(self, *args) -> None:
        self.restore()

    def save(self) -> None:
        """保存当前选中列表和隔离状态。"""
        if not _IN_MAYA:
            return
        try:
            self._selection = cmds.ls(selection=True, long=False) or []
        except Exception:
            self._selection = []
        # 动态获取面板名称
        self._panel = _get_model_panel()
        try:
            self._was_isolated = bool(
                cmds.isolateSelect(self._panel, query=True, state=True)
            )
        except Exception:
            self._was_isolated = False

    def restore(self) -> None:
        """恢复选中列表和隔离状态。"""
        if not _IN_MAYA:
            return
        try:
            cmds.isolateSelect(self._panel, state=False)
        except Exception:
            pass
        try:
            if self._selection:
                cmds.select(self._selection, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# ExportOrchestrator
# ═══════════════════════════════════════════════════════════════

class ExportOrchestrator:
    """资产导出编排器。

    统一管理单次/批量资产导出的完整 6 阶段管线。
    所有导出产物先写入临时目录，最终打包为单个 .zasset 文件。

    Args:
        base_dir: 资产库根目录（如 ~/SquirrelAssetLibrary/materials/）
    """

    # ── 几何体格式 → Maya file type 映射 ──
    GEOMETRY_FORMAT_MAP: Dict[str, str] = {
        "ma":  "mayaAscii",
        "mb":  "mayaBinary",
        "fbx": "FBX export",
        "obj": "OBJexport",
        "usd": "USD Export",
    }

    # 其他格式（折叠框内）
    # ── 配置 key → ExportConfig 字段映射 ──
    _CONFIG_TO_FIELD: Dict[str, str] = {
        "export_zmetal":       "export_zmetal",
        "export_ma":           "export_ma",
        "export_mb":           "export_mb",
        "export_fbx":          "export_fbx",
        "export_obj":          "export_obj",
        "export_usd":          "export_usd",
        "export_abc":          "export_abc",
        "export_arnold_ass":   "proxy_formats",   # 特殊：需要在代理列表中添加 "arnold"
        "export_vray_vrmesh":  "proxy_formats",
    }

    def __init__(self, base_dir: str):
        self._base_dir = os.path.normpath(base_dir) if base_dir else ""

    # ── 公共 API ──────────────────────────────────────────────

    def export_single(self, config: ExportConfig, skip_thumbnail: bool = False) -> ExportResult:
        """执行单资产完整 6 阶段导出。

        Args:
            config: 导出配置
            skip_thumbnail: True 时用占位缩略图，缩略图稍后单独生成

        Returns:
            ExportResult: 包含成功/失败状态和生成文件列表
        """
        result = ExportResult(asset_name=config.asset_name)
        safe_name = self._sanitize_filename(config.asset_name)

        # ── 变体导出：追加到已有资产 ──
        if config.variant_mode and config.variant_target_zasset:
            try:
                ok = self._export_as_variant(config, safe_name)
                result.success = ok
                if ok:
                    result.files = [config.variant_target_zasset]
                else:
                    result.error = "变体导出失败"
                return result
            except Exception as e:
                result.success = False
                result.error = f"{type(e).__name__}: {e}"
                return result

        zasset_path = os.path.join(self._base_dir, f"{safe_name}.zasset")
        staging_dir = tempfile.mkdtemp(prefix=f"{safe_name}_")

        try:
            # 跟踪实际导出的格式
            exported_formats: List[str] = []

            # Stage 1: 元数据 meta.json（始终）→ staging_dir
            meta_path = self._stage_meta(config, staging_dir, safe_name, exported_formats)

            # Stage 2: 贴图收集（按配置）→ staging_dir/textures/
            tex_path_map = {}
            if config.export_textures:
                tex_path_map = self._stage_textures(config, staging_dir)
            # 将 texture_map 写入 meta.json（原始路径→内部路径，用于导入精确匹配）
            if tex_path_map and os.path.isfile(meta_path):
                import json as _json
                with open(meta_path, 'r+', encoding='utf-8') as _mf:
                    _meta = _json.load(_mf)
                    _meta["texture_map"] = tex_path_map
                    _mf.seek(0)
                    _json.dump(_meta, _mf, indent=4, ensure_ascii=False)
                    _mf.truncate()

            # Stage 3: 材质预设 .zmetal / .mcm（按配置）→ staging_dir
            if config.export_zmetal:
                self._stage_zmetal(config, staging_dir, safe_name)
                exported_formats.append("zmetal")
                # Stage 3b: 更新 .zmetal 内的 fileTextureName → textures/{材质名}/{文件名}
                zmetal_path = os.path.join(staging_dir, f"{safe_name}.zmetal")
                if os.path.isfile(zmetal_path) and tex_path_map:
                    self._sync_zmetal_texture_paths(zmetal_path, tex_path_map)
                # .mcm：按配置导出
                if config.export_mcm:
                    mcm_path = self._stage_mcm(config, staging_dir, safe_name)
                    if mcm_path:
                        exported_formats.append("mcm")

            # 兜底扫描 staging_dir 中遗漏的 .mcm/.zmetal
            for _root, _dirs, _files in os.walk(staging_dir):
                for _f in _files:
                    _ext = os.path.splitext(_f)[1].lstrip(".")
                    if _ext in ("mcm",) and _ext not in exported_formats:
                        exported_formats.append(_ext)

            # Stage 4: 几何体（按配置）
            # material_only 模式下 ma/mb（Maya 原生格式）仍可导出材质节点；
            # fbx/obj/usd/glb 由 _stage_geometry 内部过滤跳过
            geom_files = self._stage_geometry(config, staging_dir, safe_name)
            for gf in geom_files:
                ext = os.path.splitext(gf)[1].lstrip(".")
                if ext not in exported_formats:
                    exported_formats.append(ext)
            # Stage 4b: 同步 .ma 文件内的贴图路径 → textures/{材质名}/{文件名}
            if tex_path_map:
                for gf in geom_files:
                    if gf.lower().endswith(".ma"):
                        self._sync_ma_texture_paths(gf, tex_path_map)

            # Stage 5: 缩略图（始终 / 可占位）
            if skip_thumbnail:
                thumb_path = ExportOrchestrator._generate_placeholder_thumbnail(staging_dir, safe_name)
            else:
                thumb_path = self._stage_thumbnail(config, staging_dir, safe_name)
            if thumb_path and thumb_path.lower().endswith('.aicon'):
                exported_formats.append("aicon")
            else:
                exported_formats.append("sicon")  # 缩略图视为已导出

            # Stage 6: 代理（按配置，仅非 material_only 模式）
            if not config.export_material_only:
                self._stage_proxy(config, staging_dir, safe_name)
                from squirrel_asset_manager.core.proxy_registry import ProxyFormatRegistry
                for pf in config.proxy_formats:
                    ext = ProxyFormatRegistry.get(pf)
                    exported_formats.append(ext.extension.lstrip(".") if ext else pf)

            # 构建 ani 字段（时间轴/关键帧模式导出的动画格式列表）
            exported_ani = []
            if config.ani_frame_mode in ("timeline", "keyframe"):
                if config.export_abc:
                    exported_ani.append("abc")
                if config.export_usd:
                    exported_ani.append("usd")
                if "arnold" in config.proxy_formats:
                    exported_ani.append("ass")
                if "redshift" in config.proxy_formats:
                    exported_ani.append("rs")
                if "vrmesh" in config.proxy_formats:
                    exported_ani.append("vrmesh")

            # ── 扫描 staging_dir → 构建 files_dict → ZassetBuilder.build() ──
            files_dict = {}
            meta = {}
            for _root, _dirs, filenames in os.walk(staging_dir):
                for fname in filenames:
                    full_path = os.path.join(_root, fname)
                    rel_path = os.path.relpath(full_path, staging_dir).replace("\\", "/")
                    if fname == "meta.json":
                        import json
                        with open(full_path, 'r', encoding='utf-8') as _f:
                            meta = json.load(_f)
                        continue
                    files_dict[rel_path] = full_path

            meta["formats"] = list(exported_formats) if exported_formats else []
            if exported_ani:
                meta["ani"] = exported_ani

            # 保留已有注释（覆盖已有资产时）
            if os.path.isdir(zasset_path):
                from squirrel_asset_manager.core.zasset_io import ZassetIO as _ZIO_PRESERVE
                existing_meta = _ZIO_PRESERVE.read_meta(zasset_path)
                if existing_meta and existing_meta.get("notes"):
                    meta["notes"] = existing_meta["notes"]

            try:
                from squirrel_asset_manager.core.zasset_builder import ZassetBuilder
            except ImportError:
                from core.zasset_builder import ZassetBuilder
            ZassetBuilder.build(zasset_path, files_dict, meta)

            if os.path.isdir(zasset_path):
                result.success = True
                result.files = [zasset_path]
                if thumb_path:
                    result.thumbnail_path = zasset_path  # 缩略图在 .zasset 内部

        except Exception as e:
            result.success = False
            result.error = f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

        return result

    def export_batch(
        self,
        configs: List[ExportConfig],
        on_progress: Callable[[int, int, str], None] = None,
        on_need_screenshot: Callable[[str, str], Optional[Tuple[float, float, float, float]]] = None,
    ) -> BatchSummary:
        """批量导出。

        逐个调用 export_single()，首个配置可交互（通过 on_need_screenshot），
        后续自动复用截图坐标。

        Args:
            configs: 导出配置列表
            on_progress: 进度回调 (current, total, asset_name)
            on_need_screenshot: 截图回调 (asset_name, thumb_path) → 返回截图选区或None(占位)

        Returns:
            BatchSummary: 汇总结果
        """
        total = len(configs)
        summary = BatchSummary(total=total)

        for i, cfg in enumerate(configs):
            if on_progress:
                on_progress(i + 1, total, cfg.asset_name)

            # 首个配置的截图坐标传递给后续配置
            if i == 0:
                result = self.export_single(cfg)
            else:
                # 复用首资产的截图坐标（如果存在）
                if configs[0].screenshot_rect is not None:
                    cfg.screenshot_rect = configs[0].screenshot_rect
                result = self.export_single(cfg)

            summary.results.append(result)
            if result.success:
                summary.success_count += 1
            else:
                summary.failed_count += 1

        return summary

    def retry_failed(
        self,
        summary: BatchSummary,
        on_progress: Callable[[int, int, str], None] = None,
        on_need_screenshot: Callable[[str, str], Optional[Tuple[float, float, float, float]]] = None,
        max_rounds: int = 2,
    ) -> BatchSummary:
        """重试失败项。

        仅对 summary.failed_items 重新执行 export_single()。
        成功项从 summary 中移除失败标记。

        Args:
            summary: 之前批量导出的汇总结果
            on_progress: 进度回调
            on_need_screenshot: 截图回调
            max_rounds: 最大重试轮数（默认2）

        Returns:
            新的 BatchSummary（包含原始成功项 + 重试结果）
        """
        for _round in range(max_rounds):
            failed = summary.failed_items
            if not failed:
                break

            for i, old_result in enumerate(failed):
                # 重建配置（从旧结果中反推资产名）
                cfg = ExportConfig(asset_name=old_result.asset_name, target_dir=self._base_dir)
                if on_progress:
                    on_progress(i + 1, len(failed), cfg.asset_name)

                new_result = self.export_single(cfg)

                # 替换旧结果
                for j, r in enumerate(summary.results):
                    if not r.success and r.asset_name == old_result.asset_name:
                        summary.results[j] = new_result
                        if new_result.success:
                            summary.success_count += 1
                            summary.failed_count -= 1
                        break

        return summary

    # ── 变体导出 ──────────────────────────────────────────────

    def _export_as_variant(self, config: ExportConfig, safe_name: str) -> bool:
        """变体导出：将几何体追加到已有 .zasset 资产的变体目录。

        根据 config.variant_mode:
          - "add_lod": 向已有版本追加 LOD 级别
          - "new_version": 创建新版本（初始 lod0）
          - "overwrite": 覆盖已有变体几何体文件

        支持 detect_maya_lod_group 自动检测 Maya LOD Group 并批量导出。
        """
        zasset_path = config.variant_target_zasset
        if not os.path.isdir(zasset_path):
            print(f"[VariantExport] 目标 .zasset 不存在: {zasset_path}")
            return False

        try:
            from squirrel_asset_manager.core.zasset_builder import ZassetBuilder
        except ImportError:
            from core.zasset_builder import ZassetBuilder

        mode = config.variant_mode

        # ── 自动检测 Maya LOD Group ──
        if config.detect_maya_lod_group and _IN_MAYA:
            return self._export_lod_group(zasset_path, config, safe_name)

        if mode == "add_lod":
            return self._export_add_lod(zasset_path, config, safe_name, ZassetBuilder)
        elif mode == "new_version":
            return self._export_new_version(zasset_path, config, safe_name, ZassetBuilder)
        elif mode == "overwrite":
            return self._export_overwrite_variant(zasset_path, config, safe_name)
        else:
            print(f"[VariantExport] 未知变体导出模式: {mode}")
            return False

    def _export_add_lod(self, zasset_path: str, config: ExportConfig,
                        safe_name: str, ZassetBuilder) -> bool:
        """追加 LOD 级别到已有版本"""
        staging_dir = tempfile.mkdtemp(prefix=f"{safe_name}_lod_")
        try:
            # 仅导出几何体
            geom_files = self._stage_geometry(config, staging_dir, safe_name)
            if not geom_files:
                print("[VariantExport] 几何体导出为空")
                return False

            # 构建 geom_files 映射
            geom_map = {}
            for gf in geom_files:
                ext = os.path.splitext(gf)[1].lstrip(".")
                rel = f"node.{ext}"
                geom_map[rel] = gf

            # 自动计算面数统计
            stats = None
            if _IN_MAYA:
                stats = self._compute_poly_stats(config)

            version_id = config.variant_target_version or "v1"
            lod_level = config.variant_lod_level
            lod_label = config.variant_lod_label or f"LOD{lod_level}"

            ok = ZassetBuilder.add_variant_lod(
                zasset_path=zasset_path,
                version=version_id,
                lod_level=lod_level,
                lod_label=lod_label,
                geom_files=geom_map,
                stats=stats,
            )
            print(f"[VariantExport] add_lod: version={version_id}, lod={lod_level}, ok={ok}")
            return ok
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _export_new_version(self, zasset_path: str, config: ExportConfig,
                            safe_name: str, ZassetBuilder) -> bool:
        """创建新版本（初始 lod0），支持独立材质/贴图"""
        staging_dir = tempfile.mkdtemp(prefix=f"{safe_name}_ver_")
        try:
            geom_files = self._stage_geometry(config, staging_dir, safe_name)
            if not geom_files:
                print("[VariantExport] 几何体导出为空")
                return False

            geom_map = {}
            for gf in geom_files:
                ext = os.path.splitext(gf)[1].lstrip(".")
                rel = f"node.{ext}"
                geom_map[rel] = gf

            version_id = config.variant_version_id or f"v{len(self._get_existing_versions(zasset_path)) + 1}"
            version_tag = config.variant_version_tag or version_id.lstrip("v")
            version_label = config.variant_version_label or version_id
            version_notes = config.variant_version_notes or ""

            ok = ZassetBuilder.add_variant_version(
                zasset_path=zasset_path,
                version_id=version_id,
                version_tag=version_tag,
                label=version_label,
                notes=version_notes,
                lod_files=geom_map,
            )
            if not ok:
                return False

            # ── 导出独立材质到 variants/{version}/ ──
            if config.export_zmetal and _IN_MAYA:
                var_dir = os.path.join(zasset_path, "variants", version_id)
                self._export_variant_material(config, var_dir, zasset_path, version_id)

            print(f"[VariantExport] new_version: {version_id} ({version_tag}), mat={config.export_zmetal}, ok={ok}")
            return ok
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _export_variant_material(self, config: ExportConfig, var_dir: str,
                                 zasset_path: str, version_id: str):
        """导出材质和贴图到变体版本目录"""
        mat_staging = tempfile.mkdtemp(prefix="variant_mat_")
        try:
            safe_name = config.asset_name

            # 导出 .zmetal
            if config.export_zmetal:
                self._stage_zmetal(config, mat_staging, safe_name)

            # 导出 .mcm
            mcm_path = None
            if config.export_mcm:
                mcm_path = self._stage_mcm(config, mat_staging, safe_name)

            # 复制贴图到变体目录
            tex_path_map = self._stage_textures(config, mat_staging)
            if tex_path_map:
                # 更新 .zmetal 内的贴图路径
                zmetal_path = os.path.join(mat_staging, f"{safe_name}.zmetal")
                if os.path.isfile(zmetal_path):
                    self._sync_zmetal_texture_paths(zmetal_path, tex_path_map)

            # 移动文件到 variants/{version}/
            os.makedirs(var_dir, exist_ok=True)
            for fname in os.listdir(mat_staging):
                src = os.path.join(mat_staging, fname)
                dst = os.path.join(var_dir, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                print(f"[VariantExport] 材质 → {dst}")

            # 更新 variants.json 记录材质路径
            from core.zasset_io import ZassetIO
            variants = ZassetIO.read_variants(zasset_path)
            for v in variants.get("versions", []):
                if v.get("id") == version_id:
                    v["material"] = f"variants/{version_id}/node.zmetal"
                    break
            ZassetIO.write_variants(zasset_path, variants)

        finally:
            shutil.rmtree(mat_staging, ignore_errors=True)

    def _export_overwrite_variant(self, zasset_path: str, config: ExportConfig,
                                  safe_name: str) -> bool:
        """覆盖已有变体的几何体文件"""
        staging_dir = tempfile.mkdtemp(prefix=f"{safe_name}_overwrite_")
        try:
            geom_files = self._stage_geometry(config, staging_dir, safe_name)
            if not geom_files:
                print("[VariantExport] 几何体导出为空")
                return False

            version_id = config.variant_target_version or "v1"
            lod_id = f"lod{config.variant_lod_level}"

            # 直接覆盖变体目录下的文件
            variant_dir = os.path.join(zasset_path, "variants", version_id, lod_id)
            os.makedirs(variant_dir, exist_ok=True)

            for gf in geom_files:
                ext = os.path.splitext(gf)[1].lstrip(".")
                target = os.path.join(variant_dir, f"node.{ext}")
                shutil.copy2(gf, target)
                print(f"[VariantExport] overwrite: {target}")

            print(f"[VariantExport] overwrite: version={version_id}, lod={lod_id}, files={len(geom_files)}")
            return True
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _export_lod_group(self, zasset_path: str, config: ExportConfig,
                          safe_name: str) -> bool:
        """自动检测 Maya LOD Group 并批量导出所有级别"""
        if not _IN_MAYA:
            print("[VariantExport] LOD Group 导出需要 Maya 环境")
            return False

        try:
            # 检测场景中的 LOD Group
            lod_groups = cmds.ls(type="lodGroup") or []
            if not lod_groups:
                print("[VariantExport] 场景中未找到 LOD Group")
                return False

            # 使用第一个找到的 LOD Group
            lod_group = lod_groups[0]
            print(f"[VariantExport] 检测到 LOD Group: {lod_group}")

            # 获取 LOD 阈值
            thresholds = cmds.lodGroup(lod_group, query=True, threshold=True) or []

            # 获取每个级别的物体
            level_objects = []
            for i in range(cmds.lodGroup(lod_group, query=True, level=True)):
                objs = cmds.lodGroup(lod_group, query=True, level=i, object=True) or []
                level_objects.append(objs)

            if not level_objects:
                print("[VariantExport] LOD Group 无子物体")
                return False

            try:
                from squirrel_asset_manager.core.zasset_builder import ZassetBuilder
            except ImportError:
                from core.zasset_builder import ZassetBuilder

            # 决定版本 ID
            existing = self._get_existing_versions(zasset_path)
            if config.variant_mode == "new_version":
                version_id = config.variant_version_id or f"v{len(existing) + 1}"
                version_tag = config.variant_version_tag or version_id.lstrip("v")
                version_label = config.variant_version_label or version_id
                version_notes = config.variant_version_notes or ""
            else:
                version_id = config.variant_target_version or existing[-1] if existing else "v1"

            all_ok = True
            for level, objs in enumerate(level_objects):
                if not objs:
                    continue

                staging_dir = tempfile.mkdtemp(prefix=f"{safe_name}_lod{level}_")
                try:
                    cmds.select(objs, replace=True)

                    # 配置几何体导出
                    level_cfg = ExportConfig(
                        asset_name=safe_name,
                        export_ma=config.export_ma,
                        export_mb=config.export_mb,
                        export_fbx=config.export_fbx,
                        export_obj=config.export_obj,
                        export_usd=config.export_usd,
                        associated_objects=list(objs),
                    )

                    geom_files = self._stage_geometry(level_cfg, staging_dir, safe_name)
                    if not geom_files:
                        continue

                    geom_map = {}
                    for gf in geom_files:
                        ext = os.path.splitext(gf)[1].lstrip(".")
                        geom_map[f"node.{ext}"] = gf

                    # 计算面数
                    stats = self._compute_poly_stats_simple(objs)

                    # LOD 标签
                    if level == 0:
                        label = "高精度"
                    elif level == len(level_objects) - 1:
                        label = "低精度"
                    else:
                        label = f"中精度 LOD{level}"

                    ok = ZassetBuilder.add_variant_lod(
                        zasset_path=zasset_path,
                        version=version_id,
                        lod_level=level,
                        lod_label=label,
                        geom_files=geom_map,
                        stats=stats,
                    )
                    if not ok:
                        all_ok = False
                    print(f"[VariantExport] LOD Group level={level}: {label}, ok={ok}")
                finally:
                    shutil.rmtree(staging_dir, ignore_errors=True)

            # 如果是新版本，设置版本元数据
            if config.variant_mode == "new_version":
                from core.zasset_io import ZassetIO
                variants = ZassetIO.read_variants(zasset_path)
                versions = variants.get("versions", [])
                for v in versions:
                    if v.get("id") == version_id:
                        v["tag"] = version_tag
                        v["label"] = version_label
                        v["notes"] = version_notes
                        from datetime import date
                        v["create_date"] = date.today().isoformat()
                        break
                ZassetIO.write_variants(zasset_path, variants)

                meta = ZassetIO.read_meta(zasset_path)
                if meta:
                    vtypes = set(meta.get("variant_types", []))
                    vtypes.add("version")
                    meta["variant_types"] = sorted(vtypes)
                    ZassetIO.write_meta(zasset_path, meta)

            # 恢复选择
            cmds.select(clear=True)
            return all_ok

        except Exception as e:
            print(f"[VariantExport] LOD Group 导出失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    @staticmethod
    def _compute_poly_stats(config: ExportConfig) -> dict:
        """从 ExportConfig 关联物体计算面数统计"""
        if not _IN_MAYA:
            return None
        stats = {"triangles": 0, "vertices": 0}
        objects = config.associated_objects or []
        if config.material_node and cmds.objExists(config.material_node):
            # 如果选了材质节点，查找关联物体
            try:
                sg = cmds.listConnections(config.material_node, type="shadingEngine") or []
                for s in sg:
                    linked = cmds.listConnections(s + ".dagSetMembers") or []
                    objects.extend(linked)
            except Exception:
                pass
        return ExportOrchestrator._compute_poly_stats_simple(objects)

    @staticmethod
    def _compute_poly_stats_simple(objects: list) -> dict:
        """从物体列表计算总面数和顶点数"""
        if not _IN_MAYA:
            return None
        total_tris = 0
        total_verts = 0
        for obj in set(objects):
            try:
                if not cmds.objExists(obj):
                    continue
                shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or [obj]
                for shp in shapes:
                    if cmds.nodeType(shp) in ('mesh',):
                        total_tris += cmds.polyEvaluate(shp, triangle=True)
                        total_verts += cmds.polyEvaluate(shp, vertex=True)
            except Exception:
                pass
        if total_tris == 0 and total_verts == 0:
            return None
        return {"triangles": total_tris, "vertices": total_verts}

    @staticmethod
    def _get_existing_versions(zasset_path: str) -> list:
        """获取已有版本 ID 列表"""
        from core.zasset_io import ZassetIO
        variants = ZassetIO.read_variants(zasset_path)
        return [v.get("id", "") for v in variants.get("versions", [])]

    # ── 静态工具 ──────────────────────────────────────────────

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理文件名中的非法字符。

        >>> ExportOrchestrator._sanitize_filename("Jeep:Mat/Test|Node")
        'Jeep_Mat_Test_Node'
        """
        if not name:
            return "unnamed"
        # 替换非法字符
        name = re.sub(r'[:\/\\|<>"*?]', '_', name)
        # 合并连续下划线
        name = re.sub(r'_+', '_', name)
        # 去首尾下划线
        return name.strip('_')

    @staticmethod
    def _generate_placeholder_thumbnail(asset_dir: str, safe_name: str) -> str:
        """生成占位缩略图（256x256 灰色方块）。

        Returns:
            缩略图文件路径，失败返回空字符串
        """
        thumb_path = os.path.join(asset_dir, "thumb.sicon")
        try:
            # 尝试生成一个简单的灰色 PNG
            try:
                from PySide6.QtGui import QImage, QPainter, QColor
                from PySide6.QtCore import Qt
                from PySide6.QtWidgets import QApplication
            except ImportError:
                try:
                    from PySide2.QtGui import QImage, QPainter, QColor
                    from PySide2.QtCore import Qt
                    from PySide2.QtWidgets import QApplication
                except ImportError:
                    return ""

            # PySide6 QPainter 需要 QApplication 实例（Windows 上尤其）
            if QApplication.instance() is None:
                return ""

            img = QImage(256, 256, QImage.Format.Format_ARGB32)
            img.fill(QColor(64, 64, 64))  # 深灰色

            # 画一个浅灰色边框
            painter = QPainter(img)
            painter.setPen(QColor(128, 128, 128))
            painter.drawRect(4, 4, 247, 247)

            # 居中写资产名缩写
            painter.setPen(QColor(200, 200, 200))
            font = painter.font()
            font.setPointSize(18)
            painter.setFont(font)
            display = safe_name[:20] if len(safe_name) > 20 else safe_name
            painter.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, display)
            painter.end()

            os.makedirs(asset_dir, exist_ok=True)
            img.save(thumb_path, "PNG")
            return thumb_path
        except Exception:
            return ""

    # ── Stage 1: 元数据 ──────────────────────────────────────

    def _stage_meta(
        self, config: ExportConfig, asset_dir: str, safe_name: str,
        exported_formats: List[str],
    ) -> str:
        """生成 meta.json 元数据文件。

        Returns:
            meta.json 文件路径，失败返回空字符串
        """
        meta_path = os.path.join(asset_dir, "meta.json")
        try:
            # 构建元数据
            meta = {
                "id": str(uuid.uuid4()),
                "version": "2.0",
                "software": "Maya",
                "asset_type": config.asset_type,
                "name": config.asset_name,
                "name_cn": config.name_cn or config.asset_name,
                "node_type": self._detect_node_type(config),
                "category": config.category,
                "tags": config.tags,
            }

            # 补充 Maya 运行时信息
            if _IN_MAYA:
                # 获取 Maya 版本号（如 "Maya 2025"）
                _maya_software = "Maya"
                try:
                    import maya.cmds as cmds
                    _maya_software = f"Maya {cmds.about(version=True)}"
                except Exception:
                    pass
                try:
                    # 尝试获取导出 header
                    from squirrel_asset_manager.integration.zjg_exporter import _get_export_header
                    header = _get_export_header()
                    meta["software"] = header.get("software", _maya_software)
                    meta["renderer"] = header.get("renderer", "unknown")
                    meta["color_space"] = header.get("color_space", "ACEScg")
                    meta["create_date"] = header.get("create_date", "")
                except Exception:
                    meta["software"] = _maya_software
                    meta["renderer"] = "unknown"
                    meta["color_space"] = "ACEScg"
            else:
                meta["renderer"] = "unknown"
                meta["color_space"] = "ACEScg"

            # 写入文件
            import json
            os.makedirs(os.path.dirname(meta_path), exist_ok=True)
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=4, ensure_ascii=False)

            return meta_path
        except Exception:
            return ""

    @staticmethod
    def _detect_node_type(config: "ExportConfig") -> str:
        """从导出配置中检测 Maya 材质节点类型（如 standardSurface）。
        非 Maya 环境或无材质节点时返回空字符串。"""
        if not _IN_MAYA:
            return ""
        try:
            import maya.cmds as cmds
            node = config.material_node
            if node and cmds.objExists(node):
                return cmds.nodeType(node)
        except Exception:
            pass
        return ""

    # ── Stage 2: 贴图收集 ────────────────────────────────────

    def _stage_textures(self, config: ExportConfig, asset_dir: str) -> dict:
        """收集并复制材质关联贴图到 textures/{材质名}/{文件名}。

        Returns:
            {原始绝对路径: textures/{材质名}/{文件名}} 路径映射，用于后续 sync
        """
        textures_dir = os.path.join(asset_dir, "textures")
        path_map: dict = {}  # {original_abs_path: textures/{mat}/{filename}}

        if not _IN_MAYA:
            return path_map

        try:
            from squirrel_asset_manager.integration.zjg_exporter import (
                _collect_texture_paths_from_materials,
            )

            # 确定材质列表
            if config.merge_zmetal:
                mats = self._get_materials_from_objects(config.associated_objects)
                if config.material_node and cmds.objExists(config.material_node) \
                        and config.material_node not in mats:
                    mats.append(config.material_node)
            elif config.material_node and cmds.objExists(config.material_node):
                mats = [config.material_node]
            elif config.associated_objects:
                mats = self._get_materials_from_objects(config.associated_objects)
            else:
                mats = []

            # 按材质分别收集并复制贴图到 textures/{材质名}/
            for mat in mats:
                tex_paths = _collect_texture_paths_from_materials([mat])
                if not tex_paths:
                    continue
                mat_dir = os.path.join(textures_dir, mat)
                os.makedirs(mat_dir, exist_ok=True)
                for src in tex_paths:
                    if not os.path.isfile(src):
                        continue
                    dst = os.path.join(mat_dir, os.path.basename(src))
                    if not os.path.isfile(dst) or os.path.getmtime(src) != os.path.getmtime(dst):
                        shutil.copy2(src, dst)
                    # 记录原始绝对路径 → 新相对路径
                    norm_src = src.replace("\\", "/")
                    rel = f"textures/{mat}/{os.path.basename(dst)}"
                    path_map[norm_src] = rel
        except Exception as e:
            print(f"[StageTextures] 异常: {e}")

        return path_map

    # ── Stage 3: 材质预设 ────────────────────────────────────

    def _stage_zmetal(self, config: ExportConfig, asset_dir: str, safe_name: str) -> List[str]:
        """导出 .zmetal 材质预设文件。

        merge_zmetal=True 时收集所有关联材质写入一个文件。

        Returns:
            生成的文件路径列表（含 .zmetal）
        """
        files: List[str] = []
        if not _IN_MAYA:
            return files

        from squirrel_asset_manager.integration.zjg_exporter import radar_export_materials

        try:
            print(f"[Export::zmetal] 开始导出 node_type={cmds.nodeType(config.material_node) if config.material_node and cmds.objExists(config.material_node) else '?'} material_node={config.material_node} merge={config.merge_zmetal}")
            if config.merge_zmetal:
                # ── 合并模式：收集所有关联节点 → 一个 .zmetal ──
                all_materials = set()

                # 从 material_node 添加（不限制类型）
                if config.material_node and cmds.objExists(config.material_node):
                    all_materials.add(config.material_node)

                # 从关联物体收集连接的材质/灯光
                for obj in config.associated_objects or []:
                    if not cmds.objExists(obj):
                        continue
                    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or [obj]
                    for shp in shapes:
                        if not cmds.objExists(shp):
                            continue
                        sg = cmds.listConnections(shp, type="shadingEngine") or []
                        for se in sg:
                            mat = cmds.listConnections(se + ".surfaceShader") or []
                            for m in mat:
                                if cmds.objExists(m):
                                    all_materials.add(m)

                if not all_materials:
                    return files

                cmds.select(list(all_materials), replace=True)
                radar_export_materials(
                    target_dir=asset_dir,
                    custom_name=safe_name,
                    selection_is_material=True,
                    export_all=False,
                    separate_files=False,       # ← 合并到一个文件
                    create_material_folder=False,
                    export_metadata=False,
                    export_objects=False,
                    pack_textures=False,
                    color_space="ACEScg",
                    category=config.category,
                    tags=",".join(config.tags) if config.tags else "",
                    name_cn=config.name_cn,
                )
            else:
                # ── 单材质模式：原有逻辑 ──
                if not config.material_node:
                    return files
                if not cmds.objExists(config.material_node):
                    return files

                cmds.select(config.material_node, replace=True)
                radar_export_materials(
                    target_dir=asset_dir,
                    custom_name=safe_name,
                    selection_is_material=True,
                    export_all=False,
                    separate_files=True,
                    create_material_folder=False,
                    export_metadata=False,
                    export_objects=False,
                    pack_textures=False,
                    color_space="ACEScg",
                    category=config.category,
                    tags=",".join(config.tags) if config.tags else "",
                    name_cn=config.name_cn,
                )

            # 验证生成的文件
            zmetal_path = os.path.join(asset_dir, f"{safe_name}.zmetal")
            if os.path.isfile(zmetal_path):
                files.append(zmetal_path)
                print(f"[Export::zmetal] 成功: {zmetal_path}")
            else:
                print(f"[Export::zmetal] 未生成 .zmetal (预期路径={zmetal_path})")
        except Exception as e:
            print(f"[Export] zmetal 导出异常: {e}")
            import traceback
            traceback.print_exc()

        return files

    def _stage_mcm(self, config: ExportConfig, asset_dir: str, safe_name: str) -> str:
        """导出 .mcm 材质→模型映射文件。

        Returns:
            .mcm 文件路径，失败返回空字符串
        """
        mcm_path = os.path.join(asset_dir, f"{safe_name}.mcm")
        if not _IN_MAYA:
            return ""

        try:
            from squirrel_asset_manager.integration.zjg_exporter import (
                _get_assigned_objects,
                _get_face_material_assignments,
            )

            # 收集材质列表
            if config.merge_zmetal:
                all_materials = self._get_materials_from_objects(config.associated_objects)
                if config.material_node and cmds.objExists(config.material_node) \
                        and config.material_node not in all_materials:
                    all_materials.append(config.material_node)
            else:
                all_materials = [config.material_node] if (
                    config.material_node and cmds.objExists(config.material_node)
                ) else []

            if not all_materials:
                return ""

            data = {}
            for mat in all_materials:
                assigned = _get_assigned_objects(mat)
                shape_list = list(assigned) if assigned else config.associated_objects[:1] if config.associated_objects else []
                face_assignments = _get_face_material_assignments(shape_list) if shape_list else {}
                mat_face = {}
                for mesh_name, mat_faces in face_assignments.items():
                    if mat in mat_faces:
                        mat_face[mesh_name] = {mat: mat_faces[mat]}
                # 记录每个物体的顶点/面/边数（三维匹配用）
                obj_list = list(assigned) if assigned else []
                match_info = {}
                for obj in obj_list:
                    try:
                        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or [obj]
                        for shp in shapes:
                            if cmds.nodeType(shp) in ('mesh',):
                                match_info[obj] = {
                                    "vert": cmds.polyEvaluate(shp, vertex=True),
                                    "face": cmds.polyEvaluate(shp, face=True),
                                    "edge": cmds.polyEvaluate(shp, edge=True),
                                }
                                break
                    except Exception:
                        pass
                data[mat] = {
                    "count": len(assigned) if assigned else 0,
                    "objects": obj_list,
                    "match_info": match_info,
                    "face_assignments": mat_face,
                }

            import json
            os.makedirs(asset_dir, exist_ok=True)
            with open(mcm_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            return mcm_path
        except Exception as e:
            print(f"[MCM] 导出失败: {e}")
            return ""

    # ── Stage 4: 几何体 ──────────────────────────────────────

    def _resolve_export_objects(self, config: ExportConfig) -> List[str]:
        """按 Maya 默认行为解析导出目标。

        用户选了什么就尝试导出什么。
        1. associated_objects → 用户选中的物体（不限于材质关联）
        2. material_node      → 仅选了材质节点，返回节点本身
        3. 空列表             → 跳过导出
        """
        if config.associated_objects:
            return [o for o in config.associated_objects if cmds.objExists(o)]
        if _IN_MAYA and config.material_node and cmds.objExists(config.material_node):
            return [config.material_node]
        return []

    @staticmethod
    def _is_dag_object(obj: str) -> bool:
        """检查物体是否为视口可见的 DAG 物体（非材质等 DG 节点）。"""
        if not _IN_MAYA or not cmds.objExists(obj):
            return False
        try:
            return 'dagNode' in cmds.nodeType(obj, inherited=True)
        except Exception:
            return False

    def _stage_geometry(self, config: ExportConfig, asset_dir: str, safe_name: str) -> List[str]:
        """按配置导出几何体格式。

        Returns:
            生成的几何体文件路径列表
        """
        files: List[str] = []
        if not _IN_MAYA:
            return files

        for fmt_key, maya_type in self.GEOMETRY_FORMAT_MAP.items():
            field_name = f"export_{fmt_key}"
            if not getattr(config, field_name, False):
                continue

            # material_only 模式：仅 ma/mb（Maya 原生格式）可导出材质节点
            if config.export_material_only and fmt_key not in ("ma", "mb"):
                continue

            try:
                geom_path = os.path.join(asset_dir, f"{safe_name}.{fmt_key}")

                # material_only 模式：仅导出材质节点本身，不导出关联 DAG 物体
                if config.export_material_only:
                    if config.merge_zmetal:
                        # 合并模式：导出全部材质到 ma/mb
                        mats = self._get_materials_from_objects(config.associated_objects)
                        if config.material_node and cmds.objExists(config.material_node) \
                                and config.material_node not in mats:
                            mats.append(config.material_node)
                        export_objs = mats
                    else:
                        export_objs = [config.material_node] if (
                            _IN_MAYA and config.material_node and cmds.objExists(config.material_node)
                        ) else []
                else:
                    export_objs = self._resolve_export_objects(config)
                if not export_objs:
                    print(f"[Export] 跳过 {fmt_key} 导出：无关联物体且未找到材质关联物体")
                    continue

                cmds.select(export_objs, replace=True)

                if fmt_key == "usd":
                    anim_mode = config.ani_frame_mode if not config.export_material_only else "current"
                    if anim_mode in ("timeline", "keyframe"):
                        if anim_mode == "keyframe":
                            kf_start, kf_end = ExportOrchestrator.get_keyframe_range(config.associated_objects or [])
                            if kf_start < kf_end:
                                min_t, max_t = kf_start, kf_end
                            else:
                                min_t = max_t = int(cmds.currentTime(q=True))
                        else:
                            min_t = int(cmds.playbackOptions(q=True, minTime=True))
                            max_t = int(cmds.playbackOptions(q=True, maxTime=True))
                        cmds.mayaUSDExport(
                            file=geom_path,
                            frameRange=(min_t, max_t),
                            selection=True,
                            eulerFilter=True,
                            exportUVs=1,
                            defaultUSDFormat="ascii",
                        )
                    else:
                        cmds.file(geom_path, exportSelected=True,
                                  type=maya_type, force=True,
                                  options="exportUVs=1;exportDisplayColor=1;exportMaterial=1;exportVisibility=1")
                else:
                    # 非 USD 格式：通过 cmds.file 导出
                    cmds.file(geom_path, exportSelected=True, type=maya_type, force=True)

                if os.path.isfile(geom_path):
                    files.append(geom_path)
            except Exception as e:
                print(f"[Export] {fmt_key} 导出失败: {e}")

        # ── 缓存格式（Alembic .abc） ──
        if config.export_abc and not config.export_material_only:
            try:
                abc_path = os.path.join(asset_dir, f"{safe_name}.abc")
                export_objs = self._resolve_export_objects(config)
                if export_objs:
                    if config.ani_frame_mode == "timeline":
                        min_t = cmds.playbackOptions(q=True, minTime=True)
                        max_t = cmds.playbackOptions(q=True, maxTime=True)
                        fr = f"{int(min_t)} {int(max_t)}"
                    elif config.ani_frame_mode == "keyframe":
                        kf_start, kf_end = ExportOrchestrator.get_keyframe_range(config.associated_objects or [])
                        if kf_start < kf_end:
                            fr = f"{kf_start} {kf_end}"
                        else:
                            cur = cmds.currentTime(q=True)
                            fr = f"{int(cur)} {int(cur)}"
                    else:
                        cur = cmds.currentTime(q=True)
                        fr = f"{int(cur)} {int(cur)}"
                    roots = " ".join(f"-root {o}" for o in export_objs)
                    job = (f"-frameRange {fr} -uvWrite -writeVisibility "
                           f"-dataFormat ogawa {roots} -file {abc_path}")
                    print(f"[Export] AbcExport: {job}")
                    cmds.AbcExport(j=job)
                    if os.path.isfile(abc_path) and os.path.getsize(abc_path) > 0:
                        files.append(abc_path)
                    else:
                        print(f"[Export] AbcExport 文件为空或不存在: {abc_path}")
            except Exception as e:
                print(f"[Export] abc 导出失败: {e}")

        return files

    # ── Stage 5: 缩略图 ──────────────────────────────────────

    def _stage_thumbnail(self, config: ExportConfig, asset_dir: str, safe_name: str) -> str:
        """根据 thumb_source 生成缩略图。

        - "screenshot": 由 main_window overlay 处理，此方法返回空
        - "playblast":  视口拍屏多帧 → GIF (.aicon)
        - "render":     当前渲染器渲染单帧 → .sicon
        """
        if config.skip_thumbnail:
            return ""

        thumb_source = getattr(config, 'thumb_source', 'screenshot')

        if thumb_source == "playblast":
            return self._do_playblast_thumbnail(config, asset_dir, safe_name)
        elif thumb_source == "render":
            return self._do_render_thumbnail(config, asset_dir, safe_name)
        else:
            # "screenshot" — 由 overlay 处理
            return ""

    @staticmethod
    def _find_ffmpeg() -> str:
        """查找 ffmpeg 可执行文件（优先插件内置 bin/）"""
        import os
        # ① 插件内置 bin/ffmpeg.exe
        plugin_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bin', 'ffmpeg.exe')
        if os.path.isfile(plugin_bin):
            return plugin_bin
        # ② 系统 PATH
        import shutil
        found = shutil.which('ffmpeg')
        if found:
            return found
        # ③ 常见安装路径
        for p in ['C:/ffmpeg/bin/ffmpeg.exe', 'C:/Program Files/ffmpeg/bin/ffmpeg.exe']:
            if os.path.isfile(p):
                return p
        return ''

    def _do_playblast_thumbnail(self, config: ExportConfig, asset_dir: str, safe_name: str) -> str:
        """用 cmds.playblast 拍屏 PNG 序列 → ffmpeg MP4 + 首帧静态，无 ffmpeg 则 PIL GIF"""
        import os
        import glob
        import tempfile
        import shutil
        import subprocess

        try:
            import maya.cmds as cmds
        except ImportError:
            return ""

        tmp_dir = tempfile.mkdtemp(prefix="playblast_")
        out_base = os.path.join(tmp_dir, "frame")

        try:
            if config.ani_frame_mode == "keyframe":
                kf_start, kf_end = ExportOrchestrator.get_keyframe_range(config.associated_objects or [])
                if kf_start < kf_end:
                    start, end = kf_start, kf_end
                else:
                    start = end = int(cmds.currentTime(query=True))
            else:
                start = int(cmds.playbackOptions(query=True, min=True))
                end = int(cmds.playbackOptions(query=True, max=True))
            # 超过 240 帧则缩范围
            if end - start > 240:
                end = start + 240

            # 框显物体（与截图工具逻辑一致）
            try:
                from maya import mel
                dag_objs = [o for o in (config.associated_objects or [])
                            if 'dagNode' in cmds.nodeType(o, inherited=True)]
                if dag_objs:
                    cmds.select(dag_objs, replace=True)
                    mel.eval('fitAllPanels -selectedNoChildren')
                    cmds.select(clear=True)
                    cmds.refresh()
                else:
                    mel.eval('FrameAll')
                import time
                time.sleep(0.5)
            except Exception:
                pass

            # 增大 preScale 让物体在画面中占比更大
            try:
                cmds.setAttr('perspShape.preScale', 2)
            except Exception:
                pass

            cmds.playblast(
                format='image',
                compression='jpg',
                filename=out_base,
                sequenceTime=False,
                clearCache=True,
                viewer=False,
                showOrnaments=False,
                startTime=start,
                endTime=end,
                widthHeight=(512, 512),
                forceOverwrite=True,
            )

            # 恢复 preScale
            try:
                cmds.setAttr('perspShape.preScale', 1)
            except Exception:
                pass

            frames = sorted(
                [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)
                 if os.path.isfile(os.path.join(tmp_dir, f))],
                key=lambda x: os.path.getmtime(x)
            )
            if not frames:
                print("[Playblast] 无 PNG 输出")
                return ""

            fps = max(1, min(30, len(frames) / max(1.0, (end - start) / 24.0)))

            # ① 尝试 ffmpeg → MP4 + 首帧静态缩略图
            ffmpeg = self._find_ffmpeg()
            if ffmpeg:
                try:
                    mp4_path = os.path.join(asset_dir, "thumb.mp4")
                    sicon_path = os.path.join(asset_dir, "thumb.sicon")
                    list_path = os.path.join(tmp_dir, "frames.txt")
                    with open(list_path, 'w') as f:
                        for fp in frames:
                            f.write(f"file '{fp.replace(os.sep, '/')}'\n")
                    cmd = [
                        ffmpeg, '-y', '-f', 'concat', '-safe', '0',
                        '-i', list_path,
                        '-vf', 'scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2',
                        '-r', str(int(fps)),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-crf', '12', '-preset', 'fast',
                        mp4_path,
                    ]
                    result = subprocess.run(cmd, capture_output=True, timeout=60)
                    if result.returncode == 0 and os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 100:
                        # 首帧 → 静态卡片缩略图
                        if frames:
                            shutil.copy2(frames[0], sicon_path)
                        print(f"[Playblast] MP4: {mp4_path} ({len(frames)}帧/{fps:.0f}fps) + 首帧")
                        return mp4_path
                except Exception:
                    pass

            # ② 兜底: PIL 高质量 GIF
            try:
                from PIL import Image
                aicon_path = os.path.join(asset_dir, "thumb.aicon")
                images = []
                for fp in frames:
                    try:
                        img = Image.open(fp).convert("RGB")
                        img = img.resize((512, 512), Image.LANCZOS)
                        images.append(img)
                    except Exception:
                        continue
                if images:
                    duration = int(1000 / max(1, fps))
                    images[0].save(
                        aicon_path, format='GIF',
                        save_all=True, append_images=images[1:],
                        duration=duration, loop=0,
                        optimize=False, quality=80,
                    )
                    print(f"[Playblast] GIF: {aicon_path} ({len(images)}帧)")
                    return aicon_path
            except ImportError:
                pass

            # ③ 最后兜底: 第一帧当静态
            if frames:
                sicon_path = os.path.join(asset_dir, "thumb.sicon")
                shutil.copy2(frames[0], sicon_path)
                print(f"[Playblast] 静态: {sicon_path}")
                return sicon_path
            return ""

        except Exception as e:
            print(f"[Playblast] 失败: {e}")
            import traceback; traceback.print_exc()
            return ""
        finally:
            try: shutil.rmtree(tmp_dir, ignore_errors=True)
            except: pass

    def _do_render_thumbnail(self, config: ExportConfig, asset_dir: str, safe_name: str) -> str:
        """用当前渲染器渲染 → .sicon / .mp4（cmds.RenderSequence 帧范围）"""
        import subprocess
        import glob as _glob

        try:
            import maya.cmds as cmds
            from maya import mel
        except ImportError:
            return ""

        sicon_path = os.path.join(asset_dir, "thumb.sicon")
        tmp_dir = tempfile.mkdtemp(prefix="render_thumb_")
        result_path = ""

        saved = {}
        was_hidden = {}
        old_images = ""

        try:
            # ── 框显 + 可见性处理 ──
            dag_objs = [o for o in (config.associated_objects or [])
                        if 'dagNode' in cmds.nodeType(o, inherited=True)]
            if dag_objs:
                for obj in dag_objs:
                    try:
                        was_hidden[obj] = not cmds.getAttr(obj + '.visibility')
                    except Exception:
                        pass
                for obj in dag_objs:
                    try:
                        cmds.setAttr(obj + '.visibility', True)
                    except Exception:
                        pass
                cmds.select(dag_objs, replace=True)
                mel.eval('fitAllPanels -selectedNoChildren')
                cmds.select(clear=True)
            else:
                mel.eval('FrameAll')
            cmds.refresh()

            # ── 保存渲染设置 ──
            saved['res_w'] = cmds.getAttr('defaultResolution.width')
            saved['res_h'] = cmds.getAttr('defaultResolution.height')
            saved['res_dar'] = cmds.getAttr('defaultResolution.deviceAspectRatio')
            saved['res_pa'] = cmds.getAttr('defaultResolution.pixelAspect')

            saved['vr_w'] = saved['vr_h'] = saved['vr_prefix'] = None
            saved['vr_animType'] = None
            if cmds.objExists('vraySettings'):
                saved['vr_w'] = cmds.getAttr('vraySettings.width')
                saved['vr_h'] = cmds.getAttr('vraySettings.height')
                saved['vr_prefix'] = cmds.getAttr('vraySettings.fileNamePrefix') or ""
                try:
                    saved['vr_animType'] = cmds.getAttr('vraySettings.animType')
                except Exception:
                    pass

            saved['fmt'] = cmds.getAttr('defaultRenderGlobals.imageFormat')
            saved['prefix'] = cmds.getAttr('defaultRenderGlobals.imageFilePrefix') or ""
            saved['animation'] = cmds.getAttr('defaultRenderGlobals.animation')
            saved['startFrame'] = cmds.getAttr('defaultRenderGlobals.startFrame')
            saved['endFrame'] = cmds.getAttr('defaultRenderGlobals.endFrame')
            saved['byFrameStep'] = cmds.getAttr('defaultRenderGlobals.byFrameStep')
            saved['extPad'] = cmds.getAttr('defaultRenderGlobals.extensionPadding')
            saved['putFrame'] = cmds.getAttr('defaultRenderGlobals.putFrameBeforeExt')
            saved['periodExt'] = cmds.getAttr('defaultRenderGlobals.periodInExt')

            saved['ai_fmt'] = ""
            if cmds.objExists('defaultArnoldDriver.ai_translator'):
                saved['ai_fmt'] = cmds.getAttr('defaultArnoldDriver.ai_translator')
            elif cmds.objExists('defaultArnoldDriver.aiTranslator'):
                saved['ai_fmt'] = cmds.getAttr('defaultArnoldDriver.aiTranslator')

            # ── 设缩略图尺寸（方形像素） ──
            cmds.setAttr('defaultResolution.width', 512)
            cmds.setAttr('defaultResolution.height', 512)
            cmds.setAttr('defaultResolution.deviceAspectRatio', 1)
            cmds.setAttr('defaultResolution.pixelAspect', 1)
            if cmds.objExists('vraySettings'):
                cmds.setAttr('vraySettings.width', 512)
                cmds.setAttr('vraySettings.height', 512)
                cmds.setAttr('vraySettings.fileNamePrefix', tmp_dir.replace('\\', '/') + '/thumb_sicon', type='string')

            # ── 文件名和格式 ──
            cmds.setAttr('defaultRenderGlobals.imageFormat', 32)  # PNG
            cmds.setAttr('defaultRenderGlobals.imageFilePrefix', 'thumb_sicon', type='string')
            cmds.setAttr('defaultRenderGlobals.putFrameBeforeExt', 1)
            cmds.setAttr('defaultRenderGlobals.periodInExt', 1)
            cmds.setAttr('defaultRenderGlobals.extensionPadding', 4)
            if cmds.objExists('defaultArnoldDriver.ai_translator'):
                cmds.setAttr('defaultArnoldDriver.ai_translator', 'png', type='string')
            elif cmds.objExists('defaultArnoldDriver.aiTranslator'):
                cmds.setAttr('defaultArnoldDriver.aiTranslator', 'png', type='string')

            # ── preScale ──
            try:
                cmds.setAttr('perspShape.preScale', 2)
            except Exception:
                pass

            try:
                cmds.refresh()
            except Exception:
                pass

            # ── 重定向输出到 tmp_dir ──
            ws_root = cmds.workspace(query=True, rootDirectory=True) or ""
            old_images = os.path.join(ws_root, "images") if ws_root else ""
            cmds.workspace(fileRule=['images', tmp_dir])

            # ── 渲染 ──
            cur_frame = int(cmds.currentTime(query=True))
            ani_mode = getattr(config, 'ani_frame_mode', 'current')
            is_sequence = ani_mode in ('timeline', 'keyframe')
            print(f"[Render] ani_frame_mode={ani_mode}")

            if is_sequence:
                if ani_mode == 'keyframe':
                    kf_start, kf_end = ExportOrchestrator.get_keyframe_range(config.associated_objects or [])
                    if kf_start < kf_end:
                        start, end = kf_start, kf_end
                    else:
                        start = end = cur_frame
                else:
                    start = int(cmds.playbackOptions(query=True, min=True))
                    end = int(cmds.playbackOptions(query=True, max=True))
                total = end - start + 1

                time_unit = cmds.currentUnit(query=True, time=True)
                fps_map = {'game': 15, 'film': 24, 'pal': 25, 'ntsc': 30,
                           'show': 48, 'palf': 50, 'ntscf': 60}
                scene_fps = fps_map.get(time_unit, 24)

                cmds.setAttr('defaultRenderGlobals.animation', 1)
                cmds.setAttr('defaultRenderGlobals.startFrame', start)
                cmds.setAttr('defaultRenderGlobals.endFrame', end)
                cmds.setAttr('defaultRenderGlobals.byFrameStep', 1)

                if cmds.objExists('vraySettings'):
                    try:
                        cmds.setAttr('vraySettings.animType', 1)
                    except Exception:
                        pass

                print(f"[Render] 时间轴渲染: {start}~{end} (共{total}帧, {scene_fps}fps)")
                cmds.RenderSequence(startFrame=start, endFrame=end, renderAll=False)
                cmds.currentTime(cur_frame)

                # ── 收集渲染输出 ──
                img_exts = ('.png', '.exr', '.jpg', '.tif', '.tga')
                frame_files = []
                for ext in img_exts:
                    frame_files.extend(_glob.glob(os.path.join(tmp_dir, "**", f"*{ext}"), recursive=True))
                frame_files = sorted(set(frame_files))

                project_images = os.path.join(ws_root, "images") if ws_root else ""
                if project_images and os.path.isdir(project_images):
                    for ext in img_exts:
                        for p in _glob.glob(os.path.join(project_images, "**", f"thumb_*{ext}"), recursive=True):
                            if p not in frame_files:
                                frame_files.append(p)

                if not frame_files:
                    diag = []
                    for root, dirs, files in os.walk(tmp_dir):
                        for fn in files:
                            diag.append(os.path.join(root, fn))
                    print(f"[Render] tmp_dir 文件 ({len(diag)}个): {diag[:30]}")
                    if project_images and os.path.isdir(project_images):
                        pi = []
                        for root, dirs, files in os.walk(project_images):
                            for fn in files:
                                if 'thumb' in fn.lower():
                                    pi.append(os.path.join(root, fn))
                        print(f"[Render] images 中 thumb 文件 ({len(pi)}个): {pi[:20]}")

                print(f"[Render] 帧文件数: {len(frame_files)}")

                if len(frame_files) > 1:
                    ffmpeg = ExportOrchestrator._find_ffmpeg()
                    if ffmpeg:
                        mp4_path = os.path.join(asset_dir, "thumb.mp4")
                        list_txt = os.path.join(tmp_dir, "frames.txt")
                        with open(list_txt, 'w') as lf:
                            for fp in frame_files:
                                lf.write(f"file '{fp.replace(os.sep, '/')}'\n")
                        result = subprocess.run([
                            ffmpeg, '-y',
                            '-r', str(int(scene_fps)),
                            '-f', 'concat', '-safe', '0',
                            '-i', list_txt,
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                            '-crf', '12', '-preset', 'fast',
                            mp4_path,
                        ], capture_output=True, timeout=120)
                        if result.returncode != 0:
                            print(f"[Render] ffmpeg错误: {result.stderr.decode('utf-8', errors='replace')[-500:]}")
                        if os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 100:
                            shutil.copy2(frame_files[0], sicon_path)
                            print(f"[Render] MP4: {mp4_path} ({len(frame_files)}帧) + 首帧")
                            result_path = mp4_path
                        else:
                            shutil.copy2(frame_files[0], sicon_path)
                            print(f"[Render] 首帧: {sicon_path}")
                            result_path = sicon_path
                    else:
                        shutil.copy2(frame_files[0], sicon_path)
                        print(f"[Render] 首帧(无ffmpeg): {sicon_path}")
                        result_path = sicon_path
                elif frame_files:
                    shutil.copy2(frame_files[0], sicon_path)
                    print(f"[Render] {sicon_path}")
                    result_path = sicon_path
                else:
                    print("[Render] 未找到输出文件")

            else:
                # 单帧渲染
                cmds.setAttr('defaultRenderGlobals.animation', 0)
                cmds.RenderSequence(startFrame=cur_frame, endFrame=cur_frame, renderAll=False)

                src = None
                img_exts = ('.png', '.exr', '.jpg', '.tif', '.tga')
                for ext in img_exts:
                    for name_pat in (f"thumb_sicon{ext}", f"thumb_sicon.{cur_frame:04d}{ext}",
                                      f"thumb_sicon_1{ext}", f"thumb_sicon_1.{ext}"):
                        for base in (tmp_dir, os.path.join(tmp_dir, "tmp")):
                            p = os.path.join(base, name_pat)
                            if os.path.isfile(p):
                                src = p
                                break
                        if src:
                            break
                    if src:
                        break

                if not src:
                    all_candidates = []
                    for ext in img_exts:
                        all_candidates.extend(_glob.glob(os.path.join(tmp_dir, "**", f"*{ext}"), recursive=True))
                    all_candidates.sort(key=os.path.getmtime, reverse=True)
                    if all_candidates:
                        src = all_candidates[0]

                if src:
                    if src.lower().endswith('.exr'):
                        ffmpeg = ExportOrchestrator._find_ffmpeg()
                        if ffmpeg:
                            subprocess.run([ffmpeg, '-y', '-i', src, sicon_path], capture_output=True, timeout=15)
                    else:
                        shutil.copy2(src, sicon_path)
                    if os.path.isfile(sicon_path):
                        print(f"[Render] {sicon_path}")
                        result_path = sicon_path
                else:
                    print("[Render] 未找到输出文件")

        except Exception as e:
            print(f"[Render] 失败: {e}")
            import traceback; traceback.print_exc()

        finally:
            # ── 始终恢复渲染设置 ──
            try:
                if 'res_w' in saved:
                    cmds.setAttr('defaultResolution.width', saved['res_w'])
                    cmds.setAttr('defaultResolution.height', saved['res_h'])
                    cmds.setAttr('defaultResolution.deviceAspectRatio', saved['res_dar'])
                    cmds.setAttr('defaultResolution.pixelAspect', saved['res_pa'])

                if saved.get('vr_w') is not None and cmds.objExists('vraySettings'):
                    cmds.setAttr('vraySettings.width', saved['vr_w'])
                    cmds.setAttr('vraySettings.height', saved['vr_h'])
                    cmds.setAttr('vraySettings.fileNamePrefix', saved['vr_prefix'], type='string')
                    if saved.get('vr_animType') is not None:
                        try:
                            cmds.setAttr('vraySettings.animType', saved['vr_animType'])
                        except Exception:
                            pass

                if 'fmt' in saved:
                    cmds.setAttr('defaultRenderGlobals.imageFormat', saved['fmt'])
                    cmds.setAttr('defaultRenderGlobals.imageFilePrefix', saved['prefix'], type='string')
                    cmds.setAttr('defaultRenderGlobals.animation', saved['animation'])
                    cmds.setAttr('defaultRenderGlobals.startFrame', saved['startFrame'])
                    cmds.setAttr('defaultRenderGlobals.endFrame', saved['endFrame'])
                    cmds.setAttr('defaultRenderGlobals.byFrameStep', saved['byFrameStep'])
                    cmds.setAttr('defaultRenderGlobals.extensionPadding', saved['extPad'])
                    cmds.setAttr('defaultRenderGlobals.putFrameBeforeExt', saved['putFrame'])
                    cmds.setAttr('defaultRenderGlobals.periodInExt', saved['periodExt'])

                if saved.get('ai_fmt'):
                    if cmds.objExists('defaultArnoldDriver.ai_translator'):
                        cmds.setAttr('defaultArnoldDriver.ai_translator', saved['ai_fmt'], type='string')
                    elif cmds.objExists('defaultArnoldDriver.aiTranslator'):
                        cmds.setAttr('defaultArnoldDriver.aiTranslator', saved['ai_fmt'], type='string')

                if old_images:
                    cmds.workspace(fileRule=['images', old_images])

                try:
                    cmds.setAttr('perspShape.preScale', 1)
                except Exception:
                    pass
            except Exception:
                pass

            # ── 恢复物体可见性（排除灯光，避免影响渲染） ──
            for obj, was_hid in was_hidden.items():
                if was_hid and cmds.objExists(obj):
                    try:
                        shapes = cmds.listRelatives(obj, shapes=True) or []
                        is_light = any(cmds.nodeType(s, inherited=True) == 'light' for s in shapes)
                        if not is_light:
                            cmds.setAttr(obj + '.visibility', False)
                    except Exception:
                        pass

            # ── 清理临时目录 ──
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        return result_path

    @staticmethod
    def get_keyframe_range(objects: list) -> tuple:
        """从物体列表获取关键帧范围 (start, end)，无关键帧返回 (0, 0)"""
        try:
            import maya.cmds as cmds
        except ImportError:
            return (0, 0)

        if not objects:
            return (0, 0)

        all_times = set()
        for obj in objects:
            try:
                anim_curves = cmds.listConnections(obj, type='animCurve') or []
                for curve in anim_curves:
                    key_times = cmds.keyframe(curve, query=True, timeChange=True) or []
                    all_times.update(int(t) for t in key_times)
            except Exception:
                pass

        if not all_times:
            return (0, 0)

        return (min(all_times), max(all_times))

    @staticmethod
    def create_dome_light() -> str:
        """根据当前渲染器创建 dome 灯并连接 HDR 贴图，返回创建的灯光节点名"""
        try:
            import maya.cmds as cmds
        except ImportError:
            return ""

        try:
            renderer = cmds.getAttr('defaultRenderGlobals.currentRenderer') if cmds.objExists('defaultRenderGlobals') else ''
            renderer = renderer.lower()

            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            hdr_path = os.path.join(plugin_root, 'Assets', 'IBL', 'Interior1_Neutral.exr').replace("\\", "/")
            if not os.path.isfile(hdr_path):
                hdr_path = ""

            if renderer == 'vray':
                dome_transform = cmds.createNode('transform', name='VRayDomeLight')
                dome_shape = cmds.createNode('VRayLightDomeShape', name='VRayDomeLightShape', parent=dome_transform)
                cmds.setAttr(dome_shape + '.intensityMult', 1)
                if hdr_path:
                    try:
                        tex = cmds.shadingNode('file', asTexture=True, name='vrayHDR_file')
                        _set_maya_string_attr(tex + '.fileTextureName', hdr_path)
                        _set_maya_string_attr(tex + '.colorSpace', 'Raw')
                        env_tex = cmds.createNode('VRayPlaceEnvTex', name='VRayPlaceEnvTex1')
                        cmds.setAttr(env_tex + '.horRotation', 245)
                        cmds.setAttr(env_tex + '.mappingType', 2)
                        cmds.connectAttr(env_tex + '.outUV', tex + '.uvCoord', force=True)
                        cmds.connectAttr(tex + '.outColor', dome_shape + '.domeTex', force=True)
                        cmds.setAttr(dome_shape + '.useDomeTex', 1)
                        cmds.dgdirty(dome_shape)
                        cmds.dgdirty(tex)
                    except Exception as e:
                        print(f"[Light] V-Ray dome HDR 失败: {e}")
                cmds.setAttr(dome_transform + '.visibility', lock=True)
                cmds.setAttr(dome_shape + '.visibility', lock=True)
                cmds.select(dome_transform, replace=True)
                print(f"[Light] V-Ray dome 灯已创建: {dome_transform}")
                return dome_transform

            elif renderer == 'arnold':
                dome_xform = cmds.shadingNode('aiSkyDomeLight', asLight=True)
                dome_shape = cmds.listRelatives(dome_xform, shapes=True)[0]
                cmds.setAttr(dome_xform + '.rotateY', 35)
                cmds.setAttr(dome_shape + '.intensity', 1.0)
                cmds.setAttr(dome_shape + '.camera', 1.0)
                if hdr_path:
                    try:
                        file_node = cmds.shadingNode('file', asTexture=True)
                        _set_maya_string_attr(file_node + '.fileTextureName', hdr_path)
                        _set_maya_string_attr(file_node + '.colorSpace', 'Raw')
                        cmds.connectAttr(file_node + '.outColor', dome_shape + '.color', force=True)
                    except Exception as e:
                        print(f"[Light] Arnold dome HDR 失败: {e}")
                cmds.setAttr(dome_xform + '.visibility', lock=True)
                cmds.setAttr(dome_shape + '.visibility', lock=True)
                cmds.select(dome_xform, replace=True)
                print(f"[Light] Arnold dome 灯已创建: {dome_xform}")
                return dome_xform

            elif renderer == 'redshift':
                dome_xform = cmds.shadingNode('RedshiftDomeLight', asLight=True)
                dome_shape = cmds.listRelatives(dome_xform, shapes=True)[0]
                try:
                    cmds.setAttr(dome_shape + '.intensityMult', 2)
                except Exception:
                    try:
                        cmds.setAttr(dome_shape + '.intensity', 2)
                    except Exception:
                        pass
                if hdr_path:
                    try:
                        _set_maya_string_attr(dome_shape + '.tex0', hdr_path)
                    except Exception as e:
                        print(f"[Light] Redshift dome HDR 失败: {e}")
                cmds.setAttr(dome_xform + '.visibility', lock=True)
                cmds.setAttr(dome_shape + '.visibility', lock=True)
                cmds.select(dome_xform, replace=True)
                print(f"[Light] Redshift dome 灯已创建: {dome_xform}")
                return dome_xform

            else:
                print(f"[Light] 当前渲染器 '{renderer}' 不支持自动创建 dome 灯")
                return ""

        except Exception as e:
            print(f"[Light] 创建 dome 灯失败: {e}")
            import traceback; traceback.print_exc()
            return ""

    # ── Stage 6: 代理 ────────────────────────────────────────

    def _stage_proxy(self, config: ExportConfig, asset_dir: str, safe_name: str) -> List[str]:
        """导出渲染器代理格式。

        支持: Arnold .ass, V-Ray .vrscene, Redshift .rs

        Returns:
            生成的代理文件路径列表
        """
        files: List[str] = []
        if not _IN_MAYA or not config.proxy_formats:
            return files

        from squirrel_asset_manager.core.proxy_registry import ProxyFormatRegistry
        from squirrel_asset_manager.utils.maya_plugin_checker import PluginStatus

        for fmt_key in config.proxy_formats:
            entry = ProxyFormatRegistry.get(fmt_key)
            if entry is None or not entry.enabled:
                continue

            # 检查插件
            status = ProxyFormatRegistry.check_plugin(fmt_key)
            if status != PluginStatus.LOADED:
                continue

            proxy_path = os.path.join(asset_dir, f"{safe_name}{entry.extension}")

            try:
                # ass/rs/vrmesh 动画序列
                if fmt_key in ("arnold", "redshift", "vrmesh", "vray") and config.ani_frame_mode in ("timeline", "keyframe"):
                    if config.ani_frame_mode == "keyframe":
                        kf_start, kf_end = ExportOrchestrator.get_keyframe_range(config.associated_objects or [])
                        if kf_start < kf_end:
                            min_t, max_t = kf_start, kf_end
                        else:
                            min_t = max_t = int(cmds.currentTime(q=True))
                    else:
                        min_t = int(cmds.playbackOptions(q=True, minTime=True))
                        max_t = int(cmds.playbackOptions(q=True, maxTime=True))
                    if fmt_key == "vrmesh":
                        # V-Ray 代理用 -animOn 一次导出
                        cmds.vrayCreateProxy(
                            exportType=1, dir=asset_dir, fname=safe_name,
                            previewFaces=1000, overwrite=True,
                            animOn=True, animType=3,
                            startFrame=min_t, endFrame=max_t,
                        )
                        # 检查实际文件（vrayCreateProxy 不加 .vrmesh 后缀）
                        no_ext = os.path.join(asset_dir, safe_name)
                        vrmesh_path = os.path.join(asset_dir, f"{safe_name}.vrmesh")
                        if os.path.isfile(no_ext):
                            os.rename(no_ext, vrmesh_path)
                        if os.path.isfile(vrmesh_path):
                            files.append(vrmesh_path)
                    else:
                        sub = "ass" if fmt_key == "arnold" else "rs"
                        seq_dir = os.path.join(asset_dir, sub)
                        os.makedirs(seq_dir, exist_ok=True)
                        ext = entry.extension
                        if fmt_key == "vray":
                            self._export_vray_vrscene(proxy_path, min_t, max_t)
                            if os.path.isfile(proxy_path):
                                files.append(proxy_path)
                        else:
                            print(f"[Export] {fmt_key} 时间轴: safe_name={safe_name}, frame={min_t}~{max_t}, dir={seq_dir}")
                            for frame in range(min_t, max_t + 1):
                                cmds.currentTime(frame)
                                fp = os.path.join(seq_dir, f"{safe_name}_{frame:04d}{ext}")
                                if fmt_key == "arnold":
                                    self._export_arnold_ass(fp)
                                elif fmt_key == "redshift":
                                    self._export_redshift_proxy(fp)
                                if os.path.isfile(fp):
                                    files.append(fp)
                                    if frame == min_t:
                                        print(f"[Export] rs 第一帧文件: {fp}")
                    continue

                if fmt_key == "arnold":
                    self._export_arnold_ass(proxy_path)
                elif fmt_key == "vray":
                    self._export_vray_vrscene(proxy_path)
                elif fmt_key == "vrmesh":
                    actual = self._export_vray_vrmesh(proxy_path)
                    if actual:
                        files.append(actual)
                    continue  # vrmesh 自行处理了文件检查
                elif fmt_key == "redshift":
                    self._export_redshift_proxy(proxy_path)
                else:
                    continue

                if os.path.isfile(proxy_path):
                    files.append(proxy_path)
            except Exception:
                pass

        return files

    def _export_arnold_ass(self, output_path: str) -> None:
        """导出 Arnold .ass 代理。"""
        if not _IN_MAYA:
            return
        try:
            cmds.file(output_path, force=True,
                      options="-boundingBox;-mask 14591;-lightLinks 1;-shadowLinks 1;-fullPath",
                      typ="ASS Export",
                      pr=True,  # pipeline render
                      es=True)  # export selected
        except Exception as e:
            print(f"[Export] Arnold .ass 导出失败: {e}")
            # 回退到 arnoldExportAss
            try:
                cmds.arnoldExportAss(filename=output_path, selected=True)
            except Exception:
                pass

    def _export_vray_vrscene(self, output_path: str,
                              anim_start: Optional[int] = None,
                              anim_end: Optional[int] = None) -> None:
        """导出 V-Ray .vrscene 场景。

        Args:
            output_path: 输出文件路径
            anim_start: 动画起始帧（None=当前帧）
            anim_end: 动画结束帧（None=当前帧）
        """
        if not _IN_MAYA:
            return
        try:
            opts = ""
            if anim_start is not None and anim_end is not None:
                opts = f"-range={anim_start}-{anim_end}"
            cmds.file(output_path, force=True, options=opts,
                      typ="V-Ray Scene", pr=True, es=True)
        except Exception as e:
            print(f"[Export] V-Ray .vrscene 导出失败: {e}")

    def _export_vray_vrmesh(self, output_path: str) -> str:
        """导出 V-Ray .vrmesh 代理（vrayCreateProxy），返回实际文件路径。"""
        if not _IN_MAYA:
            return ""
        try:
            out_dir = os.path.dirname(output_path)
            base = os.path.splitext(os.path.basename(output_path))[0]
            cmds.vrayCreateProxy(
                exportType=1, dir=out_dir, fname=base,
                previewFaces=1000, overwrite=True,
            )
            # vrayCreateProxy 不加 .vrmesh 后缀，手动补上
            no_ext = os.path.join(out_dir, base)
            with_ext = os.path.join(out_dir, f"{base}.vrmesh")
            if os.path.isfile(no_ext):
                os.rename(no_ext, with_ext)
                print(f"[Export] .vrmesh 添加后缀: {no_ext} → {with_ext}")
            if os.path.isfile(with_ext):
                return with_ext
            return ""
        except Exception as e:
            print(f"[Export] V-Ray .vrmesh 导出失败: {e}")
            return ""


    def _export_redshift_proxy(self, output_path: str) -> None:
        """导出 Redshift .rs 代理。"""
        if not _IN_MAYA:
            return
        try:
            import maya.cmds as cmds
            sel = cmds.ls(selection=True)
            if sel:
                cmds.rsProxy(fp=output_path, sl=True)
        except Exception as e:
            print(f"[Export] Redshift .rs 导出失败: {e}")
        except Exception:
            try:
                # 备选 API 签名
                import maya.cmds as cmds
                sel = cmds.ls(selection=True)
                if sel:
                    cmds.rsProxy(export=output_path)
            except Exception:
                pass

    # ── 辅助方法 ──────────────────────────────────────────────

    def _sync_ma_texture_paths(self, ma_path: str, path_map: dict) -> None:
        """将 .ma 文件中的 fileTextureName 更新为 textures/{材质名}/{文件名}。

        Args:
            ma_path: .ma 文件路径
            path_map: {原始绝对路径: textures/{材质名}/{文件名}} 映射
        """
        if not os.path.isfile(ma_path) or not path_map:
            return
        try:
            import re
            with open(ma_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # 归一化 .ma 中所有路径的反斜杠为斜杠，确保 regex 匹配
            content = re.sub(r'"[^"]*:[\\/][^"]*"', lambda m: m.group(0).replace("\\", "/"), content)
            changed = 0
            for orig_path, rel_path in path_map.items():
                norm_path = orig_path.replace("\\", "/")
                escaped = re.escape(norm_path)
                pattern = fr'"[^"]*{escaped}[^"]*"'
                n = len(re.findall(pattern, content))
                if n:
                    content = re.sub(pattern, lambda m, p=rel_path: f'"{p}"', content)
                    changed += n
            if changed:
                with open(ma_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"[MaSync] 已更新 {changed} 个纹理路径: {os.path.basename(ma_path)}")
        except Exception as e:
            print(f"[MaSync] 更新失败 {ma_path}: {e}")

    @staticmethod
    def _sync_zmetal_texture_paths(zmetal_path: str, path_map: dict) -> None:
        """将 .zmetal JSON 中的 fileTextureName 更新为 textures/{材质名}/{文件名}。

        Args:
            zmetal_path: .zmetal 文件路径
            path_map: {原始绝对路径: textures/{材质名}/{文件名}} 映射
        """
        if not os.path.isfile(zmetal_path) or not path_map:
            return

        try:
            import json
            with open(zmetal_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            nodes = data.get("nodes", {})
            changed = 0
            for node_name, info in nodes.items():
                if info.get("node_type") != "file":
                    continue
                ftn = info.get("attrs", {}).get("fileTextureName", {})
                if ftn.get("type") != "value":
                    continue
                old_val = ftn.get("value", "").replace("\\", "/")
                if old_val in path_map:
                    ftn["value"] = path_map[old_val]
                    changed += 1

            if changed:
                with open(zmetal_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                print(f"[ZmetalSync] 已更新 {changed} 个文件节点的贴图路径")
        except Exception as e:
            print(f"[ZmetalSync] 更新失败: {e}")

    def _count_materials(self, config: ExportConfig) -> int:
        """统计关联的材质数量。"""
        if not _IN_MAYA:
            return 1
        try:
            from squirrel_asset_manager.utils.maya_utils import get_maya_materials_from_selection
            cmds.select(config.associated_objects or [], replace=True)
            return len(get_maya_materials_from_selection())
        except Exception:
            return 1

    @staticmethod
    def _get_materials_from_objects(objects: List[str]) -> List[str]:
        """从物体列表提取材质节点名。"""
        if not _IN_MAYA:
            return []
        mats = set()
        for obj in objects:
            try:
                if not cmds.objExists(obj):
                    continue
                shapes = cmds.listRelatives(obj, shapes=True, fullPath=False) or [obj]
                for shape in shapes:
                    ses = cmds.listConnections(shape, type='shadingEngine') or []
                    for se in ses:
                        if se == 'initialShadingGroup':
                            continue
                        mat_list = cmds.listConnections(f"{se}.surfaceShader") or []
                        mats.update(mat_list)
            except Exception:
                continue
        return list(mats)

    # ── ExportConfig 工厂方法 ────────────────────────────────

    @classmethod
    def from_defaults(
        cls,
        asset_name: str,
        target_dir: str,
        category: str = "",
        name_cn: str = "",
        tags: Optional[List[str]] = None,
        material_node: Optional[str] = None,
        associated_objects: Optional[List[str]] = None,
        asset_type: str = "materials",
    ) -> ExportConfig:
        """从 export_preset.json 默认值创建 ExportConfig。

        Args:
            asset_name: 资产名
            target_dir: 导出目标目录
            category: 分类
            name_cn: 中文名
            tags: 标签
            material_node: Maya 材质节点
            associated_objects: 关联物体
            asset_type: 资产类型（materials/models/lights/textures/scenes/hdr/ani）

        Returns:
            预填充了配置默认值的 ExportConfig
        """
        config = ExportConfig(
            asset_name=asset_name,
            name_cn=name_cn or asset_name,
            category=category,
            tags=list(tags or []),
            target_dir=target_dir,
            material_node=material_node,
            associated_objects=list(associated_objects or []),
        )

        # 读取 export_preset.json 的默认值
        _preset_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "Assets", "preset", "export_preset.json",
        )
        try:
            import json
            with open(_preset_path, 'r', encoding='utf-8') as f:
                preset = json.load(f)
        except Exception:
            preset = {}

        defaults = preset.get(asset_type, preset.get("materials", {}))
        config.export_zmetal = defaults.get("zmetal", True)
        config.export_ma = defaults.get("ma", False)
        config.export_mb = defaults.get("mb", False)
        config.export_fbx = defaults.get("fbx", False)
        config.export_obj = defaults.get("obj", False)
        config.export_usd = defaults.get("usd", False)
        config.export_glb = defaults.get("glb", False)
        config.export_abc = defaults.get("abc", False)

        # 代理格式
        proxy_list: List[str] = []
        if defaults.get("arnold", False):
            proxy_list.append("arnold")
        if defaults.get("vray", False):
            proxy_list.append("vray")
        if defaults.get("redshift", False):
            proxy_list.append("redshift")
        if defaults.get("vrmesh", False):
            proxy_list.append("vrmesh")
        config.proxy_formats = proxy_list

        config.delay_ms = defaults.get("delay_ms", 2000)

        return config


# ═══════════════════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 确保包导入路径（直接运行时 core/ 不在 squirrel_asset_manager 下）
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(_script_dir))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    print("=" * 50)
    print("ExportOrchestrator 自测")
    print("=" * 50)

    passed = 0
    failed = 0

    def check(condition, label):
        global passed, failed
        if condition:
            print(f"  ✓ {label}")
            passed += 1
        else:
            print(f"  ✗ {label}")
            failed += 1

    # ── 数据类 ──
    print("\n[数据类]")
    cfg = ExportConfig(asset_name="TestMat", target_dir="/tmp/test")
    check(cfg.asset_name == "TestMat", "ExportConfig 实例化")
    check(cfg.export_zmetal is True, "export_zmetal 默认 True")
    check(cfg.export_fbx is False, "export_fbx 默认 False")
    check(cfg.delay_ms == 0, "delay_ms 默认 0")

    result = ExportResult(asset_name="Test", success=True)
    check(result.success is True, "ExportResult 实例化")
    check(result.files == [], "ExportResult.files 默认空列表")

    summary = BatchSummary(total=3, success_count=2, failed_count=1)
    check(summary.has_failures() is True, "BatchSummary.has_failures()")
    check(summary.total == 3, "BatchSummary.total")

    # ── sanitize_filename ──
    print("\n[sanitize_filename]")
    check(
        ExportOrchestrator._sanitize_filename("Jeep:Mat/Test|Node") == "Jeep_Mat_Test_Node",
        "Jeep:Mat/Test|Node → Jeep_Mat_Test_Node",
    )
    check(
        ExportOrchestrator._sanitize_filename("simple_name") == "simple_name",
        "simple_name 保持不变",
    )
    check(
        ExportOrchestrator._sanitize_filename("a:b/c|d<e>f\"g*h?i") == "a_b_c_d_e_f_g_h_i",
        "多条非法字符替换",
    )
    check(
        ExportOrchestrator._sanitize_filename("") == "unnamed",
        "空字符串 → unnamed",
    )
    check(
        ExportOrchestrator._sanitize_filename("____test____") == "test",
        "首尾下划线去除",
    )

    # ── ViewportState ──
    print("\n[ViewportState]")
    vs = ViewportState()
    check(isinstance(vs, ViewportState), "ViewportState 实例化")
    # 非 Maya 环境测试上下文管理器不崩溃
    try:
        with ViewportState() as v:
            pass
        check(True, "ViewportState 上下文管理器（非Maya环境无崩溃）")
    except Exception as e:
        check(False, f"ViewportState 上下文管理器崩溃: {e}")

    # ── ExportOrchestrator ──
    print("\n[ExportOrchestrator]")
    orch = ExportOrchestrator("/tmp/test_library")
    check(orch._base_dir.endswith("test_library"), "ExportOrchestrator 初始化")

    # 非 Maya 环境：export_single 可生成 .zasset（不含占位缩略图，缩略图由 overlay 截图写入）
    print("\n[ExportOrchestrator - 非Maya环境]")
    cfg = ExportConfig(asset_name="TestAsset", target_dir="/tmp/test_library")
    result = orch.export_single(cfg)
    check(result.success, "非Maya环境 export_single 应成功")
    check(isinstance(result, ExportResult), "返回类型为 ExportResult")
    # 验证 .zasset 输出
    zasset_path = os.path.join("/tmp/test_library", "TestAsset.zasset")
    check(os.path.isdir(zasset_path), ".zasset 文件夹已生成")
    if os.path.isdir(zasset_path):
        _Zio = None
        try:
            from squirrel_asset_manager.core.zasset_io import ZassetIO
            _Zio = ZassetIO
        except ImportError:
            try:
                from core.zasset_io import ZassetIO
                _Zio = ZassetIO
            except ImportError:
                pass
        if _Zio:
            try:
                content_meta = _Zio.read_meta(zasset_path)
                check(content_meta.get("name") == "TestAsset", ".zasset 内含正确 meta")
                check("asset_type" in content_meta, ".zasset meta 包含 asset_type")
            except Exception as e:
                check(False, f".zasset 读取失败: {e}")
        else:
            check(False, "无法导入 ZassetIO，跳过 zasset 内容验证")
    check(
        [os.path.normpath(f) for f in result.files] == [os.path.normpath(zasset_path)],
        "result.files 指向 .zasset",
    )

    # ── from_defaults 工厂 ──
    print("\n[from_defaults]")
    cfg2 = ExportOrchestrator.from_defaults(
        asset_name="MyAsset",
        target_dir="/tmp/lib",
        category="metal",
        tags=["pbr", "chrome"],
        material_node="standardSurface2",
    )
    check(cfg2.asset_name == "MyAsset", "from_defaults: asset_name")
    check(cfg2.category == "metal", "from_defaults: category")
    check("pbr" in cfg2.tags, "from_defaults: tags")

    # ── 汇总 ──
    print(f"\n{'=' * 50}")
    print(f"结果: {passed} 通过, {failed} 失败")
    print(f"{'=' * 50}")
