"""
Maya材质转换工具 - 基于映射关系数据
支持将材质从一种类型转换为另一种类型
适配 Maya 2025+
"""

import os
import sys
import json
from functools import partial

# 获取脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 预设目录：相对于脚本目录的 Assets/material_mapper_presets
PRESET_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "Assets", "material_mapper_presets"))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 尝试导入 PySide6
try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog, QMessageBox, QHeaderView, QInputDialog
    import shiboken6
    PYSIDE_VERSION = 6
    print("使用 PySide6 版本")
except ImportError as e:
    # 如果 PySide6 导入失败，尝试 PySide2
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        from PySide2.QtCore import Qt
        from PySide2.QtWidgets import QFileDialog, QMessageBox, QHeaderView, QInputDialog
        import shiboken2
        PYSIDE_VERSION = 2
        print("使用 PySide2 版本")
    except ImportError:
        raise ImportError("需要 PySide6 或 PySide2")

# 统一shiboken导入
if PYSIDE_VERSION == 6:
    import shiboken6 as shiboken
else:
    import shiboken2 as shiboken

try:
    import maya.cmds as cmds
    import maya.OpenMayaUI as omui
    IN_MAYA = True
except ImportError:
    IN_MAYA = False


_T = None
_help_path = lambda p: p
try:
    # 优先包内相对导入，避免误命中 sys.path 中其他副本（如独立库 release 目录）的顶层 utils 包
    from ..utils.i18n import t as _T, help_path as _hpath
    _help_path = _hpath
except ImportError:
    try:
        from squirrel_asset_manager.utils.i18n import t as _T, help_path as _hpath
        _help_path = _hpath
    except ImportError:
        try:
            from utils.i18n import t as _T, help_path as _hpath
            _help_path = _hpath
        except ImportError:
            _T = None

def t(key, **kwargs):
    return _T(key, **kwargs) if _T is not None else (key.format(**kwargs) if kwargs else key)


# ---- 字体 DPI 适配：以 4K 27 英寸屏（约 163 DPI）为视觉基准 ----
# 不同 DPI 屏幕按比例缩放字号，保证视觉大小一致（4K 正常、2K 偏大的问题）。
# 如基准屏尺寸不是 27 英寸，可调整 REFERENCE_DPI 值。
REFERENCE_DPI = 163.0


def _font_scale():
    """返回当前屏幕相对基准 DPI 的字体缩放系数"""
    try:
        app = QtWidgets.QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        dpi = float(screen.physicalDotsPerInch()) if screen is not None else REFERENCE_DPI
        if dpi <= 0:
            dpi = REFERENCE_DPI
        return max(0.6, min(dpi / REFERENCE_DPI, 1.5))
    except Exception:
        return 1.0


FONT_SCALE = _font_scale()


def _fs(px):
    """按 DPI 缩放字号，最小 8px 保证可读"""
    return max(8, int(px * FONT_SCALE))


def _sc(px):
    """按 DPI 缩放尺寸（按钮 padding / min-height / min-width 等），最小 1px"""
    return max(1, int(px * FONT_SCALE))


def _font_style(text):
    """将样式文本中的 @FONT_nn@（字号）与 @SIZE_nn@（尺寸）占位符按 DPI 缩放"""
    import re

    def _repl(m):
        tag, val = m.group(1), int(m.group(2))
        return str(_fs(val)) if tag == "FONT" else str(_sc(val))

    return re.sub(r'@(FONT|SIZE)_(\d+)@', _repl, text)


def rgb_to_channel(rgb_color, channel='r'):
    """从RGB颜色中提取单个通道

    Args:
        rgb_color: RGB颜色列表 [r, g, b]，每个值范围 0-1
        channel: 要提取的通道，'r'、'g'、'b'或'red'、'green'、'blue'

    Returns:
        float: 单通道值 (0-1)
    """
    if not rgb_color:
        return 0.0
    channel_map = {'r': 0, 'red': 0, 'g': 1, 'green': 1, 'b': 2, 'blue': 2}
    idx = channel_map.get(channel.lower(), 0)
    if idx < len(rgb_color):
        return float(rgb_color[idx])
    return 0.0


def rgb_to_red(rgb_color):
    """从RGB颜色中提取红通道

    Args:
        rgb_color: RGB颜色列表 [r, g, b]，每个值范围 0-1

    Returns:
        float: 红通道值 (0-1)
    """
    return rgb_to_channel(rgb_color, 'r')


def rgb_to_green(rgb_color):
    """从RGB颜色中提取绿通道

    Args:
        rgb_color: RGB颜色列表 [r, g, b]，每个值范围 0-1

    Returns:
        float: 绿通道值 (0-1)
    """
    return rgb_to_channel(rgb_color, 'g')


def rgb_to_blue(rgb_color):
    """从RGB颜色中提取蓝通道

    Args:
        rgb_color: RGB颜色列表 [r, g, b]，每个值范围 0-1

    Returns:
        float: 蓝通道值 (0-1)
    """
    return rgb_to_channel(rgb_color, 'b')


def rgb_to_grayscale(rgb_color):
    """将RGB颜色转换为灰度值

    Args:
        rgb_color: RGB颜色列表 [r, g, b]，每个值范围 0-1

    Returns:
        float: 灰度值 (0-1)
    """
    if not rgb_color or len(rgb_color) < 3:
        return 0.0
    r, g, b = rgb_color[0], rgb_color[1], rgb_color[2]
    return 0.299 * r + 0.587 * g + 0.114 * b


def transparency_to_transmission(transparency):
    """将透明度转换为透射权重

    Maya的transparency是透明度(0=不透明, 1=完全透明)
    PBR的transmissionWeight是透射率(0=不透明, 1=完全透明)

    Args:
        transparency: 透明度值，可以是单个值或RGB列表

    Returns:
        float: 透射权重 (0-1)
    """
    if transparency is None:
        return 0.0
    if isinstance(transparency, (list, tuple)):
        return max(transparency) if transparency else 0.0
    return float(transparency)


def shininess_to_roughness(shininess, glossiness_mode=False):
    """将光泽度/高光锐度转换为粗糙度

    Maya中Phong/Blinn的cosPower或shininess是高光锐度(0-1000或更宽范围)
    PBR的roughness是粗糙度(0-1)

    转换公式: roughness = 1 - (shininess / max_shininess) ^ (1/2)

    Args:
        shininess: 光泽度值
        glossiness_mode: 如果为True，输入是光泽度(1-roughness)，需要反转

    Returns:
        float: 粗糙度值 (0-1)
    """
    if shininess is None:
        return 0.5
    shininess = float(shininess)
    if glossiness_mode:
        shininess = 1.0 - shininess
    shininess = max(0.0, min(1.0, shininess))
    return 1.0 - shininess


def blinn_cosPower_to_roughness(cos_power):
    """将Blinn的cosPower转换为粗糙度

    Blinn的cosPower范围通常是2-1000
    转换公式: roughness = sqrt(2 / (cosPower + 2))

    Args:
        cos_power: Blinn cosPower值

    Returns:
        float: 粗糙度值 (0-1)
    """
    if cos_power is None or cos_power <= 0:
        return 1.0
    cos_power = float(cos_power)
    roughness = (2.0 / (cos_power + 2.0)) ** 0.5
    return max(0.0, min(1.0, roughness))


def phong_shi_to_roughness(shi):
    """将Phong的shininess转换为粗糙度

    Phong的shininess范围通常是1-1000
    转换公式: roughness = sqrt(2 / shi)

    Args:
        shi: Phong shininess值

    Returns:
        float: 粗糙度值 (0-1)
    """
    if shi is None or shi <= 0:
        return 1.0
    shi = float(shi)
    roughness = (2.0 / shi) ** 0.5
    return max(0.0, min(1.0, roughness))


def metalness_from_specular(specular_color, diffuse_color):
    """从镜面反射颜色估算金属度

    金属材质的高光颜色接近自身的diffuse颜色
    非金属材质的高光颜色接近白色

    Args:
        specular_color: 镜面反射颜色 [r, g, b]
        diffuse_color: 漫反射颜色 [r, g, b]

    Returns:
        float: 金属度估算值 (0-1)
    """
    if not specular_color or not diffuse_color:
        return 0.0
    spec_lum = rgb_to_grayscale(specular_color)
    diff_lum = rgb_to_grayscale(diffuse_color)
    if diff_lum < 0.01:
        return 1.0 if spec_lum > 0.1 else 0.0
    ratio = spec_lum / diff_lum
    return max(0.0, min(1.0, (ratio - 1.0) / 4.0))


def diffuse_roughness_to_roughness(diffuse_roughness):
    """将漫反射粗糙度转换为PBR粗糙度

    aiStandardSurface的diffuseRoughness与openPBRSurface的roughness不完全相同
    需要进行非线性转换

    Args:
        diffuse_roughness: 漫反射粗糙度 (0-1)

    Returns:
        float: 粗糙度值 (0-1)
    """
    if diffuse_roughness is None:
        return 0.5
    dr = max(0.0, min(1.0, float(diffuse_roughness)))
    return dr


def specular_to_specular_weight(specular):
    """将镜面反射强度转换为镜面权重

    Args:
        specular: 镜面反射强度值

    Returns:
        float: 镜面权重 (0-1)
    """
    if specular is None:
        return 0.0
    spec = float(specular)
    return max(0.0, min(1.0, spec))


def emission_to_emission_luminance(emission):
    """将自发光颜色/强度转换为发光亮度

    Maya中emission通常是颜色值
    openPBRSurface的emissionLuminance是亮度值(cd/m²)

    常见转换:
    - 对于白色自发光强度1.0，对应约1000 cd/m²

    Args:
        emission: 自发光值，可以是单个强度值或RGB颜色

    Returns:
        float: 发光亮度 (cd/m²)
    """
    if emission is None:
        return 0.0
    if isinstance(emission, (list, tuple)):
        intensity = max(emission) if emission else 0.0
    else:
        intensity = float(emission)
    return intensity * 1000.0


def translucence_to_subsurface(translucence):
    """将半透明度转换为次表面散射权重

    Args:
        translucence: 半透明度值 (0-1)

    Returns:
        float: 次表面散射权重 (0-1)
    """
    if translucence is None:
        return 0.0
    return max(0.0, min(1.0, float(translucence)))


def color_mul_scalar(color, scalar=1.0):
    """颜色乘以标量

    Args:
        color: RGB颜色列表 [r, g, b]（若是标量则原样返回）
        scalar: 标量值，默认 1.0

    Returns:
        list: 变换后的颜色 [r, g, b]；输入非颜色时原样返回
    """
    if not color:
        return [0.0, 0.0, 0.0]
    # 输入不是颜色（标量/数值），无法执行颜色乘法，原样返回
    if isinstance(color, (int, float)):
        return color
    try:
        scalar = float(scalar) if scalar is not None else 1.0
        return [
            max(0.0, min(1.0, color[0] * scalar)),
            max(0.0, min(1.0, color[1] * scalar)),
            max(0.0, min(1.0, color[2] * scalar))
        ]
    except Exception:
        return color


def color_add(color1, color2):
    """颜色相加

    Args:
        color1: RGB颜色列表 [r, g, b]
        color2: RGB颜色列表 [r, g, b]

    Returns:
        list: 相加后的颜色 [r, g, b]
    """
    if not color1:
        color1 = [0.0, 0.0, 0.0]
    if not color2:
        color2 = [0.0, 0.0, 0.0]
    return [
        max(0.0, min(1.0, color1[0] + color2[0])),
        max(0.0, min(1.0, color1[1] + color2[1])),
        max(0.0, min(1.0, color1[2] + color2[2]))
    ]


def color_lerp(color1, color2, t):
    """颜色线性插值

    Args:
        color1: 起始RGB颜色 [r, g, b]
        color2: 结束RGB颜色 [r, g, b]
        t: 插值因子 (0-1)

    Returns:
        list: 插值后的颜色 [r, g, b]
    """
    if not color1:
        color1 = [0.0, 0.0, 0.0]
    if not color2:
        color2 = [0.0, 0.0, 0.0]
    t = max(0.0, min(1.0, float(t) if t is not None else 0.5))
    return [
        color1[0] * (1 - t) + color2[0] * t,
        color1[1] * (1 - t) + color2[1] * t,
        color1[2] * (1 - t) + color2[2] * t
    ]


def ior_to_f0(ior):
    """将折射率(IOR)转换为F0基础反射率

    F0 = ((ior - 1) / (ior + 1))²

    Args:
        ior: 折射率值 (通常 1.0-3.0)

    Returns:
        float: F0基础反射率 (0-1)
    """
    if ior is None or ior <= 0:
        return 0.04
    ior = float(ior)
    f0 = ((ior - 1.0) / (ior + 1.0)) ** 2
    return max(0.0, min(1.0, f0))


def f0_to_specular_color(f0, diffuse_color=None):
    """将F0基础反射率转换为镜面反射颜色

    对于金属材质，F0接近diffuse颜色
    对于非金属材质，F0是单色灰度

    Args:
        f0: F0基础反射率 (0-1)，或已经是 RGB 颜色
        diffuse_color: 漫反射颜色，用于金属度估算

    Returns:
        list: 镜面反射颜色 [r, g, b]
    """
    if f0 is None:
        f0 = 0.04
    # 输入已是颜色（RGB 元组/列表），直接作为镜面颜色返回
    if isinstance(f0, (list, tuple)):
        return [
            max(0.0, min(1.0, float(c))) if isinstance(c, (int, float)) else 0.0
            for c in (list(f0) + [0.0, 0.0, 0.0])[:3]
        ]
    try:
        f0 = max(0.0, min(1.0, float(f0)))
    except (TypeError, ValueError):
        return 0.04
    return [f0, f0, f0]


def thin_film_thickness_to_weight(thickness):
    """将薄膜厚度转换为涂层权重

    薄膜厚度通常0-10000纳米
    转换: thickness / 10000，限制在0-1范围

    Args:
        thickness: 薄膜厚度值

    Returns:
        float: 涂层权重 (0-1)
    """
    if thickness is None:
        return 0.0
    thickness = float(thickness)
    weight = thickness / 10000.0
    return max(0.0, min(1.0, weight))


def invert_value(value):
    """反转值 (1 - value)

    Args:
        value: 输入值 (0-1)

    Returns:
        float: 反转后的值 (0-1)
    """
    if value is None:
        return 1.0
    return 1.0 - max(0.0, min(1.0, float(value)))


def clamp(value, min_val=0.0, max_val=1.0):
    """限制值在指定范围内

    Args:
        value: 输入值
        min_val: 最小值
        max_val: 最大值

    Returns:
        float: 限制后的值
    """
    if value is None:
        return min_val
    return max(min_val, min(max_val, float(value)))


# ==================== 反向转换函数 ====================
# 用于反向映射（源/目标互换）时自动切换转换函数，保证 A→B 与 B→A 可逆。
# TRANSFORM_REVERSE 维护正向函数与反向函数的对应关系。

def roughness_to_diffuse_roughness(roughness):
    """PBR粗糙度转漫反射粗糙度（diffuse_roughness_to_roughness 的反向，当前为恒等）"""
    if roughness is None:
        return 0.5
    return max(0.0, min(1.0, float(roughness)))


def roughness_to_shininess(roughness):
    """粗糙度转光泽度/高光锐度（shininess_to_roughness 的反向，非 glossiness 模式）"""
    if roughness is None:
        return 0.0
    return 1.0 - max(0.0, min(1.0, float(roughness)))


def roughness_to_blinn_cosPower(roughness):
    """粗糙度转Blinn cosPower（blinn_cosPower_to_roughness 的反向）

    原公式: roughness = sqrt(2 / (cosPower + 2))
    反向:   cosPower = 2 / roughness² - 2
    """
    if roughness is None:
        return 0.0
    r = max(0.001, min(1.0, float(roughness)))
    return max(0.0, 2.0 / (r * r) - 2.0)


def roughness_to_phong_shi(roughness):
    """粗糙度转Phong shininess（phong_shi_to_roughness 的反向）

    原公式: roughness = sqrt(2 / shi)
    反向:   shi = 2 / roughness²
    """
    if roughness is None:
        return 0.0
    r = max(0.001, min(1.0, float(roughness)))
    return 2.0 / (r * r)


def f0_to_ior(f0):
    """F0基础反射率转折射率（ior_to_f0 的反向）

    原公式: f0 = ((ior - 1) / (ior + 1))²
    反向:   ior = (1 + sqrt(f0)) / (1 - sqrt(f0))
    """
    if f0 is None:
        return 1.5
    f = max(0.0, min(0.999, float(f0)))
    sqrt_f = f ** 0.5
    if sqrt_f >= 1.0:
        return 100.0
    return (1.0 + sqrt_f) / (1.0 - sqrt_f)


def specular_color_to_f0(specular_color):
    """镜面反射颜色转F0基础反射率（f0_to_specular_color 的反向）

    对非金属镜面颜色取灰度作为 F0；输入已是标量时直接返回。
    """
    if specular_color is None:
        return 0.04
    if isinstance(specular_color, (int, float)):
        return max(0.0, min(1.0, float(specular_color)))
    try:
        return rgb_to_grayscale(specular_color)
    except Exception:
        return 0.04


