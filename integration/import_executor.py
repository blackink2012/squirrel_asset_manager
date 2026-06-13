"""导入执行器 — 统一入口"""

import os
from typing import List, Optional
from .format_router import get_importer_type
from .file_importer import import_file
from .texture_importer import import_texture, import_hdri


FORMAT_INTERNAL_PATH = {
    "zmetal": "node.zmetal",
    "mcm":    "node.mcm",
    "ma":     "node.ma",
    "mb":     "node.mb",
    "fbx":    "node.fbx",
    "obj":    "node.obj",
    "abc":    "node.abc",
    "usd":    "node.usd",
    "usda":   "node.usda",
    "usdc":   "node.usdc",
    "glb":    "node.glb",
    "gltf":   "node.gltf",
    "dae":    "node.dae",
    "ass":    "node.ass",
    "rs":     "node.rs",
    "proxy":  "node.proxy",
    "vrmesh": "node.vrmesh",
    "vrscene":"node.vrscene",
    "vdb":    "node.vdb",
    "sicon":  "thumb.sicon",
    "aicon":  "thumb.aicon",
}


def get_available_formats(zasset_path: str) -> List[str]:
    """读取 .zasset 的 formats 字段，返回可导入的格式列表

    排除贴图格式（png/jpg/exr/hdr等，由右键「导入贴图」菜单单独处理）、
    以及 sicon/aicon 等非导入格式。
    """
    from core.zasset_io import ZassetIO
    from .format_router import TEXTURE_FORMATS, HDR_FORMATS

    if not os.path.isdir(zasset_path):
        return []

    try:
        meta = ZassetIO.read_meta(zasset_path)
        if not meta:
            return []
        raw = meta.get("formats") or meta.get("exported_formats", []) or []
        # 排除非导入格式 + 贴图格式（由「导入贴图」菜单单独处理）
        # HDR 格式 (exr/hdr) 不排除，走通用导入
        skip = {"sicon", "aicon"} | (TEXTURE_FORMATS - HDR_FORMATS)
        return [f for f in raw if f not in skip]
    except Exception:
        return []


