# -*- coding: utf-8 -*-
"""
MaterialManager — 材质库核心管理器

职责：
  - 从磁盘加载材质库目录结构
  - 维护 material_id → Material 和 category_id → Category 两级内存索引
  - 提供搜索、增删改查、收藏管理功能
  - 库路径可配置（独立模式 / Maya 模式）

用法:
    mgr = MaterialManager()
    mgr.load_library("/path/to/squirrel_asset_manager")
    metals = mgr.get_materials("metal")
    results = mgr.search("Jeep")
"""

import os
import sys
import uuid
import json
from typing import Optional, List, Dict, Union

# 添加父目录，确保能导入同层模块
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from core.material import Material
from core.category import Category
from utils.json_handler import JSONHandler

# 配置文件路径（模块级默认配置）
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "Assets", "preset", "config.json")
_PBR_MAPPING_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "Assets", "preset", "pbr_mapping.json")


class MaterialManager:
    """
    材质库核心管理器。

    内存索引:
        _materials:   material_id → Material
        _categories:  category_id → Category
        _favorites:   collection_id → set[material_id]
    """

    def __init__(self):
        self._materials: Dict[str, Material] = {}
        self._categories: Dict[str, Category] = {}
        self._library_path: str = ""
        self._favorites: Dict[str, set] = {"default": set()}
        self._favorites_meta: Dict[str, str] = {"default": "默认收藏夹"}
        self._common_tags: Dict[str, List[str]] = {}
        self._json_handler = JSONHandler()
        self._cached_tree = None  # get_category_tree() 缓存，reload 时清空
        self._duplicate_files: List[str] = []  # UUID 重复的 zasset 文件路径列表
        self._name_cache: Dict[str, set] = {}  # sub_lib → set(name) 同名检测加速
        self._path_index: Dict[str, 'Material'] = {}  # json_path → Material 反向索引
        self._search_text_cache: Dict[str, str] = {}  # material_id → 预计算搜索文本

        # ── 从 config.json 加载配置 ──
        self._config = {}
        if os.path.isfile(_CONFIG_PATH):
            cfg = self._json_handler.read_json(_CONFIG_PATH) or {}
            self._config = cfg
        self._apply_config()

    def _apply_config(self):
        """将 config.json 的值应用到实例属性"""
        cfg = self._config

        # 子库列表 — 核心6个始终存在，config.json 可追加自定义子库
        _CORE_LIBS = {
            "materials": "材质", "models": "模型", "lights": "灯光",
            "textures": "贴图", "scenes": "场景", "hdr": "HDR",
        }
        _cfg_libs = cfg.get("sub_libraries", {})
        # 合并：核心库始终保留，config 中可能在核心之外追加了自定义库（如 "ani"）
        self.ASSET_SUB_LIBRARIES = {}
        self.ASSET_SUB_LIBRARIES.update(_CORE_LIBS)
        self.ASSET_SUB_LIBRARIES.update(_cfg_libs)

        # 默认子分类
        raw_cats = cfg.get("default_sub_categories", {})
        self.DEFAULT_SUB_CATEGORIES = {}
        for sub_lib, cat_list in raw_cats.items():
            self.DEFAULT_SUB_CATEGORIES[sub_lib] = [
                (item[0], item[1]) for item in cat_list
            ]

        # 资产文件扩展名
        raw_exts = cfg.get("asset_file_extensions", [
            ".zmetal", ".ma", ".mb", ".fbx", ".obj",
            ".abc", ".usd", ".usda", ".usdc", ".glb", ".gltf",
            ".dae", ".ass", ".rs", ".proxy", ".vrmesh", ".vdb",
        ])
        self.ASSET_FILE_EXTENSIONS = frozenset(raw_exts)

        # 常用标签
        self._common_tags = cfg.get("common_tags", {})

    def reload_config(self):
        """重新加载 config.json（设置界面改动后调用）"""
        if os.path.isfile(_CONFIG_PATH):
            cfg = self._json_handler.read_json(_CONFIG_PATH) or {}
            self._config = cfg
        self._apply_config()

    # ── 核心方法 ────────────────────────────────────────

    def load_library(self, library_path: str) -> bool:
        """
        从磁盘加载材质库。

        过程:
          1. 验证路径存在
          2. 扫描所有子库目录中的 *.zasset 文件
          3. 每个 .zasset 调用 Material.from_json()
          4. 构建 _materials 和 _categories 索引
          5. 加载 library.json 元数据（如存在）

        Args:
            library_path: 材质库根目录路径

        Returns:
            bool: 加载成功返回 True
        """
        if not library_path or not os.path.isdir(library_path):
            print(f"[MaterialManager] 库路径无效: {library_path}")
            return False

        self._library_path = os.path.abspath(library_path)
        self._cached_tree = None  # 清空分类树缓存
        self._materials.clear()
        self._name_cache.clear()
        self._duplicate_files.clear()

        # 首次运行：创建所有子库文件夹 + 子分类文件夹 + 易读名元数据
        is_first_run = (os.path.isfile(os.path.join(self._library_path, "library.json")) == False)

        for sub_dir, sub_name in self.ASSET_SUB_LIBRARIES.items():
            sub_path = os.path.join(self._library_path, sub_dir)
            if not os.path.isdir(sub_path):
                os.makedirs(sub_path, exist_ok=True)
                print(f"[MaterialManager] 创建子库: {sub_path}")
            self._ensure_folder_meta(sub_path, sub_name)
            # 确保根子库 type 字段写入
            root_meta = self._read_folder_meta(sub_path)
            if "type" not in root_meta:
                root_meta["type"] = sub_dir
                self._write_folder_meta(sub_path, root_meta)

            # 首次运行：自动创建子分类文件夹
            if is_first_run or not self._has_child_folders(sub_path):
                sub_cats = self.DEFAULT_SUB_CATEGORIES.get(sub_dir, [])
                for cat_id, cat_name_cn in sub_cats:
                    cat_path = os.path.join(sub_path, cat_id)
                    if not os.path.isdir(cat_path):
                        os.makedirs(cat_path, exist_ok=True)
                        print(f"[MaterialManager] 创建子分类: {sub_name}/{cat_name_cn}")
                    self._ensure_folder_meta(cat_path, cat_name_cn)
                    # 子分类 type 继承上级子库类型（如 materials）
                    cat_meta = self._read_folder_meta(cat_path)
                    cat_meta["type"] = sub_dir
                    self._write_folder_meta(cat_path, cat_meta)

        # 加载/创建 library.json
        lib_meta = os.path.join(self._library_path, "library.json")
        if os.path.isfile(lib_meta):
            meta = self._json_handler.read_json(lib_meta)
            if meta:
                print(f"[MaterialManager] 加载资产库: {meta.get('name', library_path)}")
        else:
            self._json_handler.write_json(lib_meta, {
                "version": "2.0",
                "name": "SquirrelLib",
                "created_date": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
                "sub_libraries": list(self.ASSET_SUB_LIBRARIES.keys()),
                "total_materials": 0,
            })

        # ── 扫描所有子库目录，索引资产 ──
        # 遍历所有子库（materials, models, textures, lights, scenes, hdr）
        sub_lib_dirs = list(self.ASSET_SUB_LIBRARIES.keys())

        def _scan_all_sub_libs():
            """对所有子库执行 _scan_materials_directory"""
            for sld in sub_lib_dirs:
                d = os.path.join(self._library_path, sld)
                if os.path.isdir(d):
                    self._scan_materials_directory(d)

        _scan_all_sub_libs()

        # ── 补充扫描库根目录下的自定义顶级分类文件夹 ──
        # 自定义顶级分类不在 ASSET_SUB_LIBRARIES 下，而是直接放在库根目录，
        # 需单独扫描以索引其中的 .zasset 资产
        try:
            for entry in os.listdir(self._library_path):
                if entry in sub_lib_dirs or entry.startswith('.'):
                    continue
                entry_path = os.path.join(self._library_path, entry)
                if not os.path.isdir(entry_path):
                    continue
                if entry in ("library.json", "favorites.json", "FolderMetadata.fdata"):
                    continue
                self._scan_materials_directory(entry_path)
        except PermissionError:
            pass

        # ── 构建分类索引（使 _categories 包含所有分类） ──
        self._build_category_index()

        # ── 文件后缀专有化：FolderMetadata 和缩略图迁移（仅一次） ──

        _lib_cfg_path = os.path.join(self._library_path, "library.json")
        _legacy_migrated = False
        try:
            with open(_lib_cfg_path, 'r', encoding='utf-8') as _f:
                _lib_cfg = json.load(_f)
                _legacy_migrated = _lib_cfg.get("_legacy_migrated", False)
        except Exception:
            _lib_cfg = {"name": os.path.basename(self._library_path)}

        if not _legacy_migrated:
            # FolderMetadata.json → FolderMetadata.fdata
            for root, dirs, files in os.walk(self._library_path):
                if "FolderMetadata.json" in files:
                    old = os.path.join(root, "FolderMetadata.json")
                    new = os.path.join(root, "FolderMetadata.fdata")
                    os.rename(old, new)
                    print(f"[Migration] {old} → {new}")

            # 缩略图迁移（全库扫描）
            for root, dirs, files in os.walk(self._library_path):
                for fname in files:
                    old_path = os.path.join(root, fname)
                    base, ext = os.path.splitext(fname)
                    if ext.lower() in (".png", ".jpg", ".jpeg"):
                        new_path = os.path.join(root, f"{base}.sicon")
                        if not os.path.isfile(new_path):
                            os.rename(old_path, new_path)
                            print(f"[Migration] {old_path} → {new_path}")
                    elif ext.lower() == ".gif":
                        new_path = os.path.join(root, f"{base}.aicon")
                        if not os.path.isfile(new_path):
                            os.rename(old_path, new_path)
                            print(f"[Migration] {old_path} → {new_path}")

            _lib_cfg["_legacy_migrated"] = True
            with open(_lib_cfg_path, 'w', encoding='utf-8') as _f:
                json.dump(_lib_cfg, _f, indent=4, ensure_ascii=False)

        # ── 加载/创建 favorites.json ──

        # 加载/创建 favorites.json
        fav_path = os.path.join(self._library_path, "favorites.json")
        if os.path.isfile(fav_path):
            self._load_favorites_from_file(fav_path)
        else:
            self._save_favorites_to_file(fav_path)

        # 刷新分类的 material_count
        self._refresh_material_counts()

        total = len(self._materials)
        print(f"[MaterialManager] 加载完成: {total} 个材质, "
              f"{len(self._categories)} 个分类")

        return total >= 0  # 空库也算成功

    # ── 文件夹元数据 ────────────────────────────────────

    METADATA_FILENAME = "FolderMetadata.fdata"

    def _ensure_folder_meta(self, dir_path: str, default_name_cn: str = ""):
        """确保文件夹内有 FolderMetadata.fdata，缺失则创建。返回元数据 dict。"""
        meta_path = os.path.join(dir_path, self.METADATA_FILENAME)
        if os.path.isfile(meta_path):
            data = self._json_handler.read_json(meta_path) or {}
            if "id" not in data:
                data["id"] = str(uuid.uuid4())
                self._json_handler.write_json(meta_path, data)
            return data
        data = {
            "id": str(uuid.uuid4()),
            "name_cn": default_name_cn or os.path.basename(dir_path),
        }
        self._json_handler.write_json(meta_path, data)
        return data

    def _read_folder_meta(self, dir_path: str) -> dict:
        """读取文件夹元数据，无则返回空 dict"""
        meta_path = os.path.join(dir_path, self.METADATA_FILENAME)
        if os.path.isfile(meta_path):
            return self._json_handler.read_json(meta_path) or {}
        return {}

    def _write_folder_meta(self, dir_path: str, data: dict):
        """写入文件夹元数据"""
        meta_path = os.path.join(dir_path, self.METADATA_FILENAME)
        self._json_handler.write_json(meta_path, data)

    def _has_child_folders(self, dir_path: str) -> bool:
        """检查目录下是否有子文件夹（不含 textures）"""
        try:
            for name in os.listdir(dir_path):
                full = os.path.join(dir_path, name)
                if os.path.isdir(full) and name != "textures":
                    return True
        except PermissionError:
            pass
        return False

    def _load_favorites_from_file(self, path: str):
        """从 favorites.json 加载收藏数据（v2: 含名称元数据）"""
        data = self._json_handler.read_json(path)
        if not data:
            return
        collections = data.get("collections", {})
        self._favorites.clear()
        self._favorites_meta.clear()
        for coll_id, coll_data in collections.items():
            if isinstance(coll_data, list):
                # v1 格式兼容
                self._favorites[coll_id] = set(coll_data)
                self._favorites_meta[coll_id] = coll_id if coll_id != "default" else "默认收藏夹"
            elif isinstance(coll_data, dict):
                self._favorites[coll_id] = set(coll_data.get("materials", []))
                self._favorites_meta[coll_id] = coll_data.get("name", coll_id)

    def _save_favorites_to_file(self, path: str):
        """保存收藏数据到 favorites.json（v2: 含名称）"""
        data = {
            "collections": {
                cid: {"name": self._favorites_meta.get(cid, cid), "materials": list(mids)}
                for cid, mids in self._favorites.items()
            }
        }
        self._json_handler.write_json(path, data)

    def _auto_save_favorites(self):
        """收藏变更时自动保存"""
        if self._library_path:
            fav_path = os.path.join(self._library_path, "favorites.json")
            self._save_favorites_to_file(fav_path)

    def _scan_materials_directory(self, materials_dir: str):
        """扫描子库目录，加载所有 *.zasset 资产"""
        zasset_files = self._json_handler.list_directory_recursive(
            materials_dir, "*.zasset"
        )

        loaded = 0
        failed = 0
        skipped = 0

        for filepath in zasset_files:
            try:
                mats = Material.from_json(filepath, self._json_handler)
                if not mats:
                    failed += 1
                    print(f"[MaterialManager] 跳过: {os.path.basename(filepath)} (from_json 返回空列表)")
                    continue
                for mat in mats:
                    if mat.id in self._materials:
                        existing = self._materials[mat.id]
                        display = existing.name or existing.name_cn or "unknown"
                        print(f"[MaterialManager] ⚠ UUID冲突: {os.path.basename(filepath)} (id={mat.id}) "
                              f"与 {display} ({os.path.basename(existing.json_path)}) 重复，收集待修复")
                        self._duplicate_files.append(filepath)
                        skipped += 1
                        continue

                    # 从路径推断分类（.zasset 在分类文件夹内）
                    mat_dir = os.path.dirname(filepath)
                    rel = os.path.relpath(mat_dir, materials_dir)
                    parts = rel.replace("\\", "/").split("/")
                    if len(parts) >= 2:
                        mat.category = parts[-2]
                    elif len(parts) == 1 and parts[0] not in (".", ""):
                        mat.category = parts[0]
                    else:
                        mat.category = "custom"
                    # 记录所属子库（优先从目录链上 FolderMetadata.fdata 的 type 字段确定，
                    # 避免 meta.json 中的 asset_type 写入非子库名导致过滤丢失）
                    folder_sub_lib = os.path.basename(materials_dir)
                    fdata_sub_lib = ""
                    check_dir = mat_dir
                    while check_dir and check_dir.startswith(materials_dir):
                        fm = self._read_folder_meta(check_dir)
                        t = fm.get("type", "")
                        if t:
                            fdata_sub_lib = t
                            break
                        parent = os.path.dirname(check_dir)
                        if parent == check_dir:
                            break
                        check_dir = parent
                    mat.sub_library = fdata_sub_lib if fdata_sub_lib else folder_sub_lib

                    self._materials[mat.id] = mat
                    loaded += 1
            except Exception as e:
                print(f"[MaterialManager] 跳过损坏文件: {filepath} ({e})")
                failed += 1

        # 名称冲突检测：全子库范围内检查完全同名资产，自动重命名
        import re as _re
        names: Dict[str, List[Material]] = {}
        sub_lib_name = os.path.basename(materials_dir)
        for mat in self._materials.values():
            if mat.sub_library == sub_lib_name and mat.name:
                names.setdefault(mat.name, []).append(mat)
        for name, group in names.items():
            if len(group) <= 1:
                continue
            group.sort(key=lambda m: m.json_path or "")
            for extra in group[1:]:
                if extra.json_path and os.path.isdir(extra.json_path):
                    parent = os.path.dirname(extra.json_path)
                    new_path = MaterialManager._resolve_zasset_path(
                        parent, name,
                        existing_names=self._name_cache.get(sub_lib_name))
                    new_name = os.path.splitext(os.path.basename(new_path))[0]
                    tmp = extra.json_path + ".tmp_rename"
                    try:
                        os.rename(extra.json_path, tmp)
                        os.rename(tmp, new_path)
                        from .zasset_io import ZassetIO
                        meta = ZassetIO.read_meta(new_path)
                        if meta:
                            meta["name"] = new_name
                            ZassetIO.write_meta(new_path, meta)
                            extra.name = new_name
                            extra.json_path = new_path
                        print(f"[MaterialManager] 名称冲突修复: {name} → {new_name}")
                    except Exception as e:
                        print(f"[MaterialManager] 名称重命名失败: {e}")
                        if os.path.isdir(tmp):
                            os.rename(tmp, extra.json_path)

        # 构建名称缓存（供 _resolve_zasset_path 全子库扫描加速）
        self._name_cache[sub_lib_name] = {m.name for m in self._materials.values()
                                           if m.sub_library == sub_lib_name and m.name}
        # 构建路径索引和搜索文本缓存
        for mat in self._materials.values():
            if mat.json_path:
                self._path_index[mat.json_path] = mat
            self._build_search_text(mat)

        sub_lib = os.path.basename(materials_dir)
        if failed > 0 or skipped > 0:
            print(f"[MaterialManager] {sub_lib}: 加载 {loaded}, 跳过 {skipped}, 失败 {failed} (共扫描 {len(zasset_files)} 个文件)")

    def _build_search_text(self, mat):
        """为材质预构建搜索文本缓存，避免搜索时重复拼接和lower()"""
        parts = [
            getattr(mat, 'name', '') or '',
            getattr(mat, 'name_cn', '') or '',
            getattr(mat, 'node_type', '') or '',
            getattr(mat, 'software', '') or '',
            getattr(mat, 'renderer', '') or '',
            " ".join(getattr(mat, 'tags', []) or []),
        ]
        self._search_text_cache[mat.id] = " ".join(parts).lower()

    def _refresh_material_counts(self):
        """刷新每个分类的 material_count"""
        # 清零
        for cat in self._categories.values():
            cat.material_count = 0
        # 计数
        for mat in self._materials.values():
            if mat.category in self._categories:
                self._categories[mat.category].material_count += 1
            # 也更新父分类
            parent_cat = self._find_parent_category(mat.category)
            while parent_cat:
                parent_cat.material_count += 1
                parent_cat = self._find_parent_category(parent_cat.id)

    def _find_parent_category(self, category_id: str) -> Optional[Category]:
        """查找分类的父分类"""
        cat = self._categories.get(category_id)
        if cat and cat.parent:
            return self._categories.get(cat.parent)
        return None

    def has_duplicates(self) -> bool:
        """是否有 UUID 重复的资产待修复"""
        return len(self._duplicate_files) > 0

    def fix_duplicate_uuids(self, progress_callback=None) -> int:
        """为所有 UUID 重复的 zasset 文件生成新的 UUID 并写回磁盘

        文件夹格式下直接写入 meta.json，无需全量重建。

        Args:
            progress_callback: 可选，每修复一个文件调用一次 callback(current, total)

        Returns:
            int: 成功修复的文件数
        """
        import json
        import uuid as _uuid
        from .zasset_io import ZassetIO

        fixed = 0
        total = len(self._duplicate_files)
        failed_files = []

        for i, filepath in enumerate(self._duplicate_files):
            try:
                if not os.path.isdir(filepath):
                    print(f"[MaterialManager] 修复跳过: 文件不存在 {filepath}")
                    continue

                meta = ZassetIO.read_meta(filepath)
                if not meta:
                    print(f"[MaterialManager] 修复跳过: 无 meta.json {filepath}")
                    continue

                old_id = meta.get('id', '?')
                meta['id'] = str(_uuid.uuid4())
                ZassetIO.write_meta(filepath, meta)

                fixed += 1
                print(f"[MaterialManager] UUID 已修复: {os.path.basename(filepath)} "
                      f"{old_id} → {meta['id']}")

            except Exception as e:
                print(f"[MaterialManager] 修复失败: {filepath} ({e})")
                failed_files.append(filepath)

            if progress_callback:
                progress_callback(i + 1, total)

        self._duplicate_files.clear()
        if failed_files:
            print(f"[MaterialManager] UUID 修复: {fixed} 成功, {len(failed_files)} 失败")
        else:
            print(f"[MaterialManager] UUID 修复: {fixed} 个文件已重新分配 UUID")

        return fixed

    def _build_category_index(self):
        """从磁盘扫描所有子库文件夹，构建 id → Category 索引。

        使 self._categories 包含所有子库 + 嵌套分类，支持：
        - search() 的层级分类过滤（get_all_descendant_ids）
        - _refresh_material_counts 正确计数
        - 跨子库同名分类的 parent/children 关系追踪
        """
        self._categories.clear()

        def _flatten(node, parent_id=None):
            cid = node["id"]
            node_type = node.get("type", "")
            cat = Category(
                id=cid, name=cid,
                name_cn=node.get("name_cn", cid),
                parent=parent_id,
                color=node.get("color", "#666666"),
            )
            self._categories[cid] = cat
            for child in node.get("children", []):
                child_cat = _flatten(child, cid)
                if child_cat:
                    cat.children.append(child_cat.id)
            return cat

        # 从 get_category_tree() 获取完整树结构，递归展开
        for sub_node in self.get_category_tree():
            _flatten(sub_node, parent_id=None)

    def reload(self) -> bool:
        """重新扫描磁盘，刷新内存缓存"""
        self._materials.clear()
        self._name_cache.clear()
        return self.load_library(self._library_path)

    # ── 查询方法 ────────────────────────────────────────

    def get_materials(self, category_id: Optional[str] = None,
                       sub_library: Optional[str] = None) -> List[Material]:
        """按分类获取材质列表。category_id=None/\"all\" 返回全部。
        支持嵌套：\"metal\" 返回 metal/ 及其子目录下所有材质。

        Args:
            category_id: 分类 ID，None/"all" 返回全部
            sub_library: 可选，指定子库名称（"materials"/"textures"/等），
                         用于防止跨子库同名分类串库
        """
        if not category_id or category_id == "all":
            result = list(self._materials.values())
        else:
            sub_lib_dir = sub_library or "materials"
            if self._library_path:
                materials_dir = os.path.join(self._library_path, sub_lib_dir)
            else:
                materials_dir = ""

            def _under_category(mat, cat_id):
                # 匹配1：category 字段精确匹配（直接同名）
                if mat.category == cat_id:
                    return True
                # 匹配2：category 为嵌套路径（如 "characters/soldier" 匹配 cat_id="soldier"）
                if mat.category:
                    norm_cat = mat.category.replace("\\", "/")
                    if norm_cat.endswith("/" + cat_id) or norm_cat == cat_id:
                        return True
                # 匹配3：磁盘路径匹配（支持嵌套层级）
                if mat.json_path and materials_dir:
                    norm_json = os.path.normpath(mat.json_path)
                    norm_materials = os.path.normpath(materials_dir)
                    if norm_json.startswith(norm_materials + os.sep):
                        rel_path = norm_json[len(norm_materials) + 1:]
                        # 直接子目录：rel_path 以 cat_id/ 开头 或 恰好等于 cat_id
                        if rel_path == cat_id or rel_path.startswith(cat_id + os.sep):
                            return True
                        # 嵌套路径：rel_path 中包含 /cat_id/ 片段（如 "characters/soldier/a.zasset"）
                        if (os.sep + cat_id + os.sep) in (os.sep + rel_path):
                            return True
                return False

            result = [m for m in self._materials.values() if _under_category(m, category_id)]

        # 可选：子库过滤（防止跨子库同名分类串库）
        if sub_library:
            result = [m for m in result if m.sub_library == sub_library]
        return result

    def get_by_id(self, material_id: str) -> Optional[Material]:
        """按 ID 获取单个材质"""
        return self._materials.get(material_id)

    def get_material_count(self, category_id: Optional[str] = None) -> int:
        """获取材质数量"""
        return len(self.get_materials(category_id))

    def get_by_path(self, json_path: str) -> Optional[Material]:
        """按 json_path（.zasset 路径）定位材质，使用反向索引"""
        if not json_path:
            return None
        return self._path_index.get(json_path)

    def is_loaded(self) -> bool:
        """检查是否已加载材质库"""
        return bool(self._library_path)

    # ── 搜索 ────────────────────────────────────────────

    def search(self, query: Union[str, dict]) -> List[Material]:
        """
        搜索材质。

        str 模式: 模糊匹配 name + name_cn + tags
        dict 模式: 结构化搜索
          {
            "keyword": str,      # 名称/标签模糊匹配
            "category": str,     # 分类筛选
            "tags": list[str],   # 标签筛选（AND）
            "node_type": str,    # 材质类型
            "sub_library": str,  # 子库过滤（materials/models/textures/lights/scenes/hdr）
          }

        Returns:
            list[Material]: 匹配的材质列表
        """
        if isinstance(query, str):
            kw = query.lower()
            if not kw:
                return list(self._materials.values())
            results = []
            for mat in self._materials.values():
                if self._keyword_match(mat, kw):
                    results.append(mat)
            return results

        if not isinstance(query, dict):
            return []

        keyword = query.get("keyword", "").lower()
        cat_filter = query.get("category", "")
        tag_filter = query.get("tags", [])
        type_filter = query.get("node_type", "")
        sub_lib_filter = query.get("sub_library", "")

        # ── 变体过滤器（函数映射，新增过滤维度只需加一行） ──
        variant_filters = {
            "has_lod": lambda m: "lod" in (m.variant_types or []),
            "has_version": lambda m: "version" in (m.variant_types or []),
            "has_variants": lambda m: bool(m.variant_types),
            "no_variants": lambda m: not bool(m.variant_types),
        }
        active_variant_filters = {}
        for key, fn in variant_filters.items():
            if key in query:
                active_variant_filters[key] = fn

        results = []
        for mat in self._materials.values():
            # 子库筛选
            if sub_lib_filter and mat.sub_library != sub_lib_filter:
                continue

            # 材质类型筛选
            if type_filter and mat.node_type != type_filter:
                continue

            # 分类筛选
            if cat_filter:
                if not self._match_category(mat, cat_filter, sub_lib_filter):
                    continue

            # 关键词
            if keyword:
                if not self._keyword_match(mat, keyword):
                    continue

            # 标签（AND）
            if tag_filter:
                mat_tags_lower = [t.lower() for t in mat.tags]
                if not all(t.lower() in mat_tags_lower for t in tag_filter):
                    continue

            # 变体过滤器
            if active_variant_filters:
                if not all(fn(mat) for fn in active_variant_filters.values()):
                    continue

            results.append(mat)

        return results

    def _match_category(self, mat, cat_filter: str, sub_lib_hint: str = "") -> bool:
        """判断材质是否匹配分类过滤器。

        优先用 _categories 索引做层级匹配，fallback 到磁盘路径匹配。
        修复子子分类（3层+嵌套）过滤始终为空的问题。
        """
        # 路径 1：_categories 索引层级匹配
        cat = self._categories.get(cat_filter)
        if cat:
            descendant_ids = cat.get_all_descendant_ids(self._categories)
            if mat.category in descendant_ids:
                return True

        # 路径 2：精确匹配（_categories 未加载时的兜底）
        if mat.category == cat_filter:
            return True

        # 路径 3：磁盘路径匹配（支持任意深度的嵌套子分类）
        if mat.json_path and self._library_path:
            sub_lib = sub_lib_hint or mat.sub_library or "materials"
            materials_dir = os.path.join(self._library_path, sub_lib)
            if not os.path.isdir(materials_dir):
                return False
            norm_json = os.path.normpath(mat.json_path)
            norm_materials = os.path.normpath(materials_dir)
            if norm_json.startswith(norm_materials + os.sep):
                rel_path = norm_json[len(norm_materials) + 1:]
                # 直接子目录或嵌套子目录匹配
                if rel_path.startswith(cat_filter + os.sep):
                    return True
                if (os.sep + cat_filter + os.sep) in (os.sep + rel_path):
                    return True
        return False

    # ── 关键词多字段匹配 ─────────────────────────────────

    def _keyword_match(self, mat, keyword: str) -> bool:
        """判断材质是否匹配关键词（支持空格分隔的多关键词 OR 模糊搜索）

        搜索字段: name, name_cn, tags, node_type, sub_library,
                  category, exported_formats, software, renderer, color_space

        任一片段命中任一字段即返回 True。
        示例: "metal arnold" → 匹配含 "metal" 或 "arnold" 的资产
        """
        fragments = keyword.lower().split()
        if not fragments:
            return True

        for frag in fragments:
            if self._fragment_match(mat, frag):
                return True
        return False

    def _fragment_match(self, mat, frag: str) -> bool:
        """单个关键词片段是否命中任一搜索字段（使用预计算缓存）"""
        # 使用预计算搜索文本缓存加速
        cached = self._search_text_cache.get(mat.id)
        if cached is not None and frag in cached:
            return True

        # 类型/子库/分类（含分类显示名）
        if frag in mat.sub_library.lower():
            return True
        if frag in mat.category.lower():
            return True
        cat_display = self.get_category_display_name(mat.category)
        if cat_display and frag in cat_display.lower():
            return True

        # 导出格式
        for fmt in mat.exported_formats:
            if frag in fmt.lower():
                return True

        # 软件/渲染器/色彩空间
        if frag in mat.software.lower():
            return True
        if frag in mat.renderer.lower():
            return True
        if frag in mat.color_space.lower():
            return True

        return False

    # ── 材质移动/复制 ─────────────────────────────────

    def _resolve_category_path(self, category_id: str, sub_lib_hint: str = "",
                                auto_create: bool = False) -> str:
        """统一磁盘路径解析器 — 用 os.walk 在所有子库中匹配文件夹名。

        替代旧的 cat.parent 拼接方式，支持任意深度的嵌套子分类。

        Args:
            category_id: 分类 ID（对应磁盘文件夹名）。
                         支持 CategoryTree 复合格式 "root_lib||short_id"，
                         遇到时自动拆分为 sub_lib_hint + 短 ID 再搜索。
            sub_lib_hint: 优先搜索的子库名（如 "materials"），找到则立即返回
            auto_create: 找不到时是否自动在子库下创建空文件夹

        Returns:
            str: 完整磁盘路径，找不到且不创建返回 ""
        """
        if not self._library_path:
            return ""

        # 处理 CategoryTree 复合 ID 格式 "root_lib||short_id"
        if "||" in category_id:
            parts = category_id.split("||", 1)
            if not sub_lib_hint:
                sub_lib_hint = parts[0]
            category_id = parts[1]

        # 搜索顺序：优先 sub_lib_hint，然后其余子库
        sub_dirs = list(self.ASSET_SUB_LIBRARIES.keys())
        if sub_lib_hint and sub_lib_hint in sub_dirs:
            sub_dirs.remove(sub_lib_hint)
            sub_dirs.insert(0, sub_lib_hint)

        for sld in sub_dirs:
            base = os.path.join(self._library_path, sld)
            if not os.path.isdir(base):
                continue
            # 根目录本身匹配
            if category_id == sld:
                return base
            for root, dirs, _ in os.walk(base):
                dirs[:] = [d for d in dirs if not d.endswith('.zasset')]
                for d in dirs:
                    if d == category_id:
                        return os.path.join(root, d)

        # 子库目录下未找到 → 搜索库根目录下的自定义顶级分类文件夹
        # （自定义顶级分类不在 ASSET_SUB_LIBRARIES 下，而是直接放在库根目录）
        try:
            root_entries = os.listdir(self._library_path)
        except PermissionError:
            root_entries = []
        sub_dirs_set = set(sub_dirs)
        for entry in root_entries:
            if entry in sub_dirs_set or entry.startswith('.'):
                continue
            entry_path = os.path.join(self._library_path, entry)
            if not os.path.isdir(entry_path):
                continue
            if entry in ("library.json", "favorites.json", "FolderMetadata.fdata"):
                continue
            if entry == category_id:
                return entry_path
            for root, dirs, _ in os.walk(entry_path):
                dirs[:] = [d for d in dirs if not d.endswith('.zasset')]
                for d in dirs:
                    if d == category_id:
                        return os.path.join(root, d)

        # 未找到 → 创建
        if auto_create:
            target_sub = sub_lib_hint or (sub_dirs[0] if sub_dirs else "materials")
            p = os.path.join(self._library_path, target_sub, category_id)
            if not os.path.isdir(p):
                os.makedirs(p, exist_ok=True)
            return p

        return ""

    def _ensure_category_dir(self, category_id: str, sub_lib: str = "") -> str:
        """确保目标分类文件夹存在，返回完整路径。不存在则创建。

        委托给 _resolve_category_path 的 os.walk 实现，支持任意深度嵌套。
        sub_lib 用于提示优先搜索的子库，避免跨子库同名分类串路径。"""
        return self._resolve_category_path(category_id, sub_lib_hint=sub_lib, auto_create=True)

    def move_material_to_category(self, material_id: str, category_id: str, sub_lib: str = "") -> bool:
        """将 .zasset 资产移动到目标分类目录（遇同名自动重命名+更新ID）。
        sub_lib 用于提示优先搜索的子库，避免跨子库同名分类串路径。"""
        mat = self._materials.get(material_id)
        if not mat or not mat.json_path or category_id == mat.category or category_id == "all":
            return False
        dst_base = self._ensure_category_dir(category_id, sub_lib=sub_lib)
        if not dst_base:
            return False
        import shutil

        src_path = mat.json_path
        # 自动重命名：同名时追加 _1, _2, ...
        original_name = mat.name
        dst_path = os.path.join(dst_base, f"{original_name}.zasset")
        new_id = mat.id
        new_name = original_name
        counter = 1
        while os.path.isdir(dst_path):
            new_name = f"{original_name}_{counter}"
            dst_path = os.path.join(dst_base, f"{new_name}.zasset")
            counter += 1
        if original_name != new_name:
            new_id = str(uuid.uuid4())
        try:
            shutil.move(src_path, dst_path)
            # 从内存构建 meta，无需 read_meta
            from core.zasset_io import ZassetIO
            meta_data = {
                "id": new_id,
                "name": new_name,
                "name_cn": getattr(mat, "name_cn", new_name),
                "category": category_id,
                "tags": list(getattr(mat, "tags", []) or []),
                "node_type": getattr(mat, "node_type", ""),
                "asset_type": sub_lib or getattr(mat, "sub_library", "materials"),
                "software": getattr(mat, "software", ""),
                "renderer": getattr(mat, "renderer", ""),
                "color_space": getattr(mat, "color_space", ""),
            }
            ZassetIO.update_meta_inplace(dst_path, meta_data)
            # 更新内存索引
            mat.id = new_id
            mat.name = new_name
            mat.json_path = dst_path
            mat.category = category_id
            self._materials[new_id] = mat
            if new_id != material_id:
                self._materials.pop(material_id, None)
            self._refresh_material_counts()
            return True
        except Exception as e:
            print(f"[MaterialManager] 移动 .zasset 失败: {e}")
            return False

    def copy_material_to_category(self, material_id: str, category_id: str, sub_lib: str = "") -> bool:
        """复制 .zasset 资产到目标分类目录（遇同名自动重命名+新UUID）。
        sub_lib 用于提示优先搜索的子库，避免跨子库同名分类串路径。"""
        mat = self._materials.get(material_id)
        if not mat or not mat.json_path or category_id == "all":
            return False
        dst_base = self._ensure_category_dir(category_id, sub_lib=sub_lib)
        if not dst_base:
            return False
        import shutil
        import datetime

        new_id = str(uuid.uuid4())
        # 自动重命名：_copy → _copy_1, _copy_2, ...
        original_name = mat.name
        candidate = f"{original_name}_copy"
        dst_path = os.path.join(dst_base, f"{candidate}.zasset")
        counter = 1
        while os.path.isdir(dst_path):
            candidate = f"{original_name}_copy_{counter}"
            dst_path = os.path.join(dst_base, f"{candidate}.zasset")
            counter += 1
        new_name = candidate
        new_name_cn = f"{mat.name_cn or mat.name}_copy"

        try:
            shutil.copytree(mat.json_path, dst_path)

            # 更新内部 meta.json
            from core.zasset_io import ZassetIO
            meta_data = ZassetIO.read_meta(dst_path)
            if meta_data:
                meta_data["id"] = new_id
                meta_data["name"] = new_name
                meta_data["name_cn"] = new_name_cn
                meta_data["category"] = category_id
                meta_data["create_date"] = datetime.datetime.now().strftime("%Y-%m-%d")
                ZassetIO.update_meta_inplace(dst_path, meta_data)

            # 索引新材质
            new_mat = Material(
                id=new_id,
                name=new_name,
                name_cn=new_name_cn,
                category=category_id,
                tags=list(mat.tags),
                node_type=mat.node_type,
                json_path=dst_path,
                thumbnail_path="",
                software=mat.software,
                renderer=mat.renderer,
                color_space=mat.color_space,
                create_date=datetime.datetime.now().strftime("%Y-%m-%d"),
            )
            self._materials[new_id] = new_mat
            self._refresh_material_counts()
            return True
        except Exception as e:
            print(f"[MaterialManager] 复制 .zasset 失败: {e}")
            return False

    # ── CRUD ────────────────────────────────────────────

    def add_material(self, json_path: str,
                     category_id: str = "custom",
                     sub_lib: str = "materials",
                     force_category: bool = False) -> Optional[Material]:
        """
        从 .zasset 文件导入材质到材质库。

        步骤:
          1. Material.from_json(json_path) 解析元数据
          2. 复制 .zasset 到 {sub_lib}/{category_id}/{name}.zasset
          3. 更新索引并返回

        Args:
            json_path: .zasset 文件路径
            category_id: 目标分类 ID（默认 "custom"）
            sub_lib: 目标根子库名称（默认 "materials"）
            force_category: 强制使用 category_id，不参考源材质 category（粘贴场景）

        Returns:
            Material | None
        """
        if not self._library_path:
            print("[MaterialManager] 未加载材质库，无法导入")
            return None

        mats = Material.from_json(json_path, self._json_handler)
        if not mats:
            print(f"[MaterialManager] 导入失败: 无法解析 {json_path}")
            return None

        first = mats[0]

        # 如果 JSON 自带分类且不为空，优先使用（粘贴时通过 force_category 跳过）
        if not force_category and first.category and first.category != "custom":
            category_id = first.category
        first.category = category_id

        # 确保分类存在
        if category_id not in self._categories:
            self._categories[category_id] = Category(
                id=category_id, name=category_id, name_cn=category_id
            )

        # 用 _ensure_category_dir 解析嵌套路径（如 materials/metal/steel）
        # 替代 os.path.join(library_path, sub_lib, category_id) 的平铺路径
        category_path = self._ensure_category_dir(category_id, sub_lib=sub_lib)

        target_zasset = os.path.join(category_path, f"{first.name}.zasset")

        # 自动重命名防冲突 — 全子库扫描，确保顶级分类下不重名
        target_zasset = self._resolve_zasset_path(
            category_path, first.name,
            existing_names=self._name_cache.get(sub_lib))

        # 跳过已存在的同路径导入
        if os.path.normpath(json_path).lower() == os.path.normpath(target_zasset).lower():
            print(f"[MaterialManager] 材质已在目标位置，跳过: {first.name}")
            return first

        # 复制到新路径 → 始终生成新 UUID，避免与源资产冲突
        copied_name = os.path.splitext(os.path.basename(target_zasset))[0]
        if copied_name != first.name:
            first.name = copied_name
        first.id = str(uuid.uuid4())

        # 单次遍历完成复制 + meta.json 更新
        from core.zasset_io import ZassetIO
        meta_data = {
            "id": first.id,
            "name": first.name,
            "name_cn": getattr(first, "name_cn", first.name),
            "category": category_id,
            "sub_library": sub_lib,
            "tags": getattr(first, "tags", []) or [],
            "node_type": getattr(first, "node_type", ""),
            "asset_type": sub_lib,
            "software": getattr(first, "software", ""),
            "renderer": getattr(first, "renderer", ""),
            "color_space": getattr(first, "color_space", ""),
        }
        if not ZassetIO.copy_with_meta_update(json_path, target_zasset, meta_data):
            print(f"[MaterialManager] 复制失败: {first.name}")
            return None
        first.json_path = target_zasset
        first.sub_library = sub_lib

        # 更新索引
        self._materials[first.id] = first
        self._path_index[first.json_path] = first
        self._build_search_text(first)
        self._refresh_material_counts()

        print(f"[MaterialManager] 导入: {first.get_display_name()} → {category_id}")
        return first

    # ── 导入辅助 ─────────────────────────────────────────

    @staticmethod
    def _resolve_zasset_path(base_path: str, asset_name: str,
                              sub_lib_path: str = "",
                              existing_names: set = None) -> str:
        """同名冲突检测：查找未占用的 .zasset 路径。

        Args:
            base_path:     目标目录
            asset_name:    资产名称
            sub_lib_path:  可选，子库根目录，提供时扫描整个子库
                           （为 None/空 时仅扫 base_path 目录）
            existing_names: 可选，已有名称集合（来自内存缓存），
                            提供时跳过 os.walk，避免磁盘 I/O

        Returns:
            str: 最终唯一的 .zasset 路径
        """
        import re as _re
        existing = set()
        if existing_names is not None:
            existing = existing_names
        elif sub_lib_path and os.path.isdir(sub_lib_path):
            for _root, _dirs, _files in os.walk(sub_lib_path):
                for d in _dirs:
                    if d.lower().endswith(".zasset"):
                        existing.add(os.path.splitext(d)[0])
        else:
            try:
                for f in os.listdir(base_path):
                    if f.lower().endswith(".zasset"):
                        existing.add(os.path.splitext(f)[0])
            except FileNotFoundError:
                pass

        scan_mode = existing_names is not None or bool(sub_lib_path)
        base_name = _re.sub(r'_\d+$', '', asset_name) if scan_mode else asset_name

        if base_name not in existing:
            return os.path.join(base_path, f"{base_name}.zasset")

        counter = 1
        while True:
            numbered = f"{base_name}_{counter:03d}"
            if numbered not in existing:
                return os.path.join(base_path, f"{numbered}.zasset")
            counter += 1

    @staticmethod
    def _load_texture_aliases() -> List[str]:
        """从 pbr_mapping.json 加载所有贴图类型别名，并同时生成带 _ 前缀的版本用于文件名匹配"""
        aliases = []
        try:
            with open(_PBR_MAPPING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = data.get("texture_type_rules", {})
            for tex_type, rule in rules.items():
                for a in rule.get("aliases", []):
                    if a and a not in aliases:
                        aliases.append(a)
                    prefixed = "_" + a
                    if prefixed not in aliases:
                        aliases.append(prefixed)
        except Exception as e:
            print(f"[Manager] 加载 pbr_mapping.json 失败: {e}")
        return aliases

    @staticmethod
    def _strip_texture_suffix(base_name: str, suffixes: List[str]) -> str:
        """剥离贴图后缀，返回分组前缀。大小写不敏感，避免空结果。"""
        if not base_name:
            return base_name
        lower_name = base_name.lower()
        for suffix in suffixes:
            if lower_name.endswith(suffix.lower()):
                stripped = base_name[:-len(suffix)]
                if stripped:  # 避免全部剥离为空
                    return stripped
        return base_name

    @staticmethod
    def _now_iso() -> str:
        """返回 ISO 格式的当前时间字符串。"""
        from datetime import datetime
        return datetime.now().isoformat(timespec='seconds')

    def import_external_folder(self, source_dir: str, category_id: str = "custom",
                                sub_lib: str = "materials") -> int:
        """
        从外部文件夹批量导入资产（不递归，所有子文件夹打包为 .zasset）。

        输出统一为 .zasset 文件夹格式。

        处理策略:
          - 根级文件：按文件名前缀去尾分组 → ZassetBuilder.build()
          - 子文件夹（除 textures）：全部 ZassetBuilder.build_from_folder()
          - .zasset 源文件：直拷 + ZassetIO.update_meta_inplace()

        Args:
            source_dir: 源文件夹路径
            category_id: 目标分类 ID（如 "building"）
            sub_lib: 目标根子库名称（如 "models"、"materials"）

        Returns:
            int: 成功导入的资产数
        """
        if not self._library_path:
            print("[MaterialManager] 未加载材质库，无法导入")
            return 0

        if not os.path.isdir(source_dir):
            return 0

        imported = 0
        base_path = os.path.join(self._library_path, sub_lib, category_id)
        os.makedirs(base_path, exist_ok=True)

        # 贴图去尾后缀（从 pbr_mapping.json 获取所有别名，按长度降序）
        pbr_aliases = self._load_texture_aliases()
        texture_suffixes = sorted(pbr_aliases, key=len, reverse=True)

        print(f"[Import] 开始导入: {source_dir} → {sub_lib}/{category_id}")

        # ════════════════════════════════════════════════════════════
        # 第一步：扫描源目录，分类收集
        # ════════════════════════════════════════════════════════════

        zasset_prefixes = set()   # 已有的 .zasset 文件前缀
        root_file_groups = {}      # group_prefix → [(full_path, fname), ...]
        subfolder_assets = []      # (src_dir, folder_name)

        for entry in sorted(os.listdir(source_dir)):
            full = os.path.join(source_dir, entry)
            if entry.startswith("."):
                continue

            if os.path.isdir(full):
                if entry == "textures":
                    continue
                # 所有子文件夹 → 打包为 .zasset（不再递归）
                subfolder_assets.append((full, entry))
            else:
                # ── 根级文件 ──
                if entry.endswith(".zasset"):
                    zasset_prefixes.add(entry[:-7])  # 去掉 .zasset 后缀
                    continue

                ext = os.path.splitext(entry)[1].lower()
                if ext not in self.ASSET_FILE_EXTENSIONS:
                    continue

                base_name = entry[:-(len(ext))] if ext else entry
                group_prefix = self._strip_texture_suffix(base_name, texture_suffixes)

                if group_prefix not in root_file_groups:
                    root_file_groups[group_prefix] = []
                root_file_groups[group_prefix].append((full, entry))

        # ════════════════════════════════════════════════════════════
        # 第二步：处理 .zasset 直拷（先于文件分组，避免重复导入）
        # ════════════════════════════════════════════════════════════

        from core.zasset_io import ZassetIO

        for zasset_prefix in zasset_prefixes:
            src_zasset = os.path.join(source_dir, f"{zasset_prefix}.zasset")
            if not os.path.isdir(src_zasset):
                continue

            target_path = self._resolve_zasset_path(base_path, zasset_prefix)
            final_name = os.path.splitext(os.path.basename(target_path))[0]

            import shutil
            if os.path.isdir(target_path):
                shutil.rmtree(target_path)
            shutil.copytree(src_zasset, target_path)

            # 更新 meta.json（使用 update_meta_inplace，不解压重建）
            meta_data = ZassetIO.read_meta(target_path)
            if meta_data:
                meta_data["id"] = str(uuid.uuid4())
                meta_data["name"] = final_name
                meta_data["category"] = category_id
                meta_data["source"] = src_zasset
                meta_data["import_date"] = self._now_iso()
                ZassetIO.update_meta_inplace(target_path, meta_data)

            # 注册到索引
            mat = self.add_material(target_path, category_id, sub_lib)
            if mat:
                imported += 1
                print(f"[Import] 导入 .zasset: {final_name} → {category_id}")

        # ════════════════════════════════════════════════════════════
        # 第三步：处理根级文件分组 → ZassetBuilder.build()
        # ════════════════════════════════════════════════════════════

        from core.zasset_builder import ZassetBuilder

        # 几何体扩展名集合（从 config.json 读取，带默认值）
        GEOM_EXTS = frozenset(self._config.get("geometry_extensions", [
            ".ma", ".mb", ".fbx", ".obj", ".abc", ".usd",
            ".usda", ".usdc", ".glb", ".gltf", ".dae",
            ".ass", ".proxy", ".vrmesh", ".vdb",
        ]))
        # 图像/贴图扩展名集合（从 config.json 读取，带默认值）
        IMG_EXTS = frozenset(self._config.get("image_extensions", [
            ".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tga",
            ".tiff", ".tif", ".bmp", ".psd",
        ]))

        for group_prefix, file_list in root_file_groups.items():
            # 跳过已被 .zasset 直拷处理的前缀
            if group_prefix in zasset_prefixes:
                continue
            if not file_list:
                continue

            # 冲突检测
            target_path = self._resolve_zasset_path(base_path, group_prefix)
            final_name = os.path.splitext(os.path.basename(target_path))[0]

            # 构建 files dict（内部路径 → 磁盘源路径）
            files = {}
            for src_file, fname in file_list:
                ext = os.path.splitext(fname)[1].lower()

                if ext == ".zmetal":
                    files["node.zmetal"] = src_file
                elif ext == ".mcm":
                    files["node.mcm"] = src_file
                elif ext == ".sicon":
                    files["thumb.sicon"] = src_file
                elif ext == ".aicon":
                    files["thumb.aicon"] = src_file
                elif ext in GEOM_EXTS:
                    # 几何体 → 重命名为 node.{ext}
                    files[f"node{ext}"] = src_file
                elif ext in IMG_EXTS:
                    # 图像 → textures/ 目录
                    files[f"textures/{fname}"] = src_file
                else:
                    # 其他 → 保留原名
                    files[fname] = src_file

            # 构建 meta dict
            meta = {
                "id": str(uuid.uuid4()),
                "name": final_name,
                "name_cn": final_name,
                "category": category_id,
                "sub_library": sub_lib,
                "tags": [],
                "node_type": "imported",
                "software": "",
                "renderer": "",
                "source": source_dir,
                "import_date": self._now_iso(),
                "version": "2.0",
            }

            if ZassetBuilder.build(target_path, files, meta):
                mat = self.add_material(target_path, category_id, sub_lib)
                if mat:
                    imported += 1
                    print(f"[Import] 导入: {final_name} → {category_id}")
            else:
                print(f"[Import] 构建失败: {final_name}")

        # ════════════════════════════════════════════════════════════
        # 第四步：处理子文件夹资产 → ZassetBuilder.build_from_folder()
        # ════════════════════════════════════════════════════════════

        for src_dir, folder_name in subfolder_assets:
            # 冲突检测
            target_path = self._resolve_zasset_path(base_path, folder_name)
            final_name = os.path.splitext(os.path.basename(target_path))[0]

            # 读取或创建 meta dict
            ameta_path = None
            try:
                for fname in os.listdir(src_dir):
                    if fname.endswith(".ameta"):
                        ameta_path = os.path.join(src_dir, fname)
                        break
            except OSError:
                pass

            if ameta_path:
                meta = self._json_handler.read_json(ameta_path) or {}
            else:
                meta = {}

            meta["id"] = str(uuid.uuid4())
            meta["name"] = final_name
            meta["name_cn"] = final_name
            meta["category"] = category_id
            meta["sub_library"] = sub_lib
            meta["source"] = src_dir
            meta["import_date"] = self._now_iso()

            if ZassetBuilder.build_from_folder(target_path, src_dir, meta):
                mat = self.add_material(target_path, category_id, sub_lib)
                if mat:
                    imported += 1
                    print(f"[Import] 导入子文件夹: {final_name} → {category_id}")
            else:
                print(f"[Import] 子文件夹打包失败: {folder_name}")

        return imported

    def remove_material(self, material_id: str) -> bool:
        """
        删除 .zasset 资产文件。

        Returns:
            bool: 成功返回 True
        """
        mat = self._materials.get(material_id)
        if not mat:
            print(f"[MaterialManager] 删除失败: 未找到资产 id={material_id}")
            return False

        name = mat.get_display_name()

        import shutil

        if mat.json_path:
            if os.path.isdir(mat.json_path):
                try:
                    shutil.rmtree(mat.json_path)
                    print(f"[MaterialManager] 已删除: {mat.json_path}")
                except OSError as e:
                    print(f"[MaterialManager] 删除文件失败: {mat.json_path} ({e})")
            else:
                print(f"[MaterialManager] 删除跳过: 文件不存在 {mat.json_path}")

        # 从索引移除
        del self._materials[material_id]
        # 从收藏夹移除
        for fav_set in self._favorites.values():
            fav_set.discard(material_id)

        self._refresh_material_counts()
        print(f"[MaterialManager] 删除: {name}")
        return True

    def update_material(self, material_id: str, updates: dict) -> bool:
        """
        更新材质元数据并写回 JSON 文件。

        支持的 updates: name, name_cn, category, tags, node_type, thumbnail_path
        不支持: id, json_path

        Returns:
            bool
        """
        mat = self._materials.get(material_id)
        if not mat:
            return False

        old_category = mat.category
        old_name = mat.name

        for key in ("name", "name_cn", "category", "tags", "node_type", "thumbnail_path", "thumb_bytes", "notes"):
            if key in updates:
                setattr(mat, key, updates[key])

        # 分类变更 → 移动 JSON 文件
        if "category" in updates and old_category != mat.category:
            self._move_material_file(mat, old_category)

        # 名称变更 → 重命名 .zasset 文件
        if "name" in updates and old_name != mat.name:
            self._rename_material_file(mat, old_name)

        # 写回 JSON 元数据（更新 source 中的 material 字段）
        if mat.json_path and (os.path.isfile(mat.json_path) or os.path.isdir(mat.json_path)):
            self._update_json_metadata(mat)

        self._refresh_material_counts()
        return True

    def batch_update(self, material_ids: List[str],
                     updates: dict) -> dict:
        """
        批量更新材质。

        Returns:
            dict: {"success": int, "failed": int, "errors": list}
        """
        success = 0
        failed = 0
        errors = []

        for mid in material_ids:
            try:
                if self.update_material(mid, updates):
                    success += 1
                else:
                    failed += 1
                    errors.append(f"{mid}: 材质不存在")
            except Exception as e:
                failed += 1
                errors.append(f"{mid}: {e}")

        return {"success": success, "failed": failed, "errors": errors}

    def _move_material_file(self, mat: Material, old_category: str):
        """移动 .zasset 资产到新分类目录"""
        if not mat.json_path or not self._library_path:
            return

        src_path = mat.json_path
        target_parent = os.path.join(self._library_path, "materials", mat.category)
        self._json_handler.ensure_directory(target_parent)
        target_path = os.path.join(target_parent, f"{mat.name}.zasset")
        try:
            if os.path.isfile(src_path) and src_path != target_path:
                import shutil
                shutil.move(src_path, target_path)
                mat.json_path = target_path
        except OSError:
            pass

    def _rename_material_file(self, mat: Material, old_name: str):
        """重命名 .zasset 文件（name 变更时调用）"""
        if not mat.json_path or not self._library_path:
            return
        src_path = mat.json_path
        dir_name = os.path.dirname(src_path)
        new_path = os.path.join(dir_name, f"{mat.name}.zasset")
        try:
            if os.path.isfile(src_path) and src_path != new_path:
                import shutil
                shutil.move(src_path, new_path)
                mat.json_path = new_path
        except OSError:
            pass

    def _update_json_metadata(self, mat: Material):
        """更新材质元数据并写回（直接修改 meta.json）"""
        try:
            if mat.is_zasset:
                from core.zasset_io import ZassetIO
                data = ZassetIO.read_meta(mat.json_path)
                if not data:
                    print(f"[Manager] _update_json_metadata: read_meta 返回空 {mat.json_path}")
                    return
                data["name"] = mat.name
                data["name_cn"] = mat.name_cn
                data["category"] = mat.category
                data["tags"] = list(mat.tags)
                data["node_type"] = mat.node_type
                data["thumbnail_path"] = mat.thumbnail_path
                data["notes"] = mat.notes
                ZassetIO.update_meta_inplace(mat.json_path, data)
            else:
                # 非 .zasset：直接写 meta.json
                import json
                meta_path = mat.json_path
                if meta_path and os.path.isfile(meta_path):
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    data["name"] = mat.name
                    data["name_cn"] = mat.name_cn
                    data["category"] = mat.category
                    data["tags"] = list(mat.tags)
                    data["node_type"] = mat.node_type
                    data["thumbnail_path"] = mat.thumbnail_path
                    data["notes"] = mat.notes
                    with open(meta_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[Manager] _update_json_metadata 失败: {mat.name}({mat.json_path}) → {e}")

    # ── 分类方法 ────────────────────────────────────────

    def get_categories(self) -> List[Category]:
        """获取所有分类列表（按 sort_order 排序）"""
        return sorted(self._categories.values(), key=lambda c: c.sort_order)

    def get_category_disk_path(self, category_id: str, sub_lib_hint: str = "") -> str:
        """返回分类在磁盘上的完整文件夹路径。

        委托给 _resolve_category_path 的 os.walk 实现，
        支持任意深度的嵌套子分类。文件夹不存在返回空字符串。

        Args:
            category_id: 分类 ID
            sub_lib_hint: 优先搜索的子库名，用于区分跨子库同名分类
        """
        return self._resolve_category_path(category_id, sub_lib_hint=sub_lib_hint)

    def get_category_display_name(self, category_id: str) -> str:
        """返回分类显示名，优先级：name_cn(易读名) → name → id"""
        cat = self._categories.get(category_id)
        if cat:
            return cat.name_cn or cat.name or cat.id
        return category_id

    def get_category_tree(self) -> list:
        """
        获取文件系统镜像树（所有子库按文件夹结构递归）。
        每个子库根目录作为顶级节点，其内容为子节点。
        结果缓存，在 reload/load_library 时清空。
        """
        if self._cached_tree is not None:
            return self._cached_tree

        result = []
        if not self._library_path:
            self._cached_tree = result
            return result

        def scan_dir(dir_path, parent_id=None, root_lib=""):
            nodes = []
            try:
                entries = sorted(os.listdir(dir_path))
            except PermissionError:
                return nodes

            for name in entries:
                full = os.path.join(dir_path, name)
                if not os.path.isdir(full):
                    continue
                if name.endswith('.zasset'):
                    continue

                meta = self._ensure_folder_meta(full)
                display = meta.get("name_cn", name)

                # 统计该分类下的材质数（仅按磁盘路径匹配，避免跨子库同名串数）
                norm_full = os.path.normpath(full)
                count = sum(1 for m in self._materials.values()
                           if m.json_path and (
                               os.path.normpath(m.json_path).startswith(norm_full + os.sep)
                           ))

                # type 优先从 FolderMetadata 读取；为空时用所属子库作为 fallback
                # （粘贴创建的目录没有 .fdata 的 type 字段，需从磁盘路径推断）
                node_type = meta.get("type", "") or root_lib

                node = {
                    "id": name, "name": name, "name_cn": display,
                    "icon": "", "description": "",
                    "color": "#666666", "parent": parent_id,
                    "children": [], "sort_order": 99,
                    "material_count": count,
                    "type": node_type,
                    "meta_id": meta.get("id", ""),
                }

                # 递归扫描子分类，传递 root_lib 继承
                child_nodes = scan_dir(full, name, root_lib or node_type)
                node["children"] = child_nodes
                nodes.append(node)
            return nodes

        # 遍历所有子库文件夹
        for sub_dir, sub_name in self.ASSET_SUB_LIBRARIES.items():
            sub_path = os.path.join(self._library_path, sub_dir)
            if not os.path.isdir(sub_path):
                continue
            meta = self._ensure_folder_meta(sub_path, sub_name)
            sub_display = meta.get("name_cn", sub_name)
            children = scan_dir(sub_path, sub_dir, sub_dir)
            # 汇总子节点计数到根节点
            children_total = sum(child.get("material_count", 0) for child in children)
            total = children_total
            # 补充直接放在子库根目录下的资产（不在任何子分类中）
            for m in self._materials.values():
                if m.json_path and os.path.normpath(m.json_path).startswith(
                        os.path.normpath(sub_path) + os.sep):
                    # 如果这个材质不在任何子分类文件夹内，追加
                    in_child = any(
                        os.path.normpath(m.json_path).startswith(
                            os.path.normpath(os.path.join(sub_path, c.get("id", ""))) + os.sep)
                        for c in children
                    )
                    if not in_child:
                        total += 1
            sub_node = {
                "id": sub_dir, "name": sub_dir, "name_cn": sub_display,
                "icon": "", "description": "",
                "color": "#d0d0d0", "parent": None,
                "children": children,
                "sort_order": 99,
                "material_count": total,
                "type": meta.get("type", sub_dir),
                "meta_id": meta.get("id", ""),
            }
            result.append(sub_node)

        # 扫描根目录下其他自定义文件夹（不在预定义列表中的）
        try:
            root_entries = sorted(os.listdir(self._library_path))
        except PermissionError:
            root_entries = []

        for name in root_entries:
            if name in self.ASSET_SUB_LIBRARIES:
                continue  # 预定义的已在上面处理
            full = os.path.join(self._library_path, name)
            if not os.path.isdir(full):
                continue
            if name.startswith("."):
                continue  # 跳过隐藏文件夹
            if name in ("library.json", "favorites.json", "FolderMetadata.fdata"):
                continue  # 跳过元数据文件

            meta = self._ensure_folder_meta(full)
            display = meta.get("name_cn", name)
            children = scan_dir(full, name, name)
            # 注意：每个子节点的 material_count 已递归包含其后代，不能重复加孙子节点
            total = sum(c.get("material_count", 0) for c in children)
            result.append({
                "id": name, "name": name, "name_cn": display,
                "icon": "", "description": "",
                "color": "#b0b0b0", "parent": None,
                "children": children,
                "sort_order": 99,
                "material_count": total,
                "type": meta.get("type", name),
            })

        self._cached_tree = result
        return result

    # ── 收藏方法 ────────────────────────────────────────

    def get_favorites(self, collection_id: str = "default") -> List[Material]:
        """获取收藏夹中的材质列表"""
        fav_ids = self._favorites.get(collection_id, set())
        result = []
        for mid in fav_ids:
            if mid in self._materials:
                result.append(self._materials[mid])
            else:
                # 回退：按 json_path 包含匹配（路径型 ID 如 models/electronics/ddd）
                for mat in self._materials.values():
                    if mat.json_path and mid in mat.json_path:
                        result.append(mat)
                        break
        return result

    def toggle_favorite(self, material_id: str,
                        collection_id: str = "default") -> bool:
        """
        切换收藏状态。

        Returns:
            bool: 当前是否已收藏 (True=已收藏)
        """
        if collection_id not in self._favorites:
            self._favorites[collection_id] = set()

        fav_set = self._favorites[collection_id]
        if material_id in fav_set:
            fav_set.discard(material_id)
            self._auto_save_favorites()
            return False
        else:
            fav_set.add(material_id)
            self._auto_save_favorites()
            return True

    def is_favorite(self, material_id: str,
                    collection_id: str = "default") -> bool:
        """检查材质是否已收藏"""
        fav_set = self._favorites.get(collection_id, set())
        return material_id in fav_set

    # ── 标签持久化 ────────────────────────────────────

    def _save_common_tags_to_config(self):
        """保存常用标签到 config.json（唯一数据源）"""
        self._config["common_tags"] = dict(self._common_tags)
        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[Manager] 保存 common_tags 到 config.json 失败: {e}")

    def get_common_tags(self, tag_type: str = "materials") -> list:
        """获取指定类型的常用标签列表"""
        return list(self._common_tags.get(tag_type, []))

    def add_common_tag(self, tag: str, tag_type: str = "materials"):
        """添加常用标签（按类型分组），同时保存到 config.json"""
        if tag_type not in self._common_tags:
            self._common_tags[tag_type] = []
        if tag not in self._common_tags[tag_type]:
            self._common_tags[tag_type].append(tag)
            self._save_common_tags_to_config()

    def remove_common_tag(self, tag: str, tag_type: str = "materials"):
        """删除常用标签，同时保存到 config.json"""
        if tag_type in self._common_tags and tag in self._common_tags[tag_type]:
            self._common_tags[tag_type].remove(tag)
            self._save_common_tags_to_config()

    def save_favorites(self):
        """手动保存收藏数据到磁盘"""
        self._auto_save_favorites()

    # ── 导出方法 ──────────────────────────────────────

    def export_material(self, material_id: str, target_dir: str,
                        include_ma: bool = False) -> dict:
        """
        导出单个 .zasset 资产到目标目录。

        Args:
            material_id: 材质 UUID
            target_dir: 输出根目录
            include_ma: 是否同时导出 .ma（需 Maya 环境）

        Returns:
            dict: {"success": bool, "path": str, "error": str}
        """
        mat = self._materials.get(material_id)
        if not mat:
            return {"success": False, "path": "", "error": f"材质不存在: {material_id}"}

        try:
            import shutil

            export_path = os.path.join(target_dir, f"{mat.name}.zasset")
            if os.path.isdir(export_path):
                shutil.rmtree(export_path)
            shutil.copytree(mat.json_path, export_path)
            print(f"[MaterialManager] 导出完成: {mat.get_display_name()} → {export_path}")
            return {"success": True, "path": export_path, "error": ""}

        except Exception as e:
            return {"success": False, "path": "", "error": str(e)}

    def export_library(self, material_ids: List[str],
                       target_dir: str) -> dict:
        """
        批量导出材质库。

        目录结构:
          {target_dir}/
            library.json             # 库元数据
            categories.json          # 分类定义
            materials/
              {category_id}/
                {material_id}.json
                {material_id}.sicon

        Args:
            material_ids: 要导出的材质 ID 列表
            target_dir: 输出根目录

        Returns:
            dict: {"success": int, "failed": int, "errors": list, "path": str}
        """
        results = {"success": 0, "failed": 0, "errors": [], "path": target_dir}

        try:
            self._json_handler.ensure_directory(target_dir)

            # 导出 library.json
            lib_meta = {
                "version": "1.0",
                "name": os.path.basename(target_dir),
                "created_date": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
                "total_materials": len(material_ids),
            }
            self._json_handler.write_json(
                os.path.join(target_dir, "library.json"), lib_meta
            )

            # 导出 categories.json
            cats = [c.to_dict() for c in self._categories.values()]
            self._json_handler.write_json(
                os.path.join(target_dir, "categories.json"), {"categories": cats}
            )

            # 逐个导出材质
            materials_dir = os.path.join(target_dir, "materials")
            for mid in material_ids:
                result = self.export_material(mid, materials_dir)
                if result["success"]:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"{mid}: {result['error']}")

            print(f"[MaterialManager] 批量导出: {results['success']}/{len(material_ids)} 成功")
            return results

        except Exception as e:
            results["errors"].append(str(e))
            return results

    # ── v2.0 批量导出 ────────────────────────────────────

    def export_asset_batch(
        self,
        configs: list,  # List[ExportConfig] — 字符串类型提示避免循环导入
    ):
        """批量导出资产到当前库路径（v2.0 新增）。

        桥接 UI 到 ExportOrchestrator，使用当前管理器加载的库路径。

        Args:
            configs: ExportConfig 列表

        Returns:
            BatchSummary: 批量导出汇总结果
        """
        from .export_orchestrator import ExportOrchestrator

        orch = ExportOrchestrator(self._library_path)
        return orch.export_batch(configs)

    # ── 辅助方法 ────────────────────────────────────────

    def get_library_path(self) -> str:
        """获取当前材质库路径"""
        return self._library_path


# ── 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import json

    passed = 0
    failed = 0

    def check(condition, label):
        global passed, failed
        if condition:
            print(f"  ✓ {label}")
            passed += 1
        else:
            print(f"  ✗ {label}")
            failed += 1

    print("=" * 50)
    print("MaterialManager 自测")
    print("=" * 50)

    # ── T1.5.9: 核心结构 ──
    print("\n[T1.5.9] 核心结构")
    mgr = MaterialManager()
    check(isinstance(mgr._categories, dict), f"_categories 是 dict: {type(mgr._categories).__name__}")
    check(len(mgr._materials) == 0, "初始材质数为 0")
    check("default" in mgr._favorites, "初始收藏夹 'default' 存在")

    # ── T1.5.10: load_library ──
    print("\n[T1.5.10] load_library")
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试库结构
        mat_dir = os.path.join(tmpdir, "materials", "custom")
        os.makedirs(mat_dir, exist_ok=True)

        # 写入 .ameta（新格式）
        test_meta = {
            "id": "uuid-test-metal-001",
            "version": "2.0",
            "software": "Maya",
            "renderer": "arnold",
            "color_space": "ACEScg",
            "create_date": "2026-05-12",
            "name": "TestMetal",
            "name_cn": "测试金属",
            "node_type": "standardSurface",
            "category": "",
            "tags": ["pbr"]
        }
        meta_path = os.path.join(mat_dir, "TestMetal.ameta")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(test_meta, f, ensure_ascii=False)

        ok = mgr.load_library(tmpdir)
        check(ok, "load_library 成功")
        check(mgr.get_material_count() == 1, f"加载后材质数: {mgr.get_material_count()}")
        # 验证分类从磁盘路径推断
        mat = mgr.get_by_id("uuid-test-metal-001")
        check(mat is not None, "UUID 定位材质成功")
        check(mat is not None and mat.category == "custom", "category 从路径推断为 custom")

        # 空库测试
        with tempfile.TemporaryDirectory() as empty_dir:
            mgr2 = MaterialManager()
            ok = mgr2.load_library(empty_dir)
            check(ok, "空库加载成功（不报错）")
            check(mgr2.get_material_count() == 0, "空库材质数为 0")

    # ── T1.5.11: CRUD ──
    print("\n[T1.5.11] CRUD 操作")

    with tempfile.TemporaryDirectory() as tmpdir:
        base = os.path.join(tmpdir, "materials")

        # 创建多个测试材质（.ameta 格式）
        test_mats = [
            ("uuid-metal-chrome", "Metal_Chrome", "metal_chrome",
             ["金属", "chrome"], "standardSurface"),
            ("uuid-fabric-silk", "Fabric_Silk", "fabric_silk",
             ["布料", "silk"], "standardSurface"),
            ("uuid-glass-clear", "Glass_Clear", "glass",
             ["玻璃"], "standardSurface"),
        ]
        for uid, name, cat, tags, ntype in test_mats:
            mat_dir = os.path.join(base, cat)
            os.makedirs(mat_dir, exist_ok=True)
            # 材质子文件夹
            full_dir = os.path.join(mat_dir, name)
            os.makedirs(full_dir, exist_ok=True)
            meta = {
                "id": uid, "version": "2.0",
                "software": "Maya", "renderer": "arnold",
                "color_space": "ACEScg", "create_date": "2026-05-12",
                "name": name, "name_cn": name,
                "node_type": ntype, "category": "", "tags": tags
            }
            with open(os.path.join(full_dir, f"{name}.ameta"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)

        # 加载
        mgr = MaterialManager()
        mgr.load_library(tmpdir)
        check(mgr.get_material_count() == 3, f"加载 3 个材质")

        # get_materials 分类筛选
        glasses = mgr.get_materials("glass")
        check(len(glasses) >= 1, f"glass 分类: {len(glasses)} 个")

        # search
        results = mgr.search("Chrome")
        check(len(results) >= 1, f"搜索 'Chrome': {len(results)} 个")

        results = mgr.search("Glass")
        check(len(results) >= 1, f"搜索 'Glass': {len(results)} 个")

        # update（UUID id）
        mat = mgr.get_by_id("uuid-metal-chrome")
        if mat:
            ok = mgr.update_material("uuid-metal-chrome",
                                     {"name_cn": "铬金属", "tags": ["金属", "chrome", "updated"]})
            check(ok, "update_material 成功")
            check(mat.name_cn == "铬金属", "name_cn 已更新")
            check("updated" in mat.tags, "tags 已更新")

        # batch_update
        result = mgr.batch_update(["uuid-metal-chrome", "uuid-fabric-silk"],
                                   {"category": "custom"})
        check(result["success"] == 2, f"batch_update: {result['success']}/2 成功")

        # 结构化搜索
        results = mgr.search({"category": "custom", "tags": ["chrome"]})
        check(len(results) >= 1, f"分类+标签搜索: {len(results)} 个")

        # remove
        ok = mgr.remove_material("uuid-glass-clear")
        check(ok, "remove_material 成功")
        check(mgr.get_material_count() == 2, f"删除后剩余 {mgr.get_material_count()} 个")

    # ── T1.5.12: 分类/收藏 ──
    print("\n[T1.5.12] 分类/收藏")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建 .ameta 格式
        full_dir = os.path.join(tmpdir, "materials", "metal", "FavItem")
        os.makedirs(full_dir, exist_ok=True)
        meta = {
            "id": "uuid-fav-item",
            "version": "2.0",
            "software": "Maya", "renderer": "arnold",
            "color_space": "ACEScg", "create_date": "2026-05-12",
            "name": "FavItem", "name_cn": "收藏项",
            "node_type": "standardSurface", "category": "", "tags": []
        }
        with open(os.path.join(full_dir, "FavItem.ameta"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

        mgr3 = MaterialManager()
        mgr3.load_library(tmpdir)

        # 分类树
        tree = mgr3.get_category_tree()
        check(len(tree) > 0, f"get_category_tree: {len(tree)} 个节点")

        # 收藏（UUID id）
        is_fav = mgr3.toggle_favorite("uuid-fav-item")
        check(is_fav == True, "toggle_favorite → True（已收藏）")
        check(mgr3.is_favorite("uuid-fav-item"), "is_favorite 返回 True")

        favs = mgr3.get_favorites()
        check(len(favs) == 1, f"收藏夹有 {len(favs)} 个材质")

        # 取消收藏
        is_fav = mgr3.toggle_favorite("uuid-fav-item")
        check(is_fav == False, "toggle_favorite → False（已取消）")

    # ── 结果 ──
    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" ✅ 全部通过")
    print("=" * 50)
