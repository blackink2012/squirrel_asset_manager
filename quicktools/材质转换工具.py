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


def color_mul_scalar(color, scalar):
    """颜色乘以标量

    Args:
        color: RGB颜色列表 [r, g, b]
        scalar: 标量值

    Returns:
        list: 变换后的颜色 [r, g, b]
    """
    if not color:
        return [0.0, 0.0, 0.0]
    scalar = float(scalar) if scalar is not None else 1.0
    return [
        max(0.0, min(1.0, color[0] * scalar)),
        max(0.0, min(1.0, color[1] * scalar)),
        max(0.0, min(1.0, color[2] * scalar))
    ]


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
        f0: F0基础反射率 (0-1)
        diffuse_color: 漫反射颜色，用于金属度估算

    Returns:
        list: 镜面反射颜色 [r, g, b]
    """
    if f0 is None:
        f0 = 0.04
    f0 = max(0.0, min(1.0, float(f0)))
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
    "颜色插值": color_lerp
}


def apply_conversion(value, transform_name, source_attrs=None):
    """应用转换函数

    Args:
        value: 要转换的值
        transform_name: 转换函数名称
        source_attrs: 可选的源属性字典，用于需要多个参数的转换

    Returns:
        转换后的值
    """
    if not transform_name or transform_name not in MATERIAL_CONVERSION_FUNCTIONS:
        return value
    func = MATERIAL_CONVERSION_FUNCTIONS[transform_name]
    try:
        if source_attrs:
            return func(value, **source_attrs)
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
}


def precompute_remap_samples(transform_name, num_samples=32):
    """为remapValue节点预计算采样点

    对转换函数在典型输入范围内均匀采样，生成(input, output)对，
    用于配置Maya remapValue节点的插值表。

    Args:
        transform_name: 转换函数名称
        num_samples: 采样点数量

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
        t = i / (num_samples - 1) if num_samples > 1 else 0.0
        input_val = input_min + t * (input_max - input_min)
        try:
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


