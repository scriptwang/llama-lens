"""P1-1 Playground：SSE 代理（mock llama-server）+ endpoint 信息。"""
import json

import httpx
from fastapi.testclient import TestClient

import backend.playground as pg
from backend.ctl import config as ctl_config
from backend.main import create_app


class FakeLlamaCfg:
    host = "127.0.0.1"
    port = 9999


class FakeCfg:
    llama = FakeLlamaCfg()


class FakeMon:
    cfg = FakeCfg()

    def snapshot(self):
        return {"llama": {"online": True, "model": {"name": "test.gguf"}}}


def _sse_lines():
    return [
        "data: " + json.dumps({"choices": [{"delta": {"content": "你好"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "世界"}}]}),
        "data: " + json.dumps({"choices": [], "usage": {"completion_tokens": 2}}),
        "data: [DONE]",
    ]


class FakeStreamResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None):
        assert method == "POST" and url.endswith("/v1/chat/completions")
        assert json["stream"] is True
        assert "model" not in json  # 不传 model，避免名称不匹配 404
        return FakeStreamResp(_sse_lines())


def _frames(text):
    out = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        event = "message"
        data = None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        out.append((event, json.loads(data) if data else None))
    return out


def _app(monkeypatch, client_cls=FakeClient):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(pg, "_monitor", lambda request, host_id: FakeMon())
    monkeypatch.setattr(pg.httpx, "AsyncClient", client_cls)
    import tempfile
    return create_app(tempfile.mkdtemp(prefix="pg-test-"))


def test_chat_sse_proxy(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/chat",
                        json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        frames = _frames(r.text)
        contents = [d["choices"][0]["delta"]["content"] for ev, d in frames if ev == "message"]
        assert contents == ["你好", "世界"]
        metrics = [d for ev, d in frames if ev == "metrics"][0]
        assert metrics["tokens"] == 2  # 取 usage 而非 delta 计数
        assert metrics["ttft_ms"] is not None
        assert metrics["total_ms"] >= 0
        assert metrics["tps"] > 0


def test_chat_connect_error(monkeypatch):
    class FailClient(FakeClient):
        async def __aenter__(self):
            raise httpx.ConnectError("connection refused")

    app = _app(monkeypatch, FailClient)
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/chat",
                        json={"messages": [{"role": "user", "content": "hi"}]})
        frames = _frames(r.text)
        assert frames and frames[0][0] == "error"
        assert "无法连接" in frames[0][1]["msg"]


def test_chat_empty_messages(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/chat", json={"messages": []})
        frames = _frames(r.text)
        assert frames and frames[0][0] == "error"


def test_playground_info(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/hosts/ai/playground")
        assert r.status_code == 200
        d = r.json()
        assert d["endpoint"] == "http://127.0.0.1:9999/v1"
        assert d["model"] == "test.gguf"
        assert d["online"] is True
