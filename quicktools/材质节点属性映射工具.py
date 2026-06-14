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
PRESET_DIR = os.path.join(SCRIPT_DIR, "..", "Assets", "material_mapper_presets")

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

        self.setWindowTitle("材质属性映射工具 - Maya 2025")
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

        self.add_btn = QtWidgets.QPushButton("添加行")
        self.add_btn.clicked.connect(lambda: self.add_row_with_options())
        button_layout.addWidget(self.add_btn)

        self.remove_btn = QtWidgets.QPushButton("移除选中行")
        self.remove_btn.clicked.connect(self.remove_selected_rows)
        button_layout.addWidget(self.remove_btn)

        self.browser_btn = QtWidgets.QPushButton("属性浏览器")
        self.browser_btn.clicked.connect(self.show_attribute_browser)
        button_layout.addWidget(self.browser_btn)

        button_layout.addStretch()

        self.help_btn = QtWidgets.QPushButton("使用帮助")
        self.help_btn.clicked.connect(self.show_help_dialog)
        button_layout.addWidget(self.help_btn)

        self.save_preset_btn = QtWidgets.QPushButton("保存预设")
        self.save_preset_btn.clicked.connect(self.save_preset_dialog)
        button_layout.addWidget(self.save_preset_btn)

        self.load_preset_btn = QtWidgets.QPushButton("加载预设")
        self.load_preset_btn.clicked.connect(self.load_preset_dialog)
        button_layout.addWidget(self.load_preset_btn)

        main_layout.addLayout(button_layout)



        # 节点类型显示区域
        node_type_layout = QtWidgets.QHBoxLayout()
        
        # 源节点选择
        source_layout = QtWidgets.QVBoxLayout()
        source_node_layout = QtWidgets.QHBoxLayout()
        source_node_label = QtWidgets.QLabel("源节点类型:")
        self.source_node_type = QtWidgets.QLineEdit()
        self.source_node_type.setReadOnly(True)
        source_node_browse_btn = QtWidgets.QPushButton("浏览")
        source_node_browse_btn.clicked.connect(lambda: self.browse_node(True))
        source_node_layout.addWidget(source_node_label)
        source_node_layout.addWidget(self.source_node_type)
        source_node_layout.addWidget(source_node_browse_btn)
        source_layout.addLayout(source_node_layout)
        
        # 源节点操作按钮
        source_buttons_layout = QtWidgets.QHBoxLayout()
        clear_source_btn = QtWidgets.QPushButton("清空源属性")
        clear_source_btn.clicked.connect(self.clear_source_attributes)
        source_buttons_layout.addWidget(clear_source_btn)
        source_layout.addLayout(source_buttons_layout)
        
        node_type_layout.addLayout(source_layout)
        
        # 目标节点选择
        target_layout = QtWidgets.QVBoxLayout()
        target_node_layout = QtWidgets.QHBoxLayout()
        target_node_label = QtWidgets.QLabel("目标节点类型:")
        self.target_node_type = QtWidgets.QLineEdit()
        self.target_node_type.setReadOnly(True)
        target_node_browse_btn = QtWidgets.QPushButton("浏览")
        target_node_browse_btn.clicked.connect(lambda: self.browse_node(False))
        target_node_layout.addWidget(target_node_label)
        target_node_layout.addWidget(self.target_node_type)
        target_node_layout.addWidget(target_node_browse_btn)
        target_layout.addLayout(target_node_layout)
        
        # 目标节点操作按钮
        target_buttons_layout = QtWidgets.QHBoxLayout()
        clear_target_btn = QtWidgets.QPushButton("清空目标属性")
        clear_target_btn.clicked.connect(self.clear_target_attributes)
        target_buttons_layout.addWidget(clear_target_btn)
        target_layout.addLayout(target_buttons_layout)
        
        node_type_layout.addLayout(target_layout)
        
        node_type_layout.addStretch()
        main_layout.addLayout(node_type_layout)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["选择", "材质属性", "目标属性", "转换函数", "默认值"])
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
        info_label = QtWidgets.QLabel(f"预设路径: {self.preset_dir}")
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)

        self.open_folder_btn = QtWidgets.QPushButton("打开文件夹")
        self.open_folder_btn.clicked.connect(self.open_preset_folder)
        info_layout.addWidget(self.open_folder_btn)

        main_layout.addLayout(info_layout)

        # 保存原始resize事件
        self.table._original_resize_event = self.table.resizeEvent
        # 重写resize事件以保持列比例
        self.table.resizeEvent = self._table_resize_event

        button_layout2 = QtWidgets.QHBoxLayout()

        self.reverse_btn = QtWidgets.QPushButton("反向映射")
        self.reverse_btn.clicked.connect(self.reverse_mapping)
        button_layout2.addWidget(self.reverse_btn)

        self.clear_btn = QtWidgets.QPushButton("清空表格")
        self.clear_btn.clicked.connect(self.clear_table)
        button_layout2.addWidget(self.clear_btn)

        example_btn = QtWidgets.QPushButton("加载示例数据")
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
        default_edit.setPlaceholderText("默认值")
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
                    clear_action = menu.addAction("清空源属性")
                else:
                    clear_action = menu.addAction("清空目标属性")

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
            clear_action = menu.addAction("清空源属性")
        else:
            clear_action = menu.addAction("清空目标属性")

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
            QMessageBox.warning(self, "警告", f"属性 '{new_text}' 已在其他行存在，已清空重复项")

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
            QMessageBox.warning(self, "警告", f"属性 '{new_text}' 已在其他行存在，已清空重复项")

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
            QMessageBox.information(self, "提示", "请先选择要删除的行（选中行或勾选复选框）")
    
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
            QMessageBox.warning(self, "警告", "没有可保存的映射数据")
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

            QMessageBox.information(self, "成功", f"预设已保存到:\n{filepath}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败:\n{str(e)}")
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
            QMessageBox.critical(self, "错误", f"加载失败:\n{str(e)}")
            return False
    
    def save_preset_dialog(self):
        """打开保存预设对话框"""
        # 生成默认文件名：源节点类型_目标节点类型
        source_type = self.source_node_type.text() if self.source_node_type else ""
        target_type = self.target_node_type.text() if self.target_node_type else ""
        default_filename = f"{source_type}_{target_type}" if (source_type and target_type) else "material_mapping"
        default_filepath = os.path.join(self.preset_dir, default_filename)
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, "保存预设", default_filepath, "Mapping Files (*.mmap)"
        )
        
        if filepath:
            if not filepath.endswith('.mmap'):
                filepath += '.mmap'
            self.save_preset(filepath)
    
    def load_preset_dialog(self):
        """打开加载预设对话框"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载预设", self.preset_dir, "Mapping Files (*.mmap)"
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
            QMessageBox.warning(self, "错误", f"无法打开文件夹:\n{str(e)}")

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
                QMessageBox.warning(self, "错误", f"获取节点类型失败: {e}")
        else:
            QMessageBox.warning(self, "警告", "请先在Maya中选择一个节点")

    def show_attribute_browser(self):
        """显示属性浏览器对话框"""
        selected_objects = cmds.ls(selection=True)
        if not selected_objects:
            QMessageBox.warning(self, "警告", "请先在Maya中选择一个对象")
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
        help_text = """
