# -*- coding: utf-8 -*-
"""
i18n — 极简翻译层

用法：
    from squirrel_asset_manager.utils.i18n import t, set_language, get_language

    btn.setText(t("btn.refresh"))                       # 静态文本
    btn.setText(t("msg.selected_n", n=count))            # 带占位符
    cmds.warning(t("warn.path_not_found", path=path))

设计原则：
- 不依赖 Qt，纯 Python，与 pyd 编译完全兼容。
- 翻译表为 JSON，键空间扁平（"btn.refresh"），不嵌套。
- 缺失键：返回 key 本身并打印 warning（便于发现遗漏）。
- 兜底语言：zh（中文为源语言，找不到 en 时回退 zh）。
"""

import json
import os
import sys

_LANG = "zh"                          # 当前语言，运行时由 set_language() 修改
_TABLES = {}                          # {lang: dict[str, str]}
_MTIMES = {}                          # {lang: float} 翻译文件 mtime，用于缓存失效检测
_TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations")
_SUPPORTED = ("zh", "en")
_FALLBACK = "zh"                      # 找不到目标语言或缺失键时的兜底


def _load_table(lang: str) -> dict:
    """按需加载某语言的翻译表，带缓存；翻译文件变更后自动重载。"""
    path = os.path.join(_TRANSLATIONS_DIR, f"{lang}.json")
    mtime = os.path.getmtime(path) if os.path.isfile(path) else None
    if lang in _TABLES and _MTIMES.get(lang) == mtime:
        return _TABLES[lang]
    table = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                table = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[i18n] 加载翻译表失败 {path}: {e}")
    _TABLES[lang] = table
    _MTIMES[lang] = mtime
    return table


def set_language(lang: str) -> None:
    """设置当前语言。未知语言回退到 zh。"""
    global _LANG
    _LANG = lang if lang in _SUPPORTED else _FALLBACK


def get_language() -> str:
    return _LANG


def supported_languages() -> tuple:
    return _SUPPORTED


def t(key: str, no_warn: bool = False, **kwargs) -> str:
    """按当前语言查表；找不到 key 回退到 fallback；再找不到返回 key 本身。

    支持 {name} 占位符：
        t("msg.selected_n", n=3) → "已选择 3 个" / "3 selected"

    Args:
        key: 翻译键。
        no_warn: 为 True 时，缺失键不打印警告（用于选项等混合键场景）。
        **kwargs: 占位符替换参数。
    """
    text = _lookup(_LANG, key)
    if text is None and _LANG != _FALLBACK:
        text = _lookup(_FALLBACK, key)
    if text is None:
        if not no_warn:
            print(f"[i18n] 缺失翻译键: {key}（lang={_LANG}）", file=sys.stderr)
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError) as e:
            print(f"[i18n] 占位符替换失败 key={key}: {e}", file=sys.stderr)
            return text
    return text


def _lookup(lang: str, key: str):
    table = _load_table(lang)
    return table.get(key)


def help_path(base_html: str) -> str:
    """根据当前语言返回帮助页路径。

    base_html 形如 ".../Assets/help/help_export.html"。
    若当前语言为 en 且存在同名 "_en.html" 文件（如 help_export_en.html），
    则返回英文版路径；否则返回中文版。找不到文件时回退中文版。
    """
    import os as _os
    if _LANG == "en" and base_html.endswith(".html"):
        en_path = base_html[:-5] + "_en.html"
        if _os.path.isfile(en_path):
            return en_path
    return base_html


# ── 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
    passed = 0
    failed = 0

    def check(condition, label):
        global passed, failed
        if condition:
            print(f"  [PASS] {label}")
            passed += 1
        else:
            print(f"  [FAIL] {label}")
            failed += 1

    print("=" * 50)
    print("i18n 自测")
    print("=" * 50)

    def test_lookup_found():
        set_language("zh")
        return t("btn.refresh") == "↻ 刷新"

    def test_lookup_fallback_to_zh():
        # en 语言下，某键在 en.json 缺失、但在 zh.json 存在 → 回退到 zh
        set_language("en")
        _load_table("en")  # 确保 en 表加载到 _TABLES 缓存
        _TABLES["en"].pop("label.thumb_size", None)  # 模拟 en 缺这个键 (仅 zh 有)
        return t("label.thumb_size") == "缩略图:"

    def test_missing_key_returns_key():
        set_language("zh")
        return t("__nonexistent__") == "__nonexistent__"

    def test_placeholder():
        set_language("en")
        return t("status.selected", count=3) == "  |  Selected: 3"

    def test_set_language_invalid_falls_back():
        set_language("fr")
        return get_language() == "zh"

    check(test_lookup_found(), "zh 查表命中 btn.refresh")
    check(test_lookup_fallback_to_zh(), "en 缺失键回退到 zh 值")
    check(test_missing_key_returns_key(), "缺失键返回 key 本身")
    check(test_placeholder(), "en 占位符替换 status.selected")
    check(test_set_language_invalid_falls_back(), "非法语言回退 zh")

    # 复位为默认
    set_language("zh")

    print(f"\n{'=' * 50}")
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" [ALL PASSED]")
    print("=" * 50)
