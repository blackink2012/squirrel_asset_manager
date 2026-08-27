# -*- coding: utf-8 -*-
"""贴图收集工具模块 — 全属性递归扫描 + 多渲染器支持 + 帧序列解析。

核心函数:
    collect_all_texture_files(root_nodes) → {绝对路径集合}
    
    不依赖 listHistory，而是递归遍历 shading 网络中每个节点的
    属性连接，匹配所有可能的贴图节点类型（file / aiImage / 
    RedshiftNormalMap / RedshiftBumpMap / RedshiftSprite 等）。

    同时支持:
    - 灯光贴图（Dome Light HDR / IES）
    - 帧序列 / UDIM 贴图模式
    - 多路径 displacement / bump 贴图收集
"""

import os
import re
import shutil
from typing import Dict, List, Optional, Set, Tuple

# ── Maya 环境检测 ──────────────────────────────────────────────
_IN_MAYA: bool = False
try:
    import maya.cmds as cmds  # noqa: F401
    _IN_MAYA = True
except ImportError:
    pass

# ── 常量 ──────────────────────────────────────────────────────
SKIP_ATTRS: Set[str] = {
    'message', 'caching', 'isHistoricallyInteresting',
    'nodeState', 'binaryPluginAttribute', 'separator',
}
COMPOUND_CHILD_ENDS: Tuple[str, ...] = ('R', 'G', 'B', 'A', 'X', 'Y', 'Z')

# 贴图文件扩展名白名单（避免误收集 .ass 等代理文件）
TEXTURE_EXTENSIONS: Set[str] = {
    '.exr', '.hdr', '.png', '.jpg', '.jpeg', '.tif', '.tiff',
    '.tga', '.tx', '.dds', '.bmp', '.psd', '.pic', '.rat',
}

# 非贴图扩展名黑名单（显式排除）
NON_TEXTURE_EXTENSIONS: Set[str] = {
    '.ass', '.vrscene', '.rs', '.abc', '.fbx', '.obj',
    '.ma', '.mb', '.usd', '.usda', '.usdc', '.zasset',
}

_DEBUG = False


def set_debug(enabled: bool = True):
    """启用/禁用调试日志。"""
    global _DEBUG
    _DEBUG = enabled


def _dbg(msg: str):
    if _DEBUG:
        print(f"[TextureUtils] {msg}")


# ═══════════════════════════════════════════════════════════════
# 节点类型注册表
# ═══════════════════════════════════════════════════════════════

# 格式: node_type → (要查询的属性名列表, 是否为帧序列属性, 属性是直接值还是连接)
_TEXTURE_NODE_REGISTRY: Dict[str, Tuple[List[str], Optional[str], bool]] = {
    # ── 通用 ──
    'file':              (['fileTextureName'],                     'fileTextureName',  True),
    'place2dTexture':    (['fileTextureName'],                     'fileTextureName',  True),
    # ── Arnold ──
    'aiImage':           (['filename'],                            'filename',         True),
    'aiStandIn':         (['dso'],                                 'dso',              True),
    'aiSkyDomeLight':    (['ai_filename'],                         None,               True),
    'aiSkyDomeLightShape': (['ai_filename'],                       None,               True),
    'aiVolume':          (['filename', 'fileName', 'filePath'],    'filename',         True),
    # ── Redshift ──
    'RedshiftNormalMap':  (['tex0', 'tex1'],                       'tex0',             True),
    'RedshiftBumpMap':    (['tex0', 'tex1', 'tex2'],               'tex0',             True),
    'RedshiftBumpBlender': (['tex0', 'tex1', 'tex2'],              'tex0',             True),
    'RedshiftSprite':     (['tex0'],                                'tex0',             True),
    'RedshiftDomeLight':  (['tex0'],                                'tex0',             True),
    'RedshiftEnvironment': (['tex0'],                               'tex0',             True),
    'RedshiftVolumeShape': (['fn'],                                 'fn',               True),
    'RedshiftProxyMesh':  (['fn', 'exoFile', 'rsProxyFile', 'proxyFile'], 'fn',         True),
    # ── V-Ray ──
    'VRayLightDomeShape': (['domeTex'],                            None,               False),  # domeTex 是连接
    'VRayMtl':            (['texmap'],                              None,               False),
    'VRayVolumeGrid':     (['ipth', 'ipthr', 'f', 'fn', 'filename', 'filePath'], 'ipth', True),
    # ── 灯光通用（IES 等属性值） ──
    # 由 _scan_light_shape_attrs 单独处理
}

