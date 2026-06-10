# -*- coding: utf-8 -*-
"""
zasset_io — .zasset 文件夹资产读写模块

格式说明：
  .zasset 是一个文件夹，内部直接存放资产文件。

  MyMat.zasset/
  ├── meta.json          # 元数据
  ├── node.zmetal        # 材质节点数据
  ├── node.mcm           # 对象映射
  ├── thumb.sicon        # 静态缩略图
  ├── thumb.aicon        # 动图缩略图
  ├── thumb.mp4          # 视频缩略图
  ├── node.ma/fbx/obj    # 几何体 (可选)
  └── textures/          # 贴图目录
      ├── diff.png
      └── norm.exr

核心策略：文件夹即资产，无需打包/解包，文件系统即数据库。
  所有读写操作直接对应 os 文件操作，零拷贝。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Dict, List, Optional


class ZassetIO:
    """.zasset 文件夹资产的读写工具。

    所有方法以路径为输入，.zasset 是文件夹。
    """

    META_PATH = "meta.json"
    NODE_PATH = "node.zmetal"
    THUMB_PATH = "thumb.sicon"
    THUMB_MP4_PATH = "thumb.mp4"
    THUMB_DISPLAY_PATHS = ["thumb.aicon", "thumb.sicon"]
    MCM_PATH = "node.mcm"
    VARIANTS_PATH = "variants.json"
    VARIANTS_DIR = "variants"
    TEXTURES_PREFIX = "textures/"
    GEOMETRY_PREFIX = "node."

    @classmethod
    def _exists(cls, zasset_path: str) -> bool:
        """判断 .zasset 文件夹是否存在。"""
        return os.path.isdir(zasset_path)

    @classmethod
    def _ensure_dir(cls, zasset_path: str):
        """确保 .zasset 文件夹存在。"""
        os.makedirs(zasset_path, exist_ok=True)

    @classmethod
    def _full_path(cls, zasset_path: str, internal_path: str) -> str:
        return os.path.join(zasset_path, internal_path)

    @classmethod
    def _is_geometry(cls, name: str) -> bool:
        return name.startswith(cls.GEOMETRY_PREFIX)

    @classmethod
    def _is_texture(cls, name: str) -> bool:
        return name.startswith(cls.TEXTURES_PREFIX)

    @classmethod
    def read_meta(cls, zasset_path: str) -> dict:
        """读取 meta.json。

        Returns:
            dict: meta.json 内容，失败返回空 dict
        """
        if not cls._exists(zasset_path):
            return {}

        try:
            meta_path = cls._full_path(zasset_path, cls.META_PATH)
            if not os.path.isfile(meta_path):
                return {}
            with open(meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    @classmethod
    def read_node(cls, zasset_path: str) -> Optional[bytes]:
        """读取 node.zmetal。"""
        if not cls._exists(zasset_path):
            return None

        try:
            node_path = cls._full_path(zasset_path, cls.NODE_PATH)
            if not os.path.isfile(node_path):
                return None
            with open(node_path, 'rb') as f:
                return f.read()
        except OSError:
            return None

    @classmethod
    def read_thumbnail(cls, zasset_path: str) -> Optional[bytes]:
        """读取缩略图（优先 .aicon GIF 首帧，跳过 .mp4）"""
        if not cls._exists(zasset_path):
            return None

        try:
            for thumb_name in cls.THUMB_DISPLAY_PATHS:
                thumb_path = cls._full_path(zasset_path, thumb_name)
                if os.path.isfile(thumb_path):
                    with open(thumb_path, 'rb') as f:
                        return f.read()
            return None
        except OSError:
            return None

    @classmethod
    def read_mp4_to_temp(cls, zasset_path: str) -> Optional[str]:
        """提取 .mp4 动图到临时文件，返回临时文件路径"""
        if not cls._exists(zasset_path):
            return None

        mp4_path = cls._full_path(zasset_path, cls.THUMB_MP4_PATH)
        if not os.path.isfile(mp4_path):
            return None

        try:
            with open(mp4_path, 'rb') as f:
                data = f.read()
            fd, tmp = tempfile.mkstemp(suffix='.mp4', prefix='thumb_')
            os.write(fd, data)
            os.close(fd)
            return tmp
        except Exception:
            return None

    @classmethod
    def read_textures(cls, zasset_path: str) -> Dict[str, bytes]:
        """读取 textures/ 目录下所有文件。

        Returns:
            dict: 文件名 → 字节内容
        """
        if not cls._exists(zasset_path):
            return {}

        tex_dir = cls._full_path(zasset_path, cls.TEXTURES_PREFIX.rstrip('/'))
        if not os.path.isdir(tex_dir):
            return {}

        result = {}
        try:
            for fname in os.listdir(tex_dir):
                fpath = os.path.join(tex_dir, fname)
                if os.path.isfile(fpath):
                    with open(fpath, 'rb') as f:
                        result[fname] = f.read()
        except OSError:
            pass
        return result

    @classmethod
    def list_contents(cls, zasset_path: str) -> List[str]:
        """列出所有内部文件路径（相对于 .zasset 根目录）。"""
        if not cls._exists(zasset_path):
            return []

        result = []
        try:
            for root, dirs, filenames in os.walk(zasset_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for fname in filenames:
                    if fname.startswith('.'):
                        continue
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, zasset_path).replace("\\", "/")
                    result.append(rel)
        except OSError:
            pass
        return sorted(result)

    @classmethod
    def read_file(cls, zasset_path: str, internal_path: str) -> Optional[bytes]:
        """读取任意内部文件的原始字节。

        Args:
            zasset_path: .zasset 文件夹路径
            internal_path: 内部路径，如 "node.zmetal", "textures/diff.png"

        Returns:
            bytes 或 None
        """
        if not cls._exists(zasset_path):
            return None

        try:
            fpath = cls._full_path(zasset_path, internal_path)
            if not os.path.isfile(fpath):
                return None
            with open(fpath, 'rb') as f:
                return f.read()
        except OSError:
            return None

    @classmethod
    def update_file_in_zasset(cls, zasset_path: str, internal_path: str, data: bytes) -> bool:
        """替换或新增内部文件（原子写入：临时文件 → os.replace）。"""
        if not cls._exists(zasset_path):
            return False

        try:
            fpath = cls._full_path(zasset_path, internal_path)
            cls._ensure_dir(os.path.dirname(fpath))
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(fpath), prefix=".tmp_")
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(tmp, fpath)
            return True
        except OSError as e:
            print(f"[ZassetIO] update_file_in_zasset 失败 {zasset_path}/{internal_path}: {e}")
            return False

    @classmethod
    def write_meta(cls, zasset_path: str, data: dict) -> bool:
        """更新 meta.json（原子写入）。"""
        if not cls._exists(zasset_path):
            return False

        try:
            meta_path = cls._full_path(zasset_path, cls.META_PATH)
            meta_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(meta_path), prefix=".tmp_")
            try:
                os.write(fd, meta_bytes)
            finally:
                os.close(fd)
            os.replace(tmp, meta_path)
            return True
        except OSError as e:
            print(f"[ZassetIO] write_meta 失败 {zasset_path}: {e}")
            return False

    @classmethod
    def write_node(cls, zasset_path: str, node_bytes: bytes) -> bool:
        """更新 node.zmetal。"""
        return cls.update_file_in_zasset(zasset_path, cls.NODE_PATH, node_bytes)

    @classmethod
    def write_thumbnail(cls, zasset_path: str, thumb_bytes: bytes) -> bool:
        """写入新缩略图，直接覆盖 thumb.sicon。先删除非 sicon 的旧缩略图。"""
        if not cls._exists(zasset_path):
            return False
        try:
            for old_thumb in ("thumb.aicon", "thumb.mp4", "thumb.png"):
                old_path = cls._full_path(zasset_path, old_thumb)
                if os.path.isfile(old_path):
                    os.remove(old_path)
        except OSError:
            pass
        fpath = cls._full_path(zasset_path, cls.THUMB_PATH)
        try:
            with open(fpath, 'wb') as f:
                f.write(thumb_bytes)
            return True
        except OSError as e:
            print(f"[ZassetIO] write_thumbnail 失败 {zasset_path}: {e}")
            return False

    @classmethod
    def write_texture(cls, zasset_path: str, tex_name: str, data: bytes) -> bool:
        """写入/更新一个贴图文件。"""
        return cls.update_file_in_zasset(zasset_path, f"{cls.TEXTURES_PREFIX}{tex_name}", data)

    @classmethod
    def delete_texture(cls, zasset_path: str, tex_name: str) -> bool:
        """删除一个贴图文件。"""
        if not cls._exists(zasset_path):
            return False

        try:
            fpath = cls._full_path(zasset_path, f"{cls.TEXTURES_PREFIX}{tex_name}")
            if os.path.isfile(fpath):
                os.remove(fpath)
            return True
        except OSError as e:
            print(f"[ZassetIO] delete_texture 失败 {zasset_path}/{tex_name}: {e}")
            return False

    @classmethod
    def copy_with_meta_update(cls, src_path: str, dst_path: str, new_meta: dict) -> bool:
        """复制 .zasset 文件夹并更新 meta.json。

        Args:
            src_path: 源 .zasset 文件夹路径
            dst_path: 目标 .zasset 文件夹路径
            new_meta: 新的 meta dict

        Returns:
            bool: 成功返回 True
        """
        if not cls._exists(src_path):
            return False
        try:
            if os.path.isdir(dst_path):
                shutil.rmtree(dst_path)
            shutil.copytree(src_path, dst_path)
            return cls.write_meta(dst_path, new_meta)
        except Exception as e:
            print(f"[ZassetIO] copy_with_meta_update 失败 {src_path} → {dst_path}: {e}")
            if os.path.isdir(dst_path):
                try:
                    shutil.rmtree(dst_path, ignore_errors=True)
                except OSError:
                    pass
            return False

    @classmethod
    def read_variants(cls, zasset_path: str) -> dict:
        """读取 variants.json。

        Returns:
            dict: variants.json 内容。不存在或无变体时返回 {"versions": []}
        """
        if not cls._exists(zasset_path):
            return {"versions": []}
        try:
            vpath = cls._full_path(zasset_path, cls.VARIANTS_PATH)
            if not os.path.isfile(vpath):
                return {"versions": []}
            with open(vpath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"versions": []}

    @classmethod
    def write_variants(cls, zasset_path: str, variants_data: dict) -> bool:
        """写入 variants.json（原子写入）。"""
        if not cls._exists(zasset_path):
            return False
        try:
            vpath = cls._full_path(zasset_path, cls.VARIANTS_PATH)
            vbytes = json.dumps(variants_data, indent=2, ensure_ascii=False).encode('utf-8')
            fd, tmp = tempfile.mkstemp(dir=zasset_path, prefix=".tmp_variants_")
            try:
                os.write(fd, vbytes)
            finally:
                os.close(fd)
            os.replace(tmp, vpath)
            return True
        except OSError as e:
            print(f"[ZassetIO] write_variants 失败 {zasset_path}: {e}")
            return False

    @classmethod
    def resolve_geometry(cls, zasset_path: str, version: str = None, lod: str = None) -> Optional[str]:
        """解析最终几何体路径。

        1. 无 variants.json 或无变体 → 返回根目录 node.ma（如果存在）
        2. 有变体 → 按 default_version/default_lod 锁定版本和LOD，
           再用指定 version/lod 覆盖 → 返回 variants.json 中 geometry 字段值。

        Args:
            zasset_path: .zasset 文件夹路径
            version: 指定版本 id，为空用 default_version
            lod: 指定 LOD id，为空用 default_lod

        Returns:
            str 或 None: 几何体相对路径（如 "variants/v1/lod0/node.ma"），不存在返回 None
        """
        variants = cls.read_variants(zasset_path)
        versions = variants.get("versions", [])
        if not versions:
            # 无变体：返回根目录默认几何体
            candidates = ["node.ma", "node.fbx", "node.obj"]
            for c in candidates:
                fpath = cls._full_path(zasset_path, c)
                if os.path.isfile(fpath):
                    return c
            return None

        # 解析版本
        target_version = version or variants.get("default_version") or versions[0]["id"]
        ver = None
        for v in versions:
            if v.get("id") == target_version:
                ver = v
                break
        if not ver:
            ver = versions[0]

        # 解析 LOD
        lods = ver.get("lods", [])
        if not lods:
            return None
        target_lod = lod or variants.get("default_lod") or lods[0]["id"]
        for l in lods:
            if l.get("id") == target_lod:
                geom = l.get("geometry")
                if geom:
                    fpath = cls._full_path(zasset_path, geom)
                    if os.path.isfile(fpath):
                        return geom
                    else:
                        # 指定 LOD 文件不存在，报错而非回退到其他 LOD
                        print(f"[ZassetIO] LOD {target_lod} 几何体文件不存在: {fpath}")
                        return None
        # 未找到匹配的 LOD：如果指定了 lod 参数，不自动回退
        if not lod:
            for l in lods:
                geom = l.get("geometry")
                if geom:
                    fpath = cls._full_path(zasset_path, geom)
                    if os.path.isfile(fpath):
                        return geom
            return None
        print(f"[ZassetIO] 未找到 LOD: {target_lod} (version={target_version})")
        return None

    @classmethod
    def resolve_material(cls, zasset_path: str, version: str = None) -> Optional[str]:
        """解析变体材质路径。优先级：
        1. variants/{version}/node.zmetal 存在 → 返回该路径
        2. 根目录 node.zmetal 存在 → 返回该路径
        3. 无 → None

        Returns:
            str 或 None: 材质文件相对路径
        """
        if not cls._exists(zasset_path):
            return None

        # 带版本：优先检查变体目录
        if version:
            candidates = [
                f"variants/{version}/node.zmetal",
            ]
            for c in candidates:
                fpath = cls._full_path(zasset_path, c)
                if os.path.isfile(fpath):
                    return c

        # 回退到根目录
        root = cls._full_path(zasset_path, cls.NODE_PATH)
        if os.path.isfile(root):
            return cls.NODE_PATH
        return None

    @classmethod
    def resolve_textures_dir(cls, zasset_path: str, version: str = None) -> Optional[str]:
        """解析变体贴图目录。优先级同 resolve_material。

        Returns:
            str 或 None: 贴图目录相对路径（如 "textures/" 或 "variants/v2/textures/"）
        """
        if not cls._exists(zasset_path):
            return None

        if version:
            candidates = [
                f"variants/{version}/textures",
            ]
            for c in candidates:
                dpath = cls._full_path(zasset_path, c)
                if os.path.isdir(dpath) and os.listdir(dpath):
                    return c + "/"

        # 回退到根目录
        root_tex = cls._full_path(zasset_path, cls.TEXTURES_PREFIX.rstrip('/'))
        if os.path.isdir(root_tex) and os.listdir(root_tex):
            return cls.TEXTURES_PREFIX
        return None

    @classmethod
    def update_meta_inplace(cls, zasset_path: str, new_meta: dict) -> bool:
        """原地替换 meta.json（与 write_meta 行为相同）。"""
        return cls.write_meta(zasset_path, new_meta)


# ═══════════════════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import tempfile

    print("=" * 60)
    print("ZassetIO 自测 (文件夹)")
    print("=" * 60)

    passed = 0
    failed = 0

    def check(condition: bool, label: str):
        global passed, failed
        if condition:
            print(f"  ✓ {label}")
            passed += 1
        else:
            print(f"  ✗ {label}")
            failed += 1

    print("\n[基础读写]")
    with tempfile.TemporaryDirectory() as tmpdir:
        zasset_path = os.path.join(tmpdir, "TestMat.zasset")
        ZassetIO._ensure_dir(zasset_path)

        with open(os.path.join(zasset_path, "meta.json"), 'w', encoding='utf-8') as f:
            json.dump({"id": "test-id", "name": "TestMat", "category": "metal"}, f)
        with open(os.path.join(zasset_path, "node.zmetal"), 'wb') as f:
            f.write(b'{"nodes":[]}')
        with open(os.path.join(zasset_path, "thumb.sicon"), 'wb') as f:
            f.write(b"fake-thumb-data")

        tex_dir = os.path.join(zasset_path, "textures")
        os.makedirs(tex_dir)
        with open(os.path.join(tex_dir, "diff.tex"), 'wb') as f:
            f.write(b"diff-data")
        with open(os.path.join(tex_dir, "norm.tex"), 'wb') as f:
            f.write(b"norm-data")
        with open(os.path.join(zasset_path, "node.ma"), 'wb') as f:
            f.write(b"maya-ascii-data")

        check(os.path.isdir(zasset_path), ".zasset 文件夹存在")

        meta = ZassetIO.read_meta(zasset_path)
        check(meta.get("name") == "TestMat", "read_meta 正确")

        node = ZassetIO.read_node(zasset_path)
        check(node == b'{"nodes":[]}', "read_node 正确")

        thumb = ZassetIO.read_thumbnail(zasset_path)
        check(thumb == b"fake-thumb-data", "read_thumbnail 正确")

        tex = ZassetIO.read_textures(zasset_path)
        check(len(tex) == 2, f"read_textures 2 个文件 (实际 {len(tex)})")
        check(tex.get("diff.tex") == b"diff-data", "read_textures diff 正确")
        check(tex.get("norm.tex") == b"norm-data", "read_textures norm 正确")

        files = ZassetIO.list_contents(zasset_path)
        check(len(files) == 6, f"list_contents 返回 {len(files)} 个文件")

        ZassetIO.write_meta(zasset_path, {"id": "new-id", "name": "NewName"})
        meta2 = ZassetIO.read_meta(zasset_path)
        check(meta2.get("name") == "NewName", "write_meta 更新成功")

        ZassetIO.write_node(zasset_path, b'{"nodes":[1,2,3]}')
        node2 = ZassetIO.read_node(zasset_path)
        check(node2 == b'{"nodes":[1,2,3]}', "write_node 更新成功")

        ZassetIO.write_texture(zasset_path, "new.tex", b"new-tex-data")
        tex2 = ZassetIO.read_textures(zasset_path)
        check("new.tex" in tex2, "write_texture 新增文件")
        check(tex2["new.tex"] == b"new-tex-data", "write_texture 内容正确")

        ZassetIO.delete_texture(zasset_path, "diff.tex")
        tex3 = ZassetIO.read_textures(zasset_path)
        check("diff.tex" not in tex3, "delete_texture 删除成功")
        check(len(tex3) == 2, "删除后贴图数量为 2")

        ZassetIO.update_file_in_zasset(zasset_path, "thumb.aicon", b"aicon-data")
        thumb2 = ZassetIO.read_thumbnail(zasset_path)
        check(thumb2 == b"aicon-data", "update_file 然后 read_thumbnail 优先 aicon")

        ZassetIO.update_meta_inplace(zasset_path, {"id": "inplace-id", "name": "InPlace"})
        meta3 = ZassetIO.read_meta(zasset_path)
        check(meta3.get("name") == "InPlace", "update_meta_inplace 成功")

    print("\n[边界情况]")
    meta = ZassetIO.read_meta("/nonexistent/path.zasset")
    check(meta == {}, "read_meta 不存在的文件返回空 dict")

    node = ZassetIO.read_node("/nonexistent/path.zasset")
    check(node is None, "read_node 不存在的文件返回 None")

    tex = ZassetIO.read_textures("/nonexistent/path.zasset")
    check(tex == {}, "read_textures 不存在的文件返回空 dict")

    files = ZassetIO.list_contents("/nonexistent/path.zasset")
    check(files == [], "list_contents 不存在的文件返回空列表")

    # copy_with_meta_update
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "src.zasset")
        dst_path = os.path.join(tmpdir, "dst.zasset")

        ZassetIO._ensure_dir(src_path)
        with open(os.path.join(src_path, "meta.json"), 'w', encoding='utf-8') as f:
            json.dump({"name": "Original"}, f)
        with open(os.path.join(src_path, "node.zmetal"), 'wb') as f:
            f.write(b"x")

        ok = ZassetIO.copy_with_meta_update(src_path, dst_path, {"name": "Copied"})
        check(ok, "copy_with_meta_update 成功")
        check(os.path.isdir(dst_path), "目标文件夹存在")
        meta = ZassetIO.read_meta(dst_path)
        check(meta.get("name") == "Copied", "目标 meta 已更新")
        node = ZassetIO.read_node(dst_path)
        check(node == b"x", "源 node 保留")

    # read_mp4_to_temp
    with tempfile.TemporaryDirectory() as tmpdir:
        zasset_path = os.path.join(tmpdir, "mp4test.zasset")
        ZassetIO._ensure_dir(zasset_path)
        with open(os.path.join(zasset_path, "thumb.mp4"), 'wb') as f:
            f.write(b"mp4-data")

        tmp = ZassetIO.read_mp4_to_temp(zasset_path)
        check(tmp is not None and os.path.isfile(tmp), "read_mp4_to_temp 返回有效文件")
        if tmp and os.path.isfile(tmp):
            os.remove(tmp)

    print(f"\n{'=' * 60}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败 ❌")
        sys.exit(1)
    else:
        print(" ✅ 全部通过")
