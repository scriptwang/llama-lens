"""服务重命名端点：单元文件改名（运行中先停后启、失败回滚、发行版目录拒绝）。"""
from types import SimpleNamespace

import pytest

from backend.ctl import errors
from backend.ctl.routers import services as svc
from backend.ctl.schemas import ServiceRenameReq


class FakeEnv:
    """mock 掉 rename 用到的全部外部依赖，记录调用序列。"""

    def __init__(self, fragment="/etc/systemd/system/old.service",
                 exists_new=False, active=False, fail_reload=False):
        self.fragment = fragment
        self.active = active
        self.fail_reload = fail_reload
        self.calls = []
        self.written = {}
        self.removed = []
        self._reload_count = 0
        # 状态化文件系统：exists/read/remove 都基于它
        self.fs = {fragment: "[Unit]\nDescription=old.service\n"}
        if exists_new:
            self.fs["/etc/systemd/system/new.service"] = "[Unit]\n"

    def install(self, monkeypatch):
        monkeypatch.setattr(svc, "get_host_row", lambda host_id: {"mid": "h1"})
        monkeypatch.setattr(svc, "host_to_conn_dict", lambda row: {"id": 1})
        monkeypatch.setattr(svc.pool, "checkout", lambda conn: "client")
        monkeypatch.setattr(svc.pool, "checkin", lambda c: None)
        monkeypatch.setattr(svc.db, "log_action", lambda *a, **k: None)

        def _exec_cmd(client, script, **k):
            if script.startswith("systemctl show"):
                self.calls.append(("show", script))
                return 0, self.fragment + "\n", ""
            self.calls.append(("exec", script))
            return 0, "", ""

        def _sftp_exists(client, path):
            self.calls.append(("exists", path))
            return path in self.fs

        def _sftp_read(client, path):
            self.calls.append(("read", path))
            return self.fs[path]

        def _sftp_write_atomic(client, path, content):
            self.calls.append(("write", path))
            self.written[path] = content
            self.fs[path] = content

        def _sftp_remove(client, path):
            self.calls.append(("remove", path))
            self.removed.append(path)
            self.fs.pop(path, None)

        def _systemctl(client, host_id, verb, name=None, timeout=None):
            self.calls.append(("systemctl", verb, name))
            if verb == "daemon-reload" and self.fail_reload:
                self._reload_count += 1
                if self._reload_count == 1:  # 仅首次（改名流程）失败，回滚 reload 成功
                    return 1, "", "reload failed"
            return 0, "", ""

        monkeypatch.setattr(svc, "exec_cmd", _exec_cmd)
        monkeypatch.setattr(svc, "sftp_exists", _sftp_exists)
        monkeypatch.setattr(svc, "sftp_read", _sftp_read)
        monkeypatch.setattr(svc, "sftp_write_atomic", _sftp_write_atomic)
        monkeypatch.setattr(svc, "sftp_remove", _sftp_remove)
        monkeypatch.setattr(svc, "systemctl", _systemctl)

        def _state(client, name):
            return {"active_state": "active" if self.active else "inactive",
                    "sub_state": "", "unit_file_state": ""}
        monkeypatch.setattr(svc, "_state", _state)


def _req(new_name):
    return ServiceRenameReq(new_name=new_name)


def _call(env, monkeypatch, name="old.service", new_name="new.service"):
    env.install(monkeypatch)
    request = SimpleNamespace(client=None)
    return svc.rename_service(_req(new_name), name, 1, request, user="t")


def test_rename_inactive(monkeypatch):
    env = FakeEnv()
    out = _call(env, monkeypatch)
    data = out["data"]
    assert data["renamed"] == "/etc/systemd/system/new.service"
    assert data["from"] == "/etc/systemd/system/old.service"
    verbs = [c for c in env.calls if c[0] == "systemctl"]
    assert verbs == [("systemctl", "daemon-reload", None)]  # 未运行：只 reload
    assert env.removed == ["/etc/systemd/system/old.service"]
    # 内容中的旧服务名被替换
    assert "new.service" in env.written["/etc/systemd/system/new.service"]
    assert "old.service" not in env.written["/etc/systemd/system/new.service"]


def test_rename_active_stops_then_starts(monkeypatch):
    env = FakeEnv(active=True)
    _call(env, monkeypatch)
    verbs = [c[1:] for c in env.calls if c[0] == "systemctl"]
    assert verbs == [("stop", "old.service"), ("daemon-reload", None), ("start", "new.service")]


def test_rename_new_name_exists(monkeypatch):
    env = FakeEnv(exists_new=True)
    with pytest.raises(errors.ApiError) as ei:
        _call(env, monkeypatch)
    assert ei.value.code == errors.FILE_EXISTS
    assert env.written == {}  # 未写任何文件


def test_rename_distro_dir_rejected(monkeypatch):
    env = FakeEnv(fragment="/usr/lib/systemd/system/old.service")
    with pytest.raises(errors.ApiError) as ei:
        _call(env, monkeypatch)
    assert ei.value.code == errors.VALIDATION_FAILED
    assert "复制" in ei.value.msg


def test_rename_same_name_rejected(monkeypatch):
    env = FakeEnv()
    with pytest.raises(errors.ApiError) as ei:
        _call(env, monkeypatch, new_name="old.service")
    assert ei.value.code == errors.VALIDATION_FAILED


def test_rename_rollback_on_reload_failure(monkeypatch):
    env = FakeEnv(fail_reload=True)
    with pytest.raises(errors.ApiError) as ei:
        _call(env, monkeypatch)
    assert ei.value.code == errors.DAEMON_RELOAD_FAILED
    # 回滚：原文件已还原、新文件已删除
    assert "/etc/systemd/system/old.service" in env.written
    assert "/etc/systemd/system/new.service" in env.removed