# 材质到 shadingEngine 的出口属性名列表
_MATERIAL_OUTPUT_ATTRS: List[str] = [
    'outColor', 'out', 'outValue', 'outAlpha',
    'surfaceShader', 'volumeShader', 'displacementShader',
]

# displacement 属性名（跨渲染器）
_DISPLACEMENT_ATTRS: List[str] = [
    'displacementShader',
    'displacement',
    'rsDisplacement',
    'rsDisplacementShader',
]

# bump / normal 属性名（跨渲染器）
_BUMP_ATTRS: List[str] = [
    'normalCamera',
    'bumpMap',
    'normal',
    'rsBumpMap',
    'rsNormalMap',
    'bump',
]

# 灯光 shape 自带的文件属性名（属性值，非连接）
_LIGHT_FILE_ATTR_PATTERNS: Tuple[str, ...] = (
    'filename', 'iesFile', 'profile',
    'ai_filename', 'aiFilename',
)


# ═══════════════════════════════════════════════════════════════
# 帧序列 / UDIM 解析
# ═══════════════════════════════════════════════════════════════

# 目录级缓存：避免逐文件 glob.glob 在 NAS/网络存储上的性能问题
# 同目录下多个贴图共享一次 os.listdir() 结果
_dir_listing_cache: Dict[str, List[str]] = {}


def _cached_dir_list(dirpath: str) -> List[str]:
    """获取目录文件列表（带缓存），避免重复 os.listdir / glob。"""
    key = os.path.normpath(dirpath).lower()
    if key in _dir_listing_cache:
        return _dir_listing_cache[key]
    try:
        files = os.listdir(dirpath)
    except Exception:
        files = []
    _dir_listing_cache[key] = files
    return files


def _cached_glob(pattern: str) -> List[str]:
    """glob.glob 替代，使用缓存的目录列表在内存中匹配。"""
    import fnmatch
    dirname = os.path.dirname(pattern)
    if not dirname:
        dirname = '.'
    file_pattern = os.path.basename(pattern)
    all_files = _cached_dir_list(dirname)
    matches = sorted(f for f in all_files if fnmatch.fnmatch(f, file_pattern))
    return [os.path.join(dirname, f) for f in matches]

