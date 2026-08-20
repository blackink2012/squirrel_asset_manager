#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Maya .ma / .zmetal 节点编辑器 (PySide6)
- 无限画布 + 动态网格
- 从左向右层级布局
- 节点上显示属性名（输入/输出端口标签）
- 中键拖拽平移画布
同时支持：普通 Python 运行 / mayapy.exe 运行 / Maya 内运行
"""

import re
import sys
import os
import math
from collections import deque

# 帮助文档按界面语言选择（zh/en）
_help_path = lambda p: p
try:
    from .i18n import help_path as _hpath
    _help_path = _hpath
except ImportError:
    try:
        from squirrel_asset_manager.utils.i18n import help_path as _hpath
        _help_path = _hpath
    except ImportError:
        pass

# ============================================================
# Maya 环境检测（先检测，稍后初始化
# ============================================================
MAYA_MODE = False  # 是否在 mayapy / Maya 中运行
_editor_window = None  # 保持窗口引用防止 Maya 内垃圾回收

# 只检测是否能导入 Maya 模块，但**不立即初始化
try:
    import maya.cmds as _maya_cmds_module_test
    _maya_api_available = True
except ImportError:
    _maya_api_available = False

# PySide 导入必须在 maya.standalone.initialize() 之前或之后都可以
# 关键是：QApplication 必须在 maya.standalone.initialize() 之前创建
def get_qt_modules():
    """获取 Qt 绑定（PySide6 优先，失败自动降级 PySide2）

    - Maya 2025+ 自带 PySide6
    - Maya 2022~2024 自带 PySide2
    """
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        pass
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    except ImportError:
        pass
    raise ImportError("需要 PySide6 或 PySide2")


QtWidgets, QtCore, QtGui = get_qt_modules()

QApplication = QtWidgets.QApplication
QMainWindow = QtWidgets.QMainWindow
QGraphicsView = QtWidgets.QGraphicsView
QGraphicsScene = QtWidgets.QGraphicsScene
QGraphicsRectItem = QtWidgets.QGraphicsRectItem
QGraphicsTextItem = QtWidgets.QGraphicsTextItem
QGraphicsPathItem = QtWidgets.QGraphicsPathItem
QGraphicsEllipseItem = QtWidgets.QGraphicsEllipseItem
QFileDialog = QtWidgets.QFileDialog
QMessageBox = QtWidgets.QMessageBox
QStatusBar = QtWidgets.QStatusBar
QToolBar = QtWidgets.QToolBar
QPushButton = QtWidgets.QPushButton
QGraphicsItem = QtWidgets.QGraphicsItem
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QWidget = QtWidgets.QWidget
QFrame = QtWidgets.QFrame
QLabel = QtWidgets.QLabel
Qt = QtCore.Qt
QPointF = QtCore.QPointF
QRectF = QtCore.QRectF
QLineF = QtCore.QLineF
Signal = QtCore.Signal
QObject = QtCore.QObject
QPen = QtGui.QPen
QBrush = QtGui.QBrush
QColor = QtGui.QColor
QPainterPath = QtGui.QPainterPath
QFont = QtGui.QFont
QPainter = QtGui.QPainter
QLinearGradient = QtGui.QLinearGradient
QWheelEvent = QtGui.QWheelEvent
QMouseEvent = QtGui.QMouseEvent
QPolygonF = QtGui.QPolygonF
QTransform = QtGui.QTransform
QKeyEvent = QtGui.QKeyEvent
QFontMetrics = QtGui.QFontMetrics


# ---------- .zmetal 解析器 ----------
def parse_zmetal_file(filepath):
    """
    解析 .zmetal JSON 文件，提取节点和带属性名的连接。
    返回: (nodes_dict, edges_with_attrs, node_input_attrs, node_output_attrs)
        nodes: {node_name: node_type}
        edges: [(src_node, src_attr, dst_node, dst_attr)]
        node_input_attrs: {node_name: set(attribute_names)}
        node_output_attrs: {node_name: set(attribute_names)}
    """
    import json

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    nodes = {}
    edges = []
    node_input_attrs = {}
    node_output_attrs = {}

    nodes_data = data.get('nodes', {})

    for node_name, node_info in nodes_data.items():
        node_type = node_info.get('node_type', 'unknown')
        nodes[node_name] = node_type

        attrs = node_info.get('attrs', {})
        for attr_name, attr_info in attrs.items():
            if attr_info.get('type') == 'connection':
                src_node = attr_info.get('source_node', '')
                src_attr = attr_info.get('source_attr', '')
                if src_node and src_attr:
                    edges.append((src_node, src_attr, node_name, attr_name))
                    node_input_attrs.setdefault(node_name, set()).add(attr_name)
                    node_output_attrs.setdefault(src_node, set()).add(src_attr)

    return nodes, edges, node_input_attrs, node_output_attrs


def write_zmetal_file(filepath, nodes, edges, source_filepath=None):
    """
    将当前节点图写入 .zmetal JSON 文件。

    如果提供了 source_filepath，以原始文件为基底，只更新 nodes 中存在的节点
    （删除在原始文件中但不在当前 nodes 中的节点，添加新节点，更新连接）。
    否则从头创建新的 zmetal 文件。

    nodes: {node_name: node_type} —— 当前 UI 中的节点
    edges: [(src_node, src_attr, dst_node, dst_attr)] —— 当前 UI 中的连接
    source_filepath: 原始文件路径
    """
    import json
    import datetime

    if source_filepath and os.path.isfile(source_filepath):
        try:
            with open(source_filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    # 更新基础元数据
    if 'version' not in data:
        data['version'] = '2.0'
    if 'software' not in data:
        data['software'] = 'Maya'
    if 'export_date' not in data:
        data['export_date'] = datetime.date.today().isoformat()

    # 构建新的 nodes 字典
    current_node_names = set(nodes.keys())
    old_nodes = data.get('nodes', {})

    new_nodes = {}
    for node_name, node_type in nodes.items():
        if node_name in old_nodes:
            # 保留原有属性数据
            old_node = old_nodes[node_name]
            new_node = {
                'node_type': node_type,
                'attrs': old_node.get('attrs', {})
            }
        else:
            # 新节点，创建空的 attrs
            new_node = {
                'node_type': node_type,
                'attrs': {}
            }
        new_nodes[node_name] = new_node

    # 重建连接：更新所有节点的 attrs
    # 先清除所有旧连接
    for node_name in new_nodes:
        attrs = new_nodes[node_name].get('attrs', {})
        keys_to_remove = []
        for attr_name, attr_info in attrs.items():
            if isinstance(attr_info, dict) and attr_info.get('type') == 'connection':
                keys_to_remove.append(attr_name)
        for key in keys_to_remove:
            del attrs[key]

    # 添加当前连接
    for src_node, src_attr, dst_node, dst_attr in edges:
        if dst_node in new_nodes:
            attrs = new_nodes[dst_node].setdefault('attrs', {})
            attrs[dst_attr] = {
                'type': 'connection',
                'source_node': src_node,
                'source_attr': src_attr
            }

    data['nodes'] = new_nodes

    # 更新 materials 和 root_materials 列表
    # 保留旧的 materials，移除已删除的，添加新的
    old_materials = data.get('materials', [])
    old_mat_names = {m['name'] for m in old_materials if isinstance(m, dict)}
    new_mat_names = set(nodes.keys())

    materials = [m for m in old_materials if isinstance(m, dict) and m.get('name') in new_mat_names]
    for name in new_mat_names - old_mat_names:
        materials.append({
            'name': name,
            'node_type': nodes[name],
            'category': '',
            'tags': []
        })
    data['materials'] = materials

    # 更新 root_materials（所有节点作为根材质候选）
    if materials:
        data['root_materials'] = [m['name'] for m in materials]
    else:
        data['root_materials'] = list(nodes.keys())

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    return True


# ---------- 增强的 .ma 解析器（提取属性名）----------
def parse_ma_file(filepath):
    """
    解析 .ma 文件，提取节点和带属性名的连接。
    返回: (nodes_dict, edges_with_attrs, node_input_attrs, node_output_attrs)
        nodes: {node_name: node_type}
        edges: [(src_node, src_attr, dst_node, dst_attr)]
        node_input_attrs: {node_name: set(attribute_names)}
        node_output_attrs: {node_name: set(attribute_names)}
    """
    nodes = {}
    edges = []
    node_input_attrs = {}
    node_output_attrs = {}

    create_node_re = re.compile(
        r'createNode\s+(\w+)\s+.*?-n\s+["\']?([^"\'\s;]+)["\']?'
    )
    connect_attr_re = re.compile(
        r'connectAttr\s+(?:-?\w+\s+)*["\']?([^"\'\s;]+)["\']?\s+["\']?([^"\'\s;]+)["\']?'
    )

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('//'):
                continue
            try:
                if line.startswith('createNode'):
                    m = create_node_re.search(line)
                    if m:
                        node_type = m.group(1)
                        node_name = m.group(2).strip('"\'')
                        nodes[node_name] = node_type
                    continue
                if 'connectAttr' in line:
                    m = connect_attr_re.search(line)
                    if m:
                        src_full = m.group(1)
                        dst_full = m.group(2)
                        if '.' not in src_full or '.' not in dst_full:
                            continue
                        src_node, src_attr = src_full.split('.', 1)
                        dst_node, dst_attr = dst_full.split('.', 1)
                        src_node = src_node.split('|')[-1]
                        dst_node = dst_node.split('|')[-1]
                        if src_node != dst_node:
                            edges.append((src_node, src_attr, dst_node, dst_attr))
                            node_input_attrs.setdefault(dst_node, set()).add(dst_attr)
                            node_output_attrs.setdefault(src_node, set()).add(src_attr)
                    continue
            except Exception:
                continue
    return nodes, edges, node_input_attrs, node_output_attrs


def write_ma_file(filepath, nodes, edges, source_filepath=None, original_node_names=None):
    """
    将当前节点图写入 .ma 文件。

    **核心策略：始终以原始文件为基底，只删除用户移除的节点，保留所有其他数据。**

    - 如果提供了 source_filepath：打开原始文件 → 只删除在 original_node_names 中
      但不在当前 nodes 中的节点 → 写入新文件
    - 如果没有 source_filepath：从头创建（仅保留节点和连接）

    Maya API 模式优先于文本模式。

    nodes: {node_name: node_type} —— 当前 UI 中的节点
    edges: [(src_node, src_attr, dst_node, dst_attr)] —— 当前 UI 中的连接
    source_filepath: 原始文件路径，用于「修改式保存」
    original_node_names: set(str) —— 原文件解析出的节点名集合（用于判断哪些节点是用户删除的）
    """
    # 优先使用 Maya API 写入
    if is_maya_available():
        return _maya_api_write(filepath, nodes, edges, source_filepath, original_node_names)

    # === 文本模式：修改并保存（保留原始几何体数据）===
    if source_filepath and os.path.isfile(source_filepath):
        try:
            with open(source_filepath, 'r', encoding='utf-8', errors='ignore') as f:
                original_lines = f.readlines()

            current_node_names = set(nodes.keys())

            # 关键修复：只删除「原文件中被解析出的节点」中不存在于当前 UI 的那些
            # 而不是删除所有不在当前 UI 中的节点（那样会删除几何体等）
            if original_node_names is not None:
                nodes_to_delete = set(original_node_names) - current_node_names
            else:
                # 没有 original_node_names 信息时，保守处理：
                # 只删除看起来像是 DG 节点的（shader, texture, deformer 等），
                # 不删除 transform/mesh 等几何体节点
                geometry_types = {'transform', 'mesh', 'nurbsCurve', 'nurbsSurface',
                                  'camera', 'light', 'locator'}
                nodes_to_delete = set()
                # 先从原文件中解析节点名，再判断类型
                for line in original_lines:
                    m = re.search(r'createNode\s+(\S+)\s+.*?\s+-n\s+"([^"]+)"', line)
                    if m:
                        ntype, nname = m.group(1), m.group(2)
                        if ntype not in geometry_types and nname not in current_node_names:
                            nodes_to_delete.add(nname)

            output_lines = []
            in_deleted_node = False

            create_node_pattern = re.compile(r'createNode\s+\S+.*?\s+-n\s+"([^"]+)"')
            connect_attr_pattern = re.compile(
                r'connectAttr\s+"([^"]+)\.[^"]*"\s+"([^"]+)\.[^"]*"'
            )

            for line in original_lines:
                stripped = line.lstrip()

                if stripped.startswith('createNode'):
                    m = create_node_pattern.search(line)
                    if m:
                        node_name = m.group(1)
                        if node_name in nodes_to_delete:
                            # 节点被用户删除 → 跳过这行及其属性
                            in_deleted_node = True
                        else:
                            # 保留
                            output_lines.append(line.rstrip('\n'))
                            in_deleted_node = False
                    else:
                        output_lines.append(line.rstrip('\n'))
                        in_deleted_node = False

                elif in_deleted_node and (stripped.startswith('\t') or stripped.startswith(' ') or stripped.startswith('setAttr')):
                    # 在被删除节点块内的属性行 → 跳过
                    if stripped.startswith('setAttr') or (len(stripped) > 0 and stripped[0] in '\t '):
                        continue
                    in_deleted_node = False
                    output_lines.append(line.rstrip('\n'))

                elif stripped.startswith('connectAttr'):
                    m = connect_attr_pattern.search(line)
                    if m:
                        src = m.group(1)
                        dst = m.group(2)
                        if src in nodes_to_delete or dst in nodes_to_delete:
                            # 涉及被删除节点的连接 → 跳过
                            continue
                        output_lines.append(line.rstrip('\n'))
                    else:
                        output_lines.append(line.rstrip('\n'))
                        in_deleted_node = False

                else:
                    output_lines.append(line.rstrip('\n'))
                    in_deleted_node = False

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(output_lines) + '\n')
            return True

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False

    # === 没有原始文件 → 从头创建（最简格式）===
    lines = []
    lines.append('//Maya ASCII 2022 scene')
    lines.append('//Name: ' + filepath)
    lines.append('requires maya "2022";')
    lines.append('currentUnit -l centimeter -a degree -t film;')
    lines.append('fileInfo "application" "maya";')
    lines.append('fileInfo "product" "Maya 2022";')
    lines.append('fileInfo "version" "2022";')
    lines.append('fileInfo "cutIdentifier" "202202010000";')
    lines.append('fileInfo "osv" "Microsoft Windows 8 Business Edition, 64-bit";')

    for node_name, node_type in nodes.items():
        lines.append(f'createNode {node_type} -n "{node_name}";')

    for src_node, src_attr, dst_node, dst_attr in edges:
        lines.append(f'connectAttr "{src_node}.{src_attr}" "{dst_node}.{dst_attr}";')

    lines.append('// End of')

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return True
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False


# ============ Maya API 支持 ============

def is_maya_available():
    """Maya API 仅在 Maya GUI 内运行时可用（MAYA_MODE=True）"""
    return MAYA_MODE and _maya_api_available


def _init_maya_standalone():
    """Maya 仅在 GUI 模式下已初始化"""
    return MAYA_MODE and _maya_api_available


def _maya_api_parse(filepath):
    """使用 Maya API 解析 .ma 文件"""
    if not _init_maya_standalone():
        return None, None, None, None

    try:
        import maya.cmds as cmds

        # 新建空白场景
        cmds.file(new=True, force=True)

        # 打开 .ma 文件
        cmds.file(filepath, open=True, force=True)

        # 获取所有 DAG 节点和 DG 节点（排除默认相机等）
        default_nodes = set(['persp', 'perspShape', 'top', 'topShape',
                             'front', 'frontShape', 'side', 'sideShape',
                             'renderLayerManager', 'defaultRenderLayer',
                             'defaultLightList', 'defaultShaderList',
                             'postProcessList', 'defaultRenderUtilityList',
                             'uiConfiguration', 'sceneConfiguration',
                             'layerManager', 'defaultLayer',
                             'renderPartition', 'modelPanel4',
                             'scriptEditorTempPanel', 'outlinerPanel1',
                             'outlinerPanel2', 'hyperGraphPanel1',
                             'graphEditor1GraphEd', 'time1',
                             'lambert1', 'particleCloud1', 'shaderGlow1',
                             'initialParticleSE', 'initialShadingGroup',
                             'initialMaterialInfo'])

        all_nodes = cmds.ls(dagObjects=True) or []
        try:
            all_nodes += cmds.ls(dependencyNodes=True) or []
        except TypeError:
            # 某些 Maya 版本不支持 dependencyNodes 标志，回退到无参数 ls
            all_nodes += cmds.ls() or []
        all_nodes = list(set(all_nodes))

        # 过滤掉默认节点，保留用户创建的节点
        user_nodes = {}
        for node in all_nodes:
            # 去掉路径，获取短名
            short_name = node.split('|')[-1]
            if short_name in default_nodes or short_name.startswith('default'):
                continue
            if short_name.startswith('initial'):
                continue
            try:
                ntype = cmds.nodeType(node)
                user_nodes[short_name] = ntype
            except Exception:
                continue

        # 获取所有连接
        edges = []
        node_input_attrs = {}
        node_output_attrs = {}

        if user_nodes:
            # 用 maya.cmds 获取所有节点之间的连接
            for node_name in list(user_nodes.keys()):
                try:
                    # 获取从该节点输出的连接
                    outputs = cmds.listConnections(node_name, source=True,
                                                   destination=False,
                                                   plugs=True, connections=True)
                    if outputs:
                        # outputs 格式: [srcPlug, dstPlug, srcPlug, dstPlug, ...]
                        for i in range(0, len(outputs), 2):
                            src_full = outputs[i]
                            dst_full = outputs[i + 1]
                            if '.' in src_full and '.' in dst_full:
                                src_n = src_full.split('.')[0].split('|')[-1]
                                src_a = '.'.join(src_full.split('.')[1:])
                                dst_n = dst_full.split('.')[0].split('|')[-1]
                                dst_a = '.'.join(dst_full.split('.')[1:])
                                if src_n in user_nodes and dst_n in user_nodes:
                                    edges.append((src_n, src_a, dst_n, dst_a))
                                    node_output_attrs.setdefault(src_n, set()).add(src_a)
                                    node_input_attrs.setdefault(dst_n, set()).add(dst_a)
                except Exception:
                    continue

        return user_nodes, edges, node_input_attrs, node_output_attrs

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, None, None, None


def _maya_api_write(filepath, nodes, edges, source_filepath=None, original_node_names=None):
    """使用 Maya API 写入 .ma 文件

    **修改式保存（推荐）：** 打开原文件，只删除在 original_node_names 中
                       但不在当前 nodes 中的节点。保留几何体、相机等所有其他数据！
    **重建式保存（备用）：** 没有原文件时，从头创建（仅保留节点和连接）
    """
    if not _init_maya_standalone():
        return False

    try:
        import maya.cmds as cmds

        current_nodes = set(nodes.keys())

        if source_filepath and os.path.isfile(source_filepath):
            # === 修改式保存：打开原始文件，只删除用户删掉的节点 ===
            cmds.file(source_filepath, open=True, force=True)

            # 关键：只删除「原文件中存在但用户在 UI 中删除了」的节点
            # original_node_names 是从原文件解析时得到的用户节点集合
            if original_node_names is not None:
                nodes_to_delete_names = set(original_node_names) - current_nodes
            else:
                # 没有原节点信息时，保守处理：只删除当前 UI 中出现过的类型相关的 DG 节点
                # 不删除 transform、mesh、camera 等几何体节点
                nodes_to_delete_names = set()

            # 删除这些节点（按短名查找）
            deleted_count = 0
            for node_name in nodes_to_delete_names:
                try:
                    # 按名字查找 - 支持可能的层级路径
                    matches = cmds.ls(node_name)
                    if matches:
                        for match in matches:
                            if cmds.objExists(match):
                                cmds.delete(match)
                                deleted_count += 1
                except Exception:
                    pass

            # 保存为 Maya ASCII 文件（保留原始文件的所有其他数据）
            if filepath.lower().endswith('.ma'):
                cmds.file(rename=filepath)
                cmds.file(save=True, type='mayaAscii', force=True)
            else:
                cmds.file(rename=filepath + '.ma')
                cmds.file(save=True, type='mayaAscii', force=True)

            return True

        else:
            # === 重建式保存：没有原始文件时，从头创建 ===
            cmds.file(new=True, force=True)

            for node_name, node_type in nodes.items():
                try:
                    if not cmds.objExists(node_name):
                        cmds.createNode(node_type, name=node_name)
                except Exception:
                    pass

            for src_node, src_attr, dst_node, dst_attr in edges:
                try:
                    if cmds.objExists(src_node) and cmds.objExists(dst_node):
                        src_plug = f'{src_node}.{src_attr}'
                        dst_plug = f'{dst_node}.{dst_attr}'
                        if not cmds.isConnected(src_plug, dst_plug):
                            cmds.connectAttr(src_plug, dst_plug, force=True)
                except Exception:
                    continue

            if filepath.lower().endswith('.ma'):
                cmds.file(rename=filepath)
                cmds.file(save=True, type='mayaAscii', force=True)
            else:
                cmds.file(rename=filepath + '.ma')
                cmds.file(save=True, type='mayaAscii', force=True)
            return True

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False


def parse_ma_file_v2(filepath):
    """
    解析 .ma 文件（V2：优先使用 Maya API）。
    返回: (nodes_dict, edges_list, node_input_attrs, node_output_attrs)
    """
    # 优先使用 Maya API
    if is_maya_available():
        nodes, edges, input_attrs, output_attrs = _maya_api_parse(filepath)
        if nodes is not None:
            return nodes, edges, input_attrs, output_attrs

    # 回退到原始文本解析
    return parse_ma_file(filepath)


def parse_file(filepath):
    """
    根据文件扩展名自动选择解析器：
    - .zmetal → parse_zmetal_file()
    - .ma / 其他 → parse_ma_file_v2() (支持 Maya API + 文本回退)
    返回: (nodes_dict, edges_list, node_input_attrs, node_output_attrs)
    """
    if filepath.lower().endswith('.zmetal'):
        return parse_zmetal_file(filepath)
    else:
        return parse_ma_file_v2(filepath)


def get_file_format(filepath):
    """根据扩展名返回文件格式类型: 'zmetal' 或 'ma'"""
    return 'zmetal' if filepath.lower().endswith('.zmetal') else 'ma'


# ==================== 撤销/重做系统 ====================

class Command:
    """命令基类，所有撤销/重做操作的基类"""
    def __init__(self, description: str):
        self.description = description

    def execute(self):
        """执行命令（重做）"""
        pass

    def undo(self):
        """撤销命令"""
        pass


class UndoStack:
    """撤销栈，管理撤销/重做命令"""
    def __init__(self):
        self.undo_stack: list[Command] = []
        self.redo_stack: list[Command] = []
        self.max_stack_size = 100
        self._scene = None

    def set_scene(self, scene):
        """设置场景引用"""
        self._scene = scene

    def _notify_change(self):
        """通知状态变化"""
        if self._scene:
            self._scene.undo_state_changed.emit()

    def push(self, command: Command):
        """执行命令并添加到撤销栈"""
        command.execute()
        self.undo_stack.append(command)
        if len(self.undo_stack) > self.max_stack_size:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._notify_change()

    def undo(self):
        """撤销"""
        if self.undo_stack:
            command = self.undo_stack.pop()
            command.undo()
            self.redo_stack.append(command)
            self._notify_change()

    def redo(self):
        """重做"""
        if self.redo_stack:
            command = self.redo_stack.pop()
            command.execute()
            self.undo_stack.append(command)
            self._notify_change()

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def clear(self):
        """清空撤销/重做栈"""
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._notify_change()


# ==================== 无限画布组件（来自 infinite_canvas.py）====================

class GridBackground:
    """网格背景绘制器"""

    def __init__(self, scene: QGraphicsScene):
        self.scene = scene
        self.grid_size = 20
        self.grid_color = QColor(50, 50, 50)  # 对比度减半
        self.grid_color_light = QColor(60, 60, 60)  # 对比度减半
        self.background_color = QColor(40, 40, 40)

    def draw(self, painter: QPainter, rect: QRectF):
        """绘制网格背景"""
        # 绘制背景
        painter.fillRect(rect, self.background_color)

        # 计算网格范围
        left = int(rect.left()) - (int(rect.left()) % self.grid_size)
        top = int(rect.top()) - (int(rect.top()) % self.grid_size)
        right = int(rect.right())
        bottom = int(rect.bottom())

        # 绘制网格线
        pen = QPen(self.grid_color)
        pen.setWidth(1)
        painter.setPen(pen)

        # 垂直线
        for x in range(left, right + 1, self.grid_size):
            painter.drawLine(x, top, x, bottom)

        # 水平线
        for y in range(top, bottom + 1, self.grid_size):
            painter.drawLine(left, y, right, y)

        # 绘制主要网格线（每5格）
        pen.setColor(self.grid_color_light)
        pen.setWidth(2)
        painter.setPen(pen)

        major_grid = self.grid_size * 5
        left_major = int(rect.left()) - (int(rect.left()) % major_grid)
        top_major = int(rect.top()) - (int(rect.top()) % major_grid)

        for x in range(left_major, right + 1, major_grid):
            painter.drawLine(x, top, x, bottom)

        for y in range(top_major, bottom + 1, major_grid):
            painter.drawLine(left, y, right, y)


# ==================== 具体命令类 ====================

class AddNodeCommand(Command):
    """添加节点命令"""
    def __init__(self, scene: 'InfiniteCanvasScene', node: 'CanvasNode'):
        super().__init__(f"添加节点: {node.title}")
        self.scene = scene
        self.node = node
        self.node_pos = node.pos()

    def execute(self):
        self.scene._add_node_internal(self.node)

    def undo(self):
        self.scene._remove_node_internal(self.node)


class RemoveNodeCommand(Command):
    """移除节点命令"""
    def __init__(self, scene: 'InfiniteCanvasScene', node: 'CanvasNode'):
        super().__init__(f"删除节点: {node.title}")
        self.scene = scene
        self.node = node
        self.node_pos = node.pos()
        self.removed_connections = []
        # 保存该节点的所有连接信息
        for conn in scene.connections:
            if conn.start_node == node or conn.end_node == node:
                self.removed_connections.append((
                    conn.start_node, conn.end_node,
                    conn.start_port, conn.end_port
                ))

    def execute(self):
        self.scene._remove_node_internal(self.node)

    def undo(self):
        self.scene._add_node_internal(self.node)
        self.node.setPos(self.node_pos)
        self.node.update_port_positions()
        # 恢复连接
        for start_node, end_node, start_port, end_port in self.removed_connections:
            if start_node in self.scene.nodes and end_node in self.scene.nodes:
                self.scene._add_connection_internal(start_node, end_node, start_port, end_port)
        # 更新所有相关连接的位置
        self.scene._on_node_moved(self.node)


class AddConnectionCommand(Command):
    """添加连接命令"""
    def __init__(self, scene: 'InfiniteCanvasScene', start_node: 'CanvasNode', end_node: 'CanvasNode',
                 start_port: tuple, end_port: tuple):
        super().__init__("添加连接")
        self.scene = scene
        self.start_node = start_node
        self.end_node = end_node
        self.start_port = start_port
        self.end_port = end_port
        self.connection = None

    def execute(self):
        self.connection = self.scene._add_connection_internal(self.start_node, self.end_node, self.start_port, self.end_port)

    def undo(self):
        if self.connection:
            self.scene._remove_connection_internal(self.connection)


class RemoveConnectionCommand(Command):
    """移除连接命令"""
    def __init__(self, scene: 'InfiniteCanvasScene', connection: 'ConnectionLine'):
        super().__init__("删除连接")
        self.scene = scene
        self.connection = connection
        self.start_node = connection.start_node
        self.end_node = connection.end_node
        self.start_port = connection.start_port
        self.end_port = connection.end_port

    def execute(self):
        self.scene._remove_connection_internal(self.connection)

    def undo(self):
        self.scene._add_connection_internal(self.start_node, self.end_node, self.start_port, self.end_port)


class MoveNodeCommand(Command):
    """移动节点命令"""
    def __init__(self, node: 'CanvasNode', old_pos: QPointF, new_pos: QPointF):
        super().__init__(f"移动节点: {node.title}")
        self.node = node
        self.old_pos = old_pos
        self.new_pos = new_pos
        self.scene = node.scene() if node.scene() else None

    def _update_connections(self):
        """更新相关连接"""
        if self.scene and hasattr(self.scene, '_on_node_moved'):
            self.scene._on_node_moved(self.node)

    def execute(self):
        self.node.setPos(self.new_pos)
        self.node.update_port_positions()
        self._update_connections()

    def undo(self):
        self.node.setPos(self.old_pos)
        self.node.update_port_positions()
        self._update_connections()


class CanvasNodeSignals(QObject):
    """节点信号类（解决 QGraphicsItem 不支持 Signal 的问题）"""
    node_moved = Signal(object)
    node_selected = Signal(object)
    connection_requested = Signal(object, tuple)


class CanvasNode(QGraphicsRectItem):
    """画布节点基类 - Maya风格"""

    # 节点类型
    NODE_TYPE_RECT = 0
    NODE_TYPE_ELLIPSE = 1
    NODE_TYPE_ROUNDED = 2

    def __init__(self, title: str = "Node", node_type: int = NODE_TYPE_ROUNDED, parent=None):
        super().__init__(parent)

        # 信号对象
        self.signals = CanvasNodeSignals()
        self.node_moved = self.signals.node_moved
        self.node_selected = self.signals.node_selected
        self.connection_requested = self.signals.connection_requested

        self.title = title
        self.node_type = node_type
        self.maya_node_type = "transform"
        self.node_id = id(self)
        self.width = 180
        self.height = 100
        self.corner_radius = 6
        self.port_radius = 5
        self.header_height = 28

        # 可折叠状态
        self.is_collapsed = False
        self.collapsed_height = self.header_height

        # 设置图形
        self.setRect(0, 0, self.width, self.height)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(10)

        # Maya风格颜色 - 默认值，会根据节点类型覆盖
        self.header_color = QColor(90, 90, 90)
        self.body_color = QColor(75, 75, 75)
        self.border_color = QColor(40, 40, 40)
        self.selected_color = QColor(200, 200, 200)
        self.text_color = QColor(230, 230, 230)
        self.port_color = QColor(150, 150, 150)
        self.port_hover_color = QColor(100, 200, 100)
        
        # 端口类型颜色（Maya风格）
        self.input_port_color = QColor(200, 200, 200)
        self.output_port_color = QColor(255, 180, 0)

        # 端口
        self.input_ports = ["输入1", "输入2"]
        self.output_ports = ["输出1"]
        self.port_positions = {}

        # 状态
        self.is_dragging = False
        self.drag_start_pos = None
        self.hovered_port = None
        self.is_connecting_from_port = False
        self.clicked_port = None
        self.move_start_pos = None
        self.hovered_collapse_button = False

        self.update_height()

    def update_height(self):
        """根据端口数量更新高度"""
        if self.is_collapsed:
            self.height = self.collapsed_height
        else:
            port_height = 22
            max_ports = max(len(self.input_ports), len(self.output_ports))
            self.height = self.header_height + max_ports * port_height + 10
        self.setRect(0, 0, self.width, self.height)
        self.update_port_positions()

    def set_ports(self, inputs: list = None, outputs: list = None):
        """设置端口"""
        if inputs is not None:
            self.input_ports = inputs
        if outputs is not None:
            self.output_ports = outputs
        self.update_height()
        self.update()

    def toggle_collapse(self):
        """切换折叠状态"""
        self.is_collapsed = not self.is_collapsed
        self.update_height()
        if self.scene():
            self.scene()._on_node_moved(self)

    def update_port_positions(self):
        """更新端口位置（场景坐标）"""
        self.port_positions.clear()
        if self.is_collapsed:
            return

        port_spacing = 22
        start_y = self.header_height + 11

        for i, port_name in enumerate(self.input_ports):
            y = start_y + i * port_spacing
            self.port_positions[("input", i)] = self.mapToScene(QPointF(0, y))

        for i, port_name in enumerate(self.output_ports):
            y = start_y + i * port_spacing
            self.port_positions[("output", i)] = self.mapToScene(QPointF(self.width, y))

    def paint(self, painter: QPainter, option, widget=None):
        """绘制节点 - Maya风格"""
        rect = self.rect()

        # 主体背景 - Maya风格：整体填充相同颜色
        border_color = self.selected_color if self.isSelected() else self.border_color
        pen = QPen(border_color, 1)
        painter.setPen(pen)
        painter.setBrush(self.body_color)
        painter.drawRoundedRect(rect, self.corner_radius, self.corner_radius)

        # 标题栏 - 稍亮一点的同色系
        header_rect = QRectF(0, 0, rect.width(), self.header_height)
        header_gradient = QLinearGradient(0, 0, 0, self.header_height)
        header_gradient.setColorAt(0, self.header_color)
        header_gradient.setColorAt(1, self.header_color.darker(115))
        painter.setBrush(header_gradient)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(header_rect, self.corner_radius, self.corner_radius)

        # 选中状态高亮
        if self.isSelected():
            highlight_rect = rect.adjusted(-2, -2, 2, 2)
            painter.setPen(QPen(self.selected_color, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(highlight_rect, self.corner_radius + 2, self.corner_radius + 2)

        # 标题栏下边框线
        painter.setPen(QPen(self.body_color.darker(130)))
        painter.drawLine(0, self.header_height, rect.width(), self.header_height)

        # 折叠按钮
        collapse_rect = QRectF(rect.width() - 20, 8, 12, 12)
        painter.setPen(QPen(self.text_color))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(collapse_rect)

        # 绘制折叠箭头
        painter.setPen(QPen(self.text_color))
        collapse_center_x = rect.width() - 14
        if self.is_collapsed:
            painter.drawLine(collapse_center_x - 3, 14, collapse_center_x + 3, 14)
            painter.drawLine(collapse_center_x, 11, collapse_center_x, 17)
        else:
            painter.drawLine(collapse_center_x - 3, 14, collapse_center_x + 3, 14)

        # 绘制标题
        painter.setPen(self.text_color)
        font = QFont("Microsoft YaHei", 9, QFont.Bold)
        painter.setFont(font)
        painter.drawText(8, 2, rect.width() - 28, self.header_height - 4, Qt.AlignLeft | Qt.AlignVCenter, self.title)

        # 绘制端口
        if not self.is_collapsed:
            self.port_positions.clear()
            self._draw_ports(painter)

    def _draw_ports(self, painter: QPainter):
        """绘制端口 - Maya风格"""
        port_spacing = 22
        start_y = self.header_height + 11

        # 绘制输入端口（左侧，灰色）
        for i, port_name in enumerate(self.input_ports):
            y = start_y + i * port_spacing
            
            is_hovered = self.hovered_port == ("input", i)
            color = self.port_hover_color if is_hovered else self.input_port_color
            
            port_center = QPointF(0, y)
            port_rect = QRectF(port_center.x() - self.port_radius, port_center.y() - self.port_radius,
                              self.port_radius * 2, self.port_radius * 2)

            painter.setPen(QPen(color.darker(130)))
            painter.setBrush(color)
            painter.drawEllipse(port_rect)

            # 端口名称
            painter.setPen(self.text_color)
            font = QFont("Microsoft YaHei", 8)
            painter.setFont(font)
            painter.drawText(8, y - 7, self.width - 16, 14,
                           Qt.AlignLeft | Qt.AlignVCenter, port_name)

            self.port_positions[("input", i)] = self.mapToScene(port_center)

        # 绘制输出端口（右侧，橙色）
        for i, port_name in enumerate(self.output_ports):
            y = start_y + i * port_spacing
            
            is_hovered = self.hovered_port == ("output", i)
            color = self.port_hover_color if is_hovered else self.output_port_color
            
            port_center = QPointF(self.width, y)
            port_rect = QRectF(port_center.x() - self.port_radius, port_center.y() - self.port_radius,
                              self.port_radius * 2, self.port_radius * 2)

            painter.setPen(QPen(color.darker(130)))
            painter.setBrush(color)
            painter.drawEllipse(port_rect)

            # 端口名称
            painter.setPen(self.text_color)
            font = QFont("Microsoft YaHei", 8)
            painter.setFont(font)
            fm = QFontMetrics(font)
            text_width = fm.horizontalAdvance(port_name)
            painter.drawText(self.width - 8 - text_width, y - 7, text_width, 14,
                           Qt.AlignRight | Qt.AlignVCenter, port_name)

            self.port_positions[("output", i)] = self.mapToScene(port_center)

    def get_port_position(self, port_type: str, port_index: int) -> QPointF:
        """获取端口位置（场景坐标）"""
        if self.is_collapsed:
            # 折叠状态下，连接到节点边缘中心
            node_center_y = self.header_height / 2.0
            if port_type == "input":
                return self.mapToScene(QPointF(0, node_center_y))
            else:
                return self.mapToScene(QPointF(self.width, node_center_y))
        pos = self.port_positions.get((port_type, port_index), QPointF())
        if pos.isNull():
            # 如果找不到端口位置，连接到节点边缘中心
            node_center_y = self.height / 2.0
            if port_type == "input":
                return self.mapToScene(QPointF(0, node_center_y))
            else:
                return self.mapToScene(QPointF(self.width, node_center_y))
        return pos

    def _get_collapse_button_pos(self):
        """获取折叠按钮区域"""
        return QRectF(self.width - 20, 8, 12, 12)

    def mousePressEvent(self, event: QMouseEvent):
        """鼠标按下事件"""
        # 检查是否点击了折叠按钮
        collapse_rect = self._get_collapse_button_pos()
        if collapse_rect.contains(event.pos()) and event.button() == Qt.LeftButton:
            self.toggle_collapse()
            return

        # 检查是否点击了端口
        self.clicked_port = self._get_port_at_pos(event.pos())
        if self.clicked_port and event.button() == Qt.LeftButton:
            self.connection_requested.emit(self, self.clicked_port)
            self.is_connecting_from_port = True
            return

        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_start_pos = event.pos()
            self.move_start_pos = self.pos()

            if event.modifiers() != Qt.ShiftModifier:
                for item in self.scene().selectedItems():
                    if item != self:
                        item.setSelected(False)
            self.setSelected(True)
            self.node_selected.emit(self)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """鼠标移动事件"""
        # 如果正在从端口连线，不移动节点
        if self.is_connecting_from_port:
            return
        
        if self.is_dragging:
            self.node_moved.emit(self)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """鼠标释放事件"""
        if event.button() == Qt.LeftButton:
            # 如果移动了节点，记录撤销命令
            if self.is_dragging and self.move_start_pos is not None:
                new_pos = self.pos()
                if (new_pos - self.move_start_pos).manhattanLength() > 1:
                    # 只有真正移动了才记录
                    if self.scene():
                        command = MoveNodeCommand(self, self.move_start_pos, new_pos)
                        if not self.scene()._ignore_undo:
                            self.scene().undo_stack.push(command)
            
            self.is_dragging = False
            self.is_connecting_from_port = False
            self.clicked_port = None
            self.move_start_pos = None
        super().mouseReleaseEvent(event)

    def _get_port_at_pos(self, pos: QPointF) -> tuple:
        """获取指定位置的端口"""
        if self.is_collapsed:
            return None
            
        port_spacing = 22
        start_y = self.header_height + 11

        # 检查输入端口
        for i in range(len(self.input_ports)):
            y = start_y + i * port_spacing
            port_pos = QPointF(0, y)
            if (pos - port_pos).manhattanLength() < self.port_radius * 2:
                return ("input", i)

        # 检查输出端口
        for i in range(len(self.output_ports)):
            y = start_y + i * port_spacing
            port_pos = QPointF(self.width, y)
            if (pos - port_pos).manhattanLength() < self.port_radius * 2:
                return ("output", i)

        return None

    def itemChange(self, change, value):
        """项目状态变化"""
        if change == QGraphicsItem.ItemPositionChange:
            self.update_port_positions()
            self.node_moved.emit(self)
        return super().itemChange(change, value)


class ConnectionLine(QGraphicsPathItem):
    """连接线 - Maya风格"""

    def __init__(self, start_node: CanvasNode = None, end_node: CanvasNode = None,
                 start_port: tuple = None, end_port: tuple = None, parent=None):
        super().__init__(parent)

        self.start_node = start_node
        self.end_node = end_node
        self.start_port = start_port  # (type, index)
        self.end_port = end_port      # (type, index)

        # Maya风格连线颜色
        self.line_color = QColor(150, 200, 150)    # 默认绿色
        self.line_color_multi = QColor(255, 220, 0)  # 多属性连线：黄色
        self.line_color_selected = QColor(150, 150, 150)  # 选中：白色
        self.line_width = 2
        self.is_multi_connection = False

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(-1)

        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(0, 0)
        self.setPath(path)

        self.update_position()

    def update_position(self):
        """更新连接线位置"""
        if self.start_node and self.start_port:
            start_pos = self.start_node.get_port_position(
                self.start_port[0], self.start_port[1]
            )
        else:
            start_pos = QPointF(0, 0)

        if self.end_node and self.end_port:
            end_pos = self.end_node.get_port_position(
                self.end_port[0], self.end_port[1]
            )
        else:
            end_pos = QPointF(0, 0)

        if start_pos.isNull() or end_pos.isNull() or (start_pos == end_pos):
            return

        path = QPainterPath()
        path.moveTo(start_pos)

        ctrl_offset = max(abs(end_pos.x() - start_pos.x()) * 0.5, 50)
        ctrl_p1 = QPointF(start_pos.x() + ctrl_offset, start_pos.y())
        ctrl_p2 = QPointF(end_pos.x() - ctrl_offset, end_pos.y())

        path.cubicTo(ctrl_p1, ctrl_p2, end_pos)
        self.setPath(path)

    def paint(self, painter: QPainter, option, widget=None):
        """绘制连接线 - Maya风格"""
        path = self.path()
        if path.isEmpty():
            return

        if self.isSelected():
            color = self.line_color_selected
        elif self.is_multi_connection:
            color = self.line_color_multi
        else:
            color = self.line_color

        pen = QPen(color)
        pen.setWidth(self.line_width + 1 if self.isSelected() else self.line_width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)


class InfiniteCanvasScene(QGraphicsScene):
    """无限画布场景"""

    # 信号
    node_added = Signal(object)
    node_removed = Signal(object)
    connection_created = Signal(object, object, tuple, tuple)  # start_node, end_node, start_port, end_port
    undo_state_changed = Signal()  # 撤销/重做状态变化

    def __init__(self, parent=None):
        super().__init__(parent)

        self.grid = GridBackground(self)
        self.nodes = []
        self.connections = []
        
        # 撤销/重做栈
        self.undo_stack = UndoStack()
        self.undo_stack.set_scene(self)
        self._ignore_undo = False  # 用于批量操作时禁用撤销记录

        # 连接状态
        self.is_connecting = False
        self.connection_start_node = None
        self.connection_start_port = None
        self.temp_connection = None

        self.setSceneRect(-10000, -10000, 20000, 20000)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        """绘制背景"""
        self.grid.draw(painter, rect)

    # ============ 内部方法（不记录撤销） ============
    def _add_node_internal(self, node: CanvasNode):
        """内部添加节点方法，不记录撤销"""
        node.node_moved.connect(self._on_node_moved)
        node.node_selected.connect(self._on_node_selected)
        node.connection_requested.connect(self._on_connection_requested)
        self.addItem(node)
        self.nodes.append(node)
        self.node_added.emit(node)

    def _remove_node_internal(self, node: CanvasNode):
        """内部移除节点方法，不记录撤销"""
        if node in self.nodes:
            # 移除相关连接
            connections_to_remove = []
            for conn in list(self.connections):
                if conn.start_node == node or conn.end_node == node:
                    connections_to_remove.append(conn)
            for conn in connections_to_remove:
                self._remove_connection_internal(conn)
            
            self.nodes.remove(node)
            self.removeItem(node)
            self.node_removed.emit(node)

    def _add_connection_internal(self, start_node: CanvasNode, end_node: CanvasNode,
                                 start_port: tuple, end_port: tuple) -> ConnectionLine:
        """内部添加连接方法，不记录撤销"""
        connection = ConnectionLine(start_node, end_node, start_port, end_port)
        self.addItem(connection)
        self.connections.append(connection)
        self.connection_created.emit(start_node, end_node, start_port, end_port)
        return connection

    def _remove_connection_internal(self, connection: ConnectionLine):
        """内部移除连接方法，不记录撤销"""
        if connection in self.connections:
            self.connections.remove(connection)
            self.removeItem(connection)

    # ============ 公共方法（记录撤销） ============
    def add_node(self, title: str = "Node", pos: QPointF = None,
                 inputs: list = None, outputs: list = None) -> CanvasNode:
        """添加节点（记录撤销）"""
        node = CanvasNode(title)

        if inputs:
            node.input_ports = inputs
        if outputs:
            node.output_ports = outputs
        node.update_height()

        if pos is None:
            pos = QPointF(0, 0)
        node.setPos(pos)
        
        if not self._ignore_undo:
            command = AddNodeCommand(self, node)
            self.undo_stack.push(command)
            return command.node if hasattr(command, 'node') else (self.nodes[-1] if self.nodes else None)
        else:
            self._add_node_internal(node)
            return node

    def remove_node(self, node: CanvasNode):
        """移除节点（记录撤销）"""
        if node in self.nodes:
            if not self._ignore_undo:
                command = RemoveNodeCommand(self, node)
                self.undo_stack.push(command)
            else:
                self._remove_node_internal(node)

    def add_connection(self, start_node: CanvasNode, end_node: CanvasNode,
                       start_port: tuple, end_port: tuple) -> ConnectionLine:
        """添加连接（记录撤销）"""
        if not self._ignore_undo:
            command = AddConnectionCommand(self, start_node, end_node, start_port, end_port)
            self.undo_stack.push(command)
            return command.connection if command.connection else (self.connections[-1] if self.connections else None)
        else:
            return self._add_connection_internal(start_node, end_node, start_port, end_port)

    def remove_connection(self, connection: ConnectionLine):
        """移除连接（记录撤销）"""
        if connection in self.connections:
            if not self._ignore_undo:
                command = RemoveConnectionCommand(self, connection)
                self.undo_stack.push(command)
            else:
                self._remove_connection_internal(connection)

    def _on_node_moved(self, node: CanvasNode):
        """节点移动处理"""
        # 更新所有相关连接
        for conn in self.connections:
            if conn.start_node == node or conn.end_node == node:
                conn.update_position()

    def _on_node_selected(self, node: CanvasNode):
        """节点选择处理"""
        pass

    def _on_connection_requested(self, node: CanvasNode, port: tuple):
        """连接请求处理 - Maya 风格连线"""
        if not self.is_connecting:
            # 开始连接
            self.is_connecting = True
            self.connection_start_node = node
            self.connection_start_port = port

            # 创建临时连接线
            start_pos = node.get_port_position(port[0], port[1])
            self.temp_connection = ConnectionLine()
            path = QPainterPath()
            path.moveTo(start_pos)
            path.lineTo(start_pos)
            self.temp_connection.setPath(path)
            self.addItem(self.temp_connection)
        else:
            # 检查是否在另一个节点上，尝试完成连接
            if node != self.connection_start_node:
                self._complete_connection(node, port)
            else:
                # 在同一节点上点击，取消连线
                self._cancel_connection()

    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.is_connecting and self.temp_connection:
            # 更新临时连接线 - 使用贝塞尔曲线
            start_pos = self.connection_start_node.get_port_position(
                self.connection_start_port[0], self.connection_start_port[1]
            )
            end_pos = event.scenePos()
            
            # 计算控制点
            dx = end_pos.x() - start_pos.x()
            ctrl_offset = min(abs(dx) / 2, 100)
            
            ctrl_p1 = QPointF(start_pos.x() + ctrl_offset, start_pos.y())
            ctrl_p2 = QPointF(end_pos.x() - ctrl_offset, end_pos.y())
            
            path = QPainterPath()
            path.moveTo(start_pos)
            path.cubicTo(ctrl_p1, ctrl_p2, end_pos)
            self.temp_connection.setPath(path)

        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.LeftButton:
            if self.is_connecting:
                # 检查是否点击了节点的端口
                item = self.itemAt(event.scenePos(), QTransform())
                if isinstance(item, CanvasNode):
                    port = item._get_port_at_pos(item.mapFromScene(event.scenePos()))
                    if port:
                        # 点击端口，尝试完成连接
                        if item != self.connection_start_node:
                            self._complete_connection(item, port)
                    else:
                        # 点击节点但没点击端口，继续连线
                        pass
                else:
                    # 点击空白处，取消连线
                    self._cancel_connection()

        super().mousePressEvent(event)

    def _cancel_connection(self):
        """取消连接"""
        self.is_connecting = False
        self.connection_start_node = None
        self.connection_start_port = None
        if self.temp_connection:
            self.removeItem(self.temp_connection)
            self.temp_connection = None

    def _complete_connection(self, end_node: CanvasNode, end_port: tuple):
        """完成连接 - Maya 风格：输出连输入"""
        # Maya 风格连线：
        # - 从输出端口开始连线，连接到输入端口
        # - 从输入端口开始连线，连接到输出端口
        
        start_type = self.connection_start_port[0]
        end_type = end_port[0]
        
        if (start_type == "output" and end_type == "input") or \
           (start_type == "input" and end_type == "output"):
            # 确保输出连输入，输入连输出
            
            if start_type == "output":
                # 输出 -> 输入，正常连接
                self.add_connection(
                    self.connection_start_node, end_node,
                    self.connection_start_port, end_port
                )
            else:
                # 输入 -> 输出，交换顺序
                self.add_connection(
                    end_node, self.connection_start_node,
                    end_port, self.connection_start_port
                )

        self._cancel_connection()

    def keyPressEvent(self, event: QKeyEvent):
        """键盘事件"""
        if event.key() == Qt.Key_Delete:
            # 删除选中的项
            selected_items = self.selectedItems()
            for item in selected_items:
                if isinstance(item, CanvasNode):
                    self.remove_node(item)
                elif isinstance(item, ConnectionLine):
                    self.remove_connection(item)

        super().keyPressEvent(event)


class InfiniteCanvasView(QGraphicsView):
    """无限画布视图"""

    # 信号
    zoom_changed = Signal(float)

    def __init__(self, scene: InfiniteCanvasScene = None, parent=None):
        if scene is None:
            scene = InfiniteCanvasScene()

        super().__init__(scene, parent)

        self.scene = scene

        # 视图设置
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        # 缩放
        self.zoom_factor = 1.15
        self.current_zoom = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 10.0

        # 平移
        self.is_panning = False
        self.last_mouse_pos = None

        # 不设置视图背景色，让场景的网格背景显示
        self.setStyleSheet("background: transparent;")
        
        # 启用视图拖放功能
        self.setAcceptDrops(True)

    def wheelEvent(self, event: QWheelEvent):
        """滚轮缩放"""
        delta = event.angleDelta().y()

        if delta > 0:
            zoom_in = True
        else:
            zoom_in = False

        if zoom_in:
            factor = self.zoom_factor
        else:
            factor = 1.0 / self.zoom_factor

        new_zoom = self.current_zoom * factor

        if self.min_zoom <= new_zoom <= self.max_zoom:
            self.scale(factor, factor)
            self.current_zoom = new_zoom
            self.zoom_changed.emit(self.current_zoom)

    def mousePressEvent(self, event: QMouseEvent):
        """鼠标按下事件"""
        if event.button() == Qt.MiddleButton:
            self.is_panning = True
            self.last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return

        elif event.button() == Qt.LeftButton and event.modifiers() == Qt.ControlModifier:
            self.is_panning = True
            self.last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return

        elif event.button() == Qt.LeftButton:
            # 检查是否点击了节点，如果是则让节点处理拖拽
            item = self.itemAt(event.pos())
            if isinstance(item, CanvasNode):
                # 不进入框选模式，让节点自己处理拖拽
                super().mousePressEvent(event)
                return
            else:
                # 点击空白处，进入框选模式
                self.setDragMode(QGraphicsView.RubberBandDrag)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """鼠标移动事件"""
        if self.is_panning and self.last_mouse_pos:
            delta = event.pos() - self.last_mouse_pos
            self.last_mouse_pos = event.pos()

            # 平移视图
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """鼠标释放事件"""
        if event.button() == Qt.MiddleButton or \
           (event.button() == Qt.LeftButton and self.is_panning):
            self.is_panning = False
            self.last_mouse_pos = None
            self.setCursor(Qt.ArrowCursor)
            return

        elif event.button() == Qt.LeftButton:
            # 释放后关闭框选模式，恢复默认行为
            self.setDragMode(QGraphicsView.NoDrag)

        super().mouseReleaseEvent(event)

    def fit_to_scene(self):
        """适应场景"""
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        # 更新当前缩放
        transform = self.transform()
        self.current_zoom = transform.m11()
        self.zoom_changed.emit(self.current_zoom)

    def reset_zoom(self):
        """重置缩放"""
        self.resetTransform()
        self.current_zoom = 1.0
        self.zoom_changed.emit(self.current_zoom)

    def center_on_origin(self):
        """居中到原点"""
        self.centerOn(0, 0)

    # ============ 视图拖放支持 ============
    def dragEnterEvent(self, event):
        """视图拖放进入事件"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for url in urls:
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    ext = file_path.lower()
                    if ext.endswith('.ma') or ext.endswith('.zmetal'):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dragMoveEvent(self, event):
        """视图拖放移动事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """视图拖放完成事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    ext = file_path.lower()
                    if ext.endswith('.ma') or ext.endswith('.zmetal'):
                        # 转发到主窗口处理
                        if hasattr(self.window(), '_load_ma_file'):
                            self.window()._load_ma_file(file_path)
                            event.acceptProposedAction()
                            return
        event.ignore()


