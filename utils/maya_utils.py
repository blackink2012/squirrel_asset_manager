import re

_IN_MAYA = False
try:
    import maya.cmds as cmds
    import maya.OpenMayaUI as omui
    _IN_MAYA = True
except ImportError:
    pass


def get_maya_version():
    if _IN_MAYA:
        try:
            raw = str(cmds.about(version=True))
            # 兼容 "2024" / "2024.2" / "2024.2.1" 等格式，取首个整数
            m = re.search(r"\d+", raw)
            return int(m.group()) if m else 0
        except Exception:
            return 0
    return 0


def get_qt_modules():
    """获取 Qt 绑定（PySide6 优先，失败自动降级 PySide2）

    - Maya 2025+ 自带 PySide6/shiboken6
    - Maya 2022~2024 自带 PySide2/shiboken2
    不依赖版本号判断：直接尝试导入，哪个可用用哪个，
    避免版本号解析失败或环境缺失绑定时报错。
    """
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        from shiboken6 import wrapInstance
        WindowType = QtCore.Qt.WindowType
        return QtWidgets, QtCore, QtGui, wrapInstance, WindowType
    except ImportError:
        pass
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        from shiboken2 import wrapInstance
        WindowType = QtCore.Qt
        return QtWidgets, QtCore, QtGui, wrapInstance, WindowType
    except ImportError:
        pass
    raise ImportError("需要 PySide6/shiboken6 或 PySide2/shiboken2")


def qt_exec(obj, *args, **kwargs):
    """exec() 兼容：PySide6 用 exec()，PySide2 只有 exec_()

    适用于 QDialog / QMenu / QDrag 等 Qt 对象。
    """
    fn = getattr(obj, "exec_", None)
    if fn is None:
        fn = obj.exec
    return fn(*args, **kwargs)


def get_maya_window():
    if not _IN_MAYA:
        return None
    try:
        QtWidgets, QtCore, QtGui, wrapInstance, WindowType = get_qt_modules()
        main_window_ptr = omui.MQtUtil.mainWindow()
        if main_window_ptr:
            return wrapInstance(int(main_window_ptr), QtWidgets.QMainWindow)
    except Exception:
        # 无 Maya 上下文（如 mayapy 独立运行 / standalone 预览）时安全返回 None
        return None
    return None


def get_maya_materials_from_selection():
    """
    从 Maya 当前选中获取材质节点列表。

    逻辑:
      - 选中材质节点 → 直接返回材质类型节点名列表
      - 选中物体 → 获取其关联的 shadingEngine → 材质节点
      - 无选中 → 返回空列表

    Returns:
        list[str]: 材质节点名列表（去重）
    """
    if not _IN_MAYA:
        return []

    selection = cmds.ls(selection=True, long=False)
    if not selection:
        return []

    # 常见材质节点类型
    _MATERIAL_TYPES = {
        'aiStandardSurface', 'standardSurface', 'lambert', 'blinn',
        'phong', 'openPBRSurface', 'pxrSurface', 'aiHair', 'aiSkin',
        'aiVolume', 'VRayMtl', 'RedshiftMaterial',
    }

    # 先判断选中项是否直接就是材质节点
    material_nodes = []
    non_material = []

    for item in selection:
        try:
            node_type = cmds.nodeType(item)
            if node_type in _MATERIAL_TYPES:
                material_nodes.append(item)
            else:
                non_material.append(item)
        except Exception:
            non_material.append(item)

    # 如果全部都是材质节点，直接返回
    if material_nodes and not non_material:
        return list(dict.fromkeys(material_nodes))  # 去重保持顺序

    # 如果有物体，提取关联的材质
    for obj in non_material:
        try:
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=False) or [obj]
            for shape in shapes:
                ses = cmds.listConnections(shape, type='shadingEngine') or []
                for se in ses:
                    if se == 'initialShadingGroup':
                        continue
                    mats = cmds.listConnections(f"{se}.surfaceShader") or []
                    material_nodes.extend(mats)
        except Exception:
            continue

    return list(dict.fromkeys(material_nodes))


def get_first_material_for_object(obj):
    """
    返回指定 DAG 物体的第一个非 initialShadingGroup 材质节点名。

    Args:
        obj: Maya 物体名称

    Returns:
        str: 材质节点名；如果无材质或出错则返回空字符串
    """
    if not _IN_MAYA:
        return ""
    try:
        shapes = cmds.listRelatives(obj, shapes=True, fullPath=False) or [obj]
        for shape in shapes:
            ses = cmds.listConnections(shape, type='shadingEngine') or []
            for se in ses:
                if se == 'initialShadingGroup':
                    continue
                mats = cmds.listConnections(f"{se}.surfaceShader") or []
                if mats:
                    return mats[0]
    except Exception:
        pass
    return ""
