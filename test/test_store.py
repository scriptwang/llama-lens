from backend.store import RingBuffer, downsample, insert_breaks


def test_ring_push_window_last():
    r = RingBuffer(maxlen=10)
    for i in range(20):
        r.push("s", float(i), i)
    assert len(r.get("s")) == 10
    assert r.last("s") == (19.0, 19)
    w = r.window("s", 5.0, 19.0)
    assert [v for _, v in w] == [14, 15, 16, 17, 18, 19]  # ts >= cutoff（含边界）
    assert r.names() == ["s"]
    assert r.last("missing") is None
    assert r.get("missing") == []


def test_ring_sink_errors_swallowed():
    def bad_sink(*a):
        raise RuntimeError("boom")
    r = RingBuffer(maxlen=5, sink=bad_sink)
    r.push("s", 1.0, 1)  # 持久化失败不影响实时链路
    calls = []
    r2 = RingBuffer(maxlen=5, sink=lambda n, t, v: calls.append((n, t, v)))
    r2.push("s", 1.0, 1)
    assert calls == [("s", 1.0, 1)]


def test_downsample_small_unchanged():
    pts = [(float(i), i) for i in range(100)]
    assert downsample(pts, 600) is pts


def test_downsample_large_reduced_keeps_ends():
    pts = [(float(i), i) for i in range(6000)]
    out = downsample(pts, 600)
    assert len(out) <= 601
    assert out[0] == pts[0]
    assert out[-1] == pts[-1]


def test_insert_breaks():
    pts = [(1.0, 1), (2.0, 2), (10.0, 3), (11.0, 4)]
    out = insert_breaks(pts, cadence=1.0, gap_factor=3.0)
    assert (10.0, None) in out
    assert len(out) == 5
    assert insert_breaks([], 1.0) == []
    assert insert_breaks([(1.0, 1)], 1.0) == [(1.0, 1)]
