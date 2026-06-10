# -*- coding: utf-8 -*-
"""系统目录 → 外部文件/文件夹导入资产"""

import os
import uuid
from ..core.zasset_builder import ZassetBuilder
from ..core.zasset_io import ZassetIO


IMPORTABLE_EXTS = {
    ".ma", ".mb", ".fbx", ".obj", ".abc", ".usd", ".usda", ".usdc",
    ".glb", ".gltf", ".dae", ".ass", ".rs", ".vrscene", ".vrmesh",
    ".png", ".jpg", ".jpeg", ".exr", ".tga", ".hdr", ".tif", ".tiff", ".bmp",
}


def import_external_files(file_paths, mgr, cat_id):
    """导入选定文件为独立 .zasset 资产"""
    if not file_paths:
        return
    base_path = _get_base_path(mgr, cat_id)
    os.makedirs(base_path, exist_ok=True)

    for fpath in file_paths:
        name = os.path.splitext(os.path.basename(fpath))[0]
        target_path = mgr._resolve_zasset_path(base_path, name)
        final_name = os.path.splitext(os.path.basename(target_path))[0]

        asset_id = str(uuid.uuid4())
        ext = os.path.splitext(fpath)[1].lower().lstrip(".")
        # 构建 files_dict
        files_dict = {f"node.{ext}": fpath}
        meta = {
            "name": final_name, "id": asset_id,
            "formats": [ext], "type": _detect_asset_type(ext),
        }
        # 缩略图：用占位
        from ..core.export_orchestrator import ExportOrchestrator
        thumb_path = ExportOrchestrator._generate_placeholder_thumbnail(
            os.path.dirname(target_path), final_name
        )
        ZassetBuilder.build(target_path, files_dict, meta)
        if thumb_path and os.path.isfile(thumb_path):
            with open(thumb_path, 'rb') as f:
                ZassetIO.write_thumbnail(target_path, f.read())


