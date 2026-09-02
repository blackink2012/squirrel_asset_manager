# -*- coding: utf-8 -*-
"""web_viewer.scanner — 资产库只读扫描器（纯标准库实现）

设计目标：
  - 零第三方依赖：不依赖 PySide6 / Maya / core 包的 .pyd，
    可用任意 Python 3.8+ 直接运行。
  - 与 Maya 插件共享同一套磁盘格式：
      <library>/
      ├── library.json
      ├── favorites.json
      ├── materials|models|lights|textures|scenes|hdr|ani/   ← 子库
      │   ├── FolderMetadata.fdata                           ← 文件夹元数据
      │   └── <category>/                                    ← 分类文件夹
      │       ├── FolderMetadata.fdata
      │       └── MyAsset.zasset/                            ← 资产文件夹
      │           ├── meta.json
      │           ├── thumb.sicon  (PNG)
      │           ├── thumb.aicon  (GIF, 可选)
      │           ├── thumb.mp4    (可选)
      │           ├── node.zmetal / node.ma / ...
      │           └── textures/
  - 扫描逻辑镜像 core/manager.py：
      · 子库列表 = 核心 7 库 + config.json 自定义追加
      · sub_library 优先取目录链上 FolderMetadata.fdata 的 type 字段
      · 分类 = .zasset 的父文件夹链（相对子库根）
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

# ── 与 core/manager.py 保持一致的核心子库 ──────────────────
CORE_SUB_LIBRARIES = {
    "materials": "材质",
    "models": "模型",
    "lights": "灯光",
    "textures": "贴图",
    "scenes": "场景",
    "hdr": "HDR",
    "ani": "动态",
}

# 缩略图优先级（与 ZassetIO.THUMB_DISPLAY_PATHS 一致：GIF 优先于 PNG）
THUMB_FILES = ["thumb.aicon", "thumb.sicon"]

# 详情页贴图目录下允许内联预览的图片扩展名
INLINE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff"}


def read_json(path: str, default=None):
    """安全读取 JSON 文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def sniff_image_mime(head: bytes) -> str:
    """根据魔数推断图片 MIME 类型（.sicon/.aicon 扩展名不可靠，按内容判断）"""
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:2] == b"BM":
        return "image/bmp"
    return "application/octet-stream"


