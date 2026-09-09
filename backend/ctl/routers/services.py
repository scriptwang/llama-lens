from fastapi import APIRouter, Depends, Request

from .. import database as db
from ..core import scanner
from ..core.systemctl import systemctl
from ..errors import (
    ApiError,
    BACKUP_FAILED,
    DAEMON_RELOAD_FAILED,
    FILE_EXISTS,
    FILE_NOT_FOUND,
    SSH_CMD_FAILED,
    VALIDATION_FAILED,
    WRITE_FAILED,
    ok,
    validate_unit_name,
)
from .auth import get_current_user
from ..schemas import ServiceCreateReq, ServiceDuplicateReq, ServiceRenameReq
from .hosts import get_host_row, host_to_conn_dict
from ..ssh_pool import exec_cmd, pool, sftp_copy, sftp_exists, sftp_read, sftp_remove, sftp_write_atomic

router = APIRouter(prefix="/api/services", tags=["services"])

ACTION_VERBS = {"start": "start", "stop": "stop", "restart": "restart"}


def _get_rules() -> dict:
    from ..routers.rules import get_rules_value
    return get_rules_value()


def _checkout(host_id: int):
    row = get_host_row(host_id)
    return pool.checkout(host_to_conn_dict(row))


def _monitor_for(request: Request, row):
    """该主机对应的 HostMonitor（未启用监控/独立 ctl 模式返回 None）。"""
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        return None
    return reg.get(row["mid"])


def _state(client, name: str) -> dict:
    code, out, _ = exec_cmd(client, f"systemctl show {name} -p ActiveState -p SubState -p UnitFileState 2>/dev/null")
    props = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    return {
        "active_state": props.get("ActiveState", "unknown"),
        "sub_state": props.get("SubState", ""),
        "unit_file_state": props.get("UnitFileState", ""),
    }


@router.get("")
def list_services(host_id: int, user: str = Depends(get_current_user)):
    client = _checkout(host_id)
    try:
        services = scanner.scan_services(client, _get_rules())
    finally:
        pool.checkin(client)
    return ok(services)


@router.get("/state")
def services_state(request: Request, host_id: int, names: str = "",
                   user: str = Depends(get_current_user)):
    """轻量状态查询：批量取多服务 ActiveState/SubState（前端 ~1s 准实时轮询用）。

    优先复用监控快照（SshPoller 每 2s 已在批量命令里采集全部服务状态，零额外 SSH）；
    无监控/不可达/快照无数据时回退单次 SSH 批量查询。
    """
    name_list = []
    for n in names.split(","):
        n = n.strip()
        if n and n not in name_list:
            validate_unit_name(n)
            name_list.append(n)
    if not name_list:
        return ok({})

    row = get_host_row(host_id)
    # 优先复用监控快照（零额外 SSH）：all_services 由 SshPoller 2s 批量命令维护
    mon = _monitor_for(request, row)
    if mon is not None:
        all_services = mon.ssh_poller.metrics.get("all_services") or {}
        if all_services:
            return ok({n: st for n, st in ((n, all_services.get(n)) for n in name_list) if st})

    # 回退：单条 SSH 命令批量查询（监控未启用/快照无数据时）
    client = pool.checkout(host_to_conn_dict(row))
    try:
        script = "for u in %s; do echo \"== $u\"; systemctl show \"$u\" -p ActiveState -p SubState 2>/dev/null; done" % " ".join(name_list)
        code, out, _ = exec_cmd(client, script)
        result = {}
        current = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("== "):
                current = line[3:].strip()
                result.setdefault(current, {})
            elif current and "=" in line:
                k, v = line.split("=", 1)
                if k == "ActiveState":
                    result[current]["active_state"] = v
                elif k == "SubState":
                    result[current]["sub_state"] = v
        return ok(result)
    finally:
        pool.checkin(client)


