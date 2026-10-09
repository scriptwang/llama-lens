"""网关 API：鉴权 + /v1/models 聚合 + SPA 不冲突 + key CRUD + 规范化透传。"""
import json
import tempfile
import time

from fastapi.testclient import TestClient

import backend.gateway.router as gwr
from backend.ctl import config as ctl_config
from backend.gateway import keys as keys_mod
from backend.main import create_app


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


class _ModelsResp:
    status_code = 200

    def json(self):
        return {"data": [{"id": "qwen3.8"}]}


class _StreamResp:
    status_code = 200
    headers = {}

    async def aiter_bytes(self):
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b'data: [DONE]\n\n'

    async def aread(self):
        return b""


class _StreamCM:
    async def __aenter__(self):
        return _StreamResp()

    async def __aexit__(self, *a):
        return False


class FakeClient:
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        return _ModelsResp()

    def stream(self, method, url, content=None, headers=None):
        FakeClient.captured["url"] = url
        FakeClient.captured["body"] = content
        return _StreamCM()


def _app(monkeypatch):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeClient)
    return create_app(tempfile.mkdtemp(prefix="gw-test-"))


def _key_headers(app):
    row = keys_mod.create_key(app.state.gateway["store"], "t")
    return {"Authorization": "Bearer %s" % row["key"]}


