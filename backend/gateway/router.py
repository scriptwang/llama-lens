"""网关数据面：/v1/* 端点（API key 鉴权）。

- POST /v1/responses：规范化（修 Codex 500）+ SSE 透传 + 重试
- POST /v1/chat/completions：SSE 透传 + 重试
- GET  /v1/models：聚合所有在线后端模型清单（id 带主机前缀）
审计/链路/用量走 GatewayWriter 异步落库（不阻塞事件循环）。

流式透传注意：不能在 `async with client.stream()` 块内 return StreamingResponse
（会提前关闭 stream）。这里手动 __aenter__/__aexit__，让 stream 生命周期覆盖
整个响应发送期（gen 的 finally 里关闭）。
"""
import asyncio
import json
import logging
import random
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import keys as keys_mod
from .normalize import normalize_responses
from .routing import route as do_route, upstream_url

log = logging.getLogger("llamalens.gateway")

router = APIRouter(tags=["gateway-data"])

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 1800.0
RETRY_DELAY_CAP = 30.0
MAX_RETRIES = 5

# 不透传给上游的头（含 authorization：避免把面板 API key 泄露给 llama-server）
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
    "authorization",
}


def _gw(request: Request):
    return getattr(request.app.state, "gateway", None)


def _err(status: int, msg: str):
    return JSONResponse(status_code=status,
                        content={"error": {"code": status, "message": msg}})


def _backoff(attempt: int, base: float = 1.0) -> float:
    return min(RETRY_DELAY_CAP, base * (2 ** attempt)) + random.uniform(0, 0.5)


def _extract_api_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def _body_model(body: bytes):
    if body:
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                return data.get("model")
        except Exception:
            pass
    return None


def _auth(request: Request, body: bytes = None):
    """返回 (key_row, error_response, error_status)。error_response 非 None 时端点直接 return。

    body 非空时解析 model 做白名单校验（allowed_models）。
    配额用尽 → 429（docs/07 §5.1），其余鉴权失败 → 401。
    """
    gw = _gw(request)
    if gw is None:
        return None, _err(503, "gateway not initialized"), 503
    if not gw.get("enabled", True):
        return None, _err(503, "gateway disabled"), 503
    model = _body_model(body)
    try:
        row, err = keys_mod.validate(gw["store"], _extract_api_key(request), model)
    except Exception:
        log.exception("API key 校验失败（存储异常）")
        return None, _err(503, "gateway key store unavailable"), 503
    if err:
        code = 429 if err == keys_mod.ERR_QUOTA else 401
        return None, _err(code, err), code
    return row, None, 0


def _reject(request: Request, request_id: str, key_row, body: bytes, err_resp, status: int):
    """被拒绝请求（401/429/503）也进审计 + 链路（received/auth span）。

    旁路失败绝不影响拒绝响应本身（故障隔离）。"""
    try:
        gw = _gw(request)
        writer = gw.get("writer") if gw else None
        if writer is None:
            return
        try:
            err_msg = json.loads(err_resp.body())["error"]["message"]
        except Exception:
            err_msg = "rejected"
        model = _body_model(body)
        t0 = time.time()
        err_resp.headers["X-Request-Id"] = request_id
        spans = [
            (request_id, t0, "received", 0.0, 0.0, json.dumps(
                {"path": request.url.path, "model": model,
                 "ip": request.client.host if request.client else ""}, ensure_ascii=False)),
            (request_id, t0, "auth", 0.0, 0.0, json.dumps({"error": err_msg}, ensure_ascii=False)),
        ]
        _safe_record(gw, writer, request_id, key_row, request, model, "", "", 0,
                     status, 0, 0, None, 0.0, 0, err_msg, spans,
                     req_preview=_req_preview(body))
    except Exception:
        log.warning("旁路审计（reject）失败，不影响代理", exc_info=True)


