# -*- coding: utf-8 -*-
"""
BatchProgressOverlay — 批量导出进度浮动面板

非模态浮动 QFrame，显示当前导出进度，支持取消操作。

用法::

    overlay = BatchProgressOverlay(parent=main_window)
    overlay.show()
    overlay.update_progress(3, 12, "CarPaint_Metallic")
    overlay.cancelled.connect(on_cancel)
"""

from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


class BatchProgressOverlay(QtWidgets.QFrame):
    """批量导出进度浮动面板。

    Signals:
        cancelled:    用户点击「取消批量导出」
        skip_current: 用户点击「跳过当前」
    """

    cancelled = QtCore.Signal()
    skip_current = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total = 0
        self._current = 0
        self._current_asset = ""
        self._cancelled = False

        self.setWindowTitle("批量导出进度")
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setMinimumSize(320, 100)
        self.setStyleSheet(
            "BatchProgressOverlay { background-color: #1e1e1e; border: 1px solid #444; "
            "border-radius: 8px; }"
        )

        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 标题行
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("批量导出")
        title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #666; border: none; font-size: 14px; }"
            "QPushButton:hover { color: #e06060; }"
        )
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # 进度文本
        self._progress_label = QtWidgets.QLabel("准备中...")
        self._progress_label.setStyleSheet("color: #c0c0c0; font-size: 12px;")
        layout.addWidget(self._progress_label)

        # 进度条
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #333; border: 1px solid #444; border-radius: 4px; "
            "height: 18px; text-align: center; color: #e0e0e0; font-size: 11px; }"
            "QProgressBar::chunk { background: #5294e2; border-radius: 3px; }"
        )
        layout.addWidget(self._progress_bar)

        # 按钮行
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        skip_btn = QtWidgets.QPushButton("跳过当前")
        skip_btn.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: #ff9800; border: 1px solid #555; "
            "border-radius: 4px; padding: 4px 12px; font-size: 11px; }"
            "QPushButton:hover { background: #4a3a2a; border-color: #ff9800; }"
        )
        skip_btn.clicked.connect(self.skip_current.emit)
        btn_row.addWidget(skip_btn)

        btn_row.addStretch()

        cancel_btn = QtWidgets.QPushButton("取消批量导出")
        cancel_btn.setStyleSheet(
            "QPushButton { background: #3a1a1a; color: #e06060; border: 1px solid #5a2a2a; "
            "border-radius: 4px; padding: 4px 14px; font-size: 11px; }"
            "QPushButton:hover { background: #5a2a2a; }"
        )
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ── 公共方法 ───────────────────────────────────────

    def update_progress(self, current: int, total: int, asset_name: str = ""):
        """更新进度显示。

        Args:
            current: 当前处理的资产序号 (1-based)
            total: 总资产数
            asset_name: 当前资产名
        """
        self._current = current
        self._total = total
        self._current_asset = asset_name

        pct = int((current / max(total, 1)) * 100)
        self._progress_bar.setValue(min(pct, 100))

        if asset_name:
            self._progress_label.setText(
                f"正在处理: {current}/{total} — {asset_name}"
            )
        else:
            self._progress_label.setText(f"正在处理: {current}/{total}")

    def is_cancelled(self) -> bool:
        """是否已请求取消。"""
        return self._cancelled

    def reset(self):
        """重置状态。"""
        self._cancelled = False
        self._current = 0
        self._total = 0
        self._current_asset = ""
        self._progress_bar.setValue(0)
        self._progress_label.setText("准备中...")

    # ── 定位 ───────────────────────────────────────────

    def position_near(self, widget):
        """将自身定位到目标 widget 的右下方。"""
        if widget and widget.isVisible():
            geo = widget.geometry()
            pos = widget.mapToGlobal(geo.topRight())
            # 偏移到右侧
            self.move(pos.x() + 10, pos.y())
        elif widget is None:
            self.move(100, 100)

    # ── 内部 ───────────────────────────────────────────

    def _on_cancel(self):
        self._cancelled = True
        self._progress_label.setText("正在取消...")
        self._progress_label.setStyleSheet("color: #e06060; font-size: 12px;")
        self.cancelled.emit()
