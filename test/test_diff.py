from backend.diff import DiffEngine


def test_cpu_pct_first_none_then_value():
    d = DiffEngine()
    assert d.cpu_pct("k", 1.0, 100, 50) is None
    # total 100→200 (Δ100)，idle 50→70 (Δ20) → 80%
    assert d.cpu_pct("k", 2.0, 200, 70) == 80.0


def test_cpu_pct_clamped_and_invalid():
    d = DiffEngine()
    d.cpu_pct("k", 1.0, 100, 100)
    # idle 回退（计数器异常）→ 钳制 100
    assert d.cpu_pct("k", 2.0, 200, 90) == 100.0
    # Δtotal <= 0 → None
    assert d.cpu_pct("k", 3.0, 200, 90) is None


def test_process_cpu_pct():
    d = DiffEngine()
    assert d.process_cpu_pct("p", 1.0, 0) is None
    # 1s 内 100 ticks（CLK_TCK=100）= 单核 100%
    assert d.process_cpu_pct("p", 2.0, 100) == 100.0


def test_bytes_rate():
    d = DiffEngine()
    assert d.bytes_rate("b", 1.0, 1000) is None
    assert d.bytes_rate("b", 3.0, 2000) == 500.0


def test_prune_keeps_only_listed_pids():
    d = DiffEngine()
    d.cpu_pct("ps:h:1", 1.0, 10, 5)
    d.cpu_pct("ps:h:2", 1.0, 10, 5)
    d.cpu_pct("other:1", 1.0, 10, 5)
    d.prune("ps:h:", {1})
    assert "ps:h:1" in d._prev
    assert "ps:h:2" not in d._prev
    assert "other:1" in d._prev
