"""Engine 适配层工厂：HostConfig.engine.type → 对应 EngineAdapter。

新增引擎（如 vLLM）只需：实现 EngineAdapter 子类 + 在 _REGISTRY 注册。
兼容 Python 3.9。
"""
from .base import EngineAdapter
from .llama_cpp import LlamaCppEngine
from .sglang import SglangEngine

_REGISTRY = {
    "llama_cpp": LlamaCppEngine,
    "sglang": SglangEngine,
}


def create_engine(cfg, events, ssh, ring) -> EngineAdapter:
    etype = cfg.engine.type
    cls = _REGISTRY.get(etype)
    if cls is None:
        raise ValueError("未知引擎类型: %s（支持: %s）" % (etype, ", ".join(sorted(_REGISTRY))))
    return cls(cfg, events, ssh, ring)
