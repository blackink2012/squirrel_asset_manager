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
    _spec.loader.exec_module(_pkg)

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
