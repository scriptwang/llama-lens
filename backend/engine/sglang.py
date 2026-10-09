"""SGLang HTTP 采集器（SglangEngine）。

SGLang 0.5.x 没有 /metrics（404），主采集源是 GET /v1/loads（每 rank 的
loads 列表），慢路径补 /get_server_info、/get_model_info、/v1/models。
速度口径：gen = decode_moments 累计计数器差分（gen_throughput 在服务端未开
metrics 时恒 0，上游 regression；无该字段时回退 gen_throughput）；
prompt = 最近 5s 窗口内逐区间（KV 池占用增量 − 同区间 decode token 增量）
均值（原 total_prefill_uncached_tokens 因调度器只记录 <2s 的连续 prefill 步，
并发下少计）；单区间占用增量超过 PREFILL_MAX_TPS·Δt 的跳变是缓存前缀激活
（cached KV → active 状态迁移），不计为 prefill 计算。
连续 3 次请求失败 → online=False + engine_down 事件；恢复 → engine_up。
只读：绝不调用 /health_generate、/flush_cache、/abort_request 等写接口。
兼容 Python 3.9。
"""
import asyncio
import logging
import time
from collections import deque
from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import HostConfig
from ..events import EventDetector
from .base import EngineAdapter

log = logging.getLogger("llamalens.sglang")

FAIL_OFFLINE = 3  # 连续失败次数 → 判定离线
PREFILL_WINDOW_S = 5.0  # 预填充速度统计窗口（秒）
PREFILL_MAX_TPS = 4000.0  # 单区间 KV 占用增量上限（t/s·s）：超过视为缓存前缀激活跳变
PREFILL_FLOOR_TPS = 30.0  # 窗口速率低于该地板显示 0（消除 decode 残余毛刺）


def _base_url(host: str, port: int) -> str:
    h = host
    if ":" in h and not h.startswith("["):  # IPv6 字面量需要方括号
        h = "[%s]" % h
    return "http://%s:%d" % (h, port)


def _fnum(v: Any) -> Optional[float]:
    """安全转 float，失败/None 返回 None。"""
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> int:
    """安全转 int，失败/None 返回 0。"""
    try:
        if v is None:
            return 0
        return int(float(v))
    except (TypeError, ValueError):
        return 0


