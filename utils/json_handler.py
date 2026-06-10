# -*- coding: utf-8 -*-
"""
JSONHandler — JSON 文件读写抽象层

提供安全的 JSON 文件读写、自动备份轮转、目录扫描等功能。
所有方法为静态方法，无状态，可在 Maya 和独立 Python 环境中使用。

用法:
    from squirrel_asset_manager.utils.json_handler import JSONHandler

    data = JSONHandler.read_json("/path/to/file.json")
    JSONHandler.safe_write_json("/path/to/file.json", data)
    files = JSONHandler.list_directory("/some/dir", "*.json")
"""

import json
import os
import glob
import shutil
import traceback


class JSONHandler:
    """JSON 文件读写工具类（全部静态方法）"""

    # ── 基础读写 ──────────────────────────────────────────

    @staticmethod
    def read_json(path: str) -> dict:
        """
        读取 JSON 文件，自动检测编码。

        编码检测顺序: UTF-8 → UTF-8-BOM → GBK (fallback)
        JSON 解析失败时返回空 dict 而非抛异常。

        Args:
            path: JSON 文件路径

        Returns:
            dict: 解析后的数据，读取失败返回 {}
        """
        if not os.path.isfile(path):
            print(f"[JSONHandler] 文件不存在: {path}")
            return {}

        # 尝试多种编码
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(path, "r", encoding=encoding) as f:
                    content = f.read()
                return json.loads(content)
            except (UnicodeDecodeError, UnicodeError):
                continue
            except json.JSONDecodeError as e:
                print(f"[JSONHandler] JSON 解析失败 ({encoding}): {path}")
                print(f"  → {e}")
                return {}

        # 所有编码都失败
        print(f"[JSONHandler] 无法解码文件: {path}")
        return {}

    @staticmethod
    def write_json(path: str, data: dict, indent: int = 2,
                   ensure_ascii: bool = False) -> bool:
        """
        写入 JSON 文件。

        Args:
            path: 目标文件路径
            data: 要写入的数据
            indent: 缩进空格数（默认 2）
            ensure_ascii: 是否转义非 ASCII 字符（默认 False，保留中文）

        Returns:
            bool: 成功返回 True
        """
        try:
            # 确保父目录存在
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            return True
        except (OSError, IOError, TypeError) as e:
            print(f"[JSONHandler] 写入失败: {path}")
            traceback.print_exc()
            return False

    # ── 安全写入 + 备份 ───────────────────────────────────

    @staticmethod
    def safe_write_json(path: str, data: dict, max_backups: int = 3) -> bool:
        """
        安全写入 JSON 文件（备份 → 临时文件 → 原子替换）。

        流程:
          1. 如原文件存在 → 轮转备份 (.bak, .bak.1, .bak.2, .bak.3)
          2. 写入临时文件 {path}.tmp
          3. os.replace(tmp, target) 原子替换
          4. 写入失败时从备份恢复

        Args:
            path: 目标文件路径
            data: 要写入的数据
            max_backups: 最大备份数量（默认 3）

        Returns:
            bool: 成功返回 True
        """
        tmp_path = path + ".tmp"
        backup_created = False

        try:
            # 1. 备份原文件
            if os.path.isfile(path):
                try:
                    JSONHandler.rotate_backups(path, max_backups)
                    backup_created = True
                except OSError:
                    # 备份失败不阻塞写入（如磁盘满）
                    print(f"[JSONHandler] 备份失败，跳过: {path}")

            # 2. 写入临时文件
            JSONHandler.write_json(tmp_path, data)

            # 3. 原子替换
            if os.path.exists(path):
                os.remove(path)
            os.replace(tmp_path, path)
            return True

        except Exception as e:
            print(f"[JSONHandler] 安全写入失败: {path}")
            traceback.print_exc()

            # 清理临时文件
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

            # 如果备份成功，尝试恢复
            if backup_created:
                bak_path = path + ".bak"
                if os.path.isfile(bak_path):
                    try:
                        shutil.copy2(bak_path, path)
                        print(f"[JSONHandler] 已从备份恢复: {path}")
                    except OSError:
                        pass

            return False

    @staticmethod
    def rotate_backups(path: str, max_backups: int = 3):
        """
        轮转备份文件。

        将现有备份文件依次后移一位:
          .bak.2 → .bak.3
          .bak.1 → .bak.2
          .bak   → .bak.1

        然后创建新的 .bak（拷贝原文件）。

        Args:
            path: 原文件路径
            max_backups: 最大备份层数（默认 3）
        """
        # 删除最旧的备份（如果存在）
        oldest = JSONHandler.get_backup_path(path, max_backups)
        if os.path.isfile(oldest):
            os.remove(oldest)

        # 从旧到新依次后移
        for version in range(max_backups - 1, 0, -1):
            older = JSONHandler.get_backup_path(path, version)
            newer = JSONHandler.get_backup_path(path, version + 1)
            if os.path.isfile(older):
                shutil.move(older, newer)

        # 创建最新的 .bak
        if os.path.isfile(path):
            shutil.copy2(path, path + ".bak")

    @staticmethod
    def get_backup_path(original_path: str, version: int = 1) -> str:
        """
        获取备份文件路径。

        Args:
            original_path: 原文件路径
            version: 备份版本号 (1 → .bak, 2 → .bak.1, ...)

        Returns:
            str: 备份文件路径

        Examples:
            get_backup_path("/a/b.json", 1) → "/a/b.json.bak"
            get_backup_path("/a/b.json", 2) → "/a/b.json.bak.1"
        """
        if version == 1:
            return original_path + ".bak"
        return original_path + ".bak." + str(version - 1)

    @staticmethod
    def restore_from_backup(path: str) -> bool:
        """
        从最新的 .bak 备份恢复原文件。

        Args:
            path: 要恢复的原文件路径

        Returns:
            bool: 恢复成功返回 True
        """
        bak_path = path + ".bak"
        if not os.path.isfile(bak_path):
            print(f"[JSONHandler] 备份不存在: {bak_path}")
            return False

        try:
            shutil.copy2(bak_path, path)
            print(f"[JSONHandler] 已从备份恢复: {path}")
            return True
        except OSError:
            traceback.print_exc()
            return False

    # ── 文件系统工具 ──────────────────────────────────────

    @staticmethod
    def list_directory(path: str, pattern: str = "*.json") -> list:
        """
        列出目录下所有匹配模式的文件（递归或非递归）。

        Args:
            path: 目录路径
            pattern: 通配符模式，如 "*.json" 或 "*.png"

        Returns:
            list[str]: 匹配文件的绝对路径列表
        """
        if not os.path.isdir(path):
            return []

        # 使用 glob 支持通配符
        search_pattern = os.path.join(path, pattern)
        files = glob.glob(search_pattern)
        return sorted(files)

    @staticmethod
    def list_directory_recursive(path: str, pattern: str = "*.json") -> list:
        """
        递归列出目录下所有匹配模式的文件或文件夹。

        Args:
            path: 目录路径
            pattern: 通配符模式

        Returns:
            list[str]: 匹配的绝对路径列表
        """
        if not os.path.isdir(path):
            return []

        from pathlib import Path
        items = []
        for p in Path(path).rglob(pattern):
            if p.is_file() or p.is_dir():
                items.append(str(p))
        return sorted(items)

    @staticmethod
    def ensure_directory(path: str):
        """
        确保目录存在，不存在则创建（幂等）。

        Args:
            path: 目录路径
        """
        if path and not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)


