import json
import time

from backend.history import HistoryStore, HistoryWriter


def test_store_write_query(tmp_path):
    store = HistoryStore(str(tmp_path / "h.db"))
    store.write_llama([("h1", 1000, 10.0, 20.0, 100, None, None),
                       ("h1", 1001, 11.0, 21.0, 101, 0.9, 3.5)])
    store.write_host([("h1", 1000, 50.0, 1000.0, 100.0, 5.0, 1.0, 2.0,
                       0.5, 0.4, 0.3, 0.2, json.dumps({"0": {"util": 90.0}}))])
    assert len(store.query_llama("h1", 999, 1002)) == 2
    hrows = store.query_host("h1", 999, 1002)
    assert len(hrows) == 1
    assert json.loads(hrows[0][11])["0"]["util"] == 90.0
    # stride 下推
    for ts in range(1000, 1100, 2):
        store.write_host([("h1", ts, 1.0, None, None, None, None, None,
                           None, None, None, None, None)])
    assert len(store.query_host("h1", 1000, 1100, 10)) <= 11
    store.close()


def test_store_events_and_cleanup(tmp_path):
    store = HistoryStore(str(tmp_path / "h.db"))
    store.write_event("h1", 1000.0, "info", "llama_up", "llama 上线")
    evs = store.query_events("h1")
    assert evs[0]["msg"] == "llama 上线"
    store.cleanup(raw_cutoff=2000, agg_cutoff=2000, events_cutoff=2000)
    assert store.query_events("h1") == []
    store.close()


def test_aggregate_minute(tmp_path):
    store = HistoryStore(str(tmp_path / "h.db"))
    m = 1800000000  # 分钟边界
    for i in range(60):
        store.write_llama([("h1", m + i, 10.0 + i, 20.0, 100 + i, None, 3.5 + i)])
        store.write_host([("h1", m + i, 50.0, 1000.0 + i, None, 1.0, 1.0,
                           2.0, None, None, None, None, None)])
    store.aggregate_minute("h1", m)
    ll = store.query_llama_1m("h1", m, m + 60)
    assert len(ll) == 1
    # 列序: ts, gen_avg, gen_max, prompt_avg, ctx_max, mtp_avg
    assert abs(ll[0][1] - 39.5) < 0.01   # gen_avg = mean(10..69)
    assert ll[0][2] == 69.0              # gen_max
    hh = store.query_host_1m("h1", m, m + 60)
    assert len(hh) == 1
    # 列序: ts, cpu_avg, mem_used_max, swap_used_max, net_rx_sum, net_tx_sum, proc_cpu_avg, gpu
    assert hh[0][1] == 50.0              # cpu_avg
    assert hh[0][2] == 1059.0            # mem_used_max
    assert hh[0][3] == 1.0               # swap_used_max
    assert hh[0][4] == 60.0              # net_rx_sum = 60 × 1.0
    # 空分钟不产生行
    store.aggregate_minute("h1", m + 60)
    assert len(store.query_llama_1m("h1", m + 60, m + 120)) == 0
    store.close()


def test_1m_query_stride(tmp_path):
    store = HistoryStore(str(tmp_path / "h.db"))
    for i in range(120):
        store.write_host_1m([("h1", 1800000000 + i * 60, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, None)])
    assert len(store.query_host_1m("h1", 1800000000, 1800007200)) == 120
    assert len(store.query_host_1m("h1", 1800000000, 1800007200, 120)) == 60  # ts%120==0 → 偶数 i
    store.close()


def test_writer_flush_and_close(tmp_path):
    store = HistoryStore(str(tmp_path / "h.db"))
    w = HistoryWriter(store, flush_interval=1.0, raw_days=7, agg_days=90, events_days=30)
    now = time.time()
    w.enqueue_llama("h1", now, "gen_speed", 42.0)
    w.enqueue_host("h1", now, "cpu", 55.0)
    w.enqueue_host("h1", now, "gpu_util_0", 90.0)
    w.enqueue_event("h1", {"ts": now, "level": "info", "type": "t", "msg": "m"})
    w.close()  # join + 最终刷盘
    t0, t1 = int(now) - 5, int(now) + 5
    assert len(store.query_llama("h1", t0, t1)) == 1
    hrows = store.query_host("h1", t0, t1)
    assert len(hrows) == 1
    assert json.loads(hrows[0][11])["0"]["util"] == 90.0
    assert len(store.query_events("h1")) == 1
    store.close()
