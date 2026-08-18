# -*- coding: utf-8 -*-
"""独立预览工具 — 不依赖 Maya GUI，加载资产库并浏览/预览资产

双击同目录的「启动独立预览.bat」即可运行。

运行环境（bat 自动探测）：
  - 独立 Python + PySide6（推荐）
  - Maya mayapy（自带 PySide，不启动 Maya GUI）

功能：加载配置的资产库 → 分类树浏览 → 缩略图网格预览。
Maya 专属功能（导入/导出/截图等）在独立模式下不可用。

验证：python standalone_preview.py --smoke-test  （创建窗口后立即退出）
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.abspath(__file__))      # .../squirrel_asset_manager
_PROJECT = os.path.dirname(_ROOT)                        # 项目根


def _ensure_front(p):
    """把路径强制移到 sys.path 最前（mayapy 等环境可能已把 cwd/项目根排在前面）"""
    while p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)


# 注意：_ROOT 必须排在 _PROJECT 之前（sys.path 靠前），
# 否则 manager.py 的绝对导入 `from core.material import ...` 会命中
# 项目根的旧版 core/（残留目录）而非包内的新版模块。
_ensure_front(_PROJECT)
_ensure_front(_ROOT)

# ── 包名兼容：脚本所在目录名与包名不一致时（如独立库 release 目录），
# 将脚本所在目录注册为 squirrel_asset_manager 包，使绝对导入可用 ──
if os.path.basename(_ROOT) != "squirrel_asset_manager" and \
        os.path.isfile(os.path.join(_ROOT, "__init__.py")):
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "squirrel_asset_manager",
        os.path.join(_ROOT, "__init__.py"),
        submodule_search_locations=[_ROOT],
    )
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["squirrel_asset_manager"] = _pkg
    try:
        _spec.loader.exec_module(_pkg)
    except ModuleNotFoundError as _e:
        _cur_abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
        # 收集本库核心 .pyd 支持的 ABI（如 cp313）
        import glob as _glob
        import re as _re
        import subprocess as _sp
        _need_abis = set()
        for _f in _glob.glob(os.path.join(_ROOT, "core", "*.pyd")) + \
                 _glob.glob(os.path.join(_ROOT, "integration", "*.pyd")):
            _m = _re.search(r"\.(cp\d+)-win", os.path.basename(_f))
            if _m:
                _need_abis.add(_m.group(1))
        _restarted = False
        if os.environ.get("_SAM_RESTARTED") != "1" and _cur_abi not in _need_abis:
            # 尝试用 py launcher 找到 ABI 匹配且装有 PySide6 的解释器并重启
            try:
                _out = _sp.run(["py", "-0p"], capture_output=True, text=True, timeout=15)
                _lines = (_out.stdout or "").splitlines()
            except Exception:
                _lines = []
            for _line in _lines:
                _vm = _re.search(r"-V:(\d+)\.(\d+)", _line)
                if not _vm:
                    continue
                _ver_abi = f"cp{_vm.group(1)}{_vm.group(2)}"
                if _ver_abi not in _need_abis:
                    continue
                _parts = _line.split(None, 1)
                if len(_parts) < 2:
                    continue
                _exe = _parts[1].strip()
                try:
                    _r = _sp.run([_exe, "-c", "import PySide6"],
                                 capture_output=True, timeout=20)
                except Exception:
                    continue
                if _r.returncode != 0:
                    continue
                print(f"[独立预览] 当前解释器 ({sys.version.split()[0]}) 与核心 .pyd 不匹配，"
                      f"自动改用 {_exe} 重新启动…")
                try:
                    _env = dict(os.environ, _SAM_RESTARTED="1")
                    _sp.run([_exe] + sys.argv, env=_env, check=False)
                    _restarted = True
                except Exception:
                    pass
                break
        if not _restarted:
            print("[独立预览] 错误：当前 Python 无法加载本库的核心模块（.pyd 与解释器版本不匹配）。")
            print(f"  详情: {_e}")
            print("  请使用与核心 .pyd 匹配的 Python 版本运行，例如：")
            print("    py -3.13 启动独立预览.bat    （对应 cp313 版本）")
            print("  或使用任意 Maya 自带的 mayapy（本 bat 会自动探测）。")
        sys.exit(1 if not _restarted else 0)

# ── Qt 绑定（PySide6 → PySide2 降级）──
try:
    from PySide6 import QtWidgets
    from PySide6 import QtCore
    _QT = "PySide6"
except ImportError:
    try:
        from PySide2 import QtWidgets
        from PySide2 import QtCore
        _QT = "PySide2"
    except ImportError:
        print("[独立预览] 错误：需要 PySide6 或 PySide2（pip install PySide6）")
        sys.exit(1)

from squirrel_asset_manager.utils.maya_utils import qt_exec


def main():
    parser = argparse.ArgumentParser(description="Squirrel Asset Manager 独立预览")
    parser.add_argument("--library", default=None,
                        help="资产库路径（默认读取插件设置中的第一个库）")
    parser.add_argument("--smoke-test", action="store_true",
                        help="创建窗口后立即退出（用于验证环境）")
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Squirrel Asset Manager 独立预览")

    from squirrel_asset_manager.ui.main_window import MaterialLibraryWindow

    win = MaterialLibraryWindow(parent=None, library_path=args.library)
    win.show()
    print(f"[独立预览] Qt 绑定: {_QT}")
    print("[独立预览] 关闭窗口即退出。")

    if args.smoke_test:
        QtCore.QTimer.singleShot(200, app.quit)

    return qt_exec(app)


if __name__ == "__main__":
    sys.exit(main())
