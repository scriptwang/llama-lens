"""压测端点：并发短请求 → 聚合吞吐 + TTFT/延迟分位（mock llama-server）。"""
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

    def __init__(self, online=True):
        self._online = online

    def snapshot(self):
        return {"llama": {"online": self._online, "model": {"name": "test.gguf"}}}


def _sse_lines(tokens=2):
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "x" * i}}]}) for i in range(1, tokens + 1)]
    lines.append("data: " + json.dumps({"choices": [], "usage": {"completion_tokens": tokens}}))
    lines.append("data: [DONE]")
    return lines


class FakeStreamResp:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"boom"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self, *a, **k):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None):
        self.calls += 1
        assert url.endswith("/v1/chat/completions")
        assert json["stream"] is True
        return FakeStreamResp(_sse_lines(2))


def _frames(text):
    out = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        event, data = "message", None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        out.append((event, json.loads(data) if data else None))
    return out


def _app(monkeypatch, mon=None, client_cls=FakeClient):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(pg, "_monitor", lambda request, host_id: mon or FakeMon())
    monkeypatch.setattr(pg.httpx, "AsyncClient", client_cls)
    import tempfile
    return create_app(tempfile.mkdtemp(prefix="stress-test-"))


def test_pct():
    assert pg._pct([], 50) is None
    assert pg._pct([10], 50) == 10
    assert pg._pct([10, 20, 30, 40], 50) in (20, 30)  # nearest-rank
    assert pg._pct([10, 20, 30, 40], 0) == 10
    assert pg._pct([10, 20, 30, 40], 100) == 40


def test_stress_summary(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/stress", json={"concurrency": 2, "count": 4, "max_tokens": 32})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        frames = _frames(r.text)
        progress = [d for ev, d in frames if ev == "progress"]
        summary = [d for ev, d in frames if ev == "summary"]
        assert len(progress) == 4
        assert len(summary) == 1
        s = summary[0]
        assert s["total"] == 4
        assert s["ok"] == 4
        assert s["fail"] == 0
        assert s["total_tokens"] == 8  # 2 tokens × 4 请求
        assert s["throughput_tps"] > 0
        # 解码吞吐：mock 极快时 gen 时间可能为 0 → None，否则应为正
        assert s["decode_tps"] is None or s["decode_tps"] > 0
        assert s["wall_s"] >= 0  # mock 极快，round 后可能为 0
        assert s["ttft_p50_ms"] is not None
        assert s["ttft_p99_ms"] is not None
        assert s["latency_p50_ms"] is not None
        assert s["latency_p99_ms"] is not None
        # progress 单调递增到 total
        assert [p["done"] for p in progress] == [1, 2, 3, 4]


def test_stress_offline(monkeypatch):
    app = _app(monkeypatch, mon=FakeMon(online=False))
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/stress", json={"concurrency": 1, "count": 2})
        frames = _frames(r.text)
        assert frames and frames[0][0] == "error"
        assert "离线" in frames[0][1]["msg"]


def test_stress_all_fail(monkeypatch):
    class FailStream(FakeStreamResp):
        def __init__(self):
            super().__init__([], status=500)

    class FailClient(FakeClient):
        def stream(self, method, url, json=None):
            return FailStream()

    app = _app(monkeypatch, client_cls=FailClient)
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/stress", json={"concurrency": 2, "count": 3})
        frames = _frames(r.text)
        summary = [d for ev, d in frames if ev == "summary"][0]
        assert summary["total"] == 3
        assert summary["ok"] == 0
        assert summary["fail"] == 3
        assert summary["total_tokens"] == 0
        # 全失败时分位为 None
        assert summary["ttft_p50_ms"] is None
        assert summary["latency_p99_ms"] is None
        assert summary["decode_tps"] is None


def test_stress_param_clamp(monkeypatch):
    """超范围参数被限幅（不报错）。"""
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/hosts/ai/stress", json={"concurrency": 999, "count": 9999, "max_tokens": 99999})
        frames = _frames(r.text)
        summary = [d for ev, d in frames if ev == "summary"][0]
        assert summary["total"] == 100  # count 限幅到 100
