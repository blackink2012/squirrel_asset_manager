#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PBR贴图转资产工具
将PBR贴图集合转换为.zasset资产格式
"""

import os
import json
import re
import sys
import uuid
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import maya.cmds as cmds
    import maya.mel as mel
    IN_MAYA = True
except ImportError:
    IN_MAYA = False

from core.zasset_io import ZassetIO
from core.zasset_builder import ZassetBuilder

def get_qt_modules():
    """动态获取Qt模块，兼容PySide6和PySide2"""
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        try:
            from PySide2 import QtWidgets, QtCore, QtGui
            return QtWidgets, QtCore, QtGui
        except ImportError:
            return None, None, None

QtWidgets, QtCore, QtGui = get_qt_modules()


def _get_export_header():
    """获取导出文件头部信息（匹配插件标准格式）"""
    software_info = "PBR Tool"
    renderer_info = "unknown"
    color_space_info = "ACEScg"
    if IN_MAYA:
        try:
            import maya.cmds as cmds
            ver = cmds.about(version=True)
            software_info = f"Maya {ver}"
            renderer_info = cmds.getAttr("defaultRenderGlobals.currentRenderer")
            if cmds.objExists("defaultColorMgtGlobals"):
                for attr_name in ["workingSpaceName", "defaultInputSpaceName", "renderingSpace"]:
                    try:
                        if cmds.attributeQuery(attr_name, node="defaultColorMgtGlobals", exists=True):
                            result = cmds.getAttr(f"defaultColorMgtGlobals.{attr_name}")
                            if result and isinstance(result, str) and result.strip():
                                color_space_info = result.strip()
                                break
                    except:
                        pass
        except:
            pass

    return {
        "version": "2.0",
        "software": software_info,
        "renderer": renderer_info,
        "color_space": color_space_info,
        "create_date": datetime.now().strftime("%Y-%m-%d")
    }


# ── 精度/分辨率识别 ──
def get_resolution_patterns(config):
    """从配置中获取分辨率匹配模式，带向后兼容默认值"""
    return config.get('resolution_patterns', [
        r'_(2k|4k|8k|1k|16k|512|1024|2048|4096|8192)$',
        r'_(2k|4k|8k|1k|16k|512|1024|2048|4096|8192)_',
    ])


def get_resolution_subdirs(config):
    """从配置中获取分辨率子目录列表，带向后兼容默认值"""
    return config.get('resolution_subdirs',
        ['2k', '4k', '8k', '1k', '16k', '512', '1024', '2048', '4096', '8192'])


def detect_resolution_from_name(filename, patterns=None):
    """从文件名中检测精度标识，返回 (clean_name, resolution) 或 (filename, None)"""
    if patterns is None:
        patterns = [
            r'_(2k|4k|8k|1k|16k|512|1024|2048|4096|8192)$',
            r'_(2k|4k|8k|1k|16k|512|1024|2048|4096|8192)_',
        ]
    lower = filename.lower()
    for pattern in patterns:
        m = re.search(pattern, lower)
        if m:
            res = m.group(1).lower()
            orig_res_match = re.search(pattern, filename, re.IGNORECASE)
            if orig_res_match:
                orig_res = orig_res_match.group(1)
                # 只替换精度的文本本身，保留周围分隔符
                start = orig_res_match.start(1)  # 精度文本起始
                end = orig_res_match.end(1)       # 精度文本结束
                clean = filename[:start] + filename[end:]
                # 如果前后都是 _，只保留一个
                clean = clean.replace('__', '_').strip('_')
                return clean, orig_res
    return filename, None


def scan_folder_for_resolutions(folder_path, subdirs=None):
    """检测文件夹下是否有精度子目录，返回匹配的精度列表"""
    if subdirs is None:
        subdirs = ['2k', '4k', '8k', '1k', '16k', '512', '1024', '2048', '4096', '8192']
    if not os.path.isdir(folder_path):
        return []
    found = []
    try:
        for name in os.listdir(folder_path):
            sub = os.path.join(folder_path, name)
            if os.path.isdir(sub) and name.lower() in subdirs:
                found.append(name)
    except PermissionError:
        pass
    return sorted(found, key=lambda x: _res_key(x))


def _res_key(res_name):
    """精度排序键：数值从大到小"""
    m = re.search(r'(\d+)', res_name)
    if m:
        return -int(m.group(1))
    return 0

def load_config():
    """加载PBR映射配置"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Assets', 'preset', 'pbr_mapping.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def find_existing_thumbnail(asset_folder, name_fallbacks, config):
    """在资产文件夹中查找已有缩略图
    依次用每个 fallback 名字替换模板中的 {materialName}/{folderName}，
    第一个找到文件的即返回。
    Args:
        asset_folder: 资产根目录
        name_fallbacks: 用于替换占位符的名称列表（优先用前面的）
        config: pbr_mapping.json 配置
    Returns:
        str: 缩略图完整路径，未找到返回 None
    """
    paths = config.get('thumbnail_search_paths', [])
    if not name_fallbacks:
        name_fallbacks = ['']
    for name in name_fallbacks:
        for pattern in paths:
            resolved = pattern.replace('{materialName}', name).replace('{folderName}', name)
            resolved = resolved.replace('\\', '/')
            full_path = os.path.join(asset_folder, resolved)
            if os.path.isfile(full_path):
                return full_path
    return None


def read_source_metadata(asset_folder, name_fallbacks, config):
    """读取源元数据文件并按映射合并到 meta dict
    依次用每个 fallback 名字替换模板中的 {materialName}/{folderName}，
    第一个找到文件的即使用其数据。
    Args:
        asset_folder: 资产根目录
        name_fallbacks: 用于替换占位符的名称列表（优先用前面的）
        config: pbr_mapping.json 配置
    Returns:
        dict: 映射后的元数据字段，未找到或失败返回空 dict
    """
    sources = config.get('metadata_sources', [])
    result = {}
    
    if not name_fallbacks:
        name_fallbacks = ['']
    
    for source in sources:
        pattern = source.get('file_pattern', '')
        fmt = source.get('file_format', 'json')
        mapping = source.get('field_mapping', [])
        
        # 尝试每个 fallback 名称
        for name in name_fallbacks:
            resolved = pattern.replace('{materialName}', name).replace('{folderName}', name)
            resolved = resolved.replace('\\', '/')
            full_path = os.path.join(asset_folder, resolved)
            
            if not os.path.isfile(full_path):
                continue
            
            # 找到文件，读取并映射
            try:
                if fmt == 'json':
                    with open(full_path, 'r', encoding='utf-8') as f:
                        raw_data = json.load(f)
                else:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        raw_data = f.read()
                
                for field in mapping:
                    source_key = field.get('source', '')
                    target_key = field.get('target', source_key)
                    processor = field.get('processor', 'none')
                    
                    raw_value = raw_data.get(source_key) if isinstance(raw_data, dict) else None
                    if raw_value is None:
                        continue
                    
                    if processor == 'split_comma':
                        processed = [t.strip() for t in raw_value.split(',') if t.strip()]
                    elif processor == 'first_line':
                        processed = raw_value.split('\n')[0].strip()
                    else:
                        processed = raw_value
                    
                    result[target_key] = processed
            except Exception:
                continue
            break  # 这个 source 已找到文件，不再 fallback
    return result


def get_routable_extensions(config):
    """获取 file_routing 中所有扩展名"""
    routing = config.get('file_routing', {})
    exts = set()
    for folder, ext_list in routing.items():
        for ext in ext_list:
            if ext.startswith('.'):
                exts.add(ext.lower())
    return list(exts)


def get_route_for_ext(ext, config):
    """获取扩展名对应的路由文件夹名，没有匹配返回 None"""
    routing = config.get('file_routing', {})
    ext = ext.lower()
    if not ext.startswith('.'):
        ext = '.' + ext
    for folder, ext_list in routing.items():
        if ext in [e.lower() for e in ext_list]:
            return folder
    return None


def collect_extra_files(asset_folder, config, folder_name):
    """收集所有路由文件（含缩略图路径），返回 {zip_target_path: disk_full_path}
    
    缩略图文件不在这里处理，由 export_zasset 中的缩略图逻辑单独处理。
    """
    result = {}
    routing = config.get('file_routing', {})
    if not asset_folder or not os.path.isdir(asset_folder):
        return result
    
    for filename in os.listdir(asset_folder):
        filepath = os.path.join(asset_folder, filename)
        if not os.path.isfile(filepath):
            continue
        ext = os.path.splitext(filename)[1].lower()
        route = get_route_for_ext(ext, config)
        if route is None:
            continue
        if route == 'root':
            result[filename] = filepath
        else:
            result[f"{route}/{filename}"] = filepath
    
    return result


def _add_texture_to_group(textures, material_name, texture_type, filename, full_path):
    """向材质贴图组添加文件，同类型多文件存入 extras 列表"""
    if material_name not in textures:
        textures[material_name] = {}
    if texture_type not in textures[material_name]:
        textures[material_name][texture_type] = {
            'filename': filename,
            'full_path': full_path,
            'type': texture_type
        }
    else:
        entry = textures[material_name][texture_type]
        if 'extras' not in entry:
            entry['extras'] = []
        entry['extras'].append({
            'filename': filename,
            'full_path': full_path,
            'type': texture_type
        })


def _collect_files_recursive(folder_path, extensions, recursive_dirs=None, exclude_dirs=None):
    """收集文件夹中的可路由文件，支持递归子目录
    
    Args:
        folder_path: 根文件夹
        extensions: routable 扩展名 set
        recursive_dirs: None=不递归, []或'*'=递归所有子文件夹, ['tex']=只递归指定名称的子文件夹
        exclude_dirs: 排除的子文件夹名列表（递归时跳过）
    Returns:
        list of (relative_dir, filename, full_path) — relative_dir 为 '' 表示根目录
    """
    if exclude_dirs is None:
        exclude_dirs = []
    exclude_dirs = [d.lower() for d in exclude_dirs]
    
    result = []
    # 根目录文件
    for fname in os.listdir(folder_path):
        fpath = os.path.join(folder_path, fname)
        if os.path.isfile(fpath):
            ext = os.path.splitext(fname)[1].lower()
            if ext in extensions:
                result.append(('', fname, fpath))
    
    if recursive_dirs is None:
        return result
    
    # 递归子目录
    auto_mode = (recursive_dirs == [] or recursive_dirs == '*')
    
    def _walk(current_path, rel_prefix):
        for fname in sorted(os.listdir(current_path)):
            fpath = os.path.join(current_path, fname)
            if os.path.isdir(fpath):
                if fname.lower() in exclude_dirs:
                    continue
                # 顶层用 recursive_dirs 过滤，进入后递归全部子目录
                _walk(fpath, rel_prefix + fname + '/')
            elif os.path.isfile(fpath):
                ext = os.path.splitext(fname)[1].lower()
                if ext in extensions:
                    result.append((rel_prefix, fname, fpath))
    
    if auto_mode:
        for fname in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, fname)
            if os.path.isdir(fpath) and fname.lower() not in exclude_dirs:
                _walk(fpath, fname + '/')
    else:
        for fname in recursive_dirs:
            fpath = os.path.join(folder_path, fname)
            if os.path.isdir(fpath) and fname.lower() not in exclude_dirs:
                _walk(fpath, fname + '/')
    
    return result


