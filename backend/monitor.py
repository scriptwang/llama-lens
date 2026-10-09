"""HostMonitor / MonitorRegistry。

- HostMonitor：每台主机一个独立监控单元（采集 + 缓冲 + 事件 + 快照），一台故障不影响其他主机。
- 1s tick：速度来源优先级（日志 tg_3s > /slots 差分）→ 写 llama 环形缓冲 → 生成快照（含 alerts）。
- MonitorRegistry：管理所有 HostMonitor，提供门户摘要。
- 兼容 Python 3.9。
"""
import asyncio
import logging
import json
import time
from typing import Any, Dict, List, Optional

from .alerts import evaluate_alerts
from .config import AppConfig, GlobalConfig, HostConfig
from .events import EventDetector
from .diff import DiffEngine
from .engine import create_engine
from .store import RingBuffer, downsample
from .pollers.ssh_conn import SshConnection
from .pollers.ssh_host import SshPoller

log = logging.getLogger("llamalens.monitor")

HOST_SERIES = ("cpu", "mem_used", "mem_buff_cache", "swap_used",
               "net_rx", "net_tx", "proc_cpu", "load_1", "load_5", "load_15")
GPU_PREFIXES = ("gpu_util_", "gpu_mem_", "gpu_temp_", "gpu_power_")


