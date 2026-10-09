"""网关路由决策：三档策略 + 健康摘除 + 主机排除（读 MonitorRegistry 实时快照）。

三档策略（详见 docs/07 §4）：
- 档1 模型亲和（默认开）：按 model 找"装了它且在线"的主机；
  档1 关 = 模型名不参与路由，请求走兜底（default 主机 / 任意在线）
- 档2 同模型副本 LB（默认关，仅档1 开时有效）：同一模型多台主机按生成速度分流
- 档3 跨模型降级（默认关，仅档1 开时有效）：目标模型不可用时按显式 fallback_rules 降级，
  透明标注 degraded（绝不静默换模型）

语义（docs/07 §13 验收）：
- 档1 开 + 指定了 model 且无匹配：默认明确报错（不静默切）；档3 开启且有规则才降级。
- 未指定 model / 档1 关：default 兜底 → 任意在线。
- 被排除主机（主机详情「网关」Tab 开关）对网关完全不可见。
"""
import fnmatch
import logging
import random
from typing import Optional

log = logging.getLogger("llamalens.gateway.routing")


def _model_name(mon) -> str:
    try:
        model = ((mon.snapshot().get("llama") or {}).get("model")) or {}
        return (model.get("name") or model.get("path") or "").strip()
    except Exception:
        return ""


def _is_online(mon) -> bool:
    try:
        return bool((mon.snapshot().get("llama") or {}).get("online"))
    except Exception:
        return False


def _speed(mon) -> float:
    """生成速度（档2 LB 分流依据；拿不到按 0）。"""
    try:
        return float((mon.snapshot().get("llama") or {}).get("gen_speed_tps") or 0.0)
    except Exception:
        return 0.0


def _match(mon, model: str) -> bool:
    """客户端 model 名 vs 主机已加载模型名（basename 相等或包含，忽略大小写）。"""
    if not model:
        return False
    name = _model_name(mon)
    if not name:
        return False
    base = name.rsplit("/", 1)[-1]
    m = model.lower()
    return m == base.lower() or m in base.lower() or base.lower() in m


def _find_by_name(registry, name: str):
    """按主机 id 或 name 查找（显式主机前缀 / default 兜底用）。"""
    if not name:
        return None
    name = name.strip()
    for mon in registry.monitors.values():
        if mon.cfg.id == name or mon.cfg.name == name:
            return mon
    return None


def _pick(matched: list, lb: bool):
    """档2 LB：多台同模型主机按生成速度分流（加权随机：速度越快分到的请求越多）；
    速度均未知时均匀随机；LB 关则取第一台。"""
    if lb and len(matched) > 1:
        weights = [_speed(m) for m in matched]
        if all(w <= 0 for w in weights):
            return random.choice(matched)
        return random.choices(matched, weights=weights, k=1)[0]
    return matched[0]


def _rule_matches(rule_model: str, model_base: str) -> bool:
    """降级规则匹配：精确（忽略大小写）或 * 通配。"""
    if not rule_model:
        return False
    return fnmatch.fnmatch(model_base.lower(), str(rule_model).lower())


def route(model: str, registry, default_host: str = "", excluded=None, strategy=None):
    """返回 (mon, new_model, explicit, degraded_reason)。mon 为 None 表示无可用主机。

    degraded_reason 非 None 表示发生了跨模型降级（响应须透明标注）。
    """
    strategy = strategy or {}
    affinity = bool(strategy.get("affinity", True))
    same_model_lb = bool(strategy.get("same_model_lb", False))
    cross_model_fallback = bool(strategy.get("cross_model_fallback", False))
    fallback_rules = strategy.get("fallback_rules") or []
    excluded = excluded or set()
    monitors = [m for m in (registry.monitors.values() if registry else [])
                if m.cfg.id not in excluded]
    online = [m for m in monitors if _is_online(m)]

    # 1) 显式主机前缀（ai.lan/xxx）→ 该主机，new_model 为斜杠后部分
    effective_model = model
    if isinstance(model, str) and "/" in model:
        head, _, tail = model.partition("/")
        head = head.strip()
        if head:
            mon = _find_by_name(registry, head)
            if mon is not None and mon.cfg.id not in excluded:
                return mon, tail, True, None
            # 显式主机被排除/不存在：用斜杠后的模型部分继续路由
            effective_model = tail

    # 2) 档1 模型亲和（档2 开启时同模型多副本按速度分流）
    #    档1 关：模型名不参与路由（不做匹配）→ 直接走兜底（default 主机 / 任意在线）
    model_unmatched = False
    if affinity and effective_model:
        matched = [m for m in online if _match(m, effective_model)]
        if matched:
            return _pick(matched, same_model_lb), effective_model, False, None
        model_unmatched = True

    # 3) 档3 跨模型降级：仅档1 开且模型无匹配时；须显式配置 fallback_rules，命中后透明标注
    if model_unmatched and cross_model_fallback:
        model_base = effective_model.rsplit("/", 1)[-1].strip()
        for rule in fallback_rules:
            if not isinstance(rule, dict):
                continue
            if not _rule_matches(rule.get("model"), model_base):
                continue
            for fb in (rule.get("fallback") or []):
                fb_matched = [m for m in online if _match(m, fb)]
                if fb_matched:
                    return (_pick(fb_matched, same_model_lb), fb, False,
                            "fallback: %s -> %s" % (model_base, fb))

    # 4) 兜底：default 主机 → 任意在线
    #    覆盖：未指定 model；档1 关（模型名被忽略）。
    #    档1 开 + 模型无匹配（model_unmatched）不走兜底：不静默换模型（docs/07 §13）。
    if not model_unmatched:
        if default_host:
            d = _find_by_name(registry, default_host)
            if d is not None and _is_online(d) and d.cfg.id not in excluded:
                return d, effective_model, False, None
        if online:
            return online[0], effective_model, False, None

    # 指定了 model 但无匹配且无兜底：明确报错（不静默切模型，docs/07 §13）
    return None, effective_model, False, None


def upstream_url(mon) -> str:
    """llama-server 地址：http://{llama.host}:{llama.port}。"""
    llama = mon.cfg.llama
    return "http://%s:%d" % (llama.host, llama.port)
