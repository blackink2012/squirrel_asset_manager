# -*- coding: utf-8 -*-
"""资产观察窗口 — 双击右侧预览图弹出。

左侧：大图预览（支持多缩略图滚轮 / 缩略图条切换）
右侧：资产属性信息
窗口可拖动、缩放、关闭（QDialog 标准窗口）。
"""

import os

from ..utils.maya_utils import get_qt_modules

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

from .preview_panel import list_asset_thumbnails


class _PreviewLabel(QtWidgets.QLabel):
    """弹窗预览图标签：滚轮切换缩略图（direction: +1 下一张 / -1 上一张）"""
    wheelSwitched = QtCore.Signal(int)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.wheelSwitched.emit(1 if delta > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)


class AssetPreviewDialog(QtWidgets.QDialog):
    """资产观察弹窗：左预览右属性，可拖动/缩放/关闭"""

    def __init__(self, material, parent=None):
        super().__init__(parent)
        self._material = material or {}
        self._thumb_names = []
        self._thumb_index = 0
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # 最大化 / 最小化 / 关闭 按钮
        self.setWindowFlags(self.windowFlags()
                            | QtCore.Qt.WindowSystemMenuHint
                            | QtCore.Qt.WindowMinimizeButtonHint
                            | QtCore.Qt.WindowMaximizeButtonHint)
        title = self._material.get("name_cn") or self._material.get("name") or t("asset_preview.title")
        self.setWindowTitle(title)
        self.resize(1350, 930)  # 默认 1.5 倍（900×620 → 1350×930）
        self.setMinimumSize(560, 420)
        self._setup_ui()
        self._load_thumb_list()
        self._build_properties()
        self._refresh_preview()

    def showEvent(self, event):
        super().showEvent(event)
        # 布局稳定后再渲染一次，避免首帧按旧（小）尺寸绘制导致图片偏小
        QtCore.QTimer.singleShot(0, self._refresh_preview)

    # ── 右键菜单（复用资产卡片菜单） ──────────────────

    def _resolve_grid(self):
        """找到主窗口的缩略图网格（用于复用卡片右键菜单）"""
        w = self.parent()
        return getattr(w, "_thumbnail_grid", None) if w else None

    def contextMenuEvent(self, event):
        grid = self._resolve_grid()
        if grid is None:
            super().contextMenuEvent(event)
            return
        gp = event.globalPos() if hasattr(event, "globalPos") else event.globalPosition().toPoint()
        grid.show_context_menu_for_material(self._material, gp, anchor_widget=self)
        event.accept()

    # ── UI ──────────────────────────────────────────

    def _setup_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # 左：预览图
        left = QtWidgets.QFrame()
        left.setStyleSheet("QFrame { background:#1a1a1a; border:1px solid #3a3a3a; }")
        left.setMinimumWidth(380)
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(6)
        self._preview_label = _PreviewLabel()
        self._preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(360, 360)
        self._preview_label.wheelSwitched.connect(self._on_wheel)
        lv.addWidget(self._preview_label, 1)

        # 多缩略图预览条（左预览图下方，点击切换主图）
        self._build_thumb_strip(lv)

        # 右：属性
        right = QtWidgets.QWidget()
        right.setMinimumWidth(260)
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(2, 0, 2, 0)
        rv.setSpacing(8)
        header = QtWidgets.QLabel(t("preview_panel.attributes"))
        header.setStyleSheet("color:#e0e0e0; font-size:14px; font-weight:bold;")
        rv.addWidget(header)
        self._prop_container = QtWidgets.QWidget()
        self._prop_layout = QtWidgets.QVBoxLayout(self._prop_container)
        self._prop_layout.setContentsMargins(0, 0, 0, 0)
        self._prop_layout.setSpacing(5)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:transparent; }")
        scroll.setWidget(self._prop_container)
        rv.addWidget(scroll, 1)

        # 左右分栏：分隔条可拖动调整比例；窗口缩放时右侧保持固定宽度不跟随
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background-color:#3a3a3a; }")
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)  # 左侧吸收窗口缩放增量
        splitter.setStretchFactor(1, 0)  # 右侧保持固定宽度
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([820, 500])
        root.addWidget(splitter, 1)

    # ── 多缩略图 ────────────────────────────────────

    def _load_thumb_list(self):
        self._thumb_names = []
        self._thumb_index = 0
        json_path = self._material.get("json_path", "")
        if json_path and not os.path.isdir(json_path):
            json_path = os.path.dirname(json_path)
        if json_path and os.path.isdir(json_path):
            if self._material.get("is_zasset"):
                self._thumb_names = list_asset_thumbnails(json_path)
            else:
                self._thumb_names = list_asset_thumbnails(json_path, base_name=self._material.get("name", ""))
        self._refresh_thumb_strip()

    def _thumb_bytes_at(self, idx):
        """返回指定缩略图索引对应的图片字节"""
        names, mat = self._thumb_names, self._material
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

    def _current_bytes(self):
        return self._thumb_bytes_at(self._thumb_index)

    def _on_prev(self):
        if self._thumb_index > 0:
            self._thumb_index -= 1
            self._refresh_preview()
            self._refresh_thumb_strip()

    def _on_next(self):
        if self._thumb_index < len(self._thumb_names) - 1:
            self._thumb_index += 1
            self._refresh_preview()
            self._refresh_thumb_strip()

    def _on_thumb_select(self, idx):
        """点击缩略图预览条 → 切换主图"""
        if 0 <= idx < len(self._thumb_names) and idx != self._thumb_index:
            self._thumb_index = idx
            self._refresh_preview()
            self._refresh_thumb_strip()

    def _on_wheel(self, direction):
        if direction > 0:
            self._on_prev()
        else:
            self._on_next()

    # ── 多缩略图预览条 ──────────────────────────────

    def _build_thumb_strip(self, lv):
        """构建左侧预览图下方的多缩略图预览条（116px，原始两倍）"""
        self._thumb_strip = QtWidgets.QScrollArea()
        self._thumb_strip.setWidgetResizable(True)
        self._thumb_strip.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._thumb_strip.setFixedHeight(132)
        self._thumb_strip.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_strip.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._thumb_strip.setStyleSheet(
            "QScrollArea { background:#141414; border:1px solid #3a3a3a; border-radius:4px; }"
            "QScrollBar:horizontal { height:6px; background:#222; }"
            "QScrollBar::handle:horizontal { background:#4a4a4a; border-radius:3px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0px; }"
        )
        self._thumb_strip_widget = QtWidgets.QWidget()
        self._thumb_strip_widget.setStyleSheet("background:#141414;")
        self._strip_layout = QtWidgets.QHBoxLayout(self._thumb_strip_widget)
        self._strip_layout.setContentsMargins(3, 3, 3, 3)
        self._strip_layout.setSpacing(4)
        self._thumb_strip.setWidget(self._thumb_strip_widget)
        self._thumb_strip.setVisible(False)
        lv.addWidget(self._thumb_strip)

    def _refresh_thumb_strip(self):
        """按当前缩略图列表重建预览条（仅多图时显示），当前索引高亮"""
        layout = self._strip_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        names = self._thumb_names
        self._thumb_strip.setVisible(len(names) > 1)
        if len(names) <= 1:
            return
        for idx in range(len(names)):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(116, 116)
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(names[idx])
            btn.setIcon(QtGui.QIcon(self._make_thumb_pixmap(idx, 104)))
            btn.setIconSize(QtCore.QSize(104, 104))
            if idx == self._thumb_index:
                btn.setStyleSheet(
                    "QPushButton { background:#2d4a6f; border:2px solid #5294e2; border-radius:4px; }")
            else:
                btn.setStyleSheet(
                    "QPushButton { background:#2a2a2a; border:2px solid #3a3a3a; border-radius:4px; }"
                    "QPushButton:hover { border-color:#5a5a5a; }")
            btn.clicked.connect(lambda checked=False, i=idx: self._on_thumb_select(i))
            layout.addWidget(btn)
        layout.addStretch(1)
        # 让预览条内部 widget 至少与内容同宽（内容超宽时出现横向滚动条）
        layout.activate()
        self._thumb_strip_widget.setMinimumWidth(layout.sizeHint().width())

    def _make_thumb_pixmap(self, idx, size):
        """生成小缩略图（加载失败时显示 ▶ 占位，如 mp4 动图）"""
        data = self._thumb_bytes_at(idx)
        pix = QtGui.QPixmap()
        if data and pix.loadFromData(data) and not pix.isNull():
            scaled = pix.scaled(size, size, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                QtCore.Qt.TransformationMode.SmoothTransformation)
            canvas = QtGui.QPixmap(size, size)
            canvas.fill(QtGui.QColor("#1a1a1a"))
            painter = QtGui.QPainter(canvas)
            x = (size - scaled.width()) // 2
            y = (size - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.end()
            return canvas
        canvas = QtGui.QPixmap(size, size)
        canvas.fill(QtGui.QColor("#222222"))
        painter = QtGui.QPainter(canvas)
        painter.setPen(QtGui.QColor(255, 255, 255, 90))
        painter.drawText(canvas.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "▶")
        painter.end()
        return canvas

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_preview()

    # ── 预览渲染 ────────────────────────────────────

    def _refresh_preview(self):
        label = self._preview_label
        data = self._current_bytes()
        w = max(label.width(), 200)
        h = max(label.height(), 200)
        canvas = QtGui.QPixmap(w, h)
        canvas.fill(QtGui.QColor("#1a1a1a"))
        pix = None
        if data:
            pix = QtGui.QPixmap()
            if not pix.loadFromData(data):
                pix = None
        if pix is not None and not pix.isNull():
            scaled = pix.scaled(w, h, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                QtCore.Qt.TransformationMode.SmoothTransformation)
            painter = QtGui.QPainter(canvas)
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.end()
        else:
            painter = QtGui.QPainter(canvas)
            painter.setPen(QtGui.QColor(255, 255, 255, 40))
            painter.drawText(canvas.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                             t("asset_preview.no_preview"))
            painter.end()
        label.setPixmap(canvas)

    # ── 属性 ────────────────────────────────────────

    def _build_properties(self):
        mat = self._material
        rows = []
        add = rows.append

        asset_name = mat.get("name", "")
        if asset_name:
            add(t("preview_panel.asset_name_value", value=asset_name))
        node_type = mat.get("node_type")
        if node_type:
            add(t("preview_panel.node_type_value", value=node_type))
        asset_type = mat.get("_asset_type")
        if asset_type:
            add(t("preview_panel.asset_type_value", value=asset_type))
        cat = mat.get("_category_display") or mat.get("category")
        if cat:
            add(t("preview_panel.category_value", value=cat))

        # 格式
        exported = mat.get("exported_formats") or mat.get("formats")
        if exported:
            df = [f.upper() for f in exported if f not in ("sicon", "aicon", "mcm")]
            add(t("preview_panel.format_value", value=", ".join(df)) if df else t("preview_panel.format_unknown"))
        else:
            node_path = mat.get("node_json_path", "")
            jp = mat.get("json_path", "")
            ext = ""
            if node_path and not node_path.endswith(".zasset"):
                ext = os.path.splitext(node_path)[1].lstrip(".").upper()
            elif jp and not jp.endswith(".zasset"):
                ext = os.path.splitext(jp)[1].lstrip(".").upper()
            add(t("preview_panel.format_value", value=ext) if ext else t("preview_panel.format_unknown"))

        # 大小
        size = self._calc_size(mat)
        add(t("preview_panel.size_value", value=self._fmt_size(size)) if size else t("preview_panel.size_dash"))

        ani = mat.get("ani", [])
        if ani:
            add(t("preview_panel.animation_format_value", value=", ".join(ani)))
        res = self._calc_resolution(mat)
        if res:
            add(t("preview_panel.resolution_value", value=res))
        sw = mat.get("software")
        if sw:
            add(t("preview_panel.software_value", value=sw))
        rd = mat.get("renderer")
        if rd:
            add(t("preview_panel.renderer_value", value=rd))
        cs = mat.get("color_space")
        if cs:
            add(t("preview_panel.color_space_value", value=cs))
        ed = mat.get("create_date") or mat.get("export_date")
        if ed:
            add(t("preview_panel.create_time_value", value=ed))
        notes = mat.get("notes")
        if notes:
            add(t("preview_panel.notes_value", value=notes))
        jp = mat.get("json_path")
        if jp:
            add(f"<span style='color:#707070'>" + self._html_escape(jp) + "</span>")

        for txt in rows:
            lb = QtWidgets.QLabel(txt)
            lb.setWordWrap(True)
            lb.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            lb.setStyleSheet("color:#d0d0d0; font-size:12px; background:transparent;")
            self._prop_layout.addWidget(lb)
        self._prop_layout.addStretch(1)

    @staticmethod
    def _html_escape(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    @staticmethod
    def _fmt_size(size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024
        return f"{size:.1f} TB"

    @staticmethod
    def _calc_size(mat):
        size = 0
        if mat.get("is_zasset"):
            zpath = mat.get("zasset_path", "") or mat.get("json_path", "")
            if zpath and os.path.isdir(zpath):
                for root, dirs, filenames in os.walk(zpath):
                    for fn in filenames:
                        try:
                            size += os.path.getsize(os.path.join(root, fn))
                        except OSError:
                            pass
        else:
            jp = mat.get("json_path", "")
            if jp and os.path.isfile(jp):
                try:
                    size += os.path.getsize(jp)
                except OSError:
                    pass
                thumb = mat.get("thumbnail_path", "")
                if thumb and os.path.isfile(thumb):
                    try:
                        size += os.path.getsize(thumb)
                    except OSError:
                        pass
        return size

    @staticmethod
    def _calc_resolution(mat):
        """读取第一张贴图的分辨率（仅读图像头，不载入全图）"""
        zpath = mat.get("zasset_path", "") or mat.get("json_path", "")
        if not zpath:
            return ""
        textures_dir = os.path.join(zpath, "textures")
        if not os.path.isdir(textures_dir):
            return ""
        exts = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".exr", ".hdr"}
        try:
            for fname in sorted(os.listdir(textures_dir)):
                if os.path.splitext(fname)[1].lower() not in exts:
                    continue
                fp = os.path.join(textures_dir, fname)
                reader = QtGui.QImageReader(fp)
                size = reader.size()
                if size.isValid():
                    return f"{size.width()} × {size.height()}"
        except Exception:
            pass
        return ""
