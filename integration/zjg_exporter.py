# -*- coding: utf-8 -*-
# ========== UI 界面 ==========
# 算法功能已拆至 zjg_core（编译为 pyd 保护源码），本模块仅保留 UI 类与入口。
# 通过 `from .zjg_core import *` 重新导出算法函数与常量，
# 保证外部 `from ..integration.zjg_exporter import xxx` 兼容可用。
from .zjg_core import *  # noqa: F401,F403
# ========== UI 界面 ==========

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
        QLabel, QLineEdit, QPushButton, QCheckBox, QFileDialog, QGroupBox, QMessageBox, QRadioButton,
        QListView, QTreeView
    )
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView
except ImportError:
    from PySide2.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
        QLabel, QLineEdit, QPushButton, QCheckBox, QFileDialog, QGroupBox, QMessageBox, QRadioButton,
        QListView, QTreeView
    )
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QAbstractItemView


def _select_multiple_directories(parent, title='选择文件夹'):
    """弹出可多选文件夹的对话框，返回选中的文件夹路径列表"""
    dialog = QFileDialog(parent, title)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.resize(dialog.width() * 2, dialog.height() * 2)
    for view_type in (QListView, QTreeView):
        for view in dialog.findChildren(view_type):
            if hasattr(view, 'setSelectionMode'):
                view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    if dialog.exec_():
        return dialog.selectedFiles()
    return []