class HostMonitor:
    def __init__(self, cfg: HostConfig, global_cfg: GlobalConfig, writer=None):
        self.cfg = cfg
        self.global_cfg = global_cfg
        self.writer = writer
        self.events = EventDetector(sink=self._event_sink)
        self.events.set_notify_sink(self._notify_sink)
        self.diff = DiffEngine()
        self.ring_llama = RingBuffer(global_cfg.llama_points, sink=self._llama_sink)
        self.ring_host = RingBuffer(global_cfg.host_points, sink=self._host_sink)
        self.ssh = SshConnection(cfg.ssh, self.events, cfg.id)
        self.engine = create_engine(cfg, self.events, self.ssh, self.ring_llama)
        self.ssh_poller = SshPoller(cfg, self.ssh, self.diff, self.ring_host, self.events)
        self._tasks: List[asyncio.Task] = []
        self._snapshot: Optional[Dict[str, Any]] = None
        self._stopped = False
        self._ws_hub = None  # 主机 WS fanout（ws.py 创建，stop 时关闭）

    # ------------------------------------------------------------------
    def _llama_sink(self, name: str, ts: float, value) -> None:
        if self.writer is not None:
            self.writer.enqueue_llama(self.cfg.id, ts, name, value)

    def _host_sink(self, name: str, ts: float, value) -> None:
        if self.writer is not None:
            self.writer.enqueue_host(self.cfg.id, ts, name, value)

    def _notify_sink(self, ev: Dict[str, Any]) -> None:
        """状态变化事件 → webhook 推送（未启用/未配置时直接跳过）。"""
        cfg = self.cfg
        if not (cfg.notify_enabled and cfg.notify_url):
            return
        from .notify import send_async
        title = {"alert": "阈值告警", "llama_up": "llama 上线", "llama_down": "llama 离线",
                 "ssh_up": "SSH 重连", "ssh_down": "SSH 断开"}.get(ev.get("type"), ev.get("type", "事件"))
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_async(
                cfg.notify_type, cfg.notify_url, cfg.name,
                ev.get("level", "info"), title, ev.get("msg", "")))
        except RuntimeError:
            pass  # 无事件循环（测试环境）时跳过

    def _event_sink(self, ev: Dict[str, Any]) -> None:
        if self.writer is not None:
            self.writer.enqueue_event(self.cfg.id, ev)

    # ------------------------------------------------------------------
    async def start(self) -> None:
        log.info("[%s] HostMonitor 启动", self.cfg.id)
        self._tasks = [
            asyncio.create_task(self.engine.start()),
            asyncio.create_task(self.ssh_poller.start()),
            asyncio.create_task(self._tick_loop()),
        ]

    async def stop(self) -> None:
        self._stopped = True
        if self._ws_hub is not None:
            self._ws_hub.close()
            self._ws_hub = None
        self.ssh_poller.stop()
        for t in self._tasks:
            t.cancel()
        await self.engine.stop()
        await self.ssh.close()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------------
    async def _tick_loop(self) -> None:
        interval = self.global_cfg.push_interval
        while not self._stopped:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                gen, prompt, source = self.engine.speeds()
                # 离线时写 None（未知）而非 0，历史曲线出现断点而不是假零线
                online = self.engine.online
                self.ring_llama.push("gen_speed", now, gen if online else None)
                self.ring_llama.push("prompt_speed", now, prompt if online else None)
                self._snapshot = self._build_snapshot(gen, prompt, source)
                # 上下文占用 1s 采样（取自合并后快照的 engine 块）；
                # 任务结束点由 LogPoller 另行写入（权威值），离线写 None 形成断点
                ctx_used = (self._snapshot["engine"].get("ctx") or {}).get("used")
                self.ring_llama.push("ctx_used", now, ctx_used if online else None)
                # 引擎专属时序项（如 SGLang 投机接受长度）
                for name, value in (self.engine.ring_extras() or {}).items():
                    self.ring_llama.push(name, now, value if online else None)
            except Exception:
                log.exception("[%s] tick 失败", self.cfg.id)

    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        if self._snapshot is None:
            gen, prompt, source = self.engine.speeds()
            self._snapshot = self._build_snapshot(gen, prompt, source)
        return self._snapshot

    def _build_snapshot(self, gen: float, prompt: float, source: str) -> Dict[str, Any]:
        now = time.time()
        hm = self.ssh_poller.metrics
        block = self.engine.build_block(hm, gen, prompt, source)

        # all_services 仅供 /api/services/state 复用（不进快照，避免 WS 每秒多推全量服务状态）
        host_metrics = {k: v for k, v in hm.items() if k not in ("_model_sizes", "all_services")}
        if isinstance(host_metrics.get("process"), dict) and self.engine.process_flags:
            host_metrics["process"] = dict(host_metrics["process"])
            host_metrics["process"]["flags"] = self.engine.process_flags

        snap = {
            "ts": now,
            "host": {"id": self.cfg.id, "name": self.cfg.name},
            "engine": block,
            # 旧字段兼容：llama 引擎与 engine 块同形；其他引擎给降级副本
            "llama": block if block.get("type") == "llama_cpp"
                     else self._llama_compat(block),
            "host_metrics": host_metrics,
            "events": self.events.list(50),
        }
        snap["alerts"] = evaluate_alerts(self.cfg.thresholds, block, host_metrics)
        # 阈值穿越事件（级别变化：升级/恢复）
        self.events.check_alerts(snap["alerts"])
        return snap

    @staticmethod
    def _llama_compat(block: Dict[str, Any]) -> Dict[str, Any]:
        """非 llama 引擎的旧 snap["llama"] 兼容副本（形状与旧版一致，无日志/槽位数据）。"""
        return {
            "online": block.get("online", False),
            "model": block.get("model") or {},
            "gen_speed_tps": block.get("gen_speed_tps", 0.0),
            "prompt_speed_tps": block.get("prompt_speed_tps", 0.0),
            "speed_source": block.get("speed_source", "api"),
            "log": None,
            "slots": [],
        }

    # ------------------------------------------------------------------
    def history(self, window_s: int) -> Dict[str, Any]:
        now = time.time()
        series: Dict[str, Any] = {}

        def add(ring: RingBuffer, name: str) -> None:
            pts = downsample(ring.window(name, window_s, now), 600)
            if pts:
                series[name] = {"ts": [t for t, _ in pts], "values": [v for _, v in pts]}

        for name in ("gen_speed", "prompt_speed", "ctx_used", "mtp_acceptance",
                     "accept_length"):
            add(self.ring_llama, name)
        for name in HOST_SERIES:
            add(self.ring_host, name)
        for name in self.ring_host.names():
            if name.startswith(GPU_PREFIXES):
                add(self.ring_host, name)
        return {"window": window_s, "series": series}

    def backfill_from_store(self, store) -> None:
        """启动时从历史 DB 回填环形缓冲，消除容器重启后短窗口（≤1h）的数据缺口。

        只回填最近 max(ring.maxlen, 3600) 秒的原始数据；deque 的 maxlen 会自动
        截断为最新的一段，保证环形缓冲装满最近 1h。不触发 sink（避免重复写库）。
        """
        if store is None:
            return
        now = int(time.time())
        try:
            t0_llama = now - max(self.ring_llama.maxlen, 3600)
            lrows = store.query_llama(self.cfg.id, t0_llama, now)
            if lrows:
                self.ring_llama.load("gen_speed", [(r[0], r[1]) for r in lrows])
                self.ring_llama.load("prompt_speed", [(r[0], r[2]) for r in lrows])
                self.ring_llama.load("ctx_used", [(r[0], r[3]) for r in lrows])
                self.ring_llama.load("mtp_acceptance", [(r[0], r[4]) for r in lrows])

            t0_host = now - max(self.ring_host.maxlen, 3600)
            hrows = store.query_host(self.cfg.id, t0_host, now)
            if hrows:
                for i, name in enumerate(HOST_SERIES):
                    self.ring_host.load(name, [(r[0], r[i + 1]) for r in hrows])
                gpu_acc: Dict[str, List] = {}
                for r in hrows:
                    raw = r[11]
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    for idx, g in data.items():
                        for key, prefix in (("util", "gpu_util_"), ("mem", "gpu_mem_"),
                                            ("temp", "gpu_temp_"), ("power", "gpu_power_")):
                            v = g.get(key)
                            if v is not None:
                                gpu_acc.setdefault(prefix + idx, []).append((r[0], v))
                for name, pts in gpu_acc.items():
                    self.ring_host.load(name, pts)
        except Exception:
            log.exception("[%s] 历史回填失败", self.cfg.id)

    def events_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.events.list(limit)

    # ------------------------------------------------------------------
    def portal_summary(self) -> Dict[str, Any]:
        snap = self.snapshot()
        ll = snap["llama"]
        hm = snap["host_metrics"]
        model = ll.get("model") or {}
        mem = hm.get("mem") or {}
        gpus = []
        for g in hm.get("gpus") or []:
            gpus.append({
                "index": g.get("index", 0),
                "util_pct": g.get("util_pct"),
                "mem_pct": round(g["mem_used_mb"] / g["mem_total_mb"] * 100.0, 1)
                if g.get("mem_total_mb") else None,
            })
        spark = downsample(self.ring_llama.window("gen_speed", 60, time.time()), 30)
        alerts = snap.get("alerts", [])
        return {
            "id": self.cfg.id,
            "name": self.cfg.name,
            "online": ll.get("online", False),
            "ssh_ok": hm.get("reachable", False),
            "model_name": model.get("name", ""),
            "n_params": model.get("n_params"),
            "gen_speed_tps": ll.get("gen_speed_tps", 0.0),
            "speed_source": ll.get("speed_source", "api"),
            "gpus": gpus,
            "cpu_pct": (hm.get("cpu") or {}).get("usage_pct"),
            "mem_pct": round(mem["used_mb"] / mem["total_mb"] * 100.0, 1) if mem.get("total_mb") else None,
            "speed_spark": [[t, v] for t, v in spark],
            "alerts": alerts,
            "alerts_count": len([a for a in alerts if a["level"] == "danger"]),
        }


