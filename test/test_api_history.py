import json

from backend.api import _gpu_series, _history_from_store


def test_gpu_series_downsampled():
    rows = []
    for i in range(10000):
        rows.append((1000 + i, None, None, None, None, None, None, None, None, None, None,
                     json.dumps({"0": {"util": 50.0, "mem": 100.0}})))
    out = _gpu_series(rows, 11, (("util", "gpu_util_"), ("mem", "gpu_mem_")))
    # 回归：GPU 序列必须与标量一致降采样到 ~600 点（此前 24h 窗口返回 8.6 万点）
    assert len(out["gpu_util_0"]["values"]) <= 601
    assert len(out["gpu_mem_0"]["values"]) <= 601
    assert out["gpu_util_0"]["values"][0] == 50.0


def test_gpu_series_skips_bad_json_and_empty():
    rows = [(1, "not-json"), (2, None), (3, json.dumps({"0": {"util": 1.0}}))]
    out = _gpu_series(rows, 1, (("util", "gpu_util_"),))
    assert out["gpu_util_0"]["values"] == [1.0]
    assert _gpu_series([], 1, (("util", "gpu_util_"),)) == {}


class FakeStore:
    def __init__(self):
        self.calls = []
        self.ranges = []

    def _rec(self, name, t0, t1, stride):
        self.calls.append((name, stride))
        self.ranges.append((t0, t1))

    def query_llama(self, host_id, t0, t1, stride=1):
        self._rec("llama", t0, t1, stride)
        return []

    def query_host(self, host_id, t0, t1, stride=1):
        self._rec("host", t0, t1, stride)
        return []

    def query_llama_1m(self, host_id, t0, t1, stride=1):
        self._rec("llama_1m", t0, t1, stride)
        return []

    def query_host_1m(self, host_id, t0, t1, stride=1):
        self._rec("host_1m", t0, t1, stride)
        return []


def test_history_stride_pushdown_all_windows():
    store = FakeStore()
    _history_from_store(store, "h1", 86400)  # 24h raw：stride=144（此前为 1 → 8.6 万行）
    assert ("llama", 144) in store.calls
    assert ("host", 144) in store.calls

    store2 = FakeStore()
    _history_from_store(store2, "h1", 7776000)  # 90d 1m：stride=12960
    assert ("llama_1m", 12960) in store2.calls
    assert ("host_1m", 12960) in store2.calls


def test_history_tier_selection():
    store = FakeStore()
    _history_from_store(store, "h1", 604800)  # 7d → raw 层
    assert ("llama", 1008) in store.calls
    assert not any(c[0].endswith("_1m") for c in store.calls)


def test_history_from_store_explicit_range():
    """自定义时间范围：t0/t1 显式传入时按给定范围查询（而非 now-window）。"""
    store = FakeStore()
    t0, t1 = 1_000_000, 1_000_000 + 7200
    out = _history_from_store(store, "h1", t1 - t0, t0=t0, t1=t1)
    assert out["window"] == 7200
    # 2h 范围走 raw 层，stride = 7200 // 600 = 12
    assert ("llama", 12) in store.calls
    assert ("host", 12) in store.calls
    # 查询范围必须是显式传入的 t0/t1（而非 now-window）
    assert store.ranges and all(r == (t0, t1) for r in store.ranges)
