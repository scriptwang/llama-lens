"""P2-1 一键体检：markdown 报告生成（纯函数）。"""
from backend.ctl.routers.diagnostic import _build_markdown


def _row():
    return {
        "id": 4, "alias": "ai", "host": "10.0.0.28", "port": 22,
        "log_source": "journal", "log_unit": "llama-server", "log_path": None,
        "systemd_unit": "llama-server.service",
    }


def _snap():
    return {
        "llama": {"online": True, "gen_speed_tps": 45.2,
                  "model": {"name": "Qwen3.8-27B-Q6_K.gguf"}},
        "host_metrics": {
            "reachable": True,
            "cpu": {"usage_pct": 12.5, "load": [0.5, 0.4, 0.3]},
            "mem": {"total_mb": 32000, "used_mb": 13000, "available_mb": 19000},
            "disk": {"mounts": [{"mount": "/", "size_gb": 476.0, "used_gb": 210.0,
                                 "avail_gb": 266.0, "use_pct": 44.0}]},
            "net": {"ifaces": [{"name": "eth0", "rx_mb_s": 1.2, "tx_mb_s": 0.3}]},
            "gpus": [{"index": 0, "name": "RTX 3080", "util_pct": 85.0,
                      "mem_used_mb": 18800, "mem_total_mb": 20480,
                      "temp_c": 72.0, "power_w": 280.0}],
        },
        "alerts": [{"level": "warn", "metric": "gpu_temp", "value": 72, "threshold": 70}],
    }


def test_markdown_full():
    services = [{"name": "llama-server.service", "active_state": "active",
                 "sub_state": "running", "unit_file_state": "enabled"}]
    events = [{"ts": 1725600000, "level": "info", "type": "llama_up", "msg": "llama 上线"}]
    md = _build_markdown(_row(), _snap(), services, events, ["E some error"])
    for expected in ("# LLMLens 体检报告 — ai (10.0.0.28:22)",
                     "## 总体状态", "引擎：在线", "SSH：已连接",
                     "## 系统", "CPU：12%", "内存：12.7 GiB / 31.2 GiB",
                     "磁盘 /：44% 已用", "网络：rx 1.2 MB/s / tx 0.3 MB/s",
                     "## GPU", "RTX 3080", "85%", "72°C", "280 W",
                     "## 服务", "llama-server.service | active (running) | enabled",
                     "## 引擎 API", "Qwen3.8-27B-Q6_K.gguf", "45.2 tok/s",
                     "## 最近事件（1）", "llama 上线",
                     "## 最近错误日志（1）", "E some error"):
        assert expected in md, "缺少: %s" % expected


def test_markdown_no_snapshot():
    md = _build_markdown(_row(), None, [], [], [])
    assert "监控未启用" in md
    assert "无 GPU 数据" in md
    assert "未扫描到服务" in md
    assert "## 最近事件（0）" in md
    assert "## 最近错误日志（0）" in md


def test_markdown_alerts_listed():
    md = _build_markdown(_row(), _snap(), [], [], [])
    assert "[warn] gpu_temp：72 >= 70" in md
