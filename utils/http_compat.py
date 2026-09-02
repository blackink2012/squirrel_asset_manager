# -*- coding: utf-8 -*-
"""requests 兼容层：Maya 2027（Python 3.13）不再自带 requests，自动回退标准库 urllib。

对外暴露与 requests 常用子集一致的接口（get / post / HTTPError / Response），
核心 AI 模块以 `from ..utils.http_compat import requests` 引入，
有 requests 时直接用真实模块，没有时用本文件的 urllib 最小实现，调用方无需感知差异。
"""

try:
    import requests  # noqa: F401
except ImportError:
    # ── 标准库 urllib 最小实现（仅覆盖本项目用到的 requests 子集）──
    import json as _json
    import types as _types
    import urllib.error as _urlerror
    import urllib.request as _urllib

    class HTTPError(Exception):
        """模拟 requests.HTTPError，携带 .response 供调用方读取状态码与正文。"""

        def __init__(self, message, response=None):
            super().__init__(message)
            self.response = response

    class Response:
        """模拟 requests.Response（status_code / text / json / raise_for_status）。"""

        def __init__(self, status_code, body_bytes):
            self.status_code = status_code
            self._body = body_bytes

        @property
        def text(self):
            return self._body.decode("utf-8", "replace")

        def json(self):
            return _json.loads(self.text)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPError("HTTP %s" % self.status_code, response=self)

    def _request(method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        data = None
        payload = kwargs.pop("json", None)
        if payload is not None:
            data = _json.dumps(payload).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        timeout = kwargs.pop("timeout", None)
        req = _urllib.Request(url, data=data, headers=headers, method=method)
        try:
            with _urllib.urlopen(req, timeout=timeout) as resp:
                return Response(resp.status, resp.read())
        except _urlerror.HTTPError as e:
            # urllib 对 4xx/5xx 直接抛异常；requests 语义是返回响应对象，
            # 由调用方 raise_for_status() 统一处理，这里保持一致
            return Response(e.code, e.read())

    def get(url, **kwargs):
        return _request("GET", url, **kwargs)

    def post(url, **kwargs):
        return _request("POST", url, **kwargs)

    # 组装成模块对象，使调用方 `from ..utils.http_compat import requests`
    # 拿到后仍可 requests.get / requests.post / requests.HTTPError
    requests = _types.ModuleType("requests")
    requests.get = get
    requests.post = post
    requests.HTTPError = HTTPError
    requests.Response = Response