def color_div_scalar(color, scalar=1.0):
    """颜色除以标量（color_mul_scalar 的反向）

    Args:
        color: RGB颜色列表 [r, g, b]（若是标量则原样返回）
        scalar: 标量值，默认 1.0

    Returns:
        list: 变换后的颜色 [r, g, b]；输入非颜色时原样返回
    """
    if not color:
        return [0.0, 0.0, 0.0]
    if isinstance(color, (int, float)):
        return color
    try:
        scalar = float(scalar) if scalar else 1.0
        if scalar == 0:
            return color
        return [
            max(0.0, min(1.0, color[0] / scalar)),
            max(0.0, min(1.0, color[1] / scalar)),
            max(0.0, min(1.0, color[2] / scalar))
        ]
    except Exception:
        return color


def transmission_to_transparency(transmission):
    """透射权重转透明度（transparency_to_transmission 的反向，当前为恒等）"""
    if transmission is None:
        return 0.0
    if isinstance(transmission, (list, tuple)):
        return max(transmission) if transmission else 0.0
    return float(transmission)


def weight_to_thin_film_thickness(weight):
    """涂层权重转薄膜厚度（thin_film_thickness_to_weight 的反向）

    原公式: weight = thickness / 10000
    反向:   thickness = weight * 10000
    """
    if weight is None:
        return 0.0
    return max(0.0, float(weight)) * 10000.0



MATERIAL_CONVERSION_FUNCTIONS = {
    # RGB通道处理
    "rgb_to_channel": rgb_to_channel,
    "RGB转单通道": rgb_to_channel,
    "rgb_to_red": rgb_to_red,
    "RGB取红": rgb_to_red,
    "rgb_to_green": rgb_to_green,
    "RGB取绿": rgb_to_green,
    "rgb_to_blue": rgb_to_blue,
    "RGB取蓝": rgb_to_blue,
    "rgb_to_grayscale": rgb_to_grayscale,
    "RGB转灰度": rgb_to_grayscale,
    
    # 透明度/透射
    "transparency_to_transmission": transparency_to_transmission,
    "透明度转透射": transparency_to_transmission,
    "透明度转透射权重": transparency_to_transmission,
    
    # 粗糙度转换
    "shininess_to_roughness": shininess_to_roughness,
    "光泽度转粗糙度": shininess_to_roughness,
    "blinn_cosPower_to_roughness": blinn_cosPower_to_roughness,
    "Blinn高光锐度转粗糙度": blinn_cosPower_to_roughness,
    "phong_shi_to_roughness": phong_shi_to_roughness,
    "Phong光泽度转粗糙度": phong_shi_to_roughness,
    "diffuse_roughness_to_roughness": diffuse_roughness_to_roughness,
    "漫反射粗糙度转PBR粗糙度": diffuse_roughness_to_roughness,
    
    # PBR参数
    "metalness_from_specular": metalness_from_specular,
    "从镜面反射估算金属度": metalness_from_specular,
    "specular_to_specular_weight": specular_to_specular_weight,
    "镜面反射强度转权重": specular_to_specular_weight,
    "ior_to_f0": ior_to_f0,
    "折射率转F0": ior_to_f0,
    "f0_to_specular_color": f0_to_specular_color,
    "F0转镜面反射颜色": f0_to_specular_color,
    
    # 其他材质参数
    "emission_to_emission_luminance": emission_to_emission_luminance,
    "自发光转发光亮度": emission_to_emission_luminance,
    "translucence_to_subsurface": translucence_to_subsurface,
    "半透明度转次表面散射": translucence_to_subsurface,
    "thin_film_thickness_to_weight": thin_film_thickness_to_weight,
    "薄膜厚度转涂层权重": thin_film_thickness_to_weight,
    "invert_value": invert_value,
    "反转值": invert_value,
    "clamp": clamp,
    "限制范围": clamp,
    
    # 颜色运算
    "color_mul_scalar": color_mul_scalar,
    "颜色乘标量": color_mul_scalar,
    "color_add": color_add,
    "颜色相加": color_add,
    "color_lerp": color_lerp,
    "颜色插值": color_lerp,
    
    # 反向转换函数（反向映射时自动切换，见 TRANSFORM_REVERSE）
    "roughness_to_diffuse_roughness": roughness_to_diffuse_roughness,
    "PBR粗糙度转漫反射粗糙度": roughness_to_diffuse_roughness,
    "roughness_to_shininess": roughness_to_shininess,
    "粗糙度转光泽度": roughness_to_shininess,
    "roughness_to_blinn_cosPower": roughness_to_blinn_cosPower,
    "粗糙度转Blinn高光锐度": roughness_to_blinn_cosPower,
    "roughness_to_phong_shi": roughness_to_phong_shi,
    "粗糙度转Phong光泽度": roughness_to_phong_shi,
    "f0_to_ior": f0_to_ior,
    "F0转折射率": f0_to_ior,
    "specular_color_to_f0": specular_color_to_f0,
    "镜面反射颜色转F0": specular_color_to_f0,
    "color_div_scalar": color_div_scalar,
    "颜色除标量": color_div_scalar,
    "transmission_to_transparency": transmission_to_transparency,
    "透射转透明度": transmission_to_transparency,
    "weight_to_thin_film_thickness": weight_to_thin_film_thickness,
    "涂层权重转薄膜厚度": weight_to_thin_film_thickness
}


def apply_conversion(value, transform_name, source_attrs=None, parameters=None):
    """应用转换函数

    Args:
        value: 要转换的值
        transform_name: 转换函数名称
        source_attrs: 可选的源属性字典，用于需要多个参数的转换
        parameters: 可选的转换函数参数字典（来自映射文件 mapping 的 "parameters" 字段），
                    如 {"scalar": 2.0} 传给 color_mul_scalar(scalar=2.0)

    Returns:
        转换后的值
    """
    if not transform_name or transform_name not in MATERIAL_CONVERSION_FUNCTIONS:
        return value
    func = MATERIAL_CONVERSION_FUNCTIONS[transform_name]
    try:
        kwargs = {}
        if parameters:
            kwargs.update(parameters)
        if source_attrs:
            # 源属性字典作为兜底，映射文件显式配置的 parameters 优先
            for k, v in source_attrs.items():
                kwargs.setdefault(k, v)
        if kwargs:
            return func(value, **kwargs)
        return func(value)
    except Exception as e:
        print(f"转换函数 {transform_name} 执行失败: {e}")
        return value


TRANSFORM_INPUT_RANGES = {
    'blinn_cosPower_to_roughness': (2.0, 1000.0),
    'Blinn高光锐度转粗糙度': (2.0, 1000.0),
    'phong_shi_to_roughness': (1.0, 1000.0),
    'Phong光泽度转粗糙度': (1.0, 1000.0),
    'shininess_to_roughness': (0.0, 1.0),
    '光泽度转粗糙度': (0.0, 1.0),
    'diffuse_roughness_to_roughness': (0.0, 1.0),
    '漫反射粗糙度转PBR粗糙度': (0.0, 1.0),
    'specular_to_specular_weight': (0.0, 1.0),
    '镜面反射强度转权重': (0.0, 1.0),
    'ior_to_f0': (1.0, 3.0),
    '折射率转F0': (1.0, 3.0),
    'emission_to_emission_luminance': (0.0, 1.0),
    '自发光转发光亮度': (0.0, 1.0),
    'translucence_to_subsurface': (0.0, 1.0),
    '半透明度转次表面散射': (0.0, 1.0),
    'thin_film_thickness_to_weight': (0.0, 10000.0),
    '薄膜厚度转涂层权重': (0.0, 10000.0),
    'invert_value': (0.0, 1.0),
    '反转值': (0.0, 1.0),
    'transparency_to_transmission': (0.0, 1.0),
    '透明度转透射': (0.0, 1.0),
    '透明度转透射权重': (0.0, 1.0),
    
    # 反向转换函数（输入均为 0-1 区间）
    'roughness_to_diffuse_roughness': (0.0, 1.0),
    'PBR粗糙度转漫反射粗糙度': (0.0, 1.0),
    'roughness_to_shininess': (0.0, 1.0),
    '粗糙度转光泽度': (0.0, 1.0),
    'roughness_to_blinn_cosPower': (0.0, 1.0),
    '粗糙度转Blinn高光锐度': (0.0, 1.0),
    'roughness_to_phong_shi': (0.0, 1.0),
    '粗糙度转Phong光泽度': (0.0, 1.0),
    'f0_to_ior': (0.0, 1.0),
    'F0转折射率': (0.0, 1.0),
    'specular_color_to_f0': (0.0, 1.0),
    '镜面反射颜色转F0': (0.0, 1.0),
    'transmission_to_transparency': (0.0, 1.0),
    '透射转透明度': (0.0, 1.0),
    'weight_to_thin_film_thickness': (0.0, 1.0),
    '涂层权重转薄膜厚度': (0.0, 1.0),
}

# 转换函数 ↔ 反向转换函数（双向映射，反向映射时自动切换；反复反向可来回切换）
TRANSFORM_REVERSE = {
    # 正向 → 反向
    'diffuse_roughness_to_roughness': 'roughness_to_diffuse_roughness',
    '漫反射粗糙度转PBR粗糙度': 'roughness_to_diffuse_roughness',
    'shininess_to_roughness': 'roughness_to_shininess',
    '光泽度转粗糙度': 'roughness_to_shininess',
    'blinn_cosPower_to_roughness': 'roughness_to_blinn_cosPower',
    'Blinn高光锐度转粗糙度': 'roughness_to_blinn_cosPower',
    'phong_shi_to_roughness': 'roughness_to_phong_shi',
    'Phong光泽度转粗糙度': 'roughness_to_phong_shi',
    'ior_to_f0': 'f0_to_ior',
    '折射率转F0': 'f0_to_ior',
    'f0_to_specular_color': 'specular_color_to_f0',
    'F0转镜面反射颜色': 'specular_color_to_f0',
    'color_mul_scalar': 'color_div_scalar',
    '颜色乘标量': 'color_div_scalar',
    'color_div_scalar': 'color_mul_scalar',
    '颜色除标量': 'color_mul_scalar',
    'transparency_to_transmission': 'transmission_to_transparency',
    '透明度转透射': 'transmission_to_transparency',
    '透明度转透射权重': 'transmission_to_transparency',
    'thin_film_thickness_to_weight': 'weight_to_thin_film_thickness',
    '薄膜厚度转涂层权重': 'weight_to_thin_film_thickness',
    'invert_value': 'invert_value',
    '反转值': 'invert_value',
    # 反向 → 正向
    'roughness_to_diffuse_roughness': 'diffuse_roughness_to_roughness',
    'PBR粗糙度转漫反射粗糙度': 'diffuse_roughness_to_roughness',
    'roughness_to_shininess': 'shininess_to_roughness',
    '粗糙度转光泽度': 'shininess_to_roughness',
    'roughness_to_blinn_cosPower': 'blinn_cosPower_to_roughness',
    '粗糙度转Blinn高光锐度': 'blinn_cosPower_to_roughness',
    'roughness_to_phong_shi': 'phong_shi_to_roughness',
    '粗糙度转Phong光泽度': 'phong_shi_to_roughness',
    'f0_to_ior': 'ior_to_f0',
    'F0转折射率': 'ior_to_f0',
    'specular_color_to_f0': 'f0_to_specular_color',
    '镜面反射颜色转F0': 'f0_to_specular_color',
    'transmission_to_transparency': 'transparency_to_transmission',
    '透射转透明度': 'transparency_to_transmission',
    'weight_to_thin_film_thickness': 'thin_film_thickness_to_weight',
    '涂层权重转薄膜厚度': 'thin_film_thickness_to_weight',
}


def precompute_remap_samples(transform_name, num_samples=32, parameters=None):
    """为remapValue节点预计算采样点

    对转换函数在典型输入范围内均匀采样，生成(input, output)对，
    用于配置Maya remapValue节点的插值表。

    Args:
        transform_name: 转换函数名称
        num_samples: 采样点数量
        parameters: 可选的转换函数参数字典（来自映射文件 mapping 的 "parameters" 字段）

    Returns:
        (input_min, input_max, samples) 或 (None, None, None)
        samples: [(input_val, output_val), ...] 列表
    """
    func = MATERIAL_CONVERSION_FUNCTIONS.get(transform_name)
    if not func:
        return None, None, None

    range_info = TRANSFORM_INPUT_RANGES.get(transform_name)
    if not range_info:
        return None, None, None

    input_min, input_max = range_info
    samples = []

    for i in range(num_samples):
        fraction = i / (num_samples - 1) if num_samples > 1 else 0.0
        input_val = input_min + fraction * (input_max - input_min)
        try:
            if parameters:
                output_val = float(func(input_val, **parameters))
            else:
                output_val = float(func(input_val))
        except Exception:
            return None, None, None
        samples.append((input_val, output_val))

    return input_min, input_max, samples


def get_maya_main_window():
    """获取Maya主窗口指针"""
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        if main_window_ptr is not None:
            if PYSIDE_VERSION == 6:
                import shiboken6 as shiboken
                return shiboken.wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
            else:
                import shiboken2 as shiboken
                return shiboken.wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except Exception as e:
        print(f"获取Maya主窗口失败: {e}")
    return None


def _renderer_suffix(target_type):
    """根据目标材质类型返回渲染器短后缀（vray→vr, redshift→rs, arnold→ai, openPBR→opb）；无法识别返回空字符串"""
    t = (target_type or "").lower()
    if "vray" in t:
        return "vr"
    if "redshift" in t or t.startswith("rs") or t == "rsmaterial":
        return "rs"
    if "openpbr" in t:
        return "opb"
    if "ai" in t or "arnold" in t:
        return "ai"
    return ""


def _converted_material_name(source_name, target_type):
    """生成转换后材质名：先剥掉旧渲染器后缀（_ai/_rs/_vr/_opb/_cvt）再追加新后缀。

    多跳转换（如 ai→rs→vr）避免后缀无限累积：Gold_ai → Gold_rs → Gold_vr。
    """
    base = source_name
    low = base.lower()
    for old in ("_ai", "_rs", "_vr", "_opb", "_cvt"):
        if low.endswith(old):
            base = base[: -len(old)]
            break
    suffix = _renderer_suffix(target_type)
    return f"{base}_{suffix}" if suffix else f"{base}_cvt"


# 材质节点识别白名单：插件材质的 getClassification 可能不可靠时的兜底
# （新增渲染器材质时在此补充，如 RedshiftStandardMaterial / RSStandardMaterial）
KNOWN_MATERIAL_TYPES = (
    'lambert', 'phong', 'blinn', 'standardSurface', 'openPBRSurface',
    'aiStandardSurface', 'aiStandard', 'VRayMtl', 'RedshiftMaterial',
    'RedshiftStandardMaterial', 'RSStandardMaterial',
    'aiStandardHair', 'aiVolume',
)


def _is_material_node(node):
    """判断节点是否为材质：优先按分类（shader）判断，白名单兜底。

    避免硬编码类型列表漏掉新材质（如 RedshiftStandardMaterial）导致
    "选择中未找到可转换的材质"。
    """
    try:
        ntype = cmds.nodeType(node)
    except Exception:
        return False
    try:
        if cmds.getClassification(ntype, satisfies="shader"):
            return True
    except Exception:
        pass
    return ntype in KNOWN_MATERIAL_TYPES


def list_convertible_targets(source_type, base_dir=None):
    """列出某源材质类型可转换的目标类型（依据映射表，含直连/中转路径）。

    供 UI 菜单构建「转换导入 ▶」子菜单使用；不实例化完整 QDialog，
    仅挂载映射目录相关属性调用映射扫描/寻路方法。
    """
    if not source_type:
        return []
    conv = MaterialConverter.__new__(MaterialConverter)
    conv.mapping_base_dir = base_dir or PRESET_DIR
    conv._fallback_mapping_dirs = []
    try:
        src_types, tgt_types = conv._collect_mapping_types()
    except Exception:
        return []
    cands = []
    for t in sorted(set(src_types) | set(tgt_types)):
        if not t or t == source_type:
            continue
        try:
            if conv.find_conversion_path(source_type, t):
                cands.append(t)
        except Exception:
            continue
    return cands


