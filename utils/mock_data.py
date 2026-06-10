DEFAULT_CATEGORIES = [
    {"id": "metal",    "name": "Metal",    "name_cn": "\u91d1\u5c5e", "icon": "", "description": "\u91d1\u5c5e\u6750\u8d28", "color": "#808080", "material_count": 4, "parent": None, "children": [
        {"id": "metal_chrome",  "name": "Chrome",  "name_cn": "\u94ec",   "parent": "metal", "material_count": 1, "color": "#C0C0C0"},
        {"id": "metal_steel",   "name": "Steel",   "name_cn": "\u94a2",   "parent": "metal", "material_count": 1, "color": "#808080"},
        {"id": "metal_gold",    "name": "Gold",    "name_cn": "\u91d1",   "parent": "metal", "material_count": 1, "color": "#FFD700"},
        {"id": "metal_copper",  "name": "Copper",  "name_cn": "\u94dc",   "parent": "metal", "material_count": 1, "color": "#D35400"},
    ]},
    {"id": "fabric",   "name": "Fabric",   "name_cn": "\u5e03\u6599", "icon": "", "description": "\u7ec7\u7269\u3001\u5e03\u6599\u3001\u4e1d\u7ef8\u7b49\u6750\u8d28", "color": "#C0392B", "material_count": 2, "parent": None, "children": [
        {"id": "fabric_silk",   "name": "Silk",   "name_cn": "\u4e1d\u7ef8", "parent": "fabric", "material_count": 1, "color": "#E74C3C"},
        {"id": "fabric_cotton", "name": "Cotton", "name_cn": "\u68c9\u5e03", "parent": "fabric", "material_count": 1, "color": "#ECF0F1"},
    ]},
    {"id": "plastic",  "name": "Plastic",  "name_cn": "\u5851\u6599", "icon": "", "description": "\u5851\u6599\u3001\u6a61\u80f6\u3001\u6811\u8102\u7b49\u6750\u8d28", "color": "#2980B9", "material_count": 2, "parent": None, "children": [
        {"id": "plastic_rough",  "name": "Rough",  "name_cn": "\u78e8\u7802", "parent": "plastic", "material_count": 1, "color": "#3498DB"},
        {"id": "plastic_glossy", "name": "Glossy", "name_cn": "\u5149\u6cfd", "parent": "plastic", "material_count": 1, "color": "#2980B9"},
    ]},
    {"id": "glass",    "name": "Glass",    "name_cn": "\u73bb\u7483", "icon": "", "description": "\u900f\u660e\u3001\u78e8\u7802\u3001\u5f69\u8272\u73bb\u7483\u6750\u8d28", "color": "#1ABC9C", "material_count": 1, "parent": None, "children": []},
    {"id": "skin",     "name": "Skin",     "name_cn": "\u76ae\u80a4", "icon": "", "description": "\u4eba\u7269\u76ae\u80a4\u3001\u751f\u7269\u8868\u76ae\u6750\u8d28", "color": "#E67E22", "material_count": 1, "parent": None, "children": []},
    {"id": "wood",     "name": "Wood",     "name_cn": "\u6728\u6750", "icon": "", "description": "\u6728\u8d28\u7eb9\u7406\u3001\u5730\u677f\u3001\u5bb6\u5177\u6750\u8d28", "color": "#A0522D", "material_count": 2, "parent": None, "children": [
        {"id": "wood_oak",  "name": "Oak",  "name_cn": "\u6a61\u6728", "parent": "wood", "material_count": 1, "color": "#8B4513"},
        {"id": "wood_pine", "name": "Pine", "name_cn": "\u677e\u6728", "parent": "wood", "material_count": 1, "color": "#DEB887"},
    ]},
    {"id": "stone",    "name": "Stone",    "name_cn": "\u77f3\u6750", "icon": "", "description": "\u77f3\u5934\u3001\u6df7\u51dd\u571f\u3001\u7816\u5899\u3001\u5927\u7406\u77f3\u6750\u8d28", "color": "#95A5A6", "material_count": 1, "parent": None, "children": []},
    {"id": "liquid",   "name": "Liquid",   "name_cn": "\u6db2\u4f53", "icon": "", "description": "\u6c34\u3001\u6cb9\u3001\u7194\u5ca9\u7b49\u6db2\u4f53\u6750\u8d28", "color": "#3498DB", "material_count": 1, "parent": None, "children": []},
    {"id": "foliage",  "name": "Foliage",  "name_cn": "\u690d\u88ab", "icon": "", "description": "\u690d\u7269\u3001\u53f6\u5b50\u3001\u8349\u5730\u6750\u8d28", "color": "#27AE60", "material_count": 1, "parent": None, "children": []},
    {"id": "custom",   "name": "Custom",   "name_cn": "\u81ea\u5b9a\u4e49", "icon": "", "description": "\u7528\u6237\u81ea\u5b9a\u4e49\u5206\u7c7b", "color": "#8E44AD", "material_count": 0, "parent": None, "children": []},
]

