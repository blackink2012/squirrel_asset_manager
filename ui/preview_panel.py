# -*- coding: utf-8 -*-
"""材质属性面板 — 显示模式 + 内联编辑 + 3D 预览"""

import os
from ..utils.maya_utils import get_qt_modules

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()
try:
    from PySide6 import QtMultimedia, QtMultimediaWidgets
except ImportError:
    from PySide2 import QtMultimedia, QtMultimediaWidgets


# ── 缩略图辅助（多缩略图：主图 thumb.* + 附加 thumb_N.*） ──

_THUMB_EXTENSIONS = ('.sicon', '.aicon', '.png', '.mp4')


def _thumb_sort_key(name: str):
    """主图（thumb.sicon/aicon/png/mp4）排最前，附加 thumb_N.* 按编号排序"""
    low = name.lower()
    if low in ('thumb.sicon', 'thumb.aicon', 'thumb.png', 'thumb.mp4'):
        return (0, 0)
    import re as _re
    m = _re.search(r'thumb_(\d+)', low)
    return (1, int(m.group(1)) if m else 1)


def list_asset_thumbnails(asset_path, base_name=None) -> list:
    """列出资产目录内缩略图文件名（主图在前，附加按编号）。

    zasset（base_name=None）：主图 thumb.sicon/aicon/png/mp4 + 附加 thumb_N.*
    文件夹资产（base_name=资产名）：主图 {name}.sicon + 附加 {name}_N.*
    """
    if not asset_path or not os.path.isdir(asset_path):
        return []
    import re as _re
    low_base = base_name.lower() if base_name else ""
    names = []
    for fn in os.listdir(asset_path):
        low = fn.lower()
        if not os.path.isfile(os.path.join(asset_path, fn)) or not low.endswith(_THUMB_EXTENSIONS):
            continue
        if base_name is not None:
            if low != f"{low_base}.sicon" and not low.startswith(low_base + "_"):
                continue
        elif not low.startswith('thumb'):
            continue
        names.append(fn)

    if base_name is not None:
        def _key(n):
            low = n.lower()
            if low == f"{low_base}.sicon":
                return (0, 0)
            m = _re.search(_re.escape(low_base) + r'_(\d+)', low)
            return (1, int(m.group(1)) if m else 1)
    else:
        _key = _thumb_sort_key
    names.sort(key=_key)
    return names


def next_thumb_slot(asset_path, base_name=None) -> str:
    """返回下一个附加缩略图文件名（thumb_2.sicon / {name}_2.sicon …）"""
    import re as _re
    max_n = 1
    pat = _re.compile(r'thumb_(\d+)') if base_name is None else \
        _re.compile(_re.escape(base_name.lower()) + r'_(\d+)')
    for n in list_asset_thumbnails(asset_path, base_name=base_name):
        m = pat.search(n.lower())
        if m:
            max_n = max(max_n, int(m.group(1)))
    prefix = "thumb" if base_name is None else base_name
    return f"{prefix}_{max_n + 1}.sicon"


# ── FlowLayout（换行布局） ──────────────────────────

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