def scan_textures(folder_path, config, recursive_dirs=None, exclude_dirs=None):
    """扫描文件夹中的贴图文件并按材质分组
    
    Args:
        recursive_dirs: None=不递归, []或'*'=递归全部, ['tex']=只递归指定子文件夹
        exclude_dirs: 排除的文件夹名列表
    """
    textures = {}
    extensions = set(get_routable_extensions(config))
    
    if not os.path.exists(folder_path):
        return textures
    
    files = _collect_files_recursive(folder_path, extensions, recursive_dirs, exclude_dirs)
    for rel_dir, filename, full_path in files:
        basename, ext = os.path.splitext(filename)
        result = identify_texture_type(basename, config)
        if result[0]:
            texture_type, matched_alias = result
            material_name = extract_material_name(basename, texture_type, matched_alias, config)
            if material_name:
                _add_texture_to_group(textures, material_name, texture_type,
                                      filename, full_path)
    
    return textures


def scan_textures_resolution_aware(folder_path, config, recursive_dirs=None, exclude_dirs=None):
    """扫描贴图并检测多精度，返回 (textures, resolution_map)
    
    textures: {material: {type: info}} — 使用默认/最高精度的贴图（向后兼容）
    resolution_map: {material: {'resolutions': [...], 'default_res': str, 'variants': {res: {type: info}}}}
    """
    resolution_map = {}
    routable_exts = set(get_routable_extensions(config))
    
    # 1. 检测精度子目录
    res_dirs = scan_folder_for_resolutions(folder_path, get_resolution_subdirs(config))
    
    # 2. 扫描根目录（基准精度）
    base_textures = scan_textures(folder_path, config, recursive_dirs, exclude_dirs)
    
    if not res_dirs:
        # 没有精度子目录 → 尝试从文件名检测精度
        res_patterns = get_resolution_patterns(config)
        textures = {}
        files = _collect_files_recursive(folder_path, routable_exts, recursive_dirs, exclude_dirs)
        for rel_dir, filename, filepath in files:
            basename, ext = os.path.splitext(filename)
            # 先剥离精度后缀再识别贴图类型
            clean_name, resolution = detect_resolution_from_name(basename, res_patterns)
            if resolution:
                result = identify_texture_type(clean_name, config)
            else:
                result = identify_texture_type(basename, config)
            
            if result[0]:
                texture_type, matched_alias = result
                material_name = extract_material_name(
                    clean_name if resolution else basename,
                    texture_type, matched_alias, config
                )
                res_key = resolution or '_default'
                if material_name:
                    if material_name not in textures:
                        textures[material_name] = {}
                        resolution_map[material_name] = {
                            'resolutions': [],
                            'default_res': '',
                            'variants': {}
                        }
                    if res_key not in resolution_map[material_name]['variants']:
                        resolution_map[material_name]['variants'][res_key] = {}
                        if resolution:
                            resolution_map[material_name]['resolutions'].append(resolution)
                    # 同类型多文件存入 extras
                    variant_entry = resolution_map[material_name]['variants'][res_key]
                    if texture_type not in variant_entry:
                        variant_entry[texture_type] = {
                            'filename': filename,
                            'full_path': filepath,
                            'type': texture_type
                        }
                    else:
                        ve = variant_entry[texture_type]
                        if 'extras' not in ve:
                            ve['extras'] = []
                        ve['extras'].append({'filename': filename, 'full_path': filepath, 'type': texture_type})
                    # 基准精度使用第一个遇到的
                    if material_name not in textures or texture_type not in textures[material_name]:
                        if material_name not in textures:
                            textures[material_name] = {}
                        # 优先从 _default 变体取，但要确认键存在
                        default_var = resolution_map[material_name]['variants'].get('_default', {})
                        if texture_type in default_var:
                            textures[material_name][texture_type] = default_var[texture_type]
                        elif not resolution:
                            textures[material_name][texture_type] = {
                                'filename': filename, 'full_path': filepath, 'type': texture_type
                            }
        
        # 设置默认精度
        for mat_name in list(resolution_map.keys()):
            res_list = resolution_map[mat_name]['resolutions']
            if res_list:
                sorted_ress = sorted(res_list, key=lambda x: _res_key(x))
                default_res = sorted_ress[0]
                resolution_map[mat_name]['default_res'] = default_res
                resolution_map[mat_name]['resolutions'] = sorted_ress
                # 用最高精度填充 textures
                highest = resolution_map[mat_name]['variants'].get(default_res, {})
                if mat_name not in textures:
                    textures[mat_name] = {}
                for k, v in highest.items():
                    textures[mat_name][k] = v
        
        return textures, resolution_map
    
    # 3. 有精度子目录：扫描每个精度目录
    combined_map = {}
    for mat_name, tex_info in base_textures.items():
        combined_map[mat_name] = tex_info
        resolution_map[mat_name] = {
            'resolutions': list(res_dirs),
            'default_res': res_dirs[0],  # 最高精度
            'variants': {}
        }
        # 把根目录贴图作为默认精度
        resolution_map[mat_name]['variants']['root'] = dict(tex_info)
    
    for res_dir in res_dirs:
        res_path = os.path.join(folder_path, res_dir)
        if not os.path.isdir(res_path):
            continue
        res_textures = scan_textures(res_path, config, recursive_dirs, exclude_dirs)
        for mat_name, tex_info in res_textures.items():
            if mat_name not in combined_map:
                combined_map[mat_name] = tex_info
            if mat_name not in resolution_map:
                resolution_map[mat_name] = {
                    'resolutions': list(res_dirs),
                    'default_res': res_dirs[0],
                    'variants': {}
                }
            resolution_map[mat_name]['variants'][res_dir] = dict(tex_info)
    
    return combined_map, resolution_map

def identify_texture_type(filename, config):
    """根据文件名识别贴图类型"""
    rules = config.get('texture_type_rules', {})
    
    candidates = []
    for texture_type, rule in rules.items():
        aliases = rule.get('aliases', [])
        priority = rule.get('priority', 99)
        
        for alias in aliases:
            lower_alias = alias.lower()
            lower_filename = filename.lower()
            
            if lower_filename.endswith('_' + lower_alias):
                candidates.append((priority, texture_type, alias))
            elif '_' + lower_alias + '_' in lower_filename:
                candidates.append((priority + 10, texture_type, alias))
            elif re.search(r'_\d+k_' + lower_alias + r'(?:\.|$)', lower_filename):
                candidates.append((priority + 5, texture_type, alias))
            elif re.search(r'_' + lower_alias + r'_\d+k(?:\.|$)', lower_filename):
                candidates.append((priority + 5, texture_type, alias))
    
    if not candidates:
        return None, None
    
    candidates.sort(key=lambda x: x[0])
    best = candidates[0]
    texture_type, matched_alias = best[1], best[2]
    
    if texture_type == 'normal' and '_nor_' in filename.lower():
        if '_nor_dx_' in filename.lower() or filename.lower().endswith('_nor_dx'):
            texture_type = 'normal_dx'
        elif '_nor_gl_' in filename.lower() or filename.lower().endswith('_nor_gl'):
            texture_type = 'normal_gl'
    
    elif texture_type == 'height':
        if '_bump_' in filename.lower() or filename.lower().endswith('_bump'):
            texture_type = 'height_bump'
        elif '_disp_' in filename.lower() or filename.lower().endswith('_disp'):
            texture_type = 'height_disp'
    
    return texture_type, matched_alias

def extract_material_name(filename, texture_type, matched_alias, config):
    """从文件名中提取材质名称"""
    lower_name = filename.lower()
    lower_alias = matched_alias.lower()
    
    if lower_name.endswith('_' + lower_alias):
        return filename[:-(len(matched_alias) + 1)]
    
    match = re.search(r'(.+?)_\d+k_' + lower_alias + r'(?:\.|$)', lower_name)
    if match:
        return filename[:match.end(1)]
    
    match = re.search(r'(.+?)_' + lower_alias + r'_\d+k(?:\.|$)', lower_name)
    if match:
        return filename[:match.end(1)]
    
    idx = lower_name.find('_' + lower_alias + '_')
    if idx >= 0:
        return filename[:idx]
    
    return filename

def _get_base_type(texture_type):
    """将 normal_dx/normal_gl/height_bump 等变体归一到基础类型"""
    if texture_type.startswith('normal_'):
        return 'normal'
    elif texture_type.startswith('height_'):
        return 'height'
    return texture_type


def _create_recipe_nodes(recipe, material_name, node_registry):
    """根据 recipe 创建中间节点，返回 {id: node_name}"""
    for node_def in recipe.get('nodes', []):
        node_id = node_def['id']
        node_type = node_def['type']
        node_name = f"{material_name}_{node_id}"
        try:
            new_node = cmds.createNode(node_type, name=node_name, skipSelect=True)
            node_registry[node_id] = new_node
            # 注册到 Hypershade 对应分类列表
            try:
                if cmds.getClassification(node_type, satisfies="shader"):
                    cmds.connectAttr(f"{new_node}.message", "defaultShaderList1.s", nextAvailable=True)
                elif cmds.getClassification(node_type, satisfies="texture"):
                    cmds.connectAttr(f"{new_node}.message", "defaultTextureList1.tx", nextAvailable=True)
                else:
                    cmds.connectAttr(f"{new_node}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
            except Exception:
                pass
            for attr, val in node_def.get('attrs', {}).items():
                try:
                    if attr == 'operation' and isinstance(val, int):
                        cmds.setAttr(f"{new_node}.{attr}", val)
                    else:
                        cmds.setAttr(f"{new_node}.{attr}", val)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Recipe] 创建节点失败 {node_name}({node_type}): {e}")


def _resolve_pbr_attr(attr_name, mapping):
    """从 mapping 表中查找 PBR 抽象属性名 → 当前着色器实际属性名
    例: baseColor → openPBRSurface 的 'baseColor', VRayMtl 的 'color'
    """
    prop = mapping.get(attr_name, {})
    return prop.get('node_attribute', attr_name)


def _format_conn_str(template, shader, node_registry, texture_nodes, mapping):
    """格式化连接字符串，处理 {pbr:xxx} 和 {node} 占位符"""
    import re
    # 先处理 {pbr:xxx} 占位符
    def replace_pbr(m):
        pbr_key = m.group(1)
        return _resolve_pbr_attr(pbr_key, mapping)
    result = re.sub(r'\{pbr:(\w+)\}', replace_pbr, template)
    # 再处理常规占位符 {shader}, {ao}, {ao_remap} 等
    return result.format(
        shader=shader,
        **{tid: node_registry.get(tid, tid) for tid in node_registry},
        **{ttype: texture_nodes.get(ttype, ttype) for ttype in texture_nodes}
    )


