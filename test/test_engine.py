"""引擎抽象层（backend/engine/）测试：注册表、SGLang 采集解析、离线判定、
queue 告警、DB 行→HostConfig 映射、引擎字段校验、/health engine 字段、
history accept_length 持久化与迁移。全部 mock HTTP/SSH，无真实网络。
"""
import asyncio
import sqlite3
import time
from types import SimpleNamespace

import pytest

from backend.alerts import evaluate_alerts
from backend.config import (DEFAULT_THRESHOLDS, EngineCfg, HostConfig, LlamaCfg,
                            LogCfg, SshCfg)
from backend.engine import create_engine
from backend.engine.llama_cpp import LlamaCppEngine
from backend.engine.sglang import SglangEngine
from backend.history import HistoryStore


def _cfg(etype="sglang", host="sg.lan", port=30000, api_key=None):
    return HostConfig(
        id="sg", name="sglang 主机",
        llama=LlamaCfg(host="sg.lan", port=8080),
        ssh=SshCfg(host="sg.lan"),
        engine=EngineCfg(type=etype, host=host, port=port, api_key=api_key),
    )


class _Events:
    """EventDetector 替身：记录上下线事件。"""

    def __init__(self):
        self.calls = []

    def set_llama_online(self, ts, online, model_name="", engine_name="llama"):
        self.calls.append((online, engine_name))

    def set_model(self, ts, model_name):
        self.calls.append(("model", model_name))


# ---------------------------------------------------------------- 注册表
def test_create_engine_registry():
    eng = create_engine(_cfg("llama_cpp"), _Events(), None, None)
    assert isinstance(eng, LlamaCppEngine)
    eng = create_engine(_cfg("sglang"), _Events(), None, None)
    assert isinstance(eng, SglangEngine)


def test_create_engine_unknown_type():
    with pytest.raises(ValueError):
        create_engine(_cfg("vllm"), _Events(), None, None)


def test_engine_cfg_defaults():
    c = EngineCfg()
    assert c.type == "llama_cpp"
    assert c.host == "" and c.port == 0
    assert c.interval == 1.0 and c.slow_interval == 30.0 and c.timeout == 3.0
    assert c.api_key is None


# ---------------------------------------------------------------- LlamaCppEngine
def test_llama_build_block_shape_and_task_history_excluded():
    eng = create_engine(_cfg("llama_cpp"), _Events(), None, None)
    eng.llama.state.update({
        "online": True,
        "model": {"name": "m.gguf", "path": "/models/m.gguf"},
        "slots": [{"id": 0, "state": "idle"}],
        "ctx": {"used": 1000, "total": 2000},
    })
    eng.log_poller.state = {
        "available": True,
        "state": {},
        "context": {"used": 500, "total": 2000},
        "mtp": {"acceptance": 0.85},
        "task_history": [{"x": 1}],   # 必须被排除出快照
    }
    block = eng.build_block(
        {"process": {"cmdline": "-m /models/m.gguf"},
         "_model_sizes": {"/models/m.gguf": 1024}},
        42.0, 123.0, "log")
    assert block["type"] == "llama_cpp"
    assert block["online"] is True
    assert block["model"]["file_size"] == 1024
    assert "task_history" not in block["log"]
    # ctx 合并：API 值优先 → used=1000/total=2000
    assert block["ctx"]["pct"] == 50.0
    assert block["ctx"]["remaining"] == 1000
    assert block["gen_speed_tps"] == 42.0
    assert block["prompt_speed_tps"] == 123.0
    assert block["speed_source"] == "log"


# ---------------------------------------------------------------- SglangEngine
class _FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, responder):
        self.responder = responder

    async def get(self, path, **kw):
        return self.responder(path)


