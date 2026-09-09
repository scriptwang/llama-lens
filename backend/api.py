"""REST 路由（架构文档 §6.1）。"""
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .ctl.routers.auth import get_current_user
from .monitor import MonitorRegistry
from .store import downsample

router = APIRouter(prefix="/api")

# ≤3600 走内存环形缓冲；≤604800 走 SQLite 原始层；更长走 1m 聚合层
VALID_WINDOWS = (300, 900, 3600, 14400, 86400, 604800, 2592000, 7776000)
MEMORY_MAX_WINDOW = 3600
RAW_MAX_WINDOW = 604800

_LLAMA_COLS = {"gen_speed": 1, "prompt_speed": 2, "ctx_used": 3, "mtp_acceptance": 4}
_HOST_COLS = {"cpu": 1, "mem_used": 2, "mem_buff_cache": 3, "swap_used": 4,
              "net_rx": 5, "net_tx": 6, "proc_cpu": 7,
              "load_1": 8, "load_5": 9, "load_15": 10}
_LLAMA_1M_COLS = {"gen_speed": 1, "prompt_speed": 3, "ctx_used": 4, "mtp_acceptance": 5}
_HOST_1M_COLS = {"cpu": 1, "mem_used": 2, "swap_used": 3,
                 "net_rx": 4, "net_tx": 5, "proc_cpu": 6}
_GPU_KEYS = (("util", "gpu_util_"), ("mem", "gpu_mem_"), ("temp", "gpu_temp_"),
             ("power", "gpu_power_"))
_GPU_1M_KEYS = (("util_avg", "gpu_util_"), ("mem_max", "gpu_mem_"),
                ("temp_max", "gpu_temp_"), ("power_avg", "gpu_power_"))


