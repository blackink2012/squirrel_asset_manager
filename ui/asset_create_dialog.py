# -*- coding: utf-8 -*-
"""
AssetCreateDialog V3 — 资产导出对话框（双列非模态版）

变化:
  - 双列布局 1:1：左侧命名+标签，右侧导出格式
  - 非模态模式：发射 exportConfigReady 信号替代 exec() 阻塞返回
  - 所有参数可空，无条件弹窗
"""

import math
import sys as _sys
from ..utils.maya_utils import get_qt_modules
from ..utils.settings import SettingsManager, apply_font_size_to_widget

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


# ── FlowLayout + FlowWidget（标签容器） ──────────────

class _FlowLayout(QtWidgets.QLayout):
    """自动换行布局"""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None
    def takeAt(self, index):
        if 0 <= index < len(self._items): return self._items.pop(index)
        return None
    def expandingDirections(self): return QtCore.Qt.Orientation(0)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self._do_layout(QtCore.QRect(0, 0, width, 0), True)
    def setGeometry(self, rect): super().setGeometry(rect); self._do_layout(rect, False)
    def sizeHint(self): return self.minimumSize()
    def minimumSize(self):
        s = QtCore.QSize()
        for item in self._items: s = s.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for item in self._items:
            sx, sy = self.spacing(), self.spacing()
            nx = x + item.sizeHint().width() + sx
            if nx - sx > r.right() and line_h > 0:
                x, y, line_h = r.x(), y + line_h + sy, 0
                nx = x + item.sizeHint().width() + sx
            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), item.sizeHint()))
            x, line_h = nx, max(line_h, item.sizeHint().height())
        return y + line_h - rect.y() + m.bottom()


class _FlowWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = _FlowLayout(self, margin=0, spacing=4)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self.flow.heightForWidth(w)
    def clear(self):
        while self.flow.count():
            it = self.flow.takeAt(0)
            if it.widget(): it.widget().deleteLater()


# ── 滚动区域辅助 ─────────────────────────────────────

