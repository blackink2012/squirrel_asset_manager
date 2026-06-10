# -*- coding: utf-8 -*-
"""
VariantImportDialog — 变体导入选择对话框

从 .zasset 读取 variants.json，让用户选择版本和 LOD 级别，
返回 (version_id, lod_id) 元组供 import_executor 使用。

用法:
    dlg = VariantImportDialog(zasset_path, parent)
    if dlg.exec() == QtWidgets.QDialog.Accepted:
        version, lod = dlg.result()
        import_variant_geometry(zasset_path, version=version, lod=lod)
"""

import os
from ..utils.maya_utils import get_qt_modules
from ..utils.settings import SettingsManager, apply_font_size_to_widget

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class VariantImportDialog(QtWidgets.QDialog):
    """变体导入选择对话框。

    显示版本列表和对应 LOD 选项，返回用户选择。
    """

    def __init__(self, zasset_path: str, parent=None):
        super().__init__(parent)
        self._zasset_path = zasset_path
        self._variants = {}
        self._selected_version = ""
        self._selected_lod = ""

        self.setWindowTitle("导入变体几何体")
        self.setMinimumWidth(400)
        self.setModal(True)

        self._load_variants()
        self._build_ui()

        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

    def _load_variants(self):
        """加载 variants.json 数据"""
        from core.zasset_io import ZassetIO
        self._variants = ZassetIO.read_variants(self._zasset_path)
        versions = self._variants.get("versions", [])
        if versions:
            default_ver = self._variants.get("default_version") or versions[0]["id"]
            self._selected_version = default_ver
            lods = versions[0].get("lods", [])
            if lods:
                default_lod = self._variants.get("default_lod") or lods[0]["id"]
                self._selected_lod = default_lod

    def _build_ui(self):
        """构建 UI 布局"""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # ── 标题 ──
        asset_name = os.path.splitext(os.path.basename(self._zasset_path))[0]
        title = QtWidgets.QLabel(f"导入资产：{asset_name}")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        versions = self._variants.get("versions", [])
        if not versions:
            label = QtWidgets.QLabel("该资产不包含变体数据")
            label.setStyleSheet("color: #888;")
            layout.addWidget(label)
            # 无变体时禁用确认
            btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
            btn_box.rejected.connect(self.reject)
            layout.addWidget(btn_box)
            return

        # ── 版本选择 ──
        ver_label = QtWidgets.QLabel("版本：")
        ver_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(ver_label)

        self._ver_group = QtWidgets.QButtonGroup(self)
        ver_layout = QtWidgets.QVBoxLayout()
        ver_layout.setSpacing(6)

        for v in versions:
            vid = v.get("id", "")
            vtag = v.get("tag", vid)
            vlabel = v.get("label", vid)
            vnotes = v.get("notes", "")
            vdate = v.get("create_date", "")

            text = f"{vtag} - {vlabel}"
            if vdate:
                text += f"  ({vdate})"
            if vnotes:
                text += f"\n    {vnotes}"

            radio = QtWidgets.QRadioButton(text)
            radio.setProperty("version_id", vid)
            if vid == self._selected_version:
                radio.setChecked(True)
            self._ver_group.addButton(radio)
            ver_layout.addWidget(radio)

        layout.addLayout(ver_layout)

        # ── LOD 选择 ──
        lod_label = QtWidgets.QLabel("LOD 精度：")
        lod_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(lod_label)

        self._lod_group = QtWidgets.QButtonGroup(self)
        self._lod_layout = QtWidgets.QVBoxLayout()
        self._lod_layout.setSpacing(6)

        layout.addLayout(self._lod_layout)

        # ── 按钮 ──
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 初始化 LOD 选项
        self._ver_group.buttonClicked.connect(self._on_version_changed)
        self._rebuild_lod_options(self._selected_version)

    def _rebuild_lod_options(self, version_id: str):
        """根据选中的版本重建 LOD 单选按钮"""
        # 清除现有 LOD 按钮
        for btn in list(self._lod_group.buttons()):
            self._lod_group.removeButton(btn)
            self._lod_layout.removeWidget(btn)
            btn.deleteLater()

        versions = self._variants.get("versions", [])
        ver = None
        for v in versions:
            if v.get("id") == version_id:
                ver = v
                break

        if not ver:
            label = QtWidgets.QLabel("该版本无可用 LOD")
            label.setStyleSheet("color: #888; padding-left: 20px;")
            self._lod_layout.addWidget(label)
            return

        lods = ver.get("lods", [])
        if not lods:
            label = QtWidgets.QLabel("该版本无可用 LOD")
            label.setStyleSheet("color: #888; padding-left: 20px;")
            self._lod_layout.addWidget(label)
            return

        first_lod_id = None
        for l in lods:
            lid = l.get("id", "")
            llabel = l.get("label", lid)
            stats = l.get("stats", {})
            formats_list = l.get("formats", [])

            text = f"{llabel} ({lid.upper()})"
            if stats:
                tris = stats.get("triangles", 0)
                verts = stats.get("vertices", 0)
                text += f"  —  {tris:,}面 / {verts:,}点"
            if formats_list:
                text += f"  [{', '.join(formats_list)}]"

            radio = QtWidgets.QRadioButton(text)
            radio.setProperty("lod_id", lid)
            if first_lod_id is None:
                first_lod_id = lid
            if lid == self._selected_lod:
                radio.setChecked(True)
            self._lod_group.addButton(radio)
            self._lod_layout.addWidget(radio)

        # 如果之前选择的 LOD 不在当前版本中，默认选第一个
        selected_btn = self._lod_group.checkedButton()
        if not selected_btn and first_lod_id:
            self._selected_lod = first_lod_id
            # 选中第一个按钮
            for btn in self._lod_group.buttons():
                if btn.property("lod_id") == first_lod_id:
                    btn.setChecked(True)
                    break

    def _on_version_changed(self, btn):
        """版本切换时刷新 LOD 选项"""
        version_id = btn.property("version_id")
        if version_id:
            self._selected_version = version_id
            default_lod = self._variants.get("default_lod", "")
            # 获取该版本的第一个 LOD
            versions = self._variants.get("versions", [])
            for v in versions:
                if v.get("id") == version_id:
                    lods = v.get("lods", [])
                    if lods:
                        self._selected_lod = default_lod if any(
                            l.get("id") == default_lod for l in lods
                        ) else lods[0]["id"]
                    break
            self._rebuild_lod_options(version_id)

    def _on_accept(self):
        """确认选择"""
        # 获取选中的版本
        ver_btn = self._ver_group.checkedButton()
        if ver_btn:
            self._selected_version = ver_btn.property("version_id")

        # 获取选中的 LOD
        lod_btn = self._lod_group.checkedButton()
        if lod_btn:
            self._selected_lod = lod_btn.property("lod_id")

        self.accept()

    def result(self) -> tuple:
        """返回用户选择的结果。

        Returns:
            (version_id, lod_id) 如 ("v1", "lod0")
            无变体时返回 (None, None)
        """
        versions = self._variants.get("versions", [])
        if not versions:
            return (None, None)

        ver = self._selected_version
        lod = self._selected_lod

        # 兜底
        if not ver and versions:
            ver = versions[0].get("id", "")
        if not lod and ver:
            for v in versions:
                if v.get("id") == ver:
                    lods = v.get("lods", [])
                    if lods:
                        lod = lods[0].get("id", "")
                    break

        return (ver, lod)
