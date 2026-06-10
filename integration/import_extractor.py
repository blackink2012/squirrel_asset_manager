"""从 .zasset 文件夹中获取指定格式文件的路径"""

import os
from typing import Optional


class ImportExtractor:
    """从 .zasset 文件夹资产中定位文件路径供 Maya 导入。

    .zasset 为文件夹格式时，文件直接存在于磁盘上，
    无需提取，直接返回源路径即可。

    用法:
        with ImportExtractor(zasset_path, "fbx") as ext:
            fbx_path = ext.extracted_path  # 文件夹内的 node.fbx 路径
            # cmds.file(fbx_path, i=True)
    """

    FORMAT_MAP = {
        "zmetal":  "node.zmetal",
        "mcm":     "node.mcm",
        "ma":      "node.ma",
        "mb":      "node.mb",
        "fbx":     "node.fbx",
        "obj":     "node.obj",
        "abc":     "node.abc",
        "usd":     "node.usd",
        "usda":    "node.usda",
        "usdc":    "node.usdc",
        "glb":     "node.glb",
        "gltf":    "node.gltf",
        "dae":     "node.dae",
        "ass":     "node.ass",
        "rs":      "node.rs",
        "proxy":   "node.proxy",
        "vrmesh":  "node.vrmesh",
        "vdb":     "node.vdb",
        "sicon":   "thumb.sicon",
        "aicon":   "thumb.aicon",
    }

    def __init__(self, zasset_path: str, format_name: str):
        self.zasset_path = zasset_path
        self.format_name = format_name.lower().lstrip(".")
        self._extracted_path: Optional[str] = None

    @property
    def extracted_path(self) -> Optional[str]:
        return self._extracted_path

    def __enter__(self):
        self.extract()
        return self

    def __exit__(self, *args):
        self.cleanup()

    def extract(self) -> Optional[str]:
        """定位 .zasset 文件夹内的目标文件路径。

        Returns:
            文件完整路径，失败返回 None
        """
        if not os.path.isdir(self.zasset_path):
            print(f"[ImportExtractor] .zasset 不存在: {self.zasset_path}")
            return None

        internal_path = self.FORMAT_MAP.get(self.format_name)
        if internal_path is None:
            internal_path = self.format_name

        candidate = os.path.join(self.zasset_path, internal_path)
        if os.path.isfile(candidate):
            self._extracted_path = candidate
            print(f"[ImportExtractor] 定位: {candidate}")
            return candidate

        dot_fmt = f".{self.format_name}"
        try:
            for fname in os.listdir(self.zasset_path):
                if not fname.startswith("textures") and not fname.startswith("."):
                    fpath = os.path.join(self.zasset_path, fname)
                    if os.path.isfile(fpath) and fname.lower().endswith(dot_fmt):
                        self._extracted_path = fpath
                        print(f"[ImportExtractor] 定位 (扩展名匹配): {fpath}")
                        return fpath
        except OSError:
            pass

        tex_dir = os.path.join(self.zasset_path, "textures")
        if os.path.isdir(tex_dir):
            try:
                # 递归搜索 textures/ 子目录（处理精度子文件夹如 2K/、4K/）
                for root, dirs, files in os.walk(tex_dir):
                    for fname in files:
                        if fname.lower().endswith(dot_fmt):
                            fpath = os.path.join(root, fname)
                            self._extracted_path = fpath
                            print(f"[ImportExtractor] 定位 (贴图): {fpath}")
                            return fpath
                # 兜底：返回第一张贴图
                for root, dirs, files in os.walk(tex_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        if os.path.isfile(fpath):
                            self._extracted_path = fpath
                            print(f"[ImportExtractor] 定位 (贴图兜底): {fpath}")
                            return fpath
            except OSError:
                pass

        print(f"[ImportExtractor] 在 {self.zasset_path} 中未找到 .{self.format_name} 文件")
        return None

    def cleanup(self):
        """文件夹格式无需清理临时文件。"""
        pass
