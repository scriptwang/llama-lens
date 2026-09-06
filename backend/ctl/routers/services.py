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
from ..schemas import ServiceCreateReq, ServiceDuplicateReq
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
        content = sftp_read(client, fragment)
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