class SglangEngine(EngineAdapter):
    type_name = "sglang"
    display_name = "SGLang"

    def __init__(self, cfg: HostConfig, events: EventDetector, ssh, ring):
        super().__init__(cfg, events, ssh, ring)
        ecfg = cfg.engine
        headers = {}
        if ecfg.api_key:
            headers["Authorization"] = "Bearer %s" % ecfg.api_key
        self._client = httpx.AsyncClient(
            base_url=_base_url(ecfg.host, ecfg.port),
            headers=headers,
            timeout=ecfg.timeout,
        )
        self._interval = ecfg.interval
        self._slow_interval = ecfg.slow_interval
        self.state: Dict[str, Any] = {
            "online": False,
            "model": {},
            "gen_speed_tps": 0.0,
            "prompt_speed_tps": 0.0,
            "requests": {"running": None, "waiting": None, "max_running": None},
            "kv": {"used": None, "total": None, "pct": None, "remaining": None},
            "cache_hit_rate": None,
            "utilization": None,
            "speculative": {
                "algorithm": None,
                "accept_length": None,
                "num_draft_tokens": None,
                "draft_model": None,
            },
            "memory": {},
            "queues": {},
            "server": {},
        }
        self._tasks = []
        self._fails = 0
        self._stopped = False
        self._prefill_hist: deque = deque(maxlen=15)  # (ts, used_sum, decode_tok_sum)
        self._last_decode: Optional[Tuple[float, float]] = None

    # ------------------------------------------------------------------
    @property
    def online(self) -> bool:
        return bool(self.state["online"])

    async def start(self) -> None:
        log.info("[%s] SglangEngine 启动 %s:%s（%.1fs / 慢 %.1fs）",
                 self.cfg.id, self.cfg.engine.host, self.cfg.engine.port,
                 self._interval, self._slow_interval)
        self._tasks = [
            asyncio.create_task(self._fast_loop()),
            asyncio.create_task(self._slow_loop()),
        ]
        try:
            await asyncio.gather(*self._tasks)
        finally:
            self._stopped = True
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._tasks = []

    async def stop(self) -> None:
        self._stopped = True

    # ------------------------------------------------------------------
    async def _fast_loop(self) -> None:
        while not self._stopped:
            try:
                now = time.time()
                data = await self._get("/v1/loads")
                if data is None:
                    self._fails += 1
                    if self._fails == 1 or self._fails % 30 == 0:
                        log.warning("[%s] SGLang /v1/loads 采集失败（连续 %d 次）",
                                    self.cfg.id, self._fails)
                    if self._fails >= FAIL_OFFLINE and self.state["online"]:
                        self._set_online(False, now)
                else:
                    self._fails = 0
                    self._apply_loads(data, now)
                    if not self.state["online"]:
                        self._set_online(True, now)
            except Exception:
                # 单周期异常不能杀死整个采集任务（同 LlamaPoller/SshPoller）
                log.exception("[%s] SGLang 快采周期异常", self.cfg.id)
            await asyncio.sleep(self._interval)

    def _set_online(self, online: bool, now: float) -> None:
        self.state["online"] = online
        if not online:
            self.state["gen_speed_tps"] = 0.0
            self.state["prompt_speed_tps"] = 0.0
            self._prefill_hist.clear()
            self._last_decode = None
        self.events.set_llama_online(
            now, online,
            model_name=(self.state["model"] or {}).get("name", ""),
            engine_name=self.display_name,
        )

    def _apply_loads(self, data: dict, now: float) -> None:
        loads = [x for x in (data.get("loads") or []) if isinstance(x, dict)]
        if not loads:
            return
        running = waiting = used = total = 0
        gen = 0.0
        dm_us_sum = 0.0
        dm_tok_sum = 0.0
        decode_seen = False
        hits: list = []
        utils: list = []
        accepts: list = []
        memory: Dict[str, float] = {}
        queues: Dict[str, float] = {}
        for lk in loads:
            running += _num(lk.get("num_running_reqs"))
            waiting += _num(lk.get("num_waiting_reqs"))
            used += _num(lk.get("num_used_tokens"))
            total += _num(lk.get("max_total_num_tokens"))
            gen += _fnum(lk.get("gen_throughput")) or 0.0
            # decode_moments=[步数, Σbatch, Σstep_us, Σbatch², Σbatch·step_us, Σ生成tokens]（累计）
            dm = lk.get("decode_moments")
            if isinstance(dm, (list, tuple)) and len(dm) >= 6:
                dm_us_sum += _fnum(dm[2]) or 0.0
                dm_tok_sum += _fnum(dm[5]) or 0.0
                decode_seen = True
            for key in ("cache_hit_rate", "utilization"):
                v = _fnum(lk.get(key))
                if v is not None:
                    (hits if key == "cache_hit_rate" else utils).append(v)
            al = _fnum((lk.get("speculative") or {}).get("accept_length"))
            if al is not None:
                accepts.append(al)
            mem = lk.get("memory")
            if isinstance(mem, dict):
                for mk in ("weight_gb", "kv_cache_gb", "graph_gb", "token_capacity"):
                    v = _fnum(mem.get(mk))
                    if v is not None:
                        memory[mk] = round(memory.get(mk, 0.0) + v, 2)
            q = lk.get("queues")
            if isinstance(q, dict):
                for qk, qv in q.items():
                    v = _fnum(qv)
                    if v is not None:
                        queues[qk] = round(queues.get(qk, 0.0) + v, 2)

        # prompt 速度：total_prefill_uncached_tokens 仅记录"连续且 <2s 的 prefill 步"
        #（idle 后首步、与 decode 交替的步、>2s 长 chunk 均被调度器跳过），并发下严重少计。
        # 改用 KV 池新增占用逐区间差分：Δused − Δdecode_tokens ≈ 区间内未命中 prefill
        # token（缓存命中已占位不重复计）。关键修正：SGLang 调度带长缓存前缀的请求时
        # num_used_tokens 会瞬间跳增整个前缀长度（cached KV → active 迁移，实测 5.8万–
        # 9.6万），并非 prefill 计算；消费级 GPU 真实 prefill 远低于 PREFILL_MAX_TPS，
        # 故单区间 Δused > PREFILL_MAX_TPS·Δt 的跳变区间不计。5s 窗口平滑 2048 页台阶
        # 且 prefill 结束后 ≤5s 归零；窗口速率低于地板显示 0（消除 decode 残余毛刺）。
        prompt = 0.0
        self._prefill_hist.append((now, float(used), float(dm_tok_sum)))
        while len(self._prefill_hist) > 2 and self._prefill_hist[1][0] < now - PREFILL_WINDOW_S:
            self._prefill_hist.popleft()
        if len(self._prefill_hist) >= 2:
            span = self._prefill_hist[-1][0] - self._prefill_hist[0][0]
            if span > 0:
                prefilled = 0.0
                points = list(self._prefill_hist)
                prev = points[0]
                for cur in points[1:]:
                    dt = cur[0] - prev[0]
                    raw = cur[1] - prev[1]
                    if dt > 0 and raw <= PREFILL_MAX_TPS * dt:
                        prefilled += max(0.0, raw - max(0.0, cur[2] - prev[2]))
                    prev = cur
                rate = prefilled / span
                prompt = rate if rate >= PREFILL_FLOOR_TPS else 0.0

        # gen 速度：gen_throughput 在服务端未开 metrics 时恒 0（上游 regression），
        # 改用 decode_moments 累计计数器差分（跨 rank 先求和）；无该字段时回退 gen_throughput
        if decode_seen:
            gen = 0.0  # dm 可用时完全弃用 gen_throughput（上游未开 metrics 恒 0 / 口径不可靠）
            prev_dm = self._last_decode
            if prev_dm is not None:
                d_us = dm_us_sum - prev_dm[0]
                d_tok = dm_tok_sum - prev_dm[1]
                if d_us > 0 and d_tok >= 0:
                    gen = d_tok / (d_us / 1e6)
                # 计数回退（服务重启归零）：重新同步基线，本轮速度记 0
            self._last_decode = (dm_us_sum, dm_tok_sum)
        else:
            self._last_decode = None

        st = self.state
        st["gen_speed_tps"] = round(gen, 2)
        st["prompt_speed_tps"] = round(prompt, 2)
        st["requests"] = {
            "running": running,
            "waiting": waiting,
            "max_running": st["requests"].get("max_running"),
        }
        st["kv"] = {
            "used": used,
            "total": total,
            "pct": round(used / total * 100.0, 1) if total else None,
            "remaining": max(0, total - used) if total else None,
        }
        st["cache_hit_rate"] = round(sum(hits) / len(hits), 4) if hits else None
        st["utilization"] = round(sum(utils) / len(utils), 4) if utils else None
        if accepts:
            st["speculative"]["accept_length"] = round(accepts[0], 2)
        st["memory"] = memory
        st["queues"] = queues

    # ------------------------------------------------------------------
    async def _slow_loop(self) -> None:
        await asyncio.sleep(1.0)  # 先让快采把在线状态/模型名立起来
        while not self._stopped:
            try:
                if self.state["online"]:
                    await self._poll_slow(time.time())
            except Exception:
                log.exception("[%s] SGLang 慢采周期异常", self.cfg.id)
            await asyncio.sleep(self._slow_interval)

    async def _poll_slow(self, now: float) -> None:
        info = await self._get("/get_server_info") or {}
        model = await self._get("/get_model_info") or {}
        models = await self._get("/v1/models") or {}

        m = dict(self.state["model"])
        name = model.get("served_model_name")
        v1_data = models.get("data") or []
        if not name and v1_data and isinstance(v1_data[0], dict):
            name = v1_data[0].get("id")
        if name:
            m["name"] = name
        if model.get("model_path"):
            m["path"] = model["model_path"]
        for k in ("quantization", "kv_cache_dtype", "model_type"):
            v = model.get(k) or info.get(k)
            if v:
                m[k] = v
        if v1_data and isinstance(v1_data[0], dict) and v1_data[0].get("max_model_len") is not None:
            m["max_model_len"] = _num(v1_data[0]["max_model_len"])
        self.state["model"] = m

        spec = self.state["speculative"]
        if info.get("speculative_algorithm"):
            spec["algorithm"] = info["speculative_algorithm"]
        if info.get("speculative_num_draft_tokens") is not None:
            spec["num_draft_tokens"] = _num(info["speculative_num_draft_tokens"])
        if info.get("speculative_draft_model_path"):
            spec["draft_model"] = str(info["speculative_draft_model_path"]).rsplit("/", 1)[-1]

        server: Dict[str, Any] = {}
        for k in ("tp_size", "dp_size", "mem_fraction_static",
                  "max_running_requests", "max_total_num_tokens"):
            if info.get(k) is not None:
                server[k] = info[k]
        self.state["server"] = server
        if info.get("max_running_requests") is not None:
            self.state["requests"]["max_running"] = _num(info["max_running_requests"])

        if name:
            self.events.set_model(now, name)

    async def _get(self, path: str) -> Optional[dict]:
        try:
            r = await self._client.get(path)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    def speeds(self) -> Tuple[float, float, str]:
        return self.state["gen_speed_tps"], self.state["prompt_speed_tps"], "api"

    def ring_extras(self) -> Optional[Dict[str, float]]:
        al = (self.state["speculative"] or {}).get("accept_length")
        if al is None:
            return None
        return {"accept_length": al}

    def build_block(self, host_metrics: Dict[str, Any], gen: float,
                    prompt: float, source: str) -> Dict[str, Any]:
        st = self.state
        return {
            "type": self.type_name,
            "online": bool(st["online"]),
            "model": dict(st["model"]),
            "gen_speed_tps": round(gen, 2),
            "prompt_speed_tps": round(prompt, 2),
            "speed_source": source,
            "log": None,
            "slots": [],
            "ctx": dict(st["kv"]),
            "requests": dict(st["requests"]),
            "kv": dict(st["kv"]),
            "cache_hit_rate": st["cache_hit_rate"],
            "utilization": st["utilization"],
            "speculative": dict(st["speculative"]),
            "memory": dict(st["memory"]),
            "queues": dict(st["queues"]),
            "server": dict(st["server"]),
        }

    def portal_extra(self, block: Dict[str, Any]) -> Dict[str, Any]:
        req = block.get("requests") or {}
        if req.get("waiting") is None:
            return {}
        return {"queued": req["waiting"]}
