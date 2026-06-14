import json
import os
import re

from ..utils.maya_utils import get_qt_modules
from ..utils.mock_data import MOCK_MATERIALS
from ..utils.settings import SettingsManager

QtWidgets, QtCore, QtGui, _, _ = get_qt_modules()


def _get_sub_style(font_size=13):
    return f"""
    QMenu {{ background-color:#2a2a2a; color:#d0d0d0;
            border:1px solid #3a3a3a; padding:4px; }}
    QMenu::item {{ padding:6px 24px 6px 14px; font-size:{font_size}px; }}
    QMenu::item:selected {{ background-color:#2a3a5a; color:#5294e2; }}
    """


def _load_presets(key: str) -> list:
    """从 config.json 加载预设列表（material_presets / dome_light_presets 等）"""
    import os, json
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "preset", "config.json")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get(key, [])
    except Exception:
        return []


def _get_texture_names(zasset_path: str) -> list:
    """从 .zasset 中读取 textures/ 下的贴图文件名列表"""
    if not zasset_path or not zasset_path.endswith('.zasset'):
        return []
    from ..integration.texture_importer import list_texture_names
    return list_texture_names(zasset_path)


class MaterialDragLabel(QtWidgets.QLabel):
    """可拖拽的缩略图标签 — 支持多选拖拽"""
    # 父级 ThumbnailGridWidget 的引用，用于获取当前选中列表
    grid_ref = None

    def __init__(self, material, parent=None):
        super(MaterialDragLabel, self).__init__(parent)
        self._material = material
        self._movie_ref = None  # GIF 动图引用
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)

    def set_movie(self, movie):
        """设置并播放 GIF 动图"""
        self._stop_movie()
        self._movie_ref = movie
        if movie:
            self.setMovie(movie)
            movie.start()

    def _stop_movie(self):
        """停止并清理当前 GIF 动图"""
        if self._movie_ref:
            self._movie_ref.stop()
            self._movie_ref.deleteLater()
        self._movie_ref = None

    def showEvent(self, event):
        super().showEvent(event)
        if self._movie_ref:
            self._movie_ref.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._movie_ref and self._movie_ref.state() == QtGui.QMovie.Running:
            self._movie_ref.setPaused(True)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            return
        if (event.pos() - getattr(self, '_drag_start', event.pos())).manhattanLength() < 10:
            return
        drag = QtGui.QDrag(self)
        mime_data = QtCore.QMimeData()
        # 所有选中材质 ID；无选中时拖当前卡片
        grid = self.grid_ref
        if grid and grid._selected_materials:
            selected = list(grid._selected_materials.values())
        else:
            selected = [self._material]
        ids = json.dumps([m.get("id", "") for m in selected])
        mime_data.setData("application/x-material-ids", ids.encode())
        mime_data.setText(ids)
        drag.setMimeData(mime_data)
        pix = self.pixmap()
        if pix:
            drag.setPixmap(pix.scaled(80, 80, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))
            drag.setHotSpot(QtCore.QPoint(40, 40))
        drag.exec(QtCore.Qt.DropAction.CopyAction)
        # 拖拽结束后触发导入/赋予（有选中则赋予，无则仅创建）
        if grid and selected:
            cursor_global = QtGui.QCursor.pos()
            grid.dragDroppedOnViewport.emit(
                [m.get("id", "") for m in selected],
                cursor_global.x(), cursor_global.y())

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super(MaterialDragLabel, self).mousePressEvent(event)


class MaterialTableModel(QtCore.QAbstractTableModel):
    SUB_LIB_NAMES = {
        "materials": "材质",
        "models": "模型",
        "textures": "贴图",
        "lights": "灯光",
        "scenes": "场景",
        "hdr": "HDR",
        "ani": "动态",
    }

    def __init__(self, parent=None):
        super(MaterialTableModel, self).__init__(parent)
        self._materials = []
        self._headers = ["\u2605", "\u540d\u79f0", "\u8d44\u4ea7\u7c7b\u578b", "\u5206\u7c7b", "\u6807\u7b7e"]

    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self._materials)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self._headers)

    def headerData(self, section, orientation, role):
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index, role):
        if not index.isValid():
            return None
        mat = self._materials[index.row()]
        col = index.column()

        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            if col == 1:
                return mat.get("name_cn", mat.get("name", ""))
            elif col == 2:
                sub_lib = mat.get("sub_library", "")
                return self.SUB_LIB_NAMES.get(sub_lib, sub_lib)
            elif col == 3:
                return self._category_name(mat)
            elif col == 4:
                return ", ".join(mat.get("tags", []))
        elif role == QtCore.Qt.ItemDataRole.DecorationRole:
            if col == 0:
                favorited = mat.get("_favorited", False)
                return "\u2605" if favorited else "\u2606"
            elif col == 1:
                pix = QtGui.QPixmap(32, 32)
                pix.fill(QtGui.QColor(mat.get("color", "#606060")))
                return QtGui.QIcon(pix)
        elif role == QtCore.Qt.ItemDataRole.ForegroundRole:
            if col == 0:
                return QtGui.QColor("#FFD700") if mat.get("_favorited", False) else QtGui.QColor("#606060")
        elif role == QtCore.Qt.ItemDataRole.UserRole:
            return mat
        elif role == QtCore.Qt.ItemDataRole.ToolTipRole:
            if col == 1:
                return f"{mat.get('name_cn', '')}\nEN: {mat.get('name', '')}\n\u7c7b\u578b: {mat.get('node_type', '')}"
        return None

    def _category_name(self, mat):
        """\u4f18\u5148\u4f7f\u7528\u5df2\u9884\u586b\u7684 _category_display"""
        dn = mat.get("_category_display")
        if dn:
            return dn
        # \u56de\u9000
        names = {
            "metal": "\u91d1\u5c5e", "fabric": "\u5e03\u6599", "plastic": "\u5851\u6599",
            "glass": "\u73bb\u7483", "skin": "\u76ae\u80a4", "wood": "\u6728\u6750",
            "stone": "\u77f3\u6750", "liquid": "\u6db2\u4f53", "foliage": "\u690d\u88ab",
        }
        return names.get(mat.get("category", ""), mat.get("category", ""))

    def set_materials(self, materials):
        self.beginResetModel()
        self._materials = list(materials)
        self.endResetModel()

    def get_material(self, row):
        if 0 <= row < len(self._materials):
            return self._materials[row]
        return None