def _series_from_rows(rows: List[tuple], colmap: Dict[str, int]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, idx in colmap.items():
        pts = downsample([(r[0], r[idx]) for r in rows], 600)
        if pts:
            out[name] = {"ts": [t for t, _ in pts], "values": [v for _, v in pts]}
    return out


def _gpu_series(rows: List[tuple], json_idx: int, keys) -> Dict[str, Any]:
    acc: Dict[str, List] = {}
    for r in rows:
        raw = r[json_idx]
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for idx, g in data.items():
            for key, prefix in keys:
                v = g.get(key)
                if v is not None:
                    acc.setdefault(prefix + idx, []).append((r[0], v))
    # 与标量序列一致：降采样到 600 点（24h 原始层 8.6 万行 / 90d 聚合层 13 万行，
    # 不降采样会产生数 MB 的 JSON 并拖慢前端渲染）
    out: Dict[str, Any] = {}
    for name, pts in acc.items():
        pts = downsample(pts, 600)
        out[name] = {"ts": [t for t, _ in pts], "values": [v for _, v in pts]}
    return out


def _history_from_store(store, host_id: str, window: int,
                        t0: Optional[int] = None, t1: Optional[int] = None) -> Dict[str, Any]:
    now = int(time.time())
    if t1 is None:
        t1 = now
    if t0 is None:
        t0 = t1 - window
    series: Dict[str, Any] = {}
    if window <= RAW_MAX_WINDOW:
        tier = "raw"
        # 步长下推到 SQL：任意窗口都只取 ~600 行（24h 窗口从 8.6 万行降到 600 行）
        stride = max(1, window // 600)
        series.update(_series_from_rows(store.query_llama(host_id, t0, t1, stride), _LLAMA_COLS))
        hrows = store.query_host(host_id, t0, t1, stride)
        series.update(_series_from_rows(hrows, _HOST_COLS))
        series.update(_gpu_series(hrows, 11, _GPU_KEYS))
    else:
        tier = "1m"
        stride = max(1, window // 600)
        series.update(_series_from_rows(store.query_llama_1m(host_id, t0, t1, stride), _LLAMA_1M_COLS))
        hrows = store.query_host_1m(host_id, t0, t1, stride)
        series.update(_series_from_rows(hrows, _HOST_1M_COLS))
        series.update(_gpu_series(hrows, 7, _GPU_1M_KEYS))
    return {"window": window, "tier": tier, "series": series}


def _registry(request: Request) -> MonitorRegistry:
    return request.app.state.registry


def _monitor(request: Request, host_id: str):
    m = _registry(request).get(host_id)
    if m is None:
        raise HTTPException(status_code=404, detail="unknown host: %s" % host_id)
    return m


@router.get("/health")
async def panel_health(request: Request):
    # 公开端点：Docker 健康检查与面板存活探测用，不含敏感信息
    reg = _registry(request)
    hosts = {}
    for mid, m in reg.monitors.items():
        snap = m.snapshot()
        hosts[mid] = {
            "llama_online": snap["llama"]["online"],
            "ssh_ok": snap["host_metrics"].get("reachable", False),
        }
    return {"status": "ok", "hosts": hosts}


@router.get("/hosts/{host_id}/overview")
async def host_overview(request: Request, host_id: str,
                        user: str = Depends(get_current_user)):
    return _monitor(request, host_id).snapshot()


@router.get("/hosts/{host_id}/tasks")
async def host_tasks(request: Request, host_id: str,
                     limit: int = Query(default=100, ge=1, le=200),
                     user: str = Depends(get_current_user)):
    """请求历史：llama-server 已完成任务的明细（时间/槽位/prompt/生成/总耗时），新→旧。"""
    mon = _monitor(request, host_id)
    hist = list(mon.log_poller.state.get("task_history") or [])
    hist = hist[-limit:]
    hist.reverse()
    return {"host_id": host_id,
            "log_available": bool(mon.log_poller.state.get("available")),
            "tasks": hist}


@router.get("/hosts/{host_id}/history")
async def host_history(request: Request, host_id: str,
                       window: int = Query(default=300),
                       start: Optional[int] = None,
                       end: Optional[int] = None,
                       user: str = Depends(get_current_user)):
    if start is not None and end is not None:
        # 自定义时间范围（查看指定历史时段）：必须走历史存储
        store = request.app.state.history
        if store is None:
            raise HTTPException(status_code=400,
                                 detail="自定义时间范围需要启用历史存储")
        now = int(time.time())
        t0 = int(start)
        t1 = min(int(end), now)
        if t1 - t0 < 60:
            raise HTTPException(status_code=400, detail="时间范围太短（至少 1 分钟）")
        if t1 - t0 > 90 * 86400:
            raise HTTPException(status_code=400, detail="时间范围太长（最多 90 天，受历史保留期限制）")
        if t0 < now - 90 * 86400:
            t0 = now - 90 * 86400
        return _history_from_store(store, host_id, t1 - t0, t0=t0, t1=t1)
    if window not in VALID_WINDOWS:
        window = 300
    if window <= MEMORY_MAX_WINDOW:
        return _monitor(request, host_id).history(window)
    store = request.app.state.history
    if store is None:
        raise HTTPException(status_code=400,
                             detail="长窗口历史不可用（历史存储未启用），最大窗口 3600")
    return _history_from_store(store, host_id, window)


@router.get("/hosts/{host_id}/events")
async def host_events(request: Request, host_id: str,
                      limit: int = Query(default=50, ge=1, le=200),
                      user: str = Depends(get_current_user)):
    mon = _monitor(request, host_id)
    mem = mon.events_list(limit)
    store = request.app.state.history
    if store is None:
        return mem
    try:
        db_ev = store.query_events(host_id, limit)
    except Exception:
        return mem
    # DB 是超集（含已刷盘的内存事件），内存里可能有未刷盘的新事件 → 合并去重
    seen = set()
    merged = []
    for ev in db_ev + mem:
        key = (ev["ts"], ev["type"], ev["msg"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(ev)
    merged.sort(key=lambda e: e["ts"])
    return merged[-limit:]
