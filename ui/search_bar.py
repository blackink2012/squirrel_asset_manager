from ..utils.maya_utils import get_qt_modules
from ..utils.settings import SettingsManager

try:
    from ..utils.i18n import t
except ImportError:
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

# QAction 位置兼容：PySide6 在 QtGui，PySide2 在 QtWidgets
try:
    _QAction = QtGui.QAction
except AttributeError:
    _QAction = QtWidgets.QAction

import functools as _functools


@_functools.lru_cache(maxsize=1024)
def _pinyin_char(ch):
    """单字符的拼音首字母（缓存结果：同字符只调一次 pypinyin，避免标签菜单批量构建卡顿）"""
    if not ch:
        return "#"
    if "\u4e00" <= ch <= "\u9fff":
        try:
            from pypinyin import pinyin as _py
            return _py(ch)[0][0][0].upper()
        except Exception:
            return ch
    return ch.upper()


def _pinyin_first_char(text):
    """获取文本的拼音首字母（中文取拼音首字母，英文直接大写）"""
    if not text:
        return "#"
    return _pinyin_char(text[0])


class SearchBarWidget(QtWidgets.QWidget):
    """增强搜索栏：防抖 + 内置清空按钮 + 标签筛选下拉 + 首字母快速定位"""

    searchChanged = QtCore.Signal(str)       # 关键词变化
    tagFilterChanged = QtCore.Signal(list)   # 标签筛选变化
    tagFilterCleared = QtCore.Signal()       # 标签筛选清除
    letterClicked = QtCore.Signal(str)       # 首字母定位（A-Z/#）
    aiSearchToggled = QtCore.Signal(bool)    # 「AI 搜索」勾选状态变化
    searchSubmit = QtCore.Signal(str)        # 输入框回车提交

    def __init__(self, parent=None, font_size=13):
        super(SearchBarWidget, self).__init__(parent)
        self._debounce_timer = QtCore.QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)  # 150ms 防抖
        self._debounce_timer.timeout.connect(self._emit_search)

        self._pending_text = ""
        self._common_tags = []        # 可用标签列表
        self._active_tags = set()     # 当前选中的标签
        self._menu_dirty = False      # 标签列表变化标记（菜单打开时懒重建）
        self._setup_ui(font_size)

    def set_font_size(self, font_size):
        """更新搜索栏所有控件字体大小和尺寸"""
        padding = max(7, int(font_size * 0.5))
        btn_size = font_size + padding * 2

        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: #333333;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: {padding}px 14px;
                color: #e0e0e0;
                font-size: {font_size}px;
                min-width: 200px;
            }}
            QLineEdit:focus {{ border-color: #5294e2; }}
        """)
        self._cancel_btn.setFixedSize(btn_size, btn_size)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                color: #e0e0e0;
                font-size: {font_size + 1}px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #c0392b; border-color: #e74c3c; color: #fff; }}
        """)
        self._tag_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: {padding}px 14px;
                color: #e0e0e0;
                font-size: {font_size}px;
            }}
            QPushButton:hover {{ background-color: #4a4a4a; }}
            QPushButton:checked {{ background-color: #2a5a8a; border-color: #5294e2; }}
        """)

    def set_common_tags(self, tags):
        """设置可用标签列表（由主窗口传入 config 中的 common_tags）。

        只存数据并标记脏，菜单项在打开时懒重建——避免启动/切库时
        对大量标签批量执行 pypinyin 拼音转换阻塞主线程。
        """
        self._common_tags = list(tags)
        self._menu_dirty = True

    def _setup_ui(self, font_size=13):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        padding = max(7, int(font_size * 0.5))
        btn_size = font_size + padding * 2

        # --- 搜索输入框 ---
        self._search_input = QtWidgets.QLineEdit()
        self._search_input.setPlaceholderText(t("search_bar.search_placeholder"))
        self._search_input.setClearButtonEnabled(True)  # 内置清空按钮
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: #333333;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: {padding}px 14px;
                color: #e0e0e0;
                font-size: {font_size}px;
                min-width: 200px;
            }}
            QLineEdit:focus {{ border-color: #5294e2; }}
        """)
        self._search_input.textChanged.connect(self._on_text_changed)
        self._search_input.returnPressed.connect(
            lambda: self.searchSubmit.emit(self._search_input.text().strip()))
        layout.addWidget(self._search_input, 1)

        # --- 取消搜索按钮 ---
        self._cancel_btn = QtWidgets.QPushButton("✕")
        self._cancel_btn.setToolTip(t("search_bar.cancel_search"))
        self._cancel_btn.setFixedSize(btn_size, btn_size)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                color: #e0e0e0;
                font-size: {font_size + 1}px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #c0392b; border-color: #e74c3c; color: #fff; }}
        """)
        self._cancel_btn.clicked.connect(self._cancel_search)
        layout.addWidget(self._cancel_btn)

        # --- AI 搜索开关 ---
        self._ai_search_cb = QtWidgets.QCheckBox(t("search_bar.ai_search"))
        self._ai_search_cb.setToolTip(t("search_bar.ai_search.tooltip"))
        self._ai_search_cb.setStyleSheet("""
            QCheckBox { color: #a0a0a0; font-size: %dpx; spacing: 6px; }
            QCheckBox:hover { color: #c8c8c8; }
            QCheckBox:checked { color: #5294e2; font-weight: bold; }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border: 1px solid #555555;
                border-radius: 3px;
                background-color: #2a2a2a;
            }
            QCheckBox::indicator:hover { border-color: #5294e2; }
            QCheckBox::indicator:checked {
                border-color: #5294e2;
                background-color: #5294e2;
            }
        """ % max(12, font_size - 1))
        self._ai_search_cb.toggled.connect(self.aiSearchToggled.emit)
        layout.addWidget(self._ai_search_cb)

        # --- 标签筛选按钮 ---
        self._tag_btn = QtWidgets.QPushButton(t("search_bar.tag_select"))
        self._tag_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: {padding}px 14px;
                color: #e0e0e0;
                font-size: {font_size}px;
            }}
            QPushButton:hover {{ background-color: #4a4a4a; }}
            QPushButton:checked {{ background-color: #2a5a8a; border-color: #5294e2; }}
        """)
        self._tag_btn.setCheckable(True)
        self._tag_btn.setChecked(False)
        layout.addWidget(self._tag_btn)

        # --- 标签弹出菜单 ---
        self._tag_menu = QtWidgets.QMenu(t("search_bar.tag_select"), self)  # 标题影响撕离窗口标题
        self._tag_menu.setTearOffEnabled(True)  # 可撕离，独立窗口方便多选
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        self._tag_menu.setStyleSheet(f"""
            QMenu {{
                background-color: #2d2d2d;
                border: 1px solid #4a4a4a;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px 6px 8px;
                color: #e0e0e0;
                font-size: {font_size}px;
            }}
            QMenu::item:selected {{ background-color: #3a5a8a; }}
            QMenu::indicator {{ width: 16px; height: 16px; }}
            QMenu::indicator:checked {{ image: none; background-color: #5294e2; border-radius: 2px; }}
        """)
        self._tag_btn.setMenu(self._tag_menu)

        # 连接标签按钮点击行为 —— 展开菜单
        self._tag_btn.clicked.connect(self._on_tag_btn_clicked)

        # 标签菜单关闭后取消 checked 状态
        self._tag_menu.aboutToHide.connect(self._on_tag_menu_closed)

        # 标签菜单打开时懒重建（仅标签列表变化后重建，避免启动路径批量构建卡顿）
        self._tag_menu.aboutToShow.connect(self._on_tag_menu_about_to_show)

    def _on_tag_menu_about_to_show(self):
        """菜单即将打开：标签列表变化时才重建菜单项"""
        if getattr(self, '_menu_dirty', False):
            self._rebuild_tag_menu()
            self._menu_dirty = False

    def _rebuild_tag_menu(self):
        """根据 _common_tags 重建标签列表（无闪烁：只替换标签 action 项）"""
        # 兼容 PySide6/shiboken6 与 PySide2/shiboken2（Maya 2022~2024 只有 shiboken2）
        try:
            from shiboken6 import isValid as _shiboken_is_valid
        except ImportError:
            try:
                from shiboken2 import isValid as _shiboken_is_valid
            except ImportError:
                _shiboken_is_valid = None
        # ── 检查 _tag_sep 是否有效（tear-off 关闭后可能被销毁） ──
        try:
            if _shiboken_is_valid is not None:
                valid = hasattr(self, '_tag_sep') and _shiboken_is_valid(self._tag_sep)
            else:
                valid = hasattr(self, '_tag_sep')
        except Exception:
            valid = hasattr(self, '_tag_sep')
        if not valid:
            self._tag_menu.clear()
            self._tag_sep = self._tag_menu.addSeparator()
            self._clear_action = self._tag_menu.addAction(t("search_bar.clear_all_tag_filters"))
            self._clear_action.triggered.connect(self._clear_all_tags)
        else:
            # 清除上次的标签 actions（分隔线前的所有 action）
            for action in list(self._tag_menu.actions()):
                if action is self._tag_sep:
                    break
                self._tag_menu.removeAction(action)

        # ── 重建标签列表 ──
        self._tag_actions = []
        if not self._common_tags:
            na = _QAction(t("search_bar.no_available_tags"), self._tag_menu)
            na.setEnabled(False)
            self._tag_actions.append(na)
            self._tag_menu.insertAction(self._tag_sep, na)
            return

        # 每标签只算一次拼音首字母（lru_cache 已按字符缓存，sort 与循环共用）
        pairs = [(_pinyin_first_char(tag), tag) for tag in self._common_tags]
        pairs.sort(key=lambda p: p[0])
        for prefix, tag in pairs:
            action = _QAction(f"{prefix}  {tag}", self._tag_menu)
            action.setCheckable(True)
            action.setChecked(tag in self._active_tags)
            action.toggled.connect(lambda checked, t=tag: self._on_tag_toggled(t, checked))
            self._tag_actions.append(action)
            self._tag_menu.insertAction(self._tag_sep, action)

    def _on_tag_btn_clicked(self, checked):
        """点击标签按钮时展开菜单"""
        # QPushButton with menu will handle this, but we need sync
        pass

    def _on_tag_menu_closed(self):
        """菜单关闭后还原按钮状态"""
        self._tag_btn.setChecked(False)

    def _on_tag_toggled(self, tag, checked):
        """单个标签切换"""
        if checked:
            self._active_tags.add(tag)
        else:
            self._active_tags.discard(tag)

        if self._active_tags:
            self._tag_btn.setText(t("search_bar.tag_select_with_count", count=len(self._active_tags)))
            self.tagFilterChanged.emit(sorted(self._active_tags))
            self._search_input.blockSignals(True)
            self._search_input.setText(" ".join(sorted(self._active_tags)))
            self._search_input.blockSignals(False)
        else:
            self._tag_btn.setText(t("search_bar.tag_select"))
            self._search_input.blockSignals(True)
            self._search_input.clear()
            self._search_input.blockSignals(False)
            self.tagFilterCleared.emit()

    def _clear_all_tags(self):
        """清除所有标签筛选"""
        self._active_tags.clear()
        self._tag_btn.setText(t("search_bar.tag_select"))
        self._menu_dirty = True  # 勾选状态变化，菜单下次打开时重建
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self.tagFilterCleared.emit()

    def _cancel_search(self):
        """取消搜索：清空搜索框和标签筛选"""
        self._search_input.clear()
        self._clear_all_tags()

    def _on_text_changed(self, text):
        """文本变化 → 启动防抖定时器"""
        self._pending_text = text
        self._debounce_timer.start()

    def _emit_search(self):
        """防抖到期，发出搜索信号"""
        self.searchChanged.emit(self._pending_text)

    def set_text(self, text):
        """外部设置搜索文本（如清空时）"""
        self._search_input.setText(text)

    def set_active_tags(self, tags):
        """外部设置标签筛选（如从详情面板点击标签时同步）"""
        tags = list(tags) if tags else []
        if tags:
            self._active_tags = set(tags)
            self._tag_btn.setText(t("search_bar.tag_select_with_count", count=len(tags)))
            self._search_input.blockSignals(True)
            self._search_input.setText(" ".join(tags))
            self._search_input.blockSignals(False)
            self._menu_dirty = True  # 勾选状态变化，菜单下次打开时重建
            self.tagFilterChanged.emit(tags)
        else:
            self._clear_all_tags()

    def text(self):
        return self._search_input.text()

    def is_ai_mode(self):
        """是否开启 AI 语义搜索"""
        return self._ai_search_cb.isChecked()

    def set_ai_mode(self, checked):
        """外部设置 AI 搜索开关（不触发 toggled 信号链）"""
        self._ai_search_cb.blockSignals(True)
        self._ai_search_cb.setChecked(bool(checked))
        self._ai_search_cb.blockSignals(False)

    def clear(self):
        """清空搜索和标签筛选"""
        self._search_input.clear()
        self._clear_all_tags()
