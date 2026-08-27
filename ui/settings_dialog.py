import os
import json
import uuid

from ..utils.maya_utils import get_qt_modules
from ..utils.json_handler import JSONHandler
from ..utils.settings import apply_font_size_to_widget
from .name_conflict_dialog import NameConflictDialog

try:
    from ..utils.i18n import t, help_path as _i18n_help_path
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key
    def _i18n_help_path(p):
        return p

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


# 配置文件路径
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "preset", "config.json"
)
_PBR_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Assets", "preset", "pbr_mapping.json"
)
_EXPORT_PRESET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Assets", "preset", "export_preset.json"
)
_CONTEXT_MENU_PRESET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Assets", "preset", "context_menu_preset.json"
)
_DOUBLE_CLICK_PRESET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Assets", "preset", "double_click_preset.json"
)


class SettingsDialog(QtWidgets.QDialog):
    settingsChanged = QtCore.Signal(dict)

    DEFAULT_SETTINGS = {
        "font_size": 13,
        "thumb_size": 180,
        "default_view": "icon",
        "last_library_path": "",
    }

    def __init__(self, parent=None, current_settings=None, material_manager=None):
        super(SettingsDialog, self).__init__(parent)
        self.setWindowTitle(t("dialog.settings.title"))
        self.setMinimumSize(960, 750)
        self.setStyleSheet("background-color: #2a2a2a;")

        self._settings = dict(current_settings) if current_settings else dict(self.DEFAULT_SETTINGS)
        self._material_manager = material_manager
        self._load_config()
        self._setup_ui()
        self._populate_library_list()
        
        font_size = self._settings.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

    def _load_config(self):
        """从 config.json 读取当前配置"""
        self._config = {}
        if os.path.isfile(_CONFIG_PATH):
            try:
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except Exception:
                pass

    def _save_config(self):
        """写回 config.json"""
        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Settings] 保存配置失败: {e}")

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(14)

        self._tab_widget = QtWidgets.QTabWidget()
        self._tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #3a3a3a; border-radius: 6px;
                background-color: #252525; padding: 16px;
            }
            QTabBar::tab {
                background-color: #2a2a2a; color: #909090; border: 1px solid #3a3a3a;
                border-bottom: none; padding: 8px 20px; font-size: 13px;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #252525; color: #5294e2; border-bottom: 2px solid #252525;
            }
            QTabBar::tab:hover { color: #d0d0d0; }
        """)

        self._tab_widget.addTab(self._create_general_tab(), t("tab.general"))
        self._tab_widget.addTab(self._create_export_defaults_tab(), t("tab.export_defaults"))
        self._tab_widget.addTab(self._create_context_menu_tab(), t("tab.context_menu"))
        self._tab_widget.addTab(self._create_formats_tab(), t("tab.supported_formats"))
        self._tab_widget.addTab(self._create_texture_suffixes_tab(), t("tab.texture_suffixes"))
        self._tab_widget.addTab(self._create_tags_tab(), t("tab.common_tags"))
        self._tab_widget.addTab(self._create_subs_tab(), t("tab.sub_libraries"))
        self._tab_widget.addTab(self._create_advanced_tab(), t("tab.advanced"))

        # 将标签页放入滚动区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self._tab_widget)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        layout.addWidget(scroll, 1)

        # 全局解锁复选框（默认锁定，常规标签页除外）
        self._unlock_cb = QtWidgets.QCheckBox(t("label.unlock_all"))
        self._unlock_cb.setStyleSheet("QCheckBox { color:#909090; font-size:11px; }")
        self._unlock_cb.stateChanged.connect(self._on_unlock_changed)
        layout.addWidget(self._unlock_cb)

        # 初始锁定非通用标签页
        self._set_locked(True)

        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(10)
        restore_btn = QtWidgets.QPushButton(t("common.restore_default"))
        restore_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #909090; border: 1px solid #4a4a4a; "
            "padding: 8px 16px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3a3a3a; color: #e0e0e0; }"
        )
        restore_btn.clicked.connect(self._on_restore_defaults)
        btn_layout.addWidget(restore_btn)

        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedWidth(32)
        help_btn.setMinimumHeight(32)
        help_btn.setToolTip(t("tooltip.settings_help"))
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none; "
            "font-size: 16px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_settings_help)
        btn_layout.addWidget(help_btn)

        btn_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #a0a0a0; border: none; "
            "padding: 9px 24px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; color: #e0e0e0; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        apply_btn = QtWidgets.QPushButton(t("common.apply"))
        apply_btn.setStyleSheet(
            "QPushButton { background-color: #2d6a4f; color: #e0e0e0; border: none; "
            "padding: 9px 24px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #40916c; }"
        )
        apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(apply_btn)

        ok_btn = QtWidgets.QPushButton(t("common.ok"))
        ok_btn.setStyleSheet(
            "QPushButton { background-color: #5294e2; color: #ffffff; border: none; "
            "padding: 9px 24px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #6ab0ff; }"
        )
        ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    # ── 常规标签页 ────────────────────────────────

    def _create_general_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        g1 = self._group(t("group.font_size"))
        g1l = QtWidgets.QVBoxLayout(g1)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(self._lb(t("label.global_font_size:")))
        self._font_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._font_slider.setRange(10, 24)
        self._font_slider.setValue(self._settings.get("font_size", 13))
        self._font_slider.setStyleSheet(_slider_style())
        fr.addWidget(self._font_slider, 1)
        self._font_val = QtWidgets.QLabel(str(self._settings.get("font_size", 13)))
        self._font_val.setStyleSheet("color:#d0d0d0;font-size:12px;min-width:20px;")
        self._font_slider.valueChanged.connect(lambda v: self._font_val.setText(str(v)))
        fr.addWidget(self._font_val)
        g1l.addLayout(fr)
        layout.addWidget(g1)

        g2 = self._group(t("group.default_view"))
        g2l = QtWidgets.QHBoxLayout(g2)
        self._view_icon = QtWidgets.QRadioButton(t("radio.view_icon"))
        self._view_list = QtWidgets.QRadioButton(t("radio.view_list"))
        dv = self._settings.get("default_view", "icon")
        self._view_icon.setChecked(dv == "icon")
        self._view_list.setChecked(dv == "list")
        for rb in [self._view_icon, self._view_list]:
            rb.setStyleSheet("QRadioButton { color: #d0d0d0; font-size: 13px; }")
        g2l.addWidget(self._view_icon)
        g2l.addWidget(self._view_list)
        g2l.addStretch()
        layout.addWidget(g2)

        # ── 界面语言 ──
        g_lang = self._group(t("group.language"))
        g_lang_l = QtWidgets.QHBoxLayout(g_lang)
        g_lang_l.addWidget(self._lb(t("label.language:")))
        self._lang_combo = QtWidgets.QComboBox()
        self._lang_combo.addItem("中文", "zh")
        self._lang_combo.addItem("English", "en")
        self._lang_combo.setStyleSheet(
            "QComboBox { background:#2a2a2a; color:#d0d0d0; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:4px 10px; font-size:13px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#2a2a2a; color:#d0d0d0; "
            "selection-background-color:#2a4a6a; }")
        current = self._settings.get("language", "zh")
        _idx = self._lang_combo.findData(current)
        if _idx >= 0:
            self._lang_combo.setCurrentIndex(_idx)
        g_lang_l.addWidget(self._lang_combo)
        g_lang_l.addStretch()
        layout.addWidget(g_lang)

        g3 = self._group(t("group.thumbnails"))
        g3l = QtWidgets.QVBoxLayout(g3)
        tr = QtWidgets.QHBoxLayout()
        tr.addWidget(self._lb(t("label.default_thumb_size:")))
        self._thumb_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._thumb_slider.setRange(100, 1024)
        self._thumb_slider.setValue(self._settings.get("thumb_size", 180))
        self._thumb_slider.setStyleSheet(_slider_style())
        tr.addWidget(self._thumb_slider, 1)
        self._thumb_val = QtWidgets.QLabel(f"{self._settings.get('thumb_size', 180)}px")
        self._thumb_val.setStyleSheet("color:#d0d0d0;font-size:12px;min-width:40px;")
        self._thumb_slider.valueChanged.connect(lambda v: self._thumb_val.setText(f"{v}px"))
        g3l.addLayout(tr)
        layout.addWidget(g3)

        g4 = self._group(t("group.library_management"))
        g4l = QtWidgets.QVBoxLayout(g4)

        # ── 库列表 ──
        self._lib_list = QtWidgets.QListWidget()
        self._lib_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #4a4a4a; border-radius:4px; "
            "color:#d0d0d0; font-size:13px; }"
            "QListWidget::item { padding:6px 10px; }"
            "QListWidget::item:selected { background:#2a4a6a; }")
        self._lib_list.setMinimumHeight(120)
        g4l.addWidget(self._lib_list)

        # ── 库操作按钮 ──
        btn_row = QtWidgets.QHBoxLayout()
        btn_style = ("QPushButton { background:#3a3a3a; color:#d0d0d0; border:none; "
                     "padding:5px 14px; font-size:12px; border-radius:4px; }"
                     "QPushButton:hover { background:#4a4a4a; }")

        add_btn = QtWidgets.QPushButton(t("btn.add_library"))
        add_btn.setStyleSheet(btn_style)
        add_btn.clicked.connect(self._on_add_library)
        btn_row.addWidget(add_btn)

        create_btn = QtWidgets.QPushButton(t("btn.create_library"))
        create_btn.setStyleSheet(btn_style)
        create_btn.clicked.connect(self._on_create_library)
        btn_row.addWidget(create_btn)

        rm_btn = QtWidgets.QPushButton(t("btn.remove_selected"))
        rm_btn.setStyleSheet(btn_style)
        rm_btn.clicked.connect(self._on_remove_library)
        btn_row.addWidget(rm_btn)

        set_default_btn = QtWidgets.QPushButton(t("btn.set_default"))
        set_default_btn.setStyleSheet(btn_style)
        set_default_btn.clicked.connect(self._on_set_default_library)
        btn_row.addWidget(set_default_btn)

        btn_row.addStretch()
        g4l.addLayout(btn_row)

        layout.addWidget(g4)

        # ── 同名冲突处理 ──
        g5 = self._group(t("group.name_conflict"))
        g5l = QtWidgets.QVBoxLayout(g5)
        g5l.setSpacing(6)

        policy = self._config.get("name_conflict_policy", {})
        current_mode = policy.get("mode", NameConflictDialog.MODE_PROMPT)

        self._conflict_prompt_rb = QtWidgets.QRadioButton(t("radio.conflict_prompt"))
        self._conflict_auto_rb = QtWidgets.QRadioButton(t("radio.conflict_auto"))
        self._conflict_manual_rb = QtWidgets.QRadioButton(t("radio.conflict_manual"))

        for rb in [self._conflict_prompt_rb, self._conflict_auto_rb, self._conflict_manual_rb]:
            rb.setStyleSheet("QRadioButton { color: #d0d0d0; font-size: 13px; }")

        self._conflict_prompt_rb.setChecked(current_mode == NameConflictDialog.MODE_PROMPT)
        self._conflict_auto_rb.setChecked(current_mode == NameConflictDialog.MODE_AUTO_RENAME)
        self._conflict_manual_rb.setChecked(current_mode == NameConflictDialog.MODE_MANUAL)

        g5l.addWidget(self._conflict_prompt_rb)
        g5l.addWidget(self._conflict_auto_rb)
        g5l.addWidget(self._conflict_manual_rb)
        layout.addWidget(g5)

        # ── 贴图导入策略 ──
        g6 = self._group(t("group.texture_import_policy"))
        g6l = QtWidgets.QVBoxLayout(g6)
        self._tex_policy_copy = QtWidgets.QRadioButton(t("radio.tex_policy_copy"))
        self._tex_policy_asset = QtWidgets.QRadioButton(t("radio.tex_policy_asset"))
        self._tex_policy_source = QtWidgets.QRadioButton(t("radio.tex_policy_source"))
        g6l.addWidget(self._tex_policy_copy)
        g6l.addWidget(self._tex_policy_asset)
        g6l.addWidget(self._tex_policy_source)
        for rb in [self._tex_policy_copy, self._tex_policy_asset, self._tex_policy_source]:
            rb.setStyleSheet("QRadioButton { color: #d0d0d0; font-size: 13px; }")
        tex_policy = self._config.get("texture_import_policy", "copy_to_project")
        self._tex_policy_copy.setChecked(tex_policy == "copy_to_project")
        self._tex_policy_asset.setChecked(tex_policy == "asset_directory")
        self._tex_policy_source.setChecked(tex_policy == "source_directory")
        layout.addWidget(g6)

        # ── 依赖文件导入策略 ──
        g7 = self._group(t("group.dep_import_policy"))
        g7l = QtWidgets.QVBoxLayout(g7)
        self._dep_policy_copy = QtWidgets.QRadioButton(
            t("radio.dep_policy_copy"))
        self._dep_policy_asset = QtWidgets.QRadioButton(
            t("radio.dep_policy_asset"))
        self._dep_policy_source = QtWidgets.QRadioButton(
            t("radio.dep_policy_source"))
        g7l.addWidget(self._dep_policy_copy)
        g7l.addWidget(self._dep_policy_asset)
        g7l.addWidget(self._dep_policy_source)
        for rb in [self._dep_policy_copy, self._dep_policy_asset, self._dep_policy_source]:
            rb.setStyleSheet("QRadioButton { color: #d0d0d0; font-size: 13px; }")
        dep_policy = self._config.get("dependency_import_policy", "copy_to_project")
        self._dep_policy_copy.setChecked(dep_policy == "copy_to_project")
        self._dep_policy_asset.setChecked(dep_policy == "asset_directory")
        self._dep_policy_source.setChecked(dep_policy == "source_directory")
        layout.addWidget(g7)

        layout.addStretch()
        return w

    # ── 导出默认值标签页 ──────────────────────────

    _EXPORT_ASSET_TYPES = ["materials", "models", "lights", "textures", "scenes", "hdr", "ani"]

    _EXPORT_FIELDS = [
        ("zmetal", t("export.label.zmetal"), "material"),
        ("zmetal_merge", t("export.label.zmetal_merge"), "material"),
        ("mcm", t("export.label.mcm"), "material"),
        ("ma", "Maya ASCII (.ma)", "geometry"),
        ("mb", "Maya Binary (.mb)", "geometry"),
        ("fbx", "FBX (.fbx)", "geometry"),
        ("obj", "OBJ (.obj)", "geometry"),
        ("usd", "USD (.usd)", "geometry"),
        ("abc", t("export.label.abc"), "cache"),
        ("arnold", t("export.label.arnold"), "proxy"),
        ("vray", t("export.label.vray"), "proxy"),
        ("redshift", t("export.label.redshift"), "proxy"),
        ("vrmesh", t("export.label.vrmesh"), "proxy"),
    ]

    def _create_export_defaults_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 左侧：资产类型列表
        self._export_type_list = QtWidgets.QListWidget()
        self._export_type_list.setFixedWidth(140)
        self._export_type_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        layout.addWidget(self._export_type_list)

        # 右侧：格式选项
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)

        # 材质格式
        mat_group = self._group(t("group.material_formats"))
        mat_layout = QtWidgets.QVBoxLayout(mat_group)
        self._export_cbs = {}
        for key, label, category in self._EXPORT_FIELDS:
            if category == "material":
                cb = QtWidgets.QCheckBox(label)
                cb.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
                mat_layout.addWidget(cb)
                self._export_cbs[key] = cb
        right.addWidget(mat_group)

        # 几何体格式
        geo_group = self._group(t("group.geometry_formats"))
        geo_layout = QtWidgets.QVBoxLayout(geo_group)
        for key, label, category in self._EXPORT_FIELDS:
            if category == "geometry":
                cb = QtWidgets.QCheckBox(label)
                cb.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
                geo_layout.addWidget(cb)
                self._export_cbs[key] = cb
        right.addWidget(geo_group)

        # 缓存与代理格式
        other_group = self._group(t("group.cache_proxy"))
        other_layout = QtWidgets.QVBoxLayout(other_group)
        for key, label, category in self._EXPORT_FIELDS:
            if category in ("cache", "proxy"):
                cb = QtWidgets.QCheckBox(label)
                cb.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
                other_layout.addWidget(cb)
                self._export_cbs[key] = cb
        right.addWidget(other_group)

        # 收集关联文件
        collect_group = self._group(t("group.collect_associated"))
        collect_layout = QtWidgets.QVBoxLayout(collect_group)
        cb_collect = QtWidgets.QCheckBox(t("checkbox.collect_associated"))
        cb_collect.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
        collect_layout.addWidget(cb_collect)
        self._export_cbs["collect_associated"] = cb_collect

        cb_textures = QtWidgets.QCheckBox(t("checkbox.collect_textures"))
        cb_textures.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
        collect_layout.addWidget(cb_textures)
        self._export_cbs["textures"] = cb_textures
        right.addWidget(collect_group)

        # 仅导出材质
        mat_only_group = self._group(t("group.export_options"))
        mat_only_layout = QtWidgets.QVBoxLayout(mat_only_group)
        cb_mat_only = QtWidgets.QCheckBox(t("checkbox.export_material_only"))
        cb_mat_only.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
        mat_only_layout.addWidget(cb_mat_only)
        self._export_cbs["material_only"] = cb_mat_only
        right.addWidget(mat_only_group)

        right.addStretch()
        layout.addLayout(right, 1)

        # 加载数据
        self._export_preset_data = self._load_export_preset()
        self._export_types = list(self._config.get("sub_libraries", {}).keys())
        if not self._export_types:
            self._export_types = ["materials", "models", "lights", "textures", "scenes", "hdr", "ani"]
        for atype in self._export_types:
            self._export_type_list.addItem(atype)

        self._export_type_list.currentRowChanged.connect(self._on_export_type_changed)
        if self._export_type_list.count() > 0:
            self._export_type_list.setCurrentRow(0)

        return w

    # ── 右键菜单标签页 ────────────────────────────

    _CONTEXT_MENU_ITEMS = [
        ("import", "ctx_menu.import"),
        ("import_geometry", "ctx_menu.import_geometry"),
        ("add_reference", "ctx_menu.add_reference"),
        ("favorites", "ctx_menu.favorites"),
        ("select_all", "ctx_menu.select_all"),
        ("duplicate", "ctx_menu.duplicate"),
        ("open_folder", "ctx_menu.open_folder"),
        ("move_to", "ctx_menu.move_to"),
        ("copy_to", "ctx_menu.copy_to"),
        ("edit", "ctx_menu.edit"),
        ("create_asset", "ctx_menu.create_asset"),
        ("update_thumbnail", "ctx_menu.update_thumbnail"),
        ("update_asset", "ctx_menu.update_asset"),
        ("delete", "ctx_menu.delete"),
        ("preview_node", "ctx_menu.preview_node"),
        ("ai_analysis", "ctx_menu.ai_analysis"),
        ("apply_material", "ctx_menu.apply_material"),
        ("apply_material_params", "ctx_menu.apply_material_params"),
        ("create_material", "ctx_menu.create_material"),
        ("import_texture", "ctx_menu.import_texture"),
        ("assign_texture", "ctx_menu.assign_texture"),
        ("apply_light", "ctx_menu.apply_light"),
        ("create_dome_light", "ctx_menu.create_dome_light"),
    ]

    # 双击命令 — 各子库可选命令列表（仅导入/创建资产相关）
    _DOUBLE_CLICK_ITEMS = {
        "materials": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
            ("apply_material", "ctx_menu.apply_material"),
        ],
        "models": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
        ],
        "lights": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
            ("apply_light", "ctx_menu.apply_light"),
        ],
        "textures": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
            ("import_texture", "ctx_menu.import_texture"),
            ("create_material", "ctx_menu.create_material"),
            ("apply_material", "ctx_menu.apply_material"),
            ("assign_texture", "ctx_menu.assign_texture"),
        ],
        "hdr": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
            ("create_dome_light", "ctx_menu.create_dome_light"),
            ("assign_texture", "ctx_menu.assign_texture_only"),
        ],
        "scenes": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
        ],
        "ani": [
            ("none", "ctx_menu.none"),
            ("import", "ctx_menu.import"),
        ],
    }
    # 自定义库通用双击命令
    _DOUBLE_CLICK_ITEMS_GENERIC = [
        ("none", "ctx_menu.none"),
        ("import", "ctx_menu.import"),
    ]

    # 双击命令的子选项定义 — 有二级菜单的命令映射其可选子项
    _DOUBLE_CLICK_CMD_OPTIONS = {
        "import": [
            "ma", "mb", "fbx", "obj", "usd", "abc",
            "ass", "vrscene", "rs", "vrmesh",
            "zmetal", "zlight", "proxy", "vdb",
        ],
        "import_texture": ["option.all"],
        # create_material 和 create_dome_light 在运行时从 config.json 和 HDR_ligt/ 动态读取
    }
    # import_texture 固定为 "导入全部"

    def _create_context_menu_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 左侧：子库类型列表
        self._ctx_type_list = QtWidgets.QListWidget()
        self._ctx_type_list.setFixedWidth(140)
        self._ctx_type_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        layout.addWidget(self._ctx_type_list)

        # 右侧：滚动区域（菜单项勾选 + 双击命令）
        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        right_container = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_container)
        right.setSpacing(8)

        # ── 右键菜单区域 ──
        ctx_tip = QtWidgets.QLabel(t("label.ctx_tip"))
        ctx_tip.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(ctx_tip)

        self._ctx_cbs = {}
        for cfg_key, i18n_key in self._CONTEXT_MENU_ITEMS:
            cb = QtWidgets.QCheckBox(t(i18n_key))
            cb.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
            right.addWidget(cb)
            self._ctx_cbs[cfg_key] = cb

        # ── 分隔线 ──
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        sep.setStyleSheet("color:#3a3a3a;")
        right.addWidget(sep)

        # ── 双击命令区域 ──
        dc_tip = QtWidgets.QLabel(t("label.dc_tip"))
        dc_tip.setStyleSheet("color:#e0a030;font-size:11px;font-weight:bold;")
        right.addWidget(dc_tip)

        self._dc_button_group = QtWidgets.QButtonGroup(self)
        self._dc_buttons = {}  # key → QRadioButton
        self._dc_buttons_layout = QtWidgets.QVBoxLayout()
        self._dc_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._dc_buttons_layout.setSpacing(6)
        right.addLayout(self._dc_buttons_layout)

        # ── 双击命令子选项（下拉框，默认隐藏）──
        self._dc_option_widget = QtWidgets.QWidget()
        opt_layout = QtWidgets.QHBoxLayout(self._dc_option_widget)
        opt_layout.setContentsMargins(0, 0, 0, 0)
        opt_label = QtWidgets.QLabel(t("label.sub_option:"))
        opt_label.setStyleSheet("color:#b0b0b0;font-size:12px;")
        self._dc_option_combo = QtWidgets.QComboBox()
        self._dc_option_combo.setStyleSheet(
            "QComboBox { background:#2a2a2a; color:#d0d0d0; border:1px solid #3a3a3a; "
            "border-radius:4px; padding:4px 8px; font-size:12px; min-width:180px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#2a2a2a; color:#d0d0d0; "
            "selection-background-color:#2d4a6f; }")
        opt_layout.addWidget(opt_label)
        opt_layout.addWidget(self._dc_option_combo, 1)
        self._dc_option_widget.setVisible(False)
        right.addWidget(self._dc_option_widget)

        right.addStretch()

        right_scroll.setWidget(right_container)
        layout.addWidget(right_scroll, 1)

        # 加载数据
        self._ctx_preset_data = self._load_context_menu_preset()
        self._dc_preset_data = self._load_double_click_preset()
        self._ctx_types = list(self._config.get("sub_libraries", {}).keys())
        if not self._ctx_types:
            self._ctx_types = ["materials", "models", "lights", "textures", "scenes", "hdr", "ani"]
        for atype in self._ctx_types:
            self._ctx_type_list.addItem(atype)

        self._ctx_type_list.currentRowChanged.connect(self._on_ctx_type_changed)
        if self._ctx_type_list.count() > 0:
            self._ctx_type_list.setCurrentRow(0)

        return w

    def _load_context_menu_preset(self) -> dict:
        """从 context_menu_preset.json 加载全部预设"""
        try:
            with open(_CONTEXT_MENU_PRESET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Settings] 加载 context_menu_preset.json 失败: {e}")
            return {}

    def _save_context_menu_preset(self):
        """将 _ctx_preset_data 写回 context_menu_preset.json"""
        try:
            with open(_CONTEXT_MENU_PRESET_PATH, "w", encoding="utf-8") as f:
                json.dump(self._ctx_preset_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Settings] 保存 context_menu_preset.json 失败: {e}")

    def _load_double_click_preset(self) -> dict:
        """从 double_click_preset.json 加载双击命令预设"""
        try:
            with open(_DOUBLE_CLICK_PRESET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Settings] 加载 double_click_preset.json 失败: {e}")
            return {}

    def _save_double_click_preset(self):
        """将 _dc_preset_data 写回 double_click_preset.json"""
        try:
            with open(_DOUBLE_CLICK_PRESET_PATH, "w", encoding="utf-8") as f:
                json.dump(self._dc_preset_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Settings] 保存 double_click_preset.json 失败: {e}")

    def _rebuild_dc_buttons(self, sub_lib: str):
        """根据子库类型重建双击命令单选按钮和子选项下拉框"""
        # 清除旧的 radio buttons
        for rb in self._dc_buttons.values():
            self._dc_button_group.removeButton(rb)
            self._dc_buttons_layout.removeWidget(rb)
            rb.setParent(None)
            rb.deleteLater()
        self._dc_buttons.clear()

        # 获取该子库可用的命令列表
        items = self._DOUBLE_CLICK_ITEMS.get(sub_lib, self._DOUBLE_CLICK_ITEMS_GENERIC)

        for cfg_key, i18n_key in items:
            rb = QtWidgets.QRadioButton(t(i18n_key))
            rb.setStyleSheet(
                "QRadioButton { color:#d0d0d0; font-size:13px; spacing:6px; }"
                "QRadioButton::indicator { width:16px; height:16px; }")
            rb.toggled.connect(lambda checked, k=cfg_key: self._on_dc_cmd_toggled(k, checked))
            self._dc_buttons_layout.addWidget(rb)
            self._dc_button_group.addButton(rb)
            self._dc_buttons[cfg_key] = rb

        # 读取当前子库的配置
        entry = self._dc_preset_data.get(sub_lib, {})
        if isinstance(entry, str):
            # 兼容旧格式：直接是命令 key
            current_cmd = entry
            current_opt = ""
        else:
            current_cmd = entry.get("cmd", "none")
            current_opt = entry.get("option", "")

        # 设置当前选中的命令
        if current_cmd in self._dc_buttons:
            self._dc_buttons[current_cmd].setChecked(True)
        else:
            self._dc_buttons["none"].setChecked(True)

        # 重建子选项下拉框
        self._rebuild_dc_option(current_cmd, current_opt, sub_lib)

    def _rebuild_dc_option(self, cmd: str, current_opt: str, sub_lib: str = ""):
        """根据选中的命令重建子选项下拉框"""
        self._dc_option_combo.clear()
        has_options = False

        if cmd == "create_dome_light":
            # 从 HDR_ligt/ 目录动态扫描 .ma 文件
            import os
            preset_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "Assets", "HDR_ligt")
            if os.path.isdir(preset_dir):
                ma_files = sorted(
                    [f for f in os.listdir(preset_dir) if f.lower().endswith('.ma')])
                if ma_files:
                    for f in ma_files:
                        self._dc_option_combo.addItem(f, f)
                    has_options = True
        elif cmd == "create_material":
            # 从 config.json 的 material_presets 动态读取材质类型
            import os, json
            cfg_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "Assets", "preset", "config.json")
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                presets = cfg.get("material_presets", [])
                # 第一项：自动（跟随渲染器）— 双击时按当前渲染器创建对应默认材质
                self._dc_option_combo.addItem("自动（跟随渲染器）", "auto")
                for p in presets:
                    node_type = p.get("node_type", "")
                    self._dc_option_combo.addItem(node_type, node_type)
                has_options = True
            except Exception:
                pass
        elif cmd in self._DOUBLE_CLICK_CMD_OPTIONS:
            options = self._DOUBLE_CLICK_CMD_OPTIONS[cmd]
            for opt in options:
                self._dc_option_combo.addItem(t(opt, no_warn=True), opt)
            has_options = True

        # 恢复已保存的子选项
        if has_options and current_opt:
            idx = self._dc_option_combo.findData(current_opt)
            if idx >= 0:
                self._dc_option_combo.setCurrentIndex(idx)
            elif self._dc_option_combo.count() > 0:
                # 默认选中第一个（兼容 ma 格式的默认项）
                self._dc_option_combo.setCurrentIndex(0)

        self._dc_option_widget.setVisible(has_options)

    def _on_dc_cmd_toggled(self, cmd: str, checked: bool):
        """双击命令单选按钮切换时，更新子选项下拉框"""
        if not checked:
            return
        # 从当前子库获取已保存的 option
        row = self._ctx_type_list.currentRow()
        if row < 0:
            return
        atype = self._ctx_types[row]
        entry = self._dc_preset_data.get(atype, {})
        if isinstance(entry, str):
            current_opt = ""
        else:
            current_opt = entry.get("option", "") if isinstance(entry, dict) else ""
        self._rebuild_dc_option(cmd, current_opt, atype)

    def _on_ctx_type_changed(self, row):
        """切换子库类型时，保存当前勾选状态并加载新类型的配置"""
        if row < 0:
            return
        if hasattr(self, '_ctx_type_list') and self._ctx_type_list.count() > 0:
            prev_row = self._ctx_type_list.property("_prev_row")
            if prev_row is not None and 0 <= prev_row < self._ctx_type_list.count():
                prev_type = self._ctx_types[prev_row]
                # 保存右键菜单状态
                entry = self._ctx_preset_data.setdefault(prev_type, {})
                for key, cb in self._ctx_cbs.items():
                    entry[key] = cb.isChecked()
                # 保存双击命令状态
                self._save_current_dc_to_preset(prev_type)
        self._ctx_type_list.setProperty("_prev_row", row)
        # 加载新类型
        atype = self._ctx_types[row]
        # 加载右键菜单
        entry = self._ctx_preset_data.get(atype, {})
        for key, cb in self._ctx_cbs.items():
            cb.setChecked(entry.get(key, False))
        # 加载双击命令
        self._rebuild_dc_buttons(atype)

    def _save_current_dc_to_preset(self, sub_lib: str):
        """保存当前双击命令和子选项到 _dc_preset_data"""
        cmd = "none"
        for key, rb in self._dc_buttons.items():
            if rb.isChecked():
                cmd = key
                break
        option = self._dc_option_combo.currentData() if self._dc_option_widget.isVisible() else ""
        self._dc_preset_data[sub_lib] = {"cmd": cmd, "option": option}

    def _set_locked(self, locked: bool):
        """锁定或解锁所有非通用标签页 — 锁定可查看不可编辑"""
        for i in range(1, self._tab_widget.count()):
            tab = self._tab_widget.widget(i)
            if not tab:
                continue
            for edit_type in (QtWidgets.QPlainTextEdit, QtWidgets.QLineEdit, QtWidgets.QTextEdit):
                for child in tab.findChildren(edit_type):
                    child.setReadOnly(locked)
            for ctrl_type in (QtWidgets.QCheckBox, QtWidgets.QPushButton, QtWidgets.QRadioButton, QtWidgets.QComboBox):
                for child in tab.findChildren(ctrl_type):
                    child.setEnabled(not locked)

    def _on_unlock_changed(self, state):
        """全局解锁时弹出警告"""
        if state:
            reply = QtWidgets.QMessageBox.warning(
                self, t("msg.unlock_warning_title"),
                t("msg.unlock_warning_text"),
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                self._unlock_cb.setChecked(False)
                return
        self._set_locked(not state)

    def _load_export_preset(self) -> dict:
        """从 export_preset.json 加载全部预设"""
        try:
            with open(_EXPORT_PRESET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Settings] 加载 export_preset.json 失败: {e}")
            return {}

    def _save_export_preset(self):
        """将 _export_preset_data 写回 export_preset.json"""
        try:
            with open(_EXPORT_PRESET_PATH, "w", encoding="utf-8") as f:
                json.dump(self._export_preset_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Settings] 保存 export_preset.json 失败: {e}")

    def _on_export_type_changed(self, row):
        if row < 0:
            return
        if hasattr(self, '_export_type_list') and self._export_type_list.count() > 0:
            prev_row = self._export_type_list.property("_prev_row")
            if prev_row is not None and 0 <= prev_row < self._export_type_list.count():
                prev_type = self._export_types[prev_row]
                entry = self._export_preset_data.setdefault(prev_type, {})
                for key, cb in self._export_cbs.items():
                    entry[key] = cb.isChecked()
        self._export_type_list.setProperty("_prev_row", row)
        # 加载新类型
        atype = self._export_types[row]
        entry = self._export_preset_data.get(atype, {})
        for key, cb in self._export_cbs.items():
            cb.setChecked(entry.get(key, False))

    # ── 支持格式标签页 ────────────────────────────

    def _create_formats_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        tip = QtWidgets.QLabel(t("label.formats_tip"))
        tip.setStyleSheet("color:#707070;font-size:11px;")
        layout.addWidget(tip)

        self._formats_edit = QtWidgets.QPlainTextEdit()
        exts = self._config.get("asset_file_extensions", [])
        self._formats_edit.setPlainText("\n".join(exts))
        self._formats_edit.setStyleSheet(
            "QPlainTextEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:8px; color:#e0e0e0; font-size:12px; font-family:monospace; }")
        layout.addWidget(self._formats_edit, 1)
        return w

    # ── 贴图后缀标签页 ────────────────────────────

    def _create_texture_suffixes_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 左侧：贴图类型列表
        self._tex_type_list = QtWidgets.QListWidget()
        self._tex_type_list.setFixedWidth(170)
        self._tex_type_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        layout.addWidget(self._tex_type_list)

        # 右侧：别名编辑区
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        self._tex_aliases_edit = QtWidgets.QPlainTextEdit()
        self._tex_aliases_edit.setStyleSheet(
            "QPlainTextEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:8px; color:#e0e0e0; font-size:12px; font-family:monospace; }")
        right.addWidget(self._tex_aliases_edit, 1)

        tip = QtWidgets.QLabel(t("label.tex_suffixes_tip"))
        tip.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(tip)

        # 恢复默认按钮
        btn_row = QtWidgets.QHBoxLayout()
        default_btn = QtWidgets.QPushButton(t("common.restore_default"))
        default_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#5294e2; border:1px solid #5294e2; "
            "padding:6px 14px; font-size:12px; border-radius:4px; }"
            "QPushButton:hover { background:#1a3a5f; }")
        default_btn.clicked.connect(self._on_reset_texture_suffixes)
        btn_row.addWidget(default_btn)
        btn_row.addStretch()
        right.addLayout(btn_row)

        layout.addLayout(right, 1)

        # 加载数据
        self._pbr_rules = self._load_pbr_rules()
        for type_name in self._pbr_rules:
            self._tex_type_list.addItem(type_name)

        # 切换类型时更新别名
        self._tex_type_list.currentRowChanged.connect(self._on_tex_type_changed)
        if self._tex_type_list.count() > 0:
            self._tex_type_list.setCurrentRow(0)

        return w

    def _load_pbr_rules(self) -> dict:
        """从 pbr_mapping.json 加载完整 texture_type_rules"""
        rules = {}
        try:
            with open(_PBR_MAPPING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("texture_type_rules", {})
            for type_name, rule in raw.items():
                aliases = list(dict.fromkeys(rule.get("aliases", [])))  # dedup, keep order
                rules[type_name] = aliases
        except Exception as e:
            print(f"[Settings] 加载 pbr_mapping.json 失败: {e}")
        return rules

    def _save_pbr_rules(self):
        """将 _pbr_rules 写回 pbr_mapping.json，保留原始结构"""
        try:
            with open(_PBR_MAPPING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = data.get("texture_type_rules", {})
            for type_name, aliases in self._pbr_rules.items():
                if type_name in rules:
                    rules[type_name]["aliases"] = [a for a in aliases if a.strip()]
            with open(_PBR_MAPPING_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Settings] 保存 pbr_mapping.json 失败: {e}")

    def _on_tex_type_changed(self, row):
        """切换贴图类型时，保存当前编辑并加载新类型的别名"""
        if row < 0:
            return
        if hasattr(self, '_tex_type_list') and self._tex_type_list.count() > 0:
            prev_row = self._tex_type_list.property("_prev_row")
            if prev_row is not None and 0 <= prev_row < self._tex_type_list.count():
                prev_type = self._tex_type_list.item(prev_row).text()
                raw = self._tex_aliases_edit.toPlainText().strip()
                if prev_type in self._pbr_rules:
                    self._pbr_rules[prev_type] = [l.strip() for l in raw.split("\n") if l.strip()]
        self._tex_type_list.setProperty("_prev_row", row)
        type_name = self._tex_type_list.item(row).text()
        aliases = self._pbr_rules.get(type_name, [])
        self._tex_aliases_edit.setPlainText("\n".join(aliases))

    def _on_reset_texture_suffixes(self):
        """从 pbr_mapping.json 重新加载"""
        self._pbr_rules = self._load_pbr_rules()
        row = self._tex_type_list.currentRow()
        if row >= 0:
            type_name = self._tex_type_list.item(row).text()
            self._tex_aliases_edit.setPlainText("\n".join(self._pbr_rules.get(type_name, [])))

    # ── 常用标签标签页 ────────────────────────────

    def _create_tags_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 左侧：子库列表
        self._tag_lib_list = QtWidgets.QListWidget()
        self._tag_lib_list.setFixedWidth(160)
        self._tag_lib_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        subs = self._config.get("sub_libraries", {})
        for k in subs:
            self._tag_lib_list.addItem(f"{subs[k]} ({k})")
        if self._tag_lib_list.count() > 0:
            self._tag_lib_list.setCurrentRow(0)
        layout.addWidget(self._tag_lib_list)

        # 右侧：标签编辑区
        right = QtWidgets.QVBoxLayout()
        self._tags_edit = QtWidgets.QPlainTextEdit()
        self._tags_edit.setStyleSheet(
            "QPlainTextEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:8px; color:#e0e0e0; font-size:12px; }")
        right.addWidget(self._tags_edit, 1)

        tag_tip = QtWidgets.QLabel(t("label.tags_tip"))
        tag_tip.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(tag_tip)
        layout.addLayout(right, 1)

        # 切换子库时更新标签列表
        self._tag_lib_list.currentRowChanged.connect(self._on_tag_lib_changed)
        self._on_tag_lib_changed(0)
        return w

    def _on_tag_lib_changed(self, row):
        if row < 0:
            return
        subs = self._config.get("sub_libraries", {})
        keys = list(subs.keys())
        if row >= len(keys):
            return
        lib_key = keys[row]
        tags = self._config.get("common_tags", {}).get(lib_key, [])
        self._tags_edit.setPlainText("\n".join(tags))
        self._tags_edit._current_lib_key = lib_key

    # ── 子库与分类标签页 ──────────────────────────

    def _create_subs_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 左侧：子库列表（支持右键菜单）
        self._sub_lib_list = QtWidgets.QListWidget()
        self._sub_lib_list.setFixedWidth(160)
        self._sub_lib_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        self._sub_lib_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._sub_lib_list.customContextMenuRequested.connect(self._on_sub_lib_context_menu)
        layout.addWidget(self._sub_lib_list)

        # 右侧编辑区
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)

        # 读取当前分类文件夹按钮
        sync_btn = QtWidgets.QPushButton(t("btn.sync_categories"))
        sync_btn.setStyleSheet(
            "QPushButton { background:#4a8c4a; color:#fff; border:none; padding:6px 12px; "
            "border-radius:4px; font-size:12px; }"
            "QPushButton:hover { background:#5aa55a; }")
        sync_btn.clicked.connect(self._on_sync_categories)
        right.addWidget(sync_btn)

        # 子库ID
        id_row = QtWidgets.QHBoxLayout()
        id_label = QtWidgets.QLabel(t("label.sub_id:"))
        id_label.setStyleSheet("color:#909090;font-size:12px;")
        id_row.addWidget(id_label)
        self._sub_id_edit = QtWidgets.QLineEdit()
        self._sub_id_edit.setStyleSheet(
            "QLineEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:6px 10px; color:#e0e0e0; font-size:12px; }")
        id_row.addWidget(self._sub_id_edit, 1)
        right.addLayout(id_row)

        # 显示名
        name_row = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel(t("label.display_name:"))
        name_label.setStyleSheet("color:#909090;font-size:12px;")
        name_row.addWidget(name_label)
        self._sub_name_edit = QtWidgets.QLineEdit()
        self._sub_name_edit.setStyleSheet(
            "QLineEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:6px 10px; color:#e0e0e0; font-size:12px; }")
        name_row.addWidget(self._sub_name_edit, 1)
        right.addLayout(name_row)

        # 默认分类
        cat_label = QtWidgets.QLabel(t("label.default_categories"))
        cat_label.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(cat_label)

        self._sub_cats_edit = QtWidgets.QPlainTextEdit()
        self._sub_cats_edit.setStyleSheet(
            "QPlainTextEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:8px; color:#e0e0e0; font-size:12px; font-family:monospace; }")
        right.addWidget(self._sub_cats_edit, 1)

        layout.addLayout(right, 1)

        # 加载数据
        self._subs_data = {}
        subs = self._config.get("sub_libraries", {})
        cats = self._config.get("default_sub_categories", {})
        for k in subs:
            self._subs_data[k] = {
                "display": subs[k],
                "categories": cats.get(k, []),
            }
            self._sub_lib_list.addItem(f"{subs[k]} ({k})")

        # 切换时保存并加载
        self._sub_lib_list.currentRowChanged.connect(self._on_sub_lib_changed)
        if self._sub_lib_list.count() > 0:
            self._sub_lib_list.setCurrentRow(0)

        return w

    def _populate_sub_lib_list(self):
        """重新填充子库列表"""
        self._sub_lib_list.clear()
        for k in self._subs_data:
            display = self._subs_data[k].get("display", k)
            self._sub_lib_list.addItem(f"{display} ({k})")
        if self._sub_lib_list.count() > 0:
            self._sub_lib_list.setCurrentRow(0)

    def _on_sub_lib_changed(self, row):
        """切换子库时保存当前编辑并加载新子库数据"""
        if row < 0:
            return
        # 保存当前
        if hasattr(self, '_sub_lib_list') and self._sub_lib_list.count() > 0:
            prev_row = self._sub_lib_list.property("_prev_row")
            if prev_row is not None and 0 <= prev_row < self._sub_lib_list.count():
                prev_key = list(self._subs_data.keys())[prev_row]
                new_key = self._sub_id_edit.text().strip()
                new_display = self._sub_name_edit.text().strip()
                cats_lines = self._sub_cats_edit.toPlainText().strip()
                # 更新分类
                self._subs_data[prev_key]["categories"] = [
                    l.strip().split(None, 1) for l in cats_lines.split("\n") if l.strip()
                ]
                # 更新显示名
                if new_display:
                    self._subs_data[prev_key]["display"] = new_display
                # 如果 ID 变更，重命名字典 key
                if new_key and new_key != prev_key:
                    if new_key in self._subs_data:
                        QtWidgets.QMessageBox.warning(self, t("msg.notice"), t("msg.sub_id_exists", id=new_key))
                    else:
                        self._subs_data[new_key] = self._subs_data.pop(prev_key)
                        item = self._sub_lib_list.item(prev_row)
                        if item:
                            item.setText(f"{self._subs_data[new_key]['display']} ({new_key})")
        self._sub_lib_list.setProperty("_prev_row", row)
        # 加载新
        key = list(self._subs_data.keys())[row]
        data = self._subs_data[key]
        self._sub_id_edit.setText(key)
        self._sub_name_edit.setText(data["display"])
        cats_lines = "\n".join(f"{c[0]} {c[1]}" for c in data["categories"] if len(c) >= 2)
        self._sub_cats_edit.setPlainText(cats_lines)

    def _on_sync_categories(self):
        """从当前库的实际文件夹结构同步分类到设置"""
        if not self._material_manager:
            QtWidgets.QMessageBox.warning(self, t("msg.notice"), t("msg.no_asset_manager"))
            return
        library_path = self._material_manager.get_library_path()
        if not library_path:
            QtWidgets.QMessageBox.warning(self, t("msg.notice"), t("msg.load_library_first"))
            return

        reply = QtWidgets.QMessageBox.question(
            self, t("msg.sync_confirm_title"),
            t("msg.sync_confirm_text"),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._subs_data = {}
        self._sub_lib_list.clear()

        try:
            categories = self._material_manager.get_category_tree()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, t("msg.notice"), t("msg.read_categories_failed", error=str(e)))
            return

        for cat in categories:
            cat_id = cat.get("id", "")
            display = cat.get("name_cn", cat_id)
            self._subs_data[cat_id] = {
                "display": display,
                "categories": [],
            }

            def collect_cats(nodes, parent_id=""):
                for node in nodes:
                    n_id = node.get("id", "")
                    n_cn = node.get("name_cn", n_id)
                    self._subs_data[cat_id]["categories"].append([n_id, n_cn])
                    if node.get("children"):
                        collect_cats(node["children"], n_id)

            if cat.get("children"):
                collect_cats(cat["children"])

            self._sub_lib_list.addItem(f"{display} ({cat_id})")

        if self._sub_lib_list.count() > 0:
            self._sub_lib_list.setCurrentRow(0)

        QtWidgets.QMessageBox.information(self, t("msg.success"), t("msg.categories_synced"))

    # ── 高级配置标签页 ────────────────────────────

    _ADV_KEYS = [
        ("geometry_extensions", t("adv.label.geometry_extensions")),
        ("image_extensions", t("adv.label.image_extensions")),
        ("material_node_types", t("adv.label.material_node_types")),
    ]

    def _create_advanced_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)

        # 左侧：配置项列表
        self._adv_type_list = QtWidgets.QListWidget()
        self._adv_type_list.setFixedWidth(240)
        self._adv_type_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        layout.addWidget(self._adv_type_list)

        # 右侧编辑区
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)

        tip = QtWidgets.QLabel(t("label.adv_tip"))
        tip.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(tip)

        self._adv_edit = QtWidgets.QPlainTextEdit()
        self._adv_edit.setStyleSheet(
            "QPlainTextEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:8px; color:#e0e0e0; font-size:12px; font-family:monospace; }")
        right.addWidget(self._adv_edit, 1)

        layout.addLayout(right, 1)

        # 加载数据
        self._adv_data = {}
        for key, desc in self._ADV_KEYS:
            vals = self._config.get(key, [])
            self._adv_data[key] = list(vals)
            self._adv_type_list.addItem(desc)

        self._adv_type_list.currentRowChanged.connect(self._on_adv_type_changed)
        if self._adv_type_list.count() > 0:
            self._adv_type_list.setCurrentRow(0)

        return w

    def _on_adv_type_changed(self, row):
        """切换时保存当前编辑并加载新配置"""
        if row < 0:
            return
        if hasattr(self, '_adv_type_list') and self._adv_type_list.count() > 0:
            prev_row = self._adv_type_list.property("_prev_row")
            if prev_row is not None and 0 <= prev_row < self._adv_type_list.count():
                prev_key = self._ADV_KEYS[prev_row][0]
                raw = self._adv_edit.toPlainText().strip()
                self._adv_data[prev_key] = [l.strip() for l in raw.split("\n") if l.strip()]
        self._adv_type_list.setProperty("_prev_row", row)
        key = self._ADV_KEYS[row][0]
        vals = self._adv_data.get(key, [])
        self._adv_edit.setPlainText("\n".join(vals))

    # ── 工具方法 ──────────────────────────────────

    def _lb(self, text):
        lb = QtWidgets.QLabel(text)
        lb.setStyleSheet("color:#909090;font-size:13px;")
        return lb

    def _group(self, title):
        g = QtWidgets.QGroupBox(title)
        g.setStyleSheet(
            "QGroupBox { color:#e0e0e0; font-size:13px; font-weight:bold; "
            "border:1px solid #3a3a3a; border-radius:6px; "
            "margin-top:10px; padding:14px 12px 12px; }"
            "QGroupBox::title { subcontrol-origin:margin; "
            "subcontrol-position:top left; padding:0 8px; }")
        return g

    def _populate_library_list(self):
        """加载已配置的库列表"""
        self._lib_list.clear()
        libs = self._settings.get("library_paths", [])
        # 兼容旧格式：last_library_path
        old_path = self._settings.get("last_library_path", "")
        if old_path and not any(l.get("path") == old_path for l in libs):
            libs.insert(0, {"name": "默认库", "path": old_path})
        default_ref = self._settings.get("default_library", "")
        self._lib_data = libs
        default_idx = 0
        for i, lib in enumerate(libs):
            name = lib.get("name", os.path.basename(lib["path"]))
            path = lib["path"]
            # 设置了默认库时仅匹配路径（兼容旧格式：名称匹配）；未设置默认库时以第一个为默认
            if default_ref:
                is_default = (path == default_ref or name == default_ref)
            else:
                is_default = (i == 0)
            suffix = f"  {t('label.default_suffix')}" if is_default else ""
            self._lib_list.addItem(f"{name} — {path}{suffix}")
            if is_default:
                default_idx = i
        if self._lib_list.count() > 0:
            self._lib_list.setCurrentRow(default_idx)

    def _on_add_library(self):
        """添加新资产库"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, t("filedialog.select_library_dir"), os.path.expanduser("~"))
        if not path:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, t("inputdlg.library_name"), t("inputdlg.library_display_name"), text=os.path.basename(path))
        if not ok or not name.strip():
            name = os.path.basename(path)
        self._lib_data.append({"name": name.strip(), "path": path})
        self._populate_library_list()

    def _on_create_library(self):
        """创建新的标准资产库"""
        # 选择父目录
        parent_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, t("filedialog.select_create_location"), os.path.expanduser("~"))
        if not parent_dir:
            return

        # 输入资产库名称
        default_name = "SquirrelLib"
        name, ok = QtWidgets.QInputDialog.getText(
            self, t("inputdlg.create_library"), t("inputdlg.library_name_prompt"), text=default_name)
        if not ok or not name.strip():
            name = default_name

        lib_path = os.path.join(parent_dir, name.strip())
        if os.path.exists(lib_path):
            QtWidgets.QMessageBox.warning(
                self, t("msg.dir_exists"),
                t("msg.dir_exists_text", path=lib_path))
            return

        # 创建资产库
        json_handler = JSONHandler()
        sub_libraries = {
            "materials": "材质", "models": "模型", "lights": "灯光",
            "textures": "贴图", "scenes": "场景", "hdr": "HDR",
        }

        try:
            os.makedirs(lib_path, exist_ok=True)

            # 创建 library.json
            json_handler.write_json(os.path.join(lib_path, "library.json"), {
                "version": "2.0",
                "name": name.strip(),
                "sub_libraries": list(sub_libraries.keys()),
            })

            # 创建子库目录
            for sub_dir, sub_name in sub_libraries.items():
                sub_path = os.path.join(lib_path, sub_dir)
                os.makedirs(sub_path, exist_ok=True)
                # 创建 FolderMetadata.fdata
                json_handler.write_json(os.path.join(sub_path, "FolderMetadata.fdata"), {
                    "id": str(uuid.uuid4()),
                    "name_cn": sub_name,
                    "type": sub_dir,
                })

            QtWidgets.QMessageBox.information(
                self, t("msg.create_success"),
                t("msg.create_success_text", path=lib_path))

            # 添加到库列表
            self._lib_data.append({"name": name.strip(), "path": lib_path})
            self._populate_library_list()

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, t("msg.create_failed"),
                t("msg.create_failed_text", error=e))

    def _on_remove_library(self):
        """删除选中的库（至少保留1个）"""
        row = self._lib_list.currentRow()
        if row < 0:
            return
        if len(self._lib_data) <= 1:
            QtWidgets.QMessageBox.warning(self, t("msg.cannot_delete"), t("msg.keep_one_library"))
            return
        del self._lib_data[row]
        self._populate_library_list()

    def _on_set_default_library(self):
        """设为默认库（唯一，保存路径避免同名歧义）"""
        row = self._lib_list.currentRow()
        if row < 0:
            return
        lib = self._lib_data[row]
        self._settings["default_library"] = lib["path"]
        self._populate_library_list()

    def _on_settings_help(self):
        """打开设置帮助"""
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = _i18n_help_path(os.path.join(plugin_root, "Assets", "help", "help_settings.html"))
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[Help] 设置帮助文件未找到:", help_path)

    def _on_restore_defaults(self):
        reply = QtWidgets.QMessageBox.question(
            self, t("common.restore_default"), t("msg.restore_default_text"),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self._font_slider.setValue(13)
            self._font_val.setText("13")
            self._view_icon.setChecked(True)
            self._view_list.setChecked(False)
            self._thumb_slider.setValue(180)
            self._thumb_val.setText("180px")
            self._conflict_prompt_rb.setChecked(True)

            # 重置导出默认值 → 重读 export_preset.json
            self._export_preset_data = self._load_export_preset()
            cur_row = self._export_type_list.currentRow()
            if cur_row >= 0:
                atype = self._export_types[cur_row]
                entry = self._export_preset_data.get(atype, {})
                for key, cb in self._export_cbs.items():
                    cb.setChecked(entry.get(key, False))

            # 重置贴图后缀
            self._on_reset_texture_suffixes()

            # 重置高级配置（材质节点类型、几何体/图像扩展名）
            default_advanced = {
                "geometry_extensions": [
                    ".ma", ".mb", ".fbx", ".obj", ".abc", ".usd",
                    ".usda", ".usdc", ".glb", ".gltf", ".dae",
                    ".ass", ".proxy", ".vrmesh", ".vdb",
                ],
                "image_extensions": [
                    ".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tga",
                    ".tiff", ".tif", ".bmp", ".psd",
                ],
                "material_node_types": [
                    "aiStandardSurface", "standardSurface", "lambert", "blinn",
                    "phong", "openPBRSurface", "pxrSurface", "aiHair", "aiSkin",
                    "aiVolume", "VRayMtl", "RedshiftMaterial",
                ],
            }
            for key in ("geometry_extensions", "image_extensions", "material_node_types"):
                self._adv_data[key] = list(default_advanced[key])
            cur_row = self._adv_type_list.currentRow()
            if cur_row >= 0:
                key = self._ADV_KEYS[cur_row][0]
                self._adv_edit.setPlainText("\n".join(self._adv_data[key]))

            # 重置右键菜单 → 重读 context_menu_preset.json
            self._ctx_preset_data = self._load_context_menu_preset()
            cur_row = self._ctx_type_list.currentRow()
            if cur_row >= 0:
                atype = self._ctx_types[cur_row]
                entry = self._ctx_preset_data.get(atype, {})
                for key, cb in self._ctx_cbs.items():
                    cb.setChecked(entry.get(key, False))

    def _collect_config(self) -> dict:
        """从各编辑控件收集配置并写入 self._config"""
        # 支持格式
        raw = self._formats_edit.toPlainText().strip()
        self._config["asset_file_extensions"] = [
            l.strip() for l in raw.split("\n") if l.strip()
        ]

        # 导出默认值 → 保存到 export_preset.json
        cur_row = self._export_type_list.currentRow()
        if cur_row >= 0:
            atype = self._export_types[cur_row]
            entry = self._export_preset_data.setdefault(atype, {})
            for key, cb in self._export_cbs.items():
                entry[key] = cb.isChecked()
        self._save_export_preset()

        # 右键菜单 → 保存到 context_menu_preset.json
        cur_row = self._ctx_type_list.currentRow()
        if cur_row >= 0:
            atype = self._ctx_types[cur_row]
            entry = self._ctx_preset_data.setdefault(atype, {})
            for key, cb in self._ctx_cbs.items():
                entry[key] = cb.isChecked()
        self._save_context_menu_preset()

        # 双击命令 → 保存到 double_click_preset.json
        cur_row = self._ctx_type_list.currentRow()
        if cur_row >= 0:
            atype = self._ctx_types[cur_row]
            self._save_current_dc_to_preset(atype)
        self._save_double_click_preset()

        # 贴图别名 → 先保存当前编辑，再写回 pbr_mapping.json
        cur_row = self._tex_type_list.currentRow()
        if cur_row >= 0:
            cur_type = self._tex_type_list.item(cur_row).text()
            raw = self._tex_aliases_edit.toPlainText().strip()
            if cur_type in self._pbr_rules:
                self._pbr_rules[cur_type] = [l.strip() for l in raw.split("\n") if l.strip()]
        self._save_pbr_rules()

        # 常用标签
        if hasattr(self._tags_edit, '_current_lib_key'):
            subs = self._config.get("sub_libraries", {})
            keys = list(subs.keys())
            idx = self._tag_lib_list.currentRow()
            if 0 <= idx < len(keys):
                lib_key = keys[idx]
                raw_tags = self._tags_edit.toPlainText().strip()
                if "common_tags" not in self._config:
                    self._config["common_tags"] = {}
                self._config["common_tags"][lib_key] = [
                    t.strip() for t in raw_tags.split("\n") if t.strip()
                ]

        # 子库与分类
        cur_row = self._sub_lib_list.currentRow()
        if cur_row >= 0:
            key = list(self._subs_data.keys())[cur_row]
            new_key = self._sub_id_edit.text().strip()
            new_display = self._sub_name_edit.text().strip()
            cats_lines = self._sub_cats_edit.toPlainText().strip()
            # 更新分类
            self._subs_data[key]["categories"] = [
                l.strip().split(None, 1) for l in cats_lines.split("\n") if l.strip()
            ]
            # 更新显示名
            if new_display:
                self._subs_data[key]["display"] = new_display
            # 如果 ID 变更，重命名字典 key
            if new_key and new_key != key:
                if new_key not in self._subs_data:
                    self._subs_data[new_key] = self._subs_data.pop(key)
        new_subs = {}
        new_cats = {}
        for k, data in self._subs_data.items():
            display = data.get("display", k)
            new_subs[k] = display
            new_cats[k] = data.get("categories", [])
        self._config["sub_libraries"] = new_subs
        self._config["default_sub_categories"] = new_cats

        # 高级配置（geometry_extensions / image_extensions / material_node_types）
        cur_row = self._adv_type_list.currentRow()
        if cur_row >= 0:
            cur_key = self._ADV_KEYS[cur_row][0]
            raw = self._adv_edit.toPlainText().strip()
            self._adv_data[cur_key] = [l.strip() for l in raw.split("\n") if l.strip()]
        for key in ("geometry_extensions", "image_extensions", "material_node_types"):
            if key in self._adv_data:
                self._config[key] = list(self._adv_data[key])

        # 同名冲突处理策略
        if self._conflict_prompt_rb.isChecked():
            mode = NameConflictDialog.MODE_PROMPT
        elif self._conflict_auto_rb.isChecked():
            mode = NameConflictDialog.MODE_AUTO_RENAME
        else:
            mode = NameConflictDialog.MODE_MANUAL
        self._config["name_conflict_policy"] = {
            "remember_choice": mode != NameConflictDialog.MODE_PROMPT,
            "mode": mode,
            "manual_name": "",
        }

        # 贴图导入策略
        if self._tex_policy_asset.isChecked():
            self._config["texture_import_policy"] = "asset_directory"
        elif self._tex_policy_source.isChecked():
            self._config["texture_import_policy"] = "source_directory"
        else:
            self._config["texture_import_policy"] = "copy_to_project"

        # 依赖文件导入策略
        if self._dep_policy_asset.isChecked():
            self._config["dependency_import_policy"] = "asset_directory"
        elif self._dep_policy_source.isChecked():
            self._config["dependency_import_policy"] = "source_directory"
        else:
            self._config["dependency_import_policy"] = "copy_to_project"

        return self._config

    def _on_apply(self):
        # 解锁状态下应用时弹出确认警告
        if hasattr(self, '_unlock_cb') and self._unlock_cb.isChecked():
            reply = QtWidgets.QMessageBox.critical(
                self, t("msg.apply_confirm_title"),
                t("msg.apply_confirm_text"),
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        # 收集配置
        self._collect_config()
        # 写回 config.json
        self._save_config()

        # 常规设置
        settings = {
            "font_size": self._font_slider.value(),
            "default_view": "icon" if self._view_icon.isChecked() else "list",
            "default_thumb_size": self._thumb_slider.value(),
            "language": self._lang_combo.currentData(),
        }
        if hasattr(self, '_lib_data') and self._lib_data:
            settings["library_paths"] = list(self._lib_data)
            settings["default_library"] = self._settings.get("default_library", "")
        self.settingsChanged.emit(settings)

    def _on_sub_lib_context_menu(self, pos):
        """子库列表右键菜单"""
        menu = QtWidgets.QMenu(self)

        add_action = menu.addAction(t("ctx_menu.add_sub_lib"))
        add_action.triggered.connect(self._on_add_sub_lib)

        row = -1
        item = self._sub_lib_list.itemAt(pos)
        if item is not None:
            row = self._sub_lib_list.row(item)
        if row >= 0:
            key = list(self._subs_data.keys())[row]
            CORE_LIBS = {"materials", "models", "lights", "textures", "scenes", "hdr", "ani"}
            if key not in CORE_LIBS:
                delete_action = menu.addAction(t("ctx_menu.delete_sub_lib"))
                delete_action.triggered.connect(lambda: self._on_delete_sub_lib(key))

        menu.exec_(self._sub_lib_list.mapToGlobal(pos))

    def _on_add_sub_lib(self):
        """添加新的自定义子库"""
        idx = 1
        while f"custom_{idx}" in self._subs_data:
            idx += 1
        new_id = f"custom_{idx}"
        self._subs_data[new_id] = {
            "display": f"自定义{idx}",
            "categories": [],
        }
        self._populate_sub_lib_list()

    def _on_delete_sub_lib(self, key=None):
        """删除选中的子库（核心子库不可删除）"""
        if key is None:
            row = self._sub_lib_list.currentRow()
            if row < 0:
                return
            key = list(self._subs_data.keys())[row]
        CORE_LIBS = {"materials", "models", "lights", "textures", "scenes", "hdr", "ani"}
        if key in CORE_LIBS:
            QtWidgets.QMessageBox.warning(
                self, t("msg.cannot_delete"),
                t("msg.core_lib_locked")
            )
            return
        reply = QtWidgets.QMessageBox.question(
            self, t("msg.confirm_delete"),
            t("msg.confirm_delete_text", name=self._subs_data[key].get('display', key))
        )
        if reply == QtWidgets.QMessageBox.Yes:
            del self._subs_data[key]
            self._populate_sub_lib_list()
            if "sub_libraries" in self._config and key in self._config["sub_libraries"]:
                del self._config["sub_libraries"][key]
            if "default_sub_categories" in self._config and key in self._config["default_sub_categories"]:
                del self._config["default_sub_categories"][key]
            self._save_config()

    def _on_ok(self):
        self._on_apply()
        self.accept()

    def get_settings(self):
        return {
            "font_size": self._font_slider.value(),
            "default_view": "icon" if self._view_icon.isChecked() else "list",
            "thumb_size": self._thumb_slider.value(),
        }


def _slider_style():
    return (
        "QSlider::groove:horizontal { background:#3a3a3a; height:4px; border-radius:2px; }"
        "QSlider::handle:horizontal { background:#d0d0d0; width:14px; height:14px;"
        "margin:-5px 0; border-radius:7px; }"
        "QSlider::handle:horizontal:hover { background:#5294e2; }"
    )