# ==================== Maya 节点图适配层 ====================

class MayaNodeGraphAdapter:
    """适配 Maya 节点数据到无限画布"""

    def __init__(self, scene: InfiniteCanvasScene):
        self.scene = scene
        self.node_map = {}  # name -> CanvasNode
        self.edge_map = {}  # (src, dst) -> ConnectionLine
        self.original_node_names = set()  # 原文件中的节点名集合，用于保存时对比

    def build_graph(self, nodes, edges_with_attrs, input_attrs, output_attrs):
        """构建图"""
        self.clear()
        self.original_node_names = set(nodes.keys())  # 记录原始节点名

        # 批量操作时禁用撤销记录
        self.scene._ignore_undo = True

        try:
            # 创建节点
            for name, ntype in nodes.items():
                inputs = sorted(input_attrs.get(name, set()))[:8]
                outputs = sorted(output_attrs.get(name, set()))[:8]

                node = self.scene.add_node(
                    title=name,
                    pos=QPointF(0, 0),
                    inputs=inputs if inputs else [""],
                    outputs=outputs if outputs else [""]
                )
                # 设置 Maya 节点类型和颜色
                node.maya_node_type = ntype
                header_color, body_color = self._get_node_colors(ntype)
                node.header_color = header_color
                node.body_color = body_color
                self.node_map[name] = node

            # 创建连接
            simple_edges = []
            connection_pairs = {}  # 统计每对节点间的连线数量
            created_connections = []  # 记录已创建的连接

            for src, src_attr, dst, dst_attr in edges_with_attrs:
                if src in self.node_map and dst in self.node_map:
                    src_node = self.node_map[src]
                    dst_node = self.node_map[dst]

                    # 查找端口索引
                    src_idx = self._find_port_index(src_node, src_attr, "output")
                    dst_idx = self._find_port_index(dst_node, dst_attr, "input")

                    if src_idx is not None and dst_idx is not None:
                        conn = self.scene.add_connection(
                            src_node, dst_node,
                            ("output", src_idx), ("input", dst_idx)
                        )
                        # 记录这对节点
                        pair = (src, dst)
                        connection_pairs[pair] = connection_pairs.get(pair, 0) + 1
                        created_connections.append((conn, pair))

                        self.edge_map[(src, dst)] = conn

                    simple_edges.append((src, dst))

            # 标记多属性连线为黄色
            for conn, pair in created_connections:
                if connection_pairs.get(pair, 0) > 1:
                    conn.is_multi_connection = True

            # 清空撤销栈（因为我们刚做了批量操作）
            self.scene.undo_stack.clear()
            
            return simple_edges
        finally:
            self.scene._ignore_undo = False

    def clear(self):
        """清空图"""
        for node in list(self.scene.nodes):
            self.scene.remove_node(node)
        self.node_map.clear()
        self.edge_map.clear()
        self.original_node_names = set()

    def get_current_state(self):
        """获取当前图的状态，返回 (nodes_dict, edges_list)
        nodes_dict: {node_name: node_type}
        edges_list: [(src_node, src_attr, dst_node, dst_attr)]
        """
        nodes_dict = {}
        edges_list = []

        for node in self.scene.nodes:
            nodes_dict[node.title] = node.maya_node_type

        for conn in self.scene.connections:
            if conn.start_node and conn.end_node and conn.start_port and conn.end_port:
                port_type, port_idx = conn.start_port
                if port_type == "output" and port_idx < len(conn.start_node.output_ports):
                    src_attr = conn.start_node.output_ports[port_idx]
                else:
                    continue

                port_type_d, port_idx_d = conn.end_port
                if port_type_d == "input" and port_idx_d < len(conn.end_node.input_ports):
                    dst_attr = conn.end_node.input_ports[port_idx_d]
                else:
                    continue

                edges_list.append((conn.start_node.title, src_attr, conn.end_node.title, dst_attr))

        return nodes_dict, edges_list

    def _find_port_index(self, node: CanvasNode, attr_name: str, port_type: str) -> int:
        """查找属性对应的端口索引"""
        ports = node.output_ports if port_type == "output" else node.input_ports
        for i, name in enumerate(ports):
            if name == attr_name:
                return i
        return None

    def _get_node_colors(self, node_type: str):
        """根据节点类型获取Maya风格低饱和度颜色 (header_color, body_color)"""
        color_map = {
            # 常见Maya节点类型 - 低饱和度配色
            'time': (QColor(150, 125, 90), QColor(135, 110, 75)),
            'transform': (QColor(110, 130, 150), QColor(95, 115, 135)),
            'place2dTexture': (QColor(135, 115, 150), QColor(120, 100, 135)),
            'place3dTexture': (QColor(135, 115, 150), QColor(120, 100, 135)),
            'file': (QColor(115, 140, 120), QColor(100, 125, 105)),
            'expression': (QColor(95, 110, 150), QColor(80, 95, 135)),
            'polySphere': (QColor(125, 125, 120), QColor(110, 110, 105)),
            'pSphereShape': (QColor(130, 125, 120), QColor(115, 110, 105)),
            'polyCube': (QColor(125, 125, 120), QColor(110, 110, 105)),
            'pCubeShape': (QColor(130, 125, 120), QColor(115, 110, 105)),
            'noise': (QColor(115, 140, 140), QColor(100, 125, 125)),
            'textureDeformer': (QColor(115, 130, 145), QColor(100, 115, 130)),
            'textureDeformerHandle': (QColor(120, 130, 140), QColor(105, 115, 125)),
            'standardSurface': (QColor(95, 115, 145), QColor(80, 100, 130)),
            'standardSurface2': (QColor(95, 115, 145), QColor(80, 100, 130)),
            'surfaceShader': (QColor(100, 105, 135), QColor(85, 90, 120)),
            'lambert': (QColor(100, 115, 135), QColor(85, 100, 120)),
            'blinn': (QColor(105, 110, 135), QColor(90, 95, 120)),
            'phong': (QColor(105, 100, 130), QColor(90, 85, 115)),
            'shadingEngine': (QColor(100, 105, 135), QColor(85, 90, 120)),
            'aiSkyDomeLight': (QColor(140, 135, 100), QColor(125, 120, 85)),
            'VRayLightDomeShape': (QColor(120, 130, 135), QColor(105, 115, 120)),
            'VRayPlaceEnvTex': (QColor(140, 115, 140), QColor(125, 100, 125)),
            'mesh': (QColor(130, 125, 120), QColor(115, 110, 105)),
            'nurbsCurve': (QColor(130, 120, 100), QColor(115, 105, 85)),
            'nurbsSurface': (QColor(130, 120, 100), QColor(115, 105, 85)),
            'camera': (QColor(110, 130, 130), QColor(95, 115, 115)),
            'light': (QColor(145, 130, 90), QColor(130, 115, 75)),
            'aiStandardSurface': (QColor(95, 115, 145), QColor(80, 100, 130)),
        }
        if node_type in color_map:
            return color_map[node_type]
        # 默认颜色 - 蓝灰色
        default_header = QColor(100, 115, 130)
        default_body = QColor(85, 100, 115)
        return (default_header, default_body)

    def apply_layout(self, simple_edges, x_spacing=280, y_spacing=120):
        """应用层级布局"""
        if not self.node_map:
            return

        node_items = self.node_map
        edges = simple_edges

        graph = {name: [] for name in node_items}
        reverse_graph = {name: [] for name in node_items}
        for src, dst in edges:
            if src in graph and dst in graph:
                if dst not in graph[src]:
                    graph[src].append(dst)
                if src not in reverse_graph[dst]:
                    reverse_graph[dst].append(src)

        depth = {name: 0 for name in node_items}
        max_depth_limit = min(len(node_items), 8)
        
        start_nodes = [name for name in node_items if len(reverse_graph[name]) == 0]
        if not start_nodes:
            start_nodes = list(node_items.keys())[:1]
        
        for start in start_nodes:
            queue = deque([(start, 0)])
            local_visited = set()
            while queue:
                current, d = queue.popleft()
                if current in local_visited:
                    continue
                local_visited.add(current)
                
                if d > depth[current] and d <= max_depth_limit:
                    depth[current] = d
                
                if d < max_depth_limit:
                    for neighbor in graph.get(current, []):
                        if neighbor not in local_visited:
                            queue.append((neighbor, d + 1))

        max_depth = max(depth.values()) if depth else 0
        
        layers = {}
        for name, d in depth.items():
            layers.setdefault(d, []).append(name)

        for d in layers:
            layers[d].sort(key=lambda n: (-len(graph[n]), n))

        pos = {}
        for d, names in sorted(layers.items()):
            node_heights = [node_items[name].height for name in names]
            total_height = sum(node_heights) + (len(names) - 1) * y_spacing * 0.5
            y_start = -total_height / 2
            current_y = y_start
            for name in names:
                pos[name] = QPointF(d * x_spacing, current_y)
                current_y += node_items[name].height + y_spacing * 0.5

        for name, item in node_items.items():
            item.setPos(pos.get(name, QPointF(0, 0)))
            item.update_port_positions()

        for conn in self.scene.connections:
            conn.update_position()


