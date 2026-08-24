# -*- coding: utf-8 -*-
import maya.cmds as cmds
import json
import os
import uuid
import traceback

# i18n 兼容导入（用户可见提示的翻译）
try:
    from squirrel_asset_manager.utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

# ========== 共享常量 ==========
SKIP_ATTRS = {
    'message', 'caching', 'isHistoricallyInteresting',
    'nodeState', 'binaryPluginAttribute', 'separator',
}
# VP2 / GPU 硬件渲染内部属性：逐帧计算的缓存状态（硬件着色器指针、相机空间量等）。
# 序列化回写会破坏材质硬件/Arnold 求值（Hypershade 预览纯绿、hyperShade 指定失效）。
VP2_INTERNAL_ATTRS = {
    'hardwareShader', 'pointCamera', 'triangleNormalCamera',
    'primitiveId', 'instanceId', 'base', 'raySampler', 'frozen',
}
COMPOUND_CHILD_ENDS = ('R', 'G', 'B', 'A', 'X', 'Y', 'Z')
DEFAULT_COLOR_SPACE = "ACEScg"


def _detect_color_space():
    """自动检测当前渲染器使用的色彩空间"""
    color_space = ""

    if cmds.objExists("defaultColorMgtGlobals"):
        attr_names = cmds.listAttr("defaultColorMgtGlobals") or []
        _log_debug(f"defaultColorMgtGlobals attrs: {[a for a in attr_names if 'space' in a.lower()]}")

    for attr_name in ["workingSpaceName", "defaultInputSpaceName", "renderingSpace", "workingSpace", "preferredRenderingSpace", "preferredWorkingSpace"]:
        try:
            if cmds.attributeQuery(attr_name, node="defaultColorMgtGlobals", exists=True):
                result = cmds.getAttr(f"defaultColorMgtGlobals.{attr_name}")
                if result and isinstance(result, str) and result.strip():
                    color_space = result.strip()
                    _log_debug(f"[色彩空间检测] defaultColorMgtGlobals.{attr_name} = {color_space}")
                    return color_space
        except Exception:
            continue

    if not color_space or color_space == "sRGB":
        color_space = DEFAULT_COLOR_SPACE
        _log_debug(f"[色彩空间检测] 未检测到，使用默认: {color_space}")

    return color_space

def _log_debug(msg):
    pass

def _log_warning(msg):
    cmds.warning(msg)


def _get_export_header(color_space=None):
    """获取导出文件头部信息"""
    from datetime import datetime
    import maya.cmds as cmds

    current_renderer = "unknown"
    try:
        current_renderer = cmds.getAttr("defaultRenderGlobals.currentRenderer")
    except Exception as e:
        _log_debug(f"获取渲染器失败: {e}")

    if not color_space:
        color_space = _detect_color_space()

    software_version = "Maya"
    try:
        ver = cmds.about(version=True)  # 返回 "2025" 或 "2025.2"
        software_version = f"Maya {ver}"
    except Exception:
        try:
            import maya
            software_version = f"Maya {maya.Version}"
        except Exception:
            pass

    return {
        "version": "2.0",
        "software": software_version,
        "renderer": current_renderer,
        "color_space": color_space,
        "create_date": datetime.now().strftime("%Y-%m-%d")
    }




def _get_material_metadata(material_name, category=None, tags=None, name_cn=None):
    """获取材质的元数据信息"""
    if not cmds.objExists(material_name):
        return {}
    node_type = cmds.nodeType(material_name)
    
    # 处理tags输入
    tags_list = []
    if tags:
        tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
    
    # 确定中文名称（如果未指定则使用英文原名）
    cn_name = name_cn if (name_cn and name_cn.strip()) else material_name
    
    return {
        "name": material_name,
        "name_cn": cn_name,
        "node_type": node_type,
        "category": category or "",
        "tags": tags_list
    }


def _build_metadata_json(materials, color_space=None, category=None, tags=None, name_cn=None):
    """构建元数据JSON结构（不含nodes数据）
    
    单材质时展平到根层级，多材质时保持materials数组。
    """
    header = _get_export_header(color_space)
    export_id = str(uuid.uuid4())
    
    if len(materials) == 1:
        mat_meta = _get_material_metadata(materials[0], category, tags, name_cn)
        return {
            "id": export_id,
            "version": header['version'],
            "software": header['software'],
            "renderer": header['renderer'],
            "color_space": header['color_space'],
            "create_date": header['export_date'],
            "name": mat_meta['name'],
            "name_cn": mat_meta['name_cn'],
            "node_type": mat_meta['node_type'],
            "category": mat_meta['category'],
            "tags": mat_meta['tags']
        }
    else:
        mats_meta = []
        for mat in materials:
            mat_meta = _get_material_metadata(mat, category, tags, name_cn)
            mat_meta['id'] = str(uuid.uuid4())
            mats_meta.append(mat_meta)
        
        return {
            "id": export_id,
            "version": header['version'],
            "software": header['software'],
            "renderer": header['renderer'],
            "color_space": header['color_space'],
            "create_date": header['export_date'],
            "materials": mats_meta
        }


def _collect_texture_paths_from_nodes(nodes_data):
    """从序列化的节点数据中收集所有文件纹理路径"""
    texture_paths = set()
    for node_name, node_info in nodes_data.items():
        if node_info.get('node_type') == 'file':
            for attr_name, attr_data in node_info.get('attrs', {}).items():
                if attr_data.get('type') == 'value' and isinstance(attr_data.get('value'), str):
                    val = attr_data['value']
                    if val and os.path.isfile(val):
                        texture_paths.add(val.replace('\\', '/'))
    return sorted(texture_paths)


def _pack_textures_and_replace(nodes_data, textures_dir, material_dir=None):
    """拷贝纹理到textures文件夹并替换节点数据中的路径为相对路径"""
    texture_paths = _collect_texture_paths_from_nodes(nodes_data)
    if not texture_paths:
        return {}

    if not os.path.exists(textures_dir):
        os.makedirs(textures_dir)

    path_map = {}
    for src_path in texture_paths:
        src_path_normalized = src_path.replace('\\', '/')
        basename = os.path.basename(src_path)
        dst_path = os.path.join(textures_dir, basename).replace('\\', '/')
        # 重名处理
        dst_base, dst_ext = os.path.splitext(basename)
        counter = 1
        while os.path.exists(dst_path) and os.path.normpath(src_path).lower() != os.path.normpath(dst_path).lower():
            dst_path = os.path.join(textures_dir, f"{dst_base}_{counter:03d}{dst_ext}").replace('\\', '/')
            counter += 1
        try:
            import shutil
            if not os.path.exists(dst_path) or os.path.getmtime(src_path) != os.path.getmtime(dst_path):
                shutil.copy2(src_path, dst_path)
            new_basename = os.path.basename(dst_path)
            rel_path = "textures/" + new_basename
            path_map[src_path_normalized] = rel_path
        except Exception as e:
            _log_debug(f"拷贝纹理失败 {src_path}: {e}")

    for node_name, node_info in nodes_data.items():
        if node_info.get('node_type') == 'file':
            for attr_name, attr_data in node_info.get('attrs', {}).items():
                if attr_data.get('type') == 'value' and isinstance(attr_data.get('value'), str):
                    val = attr_data['value'].replace('\\', '/')
                    if val in path_map:
                        attr_data['value'] = path_map[val]

    return path_map


def _collect_texture_paths_from_materials(materials):
    """从 Maya 材质节点收集文件纹理路径（全属性递归扫描）。

    使用 texture_utils 模块的全属性递归扫描替代原来的
    listHistory + file 节点检查，支持:
    - 所有渲染器贴图节点（file / aiImage / RedshiftNormalMap / ...）
    - 帧序列 / UDIM 贴图模式
    - displacement / bump 多路径收集
    """
    try:
        from squirrel_asset_manager.core.texture_utils import collect_textures_from_materials
        return sorted(collect_textures_from_materials(list(materials)))
    except ImportError:
        # 回退到内联实现（非插件环境）
        pass

    # ── 内联回退：保持兼容性 ──
    texture_paths = set()
    for mat in materials:
        try:
            history = cmds.listHistory(mat, allConnections=True) or []
            for node in history:
                if cmds.nodeType(node) == 'file':
                    try:
                        ftn = cmds.getAttr(node + '.fileTextureName')
                        if ftn and os.path.isfile(ftn):
                            texture_paths.add(ftn.replace('\\', '/'))
                    except Exception:
                        pass

            # 额外收集 displacement 贴图
            try:
                ses = cmds.listConnections(mat + '.outColor', type='shadingEngine') or []
                for se in ses:
                    disp_nodes = cmds.listConnections(se + '.displacementShader') or []
                    for dn in disp_nodes:
                        disp_history = cmds.listHistory(dn, allConnections=True) or []
                        for node in disp_history:
                            if cmds.nodeType(node) == 'file':
                                try:
                                    ftn = cmds.getAttr(node + '.fileTextureName')
                                    if ftn and os.path.isfile(ftn):
                                        texture_paths.add(ftn.replace('\\', '/'))
                                except Exception:
                                    pass
            except Exception:
                pass
        except Exception:
            pass
    return sorted(texture_paths)


def _pack_textures_from_materials(materials, textures_dir):
    """从Maya材质直接拷贝纹理到文件夹"""
    texture_paths = _collect_texture_paths_from_materials(materials)
    if not texture_paths:
        return {}

    if not os.path.exists(textures_dir):
        os.makedirs(textures_dir)

    path_map = {}
    for src_path in texture_paths:
        basename = os.path.basename(src_path)
        dst_path = os.path.join(textures_dir, basename).replace('\\', '/')
        # 重名处理：同名文件追加 _001 _002
        dst_base, dst_ext = os.path.splitext(basename)
        counter = 1
        while os.path.exists(dst_path) and os.path.normpath(src_path).lower() != os.path.normpath(dst_path).lower():
            dst_path = os.path.join(textures_dir, f"{dst_base}_{counter:03d}{dst_ext}").replace('\\', '/')
            counter += 1
        try:
            import shutil
            if not os.path.exists(dst_path) or os.path.getmtime(src_path) != os.path.getmtime(dst_path):
                shutil.copy2(src_path, dst_path)
            new_basename = os.path.basename(dst_path)
            path_map[src_path.replace('\\', '/')] = "textures/" + new_basename
        except Exception as e:
            _log_debug(f"拷贝纹理失败 {src_path}: {e}")

    return path_map


