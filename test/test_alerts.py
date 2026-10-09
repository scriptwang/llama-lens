from backend.alerts import evaluate_alerts
from backend.config import DEFAULT_THRESHOLDS


def _llama(online=True):
    return {"type": "llama_cpp", "online": online, "model": {}, "ctx": {}, "log": {}}


def _hm(reachable=True, gpus=None, cpu=None, mem=None, disk=None):
    return {
        "reachable": reachable,
        "gpus": gpus or [],
        "cpu": {"usage_pct": cpu},
        "mem": mem or {},
        "disk": {"mounts": disk or []},
    }


def test_offline_alerts():
    a = evaluate_alerts(DEFAULT_THRESHOLDS, _llama(False), _hm(False))
    m = {x["metric"]: x["level"] for x in a}
    assert m["llama"] == "danger"
    assert m["ssh"] == "warn"


def test_gpu_thresholds():
    gpus = [{"index": 0, "util_pct": 95.0, "mem_used_mb": 960, "mem_total_mb": 1000,
             "temp_c": 88.0, "power_w": 320.0, "power_limit_w": 350.0}]
    a = evaluate_alerts(DEFAULT_THRESHOLDS, _llama(), _hm(gpus=gpus))
    m = {x["metric"]: x["level"] for x in a}
    assert m["gpu0.util"] == "danger"    # 95 >= 90
    assert m["gpu0.mem"] == "danger"     # 96% >= 95
    assert m["gpu0.temp"] == "danger"    # 88 >= 85
    assert m["gpu0.power"] == "warn"     # 91.4% >= 85, < 95


def test_cpu_mem_disk():
    a = evaluate_alerts(DEFAULT_THRESHOLDS, _llama(), _hm(cpu=85.0, mem={"total_mb": 100, "used_mb": 90},
                                          disk=[{"mount": "/", "use_pct": 82.0}]))
    m = {x["metric"]: x["level"] for x in a}
    assert m["cpu"] == "warn"
    assert m["mem"] == "warn"
    assert m["disk:/"] == "warn"


def test_mtp_inverted_and_ctx():
    eng = _llama()
    eng["ctx"] = {"pct": 85.0}
    eng["log"] = {"mtp": {"acceptance": 0.60}}
    a = evaluate_alerts(DEFAULT_THRESHOLDS, eng, _hm())
    m = {x["metric"]: x["level"] for x in a}
    assert m["mtp"] == "danger"   # 60% < 65（低于阈值告警）
    assert m["ctx"] == "warn"     # 85% >= 80
