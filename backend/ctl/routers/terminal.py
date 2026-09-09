"""终端：WebSocket 交互式 shell（xterm.js ↔ paramiko invoke_shell）+ SFTP 文件管理。

- 终端走独立 SSH 连接（不进命令池，避免长会话占满池槽位），断开即释放。
- 文件管理走命令池（短操作）：列目录 / 上传 / 下载 / 新建目录 / 删除。
- 终端与文件管理都使用主机管理里配置的 SSH 用户登录。
"""
import asyncio
import json
import os
import shlex
import stat as statmod
import threading
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..errors import ApiError, FILE_NOT_FOUND, VALIDATION_FAILED, ok
from ..routers.auth import get_current_user
from ..routers.hosts import get_host_row, host_to_conn_dict
from ..security import decode_token
from ..ssh_pool import pool

router = APIRouter(prefix="/api/hosts", tags=["terminal"])

MAX_UPLOAD_BYTES = 2 * 1024 ** 3  # 单文件上传上限 2GB
CHUNK = 256 * 1024
DL_CHUNK = 1024 * 1024  # 下载读块：exec 通道 cat 裸字节流，1MB/次


def _ws_user(token: Optional[str]) -> str:
    if not token:
        raise ApiError(3001, "缺少 token")
    try:
        payload = decode_token(token)
    except Exception:
        raise ApiError(3001, "token 无效或已过期")
    return payload.get("sub") or ""


def _norm_path(path: str) -> str:
    p = os.path.normpath(path or "/")
    if not p.startswith("/"):
        raise ApiError(VALIDATION_FAILED, "路径必须是绝对路径")
    return p


@router.websocket("/{host_id}/terminal/ws")
async def terminal_ws(ws: WebSocket, host_id: int, token: Optional[str] = None):
    await ws.accept()
    client = None
    channel = None
    stop = threading.Event()
    loop = asyncio.get_running_loop()

    async def send(obj: dict):
        try:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:
            pass

    try:
        _ws_user(token)
        row = get_host_row(host_id)
        client = await asyncio.to_thread(pool.create_client, host_to_conn_dict(row))
    except Exception as e:
        await send({"type": "status", "state": "error", "reason": getattr(e, "msg", str(e))})
        await ws.close()
        return

    try:
        channel = client.get_transport().open_session()
        channel.get_pty(term="xterm-256color", width=80, height=24)
        channel.invoke_shell()
        # 不设 recv 超时：reader 线程阻塞等待，shell 空闲不中断；WS 断开时 close channel 使其退出
    except Exception as e:
        await send({"type": "status", "state": "error", "reason": f"打开 shell 失败：{e}"})
        client.close()
        await ws.close()
        return

    def reader():
        while not stop.is_set():
            try:
                data = channel.recv(65536)
            except Exception:
                break
            if not data:
                break
            try:
                asyncio.run_coroutine_threadsafe(
                    send({"type": "output", "data": data.decode("utf-8", "replace")}), loop
                )
            except Exception:
                break
        try:
            asyncio.run_coroutine_threadsafe(send({"type": "status", "state": "closed"}), loop)
        except Exception:
            pass

    threading.Thread(target=reader, daemon=True).start()
    await send({"type": "status", "state": "ready"})

    try:
        while True:
            msg = await ws.receive_text()
            try:
                m = json.loads(msg)
            except Exception:
                continue
            mtype = m.get("type")
            if mtype == "input":
                data = m.get("data", "")
                if data:
                    try:
                        channel.sendall(data.encode("utf-8"))
                    except Exception:
                        break
            elif mtype == "resize":
                try:
                    channel.resize_pty(int(m.get("cols", 80)), int(m.get("rows", 24)))
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        for closer in (lambda: channel.close(), lambda: client.close()):
            try:
                closer()
            except Exception:
                pass


# ---------------- 文件管理（SFTP） ----------------

class MkdirReq(BaseModel):
    path: str


def _list_dir(client, path: str) -> list:
    sftp = client.open_sftp()
    try:
        entries = []
        for attr in sftp.listdir_attr(path):
            entries.append({
                "name": attr.filename,
                "is_dir": statmod.S_ISDIR(attr.st_mode),
                "size": attr.st_size or 0,
                "mtime": attr.st_mtime or 0,
                "mode": attr.st_mode,
            })
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return entries
    finally:
        sftp.close()


