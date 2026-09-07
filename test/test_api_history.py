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

    def query_llama(self, host_id, t0, t1, stride=1):
        self.calls.append(("llama", stride))
        return []

    def query_host(self, host_id, t0, t1, stride=1):
        self.calls.append(("host", stride))
        return []

    def query_llama_1m(self, host_id, t0, t1, stride=1):
        self.calls.append(("llama_1m", stride))
        return []

    def query_host_1m(self, host_id, t0, t1, stride=1):
        self.calls.append(("host_1m", stride))
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
