"""SSH 连接管理器（SshConnection）。

- paramiko 为阻塞库，所有阻塞调用通过 run_in_executor 放入线程池，避免阻塞事件循环。
- 持久连接 + keepalive；断线指数退避重连（1s→2s→4s→...→30s 封顶）。
- 断线/重连产生 ssh_down / ssh_up 事件。
- 兼容 Python 3.9。
"""
import asyncio
import logging
import time
from typing import Optional

import paramiko

from ..config import SshCfg
from ..events import EventDetector

log = logging.getLogger("llamalens.ssh")

MAX_BACKOFF = 30.0


class SshConnection:
    def __init__(self, cfg: SshCfg, events: EventDetector, host_id: str):
        self.cfg = cfg
        self.events = events
        self.host_id = host_id
        self.client: Optional[paramiko.SSHClient] = None
        self.connected = False
        self._lock = asyncio.Lock()
        self._backoff = 1.0
        self._emitted_down = False
        self._fail_streak = 0
        self._cooldown_until = 0.0

    # ------------------------------------------------------------------
    def _connect_sync(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=self.cfg.host,
            port=self.cfg.port,
            username=self.cfg.user,
            timeout=self.cfg.timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        if self.cfg.key_path:
            kwargs["key_filename"] = self.cfg.key_path
        if self.cfg.password:
            kwargs["password"] = self.cfg.password
        client.connect(**kwargs)
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(self.cfg.keepalive)
        return client

    def _is_alive(self) -> bool:
        try:
            if self.client is None:
                return False
            t = self.client.get_transport()
            return t is not None and t.is_active()
        except Exception:
            return False

    def _drop(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        was_connected = self.connected
        self.connected = False
        if was_connected and not self._emitted_down:
            self._emitted_down = True
            self.events.set_ssh_connected(time.time(), False)

    # ------------------------------------------------------------------
    def _cooldown(self) -> None:
        """失败后指数冷却（1s→2s→4s→...→30s 封顶），防远端过载时高频重连。"""
        self._fail_streak += 1
        self._cooldown_until = time.time() + min(30.0, 1.0 * (2 ** min(self._fail_streak, 5)))

    async def ensure_connected(self) -> bool:
        if time.time() < self._cooldown_until:
            return False
        async with self._lock:
            if self._is_alive():
                self._on_up()
                return True
            # 需要（重）连接
            try:
                client = await asyncio.get_running_loop().run_in_executor(
                    None, self._connect_sync)
                self.client = client
                self.connected = True
                self._backoff = 1.0
                self._fail_streak = 0
                self._on_up()
                log.info("[%s] SSH 已连接 %s@%s:%s", self.host_id,
                         self.cfg.user, self.cfg.host, self.cfg.port)
                return True
            except Exception as e:
                self._drop()
                log.warning("[%s] SSH 连接失败: %s: %s", self.host_id, type(e).__name__, e)
                self._cooldown()
                return False

    def _on_up(self):
        if self._emitted_down:
            self._emitted_down = False
            self.events.set_ssh_connected(time.time(), True)

    async def exec_command(self, cmd: str, timeout: Optional[float] = None) -> Optional[str]:
        """执行命令并返回 stdout。失败返回 None。"""
        if time.time() < self._cooldown_until:
            return None
        if not await self.ensure_connected():
            return None
        timeout = timeout or self.cfg.timeout

        def _run():
            stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
            # exec_command 的 timeout 只约束建通道；再给 channel 设空闲超时，
            # 防止远端命令挂起时 stdout.read() 永久阻塞、线程泄漏
            chan = stdout.channel
            try:
                chan.settimeout(timeout)
                out = stdout.read().decode("utf-8", "replace")
                return out
            finally:
                # 显式关闭通道与各流，避免 paramiko channel 对象堆积导致内存泄漏
                for s in (stdin, stdout, stderr):
                    try:
                        s.close()
                    except Exception:
                        pass
                try:
                    chan.close()
                except Exception:
                    pass

        try:
            out = await asyncio.get_running_loop().run_in_executor(None, _run)
            self._fail_streak = 0
            return out
        except Exception as e:
            log.warning("[%s] SSH 命令执行失败: %s: %s", self.host_id, type(e).__name__, e)
            self._drop()
            self._cooldown()
            return None

    def open_stream(self, cmd: str):
        """打开长驻流式通道（日志跟随用）。调用方须先 ensure_connected。返回 stdout。"""
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=None)
        return stdout

    async def close(self) -> None:
        async with self._lock:
            self._drop()
