import os
import sys
from ..utils.maya_utils import get_qt_modules, qt_exec
from ..utils.mock_data import DEFAULT_CATEGORIES
from ..utils.settings import SettingsManager, apply_font_size_to_widget
from .detail_panel import FlowLayout

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class TagFlowWidget(QtWidgets.QWidget):
    tagToggled = QtCore.Signal(str, bool)

    def __init__(self, parent=None):
        super(TagFlowWidget, self).__init__(parent)
        self._tag_buttons = {}
        self._setup_ui()

    def _setup_ui(self):
        self._layout = FlowLayout(self, margin=0, spacing=4)
        self._layout.setSpacing(4)

    def set_tags(self, tags, selected_tags=None):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tag_buttons.clear()

        if selected_tags is None:
            selected_tags = set()

        for tag in tags:
            btn = QtWidgets.QPushButton(tag)
            checked = tag in selected_tags
            btn.setCheckable(True)
            btn.setChecked(checked)
            btn.setStyleSheet(self._style_for_state(checked))
            btn.toggled.connect(lambda state, t=tag: self._on_tag_clicked(t, state))
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self._layout.addWidget(btn)
            self._tag_buttons[tag] = btn

    def _style_for_state(self, checked):
        if checked:
            return (
                "QPushButton { background-color: #2d4a6f; color: #5294e2; border: 1px solid #5294e2; "
                "border-radius: 12px; padding: 4px 12px; font-size: 12px; }"
                "QPushButton:hover { background-color: #3a5a8a; }"
            )
        return (
            "QPushButton { background-color: #2a2a2a; color: #909090; border: 1px solid #3a3a3a; "
            "border-radius: 12px; padding: 4px 12px; font-size: 12px; }"
            "QPushButton:hover { color: #d0d0d0; border-color: #5294e2; }"
        )

    def _on_tag_clicked(self, tag, checked):
        btn = self._tag_buttons.get(tag)
        if btn:
            btn.setChecked(checked)
            btn.setStyleSheet(self._style_for_state(checked))
            btn.repaint()
        self.tagToggled.emit(tag, checked)

    def get_selected_tags(self):
        return [tag for tag, btn in self._tag_buttons.items() if btn.isChecked()]


