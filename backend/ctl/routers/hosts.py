import json
import stat
from datetime import datetime, timezone
from fnmatch import fnmatch

from fastapi import APIRouter, Depends, Request

from .. import database as db
from ..errors import ApiError, VALIDATION_FAILED, ok
from .auth import get_current_user
from ..schemas import HostConnectReq, HostUpdateReq
from ..security import decrypt_secret, encrypt_secret
from ..ssh_pool import pool, run

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


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
    if req.auth_type == "password" and not req.password:
        raise ApiError(VALIDATION_FAILED, "密码不能为空")
    if req.auth_type == "key" and not req.key_data:
        raise ApiError(VALIDATION_FAILED, "私钥内容不能为空")

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
        "SELECT id FROM hosts WHERE host = ? AND port = ? AND username = ?",
        (req.host, req.port, req.username),
    )
    if existing:
        db.execute(
            "UPDATE hosts SET alias = ?, encrypted_pwd = ?, auth_type = ?, key_passphrase_enc = ?, browse_paths = ?, last_connected_at = ? WHERE id = ?",
            (req.alias, enc, req.auth_type, enc_pass, req.browse_paths or "", _now(), existing["id"]),
        )
        host_id = existing["id"]
    else:
        cur = db.execute(
            "INSERT INTO hosts (alias, host, port, username, encrypted_pwd, auth_type, key_passphrase_enc, browse_paths, last_connected_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (req.alias, req.host, req.port, req.username, enc, req.auth_type, enc_pass, req.browse_paths or "", _now()),
        )
        host_id = cur.lastrowid
    db.log_action(
        host_id, user, "connect", "",
        json.dumps({"host": req.host, "port": req.port, "username": req.username}, ensure_ascii=False),
        request.client.host if request.client else "",
    )
    return ok({"host_id": host_id})


@router.get("")
def list_hosts(user: str = Depends(get_current_user)):
    rows = db.query("SELECT * FROM hosts ORDER BY id")
    return ok([_public(r) for r in rows])


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
            if pattern and not fnmatch(name, pattern):
                continue
            is_dir = stat.S_ISDIR(a.st_mode)
            child = (base + "/" + name) if base else ("/" + name)
            entries.append({"name": name, "path": child, "is_dir": is_dir, "size": a.st_size or 0})
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        parent = "/" if path == "/" else (path.rsplit("/", 1)[0] or "/")
        return ok({"path": path, "parent": parent, "entries": entries})
    finally:
        pool.checkin(client)


@router.put("/{host_id}")
def update_host(host_id: int, req: HostUpdateReq, request: Request, user: str = Depends(get_current_user)):
    get_host_row(host_id)
    fields, params = [], []
    if req.alias is not None:
        fields.append("alias = ?")
        params.append(req.alias)
    if req.browse_paths is not None:
        fields.append("browse_paths = ?")
        params.append(req.browse_paths)
    if fields:
        params.append(host_id)
        db.execute(f"UPDATE hosts SET {', '.join(fields)} WHERE id = ?", tuple(params))
    db.log_action(host_id, user, "update_host", "", "更新主机配置", request.client.host if request.client else "")
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
def delete_host(host_id: int, request: Request, user: str = Depends(get_current_user)):
    row = get_host_row(host_id)
    pool.close_host(host_id)
    db.log_action(
        host_id, user, "delete_host", "",
        json.dumps({"host": row["host"], "port": row["port"]}, ensure_ascii=False),
        request.client.host if request.client else "",
    )
    db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    return ok({"deleted": host_id})
