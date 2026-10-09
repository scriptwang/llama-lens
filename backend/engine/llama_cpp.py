"""llama.cpp llama-server 引擎适配器。

包装现有 LlamaPoller（/slots /props /v1/models HTTP 采集）与 LogPoller
（journalctl 日志流解析），对外提供 EngineAdapter 统一接口。
采集逻辑不改动，只做接口搬运。兼容 Python 3.9。
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from ..llama_flags import parse_cmdline
from ..pollers.llama_api import LlamaPoller
from ..pollers.log_poller import LogPoller
from .base import EngineAdapter

log = logging.getLogger("llamalens.engine.llama_cpp")


class LlamaCppEngine(EngineAdapter):
    type_name = "llama_cpp"
    display_name = "llama.cpp"

    def __init__(self, cfg, events, ssh, ring):
        super().__init__(cfg, events, ssh, ring)
        self.llama = LlamaPoller(cfg, events)
        self.log_poller = LogPoller(cfg, ssh, events, ring)
        self._tasks = []
        self._last_cmdline: Optional[str] = None
        self._flags: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    @property
    def online(self) -> bool:
        return bool(self.llama.state.get("online"))

    @property
    def process_flags(self) -> Dict[str, Any]:
        return self._flags

    async def start(self) -> None:
        log.info("[%s] LlamaCppEngine 启动", self.cfg.id)
        self._tasks = [
            asyncio.create_task(self.llama.start()),
            asyncio.create_task(self.log_poller.start()),
        ]
        try:
            await asyncio.gather(*self._tasks)
        finally:
            self.llama.stop()
            self.log_poller.stop()
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []

    async def stop(self) -> None:
        self.llama.stop()
        self.log_poller.stop()

    # ------------------------------------------------------------------
    def speeds(self) -> Tuple[float, float, str]:
        """速度来源优先级：日志 tg_3s / prompt 行 > /slots 差分。"""
        llama = self.llama.state
        gen = llama.get("gen_speed_tps") or 0.0
        prompt = llama.get("prompt_speed_tps") or 0.0
        logst = self.log_poller.state
        st = logst.get("state") or {}
        source = "api"
        if logst.get("available"):
            if st.get("phase") == "decoding" and st.get("tg_3s_tps") is not None:
                gen = st["tg_3s_tps"]
                source = "log"
            if st.get("phase") == "prompt_processing" and st.get("prompt_speed_tps") is not None:
                prompt = st["prompt_speed_tps"]
                source = "log"
        return gen, prompt, source

    def build_block(self, host_metrics: Dict[str, Any], gen: float,
                    prompt: float, source: str) -> Dict[str, Any]:
        llama = self.llama.state
        logst = self.log_poller.state

        # 模型合并：/props + /v1/models + 命令行(mmproj) + ls -l(体积)
        model = dict(llama.get("model") or {})
        cmdline = (host_metrics.get("process") or {}).get("cmdline", "")
        if cmdline != self._last_cmdline:
            self._last_cmdline = cmdline
            self._flags = parse_cmdline(cmdline)
        flags = self._flags
        sizes = host_metrics.get("_model_sizes") or {}
        if model.get("path") and model.get("path") in sizes:
            model["file_size"] = sizes[model["path"]]
        mmproj = flags.get("mmproj")
        if mmproj:
            model["mmproj_path"] = mmproj
            if mmproj in sizes:
                model["mmproj_size"] = sizes[mmproj]

        # 上下文：API 实时值（slot）优先，日志（任务结束行）兜底。
        # 注意 logst 是 LogPoller 的活引用，合并结果必须放副本，不能改原 state。
        log_snap = dict(logst)
        log_snap.pop("task_history", None)  # 走 /tasks 专用端点，不进快照
        ctx = dict(logst.get("context") or {})
        api_ctx = llama.get("ctx") or {}
        if api_ctx.get("total"):
            ctx["total"] = api_ctx["total"]
        if api_ctx.get("used") is not None:
            ctx["used"] = api_ctx["used"]
        if ctx.get("used") is not None and ctx.get("total"):
            ctx["pct"] = round(ctx["used"] / ctx["total"] * 100.0, 1)
            ctx["remaining"] = max(0, ctx["total"] - ctx["used"])
        log_snap["context"] = ctx

        return {
            "type": self.type_name,
            "online": bool(llama.get("online")),
            "model": model,
            "gen_speed_tps": round(gen, 2),
            "prompt_speed_tps": round(prompt, 2),
            "speed_source": source,
            "log": log_snap,
            "slots": llama.get("slots", []),
            "ctx": ctx,
        }
