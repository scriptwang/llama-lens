"""阈值引擎：每次快照生成时评估，产出 alerts[]（metric/level/value/threshold）。

- 默认规则见 config.DEFAULT_THRESHOLDS；mtp 为“低于阈值告警”（inverted）。
- 前端只渲染：danger → 红，warn → 黄。兼容 Python 3.9。
"""
from typing import Any, Dict, List, Optional

from .config import INVERTED_METRICS


def evaluate_alerts(thresholds: Dict[str, Dict[str, float]],
                    engine: Dict[str, Any],
                    host_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []

    def add(metric: str, level: str, value: Any, threshold: Any) -> None:
        alerts.append({"metric": metric, "level": level, "value": value, "threshold": threshold})

    def check(metric: str, value: Optional[float], key: str) -> None:
        if value is None:
            return
        t = thresholds.get(key) or {}
        warn, danger = t.get("warn"), t.get("danger")
        if key in INVERTED_METRICS:
            if danger is not None and value < danger:
                add(metric, "danger", value, danger)
            elif warn is not None and value < warn:
                add(metric, "warn", value, warn)
        else:
            if danger is not None and value >= danger:
                add(metric, "danger", value, danger)
            elif warn is not None and value >= warn:
                add(metric, "warn", value, warn)

    # 服务级
    etype = engine.get("type", "llama_cpp")
    if not engine.get("online"):
        add("llama" if etype == "llama_cpp" else "engine", "danger", 0, 1)
    if not host_metrics.get("reachable"):
        add("ssh", "warn", 0, 1)

    # GPU（按卡）
    for g in host_metrics.get("gpus") or []:
        idx = g.get("index", 0)
        check("gpu%d.util" % idx, g.get("util_pct"), "gpu_util")
        if g.get("mem_total_mb"):
            check("gpu%d.mem" % idx, round(g["mem_used_mb"] / g["mem_total_mb"] * 100.0, 1), "gpu_mem")
        check("gpu%d.temp" % idx, g.get("temp_c"), "gpu_temp")
        if g.get("power_limit_w"):
            check("gpu%d.power" % idx, round(g["power_w"] / g["power_limit_w"] * 100.0, 1), "gpu_power")

    # CPU / 内存 / 磁盘
    check("cpu", (host_metrics.get("cpu") or {}).get("usage_pct"), "cpu")
    mem = host_metrics.get("mem") or {}
    if mem.get("total_mb"):
        check("mem", round(mem["used_mb"] / mem["total_mb"] * 100.0, 1), "mem")
    for mnt in (host_metrics.get("disk") or {}).get("mounts") or []:
        check("disk:%s" % mnt.get("mount"), mnt.get("use_pct"), "disk")

    # 上下文（llama: 日志合并后的 ctx；sglang: KV cache 占用）
    check("ctx", (engine.get("ctx") or {}).get("pct"), "ctx")

    # MTP 接受率（仅 llama.cpp 日志源）
    mtp = (engine.get("log") or {}).get("mtp") or {}
    if mtp.get("acceptance") is not None:
        check("mtp", round(mtp["acceptance"] * 100.0, 1), "mtp")

    # 请求队列（仅 SGLang）
    if etype == "sglang":
        check("queue", (engine.get("requests") or {}).get("waiting"), "queue")

    return alerts
