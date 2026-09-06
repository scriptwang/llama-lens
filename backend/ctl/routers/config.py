import shlex
import time

from fastapi import APIRouter, Depends, Request

from .. import database as db
from ..core.execstart import ParseError, build_execstart, parse_execstart, validate_args
from ..core.systemctl import systemctl
from ..core.unitfile import detect_execstart_style, find_execstart_span, join_continuation, replace_execstart
from ..errors import (
    ApiError,
    BACKUP_FAILED,
    DAEMON_RELOAD_FAILED,
    FILE_NOT_FOUND,
    PARSE_FAILED,
    VALIDATION_FAILED,
    WRITE_FAILED,
    ok,
    validate_unit_name,
)
from .auth import get_current_user
from .hosts import get_host_row, host_to_conn_dict
from ..schemas import ConfigSaveReq
from ..ssh_pool import exec_cmd, pool, sftp_copy, sftp_exists, sftp_read, sftp_write_atomic

router = APIRouter(prefix="/api/services", tags=["config"])


def _fragment_path(client, name: str) -> str:
    code, out, _ = exec_cmd(client, f"systemctl show {name} -p FragmentPath --value 2>/dev/null")
    fragment = out.strip()
    if not fragment or not sftp_exists(client, fragment):
        raise ApiError(FILE_NOT_FOUND, f"服务文件不存在：{name}（可能尚未 daemon-reload，或位于 /usr/lib/systemd/system）")
    return fragment


@router.get("/{name}/config")
def get_config(name: str, host_id: int, user: str = Depends(get_current_user)):
    validate_unit_name(name)
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        fragment = _fragment_path(client, name)
        content = sftp_read(client, fragment)
        parsed = None
        parse_error = ""
        lines = content.splitlines()
        span = find_execstart_span(lines)
        if span:
            try:
                parsed = parse_execstart(join_continuation(lines, span[0], span[1]))
            except ParseError as e:
                parse_error = str(e)
        else:
            parse_error = "未找到 ExecStart 行"
        return ok({
            "raw": content,
            "fragment_path": fragment,
            "parsed": parsed,
            "parse_error": parse_error,
            "backup_exists": sftp_exists(client, fragment + ".bak"),
            "in_etc": fragment.startswith("/etc/systemd/system/"),
        })
    finally:
        pool.checkin(client)


_HELP_CACHE = {}
_HELP_TTL = 3600


@router.get("/{name}/help")
def get_help(name: str, host_id: int, refresh: bool = False, user: str = Depends(get_current_user)):
    """动态获取该服务可执行文件的 --help 输出（按 host+executable 缓存，refresh 强制刷新）"""
    validate_unit_name(name)
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        fragment = _fragment_path(client, name)
        content = sftp_read(client, fragment)
        lines = content.splitlines()
        span = find_execstart_span(lines)
        if not span:
            raise ApiError(PARSE_FAILED, "未找到 ExecStart 行")
        try:
            parsed = parse_execstart(join_continuation(lines, span[0], span[1]))
        except ParseError as e:
            raise ApiError(PARSE_FAILED, str(e))
        executable = parsed["executable"]
        cache_key = (host_id, executable)
        now = time.time()
        cached = _HELP_CACHE.get(cache_key)
        if not refresh and cached and now - cached[0] < _HELP_TTL:
            return ok(cached[1])
        help_text = ""
        for flag in ("--help", "-h"):
            try:
                code, out, err = exec_cmd(client, f"{shlex.quote(executable)} {flag}", timeout=10)
            except ApiError:
                continue
            if code == 0 or (out or "").strip():
                combined = (out or "") + (err or "")
                if combined.strip():
                    help_text = combined.strip()
                    break
        version = ""
        try:
            code, out, err = exec_cmd(client, f"{shlex.quote(executable)} --version", timeout=10)
            version = ((out or "") + (err or "")).strip()
        except ApiError:
            pass
        result = {"executable": executable, "help": help_text, "version": version}
        _HELP_CACHE[cache_key] = (now, result)
        return ok(result)
    finally:
        pool.checkin(client)


@router.put("/{name}/config")
def save_config(name: str, host_id: int, req: ConfigSaveReq, request: Request, user: str = Depends(get_current_user)):
    validate_unit_name(name)
    if req.mode not in ("source", "visual"):
        raise ApiError(VALIDATION_FAILED, "mode 必须是 source 或 visual")
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        fragment = _fragment_path(client, name)
        original = sftp_read(client, fragment)

        if req.mode == "source":
            if not req.content:
                raise ApiError(VALIDATION_FAILED, "content 不能为空")
            new_content = req.content
        else:
            if req.args is None:
                raise ApiError(VALIDATION_FAILED, "args 不能为空")
            lines = original.splitlines()
            span = find_execstart_span(lines)
            if span is None:
                raise ApiError(PARSE_FAILED, "未找到 ExecStart 行，请使用源码模式")
            try:
                parsed = parse_execstart(join_continuation(lines, span[0], span[1]))
            except ParseError as e:
                raise ApiError(PARSE_FAILED, str(e))
            validated = validate_args(req.args)
            detected = detect_execstart_style(lines, span)
            if req.style == "multiline":
                style = {"multiline": True, "indent": detected["indent"] or "    "}
            elif req.style == "single":
                style = {"multiline": False, "indent": ""}
            else:
                style = detected
            new_content = replace_execstart(original, build_execstart(parsed, validated, style, lines, span))

        try:
            sftp_copy(client, fragment, fragment + ".bak")
        except Exception as e:
            raise ApiError(BACKUP_FAILED, f"备份失败，已中止写入：{e}")
        try:
            sftp_write_atomic(client, fragment, new_content)
        except Exception as e:
            raise ApiError(WRITE_FAILED, f"写入失败：{e}")

        code, out, err = systemctl(client, host_id, "daemon-reload")
        if code != 0:
            raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败：{err.strip()}")

        result = {"backup": fragment + ".bak", "reloaded": True, "restarted": False, "restart_error": ""}
        if req.restart:
            code, out, err = systemctl(client, host_id, "restart", name, timeout=30)
            if code == 0:
                result["restarted"] = True
            else:
                result["restart_error"] = (err or out).strip()
        action = "config_save" if req.mode == "source" else "config_restart"
        db.log_action(host_id, user, action, name, f"mode={req.mode}, restart={req.restart}",
                      request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok(result)