MOCK_MATERIALS = [
    {"id": "Metal_Chrome_01",     "name": "Metal_Chrome_01",     "name_cn": "\u91d1\u5c5e_\u94ec_01",     "category": "metal_chrome",  "tags": ["\u91d1\u5c5e", "pbr", "\u94ec"],     "node_type": "aiStandardSurface", "color": "#C0C0C0"},
    {"id": "Metal_Steel_01",      "name": "Metal_Steel_01",      "name_cn": "\u91d1\u5c5e_\u94a2_01",     "category": "metal_steel",   "tags": ["\u91d1\u5c5e", "\u94a2", "\u5de5\u4e1a"],      "node_type": "aiStandardSurface", "color": "#808080"},
    {"id": "Metal_Gold_01",       "name": "Metal_Gold_01",       "name_cn": "\u91d1\u5c5e_\u91d1_01",     "category": "metal_gold",    "tags": ["\u91d1\u5c5e", "\u91d1", "\u8d35\u91d1\u5c5e"],     "node_type": "aiStandardSurface", "color": "#FFD700"},
    {"id": "Metal_Copper_01",     "name": "Metal_Copper_01",     "name_cn": "\u91d1\u5c5e_\u94dc_01",     "category": "metal_copper",  "tags": ["\u91d1\u5c5e", "\u94dc", "\u88c5\u9970"],     "node_type": "aiStandardSurface", "color": "#D35400"},
    {"id": "Fabric_Silk_01",      "name": "Fabric_Silk_01",      "name_cn": "\u5e03\u6599_\u4e1d\u7ef8_01",   "category": "fabric_silk",   "tags": ["\u5e03\u6599", "\u4e1d\u7ef8", "\u5149\u6ed1"],       "node_type": "aiStandardSurface", "color": "#E74C3C"},
    {"id": "Fabric_Cotton_01",    "name": "Fabric_Cotton_01",    "name_cn": "\u5e03\u6599_\u68c9\u5e03_01",   "category": "fabric_cotton", "tags": ["\u5e03\u6599", "\u68c9", "\u54d1\u5149"],     "node_type": "aiStandardSurface", "color": "#ECF0F1"},
    {"id": "Plastic_Rough_01",    "name": "Plastic_Rough_01",    "name_cn": "\u5851\u6599_\u78e8\u7802_01",   "category": "plastic_rough", "tags": ["\u5851\u6599", "\u7c97\u7cd9", "\u54d1\u5149"],       "node_type": "aiStandardSurface", "color": "#3498DB"},
    {"id": "Plastic_Glossy_01",   "name": "Plastic_Glossy_01",   "name_cn": "\u5851\u6599_\u5149\u6cfd_01",   "category": "plastic_glossy","tags": ["\u5851\u6599", "\u5149\u6cfd", "\u5149\u6ed1"],      "node_type": "aiStandardSurface", "color": "#2980B9"},
    {"id": "Glass_Clear_01",      "name": "Glass_Clear_01",      "name_cn": "\u73bb\u7483_\u900f\u660e_01",   "category": "glass",         "tags": ["\u73bb\u7483", "\u900f\u660e", "\u6298\u5c04"], "node_type": "aiStandardSurface", "color": "#1ABC9C"},
    {"id": "Skin_Pale_01",        "name": "Skin_Pale_01",        "name_cn": "\u76ae\u80a4_\u767d\u7699_01",   "category": "skin",          "tags": ["\u76ae\u80a4", "sss", "\u4eba\u7269"],        "node_type": "aiStandardSurface", "color": "#FADBD8"},
    {"id": "Wood_Oak_01",         "name": "Wood_Oak_01",         "name_cn": "\u6728\u6750_\u6a61\u6728_01",   "category": "wood_oak",      "tags": ["\u6728\u6750", "\u6a61\u6728", "\u5730\u677f"],        "node_type": "aiStandardSurface", "color": "#8B4513"},
    {"id": "Wood_Pine_01",        "name": "Wood_Pine_01",        "name_cn": "\u6728\u6750_\u677e\u6728_01",   "category": "wood_pine",     "tags": ["\u6728\u6750", "\u677e\u6728", "\u5bb6\u5177"],       "node_type": "aiStandardSurface", "color": "#DEB887"},
    {"id": "Stone_Marble_01",     "name": "Stone_Marble_01",     "name_cn": "\u77f3\u6750_\u5927\u7406\u77f3_01", "category": "stone",       "tags": ["\u77f3\u6750", "\u5927\u7406\u77f3", "\u5efa\u7b51"],     "node_type": "aiStandardSurface", "color": "#D5D8DC"},
    {"id": "Liquid_Water_01",     "name": "Liquid_Water_01",     "name_cn": "\u6db2\u4f53_\u6c34_01",     "category": "liquid",        "tags": ["\u6db2\u4f53", "\u6c34", "\u900f\u660e"],      "node_type": "aiStandardSurface", "color": "#85C1E9"},
    {"id": "Foliage_Leaf_01",     "name": "Foliage_Leaf_01",     "name_cn": "\u690d\u88ab_\u53f6\u5b50_01",   "category": "foliage",       "tags": ["\u690d\u88ab", "\u53f6\u5b50", "\u690d\u7269"],       "node_type": "aiStandardSurface", "color": "#2ECC71"},
]

