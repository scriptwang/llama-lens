"""REST 路由（架构文档 §6.1）。"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from .monitor import MonitorRegistry

router = APIRouter(prefix="/api")

VALID_WINDOWS = (300, 900, 3600)


def _registry(request: Request) -> MonitorRegistry:
    return request.app.state.registry


def _monitor(request: Request, host_id: str):
    m = _registry(request).get(host_id)
    if m is None:
        raise HTTPException(status_code=404, detail="unknown host: %s" % host_id)
    return m


@router.get("/health")
async def panel_health(request: Request):
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
async def host_overview(request: Request, host_id: str):
    return _monitor(request, host_id).snapshot()


@router.get("/hosts/{host_id}/history")
async def host_history(request: Request, host_id: str,
                       window: int = Query(default=300)):
    if window not in VALID_WINDOWS:
        window = 300
    return _monitor(request, host_id).history(window)


@router.get("/hosts/{host_id}/events")
async def host_events(request: Request, host_id: str,
                      limit: int = Query(default=50, ge=1, le=200)):
    return _monitor(request, host_id).events_list(limit)