<h2>材质属性映射工具 - 使用说明</h2>

<h3>一、基本功能</h3>
<p>本工具用于定义材质节点间的属性映射关系，支持设置默认值和转换函数。
主要用于不同渲染器材质类型之间的属性转换（如 lambert → aiStandardSurface）。</p>

<h3>二、快速上手</h3>
<ol>
<li><b>设置节点类型</b>：在"Maya中选择一个材质节点" → 点击源/目标的"浏览"按钮，
工具会自动识别节点类型并加载所有可读写属性到下拉列表。
您也可以直接在文本框中手动输入节点类型名称（如 <code>lambert</code>、<code>aiStandardSurface</code>）。</li>
<li><b>添加映射行</b>：点击"添加行"按钮，每行的下拉列表会自动填充对应节点类型的
所有属性，方便快速选择。</li>
<li><b>配置映射</b>：在每行中选择源属性、目标属性，可选择性填写转换函数和默认值。</li>
<li><b>保存预设</b>：点击"保存预设"将映射配置存为.mmap文件，下次可通过"加载预设"快速恢复。</li>
</ol>

<h3>三、界面按钮说明</h3>
<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
<tr><td><b>添加行</b></td><td>新增一行映射，自动根据已设置的节点类型填充属性下拉选项</td></tr>
<tr><td><b>移除选中行</b></td><td>删除当前选中的行或复选框被勾选的行（支持多选）</td></tr>
<tr><td><b>属性浏览器</b></td><td>打开属性浏览器窗口，可搜索和查看场景中选中节点的所有属性及当前值</td></tr>
<tr><td><b>使用帮助</b></td><td>显示本帮助窗口</td></tr>
<tr><td><b>保存预设</b></td><td>将当前所有映射配置导出为.mmap预设文件</td></tr>
<tr><td><b>加载预设</b></td><td>从.mmap预设文件恢复映射配置（包含节点类型信息）</td></tr>
<tr><td><b>清空源属性</b></td><td>将所有行的源属性列重置为"(无)"</td></tr>
<tr><td><b>清空目标属性</b></td><td>将所有行的目标属性列重置为"(无)"</td></tr>
<tr><td><b>反向映射</b></td><td>交换源和目标角色：节点类型互换、每行的源/目标属性互换</td></tr>
<tr><td><b>清空表格</b></td><td>删除表格中所有行</td></tr>
<tr><td><b>加载示例数据</b></td><td>加载一组常用的PBR材质属性映射示例（baseColor→color等）</td></tr>
<tr><td><b>打开文件夹</b></td><td>在资源管理器中打开预设文件存储目录</td></tr>
</table>