def import_external_folder(folder_path, mgr, cat_id):
    """导入文件夹为 .zasset（子文件夹各自独立）
    
    自动检测 PBR 贴图文件夹，支持多精度、缩略图、元数据。
    非 PBR 文件夹回退到简单打包。
    """
    if not os.path.isdir(folder_path):
        return
    base_path = _get_base_path(mgr, cat_id)
    os.makedirs(base_path, exist_ok=True)

    name = os.path.basename(folder_path)
    target_path = mgr._resolve_zasset_path(base_path, name)
    final_name = os.path.splitext(os.path.basename(target_path))[0]
    asset_id = str(uuid.uuid4())

    # ── 尝试 PBR 纹理识别 ──
    import json
    pbr_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   'Assets', 'preset', 'pbr_mapping.json')
    pbr_config = {}
    if os.path.exists(pbr_config_path):
        with open(pbr_config_path, 'r', encoding='utf-8') as f:
            pbr_config = json.load(f)

    from ..quicktools.pbr_to_zasset import (
        scan_textures_resolution_aware,
        find_existing_thumbnail,
        read_source_metadata,
        get_routable_extensions,
    )

    tex, res_map = scan_textures_resolution_aware(folder_path, pbr_config)

    if tex:
        # ── PBR 贴图路径：多精度打包 ──
        mat_name = list(tex.keys())[0]
        info = res_map.get(mat_name, {})
        variants = info.get('variants', {})
        resolutions = info.get('resolutions', [])

        meta = {
            "name": final_name, "id": asset_id,
            "type": "materials",
        }

        files_built = {}
        formats = set()
        color_texture_path = None

        if resolutions and variants:
            meta['resolutions'] = resolutions
            meta['default_resolution'] = info.get('default_res', resolutions[0])
            for res in resolutions:
                variant = variants.get(res, {})
                for tt, tex_info in variant.items():
                    fp = tex_info['full_path']
                    if os.path.isfile(fp):
                        files_built[f"textures/{res}/{tex_info['filename']}"] = fp
                        ext = os.path.splitext(fp)[1].lower().lstrip('.')
                        if ext:
                            formats.add(ext)
                        if color_texture_path is None:
                            base_types = ['baseColor', 'diffuse', 'albedo', 'color', 'col', 'diff']
                            bt = tt.split('_')[0] if '_' in tt else tt
                            if bt in base_types:
                                color_texture_path = fp
                    # 同类型 extras
                    for extra in tex_info.get('extras', []):
                        fp2 = extra['full_path']
                        if os.path.isfile(fp2):
                            files_built[f"textures/{res}/{extra['filename']}"] = fp2
                            ext = os.path.splitext(extra['filename'])[1].lower().lstrip('.')
                            if ext:
                                formats.add(ext)
        else:
            # 单精度
            for tt, tex_info in tex[mat_name].items():
                files_built[f"textures/{tex_info['filename']}"] = tex_info['full_path']
                ext = os.path.splitext(tex_info['filename'])[1].lower().lstrip('.')
                if ext:
                    formats.add(ext)
                if color_texture_path is None:
                    base_types = ['baseColor', 'diffuse', 'albedo', 'color', 'col', 'diff']
                    bt = tt.split('_')[0] if '_' in tt else tt
                    if bt in base_types:
                        color_texture_path = tex_info['full_path']
                # 同类型 extras
                for extra in tex_info.get('extras', []):
                    fp2 = extra['full_path']
                    if os.path.isfile(fp2):
                        files_built[f"textures/{extra['filename']}"] = fp2
                        ext = os.path.splitext(extra['filename'])[1].lower().lstrip('.')
                        if ext:
                            formats.add(ext)

        meta['formats'] = sorted(formats)

        # 缩略图搜索
        name_fallbacks = [mat_name, name]
        thumb_source = find_existing_thumbnail(folder_path, name_fallbacks, pbr_config)
        if not thumb_source:
            thumb_source = color_texture_path

        # 元数据搜索
        source_meta = read_source_metadata(folder_path, name_fallbacks, pbr_config)
        if source_meta:
            for key, value in source_meta.items():
                if key in ('tags',) and isinstance(value, list):
                    existing = meta.get('tags', [])
                    meta['tags'] = list(dict.fromkeys(existing + value))
                elif key in ('description', 'author', 'source_url'):
                    meta[key] = value
                else:
                    meta[key] = value

        # 构建
        from ..core.zasset_builder import ZassetBuilder
        ZassetBuilder.build(target_path, files_built, meta)

        # 缩略图
        if thumb_source and os.path.isfile(thumb_source):
            try:
                from PIL import Image
                import io
                with Image.open(thumb_source) as img:
                    img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    if img.mode not in ('RGB', 'RGBA'):
                        img = img.convert('RGBA')
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    ZassetIO.write_thumbnail(target_path, buf.getvalue())
            except Exception:
                pass
    else:
        # ── 非 PBR 路径：回退到简单打包 ──
        files_dict = {}
        for root, dirs, filenames in os.walk(folder_path):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMPORTABLE_EXTS:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, folder_path).replace("\\", "/")
                    files_dict[rel] = full

        if not files_dict:
            return

        fmt = set()
        for k in files_dict:
            ext = os.path.splitext(k)[1].lower().lstrip(".")
            if ext:
                fmt.add(ext)

        meta = {
            "name": final_name, "id": asset_id,
            "formats": list(fmt), "type": _detect_asset_type_list(fmt),
        }

        from ..core.export_orchestrator import ExportOrchestrator
        thumb_path = ExportOrchestrator._generate_placeholder_thumbnail(
            os.path.dirname(target_path), final_name
        )
        ZassetBuilder.build(target_path, files_dict, meta)
        if thumb_path and os.path.isfile(thumb_path):
            with open(thumb_path, 'rb') as f:
                ZassetIO.write_thumbnail(target_path, f.read())


def _get_base_path(mgr, cat_id):
    sub_lib = getattr(mgr, '_current_sub_lib', 'materials') or 'materials'
    lib_path = mgr.get_library_path()
    cat = cat_id or "custom"
    return os.path.join(lib_path, sub_lib, cat)


def _detect_asset_type(ext):
    mapping = {
        "png": "textures", "jpg": "textures", "exr": "textures", "hdr": "hdr",
        "tga": "textures", "tif": "textures", "bmp": "textures",
        "ma": "models", "mb": "models", "fbx": "models", "obj": "models",
        "abc": "models", "usd": "models", "glb": "models", "gltf": "models",
        "ass": "models", "rs": "models", "vrscene": "models", "vrmesh": "models",
    }
    return mapping.get(ext, "models")


def _detect_asset_type_list(fmts):
    for t in ["ma", "mb", "fbx", "obj", "abc", "usd"]:
        if t in fmts:
            return "models"
    for t in ["png", "jpg", "exr", "hdr", "tga", "tif"]:
        if t in fmts:
            return "textures"
    return "models"
