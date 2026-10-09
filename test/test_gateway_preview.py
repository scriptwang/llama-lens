"""网关内容预览：请求/响应预览提取 + SSE 内容捕获 + 审计落库 + trace 接口返回请求行。"""
import json
import tempfile
import time

from fastapi.testclient import TestClient

import backend.gateway.router as gwr
from backend.ctl import config as ctl_config
from backend.gateway import keys as keys_mod
from backend.main import create_app


# ---------- 单元：预览提取 ----------

def test_req_preview_chat_messages():
    body = json.dumps({"model": "m", "messages": [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hello world"},
    ]}).encode()
    p = gwr._req_preview(body)
    assert "[system] you are helpful" in p
    assert "[user] hello world" in p


def test_req_preview_multimodal():
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
    ]}]}).encode()
    p = gwr._req_preview(body)
    assert "what is this" in p
    assert "[图片]" in p


def test_req_preview_responses_input():
    assert gwr._req_preview(json.dumps({"input": "just a string"}).encode()) == "just a string"
    body = json.dumps({"input": [
        {"type": "message", "role": "user", "content": "hi"},
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "part1"}, {"type": "input_text", "text": "part2"}]},
    ]}).encode()
    p = gwr._req_preview(body)
    assert "hi" in p and "part1 part2" in p


def test_req_preview_non_json_fallback():
    assert gwr._req_preview(b"raw text") == "raw text"
    assert gwr._req_preview(b"") == ""


def test_req_preview_capped():
    body = json.dumps({"messages": [{"role": "user", "content": "x" * 5000}]}).encode()
    assert len(gwr._req_preview(body)) <= 2000


def test_resp_preview_chat():
    raw = json.dumps({"choices": [{"message": {"content": "the answer"}}]}).encode()
    assert gwr._resp_preview(raw) == "the answer"


def test_resp_preview_responses():
    assert gwr._resp_preview(json.dumps({"output_text": "done"}).encode()) == "done"
    raw = json.dumps({"output": [
        {"type": "message", "content": [{"type": "output_text", "text": "t1"}]}]}).encode()
    assert gwr._resp_preview(raw) == "t1"


def test_resp_preview_raw_fallback():
    assert gwr._resp_preview(b"plain") == "plain"
    assert gwr._resp_preview(b"") == ""


# ---------- 单元：SSE 内容捕获 ----------