<h3>四、右键菜单</h3>
<p>工具提供两处右键菜单：</p>
<ul>
<li><b>表格单元格右键</b>：在源属性列或目标属性列上右键，可快速"清空源属性"或"清空目标属性"</li>
<li><b>下拉列表右键</b>：在下拉列表控件上右键，可快速清空该行的属性选择</li>
</ul>

<h3>五、智能功能</h3>
<ul>
<li><b>重复属性检测</b>：当您在一行中选择某个属性后，如果其他行已存在相同属性，
工具会自动清空重复项并弹出警告，确保每侧的属性不重复。</li>
<li><b>自动匹配</b>：当加载目标节点属性时，工具会自动尝试匹配同名属性
和语义相近的属性（如 baseColor → color、roughness → roughness 等），
减少手动配置工作量。</li>
<li><b>自动保存</b>：关闭窗口时自动保存当前配置为"_last_preset.mmap"，
下次打开工具时自动恢复上次的工作状态。</li>
<li><b>节点类型自动填充</b>：设置节点类型后，添加新行时下拉列表会自动包含
该类型的所有可读写属性，无需手动输入属性名。</li>
</ul>

<h3>六、转换函数与默认值</h3>

<h4>转换函数</h4>
<p>转换函数下拉框提供预设的常用转换函数，显示为中文名称：</p>
<ul>
<li><b>RGB通道处理：</b>RGB取红、RGB取绿、RGB取蓝、RGB转灰度</li>
<li><b>透明度/透射：</b>透明度转透射</li>
<li><b>粗糙度转换：</b>Blinn高光锐度转粗糙度、Phong光泽度转粗糙度等</li>
<li><b>PBR参数：</b>折射率转F0、镜面反射强度转权重等</li>
<li><b>其他材质参数：</b>自发光转发光亮度、半透明度转次表面散射等</li>
<li><b>颜色运算：</b>颜色乘标量、颜色相加、颜色插值</li>
</ul>
<p>您也可以手动输入自定义转换函数（如lambda表达式）：</p>
<ul>
<li>变量 <code>x</code> 代表源属性的值</li>
<li>颜色类型(RGB)：x是包含3个值的列表，如 <code>[1.0, 0.5, 0.25]</code></li>
<li>标量类型：x是单个数值</li>
</ul>
<p><b>自定义示例：</b></p>
<ul>
<li>颜色反转：<code>lambda x: [1-x[0], 1-x[1], 1-x[2]]</code></li>
<li>颜色亮度加倍：<code>lambda x: [min(x[0]*2,1), min(x[1]*2,1), min(x[2]*2,1)]</code></li>
<li>数值缩放：<code>lambda x: x * 0.5</code></li>
</ul>

