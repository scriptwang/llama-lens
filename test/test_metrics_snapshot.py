from backend.ctl.routers.metrics import _metrics_from_snapshot, _parse_etime


def test_parse_etime():
    assert _parse_etime("01:02:03") == 3723
    assert _parse_etime("1-02:03:04") == 93784
    assert _parse_etime("00:00:05") == 5
    assert _parse_etime("") is None
    assert _parse_etime(None) is None
    assert _parse_etime("garbage") is None


def test_metrics_from_snapshot_mapping():
    hm = {
        "reachable": True,
        "cpu": {"usage_pct": 42.5, "cores": 16, "load": [1.0, 1.1, 1.2]},
        "mem": {"total_mb": 32000, "used_mb": 16000},
        "gpus": [{"index": 0, "name": "RTX 3080", "util_pct": 90.0, "mem_used_mb": 8000,
                  "mem_total_mb": 10240, "power_w": 250.0, "power_limit_w": 350.0,
                  "temp_c": 70.0}],
        "process": {"found": True, "pid": 1234, "cpu_pct_realtime": 55.0,
                    "rss_mb": 8000, "elapsed": "01:02:03"},
        "service": {"unit": "llama-server.service"},
    }
    out = _metrics_from_snapshot(hm)
    assert out["cpu"]["percent"] == 42.5
    assert out["cpu"]["cores"] == 16
    assert out["memory"]["percent"] == 50.0
    assert out["gpu"]["vendor"] == "nvidia"
    assert out["gpu"]["devices"][0]["util_percent"] == 90.0
    svc = out["services"]["llama-server.service"]
    assert svc["pid"] == 1234
    assert svc["mem_mb"] == 8000
    assert svc["uptime_sec"] == 3723


def test_metrics_from_snapshot_no_gpu_no_process():
    out = _metrics_from_snapshot({
        "cpu": {"usage_pct": 1.0, "cores": 4, "load": []},
        "mem": {"total_mb": 0, "used_mb": None},
        "gpus": [],
        "process": {"found": False},
        "service": {"unit": "llama-server.service"},
    })
    assert out["gpu"]["vendor"] is None
    assert out["gpu"]["devices"] == []
    assert out["memory"]["percent"] is None
    assert out["services"] == {}