def _resolve_texture_frame_pattern(path: str) -> List[str]:
    """解析帧序列 / UDIM 模式为实际文件列表。

    支持格式:
        - name.####.exr    → 展开为所有匹配帧
        - name.%04d.exr    → 同上
        - name.<UDIM>.exr  → 展开为所有 UDIM tile
        - name.1001.exr    → 检测是否为序列首帧，若是则展开全部帧
        - name.exr         → 单帧（直接返回）

    Returns:
        [文件绝对路径列表]，不带帧模式的空路径返回空列表
    """
    if not path:
        return []

    normalized = os.path.normpath(path)

    # ── 帧序列模式 (#### / %04d / %4d) ──
    for token in ('####', '%04d', '%4d'):
        if token in path:
            pattern = path.replace(token, '*')
            matches = sorted(_cached_glob(pattern))
            if matches:
                _dbg(f"帧序列展开 [{token}]: {path} → {len(matches)} 帧")
                return matches

    # ── UDIM 模式 (<UDIM>) ──
    if '<UDIM>' in path or '<udim>' in path:
        udim_pattern = path.replace('<UDIM>', '*').replace('<udim>', '*')
        matches = sorted(_cached_glob(udim_pattern))
        if matches:
            _dbg(f"UDIM 展开: {path} → {len(matches)} tile")
            return matches

    # ── 父目录存在时的 # 通配展开 ──
    parent = os.path.dirname(path)
    if os.path.isdir(parent) and '#' in path:
        wild = re.sub(r'#+', '*', os.path.basename(path))
        matches = sorted(_cached_glob(os.path.join(parent, wild)))
        if matches:
            _dbg(f"井号展开: {path} → {len(matches)} 文件")
            return matches

    # ── 单帧检测 + 序列展开 ──
    # 关键：即使 os.path.isfile 返回 True（如 texture.1001.exr），
    # 也要检测是否为帧序列的第一帧。Maya 中序列贴图的 fileTextureName
    # 通常存储首帧路径（如 texture.1001.exr），useFrameExtension=True。
    if os.path.isfile(normalized):
        expanded = _try_expand_numeric_sequence(normalized)
        if expanded:
            _dbg(f"序列展开: {os.path.basename(normalized)} → {len(expanded)} 帧")
            return expanded
        return [normalized]

    # 无法解析：返回空列表
    _dbg(f"无法解析帧模式: {path}")
    return []


def _try_expand_numeric_sequence(filepath: str) -> List[str]:
    """检测文件是否为帧序列的一部分，若是则展开全部帧。

    例如: texture.1001.exr → 扫描同目录下所有 texture.*.exr，
    如果存在 texture.1002.exr 等，返回全部帧路径。

    Args:
        filepath: 单个文件的绝对路径（如 texture.1001.exr）

    Returns:
        所有帧的路径列表（含自身），若不是序列则返回空列表
    """
    dirname = os.path.dirname(filepath)
    basename = os.path.basename(filepath)
    
    # 匹配文件名中的数字序列：name.1001.ext 或 name_1001.ext 等
    # 模式: 前缀 + 连续数字 + 后缀（扩展名）
    m = re.search(r'^(.*?)(\d+)(\.[^.]+)$', basename)
    if not m:
        return []
    
    prefix = m.group(1)   # "texture."
    digits = m.group(2)   # "1001"
    ext = m.group(3)      # ".exr"
    padding = len(digits)
    
    # 扫描同目录下匹配模式的所有文件
    pattern = os.path.join(dirname, f"{prefix}*{ext}")
    candidates = sorted(_cached_glob(pattern))
    
    if len(candidates) <= 1:
        # 只有自身一个文件，不是序列
        return []
    
    # 验证：提取所有候选文件中的数字并检查连续性
    frame_files = []
    for f in candidates:
        m2 = re.search(r'^(.*?)(\d+)(\.[^.]+)$', os.path.basename(f))
        if m2 and m2.group(3).lower() == ext.lower() and m2.group(1) == prefix:
            # 数字长度与原始匹配（相同 padding）
            if len(m2.group(2)) == padding:
                frame_files.append(os.path.normpath(f))
    
    if len(frame_files) <= 1:
        return []
    
    return sorted(frame_files)


# ═══════════════════════════════════════════════════════════════
# 文件路径提取
# ═══════════════════════════════════════════════════════════════