class ExportPresetDialog(QtWidgets.QDialog):
    VERSION = "1.0"

    def __init__(self, parent=None, materials=None):
        super(ExportPresetDialog, self).__init__(parent)
        self._materials = materials or []
        self._captured_pixmap = None
        self._set_window_flags()
        self.setWindowTitle(t("dialog.export_preset.title"))
        self.setMinimumSize(560, 620)
        self.resize(580, 680)
        self.setStyleSheet("background-color: #2a2a2a; color: #d0d0d0;")
        self._setup_ui()
        self._load_existing_tags()
        
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

    def _set_window_flags(self):
        flags = QtCore.Qt.WindowType.Dialog
        flags |= QtCore.Qt.WindowType.WindowCloseButtonHint
        flags |= QtCore.Qt.WindowType.WindowMaximizeButtonHint
        self.setWindowFlags(flags)

    def _setup_ui(self):
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll_content = QtWidgets.QWidget()
        self._main_layout = QtWidgets.QVBoxLayout(scroll_content)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(12)

        self._build_header()
        self._build_format_section()
        self._build_metadata_section()
        self._build_screenshot_section()
        self._build_batch_options()

        self._main_layout.addStretch()
        scroll.setWidget(scroll_content)
        root_layout.addWidget(scroll, 1)

        self._build_footer(root_layout)

    def _section_group(self, title):
        group = QtWidgets.QGroupBox(title)
        group.setStyleSheet(
            "QGroupBox { font-size: 13px; font-weight: bold; color: #e0e0e0; "
            "border: 1px solid #3a3a3a; border-radius: 6px; margin-top: 12px; padding: 16px 12px 12px 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }"
        )
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(8)
        return group, layout

    def _input_row(self, layout, label, widget, label_width=80):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(label_width)
        lbl.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        layout.addLayout(row)

    def _build_header(self):
        header = QtWidgets.QLabel(t("dialog.export_preset.title"))
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff; padding-bottom: 4px;")
        self._main_layout.addWidget(header)

        info = QtWidgets.QLabel()
        if self._materials:
            info.setText(t("dialog.export_preset.selected_count", n=len(self._materials)))
        else:
            info.setText(t("dialog.export_preset.no_selection"))
        info.setStyleSheet("color: #808080; font-size: 12px;")
        self._main_layout.addWidget(info)

    def _build_format_section(self):
        group, layout = self._section_group(t("dialog.export_preset.format_section"))

        dir_row = QtWidgets.QHBoxLayout()
        dir_row.setSpacing(8)
        dir_label = QtWidgets.QLabel(t("dialog.export_preset.export_dir"))
        dir_label.setFixedWidth(80)
        dir_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        self._dir_input = QtWidgets.QLineEdit()
        self._dir_input.setPlaceholderText(t("dialog.export_preset.select_export_dir"))
        self._dir_input.setStyleSheet("background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px 10px; color: #d0d0d0;")
        browse_btn = QtWidgets.QPushButton(t("dialog.export_preset.browse"))
        browse_btn.setFixedWidth(60)
        browse_btn.setStyleSheet("QPushButton { background-color: #3a3a3a; color: #d0d0d0; border: none; border-radius: 4px; padding: 6px 12px; } QPushButton:hover { background-color: #4a4a4a; }")
        browse_btn.clicked.connect(self._on_browse_dir)
        dir_row.addWidget(dir_label)
        dir_row.addWidget(self._dir_input, 1)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        name_row = QtWidgets.QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QtWidgets.QLabel(t("dialog.export_preset.file_name"))
        name_label.setFixedWidth(80)
        name_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        self._name_input = QtWidgets.QLineEdit()
        self._name_input.setPlaceholderText(t("dialog.export_preset.default_name_hint"))
        self._name_input.setStyleSheet("background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px 10px; color: #d0d0d0;")
        name_row.addWidget(name_label)
        name_row.addWidget(self._name_input, 1)
        layout.addLayout(name_row)

        sep1 = QtWidgets.QFrame()
        sep1.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #3a3a3a;")
        layout.addWidget(sep1)

        self._json_check = QtWidgets.QCheckBox(t("dialog.export_preset.export_json_default"))
        self._json_check.setChecked(True)
        self._json_check.setEnabled(False)
        self._json_check.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        layout.addWidget(self._json_check)

        self._ma_check = QtWidgets.QCheckBox(t("dialog.export_preset.export_ma_optional"))
        self._ma_check.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        layout.addWidget(self._ma_check)

        self._separate_check = QtWidgets.QCheckBox(t("dialog.export_preset.separate_files"))
        self._separate_check.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        layout.addWidget(self._separate_check)

        self._objects_check = QtWidgets.QCheckBox(t("dialog.export_preset.export_objects"))
        self._objects_check.setChecked(True)
        self._objects_check.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        layout.addWidget(self._objects_check)

        self._main_layout.addWidget(group)

    def _build_metadata_section(self):
        group, layout = self._section_group(t("dialog.export_preset.metadata_section"))

        cs_row = QtWidgets.QHBoxLayout()
        cs_row.setSpacing(8)
        cs_label = QtWidgets.QLabel(t("dialog.export_preset.color_space"))
        cs_label.setFixedWidth(80)
        cs_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        self._color_space_combo = QtWidgets.QComboBox()
        self._color_space_combo.addItems(["ACEScg", "sRGB", "Rec709", "linear"])
        self._color_space_combo.setCurrentText("ACEScg")
        self._color_space_combo.setStyleSheet(
            "QComboBox { background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; "
            "padding: 6px 10px; color: #d0d0d0; font-size: 13px; }"
            "QComboBox:hover { border-color: #5294e2; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background-color: #333333; color: #d0d0d0; selection-background-color: #4a4a4a; }"
        )
        cs_row.addWidget(cs_label)
        cs_row.addWidget(self._color_space_combo, 1)
        layout.addLayout(cs_row)

        cat_row = QtWidgets.QHBoxLayout()
        cat_row.setSpacing(8)
        cat_label = QtWidgets.QLabel(t("dialog.export_preset.material_category"))
        cat_label.setFixedWidth(80)
        cat_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        self._category_combo = QtWidgets.QComboBox()
        self._category_combo.addItem(t("dialog.export_preset.unspecified"), "")
        for cat in DEFAULT_CATEGORIES:
            self._category_combo.addItem(f"{cat['name_cn']} ({cat['name']})", cat['id'])
            for child in cat.get('children', []):
                self._category_combo.addItem(f"  {child['name_cn']} ({child['name']})", child['id'])
        self._category_combo.setStyleSheet(
            "QComboBox { background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; "
            "padding: 6px 10px; color: #d0d0d0; font-size: 13px; }"
            "QComboBox:hover { border-color: #5294e2; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background-color: #333333; color: #d0d0d0; selection-background-color: #4a4a4a; }"
        )
        cat_row.addWidget(cat_label)
        cat_row.addWidget(self._category_combo, 1)
        layout.addLayout(cat_row)

        cn_row = QtWidgets.QHBoxLayout()
        cn_row.setSpacing(8)
        cn_label = QtWidgets.QLabel(t("dialog.export_preset.chinese_name"))
        cn_label.setFixedWidth(80)
        cn_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        self._name_cn_input = QtWidgets.QLineEdit()
        self._name_cn_input.setPlaceholderText(t("dialog.export_preset.chinese_name_hint"))
        self._name_cn_input.setStyleSheet("background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px 10px; color: #d0d0d0;")
        cn_row.addWidget(cn_label)
        cn_row.addWidget(self._name_cn_input, 1)
        layout.addLayout(cn_row)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3a3a3a;")
        layout.addWidget(sep)

        tag_header_row = QtWidgets.QHBoxLayout()
        tag_header = QtWidgets.QLabel(t("dialog.export_preset.tags"))
        tag_header.setStyleSheet("color: #a0a0a0; font-size: 13px; font-weight: bold;")
        tag_header_row.addWidget(tag_header)
        tag_header_row.addStretch()

        edit_tags_btn = QtWidgets.QPushButton(t("dialog.export_preset.edit_tags"))
        edit_tags_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #808080; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #5294e2; }"
        )
        edit_tags_btn.clicked.connect(self._on_edit_tags)
        tag_header_row.addWidget(edit_tags_btn)
        layout.addLayout(tag_header_row)

        self._tag_flow = TagFlowWidget()
        self._tag_flow.setStyleSheet("background-color: transparent;")
        tag_scroll = QtWidgets.QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFixedHeight(140)
        tag_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_scroll.setStyleSheet("QScrollArea { border: 1px solid #3a3a3a; border-radius: 4px; background-color: #1a1a1a; }")
        tag_scroll.setWidget(self._tag_flow)
        layout.addWidget(tag_scroll)

        tag_input_row = QtWidgets.QHBoxLayout()
        tag_input_row.setSpacing(8)
        self._tag_input = QtWidgets.QLineEdit()
        self._tag_input.setPlaceholderText(t("dialog.export_preset.tag_input_hint"))
        self._tag_input.setStyleSheet("background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px 10px; color: #d0d0d0;")
        add_tag_btn = QtWidgets.QPushButton(t("dialog.export_preset.add_tag"))
        add_tag_btn.setFixedWidth(80)
        add_tag_btn.setStyleSheet("QPushButton { background-color: #2d4a6f; color: #5294e2; border: none; border-radius: 4px; padding: 6px 12px; } QPushButton:hover { background-color: #3a5a8a; }")
        add_tag_btn.clicked.connect(self._on_add_custom_tag)
        tag_input_row.addWidget(self._tag_input, 1)
        tag_input_row.addWidget(add_tag_btn)
        layout.addLayout(tag_input_row)

        self._main_layout.addWidget(group)

    def _build_screenshot_section(self):
        group, layout = self._section_group(t("dialog.export_preset.screenshot_section"))
        layout.setSpacing(10)

        preview_row = QtWidgets.QHBoxLayout()
        preview_row.setSpacing(12)

        preview_container = QtWidgets.QWidget()
        preview_container.setFixedSize(280, 200)
        preview_container.setStyleSheet("background-color: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 6px;")
        preview_layout_inner = QtWidgets.QVBoxLayout(preview_container)
        preview_layout_inner.setContentsMargins(0, 0, 0, 0)

        self._screenshot_label = QtWidgets.QLabel()
        self._screenshot_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._screenshot_label.setStyleSheet("background-color: transparent;")

        empty_pix = QtGui.QPixmap(278, 198)
        empty_pix.fill(QtGui.QColor("#1a1a1a"))
        painter = QtGui.QPainter(empty_pix)
        painter.setPen(QtGui.QColor(255, 255, 255, 40))
        font = painter.font()
        font.setPointSize(12)
        painter.setFont(font)
        painter.drawText(empty_pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, t("dialog.export_preset.click_to_capture"))
        painter.end()
        self._screenshot_label.setPixmap(empty_pix)

        preview_layout_inner.addWidget(self._screenshot_label)
        preview_row.addWidget(preview_container)

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        res_label = QtWidgets.QLabel(t("dialog.export_preset.screenshot_resolution"))
        res_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        controls_layout.addWidget(res_label)

        res_group = QtWidgets.QWidget()
        res_group_layout = QtWidgets.QHBoxLayout(res_group)
        res_group_layout.setContentsMargins(0, 0, 0, 0)
        res_group_layout.setSpacing(4)
        self._res_256 = QtWidgets.QRadioButton("256\u00d7256")
        self._res_512 = QtWidgets.QRadioButton("512\u00d7512")
        self._res_custom = QtWidgets.QRadioButton(t("dialog.export_preset.custom"))
        small_radio_style = "QRadioButton { color: #d0d0d0; font-size: 12px; } QRadioButton::indicator { width: 14px; height: 14px; }"
        self._res_256.setStyleSheet(small_radio_style)
        self._res_512.setStyleSheet(small_radio_style)
        self._res_custom.setStyleSheet(small_radio_style)
        self._res_256.setChecked(True)
        res_group_layout.addWidget(self._res_256)
        res_group_layout.addWidget(self._res_512)
        res_group_layout.addWidget(self._res_custom)
        controls_layout.addWidget(res_group)

        custom_res_row = QtWidgets.QHBoxLayout()
        custom_res_row.setSpacing(4)
        self._res_w_input = QtWidgets.QLineEdit("256")
        self._res_w_input.setFixedWidth(50)
        self._res_w_input.setStyleSheet("background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 4px 6px; color: #d0d0d0;")
        res_x_label = QtWidgets.QLabel("\u00d7")
        res_x_label.setStyleSheet("color: #909090;")
        self._res_h_input = QtWidgets.QLineEdit("256")
        self._res_h_input.setFixedWidth(50)
        self._res_h_input.setStyleSheet("background-color: #333333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 4px 6px; color: #d0d0d0;")
        custom_res_row.addWidget(self._res_w_input)
        custom_res_row.addWidget(res_x_label)
        custom_res_row.addWidget(self._res_h_input)
        custom_res_row.addStretch()
        controls_layout.addLayout(custom_res_row)
        self._res_w_input.setVisible(False)
        self._res_h_input.setVisible(False)
        res_x_label.setVisible(False)
        self._res_custom.toggled.connect(lambda checked: (
            self._res_w_input.setVisible(checked),
            self._res_h_input.setVisible(checked),
            res_x_label.setVisible(checked)
        ))

        controls_layout.addSpacing(4)

        capture_btn = QtWidgets.QPushButton(t("dialog.export_preset.capture_view"))
        capture_btn.setStyleSheet(
            "QPushButton { background-color: #2d6a4f; color: #ffffff; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background-color: #40916c; }"
        )
        capture_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        capture_btn.clicked.connect(self._on_capture_screenshot)
        controls_layout.addWidget(capture_btn)

        import_thumb_btn = QtWidgets.QPushButton(t("dialog.export_preset.import_thumbnail"))
        import_thumb_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #d0d0d0; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        import_thumb_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        import_thumb_btn.clicked.connect(self._on_import_thumbnail)
        controls_layout.addWidget(import_thumb_btn)

        controls_layout.addStretch()
        preview_row.addWidget(controls, 1)
        layout.addLayout(preview_row)

        self._main_layout.addWidget(group)

    def _build_batch_options(self):
        group, layout = self._section_group(t("dialog.export_preset.batch_options"))
        layout.setSpacing(8)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(8)
        mode_label = QtWidgets.QLabel(t("dialog.export_preset.export_mode"))
        mode_label.setFixedWidth(80)
        mode_label.setStyleSheet("color: #a0a0a0; font-size: 13px;")
        self._mode_selection = QtWidgets.QRadioButton(t("dialog.export_preset.export_selection"))
        self._mode_all = QtWidgets.QRadioButton(t("dialog.export_preset.export_all"))
        self._mode_selection.setChecked(True)
        radio_style = "QRadioButton { color: #d0d0d0; font-size: 13px; } QRadioButton::indicator { width: 16px; height: 16px; }"
        self._mode_selection.setStyleSheet(radio_style)
        self._mode_all.setStyleSheet(radio_style)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self._mode_selection)
        mode_row.addWidget(self._mode_all)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        self._batch_screenshot_check = QtWidgets.QCheckBox(t("dialog.export_preset.batch_screenshot"))
        self._batch_screenshot_check.setChecked(True)
        self._batch_screenshot_check.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        self._batch_screenshot_check.setToolTip(t("dialog.export_preset.batch_screenshot_tip"))
        layout.addWidget(self._batch_screenshot_check)

        self._main_layout.addWidget(group)

    def _build_footer(self, root_layout):
        footer = QtWidgets.QWidget()
        footer.setStyleSheet("background-color: transparent;")
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(8)

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setStyleSheet("color: #808080; font-size: 12px;")
        footer_layout.addWidget(self._status_label, 1)

        cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        cancel_btn.setFixedWidth(80)
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #d0d0d0; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-size: 13px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        export_btn = QtWidgets.QPushButton(t("dialog.export_preset.start_export"))
        export_btn.setFixedWidth(120)
        export_btn.setStyleSheet(
            "QPushButton { background-color: #2d6a4f; color: #ffffff; border: none; "
            "border-radius: 4px; padding: 8px 16px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #40916c; }"
        )
        export_btn.clicked.connect(self._on_export)
        footer_layout.addWidget(export_btn)

        root_layout.addWidget(footer)

    def _find_manager(self):
        """沿父级查找 MaterialManager"""
        p = self.parent()
        while p:
            if hasattr(p, '_material_manager'):
                return p._material_manager
            p = p.parent()
        return None

    def _get_all_tags(self):
        """获取所有标签：从 Manager 读取"""
        mgr = self._find_manager()
        if mgr:
            return mgr.get_common_tags()
        return []

    def _load_existing_tags(self):
        """加载常用标签到勾选流式布局"""
        self._tag_flow.set_tags(self._get_all_tags())

    def _on_edit_tags(self):
        """编辑标签库 → 添加/删除常用标签"""
        mgr = self._find_manager()
        tags = list(mgr.get_common_tags()) if mgr else []

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(t("dialog.export_preset.edit_tag_library"))
        dlg.setFixedSize(400, 340)
        dlg.setStyleSheet("background-color: #2a2a2a;")
        lyt = QtWidgets.QVBoxLayout(dlg); lyt.setSpacing(8)

        tags_w = QtWidgets.QWidget()
        tags_l = FlowLayout(tags_w, margin=4, spacing=3)

        def rebuild():
            while tags_l.count():
                it = tags_l.takeAt(0)
                if it.widget(): it.widget().deleteLater()
            for t in tags:
                btn = QtWidgets.QPushButton(f"\u2716 {t}")
                btn.setStyleSheet(
                    "QPushButton { background-color: #2a3a4a; color: #5294e2; border: 1px solid #3a5a7a; "
                    "border-radius: 10px; padding: 2px 8px; font-size: 12px; }"
                    "QPushButton:hover { background-color: #3a1a1a; color: #e06060; border-color: #603030; }"
                )
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                def make_remove(tag):
                    def handler():
                        if tag in tags:
                            tags.remove(tag)
                            if mgr: mgr.remove_common_tag(tag)
                            QtCore.QTimer.singleShot(0, rebuild)
                        # 刷新对话框内的标签
                        self._reload_tags(mgr)
                    return handler
                btn.clicked.connect(make_remove(t))
                tags_l.addWidget(btn)

        rebuild()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setWidget(tags_w)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #3a3a3a; border-radius: 4px; background-color: #1a1a1a; }")
        lyt.addWidget(scroll, 1)

        add_row = QtWidgets.QHBoxLayout()
        add_input = QtWidgets.QLineEdit()
        add_input.setPlaceholderText(t("dialog.export_preset.new_tag_name"))
        add_input.setStyleSheet("background-color: #333; border: 1px solid #4a4a4a; border-radius: 3px; padding: 5px 8px; color: #e0e0e0;")
        add_row.addWidget(add_input, 1)
        add_btn = QtWidgets.QPushButton(t("dialog.export_preset.add"))
        add_btn.setStyleSheet("QPushButton { background-color: #2d4a6f; color: #5294e2; border: none; padding: 6px 14px; border-radius: 3px; } QPushButton:hover { background-color: #3a5a8a; }")
        def do_add():
            t = add_input.text().strip()
            if t and t not in tags:
                tags.append(t)
                if mgr: mgr.add_common_tag(t)
                add_input.clear()
                rebuild()
                self._reload_tags(mgr)
        add_btn.clicked.connect(do_add)
        add_row.addWidget(add_btn)
        lyt.addLayout(add_row)

        close_btn = QtWidgets.QPushButton(t("common.close"))
        close_btn.setStyleSheet("QPushButton { background-color: #3a3a3a; color: #d0d0d0; border: none; padding: 6px; border-radius: 3px; } QPushButton:hover { background-color: #4a4a4a; }")
        close_btn.clicked.connect(dlg.accept)
        lyt.addWidget(close_btn)

        qt_exec(dlg)

    def _reload_tags(self, mgr):
        """刷新标签列表"""
        tags_src = list(mgr.get_common_tags()) if mgr else []
        # 合并已有材质标签
        from ..utils.mock_data import MOCK_MATERIALS
        existing = set()
        for m in MOCK_MATERIALS:
            for t in m.get("tags", []):
                existing.add(t)
        all_tags = sorted(existing.union(tags_src))
        self._tag_flow.set_tags(all_tags)

    def _on_browse_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, t("dialog.export_preset.select_export_dir_title"))
        if path:
            self._dir_input.setText(path.replace("\\", "/"))

    def _on_add_custom_tag(self):
        text = self._tag_input.text().strip()
        if not text:
            return
        parts = [t.strip() for t in text.split(",") if t.strip()]
        current_sel = self._tag_flow.get_selected_tags()
        for tag in parts:
            if tag and tag not in self._tag_flow._tag_buttons:
                self._tag_flow.set_tags(
                    list(self._tag_flow._tag_buttons.keys()) + [tag],
                    current_sel + [tag]
                )
            elif tag in self._tag_flow._tag_buttons:
                btn = self._tag_flow._tag_buttons[tag]
                btn.setChecked(True)
                if tag not in current_sel:
                    current_sel.append(tag)
        self._tag_input.clear()

    def _get_resolution(self):
        if self._res_256.isChecked():
            return 256, 256
        elif self._res_512.isChecked():
            return 512, 512
        else:
            try:
                w = int(self._res_w_input.text())
                h = int(self._res_h_input.text())
                return max(64, min(w, 4096)), max(64, min(h, 4096))
            except ValueError:
                return 256, 256

    def _on_capture_screenshot(self):
        w, h = self._get_resolution()
        try:
            import maya.cmds as cmds
            sel = cmds.ls(selection=True)
            if not sel:
                QtWidgets.QMessageBox.information(self, t("dialog.export_preset.notice"), t("dialog.export_preset.select_object_first"))
                return
            cmds.select(sel)
            panel = cmds.getPanel(withFocus=True)
            temp_window = cmds.window(width=w, height=h)
            cmds.paneLayout()
            temp_panel = cmds.modelPanel()
            camera = cmds.modelPanel(panel, query=True, camera=True) if panel else None
            if camera:
                cmds.modelPanel(temp_panel, edit=True, camera=camera)
            image_path = os.path.join(os.environ.get("TMP", os.environ.get("TEMP", "/tmp")), "_export_thumb_temp.png")
            cmds.playblast(
                format="image", compression="png",
                filename=image_path.replace(".png", ""),
                widthHeight=(w, h), viewer=False,
                showOrnaments=False, completeFilenameOnly=True,
                frame=cmds.currentTime(query=True),
            )
            cmds.deleteUI(temp_window)
            possible = image_path
            if os.path.exists(possible):
                self._captured_pixmap = QtGui.QPixmap(possible)
            else:
                base = image_path.replace(".png", "")
                for f in os.listdir(os.path.dirname(base)):
                    if f.startswith(os.path.basename(base)):
                        full = os.path.join(os.path.dirname(base), f)
                        self._captured_pixmap = QtGui.QPixmap(full)
                        break
            if self._captured_pixmap and not self._captured_pixmap.isNull():
                scaled = self._captured_pixmap.scaled(
                    278, 198, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation
                )
                self._screenshot_label.setPixmap(scaled)
                self._status_label.setText(t("dialog.export_preset.capture_success", w=w, h=h))
            else:
                self._status_label.setText(t("dialog.export_preset.capture_failed"))
        except ImportError:
            self._status_label.setText(t("dialog.export_preset.capture_needs_maya"))

    def _on_import_thumbnail(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, t("dialog.export_preset.select_thumbnail"), "",
            t("dialog.export_preset.image_files")
        )
        if path:
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                self._captured_pixmap = pix
                scaled = pix.scaled(
                    278, 198, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation
                )
                self._screenshot_label.setPixmap(scaled)
                self._status_label.setText(t("dialog.export_preset.thumbnail_imported", name=os.path.basename(path)))

    def _get_collected_tags(self):
        selected = set(self._tag_flow.get_selected_tags())
        custom = [t.strip() for t in self._tag_input.text().split(",") if t.strip()]
        selected.update(custom)
        return list(selected)

    def _on_export(self):
        target_dir = self._dir_input.text().strip()
        if not target_dir:
            QtWidgets.QMessageBox.warning(self, t("dialog.export_preset.notice"), t("dialog.export_preset.select_export_dir_first"))
            return
        if not os.path.isdir(target_dir):
            try:
                os.makedirs(target_dir)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, t("dialog.export_preset.error"), t("dialog.export_preset.create_dir_failed", e=e))
                return

        custom_name = self._name_input.text().strip() or None
        separate = self._separate_check.isChecked()
        export_objects = self._objects_check.isChecked()
        export_ma = self._ma_check.isChecked()
        color_space = self._color_space_combo.currentText()
        category = self._category_combo.currentData()
        tags_list = self._get_collected_tags()
        tags_str = ",".join(tags_list) if tags_list else None
        name_cn = self._name_cn_input.text().strip() or None
        export_all = self._mode_all.isChecked()
        batch_screenshot = self._batch_screenshot_check.isChecked()

        result = {
            "target_dir": target_dir,
            "custom_name": custom_name,
            "separate_files": separate,
            "export_objects": export_objects,
            "export_ma": export_ma,
            "color_space": color_space,
            "category": category,
            "tags": tags_list,
            "tags_str": tags_str,
            "name_cn": name_cn,
            "export_all": export_all,
            "batch_screenshot": batch_screenshot,
            "captured_pixmap": self._captured_pixmap,
        }

        self.accept()
        self._do_actual_export(result)

    def _do_actual_export(self, params):
        try:
            import maya.cmds as cmds
            from ..integration.zjg_exporter import (
                radar_export_materials,
                ma_export_materials,
                _pack_ma_textures,
            )

            radar_export_materials(
                target_dir=params["target_dir"],
                custom_name=params["custom_name"],
                separate_files=params["separate_files"],
                export_objects=params["export_objects"],
                color_space=params["color_space"],
                category=params["category"],
                tags=params["tags_str"],
                export_all=params["export_all"],
                name_cn=params["name_cn"],
            )

            if params["export_ma"]:
                ma_export_materials(
                    target_dir=params["target_dir"],
                    custom_name=params["custom_name"],
                    separate_files=params["separate_files"],
                )

            print(f"[ExportPreset] \u5bfc\u51fa\u5b8c\u6210: {params['target_dir']}")

            if params["batch_screenshot"] and params["captured_pixmap"]:
                thumb_dir = os.path.join(params["target_dir"], "thumbnails")
                os.makedirs(thumb_dir, exist_ok=True)
                thumb_path = os.path.join(thumb_dir, "preview.png")
                params["captured_pixmap"].save(thumb_path)
                print(f"[ExportPreset] \u9884\u89c8\u56fe\u5df2\u4fdd\u5b58: {thumb_path}")

        except ImportError as e:
            print(f"[ExportPreset] \u5bfc\u51fa\u5931\u8d25\uff08\u975eMaya\u73af\u5883\uff09: {e}")
        except Exception as e:
            print(f"[ExportPreset] \u5bfc\u51fa\u5f02\u5e38: {e}")
            import traceback
            traceback.print_exc()