def import_asset(zasset_path: str, format_name: str) -> bool:
    """统一导入入口 — 根据格式路由到对应的导入器

    Args:
        zasset_path: .zasset 文件路径
        format_name: 格式名（小写，如 "fbx", "exr", "zmetal"...）

    Returns:
        成功返回 True
    """
    if not os.path.isdir(zasset_path):
        print(f"[ImportExecutor] .zasset 不存在: {zasset_path}")
        return False

    importer_type = get_importer_type(format_name)
    print(f"[ImportExecutor] 导入: {format_name} ({importer_type}) @ {zasset_path}")

    try:
        if importer_type in ("geometry", "proxy", "volume"):
            # ma/mb 需要额外处理贴图路径替换
            if format_name in ("ma", "mb"):
                return _import_ma_with_texture_rewrite(zasset_path, format_name)
            return import_file(zasset_path, format_name)

        elif importer_type == "texture":
            return import_texture(zasset_path, format_name)

        elif importer_type == "hdri":
            return import_hdri(zasset_path, format_name)

        elif importer_type == "zmetal":
            print(f"[ImportExecutor] zmetal 格式 — 创建节点网络")
            ok, name_map = apply_zmetal_as_material(zasset_path)
            return name_map if ok else False

        elif format_name == "mcm":
            print(f"[ImportExecutor] mcm 格式 — 导入材质 + 按选择物体分配")
            return _import_mcm_with_selection(zasset_path)

        else:
            print(f"[ImportExecutor] 未知格式: {format_name}")
            return False

    except Exception as e:
        print(f"[ImportExecutor] 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _import_ma_with_texture_rewrite(zasset_path: str, format_name: str) -> bool:
    """导入 ma/mb 格式，自动替换贴图路径到工程 sourceimages/"""
    import json, tempfile
    from core.zasset_io import ZassetIO
    try:
        import maya.cmds as cmds
    except ImportError:
        return False

    try:
        all_names = ZassetIO.list_contents(zasset_path)

        meta = ZassetIO.read_meta(zasset_path)
        asset_name = os.path.splitext(os.path.basename(zasset_path))[0]
        asset_id = ""
        if meta:
            asset_name = meta.get("name") or meta.get("name_cn") or asset_name
            asset_id = meta.get("id", "")
        texture_map = meta.get("texture_map", {}) if isinstance(meta, dict) else {}

        tex_policy = _get_texture_import_policy()
        tex_path_map = {}
        tex_files = [n for n in all_names if n.startswith("textures/") and not n.endswith("/")]
        if tex_files and tex_policy == "copy_to_project":
            target_dir = _get_texture_target_dir(asset_name, asset_id)
            os.makedirs(target_dir, exist_ok=True)
            for name in tex_files:
                rel_path = name[len("textures/"):]
                target_path = os.path.join(target_dir, rel_path).replace("\\", "/")
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                data = ZassetIO.read_file(zasset_path, name)
                if os.path.isfile(target_path):
                    if os.path.getsize(target_path) == len(data):
                        tex_path_map[name] = target_path
                        continue
                with open(target_path, 'wb') as f:
                    f.write(data)
                tex_path_map[name] = target_path
        elif tex_files and tex_policy == "asset_directory":
            zasset_abs = os.path.abspath(zasset_path).replace("\\", "/")
            for name in tex_files:
                rel = name[len("textures/"):]
                tex_path_map[name] = f"{zasset_abs}/textures/{rel}"

        dot_ext = f".{format_name}"
        internal_matches = [n for n in all_names
                            if not n.startswith("textures/") and not n.endswith("/")
                            and n.lower().endswith(dot_ext)]
        if not internal_matches:
            print(f"[FileImporter] .zasset 不含 .{format_name} 文件")
            return False
        internal_file = internal_matches[0]
        file_data = ZassetIO.read_file(zasset_path, internal_file)

        if format_name == "ma":
            before_file_nodes = set(cmds.ls(type="file") or [])
            import tempfile
            fd, tmp_ma = tempfile.mkstemp(suffix=".ma")
            with os.fdopen(fd, 'wb') as f:
                f.write(file_data)
            cmds.file(tmp_ma, i=True, ignoreVersion=True,
                      preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")
            os.unlink(tmp_ma)
            if tex_policy != "source_directory":
                _replace_file_texture_paths(before_file_nodes, tex_path_map, texture_map)
            _redirect_dependency_paths(zasset_path, asset_name, asset_id)
            return True
        else:
            from .import_extractor import ImportExtractor
            with ImportExtractor(zasset_path, format_name) as ext:
                tmp_path = ext.extracted_path
                if not tmp_path:
                    return False
                before_file_nodes = set(cmds.ls(type="file") or [])
                cmds.file(tmp_path, i=True, ignoreVersion=True,
                          preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")
            if tex_policy != "source_directory":
                _replace_file_texture_paths(before_file_nodes, tex_path_map, texture_map)
            _redirect_dependency_paths(zasset_path, asset_name, asset_id)
            return True

    except Exception as e:
        print(f"[FileImporter] ma/mb 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _import_mcm_with_selection(zasset_path: str) -> bool:
    """右键导入 mcm：导入 zmetal + 用选择物体按点数匹配分配材质"""
    import maya.cmds as cmds
    import json
    from core.zasset_io import ZassetIO

    saved_sel = cmds.ls(selection=True, long=True) or []
    if not saved_sel:
        cmds.warning("[MCMImport] 请先选择要赋予材质的物体")
        return False

    ok, name_map = apply_zmetal_as_material(zasset_path)
    if not ok:
        return False

    try:
        all_names = ZassetIO.list_contents(zasset_path)
        mcm_names = [n for n in all_names if n.endswith(".mcm")]
        if not mcm_names:
            print(f"[MCMImport] .zasset 不含 .mcm 文件")
            return False
        mcm_data = json.loads(ZassetIO.read_file(zasset_path, mcm_names[0]))
    except Exception as e:
        print(f"[MCMImport] 读取 MCM 失败: {e}")
        return False

    # 3. 计算选中物体的三维数据（使用导入前保存的选择）
    def _get_mesh_info(obj):
        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
        for shp in shapes:
            if cmds.nodeType(shp) == 'mesh':
                return {
                    "vert": cmds.polyEvaluate(shp, vertex=True),
                    "face": cmds.polyEvaluate(shp, face=True),
                    "edge": cmds.polyEvaluate(shp, edge=True),
                }
        return None

    sel_info = {}
    for obj in saved_sel:
        info = _get_mesh_info(obj)
        if info:
            sel_info[obj] = info
        else:
            print(f"[MCMImport] 选择物体 {obj} 未找到 mesh 数据")
    if not sel_info:
        cmds.warning(f"[MCMImport] 选中物体均无 mesh 数据")
        return False

    # 5. 建立 MCM 旧物体名 → 选择物体名 的全局映射
    def _get_match_key(val):
        """将 match_info 值转为可哈希的元组"""
        if isinstance(val, dict) and "vert" in val:
            return (val["vert"], val.get("face"), val.get("edge"))
        if isinstance(val, (int, float)):
            return (val, None, None)
        if isinstance(val, dict) and val:
            first = list(val.values())[0]
            if isinstance(first, (int, float)):
                return (first, None, None)
        return None

    # 按三维数据分组的 MCM 物体（按 Transform 去重：Shape 和 Orig 算同一个）
    mcm_grouped = []  # [(transform, mcm_obj, match_val)]
    seen_transforms = set()
    for mat_info in mcm_data.values():
        match_info = mat_info.get("match_info", mat_info.get("vert_counts", {}))
        for mcm_obj, mcm_val in match_info.items():
            key = _get_match_key(mcm_val)
            if key is None:
                continue
            transform = mcm_obj.split("|")[-2] if "|" in mcm_obj.rstrip("|") else mcm_obj
            dedup_key = (key, transform)
            if dedup_key in seen_transforms:
                continue
            seen_transforms.add(dedup_key)
            mcm_grouped.append((transform, mcm_obj, mcm_val))

    # 为每个 MCM 物体在 sel_info 中找匹配（优先三维、回退仅顶点）
    name_map_rev = {}
    unmatched_mcm = []
    for transform, mcm_obj, mcm_val in mcm_grouped:
        matched = None
        for sel_obj, sinfo in sel_info.items():
            if sel_obj in name_map_rev.values():
                continue
            if isinstance(mcm_val, dict) and "vert" in mcm_val:
                if (mcm_val["vert"] == sinfo["vert"] and
                    mcm_val.get("face") == sinfo["face"] and
                    mcm_val.get("edge") == sinfo["edge"]):
                    matched = sel_obj
                    break
            elif isinstance(mcm_val, (int, float)):
                if mcm_val == sinfo["vert"]:
                    matched = sel_obj
                    break
            elif isinstance(mcm_val, dict) and mcm_val:
                first = list(mcm_val.values())[0]
                if isinstance(first, (int, float)) and first == sinfo["vert"]:
                    matched = sel_obj
                    break
        if matched:
            name_map_rev[mcm_obj] = matched
        else:
            unmatched_mcm.append(transform)

    for t in unmatched_mcm:
        print(f"[MCMImport] 选择物中无匹配 {t}，跳过")
    print(f"[MCMImport] MCM 匹配: {len(name_map_rev)}/{len(mcm_grouped)}")

    # 6. 用全局映射分配材质：遍历 matched 对，每对赋予对应材质
    from ..integration.zjg_exporter import _assign_material_to_objects, _assign_face_materials

    # 对每个 MCM 物体，找到它所属的所有材质
    obj_to_mats = {}  # {mcm_obj_name: [(material_actual_name, face_assignments)]}
    for mat_name, mat_info in mcm_data.items():
        actual_name = name_map.get(mat_name, mat_name)
        face_assignments = mat_info.get("face_assignments", {})
        objects_list = mat_info.get("objects", [])
        for mo in objects_list:
            obj_to_mats.setdefault(mo, []).append((actual_name, face_assignments))

    assigned_count = 0
    for mcm_obj, sel_obj in name_map_rev.items():
        # 兜底：尝试用短名匹配 obj_to_mats
        mats_for_obj = obj_to_mats.get(mcm_obj)
        if mats_for_obj is None:
            mcm_short = mcm_obj.split("|")[-1].split(":")[-1]
            for key in obj_to_mats:
                if mcm_short == key.split("|")[-1].split(":")[-1]:
                    mats_for_obj = obj_to_mats[key]
                    break
        if not mats_for_obj:
            print(f"[MCMImport] {mcm_obj} → {sel_obj}: 未找到对应材质，跳过")
            continue
        # 查这个 MCM 物体有没有面级指定
        has_face = any(fa for _, fa in mats_for_obj)
        if has_face:
            # 将面级指定中的 mesh_name 和材质名都替换
            merged_faces = {}
            for actual_name, fa in mats_for_obj:
                for mesh_name, mat_faces in fa.items():
                    # 将 MCM 中的原始材质名替换为 Maya 实际节点名
                    renamed = {}
                    for orig_mat, faces in mat_faces.items():
                        renamed[name_map.get(orig_mat, orig_mat)] = faces
                    merged_faces.setdefault(sel_obj, {}).update(renamed)
            if merged_faces:
                _assign_face_materials(merged_faces)
                assigned_count += 1
        else:
            # 无面级：将材质赋予 sel_obj
            for actual_name, _ in mats_for_obj:
                _assign_material_to_objects(actual_name, [sel_obj])
                assigned_count += 1

    print(f"[MCMImport] 已分配 {assigned_count} 个材质 → {len(name_map_rev)} 个匹配物体")
    return True


def apply_zmetal_as_material(zasset_path: str):
    """从 .zasset 中提取 node.zmetal 并在 Maya 中创建材质网络。

    自动处理：
      - 提取 textures/ 到工程 sourceimages/squirrel_asset/{资产名}/
      - 替换 file node 的 fileTextureName 指向新路径
      - 委托 zjg_exporter._radar_import_single_file 创建节点
      - 多精度贴图支持：导入时弹出精度选择

    Returns:
        (success, name_map) — success 为 True/False，name_map 为旧名→新名映射
    """
    import json
    import shutil
    import tempfile
    from core.zasset_io import ZassetIO

    if not os.path.isdir(zasset_path):
        print(f"[ImportExecutor] .zasset 不存在: {zasset_path}")
        return False, {}

    try:
        all_names = ZassetIO.list_contents(zasset_path)
        meta_data = ZassetIO.read_meta(zasset_path)
        asset_id = meta_data.get("id", "") if meta_data else ""

        asset_name = "untitled"
        if meta_data:
            asset_name = meta_data.get("name") or \
                         meta_data.get("name_cn") or \
                         os.path.splitext(os.path.basename(zasset_path))[0]

        zmetal_name = _find_zmetal_in_zip(all_names)
        if not zmetal_name:
            print(f"[ImportExecutor] .zasset 不含 .zmetal 文件 (该资产可能由快捷工具导出，不含节点网络数据)")
            return False, {}
        zmetal_data = json.loads(ZassetIO.read_file(zasset_path, zmetal_name))
        nodes_data = zmetal_data.get("nodes", {})

        # ── 多精度贴图检测 ──
        resolutions = meta_data.get("resolutions", []) if meta_data else []
        selected_resolution = None
        if len(resolutions) > 1:
            selected_resolution = _select_resolution_dialog(resolutions, meta_data.get("default_resolution", resolutions[0]))
            if selected_resolution is None:
                print(f"[ImportExecutor] 用户取消导入")
                return False, {}

        # ── 读取贴图导入策略 ──
        tex_policy = _get_texture_import_policy()

        # ── 提取贴图 ──
        tex_path_map = {}
        tex_files = [n for n in all_names if n.startswith("textures/") and not n.endswith("/")]

        if tex_policy == "source_directory":
            # 源文件目录：不拷贝、不修改路径
            pass
        elif tex_policy == "asset_directory":
            # 当前资产目录：直接指向 .zasset 内的贴图
            zasset_abs = os.path.abspath(zasset_path).replace("\\", "/")
            for name in tex_files:
                rel = name[len("textures/"):]
                tex_path_map[name] = f"{zasset_abs}/textures/{rel}"
        else:
            # copy_to_project：拷贝贴图到工程
            # 如果有多精度，只提取选中精度的贴图
            filtered = tex_files
            if selected_resolution:
                filtered = [n for n in filtered if n.startswith(f"textures/{selected_resolution}/")]

            if filtered:
                target_dir = _get_texture_target_dir(asset_name, asset_id)
                os.makedirs(target_dir, exist_ok=True)

                for name in filtered:
                    rel = name[len("textures/"):]
                    target_path = os.path.join(target_dir, rel).replace("\\", "/")
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)

                    if os.path.isfile(target_path):
                        old_size = os.path.getsize(target_path)
                        data = ZassetIO.read_file(zasset_path, name)
                        new_size = len(data)
                        if old_size == new_size:
                            tex_path_map[name] = target_path
                            continue

                    with open(target_path, 'wb') as f:
                        f.write(ZassetIO.read_file(zasset_path, name))
                    tex_path_map[name] = target_path
                    print(f"[TextureCopy] {name} → {target_path}")

        abc_inner = [n for n in all_names if n.endswith(".abc") and not n.startswith("textures/")]
        if abc_inner:
            abc_inner_path = abc_inner[0]
            abc_target_dir = _get_abc_cache_dir(asset_name, asset_id)
            os.makedirs(abc_target_dir, exist_ok=True)
            abc_target = os.path.join(abc_target_dir, f"{asset_name}.abc")
            with open(abc_target, 'wb') as f:
                f.write(ZassetIO.read_file(zasset_path, abc_inner_path))
            print(f"[ImportExecutor] abc 已提取: {abc_target}")

        # ── 替换 JSON 中的 fileTextureName ──
        if tex_policy != "source_directory":
            _rewrite_texture_paths_in_json(nodes_data, tex_path_map)
        zmetal_data["nodes"] = nodes_data

        # ── 写入临时 JSON 文件 ──
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix=f"{asset_name}_")
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(zmetal_data, f, indent=2, ensure_ascii=False)
        from ..integration.zjg_exporter import _radar_import_single_file

        count, name_map = _radar_import_single_file(tmp_path)
        os.unlink(tmp_path)  # 清理临时文件

        if count > 0:
            print(f"[ImportExecutor] 已创建 {count} 个节点 (from {os.path.basename(zasset_path)})")
            return True, name_map
        return False, {}

    except Exception as e:
        print(f"[ImportExecutor] zmetal 材质创建失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _get_texture_target_dir(asset_name: str, asset_id: str = "") -> str:
    """计算贴图目标目录：{sourceimages}/squirrel_asset/{资产名}_{ID后4位}/"""
    suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
    try:
        import maya.cmds as cmds
        ws_root = cmds.workspace(q=True, rd=True) or ""
        si_rule = cmds.workspace(fileRuleEntry="sourceImages")
        if si_rule:
            base = os.path.join(ws_root, si_rule) if si_rule else ws_root
        else:
            base = os.path.join(ws_root, "sourceimages")
        base = os.path.normpath(base)
    except Exception:
        base = os.path.normpath(os.path.join(
            os.path.expanduser("~/Documents/maya/projects/default"), "sourceimages"))
    return os.path.join(base, "squirrel_asset", f"{asset_name}{suffix}")


def _get_abc_cache_dir(asset_name: str, asset_id: str = "") -> str:
    """计算 abc 缓存目录：{工程}/cache/alembic/squirrel_asset/{资产名}_{ID后4位}/"""
    suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
    ws_root = _get_ws_root()
    return os.path.join(ws_root, "cache", "alembic", "squirrel_asset", f"{asset_name}{suffix}")


def _get_proxy_cache_dir(asset_name: str, asset_id: str = "") -> str:
    """计算代理缓存目录：{工程}/cache/squirrel_asset/{资产名}_{ID后4位}/"""
    suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
    ws_root = _get_ws_root()
    return os.path.join(ws_root, "cache", "squirrel_asset", f"{asset_name}{suffix}")


def import_variant_geometry(zasset_path: str, version: str = None, lod: str = None) -> bool:
    """导入指定变体的几何体到 Maya 场景。

    Args:
        zasset_path: .zasset 文件夹路径
        version: 版本 id（如 "v1"），为空使用默认版本
        lod: LOD id（如 "lod0"），为空使用默认 LOD

    Returns:
        成功返回 True
    """
    import tempfile
    from core.zasset_io import ZassetIO

    try:
        import maya.cmds as cmds
    except ImportError:
        print("[VariantImport] 未在 Maya 环境中运行")
        return False

    if not os.path.isdir(zasset_path):
        print(f"[VariantImport] .zasset 不存在: {zasset_path}")
        return False

    # 解析几何体路径
    geom_rel = ZassetIO.resolve_geometry(zasset_path, version=version, lod=lod)
    if not geom_rel:
        print(f"[VariantImport] 未找到变体几何体: version={version}, lod={lod}")
        return False

    geom_full = os.path.join(zasset_path, geom_rel)
    if not os.path.isfile(geom_full):
        print(f"[VariantImport] 几何体文件不存在: {geom_full}")
        return False

    ext = os.path.splitext(geom_rel)[1].lower().lstrip(".")
    print(f"[VariantImport] 导入变体几何体: {geom_rel} ({ext})")

    # 读取贴图映射（用于 .ma 文件的贴图路径替换）
    meta = ZassetIO.read_meta(zasset_path)
    texture_map = meta.get("texture_map", {}) if isinstance(meta, dict) else {}
    tex_path_map = {}

    # 如果存在贴图，按策略处理
    all_names = ZassetIO.list_contents(zasset_path)
    tex_files = [n for n in all_names if n.startswith("textures/") and not n.endswith("/")]
    tex_policy = _get_texture_import_policy()
    if tex_files and tex_policy == "copy_to_project":
        asset_name = meta.get("name", "") if meta else ""
        asset_id = meta.get("id", "") if meta else ""
        target_dir = _get_texture_target_dir(asset_name, asset_id)
        os.makedirs(target_dir, exist_ok=True)
        for name in tex_files:
            rel_path = name[len("textures/"):]
            target_path = os.path.join(target_dir, rel_path).replace("\\", "/")
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            data = ZassetIO.read_file(zasset_path, name)
            if data:
                with open(target_path, 'wb') as f:
                    f.write(data)
                tex_path_map[name] = target_path
    elif tex_files and tex_policy == "asset_directory":
        zasset_abs = os.path.abspath(zasset_path).replace("\\", "/")
        for name in tex_files:
            rel = name[len("textures/"):]
            tex_path_map[name] = f"{zasset_abs}/textures/{rel}"

    if ext == "ma":
        # .ma 文件：需要贴图路径替换
        before_file_nodes = set(cmds.ls(type="file") or [])
        cmds.file(geom_full, i=True, ignoreVersion=True,
                  preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")
        if tex_path_map or (tex_policy != "source_directory" and texture_map):
            _replace_file_texture_paths(before_file_nodes, tex_path_map, texture_map)
        asset_name = meta.get("name", "") if meta else ""
        asset_id = meta.get("id", "") if meta else ""
        _redirect_dependency_paths(zasset_path, asset_name, asset_id)
    elif ext in ("fbx", "obj", "mb"):
        cmds.file(geom_full, i=True, ignoreVersion=True,
                  preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")
        if ext == "mb":
            asset_name = meta.get("name", "") if meta else ""
            asset_id = meta.get("id", "") if meta else ""
            _redirect_dependency_paths(zasset_path, asset_name, asset_id)
    elif ext in ("abc", "usd", "usda", "usdc", "glb", "gltf"):
        # 缓存格式：复制到工程缓存目录后导入
        _import_variant_to_cache(zasset_path, geom_full, ext, meta)
    else:
        cmds.file(geom_full, i=True, ignoreVersion=True,
                  preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")

    print(f"[VariantImport] 导入成功: {geom_rel}")
    return True


def import_variant_material(zasset_path: str, version: str = None) -> bool:
    """导入变体版本的材质到 Maya 场景。

    优先使用 variants/{version}/node.zmetal，不存在则回退到根 node.zmetal。

    Args:
        zasset_path: .zasset 文件夹路径
        version: 版本 id（如 "v2"），为空使用根材质

    Returns:
        成功返回 True
    """
    from core.zasset_io import ZassetIO

    try:
        import maya.cmds as cmds
    except ImportError:
        print("[VariantMaterial] 未在 Maya 环境中运行")
        return False

    if not os.path.isdir(zasset_path):
        return False

    # 解析材质路径
    mat_rel = ZassetIO.resolve_material(zasset_path, version=version)
    if not mat_rel:
        print(f"[VariantMaterial] 未找到材质: version={version}")
        return False

    mat_full = os.path.join(zasset_path, mat_rel)
    is_variant_mat = mat_rel.startswith("variants/")
    print(f"[VariantMaterial] 使用材质: {mat_rel}")

    # 解析贴图目录
    tex_dir = ZassetIO.resolve_textures_dir(zasset_path, version=version)

    try:
        import json
        with open(mat_full, 'r', encoding='utf-8') as f:
            zmetal_data = json.load(f)
        nodes_data = zmetal_data.get("nodes", {})

        # 提取贴图
        tex_path_map = {}
        tex_policy = _get_texture_import_policy()
        if tex_dir and tex_policy != "source_directory":
            tex_dir_full = os.path.join(zasset_path, tex_dir.rstrip('/'))
            if os.path.isdir(tex_dir_full):
                if tex_policy == "copy_to_project":
                    meta = ZassetIO.read_meta(zasset_path)
                    asset_name = meta.get("name", "") if meta else ""
                    asset_id = meta.get("id", "") if meta else ""
                    target_dir = _get_texture_target_dir(asset_name, asset_id)
                    os.makedirs(target_dir, exist_ok=True)

                    for root, _, files in os.walk(tex_dir_full):
                        for fname in files:
                            src = os.path.join(root, fname)
                            rel = os.path.relpath(src, tex_dir_full).replace("\\", "/")
                            dst = os.path.join(target_dir, rel)
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            with open(src, 'rb') as sf:
                                with open(dst, 'wb') as df:
                                    df.write(sf.read())
                            tex_path_map[f"{tex_dir}{rel}"] = dst
                else:  # asset_directory
                    zasset_abs = os.path.abspath(zasset_path).replace("\\", "/")
                    for root, _, files in os.walk(tex_dir_full):
                        for fname in files:
                            src = os.path.join(root, fname)
                            rel = os.path.relpath(src, tex_dir_full).replace("\\", "/")
                            tex_path_map[f"{tex_dir}{rel}"] = f"{zasset_abs}/{tex_dir}{rel}"

        # 替换 JSON 中的贴图路径
        _rewrite_texture_paths_in_json(nodes_data, tex_path_map)
        zmetal_data["nodes"] = nodes_data

        # 写入临时文件导入
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="variant_mat_")
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(zmetal_data, f, indent=2, ensure_ascii=False)

        from ..integration.zjg_exporter import _radar_import_single_file
        count, name_map = _radar_import_single_file(tmp_path)
        os.unlink(tmp_path)

        if count > 0:
            print(f"[VariantMaterial] 已创建 {count} 个节点 (from {mat_rel})")
            return True
        return False

    except Exception as e:
        print(f"[VariantMaterial] 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _import_variant_to_cache(zasset_path: str, geom_full: str, fmt: str, meta: dict) -> bool:
    """将变体几何体复制到工程缓存目录后导入（适用 abc/usd 等格式）"""
    import shutil
    try:
        import maya.cmds as cmds
    except ImportError:
        return False

    asset_name = meta.get("name", "") if meta else ""
    asset_id = meta.get("id", "") if meta else ""

    if fmt in ("abc",):
        cache_dir = _get_abc_cache_dir(asset_name, asset_id)
    else:
        cache_dir = _get_proxy_cache_dir(asset_name, asset_id)
    os.makedirs(cache_dir, exist_ok=True)

    cache_path = os.path.join(cache_dir, os.path.basename(geom_full))
    shutil.copy2(geom_full, cache_path)
    print(f"[VariantImport] 缓存到: {cache_path}")

    before = set(cmds.ls(assemblies=True, long=True))
    cmds.file(cache_path, i=True, ignoreVersion=True,
              preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")
    after = set(cmds.ls(assemblies=True, long=True))
    new_xforms = list(after - before)
    if new_xforms:
        cmds.select(new_xforms[0], replace=True)
    return True


def _get_ws_root() -> str:
    """获取 Maya 工程根目录"""
    try:
        import maya.cmds as cmds
        ws_root = cmds.workspace(q=True, rd=True) or ""
        return os.path.normpath(ws_root)
    except Exception:
        return os.path.normpath(os.path.join(
            os.path.expanduser("~/Documents/maya/projects/default")))


def _replace_file_texture_paths(before_file_nodes: set, tex_path_map: dict,
                                texture_map: dict = None):
    """导入后用 texture_map 精确匹配贴图路径。

    用 meta.json 的 texture_map（原始绝对路径→内部路径）链式查找：
    原始路径 → 内部路径 → 磁盘路径。无需材质名、无需 try-error。
    
    回退策略：当 texture_map 无匹配时，用文件名（basename）匹配。
    """
    if not tex_path_map:
        return
    import maya.cmds as cmds
    import os

    # 构建原始路径→磁盘路径映射（texture_map + tex_path_map 链式查找）
    orig_to_disk = {}
    if texture_map:
        for orig_path, zip_rel in texture_map.items():
            norm_orig = orig_path.replace("\\", "/")
            if zip_rel in tex_path_map:
                orig_to_disk[norm_orig] = tex_path_map[zip_rel]
    
    # 构建 filename → 磁盘路径映射（回退用）
    name_to_disk = {}
    for zip_rel, disk_path in tex_path_map.items():
        basename = os.path.basename(zip_rel.replace("\\", "/"))
        if basename not in name_to_disk:
            name_to_disk[basename] = disk_path

    after_nodes = set(cmds.ls(type="file") or [])
    new_nodes = after_nodes - before_file_nodes

    for fn in new_nodes:
        try:
            old = cmds.getAttr(fn + ".fileTextureName")
            norm_old = old.replace("\\", "/")
            disk_path = orig_to_disk.get(norm_old)
            
            # 回退：通过 basename 匹配
            if not disk_path:
                old_basename = os.path.basename(norm_old)
                disk_path = name_to_disk.get(old_basename)
            
            if disk_path:
                cmds.setAttr(fn + ".fileTextureName", disk_path, type="string")
                print(f"[FileTextureReplace] {fn}: {old} → {disk_path}")
        except Exception:
            pass


def _get_texture_import_policy() -> str:
    """读取贴图导入策略设置。

    Returns:
        "copy_to_project" | "asset_directory" | "source_directory"
    """
    try:
        import os, json
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "Assets", "preset", "config.json")
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            policy = config.get("texture_import_policy", "copy_to_project")
            return policy
    except Exception:
        pass
    return "copy_to_project"


def _rewrite_texture_paths_in_json(nodes_data: dict, tex_path_map: dict):
    """扫描 JSON 中所有 file 节点，将 fileTextureName 从内部路径替换为磁盘路径

    zmetal JSON 中的 fileTextureName 已改为 textures/{材质名}/{文件名}，
    直接用全路径匹配。

    回退策略：无精确匹配时，提取文件名（basename）匹配。
    """
    import os

    # 构建 basename → 磁盘路径映射（回退用）
    name_to_disk = {}
    for zip_rel, disk_path in tex_path_map.items():
        basename = os.path.basename(zip_rel.replace("\\", "/"))
        if basename not in name_to_disk:
            name_to_disk[basename] = disk_path

    # 构建全路径 → 磁盘路径的映射
    for node_name, info in nodes_data.items():
        if info.get("node_type") != "file":
            continue
        attrs = info.get("attrs", {})
        ftn = attrs.get("fileTextureName", {})
        if ftn.get("type") != "value":
            continue
        old_val = ftn.get("value", "")
        # 精确匹配 zmetal JSON 中已存为 textures/{材质名}/{文件名}
        if old_val in tex_path_map:
            ftn["value"] = tex_path_map[old_val]
            print(f"[TextureReplace] {node_name}: {old_val} → {tex_path_map[old_val]}")
        else:
            # 回退：正则提取文件名匹配
            old_basename = os.path.basename(old_val.replace("\\", "/"))
            if old_basename in name_to_disk:
                ftn["value"] = name_to_disk[old_basename]
                print(f"[TextureReplace] {node_name}: {old_val} → {name_to_disk[old_basename]} (basename fallback)")


def _find_zmetal_in_zip(all_names: list) -> Optional[str]:
    """在 .zasset 文件列表中查找 .zmetal 文件

    优先返回 node.zmetal，其次任意 .zmetal 文件。
    """
    if "node.zmetal" in all_names:
        return "node.zmetal"
    zmetals = [n for n in all_names if n.endswith(".zmetal")]
    if zmetals:
        return zmetals[0]
    return None


def _select_resolution_dialog(resolutions, default_res):
    """弹出 Maya 对话框让用户选择导入精度，返回选中精度或 None（取消）"""
    import maya.cmds as cmds
    
    if not resolutions:
        return default_res
    
    def _res_sort_key(r):
        import re
        m = re.search(r'(\d+)', r)
        if m:
            return -int(m.group(1))
        return 0
    
    sorted_res = sorted(resolutions, key=_res_sort_key)
    
    btn_labels = [r.upper() for r in sorted_res]
    btn_labels.append("取消")
    
    default_idx = 0
    for i, r in enumerate(sorted_res):
        if r == default_res:
            default_idx = i
            break
    
    res_list = ", ".join(sorted_res)
    msg = f"资产包含多种精度贴图: {res_list}\n\n选择要导入的精度:"
    
    result = cmds.confirmDialog(
        title="选择贴图精度",
        message=msg,
        button=btn_labels,
        defaultButton=btn_labels[default_idx],
        cancelButton=btn_labels[-1],
        dismissString=btn_labels[-1]
    )
    
    if result == btn_labels[-1]:
        return None
    
    for i, label in enumerate(btn_labels[:-1]):
        if result == label:
            return sorted_res[i]
    
    return default_res


# ═══════════════════════════════════════════════════════════════
# 依赖文件导入重定向
# ═══════════════════════════════════════════════════════════════

def _get_dependency_import_policy() -> str:
    try:
        import os, json
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "Assets", "preset", "config.json")
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            policy = config.get("dependency_import_policy", "copy_to_project")
            return policy
    except Exception:
        pass
    return "copy_to_project"


def _get_dependency_target_dir(asset_name: str, asset_id: str = "") -> str:
    suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
    ws_root = _get_ws_root()
    return os.path.join(ws_root, "cache", "squirrel_asset", f"{asset_name}{suffix}")


def _get_dependency_references_target_dir(asset_name: str, asset_id: str = "") -> str:
    suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
    try:
        import maya.cmds as cmds
        ws_root = cmds.workspace(q=True, rd=True) or ""
        ref_rule = cmds.workspace(fileRuleEntry="references")
        if ref_rule:
            base = os.path.join(ws_root, ref_rule) if ref_rule else ws_root
        else:
            base = os.path.join(ws_root, "references")
        base = os.path.normpath(base)
    except Exception:
        base = os.path.normpath(os.path.join(
            os.path.expanduser("~/Documents/maya/projects/default"), "references"))
    return os.path.join(base, "squirrel_asset", f"{asset_name}{suffix}")


_NODE_TYPE_ATTRS = [
    ("AlembicNode", ("abc_File", "cacheFileName")),
    ("gpuCache", ("cacheFileName",)),
    ("cacheFile", ("cachePath", "path")),
    ("VRayVolumeGrid", ("ipth", "ipthr", "f", "fn", "filename", "fileName", "filePath")),
    ("aiVolume", ("filename", "fileName", "f", "fn", "filePath")),
    ("RedshiftVolumeShape", ("fn", "filename", "fileName", "f", "filePath")),
    ("aiStandIn", ("dso", "fn")),
    ("VRayProxy", ("fileName",)),
    ("VRayMesh", ("fileName",)),
    ("VRayScene", ("FilePath",)),
    ("RedshiftProxyMesh", ("fn", "fileName")),
    ("mayaUsdProxyShape", ("fp", "filePath")),
]


def _has_frame_pattern(path: str) -> bool:
    return any(p in path for p in ("####", "##", "%04d", "%4d", "%0", "#"))


def _resolve_frame_pattern_to_glob(path: str) -> str:
    parts = path.rsplit(".", 1)
    if len(parts) == 2:
        name, ext = parts
        for pat, glob_pat in [("####", "*"), ("##", "*"), ("%04d", "*"), ("%4d", "*"), ("%0", "*"), ("#", "*")]:
            if pat in name:
                return name.replace(pat, glob_pat) + "." + ext
    return path


def _copy_with_unique_name(src: str, dst_dir: str) -> str:
    import shutil
    basename = os.path.basename(src)
    dst = os.path.join(dst_dir, basename).replace("\\", "/")
    if not os.path.isfile(dst):
        shutil.copy2(src, dst)
        return dst
    if os.path.getsize(dst) == os.path.getsize(src):
        return dst
    name_part, ext = os.path.splitext(basename)
    counter = 1
    while True:
        new_name = f"{name_part}_{counter:03d}{ext}"
        dst = os.path.join(dst_dir, new_name).replace("\\", "/")
        if not os.path.isfile(dst):
            shutil.copy2(src, dst)
            return dst
        if os.path.getsize(dst) == os.path.getsize(src):
            return dst
        counter += 1


def _redirect_dependency_paths(zasset_path: str, asset_name: str = "", asset_id: str = ""):
    policy = _get_dependency_import_policy()
    if policy == "source_directory":
        return

    try:
        import maya.cmds as cmds
    except ImportError:
        return

    associated_dir = os.path.join(zasset_path, "associated")
    if not os.path.isdir(associated_dir):
        print(f"[DepRedirect] 无 associated/ 目录，跳过")
        return

    zasset_abs = os.path.abspath(zasset_path).replace("\\", "/")
    basename_to_target = {}
    basename_to_category = {}
    dep_target_dir = ""
    references_target_dir = ""

    if policy == "copy_to_project":
        dep_target_dir = _get_dependency_target_dir(asset_name, asset_id)
        references_target_dir = _get_dependency_references_target_dir(asset_name, asset_id)
        os.makedirs(dep_target_dir, exist_ok=True)
        os.makedirs(references_target_dir, exist_ok=True)

    for category in ("caches", "proxies", "references"):
        cat_dir = os.path.join(associated_dir, category)
        if not os.path.isdir(cat_dir):
            continue
        for fname in os.listdir(cat_dir):
            src = os.path.join(cat_dir, fname)
            if not os.path.isfile(src):
                continue
            basename_to_category[fname] = category

            if policy == "asset_directory":
                basename_to_target[fname] = f"{zasset_abs}/associated/{category}/{fname}"
            elif policy == "copy_to_project":
                if category == "references":
                    target_dir = references_target_dir
                else:
                    target_dir = os.path.join(dep_target_dir, category)
                os.makedirs(target_dir, exist_ok=True)
                basename_to_target[fname] = _copy_with_unique_name(src, target_dir)

    if not basename_to_target:
        print(f"[DepRedirect] 无依赖文件需要重定向")
        return

    print(f"[DepRedirect] 策略={policy}, 待重定向 {len(basename_to_target)} 个文件")

    for node_type, attr_names in _NODE_TYPE_ATTRS:
        nodes = cmds.ls(type=node_type) or []
        for node in nodes:
            old_path = ""
            found_attr = ""
            for attr in attr_names:
                try:
                    old_path = cmds.getAttr(f"{node}.{attr}")
                    if old_path and isinstance(old_path, str) and old_path.strip():
                        found_attr = attr
                        break
                except Exception:
                    continue

            if not old_path or not found_attr:
                continue

            old_basename = os.path.basename(old_path.replace("\\", "/"))
            new_path = basename_to_target.get(old_basename)

            if not new_path and _has_frame_pattern(old_basename):
                import fnmatch
                for bname, category in basename_to_category.items():
                    glob_pat = _resolve_frame_pattern_to_glob(bname)
                    if fnmatch.fnmatch(bname, _resolve_frame_pattern_to_glob(old_basename)):
                        if policy == "asset_directory":
                            new_path = f"{zasset_abs}/associated/{category}/{old_basename}"
                        elif policy == "copy_to_project":
                            target_dir = references_target_dir if category == "references" else os.path.join(dep_target_dir, category)
                            new_path = os.path.join(target_dir, old_basename).replace("\\", "/")
                        break

            if not new_path:
                continue

            try:
                cmds.setAttr(f"{node}.{found_attr}", new_path, type="string")
                print(f"[DepRedirect] {node}.{found_attr}: {old_path} → {new_path}")
            except Exception as e:
                print(f"[DepRedirect] 设置 {node}.{found_attr} 失败: {e}")

    ref_files = [fname for fname, category in basename_to_category.items() if category == "references"]
    if ref_files:
        import fnmatch
        ref_nodes = cmds.ls(type="reference") or []
        for rn in ref_nodes:
            try:
                old_path = cmds.referenceQuery(rn, filename=True)
            except Exception:
                continue
            if not old_path:
                continue
            old_basename = os.path.basename(old_path.replace("\\", "/"))
            new_path = basename_to_target.get(old_basename)

            if not new_path and old_basename:
                for bname in ref_files:
                    if fnmatch.fnmatch(old_basename, _resolve_frame_pattern_to_glob(bname)):
                        if policy == "asset_directory":
                            new_path = f"{zasset_abs}/associated/references/{old_basename}"
                        elif policy == "copy_to_project":
                            new_path = os.path.join(references_target_dir, old_basename).replace("\\", "/")
                        break

            if not new_path:
                continue

            try:
                cmds.file(new_path, loadReference=rn)
                print(f"[DepRedirect] reference {rn}: {old_path} → {new_path}")
            except Exception as e:
                print(f"[DepRedirect] 替换引用 {rn} 失败: {e}")


def _collect_associated_frame_sequences(zasset_path: str) -> dict:
    associated_dir = os.path.join(zasset_path, "associated")
    if not os.path.isdir(associated_dir):
        return {}
    result = {}
    for category in ("caches", "proxies", "references"):
        cat_dir = os.path.join(associated_dir, category)
        if not os.path.isdir(cat_dir):
            continue
        for fname in os.listdir(cat_dir):
            src = os.path.join(cat_dir, fname)
            if os.path.isfile(src) and _has_frame_pattern(fname):
                result[fname] = src
    return result
