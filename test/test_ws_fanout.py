"""ws fanout：_host_hub 复用 + 主机 WS 端点广播快照（多客户端共享一次序列化）。"""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ws as ws_mod
from backend.ws import _host_hub


class _FakeMonitor:
    def __init__(self):
        self._ws_hub = None
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {"ts": 1, "host": {"id": "h1", "name": "h1"}, "llama": {"online": True}}


def test_host_hub_reuses_same_hub():
    mon = _FakeMonitor()
    h1 = _host_hub(mon, 1.0)
    h2 = _host_hub(mon, 1.0)
    assert h1 is h2
    assert mon._ws_hub is h1


def test_fanout_close_idempotent():
    mon = _FakeMonitor()
    hub = _host_hub(mon, 1.0)
    assert hub._task is None  # 无客户端时任务未创建
    hub.close()
    assert hub._task is None
    hub.close()  # 幂等


def test_ws_host_broadcasts_snapshot(monkeypatch):
    monkeypatch.setattr(ws_mod.ctl_settings, "auth_enabled", False)
    mon = _FakeMonitor()
    reg = SimpleNamespace(
        get=lambda mid: mon,
        app_cfg=SimpleNamespace(global_cfg=SimpleNamespace(push_interval=0.05)),
    )
    app = FastAPI()
    app.include_router(ws_mod.router)
    app.state.registry = reg
    with TestClient(app) as client:
        with client.websocket_connect("/ws/hosts/h1") as ws:
            data = ws.receive_json()
            assert data["host"]["id"] == "h1"
            assert data["llama"]["online"] is True
    # 断开后 hub 仍在 monitor 上，close 应可安全调用
    mon._ws_hub.close()


def test_ws_host_unknown_host_closes(monkeypatch):
    monkeypatch.setattr(ws_mod.ctl_settings, "auth_enabled", False)
    reg = SimpleNamespace(
        get=lambda mid: None,
        app_cfg=SimpleNamespace(global_cfg=SimpleNamespace(push_interval=0.05)),
    )
    app = FastAPI()
    app.include_router(ws_mod.router)
    app.state.registry = reg
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws/hosts/nope"):
                pass
            assert False, "应因主机不存在而关闭"
        except Exception:
            pass  # 服务端 close(4004) → 客户端抛异常
