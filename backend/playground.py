"""P1-1 Playground：面板代理 llama-server /v1/chat/completions（SSE 流式）+ 性能指标。

- GET  /api/hosts/{host_id}/playground  Endpoint 信息卡（OpenAI 兼容地址 + 模型名 + 在线状态）
- POST /api/hosts/{host_id}/chat        SSE 代理：转发增量 content，末尾附 metrics 事件
  （ttft_ms / total_ms / tokens / tps）；失败发 error 事件，绝不影响监控主流程。
"""
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
                            content = delta.get("content")
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
