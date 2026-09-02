# -*- coding: utf-8 -*-
"""网页版资产浏览器入口 — 浏览器中浏览资产库（只读，无需 Maya / PySide6）

双击同目录的「启动网页浏览.bat」即可运行，或命令行:
    python web_viewer.py [--port 8765] [--library D:/path/to/lib] [--no-browser]

资产库路径优先级:
    --library 参数 > ~/.squirrel_asset_manager/app_settings.json 的 last_library_path
    （与 Maya 插件共用同一配置，在网页设置里改路径也会同步写回该文件）
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))      # .../squirrel_asset_manager
_PROJECT = os.path.dirname(_ROOT)                        # 项目根


def _ensure_front(p):
    while p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)


# _ROOT 在前，避免命中项目根残留的旧版模块
_ensure_front(_PROJECT)
_ensure_front(_ROOT)


def main():
    from squirrel_asset_manager.web_viewer.server import main as serve
    serve()


if __name__ == "__main__":
    main()
