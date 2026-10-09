"""网关故障隔离：旁路（审计/链路/预览/统计）任何环节崩溃，代理主链路不受影响。"""
import json
import sqlite3
import tempfile

from fastapi.testclient import TestClient

import backend.gateway.router as gwr
from backend.ctl import config as ctl_config
from backend.gateway import keys as keys_mod
from backend.main import create_app


class _Llama:
    host, port = "127.0.0.1", 9999


class _Cfg:
    def __init__(self, id):
        self.id, self.name, self.llama = id, id, _Llama()


class _Mon:
    def __init__(self, id, model):
        self.cfg = _Cfg(id)
        self._model = model

    def snapshot(self):
        return {"llama": {"online": True, "model": {"name": self._model}}}


class _Reg:
    def __init__(self, mons):
        self.monitors = {m.cfg.id: m for m in mons}


class _StreamResp:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    async def aiter_bytes(self):
        yield b"data: " + json.dumps({"choices": [{"delta": {"content": "收到"}}]},
                                     ensure_ascii=False).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"

    async def aread(self):
        return b""


class _StreamCM:
    async def __aenter__(self):
        return _StreamResp()

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        raise AssertionError("unexpected get")

    def stream(self, method, url, content=None, headers=None):
        return _StreamCM()


def _app(monkeypatch):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeClient)
    return create_app(tempfile.mkdtemp(prefix="gw-iso-"))


def _boom(*a, **k):
    raise RuntimeError("bypass down")


def test_writer_down_stream_still_200(monkeypatch):
    """审计写队列全挂：流式代理照常 200 + 内容完整。"""
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "t")
        w = app.state.gateway["writer"]
        monkeypatch.setattr(w, "enqueue_request", _boom)
        monkeypatch.setattr(w, "enqueue_spans", _boom)
        monkeypatch.setattr(w, "enqueue_usage", _boom)
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "hi"}],
                              "stream": True},
                        headers={"Authorization": "Bearer %s" % row["key"]})
        assert r.status_code == 200
        assert "收到" in r.text


def test_sse_parse_down_stream_still_200(monkeypatch):
    """SSE 旁路解析（usage/预览）抛异常：不断流。"""
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "t")
        monkeypatch.setattr(gwr._SseUsage, "feed", _boom)
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "hi"}],
                              "stream": True},
                        headers={"Authorization": "Bearer %s" % row["key"]})
        assert r.status_code == 200
        assert "收到" in r.text


def test_key_store_down_returns_503_not_500(monkeypatch):
    """key 存储异常：干净 503（不是 500 堆栈）。"""
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "t")

        def locked(token):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(app.state.gateway["store"], "get_key_by_token", locked)
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "hi"}]},
                        headers={"Authorization": "Bearer %s" % row["key"]})
        assert r.status_code == 503


def test_reject_bypass_down_still_401(monkeypatch):
    """拒绝路径旁路全挂：401 照常返回。"""
    app = _app(monkeypatch)
    with TestClient(app) as client:
        w = app.state.gateway["writer"]
        monkeypatch.setattr(w, "enqueue_request", _boom)
        monkeypatch.setattr(w, "enqueue_spans", _boom)
        monkeypatch.setattr(w, "enqueue_usage", _boom)
        r = client.post("/v1/chat/completions",
                        json={"model": "x", "messages": [{"role": "user", "content": "t"}]})
        assert r.status_code == 401


class _JsonResp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    async def aread(self):
        return self._raw


class _JsonCM:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return _JsonResp(self._payload)

    async def __aexit__(self, *a):
        return False


class FakeJsonClient:
    payload = {"choices": [{"message": {"content": "非流式回复"}}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 4}}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        raise AssertionError("unexpected get")

    def stream(self, method, url, content=None, headers=None):
        return _JsonCM(FakeJsonClient.payload)


def test_writer_down_nonstream_still_200(monkeypatch):
    """审计写队列全挂：非流式代理照常 200 + 内容完整。"""
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeJsonClient)
    app = create_app(tempfile.mkdtemp(prefix="gw-iso-"))
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "t")
        w = app.state.gateway["writer"]
        monkeypatch.setattr(w, "enqueue_request", _boom)
        monkeypatch.setattr(w, "enqueue_spans", _boom)
        monkeypatch.setattr(w, "enqueue_usage", _boom)
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "hi"}]},
                        headers={"Authorization": "Bearer %s" % row["key"]})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "非流式回复"
