from ..utils.maya_utils import get_qt_modules

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()

DEFAULT_COLLECTIONS = [
    {"id": "default", "name": "\u9ed8\u8ba4\u6536\u85cf\u5939", "icon": "\u2605", "materials": []},
    {"id": "work", "name": "\u5de5\u4f5c\u9879\u76ee", "icon": "\ud83d\udcbc", "materials": []},
]


class FavoritesPanelWidget(QtWidgets.QWidget):
    collectionSelected = QtCore.Signal(str, list)
    collectionAdded = QtCore.Signal(str, str)  # (coll_id, name)
    collectionRenamed = QtCore.Signal(str, str)
    collectionDeleted = QtCore.Signal(str)
    materialRemovedFromCollection = QtCore.Signal(str, str)
    materialDropOnCollection = QtCore.Signal(str, str)  # (material_id, collection_id)

    def __init__(self, parent=None):
        super(FavoritesPanelWidget, self).__init__(parent)
        self._collections = [dict(c) for c in DEFAULT_COLLECTIONS]
        self._active_collection_id = "default"
        self._all_favorites = set()
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #252525; border-right: 1px solid #3a3a3a;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(14, 12, 8, 8)
        header = QtWidgets.QLabel("\u2605 \u6536\u85cf\u5939")
        header.setStyleSheet("color: #e0e0e0; font-size: 14px; font-weight: bold;")
        header_row.addWidget(header)
        header_row.addStretch()

        add_coll_btn = QtWidgets.QPushButton("+")
        add_coll_btn.setFixedSize(22, 22)
        add_coll_btn.setToolTip("\u65b0\u5efa\u6536\u85cf\u5939")
        add_coll_btn.setStyleSheet(
            "QPushButton { background-color: #333333; color: #5294e2; border: none; "
            "border-radius: 3px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        add_coll_btn.clicked.connect(self._on_add_collection)
        header_row.addWidget(add_coll_btn)
        layout.addLayout(header_row)

        self._collection_list = QtWidgets.QListWidget()
        self._collection_list.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._collection_list.setStyleSheet("""
            QListWidget {
                background-color: #2a2a2a;
                border: none;
                color: #d0d0d0;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 8px 14px;
                border-bottom: 1px solid #3a3a3a;
                cursor: default;
            }
            QListWidget::item:selected {
                background-color: #3a3a3a;
                border-left: 3px solid #FFD700;
            }
            QListWidget::item:hover:!selected {
                background-color: #333333;
            }
            QListWidget::item:hover {
                background-color: #333333;
            }
        """)
        self._collection_list.itemClicked.connect(self._on_collection_clicked)
        self._collection_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._collection_list.customContextMenuRequested.connect(self._on_collection_context_menu)
        self._collection_list.setAcceptDrops(True)
        self._collection_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DropOnly)

        # 拖放处理：材质卡片放到收藏夹
        self_ref = self
        _orig_enter = self._collection_list.dragEnterEvent
        _orig_move = self._collection_list.dragMoveEvent
        _orig_drop = self._collection_list.dropEvent
        _drag_hover_item = [None]

        def _clear_hover():
            if _drag_hover_item[0] is not None:
                try:
                    _drag_hover_item[0].setBackground(QtGui.QBrush())
                except RuntimeError:
                    pass
                _drag_hover_item[0] = None

        def _set_hover(item):
            _clear_hover()
            if item:
                item.setBackground(QtGui.QBrush(QtGui.QColor("#2a3a5a")))
                _drag_hover_item[0] = item

        def _accept(event):
            if event.mimeData().hasFormat("application/x-material-ids") or event.mimeData().hasFormat("application/x-material-id"):
                event.accept()
                return True
            return False

        def _on_drag_enter(event):
            if not _accept(event):
                _orig_enter(event)

        def _on_drag_move(event):
            if event.mimeData().hasFormat("application/x-material-ids") or event.mimeData().hasFormat("application/x-material-id"):
                event.accept()
                item = self_ref._collection_list.itemAt(event.position().toPoint())
                if item:
                    _set_hover(item)
                else:
                    _clear_hover()
                return
            _orig_move(event)

        def _on_drop(event):
            _clear_hover()
            mime = event.mimeData()
            item = self_ref._collection_list.itemAt(event.position().toPoint())
            coll_id = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else "default"

            # 多选拖拽
            ids_bytes = mime.data("application/x-material-ids")
            if ids_bytes:
                import json
                ids = json.loads(ids_bytes.data().decode())
                for mid in ids:
                    self_ref.materialDropOnCollection.emit(mid, coll_id)
                event.accept()
                return

            # 单选拖拽
            if mime.hasFormat("application/x-material-id"):
                mat_id = mime.data("application/x-material-id").data().decode()
                self_ref.materialDropOnCollection.emit(mat_id, coll_id)
                event.accept()
                return

            _orig_drop(event)

        self._collection_list.dragEnterEvent = _on_drag_enter
        self._collection_list.dragMoveEvent = _on_drag_move
        self._collection_list.dropEvent = _on_drop
        layout.addWidget(self._collection_list, 1)

    def _refresh(self):
        self._collection_list.clear()
        for coll in self._collections:
            count = len(coll.get("materials", []))
            item = QtWidgets.QListWidgetItem(f"{coll['icon']}  {coll['name']}  ({count})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, coll["id"])
            if coll["id"] == "default":
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._collection_list.addItem(item)

    def _on_collection_clicked(self, item):
        coll_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self._active_collection_id = coll_id
        for coll in self._collections:
            if coll["id"] == coll_id:
                self.collectionSelected.emit(coll_id, coll.get("materials", []))
                return

    def _on_collection_context_menu(self, pos):
        item = self._collection_list.itemAt(pos)
        if item is None:
            menu = QtWidgets.QMenu(self)
            menu.addAction("+ \u65b0\u5efa\u6536\u85cf\u5939").triggered.connect(self._on_add_collection)
            menu.exec(self._collection_list.viewport().mapToGlobal(pos))
            return

        coll_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        menu = QtWidgets.QMenu(self)
        menu.addAction("+ \u65b0\u5efa\u6536\u85cf\u5939").triggered.connect(self._on_add_collection)
        menu.addSeparator()
        menu.addAction("\u6e05\u7a7a\u6536\u85cf\u5939 \u2605").triggered.connect(
            lambda: self._on_clear_collection(coll_id))

        if coll_id != "default":
            menu.addSeparator()
            menu.addAction("\u91cd\u547d\u540d").triggered.connect(lambda: self._on_rename_collection(coll_id))
            menu.addAction("\u5220\u9664\u6536\u85cf\u5939").triggered.connect(lambda: self._on_delete_collection(coll_id))

        menu.exec(self._collection_list.viewport().mapToGlobal(pos))

    def _on_add_collection(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "\u65b0\u5efa\u6536\u85cf\u5939", "\u8bf7\u8f93\u5165\u6536\u85cf\u5939\u540d\u79f0:",
            QtWidgets.QLineEdit.EchoMode.Normal
        )
        if ok and name.strip():
            coll_id = f"fav_{len(self._collections) + 1}"
            new_coll = {
                "id": coll_id, "name": name.strip(), "icon": "\ud83d\udcc1", "materials": []
            }
            self._collections.append(new_coll)
            self._refresh()
            self.collectionAdded.emit(coll_id, name.strip())
            print(f"[MaterialLibrary] \u65b0\u5efa\u6536\u85cf\u5939: {name.strip()}")

    def _on_rename_collection(self, coll_id):
        for coll in self._collections:
            if coll["id"] == coll_id:
                new_name, ok = QtWidgets.QInputDialog.getText(
                    self, "\u91cd\u547d\u540d\u6536\u85cf\u5939", "\u65b0\u540d\u79f0:",
                    QtWidgets.QLineEdit.EchoMode.Normal, coll["name"]
                )
                if ok and new_name.strip():
                    old = coll["name"]
                    coll["name"] = new_name.strip()
                    self._refresh()
                    self.collectionRenamed.emit(coll_id, new_name.strip())
                    print(f"[MaterialLibrary] \u6536\u85cf\u5939\u91cd\u547d\u540d: {old} -> {new_name.strip()}")
                return

    def _on_delete_collection(self, coll_id):
        for coll in self._collections:
            if coll["id"] == coll_id:
                reply = QtWidgets.QMessageBox.question(
                    self, "\u786e\u8ba4\u5220\u9664",
                    f"\u786e\u5b9a\u8981\u5220\u9664\u6536\u85cf\u5939 \"{coll['name']}\" \u5417\uff1f",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
                )
                if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                    self._collections = [c for c in self._collections if c["id"] != coll_id]
                    self._refresh()
                    self.collectionDeleted.emit(coll_id)
                    print(f"[MaterialLibrary] \u5220\u9664\u6536\u85cf\u5939: {coll['name']}")
                return

    def _on_clear_collection(self, coll_id):
        """\u6e05\u7a7a\u6536\u85cf\u5939\u5185\u7684\u6240\u6709\u8d44\u4ea7"""
        for coll in self._collections:
            if coll["id"] == coll_id:
                if not coll.get("materials"):
                    return
                reply = QtWidgets.QMessageBox.question(
                    self, "\u786e\u8ba4\u6e05\u7a7a",
                    f"\u786e\u5b9a\u8981\u6e05\u7a7a\u6536\u85cf\u5939 \"{coll['name']}\" \u4e2d\u7684\u6240\u6709\u8d44\u4ea7\u5417\uff1f",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
                )
                if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                    removed = list(coll.get("materials", []))
                    coll["materials"].clear()
                    self._refresh()
                    for mid in removed:
                        self.materialRemovedFromCollection.emit(coll_id, mid)
                    print(f"[MaterialLibrary] \u6e05\u7a7a\u6536\u85cf\u5939: {coll['name']} ({len(removed)} \u4e2a)")
                return

    def add_material_to_collection(self, coll_id, material_id):
        for coll in self._collections:
            if coll["id"] == coll_id:
                if material_id not in coll["materials"]:
                    coll["materials"].append(material_id)
                    self._refresh()
                return

    def remove_material_from_collection(self, coll_id, material_id):
        for coll in self._collections:
            if coll["id"] == coll_id:
                if material_id in coll["materials"]:
                    coll["materials"].remove(material_id)
                    self._refresh()
                    self.materialRemovedFromCollection.emit(coll_id, material_id)
                return

    def get_current_collection_materials(self):
        for coll in self._collections:
            if coll["id"] == self._active_collection_id:
                return coll.get("materials", [])
        return []

    def get_active_collection_id(self):
        return self._active_collection_id
