"""网关路由：显式前缀 + 三档策略（亲和/LB/跨模型降级）+ 健康摘除 + 主机排除。"""
from backend.gateway.routing import route, upstream_url


class _Llama:
    def __init__(self, host="127.0.0.1", port=9999):
        self.host, self.port = host, port


class _Cfg:
    def __init__(self, id, name):
        self.id, self.name, self.llama = id, name, _Llama()


class _Mon:
    def __init__(self, id, model, online=True, speed=0.0):
        self.cfg = _Cfg(id, id)
        self._model, self._online, self._speed = model, online, speed

    def snapshot(self):
        return {"llama": {"online": self._online,
                          "model": {"name": self._model},
                          "gen_speed_tps": self._speed}}


class _Reg:
    def __init__(self, mons):
        self.monitors = {m.cfg.id: m for m in mons}


def test_explicit_host_prefix():
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    mon, new_model, explicit, degraded = route("tvai/anything", reg, "ai")
    assert mon.cfg.id == "tvai"
    assert new_model == "anything"
    assert explicit is True
    assert degraded is None


def test_model_affinity():
    reg = _Reg([_Mon("ai", "qwen3.8-27b"), _Mon("tvai", "llama-8b")])
    mon, _, _, degraded = route("qwen3.8", reg, "ai")
    assert mon.cfg.id == "ai"
    assert degraded is None


def test_no_match_no_silent_switch():
    """指定 model 无匹配 + 档3 关 → 明确 None（不静默切模型，docs/07 §13）。"""
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    mon, _, _, _ = route("unknown-model", reg, "tvai")
    assert mon is None


def test_offline_no_match():
    """目标模型主机离线 + 档3 关 → None（不再落到任意在线）。"""
    reg = _Reg([_Mon("ai", "qwen3.8", online=False), _Mon("tvai", "llama")])
    mon, _, _, _ = route("qwen3.8", reg, "ai")
    assert mon is None


def test_no_model_default_fallback():
    """未指定 model：default 兜底 → 任意在线。"""
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    mon, _, _, _ = route("", reg, "tvai")
    assert mon.cfg.id == "tvai"
    mon, _, _, _ = route(None, _Reg([_Mon("ai", "qwen3.8")]), "")
    assert mon.cfg.id == "ai"


def test_affinity_off_ignores_model_falls_back():
    """档1 关：模型名不参与路由 → 走兜底（default 主机 / 任意在线），不再 503。"""
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    strategy = {"affinity": False}
    # 指定 model 也不做匹配：无 default → 任意在线（第一台）
    mon, _, _, _ = route("qwen3.8", reg, "", strategy=strategy)
    assert mon.cfg.id == "ai"
    # 有 default → default 主机
    mon, _, _, _ = route("qwen3.8", reg, "tvai", strategy=strategy)
    assert mon.cfg.id == "tvai"
    # 未指定 model：同样走兜底
    mon, _, _, _ = route("", reg, "tvai", strategy=strategy)
    assert mon.cfg.id == "tvai"


def test_affinity_on_no_match_never_falls_back():
    """档1 开 + 模型无匹配：绝不落到兜底主机（不静默换模型，docs/07 §13）。"""
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    mon, _, _, _ = route("unknown-model", reg, "tvai")
    assert mon is None


def test_same_model_lb_distributes_by_speed():
    """档2 LB：按生成速度分流（加权随机）——两台都要有流量，快主机占比更高。"""
    reg = _Reg([_Mon("ai", "qwen3.8", speed=10.0), _Mon("tvai", "qwen3.8", speed=42.0)])
    strategy = {"affinity": True, "same_model_lb": True}
    counts = {}
    for _ in range(400):
        mon, _, _, _ = route("qwen3.8", reg, "", strategy=strategy)
        counts[mon.cfg.id] = counts.get(mon.cfg.id, 0) + 1
    # 两台都有流量（不是永远选最快那台）
    assert counts.get("ai", 0) > 0 and counts.get("tvai", 0) > 0
    # 快主机（42 t/s）分到的流量明显多于慢主机（10 t/s）
    assert counts["tvai"] > counts["ai"]
    # 速度均未知：均匀随机（两台都有流量）
    reg0 = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "qwen3.8")])
    counts0 = {}
    for _ in range(200):
        mon, _, _, _ = route("qwen3.8", reg0, "", strategy=strategy)
        counts0[mon.cfg.id] = counts0.get(mon.cfg.id, 0) + 1
    assert counts0.get("ai", 0) > 0 and counts0.get("tvai", 0) > 0
    # LB 关：取第一台
    mon, _, _, _ = route("qwen3.8", reg, "", strategy={"affinity": True, "same_model_lb": False})
    assert mon.cfg.id == "ai"


def test_cross_model_fallback_off_no_degrade():
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    strategy = {"cross_model_fallback": False,
                "fallback_rules": [{"model": "deepseek*", "fallback": ["llama"]}]}
    mon, _, _, degraded = route("deepseek-v3", reg, "", strategy=strategy)
    assert mon is None
    assert degraded is None


def test_cross_model_fallback_degrades_with_marker():
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama-8b")])
    strategy = {"cross_model_fallback": True,
                "fallback_rules": [{"model": "deepseek*", "fallback": ["llama"]}]}
    mon, new_model, explicit, degraded = route("deepseek-v3", reg, "", strategy=strategy)
    assert mon.cfg.id == "tvai"
    assert new_model == "llama"
    assert explicit is False
    assert degraded and "deepseek-v3" in degraded and "llama" in degraded


def test_cross_model_fallback_wildcard_rule():
    reg = _Reg([_Mon("tvai", "llama-8b")])
    strategy = {"cross_model_fallback": True,
                "fallback_rules": [{"model": "qwen*", "fallback": ["llama"]}]}
    mon, _, _, degraded = route("qwen3.8", reg, "", strategy=strategy)
    assert mon is not None and degraded is not None


def test_no_available():
    reg = _Reg([_Mon("ai", "qwen3.8", online=False)])
    mon, _, _, _ = route("qwen3.8", reg, "ai")
    assert mon is None


def test_upstream_url():
    assert upstream_url(_Mon("ai", "x")) == "http://127.0.0.1:9999"


def test_excluded_host_skipped_affinity():
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "qwen3.8")])
    mon, _, _, _ = route("qwen3.8", reg, "ai", excluded={"ai"})
    assert mon.cfg.id == "tvai"


def test_excluded_host_skipped_default():
    reg = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama")])
    mon, _, _, _ = route("", reg, "ai", excluded={"ai"})
    assert mon.cfg.id == "tvai"


def test_excluded_host_skipped_explicit():
    reg = _Reg([_Mon("ai", "qwen3.8")])
    mon, _, _, _ = route("ai/whatever", reg, "", excluded={"ai"})
    assert mon is None


def test_all_excluded_no_available():
    reg = _Reg([_Mon("ai", "qwen3.8")])
    mon, _, _, _ = route("qwen3.8", reg, "ai", excluded={"ai"})
    assert mon is None
