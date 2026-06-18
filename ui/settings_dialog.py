import os
import json
import uuid

from ..utils.maya_utils import get_qt_modules
from ..utils.json_handler import JSONHandler
from ..utils.settings import apply_font_size_to_widget
from .name_conflict_dialog import NameConflictDialog

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


class SettingsDialog(QtWidgets.QDialog):
    settingsChanged = QtCore.Signal(dict)

    DEFAULT_SETTINGS = {
        "font_size": 13,
        "thumb_size": 180,
        "default_view": "icon",
        "last_library_path": "",
    }

    def __init__(self, parent=None, current_settings=None):
        super(SettingsDialog, self).__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(960, 750)
        self.setStyleSheet("background-color: #2a2a2a;")

        self._settings = dict(current_settings) if current_settings else dict(self.DEFAULT_SETTINGS)
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

        self._tab_widget.addTab(self._create_general_tab(), "常规")
        self._tab_widget.addTab(self._create_export_defaults_tab(), "导出默认值")
        self._tab_widget.addTab(self._create_formats_tab(), "支持格式")
        self._tab_widget.addTab(self._create_texture_suffixes_tab(), "贴图后缀")
        self._tab_widget.addTab(self._create_tags_tab(), "常用标签")
        self._tab_widget.addTab(self._create_subs_tab(), "子库与分类")
        self._tab_widget.addTab(self._create_advanced_tab(), "高级配置")

        # 将标签页放入滚动区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self._tab_widget)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        layout.addWidget(scroll, 1)

        # 全局解锁复选框（默认锁定，常规标签页除外）
        self._unlock_cb = QtWidgets.QCheckBox("解锁全部配置")
        self._unlock_cb.setStyleSheet("QCheckBox { color:#909090; font-size:11px; }")
        self._unlock_cb.stateChanged.connect(self._on_unlock_changed)
        layout.addWidget(self._unlock_cb)

        # 初始锁定非通用标签页
        self._set_locked(True)

        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(10)
        restore_btn = QtWidgets.QPushButton("恢复默认设置")
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
        help_btn.setToolTip("设置帮助")
        help_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ffa502; border: none; "
            "font-size: 16px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_settings_help)
        btn_layout.addWidget(help_btn)

        btn_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton("取消")
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #a0a0a0; border: none; "
            "padding: 9px 24px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a4a4a; color: #e0e0e0; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        apply_btn = QtWidgets.QPushButton("应用")
        apply_btn.setStyleSheet(
            "QPushButton { background-color: #2d6a4f; color: #e0e0e0; border: none; "
            "padding: 9px 24px; font-size: 13px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #40916c; }"
        )
        apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(apply_btn)

        ok_btn = QtWidgets.QPushButton("确定")
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

        g1 = self._group("字体大小")
        g1l = QtWidgets.QVBoxLayout(g1)
        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(self._lb("全局字体大小:"))
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

        g2 = self._group("默认视图")
        g2l = QtWidgets.QHBoxLayout(g2)
        self._view_icon = QtWidgets.QRadioButton("图标网格")
        self._view_list = QtWidgets.QRadioButton("列表视图")
        dv = self._settings.get("default_view", "icon")
        self._view_icon.setChecked(dv == "icon")
        self._view_list.setChecked(dv == "list")
        for rb in [self._view_icon, self._view_list]:
            rb.setStyleSheet("QRadioButton { color: #d0d0d0; font-size: 13px; }")
        g2l.addWidget(self._view_icon)
        g2l.addWidget(self._view_list)
        g2l.addStretch()
        layout.addWidget(g2)

        g3 = self._group("缩略图")
        g3l = QtWidgets.QVBoxLayout(g3)
        tr = QtWidgets.QHBoxLayout()
        tr.addWidget(self._lb("默认缩略图大小:"))
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

        g4 = self._group("资产库管理")
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

        add_btn = QtWidgets.QPushButton("+ 添加资产库")
        add_btn.setStyleSheet(btn_style)
        add_btn.clicked.connect(self._on_add_library)
        btn_row.addWidget(add_btn)

        create_btn = QtWidgets.QPushButton("+ 创建资产库")
        create_btn.setStyleSheet(btn_style)
        create_btn.clicked.connect(self._on_create_library)
        btn_row.addWidget(create_btn)

        rm_btn = QtWidgets.QPushButton("- 删除选中")
        rm_btn.setStyleSheet(btn_style)
        rm_btn.clicked.connect(self._on_remove_library)
        btn_row.addWidget(rm_btn)

        set_default_btn = QtWidgets.QPushButton("☆ 设为默认")
        set_default_btn.setStyleSheet(btn_style)
        set_default_btn.clicked.connect(self._on_set_default_library)
        btn_row.addWidget(set_default_btn)

        btn_row.addStretch()
        g4l.addLayout(btn_row)

        layout.addWidget(g4)

        # ── 同名冲突处理 ──
        g5 = self._group("同名冲突处理")
        g5l = QtWidgets.QVBoxLayout(g5)
        g5l.setSpacing(6)

        policy = self._config.get("name_conflict_policy", {})
        current_mode = policy.get("mode", NameConflictDialog.MODE_PROMPT)

        self._conflict_prompt_rb = QtWidgets.QRadioButton("每次询问（导出时遇到同名资产询问如何处理）")
        self._conflict_auto_rb = QtWidgets.QRadioButton("自动重命名（追加 _001, _002 …）")
        self._conflict_manual_rb = QtWidgets.QRadioButton("手动输入（弹出对话框手动输入新名称）")

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
        g6 = self._group("贴图导入策略")
        g6l = QtWidgets.QVBoxLayout(g6)
        self._tex_policy_copy = QtWidgets.QRadioButton("拷贝贴图到项目 — 将贴图拷贝到工程 sourceimages/ 目录")
        self._tex_policy_asset = QtWidgets.QRadioButton("当前资产目录 — 不拷贝，直接读取 .zasset 内的贴图")
        self._tex_policy_source = QtWidgets.QRadioButton("源文件目录 — 不修改贴图路径，保持导出时的原始路径")
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
        g7 = self._group("依赖文件导入策略")
        g7l = QtWidgets.QVBoxLayout(g7)
        self._dep_policy_copy = QtWidgets.QRadioButton(
            "拷贝到项目 — 将依赖文件拷贝到工程对应目录")
        self._dep_policy_asset = QtWidgets.QRadioButton(
            "当前资产目录 — 不拷贝，直接读取 .zasset 内的依赖文件")
        self._dep_policy_source = QtWidgets.QRadioButton(
            "源文件目录 — 不修改依赖文件路径，保持导出时的原始路径")
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
        ("zmetal", "材质节点 (.zmetal)", "material"),
        ("zmetal_merge", "合并材质 (.zmetal_merge)", "material"),
        ("mcm", "材质→模型映射 (.mcm)", "material"),
        ("ma", "Maya ASCII (.ma)", "geometry"),
        ("mb", "Maya Binary (.mb)", "geometry"),
        ("fbx", "FBX (.fbx)", "geometry"),
        ("obj", "OBJ (.obj)", "geometry"),
        ("usd", "USD (.usd)", "geometry"),
        ("abc", "Alembic (.abc)", "cache"),
        ("arnold", "Arnold (.ass)", "proxy"),
        ("vray", "V-Ray (.vrscene)", "proxy"),
        ("redshift", "Redshift (.rs)", "proxy"),
        ("vrmesh", "V-Ray 代理 (.vrmesh)", "proxy"),
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
        mat_group = self._group("材质格式")
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
        geo_group = self._group("几何体格式")
        geo_layout = QtWidgets.QVBoxLayout(geo_group)
        for key, label, category in self._EXPORT_FIELDS:
            if category == "geometry":
                cb = QtWidgets.QCheckBox(label)
                cb.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
                geo_layout.addWidget(cb)
                self._export_cbs[key] = cb
        right.addWidget(geo_group)

        # 缓存与代理格式
        other_group = self._group("缓存与代理")
        other_layout = QtWidgets.QVBoxLayout(other_group)
        for key, label, category in self._EXPORT_FIELDS:
            if category in ("cache", "proxy"):
                cb = QtWidgets.QCheckBox(label)
                cb.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
                other_layout.addWidget(cb)
                self._export_cbs[key] = cb
        right.addWidget(other_group)

        # 收集关联文件
        collect_group = self._group("收集关联文件")
        collect_layout = QtWidgets.QVBoxLayout(collect_group)
        cb_collect = QtWidgets.QCheckBox("收集场景中已挂载的缓存/代理/引用文件")
        cb_collect.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
        collect_layout.addWidget(cb_collect)
        self._export_cbs["collect_associated"] = cb_collect

        cb_textures = QtWidgets.QCheckBox("贴图 textures/")
        cb_textures.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
        collect_layout.addWidget(cb_textures)
        self._export_cbs["textures"] = cb_textures
        right.addWidget(collect_group)

        # 仅导出材质
        mat_only_group = self._group("导出选项")
        mat_only_layout = QtWidgets.QVBoxLayout(mat_only_group)
        cb_mat_only = QtWidgets.QCheckBox("仅导出材质（跳过几何体/代理）")
        cb_mat_only.setStyleSheet("QCheckBox { color:#d0d0d0; font-size:13px; }")
        mat_only_layout.addWidget(cb_mat_only)
        self._export_cbs["material_only"] = cb_mat_only
        right.addWidget(mat_only_group)

        right.addStretch()
        layout.addLayout(right, 1)

        # 加载数据
        self._export_preset_data = self._load_export_preset()
        for atype in self._EXPORT_ASSET_TYPES:
            self._export_type_list.addItem(atype)

        self._export_type_list.currentRowChanged.connect(self._on_export_type_changed)
        if self._export_type_list.count() > 0:
            self._export_type_list.setCurrentRow(0)

        return w

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
                self, "⚠ 配置修改警告",
                "除非你知道自己在改什么，否则不要修改！\n\n"
                "以下标签页将被解锁：\n"
                "• 导出默认值 — 每种资产类型的导出格式\n"
                "• 支持格式 — 资产库可识别的文件扩展名\n"
                "• 贴图后缀 — 导入时识别贴图通道类型的别名\n"
                "• 常用标签 — 各子库的预置标签列表\n"
                "• 子库与分类 — 子库及默认子分类配置\n"
                "• 高级配置 — 几何体/图像扩展名和材质节点类型\n\n"
                "错误的配置可能导致资产库功能异常。",
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
                prev_type = self._EXPORT_ASSET_TYPES[prev_row]
                entry = self._export_preset_data.setdefault(prev_type, {})
                for key, cb in self._export_cbs.items():
                    entry[key] = cb.isChecked()
        self._export_type_list.setProperty("_prev_row", row)
        # 加载新类型
        atype = self._EXPORT_ASSET_TYPES[row]
        entry = self._export_preset_data.get(atype, {})
        for key, cb in self._export_cbs.items():
            cb.setChecked(entry.get(key, False))

    # ── 支持格式标签页 ────────────────────────────

    def _create_formats_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        tip = QtWidgets.QLabel("每行一个文件扩展名，修改后请点击「应用」")
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

        tip = QtWidgets.QLabel("每行一个贴图名，对应 JSON 中该类型的 aliases")
        tip.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(tip)

        # 恢复默认按钮
        btn_row = QtWidgets.QHBoxLayout()
        default_btn = QtWidgets.QPushButton("恢复默认")
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

        tag_tip = QtWidgets.QLabel("每行一个标签，用换行分隔")
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

        # 左侧：子库列表
        self._sub_lib_list = QtWidgets.QListWidget()
        self._sub_lib_list.setFixedWidth(160)
        self._sub_lib_list.setStyleSheet(
            "QListWidget { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:4px; "
            "color:#d0d0d0; font-size:12px; }"
            "QListWidget::item:selected { background:#2d4a6f; }")
        layout.addWidget(self._sub_lib_list)

        # 右侧编辑区
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)

        # 子库ID
        id_row = QtWidgets.QHBoxLayout()
        id_label = QtWidgets.QLabel("子库ID:")
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
        name_label = QtWidgets.QLabel("显示名:")
        name_label.setStyleSheet("color:#909090;font-size:12px;")
        name_row.addWidget(name_label)
        self._sub_name_edit = QtWidgets.QLineEdit()
        self._sub_name_edit.setStyleSheet(
            "QLineEdit { background:#333; border:1px solid #4a4a4a; border-radius:4px; "
            "padding:6px 10px; color:#e0e0e0; font-size:12px; }")
        name_row.addWidget(self._sub_name_edit, 1)
        right.addLayout(name_row)

        # 默认分类
        cat_label = QtWidgets.QLabel("默认分类列表（每行一个，格式: id 名称）")
        cat_label.setStyleSheet("color:#707070;font-size:11px;")
        right.addWidget(cat_label)

        # 分类操作按钮
        cat_btn_row = QtWidgets.QHBoxLayout()
        cat_btn_style = ("QPushButton { background:#3a3a3a; color:#d0d0d0; border:none; "
                         "padding:4px 10px; font-size:11px; border-radius:3px; }"
                         "QPushButton:hover { background:#4a4a4a; }")
        add_cat_btn = QtWidgets.QPushButton("+ 添加分类")
        add_cat_btn.setStyleSheet(cat_btn_style)
        add_cat_btn.clicked.connect(self._on_add_category)
        cat_btn_row.addWidget(add_cat_btn)
        rm_cat_btn = QtWidgets.QPushButton("- 删除选中")
        rm_cat_btn.setStyleSheet(cat_btn_style)
        rm_cat_btn.clicked.connect(self._on_remove_category)
        cat_btn_row.addWidget(rm_cat_btn)
        cat_btn_row.addStretch()
        right.addLayout(cat_btn_row)

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

    def _on_sub_lib_changed(self, row):
        """切换子库时保存当前编辑并加载新子库数据"""
        if row < 0:
            return
        # 保存当前
        if hasattr(self, '_sub_lib_list') and self._sub_lib_list.count() > 0:
            prev_row = self._sub_lib_list.property("_prev_row")
            if prev_row is not None and 0 <= prev_row < self._sub_lib_list.count():
                prev_key = list(self._subs_data.keys())[prev_row]
                cats_lines = self._sub_cats_edit.toPlainText().strip()
                self._subs_data[prev_key]["categories"] = [
                    l.strip().split(None, 1) for l in cats_lines.split("\n") if l.strip()
                ]
        self._sub_lib_list.setProperty("_prev_row", row)
        # 加载新
        key = list(self._subs_data.keys())[row]
        data = self._subs_data[key]
        self._sub_id_edit.setText(key)
        self._sub_name_edit.setText(data["display"])
        cats_lines = "\n".join(f"{c[0]} {c[1]}" for c in data["categories"] if len(c) >= 2)
        self._sub_cats_edit.setPlainText(cats_lines)

    def _on_add_category(self):
        """添加顶级分类到当前子库"""
        cat_id, ok = QtWidgets.QInputDialog.getText(
            self, "添加分类", "分类ID（英文，如 metal）:")
        if not ok or not cat_id.strip():
            return
        cat_name, ok = QtWidgets.QInputDialog.getText(
            self, "添加分类", f"分类名称（中文，如 金属）:")
        if not ok or not cat_name.strip():
            return
        text = self._sub_cats_edit.toPlainText()
        if text.strip():
            text += "\n"
        text += f"{cat_id.strip()} {cat_name.strip()}"
        self._sub_cats_edit.setPlainText(text)

    def _on_remove_category(self):
        """删除选中的顶级分类"""
        cursor = self._sub_cats_edit.textCursor()
        if cursor.hasSelection():
            # 删除选中文本
            cursor.removeSelectedText()
        else:
            # 删除光标所在行
            cursor.select(QtWidgets.QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # 删除换行

    # ── 高级配置标签页 ────────────────────────────

    _ADV_KEYS = [
        ("geometry_extensions", "几何体扩展名（导入时识别几何体文件）"),
        ("image_extensions", "图像/贴图扩展名（导入时识别贴图文件）"),
        ("material_node_types", "材质节点类型（导出时识别材质节点）"),
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

        tip = QtWidgets.QLabel("每行一个，修改后请点击「应用」")
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
        default_name = self._settings.get("default_library", "")
        self._lib_data = libs
        default_idx = 0
        for i, lib in enumerate(libs):
            name = lib.get("name", os.path.basename(lib["path"]))
            path = lib["path"]
            suffix = "  [默认]" if name == default_name or i == 0 else ""
            self._lib_list.addItem(f"{name} — {path}{suffix}")
            if name == default_name:
                default_idx = i
        if self._lib_list.count() > 0:
            self._lib_list.setCurrentRow(default_idx)

    def _on_add_library(self):
        """添加新资产库"""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择资产库目录", os.path.expanduser("~"))
        if not path:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "资产库名称", "请输入该资产库的显示名称:", text=os.path.basename(path))
        if not ok or not name.strip():
            name = os.path.basename(path)
        self._lib_data.append({"name": name.strip(), "path": path})
        self._populate_library_list()

    def _on_create_library(self):
        """创建新的标准资产库"""
        # 选择父目录
        parent_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择资产库创建位置", os.path.expanduser("~"))
        if not parent_dir:
            return

        # 输入资产库名称
        default_name = "SquirrelLib"
        name, ok = QtWidgets.QInputDialog.getText(
            self, "创建资产库", "请输入资产库名称:", text=default_name)
        if not ok or not name.strip():
            name = default_name

        lib_path = os.path.join(parent_dir, name.strip())
        if os.path.exists(lib_path):
            QtWidgets.QMessageBox.warning(
                self, "目录已存在",
                f"目录已存在: {lib_path}\n请选择其他位置或使用其他名称。")
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
                self, "创建成功",
                f"资产库已创建: {lib_path}")

            # 添加到库列表
            self._lib_data.append({"name": name.strip(), "path": lib_path})
            self._populate_library_list()

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "创建失败",
                f"创建资产库失败: {e}")

    def _on_remove_library(self):
        """删除选中的库（至少保留1个）"""
        row = self._lib_list.currentRow()
        if row < 0:
            return
        if len(self._lib_data) <= 1:
            QtWidgets.QMessageBox.warning(self, "无法删除", "至少保留一个资产库。")
            return
        del self._lib_data[row]
        self._populate_library_list()

    def _on_set_default_library(self):
        """设为默认库"""
        row = self._lib_list.currentRow()
        if row < 0:
            return
        name = self._lib_data[row]["name"]
        self._settings["default_library"] = name
        self._populate_library_list()

    def _on_settings_help(self):
        """打开设置帮助"""
        import webbrowser
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = os.path.join(plugin_root, "Assets", "help", "help_settings.html")
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[Help] 设置帮助文件未找到:", help_path)

    def _on_restore_defaults(self):
        reply = QtWidgets.QMessageBox.question(
            self, "恢复默认", "确定要恢复所有设置到默认值吗？（config.json 将被重置）",
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
                atype = self._EXPORT_ASSET_TYPES[cur_row]
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
            atype = self._EXPORT_ASSET_TYPES[cur_row]
            entry = self._export_preset_data.setdefault(atype, {})
            for key, cb in self._export_cbs.items():
                entry[key] = cb.isChecked()
        self._save_export_preset()

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
            cats_lines = self._sub_cats_edit.toPlainText().strip()
            self._subs_data[key]["categories"] = [
                l.strip().split(None, 1) for l in cats_lines.split("\n") if l.strip()
            ]
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
                self, "⚠ 配置变更确认",
                "当前为解锁状态，非通用配置可能已被修改！\n\n"
                "错误的配置可能导致资产库导入导出功能异常。\n"
                "请确认你清楚每一项配置的含义。\n\n"
                "是否仍要应用当前设置？",
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
        }
        if hasattr(self, '_lib_data') and self._lib_data:
            settings["library_paths"] = list(self._lib_data)
            settings["default_library"] = self._settings.get("default_library", "")
        self.settingsChanged.emit(settings)

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