class MaterialConverter(QtWidgets.QDialog):
    """材质转换工具主窗口"""

    def __init__(self, parent=None):
        # 尝试获取Maya主窗口作为父窗口
        maya_window = get_maya_main_window()
        if maya_window is not None:
            parent = maya_window

        super(MaterialConverter, self).__init__(parent)

        self.setWindowTitle("材质转换工具 - Maya 2025")
        self.setMinimumSize(900, 600)
        self.resize(900, 650)

        # 设置窗口标志，启用最小化和最大化按钮
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowMinimizeButtonHint | QtCore.Qt.WindowMaximizeButtonHint)

        # 样式设置
        self.setStyleSheet("""
            QWidget {
                font-size: 18px;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 5px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QComboBox {
                min-height: 30px;
                padding: 5px 30px 6px 10px;
                font-size: 18px;
            }
            QPushButton {
                min-height: 30px;
                padding: 6px 15px;
                font-size: 18px;
            }
            QLineEdit {
                min-height: 30px;
                padding: 5px 10px;
                font-size: 18px;
            }
            QTableWidget {
                gridline-color: #ddd;
                background-color: white;
                font-size: 18px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 5px;
                font-size: 18px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """)

        self.preset_dir = PRESET_DIR
        if not os.path.exists(self.preset_dir):
            os.makedirs(self.preset_dir)

        # 初始化变量
        self.mapping_data = None
        self.source_material = None
        self.target_material_type = ""
        self.loaded_mappings = {}  # 存储加载的多个映射数据

        # 映射文件夹路径
        self.mapping_base_dir = os.path.join(cmds.internalVar(userPrefDir=True), "material_mapper_presets")
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
        help_btn = QtWidgets.QPushButton("❓ 使用帮助")
        help_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 15px;")
        help_btn.setStatusTip("打开完整的使用帮助文档")
        help_btn.clicked.connect(self.show_help)
        toolbar_layout.addWidget(help_btn)
        
        toolbar_layout.addStretch()
        
        main_layout.addLayout(toolbar_layout)

        # 材质类型映射列表区域
        mapping_type_group = QtWidgets.QGroupBox("材质类型映射配置")
        mapping_type_group.setStatusTip("配置源材质类型到目标材质类型的映射关系，支持多行批量转换")
        mapping_type_layout = QtWidgets.QVBoxLayout()
        mapping_type_group.setLayout(mapping_type_layout)

        # 说明标签
        info_label = QtWidgets.QLabel("提示: 点击'加载源'从选择加载材质类型，点击'加载目标'设置目标类型，重复添加多行可实现批量转换")
        info_label.setStyleSheet("color: #666; font-size: 14px; padding: 5px 0;")
        mapping_type_layout.addWidget(info_label)

        # 映射类型表格
        self.mapping_type_table = QtWidgets.QTableWidget()
        self.mapping_type_table.setStatusTip("双击单元格编辑材质类型\n源为空时表示将所有材质转换为目标类型")
        self.mapping_type_table.setColumnCount(4)
        self.mapping_type_table.setHorizontalHeaderLabels(["", "源材质类型", "目标材质类型", ""])
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
        self.mapping_type_table.setColumnWidth(3, 30)
        self.mapping_type_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.mapping_type_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.mapping_type_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        self.mapping_type_table.verticalHeader().setVisible(False)

        # 表格样式 - 暗色主题与预览表格一致
        self.mapping_type_table.setStyleSheet("""
            QTableWidget {
                background-color: #404040;
                alternate-background-color: #505050;
                color: #e0e0e0;
                gridline-color: #505050;
            }
            QTableWidget::item {
                padding: 5px;
                font-size: 14px;
            }
            QTableWidget::item:selected {
                background-color: #2080d0;
                color: white;
            }
            QHeaderView::section {
                background-color: #353535;
                color: #d0d0d0;
                border: 1px solid #303030;
                padding: 5px;
                font-weight: bold;
                font-size: 14px;
            }
        """)

        # 默认添加一行源为空目标为openPBRSurface的映射
        row = self.mapping_type_table.rowCount()
        self.mapping_type_table.insertRow(row)
        # 设置目标材质类型为openPBRSurface
        dst_item = QtWidgets.QTableWidgetItem("openPBRSurface")
        self.mapping_type_table.setItem(row, 2, dst_item)

        mapping_type_layout.addWidget(self.mapping_type_table)

        # 按钮行
        btn_layout = QtWidgets.QHBoxLayout()

        load_source_btn = QtWidgets.QPushButton("▶ 加载源")
        load_source_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 15px;")
        load_source_btn.setStatusTip("从Maya中选择的材质节点加载源材质类型到表格")
        load_source_btn.clicked.connect(self.load_source_type_from_selection)
        btn_layout.addWidget(load_source_btn)

        load_target_btn = QtWidgets.QPushButton("◀ 加载目标")
        load_target_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px 15px;")
        load_target_btn.setStatusTip("从Maya中选择的材质节点加载目标材质类型到表格")
        load_target_btn.clicked.connect(self.load_target_type_from_selection)
        btn_layout.addWidget(load_target_btn)

        btn_layout.addStretch()

        add_row_btn = QtWidgets.QPushButton("+ 添加行")
        add_row_btn.setStatusTip("添加一行空的材质类型映射")
        add_row_btn.clicked.connect(self.add_mapping_type_row)
        btn_layout.addWidget(add_row_btn)

        delete_row_btn = QtWidgets.QPushButton("删除选中")
        delete_row_btn.setStatusTip("删除表格中选中的行")
        delete_row_btn.clicked.connect(self.delete_mapping_type_row)
        btn_layout.addWidget(delete_row_btn)

        clear_all_btn = QtWidgets.QPushButton("清空全部")
        clear_all_btn.setStatusTip("清空表格中所有映射行")
        clear_all_btn.clicked.connect(self.clear_mapping_type_list)
        btn_layout.addWidget(clear_all_btn)

        btn_layout.addStretch()

        save_config_btn = QtWidgets.QPushButton("💾 保存预设")
        save_config_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 8px 15px;")
        save_config_btn.setStatusTip("将当前材质类型映射配置保存为.mlist预设文件")
        save_config_btn.clicked.connect(self.save_mapping_type_preset)
        btn_layout.addWidget(save_config_btn)

        load_config_btn = QtWidgets.QPushButton("📂 加载预设")
        load_config_btn.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; padding: 8px 15px;")
        load_config_btn.setStatusTip("从.mlist预设文件加载材质类型映射配置")
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
        material_info_group = QtWidgets.QGroupBox("材质信息")
        material_info_layout = QtWidgets.QGridLayout()
        material_info_group.setLayout(material_info_layout)

        material_info_layout.addWidget(QtWidgets.QLabel("源材质:"), 0, 0)
        self.source_name_label = QtWidgets.QLabel("(未选择)")
        self.source_name_label.setStyleSheet("color: #666; font-style: italic;")
        material_info_layout.addWidget(self.source_name_label, 0, 1)
        self.source_type_label = QtWidgets.QLabel("")
        material_info_layout.addWidget(self.source_type_label, 0, 2)

        material_info_layout.addWidget(QtWidgets.QLabel("目标材质:"), 1, 0)
        self.target_name_label = QtWidgets.QLabel("(未选择)")
        self.target_name_label.setStyleSheet("color: #666; font-style: italic;")
        material_info_layout.addWidget(self.target_name_label, 1, 1)
        self.target_type_label = QtWidgets.QLabel("")
        material_info_layout.addWidget(self.target_type_label, 1, 2)

        top_layout.addWidget(material_info_group)
        
        top_layout.addWidget(mapping_type_group)
        top_layout.setStretch(1, 1)  # 让材质类型映射配置优先缩放

        # 下半部分：映射文件、映射预览、选项和按钮
        bottom_widget = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout()
        bottom_widget.setLayout(bottom_layout)

        # 映射文件区域
        mapping_group = QtWidgets.QGroupBox("映射文件")
        mapping_group.setStatusTip("选择包含属性映射.mmap文件的文件夹，工具会自动加载其中所有映射文件")
        mapping_layout = QtWidgets.QHBoxLayout()
        mapping_group.setLayout(mapping_layout)

        self.mapping_file_edit = QtWidgets.QLineEdit()
        self.mapping_file_edit.setReadOnly(True)
        self.mapping_file_edit.setPlaceholderText("选择映射关系文件...")
        self.mapping_file_edit.setStatusTip("当前加载的映射文件所在文件夹路径")
        browse_btn = QtWidgets.QPushButton("浏览...")
        browse_btn.setStatusTip("浏览并选择包含映射.mmap文件的文件夹")
        browse_btn.clicked.connect(self.browse_mapping_file)
        self.mapping_status_label = QtWidgets.QLabel("")
        self.mapping_status_label.setStyleSheet("color: #888;")

        mapping_layout.addWidget(self.mapping_file_edit, 1)
        mapping_layout.addWidget(browse_btn)
        mapping_layout.addWidget(self.mapping_status_label)

        # 映射文件夹说明
        mapping_info_label = QtWidgets.QLabel(f"映射文件目录: {self.mapping_base_dir}")
        mapping_info_label.setStyleSheet("color: #888; font-size: 11px;")
        mapping_layout.addWidget(mapping_info_label)

        open_folder_btn = QtWidgets.QPushButton("打开文件夹")
        open_folder_btn.setStyleSheet("font-size: 12px; padding: 3px 8px;")
        open_folder_btn.setStatusTip("在资源管理器中打开映射文件所在文件夹")
        open_folder_btn.clicked.connect(self.open_mapping_folder)
        mapping_layout.addWidget(open_folder_btn)

        bottom_layout.addWidget(mapping_group)

        # 映射预览区域 - 使用选项卡显示多个映射文件（不优先缩放）
        preview_group = QtWidgets.QGroupBox("属性映射预览")
        preview_group.setStatusTip("预览已加载的属性映射关系，切换选项卡查看不同映射文件")
        preview_layout = QtWidgets.QVBoxLayout()
        preview_group.setLayout(preview_layout)

        self.preview_tabs = QtWidgets.QTabWidget()
        self.preview_tabs.setStatusTip("切换选项卡查看不同映射文件的属性映射详情")
        self.preview_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #ccc;
                background-color: #404040;
            }
            QTabBar::tab {
                background-color: #353535;
                color: #d0d0d0;
                font-size: 14px;
                padding: 6px 12px;
                border: 1px solid #303030;
            }
            QTabBar::tab:selected {
                background-color: #505050;
                color: #ffffff;
            }
            QTabBar::tab:hover {
                background-color: #454545;
            }
        """)

        preview_layout.addWidget(self.preview_tabs)

        bottom_layout.addWidget(preview_group)

        # 选项区域
        options_group = QtWidgets.QGroupBox("转换选项")
        options_group.setStatusTip("设置材质转换时的附加选项")
        options_layout = QtWidgets.QHBoxLayout()
        options_group.setLayout(options_layout)

        self.copy_textures_check = QtWidgets.QCheckBox("复制纹理连接")
        self.copy_textures_check.setStatusTip("转换时保留源材质的纹理贴图连接")
        self.copy_textures_check.setChecked(True)
        options_layout.addWidget(self.copy_textures_check)

        self.keep_original_check = QtWidgets.QCheckBox("保留原始材质")
        self.keep_original_check.setStatusTip("转换后保留原始材质节点，不自动删除")
        self.keep_original_check.setChecked(True)
        options_layout.addWidget(self.keep_original_check)

        self.auto_assign_check = QtWidgets.QCheckBox("自动应用到选择对象")
        self.auto_assign_check.setStatusTip("转换后自动将新材质应用到使用原材质的对象上")
        self.auto_assign_check.setChecked(True)
        options_layout.addWidget(self.auto_assign_check)

        options_layout.addStretch()

        # 按钮区域
        button_layout = QtWidgets.QHBoxLayout()

        self.convert_selection_btn = QtWidgets.QPushButton("▶ 转换选择")
        self.convert_selection_btn.setStatusTip("仅转换Maya中当前选中的物体/材质")
        self.convert_selection_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-weight: bold;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
        """)
        self.convert_selection_btn.clicked.connect(self.convert_selection)
        button_layout.addWidget(self.convert_selection_btn)

        self.convert_all_btn = QtWidgets.QPushButton("⚡ 转换全部")
        self.convert_all_btn.setStatusTip("转换场景中所有符合映射配置的材质")
        self.convert_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                font-weight: bold;
                min-width: 120px;
            }
            QPushButton:hover {
                background-color: #e68a00;
            }
        """)
        self.convert_all_btn.clicked.connect(self.execute_conversion)
        button_layout.addWidget(self.convert_all_btn)

        self.convert_btn = self.convert_all_btn  # 合并为一个按钮

        button_layout.addStretch()

        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.setStatusTip("关闭材质转换工具窗口")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        bottom_layout.addLayout(button_layout)

        # 状态栏
        status_layout = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("就绪 - 请选择源材质和目标材质，然后加载映射文件")
        self.status_label.setStyleSheet("color: #666; padding: 5px;")
        status_layout.addWidget(self.status_label)
        bottom_layout.addLayout(status_layout)

        # 将上下部分添加到分割器
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        
        # 设置分割器的初始大小比例（材质类型映射配置占较大空间）
        splitter.setSizes([400, 300])
        splitter.setStretchFactor(0, 1)  # top_widget优先占用空间
        splitter.setStretchFactor(1, 0)  # bottom_widget不优先占用空间
        
        # 将分割器添加到主布局
        main_layout.addWidget(splitter)

    def show_help(self):
        """显示使用帮助窗口"""
        import os
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = os.path.join(plugin_root, "Assets", "help", "材质转换工具", "help.html")
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            QtWidgets.QMessageBox.information(self, "使用帮助",
                "帮助文件未找到: " + help_path)

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
            QMessageBox.information(self, "提示", "请先在Maya中选择一个对象或材质节点")
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

        QMessageBox.information(self, "提示", "所选对象没有关联的材质，请选择其他对象")

    def load_target_from_selection(self):
        """从选择加载目标材质"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.information(self, "提示", "请先在Maya中选择一个对象或材质节点")
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

        QMessageBox.information(self, "提示", "所选对象没有关联的材质，请选择其他对象")

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
            parts.append(f"源材质: {self.source_material}")
        else:
            parts.append("源材质: 未选择")

        if self.target_material_type:
            parts.append(f"目标类型: {self.target_material_type}")
        elif self.target_name_label.text() != "(未选择)":
            parts.append(f"目标材质: {self.target_name_label.text()}")
        else:
            parts.append("目标材质: 未选择")

        if self.mapping_data:
            mapping_count = len(self.mapping_data.get('mappings', []))
            parts.append(f"映射关系: {mapping_count} 条")
        else:
            parts.append("映射关系: 未加载")

        self.status_label.setText(" | ".join(parts))

        # 更新按钮状态 - 转换所有基于配置的映射列表
        mappings = self.get_configured_mappings()
        self.convert_btn.setEnabled(bool(mappings))

    def browse_mapping_file(self):
        """浏览并选择映射文件夹，加载该文件夹下所有映射文件"""
        folder = QFileDialog.getExistingDirectory(
            self, "选择映射文件夹", self.mapping_base_dir,
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
            QMessageBox.information(self, "提示", f"选择的文件夹中没有找到映射文件(.mmap)")
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
                self.target_name_label.setText(f"(将转换为 {target_type})")

            self.mapping_file_edit.setText(folder)
            self.mapping_status_label.setText(f"已加载 {len(self.loaded_mappings)} 个映射文件")
            self.update_status()

            QMessageBox.information(self, "成功",
                                  f"成功加载 {len(self.loaded_mappings)} 个映射文件！\n\n"
                                  f"切换选项卡可查看不同映射预览。")

        if failed_files:
            detail = "\n".join(f"  • {name}: {err}" for name, err in failed_files)
            QMessageBox.warning(self, "部分加载失败",
                               f"{len(failed_files)} 个文件加载失败:\n\n{detail}")

    def open_mapping_folder(self):
        """打开映射文件夹"""
        import subprocess
        try:
            folder_path = os.path.abspath(self.mapping_base_dir)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
            subprocess.Popen(['explorer', folder_path])
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开文件夹:\n{str(e)}")

    def _add_mapping_tab(self, name, data):
        """为映射数据添加一个选项卡"""
        table = QtWidgets.QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["源属性", "目标属性", "转换函数", "默认值"])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        # 表格样式
        table.setStyleSheet("""
            QTableView {
                background-color: #404040;
                alternate-background-color: #505050;
                color: #e0e0e0;
                font-size: 16px;
            }
            QTableView::item {
                border: 1px solid #303030;
                padding: 2px;
                font-size: 16px;
            }
            QTableView::item:selected {
                background-color: #2080d0;
                color: white;
            }
            QHeaderView::section {
                background-color: #353535;
                color: #d0d0d0;
                border: 1px solid #303030;
                padding: 4px;
                font-size: 16px;
            }
        """)

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
                note = "新增属性"
            elif src_attr and not dst_attr:
                note = "将被忽略"
            elif default_value:
                note = "使用默认值"

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
                self.target_name_label.setText(f"(将转换为 {target_type})")
                self.target_name_label.setStyleSheet("color: #666; font-style: italic;")

            # 更新映射预览
            self.update_mapping_preview()

            # 更新状态
            self.mapping_status_label.setText(f"已加载 {len(data.get('mappings', []))} 条映射")
            self.update_status()

            QMessageBox.information(self, "成功",
                                  f"映射文件加载成功！\n\n"
                                  f"源类型: {data.get('source_type', '未知')}\n"
                                  f"目标类型: {target_type}\n"
                                  f"映射数量: {len(data.get('mappings', []))}")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载映射文件失败:\n{str(e)}")
            self.mapping_status_label.setText("加载失败")

    def update_mapping_preview(self):
        """更新映射预览表格"""
        # 清空现有选项卡
        self.preview_tabs.clear()

        if not self.mapping_data:
            return

        # 添加当前映射数据到选项卡
        name = self.mapping_data.get('name', '当前映射')
        self._add_mapping_tab(name, self.mapping_data)

    def load_source_type_from_selection(self):
        """从选择加载源材质类型到列表"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.warning(self, "警告", "请先在Maya中选择材质节点")
            return

        material = None
        for item in selected:
            node_type = cmds.nodeType(item)
            if node_type in ['lambert', 'phong', 'blinn', 'standardSurface', 'openPBRSurface',
                           'aiStandardSurface', 'aiStandard', 'VRayMtl', 'RedshiftMaterial',
                           'aiStandardHair', 'aiVolume']:
                material = item
                break

        if not material:
            QMessageBox.warning(self, "警告", "选择中未找到有效的材质节点")
            return

        material_type = cmds.nodeType(material)

        # 检查是否有选中的行
        selected_rows = set()
        for item in self.mapping_type_table.selectedItems():
            selected_rows.add(item.row())
        
        # 如果有选中的行，使用第一行
        if selected_rows:
            row_found = sorted(selected_rows)[0]
        else:
            # 查找空行（没有源类型的行）
            row_found = -1
            for row in range(self.mapping_type_table.rowCount()):
                src_item = self.mapping_type_table.item(row, 1)
                if not src_item or not src_item.text():
                    row_found = row
                    break
            
            # 如果没有空行，查找有源类型但没有目标类型的行
            if row_found == -1:
                for row in range(self.mapping_type_table.rowCount()):
                    src_item = self.mapping_type_table.item(row, 1)
                    dst_item = self.mapping_type_table.item(row, 2)
                    if src_item and src_item.text() == material_type and (not dst_item or not dst_item.text()):
                        row_found = row
                        break

            if row_found == -1:
                # 添加新行
                row_found = self.mapping_type_table.rowCount()
                self.mapping_type_table.insertRow(row_found)

        # 设置源材质类型
        src_item = self.mapping_type_table.item(row_found, 1)
        if not src_item:
            src_item = QtWidgets.QTableWidgetItem(material_type)
            self.mapping_type_table.setItem(row_found, 1, src_item)
        else:
            src_item.setText(material_type)

        # 高亮该行
        self.mapping_type_table.selectRow(row_found)

    def load_target_type_from_selection(self):
        """从选择加载目标材质类型到列表"""
        selected = cmds.ls(selection=True)
        if not selected:
            QMessageBox.warning(self, "警告", "请先在Maya中选择材质节点")
            return

        material = None
        for item in selected:
            node_type = cmds.nodeType(item)
            if node_type in ['lambert', 'phong', 'blinn', 'standardSurface', 'openPBRSurface',
                           'aiStandardSurface', 'aiStandard', 'VRayMtl', 'RedshiftMaterial',
                           'aiStandardHair', 'aiVolume']:
                material = item
                break

        if not material:
            QMessageBox.warning(self, "警告", "选择中未找到有效的材质节点")
            return

        material_type = cmds.nodeType(material)

        # 检查是否有选中的行
        selected_rows = set()
        for item in self.mapping_type_table.selectedItems():
            selected_rows.add(item.row())
        
        # 如果有选中的行，使用第一行
        if selected_rows:
            row_found = sorted(selected_rows)[0]
        else:
            # 查找有源但没有目标的行
            row_found = -1
            for row in range(self.mapping_type_table.rowCount()):
                src_item = self.mapping_type_table.item(row, 1)
                dst_item = self.mapping_type_table.item(row, 2)
                if src_item and src_item.text() and (not dst_item or not dst_item.text()):
                    row_found = row
                    break

            if row_found == -1:
                # 如果没有找到符合条件的行，添加到新行
                row_found = self.mapping_type_table.rowCount()
                self.mapping_type_table.insertRow(row_found)

        # 设置目标材质类型
        dst_item = self.mapping_type_table.item(row_found, 2)
        if not dst_item:
            dst_item = QtWidgets.QTableWidgetItem(material_type)
            self.mapping_type_table.setItem(row_found, 2, dst_item)
        else:
            dst_item.setText(material_type)

        # 高亮该行
        self.mapping_type_table.selectRow(row_found)

    def add_mapping_type_row(self):
        """添加一行空映射"""
        row = self.mapping_type_table.rowCount()
        self.mapping_type_table.insertRow(row)

    def delete_mapping_type_row(self):
        """删除选中的行"""
        selected_rows = set()
        # 获取所有选中的行
        for item in self.mapping_type_table.selectedItems():
            selected_rows.add(item.row())
        
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
            src_item = self.mapping_type_table.item(row, 1)
            dst_item = self.mapping_type_table.item(row, 2)
            source_type = src_item.text() if src_item else ""
            target_type = dst_item.text() if dst_item else ""
            if source_type or target_type:
                mappings.append({
                    "source_type": source_type,
                    "target_type": target_type
                })

        if not mappings:
            QMessageBox.warning(self, "警告", "没有可保存的映射配置")
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
            self, "保存映射配置预设", preset_dir, "Mapping List Files (*.mlist)"
        )

        if filepath:
            if not filepath.endswith('.mlist'):
                filepath += '.mlist'
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(preset_data, f, indent=4, ensure_ascii=False)
                QMessageBox.information(self, "成功", f"映射配置已保存到:\n{filepath}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存失败:\n{str(e)}")

    def load_mapping_type_preset(self):
        """从.mlist预设加载材质类型映射配置"""
        preset_dir = os.path.join(self.preset_dir, "mapping_type_presets")
        if not os.path.exists(preset_dir):
            os.makedirs(preset_dir)

        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载映射配置预设", preset_dir, "Mapping List Files (*.mlist)"
        )

        if not filepath:
            return

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)

            mappings = preset_data.get("mappings", [])
            if not mappings:
                QMessageBox.warning(self, "警告", "预设文件中没有映射数据")
                return

            self.mapping_type_table.setRowCount(0)
            for mapping in mappings:
                row = self.mapping_type_table.rowCount()
                self.mapping_type_table.insertRow(row)
                src_item = QtWidgets.QTableWidgetItem(mapping.get("source_type", ""))
                dst_item = QtWidgets.QTableWidgetItem(mapping.get("target_type", ""))
                self.mapping_type_table.setItem(row, 1, src_item)
                self.mapping_type_table.setItem(row, 2, dst_item)

            QMessageBox.information(self, "成功", f"已加载 {len(mappings)} 条映射配置")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载失败:\n{str(e)}")

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

            elif attr_type in ['double3', 'float3', 'double2', 'float2', 'vector']:
                value = cmds.getAttr(full_attr)
                if value is None:
                    return None
                # 处理嵌套列表，如 [(1.0, 0.5, 0.25)]
                if isinstance(value, (list, tuple)) and len(value) == 1:
                    if isinstance(value[0], (list, tuple)):
                        value = value[0]
                # 确保返回的是普通列表或元组，而不是嵌套结构
                if isinstance(value, (list, tuple)):
                    return value
                return value

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

            else:
                try:
                    return cmds.getAttr(full_attr)
                except:
                    return None

        except Exception as e:
            print(f"获取属性值失败 {node}.{attribute}: {e}")
            return None

    def _set_attribute_value(self, node, attribute, value):
        """设置节点属性的值"""
        try:
            full_attr = f"{node}.{attribute}"
            if not cmds.objExists(full_attr):
                print(f"属性不存在: {full_attr}")
                return False

            attr_type = cmds.getAttr(full_attr, type=True)

            if value is None or value == "":
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

            elif attr_type in ['double3', 'float3', 'double2', 'float2', 'vector']:
                if isinstance(value, (list, tuple)):
                    # 处理嵌套列表或元组，如 [(1.0, 0.5, 0.25)]
                    if len(value) == 1 and isinstance(value[0], (list, tuple)):
                        value = value[0]
                    
                    # 处理空列表
                    if not value:
                        return True
                    
                    # 确保有足够的元素，不足的用0填充
                    while len(value) < 3:
                        value = list(value) + [0.0]
                    
                    try:
                        cmds.setAttr(full_attr, float(value[0]), float(value[1]), float(value[2]), type='double3')
                    except Exception as e:
                        print(f"设置向量属性失败: {e}")
                        return True
                elif isinstance(value, str):
                    try:
                        cleaned = value.strip().strip('[]()')
                        parts = [float(x.strip()) for x in cleaned.replace(',', ' ').split() if x.strip()]
                        # 确保有足够的元素，不足的用0填充
                        while len(parts) < 3:
                            parts.append(0.0)
                        cmds.setAttr(full_attr, parts[0], parts[1], parts[2], type='double3')
                    except Exception as e:
                        print(f"无法解析RGB值: {value} - {e}")
                        return True
                else:
                    try:
                        # 对于单个值，设置为RGB相同的值
                        val = float(value)
                        cmds.setAttr(full_attr, val, val, val, type='double3')
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
                    cmds.setAttr(full_attr, value[0], value[1], value[2], type='double3')
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
                try:
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

            # ===== 情况1：RGB贴图 -> 浮点属性（需要桥接转换） =====
            if dst_is_float and src_plug_attr in ['outColor', 'color', 'outValue']:
                # 方案A：如果是file节点，优先连接outAlpha（单通道）
                if cmds.nodeType(src_plug_node) == 'file':
                    try:
                        cmds.connectAttr(f"{src_plug_node}.outAlpha", dst_full, force=True)
                        print(f"[纹理转换] {src_plug_node}.outAlpha -> {dst_full}")
                        return True
                    except:
                        pass

                # 方案B：连接R通道
                try:
                    cmds.connectAttr(f"{src_plug_node}.{src_plug_attr}R", dst_full, force=True)
                    print(f"[纹理转换] {src_plug_node}.{src_plug_attr}R -> {dst_full}")
                    return True
                except:
                    pass

                # 方案C：创建luminance节点做灰度转换
                try:
                    lum_node = cmds.shadingNode('luminance', name=f"{src_plug_node}_lum", asUtility=True)
                    cmds.connectAttr(src_plug, f"{lum_node}.color", force=True)
                    cmds.connectAttr(f"{lum_node}.outValue", dst_full, force=True)
                    print(f"[纹理转换] lum节点 {lum_node}.outValue -> {dst_full}")
                    return True
                except:
                    return False

            # ===== 情况2：直接连接 =====
            else:
                try:
                    cmds.connectAttr(src_plug, dst_full, force=True)
                    return True
                except:
                    return False

        except:
            return False

    def _connect_texture_with_remap(self, source_node, source_attr, target_node, target_attr, transform_name):
        """为贴图连接插入remapValue转换节点

        当源属性有纹理连接且需要数学转换时，在连接中插入remapValue节点。
        将转换函数预计算为采样点表，由Maya在渲染时插值执行。

        Args:
            source_node: 源材质节点
            source_attr: 源属性名
            target_node: 目标材质节点
            target_attr: 目标属性名
            transform_name: 转换函数名称

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

            input_min, input_max, samples = precompute_remap_samples(transform_name)
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

            for i, (in_val, out_val) in enumerate(samples):
                cmds.setAttr(f"{remap_node}.value[{i}].value_Position", in_val)
                cmds.setAttr(f"{remap_node}.value[{i}].value_FloatValue", out_val)

            cmds.setAttr(f"{remap_node}.interpolation", 1)

            cmds.connectAttr(src_plug, f"{remap_node}.inputValue", force=True)
            cmds.connectAttr(f"{remap_node}.outValue", dst_full, force=True)

            print(f"[纹理转换] remapValue {remap_node} ({transform_name}): {src_plug} -> {dst_full}")
            return True

        except Exception as e:
            print(f"remapValue转换失败: {e}")
            return False

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

            target_material_name = f"{source_material}_converted"
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

                if not dst_attr:
                    continue

                if not cmds.objExists(f"{target_material}.{dst_attr}"):
                    print(f"目标属性不存在: {target_material}.{dst_attr}")
                    failed_count += 1
                    continue

                value = None
                if src_attr and cmds.objExists(f"{source_material}.{src_attr}"):
                    if copy_textures:
                        src_full = f"{source_material}.{src_attr}"
                        has_texture = bool(cmds.listConnections(src_full, source=True, destination=False, plugs=True))

                        if has_texture:
                            if transform_name:
                                if self._connect_texture_with_remap(source_material, src_attr,
                                                                     target_material, dst_attr,
                                                                     transform_name):
                                    success_count += 1
                                    texture_count += 1
                                    continue
                            else:
                                if self._connect_texture(source_material, src_attr, target_material, dst_attr):
                                    success_count += 1
                                    texture_count += 1
                                    continue

                    value = self._get_attribute_value(source_material, src_attr)

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
                            print(f"[系统默认值] 使用系统默认值 {value} 应用到 {target_material}.{dst_attr}")
                        except Exception as e:
                            print(f"[系统默认值] 获取系统默认值失败: {e}")
                            failed_count += 1
                            continue

                # 应用转换函数
                if transform_name and value is not None:
                    converted_value = apply_conversion(value, transform_name)
                    if converted_value != value:
                        print(f"[转换] {transform_name}: {value} -> {converted_value}")
                    value = converted_value

                if value is not None:
                    if self._set_attribute_value(target_material, dst_attr, value):
                        if cmds.objExists(f"{source_material}.{src_attr}"):
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
            QMessageBox.warning(self, "警告", "请先配置材质类型映射列表")
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
            QMessageBox.information(self, "提示", "没有找到可转换的材质")
            return

        # 统计信息
        total_converted = 0
        total_objects = 0
        total_failed = 0

        # 预先获取UI设置，避免在循环中访问可能已删除的对象
        try:
            copy_textures = self.copy_textures_check.isChecked()
        except RuntimeError:
            copy_textures = True  # 默认值
        try:
            keep_original = self.keep_original_check.isChecked()
        except RuntimeError:
            keep_original = True  # 默认值

        # 执行转换
        for material, source_type, target_type in materials_to_process:
            # 查找对应的映射文件
            mapping_file = self.find_mapping_file(source_type, target_type)
            if not mapping_file:
                print(f"未找到 {source_type} -> {target_type} 的映射文件")
                continue

            # 加载映射文件
            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    mapping_data = json.load(f)
            except Exception as e:
                print(f"加载映射文件失败 {mapping_file}: {e}")
                continue

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

            # 保存当前映射数据并执行转换
            old_mapping_data = self.mapping_data
            self.mapping_data = mapping_data

            result = self.convert_material(material, target_type, copy_textures)

            if result:
                target_material, success_count, fail_count, default_count, texture_count = result
                if objects_with_material:
                    self.assign_material_to_objects(target_material, objects_with_material)
                    total_objects += len(objects_with_material)
                if not keep_original:
                    try:
                        cmds.delete(material)
                    except:
                        pass
                total_converted += 1
                total_failed += fail_count
            else:
                print(f"材质 {material} 转换失败")

            self.mapping_data = old_mapping_data

        self.status_label.setText("转换完成")
        print(f"\n{'='*60}")
        print(f"批量转换完成！")
        print(f"转换材质数: {total_converted}")
        print(f"应用对象数: {total_objects}")
        print(f"失败属性: {total_failed}")
        print(f"{'='*60}")

    def get_configured_mappings(self):
        """获取配置的材质类型映射列表"""
        mappings = []
        has_empty_source = False
        
        # 收集所有映射
        for row in range(self.mapping_type_table.rowCount()):
            src_item = self.mapping_type_table.item(row, 1)
            dst_item = self.mapping_type_table.item(row, 2)
            if dst_item:
                target_type = dst_item.text()
                if target_type:
                    source_type = "" if not src_item else src_item.text()
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
            QMessageBox.warning(self, "警告", "请先配置材质类型映射列表")
            return

        selection = cmds.ls(selection=True)
        if not selection:
            QMessageBox.warning(self, "警告", "请先在Maya中选择物体或材质")
            return

        # 获取选择中的材质
        materials = self.get_materials_from_selection(selection)
        if not materials:
            QMessageBox.warning(self, "警告", "选择中未找到可转换的材质")
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
                QMessageBox.information(self, "提示", "选择的材质已经是目标类型")
            else:
                QMessageBox.warning(self, "警告", "选择的材质类型与配置的映射不匹配")
            return

        total_converted = 0
        total_objects = 0

        # 预先获取UI设置，避免在循环中访问可能已删除的对象
        try:
            copy_textures = self.copy_textures_check.isChecked()
        except RuntimeError:
            copy_textures = True  # 默认值
        try:
            keep_original = self.keep_original_check.isChecked()
        except RuntimeError:
            keep_original = True  # 默认值

        # 执行转换
        for (source_type, target_type), mat_list in materials_by_config.items():
            # 查找映射文件
            mapping_file = self.find_mapping_file(source_type, target_type)
            if not mapping_file:
                print(f"未找到 {source_type} -> {target_type} 的映射文件")
                continue

            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    mapping_data = json.load(f)
            except Exception as e:
                print(f"加载映射文件失败 {mapping_file}: {e}")
                continue

            for material in mat_list:
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

                old_mapping_data = self.mapping_data
                self.mapping_data = mapping_data
                self.target_material_type = target_type

                result = self.convert_material(material, target_type, copy_textures)

                if result:
                    target_material, success_count, fail_count, default_count, texture_count = result
                    if objects_with_material:
                        self.assign_material_to_objects(target_material, objects_with_material)
                        total_objects += len(objects_with_material)
                    if not keep_original:
                        try:
                            cmds.delete(material)
                        except:
                            pass
                    total_converted += 1

                self.mapping_data = old_mapping_data

        self.status_label.setText("转换完成")
        print(f"\n{'='*60}")
        print(f"选择转换完成！")
        print(f"转换材质数: {total_converted}")
        print(f"应用对象数: {total_objects}")
        print(f"{'='*60}")

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
            node_type = cmds.nodeType(item)
            if node_type in ['lambert', 'phong', 'blinn', 'standardSurface', 'openPBRSurface',
                           'aiStandardSurface', 'aiStandard', 'VRayMtl', 'RedshiftMaterial',
                           'aiStandardHair', 'aiVolume']:
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
                                        conn_type = cmds.nodeType(conn)
                                        if conn_type in ['lambert', 'phong', 'blinn', 'standardSurface', 'openPBRSurface',
                                                       'aiStandardSurface', 'aiStandard', 'VRayMtl', 'RedshiftMaterial',
                                                       'aiStandardHair', 'aiVolume']:
                                            materials.append(conn)
        return list(set(materials))

    def get_objects_from_selection(self, selection):
        """从选择中获取所有物体"""
        objects = []
        for item in selection:
            # 如果是材质，检查它连接到的所有物体
            node_type = cmds.nodeType(item)
            if node_type in ['lambert', 'phong', 'blinn', 'standardSurface', 'openPBRSurface',
                           'aiStandardSurface', 'aiStandard', 'VRayMtl', 'RedshiftMaterial',
                           'aiStandardHair', 'aiVolume']:
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
            QMessageBox.warning(self, "警告", "请先加载映射文件")
            return

        source_type = self.mapping_data.get("source_type", "")

        # 获取所有匹配类型的材质
        materials = cmds.ls(materials=True)
        filtered_materials = [m for m in materials if cmds.nodeType(m) == source_type]

        if not filtered_materials:
            QMessageBox.warning(self, "警告", f"场景中没有 {source_type} 类型的材质")
            return

        # 确认批量转换
        reply = QMessageBox.question(self, "确认批量转换",
                                     f"将对 {len(filtered_materials)} 个 {source_type} 材质进行批量转换\n\n"
                                     f"是否继续？",
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

        self.status_label.setText(f"正在批量转换 {len(filtered_materials)} 个材质...")
        self.repaint()

        for i, material in enumerate(filtered_materials):
            self.status_label.setText(f"正在转换 ({i+1}/{len(filtered_materials)}): {material}...")
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

                # 删除原始材质
                try:
                    cmds.delete(material)
                except:
                    pass

        self.status_label.setText("批量转换完成")

        result_text = f"""批量转换完成！

转换统计:
• 转换材质数: {len(converted_materials)}
• 成功复制: {total_success} 个属性
• 使用默认值: {total_default} 个属性
• 复制纹理: {total_texture} 个连接
• 失败: {total_failed} 个属性"""

        QMessageBox.information(self, "成功", result_text)


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
        QMessageBox.critical(None, "错误", f"创建窗口时出错:\n{str(e)}")
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
            error_msg = f"鑴氭湰杩愯澶辫触:\n{str(e)}"
            QtWidgets.QMessageBox.critical(None, "閿欒", error_msg)
        except Exception:
            print(error_msg)