def _wire_recipe_connections(recipe, node_registry, texture_nodes, shader, mapping, available_types):
    """根据 recipe 连接所有属性，自动解析 {pbr:抽象名} 为当前着色器的真实属性名"""
    import re as _re
    for conn in recipe.get('connections', []):
        # 条件连接: if_has
        if_has = conn.get('if_has')
        if if_has and if_has not in available_types:
            continue
        try:
            from_str = _format_conn_str(conn['from'], shader, node_registry, texture_nodes, mapping)
            to_str = _format_conn_str(conn['to'], shader, node_registry, texture_nodes, mapping)

            # 检查目标属性是否需要反转 (e.g. roughness → reflectionGlossiness)
            pbr_match = _re.search(r'\{pbr:(\w+)\}', conn['to'])
            if pbr_match and mapping.get(pbr_match.group(1), {}).get('invert'):
                rev_name = f"{from_str.rsplit('.', 1)[0]}_rev"
                try:
                    rev = cmds.createNode('reverse', name=rev_name, skipSelect=True)
                    cmds.connectAttr(f"{rev}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
                    cmds.connectAttr(from_str, f"{rev}.inputX", force=True)
                    cmds.connectAttr(from_str, f"{rev}.inputY", force=True)
                    cmds.connectAttr(from_str, f"{rev}.inputZ", force=True)
                    cmds.connectAttr(f"{rev}.outputX", to_str, force=True)
                    continue
                except Exception:
                    pass

            cmds.connectAttr(from_str, to_str, force=True)
        except Exception as e:
            # 尝试复合属性子属性自动展开
            expanded = False
            try:
                to_parts = to_str.rsplit('.', 1)
                for suffix in ('X', 'Y', 'Z', 'R', 'G', 'B'):
                    child_to = f"{to_parts[0]}.{to_parts[1]}{suffix}"
                    if cmds.objExists(child_to):
                        cmds.connectAttr(from_str, child_to, force=True)
                        expanded = True
                        break
            except Exception:
                pass
            if not expanded:
                print(f"[Recipe] 连接失败: {from_str} → {to_str}: {e}")


def create_material(material_name, textures, config, existing_shader=None):
    """创建OpenPBR材质并连接贴图（支持 connection_recipes 子网络模板）
    
    Args:
        material_name: 材质名称
        textures: 贴图信息 dict
        config: pbr_mapping.json 配置
        existing_shader: 已有着色器节点名，传入则不创建新shader而是连接贴图到现有shader
    """
    if not IN_MAYA:
        return None, None

    material_type = config.get('default_material_type', 'openPBRSurface')

    try:
        print(f"[PBR] create_material: name={material_name}, type={material_type}, textures={list(textures.keys())}")
        if existing_shader and cmds.objExists(existing_shader):
            shader = existing_shader
            # 尝试获取已有shader的类型作为material_type
            existing_type = cmds.nodeType(shader)
            all_mappings = config.get('material_property_mappings', {})
            if existing_type in all_mappings:
                material_type = existing_type
        else:
            # 使用 createNode 创建（兼容 rsStandardMaterial 等 renderer 特定节点）
            # shadingNode 有时不适用 renderer 专属材质
            created = False
            for create_method, args in (
                (cmds.createNode, (material_type,)),
                (cmds.shadingNode, (material_type,)),
            ):
                try:
                    if create_method == cmds.shadingNode:
                        shader = cmds.shadingNode(material_type, asShader=True, name=material_name)
                    else:
                        shader = cmds.createNode(material_type, name=material_name, skipSelect=True)
                    created = True
                    break
                except Exception:
                    continue

            if not created:
                print(f"[PBR] 无法创建节点类型: {material_type}")
                return None

            actual_type = cmds.nodeType(shader)
            print(f"[PBR] 创建节点: {shader} (type={actual_type}, requested={material_type})")

            if actual_type == "unknown":
                print(f"[PBR] 错误: 节点类型 unknown — {material_type} 的渲染器插件可能未加载（如 redshift4maya）")
                print(f"[PBR] 节点已创建但属性不可用，跳过连接")
                return shader, None

            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=material_name + 'SG')
            # rsStandardMaterial 等节点使用 .out 而非 .outColor
            for out_attr in ('outColor', 'out'):
                if cmds.objExists(shader + '.' + out_attr):
                    cmds.connectAttr(shader + '.' + out_attr, sg + '.surfaceShader')
                    break
            else:
                # 都不存在则尝试不连接（SG 可手动指定）
                print(f"[PBR] 警告: {material_type} 无 outColor/out 输出属性")

        # 属性映射表
        all_mappings = config.get('material_property_mappings', {})
        node_mapping = all_mappings.get(material_type, {})
        mapping = node_mapping if node_mapping else config.get('openpbr_property_mapping', {})
        print(f"[PBR] mapping keys: {list(mapping.keys())}")

        # 标准化纹理类型，构建可用类型集合
        available_types = set()
        normalized_textures = {}
        for texture_type, info in textures.items():
            base = _get_base_type(texture_type)
            normalized_textures[base] = info
            available_types.add(base)

        # ── 读取 recipes 并匹配 ──
        recipes = config.get('connection_recipes', {})
        matched_recipes = []
        skip_direct = set()

        for recipe_name, recipe in recipes.items():
            requires = recipe.get('requires', [])
            requires_any = recipe.get('requires_any', [])
            exclusive = recipe.get('exclusive', [])
            optional = recipe.get('optional', False)

            if exclusive and any(t in available_types for t in exclusive):
                continue
            if requires and not all(t in available_types for t in requires):
                continue
            if requires_any and not any(t in available_types for t in requires_any):
                continue
            if not requires and not requires_any and not optional:
                continue

            matched_recipes.append((recipe_name, recipe))
            skip_direct.update(recipe.get('skip_direct', []))
            # 对于 optional 且无 requires 的（如 place2dTexture_shared），始终应用
            if optional and not requires and not requires_any:
                skip_direct_list = recipe.get('skip_direct', [])
                if skip_direct_list:
                    skip_direct.update(skip_direct_list)

        # ── 创建所有贴图的 file 节点，记录节点名 ──
        texture_nodes = {}       # texture_type → file node name
        shared_place2d = None

        # 检查是否有 place2dTexture_shared recipe
        for rname, recipe in matched_recipes:
            if recipe.get('connect_all_files'):
                recipe_nodes = {}
                _create_recipe_nodes(recipe, material_name, recipe_nodes)
                shared_place2d = recipe_nodes.get('coords')

        for texture_type, info in textures.items():
            base = _get_base_type(texture_type)
            if base not in mapping and base not in skip_direct:
                base_info = normalized_textures.get(base, {})
                base_mapping = mapping.get(base, {})
                if not base_mapping and base not in skip_direct:
                    print(f"[PBR] 跳过贴图 {texture_type}({base}): 不在mapping也不在skip_direct")
                    continue
            print(f"[PBR] 创建贴图节点: {texture_type}({base}) → {info['full_path']}")
            file_name = material_name + '_' + base + '_file'
            try:
                tex_node = cmds.shadingNode('file', asTexture=True, name=file_name)
                cmds.setAttr(tex_node + '.fileTextureName', info['full_path'], type='string')

                # 色彩空间
                cs = info.get('color_space', 'sRGB')
                cmds.setAttr(tex_node + '.colorSpace', cs, type='string')

                texture_nodes[base] = tex_node

                # 共享 place2dTexture
                if shared_place2d:
                    _connect_place2d(shared_place2d, tex_node)
            except Exception as e:
                print(f"[PBR] 创建贴图节点失败 {texture_type}({base}): {e}，跳过")

        # ── 执行匹配的 recipes ──
        recipe_registry = {}
        for rname, recipe in matched_recipes:
            if recipe.get('connect_all_files'):
                continue  # 已在上面处理
            recipe_nodes = {}
            _create_recipe_nodes(recipe, material_name, recipe_nodes)

            # per_texture: 为每个匹配的纹理类型单独创建节点和连接
            if recipe.get('per_texture'):
                for target_type in recipe.get('requires_any', []):
                    if target_type not in available_types or target_type not in texture_nodes:
                        continue
                    node_id = recipe['nodes'][0]['id'] + '_' + target_type
                    node_def = dict(recipe['nodes'][0])
                    node_def['id'] = node_id
                    node_name = f"{material_name}_{node_id}"
                    try:
                        new_node = cmds.createNode(node_def['type'], name=node_name, skipSelect=True)
                        recipe_nodes[node_id] = new_node
                        # 注册到 Hypershade
                        try:
                            cmds.connectAttr(f"{new_node}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
                        except Exception:
                            pass
                        for attr, val in node_def.get('attrs', {}).items():
                            try:
                                cmds.setAttr(f"{new_node}.{attr}", val)
                            except Exception:
                                pass
                        # 连接这个单独的实例
                        prop_info = mapping.get(target_type, {})
                        target_attr = prop_info.get('node_attribute', target_type)
                        source_node = texture_nodes[target_type]
                        # 需要反转时插入 reverse 节点 (glossiness = 1 - roughness)
                        if prop_info.get('invert'):
                            rev = cmds.createNode('reverse', name=f"{material_name}_rev_{target_type}", skipSelect=True)
                            try:
                                cmds.connectAttr(f"{rev}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
                            except Exception:
                                pass
                            cmds.connectAttr(f"{source_node}.outColorR", f"{rev}.inputX", force=True)
                            cmds.connectAttr(f"{source_node}.outColorR", f"{rev}.inputY", force=True)
                            cmds.connectAttr(f"{source_node}.outColorR", f"{rev}.inputZ", force=True)
                            source_attr = f"{rev}.outputX"
                        else:
                            source_attr = f"{source_node}.outColorR"
                        try:
                            cmds.connectAttr(source_attr, f"{new_node}.inputValue", force=True)
                            cmds.connectAttr(
                                f"{new_node}.outValue",
                                f"{shader}.{target_attr}", force=True)
                        except Exception as re:
                            # 直连 fallback
                            try:
                                cmds.connectAttr(source_attr, f"{shader}.{target_attr}", force=True)
                            except Exception:
                                print(f"[Recipe] per_texture 连接失败: {shader}.{target_attr}: {re}")
                    except Exception as e:
                        print(f"[Recipe] per_texture 节点创建失败 {node_name}: {e}")
            else:
                recipe_registry.update(recipe_nodes)
                _wire_recipe_connections(
                    recipe, recipe_nodes, texture_nodes,
                    shader, mapping, available_types)

        # ── 非 recipe 处理的贴图，走简单连接 ──
        for texture_type, info in textures.items():
            base = _get_base_type(texture_type)
            if base in skip_direct:
                continue
            if base not in mapping:
                continue

            prop_info = mapping[base]
            if prop_info.get('is_numeric', False):
                set_numeric_attribute(shader, prop_info.get('node_attribute', base), info['full_path'])
                continue
            if prop_info.get('is_combo', False):
                continue  # ARM/ORM 已由 recipe 处理

            tex_node = texture_nodes.get(base)
            if not tex_node:
                continue

            connect_texture_to_shader(tex_node, shader, prop_info)

        return shader, sg
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, str(e)


def _connect_place2d(place2d_node, file_node):
    """连接 place2dTexture 到 file 节点的 19 个属性"""
    place2d_attrs = [
        'c', 'tf', 'rf', 'mu', 'mv', 's', 'wu', 'wv',
        're', 'of', 'r', 'n', 'vt1', 'vt2', 'vt3', 'vc1'
    ]
    for attr in place2d_attrs:
        try:
            cmds.connectAttr(f"{place2d_node}.{attr}", f"{file_node}.{attr}")
        except Exception:
            pass
    try:
        cmds.connectAttr(f"{place2d_node}.o", f"{file_node}.uv")
        cmds.connectAttr(f"{place2d_node}.ofs", f"{file_node}.fs")
    except Exception:
        pass


def connect_texture_to_shader(tex_node, shader, prop_info):
    """简单直连：file 节点 → 着色器属性"""
    connection_type = prop_info.get('connection_type', 'color')
    attr_name = prop_info.get('node_attribute', '')

    if prop_info.get('requires_conversion', False):
        conversion_type = prop_info.get('conversion_node', 'bump2d')
        conv_node = cmds.shadingNode(conversion_type, asUtility=True,
                                     name=conversion_type + '_' + attr_name)
        if conversion_type == 'bump2d':
            cmds.connectAttr(tex_node + '.outColor', conv_node + '.bumpValue')
            cmds.connectAttr(conv_node + '.outNormal', shader + '.' + attr_name)
        else:
            cmds.connectAttr(tex_node + '.outColor', conv_node + '.displacement')
            cmds.connectAttr(conv_node + '.outDisplacement', shader + '.' + attr_name)
    else:
        if connection_type == 'color':
            cmds.connectAttr(tex_node + '.outColor', shader + '.' + attr_name)
        elif connection_type == 'scalar':
            if prop_info.get('invert'):
                rev = cmds.createNode('reverse', name=f"{shader}_rev_{attr_name}", skipSelect=True)
                try:
                    cmds.connectAttr(f"{rev}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
                except Exception:
                    pass
                cmds.connectAttr(tex_node + '.outColorR', rev + '.inputX', force=True)
                cmds.connectAttr(tex_node + '.outColorR', rev + '.inputY', force=True)
                cmds.connectAttr(tex_node + '.outColorR', rev + '.inputZ', force=True)
                cmds.connectAttr(rev + '.outputX', shader + '.' + attr_name, force=True)
            else:
                cmds.connectAttr(tex_node + '.outColorR', shader + '.' + attr_name, force=True)
        elif connection_type == 'vector':
            cmds.connectAttr(tex_node + '.outColor', shader + '.' + attr_name)


def connect_texture(shader, attr_name, texture_path, prop_info, config):
    """连接贴图到材质属性（保留旧接口兼容）"""
    texture_node = cmds.shadingNode('file', asTexture=True, name=os.path.basename(texture_path).replace('.', '_'))
    cmds.setAttr(texture_node + '.fileTextureName', texture_path, type='string')
    color_space = prop_info.get('color_space', 'sRGB')
    cmds.setAttr(texture_node + '.colorSpace', color_space, type='string')
    connect_texture_to_shader(texture_node, shader, prop_info)


def set_numeric_attribute(shader, attr_name, texture_path):
    """从贴图文件名提取数值并设置属性"""
    try:
        basename = os.path.splitext(os.path.basename(texture_path))[0]
        match = re.search(r'_(\d+\.?\d*)', basename)
        if match:
            value = float(match.group(1))
            cmds.setAttr(shader + '.' + attr_name, value)
    except Exception:
        pass

def export_zasset(material_name, textures, config, output_folder,
                  asset_folder=None, folder_name=None, extra_files=None,
                  resolution_map=None, selected_resolution=None,
                  thumb_source=None, source_meta=None):
    """导出为.zasset格式
    
    Args:
        material_name: 材质名称
        textures: 贴图信息 dict（来自 scan_textures）
        config: pbr_mapping.json 配置
        output_folder: 输出文件夹路径
        asset_folder: 资产根目录（用于查找缩略图和元数据）
        folder_name: 资产文件夹名（用于 {folderName} 变量替换）
        extra_files: 额外路由文件 dict {zip_target_path: disk_full_path}
        resolution_map: 精度映射表（来自 scan_textures_resolution_aware）
        selected_resolution: 选中的精度名，None 则打包所有精度
    """
    import tempfile, shutil

    header = _get_export_header()
    meta = {
        'id': str(uuid.uuid4()),
        'version': header['version'],
        'software': header['software'],
        'renderer': header['renderer'],
        'color_space': header['color_space'],
        'create_date': header['create_date'],
        'name': material_name,
        'name_cn': material_name,
        'node_type': config.get('default_material_type', 'openPBRSurface'),
        'category': guess_category(material_name, config),
        'tags': ['pbr'],
        'thumbnail_path': ''
    }

    if asset_folder and folder_name:
        if source_meta is not None:
            pass  # 使用调用方传入的预计算数据
        else:
            source_meta = read_source_metadata(asset_folder, [folder_name], config)
        for key, value in (source_meta or {}).items():
            if key in ('tags',) and isinstance(value, list):
                existing = meta.get('tags', [])
                meta['tags'] = list(dict.fromkeys(existing + value))
            elif key == 'description':
                meta['description'] = value
            elif key == 'source_url':
                meta['source_url'] = value
            elif key == 'author':
                meta['author'] = value
            else:
                meta[key] = value

    color_texture_path = None
    formats = set()
    properties = {}

    tmp_dir = tempfile.mkdtemp(prefix="pbr_build_")
    files_built = {}  # internal_path → disk_path

    try:
        # ── 多精度贴图处理 ──
        has_resolutions = resolution_map and resolution_map.get('resolutions', [])
        
        if has_resolutions:
            variants = resolution_map.get('variants', {})
            resolutions = resolution_map['resolutions']
            meta['resolutions'] = resolutions
            meta['default_resolution'] = resolution_map.get('default_res', resolutions[0])
            
            if selected_resolution and selected_resolution != '_all':
                # 只打包选中的精度
                selected_resolutions = [selected_resolution]
            else:
                selected_resolutions = resolutions
            
            for res in selected_resolutions:
                variant = variants.get(res, {})
                for texture_type, info in variant.items():
                    texture_path = info['full_path']
                    if os.path.isfile(texture_path):
                        tex_filename = os.path.basename(texture_path)
                        ext = os.path.splitext(tex_filename)[1].lower().lstrip('.')
                        if ext:
                            formats.add(ext)
                        files_built[f"textures/{res}/{tex_filename}"] = texture_path
                        
                        if color_texture_path is None:
                            base_types = ['baseColor', 'diffuse', 'albedo', 'color', 'col', 'diff']
                            bt = texture_type.split('_')[0] if '_' in texture_type else texture_type
                            if bt in base_types:
                                color_texture_path = texture_path
                    # 同类型 extras（如 .exr 与 .jpg 并存）
                    for extra in info.get('extras', []):
                        fp = extra['full_path']
                        if os.path.isfile(fp):
                            files_built[f"textures/{res}/{extra['filename']}"] = fp
                            ext = os.path.splitext(extra['filename'])[1].lower().lstrip('.')
                            if ext:
                                formats.add(ext)
                    
                    if texture_type not in properties:
                        properties[texture_type] = {
                            'type': 'texture',
                            'path': f"textures/{res}/{os.path.basename(info['filename'])}"
                        }
        else:
            # 原始逻辑：单精度
            for texture_type, info in textures.items():
                texture_path = info['full_path']
                properties[texture_type] = {
                    'type': 'texture',
                    'path': info['filename']
                }

                if os.path.isfile(texture_path):
                    tex_filename = os.path.basename(texture_path)
                    ext = os.path.splitext(tex_filename)[1].lower().lstrip('.')
                    if ext:
                        formats.add(ext)
                    files_built[f"textures/{tex_filename}"] = texture_path

                    if color_texture_path is None:
                        base_types = ['baseColor', 'diffuse', 'albedo', 'color', 'col', 'diff']
                        base_type = texture_type
                        if base_type.startswith('normal_'):
                            base_type = 'normal'
                        elif base_type.startswith('height_'):
                            base_type = 'height'
                        if base_type in base_types:
                            color_texture_path = texture_path
                # 同类型 extras
                for extra in info.get('extras', []):
                    fp = extra['full_path']
                    if os.path.isfile(fp):
                        files_built[f"textures/{extra['filename']}"] = fp
                        ext = os.path.splitext(extra['filename'])[1].lower().lstrip('.')
                        if ext:
                            formats.add(ext)

        if extra_files:
            for zip_path, disk_path in extra_files.items():
                files_built[zip_path] = disk_path
                ext = os.path.splitext(zip_path)[1].lower().lstrip('.')
                if ext:
                    formats.add(ext)

        meta['formats'] = sorted(formats)
        meta['properties'] = properties

        texture_map = {}
        for zip_path, disk_path in (extra_files or {}).items():
            if not zip_path.lower().endswith('.ma'):
                continue
            try:
                with open(disk_path, 'r', encoding='utf-8', errors='replace') as f:
                    ma_content = f.read()
                for match in re.finditer(r'setAttr\s+"\.ftn"\s+-type\s+"string"\s+"([^"]+)"', ma_content):
                    orig_path = match.group(1).replace('\\', '/')
                    orig_name = os.path.basename(orig_path)
                    if f"textures/{orig_name}" in files_built:
                        texture_map[orig_path] = f"textures/{orig_name}"
            except:
                pass
        if texture_map:
            meta['texture_map'] = texture_map

        if thumb_source is None and asset_folder and folder_name:
            thumb_source = find_existing_thumbnail(asset_folder, [folder_name], config)

        if not thumb_source:
            thumb_source = color_texture_path

        if thumb_source:
            try:
                from PIL import Image
                with Image.open(thumb_source) as img:
                    img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    if img.mode not in ('RGB', 'RGBA'):
                        img = img.convert('RGBA')
                    import io
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    thumbnail_data = buffer.getvalue()
                    thumb_path = os.path.join(tmp_dir, "thumb.sicon")
                    with open(thumb_path, 'wb') as f:
                        f.write(thumbnail_data)
                    files_built["thumb.sicon"] = thumb_path
                    meta['thumbnail_path'] = 'thumb.sicon'
            except Exception:
                pass

        asset_path = os.path.join(output_folder, material_name + '.zasset')
        success = ZassetBuilder.build(asset_path, files_built, meta)
        if success:
            return asset_path, None
        return None, "build failed"
    except Exception as e:
        return None, str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def guess_category(material_name, config):
    """根据材质名称猜测分类"""
    category_mapping = config.get('category_mapping', {})
    lower_name = material_name.lower()
    
    for category, keywords in category_mapping.items():
        for keyword in keywords:
            if keyword.lower() in lower_name:
                return category
    
    return 'AAAcustom'


def _get_import_category_path():
    """获取当前资产库分类文件夹路径（实时查询主窗口选中分类）"""
    try:
        import squirrel_asset_manager as _sam
        mw = getattr(_sam, 'main_window', None)
        if mw is None:
            return ''
        cat_tree = getattr(mw, '_get_active_category_tree', lambda: None)()
        cur_cat = cat_tree.get_active_category() if cat_tree else "custom"
        root_lib = cat_tree.get_active_root_lib() if cat_tree else "materials"
        if cur_cat == "all" or not cur_cat:
            cur_cat = "custom"
        lib = mw._active_mgr.get_library_path() if getattr(mw, '_active_mgr', None) else ""
        if lib and cur_cat:
            folder = mw._find_category_folder(cur_cat, root_lib)
            if folder and os.path.isdir(folder):
                return folder
            # 将分类内部路径分隔符 || 替换为系统路径分隔符
            safe_cat = cur_cat.replace('||', os.sep)
            # 避免 root_lib 重复（如 root_lib="textures"，safe_cat="textures\foliage"）
            if safe_cat.startswith(root_lib + os.sep):
                safe_cat = safe_cat[len(root_lib) + 1:]
            return os.path.join(lib, root_lib, safe_cat)
        return ''
    except Exception:
        import traceback
        traceback.print_exc()
        return ''


def _copy_zassets_to_category(src_folder, category_path):
    """将src_folder及其子目录下所有.zasset文件夹复制到category_path（扁平化）"""
    import shutil
    if not category_path or not os.path.isdir(src_folder):
        return 0
    os.makedirs(category_path, exist_ok=True)
    count = 0
    for dirpath, dirnames, filenames in os.walk(src_folder):
        # .zasset 是文件夹，需要在 dirnames 中查找
        for dn in list(dirnames):
            if dn.lower().endswith('.zasset'):
                src = os.path.join(dirpath, dn)
                dst = os.path.join(category_path, dn)
                try:
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    count += 1
                    print(f"[PBR Tool] 导入: {dn} -> {category_path}")
                except Exception as e:
                    print(f"[PBR Tool] 导入失败: {dn}: {e}")
    return count


class PBRToZAssetDialog(QtWidgets.QDialog):
    """PBR贴图转资产工具UI"""
    
    def __init__(self, parent=None):
        super(PBRToZAssetDialog, self).__init__(parent)
        self.setWindowTitle("PBR贴图转资产")
        self.setMinimumSize(1010, 760)
        self.setStyleSheet("""
            QDialog { background-color: #2a2a2a;  }
            QLabel { color: #d0d0d0;  }
            QLineEdit { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; 
                        padding: 5px 8px; color: #e0e0e0;  }
            QPushButton { background: #3a3a3a; color: #d0d0d0; border: none; 
                          padding: 7px 14px; border-radius: 4px;  }
            QPushButton:hover { background: #4a4a4a; }
            QPushButton:pressed { background: #2a2a2a; }
            QPushButton#okBtn { background: #5294e2; color: white; }
            QPushButton#okBtn:hover { background: #6ab0ff; }
            QTreeWidget { background: #2a2a2a; border: 1px solid #3a3a3a; color: #d0d0d0;  }
            QTreeWidget::item { padding: 4px; }
            QTreeWidget::item:hover { background: #333; }
            QTreeWidget::item:selected { background: #2d4a6f; }
            QTreeWidget::branch:open:has-children { image: none; }
            QTreeWidget::branch:closed:has-children { image: none; }
            QProgressBar { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; }
            QProgressBar::chunk { background: #5294e2; }
            QGroupBox { border: 1px solid #4a4a4a; border-radius: 4px; margin-top: 32px; padding-top: 8px; padding-bottom: 8px; padding-left: 8px; padding-right: 8px; }
            QGroupBox::title { color: #909090; font-weight: bold; subcontrol-origin: margin; subcontrol-position: top left; padding-left: 6px; padding-right: 6px; background: #2a2a2a; margin-top: -10px;  }
            QCheckBox { color: #d0d0d0;  }
            QComboBox { background: #333; border: 1px solid #4a4a4a; border-radius: 4px; 
                        padding: 3px; color: #d0d0d0;  }
            QComboBox QAbstractItemView { background: #333; border: 1px solid #4a4a4a;  }
            QScrollArea {  }
        """)
        
        self.config = load_config()
        self.textures = {}
        self._resolution_map = {}
        self._batch_results = []
        self.output_folder = ""
        
        self._setup_ui()
        self._rebuild_routing_ui()
        self._rebuild_meta_ui()

    def _on_cancel(self):
        if getattr(self, '_is_converting', False):
            self._is_cancelled = True
            self._cancel_btn.setEnabled(False)
            self._status_label.setText("正在中止...")
            try:
                import maya.cmds as _cmds
                _cmds.refresh()
            except Exception:
                pass
        else:
            self.close()

    def _on_help(self):
        import webbrowser
        import os
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = os.path.join(plugin_root, "Assets", "help", "pbr_to_zasset", "help.html")
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[PBR Tool] 帮助文件未找到:", help_path)
    
    def _setup_ui(self):
        """设置UI布局"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        
        # 主布局：左右两列
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet("QSplitter::handle { background: #3a3a3a; }")
        main_splitter.setSizes([550, 400])
        
        # 左侧：配置区域
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        
        # 输入文件夹选择
        input_group = QtWidgets.QGroupBox("贴图文件夹")
        input_layout = QtWidgets.QHBoxLayout(input_group)
        input_layout.setContentsMargins(8, 8, 8, 8)
        
        self._input_path = QtWidgets.QLineEdit()
        self._input_path.setPlaceholderText("选择包含PBR贴图的文件夹（勾选批量则选择父文件夹）...")
        input_layout.addWidget(self._input_path, 1)
        
        browse_btn = QtWidgets.QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_input_folder)
        input_layout.addWidget(browse_btn)
        
        left_layout.addWidget(input_group)
        
        # 输出文件夹选择
        output_group = QtWidgets.QGroupBox("输出位置")
        output_layout = QtWidgets.QHBoxLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)
        
        self._output_path = QtWidgets.QLineEdit()
        self._output_path.setPlaceholderText("选择资产输出文件夹...")
        output_layout.addWidget(self._output_path, 1)
        
        output_browse_btn = QtWidgets.QPushButton("浏览...")
        output_browse_btn.clicked.connect(self._browse_output_folder)
        output_layout.addWidget(output_browse_btn)
        
        left_layout.addWidget(output_group)
        
        # 选项
        options_group = QtWidgets.QGroupBox("转换选项")
        options_layout = QtWidgets.QVBoxLayout(options_group)
        options_layout.setContentsMargins(8, 8, 8, 8)
        
        self._export_zasset = QtWidgets.QCheckBox("导出.zasset资产文件")
        self._export_zasset.setChecked(True)
        options_layout.addWidget(self._export_zasset)
        
        self._batch_mode = QtWidgets.QCheckBox("批量模式（子文件夹作为独立资产）")
        self._batch_mode.setToolTip("选择父文件夹后，将每个子文件夹作为一个独立的贴图资产处理")
        options_layout.addWidget(self._batch_mode)

        # ── 递归扫描选项 ──
        self._recursive_scan = QtWidgets.QCheckBox("递归扫描子文件夹中的贴图")
        self._recursive_scan.setToolTip(
            "开启后扫描子文件夹内部的贴图文件（如 tex/ 目录下的贴图）"
        )
        self._recursive_scan.toggled.connect(self._on_recursive_toggled)
        options_layout.addWidget(self._recursive_scan)

        rec_row = QtWidgets.QHBoxLayout()
        rec_label = QtWidgets.QLabel("模式:")
        rec_label.setStyleSheet("color: #909090; font-size: 13px;")
        rec_label.setFixedWidth(36)
        rec_row.addWidget(rec_label)
        self._recursive_mode = QtWidgets.QComboBox()
        self._recursive_mode.addItems(["自动递归全部子文件夹", "手动指定子文件夹名"])
        self._recursive_mode.setToolTip(
            "自动 — 递归所有子文件夹（含嵌套）\n"
            "手动 — 只递归下方指定的文件夹名"
        )
        rec_row.addWidget(self._recursive_mode, 1)
        rec_row.setContentsMargins(0, 0, 0, 0)
        rec_widget = QtWidgets.QWidget()
        rec_widget.setLayout(rec_row)
        rec_widget.setVisible(False)
        self._recursive_row1 = rec_widget
        options_layout.addWidget(rec_widget)

        rec_row2 = QtWidgets.QHBoxLayout()
        rec_label2 = QtWidgets.QLabel("文件夹:")
        rec_label2.setStyleSheet("color: #909090; font-size: 13px;")
        rec_label2.setFixedWidth(36)
        rec_row2.addWidget(rec_label2)
        self._recursive_dirs_edit = QtWidgets.QLineEdit()
        self._recursive_dirs_edit.setPlaceholderText("手动时填写，逗号分隔（如 tex, textures）")
        self._recursive_dirs_edit.setToolTip("只递归这些名称的子文件夹")
        rec_row2.addWidget(self._recursive_dirs_edit, 1)
        rec_row2.setContentsMargins(0, 0, 0, 0)
        rec_widget2 = QtWidgets.QWidget()
        rec_widget2.setLayout(rec_row2)
        rec_widget2.setVisible(False)
        self._recursive_row2 = rec_widget2
        options_layout.addWidget(rec_widget2)

        rec_row3 = QtWidgets.QHBoxLayout()
        rec_label3 = QtWidgets.QLabel("排除:")
        rec_label3.setStyleSheet("color: #909090; font-size: 13px;")
        rec_label3.setFixedWidth(36)
        rec_row3.addWidget(rec_label3)
        self._exclude_dirs_edit = QtWidgets.QLineEdit()
        self._exclude_dirs_edit.setPlaceholderText("排除的文件夹，逗号分隔（如 Thumbs, previews）")
        self._exclude_dirs_edit.setToolTip("递归时跳过这些名称的文件夹")
        rec_row3.addWidget(self._exclude_dirs_edit, 1)
        rec_row3.setContentsMargins(0, 0, 0, 0)
        rec_widget3 = QtWidgets.QWidget()
        rec_widget3.setLayout(rec_row3)
        rec_widget3.setVisible(False)
        self._recursive_row3 = rec_widget3
        options_layout.addWidget(rec_widget3)

        self._import_to_category = QtWidgets.QCheckBox("转换后导入当前分类")
        self._import_to_category.setChecked(False)
        self._import_to_category.setToolTip("转换完成后将zasset文件拷贝到当前资产库分类文件夹（只拷贝zasset，不拷贝文件夹结构）")
        options_layout.addWidget(self._import_to_category)
        
        # ── 精度选项 ──
        res_sep = QtWidgets.QFrame()
        res_sep.setFrameShape(QtWidgets.QFrame.HLine)
        res_sep.setStyleSheet("color: #3a3a3a;")
        options_layout.addWidget(res_sep)
        
        res_label = QtWidgets.QLabel("多精度贴图:")
        res_label.setStyleSheet("color: #5294e2; font-weight: bold;")
        options_layout.addWidget(res_label)
        
        self._pack_all_res = QtWidgets.QCheckBox("打包所有精度（贴图按精度存入子目录）")
        self._pack_all_res.setChecked(True)
        self._pack_all_res.setToolTip("勾选后将所有精度的贴图打包进.zasset（textures/2k/、textures/4k/等），导入时可选")
        options_layout.addWidget(self._pack_all_res)
        
        left_layout.addWidget(options_group)
        
        # ── 文件路由配置 ──
        routing_group = QtWidgets.QGroupBox("文件路由配置")
        routing_layout = QtWidgets.QVBoxLayout(routing_group)
        routing_layout.setContentsMargins(8, 8, 8, 8)
        routing_layout.setSpacing(4)
        
        self._routing_widgets = []
        self._routing_layout = QtWidgets.QVBoxLayout()
        self._routing_layout.setSpacing(4)
        routing_layout.addLayout(self._routing_layout)
        
        routing_btn_layout = QtWidgets.QHBoxLayout()
        routing_btn_layout.setSpacing(6)
        add_route_btn = QtWidgets.QPushButton("+ 添加路由")
        add_route_btn.setStyleSheet("font-size: 13px; padding: 5px 12px;")
        add_route_btn.clicked.connect(self._add_routing_row)
        routing_btn_layout.addWidget(add_route_btn)
        save_routing_btn = QtWidgets.QPushButton("保存路由")
        save_routing_btn.setObjectName("okBtn")
        save_routing_btn.clicked.connect(self._save_routing_config)
        routing_btn_layout.addWidget(save_routing_btn)
        routing_btn_layout.addStretch()
        routing_layout.addLayout(routing_btn_layout)
        
        left_layout.addWidget(routing_group)
        
        # 元数据源配置
        meta_group = QtWidgets.QGroupBox("元数据源配置")
        meta_layout = QtWidgets.QVBoxLayout(meta_group)
        meta_layout.setContentsMargins(8, 8, 8, 8)
        meta_layout.setSpacing(6)
        
        # ── 缩略图搜索路径 ──
        thumb_label = QtWidgets.QLabel("缩略图搜索路径:")
        thumb_label.setStyleSheet("color: #5294e2; font-weight: bold;")
        meta_layout.addWidget(thumb_label)
        
        self._thumb_paths_layout = QtWidgets.QVBoxLayout()
        self._thumb_paths_layout.setSpacing(3)
        meta_layout.addLayout(self._thumb_paths_layout)
        
        thumb_add_btn = QtWidgets.QPushButton("+ 添加路径")
        thumb_add_btn.setStyleSheet("font-size: 13px; padding: 5px 12px;")
        thumb_add_btn.clicked.connect(lambda: self._add_thumb_path_row())
        meta_layout.addWidget(thumb_add_btn)
        
        # ── 分隔线 ──
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("color: #3a3a3a;")
        meta_layout.addWidget(sep)
        
        # ── 元数据源 ──
        source_label = QtWidgets.QLabel("元数据源:")
        source_label.setStyleSheet("color: #5294e2; font-weight: bold;")
        meta_layout.addWidget(source_label)
        
        self._meta_scroll = QtWidgets.QScrollArea()
        self._meta_scroll.setWidgetResizable(True)
        self._meta_scroll.setMinimumHeight(150)
        self._meta_scroll.setMaximumHeight(270)
        self._meta_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self._meta_container = QtWidgets.QWidget()
        self._meta_container.setStyleSheet("background: transparent;")
        self._meta_container_layout = QtWidgets.QVBoxLayout(self._meta_container)
        self._meta_container_layout.setContentsMargins(0, 0, 0, 0)
        self._meta_container_layout.setSpacing(4)
        self._meta_scroll.setWidget(self._meta_container)
        meta_layout.addWidget(self._meta_scroll)
        
        meta_btn_layout = QtWidgets.QHBoxLayout()
        meta_btn_layout.setSpacing(6)
        add_source_btn = QtWidgets.QPushButton("+ 添加源")
        add_source_btn.setStyleSheet("font-size: 13px; padding: 4px 10px;")
        add_source_btn.clicked.connect(self._add_meta_source)
        meta_btn_layout.addWidget(add_source_btn)
        meta_btn_layout.addStretch()
        save_meta_btn = QtWidgets.QPushButton("保存配置")
        save_meta_btn.setObjectName("okBtn")
        save_meta_btn.clicked.connect(self._save_meta_config)
        meta_btn_layout.addWidget(save_meta_btn)
        meta_layout.addLayout(meta_btn_layout)
        
        left_layout.addWidget(meta_group)
        
        left_layout.addStretch()
        main_splitter.addWidget(left_widget)
        
        # 右侧：信息显示区域
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        
        # 材质预览
        preview_group = QtWidgets.QGroupBox("识别到的材质")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        
        preview_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        
        self._material_tree = QtWidgets.QTreeWidget()
        self._material_tree.setHeaderLabel("材质和贴图")
        self._material_tree.setMinimumHeight(150)
        preview_splitter.addWidget(self._material_tree)
        
        self._preview_tree = QtWidgets.QTreeWidget()
        self._preview_tree.setHeaderLabel(".zasset 结构预览")
        self._preview_tree.setMinimumHeight(120)
        preview_splitter.addWidget(self._preview_tree)
        
        preview_layout.addWidget(preview_splitter, 1)
        
        self._scan_btn = QtWidgets.QPushButton("扫描贴图")
        self._scan_btn.clicked.connect(self._scan_textures)
        preview_layout.addWidget(self._scan_btn)
        
        right_layout.addWidget(preview_group, 1)
        
        # 进度条
        self._progress = QtWidgets.QProgressBar()
        self._progress.setVisible(False)
        right_layout.addWidget(self._progress)
        
        # 状态栏
        self._status_label = QtWidgets.QLabel("就绪")
        self._status_label.setStyleSheet("color: #808080; font-size: 12px;")
        right_layout.addWidget(self._status_label)
        main_splitter.addWidget(right_widget)
        
        layout.addWidget(main_splitter)
        
        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()
        
        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedSize(34, 34)
        help_btn.setToolTip("使用帮助")
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none;"
            "font-size: 18px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_help)
        btn_layout.addWidget(help_btn)
        
        self._cancel_btn = QtWidgets.QPushButton("取消")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)
        
        self._ok_btn = QtWidgets.QPushButton("转换")
        self._ok_btn.setObjectName("okBtn")
        self._ok_btn.setFixedWidth(180)
        self._ok_btn.clicked.connect(self._convert)
        btn_layout.addWidget(self._ok_btn)
        
        layout.addLayout(btn_layout)
    
    def _browse_input_folder(self):
        """浏览输入文件夹"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择贴图文件夹", "", 
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._input_path.setText(folder)
    
    def _browse_output_folder(self):
        """浏览输出文件夹"""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择输出文件夹", "", 
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._output_path.setText(folder)
    
    def _add_thumb_path_row(self, text=""):
        """添加一行缩略图搜索路径"""
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        path_edit = QtWidgets.QLineEdit(text)
        path_edit.setObjectName("thumb_path")
        path_edit.setPlaceholderText("{folderName}_ma_fileDependencies/thumbnail.png")
        row_layout.addWidget(path_edit, 1)
        
        del_btn = QtWidgets.QPushButton("×")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("color: #e06060; font-weight: bold; padding: 0;")
        del_btn.clicked.connect(lambda: (row.deleteLater(), None))
        row_layout.addWidget(del_btn)
        
        self._thumb_paths_layout.addWidget(row)
    
    def _add_routing_row(self, folder_name="", exts_text=""):
        """添加一行路由配置"""
        frame = QtWidgets.QFrame()
        frame.setStyleSheet("QFrame { border: 1px solid #3a3a3a; border-radius: 4px; padding: 4px; }")
        layout = QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        
        layout.addWidget(QtWidgets.QLabel("文件夹:"))
        folder_edit = QtWidgets.QLineEdit(folder_name)
        folder_edit.setObjectName("route_folder")
        folder_edit.setPlaceholderText("textures / root")
        folder_edit.setFixedWidth(140)
        layout.addWidget(folder_edit)
        
        layout.addWidget(QtWidgets.QLabel("扩展名:"))
        exts_edit = QtWidgets.QLineEdit(exts_text)
        exts_edit.setObjectName("route_exts")
        exts_edit.setPlaceholderText(".jpg .png .exr")
        layout.addWidget(exts_edit, 1)
        
        del_btn = QtWidgets.QPushButton("×")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("color: #e06060; font-weight: bold; padding: 0;")
        del_btn.clicked.connect(lambda: (frame.deleteLater(), None))
        layout.addWidget(del_btn)
        
        self._routing_layout.addWidget(frame)
    
    def _rebuild_routing_ui(self):
        """从配置重建路由UI"""
        for i in reversed(range(self._routing_layout.count())):
            w = self._routing_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        routing = self.config.get('file_routing', {})
        for folder, ext_list in routing.items():
            self._add_routing_row(folder, ' '.join(ext_list))
    
    def _save_routing_config(self):
        """从UI收集并保存文件路由配置"""
        routing = {}
        for i in range(self._routing_layout.count()):
            frame = self._routing_layout.itemAt(i).widget()
            if not frame:
                continue
            folder_edit = frame.findChild(QtWidgets.QLineEdit, "route_folder")
            exts_edit = frame.findChild(QtWidgets.QLineEdit, "route_exts")
            if not folder_edit or not exts_edit:
                continue
            folder = folder_edit.text().strip()
            exts = exts_edit.text().strip().split()
            if folder and exts:
                routing[folder] = [e if e.startswith('.') else '.' + e for e in exts]
        self.config['file_routing'] = routing
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Assets', 'preset', 'pbr_mapping.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            self._status_label.setText("路由配置已保存")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存失败", f"无法保存配置:\n{e}")
    
    def _rebuild_meta_ui(self):
        """从配置重建元数据源UI和缩略图路径"""
        # 清除缩略图路径行
        for i in reversed(range(self._thumb_paths_layout.count())):
            w = self._thumb_paths_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        paths = self.config.get('thumbnail_search_paths', [])
        for p in paths:
            self._add_thumb_path_row(p)
        
        # 清除元数据源
        for i in reversed(range(self._meta_container_layout.count())):
            w = self._meta_container_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        sources = self.config.get('metadata_sources', [])
        for src in sources:
            self._add_meta_source_widget(src)
    
    def _add_meta_source_widget(self, data=None):
        """添加一个元数据源配置widget"""
        data = data or {}
        frame = QtWidgets.QFrame()
        frame.setStyleSheet("QFrame { border: 1px solid #3a3a3a; border-radius: 4px; padding: 4px; }")
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        
        header = QtWidgets.QHBoxLayout()
        idx = self._meta_container_layout.count() + 1
        title = QtWidgets.QLabel(f"源 {idx}")
        title.setStyleSheet("color: #5294e2; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        del_btn = QtWidgets.QPushButton("删除")
        del_btn.setStyleSheet("color: #e06060; font-size: 11px; padding: 2px 8px;")
        del_btn.clicked.connect(lambda: (frame.deleteLater(), None))
        header.addWidget(del_btn)
        layout.addLayout(header)
        
        pattern_layout = QtWidgets.QHBoxLayout()
        pattern_layout.addWidget(QtWidgets.QLabel("文件路径模板:"))
        pattern_edit = QtWidgets.QLineEdit(data.get('file_pattern', ''))
        pattern_edit.setObjectName("meta_pattern")
        pattern_edit.setPlaceholderText("{folderName}_ma_fileDependencies/{folderName}.zooInfo")
        pattern_layout.addWidget(pattern_edit, 1)
        layout.addLayout(pattern_layout)
        
        fmt_layout = QtWidgets.QHBoxLayout()
        fmt_layout.addWidget(QtWidgets.QLabel("文件格式:"))
        fmt_combo = QtWidgets.QComboBox()
        fmt_combo.setObjectName("meta_format")
        fmt_combo.addItems(["json", "txt"])
        val = data.get('file_format', 'json')
        fmt_combo.setCurrentIndex(0 if val == 'json' else 1)
        fmt_layout.addWidget(fmt_combo)
        fmt_layout.addStretch()
        layout.addLayout(fmt_layout)
        
        mapping_label = QtWidgets.QLabel("字段映射:")
        mapping_label.setStyleSheet("color: #909090; font-size: 11px;")
        layout.addWidget(mapping_label)
        
        mapping_layout = QtWidgets.QVBoxLayout()
        mapping_layout.setObjectName("meta_mappings")
        mapping_layout.setSpacing(2)
        layout.addLayout(mapping_layout)
        
        add_field_btn = QtWidgets.QPushButton("+ 添加字段映射")
        add_field_btn.setStyleSheet("font-size: 13px; padding: 4px 10px;")
        add_field_btn.clicked.connect(
            lambda: self._add_meta_field_row(mapping_layout)
        )
        layout.addWidget(add_field_btn)
        
        for field in data.get('field_mapping', []):
            self._add_meta_field_row(
                mapping_layout,
                source=field.get('source', ''),
                target=field.get('target', ''),
                processor=field.get('processor', '')
            )
        
        self._meta_container_layout.addWidget(frame)
    
    def _add_meta_field_row(self, parent_layout, source="", target="", processor=""):
        """添加一行字段映射"""
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        src_edit = QtWidgets.QLineEdit(source)
        src_edit.setObjectName("field_source")
        src_edit.setPlaceholderText("源字段")
        src_edit.setFixedWidth(130)
        row_layout.addWidget(src_edit)
        
        arrow = QtWidgets.QLabel("→")
        arrow.setStyleSheet("color: #5294e2;")
        arrow.setFixedWidth(20)
        row_layout.addWidget(arrow)
        
        tgt_edit = QtWidgets.QLineEdit(target)
        tgt_edit.setObjectName("field_target")
        tgt_edit.setPlaceholderText("目标字段")
        tgt_edit.setFixedWidth(130)
        row_layout.addWidget(tgt_edit)
        
        proc_combo = QtWidgets.QComboBox()
        proc_combo.setObjectName("field_processor")
        proc_combo.addItems(["none", "split_comma", "first_line"])
        proc_combo.setCurrentText(processor if processor else "none")
        proc_combo.setToolTip(
            "none — 不做处理，值是什么就用什么（推荐，适用大多数情况）\n"
            "split_comma — 按逗号分割字符串为列表（如 \"a,b,c\" → [\"a\",\"b\",\"c\"]）\n"
            "first_line — 只取文本第一行（用于多行文本只需标题）"
        )
        row_layout.addWidget(proc_combo)
        
        del_btn = QtWidgets.QPushButton("×")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("color: #e06060; font-weight: bold; padding: 0;")
        del_btn.clicked.connect(lambda: (row.deleteLater(), None))
        row_layout.addWidget(del_btn)
        
        parent_layout.addWidget(row)
    
    def _add_meta_source(self):
        """UI按钮回调：添加空白元数据源"""
        self._add_meta_source_widget({})
    
    def _collect_meta_config(self):
        """从UI收集元数据源配置"""
        sources = []
        for i in range(self._meta_container_layout.count()):
            frame = self._meta_container_layout.itemAt(i).widget()
            if not frame or not isinstance(frame, QtWidgets.QFrame):
                continue
            
            pattern_edit = frame.findChild(QtWidgets.QLineEdit, "meta_pattern")
            fmt_combo = frame.findChild(QtWidgets.QComboBox, "meta_format")
            if not pattern_edit or not fmt_combo:
                continue
            
            fields = []
            mapping_layout = None
            for child in frame.findChildren(QtWidgets.QVBoxLayout):
                if child.objectName() == "meta_mappings":
                    mapping_layout = child
                    break
            
            if mapping_layout:
                for k in range(mapping_layout.count()):
                    row = mapping_layout.itemAt(k).widget()
                    if not row or not isinstance(row, QtWidgets.QWidget):
                        continue
                    src = row.findChild(QtWidgets.QLineEdit, "field_source")
                    tgt = row.findChild(QtWidgets.QLineEdit, "field_target")
                    proc = row.findChild(QtWidgets.QComboBox, "field_processor")
                    if src and tgt and proc:
                        fields.append({
                            'source': src.text(),
                            'target': tgt.text(),
                            'processor': proc.currentText()
                        })
            
            sources.append({
                'file_pattern': pattern_edit.text(),
                'file_format': fmt_combo.currentText(),
                'field_mapping': fields
            })
        
        return sources
    
    def _save_meta_config(self):
        """保存元数据源配置和缩略图路径到 pbr_mapping.json"""
        sources = self._collect_meta_config()
        self.config['metadata_sources'] = sources
        
        paths = []
        for i in range(self._thumb_paths_layout.count()):
            row = self._thumb_paths_layout.itemAt(i).widget()
            if not row:
                continue
            edit = row.findChild(QtWidgets.QLineEdit, "thumb_path")
            if edit and edit.text().strip():
                paths.append(edit.text().strip())
        self.config['thumbnail_search_paths'] = paths
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Assets', 'preset', 'pbr_mapping.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            self._status_label.setText("配置已保存")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "保存失败", f"无法保存配置:\n{e}")
    
    def _on_recursive_toggled(self, checked):
        """递归扫描复选框切换"""
        self._recursive_row1.setVisible(checked)
        self._recursive_row2.setVisible(checked)
        self._recursive_row3.setVisible(checked)

    def _get_recursive_params(self):
        """返回 (recursive_dirs, exclude_dirs) 或 (None, None)"""
        if not self._recursive_scan.isChecked():
            return None, None
        exclude = [d.strip() for d in self._exclude_dirs_edit.text().split(',') if d.strip()]
        if self._recursive_mode.currentIndex() == 0:
            return [], exclude  # 空列表 = 自动递归全部
        else:
            dirs = [d.strip() for d in self._recursive_dirs_edit.text().split(',') if d.strip()]
            return dirs if dirs else [], exclude

    def _scan_textures(self):
        """扫描贴图"""
        folder_path = self._input_path.text()
        if not folder_path or not os.path.exists(folder_path):
            QtWidgets.QMessageBox.warning(self, "警告", "请选择有效的贴图文件夹")
            return

        import logging
        logging.basicConfig(level=logging.DEBUG, format='[DEBUG] %(message)s')
        log = logging.getLogger('pbr_scan')
        log.debug(f"开始扫描: {folder_path}")
        log.debug(f"config texture_type_rules: {len(self.config.get('texture_type_rules', {}))} 条")
        log.debug(f"config file_routing: {list(self.config.get('file_routing', {}).keys())}")

        self._status_label.setText("正在扫描...")
        QtWidgets.QApplication.processEvents()
        
        self._batch_results = []
        self.textures = {}
        self._resolution_map = {}
        
        rec_dirs, exc_dirs = self._get_recursive_params()
        log.debug(f"递归参数: rec_dirs={rec_dirs}, exc_dirs={exc_dirs}")
        
        if self._batch_mode.isChecked():
            subfolders = [f for f in os.listdir(folder_path)
                         if os.path.isdir(os.path.join(folder_path, f))]
            subfolders.sort()
            if not subfolders:
                self._status_label.setText("未发现子文件夹")
                self._update_material_tree()
                self._update_preview_tree(folder_path)
                return
            
            for sub in subfolders:
                sub_path = os.path.join(folder_path, sub)
                tex, res_map = scan_textures_resolution_aware(sub_path, self.config, rec_dirs, exc_dirs)
                for mat_name, tex_info in tex.items():
                    self._batch_results.append((sub, mat_name, tex_info))
                    if mat_name in res_map:
                        self._resolution_map[f"{sub}/{mat_name}"] = res_map[mat_name]
            
            total_assets = len(self._batch_results)
            total_tex = sum(len(t) for _, _, t in self._batch_results)
            self._status_label.setText(f"批量发现 {total_assets} 个资产，{total_tex} 张贴图")
            log.debug(f"批量扫描结果: {total_assets} 个资产")
        else:
            self.textures, self._resolution_map = scan_textures_resolution_aware(folder_path, self.config, rec_dirs, exc_dirs)
            if self.textures:
                count = len(self.textures)
                total_tex = sum(len(t) for t in self.textures.values())
                res_count = sum(1 for v in self._resolution_map.values() if v.get('resolutions'))
                log.debug(f"扫描结果: {count} 个材质, {total_tex} 张贴图, {res_count} 个多精度")
                if res_count:
                    self._status_label.setText(f"发现 {count} 个材质，{total_tex} 张贴图（含 {res_count} 个多精度材质）")
                else:
                    self._status_label.setText(f"发现 {count} 个材质，{total_tex} 张贴图")
            else:
                self._status_label.setText("未发现有效的PBR贴图")
                log.debug("扫描结果: 未发现任何贴图")
        
        self._update_material_tree()
        self._update_preview_tree(folder_path)
    
    def _update_material_tree(self):
        """更新材质树"""
        self._material_tree.clear()
        
        if self._batch_results:
            for asset_name, material_name, tex_info in self._batch_results:
                mat_item = QtWidgets.QTreeWidgetItem([f"{asset_name} ({material_name})"])
                mat_item.setForeground(0, QtGui.QColor("#5294e2"))
                # 显示精度信息
                res_key = f"{asset_name}/{material_name}"
                self._add_resolution_info_to_item(mat_item, res_key)
                for tex_type, info in sorted(tex_info.items()):
                    tex_item = QtWidgets.QTreeWidgetItem([info['filename']])
                    tex_item.setForeground(0, QtGui.QColor("#909090"))
                    mat_item.addChild(tex_item)
                self._material_tree.addTopLevelItem(mat_item)
                mat_item.setExpanded(True)
        else:
            for material_name, tex_info in sorted(self.textures.items()):
                mat_item = QtWidgets.QTreeWidgetItem([material_name])
                mat_item.setForeground(0, QtGui.QColor("#5294e2"))
                self._add_resolution_info_to_item(mat_item, material_name)
                for tex_type, info in sorted(tex_info.items()):
                    tex_item = QtWidgets.QTreeWidgetItem([info['filename']])
                    tex_item.setForeground(0, QtGui.QColor("#909090"))
                    mat_item.addChild(tex_item)
                self._material_tree.addTopLevelItem(mat_item)
                mat_item.setExpanded(True)
    
    def _add_resolution_info_to_item(self, tree_item, material_key):
        """如果材质有多精度信息，在树节点上添加精度标签"""
        res_info = self._resolution_map.get(material_key, {})
        resolutions = res_info.get('resolutions', [])
        if resolutions:
            default = res_info.get('default_res', resolutions[0])
            label = f" [{', '.join(resolutions)}] 默认:{default}"
            current_text = tree_item.text(0)
            tree_item.setText(0, current_text + label)
            tree_item.setForeground(0, QtGui.QColor("#e67e22"))
    
    def _update_preview_tree(self, folder_path):
        """更新 .zasset 打包结构预览树"""
        self._preview_tree.clear()
        
        root_item = QtWidgets.QTreeWidgetItem([".zasset 内部结构"])
        root_item.setForeground(0, QtGui.QColor("#5294e2"))
        self._preview_tree.addTopLevelItem(root_item)
        
        meta_item = QtWidgets.QTreeWidgetItem(["meta.json"])
        meta_item.setForeground(0, QtGui.QColor("#909090"))
        root_item.addChild(meta_item)
        
        if self.config.get('thumbnail_search_paths'):
            thumb_item = QtWidgets.QTreeWidgetItem(["thumb.sicon"])
            thumb_item.setForeground(0, QtGui.QColor("#909090"))
            root_item.addChild(thumb_item)
        
        folders = {}
        routing = self.config.get('file_routing', {})
        
        # 检测是否有分辨率子目录
        has_resolution = False
        if self._resolution_map:
            for v in self._resolution_map.values():
                if v.get('resolutions'):
                    has_resolution = True
                    break
        
        if has_resolution:
            # 显示带精度子目录的结构预览
            textures_item = QtWidgets.QTreeWidgetItem(["textures/"])
            textures_item.setForeground(0, QtGui.QColor("#5294e2"))
            root_item.addChild(textures_item)
            
            # 收集所有精度
            all_res = set()
            for v in self._resolution_map.values():
                for r in v.get('resolutions', []):
                    all_res.add(r)
            
            for res in sorted(all_res, key=lambda x: _res_key(x)):
                res_item = QtWidgets.QTreeWidgetItem([f"{res}/  (贴图)"])
                res_item.setForeground(0, QtGui.QColor("#e67e22"))
                textures_item.addChild(res_item)
            
            textures_item.setExpanded(True)
        else:
            if not os.path.isdir(folder_path):
                root_item.setExpanded(True)
                return
            
            for filename in os.listdir(folder_path):
                filepath = os.path.join(folder_path, filename)
                if not os.path.isfile(filepath):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                route = get_route_for_ext(ext, self.config)
                if route is None:
                    continue
                if route == 'root':
                    item = QtWidgets.QTreeWidgetItem([filename])
                    item.setForeground(0, QtGui.QColor("#d0d0d0"))
                    root_item.addChild(item)
                else:
                    if route not in folders:
                        folders[route] = []
                    folders[route].append(filename)
            
            for folder, files in sorted(folders.items()):
                folder_item = QtWidgets.QTreeWidgetItem([f"{folder}/"])
                folder_item.setForeground(0, QtGui.QColor("#5294e2"))
                root_item.addChild(folder_item)
                for fname in sorted(files):
                    f_item = QtWidgets.QTreeWidgetItem([fname])
                    f_item.setForeground(0, QtGui.QColor("#d0d0d0"))
                    folder_item.addChild(f_item)
        
        root_item.setExpanded(True)
    
    def _convert(self):
        """执行转换"""
        if getattr(self, '_is_converting', False):
            return

        has_data = bool(self.textures) or bool(self._batch_results)
        if not has_data:
            QtWidgets.QMessageBox.warning(self, "警告", "请先扫描贴图")
            return
        
        if self._export_zasset.isChecked() and not self._output_path.text():
            if self._input_path.text():
                self._output_path.setText(self._input_path.text())
        
        output_folder = self._output_path.text().strip() if self._export_zasset.isChecked() else None
        if self._export_zasset.isChecked() and not output_folder:
            if self._input_path.text():
                output_folder = self._input_path.text().strip()
            else:
                QtWidgets.QMessageBox.warning(self, "警告", "无法确定输出文件夹，请手动指定")
                return

        self._is_converting = True
        self._is_cancelled = False
        self._cancel_btn.setText("中止")
        self._cancel_btn.setEnabled(True)
        self._ok_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        
        if self._batch_results:
            # (subfolder_name, pbr_material_name, tex_info) — 保留 PBR 材质名用于精度查找
            items = list(self._batch_results)
        else:
            items = [(None, material_name, tex) for material_name, tex in self.textures.items()]
        
        self._progress.setVisible(True)
        self._progress.setMaximum(len(items))
        self._progress.setValue(0)
        
        success_count = 0
        fail_count = 0
        errors = []
        
        input_folder = self._input_path.text()
        
        try:
            for i, (subfolder_name, pbr_mat_name, tex_info) in enumerate(items):
                # material_name 用于显示和 .zasset 命名（批量模式用子文件夹名）
                material_name = subfolder_name if subfolder_name else pbr_mat_name
                self._status_label.setText(f"正在处理: {material_name}")
                self._progress.setValue(i + 1)
                if IN_MAYA:
                    try:
                        import maya.cmds as _cmds
                        _cmds.refresh()
                    except Exception:
                        pass
                QtWidgets.QApplication.processEvents()

                if getattr(self, '_is_cancelled', False):
                    break

                try:
                    success = True
                    
                    if IN_MAYA and getattr(getattr(self, '_create_maya_material', None), 'isChecked', lambda: False)():
                        shader, error = create_material(material_name, tex_info, self.config)
                        if not shader:
                            errors.append(f"{material_name}: {error}")
                            success = False
                    
                    if self._export_zasset.isChecked() and output_folder:
                        asset_folder = None
                        folder_name = None
                        if subfolder_name:
                            asset_folder = os.path.join(input_folder, subfolder_name)
                            folder_name = subfolder_name
                        else:
                            # 非批量模式：使用输入文件夹作为资产根目录
                            asset_folder = input_folder
                            folder_name = os.path.basename(input_folder.rstrip('/\\'))
                        extra_files = None
                        # PBR 贴图已由精度扫描处理，不收集 extra_files（避免缩略图等非贴图文件混入）
                        
                        # 获取精度映射 — 用 PBR 材质名
                        res_key = f"{subfolder_name}/{pbr_mat_name}" if subfolder_name else pbr_mat_name
                        res_map = self._resolution_map.get(res_key, {})
                        
                        # 根据打包选项决定是否打包所有精度
                        selected_res = None
                        if res_map.get('resolutions'):
                            if self._pack_all_res.isChecked():
                                selected_res = '_all'
                            else:
                                selected_res = res_map.get('default_res', res_map['resolutions'][0])
                        
                        # 预计算缩略图和元数据搜索（PBR材质名 → 文件夹名 fallback）
                        name_fallbacks = []
                        if pbr_mat_name:
                            name_fallbacks.append(pbr_mat_name)
                        if folder_name and folder_name != pbr_mat_name:
                            name_fallbacks.append(folder_name)
                        if not name_fallbacks:
                            name_fallbacks = ['']
                        pre_thumb = None
                        if asset_folder:
                            pre_thumb = find_existing_thumbnail(asset_folder, name_fallbacks, self.config)
                        pre_meta = None
                        if asset_folder:
                            pre_meta = read_source_metadata(asset_folder, name_fallbacks, self.config)
                        
                        asset_path, error = export_zasset(
                            material_name, tex_info, self.config, output_folder,
                            asset_folder=asset_folder, folder_name=folder_name,
                            extra_files=extra_files,
                            resolution_map=res_map if res_map.get('resolutions') else None,
                            selected_resolution=selected_res,
                            thumb_source=pre_thumb, source_meta=pre_meta
                        )
                        if not asset_path:
                            errors.append(f"{material_name}: {error}")
                            success = False
                    
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    errors.append(f"{material_name}: {e}")
                    fail_count += 1
        finally:
            self._is_converting = False
            self._cancel_btn.setText("取消")
            self._cancel_btn.setEnabled(True)
            self._progress.setVisible(False)
            self._ok_btn.setEnabled(True)
            self._scan_btn.setEnabled(True)

            if getattr(self, '_is_cancelled', False):
                self._status_label.setText(f"已中止: {success_count} 成功, {fail_count} 失败")
                QtWidgets.QMessageBox.information(self, "已中止",
                    f"用户中止转换\n\n成功: {success_count}, 失败: {fail_count}")
            elif errors:
                error_msg = "\n".join(errors[:10])
                if len(errors) > 10:
                    error_msg += f"\n... 还有 {len(errors) - 10} 个错误"
                QtWidgets.QMessageBox.warning(self, "转换完成", 
                    f"成功: {success_count}, 失败: {fail_count}\n\n错误详情:\n{error_msg}")
            else:
                QtWidgets.QMessageBox.information(self, "转换完成", 
                    f"成功转换 {success_count} 个材质")
            
            self._status_label.setText(f"完成: {success_count} 成功, {fail_count} 失败")

            if getattr(self, '_import_to_category', None) and self._import_to_category.isChecked():
                if output_folder:
                    print("[PBR Tool] 开始导入当前分类...")
                    category_path = _get_import_category_path()
                    print(f"[PBR Tool] 目标分类路径: {category_path}")
                    if category_path and os.path.isdir(category_path):
                        imported = _copy_zassets_to_category(output_folder, category_path)
                        if imported:
                            self._status_label.setText(
                                f"完成: {success_count} 成功, {fail_count} 失败 | 已导入 {imported} 个到当前分类")
                            print(f"[PBR Tool] 已导入 {imported} 个zasset到: {category_path}")
                        else:
                            print("[PBR Tool] 未找到zasset文件或导入失败")
                    else:
                        print("[PBR Tool] 当前分类路径无效，跳过导入")
                else:
                    print("[PBR Tool] 未启用zasset导出，跳过导入")

def get_maya_window():
    """获取Maya主窗口作为父窗口"""
    if IN_MAYA:
        try:
            from maya import OpenMayaUI
            import shiboken6
            ptr = OpenMayaUI.MQtUtil.mainWindow()
            if ptr is not None:
                return shiboken6.wrapInstance(int(ptr), QtWidgets.QWidget)
        except:
            try:
                from maya import OpenMayaUI
                import shiboken2
                ptr = OpenMayaUI.MQtUtil.mainWindow()
                if ptr is not None:
                    return shiboken2.wrapInstance(int(ptr), QtWidgets.QWidget)
            except:
                pass
        
        try:
            for obj in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(obj, QtWidgets.QMainWindow) and hasattr(obj, 'windowTitle'):
                    title = obj.windowTitle()
                    if 'Autodesk' in title or ('Maya' in title and '资产' not in title and 'MaterialLibrary' not in title):
                        return obj
        except:
            pass
    return None

def main():
    """主函数"""
    print("[PBR Tool] 启动...")
    
    if QtWidgets is None:
        print("[PBR Tool] 无法加载PySide模块")
        return
    
    print("[PBR Tool] PySide模块加载成功")
    
    app = QtWidgets.QApplication.instance()
    if not app:
        print("[PBR Tool] 创建新的QApplication")
        app = QtWidgets.QApplication(sys.argv)
        need_exec = True
    else:
        print("[PBR Tool] 使用现有的QApplication")
        need_exec = False
    
    parent_window = get_maya_window()
    print(f"[PBR Tool] 父窗口: {parent_window}")
    
    print("[PBR Tool] 创建对话框...")
    dialog = PBRToZAssetDialog(parent=parent_window)
    
    if IN_MAYA and parent_window:
        dialog.setWindowFlags(QtCore.Qt.Window | 
                             QtCore.Qt.WindowTitleHint | 
                             QtCore.Qt.WindowSystemMenuHint | 
                             QtCore.Qt.WindowMinimizeButtonHint | 
                             QtCore.Qt.WindowMaximizeButtonHint | 
                             QtCore.Qt.WindowCloseButtonHint)
        dialog.setParent(parent_window, QtCore.Qt.Window)
    else:
        dialog.setWindowFlags(QtCore.Qt.Window | 
                              QtCore.Qt.WindowTitleHint | 
                              QtCore.Qt.WindowSystemMenuHint | 
                              QtCore.Qt.WindowMinimizeButtonHint | 
                              QtCore.Qt.WindowMaximizeButtonHint | 
                              QtCore.Qt.WindowCloseButtonHint)
    
    print("[PBR Tool] 显示对话框...")
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    print("[PBR Tool] 对话框已显示")
    
    if need_exec:
        print("[PBR Tool] 进入事件循环...")
        app.exec()

if __name__ == '__main__':
    main()
