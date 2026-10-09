"""Engine 适配器基类：屏蔽推理引擎（llama.cpp / SGLang / 未来 vLLM）差异。

HostMonitor 只消费这里的统一接口：
- start()/stop()：采集生命周期（start 为长驻协程，由 HostMonitor 以 task 方式运行）
- online：引擎是否在线
- speeds() → (gen_tps, prompt_tps, source)
- build_block(host_metrics, gen, prompt, source) → 快照 engine/llama 块（统一 dict）
- ring_extras() → 引擎专属时序项，如 {"accept_length": 3.5}
- portal_extra(block) → 门户卡片附加字段
- process_flags → 进程命令行解析结果（注入 host_metrics.process.flags）

兼容 Python 3.9。
"""
from typing import Any, Dict, Optional, Tuple


class EngineAdapter:
    type_name = ""
    display_name = ""

    def __init__(self, cfg, events, ssh, ring):
        self.cfg = cfg
        self.events = events
        self.ssh = ssh
        self.ring = ring

    @property
    def online(self) -> bool:
        return False

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    def speeds(self) -> Tuple[float, float, str]:
        """(gen_speed_tps, prompt_speed_tps, source)；source ∈ {"log", "api"}。"""
        return 0.0, 0.0, "api"

    def ring_extras(self) -> Optional[Dict[str, float]]:
        """本引擎写入 llama 环形缓冲的附加时序项（无则 None）。"""
        return None

    def build_block(self, host_metrics: Dict[str, Any], gen: float,
                    prompt: float, source: str) -> Dict[str, Any]:
        """快照中的引擎块。llama_cpp 引擎返回的 dict 与旧 snap["llama"] 完全同形；
        其他引擎额外携带引擎专属字段，snap["llama"] 由 HostMonitor 生成兼容副本。"""
        raise NotImplementedError

    def portal_extra(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """门户摘要（portal_summary）的附加字段。"""
        return {}

    @property
    def process_flags(self) -> Dict[str, Any]:
        return {}