class MonitorRegistry:
    def __init__(self, app_cfg: AppConfig, writer=None):
        self.app_cfg = app_cfg
        self.writer = writer
        self.monitors: Dict[str, HostMonitor] = {}
        self._lock = asyncio.Lock()
        for h in app_cfg.hosts:
            self.monitors[h.id] = HostMonitor(h, app_cfg.global_cfg, writer)

    async def start(self) -> None:
        for m in self.monitors.values():
            await m.start()

    async def stop(self) -> None:
        for m in self.monitors.values():
            await m.stop()

    def get(self, host_id: str) -> Optional[HostMonitor]:
        return self.monitors.get(host_id)

    def list(self) -> List[Dict[str, Any]]:
        return [m.portal_summary() for m in self.monitors.values()]

    # ------------------------------------------------------------------
    # 运行时增删（主机管理 CRUD 触发）
    # ------------------------------------------------------------------
    async def add_host(self, cfg: "HostConfig") -> HostMonitor:
        """新增主机监控（幂等：已存在则先停旧再启新）。"""
        async with self._lock:
            old = self.monitors.pop(cfg.id, None)
            if old is not None:
                await old.stop()
            m = HostMonitor(cfg, self.app_cfg.global_cfg, self.writer)
            self.monitors[cfg.id] = m
            await m.start()
            log.info("HostMonitor 动态添加: %s", cfg.id)
            return m

    async def remove_host(self, host_id: str) -> bool:
        """移除主机监控；返回是否移除成功。"""
        async with self._lock:
            m = self.monitors.pop(host_id, None)
            if m is None:
                return False
            await m.stop()
            log.info("HostMonitor 动态移除: %s", host_id)
            return True

    async def restart_host(self, cfg: "HostConfig") -> HostMonitor:
        """监控配置变更后重建（等价 remove + add，持锁保证原子）。"""
        async with self._lock:
            old = self.monitors.pop(cfg.id, None)
            if old is not None:
                await old.stop()
            m = HostMonitor(cfg, self.app_cfg.global_cfg, self.writer)
            self.monitors[cfg.id] = m
            await m.start()
            log.info("HostMonitor 动态重建: %s", cfg.id)
            return m
