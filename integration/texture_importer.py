"""贴图导入器 — 创建 file texture node"""

import os
import json
from typing import Optional
from .import_extractor import ImportExtractor


def _copy_texture_to_sourceimages(zasset_path: str, temp_path: str) -> str:
    """根据贴图导入策略返回贴图路径。

    - copy_to_project: 拷贝到 sourceimages/ 永久目录
    - asset_directory: 直接返回 .zasset 内绝对路径
    - source_directory: 返回原始路径，不修改

    Returns:
        最终贴图路径
    """
    try:
        from .import_executor import _get_texture_import_policy
        policy = _get_texture_import_policy()

        if policy == "source_directory":
            return temp_path

        if policy == "asset_directory":
            # 直接返回 .zasset 内贴图的绝对路径
            zasset_abs = os.path.abspath(zasset_path).replace("\\", "/")
            if temp_path.startswith(zasset_abs):
                return temp_path
            return f"{zasset_abs}/textures/{os.path.basename(temp_path)}"

        # copy_to_project
        asset_name = os.path.splitext(os.path.basename(zasset_path))[0]
        asset_id = ""

        meta_path = os.path.join(zasset_path, "meta.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                asset_name = meta.get("name") or meta.get("name_cn") or asset_name
                asset_id = meta.get("id", "")
            except Exception:
                pass

        from .import_executor import _get_texture_target_dir
        target_dir = _get_texture_target_dir(asset_name, asset_id)
        os.makedirs(target_dir, exist_ok=True)
        basename = os.path.basename(temp_path)
        target_path = os.path.join(target_dir, basename).replace("\\", "/")

        if not os.path.isfile(target_path):
            import shutil
            shutil.copy2(temp_path, target_path)
            print(f"[TextureCopy] {basename} → {target_path}")
        return target_path
    except Exception as e:
        print(f"[TextureCopy] 拷贝失败, 回退到临时路径: {e}")
        return temp_path


def _import_file_node(file_path: str, color_space: str = None) -> str:
    """创建 file 节点 + place2dTexture，返回 file 节点名"""
    import maya.cmds as cmds

    file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
    cmds.setAttr(f"{file_node}.fileTextureName", file_path, type="string")
    cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
    p2d = cmds.shadingNode("place2dTexture", asUtility=True)
    cmds.connectAttr(f"{p2d}.coverage", f"{file_node}.coverage")
    cmds.connectAttr(f"{p2d}.translateFrame", f"{file_node}.translateFrame")
    cmds.connectAttr(f"{p2d}.rotateFrame", f"{file_node}.rotateFrame")
    cmds.connectAttr(f"{p2d}.mirrorU", f"{file_node}.mirrorU")
    cmds.connectAttr(f"{p2d}.mirrorV", f"{file_node}.mirrorV")
    cmds.connectAttr(f"{p2d}.stagger", f"{file_node}.stagger")
    cmds.connectAttr(f"{p2d}.wrapU", f"{file_node}.wrapU")
    cmds.connectAttr(f"{p2d}.wrapV", f"{file_node}.wrapV")
    cmds.connectAttr(f"{p2d}.repeatUV", f"{file_node}.repeatUV")
    cmds.connectAttr(f"{p2d}.offset", f"{file_node}.offset")
    cmds.connectAttr(f"{p2d}.rotateUV", f"{file_node}.rotateUV")
    cmds.connectAttr(f"{p2d}.noiseUV", f"{file_node}.noiseUV")
    cmds.connectAttr(f"{p2d}.vertexUvOne", f"{file_node}.vertexUvOne")
    cmds.connectAttr(f"{p2d}.vertexUvTwo", f"{file_node}.vertexUvTwo")
    cmds.connectAttr(f"{p2d}.vertexUvThree", f"{file_node}.vertexUvThree")
    cmds.connectAttr(f"{p2d}.vertexCameraOne", f"{file_node}.vertexCameraOne")
    cmds.connectAttr(f"{p2d}.outUV", f"{file_node}.uv")
    cmds.connectAttr(f"{p2d}.outUvFilterSize", f"{file_node}.uvFilterSize")
    if color_space:
        try:
            cmds.setAttr(f"{file_node}.colorSpace", color_space, type="string")
        except Exception:
            pass
    cmds.select(file_node, replace=True)
    return file_node


def import_texture(zasset_path: str, format_name: str) -> bool:
    """从 .zasset 提取贴图并创建 file texture node

    Args:
        zasset_path: .zasset 文件夹路径
        format_name: 格式名（png/jpg/exr/hdr/tga...）

    Returns:
        成功返回 True
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        print("[TextureImporter] 未在 Maya 环境中运行")
        return False

    with ImportExtractor(zasset_path, format_name) as ext:
        temp_path = ext.extracted_path
        if not temp_path or not os.path.isfile(temp_path):
            print(f"[TextureImporter] 提取失败: {format_name} @ {zasset_path}")
            return False

        try:
            file_path = _copy_texture_to_sourceimages(zasset_path, temp_path)
            ext = os.path.splitext(file_path)[1].lower()
            cs = _color_space_for_ext(ext)
            file_node = _import_file_node(file_path, cs)
            print(f"[TextureImporter] 创建 file node: {file_node} ← {file_path}")
            return True
        except Exception as e:
            print(f"[TextureImporter] 创建贴图节点失败: {e}")
            return False


def import_hdri(zasset_path: str, format_name: str) -> bool:
    """从 .zasset 提取 HDR 并创建 file 节点

    Args:
        zasset_path: .zasset 文件夹路径
        format_name: 格式名（hdr/exr）

    Returns:
        成功返回 True
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        print("[HdriImporter] 未在 Maya 环境中运行")
        return False

    with ImportExtractor(zasset_path, format_name) as ext:
        temp_path = ext.extracted_path
        if not temp_path or not os.path.isfile(temp_path):
            print(f"[HdriImporter] 提取失败: {format_name} @ {zasset_path}")
            return False

        try:
            file_path = _copy_texture_to_sourceimages(zasset_path, temp_path)
            file_node = _import_file_node(file_path, "Raw")
            print(f"[HdriImporter] 创建 file 节点: {file_node} ← {file_path}")
            return True
        except Exception as e:
            print(f"[HdriImporter] 创建 file 节点失败: {e}")
            return False


def list_texture_names(zasset_path: str) -> list:
    """列出 .zasset 中 textures/ 下的所有贴图文件名（含精度子目录如 2K/）"""
    names = []
    tex_dir = os.path.join(zasset_path, "textures")
    if os.path.isdir(tex_dir):
        try:
            for root, dirs, files in os.walk(tex_dir):
                for fname in files:
                    rel_path = os.path.relpath(os.path.join(root, fname), tex_dir)
                    names.append(rel_path.replace("\\", "/"))
        except OSError:
            pass
    return names


def import_texture_by_name(zasset_path: str, texture_name: str) -> bool:
    """从 .zasset 中导入指定名称的贴图，创建 file texture node

    Args:
        zasset_path: .zasset 文件夹路径
        texture_name: textures/ 下的相对路径（如 "diff.png" 或 "2K/diff.png"）

    Returns:
        成功返回 True
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        print("[TextureImporter] 未在 Maya 环境中运行")
        return False

    fpath = os.path.join(zasset_path, "textures", texture_name)
    if not os.path.isfile(fpath):
        print(f"[TextureImporter] 贴图不存在: {texture_name}")
        return False

    try:
        file_path = _copy_texture_to_sourceimages(zasset_path, fpath)
        ext = os.path.splitext(file_path)[1].lower()
        cs = _color_space_for_ext(ext)
        file_node = _import_file_node(file_path, cs)
        print(f"[TextureImporter] 创建 file node: {file_node} ← {file_path}")
        return True
    except Exception as e:
        print(f"[TextureImporter] 导入贴图失败: {e}")
        return False


def _color_space_for_ext(ext: str) -> str:
    """根据扩展名推荐色彩空间"""
    sdr_exts = {".png", ".jpg", ".jpeg", ".tga", ".tiff", ".tif", ".bmp", ".psd"}
    hdr_exts = {".exr", ".hdr"}
    if ext in sdr_exts:
        return "sRGB"
    if ext in hdr_exts:
        return "Raw"
    return ""


def import_textures_shared_uv(zasset_path: str, texture_names: list):
    """批量导入贴图，共享一个 place2dTexture 节点以统一调整 UV。

    Args:
        zasset_path: .zasset 文件夹路径
        texture_names: textures/ 下的相对路径列表（如 ["2K/diff.jpg", "2K/norm.exr"]）

    Returns:
        (file_nodes: list, place2d_node: str) 或 None
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        print("[TextureImporter] 未在 Maya 环境中运行")
        return None

    if not texture_names:
        return None

    # 创建一个共享的 place2dTexture
    p2d = cmds.shadingNode("place2dTexture", asUtility=True,
                           name="place2d_shared_import")

    file_nodes = []
    for texture_name in texture_names:
        fpath = os.path.join(zasset_path, "textures", texture_name)
        if not os.path.isfile(fpath):
            print(f"[TextureImporter] 贴图不存在: {texture_name}")
            continue

        try:
            file_path = _copy_texture_to_sourceimages(zasset_path, fpath)
            ext = os.path.splitext(file_path)[1].lower()
            cs = _color_space_for_ext(ext)

            file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
            cmds.setAttr(f"{file_node}.fileTextureName", file_path, type="string")
            cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
            if cs:
                try:
                    cmds.setAttr(f"{file_node}.colorSpace", cs, type="string")
                except Exception:
                    pass

            # 连接到共享 place2dTexture
            cmds.connectAttr(f"{p2d}.coverage", f"{file_node}.coverage")
            cmds.connectAttr(f"{p2d}.translateFrame", f"{file_node}.translateFrame")
            cmds.connectAttr(f"{p2d}.rotateFrame", f"{file_node}.rotateFrame")
            cmds.connectAttr(f"{p2d}.mirrorU", f"{file_node}.mirrorU")
            cmds.connectAttr(f"{p2d}.mirrorV", f"{file_node}.mirrorV")
            cmds.connectAttr(f"{p2d}.stagger", f"{file_node}.stagger")
            cmds.connectAttr(f"{p2d}.wrapU", f"{file_node}.wrapU")
            cmds.connectAttr(f"{p2d}.wrapV", f"{file_node}.wrapV")
            cmds.connectAttr(f"{p2d}.repeatUV", f"{file_node}.repeatUV")
            cmds.connectAttr(f"{p2d}.offset", f"{file_node}.offset")
            cmds.connectAttr(f"{p2d}.rotateUV", f"{file_node}.rotateUV")
            cmds.connectAttr(f"{p2d}.noiseUV", f"{file_node}.noiseUV")
            cmds.connectAttr(f"{p2d}.vertexUvOne", f"{file_node}.vertexUvOne")
            cmds.connectAttr(f"{p2d}.vertexUvTwo", f"{file_node}.vertexUvTwo")
            cmds.connectAttr(f"{p2d}.vertexUvThree", f"{file_node}.vertexUvThree")
            cmds.connectAttr(f"{p2d}.vertexCameraOne", f"{file_node}.vertexCameraOne")
            cmds.connectAttr(f"{p2d}.outUV", f"{file_node}.uv")
            cmds.connectAttr(f"{p2d}.outUvFilterSize", f"{file_node}.uvFilterSize")

            file_nodes.append(file_node)
            print(f"[TextureImporter] {texture_name} → {file_node}")
        except Exception as e:
            print(f"[TextureImporter] 导入贴图失败 {texture_name}: {e}")

    print(f"[TextureImporter] 批量导入完成: {len(file_nodes)} 张贴图, 共享 {p2d}")
    return file_nodes, p2d