# ── 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

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
    print("JSONHandler 自测")
    print("=" * 50)

    # ── T1.5.1: 基础读写 ──
    print("\n[T1.5.1] 基础读写")

    # 测试读取 JEEP_B.json
    jeep_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "json示例", "JEEP_B.json"
    )
    data = JSONHandler.read_json(jeep_path)
    check(isinstance(data, dict) and len(data) > 0,
          f"读取 JEEP_B.json → {len(data)} 个顶层键")

    check("materials" in data or "material" in data or "nodes" in data,
          "JEEP_B.json 包含材质数据")

    # 测试读取不存在的文件
    empty = JSONHandler.read_json("/nonexistent/path/file.json")
    check(empty == {}, "不存在的文件 → 返回 {}")

    # 测试写入 + 回读
    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = os.path.join(tmpdir, "test_write.json")
        test_data = {"name": "测试", "value": 42, "tags": ["中文", "test"]}
        ok = JSONHandler.write_json(test_path, test_data)
        check(ok, "write_json 返回 True")

        read_back = JSONHandler.read_json(test_path)
        check(read_back == test_data, "写后回读数据一致")
        check(read_back["tags"] == ["中文", "test"], "中文内容正确保留")

    # ── T1.5.2: 安全写入 + 备份 ──
    print("\n[T1.5.2] 安全写入 + 备份轮转")

    with tempfile.TemporaryDirectory() as tmpdir:
        base = os.path.join(tmpdir, "safe_test.json")

        # 第一次写入（无原文件）
        JSONHandler.write_json(base, {"v": 1})
        ok = JSONHandler.safe_write_json(base, {"v": 2})
        check(ok, "safe_write_json 成功")
        check(JSONHandler.read_json(base) == {"v": 2}, "内容已更新")

        # 检查备份
        bak = base + ".bak"
        check(os.path.isfile(bak), "已生成 .bak 备份")
        bak_data = JSONHandler.read_json(bak)
        check(bak_data == {"v": 1}, ".bak 内容为上一版本")

        # 连续写入 5 次，测试轮转
        for i in range(3, 7):
            JSONHandler.safe_write_json(base, {"v": i})

        bak_files = [base + ".bak"] + [base + f".bak.{i}" for i in range(1, 4)]
        existing = [f for f in bak_files if os.path.isfile(f)]
        check(len(existing) >= 3, f"备份轮转正常，存在 {len(existing)} 个备份文件")

    # ── T1.5.3: 文件系统工具 ──
    print("\n[T1.5.3] 文件系统工具")

    with tempfile.TemporaryDirectory() as tmpdir:
        # ensure_directory
        test_dir = os.path.join(tmpdir, "a", "b", "c")
        JSONHandler.ensure_directory(test_dir)
        check(os.path.isdir(test_dir), "ensure_directory 递归创建目录")
        JSONHandler.ensure_directory(test_dir)  # 幂等
        check(os.path.isdir(test_dir), "ensure_directory 幂等")

        # list_directory
        for name in ["a.json", "b.json", "c.txt"]:
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write("{}")

        json_files = JSONHandler.list_directory(tmpdir, "*.json")
        check(len(json_files) == 2, f"list_directory *.json → {len(json_files)} 个")

        all_files = JSONHandler.list_directory(tmpdir, "*")
        check(len(all_files) == 4, f"list_directory * → {len(all_files)} 个")  # 含子目录

        # list_directory 不存在的路径
        check(JSONHandler.list_directory("/nonexistent") == [],
              "不存在的目录 → 返回 []")

    # ── 结果 ──
    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" ✅ 全部通过")
    print("=" * 50)
