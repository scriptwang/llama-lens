"""WebSocket 路由（架构文档 §6.2）。

- /ws/hosts/{id}：每 push_interval（默认 1s）推送快照；ping/pong 心跳；30s 无心跳断开。
- /ws/portal：每 push_interval 推送 /api/hosts 数据；单生产者多消费者广播
  （每间隔只计算/序列化一次，扇出给所有客户端）。
"""
import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .ctl.config import settings as ctl_settings
from .ctl.security import decode_token

log = logging.getLogger("llamalens.ws")

router = APIRouter()

PING_TIMEOUT = 30.0


def _ws_auth(ws: WebSocket) -> bool:
    """WS 鉴权：auth 关闭放行；否则校验 query 参数 token。"""
    if not ctl_settings.auth_enabled:
        return True
    token = ws.query_params.get("token", "")
    if not token:
        return False
    try:
        decode_token(token)
        return True
    except Exception:
        return False


class _Fanout:
    """单生产者多消费者：每 interval 计算一次 payload，广播给全部客户端。"""

    def __init__(self, payload_fn, interval: float):
        self.payload_fn = payload_fn
        self.interval = interval
        self.clients = set()
        self._task: Optional[asyncio.Task] = None

    def add(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def remove(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def _run(self) -> None:
        while True:
            if self.clients:
                try:
                    payload = json.dumps(self.payload_fn())
                except Exception:
                    log.exception("portal payload 生成失败")
                    payload = None
                if payload is not None:
                    dead = []
                    for ws in list(self.clients):
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self.clients.discard(ws)
            await asyncio.sleep(self.interval)


async def _push_loop(ws: WebSocket, payload_fn, interval: float) -> None:
    while True:
        try:
            await ws.send_json(payload_fn())
        except Exception:
            return
        await asyncio.sleep(interval)


async def _recv_loop(ws: WebSocket) -> None:
    """处理客户端消息（ping）；心跳超时或断线时退出。"""
    last_ping = time.time()
    while True:
        try:
            msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        except asyncio.TimeoutError:
            if time.time() - last_ping > PING_TIMEOUT:
                log.info("WS 心跳超时（%.0fs 无 ping），断开", PING_TIMEOUT)
                return
            continue
        try:
            data = json.loads(msg)
        except (ValueError, TypeError):
            continue
        if data.get("type") == "ping":
            last_ping = time.time()
            try:
                await ws.send_json({"type": "pong"})
            except Exception:
                return


@router.websocket("/ws/hosts/{host_id}")
async def ws_host(ws: WebSocket, host_id: str):
    if not _ws_auth(ws):
        await ws.close(code=4401)
        return
    registry = ws.app.state.registry
    monitor = registry.get(host_id)
    await ws.accept()
    if monitor is None:
        await ws.close(code=4004)
        return
    interval = registry.app_cfg.global_cfg.push_interval
    sender = asyncio.create_task(_push_loop(ws, monitor.snapshot, interval))
    try:
        await _recv_loop(ws)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        try:
            # 对端已死时 close 握手会一直等（websockets close_timeout 默认 None），限时 5s
            await asyncio.wait_for(ws.close(), timeout=5.0)
        except Exception:
            pass


@router.websocket("/ws/portal")
async def ws_portal(ws: WebSocket):
    if not _ws_auth(ws):
        await ws.close(code=4401)
        return
    registry = ws.app.state.registry
    await ws.accept()
    hub = getattr(registry, "_portal_hub", None)
    if hub is None:
        hub = _Fanout(registry.list, registry.app_cfg.global_cfg.push_interval)
        registry._portal_hub = hub
    hub.add(ws)
    try:
        await _recv_loop(ws)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(ws)
        try:
            # 对端已死时 close 握手会一直等（websockets close_timeout 默认 None），限时 5s
            await asyncio.wait_for(ws.close(), timeout=5.0)
        except Exception:
            pass
