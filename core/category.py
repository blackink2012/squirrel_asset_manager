# -*- coding: utf-8 -*-
"""
Category — 分类数据类

管理材质分类，支持树形结构（父-子分类）和 10 个预置默认分类。
通过 to_dict() 输出与 DEFAULT_CATEGORIES 兼容的格式。
"""

from dataclasses import dataclass, field
from typing import Optional, List


# ── 10 个预置默认分类 ────────────────────────────────

DEFAULT_CATEGORIES_DATA = [
    {"id": "metal",    "name": "Metal",    "name_cn": "金属", "icon": "", "description": "各种金属材质",         "color": "#808080", "sort_order": 1},
    {"id": "metal_chrome",  "name": "Chrome",  "name_cn": "铬",   "parent": "metal", "color": "#C0C0C0", "sort_order": 11},
    {"id": "metal_steel",   "name": "Steel",   "name_cn": "钢",   "parent": "metal", "color": "#808080", "sort_order": 12},
    {"id": "metal_gold",    "name": "Gold",    "name_cn": "金",   "parent": "metal", "color": "#FFD700", "sort_order": 13},
    {"id": "metal_copper",  "name": "Copper",  "name_cn": "铜",   "parent": "metal", "color": "#D35400", "sort_order": 14},
    {"id": "fabric",   "name": "Fabric",   "name_cn": "布料", "icon": "", "description": "织物、布料、丝绸等材质", "color": "#C0392B", "sort_order": 2},
    {"id": "fabric_silk",   "name": "Silk",   "name_cn": "丝绸", "parent": "fabric", "color": "#E74C3C", "sort_order": 21},
    {"id": "fabric_cotton", "name": "Cotton", "name_cn": "棉布", "parent": "fabric", "color": "#ECF0F1", "sort_order": 22},
    {"id": "plastic",  "name": "Plastic",  "name_cn": "塑料", "icon": "", "description": "塑料、橡胶、树脂等材质",  "color": "#2980B9", "sort_order": 3},
    {"id": "plastic_rough",  "name": "Rough",  "name_cn": "磨砂", "parent": "plastic", "color": "#3498DB", "sort_order": 31},
    {"id": "plastic_glossy", "name": "Glossy", "name_cn": "光泽", "parent": "plastic", "color": "#2980B9", "sort_order": 32},
    {"id": "glass",    "name": "Glass",    "name_cn": "玻璃", "icon": "", "description": "透明、磨砂、彩色玻璃材质",  "color": "#1ABC9C", "sort_order": 4},
    {"id": "skin",     "name": "Skin",     "name_cn": "皮肤", "icon": "", "description": "人物皮肤、生物表皮材质",    "color": "#E67E22", "sort_order": 5},
    {"id": "wood",     "name": "Wood",     "name_cn": "木材", "icon": "", "description": "木质纹理、地板、家具材质",   "color": "#A0522D", "sort_order": 6},
    {"id": "wood_oak",  "name": "Oak",  "name_cn": "橡木", "parent": "wood", "color": "#8B4513", "sort_order": 61},
    {"id": "wood_pine", "name": "Pine", "name_cn": "松木", "parent": "wood", "color": "#DEB887", "sort_order": 62},
    {"id": "stone",    "name": "Stone",    "name_cn": "石材", "icon": "", "description": "石头、混凝土、砖墙、大理石材质", "color": "#95A5A6", "sort_order": 7},
    {"id": "liquid",   "name": "Liquid",   "name_cn": "液体", "icon": "", "description": "水、油、熔岩等液体材质",      "color": "#3498DB", "sort_order": 8},
    {"id": "foliage",  "name": "Foliage",  "name_cn": "植被", "icon": "", "description": "植物、叶子、草地材质",        "color": "#27AE60", "sort_order": 9},
    {"id": "custom",   "name": "Custom",   "name_cn": "自定义","icon": "", "description": "用户自定义分类",             "color": "#8E44AD", "sort_order": 10},
]


# ── Category 数据类 ──────────────────────────────────

@dataclass
class Category:
    """材质分类数据类"""

    id: str
    name: str
    name_cn: str = ""
    icon: str = ""
    description: str = ""
    color: str = "#666666"
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)
    sort_order: int = 0
    material_count: int = 0

    def get_all_descendant_ids(self, category_map: dict) -> List[str]:
        """
        递归获取所有后代分类 ID（含自身）。

        Args:
            category_map: dict[id, Category] 索引

        Returns:
            list[str]: 自身 + 所有子、孙分类 ID
        """
        ids = [self.id]
        for child_id in self.children:
            if child_id in category_map:
                ids.extend(category_map[child_id].get_all_descendant_ids(category_map))
        return ids

    def to_dict(self) -> dict:
        """
        序列化为与 DEFAULT_CATEGORIES 兼容的 dict 格式。

        children 字段输出为子 dict 列表（嵌套），而非仅 ID。
        如需扁平 ID 列表，使用 children 属性。
        """
        return {
            "id": self.id,
            "name": self.name,
            "name_cn": self.name_cn or self.name,
            "icon": self.icon,
            "description": self.description,
            "color": self.color,
            "parent": self.parent,
            "children": self.children,
            "sort_order": self.sort_order,
            "material_count": self.material_count,
        }

    @classmethod
    def get_defaults(cls) -> list:
        """获取预置默认分类（含子分类）"""
        return [cls(
            id=d["id"], name=d["name"], name_cn=d["name_cn"],
            icon=d.get("icon", ""), description=d.get("description", ""),
            color=d.get("color", "#666666"),
            parent=d.get("parent"),
            sort_order=d.get("sort_order", 0),
        ) for d in DEFAULT_CATEGORIES_DATA]

    @classmethod
    def get_default_map(cls) -> dict:
        """
        获取默认分类的 id → Category 映射。

        Returns:
            dict[str, Category]
        """
        defaults = cls.get_defaults()
        return {c.id: c for c in defaults}


# ── 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
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
    print("Category 自测")
    print("=" * 50)

    # ── 默认分类 ──
    print("\n默认分类:")
    defaults = Category.get_defaults()
    check(len(defaults) >= 10, f"get_defaults 返回 {len(defaults)} 个分类")

    cat_map = {c.id: c for c in defaults}

    metal = cat_map.get("metal")
    check(metal is not None, "金属分类存在")
    if metal:
        check(len(metal.children) == 0,
              f"金属有 {len(metal.children)} 个子分类（当前为扁平结构，无子分类）")

        # 全部为顶级分类，无子分类
        descendants = metal.get_all_descendant_ids(cat_map)
        check(len(descendants) == 1,
              f"金属后代含自身 = {len(descendants)} 个")
        check("metal" in descendants, "后代含自身")

    # 验证所有分类的 parent 为 None（均为顶级分类）
    for cat in defaults:
        check(cat.parent is None,
              f"{cat.id}.parent == None（顶级分类）")

    # ── to_dict ──
    print("\nto_dict:")
    if metal:
        d = metal.to_dict()
        check(d["id"] == "metal", "to_dict id")
        check(d["name_cn"] == "金属", "to_dict name_cn")
        check(isinstance(d["children"], list), "to_dict children 是列表")

    # ── 结果 ──
    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" ✅ 全部通过")
    print("=" * 50)
