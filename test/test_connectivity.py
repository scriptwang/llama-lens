"""connectivity-test 端点：SSH（id -u）+ 引擎 API 双链路，全部 mock，无真实网络。"""
from types import SimpleNamespace

import httpx

from backend.ctl.errors import ApiError, SSH_UNREACHABLE
from backend.ctl.routers import hosts as h
from backend.ctl import security


def _row(**kw):
    base = {
        "id": 4, "mid": "ai", "host": "ai.lan", "port": 22,
        "username": "root", "auth_type": "password",
        "encrypted_pwd": "enc", "key_passphrase_enc": None,
        "llama_host": "ai.lan", "llama_port": 8080,
        "engine_type": "sglang", "engine_host": "", "engine_port": 30000,
        "engine_api_key_enc": None,
    }
    base.update(kw)
    return base


class _FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class _FakeHttp:
    """httpx.Client 替身：上下文管理器 + 记录请求路径。"""

    def __init__(self, resp=None, exc=None, paths=None):
        self._resp = resp
        self._exc = exc
        self.paths = paths

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path):
        if self.paths is not None:
            self.paths.append(path)
        if self._exc is not None:
            raise self._exc
        return self._resp


def _setup(monkeypatch, row, ssh_exc=None, http_factory=None):
    monkeypatch.setattr(h, "get_host_row", lambda host_id: row)
    monkeypatch.setattr(h, "host_to_conn_dict", lambda r: {"id": r["id"], "host": r["host"]})
    fake_client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(h.pool, "create_client", lambda d: fake_client)

    def _run(client, command, timeout=None):
        if ssh_exc is not None:
            raise ssh_exc
        return "0"
    monkeypatch.setattr(h, "run", _run)

    captured = {}

    def _client_factory(**kw):
        c = http_factory()
        captured["factory_kwargs"] = kw
        return c
    monkeypatch.setattr(h.httpx, "Client", _client_factory)
    monkeypatch.setattr(h.db, "log_action", lambda *a, **k: None)
    return captured


def _request():
    return SimpleNamespace(client=None)


def test_connectivity_all_ok(monkeypatch):
    """SSH 通 + sglang /v1/loads 200 → 整体 ok，engine 带版本，延迟为整数毫秒。"""
    row = _row()
    _setup(monkeypatch, row,
           http_factory=lambda: _FakeHttp(_FakeResp(200, {"version": "0.5.20"})))
    out = h.connectivity_test(4, _request(), user="t")
    d = out["data"]
    assert d["ok"] is True
    assert d["ssh"]["ok"] is True and isinstance(d["ssh"]["latency_ms"], int)
    assert d["engine"]["ok"] is True
    assert d["engine"]["detail"] == "version 0.5.20"


def test_connectivity_ssh_fail_and_http_401(monkeypatch):
    """SSH 失败 + 引擎 401 → 整体不 ok，各链路带 detail。"""
    row = _row()
    _setup(monkeypatch, row,
           ssh_exc=ApiError(SSH_UNREACHABLE, "SSH 连接异常：refused"),
           http_factory=lambda: _FakeHttp(_FakeResp(401)))
    out = h.connectivity_test(4, _request(), user="t")
    d = out["data"]
    assert d["ok"] is False
    assert d["ssh"]["ok"] is False and "refused" in d["ssh"]["detail"]
    assert d["engine"]["ok"] is False
    assert d["engine"]["detail"] == "HTTP 401"


def test_connectivity_llama_uses_health(monkeypatch):
    """llama_cpp 主机走 /health，而非 /v1/loads。"""
    row = _row(engine_type="llama_cpp")
    _setup(monkeypatch, row,
           http_factory=lambda: _FakeHttp(_FakeResp(200, {"status": "ok"})))
    out = h.connectivity_test(4, _request(), user="t")
    assert out["data"]["ok"] is True
    assert "version" not in out["data"]["engine"].get("detail", "")


def test_connectivity_connect_error_no_key_leak(monkeypatch):
    """连接异常：错误详情不得回显 engine api key 明文。"""
    row = _row(engine_api_key_enc=security.encrypt_secret("local-test-key"))
    req = httpx.Request("GET", "http://ai.lan:30000/v1/loads")
    _setup(monkeypatch, row,
           http_factory=lambda: _FakeHttp(exc=httpx.ConnectError("connect failed", request=req)))
    out = h.connectivity_test(4, _request(), user="t")
    d = out["data"]
    assert d["ok"] is False
    assert d["engine"]["ok"] is False
    assert "ConnectError" in d["engine"]["detail"]
    assert "local-test-key" not in str(d)


def test_builtin_scan_rules_cover_all_engines():
    """内置扫描规则覆盖面板支持的全部引擎（llama + sglang）。"""
    from backend.ctl import config as cfg
    assert "llama" in cfg.BUILTIN_SCAN_RULES["name_keywords"]
    assert "sglang" in cfg.BUILTIN_SCAN_RULES["name_keywords"]
    assert "sglang" in cfg.BUILTIN_SCAN_RULES["content_markers"]
    for et in h.ENGINE_TYPES:
        assert et in ("llama_cpp", "sglang")