class LibraryScanner:
    """资产库只读扫描器。

    用法:
        sc = LibraryScanner(config_path)
        result = sc.scan(library_path)   # 返回完整快照 dict
    """

    def __init__(self, config_path: str = ""):
        self.config_path = config_path
        self.config = read_json(config_path, {}) or {}
        cfg_libs = self.config.get("sub_libraries", {}) or {}
        merged = dict(CORE_SUB_LIBRARIES)
        merged.update(cfg_libs)
        self.sub_libraries: Dict[str, str] = merged          # id → 中文名
        self.category_colors: Dict[str, str] = self.config.get("category_colors", {}) or {}
        self.common_tags: Dict[str, List[str]] = self.config.get("common_tags", {}) or {}
        self.default_sub_categories: Dict[str, list] = self.config.get("default_sub_categories", {}) or {}

    # ── 对外主入口 ─────────────────────────────────────

    def scan(self, library_path: str) -> dict:
        """扫描资产库，返回快照。

        返回结构:
        {
            "library_path": str,
            "scanned_at": float,            # unix 时间戳
            "scan_ms": int,
            "total": int,
            "sub_libraries": [              # 侧栏树（仅含有资产的子库）
                {"id", "name", "count", "color",
                 "categories": [{"id", "name", "count", "children": [...]}]}
            ],
            "all_sub_libraries": {...},     # 配置中的完整子库表（含 0 资产）
            "tag_cloud": {sub_lib_id: [{"tag", "count"}]},
            "assets": [asset_dict, ...]
        }
        """
        t0 = time.time()
        library_path = os.path.abspath(library_path)
        assets: List[dict] = []
        seen_ids = set()
        failures = 0

        sub_lib_dirs = []
        for sub_id, sub_name in self.sub_libraries.items():
            sub_dir = os.path.join(library_path, sub_id)
            if os.path.isdir(sub_dir):
                sub_lib_dirs.append((sub_id, sub_name, sub_dir))

        for sub_id, sub_name, sub_dir in sub_lib_dirs:
            for asset in self._scan_sub_library(sub_id, sub_name, sub_dir):
                if asset["id"] and asset["id"] in seen_ids:
                    continue  # UUID 冲突：跳过（与 manager 行为一致）
                seen_ids.add(asset["id"])
                assets.append(asset)

        snapshot = self._build_snapshot(library_path, assets, failures)
        snapshot["scan_ms"] = int((time.time() - t0) * 1000)
        snapshot["scanned_at"] = time.time()
        return snapshot

    # ── 子库扫描 ───────────────────────────────────────

    def _scan_sub_library(self, sub_id: str, sub_name: str, sub_dir: str) -> List[dict]:
        """遍历子库目录，收集所有 .zasset 资产"""
        results = []
        for dirpath, dirnames, _filenames in os.walk(sub_dir):
            # 先取出当前层级的 .zasset（文件夹即资产），再从遍历列表中剔除，
            # 避免深入资产内部（textures / associated 等）
            zassets_here = [d for d in dirnames if d.endswith(".zasset")]
            dirnames[:] = [d for d in dirnames
                           if not d.endswith(".zasset") and d != "textures"]
            for dname in zassets_here:
                zpath = os.path.join(dirpath, dname)
                asset = self._parse_zasset(zpath, sub_dir, sub_id)
                if asset:
                    results.append(asset)
        return results

    def _parse_zasset(self, zasset_path: str, sub_dir: str, sub_id: str) -> Optional[dict]:
        """解析单个 .zasset 文件夹 → asset dict"""
        meta = read_json(os.path.join(zasset_path, "meta.json"))
        if not isinstance(meta, dict):
            return None

        # 分类链：.zasset 相对子库根的目录层级（不含资产本身）
        try:
            rel = os.path.relpath(os.path.dirname(zasset_path), sub_dir)
        except ValueError:
            rel = "."
        segs = [s for s in rel.replace("\\", "/").split("/") if s not in (".", "")]
        category_chain = segs if segs else [meta.get("category", "") or "custom"]

        # 子库归属：目录链上 FolderMetadata.fdata 的 type 优先（镜像 manager 逻辑）
        folder_sub_lib = self._folder_chain_type(os.path.dirname(zasset_path), sub_dir) or sub_id

        # 文件清单（一次性 listdir，供缩略图/贴图判断）
        try:
            entries = os.listdir(zasset_path)
        except OSError:
            entries = []

        # ── 预览图系列：thumb*.sicon / thumb*.aicon / thumb*.mp4 ──
        # 一个资产可有多张预览图（如 thumb.sicon + thumb_2.sicon + ...），
        # 按序号自然排序（thumb.sicon, thumb_2.sicon, ..., thumb_10.sicon）
        thumb_files = []
        for n in entries:
            low = n.lower()
            if low.startswith("thumb") and (
                    low.endswith(".sicon") or low.endswith(".aicon") or low.endswith(".mp4")):
                kind = "aicon" if low.endswith(".aicon") else \
                    ("mp4" if low.endswith(".mp4") else "sicon")
                thumb_files.append({"name": n, "kind": kind})
        if thumb_files:
            import re as _re

            def _thumb_key(t):
                m = _re.search(r"_(\d+)\.(?:sicon|aicon|mp4)$", t["name"])
                return (0 if not m else int(m.group(1)), t["name"].lower())

            thumb_files.sort(key=_thumb_key)

        has_aicon = any(t["kind"] == "aicon" for t in thumb_files)
        has_sicon = any(t["kind"] == "sicon" for t in thumb_files)
        has_mp4 = any(t["kind"] == "mp4" for t in thumb_files)

        # 贴图统计
        tex_dir = os.path.join(zasset_path, "textures")
        textures = []
        if os.path.isdir(tex_dir):
            for root, _dirs, files in os.walk(tex_dir):
                for fn in files:
                    fp = os.path.join(root, fn)
                    try:
                        size = os.path.getsize(fp)
                    except OSError:
                        size = 0
                    textures.append({
                        "name": fn,
                        "rel": os.path.relpath(fp, zasset_path).replace("\\", "/"),
                        "size": size,
                    })
        textures.sort(key=lambda t: t["name"].lower())

        try:
            mtime = os.path.getmtime(zasset_path)
        except OSError:
            mtime = 0.0

        name = meta.get("name", "") or os.path.basename(zasset_path)[:-len(".zasset")]
        name_cn = meta.get("name_cn", "") or name

        return {
            "id": meta.get("id", "") or zasset_path,
            "name": name,
            "name_cn": name_cn,
            "sub_library": folder_sub_lib,
            "category_chain": category_chain,
            "category": category_chain[-1] if category_chain else "custom",
            "tags": list(meta.get("tags", []) or []),
            "node_type": meta.get("node_type", "") or "",
            "software": meta.get("software", "") or "",
            "renderer": meta.get("renderer", "") or "",
            "color_space": meta.get("color_space", "") or "",
            "create_date": meta.get("create_date") or meta.get("export_date", "") or "",
            "resolution": meta.get("resolution", "") or "",
            "notes": meta.get("notes", "") or "",
            "formats": list(meta.get("formats") or meta.get("exported_formats") or []),
            "ani": list(meta.get("ani", []) or []),
            "has_variants": bool(meta.get("variant_types")),
            "zasset_path": zasset_path,
            "zasset_name": os.path.basename(zasset_path),
            "file_mtime": mtime,
            "has_aicon": has_aicon,
            "has_sicon": has_sicon,
            "has_mp4": has_mp4,
            "thumb_files": thumb_files,     # 预览图系列 [{name, kind}]
            "thumb_count": len(thumb_files),
            "textures": textures,
            "texture_count": len(textures),
        }

    def _folder_chain_type(self, start_dir: str, stop_dir: str) -> str:
        """从 start_dir 向上读到 stop_dir，返回 FolderMetadata.fdata 中首个 type 字段"""
        cur = start_dir
        stop_abs = os.path.abspath(stop_dir)
        while cur and os.path.abspath(cur).startswith(stop_abs):
            fdata = read_json(os.path.join(cur, "FolderMetadata.fdata")) or {}
            t = fdata.get("type", "")
            if t:
                return t
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return ""

    # ── 快照构建（分类树 / 标签云 / 计数） ──────────────

    def _build_snapshot(self, library_path: str, assets: List[dict], failures: int) -> dict:
        # 分类文件夹显示名缓存
        folder_names: Dict[str, str] = {}

        def folder_display_name(folder_path: str) -> str:
            if folder_path not in folder_names:
                fdata = read_json(os.path.join(folder_path, "FolderMetadata.fdata")) or {}
                folder_names[folder_path] = fdata.get("name_cn", "") or os.path.basename(folder_path)
            return folder_names[folder_path]

        # 子库 → 树节点缓存 {"id","name","children":{id:node}}
        trees: Dict[str, dict] = {}
        for sub_id, sub_name in self.sub_libraries.items():
            trees[sub_id] = {
                "id": sub_id, "name": sub_name, "count": 0,
                "children": {}, "_path": os.path.join(library_path, sub_id),
            }

        # 标签云
        tag_counts: Dict[str, Dict[str, int]] = {}

        for a in assets:
            sub = a["sub_library"]
            if sub not in trees:
                trees[sub] = {"id": sub, "name": sub, "count": 0,
                              "children": {}, "_path": os.path.join(library_path, sub)}
            node = trees[sub]
            node["count"] += 1

            # 沿分类链建树并累加计数
            cur = node
            sub_root = node["_path"]
            partial = sub_root
            for seg in a["category_chain"]:
                partial = os.path.join(partial, seg)
                child = cur["children"].get(seg)
                if child is None:
                    child = {"id": seg, "name": folder_display_name(partial),
                             "count": 0, "children": {}, "_path": partial}
                    cur["children"][seg] = child
                child["count"] += 1
                cur = child

            # 标签计数
            bucket = tag_counts.setdefault(sub, {})
            for t in a["tags"]:
                t = (t or "").strip()
                if t:
                    bucket[t] = bucket.get(t, 0) + 1

        # 序列化树（按 count 降序 + 名称排序）
        def ser(node: dict) -> dict:
            children = sorted(node["children"].values(),
                              key=lambda n: (-n["count"], n["name"]))
            out = {"id": node["id"], "name": node["name"], "count": node["count"],
                   "children": [ser(c) for c in children]}
            return out

        sub_libs_out = []
        for sub_id in sorted(trees.keys(),
                             key=lambda s: (-trees[s]["count"], s)):
            node = trees[sub_id]
            sub_libs_out.append({
                "id": sub_id,
                "name": self.sub_libraries.get(sub_id, node["name"]),
                "count": node["count"],
                "color": self.category_colors.get(sub_id, "#8a8f98"),
                "categories": [ser(c) for c in
                               sorted(node["children"].values(),
                                      key=lambda n: (-n["count"], n["name"]))],
            })

        tag_cloud = {
            sub_id: [{"tag": t, "count": c} for t, c in
                     sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))]
            for sub_id, bucket in tag_counts.items()
        }

        return {
            "library_path": library_path,
            "total": len(assets),
            "failures": failures,
            "all_sub_libraries": self.sub_libraries,
            "sub_libraries": sub_libs_out,
            "tag_cloud": tag_cloud,
            "assets": assets,
        }


