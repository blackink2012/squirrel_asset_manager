import json
import os

from ..utils.maya_utils import get_qt_modules
from ..utils.mock_data import DEFAULT_CATEGORIES
from ..utils.settings import SettingsManager

# config.json 路径（与 manager.py 保持一致）
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "Assets", "preset", "config.json")

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

# ── 核心顶级分类（不可删除） ──────────────────────
_CORE_SUB_LIBS = frozenset({"materials", "models", "lights", "textures", "scenes", "hdr", "ani"})

# ── 复合分类 ID 工具 ─────────────────────────────
# 用 "||" 拼接 root_lib 和 short_id，彻底避免跨子库同名分类歧义
CAT_ID_SEP = "||"

def join_cat_id(root_lib: str, short_id: str) -> str:
    """将 root_lib 和 short_id 拼接为复合 ID（如 "textures||AAAcustom"）"""
    if not short_id or short_id == "all":
        return short_id or "all"
    return f"{root_lib}{CAT_ID_SEP}{short_id}"

def split_cat_id(composite: str):
    """将复合 ID 拆分为 (root_lib, short_id)；非复合格式返回 ("", composite)"""
    if not composite or composite == "all":
        return ("materials", composite or "all")
    if CAT_ID_SEP in composite:
        parts = composite.split(CAT_ID_SEP, 1)
        return (parts[0], parts[1])
    return ("", composite)