class _ScrollArea(QtWidgets.QScrollArea):
    """暗色主题滚动区域"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #222; width: 8px; }"
            "QScrollBar::handle:vertical { background: #555; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )


# ═════════════════════════════════════════════════════
# AssetCreateDialog V3
# ═════════════════════════════════════════════════════

class AssetCreateDialog(QtWidgets.QDialog):
    """资产导出对话框 V3 — 双列非模态版

    参数:
        parent, material_name, category_display, common_tags,
        asset_type, associated_objects, material_count, plugin_statuses
        全部可空，无条件弹窗。

    信号:
        exportConfigReady(ExportConfig) — 用户点击「导出资产」后发射
    """

    exportConfigReady = QtCore.Signal(object)  # ExportConfig

    # 几何体格式定义: (字段名, 显示名, 扩展名)
    GEOMETRY_FORMATS = [
        ("ma",  "Maya ASCII",  ".ma"),
        ("mb",  "Maya Binary", ".mb"),
        ("fbx", "FBX",         ".fbx"),
        ("obj", "OBJ",         ".obj"),
        ("usd", "USD",         ".usd"),
    ]

    def __init__(
        self,
        parent=None,
        material_name="",
        category_display="",
        common_tags=None,
        asset_type="materials",
        associated_objects=None,
        material_count=1,
        plugin_statuses=None,
        material_name_cn="",
        material_tags=None,
        old_formats=None,
    ):
        super(AssetCreateDialog, self).__init__(parent)
        self._material_name = material_name
        self._name = material_name
        self._name_cn = material_name_cn
        self._tags = list(material_tags or [])
        self._common_tags = list(common_tags or [])
        self._asset_type = asset_type
        self._associated_objects = list(associated_objects or [])
        self._material_count = max(1, material_count)
        self._plugin_statuses = plugin_statuses or {}
        self._export_mode = "single"  # "single" | "batch_auto" | "batch_semi"
        self._export_sicon = True  # 始终 True，仅 UI 显示
        self._export_material_only = False
        self._category_display = category_display
        self._old_formats = set(f.lower() for f in (old_formats or []))

        # 格式勾选状态
        self._checkboxes = {}  # field_name → QCheckBox

        # 导出配置默认值（从预设文件加载）
        self._load_defaults()

        # 更新资产：按原有格式恢复勾选（覆盖预设默认值）
        if self._old_formats:
            for fmt_key in ("zmetal", "ma", "mb", "fbx", "obj", "usd", "abc"):
                field = f"_export_{fmt_key}"
                if hasattr(self, field):
                    setattr(self, field, fmt_key in self._old_formats)
            if "arnold" in self._old_formats:
                self._export_arnold = True
            if "vray" in self._old_formats:
                self._export_vray = True
            if "redshift" in self._old_formats:
                self._export_redshift = True
            # 其他格式（dae/dxf/igs/stl/wrl）在 UI 中默认不勾选，由用户手动展开

        self.setWindowTitle("导出资产")
        self.setMinimumSize(780, 760)
        self.setStyleSheet("background-color: #2a2a2a;")
        # 独立窗口（在 Maya 层级内，但不受插件窗口最小化影响）
        self.setWindowFlags(QtCore.Qt.Window
                            | QtCore.Qt.WindowMinimizeButtonHint
                            | QtCore.Qt.WindowMaximizeButtonHint
                            | QtCore.Qt.WindowCloseButtonHint)
        # 显式绑定 X 按钮
        self.reject = lambda: self.close()

        self._setup_ui()
        self.resize(780, 760)
        self._refresh_tags_display()
        self._refresh_plugin_indicators()
        
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

        self._e_name.setFocus()

    # ── 配置默认值 ────────────────────────────────────

    def _load_defaults(self):
        """从预设文件加载导出格式默认值（按 asset_type）"""
        preset_path = self._find_preset_path()
        preset = self._load_preset_json(preset_path)
        entry = preset.get(self._asset_type, preset.get("materials", {})) if preset else {}
        if not preset:
            print(f"[ExportPreset] 未加载到预设 (path={preset_path}), 使用内置默认值 asset_type={self._asset_type}")
        elif self._asset_type not in preset:
            print(f"[ExportPreset] 预设中未找到 asset_type={self._asset_type}, 回退到 materials")

        self._export_zmetal = entry.get("zmetal", True)
        self._export_ma = entry.get("ma", False)
        self._export_mb = entry.get("mb", False)
        self._export_fbx = entry.get("fbx", False)
        self._export_obj = entry.get("obj", False)
        self._export_usd = entry.get("usd", False)
        self._export_glb = entry.get("glb", False)
        self._export_abc = entry.get("abc", False)
        self._export_arnold = entry.get("arnold", False)
        self._export_vray = entry.get("vray", False)
        self._export_redshift = entry.get("redshift", False)
        self._export_vrmesh = entry.get("vrmesh", False)

        self._preset_delay_ms = entry.get("delay_ms", 2000)
        self._preset_thumb_source = entry.get("thumb_source", "screenshot")
        self._preset_ani_frame_mode = entry.get("ani_frame_mode", "current")
        self._preset_zmetal_merge = entry.get("zmetal_merge", True)
        self._preset_mcm_min_count = entry.get("mcm_min_count", 2)

        # mcm 默认勾选由预设的 "mcm" 字段控制，_material_count 运行时确定
        self._preset_mcm_enabled = entry.get("mcm", True)

    @staticmethod
    def _find_preset_path():
        import os
        # __file__ 在 Maya 中可能为相对路径或空，用多种方式尝试定位
        candidates = []
        try:
            mod_file = __file__
        except NameError:
            mod_file = ""
        if mod_file:
            base = os.path.dirname(os.path.dirname(os.path.abspath(mod_file)))
            candidates.append(os.path.join(base, "Assets", "preset", "export_preset.json"))
        # 回退：从 sys.path 查找
        import squirrel_asset_manager as _pkg
        if hasattr(_pkg, '__path__') and _pkg.__path__:
            pkg_dir = _pkg.__path__[0]
            candidates.append(os.path.join(pkg_dir, "Assets", "preset", "export_preset.json"))
        for p in candidates:
            if os.path.isfile(p):
                print(f"[ExportPreset] 已加载: {p}")
                return p
        print(f"[ExportPreset] 预设文件未找到, 尝试了: {candidates}")
        return candidates[0] if candidates else ""

    @staticmethod
    def _load_preset_json(path):
        import json, os
        if not os.path.isfile(path):
            print(f"[ExportPreset] 预设文件不存在: {path}")
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ExportPreset] 加载失败: {e}")
            return None

    # ── UI 构建（双列） ───────────────────────────────

    def _setup_ui(self):
        # 外层：滚动区域
        scroll = _ScrollArea(self)
        self.setLayout(QtWidgets.QVBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(scroll)

        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        outer_layout = QtWidgets.QVBoxLayout(content)
        outer_layout.setContentsMargins(16, 12, 16, 12)
        outer_layout.setSpacing(8)

        # ══════ 左右两栏 ══════
        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(16)

        # ── 左栏：命名 + 标签 ──
        left_widget = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)

        # 导出按钮 + 帮助按钮放在左栏最上方，同一行
        btn_row = QtWidgets.QWidget()
        btn_layout = QtWidgets.QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        confirm_btn = QtWidgets.QPushButton("导出资产")
        confirm_btn.setStyleSheet(
            "QPushButton { background-color: #5294e2; color: #ffffff; border: none; "
            "padding: 9px 0; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #6ab0ff; }"
        )
        confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(confirm_btn, 1)  # 占满大部分宽度

        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedWidth(32)
        help_btn.setMinimumHeight(32)
        help_btn.setToolTip("导出帮助")
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none; "
            "font-size: 16px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_export_help)
        btn_layout.addWidget(help_btn)
        btn_layout.addStretch()
        left.addWidget(btn_row)

        # 导出模式（三选一）
        mode_section = QtWidgets.QLabel("导出模式")
        mode_section.setStyleSheet("color: #c0c0c0; font-size: 13px; font-weight: bold;")
        left.addWidget(mode_section)

        mode_style = (
            "QRadioButton { color: #c0c0c0; font-size: 12px; spacing: 6px; }"
            "QRadioButton::indicator { width: 16px; height: 16px; border-radius: 8px; "
            "background: #333; border: 1px solid #555; }"
            "QRadioButton::indicator:checked { background: #5294e2; border-color: #5294e2; }"
        )

        self._mode_group = QtWidgets.QButtonGroup(self)
        self._rb_auto = QtWidgets.QRadioButton("全自动（各物体独立资产，自动截图）")
        self._rb_auto.setStyleSheet(mode_style)
        self._rb_semi = QtWidgets.QRadioButton("半自动（各物体独立资产，手动截图）")
        self._rb_semi.setStyleSheet(mode_style)
        self._rb_single = QtWidgets.QRadioButton("单资产（合并为一个资产，手动截图）")
        self._rb_single.setStyleSheet(mode_style)
        self._rb_single.setChecked(True)

        self._mode_group.addButton(self._rb_auto, 1)
        self._mode_group.addButton(self._rb_semi, 2)
        self._mode_group.addButton(self._rb_single, 3)
        self._mode_group.idToggled.connect(self._on_mode_changed)

        left.addWidget(self._rb_auto)
        left.addWidget(self._rb_semi)
        left.addWidget(self._rb_single)

        # 截图延迟（仅全自动可用）
        delay_row = QtWidgets.QHBoxLayout()
        delay_label = QtWidgets.QLabel("截图延迟:")
        delay_label.setStyleSheet("color: #808080; font-size: 12px;")
        delay_row.addWidget(delay_label)
        self._delay_spin = QtWidgets.QSpinBox()
        self._delay_spin.setRange(0, 60)
        self._delay_spin.setValue(self._preset_delay_ms // 1000)
        self._delay_spin.setSuffix(" 秒")
        self._delay_spin.setStyleSheet(
            "QSpinBox { background: #333; color: #d0d0d0; border: 1px solid #555; "
            "border-radius: 3px; padding: 2px 6px; font-size: 12px; min-width: 80px; }"
            "QSpinBox::up-button, QSpinBox::down-button { "
            "background: #444; border: none; width: 16px; }")
        delay_row.addWidget(self._delay_spin)
        delay_hint = QtWidgets.QLabel("（0=无延迟）")
        delay_hint.setStyleSheet("color: #606060; font-size: 11px;")
        delay_row.addWidget(delay_hint)
        delay_row.addStretch()
        self._delay_widget = QtWidgets.QWidget()
        self._delay_widget.setLayout(delay_row)
        self._delay_widget.setVisible(False)  # 默认隐藏，全自动模式时显示
        left.addWidget(self._delay_widget)

        # 信息行
        asset_type_names = {
            "materials": "材质", "models": "模型", "lights": "灯光",
            "textures": "贴图", "scenes": "场景", "hdr": "HDR",
        }
        at_display = asset_type_names.get(self._asset_type, self._asset_type)
        obj_count = len(self._associated_objects)

        info_lines = [f"目标分类: {self._category_display or '未选择'}    资产类型: {at_display}"]
        if self._material_name:
            info_lines.append(f"源节点: {self._material_name}")
        if obj_count > 0:
            info_lines.append(f"关联物体: {obj_count} 个")
        if self._material_count > 1:
            info_lines.append(f"检测到 {self._material_count} 个材质（将附带 .mcm）")

        info = QtWidgets.QLabel("\n".join(info_lines))
        info.setStyleSheet("color: #909090; font-size: 11px;")
        info.setWordWrap(True)
        left.addWidget(info)

        left.addWidget(self._make_sep())

        # 命名信息
        left.addWidget(self._make_section("命名信息"))
        left.addWidget(self._make_label("名称（英文，Maya 节点用）"))
        self._e_name = QtWidgets.QLineEdit(self._material_name)
        self._e_name.setStyleSheet(self._input_style())
        left.addWidget(self._e_name)

        left.addWidget(self._make_label("易读名（中文显示，可空）"))
        self._e_name_cn = QtWidgets.QLineEdit(self._name_cn)
        self._e_name_cn.setPlaceholderText("留空则使用英文名")
        self._e_name_cn.setStyleSheet(self._input_style())
        left.addWidget(self._e_name_cn)

        # 缩略图来源
        left.addWidget(self._make_label("缩略图来源"))
        thumb_src_grp = QtWidgets.QButtonGroup(self)
        self._thumb_screenshot = QtWidgets.QRadioButton("截屏工具")
        self._thumb_playblast = QtWidgets.QRadioButton("Maya 拍屏")
        self._thumb_render = QtWidgets.QRadioButton("渲染图")
        for rb in (self._thumb_screenshot, self._thumb_playblast, self._thumb_render):
            rb.setStyleSheet("color: #cccccc; font-size: 12px; spacing: 4px;")
            thumb_src_grp.addButton(rb)
        self._thumb_screenshot.setChecked(self._preset_thumb_source == "screenshot")
        self._thumb_playblast.setChecked(self._preset_thumb_source == "playblast")
        self._thumb_render.setChecked(self._preset_thumb_source == "render")
        thumb_src_row = QtWidgets.QHBoxLayout()
        thumb_src_row.addWidget(self._thumb_screenshot)
        thumb_src_row.addWidget(self._thumb_playblast)
        thumb_src_row.addWidget(self._thumb_render)
        thumb_src_row.addStretch()

        create_light_btn = QtWidgets.QPushButton("创建灯光")
        create_light_btn.setFixedHeight(24)
        create_light_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none; "
            "padding: 2px 10px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        create_light_btn.clicked.connect(self._on_create_dome_light)
        thumb_src_row.addWidget(create_light_btn)

        left.addLayout(thumb_src_row)

        # 标签
        left.addWidget(self._make_label("已选标签"))
        self._tags_flow = _FlowWidget()
        self._tags_flow.setMinimumHeight(28)
        left.addWidget(self._tags_flow)

        left.addWidget(self._make_label("常用标签"))
        self._common_flow = _FlowWidget()
        self._common_flow.setMinimumHeight(28)
        left.addWidget(self._common_flow)

        tag_row = QtWidgets.QHBoxLayout()
        tag_row.setSpacing(6)
        self._custom_tag = QtWidgets.QLineEdit()
        self._custom_tag.setPlaceholderText("输入自定义标签...")
        self._custom_tag.setStyleSheet(self._input_style())
        self._custom_tag.returnPressed.connect(self._add_custom_tag)
        tag_row.addWidget(self._custom_tag)

        add_btn = QtWidgets.QPushButton("+ 添加")
        add_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #5294e2; border: none; "
            "padding: 6px 14px; font-size: 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        add_btn.clicked.connect(self._add_custom_tag)
        tag_row.addWidget(add_btn)
        left.addLayout(tag_row)

        left.addStretch()

        # ── 右栏：导出格式 ──
        right_widget = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)

        right.addWidget(self._make_section("导出格式"))

        # ▸ 收集关联文件（置顶）
        right.addWidget(self._make_subsection("收集关联文件"))

        self._cb_collect_associated = QtWidgets.QCheckBox(
            "收集场景中已挂载的缓存/代理/引用文件")
        self._cb_collect_associated.setStyleSheet(
            self._checkbox_style() + "QCheckBox { color: #a0a0a0; }")
        self._checkboxes["collect_associated"] = self._cb_collect_associated
        row_ca = QtWidgets.QHBoxLayout()
        row_ca.setContentsMargins(8, 2, 0, 2)
        row_ca.addWidget(self._cb_collect_associated)
        row_ca.addStretch()
        right.addLayout(row_ca)

        # 贴图：可选复选框，默认勾选
        tex_row = QtWidgets.QHBoxLayout()
        tex_row.setContentsMargins(8, 2, 0, 2)
        cb_tex = QtWidgets.QCheckBox("\u2611 \u8d34\u56fe textures/")
        cb_tex.setChecked(True)
        cb_tex.setStyleSheet(self._checkbox_style())
        self._checkboxes["textures"] = cb_tex
        tex_row.addWidget(cb_tex)
        tex_row.addStretch()
        right.addLayout(tex_row)

        right.addWidget(self._make_sep())

        # ▸ 仅材质模式（放在核心之后、材质格式之前）
        self._cb_material_only = QtWidgets.QCheckBox("仅导出材质（跳过几何体/代理）")
        self._cb_material_only.setStyleSheet(self._checkbox_style())
        self._cb_material_only.toggled.connect(self._on_material_only_toggled)
        self._checkboxes["material_only"] = self._cb_material_only
        row_mo = QtWidgets.QHBoxLayout()
        row_mo.setContentsMargins(8, 2, 0, 2)
        row_mo.addWidget(self._cb_material_only)
        row_mo.addStretch()
        w_mo = QtWidgets.QWidget()
        w_mo.setLayout(row_mo)
        right.addWidget(w_mo)

        right.addWidget(self._make_sep())

        # ▸ 节点格式（材质/灯光/其他）
        is_material_asset = (self._asset_type == "materials")
        self._is_material_asset = is_material_asset
        self._subsection_nodes = self._make_subsection("材质格式" if is_material_asset else "节点格式")
        right.addWidget(self._subsection_nodes)
        self._cb_zmetal = self._add_checkbox_row(right, "节点预设 .zmetal" if not is_material_asset else "材质预设 .zmetal", self._export_zmetal)
        self._checkboxes["zmetal"] = self._cb_zmetal
        self._cb_zmetal.toggled.connect(self._on_zmetal_toggled)

        # .mcm 缩进行（非材质类型默认不勾选）
        self._mcm_row = QtWidgets.QWidget()
        mcm_layout = QtWidgets.QHBoxLayout(self._mcm_row)
        mcm_layout.setContentsMargins(24, 0, 0, 0)
        self._cb_mcm = QtWidgets.QCheckBox(
            f"材质映射 .mcm    (检测到 {self._material_count} 个材质)"
        )
        self._cb_mcm.setStyleSheet(self._checkbox_style())
        self._cb_mcm.setChecked(self._preset_mcm_enabled)
        mcm_layout.addWidget(self._cb_mcm)
        mcm_layout.addStretch()
        right.addWidget(self._mcm_row)
        self._mcm_row.setVisible(self._cb_zmetal.isChecked())

        # 合并 ZMETAL 缩进行
        self._merge_zmetal_row = QtWidgets.QWidget()
        mz_layout = QtWidgets.QHBoxLayout(self._merge_zmetal_row)
        mz_layout.setContentsMargins(24, 0, 0, 0)
        self._cb_merge_zmetal = QtWidgets.QCheckBox("全部材质写入一个 .zmetal" if is_material_asset else "全部节点写入一个 .zmetal")
        self._cb_merge_zmetal.setStyleSheet(self._checkbox_style())
        self._cb_merge_zmetal.setChecked(self._preset_zmetal_merge)
        mz_layout.addWidget(self._cb_merge_zmetal)
        mz_layout.addStretch()
        right.addWidget(self._merge_zmetal_row)
        self._merge_zmetal_row.setVisible(self._cb_zmetal.isChecked())

        # ▸ 几何体格式
        right.addWidget(self._make_subsection("几何体格式（通过 Maya 原生导出）"))
        for field, display, ext in self.GEOMETRY_FORMATS:
            checked = getattr(self, f"_export_{field}", False)
            cb = self._add_format_checkbox_row(right, f"{display} {ext}", field, checked)
            self._checkboxes[field] = cb

        # ▸ 动画导出（对 abc/usd/ass/rs/vrmesh 生效）
        right.addWidget(self._make_subsection("动画导出"))
        ani_row = QtWidgets.QWidget()
        ani_layout = QtWidgets.QHBoxLayout(ani_row)
        ani_layout.setContentsMargins(8, 2, 0, 2)
        ani_layout.setSpacing(10)
        self._rb_ani_current = QtWidgets.QRadioButton("当前帧")
        self._rb_ani_timeline = QtWidgets.QRadioButton("时间轴")
        self._rb_ani_keyframe = QtWidgets.QRadioButton("关键帧")
        self._rb_ani_current.setChecked(self._preset_ani_frame_mode == "current")
        self._rb_ani_timeline.setChecked(self._preset_ani_frame_mode == "timeline")
        self._rb_ani_keyframe.setChecked(self._preset_ani_frame_mode == "keyframe")
        for rb in (self._rb_ani_current, self._rb_ani_timeline, self._rb_ani_keyframe):
            rb.setStyleSheet("color: #b0b0b0; font-size: 12px;")
        ani_layout.addWidget(self._rb_ani_current)
        ani_layout.addWidget(self._rb_ani_timeline)
        ani_layout.addWidget(self._rb_ani_keyframe)
        ani_layout.addStretch(1)
        help_lbl = QtWidgets.QLabel("(abc/usd/ass/rs/vrmesh)")
        help_lbl.setStyleSheet("color: #666; font-size: 11px;")
        ani_layout.addWidget(help_lbl)
        right.addWidget(ani_row)

        # ▸ 缓存格式
        right.addWidget(self._make_subsection("缓存格式"))
        self._cb_abc = self._add_format_checkbox_row(
            right, "Alembic .abc", "abc", getattr(self, "_export_abc", False)
        )
        self._checkboxes["abc"] = self._cb_abc

        # ▸ 代理格式

        # ▸ 代理格式
        right.addWidget(self._make_subsection("代理格式"))
        self._cb_arnold = self._add_format_checkbox_row(
            right, "Arnold .ass", "arnold", self._export_arnold
        )
        self._checkboxes["arnold"] = self._cb_arnold

        self._cb_redshift = self._add_format_checkbox_row(
            right, "Redshift .rs", "redshift", self._export_redshift
        )
        self._checkboxes["redshift"] = self._cb_redshift

        self._cb_vray = self._add_format_checkbox_row(
            right, "V-Ray .vrscene", "vray", self._export_vray
        )
        self._checkboxes["vray"] = self._cb_vray

        self._cb_vrmesh = self._add_format_checkbox_row(
            right, "V-Ray .vrmesh", "vrmesh", self._export_vrmesh
        )
        self._checkboxes["vrmesh"] = self._cb_vrmesh

        right.addStretch()

        # ── 组装到左右栏 ──
        columns.addWidget(left_widget, 1)
        columns.addWidget(right_widget, 1)
        outer_layout.addLayout(columns)

    # ── 样式辅助 ───────────────────────────────────────

    @staticmethod
    def _make_label(text):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color: #808080; font-size: 11px;")
        return lbl

    @staticmethod
    def _make_section(text):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color: #c0c0c0; font-size: 13px; font-weight: bold;")
        return lbl

    @staticmethod
    def _make_subsection(text):
        lbl = QtWidgets.QLabel(f"▸ {text}")
        lbl.setStyleSheet("color: #888; font-size: 11px; margin-top: 4px;")
        return lbl

    @staticmethod
    def _make_sep():
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3a3a3a;")
        return sep

    @staticmethod
    def _input_style():
        return (
            "background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:6px 8px; color:#e0e0e0; font-size:13px;"
        )

    @staticmethod
    def _checkbox_style():
        return (
            "QCheckBox { color: #c0c0c0; font-size: 12px; spacing: 8px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px; "
            "background: #333; border: 1px solid #555; }"
            "QCheckBox::indicator:checked { background: #5294e2; border-color: #5294e2; }"
            "QCheckBox::indicator:disabled { background: #222; border-color: #333; }"
        )

    def _add_checkbox_row(self, layout, text, checked):
        """添加一个简单的 checkbox 行"""
        cb = QtWidgets.QCheckBox(text)
        cb.setChecked(checked)
        cb.setStyleSheet(self._checkbox_style())
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(8, 2, 0, 2)
        row.addWidget(cb)
        row.addStretch()
        w = QtWidgets.QWidget()
        w.setLayout(row)
        layout.addWidget(w)
        return cb

    def _add_format_checkbox_row(self, layout, text, fmt_key, checked):
        """添加带插件状态指示器的格式 checkbox"""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(8, 2, 0, 2)

        cb = QtWidgets.QCheckBox(text)
        cb.setChecked(checked)
        cb.setStyleSheet(self._checkbox_style())
        row.addWidget(cb)

        # 插件状态指示器
        self._status_labels = getattr(self, '_status_labels', {})
        status_lbl = QtWidgets.QLabel("")
        status_lbl.setStyleSheet("color: #666; font-size: 10px;")
        row.addWidget(status_lbl)
        self._status_labels[fmt_key] = (status_lbl, cb)
        row.addStretch()

        w = QtWidgets.QWidget()
        w.setLayout(row)
        layout.addWidget(w)
        return cb

    # ── 插件状态指示器 ──────────────────────────────────

    def _refresh_plugin_indicators(self):
        """根据 Maya 插件实际状态更新 UI 指示器"""
        if not self._plugin_statuses:
            return

        status_labels = getattr(self, '_status_labels', {})

        for fmt_key, (label, checkbox) in status_labels.items():
            plugin_key = None
            if fmt_key == "arnold":
                plugin_key = "mtoa"
            elif fmt_key == "vray":
                plugin_key = "vrayformaya"
            elif fmt_key == "ma" or fmt_key == "mb":
                plugin_key = None  # Maya 内置
            else:
                plugin_key = fmt_key  # fbx, obj, usd, glb

            if plugin_key is None:
                label.setText("🟢 已就绪")
                label.setStyleSheet("color: #4caf50; font-size: 10px;")
                continue

            # 从 plugin_statuses 读取（key 是插件名）
            status = self._plugin_statuses.get(plugin_key)
            if status is None:
                label.setText("🟢 已就绪")
                label.setStyleSheet("color: #4caf50; font-size: 10px;")
            elif hasattr(status, 'value'):
                if status.value == "loaded":
                    label.setText("🟢 已就绪")
                    label.setStyleSheet("color: #4caf50; font-size: 10px;")
                elif status.value == "not_loaded":
                    label.setText("🟡 未加载")
                    label.setStyleSheet("color: #ff9800; font-size: 10px;")
                else:
                    label.setText("🔴 不可用")
                    label.setStyleSheet("color: #f44336; font-size: 10px;")
                    checkbox.setEnabled(False)
                    checkbox.setToolTip("需要安装对应渲染器插件")
            else:
                label.setText("🟢 已就绪")
                label.setStyleSheet("color: #4caf50; font-size: 10px;")

    # ── 导出模式切换 ────────────────────────────────────

    def _on_mode_changed(self, btn_id, checked):
        """导出模式切换时更新 placeholder + 延迟可见性"""
        if not checked:
            return
        if btn_id in (1, 2):  # 全自动 / 半自动
            self._export_mode = "batch_auto" if btn_id == 1 else "batch_semi"
            self._e_name.setPlaceholderText("首个资产手动命名 → 后续自动使用节点名")
            self._e_name_cn.setPlaceholderText("后续资产留空")
        else:  # 单资产
            self._export_mode = "single"
            self._e_name.setPlaceholderText("")
            self._e_name_cn.setPlaceholderText("留空则使用英文名")
        # 延迟输入仅全自动可见
        self._delay_widget.setVisible(btn_id == 1)

    # ── .mcm 联动 ──────────────────────────────────────

    def _on_zmetal_toggled(self, checked):
        """.zmetal 切换时更新子选项可见性"""
        self._merge_zmetal_row.setVisible(checked)
        self._mcm_row.setVisible(checked)

    # ── 仅材质模式联动 ──────────────────────────────────

    def _on_material_only_toggled(self, checked):
        """仅材质模式——记录状态即可，与几何体/代理格式勾选正交。"""
        pass  # 状态已通过 _build_export_config 中的 export_material_only 字段传递

    # ── 标签逻辑 ───────────────────────────────────────

    def _populate_common_tags(self):
        self._common_flow.clear()
        for tag in self._common_tags:
            if tag in self._tags:
                continue
            b = QtWidgets.QPushButton(tag)
            b.setStyleSheet(
                "QPushButton { background:#333; color:#888; border:1px solid #444; "
                "border-radius:8px; padding:2px 8px; font-size:11px; }"
                "QPushButton:hover { background:#2d4a6f; color:#5294e2; border-color:#5294e2; }"
            )
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, t=tag: self._toggle_tag(t))
            self._common_flow.flow.addWidget(b)

    def _refresh_tags_display(self):
        self._tags_flow.clear()
        for tag in self._tags:
            b = QtWidgets.QPushButton(f"✖ {tag}")
            b.setStyleSheet(
                "QPushButton { background:#2a3a4a; color:#5294e2; border:1px solid #3a5a7a; "
                "border-radius:10px; padding:2px 8px; font-size:11px; }"
                "QPushButton:hover { background:#3a1a1a; color:#e06060; }"
            )
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, t=tag: self._remove_tag(t))
            self._tags_flow.flow.addWidget(b)
        self._populate_common_tags()

    def _toggle_tag(self, tag):
        if tag in self._tags:
            self._tags.remove(tag)
        else:
            self._tags.append(tag)
        self._refresh_tags_display()

    def _remove_tag(self, tag):
        if tag in self._tags:
            self._tags.remove(tag)
        self._refresh_tags_display()

    def _add_custom_tag(self):
        text = self._custom_tag.text().strip()
        if text and text not in self._tags:
            self._tags.append(text)
            self._custom_tag.clear()
            self._refresh_tags_display()

    # ── 确认 / 取消 ────────────────────────────────────

    def _on_cancel(self):
        """取消导出 — 关闭对话框"""
        self.close()

    def _on_export_help(self):
        """打开导出资产帮助"""
        import webbrowser
        import os
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = os.path.join(plugin_root, "Assets", "help", "help_export.html")
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[Help] 导出帮助文件未找到:", help_path)

    def _on_confirm(self):
        """用户点击「导出资产」— 发射信号，不关闭窗口"""
        self._name = self._e_name.text().strip() or self._material_name
        self._name_cn = self._e_name_cn.text().strip()

        config = self._build_export_config()
        self.exportConfigReady.emit(config)

    def _get_thumb_source(self) -> str:
        if self._thumb_playblast.isChecked():
            return "playblast"
        if self._thumb_render.isChecked():
            return "render"
        return "screenshot"

    def _on_create_dome_light(self):
        try:
            import maya.cmds as cmds
            previous_selection = cmds.ls(selection=True) or []
        except Exception:
            previous_selection = []
        
        from squirrel_asset_manager.core.export_orchestrator import ExportOrchestrator
        result = ExportOrchestrator.create_dome_light()
        
        if previous_selection:
            try:
                import maya.cmds as cmds
                cmds.select(previous_selection, replace=True)
            except Exception:
                pass
        
        if result:
            print(f"[Light] 已创建 dome 灯: {result}")
        else:
            try:
                import maya.cmds as cmds
                renderer = cmds.getAttr('defaultRenderGlobals.currentRenderer') if cmds.objExists('defaultRenderGlobals') else ''
                renderer = renderer.lower()
            except Exception:
                renderer = ''
            if renderer not in ('vray', 'arnold', 'redshift'):
                print(f"[Light] 当前渲染器 '{renderer}' 不支持自动创建 dome 灯，请切换到 Arnold / V-Ray / Redshift 后重试")
            else:
                print("[Light] 创建 dome 灯失败，请查看上方日志")

    def _build_export_config(self):
        """构建 ExportConfig（与 get_export_config 一致）"""
        from squirrel_asset_manager.core.export_orchestrator import ExportConfig

        proxy_list = []
        if self._checkboxes.get("arnold") and self._checkboxes["arnold"].isChecked():
            proxy_list.append("arnold")
        if self._checkboxes.get("vray") and self._checkboxes["vray"].isChecked():
            proxy_list.append("vray")
        if self._checkboxes.get("vrmesh") and self._checkboxes["vrmesh"].isChecked():
            proxy_list.append("vrmesh")
        if self._checkboxes.get("redshift") and self._checkboxes["redshift"].isChecked():
            proxy_list.append("redshift")

        config = ExportConfig(
            asset_name=self._name,
            name_cn=self._name_cn,
            category="",  # 由 main_window 注入
            tags=list(self._tags),
            asset_type=self._asset_type,
            export_zmetal=self._checkboxes.get("zmetal") and self._checkboxes["zmetal"].isChecked(),
            merge_zmetal=self._cb_merge_zmetal.isChecked() if hasattr(self, '_cb_merge_zmetal') else False,
            export_mcm=(
                self._checkboxes.get("zmetal") and self._checkboxes["zmetal"].isChecked()
                and hasattr(self, '_cb_mcm') and self._cb_mcm.isChecked()
            ),
            export_ma=self._checkboxes.get("ma") and self._checkboxes["ma"].isChecked(),
            export_mb=self._checkboxes.get("mb") and self._checkboxes["mb"].isChecked(),
            export_fbx=self._checkboxes.get("fbx") and self._checkboxes["fbx"].isChecked(),
            export_obj=self._checkboxes.get("obj") and self._checkboxes["obj"].isChecked(),
            export_usd=self._checkboxes.get("usd") and self._checkboxes["usd"].isChecked(),
            export_abc=self._checkboxes.get("abc") and self._checkboxes["abc"].isChecked(),
            collect_associated=self._checkboxes.get("collect_associated") and self._checkboxes["collect_associated"].isChecked(),
            ani_frame_mode="keyframe" if self._rb_ani_keyframe.isChecked() else ("timeline" if self._rb_ani_timeline.isChecked() else "current"),
            proxy_formats=proxy_list,
            export_material_only=self._cb_material_only.isChecked(),
            export_textures=self._checkboxes.get("textures") and self._checkboxes["textures"].isChecked(),
            export_mode=self._export_mode,
            delay_ms=self._delay_spin.value() * 1000,
            thumb_source=self._get_thumb_source(),
            target_dir="",  # 由 main_window 注入
            material_node=self._material_name,
            associated_objects=list(self._associated_objects),
        )
        return config

    # ── 返回值 API（兼容旧调用） ────────────────────────

    def result(self):
        """v1.x 兼容: 返回 (name, name_cn, tags)"""
        return self._name, self._name_cn, list(self._tags)

    def get_export_config(self):
        """v2.0: 构建并返回 ExportConfig"""
        return self._build_export_config()