class RadarTabWidget(QWidget):
    """全频雷达版选项卡（包含导入和导出）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        title_label = QLabel("全频雷达版 - 完整材质网络导出/导入")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title_label)

        help_btn = QPushButton("ℹ 使用帮助")
        help_btn.setFixedHeight(28)
        help_btn.clicked.connect(self.show_radar_help)
        help_btn.setToolTip("点击查看详细使用帮助")
        layout.addWidget(help_btn)

        info_label = QLabel(">> 选择场景中的模型或材质，导出完整材质网络；或导入.zmetal还原材质")
        layout.addWidget(info_label)

        export_basic_group = QGroupBox("导出 - 基础设置")
        export_basic_layout = QVBoxLayout()
        export_basic_layout.setSpacing(6)

        dir_layout = QHBoxLayout()
        dir_label = QLabel('工作目录:')
        dir_label.setFixedWidth(75)
        self.dir_input = QLineEdit()
        self.dir_input.setToolTip("指定导出文件的保存位置")
        browse_btn = QPushButton('浏览')
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self.browse_folder)
        browse_btn.setToolTip("点击选择工作目录")
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(browse_btn)
        export_basic_layout.addLayout(dir_layout)

        name_layout = QHBoxLayout()
        name_label = QLabel('文件名:')
        name_label.setFixedWidth(75)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('留空使用默认命名')
        self.name_input.setToolTip("导出的JSON文件名，留空使用默认命名（当前时间）")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        export_basic_layout.addLayout(name_layout)

        export_basic_group.setLayout(export_basic_layout)
        layout.addWidget(export_basic_group)

        export_options_group = QGroupBox("导出 - 选项")
        export_options_layout = QVBoxLayout()
        export_options_layout.setSpacing(6)

        export_mode_layout = QHBoxLayout()
        export_mode_label = QLabel('导出模式:')
        export_mode_label.setFixedWidth(75)
        self.export_selection_radio = QRadioButton('导出选择')
        self.export_all_radio = QRadioButton('导出全部')
        self.export_selection_radio.setChecked(True)
        self.export_selection_radio.setToolTip("只导出当前选择的模型或材质")
        self.export_all_radio.setToolTip("导出场景中所有材质")
        export_mode_layout.addWidget(export_mode_label)
        export_mode_layout.addWidget(self.export_selection_radio)
        export_mode_layout.addWidget(self.export_all_radio)
        export_options_layout.addLayout(export_mode_layout)

        self.selection_is_material_checkbox = QCheckBox('所选对象为材质节点（而非模型）')
        self.selection_is_material_checkbox.setToolTip("如果当前选择的是材质节点而不是模型，请勾选此选项")
        export_options_layout.addWidget(self.selection_is_material_checkbox)

        self.separate_checkbox = QCheckBox('每个材质导出为独立文件')
        self.separate_checkbox.setToolTip("为每个材质生成单独的.zmetal文件")
        export_options_layout.addWidget(self.separate_checkbox)

        self.export_objects_checkbox = QCheckBox('导出材质对应模型数据')
        self.export_objects_checkbox.setChecked(True)
        self.export_objects_checkbox.setToolTip("生成额外的.mcm映射文件，记录材质与模型的对应关系")
        export_options_layout.addWidget(self.export_objects_checkbox)

        self.export_metadata_checkbox = QCheckBox('导出元数据')
        self.export_metadata_checkbox.setChecked(True)
        self.export_metadata_checkbox.setToolTip("生成独立的.ameta元数据文件，包含版本、渲染器、色彩空间、分类、标签等信息")
        export_options_layout.addWidget(self.export_metadata_checkbox)

        self.create_folder_checkbox = QCheckBox('创建材质文件夹')
        self.create_folder_checkbox.setToolTip("每个材质创建独立文件夹，包含材质.zmetal、元数据.ameta和textures子文件夹")
        export_options_layout.addWidget(self.create_folder_checkbox)

        self.pack_textures_checkbox = QCheckBox('打包纹理')
        self.pack_textures_checkbox.setToolTip("将材质连接的纹理拷贝到textures文件夹，并替换路径为相对路径")
        export_options_layout.addWidget(self.pack_textures_checkbox)

        export_btn = QPushButton('执行导出')
        export_btn.setFixedHeight(32)
        export_btn.setStyleSheet('background-color: #669966; color: white; font-weight: bold;')
        export_btn.clicked.connect(self.do_export)
        export_btn.setToolTip("开始执行材质导出")
        export_options_layout.addWidget(export_btn)

        export_options_group.setLayout(export_options_layout)
        layout.addWidget(export_options_group)

        self.export_metadata_header = QPushButton("导出 - 元数据设置 ►")
        self.export_metadata_header.setFlat(True)
        self.export_metadata_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.export_metadata_header.setCheckable(True)
        self.export_metadata_header.setChecked(False)
        self.export_metadata_header.clicked.connect(self._on_export_metadata_toggle)
        layout.addWidget(self.export_metadata_header)

        self.export_metadata_content = QWidget()
        export_metadata_content_layout = QVBoxLayout()
        export_metadata_content_layout.setSpacing(6)
        export_metadata_content_layout.setContentsMargins(10, 0, 10, 5)

        color_space_layout = QHBoxLayout()
        color_space_label = QLabel('色彩空间:')
        color_space_label.setFixedWidth(75)
        detected_cs = _detect_color_space()
        self.color_space_input = QLineEdit(detected_cs)
        self.color_space_input.setPlaceholderText('ACEScg')
        self.color_space_input.setToolTip("当前渲染器自动检测的色彩空间，可手动修改")
        color_space_layout.addWidget(color_space_label)
        color_space_layout.addWidget(self.color_space_input)
        export_metadata_content_layout.addLayout(color_space_layout)

        category_layout = QHBoxLayout()
        category_label = QLabel('Category:')
        category_label.setFixedWidth(75)
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText('材质分类，如: 金属/布料/皮肤')
        self.category_input.setToolTip("材质的分类信息，用于材质库管理")
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.category_input)
        export_metadata_content_layout.addLayout(category_layout)

        tags_layout = QHBoxLayout()
        tags_label = QLabel('Tags:')
        tags_label.setFixedWidth(75)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText('逗号分隔，如: metal,rough,pbr')
        self.tags_input.setToolTip("材质的标签，多个标签用逗号分隔")
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self.tags_input)
        export_metadata_content_layout.addLayout(tags_layout)

        name_cn_layout = QHBoxLayout()
        name_cn_label = QLabel('中文名:')
        name_cn_label.setFixedWidth(75)
        self.name_cn_input = QLineEdit()
        self.name_cn_input.setPlaceholderText('留空则使用英文原名')
        self.name_cn_input.setToolTip("材质的中文名称，留空则使用英文名称")
        name_cn_layout.addWidget(name_cn_label)
        name_cn_layout.addWidget(self.name_cn_input)
        export_metadata_content_layout.addLayout(name_cn_layout)

        self.export_metadata_content.setLayout(export_metadata_content_layout)
        self.export_metadata_content.setVisible(False)
        layout.addWidget(self.export_metadata_content)

        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #CCCCCC;")
        layout.addWidget(separator)

        self.import_basic_header = QPushButton("导入 - 基础设置 ►")
        self.import_basic_header.setFlat(True)
        self.import_basic_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.import_basic_header.setCheckable(True)
        self.import_basic_header.setChecked(False)
        self.import_basic_header.clicked.connect(self._on_import_basic_toggle)
        layout.addWidget(self.import_basic_header)

        self.import_basic_content = QWidget()
        import_basic_content_layout = QVBoxLayout()
        import_basic_content_layout.setSpacing(6)
        import_basic_content_layout.setContentsMargins(10, 0, 10, 5)

        ns_layout = QHBoxLayout()
        ns_label = QLabel('命名空间:')
        ns_label.setFixedWidth(75)
        self.ns_input = QLineEdit()
        self.ns_input.setPlaceholderText('引用模型时自带的前缀，如: ns1')
        self.ns_input.setToolTip("引用模型时自带的前缀，用于匹配导出时的模型路径")
        ns_layout.addWidget(ns_label)
        ns_layout.addWidget(self.ns_input)
        import_basic_content_layout.addLayout(ns_layout)

        prefix_suffix_layout = QHBoxLayout()
        prefix_label = QLabel('材质前缀:')
        prefix_label.setFixedWidth(75)
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText('如: Imported_')
        self.prefix_input.setToolTip("在导入的材质名称前添加前缀，避免命名冲突")
        prefix_suffix_layout.addWidget(prefix_label)
        prefix_suffix_layout.addWidget(self.prefix_input)

        suffix_label = QLabel('材质后缀:')
        suffix_label.setFixedWidth(75)
        self.suffix_input = QLineEdit()
        self.suffix_input.setPlaceholderText('如: _Import')
        self.suffix_input.setToolTip("在导入的材质名称后添加后缀，避免命名冲突")
        prefix_suffix_layout.addWidget(suffix_label)
        prefix_suffix_layout.addWidget(self.suffix_input)
        import_basic_content_layout.addLayout(prefix_suffix_layout)

        path_replace_layout = QHBoxLayout()
        old_prefix_label = QLabel('原模型前缀:')
        old_prefix_label.setFixedWidth(85)
        self.old_prefix_input = QLineEdit()
        self.old_prefix_input.setPlaceholderText('导出时的模型前缀，如: OLD_')
        self.old_prefix_input.setToolTip("导出时的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(old_prefix_label)
        path_replace_layout.addWidget(self.old_prefix_input)

        new_prefix_label = QLabel('新模型前缀:')
        new_prefix_label.setFixedWidth(85)
        self.new_prefix_input = QLineEdit()
        self.new_prefix_input.setPlaceholderText('替换为，如: NEW_')
        self.new_prefix_input.setToolTip("导入到当前场景的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(new_prefix_label)
        path_replace_layout.addWidget(self.new_prefix_input)
        import_basic_content_layout.addLayout(path_replace_layout)

        suffix_replace_layout = QHBoxLayout()
        old_suffix_label = QLabel('原模型后缀:')
        old_suffix_label.setFixedWidth(85)
        self.old_suffix_input = QLineEdit()
        self.old_suffix_input.setPlaceholderText('导出时的模型后缀，如: _GEO')
        self.old_suffix_input.setToolTip("导出时的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(old_suffix_label)
        suffix_replace_layout.addWidget(self.old_suffix_input)

        new_suffix_label = QLabel('新模型后缀:')
        new_suffix_label.setFixedWidth(85)
        self.new_suffix_input = QLineEdit()
        self.new_suffix_input.setPlaceholderText('替换为，如: _MESH')
        self.new_suffix_input.setToolTip("导入到当前场景的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(new_suffix_label)
        suffix_replace_layout.addWidget(self.new_suffix_input)
        import_basic_content_layout.addLayout(suffix_replace_layout)

        self.import_basic_content.setLayout(import_basic_content_layout)
        self.import_basic_content.setVisible(False)
        layout.addWidget(self.import_basic_content)

        import_options_group = QGroupBox("导入 - 选项")
        import_options_layout = QVBoxLayout()
        import_options_layout.setSpacing(6)

        import_mode_layout = QHBoxLayout()
        import_mode_label = QLabel('导入模式:')
        import_mode_label.setFixedWidth(75)
        self.import_selection_radio = QRadioButton('导入选择')
        self.import_all_radio = QRadioButton('导入全部')
        self.import_all_radio.setChecked(True)
        self.import_selection_radio.setToolTip("只导入JSON文件中选中的材质")
        self.import_all_radio.setToolTip("导入JSON文件中的所有材质")
        import_mode_layout.addWidget(import_mode_label)
        import_mode_layout.addWidget(self.import_selection_radio)
        import_mode_layout.addWidget(self.import_all_radio)
        import_options_layout.addLayout(import_mode_layout)

        self.assign_objects_checkbox = QCheckBox('导入后指定材质给模型')
        self.assign_objects_checkbox.setChecked(True)
        self.assign_objects_checkbox.setToolTip("自动将导入的材质指定给对应模型")
        import_options_layout.addWidget(self.assign_objects_checkbox)

        self.fuzzy_match_checkbox = QCheckBox('模糊匹配模型名称')
        self.fuzzy_match_checkbox.setToolTip("当精确匹配失败时，通过名称子串包含关系进行匹配\n例如：导出时模型为TEST_pSphere1_temp，导入时模型为pSphere1，勾选后可正确匹配")
        import_options_layout.addWidget(self.fuzzy_match_checkbox)

        self.import_copy_textures_checkbox = QCheckBox('拷贝贴图到当前工程')
        self.import_copy_textures_checkbox.setChecked(True)
        self.import_copy_textures_checkbox.setToolTip("将材质关联的纹理拷贝到当前Maya工程的sourceimages目录。不勾选则使用材质数据所在文件夹的相对路径")
        import_options_layout.addWidget(self.import_copy_textures_checkbox)

        import_btn = QPushButton('执行导入')
        import_btn.setFixedHeight(32)
        import_btn.setStyleSheet('background-color: #6680AA; color: white; font-weight: bold;')
        import_btn.clicked.connect(self.do_import)
        import_btn.setToolTip("开始执行材质导入")
        import_options_layout.addWidget(import_btn)

        folder_import_btn = QPushButton('从文件夹导入...')
        folder_import_btn.setFixedHeight(28)
        folder_import_btn.setStyleSheet('background-color: #5a7a9a; color: white;')
        folder_import_btn.clicked.connect(self.do_import_from_folder)
        folder_import_btn.setToolTip("选择文件夹，递归导入文件夹内所有材质.zmetal/.json文件")
        import_options_layout.addWidget(folder_import_btn)

        import_options_group.setLayout(import_options_layout)
        layout.addWidget(import_options_group)

        layout.addStretch()

    def _on_export_metadata_toggle(self):
        is_expanded = self.export_metadata_header.isChecked()
        self.export_metadata_content.setVisible(is_expanded)
        self.export_metadata_header.setText("导出 - 元数据设置 ▼" if is_expanded else "导出 - 元数据设置 ►")

    def _on_import_basic_toggle(self):
        is_expanded = self.import_basic_header.isChecked()
        self.import_basic_content.setVisible(is_expanded)
        self.import_basic_header.setText("导入 - 基础设置 ▼" if is_expanded else "导入 - 基础设置 ►")

    def show_radar_help(self):
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton
        except ImportError:
            from PySide2.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton

        help_window = QDialog(self)
        help_window.setWindowTitle("全频雷达版使用帮助")
        help_window.resize(600, 500)

        layout = QVBoxLayout(help_window)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        help_text = QLabel()
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.RichText)
        # 使文本可选择
        help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_text.setText(
            "<b>全频雷达版 - 完整材质网络导出/导入</b><br><br>"
            "本工具提供完整的材质参数导出和导入功能，支持选择模型或材质进行操作。<br><br>"
            "<b>【导出功能】</b><br>"
            "• 支持选择场景中的模型(transform)或材质节点进行导出<br>"
            "• 导出内容包括：所有材质属性、连接信息、贴图节点等完整参数<br>"
            "• 可选择每个材质导出为独立文件，或所有材质合并为一个文件<br>"
            "• 勾选「导出材质对应模型数据」会生成额外的.mcm映射文件<br><br>"
            "• <b>注意：材质数据保存为.zmetal格式</b>（实际为JSON格式，只是后缀不同）<br><br>"
            "<b>【UI控件说明】</b><br>"
            "<b>导出 - 基础设置</b><br>"
            "• <b>工作目录</b>：指定导出文件的保存位置<br>"
            "• <b>文件名</b>：导出的JSON文件名，留空使用默认命名（当前时间）<br><br>"
            "<b>导出 - 选项</b><br>"
            "• <b>导出模式</b>：「导出选择」只导出当前选中对象；「导出全部」导出场景中所有材质<br>"
            "• <b>每个材质导出为独立文件</b>：是否为每个材质生成单独的.zmetal文件<br>"
            "• <b>导出材质对应模型数据</b>：是否生成.mcm映射文件<br><br>"
            "<b>导出 - 元数据设置</b><br>"
            "• <b>色彩空间</b>：指定导出材质的色彩空间，默认值为ACEScg<br>"
            "• <b>Category</b>：材质的分类信息，用于材质库管理，如：金属、布料、皮肤等<br>"
            "• <b>Tags</b>：材质的标签，多个标签用逗号分隔，如：metal,rough,pbr<br><br>"
            "<b>导入 - 基础设置</b><br>"
            "• <b>命名空间</b>：引用模型时自带的前缀，如ns1，用于匹配导出时的模型路径<br>"
            "• <b>材质前缀</b>：在导入的材质名称前添加前缀，避免命名冲突<br>"
            "• <b>材质后缀</b>：在导入的材质名称后添加后缀，避免命名冲突<br>"
            "• <b>原模型前缀</b>：导出时的模型路径前缀，用于路径替换<br>"
            "• <b>新模型前缀</b>：导入到当前场景的模型路径前缀，用于路径替换<br>"
            "• <b>原模型后缀</b>：导出时的模型路径后缀，用于路径替换<br>"
            "• <b>新模型后缀</b>：导入到当前场景的模型路径后缀，用于路径替换<br><br>"
            "<b>导入 - 选项</b><br>"
            "• <b>导入模式</b>：「导入选择」只导入选中的材质；「导入全部」导入所有材质<br>"
            "• <b>导入后指定材质给模型</b>：是否自动将材质指定给对应模型<br><br>"
            "<b>【常见问题】</b><br>"
            "• <b>导出时找不到材质？</b> 确保选择的对象是transform节点或材质节点<br>"
            "• <b>导入后材质不显示？</b> 检查材质是否正确连接到shadingEngine<br>"
            "• <b>指定材质失败？</b> 确保场景中存在模型物体，且路径与导出时一致<br>"
            "• <b>后缀不起作用？</b> 后缀会自动加在原材质名后面（不包括下划线，需手动添加）<br>"
            "• <b>实例物体材质丢失？</b> 工具已处理实例物体，但需确保选择的是顶层transform<br>"
        )

        scroll_area.setWidget(help_text)
        layout.addWidget(scroll_area)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(help_window.close)
        layout.addWidget(close_btn)

        help_window.setModal(False)
        help_window.show()

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择工作目录')
        if path:
            self.dir_input.setText(path.replace('\\', '/'))

    def do_export(self):
        target_dir = self.dir_input.text()
        custom_name = self.name_input.text()
        separate_files = self.separate_checkbox.isChecked()
        export_objects = self.export_objects_checkbox.isChecked()
        export_all = self.export_all_radio.isChecked()
        selection_is_material = self.selection_is_material_checkbox.isChecked()
        export_metadata = self.export_metadata_checkbox.isChecked()
        create_material_folder = self.create_folder_checkbox.isChecked()
        pack_textures = self.pack_textures_checkbox.isChecked()
        color_space = self.color_space_input.text().strip() or None
        category = self.category_input.text().strip() or None
        tags = self.tags_input.text().strip() or None
        name_cn = self.name_cn_input.text().strip() or None
        radar_export_materials(
            target_dir if target_dir else None,
            custom_name if custom_name else None,
            separate_files,
            export_objects,
            color_space,
            category,
            tags,
            export_all,
            name_cn,
            selection_is_material,
            export_metadata,
            create_material_folder,
            pack_textures
        )

    def do_import(self):
        user_ns = self.ns_input.text().strip().rstrip(":") or None
        user_prefix = self.prefix_input.text().strip() or None
        user_suffix = self.suffix_input.text().strip() or None
        old_path_prefix = self.old_prefix_input.text().strip() or None
        new_path_prefix = self.new_prefix_input.text().strip() or None
        old_path_suffix = self.old_suffix_input.text().strip() or None
        new_path_suffix = self.new_suffix_input.text().strip() or None

        start_dir = self.dir_input.text().strip()
        assign_objects = self.assign_objects_checkbox.isChecked()
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        files = QFileDialog.getOpenFileNames(
            self, '选择材质文件', start_dir if start_dir else '', 'Material Files (*.zmetal *.json)'
        )[0]

        if files:
            radar_import_materials(files, user_ns, user_prefix, user_suffix, assign_objects=assign_objects, old_path_prefix=old_path_prefix, new_path_prefix=new_path_prefix, old_path_suffix=old_path_suffix, new_path_suffix=new_path_suffix, import_selection=import_selection, fuzzy_match=fuzzy_match, copy_textures=copy_textures)
        elif start_dir:
            radar_import_materials(None, user_ns, user_prefix, user_suffix, start_dir, assign_objects, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, import_selection, fuzzy_match=fuzzy_match, copy_textures=copy_textures)

    def do_import_from_folder(self):
        user_ns = self.ns_input.text().strip().rstrip(":") or None
        user_prefix = self.prefix_input.text().strip() or None
        user_suffix = self.suffix_input.text().strip() or None
        old_path_prefix = self.old_prefix_input.text().strip() or None
        new_path_prefix = self.new_prefix_input.text().strip() or None
        old_path_suffix = self.old_suffix_input.text().strip() or None
        new_path_suffix = self.new_suffix_input.text().strip() or None
        assign_objects = self.assign_objects_checkbox.isChecked()
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        dirs = _select_multiple_directories(self, '选择材质文件夹（可按住Ctrl多选）')

        if not dirs:
            return

        json_files = _collect_json_files_from_dirs(dirs)
        if not json_files:
            cmds.warning(t("zjg.no_material_files_in_folder"))
            return

        print(f"[文件夹导入] 找到 {len(json_files)} 个材质文件")
        radar_import_materials(json_files, user_ns, user_prefix, user_suffix, assign_objects=assign_objects, old_path_prefix=old_path_prefix, new_path_prefix=new_path_prefix, old_path_suffix=old_path_suffix, new_path_suffix=new_path_suffix, import_selection=import_selection, fuzzy_match=fuzzy_match, copy_textures=copy_textures)


class MATabWidget(QWidget):
    """MA+JSON版选项卡（包含导入和导出）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        title_label = QLabel("MA+JSON版 - 材质网络导出为MA文件")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title_label)

        help_btn = QPushButton("ℹ 使用帮助")
        help_btn.setFixedHeight(28)
        help_btn.clicked.connect(self.show_ma_help)
        help_btn.setToolTip("点击查看详细使用帮助")
        layout.addWidget(help_btn)

        warning_label = QLabel(">> [重要] 请确保当前场景里已经有需要被赋值的模型！")
        warning_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(warning_label)

        export_basic_group = QGroupBox("导出 - 基础设置")
        export_basic_layout = QVBoxLayout()
        export_basic_layout.setSpacing(6)

        dir_layout = QHBoxLayout()
        dir_label = QLabel('工作目录:')
        dir_label.setFixedWidth(75)
        self.dir_input = QLineEdit()
        self.dir_input.setToolTip("指定导出文件的保存位置")
        browse_btn = QPushButton('浏览')
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self.browse_folder)
        browse_btn.setToolTip("点击选择工作目录")
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(browse_btn)
        export_basic_layout.addLayout(dir_layout)

        name_layout = QHBoxLayout()
        name_label = QLabel('文件名:')
        name_label.setFixedWidth(75)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText('留空使用默认命名')
        self.name_input.setToolTip("导出的MA和JSON文件名，留空使用默认命名（当前时间）")
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        export_basic_layout.addLayout(name_layout)

        export_basic_group.setLayout(export_basic_layout)
        layout.addWidget(export_basic_group)

        export_options_group = QGroupBox("导出 - 选项")
        export_options_layout = QVBoxLayout()
        export_options_layout.setSpacing(6)

        self.separate_checkbox = QCheckBox('每个材质导出为独立文件')
        self.separate_checkbox.setToolTip("为每个材质生成单独的MA和JSON文件")
        self.separate_checkbox.setChecked(True)
        export_options_layout.addWidget(self.separate_checkbox)

        self.export_metadata_checkbox = QCheckBox('导出元数据')
        self.export_metadata_checkbox.setChecked(True)
        self.export_metadata_checkbox.setToolTip("生成独立的.ameta元数据文件，包含版本、渲染器、色彩空间、分类、标签等信息")
        export_options_layout.addWidget(self.export_metadata_checkbox)

        self.create_folder_checkbox = QCheckBox('创建材质文件夹')
        self.create_folder_checkbox.setToolTip("每个材质创建独立文件夹，包含MA、JSON、元数据JSON和textures子文件夹")
        export_options_layout.addWidget(self.create_folder_checkbox)

        self.pack_textures_checkbox = QCheckBox('打包纹理')
        self.pack_textures_checkbox.setToolTip("将材质连接的纹理拷贝到textures文件夹，并替换MA文件中的路径为相对路径")
        export_options_layout.addWidget(self.pack_textures_checkbox)

        export_btn = QPushButton("执行导出")
        export_btn.setFixedHeight(32)
        export_btn.setStyleSheet('background-color: #669966; color: white; font-weight: bold;')
        export_btn.clicked.connect(self.do_export)
        export_btn.setToolTip("开始执行材质导出")
        export_options_layout.addWidget(export_btn)

        export_options_group.setLayout(export_options_layout)
        layout.addWidget(export_options_group)

        self.ma_export_metadata_header = QPushButton("导出 - 元数据设置 ►")
        self.ma_export_metadata_header.setFlat(True)
        self.ma_export_metadata_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.ma_export_metadata_header.setCheckable(True)
        self.ma_export_metadata_header.setChecked(False)
        self.ma_export_metadata_header.clicked.connect(self._on_ma_export_metadata_toggle)
        layout.addWidget(self.ma_export_metadata_header)

        self.ma_export_metadata_content = QWidget()
        ma_export_metadata_content_layout = QVBoxLayout()
        ma_export_metadata_content_layout.setSpacing(6)
        ma_export_metadata_content_layout.setContentsMargins(10, 0, 10, 5)

        color_space_layout = QHBoxLayout()
        color_space_label = QLabel('色彩空间:')
        color_space_label.setFixedWidth(75)
        detected_cs = _detect_color_space()
        self.ma_color_space_input = QLineEdit(detected_cs)
        self.ma_color_space_input.setPlaceholderText('ACEScg')
        self.ma_color_space_input.setToolTip("当前渲染器自动检测的色彩空间，可手动修改")
        color_space_layout.addWidget(color_space_label)
        color_space_layout.addWidget(self.ma_color_space_input)
        ma_export_metadata_content_layout.addLayout(color_space_layout)

        category_layout = QHBoxLayout()
        category_label = QLabel('Category:')
        category_label.setFixedWidth(75)
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText('材质分类，如: 金属/布料/皮肤')
        self.category_input.setToolTip("材质的分类信息，用于材质库管理")
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.category_input)
        ma_export_metadata_content_layout.addLayout(category_layout)

        tags_layout = QHBoxLayout()
        tags_label = QLabel('Tags:')
        tags_label.setFixedWidth(75)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText('逗号分隔，如: metal,rough,pbr')
        self.tags_input.setToolTip("材质的标签，多个标签用逗号分隔")
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self.tags_input)
        ma_export_metadata_content_layout.addLayout(tags_layout)

        name_cn_layout = QHBoxLayout()
        name_cn_label = QLabel('中文名:')
        name_cn_label.setFixedWidth(75)
        self.name_cn_input = QLineEdit()
        self.name_cn_input.setPlaceholderText('留空则使用英文原名')
        self.name_cn_input.setToolTip("材质的中文名称，留空则使用英文名称")
        name_cn_layout.addWidget(name_cn_label)
        name_cn_layout.addWidget(self.name_cn_input)
        ma_export_metadata_content_layout.addLayout(name_cn_layout)

        self.ma_export_metadata_content.setLayout(ma_export_metadata_content_layout)
        self.ma_export_metadata_content.setVisible(False)
        layout.addWidget(self.ma_export_metadata_content)

        self.ma_import_basic_header = QPushButton("导入 - 基础设置 ►")
        self.ma_import_basic_header.setFlat(True)
        self.ma_import_basic_header.setStyleSheet("text-align: left; padding: 2px 5px; font-weight: bold;")
        self.ma_import_basic_header.setCheckable(True)
        self.ma_import_basic_header.setChecked(False)
        self.ma_import_basic_header.clicked.connect(self._on_ma_import_basic_toggle)
        layout.addWidget(self.ma_import_basic_header)

        self.ma_import_basic_content = QWidget()
        ma_import_basic_content_layout = QVBoxLayout()
        ma_import_basic_content_layout.setSpacing(6)
        ma_import_basic_content_layout.setContentsMargins(10, 0, 10, 5)

        ns_layout = QHBoxLayout()
        ns_label = QLabel('命名空间:')
        ns_label.setFixedWidth(70)
        self.ns_edit = QLineEdit()
        self.ns_edit.setPlaceholderText("引用模型时自带的前缀，如: ns1")
        self.ns_edit.setToolTip("引用模型时自带的前缀，用于匹配导出时的模型路径")
        ns_layout.addWidget(ns_label)
        ns_layout.addWidget(self.ns_edit, 1)
        ma_import_basic_content_layout.addLayout(ns_layout)

        prefix_suffix_layout = QHBoxLayout()
        prefix_label = QLabel('材质前缀:')
        prefix_label.setFixedWidth(70)
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("如: Imported_")
        self.prefix_edit.setToolTip("在导入的材质名称前添加前缀，避免命名冲突")
        prefix_suffix_layout.addWidget(prefix_label)
        prefix_suffix_layout.addWidget(self.prefix_edit, 1)

        suffix_label = QLabel('材质后缀:')
        suffix_label.setFixedWidth(70)
        self.suffix_edit = QLineEdit()
        self.suffix_edit.setPlaceholderText("如: _Import")
        self.suffix_edit.setToolTip("在导入的材质名称后添加后缀，避免命名冲突")
        prefix_suffix_layout.addWidget(suffix_label)
        prefix_suffix_layout.addWidget(self.suffix_edit, 1)
        ma_import_basic_content_layout.addLayout(prefix_suffix_layout)

        path_replace_layout = QHBoxLayout()
        old_prefix_label = QLabel('原模型前缀:')
        old_prefix_label.setFixedWidth(80)
        self.old_prefix_edit = QLineEdit()
        self.old_prefix_edit.setPlaceholderText("导出时的模型前缀，如: OLD_")
        self.old_prefix_edit.setToolTip("导出时的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(old_prefix_label)
        path_replace_layout.addWidget(self.old_prefix_edit, 1)

        new_prefix_label = QLabel('新模型前缀:')
        new_prefix_label.setFixedWidth(80)
        self.new_prefix_edit = QLineEdit()
        self.new_prefix_edit.setPlaceholderText("替换为，如: NEW_")
        self.new_prefix_edit.setToolTip("导入到当前场景的模型路径前缀，用于路径替换")
        path_replace_layout.addWidget(new_prefix_label)
        path_replace_layout.addWidget(self.new_prefix_edit, 1)
        ma_import_basic_content_layout.addLayout(path_replace_layout)

        suffix_replace_layout = QHBoxLayout()
        old_suffix_label = QLabel('原模型后缀:')
        old_suffix_label.setFixedWidth(80)
        self.old_suffix_edit = QLineEdit()
        self.old_suffix_edit.setPlaceholderText("导出时的模型后缀，如: _GEO")
        self.old_suffix_edit.setToolTip("导出时的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(old_suffix_label)
        suffix_replace_layout.addWidget(self.old_suffix_edit, 1)

        new_suffix_label = QLabel('新模型后缀:')
        new_suffix_label.setFixedWidth(80)
        self.new_suffix_edit = QLineEdit()
        self.new_suffix_edit.setPlaceholderText("替换为，如: _MESH")
        self.new_suffix_edit.setToolTip("导入到当前场景的模型路径后缀，用于路径替换")
        suffix_replace_layout.addWidget(new_suffix_label)
        suffix_replace_layout.addWidget(self.new_suffix_edit, 1)
        ma_import_basic_content_layout.addLayout(suffix_replace_layout)

        self.ma_import_basic_content.setLayout(ma_import_basic_content_layout)
        self.ma_import_basic_content.setVisible(False)
        layout.addWidget(self.ma_import_basic_content)

        import_options_group = QGroupBox("导入 - 选项")
        import_options_layout = QVBoxLayout()
        import_options_layout.setSpacing(6)

        import_mode_layout = QHBoxLayout()
        import_mode_label = QLabel('导入模式:')
        import_mode_label.setFixedWidth(70)
        self.import_selection_radio = QRadioButton('导入选择')
        self.import_all_radio = QRadioButton('导入全部')
        self.import_all_radio.setChecked(True)
        self.import_selection_radio.setToolTip("只导入JSON文件中选中的材质")
        self.import_all_radio.setToolTip("导入JSON文件中的所有材质")
        import_mode_layout.addWidget(import_mode_label)
        import_mode_layout.addWidget(self.import_selection_radio)
        import_mode_layout.addWidget(self.import_all_radio)
        import_options_layout.addLayout(import_mode_layout)

        self.fuzzy_match_checkbox = QCheckBox('模糊匹配模型名称')
        self.fuzzy_match_checkbox.setToolTip("当精确匹配失败时，通过名称子串包含关系进行匹配\n例如：导出时模型为TEST_pSphere1_temp，导入时模型为pSphere1，勾选后可正确匹配")
        import_options_layout.addWidget(self.fuzzy_match_checkbox)

        self.import_copy_textures_checkbox = QCheckBox('拷贝贴图到当前工程')
        self.import_copy_textures_checkbox.setChecked(True)
        self.import_copy_textures_checkbox.setToolTip("将材质关联的纹理拷贝到当前Maya工程的sourceimages目录。不勾选则使用材质数据所在文件夹的相对路径")
        import_options_layout.addWidget(self.import_copy_textures_checkbox)

        import_btn = QPushButton("执行导入")
        import_btn.setFixedHeight(32)
        import_btn.setStyleSheet('background-color: #6680AA; color: white; font-weight: bold;')
        import_btn.clicked.connect(self.do_import)
        import_btn.setToolTip("开始执行材质导入")
        import_options_layout.addWidget(import_btn)

        folder_import_btn = QPushButton('从文件夹导入...')
        folder_import_btn.setFixedHeight(28)
        folder_import_btn.setStyleSheet('background-color: #5a7a9a; color: white;')
        folder_import_btn.clicked.connect(self.do_ma_import_from_folder)
        folder_import_btn.setToolTip("选择文件夹，递归导入文件夹内所有材质MA+JSON文件")
        import_options_layout.addWidget(folder_import_btn)

        import_options_group.setLayout(import_options_layout)
        layout.addWidget(import_options_group)

        layout.addStretch()

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择工作目录')
        if path:
            self.dir_input.setText(path.replace('\\', '/'))

    def do_export(self):
        target_dir = self.dir_input.text()
        custom_name = self.name_input.text()
        separate_files = self.separate_checkbox.isChecked()
        export_metadata = self.export_metadata_checkbox.isChecked()
        create_material_folder = self.create_folder_checkbox.isChecked()
        pack_textures = self.pack_textures_checkbox.isChecked()
        color_space = self.ma_color_space_input.text().strip() or None
        category = self.category_input.text().strip() or None
        tags = self.tags_input.text().strip() or None
        name_cn = self.name_cn_input.text().strip() or None
        ma_export_materials(
            target_dir if target_dir else None,
            custom_name if custom_name else None,
            separate_files,
            color_space,
            category,
            tags,
            name_cn,
            export_metadata,
            create_material_folder,
            pack_textures
        )

    def do_import(self):
        user_ns = self.ns_edit.text().strip().rstrip(":") or None
        user_prefix = self.prefix_edit.text().strip() or None
        user_suffix = self.suffix_edit.text().strip() or None
        old_path_prefix = self.old_prefix_edit.text().strip() or None
        new_path_prefix = self.new_prefix_edit.text().strip() or None
        old_path_suffix = self.old_suffix_edit.text().strip() or None
        new_path_suffix = self.new_suffix_edit.text().strip() or None
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        start_dir = self.dir_input.text().strip()

        file, _ = QFileDialog.getOpenFileName(
            self, '选择材质文件', start_dir if start_dir else '', 'Material Files (*.zmetal *.json)'
        )

        if file:
            ma_import_materials(file, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, import_selection, fuzzy_match, copy_textures)

    def do_ma_import_from_folder(self):
        user_ns = self.ns_edit.text().strip().rstrip(":") or None
        user_prefix = self.prefix_edit.text().strip() or None
        user_suffix = self.suffix_edit.text().strip() or None
        old_path_prefix = self.old_prefix_edit.text().strip() or None
        new_path_prefix = self.new_prefix_edit.text().strip() or None
        old_path_suffix = self.old_suffix_edit.text().strip() or None
        new_path_suffix = self.new_suffix_edit.text().strip() or None
        import_selection = self.import_selection_radio.isChecked()
        fuzzy_match = self.fuzzy_match_checkbox.isChecked()
        copy_textures = self.import_copy_textures_checkbox.isChecked()

        dirs = _select_multiple_directories(self, '选择材质文件夹（可按住Ctrl多选）')

        if not dirs:
            return

        json_files = _collect_json_files_from_dirs(dirs)
        if not json_files:
            cmds.warning(t("zjg.no_material_json_in_folder"))
            return

        print(f"[文件夹导入] 找到 {len(json_files)} 个材质MA+JSON文件")
        for jf in json_files:
            print(f"  [文件夹导入] 正在导入: {jf}")
            ma_import_materials(jf, user_ns, user_prefix, user_suffix, old_path_prefix, new_path_prefix, old_path_suffix, new_path_suffix, import_selection, fuzzy_match, copy_textures)

    def _on_ma_export_metadata_toggle(self):
        is_expanded = self.ma_export_metadata_header.isChecked()
        self.ma_export_metadata_content.setVisible(is_expanded)
        self.ma_export_metadata_header.setText("导出 - 元数据设置 ▼" if is_expanded else "导出 - 元数据设置 ►")

    def _on_ma_import_basic_toggle(self):
        is_expanded = self.ma_import_basic_header.isChecked()
        self.ma_import_basic_content.setVisible(is_expanded)
        self.ma_import_basic_header.setText("导入 - 基础设置 ▼" if is_expanded else "导入 - 基础设置 ►")

    def show_ma_help(self):
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton
        except ImportError:
            from PySide2.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QPushButton

        help_window = QDialog(self)
        help_window.setWindowTitle("MA 版使用帮助")
        help_window.resize(600, 500)

        layout = QVBoxLayout(help_window)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        help_text = QLabel()
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.RichText)
        # 使文本可选择
        help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_text.setText(
            "<b>MA+JSON版 - 材质网络导出为MA文件</b><br><br>"
            "本工具将材质网络导出为MA文件格式，适合引用到其他场景，保留完整的材质连接关系。<br><br>"
            "<b>【导出功能】</b><br>"
            "• 选择带有材质的模型(transform)节点进行导出<br>"
            "• 导出为两个文件：.ma材质文件和.json映射文件<br>"
            "• MA文件只包含材质节点，不包含模型数据<br>"
            "• JSON文件记录材质与模型的对应关系<br>"
            "• 可选择每个材质导出为独立文件，或所有材质合并为一个文件<br><br>"
            "<b>【UI控件说明】</b><br>"
            "<b>导出 - 基础设置</b><br>"
            "• <b>工作目录</b>：指定导出文件的保存位置<br>"
            "• <b>文件名</b>：导出的MA和JSON文件名，留空使用默认命名（当前时间）<br><br>"
            "<b>导出 - 选项</b><br>"
            "• <b>每个材质导出为独立文件</b>：是否为每个材质生成单独的MA和JSON文件<br><br>"
            "<b>导入 - 基础设置</b><br>"
            "• <b>命名空间</b>：引用模型时自带的前缀，如ns1，用于匹配导出时的模型路径<br>"
            "• <b>材质前缀</b>：在导入的材质名称前添加前缀，避免命名冲突<br>"
            "• <b>材质后缀</b>：在导入的材质名称后添加后缀，避免命名冲突<br>"
            "• <b>原模型前缀</b>：导出时的模型路径前缀，用于路径替换<br>"
            "• <b>新模型前缀</b>：导入到当前场景的模型路径前缀，用于路径替换<br>"
            "• <b>原模型后缀</b>：导出时的模型路径后缀，用于路径替换<br>"
            "• <b>新模型后缀</b>：导入到当前场景的模型路径后缀，用于路径替换<br><br>"
            "<b>导入 - 选项</b><br>"
            "• <b>导入模式</b>：「导入选择」只导入选中的材质；「导入全部」导入所有材质<br>"
            "• <b>执行导入</b>：点击后弹出文件选择窗口，选择JSON文件进行导入<br><br>"
            "<b>【模型匹配策略】</b><br>"
            "1. 优先使用完整长路径匹配<br>"
            "2. 如果填写了命名空间，使用命名空间+短名称匹配<br>"
            "3. 如果都没填，使用纯短名称匹配（可能匹配到错误的物体）<br><br>"
            "<b>【常见问题】</b><br>"
            "• <b>找不到配套MA文件？</b> 确保JSON和MA文件在同一目录下<br>"
            "• <b>模型匹配失败？</b> 检查命名空间是否正确，确保模型路径一致<br>"
            "• <b>材质指定到错误物体？</b> 可能是短名称重复导致，尝试填写命名空间<br>"
            "• <b>后缀不起作用？</b> 后缀会自动加在原材质名后面（不包括下划线，需手动添加）<br>"
            "• <b>导入后材质不显示？</b> 检查是否正确创建了 shadingEngine 并指定了模型<br>"
        )

        scroll_area.setWidget(help_text)
        layout.addWidget(scroll_area)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(help_window.close)
        layout.addWidget(close_btn)

        help_window.setModal(False)
        help_window.show()


class MaterialTransferTool(QMainWindow):
    WINDOW_OBJECT_NAME = 'MaterialTransferTool_Window'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(self.WINDOW_OBJECT_NAME)
        self.setWindowTitle('材质导入导出工具')
        self.resize(400, 600)

        self._is_closed = False
        self.closeEvent = self._on_close

        self._setup_ui()

    def _on_close(self, event):
        self._is_closed = True
        event.accept()

        central_widget = self.centralWidget()
        if central_widget:
            central_widget.deleteLater()
        self.deleteLater()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(5, 5, 5, 5)

        self.tabs = QTabWidget()

        self.radar_tab = RadarTabWidget()
        self.tabs.addTab(self.radar_tab, "全频雷达版")

        self.ma_tab = MATabWidget()
        self.tabs.addTab(self.ma_tab, "MA+JSON版")

        main_layout.addWidget(self.tabs)


_window_instance = None
_maya_main_window = None


def _get_maya_main_window():
    """延迟获取Maya主窗口，避免启动时卡顿"""
    global _maya_main_window
    if _maya_main_window is not None:
        return _maya_main_window
    
    try:
        import maya.OpenMayaUI as omui
        try:
            import shiboken6 as shiboken
        except ImportError:
            import shiboken2 as shiboken
        window_ptr = omui.MQtUtil.mainWindow()
        if window_ptr:
            _maya_main_window = shiboken.wrapInstance(int(window_ptr), QWidget)
            return _maya_main_window
    except Exception: pass
    return None


def show_ui():
    global _window_instance

    app = QApplication.instance()
    if not app:
        app = QApplication([])

    if _window_instance is not None:
        try:
            if _window_instance.isVisible():
                _window_instance.raise_()
                _window_instance.activateWindow()
            else:
                _window_instance.show()
                _window_instance.raise_()
                _window_instance.activateWindow()
        except Exception:
            _window_instance = None

    if _window_instance is None:
        maya_window = _get_maya_main_window()
        if maya_window:
            _window_instance = MaterialTransferTool(maya_window)
        else:
            _window_instance = MaterialTransferTool()

        _window_instance.show()
        # 处理所有待处理的事件，避免Maya卡住
        QApplication.processEvents()

    return _window_instance


if __name__ == '__main__':
    show_ui()