def _replace_texture_paths_in_ma(ma_filepath, textures_dir, materials):
    """在导出的MA文件中替换纹理路径为相对路径"""
    path_map = _pack_textures_from_materials(materials, textures_dir)
    if not path_map:
        return

    with open(ma_filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    for old_path, new_path in path_map.items():
        content = content.replace(old_path, new_path)
        alt_old = old_path.replace('/', '\\\\')
        if alt_old in content:
            content = content.replace(alt_old, new_path)

    with open(ma_filepath, 'w', encoding='utf-8') as f:
        f.write(content)


def _resolve_texture_path(texture_path, json_filepath):
    """解析纹理路径：如果是相对路径，基于JSON文件所在目录解析为绝对路径"""
    if not texture_path:
        return texture_path
    tex = texture_path.replace('\\', '/')
    if tex.startswith('textures/') or tex.startswith('.\\') or tex.startswith('./'):
        json_dir = os.path.dirname(json_filepath).replace('\\', '/')
        resolved = os.path.normpath(os.path.join(json_dir, tex)).replace('\\', '/')
        return resolved
    return tex


def _process_texture_paths_in_nodes(nodes_data, json_filepath, copy_textures):
    """导入时处理节点数据中的纹理路径
    
    Args:
        nodes_data: dict of serialized node data
        json_filepath: path to the JSON file being imported
        copy_textures: if True, copy textures to Maya project sourceimages
    
    Returns:
        list of copied textures (if copy_textures=True)
    """
    if not nodes_data:
        return []
    
    for node_name, node_info in nodes_data.items():
        if node_info.get('node_type') == 'file':
            for attr_name, attr_data in node_info.get('attrs', {}).items():
                if attr_data.get('type') == 'value' and isinstance(attr_data.get('value'), str):
                    val = attr_data['value']
                    # Check if this is a texture path (relative or absolute file path)
                    if val and ('textures/' in val.replace('\\', '/') or os.path.isfile(val) or val.startswith('textures/')):
                        resolved = _resolve_texture_path(val, json_filepath)
                        if copy_textures:
                            try:
                                if os.path.isfile(resolved):
                                    import shutil
                                    project_dir = cmds.workspace(query=True, rootDirectory=True) or os.path.dirname(json_filepath)
                                    sourceimages_dir = os.path.join(project_dir, 'sourceimages').replace('\\', '/')
                                    if not os.path.exists(sourceimages_dir):
                                        os.makedirs(sourceimages_dir)
                                    basename = os.path.basename(resolved)
                                    dst = os.path.join(sourceimages_dir, basename).replace('\\', '/')
                                    if not os.path.exists(dst) or os.path.getmtime(resolved) != os.path.getmtime(dst):
                                        shutil.copy2(resolved, dst)
                                    attr_data['value'] = dst
                                else:
                                    attr_data['value'] = resolved
                            except Exception as e:
                                _log_debug(f"拷贝纹理失败 {resolved}: {e}")
                                attr_data['value'] = resolved
                        else:
                            attr_data['value'] = resolved
    return True


def _process_texture_paths_in_maya(json_filepath, copy_textures=False):
    """导入MA后处理Maya中的纹理节点路径"""
    file_nodes = cmds.ls(type='file') or []
    if not file_nodes:
        return
    
    json_dir = os.path.dirname(json_filepath).replace('\\', '/')
    
    for node in file_nodes:
        try:
            current_path = cmds.getAttr(node + '.fileTextureName')
            if not current_path:
                continue
            cur = current_path.replace('\\', '/')
            
            new_path = None
            if 'textures/' in cur:
                resolved = os.path.normpath(os.path.join(json_dir, cur)).replace('\\', '/')
                new_path = resolved
            
            if new_path:
                if copy_textures and os.path.isfile(new_path):
                    try:
                        import shutil
                        project_dir = cmds.workspace(query=True, rootDirectory=True) or json_dir
                        sourceimages_dir = os.path.join(project_dir, 'sourceimages').replace('\\', '/')
                        if not os.path.exists(sourceimages_dir):
                            os.makedirs(sourceimages_dir)
                        basename = os.path.basename(new_path)
                        dst = os.path.join(sourceimages_dir, basename).replace('\\', '/')
                        if not os.path.exists(dst) or os.path.getmtime(new_path) != os.path.getmtime(dst):
                            shutil.copy2(new_path, dst)
                        cmds.setAttr(node + '.fileTextureName', dst, type='string')
                    except Exception as e:
                        _log_debug(f"拷贝纹理失败 {new_path}: {e}")
                        cmds.setAttr(node + '.fileTextureName', new_path, type='string')
                else:
                    cmds.setAttr(node + '.fileTextureName', new_path, type='string')
                print(f"  [纹理路径] {node}: {current_path} -> {new_path}")
        except Exception:
            pass






def _collect_json_files_from_dirs(dir_paths):
    """递归扫描文件夹收集所有材质文件（排除.ameta,.mcm）"""
    json_files = []
    for root_dir in dir_paths:
        if not os.path.isdir(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            for f in files:
                if (f.endswith('.json') or f.endswith('.zmetal')) and not f.endswith('_meta.json') and not f.endswith('.ameta') and not f.endswith('.mcm') and not f.endswith('_objects.json'):
                    full_path = os.path.join(root, f).replace('\\', '/')
                    if full_path not in json_files:
                        json_files.append(full_path)
    return sorted(json_files)


# ========== 共享辅助函数 ==========

def _replace_shape_path(shape_name, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None):
    """统一的模型路径前缀/后缀替换函数"""
    if not shape_name:
        return shape_name
    
    replaced = shape_name
    
    if old_path_prefix is not None and old_path_prefix != "":
        path_parts = [p for p in replaced.split("|") if p]
        if len(path_parts) >= 2:
            path_parts[-2] = path_parts[-2].replace(old_path_prefix, new_path_prefix if new_path_prefix is not None and new_path_prefix != "" else "")
        if len(path_parts) >= 1:
            path_parts[-1] = path_parts[-1].replace(old_path_prefix, new_path_prefix if new_path_prefix is not None and new_path_prefix != "" else "")
        replaced = "|" + "|".join(path_parts)
    elif new_path_prefix is not None and new_path_prefix != "":
        path_parts = [p for p in replaced.split("|") if p]
        if len(path_parts) >= 2:
            path_parts[-2] = new_path_prefix + path_parts[-2]
        if len(path_parts) >= 1:
            path_parts[-1] = new_path_prefix + path_parts[-1]
        replaced = "|" + "|".join(path_parts)
    
    if old_path_suffix is not None and old_path_suffix != "":
        path_parts = [p for p in replaced.split("|") if p]
        if len(path_parts) >= 2:
            path_parts[-2] = path_parts[-2].replace(old_path_suffix, new_path_suffix if new_path_suffix is not None and new_path_suffix != "" else "")
        if len(path_parts) >= 1:
            path_parts[-1] = path_parts[-1].replace(old_path_suffix, new_path_suffix if new_path_suffix is not None and new_path_suffix != "" else "")
        replaced = "|" + "|".join(path_parts)
    elif new_path_suffix is not None and new_path_suffix != "":
        path_parts = [p for p in replaced.split("|") if p]
        if len(path_parts) >= 2:
            path_parts[-2] = path_parts[-2] + new_path_suffix
        if len(path_parts) >= 1:
            path_parts[-1] = path_parts[-1] + new_path_suffix
        replaced = "|" + "|".join(path_parts)
    
    return replaced


def _get_all_instance_dag_paths(shape_dag_path):
    """获取共享同一shape节点的所有DAG路径（处理实例物体）
    
    当多个transform共享同一个shape节点时（实例复制），
    返回所有DAG路径，确保材质能指定到每个实例。
    """
    if not shape_dag_path or not cmds.objExists(shape_dag_path):
        return [shape_dag_path] if shape_dag_path else []
    
    all_parents = cmds.listRelatives(shape_dag_path, parent=True, allParents=True, fullPath=True) or []
    
    if len(all_parents) <= 1:
        return [shape_dag_path]
    
    shape_name = shape_dag_path.split('|')[-1]
    
    all_paths = []
    for parent in all_parents:
        dag_path = f"{parent}|{shape_name}"
        if cmds.objExists(dag_path) and dag_path not in all_paths:
            all_paths.append(dag_path)
    
    return all_paths if all_paths else [shape_dag_path]


def _find_shape_node(shape_path, user_ns=None, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """查找形状节点的公共函数
    
    匹配策略（按优先级）：
    1. 替换后的长路径精确匹配
    2. 原始长路径精确匹配（处理个别模型未遵循命名规则的情况）
    3. 替换后的transform长路径匹配（找transform再取子shape）
    4. 带命名空间的长路径匹配
    5. 短名称唯一匹配（仅在唯一匹配时启用，避免同名物体误匹配）
    6. 模糊匹配（仅在启用时）
    """
    replaced_shape_name = _replace_shape_path(shape_path, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)

    target_shape = None

    path_parts = [p for p in replaced_shape_name.split("|") if p]
    shape_short_name = path_parts[-1].split(":")[-1] if path_parts else ""

    # 1. 替换后的长路径精确匹配
    if cmds.ls(replaced_shape_name, long=True):
        target_shape = cmds.ls(replaced_shape_name, long=True)[0]

    # 2. 原始长路径精确匹配（处理个别模型未遵循命名规则的情况）
    if not target_shape and (old_path_prefix or old_path_suffix):
        if cmds.ls(shape_path, long=True):
            target_shape = cmds.ls(shape_path, long=True)[0]

    # 3. 替换后的transform长路径匹配
    if not target_shape:
        path_parts = [p for p in replaced_shape_name.split("|") if p]
        if len(path_parts) >= 2:
            transform_path = "|" + "|".join(path_parts[:-1])
        else:
            transform_path = "|" + path_parts[0] if path_parts else ""
        
        if transform_path:
            possible_transforms = cmds.ls(transform_path, long=True) or []
            possible_transforms = [t for t in possible_transforms if cmds.nodeType(t) == "transform"]
            
            if possible_transforms:
                for pt in possible_transforms:
                    children = cmds.listRelatives(pt, children=True, fullPath=True, noIntermediate=True) or []
                    for child in children:
                        if cmds.nodeType(child) in ("mesh", "nurbsCurve", "nurbsSurface"):
                            target_shape = child
                            break
                    if target_shape:
                        break

    # 4. 带命名空间的长路径匹配
    if not target_shape and user_ns:
        path_parts = replaced_shape_name.split("|")
        ns_path_parts = []
        for part in path_parts:
            if part:
                ns_part = user_ns + ":" + part
                ns_path_parts.append(ns_part)
            else:
                ns_path_parts.append("")
        ns_path = "|".join(ns_path_parts)

        if cmds.ls(ns_path, long=True):
            target_shape = cmds.ls(ns_path, long=True)[0]
        else:
            # 尝试带命名空间的transform路径
            ns_path_parts_clean = [p for p in ns_path.split("|") if p]
            if len(ns_path_parts_clean) >= 2:
                ns_transform_path = "|" + "|".join(ns_path_parts_clean[:-1])
                possible_transforms = cmds.ls(ns_transform_path, long=True) or []
                possible_transforms = [t for t in possible_transforms if cmds.nodeType(t) == "transform"]
                for pt in possible_transforms:
                    children = cmds.listRelatives(pt, children=True, fullPath=True, noIntermediate=True) or []
                    for child in children:
                        if cmds.nodeType(child) in ("mesh", "nurbsCurve", "nurbsSurface"):
                            target_shape = child
                            break
                    if target_shape:
                        break

    # 5. 短名称唯一匹配（transform|transformShape 模式，仅在唯一匹配时启用）
    if not target_shape and not user_ns:
        transform_short_name = path_parts[-2].split(":")[-1] if len(path_parts) >= 2 else ""
        if transform_short_name:
            path_patterns = [
                f"*{transform_short_name}|{shape_short_name}",
                f"*{transform_short_name}|{shape_short_name}Shape",
                f"*{transform_short_name}|{shape_short_name}Shape1",
            ]
            for pattern in path_patterns:
                matched = cmds.ls(pattern, long=True) or []
                if len(matched) == 1:
                    target_shape = matched[0]
                    break

        if not target_shape:
            matched_shapes = cmds.ls(shape_short_name, long=True) or []
            if len(matched_shapes) == 1:
                target_shape = matched_shapes[0]
            else:
                short_without_ns = shape_short_name.split(":")[-1] if ":" in shape_short_name else shape_short_name
                if short_without_ns != shape_short_name:
                    fallback_matches = cmds.ls(short_without_ns, long=True) or []
                    if len(fallback_matches) == 1:
                        target_shape = fallback_matches[0]

    # 6. 模糊匹配（仅在明确启用且其他方法都失败时）
    if not target_shape and fuzzy_match:
        fuzzy_result = _fuzzy_find_shape(shape_path, user_ns, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)
        if fuzzy_result:
            target_shape = fuzzy_result

    return target_shape


def _fuzzy_find_shape(shape_path, user_ns=None, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None):
    """模糊匹配查找形状节点 - 当精确匹配失败时，通过名称子串包含关系进行匹配
    
    匹配策略：
    1. 对导出路径和场景路径的每一层级进行子串匹配
    2. 长路径层级匹配得分：路径层级越接近得分越高
    3. transform名称和shape名称的子串包含关系匹配
    4. 对于实例物体，返回最佳匹配的DAG路径，由调用方通过_get_all_instance_dag_paths获取所有实例
    """
    replaced_shape_name = _replace_shape_path(shape_path, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)
    
    path_parts = [p for p in replaced_shape_name.split("|") if p]
    exported_transform_name = path_parts[-2] if len(path_parts) >= 2 else ""
    exported_shape_name = path_parts[-1] if path_parts else ""
    
    if ":" in exported_transform_name:
        exported_transform_short = exported_transform_name.split(":")[-1]
    else:
        exported_transform_short = exported_transform_name
    
    if ":" in exported_shape_name:
        exported_shape_short = exported_shape_name.split(":")[-1]
    else:
        exported_shape_short = exported_shape_name
    
    exported_path_parts_short = []
    for part in path_parts:
        if ":" in part:
            exported_path_parts_short.append(part.split(":")[-1])
        else:
            exported_path_parts_short.append(part)
    
    all_shapes = cmds.ls(type='mesh', long=True) or []
    
    candidates = []
    for scene_shape in all_shapes:
        scene_parts = [p for p in scene_shape.split("|") if p]
        scene_transform = scene_parts[-2] if len(scene_parts) >= 2 else ""
        scene_shape_name = scene_parts[-1] if scene_parts else ""
        
        if ":" in scene_transform:
            scene_transform_short = scene_transform.split(":")[-1]
        else:
            scene_transform_short = scene_transform
        
        if ":" in scene_shape_name:
            scene_shape_short = scene_shape_name.split(":")[-1]
        else:
            scene_shape_short = scene_shape_name
        
        matched = False
        if scene_transform_short and exported_transform_short:
            if scene_transform_short in exported_transform_short or exported_transform_short in scene_transform_short:
                matched = True
        
        if not matched and scene_shape_short and exported_shape_short:
            shape_core = scene_shape_short.replace("Shape", "").replace("Shape1", "")
            exported_core = exported_shape_short.replace("Shape", "").replace("Shape1", "")
            if shape_core and exported_core:
                if shape_core in exported_core or exported_core in shape_core:
                    matched = True
        
        if matched:
            scene_path_parts_short = []
            for part in scene_parts:
                if ":" in part:
                    scene_path_parts_short.append(part.split(":")[-1])
                else:
                    scene_path_parts_short.append(part)
            
            candidates.append((scene_shape, scene_transform_short, exported_transform_short, scene_path_parts_short, exported_path_parts_short))
    
    if len(candidates) == 1:
        _log_debug(f"[模糊匹配] 唯一匹配: {shape_path} -> {candidates[0][0]}")
        return candidates[0][0]
    elif len(candidates) > 1:
        best = None
        best_score = -1
        
        for scene_shape, scene_t, exported_t, scene_pps, exported_pps in candidates:
            score = 0
            
            if scene_t in exported_t:
                score += 100 - (len(exported_t) - len(scene_t))
            elif exported_t in scene_t:
                score += 100 - (len(scene_t) - len(exported_t))
            
            len_diff = abs(len(scene_t) - len(exported_t))
            score += max(0, 50 - len_diff)
            
            min_len = min(len(scene_t), len(exported_t))
            prefix_match = 0
            for i in range(min_len):
                if scene_t[i] == exported_t[i]:
                    prefix_match += 1
                else:
                    break
            score += prefix_match * 5
            
            scene_words = scene_t.split('_')
            exported_words = exported_t.split('_')
            common_words = set(scene_words) & set(exported_words)
            score += len(common_words) * 20
            
            # 长路径层级匹配得分：比较每一层路径的名称相似度
            # 从后向前比较（越靠近shape的层级权重越高）
            min_depth = min(len(scene_pps), len(exported_pps))
            for i in range(1, min_depth + 1):
                s_part = scene_pps[-i]
                e_part = exported_pps[-i]
                if s_part == e_part:
                    score += 30 * i  # 层级越深（越靠近shape）得分越高
                elif s_part in e_part or e_part in s_part:
                    score += 15 * i  # 子串匹配也有得分
            
            if score > best_score:
                best_score = score
                best = scene_shape
        
        if best:
            _log_debug(f"[模糊匹配] 多候选最佳匹配 (得分: {best_score}): {shape_path} -> {best}")
            return best
    
    return None


def _build_material_map(user_prefix=None, user_suffix=None):
    """构建材质名称映射表的公共函数"""
    # 获取当前场景中的所有材质，用于材质名称映射
    all_materials = cmds.ls(materials=True) or []
    
    # 构建材质名称映射表
    material_map = {}
    for mat in all_materials:
        # 获取材质的基础名称（去掉命名空间）
        mat_base = mat.split(":")[-1]
        # 尝试反向推导原始材质名称
        if user_prefix and mat_base.startswith(user_prefix):
            original_name = mat_base[len(user_prefix):]
            if user_suffix and original_name.endswith(user_suffix):
                original_name = original_name[:-len(user_suffix)]
            material_map[original_name] = mat
            # 添加带命名空间的原始名称映射
            if ':' in mat:
                ns = mat.split(':')[0]
                material_map[f"{ns}:{original_name}"] = mat
        elif user_suffix and mat_base.endswith(user_suffix):
            original_name = mat_base[:-len(user_suffix)]
            material_map[original_name] = mat
            # 添加带命名空间的原始名称映射
            if ':' in mat:
                ns = mat.split(':')[0]
                material_map[f"{ns}:{original_name}"] = mat
        else:
            # 没有前缀后缀时，直接使用原始名称
            material_map[mat_base] = mat
            # 添加完整名称映射
            material_map[mat] = mat
    
    return material_map


def _collect_face_assignment_objects(mapping_data, materials_to_import=None, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """收集所有需要处理面级材质指定的物体路径"""
    objects_with_face_assignments = set()
    for mat_name, info in mapping_data.items():
        # 如果指定了需要导入的材质，则只处理这些材质
        if materials_to_import and mat_name not in materials_to_import:
            continue
        face_assignments = info.get('face_assignments', {})
        if face_assignments:
            for mesh_name in face_assignments:
                possible_shape_paths = []
                if '|' in mesh_name:
                    short_name = mesh_name.split('|')[-1]
                    possible_shape_paths = [
                        f"{mesh_name}|{short_name}Shape",
                        f"{mesh_name}|{short_name}Shape1",
                        mesh_name
                    ]
                else:
                    possible_shape_paths = [mesh_name]
                
                for shape_path in possible_shape_paths:
                    target_shape = _find_shape_node(shape_path, None, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                    if target_shape:
                        all_instance_paths = _get_all_instance_dag_paths(target_shape)
                        for inst_path in all_instance_paths:
                            objects_with_face_assignments.add(inst_path)
                        break

                if not any(obj in objects_with_face_assignments for obj in possible_shape_paths):
                    possible_paths = [
                        mesh_name,
                    ]

                    if '|' in mesh_name:
                        short_name = mesh_name.split('|')[-1]
                        possible_paths.append(short_name)
                        possible_paths.append(f"|{short_name}")

                    for path in possible_paths:
                        replaced_path = _replace_shape_path(path, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)
                        if cmds.objExists(replaced_path):
                            objects_with_face_assignments.add(replaced_path)
                            if '|' in replaced_path:
                                parts = replaced_path.split('|')
                                if len(parts) > 1:
                                    mesh_path = '|'.join(parts[:-1])
                                    if cmds.objExists(mesh_path):
                                        objects_with_face_assignments.add(mesh_path)
                            break

                    if not any(cmds.objExists(p) for p in objects_with_face_assignments) and (old_path_prefix or old_path_suffix or new_path_prefix or new_path_suffix):
                        for path in possible_paths:
                            if cmds.objExists(path):
                                objects_with_face_assignments.add(path)
                                if '|' in path:
                                    parts = path.split('|')
                                    if len(parts) > 1:
                                        mesh_path = '|'.join(parts[:-1])
                                        if cmds.objExists(mesh_path):
                                            objects_with_face_assignments.add(mesh_path)
                                break

    return objects_with_face_assignments


def _should_skip_object_for_face(obj, objects_with_face_assignments, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """检查物体是否有面级材质指定，如果有则应跳过整体指定"""
    if obj in objects_with_face_assignments:
        return True
    check_obj = _replace_shape_path(obj, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)
    if check_obj in objects_with_face_assignments:
        return True

    resolved_shape = _find_shape_node(obj, None, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
    if resolved_shape:
        if resolved_shape in objects_with_face_assignments:
            return True
        resolved_parents = set(cmds.listRelatives(resolved_shape, parent=True, allParents=True, fullPath=True) or [])
        for stored_path in objects_with_face_assignments:
            if '|' in stored_path:
                stored_parent = '|'.join(stored_path.split('|')[:-1])
                if stored_parent in resolved_parents:
                    return True
        all_instance_paths = _get_all_instance_dag_paths(resolved_shape)
        for inst_path in all_instance_paths:
            if inst_path in objects_with_face_assignments:
                return True

    if '|' in obj:
        parts = obj.split('|')
        if len(parts) > 1:
            transform_name = parts[-1]
            possible_shape_paths = [
                f"{obj}|{transform_name}Shape",
                f"{obj}|{transform_name}Shape1"
            ]
            for shape_path in possible_shape_paths:
                if shape_path in objects_with_face_assignments:
                    return True
                replaced_shape_path = _replace_shape_path(shape_path, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)
                if replaced_shape_path in objects_with_face_assignments:
                    return True
                resolved = _find_shape_node(shape_path, None, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                if resolved:
                    if resolved in objects_with_face_assignments:
                        return True
                    resolved_parents = set(cmds.listRelatives(resolved, parent=True, allParents=True, fullPath=True) or [])
                    for stored_path in objects_with_face_assignments:
                        if '|' in stored_path:
                            stored_parent = '|'.join(stored_path.split('|')[:-1])
                            if stored_parent in resolved_parents:
                                return True

    return False


def _filter_objects_by_selection(objects_list, selected_objects, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """根据选择过滤物体列表
    
    核心逻辑：从选择物体出发，收集所有关联的shape路径，
    然后与JSON中的shape路径进行比较。
    """
    selected_shape_set = set()
    selected_transform_set = set()
    
    for sel in selected_objects:
        try:
            node_type = cmds.nodeType(sel)
        except Exception:
            continue
        
        if node_type == 'transform':
            selected_transform_set.add(sel)
            shapes = cmds.listRelatives(sel, shapes=True, fullPath=True, noIntermediate=True) or []
            for shape in shapes:
                selected_shape_set.add(shape)
                for inst in _get_all_instance_dag_paths(shape):
                    selected_shape_set.add(inst)
                    selected_transform_set.add('|'.join(inst.split('|')[:-1]))
        elif node_type in ('mesh', 'nurbsCurve', 'nurbsSurface'):
            selected_shape_set.add(sel)
            for inst in _get_all_instance_dag_paths(sel):
                selected_shape_set.add(inst)
                selected_transform_set.add('|'.join(inst.split('|')[:-1]))
            parent = cmds.listRelatives(sel, parent=True, fullPath=True) or []
            if parent:
                selected_transform_set.add(parent[0])
    
    filtered_objects = []
    for obj in objects_list:
        check_obj_replaced = _replace_shape_path(obj, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix)
        
        # 检查原始路径和替换后的路径是否在选择范围内
        if obj in selected_shape_set or check_obj_replaced in selected_shape_set:
            filtered_objects.append(obj)
            continue
        if obj in selected_transform_set or check_obj_replaced in selected_transform_set:
            filtered_objects.append(obj)
            continue
        
        # 检查路径的 transform 部分
        for check_path in [obj, check_obj_replaced]:
            if '|' in check_path:
                # 提取 transform 路径（去掉最后一个|后的部分）
                transform_path = '|'.join(check_path.split('|')[:-1])
                if transform_path in selected_transform_set:
                    filtered_objects.append(obj)
                    break
        else:
            if fuzzy_match:
                target_shape = _find_shape_node(obj, None, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match=True)
                if target_shape:
                    if target_shape in selected_shape_set:
                        filtered_objects.append(obj)
                        continue
                    for inst_path in _get_all_instance_dag_paths(target_shape):
                        if inst_path in selected_shape_set:
                            filtered_objects.append(obj)
                            break
                        inst_transform = '|'.join(inst_path.split('|')[:-1])
                        if inst_transform in selected_transform_set:
                            filtered_objects.append(obj)
                            break
    
    return filtered_objects


def _get_materials_to_import(objects_files, selected_objects, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """根据选择的物体获取需要导入的材质列表"""
    materials_to_import = set()
    for filepath in objects_files:
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    mapping_data = json.load(f)
                for mat_name, info in mapping_data.items():
                    objects_list = info.get('objects', [])
                    filtered = _filter_objects_by_selection(objects_list, selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                    if filtered:
                        materials_to_import.add(mat_name)
            except Exception: pass
    
    return materials_to_import


def _get_materials_from_mesh(mesh_node):
    """获取指定mesh节点使用的所有材质"""
    materials = []
    try:
        shadingEngines = cmds.listConnections(mesh_node, type='shadingEngine') or []
        for sg in set(shadingEngines):
            connected_materials = cmds.listConnections(sg + '.surfaceShader') or []
            materials.extend(connected_materials)
    except Exception as e:
        _log_debug(f"获取mesh材质失败 {mesh_node}: {e}")
    return list(set(materials))


def _get_assigned_objects(mat):
    """获取指定材质对应的所有模型shape节点"""
    shading_engines = cmds.listConnections(mat, type='shadingEngine') or []
    assigned_objects = []
    for se in shading_engines:
        members = cmds.sets(se, query=True) or []
        for member in members:
            if '.' in member:
                member = member.split('.')[0]
            if not cmds.objExists(member):
                continue
            long_names = cmds.ls(member, long=True)
            if not long_names:
                continue
            long_name = long_names[0]
            if cmds.nodeType(long_name) == 'transform':
                children = cmds.listRelatives(long_name, shapes=True, fullPath=True) or []
                for shape in children:
                    if shape not in assigned_objects:
                        assigned_objects.append(shape)
            elif cmds.nodeType(long_name) != 'shadingEngine':
                if long_name not in assigned_objects:
                    assigned_objects.append(long_name)
    return assigned_objects


def _get_face_material_assignments(selected_meshes=None):
    """获取模型的面级材质指定关系
    
    Args:
        selected_meshes: 可选，指定要处理的mesh节点列表。如果为None，则处理所有mesh节点
    """
    face_assignments = {}
    
    # 确定要处理的mesh节点
    if selected_meshes:
        # 只处理指定的mesh节点
        all_meshes = selected_meshes
    else:
        # 处理所有mesh节点
        all_meshes = cmds.ls(type='mesh', long=True) or []
    
    # 提前构建mesh短名称到长名称的映射，避免重复调用cmds.ls
    mesh_name_map = {}
    for mesh in all_meshes:
        short_name = mesh.split('|')[-1]
        mesh_name_map[short_name] = mesh
    
    for mesh in all_meshes:
        # 检查是否有面级材质指定
        try:
            # 获取所有关联的shadingEngine
            shading_engines = cmds.listConnections(mesh, type='shadingEngine') or []
            
            # 对于每个shadingEngine，检查是否有面级指定
            for se in shading_engines:
                # 获取与shadingEngine关联的材质
                materials = cmds.ls(cmds.listConnections(se, source=True, destination=False), materials=True) or []
                if not materials:
                    continue
                
                material = materials[0]
                
                # 检查是否有面级指定（通过获取faceSet或直接检查）
                # 方法1：尝试获取faceSet
                face_sets = cmds.sets(se, query=True) or []
                
                # 方法2：直接检查面级指定
                if face_sets:
                    for face_set in face_sets:
                        # 检查是否是面集
                        if '.' in face_set and 'f[' in face_set:
                            # 格式：mesh.f[0:10] 或 长路径.f[0:10]
                            mesh_name = face_set.split('.')[0]
                            face_spec = face_set.split('.')[1]
                            
                            # 使用预构建的映射表获取长名称
                            if mesh_name in mesh_name_map:
                                mesh_name = mesh_name_map[mesh_name]
                            # 如果映射表中没有，尝试直接获取
                            elif not mesh_name.startswith('|'):
                                mesh_long_names = cmds.ls(mesh_name, long=True) or []
                                if mesh_long_names:
                                    mesh_name = mesh_long_names[0]
                            
                            # 优化：按材质分组存储面级指定
                            if mesh_name not in face_assignments:
                                face_assignments[mesh_name] = {}
                            
                            if material not in face_assignments[mesh_name]:
                                face_assignments[mesh_name][material] = []
                            
                            face_assignments[mesh_name][material].append(face_spec)
                else:
                    # 尝试直接获取面级指定
                    try:
                        # 获取shadingEngine的所有成员
                        members = cmds.sets(se, query=True) or []
                        for member in members:
                            # 检查是否是面级指定
                            if '.' in member and 'f[' in member:
                                # 格式：mesh.f[0:10] 或 长路径.f[0:10]
                                mesh_name = member.split('.')[0]
                                face_spec = member.split('.')[1]
                                
                                # 使用预构建的映射表获取长名称
                                if mesh_name in mesh_name_map:
                                    mesh_name = mesh_name_map[mesh_name]
                                # 如果映射表中没有，尝试直接获取
                                elif not mesh_name.startswith('|'):
                                    mesh_long_names = cmds.ls(mesh_name, long=True) or []
                                    if mesh_long_names:
                                        mesh_name = mesh_long_names[0]
                                
                                # 优化：按材质分组存储面级指定
                                if mesh_name not in face_assignments:
                                    face_assignments[mesh_name] = {}
                                
                                if material not in face_assignments[mesh_name]:
                                    face_assignments[mesh_name][material] = []
                                
                                face_assignments[mesh_name][material].append(face_spec)
                    except Exception as e:
                        _log_debug(f"直接获取面级指定失败 {mesh}: {e}")
        except Exception as e:
            _log_debug(f"获取面级材质指定失败 {mesh}: {e}")
    
    return face_assignments


def _get_all_shapes_with_material_from_selection(selection):
    """从选择中获取所有关联了材质的shape节点（处理实例物体）"""
    shapes_with_material = []

    for item in selection:
        try:
            item_long = cmds.ls(item, long=True)
            if not item_long:
                continue
            item_long = item_long[0]

            if cmds.nodeType(item_long) == 'mesh':
                shapes_with_material.append(item_long)
            elif cmds.nodeType(item_long) == 'transform':
                descendants = cmds.listRelatives(item_long, shapes=True, fullPath=True, noIntermediate=True) or []
                for shape in descendants:
                    if cmds.nodeType(shape) == 'mesh':
                        shapes_with_material.append(shape)
            elif cmds.nodeType(item_long) == 'shadingEngine':
                members = cmds.sets(item_long, query=True) or []
                for member in members:
                    if '.' in member:
                        member = member.split('.')[0]
                    if not cmds.objExists(member):
                        continue
                    member_long = cmds.ls(member, long=True)
                    if not member_long:
                        continue
                    member_long = member_long[0]
                    if cmds.nodeType(member_long) == 'transform':
                        children = cmds.listRelatives(member_long, shapes=True, fullPath=True, noIntermediate=True) or []
                        for shape in children:
                            if shape not in shapes_with_material:
                                shapes_with_material.append(shape)
                    elif cmds.nodeType(member_long) == 'mesh':
                        if member_long not in shapes_with_material:
                            shapes_with_material.append(member_long)
            elif cmds.objectType(item_long, isa='material'):
                shadingEngines = cmds.listConnections(item_long, type='shadingEngine') or []
                for se in shadingEngines:
                    materials_in_se = cmds.ls(cmds.listConnections(se, source=True, destination=False), materials=True) or []
                    for mat in materials_in_se:
                        if mat == item_long:
                            members = cmds.sets(se, query=True) or []
                            for member in members:
                                if '.' in member:
                                    member = member.split('.')[0]
                                if not cmds.objExists(member):
                                    continue
                                member_long = cmds.ls(member, long=True)
                                if not member_long:
                                    continue
                                member_long = member_long[0]
                                if cmds.nodeType(member_long) == 'transform':
                                    children = cmds.listRelatives(member_long, shapes=True, fullPath=True, noIntermediate=True) or []
                                    for shape in children:
                                        if shape not in shapes_with_material:
                                            shapes_with_material.append(shape)
                                elif cmds.nodeType(member_long) == 'mesh':
                                    if member_long not in shapes_with_material:
                                        shapes_with_material.append(member_long)
        except Exception: pass

    return shapes_with_material


def _get_materials_from_selection(selection):
    """根据选择获取所有关联的材质（支持模型和材质混合选择，处理实例物体，支持直接选择材质节点）"""
    materials = []
    shape_selection = []

    material_types = {'aiStandardSurface', 'standardSurface', 'lambert', 'blinn', 'phong', 'openPBRSurface', 'pxrSurface', 'aiHair', 'aiSkin', 'aiVolume'}

    for item in selection:
        try:
            item_long = cmds.ls(item, long=True)
            if not item_long:
                continue
            item_long = item_long[0]

            node_type = cmds.nodeType(item_long)
            if node_type in material_types:
                materials.append(item_long)
            else:
                shape_selection.append(item)
        except Exception:
            shape_selection.append(item)

    if shape_selection:
        shapes = _get_all_shapes_with_material_from_selection(shape_selection)
        for shape in shapes:
            materials.extend(_get_materials_from_mesh(shape))

    return list(set(materials))


# ========== 全频雷达版：序列化/反序列化函数 ==========

def _get_processable_attrs(node):
    """【终极全频段扫描】——listAttr(every=True) 优先，失败回退默认调用。

    注意：部分 Maya 版本（如 2025 中文版）的 cmds.listAttr 不支持 every 标志，
    会抛「标志 every 无效」。此时回退到默认 listAttr(node)（已含全部可见属性），
    避免属性集为空导致 zmetal 导出丢失材质属性。
    """
    all_attrs = set()
    node_type = cmds.nodeType(node)

    # every=True 已包含全部属性（标准/隐藏/不可写/动态）
    try:
        attrs = cmds.listAttr(node, every=True) or []
        if attrs:
            all_attrs.update(attrs)
    except Exception:
        pass

    # every=True 不可用或返回空 → 回退默认调用（可见属性）
    if not all_attrs:
        try:
            all_attrs.update(cmds.listAttr(node) or [])
        except Exception:
            pass

    # PBR 属性兜底：从 every=True 结果中筛选，无需逐个 objExists
    pbr_attrs = {
        'baseColor', 'metallic', 'roughness', 'specularIOR', 'specularStrength',
        'emissiveColor', 'opacity', 'normal', 'displacement',
        'baseColorTexture', 'metallicTexture', 'roughnessTexture', 'specularTexture',
        'emissiveTexture', 'opacityTexture', 'normalTexture', 'displacementTexture',
        'color', 'transparency', 'incandescence', 'ambientColor',
        'diffuse', 'specular', 'specularColor', 'glossiness',
        'reflectivity', 'reflectionColor', 'refraction', 'refractionColor',
    }
    # every=True 返回所有子属性名称，复合属性名可能不在列表中
    # 通过检查子属性来推断父属性存在
    for pbr_name in pbr_attrs:
        for suffix in ('R', 'G', 'B', 'A'):
            if pbr_name + suffix in all_attrs:
                all_attrs.add(pbr_name)
                break

    filtered = set()
    for attr in all_attrs:
        if attr in SKIP_ATTRS or attr in VP2_INTERNAL_ATTRS or attr.startswith('out') or '[' in attr or '.' in attr:
            continue
        is_child = False
        if len(attr) >= 2 and attr[-1] in COMPOUND_CHILD_ENDS:
            parent = attr[:-1]
            if parent not in SKIP_ATTRS and parent not in VP2_INTERNAL_ATTRS \
                    and not parent.startswith('out') and '.' not in parent:
                filtered.add(parent)
                is_child = True
        if not is_child:
            filtered.add(attr)

    return list(filtered)


def _get_attr_value(node, attr):
    """暴力数据提取器"""
    full_attr = f"{node}.{attr}"
    attr_type = None

    try:
        attr_type = cmds.getAttr(full_attr, type=True)
    except Exception: pass

    try:
        if attr_type == 'enum':
            try:
                return cmds.getAttr(full_attr, asString=True)
            except Exception: pass

        val = cmds.getAttr(full_attr)
        if val is None:
            return None

        def flatten(value):
            if isinstance(value, (list, tuple)):
                if len(value) == 1 and isinstance(value[0], (list, tuple)):
                    return flatten(value[0])
                return [flatten(item) for item in value]
            return value

        if isinstance(val, (list, tuple)):
            flattened = flatten(val)
            if all(isinstance(item, (int, float)) for item in flattened):
                return flattened
            return list(val)
        else:
            return val
    except Exception: pass

    return None


# 场景全局/默认节点类型：导出时跳过，避免导入重建出重复的全局节点
# （如 defaultColorMgtGlobals → colorManagementGlobals 被重建为 defaultColorMgtGlobals1）
_SCENE_GLOBAL_TYPES = {
    'colorManagementGlobals', 'renderGlobals', 'renderGlobalsList',
    'renderLayerManager', 'renderLayer', 'renderSetupLayer',
    'displayLayerManager', 'displayLayer', 'displayLayer',
    'time', 'dynController', 'lightLinker', 'multilister',
    'renderGlobalsSource', 'defaultLightList',
}
# 已知默认实例名（Maya 新建场景即存在，非 default 前缀）
_SCENE_DEFAULT_NAMES = {
    'time1', 'lambert1', 'standardSurface1', 'particleCloud1',
    'initialShadingGroup', 'initialParticleSE',
    'persp', 'top', 'front', 'side', 'lightLinker1',
    'layerManager', 'defaultLayer', 'defaultRenderLayer',
    'defaultObjectSet', 'defaultLightSet', 'defaultResolution',
    'defaultRenderQuality', 'defaultFontList', 'defaultRenderGlobals',
    'defaultColorMgtGlobals', 'defaultHardwareRenderGlobals',
    'defaultHardwareRenderingGlobals', 'defaultBakeLayer',
    'defaultBakeLayerManager', 'defaultBakeLayer1',
}


def _is_scene_global_node(node: str) -> bool:
    """判断节点是否为 Maya 场景全局/默认节点（导出时跳过，导入时不应重建）。"""
    try:
        ntype = cmds.nodeType(node)
    except Exception:
        ntype = ""
    if ntype in _SCENE_GLOBAL_TYPES:
        return True
    short = node.split(":")[-1].split("|")[-1]
    if short.startswith("default"):
        return True
    return short in _SCENE_DEFAULT_NAMES


def _serialize_node(node, nodes_dict):
    if node in nodes_dict:
        return
    # 场景全局节点不导出（避免导入时重建出重复的全局节点）
    if _is_scene_global_node(node):
        print(f"[zjg_exporter] 跳过场景全局节点: {node}")
        return
    try:
        node_type = cmds.nodeType(node)
    except Exception:
        print(f"[zjg_exporter] 跳过无法识别的节点: {node}")
        nodes_dict[node] = {'node_type': 'unknown', 'attrs': {}}
        return
    attrs_data = {}

    # ── 一次性获取所有上游连接（替代逐属性 listConnections）──
    # 注意：Maya 中 listConnections(connections=True, plugs=True) 返回的是
    # 扁平列表 [node_plug, other_plug, node_plug, other_plug, ...]，不是 (src, dst) 元组对；
    # 配合 source=True, destination=False 时，node 恒为 destination（连入连接），
    # 每对为 (node_dst_plug, src_plug)。一次 API 调用替代 N 次（N = 属性数，通常 30~100）
    conn_map = {}  # {dest_parent_attr: (source_node, source_attr)}
    try:
        all_conns = cmds.listConnections(
            node, source=True, destination=False,
            connections=True, plugs=True,
        ) or []
        for i in range(0, len(all_conns) - 1, 2):
            dst_plug, src_plug = all_conns[i], all_conns[i + 1]
            if not dst_plug.startswith(node + '.'):
                continue  # 防御：dst 侧必须是当前节点
            try:
                dst_attr = dst_plug.rsplit('.', 1)[1]
                src_node, src_attr = src_plug.rsplit('.', 1)
            except Exception:
                continue
            # 复合属性子通道（R/G/B/A/X/Y/Z）→ 映射到父属性
            if len(dst_attr) >= 2 and dst_attr[-1] in COMPOUND_CHILD_ENDS:
                parent = dst_attr[:-1]
                if parent not in conn_map:
                    conn_map[parent] = (src_node, src_attr)
            else:
                if dst_attr not in conn_map:
                    conn_map[dst_attr] = (src_node, src_attr)
    except Exception:
        pass

    # ── 遍历属性：连接信息从 conn_map 查找，不需要逐个 listConnections ──
    for attr in _get_processable_attrs(node):
        val = None
        try:
            val = _get_attr_value(node, attr)
        except Exception:
            pass

        if attr in conn_map:
            src_node, src_attr = conn_map[attr]
            if _is_scene_global_node(src_node):
                # 源是场景全局节点 → 不导出连接，退化为静态值（若有）
                if val is not None:
                    attrs_data[attr] = {'type': 'value', 'value': val}
                continue
            _serialize_node(src_node, nodes_dict)
            attrs_data[attr] = {
                'type': 'connection',
                'source_node': src_node,
                'source_attr': src_attr,
            }
            if val is not None:
                attrs_data[attr]['value'] = val
        elif val is not None:
            attrs_data[attr] = {'type': 'value', 'value': val}

    nodes_dict[node] = {'node_type': node_type, 'attrs': attrs_data}

    # ── 置换关联（不序列化 SE，避免导入的 SE 破坏 Maya 对材质的识别）──
    # 置换链在材质下游：material → shadingEngine.displacementShader → displacement节点 → file。
    # 只序列化置换节点及其上游网络，并把「材质 ↔ 置换节点」关联记入材质节点字段
    # displacement_node；导入时由插件用原生 cmds.sets 创建 SE 并链接（hyperShade 可识别）。
    try:
        if cmds.getClassification(node_type, satisfies="shader"):
            for se_node in (cmds.listConnections(
                    node, source=False, destination=True, type='shadingEngine') or []):
                disp_conns = cmds.listConnections(
                    f"{se_node}.displacementShader", source=True, destination=False) or []
                for disp_node in disp_conns:
                    if disp_node == node:
                        continue
                    if disp_node not in nodes_dict:
                        _serialize_node(disp_node, nodes_dict)
                    nodes_dict[node]['displacement_node'] = disp_node
    except Exception:
        pass


def _set_attr_safe(node, attr, value):
    """多级降级暴力写入器"""
    full_attr = f"{node}.{attr}"

    def ensure_flat(value):
        if isinstance(value, list):
            if len(value) == 1 and isinstance(value[0], list):
                return ensure_flat(value[0])
            return value
        return value

    value = ensure_flat(value)

    try:
        attr_type = cmds.getAttr(full_attr, type=True)
        if attr_type == 'enum' and isinstance(value, str):
            enums = cmds.attributeQuery(attr, node=node, listEnum=True)[0].split(':')
            if value in enums:
                value = enums.index(value)
    except Exception: pass

    try:
        cmds.setAttr(full_attr, value)
        return
    except Exception: pass

    try:
        cmds.setAttr(full_attr, value, type='string')
        return
    except Exception: pass

    if isinstance(value, list):
        try:
            cmds.setAttr(full_attr, *value)
            return
        except Exception: pass
        try:
            if len(value) == 16:
                cmds.setAttr(full_attr, *value, type='matrix')
            elif len(value) == 3:
                cmds.setAttr(full_attr, *value, type='double3')
            elif len(value) == 2:
                cmds.setAttr(full_attr, *value, type='double2')
            elif len(value) > 0:
                cmds.setAttr(full_attr, *value, type='doubleArray')
            return
        except Exception: pass

    if isinstance(value, (int, float)):
        try:
            cmds.setAttr(full_attr, float(value))
            return
        except Exception: pass


def _assign_material_to_objects(mat_name, objects_list, user_ns=None, user_prefix=None, user_suffix=None, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """将材质指定给模型物体，返回 (成功数量, 失败物体列表)"""
    if not objects_list:
        return 0, []

    if user_prefix or user_suffix:
        new_mat_name = f"{user_prefix or ''}{mat_name}{user_suffix or ''}"
    else:
        new_mat_name = mat_name

    if not cmds.objExists(new_mat_name):
        return 0, objects_list

    mat_base = new_mat_name.split(":")[-1]
    sg_name = f"{mat_base}SG"

    if cmds.namespace(exists=new_mat_name.split(":")[0] if ":" in new_mat_name else ""):
        ns = new_mat_name.split(":")[0]
        sg_name = f"{ns}:{mat_base}SG"

    if cmds.objExists(sg_name) and cmds.nodeType(sg_name) == 'shadingEngine':
        target_se = sg_name
    else:
        if cmds.objExists(sg_name):
            cmds.delete(sg_name)
        target_se = cmds.sets(renderable=True, empty=True, name=sg_name)

    try:
        cmds.connectAttr(f"{new_mat_name}.outColor", f"{target_se}.surfaceShader", force=True)
    except Exception: pass

    success_count = 0
    assigned_shapes = set()
    failed_objects = []
    for shape_long_name in objects_list:
        target_shape = _find_shape_node(shape_long_name, user_ns, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)

        if target_shape:
            all_instance_paths = _get_all_instance_dag_paths(target_shape)
            shape_assigned = False
            for instance_path in all_instance_paths:
                if instance_path in assigned_shapes:
                    continue
                assigned_shapes.add(instance_path)
                try:
                    old_ses = cmds.listConnections(instance_path, type='shadingEngine') or []
                    for old_se in old_ses:
                        if old_se and old_se != "initialShadingGroup":
                            try:
                                cmds.sets(instance_path, remove=old_se)
                            except Exception: pass
                    cmds.sets(instance_path, forceElement=target_se)
                    success_count += 1
                    shape_assigned = True
                except Exception: pass
            if not shape_assigned:
                failed_objects.append(shape_long_name)
        else:
            failed_objects.append(shape_long_name)

    return success_count, failed_objects


def _assign_face_materials(face_assignments, user_ns=None, user_prefix=None, user_suffix=None, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, fuzzy_match=False):
    """还原面级材质指定"""
    success_count = 0  # 按模型计数，不是按面计数
    
    # 构建材质名称映射表
    material_map = _build_material_map(user_prefix, user_suffix)
    
    for mesh_name, mat_faces_map in face_assignments.items():
        target_mesh = None
        
        possible_shape_paths = []
        if '|' in mesh_name:
            short_name = mesh_name.split('|')[-1]
            possible_shape_paths = [
                f"{mesh_name}|{short_name}Shape",
                f"{mesh_name}|{short_name}Shape1",
                mesh_name
            ]
        else:
            possible_shape_paths = [mesh_name]
        
        for shape_path in possible_shape_paths:
            target_shape = _find_shape_node(shape_path, user_ns, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
            if target_shape:
                target_mesh = target_shape
                break
        
        if target_mesh:
            all_instance_paths = _get_all_instance_dag_paths(target_mesh)
            model_assigned = False
            for original_mat_name, face_specs in mat_faces_map.items():
                new_mat_name = None

                if original_mat_name in material_map:
                    new_mat_name = material_map[original_mat_name]
                else:
                    mat_base = original_mat_name.split(":")[-1]
                    if mat_base in material_map:
                        new_mat_name = material_map[mat_base]

                if not new_mat_name:
                    if user_prefix or user_suffix:
                        mat_base = original_mat_name.split(":")[-1]
                        new_mat_base = f"{user_prefix or ''}{mat_base}{user_suffix or ''}"
                        possible_mats = cmds.ls(new_mat_base, materials=True) or []
                        if possible_mats:
                            new_mat_name = possible_mats[0]
                        else:
                            if ':' in original_mat_name:
                                ns = original_mat_name.split(':')[0]
                                new_mat_name = f"{ns}:{new_mat_base}"
                    else:
                        new_mat_name = original_mat_name

                if not new_mat_name or not cmds.objExists(new_mat_name):
                    possible_mats = cmds.ls(original_mat_name, materials=True) or []
                    if possible_mats:
                        new_mat_name = possible_mats[0]
                    else:
                        mat_base = original_mat_name.split(":")[-1]
                        possible_mats = cmds.ls(f"*{mat_base}*", materials=True) or []
                        if possible_mats:
                            new_mat_name = possible_mats[0]
                        else:
                            continue

                mat_base = new_mat_name.split(":")[-1]
                sg_name = f"{mat_base}SG"

                if cmds.namespace(exists=new_mat_name.split(":")[0] if ":" in new_mat_name else ""):
                    ns = new_mat_name.split(":")[0]
                    sg_name = f"{ns}:{mat_base}SG"

                if cmds.objExists(sg_name) and cmds.nodeType(sg_name) == 'shadingEngine':
                    target_se = sg_name
                else:
                    if cmds.objExists(sg_name):
                        cmds.delete(sg_name)
                    target_se = cmds.sets(renderable=True, empty=True, name=sg_name)

                try:
                    cmds.connectAttr(f"{new_mat_name}.outColor", f"{target_se}.surfaceShader", force=True)
                except Exception: pass

                for instance_path in all_instance_paths:
                    for face_spec in face_specs:
                        face_path = f"{instance_path}.{face_spec}"
                        try:
                            if cmds.objExists(face_path):
                                cmds.sets(face_path, forceElement=target_se)
                                model_assigned = True
                        except Exception as e:
                            _log_debug(f"面级材质指定失败 {face_path}: {e}")

            if model_assigned:
                success_count += 1
    
    return success_count


# ========== 全频雷达版：导出/导入功能 ==========

def radar_export_materials(target_dir=None, custom_name=None, separate_files=False, export_objects=False, color_space=None, category=None, tags=None, export_all=False, name_cn=None, selection_is_material=False, export_metadata=False, create_material_folder=False, pack_textures=False):
    """全频雷达版导出材质"""
    if export_all:
        materials = cmds.ls(materials=True) or []
        materials = [m for m in materials if cmds.objExists(m) and (cmds.getAttr(m + ".aiSurfaceType", checkParameter=True) is not None or cmds.objectType(m) in ['aiStandardSurface', 'standardSurface', 'lambert', 'blinn', 'phong', 'openPBRSurface'] or not cmds.attributeQuery('aiSurfaceType', node=m, exists=True))]
        if not materials:
            all_nodes = cmds.ls(type=['shader', 'surfaceShader'])
            materials = [n for n in all_nodes if cmds.objectType(n) in ['shader', 'surfaceShader']]
        selected_shapes = set()
        for mat in materials:
            assigned = _get_assigned_objects(mat)
            selected_shapes.update(assigned)
    else:
        selection = cmds.ls(sl=True)
        if not selection:
            cmds.warning(t("zjg.select_model_or_material"))
            return

        if selection_is_material:
            materials = [item for item in selection if cmds.objExists(item)]
            if not materials:
                cmds.warning(t("zjg.invalid_export_node"))
                return
            print(f"[radar_export] 选中 {len(materials)} 个可导出节点: {[cmds.nodeType(m) for m in materials]}")
        else:
            materials = _get_materials_from_selection(selection)
            if not materials:
                cmds.warning(t("zjg.no_related_material"))
                return

        selected_shapes = set(_get_all_shapes_with_material_from_selection(selection))

    maya_file = cmds.file(query=True, sceneName=True)
    if maya_file:
        maya_basename = os.path.basename(maya_file)
        default_name = os.path.splitext(maya_basename)[0]
    else:
        default_name = "MaterialData"

    if separate_files:
        face_assignments = _get_face_material_assignments(selected_shapes) if export_objects else {}
        
        for mat in materials:
            data = {
                'root_materials': [mat],
                'nodes': {}
            }
            _serialize_node(mat, data['nodes'])

            material_folder = None
            textures_dir = None
            file_name = custom_name if custom_name else mat
            filepath = None

            if target_dir and os.path.isdir(target_dir):
                if create_material_folder:
                    material_folder = os.path.join(target_dir, file_name).replace("\\", "/")
                    if not os.path.exists(material_folder):
                        os.makedirs(material_folder)
                    textures_dir = os.path.join(material_folder, "textures").replace("\\", "/")
                    if not os.path.exists(textures_dir):
                        os.makedirs(textures_dir)
                    filepath = os.path.join(material_folder, file_name + ".zmetal").replace("\\", "/")
                else:
                    filepath = os.path.join(target_dir, file_name + ".zmetal").replace("\\", "/")

            if not filepath:
                result = cmds.fileDialog2(fileMode=0, fileFilter="Material Files (*.zmetal)", caption=f"导出材质 {mat} 为 .zmetal")
                if result:
                    chosen = result[0]
                    if create_material_folder:
                        parent_dir = os.path.dirname(chosen)
                        base = os.path.splitext(os.path.basename(chosen))[0]
                        material_folder = os.path.join(parent_dir, base).replace("\\", "/")
                        if not os.path.exists(material_folder):
                            os.makedirs(material_folder)
                        textures_dir = os.path.join(material_folder, "textures").replace("\\", "/")
                        if not os.path.exists(textures_dir):
                            os.makedirs(textures_dir)
                        filepath = os.path.join(material_folder, base + ".zmetal").replace("\\", "/")
                    else:
                        filepath = chosen

            if pack_textures and textures_dir is None:
                if not filepath:
                    textures_dir = None
                else:
                    if create_material_folder and material_folder:
                        textures_dir = os.path.join(material_folder, "textures").replace("\\", "/")
                    else:
                        textures_dir = os.path.join(os.path.dirname(filepath), "textures").replace("\\", "/")

            if pack_textures and filepath:
                if not textures_dir:
                    parent = os.path.dirname(filepath)
                    textures_dir = os.path.join(parent, "textures").replace("\\", "/")
                _pack_textures_and_replace(data['nodes'], textures_dir)
                print(f"[纹理打包] {mat}: 纹理 -> {textures_dir}")

            if filepath:
                if not os.path.exists(os.path.dirname(filepath)):
                    os.makedirs(os.path.dirname(filepath))
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                print(f"[材质导出] 成功: 材质 {mat} 及贴图全参数 -> {filepath}")

            if export_metadata:
                meta_data = _build_metadata_json([mat], color_space, category, tags, name_cn)
                meta_filepath = None
                if filepath:
                    if create_material_folder and material_folder:
                        meta_filepath = os.path.join(material_folder, file_name + ".ameta").replace("\\", "/")
                    else:
                        meta_filepath = filepath.replace('.zmetal', '.ameta')
                if not meta_filepath and target_dir and os.path.isdir(target_dir):
                    file_name_meta = file_name + ".ameta"
                    meta_filepath = os.path.join(target_dir, file_name_meta).replace("\\", "/")
                if not meta_filepath:
                    result = cmds.fileDialog2(fileMode=0, fileFilter="Meta Files (*.ameta)", caption=f"导出材质 {mat} 元数据为 .ameta")
                    if result:
                        meta_filepath = result[0]
                if meta_filepath:
                    with open(meta_filepath, 'w', encoding='utf-8') as f:
                        json.dump(meta_data, f, indent=4, ensure_ascii=False)
                    print(f"[元数据导出] 成功: {mat} -> {meta_filepath}")

            if export_objects:
                all_assigned = _get_assigned_objects(mat)
                assigned_objects = [obj for obj in all_assigned if obj in selected_shapes]
                if assigned_objects:
                    mat_face_assignments = {}
                    for mesh_name, mat_faces_map in face_assignments.items():
                        if mat in mat_faces_map:
                            mat_face_assignments[mesh_name] = {mat: mat_faces_map[mat]}
                    
                    objects_data = {
                        mat: {
                            'count': len(assigned_objects),
                            'objects': assigned_objects,
                            'face_assignments': mat_face_assignments
                        }
                    }
                    objects_filepath = None
                    if filepath:
                        if create_material_folder and material_folder:
                            objects_filepath = os.path.join(material_folder, file_name + ".mcm").replace("\\", "/")
                        else:
                            objects_filepath = filepath.replace('.zmetal', '.mcm')
                    if not objects_filepath and target_dir and os.path.isdir(target_dir):
                        file_name_obj = file_name + ".mcm"
                        objects_filepath = os.path.join(target_dir, file_name_obj).replace("\\", "/")
                    if not objects_filepath:
                        result = cmds.fileDialog2(fileMode=0, fileFilter="Mapping Files (*.mcm)", caption=f"导出材质对应模型 {mat} 为 .mcm")
                        if result:
                            objects_filepath = result[0]
                    if objects_filepath:
                        with open(objects_filepath, 'w', encoding='utf-8') as f:
                            json.dump(objects_data, f, indent=4, ensure_ascii=False)
                        print(f"[模型导出] 成功: {mat} 对应 {len(assigned_objects)} 个模型 -> {objects_filepath}")
    else:
        data = {
            'root_materials': materials,
            'nodes': {}
        }
        for mat in materials:
            _serialize_node(mat, data['nodes'])

        if custom_name:
            file_name = custom_name + ".zmetal"
        else:
            file_name = default_name + ".zmetal"

        material_folder = None
        textures_dir = None
        filepath = None
        if target_dir and os.path.isdir(target_dir):
            if create_material_folder:
                base_name = custom_name if custom_name else default_name
                material_folder = os.path.join(target_dir, base_name).replace("\\", "/")
                if not os.path.exists(material_folder):
                    os.makedirs(material_folder)
                textures_dir = os.path.join(material_folder, "textures").replace("\\", "/")
                if not os.path.exists(textures_dir):
                    os.makedirs(textures_dir)
                filepath = os.path.join(material_folder, base_name + ".zmetal").replace("\\", "/")
            else:
                filepath = os.path.join(target_dir, file_name).replace("\\", "/")

        if not filepath:
            result = cmds.fileDialog2(fileMode=0, fileFilter="Material Files (*.zmetal)", caption="导出材质为 .zmetal")
            if result:
                filepath = result[0]

        if pack_textures and filepath:
            if textures_dir is None:
                textures_dir = os.path.join(os.path.dirname(filepath), "textures").replace("\\", "/")
            _pack_textures_and_replace(data['nodes'], textures_dir)
            print(f"[纹理打包] {len(materials)} 个材质纹理 -> {textures_dir}")

        if filepath:
            if not os.path.exists(os.path.dirname(filepath)):
                os.makedirs(os.path.dirname(filepath))
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            print(f"[材质导出] 成功: {len(materials)} 个材质及贴图全参数 -> {filepath}")

        if export_metadata:
            meta_data = _build_metadata_json(materials, color_space, category, tags, name_cn)
            meta_filepath = None
            if filepath:
                if create_material_folder and material_folder:
                    base_name = custom_name if custom_name else default_name
                    meta_filepath = os.path.join(material_folder, base_name + ".ameta").replace("\\", "/")
                else:
                    meta_filepath = filepath.replace('.zmetal', '.ameta')
            if not meta_filepath and target_dir and os.path.isdir(target_dir):
                file_name_meta = (custom_name if custom_name else default_name) + ".ameta"
                meta_filepath = os.path.join(target_dir, file_name_meta).replace("\\", "/")
            if not meta_filepath:
                result = cmds.fileDialog2(fileMode=0, fileFilter="Meta Files (*.ameta)", caption="导出材质元数据为 .ameta")
                if result:
                    meta_filepath = result[0]
            if meta_filepath:
                with open(meta_filepath, 'w', encoding='utf-8') as f:
                    json.dump(meta_data, f, indent=4, ensure_ascii=False)
                print(f"[元数据导出] 成功: {len(materials)} 个材质元数据 -> {meta_filepath}")

        if export_objects:
            material_to_objects = {}
            # 获取面级材质指定（只处理选择的模型）
            face_assignments = _get_face_material_assignments(selected_shapes)
            for mat in materials:
                all_assigned = _get_assigned_objects(mat)
                assigned_objects = [obj for obj in all_assigned if obj in selected_shapes]
                if assigned_objects:
                    # 过滤出只与当前材质相关的面级指定
                    mat_face_assignments = {}
                    for mesh_name, mat_faces_map in face_assignments.items():
                        # 只保留当前材质的面级指定
                        if mat in mat_faces_map:
                            mat_face_assignments[mesh_name] = {mat: mat_faces_map[mat]}
                    
                    material_to_objects[mat] = {
                        'count': len(assigned_objects),
                        'objects': assigned_objects,
                        'face_assignments': mat_face_assignments
                    }
            if material_to_objects:
                objects_filepath = None
                if filepath:
                    if create_material_folder and material_folder:
                        base_name = custom_name if custom_name else default_name
                        objects_filepath = os.path.join(material_folder, base_name + ".mcm").replace("\\", "/")
                    else:
                        objects_filepath = filepath.replace('.zmetal', '.mcm')
                if not objects_filepath and target_dir and os.path.isdir(target_dir):
                    file_name_obj = (custom_name if custom_name else default_name) + ".mcm"
                    objects_filepath = os.path.join(target_dir, file_name_obj).replace("\\", "/")
                if not objects_filepath:
                    result = cmds.fileDialog2(fileMode=0, fileFilter="Mapping Files (*.mcm)", caption="导出材质对应模型为 .mcm")
                    if result:
                        objects_filepath = result[0]
                if objects_filepath:
                    with open(objects_filepath, 'w', encoding='utf-8') as f:
                        json.dump(material_to_objects, f, indent=4, ensure_ascii=False)
                    print(f"[模型导出] 成功: {len(material_to_objects)} 个材质的模型映射 -> {objects_filepath}")


def _get_connected_nodes(node_name, node_info, all_nodes, visited=None):
    result = set()
    if visited is None:
        visited = set()
    if node_name in visited:
        return result
    visited.add(node_name)

    result.add(node_name)

    for attr, attr_data in node_info.get('attrs', {}).items():
        if attr_data.get('type') == 'connection':
            src = attr_data.get('source_node')
            if src in all_nodes:
                result.add(src)
                result.update(_get_connected_nodes(src, all_nodes[src], all_nodes, visited))

    return result

def _radar_import_single_file(filepath, prefix=None, suffix=None, materials_to_import=None, copy_textures=False):
    """导入单个 JSON 文件（全频雷达版）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    nodes_data = data.get('nodes', {})

    if copy_textures:
        _process_texture_paths_in_nodes(nodes_data, filepath, copy_textures=True)
    else:
        _process_texture_paths_in_nodes(nodes_data, filepath, copy_textures=False)

    root_mats = set(data.get('root_materials', []))

    if materials_to_import is not None:
        root_mats = root_mats & materials_to_import
        nodes_to_include = set()
        for mat in root_mats:
            if mat in nodes_data:
                nodes_to_include.update(_get_connected_nodes(mat, nodes_data[mat], nodes_data))
        filtered_nodes = {k: v for k, v in nodes_data.items() if k in nodes_to_include}
        nodes_data = filtered_nodes

    version = data.get('version', '1.0')
    software = data.get('software', 'unknown')
    renderer = data.get('renderer', 'unknown')
    color_space = data.get('color_space', 'sRGB')
    compatibility = data.get('compatibility', [])

    print(f"[材质导入] 版本: {version}, 软件: {software}, 渲染器: {renderer}, 色彩空间: {color_space}")
    if compatibility:
        print(f"[材质导入] 兼容软件: {', '.join(compatibility)}")

    if 'material' in data:
        mat_info = data['material']
        if materials_to_import is None or mat_info.get('name') in materials_to_import:
            print(f"[材质导入] 材质名称: {mat_info.get('name', 'N/A')}, 类型: {mat_info.get('node_type', 'N/A')}")
            print(f"[材质导入] 分类: {mat_info.get('category', 'N/A')}, 标签: {', '.join(mat_info.get('tags', [])) or '无'}")
    elif 'materials' in data:
        for mat_info in data['materials']:
            if materials_to_import is None or mat_info.get('name') in materials_to_import:
                print(f"[材质导入] 材质名称: {mat_info.get('name', 'N/A')}, 类型: {mat_info.get('node_type', 'N/A')}")
                print(f"[材质导入] 分类: {mat_info.get('category', 'N/A')}, 标签: {', '.join(mat_info.get('tags', [])) or '无'}")
    name_map = {}
    failed_nodes = []

    for old_name, info in nodes_data.items():
        ntype, is_root = info['node_type'], old_name in root_mats
        if prefix or suffix:
            new_name = f"{prefix or ''}{old_name}{suffix or ''}"
        else:
            new_name = old_name
        try:
            light_xform = None  # 灯光 transform 父级
            # 灯光 shape 节点：使用 shadingNode(asLight=True) 自动创建完整灯光
            # Maya 会自动注册到 defaultLightList1 / defaultLightSet
            if cmds.getClassification(ntype, satisfies="light"):
                # 尝试多种 shading node 类型名（不同渲染器注册方式不同）
                # V-Ray:   shadingNode("VRayLightRectShape", asLight=True)
                # Arnold:  shadingNode("aiAreaLight", asLight=True)
                cand_types = [ntype]  # 首先尝试原始 shape 类型
                if ntype.endswith('Shape') or ntype.endswith('shape'):
                    cand_types.append(ntype[:-5])  # 去掉 "Shape" 后缀
                light_xform = None
                for st in cand_types:
                    try:
                        light_xform = cmds.shadingNode(st, asLight=True, skipSelect=True)
                        print(f"[Import] shadingNode({st}) 成功")
                        break
                    except Exception:
                        continue

                if light_xform:
                    # shadingNode 成功 → 绝不重命名（渲染器内部引用）
                    shapes = cmds.listRelatives(light_xform, shapes=True) or []
                    new_node = shapes[0] if shapes else light_xform
                    print(f"[Import] 灯光节点: {new_node} (type={ntype}) → {light_xform}")
                else:
                    # shadingNode 全部失败 → 回退到 createNode + 手动注册
                    print(f"[Import] shadingNode 失败 (candidates={cand_types})，回退到 createNode")
                    xform_name = new_name.replace('Shape', '').replace('shape', '')
                    light_xform = cmds.createNode('transform', name=xform_name, skipSelect=True)
                    new_node = cmds.createNode(ntype, name=new_name, parent=light_xform)
                    try:
                        cmds.connectAttr(f"{light_xform}.message", "defaultLightList1.l", nextAvailable=True)
                    except Exception:
                        pass
                    try:
                        cmds.sets(light_xform, add='defaultLightSet')
                    except Exception:
                        pass
                    print(f"[Import] 灯光节点(回退): {new_node} (type={ntype})")
            elif ntype == 'shadingEngine':
                # 导入重建的 shadingEngine 会被 Maya 标记为异常节点：
                # hyperShade 无法指定（物体留在 initialShadingGroup → 视口纯绿）、
                # 材质预览失效。SE 由 hyperShade / 插件在指定材质时原生创建，
                # 这里跳过不导入（兼容旧格式 zmetal 中可能残留的 SE 节点）。
                name_map[old_name] = None
                continue
            else:
                # 材质类节点用 shadingNode(asShader=True) 创建：
                # createNode 生成的材质不被 hyperShade 识别，会导致「应用材质」后
                # 物体仍留在 initialShadingGroup（视口显示绿色）。
                try:
                    if cmds.getClassification(ntype, satisfies="shader"):
                        new_node = cmds.shadingNode(ntype, asShader=True,
                                                    name=new_name, skipSelect=True)
                    else:
                        new_node = cmds.createNode(ntype, name=new_name, skipSelect=True)
                except Exception:
                    new_node = cmds.createNode(ntype, name=new_name, skipSelect=True)
                # 非灯光节点：原有注册逻辑
                # shadingEngine 由 cmds.sets 注册，跳过；shader 需手动注册到
                # defaultShaderList1（hyperShade 依赖它识别材质）
                try:
                    if ntype == 'shadingEngine':
                        pass
                    elif cmds.getClassification(ntype, satisfies="shader"):
                        cmds.connectAttr(f"{new_node}.message", "defaultShaderList1.s", nextAvailable=True)
                    elif cmds.getClassification(ntype, satisfies="texture"):
                        cmds.connectAttr(f"{new_node}.message", "defaultTextureList1.tx", nextAvailable=True)
                    else:
                        cmds.connectAttr(f"{new_node}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
                except Exception as e:
                    print(f"[Import] 节点注册到分类列表失败 [{new_name}]: {e}")
            name_map[old_name] = new_node
        except Exception as e:
            cmds.warning(t("zjg.create_node_failed", name=old_name, error=e))
            name_map[old_name] = None
            failed_nodes.append(old_name)

    for old_name, info in nodes_data.items():
        new_node = name_map.get(old_name)
        if not new_node:
            continue
        for attr, attr_data in info.get('attrs', {}).items():
            if attr_data.get('type') == 'connection':
                src_new = name_map.get(attr_data['source_node'])
                connection_success = False
                if src_new:
                    try:
                        cmds.connectAttr(f"{src_new}.{attr_data['source_attr']}", f"{new_node}.{attr}", force=True)
                        connection_success = True
                    except Exception:
                        # 复合属性（如 multiplyDivide.input2）需连接到子属性（input2X/Y/Z）
                        for suffix in COMPOUND_CHILD_ENDS:
                            child_attr = f"{new_node}.{attr}{suffix}"
                            if cmds.objExists(child_attr):
                                try:
                                    cmds.connectAttr(f"{src_new}.{attr_data['source_attr']}", child_attr, force=True)
                                    connection_success = True
                                except Exception:
                                    pass
                if not connection_success and 'value' in attr_data:
                    _set_attr_safe(new_node, attr, attr_data['value'])
            elif attr_data.get('type') == 'value':
                _set_attr_safe(new_node, attr, attr_data['value'])

    # ── 置换重建：为带 displacement_node 标记的材质用原生方式创建 SE 并链接 ──
    # 导入流程内创建的 shadingEngine 会被 Maya 标记为异常（hyperShade 无法使用），
    # 因此这里用 cmds.sets(renderable=True) 原生创建（等价 hyperShade 自身创建），
    # 链接 surfaceShader + displacementShader，hyperShade 可正常识别。
    # 关联来源：① 新格式 zmetal 的材质 displacement_node 字段；
    #          ② 旧格式 zmetal 的 shadingEngine 节点数据（surfaceShader/displacementShader 连接）推断。
    displacement_map = {}  # 材质old_name → 置换节点old_name
    for old_name, info in nodes_data.items():
        if isinstance(info, dict) and info.get('displacement_node'):
            displacement_map[old_name] = info['displacement_node']
    for old_name, info in nodes_data.items():
        if not isinstance(info, dict):
            continue
        if info.get('node_type') != 'shadingEngine':
            continue
        attrs = info.get('attrs', {}) or {}
        surf = attrs.get('surfaceShader') or {}
        disp = attrs.get('displacementShader') or {}
        mat_old = surf.get('source_node') if isinstance(surf, dict) else None
        disp_old = disp.get('source_node') if isinstance(disp, dict) else None
        if mat_old and disp_old and mat_old not in displacement_map:
            displacement_map[mat_old] = disp_old

    for mat_old, disp_old in displacement_map.items():
        new_mat = name_map.get(mat_old)
        new_disp = name_map.get(disp_old)
        if not new_mat or not new_disp:
            continue
        mat_base = new_mat.split(":")[-1]
        sg_name = f"{mat_base}SG"
        try:
            if cmds.objExists(sg_name) and cmds.nodeType(sg_name) == 'shadingEngine':
                sg = sg_name
            else:
                if cmds.objExists(sg_name):
                    cmds.delete(sg_name)
                sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
            cmds.connectAttr(f"{new_mat}.outColor", f"{sg}.surfaceShader", force=True)
            cmds.connectAttr(f"{new_disp}.displacement", f"{sg}.displacementShader", force=True)
            print(f"[Import] 置换 SE 已重建: {sg} ({new_mat} + {new_disp})")
        except Exception as e:
            print(f"[Import] 置换 SE 重建失败 ({new_mat}): {e}")

    success_count = len(nodes_data) - len(failed_nodes)
    print(f"[材质导入] 完成: {filepath} - {success_count} 个节点已还原。")
    return success_count, name_map


def radar_import_materials(file_paths=None, user_ns=None, user_prefix=None, user_suffix=None, dir_path=None, assign_objects=False, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, import_selection=False, fuzzy_match=False, copy_textures=False):
    """全频雷达版导入材质"""
    json_files = []
    objects_files = []

    def _is_objects_file(path):
        return path.endswith('.mcm') or path.endswith('_objects.json')

    def _to_objects_file(mat_path):
        if mat_path.endswith('.zmetal'):
            return mat_path.replace('.zmetal', '.mcm')
        return mat_path.replace('.json', '_objects.json')

    def _to_material_file(obj_path):
        new = obj_path.rsplit('.mcm', 1)[0] + '.zmetal'
        if os.path.exists(new):
            return new, True
        old = obj_path.rsplit('_objects.json', 1)[0] + '.json'
        if obj_path.endswith('_objects.json') and os.path.exists(old):
            return old, True
        return None, False

    def _find_objects_file(mat_path):
        new = _to_objects_file(mat_path)
        if os.path.exists(new):
            return new
        old = mat_path.replace('.zmetal', '_objects.json').replace('.json', '_objects.json')
        if os.path.exists(old):
            return old
        return None

    if file_paths:
        if isinstance(file_paths, str):
            if _is_objects_file(file_paths):
                objects_files.append(file_paths)
                mat, found = _to_material_file(file_paths)
                if found:
                    json_files.append(mat)
            else:
                json_files.append(file_paths)
                obj = _find_objects_file(file_paths)
                if obj:
                    objects_files.append(obj)
        else:
            for f in file_paths:
                if _is_objects_file(f):
                    objects_files.append(f)
                    mat, found = _to_material_file(f)
                    if found:
                        json_files.append(mat)
                else:
                    json_files.append(f)
                    obj = _find_objects_file(f)
                    if obj:
                        objects_files.append(obj)

    if dir_path and os.path.isdir(dir_path):
        all_json_files = []
        for f in os.listdir(dir_path):
            if f.endswith('.json') or f.endswith('.zmetal') or f.endswith('.mcm'):
                full_path = os.path.join(dir_path, f).replace('\\', '/')
                all_json_files.append(full_path)
        
        processed = set()
        for f in all_json_files:
            if f in processed:
                continue
            if _is_objects_file(f):
                objects_files.append(f)
                mat, found = _to_material_file(f)
                if found and mat in all_json_files:
                    json_files.append(mat)
                    processed.add(mat)
            else:
                json_files.append(f)
                obj = _to_objects_file(f)
                if obj in all_json_files:
                    objects_files.append(obj)
                    processed.add(obj)
                else:
                    obj = f.replace('.json', '_objects.json').replace('.zmetal', '_objects.json')
                    if obj in all_json_files:
                        objects_files.append(obj)
                        processed.add(obj)

    if not json_files and not objects_files:
        app = QApplication.instance()
        if app is None:
            return False

        files = QFileDialog.getOpenFileNames(
            None, '选择材质文件', '', 'Material Files (*.zmetal *.json *.mcm)'
        )[0]

        if not files:
            return False
        
        all_selected = set(files)
        for f in files:
            if _is_objects_file(f):
                objects_files.append(f)
                mat, found = _to_material_file(f)
                if found and mat in all_selected:
                    json_files.append(mat)
                elif not found:
                    mat, _ = _to_material_file(f)
                    if mat in all_selected:
                        json_files.append(mat)
            else:
                json_files.append(f)
                obj = _to_objects_file(f)
                if obj in all_selected:
                    objects_files.append(obj)
                else:
                    obj = f.replace('.json', '_objects.json').replace('.zmetal', '_objects.json')
                    if obj in all_selected:
                        objects_files.append(obj)

    if old_path_prefix is not None:
        print(f"[系统] 路径前缀替换: {old_path_prefix} -> {new_path_prefix if new_path_prefix is not None else ''}")

    if old_path_suffix is not None:
        print(f"[系统] 路径后缀替换: {old_path_suffix} -> {new_path_suffix if new_path_suffix is not None else ''}")

    selected_objects = set()
    materials_to_import = None
    if import_selection:
        selected_objects = set(cmds.ls(sl=True, long=True) or [])
        if selected_objects and objects_files:
            materials_to_import = _get_materials_to_import(objects_files, selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
        elif selected_objects and not objects_files:
            print("[材质导入] 未找到 objects.json 文件，无法按选择过滤材质")

    total_success = 0

    # 导入材质（如果有材质文件）
    if json_files:
        for filepath in json_files:
            if os.path.exists(filepath):
                if materials_to_import is not None:
                    success, name_map = _radar_import_single_file(filepath, user_prefix, user_suffix, materials_to_import, copy_textures)
                else:
                    success, name_map = _radar_import_single_file(filepath, user_prefix, user_suffix, copy_textures=copy_textures)
                total_success += success
            else:
                cmds.warning(t("zjg.file_not_found", path=filepath))

    if assign_objects and objects_files:
        all_failed_objects = []
        for filepath in objects_files:
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        mapping_data = json.load(f)
                    total_assigned = 0

                    # 收集所有需要处理面级材质指定的物体
                    objects_with_face_assignments = _collect_face_assignment_objects(mapping_data, materials_to_import, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)

                    for mat_name, info in mapping_data.items():
                        # 只处理需要导入的材质
                        if materials_to_import and mat_name not in materials_to_import:
                            continue
                        objects_list = info.get('objects', [])
                        if objects_list:
                            if import_selection and selected_objects:
                                objects_list = _filter_objects_by_selection(objects_list, selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)

                            # 过滤掉有面级材质指定的物体
                            filtered_objects = [obj for obj in objects_list
                                             if not _should_skip_object_for_face(obj, objects_with_face_assignments, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)]

                            if filtered_objects:
                                count, failed_objects = _assign_material_to_objects(mat_name, filtered_objects, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                                total_assigned += count
                                all_failed_objects.extend(failed_objects)

                        # 还原面级材质指定
                        face_assignments = info.get('face_assignments', {})
                        if face_assignments:
                            face_count = _assign_face_materials(face_assignments, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                            total_assigned += face_count
                    print(f"[模型指定] 完成: {total_assigned} 个模型已指定材质")
                    if all_failed_objects:
                        print(f"[警告] {len(all_failed_objects)} 个模型材质指定失败")
                        failed_transforms = []
                        for obj in all_failed_objects:
                            if '|' in obj:
                                transform = '|'.join(obj.split('|')[:-1])
                                if cmds.objExists(transform):
                                    failed_transforms.append(transform)
                            elif cmds.objExists(obj):
                                failed_transforms.append(obj)
                        if failed_transforms:
                            cmds.select(failed_transforms, replace=True)
                            print(f"[选择] 已选择 {len(failed_transforms)} 个指定失败的模型")
                except Exception as e:
                    cmds.warning(t("zjg.assign_material_failed", error=e))
    elif assign_objects and not objects_files:
        print("[材质导入] 未找到 objects.json 文件，无法指定材质")

    if json_files:
        print(f"[材质导入] 总计: {len(json_files)} 个文件，{total_success} 个节点已还原。")
    elif objects_files:
        print("[材质导入] 仅指定材质，未导入新材质")

    return True

def _get_short_name(full_name):
    if '|' in full_name:
        return full_name.split('|')[-1]
    if ':' in full_name:
        return full_name.split(':')[-1]
    return full_name


# ========== MA+JSON版：导出/导入功能 ==========

def ma_export_materials(target_dir=None, custom_name=None, separate_files=False, color_space=None, category=None, tags=None, name_cn=None, export_metadata=False, create_material_folder=False, pack_textures=False):
    """MA+JSON版导出材质"""
    transforms = cmds.ls(selection=True, transforms=True)
    if not transforms:
        cmds.warning(t("zjg.select_model_with_material"))
        return False

    materials = set()
    selected_shapes = []
    for tran in transforms:
        shapes = cmds.listRelatives(tran, shapes=True, fullPath=True) or []
        for shape in shapes:
            selected_shapes.append(shape)
            ses = cmds.listConnections(shape, type='shadingEngine') or []
            for se in ses:
                mats = cmds.ls(cmds.listConnections(se, source=True, destination=False), materials=True) or []
                for mat in mats:
                    materials.add(mat)

    if not materials:
        cmds.warning(t("zjg.model_has_no_material"))
        return False

    materials = list(materials)

    maya_file = cmds.file(query=True, sceneName=True)
    if maya_file:
        maya_basename = os.path.basename(maya_file)
        default_name = os.path.splitext(maya_basename)[0]
    else:
        default_name = "MaterialData"

    if separate_files:
        result_data = {}
        face_assignments = _get_face_material_assignments(selected_shapes)
        
        for mat in materials:
            objs = _get_assigned_objects(mat)
            filtered_objs = [obj for obj in objs if obj in selected_shapes]
            
            mat_face_assignments = {}
            for mesh_name, mat_faces_map in face_assignments.items():
                if mat in mat_faces_map:
                    mat_face_assignments[mesh_name] = {mat: mat_faces_map[mat]}
            
            result_data[mat] = {
                "count": len(filtered_objs), 
                "objects": filtered_objs,
                "face_assignments": mat_face_assignments
            }
        
        json_filepath = None
        materials_folder = None
        
        if target_dir and os.path.isdir(target_dir):
            if custom_name:
                json_filename = custom_name + ".zmetal"
                materials_folder_name = custom_name
            else:
                json_filename = "materials.zmetal"
                materials_folder_name = "materials"
            json_filepath = os.path.join(target_dir, json_filename).replace("\\", "/")
            materials_folder = os.path.join(target_dir, materials_folder_name).replace("\\", "/")
        
        if not json_filepath:
            result = cmds.fileDialog2(fileMode=0, fileFilter="Material Files (*.zmetal)", caption="导出材质映射为 .zmetal")
            if result:
                json_filepath = result[0]
                materials_folder_name = os.path.splitext(os.path.basename(json_filepath))[0]
                materials_folder = os.path.join(os.path.dirname(json_filepath), materials_folder_name).replace("\\", "/")
        
        if not create_material_folder:
            if materials_folder and not os.path.exists(materials_folder):
                os.makedirs(materials_folder)

        # 导出每个材质
        for mat in materials:
            if create_material_folder:
                if target_dir and os.path.isdir(target_dir):
                    mat_folder = os.path.join(target_dir, mat).replace("\\", "/")
                else:
                    mat_folder = os.path.join(os.path.dirname(json_filepath), mat).replace("\\", "/")
                if not os.path.exists(mat_folder):
                    os.makedirs(mat_folder)
                textures_dir = os.path.join(mat_folder, "textures").replace("\\", "/")
                if not os.path.exists(textures_dir):
                    os.makedirs(textures_dir)
                ma_filepath = os.path.join(mat_folder, f"{mat}.ma").replace("\\", "/")
            else:
                ma_filepath = os.path.join(materials_folder, f"{mat}.ma").replace("\\", "/")
                textures_dir = None
            
            if pack_textures:
                if textures_dir is None:
                    if create_material_folder:
                        textures_dir = os.path.join(os.path.dirname(ma_filepath), "textures").replace("\\", "/")
                    else:
                        textures_dir = os.path.join(materials_folder, "textures").replace("\\", "/")

            raw_nodes = set()
            raw_nodes.add(mat)
            for node in cmds.listHistory(mat, allConnections=True) or []:
                raw_nodes.add(node)
            
            clean_nodes = []
            for node in raw_nodes:
                inherited_types = cmds.nodeType(node, inherited=True)
                if 'dagNode' not in inherited_types:
                    clean_nodes.append(node)
            
            if clean_nodes:
                try:
                    original_sel = cmds.ls(selection=True)
                    cmds.select(clean_nodes, replace=True)
                    cmds.file(ma_filepath, exportSelected=True, type="mayaAscii", force=True)
                    if original_sel:
                        cmds.select(original_sel, replace=True)
                    else:
                        cmds.select(clear=True)
                    print(f"[MA+JSON导出] 成功: {mat} -> {os.path.basename(ma_filepath)}")

                    if pack_textures:
                        _replace_texture_paths_in_ma(ma_filepath, textures_dir, [mat])
                        print(f"[MA+JSON纹理打包] {mat} -> {textures_dir}")
                except Exception as e:
                    cmds.warning(t("zjg.export_ma_failed", mat=mat, error=e))
        
        if create_material_folder:
            for mat in materials:
                mat_folder = os.path.join(target_dir, mat).replace("\\", "/") if target_dir and os.path.isdir(target_dir) else os.path.join(os.path.dirname(json_filepath), mat).replace("\\", "/")
                mat_json_path = os.path.join(mat_folder, f"{mat}.zmetal").replace("\\", "/")
                try:
                    with open(mat_json_path, 'w', encoding='utf-8') as f:
                        json.dump({mat: result_data[mat]}, f, indent=4, ensure_ascii=False)
                    print(f"[MA+JSON导出] 成功: {mat} 映射 -> {os.path.basename(mat_json_path)}")
                except Exception as e:
                    cmds.warning(t("zjg.write_json_failed", error=e))

                if export_metadata:
                    try:
                        meta_data = _build_metadata_json([mat], color_space, category, tags, name_cn)
                        meta_filepath = os.path.join(mat_folder, f"{mat}.ameta").replace("\\", "/")
                        with open(meta_filepath, 'w', encoding='utf-8') as f:
                            json.dump(meta_data, f, indent=4, ensure_ascii=False)
                        print(f"[MA+JSON元数据导出] 成功: {mat} -> {os.path.basename(meta_filepath)}")
                    except Exception as e:
                        cmds.warning(t("zjg.write_metadata_json_failed", error=e))
        else:
            if json_filepath:
                try:
                    with open(json_filepath, 'w', encoding='utf-8') as f:
                        json.dump(result_data, f, indent=4, ensure_ascii=False)
                    print(f"[MA+JSON导出] 成功: 材质映射 -> {os.path.basename(json_filepath)}")
                except Exception as e:
                    cmds.warning(t("zjg.write_json_failed", error=e))
                    return False

            if export_metadata and json_filepath:
                try:
                    meta_data = _build_metadata_json(materials, color_space, category, tags, name_cn)
                    meta_filepath = json_filepath.replace('.zmetal', '.ameta')
                    with open(meta_filepath, 'w', encoding='utf-8') as f:
                        json.dump(meta_data, f, indent=4, ensure_ascii=False)
                    print(f"[MA+JSON元数据导出] 成功: {len(materials)} 个材质元数据 -> {os.path.basename(meta_filepath)}")
                except Exception as e:
                    cmds.warning(t("zjg.write_metadata_json_failed", error=e))
    else:
        result_data = {}
        # 获取面级材质指定（只处理选择的模型）
        face_assignments = _get_face_material_assignments(selected_shapes)
        for mat in materials:
            objs = _get_assigned_objects(mat)
            # 过滤出只与选择的模型相关的对象
            filtered_objs = [obj for obj in objs if obj in selected_shapes]
            
            # 过滤出只与当前材质相关的面级指定
            mat_face_assignments = {}
            for mesh_name, mat_faces_map in face_assignments.items():
                # 只保留当前材质的面级指定
                if mat in mat_faces_map:
                    mat_face_assignments[mesh_name] = {mat: mat_faces_map[mat]}
            
            result_data[mat] = {
                "count": len(filtered_objs), 
                "objects": filtered_objs,
                "face_assignments": mat_face_assignments
            }

        json_filepath = None
        material_folder = None
        textures_dir = None
        if target_dir and os.path.isdir(target_dir):
            if custom_name:
                file_name = custom_name + ".zmetal"
            else:
                file_name = default_name + ".zmetal"
            if create_material_folder:
                base_name = custom_name if custom_name else default_name
                material_folder = os.path.join(target_dir, base_name).replace("\\", "/")
                if not os.path.exists(material_folder):
                    os.makedirs(material_folder)
                textures_dir = os.path.join(material_folder, "textures").replace("\\", "/")
                if not os.path.exists(textures_dir):
                    os.makedirs(textures_dir)
                json_filepath = os.path.join(material_folder, base_name + ".zmetal").replace("\\", "/")
            else:
                json_filepath = os.path.join(target_dir, file_name).replace("\\", "/")

        if not json_filepath:
            result = cmds.fileDialog2(fileMode=0, fileFilter="Material Files (*.zmetal)", caption="导出材质为 .zmetal")
            if result:
                json_filepath = result[0]

        if json_filepath:
            try:
                with open(json_filepath, 'w', encoding='utf-8') as f:
                    json.dump(result_data, f, indent=4, ensure_ascii=False)
            except Exception as e:
                cmds.warning(t("zjg.write_material_file_failed", error=e))
                return False

            if export_metadata:
                try:
                    meta_data = _build_metadata_json(materials, color_space, category, tags, name_cn)
                    meta_filepath = json_filepath.replace('.zmetal', '.ameta')
                    with open(meta_filepath, 'w', encoding='utf-8') as f:
                        json.dump(meta_data, f, indent=4, ensure_ascii=False)
                    print(f"[MA+JSON元数据导出] 成功: {len(materials)} 个材质元数据 -> {os.path.basename(meta_filepath)}")
                except Exception as e:
                    cmds.warning(t("zjg.write_metadata_json_failed", error=e))

            ma_filepath = json_filepath.rsplit('.', 1)[0] + '.ma'

            raw_nodes = set()
            for mat in materials:
                raw_nodes.add(mat)
                for node in cmds.listHistory(mat, allConnections=True) or []:
                    raw_nodes.add(node)

            clean_nodes = []
            for node in raw_nodes:
                inherited_types = cmds.nodeType(node, inherited=True)
                if 'dagNode' not in inherited_types:
                    clean_nodes.append(node)

            if clean_nodes:
                try:
                    original_sel = cmds.ls(selection=True)
                    cmds.select(clean_nodes, replace=True)
                    cmds.file(ma_filepath, exportSelected=True, type="mayaAscii", force=True)
                    if original_sel:
                        cmds.select(original_sel, replace=True)
                    else:
                        cmds.select(clear=True)
                    print(f"[MA+JSON导出] 成功: {len(materials)} 个材质 -> {os.path.basename(json_filepath)}, {os.path.basename(ma_filepath)}")

                    if pack_textures:
                        if textures_dir is None:
                            textures_dir = os.path.join(os.path.dirname(ma_filepath), "textures").replace("\\", "/")
                        _replace_texture_paths_in_ma(ma_filepath, textures_dir, materials)
                        print(f"[MA+JSON纹理打包] {len(materials)} 个材质纹理 -> {textures_dir}")
                except Exception as e:
                    cmds.warning(t("zjg.export_ma_failed_generic", error=e))
                    return False

    return True


def ma_import_materials(json_path, user_ns=None, user_prefix=None, user_suffix=None, old_path_prefix=None, new_path_prefix=None, old_path_suffix=None, new_path_suffix=None, import_selection=False, fuzzy_match=False, copy_textures=False):
    """MA+JSON版导入材质"""
    if not os.path.exists(json_path):
        cmds.warning(t("zjg.json_file_not_found", path=json_path))
        return False

    # 读取JSON文件
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        cmds.warning(t("zjg.read_json_failed", error=e))
        return False

    # 检查材质文件夹（与JSON文件同名）
    json_dir = os.path.dirname(json_path)
    materials_folder_name = os.path.splitext(os.path.basename(json_path))[0]
    materials_folder = os.path.join(json_dir, materials_folder_name).replace("\\", "/")
    
    # 收集选择的物体
    selected_objects = set()
    if import_selection:
        selected_objects = set(cmds.ls(sl=True, long=True) or [])
        if not selected_objects:
            cmds.warning(t("zjg.no_selection"))
            return False

    # 过滤需要导入的材质
    materials_to_import = set()
    if import_selection and selected_objects:
        # 找出选择物体相关的材质
        for mat_name, info in data.items():
            # 检查对象级材质指定
            for original_shape_name in info.get("objects", []):
                if _filter_objects_by_selection([original_shape_name], selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match):
                    materials_to_import.add(mat_name)
                    break
            # 检查面级材质指定
            face_assignments = info.get("face_assignments", {})
            for mesh_name in face_assignments:
                if _filter_objects_by_selection([mesh_name], selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match):
                    materials_to_import.add(mat_name)
                    break
        
        if not materials_to_import:
            cmds.warning(t("zjg.no_related_material_found"))
            return False
        print(f"[MA+JSON导入] 找到 {len(materials_to_import)} 个与选择物体相关的材质")
    else:
        # 导入所有材质
        materials_to_import = set(data.keys())

    # 收集需要导入的MA文件
    ma_files = []
    if os.path.exists(materials_folder):
        # 新格式：从与JSON同名的文件夹导入对应材质的MA文件
        for mat_name in materials_to_import:
            ma_file = os.path.join(materials_folder, f"{mat_name}.ma").replace("\\", "/")
            if os.path.exists(ma_file):
                ma_files.append(ma_file)
            else:
                print(f"[MA+JSON导入] 警告: 找不到材质 {mat_name} 的 MA 文件")
        
        if not ma_files:
            cmds.warning(t("zjg.no_ma_files_found"))
            return False
    else:
        # 兼容旧格式：尝试查找与JSON同名的MA文件
        ma_path = json_path.rsplit('.', 1)[0] + '.ma'
        if not os.path.exists(ma_path):
            cmds.warning(t("zjg.ma_file_not_found", path=ma_path))
            return False
        ma_files = [ma_path]

    if user_ns:
        print(f"\n[系统] 检测到指定命名空间，将优先使用 [ {user_ns} ] 进行匹配...")

    if user_prefix:
        print(f"[系统] 检测到指定材质前缀，将使用 [ {user_prefix} ] 为导入材质添加前缀...")

    if user_suffix:
        print(f"[系统] 检测到指定材质后缀，将使用 [ {user_suffix} ] 为导入材质添加后缀...")

    if old_path_prefix and new_path_prefix is not None:
        print(f"[系统] 路径前缀替换: {old_path_prefix} -> {new_path_prefix}")

    if old_path_suffix is not None and new_path_suffix is not None:
        print(f"[系统] 路径后缀替换: {old_path_suffix} -> {new_path_suffix}")

    temp_ns_base = "tempMatImport"
    i = 1
    while True:
        current_ns = temp_ns_base if i == 1 else f"{temp_ns_base}{i}"
        if cmds.namespace(exists=current_ns):
            try:
                cmds.namespace(removeNamespace=current_ns, mergeNamespaceWithRoot=False)
            except Exception: pass
            i += 1
        else:
            break

    # 导入所有MA文件
    for ma_file in ma_files:
        try:
            cmds.file(ma_file, i=True, type="mayaAscii", namespace=temp_ns_base, defaultNamespace=False)
            print(f"[MA+JSON导入] 成功导入: {os.path.basename(ma_file)}")
        except Exception as e:
            cmds.warning(t("zjg.import_ma_failed", file=ma_file, error=e))

    _process_texture_paths_in_maya(json_path, copy_textures)

    all_current_mats = cmds.ls(materials=True) or []
    success_count = 0
    skip_count = 0
    failed_objects = []

    # 首先收集所有需要处理的物体和它们的面级材质指定
    objects_with_face_assignments = _collect_face_assignment_objects(data, materials_to_import, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)

    for original_mat_name, info in data.items():
        # 只处理需要导入的材质
        if original_mat_name not in materials_to_import:
            continue
        matched_imported_mat = None

        if ":" in original_mat_name:
            original_ns = original_mat_name.split(":")[0]
            original_base_name = original_mat_name.split(":")[-1]
        else:
            original_ns = ""
            original_base_name = original_mat_name

        for mat in all_current_mats:
            if ":" in mat:
                mat_ns = mat.split(":")[0]
                mat_base = mat.split(":")[-1]
                if mat_base == original_base_name:
                    if original_ns and mat_ns and mat_ns.startswith(temp_ns_base):
                        matched_imported_mat = mat
                        break
                    elif not original_ns and mat_ns.startswith(temp_ns_base):
                        matched_imported_mat = mat
                        break

        if not matched_imported_mat:
            continue

        mat_namespace = matched_imported_mat.split(":")[0]
        mat_base = matched_imported_mat.split(":")[-1]

        sg_mat_base = f"{user_prefix or ''}{mat_base}{user_suffix or ''}"

        sg_name = f"{mat_namespace}:{sg_mat_base}SG"

        # 1) 目标命名 SE 已存在（多为 .ma 自带，含 surface/displacement/volume 完整网络）
        if cmds.objExists(sg_name) and cmds.nodeType(sg_name) == 'shadingEngine':
            target_se = sg_name
        else:
            # 2) 优先复用 .ma 中真实导入的 SE（名称不匹配时仍保留其完整网络连接）
            imported_ses = cmds.listConnections(
                matched_imported_mat, source=False, destination=True,
                type='shadingEngine') or []
            target_se = imported_ses[0] if imported_ses else None
            # 3) 都没有则新建 SE（只连 surfaceShader，置换由下方兜底补连）
            if target_se is None:
                if cmds.objExists(sg_name):
                    cmds.delete(sg_name)
                target_se = cmds.sets(renderable=True, empty=True, name=sg_name)

        try:
            cmds.connectAttr(f"{matched_imported_mat}.outColor", f"{target_se}.surfaceShader", force=True)
        except Exception as e:
            _log_debug(f"连接材质到shadingEngine失败: {e}")
            continue

        # 兜底补连置换：SE 未连接 displacementShader 时，从同命名空间查找置换节点
        try:
            if not (cmds.listConnections(f"{target_se}.displacementShader", source=True, destination=False) or []):
                ns_prefix = f"{mat_namespace}:"
                for dn in (cmds.ls(type='displacementShader') or []):
                    if not dn.startswith(ns_prefix):
                        continue
                    try:
                        cmds.connectAttr(f"{dn}.displacement", f"{target_se}.displacementShader", force=True)
                        print(f"[MA+JSON导入] 置换 SE 已链接: {target_se}.displacementShader <- {dn}")
                        break
                    except Exception:
                        pass
        except Exception:
            pass

        for original_shape_name in info["objects"]:
            # 根据选择过滤
            if import_selection and selected_objects:
                filtered = _filter_objects_by_selection([original_shape_name], selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                if not filtered:
                    continue

            # 检查是否有面级材质指定
            if _should_skip_object_for_face(original_shape_name, objects_with_face_assignments, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match):
                continue

            # 使用公共函数查找形状节点
            target_shape = _find_shape_node(original_shape_name, user_ns, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)

            if target_shape:
                all_instance_paths = _get_all_instance_dag_paths(target_shape)
                for instance_path in all_instance_paths:
                    try:
                        old_ses = cmds.listConnections(instance_path, type='shadingEngine') or []
                        for old_se in old_ses:
                            if old_se and old_se != "initialShadingGroup":
                                cmds.sets(instance_path, remove=old_se)
                        cmds.sets(instance_path, forceElement=target_se)
                        success_count += 1
                    except Exception: pass
            else:
                skip_count += 1
                failed_objects.append(original_shape_name)

    i = 1
    while True:
        current_ns = temp_ns_base if i == 1 else f"{temp_ns_base}{i}"
        if cmds.namespace(exists=current_ns):
            try:
                ns_materials = cmds.ls(current_ns + ":*", materials=True) or []
                for mat in ns_materials:
                    mat_name = mat.split(":")[-1]
                    if user_prefix or user_suffix:
                        new_mat_name = f"{user_prefix or ''}{mat_name}{user_suffix or ''}"
                        cmds.rename(mat, new_mat_name)
                cmds.namespace(moveNamespace=(current_ns, ":"), force=True)
                cmds.namespace(removeNamespace=current_ns)
            except Exception: pass
            i += 1
        else:
            break

    # 还原面级材质指定
    face_success_count = 0
    # 构建材质名称映射表，处理重命名后的材质
    material_name_map = {}
    for original_mat_name, info in data.items():
        # 构建新的材质名称
        if user_prefix or user_suffix:
            mat_base = original_mat_name.split(":")[-1]
            new_mat_base = f"{user_prefix or ''}{mat_base}{user_suffix or ''}"
            # 查找实际的材质名称
            possible_mats = cmds.ls(new_mat_base, materials=True) or []
            if possible_mats:
                material_name_map[original_mat_name] = possible_mats[0]
            else:
                # 尝试带命名空间的版本
                if ':' in original_mat_name:
                    ns = original_mat_name.split(':')[0]
                    possible_mats = cmds.ls(f"{ns}:{new_mat_base}", materials=True) or []
                    if possible_mats:
                        material_name_map[original_mat_name] = possible_mats[0]
        else:
            # 没有前缀后缀时，直接使用原始名称
            possible_mats = cmds.ls(original_mat_name, materials=True) or []
            if possible_mats:
                material_name_map[original_mat_name] = possible_mats[0]
    
    for original_mat_name, info in data.items():
        # 只处理需要导入的材质
        if original_mat_name not in materials_to_import:
            continue
        # 还原面级材质指定
        face_assignments = info.get('face_assignments', {})
        if face_assignments:
            # 根据选择过滤面级材质指定
            if import_selection and selected_objects:
                filtered_face_assignments = {}
                for mesh_name, mat_faces_map in face_assignments.items():
                    # 检查这个mesh是否在选择范围内
                    if _filter_objects_by_selection([mesh_name], selected_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match):
                        filtered_face_assignments[mesh_name] = mat_faces_map
                if not filtered_face_assignments:
                    continue
                face_assignments = filtered_face_assignments
            
            # 转换面级材质指定中的材质名称
            converted_face_assignments = {}
            for mesh_name, mat_faces_map in face_assignments.items():
                converted_mat_faces = {}
                for mat_name, face_specs in mat_faces_map.items():
                    # 使用映射表中的实际材质名称
                    if mat_name in material_name_map:
                        converted_mat_faces[material_name_map[mat_name]] = face_specs
                    else:
                        # 尝试直接使用
                        converted_mat_faces[mat_name] = face_specs
                if converted_mat_faces:
                    converted_face_assignments[mesh_name] = converted_mat_faces
            
            if converted_face_assignments:
                face_count = _assign_face_materials(converted_face_assignments, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, fuzzy_match)
                face_success_count += face_count

    cmds.select(clear=True)
    print(f"导入完成: {success_count} 个物体指定成功, {skip_count} 个物体未找到匹配, {face_success_count} 个面级材质指定成功")
    if failed_objects:
        print(f"[警告] {len(failed_objects)} 个模型材质指定失败")
        failed_transforms = []
        for obj in failed_objects:
            if '|' in obj:
                transform = '|'.join(obj.split('|')[:-1])
                if cmds.objExists(transform):
                    failed_transforms.append(transform)
            elif cmds.objExists(obj):
                failed_transforms.append(obj)
        if failed_transforms:
            cmds.select(failed_transforms, replace=True)
            print(f"[选择] 已选择 {len(failed_transforms)} 个指定失败的模型")

    return True


# ========== UI 界面 ==========

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QLineEdit, QPushButton, QCheckBox, QFileDialog, QGroupBox, QMessageBox, QRadioButton,
    QListView, QTreeView
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView


def _select_multiple_directories(parent, title='选择文件夹'):
    """弹出可多选文件夹的对话框，返回选中的文件夹路径列表"""
    dialog = QFileDialog(parent, title)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.resize(dialog.width() * 2, dialog.height() * 2)
    for view_type in (QListView, QTreeView):
        for view in dialog.findChildren(view_type):
            if hasattr(view, 'setSelectionMode'):
                view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    if dialog.exec_():
        return dialog.selectedFiles()
    return []



class RadarTabWidget(QWidget):
    """全频雷达版选项卡（包含导入和导出）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        title_label = QLabel("全频雷达版 - 完整材质网络导出/导入")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title_label)

        help_btn = QPushButton("ℹ 使用帮助")
        help_btn.setFixedHeight(28)
        help_btn.clicked.connect(self.show_radar_help)
        help_btn.setToolTip("点击查看详细使用帮助")
        layout.addWidget(help_btn)

        info_label = QLabel(">> 选择场景中的模型或材质，导出完整材质网络；或导入.zmetal还原材质")
        layout.addWidget(info_label)

        export_basic_group = QGroupBox("导出 - 基础设置")
        export_basic_layout = QVBoxLayout()
        export_basic_layout.setSpacing(6)

        dir_layout = QHBoxLayout()
        dir_label = QLabel('工作目录:')
        dir_label.setFixedWidth(75)
        self.dir_input = QLineEdit()
        self.dir_input.setToolTip("指定导出文件的保存位置")
        browse_btn = QPushButton('浏览')
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self.browse_folder)
        browse_btn.setToolTip("点击选择工作目录")
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(browse_btn)
        export_basic_layout.addLayout(dir_layout)

        name_layout = QHBoxLayout()
        name_label = QLabel('文件名:')
        name_label.setFixedWidth(75)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('留空使用默认命名')
        self.name_input.setToolTip("导出的JSON文件名，留空使用默认命名（当前时间）")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        export_basic_layout.addLayout(name_layout)

        export_basic_group.setLayout(export_basic_layout)
        layout.addWidget(export_basic_group)

        export_options_group = QGroupBox("导出 - 选项")
        export_options_layout = QVBoxLayout()
        export_options_layout.setSpacing(6)

        export_mode_layout = QHBoxLayout()
        export_mode_label = QLabel('导出模式:')
        export_mode_label.setFixedWidth(75)
        self.export_selection_radio = QRadioButton('导出选择')
        self.export_all_radio = QRadioButton('导出全部')
        self.export_selection_radio.setChecked(True)
        self.export_selection_radio.setToolTip("只导出当前选择的模型或材质")
        self.export_all_radio.setToolTip("导出场景中所有材质")
        export_mode_layout.addWidget(export_mode_label)
        export_mode_layout.addWidget(self.export_selection_radio)
        export_mode_layout.addWidget(self.export_all_radio)
        export_options_layout.addLayout(export_mode_layout)

        self.selection_is_material_checkbox = QCheckBox('所选对象为材质节点（而非模型）')
        self.selection_is_material_checkbox.setToolTip("如果当前选择的是材质节点而不是模型，请勾选此选项")
        export_options_layout.addWidget(self.selection_is_material_checkbox)

        self.separate_checkbox = QCheckBox('每个材质导出为独立文件')
        self.separate_checkbox.setToolTip("为每个材质生成单独的.zmetal文件")
        export_options_layout.addWidget(self.separate_checkbox)

        self.export_objects_checkbox = QCheckBox('导出材质对应模型数据')
        self.export_objects_checkbox.setChecked(True)
        self.export_objects_checkbox.setToolTip("生成额外的.mcm映射文件，记录材质与模型的对应关系")
        export_options_layout.addWidget(self.export_objects_checkbox)

        self.export_metadata_checkbox = QCheckBox('导出元数据')
        self.export_metadata_checkbox.setChecked(True)
        self.export_metadata_checkbox.setToolTip("生成独立的.ameta元数据文件，包含版本、渲染器、色彩空间、分类、标签等信息")
        export_options_layout.addWidget(self.export_metadata_checkbox)

        self.create_folder_checkbox = QCheckBox('创建材质文件夹')
        self.create_folder_checkbox.setToolTip("每个材质创建独立文件夹，包含材质.zmetal、元数据.ameta和textures子文件夹")
        export_options_layout.addWidget(self.create_folder_checkbox)

        self.pack_textures_checkbox = QCheckBox('打包纹理')
        self.pack_textures_checkbox.setToolTip("将材质连接的纹理拷贝到textures文件夹，并替换路径为相对路径")
        export_options_layout.addWidget(self.pack_textures_checkbox)

        export_btn = QPushButton('执行导出')
        export_btn.setFixedHeight(32)
        export_btn.setStyleSheet('background-color: #669966; color: white; font-weight: bold;')
        export_btn.clicked.connect(self.do_export)
        export_btn.setToolTip("开始执行材质导出")
        export_options_layout.addWidget(export_btn)

        export_options_group.setLayout(export_options_layout)
        layout.addWidget(export_options_group)

        self.export_metadata_header = QPushButton("导出 - 元数据设置 ►")
        self.export_metadata_header.setFlat(True)
        self.export_metadata_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.export_metadata_header.setCheckable(True)
        self.export_metadata_header.setChecked(False)
        self.export_metadata_header.clicked.connect(self._on_export_metadata_toggle)
        layout.addWidget(self.export_metadata_header)

        self.export_metadata_content = QWidget()
        export_metadata_content_layout = QVBoxLayout()
        export_metadata_content_layout.setSpacing(6)
        export_metadata_content_layout.setContentsMargins(10, 0, 10, 5)

        color_space_layout = QHBoxLayout()
        color_space_label = QLabel('色彩空间:')
        color_space_label.setFixedWidth(75)
        detected_cs = _detect_color_space()
        self.color_space_input = QLineEdit(detected_cs)
        self.color_space_input.setPlaceholderText('ACEScg')
        self.color_space_input.setToolTip("当前渲染器自动检测的色彩空间，可手动修改")
        color_space_layout.addWidget(color_space_label)
        color_space_layout.addWidget(self.color_space_input)
        export_metadata_content_layout.addLayout(color_space_layout)

        category_layout = QHBoxLayout()
        category_label = QLabel('Category:')
        category_label.setFixedWidth(75)
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText('材质分类，如: 金属/布料/皮肤')
        self.category_input.setToolTip("材质的分类信息，用于材质库管理")
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.category_input)
        export_metadata_content_layout.addLayout(category_layout)

        tags_layout = QHBoxLayout()
        tags_label = QLabel('Tags:')
        tags_label.setFixedWidth(75)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText('逗号分隔，如: metal,rough,pbr')
        self.tags_input.setToolTip("材质的标签，多个标签用逗号分隔")
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self.tags_input)
        export_metadata_content_layout.addLayout(tags_layout)

        name_cn_layout = QHBoxLayout()
        name_cn_label = QLabel('中文名:')
        name_cn_label.setFixedWidth(75)
        self.name_cn_input = QLineEdit()
        self.name_cn_input.setPlaceholderText('留空则使用英文原名')
        self.name_cn_input.setToolTip("材质的中文名称，留空则使用英文名称")
        name_cn_layout.addWidget(name_cn_label)
        name_cn_layout.addWidget(self.name_cn_input)
        export_metadata_content_layout.addLayout(name_cn_layout)

        self.export_metadata_content.setLayout(export_metadata_content_layout)
        self.export_metadata_content.setVisible(False)
        layout.addWidget(self.export_metadata_content)

        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #CCCCCC;")
        layout.addWidget(separator)

        self.import_basic_header = QPushButton("导入 - 基础设置 ►")
        self.import_basic_header.setFlat(True)
        self.import_basic_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.import_basic_header.setCheckable(True)
        self.import_basic_header.setChecked(False)
        self.import_basic_header.clicked.connect(self._on_import_basic_toggle)
        layout.addWidget(self.import_basic_header)

        self.import_basic_content = QWidget()
        import_basic_content_layout = QVBoxLayout()
        import_basic_content_layout.setSpacing(6)
        import_basic_content_layout.setContentsMargins(10, 0, 10, 5)

        ns_layout = QHBoxLayout()
        ns_label = QLabel('命名空间:')
        ns_label.setFixedWidth(75)
        self.ns_input = QLineEdit()
        self.ns_input.setPlaceholderText('引用模型时自带的前缀，如: ns1')
        self.ns_input.setToolTip("引用模型时自带的前缀，用于匹配导出时的模型路径")
        ns_layout.addWidget(ns_label)
        ns_layout.addWidget(self.ns_input)
        import_basic_content_layout.addLayout(ns_layout)

        prefix_suffix_layout = QHBoxLayout()
        prefix_label = QLabel('材质前缀:')
        prefix_label.setFixedWidth(75)
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText('如: Imported_')
        self.prefix_input.setToolTip("在导入的材质名称前添加前缀，避免命名冲突")
        prefix_suffix_layout.addWidget(prefix_label)
        prefix_suffix_layout.addWidget(self.prefix_input)

        suffix_label = QLabel('材质后缀:')
        suffix_label.setFixedWidth(75)
        self.suffix_input = QLineEdit()
        self.suffix_input.setPlaceholderText('如: _Import')
        self.suffix_input.setToolTip("在导入的材质名称后添加后缀，避免命名冲突")
        prefix_suffix_layout.addWidget(suffix_label)
        prefix_suffix_layout.addWidget(self.suffix_input)
        import_basic_content_layout.addLayout(prefix_suffix_layout)

        path_replace_layout = QHBoxLayout()
        old_prefix_label = QLabel('原模型前缀:')
        old_prefix_label.setFixedWidth(85)
        self.old_prefix_input = QLineEdit()
        self.old_prefix_input.setPlaceholderText('导出时的模型前缀，如: OLD_')
        self.old_prefix_input.setToolTip("导出时的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(old_prefix_label)
        path_replace_layout.addWidget(self.old_prefix_input)

        new_prefix_label = QLabel('新模型前缀:')
        new_prefix_label.setFixedWidth(85)
        self.new_prefix_input = QLineEdit()
        self.new_prefix_input.setPlaceholderText('替换为，如: NEW_')
        self.new_prefix_input.setToolTip("导入到当前场景的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(new_prefix_label)
        path_replace_layout.addWidget(self.new_prefix_input)
        import_basic_content_layout.addLayout(path_replace_layout)

        suffix_replace_layout = QHBoxLayout()
        old_suffix_label = QLabel('原模型后缀:')
        old_suffix_label.setFixedWidth(85)
        self.old_suffix_input = QLineEdit()
        self.old_suffix_input.setPlaceholderText('导出时的模型后缀，如: _GEO')
        self.old_suffix_input.setToolTip("导出时的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(old_suffix_label)
        suffix_replace_layout.addWidget(self.old_suffix_input)

        new_suffix_label = QLabel('新模型后缀:')
        new_suffix_label.setFixedWidth(85)
        self.new_suffix_input = QLineEdit()
        self.new_suffix_input.setPlaceholderText('替换为，如: _MESH')
        self.new_suffix_input.setToolTip("导入到当前场景的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(new_suffix_label)
        suffix_replace_layout.addWidget(self.new_suffix_input)
        import_basic_content_layout.addLayout(suffix_replace_layout)

        self.import_basic_content.setLayout(import_basic_content_layout)
        self.import_basic_content.setVisible(False)
        layout.addWidget(self.import_basic_content)

        import_options_group = QGroupBox("导入 - 选项")
        import_options_layout = QVBoxLayout()
        import_options_layout.setSpacing(6)

        import_mode_layout = QHBoxLayout()
        import_mode_label = QLabel('导入模式:')
        import_mode_label.setFixedWidth(75)
        self.import_selection_radio = QRadioButton('导入选择')
        self.import_all_radio = QRadioButton('导入全部')
        self.import_all_radio.setChecked(True)
        self.import_selection_radio.setToolTip("只导入JSON文件中选中的材质")
        self.import_all_radio.setToolTip("导入JSON文件中的所有材质")
        import_mode_layout.addWidget(import_mode_label)
        import_mode_layout.addWidget(self.import_selection_radio)
        import_mode_layout.addWidget(self.import_all_radio)
        import_options_layout.addLayout(import_mode_layout)

        self.assign_objects_checkbox = QCheckBox('导入后指定材质给模型')
        self.assign_objects_checkbox.setChecked(True)
        self.assign_objects_checkbox.setToolTip("自动将导入的材质指定给对应模型")
        import_options_layout.addWidget(self.assign_objects_checkbox)

        self.fuzzy_match_checkbox = QCheckBox('模糊匹配模型名称')
        self.fuzzy_match_checkbox.setToolTip("当精确匹配失败时，通过名称子串包含关系进行匹配\n例如：导出时模型为TEST_pSphere1_temp，导入时模型为pSphere1，勾选后可正确匹配")
        import_options_layout.addWidget(self.fuzzy_match_checkbox)

        self.import_copy_textures_checkbox = QCheckBox('拷贝贴图到当前工程')
        self.import_copy_textures_checkbox.setChecked(True)
        self.import_copy_textures_checkbox.setToolTip("将材质关联的纹理拷贝到当前Maya工程的sourceimages目录。不勾选则使用材质数据所在文件夹的相对路径")
        import_options_layout.addWidget(self.import_copy_textures_checkbox)

        import_btn = QPushButton('执行导入')
        import_btn.setFixedHeight(32)
        import_btn.setStyleSheet('background-color: #6680AA; color: white; font-weight: bold;')
        import_btn.clicked.connect(self.do_import)
        import_btn.setToolTip("开始执行材质导入")
        import_options_layout.addWidget(import_btn)

        folder_import_btn = QPushButton('从文件夹导入...')
        folder_import_btn.setFixedHeight(28)
        folder_import_btn.setStyleSheet('background-color: #5a7a9a; color: white;')
        folder_import_btn.clicked.connect(self.do_import_from_folder)
        folder_import_btn.setToolTip("选择文件夹，递归导入文件夹内所有材质.zmetal/.json文件")
        import_options_layout.addWidget(folder_import_btn)

        import_options_group.setLayout(import_options_layout)
        layout.addWidget(import_options_group)

        layout.addStretch()

    def _on_export_metadata_toggle(self):
        is_expanded = self.export_metadata_header.isChecked()
        self.export_metadata_content.setVisible(is_expanded)
        self.export_metadata_header.setText("导出 - 元数据设置 ▼" if is_expanded else "导出 - 元数据设置 ►")

    def _on_import_basic_toggle(self):
        is_expanded = self.import_basic_header.isChecked()
        self.import_basic_content.setVisible(is_expanded)
        self.import_basic_header.setText("导入 - 基础设置 ▼" if is_expanded else "导入 - 基础设置 ►")

    def show_radar_help(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton

        help_window = QDialog(self)
        help_window.setWindowTitle("全频雷达版使用帮助")
        help_window.resize(600, 500)

        layout = QVBoxLayout(help_window)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        help_text = QLabel()
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.RichText)
        # 使文本可选择
        help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_text.setText(
            "<b>全频雷达版 - 完整材质网络导出/导入</b><br><br>"
            "本工具提供完整的材质参数导出和导入功能，支持选择模型或材质进行操作。<br><br>"
            "<b>【导出功能】</b><br>"
            "• 支持选择场景中的模型(transform)或材质节点进行导出<br>"
            "• 导出内容包括：所有材质属性、连接信息、贴图节点等完整参数<br>"
            "• 可选择每个材质导出为独立文件，或所有材质合并为一个文件<br>"
            "• 勾选「导出材质对应模型数据」会生成额外的.mcm映射文件<br><br>"
            "• <b>注意：材质数据保存为.zmetal格式</b>（实际为JSON格式，只是后缀不同）<br><br>"
            "<b>【UI控件说明】</b><br>"
            "<b>导出 - 基础设置</b><br>"
            "• <b>工作目录</b>：指定导出文件的保存位置<br>"
            "• <b>文件名</b>：导出的JSON文件名，留空使用默认命名（当前时间）<br><br>"
            "<b>导出 - 选项</b><br>"
            "• <b>导出模式</b>：「导出选择」只导出当前选中对象；「导出全部」导出场景中所有材质<br>"
            "• <b>每个材质导出为独立文件</b>：是否为每个材质生成单独的.zmetal文件<br>"
            "• <b>导出材质对应模型数据</b>：是否生成.mcm映射文件<br><br>"
            "<b>导出 - 元数据设置</b><br>"
            "• <b>色彩空间</b>：指定导出材质的色彩空间，默认值为ACEScg<br>"
            "• <b>Category</b>：材质的分类信息，用于材质库管理，如：金属、布料、皮肤等<br>"
            "• <b>Tags</b>：材质的标签，多个标签用逗号分隔，如：metal,rough,pbr<br><br>"
            "<b>导入 - 基础设置</b><br>"
            "• <b>命名空间</b>：引用模型时自带的前缀，如ns1，用于匹配导出时的模型路径<br>"
            "• <b>材质前缀</b>：在导入的材质名称前添加前缀，避免命名冲突<br>"
            "• <b>材质后缀</b>：在导入的材质名称后添加后缀，避免命名冲突<br>"
            "• <b>原模型前缀</b>：导出时的模型路径前缀，用于路径替换<br>"
            "• <b>新模型前缀</b>：导入到当前场景的模型路径前缀，用于路径替换<br>"
            "• <b>原模型后缀</b>：导出时的模型路径后缀，用于路径替换<br>"
            "• <b>新模型后缀</b>：导入到当前场景的模型路径后缀，用于路径替换<br><br>"
            "<b>导入 - 选项</b><br>"
            "• <b>导入模式</b>：「导入选择」只导入选中的材质；「导入全部」导入所有材质<br>"
            "• <b>导入后指定材质给模型</b>：是否自动将材质指定给对应模型<br><br>"
            "<b>【常见问题】</b><br>"
            "• <b>导出时找不到材质？</b> 确保选择的对象是transform节点或材质节点<br>"
            "• <b>导入后材质不显示？</b> 检查材质是否正确连接到shadingEngine<br>"
            "• <b>指定材质失败？</b> 确保场景中存在模型物体，且路径与导出时一致<br>"
            "• <b>后缀不起作用？</b> 后缀会自动加在原材质名后面（不包括下划线，需手动添加）<br>"
            "• <b>实例物体材质丢失？</b> 工具已处理实例物体，但需确保选择的是顶层transform<br>"
        )

        scroll_area.setWidget(help_text)
        layout.addWidget(scroll_area)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(help_window.close)
        layout.addWidget(close_btn)

        help_window.setModal(False)
        help_window.show()

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择工作目录')
        if path:
            self.dir_input.setText(path.replace('\\', '/'))

    def do_export(self):
        target_dir = self.dir_input.text()
        custom_name = self.name_input.text()
        separate_files = self.separate_checkbox.isChecked()
        export_objects = self.export_objects_checkbox.isChecked()
        export_all = self.export_all_radio.isChecked()
        selection_is_material = self.selection_is_material_checkbox.isChecked()
        export_metadata = self.export_metadata_checkbox.isChecked()
        create_material_folder = self.create_folder_checkbox.isChecked()
        pack_textures = self.pack_textures_checkbox.isChecked()
        color_space = self.color_space_input.text().strip() or None
        category = self.category_input.text().strip() or None
        tags = self.tags_input.text().strip() or None
        name_cn = self.name_cn_input.text().strip() or None
        radar_export_materials(
            target_dir if target_dir else None,
            custom_name if custom_name else None,
            separate_files,
            export_objects,
            color_space,
            category,
            tags,
            export_all,
            name_cn,
            selection_is_material,
            export_metadata,
            create_material_folder,
            pack_textures
        )

    def do_import(self):
        user_ns = self.ns_input.text().strip().rstrip(":") or None
        user_prefix = self.prefix_input.text().strip() or None
        user_suffix = self.suffix_input.text().strip() or None
        old_path_prefix = self.old_prefix_input.text().strip() or None
        new_path_prefix = self.new_prefix_input.text().strip() or None
        old_path_suffix = self.old_suffix_input.text().strip() or None
        new_path_suffix = self.new_suffix_input.text().strip() or None

        start_dir = self.dir_input.text().strip()
        assign_objects = self.assign_objects_checkbox.isChecked()
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        files = QFileDialog.getOpenFileNames(
            self, '选择材质文件', start_dir if start_dir else '', 'Material Files (*.zmetal *.json)'
        )[0]

        if files:
            radar_import_materials(files, user_ns, user_prefix, user_suffix, assign_objects=assign_objects, old_path_prefix=old_path_prefix, new_path_prefix=new_path_prefix, old_path_suffix=old_path_suffix, new_path_suffix=new_path_suffix, import_selection=import_selection, fuzzy_match=fuzzy_match, copy_textures=copy_textures)
        elif start_dir:
            radar_import_materials(None, user_ns, user_prefix, user_suffix, start_dir, assign_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, import_selection, fuzzy_match=fuzzy_match, copy_textures=copy_textures)

    def do_import_from_folder(self):
        user_ns = self.ns_input.text().strip().rstrip(":") or None
        user_prefix = self.prefix_input.text().strip() or None
        user_suffix = self.suffix_input.text().strip() or None
        old_path_prefix = self.old_prefix_input.text().strip() or None
        new_path_prefix = self.new_prefix_input.text().strip() or None
        old_path_suffix = self.old_suffix_input.text().strip() or None
        new_path_suffix = self.new_suffix_input.text().strip() or None
        assign_objects = self.assign_objects_checkbox.isChecked()
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        dirs = _select_multiple_directories(self, '选择材质文件夹（可按住Ctrl多选）')

        if not dirs:
            return

        json_files = _collect_json_files_from_dirs(dirs)
        if not json_files:
            cmds.warning(t("zjg.no_material_files_in_folder"))
            return

        print(f"[文件夹导入] 找到 {len(json_files)} 个材质文件")
        radar_import_materials(json_files, user_ns, user_prefix, user_suffix, assign_objects=assign_objects, old_path_prefix=old_path_prefix, new_path_prefix=new_path_prefix, old_path_suffix=old_path_suffix, new_path_suffix=new_path_suffix, import_selection=import_selection, fuzzy_match=fuzzy_match, copy_textures=copy_textures)


class MATabWidget(QWidget):
    """MA+JSON版选项卡（包含导入和导出）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        title_label = QLabel("MA+JSON版 - 材质网络导出为MA文件")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title_label)

        help_btn = QPushButton("ℹ 使用帮助")
        help_btn.setFixedHeight(28)
        help_btn.clicked.connect(self.show_ma_help)
        help_btn.setToolTip("点击查看详细使用帮助")
        layout.addWidget(help_btn)

        warning_label = QLabel(">> [重要] 请确保当前场景里已经有需要被赋值的模型！")
        warning_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(warning_label)

        export_basic_group = QGroupBox("导出 - 基础设置")
        export_basic_layout = QVBoxLayout()
        export_basic_layout.setSpacing(6)

        dir_layout = QHBoxLayout()
        dir_label = QLabel('工作目录:')
        dir_label.setFixedWidth(75)
        self.dir_input = QLineEdit()
        self.dir_input.setToolTip("指定导出文件的保存位置")
        browse_btn = QPushButton('浏览')
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self.browse_folder)
        browse_btn.setToolTip("点击选择工作目录")
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(browse_btn)
        export_basic_layout.addLayout(dir_layout)

        name_layout = QHBoxLayout()
        name_label = QLabel('文件名:')
        name_label.setFixedWidth(75)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('留空使用默认命名')
        self.name_input.setToolTip("导出的MA和JSON文件名，留空使用默认命名（当前时间）")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        export_basic_layout.addLayout(name_layout)

        export_basic_group.setLayout(export_basic_layout)
        layout.addWidget(export_basic_group)

        export_options_group = QGroupBox("导出 - 选项")
        export_options_layout = QVBoxLayout()
        export_options_layout.setSpacing(6)

        self.separate_checkbox = QCheckBox('每个材质导出为独立文件')
        self.separate_checkbox.setToolTip("为每个材质生成单独的MA和JSON文件")
        self.separate_checkbox.setChecked(True)
        export_options_layout.addWidget(self.separate_checkbox)

        self.export_metadata_checkbox = QCheckBox('导出元数据')
        self.export_metadata_checkbox.setChecked(True)
        self.export_metadata_checkbox.setToolTip("生成独立的.ameta元数据文件，包含版本、渲染器、色彩空间、分类、标签等信息")
        export_options_layout.addWidget(self.export_metadata_checkbox)

        self.create_folder_checkbox = QCheckBox('创建材质文件夹')
        self.create_folder_checkbox.setToolTip("每个材质创建独立文件夹，包含MA、JSON、元数据JSON和textures子文件夹")
        export_options_layout.addWidget(self.create_folder_checkbox)

        self.pack_textures_checkbox = QCheckBox('打包纹理')
        self.pack_textures_checkbox.setToolTip("将材质连接的纹理拷贝到textures文件夹，并替换MA文件中的路径为相对路径")
        export_options_layout.addWidget(self.pack_textures_checkbox)

        export_btn = QPushButton("执行导出")
        export_btn.setFixedHeight(32)
        export_btn.setStyleSheet('background-color: #669966; color: white; font-weight: bold;')
        export_btn.clicked.connect(self.do_export)
        export_btn.setToolTip("开始执行材质导出")
        export_options_layout.addWidget(export_btn)

        export_options_group.setLayout(export_options_layout)
        layout.addWidget(export_options_group)

        self.ma_export_metadata_header = QPushButton("导出 - 元数据设置 ►")
        self.ma_export_metadata_header.setFlat(True)
        self.ma_export_metadata_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.ma_export_metadata_header.setCheckable(True)
        self.ma_export_metadata_header.setChecked(False)
        self.ma_export_metadata_header.clicked.connect(self._on_ma_export_metadata_toggle)
        layout.addWidget(self.ma_export_metadata_header)

        self.ma_export_metadata_content = QWidget()
        ma_export_metadata_content_layout = QVBoxLayout()
        ma_export_metadata_content_layout.setSpacing(6)
        ma_export_metadata_content_layout.setContentsMargins(10, 0, 10, 5)

        color_space_layout = QHBoxLayout()
        color_space_label = QLabel('色彩空间:')
        color_space_label.setFixedWidth(75)
        detected_cs = _detect_color_space()
        self.ma_color_space_input = QLineEdit(detected_cs)
        self.ma_color_space_input.setPlaceholderText('ACEScg')
        self.ma_color_space_input.setToolTip("当前渲染器自动检测的色彩空间，可手动修改")
        color_space_layout.addWidget(color_space_label)
        color_space_layout.addWidget(self.ma_color_space_input)
        ma_export_metadata_content_layout.addLayout(color_space_layout)

        category_layout = QHBoxLayout()
        category_label = QLabel('Category:')
        category_label.setFixedWidth(75)
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText('材质分类，如: 金属/布料/皮肤')
        self.category_input.setToolTip("材质的分类信息，用于材质库管理")
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.category_input)
        ma_export_metadata_content_layout.addLayout(category_layout)

        tags_layout = QHBoxLayout()
        tags_label = QLabel('Tags:')
        tags_label.setFixedWidth(75)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText('逗号分隔，如: metal,rough,pbr')
        self.tags_input.setToolTip("材质的标签，多个标签用逗号分隔")
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self.tags_input)
        ma_export_metadata_content_layout.addLayout(tags_layout)

        name_cn_layout = QHBoxLayout()
        name_cn_label = QLabel('中文名:')
        name_cn_label.setFixedWidth(75)
        self.name_cn_input = QLineEdit()
        self.name_cn_input.setPlaceholderText('留空则使用英文原名')
        self.name_cn_input.setToolTip("材质的中文名称，留空则使用英文名称")
        name_cn_layout.addWidget(name_cn_label)
        name_cn_layout.addWidget(self.name_cn_input)
        ma_export_metadata_content_layout.addLayout(name_cn_layout)

        self.ma_export_metadata_content.setLayout(ma_export_metadata_content_layout)
        self.ma_export_metadata_content.setVisible(False)
        layout.addWidget(self.ma_export_metadata_content)

        self.ma_import_basic_header = QPushButton("导入 - 基础设置 ►")
        self.ma_import_basic_header.setFlat(True)
        self.ma_import_basic_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.ma_import_basic_header.setCheckable(True)
        self.ma_import_basic_header.setChecked(False)
        self.ma_import_basic_header.clicked.connect(self._on_ma_import_basic_toggle)
        layout.addWidget(self.ma_import_basic_header)

        self.ma_import_basic_content = QWidget()
        ma_import_basic_content_layout = QVBoxLayout()
        ma_import_basic_content_layout.setSpacing(6)
        ma_import_basic_content_layout.setContentsMargins(10, 0, 10, 5)

        ns_layout = QHBoxLayout()
        ns_label = QLabel('命名空间:')
        ns_label.setFixedWidth(70)
        self.ns_edit = QLineEdit()
        self.ns_edit.setPlaceholderText("引用模型时自带的前缀，如: ns1")
        self.ns_edit.setToolTip("引用模型时自带的前缀，用于匹配导出时的模型路径")
        ns_layout.addWidget(ns_label)
        ns_layout.addWidget(self.ns_edit, 1)
        ma_import_basic_content_layout.addLayout(ns_layout)

        prefix_suffix_layout = QHBoxLayout()
        prefix_label = QLabel('材质前缀:')
        prefix_label.setFixedWidth(70)
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("如: Imported_")
        self.prefix_edit.setToolTip("在导入的材质名称前添加前缀，避免命名冲突")
        prefix_suffix_layout.addWidget(prefix_label)
        prefix_suffix_layout.addWidget(self.prefix_edit, 1)

        suffix_label = QLabel('材质后缀:')
        suffix_label.setFixedWidth(70)
        self.suffix_edit = QLineEdit()
        self.suffix_edit.setPlaceholderText("如: _Import")
        self.suffix_edit.setToolTip("在导入的材质名称后添加后缀，避免命名冲突")
        prefix_suffix_layout.addWidget(suffix_label)
        prefix_suffix_layout.addWidget(self.suffix_edit, 1)
        ma_import_basic_content_layout.addLayout(prefix_suffix_layout)

        path_replace_layout = QHBoxLayout()
        old_prefix_label = QLabel('原模型前缀:')
        old_prefix_label.setFixedWidth(80)
        self.old_prefix_edit = QLineEdit()
        self.old_prefix_edit.setPlaceholderText("导出时的模型前缀，如: OLD_")
        self.old_prefix_edit.setToolTip("导出时的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(old_prefix_label)
        path_replace_layout.addWidget(self.old_prefix_edit, 1)

        new_prefix_label = QLabel('新模型前缀:')
        new_prefix_label.setFixedWidth(80)
        self.new_prefix_edit = QLineEdit()
        self.new_prefix_edit.setPlaceholderText("替换为，如: NEW_")
        self.new_prefix_edit.setToolTip("导入到当前场景的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(new_prefix_label)
        path_replace_layout.addWidget(self.new_prefix_edit, 1)
        ma_import_basic_content_layout.addLayout(path_replace_layout)

        suffix_replace_layout = QHBoxLayout()
        old_suffix_label = QLabel('原模型后缀:')
        old_suffix_label.setFixedWidth(80)
        self.old_suffix_edit = QLineEdit()
        self.old_suffix_edit.setPlaceholderText("导出时的模型后缀，如: _GEO")
        self.old_suffix_edit.setToolTip("导出时的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(old_suffix_label)
        suffix_replace_layout.addWidget(self.old_suffix_edit, 1)

        new_suffix_label = QLabel('新模型后缀:')
        new_suffix_label.setFixedWidth(80)
        self.new_suffix_edit = QLineEdit()
        self.new_suffix_edit.setPlaceholderText("替换为，如: _MESH")
        self.new_suffix_edit.setToolTip("导入到当前场景的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(new_suffix_label)
        suffix_replace_layout.addWidget(self.new_suffix_edit, 1)
        ma_import_basic_content_layout.addLayout(suffix_replace_layout)

        self.ma_import_basic_content.setLayout(ma_import_basic_content_layout)
        self.ma_import_basic_content.setVisible(False)
        layout.addWidget(self.ma_import_basic_content)

        import_options_group = QGroupBox("导入 - 选项")
        import_options_layout = QVBoxLayout()
        import_options_layout.setSpacing(6)

        import_mode_layout = QHBoxLayout()
        import_mode_label = QLabel('导入模式:')
        import_mode_label.setFixedWidth(70)
        self.import_selection_radio = QRadioButton('导入选择')
        self.import_all_radio = QRadioButton('导入全部')
        self.import_all_radio.setChecked(True)
        self.import_selection_radio.setToolTip("只导入JSON文件中选中的材质")
        self.import_all_radio.setToolTip("导入JSON文件中的所有材质")
        import_mode_layout.addWidget(import_mode_label)
        import_mode_layout.addWidget(self.import_selection_radio)
        import_mode_layout.addWidget(self.import_all_radio)
        import_options_layout.addLayout(import_mode_layout)

        self.fuzzy_match_checkbox = QCheckBox('模糊匹配模型名称')
        self.fuzzy_match_checkbox.setToolTip("当精确匹配失败时，通过名称子串包含关系进行匹配\n例如：导出时模型为TEST_pSphere1_temp，导入时模型为pSphere1，勾选后可正确匹配")
        import_options_layout.addWidget(self.fuzzy_match_checkbox)

        self.import_copy_textures_checkbox = QCheckBox('拷贝贴图到当前工程')
        self.import_copy_textures_checkbox.setChecked(True)
        self.import_copy_textures_checkbox.setToolTip("将材质关联的纹理拷贝到当前Maya工程的sourceimages目录。不勾选则使用材质数据所在文件夹的相对路径")
        import_options_layout.addWidget(self.import_copy_textures_checkbox)

        import_btn = QPushButton("执行导入")
        import_btn.setFixedHeight(32)
        import_btn.setStyleSheet('background-color: #6680AA; color: white; font-weight: bold;')
        import_btn.clicked.connect(self.do_import)
        import_btn.setToolTip("开始执行材质导入")
        import_options_layout.addWidget(import_btn)

        folder_import_btn = QPushButton('从文件夹导入...')
        folder_import_btn.setFixedHeight(28)
        folder_import_btn.setStyleSheet('background-color: #5a7a9a; color: white;')
        folder_import_btn.clicked.connect(self.do_ma_import_from_folder)
        folder_import_btn.setToolTip("选择文件夹，递归导入文件夹内所有材质MA+JSON文件")
        import_options_layout.addWidget(folder_import_btn)

        import_options_group.setLayout(import_options_layout)
        layout.addWidget(import_options_group)

        layout.addStretch()

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择工作目录')
        if path:
            self.dir_input.setText(path.replace('\\', '/'))

    def do_export(self):
        target_dir = self.dir_input.text()
        custom_name = self.name_input.text()
        separate_files = self.separate_checkbox.isChecked()
        export_metadata = self.export_metadata_checkbox.isChecked()
        create_material_folder = self.create_folder_checkbox.isChecked()
        pack_textures = self.pack_textures_checkbox.isChecked()
        color_space = self.ma_color_space_input.text().strip() or None
        category = self.category_input.text().strip() or None
        tags = self.tags_input.text().strip() or None
        name_cn = self.name_cn_input.text().strip() or None
        ma_export_materials(
            target_dir if target_dir else None,
            custom_name if custom_name else None,
            separate_files,
            color_space,
            category,
            tags,
            name_cn,
            export_metadata,
            create_material_folder,
            pack_textures
        )

    def do_import(self):
        user_ns = self.ns_edit.text().strip().rstrip(":") or None
        user_prefix = self.prefix_edit.text().strip() or None
        user_suffix = self.suffix_edit.text().strip() or None
        old_path_prefix = self.old_prefix_edit.text().strip() or None
        new_path_prefix = self.new_prefix_edit.text().strip() or None
        old_path_suffix = self.old_suffix_edit.text().strip() or None
        new_path_suffix = self.new_suffix_edit.text().strip() or None
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        start_dir = self.dir_input.text().strip()

        file, _ = QFileDialog.getOpenFileName(
            self, '选择材质文件', start_dir if start_dir else '', 'Material Files (*.zmetal *.json)'
        )

        if file:
            ma_import_materials(file, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, import_selection, fuzzy_match, copy_textures)

    def do_ma_import_from_folder(self):
        user_ns = self.ns_edit.text().strip().rstrip(":") or None
        user_prefix = self.prefix_edit.text().strip() or None
        user_suffix = self.suffix_edit.text().strip() or None
        old_path_prefix = self.old_prefix_edit.text().strip() or None
        new_path_prefix = self.new_prefix_edit.text().strip() or None
        old_path_suffix = self.old_suffix_edit.text().strip() or None
        new_path_suffix = self.new_suffix_edit.text().strip() or None
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        dirs = _select_multiple_directories(self, '选择材质文件夹（可按住Ctrl多选）')

        if not dirs:
            return

        json_files = _collect_json_files_from_dirs(dirs)
        if not json_files:
            cmds.warning(t("zjg.no_material_json_in_folder"))
            return

        print(f"[文件夹导入] 找到 {len(json_files)} 个材质MA+JSON文件")
        for jf in json_files:
            print(f"  [文件夹导入] 正在导入: {jf}")
            ma_import_materials(jf, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, import_selection, fuzzy_match, copy_textures)

    def _on_ma_export_metadata_toggle(self):
        is_expanded = self.ma_export_metadata_header.isChecked()
        self.ma_export_metadata_content.setVisible(is_expanded)
        self.ma_export_metadata_header.setText("导出 - 元数据设置 ▼" if is_expanded else "导出 - 元数据设置 ►")

    def _on_ma_import_basic_toggle(self):
        is_expanded = self.ma_import_basic_header.isChecked()
        self.ma_import_basic_content.setVisible(is_expanded)
        self.ma_import_basic_header.setText("导入 - 基础设置 ▼" if is_expanded else "导入 - 基础设置 ►")

    def show_ma_help(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton

        help_window = QDialog(self)
        help_window.setWindowTitle("MA+JSON版使用帮助")
        help_window.resize(600, 500)

        layout = QVBoxLayout(help_window)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        help_text = QLabel()
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.RichText)
        # 使文本可选择
        help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_text.setText(
            "<b>MA+JSON版 - 材质网络导出为MA文件</b><br><br>"
            "本工具将材质网络导出为MA文件格式，适合引用到其他场景，保留完整的材质连接关系。<br><br>"
            "<b>【导出功能】</b><br>"
            "• 选择带有材质的模型(transform)节点进行导出<br>"
            "• 导出为两个文件：.ma材质文件和.json映射文件<br>"
            "• MA文件只包含材质节点，不包含模型数据<br>"
            "• JSON文件记录材质与模型的对应关系<br>"
            "• 可选择每个材质导出为独立文件，或所有材质合并为一个文件<br><br>"
            "<b>【UI控件说明】</b><br>"
            "<b>导出 - 基础设置</b><br>"
            "• <b>工作目录</b>：指定导出文件的保存位置<br>"
            "• <b>文件名</b>：导出的MA和JSON文件名，留空使用默认命名（当前时间）<br><br>"
            "<b>导出 - 选项</b><br>"
            "• <b>每个材质导出为独立文件</b>：是否为每个材质生成单独的MA和JSON文件<br><br>"
            "<b>导入 - 基础设置</b><br>"
            "• <b>命名空间</b>：引用模型时自带的前缀，如ns1，用于匹配导出时的模型路径<br>"
            "• <b>材质前缀</b>：在导入的材质名称前添加前缀，避免命名冲突<br>"
            "• <b>材质后缀</b>：在导入的材质名称后添加后缀，避免命名冲突<br>"
            "• <b>原模型前缀</b>：导出时的模型路径前缀，用于路径替换<br>"
            "• <b>新模型前缀</b>：导入到当前场景的模型路径前缀，用于路径替换<br>"
            "• <b>原模型后缀</b>：导出时的模型路径后缀，用于路径替换<br>"
            "• <b>新模型后缀</b>：导入到当前场景的模型路径后缀，用于路径替换<br><br>"
            "<b>导入 - 选项</b><br>"
            "• <b>导入模式</b>：「导入选择」只导入选中的材质；「导入全部」导入所有材质<br>"
            "• <b>执行导入</b>：点击后弹出文件选择窗口，选择JSON文件进行导入<br><br>"
            "<b>【模型匹配策略】</b><br>"
            "1. 优先使用完整长路径匹配<br>"
            "2. 如果填写了命名空间，使用命名空间+短名称匹配<br>"
            "3. 如果都没填，使用纯短名称匹配（可能匹配到错误的物体）<br><br>"
            "<b>【常见问题】</b><br>"
            "• <b>找不到配套MA文件？</b> 确保JSON和MA文件在同一目录下<br>"
            "• <b>模型匹配失败？</b> 检查命名空间是否正确，确保模型路径一致<br>"
            "• <b>材质指定到错误物体？</b> 可能是短名称重复导致，尝试填写命名空间<br>"
            "• <b>后缀不起作用？</b> 后缀会自动加在原材质名后面（不包括下划线，需手动添加）<br>"
            "• <b>导入后材质不显示？</b> 检查是否正确创建了 shadingEngine 并指定了模型<br>"
        )

        scroll_area.setWidget(help_text)
        layout.addWidget(scroll_area)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(help_window.close)
        layout.addWidget(close_btn)

        help_window.setModal(False)
        help_window.show()


class MaterialTransferTool(QMainWindow):
    WINDOW_OBJECT_NAME = 'MaterialTransferTool_Window'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(self.WINDOW_OBJECT_NAME)
        self.setWindowTitle('材质导入导出工具')
        self.resize(400, 600)

        self._is_closed = False
        self.closeEvent = self._on_close

        self._setup_ui()

    def _on_close(self, event):
        self._is_closed = True
        event.accept()

        central_widget = self.centralWidget()
        if central_widget:
            central_widget.deleteLater()
        self.deleteLater()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.tabs = QTabWidget()

        self.radar_tab = RadarTabWidget()
        self.tabs.addTab(self.radar_tab, "全频雷达版")

        self.ma_tab = MATabWidget()
        self.tabs.addTab(self.ma_tab, "MA+JSON版")

        main_layout.addWidget(self.tabs)


_window_instance = None
_maya_main_window = None


def _get_maya_main_window():
    """延迟获取Maya主窗口，避免启动时卡顿"""
    global _maya_main_window
    if _maya_main_window is not None:
        return _maya_main_window
    
    try:
        import maya.OpenMayaUI as omui
        import shiboken6
        window_ptr = omui.MQtUtil.mainWindow()
        if window_ptr:
            _maya_main_window = shiboken6.wrapInstance(int(window_ptr), QWidget)
            return _maya_main_window
    except Exception: pass
    return None


def show_ui():
    global _window_instance

    app = QApplication.instance()
    if not app:
        app = QApplication([])

    if _window_instance is not None:
        try:
            if _window_instance.isVisible():
                _window_instance.raise_()
                _window_instance.activateWindow()
            else:
                _window_instance.show()
                _window_instance.raise_()
                _window_instance.activateWindow()
        except Exception:
            _window_instance = None

    if _window_instance is None:
        maya_window = _get_maya_main_window()
        if maya_window:
            _window_instance = MaterialTransferTool(maya_window)
        else:
            _window_instance = MaterialTransferTool()

        _window_instance.show()
        # 处理所有待处理的事件，避免Maya卡住
        QApplication.processEvents()

    return _window_instance


if __name__ == '__main__':
    show_ui()