def _loads_payload(prefill=150, waiting=0, dm=None, dm2=None, used=1000):
    p = {"loads": [
        {"num_running_reqs": 3, "num_waiting_reqs": waiting, "num_used_tokens": used,
         "max_total_num_tokens": 4000, "gen_throughput": 12.5,
         "total_prefill_uncached_tokens": prefill,
         "cache_hit_rate": 0.8, "utilization": 0.7,
         "speculative": {"accept_length": 3.2},
         "memory": {"weight_gb": 10, "kv_cache_gb": 20, "graph_gb": 1,
                    "token_capacity": 4000},
         "queues": {"waiting": 2}},
        {"num_running_reqs": 1, "num_waiting_reqs": 0, "num_used_tokens": 500,
         "max_total_num_tokens": 4000, "gen_throughput": 7.5,
         "total_prefill_uncached_tokens": 0,
         "cache_hit_rate": 0.9, "utilization": 0.8,
         "speculative": {"accept_length": 3.4},
         "memory": {"weight_gb": 10},
         "queues": {"waiting": 1}},
    ]}
    if dm is not None:
        p["loads"][0]["decode_moments"] = dm
    if dm2 is not None:
        p["loads"][1]["decode_moments"] = dm2
    return p


def test_sglang_apply_loads():
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(), 1000.0)
    st = eng.state
    # 多 rank 求和
    assert st["requests"]["running"] == 4
    assert st["requests"]["waiting"] == 0
    assert st["kv"]["used"] == 1500 and st["kv"]["total"] == 8000
    assert st["kv"]["pct"] == 18.8
    assert st["gen_speed_tps"] == 20.0
    assert st["prompt_speed_tps"] == 0.0     # 首次无差分
    assert st["cache_hit_rate"] == 0.85
    assert st["utilization"] == 0.75
    assert st["speculative"]["accept_length"] == 3.2
    assert st["memory"]["weight_gb"] == 20.0
    assert st["queues"]["waiting"] == 3.0
    # 第二次：KV 占用 1500 → 1650（decode token 不变），窗口 2s → 75 t/s
    eng._apply_loads(_loads_payload(prefill=300, used=1150), 1002.0)
    assert eng.state["prompt_speed_tps"] == 75.0
    assert eng.ring_extras() == {"accept_length": 3.2}


def test_sglang_prompt_speed_window():
    """prompt 速度 = 最近 10s 窗口内（KV 池新增占用 − decode token）均值：
    长 prefill（>2s chunk 被调度器计数跳过）仍可见；请求结束释放不归负；纯 decode 为 0。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(used=1000, dm=[1, 1, 100_000, 1, 100, 100]), 1000.0)
    assert eng.state["prompt_speed_tps"] == 0.0          # 首帧无差分
    # 模拟长 prefill：KV 每 2s 增 2048（页粒度分配、chunk 与 decode 交替）
    for t, used in ((1002.0, 3048), (1004.0, 5096), (1006.0, 7144), (1008.0, 9192)):
        eng._apply_loads(_loads_payload(used=used, dm=[1, 1, 100_000, 1, 100, 100]), t)
    assert eng.state["prompt_speed_tps"] == 8192.0 / 8.0  # 1024.0
    # 请求结束 KV 释放归 0：不归负，窗口稀释
    eng._apply_loads(_loads_payload(used=0, dm=[1, 1, 100_000, 1, 100, 100]), 1009.0)
    assert 0.0 < eng.state["prompt_speed_tps"] < 1024.0
    # 纯 decode：KV 增量与 decode token 增量相等 → 预填充速度 0
    eng2 = create_engine(_cfg(), _Events(), None, None)
    eng2._apply_loads(_loads_payload(used=1000, dm=[1, 1, 100_000, 1, 100, 100]), 1000.0)
    eng2._apply_loads(_loads_payload(used=1100, dm=[2, 2, 200_000, 2, 200, 200]), 1002.0)
    assert eng2.state["prompt_speed_tps"] == 0.0


def test_sglang_prompt_speed_cache_jump_excluded():
    """缓存前缀激活跳变（num_used_tokens 瞬间跳增整个前缀长度）不计为 prefill：
    +96256 跳变区间 → 预填充速度恒 0；KV 占用展示不受影响。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(used=1000, dm=[1, 1, 100_000, 1, 100, 100]), 1000.0)
    # 缓存前缀激活：占用 1000 → 97256（Δ=96256 > 4000 t/s × 2s），实际 prefill 计算仅 ~305 token
    eng._apply_loads(_loads_payload(used=97256, dm=[1, 1, 100_000, 1, 100, 100]), 1002.0)
    assert eng.state["prompt_speed_tps"] == 0.0
    assert eng.state["kv"]["used"] == 97756  # 97256 + rank1 固定 500，展示口径不变
    # 跳变后进入纯 decode：速度仍为 0（跳变区间不被窗口残留放大）
    eng._apply_loads(_loads_payload(used=97356, dm=[2, 2, 200_000, 2, 200, 200]), 1004.0)
    assert eng.state["prompt_speed_tps"] == 0.0


