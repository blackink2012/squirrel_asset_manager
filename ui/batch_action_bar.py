"""
多选操作栏（BatchActionBar）。
当缩略图网格中有多个资产被选中时，显示在工具栏下方。
提供批量操作的快捷按钮组。
"""

from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class BatchActionBar(QtWidgets.QFrame):
    """多选操作栏 — 选中多个资产时显示"""

    # 信号
    renameRequested = QtCore.Signal(list)       # list[dict] materials
    tagRequested = QtCore.Signal(list)          # list[dict] materials
    moveRequested = QtCore.Signal(list)         # list[dict] materials
    copyRequested = QtCore.Signal(list)         # list[dict] materials
    deleteRequested = QtCore.Signal(list)       # list[dict] materials
    clearSelectionRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super(BatchActionBar, self).__init__(parent)
        self._material_count = 0
        self._font_size = 13
        self.setObjectName("batchActionBar")
        self.setStyleSheet("""
            #batchActionBar {
                background-color: transparent;
            }
        """)
        self.setFixedHeight(36)
        self.setVisible(False)  # 默认隐藏

        self._setup_ui()

    def _setup_ui(self):
        fs = self._font_size
        bar_h = max(32, int(fs * 2.5))
        btn_pad = max(4, int(fs * 0.4))
        self.setFixedHeight(bar_h)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)

        # 选中数量提示
        self._count_label = QtWidgets.QLabel("已选中 N 个资产")
        self._count_label.setStyleSheet(f"color: #5294e2; font-size: {fs}px; font-weight: bold;")
        layout.addWidget(self._count_label)

        # 分隔线
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        sep.setStyleSheet("color: #3a5a7a;")
        sep.setFixedWidth(1)
        layout.addWidget(sep)

        btn_style = f"""
            QPushButton {{
                background-color: #2a3a5a; color: #c0d0f0;
                border: 1px solid #3a5a8a; border-radius: 3px;
                padding: {btn_pad}px {max(10, int(fs * 1))}px; font-size: {fs}px;
            }}
            QPushButton:hover {{ background-color: #3a4a7a; }}
            QPushButton:pressed {{ background-color: #1a2a4a; }}
        """

        # 批量重命名按钮
        self._rename_btn = QtWidgets.QPushButton("批量重命名")
        self._rename_btn.setStyleSheet(btn_style)
        self._rename_btn.clicked.connect(self._on_rename)
        layout.addWidget(self._rename_btn)

        # 批量标签按钮
        self._tag_btn = QtWidgets.QPushButton("批量标签")
        self._tag_btn.setStyleSheet(btn_style)
        self._tag_btn.clicked.connect(self._on_tag)
        layout.addWidget(self._tag_btn)

        # 批量移动按钮
        self._move_btn = QtWidgets.QPushButton("批量移动")
        self._move_btn.setStyleSheet(btn_style)
        self._move_btn.clicked.connect(self._on_move)
        layout.addWidget(self._move_btn)

        # 批量复制按钮
        self._copy_btn = QtWidgets.QPushButton("批量复制")
        self._copy_btn.setStyleSheet(btn_style)
        self._copy_btn.clicked.connect(self._on_copy)
        layout.addWidget(self._copy_btn)

        # 批量删除按钮
        self._delete_btn = QtWidgets.QPushButton("批量删除")
        self._delete_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #5a2a2a; color: #f0a0a0;
                border: 1px solid #7a3a3a; border-radius: 3px;
                padding: {btn_pad}px {max(10, int(fs * 1))}px; font-size: {fs}px;
            }}
            QPushButton:hover {{ background-color: #7a3a3a; }}
            QPushButton:pressed {{ background-color: #4a1a1a; }}
        """)
        self._delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(self._delete_btn)

        # 弹性空间
        layout.addStretch(1)

        # 清除选中按钮
        clear_btn = QtWidgets.QPushButton("✕ 取消选中")
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: #909090;
                border: 1px solid #4a4a4a; border-radius: 3px;
                padding: {btn_pad}px {int(fs * 0.8)}px; font-size: {max(11, fs - 2)}px;
            }}
            QPushButton:hover {{ color: #d0d0d0; border-color: #6a6a6a; }}
        """)
        clear_btn.clicked.connect(self._on_clear_selection)
        layout.addWidget(clear_btn)

    def _materials(self):
        """返回当前选中的材质列表（由外部注入）"""
        return getattr(self, "_selected_materials", [])

    # ── 公共接口 ──

    def show_with_count(self, count: int, materials: list):
        """显示操作栏并设置选中数量"""
        self._material_count = count
        self._selected_materials = materials
        self._count_label.setText(f"已选中 {count} 个资产")

        # 根据选中数量启用/禁用按钮
        has_selection = count > 0
        self._rename_btn.setEnabled(has_selection)
        self._tag_btn.setEnabled(has_selection)
        self._move_btn.setEnabled(has_selection)
        self._copy_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)
        self.raise_()

    def hide_bar(self):
        """隐藏操作栏"""
        self.setVisible(False)
        self._material_count = 0

    def set_font_size(self, font_size):
        """根据全局字体大小调整批量操作栏的尺寸和样式（不销毁重建）"""
        if font_size == self._font_size:
            return
        self._font_size = font_size
        fs = font_size
        bar_h = max(32, int(fs * 2.5))
        btn_pad = max(4, int(fs * 0.4))
        self.setFixedHeight(bar_h)

        # 更新标签
        if hasattr(self, '_count_label'):
            self._count_label.setStyleSheet(f"color: #5294e2; font-size: {fs}px; font-weight: bold;")

        # 更新普通按钮样式
        btn_style = f"""
            QPushButton {{
                background-color: #2a3a5a; color: #c0d0f0;
                border: 1px solid #3a5a8a; border-radius: 3px;
                padding: {btn_pad}px {max(10, int(fs * 1))}px; font-size: {fs}px;
            }}
            QPushButton:hover {{ background-color: #3a4a7a; }}
            QPushButton:pressed {{ background-color: #1a2a4a; }}
        """
        for btn_name in ('_rename_btn', '_tag_btn', '_move_btn', '_copy_btn'):
            btn = getattr(self, btn_name, None)
            if btn:
                btn.setStyleSheet(btn_style)

        # 更新删除按钮样式
        del_btn_style = f"""
            QPushButton {{
                background-color: #5a2a2a; color: #f0a0a0;
                border: 1px solid #7a3a3a; border-radius: 3px;
                padding: {btn_pad}px {max(10, int(fs * 1))}px; font-size: {fs}px;
            }}
            QPushButton:hover {{ background-color: #7a3a3a; }}
            QPushButton:pressed {{ background-color: #4a1a1a; }}
        """
        if hasattr(self, '_delete_btn'):
            self._delete_btn.setStyleSheet(del_btn_style)

        # 更新清除选中按钮样式
        clear_style = f"""
            QPushButton {{
                background-color: transparent; color: #909090;
                border: 1px solid #4a4a4a; border-radius: 3px;
                padding: {btn_pad}px {int(fs * 0.8)}px; font-size: {max(11, fs - 2)}px;
            }}
            QPushButton:hover {{ color: #d0d0d0; border-color: #6a6a6a; }}
        """
        layout = self.layout()
        if layout:
            clear_btn = layout.itemAt(layout.count() - 1)
            if clear_btn and clear_btn.widget():
                clear_btn.widget().setStyleSheet(clear_style)

    # ── 内部事件 ──

    def _on_rename(self):
        mats = getattr(self, "_selected_materials", [])
        if mats:
            self.renameRequested.emit(mats)

    def _on_tag(self):
        mats = getattr(self, "_selected_materials", [])
        if mats:
            self.tagRequested.emit(mats)

    def _on_move(self):
        mats = getattr(self, "_selected_materials", [])
        if mats:
            self.moveRequested.emit(mats)

    def _on_copy(self):
        mats = getattr(self, "_selected_materials", [])
        if mats:
            self.copyRequested.emit(mats)

    def _on_delete(self):
        mats = getattr(self, "_selected_materials", [])
        if mats:
            self.deleteRequested.emit(mats)

    def _on_clear_selection(self):
        self.clearSelectionRequested.emit()
