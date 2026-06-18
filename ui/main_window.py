import os
import subprocess
import tempfile
import uuid

from ..utils.maya_utils import get_qt_modules, get_maya_window

QtWidgets, QtCore, QtGui, _, WindowType = get_qt_modules()

from .category_tree import CategoryTreeWidget, join_cat_id, split_cat_id
from .favorites_panel import FavoritesPanelWidget
from .thumbnail_view import ThumbnailGridWidget
from .preview_panel import PreviewPanelWidget
from .search_bar import SearchBarWidget, _pinyin_first_char
from .settings_dialog import SettingsDialog
from .export_preset_dialog import ExportPresetDialog
from .asset_create_dialog import AssetCreateDialog
from .thumbnail_capture_overlay import ThumbnailCaptureOverlay
from .name_conflict_dialog import NameConflictDialog

from ..core.manager import MaterialManager
from ..core.category import Category
from ..utils.mock_data import MOCK_MATERIALS
from .preview_panel import FlowLayout

try:
    from ..utils.settings import SettingsManager
except ImportError:
    SettingsManager = None

try:
    from ..utils.error_handler import handle_errors
except ImportError:
    def handle_errors(context="", show_dialog=True):
        def decorator(func): return func
        return decorator


SCRIPT_DIR = os.path.dirname(os.path.dirname(__file__))