MOCK_PRESETS = [
    {"id": "preset_chrome_default", "name": "\u94ec\u9ed8\u8ba4",     "node_type": "aiStandardSurface", "color": "#C0C0C0", "is_default": True},
    {"id": "preset_gold_polished",  "name": "\u91d1\u5c5e\u629b\u5149",   "node_type": "aiStandardSurface", "color": "#FFD700", "is_default": False},
    {"id": "preset_glass_clear",    "name": "\u900f\u660e\u73bb\u7483",   "node_type": "aiStandardSurface", "color": "#1ABC9C", "is_default": True},
    {"id": "preset_rubber_matte",   "name": "\u6a61\u80f6\u54d1\u5149",   "node_type": "aiStandardSurface", "color": "#333333", "is_default": False},
    {"id": "preset_skin_sss",       "name": "SSS\u76ae\u80a4",        "node_type": "aiStandardSurface", "color": "#FADBD8", "is_default": True},
    {"id": "preset_wood_varnish",   "name": "\u6e05\u6f06\u6728\u6750",   "node_type": "aiStandardSurface", "color": "#8B4513", "is_default": False},
    {"id": "preset_water_ocean",    "name": "\u6d77\u6d0b\u6c34",     "node_type": "aiStandardSurface", "color": "#1a6090", "is_default": False},
    {"id": "preset_leaves",         "name": "\u690d\u7269\u53f6\u7247",   "node_type": "aiStandardSurface", "color": "#2ECC71", "is_default": True},
]