class LibraryState:
    """线程安全的库状态容器（后台扫描 + 原子快照切换）"""

    def __init__(self, config_path: str = ""):
        self._lock = threading.Lock()
        self._snapshot: Optional[dict] = None
        self._scanning = False
        self._library_path = ""
        self._error = ""
        self.scanner = LibraryScanner(config_path)

    @property
    def library_path(self) -> str:
        return self._library_path

    def scan(self, library_path: str, background: bool = False):
        """扫描（可后台执行）"""
        self._library_path = library_path
        if not library_path or not os.path.isdir(library_path):
            with self._lock:
                self._snapshot = None
                self._error = "路径不存在或不可访问"
            return False

        if background:
            t = threading.Thread(target=self._scan_sync, args=(library_path,), daemon=True)
            t.start()
            return True
        return self._scan_sync(library_path)

    def _scan_sync(self, library_path: str) -> bool:
        with self._lock:
            self._scanning = True
        try:
            snap = self.scanner.scan(library_path)
            snap["library_path"] = library_path
            with self._lock:
                self._snapshot = snap
                self._error = ""
                self._scanning = False
            return True
        except Exception as e:  # 扫描异常不拖垮服务
            with self._lock:
                self._error = str(e)
                self._scanning = False
            return False

    def get_snapshot(self) -> Optional[dict]:
        with self._lock:
            return self._snapshot

    @property
    def scanning(self) -> bool:
        with self._lock:
            return self._scanning

    @property
    def error(self) -> str:
        with self._lock:
            return self._error
