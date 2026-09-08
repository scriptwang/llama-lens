"""services_state 端点：优先复用监控快照（零额外 SSH），无监控/无数据时回退 SSH。"""
from types import SimpleNamespace

from backend.ctl.routers import services as svc


def _request_with_monitor(mon):
    reg = SimpleNamespace(get=lambda mid: mon)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(registry=reg)))


def test_services_state_from_snapshot_no_ssh(monkeypatch):
    """监控快照有 all_services → 直接返回，绝不走 SSH。"""
    mon = SimpleNamespace(ssh_poller=SimpleNamespace(metrics={"all_services": {
        "llama.service": {"active_state": "active", "sub_state": "running"},
        "sshd.service": {"active_state": "inactive", "sub_state": "dead"},
    }}))
    request = _request_with_monitor(mon)
    monkeypatch.setattr(svc, "get_host_row", lambda host_id: {"mid": "h1"})

    def _boom(*a, **k):
        raise AssertionError("不应走 SSH（应复用监控快照）")
    monkeypatch.setattr(svc.pool, "checkout", _boom)

    out = svc.services_state(request, 1, "llama.service,sshd.service,unknown.service", user="t")
    data = out["data"]
    assert data["llama.service"] == {"active_state": "active", "sub_state": "running"}
    assert data["sshd.service"]["active_state"] == "inactive"
    assert "unknown.service" not in data  # 快照里没有的不出现


def test_services_state_empty_names():
    out = svc.services_state(SimpleNamespace(), 1, "", user="t")
    assert out["data"] == {}


def test_services_state_no_monitor_falls_back_to_ssh(monkeypatch):
    """无监控（registry=None）→ 回退 SSH 批量查询。"""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(registry=None)))
    monkeypatch.setattr(svc, "get_host_row", lambda host_id: {"mid": "h1"})
    monkeypatch.setattr(svc, "host_to_conn_dict", lambda row: {"id": 1, "host": "h", "port": 22,
                                                               "username": "u", "password": "p"})

    captured = {}

    class _FakeClient:
        pass

    def _fake_checkout(conn):
        captured["checkout"] = True
        return _FakeClient()

    def _fake_exec_cmd(client, script, **k):
        captured["script"] = script
        # 模拟远端 systemctl show 输出
        return 0, "== llama.service\nActiveState=active\nSubState=running\n", ""

    monkeypatch.setattr(svc.pool, "checkout", _fake_checkout)
    monkeypatch.setattr(svc.pool, "checkin", lambda c: None)
    monkeypatch.setattr(svc, "exec_cmd", _fake_exec_cmd)

    out = svc.services_state(request, 1, "llama.service", user="t")
    assert captured.get("checkout") is True
    assert "systemctl show" in captured["script"]
    assert out["data"]["llama.service"] == {"active_state": "active", "sub_state": "running"}