class _SseUsage:
    """边转发边解析 SSE，提取 usage + 响应内容预览（不改变透传内容）。

    思考模型（如 Qwen3）会先输出 reasoning_content 再输出 content；
    max_tokens 不够时可能只有思考没有回复——两者都捕获，链路不留黑盒。"""
    __slots__ = ("buf", "prompt", "completion", "content", "reasoning")
    CAP = 4000
    REASONING_CAP = 2000

    def __init__(self):
        self.buf = b""
        self.prompt = 0
        self.completion = 0
        self.content = ""
        self.reasoning = ""

    def feed(self, chunk: bytes):
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            usage = obj.get("usage")
            if isinstance(usage, dict):
                if usage.get("prompt_tokens") is not None:
                    self.prompt = int(usage["prompt_tokens"])
                if usage.get("completion_tokens") is not None:
                    self.completion = int(usage["completion_tokens"])
            if obj.get("type") == "response.completed":
                u = ((obj.get("response") or {}).get("usage")) or {}
                if u.get("input_tokens") is not None:
                    self.prompt = int(u["input_tokens"])
                if u.get("output_tokens") is not None:
                    self.completion = int(u["output_tokens"])
            # 响应内容预览：chat delta.content / responses output_text.delta
            piece = None
            ch = obj.get("choices")
            if isinstance(ch, list) and ch and isinstance(ch[0], dict):
                d = ch[0].get("delta")
                if isinstance(d, dict):
                    if isinstance(d.get("content"), str):
                        piece = d["content"]
                    r = d.get("reasoning_content", d.get("reasoning"))
                    if isinstance(r, str) and r and len(self.reasoning) < self.REASONING_CAP:
                        self.reasoning = (self.reasoning + r)[:self.REASONING_CAP]
            elif obj.get("type") == "response.output_text.delta" and isinstance(obj.get("delta"), str):
                piece = obj["delta"]
            if piece and len(self.content) < self.CAP:
                self.content = (self.content + piece)[:self.CAP]


def _resp_headers(resp):
    out = {}
    for k, v in resp.headers.items():
        if k.lower() in HOP_BY_HOP:
            continue
        out[k] = v
    return out


def _calc_cost(model: str, prompt_tokens: int, completion_tokens: int, prices: dict) -> float:
    """token 单价口径（docs/07 §8.3 model_prices: {model: price_per_1k}）。

    按模型名匹配（basename 相等或包含，忽略大小写）；未配置价格记 0。
    """
    if not prices or not model:
        return 0.0
    base = model.rsplit("/", 1)[-1].strip().lower()
    price = None
    for name, p in prices.items():
        n = str(name).rsplit("/", 1)[-1].strip().lower()
        if not n:
            continue
        if base == n or n in base or base in n:
            try:
                price = float(p)
            except (TypeError, ValueError):
                price = None
            break
    if price is None or price <= 0:
        return 0.0
    return round((int(prompt_tokens or 0) + int(completion_tokens or 0)) / 1000.0 * price, 6)


def _json_usage(raw: bytes):
    """非流式 JSON 响应里的 usage（chat: prompt/completion_tokens；responses: input/output_tokens）。"""
    try:
        obj = json.loads(raw)
    except Exception:
        return 0, 0
    if not isinstance(obj, dict):
        return 0, 0
    u = obj.get("usage")
    if not isinstance(u, dict):
        return 0, 0
    p = u.get("prompt_tokens", u.get("input_tokens")) or 0
    c = u.get("completion_tokens", u.get("output_tokens")) or 0
    try:
        return int(p), int(c)
    except (TypeError, ValueError):
        return 0, 0


def _req_preview(body: bytes, cap: int = 2000) -> str:
    """请求内容预览：chat messages / responses input；解析失败退回原文。"""
    if not body:
        return ""
    try:
        data = json.loads(body)
    except Exception:
        return body.decode("utf-8", "replace")[:cap]
    if not isinstance(data, dict):
        return ""
    msgs = data.get("messages")
    if isinstance(msgs, list):
        parts = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = m.get("role") or "?"
            c = m.get("content")
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                text = " ".join(p.get("text", "") for p in c
                                if isinstance(p, dict) and p.get("type") == "text")
                if any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
                    text += " [图片]"
            else:
                text = ""
            parts.append("[%s] %s" % (role, text.strip()))
        return "\n".join(parts)[:cap]
    inp = data.get("input")
    if isinstance(inp, str):
        return inp[:cap]
    if isinstance(inp, list):
        parts = []
        for it in inp:
            if isinstance(it, str):
                parts.append(it)
            elif isinstance(it, dict):
                c = it.get("content")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    parts.append(" ".join(str(p.get("text", "")) for p in c
                                          if isinstance(p, dict) and p.get("text")))
                elif isinstance(it.get("text"), str):
                    parts.append(it["text"])
        return "\n".join(parts)[:cap]
    return ""


