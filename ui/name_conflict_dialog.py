# -*- coding: utf-8 -*-
"""
NameConflictDialog — 同名资产冲突处理对话框

当导出资产的目标目录已存在时，提供三种处理方式：
  1. 自动重命名（追加 _001, _002 …）
  2. 手动输入新名称
  3. 取消当前操作

支持「记住选择，不再提示」开关，后续同名冲突可直接按
已保存策略处理，无需再次弹窗。
"""

from ..utils.maya_utils import get_qt_modules
from ..utils.settings import SettingsManager, apply_font_size_to_widget

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class NameConflictDialog(QtWidgets.QDialog):
    """同名资产冲突处理对话框"""

    # 策略模式常量
    MODE_PROMPT = "prompt"          # 每次询问
    MODE_AUTO_RENAME = "auto_rename"  # 自动重命名
    MODE_MANUAL = "manual"          # 手动输入

    def __init__(self, asset_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("dialog.name_conflict.title"))
        self.setMinimumWidth(420)
        self.setModal(True)

        self._result_mode = self.MODE_AUTO_RENAME
        self._result_name = ""
        self._result_remember = False

        self._build_ui(asset_name)
        
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

    def _build_ui(self, asset_name: str):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # ── 警告图标 + 信息 ──
        header = QtWidgets.QHBoxLayout()
        icon_lbl = QtWidgets.QLabel("⚠️")
        icon_lbl.setStyleSheet("font-size: 28px;")
        header.addWidget(icon_lbl)

        info_lbl = QtWidgets.QLabel(
            t("dialog.name_conflict.override_warning", name=asset_name)
        )
        info_lbl.setWordWrap(True)
        header.addWidget(info_lbl, 1)
        layout.addLayout(header)

        # ── 分隔线 ──
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("color: #555;")
        layout.addWidget(sep)

        # ── 请选择处理方式 ──
        prompt = QtWidgets.QLabel(t("dialog.name_conflict.choose_method"))
        prompt.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(prompt)

        # ── 自动重命名 Radio ──
        self._auto_radio = QtWidgets.QRadioButton(t("dialog.name_conflict.auto_rename"))
        self._auto_radio.setChecked(True)
        layout.addWidget(self._auto_radio)

        # ── 手动输入 Radio + 输入框 ──
        self._manual_radio = QtWidgets.QRadioButton(t("dialog.name_conflict.manual_name"))

        manual_layout = QtWidgets.QHBoxLayout()
        manual_layout.addSpacing(24)
        manual_layout.addWidget(self._manual_radio)

        self._name_edit = QtWidgets.QLineEdit()
        self._name_edit.setPlaceholderText(t("dialog.name_conflict.new_name_input"))
        self._name_edit.setEnabled(False)
        self._name_edit.setMinimumWidth(180)
        manual_layout.addWidget(self._name_edit, 1)
        layout.addLayout(manual_layout)

        # ── 记住选择 CheckBox ──
        self._remember_cb = QtWidgets.QCheckBox(t("dialog.name_conflict.remember_choice"))
        layout.addWidget(self._remember_cb)

        layout.addStretch()

        # ── 按钮 ──
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        self._ok_btn = QtWidgets.QPushButton(t("common.ok"))
        self._ok_btn.setDefault(True)
        self._cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        btn_layout.addWidget(self._ok_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

        # ── 信号连接 ──
        self._auto_radio.toggled.connect(self._on_mode_changed)
        self._ok_btn.clicked.connect(self._on_accept)
        self._cancel_btn.clicked.connect(self.reject)

    def _on_mode_changed(self, checked: bool):
        """切换自动/手动模式"""
        if self._auto_radio.isChecked():
            self._name_edit.setEnabled(False)
        else:
            self._name_edit.setEnabled(True)
            self._name_edit.setFocus()

    def _on_accept(self):
        """收集结果并关闭"""
        if self._auto_radio.isChecked():
            self._result_mode = self.MODE_AUTO_RENAME
            self._result_name = ""
        else:
            name = self._name_edit.text().strip()
            if not name:
                QtWidgets.QMessageBox.warning(self, t("dialog.name_conflict.input_error"),
                    t("dialog.name_conflict.input_name_prompt"))
                self._name_edit.setFocus()
                return
            self._result_mode = self.MODE_MANUAL
            self._result_name = name

        self._result_remember = self._remember_cb.isChecked()
        self.accept()

    def result(self):
        """返回 (mode, new_name, remember) 元组

        Returns:
            mode: "auto_rename" | "manual"
            new_name: 用户输入的名称（仅 manual 模式有效）
            remember: 是否记住选择
        """
        return self._result_mode, self._result_name, self._result_remember
