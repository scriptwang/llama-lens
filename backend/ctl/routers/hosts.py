import json
import re
import stat
from datetime import datetime, timezone
from fnmatch import fnmatch

from fastapi import APIRouter, Depends, Request

from .. import database as db
from ..errors import ApiError, VALIDATION_FAILED, ok, validate_unit_name
from ..hostsync import MONITOR_FIELDS, apply_monitor, apply_monitor_async, gen_mid
from .auth import get_current_user
from ...notify import CHANNELS as NOTIFY_CHANNELS
from ..schemas import HostConnectReq, HostUpdateReq
from ..security import decrypt_secret, encrypt_secret
from ..ssh_pool import pool, run

router = APIRouter(prefix="/api/hosts", tags=["hosts"])

# ---------------------------------------------------------------------------
# 主机配置字段校验
# 这些字段会被拼进在被监控主机上执行的 shell 命令（BATCH_CMD / 日志跟随），
# 必须白名单限制，防止命令注入（服务名另有 validate_unit_name 校验）
# ---------------------------------------------------------------------------
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")      # 进程名
UNIT_REF_RE = re.compile(r"^[A-Za-z0-9._@-]+$")      # unit 名（可不含 .service 后缀）
SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")    # 日志文件绝对路径
SAFE_MOUNT_RE = re.compile(r"^/[A-Za-z0-9._-]*$")    # 挂载点


def validate_monitor_fields(systemd_unit, process_name, log_source, log_unit,
                            log_path, disk_mounts) -> None:
    """校验监控相关字段的最终取值（写入 DB 前调用）。"""
    if systemd_unit:
        validate_unit_name(systemd_unit)
    if not SAFE_NAME_RE.match(process_name or ""):
        raise ApiError(VALIDATION_FAILED, "进程名仅允许字母数字 . _ -")
    if log_source not in ("journal", "file"):
        raise ApiError(VALIDATION_FAILED, "log_source 必须是 journal 或 file")
    if log_unit and not UNIT_REF_RE.match(log_unit):
        raise ApiError(VALIDATION_FAILED, "日志 unit 名仅允许字母数字 . _ - @")
    if log_path and not SAFE_PATH_RE.match(log_path):
        raise ApiError(VALIDATION_FAILED, "日志路径必须是绝对路径，仅允许字母数字 . _ / -")
    if log_source == "file" and not log_path:
        raise ApiError(VALIDATION_FAILED, "log_source=file 时必须配置 log_path")
    for m in disk_mounts or []:
        if not SAFE_MOUNT_RE.match(str(m)):
            raise ApiError(VALIDATION_FAILED, "非法挂载点：%s" % m)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _public(row) -> dict:
    return {
        "id": row["id"],
        "alias": row["alias"],
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "auth_type": row["auth_type"],
        "browse_paths": row["browse_paths"] or "",
        "notify_enabled": bool(row["notify_enabled"]),
        "notify_type": row["notify_type"] or "wecom",
        "notify_url": row["notify_url"] or "",
        "created_at": row["created_at"],
        "last_connected_at": row["last_connected_at"],
    }


def host_to_conn_dict(row) -> dict:
    """把 hosts 行解密为 SSH 池可用的 dict"""
    d = {
        "id": row["id"],
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "auth_type": row["auth_type"],
    }
    if row["auth_type"] == "key":
        d["key_data"] = decrypt_secret(row["encrypted_pwd"])
        d["key_passphrase"] = decrypt_secret(row["key_passphrase_enc"]) if row["key_passphrase_enc"] else None
    else:
        d["password"] = decrypt_secret(row["encrypted_pwd"])
    return d


def get_host_row(host_id: int):
    row = db.query_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if row is None:
        raise ApiError(VALIDATION_FAILED, "主机不存在或已被删除")
    return row