def _resp_preview(raw: bytes, cap: int = 4000) -> str:
    """非流式响应内容预览：chat choices[0].message.content / responses output。"""
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
    except Exception:
        return raw.decode("utf-8", "replace")[:cap]
    if not isinstance(obj, dict):
        return ""
    ch = obj.get("choices")
    if isinstance(ch, list) and ch and isinstance(ch[0], dict):
        msg = ch[0].get("message") or {}
        r = msg.get("reasoning_content", msg.get("reasoning"))
        c = msg.get("content")
        return _join_preview(
            (r[:2000] if isinstance(r, str) else ""),
            (c[:cap] if isinstance(c, str) else "")) or ""
    out = obj.get("output_text")
    if isinstance(out, str):
        return out[:cap]
    out = obj.get("output")
    if isinstance(out, list):
        parts = []
        for it in out:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("text"), str):
                parts.append(it["text"])
                continue
            c = it.get("content")
            if isinstance(c, list):
                parts.append(" ".join(str(p.get("text", "")) for p in c
                                       if isinstance(p, dict) and p.get("text")))
        if parts:
            return "\n".join(parts)[:cap]
    return ""


def _join_preview(reasoning: str, content: str) -> str:
    """响应预览 = [思考] 前缀的思考内容 + 正式回复（思考模型截断时不留黑盒）。"""
    parts = []
    if reasoning:
        parts.append("[思考] " + reasoning)
    if content:
        parts.append(content)
    return "\n".join(parts)


def _record(gw, writer, request_id, key_row, request, model, target_host, served_by,
            degraded, status, prompt_tokens, completion_tokens, ttft_ms, total_s,
            retries, error, spans, req_preview=None, resp_preview=None):
    """审计 + 链路 + 用量累计（异步落库）。"""
    if writer is None:
        return
    key_id = key_row.get("id") if key_row else None
    tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    cost = _calc_cost(model, prompt_tokens, completion_tokens, gw.get("model_prices") or {})
    writer.enqueue_usage(key_id, tokens, cost)
    writer.enqueue_request((
        request_id, key_id, time.time(), request.url.path, model or "",
        target_host, served_by, int(degraded), int(status),
        int(prompt_tokens or 0), int(completion_tokens or 0),
        ttft_ms, (total_s * 1000.0) if total_s is not None else None,
        int(retries or 0), cost, error,
        req_preview, resp_preview,
    ))
    if spans:
        writer.enqueue_spans(spans)


def _safe_record(*args, **kwargs):
    """旁路审计入口：任何异常只记日志，绝不影响代理主链路（docs/07 故障隔离）。"""
    try:
        _record(*args, **kwargs)
    except Exception:
        log.warning("旁路审计落库失败，不影响代理", exc_info=True)