class _WheelSwitchLabel(QtWidgets.QLabel):
    """预览图标签：滚轮切换缩略图（direction: +1 下一张 / -1 上一张）+ 双击"""
    wheelSwitched = QtCore.Signal(int)
    doubleClicked = QtCore.Signal()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.wheelSwitched.emit(1 if delta > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


# ── 材质属性面板 ────────────────────────────────────

class PreviewPanelWidget(QtWidgets.QWidget):
    tagFilterRequested = QtCore.Signal(list)
    favoriteToggled = QtCore.Signal(str, bool)
    editRequested = QtCore.Signal(dict)
    thumbnailCaptureRequested = QtCore.Signal(str)  # 材质 id → 截图
    thumbnailImportRequested = QtCore.Signal(str)   # 材质 id → 导入
    addThumbnailFileRequested = QtCore.Signal(dict)  # 添加缩略图（导入文件到新槽位）
    thumbnailDeleteRequested = QtCore.Signal(str, int)  # (材质 id, 缩略图索引) 删除附加缩略图
    previewDoubleClicked = QtCore.Signal(dict)  # 双击预览图 → 打开资产观察窗口
    commonTagRequested = QtCore.Signal(str)         # 新增常用标签

    def __init__(self, parent=None):
        super().__init__(parent)
        self._material = None
        self._font_size = 13
        self._common_tags = []
        self._edit_snapshot = {}
        self._gif_movie = None  # GIF 动图引用
        self._mp4_player = None  # QMediaPlayer MP4 播放器
        self._mp4_widget = None  # QVideoWidget
        self._active_filter_tags = set()  # 当前高亮的筛选标签（支持多选）
        self._resolution_cache = {}  # material_id → 分辨率字符串缓存
        self._setup_ui()

    # ── UI 构建 ─────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet("background-color: #252525; border-left: 1px solid #3a3a3a;")
        self.setMinimumWidth(290)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background-color: #3a3a3a; }")
        
        # 用滚动区域包裹属性面板
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self._build_metadata())
        
        splitter.addWidget(scroll)
        splitter.addWidget(self._build_preview())
        splitter.setStretchFactor(0, 0)  # metadata 可收缩
        splitter.setStretchFactor(1, 1)  # 预览优先
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        root.addWidget(splitter, 1)

    def resizeEvent(self, event):
        """响应面板尺寸变化：预览区域保持方形，边长由右栏宽度驱动"""
        super().resizeEvent(event)
        try:
            if not hasattr(self, '_preview_frame') or not hasattr(self, '_preview_label'):
                return
            pw = self.width()
            target_size = pw - 20
            if target_size < 160:
                target_size = 160
            self._preview_frame.setFixedSize(target_size, target_size)
            s = target_size - 16
            if s < 80:
                s = 160
            self._preview_label.setFixedSize(s, s)
            if self._gif_movie:
                self._gif_movie.setScaledSize(QtCore.QSize(s, s))
            if self._mp4_widget:
                self._mp4_widget.setFixedSize(s, s)
            if self._material:
                self._draw_preview(self._material)
            elif not self._gif_movie:
                self._draw_empty_preview(s)
            # 强制更新布局以正确收缩空间
            self.updateGeometry()
        except Exception:
            pass

    def ensure_preview_square(self):
        """外部调用：主动触发预览强制方形，用于 splitterMoved 信号"""
        if not hasattr(self, '_preview_frame') or not hasattr(self, '_preview_label'):
            return
        try:
            s = self._preview_frame.width() - 16
            if s < 80:
                s = 160
            if self._material:
                self._draw_preview(self._material)
            else:
                self._draw_empty_preview(s)
            if self._gif_movie:
                self._gif_movie.setScaledSize(QtCore.QSize(s, s))
            if self._mp4_widget:
                self._mp4_widget.setFixedSize(s, s)
        except Exception:
            pass

    def showEvent(self, event):
        """首次显示时强制预览区域为方形"""
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._ensure_square_on_show)

    def _ensure_square_on_show(self):
        """延迟确保预览区域为方形，等待布局计算完成"""
        if not hasattr(self, '_preview_frame') or not hasattr(self, '_preview_label'):
            return
        try:
            target_size = self.width() - 20
            if target_size < 160:
                target_size = 160
            self._preview_frame.setFixedSize(target_size, target_size)
            QtCore.QTimer.singleShot(10, self._finalize_square)
        except Exception:
            pass

    def _finalize_square(self):
        """最终设置方形预览"""
        try:
            fw = self._preview_frame.width()
            s = fw - 16
            if s < 80:
                s = 160
            self._preview_label.setFixedSize(s, s)
            if self._material:
                self._draw_preview(self._material)
            else:
                self._draw_empty_preview(s)
            self.updateGeometry()
        except Exception:
            pass

    def _build_metadata(self):
        w = QtWidgets.QWidget()
        w.setStyleSheet("background-color: #252525;")
        lyt = QtWidgets.QVBoxLayout(w)
        lyt.setContentsMargins(12, 10, 12, 8); lyt.setSpacing(4)

        # ── 标题行 ──
        hr = QtWidgets.QHBoxLayout(); hr.setSpacing(4)
        lbl = QtWidgets.QLabel(t("preview_panel.attributes"))
        lbl.setStyleSheet("color: #e0e0e0; font-size: 13px; font-weight: bold;")
        hr.addWidget(lbl); hr.addStretch(1)

        self._fav_btn = QtWidgets.QPushButton("☆")
        self._fav_btn.setFixedSize(22, 22); self._fav_btn.setToolTip(t("common.favorite"))
        self._fav_btn.setStyleSheet("QPushButton { background:transparent; color:#606060; border:none; font-size:15px; } QPushButton:hover { color:#FFD700; }")
        self._fav_btn.clicked.connect(self._on_fav); self._fav_btn.setVisible(False)
        hr.addWidget(self._fav_btn)

        self._edit_btn = QtWidgets.QPushButton("✏")
        self._edit_btn.setFixedSize(22, 22); self._edit_btn.setToolTip(t("common.edit"))
        self._edit_btn.setStyleSheet("QPushButton { background:transparent; color:#808080; border:none; font-size:13px; } QPushButton:hover { color:#5294e2; }")
        self._edit_btn.clicked.connect(self._enter_edit)
        hr.addWidget(self._edit_btn)

        self._save_btn = QtWidgets.QPushButton("✔")
        self._save_btn.setFixedSize(22, 22); self._save_btn.setToolTip(t("common.save"))
        self._save_btn.setStyleSheet("QPushButton { background:transparent; color:#5294e2; border:none; font-size:13px; } QPushButton:hover { color:#6ab0ff; }")
        self._save_btn.clicked.connect(self._save); self._save_btn.setVisible(False)
        hr.addWidget(self._save_btn)

        self._cancel_btn = QtWidgets.QPushButton("✖")
        self._cancel_btn.setFixedSize(22, 22); self._cancel_btn.setToolTip(t("common.cancel"))
        self._cancel_btn.setStyleSheet("QPushButton { background:transparent; color:#808080; border:none; font-size:13px; } QPushButton:hover { color:#e06060; }")
        self._cancel_btn.clicked.connect(self._cancel); self._cancel_btn.setVisible(False)
        hr.addWidget(self._cancel_btn)
        lyt.addLayout(hr)

        # ── 显示/编辑栈 ──
        self._stack = QtWidgets.QStackedWidget()
        self._stack.addWidget(self._build_display())
        self._stack.addWidget(self._build_edit())
        lyt.addWidget(self._stack, 1)
        return w

    def _build_display(self):
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(0, 2, 0, 0); l.setSpacing(4)

        self._d_name = QtWidgets.QLabel("-")
        self._d_name.setStyleSheet("color:#d0d0d0; font-size:13px; font-weight:bold;")
        self._d_name.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self._d_name)

        self._d_asset_name = QtWidgets.QLabel("")
        self._d_asset_name.setStyleSheet("color:#707070; font-size:11px;")
        self._d_asset_name.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_asset_name.setVisible(False)
        l.addWidget(self._d_asset_name)

        self._d_type = QtWidgets.QLabel(t("preview_panel.node_type") + ": -")
        self._d_type.setStyleSheet("color:#909090; font-size:11px;")
        self._d_type.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self._d_type)

        self._d_asset = QtWidgets.QLabel(t("preview_panel.asset_type") + ": -")
        self._d_asset.setStyleSheet("color:#909090; font-size:11px;")
        self._d_asset.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self._d_asset)

        self._d_cat = QtWidgets.QLabel(t("common.category") + ": -")
        self._d_cat.setStyleSheet("color:#909090; font-size:11px;")
        self._d_cat.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self._d_cat)

        self._d_filetype = QtWidgets.QLabel(t("preview_panel.format") + ": -")
        self._d_filetype.setStyleSheet("color:#909090; font-size:11px;")
        self._d_filetype.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self._d_filetype)

        self._d_ani = QtWidgets.QLabel("")
        self._d_ani.setStyleSheet("color:#e0a030; font-size:11px;")
        self._d_ani.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_ani.setVisible(False)
        l.addWidget(self._d_ani)

        self._d_filesize = QtWidgets.QLabel(t("preview_panel.size") + ": -")
        self._d_filesize.setStyleSheet("color:#909090; font-size:11px;")
        self._d_filesize.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(self._d_filesize)

        self._d_resolution = QtWidgets.QLabel("")
        self._d_resolution.setStyleSheet("color:#909090; font-size:11px;")
        self._d_resolution.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_resolution.setVisible(False)
        l.addWidget(self._d_resolution)

        # ── 材质文件元信息 ──
        self._d_software = QtWidgets.QLabel("")
        self._d_software.setStyleSheet("color:#707070; font-size:10px;")
        self._d_software.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_software.setVisible(False)
        l.addWidget(self._d_software)

        self._d_renderer = QtWidgets.QLabel("")
        self._d_renderer.setStyleSheet("color:#707070; font-size:10px;")
        self._d_renderer.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_renderer.setVisible(False)
        l.addWidget(self._d_renderer)

        self._d_colorspace = QtWidgets.QLabel("")
        self._d_colorspace.setStyleSheet("color:#707070; font-size:10px;")
        self._d_colorspace.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_colorspace.setVisible(False)
        l.addWidget(self._d_colorspace)

        self._d_export = QtWidgets.QLabel("")
        self._d_export.setStyleSheet("color:#707070; font-size:10px;")
        self._d_export.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_export.setVisible(False)
        l.addWidget(self._d_export)

        sep = QtWidgets.QFrame(); sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet("color:#3a3a3a; margin:2px 0;")
        l.addWidget(sep)

        l.addWidget(QtWidgets.QLabel(t("common.tag"))); l.itemAt(l.count()-1).widget().setStyleSheet("color:#808080; font-size:11px;")

        self._d_tags = FlowWidget()
        l.addWidget(self._d_tags)

        self._d_notes = QtWidgets.QLabel("")
        self._d_notes.setStyleSheet("color:#888888; font-size:11px; padding-top:4px;")
        self._d_notes.setWordWrap(True)
        self._d_notes.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._d_notes.setVisible(False)
        l.addWidget(self._d_notes)

        l.addStretch()
        return w

    def _build_edit(self):
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(0, 2, 0, 0); l.setSpacing(4)

        s = "background:#333; border:1px solid #4a4a4a; border-radius:3px; padding:4px 6px; color:#e0e0e0; font-size:12px;"
        self._e_name = QtWidgets.QLineEdit(); self._e_name.setStyleSheet(s)
        l.addWidget(QtWidgets.QLabel(t("common.name"))); l.addWidget(self._e_name)

        self._e_cat = QtWidgets.QComboBox()
        self._e_cat.setStyleSheet("QComboBox { background:#333; border:1px solid #4a4a4a; border-radius:3px; padding:3px 5px; color:#e0e0e0; font-size:12px; } QComboBox::drop-down { border:none; } QComboBox QAbstractItemView { background:#333; color:#e0e0e0; font-size:12px; }")
        l.addWidget(QtWidgets.QLabel(t("common.category"))); l.addWidget(self._e_cat)

        l.addWidget(QtWidgets.QLabel(t("common.tag")))
        self._e_tags = FlowWidget()
        l.addWidget(self._e_tags)

        add = QtWidgets.QPushButton(t("preview_panel.add_tag"))
        add.setStyleSheet("QPushButton { background:transparent; color:#5294e2; border:none; font-size:11px; } QPushButton:hover { color:#6ab0ff; }")
        add.clicked.connect(self._add_tag)
        l.addWidget(add)

        l.addWidget(QtWidgets.QLabel(t("preview_panel.common_tags")))
        self._e_common = FlowWidget()
        l.addWidget(self._e_common)
        l.addStretch()
        return w

    def _build_preview(self):
        w = QtWidgets.QWidget()
        w.setStyleSheet("background-color:#222; border-top:1px solid #3a3a3a;")
        w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Maximum)
        l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(10, 8, 10, 10); l.setSpacing(6)

        hr = QtWidgets.QHBoxLayout()
        h = QtWidgets.QLabel(t("preview_panel.preview")); h.setStyleSheet("color:#e0e0e0; font-size:13px; font-weight:bold;")
        hr.addWidget(h); hr.addStretch()
        l.addLayout(hr)

        self._preview_frame = QtWidgets.QFrame()
        self._preview_frame.setStyleSheet("QFrame { background:#1a1a1a; }")
        self._preview_frame.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
        fl = QtWidgets.QVBoxLayout(self._preview_frame); fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(0)
        self._preview_label = _WheelSwitchLabel()
        self._preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(160, 160)
        self._preview_label.wheelSwitched.connect(self._on_preview_wheel)
        self._preview_label.doubleClicked.connect(self._on_preview_double_clicked)
        fl.addWidget(self._preview_label)
        l.addWidget(self._preview_frame, 0)

        # 缩略图操作按钮行（截取 / 导入 / 添加 + 多图切换 ◀ n/n ▶ 删除）
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(6)
        btn_s = "QPushButton { background:#3a3a3a; color:#d0d0d0; border:none; padding:5px 12px; font-size:12px; border-radius:4px; } QPushButton:hover { background:#4a4a4a; }"
        cap_btn = QtWidgets.QPushButton(t("preview_panel.capture"))
        cap_btn.setStyleSheet(btn_s)
        cap_btn.clicked.connect(self._on_thumbnail_capture)
        btn_row.addWidget(cap_btn)
        imp_btn = QtWidgets.QPushButton(t("common.import"))
        imp_btn.setStyleSheet(btn_s)
        imp_btn.clicked.connect(self._on_thumbnail_import)
        btn_row.addWidget(imp_btn)
        add_btn = QtWidgets.QPushButton(t("preview_panel.add_thumb"))
        add_btn.setStyleSheet(btn_s)
        add_btn.clicked.connect(self._on_thumbnail_add_file)
        btn_row.addWidget(add_btn)
        # 多缩略图切换控件（◀ 计数 ▶ 删除，仅有多图时显示）
        nav_s = "QPushButton { background:#2a2a2a; color:#c8c8c8; border:1px solid #3a3a3a; padding:3px 10px; font-size:12px; border-radius:4px; } QPushButton:hover { background:#3a3a3a; } QPushButton:disabled { color:#666; }"
        self._thumb_prev_btn = QtWidgets.QPushButton("◀")
        self._thumb_prev_btn.setStyleSheet(nav_s)
        self._thumb_prev_btn.setFixedWidth(34)
        self._thumb_prev_btn.clicked.connect(self._on_thumb_prev)
        self._thumb_counter = QtWidgets.QLabel("1/1")
        self._thumb_counter.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._thumb_counter.setStyleSheet("color:#9a9a9a; font-size:12px;")
        self._thumb_next_btn = QtWidgets.QPushButton("▶")
        self._thumb_next_btn.setStyleSheet(nav_s)
        self._thumb_next_btn.setFixedWidth(34)
        self._thumb_next_btn.clicked.connect(self._on_thumb_next)
        self._thumb_delete_btn = QtWidgets.QPushButton(t("preview_panel.delete_thumb"))
        self._thumb_delete_btn.setStyleSheet(nav_s)
        self._thumb_delete_btn.clicked.connect(self._on_thumb_delete)
        self._thumb_nav_widget = QtWidgets.QWidget()
        nav_row = QtWidgets.QHBoxLayout(self._thumb_nav_widget)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(6)
        nav_row.addWidget(self._thumb_prev_btn)
        nav_row.addWidget(self._thumb_counter, 1)
        nav_row.addWidget(self._thumb_next_btn)
        nav_row.addWidget(self._thumb_delete_btn)
        self._thumb_nav_widget.setVisible(False)
        btn_row.addWidget(self._thumb_nav_widget)
        btn_row.addStretch()
        l.addLayout(btn_row)
        self._thumb_btns = [cap_btn, imp_btn, add_btn]
        self._thumb_names = []
        self._thumb_index = 0

        self._show_empty_preview()
        return w

    # ── 显示模式 ────────────────────────────────────

    def show_material(self, material):
        self._active_filter_tags.clear()
        if not material:
            self._material = None
            self._d_name.setText("-"); self._d_asset_name.setText("")
            self._d_type.setText(t("preview_panel.node_type") + ": -")
            self._d_asset.setText(t("preview_panel.asset_type") + ": -")
            self._d_cat.setText(t("common.category") + ": -")
            self._d_filetype.setText(t("preview_panel.format") + ": -")
            self._d_filesize.setText(t("preview_panel.size") + ": -")
            self._d_resolution.setText("")
            for lb in [self._d_software, self._d_renderer, self._d_colorspace, self._d_export]:
                lb.setText("")
            self._rebuild_display_tags([])
            self._d_notes.setVisible(False)
            self._show_empty_preview()
            self._fav_btn.setVisible(False)
            return
        self._material = material
        self._d_name.setText(material.get("name_cn", "-"))
        asset_name = material.get("name", "")
        if asset_name:
            self._d_asset_name.setText(t("preview_panel.asset_name_value", value=asset_name))
            self._d_asset_name.show()
        else:
            self._d_asset_name.hide()
        self._d_type.setText(t("preview_panel.node_type_value", value=material.get('node_type') or '-'))
        self._d_asset.setText(t("preview_panel.asset_type_value", value=material.get('_asset_type', '-')))
        self._d_cat.setText(t("preview_panel.category_value", value=material.get('_category_display') or material.get('category', '-')))

        # 文件类型和大小
        self._set_file_info(material)

        res = self._get_texture_resolution(material)
        if res:
            self._d_resolution.setText(t("preview_panel.resolution_value", value=res))
            self._d_resolution.show()
        else:
            self._d_resolution.hide()

        sw = material.get('software')
        if sw:
            self._d_software.setText(t("preview_panel.software_value", value=sw))
            self._d_software.show()
        else:
            self._d_software.hide()
        if material.get('renderer'):
            self._d_renderer.setText(t("preview_panel.renderer_value", value=material.get('renderer')))
            self._d_renderer.show()
        else:
            self._d_renderer.hide()
        cs = material.get("color_space")
        if cs:
            self._d_colorspace.setText(t("preview_panel.color_space_value", value=cs))
            self._d_colorspace.show()
        else:
            self._d_colorspace.hide()
        ed = material.get("create_date") or material.get("export_date", "")
        if ed:
            self._d_export.setText(t("preview_panel.create_time_value", value=ed))
            self._d_export.show()
        else:
            self._d_export.hide()
        self._rebuild_display_tags(material.get("tags", []))
        notes = material.get("notes", "")
        if notes:
            self._d_notes.setText(t("preview_panel.notes_value", value=notes))
            self._d_notes.setVisible(True)
        else:
            self._d_notes.setVisible(False)
        self._update_fav_btn()
        self._load_thumb_list(material)
        self._draw_preview(material, clear_events=True)
        self._update_thumb_buttons()

    def _update_thumb_buttons(self):
        """有选中材质时启用截取/导入/添加按钮"""
        enabled = self._material is not None
        for b in self._thumb_btns:
            b.setEnabled(enabled)
        self._update_thumb_nav()

    def _on_thumbnail_capture(self):
        if self._material:
            self.thumbnailCaptureRequested.emit(self._material.get("id", ""))

    def _on_thumbnail_import(self):
        if self._material:
            self.thumbnailImportRequested.emit(self._material.get("id", ""))

    def _on_thumbnail_add_file(self):
        if self._material:
            self.addThumbnailFileRequested.emit(self._material)

    # ── 多缩略图：列表加载 / 切换 / 删除 ──────────────

    def _load_thumb_list(self, material):
        """加载当前资产的缩略图列表，重置到第 1 张"""
        self._thumb_names = []
        self._thumb_index = 0
        json_path = material.get("json_path", "")
        if json_path and not os.path.isdir(json_path):
            json_path = os.path.dirname(json_path)
        if json_path and os.path.isdir(json_path):
            if material.get("is_zasset"):
                self._thumb_names = list_asset_thumbnails(json_path)
            else:
                self._thumb_names = list_asset_thumbnails(json_path, base_name=material.get("name", ""))
        self._update_thumb_nav()

    def _current_thumb_bytes(self, mat):
        """返回当前缩略图索引对应的图片字节"""
        names = getattr(self, '_thumb_names', [])
        idx = getattr(self, '_thumb_index', 0)
        if names and 0 <= idx < len(names):
            if idx == 0 and mat.get("thumb_bytes"):
                return mat.get("thumb_bytes")
            json_path = mat.get("json_path", "")
            if json_path and not os.path.isdir(json_path):
                json_path = os.path.dirname(json_path)
            try:
                with open(os.path.join(json_path, names[idx]), 'rb') as f:
                    return f.read()
            except Exception:
                return None
        return mat.get("thumb_bytes")

    def _update_thumb_nav(self):
        """根据缩略图数量与当前索引刷新切换行（仅多图时显示）"""
        names = getattr(self, '_thumb_names', [])
        idx = getattr(self, '_thumb_index', 0)
        n = len(names)
        self._thumb_nav_widget.setVisible(bool(self._material) and n > 1)
        self._thumb_counter.setText(f"{idx + 1}/{n}" if n else "")
        self._thumb_prev_btn.setEnabled(idx > 0)
        self._thumb_next_btn.setEnabled(idx < n - 1)
        self._thumb_delete_btn.setEnabled(n > 1 and 0 < idx < n)

    def reload_thumbnails(self, target_index=0):
        """添加/删除缩略图后重新扫描并刷新显示。

        target_index 为负数时定位到最后一张（新增缩略图）。
        """
        if self._material:
            self._load_thumb_list(self._material)
            if self._thumb_names:
                if target_index < 0:
                    target_index = len(self._thumb_names) - 1
                self._thumb_index = max(0, min(target_index, len(self._thumb_names) - 1))
            self._draw_preview(self._material, clear_events=True)
            self._update_thumb_buttons()

    def _on_thumb_prev(self):
        if self._thumb_index > 0:
            self._thumb_index -= 1
            self._draw_preview(self._material, clear_events=True)
            self._update_thumb_nav()

    def _on_thumb_next(self):
        if self._thumb_index < len(self._thumb_names) - 1:
            self._thumb_index += 1
            self._draw_preview(self._material, clear_events=True)
            self._update_thumb_nav()

    def _on_preview_wheel(self, direction):
        """预览图区域滚轮 → 切换缩略图（direction>0 上一张，<0 下一张）"""
        if direction > 0:
            self._on_thumb_prev()
        else:
            self._on_thumb_next()

    def _on_preview_double_clicked(self):
        """双击预览图 → 通知主窗口打开资产观察窗口"""
        if self._material:
            self.previewDoubleClicked.emit(self._material)

    def _on_thumb_delete(self):
        if self._material and self._thumb_index > 0 and self._thumb_names:
            self.thumbnailDeleteRequested.emit(self._material.get("id", ""), self._thumb_index)

    def _set_file_info(self, material):
        # 获取 .zasset 文件路径
        json_path = material.get("json_path", "")

        # 如果 dict 不含文件路径，从主窗口的 MaterialManager 查找
        if not json_path and material.get("node_type"):
            w = self.window()
            mgr = getattr(w, '_material_manager', None)
            if mgr:
                mat = mgr.get_by_id(material.get("id", ""))
                if mat and mat.json_path:
                    json_path = mat.json_path

        # 文件格式 — 优先从 meta.json 的 formats/exported_formats 读取
        exported = material.get("exported_formats") or material.get("formats")
        if exported:
            # 过滤掉缩略图格式（sicon/aicon）和内部映射（mcm），zmetal是有效用户格式应保留
            display_formats = [f.upper() for f in exported
                               if f not in ("sicon", "aicon", "mcm")]
            if display_formats:
                self._d_filetype.setText(t("preview_panel.format_value", value=', '.join(display_formats)))
            else:
                # 无任何有效格式 → 未知
                self._d_filetype.setText(t("preview_panel.format_unknown"))

            # 动画格式（ani 字段）
            ani = material.get("ani", [])
            if ani:
                self._d_ani.setText(t("preview_panel.animation_format_value", value=', '.join(ani)))
                self._d_ani.show()
            else:
                self._d_ani.hide()
        else:
            node_path = material.get("node_json_path", "")
            if node_path and not node_path.endswith(".zasset"):
                ext = os.path.splitext(node_path)[1].lstrip(".").upper()
                self._d_filetype.setText(t("preview_panel.format_value", value=ext))
            elif json_path and not json_path.endswith(".zasset"):
                ext = os.path.splitext(json_path)[1].lstrip(".").upper()
                self._d_filetype.setText(t("preview_panel.format_value", value=ext))
            else:
                # .zasset 但 meta.json 中无 formats 字段 → 未知
                self._d_filetype.setText(t("preview_panel.format_unknown"))

        # 文件大小（.zasset 优先，再回退 json_path）
        size = 0
        is_zasset = material.get("is_zasset", False)
        if is_zasset:
            zpath = material.get("zasset_path", "") or json_path
            if zpath and os.path.isdir(zpath):
                for root, dirs, filenames in os.walk(zpath):
                    for fn in filenames:
                        fp = os.path.join(root, fn)
                        try:
                            size += os.path.getsize(fp)
                        except OSError:
                            pass
        elif json_path and os.path.isfile(json_path):
            size += os.path.getsize(json_path)
            thumb = material.get("thumbnail_path", "")
            if thumb and os.path.isfile(thumb):
                size += os.path.getsize(thumb)
        if size > 0:
            self._d_filesize.setText(t("preview_panel.size_value", value=self._fmt_size(size)))
        else:
            self._d_filesize.setText(t("preview_panel.size_dash"))

    @staticmethod
    def _fmt_size(size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024
        return f"{size:.1f} TB"

    def set_font_size(self, font_size):
        self._font_size = font_size
        from ..utils.settings import apply_font_size_to_widget
        apply_font_size_to_widget(self, font_size)
        if self._material:
            self._rebuild_display_tags(self._material.get("tags", []))

    def _rebuild_display_tags(self, tags):
        """差异更新标签pill：只增删变化的，保留相同的"""
        # 获取现有标签
        existing_tags = []
        for i in range(self._d_tags.flow.count()):
            it = self._d_tags.flow.itemAt(i)
            btn = it.widget() if it else None
            if isinstance(btn, QtWidgets.QPushButton):
                existing_tags.append(btn.text())

        new_set = set(tags)
        old_set = set(existing_tags)

        # 如果完全相同，只更新样式即可
        if new_set == old_set:
            self._update_tag_styles()
            return

        # 需要删除的标签
        to_remove = old_set - new_set
        if to_remove:
            for i in range(self._d_tags.flow.count() - 1, -1, -1):
                it = self._d_tags.flow.itemAt(i)
                btn = it.widget() if it else None
                if isinstance(btn, QtWidgets.QPushButton) and btn.text() in to_remove:
                    btn.setParent(None)
                    btn.deleteLater()

        # 需要添加的标签
        to_add = new_set - old_set
        ts = self._font_size
        for t in to_add:
            b = QtWidgets.QPushButton(t)
            active = (t in self._active_filter_tags)
            if active:
                b.setStyleSheet(
                    "QPushButton { background:#2a5a8a; color:#fff; border:1px solid #5294e2; "
                    f"border-radius:8px; padding:2px 8px; font-size:{ts}px; }}"
                    "QPushButton:hover { background:#3a6a9a; }"
                )
            else:
                b.setStyleSheet(
                    "QPushButton { background:#2a3a4a; color:#5294e2; border:1px solid #3a5a7a; "
                    f"border-radius:8px; padding:2px 8px; font-size:{ts}px; }}"
                    "QPushButton:hover { background:#3a4a5a; }"
                    "QPushButton:pressed { background:#1a3a5a; border-color:#5294e2; }"
                )
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, tag=t: self._on_tag_clicked(tag))
            self._d_tags.flow.addWidget(b)

    def _on_tag_clicked(self, tag):
        ctrl_held = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ControlModifier)
        if ctrl_held:
            if tag in self._active_filter_tags:
                self._active_filter_tags.discard(tag)
            else:
                self._active_filter_tags.add(tag)
        else:
            if self._active_filter_tags == {tag}:
                self._active_filter_tags.clear()
            else:
                self._active_filter_tags = {tag}
        tags_list = sorted(self._active_filter_tags)
        self.tagFilterRequested.emit(tags_list)
        # 仅更新标签样式，不重建
        self._update_tag_styles()

    def _update_tag_styles(self):
        """增量更新标签pill的高亮样式，不销毁重建"""
        ts = self._font_size
        for i in range(self._d_tags.flow.count()):
            it = self._d_tags.flow.itemAt(i)
            btn = it.widget() if it else None
            if not isinstance(btn, QtWidgets.QPushButton):
                continue
            tag = btn.text()
            active = (tag in self._active_filter_tags)
            if active:
                btn.setStyleSheet(
                    "QPushButton { background:#2a5a8a; color:#fff; border:1px solid #5294e2; "
                    f"border-radius:8px; padding:2px 8px; font-size:{ts}px; }}"
                    "QPushButton:hover { background:#3a6a9a; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background:#2a3a4a; color:#5294e2; border:1px solid #3a5a7a; "
                    f"border-radius:8px; padding:2px 8px; font-size:{ts}px; }}"
                    "QPushButton:hover { background:#3a4a5a; }"
                    "QPushButton:pressed { background:#1a3a5a; border-color:#5294e2; }"
                )

    def clear_tag_filter(self):
        self._active_filter_tags.clear()
        self._update_tag_styles()

    def _update_fav_btn(self):
        if not self._material: return
        is_fav = self._material.get("_favorited", False)
        self._fav_btn.setText("★" if is_fav else "☆")
        hover_color = "#FFD700" if is_fav else "#909090"
        self._fav_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{'#FFD700' if is_fav else '#606060'}; "
            f"border:none; font-size:15px; }} QPushButton:hover {{ color:{hover_color}; }}")
        self._fav_btn.setVisible(True)

    def _on_fav(self):
        if self._material:
            fav = not self._material.get("_favorited", False)
            self._material["_favorited"] = fav
            self._update_fav_btn()
            mid = self._material.get("id", "")
            if mid: self.favoriteToggled.emit(mid, fav)

    # ── 编辑模式 ────────────────────────────────────

    def _enter_edit(self):
        if not self._material: return
        self._edit_snapshot = {
            "name_cn": self._material.get("name_cn", ""),
            "tags": list(self._material.get("tags", [])),
            "category": self._material.get("category", ""),
        }
        self._e_name.setText(self._material.get("name_cn", ""))
        idx = self._e_cat.findData(self._material.get("category", ""))
        if idx >= 0: self._e_cat.setCurrentIndex(idx)
        self._rebuild_edit_tags()
        self._rebuild_common_tags()
        self._edit_btn.setVisible(False)
        self._fav_btn.setVisible(False)
        self._save_btn.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._stack.setCurrentIndex(1)

    def _save(self):
        if not self._material: return
        self._material["name_cn"] = self._e_name.text().strip()
        idx = self._e_cat.currentIndex()
        if idx >= 0: self._material["category"] = self._e_cat.currentData()
        self._exit_edit()
        self.editRequested.emit(self._material)

    def _cancel(self):
        if self._material and self._edit_snapshot:
            for k, v in self._edit_snapshot.items():
                self._material[k] = v
        self._exit_edit()

    def _exit_edit(self):
        self._edit_btn.setVisible(True)
        self._fav_btn.setVisible(True)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._stack.setCurrentIndex(0)
        # 刷新显示
        if self._material:
            self._d_name.setText(self._material.get("name_cn", "-"))
            self._rebuild_display_tags(self._material.get("tags", []))

    # ── 编辑标签 ────────────────────────────────────

    def _rebuild_edit_tags(self):
        while self._e_tags.flow.count():
            it = self._e_tags.flow.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        if not self._material: return
        for t in self._material.get("tags", []):
            self._add_edit_pill(t)

    def _add_edit_pill(self, tag):
        b = QtWidgets.QPushButton(f"✖ {tag}")
        ts = self._font_size
        b.setStyleSheet(f"QPushButton {{ background:#2a3a4a; color:#5294e2; border:1px solid #3a5a7a; border-radius:10px; padding:1px 7px; font-size:{ts}px; }} QPushButton:hover {{ background:#3a1a1a; color:#e06060; }}")
        b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        def remove(t):
            def h():
                if self._material and t in self._material.get("tags", []):
                    self._material["tags"].remove(t)
                    QtCore.QTimer.singleShot(0, self._rebuild_edit_tags)
                    QtCore.QTimer.singleShot(10, self._rebuild_common_tags)
            return h
        b.clicked.connect(remove(tag))
        self._e_tags.flow.addWidget(b)

    def _rebuild_common_tags(self):
        while self._e_common.flow.count():
            it = self._e_common.flow.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        current = set(self._material.get("tags", [])) if self._material else set()
        for t in self._common_tags:
            if t not in current:
                b = QtWidgets.QPushButton(t)
                ts = self._font_size
                b.setStyleSheet(f"QPushButton {{ background:#333; color:#888; border:1px solid #444; border-radius:8px; padding:1px 6px; font-size:{ts}px; }} QPushButton:hover {{ background:#2d4a6f; color:#5294e2; border-color:#5294e2; }}")
                b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                def add(tag):
                    def h():
                        if self._material:
                            self._material.setdefault("tags", [])
                            if tag not in self._material["tags"]:
                                self._material["tags"].append(tag)
                                self._add_edit_pill(tag)
                                QtCore.QTimer.singleShot(0, self._rebuild_common_tags)
                    return h
                b.clicked.connect(add(t))
                self._e_common.flow.addWidget(b)

    def _add_tag(self):
        t_input, ok = QtWidgets.QInputDialog.getText(self, t("preview_panel.add_tag"), t("preview_panel.new_tag") + ":")
        if ok and t_input.strip():
            tag = t_input.strip()
            if self._material:
                self._material.setdefault("tags", [])
                if tag not in self._material["tags"]:
                    self._material["tags"].append(tag)
                    self._add_edit_pill(tag)
                    self._rebuild_common_tags()
                    self.commonTagRequested.emit(tag)  # 通知主窗口同步到 config

    # ── 外部接口 ────────────────────────────────────

    def set_edit_categories(self, cats):
        self._e_cat.clear()
        def add(nodes, pre=""):
            for n in nodes:
                if n["id"] != "all":
                    self._e_cat.addItem(f"{pre}{n['name_cn']}", n["id"])
                    add(n.get("children", []), pre + "  ")
        add(cats)

    def set_common_tags(self, tags):
        self._common_tags = list(tags)

    # ── 3D 预览 ─────────────────────────────────────

    def _show_empty_preview(self):
        self._thumb_names = []
        self._thumb_index = 0
        self._draw_empty_preview(160)

    def _draw_empty_preview(self, size):
        """按指定尺寸绘制空白预览"""
        self._stop_gif()
        self._preview_label.setFixedSize(size, size)
        p = QtGui.QPixmap(size, size); p.fill(QtGui.QColor("#1a1a1a"))
        painter = QtGui.QPainter(p); painter.setPen(QtGui.QColor(255, 255, 255, 25))
        f = painter.font(); f.setPointSize(max(10, size // 13)); painter.setFont(f)
        painter.drawText(p.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, t("preview_panel.no_material_selected"))
        painter.end(); self._preview_label.setPixmap(p)
        for b in getattr(self, '_thumb_btns', []):
            b.setEnabled(False)
        if hasattr(self, '_update_thumb_nav'):
            self._update_thumb_nav()

    def _draw_preview(self, mat, clear_events=False):
        # 停止旧播放器
        self._stop_media()
        # 仅切换材质时立即重绘，清除上一材质的视频帧（避免同步读 mp4 阻塞期间残留旧画面）
        # resize 路径不调用，防止拖动分割条时事件重入导致抖动
        if clear_events:
            QtWidgets.QApplication.processEvents()

        thumb_path = mat.get("thumbnail_path", "")
        is_zasset = mat.get("is_zasset", False)
        # zasset 恒走字节流；文件夹资产仅在查看附加缩略图（索引>0）时走字节流，避免破坏单图动图播放
        use_thumb_bytes = is_zasset or getattr(self, '_thumb_index', 0) > 0
        thumb_bytes = self._current_thumb_bytes(mat) if use_thumb_bytes else None
        json_path = mat.get("json_path", "")

        s = self._preview_frame.width()
        if s < 80:
            s = 160

        # ⚡ MP4 动图 → QMediaPlayer 播放
        if json_path and os.path.isdir(json_path):
            from ..core.zasset_io import ZassetIO
            mp4_tmp = ZassetIO.read_mp4_to_temp(json_path)
            if mp4_tmp:
                self._play_mp4(mp4_tmp, s)
                return

        # ⚡ .zasset 模式：从 thumb_bytes 加载缩略图
        if is_zasset and thumb_bytes:
            # GIF 动图 → 读首帧为静态（避免 QMovie 崩溃）
            if len(thumb_bytes) >= 3 and thumb_bytes[:3] == b"GIF":
                pix = QtGui.QPixmap()
                pix.loadFromData(thumb_bytes)
                if not pix.isNull():
                    self._preview_label.setFixedSize(s, s)
                    scaled = pix.scaled(s, s, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                        QtCore.Qt.TransformationMode.SmoothTransformation)
                    result = QtGui.QPixmap(s, s)
                    result.fill(QtGui.QColor("#1a1a1a"))
                    painter = QtGui.QPainter(result)
                    x = (s - scaled.width()) // 2
                    y = (s - scaled.height()) // 2
                    painter.drawPixmap(x, y, scaled)
                    painter.end()
                    self._preview_label.setPixmap(result)
                return

            # 静态缩略图 → QPixmap.loadFromData()
            pix = QtGui.QPixmap()
            if pix.loadFromData(thumb_bytes):
                self._preview_label.setFixedSize(s, s)
                scaled = pix.scaled(s, s, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                    QtCore.Qt.TransformationMode.SmoothTransformation)
                result = QtGui.QPixmap(s, s)
                result.fill(QtGui.QColor("#1a1a1a"))
                painter = QtGui.QPainter(result)
                x = (s - scaled.width()) // 2
                y = (s - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                painter.end()
                self._preview_label.setPixmap(result)
                return

        # 旧模式（文件夹资产）：从文件路径加载
        # GIF 动图支持
        if thumb_path.lower().endswith(".aicon") and os.path.isfile(thumb_path):
            self._preview_label.setFixedSize(s, s)
            movie = QtGui.QMovie(thumb_path)
            movie.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
            movie.setScaledSize(QtCore.QSize(s, s))
            self._preview_label.setMovie(movie)
            movie.start()
            self._gif_movie = movie
            return

        if thumb_path and os.path.isfile(thumb_path):
            pix = QtGui.QPixmap(thumb_path)
            if not pix.isNull():
                self._preview_label.setFixedSize(s, s)
                scaled = pix.scaled(s, s, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                    QtCore.Qt.TransformationMode.SmoothTransformation)
                result = QtGui.QPixmap(s, s)
                result.fill(QtGui.QColor("#1a1a1a"))
                painter = QtGui.QPainter(result)
                x = (s - scaled.width()) // 2
                y = (s - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                painter.end()
                self._preview_label.setPixmap(result)
                return

        # 无缩略图 → 绘制 3D 形状
        self._preview_label.setFixedSize(s, s)
        p = QtGui.QPixmap(s, s); p.fill(QtGui.QColor("#1a1a1a"))
        painter = QtGui.QPainter(p); painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        c = QtGui.QColor(mat.get("color", "#606060"))
        cx, cy = s // 2, s // 2 - s // 10; r = int(s * 0.28)
        self._draw_sphere(painter, cx, cy, r, c)
        painter.end(); self._preview_label.setPixmap(p)

    def _get_texture_resolution(self, material):
        """动态获取第一张贴图的分辨率（带缓存）"""
        mat_id = material.get("id", "")
        if mat_id and mat_id in self._resolution_cache:
            return self._resolution_cache[mat_id]

        zasset_path = material.get("zasset_path", "") or material.get("json_path", "")
        if not zasset_path:
            return ""

        textures_dir = os.path.join(zasset_path, "textures")
        if not os.path.isdir(textures_dir):
            return ""

        img_extensions = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".exr", ".hdr"}

        result = ""
        try:
            for fname in os.listdir(textures_dir):
                lower_name = fname.lower()
                ext = os.path.splitext(lower_name)[1]
                if ext in img_extensions:
                    full_path = os.path.join(textures_dir, fname)
                    if os.path.isfile(full_path):
                        try:
                            reader = QtGui.QImageReader(full_path)
                            size = reader.size()
                            if size.isValid():
                                result = f"{size.width()}x{size.height()}"
                                break
                        except Exception:
                            pass
                        if ext in (".exr", ".hdr"):
                            resolution = self._read_hdr_exr_size(full_path)
                            if resolution:
                                result = resolution
                                break
        except Exception:
            pass

        if mat_id and result:
            self._resolution_cache[mat_id] = result
        return result

    def _read_hdr_exr_size(self, filepath):
        """读取 HDR/EXR 文件的尺寸（二进制方式）"""
        ext = os.path.splitext(filepath.lower())[1]
        
        try:
            with open(filepath, 'rb') as f:
                if ext == '.hdr':
                    return self._read_hdr_size(f)
                elif ext == '.exr':
                    return self._read_exr_size(f)
        except Exception:
            pass
        
        return ""

    def _read_hdr_size(self, f):
        """读取 Radiance HDR 格式尺寸"""
        header = b""
        while len(header) < 2048:
            line = f.readline()
            if not line:
                break
            header += line
            if b'\n\n' in header or b'\x0a\x0a' in header:
                break
        
        header_str = header.decode('ascii', errors='replace')
        import re
        match = re.search(r'-Y\s+(\d+)\s+\+X\s+(\d+)', header_str)
        if match:
            height = int(match.group(1))
            width = int(match.group(2))
            return f"{width}x{height}"
        return ""

    def _read_exr_size(self, f):
        """读取 OpenEXR 格式尺寸 — 遍历属性列表找到 dataWindow"""
        import struct
        f.seek(0)
        magic = f.read(4)
        if magic != b'\x76\x2f\x31\x01':
            return ""

        f.read(4)
        pos = 8

        while True:
            f.seek(pos)
            name = b""
            while True:
                b = f.read(1)
                if not b or b == b'\x00':
                    break
                name += b
            pos += len(name) + 1

            if not name:
                break

            f.seek(pos)
            attr_type = b""
            while True:
                b = f.read(1)
                if not b or b == b'\x00':
                    break
                attr_type += b
            pos += len(attr_type) + 1

            size_data = f.read(4)
            if len(size_data) < 4:
                break
            attr_size = struct.unpack('<I', size_data)[0]
            pos += 4

            if name == b'dataWindow' and attr_type == b'box2i':
                data = f.read(attr_size)
                if len(data) >= 16:
                    xmin, ymin, xmax, ymax = struct.unpack('<iiii', data[:16])
                    width = xmax - xmin + 1
                    height = ymax - ymin + 1
                    return f"{width}x{height}"
                return ""

            pos += attr_size

    def _stop_media(self):
        """停止所有媒体播放（GIF + MP4）"""
        if self._gif_movie:
            self._gif_movie.stop()
            self._gif_movie.deleteLater()
        self._gif_movie = None
        if self._mp4_player:
            self._mp4_player.stop()
            self._mp4_player.deleteLater()
        self._mp4_player = None
        if self._mp4_widget:
            self._mp4_widget.hide()  # 立即隐藏，避免切换时闪现上一次的视频画面
            self._mp4_widget.deleteLater()
        self._mp4_widget = None
        self._preview_label.clear()
        self._preview_label.setMovie(None)

    def _stop_gif(self):
        self._stop_media()

    def _play_mp4(self, mp4_path, size):
        url = QtCore.QUrl.fromLocalFile(mp4_path)
        self._mp4_player = QtMultimedia.QMediaPlayer(self)
        self._mp4_widget = QtMultimediaWidgets.QVideoWidget(self._preview_label)
        self._mp4_widget.setFixedSize(size, size)
        self._mp4_widget.show()
        self._mp4_player.setVideoOutput(self._mp4_widget)
        self._mp4_player.setSource(url)
        self._mp4_player.play()
        self._mp4_player.mediaStatusChanged.connect(
            lambda s: s == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia and self._mp4_player.play()
        )

    def _draw_sphere(self, p, x, y, r, c):
        g = QtGui.QRadialGradient(QtCore.QPointF(x - r * 0.3, y - r * 0.35), r * 1.3)
        g.setColorAt(0, c.lighter(150)); g.setColorAt(0.5, c); g.setColorAt(0.85, c.darker(180)); g.setColorAt(1, c.darker(180).darker(150))
        p.setBrush(QtGui.QBrush(g)); p.setPen(QtCore.Qt.PenStyle.NoPen); p.drawEllipse(QtCore.QPoint(x, y), r, r)
        hg = QtGui.QRadialGradient(QtCore.QPointF(x - r * 0.4, y - r * 0.5), r * 0.55)
        hg.setColorAt(0, QtGui.QColor(255, 255, 255, 60)); hg.setColorAt(0.4, QtGui.QColor(255, 255, 255, 20)); hg.setColorAt(1, QtGui.QColor(255, 255, 255, 0))
        p.setBrush(QtGui.QBrush(hg)); p.drawEllipse(QtCore.QPoint(x, y), r, r)
