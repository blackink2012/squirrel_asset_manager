# -*- coding: utf-8 -*-
"""
Material — 材质数据类

从 .zasset 文件夹解析材质:
  - meta.json:      元数据（id UUID, name, name_cn, tags 等）
  - node.zmetal:    节点属性数据
  - thumb.sicon:    缩略图
  - node.mcm:       对象映射（可选）
  - node.ma/fbx/…:  几何体文件（可选）
  - textures/:      贴图目录（可选）

通过 to_dict() 桥接 UI 层，输出格式与 MOCK_MATERIALS 完全兼容。
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


# ── 颜色推断：按材质类型给默认色 ──────────────────────

import os
import json

_CONFIG_COLORS = {}
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Assets", "preset", "config.json")
if os.path.isfile(_config_path):
    try:
        with open(_config_path, "r", encoding="utf-8") as f:
            _cfg = json.load(f)
            _CONFIG_COLORS = _cfg.get("node_type_colors", {})
    except Exception:
        pass

_DEFAULT_COLOR = "#606060"


def _infer_color(node_type: str) -> str:
    """根据材质节点类型推断显示颜色（从 config.json 读取）"""
    return _CONFIG_COLORS.get(node_type, _DEFAULT_COLOR)


def reload_color_config():
    """重新加载颜色配置（设置界面改动后调用）"""
    global _CONFIG_COLORS
    if os.path.isfile(_config_path):
        try:
            with open(_config_path, "r", encoding="utf-8") as f:
                _cfg = json.load(f)
                _CONFIG_COLORS = _cfg.get("node_type_colors", {})
        except Exception:
            pass


# ── Material 数据类 ────────────────────────────────────

@dataclass
class Material:
    """
    材质数据类，对应 _meta.json 中的一个材质。

    字段说明:
        id:             全局唯一标识（UUID），来自 _meta.json
        name:           英文名称，用于 Maya 节点命名和文件关联
        name_cn:        中文显示名称，为空时 UI 回退到 name
        category:       分类 ID，无分类时默认 "custom"
        tags:           标签列表
        node_type:      材质节点类型（如 standardSurface）
        json_path:      _meta.json 文件路径（主入口）
        thumbnail_path: 缩略图路径，默认为空
        software:       导出软件名
        renderer:       渲染器名
        color_space:    色彩空间
        create_date:    创建时间
        source:         原始 JSON 数据引用（惰性加载，repr 不显示）

    计算属性:
        node_json_path: 节点属性 JSON 路径（从 json_path 推导）
    """

    id: str
    name: str
    name_cn: str = ""
    sub_library: str = ""     # 所属子库（materials/textures/models/lights/scenes/hdr）
    category: str = "custom"
    tags: List[str] = field(default_factory=list)
    node_type: str = ""
    json_path: str = ""
    thumbnail_path: str = ""
    software: str = ""
    renderer: str = ""
    color_space: str = ""
    create_date: str = ""
    file_mtime: float = 0.0  # 文件修改时间戳，用于按时间排序
    exported_formats: List[str] = field(default_factory=list)
    ani: List[str] = field(default_factory=list)  # 动画格式列表
    resolution: str = ""     # 贴图分辨率，如 "2048x2048"
    notes: str = ""          # 用户注释
    # ── 变体字段 ──
    variant_types: List[str] = field(default_factory=list)  # ["lod", "version"]
    default_version: str = ""   # 默认版本 id
    default_lod: str = ""       # 默认 LOD id
    # ── 缓存字段 ──
    node_bytes: Optional[bytes] = field(default=None, repr=False)   # node.zmetal 内容
    thumb_bytes: Optional[bytes] = field(default=None, repr=False)  # thumb.sicon 内容
    mcm_bytes: Optional[bytes] = field(default=None, repr=False)    # node.mcm 内容
    source: Optional[Dict[str, Any]] = field(default=None, repr=False)

    # ── 显示名称 ──

    def get_display_name(self) -> str:
        """返回中文名，为空时回退到英文名"""
        if self.name_cn and self.name_cn.strip():
            return self.name_cn
        return self.name or self.id

    # ── 派生路径 ──

    @property
    def node_json_path(self) -> str:
        """节点属性 JSON 路径。

        .zasset 模式: 返回空字符串（内容在 node_bytes 中），
                      调用方应通过 read_node_content() 获取。
        """
        if not self.json_path:
            return ""
        if self.is_zasset:
            return ""
        return os.path.join(os.path.dirname(self.json_path), f"{self.name}.zmetal")

    # ── .zasset 属性 ──

    @property
    def is_zasset(self) -> bool:
        """判断当前资产是否为 .zasset 文件夹模式。"""
        return bool(self.json_path and self.json_path.endswith(".zasset"))

    @property
    def zasset_path(self) -> str:
        """返回 .zasset 文件夹路径"""
        if self.is_zasset and self.json_path:
            return self.json_path
        return ""

    def read_node_content(self) -> Optional[bytes]:
        """读取节点属性文件内容。.zasset: 从内部 node.zmetal 读取；否则从外部 JSON 文件读取。"""
        if self.is_zasset:
            if self.node_bytes is None:
                from core.zasset_io import ZassetIO
                self.node_bytes = ZassetIO.read_node(self.json_path)
            return self.node_bytes
        npath = self.node_json_path
        if npath and os.path.isfile(npath):
            try:
                with open(npath, 'rb') as f:
                    return f.read()
            except OSError:
                pass
        return None

    def read_thumbnail_content(self) -> Optional[bytes]:
        """读取缩略图内容。

        .zasset 模式: 返回缓存的 thumb_bytes
        非 .zasset: 从 thumbnail_path 文件读取（兼容外部临时文件）

        Returns:
            bytes: 缩略图数据，失败返回 None
        """
        if self.is_zasset:
            if self.thumb_bytes is None:
                from core.zasset_io import ZassetIO
                self.thumb_bytes = ZassetIO.read_thumbnail(self.json_path)
            return self.thumb_bytes
        if self.thumbnail_path and os.path.isfile(self.thumbnail_path):
            try:
                with open(self.thumbnail_path, 'rb') as f:
                    return f.read()
            except OSError:
                pass
        return None

    # ── 序列化（UI 桥接） ──

    def to_dict(self, include_thumb: bool = True) -> dict:
        """
        序列化为 UI 层兼容的 dict（与 MOCK_MATERIALS 格式一致）。

        Args:
            include_thumb: 是否读取缩略图字节。批量填充网格时传 False，
                           缩略图由网格按需懒加载；单材质预览时保持 True。

        Returns:
            dict: 包含 id, name, name_cn, category, tags, node_type,
                  color, thumbnail_path, json_path, node_json_path,
                  software, renderer, color_space, create_date
        """
        tb = self.read_thumbnail_content() if include_thumb else None
        d = {
            "id":               self.id,
            "name":             self.name,
            "name_cn":          self.name_cn or self.name,
            "sub_library":      self.sub_library,
            "category":         self.category,
            "tags":             list(self.tags),
            "node_type":        self.node_type,
            "color":            _infer_color(self.node_type),
            "thumbnail_path":   self.thumbnail_path,
            "json_path":        self.json_path,
            "node_json_path":   self.node_json_path,
            "is_zasset":        self.is_zasset,
            "zasset_path":      self.zasset_path,
            "thumb_bytes":      tb,
            "software":         self.software,
            "renderer":         self.renderer,
            "color_space":      self.color_space,
            "create_date":      self.create_date,
            "file_mtime":       self.file_mtime,
            "exported_formats": list(self.exported_formats),
            "ani":              list(self.ani),
            "resolution":       self.resolution,
            # ── 变体字段 ──
            "variant_types":    list(self.variant_types),
            "default_version":  self.default_version,
            "default_lod":      self.default_lod,
            "has_variants":     bool(self.variant_types),
            "notes":            self.notes,
        }
        return d

    # ── 工厂方法 ──

    @classmethod
    def from_json(cls, path: str, json_handler=None) -> List["Material"]:
        """
        从 .zasset 文件夹解析 Material 实例。

        从 .zasset 文件夹内 meta.json 读取元数据。

        Args:
            path: .zasset 文件路径
            json_handler: 保留参数（兼容旧签名），不再使用

        Returns:
            list[Material]: 包含 1 个 Material 的列表，非 .zasset 返回空列表
        """
        if path.endswith(".zasset"):
            return cls._from_zasset(path)

        return []

    # ── .zasset 工厂 ──

    @classmethod
    def _from_zasset(cls, zasset_path: str) -> List["Material"]:
        """从 .zasset 文件夹解析 Material。

        读取 .zasset 文件夹内 meta.json 作为元数据，缩略图/node/mcm 通过
        惰性属性按需加载。
        """
        from core.zasset_io import ZassetIO

        data = ZassetIO.read_meta(zasset_path)
        if not data or not isinstance(data, dict):
            return []

        material_id = data.get("id", "")
        name = data.get("name", "")
        name_cn = data.get("name_cn", "")
        node_type = data.get("node_type", "")
        category = data.get("category", "") or "custom"
        tags = data.get("tags", []) or []
        software = data.get("software", "")
        renderer = data.get("renderer", "")
        color_space = data.get("color_space", "")
        create_date = data.get("create_date") or data.get("export_date", "")
        resolution = data.get("resolution", "")
        # meta.json 中格式字段名为 "formats"（zasset_builder/export_orchestrator 写入），
        # 兼容旧数据中可能存在的 "exported_formats" 字段
        exported_formats = data.get("formats") or data.get("exported_formats", []) or []
        ani = data.get("ani", []) or []
        variant_types = data.get("variant_types", []) or []
        default_version = data.get("default_version", "")
        default_lod = data.get("default_lod", "")
        notes = data.get("notes", "")

        # 惰性加载：仅保存路径，thumb/node/mcm 首次访问时读取
        from core.zasset_io import ZassetIO as _ZIO

        # 从文件系统获取修改时间
        abs_path = os.path.abspath(zasset_path)
        try:
            file_mtime = os.path.getmtime(abs_path)
        except OSError:
            file_mtime = 0.0

        mat = cls(
            id=material_id,
            name=name,
            name_cn=name_cn or name,
            sub_library="",  # 由 _scan_materials_directory 在 manager 层填充
            category=category,
            tags=list(tags),
            node_type=node_type,
            json_path=abs_path,
            thumbnail_path="",  # 内容在 thumb_bytes
            software=software,
            renderer=renderer,
            color_space=color_space,
            create_date=create_date,
            file_mtime=file_mtime,
            exported_formats=exported_formats,
            ani=ani,
            resolution=resolution,
            variant_types=variant_types,
            default_version=default_version,
            default_lod=default_lod,
            notes=notes,
            # 惰性加载：启动时不读取
            node_bytes=None,
            thumb_bytes=None,
            mcm_bytes=None,
            source=data,
        )
        return [mat]


# ── 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # 添加父目录到 path，以便导入 json_handler
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.json_handler import JSONHandler

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
    print("Material 自测")
    print("=" * 50)

    # ── T1.5.5: 基础构造 + to_dict ──
    print("\n[T1.5.5] Material 基础构造 + to_dict")

    mat = Material(
        id="550e8400-e29b-41d4-a716-446655440000",
        name="AltaCarpet_STRD",
        name_cn="Alta地毯",
        category="custom",
        tags=["布料", "pbr"],
        node_type="standardSurface",
        json_path="/tmp/materials/custom/AltaCarpet_STRD/AltaCarpet_STRD_meta.json",
    )

    check(mat.get_display_name() == "Alta地毯",
          "get_display_name 返回中文名")
    expected_node = os.path.join(os.path.dirname(mat.json_path), "AltaCarpet_STRD.zmetal")
    check(os.path.normpath(mat.node_json_path) == os.path.normpath(expected_node),
          "node_json_path 从 meta 路径正确推导")

    mat_no_cn = Material(
        id="uuid-2", name="TestMaterial", node_type="standardSurface",
    )
    check(mat_no_cn.get_display_name() == "TestMaterial",
          "无 name_cn 时 get_display_name 返回 name")
    check(mat_no_cn.name_cn == "",
          "未传 name_cn 时默认为空字符串")

    d = mat.to_dict()
    required_keys = {"id", "name", "name_cn", "category", "tags",
                     "node_type", "color", "thumbnail_path", "json_path",
                     "node_json_path", "exported_formats"}
    check(required_keys.issubset(set(d.keys())),
          f"to_dict 包含所有必需键: {sorted(d.keys())}")

    check(d["color"] == "#A0A0A0",
          "standardSurface 颜色推断为 #A0A0A0")

    mat_unknown = Material(id="X", name="X", node_type="unknown_type")
    check(mat_unknown.to_dict()["color"] == "#606060",
          "未知类型 → 默认颜色 #606060")

    # ── T1.5.6: from_json（新 _meta.json 格式） ──
    print("\n[T1.5.6] Material.from_json() — _meta.json 格式")

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试 _meta.json
        meta_path = os.path.join(tmpdir, "TestMetal_meta.json")
        meta_data = {
            "id": "uuid-test-metal",
            "version": "2.0",
            "software": "Maya",
            "renderer": "arnold",
            "color_space": "ACEScg",
            "create_date": "2026-05-12",
            "name": "TestMetal",
            "name_cn": "测试金属",
            "node_type": "standardSurface",
            "category": "metal",
            "tags": ["pbr", "metal"]
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False)

        # 创建同目录缩略图（.sicon 格式）
        thumb_path = os.path.join(tmpdir, "TestMetal.sicon")
        with open(thumb_path, "w") as f:
            f.write("fake sicon")

        mats = Material.from_json(meta_path, JSONHandler)
        check(len(mats) == 1, "_meta.json → 返回 1 个 Material")
        if mats:
            m = mats[0]
            check(m.id == "uuid-test-metal", f"id 正确: {m.id}")
            check(m.name == "TestMetal", "name 正确")
            check(m.name_cn == "测试金属", "name_cn 正确")
            check(m.category == "metal", "category 正确")
            check(m.tags == ["pbr", "metal"], "tags 正确")
            check(m.software == "Maya", "software 正确")
            check(m.renderer == "arnold", "renderer 正确")
            check(m.thumbnail_path == thumb_path, "缩略图路径正确")
            check(m.node_json_path == os.path.join(tmpdir, "TestMetal.zmetal"),
                  "node_json_path 推导正确")

    # 测试空 _meta.json（空 dict → 返回空列表，无有效字段）
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_path = os.path.join(tmpdir, "empty_meta.json")
        with open(empty_path, "w") as f:
            f.write("{}")
        mats = Material.from_json(empty_path, JSONHandler)
        check(len(mats) == 0, "空 _meta.json → 返回空列表（无有效字段）")

    # ── T1.5.7: to_dict 兼容性 ──
    print("\n[T1.5.7] to_dict 格式兼容性")

    mock_compatible = mat.to_dict()
    check(mock_compatible["id"] == "550e8400-e29b-41d4-a716-446655440000",
          "to_dict 'id' 正确")
    check(isinstance(mock_compatible["tags"], list),
          "to_dict 'tags' 是列表")
    check("color" in mock_compatible,
          "to_dict 包含 'color' 字段")
    check("thumbnail_path" in mock_compatible,
          "to_dict 包含 'thumbnail_path' 字段")
    check("node_json_path" in mock_compatible,
          "to_dict 包含 'node_json_path' 字段")

    # ── T1.5.8: from_json — .zasset 格式 ──
    print("\n[T1.5.8] Material.from_json() — .zasset 格式")

    with tempfile.TemporaryDirectory() as tmpdir:
        zasset_path = os.path.join(tmpdir, "TestZAsset.zasset")
        os.makedirs(zasset_path, exist_ok=True)
        meta_zasset = {
            "id": "uuid-zasset-test",
            "version": "2.0",
            "software": "Maya",
            "renderer": "arnold",
            "color_space": "ACEScg",
            "create_date": "2026-05-18",
            "name": "TestZAsset",
            "name_cn": "测试资产",
            "node_type": "standardSurface",
            "category": "metal",
            "tags": ["pbr", "zip"]
        }
        with open(os.path.join(zasset_path, "meta.json"), 'w', encoding='utf-8') as f:
            json.dump(meta_zasset, f, ensure_ascii=False)
        with open(os.path.join(zasset_path, "node.zmetal"), 'wb') as f:
            f.write(b'{"nodes":[{"type":"standardSurface"}]}')
        with open(os.path.join(zasset_path, "thumb.sicon"), 'wb') as f:
            f.write(b"fake-thumb-data")

        mats = Material.from_json(zasset_path)
        check(len(mats) == 1, ".zasset → 返回 1 个 Material")
        if mats:
            z = mats[0]
            check(z.id == "uuid-zasset-test", f".zasset id 正确: {z.id}")
            check(z.name == "TestZAsset", ".zasset name 正确")
            check(z.name_cn == "测试资产", ".zasset name_cn 正确")
            check(z.category == "metal", ".zasset category 正确")
            check(z.tags == ["pbr", "zip"], ".zasset tags 正确")
            check(z.software == "Maya", ".zasset software 正确")
            check(z.renderer == "arnold", ".zasset renderer 正确")
            check(z.is_zasset is True, "is_zasset 为 True")
            check(z.zasset_path == zasset_path, "zasset_path 正确")
            check(z.thumbnail_path == "", ".zasset 缩略图路径为空（内容在 thumb_bytes）")
            check(z.thumb_bytes == b"fake-thumb-data", "thumb_bytes 已缓存")
            check(z.node_bytes is not None, "node_bytes 已缓存")
            check(z.node_bytes == b'{"nodes":[{"type":"standardSurface"}]}',
                  "node_bytes 内容正确")
            check(z.node_json_path == "", ".zasset node_json_path 为空（非文件路径）")
            check(z.json_path == os.path.abspath(zasset_path), "json_path 指向 .zasset")

            # to_dict 检查
            d = z.to_dict()
            check(d["is_zasset"] is True, "to_dict is_zasset 正确")
            check(d["zasset_path"] == zasset_path, "to_dict zasset_path 正确")
            check(d["thumbnail_path"] == "", "to_dict thumbnail_path 为空")
            check(d["node_json_path"] == "", "to_dict node_json_path 为空")

    # ── 结果 ──
    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" ✅ 全部通过")
    print("=" * 50)
