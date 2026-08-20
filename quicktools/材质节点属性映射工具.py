"""
Maya材质属性映射工具 - PySide6 版本
支持两列属性映射、.mmap预设保存/加载
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
    from PySide6.QtWidgets import QFileDialog, QMessageBox, QHeaderView
    import shiboken6
    PYSIDE_VERSION = 6
    print("使用 PySide6 版本")
except ImportError as e:
    # 如果 PySide6 导入失败，尝试 PySide2
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
        from PySide2.QtCore import Qt
        from PySide2.QtWidgets import QFileDialog, QMessageBox, QHeaderView
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
    from utils.i18n import t as _T, help_path as _hpath
    _help_path = _hpath
except ImportError:
    try:
        from squirrel_asset_manager.utils.i18n import t as _T, help_path as _hpath
        _help_path = _hpath
    except ImportError:
        _T = None

def t(key, **kwargs):
    return _T(key, **kwargs) if _T is not None else (key.format(**kwargs) if kwargs else key)


# ==================== 转换函数列表 ====================
# 转换函数字典 - 包含中英文名称映射（仅用于UI显示，不包含实际算法）
MATERIAL_CONVERSION_FUNCTIONS = {
    # RGB通道处理
    "RGB转单通道": "rgb_to_channel",
    "rgb_to_channel": "rgb_to_channel",
    "RGB取红": "rgb_to_red",
    "rgb_to_red": "rgb_to_red",
    "RGB取绿": "rgb_to_green",
    "rgb_to_green": "rgb_to_green",
    "RGB取蓝": "rgb_to_blue",
    "rgb_to_blue": "rgb_to_blue",
    "RGB转灰度": "rgb_to_grayscale",
    "rgb_to_grayscale": "rgb_to_grayscale",
    
    # 透明度/透射
    "透明度转透射": "transparency_to_transmission",
    "透明度转透射权重": "transparency_to_transmission",
    "transparency_to_transmission": "transparency_to_transmission",
    
    # 粗糙度转换
    "光泽度转粗糙度": "shininess_to_roughness",
    "shininess_to_roughness": "shininess_to_roughness",
    "Blinn高光锐度转粗糙度": "blinn_cosPower_to_roughness",
    "blinn_cosPower_to_roughness": "blinn_cosPower_to_roughness",
    "Phong光泽度转粗糙度": "phong_shi_to_roughness",
    "phong_shi_to_roughness": "phong_shi_to_roughness",
    "漫反射粗糙度转PBR粗糙度": "diffuse_roughness_to_roughness",
    "diffuse_roughness_to_roughness": "diffuse_roughness_to_roughness",
    
    # PBR参数
    "从镜面反射估算金属度": "metalness_from_specular",
    "metalness_from_specular": "metalness_from_specular",
    "镜面反射强度转权重": "specular_to_specular_weight",
    "specular_to_specular_weight": "specular_to_specular_weight",
    "折射率转F0": "ior_to_f0",
    "ior_to_f0": "ior_to_f0",
    "F0转镜面反射颜色": "f0_to_specular_color",
    "f0_to_specular_color": "f0_to_specular_color",
    
    # 其他材质参数
    "自发光转发光亮度": "emission_to_emission_luminance",
    "emission_to_emission_luminance": "emission_to_emission_luminance",
    "半透明度转次表面散射": "translucence_to_subsurface",
    "translucence_to_subsurface": "translucence_to_subsurface",
    "薄膜厚度转涂层权重": "thin_film_thickness_to_weight",
    "thin_film_thickness_to_weight": "thin_film_thickness_to_weight",
    "反转值": "invert_value",
    "invert_value": "invert_value",
    "限制范围": "clamp",
    "clamp": "clamp",
    
    # 颜色运算
    "颜色乘标量": "color_mul_scalar",
    "color_mul_scalar": "color_mul_scalar",
    "颜色相加": "color_add",
    "color_add": "color_add",
    "颜色插值": "color_lerp",
    "color_lerp": "color_lerp"
}

# 获取唯一的中文名称列表（去重，按类别排序）
def get_conversion_function_options():
    """获取转换函数选项列表（优先显示中文名称）"""
    seen = set()
    options = ["(无)"]
    
    # 按类别添加
    categories = [
        "RGB通道处理", ["RGB转单通道", "RGB取红", "RGB取绿", "RGB取蓝", "RGB转灰度"],
        "透明度/透射", ["透明度转透射", "透明度转透射权重"],
        "粗糙度转换", ["光泽度转粗糙度", "Blinn高光锐度转粗糙度", "Phong光泽度转粗糙度", "漫反射粗糙度转PBR粗糙度"],
        "PBR参数", ["从镜面反射估算金属度", "镜面反射强度转权重", "折射率转F0", "F0转镜面反射颜色"],
        "其他材质参数", ["自发光转发光亮度", "半透明度转次表面散射", "薄膜厚度转涂层权重", "反转值", "限制范围"],
        "颜色运算", ["颜色乘标量", "颜色相加", "颜色插值"]
    ]
    
    for i in range(0, len(categories), 2):
        cat_name = categories[i]
        funcs = categories[i + 1]
        for func_name in funcs:
            if func_name not in seen:
                options.append(func_name)
                seen.add(func_name)
    
    return options


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


class MaterialPropertyMapper(QtWidgets.QDialog):
    """材质属性映射工具主窗口"""

    def __init__(self, parent=None):
        # 尝试获取Maya主窗口作为父窗口
        maya_window = get_maya_main_window()
        if maya_window is not None:
            parent = maya_window

        super(MaterialPropertyMapper, self).__init__(parent)

        self.setWindowTitle(t("qtool.matprop.window_title"))
        self.setMinimumSize(600, 1000)

        # 增大下拉按钮宽度
        self.setStyleSheet("""
            QWidget {
                font-size: 18px;
            }
            QComboBox {
                min-height: 30px;
                padding: 5px 30px 6px 10px;
                font-size: 18px;
            }
            QComboBox::drop-down {
                width: 25px;
            }
            QPushButton {
                font-size: 18px;
            }
            QLineEdit {
                font-size: 18px;
            }
            QTableWidget {
                font-size: 18px;
            }
        """)

        self.preset_dir = PRESET_DIR
        if not os.path.exists(self.preset_dir):
            os.makedirs(self.preset_dir)

        # 初始化节点信息
        self.source_node_name = ""
        self.target_node_name = ""

        self.setup_ui()
        self.load_last_preset()

    def _table_resize_event(self, event):
        """表格resize事件，保持列比例"""
        table = self.table
        if table.columnCount() == 0:
            return

        total_width = table.viewport().width()
        if total_width <= 0:
            return

        # 定义列宽比例：选择50px固定, 材质属性3/6, 目标属性3/6, 转换函数1/6, 默认值1/6
        fixed_width = 50
        stretch_width = total_width - fixed_width
        col3_width = int(stretch_width * (1/6))
        col4_width = int(stretch_width * (1/6))
        remaining = stretch_width - col3_width - col4_width
        col1_width = remaining // 2
        col2_width = remaining - col1_width

        # 保存用户手动调整的宽度比例（如果曾经调整过）
        if not hasattr(self, '_user_adjusted_ratio'):
            self._user_adjusted_ratio = False

        if not self._user_adjusted_ratio:
            table.setColumnWidth(0, fixed_width)
            table.setColumnWidth(1, col1_width)
            table.setColumnWidth(2, col2_width)
            table.setColumnWidth(3, col3_width)
            table.setColumnWidth(4, col4_width)

        # 调用原始resize事件
        if hasattr(table, '_original_resize_event'):
            table._original_resize_event(event)

    def setup_ui(self):
        """创建UI界面"""
        main_layout = QtWidgets.QVBoxLayout(self)

        button_layout = QtWidgets.QHBoxLayout()

        self.add_btn = QtWidgets.QPushButton(t("btn.add_row"))
        self.add_btn.clicked.connect(lambda: self.add_row_with_options())
        button_layout.addWidget(self.add_btn)

        self.remove_btn = QtWidgets.QPushButton(t("btn.remove_selected"))
        self.remove_btn.clicked.connect(self.remove_selected_rows)
        button_layout.addWidget(self.remove_btn)

        self.browser_btn = QtWidgets.QPushButton(t("btn.attribute_browser"))
        self.browser_btn.clicked.connect(self.show_attribute_browser)
        button_layout.addWidget(self.browser_btn)

        button_layout.addStretch()

        self.help_btn = QtWidgets.QPushButton(t("btn.help"))
        self.help_btn.clicked.connect(self.show_help_dialog)
        button_layout.addWidget(self.help_btn)

        self.save_preset_btn = QtWidgets.QPushButton(t("btn.save_preset"))
        self.save_preset_btn.clicked.connect(self.save_preset_dialog)
        button_layout.addWidget(self.save_preset_btn)

        self.load_preset_btn = QtWidgets.QPushButton(t("btn.load_preset"))
        self.load_preset_btn.clicked.connect(self.load_preset_dialog)
        button_layout.addWidget(self.load_preset_btn)

        main_layout.addLayout(button_layout)



        # 节点类型显示区域
        node_type_layout = QtWidgets.QHBoxLayout()
        
        # 源节点选择
        source_layout = QtWidgets.QVBoxLayout()
        source_node_layout = QtWidgets.QHBoxLayout()
        source_node_label = QtWidgets.QLabel(t("qtool.matprop.label.source_node_type"))
        self.source_node_type = QtWidgets.QLineEdit()
        self.source_node_type.setReadOnly(True)
        source_node_browse_btn = QtWidgets.QPushButton(t("btn.browse"))
        source_node_browse_btn.clicked.connect(lambda: self.browse_node(True))
        source_node_layout.addWidget(source_node_label)
        source_node_layout.addWidget(self.source_node_type)
        source_node_layout.addWidget(source_node_browse_btn)
        source_layout.addLayout(source_node_layout)
        
        # 源节点操作按钮
        source_buttons_layout = QtWidgets.QHBoxLayout()
        clear_source_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.clear_source"))
        clear_source_btn.clicked.connect(self.clear_source_attributes)
        source_buttons_layout.addWidget(clear_source_btn)
        source_layout.addLayout(source_buttons_layout)
        
        node_type_layout.addLayout(source_layout)
        
        # 目标节点选择
        target_layout = QtWidgets.QVBoxLayout()
        target_node_layout = QtWidgets.QHBoxLayout()
        target_node_label = QtWidgets.QLabel(t("qtool.matprop.label.target_node_type"))
        self.target_node_type = QtWidgets.QLineEdit()
        self.target_node_type.setReadOnly(True)
        target_node_browse_btn = QtWidgets.QPushButton(t("btn.browse"))
        target_node_browse_btn.clicked.connect(lambda: self.browse_node(False))
        target_node_layout.addWidget(target_node_label)
        target_node_layout.addWidget(self.target_node_type)
        target_node_layout.addWidget(target_node_browse_btn)
        target_layout.addLayout(target_node_layout)
        
        # 目标节点操作按钮
        target_buttons_layout = QtWidgets.QHBoxLayout()
        clear_target_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.clear_target"))
        clear_target_btn.clicked.connect(self.clear_target_attributes)
        target_buttons_layout.addWidget(clear_target_btn)
        target_layout.addLayout(target_buttons_layout)
        
        node_type_layout.addLayout(target_layout)
        
        node_type_layout.addStretch()
        main_layout.addLayout(node_type_layout)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            t("qtool.matprop.header.select"),
            t("qtool.matprop.header.material_attr"),
            t("qtool.matprop.header.target_attr"),
            t("qtool.matprop.header.transform"),
            t("qtool.matprop.header.default")
        ])
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)

        header = self.table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
            header.setSectionResizeMode(3, QHeaderView.Interactive)
            header.setSectionResizeMode(4, QHeaderView.Interactive)
        else:
            header.setResizeMode(0, QHeaderView.Fixed)
            header.setResizeMode(1, QHeaderView.Interactive)
            header.setResizeMode(2, QHeaderView.Interactive)
            header.setResizeMode(3, QHeaderView.Interactive)
            header.setResizeMode(4, QHeaderView.Interactive)

        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 100)

        self.table.horizontalHeader().setStretchLastSection(False)

        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

        main_layout.addWidget(self.table)

        info_layout = QtWidgets.QHBoxLayout()
        info_label = QtWidgets.QLabel(f'{t("qtool.matprop.label.preset_path")}: {self.preset_dir}')
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)

        self.open_folder_btn = QtWidgets.QPushButton(t("common.open_folder"))
        self.open_folder_btn.clicked.connect(self.open_preset_folder)
        info_layout.addWidget(self.open_folder_btn)

        main_layout.addLayout(info_layout)

        # 保存原始resize事件
        self.table._original_resize_event = self.table.resizeEvent
        # 重写resize事件以保持列比例
        self.table.resizeEvent = self._table_resize_event

        button_layout2 = QtWidgets.QHBoxLayout()

        self.reverse_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.reverse_mapping"))
        self.reverse_btn.clicked.connect(self.reverse_mapping)
        button_layout2.addWidget(self.reverse_btn)

        self.clear_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.clear_table"))
        self.clear_btn.clicked.connect(self.clear_table)
        button_layout2.addWidget(self.clear_btn)

        example_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.load_example"))
        example_btn.clicked.connect(self.load_example_data)
        button_layout2.addWidget(example_btn)

        main_layout.addLayout(button_layout2)

        for i in range(3):
            self.add_row()

    def add_row_with_options(self):
        """添加新行，自动获取当前材质类型的属性列表"""
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""

        source_attributes = []
        target_attributes = []

        if source_type:
            try:
                temp_node = cmds.createNode(source_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    source_attributes = self._filter_vector_components(attributes)
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取源节点类型属性失败: {e}")

        if target_type:
            try:
                temp_node = cmds.createNode(target_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    target_attributes = self._filter_vector_components(attributes)
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取目标节点类型属性失败: {e}")

        self.add_row(left_text="", right_text="", left_options=source_attributes, right_options=target_attributes)
    
    def add_row(self, left_text="", right_text="", left_options=None, right_options=None, transform_text="", default_value=""):
        """添加新行

        Args:
            left_text: 左侧当前选中的文本
            right_text: 右侧当前选中的文本
            left_options: 左侧下拉列表的所有选项
            right_options: 右侧下拉列表的所有选项
            transform_text: 转换函数文本
            default_value: 默认值
        """
        row_position = self.table.rowCount()
        self.table.insertRow(row_position)

        # 复选框
        checkbox = QtWidgets.QCheckBox()
        checkbox_widget = QtWidgets.QWidget()
        checkbox_layout = QtWidgets.QHBoxLayout(checkbox_widget)
        checkbox_layout.addWidget(checkbox)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(row_position, 0, checkbox_widget)

        # 左侧材质属性输入 - 可编辑下拉列表
        left_combo = QtWidgets.QComboBox()
        left_combo.setEditable(True)
        left_combo.wheelEvent = lambda e: e.ignore()
        left_combo.installEventFilter(self)
        # 添加none选项作为清空标识
        left_combo.addItem("(无)")
        if left_options:
            left_combo.addItems(left_options)
            if left_text:
                left_combo.setCurrentText(left_text)
            else:
                left_combo.setCurrentText("(无)")
        elif left_text:
            left_combo.addItem(left_text)
            left_combo.setCurrentText(left_text)
        else:
            left_combo.setCurrentText("(无)")
        left_combo.currentTextChanged.connect(lambda text: self._on_left_attribute_changed(row_position, text))
        self.table.setCellWidget(row_position, 1, left_combo)

        # 右侧目标属性输入 - 可编辑下拉列表
        right_combo = QtWidgets.QComboBox()
        right_combo.setEditable(True)
        right_combo.wheelEvent = lambda e: e.ignore()
        right_combo.installEventFilter(self)
        # 添加none选项作为清空标识
        right_combo.addItem("(无)")
        if right_options:
            right_combo.addItems(right_options)
            if right_text:
                right_combo.setCurrentText(right_text)
            else:
                right_combo.setCurrentText("(无)")
        elif right_text:
            right_combo.addItem(right_text)
            right_combo.setCurrentText(right_text)
        else:
            right_combo.setCurrentText("(无)")
        right_combo.currentTextChanged.connect(lambda text: self._on_right_attribute_changed(row_position, text))
        self.table.setCellWidget(row_position, 2, right_combo)

        # 转换函数输入 - 可编辑下拉列表（显示中文名称）
        transform_combo = QtWidgets.QComboBox()
        transform_combo.setEditable(True)
        transform_combo.wheelEvent = lambda e: e.ignore()
        # 添加转换函数选项
        func_options = get_conversion_function_options()
        transform_combo.addItems(func_options)
        # 设置当前值
        if transform_text:
            # 尝试映射为中文显示
            display_text = transform_text
            # 检查是否是英文名称，尝试找到对应的中文
            # 先收集所有中文名称（不包含英文形式的）
            chinese_names = []
            for name in func_options:
                if name != "(无)":
                    chinese_names.append(name)
            # 查找对应的中文
            for chi_name in chinese_names:
                if chi_name in MATERIAL_CONVERSION_FUNCTIONS and MATERIAL_CONVERSION_FUNCTIONS[chi_name] == transform_text:
                    display_text = chi_name
                    break
            transform_combo.setCurrentText(display_text)
        else:
            transform_combo.setCurrentText("(无)")
        self.table.setCellWidget(row_position, 3, transform_combo)

        # 默认值输入 - 文本框
        default_edit = QtWidgets.QLineEdit()
        default_edit.setPlaceholderText(t("qtool.matprop.placeholder.default_value"))
        if default_value:
            default_edit.setText(default_value)
        self.table.setCellWidget(row_position, 4, default_edit)

    def eventFilter(self, obj, event):
        """事件过滤器 - 处理下拉列表的右键点击"""
        if event.type() == QtCore.QEvent.ContextMenu:
            # 判断是左侧还是右侧的下拉列表
            column = -1
            for row in range(self.table.rowCount()):
                if self.table.cellWidget(row, 1) == obj:
                    column = 1
                    break
                elif self.table.cellWidget(row, 2) == obj:
                    column = 2
                    break

            if column in [1, 2]:
                menu = QtWidgets.QMenu()
                if column == 1:
                    clear_action = menu.addAction(t("qtool.matprop.menu.clear_source"))
                else:
                    clear_action = menu.addAction(t("qtool.matprop.menu.clear_target"))

                action = menu.exec_(event.globalPos())
                if action == clear_action:
                    # 找到该下拉列表对应的行并设置为"(无)"
                    for row in range(self.table.rowCount()):
                        if self.table.cellWidget(row, column) == obj:
                            obj.setCurrentText("(无)")
                            break
                return True

        # 对于其他事件，不拦截，让Qt继续处理
        return False

    def _show_table_context_menu(self, pos):
        """显示表格右键菜单

        Args:
            pos: 相对于viewport的鼠标位置 (QPoint)
        """
        # 获取点击位置对应的索引
        index = self.table.indexAt(pos)

        if not index.isValid():
            return

        row = index.row()
        column = index.column()

        # 只对属性列显示菜单（源属性列和目标属性列）
        if column not in [1, 2]:
            return

        # 检查该单元格是否有下拉列表
        combo = self.table.cellWidget(row, column)
        if not combo:
            return

        menu = QtWidgets.QMenu()

        if column == 1:
            clear_action = menu.addAction(t("qtool.matprop.menu.clear_source"))
        else:
            clear_action = menu.addAction(t("qtool.matprop.menu.clear_target"))

        # 显示菜单并获取选中的动作
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))

        if action == clear_action:
            self._clear_single_attribute(row, column)

    def _clear_single_attribute(self, row, column):
        """清空单个单元格的下拉列表值"""
        if row < 0 or row >= self.table.rowCount():
            return

        combo = self.table.cellWidget(row, column)
        if combo:
            combo.setCurrentText("(无)")

    def _on_left_attribute_changed(self, changed_row, new_text):
        """左侧属性变更检测"""
        if not new_text or new_text == "(无)":
            return

        # 检查同一侧是否有重复
        duplicates = []
        for row in range(self.table.rowCount()):
            if row != changed_row:
                combo = self.table.cellWidget(row, 1)
                if combo and combo.currentText() == new_text:
                    duplicates.append(row)

        if duplicates:
            # 清空重复的属性
            for row in duplicates:
                self._clear_single_attribute(row, 1)
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.duplicate_attr", new_text=new_text))

    def _on_right_attribute_changed(self, changed_row, new_text):
        """右侧属性变更检测"""
        if not new_text or new_text == "(无)":
            return

        # 检查同一侧是否有重复
        duplicates = []
        for row in range(self.table.rowCount()):
            if row != changed_row:
                combo = self.table.cellWidget(row, 2)
                if combo and combo.currentText() == new_text:
                    duplicates.append(row)

        if duplicates:
            # 清空重复的属性
            for row in duplicates:
                self._clear_single_attribute(row, 2)
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.duplicate_attr", new_text=new_text))

    def remove_selected_rows(self):
        """移除选中行"""
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        
        # 如果没有选中行，检查哪些行的复选框被选中
        if not selected_rows:
            for row in range(self.table.rowCount()):
                widget = self.table.cellWidget(row, 0)
                if widget:
                    checkbox = widget.findChild(QtWidgets.QCheckBox)
                    if checkbox and checkbox.isChecked():
                        selected_rows.add(row)
        
        # 按降序删除，避免索引变化
        for row in sorted(selected_rows, reverse=True):
            self.table.removeRow(row)
        
        if not selected_rows:
            QMessageBox.information(self, t("common.tip"), t("qtool.matprop.msg.select_row_to_delete"))
    
    def clear_table(self):
        """清空表格"""
        while self.table.rowCount() > 0:
            self.table.removeRow(0)
    
    def get_mapping_data(self):
        """获取所有映射数据"""
        data = []
        for row in range(self.table.rowCount()):
            # 从下拉列表获取属性值
            left_combo = self.table.cellWidget(row, 1)
            right_combo = self.table.cellWidget(row, 2)
            transform_combo = self.table.cellWidget(row, 3)
            default_edit = self.table.cellWidget(row, 4)

            if left_combo and right_combo:
                left_text = left_combo.currentText().strip()
                right_text = right_combo.currentText().strip()
                transform_text = transform_combo.currentText().strip() if transform_combo else ""
                default_value = default_edit.text().strip() if default_edit else ""

                # 忽略"(无)"选项
                if left_text == "(无)":
                    left_text = ""
                if right_text == "(无)":
                    right_text = ""
                if transform_text == "(无)":
                    transform_text = ""

                # 将中文转换为英文函数名（用于保存）
                if transform_text and transform_text in MATERIAL_CONVERSION_FUNCTIONS:
                    transform_text = MATERIAL_CONVERSION_FUNCTIONS[transform_text]

                # 保存所有行，即使源或目标属性为空
                if left_text or right_text:  # 至少有一个非空
                    data.append({
                        "source_attribute": left_text,
                        "target_attribute": right_text,
                        "transform": transform_text,
                        "default_value": default_value
                    })
        return data

    def set_mapping_data(self, data):
        """设置映射数据到表格"""
        # 清空表格
        self.clear_table()

        # 获取源节点和目标节点类型
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""

        # 获取源节点和目标节点的属性列表
        source_attributes = []
        target_attributes = []

        if source_type:
            # 尝试获取源节点类型的属性
            try:
                # 创建一个临时节点来获取属性
                temp_node = cmds.createNode(source_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    source_attributes = self._filter_vector_components(attributes)
                # 删除临时节点
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取源节点类型属性失败: {e}")

        if target_type:
            # 尝试获取目标节点类型的属性
            try:
                # 创建一个临时节点来获取属性
                temp_node = cmds.createNode(target_type, skipSelect=True)
                attributes = cmds.listAttr(temp_node, read=True, write=True, visible=True)
                if attributes:
                    target_attributes = self._filter_vector_components(attributes)
                # 删除临时节点
                cmds.delete(temp_node)
            except Exception as e:
                print(f"获取目标节点类型属性失败: {e}")

        # 添加新数据
        for item in data:
            source_attr = item.get("source_attribute", "")
            target_attr = item.get("target_attribute", "")
            
            # 确保即使源或目标为空也能正确添加行
            self.add_row(
                source_attr,
                target_attr,
                left_options=source_attributes,
                right_options=target_attributes,
                transform_text=item.get("transform", ""),
                default_value=item.get("default_value", "")
            )

    def save_preset(self, filepath):
        """保存预设到文件"""
        mappings = self.get_mapping_data()
        if not mappings:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.no_mapping_data"))
            return False

        from datetime import datetime

        source_node_type = self.source_node_type.text() if self.source_node_type else ""
        target_node_type = self.target_node_type.text() if self.target_node_type else ""

        # 构建新的JSON结构
        preset_data = {
            "version": "3.0",
            "name": f"{source_node_type} → {target_node_type}",
            "software": "maya",
            "source_type": source_node_type,
            "target_type": target_node_type,
            "description": f"{source_node_type} 到 {target_node_type} 的属性映射",
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "mappings": mappings
        }

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(preset_data, f, indent=4, ensure_ascii=False)

            QMessageBox.information(self, t("msg.success"), t("qtool.matprop.msg.preset_saved", filepath=filepath))
            return True
        except Exception as e:
            QMessageBox.critical(self, t("msg.error"), t("qtool.matprop.msg.save_failed", e=str(e)))
            return False

    def load_preset(self, filepath):
        """从文件加载预设"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)

            # 加载源节点和目标节点类型信息
            source_type = preset_data.get("source_type", "")
            target_type = preset_data.get("target_type", "")

            # 设置源节点类型信息
            if source_type:
                self.source_node_name = ""
                if self.source_node_type:
                    self.source_node_type.setText(source_type)

            # 设置目标节点类型信息
            if target_type:
                self.target_node_name = ""
                if self.target_node_type:
                    self.target_node_type.setText(target_type)



            # 加载映射数据
            mappings = preset_data.get("mappings", [])
            self.set_mapping_data(mappings)


            return True
        except Exception as e:
            QMessageBox.critical(self, t("msg.error"), t("qtool.matprop.msg.load_failed", e=str(e)))
            return False
    
    def save_preset_dialog(self):
        """打开保存预设对话框"""
        # 生成默认文件名：源节点类型_目标节点类型
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""
        default_filename = f"{source_type}_{target_type}" if (source_type and target_type) else "material_mapping"
        default_filepath = os.path.join(self.preset_dir, default_filename)
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, t("qtool.matprop.dialog.save_preset"), default_filepath, t("qtool.matprop.dialog.mapping_filter")
        )

        if filepath:
            if not filepath.endswith('.mmap'):
                filepath += '.mmap'
            self.save_preset(filepath)

    def load_preset_dialog(self):
        """打开加载预设对话框"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, t("qtool.matprop.dialog.load_preset"), self.preset_dir, t("qtool.matprop.dialog.mapping_filter")
        )
        
        if filepath:
            self.load_preset(filepath)
    
    def save_last_preset(self):
        """自动保存当前设置为最后使用的预设"""
        last_preset_path = os.path.join(self.preset_dir, "_last_preset.mmap")
        mappings = self.get_mapping_data()

        if mappings:
            from datetime import datetime
            source_node_type = self.source_node_type.text() if self.source_node_type else ""
            target_node_type = self.target_node_type.text() if self.target_node_type else ""

            preset_data = {
                "version": "3.0",
                "name": f"{source_node_type} → {target_node_type}",
                "software": "maya",
                "source_type": source_node_type,
                "target_type": target_node_type,
                "description": f"{source_node_type} 到 {target_node_type} 的属性映射",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "mappings": mappings
            }

            try:
                with open(last_preset_path, 'w', encoding='utf-8') as f:
                    json.dump(preset_data, f, indent=4, ensure_ascii=False)
            except IOError as e:
                print(f"保存最后预设失败: {e}")
    
    def load_last_preset(self):
        """加载最后使用的预设"""
        last_preset_path = os.path.join(self.preset_dir, "_last_preset.mmap")
        if os.path.exists(last_preset_path):
            self.load_preset(last_preset_path)
    
    def open_preset_folder(self):
        """打开预设文件夹"""
        import subprocess
        try:
            folder_path = os.path.abspath(self.preset_dir)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
            subprocess.Popen(['explorer', folder_path])
        except Exception as e:
            QMessageBox.warning(self, t("msg.error"), t("qtool.matprop.msg.open_folder_failed", e=str(e)))

    def load_example_data(self):
        """加载示例数据"""
        example_data = [
            {"source_attribute": "baseColor", "target_attribute": "color"},
            {"source_attribute": "roughness", "target_attribute": "roughness"},
            {"source_attribute": "metalness", "target_attribute": "metallic"},
            {"source_attribute": "normal", "target_attribute": "normalCamera"},
            {"source_attribute": "emissive", "target_attribute": "incandescence"},
            {"source_attribute": "opacity", "target_attribute": "transparency"},
            {"source_attribute": "specular", "target_attribute": "specularColor"},
            {"source_attribute": "ior", "target_attribute": "refractions"},
        ]
        self.set_mapping_data(example_data)





    def reverse_mapping(self):
        """交换源属性和目标属性"""
        # 交换源节点和目标节点的名称和类型
        source_name = self.source_node_name
        target_name = self.target_node_name

        self.source_node_name = target_name
        self.target_node_name = source_name

        # 交换节点类型显示
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""
        self.source_node_type.setText(target_type)
        self.target_node_type.setText(source_type)

        # 交换表格中的属性
        for row in range(self.table.rowCount()):
            left_combo = self.table.cellWidget(row, 1)
            right_combo = self.table.cellWidget(row, 2)

            if left_combo and right_combo:
                # 交换当前文本
                left_text = left_combo.currentText()
                right_text = right_combo.currentText()
                left_combo.setCurrentText(right_text)
                right_combo.setCurrentText(left_text)

                # 交换选项列表
                left_options = [left_combo.itemText(i) for i in range(left_combo.count())]
                right_options = [right_combo.itemText(i) for i in range(right_combo.count())]

                left_combo.blockSignals(True)
                right_combo.blockSignals(True)

                left_combo.clear()
                left_combo.addItems(right_options)
                right_combo.clear()
                right_combo.addItems(left_options)

                # 恢复当前文本（在选项交换后）
                left_combo.setCurrentText(right_text)
                right_combo.setCurrentText(left_text)

                left_combo.blockSignals(False)
                right_combo.blockSignals(False)

    def load_node_attributes(self, node, is_source):
        """加载节点的默认属性到表格"""
        try:
            # 获取节点的可读写属性（保持Maya原生的属性顺序）
            attributes = cmds.listAttr(node, read=True, write=True, visible=True)
            if not attributes:
                return

            # 过滤掉向量属性的分量
            filtered_attributes = self._filter_vector_components(attributes)

            if is_source:
                # 加载源节点属性到左侧
                # 保留右侧的目标属性
                current_targets = []
                for row in range(self.table.rowCount()):
                    right_combo = self.table.cellWidget(row, 2)
                    if right_combo:
                        current_targets.append(right_combo.currentText())

                # 清空表格
                self.clear_table()

                # 添加源节点属性到左侧，保留右侧目标属性
                for i, attr in enumerate(filtered_attributes):
                    target_value = current_targets[i] if i < len(current_targets) else ""
                    self.add_row(left_text=attr, left_options=filtered_attributes,
                                right_text=target_value, right_options=current_targets)
            else:
                # 加载目标节点属性到右侧
                # 保留左侧的源属性
                current_sources = []
                for row in range(self.table.rowCount()):
                    left_combo = self.table.cellWidget(row, 1)
                    if left_combo:
                        current_sources.append(left_combo.currentText())

                # 清空表格
                self.clear_table()

                # 自动匹配属性
                matched_targets = self._auto_match_attributes(current_sources, filtered_attributes.copy())

                # 首先添加有匹配的源属性
                for i, src_attr in enumerate(current_sources):
                    target_value = matched_targets.get(src_attr, "")
                    self.add_row(left_text=src_attr, left_options=current_sources,
                                right_text=target_value, right_options=filtered_attributes)

                # 然后添加剩余的目标属性
                remaining_targets = [attr for attr in filtered_attributes if attr not in matched_targets.values()]
                for target_attr in remaining_targets:
                    self.add_row(left_text="", left_options=current_sources,
                                right_text=target_attr, right_options=filtered_attributes)

        except Exception as e:
            print(f"加载节点属性失败: {e}")

    def _auto_match_attributes(self, source_attrs, target_attrs):
        """自动匹配属性

        Args:
            source_attrs: 源属性列表
            target_attrs: 目标属性列表（会被修改，使用时请传入副本）

        Returns:
            dict: 源属性到目标属性的映射
        """
        matches = {}

        # 首先匹配同名属性
        for src_attr in source_attrs:
            if src_attr in target_attrs:
                matches[src_attr] = src_attr
                target_attrs.remove(src_attr)  # 避免重复匹配

        # 然后匹配最相似的属性
        for src_attr in source_attrs:
            if src_attr not in matches:
                best_match = self._find_best_match(src_attr, target_attrs)
                if best_match:
                    matches[src_attr] = best_match
                    target_attrs.remove(best_match)  # 避免重复匹配

        return matches

    def _find_best_match(self, source_attr, target_attrs):
        """找到最相似的属性

        Args:
            source_attr: 源属性名
            target_attrs: 目标属性列表

        Returns:
            str: 最相似的目标属性名，或None
        """
        if not target_attrs:
            return None

        best_match = None
        highest_score = 0

        for target_attr in target_attrs:
            score = self._score_match(source_attr, target_attr)
            if score > highest_score:
                highest_score = score
                best_match = target_attr

        # 只有相似度足够高才返回
        if highest_score > 0.5:
            return best_match
        return None

    def _score_match(self, source_attr, target_attr):
        """多因素评分系统计算匹配度

        Args:
            source_attr: 源属性名
            target_attr: 目标属性名

        Returns:
            float: 匹配度分数 (0-1)
        """
        # 1. 完全匹配
        if source_attr == target_attr:
            return 1.0

        # 2. 语义映射匹配
        semantic_map = self._get_semantic_mapping()
        source_key = self._to_snake_case(source_attr)
        target_key = self._to_snake_case(target_attr)
        if source_key in semantic_map and semantic_map[source_key] == target_key:
            return 0.95

        # 3. 字符串相似度
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, source_key, target_key).ratio()

        # 4. 关键词重叠
        source_words = set(source_key.split('_'))
        target_words = set(target_key.split('_'))
        if source_words & target_words:
            keyword_score = len(source_words & target_words) / max(len(source_words), len(target_words))
        else:
            keyword_score = 0

        # 5. 长度相似性
        length_ratio = min(len(source_key), len(target_key)) / max(len(source_key), len(target_key))

        # 综合评分
        total_score = (similarity * 0.5) + (keyword_score * 0.3) + (length_ratio * 0.2)
        return min(total_score, 1.0)

    def _to_snake_case(self, text):
        """将驼峰命名转换为蛇形命名

        Args:
            text: 原始字符串

        Returns:
            str: 转换后的蛇形命名字符串
        """
        import re
        # 将驼峰命名转换为下划线分隔
        text = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', text)
        text = re.sub('([a-z0-9])([A-Z])', r'\1_\2', text)
        return text.lower()

    def _get_semantic_mapping(self):
        """获取语义映射字典

        Returns:
            dict: 语义映射字典
        """
        return {
            # 基础属性
            'base_color': 'color',
            'basecolor': 'color',
            'diffuse_color': 'color',
            'diffusecolor': 'color',
            
            # 粗糙度
            'roughness': 'roughness',
            'specular_roughness': 'roughness',
            'refl_roughness': 'roughness',
            
            # 金属度
            'metalness': 'metallic',
            'metallic': 'metallic',
            
            # 法线
            'normal': 'normal_camera',
            'normal_camera': 'normal_camera',
            'normalcamera': 'normal_camera',
            
            # 发光
            'emission': 'incandescence',
            'emission_color': 'incandescence',
            'emissioncolor': 'incandescence',
            'incandescence': 'incandescence',
            
            # 透明度
            'opacity': 'transparency',
            'opacity_color': 'transparency',
            'transparency': 'transparency',
            
            # 高光
            'specular': 'specular_color',
            'specular_color': 'specular_color',
            'specularcolor': 'specular_color',
            
            # 折射率
            'ior': 'refractions',
            'refractive_index': 'refractions',
            'refractions': 'refractions',
            
            # 反射
            'reflectivity': 'reflectance',
            'reflectance': 'reflectance',
            'refl_weight': 'reflectance',
            
            # 凹凸
            'bump': 'bump_map',
            'bump_map': 'bump_map',
            'bumpmap': 'bump_map',
            
            # 位移
            'displacement': 'displace',
            'displace': 'displace'
        }

    def _filter_vector_components(self, attributes):
        """过滤掉向量属性的分量，只保留主属性"""
        # 常见的向量分量后缀
        vector_suffixes = ['R', 'G', 'B', 'X', 'Y', 'Z', 'W', 'U', 'V']
        
        # 存储主属性
        main_attributes = []
        # 存储已处理的主属性名
        processed_main_attrs = set()
        
        for attr in attributes:
            # 检查是否为分量属性
            is_component = False
            for suffix in vector_suffixes:
                if attr.endswith(suffix):
                    # 获取主属性名
                    main_attr = attr[:-1]
                    # 如果主属性存在，且未处理过
                    if main_attr in attributes and main_attr not in processed_main_attrs:
                        main_attributes.append(main_attr)
                        processed_main_attrs.add(main_attr)
                    is_component = True
                    break
            
            # 如果不是分量属性，直接添加
            if not is_component and attr not in processed_main_attrs:
                main_attributes.append(attr)
                processed_main_attrs.add(attr)
        
        return main_attributes

    def clear_source_attributes(self):
        """清空所有源属性"""
        for row in range(self.table.rowCount()):
            left_combo = self.table.cellWidget(row, 1)
            if left_combo:
                left_combo.setCurrentText("(无)")

    def clear_target_attributes(self):
        """清空所有目标属性"""
        for row in range(self.table.rowCount()):
            right_combo = self.table.cellWidget(row, 2)
            if right_combo:
                right_combo.setCurrentText("(无)")

    def browse_node(self, is_source):
        """浏览并选择Maya节点"""
        # 获取当前选择的节点
        selected_nodes = cmds.ls(selection=True)
        if selected_nodes:
            node = selected_nodes[0]
            # 获取节点类型
            try:
                node_type = cmds.nodeType(node)
                if is_source:
                    self.source_node_name = node
                    self.source_node_type.setText(node_type)
                    # 加载源节点属性到表格
                    self.load_node_attributes(node, is_source)
                else:
                    self.target_node_name = node
                    self.target_node_type.setText(node_type)
                    # 加载目标节点属性到表格
                    self.load_node_attributes(node, False)
            except Exception as e:
                print(f"获取节点类型失败: {e}")
                QMessageBox.warning(self, t("msg.error"), t("qtool.matprop.msg.get_node_type_failed", e=e))
        else:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.select_node_in_maya"))

    def show_attribute_browser(self):
        """显示属性浏览器对话框"""
        selected_objects = cmds.ls(selection=True)
        if not selected_objects:
            QMessageBox.warning(self, t("msg.warning"), t("qtool.matprop.msg.select_object_in_maya"))
            return

        dialog = AttributeBrowserDialog(selected_objects[0], self)
        dialog.attribute_selected.connect(self._on_attribute_selected)
        dialog.show()

    def _on_attribute_selected(self, attribute_name, is_source):
        """属性浏览器选中的属性"""
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount()
            self.add_row()

        if is_source:
            combo = self.table.cellWidget(row, 1)
            if combo:
                # 检查属性是否已在下拉列表中
                if attribute_name not in [combo.itemText(i) for i in range(combo.count())]:
                    combo.addItem(attribute_name)
                combo.setCurrentText(attribute_name)
        else:
            combo = self.table.cellWidget(row, 2)
            if combo:
                # 检查属性是否已在下拉列表中
                if attribute_name not in [combo.itemText(i) for i in range(combo.count())]:
                    combo.addItem(attribute_name)
                combo.setCurrentText(attribute_name)


    
    def closeEvent(self, event):
        """窗口关闭时自动保存"""
        self.save_last_preset()
        super(MaterialPropertyMapper, self).closeEvent(event)

    def show_help_dialog(self):
        """显示帮助对话框"""
        import os
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _help_path(os.path.join(plugin_root, "Assets", "help", "材质节点属性映射工具", "help.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            QtWidgets.QMessageBox.information(self, t("btn.help"),
                t("qtool.matprop.msg.help_not_found", path=help_path))


class AttributeBrowserDialog(QtWidgets.QDialog):
    """属性浏览器对话框"""

    attribute_selected = QtCore.Signal(str, bool)

    ATTRIBUTE_TYPE_MAP = {
        'double': 'qtool.matprop.attrtype.numeric',
        'float': 'qtool.matprop.attrtype.numeric',
        'int': 'qtool.matprop.attrtype.integer',
        'long': 'qtool.matprop.attrtype.integer',
        'short': 'qtool.matprop.attrtype.integer',
        'byte': 'qtool.matprop.attrtype.integer',
        'char': 'qtool.matprop.attrtype.integer',
        'bool': 'qtool.matprop.attrtype.boolean',
        'double2': 'qtool.matprop.attrtype.vector2',
        'double3': 'qtool.matprop.attrtype.vector3',
        'float2': 'qtool.matprop.attrtype.vector2',
        'float3': 'qtool.matprop.attrtype.vector3',
        'vector': 'qtool.matprop.attrtype.vector',
        'string': 'qtool.matprop.attrtype.string',
        'message': 'qtool.matprop.attrtype.message',
        'time': 'qtool.matprop.attrtype.time',
        'doubleArray': 'qtool.matprop.attrtype.numeric_array',
        'floatArray': 'qtool.matprop.attrtype.numeric_array',
        'intArray': 'qtool.matprop.attrtype.integer_array',
        'stringArray': 'qtool.matprop.attrtype.string_array',
        'vectorArray': 'qtool.matprop.attrtype.vector_array',
    }

    def __init__(self, node_name, parent=None):
        super(AttributeBrowserDialog, self).__init__(parent)
        self.node_name = node_name
        self.setWindowTitle(t("qtool.matprop.dialog.attribute_browser_title", node_name=node_name))
        self.setMinimumSize(500, 400)
        self.setup_ui()
        self.load_attributes()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        search_layout = QtWidgets.QHBoxLayout()
        search_layout.addWidget(QtWidgets.QLabel(t("qtool.matprop.label.search")))
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.textChanged.connect(self.filter_attributes)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            t("qtool.matprop.header.attr_name"),
            t("qtool.matprop.header.type"),
            t("qtool.matprop.header.current_value"),
            t("qtool.matprop.header.action")
        ])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.itemDoubleClicked.connect(self.on_attribute_double_clicked)

        header = self.table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Fixed)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.Fixed)
        else:
            header.setResizeMode(0, QHeaderView.Stretch)
            header.setResizeMode(1, QHeaderView.Fixed)
            header.setResizeMode(2, QHeaderView.Stretch)
            header.setResizeMode(3, QHeaderView.Fixed)

        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(3, 100)

        layout.addWidget(self.table)

        button_layout = QtWidgets.QHBoxLayout()

        self.use_as_source_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.use_as_source"))
        self.use_as_source_btn.clicked.connect(lambda: self.use_attribute(True))
        button_layout.addWidget(self.use_as_source_btn)

        self.use_as_target_btn = QtWidgets.QPushButton(t("qtool.matprop.btn.use_as_target"))
        self.use_as_target_btn.clicked.connect(lambda: self.use_attribute(False))
        button_layout.addWidget(self.use_as_target_btn)

        button_layout.addStretch()

        close_btn = QtWidgets.QPushButton(t("common.close"))
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def load_attributes(self):
        """加载节点的所有属性"""
        self.table.setRowCount(0)

        if not cmds.objExists(self.node_name):
            return

        attributes = cmds.listAttr(self.node_name, read=True, write=True)
        if not attributes:
            return

        for attr in sorted(attributes):
            try:
                attr_full = f"{self.node_name}.{attr}"
                attr_type = cmds.getAttr(attr_full, type=True)
                try:
                    value = cmds.getAttr(attr_full)
                    if isinstance(value, (list, tuple)):
                        value_str = str(value)
                    elif value is None:
                        value_str = "None"
                    else:
                        value_str = str(value)
                except RuntimeError:
                    value_str = "N/A"

                type_text = t(self.ATTRIBUTE_TYPE_MAP.get(attr_type, attr_type))

                row = self.table.rowCount()
                self.table.insertRow(row)

                self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(attr))
                self.table.item(row, 0).setData(Qt.UserRole, attr)

                self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(type_text))
                self.table.item(row, 1).setData(Qt.UserRole, attr_type)

                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(value_str))

                use_btn = QtWidgets.QPushButton(t("btn.use"))
                use_btn.clicked.connect(partial(self.on_use_clicked, row))
                self.table.setCellWidget(row, 3, use_btn)

            except Exception as e:
                continue

        self.table.resizeRowsToContents()

    def filter_attributes(self, text):
        """根据搜索文本过滤属性"""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                attr_name = item.text().lower()
                text_lower = text.lower()
                self.table.setRowHidden(row, text_lower not in attr_name)

    def on_use_clicked(self, row):
        """使用按钮点击"""
        item = self.table.item(row, 0)
        if item:
            attr_name = item.text()
            is_source = True
            self.attribute_selected.emit(attr_name, is_source)

    def on_attribute_double_clicked(self, item, column):
        """双击属性"""
        row = item.row()
        attr_item = self.table.item(row, 0)
        if attr_item:
            attr_name = attr_item.text()
            self.attribute_selected.emit(attr_name, True)

    def use_attribute(self, is_source):
        """使用选中的属性"""
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            QMessageBox.information(self, t("common.tip"), t("qtool.matprop.msg.select_attribute_first"))
            return

        row = selected_rows[0].row()
        attr_item = self.table.item(row, 0)
        if attr_item:
            attr_name = attr_item.text()
            self.attribute_selected.emit(attr_name, is_source)


def main():
    """QuickTool 入口函数"""
    import sys
    
    if QtWidgets is None:
        print("[MaterialMapper] 无法加载 PySide 模块")
        return

    print("[MaterialMapper] PySide 模块加载成功")

    app = QtWidgets.QApplication.instance()
    if not app:
        print("[MaterialMapper] 创建新的 QApplication")
        app = QtWidgets.QApplication(sys.argv)
        need_exec = True
    else:
        print("[MaterialMapper] 使用现有的 QApplication")
        need_exec = False

    parent_window = get_maya_main_window()
    print(f"[MaterialMapper] 父窗口: {parent_window}")

    print("[MaterialMapper] 创建对话框...")
    dialog = MaterialPropertyMapper(parent=parent_window)

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

    print("[MaterialMapper] 显示对话框...")
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    print("[MaterialMapper] 对话框已显示")

    if need_exec:
        print("[MaterialMapper] 进入事件循环...")
        app.exec()


# 主启动函数
def show_material_property_mapper():
    """显示材质属性映射工具窗口"""
    # 清理可能存在的旧窗口
    global mapper_window
    if 'mapper_window' in globals() and mapper_window is not None:
        try:
            mapper_window.close()
            mapper_window.deleteLater()
        except:
            pass
        mapper_window = None

    # 清理Qt应用中的旧窗口
    for widget in QtWidgets.QApplication.topLevelWidgets():
        try:
            if widget.__class__.__name__ == "MaterialPropertyMapper":
                widget.close()
                widget.deleteLater()
        except:
            pass

    try:
        mapper_window = MaterialPropertyMapper()
        mapper_window.show()

        # 确保窗口在最前面
        mapper_window.raise_()
        mapper_window.activateWindow()

        return mapper_window
    except Exception as e:
        print(f"创建窗口时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        QMessageBox.critical(None, t("msg.error"), t("qtool.matprop.msg.create_window_failed", e=str(e)))
        return None


# 直接运行脚本时显示窗口
if __name__ == "__main__":
    try:
        # 清理可能存在的旧窗口
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.__class__.__name__ == "MaterialPropertyMapper":
                widget.close()
                widget.deleteLater()
        
        # 显示新窗口
        main()
    except Exception as e:
        print(f"运行脚本时出错: {e}")
        # 尝试使用简单错误对话框
        try:
            error_msg = t("qtool.matprop.msg.script_run_failed", e=str(e))
            QtWidgets.QMessageBox.critical(None, t("msg.error"), error_msg)
        except Exception:
            print(error_msg)