def test_sglang_prompt_speed_realistic_prefill():
    """实机尺度：2048 页台阶约每 4s 一级（≈500 t/s）→ 窗口速率 ≈512，不虚高。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(used=1000, dm=[1, 1, 100_000, 1, 100, 100]), 1000.0)
    for t, used in ((1004.0, 3048), (1008.0, 5096)):
        eng._apply_loads(_loads_payload(used=used, dm=[1, 1, 100_000, 1, 100, 100]), t)
    assert 400.0 <= eng.state["prompt_speed_tps"] <= 550.0  # 4096 / 8s = 512.0


def test_sglang_prompt_speed_decays_after_prefill():
    """prefill 结束、decode 继续 → ≤6s 预填充速度归 0（5s 窗口衰减尾 + 地板），
    不再出现"输出 token 时还有预填充速度"。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(used=1000, dm=[1, 1, 100_000, 1, 100, 100]), 1000.0)
    for t, used in ((1004.0, 3048), (1008.0, 5096)):
        eng._apply_loads(_loads_payload(used=used, dm=[1, 1, 100_000, 1, 100, 100]), t)
    # prefill 结束（1008）后 decode：KV 增量与 decode token 增量同步
    eng._apply_loads(_loads_payload(used=5196, dm=[2, 2, 200_000, 2, 200, 200]), 1010.0)
    eng._apply_loads(_loads_payload(used=5296, dm=[3, 3, 300_000, 3, 300, 300]), 1012.0)
    eng._apply_loads(_loads_payload(used=5396, dm=[4, 4, 400_000, 4, 400, 400]), 1014.0)
    assert eng.state["prompt_speed_tps"] == 0.0


def test_sglang_prompt_speed_jump_and_prefill_mixed():
    """同一窗口内跳变与正常 prefill 混合：跳变区间被剔除，速率不被放大
    （旧算法 (20000+2048)/4s ≈ 5512；新算法 2048/4s = 512）。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(used=1000, dm=[1, 1, 100_000, 1, 100, 100]), 1000.0)
    eng._apply_loads(_loads_payload(used=21000, dm=[1, 1, 100_000, 1, 100, 100]), 1002.0)  # 缓存激活跳变
    eng._apply_loads(_loads_payload(used=23048, dm=[1, 1, 100_000, 1, 100, 100]), 1004.0)  # 正常 prefill 台阶
    assert 400.0 <= eng.state["prompt_speed_tps"] <= 600.0


def test_sglang_decode_moments_speed():
    """gen 速度走 decode_moments 累计差分：首帧 0、重启回退 0、正常 = Δtok/Δus。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(dm=[10, 20, 2_000_000, 40, 200, 100]), 1000.0)
    assert eng.state["gen_speed_tps"] == 0.0
    eng._apply_loads(_loads_payload(dm=[12, 24, 2_500_000, 48, 240, 138]), 1002.0)
    assert eng.state["gen_speed_tps"] == 76.0          # (138-100)/(0.5s)
    eng._apply_loads(_loads_payload(dm=[5, 5, 100_000, 10, 100, 10]), 1004.0)
    assert eng.state["gen_speed_tps"] == 0.0           # 计数回退（重启归零）
    eng._apply_loads(_loads_payload(dm=[15, 30, 1_100_000, 60, 600, 20]), 1006.0)
    assert eng.state["gen_speed_tps"] == 10.0          # (20-10)/(1.0s)


def test_sglang_decode_moments_multi_rank():
    """多 rank：先跨 rank 求和再差分。"""
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._apply_loads(_loads_payload(dm=[10, 20, 1_500_000, 40, 200, 80],
                                    dm2=[2, 2, 500_000, 4, 20, 20]), 1000.0)
    assert eng.state["gen_speed_tps"] == 0.0
    eng._apply_loads(_loads_payload(dm=[12, 24, 2_000_000, 48, 240, 120],
                                    dm2=[3, 3, 500_000, 6, 30, 18]), 1002.0)
    assert eng.state["gen_speed_tps"] == 76.0          # 和 (2.0e6,100)→(2.5e6,138)