def test_models_requires_key(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401


def test_models_aggregates_and_not_spa(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        r = client.get("/v1/models", headers=_key_headers(app))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")  # 非 index.html
        # 逻辑模型名（不带主机前缀）：选模型不预设主机，由路由策略决策
        assert any(d["id"] == "qwen3.8" for d in r.json()["data"])
        assert all("/" not in d["id"] for d in r.json()["data"])


class _PerHostModelsResp:
    status_code = 200

    def __init__(self, model_id):
        self._model_id = model_id

    def json(self):
        return {"data": [{"id": self._model_id}]}


class _PerHostClient:
    """按上游 URL 返回不同模型清单（验证跨主机去重）。"""
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        if "9999" in url:  # 所有 _Mon 的 llama 端口相同，按注册顺序轮转
            _PerHostClient._idx = getattr(_PerHostClient, "_idx", 0)
            mid = _PerHostClient.models[_PerHostClient._idx % len(_PerHostClient.models)]
            _PerHostClient._idx += 1
            return _PerHostModelsResp(mid)
        return _ModelsResp()

    def stream(self, method, url, content=None, headers=None):
        _PerHostClient.captured["url"] = url
        _PerHostClient.captured["body"] = content
        return _StreamCM()


def test_models_logical_names_dedup_across_hosts(monkeypatch):
    """/v1/models：同名模型跨主机去重（逻辑名），hosts 列出承载主机（LB 候选池）。"""
    _PerHostClient.models = ["/share/llm/qwen3.8-27b.gguf",
                             "/models/qwen3.8-27b.gguf",
                             "/models/llama-8b.gguf"]
    _PerHostClient._idx = 0
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", _PerHostClient)
    app = create_app(tempfile.mkdtemp(prefix="gw-test-"))
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([
            _Mon("ai", "/share/llm/qwen3.8-27b.gguf"),
            _Mon("tvai", "/models/qwen3.8-27b.gguf"),
            _Mon("gpu1", "/models/llama-8b.gguf"),
        ])
        r = client.get("/v1/models", headers=_key_headers(app))
        data = {d["id"]: d for d in r.json()["data"]}
        # 同名模型只出现一次，hosts 合并（LB 候选池）
        assert "qwen3.8-27b.gguf" in data
        assert sorted(data["qwen3.8-27b.gguf"]["hosts"]) == ["ai", "tvai"]
        assert data["llama-8b.gguf"]["hosts"] == ["gpu1"]
        # 逻辑名可路由（亲和命中）
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8-27b.gguf",
                              "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        # 显式 host/model 仍可指定主机（向后兼容）
        r = client.post("/v1/chat/completions",
                        json={"model": "tvai//models/llama-8b.gguf",
                              "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        assert _PerHostClient.captured["url"].endswith("/v1/chat/completions")


def test_responses_normalizes(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        body = {"model": "qwen3.8", "input": [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "message", "role": "system", "content": "sys"},
        ]}
        r = client.post("/v1/responses", json=body, headers=_key_headers(app))
        assert r.status_code == 200
        fwd = json.loads(FakeClient.captured["body"])
        assert fwd["input"][0]["role"] == "developer"  # system 合并置首


def test_key_crud(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/gateway/keys", json={"name": "codex", "quota_tokens": 1000})
        assert r.json()["code"] == 0
        key = r.json()["data"]["key"]
        assert key["key"].startswith("ll-")
        assert key["quota_tokens"] == 1000
        assert len(client.get("/api/gateway/keys").json()["data"]["keys"]) == 1
        r = client.patch("/api/gateway/keys/%s" % key["id"], json={"enabled": False})
        assert r.json()["data"]["key"]["enabled"] == 0
        r = client.delete("/api/gateway/keys/%s" % key["id"])
        assert r.json()["data"]["deleted"] == key["id"]
        assert len(client.get("/api/gateway/keys").json()["data"]["keys"]) == 0


def test_status(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/gateway/status")
        assert r.json()["code"] == 0
        assert r.json()["data"]["base_url"].endswith("/v1")


def test_host_exclusion_toggle(monkeypatch):
    from backend.ctl import database as ctl_db
    app = _app(monkeypatch)
    # 在 lifespan 前插入（registry 启动时从 hosts 表构建）
    ctl_db.execute("INSERT INTO hosts (mid, alias, host, username) "
                   "VALUES ('gwtest', 'gwtest', '10.9.9.9', 'u')")
    with TestClient(app) as client:
        # 列表：默认未排除
        hosts = client.get("/api/gateway/hosts").json()["data"]["hosts"]
        assert any(h["mid"] == "gwtest" and h["excluded"] is False for h in hosts)
        # 排除：内存集合 + hosts 表都更新
        r = client.patch("/api/gateway/hosts/gwtest", json={"excluded": True})
        assert r.json()["data"]["excluded"] is True
        assert "gwtest" in app.state.gateway["excluded"]
        assert ctl_db.query_one(
            "SELECT gateway_excluded FROM hosts WHERE mid = 'gwtest'")["gateway_excluded"] == 1
        # 排除后 /v1/models 不再聚合该主机
        app.state.gateway["registry"] = _Reg([_Mon("gwtest", "qwen3.8")])
        r = client.get("/v1/models", headers=_key_headers(app))
        assert r.status_code == 502  # 无可用上游
        # 恢复纳入
        r = client.patch("/api/gateway/hosts/gwtest", json={"excluded": False})
        assert r.json()["data"]["excluded"] is False
        assert "gwtest" not in app.state.gateway["excluded"]
        r = client.get("/v1/models", headers=_key_headers(app))
        assert any(d["id"] == "qwen3.8" for d in r.json()["data"])


def test_host_exclusion_unknown_host(monkeypatch):
    app = _app(monkeypatch)
    with TestClient(app) as client:
        r = client.patch("/api/gateway/hosts/nope", json={"excluded": True})
        assert r.json()["code"] != 0


# ---------- 三档策略 / 白名单 / 费用 / 统计 / 过滤 ----------

class _JsonResp:
    """非流式 JSON 上游响应（验证 served_by/degraded 注入）。"""
    status_code = 200
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    async def aread(self):
        return self._raw

    async def aiter_bytes(self):
        yield self._raw


class _JsonCM:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return _JsonResp(self._payload)

    async def __aexit__(self, *a):
        return False


class FakeJsonClient:
    captured = {}
    payload = {"choices": [{"message": {"content": "ok"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        return _ModelsResp()

    def stream(self, method, url, content=None, headers=None):
        FakeJsonClient.captured["url"] = url
        FakeJsonClient.captured["body"] = content
        return _JsonCM(FakeJsonClient.payload)


def _json_app(monkeypatch):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeJsonClient)
    return create_app(tempfile.mkdtemp(prefix="gw-test-"))


def test_strategy_patch_persists_and_degrades(monkeypatch):
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama-8b")])
        # 默认：无匹配明确 503（不静默切）
        r = client.post("/v1/chat/completions",
                        json={"model": "deepseek-v3", "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 503
        # 开启档3 + 规则 → 降级到 tvai，响应透明标注
        r = client.patch("/api/gateway/strategy", json={
            "cross_model_fallback": True,
            "fallback_rules": [{"model": "deepseek*", "fallback": ["llama"]}],
        })
        assert r.json()["code"] == 0
        assert app.state.gateway["strategy"]["cross_model_fallback"] is True
        assert app.state.gateway["store"].get_kv("strategy")["cross_model_fallback"] is True
        r = client.post("/v1/chat/completions",
                        json={"model": "deepseek-v3", "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        assert r.headers.get("X-Served-By") == "tvai"
        assert r.headers.get("X-Degraded") == "1"
        body = r.json()
        assert body["degraded"] is True
        assert body["served_by"] == "tvai"
        assert "deepseek-v3 -> llama" in body["degraded_reason"]
        # 上游收到的 model 已替换为降级目标
        fwd = json.loads(FakeJsonClient.captured["body"])
        assert fwd["model"] == "llama"
        # 未降级请求无标注
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        assert r.headers.get("X-Served-By") == "ai"
        assert r.headers.get("X-Degraded") is None
        assert "degraded" not in r.json()


def test_strategy_patch_validation(monkeypatch):
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        r = client.patch("/api/gateway/strategy", json={})
        assert r.json()["code"] != 0
        r = client.patch("/api/gateway/strategy",
                         json={"fallback_rules": [{"model": "x"}]})
        assert r.json()["code"] != 0


def test_allowed_models_whitelist(monkeypatch):
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8"), _Mon("tvai", "llama-8b")])
        row = keys_mod.create_key(app.state.gateway["store"], "limited",
                                  allowed_models="qwen3.8")
        h = {"Authorization": "Bearer %s" % row["key"]}
        r = client.post("/v1/chat/completions",
                        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 401
        assert "不允许访问模型" in r.json()["error"]["message"]
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 200


def test_cost_calculation():
    from backend.gateway.router import _calc_cost
    prices = {"qwen3.8": 0.01}
    assert _calc_cost("qwen3.8", 1000, 500, prices) == 0.015
    assert _calc_cost("ai/qwen3.8-27b", 1000, 0, prices) == 0.01  # 前缀 + 子串匹配
    assert _calc_cost("llama", 1000, 0, prices) == 0.0  # 未配置价格
    assert _calc_cost("qwen3.8", 1000, 0, {}) == 0.0


def test_stats_and_audit_filters(monkeypatch):
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        store = app.state.gateway["store"]
        now = time.time()
        rows = [
            ("r1", "k1", now - 100, "/v1/chat/completions", "qwen3.8", "ai", "ai", 0, 200, 100, 50, 10.0, 1000.0, 0, 0.002, None, "[user] hi", "ok"),
            ("r2", "k1", now - 90, "/v1/chat/completions", "llama", "tvai", "tvai", 1, 200, 200, 100, 20.0, 2000.0, 1, 0.005, None, "[user] yo", "degraded reply"),
            ("r3", "k2", now - 80, "/v1/chat/completions", "qwen3.8", "ai", "ai", 0, 500, 0, 0, None, 500.0, 2, 0.0, "upstream error", "[user] fail", None),
        ]
        for row in rows:
            store.write_request(row)
        # 统计：按模型聚合
        r = client.get("/api/gateway/stats?group_by=model&days=1")
        stats = {s["group"]: s for s in r.json()["data"]["stats"]}
        assert stats["qwen3.8"]["requests"] == 2
        assert stats["qwen3.8"]["tokens"] == 150  # r1(150) + r3(0)
        assert stats["llama"]["tokens"] == 300
        assert stats["llama"]["degraded"] == 1
        # 统计：按 key 聚合（带名称）
        r = client.get("/api/gateway/stats?group_by=key&days=1")
        stats = {s["group"]: s for s in r.json()["data"]["stats"]}
        assert stats["k1"]["requests"] == 2
        # 审计过滤：model / status / since
        r = client.get("/api/gateway/requests?model=qwen3.8")
        assert len(r.json()["data"]["requests"]) == 2
        r = client.get("/api/gateway/requests?status=500")
        assert [x["id"] for x in r.json()["data"]["requests"]] == ["r3"]
        r = client.get("/api/gateway/requests?since=%f" % (now - 95))
        assert sorted(x["id"] for x in r.json()["data"]["requests"]) == ["r2", "r3"]


# ---------- 配额 429 / 兜底透传 / 重试 / span 完整性 ----------

def test_quota_exceeded_429(monkeypatch):
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        row = keys_mod.create_key(app.state.gateway["store"], "poor", quota_tokens=10)
        # 手动把用量顶到额度
        app.state.gateway["store"].add_usage(row["id"], 10, 0.0)
        h = {"Authorization": "Bearer %s" % row["key"]}
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 429
        assert "额度" in r.json()["error"]["message"]
        # 鉴权失败仍是 401
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": []},
                        headers={"Authorization": "Bearer ll-bad"})
        assert r.status_code == 401


def test_passthrough_other_paths(monkeypatch):
    """§3.4 其余 /v1/* 路径原样透传。"""
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        r = client.post("/v1/embeddings",
                        json={"model": "qwen3.8", "input": "hi"},
                        headers=_key_headers(app))
        assert r.status_code == 200
        assert FakeJsonClient.captured["url"].endswith("/v1/embeddings")
        # 鉴权同样生效
        r = client.post("/v1/embeddings", json={"input": "hi"})
        assert r.status_code == 401


class _FlakyResp:
    def __init__(self, status_code, fail_remaining):
        self.status_code = status_code
        self._fail_remaining = fail_remaining
        self.headers = {"content-type": "application/json"}
        self._raw = json.dumps({"ok": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()

    async def aread(self):
        return self._raw

    async def aiter_bytes(self):
        yield self._raw


class _FlakyCM:
    def __init__(self, client_state):
        self._st = client_state

    async def __aenter__(self):
        if self._st["fails"] > 0:
            self._st["fails"] -= 1
            if self._st["mode"] == "connect":
                import httpx
                raise httpx.ConnectError("boom")
            return _FlakyResp(500, self._st["fails"])
        return _FlakyResp(200, 0)

    async def __aexit__(self, *a):
        return False


class FakeFlakyClient:
    state = {"fails": 0, "mode": "connect"}
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def get(self, url):
        return _ModelsResp()

    def stream(self, method, url, content=None, headers=None):
        FakeFlakyClient.captured["url"] = url
        return _FlakyCM(FakeFlakyClient.state)


def _flaky_app(monkeypatch):
    monkeypatch.setattr(ctl_config.settings, "auth_enabled", False)
    monkeypatch.setattr(gwr.httpx, "AsyncClient", FakeFlakyClient)
    monkeypatch.setattr(gwr, "_backoff", lambda attempt, base=1.0: 0.01)
    return create_app(tempfile.mkdtemp(prefix="gw-test-"))


def test_retry_on_connect_error(monkeypatch):
    app = _flaky_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        FakeFlakyClient.state = {"fails": 2, "mode": "connect"}
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        # 审计里 retries=2（writer 异步，直接查 store 前手动 flush）
        gw = app.state.gateway
        while not gw["writer"]._queue.empty():
            pass
        time.sleep(2.5)  # 等 writer 刷盘
        rows = gw["store"].query_requests(limit=1)
        assert rows[0]["retries"] == 2
        assert rows[0]["status"] == 200


def test_retry_on_5xx(monkeypatch):
    app = _flaky_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        FakeFlakyClient.state = {"fails": 1, "mode": "5xx"}
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        time.sleep(2.5)
        rows = app.state.gateway["store"].query_requests(limit=1)
        assert rows[0]["retries"] == 1


def test_span_completeness(monkeypatch):
    """§5.3/§13：收到→鉴权→路由→转发→TTFT→完成 全链路 span。"""
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=_key_headers(app))
        assert r.status_code == 200
        time.sleep(2.5)
        rows = app.state.gateway["store"].query_requests(limit=1)
        assert rows, "审计应有记录"
        spans = app.state.gateway["store"].query_spans(rows[0]["id"])
        names = [s["name"] for s in spans]
        for expected in ("received", "auth", "route", "forward", "done"):
            assert expected in names, "缺少 span: %s（实际 %s）" % (expected, names)
        # 非流式无 ttft span（首字节=整体），流式才有
        # 拒绝请求也有 received/auth span
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": []},
                        headers={"Authorization": "Bearer ll-bad"})
        assert r.status_code == 401
        time.sleep(2.5)
        rows = app.state.gateway["store"].query_requests(limit=1)
        assert rows[0]["status"] == 401
        names = [s["name"] for s in app.state.gateway["store"].query_spans(rows[0]["id"])]
        assert "received" in names and "auth" in names


# ---------- 模型单价 / key 有效期 / 状态过滤 / 白名单部分匹配 ----------

def test_prices_patch_persists_and_charges(monkeypatch):
    """PATCH /prices：立即生效（status 可见 + 请求计费）+ 持久化 gateway.db。"""
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8")])
        # 未配置单价：费用 0
        r = client.get("/api/gateway/status")
        assert r.json()["data"]["model_prices"] == {}
        h = _key_headers(app)
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 200
        time.sleep(2.5)
        rows = app.state.gateway["store"].query_requests(limit=1)
        assert rows[0]["cost"] == 0.0
        # 配置单价（1 元/1k）→ 立即生效
        r = client.patch("/api/gateway/prices",
                         json={"model_prices": {"qwen3.8": 1.0}})
        assert r.status_code == 200
        assert r.json()["data"]["model_prices"] == {"qwen3.8": 1.0}
        # 持久化：kv 里有
        assert app.state.gateway["store"].get_kv("model_prices") == {"qwen3.8": 1.0}
        # status 可见
        r = client.get("/api/gateway/status")
        assert r.json()["data"]["model_prices"] == {"qwen3.8": 1.0}
        # 新请求计费：15 tokens × 1.0/1k = 0.015
        r = client.post("/v1/chat/completions",
                        json={"model": "qwen3.8", "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 200
        time.sleep(2.5)
        rows = app.state.gateway["store"].query_requests(limit=1)
        assert abs(rows[0]["cost"] - 0.015) < 1e-9
        # 非法单价拒绝（ctl 约定：HTTP 200 + 信封 code 4001）
        r = client.patch("/api/gateway/prices", json={"model_prices": {"x": "abc"}})
        assert r.json()["code"] == 4001
        r = client.patch("/api/gateway/prices", json={"model_prices": {"x": -1}})
        assert r.json()["code"] == 4001
        # 非法输入不覆盖已有单价
        assert app.state.gateway["store"].get_kv("model_prices") == {"qwen3.8": 1.0}


def test_key_expires_at_create_and_update(monkeypatch):
    """key 有效期：创建时设置 / 编辑修改 / 空串清除。"""
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/gateway/keys",
                        json={"name": "exp", "expires_at": "2027-01-01T23:59:59Z"})
        kid = r.json()["data"]["key"]["id"]
        assert r.json()["data"]["key"]["expires_at"] == "2027-01-01T23:59:59Z"
        # 修改
        r = client.patch("/api/gateway/keys/%s" % kid,
                         json={"expires_at": "2026-12-31T23:59:59Z"})
        assert r.json()["data"]["key"]["expires_at"] == "2026-12-31T23:59:59Z"
        # 空串清除 → 永久
        r = client.patch("/api/gateway/keys/%s" % kid, json={"expires_at": ""})
        assert r.json()["data"]["key"]["expires_at"] is None
        # 已过期的 key 被拒
        client.patch("/api/gateway/keys/%s" % kid,
                     json={"expires_at": "2020-01-01T00:00:00Z"})
        row = app.state.gateway["store"].get_key(kid)
        r = client.get("/v1/models",
                       headers={"Authorization": "Bearer %s" % row["key"]})
        assert r.status_code == 401
        assert "过期" in r.json()["error"]["message"]


def test_status_filter_2xx_5xx(monkeypatch):
    """审计状态过滤：200=2xx 段、500=5xx 段（201/502 也能命中）。"""
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        store = app.state.gateway["store"]
        now = time.time()
        rows = [
            ("s1", "k1", now - 30, "/v1/chat/completions", "m", "ai", "ai", 0, 200, 1, 1, None, 10.0, 0, 0.0, None, None, None),
            ("s2", "k1", now - 20, "/v1/chat/completions", "m", "ai", "ai", 0, 201, 1, 1, None, 10.0, 0, 0.0, None, None, None),
            ("s3", "k1", now - 10, "/v1/chat/completions", "m", "ai", "ai", 0, 502, 0, 0, None, 10.0, 1, 0.0, "up", None, None),
            ("s4", "k1", now - 5, "/v1/chat/completions", "m", "ai", "ai", 0, 429, 0, 0, None, 10.0, 0, 0.0, "quota", None, None),
        ]
        for row in rows:
            store.write_request(row)
        r = client.get("/api/gateway/requests?status=200")
        assert sorted(x["id"] for x in r.json()["data"]["requests"]) == ["s1", "s2"]
        r = client.get("/api/gateway/requests?status=500")
        assert [x["id"] for x in r.json()["data"]["requests"]] == ["s3"]
        r = client.get("/api/gateway/requests?status=429")
        assert [x["id"] for x in r.json()["data"]["requests"]] == ["s4"]


def test_allowed_models_partial_match(monkeypatch):
    """白名单部分匹配：qwen3.8 应放行 qwen3.8-27b-ud.gguf（含主机前缀）。"""
    app = _json_app(monkeypatch)
    with TestClient(app) as client:
        app.state.gateway["registry"] = _Reg([_Mon("ai", "qwen3.8-27b-ud.gguf")])
        row = keys_mod.create_key(app.state.gateway["store"], "partial",
                                  allowed_models="qwen3.8")
        h = {"Authorization": "Bearer %s" % row["key"]}
        r = client.post("/v1/chat/completions",
                        json={"model": "ai/qwen3.8-27b-ud.gguf",
                              "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 200
        # 不匹配的模型仍拒绝
        r = client.post("/v1/chat/completions",
                        json={"model": "llama-8b", "messages": [{"role": "user", "content": "hi"}]},
                        headers=h)
        assert r.status_code == 401
