"""P1-2 效率统计：token 产出 / GPU 耗电 / tokens 每瓦（mock 历史存储）。"""
import json
import time

from fastapi.testclient import TestClient

from backend.ctl import config as ctl_config
from backend.efficiency import _bucket_series
from backend.main import create_app


class FakeStore:
    """24h 原始数据：llama @1s（gen_speed 恒定 10 tok/s），host @2s（GPU0 恒定 100W）。"""

    def __init__(self, now=None):
        self.now = now or int(time.time())
        t0 = self.now - 86400
        # 行格式与 query_* 返回一致（不含 host_id）
        self.ll = [(t, 10.0, None, None, None) for t in range(t0, self.now, 1)]
        self.hh = [(t, None, None, None, None, None, None, None, None, None, None,
                    json.dumps({"0": {"util": 50, "mem": 100, "temp": 60, "power": 100.0}}))
                   for t in range(t0, self.now, 2)]

    def query_llama(self, host_id, t0, t1, stride=1):
        return [r for r in self.ll if t0 <= r[0] < t1][::stride]

    def query_host(self, host_id, t0, t1, stride=1):
        return [r for r in self.hh if t0 <= r[0] < t1][::stride]

    def query_llama_1m(self, host_id, t0, t1, stride=1):
        return []

    def query_host_1m(self, host_id, t0, t1, stride=1):
        return []


class FakeMon:
    def snapshot(self):
        return {"host_metrics": {"gpus": [{"index": 0, "name": "RTX 3080"}]}}


def _app(monkeypatch, store):
    import backend.efficiency as eff
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(eff, "_monitor", lambda request, host_id: FakeMon())
    import tempfile
    app = create_app(tempfile.mkdtemp(prefix="eff-test-"))
    return app, store


def test_efficiency_24h(monkeypatch):
    store = FakeStore()
    app, _ = _app(monkeypatch, store)
    with TestClient(app) as client:
        client.app.state.history = store
        r = client.get("/api/efficiency", params={"host_id": "ai", "range": "24h"})
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is True
        # 10 tok/s × 86400s ≈ 864000 tokens（±采样边界）
        assert abs(d["tokens_total"] - 864000) < 200
        # 100W × 24h = 2.4 kWh
        assert abs(d["energy_kwh"] - 2.4) < 0.05
        assert d["gpus"][0]["name"] == "RTX 3080"
        assert abs(d["gpus"][0]["kwh"] - 2.4) < 0.05
        # tokens/Wh ≈ 864000 / 2400Wh = 360
        assert abs(d["tokens_per_wh"] - 360) < 5
        assert d["cost"] is None  # 未配电价
        # 24h 按小时分桶：24~25 个点（含当前不完整小时）
        assert 24 <= len(d["series"]["tokens"]["ts"]) <= 25
        assert d["series"]["tokens"]["values"][0] > 0


def test_efficiency_no_store(monkeypatch):
    app, _ = _app(monkeypatch, None)
    with TestClient(app) as client:
        client.app.state.history = None
        r = client.get("/api/efficiency", params={"host_id": "ai"})
        d = r.json()
        assert d["available"] is False


def test_bucket_series_fills_gaps():
    out = _bucket_series({0: 10.0, 3600: 20.0}, 3600, 0, 7201)
    assert out["ts"] == [0, 3600, 7200]
    assert out["values"] == [10.0, 20.0, 0.0]


def test_efficiency_custom_range(monkeypatch):
    """start/end 自定义范围（与历史趋势共用时间选择）：按给定范围统计。"""
    store = FakeStore()
    app, _ = _app(monkeypatch, store)
    now = store.now
    with TestClient(app) as client:
        client.app.state.history = store
        # 最近 2 小时（≤1 天 → 原始表、按小时分桶）
        r = client.get("/api/efficiency", params={"host_id": "ai", "start": now - 7200, "end": now})
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is True
        assert d["range"] == "custom"
        assert d["start"] == now - 7200
        assert d["end"] == now
        # 10 tok/s × 7200s ≈ 72000 tokens
        assert abs(d["tokens_total"] - 72000) < 200
        # 2 小时按小时分桶：2~3 个点
        assert 2 <= len(d["series"]["tokens"]["ts"]) <= 3


def test_efficiency_custom_range_validation(monkeypatch):
    store = FakeStore()
    app, _ = _app(monkeypatch, store)
    now = store.now
    with TestClient(app) as client:
        client.app.state.history = store
        # 太短（<1 分钟）→ 400
        r = client.get("/api/efficiency", params={"host_id": "ai", "start": now - 30, "end": now})
        assert r.status_code == 400
        # 太长（>90 天）→ 400
        r = client.get("/api/efficiency", params={"host_id": "ai", "start": now - 91 * 86400, "end": now})
        assert r.status_code == 400