async def _proxy(request: Request, body: bytes, normalize: bool,
                 key_row, request_id: str):
    """路由 + （可选）规范化 + 带重试 SSE 透传 + 审计。返回 Response。"""
    gw = _gw(request)
    writer = gw.get("writer")
    registry = gw.get("registry")
    default_host = gw.get("default_host", "")
    t0 = time.time()
    spans = []

    def span(name, meta=None):
        now_ms = (time.time() - t0) * 1000.0
        try:
            meta_s = json.dumps(meta, ensure_ascii=False) if meta else None
        except Exception:
            meta_s = None  # 旁路序列化失败：丢 meta，不影响代理
        spans.append((request_id, t0, name, now_ms, now_ms, meta_s))

    # 解析 model（路由用）
    model, data = None, None
    if body:
        try:
            data = json.loads(body)
        except Exception:
            data = None
        if isinstance(data, dict):
            model = data.get("model")
    req_preview = _req_preview(body)

    span("received", {"path": request.url.path, "model": model,
                      "ip": request.client.host if request.client else ""})
    span("auth", {"api_key": key_row.get("id") if key_row else None, "ok": True})

    mon, new_model, explicit, degraded_reason = do_route(
        model, registry, default_host,
        excluded=gw.get("excluded"), strategy=gw.get("strategy"))
    if mon is None:
        span("route", {"error": "no available host", "model": model})
        _safe_record(gw, writer, request_id, key_row, request, model, "", "", 0,
                     503, 0, 0, None, time.time() - t0, 0, "no available host", spans,
                     req_preview=req_preview)
        resp = _err(503, "no available llama-server host for model %r"
                    "（模型亲和无匹配；跨模型降级默认关闭，见集群页路由策略）" % (model,))
        resp.headers["X-Request-Id"] = request_id
        return resp

    upstream = upstream_url(mon)
    host_id = mon.cfg.id
    if isinstance(data, dict) and new_model != model:
        data["model"] = new_model
    if normalize and isinstance(data, dict):
        data, changes = normalize_responses(data)
        if changes:
            span("normalize", {"changes": changes})
    if data is not None and (new_model != model or normalize):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    span("route", {"host": host_id, "upstream": upstream, "explicit": explicit,
                   "model": model, "new_model": new_model,
                   "degraded": degraded_reason})

    headers = [(k, v) for k, v in request.headers.items()
               if k.lower() not in HOP_BY_HOP]
    path = request.url.path
    attempt = 0
    retries = 0
    while True:
        client = httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT))
        cm = client.stream(request.method, upstream + path, content=body, headers=headers)
        try:
            resp = await cm.__aenter__()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError, httpx.PoolTimeout) as e:
            await client.aclose()
            if attempt < MAX_RETRIES:
                retries += 1
                delay = _backoff(attempt)
                span("retry", {"attempt": attempt + 1, "error": type(e).__name__,
                               "wait": round(delay, 1)})
                log.warning("upstream %s unreachable (%s); retry %d/%d in %.1fs",
                            upstream, type(e).__name__, attempt + 1, MAX_RETRIES, delay)
                await asyncio.sleep(delay)
                attempt += 1
                continue
            _safe_record(gw, writer, request_id, key_row, request, model, host_id, host_id,
                          0, 502, 0, 0, None, time.time() - t0, retries,
                          "upstream error: %s" % e, spans, req_preview=req_preview)
            return _err(502, "proxy upstream error: %s" % e)

        if resp.status_code >= 500 and attempt < MAX_RETRIES:
            await resp.aread()
            await cm.__aexit__(None, None, None)
            await client.aclose()
            retries += 1
            delay = _backoff(attempt)
            span("retry", {"attempt": attempt + 1, "status": resp.status_code,
                           "wait": round(delay, 1)})
            log.warning("upstream %s %d; retry %d/%d in %.1fs",
                        upstream, resp.status_code, attempt + 1, MAX_RETRIES, delay)
            await asyncio.sleep(delay)
            attempt += 1
            continue

        # 最终响应：透明标注 served_by / degraded（docs/07 §1.2 不静默降级）
        span("forward", {"upstream": upstream, "status": resp.status_code})
        resp_headers = _resp_headers(resp)
        resp_headers["X-Served-By"] = host_id
        resp_headers["X-Request-Id"] = request_id
        if degraded_reason:
            resp_headers["X-Degraded"] = "1"
            resp_headers["X-Degraded-Reason"] = degraded_reason
        degraded_flag = 1 if degraded_reason else 0
        ctype = (resp.headers.get("content-type") or "").lower()

        if "text/event-stream" not in ctype:
            # 非流式：整体读取；JSON 响应注入 served_by/degraded 字段
            raw = await resp.aread()
            await cm.__aexit__(None, None, None)
            await client.aclose()
            total = time.time() - t0
            prompt_t, completion_t = _json_usage(raw)
            if "application/json" in ctype and degraded_reason:
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        obj["served_by"] = host_id
                        obj["degraded"] = True
                        obj["degraded_reason"] = degraded_reason
                        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                except Exception:
                    pass
            span("done", {"status": resp.status_code,
                          "tokens": prompt_t + completion_t,
                          "ms": round(total * 1000, 1)})
            _safe_record(gw, writer, request_id, key_row, request, model,
                          host_id, host_id, degraded_flag, resp.status_code,
                          prompt_t, completion_t, total * 1000.0, total,
                          retries, None, spans, req_preview=req_preview,
                          resp_preview=_resp_preview(raw))
            return Response(raw, status_code=resp.status_code,
                            headers=resp_headers,
                            media_type=resp.headers.get("content-type") or "application/octet-stream")

        # 流式：原样透传（gen 负责迭代 + 关闭 stream + 记录审计）
        usage = _SseUsage()
        state = {"ttft": None, "first": True}

        async def gen():
            try:
                async for chunk in resp.aiter_bytes():
                    if state["first"]:
                        state["ttft"] = time.time() - t0
                        state["first"] = False
                        span("ttft", {"ms": round(state["ttft"] * 1000, 1)})
                    yield chunk
                    try:
                        usage.feed(chunk)
                    except Exception:
                        pass  # 旁路解析失败：只丢 usage/预览，不断流
            finally:
                try:
                    await cm.__aexit__(None, None, None)
                finally:
                    await client.aclose()
                    total = time.time() - t0
                    ttft_ms = (state["ttft"] * 1000.0) if state["ttft"] is not None else None
                    span("done", {"status": resp.status_code,
                                  "tokens": usage.prompt + usage.completion,
                                  "ms": round(total * 1000, 1)})
                    _safe_record(gw, writer, request_id, key_row, request, model,
                                   host_id, host_id, degraded_flag, resp.status_code,
                                   usage.prompt, usage.completion, ttft_ms, total,
                                   retries, None, spans, req_preview=req_preview,
                                   resp_preview=_join_preview(usage.reasoning, usage.content) or None)

        return StreamingResponse(gen(), status_code=resp.status_code,
                                 headers=resp_headers)


