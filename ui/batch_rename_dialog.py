"""
批量重命名对话框。
支持三种模式：前缀追加、后缀追加、查找替换。
实时预览重命名前后的名称变化。
"""

import os
from ..utils.maya_utils import get_qt_modules
from ..utils.settings import SettingsManager, apply_font_size_to_widget

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class BatchRenameDialog(QtWidgets.QDialog):
    """批量重命名对话框"""

    def __init__(self, parent=None, materials=None):
        """
        Args:
            materials: list[dict] — 选中的材质列表（含 name / name_cn）
        """
        super(BatchRenameDialog, self).__init__(parent)
        self._materials = list(materials) if materials else []
        self._rename_target = "name_cn"  # "name_cn" | "name"

        self.setWindowTitle(t("dialog.batch_rename.title", n=len(self._materials)))
        self.setFixedSize(520, 460)
        self.setStyleSheet("background-color: #2a2a2a;")

        self._setup_ui()
        self._update_preview()
        
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── 重命名目标选择 ──
        target_layout = QtWidgets.QHBoxLayout()
        target_layout.addWidget(self._make_label(t("batch_rename.rename_label")))
        self._target_combo = QtWidgets.QComboBox()
        self._target_combo.addItem(t("batch_rename.display_name") + " (name_cn)", "name_cn")
        self._target_combo.addItem(t("batch_rename.file_name") + " (name)", "name")
        self._target_combo.setStyleSheet(self._COMBO_STYLE)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        target_layout.addWidget(self._target_combo)
        target_layout.addStretch()
        layout.addLayout(target_layout)

        # ── 模式选择（RadioButton）──
        self._mode_group = QtWidgets.QButtonGroup(self)
        radio_style = "QRadioButton { color: #d0d0d0; font-size: 13px; spacing: 6px; }"

        self._rb_prefix = QtWidgets.QRadioButton(t("batch_rename.prefix_append"))
        self._rb_prefix.setStyleSheet(radio_style)
        self._rb_prefix.toggled.connect(self._on_mode_changed)

        self._rb_suffix = QtWidgets.QRadioButton(t("batch_rename.suffix_append"))
        self._rb_suffix.setStyleSheet(radio_style)

        self._rb_replace = QtWidgets.QRadioButton(t("batch_rename.find_replace"))
        self._rb_replace.setStyleSheet(radio_style)

        self._mode_group.addButton(self._rb_prefix, 0)
        self._mode_group.addButton(self._rb_suffix, 1)
        self._mode_group.addButton(self._rb_replace, 2)
        self._rb_prefix.setChecked(True)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.setSpacing(20)
        mode_layout.addWidget(self._rb_prefix)
        mode_layout.addWidget(self._rb_suffix)
        mode_layout.addWidget(self._rb_replace)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        # ── 参数输入区域 ──
        self._input_widget = QtWidgets.QWidget()
        input_layout = QtWidgets.QHBoxLayout(self._input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)

        # 前缀/后缀模式 - 单输入框
        self._single_input = QtWidgets.QLineEdit()
        self._single_input.setPlaceholderText(t("batch_rename.input_placeholder"))
        self._single_input.setStyleSheet(self._INPUT_STYLE)
        self._single_input.textChanged.connect(self._update_preview)
        input_layout.addWidget(self._single_input)

        # 查找替换模式 - 双输入框
        self._replace_widget = QtWidgets.QWidget()
        rp_layout = QtWidgets.QHBoxLayout(self._replace_widget)
        rp_layout.setContentsMargins(0, 0, 0, 0)

        self._find_input = QtWidgets.QLineEdit()
        self._find_input.setPlaceholderText(t("batch_rename.find_placeholder"))
        self._find_input.setStyleSheet(self._INPUT_STYLE)
        self._find_input.textChanged.connect(self._update_preview)
        rp_layout.addWidget(self._find_input)

        rp_layout.addWidget(QtWidgets.QLabel("→"))
        rp_layout.itemAt(1).widget().setStyleSheet("color: #909090; font-size: 14px;")

        self._replace_input = QtWidgets.QLineEdit()
        self._replace_input.setPlaceholderText(t("batch_rename.replace_placeholder"))
        self._replace_input.setStyleSheet(self._INPUT_STYLE)
        self._replace_input.textChanged.connect(self._update_preview)
        rp_layout.addWidget(self._replace_input)

        self._replace_widget.setVisible(False)
        input_layout.addWidget(self._replace_widget)

        layout.addWidget(self._input_widget)

        # ── 预览列表 ──
        layout.addWidget(self._make_label(t("batch_rename.preview_label")))
        self._preview_list = QtWidgets.QListWidget()
        self._preview_list.setStyleSheet("""
            QListWidget {
                background-color: #1a1a1a; color: #d0d0d0;
                border: 1px solid #3a3a3a; border-radius: 4px;
                font-size: 12px;
            }
            QListWidget::item { padding: 4px 8px; }
            QListWidget::item:odd { background-color: #222222; }
        """)
        self._preview_list.setMaximumHeight(200)
        layout.addWidget(self._preview_list)

        # ── 按钮 ──
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        cancel_btn.setStyleSheet(self._BTN_STYLE)
        cancel_btn.clicked.connect(self.reject)

        self._ok_btn = QtWidgets.QPushButton(t("batch_rename.action"))
        self._ok_btn.setStyleSheet(self._OK_BTN_STYLE)
        self._ok_btn.clicked.connect(self.accept)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self._ok_btn)
        layout.addLayout(btn_layout)

    # ── 样式常量 ──
    _INPUT_STYLE = """
        QLineEdit {
            background-color: #1a1a1a; color: #e0e0e0;
            border: 1px solid #4a4a4a; border-radius: 4px;
            padding: 6px 10px; font-size: 13px;
        }
        QLineEdit:focus { border-color: #5294e2; }
    """

    _COMBO_STYLE = """
        QComboBox {
            background-color: #333; color: #e0e0e0;
            border: 1px solid #4a4a4a; border-radius: 4px;
            padding: 5px 10px; font-size: 13px;
        }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView {
            background-color: #333; color: #e0e0e0;
            selection-background-color: #2a3a5a;
        }
    """

    _BTN_STYLE = """
        QPushButton {
            background-color: #3a3a3a; color: #d0d0d0;
            border: none; padding: 8px 20px;
            font-size: 13px; border-radius: 4px;
        }
        QPushButton:hover { background-color: #4a4a4a; }
    """

    _OK_BTN_STYLE = """
        QPushButton {
            background-color: #5294e2; color: #ffffff;
            border: none; padding: 8px 20px;
            font-size: 13px; border-radius: 4px;
        }
        QPushButton:hover { background-color: #6aa8f0; }
        QPushButton:disabled { background-color: #3a3a3a; color: #666666; }
    """

    def _make_label(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color: #b0b0b0; font-size: 12px;")
        return lbl

    # ── 事件 ──

    def _on_target_changed(self, idx):
        self._rename_target = self._target_combo.itemData(idx)
        self._update_preview()

    def _on_mode_changed(self):
        mode = self._mode_group.checkedId()
        is_replace = mode == 2
        self._single_input.setVisible(not is_replace)
        self._replace_widget.setVisible(is_replace)
        self._update_preview()

    # ── 核心逻辑 ──

    def _build_new_name(self, old_name: str) -> str:
        """根据当前模式和输入计算新名称"""
        mode = self._mode_group.checkedId()
        if mode == 0:  # 前缀
            prefix = self._single_input.text()
            return prefix + old_name
        elif mode == 1:  # 后缀
            suffix = self._single_input.text()
            return old_name + suffix
        elif mode == 2:  # 查找替换
            find = self._find_input.text()
            replace = self._replace_input.text()
            return old_name.replace(find, replace) if find else old_name
        return old_name

    def _update_preview(self):
        """刷新预览列表"""
        self._preview_list.clear()
        changed_count = 0
        for m in self._materials:
            old_name = m.get(self._rename_target, "") or m.get("name", "")
            new_name = self._build_new_name(old_name)
            is_changed = new_name != old_name
            if is_changed:
                changed_count += 1
            if old_name == new_name:
                item_text = f"  {old_name}  ({t('batch_rename.unchanged')})"
            else:
                item_text = f"  {old_name}  →  {new_name}"
            item = QtWidgets.QListWidgetItem(item_text)
            if is_changed:
                item.setForeground(QtGui.QColor("#5294e2"))
            else:
                item.setForeground(QtGui.QColor("#888888"))
            self._preview_list.addItem(item)

        self._ok_btn.setText(t("batch_rename.action_with_count", changed=changed_count, total=len(self._materials)))
        self._ok_btn.setEnabled(changed_count > 0)

    def get_rename_results(self):
        """获取重命名结果列表，供调用方执行

        Returns:
            list[tuple]: [(old_name, new_name, material_dict), ...]
        """
        results = []
        for m in self._materials:
            old_name = m.get(self._rename_target, "") or m.get("name", "")
            new_name = self._build_new_name(old_name)
            if new_name != old_name:
                results.append((old_name, new_name, m))
        return results

    def get_rename_target(self):
        """返回重命名字段名: "name_cn" 或 "name" """
        return self._rename_target