class ThumbnailGridWidget(QtWidgets.QStackedWidget):
    materialSelected = QtCore.Signal(dict)
    materialApplied = QtCore.Signal(dict)
    favoriteToggled = QtCore.Signal(str, bool)
    addToFavoriteRequested = QtCore.Signal(str, str)  # (material_id, collection_id)
    editMaterialRequested = QtCore.Signal(dict)   # 编辑材质元数据
    selectionChanged = QtCore.Signal()
    createAssetRequested = QtCore.Signal(dict)    # 创建资产（从Maya选中）
    exportPresetRequested = QtCore.Signal(dict)
    deleteRequested = QtCore.Signal(list)  # list of material IDs
    thumbnailUpdateRequested = QtCore.Signal(str)
    thumbnailCaptureRequested = QtCore.Signal(str)  # 缩略图→截取
    thumbnailImportRequested = QtCore.Signal(str)   # 缩略图→导入
    updateAssetRequested = QtCore.Signal(str)        # 更新资产→传入 mid
    columnCountChanged = QtCore.Signal(int)
    thumbSizeChanged = QtCore.Signal(int)
    openFolderRequested = QtCore.Signal(dict)
    moveRequested = QtCore.Signal(dict)
    clipboardChanged = QtCore.Signal(list)
    pasteRequested = QtCore.Signal()
    importRequested = QtCore.Signal(str)  # "folder" or "files"
    assetImportRequested = QtCore.Signal(str, str)  # (zasset_path, format_name)
    variantGeometryImportRequested = QtCore.Signal(str, str, str)  # (zasset_path, version, lod)
    variantMaterialImportRequested = QtCore.Signal(str, str)  # (zasset_path, version)
    variantVersionDeleteRequested = QtCore.Signal(str, str)  # (zasset_path, version_id)
    variantLodDeleteRequested = QtCore.Signal(str, str, str)  # (zasset_path, version_id, lod_id)
    copyToProjectRequested = QtCore.Signal(list)
    createMaterialRequested = QtCore.Signal(str, dict, str)   # (node_type, material_data, resolution_or_empty)
    createDomeLightRequested = QtCore.Signal(str, dict)  # (preset_path, hdr_material)
    assignHdrToDomeRequested = QtCore.Signal(dict)  # (hdr_material) HDR→指定给选中dome灯
    importSingleTextureRequested = QtCore.Signal(str, str)  # (zasset_path, texture_name)
    aiAnalysisRequested = QtCore.Signal(dict)  # AI 分析请求
    importTexturesSharedUVRequested = QtCore.Signal(str, list)  # (zasset_path, texture_names) — 共享UV批量导入 单个贴图导入
    assignTextureToMaterialRequested = QtCore.Signal(dict)  # (texture_material) 贴图→指定给选中材质
    dragDroppedOnViewport = QtCore.Signal(list, int, int)  # ([material_ids], global_x, global_y)
    previewNodeRequested = QtCore.Signal(str)  # (node_file_path) 预览节点文件
    importZlightAsRenderer = QtCore.Signal(str, str)  # (zasset_path, renderer) 以指定渲染器导入灯光
    VIEW_ICON = 0
    VIEW_LIST = 1

    DEFAULT_THUMB = 180
    PADDING = 10

    def __init__(self, parent=None):
        super(ThumbnailGridWidget, self).__init__(parent)
        self._materials = list(MOCK_MATERIALS)
        self._filtered_materials = list(self._materials)
        self._columns = 4
        self._thumb_size = self.DEFAULT_THUMB
        self._selected_material = None
        self._selected_materials = {}       # material_id → material dict (multi-select)
        self._last_clicked_id = None        # for shift-range select
        self._sort_key = "name_cn"
        self._active_filters = set()
        self._active_tags = set()
        self._current_cat_id = None         # 当前分类筛选（用于组合搜索）
        self._current_desc_ids = None       # 当前分类后代 ID
        self._current_search_kw = None      # 当前搜索关键词（用于组合分类切换）
        self._view_mode = self.VIEW_ICON
        self._manager = None                # MaterialManager 引用（用于收藏判断）
        self._card_widgets = {}             # material_id → QFrame（缩放复用）
        self._thumb_cache = {}              # material_id → 原始 QPixmap
        # ── 虚拟化相关 ──
        self._card_pool = {}               # material_id → QFrame（虚拟化卡片池）
        self._columns = 8                  # 当前列数（默认最大列数，卡片最小）
        self._min_columns = 2              # 最小列数（最大卡片）
        self._max_columns = 8              # 最大列数（最小卡片）
        self._last_thumb_size = 0          # 上次的卡片大小，用于避免不必要的重建
        self._zoom_timer = QtCore.QTimer()
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(16)
        self._zoom_timer.timeout.connect(self._relayout_cards)
        self._scroll_timer = QtCore.QTimer()
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._update_visible_cards)
        # 框选节流定时器
        self._rubber_timer = QtCore.QTimer()
        self._rubber_timer.setSingleShot(True)
        self._rubber_timer.setInterval(30)
        self._rubber_timer.timeout.connect(self._do_rubber_update)
        self._rubber_pending_rect = None

        self._setup_icon_view()
        self._setup_list_view()
        self.setCurrentIndex(self.VIEW_ICON)
        self._icon_container.hide()
        def _first_refresh():
            self._auto_columns()
            self._refresh()
            self._icon_container.show()
        QtCore.QTimer.singleShot(0, _first_refresh)

    def _setup_icon_view(self):
        # 自定义 ScrollArea 支持 Ctrl+滚轮缩放 + 自适应列数
        grid_ref = self
        self._in_resize = False

        class ZoomScrollArea(QtWidgets.QScrollArea):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

            def keyPressEvent(self, event):
                if event.key() == QtCore.Qt.Key.Key_F:
                    grid_ref._scroll_to_selected()
                    event.accept()
                    return
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
                    if event.key() == QtCore.Qt.Key.Key_C:
                        grid_ref._copy_selected_to_clipboard()
                        event.accept()
                        return
                    if event.key() == QtCore.Qt.Key.Key_A:
                        grid_ref._select_all_cards()
                        event.accept()
                        return
                super().keyPressEvent(event)

            def wheelEvent(self, event):
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        grid_ref._columns = max(grid_ref._min_columns, grid_ref._columns - 1)
                    else:
                        grid_ref._columns = min(grid_ref._max_columns, grid_ref._columns + 1)
                    grid_ref._auto_columns()
                    grid_ref.columnCountChanged.emit(grid_ref._columns)
                    grid_ref._zoom_timer.start()
                    event.accept()
                else:
                    super().wheelEvent(event)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                if grid_ref._in_resize:
                    return
                grid_ref._in_resize = True
                try:
                    grid_ref._auto_columns()
                    grid_ref._zoom_timer.start()
                finally:
                    grid_ref._in_resize = False

            def contextMenuEvent(self, event):
                pos_in_viewport = event.pos()
                container_pos = grid_ref._icon_container.mapFrom(self.viewport(), pos_in_viewport)
                child = grid_ref._icon_container.childAt(container_pos)
                if child and hasattr(child, 'material_data'):
                    event.ignore()
                    return
                grid_ref._on_empty_area_menu(container_pos)
                event.accept()

        scroll = ZoomScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: #2a2a2a; }")
        scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._scroll = scroll

        self._icon_container = QtWidgets.QWidget()
        self._icon_container.setStyleSheet("background-color: #2a2a2a;")
        # 虚拟化：不使用布局管理器，直接放置卡片并手动move位置
        self._main_layout = None

        # 空白区域右键 → 创建材质预设
        self._icon_container.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._icon_container.customContextMenuRequested.connect(self._on_empty_area_menu)

        # 空白区域框选支持
        self._rubber_band = None
        self._rubber_origin = None
        self._icon_container.mousePressEvent = self._icon_mouse_press
        self._icon_container.mouseMoveEvent = lambda e: self._icon_mouse_move(e)
        self._icon_container.mouseReleaseEvent = self._icon_mouse_release

        scroll.setWidget(self._icon_container)
        self.addWidget(scroll)

    def _icon_mouse_press(self, event):
        """在空白区域按下 → 开始框选"""
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        # 检查是否点到了卡片
        child = self._icon_container.childAt(event.position().toPoint())
        card = child if hasattr(child, 'material_data') else None
        if card:
            # 点击到卡片，交给卡片的 mousePressEvent
            return
        # 空白点击 → 清除所有选中
        self._selected_materials.clear()
        self._selected_material = None
        self._last_clicked_id = None
        self._refresh_card_highlights()
        self.selectionChanged.emit()
        # 开始框选
        self._rubber_origin = event.position().toPoint()
        if not self._rubber_band:
            self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self._icon_container)
        self._rubber_band.setGeometry(QtCore.QRect(self._rubber_origin, QtCore.QSize()))
        self._rubber_band.show()

    def _icon_mouse_move(self, event):
        if self._rubber_band and self._rubber_origin:
            rect = QtCore.QRect(self._rubber_origin, event.position().toPoint()).normalized()
            self._rubber_band.setGeometry(rect)
            # 节流：30ms内只执行一次框选更新
            self._rubber_pending_rect = rect
            self._rubber_timer.start()

    def _do_rubber_update(self):
        """节流后的框选更新，只更新状态变化的卡片"""
        rect = self._rubber_pending_rect
        if rect is None:
            return
        for i, mat in enumerate(self._filtered_materials):
            mid = mat.get("id", "")
            if not mid or mid not in self._card_pool:
                continue
            pos = self._calc_card_position(i)
            if not pos:
                continue
            x, y, card_w, card_h = pos
            card_rect = QtCore.QRect(x, y, card_w, card_h)
            in_rubber = rect.intersects(card_rect)
            is_prev_selected = mid in self._selected_materials
            already_selected = in_rubber or is_prev_selected
            # 只更新状态变化的卡片
            card = self._card_pool[mid]
            prev_state = card.property("_rubber_selected")
            if prev_state == already_selected:
                continue
            card.setProperty("_rubber_selected", already_selected)
            bc = '#5294e2' if already_selected else '#3a3a3a'
            hover_qss = "" if already_selected else "QFrame#thumbnailCard:hover { border: 2px solid #555555; background-color: #2a2a2a; }"
            card.setStyleSheet(
                f"QFrame#thumbnailCard {{ background-color: #252525;"
                f"border: 2px solid {bc}; border-radius: 6px; }}"
                + hover_qss
            )

    def _icon_mouse_release(self, event):
        if self._rubber_band and self._rubber_origin:
            rect = QtCore.QRect(self._rubber_origin, event.position().toPoint()).normalized()
            self._rubber_band.hide()
            self._select_cards_in_rect(rect)
            self._rubber_origin = None

    def _select_cards_in_rect(self, rect):

        self._selected_materials.clear()
        self._last_clicked_id = None
        self._selected_material = None
        if rect.width() < 5 and rect.height() < 5:
            self._refresh_card_highlights()
            self.selectionChanged.emit()
            return
        selected_count = 0
        for i, mat in enumerate(self._filtered_materials):
            mid = mat.get("id", "")
            if not mid:
                continue
            pos = self._calc_card_position(i)
            if not pos:
                continue
            x, y, card_w, card_h = pos
            card_rect = QtCore.QRect(x, y, card_w, card_h)
            if rect.intersects(card_rect):
                self._selected_materials[mid] = mat
                self._last_clicked_id = mid
                selected_count += 1

        if self._selected_materials:
            first = next(iter(self._selected_materials.values()))
            self._selected_material = first
            self.materialSelected.emit(first)
        self._refresh_card_highlights()
        self.selectionChanged.emit()

    def _setup_list_view(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table_model = MaterialTableModel()
        self._table_view = QtWidgets.QTableView()
        self._table_view.setModel(self._table_model)
        self._table_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table_view.setShowGrid(False)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._table_view.verticalHeader().setVisible(False)
        self._table_view.setColumnWidth(0, 40)
        self._table_view.setColumnWidth(1, 200)
        self._table_view.setColumnWidth(2, 160)
        self._table_view.setColumnWidth(3, 80)
        self._table_view.setStyleSheet("""
            QTableView {
                background-color: #2a2a2a; border: none; color: #d0d0d0;
                font-size: 13px; gridline-color: #3a3a3a;
            }
            QTableView::item {
                padding: 8px 12px; border-bottom: 1px solid #3a3a3a;
            }
            QTableView::item:selected {
                background-color: #3a3a3a; color: #ffffff;
            }
            QTableView::item:hover {
                background-color: #333333;
            }
            QHeaderView::section {
                background-color: #252525; color: #909090; border: none;
                border-bottom: 1px solid #3a3a3a; padding: 8px 12px;
                font-size: 12px; font-weight: bold;
            }
        """)
        self._table_view.clicked.connect(self._on_table_clicked)
        self._table_view.doubleClicked.connect(self._on_table_double_clicked)
        self._table_view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.PreventContextMenu)
        # 右键按下立即弹出菜单（Maya 风格）
        _orig_table_press = self._table_view.mousePressEvent
        def _table_mouse_press(event):
            if event.button() == QtCore.Qt.MouseButton.RightButton:
                index = self._table_view.indexAt(event.position().toPoint())
                if index.isValid():
                    self._on_table_context_menu(event.position().toPoint())
                return
            _orig_table_press(event)
        self._table_view.mousePressEvent = _table_mouse_press
        self._table_view.selectionModel().selectionChanged.connect(
            lambda: self.selectionChanged.emit()
        )

        layout.addWidget(self._table_view)
        self.addWidget(container)

    def _on_table_clicked(self, index):
        mat = self._table_model.get_material(index.row())
        if mat:
            self._selected_material = mat
            self.materialSelected.emit(mat)

    def _on_table_double_clicked(self, index):
        mat = self._table_model.get_material(index.row())
        if mat:
            print(f"[MaterialLibrary] \u53cc\u51fb\u5e94\u7528: {mat.get('name_cn')}")
            self.materialApplied.emit(mat)

    def _on_table_context_menu(self, pos):
        index = self._table_view.indexAt(pos)
        mat = self._table_model.get_material(index.row()) if index.isValid() else None
        if not mat:
            return
        
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(_get_sub_style(font_size))
        apply_action = menu.addAction("\u5e94\u7528\u6750\u8d28")
        menu.addSeparator()

        # 收藏夹子菜单（列表视图）
        mid = mat.get("id", "") if mat else ""
        if self._manager and mid:
            fav_sub = QtWidgets.QMenu("☆ 添加到收藏夹", menu)
            fav_sub.setStyleSheet(_get_sub_style(font_size))
            for cid in self._manager._favorites.keys():
                name = self._manager._favorites_meta.get(cid, "默认收藏夹" if cid == "default" else cid)
                is_in = mid in self._manager._favorites.get(cid, set())
                label = f"{'★ ' if is_in else '☆ '}{name}"
                a = fav_sub.addAction(label)
                a.setData(cid)
                def make_handler(coll_id, already_in):
                    def handler():
                        if already_in:
                            self._manager._favorites[coll_id].discard(mid)
                        else:
                            self._manager._favorites[coll_id].add(mid)
                        self._manager.save_favorites()
                        self.addToFavoriteRequested.emit(mid, coll_id)
                    return handler
                a.triggered.connect(make_handler(cid, is_in))
            menu.addMenu(fav_sub)
        else:
            fav_action = menu.addAction("☆ 添加到收藏夹")
        menu.addSeparator()
        edit_action = menu.addAction("\u7f16\u8f91")
        export_action = menu.addAction("\u521b\u5efa\u8d44\u4ea7")
        menu.addSeparator()
        if self._manager and mid:
            move_sub = self._build_category_submenu("\u2795 \u79fb\u52a8\u5230", mid, move=True, font_size=font_size)
            if move_sub: menu.addMenu(move_sub)
            copy_sub = self._build_category_submenu("\ud83d\udcc1 \u590d\u5236\u5230", mid, move=False, font_size=font_size)
            if copy_sub: menu.addMenu(copy_sub)
        folder_action = menu.addAction("\ud83d\udcc2 \u6253\u5f00\u6587\u4ef6\u5939")
        # 更新缩略图 → 子菜单（截取 / 导入）
        thumb_menu = QtWidgets.QMenu("\u66f4\u65b0\u7f29\u7565\u56fe", menu)
        thumb_menu.setStyleSheet(_get_sub_style(font_size))
        cap_action = thumb_menu.addAction("\ud83d\udcf7 \u622a\u53d6")
        imp_action = thumb_menu.addAction("\ud83d\udcc2 \u5bfc\u5165")
        menu.addMenu(thumb_menu)
        delete_action = menu.addAction("\u5220\u9664")

        action = menu.exec(self._table_view.viewport().mapToGlobal(pos))
        if action == apply_action:
            self.materialApplied.emit(mat)
        elif action == edit_action:
            self.editMaterialRequested.emit(mat)
        elif action == export_action:
            self.createAssetRequested.emit(mat)
        elif action == folder_action:
            self.openFolderRequested.emit(mat)
        elif action == delete_action:
            ids = list(self._selected_materials.keys()) if self._selected_materials else [mat.get("id", "")]
            self.deleteRequested.emit(ids)
        elif action == cap_action:
            self.thumbnailCaptureRequested.emit(mat.get("id", ""))
        elif action == imp_action:
            self.thumbnailImportRequested.emit(mat.get("id", ""))

    def set_view_mode(self, mode):
        self._view_mode = mode
        self.setCurrentIndex(mode)
        if mode == self.VIEW_LIST:
            self._refresh_list()
        else:
            self._refresh_icon()

    def set_thumb_size(self, size):
        self._thumb_size = max(100, min(1024, size))
        self.thumbSizeChanged.emit(self._thumb_size)
        if self._view_mode == self.VIEW_ICON:
            if hasattr(self, '_scroll'):
                avail = self._scroll.viewport().width() - self.PADDING * 2
                if avail >= 80:
                    self._columns = max(self._min_columns, min(8, avail // self._thumb_size))
                    self._auto_columns()
                else:
                    self._zoom_timer.start()
            else:
                self._zoom_timer.start()

    def _auto_columns(self):
        """根据列数计算卡片大小，确保横向填满，自动换行"""
        if not hasattr(self, '_icon_container') or not hasattr(self, '_scroll'):
            return
        avail = self._scroll.viewport().width() - self.PADDING * 2
        if avail < 80:  # 太窄，不处理
            return

        min_thumb_size = 80
        
        # 当卡片太小放不下时，自动减少列数（换行）
        while self._columns > self._min_columns:
            calc_size = avail // self._columns
            if calc_size >= min_thumb_size:
                break
            self._columns -= 1
        
        # 根据列数计算卡片大小，确保横向填满
        self._thumb_size = max(min_thumb_size, avail // self._columns)
        
        # 更新容器尺寸以填满视口
        if hasattr(self, '_card_pool') and self._filtered_materials:
            total_h = self._calc_total_height()
            container_w = max(avail + self.PADDING * 2, self._columns * self._thumb_size + self.PADDING * 2)
            container_h = max(total_h, self._scroll.viewport().height())
            self._icon_container.setFixedSize(container_w, container_h)
            
            # 通知外部滑块更新
            if hasattr(self, 'columnCountChanged'):
                self.columnCountChanged.emit(self._columns)

    # ── 虚拟化核心方法 ────────────────────────────────

    def _on_scroll(self):
        self._scroll_timer.start()

    def _calc_card_height(self):
        W = self._thumb_size
        pad = max(3, int(W * 0.06))
        thumb_sz = W - 4 - pad * 2
        text_h = max(12, int(W * 0.16))
        gap = max(2, int(W * 0.02))
        return thumb_sz + text_h + pad * 2 + gap

    def _calc_total_height(self):
        if not self._filtered_materials:
            return 0
        card_h = self._calc_card_height()
        total_rows = (len(self._filtered_materials) + self._columns - 1) // self._columns
        padding = self.PADDING
        return padding * 2 + total_rows * card_h

    def _calc_card_position(self, idx):
        if idx < 0 or idx >= len(self._filtered_materials):
            return None
        card_w = self._thumb_size
        card_h = self._calc_card_height()
        padding = self.PADDING
        row = idx // self._columns
        col = idx % self._columns
        x = padding + col * card_w
        y = padding + row * card_h

        return (x, y, card_w, card_h)

    def _update_visible_cards(self, force_all=False):
        if not hasattr(self, '_scroll') or not self._filtered_materials:
            return
        if not hasattr(self, '_icon_container'):
            return

        card_w = self._thumb_size
        card_h = self._calc_card_height()
        padding = self.PADDING

        # 计算可见区域范围（带缓冲）
        viewport = self._scroll.viewport()
        viewport_h = viewport.height()
        scroll_y = self._scroll.verticalScrollBar().value()
        
        start_row = max(0, (scroll_y - card_h * 2) // card_h)
        end_row = (scroll_y + viewport_h + card_h * 2) // card_h + 1
        
        total_items = len(self._filtered_materials)
        total_rows = (total_items + self._columns - 1) // self._columns
        end_row = min(end_row, total_rows)
        
        created_count = 0
        moved_count = 0

        # 遍历所有材料
        for i, mat in enumerate(self._filtered_materials):
            mid = mat.get("id", "")
            if not mid:
                continue

            row = i // self._columns
            col = i % self._columns
            x = padding + col * card_w
            y = padding + row * card_h

            if mid not in self._card_pool:
                # 只创建可见区域内的卡片（或强制创建所有）
                if force_all or (start_row <= row <= end_row):

                    card = self._create_card(mat)
                    self._card_pool[mid] = card
                    card.setParent(self._icon_container)
                    card.move(x, y)
                    card.show()
                    created_count += 1
            else:
                card = self._card_pool[mid]
                card.move(x, y)
                moved_count += 1



        total_h = self._calc_total_height()
        container_w = max(self._scroll.viewport().width(), self._columns * card_w + padding * 2)
        container_h = max(total_h, self._scroll.viewport().height())
        self._icon_container.setFixedSize(container_w, container_h)

    def set_sort_key(self, key):
        reverse = False
        if key.startswith("-"):
            reverse = True
            key = key[1:]
        self._sort_key = key
        self._sort_materials(reverse)
        self._refresh()

    def _sort_materials(self, reverse=False):
        key = self._sort_key
        self._filtered_materials.sort(
            key=lambda m: self._sort_key_fn(m, key),
            reverse=reverse
        )

    def _reapply_sort(self):
        """按当前 _sort_key 重新排序 _filtered_materials。

        筛选方法重建 _filtered_materials 后调用，确保排序不丢失。
        """
        reverse = False
        key = self._sort_key
        if key.startswith("-"):
            reverse = True
            key = key[1:]
        self._filtered_materials.sort(
            key=lambda m: self._sort_key_fn(m, key),
            reverse=reverse
        )

    @staticmethod
    def _sort_key_fn(m: dict, key: str):
        val = m.get(key)
        if val is None:
            return "" if key != "file_mtime" else 0.0  # 统一缺失值类型
        if isinstance(val, str):
            return val.lower()
        return val

    def _refresh(self):
        if self._view_mode == self.VIEW_LIST:
            self._refresh_list()
        else:
            self._refresh_icon()

    def _refresh_icon(self):

        # 清空所有卡片，确保切换分类时没有残留
        for mid in list(self._card_pool.keys()):
            card = self._card_pool[mid]
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self._card_pool.clear()

        # 重新创建可见区域的卡片
        self._update_visible_cards(False)
        


    def _relayout_cards(self):
        """缩放时：调整所有卡片大小和位置，清理不再需要的卡片"""
        card_w = self._thumb_size
        card_h = self._calc_card_height()
        padding = self.PADDING

        # 只在卡片大小真正改变时才调整大小
        size_changed = (self._last_thumb_size != self._thumb_size)
        if size_changed:
            for mid in self._card_pool:
                card = self._card_pool[mid]
                self._resize_card_simple(card)
            self._last_thumb_size = self._thumb_size

        # 收集当前需要显示的 material_id
        visible_mids = set()
        for i, mat in enumerate(self._filtered_materials):
            mid = mat.get("id", "")
            if not mid:
                continue
            visible_mids.add(mid)

            row = i // self._columns
            col = i % self._columns
            x = padding + col * card_w
            y = padding + row * card_h

            if mid in self._card_pool:
                card = self._card_pool[mid]
                card.move(x, y)
            else:
                card = self._create_card(mat)
                self._card_pool[mid] = card
                card.setParent(self._icon_container)
                card.move(x, y)
                card.show()

        # 清理不再需要的卡片（防止筛选/删除后旧卡片堆叠）
        for mid in list(self._card_pool.keys()):
            if mid not in visible_mids:
                card = self._card_pool.pop(mid)
                card.hide()
                card.setParent(None)
                card.deleteLater()

        total_h = self._calc_total_height()
        container_w = max(self._scroll.viewport().width(), self._columns * card_w + padding * 2)
        container_h = max(total_h, self._scroll.viewport().height())
        self._icon_container.setFixedSize(container_w, container_h)

    def _resize_card_simple(self, card):
        """简单调整卡片大小（不重建布局，避免闪烁）"""
        W = self._thumb_size
        pad = max(3, int(W * 0.06))
        thumb_sz = W - 4 - pad * 2
        text_h = max(12, int(W * 0.16))
        gap = max(2, int(W * 0.02))
        card_h = thumb_sz + text_h + pad * 2 + gap
        
        card.setFixedSize(W, card_h)
        
        layout = card.layout()
        if layout:
            for i in range(layout.count() - 1, -1, -1):
                item = layout.itemAt(i)
                if item:
                    w = item.widget()
                    if w:
                        if isinstance(w, MaterialDragLabel):
                            w.setFixedSize(thumb_sz, thumb_sz)
                            if not getattr(w, '_movie_ref', None):
                                mid = getattr(card, 'material_data', {}).get("id", "")
                                orig = self._thumb_cache.get(mid)
                                if orig and not orig.isNull():
                                    pix = orig.scaled(thumb_sz, thumb_sz,
                                                      QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                                      QtCore.Qt.TransformationMode.SmoothTransformation)
                                    w.setPixmap(pix)
                                else:
                                    # 无缩略图：重新绘制文本到新的尺寸
                                    mat = getattr(card, 'material_data', {})
                                    pix = QtGui.QPixmap(thumb_sz, thumb_sz)
                                    pix.fill(QtGui.QColor(mat.get("color", "#606060")))
                                    painter = QtGui.QPainter(pix)
                                    painter.setPen(QtGui.QColor(255, 255, 255, 60))
                                    font = painter.font()
                                    font.setPointSize(max(8, int(W * 0.08)))
                                    painter.setFont(font)
                                    painter.drawText(pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                                                     mat.get("name_cn", ""))
                                    painter.end()
                                    w.setPixmap(pix)
                        elif isinstance(w, QtWidgets.QWidget) and w.objectName() == "textArea":
                            w.setFixedHeight(text_h)
                            ta_layout = w.layout()
                            if ta_layout:
                                for ci in range(ta_layout.count()):
                                    ci_w = ta_layout.itemAt(ci).widget()
                                    if ci_w:
                                        if ci_w.objectName() == "favLabel":
                                            ci_w.setFixedSize(max(16, int(W * 0.11)), max(16, int(W * 0.11)))
                                            fs_icon = max(12, int(W * 0.09))
                                            ci_w.setStyleSheet(
                                                f"color: {'#FFD700' if ci_w.text() == '★' else '#606060'};"
                                                f"font-size: {fs_icon}px; background: transparent;")
                                        elif ci_w.objectName() == "nameLabel":
                                            fs_txt = max(9, int(W * 0.07))
                                            ci_w.setStyleSheet(
                                                f"color: #d0d0d0; font-size: {fs_txt}px; background: transparent;")
            
            layout.setContentsMargins(pad, pad, pad, pad)
            layout.setSpacing(gap)
            layout.update()

    def _resize_card_full(self, card, material):
        """重建卡片内部布局（复用子控件），所有尺寸基于当前 W 等比计算"""
        W = self._thumb_size
        pad = max(3, int(W * 0.06))
        thumb_sz = W - 4 - pad * 2
        text_h = max(12, int(W * 0.16))
        gap = max(2, int(W * 0.02))
        card_h = thumb_sz + text_h + pad * 2 + gap
        card.setFixedSize(W, card_h)

        thumb = card.findChild(MaterialDragLabel)
        fav = card.findChild(QtWidgets.QLabel, "favLabel")
        name_label = card.findChild(QtWidgets.QLabel, "nameLabel")
        for w in (thumb, fav, name_label):
            if w:
                w.setParent(None)
        old = card.layout()
        if old:
            QtWidgets.QWidget().setLayout(old)

        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(gap)

        if thumb:
            thumb.setParent(card)
            thumb.setFixedSize(thumb_sz, thumb_sz)
            thumb.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            thumb.setScaledContents(False)
            # GIF 动图使用 QMovie，不需要 setPixmap
            if thumb._movie_ref:
                pass
            else:
                mid = material.get("id", "")
                orig = self._thumb_cache.get(mid)
                if orig and not orig.isNull():
                    pix = orig.scaled(thumb_sz, thumb_sz,
                                      QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                      QtCore.Qt.TransformationMode.SmoothTransformation)
                else:
                    # 尝试从 .zasset thumb_bytes 加载
                    is_zas = material.get("is_zasset", False)
                    t_bytes = material.get("thumb_bytes") if is_zas else None
                    if is_zas and t_bytes:
                        raw = QtGui.QPixmap()
                        if raw.loadFromData(t_bytes):
                            self._thumb_cache[mid] = raw
                            pix = raw.scaled(thumb_sz, thumb_sz,
                                             QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                             QtCore.Qt.TransformationMode.SmoothTransformation)
                        else:
                            pix = QtGui.QPixmap(thumb_sz, thumb_sz)
                            pix.fill(QtGui.QColor(material.get("color", "#606060")))
                            p = QtGui.QPainter(pix)
                            p.setPen(QtGui.QColor(255, 255, 255, 60))
                            f = p.font(); f.setPointSize(max(8, int(W * 0.08))); p.setFont(f)
                            p.drawText(pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                                       material.get("name_cn", ""))
                            p.end()
                    else:
                        pix = QtGui.QPixmap(thumb_sz, thumb_sz)
                        pix.fill(QtGui.QColor(material.get("color", "#606060")))
                        p = QtGui.QPainter(pix)
                        p.setPen(QtGui.QColor(255, 255, 255, 60))
                        f = p.font(); f.setPointSize(max(8, int(W * 0.08))); p.setFont(f)
                        p.drawText(pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                                   material.get("name_cn", ""))
                        p.end()
                thumb.setPixmap(pix)
            layout.addWidget(thumb, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        text_area = QtWidgets.QWidget()
        text_area.setObjectName("textArea")
        text_area.setFixedHeight(text_h)
        text_row = QtWidgets.QHBoxLayout(text_area)
        text_row.setContentsMargins(0, max(1, int(W * 0.01)), 0, 0)
        text_row.setSpacing(max(2, int(W * 0.015)))

        if fav:
            fav.setParent(text_area)
            fav.setFixedSize(max(16, int(W * 0.11)), max(16, int(W * 0.11)))
            is_fav = material.get("_favorited", False)
            fav.setText("\u2605" if is_fav else "\u2606")
            fav.setStyleSheet(
                f"color: {'#FFD700' if is_fav else '#606060'};"
                f"font-size: {max(12, int(W * 0.09))}px; background: transparent;")
            text_row.addWidget(fav)
        if name_label:
            name_label.setParent(text_area)
            name_label.setStyleSheet(
                f"color: #d0d0d0; font-size: {max(9, int(W * 0.07))}px; background: transparent;")
            text_row.addWidget(name_label, 1)

        layout.addWidget(text_area)
        card.show()
        card.update()

    def _refresh_list(self):
        self._table_model.set_materials(self._filtered_materials)

    def _scroll_to_selected(self):
        """F 键：将视口滚动到选中卡片居中位置"""
        if not self._selected_material:
            return
        mid = self._selected_material.get("id", "")
        card = self._card_pool.get(mid)
        if card and self._scroll:
            # 计算卡片相对于视口中心的偏移并滚动
            viewport_rect = self._scroll.viewport().rect()
            card_rect = self._scroll.widget().childrenRect()
            # 获取卡片在 scroll area widget 坐标中的位置
            card_pos = card.pos()
            card_size = card.size()
            # 目标滚动位置：卡片居中
            target_x = max(0, card_pos.x() + card_size.width() // 2 - viewport_rect.width() // 2)
            target_y = max(0, card_pos.y() + card_size.height() // 2 - viewport_rect.height() // 2)
            self._scroll.ensureVisible(
                card_pos.x() + card_size.width() // 2,
                card_pos.y() + card_size.height() // 2,
                viewport_rect.width() // 4,
                viewport_rect.height() // 4,
            )
        # 列表视图切换回对应行
        mid_idx = None
        for i, m in enumerate(self._filtered_materials):
            if m.get("id") == mid:
                mid_idx = i
                break
        if mid_idx is not None and hasattr(self._table_view, 'scrollTo'):
            idx = self._table_model.index(mid_idx, 0)
            self._table_view.scrollTo(idx)

    def _clear_grid(self):
        # 虚拟化：直接清除卡片池中的所有卡片
        for mid in list(self._card_pool.keys()):
            card = self._card_pool[mid]
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self._card_pool.clear()

    def _create_card(self, material):
        W = self._thumb_size
        # ── 全部按 W 比例计算，确保卡片像单一物体等比缩放 ──
        pad     = max(3, int(W * 0.06))           # 四周边距 ~6%
        thumb_sz = W - 4 - pad * 2               # -4 = CSS border 2px×2边
        text_h  = max(12, int(W * 0.16))           # 文本区高度 ~16%
        gap     = max(2, int(W * 0.02))            # 缩略图与文本间距
        card_h  = thumb_sz + text_h + pad * 2 + gap

        card = QtWidgets.QFrame()
        card.setFixedSize(W, card_h)
        card.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        card.setObjectName("thumbnailCard")
        card.material_data = material
        card.setStyleSheet(
            "QFrame#thumbnailCard { background-color: #252525;"
            "border: 2px solid #3a3a3a; border-radius: 6px; }"
            "QFrame#thumbnailCard:hover { border: 2px solid #555555; background-color: #2a2a2a; }"
        )

        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(gap)

        # ── 缩略图 ──
        thumb = MaterialDragLabel(material)
        MaterialDragLabel.grid_ref = self  # 多选拖拽需要引用 grid
        thumb.setFixedSize(thumb_sz, thumb_sz)
        thumb.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        thumb.setScaledContents(False)

        thumb_path = material.get("thumbnail_path", "")
        is_zasset = material.get("is_zasset", False)
        thumb_bytes = material.get("thumb_bytes") if is_zasset else None
        mid = material.get("id", "")
        pix_loaded = False

        # ⚡ .zasset 模式：从 thumb_bytes 加载缩略图
        if is_zasset and thumb_bytes:
            # GIF 动图 → 读首帧为静态（避免 QMovie 崩溃）
            if len(thumb_bytes) >= 3 and thumb_bytes[:3] == b"GIF":
                raw = QtGui.QPixmap()
                raw.loadFromData(thumb_bytes)
                if not raw.isNull():
                    self._thumb_cache[mid] = raw
                    pix = raw.scaled(thumb_sz, thumb_sz,
                                     QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                     QtCore.Qt.TransformationMode.SmoothTransformation)
                    thumb.setPixmap(pix)
                    pix_loaded = True
            else:
                raw = QtGui.QPixmap()
                if raw.loadFromData(thumb_bytes):
                    w, h = raw.width(), raw.height()
                    if w != h:
                        size = min(w, h)
                        raw = raw.copy((w - size) // 2, (h - size) // 2, size, size)
                    self._thumb_cache[mid] = raw  # 缓存原图，缩放时复用
                    pix = raw.scaled(thumb_sz, thumb_sz,
                                     QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                     QtCore.Qt.TransformationMode.SmoothTransformation)
                    thumb.setPixmap(pix)
                    pix_loaded = True

        # 旧模式（文件夹资产）：从文件路径加载
        # .aicon / .mp4 → 读首帧为静态（避免 QMovie 崩溃）
        if not pix_loaded and thumb_path.lower().endswith(('.aicon', '.mp4')) and os.path.isfile(thumb_path):
            raw = QtGui.QPixmap(thumb_path)
            if not raw.isNull():
                self._thumb_cache[mid] = raw
                pix = raw.scaled(thumb_sz, thumb_sz,
                                 QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                 QtCore.Qt.TransformationMode.SmoothTransformation)
                thumb.setPixmap(pix)
                pix_loaded = True

        if not pix_loaded and thumb_path and os.path.isfile(thumb_path):
            raw = QtGui.QPixmap(thumb_path)
            if not raw.isNull():
                w, h = raw.width(), raw.height()
                if w != h:
                    size = min(w, h)
                    raw = raw.copy((w - size) // 2, (h - size) // 2, size, size)
                self._thumb_cache[mid] = raw  # 缓存原图，缩放时复用
                pix = raw.scaled(thumb_sz, thumb_sz,
                                 QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                 QtCore.Qt.TransformationMode.SmoothTransformation)
                thumb.setPixmap(pix)
                pix_loaded = True

        if not pix_loaded:
            pix = QtGui.QPixmap(thumb_sz, thumb_sz)
            pix.fill(QtGui.QColor(material.get("color", "#606060")))
            painter = QtGui.QPainter(pix)
            painter.setPen(QtGui.QColor(255, 255, 255, 60))
            font = painter.font()
            font.setPointSize(max(8, int(W * 0.08)))
            painter.setFont(font)
            painter.drawText(pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                             material.get("name_cn", ""))
            painter.end()
            thumb.setPixmap(pix)
        layout.addWidget(thumb, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        # ── 文本区 ──
        text_area = QtWidgets.QWidget()
        text_area.setObjectName("textArea")
        text_area.setFixedHeight(text_h)

        text_row = QtWidgets.QHBoxLayout(text_area)
        text_row.setContentsMargins(0, max(1, int(W * 0.01)), 0, 0)
        text_row.setSpacing(max(2, int(W * 0.015)))

        fav = QtWidgets.QLabel()
        fav.setObjectName("favLabel")
        fav.setFixedSize(max(16, int(W * 0.11)), max(16, int(W * 0.11)))
        fav.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        is_fav = material.get("_favorited", False)
        fav.setText("\u2605" if is_fav else "\u2606")
        fav.setStyleSheet(
            f"color: {'#FFD700' if is_fav else '#606060'};"
            f"font-size: {max(12, int(W * 0.09))}px; background: transparent;"
        )
        fav.setToolTip("\u70b9\u51fb\u6dfb\u52a0/\u53d6\u6d88\u6536\u85cf")
        fav.mousePressEvent = lambda e, m=material, i=fav: self._toggle_favorite(m, i)
        text_row.addWidget(fav)

        name_label = QtWidgets.QLabel(material.get("name_cn", material.get("name", "")))
        name_label.setObjectName("nameLabel")
        name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        name_label.setStyleSheet(
            f"color: #d0d0d0; font-size: {max(9, int(W * 0.07))}px; background: transparent;"
        )
        text_row.addWidget(name_label, 1)

        layout.addWidget(text_area)

        self._attach_card_events(card)
        return card

    def _toggle_favorite(self, material, indicator=None):
        """切换收藏状态，每次从 dict 读取最新状态，避免闭包捕获旧值"""
        current_fav = material.get("_favorited", False)
        new_fav = not current_fav
        material["_favorited"] = new_fav
        self.favoriteToggled.emit(material.get("id", ""), new_fav)
        if indicator:
            indicator.setText("\u2605" if new_fav else "\u2606")
            # 只改颜色和字符，保留现有字号（卡片动态计算 vs 面板固定字号）
            old_style = indicator.styleSheet()
            m = re.search(r'font-size:\s*[\d.]+px', old_style)
            font_part = m.group(0) if m else 'font-size: 15px'
            new_color = '#FFD700' if new_fav else '#606060'
            indicator.setStyleSheet(
                f"color: {new_color}; {font_part}; background: transparent;"
            )

    def _attach_card_events(self, card):
        _drag_threshold = 20  # 像素，超过视为拖拽

        def mouse_press(event):
            if event.button() == QtCore.Qt.MouseButton.RightButton:
                self._on_context_menu(event.position().toPoint(), card)
                return

            modifiers = QtWidgets.QApplication.keyboardModifiers() if hasattr(QtWidgets, 'QApplication') else QtCore.Qt.KeyboardModifier.NoModifier
            try:
                modifiers = event.modifiers()
            except Exception:
                pass

            ctrl = bool(modifiers & QtCore.Qt.KeyboardModifier.ControlModifier)
            shift = bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier)

            if ctrl:
                if hasattr(card, 'material_data') and card.material_data.get("id") not in self._selected_materials:
                    self._on_card_ctrl_click(card)
            elif shift:
                self._on_card_shift_click(card)
            else:
                # 不立即切换选择——等 mouseRelease 判断是点击还是拖拽
                card._press_pos = event.position().toPoint()

        def mouse_release(event):
            if event.button() != QtCore.Qt.MouseButton.LeftButton:
                return
            press_pos = getattr(card, '_press_pos', None)
            if press_pos is None:
                return
            # 鼠标移动超过阈值 → 视为拖拽，不改变选择
            if (event.position().toPoint() - press_pos).manhattanLength() > _drag_threshold:
                return
            # 未移动 → 视为点击，切换为单选
            if hasattr(card, 'material_data'):
                self._on_card_clicked(card)

        card.mousePressEvent = mouse_press
        card.mouseReleaseEvent = mouse_release

        def mouse_double(event):
            # 取消双击应用材质
            pass
        card.mouseDoubleClickEvent = mouse_double

        card.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.PreventContextMenu)

    def _on_empty_area_menu(self, pos):
        """空白区域右键 → 创建资产 / 粘贴 / 导入"""
        child = self._icon_container.childAt(pos)
        if child and hasattr(child, 'material_data'):
            return
        menu = QtWidgets.QMenu(self)
        create_action = menu.addAction("创建资产")
        paste_action = menu.addAction("📋 粘贴")
        menu.addSeparator()
        select_all_action = menu.addAction("全选")
        import_sub = menu.addMenu("📥 从外部导入")
        import_files_action = import_sub.addAction("📄 从文件导入")
        import_zasset_action = import_sub.addAction("📦 导入 .zasset 资产")
        import_sub.addSeparator()
        import_textures_action = import_sub.addAction("🖼️ 导入贴图")
        import_hdr_action = import_sub.addAction("☀️ 导入HDR")
        action = menu.exec(self._icon_container.mapToGlobal(pos))
        if action == create_action:
            self.createAssetRequested.emit({})
        elif action == paste_action:
            self.pasteRequested.emit()
        elif action == select_all_action:
            self._select_all_cards()
        elif action == import_files_action:
            self.importRequested.emit("files")
        elif action == import_zasset_action:
            self.importRequested.emit("zasset")
        elif action == import_textures_action:
            self.importRequested.emit("textures")
        elif action == import_hdr_action:
            self.importRequested.emit("hdr")

    def _on_card_clicked(self, card):
        """普通点击：单选"""
        if not hasattr(card, 'material_data'):
            return
        mat = card.material_data
        mid = mat.get("id")
        self._selected_materials.clear()
        self._selected_materials[mid] = mat
        self._selected_material = mat
        self._last_clicked_id = mid
        self._refresh_card_highlights()
        self.materialSelected.emit(mat)
        self.selectionChanged.emit()

    def _on_card_ctrl_click(self, card):
        """Ctrl+点击：切换多选"""
        if not hasattr(card, 'material_data'):
            return
        mat = card.material_data
        mid = mat.get("id")
        if mid in self._selected_materials:
            del self._selected_materials[mid]
        else:
            self._selected_materials[mid] = mat
        self._last_clicked_id = mid
        self._selected_material = mat
        self._refresh_card_highlights()
        self.selectionChanged.emit()

    def _on_card_shift_click(self, card):
        """Shift+点击：范围多选"""
        if not hasattr(card, 'material_data') or not self._last_clicked_id:
            return
        mat = card.material_data
        target_id = mat.get("id")

        # 在 _filtered_materials 中找到 last 和 target 的 index
        ids = [m.get("id") for m in self._filtered_materials]
        try:
            idx_last = ids.index(self._last_clicked_id)
            idx_target = ids.index(target_id)
        except ValueError:
            return

        lo, hi = min(idx_last, idx_target), max(idx_last, idx_target)
        self._selected_materials.clear()
        for i in range(lo, hi + 1):
            m = self._filtered_materials[i]
            self._selected_materials[m.get("id")] = m
        self._selected_material = mat
        self._last_clicked_id = target_id
        self._refresh_card_highlights()
        self.selectionChanged.emit()

    def _refresh_card_highlights(self):
        """刷新卡片的选中高亮（仅更新状态变化的卡片）"""
        for mid in self._card_pool:
            card = self._card_pool[mid]
            if not hasattr(card, 'material_data'):
                continue
            is_selected = mid in self._selected_materials
            # 跳过状态未变化的卡片
            prev_selected = card.property("_card_selected")
            if prev_selected == is_selected:
                continue
            card.setProperty("_card_selected", is_selected)
            border_color = '#5294e2' if is_selected else '#3a3a3a'
            hover_qss = "" if is_selected else "QFrame#thumbnailCard:hover { border: 2px solid #555555; background-color: #2a2a2a; }"
            card.setStyleSheet(
                f"QFrame#thumbnailCard {{ background-color: #252525;"
                f"border: 2px solid {border_color}; border-radius: 6px; }}"
                + hover_qss
            )

    def _refresh_card_favorites(self):
        """刷新所有卡片池中已有卡片的收藏星标"""
        for mid in self._card_pool:
            card = self._card_pool[mid]
            if not hasattr(card, 'material_data'):
                continue
            is_fav = card.material_data.get("_favorited", False)
            fav = card.findChild(QtWidgets.QLabel, "favLabel")
            if fav:
                fav.setText("\u2605" if is_fav else "\u2606")
                old_style = fav.styleSheet()
                m = re.search(r'font-size:\s*[\d.]+px', old_style) if old_style else None
                font_part = m.group(0) if m else 'font-size: 15px'
                new_color = '#FFD700' if is_fav else '#606060'
                fav.setStyleSheet(
                    f"color: {new_color}; {font_part}; background: transparent;"
                )

    def _on_card_double_clicked(self, card):
        if hasattr(card, 'material_data'):
            print(f"[MaterialLibrary] \u53cc\u51fb\u5e94\u7528\u6750\u8d28: {card.material_data.get('name_cn')}")
            self.materialApplied.emit(card.material_data)

    @staticmethod
    def _collect_node_files(json_path, variant_types=None):
        """收集资产中的节点文件（zmetal / ma），返回 [(display_name, file_path)]"""
        import os
        node_files = []

        if not json_path or not os.path.isdir(json_path):
            return node_files

        # 1. 扫描根目录下所有节点文件（.zmetal / .ma）
        try:
            for fname in sorted(os.listdir(json_path)):
                fp = os.path.join(json_path, fname)
                if not os.path.isfile(fp):
                    continue
                lower = fname.lower()
                if lower.endswith('.zmetal'):
                    node_files.append((fname, fp))
                elif lower.endswith('.ma') and fname != 'node.ma':
                    node_files.append((fname, fp))
        except OSError:
            pass

        # 2. 检查变体目录下的 node.zmetal
        if variant_types:
            variants_dir = os.path.join(json_path, 'variants')
            if os.path.isdir(variants_dir):
                try:
                    for ver_name in sorted(os.listdir(variants_dir)):
                        ver_dir = os.path.join(variants_dir, ver_name)
                        if not os.path.isdir(ver_dir):
                            continue
                        # 扫描变体版本目录下的节点文件
                        for fname in sorted(os.listdir(ver_dir)):
                            fl = fname.lower()
                            if fl.endswith('.zmetal') or (fl.endswith('.ma') and fname != 'node.ma'):
                                fp = os.path.join(ver_dir, fname)
                                if os.path.isfile(fp):
                                    node_files.append((f'variants/{ver_name}/{fname}', fp))
                        # 检查 LOD 子目录
                        for lod_name in sorted(os.listdir(ver_dir)):
                            lod_dir = os.path.join(ver_dir, lod_name)
                            if not os.path.isdir(lod_dir):
                                continue
                            for fname in sorted(os.listdir(lod_dir)):
                                fl = fname.lower()
                                if fl.endswith('.zmetal') or (fl.endswith('.ma') and fname != 'node.ma'):
                                    fp = os.path.join(lod_dir, fname)
                                    if os.path.isfile(fp):
                                        node_files.append((f'variants/{ver_name}/{lod_name}/{fname}', fp))
                except OSError:
                    pass

        return node_files

    def _on_context_menu(self, pos, card):
        mat = card.material_data if hasattr(card, 'material_data') else {}
        sub_lib = mat.get('sub_library', '')
        sm = SettingsManager()
        sm.load()
        font_size = sm.get("font_size", 13)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(_get_sub_style(font_size))

        # ── 导入始终在最上 ──
        json_path = mat.get('json_path', '')
        import_actions = {}
        import_action = None
        zlight_renderer = None  # (action, renderer_name) for zlight
        if json_path and json_path.endswith('.zasset'):
            from ..integration.import_executor import get_available_formats
            formats = get_available_formats(json_path)
            has_zlight = "zlight" in formats
            other_formats = [f for f in formats if f != "zlight"]

            # zlight 格式 → 渲染器子菜单（按渲染器创建灯光）
            if has_zlight:
                zlight_sub = QtWidgets.QMenu('\U0001f4e5 导入灯光（选择渲染器）', menu)
                zlight_sub.setStyleSheet(_get_sub_style(font_size))
                for r_name, r_label in [("arnold", "Arnold"), ("vray", "V-Ray"),
                                         ("redshift", "Redshift"), ("maya", "Maya 原生")]:
                    a = zlight_sub.addAction(f'  {r_label}')
                    zlight_renderer = a, r_name
                    import_actions[a] = f"zlight:{r_name}"
                menu.addMenu(zlight_sub)

            # 其他格式
            if other_formats:
                if len(other_formats) == 1:
                    import_action = menu.addAction(f'\U0001f4e5 \u5bfc\u5165 {other_formats[0]}')
                    # Store the actual format for action matching
                    oa = import_action
                    import_actions[oa] = other_formats[0]
                elif len(other_formats) > 1:
                    other_sub = QtWidgets.QMenu('\U0001f4e5 \u5bfc\u5165', menu)
                    other_sub.setStyleSheet(_get_sub_style(font_size))
                    for fmt in other_formats:
                        a = other_sub.addAction(f'  {fmt}')
                        import_actions[a] = fmt
                    menu.addMenu(other_sub)
        else:
            import_action = menu.addAction('\U0001f4e5 \u5bfc\u5165')

        # ── 变体几何体导入 ──
        variant_types = mat.get('variant_types', [])
        variant_actions = {}
        mat_actions = {}
        delete_actions = {}
        if variant_types and json_path.endswith('.zasset'):
            from ..core.zasset_io import ZassetIO
            variants_data = ZassetIO.read_variants(json_path)
            versions = variants_data.get('versions', [])

            if versions:
                geom_sub = QtWidgets.QMenu('\U0001f4e6 导入几何体', menu)
                geom_sub.setStyleSheet(_get_sub_style(font_size))

                # LOD 精度子菜单
                default_version = variants_data.get('default_version', versions[0].get('id', ''))
                default_lod = variants_data.get('default_lod', '')
                current_ver = None
                for v in versions:
                    if v.get('id') == default_version:
                        current_ver = v
                        break
                if not current_ver:
                    current_ver = versions[0]

                lods = current_ver.get('lods', [])
                if len(lods) > 1 or 'lod' in variant_types:
                    lod_sub = QtWidgets.QMenu('LOD 精度', geom_sub)
                    lod_sub.setStyleSheet(_get_sub_style(font_size))
                    ver_lods = current_ver.get('lods', [])
                    for l in ver_lods:
                        lid = l.get('id', '')
                        llabel = l.get('label', lid)
                        stats = l.get('stats', {})
                        tris = stats.get('triangles', 0)
                        text = f'{llabel} ({lid.upper()})'
                        if tris:
                            text += f'  —  {tris:,}面'
                        a = lod_sub.addAction(text)
                        variant_actions[a] = (default_version, lid)
                    lod_sub.addSeparator()
                    a_all = lod_sub.addAction('选择版本和LOD...')
                    variant_actions[a_all] = ('__choose__', '__choose__')

                    # ── LOD 删除入口 ──
                    lod_sub.addSeparator()
                    for l in ver_lods:
                        lid = l.get('id', '')
                        llabel = l.get('label', lid)
                        a = lod_sub.addAction(f'🗑 删除 {llabel} ({lid.upper()})')
                        delete_actions[a] = ('lod', default_version, lid)
                    geom_sub.addMenu(lod_sub)
                else:
                    # 仅一个 LOD：直接用
                    lid = lods[0].get('id', '') if lods else ''
                    a = geom_sub.addAction(f'导入几何体 ({lods[0].get("label", "")})' if lods else '导入几何体')
                    variant_actions[a] = (default_version, lid)

                # 切换版本子菜单
                if len(versions) > 1:
                    geom_sub.addSeparator()
                    ver_sub = QtWidgets.QMenu('切换版本', geom_sub)
                    ver_sub.setStyleSheet(_get_sub_style(font_size))
                    for v in versions:
                        vid = v.get('id', '')
                        vtag = v.get('tag', vid)
                        vlabel = v.get('label', vid)
                        is_default = (vid == default_version)
                        prefix = '\u25cf ' if is_default else '\u25cb '
                        text = f'{prefix}{vtag} - {vlabel}'
                        a = ver_sub.addAction(text)
                        # 版本切换：使用该版本的默认 LOD
                        v_lods = v.get('lods', [])
                        v_default_lod = default_lod if any(l.get('id') == default_lod for l in v_lods) else (
                            v_lods[0].get('id', '') if v_lods else ''
                        )
                        variant_actions[a] = (vid, v_default_lod)
                    ver_sub.addSeparator()
                    a_all2 = ver_sub.addAction('选择版本和LOD...')
                    variant_actions[a_all2] = ('__choose__', '__choose__')

                    # ── 版本独立材质入口 ──
                    has_version_mat = any(v.get('material') for v in versions)
                    if has_version_mat:
                        ver_sub.addSeparator()
                        for v in versions:
                            if v.get('material'):
                                vid = v.get('id', '')
                                vtag = v.get('tag', vid)
                                vlabel = v.get('label', vid)
                                a = ver_sub.addAction(f'导入 {vtag} 版本材质')
                                mat_actions[a] = (vid,)

                    # ── 版本删除入口 ──
                    ver_sub.addSeparator()
                    for v in versions:
                        vid = v.get('id', '')
                        vtag = v.get('tag', vid)
                        vlabel = v.get('label', vid)
                        a = ver_sub.addAction(f'🗑 删除 {vtag} - {vlabel}')
                        delete_actions[a] = ('version', vid)

                    geom_sub.addMenu(ver_sub)

                menu.addMenu(geom_sub)

        # 分类专属按钮
        if sub_lib == 'materials':
            menu.addAction('\u5e94\u7528\u6750\u8d28\u5230\u9009\u4e2d\u5bf9\u8c61').triggered.connect(
                lambda: self._do_apply_material(mat))
        elif sub_lib == 'textures':
            menu.addAction('应用材质到选中对象').triggered.connect(
                lambda: self._do_apply_material(mat))
            # 提前获取贴图列表，用于判断是否有分辨率子目录
            tex_names = _get_texture_names(json_path)

            # 贴图额外：创建材质子菜单
            presets = _load_presets('material_presets')
            if presets:
                mat_sub = QtWidgets.QMenu('创建材质', menu)
                mat_sub.setStyleSheet(_get_sub_style(font_size))

                # 检测是否有分辨率子目录
                resolutions = sorted(set(
                    tn.split('/')[0] for tn in tex_names if '/' in tn
                ))
                has_res = len(resolutions) > 1  # 仅多个精度时才需要选择

                for p in presets:
                    if has_res:
                        # 精度子目录 → 二级菜单：材质名 → 精度列表
                        preset_sub = QtWidgets.QMenu(p['name'], mat_sub)
                        preset_sub.setStyleSheet(_get_sub_style(font_size))
                        for res in resolutions:
                            a = preset_sub.addAction(f'  {res}')
                            a.triggered.connect(
                                lambda *a, nt=p['node_type'], md=mat, r=res:
                                    self.createMaterialRequested.emit(nt, md, r))
                        mat_sub.addMenu(preset_sub)
                    elif len(resolutions) == 1:
                        # 只有一个精度，直接创建不弹菜单
                        a = mat_sub.addAction(f'  {p["name"]}')
                        a.triggered.connect(
                            lambda *a, nt=p['node_type'], md=mat, r=resolutions[0]:
                                self.createMaterialRequested.emit(nt, md, r))
                    else:
                        a = mat_sub.addAction(f'  {p["name"]}')
                        a.triggered.connect(
                            lambda *a, nt=p['node_type'], md=mat:
                                self.createMaterialRequested.emit(nt, md, ''))
                menu.addMenu(mat_sub)
            # 贴图额外：导入贴图子菜单
            if tex_names:
                tex_sub = QtWidgets.QMenu('导入贴图', menu)
                tex_sub.setStyleSheet(_get_sub_style(font_size))

                # 按分辨率分组
                resolution_groups = {}
                for tn in tex_names:
                    parts = tn.split('/', 1)
                    if len(parts) == 2:
                        res, fname = parts
                        resolution_groups.setdefault(res, []).append(fname)
                    else:
                        resolution_groups.setdefault('', []).append(tn)

                if len(resolution_groups) > 1:
                    # 多个精度 → 二级菜单
                    for res, files in sorted(resolution_groups.items()):
                        res_label = res if res else '根目录'
                        res_sub = QtWidgets.QMenu(res_label, tex_sub)
                        res_sub.setStyleSheet(_get_sub_style(font_size))
                        for fname in files:
                            full_path = f"{res}/{fname}" if res else fname
                            action = res_sub.addAction(f'  {fname}')
                            action.triggered.connect(
                                lambda *a, jp=json_path, fp=full_path:
                                    self.importSingleTextureRequested.emit(jp, fp))
                        res_sub.addSeparator()
                        res_paths = [f"{res}/{f}" if res else f for f in files]
                        all_label = f'  导入全部 ({res_label})'
                        action_all = res_sub.addAction(all_label)
                        action_all.triggered.connect(
                            lambda *a, jp=json_path, fps=res_paths:
                                self.importTexturesSharedUVRequested.emit(jp, fps))
                        tex_sub.addMenu(res_sub)
                else:
                    # 0 或 1 个精度 — 扁平列表
                    all_files = []
                    for res, files in resolution_groups.items():
                        for fname in files:
                            full_path = f"{res}/{fname}" if res else fname
                            a = tex_sub.addAction(f'  {fname}')
                            a.triggered.connect(lambda *a, jp=json_path, tn=full_path:
                                self.importSingleTextureRequested.emit(jp, tn))
                            all_files.append(full_path)
                    tex_sub.addSeparator()
                    a_all = tex_sub.addAction('  导入全部')
                    a_all.triggered.connect(lambda *a, jp=json_path, fps=all_files:
                        self.importTexturesSharedUVRequested.emit(jp, fps))

                menu.addMenu(tex_sub)
            # 贴图额外：指定贴图到选中材质
            menu.addAction('指定贴图到材质').triggered.connect(
                lambda: self.assignTextureToMaterialRequested.emit(mat))
        elif sub_lib == 'lights':
            menu.addAction('应用灯光参数').triggered.connect(
                lambda: print('[MaterialLibrary] 应用灯光参数: TODO'))
        elif sub_lib == 'hdr':
            preset_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'Assets', 'HDR_ligt')
            if os.path.isdir(preset_dir):
                ma_files = sorted(
                    [f for f in os.listdir(preset_dir) if f.lower().endswith('.ma')])
                dome_sub = QtWidgets.QMenu('创建环境光', menu)
                dome_sub.setStyleSheet(_get_sub_style(font_size))
                for ma_file in ma_files:
                    preset_path = os.path.join(preset_dir, ma_file)
                    preset_name = os.path.splitext(ma_file)[0]
                    a = dome_sub.addAction(f'  {preset_name}')
                    a.triggered.connect(lambda *a, pp=preset_path, m=mat:
                        self.createDomeLightRequested.emit(pp, m))
                menu.addMenu(dome_sub)
                menu.addAction('指定贴图').triggered.connect(
                    lambda: self.assignHdrToDomeRequested.emit(mat))

        menu.addSeparator()
        mid = mat.get('id', '')
        self._build_favorites_submenu(menu, mid, font_size)
        menu.addSeparator()

        select_all_action = menu.addAction('全选')
        duplicate_action = menu.addAction('复制')
        folder_action = menu.addAction('📂 打开文件夹')
        menu.addSeparator()

        if self._manager:
            move_sub = self._build_category_submenu('➕ 移动到', mid, move=True, font_size=font_size)
            if move_sub: menu.addMenu(move_sub)
            copy_sub = self._build_category_submenu('📁 复制到', mid, move=False, font_size=font_size)
            if copy_sub: menu.addMenu(copy_sub)
        menu.addSeparator()

        edit_action = menu.addAction('编辑')
        thumb_menu = QtWidgets.QMenu('更新缩略图', menu)
        thumb_menu.setStyleSheet(_get_sub_style(font_size))
        cap_action = thumb_menu.addAction('📷 截取')
        imp_thumb_action = thumb_menu.addAction('📂 导入')
        menu.addMenu(thumb_menu)
        
        update_asset_action = menu.addAction('更新资产')
        delete_action = menu.addAction('删除')
        menu.addSeparator()

        # ── 预览节点（zmetal / ma 文件）──
        preview_node_actions = {}
        node_files = self._collect_node_files(json_path, mat.get('variant_types', []))
        if node_files:
            preview_sub = QtWidgets.QMenu('🔍 预览节点', menu)
            preview_sub.setStyleSheet(_get_sub_style(font_size))
            for display_name, file_path in node_files:
                a = preview_sub.addAction(f'  {display_name}')
                preview_node_actions[a] = file_path
            menu.addMenu(preview_sub)

        ai_action = menu.addAction('\U0001f9e0 AI 分析缩略图')

        action = menu.exec(card.mapToGlobal(pos))
        if action is None:  # 菜单被取消，不做任何操作
            return
        if action in preview_node_actions:
            self.previewNodeRequested.emit(preview_node_actions[action])
            return
        if action == select_all_action:
            self._select_all_cards()
        elif action == duplicate_action:
            self._copy_selected_to_clipboard(mid=mid, mat=mat)
        elif action == folder_action:
            self.openFolderRequested.emit(mat)
        elif action == edit_action:
            self.editMaterialRequested.emit(mat)
        elif action == ai_action:
            self.aiAnalysisRequested.emit(mat)
        elif action == cap_action:
            self.thumbnailCaptureRequested.emit(mat.get('id', ''))
        elif action == imp_thumb_action:
            self.thumbnailImportRequested.emit(mat.get('id', ''))
        elif action == update_asset_action:
            self.updateAssetRequested.emit(mid)
        elif action == delete_action:
            ids = list(self._selected_materials.keys()) if self._selected_materials else [mid]
            self.deleteRequested.emit(ids)
        elif action in import_actions:
            fmt = import_actions[action]
            # 收集所有选中+右键卡片路径
            paths = set()
            if self._selected_materials:
                for m in self._selected_materials.values():
                    p = m.get("json_path", "")
                    if p:
                        paths.add(p)
            if json_path:
                paths.add(json_path)
            # zlight:renderer → 以指定渲染器导入
            if fmt.startswith("zlight:"):
                renderer = fmt.split(":", 1)[1]
                for p in paths:
                    self.importZlightAsRenderer.emit(p, renderer)
            else:
                for p in paths:
                    self.assetImportRequested.emit(p, fmt)
        elif action == import_action and json_path:
            fmt = formats[0] if json_path.endswith('.zasset') else os.path.splitext(json_path)[1].lstrip('.')
            # 收集所有选中+右键卡片路径
            paths = set()
            if self._selected_materials:
                for m in self._selected_materials.values():
                    p = m.get("json_path", "")
                    if p:
                        paths.add(p)
            if json_path:
                paths.add(json_path)
            for p in paths:
                self.assetImportRequested.emit(p, fmt)
        elif action in variant_actions:
            version, lod = variant_actions[action]
            if version == '__choose__' or lod == '__choose__':
                # 弹出选择对话框
                from .variant_import_dialog import VariantImportDialog
                dlg = VariantImportDialog(json_path, self)
                if dlg.exec() == QtWidgets.QDialog.Accepted:
                    version, lod = dlg.result()
                    if version and lod:
                        self.variantGeometryImportRequested.emit(json_path, version, lod)
            elif version and lod:
                self.variantGeometryImportRequested.emit(json_path, version, lod)
        elif action in mat_actions:
            version = mat_actions[action][0]
            self.variantMaterialImportRequested.emit(json_path, version)
        elif action in delete_actions:
            action_type, *args = delete_actions[action]
            if action_type == 'version':
                self.variantVersionDeleteRequested.emit(json_path, args[0])
            elif action_type == 'lod':
                self.variantLodDeleteRequested.emit(json_path, args[0], args[1])

    def _do_apply_material(self, mat):
        print(f"[MaterialLibrary] \u5e94\u7528\u6750\u8d28: {mat.get('name_cn')}")
        self.materialApplied.emit(mat)

    def _build_favorites_submenu(self, menu, mid, font_size=13):
        if not self._manager:
            menu.addAction('☆ 添加到收藏夹').triggered.connect(
                lambda: self.addToFavoriteRequested.emit(mid, ''))
            return
        fav_sub = QtWidgets.QMenu('☆ 添加到收藏夹', menu)
        fav_sub.setStyleSheet(_get_sub_style(font_size))
        for cid in self._manager._favorites.keys():
            name = self._manager._favorites_meta.get(cid, '默认收藏夹' if cid == 'default' else cid)
            is_in = mid in self._manager._favorites.get(cid, set())
            label = f"{'★ ' if is_in else '☆ '}{name}"
            a = fav_sub.addAction(label)
            a.setData(cid)
            def make_handler(coll_id, already_in):
                def handler():
                    if already_in:
                        self._manager._favorites[coll_id].discard(mid)
                    else:
                        self._manager._favorites[coll_id].add(mid)
                    self._manager.save_favorites()
                    self.addToFavoriteRequested.emit(mid, coll_id)
                return handler
            a.triggered.connect(make_handler(cid, is_in))
        menu.addMenu(fav_sub)
    def set_manager(self, mgr):
        """设置 MaterialManager 引用（用于收藏星标判断）"""
        self._manager = mgr

    def set_materials(self, materials):
        self._materials = list(materials)
        self._filtered_materials = list(materials)
        self._selected_materials.clear()
        self._selected_material = None
        self._last_clicked_id = None
        # 重置筛选上下文（因为数据源已完全替换）
        self._active_filters.clear()
        self._active_tags.clear()
        self._current_cat_id = None
        self._current_desc_ids = None
        self._current_search_kw = None
        self._apply_fav_flags()
        self._reapply_sort()
        self._refresh()

    def _copy_selected_to_clipboard(self, mid="", mat=None):
        """复制选中资产到系统剪贴板（支持资源管理器粘贴）+ 内部剪贴板"""
        ids = list(self._selected_materials.keys()) if self._selected_materials else ([mid] if mid else [])
        if ids:
            self.clipboardChanged.emit(ids)
        # 写入系统剪贴板（文件路径 → QUrl）
        mats = list(self._selected_materials.values()) if self._selected_materials else ([mat] if mat else [])
        file_urls = []
        for m in mats:
            fp = m.get("json_path", "")
            if fp and os.path.isfile(fp):
                file_urls.append(QtCore.QUrl.fromLocalFile(fp))
        if file_urls:
            mime = QtCore.QMimeData()
            mime.setUrls(file_urls)
            QtWidgets.QApplication.clipboard().setMimeData(mime)

    def _select_all_cards(self):
        """全选当前可见卡片"""
        self._selected_materials.clear()
        for m in self._filtered_materials:
            mid = m.get("id", "")
            if mid:
                self._selected_materials[mid] = m
        self._refresh()
        self._refresh_card_highlights()
        self.selectionChanged.emit()
        if self._selected_materials:
            first = next(iter(self._selected_materials.values()))
            self.materialSelected.emit(first)

    def _apply_fav_flags(self):
        """从 MaterialManager._favorites 同步收藏标记"""
        if not self._manager:
            return
        fav_ids = set()
        for mids in self._manager._favorites.values():
            fav_ids.update(mids)
        for m in self._materials:
            m["_favorited"] = m.get("id", "") in fav_ids
        for m in self._filtered_materials:
            m["_favorited"] = m.get("id", "") in fav_ids

    def _build_category_submenu(self, title, material_id, move=True, font_size=13):
        """创建级联分层分类菜单（移动/复制共用），支持全局跨子库"""
        if not self._manager:
            return None
        ids = list(self._selected_materials.keys()) if len(self._selected_materials) > 1 else [material_id]
        ids = [mid for mid in ids if mid]
        if not ids:
            return None

        sub = QtWidgets.QMenu(title, self)
        sub.setStyleSheet(_get_sub_style(font_size))
        tree = self._manager.get_category_tree()

        # 执行移动/复制的回调
        def make_handler(tid, mids, sub_lib=""):
            def handler():
                for mid in mids:
                    if move:
                        self._manager.move_material_to_category(mid, tid, sub_lib=sub_lib)
                    else:
                        self._manager.copy_material_to_category(mid, tid, sub_lib=sub_lib)
                self._manager.reload()
                w = self.window()
                if hasattr(w, '_category_tree'):
                    cur = w._category_tree.get_active_category()
                    if hasattr(w, '_refresh_category_tree'):
                        w._refresh_category_tree()
                    if cur and cur != "all":
                        w._category_tree._select_by_id(cur)
                        w._category_tree._active_category = cur
                    if hasattr(w, '_on_category_selected'):
                        desc_ids = w._category_tree.get_descendant_ids(cur)
                        root_lib = "materials"
                        if hasattr(w, '_detect_root_library'):
                            root_lib = w._detect_root_library(cur)
                        w._on_category_selected(cur, desc_ids, root_lib)
            return handler

        # 递归构建级联子菜单
        def build_cascade(nodes, parent_menu, parent_type=""):
            """递归构建级联菜单：每个可点击的叶子节点、也有子节点的父节点本身也可点击"""
            for n in nodes:
                if n["id"] == "all":
                    continue
                display = n.get("name_cn") or n.get("name") or n["id"]
                children = n.get("children", [])
                n_type = n.get("type", "") or parent_type
                if children:
                    # 有子分类 → 创建级联子菜单，并将当前节点作为菜单内第一个可点击动作
                    child_menu = QtWidgets.QMenu(display, parent_menu)
                    child_menu.setStyleSheet(parent_menu.styleSheet())
                    # 当前节点作为子菜单的第一个动作（显示"← 当前分类"）
                    self_a = child_menu.addAction(f"← {display}")
                    self_a.triggered.connect(make_handler(n["id"], ids, n_type))
                    child_menu.addSeparator()
                    build_cascade(children, child_menu, n_type)
                    parent_menu.addMenu(child_menu)
                else:
                    # 叶子节点 → 直接添加动作
                    a = parent_menu.addAction(display)
                    a.triggered.connect(make_handler(n["id"], ids, n_type))

        # 从顶级节点开始构建（不显示"全部"）
        for top in tree:
            if top["id"] == "all":
                continue
            top_display = top.get("name_cn") or top.get("name") or top["id"]
            top_children = top.get("children", [])
            # 顶级节点如果有子节点，创建子菜单；否则直接动作
            if top_children:
                top_menu = QtWidgets.QMenu(top_display, sub)
                top_menu.setStyleSheet(sub.styleSheet())
                # 顶级节点本身也作为第一个可点击动作
                self_a = top_menu.addAction(f"← {top_display}")
                self_a.triggered.connect(make_handler(top["id"], ids, top.get("type", "")))
                top_menu.addSeparator()
                build_cascade(top_children, top_menu, top.get("type", ""))
                sub.addMenu(top_menu)
            else:
                a = sub.addAction(top_display)
                a.triggered.connect(make_handler(top["id"], ids, top.get("type", "")))

        return sub if sub.actions() else None

    def filter_by_category(self, category_id, descendant_ids=None):
        """按分类筛选（与搜索/标签组合）"""
        self._current_cat_id = category_id
        self._current_desc_ids = descendant_ids
        if category_id == "all" or not category_id:
            self._active_filters.discard("category")
        else:
            self._active_filters.add("category")
        self._apply_all_filters(category_id=category_id, descendant_ids=descendant_ids)

    def filter_by_search(self, keyword):
        """按关键词搜索（与分类/标签组合）"""
        self._current_search_kw = keyword
        if not keyword:
            self._active_filters.discard("search")
        else:
            self._active_filters.add("search")
        self._apply_all_filters(keyword=keyword)

    def filter_by_tags(self, tags):
        self._active_tags = set(tags)
        self._apply_all_filters()

    def filter_by_favorites(self, favorites):
        fav_ids = set(favorites)
        if not fav_ids:
            self._filtered_materials = list(self._materials)
        else:
            self._filtered_materials = [m for m in self._materials if m.get("id") in fav_ids]
        self._reapply_sort()
        self._apply_fav_flags()
        self._refresh()
        self.selectionChanged.emit()

    def filter_by_recent(self):
        self._filtered_materials = list(self._materials)[:8]
        self._reapply_sort()
        self._refresh()
        self.selectionChanged.emit()

    def _apply_all_filters(self, category_id=None, keyword=None, descendant_ids=None):
        result = list(self._materials)

        # --- 分类筛选（若未传参则使用存储的上下文） ---
        effective_cat = category_id if category_id is not None else self._current_cat_id
        effective_desc = descendant_ids if descendant_ids is not None else self._current_desc_ids
        if "category" in self._active_filters and effective_cat and effective_cat != "all":
            if effective_desc:
                result = [m for m in result if m.get("category") in effective_desc]
            else:
                result = [m for m in result if m.get("category") == effective_cat]

        # --- 关键词搜索 ---
        if keyword:
            kw = keyword.lower()
            result = [
                m for m in result
                if kw in m.get("name_cn", "").lower()
                or kw in m.get("name", "").lower()
                or any(kw in t.lower() for t in m.get("tags", []))
            ]

        # --- 标签筛选 ---
        if self._active_tags:
            result = [
                m for m in result
                if any(tag in [t.lower() for t in m.get("tags", [])] for tag in self._active_tags)
            ]

        self._filtered_materials = result
        self._reapply_sort()
        self._apply_fav_flags()
        self._refresh()
        self.selectionChanged.emit()

    def set_column_count(self, count):
        self._columns = max(1, min(count, 10))
        if self._view_mode == self.VIEW_ICON:
            self._refresh_icon()

    def get_visible_count(self):
        return len(self._filtered_materials)

    def get_selected_material(self):
        return self._selected_material

    def get_selected_materials_list(self):
        """获取多选材质列表"""
        return list(self._selected_materials.values())

    def get_selection_count(self):
        """获取选中数量"""
        count = len(self._selected_materials)
        # 如果是列表视图模式，用表格的选中行
        if self._view_mode == self.VIEW_LIST and hasattr(self, '_table_view'):
            count = max(count, len(self._table_view.selectionModel().selectedRows()))
        return count

    def clear_selection(self):
        """清除所有选中状态"""
        self._selected_materials.clear()
        self._selected_material = None
        self._last_clicked_id = None
        self._refresh_card_highlights()
        # 列表视图也清除
        if self._view_mode == self.VIEW_LIST and hasattr(self, '_table_view'):
            self._table_view.clearSelection()
        self.selectionChanged.emit()
