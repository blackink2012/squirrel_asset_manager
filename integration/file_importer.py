"""通用 Maya file -import 导入器"""

import os
from typing import Optional
from .import_extractor import ImportExtractor


def import_file(zasset_path: str, format_name: str) -> bool:
    """从 .zasset 提取文件后执行 Maya 导入

    Args:
        zasset_path: .zasset 文件夹路径
        format_name: 格式名（fbx/obj/ma/mb/abc/usd/glb/gltf/dae/ass/vrmesh/vdb）

    Returns:
        成功返回 True
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        print("[FileImporter] 未在 Maya 环境中运行")
        return False

    if format_name in ("abc", "ass", "rs", "vrmesh", "vrscene", "usd"):
        return _import_to_cache(zasset_path, format_name)

    with ImportExtractor(zasset_path, format_name) as ext:
        file_path = ext.extracted_path
        if not file_path or not os.path.isfile(file_path):
            print(f"[FileImporter] 提取失败: {format_name} @ {zasset_path}")
            return False

        try:
            if format_name == "vrmesh":
                _import_vrmesh(file_path)
            else:
                cmds.file(file_path, i=True, ignoreVersion=True,
                          preserveReferences=True, mergeNamespacesOnClash=False,
                          namespace=":")
            print(f"[FileImporter] 导入成功: {file_path}")
            return True
        except Exception as e:
            print(f"[FileImporter] 导入失败 {file_path}: {e}")
            return False


def _import_to_cache(zasset_path: str, format_name: str) -> bool:
    """将 .zasset 文件夹中的指定格式文件复制到工程缓存目录后导入

    适用格式: abc, ass, rs（需要持久的磁盘路径引用）
    """
    import json
    import shutil
    try:
        import maya.cmds as cmds
    except ImportError:
        return False

    if not os.path.isdir(zasset_path):
        return False

    dot_ext = f".{format_name}"

    meta_path = os.path.join(zasset_path, "meta.json")
    meta = {}
    asset_id = ""
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:
            pass

    asset_name = meta.get("name") or os.path.splitext(os.path.basename(zasset_path))[0]
    asset_id = meta.get("id", "")
    _ani_list = meta.get("ani", []) if isinstance(meta, dict) else []

    zasset_basename = os.path.splitext(os.path.basename(zasset_path))[0]

    from .import_executor import _get_abc_cache_dir, _get_proxy_cache_dir
    cache_dir = _get_proxy_cache_dir(zasset_basename, asset_id) if format_name != "abc" else \
                 _get_abc_cache_dir(zasset_basename, asset_id)
    os.makedirs(cache_dir, exist_ok=True)

    def _find_files():
        result = []
        try:
            for fname in os.listdir(zasset_path):
                fpath = os.path.join(zasset_path, fname)
                if os.path.isfile(fpath) and fname.lower().endswith(dot_ext):
                    result.append(fname)
        except OSError:
            pass
        return result

    def _read_file(name):
        fpath = os.path.join(zasset_path, name)
        if os.path.isfile(fpath):
            with open(fpath, 'rb') as f:
                return f.read()
        return None

    if format_name in ("ass", "rs") and format_name in _ani_list:
        sub = "ass" if format_name == "ass" else "rs"
        seq_dir = os.path.join(cache_dir, sub)
        if os.path.isdir(seq_dir):
            shutil.rmtree(seq_dir)
        os.makedirs(seq_dir, exist_ok=True)

        src_seq_dir = os.path.join(zasset_path, sub)
        if os.path.isdir(src_seq_dir):
            for fname in os.listdir(src_seq_dir):
                src = os.path.join(src_seq_dir, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(seq_dir, fname))

        seq_files = sorted(os.listdir(seq_dir))
        if not seq_files:
            print(f"[FileImporter] .zasset 的 {sub}/ 子目录为空")
            return False
        cache_path = os.path.join(seq_dir, seq_files[0])
        print(f"[FileImporter] {format_name} 序列已缓存到: {seq_dir}")
    else:
        inner = _find_files()
        if not inner:
            print(f"[FileImporter] .zasset 不含 .{format_name} 文件")
            return False
        inner_name = inner[0]
        cache_path = os.path.join(cache_dir, f"{asset_name}{dot_ext}")
        data = _read_file(inner_name)
        if data:
            with open(cache_path, 'wb') as f:
                f.write(data)
        print(f"[FileImporter] {format_name} 已缓存: {cache_path}")

    try:
        if format_name == "ass":
            try:
                cmds.arnoldAssImport(filename=cache_path)
                for old_name in ("arnoldStandIn", "arnoldStandInShape"):
                    if cmds.objExists(old_name):
                        counter = 1
                        new_name = f"{asset_name}_ass_{counter}"
                        while cmds.objExists(new_name):
                            counter += 1
                            new_name = f"{asset_name}_ass_{counter}"
                        cmds.rename(old_name, new_name)
                        print(f"[AssImport] 重命名: {old_name} → {new_name}")
            except Exception:
                before = set(cmds.ls(assemblies=True, long=True))
                cmds.file(cache_path, i=True, ignoreVersion=True)
                after = set(cmds.ls(assemblies=True, long=True))
                new_nodes = list(after - before)
                if new_nodes:
                    cmds.select(new_nodes[0], replace=True)
            if "ass" in _ani_list:
                standin_shapes = cmds.ls(type="aiStandIn") or []
                if standin_shapes:
                    cmds.setAttr(standin_shapes[-1] + ".useFrameExtension", 1)
                    print(f"[FileImporter] ass 动画序列: {standin_shapes[-1]}.useFrameExtension=1")
        elif format_name == "rs":
            base_name = asset_name
            xform = cmds.createNode("transform", name=base_name)
            shape = cmds.createNode("mesh", name=f"{base_name}Shape", parent=xform)
            dg_node = cmds.createNode("RedshiftProxyMesh", name=f"{base_name}_proxy")
            cache_path_unix = cache_path.replace("\\", "/")
            cmds.setAttr(dg_node + ".fileName", cache_path_unix, type="string")
            print(f"[FileImporter] rs fileName 设置为: {cache_path}")
            if "rs" in _ani_list:
                cmds.setAttr(dg_node + ".useFrameExtension", 1)
                print(f"[FileImporter] rs 动画序列: {dg_node}.useFrameExtension=1")
            cmds.connectAttr(f"{dg_node}.outMesh", f"{shape}.inMesh", force=True)
            if cmds.objExists("time1"):
                cmds.connectAttr("time1.outTime", dg_node + ".currentTime", force=True)
            if cmds.objExists("lambert1"):
                cmds.select(shape, replace=True)
                cmds.hyperShade(assign="lambert1")
            print(f"[FileImporter] rs 导入成功: {base_name}")
            cmds.select(xform, replace=True)
            return True
        elif format_name == "vrmesh":
            base_name = os.path.splitext(os.path.basename(cache_path))[0]
            cache_path_unix = cache_path.replace("\\", "/")
            cmds.vrayCreateProxy(
                node=base_name,
                existing=True,
                dir=cache_path_unix,
                createProxyNode=True,
            )
            print(f"[FileImporter] vrmesh 导入成功: {base_name}")
            return True
        elif format_name == "vrscene":
            base_name = os.path.splitext(os.path.basename(cache_path))[0]
            xform = cmds.createNode("transform", name=base_name)
            shape = cmds.createNode("mesh", name=f"{base_name}Shape", parent=xform)
            dg_node = cmds.createNode("VRayScene", name=f"{base_name}_vray")
            ws_root = cmds.workspace(q=True, rd=True) or ""
            rel_path = os.path.relpath(cache_path, ws_root).replace("\\", "/")
            cmds.setAttr(f"{dg_node}.FilePath", rel_path, type="string")
            cmds.connectAttr(f"{dg_node}.outMesh", f"{shape}.inMesh", force=True)
            if cmds.objExists("time1"):
                cmds.connectAttr("time1.outTime", dg_node + ".inputTime", force=True)
            if cmds.objExists("lambert1"):
                cmds.select(shape, replace=True)
                cmds.hyperShade(assign="lambert1")
            print(f"[FileImporter] vrscene 导入成功: {base_name}")
            cmds.select(xform, replace=True)
            return True
        elif format_name == "usd" and "usd" in _ani_list:
            before = set(cmds.ls(assemblies=True, long=True))
            cmds.file(cache_path, i=True, ignoreVersion=True,
                      type="USD Import", ra=True,
                      importTimeRange="override", importFrameRate=True,
                      options="shadingMode=[[useRegistry,UsdPreviewSurface]];"
                              "readAnimData=1;primPath=/",
                      mergeNamespacesOnClash=False, namespace=":")
            after = set(cmds.ls(assemblies=True, long=True))
            new_xforms = list(after - before)
            if new_xforms:
                cmds.select(new_xforms[0], replace=True)
        else:
            before = set(cmds.ls(assemblies=True, long=True))
            cmds.file(cache_path, i=True, ignoreVersion=True,
                      preserveReferences=True, mergeNamespacesOnClash=False,
                      namespace=":")
            after = set(cmds.ls(assemblies=True, long=True))
            new_xforms = list(after - before)
            if new_xforms:
                cmds.select(new_xforms[0], replace=True)
        print(f"[FileImporter] {format_name} 导入成功: {cache_path}")
        return True
    except Exception as e:
        print(f"[FileImporter] {format_name} 导入失败: {e}")
        return False
