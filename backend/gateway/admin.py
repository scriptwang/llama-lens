"""网关控制面：/api/gateway/*（JWT 鉴权，供面板 UI 用）。

- GET    /api/gateway/status：网关状态 + 接入信息
- GET    /api/gateway/keys：API key 列表
- POST   /api/gateway/keys：创建 key
- PATCH  /api/gateway/keys/{id}：启用/禁用/改额度
- DELETE /api/gateway/keys/{id}：删除 key
- GET    /api/gateway/requests：审计列表
- GET    /api/gateway/requests/{id}/trace：链路
"""
from typing import List, Optional

import asyncio
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..ctl.errors import ApiError, INTERNAL, VALIDATION_FAILED, ok
from ..ctl.routers.auth import get_current_user
from .routing import route as do_route, upstream_url

router = APIRouter(prefix="/api/gateway", tags=["gateway-admin"])
log = logging.getLogger("llamalens.gateway.admin")


def _gw(request: Request):
    return getattr(request.app.state, "gateway", None)


def _store(request: Request):
    gw = _gw(request)
    if gw is None:
        raise ApiError(INTERNAL, "gateway not initialized")
    return gw["store"]


def _online(mon) -> bool:
    try:
        return bool((mon.snapshot().get("llama") or {}).get("online"))
    except Exception:
        return False


class KeyCreateReq(BaseModel):
    name: str = ""
    quota_tokens: int = 0
    quota_cost: float = 0.0
    allowed_models: str = ""
    expires_at: str = ""


