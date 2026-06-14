"""
.zlight — 渲染器无关的灯光资产格式

类似 PBR 贴图可创建不同渲染器材质，.zlight 描述灯光的物理属性，
导入时根据当前渲染器自动创建对应的灯光节点。

支持类型: area / point / spot / directional / dome / disk / cylinder
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

try:
    import maya.cmds as cmds
    _IN_MAYA = True
except ImportError:
    _IN_MAYA = False


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class LightTransform:
    translate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotate:    List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale:     List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])


@dataclass
class LightData:
    """单个灯光的通用数据模型"""
    name: str = "light"
    light_type: str = "area"  # area | point | spot | directional | dome | disk | cylinder

    # ── 通用物理属性 ──
    color:       List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    intensity:   float = 1.0
    exposure:    float = 0.0
    temperature: float = 6500.0      # 0 = 不使用色温
    normalize:   bool = True
    visible:     bool = False

    # ── 类型特有属性 ──
    cone_angle:     float = 45.0     # spot 光锥角度
    penumbra_angle: float = 0.0      # spot 半影角度
    dropoff:        float = 0.0       # spot 衰减

    angular_diameter: float = 0.53   # directional 角直径（度）

    hdr_path: str = ""               # dome 的 HDR 贴图路径

    # ── 连接的贴图/IES/光域网 ──
    # {"color": "C:/tex/hdr.hdr", "iesProfile": "C:/ies/light.ies", ...}
    connected_files: Dict[str, str] = field(default_factory=dict)

    # ── 连接节点信息（含程序纹理等非文件节点）──
    # {"color": {"node_type": "checker", "connected": true}}
    connections: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ── 上游节点网络（ZMETAL 格式完整序列化）──
    # {"node_name": {"node_type": "...", "attrs": {...}}}
    node_network: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ── 变换 ──
    transform: LightTransform = field(default_factory=LightTransform)


# ═══════════════════════════════════════════════════════════════
# 渲染器 → 灯光类型映射
# ═══════════════════════════════════════════════════════════════

# 导出：Maya 灯光 nodeType → 通用类型
_MAYA_TYPE_TO_LIGHT_TYPE: Dict[str, str] = {
    # Arnold
    "aiAreaLight":        "area",
    "aiSkyDomeLight":     "dome",
    "aiPhotometricLight": "point",
    "aiLightBlocker":     None,
    # Maya 原生
    "areaLight":          "area",
    "pointLight":         "point",
    "spotLight":          "spot",
    "directionalLight":   "directional",
    "ambientLight":       None,
    "volumeLight":        None,
    # V-Ray
    "VRayLightRectShape":    "area",
    "VRayLightDomeShape":    "dome",
    "VRayLightSphereShape":  "point",
    "VRayLightIESShape":     "point",
    "VRayLightMeshShape":    "area",
    "VRayLightDiscShape":    "area",
    "VRayLightSpotShape":    "spot",
    "VRayLightDistantShape": "directional",
    "VRaySunShape":          "directional",
    "VRaySunTarget":         None,
    # Redshift
    "RedshiftPhysicalLight":    "area",
    "RedshiftDomeLight":        "dome",
    "RedshiftIESLight":         "point",
    "RedshiftPortalLight":      None,
    "RedshiftSunAndSky":        "directional",
    "RedshiftNightSky":         None,
}

# 导入：通用类型 → 当前渲染器灯光节点类型
_RENDERER_LIGHT_MAP: Dict[str, Dict[str, str]] = {
    "arnold": {
        "area":        "aiAreaLight",
        "point":       "aiPhotometricLight",
        "spot":        "aiPhotometricLight",  # Arnold 无原生 spot→用 point
        "directional": "aiSkyDomeLight",       # Arnold 无原生平行光→用 dome 近似
        "dome":        "aiSkyDomeLight",
        "disk":        "aiAreaLight",
        "cylinder":    "aiAreaLight",
    },
    "vray": {
        "area":        "VRayLightRectShape",
        "point":       "VRayLightSphereShape",
        "spot":        "VRayLightSpotShape",
        "directional": "VRayLightDistantShape",
        "dome":        "VRayLightDomeShape",
        "disk":        "VRayLightDiscShape",
        "cylinder":    "VRayLightRectShape",
    },
    "redshift": {
        "area":        "RedshiftPhysicalLight",
        "point":       "RedshiftIESLight",
        "spot":        "RedshiftPhysicalLight",     # Redshift 无原生 spot→用 Physical
        "directional": "RedshiftSunAndSky",
        "dome":        "RedshiftDomeLight",
        "disk":        "RedshiftPhysicalLight",
        "cylinder":    "RedshiftPhysicalLight",
    },
    "maya": {
        "area":        "areaLight",
        "point":       "pointLight",
        "spot":        "spotLight",
        "directional": "directionalLight",
        "dome":        "directionalLight",     # Maya 无原生 dome→用 directional 近似
        "disk":        "areaLight",
        "cylinder":    "areaLight",
    },
}


# ═══════════════════════════════════════════════════════════════
# 属性映射：通用属性 → 各渲染器节点属性名
# ═══════════════════════════════════════════════════════════════

_RENDERER_ATTR_MAP: Dict[str, Dict[str, Any]] = {
    "arnold": {
        "color":        "color",
        "intensity":    "intensity",
        "exposure":     "exposure",
        "temperature":  "colorTemperature",
        "normalize":    "normalize",
        "visible":      "lightVisible",
        "cone_angle":   None,        # Arnold Photometric 无 cone angle
        "penumbra_angle": None,
        "dropoff":      None,
        "angular_diameter": None,
        "hdr_path":     "color",     # aiSkyDomeLight.color 连 file 节点
    },
    "vray": {
        "color":        ("lightColor", "srgb"),
        "intensity":    "intensityMult",
        "exposure":     None,
        "temperature":  None,
        "normalize":    None,
        "visible":      "lightVisible",
        "cone_angle":   "coneAngle",
        "penumbra_angle": "penumbraAngle",
        "dropoff":      "dropOff",
        "angular_diameter": None,
        "hdr_path":     "domeTex",
    },
    "redshift": {
        "color":        "color",
        "intensity":    "intensity",
        "exposure":     "exposure",
        "temperature":  None,
        "normalize":    "areaNormalize",
        "visible":      "visible",
        "cone_angle":   None,
        "penumbra_angle": None,
        "dropoff":      None,
        "angular_diameter": None,
        "hdr_path":     "tex0",
    },
    "maya": {
        "color":        ("color", "srgb"),
        "intensity":    "intensity",
        "exposure":     None,
        "temperature":  None,
        "normalize":    None,
        "visible":      "lightVisible",
        "cone_angle":   "coneAngle",
        "penumbra_angle": "penumbraAngle",
        "dropoff":      "dropoff",
        "angular_diameter": None,
        "hdr_path":     None,
    },
}


def _scan_connected_file_nodes(shape_node: str, data: LightData):
    """扫描灯光 shape 上所有入连接，记录文件路径和节点信息。

    使用 listConnections(connections=True, plugs=True) 直接获取所有
    入连接的 (src_plug, dest_plug) 对，避免 listAttr 过滤器遗漏属性。

    Args:
        shape_node: Maya 灯光 shape 完整路径
        data: 要填充的 LightData
    """
    if not _IN_MAYA:
        return
    try:
        # 获取所有入连接的源 plugs（仅 plug 名，不配对）
        src_plugs = cmds.listConnections(shape_node, source=True, destination=False,
                                          plugs=True) or []
        # 如: ["file1.outColor", "checker1.outAlpha"]
        for src_plug in src_plugs:
            if '.' not in src_plug:
                continue
            src_node, src_attr = src_plug.split('.', 1)

            # 反向查：这个源 plug 连到了 shape_node 的哪个属性
            dest_plugs = cmds.listConnections(src_plug, source=False, destination=True,
                                               plugs=True) or []
            for dest_plug in dest_plugs:
                # 检查 dest_plug 属于 shape_node
                if not dest_plug.startswith(shape_node + '.') and not dest_plug.startswith(shape_node.split('|')[-1] + '.'):
                    continue
                if '.' not in dest_plug:
                    continue
                dest_attr = dest_plug.rsplit('.', 1)[-1]

                if not cmds.objExists(src_node):
                    continue
                ntype = cmds.nodeType(src_node)

                # 记录所有连接（包含源节点和源属性用于导入时重建）
                data.connections[dest_attr] = {
                    "node_type": ntype, "connected": True,
                    "source_node": src_node, "source_attr": src_attr}

                # ── 尝试提取文件路径 ──
                file_path = _extract_file_path(src_node, ntype)
                if file_path and os.path.isfile(file_path):
                    data.connected_files[dest_attr] = file_path
                    print(f"[LightIO] 发现连入文件: {dest_attr} ← {ntype} → {file_path}")
                else:
                    print(f"[LightIO] 发现连接: {dest_attr} ← {ntype}（非文件节点）")
    except Exception as e:
        print(f"[LightIO] 扫描连接失败: {e}")


def get_connected_texture_files(shape_node: str) -> List[str]:
    """获取灯光 shape 连接的所有贴图文件路径（用于导出时收集贴图到 zasset）。

    返回文件路径列表。
    """
    paths = set()
    if not _IN_MAYA:
        return []
    try:
        src_plugs = cmds.listConnections(shape_node, source=True, destination=False,
                                          plugs=True) or []
        for src_plug in src_plugs:
            if '.' not in src_plug:
                continue
            src_node, src_attr = src_plug.split('.', 1)
            if not cmds.objExists(src_node):
                continue
            ntype = cmds.nodeType(src_node)
            file_path = _extract_file_path(src_node, ntype)
            if file_path and os.path.isfile(file_path):
                paths.add(file_path)
    except Exception:
        pass
    return list(paths)


def _extract_file_path(node: str, ntype: str) -> str:
    """从各类节点中提取文件路径。

    支持: file, aiImage, place2dTexture（UV）, 以及 IES 节点
    """
    # file 节点
    if ntype == "file":
        try:
            return cmds.getAttr(f"{node}.fileTextureName") or ""
        except Exception:
            pass

    # Arnold aiImage
    if ntype == "aiImage":
        try:
            return cmds.getAttr(f"{node}.filename") or ""
        except Exception:
            pass

    # IES 节点
    if "IES" in ntype.upper() or "PHOTOMETRIC" in ntype.upper():
        for ies_attr in ("iesFile", "profile", "aiFilename"):
            try:
                path = cmds.getAttr(f"{node}.{ies_attr}")
                if path and isinstance(path, str):
                    return path
            except Exception:
                pass

    return ""


# ═══════════════════════════════════════════════════════════════
# 导出 — Maya → .zlight JSON
# ═══════════════════════════════════════════════════════════════

def export_light_from_maya(shape_node: str) -> Optional[LightData]:
    """从 Maya 灯光 shape 节点提取通用光参数。

    Args:
        shape_node: Maya 灯光 shape 节点完整路径

    Returns:
        LightData 或 None（无效类型/导出失败）
    """
    if not _IN_MAYA or not cmds.objExists(shape_node):
        return None

    ntype = cmds.nodeType(shape_node)
    light_type = _MAYA_TYPE_TO_LIGHT_TYPE.get(ntype)
    if light_type is None:
        print(f"[LightIO] 不支持的灯光类型: {ntype}")
        return None

    data = LightData(
        name=shape_node,
        light_type=light_type,
    )

    # ── 变换（从 transform 父级读取） ──
    parents = cmds.listRelatives(shape_node, parent=True, fullPath=True) or []
    if parents:
        data.name = parents[0]
        try:
            data.transform.translate = list(
                cmds.getAttr(f"{parents[0]}.translate")[0])
            data.transform.rotate = list(
                cmds.getAttr(f"{parents[0]}.rotate")[0])
            data.transform.scale = list(
                cmds.getAttr(f"{parents[0]}.scale")[0])
        except Exception:
            pass

    # ── 通用属性提取 ──
    # 对每个通用参数，遍历所有渲染器的属性名，取第一个存在的
    def _read_attr_val(attr_name):
        """从 shape_node 读取 attr_name，返回 (exists, value)"""
        full = f"{shape_node}.{attr_name}"
        if not cmds.objExists(full):
            return False, None
        try:
            val = cmds.getAttr(full)
            # 处理嵌套容器：((r,g,b),) → (r,g,b) 或 [[r,g,b]] → [r,g,b]
            while isinstance(val, (list, tuple)) and len(val) == 1:
                val = val[0]
            return True, val
        except Exception:
            return False, None

    # 获取所有渲染器对该属性的候选属性名
    def _get_attr_candidates(ukey):
        """返回 [(r_attr, is_tuple, tuple_data), ...]"""
        candidates = []
        for rn in ("arnold", "vray", "redshift", "maya"):
            amap = _RENDERER_ATTR_MAP.get(rn, {})
            spec = amap.get(ukey)
            if spec is None:
                continue
            if isinstance(spec, tuple):
                candidates.append((spec[0], True, spec))
            else:
                candidates.append((spec, False, None))
        return candidates

    # 辅助：尝试所有候选并设置值
    def _try_set_data(ukey, setter):
        for r_attr, is_tuple, _ in _get_attr_candidates(ukey):
            exists, val = _read_attr_val(r_attr)
            if exists and val is not None:
                setter(val)
                return True
        return False

    # ── 逐一提取每个通用参数 ──
    _try_set_data("color", lambda v: setattr(data, "color",
        [float(x) for x in v[:3]] if isinstance(v, (list, tuple)) and len(v) >= 3 else [float(v)]*3))
    _try_set_data("intensity", lambda v: setattr(data, "intensity", float(v)))
    _try_set_data("exposure", lambda v: setattr(data, "exposure", float(v)))
    _try_set_data("temperature", lambda v: setattr(data, "temperature", float(v) if float(v) > 0 else 6500.0))
    _try_set_data("normalize", lambda v: setattr(data, "normalize", bool(v)))
    _try_set_data("visible", lambda v: setattr(data, "visible", bool(v)))

    # 类型特有属性
    if light_type == "spot":
        _try_set_data("cone_angle", lambda v: setattr(data, "cone_angle", float(v)))
        _try_set_data("penumbra_angle", lambda v: setattr(data, "penumbra_angle", float(v)))
        _try_set_data("dropoff", lambda v: setattr(data, "dropoff", float(v)))
    if light_type == "directional":
        _try_set_data("angular_diameter", lambda v: setattr(data, "angular_diameter", float(v)))
    if light_type == "dome":
        _try_set_data("hdr_path", lambda v: setattr(data, "hdr_path", str(v) if isinstance(v, str) and v.strip() else ""))

    # ── 额外属性：记录尺寸/形状信息（area light 的宽高）──
    if light_type == "area":
        for attr in ("width", "sizeX", "u_size"):
            exists, val = _read_attr_val(attr)
            if exists and val is not None:
                data.transform.scale[0] = float(val)
                break
        for attr in ("height", "sizeY", "v_size"):
            exists, val = _read_attr_val(attr)
            if exists and val is not None:
                data.transform.scale[1] = float(val)
                break

    # ── 收集连接的文件贴图/IES/光域网 ──
    _scan_connected_file_nodes(shape_node, data)

    # ── 序列化完整上游节点网络（ZMETAL 方式）──
    try:
        from squirrel_asset_manager.integration.zjg_exporter import _serialize_node
        net = {}
        # 直接用 plugs=True 获取所有源 plug（不依赖 listAttr 过滤器）
        src_plugs = cmds.listConnections(shape_node, source=True, destination=False, plugs=True) or []
        for src_plug in src_plugs:
            if '.' in src_plug:
                src_node, src_attr = src_plug.split('.', 1)
                _serialize_node(src_node, net)
        if net:
            data.node_network = net
            print(f"[LightIO] 序列化上游节点网络: {len(net)} 个节点")
    except Exception as e:
        print(f"[LightIO] 节点网络序列化失败: {e}")

    return data


def export_lights_to_json(shape_nodes: List[str], filepath: str) -> bool:
    """导出多个灯光 shape 节点到 .zlight JSON 文件。

    Returns:
        bool 是否成功
    """
    lights = []
    warnings = []
    for shp in shape_nodes:
        ld = export_light_from_maya(shp)
        if ld:
            lights.append(ld)
        else:
            warnings.append(f"  跳过: {shp} (不支持的灯光类型)")

    if warnings and not lights:
        print(f"[LightIO] 所有灯光均不支持: {', '.join(shape_nodes)}")
        return False

    if warnings:
        print(f"[LightIO] 部分灯光跳过:\n" + "\n".join(warnings))

    # 构建 JSON
    doc = {
        "version": "1.0",
        "description": "渲染器无关灯光资产",
        "software": _get_software(),
        "lights": [_lightdata_to_dict(ld) for ld in lights],
    }

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    print(f"[LightIO] 导出成功: {filepath} ({len(lights)} 个灯光)")
    return True


def _lightdata_to_dict(ld: LightData) -> dict:
    """LightData → JSON 字典（清理空值/默认值）"""
    d = asdict(ld)
    d["transform"] = {
        "translate": [round(float(v), 6) for v in ld.transform.translate],
        "rotate":    [round(float(v), 6) for v in ld.transform.rotate],
        "scale":     [round(float(v), 6) for v in ld.transform.scale],
    }
    if isinstance(ld.color, (list, tuple)):
        d["color"] = [round(float(v), 6) for v in ld.color]
    if ld.connected_files:
        d["connected_files"] = ld.connected_files
    if ld.connections:
        d["connections"] = ld.connections
    if ld.node_network:
        d["node_network"] = ld.node_network
    return d


# ═══════════════════════════════════════════════════════════════
# 导入 — .zlight JSON → Maya 灯光节点
# ═══════════════════════════════════════════════════════════════

def import_lights_from_json(filepath: str, renderer: str = "") -> Tuple[int, List[str]]:
    """从 .zlight JSON 创建灯光节点。

    Args:
        filepath: .zlight 文件路径
        renderer: 指定渲染器（"" = 自动检测当前渲染器）

    Returns:
        (创建数量, 创建的灯光 transform 名称列表)
    """
    if not _IN_MAYA:
        return 0, []

    with open(filepath, "r", encoding="utf-8") as f:
        doc = json.load(f)

    lights_data = doc.get("lights", [])
    if not renderer:
        renderer = _detect_renderer()
    created = []

    for light_dict in lights_data:
        ld = _dict_to_lightdata(light_dict)
        try:
            xform = _create_light(ld, renderer)
            if xform:
                created.append(xform)
                print(f"[LightIO] 创建灯光: {xform} (type={ld.light_type}, renderer={renderer})")
        except Exception as e:
            print(f"[LightIO] 灯光创建失败 [{ld.name}]: {e}")

    print(f"[LightIO] 导入完成: {filepath} — {len(created)}/{len(lights_data)} 个灯光")
    return len(created), created


def _dict_to_lightdata(d: dict) -> LightData:
    """JSON 字典 → LightData"""
    t = d.get("transform", {})
    return LightData(
        name=d.get("name", "light"),
        light_type=d.get("light_type", "area"),
        color=d.get("color", [1.0, 1.0, 1.0]),
        intensity=d.get("intensity", 1.0),
        exposure=d.get("exposure", 0.0),
        temperature=d.get("temperature", 6500.0),
        normalize=d.get("normalize", True),
        visible=d.get("visible", False),
        cone_angle=d.get("cone_angle", 45.0),
        penumbra_angle=d.get("penumbra_angle", 0.0),
        dropoff=d.get("dropoff", 0.0),
        angular_diameter=d.get("angular_diameter", 0.53),
        hdr_path=d.get("hdr_path", ""),
        connected_files=d.get("connected_files", {}),
        connections=d.get("connections", {}),
        node_network=d.get("node_network", {}),
        transform=LightTransform(
            translate=t.get("translate", [0.0, 0.0, 0.0]),
            rotate=t.get("rotate", [0.0, 0.0, 0.0]),
            scale=t.get("scale", [1.0, 1.0, 1.0]),
        ),
    )


def _create_light(ld: LightData, renderer: str) -> Optional[str]:
    """根据 LightData + 渲染器在 Maya 中创建灯光节点。

    Returns:
        transform 节点名，失败返回 None
    """
    node_type = _RENDERER_LIGHT_MAP.get(renderer, {}).get(ld.light_type)
    if not node_type:
        print(f"[LightIO] 渲染器 {renderer} 不支持灯光类型 {ld.light_type}")
        return None

    amap = _RENDERER_ATTR_MAP.get(renderer, {})
    if not amap:
        print(f"[LightIO] 渲染器 {renderer} 无属性映射")
        return None

    # ── 创建灯光 ──
    try:
        xform = cmds.shadingNode(node_type, asLight=True)
    except Exception as e:
        # shadingNode 失败 → 有些渲染器要完整 shape 名
        print(f"[LightIO] shadingNode({node_type}) 失败: {e}")
        return None

    shapes = cmds.listRelatives(xform, shapes=True) or []
    shape_node = shapes[0] if shapes else xform

    # ── 设置变换 ──
    try:
        cmds.setAttr(f"{xform}.translate", *ld.transform.translate)
        cmds.setAttr(f"{xform}.rotate", *ld.transform.rotate)
        cmds.setAttr(f"{xform}.scale", *ld.transform.scale)
    except Exception:
        pass

    # ── 设置通用属性 ──
    def _set(attr_name, value):
        full = f"{shape_node}.{attr_name}"
        if not cmds.objExists(full):
            return
        try:
            if isinstance(value, list):
                cmds.setAttr(full, *value, type="double3")
            elif isinstance(value, bool):
                cmds.setAttr(full, value)
            elif isinstance(value, float):
                cmds.setAttr(full, value)
        except Exception:
            pass

    # color
    color_spec = amap.get("color")
    if color_spec:
        if isinstance(color_spec, tuple):
            attr, _ = color_spec
            _set(attr, ld.color)
        else:
            _set(color_spec, ld.color)

    # intensity
    if amap.get("intensity"):
        _set(amap["intensity"], ld.intensity)

    # exposure
    if amap.get("exposure"):
        _set(amap["exposure"], ld.exposure)

    # temperature (only if > 0)
    if amap.get("temperature") and ld.temperature > 0:
        _set(amap["temperature"], ld.temperature)

    # normalize
    if amap.get("normalize"):
        _set(amap["normalize"], ld.normalize)

    # visible
    if amap.get("visible"):
        _set(amap["visible"], ld.visible)

    # cone angle (spot only)
    if ld.light_type == "spot" and amap.get("cone_angle"):
        # Maya 原生 spot light 接受 deg，其他渲染器也是 deg
        _set(amap["cone_angle"], ld.cone_angle)

    # penumbra (spot only)
    if ld.light_type == "spot" and amap.get("penumbra_angle"):
        _set(amap["penumbra_angle"], ld.penumbra_angle)

    # dropoff (spot only)
    if ld.light_type == "spot" and amap.get("dropoff"):
        _set(amap["dropoff"], ld.dropoff)

    # angular diameter (directional only)
    if ld.light_type == "directional" and amap.get("angular_diameter"):
        _set(amap["angular_diameter"], ld.angular_diameter)

    # HDR path (dome only)
    if ld.light_type == "dome" and ld.hdr_path and amap.get("hdr_path"):
        _set_hdr(shape_node, ld.hdr_path, amap["hdr_path"], renderer)

    # ── 恢复连接的贴图/IES 文件 ──
    # 仅在没有完整 node_network 时才用 connected_files 简单恢复
    if ld.connected_files and not ld.node_network:
        _restore_connected_files(shape_node, ld.connected_files, renderer)

    # ── 重建上游节点网络（ZMETAL 方式）──
    if ld.node_network:
        _restore_node_network(shape_node, ld.node_network, ld.connections)

    return xform


def _set_hdr(shape_node: str, hdr_path: str, attr_name: str, renderer: str):
    """为 dome 灯光连接 HDR 贴图 file 节点"""
    if not os.path.isfile(hdr_path):
        print(f"[LightIO] HDR 文件不存在: {hdr_path}")
        return

    try:
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(f"{file_node}.fileTextureName", hdr_path, type="string")
        cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)

        # 尝试设置色彩空间为 Raw
        try:
            cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")
        except Exception:
            pass

        full_attr = f"{shape_node}.{attr_name}"
        if cmds.objExists(full_attr):
            cmds.connectAttr(f"{file_node}.outColor", full_attr, force=True)
            print(f"[LightIO] HDR 已连接: {hdr_path} → {full_attr}")
    except Exception as e:
        print(f"[LightIO] HDR 连接失败: {e}")


def _restore_node_network(shape_node: str, network: Dict[str, Dict],
                          connections: Dict[str, Dict] = None):
    """从 ZMETAL 格式节点数据重建上游节点网络并连接到灯光。

    Args:
        shape_node: 目标灯光 shape 节点
        network: {node_name: {node_type, attrs}} 字典
        connections: 灯光属性连接信息 {dest_attr: {source_node, source_attr, node_type}}
    """
    if not network:
        return
    name_map = {}
    import re

    # ── 第一步：创建所有节点 ──
    for old_name, info in network.items():
        ntype = info.get("node_type", "")
        if not ntype:
            continue
        try:
            new_node = cmds.createNode(ntype, skipSelect=True)
            name_map[old_name] = new_node
            # 注册到对应列表（材质编辑器可见）
            try:
                if cmds.getClassification(ntype, satisfies="shader"):
                    cmds.connectAttr(f"{new_node}.message", "defaultShaderList1.s", nextAvailable=True)
                elif cmds.getClassification(ntype, satisfies="texture"):
                    cmds.connectAttr(f"{new_node}.message", "defaultTextureList1.tx", nextAvailable=True)
                else:
                    cmds.connectAttr(f"{new_node}.message", "defaultRenderUtilityList1.u", nextAvailable=True)
            except Exception:
                pass
        except Exception as e:
            print(f"[LightIO] 网络节点创建失败 [{old_name} ({ntype})]: {e}")
            name_map[old_name] = None

    # ── 第二步：设置属性值 ──
    for old_name, info in network.items():
        new_node = name_map.get(old_name)
        if not new_node:
            continue
        attrs = info.get("attrs", {})
        for attr_key, attr_data in attrs.items():
            if attr_data.get("type") != "value":
                continue
            val = attr_data.get("value")
            if val is None:
                continue
            full_attr = f"{new_node}.{attr_key}"
            if not cmds.objExists(full_attr):
                continue
            try:
                if isinstance(val, list):
                    if len(val) == 3 and full_attr.endswith(('.c', '.ct', '.oc', '.outColor', '.sc')):
                        cmds.setAttr(full_attr, *val, type="double3")
                    elif len(val) == 2:
                        cmds.setAttr(full_attr, val[0], val[1])
                    else:
                        cmds.setAttr(full_attr, *val)
                elif isinstance(val, bool):
                    cmds.setAttr(full_attr, val)
                elif isinstance(val, float) or isinstance(val, int):
                    cmds.setAttr(full_attr, val)
                elif isinstance(val, str) and val:
                    cmds.setAttr(full_attr, val, type="string")
            except Exception:
                pass

    # ── 第三步：恢复连接 ──
    for old_name, info in network.items():
        new_node = name_map.get(old_name)
        if not new_node:
            continue
        attrs = info.get("attrs", {})
        for attr_key, attr_data in attrs.items():
            if attr_data.get("type") != "connection":
                continue
            src_old = attr_data.get("source_node", "")
            src_attr = attr_data.get("source_attr", "")
            # 源节点可能在 network 中，也可能是灯光 shape
            src_new = name_map.get(src_old, src_old)
            if not src_new or not cmds.objExists(src_new):
                continue
            full_dest = f"{new_node}.{attr_key}"
            full_src = f"{src_new}.{src_attr}"
            if cmds.objExists(full_dest) and cmds.objExists(full_src):
                try:
                    cmds.connectAttr(full_src, full_dest, force=True)
                except Exception:
                    pass

    # ── 第四步：将上游节点连回灯光 shape ──
    if connections:
        # 获取 transform 父节点（某些属性在 transform 上）
        xform_node = shape_node
        try:
            parents = cmds.listRelatives(shape_node, parent=True, fullPath=True) or []
            if parents:
                xform_node = parents[0]
        except Exception:
            pass

        # 构建 dest 候选节点列表 [shape, transform]
        dest_nodes = [shape_node]
        if xform_node != shape_node:
            dest_nodes.append(xform_node)

        for dest_attr, conn_info in connections.items():
            old_src = conn_info.get("source_node", "")
            src_attr = conn_info.get("source_attr", "")
            if not old_src or not src_attr:
                print(f"[LightIO] connections 缺少 source_node/source_attr: {conn_info}")
                continue
            src_new = name_map.get(old_src, old_src)
            if not src_new or not cmds.objExists(src_new):
                print(f"[LightIO] 源节点不存在: {old_src}")
                continue

            full_src = f"{src_new}.{src_attr}"
            if not cmds.objExists(full_src):
                print(f"[LightIO] 源 plug 不存在: {full_src}")
                continue

            connected = False
            for dest_node in dest_nodes:
                full_dest = f"{dest_node}.{dest_attr}"
                # 直接尝试连接，不检查 objExists（属性可能被别名化）
                try:
                    cmds.connectAttr(full_src, full_dest, force=True)
                    print(f"[LightIO] 连回灯光: {full_src} → {full_dest}")
                    connected = True
                    break
                except Exception:
                    pass

            if not connected:
                print(f"[LightIO] 无法连回灯光: {full_src} → {shape_node}.{dest_attr}")

    print(f"[LightIO] 节点网络重建完成: {len(name_map)} 个节点")


def _restore_connected_files(shape_node: str, files: Dict[str, str], renderer: str):
    """恢复灯光上连接的贴图和 IES 文件。

    Args:
        shape_node: 灯光 shape 节点
        files: {attr_name: file_path} 字典
    """
    for attr_key, file_path in files.items():
        if not os.path.isfile(file_path):
            print(f"[LightIO] 文件不存在，跳过: {file_path}")
            continue

        # 解析属性名（iesProfile:xxx → 找 IES 属性）
        target_attr = attr_key
        is_ies = False
        if attr_key.startswith("iesProfile:"):
            target_attr = attr_key.split(":", 1)[1]
            is_ies = True

        try:
            file_node = cmds.shadingNode("file", asTexture=True)
            cmds.setAttr(f"{file_node}.fileTextureName", file_path, type="string")
            cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
            # 贴图设为 Raw，IES 也设为 Raw
            try:
                cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")
            except Exception:
                pass

            full_attr = f"{shape_node}.{target_attr}"
            if cmds.objExists(full_attr):
                cmds.connectAttr(f"{file_node}.outColor", full_attr, force=True)
                kind = "IES" if is_ies else "贴图"
                print(f"[LightIO] {kind}已连接: {file_path} → {full_attr}")
            else:
                print(f"[LightIO] 属性不存在: {full_attr}")
        except Exception as e:
            print(f"[LightIO] 文件连接失败 [{target_attr}]: {e}")


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _detect_renderer() -> str:
    """检测当前 Maya 激活的渲染器"""
    if not _IN_MAYA:
        return "maya"
    try:
        r = cmds.getAttr("defaultRenderGlobals.currentRenderer")
        r_lower = r.lower() if r else ""
        if "arnold" in r_lower:
            return "arnold"
        if "vray" in r_lower or "v-ray" in r_lower:
            return "vray"
        if "redshift" in r_lower:
            return "redshift"
        return "maya"
    except Exception:
        return "maya"


def _get_software() -> str:
    """获取当前软件标识"""
    if not _IN_MAYA:
        return "unknown"
    try:
        ver = cmds.about(version=True)
        return f"Maya {ver}"
    except Exception:
        return "Maya"