# ---------- 主窗口 ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ma-zmetal节点编辑器 - 无限画布")
        self.resize(1300, 900)
        
        # 启用拖放功能
        self.setAcceptDrops(True)

        # 创建画布组件
        self.canvas_widget = QWidget()
        layout = QVBoxLayout(self.canvas_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 画布视图
        self.scene = InfiniteCanvasScene()
        self.view = InfiniteCanvasView(self.scene)

        # 工具栏
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        layout.addWidget(self.view)

        # 状态栏
        statusbar = self._create_statusbar()
        layout.addWidget(statusbar)

        self.setCentralWidget(self.canvas_widget)

        # Maya 适配器
        self.adapter = MayaNodeGraphAdapter(self.scene)

        # 当前编辑的文件路径
        self.current_file_path = None
        self.simple_edges = []

        self._create_menu()
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

        self.setStyleSheet("""
            QMainWindow { background-color: #3a3a3a; }
            QMenuBar { background-color: #4a4a4a; color: #eee; }
            QMenuBar::item:selected { background-color: #5a5a5a; }
            QToolBar { background-color: #4a4a4a; border: none; spacing: 4px; }
            QPushButton { background-color: #5a5a5a; color: white; border: none; padding: 6px; border-radius: 3px; }
            QPushButton:hover { background-color: #6a6a6a; }
            QPushButton:disabled { background-color: #3a3a3a; color: #6a6a6a; }
            QStatusBar { color: #ccc; }
        """)

        # 连接撤销/重做状态变化信号
        self.scene.undo_state_changed.connect(self._update_undo_redo_state)

        # 启动时画布为空白，不加载示例数据

    def _create_toolbar(self) -> QFrame:
        """创建工具栏"""
        toolbar = QFrame()
        toolbar.setFrameStyle(QFrame.StyledPanel)
        toolbar.setMaximumHeight(50)
        toolbar.setStyleSheet("""
            QFrame {
                background-color: #3d3d3d;
                border-bottom: 1px solid #555;
            }
            QPushButton {
                background-color: #4a4a4a;
                color: #ddd;
                border: 1px solid #555;
                padding: 5px 15px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #5a5a5a;
            }
            QPushButton:pressed {
                background-color: #6a6a6a;
            }
        """)

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 5, 10, 5)

        # 打开文件按钮
        btn_open = QPushButton("打开文件")
        btn_open.clicked.connect(self.open_file)
        layout.addWidget(btn_open)

        # 保存文件按钮
        btn_save = QPushButton("保存文件")
        btn_save.clicked.connect(self.save_file)
        layout.addWidget(btn_save)

        # 另存为按钮
        btn_save_as = QPushButton("另存为...")
        btn_save_as.clicked.connect(self.save_file_as)
        layout.addWidget(btn_save_as)

        # 撤销按钮
        self.btn_undo = QPushButton("撤销 (Ctrl+Z)")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_undo.setEnabled(False)
        layout.addWidget(self.btn_undo)

        # 重做按钮
        self.btn_redo = QPushButton("重做 (Ctrl+Y)")
        self.btn_redo.clicked.connect(self._redo)
        self.btn_redo.setEnabled(False)
        layout.addWidget(self.btn_redo)

        # 层级布局按钮
        btn_layout = QPushButton("层级布局")
        btn_layout.clicked.connect(self.apply_hierarchy_layout)
        layout.addWidget(btn_layout)

        # 清空按钮
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.clear_scene)
        layout.addWidget(btn_clear)

        layout.addStretch()

        # 缩放控制
        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedWidth(30)
        btn_zoom_in.clicked.connect(self._zoom_in)
        layout.addWidget(btn_zoom_in)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: #ddd; min-width: 50px;")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.zoom_label)

        btn_zoom_out = QPushButton("-")
        btn_zoom_out.setFixedWidth(30)
        btn_zoom_out.clicked.connect(self._zoom_out)
        layout.addWidget(btn_zoom_out)

        btn_reset = QPushButton("重置视图")
        btn_reset.clicked.connect(self._reset_view)
        layout.addWidget(btn_reset)

        # 帮助按钮
        help_btn = QPushButton("?")
        help_btn.setFixedSize(34, 34)
        help_btn.setToolTip("使用帮助")
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none;"
            "font-size: 18px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_help)
        layout.addWidget(help_btn)

        # 连接缩放信号
        self.view.zoom_changed.connect(self._on_zoom_changed)

        return toolbar

    def _create_statusbar(self) -> QFrame:
        """创建状态栏"""
        statusbar = QFrame()
        statusbar.setFrameStyle(QFrame.StyledPanel)
        statusbar.setMaximumHeight(25)
        statusbar.setStyleSheet("""
            QFrame {
                background-color: #3d3d3d;
                border-top: 1px solid #555;
            }
            QLabel {
                color: #aaa;
                font-size: 11px;
            }
        """)

        layout = QHBoxLayout(statusbar)
        layout.setContentsMargins(10, 2, 10, 2)

        self.status_label = QLabel("就绪 | 中键拖拽平移 | 滚轮缩放 | Delete删除 | Ctrl+Z撤销 | Ctrl+Y重做")
        layout.addWidget(self.status_label)

        layout.addStretch()

        self.pos_label = QLabel("(0, 0)")
        layout.addWidget(self.pos_label)

        return statusbar

    def _create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")

        open_action = file_menu.addAction("打开 .ma / .zmetal 文件")
        open_action.triggered.connect(self.open_file)
        open_action.setShortcut("Ctrl+O")

        save_action = file_menu.addAction("保存")
        save_action.triggered.connect(self.save_file)
        save_action.setShortcut("Ctrl+S")

        save_as_action = file_menu.addAction("另存为...")
        save_as_action.triggered.connect(self.save_file_as)
        save_as_action.setShortcut("Ctrl+Shift+S")

        clear_action = file_menu.addAction("清空")
        clear_action.triggered.connect(self.clear_scene)

        file_menu.addSeparator()
        exit_action = file_menu.addAction("退出")
        exit_action.triggered.connect(self.close)

        edit_menu = menubar.addMenu("编辑")
        self.undo_action = edit_menu.addAction("撤销")
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self._undo)
        self.undo_action.setEnabled(False)
        
        self.redo_action = edit_menu.addAction("重做")
        self.redo_action.setShortcut("Ctrl+Y")
        self.redo_action.triggered.connect(self._redo)
        self.redo_action.setEnabled(False)

        view_menu = menubar.addMenu("视图")
        layout_action = view_menu.addAction("层级布局")
        layout_action.triggered.connect(self.apply_hierarchy_layout)
        reset_view = view_menu.addAction("重置视图")
        reset_view.triggered.connect(self._reset_view)

        help_menu = menubar.addMenu("帮助")
        help_action = help_menu.addAction("使用帮助")
        help_action.triggered.connect(self._on_help)
        help_action.setShortcut("F1")

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "支持的文件 (*.ma *.zmetal);;Maya Ascii (*.ma);;材质节点 (*.zmetal);;所有文件 (*)")
        if not path:
            return
        self._load_file(path)

    def save_file(self):
        """保存当前节点图到文件 —— 打开原始文件，只删除用户移除的节点，保留其他所有数据"""
        if not self.current_file_path:
            self.save_file_as()
            return

        try:
            nodes, edges = self.adapter.get_current_state()
            if not nodes:
                QMessageBox.warning(self, "保存", "当前没有任何节点可保存。")
                return

            file_format = get_file_format(self.current_file_path)

            if file_format == 'zmetal':
                success = write_zmetal_file(
                    self.current_file_path, nodes, edges,
                    source_filepath=self.current_file_path
                )
            else:
                success = write_ma_file(
                    self.current_file_path, nodes, edges,
                    source_filepath=self.current_file_path,
                    original_node_names=self.adapter.original_node_names
                )
            if success:
                fmt_label = "zmetal" if file_format == 'zmetal' else "MA"
                self.statusBar.showMessage(f"已保存({fmt_label}): {len(nodes)} 个节点, {len(edges)} 条连接 -> {self.current_file_path}", 4000)
            else:
                QMessageBox.critical(self, "保存失败", f"写入文件时发生错误: {self.current_file_path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "保存失败", f"保存文件时发生异常：\n{str(e)}")

    def save_file_as(self):
        """另存为新文件 —— 使用原始文件作为基底，保留几何体等数据"""
        try:
            nodes, edges = self.adapter.get_current_state()
            if not nodes:
                QMessageBox.warning(self, "保存", "当前没有任何节点可保存。")
                return

            default_name = "untitled.ma"
            if self.current_file_path:
                import os
                default_name = os.path.basename(self.current_file_path)

            path, _ = QFileDialog.getSaveFileName(
                self, "另存为", default_name,
                "Maya Ascii (*.ma);;材质节点 (*.zmetal);;所有文件 (*)"
            )
            if not path:
                return

            file_format = get_file_format(path)

            if file_format == 'zmetal':
                if not path.lower().endswith('.zmetal'):
                    path += '.zmetal'
                source_for_save = self.current_file_path if self.current_file_path and self.current_file_path.lower().endswith('.zmetal') else None
                success = write_zmetal_file(
                    path, nodes, edges,
                    source_filepath=source_for_save
                )
            else:
                if not path.lower().endswith('.ma'):
                    path += '.ma'
                source_for_save = self.current_file_path if self.current_file_path and not self.current_file_path.lower().endswith('.zmetal') else None
                success = write_ma_file(
                    path, nodes, edges,
                    source_filepath=source_for_save,
                    original_node_names=self.adapter.original_node_names
                )
            if success:
                self.current_file_path = path
                self.setWindowTitle(f"ma-zmetal节点编辑器 - {path}")
                self.statusBar.showMessage(f"已另存为: {len(nodes)} 个节点, {len(edges)} 条连接 -> {path}", 4000)
            else:
                QMessageBox.critical(self, "保存失败", f"写入文件时发生错误: {path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "保存失败", f"保存文件时发生异常：\n{str(e)}")

    def load_mock_data(self):
        """加载测试数据用于调试中键拖拽"""
        nodes = {
            'place2dTexture1': 'place2dTexture',
            'file1': 'file',
            'aiSkyDomeLight1': 'aiSkyDomeLight',
            'transform1': 'transform',
            'VRayPlaceEnvTex1': 'VRayPlaceEnvTex',
            'Collapsed_Stars_bude_exr': 'file',
            'VRayLightDomeShape1': 'VRayLightDomeShape',
            'place2dTexture3': 'place2dTexture',
        }

        edges_with_attrs = [
            ('place2dTexture1', 'c', 'file1', 'c'),
            ('place2dTexture1', 'msg', 'file1', 'msg'),
            ('place2dTexture1', 'mu', 'file1', 'mu'),
            ('place2dTexture1', 'mv', 'file1', 'mv'),
            ('file1', 'oc', 'aiSkyDomeLight1', 'sc'),
            ('file1', 'msg', 'aiSkyDomeLight1', 'ltd'),
            ('VRayPlaceEnvTex1', 'Out UV', 'Collapsed_Stars_bude_exr', 'UV 坐标'),
            ('Collapsed_Stars_bude_exr', '输出颜色', 'VRayLightDomeShape1', 'Dome Tex'),
            ('place2dTexture3', 'UV 坐标', 'VRayPlaceEnvTex1', 'Transform'),
        ]

        node_input_attrs = {}
        node_output_attrs = {}

        for src, src_attr, dst, dst_attr in edges_with_attrs:
            node_input_attrs.setdefault(dst, set()).add(dst_attr)
            node_output_attrs.setdefault(src, set()).add(src_attr)

        self.build_graph(nodes, edges_with_attrs, node_input_attrs, node_output_attrs)
        self.statusBar.showMessage("已加载测试数据 - 中键拖动画布，滚轮缩放", 5000)

    def build_graph(self, nodes, edges_with_attrs, input_attrs, output_attrs):
        self.simple_edges = self.adapter.build_graph(nodes, edges_with_attrs, input_attrs, output_attrs)
        self.apply_hierarchy_layout()
        self._reset_view()

    def apply_hierarchy_layout(self):
        if not self.adapter.node_map:
            return
        self.adapter.apply_layout(self.simple_edges)
        self.view.update()

    def clear_scene(self):
        """清空画布，重置为初始状态"""
        if not self.scene.nodes:
            return
        reply = QMessageBox.question(
            self, "清空确认",
            "确定要清空当前画布的所有节点和连接吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.adapter.clear()
            self.current_file_path = None
            self.setWindowTitle("ma-zmetal节点编辑器")
            self.simple_edges = []
            self.statusBar.showMessage("画布已清空", 3000)

    def _reset_view(self):
        self.view.reset_zoom()
        self.view.center_on_origin()

    def _zoom_in(self):
        """放大"""
        self.view.wheelEvent(type('Event', (), {'angleDelta': lambda: type('Delta', (), {'y': lambda: 120})()})())

    def _zoom_out(self):
        """缩小"""
        self.view.wheelEvent(type('Event', (), {'angleDelta': lambda: type('Delta', (), {'y': lambda: -120})()})())

    def _on_zoom_changed(self, zoom: float):
        """缩放变化处理"""
        self.zoom_label.setText(f"{int(zoom * 100)}%")

    # ============ 撤销/重做功能 ============
    def _undo(self):
        """撤销操作"""
        if self.scene.undo_stack.can_undo():
            # 获取将要撤销的命令描述
            desc = None
            if self.scene.undo_stack.undo_stack:
                desc = self.scene.undo_stack.undo_stack[-1].description
            
            self.scene.undo_stack.undo()
            self._update_undo_redo_state()
            
            if desc:
                self.statusBar.showMessage(f"已撤销: {desc}", 2000)
            else:
                self.statusBar.showMessage("已撤销", 2000)

    def _redo(self):
        """重做操作"""
        if self.scene.undo_stack.can_redo():
            # 获取将要重做的命令描述
            desc = None
            if self.scene.undo_stack.redo_stack:
                desc = self.scene.undo_stack.redo_stack[-1].description
            
            self.scene.undo_stack.redo()
            self._update_undo_redo_state()
            
            if desc:
                self.statusBar.showMessage(f"已重做: {desc}", 2000)
            else:
                self.statusBar.showMessage("已重做", 2000)

    def _update_undo_redo_state(self):
        """更新撤销/重做按钮的状态"""
        can_undo = self.scene.undo_stack.can_undo()
        can_redo = self.scene.undo_stack.can_redo()
        
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)
        self.undo_action.setEnabled(can_undo)
        self.redo_action.setEnabled(can_redo)

    # ============ 拖放功能 ============
    def dragEnterEvent(self, event):
        """拖放进入事件"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for url in urls:
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    ext = file_path.lower()
                    if ext.endswith('.ma') or ext.endswith('.zmetal'):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dragMoveEvent(self, event):
        """拖放移动事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """拖放完成事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    ext = file_path.lower()
                    if ext.endswith('.ma') or ext.endswith('.zmetal'):
                        self._load_ma_file(file_path)
                        event.acceptProposedAction()
                        return
        event.ignore()

    def _load_ma_file(self, file_path):
        """加载文件（自动识别 .ma / .zmetal）"""
        try:
            nodes, edges_with_attrs, input_attrs, output_attrs = parse_file(file_path)
            if not nodes:
                QMessageBox.warning(self, "解析结果", "未找到任何节点，请检查文件格式。")
                return
            self.current_file_path = file_path
            self.setWindowTitle(f"ma-zmetal节点编辑器 - {file_path}")
            self.build_graph(nodes, edges_with_attrs, input_attrs, output_attrs)
            self.statusBar.showMessage(f"加载完成: {len(nodes)} 个节点, {len(edges_with_attrs)} 条连接", 4000)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "解析错误", f"解析文件时发生异常：\n{str(e)}\n\n详细信息请查看控制台输出。")

    def _on_help(self):
        """打开使用帮助"""
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "预览ma节点连接", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[NodeEditor] 帮助文件未找到:", help_path)


def main(file_path=None):
    global MAYA_MODE, _editor_window

    # 检查是否已存在 QApplication（Maya GUI 内运行，或重复调用）
    app = QApplication.instance()

    if app is None:
        # 独立运行：创建 QApplication，使用文本解析器（无需 Maya）
        app = QApplication(sys.argv)
        MAYA_MODE = False
    else:
        # 已有 QApplication（在 Maya GUI 中运行），可使用 Maya API 增强解析
        MAYA_MODE = True

    # 保持全局引用防止窗口被垃圾回收
    win = MainWindow()
    _editor_window = win
    win.show()

    # 如果传入了文件路径，自动加载
    if file_path and os.path.isfile(file_path):
        win._load_ma_file(file_path)

    if MAYA_MODE and not sys.executable.endswith('mayapy.exe'):
        # Maya GUI 模式：Maya 自己处理事件循环
        win.raise_()
        win.activateWindow()
    else:
        # 独立运行：需要自己调用 app.exec()
        sys.exit(app.exec())

    return win


if __name__ == "__main__":
    main()
