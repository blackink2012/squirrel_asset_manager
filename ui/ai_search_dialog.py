# -*- coding: utf-8 -*-
"""AI 搜索预览资产 — 聊天式搜索对话框

用户以自然语言描述想要的资产 → AI 提取概括性 tag 关键词 → 搜索资产库 →
对话气泡内以缩略图网格返回。支持：
- 标签 chips 点 ✕ 移除 / 点击编辑（移除或编辑后免 AI 直接重搜）
- 卡片右键：导入到 Maya / 在库中定位 / 找相似 / 发送给 AI 分析 / 查看详情
- 拖拽卡片到 Maya 视口导入（复用主窗口 dragDroppedOnViewport 链路）
- 🖼 发送图片 → 视觉模型分析 → 更精确搜索（图搜图）
- 快捷指令 chips、结果折叠展开、多轮细化上下文
"""
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

try:
    from ..core.ai_search import (
        AISearchAssistant, load_ai_search_settings,
        get_ai_search_api_key, set_ai_search_api_key, save_ai_search_config,
        search_materials_with_notes, fetch_available_models,
        load_wishlist, save_wishlist, _expand_bilingual)
except ImportError:
    AISearchAssistant = None
    load_ai_search_settings = lambda: {}
    get_ai_search_api_key = lambda s, p: ""
    set_ai_search_api_key = lambda s, p, k: s
    save_ai_search_config = lambda c: None
    search_materials_with_notes = lambda mgr, kws, sub_lib="", limit=30: []
    fetch_available_models = lambda provider, api_key="", base_url="": []
    load_wishlist = lambda: []
    save_wishlist = lambda items: None
    _expand_bilingual = lambda kws: list(kws)

try:
    from ..utils.settings import apply_font_size_to_widget, get_ui_font_size
except ImportError:
    apply_font_size_to_widget = lambda widget, font_size: None
    get_ui_font_size = lambda: 13

try:
    from ..core.ai_analyzer import AIAnalyzer
except ImportError:
    AIAnalyzer = None

from ..utils.maya_utils import get_qt_modules, qt_exec, qt_connect
from .preview_panel import FlowLayout

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

# AI 对话框创建时从主 UI 设置同步字体大小（默认 13），4K 高 DPI 下与主界面保持一致
_ACTIVE_FONT_SIZE = {"v": None}


def _ui_fs():
    """当前生效的 UI 字体大小（对话框创建时从主 UI 设置同步），默认 13"""
    if _ACTIVE_FONT_SIZE["v"] is None:
        try:
            _ACTIVE_FONT_SIZE["v"] = int(get_ui_font_size())
        except Exception:
            _ACTIVE_FONT_SIZE["v"] = 13
    return _ACTIVE_FONT_SIZE["v"]


def _font_style(ss, fs=None):
    """把样式表字符串中的 font-size 统一替换为 fs（默认当前生效字体大小）"""
    fs = _ui_fs() if fs is None else fs
    return re.sub(r"font-size:\s*\d+px", f"font-size: {fs}px", ss)


def _event_pos(event):
    """QMouseEvent 坐标兼容：PySide6 用 position()，PySide2 用 pos()"""
    return event.position().toPoint() if hasattr(event, "position") else event.pos()


def _safe_emit(sig, *args):
    """线程内安全发信号：目标 QObject 已销毁时静默忽略，避免 RuntimeError 崩溃"""
    try:
        sig.emit(*args)
    except RuntimeError:
        pass