<h4>默认值</h4>
<p>当源属性不存在或为空时使用默认值：</p>
<ul>
<li>数值类型：直接输入数字，如 <code>0.5</code></li>
<li>颜色类型：用空格或逗号分隔，如 <code>1 0.5 0.25</code> 或 <code>1,0.5,0.25</code></li>
</ul>

<h3>七、常见问题</h3>

<h4>1. 为什么添加行时下拉列表是空的？</h4>
<p>需要先设置源/目标节点类型。在场景中选择材质节点后点击"浏览"按钮，
或直接在节点类型文本框中输入类型名称（如 <code>lambert</code>）。</p>

<h4>2. 属性映射不生效</h4>
<ul>
<li>确保源属性和目标属性名称正确（可使用属性浏览器确认）</li>
<li>检查属性类型是否兼容（如color不能直接赋值给float）</li>
<li>不同渲染器的材质属性名称可能不同，建议使用属性浏览器确认</li>
</ul>

<h4>3. 转换函数报错</h4>
<ul>
<li>确保转换函数语法正确，建议先在Maya脚本编辑器中测试</li>
<li>注意颜色类型返回列表，标量类型返回数值</li>
</ul>

<h4>4. 加载预设后节点类型正确但属性不对</h4>
<p>预设文件保存了节点类型名称，加载时会自动创建临时节点来获取属性列表。
如果该节点类型在当前Maya版本中不可用（如未加载对应插件），下拉列表会为空。</p>

<h4>5. 为什么属性列表里没有某些属性？</h4>
<p>工具只显示<b>可读写且可见</b>的属性。只读属性、隐藏属性和被锁定的属性不会出现在列表中。</p>

<h3>八、数据格式说明</h3>
<p>保存的.mmap文件格式如下：</p>
<pre>
{
    "version": "3.0",
    "name": "lambert → aiStandardSurface",
    "software": "maya",
    "source_type": "lambert",
    "target_type": "aiStandardSurface",
    "description": "lambert 到 aiStandardSurface 的属性映射",
    "created_date": "2026-04-28",
    "mappings": [
        {
            "source_attribute": "color",
            "target_attribute": "baseColor",
            "transform": "",
            "default_value": "0.8 0.8 0.8"
        }
    ]
}
</pre>

<h3>九、操作技巧</h3>
<ul>
<li>使用 <b>Shift</b> 或 <b>Ctrl</b> 键可多选行进行批量删除</li>
<li>勾选每行首列的复选框也可以标记要删除的行</li>
<li>下拉列表支持<b>手动输入</b>，可以填入自定义的属性名</li>
<li>属性浏览器支持<b>搜索过滤</b>，快速定位目标属性</li>
<li>先设置源节点类型再设置目标节点类型，可触发自动匹配</li>
</ul>