def _is_texture_extension(path: str) -> bool:
    """判断文件扩展名是否为贴图类型。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in NON_TEXTURE_EXTENSIONS:
        return False
    if ext in TEXTURE_EXTENSIONS:
        return True
    # 未知扩展名：允许（某些渲染器使用自定义格式）
    return True


def _extract_file_path(node: str, ntype: str) -> Optional[str]:
    """从各类节点中提取文件路径。

    支持:
        file, aiImage, Redshift 系列, V-Ray 系列,
        Arnold 灯光, IES 节点, 以及注册表中的所有类型。

    Returns:
        文件路径字符串，未找到返回 None
    """
    # 1. 检查注册表
    if ntype in _TEXTURE_NODE_REGISTRY:
        attr_names, _, _ = _TEXTURE_NODE_REGISTRY[ntype]
        for attr in attr_names:
            try:
                full = f"{node}.{attr}"
                if not cmds.objExists(full):
                    continue
                val = cmds.getAttr(full)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()
            except Exception:
                # 某些属性可能不是字符串（如连接），跳过
                continue

    # 2. 通用 file 节点回退
    if ntype == 'file':
        try:
            return cmds.getAttr(f"{node}.fileTextureName") or None
        except Exception:
            pass

    # 3. IES / 光域网节点
    if 'IES' in ntype.upper() or 'PHOTOMETRIC' in ntype.upper():
        for ies_attr in ('iesFile', 'profile', 'aiFilename', 'filename'):
            try:
                path = cmds.getAttr(f"{node}.{ies_attr}")
                if path and isinstance(path, str):
                    return path
            except Exception:
                pass

    return None


def _scan_light_shape_file_attrs(shape_node: str) -> List[str]:
    """扫描灯光 shape 自身属性中的文件路径（非连接方式存储）。

    用于收集 IES 光域网、Arnold ai_filename 等直接写入属性值的贴图。
    """
    paths: List[str] = []
    if not _IN_MAYA:
        return paths
    try:
        for attr in cmds.listAttr(shape_node) or []:
            if not any(attr.endswith(p) for p in _LIGHT_FILE_ATTR_PATTERNS):
                continue
            try:
                val = cmds.getAttr(f"{shape_node}.{attr}")
                if isinstance(val, str) and val.strip():
                    paths.append(val.strip())
            except Exception:
                pass
    except Exception:
        pass
    return paths


# ═══════════════════════════════════════════════════════════════
# 核心：全属性递归扫描
# ═══════════════════════════════════════════════════════════════

_MAX_RECURSE_DEPTH = 200


def _summarize_visited_types(visited: Set[str]) -> str:
    """汇总已遍历节点的类型分布（调试用）。"""
    from collections import Counter
    type_counts = Counter()
    for node in visited:
        try:
            if cmds.objExists(node):
                type_counts[cmds.nodeType(node)] += 1
        except Exception:
            pass
    items = [f"{t}x{c}" for t, c in type_counts.most_common(8)]
    return ", ".join(items) if items else "(empty)"


def _get_all_connectable_attrs(node: str) -> List[str]:
    """获取节点上所有可能有连接的属性列表（去重复合子属性）。

    注意：
    - 不使用 writable=True：已连接的属性被 Maya 视为不可写入，会被排除
    - 不使用 scalar=True：复合属性（如 baseColor）是 compound 类型，会被排除
    改用无过滤的 listAttr 获取全部属性，再手动过滤。
    """
    all_attrs: List[str] = []
    # 标准属性（含 compound、含非 writable 属性）
    try:
        all_attrs.extend(cmds.listAttr(node) or [])
    except Exception:
        pass
    # 隐藏属性
    try:
        all_attrs.extend(cmds.listAttr(node, hidden=True) or [])
    except Exception:
        pass
    # 用户自定义属性
    try:
        all_attrs.extend(cmds.listAttr(node, userDefined=True) or [])
    except Exception:
        pass

    # 去重并过滤
    seen: Set[str] = set()
    filtered: List[str] = []
    for attr in sorted(set(all_attrs)):
        # 跳过系统属性
        if attr in SKIP_ATTRS:
            continue
        # 跳过已知的内部属性
        if attr.startswith('__') or attr.startswith('_'):
            if attr not in ('_nodeState',):
                continue
        # 跳过输出属性
        if attr.startswith('out') or attr.startswith('output'):
            continue
        # 跳过数组/索引属性
        if '[' in attr or '.' in attr:
            continue
        # 跳过复合属性的子属性（R/G/B/X/Y/Z 结尾）
        if len(attr) >= 2 and attr[-1] in COMPOUND_CHILD_ENDS:
            parent_attr = attr[:-1]
            if parent_attr not in SKIP_ATTRS:
                seen.add(parent_attr)
            continue
        # 跳过子属性 A(Alpha) — 如果是单独的 Alpha 通道子属性
        if len(attr) >= 2 and attr[-1] == 'A':
            parent_attr = attr[:-1]
            if parent_attr not in SKIP_ATTRS:
                seen.add(parent_attr)
            continue
        if attr not in seen:
            seen.add(attr)
            filtered.append(attr)

    return filtered


def collect_all_texture_files(
    root_nodes,
    collect_lights: bool = True,
) -> Set[str]:
    """全属性递归扫描收集所有贴图文件路径。

    从 root_nodes 出发，递归遍历每个节点的上游属性连接。
    对每个节点检查是否为贴图节点类型，提取并展开所有文件路径。

    Args:
        root_nodes: 起始节点或节点列表（材质/灯光/物体均可）
        collect_lights: 是否也收集灯光 shape 属性中的文件路径

    Returns:
        {绝对路径集合}，帧序列已展开为多帧
    """
    if not _IN_MAYA:
        return set()

    if isinstance(root_nodes, str):
        root_nodes = [root_nodes]
    if not root_nodes:
        return set()

    visited: Set[str] = set()
    texture_paths: Set[str] = set()
    _collected_from: List[str] = []  # 调试用
    _failed_checks: List[str] = []   # 调试用：检查过但无连接的属性

    def _traverse(node: str, depth: int = 0):
        if depth > _MAX_RECURSE_DEPTH:
            _dbg(f"  达到最大递归深度 {_MAX_RECURSE_DEPTH}，停止")
            return
        if node in visited:
            return
        if not cmds.objExists(node):
            _dbg(f"  节点不存在: {node}")
            return
        visited.add(node)

        try:
            ntype = cmds.nodeType(node)
        except Exception:
            return

        if _DEBUG:
            indent = "  " * min(depth, 6)
            _dbg(f"{indent}[depth={depth}] 进入: {ntype}({node})")

        # ── 1. 提取当前节点的文件路径 ──
        path = _extract_file_path(node, ntype)
        if path:
            resolved = _resolve_texture_frame_pattern(path)
            for p in resolved:
                if _is_texture_extension(p):
                    if p not in texture_paths:
                        texture_paths.add(p)
                        _collected_from.append(
                            f"{os.path.basename(p)} ← {ntype}({node})"
                        )

        # ── 2. 灯光 shape 属性值中的文件路径 ──
        if collect_lights and ntype not in ('file', 'place2dTexture'):
            light_paths = _scan_light_shape_file_attrs(node)
            for lp in light_paths:
                resolved = _resolve_texture_frame_pattern(lp)
                for p in resolved:
                    if _is_texture_extension(p) and p not in texture_paths:
                        texture_paths.add(p)
                        _collected_from.append(
                            f"{os.path.basename(p)} ← {ntype}({node}) [属性值]"
                        )

        # ── 3. 一次性获取所有上游连接，递归上行 ──
        # 原来对每个属性逐个调用 listConnections（每个节点 100~400 次 Maya API 调用）
        # 现在仅 1 次 listConnections(node, source=True) 获取全部上游源节点
        try:
            all_sources = cmds.listConnections(
                node, source=True, destination=False
            )
            if all_sources:
                # 去重：同一个源节点可能连到多个属性
                seen_sources: Set[str] = set()
                for src in all_sources:
                    if src not in seen_sources:
                        seen_sources.add(src)
                        _traverse(src, depth + 1)
        except Exception:
            pass

        # ── 3b. 材质 → 下游 shadingEngine → displacementShader（置换贴图）──
        # 置换链在材质的下游：material → shadingEngine.displacementShader →
        # displacement 节点 → file。纯上游遍历到不了 shadingEngine，
        # 需从材质节点显式向下游查找（对照第 4 步的 shadingEngine 分支）。
        try:
            ses = cmds.listConnections(
                node, source=False, destination=True, type='shadingEngine'
            ) or []
            for se_node in ses:
                if se_node in visited:
                    continue
                disp_conns = cmds.listConnections(
                    f"{se_node}.displacementShader",
                    source=True, destination=False,
                ) or []
                for disp_node in disp_conns:
                    _traverse(disp_node, depth + 1)
        except Exception:
            pass

        # ── 4. 特殊：shadingEngine → displacementShader ──
        if ntype == 'shadingEngine':
            try:
                disp_conns = cmds.listConnections(
                    f"{node}.displacementShader",
                    source=True, destination=False,
                )
                if disp_conns:
                    for disp in disp_conns:
                        _traverse(disp, depth + 1)
            except Exception:
                pass

    # ── 遍历所有根节点 ──
    for root in root_nodes:
        if _DEBUG:
            _dbg(f"--- 从根节点开始扫描: {root} ---")
        _traverse(root)

    if _DEBUG:
        _dbg(f"扫描完成: 遍历了 {len(visited)} 个节点，收集到 {len(texture_paths)} 个贴图文件")
        if not texture_paths and visited:
            _dbg(f"警告: 遍历了 {len(visited)} 个节点但未找到贴图！")
            _dbg(f"  已遍历的节点类型: {_summarize_visited_types(visited)}")
        for entry in _collected_from:
            _dbg(f"  ✓ {entry}")

    return texture_paths


# ═══════════════════════════════════════════════════════════════
# 场景级收集（材质 + 灯光 + 物体递归）
# ═══════════════════════════════════════════════════════════════

def collect_textures_from_materials(material_nodes: List[str]) -> Set[str]:
    """从材质节点列表收集所有上游贴图。

    对每个材质节点递归扫描其 shading 网络。

    Args:
        material_nodes: Maya 材质节点名列表

    Returns:
        {绝对路径集合}
    """
    if not _IN_MAYA:
        return set()

    all_paths: Set[str] = set()
    for mat in material_nodes:
        if not cmds.objExists(mat):
            _dbg(f"跳过不存在的材质: {mat}")
            continue
        paths = collect_all_texture_files(mat, collect_lights=False)
        all_paths.update(paths)

    return all_paths


def collect_textures_from_objects(
    objects: List[str],
    extra_materials: Optional[List[str]] = None,
) -> Set[str]:
    """从物体列表收集所有关联贴图。

    1. 从物体的 shadingEngine 收集材质
    2. 递归扫描所有材质的 shading 网络
    3. 额外收集灯光贴图（Dome Light HDR / IES 等）

    Args:
        objects: Maya 物体节点名列表
        extra_materials: 额外要包含的材质节点

    Returns:
        {绝对路径集合}
    """
    if not _IN_MAYA:
        return set()

    all_paths: Set[str] = set()
    all_materials: Set[str] = set()
    all_lights: Set[str] = set()

    for obj in objects:
        if not cmds.objExists(obj):
            continue

        # 收集所有 shape 节点（batch：一次 cmds.ls 替代逐后代 listRelatives(shapes)）
        descendants = cmds.listRelatives(
            obj, allDescendents=True, fullPath=True
        ) or []
        # batch filter: cmds.ls 可以一次调用来过滤 shape 节点
        shapes = cmds.ls(descendants, shapes=True, long=True) or []
        obj_shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
        for s in obj_shapes:
            if s not in shapes:
                shapes.append(s)

        for shape in shapes:
            if not cmds.objExists(shape):
                continue
            ntype = cmds.nodeType(shape)
            # 判断是否为灯光
            try:
                is_light = cmds.getClassification(ntype, satisfies="light")
            except Exception:
                is_light = False

            if is_light:
                all_lights.add(shape)
                # 灯光自身也要递归扫描贴图
                light_tex = collect_all_texture_files(shape, collect_lights=True)
                all_paths.update(light_tex)
                continue

            # 从 shadingEngine 收集材质
            try:
                ses = cmds.listConnections(shape, type='shadingEngine') or []
                for se in ses:
                    if se == 'initialShadingGroup':
                        continue
                    mat_list = cmds.listConnections(
                        f"{se}.surfaceShader"
                    ) or []
                    # 也收集 volumeShader / displacementShader
                    for port in ('volumeShader', 'displacementShader'):
                        try:
                            extras = cmds.listConnections(
                                f"{se}.{port}"
                            ) or []
                            mat_list.extend(extras)
                        except Exception:
                            pass
                    all_materials.update(mat_list)
            except Exception:
                continue

    # 添加额外材质
    if extra_materials:
        for m in extra_materials:
            if m and cmds.objExists(m):
                all_materials.add(m)

    # 扫描所有材质
    if _DEBUG:
        _dbg(
            f"从 {len(objects)} 个物体发现: "
            f"{len(all_materials)} 个材质, {len(all_lights)} 个灯光"
        )
        if all_materials:
            _dbg(f"  材质: {sorted(all_materials)[:20]}")
        if all_lights:
            _dbg(f"  灯光: {sorted(all_lights)[:20]}")

    for mat in sorted(all_materials):
        if not cmds.objExists(mat):
            continue
        paths = collect_all_texture_files(mat, collect_lights=False)
        all_paths.update(paths)

    if _DEBUG:
        _dbg(
            f"从 {len(objects)} 个物体收集: "
            f"{len(all_materials)} 个材质, {len(all_lights)} 个灯光, "
            f"{len(all_paths)} 个贴图文件"
        )

    return all_paths


# ═══════════════════════════════════════════════════════════════
# 贴图复制工具
# ═══════════════════════════════════════════════════════════════

def copy_textures_to_dir(
    texture_paths: Set[str],
    dest_dir: str,
    subfolder: str = "",
) -> Dict[str, str]:
    """将贴图文件复制到目标目录。

    Args:
        texture_paths: 源文件绝对路径集合
        dest_dir: 目标根目录（会在其下创建 textures/ 子目录）
        subfolder: 可选子文件夹（如材质名）

    Returns:
        {原始绝对路径: 目标相对路径} 路径映射
    """
    path_map: Dict[str, str] = {}
    if not texture_paths:
        return path_map

    tex_dir = os.path.join(dest_dir, "textures")
    if subfolder:
        tex_dir = os.path.join(tex_dir, subfolder)
    os.makedirs(tex_dir, exist_ok=True)

    for src in sorted(texture_paths):
        norm_src = src.replace("\\", "/")
        basename = os.path.basename(src)
        dst = os.path.join(tex_dir, basename)

        # 重名处理
        dst_base, dst_ext = os.path.splitext(basename)
        counter = 1
        while (
            os.path.exists(dst)
            and os.path.normcase(os.path.abspath(src)).lower()
            != os.path.normcase(os.path.abspath(dst)).lower()
        ):
            dst = os.path.join(tex_dir, f"{dst_base}_{counter:03d}{dst_ext}")
            counter += 1

        try:
            # 只在源和目标不同时复制（时间戳优化）
            if not os.path.isfile(dst) or os.path.getmtime(src) != os.path.getmtime(dst):
                shutil.copy2(src, dst)
                _dbg(f"复制: {basename}")
            rel = os.path.relpath(dst, os.path.dirname(dest_dir)).replace("\\", "/")
            if subfolder:
                rel = f"textures/{subfolder}/{os.path.basename(dst)}"
            else:
                rel = f"textures/{os.path.basename(dst)}"
            path_map[norm_src] = rel
        except Exception as e:
            print(f"[TextureUtils] 复制失败 [{src}]: {e}")

    return path_map