@router.post("")
def create_service(req: ServiceCreateReq, host_id: int, request: Request, user: str = Depends(get_current_user)):
    """新建服务单元文件（写入 /etc/systemd/system，已存在则拒绝，失败自动回滚）"""
    name = req.name.strip()
    if not name.lower().endswith(".service"):
        name += ".service"
    validate_unit_name(name)
    content = req.content or ""
    if not content.strip():
        raise ApiError(VALIDATION_FAILED, "content 不能为空")
    if "[Unit]" not in content:
        raise ApiError(VALIDATION_FAILED, "单元文件必须包含 [Unit] 段")
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        path = f"/etc/systemd/system/{name}"
        if sftp_exists(client, path):
            raise ApiError(FILE_EXISTS, f"服务文件已存在：{path}")
        try:
            sftp_write_atomic(client, path, content if content.endswith("\n") else content + "\n")
        except Exception as e:
            raise ApiError(WRITE_FAILED, f"写入失败：{e}")
        code, out, err = systemctl(client, host_id, "daemon-reload")
        if code != 0:
            try:
                sftp_remove(client, path)
                exec_cmd(client, "systemctl daemon-reload 2>/dev/null")
            except Exception:
                pass
            raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败，已回滚删除：{err.strip()}")
        db.log_action(host_id, user, "create", name, f"已创建 {path}", request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok({"created": path, "reloaded": True})


@router.post("/{name}/restore")
def restore_backup(name: str, host_id: int, request: Request, user: str = Depends(get_current_user)):
    """用 .bak 备份恢复服务文件（必须先于 /{name}/{action} 注册，避免被 action 路由吞掉）"""
    validate_unit_name(name)
    client = _checkout(host_id)
    try:
        code, out, _ = exec_cmd(client, f"systemctl show {name} -p FragmentPath --value 2>/dev/null")
        fragment = out.strip()
        if not fragment or not sftp_exists(client, fragment):
            raise ApiError(FILE_NOT_FOUND, f"服务文件不存在：{name}")
        bak = fragment + ".bak"
        if not sftp_exists(client, bak):
            raise ApiError(BACKUP_FAILED, "备份文件（.bak）不存在，无法恢复")
        sftp_copy(client, bak, fragment)
        code, out, err = systemctl(client, host_id, "daemon-reload")
        if code != 0:
            raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败：{err.strip()}")
        db.log_action(host_id, user, "restore", name, f"已从 {bak} 恢复", request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok({"restored": fragment, "backup": bak})


@router.post("/{name}/duplicate")
def duplicate_service(req: ServiceDuplicateReq, name: str, host_id: int, request: Request, user: str = Depends(get_current_user)):
    """复制服务：以相同内容创建新单元文件（内容中引用的原服务名替换为新服务名），必须先于 /{name}/{action} 注册"""
    validate_unit_name(name)
    new_name = req.new_name.strip()
    if not new_name.lower().endswith(".service"):
        new_name += ".service"
    validate_unit_name(new_name)
    if new_name == name:
        raise ApiError(VALIDATION_FAILED, "新服务名不能与原服务名相同")
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        code, out, _ = exec_cmd(client, f"systemctl show {name} -p FragmentPath --value 2>/dev/null")
        fragment = out.strip()
        if not fragment or not sftp_exists(client, fragment):
            raise ApiError(FILE_NOT_FOUND, f"服务文件不存在：{name}")
        content = req.content if req.content is not None else sftp_read(client, fragment)
        if name in content:
            content = content.replace(name, new_name)
        path = f"/etc/systemd/system/{new_name}"
        if sftp_exists(client, path):
            raise ApiError(FILE_EXISTS, f"服务文件已存在：{path}")
        try:
            sftp_write_atomic(client, path, content if content.endswith("\n") else content + "\n")
        except Exception as e:
            raise ApiError(WRITE_FAILED, f"写入失败：{e}")
        code, out, err = systemctl(client, host_id, "daemon-reload")
        if code != 0:
            try:
                sftp_remove(client, path)
                exec_cmd(client, "systemctl daemon-reload 2>/dev/null")
            except Exception:
                pass
            raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败，已回滚删除：{err.strip()}")
        db.log_action(host_id, user, "duplicate", name, f"已复制 {fragment} 为 {path}", request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok({"created": path, "source": fragment, "reloaded": True})


@router.post("/{name}/rename")
def rename_service(req: ServiceRenameReq, name: str, host_id: int, request: Request, user: str = Depends(get_current_user)):
    """重命名服务：单元文件改名（运行中先停，改名后以新名启动；失败自动回滚）。

    仅支持 /etc/systemd/system 下的单元文件（发行版目录的文件改名会破坏包管理，
    提示用「复制」代替）。必须先于 /{name}/{action} 注册，避免被 action 路由吞掉。
    """
    validate_unit_name(name)
    new_name = req.new_name.strip()
    if not new_name.lower().endswith(".service"):
        new_name += ".service"
    validate_unit_name(new_name)
    if new_name == name:
        raise ApiError(VALIDATION_FAILED, "新服务名不能与原服务名相同")
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        code, out, _ = exec_cmd(client, f"systemctl show {name} -p FragmentPath --value 2>/dev/null")
        fragment = out.strip()
        if not fragment or not sftp_exists(client, fragment):
            raise ApiError(FILE_NOT_FOUND, f"服务文件不存在：{name}")
        if not fragment.startswith("/etc/systemd/system/"):
            raise ApiError(VALIDATION_FAILED,
                           "该服务文件在发行版目录（%s），不支持改名；请用「复制」创建新服务" % fragment)
        path = f"/etc/systemd/system/{new_name}"
        if sftp_exists(client, path):
            raise ApiError(FILE_EXISTS, f"服务文件已存在：{path}")
        orig_content = sftp_read(client, fragment)
        content = orig_content.replace(name, new_name) if name in orig_content else orig_content
        was_active = _state(client, name).get("active_state") == "active"
        if was_active:
            code, out, err = systemctl(client, host_id, "stop", name, timeout=30)
            if code != 0:
                raise ApiError(SSH_CMD_FAILED, f"停止服务失败，无法改名：{(err or out).strip()}")
        try:
            sftp_write_atomic(client, path, content if content.endswith("\n") else content + "\n")
            sftp_remove(client, fragment)
            code, out, err = systemctl(client, host_id, "daemon-reload")
            if code != 0:
                raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败：{err.strip()}")
            if was_active:
                code, out, err = systemctl(client, host_id, "start", new_name, timeout=30)
                if code != 0:
                    raise ApiError(SSH_CMD_FAILED, f"以新名启动失败：{(err or out).strip()}")
        except Exception:
            # 回滚：还原原文件、删除新文件、reload，原运行中的服务恢复启动
            try:
                sftp_write_atomic(client, fragment, orig_content if orig_content.endswith("\n") else orig_content + "\n")
                if sftp_exists(client, path):
                    sftp_remove(client, path)
                exec_cmd(client, "systemctl daemon-reload 2>/dev/null")
                if was_active:
                    systemctl(client, host_id, "start", name, timeout=30)
            except Exception:
                pass
            raise
        db.log_action(host_id, user, "rename", name, f"已重命名 {fragment} 为 {path}",
                      request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok({"renamed": path, "from": fragment, "reloaded": True})


@router.post("/{name}/delete")
def delete_service(name: str, host_id: int, request: Request, user: str = Depends(get_current_user)):
    """删除服务：停止（如运行中）+ 删除单元文件 + daemon-reload。

    仅支持 /etc/systemd/system 下的单元文件（发行版目录的文件由包管理持有，删除会破坏系统包）。
    存在 .bak 备份时一并删除（避免日后同名新服务误恢复旧内容）；失败自动回滚（还原文件 + 重启原服务）。
    必须先于 /{name}/{action} 注册，避免被 action 路由吞掉。
    """
    validate_unit_name(name)
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        code, out, _ = exec_cmd(client, f"systemctl show {name} -p FragmentPath --value 2>/dev/null")
        fragment = out.strip()
        if not fragment or not sftp_exists(client, fragment):
            raise ApiError(FILE_NOT_FOUND, f"服务文件不存在：{name}")
        if not fragment.startswith("/etc/systemd/system/"):
            raise ApiError(VALIDATION_FAILED,
                           "该服务文件在发行版目录（%s），不允许删除；请通过包管理移除" % fragment)
        was_active = _state(client, name).get("active_state") == "active"
        if was_active:
            code, out, err = systemctl(client, host_id, "stop", name, timeout=30)
            if code != 0:
                raise ApiError(SSH_CMD_FAILED, f"停止服务失败，无法删除：{(err or out).strip()}")
        orig_content = sftp_read(client, fragment)
        bak = fragment + ".bak"
        had_bak = sftp_exists(client, bak)
        try:
            sftp_remove(client, fragment)
            code, out, err = systemctl(client, host_id, "daemon-reload")
            if code != 0:
                raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败：{err.strip()}")
            if had_bak:
                sftp_remove(client, bak)  # 成功后才删备份（失败时保留用于回滚）
        except Exception:
            # 回滚：还原单元文件、reload、原运行中的服务恢复启动
            try:
                sftp_write_atomic(client, fragment, orig_content if orig_content.endswith("\n") else orig_content + "\n")
                exec_cmd(client, "systemctl daemon-reload 2>/dev/null")
                if was_active:
                    systemctl(client, host_id, "start", name, timeout=30)
            except Exception:
                pass
            raise
        db.log_action(host_id, user, "delete", name,
                     f"已删除 {fragment}" + ("（含 .bak 备份）" if had_bak else ""),
                     request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok({"deleted": fragment, "stopped": was_active, "reloaded": True})


@router.post("/{name}/{action}")
def service_action(name: str, action: str, host_id: int, request: Request, user: str = Depends(get_current_user)):
    validate_unit_name(name)
    if action not in ACTION_VERBS and action != "refresh":
        raise ApiError(VALIDATION_FAILED, "未知操作")
    client = _checkout(host_id)
    try:
        if action == "refresh":
            data = _state(client, name)
        else:
            code, out, err = systemctl(client, host_id, ACTION_VERBS[action], name, timeout=30)
            if code != 0:
                raise ApiError(SSH_CMD_FAILED, f"{action} 失败：{(err or out).strip()}")
            data = _state(client, name)
        db.log_action(host_id, user, action, name, "", request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok(data)


@router.get("/{name}/status")
def service_status(name: str, host_id: int, user: str = Depends(get_current_user)):
    validate_unit_name(name)
    client = _checkout(host_id)
    try:
        data = _state(client, name)
    finally:
        pool.checkin(client)
    return ok(data)
