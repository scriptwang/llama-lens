"""HostMonitor / MonitorRegistry。

- HostMonitor：每台主机一个独立监控单元（采集 + 缓冲 + 事件 + 快照），一台故障不影响其他主机。
- 1s tick：速度来源优先级（日志 tg_3s > /slots 差分）→ 写 llama 环形缓冲 → 生成快照（含 alerts）。
- MonitorRegistry：管理所有 HostMonitor，提供门户摘要。
- 兼容 Python 3.9。
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Union

from .alerts import evaluate_alerts
from .config import AppConfig, GlobalConfig, HostConfig
from .events import EventDetector
from .diff import DiffEngine
from .llama_flags import parse_cmdline
from .store import RingBuffer, downsample
from .pollers.llama_api import LlamaPoller
from .pollers.log_poller import LogPoller
from .pollers.ssh_conn import SshConnection
from .pollers.ssh_host import SshPoller
from .pollers.vllm_api import VllmPoller

log = logging.getLogger("llamalens.monitor")

HOST_SERIES = ("cpu", "mem_used", "mem_buff_cache", "swap_used",
               "net_rx", "net_tx", "proc_cpu", "load_1", "load_5", "load_15")
GPU_PREFIXES = ("gpu_util_", "gpu_mem_", "gpu_temp_", "gpu_power_")


class HostMonitor:
    def __init__(self, cfg: HostConfig, global_cfg: GlobalConfig):
        self.cfg = cfg
        self.global_cfg = global_cfg
        self.events = EventDetector()
        self.diff = DiffEngine()
        self.ring_llama = RingBuffer(global_cfg.llama_points)
        self.ring_host = RingBuffer(global_cfg.host_points)
        self.ssh = SshConnection(cfg.ssh, self.events, cfg.id)
        # 支持多端口：为每个 LlamaCfg 创建 LlamaPoller
        self.llamas: List[LlamaPoller] = []
        for llama_cfg in cfg.llama:
            # 创建临时 HostConfig 副本，覆盖 llama 字段
            import copy
            temp_cfg = copy.copy(cfg)
            temp_cfg.llama = llama_cfg  # 替换为单个 LlamaCfg
            self.llamas.append(LlamaPoller(temp_cfg, self.events))
        self.ssh_poller = SshPoller(cfg, self.ssh, self.diff, self.ring_host, self.events)
        self.log_poller = LogPoller(cfg, self.ssh, self.events, self.ring_llama)
        # vLLM /metrics 采集器（可选，仅在配置了 vllm 时启用）
        self.vllm = VllmPoller(cfg, self.events) if cfg.vllm else None
        self._tasks: List[asyncio.Task] = []
        self._snapshot: Optional[Dict[str, Any]] = None
        self._stopped = False
        self._last_cmdline: Optional[str] = None
        self._flags: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    async def start(self) -> None:
        log.info("[%s] HostMonitor 启动", self.cfg.id)
        poller_tasks = [
            asyncio.create_task(llama.start()) for llama in self.llamas
        ]
        if self.vllm is not None:
            poller_tasks.append(asyncio.create_task(self.vllm.start()))
        self._tasks = poller_tasks + [
            asyncio.create_task(self.ssh_poller.start()),
            asyncio.create_task(self.log_poller.start()),
            asyncio.create_task(self._tick_loop()),
        ]

    async def stop(self) -> None:
        self._stopped = True
        for llama in self.llamas:
            llama.stop()
        if self.vllm is not None:
            self.vllm.stop()
        self.ssh_poller.stop()
        self.log_poller.stop()
        for t in self._tasks:
            t.cancel()
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
                gen, prompt = self._speeds()
                # 离线时写 None（未知）而非 0，历史曲线出现断点而不是假零线
                online = any(llama.state.get("online") for llama in self.llamas)
                self.ring_llama.push("gen_speed", now, gen if online else None)
                self.ring_llama.push("prompt_speed", now, prompt if online else None)
                # vLLM MTP 接受率写入环形缓冲（供历史曲线使用）
                if self.vllm is not None and self.vllm.state.get("online"):
                    mtp_rate = self.vllm.state.get("mtp_acceptance_rate")
                    self.ring_llama.push("mtp_acceptance", now, mtp_rate)
                self._snapshot = self._build_snapshot(gen, prompt)
                # 上下文占用 1s 采样（API 实时值优先、日志兜底，取自合并后快照）；
                # 任务结束点由 LogPoller 另行写入（权威值），离线写 None 形成断点
                ctx_used = (self._snapshot["llama"]["log"].get("context") or {}).get("used")
                self.ring_llama.push("ctx_used", now, ctx_used if online else None)
            except Exception:
                log.exception("[%s] tick 失败", self.cfg.id)

    def _speeds(self):
        """速度来源优先级：日志 tg_3s / prompt 行 > vLLM total_prompt_tps > /slots 差分。合并所有端口。"""
        gen_total = 0.0
        prompt_total = 0.0
        for llama in self.llamas:
            state = llama.state
            if state.get("online"):
                gen_total += state.get("gen_speed_tps") or 0.0
                prompt_total += state.get("prompt_speed_tps") or 0.0
        gen = gen_total
        prompt = prompt_total
        # vLLM 速度源（当 llama 端口未在线时作为主要速度源）
        if self.vllm is not None and self.vllm.state.get("online"):
            vllm_gen = self.vllm.state.get("total_gen_tps") or 0.0
            vllm_prompt = self.vllm.state.get("total_prompt_tps") or 0.0
            if vllm_gen > 0:
                gen = vllm_gen  # vLLM 生成速度优先
            if vllm_prompt > 0:
                prompt = vllm_prompt  # vLLM prefill 速度优先
        logst = self.log_poller.state
        st = logst.get("state") or {}
        if logst.get("available"):
            if st.get("phase") == "decoding" and st.get("tg_3s_tps") is not None:
                gen = st["tg_3s_tps"]
            if st.get("phase") == "prompt_processing" and st.get("prompt_speed_tps") is not None:
                prompt = st["prompt_speed_tps"]
        return gen, prompt

    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        if self._snapshot is None:
            gen, prompt = self._speeds()
            self._snapshot = self._build_snapshot(gen, prompt)
        return self._snapshot

    def _build_snapshot(self, gen: float, prompt: float) -> Dict[str, Any]:
        now = time.time()
        # 合并所有 llama poller 的状态
        all_models = []
        all_slots = []
        all_online = False
        for llama in self.llamas:
            state = llama.state
            if state.get("online"):
                all_online = True
            model = dict(state.get("model") or {})
            if model:
                all_models.append(model)
            all_slots.extend(state.get("slots", []))
        
        logst = self.log_poller.state
        hm = self.ssh_poller.metrics
        st = logst.get("state") or {}

        source = "log" if (logst.get("available") and (
            (st.get("phase") == "decoding" and st.get("tg_3s_tps") is not None) or
            (st.get("phase") == "prompt_processing" and st.get("prompt_speed_tps") is not None)
        )) else "api"

        # 命令行和 flags 取第一个模型的
        cmdline = (hm.get("process") or {}).get("cmdline", "")
        if cmdline != self._last_cmdline:
            self._last_cmdline = cmdline
            self._flags = parse_cmdline(cmdline)
        flags = self._flags
        sizes = hm.get("_model_sizes") or {}

        host_metrics = {k: v for k, v in hm.items() if k != "_model_sizes"}
        if isinstance(host_metrics.get("process"), dict):
            host_metrics["process"] = dict(host_metrics["process"])
            host_metrics["process"]["flags"] = flags

        # 上下文：API 实时值（slot）优先，日志（任务结束行）兜底。
        log_snap = dict(logst)
        ctx = dict(logst.get("context") or {})
        # 取第一个在线 poller 的上下文
        for llama in self.llamas:
            api_ctx = llama.state.get("ctx") or {}
            if api_ctx.get("total"):
                ctx["total"] = api_ctx["total"]
            if api_ctx.get("used") is not None:
                ctx["used"] = api_ctx["used"]
            if ctx.get("used") is not None and ctx.get("total"):
                ctx["pct"] = round(ctx["used"] / ctx["total"] * 100.0, 1)
                ctx["remaining"] = max(0, ctx["total"] - ctx["used"])
            break
        log_snap["context"] = ctx

        # vLLM /metrics 状态（可选）
        vllm_state = None
        if self.vllm is not None:
            v = self.vllm.state
            # vLLM 在线则合并 online 状态（与 llama 独立判断）
            vllm_state = {k: v.get(k) for k in (
                "online", "gpu_cache_pct", "cpu_cache_pct",
                "running_requests", "waiting_requests",
                "prompt_tokens_total", "generation_tokens_total",
                "preemptions_total", "last_poll_ts",
                # 速度统计（总差分）
                "total_gen_tps", "total_prompt_tps",
                # 速度统计（请求级平均）
                "avg_gen_tps", "avg_prompt_tps",
                # 每个请求的统计
                "requests",
                # MTP / 投机解码统计
                "mtp_acceptance_rate", "mtp_accepted", "mtp_generated",
                "mtp_mean_len", "mtp_spec_decode_tps",
                # 详细配置
                "model_id", "model_path", "max_model_len",
                "allow_sampling", "allow_logprobs", "allow_fine_tuning",
                "prefix_caching", "kv_cache_dtype", "block_size",
                "gpu_mem_utilization", "num_gpu_blocks",
                "mamba_cache_dtype", "mamba_cache_mode",
                "sliding_window", "engine_state",
            )}

        snap = {
            "ts": now,
            "host": {"id": self.cfg.id, "name": self.cfg.name},
            "llama": {
                "online": all_online,
                "models": all_models,  # 支持多模型
                "gen_speed_tps": round(gen, 2),
                "prompt_speed_tps": round(prompt, 2),
                "speed_source": source,
                "log": log_snap,
                "slots": all_slots,  # 合并所有端口的 slots
            },
            "vllm": vllm_state,
            "host_metrics": host_metrics,
            "events": self.events.list(50),
        }
        snap["alerts"] = evaluate_alerts(self.cfg.thresholds, snap["llama"],
                                         host_metrics, log_snap)
        # 阈值穿越事件（级别变化：升级/恢复）
        self.events.check_alerts(snap["alerts"])
        return snap

    # ------------------------------------------------------------------
    def history(self, window_s: int) -> Dict[str, Any]:
        now = time.time()
        series: Dict[str, Any] = {}

        def add(ring: RingBuffer, name: str) -> None:
            pts = downsample(ring.window(name, window_s, now), 600)
            if pts:
                series[name] = {"ts": [t for t, _ in pts], "values": [v for _, v in pts]}

        for name in ("gen_speed", "prompt_speed", "ctx_used", "mtp_acceptance"):
            add(self.ring_llama, name)
        for name in HOST_SERIES:
            add(self.ring_host, name)
        for name in self.ring_host.names():
            if name.startswith(GPU_PREFIXES):
                add(self.ring_host, name)
        return {"window": window_s, "series": series}

    def events_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.events.list(limit)

    # ------------------------------------------------------------------
    def portal_summary(self) -> Dict[str, Any]:
        snap = self.snapshot()
        ll = snap["llama"]
        hm = snap["host_metrics"]
        models = ll.get("models") or []
        if not models and ll.get("model"):
            models = [ll["model"]]
        
        # 添加 vLLM 模型
        vllm = snap.get("vllm")
        if vllm and vllm.get("online"):
            vllm_model = {
                "name": vllm.get("model_id", vllm.get("model_name", "")),
                "n_params": None,
                "engine": "vllm",
            }
            models.append(vllm_model)
        
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
            "llama_ok": ll.get("online", False),
            "vllm_online": bool(vllm and vllm.get("online")) if vllm else None,
            "ssh_ok": hm.get("reachable", False),
            "models": models,  # 多模型列表（llama-server + vLLM）
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
    def __init__(self, app_cfg: AppConfig):
        self.app_cfg = app_cfg
        self.monitors: Dict[str, HostMonitor] = {}
        for h in app_cfg.hosts:
            self.monitors[h.id] = HostMonitor(h, app_cfg.global_cfg)

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

    async def add_host(self, host_cfg: HostConfig) -> None:
        """运行时添加主机。"""
        if host_cfg.id in self.monitors:
            raise ValueError("host id 已存在: %s" % host_cfg.id)
        monitor = HostMonitor(host_cfg, self.app_cfg.global_cfg)
        self.monitors[host_cfg.id] = monitor
        await monitor.start()
        # 更新 app_cfg
        self.app_cfg.hosts.append(host_cfg)
        log.info("[%s] 主机已添加并开始监控", host_cfg.id)

    async def remove_host(self, host_id: str) -> None:
        """运行时删除主机。"""
        if host_id not in self.monitors:
            raise ValueError("host id 不存在: %s" % host_id)
        monitor = self.monitors.pop(host_id)
        await monitor.stop()
        # 从 app_cfg 移除
        self.app_cfg.hosts = [h for h in self.app_cfg.hosts if h.id != host_id]
        log.info("[%s] 主机已删除并停止监控", host_id)
