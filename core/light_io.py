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
    "RedshiftDomeLight":    "dome",
    "RedshiftAreaLight":    "area",
    "RedshiftPointLight":   "point",
    "RedshiftSpotLight":    "spot",
    "RedshiftDirectionalLight": "directional",
    "RedshiftEnvironmentLight": "dome",
    "RedshiftIESLight":     "point",
    "RedshiftPortalLight":  None,
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
        "area":        "RedshiftAreaLight",
        "point":       "RedshiftPointLight",
        "spot":        "RedshiftSpotLight",
        "directional": "RedshiftDirectionalLight",
        "dome":        "RedshiftDomeLight",
        "disk":        "RedshiftAreaLight",
        "cylinder":    "RedshiftAreaLight",
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
        "color":        ("lightColor", "srgb"),
        "intensity":    "intensity",
        "exposure":     "exposure",
        "temperature":  "colorTemperature",
        "normalize":    "areaNormalize",
        "visible":      "visible",
        "cone_angle":   "coneAngle",
        "penumbra_angle": "coneFalloff",
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
    # 使用当前渲染器的属性名尝试读取，回退到常见属性名
    renderer = _detect_renderer()
    rmap = _RENDERER_ATTR_MAP.get(renderer, _RENDERER_ATTR_MAP["maya"])

    def _read(attr_name):
        """尝试从 shape_node 读取 attr_name，不存在返回 None"""
        full = f"{shape_node}.{attr_name}"
        if cmds.objExists(full):
            try:
                return cmds.getAttr(full)
            except Exception:
                pass
        return None

    def _read_color(attr_name):
        """读取 RGB 颜色，处理 srgb/linear 差异"""
        full = f"{shape_node}.{attr_name}"
        if not cmds.objExists(full):
            return None
        try:
            val = cmds.getAttr(full)
            if isinstance(val, list) and len(val) >= 3 and isinstance(val[0], list):
                val = val[0]
            return list(val)[:3] if isinstance(val, list) else [val, val, val]
        except Exception:
            return None

    # 尝试多种属性名读取通用参数
    for r_name in ("arnold", "vray", "redshift", "maya"):
        amap = _RENDERER_ATTR_MAP.get(r_name, {})
        for ukey, attr_spec in amap.items():
            if attr_spec is None:
                continue
            if isinstance(attr_spec, tuple):
                r_attr, _ = attr_spec
            else:
                r_attr = attr_spec

            full = f"{shape_node}.{r_attr}"
            if not cmds.objExists(full):
                continue

            try:
                val = cmds.getAttr(full)
            except Exception:
                continue

            # 取第一组（处理 list-of-list）
            if isinstance(val, list) and len(val) >= 3 and isinstance(val[0], list):
                val = val[0]

            if ukey == "color":
                if isinstance(val, list) and len(val) >= 3:
                    data.color = list(val)[:3]
            elif ukey == "intensity" and val is not None:
                data.intensity = float(val)
            elif ukey == "exposure" and val is not None:
                data.exposure = float(val)
            elif ukey == "temperature" and val is not None and float(val) > 0:
                data.temperature = float(val)
            elif ukey == "normalize" and val is not None:
                data.normalize = bool(val)
            elif ukey == "visible" and val is not None:
                data.visible = bool(val)
            elif ukey == "cone_angle" and light_type == "spot" and val is not None:
                data.cone_angle = float(val)
            elif ukey == "penumbra_angle" and light_type == "spot" and val is not None:
                data.penumbra_angle = float(val)
            elif ukey == "dropoff" and light_type == "spot" and val is not None:
                data.dropoff = float(val)
            elif ukey == "angular_diameter" and light_type == "directional" and val is not None:
                data.angular_diameter = float(val)
            elif ukey == "hdr_path" and light_type == "dome" and val is not None:
                if isinstance(val, str) and val.strip():
                    data.hdr_path = val

            # 已找到值就不再尝试其他渲染器的同名属性
            break

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
        "translate": [round(v, 6) for v in ld.transform.translate],
        "rotate":    [round(v, 6) for v in ld.transform.rotate],
        "scale":     [round(v, 6) for v in ld.transform.scale],
    }
    # 移除零值/none 避免 JSON 臃肿
    d["color"] = [round(v, 6) for v in ld.color]
    return d


# ═══════════════════════════════════════════════════════════════
# 导入 — .zlight JSON → Maya 灯光节点
# ═══════════════════════════════════════════════════════════════

def import_lights_from_json(filepath: str) -> Tuple[int, List[str]]:
    """从 .zlight JSON 创建当前渲染器灯光节点。

    Args:
        filepath: .zlight 文件路径

    Returns:
        (创建数量, 创建的灯光 transform 名称列表)
    """
    if not _IN_MAYA:
        return 0, []

    with open(filepath, "r", encoding="utf-8") as f:
        doc = json.load(f)

    lights_data = doc.get("lights", [])
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
