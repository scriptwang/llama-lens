import io
import socket
import threading
import time

import paramiko

from .config import settings
from .errors import (
    ApiError,
    SSH_AUTH_FAILED,
    SSH_CMD_FAILED,
    SSH_POOL_EXHAUSTED,
    SSH_TIMEOUT,
    SSH_UNREACHABLE,
)

MAX_PER_HOST = 2
IDLE_TIMEOUT = 300
# 池满时 checkout 的等待上限：前端 5s 指标轮询 + 用户操作（启停/扫描/日志）
# 易产生 3 个并发请求，直接报错体验差；改为排队等待空闲槽位。
CHECKOUT_WAIT_TIMEOUT = 30


def load_pkey(key_data: str, passphrase=None):
    classes = [getattr(paramiko, n) for n in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey") if hasattr(paramiko, n)]
    for cls in classes:
        try:
            return cls.from_private_key(io.StringIO(key_data), password=passphrase or None)
        except Exception:
            continue
    raise ApiError(SSH_AUTH_FAILED, "无法解析私钥（支持 OpenSSH/PEM 格式 RSA/ED25519/ECDSA）")


class _Conn:
    def __init__(self, client, host_id: int):
        self.client = client
        self.host_id = host_id
        self.in_use = False
        self.last_used = time.time()


class SSHPool:
    def __init__(self):
        self._pool = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    def _reap_loop(self):
        while not self._stop.wait(60):
            now = time.time()
            with self._lock:
                for host_id in list(self._pool.keys()):
                    keep = []
                    for c in self._pool[host_id]:
                        if not c.in_use and now - c.last_used > IDLE_TIMEOUT:
                            self._close(c)
                        else:
                            keep.append(c)
                    self._pool[host_id] = keep

    @staticmethod
    def _close(c):
        try:
            c.client.close()
        except Exception:
            pass

    @staticmethod
    def _healthy(c) -> bool:
        try:
            t = c.client.get_transport()
            return bool(t and t.is_active())
        except Exception:
            return False

    def _connect(self, host_row: dict) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=host_row["host"],
            port=int(host_row["port"]),
            username=host_row["username"],
            timeout=settings.ssh_timeout,
            banner_timeout=settings.ssh_timeout,
            auth_timeout=settings.ssh_timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        if host_row["auth_type"] == "key":
            kwargs["pkey"] = load_pkey(host_row["key_data"], host_row.get("key_passphrase"))
        else:
            kwargs["password"] = host_row.get("password")
        client.connect(**kwargs)
        return client

    def create_client(self, host_row: dict) -> paramiko.SSHClient:
        """新建一条 SSH 连接（带错误码映射），不注册进池"""
        try:
            return self._connect(host_row)
        except paramiko.AuthenticationException as e:
            raise ApiError(SSH_AUTH_FAILED, f"SSH 认证失败：{e}")
        except (socket.timeout, TimeoutError):
            raise ApiError(SSH_TIMEOUT, f"SSH 连接超时（>{settings.ssh_timeout}s）：{host_row['host']}:{host_row['port']}")
        except (paramiko.SSHException, OSError) as e:
            raise ApiError(SSH_UNREACHABLE, f"无法连接 {host_row['host']}:{host_row['port']}：{e}")

    def checkout(self, host_row: dict) -> paramiko.SSHClient:
        host_id = host_row["id"]
        deadline = time.time() + CHECKOUT_WAIT_TIMEOUT
        while True:
            with self._lock:
                conns = self._pool.get(host_id, [])
                kept = []
                reusable = None
                for c in conns:
                    if c.in_use:
                        kept.append(c)
                        continue
                    if self._healthy(c):
                        reusable = c
                        kept.append(c)
                    else:
                        self._close(c)
                self._pool[host_id] = kept
                if reusable is not None:
                    reusable.in_use = True
                    return reusable.client
                if len(kept) < MAX_PER_HOST:
                    break
            # 池满：等待连接释放，而不是立即报错
            if time.time() >= deadline:
                raise ApiError(SSH_POOL_EXHAUSTED, "SSH 连接池繁忙，等待 %d 秒后仍无可用连接，请稍后重试" % CHECKOUT_WAIT_TIMEOUT)
            time.sleep(0.2)
        for attempt in range(2):
            try:
                client = self.create_client(host_row)
            except ApiError as e:
                if e.code == SSH_TIMEOUT and attempt == 0:
                    continue
                raise
            with self._lock:
                conn = _Conn(client, host_id)
                conn.in_use = True  # 新连接立即标记占用，避免并发请求复用同一 client
                self._pool.setdefault(host_id, []).append(conn)
            return client
        raise ApiError(SSH_TIMEOUT, f"SSH 连接超时（>{settings.ssh_timeout}s）：{host_row['host']}:{host_row['port']}")

    def checkin(self, client) -> None:
        with self._lock:
            for conns in self._pool.values():
                for c in conns:
                    if c.client is client:
                        c.in_use = False
                        c.last_used = time.time()
                        return

    def close_host(self, host_id: int) -> None:
        with self._lock:
            for c in self._pool.pop(host_id, []):
                self._close(c)

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for conns in self._pool.values():
                for c in conns:
                    self._close(c)
            self._pool.clear()


pool = SSHPool()


def exec_cmd(client, command: str, timeout: int = None):
    """执行远端命令，返回 (exit_code, stdout, stderr)"""
    timeout = timeout or settings.ssh_timeout
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err
    except socket.timeout:
        raise ApiError(SSH_TIMEOUT, f"远端命令执行超时（>{timeout}s）：{command[:100]}")
    except Exception as e:
        raise ApiError(SSH_UNREACHABLE, f"SSH 连接异常：{e}")


def run(client, command: str, timeout: int = None) -> str:
    code, out, err = exec_cmd(client, command, timeout)
    if code != 0:
        raise ApiError(SSH_CMD_FAILED, f"命令失败({code})：{command[:100]}\n{(err or out).strip()}")
    return out


def sftp_read(client, path: str) -> str:
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "r") as f:
            return f.read().decode("utf-8", "replace")
    finally:
        sftp.close()


def sftp_write_atomic(client, path: str, content: str) -> None:
    sftp = client.open_sftp()
    tmp = f"{path}.tmp.{time.time_ns()}"
    try:
        with sftp.open(tmp, "w") as f:
            f.write(content)
        try:
            sftp.remove(path)
        except IOError:
            pass
        sftp.rename(tmp, path)
    finally:
        sftp.close()


def sftp_copy(client, src: str, dst: str) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.open(src, "r") as fin, sftp.open(dst, "w") as fout:
            while True:
                chunk = fin.read(65536)
                if not chunk:
                    break
                fout.write(chunk)
    finally:
        sftp.close()


def sftp_exists(client, path: str) -> bool:
    sftp = client.open_sftp()
    try:
        sftp.stat(path)
        return True
    except IOError:
        return False
    finally:
        sftp.close()


def sftp_remove(client, path: str) -> None:
    sftp = client.open_sftp()
    try:
        sftp.remove(path)
    finally:
        sftp.close()
