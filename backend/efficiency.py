"""P1-2 效率统计：token 产出 + GPU 累计耗电 + tokens/瓦。

- GET /api/efficiency?host_id={mid}&range=24h|7d|30d  或  ?start={unix}&end={unix}（自定义，与历史趋势共用时间选择）
- 范围 ≤1 天用原始表（ts_llama @1s / ts_host @2s），>1 天用 1 分钟聚合表（更轻）。
- token 产出 = Σ gen_speed × Δt；耗电 = Σ power × Δt（各卡分别累计）。
"""
import json
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .api import _monitor
from .ctl.routers.auth import get_current_user

router = APIRouter(prefix="/api", tags=["efficiency"])

RANGES = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
DAY = 86400
J_PER_KWH = 3.6e6


def _bucket_series(points: Dict[int, float], bucket: int, t0: int, t1: int) -> Dict[str, list]:
    """稀疏桶 → 连续序列（缺失桶补 0）"""
    ts, values = [], []
    b = t0 - (t0 % bucket)
    while b < t1:
        ts.append(b)
        values.append(round(points.get(b, 0.0), 1))
        b += bucket
    return {"ts": ts, "values": values}


@router.get("/efficiency")
def efficiency(host_id: str, request: Request, range: str = Query(default="7d"),
               start: Optional[int] = None, end: Optional[int] = None,
               user: str = Depends(get_current_user)):
    # 时间范围：start/end（自定义，与历史趋势共用）优先；否则固定档位
    now = int(time.time())
    if start is not None and end is not None:
        t0 = int(start)
        t1 = min(int(end), now)
        if t1 - t0 < 60:
            raise HTTPException(status_code=400, detail="时间范围太短（至少 1 分钟）")
        if t1 - t0 > 90 * DAY:
            raise HTTPException(status_code=400, detail="时间范围太长（最多 90 天，受历史保留期限制）")
        if t0 < now - 90 * DAY:
            t0 = now - 90 * DAY
        range_name = "custom"
    else:
        if range not in RANGES:
            range = "7d"
        t0 = now - RANGES[range]
        t1 = now
        range_name = range
    span = t1 - t0

    mon = _monitor(request, host_id)
    store = request.app.state.history
    if store is None:
        return {"range": range_name, "start": t0, "end": t1, "available": False, "reason": "历史存储未启用"}

    use_1m = span > DAY
    dt = 60 if use_1m else 1          # llama 采样间隔（1m 表为 60s，原始表 1s）
    dt_host = 60 if use_1m else 2     # host 采样间隔（1m 表为 60s，原始表 2s）

    ll = (store.query_llama_1m(host_id, t0, t1) if use_1m
          else store.query_llama(host_id, t0, t1))
    hh = (store.query_host_1m(host_id, t0, t1) if use_1m
          else store.query_host(host_id, t0, t1))

    # ---- token 产出：gen_speed (tok/s) × Δt，按天/按桶累计 ----
    tokens_total = 0.0
    day_tokens: Dict[int, float] = {}
    bucket_tokens: Dict[int, float] = {}
    bucket = DAY if use_1m else 3600
    for r in ll:
        ts, gen = r[0], r[1]
        if gen is None:
            continue
        v = float(gen) * dt
        tokens_total += v
        day = ts - (ts % DAY)
        day_tokens[day] = day_tokens.get(day, 0.0) + v
        b = ts - (ts % bucket)
        bucket_tokens[b] = bucket_tokens.get(b, 0.0) + v

    # ---- GPU 耗电：power (W) × Δt，按卡累计 ----
    energy_j: Dict[str, float] = {}
    bucket_power: Dict[int, float] = {}
    gpu_idx = 7 if use_1m else 11  # query 结果列序（不含 host_id）
    for r in hh:
        ts, gpu_json = r[0], r[gpu_idx]
        if not gpu_json:
            continue
        try:
            data = json.loads(gpu_json)
        except (ValueError, TypeError):
            continue
        pkey = "power_avg" if use_1m else "power"
        total_w = 0.0
        for idx, g in data.items():
            p = g.get(pkey)
            if p is None:
                continue
            j = float(p) * dt_host
            energy_j[idx] = energy_j.get(idx, 0.0) + j
            total_w += float(p)
        if total_w > 0:
            b = ts - (ts % bucket)
            bucket_power[b] = bucket_power.get(b, 0.0) + total_w

    energy_kwh = sum(energy_j.values()) / J_PER_KWH
    # GPU 名称：取监控快照（无则用索引）
    gpu_names = {}
    try:
        for g in (mon.snapshot().get("host_metrics") or {}).get("gpus") or []:
            gpu_names[str(g.get("index", 0))] = g.get("name") or ""
    except Exception:
        pass
    gpus = []
    for idx in sorted(energy_j, key=lambda x: int(x) if str(x).isdigit() else 0):
        gpus.append({
            "index": int(idx) if str(idx).isdigit() else idx,
            "name": gpu_names.get(idx, ""),
            "kwh": round(energy_j[idx] / J_PER_KWH, 3),
        })

    # ---- 效率与费用 ----
    tokens_per_wh = (tokens_total / (energy_kwh * 1000)) if energy_kwh > 0 else None
    price = 0.0
    try:
        price = float(request.app.state.registry.app_cfg.global_cfg.electricity_price or 0.0)
    except Exception:
        pass
    days = max(1, span // DAY)
    out: Dict[str, Any] = {
        "range": range_name,
        "start": t0,
        "end": t1,
        "available": True,
        "tokens_total": int(tokens_total),
        "tokens_per_day": int(tokens_total / days),
        "energy_kwh": round(energy_kwh, 3),
        "gpus": gpus,
        "tokens_per_wh": round(tokens_per_wh, 1) if tokens_per_wh is not None else None,
        "electricity_price": price,
        "cost": round(energy_kwh * price, 2) if price > 0 else None,
        "series": {
            "bucket": bucket,
            "tokens": _bucket_series(bucket_tokens, bucket, t0, t1),
            "power_w": _bucket_series(bucket_power, bucket, t0, t1),
        },
    }
    return out
