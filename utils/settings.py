# -*- coding: utf-8 -*-
"""
SettingsManager — 应用设置持久化管理

存储路径: ~/.squirrel_asset_manager/app_settings.json
每次 set() 立即写入磁盘，避免退出前状态丢失。
"""

import os
import json
import re
import tempfile
import time


class SettingsManager:
    """应用设置管理器"""

    DEFAULT_SETTINGS = {
        "theme": "dark",
        "font_size": 13,
        "thumb_size": 180,
        "default_view": "icon",
        "language": "zh",
        "last_library_path": "",
        "model_library_path": "",
        "light_path": "",
        "texture_library_path": "",
        "scene_path": "",
        "hdr_path": "",
        "last_export_path": "",
        "window_state": {"width": 1400, "height": 900},
        # AI 服务配置（provider: ollama / deepseek / qwen / zhipu）
        "ai_provider": "ollama",
        "ai_ollama_host": "http://localhost:11434",
        "ai_api_key": "",       # 旧版共享 Key（兼容读取；新逻辑按供应商存 ai_api_keys）
        "ai_api_keys": {},      # 各供应商独立 API Key: {provider: key}
        "ai_base_url": "",
        "ai_model": "",
    }

    def __init__(self):
        self._dir = os.path.join(os.path.expanduser("~"), ".squirrel_asset_manager")
        self._path = os.path.join(self._dir, "app_settings.json")
        self._settings = dict(self.DEFAULT_SETTINGS)
        self._had_load_error = False  # 上次 load() 是否因文件损坏而失败（供调用方避免覆盖用户配置）

    def load(self) -> dict:
        """加载设置，缺失键回退到默认值"""
        try:
            if os.path.isfile(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # 合并：用默认值填补缺失键
                merged = dict(self.DEFAULT_SETTINGS)
                merged.update(loaded)
                self._settings = merged
                return merged
        except (json.JSONDecodeError, OSError) as e:
            print(f"[SettingsManager] 加载设置失败: {e}，使用默认值")
            self._had_load_error = True
            # 损坏文件先备份，避免后续 set() 覆盖导致用户配置（如 library_paths）永久丢失
            try:
                if os.path.isfile(self._path):
                    bak = self._path + ".bak"
                    if os.path.exists(bak):
                        bak = self._path + ".corrupt_" + str(int(time.time()))
                    os.replace(self._path, bak)
                    print(f"[SettingsManager] 损坏的设置文件已备份到: {bak}")
            except OSError as e2:
                print(f"[SettingsManager] 备份损坏设置文件失败: {e2}")

        self._settings = dict(self.DEFAULT_SETTINGS)
        return self._settings

    def save(self, settings: dict = None):
        """保存当前设置到磁盘"""
        if settings is not None:
            self._settings.update(settings)
        self._write_file()

    def get(self, key: str, default=None):
        """读取单个设置"""
        return self._settings.get(key, default)

    def set(self, key: str, value):
        """更新单个设置并立即写入磁盘"""
        self._settings[key] = value
        self._write_file()

    def _write_file(self):
        """内部写盘（原子写入：先写临时文件再替换，避免崩溃/多进程竞争损坏 JSON）"""
        try:
            os.makedirs(self._dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._settings, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            print(f"[SettingsManager] 保存设置失败: {e}")

    @property
    def settings(self) -> dict:
        return dict(self._settings)


def get_ai_api_key(settings: dict, provider: str) -> str:
    """读取指定供应商的 API Key。

    已建立按供应商存储（ai_api_keys 非空）时：只取当前供应商的 Key，其他供应商返回空，
    避免串用别的服务商的 Key；
    尚未迁移（ai_api_keys 为空/缺失）时：回退旧版共享 ai_api_key。
    """
    keys = settings.get("ai_api_keys")
    if isinstance(keys, dict) and keys:
        return keys.get(provider, "")
    return settings.get("ai_api_key", "")


def set_ai_api_key(settings: dict, provider: str, key: str) -> dict:
    """把 API Key 存入 ai_api_keys[provider]（同时同步旧版 ai_api_key 便于兼容读取），返回新设置副本"""
    out = dict(settings)
    keys = dict(out.get("ai_api_keys") or {})
    keys[provider] = key
    out["ai_api_keys"] = keys
    out["ai_api_key"] = key
    return out


def apply_font_size_to_widget(widget, font_size):
    """
    将字体大小应用到指定widget及其所有子控件
    
    Args:
        widget: QWidget 对象
        font_size: 字体大小（整数）
    """
    from ..utils.maya_utils import get_qt_modules
    QtWidgets, _, QtGui, _, _ = get_qt_modules()
    
    font = QtGui.QFont()
    font.setPointSize(font_size)
    for w in widget.findChildren(QtWidgets.QWidget):
        obj_name = w.objectName()
        if obj_name == "toolbar" or hasattr(w, '_search_bar'):
            continue
        if isinstance(w, QtWidgets.QPushButton) and w.parent() and w.parent().objectName() == "toolbar":
            continue
        if isinstance(w, QtWidgets.QLineEdit) and w.parent() and hasattr(w.parent(), '_search_input'):
            continue
        w.setFont(font)
        ss = w.styleSheet()
        if ss:
            ss = re.sub(r'font-size:\s*\d+px', f'font-size: {font_size}px', ss)
            w.setStyleSheet(ss)
    
    widget.setFont(font)
    ss = widget.styleSheet()
    if ss:
        ss = re.sub(r'font-size:\s*\d+px', f'font-size: {font_size}px', ss)
        widget.setStyleSheet(ss)


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
    print("SettingsManager 自测")
    print("=" * 50)

    # 模拟独立路径
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_settings_path = os.path.join(tmpdir, "app_settings.json")

        class _TestSettingsManager(SettingsManager):
            def __init__(self):
                self._dir = tmpdir
                self._path = tmp_settings_path
                self._settings = dict(self.DEFAULT_SETTINGS)

        sm = _TestSettingsManager()

        # 首次加载（无文件）
        s = sm.load()
        check(s["theme"] == "dark", "首次加载使用默认主题")
        check(s["font_size"] == 13, "首次加载使用默认字号")

        # 写入
        sm.set("theme", "midnight")
        sm.set("font_size", 15)
        check(os.path.isfile(tmp_settings_path), "设置文件已创建")

        # 重新加载
        sm2 = _TestSettingsManager()
        s2 = sm2.load()
        check(s2["theme"] == "midnight", "重载后主题保持")
        check(s2["font_size"] == 15, "重载后字号保持")

        # 缺失键回退
        check(s2.get("unknown_key", "fallback") == "fallback", "缺失键回退默认值")

    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" ✅ 全部通过")
    print("=" * 50)