@router.post("/connect")
def connect(req: HostConnectReq, request: Request, user: str = Depends(get_current_user)):
    if req.auth_type not in ("password", "key"):
        raise ApiError(VALIDATION_FAILED, "auth_type 必须是 password 或 key")
    if req.notify_type not in NOTIFY_CHANNELS:
        raise ApiError(VALIDATION_FAILED, "notify_type 必须是 %s 之一" % "/".join(NOTIFY_CHANNELS))
    if req.auth_type == "password" and not req.password:
        raise ApiError(VALIDATION_FAILED, "密码不能为空")
    if req.auth_type == "key" and not req.key_data:
        raise ApiError(VALIDATION_FAILED, "私钥内容不能为空")
    validate_monitor_fields(req.systemd_unit, req.process_name, req.log_source,
                            req.log_unit, req.log_path, req.disk_mounts)
    unit = req.systemd_unit or "llama-server.service"

    test_row = {
        "id": 0,
        "host": req.host,
        "port": req.port,
        "username": req.username,
        "auth_type": req.auth_type,
        "password": req.password,
        "key_data": req.key_data,
        "key_passphrase": req.key_passphrase,
    }
    client = pool.create_client(test_row)
    try:
        run(client, "id -u")
        run(client, "systemctl --version 2>/dev/null || true")
    finally:
        client.close()

    enc = encrypt_secret(req.password if req.auth_type == "password" else req.key_data)
    enc_pass = encrypt_secret(req.key_passphrase) if (req.auth_type == "key" and req.key_passphrase) else None
    existing = db.query_one(
        "SELECT * FROM hosts WHERE host = ? AND port = ? AND username = ?",
        (req.host, req.port, req.username),
    )
    mon_vals = (
        1 if req.monitor_enabled else 0,
        req.llama_host or req.host, req.llama_port, req.llama_interval,
        req.llama_slow_interval, req.llama_timeout,
        req.ssh_interval, req.ssh_keepalive, req.ssh_timeout,
        req.key_path, req.process_name, unit,
        req.log_source, req.log_unit, req.log_path,
        1 if req.log_follow else 0, req.log_catchup_sec,
        json.dumps(req.disk_mounts) if req.disk_mounts else '["/"]',
        json.dumps(req.thresholds) if req.thresholds else None,
        1 if req.notify_enabled else 0, req.notify_type or "wecom", req.notify_url or "",
    )
    if existing:
        db.execute(
            """UPDATE hosts SET alias = ?, encrypted_pwd = ?, auth_type = ?, key_passphrase_enc = ?,
               browse_paths = ?, last_connected_at = ?,
               monitor_enabled = ?, llama_host = ?, llama_port = ?, llama_interval = ?,
               llama_slow_interval = ?, llama_timeout = ?, ssh_interval = ?, ssh_keepalive = ?,
               ssh_timeout = ?, key_path = ?, process_name = ?, systemd_unit = ?,
               log_source = ?, log_unit = ?, log_path = ?, log_follow = ?, log_catchup_sec = ?,
               disk_mounts = ?, thresholds = ?,
               notify_enabled = ?, notify_type = ?, notify_url = ? WHERE id = ?""",
            (req.alias, enc, req.auth_type, enc_pass, req.browse_paths or "", _now(),
             *mon_vals, existing["id"]),
        )
        host_id = existing["id"]
        action = "restart"
        pool.close_host(host_id)  # 凭证已变更：失效管理侧连接池缓存
    else:
        mid = req.mid or gen_mid(req.alias, req.host)
        cur = db.execute(
            """INSERT INTO hosts (mid, alias, host, port, username, encrypted_pwd, auth_type,
               key_passphrase_enc, browse_paths, last_connected_at,
               monitor_enabled, llama_host, llama_port, llama_interval,
               llama_slow_interval, llama_timeout, ssh_interval, ssh_keepalive,
               ssh_timeout, key_path, process_name, systemd_unit,
               log_source, log_unit, log_path, log_follow, log_catchup_sec,
               disk_mounts, thresholds, notify_enabled, notify_type, notify_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, req.alias, req.host, req.port, req.username, enc, req.auth_type,
             enc_pass, req.browse_paths or "", _now(), *mon_vals),
        )
        host_id = cur.lastrowid
        action = "start"
    db.log_action(
        host_id, user, "connect", "",
        json.dumps({"host": req.host, "port": req.port, "username": req.username}, ensure_ascii=False),
        request.client.host if request.client else "",
    )
    row = db.query_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    apply_monitor(request, action, row)
    return ok({"host_id": host_id, "mid": row["mid"]})


@router.get("")
def list_hosts(request: Request, user: str = Depends(get_current_user)):
    """统一主机列表：管理字段 + mid + 监控实时状态。

    返回纯数组（监控前端兼容）：id=mid（监控/路由用），db_id=整型主键（管理 API 用）。
    """
    rows = db.query("SELECT * FROM hosts ORDER BY id")
    reg = getattr(request.app.state, "registry", None)
    out = []
    for r in rows:
        d = _public(r)
        d["id"] = r["mid"]
        d["db_id"] = r["id"]
        d["name"] = r["alias"] or r["host"]
        d["monitor_enabled"] = bool(r["monitor_enabled"])
        mon = reg.get(r["mid"]) if reg is not None else None
        if mon is not None:
            live = mon.portal_summary()
            live.pop("id", None)
            d.update(live)
        else:
            d.update({
                "online": None, "ssh_ok": None, "model_name": "", "n_params": None,
                "gen_speed_tps": 0.0, "speed_source": None, "gpus": [],
                "cpu_pct": None, "mem_pct": None, "speed_spark": [],
                "alerts": [], "alerts_count": 0,
            })
        out.append(d)
    return out


@router.get("/{host_id}/fs")
def list_fs(host_id: int, path: str = "/", pattern: str = "", user: str = Depends(get_current_user)):
    """列出远端目录（SFTP），支持按文件名 pattern 过滤（如 *.gguf）"""
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        path = (path or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        sftp = client.open_sftp()
        try:
            attrs = sftp.listdir_attr(path)
        except IOError as e:
            raise ApiError(VALIDATION_FAILED, f"无法列出目录 {path}：{e}")
        finally:
            sftp.close()
        base = path.rstrip("/")
        entries = []
        for a in attrs:
            name = a.filename
            is_dir = stat.S_ISDIR(a.st_mode)
            # 过滤只作用于文件；目录始终显示（否则带过滤时无法导航）
            if pattern and not is_dir and not fnmatch(name, pattern):
                continue
            child = (base + "/" + name) if base else ("/" + name)
            entries.append({"name": name, "path": child, "is_dir": is_dir, "size": a.st_size or 0})
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        parent = "/" if path == "/" else (path.rsplit("/", 1)[0] or "/")
        return ok({"path": path, "parent": parent, "entries": entries})
    finally:
        pool.checkin(client)


@router.put("/{host_id}")
async def update_host(host_id: int, req: HostUpdateReq, request: Request, user: str = Depends(get_current_user)):
    row0 = get_host_row(host_id)
    # 校验"更新后最终状态"（变更字段 + 现有值合并），防命令注入
    validate_monitor_fields(
        req.systemd_unit if req.systemd_unit is not None else row0["systemd_unit"],
        req.process_name if req.process_name is not None else row0["process_name"],
        req.log_source if req.log_source is not None else row0["log_source"],
        req.log_unit if req.log_unit is not None else row0["log_unit"],
        req.log_path if req.log_path is not None else row0["log_path"],
        req.disk_mounts if req.disk_mounts is not None else json.loads(row0["disk_mounts"] or "[]"),
    )
    if req.systemd_unit == "":
        req.systemd_unit = "llama-server.service"
    fields, params = [], []
    if req.alias is not None:
        fields.append("alias = ?")
        params.append(req.alias)
    if req.browse_paths is not None:
        fields.append("browse_paths = ?")
        params.append(req.browse_paths)
    mon_changed = False
    for f in MONITOR_FIELDS:
        v = getattr(req, f)
        if v is None:
            continue
        if f in ("monitor_enabled", "log_follow"):
            v = 1 if v else 0
        elif f in ("disk_mounts", "thresholds"):
            v = json.dumps(v)
        fields.append(f"{f} = ?")
        params.append(v)
        mon_changed = True
    if fields:
        params.append(host_id)
        db.execute(f"UPDATE hosts SET {', '.join(fields)} WHERE id = ?", tuple(params))
    db.log_action(host_id, user, "update_host", "", "更新主机配置", request.client.host if request.client else "")
    if mon_changed:
        row = db.query_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
        await apply_monitor_async(request, "restart", row)
    return ok({"updated": host_id})


@router.post("/{host_id}/test")
def test_host(host_id: int, request: Request, user: str = Depends(get_current_user)):
    row = get_host_row(host_id)
    client = pool.create_client(host_to_conn_dict(row))
    try:
        run(client, "id -u")
    finally:
        client.close()
    db.execute("UPDATE hosts SET last_connected_at = ? WHERE id = ?", (_now(), host_id))
    db.log_action(host_id, user, "connect", "", "测试连接", request.client.host if request.client else "")
    return ok({"ok": True})


@router.delete("/{host_id}")
async def delete_host(host_id: int, request: Request, user: str = Depends(get_current_user)):
    row = get_host_row(host_id)
    await apply_monitor_async(request, "stop", row)
    pool.close_host(host_id)
    db.log_action(
        host_id, user, "delete_host", "",
        json.dumps({"host": row["host"], "port": row["port"]}, ensure_ascii=False),
        request.client.host if request.client else "",
    )
    db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    return ok({"deleted": host_id})
