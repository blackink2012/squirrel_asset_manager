"""格式路由 — 根据格式名确定导入策略"""

# 几何体/交换格式（走 file -import）
GEOMETRY_FORMATS = {
    "ma", "mb", "fbx", "obj", "abc", "usd", "usda", "usdc",
    "glb", "gltf", "dae",
}

# 渲染代理格式
PROXY_FORMATS = {"ass", "rs", "proxy", "vrmesh", "vrscene"}

# 体积格式
VOLUME_FORMATS = {"vdb"}

# 贴图格式（创建 file node）
TEXTURE_FORMATS = {
    "png", "jpg", "jpeg", "exr", "hdr", "tga",
    "tiff", "tif", "bmp", "psd",
}

# 材质格式（解析 JSON 创建 shading network）
ZMETAL_FORMATS = {"zmetal"}

# HDR 格式（创建 aiSkyDomeLight）
HDR_FORMATS = {"hdr", "exr"}


def get_importer_type(format_name: str) -> str:
    """返回格式对应的导入器类型

    Returns:
        "geometry" | "proxy" | "volume" | "texture" | "zmetal" | "hdri" | "unknown"
    """
    f = format_name.lower().lstrip(".")
    if f in ZMETAL_FORMATS:
        return "zmetal"
    if f in GEOMETRY_FORMATS:
        return "geometry"
    if f in PROXY_FORMATS:
        return "proxy"
    if f in VOLUME_FORMATS:
        return "volume"
    if f in TEXTURE_FORMATS:
        return "texture" if f not in HDR_FORMATS else "hdri"
    if f in HDR_FORMATS:
        return "hdri"
    return "unknown"