class CategoryTreeWidget(QtWidgets.QWidget):
    categorySelected = QtCore.Signal(str, list, str)  # (category_id_composite, descendant_ids, root_lib)
    categoriesMultiSelected = QtCore.Signal(list, list, str)  # ([cat_ids], [all_desc_ids], root_lib)
    categoryAdded = QtCore.Signal(dict)
    topLevelCategoryAdded = QtCore.Signal(str, str, str, str)  # (cat_id, name_cn, root_lib, cat_type) — 独立路径，不与 categoryAdded 混用
    categoryDeleted = QtCore.Signal(str, str, str)  # (cat_id, root_lib, parent_id)
    categoryEdited = QtCore.Signal(str, str, str)  # (category_id, new_name_cn, root_lib)
    openFolderRequested = QtCore.Signal(str)
    categoryMoved = QtCore.Signal(str, str)            # (cat_id, new_parent_id)
    materialDropOnCategory = QtCore.Signal(str, str, str)  # (material_id, category_id, root_lib)

    CATEGORY_COLORS = {
        "all": "#5294e2",
        "metal": "#808080", "fabric": "#C0392B", "plastic": "#2980B9",
        "glass": "#1ABC9C", "skin": "#E67E22", "wood": "#A0522D",
        "stone": "#95A5A6", "liquid": "#3498DB", "foliage": "#27AE60",
        "custom": "#8E44AD",
    }

    LIBRARIES = [
        ("asset",    "SquirrelLib", ""),
    ]

    SUB_LIBRARIES = [
        ("material", "材质"),
        ("model",    "模型"),
        ("light",    "灯光"),
        ("texture",  "贴图"),
        ("scene",    "场景"),
        ("hdr",      "HDR"),
    ]

    def __init__(self, parent=None):
        super(CategoryTreeWidget, self).__init__(parent)
        self._categories = list(DEFAULT_CATEGORIES)
        self._active_category = "all"
        self._setup_ui()
        self._populate_tree()
        self._select_all()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(18)
        self._tree.setAnimated(True)
        self._tree.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._tree.setDragEnabled(False)
        self._tree.setAcceptDrops(True)
        self._tree.setDropIndicatorShown(False)
        self._tree.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DropOnly)
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._tree.setStyleSheet("""
            QTreeWidget {
                background-color: #252525;
                border: none;
                color: #d0d0d0;
                font-size: 13px;
                outline: none;
            }
            QTreeWidget::item {
                padding: 6px 6px;
                min-height: 24px;
                border: none;
            }
            QTreeWidget::item:selected {
                background-color: #2a3a5a;
                color: #5294e2;
            }
            QTreeWidget::item:hover:!selected {
                background-color: #333333;
            }
            QTreeWidget::item:hover:selected {
                background-color: #2a3a5a;
                color: #5294e2;
            }
        """)
        self._tree.itemClicked.connect(self._on_item_single_clicked)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)

        # 拖放处理：支持分类内部拖拽 + 材质卡片外部拖放
        self_ref = self
        _orig_drop = self._tree.dropEvent
        _orig_drag_enter = self._tree.dragEnterEvent
        _orig_drag_move = self._tree.dragMoveEvent

        def _accept_material_drag(event):
            """检查是否为材质卡片拖放，是则接受并设置 CopyAction"""
            mime = event.mimeData()
            if mime.hasFormat("application/x-material-ids") or mime.hasFormat("application/x-material-id"):
                event.setDropAction(QtCore.Qt.DropAction.CopyAction)
                event.accept()
                return True
            # PySide6 跨控件拖拽时自定义 MIME 可能丢失，退而检查 text 是否为 JSON 数组
            if mime.hasText():
                txt = mime.text()
                if txt.startswith("[") and txt.endswith("]"):
                    event.setDropAction(QtCore.Qt.DropAction.CopyAction)
                    event.accept()
                    return True
            return False

        _drag_hover_item = [None]

        def _clear_drag_hover():
            if _drag_hover_item[0] is not None:
                try:
                    _drag_hover_item[0].setBackground(0, QtCore.Qt.BrushStyle.NoBrush)
                    _drag_hover_item[0].setForeground(0, QtGui.QColor("#d0d0d0"))
                except RuntimeError:
                    pass
                _drag_hover_item[0] = None

        def _set_drag_hover(item):
            _clear_drag_hover()
            if item:
                item.setBackground(0, QtGui.QBrush(QtGui.QColor("#2a3a4a")))
                item.setForeground(0, QtGui.QColor("#5294e2"))
                _drag_hover_item[0] = item

        def _on_drag_enter(event):
            if _accept_material_drag(event):
                return
            _orig_drag_enter(event)

        def _on_drag_move(event):
            mime = event.mimeData()
            is_material = (mime.hasFormat("application/x-material-ids")
                           or mime.hasFormat("application/x-material-id")
                           or (mime.hasText() and mime.text().startswith("[") and mime.text().endswith("]")))
            if is_material:
                item = self_ref._tree.itemAt(event.position().toPoint())
                if item and item.data(0, QtCore.Qt.ItemDataRole.UserRole) != "all":
                    _set_drag_hover(item)
                else:
                    _clear_drag_hover()
                event.setDropAction(QtCore.Qt.DropAction.CopyAction)
                event.accept()
                return
            _orig_drag_move(event)

        def _on_drop(event):
            _clear_drag_hover()
            mime = event.mimeData()
            import json
            ids = None

            def _get_target_root_lib(target):
                if target:
                    return target.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) or ""
                return ""

            # 尝试从自定义 MIME 类型读取（可能被 PySide6 丢弃）
            ids_bytes = mime.data("application/x-material-ids")
            if ids_bytes:
                try:
                    ids = json.loads(ids_bytes.data().decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            # 自定义 MIME 丢失时退而从 text 读取
            if ids is None and mime.hasText():
                try:
                    ids = json.loads(mime.text())
                except (json.JSONDecodeError, TypeError):
                    pass

            if ids is not None and isinstance(ids, list) and len(ids) > 0:
                target = self_ref._tree.itemAt(event.position().toPoint())
                tgt_id = target.data(0, QtCore.Qt.ItemDataRole.UserRole) if target else None
                root_lib = _get_target_root_lib(target)
                if target and tgt_id and tgt_id != "all":
                    for mid in ids:
                        self_ref.materialDropOnCategory.emit(mid, tgt_id, root_lib)
                    event.setDropAction(QtCore.Qt.DropAction.CopyAction)
                    event.accept()
                    return

            # 单选拖拽 (legacy)
            mat_id_bytes = mime.data("application/x-material-id")
            if mat_id_bytes:
                mat_id = mat_id_bytes.data().decode()
                target = self_ref._tree.itemAt(event.position().toPoint())
                tgt_id = target.data(0, QtCore.Qt.ItemDataRole.UserRole) if target else None
                root_lib = _get_target_root_lib(target)
                if mat_id and tgt_id and tgt_id != "all":
                    self_ref.materialDropOnCategory.emit(mat_id, tgt_id, root_lib)
                    event.setDropAction(QtCore.Qt.DropAction.CopyAction)
                    event.accept()
                    return

            # 分类内部拖拽
            target = self_ref._tree.itemAt(event.position().toPoint())
            src = self_ref._tree.currentItem()
            if target and src:
                src_id = src.data(0, QtCore.Qt.ItemDataRole.UserRole)
                tgt_id = target.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if src_id and tgt_id and src_id != "all" and tgt_id != "all":
                    if src_id != tgt_id:
                        self_ref.categoryMoved.emit(src_id, tgt_id)

            _orig_drop(event)

        self._tree.dragEnterEvent = _on_drag_enter
        self._tree.dragMoveEvent = _on_drag_move
        self._tree.dragLeaveEvent = lambda e: _clear_drag_hover()
        self._tree.dropEvent = _on_drop

        layout.addWidget(self._tree)

        add_btn = QtWidgets.QPushButton("+ \u6dfb\u52a0\u5206\u7c7b")
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #5294e2;
                border: none;
                padding: 10px;
                font-size: 12px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #333333;
                color: #6ab0ff;
            }
        """)
        add_btn.clicked.connect(lambda: self._on_add_category())
        layout.addWidget(add_btn)

    def _populate_tree(self):
        """完全镜像文件系统：self._categories 来自 get_category_tree()"""
        self._tree.clear()
        for cat in self._categories:
            self._add_category_item(None, cat, 0)

    def _display_name(self, cat):
        """优先 name_cn，其次 name，最后 id"""
        return cat.get("name_cn") or cat.get("name") or cat.get("id") or "?"

    def _add_category_item(self, parent_item, cat, depth=0, root_lib=None):
        item = QtWidgets.QTreeWidgetItem()
        display = cat.get("name_cn") or cat.get("name") or cat.get("id") or "?"
        children = cat.get("children", [])
        indent = "  " * (depth + 1)

        # 顶级节点：优先使用 type 字段（FolderMetadata 中存储的类型），
        # 确保自定义顶级分类的右键菜单 root_lib 与其实际类型一致
        if parent_item is None:
            root_lib = cat.get("type") or cat["id"]

        count = cat.get("material_count", 0)
        if count > 0:
            item.setText(0, f"{indent}{display}  ({count})")
        else:
            item.setText(0, f"{indent}{display}")

        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, cat["id"])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, bool(cat.get("children")))
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 2, depth)
        # 存储所属根子库 ID，用于区分不同子库下同名的子分类
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 3, root_lib)

        color = self.CATEGORY_COLORS.get(cat["id"], "#d0d0d0")
        if parent_item is not None:
            item.setForeground(0, QtGui.QColor("#d0d0d0"))
            item.setFont(0, QtGui.QFont())

        children = cat.get("children", [])
        if children:
            for child_cat in children:
                self._add_category_item(item, child_cat, depth + 1, root_lib)

        if parent_item is None:
            self._tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)

        return item

    def _collect_descendant_ids(self, cat):
        ids = [cat["id"]]
        for child in cat.get("children", []):
            ids.extend(self._collect_descendant_ids(child))
        return ids

    def _collect_item_descendant_ids(self, item):
        """从 QTreeWidgetItem 的子树收集所有后代分类 ID（不受 _find_category 同名歧义影响）"""
        ids = []
        for i in range(item.childCount()):
            child = item.child(i)
            cid = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if cid:
                ids.append(cid)
                ids.extend(self._collect_item_descendant_ids(child))
        return ids

    def _find_category(self, cat_id, root_lib=None):
        """递归搜索 cat_id 在所有层级中的位置，返回 (node, parent_node)。
        传 root_lib 可区分跨子库同名分类。cat_id 可为复合 ID。"""
        # 复合 ID → 拆分为 (root_lib, short_id)
        if CAT_ID_SEP in cat_id:
            rl, short = split_cat_id(cat_id)
            if root_lib is None:
                root_lib = rl
            cat_id = short
        def _search(nodes, parent=None):
            for cat in nodes:
                # 检查 type 字段（即 root_lib）
                cat_type = cat.get("type", "")
                if cat["id"] == cat_id and (root_lib is None or cat_type == root_lib):
                    return cat, parent
                if cat.get("children"):
                    result = _search(cat["children"], cat)
                    if result[0]:
                        return result
            return None, None
        return _search(self._categories)

    def _select_all(self):
        if self._tree.topLevelItemCount() > 0:
            self._tree.setCurrentItem(self._tree.topLevelItem(0))
            self._active_category = "all"
            self.categorySelected.emit("all", [], "materials")

    def _select_category(self, category_id):
        """选中分类并发射信号（category_id 可为 short_id 或 composite，含 root_lib 信息）"""
        # 从 tree item 读取 root_lib
        root_lib = ""
        item = self._tree.currentItem()
        if item:
            rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
            if rl:
                root_lib = rl
        # 存复合 ID（若无 root_lib 但有 separator，说明已是复合 ID）
        if root_lib and category_id != "all":
            composite = join_cat_id(root_lib, category_id)
        elif CAT_ID_SEP in category_id:
            composite = category_id
            root_lib, category_id = split_cat_id(category_id)
        else:
            composite = category_id
            root_lib = category_id
        self._active_category = composite
        descendant_ids = [category_id]
        if category_id != "all":
            if item:
                descendant_ids = [category_id] + self._collect_item_descendant_ids(item)
            else:
                cat, _ = self._find_category(category_id, root_lib)
                if cat:
                    descendant_ids = self._collect_descendant_ids(cat)
        self.categorySelected.emit(composite, descendant_ids, root_lib)

    def _on_item_single_clicked(self, item, column):
        """单击→选择分类（不展开），展开由系统箭头处理
        
        多选模式下（Ctrl+点击/Shift+点击），由 _on_selection_changed 处理，此方法跳过。
        """
        selected_count = len(self._tree.selectedItems())
        if selected_count > 1:
            return
        category_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if category_id:
            # 显式清除旧选择再 setCurrentItem — ExtendedSelection 模式下
            # setCurrentItem 不会清空已有选中，会触发 _on_selection_changed 误入多选分支，
            # 进而导致 _on_categories_multi_selected → 清空标签 → 错误搜索（跨库串数据）
            self._tree.clearSelection()
            self._tree.setCurrentItem(item)
            self._select_category(category_id)

    def _on_selection_changed(self):
        """选择变化处理（当前仅处理多选，单选由 _on_item_single_clicked 处理）"""
        selected = self._tree.selectedItems()
        if len(selected) <= 1:
            return  # 单选由 _on_item_single_clicked 或程序化 _select_* 处理
        
        # 多选：收集所有选中分类的 ID 和后代 ID
        cat_ids = []
        all_desc_ids = []
        root_lib = "materials"
        for item in selected:
            cat_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if cat_id and cat_id != "all":
                cat_ids.append(cat_id)
                all_desc_ids.append(cat_id)
                all_desc_ids.extend(self._collect_item_descendant_ids(item))
                rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
                if rl:
                    root_lib = rl
        
        if cat_ids:
            # 存复合 ID（逗号分隔多个短 ID），root_lib 取最后一个选中项
            self._active_category = ",".join(join_cat_id(root_lib, cid) for cid in cat_ids)
            self.categoriesMultiSelected.emit(cat_ids, all_desc_ids, root_lib)

    def _on_item_double_clicked(self, item, column):
        """双击→展开/折叠 + 选择"""
        category_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if category_id:
            self._tree.clearSelection()
            self._tree.setCurrentItem(item)
            # 展开时临时断开itemExpanded信号，避免双重级联
            if item.childCount() > 0:
                self._tree.itemExpanded.disconnect(self._on_item_expanded)
                item.setExpanded(not item.isExpanded())
                self._tree.itemExpanded.connect(self._on_item_expanded)
            self._select_category(category_id)

    def _on_item_expanded(self, item):
        """展开节点时不再强制切换选中，避免级联搜索刷新"""

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)

        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color:#2a2a2a; color:#d0d0d0; border:1px solid #3a3a3a; padding:4px; }}
            QMenu::item {{ padding:6px 24px 6px 14px; font-size:{font_size}px; }}
            QMenu::item:selected {{ background-color:#2a3a5a; color:#5294e2; }}
        """)

        if item is not None:
            cat_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            is_parent = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            depth = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 2)

            if cat_id != "all":
                # 子库根（depth==0）或子分类（depth>0）均可添加子分类
                rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
                menu.addAction("+ \u6dfb\u52a0\u5b50\u5206\u7c7b").triggered.connect(
                    lambda checked=False, cid=cat_id, root_lib=rl: self._on_add_child_category(cid, root_lib)
                )
                menu.addSeparator()
                menu.addAction("\ud83d\udcc2 \u6253\u5f00\u6587\u4ef6\u5939").triggered.connect(
                    lambda checked=False, cid=cat_id: self.openFolderRequested.emit(cid)
                )
                menu.addSeparator()
                menu.addAction("\u2795 \u79fb\u52a8\u5230...").triggered.connect(
                    lambda checked=False, cid=cat_id: self._on_move_category(cid)
                )
                menu.addSeparator()
                menu.addAction("\u7f16\u8f91\u6613\u8bfb\u540d").triggered.connect(
                    lambda checked=False, cid=cat_id: self._on_edit_category(cid)
                )
                # 核心顶级分类不可删除
                if cat_id not in _CORE_SUB_LIBS:
                    menu.addAction("\u5220\u9664\u5206\u7c7b").triggered.connect(
                        lambda checked=False, cid=cat_id: self._on_delete_category(cid)
                    )
                if is_parent:
                    menu.addSeparator()
                    menu.addAction("\u5168\u90e8\u5c55\u5f00").triggered.connect(
                        lambda checked=False, it=item: self._tree.expandItem(it)
                    )
                    menu.addAction("\u5168\u90e8\u6298\u53e0").triggered.connect(
                        lambda checked=False, it=item: self._tree.collapseItem(it)
                    )
            else:
                # 从当前树实例的 currentItem 提取 root_lib（比 global _current_root_lib 更准）
                curr = self._tree.currentItem()
                all_rl = curr.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) if curr else None
                menu.addAction("+ \u6dfb\u52a0\u9876\u7ea7\u5206\u7c7b").triggered.connect(
                    lambda checked=False, root_lib=all_rl: self._on_add_category(root_lib=root_lib)
                )
        else:
            # 从当前树实例的 currentItem 提取 root_lib
            curr = self._tree.currentItem()
            blank_rl = curr.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) if curr else None
            menu.addAction("+ \u6dfb\u52a0\u9876\u7ea7\u5206\u7c7b").triggered.connect(
                lambda checked=False, root_lib=blank_rl: self._on_add_category(root_lib=root_lib)
            )

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _on_add_category(self, parent_id=None, root_lib=None):
        """添加分类：双输入对话框 → 中文名 + 英文 ID"""
        title = f"\u6dfb\u52a0\u5b50\u5206\u7c7b\u5230 {parent_id}" if parent_id else "\u6dfb\u52a0\u9876\u7ea7\u5206\u7c7b"

        # 自定义双输入对话框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setFixedSize(380, 180 if parent_id is None else 150)
        dialog.setStyleSheet("background-color: #2a2a2a;")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(8)

        # 第一行：文件夹名（唯一标识）
        id_layout = QtWidgets.QHBoxLayout()
        id_label = QtWidgets.QLabel("\u6587\u4ef6\u5939\u540d:")
        id_label.setFixedWidth(80)
        id_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        id_input = QtWidgets.QLineEdit()
        id_input.setPlaceholderText("\u5982\uff1ametal_copper")
        id_input.setStyleSheet("background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0;")
        id_layout.addWidget(id_label)
        id_layout.addWidget(id_input)
        layout.addLayout(id_layout)

        # 第二行：易读名（可选）
        cn_layout = QtWidgets.QHBoxLayout()
        cn_label = QtWidgets.QLabel("\u6613\u8bfb\u540d:")
        cn_label.setFixedWidth(80)
        cn_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        cn_input = QtWidgets.QLineEdit()
        cn_input.setPlaceholderText("\u5982\uff1a\u94dc\uff08\u4e0d\u586b\u5219\u7528\u6587\u4ef6\u5939\u540d\uff09")
        cn_input.setStyleSheet("background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0;")
        cn_layout.addWidget(cn_label)
        cn_layout.addWidget(cn_input)
        layout.addLayout(cn_layout)

        # 第三行：类型 type（仅顶级分类可定义，子分类继承父类型）
        # 改为下拉菜单，选项来自 config.json 的 sub_libraries，
        # 确保类型始终是合法子库以适配个性化右键菜单
        if parent_id is None:
            type_layout = QtWidgets.QHBoxLayout()
            type_label = QtWidgets.QLabel("\u7c7b\u578b(type):")
            type_label.setFixedWidth(80)
            type_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
            type_combo = QtWidgets.QComboBox()
            type_combo.setStyleSheet("QComboBox { background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0; } QComboBox::drop-down { border: none; } QComboBox QAbstractItemView { background-color: #2a2a2a; color: #d0d0d0; selection-background-color: #2a3a5a; }")
            # 从 config.json 读取子库列表作为下拉选项
            try:
                with open(_CONFIG_PATH, 'r', encoding='utf-8') as _f:
                    _cfg = json.loads(_f.read())
                _sub_libs = _cfg.get("sub_libraries", {})
            except Exception:
                _sub_libs = {
                    "materials": "\u6750\u8d28", "models": "\u6a21\u578b", "lights": "\u706f\u5149",
                    "textures": "\u8d34\u56fe", "scenes": "\u573a\u666f", "hdr": "HDR", "ani": "\u52a8\u6001"
                }
            for _key, _label in _sub_libs.items():
                type_combo.addItem(f"{_label} ({_key})", _key)
            type_layout.addWidget(type_label)
            type_layout.addWidget(type_combo)
            layout.addLayout(type_layout)
        else:
            type_combo = None

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QtWidgets.QPushButton("\u53d6\u6d88")
        cancel_btn.setStyleSheet("QPushButton { background-color: #3a3a3a; color: #a0a0a0; border: none; padding: 6px 16px; border-radius: 4px; } QPushButton:hover { color: #e0e0e0; }")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QtWidgets.QPushButton("\u786e\u5b9a")
        ok_btn.setStyleSheet("QPushButton { background-color: #5294e2; color: #fff; border: none; padding: 6px 16px; border-radius: 4px; } QPushButton:hover { background-color: #6ab0ff; }")
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        name_cn = cn_input.text().strip()
        cat_id = id_input.text().strip()
        if not cat_id:
            QtWidgets.QMessageBox.warning(self, "\u63d0\u793a", "\u6587\u4ef6\u5939\u540d\u4e0d\u80fd\u4e3a\u7a7a")
            return
        # 易读名为空则用文件夹名
        if not name_cn:
            name_cn = cat_id
        cat_type = type_combo.currentData() if parent_id is None else None

        self.categoryAdded.emit({"id": cat_id, "name_cn": name_cn, "parent": parent_id,
                                 "root_lib": root_lib, "type": cat_type})

    def _on_add_child_category(self, parent_id, root_lib=None):
        self._on_add_category(parent_id, root_lib=root_lib)

    # ── 独立路径：右键子库根节点添加顶级分类 ────────────

    def _show_top_level_category_dialog(self, root_lib):
        """独立对话框 + 独立信号，与 categoryAdded/on_category_added 完全隔离"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("\u6dfb\u52a0\u9876\u7ea7\u5206\u7c7b")
        dialog.setFixedSize(380, 150)
        dialog.setStyleSheet("background-color: #2a2a2a;")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(8)

        id_layout = QtWidgets.QHBoxLayout()
        id_label = QtWidgets.QLabel("\u6587\u4ef6\u5939\u540d:")
        id_label.setFixedWidth(80)
        id_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        id_input = QtWidgets.QLineEdit()
        id_input.setPlaceholderText("\u5982\uff1ametal_copper")
        id_input.setStyleSheet("background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0;")
        id_layout.addWidget(id_label)
        id_layout.addWidget(id_input)
        layout.addLayout(id_layout)

        cn_layout = QtWidgets.QHBoxLayout()
        cn_label = QtWidgets.QLabel("\u6613\u8bfb\u540d:")
        cn_label.setFixedWidth(80)
        cn_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        cn_input = QtWidgets.QLineEdit()
        cn_input.setPlaceholderText("\u5982\uff1a\u94dc\uff08\u4e0d\u586b\u5219\u7528\u6587\u4ef6\u5939\u540d\uff09")
        cn_input.setStyleSheet("background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0;")
        cn_layout.addWidget(cn_label)
        cn_layout.addWidget(cn_input)
        layout.addLayout(cn_layout)

        type_layout = QtWidgets.QHBoxLayout()
        type_label = QtWidgets.QLabel("\u7c7b\u578b(type):")
        type_label.setFixedWidth(80)
        type_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        type_combo = QtWidgets.QComboBox()
        type_combo.setStyleSheet("QComboBox { background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0; } QComboBox::drop-down { border: none; } QComboBox QAbstractItemView { background-color: #2a2a2a; color: #d0d0d0; selection-background-color: #2a3a5a; }")
        try:
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as _f:
                _cfg = json.loads(_f.read())
            _sub_libs = _cfg.get("sub_libraries", {})
        except Exception:
            _sub_libs = {
                "materials": "\u6750\u8d28", "models": "\u6a21\u578b", "lights": "\u706f\u5149",
                "textures": "\u8d34\u56fe", "scenes": "\u573a\u666f", "hdr": "HDR", "ani": "\u52a8\u6001"
            }
        for _key, _label in _sub_libs.items():
            type_combo.addItem(f"{_label} ({_key})", _key)
        if root_lib in _sub_libs:
            idx = type_combo.findData(root_lib)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
        type_layout.addWidget(type_label)
        type_layout.addWidget(type_combo)
        layout.addLayout(type_layout)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QtWidgets.QPushButton("\u53d6\u6d88")
        cancel_btn.setStyleSheet("QPushButton { background-color: #3a3a3a; color: #a0a0a0; border: none; padding: 6px 16px; border-radius: 4px; } QPushButton:hover { color: #e0e0e0; }")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QtWidgets.QPushButton("\u786e\u5b9a")
        ok_btn.setStyleSheet("QPushButton { background-color: #5294e2; color: #fff; border: none; padding: 6px 16px; border-radius: 4px; } QPushButton:hover { background-color: #6ab0ff; }")
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        name_cn = cn_input.text().strip()
        cat_id = id_input.text().strip()
        cat_type = type_combo.currentData()
        if not cat_id:
            QtWidgets.QMessageBox.warning(self, "\u63d0\u793a", "\u6587\u4ef6\u5939\u540d\u4e0d\u80fd\u4e3a\u7a7a")
            return
        if not name_cn:
            name_cn = cat_id

        self.topLevelCategoryAdded.emit(cat_id, name_cn, root_lib, cat_type)

    def _on_edit_category(self, cat_id):
        cat, parent_cat = self._find_category(cat_id)
        if cat is None:
            return

        # 自定义双输入对话框（文件夹名只读 + 易读名可编辑）
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("\u7f16\u8f91\u5206\u7c7b")
        dialog.setFixedSize(380, 130)
        dialog.setStyleSheet("background-color: #2a2a2a;")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(8)

        # 第一行：文件夹名（只读）
        id_layout = QtWidgets.QHBoxLayout()
        id_label = QtWidgets.QLabel("\u6587\u4ef6\u5939\u540d:")
        id_label.setFixedWidth(80)
        id_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        id_input = QtWidgets.QLineEdit(cat_id)
        id_input.setReadOnly(True)
        id_input.setStyleSheet("background-color: #222; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #808080;")
        id_layout.addWidget(id_label)
        id_layout.addWidget(id_input)
        layout.addLayout(id_layout)

        # 第二行：易读名
        cn_layout = QtWidgets.QHBoxLayout()
        cn_label = QtWidgets.QLabel("\u6613\u8bfb\u540d:")
        cn_label.setFixedWidth(80)
        cn_label.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        cn_input = QtWidgets.QLineEdit(cat.get("name_cn") or cat.get("name") or cat_id)
        cn_input.setStyleSheet("background-color: #333; border: 1px solid #4a4a4a; border-radius: 4px; padding: 6px; color: #e0e0e0;")
        cn_layout.addWidget(cn_label)
        cn_layout.addWidget(cn_input)
        layout.addLayout(cn_layout)

        # 确定/取消
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QtWidgets.QPushButton("\u53d6\u6d88")
        cancel_btn.setStyleSheet("QPushButton { background-color: #3a3a3a; color: #a0a0a0; border: none; padding: 6px 16px; border-radius: 4px; } QPushButton:hover { color: #e0e0e0; }")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QtWidgets.QPushButton("\u786e\u5b9a")
        ok_btn.setStyleSheet("QPushButton { background-color: #5294e2; color: #fff; border: none; padding: 6px 16px; border-radius: 4px; } QPushButton:hover { background-color: #6ab0ff; }")
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        new_name = cn_input.text().strip()
        if not new_name:
            return

        # 只更新易读名（name_cn），不覆盖文件夹名（name/id）
        cat["name_cn"] = new_name
        # 在 _populate_tree 之前保存 root_lib（重建树后 currentItem 丢失）
        item = self._tree.currentItem()
        root_lib = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) if item else ""
        self._populate_tree()
        print(f"[MaterialLibrary] \u7f16\u8f91\u5206\u7c7b: {cat_id} -> {new_name}")
        self.categoryEdited.emit(cat_id, new_name, root_lib or "materials")

    def _on_delete_category(self, cat_id):
        cat, parent_cat = self._find_category(cat_id)
        if cat is None:
            return
        child_count = len(cat.get("children", []))
        display_name = cat.get("name_cn", cat_id)

        # \u2500\u2500 \u7b2c\u4e00\u7ea7\u786e\u8ba4\uff1a\u7b80\u5355\u8be2\u95ee \u2500\u2500
        msg1 = f"\u786e\u5b9a\u8981\u5220\u9664\u5206\u7c7b \"{display_name}\" \u5417\uff1f"
        if child_count > 0:
            msg1 += f"\n\u5b83\u5305\u542b {child_count} \u4e2a\u5b50\u5206\u7c7b\uff0c\u5b50\u5206\u7c7b\u4e5f\u4f1a\u88ab\u5220\u9664\u3002"
        msg1 += "\n\u26a0\ufe0f \u8be5\u5206\u7c7b\u4e0b\u7684\u8d44\u4ea7\u5c06\u88ab\u4e00\u5e76\u5220\u9664\uff01"
        reply1 = QtWidgets.QMessageBox.question(
            self, "\u5220\u9664\u5206\u7c7b", msg1,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        if reply1 != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        # \u2500\u2500 \u7b2c\u4e8c\u7ea7\u786e\u8ba4\uff1a\u5f3a\u8b66\u544a\uff0c\u9ed8\u8ba4\u6309\u94ae\u4e3a No \u2500\u2500
        asset_count = cat.get("material_count", 0)
        msg2 = f"\u26a0\ufe0f  \u6b64\u64cd\u4f5c\u4e0d\u53ef\u64a4\u9500\uff01\n\n\u5c06\u6c38\u4e45\u5220\u9664\u5206\u7c7b \"{display_name}\""
        if child_count > 0:
            msg2 += f" \u53ca\u5176 {child_count} \u4e2a\u5b50\u5206\u7c7b"
        if asset_count > 0:
            msg2 += f"\n\n\u8be5\u5206\u7c7b\u4e0b\u6709 {asset_count} \u4e2a\u8d44\u4ea7\uff0c\u5b83\u4eec\u5c06\u88ab\u4e00\u5e76\u5220\u9664\uff01"
        msg2 += "\n\n\u786e\u8ba4\u7ee7\u7eed\u5220\u9664\uff1f"
        reply2 = QtWidgets.QMessageBox.warning(
            self, "\u26a0\ufe0f \u6700\u7ec8\u786e\u8ba4\u5220\u9664", msg2,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No  # \u9ed8\u8ba4\u805a\u7126 No \u9632\u6b62\u8bef\u64cd\u4f5c
        )
        if reply2 != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        # \u2500\u2500 \u6267\u884c\u5220\u9664 \u2500\u2500
        if parent_cat:
            parent_cat["children"] = [c for c in parent_cat["children"] if c["id"] != cat_id]
        else:
            self._categories = [c for c in self._categories if c["id"] != cat_id]
        print(f"[MaterialLibrary] \u5220\u9664\u5206\u7c7b: {cat_id}")
        # \u5148 emit \u4fe1\u53f7\uff08\u6b64\u65f6\u6811\u5c1a\u672a\u91cd\u5efa\uff0ccurrentItem \u4ecd\u6709\u6548\uff09\uff0c\u518d\u91cd\u65b0\u6784\u5efa\u6811
        # \u4ece cat \u8282\u70b9\u8bfb\u53d6 root_lib\uff08type \u5b57\u6bb5\u7ee7\u627f\u81ea FolderMetadata\uff09
        root_lib = cat.get("type") or "materials"
        parent_id = parent_cat["id"] if parent_cat else ""
        self.categoryDeleted.emit(cat_id, root_lib, parent_id)
        # ⚠️ 不再调用 _populate_tree()：handler _on_category_deleted 内部已通过
        # _refresh_category_tree() → refresh_tree() → _populate_tree() 完成树重建，
        # 此处再调会清掉 handler 中恢复的展开状态，导致删除后树坍塌到顶级。

    def _on_move_category(self, cat_id):
        """移动到 → 选择新父级"""
        # 收集所有分类作为目标
        targets = [("根目录", "all")]
        for c in self._categories:
            if c["id"] != cat_id:
                targets.append((c.get("name_cn", c["id"]), c["id"]))
        names = [t[0] for t in targets]
        name, ok = QtWidgets.QInputDialog.getItem(
            self, f"\u79fb\u52a8 {cat_id} \u5230", "\u9009\u62e9\u76ee\u6807\u5206\u7c7b:",
            names, 0, False
        )
        if ok and name:
            idx = names.index(name)
            new_parent = targets[idx][1]
            self.categoryMoved.emit(cat_id, new_parent)

    def set_categories(self, categories):
        self._categories = list(categories)
        self._populate_tree()

    def refresh_tree(self, categories):
        """保留展开状态 + 选中状态刷新（_active_category 已是复合 ID 含 root_lib）"""
        expanded = self._save_expanded()
        selected_composite = self._active_category
        # 从复合 ID 提取 root_lib（如 "textures||AAAcustom" → "textures"）
        selected_root_lib = "materials"
        if CAT_ID_SEP in selected_composite:
            selected_root_lib, _ = split_cat_id(selected_composite)
        elif selected_composite and selected_composite != "all":
            item = self._tree.currentItem()
            if item:
                rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
                if rl:
                    selected_root_lib = rl
        self._categories = list(categories)
        self._populate_tree()
        # 临时断开 itemExpanded，避免 _restore_expanded 触发 _select_category
        self._tree.itemExpanded.disconnect(self._on_item_expanded)
        try:
            self._restore_expanded(expanded)
        finally:
            self._tree.itemExpanded.connect(self._on_item_expanded)
        self._select_by_id(selected_composite, selected_root_lib)

    def _save_expanded(self):
        """递归收集展开的 item id"""
        expanded = set()
        def walk(parent):
            for i in range(parent.childCount()):
                item = parent.child(i)
                mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if item.isExpanded() and mid:
                    expanded.add(mid)
                walk(item)
        walk(self._tree.invisibleRootItem())
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if item.isExpanded() and mid:
                expanded.add(mid)
            walk(item)
        return expanded

    def _restore_expanded(self, expanded):
        """恢复展开状态"""
        def walk(parent):
            for i in range(parent.childCount()):
                item = parent.child(i)
                mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if mid in expanded:
                    item.setExpanded(True)
                walk(item)
        walk(self._tree.invisibleRootItem())
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if mid in expanded:
                item.setExpanded(True)
            walk(item)

    def get_active_category(self):
        """返回复合分类 ID（如 "textures||AAAcustom"），包含 root_lib 信息避免歧义"""
        return self._active_category

    def get_active_root_lib(self) -> str:
        """返回当前选中项的 root_lib（子库名），默认返回 'materials'"""
        item = self._tree.currentItem()
        if item:
            rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
            if rl:
                return rl
        return "materials"

    def get_descendant_ids(self, cat_id, root_lib=None):
        """获取分类的所有后代 ID。cat_id 可为复合 ID，传 root_lib 可区分跨子库同名分类。
        返回 short_id 列表（与材质库存储格式一致）。"""
        # 复合 ID → 提取 short_id
        if CAT_ID_SEP in cat_id:
            rl, short = split_cat_id(cat_id)
            if root_lib is None:
                root_lib = rl
            cat_id = short
        cat, _ = self._find_category(cat_id, root_lib)
        if cat:
            return self._collect_descendant_ids(cat)
        return [cat_id]

    # ── 默认选中 & 展开 ─────────────────────────────

    def select_first_sub_library(self):
        """选中第一个子库（materials/材质库）并展开它"""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            cat_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if cat_id and cat_id != "all":
                if item.childCount() > 0:
                    item.setExpanded(True)
                self._tree.setCurrentItem(item)
                self._select_category(cat_id)
                return

    # ── 展开状态保存/恢复（用于 UI 状态持久化）───────

    def get_expanded_ids(self) -> list:
        """获取当前展开的所有 item id 列表"""
        expanded = []
        def walk(parent):
            for i in range(parent.childCount()):
                item = parent.child(i)
                mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if item.isExpanded() and mid:
                    expanded.append(mid)
                walk(item)
        walk(self._tree.invisibleRootItem())
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if item.isExpanded() and mid:
                expanded.append(mid)
            walk(item)
        return expanded

    def set_expanded_ids(self, ids: list):
        """根据 ID 列表恢复展开状态（不触发 _on_item_expanded 避免串选）"""
        if not ids:
            return
        expanded_set = set(ids)
        def walk(parent):
            for i in range(parent.childCount()):
                item = parent.child(i)
                mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if mid in expanded_set:
                    item.setExpanded(True)
                walk(item)
        # 临时断开 itemExpanded，避免展开时触发 _select_category 覆盖当前选中
        self._tree.itemExpanded.disconnect(self._on_item_expanded)
        try:
            walk(self._tree.invisibleRootItem())
            for i in range(self._tree.topLevelItemCount()):
                item = self._tree.topLevelItem(i)
                mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if mid in expanded_set:
                    item.setExpanded(True)
                walk(item)
        finally:
            self._tree.itemExpanded.connect(self._on_item_expanded)

    def _select_by_id(self, cat_id, root_lib=None):
        """按 ID 选中分类（不触发信号），递归展开父级使其可见。
        可传 root_lib 区分跨子库同名分类。cat_id 可为复合 ID。"""
        # 复合 ID → 拆分为 (root_lib, short_id)
        if CAT_ID_SEP in cat_id:
            rl, short = split_cat_id(cat_id)
            if root_lib is None:
                root_lib = rl
            cat_id = short
        def walk(parent):
            for i in range(parent.childCount()):
                item = parent.child(i)
                mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) or ""
                if mid == cat_id:
                    if root_lib is None or rl == root_lib:
                        # 展开所有父级
                        p = item.parent()
                        while p:
                            p.setExpanded(True)
                            p = p.parent()
                        self._tree.setCurrentItem(item)
                        self._tree.scrollToItem(item, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
                        return True
                    # root_lib 不匹配，跳过该项但继续搜索兄弟节点
                if walk(item):
                    return True
            return False
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            mid = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if mid == cat_id:
                self._tree.setCurrentItem(item)
                self._tree.scrollToItem(item, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
            if walk(item):
                return True
        return False