_DIALOG_STYLE = """
QDialog, QWidget { background-color: #252525; }
QLabel { color: #a0a0a0; font-size: 13px; }
QLineEdit {
    background-color: #2a2a2a;
    border: 1px solid #4a4a4a;
    border-radius: 5px;
    color: #e0e0e0;
    padding: 7px 10px;
    font-size: 13px;
}
QLineEdit:focus { border-color: #5294e2; }
QPushButton {
    background-color: #3a3a3a;
    border: none;
    border-radius: 4px;
    color: #d0d0d0;
    padding: 7px 16px;
    font-size: 13px;
}
QPushButton:hover { background-color: #4a4a4a; }
QPushButton:disabled { background-color: #2a2a2a; color: #606060; }
QPushButton#sendBtn { background-color: #5294e2; color: #ffffff; }
QPushButton#sendBtn:hover { background-color: #62a4f2; }
QPushButton#sendBtn:disabled { background-color: #2d4a6f; color: #8aa4c8; }
QPushButton#iconBtn { background-color: #3a3a3a; padding: 7px 10px; }
QPushButton#chipBtn {
    background-color: #1f2f4f;
    border: 1px solid #2d4a6f;
    border-radius: 10px;
    color: #7a9cc8;
    padding: 3px 10px;
    font-size: 12px;
}
QPushButton#chipBtn:hover { background-color: #2d4a6f; color: #9cc4ff; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #1a1a1a; width: 8px; }
QScrollBar::handle:vertical { background: #4a4a4a; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_MENU_STYLE = """
QMenu { background-color: #2d2d2d; color: #d0d0d0; border: 1px solid #4a4a4a; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 14px; font-size: 13px; }
QMenu::item:selected { background-color: #2d4a6f; color: #5294e2; }
"""

# AI 右键动作 → 卡片信号名（结果卡片与心愿单共用）
_AI_ACTION_SIGNALS = {
    "locate": "locateRequested",
    "find_similar": "findSimilarRequested",
    "send_ai": "sendToAiRequested",
    "wishlist": "wishlistRequested",
    "detail": "detailRequested",
}


def _resolve_main_grid(host):
    """定位主窗口的缩略图网格（用于继承主窗口资产右键的个性化导入菜单）"""
    w = host.parent() if host is not None else None
    return getattr(w, "_thumbnail_grid", None) if w else None


def _build_ai_menu_actions(menu, mat, font_size=13):
    """在主窗口资产右键菜单末尾追加 AI 搜索专属动作（返回 动作→标识 映射）"""
    menu.addSeparator()
    locate_a = menu.addAction(t("ai_search.ctx_locate"))
    find_a = menu.addAction(t("ai_search.ctx_find_similar"))
    send_a = menu.addAction(t("ai_search.ctx_send_to_ai"))
    wish_a = menu.addAction(t("ai_search.wishlist_add"))
    detail_a = menu.addAction(t("ai_search.ctx_detail"))
    return {locate_a: "locate", find_a: "find_similar",
            send_a: "send_ai", wish_a: "wishlist", detail_a: "detail"}


def _emit_ai_context_action(widget, material, ident):
    """按标识把 AI 右键动作分发到卡片对应信号"""
    sig_name = _AI_ACTION_SIGNALS.get(ident)
    if sig_name:
        getattr(widget, sig_name).emit(material)


def _show_main_context_menu(widget, host, pos):
    """委托主窗口资产右键菜单（仅收藏夹以上 + AI 动作），返回命中的 AI 动作标识。

    widget 为卡片（需含 _material 且可 mapToGlobal），host 为其宿主对话框；
    主窗口网格不可用时返回 None。
    """
    grid = _resolve_main_grid(host)
    if grid is None:
        return None
    gp = widget.mapToGlobal(pos)
    sel = {widget._material.get("id", ""): widget._material} if widget._material.get("id") else {}
    return grid.show_context_menu_for_material(
        widget._material, gp, anchor_widget=widget,
        extra_actions=[_build_ai_menu_actions], selection=sel, top_only=True)


class _SingleDoubleClick:
    """单击 / 双击判别：Qt 双击序列为 Press→Release→DblClick→Release。

    按下记录位置并停表；释放且未拖动时启动 interval 毫秒单击定时器；
    双击（DblClick）立即触发双击回调并抑制随后的第二次 Release，避免误触发单击。
    """

    def __init__(self, widget, on_single, on_double, threshold=10, interval=150):
        self.on_single = on_single
        self.on_double = on_double
        self.threshold = threshold
        self.press = None
        self.suppress = False
        self._timer = QtCore.QTimer(widget)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._fire_single)

    def press_event(self, pos):
        self.press = pos
        self.suppress = False
        self._timer.stop()

    def release_event(self, pos):
        if self.suppress:
            self.suppress = False
            return
        p = self.press
        self.press = None
        if p is not None and (pos - p).manhattanLength() < self.threshold:
            self._timer.start()

    def double_event(self):
        self._timer.stop()
        self.suppress = True
        self.on_double()

    def cancel(self):
        self._timer.stop()
        self.press = None

    def _fire_single(self):
        self.suppress = False
        self.on_single()


class _ThumbSignal(QtCore.QObject):
    """跨线程缩略图加载完成信号"""
    loaded = QtCore.Signal(str, object)  # (material_id, bytes or None)


class _TagChip(QtWidgets.QFrame):
    """可移除 / 可内联编辑的关键词标签"""
    removed = QtCore.Signal(str)
    edited = QtCore.Signal(str, str)  # (old, new)

    def __init__(self, tag, parent=None):
        super(_TagChip, self).__init__(parent)
        self._tag = tag
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip(t("ai_search.keywords_label"))
        self.setStyleSheet(_font_style("""
            QFrame { background-color: #1d4a6f; border: 1px solid #2d5a8f;
                     border-radius: 10px; }
            QFrame:hover { background-color: #2d5a8f; }
            QLabel { color: #9cc4ff; background: transparent; font-size: 12px; }
        """))
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 6, 2)
        lay.setSpacing(5)
        self._label = QtWidgets.QLabel(tag)
        lay.addWidget(self._label)
        self._del = QtWidgets.QLabel("✕")
        self._del.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._del.setToolTip(t("ai_search.keywords_label"))
        lay.addWidget(self._del)
        self._del.mousePressEvent = lambda e: self.removed.emit(self._tag)
        self._label.mousePressEvent = self._begin_edit
        self.mousePressEvent = self._begin_edit

    def _begin_edit(self, _event):
        """点击标签 → 内联编辑，回车/失焦确认后发出 edited"""
        lay = self.layout()
        edit = QtWidgets.QLineEdit(self._tag)
        edit.setFixedWidth(100)
        edit.setStyleSheet(_font_style("""
            QLineEdit { background-color: #12293f; border: 1px solid #5294e2;
                        border-radius: 4px; color: #9cc4ff; padding: 0 4px; font-size: 12px; }
        """))
        edit.selectAll()
        edit.setFocus()
        lay.replaceWidget(self._label, edit)
        self._label.hide()

        def _commit(*_a):
            new = edit.text().strip()
            if new and new != self._tag:
                self.edited.emit(self._tag, new)
            edit.deleteLater()
            self._label.show()
        edit.editingFinished.connect(_commit)


class _ResultCard(QtWidgets.QFrame):
    """搜索结果缩略图卡片：右键菜单 + 拖拽到 Maya + 缩略图懒加载"""
    importRequested = QtCore.Signal(dict, str)      # (material, format)
    locateRequested = QtCore.Signal(dict)
    findSimilarRequested = QtCore.Signal(dict)
    sendToAiRequested = QtCore.Signal(dict)
    wishlistRequested = QtCore.Signal(dict)
    detailRequested = QtCore.Signal(dict)
    dragRequested = QtCore.Signal(list, int, int)   # ([ids], gx, gy)

    def __init__(self, material, host, parent=None):
        super(_ResultCard, self).__init__(parent)
        self._material = material
        self._host = host
        self._press_pos = None
        self._drag_started = False
        # 单击 → 在库中定位；双击 → 打开资产预览窗口
        self._click = _SingleDoubleClick(
            self,
            on_single=lambda: self.locateRequested.emit(self._material),
            on_double=lambda: self.detailRequested.emit(self._material))
        self.setObjectName("resultCard")
        self.setStyleSheet("""
            QFrame#resultCard { background-color: #1f1f1f;
                                border: 1px solid #3a3a3a; border-radius: 6px; }
            QFrame#resultCard:hover { border: 1px solid #5294e2; }
            QLabel { background: transparent; }
        """)
        self._build()
        host.request_thumb(self)

    # ── UI ──
    def _build(self):
        fs = getattr(self._host, "_font_size", _ui_fs())
        self._thumb_size = int(96 * getattr(self._host, "_scale", 1.0))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._thumb_label = QtWidgets.QLabel()
        self._thumb_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumb_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet(
            "background-color: #1a1a1a; border-radius: 4px; color: #606060;")
        self._thumb_label.setText(t("ai_search.no_thumb"))
        lay.addWidget(self._thumb_label, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        name = self._material.get("name_cn") or self._material.get("name", "")
        self._name_label = QtWidgets.QLabel(name)
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet(f"color: #e0e0e0; font-size: {fs}px;")
        self._name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._name_label)

    def material(self):
        return self._material

    # ── 缩略图 ──
    def set_thumb_bytes(self, data):
        if not data:
            return
        pix = QtGui.QPixmap()
        if not pix.loadFromData(data) or pix.isNull():
            return
        # 方形裁剪（与主网格一致）
        if pix.width() != pix.height():
            s = min(pix.width(), pix.height())
            pix = pix.copy((pix.width() - s) // 2, (pix.height() - s) // 2, s, s)
        pix = pix.scaled(self._thumb_size, self._thumb_size,
                         QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                         QtCore.Qt.TransformationMode.SmoothTransformation)
        self._thumb_label.setPixmap(pix)

    # ── 鼠标交互：左键单击定位 / 双击预览 / 右键菜单 / 拖拽到 Maya ──
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._press_pos = _event_pos(event)
            self._click.cancel()
            self._show_menu(_event_pos(event))
            return
        self._press_pos = _event_pos(event)
        self._drag_started = False
        self._click.press_event(_event_pos(event))
        super(_ResultCard, self).mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and not self._drag_started:
            self._click.release_event(_event_pos(event))
        super(_ResultCard, self).mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._click.double_event()
        event.accept()

    def _show_menu(self, pos):
        if _resolve_main_grid(self._host) is None:
            self._show_simple_menu(pos)
            return
        result = _show_main_context_menu(self, self._host, pos)
        if result:
            _emit_ai_context_action(self, self._material, result)

    def _show_simple_menu(self, pos):
        """兜底右键菜单（主窗口网格不可用时）"""
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(_font_style(_MENU_STYLE))
        json_path = self._material.get("json_path", "")
        import_actions = {}
        if json_path and json_path.endswith(".zasset"):
            fmts = []
            try:
                from ..integration.import_executor import get_available_formats
                fmts = get_available_formats(json_path)
            except Exception:
                fmts = []
            if len(fmts) == 1:
                a = menu.addAction(t("ai_search.ctx_import"))
                import_actions[a] = fmts[0]
            elif len(fmts) > 1:
                sub = menu.addMenu(t("ai_search.ctx_import"))
                for f in fmts:
                    a = sub.addAction("  %s" % f)
                    import_actions[a] = f
        elif json_path:
            a = menu.addAction(t("ai_search.ctx_import"))
            import_actions[a] = os.path.splitext(json_path)[1].lstrip(".") or "ma"

        locate_a = menu.addAction(t("ai_search.ctx_locate"))
        find_a = menu.addAction(t("ai_search.ctx_find_similar"))
        send_a = menu.addAction(t("ai_search.ctx_send_to_ai"))
        wish_a = menu.addAction(t("ai_search.wishlist_add"))
        menu.addSeparator()
        detail_a = menu.addAction(t("ai_search.ctx_detail"))

        action = qt_exec(menu, self.mapToGlobal(pos))
        if action in import_actions:
            self.importRequested.emit(self._material, import_actions[action])
        elif action == locate_a:
            self.locateRequested.emit(self._material)
        elif action == find_a:
            self.findSimilarRequested.emit(self._material)
        elif action == send_a:
            self.sendToAiRequested.emit(self._material)
        elif action == wish_a:
            self.wishlistRequested.emit(self._material)
        elif action == detail_a:
            self.detailRequested.emit(self._material)

    # ── 拖拽到 Maya ──
    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            return
        if self._press_pos is None:
            return
        if (event.pos() - self._press_pos).manhattanLength() < 10:
            return
        self._drag_started = True
        self._click.cancel()
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        ids = json.dumps([self._material.get("id", "")])
        mime.setData("application/x-material-ids", ids.encode())
        mime.setText(ids)
        drag.setMimeData(mime)
        pix = self._thumb_label.pixmap()
        if pix:
            drag.setPixmap(pix.scaled(80, 80, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                      QtCore.Qt.TransformationMode.SmoothTransformation))
            drag.setHotSpot(QtCore.QPoint(40, 40))
        qt_exec(drag, QtCore.Qt.DropAction.CopyAction)
        # 鼠标落在插件窗口之外 → 视为拖到 Maya，交给主窗口导入
        cursor = QtGui.QCursor.pos()
        top_window = self.window()
        outside = (top_window is None
                   or not top_window.frameGeometry().contains(cursor))
        if outside:
            self.dragRequested.emit([self._material.get("id", "")],
                                    cursor.x(), cursor.y())


class _WishlistRow(QtWidgets.QFrame):
    """心愿单条目：大缩略图 + 名称 + ✕ 移除。

    单击定位到主窗口；双击打开资产预览窗口；右键继承主窗口资产右键菜单
    （仅收藏夹以上 + AI 动作）。
    """
    removeRequested = QtCore.Signal(str)   # material_id
    clicked = QtCore.Signal(dict)          # material（单击 → 定位）
    locateRequested = QtCore.Signal(dict)
    findSimilarRequested = QtCore.Signal(dict)
    sendToAiRequested = QtCore.Signal(dict)
    wishlistRequested = QtCore.Signal(dict)
    detailRequested = QtCore.Signal(dict)

    def __init__(self, material, host=None, pix=None, parent=None):
        super(_WishlistRow, self).__init__(parent)
        self._material = material
        self._host = host
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame { background-color: #202020; border: 1px solid #3a3a3a;
                     border-radius: 6px; }
            QFrame:hover { border: 1px solid #5294e2; background-color: #252525; }
            QLabel { background: transparent; }
        """)
        # 单击 → 在库中定位；双击 → 打开资产预览窗口
        self._click = _SingleDoubleClick(
            self,
            on_single=lambda: self.clicked.emit(self._material),
            on_double=lambda: self.detailRequested.emit(self._material))
        self._fs = getattr(self._host, "_font_size", _ui_fs())
        self._thumb_size = int(92 * getattr(self._host, "_scale", 1.0))
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(6, 4, 4, 6)
        lay.setSpacing(4)
        self._thumb_label = QtWidgets.QLabel()
        self._thumb_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumb_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet(
            "background-color: #141414; border-radius: 6px; color: #606060;")
        lay.addWidget(self._thumb_label, 0, 0)
        # ✕ 放在卡片右上角（不是缩略图右上角）
        self._del = QtWidgets.QLabel("✕")
        self._del.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._del.setToolTip(t("ai_search.wishlist_remove"))
        self._del.setStyleSheet("padding: 2px 4px; color: #a0a0a0;")
        self._del.mousePressEvent = lambda e: (
            self.removeRequested.emit(material.get("id", "")), e.accept())
        lay.addWidget(self._del, 0, 1,
                      QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignRight)
        lay.setColumnStretch(1, 1)
        name = material.get("name_cn") or material.get("name", "")
        self._name_label = QtWidgets.QLabel(name)
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet(f"color: #d0d0d0; font-size: {self._fs}px;")
        self._name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # 长名称可收缩换行，避免撑宽卡片导致右列被视口切掉
        self._name_label.setMinimumWidth(0)
        self._name_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                       QtWidgets.QSizePolicy.Policy.Preferred)
        lay.addWidget(self._name_label, 1, 0, 1, 2)
        self.setMinimumWidth(0)
        # 行与缩略图共享同一套单击/双击/右键处理
        self.mousePressEvent = self._on_row_press
        self.mouseReleaseEvent = self._on_row_release
        self.mouseDoubleClickEvent = self._on_row_double
        self._thumb_label.mousePressEvent = self._on_row_press
        self._thumb_label.mouseReleaseEvent = self._on_row_release
        self._thumb_label.mouseDoubleClickEvent = self._on_row_double
        if pix is not None:
            self.set_thumb(pix)

    def set_thumb(self, pix):
        scaled = pix.scaled(self._thumb_size - 6, self._thumb_size - 6,
                            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                            QtCore.Qt.TransformationMode.SmoothTransformation)
        self._thumb_label.setPixmap(scaled)

    # ── 鼠标交互：单击定位 / 双击预览 / 右键菜单 ──
    def _on_row_press(self, event):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._click.cancel()
            self._show_menu(_event_pos(event))
            return
        self._click.press_event(_event_pos(event))

    def _on_row_release(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._click.release_event(_event_pos(event))

    def _on_row_double(self, event):
        self._click.double_event()
        event.accept()

    def _show_menu(self, pos):
        if _resolve_main_grid(self._host) is None:
            return
        result = _show_main_context_menu(self, self._host, pos)
        if result:
            _emit_ai_context_action(self, self._material, result)


class _ComposeItem(QtWidgets.QFrame):
    """输入区已选图片小卡片（缩略图 + ✕ 移除）"""
    removeRequested = QtCore.Signal(int)  # index

    def __init__(self, pix, index, parent=None):
        super(_ComposeItem, self).__init__(parent)
        self.setStyleSheet("""
            QFrame { background-color: #1f1f1f; border: 1px solid #3a3a3a;
                     border-radius: 6px; }
            QFrame:hover { border: 1px solid #5294e2; }
            QLabel { background: transparent; }
        """)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        ts = max(30, int(40 * _ui_fs() / 13.0))
        thumb = QtWidgets.QLabel()
        thumb.setFixedSize(ts, ts)
        thumb.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background-color: #1a1a1a; border-radius: 3px;")
        scaled = pix.scaled(ts - 2, ts - 2, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                            QtCore.Qt.TransformationMode.SmoothTransformation)
        thumb.setPixmap(scaled)
        lay.addWidget(thumb)
        del_label = QtWidgets.QLabel("✕")
        del_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        del_label.setStyleSheet(f"color: #a06060; font-size: {_ui_fs()}px;")
        del_label.setToolTip(t("ai_search.remove_image"))
        lay.addWidget(del_label)
        del_label.mousePressEvent = lambda e, i=index: self.removeRequested.emit(i)


class _AIBubble(QtWidgets.QFrame):
    """AI 回复气泡：意图关键词 chips + 结果概要 + 缩略图网格 + 操作脚注"""
    keywordsChanged = QtCore.Signal(object)  # (list) 移除/编辑标签后重搜

    def __init__(self, host, parent=None):
        super(_AIBubble, self).__init__(parent)
        self._host = host
        self._keywords = []
        self._materials = []
        self._shown = 6
        self.setStyleSheet("""
            QFrame { background-color: #2a2a2a; border: 1px solid #3a3a3a;
                     border-radius: 10px; }
            QLabel { background: transparent; }
        """)
        self._build()

    def _build(self):
        fs = getattr(self._host, "_font_size", _ui_fs())
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        self._head = QtWidgets.QLabel(t("ai_search.keywords_label"))
        self._head.setStyleSheet(f"color: #8ab4f8; font-size: {fs}px;")
        outer.addWidget(self._head)

        self._chips_wrap = QtWidgets.QWidget()
        self._chips_wrap.setStyleSheet("background: transparent;")
        self._chips = FlowLayout(self._chips_wrap, margin=0, spacing=6)
        outer.addWidget(self._chips_wrap)

        self._summary = QtWidgets.QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(f"color: #a0a0a0; font-size: {fs}px;")
        outer.addWidget(self._summary)

        self._grid_container = QtWidgets.QWidget()
        self._grid_container.setStyleSheet("background: transparent;")
        outer.addWidget(self._grid_container)

        self._expand_label = QtWidgets.QLabel(t("ai_search.expand_all"))
        self._expand_label.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._expand_label.setStyleSheet(f"color: #5294e2; font-size: {fs}px;")
        self._expand_label.mousePressEvent = self._toggle_expand
        self._expand_label.setVisible(False)
        outer.addWidget(self._expand_label)

        self._foot = QtWidgets.QLabel(t("ai_search.footnote"))
        self._foot.setWordWrap(True)
        self._foot.setStyleSheet(f"color: #707070; font-size: {fs}px;")
        outer.addWidget(self._foot)

    # ── 关键词 chips ──
    def set_keywords(self, keywords, edited=False):
        self._keywords = list(keywords)
        self._head.setText(
            t("ai_search.keywords_edited_label") if edited
            else t("ai_search.keywords_label"))
        while self._chips.count():
            item = self._chips.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for kw in self._keywords:
            chip = _TagChip(kw)
            chip.removed.connect(self._on_chip_removed)
            chip.edited.connect(self._on_chip_edited)
            self._chips.addWidget(chip)
        self._chips_wrap.update()

    def _on_chip_removed(self, tag):
        kws = [k for k in self._keywords if k != tag]
        self.keywordsChanged.emit(kws)

    def _on_chip_edited(self, old, new):
        kws = [new if k == old else k for k in self._keywords]
        self.keywordsChanged.emit(kws)

    # ── 结果 ──
    def set_results(self, materials, shown=6):
        self._materials = list(materials)
        self._shown = shown
        self._render_grid()
        self._update_summary()

    def _update_summary(self):
        n = len(self._materials)
        if n == 0:
            self._summary.setText(t("ai_search.no_results"))
        elif n <= self._shown:
            self._summary.setText(t("ai_search.found_count", n=n))
        else:
            self._summary.setText(t("ai_search.found_subset", n=n, shown=self._shown))
        self._expand_label.setVisible(n > 6)
        self._expand_label.setText(t("ai_search.collapse") if self._shown >= n
                                   else t("ai_search.expand_all"))

    def _render_grid(self):
        # 清空网格容器
        old = self._grid_container.layout()
        if old is not None:
            while old.count():
                item = old.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            QtWidgets.QWidget().setLayout(old)
        mats = self._materials[:self._shown]
        if not mats:
            return
        grid = QtWidgets.QGridLayout(self._grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        cols = 3
        for i, m in enumerate(mats):
            card = _ResultCard(m, self._host)
            card.importRequested.connect(self._host._on_card_import)
            card.locateRequested.connect(self._host._on_card_locate)
            card.findSimilarRequested.connect(self._host._on_card_find_similar)
            card.sendToAiRequested.connect(self._host._on_card_send_to_ai)
            card.wishlistRequested.connect(self._host._on_card_wishlist)
            card.detailRequested.connect(self._host._on_card_detail)
            card.dragRequested.connect(self._host._on_card_drag)
            grid.addWidget(card, i // cols, i % cols)

    def _toggle_expand(self, _event):
        n = len(self._materials)
        if self._shown >= n:
            self._shown = 6
        else:
            self._shown = n
        self._render_grid()
        self._update_summary()
        self._host._scroll_to_bottom()


class AISearchConfigDialog(QtWidgets.QDialog):
    """AI 搜索独立模型配置 — 使用独立设置命名空间，不与主 UI 的 AI 分析设置交叉"""

    modelsFetched = QtCore.Signal(str, object)  # (provider, (seq, models))

    def __init__(self, parent=None, available_models=None):
        super(AISearchConfigDialog, self).__init__(parent)
        self._font_size = _ui_fs()
        _ACTIVE_FONT_SIZE["v"] = self._font_size
        self._scale = self._font_size / 13.0
        self._available_models = available_models or []
        self._providers = AIAnalyzer.PROVIDERS if AIAnalyzer else {}
        self._saved = load_ai_search_settings()
        self._fetch_seq = 0
        self._closed = False
        self.setWindowTitle(t("ai_search.config_title"))
        self.setMinimumWidth(int(430 * self._scale))
        self.setStyleSheet(_DIALOG_STYLE)
        self.modelsFetched.connect(self._on_models_fetched)
        self._setup_ui()

    def _current_provider(self):
        idx = self._provider_combo.currentIndex()
        return self._provider_combo.itemData(idx) or "ollama"

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(t("ai_search.config_title"))
        title.setStyleSheet("color: #ffffff; font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # 服务商
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(t("label.ai_provider"))
        lbl.setFixedWidth(int(80 * self._scale))
        row.addWidget(lbl)
        self._provider_combo = QtWidgets.QComboBox()
        saved_provider = self._saved.get("ai_search_provider", "ollama")
        for key, cfg in (self._providers or {}).items():
            self._provider_combo.addItem(cfg.get("label", key), key)
        if self._provider_combo.count() == 0:
            self._provider_combo.addItem("Ollama（本地）", "ollama")
        self._provider_combo.setCurrentIndex(
            max(0, self._provider_combo.findData(saved_provider)))
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        row.addWidget(self._provider_combo, 1)
        layout.addLayout(row)

        # API Key（Ollama 不需要）
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(t("label.ai_api_key"))
        lbl.setFixedWidth(int(80 * self._scale))
        row.addWidget(lbl)
        self._api_key_edit = QtWidgets.QLineEdit()
        self._api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._api_key_edit.setText(
            get_ai_search_api_key(self._saved, saved_provider))
        self._api_key_edit.editingFinished.connect(self._on_conn_fields_edited)
        row.addWidget(self._api_key_edit, 1)
        layout.addLayout(row)

        # API 地址
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(t("label.ai_base_url"))
        lbl.setFixedWidth(int(80 * self._scale))
        row.addWidget(lbl)
        self._base_url_edit = QtWidgets.QLineEdit()
        self._base_url_edit.setText(self._saved.get("ai_search_base_url", ""))
        self._base_url_edit.editingFinished.connect(self._on_conn_fields_edited)
        row.addWidget(self._base_url_edit, 1)
        layout.addLayout(row)

        # 模型
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(t("label.ai_model"))
        lbl.setFixedWidth(int(80 * self._scale))
        row.addWidget(lbl)
        self._model_combo = QtWidgets.QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        row.addWidget(self._model_combo, 1)
        layout.addLayout(row)

        layout.addStretch()

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()
        cancel_btn = QtWidgets.QPushButton(t("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        ok_btn = QtWidgets.QPushButton(t("common.ok"))
        ok_btn.setObjectName("sendBtn")
        ok_btn.clicked.connect(self._on_confirm)
        btns.addWidget(ok_btn)
        layout.addLayout(btns)

        apply_font_size_to_widget(self, self._font_size)
        self._on_provider_changed()

    def _on_provider_changed(self, *_):
        provider = self._current_provider()
        cfg = (self._providers or {}).get(provider, {})
        needs_key = bool(cfg.get("needs_key"))
        self._api_key_edit.setEnabled(needs_key)
        if not needs_key:
            self._api_key_edit.setPlaceholderText("(本地服务无需填写)")
            self._api_key_edit.setText("")
        else:
            self._api_key_edit.setPlaceholderText(
                t("ai_search.config_api_key_placeholder"))
            self._api_key_edit.setText(
                get_ai_search_api_key(self._saved, provider))
        # 地址：当前值为默认值之一时切换服务商跟随默认
        provider_defaults = {
            c.get("base_url", "").rstrip("/")
            for c in (self._providers or {}).values() if c.get("base_url")
        }
        current_url = self._base_url_edit.text().strip().rstrip("/")
        if not current_url or current_url in provider_defaults:
            self._base_url_edit.setText(cfg.get("base_url", ""))
        # 模型列表：始终实时获取（Ollama /api/tags，云端 /models），不显示硬编码列表
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItem(t("ai_search.config_loading_models"))
        self._model_combo.blockSignals(False)
        self._fetch_models(provider,
                           self._api_key_edit.text().strip(),
                           self._base_url_edit.text().strip())

    def _on_conn_fields_edited(self):
        """API Key / 地址编辑完成 → 重新实时获取模型列表"""
        provider = self._current_provider()
        if provider != "ollama":
            self._fetch_models(provider,
                               self._api_key_edit.text().strip(),
                               self._base_url_edit.text().strip())

    def _fetch_models(self, provider, api_key, base_url):
        """后台实时获取模型列表，序号丢弃过期响应"""
        self._fetch_seq += 1
        seq = self._fetch_seq

        def _work():
            models = self._query_models(provider, api_key, base_url)
            _safe_emit(self.modelsFetched, provider, (seq, models))

        threading.Thread(target=_work, daemon=True).start()

    def _query_models(self, provider, api_key, base_url):
        """实时查询模型列表（复用模块级实现，与默认模型取逻辑一致）"""
        return fetch_available_models(provider, api_key, base_url)

    def _on_model_changed(self, text):
        """用户选定真实模型 → 立即持久化，下次加载 / 插件重载仍使用该模型"""
        text = (text or "").strip()
        if not text:
            return
        items = [self._model_combo.itemText(i)
                 for i in range(self._model_combo.count())]
        if text not in items:
            return  # 打字中间态 / 自定义名称，交由「确定」按钮保存
        if text in (t("ai_search.config_loading_models"),
                    t("ai_search.config_no_models")):
            return
        try:
            save_ai_search_config(self.get_config())
            self._saved = load_ai_search_settings()
        except Exception as e:
            print(f"[AISearch] 自动保存模型失败: {e}")

    def _on_models_fetched(self, provider, seq_models):
        """实时模型列表返回：仅处理最新序号与当前服务商，过期响应直接丢弃"""
        if self._closed:
            return
        seq, models = seq_models
        if provider != self._current_provider() or seq != self._fetch_seq:
            return
        # 用户本会话已选定的模型（编辑 API Key/地址触发的刷新不得覆盖用户选择）
        current = self._model_combo.currentText().strip()
        picked = current and current not in (
            t("ai_search.config_loading_models"),
            t("ai_search.config_no_models"))
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if models:
            self._model_combo.addItems(models)
            if picked and current in models:
                self._model_combo.setCurrentText(current)
            else:
                saved = self._saved.get("ai_search_model", "")
                if saved and saved in models:
                    self._model_combo.setCurrentText(saved)
                elif not saved:
                    # 无已保存预设：与默认模型解析一致，优先沿用主 UI 模型
                    shared = (self._saved.get("ai_model") or "").strip()
                    if shared in models:
                        self._model_combo.setCurrentText(shared)
                    else:
                        self._model_combo.setCurrentIndex(0)
                else:
                    self._model_combo.setCurrentIndex(0)
        else:
            self._model_combo.addItem(t("ai_search.config_no_models"))
        self._model_combo.blockSignals(False)

    def get_config(self):
        return {
            "provider": self._current_provider(),
            "api_key": self._api_key_edit.text().strip(),
            "base_url": self._base_url_edit.text().strip(),
            "model": self._model_combo.currentText().strip(),
        }

    def _on_confirm(self):
        model = self._model_combo.currentText().strip()
        loading = t("ai_search.config_loading_models")
        no_models = t("ai_search.config_no_models")
        if model == loading:
            # 列表尚未加载完成就点确定 → 阻止保存空预设，提示等待
            QtWidgets.QMessageBox.warning(
                self, t("ai_search.config_title"),
                t("ai_search.config_wait_models"))
            return
        if model == no_models:
            QtWidgets.QMessageBox.warning(
                self, t("ai_search.config_title"), no_models)
            return
        cfg = self.get_config()
        save_ai_search_config(cfg)
        self.accept()

    def closeEvent(self, event):
        """关闭（确定 / ✕）时兜底保存当前选中的模型预设，不依赖任何选择信号"""
        try:
            text = self._model_combo.currentText().strip()
            if text and text not in (
                    t("ai_search.config_loading_models"),
                    t("ai_search.config_no_models")):
                save_ai_search_config(self.get_config())
        except Exception as e:
            print(f"[AISearch] 关闭时保存模型预设失败: {e}")
        super(AISearchConfigDialog, self).closeEvent(event)


class AISearchDialog(QtWidgets.QDialog):
    """AI 搜索预览资产 — 聊天式搜索对话框"""

    # 与主窗口交互的信号
    importAssetRequested = QtCore.Signal(str, str)      # (zasset_path, format_name)
    locateAssetRequested = QtCore.Signal(dict)          # (material dict)
    dragDroppedOnViewport = QtCore.Signal(list, int, int)  # ([ids], gx, gy)

    # 内部线程 → UI 信号
    _intentDone = QtCore.Signal(object)
    _imageIntentDone = QtCore.Signal(object)
    _availabilityDone = QtCore.Signal(bool)
    _defaultModelDone = QtCore.Signal(object)  # (provider, target_model)

    def __init__(self, parent=None, manager=None):
        super(AISearchDialog, self).__init__(parent)
        self._manager = manager
        self._assistant = None
        self._analyzer = None
        self._ai_available = False
        self._closed = False
        self._busy = False
        self._context = {}           # 上一轮 {text, keywords, sub_library}
        self._last_user_text = ""
        self._last_materials = []    # 最近一次搜索结果（供拖入输入区图搜图）
        self._compose_items = []     # 组合图片：[{"kind": "file"|"material", ...}]
        self._thinking_line = None
        self._cards_by_id = {}       # material_id → [card]
        self._thumb_cache = {}       # material_id → bytes
        self._thumb_pending = set()
        self._thumb_pool = ThreadPoolExecutor(max_workers=4)
        self._thumb_sig = _ThumbSignal(self)
        self._thumb_sig.loaded.connect(self._on_thumb_loaded)
        self._wish_thumb_sig = _ThumbSignal(self)
        self._wish_thumb_sig.loaded.connect(self._on_wish_thumb_loaded)

        self._intentDone.connect(self._on_intent_done)
        self._imageIntentDone.connect(self._on_image_intent_done)
        self._availabilityDone.connect(self._on_availability_done)
        self._defaultModelDone.connect(self._on_default_model_done)

        self.setWindowTitle(t("ai_search.title"))
        self._font_size = _ui_fs()
        _ACTIVE_FONT_SIZE["v"] = self._font_size
        self._scale = self._font_size / 13.0
        self.setMinimumSize(int(1010 * self._scale), int(560 * self._scale))
        self.resize(int(1170 * self._scale), int(760 * self._scale))
        self.setStyleSheet(_DIALOG_STYLE)
        self._wish_max_stretch = -1  # 心愿单网格已设置过 stretch 的最大行号（重建时需清理）
        self._setup_ui()
        self._load_wishlist()
        self._reload_analyzer()
        self._refresh_default_model()
        self._add_welcome()

    # ── UI ──────────────────────────────────────────────

    def _setup_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ── 顶栏 ──
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("🤖 " + t("ai_search.title"))
        title.setStyleSheet("color: #ffffff; font-size: 15px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        self._model_label = QtWidgets.QLabel("")
        self._model_label.setStyleSheet("color: #7a8ab0; font-size: 12px;")
        header.addWidget(self._model_label)
        cfg_btn = QtWidgets.QPushButton("⚙")
        cfg_btn.setObjectName("iconBtn")
        cfg_btn.setToolTip(t("ai_search.config_tooltip"))
        cfg_btn.clicked.connect(self._open_config)
        header.addWidget(cfg_btn)
        clear_btn = QtWidgets.QPushButton(t("ai_search.clear"))
        clear_btn.setToolTip(t("ai_search.clear"))
        clear_btn.clicked.connect(self._on_clear)
        header.addWidget(clear_btn)
        root.addLayout(header)

        # ── 主体：左侧聊天区 + 右侧心愿单 ──
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(8)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(8)
        body.addLayout(left, 1)

        # ── 聊天滚动区 ──
        self._chat_scroll = QtWidgets.QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._chat_container = QtWidgets.QWidget()
        self._chat_container.setStyleSheet("background: transparent;")
        self._chat_layout = QtWidgets.QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(4, 4, 4, 4)
        self._chat_layout.setSpacing(8)
        self._chat_layout.addStretch(1)
        self._chat_scroll.setWidget(self._chat_container)
        left.addWidget(self._chat_scroll, 1)

        # ── 快捷指令 ──
        quick_row = QtWidgets.QHBoxLayout()
        quick_row.setSpacing(6)
        quick_defs = [
            ("ai_search.quick_textures", "找贴图"),
            ("ai_search.quick_hdr", "找 HDR"),
            ("ai_search.quick_metal", "金属材质"),
            ("ai_search.quick_retro", "复古风格"),
        ]
        self._quick_buttons = []
        for key, text in quick_defs:
            btn = QtWidgets.QPushButton(t(key) if key else text)
            btn.setObjectName("chipBtn")
            btn.clicked.connect(lambda _=False, s=text: self._input.setText(s))
            quick_row.addWidget(btn)
            self._quick_buttons.append(btn)
        custom_btn = QtWidgets.QPushButton(t("ai_search.quick_custom"))
        custom_btn.setObjectName("chipBtn")
        custom_btn.clicked.connect(self._on_add_custom_quick)
        quick_row.addWidget(custom_btn)
        quick_row.addStretch()
        self._quick_row = quick_row
        self._quick_custom_btn = custom_btn
        left.addLayout(quick_row)

        # ── 组合图片条（多图 + 文本一起发送） ──
        self._compose_wrap = QtWidgets.QWidget()
        self._compose_wrap.setVisible(False)
        self._compose_wrap.setStyleSheet("background: transparent;")
        self._compose_layout = QtWidgets.QHBoxLayout(self._compose_wrap)
        self._compose_layout.setContentsMargins(0, 0, 0, 0)
        self._compose_layout.setSpacing(6)
        self._compose_layout.addStretch(1)
        left.addWidget(self._compose_wrap)

        # ── 输入区 ──
        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(8)
        self._image_btn = QtWidgets.QPushButton("🖼")
        self._image_btn.setObjectName("iconBtn")
        self._image_btn.setToolTip(t("ai_search.attach_images"))
        self._image_btn.clicked.connect(self._on_attach_images)
        input_row.addWidget(self._image_btn)

        self._input = QtWidgets.QLineEdit()
        self._input.setPlaceholderText(t("ai_search.input_placeholder"))
        self._input.returnPressed.connect(self._on_send)
        self._input.setAcceptDrops(True)
        self._input.dragEnterEvent = self._on_input_drag_enter
        self._input.dropEvent = self._on_input_drop
        input_row.addWidget(self._input, 1)

        self._send_btn = QtWidgets.QPushButton(t("ai_search.send"))
        self._send_btn.setObjectName("sendBtn")
        self._send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self._send_btn)
        left.addLayout(input_row)

        # ── 右侧心愿单 ──
        body.addWidget(self._build_wishlist_panel(), 0)
        root.addLayout(body, 1)

        # 对齐主 UI 字体（含对话框样式表与既有控件的字号）
        apply_font_size_to_widget(self, self._font_size)

    # ── 心愿单 ──────────────────────────────────────────

    def _build_wishlist_panel(self):
        """右侧「心愿单」面板：AI 回复中收藏的资产，跨会话持久化"""
        panel = QtWidgets.QFrame()
        panel.setObjectName("wishlistPanel")
        panel.setFixedWidth(int(300 * self._scale))
        panel.setStyleSheet("""
            QFrame#wishlistPanel { background-color: #1b1b1b;
                                   border: 1px solid #2a2a2a; border-radius: 8px; }
            QLabel { background: transparent; }
        """)
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("⭐ " + t("ai_search.wishlist_title"))
        title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        header.addWidget(title)
        self._wishlist_count = QtWidgets.QLabel("0")
        self._wishlist_count.setStyleSheet("color: #7a8ab0; font-size: 12px;")
        header.addWidget(self._wishlist_count)
        header.addStretch()
        clear_btn = QtWidgets.QPushButton(t("ai_search.wishlist_clear"))
        clear_btn.setObjectName("chipBtn")
        clear_btn.setToolTip(t("ai_search.wishlist_clear"))
        clear_btn.clicked.connect(self._on_wishlist_clear)
        header.addWidget(clear_btn)
        v.addLayout(header)

        self._wish_scroll = QtWidgets.QScrollArea()
        self._wish_scroll.setWidgetResizable(True)
        self._wish_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._wish_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        # 禁止横向滚动，容器宽度始终贴合视口，保证两列完整显示
        self._wish_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._wish_container = QtWidgets.QWidget()
        self._wish_container.setStyleSheet("background: transparent;")
        self._wish_container.setMinimumWidth(0)
        # 水平 Ignored：让 widgetResizable 把容器宽度始终约束为视口宽，两列完整显示
        self._wish_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred)
        # 支持把对话框结果卡片直接拖入心愿单
        self._wish_container.setAcceptDrops(True)
        self._wish_container.dragEnterEvent = self._on_wish_drag_enter
        self._wish_container.dropEvent = self._on_wish_drop
        self._wish_layout = QtWidgets.QGridLayout(self._wish_container)
        self._wish_layout.setContentsMargins(0, 0, 0, 0)
        self._wish_layout.setSpacing(6)
        self._wish_layout.setColumnStretch(0, 1)
        self._wish_layout.setColumnStretch(1, 1)
        self._wish_scroll.setWidget(self._wish_container)
        v.addWidget(self._wish_scroll, 1)
        return panel

    def _load_wishlist(self):
        """读取持久化心愿单并渲染"""
        self._wishlist = load_wishlist()
        self._render_wishlist()

    def _render_wishlist(self):
        """重建心愿单条目列表（双列网格）"""
        # QGridLayout 移除条目不会清除行 stretch 设置：旧的 stretch 行变成内容行后，
        # 会被 widgetResizable 分配的多余纵向空间拉高，导致卡片变成长条。先清掉历史 stretch。
        for r in range(self._wish_max_stretch + 1):
            self._wish_layout.setRowStretch(r, 0)
        while self._wish_layout.count():
            item = self._wish_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._wishlist_count.setText(str(len(self._wishlist)))
        self._wish_rows = []
        if not self._wishlist:
            empty = QtWidgets.QLabel(t("ai_search.wishlist_empty"))
            empty.setWordWrap(True)
            empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            empty.setStyleSheet(f"color: #7a8ab0; font-size: {self._font_size}px; padding: 8px;")
            self._wish_layout.addWidget(empty, 0, 0, 1, 2)
            self._wish_max_stretch = 1
            self._wish_layout.setRowStretch(1, 1)
            return
        n = len(self._wishlist)
        for i, it in enumerate(self._wishlist):
            row = _WishlistRow(it, host=self)
            row.removeRequested.connect(self._on_wishlist_remove)
            row.clicked.connect(self._on_wishlist_locate)           # 单击 → 定位
            row.detailRequested.connect(self._on_card_detail)       # 双击 → 预览
            row.locateRequested.connect(self._on_card_locate)       # 右键菜单
            row.findSimilarRequested.connect(self._on_card_find_similar)
            row.sendToAiRequested.connect(self._on_card_send_to_ai)
            row.wishlistRequested.connect(self._on_card_wishlist)
            self._wish_layout.addWidget(row, i // 2, i % 2)
            self._wish_rows.append(row)
            self._load_wish_thumb(it.get("id", ""), it)
        # 尾部留白，条目靠上排列（仅最后一个空行 stretch，不作用于内容行）
        self._wish_max_stretch = (n + 1) // 2
        self._wish_layout.setRowStretch((n + 1) // 2, 1)

    def _load_wish_thumb(self, mid, material):
        """后台读取缩略图，避免阻塞"""
        def _read():
            data = None
            try:
                from ..core.zasset_io import ZassetIO
                zpath = material.get("json_path") or material.get("zasset_path") or ""
                data = (ZassetIO.read_thumbnail(zpath)
                        if zpath and os.path.isdir(zpath) else None)
            except Exception:
                data = None
            _safe_emit(self._wish_thumb_sig.loaded, mid, data)
        self._thumb_pool.submit(_read)

    def _on_wish_thumb_loaded(self, mid, data):
        if self._closed or not data:
            return
        pix = QtGui.QPixmap()
        if not pix.loadFromData(data):
            return
        for row in getattr(self, "_wish_rows", []):
            if row._material.get("id", "") == mid:
                row.set_thumb(pix)
                break

    def _on_card_wishlist(self, material, silent=False):
        """加入心愿单（右键卡片 / 拖入），已存在则提示"""
        mid = material.get("id", "")
        if not mid:
            return False
        name = material.get("name_cn") or material.get("name", "")
        if any(it.get("id") == mid for it in self._wishlist):
            if not silent:
                self._add_system(t("ai_search.wishlist_exists", name=name))
            return False
        item = {k: material.get(k) for k in (
            "id", "name", "name_cn", "json_path", "zasset_path",
            "sub_library", "category", "tags")}
        self._wishlist.append(item)
        save_wishlist(self._wishlist)
        self._render_wishlist()
        self._load_wish_thumb(mid, item)
        if not silent:
            self._add_system(t("ai_search.wishlist_added", name=name))
        return True

    def _on_wish_drag_enter(self, event):
        """拖入心愿单：接受资产卡片拖拽"""
        if event.mimeData().hasFormat("application/x-material-ids"):
            event.acceptProposedAction()
            return
        event.ignore()

    def _on_wish_drop(self, event):
        """拖入心愿单：按 id 解析资产并加入（静默）"""
        mime = event.mimeData()
        if not mime.hasFormat("application/x-material-ids"):
            event.ignore()
            return
        try:
            ids = json.loads(bytes(mime.data("application/x-material-ids")).decode())
        except Exception:
            ids = []
        event.acceptProposedAction()
        if not ids:
            return
        # id → 资产 dict（优先最近搜索结果，其次已渲染卡片）
        by_id = {}
        for m in self._last_materials:
            by_id[m.get("id", "")] = m
        for cid, cards in getattr(self, "_cards_by_id", {}).items():
            if cards:
                by_id.setdefault(cid, cards[0]._material)
        added = 0
        for mid in ids:
            mat = by_id.get(mid)
            if mat is not None:
                if self._on_card_wishlist(mat, silent=True):
                    added += 1
        if added:
            self._add_system(t("ai_search.wishlist_added_n", n=added))

    def _on_wishlist_remove(self, mid):
        self._wishlist = [it for it in self._wishlist if it.get("id") != mid]
        save_wishlist(self._wishlist)
        self._render_wishlist()

    def _on_wishlist_clear(self):
        if not self._wishlist:
            return
        ret = QtWidgets.QMessageBox.question(
            self, t("ai_search.wishlist_title"),
            t("ai_search.wishlist_clear_confirm"),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if ret != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._wishlist = []
        save_wishlist(self._wishlist)
        self._render_wishlist()
        self._add_system(t("ai_search.wishlist_cleared"))

    def _on_wishlist_locate(self, material):
        """点击心愿单条目 → 主窗口定位"""
        self.locateAssetRequested.emit(material)

    def _model_text(self):
        if self._analyzer is not None:
            return "%s · %s" % (self._analyzer.provider, self._analyzer.model)
        return t("ai_search.error_ai_unavailable")

    def _reload_analyzer(self):
        """重建 AIAnalyzer（读取保存的设置），可用性后台异步探测不阻塞开窗"""
        if AISearchAssistant:
            try:
                # fetch_default=False：无预设时直接用主 UI 模型，不联网，秒开
                self._assistant = AISearchAssistant(fetch_default=False)
                self._analyzer = self._assistant.analyzer
            except Exception as e:
                print(f"[AISearch] 初始化 AI 失败: {e}")
                self._assistant = None
                self._analyzer = None
        else:
            self._assistant = None
            self._analyzer = None
        self._ai_available = self._assistant is not None
        if hasattr(self, "_model_label"):
            self._model_label.setText(self._model_text())
        self._probe_availability()

    def _refresh_default_model(self):
        """后台刷新默认模型（仅无预设时）：不阻塞对话框打开，列表就绪后校正默认模型"""
        settings = load_ai_search_settings()
        provider = (settings.get("ai_search_provider") or "").strip()
        if not provider:
            return
        if (settings.get("ai_search_model") or "").strip():
            return  # 已有预设，无需实时校正
        api_key = get_ai_search_api_key(settings, provider)
        base_url = (settings.get("ai_search_base_url") or "").strip()

        def _work():
            try:
                models = fetch_available_models(provider, api_key, base_url)
                if not models:
                    return
                shared = (settings.get("ai_model") or "").strip()
                target = shared if shared in models else models[0]
                if self._analyzer is not None and self._analyzer.model == target:
                    return
                _safe_emit(self._defaultModelDone, (provider, target))
            except Exception as e:
                print(f"[AISearch] 刷新默认模型失败: {e}")

        threading.Thread(target=_work, daemon=True).start()

    def _on_default_model_done(self, payload):
        """实时默认模型就绪：校正分析器（不再次联网）"""
        if self._closed:
            return
        provider, target = payload
        try:
            settings = load_ai_search_settings()
            api_key = get_ai_search_api_key(settings, provider)
            base_url = (settings.get("ai_search_base_url") or "").strip()
            cfg = AIAnalyzer.PROVIDERS.get(provider, {}) or {}
            if not base_url:
                base_url = cfg.get("base_url", "")
            analyzer = AIAnalyzer(provider=provider, model=target,
                                  api_key=api_key, base_url=base_url or None)
            self._assistant = AISearchAssistant(analyzer=analyzer)
            self._analyzer = analyzer
            if hasattr(self, "_model_label"):
                self._model_label.setText(self._model_text())
        except Exception as e:
            print(f"[AISearch] 默认模型校正失败: {e}")

    def _probe_availability(self):
        """后台探测 AI 服务可用性（Ollama 未启动时避免开窗卡顿）"""
        def _work():
            ok = False
            try:
                ok = bool(self._assistant and self._assistant.available)
            except Exception:
                ok = False
            _safe_emit(self._availabilityDone, ok)
        threading.Thread(target=_work, daemon=True).start()

    def _on_availability_done(self, ok):
        if self._closed:
            return
        self._ai_available = bool(ok)
        if not ok:
            self._add_system(t("ai_search.error_ai_unavailable"))

    def _open_config(self):
        """打开 AI 搜索独立模型配置（不读写主 UI 的 AI 分析设置）"""
        dlg = AISearchConfigDialog(self)
        if qt_exec(dlg) == QtWidgets.QDialog.Accepted:
            self._reload_analyzer()
            self._add_system(self._model_text())

    # ── 消息渲染 ────────────────────────────────────────

    def _add_widget(self, widget, alignment=None):
        idx = max(0, self._chat_layout.count() - 1)
        if alignment is None:
            self._chat_layout.insertWidget(idx, widget)
        else:
            self._chat_layout.insertWidget(idx, widget, 0, alignment)

    def _add_user_text(self, text):
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(_font_style("""
            QFrame { background-color: #2d4a6f; border-radius: 10px; }
            QLabel { color: #e6f0ff; font-size: 13px; background: transparent; }
        """))
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(10, 7, 10, 7)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        frame.setMaximumWidth(int(620 * self._scale))
        self._add_widget(frame, QtCore.Qt.AlignmentFlag.AlignRight)
        self._scroll_to_bottom()

    def _add_user_image(self, pix, caption):
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(_font_style("""
            QFrame { background-color: #2d4a6f; border-radius: 10px; }
            QLabel { color: #e6f0ff; font-size: 12px; background: transparent; }
        """))
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        img = QtWidgets.QLabel()
        img.setFixedSize(int(120 * self._scale), int(120 * self._scale))
        img.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        img.setStyleSheet("background-color: #1a1a1a; border-radius: 6px;")
        scaled = pix.scaled(int(116 * self._scale), int(116 * self._scale),
                            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                            QtCore.Qt.TransformationMode.SmoothTransformation)
        img.setPixmap(scaled)
        lay.addWidget(img, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        if caption:
            cap = QtWidgets.QLabel(caption)
            cap.setWordWrap(True)
            cap.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(cap)
        frame.setMaximumWidth(int(260 * self._scale))
        self._add_widget(frame, QtCore.Qt.AlignmentFlag.AlignRight)
        self._scroll_to_bottom()

    def _add_user_images(self, pixmaps, text=""):
        """用户气泡：一张或多张图片（+可选文本），右对齐"""
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(_font_style("""
            QFrame { background-color: #2d4a6f; border-radius: 10px; }
            QLabel { color: #e6f0ff; font-size: 13px; background: transparent; }
        """))
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)
        if text:
            label = QtWidgets.QLabel(text)
            label.setWordWrap(True)
            lay.addWidget(label)
        if pixmaps:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            shown = pixmaps[:4]
            for pix in shown:
                img = QtWidgets.QLabel()
                img.setFixedSize(int(84 * self._scale), int(84 * self._scale))
                img.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                img.setStyleSheet("background-color: #1a1a1a; border-radius: 5px;")
                scaled = pix.scaled(int(80 * self._scale), int(80 * self._scale),
                                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                    QtCore.Qt.TransformationMode.SmoothTransformation)
                img.setPixmap(scaled)
                row.addWidget(img)
            if len(pixmaps) > 4:
                more = QtWidgets.QLabel("+%d" % (len(pixmaps) - 4))
                more.setStyleSheet(f"color: #8aa4c8; font-size: {self._font_size}px;")
                more.setFixedWidth(int(30 * self._scale))
                more.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                row.addWidget(more)
            row.addStretch()
            lay.addLayout(row)
        frame.setMaximumWidth(int(560 * self._scale))
        self._add_widget(frame, QtCore.Qt.AlignmentFlag.AlignRight)
        self._scroll_to_bottom()

    def _add_system(self, text):
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: #808080; font-size: {self._font_size}px;")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setMargin(2)
        self._add_widget(label)
        self._scroll_to_bottom()
        return label

    def _add_ai_bubble(self):
        bubble = _AIBubble(host=self)
        bubble.keywordsChanged.connect(self._on_bubble_keywords_changed)
        self._add_widget(bubble)
        self._scroll_to_bottom()
        return bubble

    def _add_thinking(self):
        self._thinking_line = self._add_system(t("ai_search.thinking"))

    def _remove_thinking(self):
        if self._thinking_line is not None:
            try:
                self._chat_layout.removeWidget(self._thinking_line)
                self._thinking_line.deleteLater()
            except Exception:
                pass
            self._thinking_line = None

    def _add_welcome(self):
        self._add_system(t("ai_search.input_placeholder"))
        if not self._ai_available:
            self._add_system(t("ai_search.error_ai_unavailable"))

    def _scroll_to_bottom(self):
        bar = self._chat_scroll.verticalScrollBar()
        QtCore.QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _on_clear(self):
        # 清空消息（保留底部 stretch）
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._context = {}
        self._last_materials = []
        self._add_welcome()

    # ── 搜索 ────────────────────────────────────────────

    def _search(self, keywords, sub_library=""):
        """按关键词搜索并打分排序（含备注匹配），返回 to_dict 后的 dict 列表（前 30）"""
        if not self._manager or not keywords:
            return []
        return search_materials_with_notes(
            self._manager, keywords, sub_library, limit=30)

    def _run_search_and_render(self, keywords, sub_library, edited=False):
        mats = self._search(keywords, sub_library)
        self._last_materials = mats
        bubble = self._add_ai_bubble()
        bubble.set_keywords(keywords, edited=edited)
        bubble.set_results(mats)
        self._scroll_to_bottom()

    # ── 文本发送 → 意图分析 ─────────────────────────────

    def _on_send(self):
        text = self._input.text().strip()
        has_images = bool(self._compose_items)
        if (not text and not has_images) or self._busy:
            return
        if not self._manager:
            self._add_system(t("ai_search.error_need_library"))
            return
        self._input.clear()
        self._last_user_text = text
        context = dict(self._context)

        # ── 有组合图片 → 图搜图（图片 + 文本一起送 AI 分析） ──
        if has_images:
            pixmaps = [item["pix"] for item in self._compose_items]
            self._add_user_images(pixmaps, text)
            compose = list(self._compose_items)
            self._clear_compose()
            self._set_busy(True)
            self._add_thinking()

            def _work():
                payload = {"keywords": [], "sub_library": "", "note": "", "warn": ""}
                try:
                    blobs = []
                    for item in compose:
                        if item["kind"] == "file":
                            with open(item["path"], "rb") as f:
                                blobs.append(f.read())
                        else:
                            from ..core.zasset_io import ZassetIO
                            mat = item["material"]
                            zpath = mat.get("json_path") or mat.get("zasset_path") or ""
                            data = (ZassetIO.read_thumbnail(zpath)
                                    if zpath and os.path.isdir(zpath) else None)
                            if data:
                                blobs.append(data)
                    if self._assistant is not None:
                        if blobs and self._assistant.supports_vision:
                            res = self._assistant.analyze_image_intent(
                                blobs, text, context)
                            if res:
                                payload = res
                                payload["warn"] = ""
                            else:
                                payload["warn"] = "no_vision"
                        else:
                            payload["warn"] = "no_vision"
                            if text:
                                res = self._assistant.analyze_intent(text, context)
                                if res and res.get("keywords"):
                                    payload = res
                                    payload["warn"] = "no_vision"
                except Exception as e:
                    print(f"[AISearch] 图文分析异常: {e}")
                if not self._closed:
                    _safe_emit(self._imageIntentDone, payload)

            threading.Thread(target=_work, daemon=True).start()
            return

        # ── 纯文本 → 意图分析 ──
        self._add_user_text(text)
        self._set_busy(True)
        self._add_thinking()

        def _work():
            payload = {"keywords": [], "sub_library": "", "note": ""}
            try:
                if self._assistant is not None:
                    payload = self._assistant.analyze_intent(text, context)
            except Exception as e:
                print(f"[AISearch] 意图分析异常: {e}")
            if not self._closed:
                _safe_emit(self._intentDone, payload)

        threading.Thread(target=_work, daemon=True).start()

    def _on_intent_done(self, payload):
        if self._closed:
            return
        self._set_busy(False)
        self._remove_thinking()
        payload = payload or {}
        if payload.get("error") == "model_not_found":
            self._add_system(self._model_not_found_text())
        keywords = payload.get("keywords") or []
        if not keywords:
            self._add_system(t("ai_search.error_no_keywords"))
            return
        # 中英成对展开：AI 常只回英文，命中对照表的词补中文写法，显示与搜索一致
        keywords = _expand_bilingual(keywords)
        sub_lib = payload.get("sub_library", "")
        self._context = {
            "text": self._last_user_text,
            "keywords": keywords,
            "sub_library": sub_lib,
        }
        self._run_search_and_render(keywords, sub_lib, edited=False)

    def _model_not_found_text(self):
        """模型不存在/未安装时的友好提示（列出当前可用的模型）"""
        model = self._analyzer.model if self._analyzer else "?"
        installed = []
        try:
            probe = AIAnalyzer(provider="ollama") if AIAnalyzer else None
            installed = (probe.get_available_models()
                         if probe and probe.is_available() else [])
        except Exception:
            installed = []
        if installed:
            return t("ai_search.error_model_not_found",
                     model=model, available=", ".join(installed[:6]))
        return t("ai_search.error_model_not_found_simple", model=model)

    # ── 标签 chips 移除/编辑 → 免 AI 重搜 ───────────────

    def _on_bubble_keywords_changed(self, keywords):
        bubble = self.sender()
        if not keywords:
            if bubble is not None:
                bubble.set_keywords([], edited=True)
            self._add_system(t("ai_search.error_no_keywords"))
            return
        sub_lib = self._context.get("sub_library", "")
        mats = self._search(keywords, sub_lib)
        self._last_materials = mats
        self._context["keywords"] = keywords
        if bubble is not None:
            bubble.set_keywords(keywords, edited=True)
            bubble.set_results(mats)
        self._scroll_to_bottom()

    # ── 组合图片（多图 + 文本一起发送）/ 找相似 ──────────

    def _on_attach_images(self):
        """🖼 选择图片加入组合区（可多张，与文本一起发送）"""
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, t("ai_search.attach_images"), "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;所有文件 (*)")
        if not paths:
            return
        for p in paths:
            pix = QtGui.QPixmap(p)
            if pix.isNull():
                continue
            self._compose_items.append({"kind": "file", "path": p, "pix": pix})
        self._rebuild_compose()

    def _clear_compose(self):
        self._compose_items = []
        self._rebuild_compose()

    def _rebuild_compose(self):
        """重建组合图片条"""
        while self._compose_layout.count() > 1:  # 保留末尾 stretch
            item = self._compose_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for i, item in enumerate(self._compose_items):
            w = _ComposeItem(item["pix"], i)
            w.removeRequested.connect(self._on_compose_remove)
            self._compose_layout.insertWidget(self._compose_layout.count() - 1, w)
        self._compose_wrap.setVisible(bool(self._compose_items))
        self._compose_wrap.update()

    def _on_compose_remove(self, index):
        if 0 <= index < len(self._compose_items):
            self._compose_items.pop(index)
        self._rebuild_compose()

    def _start_image_search_from_material(self, material, caption, text=""):
        if self._busy:
            return
        mid = material.get("id", "")
        # 用户气泡：优先用已加载缓存，否则同步读取缩略图
        pix = None
        data = self._thumb_cache.get(mid)
        if data:
            pix = QtGui.QPixmap()
            pix.loadFromData(data)
        if pix is None or pix.isNull():
            pix = self._pixmap_for_material(material)
        if pix is not None and not pix.isNull():
            self._add_user_image(pix, caption)
        else:
            self._add_user_text(caption)
        name = material.get("name_cn") or material.get("name", "")
        text = text or t("ai_search.similar_caption", name=name)

        def _work():
            payload = {"keywords": [], "sub_library": "", "note": "", "warn": ""}
            try:
                from ..core.zasset_io import ZassetIO
                zpath = material.get("json_path") or material.get("zasset_path") or ""
                data = ZassetIO.read_thumbnail(zpath) if zpath and os.path.isdir(zpath) else None
                vision = bool(self._assistant and self._assistant.supports_vision)
                if not vision:
                    payload["warn"] = "no_vision"
                if data is not None:
                    res = self._assistant.analyze_image_intent(data, text) if self._assistant else None
                    if res:
                        payload = res
                        payload["warn"] = ""
                    else:
                        payload["keywords"] = list(material.get("tags", []) or [])
                        payload["sub_library"] = material.get("sub_library", "")
                else:
                    payload["keywords"] = list(material.get("tags", []) or [])
                    payload["sub_library"] = material.get("sub_library", "")
            except Exception as e:
                print(f"[AISearch] 找相似异常: {e}")
            if not self._closed:
                _safe_emit(self._imageIntentDone, payload)

        self._set_busy(True)
        self._add_thinking()
        threading.Thread(target=_work, daemon=True).start()

    def _on_image_intent_done(self, payload):
        if self._closed:
            return
        self._set_busy(False)
        self._remove_thinking()
        payload = payload or {}
        if payload.get("warn") == "no_vision":
            self._add_system(t("ai_search.error_no_vision"))
        keywords = payload.get("keywords") or []
        if not keywords:
            self._add_system(t("ai_search.error_no_keywords"))
            return
        # 中英成对展开：AI 常只回英文，命中对照表的词补中文写法，显示与搜索一致
        keywords = _expand_bilingual(keywords)
        sub_lib = payload.get("sub_library", "")
        self._context = {
            "text": self._last_user_text,
            "keywords": keywords,
            "sub_library": sub_lib,
        }
        self._run_search_and_render(keywords, sub_lib, edited=False)

    # ── 卡片操作 ────────────────────────────────────────

    def _on_card_import(self, material, fmt):
        path = material.get("json_path", "")
        if path:
            self.importAssetRequested.emit(path, fmt)

    def _on_card_locate(self, material):
        self.locateAssetRequested.emit(material)

    def _on_card_find_similar(self, material):
        self._start_image_search_from_material(material, caption="")

    def _on_card_send_to_ai(self, material):
        """把资产缩略图加入组合区（像上传图片一样，可多张），点击「发送」时才统一分析"""
        if self._busy:
            return
        pix = self._pixmap_for_material(material)
        if pix is None or pix.isNull():
            name = material.get("name_cn") or material.get("name", "")
            self._add_system(t("ai_search.no_thumbnail", name=name))
            return
        self._compose_items.append(
            {"kind": "material", "material": material, "pix": pix})
        self._rebuild_compose()
        self._scroll_to_bottom()

    def _resolve_full_material(self, material):
        """把轻量 material dict（心愿单等）补全为完整数据。

        预览窗口需要 is_zasset / thumb_bytes / node_type / exported_formats 等字段，
        轻量 dict 缺失会导致预览无图、属性不全；管理器找不到时原样返回。
        """
        if material.get("is_zasset") is not None:
            return material
        mgr = getattr(self, "_manager", None)
        if mgr is None:
            return material
        m = None
        mid = material.get("id", "")
        if mid:
            try:
                m = mgr.get_by_id(mid)
            except Exception:
                m = None
        if m is None:
            p = material.get("json_path", "")
            if p:
                try:
                    m = mgr.get_by_path(p)
                except Exception:
                    m = None
        if m is None:
            return material
        try:
            d = m.to_dict() if hasattr(m, "to_dict") else None
        except Exception:
            d = None
        return d if d else material

    def _on_card_detail(self, material):
        """查看详情：打开独立资产预览窗口（左大图右属性，不关闭聊天窗）"""
        if not material or not material.get("json_path"):
            return
        # 心愿单等轻量 dict 补全为完整数据，保证预览图与属性完整
        material = self._resolve_full_material(material)
        try:
            from .asset_preview_dialog import AssetPreviewDialog
            # 父级优先用主窗口（预览窗口右键菜单需复用主窗口的缩略图网格）
            host = self
            if self.parent() is not None and hasattr(self.parent(), "_thumbnail_grid"):
                host = self.parent()
            dlg = AssetPreviewDialog(material, host)
            self._preview_dlg = dlg  # 持有引用，防止被 GC
            dlg.show()
        except Exception as e:
            print(f"[AISearch] 打开预览窗口失败: {e}")
            # 兜底：定位到主窗口
            self.locateAssetRequested.emit(material)

    def _on_card_drag(self, ids, gx, gy):
        self.dragDroppedOnViewport.emit(ids, gx, gy)

    # ── 拖入输入区 → 图搜图 ─────────────────────────────

    def _on_input_drag_enter(self, event):
        mime = event.mimeData()
        if mime.hasFormat("application/x-material-ids") or mime.hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def _on_input_drop(self, event):
        mime = event.mimeData()
        if mime.hasFormat("application/x-material-ids"):
            try:
                ids = json.loads(bytes(mime.data("application/x-material-ids")).decode())
            except Exception:
                ids = []
            for m in self._last_materials:
                if m.get("id") in ids:
                    # 拖入输入区 → 把该资产缩略图加入组合区（可与文本一起发送）
                    pix = self._pixmap_for_material(m)
                    if pix is not None and not pix.isNull():
                        self._compose_items.append(
                            {"kind": "material", "material": m, "pix": pix})
                        self._rebuild_compose()
                        event.acceptProposedAction()
                        return
        event.ignore()

    # ── 缩略图懒加载 ────────────────────────────────────

    def request_thumb(self, card):
        mat = card.material()
        mid = mat.get("id", "")
        if not mid:
            return
        self._cards_by_id.setdefault(mid, [])
        if card not in self._cards_by_id[mid]:
            self._cards_by_id[mid].append(card)
        cached = self._thumb_cache.get(mid)
        if cached:
            card.set_thumb_bytes(cached)
            return
        if mid in self._thumb_pending:
            return
        zpath = mat.get("json_path") or mat.get("zasset_path") or ""
        if not zpath or not os.path.isdir(zpath):
            return
        self._thumb_pending.add(mid)

        def _read():
            try:
                from ..core.zasset_io import ZassetIO
                return ZassetIO.read_thumbnail(zpath)
            except Exception:
                return None

        fut = self._thumb_pool.submit(_read)

        def _done(f, m=mid):
            if self._closed:
                return
            try:
                data = f.result()
            except Exception:
                data = None
            _safe_emit(self._thumb_sig.loaded, m, data)

        fut.add_done_callback(_done)

    def _on_thumb_loaded(self, mid, data):
        if self._closed:
            return
        self._thumb_pending.discard(mid)
        if not data:
            return
        self._thumb_cache[mid] = data
        for card in self._cards_by_id.get(mid, []):
            try:
                card.set_thumb_bytes(data)
            except Exception:
                pass

    def _pixmap_for_material(self, material):
        """取资产缩略图为 QPixmap（读缓存，无则同步读磁盘）"""
        mid = material.get("id", "")
        data = self._thumb_cache.get(mid)
        if not data:
            try:
                from ..core.zasset_io import ZassetIO
                zpath = material.get("json_path") or material.get("zasset_path") or ""
                data = ZassetIO.read_thumbnail(zpath) if zpath and os.path.isdir(zpath) else None
                if data:
                    self._thumb_cache[mid] = data
            except Exception:
                data = None
        if not data:
            return None
        pix = QtGui.QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            return pix
        return None

    # ── 杂项 ────────────────────────────────────────────

    def _set_busy(self, busy):
        self._busy = busy
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)
        self._image_btn.setEnabled(not busy)

    def _on_add_custom_quick(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self, t("ai_search.quick_custom"),
            t("ai_search.quick_custom_placeholder"))
        if not ok or not text.strip():
            return
        name = text.strip()
        btn = QtWidgets.QPushButton(name)
        btn.setObjectName("chipBtn")
        btn.clicked.connect(lambda _=False, s=name: self._input.setText(s))
        # 插入到「+ 自定义指令」按钮之前
        self._quick_row.insertWidget(
            self._quick_row.indexOf(self._quick_custom_btn), btn)

    def closeEvent(self, event):
        self._closed = True
        try:
            self._thumb_pool.shutdown(wait=False)
        except Exception:
            pass
        super(AISearchDialog, self).closeEvent(event)