@router.get("/{host_id}/files")
def list_files(host_id: int, path: str = "/", user: str = Depends(get_current_user)):
    p = _norm_path(path)
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        try:
            entries = _list_dir(client, p)
        except IOError:
            raise ApiError(FILE_NOT_FOUND, f"目录不存在或不可读：{p}")
        return ok({"path": p, "entries": entries})
    finally:
        pool.checkin(client)


@router.post("/{host_id}/files/upload")
async def upload_file(host_id: int, path: str = Form("/"), file: UploadFile = File(...),
                      user: str = Depends(get_current_user)):
    # 独立 SSH 连接：大文件上传可能持续数分钟，不能占用命令池槽位（会阻塞监控轮询/服务操作）
    p = _norm_path(path)
    row = get_host_row(host_id)
    client = await asyncio.to_thread(pool.create_client, host_to_conn_dict(row))
    sftp = None
    try:
        name = os.path.basename(file.filename or "upload") or "upload"
        dst = os.path.join(p, name)
        sftp = client.open_sftp()
        written = 0
        with sftp.open(dst, "w") as f:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ApiError(VALIDATION_FAILED, "文件超过 2GB 上传上限")
                f.write(chunk)
        return ok({"path": dst, "size": written})
    except IOError:
        raise ApiError(FILE_NOT_FOUND, f"目标目录不存在或不可写：{p}")
    finally:
        if sftp:
            sftp.close()
        try:
            client.close()
        except Exception:
            pass


@router.get("/{host_id}/files/download")
def download_file(host_id: int, path: str, token: Optional[str] = None,
                  user: str = Depends(get_current_user)):
    """下载走独立 SSH 连接 + 浏览器原生流式下载（支持 ?token= 直链，<a href> 直接下载）。

    不用命令池：大文件传输可能持续数分钟，占池会阻塞监控轮询/服务操作导致全局卡顿。
    前端用直链而非 fetch+blob：浏览器直接流式写盘，不吃内存。
    """
    p = _norm_path(path)
    row = get_host_row(host_id)
    client = pool.create_client(host_to_conn_dict(row))

    def gen():
        # 用 exec 通道 cat 文件：裸 SSH 字节流，绕过 SFTP 逐包请求/应答开销（实测比 SFTP 快数倍）
        transport = client.get_transport()
        channel = transport.open_session()
        try:
            channel.exec_command(f"cat {shlex.quote(p)}")
            while True:
                chunk = channel.recv(DL_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                channel.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    try:
        sftp = client.open_sftp()
        size = sftp.stat(p).st_size
        sftp.close()
    except IOError:
        client.close()
        raise ApiError(FILE_NOT_FOUND, f"文件不存在：{p}")
    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{os.path.basename(p)}"',
                 "Content-Length": str(size or "")},
    )


@router.post("/{host_id}/files/mkdir")
def mkdir(host_id: int, req: MkdirReq, user: str = Depends(get_current_user)):
    p = _norm_path(req.path)
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        sftp = client.open_sftp()
        try:
            sftp.mkdir(p)
        except IOError as e:
            raise ApiError(VALIDATION_FAILED, f"创建目录失败：{e}")
        finally:
            sftp.close()
        return ok({"path": p})
    finally:
        pool.checkin(client)


def _rmtree_sftp(sftp, path: str):
    for attr in sftp.listdir_attr(path):
        sub = os.path.join(path, attr.filename)
        if statmod.S_ISDIR(attr.st_mode):
            _rmtree_sftp(sftp, sub)
            sftp.rmdir(sub)
        else:
            sftp.remove(sub)
    sftp.rmdir(path)


@router.delete("/{host_id}/files")
def delete_file(host_id: int, path: str, user: str = Depends(get_current_user)):
    p = _norm_path(path)
    if p == "/":
        raise ApiError(VALIDATION_FAILED, "不允许删除根目录")
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        sftp = client.open_sftp()
        try:
            attr = sftp.stat(p)
        except IOError:
            raise ApiError(FILE_NOT_FOUND, f"不存在：{p}")
        try:
            if statmod.S_ISDIR(attr.st_mode):
                _rmtree_sftp(sftp, p)
            else:
                sftp.remove(p)
        except IOError as e:
            raise ApiError(VALIDATION_FAILED, f"删除失败：{e}")
        finally:
            sftp.close()
        return ok({"path": p})
    finally:
        pool.checkin(client)
