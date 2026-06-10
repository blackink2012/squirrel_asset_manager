# -*- coding: utf-8 -*-
"""
zasset_builder — .zasset 文件夹资产构建工具

提供统一的构建入口，导入和导出两个功能链路共用。
与 ZassetIO 的分工：
  - ZassetIO: 已有 .zasset 文件夹的读写、更新
  - ZassetBuilder: 从素材构建新的 .zasset 文件夹（导入/导出）

设计原则：
  - 直写临时文件夹 + os.replace() 原子替换
  - 峰值内存 = 最大单文件尺寸（逐个读取磁盘文件写入）
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, Optional


class ZassetBuilder:
    """构建 .zasset 文件夹的公共工具，导入导出共用。"""

    @staticmethod
    def build(
        output_path: str,
        files: Dict[str, str],     # 内部路径 → 磁盘源文件路径
        meta: dict,                # meta.json 内容
    ) -> bool:
        """构建 .zasset 文件夹。

        直写临时文件夹后 os.replace() 原子替换，
        峰值内存 = 最大单文件尺寸。

        Args:
            output_path: 输出的 .zasset 文件夹路径
            files:       内部路径 → 磁盘源文件路径 的映射。
                         内部路径示例: "node.zmetal", "node.ma",
                         "textures/diff.tex", "thumb.sicon"
            meta:        meta.json 内容（id, name, category, ...）

        Returns:
            bool: 成功返回 True
        """
        if "formats" not in meta or not meta.get("formats"):
            meta = dict(meta)
            formats = set()
            for internal_path in files:
                ext = os.path.splitext(internal_path)[1].lower().lstrip(".")
                if ext:
                    formats.add(ext)
            meta["formats"] = sorted(formats)

        tmp_path = output_path + ".tmp"
        try:
            if os.path.isdir(tmp_path):
                shutil.rmtree(tmp_path)
            os.makedirs(tmp_path, exist_ok=True)

            meta_bytes = json.dumps(meta, indent=2, ensure_ascii=False).encode('utf-8')
            with open(os.path.join(tmp_path, "meta.json"), 'wb') as f:
                f.write(meta_bytes)

            for internal_path, disk_path in files.items():
                if not os.path.isfile(disk_path):
                    print(f"[ZassetBuilder] 文件不存在，跳过: {disk_path}")
                    continue
                target = os.path.join(tmp_path, internal_path)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(disk_path, 'rb') as src:
                    with open(target, 'wb') as dst:
                        dst.write(src.read())

            if os.path.isdir(output_path):
                shutil.rmtree(output_path)
            os.rename(tmp_path, output_path)
            return True

        except Exception as e:
            print(f"[ZassetBuilder] build 失败 {output_path}: {e}")
            if os.path.isdir(tmp_path):
                try:
                    shutil.rmtree(tmp_path, ignore_errors=True)
                except OSError:
                    pass
            return False

    @staticmethod
    def build_from_folder(
        output_path: str,
        source_folder: str,
        meta: dict,
    ) -> bool:
        """从已有文件夹直接打包为 .zasset。

        扫描 source_folder 所有非隐藏文件，
        按原路径名构建 files dict → 调用 build()。

        Args:
            output_path:  输出的 .zasset 文件夹路径
            source_folder: 源文件夹路径
            meta:         meta.json 内容

        Returns:
            bool: 成功返回 True
        """
        if not os.path.isdir(source_folder):
            print(f"[ZassetBuilder] 源文件夹不存在: {source_folder}")
            return False

        files: Dict[str, str] = {}
        for root, dirs, filenames in os.walk(source_folder):
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for fname in filenames:
                if fname.startswith('.'):
                    continue
                if fname in ("meta.json", ".fmeta"):
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, source_folder)
                rel_path = rel_path.replace("\\", "/")
                files[rel_path] = full_path

        return ZassetBuilder.build(output_path, files, meta)

    # ── 变体操作 ────────────────────────────────────────

    @staticmethod
    def add_variant_lod(
        zasset_path: str,
        version: str,
        lod_level: int,
        lod_label: str,
        geom_files: Dict[str, str],  # 内部路径 → 磁盘源文件路径
        stats: Optional[Dict[str, int]] = None,
    ) -> bool:
        """向指定版本追加一个 LOD 级别。

        将 geom_files 写入 variants/{version}/lod{level}/ 目录，
        更新 variants.json 中的 lods 列表。

        Args:
            zasset_path: .zasset 文件夹路径
            version: 目标版本 id（如 "v1"）
            lod_level: LOD 级别（0最高）
            lod_label: UI 显示名（如 "高精度"）
            geom_files: 内部相对路径 → 磁盘源文件路径。
                        如 {"node.ma": "/tmp/my_lod1.ma", "node.fbx": "/tmp/my_lod1.fbx"}
            stats: 可选面数统计 {"triangles": 5020, "vertices": 3100}

        Returns:
            bool: 成功返回 True
        """
        from core.zasset_io import ZassetIO

        if not ZassetIO._exists(zasset_path):
            print(f"[ZassetBuilder] .zasset 不存在: {zasset_path}")
            return False

        variants = ZassetIO.read_variants(zasset_path)
        versions = variants.get("versions", [])

        # 查找或创建版本
        ver = None
        for v in versions:
            if v.get("id") == version:
                ver = v
                break
        if not ver:
            ver = {"id": version, "tag": version.lstrip("v"), "label": version, "lods": []}
            versions.append(ver)

        # 构建 LOD 目录
        lod_id = f"lod{lod_level}"
        base = f"variants/{version}/{lod_id}"
        os.makedirs(os.path.join(zasset_path, base), exist_ok=True)

        # 写入几何体文件
        formats = []
        for rel, disk in geom_files.items():
            if not os.path.isfile(disk):
                print(f"[ZassetBuilder] 几何体文件不存在，跳过: {disk}")
                continue
            target = os.path.join(zasset_path, base, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(disk, 'rb') as src:
                with open(target, 'wb') as dst:
                    dst.write(src.read())
            ext = os.path.splitext(rel)[1].lstrip(".")
            if ext:
                formats.append(ext)

        # 主几何体路径
        main_geom = None
        for rel in geom_files:
            if rel.startswith("node."):
                main_geom = f"{base}/{rel}"
                break
        if not main_geom and geom_files:
            first_rel = list(geom_files.keys())[0]
            main_geom = f"{base}/{first_rel}"

        # 更新 variants.json
        lod_entry = {
            "id": lod_id,
            "label": lod_label,
            "level": lod_level,
            "geometry": main_geom or f"{base}/node.ma",
            "formats": sorted(formats),
        }
        if stats:
            lod_entry["stats"] = stats

        # 替换同级别 LOD
        lods = ver.get("lods", [])
        replaced = False
        for i, l in enumerate(lods):
            if l.get("id") == lod_id:
                lods[i] = lod_entry
                replaced = True
                break
        if not replaced:
            lods.append(lod_entry)
            lods.sort(key=lambda x: x.get("level", 0))
        ver["lods"] = lods

        # 更新 default_lod（如果是第一个 LOD）
        if not variants.get("default_lod"):
            variants["default_lod"] = lod_id
        if not variants.get("default_version"):
            variants["default_version"] = version

        variants["versions"] = versions
        ZassetIO.write_variants(zasset_path, variants)

        # 更新 meta.json variant_types
        meta = ZassetIO.read_meta(zasset_path)
        if meta:
            vtypes = set(meta.get("variant_types", []))
            if "lod" not in vtypes:
                vtypes.add("lod")
                meta["variant_types"] = sorted(vtypes)
            if not meta.get("default_lod"):
                meta["default_lod"] = lod_id
            if not meta.get("default_version"):
                meta["default_version"] = version
            ZassetIO.write_meta(zasset_path, meta)

        return True

    @staticmethod
    def remove_variant_lod(
        zasset_path: str,
        version_id: str,
        lod_id: str,
    ) -> bool:
        """从指定版本中删除一个 LOD 级别。

        删除 variants/{version}/{lod}/ 目录，从 variants.json 移除该 LOD 条目。
        如果删除后版本没有 LOD 了，保留空版本（可手动删除版本）。

        Returns:
            bool: 成功返回 True
        """
        import shutil
        from core.zasset_io import ZassetIO

        if not ZassetIO._exists(zasset_path):
            return False

        variants = ZassetIO.read_variants(zasset_path)
        versions = variants.get("versions", [])

        ver = None
        for v in versions:
            if v.get("id") == version_id:
                ver = v
                break
        if not ver:
            return False

        lods = ver.get("lods", [])
        lod_found = None
        for l in lods:
            if l.get("id") == lod_id:
                lod_found = l
                break
        if not lod_found:
            return False

        # 删除磁盘文件
        lod_dir = os.path.join(zasset_path, "variants", version_id, lod_id)
        if os.path.isdir(lod_dir):
            shutil.rmtree(lod_dir, ignore_errors=True)
            print(f"[ZassetBuilder] 删除 LOD 目录: {lod_dir}")

        # 更新 variants.json
        lods.remove(lod_found)
        ver["lods"] = lods
        ZassetIO.write_variants(zasset_path, variants)

        # 更新 default_lod（如果删了默认 LOD）
        if variants.get("default_lod") == lod_id and lods:
            variants["default_lod"] = lods[0]["id"]
            ZassetIO.write_variants(zasset_path, variants)

        return True

    @staticmethod
    def remove_variant_version(
        zasset_path: str,
        version_id: str,
    ) -> bool:
        """删除整个版本，包含所有 LOD 和独立材质。

        删除 variants/{version}/ 目录，从 variants.json 移除该版本条目。
        更新 meta.json 的 variant_types。

        Returns:
            bool: 成功返回 True
        """
        import shutil
        from core.zasset_io import ZassetIO

        if not ZassetIO._exists(zasset_path):
            return False

        variants = ZassetIO.read_variants(zasset_path)
        versions = variants.get("versions", [])

        ver_found = None
        for v in versions:
            if v.get("id") == version_id:
                ver_found = v
                break
        if not ver_found:
            return False

        # 删除磁盘目录
        ver_dir = os.path.join(zasset_path, "variants", version_id)
        if os.path.isdir(ver_dir):
            shutil.rmtree(ver_dir, ignore_errors=True)
            print(f"[ZassetBuilder] 删除版本目录: {ver_dir}")

        # 更新 variants.json
        versions.remove(ver_found)
        variants["versions"] = versions

        # 更新 default_version
        if variants.get("default_version") == version_id:
            variants["default_version"] = versions[0]["id"] if versions else ""
        ZassetIO.write_variants(zasset_path, variants)

        # 更新 meta.json
        meta = ZassetIO.read_meta(zasset_path)
        if meta:
            if not versions:
                # 无版本了，清除变体类型
                meta["variant_types"] = []
                meta.pop("default_version", None)
                meta.pop("default_lod", None)
            else:
                if meta.get("default_version") == version_id:
                    meta["default_version"] = versions[0]["id"]
            ZassetIO.write_meta(zasset_path, meta)

        return True

    @staticmethod
    def add_variant_version(
        zasset_path: str,
        version_id: str,
        version_tag: str,
        label: str,
        notes: str,
        lod_files: Dict[str, str],
    ) -> bool:
        """创建新版本（初版仅 lod0）。

        在 variants/{version_id}/lod0/ 下创建几何体，
        新增版本条目到 variants.json。
        """
        from datetime import date
        from core.zasset_io import ZassetIO

        # 先创建 lod0
        ok = ZassetBuilder.add_variant_lod(
            zasset_path=zasset_path,
            version=version_id,
            lod_level=0,
            lod_label="高精度",
            geom_files=lod_files,
        )
        if not ok:
            return False

        # 追加版本元数据
        variants = ZassetIO.read_variants(zasset_path)
        versions = variants.get("versions", [])
        for v in versions:
            if v.get("id") == version_id:
                v["tag"] = version_tag
                v["label"] = label
                v["notes"] = notes
                v["create_date"] = date.today().isoformat()
                break

        ZassetIO.write_variants(zasset_path, variants)

        # 更新 meta.json
        meta = ZassetIO.read_meta(zasset_path)
        if meta:
            vtypes = set(meta.get("variant_types", []))
            if "version" not in vtypes:
                vtypes.add("version")
                meta["variant_types"] = sorted(vtypes)
            ZassetIO.write_meta(zasset_path, meta)

        return True