<h3>十、技术支持</h3>
<p>如遇到其他问题，请检查Maya脚本编辑器（Script Editor）中的错误信息，
错误详情会打印在输出窗口中。</p>
"""

        help_dialog = QtWidgets.QDialog(self)
        help_dialog.setWindowTitle("使用帮助")
        help_dialog.setMinimumSize(500, 400)
        help_dialog.resize(700, 600)
        help_dialog.setModal(False)

        layout = QtWidgets.QVBoxLayout(help_dialog)

        text_browser = QtWidgets.QTextBrowser()
        text_browser.setHtml(help_text)
        text_browser.setOpenExternalLinks(True)
        text_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #f5f5f5;
                color: #333;
                font-size: 16px;
                padding: 10px;
            }
            QTextBrowser h2 {
                color: #2196F3;
                font-size: 24px;
            }
            QTextBrowser h3 {
                color: #4CAF50;
                font-size: 20px;
                margin-top: 15px;
            }
            QTextBrowser h4 {
                color: #FF9800;
                font-size: 18px;
                margin-top: 10px;
            }
            QTextBrowser code {
                background-color: #eee;
                padding: 2px 5px;
                font-family: Consolas, monospace;
            }
            QTextBrowser pre {
                background-color: #2d2d2d;
                color: #f8f8f2;
                padding: 10px;
                border-radius: 5px;
                font-family: Consolas, monospace;
            }
            QTextBrowser table {
                border: 1px solid #ccc;
                border-collapse: collapse;
                margin: 10px 0;
            }
            QTextBrowser td {
                border: 1px solid #ccc;
                padding: 6px 10px;
            }
        """)

        layout.addWidget(text_browser)

        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.clicked.connect(help_dialog.close)
        layout.addWidget(close_btn)

        help_dialog.show()


class AttributeBrowserDialog(QtWidgets.QDialog):
    """属性浏览器对话框"""

    attribute_selected = QtCore.Signal(str, bool)

    ATTRIBUTE_TYPE_MAP = {
        'double': '数值',
        'float': '数值',
        'int': '整数',
        'long': '整数',
        'short': '整数',
        'byte': '整数',
        'char': '整数',
        'bool': '布尔',
        'double2': '二维向量',
        'double3': '三维向量',
        'float2': '二维向量',
        'float3': '三维向量',
        'vector': '向量',
        'string': '字符串',
        'message': '消息',
        'time': '时间',
        'doubleArray': '数值数组',
        'floatArray': '数值数组',
        'intArray': '整数数组',
        'stringArray': '字符串数组',
        'vectorArray': '向量数组',
    }

    def __init__(self, node_name, parent=None):
        super(AttributeBrowserDialog, self).__init__(parent)
        self.node_name = node_name
        self.setWindowTitle(f"属性浏览器 - {node_name}")
        self.setMinimumSize(500, 400)
        self.setup_ui()
        self.load_attributes()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        search_layout = QtWidgets.QHBoxLayout()
        search_layout.addWidget(QtWidgets.QLabel("搜索:"))
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.textChanged.connect(self.filter_attributes)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["属性名", "类型", "当前值", "操作"])
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

        self.use_as_source_btn = QtWidgets.QPushButton("用作源属性")
        self.use_as_source_btn.clicked.connect(lambda: self.use_attribute(True))
        button_layout.addWidget(self.use_as_source_btn)

        self.use_as_target_btn = QtWidgets.QPushButton("用作目标属性")
        self.use_as_target_btn.clicked.connect(lambda: self.use_attribute(False))
        button_layout.addWidget(self.use_as_target_btn)

        button_layout.addStretch()

        close_btn = QtWidgets.QPushButton("关闭")
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

                type_text = self.ATTRIBUTE_TYPE_MAP.get(attr_type, attr_type)

                row = self.table.rowCount()
                self.table.insertRow(row)

                self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(attr))
                self.table.item(row, 0).setData(Qt.UserRole, attr)

                self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(type_text))
                self.table.item(row, 1).setData(Qt.UserRole, attr_type)

                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(value_str))

                use_btn = QtWidgets.QPushButton("使用")
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
            QMessageBox.information(self, "提示", "请先选择一个属性")
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
        QMessageBox.critical(None, "错误", f"创建窗口时出错:\n{str(e)}")
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
            error_msg = f"脚本运行失败:\n{str(e)}"
            QtWidgets.QMessageBox.critical(None, "错误", error_msg)
        except Exception:
            print(error_msg)