def test_sglang_build_block_and_portal_extra():
    eng = create_engine(_cfg(), _Events(), None, None)
    eng.state["online"] = True
    eng.state["requests"]["waiting"] = 5
    block = eng.build_block({}, 1.0, 2.0, "api")
    assert block["type"] == "sglang"
    assert block["online"] is True
    assert block["log"] is None and block["slots"] == []
    assert block["ctx"] == block["kv"]
    assert eng.portal_extra(block) == {"queued": 5}
    eng.state["requests"]["waiting"] = None
    block2 = eng.build_block({}, 1.0, 2.0, "api")
    assert eng.portal_extra(block2) == {}


def test_sglang_offline_and_recovery():
    eng = create_engine(_cfg(), _Events(), None, None)
    eng._interval = 0.01
    eng._slow_interval = 3600.0

    async def run_loop(client, secs):
        eng._client = client
        task = asyncio.create_task(eng._fast_loop())
        await asyncio.sleep(secs)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def scenario():
        # 先置在线，再连续失败 ≥3 次 → 离线事件
        eng._set_online(True, time.time())
        await run_loop(_FakeClient(lambda p: _FakeResp(500)), 0.08)
        assert eng.state["online"] is False
        assert (False, "SGLang") in eng.events.calls
        # 恢复 → 在线事件
        await run_loop(_FakeClient(lambda p: _FakeResp(200, _loads_payload())), 0.05)
        assert eng.state["online"] is True
        assert (True, "SGLang") in eng.events.calls

    asyncio.run(scenario())


# ---------------------------------------------------------------- 告警
def test_queue_alerts_sglang_only():
    hm = {"reachable": True, "gpus": [], "cpu": {}, "mem": {}, "disk": {}}
    sg = {"type": "sglang", "online": True, "requests": {"waiting": 8}}
    m = {x["metric"]: x["level"]
         for x in evaluate_alerts(DEFAULT_THRESHOLDS, sg, hm)}
    assert m["queue"] == "warn"
    sg["requests"]["waiting"] = 16
    m = {x["metric"]: x["level"]
         for x in evaluate_alerts(DEFAULT_THRESHOLDS, sg, hm)}
    assert m["queue"] == "danger"
    # llama 主机不评估 queue
    ll = {"type": "llama_cpp", "online": True, "requests": {"waiting": 99}}
    m = {x["metric"]: x["level"]
         for x in evaluate_alerts(DEFAULT_THRESHOLDS, ll, hm)}
    assert "queue" not in m


def test_engine_offline_alert_name():
    hm = {"reachable": True, "gpus": [], "cpu": {}, "mem": {}, "disk": {}}
    sg = {"type": "sglang", "online": False}
    m = {x["metric"]: x["level"]
         for x in evaluate_alerts(DEFAULT_THRESHOLDS, sg, hm)}
    assert m["engine"] == "danger"
    ll = {"type": "llama_cpp", "online": False}
    m = {x["metric"]: x["level"]
         for x in evaluate_alerts(DEFAULT_THRESHOLDS, ll, hm)}
    assert m["llama"] == "danger"


# ---------------------------------------------------------------- DB 行映射
def _row(**kw):
    base = {
        "mid": "sg", "alias": "SG 主机", "host": "sg.lan", "port": 22,
        "username": "root", "auth_type": "password",
        "encrypted_pwd": None, "key_passphrase_enc": None, "key_path": "",
        "ssh_interval": 2.0, "ssh_keepalive": 15, "ssh_timeout": 15.0,
        "llama_host": "sg.lan", "llama_port": 8080, "llama_interval": 1.0,
        "llama_slow_interval": 30.0, "llama_timeout": 3.0,
        "log_source": "file", "log_unit": "llama-server",
        "log_path": "/var/log/sglang.log", "log_follow": 0, "log_catchup_sec": 200,
        "disk_mounts": '["/"]', "thresholds": "{}", "monitor_enabled": 1,
        "engine_type": "llama_cpp", "engine_host": "", "engine_port": 0,
        "engine_interval": 1.0, "engine_slow_interval": 30.0, "engine_timeout": 3.0,
        "engine_api_key_enc": None, "process_cmdline": "",
        "process_name": "python3", "systemd_unit": "sglang.service",
        "notify_enabled": 0, "notify_type": "wecom", "notify_url": "",
    }
    base.update(kw)
    return base