def _load_material_color_mapping():
    """从 pbr_mapping.json 加载材质类型→主颜色属性名映射"""
    mapping = {}
    try:
        import json
        mpath = os.path.join(SCRIPT_DIR, 'Assets', 'preset', 'pbr_mapping.json')
        if os.path.isfile(mpath):
            with open(mpath, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            mat_maps = cfg.get('material_property_mappings', {})
            for ntype, props in mat_maps.items():
                if 'baseColor' in props:
                    bc = props['baseColor']
                    mapping[ntype] = bc.get('node_attribute', 'baseColor') if isinstance(bc, dict) else bc
    except Exception:
        pass
    mapping.setdefault('lambert', 'color')
    mapping.setdefault('blinn', 'color')
    mapping.setdefault('phong', 'color')
    mapping.setdefault('RedshiftMaterial', 'diffuse_color')
    return mapping


class MaterialLibraryWindow(QtWidgets.QMainWindow):
    VERSION = "1.0.0-beta"
    WINDOW_NAME = "MaterialLibraryWindow"
    VIEW_ICON = 0
    VIEW_LIST = 1

    def __init__(self, parent=None, library_path=None):
        if parent is None:
            parent = get_maya_window()
        super(MaterialLibraryWindow, self).__init__(parent)

        self.setWindowTitle(f"Maya资产管理工具 版本 {self.VERSION}")
        self.setMinimumSize(1100, 650)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(
            WindowType.Window
            | WindowType.WindowMinimizeButtonHint
            | WindowType.WindowMaximizeButtonHint
            | WindowType.WindowCloseButtonHint
        )

        self._current_view = self.VIEW_ICON
        self._current_fav_collection = "default"
        self._left_panel_visible = True
        self._use_mock = False
        self._current_root_lib = "materials"

        self._settings_mgr = SettingsManager() if SettingsManager else None
        self._app_settings = self._load_settings()
        self._material_manager = MaterialManager()
        # 注册到模块级变量，方便 Maya 控制台访问
        import sys as _sys
        _sam_mod = _sys.modules.get('squirrel_asset_manager')
        if _sam_mod:
            _sam_mod.manager = self._material_manager
        self._init_data_layer(library_path)
        self._restore_window_state()

        self._setup_ui()
        self._create_connections()
        self._apply_styles()
        self._load_data()
        # 设置搜索栏标签列表
        self._init_search_bar_tags()
        # 注：拖拽到视口由 dragDroppedOnViewport 信号处理，无需覆盖层

    # ── 数据层 ────────────────────────────────────────

    def _init_data_layer(self, library_path=None):
        # 加载多库配置
        self._libraries = self._app_settings.get("library_paths", [])
        if not self._libraries:
            # 兼容旧格式：last_library_path
            old_path = self._app_settings.get("last_library_path", "")
            if old_path:
                self._libraries = [{"name": "默认库", "path": old_path}]
            else:
                default_path = os.path.join(os.path.expanduser("~"), "SquirrelLib")
                os.makedirs(default_path, exist_ok=True)
                self._libraries = [{"name": "默认库", "path": default_path}]
                self._app_settings["library_paths"] = self._libraries
                if self._settings_mgr:
                    self._settings_mgr.set("library_paths", self._libraries)

        path = library_path or self._libraries[0]["path"]
        if path and os.path.isdir(path):
            try:
                ok = self._material_manager.load_library(path)
                if ok:
                    self._use_mock = False
                    print(f"[MaterialLibrary] 已加载材质库: {path} "
                          f"({self._material_manager.get_material_count()} 个材质)")
                    return
            except Exception as e:
                print(f"[MaterialLibrary] 加载材质库失败: {e}")

        # 回退：自动创建默认
        default_path = os.path.join(os.path.expanduser("~"), "SquirrelLib")
        os.makedirs(default_path, exist_ok=True)
        ok = self._material_manager.load_library(default_path)
        if ok:
            self._use_mock = False
            print(f"[MaterialLibrary] 已回退到默认材质库: {default_path}")
            return

        self._use_mock = True

    def _load_data(self):
        self._populate_library_combo()
        if self._use_mock:
            self._thumbnail_grid.set_materials(list(MOCK_MATERIALS))
        else:
            # load_library 已扫描磁盘构建了分类树缓存，使用缓存避免重复扫描
            self._refresh_category_tree(force=False)
            saved_cat = self._app_settings.get("active_category", "")
            if saved_cat and saved_cat != "all":
                # 有保存的上次分类状态 → 直接加载目标分类数据
                # 避免先显示 MOCK 色块再切换导致 UI 闪烁
                saved_root_lib = self._app_settings.get("active_root_lib", "")
                root_lib = saved_root_lib if saved_root_lib else self._detect_root_library(saved_cat)
                if root_lib:
                    self._current_root_lib = root_lib
                self._category_tree._select_by_id(saved_cat, root_lib if root_lib else None)
                desc_ids = self._category_tree.get_descendant_ids(saved_cat, root_lib if root_lib else None)
                self._on_category_selected(saved_cat, desc_ids, root_lib or "materials")
            else:
                self._category_tree.select_first_sub_library()
        # 启动后延迟检测重复 UUID
        QtCore.QTimer.singleShot(500, self._check_duplicate_uuids)

    def _load_settings(self):
        defaults = {
            "font_size": 13, "thumb_size": 180,
            "default_view": "icon",
            "last_library_path": "", "last_export_path": "",
            "window_state": {"width": 1400, "height": 900},
            "active_category": "",
            "active_root_lib": "",
            "expanded_ids": [],
            "project_category": "",
            "proj_root_lib": "",
            "left_tab_index": 0,
            "view_mode": "icon",
            "sort_order": 0,
            "panel_visible": True,
            "splitter_sizes": [],
        }
        return self._settings_mgr.load() if self._settings_mgr else dict(defaults)

    def _restore_window_state(self):
        ws = self._app_settings.get("window_state", {})
        self.resize(ws.get("width", 1400), ws.get("height", 900))

    def _refresh_material_grid(self, category_id="all", mgr=None, tree=None):
        if mgr is None:
            mgr = self._material_manager
        if tree is None:
            tree = self._category_tree
        if category_id == "all":
            materials = mgr.get_materials(sub_library=self._current_root_lib)
        else:
            desc_ids = tree.get_descendant_ids(category_id)
            if len(desc_ids) > 1:
                materials = []
                seen = set()
                for did in desc_ids:
                    for m in mgr.get_materials(did, sub_library=self._current_root_lib):
                        if m.id not in seen:
                            seen.add(m.id)
                            materials.append(m)
            else:
                materials = mgr.get_materials(category_id, sub_library=self._current_root_lib)
        dicts = [m.to_dict() for m in materials]
        # 推导资产类型（当前分类所属根子库）
        asset_type = self._material_manager.ASSET_SUB_LIBRARIES.get(
            self._current_root_lib, self._current_root_lib)
        for d in dicts:
            d["_category_display"] = mgr.get_category_display_name(d.get("category", ""))
            d["_asset_type"] = asset_type
        self._thumbnail_grid.set_materials(dicts)

    def _load_full_sub_lib(self):
        """加载当前子库所有材质到网格（用于组合筛选）"""
        all_mats = self._material_manager.get_materials(sub_library=self._current_root_lib)
        dicts = [m.to_dict() for m in all_mats]
        asset_type = self._material_manager.ASSET_SUB_LIBRARIES.get(
            self._current_root_lib, self._current_root_lib)
        for d in dicts:
            d["_category_display"] = self._material_manager.get_category_display_name(
                d.get("category", ""))
            d["_asset_type"] = asset_type
        self._thumbnail_grid.set_materials(dicts)

    # ── Dict 模式联合搜索 ──────────────────────────

    def _get_active_category_tree(self):
        """返回当前活动的分类树（主树或项目树）"""
        return self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree

    def _dict_mode_search_and_set(self, keyword=None, category=None, tags=None):
        """Dict 模式联合搜索 — 构建 query → mgr.search() → set_materials()

        从当前 UI 状态自动读取未传入的参数（关键词、分类、标签），
        调用 mgr.search(dict) 执行服务端组合过滤，将结果填入网格。

        Args:
            keyword: 搜索关键词，None 沿用当前搜索栏，'' 表示不传 keyword
            category: 分类 ID，None 沿用当前选中分类，"all" 表示全部分类
            tags: 标签列表，None 沿用当前活跃标签，[] 表示不传 tags
        """
        mgr = self._active_mgr
        root_lib = self._current_root_lib

        # ── 1. 构建 query ──
        query = {"sub_library": root_lib}

        # 关键词
        if keyword is None:
            kw = self._search_bar.text()
        else:
            kw = keyword
        if kw:
            query["keyword"] = kw

        # 分类 — 从正确的分类树读取
        cat_tree = self._proj_category_tree if mgr is self._project_mgr else self._category_tree
        if category is None:
            active_cat = cat_tree.get_active_category()
            # 复合 ID（含 "||"）→ 提取 short_id 用于 manager 查询
            # 逗号分隔的多选格式（如 "id1,id2" → "||" 不会出现在多个 ID 拼接中）
            if active_cat and active_cat != "all":
                cat_id = active_cat
            else:
                cat_id = None
        elif category and category != "all":
            cat_id = category
        else:
            cat_id = None

        # 检测多选（逗号分隔的分类 ID 列表）
        is_multi = cat_id and "," in str(cat_id)

        if is_multi:
            # 多选模式：搜索时不传 category（sub_library 已隔离），后过滤
            multi_cat_ids = str(cat_id).split(",")
            # 收集所有后代 ID
            all_desc_ids = set()
            root_lib_from_tree = root_lib
            for cid in multi_cat_ids:
                # cid 可能是复合 ID，get_descendant_ids 已支持
                all_desc_ids.add(cid)
                desc = cat_tree.get_descendant_ids(cid, root_lib_from_tree)
                all_desc_ids.update(desc)
            cat_id_for_filter = None  # 不传给 search()
        elif cat_id and cat_id != root_lib:
            # 如果选中的分类就是子库根节点，跳过 category 筛选
            # 因为 sub_library 筛选已覆盖整个子库，再加 category 会导致 search() 中
            # self._categories.get(root_lib) 返回 None 而走 fallback 全量筛除
            # cat_id 可能是复合 ID，提取 short_id 传给 manager
            _, cat_id_for_filter = split_cat_id(cat_id)
            if not cat_id_for_filter:
                cat_id_for_filter = cat_id
        else:
            cat_id_for_filter = None

        if cat_id_for_filter:
            query["category"] = cat_id_for_filter

        # 标签
        if tags is None:
            restored_tags = set(self._thumbnail_grid._active_tags)
            q_tags = list(restored_tags) if restored_tags else []
        else:
            restored_tags = set(tags)
            q_tags = tags if tags else []
        if q_tags:
            query["tags"] = q_tags

        # ── 2. 执行搜索 ──
        results = mgr.search(query)
        dicts = [m.to_dict() for m in results]
        asset_type = mgr.ASSET_SUB_LIBRARIES.get(root_lib, root_lib)
        for d in dicts:
            d["_category_display"] = mgr.get_category_display_name(
                d.get("category", ""))
            d["_asset_type"] = asset_type

        # ── 3. 设置网格数据（set_materials 会清空筛选状态） ──
        self._thumbnail_grid.set_materials(dicts)

        # ── 4. 恢复筛选状态（仅用于 UI 展示，实际已由 mgr 过滤好） ──
        if q_tags:
            self._thumbnail_grid._active_tags = restored_tags
            self._thumbnail_grid._active_filters.add("tags")
        if kw:
            self._thumbnail_grid._current_search_kw = kw
            self._thumbnail_grid._active_filters.add("search")
        if cat_id:
            self._thumbnail_grid._current_cat_id = cat_id
            self._thumbnail_grid._active_filters.add("category")

    def _refresh_keep_current(self, reload_materials=False):
        """重载+刷新，保持当前激活库的选中状态（仅触发一次 _on_category_selected）

        Args:
            reload_materials: True 时从磁盘重新扫描所有资产（例如 UUID 修复后需要）
        """
        if self._active_mgr is self._project_mgr:
            self._on_refresh_project()
            return
        cur = self._category_tree.get_active_category()
        sub_lib = self._category_tree.get_active_root_lib()
        if reload_materials:
            self._material_manager.reload()
        # 阻塞树内部 QTreeWidget 信号避免 _refresh_category_tree 触发两次选中回调
        self._category_tree._tree.blockSignals(True)
        self._refresh_category_tree()
        self._category_tree._tree.blockSignals(False)
        if cur != "all":
            self._category_tree._select_by_id(cur, sub_lib)
            self._category_tree._active_category = cur
            desc_ids = self._category_tree.get_descendant_ids(cur, sub_lib)
        else:
            desc_ids = []
        self._on_category_selected(cur, desc_ids, sub_lib)
        if reload_materials:
            QtCore.QTimer.singleShot(300, self._check_duplicate_uuids)

    def _refresh_category_tree(self, force=True):
        if not self._use_mock:
            if force:
                self._active_mgr._cached_tree = None
            tree = self._active_mgr.get_category_tree()
            # 分类树变更时清除缓存
            self._cached_cat_tree = None
            if hasattr(self, '_cached_ui_data'):
                self._cached_ui_data.clear()
            if self._active_mgr is self._project_mgr:
                self._proj_category_tree.refresh_tree(tree)
            else:
                self._category_tree.refresh_tree(tree)

    # ── UI 布局 ───────────────────────────────────────

    def _setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        self.setStyleSheet("background-color: #2a2a2a;")

        root_layout = QtWidgets.QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._create_toolbar())

        # 主分割：左侧(分类树) | 中间(缩略图) | 右侧(预览)
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_splitter.setObjectName("mainSplitter")
        main_splitter.setHandleWidth(6)
        main_splitter.setChildrenCollapsible(True)
        main_splitter.setStyleSheet("QSplitter::handle { background-color: #3a3a3a; }")

        self._left_panel = self._create_left_panel()
        self._left_panel.setMinimumWidth(240)
        main_splitter.addWidget(self._left_panel)

        # 中间：缩略图 / 文件浏览器（系统目录时切换）
        self._center_stack = QtWidgets.QStackedWidget()
        self._center_stack.setMinimumWidth(0)  # 隐式 minimumSizeHint 覆盖为 0

        center_widget = QtWidgets.QWidget()
        center_widget.setMinimumWidth(0)  # 递归覆盖子控件隐式最小宽度
        center_layout = QtWidgets.QVBoxLayout(center_widget)
        center_layout.setContentsMargins(6, 0, 6, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self._create_view_control())
        self._thumbnail_grid = ThumbnailGridWidget()
        if not self._use_mock:
            self._thumbnail_grid.set_manager(self._material_manager)
        center_layout.addWidget(self._thumbnail_grid, 1)
        self._center_stack.addWidget(center_widget)

        main_splitter.addWidget(self._center_stack)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 4)

        # 右侧：材质预览面板
        self._right_panel = PreviewPanelWidget()
        main_splitter.addWidget(self._right_panel)

        # 拖拽分割手柄时强制预览保持方形
        main_splitter.splitterMoved.connect(self._on_main_splitter_moved)

        root_layout.addWidget(main_splitter, 1)
        root_layout.addWidget(self._create_status_bar())

    def _create_toolbar(self):
        font_size = self._app_settings.get("font_size", 13)
        btn_padding = max(7, int(font_size * 0.5))
        btn_height = max(font_size + btn_padding * 2, int(font_size * 2.5))
        toolbar_height = max(44, btn_height + 12)  # 12 = layout margins (6+6)
        
        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("toolbar")
        toolbar.setStyleSheet("#toolbar { background-color: #252525; border-bottom: 1px solid #3a3a3a; }")
        toolbar.setFixedHeight(toolbar_height)
        layout = QtWidgets.QHBoxLayout(toolbar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        self._panel_toggle_btn = QtWidgets.QPushButton("\u2261")
        self._panel_toggle_btn.setFixedSize(btn_height, btn_height)
        self._panel_toggle_btn.setToolTip("显示/隐藏分类面板")
        self._panel_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #909090; border: none;"
            f"font-size: {font_size + 5}px; font-weight: bold; }}"
            "QPushButton:hover { color: #e0e0e0; }"
        )
        layout.addWidget(self._panel_toggle_btn)

        self._search_bar = SearchBarWidget(font_size=font_size)
        # 批量操作栏（放在搜索框右侧，搜索框自动缩小）
        from .batch_action_bar import BatchActionBar
        self._batch_action_bar = BatchActionBar(self)
        self._batch_action_bar.setVisible(False)
        # 搜索框 + 批量栏共享中央区域
        self._toolbar_center = QtWidgets.QWidget()
        self._toolbar_center.setStyleSheet("background: transparent;")
        center_layout = QtWidgets.QHBoxLayout(self._toolbar_center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(6)
        center_layout.addWidget(self._search_bar, 1)
        center_layout.addWidget(self._batch_action_bar, 0)
        layout.addWidget(self._toolbar_center, 1)

        btn_style = f"""
            QPushButton {{ background-color: #3a3a3a; color: #d0d0d0; border: none;
                padding: {btn_padding}px 14px; font-size: {font_size}px; border-radius: 4px; }}
            QPushButton:hover {{ background-color: #4a4a4a; }}
            QPushButton:pressed {{ background-color: #2a2a2a; }}
            QPushButton:checked {{ background-color: #5294e2; color: #ffffff; }}
        """

        refresh_btn = QtWidgets.QPushButton("↻ 刷新")
        refresh_btn.setToolTip("刷新材质库")
        refresh_btn.setStyleSheet(btn_style)
        refresh_btn.clicked.connect(self._on_refresh)
        layout.addWidget(refresh_btn)

        ai_btn_style = f"""
            QPushButton {{ background-color: #2d3a5a; color: #d0e0ff; border: none;
                padding: {btn_padding}px 14px; font-size: {font_size}px; border-radius: 4px; }}
            QPushButton:hover {{ background-color: #3d4a6a; }}
            QPushButton:pressed {{ background-color: #1d2a4a; }}
        """
        self._ai_tools_btn = QtWidgets.QPushButton("🤖 AI 工具")
        self._ai_tools_btn.setStyleSheet(ai_btn_style)
        self._ai_tools_menu = QtWidgets.QMenu(self)
        self._ai_tools_menu.setStyleSheet(f"""
            QMenu {{ background-color:#2a2a2a; color:#d0d0d0; border:1px solid #3a3a3a; padding:4px; }}
            QMenu::item {{ padding:6px 24px 6px 14px; font-size:{font_size}px; }}
            QMenu::item:selected {{ background-color:#2d4a6f; color:#5294e2; }}
        """)
        self._ai_tools_btn.setMenu(self._ai_tools_menu)
        self._ai_tools_menu.aboutToShow.connect(self._refresh_ai_tools_menu)
        self._refresh_ai_tools_menu()
        layout.addWidget(self._ai_tools_btn)

        self._quick_tools_btn = QtWidgets.QPushButton("快捷工具")
        self._quick_tools_btn.setStyleSheet(btn_style)
        self._quick_tools_menu = QtWidgets.QMenu(self)
        self._quick_tools_menu.setStyleSheet(f"""
            QMenu {{ background-color:#2a2a2a; color:#d0d0d0; border:1px solid #3a3a3a; padding:4px; }}
            QMenu::item {{ padding:6px 24px 6px 14px; font-size:{font_size}px; }}
            QMenu::item:selected {{ background-color:#2d4a6f; color:#5294e2; }}
        """)
        self._quick_tools_btn.setMenu(self._quick_tools_menu)
        self._quick_tools_menu.aboutToShow.connect(self._refresh_quick_tools_menu)
        self._refresh_quick_tools_menu()
        layout.addWidget(self._quick_tools_btn)

        settings_btn = QtWidgets.QPushButton("设置")
        settings_btn.setStyleSheet(btn_style)
        settings_btn.clicked.connect(self._on_settings)
        layout.addWidget(settings_btn)

        help_btn = QtWidgets.QPushButton("?")
        help_btn.setObjectName("help_btn")
        help_btn.setFixedSize(btn_height, btn_height)
        help_btn.setToolTip("使用帮助")
        help_btn.setStyleSheet(
            f"QPushButton {{ background-color: #3a3a3a; color: #ffa502; border: none;"
            f"font-size: {font_size + 5}px; font-weight: bold; border-radius: 4px; }}"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        help_btn.clicked.connect(self._on_help)
        layout.addWidget(help_btn)

        return toolbar

    def _refresh_ai_tools_menu(self):
        self._ai_tools_menu.clear()

        if not hasattr(self, '_thumbnail_grid'):
            no_action = self._ai_tools_menu.addAction("初始化中...")
            no_action.setEnabled(False)
            return

        selected = self._thumbnail_grid.get_selected_materials_list()
        selected_count = len(selected)

        if selected_count > 0:
            action = self._ai_tools_menu.addAction(
                f"🤖 AI 分析缩略图 ({selected_count} 个选中资产)")
        else:
            action = self._ai_tools_menu.addAction(
                "🤖 AI 分析缩略图 (右键资产→AI 分析)")
        action.triggered.connect(self._on_ai_analysis_with_config)

        self._ai_tools_menu.addSeparator()

        no_action = self._ai_tools_menu.addAction(
            "更多 AI 工具请添加到此菜单...")
        no_action.setEnabled(False)

    def _refresh_quick_tools_menu(self):
        """刷新快捷工具菜单，从 quicktools 文件夹加载脚本"""
        self._quick_tools_menu.clear()
        
        quicktools_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "quicktools")
        
        if not os.path.exists(quicktools_dir):
            os.makedirs(quicktools_dir, exist_ok=True)
            action = self._quick_tools_menu.addAction("暂无快捷工具")
            action.setEnabled(False)
            return
        
        scripts = []
        for filename in os.listdir(quicktools_dir):
            if filename.endswith(".py"):
                scripts.append(filename)
        
        if not scripts:
            action = self._quick_tools_menu.addAction("暂无快捷工具")
            action.setEnabled(False)
            return
        
        for script in sorted(scripts):
            name = os.path.splitext(script)[0]
            action = self._quick_tools_menu.addAction(name.replace("_", " "))
            action.triggered.connect(lambda checked=False, s=script: self._run_quick_tool(s))

    def _run_quick_tool(self, script_name):
        """执行指定的快捷工具脚本"""
        quicktools_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "quicktools")
        script_path = os.path.join(quicktools_dir, script_name)
        
        print(f"[QuickTool] 尝试执行脚本: {script_name}")
        print(f"[QuickTool] 脚本路径: {script_path}")
        
        if not os.path.exists(script_path):
            print(f"[QuickTool] 错误: 脚本不存在: {script_path}")
            return
        
        try:
            import importlib.util
            import sys
            
            if script_path not in sys.path:
                sys.path.insert(0, os.path.dirname(script_path))
            
            module_name = os.path.splitext(script_name)[0]
            
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if spec is None:
                print(f"[QuickTool] 错误: 无法创建模块规范")
                return
            
            module = importlib.util.module_from_spec(spec)
            
            spec.loader.exec_module(module)
            
            if hasattr(module, 'main'):
                print(f"[QuickTool] 调用 main() 函数")
                # 传递主窗口引用给 quicktool（用于"导入当前分类"功能实时查询）
                try:
                    import squirrel_asset_manager as _sam
                    _sam.main_window = self
                    print("[QuickTool] 已传递主窗口引用")
                except Exception:
                    import traceback
                    traceback.print_exc()
                module.main()
                print(f"[QuickTool] 脚本执行完成: {script_name}")
            else:
                print(f"[QuickTool] 警告: 脚本 {script_name} 没有 main() 函数")
        except Exception as e:
            import traceback
            print(f"[QuickTool] 执行脚本失败 {script_name}: {str(e)}")
            print(f"[QuickTool] 详细错误: {traceback.format_exc()}")

    def _create_left_panel(self):
        self._left_panel_container = QtWidgets.QTabWidget()
        self._left_panel_container.setMinimumWidth(240)
        self._left_panel_container.setStyleSheet("""
            QTabWidget::pane { border: none; background-color: #252525; }
            QTabBar::tab {
                background-color: #252525; color: #909090; border: none;
                border-bottom: 2px solid transparent;
                padding: 8px 9px; font-size: 12px; min-width: 47px;
                cursor: default;
            }
            QTabBar::tab:selected {
                background-color: #2a2a2a; color: #5294e2;
                border-bottom: 2px solid #5294e2;
            }
            QTabBar::tab:hover:!selected { background-color: #333333; color: #d0d0d0; }
        """)

        # Tab 0: 材质分类
        cat_widget = QtWidgets.QWidget()
        cat_layout = QtWidgets.QVBoxLayout(cat_widget)
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(0)

        # ── 库切换下拉框 ──
        self._lib_combo = QtWidgets.QComboBox()
        self._lib_combo.setStyleSheet(
            "QComboBox { background:#2a2a2a; color:#d0d0d0; border:none; "
            "padding:4px 8px; font-size:12px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#2a2a2a; color:#d0d0d0; "
            "selection-background-color:#2a4a6a; border:1px solid #3a3a3a; }")
        self._lib_combo.currentIndexChanged.connect(self._on_library_switched)
        cat_layout.addWidget(self._lib_combo)

        self._category_tree = CategoryTreeWidget()
        cat_layout.addWidget(self._category_tree)
        self._left_panel_container.addTab(cat_widget, "分类")

        # Tab 1: 项目
        project_widget = QtWidgets.QWidget()
        project_layout = QtWidgets.QVBoxLayout(project_widget)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setSpacing(0)

        self._project_mgr = MaterialManager()
        # 注册到模块级变量
        import sys as _sys2
        _sam_mod2 = _sys2.modules.get('squirrel_asset_manager')
        if _sam_mod2:
            _sam_mod2.project_manager = self._project_mgr
        self._project_loaded = False
        self._active_mgr = self._material_manager  # 当前激活的管理器

        # 工具栏：创建 + 刷新
        proj_toolbar = QtWidgets.QWidget()
        proj_toolbar.setStyleSheet("background-color: #222222;")
        proj_toolbar_layout = QtWidgets.QHBoxLayout(proj_toolbar)
        proj_toolbar_layout.setContentsMargins(8, 6, 8, 6)

        create_proj_btn = QtWidgets.QPushButton("+ 创建")
        create_proj_btn.setStyleSheet(
            "QPushButton { background-color: #5294e2; color: #fff; border: none;"
            "padding: 4px 10px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #6ab0ff; }"
        )
        create_proj_btn.clicked.connect(self._on_create_project_lib)
        proj_toolbar_layout.addWidget(create_proj_btn)

        refresh_proj_btn = QtWidgets.QPushButton("↻ 刷新")
        refresh_proj_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #d0d0d0; border: none;"
            "padding: 4px 10px; font-size: 11px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        refresh_proj_btn.clicked.connect(self._on_refresh_project)
        proj_toolbar_layout.addWidget(refresh_proj_btn)
        proj_toolbar_layout.addStretch()

        self._proj_status = QtWidgets.QLabel("")
        self._proj_status.setStyleSheet("color: #808080; font-size: 11px;")
        proj_toolbar_layout.addWidget(self._proj_status)
        project_layout.addWidget(proj_toolbar)

        # 分类树（与主分类列表功能和交互完全一致）
        self._proj_category_tree = CategoryTreeWidget()
        self._proj_category_tree.categorySelected.connect(self._on_proj_category_selected)
        self._proj_category_tree.categoriesMultiSelected.connect(self._on_proj_categories_multi_selected)
        self._proj_category_tree.categoryAdded.connect(self._on_proj_category_added)
        self._proj_category_tree.categoryEdited.connect(self._on_proj_category_edited)
        self._proj_category_tree.categoryDeleted.connect(self._on_proj_category_deleted)
        self._proj_category_tree.openFolderRequested.connect(self._on_proj_open_folder)
        self._proj_category_tree.topLevelCategoryAdded.connect(self._on_proj_add_top_level_category)
        self._proj_category_tree.materialDropOnCategory.connect(lambda m, c, r: (
            setattr(self, '_active_mgr', self._project_mgr), self._on_material_dropped_on_category(m, c, r))[-1])
        project_layout.addWidget(self._proj_category_tree, 1)

        self._left_panel_container.addTab(project_widget, "项目")

        # Tab 2: 收藏
        self._favorites_panel = FavoritesPanelWidget()
        self._left_panel_container.addTab(self._favorites_panel, "收藏")

        # 切换到"工程"选项卡时自动加载
        self._left_panel_container.currentChanged.connect(self._on_left_tab_changed)

        return self._left_panel_container

    def _create_view_control(self):
        control = QtWidgets.QWidget()
        control.setStyleSheet("background-color: transparent;")
        control.setFixedHeight(36)
        layout = QtWidgets.QHBoxLayout(control)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # ── 左侧：操作按钮 ──
        action_btn_style = """
            QPushButton { background-color: #3a3a3a; color: #d0d0d0; border: none;
                padding: 5px 12px; font-size: 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #4a4a4a; }
        """
        # 从外部导入（文件夹/文件双模式）
        import_menu = QtWidgets.QMenu(self)
        font_size = self._app_settings.get("font_size", 13)
        import_menu.setStyleSheet(f"""
            QMenu {{ background-color:#2a2a2a; color:#d0d0d0; border:1px solid #3a3a3a; padding:4px; }}
            QMenu::item {{ padding:6px 24px 6px 14px; font-size:{font_size}px; }}
            QMenu::item:selected {{ background-color:#2a3a5a; color:#5294e2; }}
        """)
        import_menu.addAction("📄 从文件导入").triggered.connect(self._on_import_files)
        import_menu.addAction("📦 导入 .zasset 资产").triggered.connect(self._on_import_zasset_folder)
        import_menu.addSeparator()
        import_menu.addAction("🖼️ 导入贴图").triggered.connect(self._on_import_textures)
        import_menu.addAction("☀️ 导入HDR").triggered.connect(self._on_import_hdr)

        import_btn = QtWidgets.QPushButton("📥 从外部导入")
        import_btn.setStyleSheet(action_btn_style)
        import_btn.setToolTip("导入外部资产（支持文件夹或单个文件）")
        import_btn.setMenu(import_menu)
        layout.addWidget(import_btn)

        create_asset_btn = QtWidgets.QPushButton("🎯 导出资产")
        create_asset_btn.setStyleSheet(
            "QPushButton { background-color: #2d6a4f; color: #ffffff; border: none; "
            "padding: 5px 12px; font-size: 12px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #40916c; }"
        )
        create_asset_btn.setToolTip("从 Maya 选中物体创建材质资产到当前分类")
        create_asset_btn.clicked.connect(self._on_create_asset)
        layout.addWidget(create_asset_btn)

        layout.addStretch()

        # ── 右侧：结果 + 视图切换 + 缩略图滑块 ──
        self._result_label = QtWidgets.QLabel()
        self._result_label.setStyleSheet("color: #909090; font-size: 12px;")
        layout.addWidget(self._result_label)

        view_btn_style = """
            QPushButton { background-color: #3a3a3a; color: #909090; border: none;
                padding: 4px 10px; font-size: 11px; border-radius: 3px; }
            QPushButton:hover { color: #d0d0d0; }
            QPushButton:checked { background-color: #5294e2; color: #ffffff; }
        """

        self._tag_btn = QtWidgets.QPushButton("编辑标签")
        self._tag_btn.setCheckable(True)
        self._tag_btn.setStyleSheet(view_btn_style)
        self._tag_btn.setToolTip("选择标签筛选")
        self._tag_btn.clicked.connect(self._on_tag_popup)
        layout.addWidget(self._tag_btn)

        self._icon_view_btn = QtWidgets.QPushButton("图标")
        self._icon_view_btn.setCheckable(True)
        self._icon_view_btn.setChecked(True)
        self._icon_view_btn.setStyleSheet(view_btn_style)
        self._icon_view_btn.clicked.connect(lambda: self._switch_view(self.VIEW_ICON))
        layout.addWidget(self._icon_view_btn)

        self._list_view_btn = QtWidgets.QPushButton("列表")
        self._list_view_btn.setCheckable(True)
        self._list_view_btn.setStyleSheet(view_btn_style)
        self._list_view_btn.clicked.connect(lambda: self._switch_view(self.VIEW_LIST))
        layout.addWidget(self._list_view_btn)

        self._sort_combo = QtWidgets.QComboBox()
        self._sort_combo.addItems(["名称↑", "名称↓", "类型", "分类", "时间↑", "时间↓"])
        self._sort_combo.setStyleSheet("""
            QComboBox { background-color: #3a3a3a; border: 1px solid #4a4a4a; border-radius: 3px;
                padding: 2px 6px; color: #909090; font-size: 11px; min-width: 80px; }
            QComboBox:hover { border-color: #5294e2; color: #d0d0d0; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView { background-color: #333333; color: #d0d0d0;
                selection-background-color: #4a4a4a; font-size: 11px; }
        """)
        layout.addWidget(self._sort_combo)

        thumb_label = QtWidgets.QLabel("缩略图:")
        thumb_label.setStyleSheet("color: #909090; font-size: 12px;")
        layout.addWidget(thumb_label)

        self._thumb_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._thumb_slider.setRange(2, 8)
        self._thumb_slider.setValue(2)
        self._thumb_slider.setFixedWidth(120)
        self._thumb_slider.setStyleSheet(
            "QSlider::groove:horizontal { background: #3a3a3a; height: 4px; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #d0d0d0; width: 12px; height: 12px;"
            "margin: -4px 0; border-radius: 6px; }"
            "QSlider::handle:horizontal:hover { background: #5294e2; }"
        )
        layout.addWidget(self._thumb_slider)

        return control

    def _create_status_bar(self):
        status = QtWidgets.QFrame()
        status.setStyleSheet("background-color: #252525; border-top: 1px solid #3a3a3a;")
        status.setFixedHeight(26)
        layout = QtWidgets.QHBoxLayout(status)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(20)

        self._status_count = QtWidgets.QLabel()
        self._status_count.setStyleSheet("color: #909090; font-size: 12px;")
        layout.addWidget(self._status_count)

        self._status_info = QtWidgets.QLabel()
        self._status_info.setStyleSheet("color: #909090; font-size: 12px;")
        layout.addWidget(self._status_info, 1)
        layout.addStretch()
        return status

    # ── 主分割器拖拽回调 ──────────────────────────────

    def _on_main_splitter_moved(self, pos, index):
        """splitterMoved 回调：拖拽手柄时强制右侧预览保持方形"""
        self._right_panel.ensure_preview_square()

    # ── 信号连接 ─────────────────────────────────────

    def _create_connections(self):
        self._category_tree.categorySelected.connect(
            lambda c, d, r: (setattr(self, '_active_mgr', self._material_manager),
                              self._on_category_selected(c, d, r))[-1])
        self._category_tree.categoriesMultiSelected.connect(
            lambda ci, ai, r: (setattr(self, '_active_mgr', self._material_manager),
                               self._on_categories_multi_selected(ci, ai, r))[-1])
        self._category_tree.categoryAdded.connect(lambda cd: (
            setattr(self, '_active_mgr', self._material_manager), self._on_category_added(cd))[-1])
        self._category_tree.topLevelCategoryAdded.connect(lambda c, n, r: (
            setattr(self, '_active_mgr', self._material_manager), self._on_add_top_level_category(c, n, r))[-1])
        self._category_tree.categoryEdited.connect(lambda c, n, r="materials": (
            setattr(self, '_active_mgr', self._material_manager), self._on_category_edited(c, n, r))[-1])
        self._category_tree.categoryDeleted.connect(lambda c, r="materials", p="": (
            setattr(self, '_active_mgr', self._material_manager), self._on_category_deleted(c, r, p))[-1])
        self._category_tree.openFolderRequested.connect(lambda c: (
            setattr(self, '_active_mgr', self._material_manager), self._on_open_category_folder(c))[-1])
        self._category_tree.materialDropOnCategory.connect(lambda m, c, r: (
            setattr(self, '_active_mgr', self._material_manager), self._on_material_dropped_on_category(m, c, r))[-1])

        self._thumbnail_grid.openFolderRequested.connect(self._on_open_material_folder)
        self._thumbnail_grid.moveRequested.connect(self._on_move_material)

        self._favorites_panel.collectionSelected.connect(self._on_fav_collection_selected)
        self._favorites_panel.collectionAdded.connect(self._on_fav_collection_added)
        self._favorites_panel.collectionRenamed.connect(self._on_fav_collection_added)
        self._favorites_panel.materialDropOnCollection.connect(self._on_fav_drop_material)
        self._favorites_panel.collectionDeleted.connect(self._on_fav_collection_deleted)

        self._thumbnail_grid.materialSelected.connect(self._on_material_selected)
        self._thumbnail_grid.materialApplied.connect(self._on_material_applied)
        self._thumbnail_grid.favoriteToggled.connect(self._on_favorite_toggled)
        self._thumbnail_grid.addToFavoriteRequested.connect(self._on_add_to_favorite)
        self._thumbnail_grid.editMaterialRequested.connect(self._show_edit_dialog)
        self._thumbnail_grid.copyToProjectRequested.connect(self._on_copy_to_project)
        self._thumbnail_grid.pasteRequested.connect(self._on_paste)
        self._thumbnail_grid.clipboardChanged.connect(self._on_clipboard_changed)
        self._thumbnail_grid.importRequested.connect(self._on_grid_import)
        self._thumbnail_grid.assetImportRequested.connect(self._on_asset_import_into_maya)
        self._thumbnail_grid.variantGeometryImportRequested.connect(self._on_variant_geometry_import)
        self._thumbnail_grid.variantMaterialImportRequested.connect(self._on_variant_material_import)
        self._thumbnail_grid.variantVersionDeleteRequested.connect(self._on_variant_version_delete)
        self._thumbnail_grid.variantLodDeleteRequested.connect(self._on_variant_lod_delete)
        self._thumbnail_grid.createMaterialRequested.connect(self._on_create_material)
        self._thumbnail_grid.createDomeLightRequested.connect(self._on_create_dome_light)
        self._thumbnail_grid.assignHdrToDomeRequested.connect(self._on_assign_hdr_to_dome)
        self._thumbnail_grid.importSingleTextureRequested.connect(self._on_import_single_texture)
        self._thumbnail_grid.importTexturesSharedUVRequested.connect(self._on_import_textures_shared_uv)
        self._thumbnail_grid.assignTextureToMaterialRequested.connect(self._on_assign_texture_to_material)
        self._thumbnail_grid.aiAnalysisRequested.connect(self._on_ai_analysis)

        self._clipboard = []  # 复制的材质 ID 列表
        self._asset_overlay = None  # 资产创建截图窗口（跨批次复用）
        self._capture_overlay = None  # 预览面板截图窗口（单例）
        self._right_panel.editRequested.connect(self._on_edit_material)
        self._right_panel.favoriteToggled.connect(self._on_favorite_toggled)
        self._thumbnail_grid.exportPresetRequested.connect(self._on_grid_export_preset)
        self._thumbnail_grid.createAssetRequested.connect(
            lambda _: self._on_create_asset())
        self._thumbnail_grid.deleteRequested.connect(self._on_grid_delete)
        self._thumbnail_grid.thumbnailUpdateRequested.connect(self._on_thumbnail_update)
        self._thumbnail_grid.thumbnailImportRequested.connect(self._on_preview_thumbnail_import)
        self._thumbnail_grid.thumbnailCaptureRequested.connect(self._on_preview_thumbnail_capture)
        self._thumbnail_grid.updateAssetRequested.connect(self._on_update_asset)
        self._thumbnail_grid.dragDroppedOnViewport.connect(self._on_drag_dropped)
        self._thumbnail_grid.previewNodeRequested.connect(self._on_preview_node)
        self._thumbnail_grid.importZlightAsRenderer.connect(self._on_import_zlight_as_renderer)
        self._thumbnail_grid.applyLightToSelectionRequested.connect(self._on_apply_light_to_selection)

        self._search_bar.searchChanged.connect(self._on_search)
        self._search_bar.tagFilterChanged.connect(self._on_search_tag_changed)
        self._search_bar.tagFilterCleared.connect(self._on_search_tag_cleared)
        self._search_bar.letterClicked.connect(self._on_letter_clicked)

        self._right_panel.tagFilterRequested.connect(self._on_tag_filter_from_detail)
        self._right_panel.thumbnailCaptureRequested.connect(self._on_preview_thumbnail_capture)
        self._right_panel.thumbnailImportRequested.connect(self._on_preview_thumbnail_import)
        self._right_panel.commonTagRequested.connect(self._on_common_tag_added)

        self._thumb_slider.valueChanged.connect(self._on_thumb_slider_changed)
        self._thumbnail_grid.columnCountChanged.connect(self._on_grid_thumb_changed)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self._panel_toggle_btn.clicked.connect(self._on_panel_toggle)

        self._thumbnail_grid.selectionChanged.connect(self._update_status_bar)
        self._thumbnail_grid.selectionChanged.connect(self._update_batch_action_bar)

        # 批量操作栏信号
        self._batch_action_bar.renameRequested.connect(self._on_batch_rename)
        self._batch_action_bar.tagRequested.connect(self._on_batch_tag)
        self._batch_action_bar.moveRequested.connect(self._on_batch_move)
        self._batch_action_bar.copyRequested.connect(self._on_batch_copy)
        self._batch_action_bar.deleteRequested.connect(self._on_batch_delete)
        self._batch_action_bar.clearSelectionRequested.connect(self._on_batch_clear_selection)

    # ── 搜索 ──────────────────────────────────────────

    def _on_search(self, keyword):
        if self._use_mock:
            self._thumbnail_grid.filter_by_search(keyword)
            return

        # Dict 模式：让 mgr.search() 处理组合过滤
        self._dict_mode_search_and_set(keyword=keyword)

    # ── 搜索栏标签筛选 ────────────────────────────────

    def _on_search_tag_changed(self, tags):
        """搜索栏标签筛选变化"""
        if self._use_mock:
            self._thumbnail_grid.filter_by_tags(tags)
        else:
            self._dict_mode_search_and_set(tags=tags)

    def _on_search_tag_cleared(self):
        """搜索栏标签筛选清除"""
        if self._use_mock:
            self._thumbnail_grid.filter_by_tags([])
        else:
            self._dict_mode_search_and_set(tags=[])
        self._right_panel.clear_tag_filter()

    def _on_letter_clicked(self, letter):
        """首字母定位：找到第一个匹配的资产并滚动到其位置"""
        grid = self._thumbnail_grid
        mats = getattr(grid, '_filtered_materials', [])
        if not mats:
            return

        # 查找第一个首字母匹配的资产
        target_idx = -1
        for i, m in enumerate(mats):
            name = m.get("name_cn", m.get("name", ""))
            if not name:
                continue
            name_letter = _pinyin_first_char(name)
            if letter == "#":
                if not name_letter.isalpha():
                    target_idx = i
                    break
            elif name_letter == letter:
                target_idx = i
                break

        if target_idx < 0:
            return

        mat = mats[target_idx]
        mid = mat.get("id", "")
        grid._selected_material = mat
        if mid:
            grid._selected_materials.clear()
            grid._selected_materials[mid] = mat
        grid.materialSelected.emit(mat)

        # 图标视图 - 滚动到卡片
        card = grid._card_widgets.get(mid) if hasattr(grid, '_card_widgets') else None
        if card and hasattr(grid, '_scroll') and grid._scroll:
            vp = grid._scroll.viewport().rect()
            p = card.pos()
            s = card.size()
            grid._scroll.ensureVisible(p.x() + s.width() // 2, p.y() + s.height() // 2,
                                        vp.width() // 4, vp.height() // 4)
        # 列表视图 - 滚动到行
        if hasattr(grid, '_table_view') and hasattr(grid, '_table_model'):
            idx = grid._table_model.index(target_idx, 0)
            grid._table_view.scrollTo(idx)

    def _init_search_bar_tags(self):
        """从 manager 加载常用标签到搜索栏"""
        if self._use_mock:
            return
        all_tags = set()
        for sub_lib in ("materials", "models", "textures", "lights", "scenes", "hdr"):
            all_tags.update(self._material_manager.get_common_tags(sub_lib))
        self._search_bar.set_common_tags(sorted(all_tags))

    def _refresh_search_bar_tags(self, root_lib=None):
        """刷新搜索栏的标签菜单（标签数据变更后调用）。
        
        当 root_lib 指定时，只加载该子库的标签；
        当 root_lib 为 None 时，合并所有子库标签（用于 "all" 模式）。
        """
        if self._use_mock:
            return
        all_tags = set()
        if root_lib and root_lib != "all":
            all_tags.update(self._material_manager.get_common_tags(root_lib))
        else:
            for sub_lib in ("materials", "models", "textures", "lights", "scenes", "hdr"):
                all_tags.update(self._material_manager.get_common_tags(sub_lib))
        self._search_bar.set_common_tags(sorted(all_tags))

    def _on_common_tag_added(self, tag):
        """预览面板添加了新标签 → 同步到 manager 并刷新搜索栏"""
        mat_type = self._current_tag_type()
        self._material_manager.add_common_tag(tag, mat_type)
        self._refresh_search_bar_tags(self._current_root_lib)
        # 刷新预览面板的可用标签列表
        self._right_panel.set_common_tags(self._material_manager.get_common_tags(mat_type))

    # ── 标签筛选 ──────────────────────────────────────

    def _current_tag_type(self) -> str:
        """获取当前选中分类的类型（标签命名空间）"""
        cur = self._category_tree.get_active_category()
        return self._detect_root_library(cur)

    def _on_tag_popup(self):
        """弹出标签编辑/筛选窗口（非模态单例）"""
        from ..utils.settings import SettingsManager, apply_font_size_to_widget
        sm = SettingsManager(); sm.load()
        fs = sm.get("font_size", 13)
        scale = fs / 13.0
        win_w = int(380 * scale)
        win_h = int(320 * scale)
        tag_btn_h = int(26 * scale)
        btn_pad = int(4 * scale)

        if hasattr(self, '_tag_dialog') and self._tag_dialog is not None:
            try:
                if self._tag_dialog.isVisible():
                    self._tag_dialog.raise_()
                    self._tag_dialog.activateWindow()
                    return
            except RuntimeError:
                pass

        tag_type = self._current_tag_type()
        dlg = QtWidgets.QDialog(self)
        self._tag_dialog = dlg
        dlg.setWindowTitle("编辑标签")
        dlg.setFixedSize(win_w, win_h)
        dlg.setWindowFlags(dlg.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        dlg.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setStyleSheet("background-color: #2a2a2a;")
        lyt = QtWidgets.QVBoxLayout(dlg); lyt.setSpacing(int(8 * scale))

        hint = QtWidgets.QLabel("选中标签即可筛选  |  右键标签可删除")
        hint.setStyleSheet(f"color: #808080; font-size: {fs}px;")
        lyt.addWidget(hint)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        tag_container = QtWidgets.QWidget()
        tag_container.setStyleSheet("background: transparent;")
        tag_flow = FlowLayout(tag_container, margin=int(4 * scale), spacing=int(4 * scale))
        scroll.setWidget(tag_container)

        active_tags = set(self._thumbnail_grid._active_tags)
        all_tags = self._material_manager.get_common_tags(tag_type)

        def refresh_flow():
            while tag_flow.count():
                it = tag_flow.takeAt(0)
                if it.widget(): it.widget().deleteLater()
            for t in sorted(all_tags):
                selected = t in active_tags
                b = QtWidgets.QPushButton(t)
                b.setFixedHeight(tag_btn_h)
                b.setCheckable(True)
                b.setChecked(selected)
                b.setStyleSheet(
                    "QPushButton { background: " + ("#2d4a6f" if selected else "#333") +
                    "; color: " + ("#5294e2" if selected else "#909090") +
                    "; border: 1px solid " + ("#3a5a7a" if selected else "#444") +
                    "; border-radius: " + str(int(10 * scale)) + "px; padding: " + str(int(2 * scale)) + "px " + str(int(10 * scale)) + "px; font-size: " + str(fs) + "px; }"
                    "QPushButton:hover { background: #3a4a5a; }"
                )
                b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                def _apply_tag_filter(tag_set):
                    if self._use_mock:
                        self._thumbnail_grid.filter_by_tags(list(tag_set))
                    else:
                        self._dict_mode_search_and_set(tags=list(tag_set))

                def make_handler(tag):
                    def handler():
                        if tag in active_tags:
                            active_tags.discard(tag)
                        else:
                            active_tags.add(tag)
                        _apply_tag_filter(active_tags)
                        refresh_flow()
                        self._tag_btn.setChecked(bool(active_tags))
                    return handler
                b.clicked.connect(make_handler(t))
                def make_del(tag):
                    def h():
                        self._material_manager.remove_common_tag(tag, tag_type)
                        all_tags.remove(tag)
                        active_tags.discard(tag)
                        _apply_tag_filter(active_tags)
                        refresh_flow()
                        self._tag_btn.setChecked(bool(active_tags))
                    return h
                b.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)
                del_action = QtGui.QAction("删除", b)
                del_action.triggered.connect(make_del(t))
                b.addAction(del_action)
                tag_flow.addWidget(b)
            tag_container.adjustSize()

        refresh_flow()
        lyt.addWidget(scroll, 1)

        add_row = QtWidgets.QHBoxLayout()
        add_input = QtWidgets.QLineEdit()
        add_input.setPlaceholderText("新标签名称...")
        add_input.setStyleSheet(f"background: #333; border: 1px solid #4a4a4a; border-radius: {int(3 * scale)}px; padding: {btn_pad}px {int(8 * scale)}px; color: #e0e0e0; font-size: {fs}px;")
        add_row.addWidget(add_input, 1)
        add_btn = QtWidgets.QPushButton("添加")
        add_btn.setStyleSheet(f"QPushButton {{ background: #5294e2; color: #fff; border: none; border-radius: {int(3 * scale)}px; padding: {btn_pad}px {int(12 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ background: #6ab0ff; }}")
        def add_tag():
            t = add_input.text().strip()
            if t and t not in all_tags:
                self._material_manager.add_common_tag(t, tag_type)
                all_tags.append(t)
                add_input.clear()
                refresh_flow()
        add_btn.clicked.connect(add_tag)
        add_row.addWidget(add_btn)
        lyt.addLayout(add_row)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        clear_btn = QtWidgets.QPushButton("清除筛选")
        clear_btn.setStyleSheet(f"QPushButton {{ background: #3a3a3a; color: #a0a0a0; border: none; border-radius: {int(3 * scale)}px; padding: {btn_pad}px {int(12 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ color: #e0e0e0; }}")
        clear_btn.clicked.connect(lambda: (active_tags.clear(), self._thumbnail_grid.filter_by_tags([]), self._tag_btn.setChecked(False), dlg.close()))
        btn_row.addWidget(clear_btn)
        ok_btn = QtWidgets.QPushButton("关闭")
        ok_btn.setStyleSheet(f"QPushButton {{ background: #5294e2; color: #fff; border: none; border-radius: {int(3 * scale)}px; padding: {btn_pad}px {int(16 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ background: #6ab0ff; }}")
        ok_btn.clicked.connect(dlg.close)
        btn_row.addWidget(ok_btn)
        lyt.addLayout(btn_row)

        dlg.finished.connect(lambda: self._tag_btn.setChecked(bool(active_tags)))
        dlg.finished.connect(lambda: setattr(self, '_tag_dialog', None))

        apply_font_size_to_widget(dlg, fs)
        dlg.show()

    # ── 样式 ──────────────────────────────────────────

    def _read_qss(self):
        qss_path = os.path.join(SCRIPT_DIR, "resources", "styles", "main.qss")
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _apply_styles(self):
        font_size = self._app_settings.get("font_size", 13)
        qss = self._read_qss()
        if qss:
            self.setStyleSheet(qss)
        self._force_font_size(font_size)
        self._update_toolbar_font_size(font_size)
        self._set_tags_font_size(font_size)
        if hasattr(self, '_batch_action_bar') and self._batch_action_bar:
            self._batch_action_bar.set_font_size(font_size)

    def _force_font_size(self, font_size):
        import re
        font = QtGui.QFont()
        font.setPointSize(font_size)
        self.setUpdatesEnabled(False)
        try:
            for w in self.findChildren(QtWidgets.QWidget):
                w.setFont(font)
                ss = w.styleSheet()
                if ss:
                    ss = re.sub(r'font-size:\s*\d+px', f'font-size: {font_size}px', ss)
                    w.setStyleSheet(ss)
        finally:
            self.setUpdatesEnabled(True)

    def _update_toolbar_font_size(self, font_size):
        """更新工具栏高度和按钮大小以适应新字体"""
        toolbar = self.findChild(QtWidgets.QFrame, "toolbar")
        if not toolbar:
            return
        btn_padding = max(7, int(font_size * 0.5))
        btn_height = max(font_size + btn_padding * 2, int(font_size * 2.5))
        toolbar_height = max(44, btn_height + 12)  # 12 = layout margins (6+6)
        toolbar.setFixedHeight(toolbar_height)

        # Panel toggle button
        self._panel_toggle_btn.setFixedSize(btn_height, btn_height)
        self._panel_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #909090; border: none;"
            f"font-size: {font_size + 5}px; font-weight: bold; }}"
            "QPushButton:hover { color: #e0e0e0; }"
        )

        # Search bar
        self._search_bar.set_font_size(font_size)

        # Other toolbar buttons (refresh, quick tools, settings, help)
        btn_style = f"""
            QPushButton {{ background-color: #3a3a3a; color: #d0d0d0; border: none;
                padding: {btn_padding}px 14px; font-size: {font_size}px; border-radius: 4px; }}
            QPushButton:hover {{ background-color: #4a4a4a; }}
            QPushButton:pressed {{ background-color: #2a2a2a; }}
            QPushButton:checked {{ background-color: #5294e2; color: #ffffff; }}
        """
        for btn in toolbar.findChildren(QtWidgets.QPushButton):
            if btn is self._panel_toggle_btn:
                continue
            # 跳过搜索栏内的按钮（由 search_bar.set_font_size 管理）
            if btn.parent() and hasattr(btn.parent(), '_search_input'):
                continue
            if btn.objectName() == "help_btn":
                btn.setFixedSize(btn_height, btn_height)
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: #3a3a3a; color: #ffa502; border: none;"
                    f"font-size: {font_size + 5}px; font-weight: bold; border-radius: 4px; }}"
                    "QPushButton:hover { background-color: #4a4a4a; }"
                )
            else:
                btn.setStyleSheet(btn_style)

        # Quick tools menu
        self._quick_tools_menu.setStyleSheet(f"""
            QMenu {{ background-color:#2a2a2a; color:#d0d0d0; border:1px solid #3a3a3a; padding:4px; }}
            QMenu::item {{ padding:6px 24px 6px 14px; font-size:{font_size}px; }}
            QMenu::item:selected {{ background-color:#2d4a6f; color:#5294e2; }}
        """)

    def _set_tags_font_size(self, font_size):
        """更新资产标签字体大小"""
        if hasattr(self, '_right_panel') and hasattr(self._right_panel, 'set_font_size'):
            self._right_panel.set_font_size(font_size)

    # ── 交互 ─────────────────────────────────────────

    def _switch_view(self, mode):
        self._current_view = mode
        self._icon_view_btn.setChecked(mode == self.VIEW_ICON)
        self._list_view_btn.setChecked(mode == self.VIEW_LIST)
        self._thumbnail_grid.set_view_mode(mode)

    def _on_category_selected(self, category_id, descendant_ids, root_lib="materials"):
        """选中分类 → 统一 .zasset 路径刷新网格（所有子库同一套逻辑）"""
        # category_id 可能是复合 ID（如 "textures||AAAcustom"），提取 short_id
        if "||" in category_id:
            _, cat_short = split_cat_id(category_id)
        else:
            cat_short = category_id
        self._current_root_lib = root_lib
        if self._use_mock:
            self._thumbnail_grid.filter_by_category(cat_short, descendant_ids)
            return

        # 切换子库时清除旧标签筛选，避免跨库标签串扰
        if root_lib != getattr(self, '_prev_root_lib', None):
            self._search_bar._clear_all_tags()
        self._prev_root_lib = root_lib

        # 按当前子库刷新标签列表（只显示当前顶级分类的标签）
        self._refresh_search_bar_tags(root_lib)

        # Dict 模式：让 mgr.search() 处理组合过滤（分类+搜索+标签共存）
        self._dict_mode_search_and_set(category=cat_short)

    def _on_categories_multi_selected(self, cat_ids, all_desc_ids, root_lib="materials"):
        """多选分类 → 聚合所有选中分类及其后代的资产

        修复要点：
        1. 多选不触发 _clear_all_tags → 避免 _dict_mode_search_and_set 中途覆盖缩略图
        2. 跨子库多选时搜索所有涉及的子库并合并结果
        """
        mgr = self._project_mgr if self._active_mgr is self._project_mgr else self._material_manager

        # ── 0. 推断所有涉及的根子库 ──
        all_root_libs = set()
        for cid in cat_ids:
            if cid in mgr.ASSET_SUB_LIBRARIES:
                all_root_libs.add(cid)
            else:
                all_root_libs.add(root_lib)

        # 更新当前 root_lib 为首个（用于后续单选的标签切换检测）
        self._current_root_lib = root_lib

        # 多选不触发 _clear_all_tags — 该逻辑专为单选跨子库标签隔离设计，
        # 在多选下会触发 _dict_mode_search_and_set → 中途覆盖正确缩略图数据
        # 多选结束后也不更新 _prev_root_lib（保留单选记忆，下次单选时仍可正确检测切换）

        # ── 1. 收集搜索参数 ──
        kw = self._search_bar.text()
        restored_tags = set(self._thumbnail_grid._active_tags)
        q_tags = list(restored_tags) if restored_tags else []

        # ── 2. 搜索所有涉及的子库并合并 ──
        all_results = []
        for sub_lib in all_root_libs:
            query = {"sub_library": sub_lib}
            if kw:
                query["keyword"] = kw
            if q_tags:
                query["tags"] = q_tags
            all_results.extend(mgr.search(query))

        # ── 3. 后过滤：只保留属于任何选中分类或其子分类的资产 ──
        desc_set = set(all_desc_ids)
        filtered = [m for m in all_results if m.category in desc_set]
        dicts = [m.to_dict() for m in filtered]

        # 为每个资产设置显示属性（从第一个根子库取 asset_type 展示用）
        first_sub_lib = next(iter(all_root_libs), root_lib)
        asset_type = mgr.ASSET_SUB_LIBRARIES.get(first_sub_lib, first_sub_lib)
        for d in dicts:
            d["_category_display"] = mgr.get_category_display_name(
                d.get("category", ""))
            d["_asset_type"] = asset_type

        self._thumbnail_grid.set_materials(dicts)
        if q_tags:
            self._thumbnail_grid._active_tags = restored_tags
            self._thumbnail_grid._active_filters.add("tags")
        if kw:
            self._thumbnail_grid._current_search_kw = kw
            self._thumbnail_grid._active_filters.add("search")
        # 用逗号连接所有选中分类作为 cat_id
        self._thumbnail_grid._current_cat_id = ",".join(cat_ids)
        self._thumbnail_grid._active_filters.add("category")

    def _detect_root_library(self, category_id) -> str:
        """从分类树数据检测 category_id 属于哪个根子库。
        使用节点 type 字段（创建时从 FolderMetadata 继承），直通根子库。"""
        if not category_id or category_id == "all":
            return "materials"
        cat, _ = self._category_tree._find_category(category_id)
        # type 字段在 get_category_tree() 中从 FolderMetadata 读取，始终指向根子库
        if cat and cat.get("type") in self._material_manager.ASSET_SUB_LIBRARIES:
            return cat["type"]
        if category_id in self._material_manager.ASSET_SUB_LIBRARIES:
            return category_id
        # 从树中直接查找（自定义根文件夹）
        for c in self._category_tree._categories:
            if c["id"] == category_id:
                return category_id
        return "materials"  # default

    # ── 分类统一工具方法 ─────────────────────────

    def _find_category_folder(self, cat_id, root_lib=None) -> str:
        """搜索 cat_id 对应的磁盘文件夹路径，找不到返回空字符串。
        指定 root_lib 时只在对应子库下搜索，避免同名跨子库歧义。
        同时处理根目录匹配：如 cat_id="materials" 且 root_lib="materials"→直接返回根路径。
        """
        lib = self._active_mgr.get_library_path()
        if not lib:
            return ""
        # 指定了 root_lib 则限定搜索范围
        if root_lib:
            root_path = os.path.join(lib, root_lib)
            # 根目录本身匹配 → 直接返回
            if cat_id == root_lib and os.path.isdir(root_path):
                return root_path
            if not os.path.isdir(root_path):
                return ""
            for root, dirs, _ in os.walk(root_path):
                for d in dirs:
                    if d == cat_id:
                        return os.path.join(root, d)
        else:
            for root, dirs, _ in os.walk(lib):
                for d in dirs:
                    if d == cat_id:
                        return os.path.join(root, d)
        return ""

    # ── 分类增删改 ────────────────────────────────

    def _get_tree_root_lib(self):
        """从当前激活分类树的选中项读取所属根子库 ID"""
        tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
        item = tree._tree.currentItem()
        if item:
            rl = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
            if rl:
                return rl
        return "materials"

    def _on_category_added(self, cat_dict):
        """添加分类 → 在当前选中文件夹下创建子文件夹 + 元数据 JSON"""
        if self._use_mock:
            return
        mgr = self._active_mgr
        cat_id = cat_dict["id"]
        parent_id = cat_dict.get("parent")
        lib = mgr.get_library_path()
        if not lib:
            return

        # 优先级：信号中的 root_lib（右键场景可从 item 提取）>
        #          _current_root_lib（左键点击设置）>
        #          _get_tree_root_lib()（依赖 tree.currentItem，右键不准）
        root_lib = (cat_dict.get("root_lib")
                    or self._current_root_lib
                    or self._get_tree_root_lib()
                    or "materials")

        # 确定目标路径和父类型
        if parent_id:
            parent_path = self._find_category_folder(parent_id, root_lib)
            if not parent_path:
                print(f"[MaterialLibrary] 找不到父文件夹: {parent_id} (root={root_lib})")
                return
            cat_dir = os.path.join(parent_path, cat_id)
            parent_meta = mgr._read_folder_meta(parent_path)
            asset_type = parent_meta.get("type", root_lib)
        else:
            # 顶级分类：直接在库根目录下创建，不涉及子库
            cat_dir = os.path.join(lib, cat_id)
            asset_type = cat_dict.get("type") or root_lib

        os.makedirs(cat_dir, exist_ok=True)
        import uuid
        mgr._write_folder_meta(cat_dir, {
            "id": str(uuid.uuid4()),
            "name_cn": cat_dict["name_cn"],
            "type": asset_type,
        })
        self._refresh_category_tree()
        # 创建后自动选中新文件夹
        self._category_tree._select_by_id(cat_id)
        composite = join_cat_id(root_lib, cat_id)
        self._category_tree._active_category = composite
        desc_ids = self._category_tree.get_descendant_ids(cat_id)
        self._on_category_selected(cat_id, desc_ids, root_lib)

    # ── 独立路径：右键子库根节点添加顶级分类 ────────────

    def _on_add_top_level_category(self, cat_id, name_cn, root_lib):
        """独立路径：右键子库根节点添加顶级分类，与 _on_category_added 完全隔离"""
        if self._use_mock:
            return
        mgr = self._active_mgr
        lib = mgr.get_library_path()
        if not lib:
            return
        cat_dir = os.path.join(lib, root_lib, cat_id)
        os.makedirs(cat_dir, exist_ok=True)
        import uuid
        mgr._write_folder_meta(cat_dir, {
            "id": str(uuid.uuid4()),
            "name_cn": name_cn,
            "type": root_lib,
        })
        self._refresh_category_tree()
        self._category_tree._select_by_id(cat_id)
        composite = join_cat_id(root_lib, cat_id)
        self._category_tree._active_category = composite
        desc_ids = self._category_tree.get_descendant_ids(cat_id)
        self._on_category_selected(cat_id, desc_ids, root_lib)

    def _on_category_edited(self, cat_id, new_name_cn, root_lib="materials"):
        """编辑易读名 → 写入 FolderMetadata"""
        if self._use_mock:
            return
        mgr = self._active_mgr
        folder = self._find_category_folder(cat_id, root_lib)
        if folder:
            meta = mgr._read_folder_meta(folder)
            meta["name_cn"] = new_name_cn
            mgr._write_folder_meta(folder, meta)
            self._refresh_category_tree()
        else:
            print(f"[MainWindow] _on_category_edited: 未找到文件夹 {cat_id} (root_lib={root_lib})")

    def _on_category_deleted(self, cat_id, root_lib="materials", parent_id=""):
        """分类删除 → 删除内存 + 磁盘文件夹，删除后自动选中父分类"""
        if self._use_mock:
            return
        mgr = self._active_mgr
        mgr._categories.pop(cat_id, None)

        lib = mgr.get_library_path()
        folder = self._find_category_folder(cat_id, root_lib)
        if not folder and lib:
            root_folder = os.path.join(lib, cat_id)
            if os.path.isdir(root_folder):
                folder = root_folder
        if folder:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
            print(f"[MaterialLibrary] 删除文件夹: {folder}")

        # 材质移到 custom
        if root_lib == "materials":
            for mid, mat in list(mgr._materials.items()):
                if mat.category == cat_id:
                    mat.category = "custom"

        mgr.reload()
        self._refresh_category_tree()

        # 删除后自动选中父分类
        target_id = parent_id or "all"
        if target_id and target_id != "all":
            self._category_tree._active_category = join_cat_id(root_lib, target_id)
        else:
            self._category_tree._active_category = "all"
        if parent_id:
            self._category_tree._select_by_id(parent_id)
            # 展开父分类本身（会触发 categorySelected → _on_category_selected → 自动刷新网格）
            item = self._category_tree._tree.currentItem()
            if item and item.childCount() > 0:
                item.setExpanded(True)
            else:
                # 无子节点 → 无展开信号 → 需手动刷新
                self._refresh_material_grid(target_id)
        else:
            # 删的是顶级分类 → 回到 "all"
            self._refresh_material_grid("all")

    def _on_open_category_folder(self, cat_id):
        """在资源管理器打开分类文件夹（支持所有子库）"""
        mgr = self._active_mgr
        lib = mgr.get_library_path()
        if not lib:
            return

        tree = self._proj_category_tree if mgr is self._project_mgr else self._category_tree
        item = tree._tree.currentItem()
        if not item:
            return

        # 从树节点层级反向构建完整相对路径
        parts = []
        current = item
        root_lib = "materials"
        while current:
            mid = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
            rl = current.data(0, QtCore.Qt.ItemDataRole.UserRole + 3)
            depth = current.data(0, QtCore.Qt.ItemDataRole.UserRole + 2)
            if depth == 0:
                root_lib = rl or mid
                break
            parts.append(mid)
            current = current.parent()
        parts.reverse()

        full_path = os.path.join(lib, root_lib, *parts)
        if os.path.isdir(full_path):
            os.startfile(full_path)

    def _on_open_material_folder(self, mat_dict):
        """在资源管理器打开文件并选中对应 .zasset 文件"""
        # 直接从 mat_dict 取 json_path（to_dict 已包含）
        file_path = mat_dict.get("json_path", "")
        if not file_path or not os.path.exists(file_path):
            # 尝试通过 ID 从 manager 获取
            mid = mat_dict.get("id", "")
            if mid and self._active_mgr.get_library_path():
                mat = self._active_mgr.get_by_id(mid)
                if mat and mat.json_path and os.path.exists(mat.json_path):
                    file_path = mat.json_path
        if file_path and os.path.exists(file_path):
            if os.path.isfile(file_path):
                # explorer /select 选中文件
                subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
            else:
                os.startfile(os.path.normpath(file_path))

    def _on_move_material(self, mat_dict):
        """右键 → 移动材质到同子库分类（支持嵌套层级显示，用缩进区分父子）"""
        if self._use_mock:
            return
        mid = mat_dict.get("id", "")
        mat = self._active_mgr.get_by_id(mid)
        if not mat or not mat.json_path:
            return

        # 从 .zasset 路径推断所属子库（相对路径第一段）
        lib = self._active_mgr.get_library_path()
        rel_path = os.path.relpath(mat.json_path, lib)
        sub_lib = rel_path.split(os.sep)[0] if os.sep in rel_path else "materials"

        # 获取分类树，扁平化同一子库下的所有分类（层级用缩进区分，避免同名混淆）
        tree = self._active_mgr.get_category_tree()
        cats = []
        cat_ids = []

        def collect(node, depth=0):
            if node["id"] == "all":
                return
            # 深度 0 = 子库根节点（materials/models/...），跳过非同子库
            if depth == 0 and node["id"] != sub_lib:
                return
            if depth == 0:
                # 子库根节点本身不加入列表，只递归子节点
                for child in node.get("children", []):
                    collect(child, depth + 1)
                return

            # 构建层级缩进显示名（如 "  fabric / sub_fabric"）
            prefix = "  " * (depth - 1)
            display_name = node.get("name_cn") or node.get("name") or node["id"]
            cats.append(prefix + display_name)
            cat_ids.append(node["id"])

            for child in node.get("children", []):
                collect(child, depth + 1)

        for sub_node in tree:
            collect(sub_node)

        if not cats:
            return
        name, ok = QtWidgets.QInputDialog.getItem(
            self, f"移动 {mat.get_display_name()}", "选择目标分类:", cats, 0, False)
        if not ok:
            return
        idx = cats.index(name)
        target = cat_ids[idx] or "custom"
        if mat.category == target:
            return
        self._move_material_to_category(mat.id, target, sub_lib)
        # 移动后刷新计数+跳转目标分类
        # 阻塞树信号，避免 _refresh_category_tree 内 _select_all 发射错误的
        # categorySelected 信号（root_lib="materials"）覆盖当前 root_lib
        tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
        tree._tree.blockSignals(True)
        self._refresh_category_tree()
        tree._tree.blockSignals(False)
        tree._select_by_id(target, sub_lib)
        tree._active_category = join_cat_id(sub_lib, target)
        desc_ids = tree.get_descendant_ids(target, sub_lib)
        self._on_category_selected(target, desc_ids, sub_lib)

    def _on_category_moved(self, cat_id, new_parent_id):
        """拖拽分类 → 移动文件夹"""
        if self._use_mock:
            return
        lib = self._material_manager.get_library_path()
        if not lib:
            return

        src = self._material_manager.get_category_disk_path(cat_id)
        if not src:
            return

        # 从 src 推断所属子库（models/materials/textures/lights/scenes/hdr）
        rel = os.path.relpath(src, lib)
        sub_lib = rel.split(os.sep)[0] if os.sep in rel else "materials"

        if new_parent_id and new_parent_id != "all":
            # 移动到另一个顶级分类或子分类下
            parent_path = self._material_manager.get_category_disk_path(new_parent_id)
            if not parent_path:
                parent_path = os.path.join(lib, sub_lib, new_parent_id)
            dst = os.path.join(parent_path, cat_id)
        else:
            dst = os.path.join(lib, sub_lib, cat_id)

        if src != dst and not os.path.exists(dst):
            import shutil
            shutil.move(src, dst)
        self._material_manager.reload()
        self._refresh_category_tree()
        self._update_status_bar()
        print(f"[MaterialLibrary] 移动分类: {cat_id} → {new_parent_id or '根'}")

    def _on_clipboard_changed(self, ids):
        self._clipboard = list(ids)

    def _on_copy_to_project(self, material_ids):
        """复制选中材质到项目库"""
        self._clipboard = list(material_ids)  # 存入剪贴板
        if not material_ids or self._use_mock:
            return
        # 确保项目库已加载
        lib_path = self._get_project_library_path()
        if not os.path.isdir(lib_path):
            QtWidgets.QMessageBox.information(self, "提示", "请先在项目选项卡中点击「创建」按钮初始化项目库")
            return
        self._project_mgr.load_library(lib_path)

        # 收集项目库的分类（用于选择目标）
        tree = self._project_mgr.get_category_tree()
        cats = []; cat_ids = []
        def collect(nodes):
            for n in nodes:
                if n["id"] != "all":
                    cats.append(n.get("name_cn") or n.get("name") or n["id"])
                    cat_ids.append(n["id"])
                    collect(n.get("children", []))
        collect(tree)
        if not cats:
            QtWidgets.QMessageBox.information(self, "提示", "项目库中还没有分类，请先在项目标签中创建分类")
            return

        name, ok = QtWidgets.QInputDialog.getItem(
            self, "复制到项目库", f"选择目标分类 ({len(material_ids)} 个材质):", cats, 0, False)
        if not ok:
            return
        target = cat_ids[cats.index(name)]

        imported = 0
        for mid in material_ids:
            mat = self._material_manager.get_by_id(mid)
            if not mat or not mat.json_path:
                continue
            result = self._project_mgr.add_material(mat.json_path, target, mat.sub_library or "materials")
            if result:
                imported += 1
        if imported:
            self._on_refresh_project()
            QtWidgets.QMessageBox.information(self, "完成", f"已复制 {imported} 个材质到项目库")
        else:
            QtWidgets.QMessageBox.warning(self, "失败", "未能复制材质")

    def _on_paste(self):
        """粘贴剪贴板中的材质到当前分类"""
        if not self._clipboard:
            return
        # 从当前选中分类树读取分类和子库（确保直接取最新值）
        if self._active_mgr is self._project_mgr:
            cur_composite = self._proj_category_tree.get_active_category()
            sub_lib = self._proj_category_tree.get_active_root_lib()
        else:
            cur_composite = self._category_tree.get_active_category()
            sub_lib = self._category_tree.get_active_root_lib()
        # 复合 ID → 提取 short_id 传给 manager
        if cur_composite and cur_composite != "all":
            _, cur = split_cat_id(cur_composite)
            if not cur:
                cur = cur_composite
        else:
            cur = "custom"
        imported = 0
        for mid in self._clipboard:
            # 先在当前激活库查找，找不到再从另一个库查找（跨库粘贴）
            mat = self._active_mgr.get_by_id(mid)
            if not mat:
                other = self._project_mgr if self._active_mgr is self._material_manager else self._material_manager
                mat = other.get_by_id(mid)
            if not mat or not mat.json_path:
                continue
            result = self._active_mgr.add_material(mat.json_path, cur, sub_lib, force_category=True)
            if result:
                imported += 1
        if imported:
            # add_material 已加入内存索引，不 reload 避免文件系统缓存延迟
            self._refresh_category_tree()
            tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
            tree._select_by_id(cur, sub_lib)
            tree._active_category = cur
            desc_ids = tree.get_descendant_ids(cur, sub_lib)
            self._on_category_selected(cur, desc_ids, sub_lib)

    def _on_material_dropped_on_category(self, mat_id, cat_id, root_lib=""):
        """拖拽材质卡片到分类（支持多选批量，延迟刷新+跳转目标）"""
        self._move_material_to_category(mat_id, cat_id, root_lib)
        # 保存目标分类信息，延迟批量刷新（多选拖拽时80ms防抖）
        self._drop_target = (cat_id, root_lib)
        if not hasattr(self, '_cat_refresh_timer'):
            self._cat_refresh_timer = QtCore.QTimer()
            self._cat_refresh_timer.setSingleShot(True)
            self._cat_refresh_timer.timeout.connect(self._batch_drop_refresh)
        self._cat_refresh_timer.start(80)

    def _batch_drop_refresh(self):
        """拖拽刷新：跳转到最后拖入的目标分类"""
        target, root_lib = getattr(self, '_drop_target', (None, ""))
        if not target:
            return
        tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
        tree._tree.blockSignals(True)
        self._refresh_category_tree()
        tree._tree.blockSignals(False)
        tree._select_by_id(target, root_lib)
        tree._active_category = target
        desc_ids = tree.get_descendant_ids(target, root_lib)
        self._on_category_selected(target, desc_ids, root_lib)

    def _move_material_to_category(self, material_id_or_path, cat_id, sub_lib=""):
        """将材质移动到目标分类目录（兼容 UUID 和 json_path，支持子分类）

        统一委托 Manager.move_material_to_category() 处理 .zasset 文件移动。"""
        if self._use_mock or cat_id == "all":
            return
        mat = (self._active_mgr.get_by_id(material_id_or_path)
               or self._active_mgr.get_by_path(material_id_or_path))
        if not mat or mat.category == cat_id or not mat.json_path:
            return

        try:
            self._active_mgr.move_material_to_category(mat.id, cat_id, sub_lib=sub_lib)
            mat.category = cat_id
        except Exception as e:
            print(f"[MaterialLibrary] 移动材质失败: {material_id_or_path} -> {cat_id}: {e}")

    def _batch_cat_refresh(self):
        """批量移动后的统一刷新 → 委托 _refresh_keep_current"""
        self._refresh_keep_current()

    def _on_material_selected(self, material):
        self._right_panel.show_material(material)
        # 预填编辑分类列表（缓存，避免每次选中都重建）
        cat_tree = getattr(self, '_cached_cat_tree', None)
        if cat_tree is None:
            cat_tree = self._active_mgr.get_category_tree()
            self._cached_cat_tree = cat_tree
        self._right_panel.set_edit_categories(cat_tree)
        # 获取当前类型的标签（缓存）
        mat_type = self._current_tag_type()
        cache_key = ('common_tags', mat_type)
        common_tags = self._cached_ui_data.get(cache_key) if hasattr(self, '_cached_ui_data') else None
        if common_tags is None:
            common_tags = self._active_mgr.get_common_tags(mat_type)
            if not hasattr(self, '_cached_ui_data'):
                self._cached_ui_data = {}
            self._cached_ui_data[cache_key] = common_tags
        self._right_panel.set_common_tags(common_tags)
        self._status_info.setText(f"已选择: {material.get('name_cn', '')}")
        # 同步收藏状态
        mid = material.get("id", "")
        if mid:
            is_fav = any(mid in s for s in self._active_mgr._favorites.values())
            material["_favorited"] = is_fav

    def _on_material_applied(self, material):
        """应用材质到选中物体 — 支持 .zmetal 和 PBR 贴图两种资产"""
        print(f"[MaterialLibrary] 应用材质: {material.get('name_cn')}")
        try:
            import maya.cmds as cmds
            import json
            from ..core.zasset_io import ZassetIO
            json_path = material.get("json_path", "")
            if not json_path or not json_path.endswith(".zasset"):
                return

            # 保存选中物体（创建过程可能触发 Maya 自动取消选中）
            saved_sel = cmds.ls(sl=True, long=True) or []

            # 检测 .zmetal 是否存在
            all_names = ZassetIO.list_contents(json_path)
            zmetal_name = "node.zmetal"
            if zmetal_name not in all_names:
                zmetals = [n for n in all_names if n.endswith(".zmetal")]
                zmetal_name = zmetals[0] if zmetals else ""

            if zmetal_name:
                # ── .zmetal 材质网络路径 ──
                root_material_names = []
                data = json.loads(ZassetIO.read_file(json_path, zmetal_name))
                root_material_names = data.get("root_materials", [])

                from ..integration.import_executor import apply_zmetal_as_material
                if apply_zmetal_as_material(json_path):
                    if not saved_sel:
                        print(f"[MaterialLibrary] 材质已创建（无选中物体，未赋予）")
                        return
                    assigned = False
                    for mat_name in root_material_names:
                        if cmds.objExists(mat_name):
                            ntype = cmds.nodeType(mat_name)
                            if ntype not in ("place2dTexture", "file", "bump2d", "layeredTexture"):
                                cmds.select(saved_sel, replace=True)
                                cmds.hyperShade(assign=mat_name)
                                print(f"[MaterialLibrary] 赋予材质 {mat_name} ({ntype}) → {len(saved_sel)} 个物体")
                                assigned = True
                                break
                    if not assigned:
                        after = cmds.ls(type="shadingDependNode")
                        for n in reversed(after):
                            ntype = cmds.nodeType(n)
                            if ntype not in ("place2dTexture", "file", "bump2d", "layeredTexture"):
                                cmds.select(saved_sel, replace=True)
                                cmds.hyperShade(assign=n)
                                print(f"[MaterialLibrary] 赋予材质(回退) {n} ({ntype}) → {len(saved_sel)} 个物体")
                                break
                else:
                    print(f"[MaterialLibrary] 材质创建失败")
            else:
                # ── 无 .zmetal → PBR 贴图资产，创建 openPBR 材质并连接贴图 ──
                self._apply_pbr_texture_material(json_path, saved_sel, material)

        except Exception as e:
            print(f"[MaterialLibrary] 应用材质失败: {e}")
            import traceback
            traceback.print_exc()

    def _apply_pbr_texture_material(self, json_path, saved_sel, material):
        """为 PBR 贴图资产创建材质并赋予选中物体（委托 pbr_to_zasset.create_material）"""
        import maya.cmds as cmds
        import json, os, re
        from ..core.zasset_io import ZassetIO
        from ..quicktools.pbr_to_zasset import create_material

        try:
            meta = ZassetIO.read_meta(json_path)
            if not meta or not meta.get("properties"):
                print(f"[MaterialLibrary] 该资产无贴图属性数据")
                return

            # 加载 pbr_mapping.json
            mapping_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'Assets', 'preset', 'pbr_mapping.json')
            pbr_config = {}
            try:
                with open(mapping_path, 'r', encoding='utf-8') as f:
                    pbr_config = json.load(f)
            except Exception as le:
                print(f"[MaterialLibrary] pbr_mapping加载失败: {le}")
                return
            pbr_config['default_material_type'] = 'openPBRSurface'

            properties = meta.get("properties", {})

            # 提取贴图到磁盘
            default_res = meta.get("default_resolution", "")
            tex_files = [n for n in ZassetIO.list_contents(json_path)
                         if n.startswith("textures/") and not n.endswith("/")]
            if default_res:
                tex_files = [n for n in tex_files
                             if n.startswith(f"textures/{default_res}/")]
            if not tex_files:
                print(f"[MaterialLibrary] 未找到贴图文件")
                return

            asset_name = meta.get("name",
                                 os.path.splitext(os.path.basename(json_path))[0])
            asset_id = meta.get("id", "")
            suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
            try:
                ws_root = cmds.workspace(q=True, rd=True) or ""
                si_rule = cmds.workspace(fileRuleEntry="sourceImages")
                base = os.path.join(ws_root, si_rule) if si_rule else ws_root
                base = os.path.normpath(base)
            except Exception:
                base = os.path.normpath(os.path.join(
                    os.path.expanduser("~/Documents/maya/projects/default"),
                    "sourceimages"))
            target_dir = os.path.join(base, "squirrel_asset", f"{asset_name}{suffix}")
            os.makedirs(target_dir, exist_ok=True)

            tex_path_map = {}
            for name in tex_files:
                rel = name[len("textures/"):]
                target_path = os.path.join(target_dir, rel).replace("\\", "/")
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                data = ZassetIO.read_file(json_path, name)
                if os.path.isfile(target_path):
                    if os.path.getsize(target_path) == len(data):
                        tex_path_map[name] = target_path
                        continue
                with open(target_path, 'wb') as f:
                    f.write(data)
                tex_path_map[name] = target_path

            # 跨精度文件名匹配
            _RES_PATTERN = re.compile(r'_\d+[kK](?=_|\.)', re.IGNORECASE)
            def _norm_name(name):
                return _RES_PATTERN.sub('', name)

            fname_to_disk = {}
            for zip_rel, disk_path in tex_path_map.items():
                fname = os.path.basename(zip_rel.replace("\\", "/"))
                fname_to_disk[fname] = disk_path
                fname_to_disk[fname.lower()] = disk_path
                fname_to_disk[_norm_name(fname)] = disk_path
                fname_to_disk[_norm_name(fname).lower()] = disk_path

            def _find_disk_path(tex_filename):
                for candidate in (tex_filename, tex_filename.lower()):
                    if candidate in fname_to_disk:
                        return fname_to_disk[candidate]
                basename = os.path.basename(tex_filename.replace("\\", "/"))
                for candidate in (basename, basename.lower()):
                    if candidate in fname_to_disk:
                        return fname_to_disk[candidate]
                    n = _norm_name(candidate)
                    if n in fname_to_disk:
                        return fname_to_disk[n]
                for k, v in fname_to_disk.items():
                    if k.lower() in (tex_filename.lower(), basename.lower()):
                        return v
                    if _norm_name(k).lower() == _norm_name(basename).lower():
                        return v
                return None

            # 构建 textures dict
            color_space_map = {}
            type_rules = pbr_config.get('texture_type_rules', {})
            for pbr_type, rule in type_rules.items():
                color_space_map[pbr_type] = rule.get('color_space', 'sRGB')
                if rule.get('is_combo'):
                    for usage_type in rule.get('channels', {}).values():
                        if isinstance(usage_type, dict):
                            usage = usage_type.get('usage', '')
                            if usage:
                                color_space_map[usage] = rule.get('color_space', 'Raw')
                        elif isinstance(usage_type, str):
                            color_space_map[usage_type] = rule.get('color_space', 'Raw')

            textures = {}
            for tex_type, info in properties.items():
                if info.get('type') != 'texture':
                    continue
                tex_filename = info.get('path', '')
                if not tex_filename:
                    continue
                disk_path = _find_disk_path(tex_filename)
                if not disk_path:
                    continue
                base = tex_type
                if base.startswith('normal_'):
                    base = 'normal'
                elif base.startswith('height_'):
                    base = 'height'
                cs = color_space_map.get(base, color_space_map.get(tex_type, 'sRGB'))
                textures[tex_type] = {'full_path': disk_path, 'color_space': cs}

            # 调用统一 create_material
            material_name = f"{asset_name}{suffix}"
            shader, result = create_material(material_name, textures, pbr_config)

            if shader:
                print(f"[MaterialLibrary] PBR 材质已创建: {shader}, 贴图数: {len(textures)}")
                if saved_sel:
                    cmds.select(saved_sel, replace=True)
                    cmds.hyperShade(assign=shader)
                    print(f"[MaterialLibrary] 赋予 {shader} → {len(saved_sel)} 个物体")
            else:
                print(f"[MaterialLibrary] PBR 材质创建失败: {result}")

        except Exception as e:
            print(f"[MaterialLibrary] PBR 贴图材质创建失败: {e}")
            import traceback
            traceback.print_exc()

    def _on_favorite_toggled(self, material_id, is_fav):
        """收藏切换 → 使用当前激活的管理器"""
        if not self._use_mock:
            is_fav = self._active_mgr.toggle_favorite(material_id, self._current_fav_collection)
        self._sync_fav_ui_state(material_id, is_fav)
        self._sync_favorites_panel()
        self._active_mgr.save_favorites()

    def _on_edit_material(self, mat_dict):
        """编辑材质元数据（右键弹出窗口或右侧内联编辑）"""
        if self._use_mock:
            self._show_edit_dialog(mat_dict)
            return

        path = mat_dict.get("json_path", "")
        mgr = self._active_mgr
        mat = mgr.get_by_path(path) if path else None
        if not mat:
            return

        new_cn = mat_dict.get("name_cn", mat.name_cn)
        new_tags = mat_dict.get("tags", mat.tags)
        new_cat = mat_dict.get("category", mat.category)
        new_notes = mat_dict.get("notes", mat.notes)
        mid = mat.id

        cat_changed = new_cat and new_cat != mat.category
        if cat_changed:
            self._move_material_to_category(path, new_cat)

        # 内存立即生效 + 后台写磁盘
        mat.name_cn = new_cn
        mat.tags = new_tags
        mat.notes = new_notes
        mgr._refresh_material_counts()
        disk_work = lambda: mgr.update_material(mid, {"name_cn": new_cn, "tags": new_tags, "notes": new_notes})
        import threading
        threading.Thread(target=disk_work, daemon=True).start()

        # 直接更新卡片标签 + 内存中的 dict（跳过全量 refresh）
        grid = self._thumbnail_grid
        for lst in (grid._materials, grid._filtered_materials):
            for dm in lst:
                if dm.get("id") == mid:
                    dm["name_cn"] = new_cn
                    dm["tags"] = new_tags
                    dm["notes"] = new_notes
                    break

        card = grid._card_pool.get(mid)
        if card:
            name_label = card.findChild(QtWidgets.QLabel, "nameLabel")
            if name_label:
                name_label.setText(new_cn or mat_dict.get("name", ""))

        if cat_changed:
            self._refresh_category_tree()
            self._refresh_keep_current()
        else:
            grid._apply_fav_flags()

        # 刷新右侧面板
        mat = self._active_mgr.get_by_path(path)
        if mat:
            d = mat.to_dict()
            d["_category_display"] = self._active_mgr.get_category_display_name(
                d.get("category", ""))
            self._right_panel.show_material(d)

    def _show_confirm_dialog(self, title, message, is_warning=False):
        """显示带全局字体大小的确认对话框"""
        fs = self._app_settings.get("font_size", 13)

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        if is_warning:
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        else:
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)

        font = QtGui.QFont()
        font.setPointSize(fs)
        dialog.setFont(font)

        reply = dialog.exec()
        return reply

    def _show_edit_dialog(self, mat_dict):
        """右键弹出编辑对话框（标签 pill 风格，与内联编辑一致）"""
        from ..utils.settings import SettingsManager, apply_font_size_to_widget
        sm = SettingsManager(); sm.load()
        fs = sm.get("font_size", 13)
        scale = fs / 13.0
        win_w = int(420 * scale)
        win_h = int(560 * scale)
        tag_btn_h = int(24 * scale)
        btn_pad = int(6 * scale)

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"\u7f16\u8f91 {mat_dict.get('name_cn', '')}")
        dlg.setFixedSize(win_w, win_h)
        dlg.setStyleSheet("background-color: #2a2a2a;")
        lyt = QtWidgets.QVBoxLayout(dlg); lyt.setSpacing(int(10 * scale))

        label_font = f"font-size: {fs}px; color: #e0e0e0;"
        lyt.addWidget(QtWidgets.QLabel(f'<span style="{label_font}">\u4e2d\u6587\u540d</span>'))
        cn = QtWidgets.QLineEdit(mat_dict.get("name_cn", ""))
        cn.setStyleSheet(f"background-color: #333; border: 1px solid #4a4a4a; border-radius: {int(3 * scale)}px; padding: {btn_pad}px {int(8 * scale)}px; color: #e0e0e0; font-size: {fs}px;")
        lyt.addWidget(cn)

        lyt.addWidget(QtWidgets.QLabel(f'<span style="{label_font}">\u5206\u7c7b</span>'))
        cat_combo = QtWidgets.QComboBox()
        cat_combo.setStyleSheet(f"QComboBox {{ background-color: #333; border: 1px solid #4a4a4a; border-radius: {int(3 * scale)}px; padding: {int(5 * scale)}px {int(8 * scale)}px; color: #e0e0e0; font-size: {fs}px; }} QComboBox::drop-down {{ border: none; }}")
        tree = self._active_mgr.get_category_tree()
        def add_cats(nodes, prefix=""):
            for n in nodes:
                if n["id"] != "all":
                    cat_combo.addItem(f"{prefix}{n['name_cn']}", n["id"])
                    add_cats(n.get("children", []), prefix + "  ")
        add_cats(tree)
        idx = cat_combo.findData(mat_dict.get("category", ""))
        if idx >= 0: cat_combo.setCurrentIndex(idx)
        lyt.addWidget(cat_combo)

        lyt.addWidget(QtWidgets.QLabel(f'<span style="{label_font}">\u6807\u7b7e</span>'))
        tags_w = QtWidgets.QWidget()
        tags_w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Preferred)
        tags_l = FlowLayout(tags_w, margin=0, spacing=int(3 * scale))
        tags_data = list(mat_dict.get("tags", []))

        def rebuild():
            while tags_l.count():
                it = tags_l.takeAt(0)
                if it.widget(): it.widget().deleteLater()
            for t in tags_data:
                btn = QtWidgets.QPushButton(f"\u2716 {t}")
                btn.setFixedHeight(tag_btn_h)
                btn.setStyleSheet(f"QPushButton {{ background-color: #2a3a4a; color: #5294e2; border: 1px solid #3a5a7a; border-radius: {int(10 * scale)}px; padding: {int(1 * scale)}px {int(8 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ background-color: #3a1a1a; color: #e06060; }}")
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                def make_handler(tag):
                    def handler():
                        if tag in tags_data:
                            tags_data.remove(tag)
                            QtCore.QTimer.singleShot(0, rebuild)
                    return handler
                btn.clicked.connect(make_handler(t))
                tags_l.addWidget(btn)
        rebuild()
        lyt.addWidget(tags_w)

        add_btn = QtWidgets.QPushButton("+")
        add_btn.setFixedSize(int(22 * scale), int(22 * scale))
        add_btn.setStyleSheet(f"QPushButton {{ background-color: #3a3a3a; color: #5294e2; border: 1px solid #4a4a4a; border-radius: {int(11 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ background-color: #4a4a4a; }}")
        def add_tag():
            t, ok = QtWidgets.QInputDialog.getText(dlg, "添加标签", "新标签:")
            if ok and t.strip() and t.strip() not in tags_data:
                tags_data.append(t.strip()); rebuild()
        add_btn.clicked.connect(add_tag)
        lyt.addWidget(add_btn)

        common_tags = self._material_manager.get_common_tags(self._current_tag_type()) if not self._use_mock else [
            "金属", "pbr", "布料", "玻璃", "木材"]
        lyt.addWidget(QtWidgets.QLabel(f'<span style="{label_font}">\u5e38\u7528\u6807\u7b7e</span>'))
        common_w = QtWidgets.QWidget()
        common_l = FlowLayout(common_w, margin=0, spacing=int(3 * scale))
        for ct in common_tags:
            if ct not in tags_data:
                cbtn = QtWidgets.QPushButton(ct)
                cbtn.setFixedHeight(tag_btn_h)
                cbtn.setStyleSheet(f"QPushButton {{ background-color: #333; color: #888; border: 1px solid #444; border-radius: {int(10 * scale)}px; padding: {int(1 * scale)}px {int(8 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ background-color: #2d4a6f; color: #5294e2; border-color: #5294e2; }}")
                cbtn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                def add_common(tx):
                    if tx not in tags_data:
                        tags_data.append(tx)
                        QtCore.QTimer.singleShot(0, rebuild)
                cbtn.clicked.connect(lambda checked=False, tx=ct: add_common(tx))
                common_l.addWidget(cbtn)
        lyt.addWidget(common_w)

        # ── 注释 ──
        lyt.addWidget(QtWidgets.QLabel(f'<span style="{label_font}">\u6ce8\u91ca</span>'))
        notes_edit = QtWidgets.QPlainTextEdit()
        notes_edit.setPlaceholderText("\u5907\u6ce8\u4fe1\u606f\uff0c\u652f\u6301\u591a\u884c\u6587\u672c...")
        notes_edit.setPlainText(mat_dict.get("notes", ""))
        notes_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: #333; border: 1px solid #4a4a4a; "
            f"border-radius: {int(3 * scale)}px; padding: {int(6 * scale)}px; "
            f"color: #e0e0e0; font-size: {fs}px; }}"
        )
        notes_edit.setFixedHeight(int(80 * scale))
        lyt.addWidget(notes_edit)

        lyt.addStretch()
        br = QtWidgets.QHBoxLayout(); br.addStretch()
        c = QtWidgets.QPushButton("\u53d6\u6d88")
        c.setStyleSheet(f"QPushButton {{ background-color: #3a3a3a; color: #a0a0a0; border: none; padding: {btn_pad}px {int(16 * scale)}px; border-radius: {int(3 * scale)}px; font-size: {fs}px; }} QPushButton:hover {{ color: #e0e0e0; }}")
        c.clicked.connect(dlg.reject); br.addWidget(c)
        ok = QtWidgets.QPushButton("\u4fdd\u5b58")
        ok.setStyleSheet(f"QPushButton {{ background-color: #5294e2; color: #fff; border: none; padding: {btn_pad}px {int(16 * scale)}px; border-radius: {int(3 * scale)}px; font-size: {fs}px; }}")
        ok.clicked.connect(dlg.accept); br.addWidget(ok)
        lyt.addLayout(br)

        apply_font_size_to_widget(dlg, fs)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted: return
        mat_dict["name_cn"] = cn.text().strip()
        mat_dict["tags"] = list(tags_data)
        mat_dict["category"] = cat_combo.currentData()
        mat_dict["notes"] = notes_edit.toPlainText()
        self._on_edit_material(mat_dict)

    def _on_add_to_favorite(self, material_id, coll_id=""):
        """右键 → 添加到收藏夹 → 子菜单已直接处理，同步 UI"""
        if self._use_mock or not self._material_manager:
            return
        is_in_any = any(material_id in s for s in self._active_mgr._favorites.values())
        self._sync_fav_ui_state(material_id, is_in_any)
        self._sync_favorites_panel()

    def _sync_fav_ui_state(self, material_id, is_fav):
        """同步收藏星标到缩略图网格和右侧面板"""
        for lst in [self._thumbnail_grid._materials, self._thumbnail_grid._filtered_materials]:
            for m in lst:
                if m.get("id") == material_id:
                    m["_favorited"] = is_fav
        if self._right_panel._material and self._right_panel._material.get("id") == material_id:
            self._right_panel._material["_favorited"] = is_fav
            self._right_panel._update_fav_btn()
        # 同步更新已存在的卡片星标 UI，避免不重建卡片时星标不同步
        self._thumbnail_grid._refresh_card_favorites()

    def _sync_favorites_panel(self):
        """从当前激活管理器同步收藏数据到收藏夹面板"""
        if self._use_mock:
            return
        mgr = self._active_mgr
        # 保留现有收藏夹的元数据（name, icon）
        existing = {c["id"]: c for c in getattr(self._favorites_panel, '_collections', [])}
        new_collections = []
        for coll_id, mat_ids in mgr._favorites.items():
            name = mgr._favorites_meta.get(coll_id, coll_id)
            if coll_id in existing:
                c = existing[coll_id]
                c["name"] = name if name != coll_id else c.get("name", coll_id)
                c["materials"] = list(mat_ids)
                new_collections.append(c)
            else:
                new_collections.append({
                    "id": coll_id, "name": name,
                    "icon": "★" if coll_id == "default" else "📁",
                    "materials": list(mat_ids),
                })
        if not new_collections:
            new_collections = [{"id": "default", "name": "默认收藏夹", "icon": "★", "materials": []}]
        self._favorites_panel._collections = new_collections
        self._favorites_panel._refresh()

    def _on_fav_collection_selected(self, coll_id, material_ids):
        self._current_fav_collection = coll_id
        if self._use_mock:
            self._thumbnail_grid.filter_by_favorites(set(material_ids))
        else:
            favs = self._active_mgr.get_favorites(coll_id)
            if not favs:
                other = self._project_mgr if self._active_mgr is self._material_manager else self._material_manager
                favs = other.get_favorites(coll_id)
            self._thumbnail_grid.set_materials([m.to_dict() for m in favs])

    def _on_fav_collection_added(self, coll_id, name=""):
        """新建收藏夹 → 同步到当前激活管理器"""
        if not self._use_mock:
            if coll_id not in self._active_mgr._favorites:
                self._active_mgr._favorites[coll_id] = set()
            if name:
                self._active_mgr._favorites_meta[coll_id] = name
            self._active_mgr.save_favorites()
        self._sync_favorites_panel()

    def _on_fav_collection_deleted(self, coll_id):
        """删除收藏夹 → 从当前激活管理器移除并保存"""
        if not self._use_mock and coll_id != "default":
            self._active_mgr._favorites.pop(coll_id, None)
            self._active_mgr._favorites_meta.pop(coll_id, None)
            self._active_mgr.save_favorites()
            self._sync_favorites_panel()

    def _on_fav_drop_material(self, mat_id, coll_id):
        """拖放材质到收藏夹。Ctrl+拖放=复制，普通拖放=移动到该收藏夹"""
        if self._use_mock or not mat_id:
            return
        ctrl = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ControlModifier)
        mgr = self._active_mgr
        if coll_id not in mgr._favorites:
            mgr._favorites[coll_id] = set()
        mgr._favorites[coll_id].add(mat_id)
        if not ctrl:
            for cid, mids in mgr._favorites.items():
                if cid != coll_id:
                    mids.discard(mat_id)
        is_in_any = any(mat_id in s for s in mgr._favorites.values())
        self._sync_fav_ui_state(mat_id, is_in_any)
        self._sync_favorites_panel()
        mgr.save_favorites()

    def _on_tag_filter_from_detail(self, tags):
        lower_tags = [t.lower() for t in tags]
        if lower_tags:
            if self._use_mock:
                self._thumbnail_grid.filter_by_tags(lower_tags)
            else:
                self._dict_mode_search_and_set(tags=lower_tags)
        else:
            if self._use_mock:
                self._thumbnail_grid.filter_by_tags([])
            else:
                self._dict_mode_search_and_set(tags=[])
        self._search_bar.set_active_tags(lower_tags)

    def _on_thumb_slider_changed(self, value):
        """滑块值反向：向左=小卡片多列，向右=大卡片少列"""
        self._thumbnail_grid._columns = max(2, min(8, 10 - value))
        self._thumbnail_grid._auto_columns()
        if self._thumbnail_grid._view_mode == self._thumbnail_grid.VIEW_ICON:
            self._thumbnail_grid._zoom_timer.start()

    def _on_grid_thumb_changed(self, column_count):
        """鼠标滚轮缩放 → 同步滑块（反向：列数转滑块值）"""
        self._thumb_slider.blockSignals(True)
        self._thumb_slider.setValue(10 - column_count)
        self._thumb_slider.blockSignals(False)

    def _on_sort_changed(self, index):
        sort_keys = ["name_cn", "-name_cn", "node_type", "category", "file_mtime", "-file_mtime"]
        if index < len(sort_keys):
            self._thumbnail_grid.set_sort_key(sort_keys[index])

    def _on_panel_toggle(self):
        self._left_panel_visible = not self._left_panel_visible
        self._left_panel_container.setVisible(self._left_panel_visible)

    @handle_errors(context="刷新材质库")
    def _on_refresh(self):
        if self._use_mock:
            return
        self._material_manager.reload()
        self._refresh_keep_current()
        self._update_status_bar()
        print("[MaterialLibrary] 材质库已刷新")

    # ── 多库切换 ─────────────────────────────────────
    def _populate_library_combo(self):
        """填充库切换下拉框"""
        self._lib_combo.blockSignals(True)
        self._lib_combo.clear()
        for lib in self._libraries:
            name = lib.get("name", os.path.basename(lib["path"]))
            path = lib["path"]
            self._lib_combo.addItem(f"{name} — {path}", lib["path"])
        self._lib_combo.blockSignals(False)

    def _on_library_switched(self, index):
        """切换资产库"""
        if index < 0 or index >= len(self._libraries):
            return
        lib = self._libraries[index]
        path = lib["path"]
        if not os.path.isdir(path):
            QtWidgets.QMessageBox.warning(self, "路径无效", f"资产库路径不存在: {path}")
            return
        self._material_manager.load_library(path)
        self._active_mgr = self._material_manager
        self._thumbnail_grid.set_manager(self._material_manager)
        self._refresh_category_tree()
        self._refresh_material_grid("all")
        self._update_status_bar()
        print(f"[MaterialLibrary] 已切换资产库: {path}")

    def _on_left_tab_changed(self, index):
        """左侧选项卡切换"""
        if index == 0:  # 分类选项卡
            self._active_mgr = self._material_manager
            self._thumbnail_grid.set_manager(self._material_manager)
        if index == 2:  # 项目选项卡
            self._load_project_library()
            self._active_mgr = self._project_mgr
            self._thumbnail_grid.set_manager(self._project_mgr)
        if index == 1:  # 收藏选项卡 → 同步
            self._sync_favorites_panel()
        self._center_stack.setCurrentIndex(0)

    def _get_project_library_path(self):
        """获取项目库路径"""
        try:
            import maya.cmds as cmds
            proj = cmds.workspace(query=True, rootDirectory=True)
        except (ImportError, Exception):
            proj = os.getcwd()
        return os.path.join(proj, "SquirrelLib")

    def _on_create_project_lib(self):
        """创建简化的资产文件夹（仅根目录，不含子分类）"""
        lib = self._get_project_library_path()
        os.makedirs(lib, exist_ok=True)
        # 先写 library.json，避免后续 load_library 自动创建子分类
        self._project_mgr._json_handler.write_json(os.path.join(lib, "library.json"), {
            "version": "2.0", "name": "SquirrelLib",
            "sub_libraries": list(self._project_mgr.ASSET_SUB_LIBRARIES.keys()),
        })
        for sub_dir, sub_name in self._project_mgr.ASSET_SUB_LIBRARIES.items():
            sub_path = os.path.join(lib, sub_dir)
            if not os.path.isdir(sub_path):
                os.makedirs(sub_path, exist_ok=True)
            self._project_mgr._ensure_folder_meta(sub_path, sub_name)
            root_meta = self._project_mgr._read_folder_meta(sub_path)
            if "type" not in root_meta:
                root_meta["type"] = sub_dir
                self._project_mgr._write_folder_meta(sub_path, root_meta)
        self._on_refresh_project()

    def _load_project_library(self):
        """加载项目库文件夹树"""
        if self._project_loaded:
            return
        lib_path = self._get_project_library_path()
        self._project_mgr.load_library(lib_path)
        tree = self._project_mgr.get_category_tree()
        self._proj_category_tree.set_categories(tree)
        self._project_loaded = True
        self._active_mgr = self._project_mgr
        self._thumbnail_grid.set_manager(self._project_mgr)
        self._proj_status.setText(lib_path)

    def _on_refresh_project(self):
        cur = self._proj_category_tree.get_active_category()
        self._project_loaded = False
        self._project_mgr = MaterialManager()
        self._load_project_library()
        self._thumbnail_grid.set_manager(self._project_mgr)
        # 恢复选中分类
        if cur and cur != "all":
            self._proj_category_tree._select_by_id(cur)
            self._proj_category_tree._active_category = cur
            desc_ids = self._proj_category_tree.get_descendant_ids(cur)
            root_lib = cur
            # 复合 ID → 提取 root_lib
            if "||" in cur:
                root_lib, _ = split_cat_id(cur)
            parent_cat, parent_node = self._proj_category_tree._find_category(cur)
            if parent_node:
                root_lib = parent_node["id"]
            self._on_proj_category_selected(cur, desc_ids, root_lib)
        QtCore.QTimer.singleShot(300, self._check_duplicate_uuids)

    # ── 项目分类操作 ─────────────────────────────

    def _on_proj_category_selected(self, category_id, descendant_ids, root_lib="materials"):
        """项目树选中分类 → 设置管理器后委托资产库处理器"""
        self._active_mgr = self._project_mgr
        self._on_category_selected(category_id, descendant_ids, root_lib)

    def _on_proj_categories_multi_selected(self, cat_ids, all_desc_ids, root_lib="materials"):
        """项目树多选分类 → 设置管理器后委托资产库处理器"""
        self._active_mgr = self._project_mgr
        self._on_categories_multi_selected(cat_ids, all_desc_ids, root_lib)

    def _on_proj_category_added(self, cat_dict):
        """项目库创建分类 → 委托资产库处理器"""
        self._active_mgr = self._project_mgr
        self._on_category_added(cat_dict)

    def _on_proj_add_top_level_category(self, cat_id, name_cn, root_lib):
        """项目库：右键子库根节点添加顶级分类 → 委托"""
        self._active_mgr = self._project_mgr
        self._on_add_top_level_category(cat_id, name_cn, root_lib)

    def _on_proj_category_edited(self, cat_id, new_name_cn):
        """项目库编辑分类易读名 → 委托"""
        self._active_mgr = self._project_mgr
        self._on_category_edited(cat_id, new_name_cn)

    def _on_proj_category_deleted(self, cat_id):
        """项目库删除分类 → 委托"""
        self._active_mgr = self._project_mgr
        self._on_category_deleted(cat_id)

    def _on_proj_open_folder(self, cat_id):
        """打开项目库文件夹 → 委托"""
        self._active_mgr = self._project_mgr
        self._on_open_category_folder(cat_id)

    def _find_folder_in_lib(self, mgr, cat_id) -> str:
        """在指定管理器的库路径中查找文件夹"""
        lib = mgr.get_library_path()
        if not lib:
            return ""
        for root, dirs, _ in os.walk(lib):
            for d in dirs:
                if d == cat_id:
                    return os.path.join(root, d)
        return ""

    def _update_status_bar(self):
        if self._use_mock:
            total = len(MOCK_MATERIALS)
        else:
            total = self._material_manager.get_material_count()
        visible = self._thumbnail_grid.get_visible_count()
        sel_count = self._thumbnail_grid.get_selection_count()
        msg = f"材料: {visible}/{total}"
        if sel_count > 0:
            msg += f"  |  已选: {sel_count}"
        self._status_count.setText(msg)

    def _update_batch_action_bar(self):
        """选中变化时更新批量操作栏"""
        sel_mats = self._thumbnail_grid.get_selected_materials_list()
        sel_count = len(sel_mats)
        self._batch_action_bar.show_with_count(sel_count, sel_mats)
        self._batch_action_bar.setVisible(sel_count > 1)

    def _get_selected_materials_for_batch(self):
        """获取当前选中的材质字典列表（用于批量操作）"""
        return self._thumbnail_grid.get_selected_materials_list()

    def _on_batch_rename(self, materials):
        """批量重命名"""
        from .batch_rename_dialog import BatchRenameDialog
        dlg = BatchRenameDialog(self, materials)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            results = dlg.get_rename_results()
            target_field = dlg.get_rename_target()
            if not results:
                return
            mgr = self._active_mgr
            success = 0
            failed = 0
            for old_name, new_name, mat_dict in results:
                mid = mat_dict.get("id", "")
                if mid:
                    try:
                        mgr.update_material(mid, {target_field: new_name})
                        success += 1
                    except Exception as e:
                        failed += 1
                        print(f"[BatchRename] 失败: {mid} → {e}")
            mgr.reload()
            self._refresh_keep_current()
            msg = f"批量重命名完成：成功 {success} 个"
            if failed:
                msg += f"，失败 {failed} 个"
            QtWidgets.QMessageBox.information(self, "批量重命名", msg)

    def _on_batch_tag(self, materials):
        """批量标签管理"""
        # 从材料列表推断当前子库，获取对应的常用标签
        mgr = self._active_mgr
        sub_lib = "materials"
        if materials:
            # 优先用材料的子库字段
            sub_lib = materials[0].get("sub_library", "")
            if not sub_lib:
                # 从 json_path 推断
                jp = materials[0].get("json_path", "")
                if jp:
                    rel = os.path.relpath(jp, mgr.get_library_path())
                    parts = rel.split(os.sep)
                    if parts:
                        sub_lib = parts[0]
        common_tags = mgr._common_tags.get(sub_lib, [])
        from .batch_tag_dialog import BatchTagDialog
        dlg = BatchTagDialog(self, materials, common_tags=common_tags)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            op = dlg.get_tag_operation()
            if not op:
                return
            mode = op["mode"]
            tags = op["tags"]
            if not tags and mode != "overwrite":
                return
            success = 0
            failed = 0
            for mat_dict in materials:
                mid = mat_dict.get("id", "")
                if not mid:
                    continue
                try:
                    mat = mgr.get_by_id(mid)
                    if not mat:
                        failed += 1
                        continue
                    existing_tags = list(mat.tags)
                    if mode == "add":
                        new_tags = existing_tags + [t for t in tags if t.lower() not in [x.lower() for x in existing_tags]]
                    elif mode == "remove":
                        remove_lower = [t.lower() for t in tags]
                        new_tags = [t for t in existing_tags if t.lower() not in remove_lower]
                    else:  # overwrite
                        new_tags = list(tags)
                    mgr.update_material(mid, {"tags": new_tags})
                    success += 1
                except Exception as e:
                    failed += 1
                    print(f"[BatchTag] 失败: {mid} → {e}")
            mgr.reload()
            self._refresh_keep_current()
            msg = f"批量标签操作完成：成功 {success} 个"
            if failed:
                msg += f"，失败 {failed} 个"
            QtWidgets.QMessageBox.information(self, "批量标签", msg)

    def _on_batch_move(self, materials):
        """批量移动分类 — 复用已有的移动逻辑"""
        # 取第一个材质的子库作为推荐子库
        if not materials:
            return
        mgr = self._active_mgr
        first = materials[0]
        mid = first.get("id", "")
        if not mid:
            return
        mat = mgr.get_by_id(mid)
        if not mat or not mat.json_path:
            return

        # 从路径推断子库
        lib = mgr.get_library_path()
        rel_path = os.path.relpath(mat.json_path, lib)
        sub_lib = rel_path.split(os.sep)[0] if os.sep in rel_path else "materials"

        # 获取分类树
        tree = mgr.get_category_tree()
        cats = []
        cat_ids = []

        def collect(node, depth=0):
            if node["id"] == "all":
                return
            if depth == 0 and node["id"] != sub_lib:
                return
            if depth == 0:
                for child in node.get("children", []):
                    collect(child, depth + 1)
                return
            prefix = "  " * (depth - 1)
            display_name = node.get("name_cn") or node.get("name") or node["id"]
            cats.append(prefix + display_name)
            cat_ids.append(node["id"])
            for child in node.get("children", []):
                collect(child, depth + 1)

        for sub_node in tree:
            collect(sub_node)

        if not cats:
            return

        name, ok = QtWidgets.QInputDialog.getItem(
            self, f"批量移动 ({len(materials)} 个资产)", "选择目标分类:", cats, 0, False)
        if not ok:
            return
        idx = cats.index(name)
        target = cat_ids[idx] or "custom"

        # 逐个移动
        for mat_dict in materials:
            mid = mat_dict.get("id", "")
            if mid:
                try:
                    mgr.move_material_to_category(mid, target, sub_lib=sub_lib)
                except Exception as e:
                    print(f"[BatchMove] 失败: {mid} → {e}")

        mgr.reload()
        self._refresh_keep_current()

    def _on_batch_copy(self, materials):
        """批量复制 — 选择目标分类后逐个复制"""
        if not materials:
            return
        mgr = self._active_mgr
        first = materials[0]
        mid = first.get("id", "")
        if not mid:
            return
        mat = mgr.get_by_id(mid)
        if not mat or not mat.json_path:
            return

        lib = mgr.get_library_path()
        rel_path = os.path.relpath(mat.json_path, lib)
        sub_lib = rel_path.split(os.sep)[0] if os.sep in rel_path else "materials"

        tree = mgr.get_category_tree()
        cats, cat_ids = [], []

        def collect(node, depth=0):
            if node["id"] == "all":
                return
            if depth == 0 and node["id"] != sub_lib:
                return
            if depth == 0:
                for child in node.get("children", []):
                    collect(child, depth + 1)
                return
            prefix = "  " * (depth - 1)
            display_name = node.get("name_cn") or node.get("name") or node["id"]
            cats.append(prefix + display_name)
            cat_ids.append(node["id"])
            for child in node.get("children", []):
                collect(child, depth + 1)

        for sub_node in tree:
            collect(sub_node)
        if not cats:
            return

        name, ok = QtWidgets.QInputDialog.getItem(
            self, f"批量复制 ({len(materials)} 个资产)", "选择目标分类:", cats, 0, False)
        if not ok:
            return
        idx = cats.index(name)
        target = cat_ids[idx] or "custom"

        for mat_dict in materials:
            mid = mat_dict.get("id", "")
            if mid:
                try:
                    mgr.copy_material_to_category(mid, target, sub_lib=sub_lib)
                except Exception as e:
                    print(f"[BatchCopy] 失败: {mid} → {e}")

        mgr.reload()
        self._refresh_keep_current()

    def _on_batch_delete(self, materials):
        """批量删除 — 委托给已有的 _on_grid_delete"""
        ids = [m.get("id", "") for m in materials if m.get("id")]
        if ids:
            self._on_grid_delete(ids)

    def _on_batch_clear_selection(self):
        """清除选中"""
        self._thumbnail_grid.clear_selection()

    # ── 导入 / 创建预设 ──────────────────────────────

    def _on_grid_import(self, mode):
        if mode == "zasset":
            self._on_import_zasset_folder()
        elif mode == "textures":
            self._on_import_textures()
        elif mode == "hdr":
            self._on_import_hdr()
        else:
            self._on_import_files()

    def _on_drag_dropped(self, material_ids, global_x, global_y):
        """拖拽结束后触发 — 有选中物体则赋予材质，否则仅创建"""
        import maya.cmds as cmds

        try:
            mgr = self._active_mgr
            if not mgr:
                return

            mats = []
            for mid in material_ids:
                mat = mgr.get_by_id(mid)
                if mat:
                    mats.append(mat)
            if not mats:
                return

            # 多选 → 全部导入，用第一次选择的格式
            if len(mats) > 1:
                first_mat = mats[0]
                first_fmt = None
                for mat in mats:
                    fmt = self._import_asset_to_scene(mat, preset_format=first_fmt)
                    if first_fmt is None and fmt:
                        first_fmt = fmt
                return

            mat = mats[0]
            sub_lib = mat.sub_library
            json_path = mat.json_path or ""

            if sub_lib in ("materials", "textures") and json_path.endswith(".zasset"):
                saved_sel = cmds.ls(sl=True, long=True) or []
                from ..integration.import_executor import apply_zmetal_as_material
                apply_zmetal_as_material(json_path)
                if saved_sel:
                    self._assign_created_material(json_path, saved_sel)
                    cmds.select(saved_sel, replace=True)
            elif sub_lib == "hdr" and json_path.endswith(".zasset"):
                preset_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'Assets', 'HDR_ligt')
                if os.path.isdir(preset_dir):
                    ma_files = sorted([f for f in os.listdir(preset_dir) if f.lower().endswith('.ma')])
                    if ma_files:
                        names = [os.path.splitext(f)[0] for f in ma_files]
                        chosen, ok = QtWidgets.QInputDialog.getItem(
                            self, "选择环境光类型", "请选择要创建的穹顶灯类型:",
                            names, 0, False)
                        if ok and chosen:
                            idx = names.index(chosen)
                            preset_path = os.path.join(preset_dir, ma_files[idx])
                            mat_dict = {"json_path": json_path, "name_cn": mat.name_cn, "id": mat.id}
                            self._on_create_dome_light(preset_path, mat_dict)
                    else:
                        print(f"[DragDrop] HDR_ligt 文件夹为空")
                else:
                    print(f"[DragDrop] HDR_ligt 文件夹不存在")
            else:
                self._import_asset_to_scene(mat)

        except Exception as e:
            print(f"[DragDrop] 处理失败: {e}")

    def _choose_format_dialog(self, formats):
        """弹出格式选择对话框，返回选中格式"""
        if not formats:
            return ""
        if len(formats) == 1:
            return formats[0]
        item, ok = QtWidgets.QInputDialog.getItem(
            self, "选择导入格式", "该资产有多个格式，请选择：", formats, 0, False)
        return item if ok else ""

    def _import_asset_to_scene(self, mat, preset_format=None):
        """将资产导入到 Maya 场景（多格式时弹窗选择，多选继承第一次选择）

        Returns:
            实际使用的格式名，未选择返回空字符串
        """
        from ..integration.import_executor import import_asset
        json_path = mat.json_path or ""
        if not json_path.endswith(".zasset"):
            return ""
        from ..integration.import_executor import get_available_formats
        formats = get_available_formats(json_path)
        if not formats:
            return ""
        fmt = preset_format or self._choose_format_dialog(formats)
        if not fmt:
            return ""
        import_asset(json_path, fmt)
        return fmt

    def _update_preview_thumbnail(self, mat, pixmap):
        """直接更新预览面板的缩略图，绕过 _draw_preview 的全量重建。"""
        panel = self._right_panel
        s = min(panel._preview_frame.width() - 16, panel._preview_frame.height() - 16)
        if s < 80:
            s = 160
        panel._preview_label.setFixedSize(s, s)
        scaled = pixmap.scaled(s, s, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                               QtCore.Qt.TransformationMode.SmoothTransformation)
        result = QtGui.QPixmap(s, s)
        result.fill(QtGui.QColor("#1a1a1a"))
        painter = QtGui.QPainter(result)
        x = (s - scaled.width()) // 2
        y = (s - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        panel._preview_label.setPixmap(result)
        panel._preview_label.update()  # 强制重绘
        panel._update_thumb_buttons()
        QtWidgets.QApplication.processEvents()  # 刷新事件循环

    def _assign_created_material(self, json_path, target_objects):
        """查找刚创建的材质并赋予目标物体"""
        import maya.cmds as cmds
        import json
        from ..core.zasset_io import ZassetIO

        root_names = []
        all_names = ZassetIO.list_contents(json_path)
        zn = "node.zmetal"
        if zn not in all_names:
            zns = [n for n in all_names if n.endswith(".zmetal")]
            zn = zns[0] if zns else ""
        if zn:
            data = json.loads(ZassetIO.read_file(json_path, zn))
            root_names = data.get("root_materials", [])

        for rn in root_names:
            if cmds.objExists(rn):
                cmds.select(target_objects, replace=True)
                cmds.hyperShade(assign=rn)
                print(f"[DragDrop] 赋予材质 {rn} → {target_objects[0]}")
                return

        after = cmds.ls(type="shadingDependNode")
        for n in reversed(after):
            ntype = cmds.nodeType(n)
            if ntype not in ("place2dTexture", "file", "bump2d", "layeredTexture"):
                cmds.select(target_objects, replace=True)
                cmds.hyperShade(assign=n)
                print(f"[DragDrop] 赋予材质(回退) {n} ({ntype}) → {target_objects[0]}")
                return

    def _load_json_presets(self, key):
        """从 config.json 读取预设列表"""
        import os, json as _json
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "preset", "config.json")
        try:
            with open(cfg_path, 'r') as f:
                return _json.load(f).get(key, [])
        except Exception:
            return []

    def _on_asset_import_into_maya(self, zasset_path, format_name):
        """右键 → 导入 → 将资产导入到 Maya 场景"""
        from ..integration.import_executor import import_asset
        print(f"[Import] 导入到场景: {os.path.basename(zasset_path)} / {format_name}")
        import_asset(zasset_path, format_name)

    def _on_variant_geometry_import(self, zasset_path, version, lod):
        """右键 → 导入几何体 → 导入指定变体到 Maya 场景"""
        from ..integration.import_executor import import_variant_geometry
        print(f"[VariantImport] 导入变体: {os.path.basename(zasset_path)} version={version} lod={lod}")
        import_variant_geometry(zasset_path, version=version, lod=lod)

    def _on_variant_material_import(self, zasset_path, version):
        """右键 → 导入版本材质 → 导入变体版本的材质到 Maya"""
        from ..integration.import_executor import import_variant_material
        print(f"[VariantMaterial] 导入版本材质: {os.path.basename(zasset_path)} version={version}")
        success = import_variant_material(zasset_path, version=version)
        if not success:
            QtWidgets.QMessageBox.warning(
                self, "导入材质失败",
                f"无法导入版本 {version} 的材质。\n\n"
                f"请确认该版本包含独立的材质文件。")

    def _on_variant_version_delete(self, zasset_path, version_id):
        """右键 → 删除版本"""
        reply = QtWidgets.QMessageBox.question(
            self, "删除版本",
            f"确定要删除版本「{version_id}」及其所有 LOD 和材质吗？\n\n此操作不可撤销。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return
        from ..core.zasset_builder import ZassetBuilder
        ok = ZassetBuilder.remove_variant_version(zasset_path, version_id)
        if ok:
            self._on_refresh()
            print(f"[VariantDelete] 已删除版本: {version_id} from {zasset_path}")
        else:
            QtWidgets.QMessageBox.warning(self, "删除失败", f"无法删除版本「{version_id}」")

    def _on_variant_lod_delete(self, zasset_path, version_id, lod_id):
        """右键 → 删除 LOD"""
        reply = QtWidgets.QMessageBox.question(
            self, "删除 LOD",
            f"确定要删除版本「{version_id}」的「{lod_id}」吗？\n\n"
            f"此操作仅删除该 LOD 精度，版本和其他 LOD 不受影响。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return
        from ..core.zasset_builder import ZassetBuilder
        ok = ZassetBuilder.remove_variant_lod(zasset_path, version_id, lod_id)
        if ok:
            self._on_refresh()
            print(f"[LodDelete] 已删除 LOD: {version_id}/{lod_id} from {zasset_path}")
        else:
            QtWidgets.QMessageBox.warning(self, "删除失败", f"无法删除「{version_id}/{lod_id}」")

    def _on_import_single_texture(self, zasset_path, texture_name):
        """右键 → 导入贴图 → 导入单个贴图文件到 Maya"""
        from ..integration.texture_importer import import_texture_by_name
        print(f"[Import] 导入贴图: {texture_name} from {os.path.basename(zasset_path)}")
        import_texture_by_name(zasset_path, texture_name)

    def _on_import_textures_shared_uv(self, zasset_path, texture_names):
        """右键 → 导入贴图 → 导入全部：批量导入，共享 place2dTexture 节点"""
        from ..integration.texture_importer import import_textures_shared_uv
        print(f"[Import] 批量导入贴图: {len(texture_names)} 张 from {os.path.basename(zasset_path)}")
        result = import_textures_shared_uv(zasset_path, texture_names)
        if result:
            file_nodes, p2d = result
            print(f"[Import] 批量导入完成: {len(file_nodes)} 个 file 节点, 共享 {p2d}")

    def _on_assign_texture_to_material(self, texture_material):
        """贴图 → 指定贴图：将贴图资产的贴图指定给选中的材质节点"""
        import maya.cmds as cmds
        import json, os
        from ..core.zasset_io import ZassetIO

        sel = cmds.ls(sl=True, long=True) or []
        if not sel:
            cmds.warning("请先选中一个材质节点")
            print("[AssignTex] 未选中任何对象，请先在 Hypershade/视口中选中一个材质节点")
            return

        json_path = texture_material.get("json_path", "")
        if not json_path or not os.path.isdir(json_path):
            print(f"[AssignTex] 贴图资产路径无效: {json_path}")
            cmds.warning(f"贴图资产路径无效")
            return

        try:
            from ..quicktools.pbr_to_zasset import create_material

            meta = ZassetIO.read_meta(json_path)
            properties = meta.get("properties", {}) if meta else {}
            asset_name = meta.get("name") or meta.get("name_cn") or "" if meta else ""
            asset_id = meta.get("id", "") if meta else ""

            # 提取贴图到磁盘
            tex_files = [n for n in ZassetIO.list_contents(json_path)
                         if n.startswith("textures/") and not n.endswith("/")]
            default_res = meta.get("default_resolution", "") if meta else ""
            if default_res:
                tex_files = [n for n in tex_files
                             if n.startswith(f"textures/{default_res}/")]

            suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
            try:
                ws_root = cmds.workspace(q=True, rd=True) or ""
                si_rule = cmds.workspace(fileRuleEntry="sourceImages")
                base = os.path.join(ws_root, si_rule) if si_rule else ws_root
                base = os.path.normpath(base)
            except Exception:
                base = os.path.normpath(os.path.join(
                    os.path.expanduser("~/Documents/maya/projects/default"),
                    "sourceimages"))
            target_dir = os.path.join(base, "squirrel_asset", f"{asset_name}{suffix}")
            os.makedirs(target_dir, exist_ok=True)

            tex_path_map = {}
            for name in tex_files:
                rel = name[len("textures/"):]
                target_path = os.path.join(target_dir, rel).replace("\\", "/")
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                data = ZassetIO.read_file(json_path, name)
                if os.path.isfile(target_path):
                    if os.path.getsize(target_path) == len(data):
                        tex_path_map[name] = target_path
                        continue
                with open(target_path, 'wb') as f:
                    f.write(data)
                tex_path_map[name] = target_path

            import re as _re_assign
            _RES_PAT = _re_assign.compile(r'_\d+[kK](?=_|\.)', _re_assign.IGNORECASE)
            def _norm(n):
                return _RES_PAT.sub('', n)

            fname_to_disk = {}
            for zip_rel, disk_path in tex_path_map.items():
                fname = os.path.basename(zip_rel.replace("\\", "/"))
                fname_to_disk[fname] = disk_path
                fname_to_disk[fname.lower()] = disk_path
                fname_to_disk[_norm(fname)] = disk_path
                fname_to_disk[_norm(fname).lower()] = disk_path

            def _find(fp):
                for c in (fp, fp.lower()):
                    if c in fname_to_disk: return fname_to_disk[c]
                b = os.path.basename(fp.replace("\\", "/"))
                for c in (b, b.lower()):
                    if c in fname_to_disk: return fname_to_disk[c]
                    n = _norm(c)
                    if n in fname_to_disk: return fname_to_disk[n]
                for k, v in fname_to_disk.items():
                    if k.lower() in (fp.lower(), b.lower()): return v
                    if _norm(k).lower() == _norm(b).lower(): return v
                return None

            # 加载 pbr_mapping.json
            mapping_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'Assets', 'preset', 'pbr_mapping.json')
            pbr_config = {}
            with open(mapping_path, 'r', encoding='utf-8') as f:
                pbr_config = json.load(f)

            # 色彩空间映射
            color_space_map = {}
            type_rules = pbr_config.get('texture_type_rules', {})
            for t, rule in type_rules.items():
                color_space_map[t] = rule.get('color_space', 'sRGB')
                if rule.get('is_combo'):
                    for ut in rule.get('channels', {}).values():
                        if isinstance(ut, dict):
                            u = ut.get('usage', '')
                            if u: color_space_map[u] = rule.get('color_space', 'Raw')
                        elif isinstance(ut, str):
                            color_space_map[ut] = rule.get('color_space', 'Raw')

            textures = {}
            for tex_type, info in properties.items():
                if info.get('type') != 'texture': continue
                p = info.get('path', '')
                if not p: continue
                dp = _find(p)
                if not dp: continue
                base = tex_type
                if base.startswith('normal_'): base = 'normal'
                elif base.startswith('height_'): base = 'height'
                cs = color_space_map.get(base, color_space_map.get(tex_type, 'sRGB'))
                textures[tex_type] = {'full_path': dp, 'color_space': cs}

            # 对每个选中的材质节点，连接所有贴图
            pbr_config['default_material_type'] = 'openPBRSurface'
            for obj in sel:
                ntype = cmds.nodeType(obj)
                pbr_config['default_material_type'] = ntype
                shader, result = create_material(
                    f"{asset_name}{suffix}", textures, pbr_config,
                    existing_shader=obj)
                if shader:
                    print(f"[AssignTex] 贴图已指定到 {shader} ({ntype}), 贴图数: {len(textures)}")
                else:
                    print(f"[AssignTex] 指定失败: {result}")

        except Exception as e:
            print(f"[AssignTex] 指定贴图失败: {e}")
            import traceback
            traceback.print_exc()

    def _on_create_material(self, node_type, material, resolution=''):
        """贴图 → 创建指定类型的材质节点并自动连接贴图（委托 pbr_to_zasset.create_material）
        
        Args:
            node_type: Maya 节点类型（如 openPBRSurface）
            material: 材质数据字典
            resolution: 精度选择（如 '2K'），空字符串表示使用默认精度
        """
        import maya.cmds as cmds
        import json, os
        from ..core.zasset_io import ZassetIO
        from ..quicktools.pbr_to_zasset import create_material

        json_path = material.get("json_path", "")
        if not json_path or not json_path.endswith(".zasset"):
            try:
                node = cmds.shadingNode(node_type, asShader=True)
                cmds.select(node, replace=True)
                print(f"[MaterialLibrary] 创建材质: {node} ({node_type})")
            except Exception as e:
                print(f"[MaterialLibrary] 创建材质失败: {e}")
            return

        try:
            # 1. 加载配置
            mapping_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'Assets', 'preset', 'pbr_mapping.json')
            pbr_config = {}
            try:
                with open(mapping_path, 'r', encoding='utf-8') as f:
                    pbr_config = json.load(f)
                print(f"[MaterialLibrary] pbr_mapping加载成功, material_property_mappings类型数={len(pbr_config.get('material_property_mappings', {}))}")
            except Exception as le:
                print(f"[MaterialLibrary] pbr_mapping加载失败: {le}")
            pbr_config['default_material_type'] = node_type

            # 2. 读取 meta 获取贴图类型信息
            meta = ZassetIO.read_meta(json_path)
            properties = meta.get("properties", {})

            # 3. 提取贴图到磁盘
            tex_path_map = {}
            tex_files = [n for n in ZassetIO.list_contents(json_path)
                         if n.startswith("textures/") and not n.endswith("/")]
            if resolution:
                tex_files = [n for n in tex_files
                             if n.startswith(f"textures/{resolution}/")]

            if tex_files:
                asset_name = meta.get("name",
                                     os.path.splitext(os.path.basename(json_path))[0])
                asset_id = meta.get("id", "")
                suffix = f"_{asset_id[-4:]}" if len(asset_id) >= 4 else ""
                try:
                    ws_root = cmds.workspace(q=True, rd=True) or ""
                    si_rule = cmds.workspace(fileRuleEntry="sourceImages")
                    base = os.path.join(ws_root, si_rule) if si_rule else ws_root
                    base = os.path.normpath(base)
                except Exception:
                    base = os.path.normpath(os.path.join(
                        os.path.expanduser("~/Documents/maya/projects/default"),
                        "sourceimages"))
                target_dir = os.path.join(base, "squirrel_asset",
                                         f"{asset_name}{suffix}")
                os.makedirs(target_dir, exist_ok=True)
                for name in tex_files:
                    rel = name[len("textures/"):]
                    target_path = os.path.join(target_dir, rel).replace("\\", "/")
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    data = ZassetIO.read_file(json_path, name)
                    if os.path.isfile(target_path):
                        if os.path.getsize(target_path) == len(data):
                            tex_path_map[name] = target_path
                            continue
                    with open(target_path, 'wb') as f:
                        f.write(data)
                    tex_path_map[name] = target_path

            # 4. 构建 textures dict → 调用 create_material()
            import re
            # 分辨率模式：匹配 _2K / _4K / _8K 等后缀，用于跨精度匹配
            _RES_PATTERN = re.compile(r'_\d+[kK](?=_|\.)', re.IGNORECASE)

            def _norm_name(name):
                """去掉分辨率标记，用于跨精度文件名匹配"""
                return _RES_PATTERN.sub('', name)

            fname_to_disk = {}
            for zip_rel, disk_path in tex_path_map.items():
                fname = os.path.basename(zip_rel.replace("\\", "/"))
                fname_to_disk[fname] = disk_path
                fname_to_disk[fname.lower()] = disk_path
                # 分辨率无关的标准化名
                fname_to_disk[_norm_name(fname)] = disk_path
                fname_to_disk[_norm_name(fname).lower()] = disk_path

            def _find_disk_path(tex_filename):
                disk_path = fname_to_disk.get(tex_filename)
                if disk_path:
                    return disk_path
                disk_path = fname_to_disk.get(tex_filename.lower())
                if disk_path:
                    return disk_path
                basename = os.path.basename(tex_filename.replace("\\", "/"))
                for candidate in (basename, basename.lower()):
                    disk_path = fname_to_disk.get(candidate)
                    if disk_path:
                        return disk_path
                    # 跨精度匹配：去掉分辨率标记后查找
                    disk_path = fname_to_disk.get(_norm_name(candidate))
                    if disk_path:
                        return disk_path
                for k, v in fname_to_disk.items():
                    if k.lower() == tex_filename.lower() or k.lower() == basename.lower():
                        return v
                    if _norm_name(k).lower() == _norm_name(basename).lower():
                        return v
                return None

            textures = {}
            color_space_map = {}
            type_rules = pbr_config.get('texture_type_rules', {})
            for pbr_type, rule in type_rules.items():
                color_space_map[pbr_type] = rule.get('color_space', 'sRGB')
                if rule.get('is_combo'):
                    ch = rule.get('channels', {})
                    for ch_name, usage_type in ch.items():
                        # channels 值可能是字符串 "ao" 或 dict {"usage": "ao"}
                        if isinstance(usage_type, dict):
                            usage = usage_type.get('usage', '')
                            if usage:
                                color_space_map[usage] = rule.get('color_space', 'Raw')
                        elif isinstance(usage_type, str):
                            color_space_map[usage_type] = rule.get('color_space', 'Raw')

            for tex_type, info in properties.items():
                if info.get('type') != 'texture':
                    continue
                tex_filename = info.get('path', '')
                if not tex_filename:
                    continue
                disk_path = _find_disk_path(tex_filename)
                if not disk_path:
                    continue
                # 归一化类型名：normal_dx→normal, height_bump→height
                base = tex_type
                if base.startswith('normal_'):
                    base = 'normal'
                elif base.startswith('height_'):
                    base = 'height'
                cs = color_space_map.get(base, color_space_map.get(tex_type, 'sRGB'))
                textures[tex_type] = {'full_path': disk_path, 'color_space': cs}

            # 5. 委托 create_material 创建完整节点网络（含 recipe 子网络）
            material_name = f"{asset_name}{suffix}"
            shader, result = create_material(material_name, textures, pbr_config)

            if shader:
                cmds.select(shader, replace=True)
                print(f"[MaterialLibrary] 完成: {shader}({node_type}), "
                      f"贴图数: {len(textures)}")
            else:
                print(f"[MaterialLibrary] 创建材质失败: {result}")

        except Exception as e:
            print(f"[MaterialLibrary] 创建材质失败: {e}")
            import traceback
            traceback.print_exc()

    def _on_assign_hdr_to_dome(self, hdr_material):
        """HDR → 指定贴图：将当前HDR贴图指定给选中的dome灯"""
        import maya.cmds as cmds
        import json, os
        from ..core.zasset_io import ZassetIO

        sel = cmds.ls(sl=True, long=True) or []
        if not sel:
            cmds.warning("请先选中一个穹顶灯光")
            return

        json_path = hdr_material.get("json_path", "")
        if not json_path or not os.path.isdir(json_path):
            print(f"[AssignHdr] HDR资产路径无效: {json_path}")
            return

        try:
            from ..integration.import_executor import _get_texture_target_dir

            asset_name = ""
            asset_id = ""
            hdr_target_path = ""
            all_names = ZassetIO.list_contents(json_path)
            meta = ZassetIO.read_meta(json_path)
            if meta:
                asset_name = meta.get("name") or meta.get("name_cn") or ""
                asset_id = meta.get("id", "")

            hdr_exts = {'.hdr', '.exr', '.png', '.jpg', '.jpeg', '.tga', '.tiff', '.tif'}
            tex_files = [n for n in all_names
                         if n.startswith("textures/") and not n.endswith("/")
                         and os.path.splitext(n)[1].lower() in hdr_exts]
            if not tex_files:
                tex_files = [n for n in all_names
                             if not n.startswith("textures/") and not n.endswith("/")
                             and os.path.splitext(n)[1].lower() in hdr_exts
                             and n not in ("meta.json", "node.zmetal", "node.mcm")]
            if not tex_files:
                print(f"[AssignHdr] HDR资产中未找到贴图文件")
                return
            tex_name = tex_files[0]
            tex_basename = os.path.basename(tex_name)
            target_dir = _get_texture_target_dir(asset_name, asset_id)
            os.makedirs(target_dir, exist_ok=True)
            hdr_target_path = os.path.join(target_dir, tex_basename).replace("\\", "/")
            if not os.path.isfile(hdr_target_path):
                with open(hdr_target_path, 'wb') as f:
                    f.write(ZassetIO.read_file(json_path, tex_name))
                print(f"[AssignHdr] 贴图已拷贝: {hdr_target_path}")

            # ── 2. 对选中的每个对象，找形状节点并替换现有HDR贴图路径 ──
            for obj in sel:
                shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or [obj]
                for shape in shapes:
                    if not cmds.objExists(shape):
                        continue
                    assigned = False
                    # 2a. 扫描所有字符串属性，匹配贴图扩展名
                    for try_visible in (True, False):
                        if assigned:
                            break
                        attrs = cmds.listAttr(shape, string=True, visible=try_visible) or []
                        for attr_name in attrs:
                            try:
                                full_attr = f"{shape}.{attr_name}"
                                old_val = cmds.getAttr(full_attr)
                                if not isinstance(old_val, str) or not old_val:
                                    continue
                                ext = os.path.splitext(old_val)[1].lower()
                                if ext in ('.hdr', '.exr', '.png', '.jpg', '.jpeg', '.tga'):
                                    cmds.setAttr(full_attr, hdr_target_path, type="string")
                                    print(f"[AssignHdr] {full_attr} → {hdr_target_path}")
                                    assigned = True
                                    break
                            except Exception:
                                pass
                    # 2b. 回退：检查已知的贴图属性名（tex0, tex, fileTextureName, texture 等）
                    if not assigned:
                        for known_attr in ("tex0", "tex", "texture", "fileTextureName"):
                            full_attr = f"{shape}.{known_attr}"
                            if cmds.objExists(full_attr):
                                try:
                                    cmds.setAttr(full_attr, hdr_target_path, type="string")
                                    print(f"[AssignHdr] {full_attr} → {hdr_target_path}")
                                    assigned = True
                                    break
                                except Exception:
                                    pass
                    # 2c. 查找连接到灯光颜色属性的 file 节点并替换路径
                    if not assigned:
                        for color_attr in ("color", "lightColor", "sc", "dt"):
                            color_plug = f"{shape}.{color_attr}"
                            if not cmds.objExists(color_plug):
                                continue
                            connections = cmds.listConnections(color_plug, source=True,
                                                               destination=False, plugs=True) or []
                            for src_plug in connections:
                                src_node = src_plug.split('.')[0]
                                if cmds.nodeType(src_node) == 'file':
                                    try:
                                        cmds.setAttr(f"{src_node}.fileTextureName",
                                                     hdr_target_path, type="string")
                                        cmds.setAttr(f"{src_node}.ignoreColorSpaceFileRules", True)
                                        print(f"[AssignHdr] {src_node}.fileTextureName → {hdr_target_path}")
                                        assigned = True
                                    except Exception as e:
                                        print(f"[AssignHdr] 更新 {src_node} 失败: {e}")
                    if not assigned:
                        print(f"[AssignHdr] {shape} 无现有贴图，创建贴图节点连接")
                        ntype = cmds.nodeType(shape)
                        # ── 创建 file 节点 ──
                        file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
                        cmds.setAttr(f"{file_node}.fileTextureName", hdr_target_path, type="string")
                        cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
                        try:
                            cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")
                        except Exception:
                            pass
                        p2d = cmds.shadingNode("place2dTexture", asUtility=True)
                        cmds.connectAttr(f"{p2d}.coverage", f"{file_node}.coverage")
                        cmds.connectAttr(f"{p2d}.translateFrame", f"{file_node}.translateFrame")
                        cmds.connectAttr(f"{p2d}.rotateFrame", f"{file_node}.rotateFrame")
                        cmds.connectAttr(f"{p2d}.mirrorU", f"{file_node}.mirrorU")
                        cmds.connectAttr(f"{p2d}.mirrorV", f"{file_node}.mirrorV")
                        cmds.connectAttr(f"{p2d}.stagger", f"{file_node}.stagger")
                        cmds.connectAttr(f"{p2d}.wrapU", f"{file_node}.wrapU")
                        cmds.connectAttr(f"{p2d}.wrapV", f"{file_node}.wrapV")
                        cmds.connectAttr(f"{p2d}.repeatUV", f"{file_node}.repeatUV")
                        cmds.connectAttr(f"{p2d}.offset", f"{file_node}.offset")
                        cmds.connectAttr(f"{p2d}.rotateUV", f"{file_node}.rotateUV")
                        cmds.connectAttr(f"{p2d}.noiseUV", f"{file_node}.noiseUV")
                        cmds.connectAttr(f"{p2d}.vertexUvOne", f"{file_node}.vertexUvOne")
                        cmds.connectAttr(f"{p2d}.vertexUvTwo", f"{file_node}.vertexUvTwo")
                        cmds.connectAttr(f"{p2d}.vertexUvThree", f"{file_node}.vertexUvThree")
                        cmds.connectAttr(f"{p2d}.vertexCameraOne", f"{file_node}.vertexCameraOne")
                        # ── 按渲染器连接贴图输出 ──
                        if ntype == "VRayLightDomeShape":
                            # 启用 useDomeTex（默认可能锁定）
                            dome_attr = f"{shape}.useDomeTex"
                            try:
                                cmds.setAttr(dome_attr, lock=False)
                            except Exception:
                                pass
                            cmds.setAttr(dome_attr, 1)
                            # V-Ray 链: p2d.uv → env.ouv → file.uv, file.oc → shape.dt
                            env = cmds.shadingNode("VRayPlaceEnvTex", asUtility=True)
                            parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                            if parent:
                                cmds.connectAttr(f"{parent[0]}.wm", f"{env}.tm")
                            cmds.connectAttr(f"{p2d}.uv", f"{env}.ouv")
                            cmds.connectAttr(f"{env}.ouv", f"{file_node}.uv")
                            cmds.connectAttr(f"{p2d}.outUvFilterSize", f"{file_node}.uvFilterSize")
                            cmds.connectAttr(f"{file_node}.oc", f"{shape}.dt")
                            print(f"[AssignHdr] VRay: file.oc → {shape}.dt (useDomeTex=1)")
                            assigned = True
                        elif ntype in ("aiSkyDomeLight", "skyDomeLight"):
                            cmds.connectAttr(f"{p2d}.outUV", f"{file_node}.uv")
                            cmds.connectAttr(f"{p2d}.outUvFilterSize", f"{file_node}.uvFilterSize")
                            cmds.connectAttr(f"{file_node}.outColor", f"{shape}.sc")
                            print(f"[AssignHdr] Arnold: file.outColor → {shape}.sc")
                            assigned = True
                        elif ntype == "RedshiftDomeLight":
                            cmds.setAttr(f"{shape}.tex0", hdr_target_path, type="string")
                            print(f"[AssignHdr] Redshift: {shape}.tex0 = {hdr_target_path}")
                            assigned = True
                        else:
                            cmds.connectAttr(f"{p2d}.outUV", f"{file_node}.uv")
                            cmds.connectAttr(f"{p2d}.outUvFilterSize", f"{file_node}.uvFilterSize")
                            # 通用：尝试 color → sc → lightColor → color
                            for out_attr, in_attr in (("outColor", "sc"), ("outColor", "lightColor"), ("oc", "dt"), ("outColor", "color")):
                                target_plug = f"{shape}.{in_attr}"
                                if cmds.objExists(target_plug):
                                    try:
                                        cmds.connectAttr(f"{file_node}.{out_attr}", target_plug)
                                        print(f"[AssignHdr] 通用: file.{out_attr} → {target_plug}")
                                        assigned = True
                                        break
                                    except Exception:
                                        pass
                        if not assigned:
                            print(f"[AssignHdr] 未能为 {shape} ({ntype}) 创建贴图连接")
            print(f"[AssignHdr] 指定贴图完成")
        except Exception as e:
            print(f"[AssignHdr] 指定贴图失败: {e}")
            import traceback
            traceback.print_exc()

    def _on_create_dome_light(self, preset_path, hdr_material):
        """HDR → 创建环境光：导入MA预设 + 替换HDR贴图路径 + 拷贝贴图到工程"""
        import maya.cmds as cmds
        import json, os, tempfile
        from ..core.zasset_io import ZassetIO

        json_path = hdr_material.get("json_path", "")
        if not json_path or not os.path.isdir(json_path):
            print(f"[MaterialLibrary] HDR资产路径无效: {json_path}")
            return

        if not os.path.isfile(preset_path):
            print(f"[MaterialLibrary] 预设文件不存在: {preset_path}")
            return

        try:
            from ..integration.import_executor import _get_texture_target_dir

            asset_name = ""
            asset_id = ""
            hdr_target_path = ""

            all_names = ZassetIO.list_contents(json_path)
            meta = ZassetIO.read_meta(json_path)
            if meta:
                asset_name = meta.get("name") or meta.get("name_cn") or ""
                asset_id = meta.get("id", "")

            hdr_exts = {'.hdr', '.exr', '.png', '.jpg', '.jpeg', '.tga', '.tiff', '.tif'}
            tex_files = [n for n in all_names
                         if n.startswith("textures/") and not n.endswith("/")
                         and os.path.splitext(n)[1].lower() in hdr_exts]
            if not tex_files:
                tex_files = [n for n in all_names
                             if not n.startswith("textures/") and not n.endswith("/")
                             and os.path.splitext(n)[1].lower() in hdr_exts
                             and n not in ("meta.json", "node.zmetal", "node.mcm")]
            if not tex_files:
                print(f"[MaterialLibrary] HDR资产中未找到贴图文件")
                return

            tex_name = tex_files[0]
            tex_basename = os.path.basename(tex_name)

            target_dir = _get_texture_target_dir(asset_name, asset_id)
            os.makedirs(target_dir, exist_ok=True)
            hdr_target_path = os.path.join(target_dir, tex_basename).replace("\\", "/")

            if not os.path.isfile(hdr_target_path):
                with open(hdr_target_path, 'wb') as f:
                    f.write(ZassetIO.read_file(json_path, tex_name))
                print(f"[DomeLight] 贴图已拷贝: {hdr_target_path}")
            else:
                print(f"[DomeLight] 贴图已存在: {hdr_target_path}")

            # ── 2. 导入 MA 预设文件 ──
            # 从 MA 预设文件中解析出所有节点类型（用于只追踪这些类型的变更，避免全场景扫描）
            import re
            preset_types = set(re.findall(r'createNode\s+(\S+)', open(preset_path, 'r', encoding='utf-8').read()))
            preset_types.add('file')
            preset_snapshot = {}
            for nt in preset_types:
                try:
                    preset_snapshot[nt] = set(cmds.ls(type=nt) or [])
                except Exception:
                    preset_snapshot[nt] = set()

            fd, tmp_ma = tempfile.mkstemp(suffix=".ma")
            try:
                with os.fdopen(fd, 'wb') as f:
                    with open(preset_path, 'rb') as src:
                        f.write(src.read())
                cmds.file(tmp_ma, i=True, ignoreVersion=True,
                          preserveReferences=True, mergeNamespacesOnClash=False, namespace=":")
            finally:
                if os.path.isfile(tmp_ma):
                    os.unlink(tmp_ma)

            # ── 3. 替换贴图路径：file节点 + 通用字符串属性 ──
            for nt in preset_types:
                before_set = preset_snapshot.get(nt, set())
                after_set = set(cmds.ls(type=nt) or [])
                for node in after_set - before_set:
                    if not cmds.objExists(node):
                        continue
                    # 3a. file 节点 → 设置 fileTextureName
                    if nt == 'file':
                        try:
                            cmds.setAttr(node + ".fileTextureName", hdr_target_path, type="string")
                            cmds.setAttr(node + ".ignoreColorSpaceFileRules", True)
                            try:
                                cmds.setAttr(node + ".colorSpace", "Raw", type="string")
                            except Exception:
                                pass
                            print(f"[DomeLight] file节点贴图已替换: {node} → {hdr_target_path}")
                        except Exception as e:
                            print(f"[DomeLight] 替换 {node} 贴图失败: {e}")
                        continue
                    # 3b. 其他节点 → 遍历字符串属性，匹配贴图扩展名
                    attrs = cmds.listAttr(node, string=True, visible=True) or []
                    for attr in attrs:
                        try:
                            full_attr = f"{node}.{attr}"
                            old_val = cmds.getAttr(full_attr)
                            if not isinstance(old_val, str) or not old_val:
                                continue
                            ext = os.path.splitext(old_val)[1].lower()
                            if ext in ('.hdr', '.exr', '.png', '.jpg', '.jpeg', '.tga'):
                                cmds.setAttr(full_attr, hdr_target_path, type="string")
                                print(f"[DomeLight] 属性贴图已替换: {full_attr} → {hdr_target_path}")
                        except Exception:
                            pass

            cmds.select(clear=True)
            print(f"[MaterialLibrary] 创建环境光完成 ({os.path.basename(preset_path)})")
        except Exception as e:
            print(f"[MaterialLibrary] 创建环境光失败: {e}")
            import traceback
            traceback.print_exc()
    @handle_errors(context="导入 zasset", show_dialog=True)
    def _on_import_zasset_folder(self):
        """选择文件夹，扫描所有 .zasset 子文件夹 → 复制到当前分类 + 新UUID + 自动去重"""
        if self._use_mock:
            QtWidgets.QMessageBox.information(
                self, "提示", "当前为 Mock 模式。\n请在设置中配置材质库路径后再导入。")
            return

        src_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择包含 .zasset 资产的文件夹")
        if not src_dir:
            return

        import shutil
        from ..core.zasset_io import ZassetIO
        from ..core.material import Material

        mgr = self._active_mgr
        # 获取当前分类和子库
        if self._active_mgr is self._project_mgr:
            current_cat = self._proj_category_tree.get_active_category()
        else:
            current_cat = self._category_tree.get_active_category()
        sub_lib = self._current_root_lib
        if current_cat == "all":
            current_cat = join_cat_id("materials", "custom")
            sub_lib = "materials"

        cat_short = current_cat
        if "||" in current_cat:
            _, cat_short = split_cat_id(current_cat)

        cat_path = cat_short
        folder = self._find_category_folder(cat_short, sub_lib)
        if folder:
            lib_path = mgr.get_library_path()
            full_rel = os.path.relpath(folder, lib_path).replace("\\", "/")
            prefix = sub_lib + "/"
            if full_rel.startswith(prefix):
                cat_path = full_rel[len(prefix):]

        base_path = os.path.join(mgr.get_library_path(), sub_lib, cat_path)
        os.makedirs(base_path, exist_ok=True)

        now_iso = MaterialManager._now_iso()
        imported = 0
        skipped = 0

        for entry in sorted(os.listdir(src_dir)):
            if not entry.lower().endswith(".zasset"):
                continue
            zasset_src = os.path.join(src_dir, entry)
            if not os.path.isdir(zasset_src):
                continue

            asset_name = os.path.splitext(entry)[0]
            target_path = MaterialManager._resolve_zasset_path(base_path, asset_name)
            final_name = os.path.splitext(os.path.basename(target_path))[0]

            try:
                shutil.copytree(zasset_src, target_path)
            except OSError as e:
                print(f"[Import] 复制失败 {entry}: {e}")
                skipped += 1
                continue

            old_meta = ZassetIO.read_meta(target_path) or {}
            new_meta = {
                "id": str(uuid.uuid4()),
                "name": final_name,
                "name_cn": old_meta.get("name_cn", final_name),
                "category": cat_path,
                "tags": old_meta.get("tags", []),
                "node_type": old_meta.get("node_type", "imported"),
                "software": old_meta.get("software", ""),
                "renderer": old_meta.get("renderer", ""),
                "color_space": old_meta.get("color_space", ""),
                "source": zasset_src,
                "import_date": now_iso,
                "create_date": old_meta.get("create_date", now_iso),
            }
            ZassetIO.update_meta_inplace(target_path, new_meta)

            mat_list = Material.from_json(target_path)
            if mat_list:
                mat = mat_list[0]
                mat.json_path = target_path
                mat.sub_library = sub_lib
                mgr._materials[mat.id] = mat
                mgr._refresh_material_counts()
                imported += 1
                print(f"[Import] .zasset: {entry} → {final_name}")

        if imported > 0:
            self._refresh_category_tree()
            tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
            tree._select_by_id(cat_path, sub_lib)
            tree._active_category = cat_path
            desc_ids = tree.get_descendant_ids(cat_path, sub_lib)
            self._on_category_selected(cat_path, desc_ids, sub_lib)
            msg = f"成功导入 {imported} 个 .zasset 资产。"
            if skipped:
                msg += f"\n跳过 {skipped} 个（复制失败）。"
            QtWidgets.QMessageBox.information(self, "导入完成", msg)
        else:
            QtWidgets.QMessageBox.information(
                self, "提示", "在选定文件夹中未找到 .zasset 资产。")

    @handle_errors(context="导入贴图", show_dialog=True)
    def _on_import_textures(self):
        """选择多张贴图 → 作为一个PBR贴图资产导入 .zasset + textures/ + pbr_mapping 适配缩略图"""
        if self._use_mock:
            QtWidgets.QMessageBox.information(
                self, "提示", "当前为 Mock 模式。\n请在设置中配置材质库路径后再导入。")
            return

        exts = " ".join(f"*{e}" for e in ['.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff', '.bmp', '.exr'])
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "选择贴图文件（多选视为同一套PBR贴图）", "", f"贴图文件 ({exts})")
        if not files:
            return

        import shutil, json
        from ..core.zasset_io import ZassetIO
        from ..core.material import Material

        _pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mapping_path = os.path.join(_pkg_dir, "Assets", "preset", "pbr_mapping.json")
        color_suffixes = set()
        all_suffixes = set()
        try:
            with open(mapping_path, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            for rule_name, rule in mapping.get("texture_type_rules", {}).items():
                aliases = [a.lower() for a in rule.get("aliases", [])]
                all_suffixes.update(aliases)
                if rule.get("is_color"):
                    rn_lower = rule_name.lower()
                    if rn_lower in ("albedo", "basecolor", "diffuse"):
                        color_suffixes.update(aliases)
        except Exception:
            pass

        _essential_color_suffixes = {'color', 'diffuse', 'diff', 'albedo', 'basecolor', 'base_color', 'base'}
        _essential_all_suffixes = _essential_color_suffixes | {'roughness', 'rough', 'metallic', 'metalness',
                                                               'normal', 'norm', 'bump', 'displacement', 'disp', 'height',
                                                               'ao', 'ambient_occlusion', 'specular', 'spec', 'gloss', 'glossiness',
                                                               'opacity', 'alpha', 'emissive', 'emission'}
        all_suffixes.update(_essential_all_suffixes)
        color_suffixes.update(_essential_color_suffixes)

        mgr = self._active_mgr
        if self._active_mgr is self._project_mgr:
            current_cat = self._proj_category_tree.get_active_category()
        else:
            current_cat = self._category_tree.get_active_category()
        sub_lib = self._current_root_lib
        if current_cat == "all":
            current_cat = join_cat_id("materials", "custom")
            sub_lib = "materials"

        cat_short = current_cat
        if "||" in current_cat:
            _, cat_short = split_cat_id(current_cat)

        cat_path = cat_short
        folder = self._find_category_folder(cat_short, sub_lib)
        if folder:
            lib_path = mgr.get_library_path()
            full_rel = os.path.relpath(folder, lib_path).replace("\\", "/")
            prefix = sub_lib + "/"
            if full_rel.startswith(prefix):
                cat_path = full_rel[len(prefix):]

        base_path = os.path.join(mgr.get_library_path(), sub_lib, cat_path)
        os.makedirs(base_path, exist_ok=True)

        def _match_sfx(stem, sfx_list):
            for sfx in sorted(sfx_list, key=lambda s: -len(s)):
                marker = "_" + sfx
                if marker in stem or stem.startswith(sfx + "_") or stem == sfx:
                    return sfx
            return None

        # 先找 color_file，确定资产名
        color_file = None
        for fp in files:
            fname = os.path.basename(fp)
            stem_lower = os.path.splitext(fname)[0].lower()
            matched = _match_sfx(stem_lower, color_suffixes)
            if matched and color_file is None:
                color_file = fp
        if color_file is None:
            color_file = files[-1]

        # 用 color_file（或 fallback）来命名资产，去掉 PBR 后缀和分辨率后缀（_1k/_2k/_4k/_8k）
        asset_source_file = color_file if color_file else files[0]
        first_stem = os.path.splitext(os.path.basename(asset_source_file))[0]
        first_lower = first_stem.lower()

        # 分辨率后缀列表
        res_suffixes = {'_1k', '_2k', '_4k', '_8k', '_16k'}
        # 先去 PBR 后缀，再去分辨率后缀
        for sfx in sorted(all_suffixes, key=lambda s: -len(s)):
            marker = "_" + sfx
            if first_lower.endswith(marker) or first_lower == sfx:
                first_stem = first_stem[:-(len(sfx) + 1)] if first_lower.endswith(marker) else first_stem[len(sfx):]
                first_lower = first_stem.lower()
                break
        # 去分辨率后缀
        for r_sfx in res_suffixes:
            if first_lower.endswith(r_sfx):
                first_stem = first_stem[:-len(r_sfx)]
                break

        target_path = MaterialManager._resolve_zasset_path(base_path, first_stem)
        final_name = os.path.splitext(os.path.basename(target_path))[0]
        zasset_textures = os.path.join(target_path, "textures")
        os.makedirs(zasset_textures, exist_ok=True)

        # 现在复制贴图
        for fp in files:
            fname = os.path.basename(fp)
            shutil.copy2(fp, os.path.join(zasset_textures, fname))

        now_iso = MaterialManager._now_iso()
        thumb_bytes = None
        if color_file:
            try:
                from PIL import Image
                from io import BytesIO
                img = Image.open(color_file)
                img = img.convert("RGB")
                img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, "PNG")
                thumb_bytes = buf.getvalue()
            except Exception:
                pass

        if thumb_bytes:
            thumb_dest = os.path.join(target_path, "thumb.sicon")
            with open(thumb_dest, 'wb') as f:
                f.write(thumb_bytes)

        meta = {
            "id": str(uuid.uuid4()),
            "name": final_name,
            "name_cn": final_name,
            "category": cat_path,
            "sub_library": sub_lib,
            "tags": ["texture"],
            "node_type": "texture",
            "software": "Texture Import",
            "renderer": "",
            "color_space": "ACEScg",
            "asset_type": sub_lib,
            "create_date": now_iso,
            "import_date": now_iso,
            "thumbnail_path": "thumb.sicon" if thumb_bytes else "",
        }
        ZassetIO.write_meta(target_path, meta)

        imported = 0
        mat_list = Material.from_json(target_path)

        if mat_list:
            mat = mat_list[0]
            mat.json_path = target_path
            mat.sub_library = sub_lib
            mgr._materials[mat.id] = mat
            mgr._refresh_material_counts()
            imported = 1
            print(f"[Import] 贴图: {final_name} ({len(files)} 张贴图)")

        if imported > 0:
            self._refresh_category_tree()
            tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
            tree._select_by_id(cat_path, sub_lib)
            tree._active_category = cat_path
            desc_ids = tree.get_descendant_ids(cat_path, sub_lib)
            self._on_category_selected(cat_path, desc_ids, sub_lib)
            QtWidgets.QMessageBox.information(self, "导入完成", f"成功导入 {imported} 个贴图资产。")
        else:
            QtWidgets.QMessageBox.information(self, "提示", "未能导入贴图。")

    @handle_errors(context="导入HDR", show_dialog=True)
    def _on_import_hdr(self):
        """选择 .hdr/.exr 文件 → 创建 .zasset + textures/ + 自动缩略图"""
        if self._use_mock:
            QtWidgets.QMessageBox.information(
                self, "提示", "当前为 Mock 模式。\n请在设置中配置材质库路径后再导入。")
            return

        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "选择HDR文件", "", "HDR文件 (*.hdr *.exr)")
        if not files:
            return

        import shutil
        from ..core.zasset_io import ZassetIO
        from ..core.material import Material

        mgr = self._active_mgr
        if self._active_mgr is self._project_mgr:
            current_cat = self._proj_category_tree.get_active_category()
        else:
            current_cat = self._category_tree.get_active_category()
        sub_lib = self._current_root_lib

        if current_cat == "all":
            sub_lib = "hdr"
            current_cat = join_cat_id("hdr", "custom")

        cat_short = current_cat
        if "||" in current_cat:
            _, cat_short = split_cat_id(current_cat)

        cat_path = cat_short
        folder = self._find_category_folder(cat_short, sub_lib)
        if folder:
            lib_path = mgr.get_library_path()
            full_rel = os.path.relpath(folder, lib_path).replace("\\", "/")
            prefix = sub_lib + "/"
            if full_rel.startswith(prefix):
                cat_path = full_rel[len(prefix):]

        base_path = os.path.join(mgr.get_library_path(), sub_lib, cat_path)
        os.makedirs(base_path, exist_ok=True)

        try:
            from ..quicktools.hdr_to_zasset import generate_thumbnail_from_hdr
            _hdr_thumb_ok = True
        except Exception:
            _hdr_thumb_ok = False

        now_iso = MaterialManager._now_iso()
        imported = 0

        for hdr_file in files:
            asset_name = os.path.splitext(os.path.basename(hdr_file))[0]
            target_path = MaterialManager._resolve_zasset_path(base_path, asset_name)
            final_name = os.path.splitext(os.path.basename(target_path))[0]
            zasset_textures = os.path.join(target_path, "textures")
            os.makedirs(zasset_textures, exist_ok=True)

            hdr_fname = os.path.basename(hdr_file)
            shutil.copy2(hdr_file, os.path.join(zasset_textures, hdr_fname))

            thumb_bytes = None
            if _hdr_thumb_ok:
                try:
                    thumb_bytes = generate_thumbnail_from_hdr(hdr_file)
                except Exception as e:
                    print(f"[Import] HDR缩略图生成失败: {e}")

            if thumb_bytes:
                thumb_dest = os.path.join(target_path, "thumb.sicon")
                with open(thumb_dest, 'wb') as f:
                    f.write(thumb_bytes)

            ext = os.path.splitext(hdr_file)[1].lower().lstrip(".")
            meta = {
                "id": str(uuid.uuid4()),
                "name": final_name,
                "name_cn": final_name,
                "category": cat_path,
                "sub_library": sub_lib,
                "tags": ["hdr", "environment", ext],
                "node_type": "environmentMap",
                "asset_type": "hdr",
                "software": "HDR Import",
                "renderer": "",
                "color_space": "ACEScg",
                "create_date": now_iso,
                "import_date": now_iso,
                "thumbnail_path": "thumb.sicon" if thumb_bytes else "",
            }
            ZassetIO.write_meta(target_path, meta)

            mat_list = Material.from_json(target_path)
            if mat_list:
                mat = mat_list[0]
                mat.json_path = target_path
                mat.sub_library = sub_lib
                mgr._materials[mat.id] = mat
                mgr._refresh_material_counts()
                imported += 1
                print(f"[Import] HDR: {os.path.basename(hdr_file)} → {final_name}")

        if imported > 0:
            self._refresh_category_tree()
            tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
            tree._select_by_id(cat_path, sub_lib)
            tree._active_category = cat_path
            desc_ids = tree.get_descendant_ids(cat_path, sub_lib)
            self._on_category_selected(cat_path, desc_ids, sub_lib)
            QtWidgets.QMessageBox.information(self, "导入完成", f"成功导入 {imported} 个HDR资产。")
        else:
            QtWidgets.QMessageBox.information(self, "提示", "未能导入HDR资产。")

    @handle_errors(context="导入文件", show_dialog=True)
    def _on_import_files(self):
        """从外部文件导入资产（每个选中文件独立生成 .zasset）"""
        if self._use_mock:
            QtWidgets.QMessageBox.information(
                self, "提示", "当前为 Mock 模式。\n请在设置中配置材质库路径后再导入。")
            return

        # 支持的文件过滤器
        exts = sorted(self._active_mgr.ASSET_FILE_EXTENSIONS)
        ext_filter = "资产文件 (" + " ".join(f"*{e}" for e in exts) + ")"
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "选择要导入的资产文件",
            "", ext_filter)
        if not files:
            return

        # 获取当前分类和子库（嵌套分类需构建完整层级路径）
        if self._active_mgr is self._project_mgr:
            current_cat = self._proj_category_tree.get_active_category()
        else:
            current_cat = self._category_tree.get_active_category()
        sub_lib = self._current_root_lib
        if current_cat == "all":
            current_cat = join_cat_id("materials", "custom")
            sub_lib = "materials"

        import shutil
        from ..core.zasset_io import ZassetIO
        from ..core.zasset_builder import ZassetBuilder

        imported = 0
        now_iso = MaterialManager._now_iso()

        # 构建完整分类路径（处理嵌套：如 models/characters/soldier 而不是 flat "soldier"）
        cat_short = current_cat
        if "||" in current_cat:
            _, cat_short = split_cat_id(current_cat)
        cat_path = cat_short
        folder = self._find_category_folder(cat_short, sub_lib)
        if folder:
            lib_path = self._active_mgr.get_library_path()
            full_rel = os.path.relpath(folder, lib_path).replace("\\", "/")
            # 去掉 root_lib 前缀（如 "models/characters/soldier" → "characters/soldier"）
            prefix = sub_lib + "/"
            if full_rel.startswith(prefix):
                cat_path = full_rel[len(prefix):]
        base_path = os.path.join(self._active_mgr.get_library_path(), sub_lib, cat_path)

        for src_file in files:
            fname = os.path.basename(src_file)
            ext = os.path.splitext(fname)[1].lower()
            asset_name = os.path.splitext(fname)[0]

            if ext == ".zasset" and os.path.isdir(src_file):
                target_path = MaterialManager._resolve_zasset_path(base_path, asset_name)
                shutil.copytree(src_file, target_path)
                final_name = os.path.splitext(os.path.basename(target_path))[0]
                old_meta = ZassetIO.read_meta(target_path) or {}
                new_meta = {
                    "id": str(uuid.uuid4()),
                    "name": final_name,
                    "name_cn": final_name,
                    "category": cat_path,
                    "tags": [],
                    "node_type": "imported",
                    "source": src_file,
                    "import_date": now_iso,
                    "create_date": old_meta.get("create_date", now_iso),
                }
                ZassetIO.update_meta_inplace(target_path, new_meta)
                from ..core.material import Material
                mat_list = Material.from_json(target_path)
                if mat_list:
                    mat = mat_list[0]
                    mat.json_path = target_path
                    mat.sub_library = sub_lib
                    self._active_mgr._materials[mat.id] = mat
                    self._active_mgr._refresh_material_counts()
                    imported += 1
                    print(f"[Import] 导入 .zasset: {os.path.basename(target_path)}")
                continue

            if not os.path.isfile(src_file):
                continue

            # ── 常规文件：每文件独立 .zasset ──
            target_path = MaterialManager._resolve_zasset_path(base_path, asset_name)
            final_name = os.path.splitext(os.path.basename(target_path))[0]

            meta = {
                "id": str(uuid.uuid4()),
                "name": final_name,
                "name_cn": final_name,
                "category": cat_path,
                "tags": [],
                "node_type": "imported",
                "software": "Maya",
                "renderer": "",
                "version": "2.0",
                "source": src_file,
                "import_date": now_iso,
                "create_date": now_iso,
                "formats": [ext.lstrip(".")],
            }

            files_dict = {f"node{ext}": src_file}

            ok = ZassetBuilder.build(target_path, files_dict, meta)
            if not ok:
                print(f"[Import] 构建 .zasset 失败: {src_file}")
                continue

            # 直接注册，绕过 add_material 的二次 copy
            from ..core.material import Material
            mat_list = Material.from_json(target_path)
            if mat_list:
                mat = mat_list[0]
                mat.json_path = target_path
                mat.sub_library = sub_lib
                self._active_mgr._materials[mat.id] = mat
                self._active_mgr._refresh_material_counts()
                imported += 1
                print(f"[Import] 导入文件: {final_name} → {sub_lib}/{cat_path}")

        if imported > 0:
            self._refresh_category_tree()
            tree = self._proj_category_tree if self._active_mgr is self._project_mgr else self._category_tree
            tree._select_by_id(cat_path, sub_lib)
            tree._active_category = cat_path
            desc_ids = tree.get_descendant_ids(cat_path, sub_lib)
            self._on_category_selected(cat_path, desc_ids, sub_lib)
            print(f"[MaterialLibrary] 从文件导入了 {imported} 个资产")
        else:
            QtWidgets.QMessageBox.information(
                self, "提示", "未能导入任何资产。")

    def _on_create_preset(self):
        """创建材质预设（弹出导出对话框）"""
        dialog = ExportPresetDialog(self, materials=[])
        dialog.exec()
        self._refresh_search_bar_tags(self._current_root_lib)

    def _on_grid_export_preset(self, material):
        dialog = ExportPresetDialog(self, materials=[material])
        dialog.exec()
        self._refresh_search_bar_tags(self._current_root_lib)

    @handle_errors(context="删除材质", show_dialog=True)
    def _on_grid_delete(self, material_ids):
        """删除选中的材质（支持批量）。多选时显示警告确认。"""
        if not material_ids:
            return
        is_batch = len(material_ids) > 1

        # 收集材质名称用于提示
        names = []
        mgr = self._active_mgr
        for mid in material_ids:
            mat = mgr.get_by_id(mid) if not self._use_mock else None
            # 也尝试按名称匹配（子库网格项）
            if not mat:
                for m in mgr._materials.values():
                    if mid in m.json_path or m.name == mid:
                        mat = m
                        break
            names.append(mid if not mat else (mat.name or mat.name_cn or mid))

        # 多选时显示更详细的警告
        if is_batch:
            name_list = "\n".join(f"  • {n}" for n in names[:20])
            if len(names) > 20:
                name_list += f"\n  ... 等共 {len(names)} 个"
            msg = (f"确定要删除以下 {len(names)} 个资产吗？\n\n"
                   f"{name_list}\n\n"
                   f"⚠️ 此操作不可逆！将永久删除该资产文件。")
            reply = self._show_confirm_dialog("确认批量删除", msg, is_warning=True)
        else:
            msg = f"确定要删除资产 '{names[0]}' 吗？\n\n此操作将永久删除该资产文件。"
            reply = self._show_confirm_dialog("确认删除", msg)
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        print(f"[MainWindow] 删除资产: ids={material_ids}")
        # 逐个删除
        if not self._use_mock:
            for mid in material_ids:
                for mids in mgr._favorites.values():
                    mids.discard(mid)
                mgr.save_favorites()
                mgr.remove_material(mid)
            self._favorites_panel._refresh()
            if self._active_mgr is self._project_mgr:
                self._on_refresh_project()
            else:
                self._refresh_keep_current(reload_materials=True)

    def _check_duplicate_uuids(self):
        """检测 UUID 重复的资产，弹窗询问是否自动修复"""
        mgr = self._active_mgr if self._active_mgr else self._material_manager
        if not mgr or not mgr.has_duplicates():
            return

        count = len(mgr._duplicate_files)
        reply = QtWidgets.QMessageBox.question(
            self, "检测到重复 UUID",
            f"当前资产库中发现 {count} 个 UUID 重复的资产文件。\n\n"
            f"这些资产因 UUID 与其他资产冲突而无法在分类中显示。\n\n"
            f"是否自动为这些资产生成新的 UUID？\n"
            f"（仅修改 meta.json 中的 id 字段，不改变资产内容）",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes
        )

        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._status_info.setText(f"正在修复 UUID...")

        def _progress_cb(i, total):
            self._status_info.setText(f"修复 UUID: {i}/{total}")
            QtWidgets.QApplication.processEvents()

        fixed = mgr.fix_duplicate_uuids(progress_callback=_progress_cb)

        if fixed > 0:
            QtWidgets.QMessageBox.information(self, "修复完成",
                f"已为 {fixed} 个资产重新分配 UUID。\n\n"
                f"正在刷新资产库以加载这些资产...")
            if self._active_mgr is self._project_mgr:
                self._on_refresh_project()
            else:
                self._refresh_keep_current(reload_materials=True)
        self._update_status_bar()

    def _on_thumbnail_update(self, material_id):
        """调起截图覆盖层，让 overlay 直接将截图保存到资产目录的 thumb.sicon"""
        if self._use_mock:
            return
        mgr = self._active_mgr
        mat = mgr.get_by_id(material_id)
        if not mat:
            return

        if mat.is_zasset:
            thumb_dest = os.path.join(mat.zasset_path, "thumb.sicon")
        else:
            thumb_dest = os.path.join(os.path.dirname(mat.json_path), f"{mat.name}.sicon")

        from .thumbnail_capture_overlay import ThumbnailCaptureOverlay
        existing = ThumbnailCaptureOverlay.find_existing()
        if existing is not None:
            try:
                existing.winId()
                if existing.toolbar:
                    existing.toolbar.winId()
                self._capture_overlay = existing
            except RuntimeError:
                existing = None
                self._capture_overlay = None

        if self._capture_overlay and self._capture_overlay.isVisible():
            self._capture_overlay.save_path_override = thumb_dest
            self._capture_overlay.raise_()
            self._capture_overlay.activateWindow()
            if hasattr(self._capture_overlay, 'toolbar'):
                tt = self._capture_overlay.toolbar
                if not tt.isVisible():
                    tt.show()
                    tt.raise_()
        else:
            self._capture_overlay = ThumbnailCaptureOverlay(keep_alive=True)
            self._capture_overlay.save_path_override = thumb_dest
            self._capture_overlay.show()

        try:
            self._capture_overlay.captured.disconnect()
        except (TypeError, RuntimeError):
            pass

        def on_done(pixmap):
            if pixmap.isNull():
                return
            self._apply_new_thumbnail(mat, pixmap)

        self._capture_overlay.captured.connect(on_done)

    # ── 预览面板缩略图操作 ─────────────────────────────

    def _on_preview_thumbnail_capture(self, material_id):
        """预览面板「截取」→ 复用截图覆盖层，overlay 直写缩略图到资产目录"""
        mgr = self._active_mgr
        mat = mgr.get_by_id(material_id)
        if not mat or not mat.json_path:
            return

        if mat.is_zasset:
            thumb_dest = os.path.join(mat.zasset_path, "thumb.sicon")
        else:
            thumb_dest = os.path.join(os.path.dirname(mat.json_path), f"{mat.name}.sicon")

        from .thumbnail_capture_overlay import ThumbnailCaptureOverlay
        existing = ThumbnailCaptureOverlay.find_existing()
        if existing is not None:
            try:
                existing.winId()
                if existing.toolbar:
                    existing.toolbar.winId()
                self._capture_overlay = existing
            except RuntimeError:
                existing = None
                self._capture_overlay = None
        if self._capture_overlay and self._capture_overlay.isVisible():
            self._capture_overlay.save_path_override = thumb_dest
            self._capture_overlay.raise_()
            self._capture_overlay.activateWindow()
            if hasattr(self._capture_overlay, 'toolbar'):
                tt = self._capture_overlay.toolbar
                if not tt.isVisible():
                    tt.show()
                    tt.raise_()
        else:
            self._capture_overlay = ThumbnailCaptureOverlay(keep_alive=True)
            self._capture_overlay.save_path_override = thumb_dest
            self._capture_overlay.show()

        try:
            self._capture_overlay.captured.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            self._capture_overlay.cancelled.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            self._capture_overlay.recordingFinished.disconnect()
        except (TypeError, RuntimeError):
            pass

        def on_done(pixmap):
            if pixmap.isNull():
                return
            self._apply_new_thumbnail(mat, pixmap)

        def on_recording(gif_path):
            if not os.path.isfile(gif_path):
                return
            import shutil
            if mat.is_zasset:
                shutil.copy(gif_path, os.path.join(mat.zasset_path, "thumb.aicon"))
            with open(gif_path, 'rb') as f:
                mat.thumb_bytes = f.read()
            self._thumbnail_grid._thumb_cache.pop(mat.id, None)
            px = QtGui.QPixmap(gif_path)
            if not px.isNull():
                self._thumbnail_grid._thumb_cache[mat.id] = px
                self._update_preview_thumbnail(mat, px)
            card = self._thumbnail_grid._card_pool.get(mat.id)
            if card and hasattr(card, 'material_data') and isinstance(card.material_data, dict):
                card.material_data["thumb_bytes"] = mat.thumb_bytes
            for mdict in self._thumbnail_grid._filtered_materials:
                if mdict.get("id") == mat.id:
                    mdict["thumb_bytes"] = mat.thumb_bytes
                    break
            for mid in list(self._thumbnail_grid._selected_materials.keys()):
                if mid == mat.id:
                    self._thumbnail_grid._selected_materials[mid]["thumb_bytes"] = mat.thumb_bytes
                    break
            if self._right_panel._material and self._right_panel._material.get("id") == mat.id:
                self._right_panel._material["thumb_bytes"] = mat.thumb_bytes
            self._right_panel._update_thumb_buttons()

        self._capture_overlay.captured.connect(on_done)
        self._capture_overlay.recordingFinished.connect(on_recording)

    def _on_preview_thumbnail_import(self, material_id):
        """预览面板「导入」→ 打开文件浏览器，选中即复制到资产目录"""
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择缩略图", "",
            "图片 (*.sicon *.jpg *.jpeg *.aicon *.bmp);;所有文件 (*.*)")
        if not file_path:
            return

        mgr = self._active_mgr
        mat = mgr.get_by_id(material_id)
        if not mat or not mat.json_path:
            return

        import shutil
        if mat.is_zasset:
            shutil.copy2(file_path, os.path.join(mat.zasset_path, "thumb.sicon"))
        else:
            ext = os.path.splitext(file_path)[1] or ".sicon"
            dest = os.path.join(os.path.dirname(mat.json_path), f"{mat.name}{ext}")
            shutil.copy2(file_path, dest)

        with open(file_path, 'rb') as f:
            mat.thumb_bytes = f.read()
        px = QtGui.QPixmap(file_path)
        if not px.isNull():
            self._thumbnail_grid._thumb_cache[mat.id] = px
            card = self._thumbnail_grid._card_pool.get(mat.id)
            if card:
                from .thumbnail_view import MaterialDragLabel
                thumb_widget = card.findChild(MaterialDragLabel)
                if thumb_widget:
                    scaled = px.scaled(
                        thumb_widget.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation)
                    thumb_widget.setPixmap(scaled)
                if hasattr(card, 'material_data') and isinstance(card.material_data, dict):
                    card.material_data["thumb_bytes"] = mat.thumb_bytes
        for mdict in self._thumbnail_grid._filtered_materials:
            if mdict.get("id") == mat.id:
                mdict["thumb_bytes"] = mat.thumb_bytes
                break
        for mid in list(self._thumbnail_grid._selected_materials.keys()):
            if mid == mat.id:
                self._thumbnail_grid._selected_materials[mid]["thumb_bytes"] = mat.thumb_bytes
                break
        if self._right_panel._material and self._right_panel._material.get("id") == mat.id:
            self._right_panel._material["thumb_bytes"] = mat.thumb_bytes
            self._update_preview_thumbnail(mat, px)
        if not px.isNull():
            self._right_panel._update_thumb_buttons()

    def _apply_new_thumbnail(self, mat, pixmap):
        """统一的新缩略图应用逻辑：更新所有数据存储 + 即时刷新 UI"""
        ba = QtCore.QByteArray()
        buf = QtCore.QBuffer(ba)
        buf.open(QtCore.QIODeviceBase.WriteOnly)
        pixmap.save(buf, "PNG")
        buf.close()
        new_tb = bytes(ba)

        mat.thumb_bytes = new_tb
        self._thumbnail_grid._thumb_cache[mat.id] = pixmap.copy()

        card = self._thumbnail_grid._card_pool.get(mat.id)
        if card:
            from .thumbnail_view import MaterialDragLabel
            thumb_widget = card.findChild(MaterialDragLabel)
            if thumb_widget:
                scaled = pixmap.scaled(
                    thumb_widget.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation)
                thumb_widget.setPixmap(scaled)
            if hasattr(card, 'material_data') and isinstance(card.material_data, dict):
                card.material_data["thumb_bytes"] = new_tb

        for mdict in self._thumbnail_grid._filtered_materials:
            if mdict.get("id") == mat.id:
                mdict["thumb_bytes"] = new_tb
                break

        for mid in list(self._thumbnail_grid._selected_materials.keys()):
            if mid == mat.id:
                self._thumbnail_grid._selected_materials[mid]["thumb_bytes"] = new_tb
                break

        if self._right_panel._material and self._right_panel._material.get("id") == mat.id:
            self._right_panel._material["thumb_bytes"] = new_tb
            self._update_preview_thumbnail(mat, pixmap)

        self._right_panel._update_thumb_buttons()

    # ── 资产创建 ───────────────────────────────────────

    def _get_current_asset_type(self) -> str:
        """获取当前选中分类对应的子库名（materials/models/textures/...）"""
        try:
            tree = self._get_active_category_tree()
            item = tree._tree.currentItem()
            if not item:
                return "materials"
            # 向上遍历找到顶级分类 → 读取子库
            while True:
                parent = item.parent()
                if parent is None:
                    break
                gp = parent.parent()
                if gp is None:
                    break
                item = parent
            root_lib = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) or ""
            if root_lib:
                return root_lib
            cat_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole) or ""
            sub_libs = self._active_mgr.ASSET_SUB_LIBRARIES if hasattr(self._active_mgr, 'ASSET_SUB_LIBRARIES') else {}
            if cat_id in sub_libs:
                return cat_id
            return "materials"
        except Exception:
            return "materials"

    def _get_current_category_display(self) -> str:
        """获取当前选中分类的显示名"""
        try:
            tree = self._get_active_category_tree()
            item = tree._tree.currentItem()
            if not item:
                return ""
            return item.text(0) or ""
        except Exception:
            return ""

    def _get_common_tags_for_current_category(self) -> list:
        """获取当前选中顶级分类下的所有唯一标签。

        从分类树中找到当前选中项所属的顶级分类（子库下第一级），
        收集该分类及其子分类下所有资产的 tags，去重后返回。
        若当前选中为子库根或"全部"，则返回全局常用标签。
        """
        try:
            tree = self._get_active_category_tree()
            item = tree._tree.currentItem()
            if not item:
                return self._active_mgr.get_common_tags("materials") if hasattr(self, '_active_mgr') else []

            # 向上遍历找到顶级分类节点（depth==1，即子库下第一级）
            # 如果当前就是子库根或"全部"（topLevelItem），保持不动
            while True:
                parent = item.parent()
                if parent is None:
                    # 到了 invisibleRootItem → 当前为 topLevelItem
                    break
                # 检查 parent 是否为 topLevelItem（即 item 为 depth 1）
                gp = parent.parent()  # grandparent
                if gp is None:
                    # parent 是 topLevelItem，item 是 depth 1
                    break
                item = parent

            cat_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            root_lib = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 3) or ""

            if not cat_id or cat_id == "all":
                return self._active_mgr.get_common_tags("materials")

            sub_libs = self._active_mgr.ASSET_SUB_LIBRARIES if hasattr(self._active_mgr, 'ASSET_SUB_LIBRARIES') else {}
            if cat_id in sub_libs:
                # 选中了子库根，返回全局标签
                return self._active_mgr.get_common_tags(cat_id)

            # 返回该子库的 config 常用标签
            if root_lib:
                return self._active_mgr.get_common_tags(root_lib)
            return []
        except Exception:
            try:
                return self._active_mgr.get_common_tags("materials")
            except Exception:
                return []

    def _get_maya_parent(self):
        """获取 Maya 主窗口作为父级"""
        try:
            from maya import OpenMayaUI as _omu
            from shiboken6 import wrapInstance
            maya_ptr = _omu.MQtUtil.mainWindow()
            if maya_ptr is not None:
                return wrapInstance(int(maya_ptr), QtWidgets.QWidget)
        except Exception:
            pass
        return None

    def _on_ai_analysis(self, material):
        """AI 分析缩略图（右键菜单）— 选中资产后走统一配置流程"""
        mid = material.get('id', '')
        if mid:
            self._thumbnail_grid._selected_materials.clear()
            self._thumbnail_grid._selected_materials[mid] = material
            self._thumbnail_grid._refresh_card_highlights()
        self._on_ai_analysis_with_config()

    def _on_ai_analysis_with_config(self):
        """弹出配置对话框 → 批量分析"""
        selected = self._thumbnail_grid.get_selected_materials_list()
        if not selected:
            QtWidgets.QMessageBox.information(self, "AI 分析",
                "请先在网格中选中要分析的资产")
            return

        try:
            from ..core.ai_analyzer import AIAnalyzer
            analyzer = AIAnalyzer()
            if not analyzer.is_available():
                QtWidgets.QMessageBox.warning(self, "AI 分析",
                    "无法连接到 Ollama 服务。\n\n"
                    "请确保 Ollama 已安装并启动：\n"
                    "1. 下载安装: https://ollama.com\n"
                    "2. 拉取模型: ollama pull qwen3-vl:8b\n"
                    "3. 启动服务: ollama serve")
                return
            models = analyzer.get_available_models()
        except Exception:
            models = []

        from .ai_analysis_dialog import AIAnalysisConfigDialog
        dlg = AIAnalysisConfigDialog(self, available_models=models)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return

        config = dlg.get_config()
        self._on_ai_analysis_batch(config=config)

    def _on_ai_analysis_batch(self, config=None):
        """AI 分析缩略图（AI 工具下拉按钮批量）"""
        config = config or {}
        language = config.get('language', '中文')
        review = config.get('review_output', True)
        model = config.get('model', '')
        translate_existing_tags = config.get('translate_existing_tags', False)

        selected = self._thumbnail_grid.get_selected_materials_list()
        if not selected:
            QtWidgets.QMessageBox.information(self, "AI 分析",
                "请先在网格中选中要分析的资产")
            return

        total = len(selected)

        try:
            from ..core.ai_analyzer import AIAnalyzer
            analyzer = AIAnalyzer()
            if model:
                analyzer.model = model
            if not analyzer.is_available():
                QtWidgets.QMessageBox.warning(self, "AI 分析",
                    "无法连接到 Ollama 服务，请确保 Ollama 已启动")
                return
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "AI 分析", f"初始化失败: {e}")
            return

        if review:
            label_text = "正在分析 0/{0} ...".format(total)
        else:
            label_text = "正在分析并应用 0/{0} ...".format(total)

        progress = QtWidgets.QProgressDialog(
            label_text, "取消", 0, total, self)
        progress.setWindowTitle("批量 AI 分析")
        progress.setModal(True)
        progress.setMinimumDuration(0)
        progress.show()
        QtCore.QCoreApplication.processEvents()

        collected = []

        for i, mat in enumerate(selected):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"正在分析 {i + 1}/{total}: {mat.get('name', '')}")
            progress.setValue(i)
            QtCore.QCoreApplication.processEvents()

            thumb_bytes = mat.get('thumb_bytes', None)
            if not thumb_bytes:
                print(f"[AI Batch] 跳过无缩略图: {mat.get('name', '')}")
                continue

            sub_library = mat.get('sub_library', 'materials')
            existing_tags = mat.get('tags', [])
            if translate_existing_tags and existing_tags:
                existing_tags = analyzer.translate_tags(existing_tags, language)
            result = analyzer.analyze_image(thumb_bytes, sub_library, language=language, existing_tags=existing_tags if existing_tags else None)
            if not result:
                print(f"[AI Batch] 分析失败: {mat.get('name', '')}")
                continue

            collected.append({
                'material': mat,
                'result': result,
            })

        progress.setValue(total)
        progress.close()

        if not collected:
            QtWidgets.QMessageBox.warning(self, "批量 AI 分析", "没有资产分析成功")
            return

        if review:
            from .ai_analysis_dialog import AIBatchResultsDialog
            dlg = AIBatchResultsDialog(self, results=collected)
            dlg.batchApplied.connect(self._on_batch_ai_applied)
            dlg.exec()
        else:
            self._on_batch_ai_applied(collected)

    def _on_batch_ai_applied(self, selected_results):
        mgr = self._active_mgr
        if not mgr:
            return

        success = 0
        failed = 0

        for entry in selected_results:
            mat = entry.get('material', {})
            result = entry.get('result', {})
            material_id = mat.get('id', '')

            updates = result.copy()
            move_to_category = entry.get('move_to_category', False)
            category = updates.pop('sub_category', '')
            if category:
                updates['category'] = category
                if move_to_category:
                    sub_lib = mat.get('sub_library', '')
                    moved = mgr.move_material_to_category(material_id, category, sub_lib=sub_lib)
                    if moved:
                        print(f"[AI Batch] 资产 {mat.get('name', '')} 已移动到分类: {category}")
                        updated_mat = mgr._materials.get(material_id)
                        if not updated_mat:
                            updated_mat = mgr._materials.get(mat.get('id', ''))
                        if updated_mat:
                            material_id = getattr(updated_mat, 'id', material_id)
                            updates.pop('category', None)
                        # 继续应用 name_cn/tags/notes（不 continue）
                    else:
                        print(f"[AI Batch] 移动失败: {mat.get('name', '')} -> {category}")

            if material_id and updates:
                ok = mgr.update_material(material_id, updates)
                if ok:
                    success += 1
                else:
                    failed += 1
            else:
                failed += 1

        self._refresh_material_grid()
        QtWidgets.QMessageBox.information(self, "批量 AI 分析",
            f"应用完成!\n成功: {success}  失败: {failed}")

    def _do_ai_analysis_for_material(self, material, show_dialog=True):
        mgr = self._active_mgr
        if not mgr:
            return

        material_id = material.get('id', '')
        if not material_id:
            return

        thumb_bytes = material.get('thumb_bytes', None)
        if not thumb_bytes:
            QtWidgets.QMessageBox.warning(self, "AI 分析", "该资产没有缩略图可分析")
            return

        sub_library = material.get('sub_library', 'materials')

        try:
            from ..core.ai_analyzer import AIAnalyzer

            analyzer = AIAnalyzer()
            if not analyzer.is_available():
                QtWidgets.QMessageBox.warning(self, "AI 分析",
                    "无法连接到 Ollama 服务，请确保 Ollama 已启动")
                return

            progress_dlg = QtWidgets.QProgressDialog("正在分析缩略图...", "取消", 0, 0, self)
            progress_dlg.setWindowTitle("AI 分析")
            progress_dlg.setModal(True)
            progress_dlg.show()
            QtCore.QCoreApplication.processEvents()

            result = analyzer.analyze_image(thumb_bytes, sub_library)

            progress_dlg.close()

            if not result:
                QtWidgets.QMessageBox.warning(self, "AI 分析", "分析失败，请重试")
                return

            if show_dialog:
                from .ai_analysis_dialog import AIAnalysisDialog
                dlg = AIAnalysisDialog(self, material=material, analysis_result=result)
                dlg.analysisApplied.connect(
                    lambda updates: self._on_ai_analysis_applied(material_id, updates))
                dlg.exec()
            else:
                updates = result.copy()
                category = updates.pop('sub_category', '')
                if category:
                    updates['category'] = category
                self._on_ai_analysis_applied(material_id, updates)

        except Exception as e:
            print(f"[AI Analysis] Error: {e}")
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(self, "AI 分析", f"分析过程发生错误: {str(e)}")

    def _on_ai_analysis_applied(self, material_id, updates):
        """应用 AI 分析结果到元数据"""
        mgr = self._active_mgr
        if not mgr:
            return

        material_id = updates.pop('material_id', material_id)
        move_to_category = updates.pop('move_to_category', False)
        if not material_id:
            return

        if move_to_category and 'category' in updates:
            target_category = updates.pop('category')
            mat = mgr._materials.get(material_id)
            sub_lib = getattr(mat, 'sub_library', '') if mat else ''
            moved = mgr.move_material_to_category(material_id, target_category, sub_lib=sub_lib)
            if moved:
                updated_mat = mgr._materials.get(material_id)
                if not updated_mat and mat:
                    updated_mat = mgr._materials.get(getattr(mat, 'id', ''))
                if updated_mat:
                    material_id = getattr(updated_mat, 'id', material_id)
                # 继续应用 name_cn/tags/notes（不 return）

        success = mgr.update_material(material_id, updates)
        if success:
            self._refresh_material_grid()
            QtWidgets.QMessageBox.information(self, "AI 分析", "元数据已更新")
        else:
            QtWidgets.QMessageBox.warning(self, "AI 分析", "更新元数据失败")

    def _on_update_asset(self, mid):
        """右键→更新资产：弹出预填对话框，确认后替换原 .zasset"""
        mgr = self._active_mgr
        if not mgr:
            return
        mat = mgr.get_by_id(mid)
        if not mat:
            return

        old_path = mat.json_path
        if not old_path or not old_path.endswith(".zasset"):
            return

        # 读取原资产格式列表（从 meta + 内部文件推断）
        old_formats = set()
        try:
            import json as _json
            from ..core.zasset_io import ZassetIO
            all_names = ZassetIO.list_contents(old_path)
            old_meta = ZassetIO.read_meta(old_path)
            if old_meta:
                old_formats = set(old_meta.get("exported_formats", old_meta.get("formats", [])))
            if not old_formats:
                for n in all_names:
                    ext = os.path.splitext(n)[1].lstrip(".").lower()
                    fmt_map = {"zmetal": "zmetal", "ma": "ma", "mb": "mb",
                               "fbx": "fbx", "obj": "obj", "abc": "abc",
                               "ass": "arnold",
                               "vrmesh": "vray", "rs": "redshift"}
                    if ext in fmt_map:
                        old_formats.add(fmt_map[ext])
        except Exception:
            old_meta = {}

        # 弹出创建对话框，预填现有数据
        try:
            common_tags = self._get_common_tags_for_current_category()
        except Exception:
            common_tags = []
        try:
            from ..utils.maya_plugin_checker import MayaPluginChecker
            plugin_statuses = MayaPluginChecker.get_all_statuses()
        except Exception:
            plugin_statuses = {}

        dlg = AssetCreateDialog(
            self._get_maya_parent(),
            material_name=mat.name,
            category_display=mgr.get_category_display_name(mat.category),
            common_tags=common_tags,
            asset_type=mat.sub_library,
            associated_objects=[],
            material_count=0,
            plugin_statuses=plugin_statuses,
            material_name_cn=mat.name_cn,
            material_tags=list(mat.tags),
            old_formats=list(old_formats),
        )

        def _on_update_ready(base_config):
            """确认后：导出新 .zasset → 替换旧 .zasset（保留 ID + 标签）"""
            import shutil, tempfile

            try:
                import maya.cmds as cmds
                sel = cmds.ls(selection=True, long=False) or []
            except Exception:
                sel = []

            cat_dir = os.path.dirname(old_path)
            base_config.target_dir = cat_dir
            base_config.category = mat.category

            from ..core.export_orchestrator import ExportOrchestrator, ExportConfig

            # 查找 Maya 中与资产同名的材质节点（用于 zmetal 导出）
            mat_node = ""
            try:
                import maya.cmds as cmds
                # 先用对话框中的名字（用户可能改了名）
                if base_config.asset_name and cmds.objExists(base_config.asset_name):
                    mat_node = base_config.asset_name
                    print(f"[UpdateAsset] 用对话框名称: {mat_node}")
                elif cmds.objExists(mat.name):
                    mat_node = mat.name
                    print(f"[UpdateAsset] 用原资产名: {mat_node}")
                elif sel:
                    _MAT_TYPES = set(mgr._config.get("material_node_types", [
                        'aiStandardSurface', 'standardSurface', 'lambert', 'blinn',
                        'phong', 'openPBRSurface', 'pxrSurface',
                    ]))
                    for s in sel:
                        nt = cmds.nodeType(s)
                        if nt in _MAT_TYPES:
                            mat_node = s
                            break
                        if not cmds.objectType(s, isAType='dagNode'):
                            mat_node = s
                            break
                        sgs = cmds.listConnections(s, type="shadingEngine") or []
                        for sg in sgs:
                            cons = cmds.listConnections(f"{sg}.surfaceShader") or []
                            for c in cons:
                                if cmds.objExists(c):
                                    mat_node = c
                                    break
                            if mat_node:
                                break
                    if mat_node:
                        pass  # found above
            except Exception:
                pass

            # 导出到临时目录（避免与旧文件冲突）
            tmp_dir = tempfile.mkdtemp(prefix=f"update_{base_config.asset_name}_")

            cfg = ExportConfig(
                asset_name=base_config.asset_name,
                name_cn=base_config.name_cn,
                category=mat.category,
                tags=list(mat.tags),
                asset_type=mat.sub_library,
                export_zmetal=base_config.export_zmetal,
                merge_zmetal=base_config.merge_zmetal,
                export_mcm=base_config.export_mcm,
                export_ma=base_config.export_ma,
                export_mb=base_config.export_mb,
                export_fbx=base_config.export_fbx,
                export_obj=base_config.export_obj,
                export_usd=base_config.export_usd,
                export_abc=base_config.export_abc,
                ani_frame_mode=base_config.ani_frame_mode,
                proxy_formats=base_config.proxy_formats,
                export_material_only=base_config.export_material_only,
                export_mode="single",
                target_dir=tmp_dir,  # 导出到临时目录
                associated_objects=sel,
                material_node=mat_node or base_config.material_node or mat.name,
                collect_associated=getattr(base_config, 'collect_associated', False),
            )
            print(f"[UpdateAsset] ExportConfig material_node={cfg.material_node}, zmetal={cfg.export_zmetal}")

            # ── 几何体格式导出时，询问是否追加为变体 ──
            has_geom = cfg.export_ma or cfg.export_mb or cfg.export_fbx or cfg.export_obj or cfg.export_usd
            print(f"[UpdateAsset] has_geom={has_geom} ma={cfg.export_ma} mb={cfg.export_mb} "
                  f"fbx={cfg.export_fbx} obj={cfg.export_obj} usd={cfg.export_usd} sel={len(sel)}")
            if has_geom:
                from ..core.zasset_io import ZassetIO
                existing_variants = ZassetIO.read_variants(old_path)
                existing_versions = existing_variants.get("versions", [])
                choice, target_version = self._show_variant_or_rename_dialog(
                    cfg.asset_name, old_path, existing_versions)
                if choice == "cancel":
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return
                elif choice in ("add_lod", "new_version"):
                    # 变体导出需要选中物体
                    if not sel:
                        QtWidgets.QMessageBox.warning(
                            self, "更新资产",
                            "追加 LOD / 创建新版本需要导出几何体。\n\n"
                            "请先在 Maya 视口中选中要导出的物体，再点确定。")
                        return
                    # 变体导出：直接追加到已有 .zasset
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    if choice == "add_lod":
                        version_id = target_version or (existing_versions[-1]["id"] if existing_versions else "v1")
                        max_level = 0
                        for v in existing_versions:
                            if v.get("id") == version_id:
                                for l in v.get("lods", []):
                                    max_level = max(max_level, l.get("level", 0))
                        cfg.variant_mode = "add_lod"
                        cfg.variant_target_zasset = old_path
                        cfg.variant_target_version = version_id
                        cfg.variant_lod_level = max_level + 1
                        cfg.variant_lod_label = f"LOD{max_level + 1}"
                        cfg.export_zmetal = False
                        cfg.export_mcm = False
                        cfg.target_dir = old_path
                        print(f"[UpdateAsset] 变体追加 LOD: {old_path} v={version_id} lod={max_level + 1}")
                    else:
                        next_ver = len(existing_versions) + 1
                        cfg.variant_mode = "new_version"
                        cfg.variant_target_zasset = old_path
                        cfg.variant_version_id = f"v{next_ver}"
                        cfg.variant_version_tag = f"{next_ver}.0"
                        cfg.variant_version_label = f"版本 {next_ver}"
                        cfg.target_dir = old_path
                        # 新版本：保留用户勾选的 zmetal/mcm 状态，支持独立材质
                        print(f"[UpdateAsset] 变体新版本: {old_path} v{next_ver}")

                    orch = ExportOrchestrator(old_path)
                    result = orch.export_single(cfg)
                    if result.success:
                        self._on_refresh()
                        print(f"[UpdateAsset] 变体更新完成: {old_path}")
                    else:
                        QtWidgets.QMessageBox.warning(
                            self, "变体导出失败",
                            f"导出失败: {result.error}\n\n"
                            f"请确认在 Maya 中选中了要导出的几何体物体。")
                    return
                # "rename" → 继续原有替换流程

            orch = ExportOrchestrator(tmp_dir)  # 用临时目录
            result = orch.export_single(cfg)

            if result.success and result.files:
                new_zasset = result.files[0]
                if not os.path.isdir(new_zasset):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return

                # 保留旧缩略图（新导出不生成缩略图时）
                try:
                    from ..core.zasset_io import ZassetIO
                    old_thumb_name = None
                    old_thumb_data = None
                    for name in ("thumb.sicon", "thumb.aicon", "thumb.png"):
                        data = ZassetIO.read_file(old_path, name)
                        if data:
                            old_thumb_name = name
                            old_thumb_data = data
                            break
                    if old_thumb_data:
                        existing = ZassetIO.read_file(new_zasset, old_thumb_name)
                        if not existing:
                            ZassetIO.update_file_in_zasset(new_zasset, old_thumb_name, old_thumb_data)
                except Exception:
                    pass

                # 原子替换
                tmp_rename = old_path + ".update_tmp"
                try:
                    if os.path.exists(old_path):
                        os.replace(old_path, tmp_rename)
                    os.replace(new_zasset, old_path)
                    if os.path.exists(tmp_rename):
                        os.remove(tmp_rename)
                except Exception:
                    if os.path.exists(tmp_rename):
                        os.replace(tmp_rename, old_path)
                    raise

                # 保持原有 ID（标签和名称使用新导出值或原有值）
                import json
                from ..core.zasset_io import ZassetIO
                new_meta = ZassetIO.read_meta(old_path)
                new_meta["id"] = mat.id
                ZassetIO.update_meta_inplace(old_path, new_meta)

                self._on_refresh()
                print(f"[UpdateAsset] 更新完成: {old_path}")

            shutil.rmtree(tmp_dir, ignore_errors=True)

        dlg.exportConfigReady.connect(_on_update_ready)
        dlg.setModal(False)
        dlg.show()
        self._export_dialog = dlg

        dlg.finished.connect(self._on_export_dialog_closed)
        dlg.setWindowTitle("更新资产")

    def _on_create_asset(self):
        """资产导出流程 V3 — 每次点击重建对话框"""
        # 销毁已有对话框
        old = getattr(self, '_export_dialog', None)
        if old is not None:
            try:
                old.close()
                old.deleteLater()
            except RuntimeError:
                pass
        self._export_dialog = None

        # 获取 Maya 主窗口作为父级
        maya_parent = self._get_maya_parent()

        # 只获取不依赖 Maya 选中状态的数据
        try:
            common_tags = self._get_common_tags_for_current_category()
        except Exception:
            common_tags = []

        # 根据当前选中分类推断资产类型（子库）和显示名
        asset_type = self._get_current_asset_type()
        category_display = self._get_current_category_display()

        try:
            from ..utils.maya_plugin_checker import MayaPluginChecker
            plugin_statuses = MayaPluginChecker.get_all_statuses()
        except Exception:
            plugin_statuses = {}

        dlg = AssetCreateDialog(
            maya_parent,
            material_name="",
            category_display=category_display,
            common_tags=common_tags,
            asset_type=asset_type,
            associated_objects=[],
            material_count=0,
            plugin_statuses=plugin_statuses,
        )
        dlg.setModal(False)
        dlg.exportConfigReady.connect(self._on_export_config_ready)
        dlg.finished.connect(self._on_export_dialog_closed)
        dlg.show()
        self._export_dialog = dlg

    def _on_export_config_ready(self, base_config):
        """非模态对话框确认后 — 实时获取 Maya 状态，执行导出"""

        # ── ① 实时获取 Maya 选中状态 ──
        try:
            import maya.cmds as cmds
            sel = cmds.ls(selection=True, long=False) or []
        except Exception:
            cmds = None
            sel = []

        # ── ② 分类校验 + 自动路由 ──
        if self._active_mgr is self._project_mgr:
            cat_tree = self._proj_category_tree
        else:
            cat_tree = self._category_tree
        cat_id = cat_tree.get_active_category()
        root_lib = cat_tree.get_active_root_lib()
        if not cat_id or cat_id == "all":
            QtWidgets.QMessageBox.warning(self, "导出资产",
                "请先在左侧分类树中选择目标分类。")
            return

        _RESOLVED_DIR = ""
        cat_obj = self._active_mgr._categories.get(cat_id)
        is_sub_lib_root = (cat_id in self._active_mgr.ASSET_SUB_LIBRARIES)
        if cat_obj is not None and cat_obj.parent is None or is_sub_lib_root:
            top_dir = self._active_mgr.get_category_disk_path(cat_id, sub_lib_hint=root_lib)
            if top_dir:
                if is_sub_lib_root:
                    default_id = "AAAcustom"
                    default_name = "自定义"
                else:
                    default_id = f"{cat_id}_默认"
                    default_name = "默认"

                default_dir = os.path.join(top_dir, default_id)
                os.makedirs(default_dir, exist_ok=True)
                fmeta = os.path.join(default_dir, self._active_mgr.METADATA_FILENAME)
                if not os.path.isfile(fmeta):
                    self._active_mgr._json_handler.write_json(fmeta, {
                        "id": str(uuid.uuid4()),
                        "name_cn": default_name,
                    })
                if default_id not in self._active_mgr._categories:
                    self._active_mgr._categories[default_id] = Category(
                        id=default_id, name=default_name, name_cn=default_name, parent=cat_id,
                    )
                    if cat_obj is not None and default_id not in cat_obj.children:
                        cat_obj.children.append(default_id)
                _RESOLVED_DIR = default_dir
                cat_id = default_id

        if _RESOLVED_DIR:
            cat_dir = _RESOLVED_DIR
        else:
            cat_dir = self._active_mgr.get_category_disk_path(cat_id, sub_lib_hint=root_lib)
        if not cat_dir:
            QtWidgets.QMessageBox.warning(self, "导出资产",
                f"分类文件夹不存在: {cat_id}")
            return

        # ── ③ 解析选中项：分离材质节点与 DAG 物体 ──
        # 材质节点类型列表从 config.json 读取，用户可在设置 UI 中自定义
        _MATERIAL_TYPES = set(self._active_mgr._config.get("material_node_types", [
            'aiStandardSurface', 'standardSurface', 'lambert', 'blinn',
            'phong', 'openPBRSurface', 'pxrSurface', 'aiHair', 'aiSkin',
            'aiVolume', 'VRayMtl', 'RedshiftMaterial',
        ]))
        dag_objects = []
        direct_materials = []
        for item in sel:
            try:
                nt = cmds.nodeType(item)
            except Exception:
                dag_objects.append(item)
                continue
            if nt in _MATERIAL_TYPES:
                direct_materials.append(item)
            else:
                dag_objects.append(item)

        # ── ④ 导出模式 ──
        export_mode = base_config.export_mode  # "single" | "batch_auto" | "batch_semi"

        # ── ⑤ 构建 configs 列表 ──
        from ..utils.maya_utils import get_first_material_for_object
        from ..core.export_orchestrator import ExportConfig

        def _clone_base(field_map=None):
            """从 base_config 克隆一份 ExportConfig，覆盖指定字段"""
            kwargs = dict(
                asset_name=base_config.asset_name or (sel[0] if sel else "untitled"),
                name_cn=base_config.name_cn,
                category=cat_id,
                tags=list(base_config.tags or []),
                asset_type=base_config.asset_type,
                target_dir=cat_dir,
                material_node="",
                associated_objects=[],
                export_zmetal=base_config.export_zmetal,
                merge_zmetal=base_config.merge_zmetal,
                export_mcm=base_config.export_mcm,
                export_ma=base_config.export_ma,
                export_mb=base_config.export_mb,
                export_fbx=base_config.export_fbx,
                export_obj=base_config.export_obj,
                export_usd=base_config.export_usd,
                export_abc=base_config.export_abc,
                ani_frame_mode=base_config.ani_frame_mode,
                proxy_formats=list(base_config.proxy_formats or []),
                export_material_only=base_config.export_material_only,
                export_textures=getattr(base_config, 'export_textures', True),
                export_mode=export_mode,
                delay_ms=base_config.delay_ms,
                thumb_source=getattr(base_config, 'thumb_source', 'screenshot'),
                collect_associated=getattr(base_config, 'collect_associated', False),
            )
            if field_map:
                kwargs.update(field_map)
            return ExportConfig(**kwargs)

        configs = []

        def _resolve_light_shape(dag_obj):
            """如果 DAG 物体有灯光 shape 子节点，返回其 shape 节点名；否则返回空字符串"""
            try:
                shapes = cmds.listRelatives(dag_obj, shapes=True, fullPath=True) or []
                for shp in shapes:
                    shp_type = cmds.nodeType(shp)
                    # 方法1: getClassification（Maya 标准分类，兼容 Arnold/V-Ray/Redshift 等插件）
                    if cmds.getClassification(shp_type, satisfies="light"):
                        return shp
                    # 方法2: inherited types 包含 light（Maya 原生灯光）
                    inherited = cmds.nodeType(shp, inherited=True) or []
                    if isinstance(inherited, (list, tuple)) and 'light' in inherited:
                        return shp
                    # 方法3: 类型名包含 Light（宽松兜底）
                    if 'Light' in shp_type:
                        return shp
            except Exception:
                pass
            return ""

        if export_mode == "single":
            # ⑤ 合并模式：所有物体一个资产
            all_objects = list(dag_objects)
            first_mat = ""
            if direct_materials:
                first_mat = direct_materials[0]
            elif dag_objects:
                first_mat = get_first_material_for_object(dag_objects[0])
                # 回退：所选物体无关联材质，尝试作为可导出节点本身
                if not first_mat and dag_objects:
                    # 检测灯光：使用 shape 节点代替 transform，确保灯光参数完整导出
                    light_shape = _resolve_light_shape(dag_objects[0])
                    first_mat = light_shape or dag_objects[0]
            cfg = _clone_base({
                "material_node": first_mat or "",
                "associated_objects": all_objects,
            })
            print(f"[Export] material_node={first_mat} (type={cmds.nodeType(first_mat) if first_mat and cmds.objExists(first_mat) else '?'})")
            configs.append(cfg)

        else:
            # ⑤ 分批模式（batch_auto / batch_semi）：每物体独立资产
            # ⑤-A: DAG 物体
            for i, obj in enumerate(dag_objects):
                mat_node = get_first_material_for_object(obj)
                is_first = (i == 0 and not direct_materials)
                if base_config.export_material_only:
                    asset_name = mat_node or obj
                else:
                    asset_name = obj
                # 检测灯光：使用 shape 节点代替 transform
                if not mat_node:
                    light_shape = _resolve_light_shape(obj)
                    mat_node = light_shape
                cfg = _clone_base({
                    "asset_name": base_config.asset_name or asset_name,
                    "name_cn": base_config.name_cn if is_first else "",
                    "material_node": mat_node or "",
                    "associated_objects": [obj],
                })
                if mat_node:
                    print(f"[Export] batch material_node={mat_node} (type={cmds.nodeType(mat_node) if cmds.objExists(mat_node) else '?'})")
                configs.append(cfg)

            # ⑤-B: 直接选的材质节点
            for i, mat_name in enumerate(direct_materials):
                is_first = (i == 0 and not configs)
                cfg = _clone_base({
                    "asset_name": base_config.asset_name or mat_name,
                    "name_cn": base_config.name_cn if is_first else "",
                    "material_node": mat_name,
                    "associated_objects": [],
                })
                configs.append(cfg)

        # ── ⑥ 同名冲突检测 ──
        conflict_policy = self._load_name_conflict_policy()
        for i, cfg in enumerate(configs):
            safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
            asset_zasset = os.path.join(cat_dir, f"{safe_name}.zasset")
            if not os.path.exists(asset_zasset):
                continue

            if conflict_policy.get("remember_choice"):
                if conflict_policy["mode"] == NameConflictDialog.MODE_AUTO_RENAME:
                    new_name = self._resolve_auto_rename(safe_name, cat_dir)
                    configs[i].asset_name = new_name
                    print(f"[NameConflict] 自动重命名: {safe_name} → {new_name}")
                elif conflict_policy["mode"] == NameConflictDialog.MODE_MANUAL:
                    new_name, ok = QtWidgets.QInputDialog.getText(
                        self, "资产重命名",
                        f"资产「{cfg.asset_name}」已存在，请输入新名称：", text=""
                    )
                    if ok and new_name.strip():
                        configs[i].asset_name = new_name.strip()
                        print(f"[NameConflict] 手动输入: {safe_name} → {new_name.strip()}")
                    else:
                        fallback = self._resolve_auto_rename(safe_name, cat_dir)
                        configs[i].asset_name = fallback
                        print(f"[NameConflict] 手动取消/无输入，降级自动重命名: {safe_name} → {fallback}")
            else:
                conflict_dlg = NameConflictDialog(cfg.asset_name, self)
                if not conflict_dlg.exec():
                    print(f"[NameConflict] 用户取消导出")
                    return

                mode, new_name, remember = conflict_dlg.result()
                if mode == NameConflictDialog.MODE_AUTO_RENAME:
                    resolved = self._resolve_auto_rename(safe_name, cat_dir)
                    configs[i].asset_name = resolved
                    print(f"[NameConflict] 自动重命名: {safe_name} → {resolved}")
                elif mode == NameConflictDialog.MODE_MANUAL:
                    configs[i].asset_name = new_name
                    print(f"[NameConflict] 手动重命名: {safe_name} → {new_name}")

                if remember:
                    self._save_name_conflict_policy(mode, new_name if mode == NameConflictDialog.MODE_MANUAL else "")

        # ── ⑦ 保存状态 + 启动导出 ──
        self._asset_configs = configs
        self._asset_queue = list(configs)
        self._asset_cat_dir = cat_dir
        self._asset_results = []
        self._screenshot_rect = None

        from .batch_progress_overlay import BatchProgressOverlay

        is_real_batch = export_mode in ("batch_auto", "batch_semi") and len(configs) > 1
        if is_real_batch:
            self._batch_progress = BatchProgressOverlay(self)
            self._batch_progress.cancelled.connect(self._on_batch_cancelled)
            self._batch_progress.skip_current.connect(self._on_batch_skip_current)
        else:
            self._batch_progress = None

        self._process_next_asset_v2(0)

    def _on_export_dialog_closed(self):
        """对话框关闭后清理引用"""
        self._export_dialog = None

    # ── 视口管理：模型缩略图截图前的视口设置 ─────────────────

    def _prepare_viewport_for_model_thumbnail(self, cfg):
        """doHideObjects 独显（不框显）

        为模型资产截图做准备：选中 DAG 物体 → 隐藏未选中（doHideObjects false）
        → 取消选择。保留用户当前的视口构图。
        截图后 _restore_viewport_after_thumbnail 用 showLastHidden 恢复。
        """
        import maya.cmds as cmds
        from maya import mel

        try:
            # 1. 从 config 中取出关联物体，过滤出 DAG 物体
            dag_objs = [o for o in cfg.associated_objects
                        if 'dagNode' in cmds.nodeType(o, inherited=True)]

            if not dag_objs:
                print(f"[Thumbnail] 无 DAG 物体 (associated_objects={cfg.associated_objects})")
                return False

            # 2. 选中 DAG 物体
            cmds.select(dag_objs, replace=True)

            # 3. 隐藏未选中物体（独显），但不框显，保留用户视口构图
            mel.eval('doHideObjects false')

            # 4. 取消选择（避免取景框被选中高亮干扰）
            cmds.select(clear=True)
            print(f"[Thumbnail] 视口已设置: hidden unselected, {len(dag_objs)} objects (no frame)")
            return True

        except Exception as e:
            print(f"[Thumbnail] _prepare_viewport_for_model_thumbnail 异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _restore_viewport_after_thumbnail(self):
        """恢复视口：显示上次隐藏的对象（showLastHidden）"""
        from maya import mel

        try:
            mel.eval('showLastHidden')
            print(f"[Thumbnail] 视口已恢复（showLastHidden）")
        except Exception as e:
            print(f"[Thumbnail] _restore_viewport_after_thumbnail 异常: {e}")

    def _process_next_asset_v2(self, index: int):
        """V2 批量资产处理——支持首资产交互、后续自动"""
        if index >= len(self._asset_configs):
            self._finish_batch_export()
            return

        if self._batch_progress and self._batch_progress.is_cancelled():
            self._finish_batch_export()
            return

        cfg = self._asset_configs[index]
        total = len(self._asset_configs)

        if self._batch_progress:
            self._batch_progress.update_progress(index + 1, total, cfg.asset_name)

        # ── 全自动（材质/模型统一流程） ──
        if cfg.export_mode == "batch_auto":
            if index == 0:
                # 首资产：交互定位取景框
                if self._batch_progress:
                    self._batch_progress.show()
                    self._batch_progress.position_near(self)
                self._process_single_asset_interactive(cfg, index)
            else:
                # 后续资产：自动复用前一次截图位置
                if self._screenshot_rect:
                    cfg.screenshot_rect = self._screenshot_rect
                self._process_single_asset_auto(cfg, index)

        # ── 半自动：每资产都弹出截图 UI，其他自动 ──
        elif cfg.export_mode == "batch_semi":
            if self._batch_progress:
                self._batch_progress.show()
                self._batch_progress.position_near(self)
            self._process_single_asset_interactive(cfg, index)

        # ── 单资产：交互截图（不变） ──
        else:  # single
            self._process_single_asset_interactive(cfg, index)

    def _process_single_asset_interactive(self, cfg, index):
        """首资产：先导出数据，再用截图 overlay（复用已有 UI，不重建不位移）"""

        # ① 先导出资产数据
        from ..integration.export_connector import ExportConnector
        thumb_source = getattr(cfg, 'thumb_source', 'screenshot')
        skip_thumb = thumb_source != 'screenshot'
        export_result = ExportConnector.export_asset(cfg, skip_thumbnail=skip_thumb)
        self._asset_results.append(export_result)
        # 记录待生成的缩略图
        if skip_thumb and export_result.success:
            pending = getattr(self, '_pending_thumbnails', None)
            if pending is None:
                self._pending_thumbnails = []
            safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
            self._pending_thumbnails.append((cfg, safe_name))

        if not export_result.success:
            print(f"[AssetExport] 导出失败: {cfg.asset_name} — {export_result.error}")
            self._refresh_after_export()
            QtCore.QTimer.singleShot(200, lambda: self._process_next_asset_v2(index + 1))
            return

        # 选中材质节点（让 Hypershade 预览更新）
        try:
            import maya.cmds as cmds
            if cfg.material_node and cmds.objExists(cfg.material_node):
                cmds.select(cfg.material_node, replace=True)
        except Exception:
            pass

        # 记录最后导出路径（供 _refresh_after_export 诊断用）
        safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
        self._last_export_dir = os.path.join(self._asset_cat_dir, safe_name)
        self._last_export_name = safe_name

        # ②½ 导出完成后立刻刷新网格，让资产卡片立即显示（占位缩略图），
        #     截图完成后 on_captured 回调中的 _refresh_after_export 只更新缩略图
        self._refresh_after_export()

        # ②¾ 模型类：截面前设置视口（fitAllPanels + isolateSelect 独显）
        # 单资产模式不自动隐藏/独显，由用户手动隔离物体
        if not cfg.export_material_only and cfg.export_mode != "single":
            self._prepare_viewport_for_model_thumbnail(cfg)

        # ③ 截图 — 根据 thumb_source 分派
        thumb_source = getattr(cfg, 'thumb_source', 'screenshot')
        if thumb_source != "screenshot":
            # 非截屏工具模式：playblast/render 已在 _stage_thumbnail 中完成
            self._restore_viewport_after_thumbnail()
            self._refresh_after_export()
            self._process_next_asset_v2(index + 1)
            return

        # ③ 截图 — 复用已有 overlay，绝不重建（保持用户定位）
        # 优先扫描 Qt 控件树：跨插件重载后旧 overlay 仍在 Maya 界面中
        from .thumbnail_capture_overlay import ThumbnailCaptureOverlay

        existing = ThumbnailCaptureOverlay.find_existing()
        if existing is not None:
            # 验证 Python 包装器是否"活着"（插件重载后可能返回裸 C++ 包装器，
            # 丢失了 selection_rect / toolbar 等实例属性）
            if hasattr(existing, 'selection_rect') and hasattr(existing, 'toolbar'):
                self._asset_overlay = existing
                need_show = False
                # 确保 toolbar 可见（跨会话后可能被 Qt 清理）
                if not existing.toolbar.isVisible():
                    existing.toolbar.show()
                    existing.toolbar.raise_()
            else:
                # 僵尸包装器：关掉旧 widget，走新建流程
                existing.close()
                existing.deleteLater()
                existing = None

        if existing is None:
            if (not hasattr(self, '_asset_overlay') or
                  self._asset_overlay is None):
                self._asset_overlay = ThumbnailCaptureOverlay(keep_alive=True)
                need_show = True
            else:
                need_show = False

        safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
        material_dir = os.path.join(self._asset_cat_dir, safe_name)

        # Phase 5: 不再给 overlay 设置 save_path_override，避免写入独立 .sicon 文件
        # 缩略图由 on_captured 回调直接写入 .zasset 内部 thumb.sicon
        self._asset_overlay.save_path_override = None
        self._asset_overlay.save_path = material_dir

        def on_captured(pixmap):
            if not pixmap.isNull():
                # 写入 .zasset 内部 thumb.sicon
                zasset_path = material_dir + ".zasset"
                if os.path.isdir(zasset_path):
                    try:
                        ba = QtCore.QByteArray()
                        buf = QtCore.QBuffer(ba)
                        buf.open(QtCore.QIODeviceBase.WriteOnly)
                        pixmap.save(buf, "PNG")
                        buf.close()
                        from ..core.zasset_io import ZassetIO
                        ZassetIO.write_thumbnail(zasset_path, bytes(ba))
                        print(f"[AssetExport] 缩略图已写入 .zasset: {zasset_path}")
                    except Exception as e:
                        print(f"[AssetExport] 写入 .zasset 缩略图失败: {e}")

                try:
                    sel_rect = self._asset_overlay.selection_rect
                    screen = QtWidgets.QApplication.primaryScreen()
                    if screen:
                        sw, sh = screen.size().width(), screen.size().height()
                        rx = sel_rect.x() / sw
                        ry = sel_rect.y() / sh
                        rw = sel_rect.width() / sw
                        rh = sel_rect.height() / sh
                        self._screenshot_rect = (rx, ry, rw, rh)
                except Exception:
                    pass
            # 模型类：截图后恢复视口（关闭 isolate）
            if not cfg.export_material_only:
                self._restore_viewport_after_thumbnail()
            self._refresh_after_export()
            QtCore.QTimer.singleShot(200, lambda: self._process_next_asset_v2(index + 1))

        def on_skip():
            # 模型类：跳过截图也恢复视口
            if not cfg.export_material_only:
                self._restore_viewport_after_thumbnail()
            self._refresh_after_export()
            QtCore.QTimer.singleShot(200, lambda: self._process_next_asset_v2(index + 1))

        def on_reset_rect():
            self._screenshot_rect = None
            print("[AssetExport] 截图选区已重置")

        try: self._asset_overlay.captured.disconnect()
        except: pass
        try: self._asset_overlay.recordingFinished.disconnect()
        except: pass
        try: self._asset_overlay.skipRequested.disconnect()
        except: pass
        try: self._asset_overlay.resetRectRequested.disconnect()
        except: pass

        self._asset_overlay.captured.connect(on_captured)
        self._asset_overlay.skipRequested.connect(on_skip)
        self._asset_overlay.resetRectRequested.connect(on_reset_rect)

        # 只在首次创建时才 show，后续复用绝不重新显示/移动
        if need_show:
            self._asset_overlay.show()
        # 但如果没显示（例如被意外关闭），恢复它
        elif not self._asset_overlay.isVisible():
            self._asset_overlay.show()

    def _process_single_asset_auto(self, cfg, index):
        """后续资产：自动导出 + 复用截图选区"""
        from ..integration.export_connector import ExportConnector

        # 直接导出（无对话框）
        thumb_source = getattr(cfg, 'thumb_source', 'screenshot')
        skip_thumb = thumb_source != 'screenshot'
        result = ExportConnector.export_asset(cfg, skip_thumbnail=skip_thumb)
        self._asset_results.append(result)
        if skip_thumb and result.success:
            safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
            self._pending_thumbnails.append((cfg, safe_name))

        # 记录最后导出路径
        safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
        self._last_export_dir = os.path.join(self._asset_cat_dir, safe_name)
        self._last_export_name = safe_name

        # 导出完成后立刻刷新网格，让资产卡片立即可见（截图后 on_captured_auto 只更新缩略图）
        self._refresh_after_export()

        if not result.success:
            print(f"[AssetExport] 失败: {cfg.asset_name} — {result.error}")

        # 非截屏工具模式：缩略图已在 _stage_thumbnail 中生成，跳过 overlay
        thumb_source = getattr(cfg, 'thumb_source', 'screenshot')
        if thumb_source != "screenshot":
            self._process_next_asset_v2(index + 1)
            return

        # 选中材质节点（让 Hypershade 预览更新，为截图就绪）
        try:
            import maya.cmds as cmds
            if cfg.material_node and cmds.objExists(cfg.material_node):
                cmds.select(cfg.material_node, replace=True)
                cmds.refresh(force=True)  # 触发预览立即开始渲染
        except Exception:
            pass

        # 模型类：自动截图前设置视口（fitAllPanels + isolateSelect 独显）
        if not cfg.export_material_only:
            self._prepare_viewport_for_model_thumbnail(cfg)

        # 如果有截图 overlay 且已保存选区，自动截图
        if self._asset_overlay and self._screenshot_rect:
            safe_name = cfg.asset_name.replace(':', '_').replace('/', '_')
            material_dir = os.path.join(self._asset_cat_dir, safe_name)

            # Phase 5: 不再设置 save_path_override，避免写入独立 .sicon 文件
            self._asset_overlay.save_path_override = None
            self._asset_overlay.save_path = material_dir

            def on_captured_auto(pixmap):
                if not pixmap.isNull():
                    # 写入 .zasset 内部 thumb.sicon
                    zasset_path = material_dir + ".zasset"
                    if os.path.isdir(zasset_path):
                        try:
                            ba = QtCore.QByteArray()
                            buf = QtCore.QBuffer(ba)
                            buf.open(QtCore.QIODeviceBase.WriteOnly)
                            pixmap.save(buf, "PNG")
                            buf.close()
                            from ..core.zasset_io import ZassetIO
                            ZassetIO.write_thumbnail(zasset_path, bytes(ba))
                            print(f"[AssetExport] 缩略图已写入 .zasset: {zasset_path}")
                        except Exception as e:
                            print(f"[AssetExport] 写入 .zasset 缩略图失败: {e}")
                # 模型类：截图后恢复视口（关闭 isolate）
                if not cfg.export_material_only:
                    self._restore_viewport_after_thumbnail()
                self._refresh_after_export()
                QtCore.QTimer.singleShot(100, lambda: self._process_next_asset_v2(index + 1))

            try: self._asset_overlay.captured.disconnect()
            except: pass
            self._asset_overlay.captured.connect(on_captured_auto)

            # 构建截图坐标
            try:
                screen = QtWidgets.QApplication.primaryScreen()
                if screen:
                    sw, sh = screen.size().width(), screen.size().height()
                    rx, ry, rw, rh = self._screenshot_rect
                    x, y = int(rx * sw), int(ry * sh)
                    w, h = int(rw * sw), int(rh * sh)
                    self._asset_overlay.selection_rect = QtCore.QRect(x, y, w, h)
                    # 等 N 秒后截图（不刷新不轮询，让 Arnold 异步渲染自然完成）
                    delays_ms = max(cfg.delay_ms, 300)
                    QtCore.QTimer.singleShot(
                        delays_ms,
                        lambda: self._take_auto_screenshot(cfg.material_node, index))
                    return
            except Exception:
                pass

        # 回退：无截图
        self._refresh_after_export()
        QtCore.QTimer.singleShot(100, lambda: self._process_next_asset_v2(index + 1))

    def _take_auto_screenshot(self, material_node, index):
        """延迟后执行自动截图 — 截图前确保材质节点选中 + 刷新预览"""
        try:
            import maya.cmds as cmds
            if material_node and cmds.objExists(material_node):
                cmds.select(material_node, replace=True)
                # 强制刷新 HyperShade / 属性编辑器预览（解决 Arnold 渲染队列合并问题）
                from maya import mel
                mel.eval("refreshAE;")
                mel.eval("refreshEditorTemplates;")
        except Exception:
            pass
        QtWidgets.QApplication.processEvents()
        self._asset_overlay._on_screenshot()

    def _refresh_after_export(self, asset_dir="", safe_name=""):
        """导出后刷新 UI——等待 .zasset 文件就绪 + 诊断"""
        if not asset_dir and not safe_name:
            # 从实例变量中读取最后导出路径
            asset_dir = getattr(self, '_last_export_dir', '')
            safe_name = getattr(self, '_last_export_name', '')
        if asset_dir and safe_name:
            # .zasset 模式：资产是单个 .zasset 文件，缩略图在其内部
            zasset_path = f"{asset_dir}.zasset"
            for retry in range(5):
                if os.path.isdir(zasset_path):
                    break
                import time
                time.sleep(0.05)
            if not os.path.isdir(zasset_path):
                print(f"[AssetExport] ⚠ .zasset 文件未就绪: {zasset_path}")
        try:
            self._thumbnail_grid._thumb_cache.clear()
            self._thumbnail_grid._clear_grid()
            self._refresh_keep_current(reload_materials=True)
        except Exception:
            import traceback
            traceback.print_exc()

    def _generate_batch_thumbnails(self):
        """延迟生成所有待处理的缩略图（拍屏/渲染图模式）"""
        pending = getattr(self, '_pending_thumbnails', None)
        if not pending:
            return
        self._pending_thumbnails = None
        print(f"[Batch] 开始生成 {len(pending)} 个缩略图...")
        from ..core.export_orchestrator import ExportOrchestrator
        from ..core.zasset_io import ZassetIO
        orch = ExportOrchestrator(self._asset_cat_dir)
        for cfg, safe_name in pending:
            try:
                thumb_source = getattr(cfg, 'thumb_source', 'screenshot')
                if thumb_source == 'playblast':
                    result_path = orch._do_playblast_thumbnail(cfg, self._asset_cat_dir, safe_name)
                elif thumb_source == 'render':
                    result_path = orch._do_render_thumbnail(cfg, self._asset_cat_dir, safe_name)
                else:
                    continue
                if result_path:
                    zasset_path = os.path.join(self._asset_cat_dir, f"{safe_name}.zasset")
                    if not os.path.isdir(zasset_path):
                        continue
                    # 注入 .sicon 和 .mp4（如果存在）
                    for thumb_name in ("thumb.sicon", "thumb.mp4"):
                        thumb_file = os.path.join(self._asset_cat_dir, thumb_name)
                        if os.path.isfile(thumb_file):
                            with open(thumb_file, 'rb') as f:
                                ZassetIO.update_file_in_zasset(zasset_path, thumb_name, f.read())
                            os.remove(thumb_file)
            except Exception as e:
                print(f"[Batch] 缩略图生成失败: {cfg.asset_name} — {e}")
        self._refresh_after_export()
        print(f"[Batch] 缩略图生成完成")

    def _finish_batch_export(self):
        """批量导出完成"""
        # 首先生成延迟的缩略图（拍屏/渲染图模式）
        self._generate_batch_thumbnails()
        if self._batch_progress:
            self._batch_progress.hide()

        results = getattr(self, '_asset_results', [])
        total = len(self._asset_configs) if hasattr(self, '_asset_configs') else 0

        if not results and total == 0:
            return

        # 单资产：不弹窗，静默完成
        if total <= 1:
            self._cleanup_after_export()
            return

        success_count = sum(1 for r in results if r.success)
        failed_count = sum(1 for r in results if not r.success)

        if failed_count == 0:
            QtWidgets.QMessageBox.information(self, "导出资产",
                f"导出完成！\n\n✅ 全部成功: {total} 个资产")
        else:
            failed_details = "\n".join(
                f"• {r.asset_name}: {r.error[:80] or '未知错误'}"
                for r in results if not r.success
            )
            msg = (
                f"导出完成！\n\n"
                f"✅ 成功: {success_count} 个\n"
                f"❌ 失败: {failed_count} 个\n\n"
                f"── 失败详情 ──\n"
                f"{failed_details}"
            )
            reply = QtWidgets.QMessageBox.question(
                self, "导出资产", msg,
                QtWidgets.QMessageBox.StandardButton.Retry
                | QtWidgets.QMessageBox.StandardButton.Close,
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Retry:
                self._retry_failed_exports()

        self._cleanup_after_export()

    def _cleanup_after_export(self):
        """清理导出后的临时状态"""
        if self._batch_progress:
            self._batch_progress.hide()
            self._batch_progress = None
        # 不关闭 overlay，用户可以手动关闭

    def _retry_failed_exports(self):
        """重试失败项"""
        results = getattr(self, '_asset_results', [])
        failed_configs = []
        for i, r in enumerate(results):
            if not r.success and i < len(self._asset_configs):
                failed_configs.append(self._asset_configs[i])

        if not failed_configs:
            QtWidgets.QMessageBox.information(self, "导出资产", "没有需要重试的项目。")
            return

        # 重新处理失败项
        self._asset_queue = list(failed_configs)
        self._asset_results = [r for r in results if r.success]
        self._asset_configs = failed_configs

        if self._batch_progress is None:
            from .batch_progress_overlay import BatchProgressOverlay
            self._batch_progress = BatchProgressOverlay(self)
            self._batch_progress.cancelled.connect(self._on_batch_cancelled)

        self._batch_progress.reset()
        self._batch_progress.show()
        self._batch_progress.position_near(self)
        self._process_next_asset_v2(0)

    def _on_batch_cancelled(self):
        """批量导出被取消"""
        print("[AssetExport] 批量导出已取消")
        if self._batch_progress:
            self._batch_progress.hide()

    def _on_batch_skip_current(self):
        """跳过当前资产"""
        print("[AssetExport] 跳过当前资产")
        # 通过设置 skip_thumbnail 标志跳过
        if self._asset_queue:
            self._asset_queue[0].skip_thumbnail = True

    # ── 设置 ──────────────────────────────────────────

    def _on_help(self):
        """打开使用帮助"""
        import webbrowser
        import os
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_path = os.path.join(plugin_root, "Assets", "help", "help.html")
        if os.path.isfile(help_path):
            webbrowser.open("file:///" + help_path.replace(os.sep, "/"))
        else:
            print("[Help] 帮助文件未找到:", help_path)

    def _on_preview_node(self, file_path):
        """预览节点文件 — 启动 ma-zmetal 节点编辑器"""
        import os
        import sys
        if not os.path.isfile(file_path):
            print(f"[NodePreview] 文件不存在: {file_path}")
            return

        try:
            plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            utils_dir = os.path.join(plugin_root, "utils")
            if utils_dir not in sys.path:
                sys.path.insert(0, utils_dir)

            import importlib.util
            editor_path = os.path.join(utils_dir, "预览ma节点连接.py")
            spec = importlib.util.spec_from_file_location("node_editor_preview", editor_path)
            if spec is None:
                print("[NodePreview] 无法加载节点编辑器模块")
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, 'main'):
                print(f"[NodePreview] 启动节点编辑器: {file_path}")
                module.main(file_path=file_path)
            else:
                print("[NodePreview] 节点编辑器模块缺少 main() 函数")
        except Exception as e:
            import traceback
            print(f"[NodePreview] 启动失败: {e}")
            traceback.print_exc()

    def _on_import_zlight_as_renderer(self, zasset_path: str, renderer: str):
        """以指定渲染器导入 zlight 灯光资产"""
        import json, tempfile, os
        from squirrel_asset_manager.core.zasset_io import ZassetIO
        from squirrel_asset_manager.core.light_io import import_lights_from_json
        from squirrel_asset_manager.integration.import_executor import _copy_zlight_dependencies

        try:
            all_names = ZassetIO.list_contents(zasset_path)
            zlight_name = None
            for n in all_names:
                if n.endswith(".zlight") and os.path.dirname(n) == "":
                    zlight_name = n
                    break
            if not zlight_name:
                for n in all_names:
                    if n.endswith(".zlight"):
                        zlight_name = n
                        break
            if not zlight_name:
                print(f"[ImportZlight] .zasset 不含 .zlight 文件")
                return

            # ── 读取元数据获取 asset_id ──
            meta_data = ZassetIO.read_meta(zasset_path) or {}
            asset_id = meta_data.get("id", "")
            asset_name = meta_data.get("name") or os.path.splitext(os.path.basename(zasset_path))[0]

            zlight_data = json.loads(ZassetIO.read_file(zasset_path, zlight_name))
            # 复制依赖文件到项目目录
            zlight_data = _copy_zlight_dependencies(zlight_data, asset_name, asset_id, zasset_path)

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zlight")
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(zlight_data, f, indent=2, ensure_ascii=False)

            count, created = import_lights_from_json(tmp_path, renderer=renderer)
            os.unlink(tmp_path)

            if count > 0:
                print(f"[ImportZlight] 已创建 {count} 个 {renderer} 灯光")
                try:
                    import maya.cmds as cmds
                    cmds.select(created, replace=True)
                except Exception:
                    pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ImportZlight] 导入失败: {e}")

    def _on_apply_light_to_selection(self, zasset_path: str):
        """将 zlight 参数应用到 Maya 场景中选中的灯光节点"""
        import json, os
        from squirrel_asset_manager.core.zasset_io import ZassetIO
        from squirrel_asset_manager.core.light_io import (_dict_to_lightdata,
                                                          apply_light_params_to_shape,
                                                          _detect_renderer)
        from squirrel_asset_manager.integration.import_executor import _copy_zlight_dependencies

        try:
            import maya.cmds as cmds

            # ── 获取选中灯光 shape ──
            selection = cmds.ls(selection=True, long=True) or []
            light_shapes = []
            for obj in selection:
                ntype = cmds.nodeType(obj)
                if cmds.getClassification(ntype, satisfies="light"):
                    light_shapes.append(obj)
                # 检查 transform 下的 shape
                shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
                for s in shapes:
                    if cmds.getClassification(cmds.nodeType(s), satisfies="light"):
                        light_shapes.append(s)

            if not light_shapes:
                print("[ApplyLight] 未选中任何灯光节点")
                return

            # ── 读取 zlight 数据 ──
            all_names = ZassetIO.list_contents(zasset_path)
            zlight_name = None
            for n in all_names:
                if n.endswith(".zlight") and os.path.dirname(n) == "":
                    zlight_name = n
                    break
            if not zlight_name:
                for n in all_names:
                    if n.endswith(".zlight"):
                        zlight_name = n
                        break
            if not zlight_name:
                print(f"[ApplyLight] .zasset 不含 .zlight 文件")
                return

            zlight_data = json.loads(ZassetIO.read_file(zasset_path, zlight_name))

            # ── 复制依赖文件 ──
            meta_data = ZassetIO.read_meta(zasset_path) or {}
            asset_id = meta_data.get("id", "")
            asset_name = meta_data.get("name") or os.path.splitext(os.path.basename(zasset_path))[0]
            zlight_data = _copy_zlight_dependencies(zlight_data, asset_name, asset_id, zasset_path)

            # ── 解析灯光数据 ──
            lights = zlight_data.get("lights", [])
            if not lights:
                print("[ApplyLight] zlight 无灯光数据")
                return

            renderer = _detect_renderer()
            applied = 0
            for light_dict in lights:
                ld = _dict_to_lightdata(light_dict)
                for shape in light_shapes:
                    if apply_light_params_to_shape(shape, ld, renderer):
                        applied += 1

            print(f"[ApplyLight] 已应用 {applied} 处灯光参数 (from {os.path.basename(zasset_path)})")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ApplyLight] 应用失败: {e}")

    def _on_settings(self):
        """打开设置窗口（非模态单例）"""
        # 单例检查：已存在且可见时直接前置
        if hasattr(self, '_settings_dialog') and self._settings_dialog is not None:
            try:
                if self._settings_dialog.isVisible():
                    self._settings_dialog.raise_()
                    self._settings_dialog.activateWindow()
                    return
            except RuntimeError:
                pass

        dialog = SettingsDialog(self, current_settings=self._app_settings)
        self._settings_dialog = dialog
        dialog.settingsChanged.connect(self._on_settings_changed)
        dialog.finished.connect(lambda: setattr(self, '_settings_dialog', None))
        dialog.setModal(False)
        dialog.show()

    def _on_settings_changed(self, settings):
        self._app_settings.update(settings)
        if self._settings_mgr:
            for k, v in settings.items():
                self._settings_mgr.set(k, v)

        # 库路径变更 → 更新下拉框
        if "library_paths" in settings:
            self._libraries = settings["library_paths"]
            self._populate_library_combo()

        # 按变更项增量更新
        font_changed = "font_size" in settings
        path_changed = False
        config_changed = any(k in settings for k in (
            "default_thumb_size", "default_view", "texture_suffixes",
            "common_tags", "asset_file_extensions", "geometry_extensions",
            "image_extensions", "material_node_types", "sub_libraries",
            "default_sub_categories", "export_defaults"))

        if font_changed:
            self._apply_styles()
            font_size = self._app_settings.get("font_size", 13)
            self._set_tags_font_size(font_size)

        if config_changed:
            self._material_manager.reload_config()
            if hasattr(self, '_project_mgr'):
                self._project_mgr.reload_config()
            self._refresh_keep_current()
            self._refresh_search_bar_tags(self._current_root_lib)

        default_thumb_size = settings.get("default_thumb_size")
        if default_thumb_size is not None:
            self._thumbnail_grid.set_thumb_size(default_thumb_size)
        default_view = settings.get("default_view")
        if default_view is not None:
            self._switch_view(self.VIEW_ICON if default_view == "icon" else self.VIEW_LIST)

        new_path = settings.get("last_library_path", "")
        if new_path and os.path.normpath(new_path) != os.path.normpath(self._material_manager.get_library_path() or ""):
            self._material_manager.load_library(new_path)
            self._use_mock = (self._material_manager.get_material_count() == 0)
            self._load_data()
            self._update_status_bar()
            path_changed = True

        # 仅字体变更且无其他配置变更时，不需要全量刷新
        if font_changed and not config_changed and not path_changed:
            pass  # _apply_styles 已处理

    # ── 窗口状态 ─────────────────────────────────────

    def _save_ui_state(self):
        """保存完整的 UI 状态到 settings"""
        if not self._settings_mgr:
            return
        state = {
            "window_state": {"width": self.width(), "height": self.height()},
            "active_category": self._category_tree.get_active_category(),
            "active_root_lib": self._category_tree.get_active_root_lib(),
            "expanded_ids": self._category_tree.get_expanded_ids(),
            "project_category": self._proj_category_tree.get_active_category(),
            "proj_root_lib": self._proj_category_tree.get_active_root_lib(),
            "proj_expanded_ids": self._proj_category_tree.get_expanded_ids(),
            "left_tab_index": self._left_panel_container.currentIndex(),
            "view_mode": "icon" if self._current_view == self.VIEW_ICON else "list",
            "thumb_size": self._thumb_slider.value(),
            "sort_order": self._sort_combo.currentIndex(),
            "panel_visible": self._left_panel_visible,
            "splitter_sizes": list(self.findChild(QtWidgets.QSplitter, "mainSplitter").sizes())
                if self.findChild(QtWidgets.QSplitter, "mainSplitter") else [],
        }
        for k, v in state.items():
            self._settings_mgr.set(k, v)
        self._active_mgr.save_favorites()

    def _restore_ui_state(self):
        """从 settings 恢复完整的 UI 状态"""
        if not self._settings_mgr:
            return
        s = self._app_settings

        # 恢复视图模式
        view_mode = s.get("view_mode", "icon")
        self._switch_view(self.VIEW_ICON if view_mode == "icon" else self.VIEW_LIST)

        # 恢复缩略图大小（setValue 会触发 signal → _on_thumb_slider_changed → set_thumb_size + _auto_columns）
        thumb_size = s.get("thumb_size", 2)
        self._thumb_slider.blockSignals(False)
        self._thumb_slider.setValue(thumb_size)

        # 恢复排序
        sort_order = s.get("sort_order", 0)
        self._sort_combo.setCurrentIndex(sort_order)

        # 恢复面板可见性
        panel_visible = s.get("panel_visible", True)
        self._left_panel_visible = panel_visible
        self._left_panel_container.setVisible(panel_visible)

        # 恢复分栏宽度
        splitter_sizes = s.get("splitter_sizes", [])
        if splitter_sizes:
            sp = self.findChild(QtWidgets.QSplitter, "mainSplitter")
            if sp and len(splitter_sizes) == sp.count():
                try:
                    sp.setSizes(splitter_sizes)
                except Exception:
                    pass

        # 恢复左侧选项卡
        tab_index = s.get("left_tab_index", 0)
        if 0 <= tab_index < self._left_panel_container.count():
            self._left_panel_container.setCurrentIndex(tab_index)

        # 根据当前选项卡恢复对应的分类树
        if tab_index == 1:
            # 项目选项卡：先加载项目库再恢复选中
            self._load_project_library()
            proj_cat = s.get("project_category", "")
            proj_root_lib = s.get("proj_root_lib", "")
            proj_expanded = s.get("proj_expanded_ids", [])
            if proj_expanded:
                self._proj_category_tree.set_expanded_ids(proj_expanded)
            if proj_cat and proj_cat != "all":
                self._proj_category_tree._select_by_id(proj_cat, proj_root_lib if proj_root_lib else None)
                self._proj_category_tree._active_category = proj_cat
                desc_ids = self._proj_category_tree.get_descendant_ids(proj_cat)
                self._active_mgr = self._project_mgr
                self._thumbnail_grid.set_manager(self._project_mgr)
                self._on_refresh_project()
        else:
            # 分类/收藏选项卡：恢复分类树的选中
            expanded_ids = s.get("expanded_ids", [])
            if expanded_ids:
                self._category_tree.set_expanded_ids(expanded_ids)
            active_cat = s.get("active_category", "")
            active_root_lib = s.get("active_root_lib", "")
            # _load_data 已同步加载保存的分类，无需重复加载
            if active_cat and active_cat != "all" and active_cat != self._category_tree.get_active_category():
                self._category_tree._select_by_id(active_cat, active_root_lib if active_root_lib else None)
                self._category_tree._active_category = active_cat
                desc_ids = self._category_tree.get_descendant_ids(active_cat)
                root_lib = active_root_lib if active_root_lib else self._detect_root_library(active_cat)
                self._active_mgr = self._material_manager
                self._thumbnail_grid.set_manager(self._material_manager)
                self._on_category_selected(active_cat, desc_ids, root_lib)

    # ── 同名冲突策略 ─────────────────────────────────────

    def _load_name_conflict_policy(self) -> dict:
        """从 config.json 加载同名冲突处理策略"""
        import json
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "Assets", "preset", "config.json"
        )
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return cfg.get("name_conflict_policy",
                               {"remember_choice": False, "mode": "prompt"})
        except Exception as e:
            print(f"[NameConflict] 加载策略失败: {e}")
        return {"remember_choice": False, "mode": "prompt"}

    def _save_name_conflict_policy(self, mode: str, manual_name: str = ""):
        """保存同名冲突策略到 config.json"""
        import json
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "Assets", "preset", "config.json"
        )
        try:
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["name_conflict_policy"] = {
                "remember_choice": True,
                "mode": mode,
                "manual_name": manual_name,
            }
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[NameConflict] 保存策略失败: {e}")

    def _resolve_auto_rename(self, safe_name: str, cat_dir: str) -> str:
        """自动生成不冲突的名称（追加 _001, _002 …），扫描整个顶级分类。"""
        import re
        base = re.sub(r'_\d+$', '', safe_name)  # 去掉尾部已有的编号
        # 顶级分类目录（cat_dir 的父级）
        top_dir = os.path.dirname(cat_dir) if os.path.dirname(cat_dir) != cat_dir else cat_dir
        # 收集顶级目录下所有 .zasset 文件名
        existing = set()
        for _root, _dirs, _files in os.walk(top_dir):
            for d in _dirs:
                if d.lower().endswith(".zasset"):
                    existing.add(os.path.splitext(d)[0])
        counter = 1
        while True:
            new_name = f"{base}_{counter:03d}"
            if new_name not in existing:
                return new_name
            counter += 1

    def _show_variant_or_rename_dialog(self, asset_name: str, zasset_path: str,
                                        existing_versions: list):
        """更新资产时，让用户选择：追加 LOD / 创建新版本 / 替换资产

        Returns:
            (choice, version_id) — choice: "add_lod"|"new_version"|"rename"|"cancel"
            当 choice=="add_lod" 时 version_id 为目标版本；其余情况为 None
        """
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("更新资产")
        msg.setText(f"资产「{asset_name}」已存在。\n\n请选择更新方式：")
        msg.setIcon(QtWidgets.QMessageBox.Question)

        btn_add_lod = msg.addButton("追加为 LOD", QtWidgets.QMessageBox.ActionRole)
        btn_new_ver = msg.addButton("创建新版本", QtWidgets.QMessageBox.ActionRole)
        btn_replace = msg.addButton("替换资产", QtWidgets.QMessageBox.ActionRole)
        btn_cancel = msg.addButton("取消", QtWidgets.QMessageBox.RejectRole)

        if not existing_versions:
            btn_add_lod.setEnabled(False)
            btn_add_lod.setToolTip("当前无可用版本，请先创建新版本")

        msg.setDefaultButton(btn_add_lod)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_add_lod:
            if len(existing_versions) == 1:
                return ("add_lod", existing_versions[0].get("id", "v1"))
            # 多版本：弹出版本选择
            ver_names = []
            ver_ids = []
            for v in existing_versions:
                vid = v.get("id", "")
                vtag = v.get("tag", vid)
                vlabel = v.get("label", vid)
                ver_names.append(f"{vtag} - {vlabel}")
                ver_ids.append(vid)
            ver_name, ok = QtWidgets.QInputDialog.getItem(
                self, "选择目标版本",
                "将 LOD 追加到哪个版本？",
                ver_names, len(ver_names) - 1, False)
            if not ok:
                return ("cancel", None)
            idx = ver_names.index(ver_name)
            return ("add_lod", ver_ids[idx])
        elif clicked == btn_new_ver:
            return ("new_version", None)
        elif clicked == btn_replace:
            return ("rename", None)
        else:
            return ("cancel", None)

    def closeEvent(self, event):
        self._save_ui_state()
        event.accept()

    @classmethod
    def show_window(cls, library_path=None):
        import maya.cmds as cmds
        if cmds.window(cls.WINDOW_NAME, exists=True):
            cmds.deleteUI(cls.WINDOW_NAME)
        maya_window = get_maya_window()
        cls._instance = cls(parent=maya_window, library_path=library_path)
        cls._instance.setObjectName(cls.WINDOW_NAME)
        cls._instance.show()
        # 延迟恢复 UI 状态（窗口显示后异步执行，避免阻塞启动）
        QtCore.QTimer.singleShot(0, cls._instance._restore_ui_state)
        cls._instance._update_status_bar()