def test_sse_usage_captures_chat_content():
    u = gwr._SseUsage()
    u.feed(b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n')
    u.feed(b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: [DONE]\n\n')
    assert u.content == "hello"


def test_sse_usage_captures_responses_delta():
    u = gwr._SseUsage()
    u.feed(b'data: {"type": "response.output_text.delta", "delta": "ab"}\n\n')
    u.feed(b'data: {"type": "response.output_text.delta", "delta": "cd"}\n\n')
    assert u.content == "abcd"


def test_sse_usage_content_capped():
    u = gwr._SseUsage()
    u.feed(b'data: {"choices":[{"delta":{"content":"' + b"x" * 5000 + b'"}}]}\n\n')
    assert len(u.content) == gwr._SseUsage.CAP


def test_sse_usage_captures_reasoning():
    """思考模型：reasoning_content 与 content 分别捕获，预览带 [思考] 前缀。"""
    u = gwr._SseUsage()
    u.feed(b"data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "用户在问"}}]},
                                   ensure_ascii=False).encode() + b"\n\n")
    u.feed(b"data: " + json.dumps({"choices": [{"delta": {"content": "收到"}}]},
                                   ensure_ascii=False).encode() + b"\n\n")
    assert u.reasoning == "用户在问"
    assert u.content == "收到"
    assert gwr._join_preview(u.reasoning, u.content) == "[思考] 用户在问\n收到"
    assert gwr._join_preview("", "") == ""


def test_sse_usage_reasoning_capped():
    u = gwr._SseUsage()
    u.feed(b"data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "x" * 5000}}]}).encode() + b"\n\n")
    assert len(u.reasoning) == gwr._SseUsage.REASONING_CAP


def test_resp_preview_nonstream_reasoning():
    raw = json.dumps({"choices": [{"message": {"reasoning_content": "想了一下", "content": "答案"}}]}).encode()
    p = gwr._resp_preview(raw)
    assert p.startswith("[思考] 想了一下")
    assert p.endswith("答案")


# ---------- E2E：审计落库 + trace 接口 ----------

class _Llama:
    host, port = "127.0.0.1", 9999


class _Cfg:
    def __init__(self, id):
        self.id, self.name, self.llama = id, id, _Llama()


class _Mon:
    def __init__(self, id, model):
        self.cfg = _Cfg(id)
        self._model = model

    def snapshot(self):
        return {"llama": {"online": True, "model": {"name": self._model}}}


class _Reg:
    def __init__(self, mons):
        self.monitors = {m.cfg.id: m for m in mons}


class _StreamResp:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    async def aiter_bytes(self):
        yield b"data: " + json.dumps({"choices": [{"delta": {"content": "收到"}}]},
                                     ensure_ascii=False).encode() + b"\n\n"
        yield b"data: " + json.dumps({"choices": [{"delta": {}}],
                                      "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                                     ensure_ascii=False).encode() + b"\n\n"
        yield b'data: [DONE]\n\n'

    async def aread(self):
        return b""


class _StreamCM:
    async def __aenter__(self):
        return _StreamResp()

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        raise AssertionError("unexpected get")

    def stream(self, method, url, content=None, headers=None):
        return _StreamCM()


def _app(monkeypatch):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeClient)
    return create_app(tempfile.mkdtemp(prefix="gw-prev-"))


def test_stream_audit_has_previews_and_trace_returns_row(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "t")
        h = {"Authorization": "Bearer %s" % row["key"]}
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "只回复两个字：收到"}],
                              "stream": True},
                        headers=h)
        assert r.status_code == 200
        rid = r.headers["x-request-id"]
        time.sleep(2.5)  # 等 writer 刷盘
        req = app.state.gateway["store"].get_request(rid)
        assert req is not None
        assert "[user] 只回复两个字：收到" in req["req_preview"]
        assert req["resp_preview"] == "收到"
        assert req["prompt_tokens"] == 5 and req["completion_tokens"] == 2
        # trace 接口：请求行 + spans 一起返回
        d = client.get("/api/gateway/requests/%s/trace" % rid).json()["data"]
        assert d["request"]["id"] == rid
        assert d["request"]["resp_preview"] == "收到"
        assert [s["name"] for s in d["spans"]]


def test_rejected_request_has_req_preview_and_id_header(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "no key"}]})
        assert r.status_code == 401
        rid = r.headers["x-request-id"]
        time.sleep(2.5)
        req = app.state.gateway["store"].get_request(rid)
        assert req is not None
        assert "[user] no key" in req["req_preview"]
        assert req["status"] == 401


class _JsonResp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    async def aread(self):
        return self._raw


class _JsonCM:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return _JsonResp(self._payload)

    async def __aexit__(self, *a):
        return False


class FakeJsonClient:
    payload = {"choices": [{"message": {"content": "非流式回复"}}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 4}}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        raise AssertionError("unexpected get")

    def stream(self, method, url, content=None, headers=None):
        return _JsonCM(FakeJsonClient.payload)


def test_nonstream_audit_has_resp_preview(monkeypatch):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeJsonClient)
    app = create_app(tempfile.mkdtemp(prefix="gw-prev-"))
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "t")
        h = {"Authorization": "Bearer %s" % row["key"]}
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8",
                              "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 200
        rid = r.headers["x-request-id"]
        time.sleep(2.5)
        req = app.state.gateway["store"].get_request(rid)
        assert req["resp_preview"] == "非流式回复"
        assert "[user] hi" in req["req_preview"]