class KeyUpdateReq(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    quota_tokens: Optional[int] = None
    quota_cost: Optional[float] = None
    allowed_models: Optional[str] = None
    expires_at: Optional[str] = None


@router.get("/status")
def status(request: Request, user: str = Depends(get_current_user)):
    gw = _gw(request)
    if gw is None:
        return ok({"enabled": False, "reason": "gateway not initialized"})
    registry = gw.get("registry")
    mons = list(registry.monitors.values()) if registry else []
    base = str(request.base_url).rstrip("/")
    v1 = base + "/v1"
    writer = gw.get("writer")
    store = gw.get("store")
    return ok({
        "enabled": gw.get("enabled", True),
        "base_url": v1,
        "hosts_total": len(mons),
        "hosts_online": sum(1 for m in mons if _online(m)),
        "default_host": gw.get("default_host", ""),
        "strategy": gw.get("strategy", {}),
        "model_prices": gw.get("model_prices") or {},
        # 保留期 + 存储占用（前端据此对齐展示，避免提供超出保留期的选项）
        "audit_days": getattr(writer, "_audit_days", 0),
        "trace_days": getattr(writer, "_trace_days", 0),
        "db_size_mb": store.db_size_mb() if store else 0.0,
        "examples": {
            "curl": ('curl %s/chat/completions -H "Authorization: Bearer <API_KEY>" '
                     '-H "Content-Type: application/json" '
                     '-d \'{"model":"<model>","stream":true,'
                     '"messages":[{"role":"user","content":"hi"}]}\'') % v1,
        },
    })


@router.get("/keys")
def list_keys(request: Request, user: str = Depends(get_current_user)):
    return ok({"keys": _store(request).list_keys()})


@router.post("/keys")
def create_key(req: KeyCreateReq, request: Request, user: str = Depends(get_current_user)):
    from . import keys as keys_mod
    row = keys_mod.create_key(_store(request), req.name, req.quota_tokens,
                              req.quota_cost, req.allowed_models, req.expires_at)
    return ok({"key": row})


@router.patch("/keys/{key_id}")
def update_key(key_id: str, req: KeyUpdateReq, request: Request,
               user: str = Depends(get_current_user)):
    store = _store(request)
    row = store.get_key(key_id)
    if row is None:
        raise ApiError(VALIDATION_FAILED, "API key 不存在")
    if req.name is not None:
        row["name"] = req.name
    if req.enabled is not None:
        row["enabled"] = 1 if req.enabled else 0
    if req.quota_tokens is not None:
        row["quota_tokens"] = int(req.quota_tokens)
    if req.quota_cost is not None:
        row["quota_cost"] = float(req.quota_cost)
    if req.allowed_models is not None:
        row["allowed_models"] = req.allowed_models
    if req.expires_at is not None:
        row["expires_at"] = req.expires_at or None
    store.upsert_key((row["id"], row["key"], row["name"], row["enabled"],
                      row["quota_tokens"], row["quota_cost"], row["used_tokens"],
                      row["used_cost"], row["allowed_models"], row["expires_at"],
                      row["created_at"]))
    return ok({"key": store.get_key(key_id)})


@router.delete("/keys/{key_id}")
def delete_key(key_id: str, request: Request, user: str = Depends(get_current_user)):
    store = _store(request)
    if store.get_key(key_id) is None:
        raise ApiError(VALIDATION_FAILED, "API key 不存在")
    store.delete_key(key_id)
    return ok({"deleted": key_id})


class StrategyReq(BaseModel):
    affinity: Optional[bool] = None
    same_model_lb: Optional[bool] = None
    cross_model_fallback: Optional[bool] = None
    fallback_rules: Optional[list] = None


@router.patch("/strategy")
def set_strategy(req: StrategyReq, request: Request, user: str = Depends(get_current_user)):
    """路由策略动态开关：更新内存（立即生效）+ 持久化 gateway.db（重启保留）。"""
    gw = _gw(request)
    if gw is None:
        raise ApiError(INTERNAL, "网关未启用")
    strategy = dict(gw.get("strategy") or {})
    changed = []
    for field in ("affinity", "same_model_lb", "cross_model_fallback"):
        v = getattr(req, field)
        if v is not None:
            strategy[field] = bool(v)
            changed.append(field)
    if req.fallback_rules is not None:
        for rule in req.fallback_rules:
            if not isinstance(rule, dict) or not rule.get("model") or not rule.get("fallback"):
                raise ApiError(VALIDATION_FAILED, "fallback_rules 需为 [{model, fallback: []}]")
        strategy["fallback_rules"] = req.fallback_rules
        changed.append("fallback_rules")
    if not changed:
        raise ApiError(VALIDATION_FAILED, "无变更字段")
    gw["strategy"] = strategy
    gw["store"].set_kv("strategy", strategy)
    log.info("网关路由策略变更: %s (user=%s)", strategy, user)
    return ok({"strategy": strategy})


class PricesReq(BaseModel):
    model_prices: dict


@router.patch("/prices")
def set_prices(req: PricesReq, request: Request, user: str = Depends(get_current_user)):
    """模型单价（元/1k tokens）：更新内存（立即生效）+ 持久化 gateway.db（重启保留）。

    配置后请求费用才会计费，API Key 的「费用额度」才会生效。"""
    gw = _gw(request)
    if gw is None:
        raise ApiError(INTERNAL, "网关未启用")
    prices = {}
    for name, p in (req.model_prices or {}).items():
        try:
            pf = float(p)
        except (TypeError, ValueError):
            raise ApiError(VALIDATION_FAILED, "单价需为数字：%s" % name)
        if pf < 0:
            raise ApiError(VALIDATION_FAILED, "单价不能为负：%s" % name)
        prices[str(name)] = pf
    gw["model_prices"] = prices
    gw["store"].set_kv("model_prices", prices)
    log.info("网关模型单价变更: %s (user=%s)", prices, user)
    return ok({"model_prices": prices})


class HostGwReq(BaseModel):
    excluded: bool


@router.get("/hosts")
def list_gateway_hosts(request: Request, user: str = Depends(get_current_user)):
    """各主机在网关里的状态（在线 / 已排除 / 模型）。"""
    gw = _gw(request)
    if gw is None:
        return ok({"hosts": []})
    registry = gw.get("registry")
    excluded = gw.get("excluded") or set()
    out = []
    for mon in (registry.monitors.values() if registry else []):
        try:
            llama = mon.snapshot().get("llama") or {}
            model_name = (llama.get("model") or {}).get("name") or ""
        except Exception:
            model_name = ""
        out.append({
            "mid": mon.cfg.id,
            "name": mon.cfg.name,
            "online": _online(mon),
            "excluded": mon.cfg.id in excluded,
            "model_name": model_name,
        })
    return ok({"hosts": out})


@router.patch("/hosts/{host_id}")
def set_host_excluded(host_id: str, req: HostGwReq, request: Request,
                      user: str = Depends(get_current_user)):
    """主机纳入/排除网关：持久化到 hosts 表 + 更新内存路由集合。"""
    gw = _gw(request)
    if gw is None:
        raise ApiError(INTERNAL, "网关未启用")
    from ..ctl import database as ctl_db
    row = ctl_db.query_one("SELECT mid FROM hosts WHERE mid = ?", (host_id,))
    if row is None:
        raise ApiError(VALIDATION_FAILED, "主机不存在")
    ctl_db.execute("UPDATE hosts SET gateway_excluded = ? WHERE mid = ?",
                   (1 if req.excluded else 0, host_id))
    excluded = gw.setdefault("excluded", set())
    if req.excluded:
        excluded.add(host_id)
    else:
        excluded.discard(host_id)
    log.info("网关主机排除变更: %s excluded=%s (user=%s)", host_id, req.excluded, user)
    return ok({"mid": host_id, "excluded": bool(req.excluded)})


@router.get("/requests")
def list_requests(request: Request, user: str = Depends(get_current_user),
                  limit: int = 100, api_key_id: str = None, target_host: str = None,
                  degraded: int = None, model: str = None, status: int = None,
                  since: float = None):
    return ok({"requests": _store(request).query_requests(
        limit=limit, api_key_id=api_key_id, target_host=target_host, degraded=degraded,
        model=model, status=status, since_ts=since)})


@router.get("/stats")
def stats(request: Request, user: str = Depends(get_current_user),
          group_by: str = "key", days: int = 30, api_key_id: str = None):
    """成本/用量聚合（docs/07 §6.2 观测：成本统计）。group_by: key|model|day。"""
    store = _store(request)
    if store is None:
        return ok({"stats": []})
    days = max(1, min(int(days or 30), 365))
    since = time.time() - days * 86400
    rows = store.query_stats(group_by=group_by, since_ts=since, api_key_id=api_key_id)
    if group_by == "key":
        keys = {k["id"]: k for k in store.list_keys()}
        for r in rows:
            k = keys.get(r["group"])
            r["name"] = (k or {}).get("name") or r["group"] or "(无 key)"
    return ok({"stats": rows, "days": days})


@router.get("/requests/{request_id}/trace")
def trace(request_id: str, request: Request, user: str = Depends(get_current_user)):
    st = _store(request)
    return ok({"request_id": request_id,
               "request": st.get_request(request_id),
               "spans": st.query_spans(request_id)})


# ---------------------------------------------------------------------------
# 网关推理压测：经统一路由打到上游（合成负载，不写审计、不污染统计）
# ---------------------------------------------------------------------------

STRESS_READ_TIMEOUT = 300.0   # 单请求读超时（秒）


class StressReq(BaseModel):
    model: str = ""            # 网关模型名（host/model 可指定主机）
    concurrency: int = 4       # 并发路数（1-16）
    count: int = 8             # 总请求数（1-100）
    max_tokens: int = 64       # 每请求生成上限（16-512，短请求）
    prompt: str = ""           # 压测 prompt（≤500 字，缺省用内置短 prompt）
    temperature: float = 0.7


def _pct(sorted_vals: List[float], p: float):
    """分位数（nearest-rank，无第三方依赖）；p 为 0-100。空列表返回 None。"""
    if not sorted_vals:
        return None
    k = int(round(p / 100.0 * (len(sorted_vals) - 1)))
    return sorted_vals[max(0, min(len(sorted_vals) - 1, k))]


def _sse(event: Optional[str], data: dict) -> str:
    out = ""
    if event:
        out += "event: " + event + "\n"
    out += "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
    return out


@router.post("/stress")
async def stress(req: StressReq, request: Request,
                 user: str = Depends(get_current_user)):
    """网关推理压测：按当前路由策略选主机，并发短请求打满上游，SSE 回报进度与汇总。

    - progress：每完成一个请求发一条（done/total/ok/fail + 该请求 ttft/total/tokens/error）
    - summary：全部完成后发聚合指标（聚合/解码吞吐、TTFT/延迟 P50/P99、总 tokens、耗时、served_by）
    - 合成负载不写审计（不污染费用/统计）；客户端断开 → 取消全部在途请求。
    """
    gw = _gw(request)
    if gw is None:
        return StreamingResponse(
            iter([_sse("error", {"msg": "gateway not initialized"})]),
            media_type="text/event-stream")
    registry = gw.get("registry")
    default_host = gw.get("default_host", "")
    model = (req.model or "").strip()
    if not model:
        return StreamingResponse(
            iter([_sse("error", {"msg": "缺少 model 参数"})]),
            media_type="text/event-stream")

    concurrency = max(1, min(int(req.concurrency or 4), 16))
    total = max(1, min(int(req.count or 8), 100))
    max_tokens = max(16, min(int(req.max_tokens or 64), 512))
    prompt = (req.prompt or "").strip()[:500] or "用一句话介绍你自己。"
    temperature = max(0.0, min(float(req.temperature or 0.7), 2.0))

    strategy = gw.get("strategy")
    excluded = gw.get("excluded")

    def _route_once():
        return do_route(model, registry, default_host,
                        excluded=excluded, strategy=strategy)

    mon0, _, _, degraded_reason = _route_once()
    if mon0 is None:
        return StreamingResponse(
            iter([_sse("error", {"msg": "无可用主机（模型亲和无匹配；跨模型降级默认关闭，见集群页路由策略）"})]),
            media_type="text/event-stream")

    async def gen():
        sem = asyncio.Semaphore(concurrency)
        results: List[dict] = []
        tasks: List[asyncio.Task] = []

        async def one(client: httpx.AsyncClient) -> dict:
            async with sem:
                # 按请求路由（与真实流量一致：LB 开启时分布到多台主机）
                mon, new_model, _e, _d = _route_once()
                if mon is None:
                    return {"ok": False, "error": "无可用主机"}
                upstream = upstream_url(mon)
                host_id = mon.cfg.id
                body = {
                    "model": new_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                start = time.time()
                ttft: Optional[float] = None
                delta_count = 0
                usage_tokens: Optional[int] = None
                try:
                    async with client.stream("POST", upstream + "/v1/chat/completions",
                                             json=body) as resp:
                        if resp.status_code != 200:
                            detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                            return {"ok": False, "error": "HTTP %d %s" % (resp.status_code, detail)}
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                            except ValueError:
                                continue
                            usage = chunk.get("usage")
                            if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                                usage_tokens = int(usage["completion_tokens"])
                            for choice in chunk.get("choices") or []:
                                delta = (choice or {}).get("delta") or {}
                                if delta.get("reasoning_content") or delta.get("content"):
                                    if ttft is None:
                                        ttft = time.time() - start
                                    delta_count += 1
                    tokens = usage_tokens if usage_tokens is not None else delta_count
                    return {
                        "ok": True,
                        "host": host_id,
                        "ttft_ms": round(ttft * 1000) if ttft is not None else None,
                        "total_ms": round((time.time() - start) * 1000),
                        "tokens": tokens,
                    }
                except httpx.HTTPError as e:
                    return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
                except Exception as e:
                    return {"ok": False, "error": str(e)}

        try:
            t0 = time.time()
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=STRESS_READ_TIMEOUT)) as client:
                tasks = [asyncio.create_task(one(client)) for _ in range(total)]
                for fut in asyncio.as_completed(tasks):
                    r = await fut
                    results.append(r)
                    ok_n = sum(1 for x in results if x["ok"])
                    yield _sse("progress", {
                        "done": len(results), "total": total,
                        "ok": ok_n, "fail": len(results) - ok_n,
                        "ttft_ms": r.get("ttft_ms"),
                        "total_ms": r.get("total_ms"),
                        "tokens": r.get("tokens"),
                        "error": r.get("error"),
                    })
            wall = time.time() - t0
            ok_results = [r for r in results if r["ok"]]
            total_tokens = sum(r.get("tokens") or 0 for r in ok_results)
            ttfts = sorted(r["ttft_ms"] for r in ok_results if r.get("ttft_ms") is not None)
            latencies = sorted(r["total_ms"] for r in ok_results if r.get("total_ms") is not None)
            # 解码吞吐：与主机压测同口径（剔除 prefill/TTFT），便于横向对比
            gen_secs = 0.0
            gen_tokens = 0
            for r in ok_results:
                if r.get("total_ms") is not None and r.get("ttft_ms") is not None:
                    gen_secs += max(0.0, (r["total_ms"] - r["ttft_ms"]) / 1000.0)
                    gen_tokens += r.get("tokens") or 0
            decode_tps = round(gen_tokens / gen_secs, 1) if gen_secs > 0 else None
            host_counts = {}
            for r in ok_results:
                h = r.get("host") or "?"
                host_counts[h] = host_counts.get(h, 0) + 1
            served_by = ", ".join("%s×%d" % (h, n) for h, n in
                                  sorted(host_counts.items(), key=lambda x: -x[1]))
            yield _sse("summary", {
                "total": total,
                "ok": len(ok_results),
                "fail": total - len(ok_results),
                "total_tokens": total_tokens,
                "wall_s": round(wall, 1),
                "throughput_tps": round(total_tokens / wall, 1) if wall > 0 else 0.0,
                "decode_tps": decode_tps,
                "ttft_p50_ms": _pct(ttfts, 50),
                "ttft_p99_ms": _pct(ttfts, 99),
                "latency_p50_ms": _pct(latencies, 50),
                "latency_p99_ms": _pct(latencies, 99),
                "served_by": served_by,
                "degraded": bool(degraded_reason),
                "degraded_reason": degraded_reason or "",
            })
        except Exception as e:
            yield _sse("error", {"msg": "压测失败：%s" % e})
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
