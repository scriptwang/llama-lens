"""P1-1 Playground：面板代理 llama-server /v1/chat/completions（SSE 流式）+ 性能指标。

- GET  /api/hosts/{host_id}/playground  Endpoint 信息卡（OpenAI 兼容地址 + 模型名 + 在线状态）
- POST /api/hosts/{host_id}/chat        SSE 代理：转发增量 content，末尾附 metrics 事件
  （ttft_ms / total_ms / tokens / tps）；失败发 error 事件，绝不影响监控主流程。
"""
import asyncio
import json
import time
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .api import _monitor
from .ctl.routers.auth import get_current_user

router = APIRouter(prefix="/api/hosts", tags=["playground"])

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 600.0   # 长生成读超时（秒）
MAX_MESSAGES = 200     # 单次请求消息条数上限


class ChatReq(BaseModel):
    messages: List[dict]
    temperature: float = 0.7
    top_p: float = 1.0
    top_k: int = 40        # 0 = 不启用
    min_p: float = 0.0     # 0 = 不启用
    repeat_penalty: float = 1.1
    max_tokens: int = 0  # 0 = 不限制


def _sse(event: Optional[str], data: dict) -> str:
    out = ""
    if event:
        out += "event: " + event + "\n"
    out += "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
    return out


@router.get("/{host_id}/playground")
def playground_info(host_id: str, request: Request, user: str = Depends(get_current_user)):
    """Endpoint 信息：OpenAI 兼容地址 + 当前模型名 + 在线状态"""
    mon = _monitor(request, host_id)
    cfg = mon.cfg
    ll = mon.snapshot().get("llama") or {}
    model = ll.get("model") or {}
    return {
        "endpoint": "http://%s:%d/v1" % (cfg.llama.host, cfg.llama.port),
        "model": model.get("name") or "",
        "online": bool(ll.get("online")),
    }


@router.post("/{host_id}/chat")
async def chat(host_id: str, req: ChatReq, request: Request, user: str = Depends(get_current_user)):
    """SSE 代理 llama-server 聊天补全。不传 model 字段（llama-server 默认用已加载模型，
    避免名称不匹配 404）。"""
    if not req.messages or len(req.messages) > MAX_MESSAGES:
        return StreamingResponse(
            iter([_sse("error", {"msg": "messages 为空或超过 %d 条" % MAX_MESSAGES})]),
            media_type="text/event-stream")
    mon = _monitor(request, host_id)
    cfg = mon.cfg
    base = "http://%s:%d" % (cfg.llama.host, cfg.llama.port)
    body = {
        "messages": req.messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "min_p": req.min_p,
        "repeat_penalty": req.repeat_penalty,
    }
    if req.max_tokens > 0:
        body["max_tokens"] = req.max_tokens

    async def gen():
        start = time.time()
        ttft: Optional[float] = None
        delta_count = 0
        usage_tokens: Optional[int] = None
        try:
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT)) as client:
                async with client.stream("POST", base + "/v1/chat/completions", json=body) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                        yield _sse("error", {"msg": "llama-server 返回 %d：%s" % (resp.status_code, detail)})
                        return
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
                            reasoning = delta.get("reasoning_content")
                            content = delta.get("content")
                            # 思考 token（reasoning_content）：转发给前端展示，并计入 TTFT/tps。
                            # 否则思考阶段被丢弃 → TTFT 把思考时间算进去（虚高）、
                            # tps = (思考+回答 tokens) / 仅回答时间（虚高，与监控页不一致）。
                            if reasoning:
                                if ttft is None:
                                    ttft = time.time() - start
                                delta_count += 1
                                yield _sse(None, {"reasoning": reasoning})
                            if content:
                                if ttft is None:
                                    ttft = time.time() - start
                                delta_count += 1
                                yield _sse(None, {"choices": [{"delta": {"content": content}}]})
            total = time.time() - start
            tokens = usage_tokens if usage_tokens is not None else delta_count
            gen_time = total - (ttft or 0)
            tps = tokens / gen_time if tokens and gen_time > 0 else 0.0
            yield _sse("metrics", {
                "ttft_ms": round(ttft * 1000) if ttft is not None else None,
                "total_ms": round(total * 1000),
                "tokens": tokens,
                "tps": round(tps, 1),
            })
        except httpx.ConnectError:
            yield _sse("error", {"msg": "无法连接 llama-server（%s），服务可能未启动" % base})
        except httpx.ReadTimeout:
            yield _sse("error", {"msg": "读取超时（生成时间过长？）"})
        except httpx.HTTPError as e:
            yield _sse("error", {"msg": "代理请求失败：%s" % e})
        except Exception as e:  # 兜底：代理异常不冒泡到全局 handler
            yield _sse("error", {"msg": "代理错误：%s" % e})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ---------------------------------------------------------------------------
# 简单压测（P1-1 可选项）：N 并发短请求 → 聚合吞吐 + TTFT/延迟分位
# ---------------------------------------------------------------------------

STRESS_READ_TIMEOUT = 300.0   # 单请求读超时（秒）


class StressReq(BaseModel):
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


@router.post("/{host_id}/stress")
async def stress(host_id: str, req: StressReq, request: Request,
                 user: str = Depends(get_current_user)):
    """简单压测：并发 N 路短请求打满推理服务，SSE 流式回报进度与汇总。

    - progress：每完成一个请求发一条（done/total/ok/fail + 该请求 ttft/total/tokens/error）
    - summary：全部完成后发聚合指标（聚合/解码吞吐、TTFT/延迟 P50/P99、总 tokens、耗时）
    - 客户端断开（停止按钮）→ 取消全部在途请求，绝不影响监控主流程。
    """
    # 参数限幅：防误配置打爆推理服务
    concurrency = max(1, min(int(req.concurrency or 4), 16))
    total = max(1, min(int(req.count or 8), 100))
    max_tokens = max(16, min(int(req.max_tokens or 64), 512))
    prompt = (req.prompt or "").strip()[:500] or "用一句话介绍你自己。"
    temperature = max(0.0, min(float(req.temperature or 0.7), 2.0))

    mon = _monitor(request, host_id)
    cfg = mon.cfg
    base = "http://%s:%d" % (cfg.llama.host, cfg.llama.port)
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async def gen():
        if not (mon.snapshot().get("llama") or {}).get("online"):
            yield _sse("error", {"msg": "llama 离线，无法压测"})
            return
        sem = asyncio.Semaphore(concurrency)
        results: List[dict] = []
        tasks: List[asyncio.Task] = []

        async def one(client: httpx.AsyncClient) -> dict:
            async with sem:
                start = time.time()
                ttft: Optional[float] = None
                delta_count = 0
                usage_tokens: Optional[int] = None
                try:
                    async with client.stream("POST", base + "/v1/chat/completions", json=body) as resp:
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
            # 解码吞吐：与监控页 gen_speed 同口径（剔除 prefill/TTFT），便于直接对比
            gen_secs = 0.0
            gen_tokens = 0
            for r in ok_results:
                if r.get("total_ms") is not None and r.get("ttft_ms") is not None:
                    gen_secs += max(0.0, (r["total_ms"] - r["ttft_ms"]) / 1000.0)
                    gen_tokens += r.get("tokens") or 0
            decode_tps = round(gen_tokens / gen_secs, 1) if gen_secs > 0 else None
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