def test_row_to_host_config_engine_fields():
    from backend.ctl.hostsync import row_to_host_config
    from backend.ctl.security import encrypt_secret
    cfg = row_to_host_config(_row(
        engine_type="sglang",
        engine_api_key_enc=encrypt_secret("sk-abc"),
    ))
    assert cfg.engine.type == "sglang"
    assert cfg.engine.api_key == "sk-abc"          # 解密
    assert cfg.engine.port == 30000                # 0 → sglang 默认
    assert cfg.engine.host == "sg.lan"             # 回退 llama_host
    assert cfg.process_cmdline == "sglang serve"   # sglang 默认 cmdline


def test_row_to_host_config_llama_default():
    from backend.ctl.hostsync import row_to_host_config
    cfg = row_to_host_config(_row())
    assert cfg.engine.type == "llama_cpp"
    assert cfg.engine.api_key is None
    assert cfg.process_cmdline == ""


# ---------------------------------------------------------------- 字段校验
def test_validate_engine_fields():
    from backend.ctl.errors import ApiError
    from backend.ctl.routers.hosts import validate_engine_fields
    validate_engine_fields("llama_cpp", "")
    validate_engine_fields("sglang", "sglang serve")
    with pytest.raises(ApiError):
        validate_engine_fields("vllm", "")
    with pytest.raises(ApiError):
        validate_engine_fields("sglang", "a; rm -rf /")
    with pytest.raises(ApiError):
        validate_engine_fields("sglang", "x" * 129)


# ---------------------------------------------------------------- /health
def test_health_engine_fields(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    class _Mon:
        def snapshot(self):
            return {
                "llama": {"online": True, "model": {}},
                "engine": {"type": "sglang", "online": True},
                "host_metrics": {"reachable": True},
            }

    class _Reg:
        def __init__(self, mons):
            self.monitors = mons

    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        app.state.registry = _Reg({"sg": _Mon()})
        r = client.get("/api/health")
        assert r.status_code == 200
        host = r.json()["hosts"]["sg"]
        assert host["engine"] == "sglang"
        assert host["engine_online"] is True


# ---------------------------------------------------------------- history
def test_history_accept_length_write_query(tmp_path):
    store = HistoryStore(str(tmp_path / "h.db"))
    store.write_llama([("h1", 1000, 40.0, 120.0, 500.0, 0.85, 3.5)])
    rows = store.query_llama("h1", 0, 2000)
    assert rows[0][5] == 3.5
    cols = [r[1] for r in store._conn.execute("PRAGMA table_info(ts_llama)")]
    assert "accept_length" in cols
    cols1m = [r[1] for r in store._conn.execute("PRAGMA table_info(ts_llama_1m)")]
    assert "accept_avg" in cols1m
    store.close()


def test_history_migration_adds_accept_length(tmp_path):
    """旧库（无 accept_length 列）打开后自动 ALTER 追加。"""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ts_llama (host_id TEXT NOT NULL, ts INTEGER NOT NULL, "
                 "gen_speed REAL, prompt_speed REAL, ctx_used REAL, "
                 "mtp_acceptance REAL, PRIMARY KEY (host_id, ts))")
    conn.execute("CREATE TABLE ts_llama_1m (host_id TEXT NOT NULL, ts INTEGER NOT NULL, "
                 "gen_avg REAL, gen_max REAL, prompt_avg REAL, ctx_max REAL, "
                 "mtp_avg REAL, PRIMARY KEY (host_id, ts))")
    conn.commit()
    conn.close()
    store = HistoryStore(db)
    cols = [r[1] for r in store._conn.execute("PRAGMA table_info(ts_llama)")]
    assert "accept_length" in cols
    cols1m = [r[1] for r in store._conn.execute("PRAGMA table_info(ts_llama_1m)")]
    assert "accept_avg" in cols1m
    store.close()
