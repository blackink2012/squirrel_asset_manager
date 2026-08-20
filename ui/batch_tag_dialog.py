"""
批量标签管理对话框。
支持两种操作：
  1. 添加标签 — 从常用标签勾选或手动输入
  2. 删除标签 — 点击资产现有标签的 ✖ 标记移除

标签以 pill 按钮形式排列，流式布局节省空间。
"""

from ..utils.maya_utils import get_qt_modules
from ..utils.settings import SettingsManager, apply_font_size_to_widget

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


# ── 流式布局 ──

class FlowLayout(QtWidgets.QLayout):
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
        size = QtCore.QSize()
        for item in self._items: size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())
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


class FlowWidget(QtWidgets.QWidget):
    """自动换行容器"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, margin=0, spacing=3)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self.flow.heightForWidth(w)


# ── Pill 样式 ──

_PILL_NORMAL = """
    QPushButton {
        background:#2a2a2a; color:#a0a0a0;
        border:1px solid #4a4a4a; border-radius:10px;
        padding:2px 10px; font-size:12px;
    }
    QPushButton:hover { background:#3a3a3a; color:#d0d0d0; border-color:#5a5a5a; }
"""

_PILL_SELECTED = """
    QPushButton {
        background:#1a3a5a; color:#5294e2;
        border:1px solid #5294e2; border-radius:10px;
        padding:2px 10px; font-size:12px;
    }
    QPushButton:hover { background:#2a4a6a; }
"""

_PILL_REMOVE = """
    QPushButton {
        background:#3a1a1a; color:#e06060;
        border:1px solid #e06060; border-radius:10px;
        padding:2px 10px; font-size:12px;
    }
    QPushButton:hover { background:#4a2a2a; }
"""



def _make_select_pill(tag, selected=False):
    """可切换选中状态的 pill（添加模式用）"""
    b = QtWidgets.QPushButton(tag)
    b.setCheckable(True)
    b.setChecked(selected)
    b.setStyleSheet(_PILL_SELECTED if selected else _PILL_NORMAL)
    b.toggled.connect(lambda chk: b.setStyleSheet(_PILL_SELECTED if chk else _PILL_NORMAL))
    b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    return b


def _make_remove_pill(tag, toggle_cb, is_pending=False):
    """单个整体 pill：✖ 标签文字，点击切换红色/正常（待删标记）"""
    b = QtWidgets.QPushButton(f"\u2716 {tag}")
    b.setCheckable(True)
    b.setChecked(is_pending)
    b.setStyleSheet(_PILL_REMOVE if is_pending else _PILL_NORMAL)
    b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    b.toggled.connect(lambda chk: (
        b.setStyleSheet(_PILL_REMOVE if chk else _PILL_NORMAL),
        toggle_cb(tag, chk)
    ))
    return b


# ── 主对话框 ──

class BatchTagDialog(QtWidgets.QDialog):
    """批量标签管理对话框"""

    MODE_ADD = 0
    MODE_REMOVE = 1

    def __init__(self, parent=None, materials=None, common_tags=None):
        """
        Args:
            materials: list[dict] — 选中的材质列表（含 tags, name, name_cn）
            common_tags: list[str] — 当前子库的常用标签列表
        """
        super(BatchTagDialog, self).__init__(parent)
        self._materials = list(materials) if materials else []
        self._common_tags = list(common_tags) if common_tags else []

        # 收集资产现有标签（去重、排序）
        self._existing_tags = sorted(set(
            t.strip()
            for m in self._materials
            for t in m.get("tags", [])
            if t.strip()
        ))

        # 删除模式：红色标记待删的标签
        self._pending_remove_tags = set()

        self.setWindowTitle(t("dialog.batch_tag.title", n=len(self._materials)))
        self.setMinimumWidth(420)
        self.setStyleSheet("background-color: #2a2a2a;")
        self._setup_ui()
        
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        apply_font_size_to_widget(self, font_size)

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(14, 14, 14, 14)

        # ── 模式选择 ──
        radio_style = "QRadioButton { color: #d0d0d0; font-size: 13px; spacing: 6px; }"

        self._mode_group = QtWidgets.QButtonGroup(self)

        self._rb_add = QtWidgets.QRadioButton(t("batch_tag.add_tags"))
        self._rb_add.setChecked(True); self._rb_add.setStyleSheet(radio_style)
        self._rb_remove = QtWidgets.QRadioButton(t("batch_tag.remove_tags"))
        self._rb_remove.setStyleSheet(radio_style)

        self._mode_group.addButton(self._rb_add, self.MODE_ADD)
        self._mode_group.addButton(self._rb_remove, self.MODE_REMOVE)

        self._mode_group.buttonToggled.connect(self._on_mode_changed)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.setSpacing(20)
        mode_layout.addWidget(self._rb_add)
        mode_layout.addWidget(self._rb_remove)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        # ══════════════════════════════════════════
        # 添加模式 UI
        # ══════════════════════════════════════════
        self._add_widget = QtWidgets.QWidget()
        add_layout = QtWidgets.QVBoxLayout(self._add_widget)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(6)

        # 手动输入
        self._tag_input = QtWidgets.QLineEdit()
        self._tag_input.setPlaceholderText(t("batch_tag.input_placeholder"))
        self._tag_input.setStyleSheet("QLineEdit { background:#1a1a1a; color:#e0e0e0; border:1px solid #4a4a4a; border-radius:4px; padding:5px 8px; font-size:12px; } QLineEdit:focus { border-color:#5294e2; }")
        self._tag_input.textChanged.connect(self._update_preview)
        add_layout.addWidget(self._tag_input)

        # 常用标签
        add_layout.addWidget(self._make_label(t("batch_tag.common_tags_label")))
        self._common_flow = FlowWidget()
        self._common_flow.setMaximumHeight(120)
        if self._common_tags:
            for tag in self._common_tags:
                self._common_flow.flow.addWidget(_make_select_pill(tag))
        else:
            add_layout.addWidget(self._make_label(t("batch_tag.no_common_tags")))
        add_layout.addWidget(self._common_flow)

        # 常用标签全选/取消
        sel_row = QtWidgets.QHBoxLayout()
        for txt, chk in [(t("common.select_all"), True), (t("btn.deselect_all"), False)]:
            b = QtWidgets.QPushButton(txt)
            b.setStyleSheet("QPushButton { background:#333; color:#b0b0b0; border:none; padding:3px 12px; font-size:11px; border-radius:3px; } QPushButton:hover { background:#4a4a4a; }")
            b.clicked.connect(lambda _, c=chk: self._set_common_all(c))
            sel_row.addWidget(b)
        sel_row.addStretch()
        add_layout.addLayout(sel_row)

        layout.addWidget(self._add_widget)

        # ══════════════════════════════════════════
        # 删除模式 UI
        # ══════════════════════════════════════════
        self._remove_widget = QtWidgets.QWidget()
        remove_layout = QtWidgets.QVBoxLayout(self._remove_widget)
        remove_layout.setContentsMargins(0, 0, 0, 0)
        remove_layout.setSpacing(6)

        remove_layout.addWidget(self._make_label(t("batch_tag.remove_hint")))

        self._existing_flow = FlowWidget()
        self._existing_flow.setMaximumHeight(150)
        if self._existing_tags:
            for tag in self._existing_tags:
                self._existing_flow.flow.addWidget(
                    _make_remove_pill(tag, self._on_toggle_pending))
        else:
            remove_layout.addWidget(self._make_label(t("batch_tag.no_removable_tags")))
        remove_layout.addWidget(self._existing_flow)

        self._remove_widget.setVisible(False)
        layout.addWidget(self._remove_widget)

        # ══════════════════════════════════════════
        # 预览
        # ══════════════════════════════════════════
        layout.addWidget(self._make_label(t("batch_tag.preview_label")))
        self._preview_label = QtWidgets.QLabel()
        self._preview_label.setStyleSheet("background:#1a1a1a; color:#b0b0b0; border:1px solid #3a3a3a; border-radius:4px; padding:8px; font-size:12px;")
        self._preview_label.setWordWrap(True)
        self._preview_label.setMaximumHeight(50)
        layout.addWidget(self._preview_label)
        self._update_preview()

        # ══════════════════════════════════════════
        # 按钮
        # ══════════════════════════════════════════
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        cancel_btn.setStyleSheet("QPushButton { background:#3a3a3a; color:#d0d0d0; border:none; padding:7px 18px; font-size:13px; border-radius:4px; } QPushButton:hover { background:#4a4a4a; }")
        cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QtWidgets.QPushButton(t("common.apply"))
        self._ok_btn.setStyleSheet("QPushButton { background:#5294e2; color:#fff; border:none; padding:7px 18px; font-size:13px; border-radius:4px; } QPushButton:hover { background:#6aa8f0; } QPushButton:disabled { background:#3a3a3a; color:#666; }")
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn); btn_row.addWidget(self._ok_btn)
        layout.addLayout(btn_row)

    # ── 辅助 ──

    def _make_label(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#a0a0a0; font-size:11px;")
        return lbl

    def _rebuild_existing_pills(self):
        """刷新删除标签的 pill 列表"""
        while self._existing_flow.flow.count():
            w = self._existing_flow.flow.takeAt(0).widget()
            if w: w.deleteLater()
        for tag in self._existing_tags:
            is_pending = tag in self._pending_remove_tags
            self._existing_flow.flow.addWidget(
                _make_remove_pill(tag, self._on_toggle_pending, is_pending))
        else:
            lbl = QtWidgets.QLabel(t("batch_tag.all_tags_processed"))
            lbl.setStyleSheet("color:#a0a0a0; font-size:11px; padding:4px 0;")
            self._existing_flow.flow.addWidget(lbl)
        self._update_preview()

    # ── 事件 ──

    def _on_mode_changed(self):
        mode = self._mode_group.checkedId()
        self._add_widget.setVisible(mode == self.MODE_ADD)
        self._remove_widget.setVisible(mode == self.MODE_REMOVE)
        self._update_preview()

    def _on_toggle_pending(self, tag, selected):
        """点击标签文字 → 切换待删标记"""
        if selected:
            self._pending_remove_tags.add(tag)
        else:
            self._pending_remove_tags.discard(tag)
        self._update_preview()

    def _set_common_all(self, checked):
        """常用标签全选/取消"""
        for i in range(self._common_flow.flow.count()):
            w = self._common_flow.flow.itemAt(i).widget()
            if isinstance(w, QtWidgets.QPushButton) and w.isCheckable():
                w.setChecked(checked)
        self._update_preview()

    # ── 辅助获取 ──

    def _get_common_checked(self):
        tags = []
        for i in range(self._common_flow.flow.count()):
            w = self._common_flow.flow.itemAt(i).widget()
            if isinstance(w, QtWidgets.QPushButton) and w.isCheckable() and w.isChecked():
                tags.append(w.text())
        return tags

    def _get_typed_tags(self):
        return [t.strip() for t in self._tag_input.text().split(",") if t.strip()]

    def _update_preview(self):
        mode = self._mode_group.checkedId()
        count = len(self._materials)
        if mode == self.MODE_ADD:
            tags = self._get_typed_tags() + self._get_common_checked()
            self._preview_label.setText(
                t("batch_tag.preview_add_count", count=count, n=len(tags)) if tags
                else t("batch_tag.preview_add_hint", count=count)
            )
        else:
            pending = len(self._pending_remove_tags)
            if pending:
                self._preview_label.setText(
                    t("batch_tag.preview_remove_count", count=count, n=pending))
            else:
                self._preview_label.setText(
                    t("batch_tag.preview_remove_hint", count=count))

    # ── 获取结果 ──

    def get_tag_operation(self):
        mode = self._mode_group.checkedId()
        if mode == self.MODE_ADD:
            tags = self._get_typed_tags() + self._get_common_checked()
            return {"mode": "add", "tags": tags} if tags else None
        elif mode == self.MODE_REMOVE:
            tags = list(self._pending_remove_tags)
            return {"mode": "remove", "tags": tags} if tags else None
        return None
