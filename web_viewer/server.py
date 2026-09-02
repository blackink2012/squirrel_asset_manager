# -*- coding: utf-8 -*-
"""web_viewer.server — 资产库浏览器版 HTTP 服务（纯标准库）

零依赖：http.server + json + os，任意 Python 3.8+ 可运行。

路由:
  GET  /                     前端页面
  GET  /static/*             静态资源
  GET  /api/state            库状态 + 分类树 + 标签云（不含资产明细）
  GET  /api/assets           资产列表（过滤/搜索/排序/分页）
  GET  /api/asset/<id>       资产详情
  GET  /api/thumb/<id>       缩略图（aicon GIF 优先，sicon PNG 兜底）
  GET  /api/media/<id>       动图/视频（thumb.mp4）
  GET  /api/file/<id>?rel=   zasset 内部文件（贴图预览，带路径安全检查）
  GET  /api/fs?path=         服务端文件夹浏览（用于库路径选择）
  GET  /api/settings         查看当前设置（端口/库路径）
  POST /api/library          设置资产库路径（写回 app_settings.json）
  POST /api/refresh          重新扫描
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import re
import string
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from .scanner import LibraryState, sniff_image_mime, INLINE_IMAGE_EXTS

# ── 路径常量 ────────────────────────────────────────────
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))          # .../web_viewer
_ROOT = os.path.dirname(_PKG_DIR)                              # .../squirrel_asset_manager
_STATIC_DIR = os.path.join(_PKG_DIR, "static")
_CONFIG_PATH = os.path.join(_ROOT, "Assets", "preset", "config.json")
_SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".squirrel_asset_manager")
_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "app_settings.json")

DEFAULT_PORT = 8765
MAX_PAGE = 500          # 单页最大返回数
ABS_PAGE_LIMIT = 4000   # 硬上限


# ── 应用设置读写（与 Maya 插件共享 app_settings.json） ───

def load_app_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def save_app_settings(update: dict) -> dict:
    """合并写入 app_settings.json（保留其他键，原子替换）"""
    merged = load_app_settings()
    merged.update(update)
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    tmp = _SETTINGS_PATH + ".tmp_webviewer"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _SETTINGS_PATH)
    return merged


# ── 全局状态 ────────────────────────────────────────────

STATE = LibraryState(_CONFIG_PATH)


def resolve_library_path(args) -> str:
    """库路径优先级: CLI 参数 > app_settings.last_library_path"""
    if getattr(args, "library", ""):
        return os.path.abspath(args.library)
    settings = load_app_settings()
    return settings.get("last_library_path", "") or ""


# ── 过滤 / 搜索 / 排序 ─────────────────────────────────

def filter_assets(snap: dict, params: dict) -> dict:
    """按参数过滤资产列表，返回 {items, total}"""
    assets = snap.get("assets", [])

    lib = params.get("lib", "")
    cat = params.get("category", "")
    q = (params.get("q") or "").strip().lower()
    tags = [t for t in (params.get("tags") or "").split(",") if t.strip()]
    sort = params.get("sort", "recent")

    items = []
    for a in assets:
        if lib and a["sub_library"] != lib:
            continue
        if cat and cat not in a["category_chain"]:
            continue
        if tags:
            at = set(a["tags"])
            if not all(t in at for t in tags):
                continue
        if q:
            hay = " ".join([
                a.get("name") or "", a.get("name_cn") or "",
                a.get("node_type") or "", a.get("software") or "",
                a.get("renderer") or "", " ".join(a.get("tags") or []),
                a.get("sub_library") or "",
            ]).lower()
            # 支持多关键词（空格 AND）
            if not all(frag in hay for frag in q.split()):
                continue
        items.append(a)

    # 排序
    if sort == "oldest":
        items.sort(key=lambda a: ((a.get("file_mtime") or 0), (a.get("name") or "").lower()))
    elif sort == "name_asc":
        items.sort(key=lambda a: (a.get("name_cn") or a.get("name") or "").lower())
    elif sort == "name_desc":
        items.sort(key=lambda a: (a.get("name_cn") or a.get("name") or "").lower(), reverse=True)
    else:  # recent
        items.sort(key=lambda a: (-(a.get("file_mtime") or 0), (a.get("name") or "").lower()))

    total = len(items)
    try:
        page = max(1, int(params.get("page", 1)))
        page_size = min(MAX_PAGE, max(1, int(params.get("page_size", 120))))
    except ValueError:
        page, page_size = 1, 120
    page_size = min(page_size, ABS_PAGE_LIMIT)
    start = (page - 1) * page_size
    items = items[start:start + page_size]

    # 精简字段（列表页不需要贴图清单）
    slim_keys = ("id", "name", "name_cn", "sub_library", "category_chain", "category",
                 "tags", "node_type", "software", "renderer", "color_space",
                 "create_date", "resolution", "formats", "ani", "has_variants",
                 "has_aicon", "has_sicon", "has_mp4", "texture_count",
                 "file_mtime", "zasset_name")
    slim = [{k: a[k] for k in slim_keys} for a in items]
    return {"items": slim, "total": total, "page": page, "page_size": page_size}


# ── 文件系统浏览（库路径选择用） ────────────────────────

def list_windows_drives() -> list:
    drives = []
    for letter in string.ascii_uppercase:
        d = f"{letter}:\\"
        if os.path.isdir(d):
            drives.append(d)
    return drives


def browse_fs(path: str) -> dict:
    """列出目录的子文件夹（供前端文件夹选择器）"""
    path = path or os.path.abspath(os.sep)
    if not os.path.isdir(path):
        return {"path": path, "parent": "", "dirs": [], "error": "路径不存在"}
    dirs = []
    try:
        for name in sorted(os.listdir(path), key=lambda s: s.lower()):
            full = os.path.join(path, name)
            if os.path.isdir(full) and not name.startswith("$"):
                dirs.append({"name": name, "path": full})
    except OSError as e:
        return {"path": path, "parent": "", "dirs": [], "error": str(e)}
    parent = os.path.dirname(path.rstrip("\\/")) or ""
    if parent == path:
        parent = ""
    return {"path": path, "parent": parent, "dirs": dirs, "error": ""}


# ── 请求处理器 ──────────────────────────────────────────

class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "SquirrelWebViewer/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础工具 ----

    def log_message(self, fmt, *args):
        # 精简日志：跳过静态资源 200
        if "/static/" in (args[0] if args else ""):
            return
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, mime: str, etag: str = "", download_name: str = ""):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=300")
        if etag:
            self.send_header("ETag", '"%s"' % etag)
        if download_name:
            self.send_header("Content-Disposition",
                             'inline; filename="%s"' % download_name.replace('"', ""))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: str, mime: str):
        try:
            stat = os.stat(path)
        except OSError:
            self._send_json({"error": "file not found"}, 404)
            return
        etag = "%d-%d" % (stat.st_mtime_ns, stat.st_size)
        if self.headers.get("If-None-Match", "").strip('"') == etag:
            self.send_response(304)
            self.send_header("ETag", '"%s"' % etag)
            self.end_headers()
            return
        with open(path, "rb") as f:
            data = f.read()
        self._send_bytes(data, mime, etag=etag,
                         download_name=os.path.basename(path))

    def _read_body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError, OSError):
            return {}

    def _find_asset(self, asset_id: str) -> dict:
        snap = STATE.get_snapshot()
        if not snap:
            return {}
        for a in snap.get("assets", []):
            if a["id"] == asset_id:
                return a
        return {}

    def _safe_zasset_file(self, asset: dict, rel: str) -> str:
        """把 rel 解析为 zasset 内部文件路径（拒绝越界）"""
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            return ""
        full = os.path.normpath(os.path.join(asset["zasset_path"], rel))
        root = os.path.normpath(asset["zasset_path"])
        if full != root and not full.startswith(root + os.sep):
            return ""
        return full if os.path.isfile(full) else ""

    # ---- GET 路由 ----

    def do_GET(self):
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        # 首页
        if route in ("/", "/index.html"):
            return self._serve_static("index.html")

        # 静态资源
        if route.startswith("/static/"):
            rel = posixpath.normpath(route[len("/static/"):]).lstrip("/")
            if ".." in rel.split("/"):
                return self._send_json({"error": "forbidden"}, 403)
            return self._serve_static(rel)

        # API
        if route == "/api/state":
            return self._api_state()
        if route == "/api/assets":
            return self._api_assets(qs)
        if route == "/api/settings":
            settings = load_app_settings()
            return self._send_json({
                "port": self.server.server_address[1],
                "library_path": STATE.library_path,
                "last_library_path": settings.get("last_library_path", ""),
            })
        if route == "/api/refresh":
            ok = STATE.scan(STATE.library_path)
            return self._send_json({"ok": ok, "scanning": STATE.scanning})

        m = re.match(r"^/api/asset/([^/]+)$", route)
        if m:
            return self._api_asset_detail(unquote(m.group(1)))
        m = re.match(r"^/api/thumb/([^/]+)$", route)
        if m:
            return self._api_thumb(unquote(m.group(1)), qs.get("file", ""))
        m = re.match(r"^/api/media/([^/]+)$", route)
        if m:
            return self._api_media(unquote(m.group(1)), qs.get("file", ""))
        m = re.match(r"^/api/file/([^/]+)$", route)
        if m:
            return self._api_file(unquote(m.group(1)), qs.get("rel", ""))
        if route == "/api/fs":
            return self._send_json(browse_fs(qs.get("path", "")))

        self._send_json({"error": "not found", "path": route}, 404)

    # ---- POST 路由 ----

    def do_POST(self):
        parsed = urlparse(self.path)
        route = unquote(parsed.path)

        if route == "/api/library":
            body = self._read_body_json()
            path = (body.get("path") or "").strip()
            if not path:
                return self._send_json({"ok": False, "error": "路径为空"}, 400)
            if not os.path.isdir(path):
                return self._send_json({"ok": False, "error": "路径不存在: %s" % path}, 400)
            # 写回 app_settings.json（与 Maya 插件共享 last_library_path）
            save_app_settings({"last_library_path": path.replace("/", os.sep)})
            ok = STATE.scan(path)
            return self._send_json({
                "ok": ok,
                "library_path": path,
                "total": (STATE.get_snapshot() or {}).get("total", 0),
            })

        if route == "/api/refresh":
            ok = STATE.scan(STATE.library_path)
            return self._send_json({"ok": ok, "scanning": STATE.scanning})

        self._send_json({"error": "not found"}, 404)

    # ---- API 实现 ----

    def _serve_static(self, rel: str):
        full = os.path.normpath(os.path.join(_STATIC_DIR, rel))
        if not os.path.isfile(full):
            return self._send_json({"error": "not found", "file": rel}, 404)
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            mime = "text/javascript; charset=utf-8"
        elif full.endswith(".css"):
            mime = "text/css; charset=utf-8"
        elif full.endswith(".html"):
            mime = "text/html; charset=utf-8"
        self._send_file(full, mime)

    def _api_state(self):
        snap = STATE.get_snapshot()
        if not snap:
            return self._send_json({
                "ready": False,
                "scanning": STATE.scanning,
                "error": STATE.error or "尚未加载资产库",
                "library_path": STATE.library_path,
                "config_sub_libraries": STATE.scanner.sub_libraries,
            })
        # 不带 assets 明细，减小响应
        payload = {k: v for k, v in snap.items() if k != "assets"}
        payload["ready"] = True
        payload["scanning"] = STATE.scanning
        payload["error"] = STATE.error
        return self._send_json(payload)

    def _api_assets(self, qs: dict):
        snap = STATE.get_snapshot()
        if not snap:
            return self._send_json({"items": [], "total": 0, "error": "未加载资产库"})
        result = filter_assets(snap, qs)
        return self._send_json(result)

    def _api_asset_detail(self, asset_id: str):
        asset = self._find_asset(asset_id)
        if not asset:
            return self._send_json({"error": "asset not found"}, 404)
        # zasset 内部文件清单（不含贴图，贴图已在 textures 字段）
        try:
            entries = sorted(os.listdir(asset["zasset_path"]))
        except OSError:
            entries = []
        asset = dict(asset)
        asset["entries"] = entries
        asset["has_meta"] = "meta.json" in entries
        asset["previews"] = self._build_previews(asset)
        return self._send_json(asset)

    # 浏览器可直接预览的图片扩展名（exr/hdr/tga/tiff 等不算预览图）
    _PREVIEW_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

    def _build_previews(self, asset: dict) -> list:
        """收集资产的全部预览图（thumb* 系列：sicon 静态图 / aicon 动图 / mp4 视频）

        一个资产可有多张预览图（thumb.sicon + thumb_2.sicon + ...），
        按扫描器排序顺序返回（thumb.sicon 第一张）。

        返回: [{type: image|gif|video, name, url}]
        """
        import urllib.parse as _up
        aid = _up.quote(asset["id"])
        previews = []
        for tf in asset.get("thumb_files", []):
            fname = _up.quote(tf["name"])
            if tf["kind"] == "mp4":
                previews.append({"type": "video", "name": tf["name"],
                                 "url": f"/api/media/{aid}?file={fname}"})
            elif tf["kind"] == "aicon":
                previews.append({"type": "gif", "name": tf["name"],
                                 "url": f"/api/media/{aid}?file={fname}"})
            else:
                previews.append({"type": "image", "name": tf["name"],
                                 "url": f"/api/thumb/{aid}?file={fname}"})
        return previews

    def _resolve_thumb_file(self, asset: dict, file_name: str) -> str:
        """把 file 参数解析为 zasset 内 thumb* 文件路径（拒绝越界与非 thumb 文件）"""
        if not file_name:
            return ""
        base = os.path.basename(file_name.replace("\\", "/"))
        low = base.lower()
        if not (low.startswith("thumb") and (
                low.endswith(".sicon") or low.endswith(".aicon") or low.endswith(".mp4"))):
            return ""
        full = os.path.normpath(os.path.join(asset["zasset_path"], base))
        root = os.path.normpath(asset["zasset_path"])
        if full != root and not full.startswith(root + os.sep):
            return ""
        return full if os.path.isfile(full) else ""

    def _api_thumb(self, asset_id: str, file_name: str = ""):
        asset = self._find_asset(asset_id)
        if not asset:
            return self._send_json({"error": "asset not found"}, 404)
        # 指定预览图文件（多预览图场景：thumb_2.sicon 等）
        if file_name:
            fp = self._resolve_thumb_file(asset, file_name)
            if not fp:
                return self._send_json({"error": "thumb file not found"}, 404)
            with open(fp, "rb") as f:
                head = f.read(16)
            mime = sniff_image_mime(head)
            if mime == "application/octet-stream":
                mime = "image/png"
            return self._send_file(fp, mime)
        # 默认：优先 GIF 动图，其次 PNG；卡片小图用静态 sicon 更省流量
        prefer = "thumb.aicon" if self.path.endswith("?animated=1") else "thumb.sicon"
        candidates = [prefer, "thumb.aicon", "thumb.sicon"]
        for name in candidates:
            fp = os.path.join(asset["zasset_path"], name)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    head = f.read(16)
                mime = sniff_image_mime(head)
                if mime == "application/octet-stream":
                    mime = "image/png"
                return self._send_file(fp, mime)
        return self._send_json({"error": "no thumbnail"}, 404)

    def _api_media(self, asset_id: str, file_name: str = ""):
        asset = self._find_asset(asset_id)
        if not asset:
            return self._send_json({"error": "asset not found"}, 404)
        # 指定动图/视频文件（多预览图场景：thumb_2.aicon 等）
        if file_name:
            fp = self._resolve_thumb_file(asset, file_name)
            if not fp:
                return self._send_json({"error": "media file not found"}, 404)
            low = fp.lower()
            if low.endswith(".mp4"):
                return self._send_file(fp, "video/mp4")
            return self._send_file(fp, "image/gif")
        fp = os.path.join(asset["zasset_path"], "thumb.mp4")
        if os.path.isfile(fp):
            return self._send_file(fp, "video/mp4")
        # 无 mp4 时回退 aicon GIF
        fp = os.path.join(asset["zasset_path"], "thumb.aicon")
        if os.path.isfile(fp):
            return self._send_file(fp, "image/gif")
        return self._send_json({"error": "no media"}, 404)

    def _api_file(self, asset_id: str, rel: str):
        asset = self._find_asset(asset_id)
        if not asset:
            return self._send_json({"error": "asset not found"}, 404)
        full = self._safe_zasset_file(asset, rel)
        if not full:
            return self._send_json({"error": "invalid path"}, 400)
        ext = os.path.splitext(full)[1].lower()
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ext in INLINE_IMAGE_EXTS:
            with open(full, "rb") as f:
                head = f.read(16)
            sniffed = sniff_image_mime(head)
            if sniffed != "application/octet-stream":
                mime = sniffed
            else:
                # tga/tiff/psd 等浏览器不支持的格式 → 占位
                if ext in (".tga", ".tif", ".tiff", ".psd", ".exr", ".hdr"):
                    return self._send_json({"error": "unsupported image format",
                                            "ext": ext}, 415)
        return self._send_file(full, mime)


# ── 入口 ────────────────────────────────────────────────

def _kill_port_owner(port: int) -> bool:
    """结束占用指定端口的 python 进程（Windows），供启动前清理旧实例。

    仅在占用者是 python 时结束，避免误杀其他程序。失败静默返回 False。
    """
    if os.name != "nt":
        return False
    import subprocess

    def _run(args):
        # Windows 控制台输出为 GBK 编码，避免 text=True 的 UTF-8 解码崩溃
        r = subprocess.run(args, capture_output=True, timeout=10)
        return (r.stdout or b"").decode("gbk", errors="replace")

    try:
        out = _run(["netstat", "-ano", "-p", "tcp"])
        pids = set()
        for line in out.splitlines():
            if "LISTENING" not in line:
                continue
            parts = line.split()
            # netstat 行: TCP  127.0.0.1:8765  0.0.0.0:0  LISTENING  33992
            if len(parts) >= 5 and parts[1].endswith(f":{port}"):
                pids.add(parts[-1])
        killed = False
        for pid in pids:
            info = _run(["tasklist", "/FI", f"PID eq {pid}"])
            if "python" not in info.lower():
                continue
            _run(["taskkill", "/PID", pid, "/F"])
            print(f"[网页浏览器] 已结束占用端口 {port} 的旧实例 PID={pid}")
            killed = True
        return killed
    except Exception as e:
        print(f"[网页浏览器] 端口清理失败: {e}")
        return False


def pick_port(preferred: int, max_try: int = 20) -> int:
    import socket
    port = preferred
    for _ in range(max_try):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                # 端口被占：先尝试结束旧 web_viewer 实例，再重试原端口
                if _kill_port_owner(port):
                    continue
                port += 1
    return preferred


def main(argv=None):
    parser = argparse.ArgumentParser(description="Squirrel 资产库网页浏览器")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--library", default="", help="资产库路径（默认取 app_settings.last_library_path）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    port = pick_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ViewerHandler)
    httpd.daemon_threads = True

    lib = resolve_library_path(args)
    if lib:
        print(f"[网页浏览器] 资产库: {lib}")
        STATE.scan(lib, background=True)
    else:
        print("[网页浏览器] 未配置资产库路径，请在页面设置中选择")

    url = f"http://127.0.0.1:{port}/"
    print(f"[网页浏览器] 服务已启动: {url}  (Ctrl+C 退出)")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[网页浏览器] 已退出")


if __name__ == "__main__":
    main()