@router.post("/v1/responses")
async def responses(request: Request):
    body = await request.body()
    request_id = uuid.uuid4().hex
    key_row, err, status = _auth(request, body)
    if err:
        _reject(request, request_id, key_row, body, err, status)
        return err
    return await _proxy(request, body, normalize=True,
                        key_row=key_row, request_id=request_id)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.body()
    request_id = uuid.uuid4().hex
    key_row, err, status = _auth(request, body)
    if err:
        _reject(request, request_id, key_row, body, err, status)
        return err
    return await _proxy(request, body, normalize=False,
                        key_row=key_row, request_id=request_id)


@router.get("/v1/models")
async def models(request: Request):
    request_id = uuid.uuid4().hex
    key_row, err, status = _auth(request)
    if err:
        _reject(request, request_id, key_row, None, err, status)
        return err
    gw = _gw(request)
    registry = gw.get("registry")
    excluded = gw.get("excluded") or set()
    mons = [m for m in registry.monitors.values()
            if _online(m) and m.cfg.id not in excluded] if registry else []
    # 逻辑模型名（basename，跨主机去重）：选模型不预设主机，由路由策略（亲和/LB）决策。
    # 需要显式指定主机时，客户端仍可发送 host/model（显式前缀路由，见 docs/07 §4）。
    merged, seen, errors = [], set(), []
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=10.0)) as client:
        for mon in mons:
            host_id = mon.cfg.id
            try:
                r = await client.get(upstream_url(mon) + "/v1/models")
                if r.status_code != 200:
                    errors.append("%s: HTTP %d" % (host_id, r.status_code))
                    continue
                for item in (r.json().get("data") or []):
                    mid = item.get("id") or item.get("model") or item.get("name")
                    if not mid:
                        continue
                    base = str(mid).rsplit("/", 1)[-1].strip()
                    if not base:
                        continue
                    key = base.lower()
                    if key in seen:
                        # 同名模型在多台主机：合并 hosts（LB 候选池）
                        for e in merged:
                            if e["id"].lower() == key:
                                if host_id not in e["hosts"]:
                                    e["hosts"].append(host_id)
                                break
                        continue
                    seen.add(key)
                    logical = dict(item)
                    for f in ("id", "model", "name"):
                        if isinstance(logical.get(f), str):
                            logical[f] = base
                    logical["hosts"] = [host_id]
                    merged.append(logical)
            except Exception as e:
                errors.append("%s: %s" % (host_id, e))
    if not merged:
        return _err(502, "all upstreams unavailable: " + "; ".join(errors))
    return JSONResponse({"object": "list", "data": merged})


@router.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def passthrough(request: Request, path: str):
    """其余 /v1/* 路径原样透传（docs/07 §3.4）：鉴权 + 路由 + 审计，不规范化。

    注意：本 router 无 prefix，必须显式 /v1/ 前缀，否则会吃掉 /api/* 等所有路由。
    """
    body = await request.body()
    request_id = uuid.uuid4().hex
    key_row, err, status = _auth(request, body)
    if err:
        _reject(request, request_id, key_row, body, err, status)
        return err
    return await _proxy(request, body, normalize=False,
                        key_row=key_row, request_id=request_id)


def _online(mon) -> bool:
    try:
        return bool((mon.snapshot().get("llama") or {}).get("online"))
    except Exception:
        return False