class MaterialConverter(QtWidgets.QDialog):
    """材质转换工具主窗口"""

    def __init__(self, parent=None):
        # 尝试获取Maya主窗口作为父窗口
        maya_window = get_maya_main_window()
        if maya_window is not None:
            parent = maya_window

        super(MaterialConverter, self).__init__(parent)

        self.setWindowTitle(t("qtool.matconv.window_title"))
        self.setMinimumSize(675, 600)
        self.resize(675, 650)

        # 设置窗口标志，启用最小化和最大化按钮
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowMinimizeButtonHint | QtCore.Qt.WindowMaximizeButtonHint)

        # 样式设置
        self.setStyleSheet(_font_style("""
            QWidget {
                font-size: @FONT_18@px;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: @SIZE_5@px;
                margin-top: @SIZE_8@px;
                padding-top: @SIZE_8@px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: @SIZE_10@px;
                padding: 0 @SIZE_5@px;
            }
            QComboBox {
                min-height: @SIZE_30@px;
                padding: @SIZE_5@px @SIZE_30@px @SIZE_6@px @SIZE_10@px;
                font-size: @FONT_18@px;
            }
            QPushButton {
                min-height: @SIZE_30@px;
                padding: @SIZE_6@px @SIZE_15@px;
                font-size: @FONT_18@px;
            }
            QLineEdit {
                min-height: @SIZE_30@px;
                padding: @SIZE_5@px @SIZE_10@px;
                font-size: @FONT_18@px;
            }
            QTableWidget {
                gridline-color: #ddd;
                background-color: white;
                font-size: @FONT_18@px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: @SIZE_5@px;
                font-size: @FONT_18@px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """))

        self.preset_dir = PRESET_DIR
        if not os.path.exists(self.preset_dir):
            os.makedirs(self.preset_dir)

        # 初始化变量
        self.mapping_data = None
        self.source_material = None
        self.target_material_type = ""
        self.loaded_mappings = {}  # 存储加载的多个映射数据

        # 映射文件夹路径（插件内置 Assets/material_mapper_presets，基于脚本位置相对解析，移动插件后依然有效）
        self.mapping_base_dir = PRESET_DIR
        if not os.path.exists(self.mapping_base_dir):
            os.makedirs(self.mapping_base_dir)
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            fallback_dir = os.path.join(script_dir, "json示例", "material_mapper_presets").replace("\\", "/")
            self._fallback_mapping_dirs = [fallback_dir] if os.path.exists(fallback_dir) else []
        except Exception:
            self._fallback_mapping_dirs = []

        self.setup_ui()
        self.refresh_from_selection()

    def setup_ui(self):
        """创建UI界面"""
        main_layout = QtWidgets.QVBoxLayout(self)

        # 顶部工具栏
        toolbar_layout = QtWidgets.QHBoxLayout()
        toolbar_layout.setContentsMargins(5, 5, 5, 5)
        
        # 使用帮助按钮
        help_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.help"))
        help_btn.setStyleSheet(_font_style("background-color: #4CAF50; color: white; font-weight: bold; padding: @SIZE_8@px @SIZE_15@px;"))
        help_btn.setStatusTip(t("qtool.matconv.status.open_help"))
        help_btn.clicked.connect(self.show_help)
        toolbar_layout.addWidget(help_btn)
        
        toolbar_layout.addStretch()
        
        main_layout.addLayout(toolbar_layout)

        # 材质类型映射列表区域
        mapping_type_group = QtWidgets.QGroupBox(t("qtool.matconv.group.mapping_config"))
        mapping_type_group.setStatusTip(t("qtool.matconv.status.mapping_config"))
        mapping_type_layout = QtWidgets.QVBoxLayout()
        mapping_type_group.setLayout(mapping_type_layout)

        # 说明标签
        info_label = QtWidgets.QLabel(t("qtool.matconv.label.mapping_config_hint"))
        info_label.setStyleSheet(f"color: #666; font-size: {_fs(14)}px; padding: {_sc(5)}px 0;")
        mapping_type_layout.addWidget(info_label)

        # 映射类型表格
        self.mapping_type_table = QtWidgets.QTableWidget()
        self.mapping_type_table.setStatusTip(t("qtool.matconv.status.mapping_table"))
        self.mapping_type_table.setColumnCount(4)
        self.mapping_type_table.setHorizontalHeaderLabels(["", t("qtool.matconv.header.source_type"), t("qtool.matconv.header.target_type"), ""])
        self.mapping_type_table.horizontalHeader().setStretchLastSection(False)

        header = self.mapping_type_table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
            header.setSectionResizeMode(3, QHeaderView.Fixed)
        else:
            header.setResizeMode(0, QHeaderView.Fixed)
            header.setResizeMode(1, QHeaderView.Interactive)
            header.setResizeMode(2, QHeaderView.Interactive)
            header.setResizeMode(3, QHeaderView.Fixed)

        self.mapping_type_table.setColumnWidth(0, 30)
        self.mapping_type_table.setColumnWidth(1, 160)
        self.mapping_type_table.setColumnWidth(2, 160)
        self.mapping_type_table.setColumnWidth(3, 30)
        self.mapping_type_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.mapping_type_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.mapping_type_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        self.mapping_type_table.verticalHeader().setVisible(False)

        # 表格样式 - 暗色主题与预览表格一致
        self.mapping_type_table.setStyleSheet(_font_style("""
            QTableWidget {
                background-color: #404040;
                alternate-background-color: #505050;
                color: #e0e0e0;
                gridline-color: #505050;
            }
            QTableWidget::item {
                padding: @SIZE_5@px;
                font-size: @FONT_16@px;
            }
            QTableWidget::item:selected {
                background-color: #2080d0;
                color: white;
            }
            QHeaderView::section {
                background-color: #353535;
                color: #d0d0d0;
                border: 1px solid #303030;
                padding: @SIZE_5@px;
                font-weight: bold;
                font-size: @FONT_16@px;
            }
        """))

        # 默认添加一行源为空目标为openPBRSurface的映射
        row = self.mapping_type_table.rowCount()
        self.mapping_type_table.insertRow(row)
        self._set_mapping_combo(row, 1, "")
        # 设置目标材质类型为openPBRSurface
        self._set_mapping_combo(row, 2, "openPBRSurface")

        mapping_type_layout.addWidget(self.mapping_type_table)

        # 按钮行
        btn_layout = QtWidgets.QHBoxLayout()

        load_source_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.load_source"))
        load_source_btn.setStyleSheet(_font_style("background-color: #4CAF50; color: white; font-weight: bold; padding: @SIZE_8@px @SIZE_15@px;"))
        load_source_btn.setStatusTip(t("qtool.matconv.status.load_source"))
        load_source_btn.clicked.connect(self.load_source_type_from_selection)
        btn_layout.addWidget(load_source_btn)

        load_target_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.load_target"))
        load_target_btn.setStyleSheet(_font_style("background-color: #2196F3; color: white; font-weight: bold; padding: @SIZE_8@px @SIZE_15@px;"))
        load_target_btn.setStatusTip(t("qtool.matconv.status.load_target"))
        load_target_btn.clicked.connect(self.load_target_type_from_selection)
        btn_layout.addWidget(load_target_btn)

        btn_layout.addStretch()

        add_row_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.add_row"))
        add_row_btn.setStatusTip(t("qtool.matconv.status.add_row"))
        add_row_btn.clicked.connect(self.add_mapping_type_row)
        btn_layout.addWidget(add_row_btn)

        delete_row_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.delete_row"))
        delete_row_btn.setStatusTip(t("qtool.matconv.status.delete_row"))
        delete_row_btn.clicked.connect(self.delete_mapping_type_row)
        btn_layout.addWidget(delete_row_btn)

        clear_all_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.clear_all"))
        clear_all_btn.setStatusTip(t("qtool.matconv.status.clear_all"))
        clear_all_btn.clicked.connect(self.clear_mapping_type_list)
        btn_layout.addWidget(clear_all_btn)

        btn_layout.addStretch()

        save_config_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.save_preset"))
        save_config_btn.setStyleSheet(_font_style("background-color: #FF9800; color: white; font-weight: bold; padding: @SIZE_8@px @SIZE_15@px;"))
        save_config_btn.setStatusTip(t("qtool.matconv.status.save_preset"))
        save_config_btn.clicked.connect(self.save_mapping_type_preset)
        btn_layout.addWidget(save_config_btn)

        load_config_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.load_preset"))
        load_config_btn.setStyleSheet(_font_style("background-color: #9C27B0; color: white; font-weight: bold; padding: @SIZE_8@px @SIZE_15@px;"))
        load_config_btn.setStatusTip(t("qtool.matconv.status.load_preset"))
        load_config_btn.clicked.connect(self.load_mapping_type_preset)
        btn_layout.addWidget(load_config_btn)

        mapping_type_layout.addLayout(btn_layout)

        # 创建上下分割的布局
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        
        # 上半部分：材质类型映射（优先缩放）
        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout()
        top_widget.setLayout(top_layout)

        # 材质信息显示区域
        material_info_group = QtWidgets.QGroupBox(t("qtool.matconv.group.material_info"))
        material_info_layout = QtWidgets.QGridLayout()
        material_info_group.setLayout(material_info_layout)

        material_info_layout.addWidget(QtWidgets.QLabel(t("qtool.matconv.label.source_material")), 0, 0)
        self.source_name_label = QtWidgets.QLabel("(未选择)")
        self.source_name_label.setStyleSheet("color: #666; font-style: italic;")
        material_info_layout.addWidget(self.source_name_label, 0, 1)
        self.source_type_label = QtWidgets.QLabel("")
        material_info_layout.addWidget(self.source_type_label, 0, 2)

        material_info_layout.addWidget(QtWidgets.QLabel(t("qtool.matconv.label.target_material")), 1, 0)
        self.target_name_label = QtWidgets.QLabel("(未选择)")
        self.target_name_label.setStyleSheet("color: #666; font-style: italic;")
        material_info_layout.addWidget(self.target_name_label, 1, 1)
        self.target_type_label = QtWidgets.QLabel("")
        material_info_layout.addWidget(self.target_type_label, 1, 2)

        # 材质信息区域已从 UI 移除（代码保留，后续如需恢复取消下行注释即可）
        # top_layout.addWidget(material_info_group)

        # 映射文件区域（置于 UI 顶部）
        mapping_group = QtWidgets.QGroupBox(t("qtool.matconv.group.mapping_file"))
        mapping_group.setStatusTip(t("qtool.matconv.status.mapping_file"))
        mapping_layout = QtWidgets.QVBoxLayout()
        mapping_group.setLayout(mapping_layout)

        # 第一行：文件路径 + 浏览 + 状态
        mapping_row1 = QtWidgets.QHBoxLayout()
        self.mapping_file_edit = QtWidgets.QLineEdit()
        self.mapping_file_edit.setReadOnly(True)
        self.mapping_file_edit.setPlaceholderText(t("qtool.matconv.placeholder.mapping_file"))
        self.mapping_file_edit.setStatusTip(t("qtool.matconv.status.mapping_file_path"))
        browse_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.browse"))
        browse_btn.setStatusTip(t("qtool.matconv.status.browse"))
        browse_btn.clicked.connect(self.browse_mapping_file)
        self.mapping_status_label = QtWidgets.QLabel("")
        self.mapping_status_label.setStyleSheet("color: #888;")

        mapping_row1.addWidget(self.mapping_file_edit, 1)
        mapping_row1.addWidget(browse_btn)
        mapping_row1.addWidget(self.mapping_status_label)

        # 第二行：映射文件夹说明 + 打开文件夹按钮
        mapping_row2 = QtWidgets.QHBoxLayout()
        mapping_info_label = QtWidgets.QLabel(f'{t("qtool.matconv.label.mapping_dir")}: {self.mapping_base_dir}')
        mapping_info_label.setStyleSheet(f"color: #888; font-size: {_fs(11)}px;")
        open_folder_btn = QtWidgets.QPushButton(t("common.open_folder"))
        open_folder_btn.setStatusTip(t("qtool.matconv.status.open_mapping_folder"))
        open_folder_btn.clicked.connect(self.open_mapping_folder)
        mapping_row2.addWidget(mapping_info_label, 1)
        mapping_row2.addWidget(open_folder_btn)

        mapping_layout.addLayout(mapping_row1)
        mapping_layout.addLayout(mapping_row2)

        top_layout.addWidget(mapping_group)

        top_layout.addWidget(mapping_type_group)
        top_layout.setStretch(1, 1)  # 让材质类型映射配置优先缩放

        # 下半部分：选项和按钮
        bottom_widget = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout()
        bottom_widget.setLayout(bottom_layout)

        # 映射文件区域已移至顶部（见 _init_ui 顶部）

        # 映射预览区域 - 使用选项卡显示多个映射文件（不优先缩放）
        preview_group = QtWidgets.QGroupBox(t("qtool.matconv.group.preview"))
        preview_group.setStatusTip(t("qtool.matconv.status.preview"))
        preview_layout = QtWidgets.QVBoxLayout()
        preview_group.setLayout(preview_layout)

        self.preview_tabs = QtWidgets.QTabWidget()
        self.preview_tabs.setStatusTip(t("qtool.matconv.status.preview_tabs"))
        self.preview_tabs.setStyleSheet(_font_style("""
            QTabWidget::pane {
                border: 1px solid #ccc;
                background-color: #404040;
            }
            QTabBar::tab {
                background-color: #353535;
                color: #d0d0d0;
                font-size: @FONT_14@px;
                padding: @SIZE_6@px @SIZE_12@px;
                border: 1px solid #303030;
            }
            QTabBar::tab:selected {
                background-color: #505050;
                color: #ffffff;
            }
            QTabBar::tab:hover {
                background-color: #454545;
            }
        """))

        preview_layout.addWidget(self.preview_tabs)

        # 属性映射预览已从 UI 移除（代码保留，后续如需恢复取消下行注释即可）
        # bottom_layout.addWidget(preview_group)

        # 选项区域
        options_group = QtWidgets.QGroupBox(t("qtool.matconv.group.options"))
        options_group.setStatusTip(t("qtool.matconv.status.options"))
        options_layout = QtWidgets.QHBoxLayout()
        options_layout.setContentsMargins(_sc(8), _sc(2), _sc(8), _sc(2))
        options_layout.setSpacing(_sc(8))
        options_group.setLayout(options_layout)
        options_group.setMaximumHeight(_sc(64))  # 压缩转换选项高度

        self.copy_textures_check = QtWidgets.QCheckBox(t("qtool.matconv.check.copy_textures"))
        self.copy_textures_check.setStatusTip(t("qtool.matconv.status.copy_textures"))
        self.copy_textures_check.setChecked(True)
        options_layout.addWidget(self.copy_textures_check)

        self.keep_original_check = QtWidgets.QCheckBox(t("qtool.matconv.check.keep_original"))
        self.keep_original_check.setStatusTip(t("qtool.matconv.status.keep_original"))
        self.keep_original_check.setChecked(False)
        options_layout.addWidget(self.keep_original_check)

        self.auto_assign_check = QtWidgets.QCheckBox(t("qtool.matconv.check.auto_assign"))
        self.auto_assign_check.setStatusTip(t("qtool.matconv.status.auto_assign"))
        self.auto_assign_check.setChecked(True)
        options_layout.addWidget(self.auto_assign_check)

        self.fallback_default_check = QtWidgets.QCheckBox(t("qtool.matconv.check.fallback_default"))
        self.fallback_default_check.setStatusTip(t("qtool.matconv.status.fallback_default"))
        self.fallback_default_check.setChecked(False)
        options_layout.addWidget(self.fallback_default_check)

        options_layout.addStretch()

        bottom_layout.addWidget(options_group)

        # 按钮区域
        button_layout = QtWidgets.QHBoxLayout()

        self.convert_selection_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.convert_selection"))
        self.convert_selection_btn.setStatusTip(t("qtool.matconv.status.convert_selection"))
        self.convert_selection_btn.setStyleSheet(_font_style("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
                min-width: @SIZE_200@px;
                border: none;
                border-radius: @SIZE_4@px;
            }
            QPushButton:hover {
                background-color: #5EB0F7;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """))
        self.convert_selection_btn.clicked.connect(self.convert_selection)
        button_layout.addWidget(self.convert_selection_btn)

        self.convert_all_btn = QtWidgets.QPushButton(t("qtool.matconv.btn.convert_all"))
        self.convert_all_btn.setStatusTip(t("qtool.matconv.status.convert_all"))
        self.convert_all_btn.setStyleSheet(_font_style("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                font-weight: bold;
                min-width: @SIZE_240@px;
                border: none;
                border-radius: @SIZE_4@px;
            }
            QPushButton:hover {
                background-color: #FFB74D;
            }
            QPushButton:pressed {
                background-color: #F57C00;
            }
        """))
        self.convert_all_btn.clicked.connect(self.execute_conversion)
        button_layout.addWidget(self.convert_all_btn)

        self.convert_btn = self.convert_all_btn  # 合并为一个按钮

        button_layout.addStretch()

        close_btn = QtWidgets.QPushButton(t("common.close"))
        close_btn.setStatusTip(t("qtool.matconv.status.close"))
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        bottom_layout.addLayout(button_layout)

        # 状态栏
        status_layout = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel(t("qtool.matconv.label.status_ready"))
        self.status_label.setStyleSheet(f"color: #666; padding: {_sc(5)}px;")
        status_layout.addWidget(self.status_label)
        bottom_layout.addLayout(status_layout)

        # 将上下部分添加到分割器
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        
        # 设置分割器的初始大小比例（材质类型映射配置占较大空间，列表占比加倍）
        splitter.setSizes([750, 200])
        splitter.setStretchFactor(0, 1)  # top_widget优先占用空间
        splitter.setStretchFactor(1, 0)  # bottom_widget不优先占用空间
        
        # 将分割器添加到主布局
        main_layout.addWidget(splitter)

    def show_help(self):
        """显示使用帮助窗口"""
        import os
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "材质转换工具", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            QtWidgets.QMessageBox.information(self, t("btn.help"),
                t("qtool.matconv.msg.help_not_found", path=help_path))

    def _preview_table_resize_event(self, event):
        """预览表格resize事件，保持列比例：源属性2/3, 目标属性2/3, 默认值1/3, 说明1/3"""
        table = self.preview_table
        if table.columnCount() == 0:
            return

        total_width = table.viewport().width()
        if total_width <= 0:
            return

        # 保持比例：源属性1/3, 目标属性1/3, 默认值1/6, 说明1/6
        # 但用户要求前两列占2/3，后两列占1/3
        col2_width = int(total_width * (1/6))  # 默认值占1/6
        col3_width = int(total_width * (1/6))  # 说明占1/6
        remaining = total_width - col2_width - col3_width
        col0_width = remaining // 2  # 源属性
        col1_width = remaining - col0_width  # 目标属性

        table.setColumnWidth(0, col0_width)
        table.setColumnWidth(1, col1_width)
        table.setColumnWidth(2, col2_width)
        table.setColumnWidth(3, col3_width)

        if hasattr(table, '_original_resize_event'):
            table._original_resize_event(event)

    def refresh_from_selection(self):
        """从Maya选择刷新材质信息"""
        selected = cmds.ls(selection=True)
        if selected:
            # 尝试从选择中获取材质
            for obj in selected:
                # 获取材质
                shading_engines = cmds.listConnections(obj, type='shadingEngine')
                if shading_engines:
                    materials = cmds.ls(cmds.listConnections(shading_engines[0]), materials=True)
                    if materials:
                        self.load_material_info(materials[0], True)
                        break

    def load_material_info(self, material_name, is_source):
        """加载材质信息"""
        if not cmds.objExists(material_name):
            return

        try:
            material_type = cmds.nodeType(material_name)

            if is_source:
                self.source_material = material_name
                self.source_name_label.setText(material_name)
                self.source_name_label.setStyleSheet("color: #333; font-weight: bold;")
                self.source_type_label.setText(f"({material_type})")

                # 自动加载映射文件
                self.auto_load_mapping_for_material(material_name, material_type)
            else:
                self.target_name_label.setText(material_name)
                self.target_name_label.setStyleSheet("color: #333; font-weight: bold;")
                self.target_type_label.setText(f"({material_type})")
                self.target_material_type = material_type

            self.update_status()
        except Exception as e:
            print(f"加载材质信息失败: {e}")

    def auto_load_mapping_for_material(self, material_name, material_type):
        """根据材质类型自动加载映射文件"""
        # 如果已经有映射数据，不自动加载
        if self.mapping_data:
            return

        # 使用新的文件夹查找方式
        self.auto_load_mapping_for_source_material(material_name)

    def load_source_from_selection(self):
        """从选择加载源材质"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.select_object_in_maya"))
            return

        for obj in selected:
            # 检查是否是材质节点
            if cmds.nodeType(obj) in ['lambert', 'phong', 'blinn', 'standardSurface', 'aiStandardSurface',
                                      'openPBRSurface', 'file', 'place2dTexture']:
                self.load_material_info(obj, True)
                return

            # 尝试获取材质
            shading_engines = cmds.listConnections(obj, type='shadingEngine')
            if shading_engines:
                materials = cmds.ls(cmds.listConnections(shading_engines[0]), materials=True)
                if materials:
                    self.load_material_info(materials[0], True)
                    return

        QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.no_associated_material"))

    def load_target_from_selection(self):
        """从选择加载目标材质"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.select_object_in_maya"))
            return

        for obj in selected:
            # 检查是否是材质节点
            if cmds.nodeType(obj) in ['lambert', 'phong', 'blinn', 'standardSurface', 'aiStandardSurface',
                                      'openPBRSurface', 'file', 'place2dTexture']:
                self.load_material_info(obj, False)
                return

            # 尝试获取材质
            shading_engines = cmds.listConnections(obj, type='shadingEngine')
            if shading_engines:
                materials = cmds.ls(cmds.listConnections(shading_engines[0]), materials=True)
                if materials:
                    self.load_material_info(materials[0], False)
                    return

        QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.no_associated_material"))

    def clear_source(self):
        """清空源材质"""
        self.source_material = None
        self.source_name_label.setText("(未选择)")
        self.source_name_label.setStyleSheet("color: #666; font-style: italic;")
        self.source_type_label.setText("")
        self.update_status()

    def clear_target(self):
        """清空目标材质"""
        self.target_name_label.setText("(未选择)")
        self.target_name_label.setStyleSheet("color: #666; font-style: italic;")
        self.target_type_label.setText("")
        self.target_material_type = ""
        self.update_status()

    def update_status(self):
        """更新状态信息"""
        parts = []

        if self.source_material:
            parts.append(t("qtool.matconv.status.source_material", name=self.source_material))
        else:
            parts.append(t("qtool.matconv.status.source_unselected"))

        if self.target_material_type:
            parts.append(t("qtool.matconv.status.target_type", type=self.target_material_type))
        elif self.target_name_label.text() != "(未选择)":
            parts.append(t("qtool.matconv.status.target_material", name=self.target_name_label.text()))
        else:
            parts.append(t("qtool.matconv.status.target_unselected"))

        if self.mapping_data:
            mapping_count = len(self.mapping_data.get('mappings', []))
            parts.append(t("qtool.matconv.status.mapping_count", count=mapping_count))
        else:
            parts.append(t("qtool.matconv.status.mapping_unloaded"))

        self.status_label.setText(" | ".join(parts))

        # 更新按钮状态 - 转换所有基于配置的映射列表
        mappings = self.get_configured_mappings()
        self.convert_btn.setEnabled(bool(mappings))

    def browse_mapping_file(self):
        """浏览并选择映射文件夹，加载该文件夹下所有映射文件"""
        folder = QFileDialog.getExistingDirectory(
            self, t("qtool.matconv.dialog.select_mapping_folder"), self.mapping_base_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if not folder:
            return

        # 扫描文件夹下的所有mmap文件
        mmap_files = []
        for file in os.listdir(folder):
            if file.endswith('.mmap'):
                mmap_files.append(os.path.join(folder, file))

        if not mmap_files:
            QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.no_mapping_files"))
            return

        # 清空现有选项卡
        self.preview_tabs.clear()
        self.mapping_data = None
        self.loaded_mappings = {}  # 存储加载的映射数据

        # 加载所有映射文件
        failed_files = []
        for filepath in mmap_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    mapping_name = data.get('name', os.path.basename(filepath))
                    self.loaded_mappings[mapping_name] = data
                    self._add_mapping_tab(mapping_name, data)
            except Exception as e:
                print(f"加载映射文件失败 {filepath}: {e}")
                failed_files.append((os.path.basename(filepath), str(e)))

        if self.loaded_mappings:
            # 设置第一个选项卡的数据为当前映射数据
            first_name = list(self.loaded_mappings.keys())[0]
            self.mapping_data = self.loaded_mappings[first_name]

            # 设置目标材质类型
            target_type = self.mapping_data.get("target_type", "")
            if target_type:
                self.target_material_type = target_type
                self.target_type_label.setText(f"({target_type})")
                self.target_name_label.setText(t("qtool.matconv.target_will_become", type=target_type))

            self.mapping_file_edit.setText(folder)
            self.mapping_status_label.setText(t("qtool.matconv.status.mappings_loaded", count=len(self.loaded_mappings)))
            self.update_status()

            QMessageBox.information(self, t("msg.success"),
                                  t("qtool.matconv.msg.mappings_loaded", count=len(self.loaded_mappings)))

        if failed_files:
            detail = "\n".join(f"  • {name}: {err}" for name, err in failed_files)
            QMessageBox.warning(self, t("qtool.matconv.msg.partial_load_failed_title"),
                               t("qtool.matconv.msg.partial_load_failed", count=len(failed_files), detail=detail))

    def open_mapping_folder(self):
        """打开映射文件夹"""
        import subprocess
        try:
            folder_path = os.path.abspath(self.mapping_base_dir)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
            subprocess.Popen(['explorer', folder_path])
        except Exception as e:
            QMessageBox.warning(self, t("msg.error"), t("qtool.matconv.msg.open_folder_failed", e=str(e)))

    def _add_mapping_tab(self, name, data):
        """为映射数据添加一个选项卡"""
        table = QtWidgets.QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels([
            t("qtool.matconv.header.source_attr"),
            t("qtool.matconv.header.target_attr"),
            t("qtool.matconv.header.transform"),
            t("qtool.matconv.header.default")
        ])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        # 表格样式
        table.setStyleSheet(_font_style("""
            QTableView {
                background-color: #404040;
                alternate-background-color: #505050;
                color: #e0e0e0;
                font-size: @FONT_16@px;
            }
            QTableView::item {
                border: 1px solid #303030;
                padding: @SIZE_2@px;
                font-size: @FONT_16@px;
            }
            QTableView::item:selected {
                background-color: #2080d0;
                color: white;
            }
            QHeaderView::section {
                background-color: #353535;
                color: #d0d0d0;
                border: 1px solid #303030;
                padding: @SIZE_4@px;
                font-size: @FONT_16@px;
            }
        """))

        header = table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
            header.setSectionResizeMode(3, QHeaderView.Interactive)
        else:
            header.setResizeMode(0, QHeaderView.Interactive)
            header.setResizeMode(1, QHeaderView.Interactive)
            header.setResizeMode(2, QHeaderView.Interactive)
            header.setResizeMode(3, QHeaderView.Interactive)

        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 150)
        table.setColumnWidth(2, 100)
        table.setColumnWidth(3, 100)
        table.horizontalHeader().setStretchLastSection(False)

        mappings = data.get("mappings", [])
        for i, mapping in enumerate(mappings):
            table.insertRow(i)

            src_attr = mapping.get("source_attribute", "")
            dst_attr = mapping.get("target_attribute", "")
            default_value = mapping.get("default_value", "")
            transform = mapping.get("transform", "")

            note = ""
            if not src_attr and dst_attr:
                note = t("qtool.matconv.note.new_attr")
            elif src_attr and not dst_attr:
                note = t("qtool.matconv.note.ignored")
            elif default_value:
                note = t("qtool.matconv.note.use_default")

            table.setItem(i, 0, QtWidgets.QTableWidgetItem(src_attr))
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(dst_attr))
            table.setItem(i, 2, QtWidgets.QTableWidgetItem(transform))
            table.setItem(i, 3, QtWidgets.QTableWidgetItem(default_value if default_value else note))

            if default_value:
                for j in range(4):
                    item = table.item(i, j)
                    if item:
                        item.setBackground(QtGui.QColor(255, 255, 220))

        table.resizeRowsToContents()
        self.preview_tabs.addTab(table, name)

    def load_mapping_file(self, filepath):
        """加载单个映射文件"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.mapping_data = data
            self.mapping_file_edit.setText(filepath)

            # 设置目标材质类型
            target_type = data.get("target_type", "")
            if not self.target_material_type and target_type:
                self.target_material_type = target_type
                self.target_type_label.setText(f"({target_type})")
                self.target_name_label.setText(t("qtool.matconv.target_will_become", type=target_type))
                self.target_name_label.setStyleSheet("color: #666; font-style: italic;")

            # 更新映射预览
            self.update_mapping_preview()

            # 更新状态（明确区分映射文件与映射条数，避免误解）
            mapping_count = len(data.get('mappings', []))
            self.mapping_status_label.setText(
                f"{os.path.basename(filepath)}（{mapping_count} 条映射）")
            self.update_status()

            QMessageBox.information(self, t("msg.success"),
                                  t("qtool.matconv.msg.mapping_loaded",
                                    source_type=data.get('source_type', t("qtool.matconv.common.unknown")),
                                    target_type=target_type,
                                    count=mapping_count))

        except Exception as e:
            QMessageBox.critical(self, t("msg.error"), t("qtool.matconv.msg.load_mapping_failed", e=str(e)))
            self.mapping_status_label.setText(t("qtool.matconv.status.load_failed"))

    def update_mapping_preview(self):
        """更新映射预览表格"""
        # 清空现有选项卡
        self.preview_tabs.clear()

        if not self.mapping_data:
            return

        # 添加当前映射数据到选项卡
        name = self.mapping_data.get('name', t("qtool.matconv.common.current_mapping"))
        self._add_mapping_tab(name, self.mapping_data)

    def load_source_type_from_selection(self):
        """从选择加载源材质类型到列表"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.select_material_node_in_maya"))
            return

        material = None
        for item in selected:
            if _is_material_node(item):
                material = item
                break

        if not material:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.no_valid_material_node"))
            return

        material_type = cmds.nodeType(material)

        # 检查是否有选中的行
        selected_rows = set()
        for idx in self.mapping_type_table.selectionModel().selectedRows():
            selected_rows.add(idx.row())

        # 如果有选中的行，使用第一行
        if selected_rows:
            row_found = sorted(selected_rows)[0]
        else:
            # 查找空行（没有源类型的行）
            row_found = -1
            for row in range(self.mapping_type_table.rowCount()):
                if not self._get_cell_text(row, 1):
                    row_found = row
                    break

            # 如果没有空行，查找有源类型但没有目标类型的行
            if row_found == -1:
                for row in range(self.mapping_type_table.rowCount()):
                    src_text = self._get_cell_text(row, 1)
                    dst_text = self._get_cell_text(row, 2)
                    if src_text == material_type and not dst_text:
                        row_found = row
                        break

            if row_found == -1:
                # 添加新行
                row_found = self.mapping_type_table.rowCount()
                self.mapping_type_table.insertRow(row_found)

        # 设置源材质类型
        self._set_cell_text(row_found, 1, material_type)

        # 高亮该行
        self.mapping_type_table.selectRow(row_found)

    def load_target_type_from_selection(self):
        """从选择加载目标材质类型到列表"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.select_material_node_in_maya"))
            return

        material = None
        for item in selected:
            if _is_material_node(item):
                material = item
                break

        if not material:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.no_valid_material_node"))
            return

        material_type = cmds.nodeType(material)

        # 检查是否有选中的行
        selected_rows = set()
        for idx in self.mapping_type_table.selectionModel().selectedRows():
            selected_rows.add(idx.row())

        # 如果有选中的行，使用第一行
        if selected_rows:
            row_found = sorted(selected_rows)[0]
        else:
            # 查找有源但没有目标的行
            row_found = -1
            for row in range(self.mapping_type_table.rowCount()):
                src_text = self._get_cell_text(row, 1)
                dst_text = self._get_cell_text(row, 2)
                if src_text and not dst_text:
                    row_found = row
                    break

            if row_found == -1:
                # 如果没有找到符合条件的行，添加到新行
                row_found = self.mapping_type_table.rowCount()
                self.mapping_type_table.insertRow(row_found)

        # 设置目标材质类型
        self._set_cell_text(row_found, 2, material_type)

        # 高亮该行
        self.mapping_type_table.selectRow(row_found)

    def add_mapping_type_row(self):
        """添加一行空映射"""
        row = self.mapping_type_table.rowCount()
        self.mapping_type_table.insertRow(row)
        self._set_mapping_combo(row, 1, "")
        self._set_mapping_combo(row, 2, "")

    def delete_mapping_type_row(self):
        """删除选中的行"""
        selected_rows = set()
        # 获取所有选中的行
        for idx in self.mapping_type_table.selectionModel().selectedRows():
            selected_rows.add(idx.row())

        # 如果没有选中项，检查是否有当前行
        if not selected_rows and self.mapping_type_table.currentRow() >= 0:
            selected_rows.add(self.mapping_type_table.currentRow())

        for row in sorted(selected_rows, reverse=True):
            self.mapping_type_table.removeRow(row)

    def clear_mapping_type_list(self):
        """清空映射类型列表"""
        self.mapping_type_table.setRowCount(0)

    def save_mapping_type_preset(self):
        """保存材质类型映射配置为.mlist预设"""
        mappings = []
        for row in range(self.mapping_type_table.rowCount()):
            source_type = self._get_cell_text(row, 1)
            target_type = self._get_cell_text(row, 2)
            if source_type or target_type:
                mappings.append({
                    "source_type": source_type,
                    "target_type": target_type
                })

        if not mappings:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.no_mapping_config_to_save"))
            return

        from datetime import datetime
        preset_data = {
            "version": "1.0",
            "name": "材质类型映射配置",
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "mappings": mappings
        }

        preset_dir = os.path.join(self.preset_dir, "mapping_type_presets")
        if not os.path.exists(preset_dir):
            os.makedirs(preset_dir)

        filepath, _ = QFileDialog.getSaveFileName(
            self, t("qtool.matconv.dialog.save_mapping_preset"), preset_dir, t("qtool.matconv.dialog.mlist_filter")
        )

        if filepath:
            if not filepath.endswith('.mlist'):
                filepath += '.mlist'
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(preset_data, f, indent=4, ensure_ascii=False)
                QMessageBox.information(self, t("msg.success"), t("qtool.matconv.msg.mapping_config_saved", filepath=filepath))
            except Exception as e:
                QMessageBox.critical(self, t("msg.error"), t("qtool.matconv.msg.save_mapping_config_failed", e=str(e)))

    def load_mapping_type_preset(self):
        """从.mlist预设加载材质类型映射配置"""
        preset_dir = os.path.join(self.preset_dir, "mapping_type_presets")
        if not os.path.exists(preset_dir):
            os.makedirs(preset_dir)

        filepath, _ = QFileDialog.getOpenFileName(
            self, t("qtool.matconv.dialog.load_mapping_preset"), preset_dir, t("qtool.matconv.dialog.mlist_filter")
        )

        if not filepath:
            return

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)

            mappings = preset_data.get("mappings", [])
            if not mappings:
                QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.no_mapping_in_preset"))
                return

            self.mapping_type_table.setRowCount(0)
            for mapping in mappings:
                row = self.mapping_type_table.rowCount()
                self.mapping_type_table.insertRow(row)
                self._set_mapping_combo(row, 1, mapping.get("source_type", ""))
                self._set_mapping_combo(row, 2, mapping.get("target_type", ""))

            QMessageBox.information(self, t("msg.success"), t("qtool.matconv.msg.mapping_config_loaded", count=len(mappings)))
        except Exception as e:
            QMessageBox.critical(self, t("msg.error"), t("qtool.matconv.msg.load_mapping_config_failed", e=str(e)))

    def _list_available_mappings(self):
        """列出所有搜索目录下可用的映射文件名（用于未找到时的提示）"""
        files = []
        dirs = [self.mapping_base_dir]
        for fb in getattr(self, '_fallback_mapping_dirs', []):
            if os.path.exists(fb):
                dirs.append(fb)
        for d in dirs:
            if not os.path.exists(d):
                continue
            for f in os.listdir(d):
                if f.endswith('.mmap') and f not in files:
                    files.append(f)
        return sorted(files)

    def _collect_mapping_types(self):
        """扫描映射文件，收集所有可用的源/目标材质类型

        Returns:
            tuple: (source_types, target_types) 两个 sorted list
        """
        source_types = set()
        target_types = set()
        dirs = [self.mapping_base_dir]
        for fb in getattr(self, '_fallback_mapping_dirs', []):
            if os.path.exists(fb):
                dirs.append(fb)
        for d in dirs:
            if not os.path.exists(d):
                continue
            for f in os.listdir(d):
                if not f.endswith('.mmap') or f.startswith('_'):
                    continue
                filepath = os.path.join(d, f)
                try:
                    with open(filepath, 'r', encoding='utf-8') as fp:
                        data = json.load(fp)
                    st = data.get('source_type', '')
                    tt = data.get('target_type', '')
                    if st:
                        source_types.add(st)
                    if tt:
                        target_types.add(tt)
                except Exception:
                    # JSON 解析失败时回退到文件名解析
                    base = f[:-5]  # 去掉 .mmap
                    parts = base.rsplit('_', 1)
                    if len(parts) == 2:
                        source_types.add(parts[0])
                        target_types.add(parts[1])
        return sorted(source_types), sorted(target_types)

    def _create_type_combo(self, current_text="", is_source=True):
        """创建材质类型下拉框

        Args:
            current_text: 当前选中的文本
            is_source: True=源类型下拉框，False=目标类型下拉框

        Returns:
            QComboBox
        """
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        source_types, target_types = self._collect_mapping_types()
        types = source_types if is_source else target_types
        combo.addItem("")  # 空选项
        for tp in types:
            combo.addItem(tp)
        if current_text:
            idx = combo.findText(current_text)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(current_text)
        # 样式与表格一致（含编辑框与弹出列表字体）
        combo.setStyleSheet(_font_style("""
            QComboBox {
                padding: @SIZE_3@px @SIZE_8@px;
                font-size: @FONT_16@px;
            }
            QComboBox QLineEdit {
                font-size: @FONT_16@px;
                padding: @SIZE_2@px @SIZE_4@px;
            }
            QComboBox QAbstractItemView {
                font-size: @FONT_16@px;
                background-color: #353535;
                color: #e0e0e0;
                selection-background-color: #2080d0;
                selection-color: white;
                padding: @SIZE_2@px;
            }
        """))
        return combo

    def _set_mapping_combo(self, row, col, text=""):
        """在映射类型表格的指定单元格设置下拉框"""
        is_source = (col == 1)
        combo = self._create_type_combo(text, is_source)
        self.mapping_type_table.setCellWidget(row, col, combo)
        return combo

    def _get_cell_text(self, row, col):
        """读取单元格文本（兼容下拉框和普通项）"""
        widget = self.mapping_type_table.cellWidget(row, col)
        if widget:
            return widget.currentText()
        item = self.mapping_type_table.item(row, col)
        return item.text() if item else ""

    def _set_cell_text(self, row, col, text):
        """设置单元格文本（有下拉框则更新下拉框，否则创建下拉框或设置项）"""
        widget = self.mapping_type_table.cellWidget(row, col)
        if widget:
            idx = widget.findText(text)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            else:
                widget.setEditText(text)
        else:
            if col in (1, 2):
                self._set_mapping_combo(row, col, text)
            else:
                item = self.mapping_type_table.item(row, col)
                if item:
                    item.setText(text)
                else:
                    self.mapping_type_table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    def find_mapping_file(self, source_type, target_type):
        """根据源材质类型和目标材质类型查找映射文件

        Args:
            source_type: 源材质类型（如 "lambert", "aiStandardSurface"）
            target_type: 目标材质类型（如 "openPBRSurface"）

        Returns:
            找到的映射文件路径，如果没有找到返回None
        """
        expected_filename = f"{source_type}_{target_type}.mmap"

        def _search_in_dir(base_dir):
            if not os.path.exists(base_dir):
                return None
            # 平铺结构：映射文件直接放在目录下（材质节点属性映射工具的保存格式）
            flat_filepath = os.path.join(base_dir, expected_filename)
            if os.path.exists(flat_filepath):
                return flat_filepath
            # 子目录结构：{target_type}/{source_type}_{target_type}.mmap
            target_folder = os.path.join(base_dir, target_type)
            if os.path.exists(target_folder):
                filepath = os.path.join(target_folder, expected_filename)
                if os.path.exists(filepath):
                    return filepath
            for folder_name in os.listdir(base_dir):
                folder_path = os.path.join(base_dir, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                filepath = os.path.join(folder_path, expected_filename)
                if os.path.exists(filepath):
                    return filepath
            return None

        result = _search_in_dir(self.mapping_base_dir)
        if result:
            return result

        for fallback_dir in getattr(self, '_fallback_mapping_dirs', []):
            result = _search_in_dir(fallback_dir)
            if result:
                return result

        return None

    def find_conversion_path(self, source_type, target_type):
        """查找转换路径（支持直连和中转）

        优先直接映射 A→B；若不存在，则尝试中转 A→中间格式→B。
        中间格式从所有映射文件中自动扫描，优先 openPBRSurface。

        Returns:
            list of (mapping_file, step_source, step_target) tuples，或 None
        """
        if not source_type or not target_type:
            return None

        # 1. 尝试直接映射
        direct = self.find_mapping_file(source_type, target_type)
        if direct:
            return [(direct, source_type, target_type)]

        # 2. 收集所有可用类型（作为中间桥梁候选）
        all_types = set()
        dirs = [self.mapping_base_dir]
        for fb in getattr(self, '_fallback_mapping_dirs', []):
            if os.path.exists(fb):
                dirs.append(fb)
        for d in dirs:
            if not os.path.exists(d):
                continue
            for f in os.listdir(d):
                if not f.endswith('.mmap') or f.startswith('_'):
                    continue
                # 从文件名解析 source_target
                base = f[:-5]
                parts = base.rsplit('_', 1)
                if len(parts) == 2:
                    all_types.add(parts[0])
                    all_types.add(parts[1])

        # 优先 openPBRSurface 作为中转格式，然后尝试其他类型
        preferred_hub = "openPBRSurface"
        ordered = sorted(all_types, key=lambda t: (0 if t == preferred_hub else 1, t))

        for intermediate in ordered:
            if intermediate == source_type or intermediate == target_type:
                continue
            step1 = self.find_mapping_file(source_type, intermediate)
            step2 = self.find_mapping_file(intermediate, target_type)
            if step1 and step2:
                print(f"中转路径: {source_type} -> {intermediate} -> {target_type}")
                return [(step1, source_type, intermediate),
                        (step2, intermediate, target_type)]

        return None

    def _convert_material_via_path(self, material, source_type, target_type, copy_textures, fallback_default=False):
        """通过转换路径（支持多步中转）转换单个材质

        Args:
            material: 源材质节点
            source_type: 源材质类型
            target_type: 目标材质类型
            copy_textures: 是否复制纹理连接
            fallback_default: 无映射路径时是否创建默认目标材质（仅类型正确，不复制属性）

        Returns:
            (target_material, success_count, failed_count, default_count, texture_count) 或 None
        """
        path = self.find_conversion_path(source_type, target_type)
        if not path:
            print(f"未找到 {source_type} -> {target_type} 的转换路径（直接或中转）")
            available = self._list_available_mappings()
            if available:
                print("可用映射文件: " + ", ".join(available))
            if fallback_default:
                try:
                    target_name = _converted_material_name(material, target_type)
                    target_material = cmds.shadingNode(target_type, name=target_name, asShader=True)
                    print(f"[默认材质] 未找到映射文件，创建默认 {target_type} 材质: {target_material}")
                    return (target_material, 0, 0, 0, 0)
                except Exception as e:
                    print(f"[默认材质] 创建默认材质失败: {e}")
                    return None
            return None

        current_source = material
        intermediate_materials = []
        old_mapping_data = self.mapping_data
        old_target_type = getattr(self, 'target_material_type', target_type)

        try:
            for i, (mapping_file, step_src, step_dst) in enumerate(path):
                # 加载映射数据
                try:
                    with open(mapping_file, 'r', encoding='utf-8') as f:
                        self.mapping_data = json.load(f)
                except Exception as e:
                    print(f"加载映射文件失败 {mapping_file}: {e}")
                    return None

                if len(path) > 1:
                    print(f"  步骤 {i+1}/{len(path)}: {step_src} -> {step_dst}")

                self.target_material_type = step_dst
                result = self.convert_material(current_source, step_dst, copy_textures)

                if not result:
                    print(f"转换失败: {step_src} -> {step_dst}")
                    return None

                target_material = result[0]

                if i < len(path) - 1:
                    # 非最后一步：结果作为下一步输入，记录待清理的中间材质
                    intermediate_materials.append(target_material)
                    current_source = target_material
                else:
                    # 最后一步：result 即为最终结果
                    pass

        finally:
            self.mapping_data = old_mapping_data
            self.target_material_type = old_target_type

        # 清理中间材质
        for im in intermediate_materials:
            try:
                if cmds.objExists(im):
                    cmds.delete(im)
                    print(f"  清理中间材质: {im}")
            except Exception:
                pass

        return result

    def auto_load_mapping_for_source_material(self, source_material):
        """根据源材质自动查找并加载对应的映射文件

        查找顺序：
        1. 先获取源材质的类型
        2. 扫描映射文件夹（含 fallback 目录），找到目标材质类型
        3. 找到匹配的映射文件并加载
        """
        if not source_material:
            return False

        source_type = cmds.nodeType(source_material)
        if not source_type:
            return False

        all_search_dirs = [self.mapping_base_dir]
        for fb_dir in getattr(self, '_fallback_mapping_dirs', []):
            if os.path.exists(fb_dir):
                all_search_dirs.append(fb_dir)

        target_types_seen = set()
        for search_dir in all_search_dirs:
            if not os.path.exists(search_dir):
                continue
            # 平铺结构：直接扫描 {source_type}_*.mmap 文件
            for fname in os.listdir(search_dir):
                if fname.startswith(f"{source_type}_") and fname.endswith(".mmap"):
                    mapping_file = os.path.join(search_dir, fname)
                    print(f"自动找到映射文件: {mapping_file}")
                    self.load_mapping_file(mapping_file)
                    return True
            # 子目录结构：遍历目标材质类型目录
            for target_type in os.listdir(search_dir):
                target_path = os.path.join(search_dir, target_type)
                if not os.path.isdir(target_path):
                    continue
                if target_type in target_types_seen:
                    continue
                target_types_seen.add(target_type)

                mapping_file = self.find_mapping_file(source_type, target_type)
                if mapping_file:
                    print(f"自动找到映射文件: {mapping_file}")
                    self.load_mapping_file(mapping_file)
                    return True

        return False

    def _parse_default_value(self, default_value_str):
        """解析默认值字符串，支持多种数据格式"""
        if not default_value_str:
            return None

        try:
            return int(default_value_str)
        except ValueError:
            try:
                return float(default_value_str)
            except ValueError:
                pass

        cleaned = default_value_str.strip()
        if (cleaned.startswith('[') and cleaned.endswith(']')) or \
           (cleaned.startswith('(') and cleaned.endswith(')')):
            cleaned = cleaned[1:-1]

        parts = []
        if ' ' in cleaned:
            parts = cleaned.split()
        elif ',' in cleaned:
            parts = [p.strip() for p in cleaned.split(',')]

        if len(parts) == 3:
            try:
                rgb = [float(p) for p in parts]
                return rgb
            except ValueError:
                pass

        return default_value_str

    def _get_attribute_value(self, node, attribute):
        """获取节点属性的值"""
        try:
            full_attr = f"{node}.{attribute}"
            if not cmds.objExists(full_attr):
                return None

            attr_type = cmds.getAttr(full_attr, type=True)

            if attr_type in ['double', 'float', 'int', 'long', 'short', 'byte', 'char']:
                return cmds.getAttr(full_attr)

            elif attr_type in ['double3', 'float3', 'double2', 'float2', 'vector',
                               'Tdouble3', 'Tfloat3', 'Tdouble2', 'Tfloat2',
                               'double4', 'float4', 'Tdouble4', 'Tfloat4']:
                value = cmds.getAttr(full_attr)
                return self._flatten_value(value)

            elif attr_type == 'string':
                return cmds.getAttr(full_attr)

            elif attr_type == 'bool':
                return cmds.getAttr(full_attr)

            elif attr_type in ['doubleLinear', 'floatLinear']:
                return cmds.getAttr(full_attr)

            elif attr_type in ['doubleArray', 'floatArray', 'intArray']:
                return cmds.getAttr(full_attr, asString=True)

            elif attr_type == 'enum':
                return cmds.getAttr(full_attr)

            elif attr_type == 'compound':
                # 可能是颜色/向量 compound，getAttr 返回元组，可能是嵌套的
                try:
                    value = cmds.getAttr(full_attr)
                    return self._flatten_value(value)
                except:
                    return None

            else:
                # 未知类型：返回原始值并统一拍平（兼容 Maya 各种颜色 compound）
                try:
                    value = cmds.getAttr(full_attr)
                    return self._flatten_value(value)
                except:
                    return None

        except Exception as e:
            print(f"获取属性值失败 {node}.{attribute}: {e}")
            return None

    @staticmethod
    def _flatten_value(value):
        """将 Maya getAttr 返回的任意嵌套结构拍平为普通 (r,g,b)/(r,g) 或原值。

        Maya 对颜色/向量 compound 的返回形式不一，常见有：
            [(1.0, 0.2, 0.3)] / ((1.0, 0.2, 0.3),) / [[1.0, 0.2, 0.3]]
            (1.0, 0.2, 0.3) / (1.0, 0.2, 0.3, 1.0)
        统一解包成最外层非单元素的可迭代。
        """
        if value is None:
            return None
        while isinstance(value, (list, tuple)) and len(value) == 1:
            inner = value[0]
            if isinstance(inner, (list, tuple)):
                value = inner
            else:
                break
        if isinstance(value, (list, tuple)):
            return value
        return value

    # ------------------------------------------------------------------
    # 颜色属性赋值：优先使用 R/G/B 子通道逐通道 setAttr
    # （VRayMtl.color / openPBR.baseColor / Lambert.color 等 Maya 颜色
    #  编辑器 UI 默认以 HSV 展示，但其底层 compound 子属性一律是
    #  .R / .G / .B 命名，逐通道 setAttr 比 type=double3 更稳定。）
    # ------------------------------------------------------------------
    def _try_set_color_by_children(self, node, attribute, value):
        """若 value 是长度 >=3 的元组/列表，且 {attribute}R/.G/.B 子通道存在，
        则逐通道 setAttr。成功返回 True，否则 False。"""
        if not isinstance(value, (list, tuple)):
            return False
        if len(value) < 3:
            return False
        suffixes = ('R', 'G', 'B')
        children = [f"{attribute}{s}" for s in suffixes]
        full_attr = f"{node}.{attribute}"
        # 必须保证属性存在，否则子通道也不存在
        if not cmds.objExists(full_attr):
            return False
        for c in children:
            if not cmds.objExists(f"{node}.{c}"):
                return False
        try:
            for c, v in zip(children, value[:3]):
                cmds.setAttr(f"{node}.{c}", float(v))
            return True
        except Exception as e:
            print(f"[颜色逐通道写入] 失败 {full_attr} ({value}): {e}")
            return False

    def _debug_read_color(self, full_attr):
        """调试用：回读颜色属性当前值（含类型信息）"""
        try:
            attr_type = cmds.getAttr(full_attr, type=True)
            got = cmds.getAttr(full_attr)
            return f"type={attr_type} value={self._flatten_value(got)}"
        except Exception as e:
            return f"读取失败: {e}"

    def _set_color_attribute_robust(self, node, attribute, value):
        """颜色/向量（list/tuple）多方案写入。

        依次尝试：
          1. 逐子通道 setAttr(node.{attr}R/.G/.B)——最可靠，逐通道独立写入，
             不会出现整体 setAttr 对部分渲染器（如 V-Ray）属性红色通道丢失的问题。
          2. setAttr(node.{attr}, r, g, b, type='float3')——V-Ray Tfloat3 /
             Maya float3 的标准写法，优先于无 type。
          3. setAttr(node.{attr}, r, g, b, type='double3')
          4. setAttr(node.{attr}, r, g, b)（三个位置参数，无 type，最后兜底）
        任一方案 setAttr 无异常即认为写入成功。
        """
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return False
        vals = [float(v) for v in value[:3]]
        full_attr = f"{node}.{attribute}"

        # 方案1：逐子通道 R/G/B——写入成功即成功
        if self._try_set_color_by_children(node, attribute, vals):
            print(f"[颜色写入] 逐通道成功 {full_attr} <- {vals} 回读 {self._debug_read_color(full_attr)}")
            return True

        # 方案2 / 方案3：带 type（float3 优先，兼容 V-Ray Tfloat3）
        for typ in ('float3', 'double3'):
            try:
                cmds.setAttr(full_attr, vals[0], vals[1], vals[2], type=typ)
                print(f"[颜色写入] {typ} 成功 {full_attr} <- {vals} 回读 {self._debug_read_color(full_attr)}")
                return True
            except Exception as e:
                print(f"[颜色写入] {typ} 失败 {full_attr}: {e}")

        # 方案4：无 type 兜底
        try:
            cmds.setAttr(full_attr, vals[0], vals[1], vals[2])
            print(f"[颜色写入] 无type 成功 {full_attr} <- {vals} 回读 {self._debug_read_color(full_attr)}")
            return True
        except Exception as e:
            print(f"[颜色写入] 无type 失败 {full_attr}: {e}")

        return False

    def _is_color_child_attr(self, node, attr):
        """判断属性是否为颜色子通道（如 diffuseColorR / colorB / baseColorG）

        渲染器材质的颜色子通道常与父颜色属性（如 color ↔ diffuseColor）别名共享，
        单独写入子通道可能覆盖已整体设置的颜色（导致通道丢失）。
        当 attr 以 R/G/B 结尾且其父属性存在时视为颜色子通道。

        Args:
            node: 目标材质节点
            attr: 目标属性名

        Returns:
            bool: 是否为颜色子通道
        """
        if not attr or len(attr) <= 1 or attr[-1] not in 'RGB':
            return False
        parent = attr[:-1]
        return cmds.objExists(f"{node}.{parent}")

    def _disconnect_incoming(self, full_attr):
        """断开目标属性上来自其他节点的输入连接

        VRayMtl 等渲染器材质的复合颜色属性（如 diffuseColor）可能与别名属性
        （如 color）或内部默认连接共享，setAttr 时会报"已锁定或已连接"。
        设置前先断开这些输入连接，保证值能正确写入。
        """
        try:
            incoming = cmds.listConnections(full_attr, source=True, destination=False, plugs=True)
            if not incoming:
                return
            for plug in incoming:
                try:
                    cmds.disconnectAttr(plug, full_attr)
                except Exception:
                    pass
        except Exception:
            pass

    def _set_attribute_value(self, node, attribute, value):
        """设置节点属性的值"""
        try:
            full_attr = f"{node}.{attribute}"
            if not cmds.objExists(full_attr):
                print(f"属性不存在: {full_attr}")
                return False

            # 断开目标属性上的现有输入连接，避免"已锁定或已连接"导致写入失败
            self._disconnect_incoming(full_attr)

            attr_type = cmds.getAttr(full_attr, type=True)

            if value is None or value == "":
                return True

            # ==============================================================
            # 颜色/向量值（list/tuple，长度2-4）：多方案写入 + 回读验证。
            # 不依赖 attr_type 识别，覆盖 Maya 各种颜色属性
            # （compound / float3 / Tfloat3 / V-Ray 自定义类型等），
            # 彻底解决“颜色写入后部分通道丢失（如红色=0）”的问题。
            # 若全部方案都失败，则继续走下方 attr_type 分支兜底。
            # ==============================================================
            if isinstance(value, (list, tuple)) and 2 <= len(value) <= 4:
                if self._set_color_attribute_robust(node, attribute, value):
                    return True

            if attr_type in ['double', 'float', 'int', 'long', 'short', 'byte', 'char']:
                # 如果值是列表或元组，取第一个元素
                if isinstance(value, (list, tuple)):
                    if value:
                        value = value[0]
                    else:
                        return True
                try:
                    cmds.setAttr(full_attr, float(value))
                except:
                    try:
                        cmds.setAttr(full_attr, int(value))
                    except:
                        cmds.setAttr(full_attr, value)
                return True

            elif attr_type == 'bool':
                cmds.setAttr(full_attr, bool(value))
                return True

            elif attr_type == 'string':
                cmds.setAttr(full_attr, str(value), type='string')
                return True

            elif attr_type in ['double3', 'float3', 'double2', 'float2', 'vector',
                               'Tdouble3', 'Tfloat3', 'Tdouble2', 'Tfloat2']:
                if isinstance(value, (list, tuple)):
                    # 处理嵌套列表或元组，如 [(1.0, 0.5, 0.25)]
                    if len(value) == 1 and isinstance(value[0], (list, tuple)):
                        value = value[0]

                    # 处理空列表
                    if not value:
                        return True

                    # 判断是 2 / 3 分量
                    n = 2 if attr_type.endswith('2') else 3
                    # 确保有足够的元素，不足的用0填充
                    val_list = list(value)
                    while len(val_list) < n:
                        val_list.append(0.0)
                    args = [float(v) for v in val_list[:n]]

                    try:
                        # 优先按类型匹配的 type 参数写入；如失败回退纯位置参数
                        typ = 'double2' if attr_type.endswith('2') else 'double3'
                        cmds.setAttr(full_attr, *args, type=typ)
                    except Exception as e:
                        try:
                            cmds.setAttr(full_attr, *args)
                        except Exception as e2:
                            print(f"设置向量属性失败: {e} / fallback: {e2}")
                            return True
                elif isinstance(value, str):
                    try:
                        cleaned = value.strip().strip('[]()')
                        parts = [float(x.strip()) for x in cleaned.replace(',', ' ').split() if x.strip()]
                        n = 2 if attr_type.endswith('2') else 3
                        while len(parts) < n:
                            parts.append(0.0)
                        args = parts[:n]
                        typ = 'double2' if attr_type.endswith('2') else 'double3'
                        try:
                            cmds.setAttr(full_attr, *args, type=typ)
                        except:
                            cmds.setAttr(full_attr, *args)
                    except Exception as e:
                        print(f"无法解析RGB值: {value} - {e}")
                        return True
                else:
                    try:
                        # 对于单个值，设置为RGB相同的值
                        val = float(value)
                        n = 2 if attr_type.endswith('2') else 3
                        args = [val] * n
                        typ = 'double2' if attr_type.endswith('2') else 'double3'
                        try:
                            cmds.setAttr(full_attr, *args, type=typ)
                        except:
                            cmds.setAttr(full_attr, *args)
                    except Exception as e:
                        print(f"设置向量属性失败: {e}")
                        return True
                return True

            elif attr_type == 'enum':
                try:
                    cmds.setAttr(full_attr, int(value))
                except:
                    cmds.setAttr(full_attr, value)
                return True

            elif attr_type in ['doubleLinear', 'floatLinear']:
                try:
                    cmds.setAttr(full_attr, float(value))
                except:
                    pass
                return True

            elif attr_type == 'compound':
                if isinstance(value, (list, tuple)) and len(value) >= 3:
                    try:
                        cmds.setAttr(full_attr, float(value[0]), float(value[1]), float(value[2]), type='double3')
                    except Exception:
                        # 回退：不指定 type，直接传 3 个位置参数
                        try:
                            cmds.setAttr(full_attr, float(value[0]), float(value[1]), float(value[2]))
                        except Exception:
                            return False
                return True

            elif attr_type in ['doubleArray', 'floatArray', 'intArray']:
                try:
                    if isinstance(value, str):
                        cleaned = value.strip('[]()')
                        parts = [float(x.strip()) for x in cleaned.split(',') if x.strip()]
                        cmds.setAttr(full_attr, parts, type=attr_type)
                    else:
                        cmds.setAttr(full_attr, value, type=attr_type)
                except:
                    pass
                return True

            elif attr_type == 'time':
                cmds.setAttr(full_attr, float(value))
                return True

            else:
                # 未知类型：仍然先尝试将 list/tuple 的颜色/向量值以 3 个位置参数传入，
                # 避免 setAttr(full_attr, (R,G,B)) 这样的参数错误导致只有部分通道生效
                try:
                    if isinstance(value, (list, tuple)) and 2 <= len(value) <= 4:
                        args = [float(v) for v in value]
                        cmds.setAttr(full_attr, *args)
                        return True
                    cmds.setAttr(full_attr, value)
                except Exception as e:
                    print(f"设置属性值失败 {full_attr} (类型 {attr_type}): {e}")
                    return False

            return True
        except Exception as e:
            print(f"设置属性失败 {node}.{attribute}: {e}")
            return False

    def _connect_texture(self, source_node, source_attr, target_node, target_attr):
        """连接纹理

        Args:
            source_node: 源材质节点
            source_attr: 源属性名
            target_node: 目标材质节点
            target_attr: 目标属性名

        Returns:
            bool: 是否成功连接
        """
        try:
            src_full = f"{source_node}.{source_attr}"
            dst_full = f"{target_node}.{target_attr}"

            if not cmds.objExists(src_full):
                return False
            if not cmds.objExists(dst_full):
                return False

            connections = cmds.listConnections(src_full, source=True, destination=False, plugs=True)
            if not connections:
                print(f"[纹理转换] {src_full} 无上游连接")
                return False

            src_plug = connections[0]
            src_plug_node = src_plug.split('.')[0]
            src_plug_attr = src_plug.split('.')[1] if '.' in src_plug else ''
            dst_attr_type = cmds.getAttr(dst_full, type=True)
            dst_is_float = dst_attr_type in ['float', 'double']

            # 先断开目标上的现有连接
            existing = cmds.listConnections(dst_full, source=True, destination=False, plugs=True)
            if existing:
                try:
                    cmds.disconnectAttr(existing[0], dst_full)
                except:
                    pass

            # ===== 情况1：RGB/标量贴图 -> 浮点属性（需要桥接转换） =====
            if dst_is_float and src_plug_attr in ['outColor', 'color', 'outValue']:
                # 方案A：如果是file节点，优先连接outAlpha（单通道）
                if cmds.nodeType(src_plug_node) == 'file':
                    try:
                        cmds.connectAttr(f"{src_plug_node}.outAlpha", dst_full, force=True)
                        print(f"[纹理转换] {src_plug_node}.outAlpha -> {dst_full}")
                        return True
                    except Exception as e:
                        print(f"[纹理转换] 方案A失败: {e}")

                # 方案B：标量输出（outValue）直接连接；向量输出（outColor/color）连R通道
                try:
                    if src_plug_attr == 'outValue':
                        cmds.connectAttr(src_plug, dst_full, force=True)
                        print(f"[纹理转换] {src_plug} -> {dst_full}")
                    else:
                        cmds.connectAttr(f"{src_plug_node}.{src_plug_attr}R", dst_full, force=True)
                        print(f"[纹理转换] {src_plug_node}.{src_plug_attr}R -> {dst_full}")
                    return True
                except Exception as e:
                    print(f"[纹理转换] 方案B失败: {e}")

                # 方案C：仅向量输出可做 luminance 灰度转换（标量输出直接连接即可，无需转换）
                if src_plug_attr not in ['outColor', 'color']:
                    return False
                try:
                    lum_node = cmds.shadingNode('luminance', name=f"{src_plug_node}_lum", asUtility=True)
                    # luminance 的输入属性名因 Maya 版本而异，动态探测
                    input_attr = None
                    for cand in ('color', 'inputColor', 'input', 'value'):
                        if cmds.objExists(f"{lum_node}.{cand}"):
                            input_attr = cand
                            break
                    if input_attr is None:
                        return False
                    cmds.connectAttr(src_plug, f"{lum_node}.{input_attr}", force=True)
                    cmds.connectAttr(f"{lum_node}.outValue", dst_full, force=True)
                    print(f"[纹理转换] lum节点 {lum_node}.outValue -> {dst_full}")
                    return True
                except Exception as e:
                    print(f"[纹理转换] 方案C失败: {e}")
                    return False

            # ===== 情况2：直接连接 =====
            else:
                try:
                    cmds.connectAttr(src_plug, dst_full, force=True)
                    print(f"[纹理转换] {src_plug} -> {dst_full}")
                    return True
                except Exception as e:
                    print(f"[纹理转换] 直接连接失败 {src_plug} -> {dst_full}: {e}")
                    return False

        except Exception as e:
            print(f"[纹理转换] 连接失败 {source_node}.{source_attr} -> {target_node}.{target_attr}: {e}")
            return False

    def _connect_texture_with_remap(self, source_node, source_attr, target_node, target_attr, transform_name, parameters=None):
        """为贴图连接插入remapValue转换节点

        当源属性有纹理连接且需要数学转换时，在连接中插入remapValue节点。
        将转换函数预计算为采样点表，由Maya在渲染时插值执行。

        Args:
            source_node: 源材质节点
            source_attr: 源属性名
            target_node: 目标材质节点
            target_attr: 目标属性名
            transform_name: 转换函数名称
            parameters: 可选的转换函数参数字典（来自映射文件 mapping 的 "parameters" 字段）

        Returns:
            bool: 是否成功
        """
        try:
            src_full = f"{source_node}.{source_attr}"
            dst_full = f"{target_node}.{target_attr}"

            if not cmds.objExists(src_full) or not cmds.objExists(dst_full):
                return False

            connections = cmds.listConnections(src_full, source=True, destination=False, plugs=True)
            if not connections:
                return False

            input_min, input_max, samples = precompute_remap_samples(transform_name, parameters=parameters)
            if not samples:
                return False

            src_plug = connections[0]
            dst_attr_type = cmds.getAttr(dst_full, type=True)
            dst_is_float = dst_attr_type in ['float', 'double']

            if not dst_is_float:
                return False

            existing = cmds.listConnections(dst_full, source=True, destination=False, plugs=True)
            if existing:
                try:
                    cmds.disconnectAttr(existing[0], dst_full)
                except:
                    pass

            remap_node = cmds.shadingNode(
                'remapValue', asUtility=True,
                name=f"{source_node}_{source_attr}_remap"
            )

            cmds.setAttr(f"{remap_node}.inputMin", input_min)
            cmds.setAttr(f"{remap_node}.inputMax", input_max)
            cmds.setAttr(f"{remap_node}.outputMin", samples[0][1])
            cmds.setAttr(f"{remap_node}.outputMax", samples[-1][1])

            # 只使用节点自带的首尾两个插值点（identity 0→0、1→1），
            # 配合 inputMin/inputMax + outputMin/outputMax 即完成线性映射。
            # 不额外扩展 value 数组，避免访问不存在的插值点导致 setAttr 失败。
            # 注意：remapValue 没有 interpolation 属性（那是 remapColor 的），
            # 插值类型由每个 value[i].value_Interp 控制，默认即线性，无需设置。
            cmds.setAttr(f"{remap_node}.value[0].value_Position", 0.0)
            cmds.setAttr(f"{remap_node}.value[0].value_FloatValue", 0.0)
            cmds.setAttr(f"{remap_node}.value[1].value_Position", 1.0)
            cmds.setAttr(f"{remap_node}.value[1].value_FloatValue", 1.0)

            cmds.connectAttr(src_plug, f"{remap_node}.inputValue", force=True)
            cmds.connectAttr(f"{remap_node}.outValue", dst_full, force=True)

            print(f"[纹理转换] remapValue {remap_node} ({transform_name}): {src_plug} -> {dst_full}")
            return True

        except Exception as e:
            print(f"[纹理转换] remapValue转换失败 {source_node}.{source_attr} -> {target_node}.{target_attr}: {e}")
            return False

    def _connect_texture_transform(self, source_node, source_attr, target_node, target_attr, transform_name, parameters=None):
        """按转换函数类型选择合适的节点处理纹理连接

        优先 remapValue（浮点输出类函数）；颜色运算类用 multiplyDivide / plusMinusAverage /
        blendColors / clamp；通道类用 vectorComponent / luminance。
        全部失败返回 False，调用方回退到数值转换（纹理被拍平）。

        Args:
            source_node: 源材质节点
            source_attr: 源属性名
            target_node: 目标材质节点
            target_attr: 目标属性名
            transform_name: 转换函数名称（中英文均可）
            parameters: 可选的转换函数参数字典（来自映射文件 mapping 的 "parameters" 字段）

        Returns:
            bool: 是否成功
        """
        try:
            # 1) 可 remap 的函数：浮点目标用 remapValue（线性近似）
            if transform_name in TRANSFORM_INPUT_RANGES:
                if self._connect_texture_with_remap(source_node, source_attr, target_node, target_attr, transform_name, parameters):
                    return True

            src_full = f"{source_node}.{source_attr}"
            dst_full = f"{target_node}.{target_attr}"
            if not cmds.objExists(src_full) or not cmds.objExists(dst_full):
                return False
            connections = cmds.listConnections(src_full, source=True, destination=False, plugs=True)
            if not connections:
                return False
            src_plug = connections[0]

            func = MATERIAL_CONVERSION_FUNCTIONS.get(transform_name)
            if func is None:
                return False
            fname = func.__name__
            params = parameters or {}

            dst_attr_type = cmds.getAttr(dst_full, type=True)
            dst_is_float = dst_attr_type in ['float', 'double']

            # 断开目标上的现有连接
            existing = cmds.listConnections(dst_full, source=True, destination=False, plugs=True)
            if existing:
                try:
                    cmds.disconnectAttr(existing[0], dst_full)
                except Exception:
                    pass

            # ===== 颜色乘/除标量：multiplyDivide =====
            if fname in ('color_mul_scalar', 'color_div_scalar'):
                scalar = float(params.get('scalar', 1.0)) or 1.0
                md = cmds.shadingNode('multiplyDivide', asUtility=True,
                                      name=f"{source_node}_{source_attr}_mul")
                if fname == 'color_div_scalar':
                    cmds.setAttr(f"{md}.operation", 2)  # 2 = Divide
                cmds.setAttr(f"{md}.input2X", scalar)
                cmds.setAttr(f"{md}.input2Y", scalar)
                cmds.setAttr(f"{md}.input2Z", scalar)
                cmds.connectAttr(src_plug, f"{md}.input1", force=True)
                if dst_is_float:
                    cmds.connectAttr(f"{md}.outputX", dst_full, force=True)
                else:
                    cmds.connectAttr(f"{md}.output", dst_full, force=True)
                print(f"[纹理转换] multiplyDivide {md}: {src_plug} -> {dst_full}")
                return True

            # ===== 颜色相加：plusMinusAverage（用 input3D，兼容颜色） =====
            if fname == 'color_add':
                color2 = params.get('color2', [1.0, 1.0, 1.0])
                pma = cmds.shadingNode('plusMinusAverage', asUtility=True,
                                       name=f"{source_node}_{source_attr}_add")
                cmds.setAttr(f"{pma}.operation", 1)  # 1 = Sum
                cmds.connectAttr(src_plug, f"{pma}.input3D[0]", force=True)
                for i, suffix in enumerate(('x', 'y', 'z')):
                    cmds.setAttr(f"{pma}.input3D[1].input3D{suffix}", float(color2[i]))
                if dst_is_float:
                    cmds.connectAttr(f"{pma}.output3Dx", dst_full, force=True)
                else:
                    cmds.connectAttr(f"{pma}.output3D", dst_full, force=True)
                print(f"[纹理转换] plusMinusAverage {pma}: {src_plug} -> {dst_full}")
                return True

            # ===== 颜色插值：blendColors =====
            if fname == 'color_lerp':
                color2 = params.get('color2', [1.0, 1.0, 1.0])
                t = float(params.get('t', 0.5))
                bc = cmds.shadingNode('blendColors', asUtility=True,
                                      name=f"{source_node}_{source_attr}_lerp")
                cmds.connectAttr(src_plug, f"{bc}.color1", force=True)
                for i, c in enumerate(('R', 'G', 'B')):
                    cmds.setAttr(f"{bc}.color2{c}", float(color2[i]))
                cmds.setAttr(f"{bc}.blender", t)
                if dst_is_float:
                    cmds.connectAttr(f"{bc}.outputR", dst_full, force=True)
                else:
                    cmds.connectAttr(f"{bc}.output", dst_full, force=True)
                print(f"[纹理转换] blendColors {bc}: {src_plug} -> {dst_full}")
                return True

            # ===== 限制范围：clamp =====
            if fname == 'clamp':
                min_val = float(params.get('min_val', 0.0))
                max_val = float(params.get('max_val', 1.0))
                cp = cmds.shadingNode('clamp', asUtility=True,
                                      name=f"{source_node}_{source_attr}_clamp")
                cmds.connectAttr(src_plug, f"{cp}.input", force=True)
                for c in ('R', 'G', 'B'):
                    cmds.setAttr(f"{cp}.min{c}", min_val)
                    cmds.setAttr(f"{cp}.max{c}", max_val)
                if dst_is_float:
                    cmds.connectAttr(f"{cp}.outputR", dst_full, force=True)
                else:
                    cmds.connectAttr(f"{cp}.output", dst_full, force=True)
                print(f"[纹理转换] clamp {cp}: {src_plug} -> {dst_full}")
                return True

            # ===== 取单通道：直接连颜色通道（Maya 无 vectorComponent 节点） =====
            if fname in ('rgb_to_channel', 'rgb_to_red', 'rgb_to_green', 'rgb_to_blue'):
                if not dst_is_float:
                    return False
                index = {'rgb_to_red': 0, 'rgb_to_green': 1, 'rgb_to_blue': 2}.get(fname)
                if index is None:
                    ch = str(params.get('channel', 'r')).lower()
                    index = {'r': 0, 'g': 1, 'b': 2, 'x': 0, 'y': 1, 'z': 2,
                             'red': 0, 'green': 1, 'blue': 2}.get(ch, 0)
                channel_suffix = ('R', 'G', 'B')[index]
                src_plug_attr = src_plug.split('.')[-1]
                if src_plug_attr in ('outValue', 'outAlpha'):
                    # 源已是单通道输出，直接连接
                    cmds.connectAttr(src_plug, dst_full, force=True)
                    print(f"[纹理转换] 通道直连 {src_plug} -> {dst_full}")
                else:
                    # 颜色输出取指定通道，如 outColorR
                    cmds.connectAttr(f"{src_plug}{channel_suffix}", dst_full, force=True)
                    print(f"[纹理转换] 取通道 {src_plug}{channel_suffix} -> {dst_full}")
                return True

            # ===== 转灰度：luminance（输出浮点，仅浮点目标可用） =====
            if fname == 'rgb_to_grayscale':
                if not dst_is_float:
                    return False
                lum = cmds.shadingNode('luminance', asUtility=True,
                                       name=f"{source_node}_{source_attr}_lum")
                # luminance 的输入属性名因 Maya 版本而异，动态探测
                input_attr = None
                for cand in ('color', 'inputColor', 'input', 'value'):
                    if cmds.objExists(f"{lum}.{cand}"):
                        input_attr = cand
                        break
                if input_attr is None:
                    attrs = cmds.listAttr(lum, write=True) or []
                    for a in attrs:
                        if a.lower() in ('color', 'inputcolor', 'input', 'value', 'inputvalue'):
                            input_attr = a
                            break
                if input_attr is None:
                    return False
                cmds.connectAttr(src_plug, f"{lum}.{input_attr}", force=True)
                cmds.connectAttr(f"{lum}.outValue", dst_full, force=True)
                print(f"[纹理转换] luminance {lum}.{input_attr}: {src_plug} -> {dst_full}")
                return True

            return False
        except Exception as e:
            print(f"[纹理转换] 节点处理失败 {transform_name}: {e}")
            return False

    def _replace_source_material(self, source, target):
        """原位替换材质：删除源材质，将新材质改回源材质名，并重连 SG 保持网格赋值。

        Args:
            source: 源材质节点名
            target: 新材质节点名

        Returns:
            str: 替换后的目标材质名
        """
        import maya.cmds as cmds
        # 记录源材质到 shadingEngine 的连接（surfaceShader/volumeShader 等），
        # 避免删除源材质后 SG 输入断开导致物体丢失材质赋值
        sg_plugs = []  # [(sg名, sg输入属性)]
        try:
            for plug in (cmds.listConnections(source, destination=True, plugs=True,
                                              type="shadingEngine") or []):
                if "." not in plug:
                    continue
                sg_name, sg_attr = plug.rsplit(".", 1)
                sg_plugs.append((sg_name, sg_attr))
        except Exception:
            pass

        try:
            cmds.delete(source)
        except Exception:
            return target
        new_name = target
        try:
            new_name = cmds.rename(target, source)
        except Exception:
            pass

        # 重连 SG：目标材质 outColor → SG 的对应输入属性
        for sg, sg_attr in sg_plugs:
            try:
                if not cmds.objExists(sg):
                    continue
                cmds.connectAttr(f"{new_name}.outColor", f"{sg}.{sg_attr}", force=True)
            except Exception as e:
                print(f"[替换材质] 重连 {sg}.{sg_attr} 失败: {e}")
        return new_name

    def _report_failed_materials(self, failed_materials):
        """转换完成后报告失败材质，可选择在Maya中选中

        Args:
            failed_materials: 失败的材质节点名列表
        """
        if not failed_materials:
            return

        msg = t("qtool.matconv.msg.fail_list_text", count=len(failed_materials)) + ":\n\n"
        msg += "\n".join(f"  - {m}" for m in failed_materials)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(t("qtool.matconv.msg.fail_list_title"))
        box.setText(msg)
        select_btn = box.addButton(t("qtool.matconv.btn.select_failed"), QMessageBox.AcceptRole)
        box.addButton(t("common.close"), QMessageBox.RejectRole)
        box.exec()

        if box.clickedButton() == select_btn:
            try:
                cmds.select(failed_materials, replace=True)
                print(f"已在Maya中选中 {len(failed_materials)} 个失败材质")
            except Exception as e:
                print(f"选中失败材质出错: {e}")

    def apply_zmetal_to_material(self, source_node_data, source_scene_material, target_material, copy_textures=True):
        """将 zmetal 源材质节点数据应用到已存在的目标材质（右键「应用材质参数」）。

        - 同类型：属性直拷 + 贴图/上游节点重连
        - 不同类型：按 {source_type}_{target_type}.mmap 映射转换后应用

        Args:
            source_node_data: zmetal 中源材质的节点数据（node_type + attrs）
            source_scene_material: 源材质在场景中的临时节点名（其贴图连接可复用）
            target_material: 目标材质节点名

        Returns:
            bool: 是否有属性被应用
        """
        import maya.cmds as cmds
        source_type = source_node_data.get('node_type', '') or ""
        if not source_type:
            return False
        target_type = cmds.nodeType(target_material)

        if source_type == target_type:
            mappings = None  # 同类型直拷
        else:
            path = self.find_conversion_path(source_type, target_type)
            if not path:
                print(f"[应用材质参数] 缺少映射: {source_type} -> {target_type}")
                return False
            try:
                with open(path[0][0], 'r', encoding='utf-8') as f:
                    mappings = json.load(f).get('mappings', [])
            except Exception as e:
                print(f"[应用材质参数] 加载映射失败: {e}")
                return False

        # 内部/不可写属性跳过（同类型直拷时避免把 zmetal 的 VP2 内部量写坏目标）
        skip = {'message', 'caching', 'isHistoricallyInteresting', 'nodeState',
                'hardwareShader', 'pointCamera', 'triangleNormalCamera',
                'primitiveId', 'instanceId', 'base', 'raySampler', 'frozen'}

        attrs = source_node_data.get('attrs', {})
        applied = 0
        for attr, adata in attrs.items():
            if attr in skip:
                continue
            dst_attr, transform, parameters = attr, "", {}
            if mappings is not None:
                entry = next((m for m in mappings if m.get('source_attribute') == attr), None)
                if entry is None:
                    continue
                dst_attr = entry.get('target_attribute') or ""
                transform = entry.get('transform', "")
                parameters = entry.get('parameters') or {}
            if not dst_attr:
                continue
            full = f"{target_material}.{dst_attr}"
            if not cmds.objExists(full):
                continue
            if self._is_color_child_attr(target_material, dst_attr):
                continue
            if adata.get('type') == 'connection' and copy_textures:
                ok = self._connect_texture(source_scene_material, attr, target_material, dst_attr) \
                    if not transform else self._connect_texture_transform(
                        source_scene_material, attr, target_material, dst_attr, transform, parameters)
                if ok:
                    applied += 1
            else:
                value = adata.get('value')
                if value is None:
                    continue
                if transform:
                    value = apply_conversion(value, transform, parameters=parameters)
                if self._set_attribute_value(target_material, dst_attr, value):
                    applied += 1
        print(f"[应用材质参数] 已应用 {applied} 个属性到 {target_material} ({source_type} -> {target_type})")
        return applied > 0

    def convert_material(self, source_material, target_material_type, copy_textures=True):
        """转换单个材质

        Args:
            source_material: 源材质节点
            target_material_type: 目标材质类型
            copy_textures: 是否复制纹理连接

        Returns:
            (target_material, success_count, failed_count, default_count, texture_count) 或 None
        """
        if not self.mapping_data:
            return None

        try:
            source_type = cmds.nodeType(source_material)

            target_material_name = _converted_material_name(source_material, target_material_type)
            # 使用shadingNode创建材质，这样会自动在Hypershade中显示
            target_material = cmds.shadingNode(target_material_type, name=target_material_name, asShader=True)

            mappings = self.mapping_data.get("mappings", [])

            success_count = 0
            failed_count = 0
            default_count = 0
            texture_count = 0

            for mapping in mappings:
                src_attr = mapping["source_attribute"]
                dst_attr = mapping["target_attribute"]
                default_value_str = mapping.get("default_value", "")
                transform_name = mapping.get("transform", "")
                parameters = mapping.get("parameters") or {}

                if not dst_attr:
                    continue

                if not cmds.objExists(f"{target_material}.{dst_attr}"):
                    print(f"目标属性不存在: {target_material}.{dst_attr}")
                    failed_count += 1
                    continue

                # 跳过颜色子通道目标（如 diffuseColorR）：颜色应通过父属性整体写入，
                # 单独写子通道会被 AI 生成的垃圾映射覆盖（如 aiMatteColorA -> diffuseColorR 把红色写 0）
                if self._is_color_child_attr(target_material, dst_attr):
                    print(f"[跳过] 颜色子通道目标 {target_material}.{dst_attr}，由父属性统一处理")
                    continue

                value = None
                src_exists = bool(src_attr) and cmds.objExists(f"{source_material}.{src_attr}")
                if src_exists:
                    src_full = f"{source_material}.{src_attr}"
                    # 检测连接：上游输入（贴图等）或下游输出（AOV 等）
                    has_connection = bool(cmds.listConnections(src_full, source=True, destination=False, plugs=True)) or \
                                     bool(cmds.listConnections(src_full, source=False, destination=True, plugs=True))

                    if copy_textures and has_connection:
                        if transform_name:
                            if self._connect_texture_transform(source_material, src_attr,
                                                                target_material, dst_attr,
                                                                transform_name, parameters):
                                success_count += 1
                                texture_count += 1
                                continue
                        else:
                            if self._connect_texture(source_material, src_attr, target_material, dst_attr):
                                success_count += 1
                                texture_count += 1
                                continue

                    value = self._get_attribute_value(source_material, src_attr)
                    # 连接属性读取的值可能为空字符串，视为无值（走默认值，避免空值覆盖）
                    if value is None or (isinstance(value, str) and not value.strip()):
                        value = None

                if value is None:
                    if default_value_str:
                        value = self._parse_default_value(default_value_str)
                        if value is not None:
                            print(f"[默认值] 使用默认值 {value} 应用到 {target_material}.{dst_attr}")
                            default_count += 1
                        else:
                            print(f"[默认值] 默认值解析失败: {default_value_str}")
                            failed_count += 1
                            continue
                    else:
                        try:
                            value = cmds.getAttr(f"{target_material}.{dst_attr}")
                            # 系统默认值为空字符串时无实际可设置的值，跳过
                            if isinstance(value, str) and not value.strip():
                                print(f"[系统默认值] {target_material}.{dst_attr} 默认值为空，跳过")
                                failed_count += 1
                                continue
                            print(f"[系统默认值] 使用系统默认值 {value} 应用到 {target_material}.{dst_attr}")
                        except Exception as e:
                            print(f"[系统默认值] 获取系统默认值失败: {e}")
                            failed_count += 1
                            continue

                # 应用转换函数
                if transform_name and value is not None:
                    converted_value = apply_conversion(value, transform_name, parameters=parameters)
                    if converted_value != value:
                        print(f"[转换] {transform_name}: {value} -> {converted_value}")
                    value = converted_value

                if value is not None:
                    if self._set_attribute_value(target_material, dst_attr, value):
                        if src_attr and cmds.objExists(f"{source_material}.{src_attr}"):
                            print(f"[复制] {source_material}.{src_attr} = {value} -> {target_material}.{dst_attr}")
                        success_count += 1
                    else:
                        failed_count += 1
                else:
                    failed_count += 1

            try:
                cmds.setAttr(f"{target_material}.name", source_material, type='string')
            except:
                pass

            return target_material, success_count, failed_count, default_count, texture_count

        except Exception as e:
            print(f"转换材质失败: {e}")
            return None

    def execute_conversion(self):
        """根据配置的映射类型列表执行转换"""
        # 获取配置的映射类型列表
        mappings = self.get_configured_mappings()
        if not mappings:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.configure_mapping_first"))
            return

        # 检查是否是全局转换（源为空）
        is_global_conversion = False
        global_target_type = ""
        if len(mappings) == 1 and mappings[0][0] == "":
            is_global_conversion = True
            global_target_type = mappings[0][1]

        # 收集所有需要转换的材质及其目标类型
        materials_to_process = []
        if is_global_conversion:
            # 全局转换：所有材质都转换为目标类型
            all_materials = cmds.ls(materials=True)
            for mat in all_materials:
                mat_type = cmds.nodeType(mat)
                # 跳过已经是目标类型的材质
                if mat_type != global_target_type:
                    materials_to_process.append((mat, mat_type, global_target_type))
        else:
            # 常规转换：根据源类型匹配
            for source_type, target_type in mappings:
                # 查找所有该类型的材质
                all_materials = cmds.ls(materials=True)
                for mat in all_materials:
                    if cmds.nodeType(mat) == source_type:
                        # 检查是否已经在处理列表中
                        already_processed = False
                        for existing_mat, existing_source, existing_target in materials_to_process:
                            if existing_mat == mat:
                                already_processed = True
                                break
                        if not already_processed:
                            materials_to_process.append((mat, source_type, target_type))

        if not materials_to_process:
            QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.no_materials_to_convert"))
            return

        # 统计信息
        total_converted = 0
        total_objects = 0
        total_failed = 0
        failed_materials = []

        # 预先获取UI设置，避免在循环中访问可能已删除的对象
        try:
            copy_textures = self.copy_textures_check.isChecked()
        except RuntimeError:
            copy_textures = True  # 默认值
        try:
            keep_original = self.keep_original_check.isChecked()
        except RuntimeError:
            keep_original = True  # 默认值
        try:
            fallback_default = self.fallback_default_check.isChecked()
        except RuntimeError:
            fallback_default = False  # 默认值

        # 执行转换
        for material, source_type, target_type in materials_to_process:
            print(f"\n开始转换 {material} ({source_type} -> {target_type})")

            # 查找使用该材质的物体
            objects_with_material = []
            shading_engines = cmds.listConnections(material, type='shadingEngine')
            if shading_engines:
                for se in shading_engines:
                    members = cmds.sets(se, query=True)
                    if members:
                        for member in members:
                            if cmds.objectType(member) == 'mesh':
                                transforms = cmds.listRelatives(member, parent=True, fullPath=True)
                                if transforms and transforms[0] not in objects_with_material:
                                    objects_with_material.append(transforms[0])

            if not objects_with_material:
                all_transforms = cmds.ls(dag=True, type='transform')
                for obj in all_transforms:
                    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
                    if shapes:
                        for shape in shapes:
                            ses = cmds.listConnections(shape, type='shadingEngine')
                            if ses:
                                for se in ses:
                                    connected = cmds.listConnections(se, source=True, destination=False)
                                    if connected and material in connected:
                                        if obj not in objects_with_material:
                                            objects_with_material.append(obj)

            # 通过转换路径执行（支持直连和中转）
            result = self._convert_material_via_path(material, source_type, target_type, copy_textures, fallback_default)

            if result:
                target_material, success_count, fail_count, default_count, texture_count = result
                if objects_with_material:
                    self.assign_material_to_objects(target_material, objects_with_material)
                    total_objects += len(objects_with_material)
                if not keep_original:
                    target_material = self._replace_source_material(material, target_material)
                total_converted += 1
                total_failed += fail_count
            else:
                print(f"材质 {material} 转换失败")
                failed_materials.append(material)

        self.status_label.setText(t("qtool.matconv.status.conversion_done"))
        print(f"\n{'='*60}")
        print(f"批量转换完成！")
        print(f"转换材质数: {total_converted}")
        print(f"应用对象数: {total_objects}")
        print(f"失败属性: {total_failed}")
        if failed_materials:
            print(f"失败材质数: {len(failed_materials)}")
            for fm in failed_materials:
                print(f"  - {fm}")
        print(f"{'='*60}")

        # 报告失败材质并支持在Maya中选中
        self._report_failed_materials(failed_materials)

    def get_configured_mappings(self):
        """获取配置的材质类型映射列表"""
        mappings = []
        has_empty_source = False
        
        # 收集所有映射
        for row in range(self.mapping_type_table.rowCount()):
            source_type = self._get_cell_text(row, 1)
            target_type = self._get_cell_text(row, 2)
            if target_type:
                mappings.append((source_type, target_type))
                if not source_type:
                    has_empty_source = True
        
        # 如果只有一行且源为空，返回特殊标记
        if len(mappings) == 1 and has_empty_source:
            return [('', mappings[0][1])]
        
        # 过滤掉空源的行，只保留有明确源类型的映射
        filtered_mappings = [(src, dst) for src, dst in mappings if src]
        return filtered_mappings

    def convert_selection(self):
        """转换选择中的物体/材质"""
        # 获取配置的映射类型列表
        mappings = self.get_configured_mappings()
        if not mappings:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.configure_mapping_first"))
            return

        selection = cmds.ls(selection=True)
        if not selection:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.select_object_or_material_in_maya"))
            return

        # 获取选择中的材质
        materials = self.get_materials_from_selection(selection)
        if not materials:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.no_convertible_material_in_selection"))
            return

        # 检查是否是全局转换（源为空）
        is_global_conversion = False
        global_target_type = ""
        if len(mappings) == 1 and mappings[0][0] == "":
            is_global_conversion = True
            global_target_type = mappings[0][1]

        # 按配置的映射类型过滤
        materials_by_config = {}
        if is_global_conversion:
            # 全局转换：所有选择的材质都转换为目标类型
            for mat in materials:
                mat_type = cmds.nodeType(mat)
                # 跳过已经是目标类型的材质
                if mat_type != global_target_type:
                    key = (mat_type, global_target_type)
                    if key not in materials_by_config:
                        materials_by_config[key] = []
                    materials_by_config[key].append(mat)
        else:
            # 常规转换：根据源类型匹配
            for mat in materials:
                mat_type = cmds.nodeType(mat)
                for source_type, target_type in mappings:
                    if mat_type == source_type:
                        if (source_type, target_type) not in materials_by_config:
                            materials_by_config[(source_type, target_type)] = []
                        materials_by_config[(source_type, target_type)].append(mat)
                        break

        if not materials_by_config:
            if is_global_conversion:
                QMessageBox.information(self, t("common.tip"), t("qtool.matconv.msg.already_target_type"))
            else:
                QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.mapping_mismatch"))
            return

        total_converted = 0
        total_objects = 0
        failed_materials = []

        # 预先获取UI设置，避免在循环中访问可能已删除的对象
        try:
            copy_textures = self.copy_textures_check.isChecked()
        except RuntimeError:
            copy_textures = True  # 默认值
        try:
            keep_original = self.keep_original_check.isChecked()
        except RuntimeError:
            keep_original = True  # 默认值
        try:
            fallback_default = self.fallback_default_check.isChecked()
        except RuntimeError:
            fallback_default = False  # 默认值

        # 执行转换
        for (source_type, target_type), mat_list in materials_by_config.items():
            for material in mat_list:
                print(f"\n开始转换 {material} ({source_type} -> {target_type})")

                # 查找使用该材质的物体
                objects_with_material = []
                shading_engines = cmds.listConnections(material, type='shadingEngine')
                if shading_engines:
                    for se in shading_engines:
                        members = cmds.sets(se, query=True)
                        if members:
                            for member in members:
                                if cmds.objectType(member) == 'mesh':
                                    transforms = cmds.listRelatives(member, parent=True, fullPath=True)
                                    if transforms and transforms[0] not in objects_with_material:
                                        objects_with_material.append(transforms[0])

                if not objects_with_material:
                    all_transforms = cmds.ls(dag=True, type='transform')
                    for obj in all_transforms:
                        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
                        if shapes:
                            for shape in shapes:
                                ses = cmds.listConnections(shape, type='shadingEngine')
                                if ses:
                                    for se in ses:
                                        connected = cmds.listConnections(se, source=True, destination=False)
                                        if connected and material in connected:
                                            if obj not in objects_with_material:
                                                objects_with_material.append(obj)

                # 通过转换路径执行（支持直连和中转）
                result = self._convert_material_via_path(material, source_type, target_type, copy_textures, fallback_default)

                if result:
                    target_material, success_count, fail_count, default_count, texture_count = result
                    if objects_with_material:
                        self.assign_material_to_objects(target_material, objects_with_material)
                        total_objects += len(objects_with_material)
                    if not keep_original:
                        target_material = self._replace_source_material(material, target_material)
                    total_converted += 1
                else:
                    print(f"材质 {material} 转换失败")
                    failed_materials.append(material)

        self.status_label.setText(t("qtool.matconv.status.conversion_done"))
        print(f"\n{'='*60}")
        print(f"选择转换完成！")
        print(f"转换材质数: {total_converted}")
        print(f"应用对象数: {total_objects}")
        if failed_materials:
            print(f"失败材质数: {len(failed_materials)}")
            for fm in failed_materials:
                print(f"  - {fm}")
        print(f"{'='*60}")

        # 报告失败材质并支持在Maya中选中
        self._report_failed_materials(failed_materials)

    def assign_material_to_objects(self, material, objects):
        """将材质应用到对象"""
        if not objects:
            print("没有对象需要应用材质")
            return

        for obj in objects:
            # 确保对象存在
            if not cmds.objExists(obj):
                print(f"对象不存在: {obj}")
                continue

            # 获取对象的shape
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                print(f"对象没有shape: {obj}")
                continue

            for shape in shapes:
                # 检查是否是可着色的shape
                shape_type = cmds.objectType(shape)
                if shape_type not in ['mesh', 'nurbsSurface', 'subdiv', 'plane']:
                    continue

                # 查找该shape当前连接的shadingEngine
                shading_engines = cmds.listConnections(shape, type='shadingEngine')

                if shading_engines:
                    # 找到shadingEngine，将新材质连接到它的surfaceShader
                    for se in shading_engines:
                        try:
                            # 断开旧的材质连接
                            old_connections = cmds.listConnections(f"{se}.surfaceShader", source=True, destination=False)
                            if old_connections:
                                for old_mat in old_connections:
                                    try:
                                        cmds.disconnectAttr(f"{old_mat}.outColor", f"{se}.surfaceShader")
                                    except:
                                        pass

                            # 连接新材质
                            cmds.connectAttr(f"{material}.outColor", f"{se}.surfaceShader", force=True)
                            print(f"已将材质 {material} 应用到: {obj}")
                        except Exception as e:
                            print(f"连接材质失败: {e}")
                else:
                    # 没有shadingEngine，需要创建一个
                    try:
                        # 创建新的shadingEngine
                        se = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{material}SG")
                        # 连接材质到shadingEngine
                        cmds.connectAttr(f"{material}.outColor", f"{se}.surfaceShader", force=True)
                        # 将shape添加到shadingEngine
                        cmds.sets(shape, edit=True, forceElement=se)
                        print(f"已创建shadingEngine并应用材质 {material} 到: {obj}")
                    except Exception as e:
                        print(f"创建shadingEngine并应用材质失败: {e}")

    def get_materials_from_selection(self, selection):
        """从选择中获取材质列表"""
        materials = []
        for item in selection:
            # 检查是否是材质节点
            if _is_material_node(item):
                materials.append(item)
            else:
                # 尝试从物体获取材质
                shapes = cmds.listRelatives(item, shapes=True)
                if shapes:
                    for shape in shapes:
                        # 获取连接到该shape的shadingEngine
                        shading_engines = cmds.listConnections(shape, type='shadingEngine')
                        if shading_engines:
                            for se in shading_engines:
                                # 从shadingEngine获取连接的材质
                                connected = cmds.listConnections(se)
                                if connected:
                                    for conn in connected:
                                        if _is_material_node(conn):
                                            materials.append(conn)
        return list(set(materials))

    def get_objects_from_selection(self, selection):
        """从选择中获取所有物体"""
        objects = []
        for item in selection:
            # 如果是材质，检查它连接到的所有物体
            if _is_material_node(item):
                # 查找使用该材质的所有物体
                shading_engines = cmds.listConnections(item, type='shadingEngine')
                if shading_engines:
                    for se in shading_engines:
                        connected = cmds.listConnections(se, destination=True)
                        if connected:
                            for conn in connected:
                                # 检查是否是shape节点
                                if cmds.objectType(conn) == 'mesh' or cmds.objectType(conn) == 'nurbsSurface':
                                    parent = cmds.listRelatives(conn, parent=True)
                                    if parent:
                                        objects.append(parent[0])
                                elif cmds.objectType(conn) == 'shadingEngine':
                                    pass
                                else:
                                    # 可能是transform
                                    if cmds.nodeType(conn) == 'transform':
                                        objects.append(conn)
            else:
                # 普通物体
                if cmds.nodeType(item) == 'transform':
                    objects.append(item)
                elif cmds.listRelatives(item, parent=True):
                    parent = cmds.listRelatives(item, parent=True)[0]
                    if cmds.nodeType(parent) == 'transform':
                        objects.append(parent)
        return list(set(objects))

    def _find_mapping_for_source_type(self, source_type):
        """根据源材质类型查找映射文件

        在映射文件夹和 fallback 目录中查找：
        1. 遍历所有目标材质文件夹
        2. 查找 {source_type}_{target_type}.mmap 文件
        """
        all_search_dirs = [self.mapping_base_dir]
        for fb_dir in getattr(self, '_fallback_mapping_dirs', []):
            if os.path.exists(fb_dir):
                all_search_dirs.append(fb_dir)

        target_types_seen = set()
        for search_dir in all_search_dirs:
            if not os.path.exists(search_dir):
                continue

            # 平铺结构：直接扫描 {source_type}_*.mmap 文件
            for fname in os.listdir(search_dir):
                if fname.startswith(f"{source_type}_") and fname.endswith(".mmap"):
                    return os.path.join(search_dir, fname)

            # 子目录结构：遍历目标材质类型目录
            for target_type in os.listdir(search_dir):
                target_path = os.path.join(search_dir, target_type)
                if not os.path.isdir(target_path):
                    continue
                if target_type in target_types_seen:
                    continue
                target_types_seen.add(target_type)

                filename = f"{source_type}_{target_type}.mmap"
                filepath = os.path.join(target_path, filename)
                if os.path.exists(filepath):
                    return filepath

        return None

    def batch_conversion(self):
        """批量转换材质"""
        if not self.mapping_data or not self.target_material_type:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.load_mapping_first"))
            return

        source_type = self.mapping_data.get("source_type", "")

        # 获取所有匹配类型的材质
        materials = cmds.ls(materials=True)
        filtered_materials = [m for m in materials if cmds.nodeType(m) == source_type]

        if not filtered_materials:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matconv.msg.no_material_of_type", source_type=source_type))
            return

        # 确认批量转换
        reply = QMessageBox.question(self, t("qtool.matconv.msg.confirm_batch_title"),
                                     t("qtool.matconv.msg.confirm_batch_text", count=len(filtered_materials), source_type=source_type),
                                     QMessageBox.Yes | QMessageBox.No)

        if reply != QMessageBox.Yes:
            return

        total_success = 0
        total_failed = 0
        total_default = 0
        total_texture = 0
        converted_materials = []

        # 预先获取UI设置，避免在循环中访问可能已删除的对象
        try:
            copy_textures = self.copy_textures_check.isChecked()
        except RuntimeError:
            copy_textures = True  # 默认值
        try:
            keep_original = self.keep_original_check.isChecked()
        except RuntimeError:
            keep_original = True  # 默认值

        self.status_label.setText(t("qtool.matconv.status.batch_converting", count=len(filtered_materials)))
        self.repaint()

        for i, material in enumerate(filtered_materials):
            self.status_label.setText(t("qtool.matconv.status.converting", i=i + 1, total=len(filtered_materials), material=material))
            self.repaint()

            result = self.convert_material(material, self.target_material_type, copy_textures)
            if result:
                target_material, success_count, failed_count, default_count, texture_count = result
                converted_materials.append((material, target_material))
                total_success += success_count
                total_failed += failed_count
                total_default += default_count
                total_texture += texture_count

        # 更新材质连接
        if converted_materials and not keep_original:
            all_objects = cmds.ls(geometry=True)
            for material, target_material in converted_materials:
                objects_using_material = []

                # 查找使用该材质的所有对象
                for obj in all_objects:
                    shapes = cmds.listRelatives(obj, shapes=True)
                    if shapes:
                        for shape in shapes:
                            shading_engines = cmds.listConnections(shape, type='shadingEngine')
                            if shading_engines:
                                for se in shading_engines:
                                    materials = cmds.ls(cmds.listConnections(se), materials=True)
                                    if material in materials:
                                        objects_using_material.append(obj)

                # 重新分配材质
                if objects_using_material:
                    self.assign_material_to_objects(target_material, objects_using_material)

                # 原位替换：删除源材质，新材质改回源材质名
                target_material = self._replace_source_material(material, target_material)

        self.status_label.setText(t("qtool.matconv.status.batch_conversion_done"))

        result_text = t("qtool.matconv.msg.batch_result",
                        converted=len(converted_materials),
                        success=total_success,
                        default=total_default,
                        texture=total_texture,
                        failed=total_failed)

        QMessageBox.information(self, t("msg.success"), result_text)


# 主启动函数
def show_material_converter():
    """显示材质转换工具窗口"""
    global converter_window
    if 'converter_window' in globals() and converter_window is not None:
        try:
            converter_window.close()
            converter_window.deleteLater()
        except:
            pass
        converter_window = None

    for widget in QtWidgets.QApplication.topLevelWidgets():
        try:
            if widget.__class__.__name__ == "MaterialConverter":
                widget.close()
                widget.deleteLater()
        except:
            pass

    try:
        converter_window = MaterialConverter()
        converter_window.show()
        converter_window.raise_()
        converter_window.activateWindow()
        return converter_window
    except Exception as e:
        print(f"创建窗口时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        QMessageBox.critical(None, t("msg.error"), t("qtool.matconv.msg.create_window_failed", e=str(e)))
        return None


def main():
    """QuickTool 入口函数"""
    import sys
    
    if QtWidgets is None:
        print("[MaterialConverter] 无法加载 PySide 模块")
        return

    print("[MaterialConverter] PySide 模块加载成功")

    app = QtWidgets.QApplication.instance()
    if not app:
        print("[MaterialConverter] 创建新的 QApplication")
        app = QtWidgets.QApplication(sys.argv)
        need_exec = True
    else:
        print("[MaterialConverter] 使用现有的 QApplication")
        need_exec = False

    parent_window = get_maya_main_window()
    print(f"[MaterialConverter] 父窗口: {parent_window}")

    print("[MaterialConverter] 创建对话框...")
    dialog = MaterialConverter(parent=parent_window)

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

    print("[MaterialConverter] 显示对话框...")
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    print("[MaterialConverter] 对话框已显示")

    if need_exec:
        print("[MaterialConverter] 进入事件循环...")
        app.exec()


# 鐩存帴杩愯鑴氭湰鏃舵樉绀虹獥鍙?
if __name__ == "__main__":
    try:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.__class__.__name__ == "MaterialConverter":
                widget.close()
                widget.deleteLater()

        main()
    except Exception as e:
        print(f"杩愯鑴氭湰鏃跺嚭閿? {e}")
        try:
            error_msg = t("qtool.matconv.msg.script_run_failed", e=str(e))
            QtWidgets.QMessageBox.critical(None, t("msg.error"), error_msg)
        except Exception:
            print(error_msg)
