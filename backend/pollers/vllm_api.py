"""vLLM HTTP 采集器（VllmPoller）。

- 每 interval（默认 2s）轮询 vLLM 的 Prometheus 兼容 /metrics 端点。
- 解析 Prometheus text 格式 → {metric_name, labels} → value 字典。
- 提取关键指标（GPU 缓存使用率、KV 缓存、请求队列、token 吞吐）。
- 3 次连续请求失败 → online=False + vllm_down 事件；恢复 → vllm_up。
- 状态存入 self.state，HostMonitor.snapshot() 读取后合并到快照。
- 兼容 Python 3.9（不使用 3.10+ 语法）。
"""
import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from ..config import HostConfig
from ..events import EventDetector

log = logging.getLogger("llamalens.vllm")

FAIL_OFFLINE = 3  # 连续失败 count → mark vLLM offline


class VllmPoller:
    """vLLM /metrics HTTP poller. Reads Prometheus text, extracts key indicators into self.state.

    state keys (source Prometheus metric in parentheses):
      - online: bool (True when /metrics returned HTTP 200)
      - gpu_cache_pct: float 0-100 (vllm:kv_cache_usage_perc, 0-1)
      - cpu_cache_pct: float 0-100 (vllm:cpu_cache_usage_perc, 0-1)
      - running_requests: int (vllm:num_requests_running) — 当前并发
      - waiting_requests: int (vllm:num_requests_waiting)
      - prompt_tokens_total: int (vllm:prompt_tokens_total)
      - generation_tokens_total: int (vllm:generation_tokens_total)
      - preemptions_total: int (vllm:num_preemptions_total)
      - last_poll_ts: float (time.time() of last successful poll)

      速度统计（从累计 token 差分计算）：
      - total_gen_tps: float — 总生成速度 (tokens/s)
      - total_prompt_tps: float — 总预填充速度 (tokens/s)
      - last_prompt_tokens: int — 上次采集的 prompt token 累计
      - last_gen_tokens: int — 上次采集的生成 token 累计
      - last_poll_ts_prev: float — 上次采集时间（用于差分）

      每个请求的统计（来自 vllm:request_prompt_tokens / vllm:request_generation_tokens）：
      - requests: list[dict] — 每个请求的 token 统计
        - prompt_tokens: int — prompt token 数
        - generation_tokens: int — 生成 token 数
        - prompt_tokens_total: int — 累计 prompt token
        - generation_tokens_total: int — 累计生成 token
        - prompt_speed_tps: float — prompt 速度 (tokens/s)
        - gen_speed_tps: float — 生成速度 (tokens/s)
        - prompt_latency_avg: float — 平均 prompt 延迟 (s)
        - gen_latency_avg: float — 平均生成延迟 (s/token)

      MTP / 投机解码统计：
      - mtp_acceptance_rate: float — MTP 接受率 (0-1)
      - mtp_accepted: int — 被接受的 draft token 数
      - mtp_generated: int — 生成的 draft token 总数
      - mtp_mean_len: float — 平均接受长度
      - mtp_spec_decode_tps: float — 投机解码速度 (tokens/s)

    详细配置（来自 vllm:cache_config_info 的标签 + /v1/models）：
      - model_id / model_path / max_model_len / allow_sampling /
        allow_logprobs / allow_fine_tuning  （/v1/models）
      - prefix_caching / kv_cache_dtype / block_size /
        gpu_mem_utilization / num_gpu_blocks / mamba_cache_dtype /
        mamba_cache_mode / sliding_window   （cache_config_info 标签）
      - engine_state (vllm:engine_sleep_state，当前激活的 sleep_state，
        如 "awake" 表示推理在线；"weights_offloaded"/"discard_all" 表示休眠)
      - raw: dict (full {(name, labels)} → value, for debugging)
    """

    def __init__(self, cfg: HostConfig, events: EventDetector):
        self.cfg = cfg
        self.events = events
        self.vllm_cfg = cfg.vllm  # VllmCfg (already parsed from hosts.yaml)
        self.state: Dict[str, Any] = {
            "online": False,
            "gpu_cache_pct": 0.0,
            "cpu_cache_pct": 0.0,
            "running_requests": 0,
            "waiting_requests": 0,
            "prompt_tokens_total": 0,
            "generation_tokens_total": 0,
            "preemptions_total": 0,
            "last_poll_ts": 0.0,
            # 速度统计（差分计算）
            "total_gen_tps": 0.0,
            "total_prompt_tps": 0.0,
            "last_prompt_tokens": 0,
            "last_gen_tokens": 0,
            "last_poll_ts_prev": 0.0,
            # 每个请求的统计
            "requests": [],
            # MTP / 投机解码统计
            "mtp_acceptance_rate": None,
            "mtp_accepted": None,
            "mtp_generated": None,
            "mtp_mean_len": None,
            "mtp_spec_decode_tps": None,
            # 详细配置（来自 cache_config_info 标签 + /v1/models）
            "model_id": "",
            "model_path": "",
            "max_model_len": 0,
            "allow_sampling": None,
            "allow_logprobs": None,
            "allow_fine_tuning": None,
            "prefix_caching": None,
            "kv_cache_dtype": "",
            "block_size": 0,
            "gpu_mem_utilization": None,
            "num_gpu_blocks": 0,
            "mamba_cache_dtype": "",
            "mamba_cache_mode": "",
            "sliding_window": "",
            "engine_state": "",
            "raw": {},
        }
        self._fail_count = 0
        self._client: Optional[httpx.AsyncClient] = None
        self._stopped = False
        # 慢速轮询 /v1/models（模型元数据：上下文长度 / 权限），默认 30s
        self._last_models_ts = 0.0
        self.models_interval = max(30.0, float(getattr(self.vllm_cfg, "slow_interval", 30.0)) if self.vllm_cfg else 30.0)

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if not self.vllm_cfg:
            log.warning("[%s] vLLM 未配置，VllmPoller 跳过", self.cfg.id)
            return
        log.info("[%s] VllmPoller 启动 %s:%s（每 %.1fs 采集一次）",
                   self.cfg.id, self.vllm_cfg.host, self.vllm_cfg.port,
                   self.vllm_cfg.interval)
        base_url = "http://%s:%d" % (self.vllm_cfg.host, self.vllm_cfg.port)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=self.vllm_cfg.timeout,
        )
        try:
            while not self._stopped:
                await self._poll_once()
                await asyncio.sleep(self.vllm_cfg.interval)
        finally:
            if self._client:
                await self._client.aclose()

    def stop(self) -> None:
        self._stopped = True

    # ------------------------------------------------------------------
    async def _poll_once(self) -> None:
        if self._client is None:
            return
        try:
            resp = await self._client.get("/metrics")
            resp.raise_for_status()
            text = resp.text
            raw = self._parse_prometheus(text)
            self.state["raw"] = raw
            self._update_state(raw)
            self.state["online"] = True
            self.state["last_poll_ts"] = time.time()
            self._fail_count = 0
            if not getattr(self, "_ever_online", False):
                self._ever_online = True
                self.events.emit(
                    time.time(), "info", "vllm_up",
                    "vLLM 上线: %s:%d" % (self.vllm_cfg.host, self.vllm_cfg.port),
                )
            # 慢速轮询 /v1/models（模型元数据：上下文长度 / 权限）
            await self._maybe_fetch_models()
        except Exception as e:
            self._fail_count += 1
            if self._fail_count == 1 or self._fail_count % 15 == 0:
                log.warning("[%s] vLLM 请求失败(%d): %s", self.cfg.id, self._fail_count, e)
            if self._fail_count >= FAIL_OFFLINE and self.state["online"]:
                self.state["online"] = False
                self.events.emit(time.time(), "error", "vllm_down", "vLLM 离线")

    async def _maybe_fetch_models(self) -> None:
        """慢速轮询 /v1/models（默认 30s），提取模型元数据到 state。

        /v1/models 是 OpenAI 兼容接口，vLLM 支持。返回的 data[0] 包含：
          - id: 模型 ID（如 "qwen3.8-27b-hauhau-aggressive"）
          - root: 模型路径（如 "/data/models/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-AWQ-MTP"）
          - max_model_len: 最大上下文长度（如 262144）
          - permission: 权限列表（如 [{"allow_sampling": true, "allow_logprobs": true, "allow_fine_tuning": false}]）
        """
        now = time.time()
        if now - self._last_models_ts < self.models_interval:
            return  # 未到下次慢速轮询时间
        self._last_models_ts = now
        try:
            resp = await self._client.get("/v1/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data") or []
            if not models:
                return
            m = models[0]
            st = self.state
            # 模型 ID / 路径
            st["model_id"] = m.get("id") or ""
            st["model_path"] = m.get("root") or ""
            # 最大上下文长度
            st["max_model_len"] = _to_int(m.get("max_model_len"))
            # 权限（取第一条）
            perms = m.get("permission") or []
            p = perms[0] if perms else {}
            st["allow_sampling"] = p.get("allow_sampling")
            st["allow_logprobs"] = p.get("allow_logprobs")
            st["allow_fine_tuning"] = p.get("allow_fine_tuning")
        except Exception as e:
            log.warning("[%s] vLLM /v1/models 请求失败: %s", self.cfg.id, e)

    def _update_state(self, raw: Dict[str, Dict[str, Any]]) -> None:
        """从解析后的指标中提取 vLLM 核心指标 + 详细配置。

        raw 结构: {metric_name: {"value": <sum>, "labels": <first series labels>, "series": [(labels_dict, value), ...]}}
        对普通标量指标，raw[name]["value"] 即为该指标所有标签序列的值之和
        （单模型部署下每个指标仅一条序列，即其原值）。
        对 cache_config_info / engine_sleep_state，用 labels 和 series 提取配置详情。
        """
        st = self.state

        # ---- 核心指标（标量） ----
        def _val(name: str) -> Optional[float]:
            """取 raw 中某指标的值（已跨标签求和）。"""
            entry = raw.get(name)
            return entry.get("value") if entry else None

        # KV 缓存占用（Prometheus 值为 0-1，转为 0-100 百分比）。
        # 新版 vLLM 使用 vllm:kv_cache_usage_perc；旧版拆分为
        # vllm:gpu_cache_usage_perc / vllm:cpu_cache_usage_perc，故依次回退。
        kv = _val("vllm:kv_cache_usage_perc")
        if kv is None:
            kv = _val("vllm:gpu_cache_usage_perc")
        st["gpu_cache_pct"] = _to_pct(kv)
        st["cpu_cache_pct"] = _to_pct(_val("vllm:cpu_cache_usage_perc"))
        # 调度器状态：运行中 / 等待中请求数（当前并发）
        st["running_requests"] = _to_int(_val("vllm:num_requests_running"))
        st["waiting_requests"] = _to_int(_val("vllm:num_requests_waiting"))
        # 累计计数
        prompt_total = _to_int(_val("vllm:prompt_tokens_total"))
        gen_total = _to_int(_val("vllm:generation_tokens_total"))
        st["prompt_tokens_total"] = prompt_total
        st["generation_tokens_total"] = gen_total
        st["preemptions_total"] = _to_int(_val("vllm:num_preemptions_total"))

        # ---- 速度统计（差分计算） ----
        now = st["last_poll_ts"]
        prev = st["last_poll_ts_prev"]
        dt = now - prev if now > 0 and prev > 0 else 0.0
        if dt > 0:
            prev_prompt = st.get("last_prompt_tokens", 0)
            prev_gen = st.get("last_gen_tokens", 0)
            prompt_diff = max(0, prompt_total - prev_prompt)
            gen_diff = max(0, gen_total - prev_gen)
            st["total_prompt_tps"] = round(prompt_diff / dt, 2)
            st["total_gen_tps"] = round(gen_diff / dt, 2)
            # 计算请求级平均速度（用于更平滑的显示）
            total_prompt_speed = 0.0
            total_gen_speed = 0.0
            req_count = 0
            for req in st.get("requests", []):
                ps = req.get("prompt_speed_tps", 0)
                gs = req.get("gen_speed_tps", 0)
                if ps > 0:
                    total_prompt_speed += ps
                    req_count += 1
                if gs > 0:
                    total_gen_speed += gs
            st["avg_prompt_tps"] = round(total_prompt_speed / req_count, 2) if req_count > 0 else None
            st["avg_gen_tps"] = round(total_gen_speed / req_count, 2) if req_count > 0 else None
        # 更新累计值（供下次差分）
        st["last_prompt_tokens"] = prompt_total
        st["last_gen_tokens"] = gen_total
        st["last_poll_ts_prev"] = st["last_poll_ts"]

        # ---- 每个请求的统计 ----
        st["requests"] = self._extract_requests(raw)
        if st["requests"]:
            log.debug("[%s] 提取到 %d 个请求的统计", self.cfg.id, len(st["requests"]))

        # ---- MTP / 投机解码统计 ----
        # 注意：vLLM 的 Counter 指标带 _total 后缀
        # 实际指标名: vllm:spec_decode_num_draft_tokens_total, vllm:spec_decode_num_accepted_tokens_total
        # 不存在 vllm:spec_decode_mtp_acceptance_rate / mtp_mean_len / success_rate

        # 接受率 = accepted / draft_tokens (从 Counter 差分计算)
        mtp_acceptance_rate = None
        draft_tokens_entry = raw.get("vllm:spec_decode_num_draft_tokens_total", {})
        draft_tokens = _to_int(draft_tokens_entry.get("value"))
        
        accepted_tokens_entry = raw.get("vllm:spec_decode_num_accepted_tokens_total", {})
        accepted_tokens = _to_int(accepted_tokens_entry.get("value"))
        
        # 从累计值计算接受率（如果没有独立的 acceptance_rate 指标）
        if draft_tokens > 0 and accepted_tokens > 0:
            mtp_acceptance_rate = accepted_tokens / draft_tokens
        
        st["mtp_acceptance_rate"] = mtp_acceptance_rate
        st["mtp_accepted"] = accepted_tokens
        st["mtp_generated"] = draft_tokens
        st["mtp_mean_len"] = None  # 服务器未提供此指标
        st["mtp_spec_decode_tps"] = None  # 服务器未提供此指标
        
        # 打印 accepted/token 位置分布用于调试
        if draft_tokens > 0:
            accept_pos_series = raw.get("vllm:spec_decode_num_accepted_tokens_per_pos_total", {}).get("series", [])
            if accept_pos_series:
                total_by_pos = sum(v for _, v in accept_pos_series)
                log.debug("[%s] MTP: accepted=%d/draft=%d=%.1f%%, by_pos_total=%d",
                         self.cfg.id, accepted_tokens, draft_tokens,
                         mtp_acceptance_rate * 100 if mtp_acceptance_rate else 0,
                         total_by_pos)

        # 调试：打印 raw 中所有指标名称（首次或首次有数据时）
        if not hasattr(self, '_debugged_metrics'):
            self._debugged_metrics = True
            log.debug("[%s] Prometheus 指标列表 (%d 个): %s", 
                     self.cfg.id, len(raw), sorted(raw.keys()))

        # ---- 详细配置（来自 vllm:cache_config_info 的标签）----
        cc = raw.get("vllm:cache_config_info", {}).get("labels") or {}
        st["prefix_caching"] = _bool_label(cc.get("enable_prefix_caching"))
        st["kv_cache_dtype"] = cc.get("cache_dtype") or ""
        st["block_size"] = _to_int(cc.get("block_size"))
        st["gpu_mem_utilization"] = _to_float(cc.get("gpu_memory_utilization"))
        # num_gpu_blocks = KV 缓存容量（可容纳的 block 总数）
        st["num_gpu_blocks"] = _to_int(cc.get("num_gpu_blocks"))
        # MTP / 投机解码（speculative decoding）配置：
        # mamba_cache_dtype / mamba_cache_mode 控制 spec decode 的缓存行为
        st["mamba_cache_dtype"] = cc.get("mamba_cache_dtype") or ""
        st["mamba_cache_mode"] = cc.get("mamba_cache_mode") or ""
        # sliding_window: 若为 "None" 表示未启用 sliding window attention
        st["sliding_window"] = cc.get("sliding_window") or ""

        # ---- 引擎状态（vllm:engine_sleep_state）----
        # engine_sleep_state 是一个 one-hot 指标：
        #   vllm:engine_sleep_state{sleep_state="awake"} 1.0
        #   vllm:engine_sleep_state{sleep_state="weights_offloaded"} 0.0
        #   vllm:engine_sleep_state{sleep_state="discard_all"} 0.0
        # 找到 value == 1.0 的那条序列，取其 labels 中的 sleep_state 值。
        es_series = raw.get("vllm:engine_sleep_state", {}).get("series") or []
        engine_state = ""
        for labels, value in es_series:
            if value == 1.0 and labels:
                engine_state = labels.get("sleep_state", "")
                break
        st["engine_state"] = engine_state

    def _extract_requests(self, raw: Dict[str, Dict[str, Any]]) -> list:
        """从 vLLM Prometheus 指标中提取请求统计。

        vLLM 的请求级指标是 Histogram 类型，不是带 request_id 标签的 Gauge。
        因此这里提取的是请求的聚合统计（来自 Histogram buckets），而非每个请求的独立指标。
        
        指标来源（兼容标准 vLLM 和 1Cat-vLLM）：
        - vllm:num_prompt_tokens_request{le="..."} — prompt token 数分布（1Cat-vLLM）
        - vllm:request_prompt_tokens{le="..."} — prompt token 数分布（标准 vLLM）
        - vllm:num_generation_tokens_request{le="..."} — 生成 token 数分布（1Cat-vLLM）
        - vllm:request_generation_tokens{le="..."} — 生成 token 数分布（标准 vLLM）
        - vllm:time_to_first_token_seconds{le="..."} — TTFT 分布
        - vllm:e2e_request_latency_seconds{le="..."} — 端到端延迟分布
        - vllm:prefill_time_request{le="..."} — prefill 时间分布
        - vllm:decode_time_request{le="..."} — decode 时间分布
        """
        # 从 Histogram 中提取 p50/p90/p99 分位数
        def _hist_percentiles(metric_name: str) -> dict:
            """从 Histogram 指标中提取 p50/p90/p99 值。"""
            entry = raw.get(metric_name, {})
            series = entry.get("series", [])
            buckets = {}
            for labels, value in series:
                le = labels.get("le")
                if le and le != "+Inf":
                    try:
                        buckets[float(le)] = value
                    except (ValueError, TypeError):
                        pass
            if not buckets:
                return {"p50": None, "p90": None, "p99": None}
            
            sorted_buckets = sorted(buckets.items())
            result = {}
            for pct, target in [("p50", 0.5), ("p90", 0.9), ("p99", 0.99)]:
                # 找到最接近目标的 bucket
                closest = None
                for val, count in sorted_buckets:
                    if val >= target * max(buckets.keys()):
                        closest = count
                        break
                if closest is None:
                    closest = sorted_buckets[-1][1] if sorted_buckets else None
                result[pct] = _to_float(closest)
            return result

        # 提取请求统计（尝试 1Cat-vLLM 命名，回退到标准 vLLM 命名）
        def _hist_percentiles_fallback(name_1cat: str, name_std: str) -> dict:
            """尝试多个指标名称，返回第一个有数据的。"""
            result = _hist_percentiles(name_1cat)
            if result["p50"] is not None:
                return result
            return _hist_percentiles(name_std)

        prompt_stats = _hist_percentiles_fallback(
            "vllm:num_prompt_tokens_request", "vllm:request_prompt_tokens")
        gen_stats = _hist_percentiles_fallback(
            "vllm:num_generation_tokens_request", "vllm:request_generation_tokens")
        ttft_stats = _hist_percentiles("vllm:time_to_first_token_seconds")
        e2e_stats = _hist_percentiles("vllm:e2e_request_latency_seconds")
        prefill_stats = _hist_percentiles("vllm:prefill_time_request")
        decode_stats = _hist_percentiles("vllm:decode_time_request")

        # 获取请求成功总数
        request_success_entry = raw.get("vllm:request_success_total", {})
        request_success = _to_int(request_success_entry.get("value"))

        return [{
            "request_count": request_success,
            "prompt_tokens": {
                "p50": prompt_stats["p50"],
                "p90": prompt_stats["p90"],
                "p99": prompt_stats["p99"],
            },
            "generation_tokens": {
                "p50": gen_stats["p50"],
                "p90": gen_stats["p90"],
                "p99": gen_stats["p99"],
            },
            "ttft_seconds": {
                "p50": ttft_stats["p50"],
                "p90": ttft_stats["p90"],
                "p99": ttft_stats["p99"],
            },
            "e2e_latency_seconds": {
                "p50": e2e_stats["p50"],
                "p90": e2e_stats["p90"],
                "p99": e2e_stats["p99"],
            },
            "prefill_time_seconds": {
                "p50": prefill_stats["p50"],
                "p90": prefill_stats["p90"],
                "p99": prefill_stats["p99"],
            },
            "decode_time_seconds": {
                "p50": decode_stats["p50"],
                "p90": decode_stats["p90"],
                "p99": decode_stats["p99"],
            },
        }]

    @staticmethod
    def _parse_prometheus(text: str) -> Dict[str, Dict[str, Any]]:
        """Parse Prometheus text format into a rich per-metric structure.

        Returns:
          {metric_name: {
              "value": float,   # sum of the series values
              "labels": dict,   # the first series' labels (may be {})
              "series": [(labels_dict, value), ...]
          }}

        Line format (one metric sample per line):
          <name>{label="val",...} <value>
          <name> <value>       (no labels)
          # HELP <name> ...     (comment – skipped)
          # TYPE <name> gauge    (comment – skipped)

        The value is always the last whitespace-separated token on the line.
        We keep the per-series labels because several metrics (notably
        vllm:cache_config_info and vllm:engine_sleep_state) encode their
        meaningful payload in the labels rather than in the value.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # 值始终是行内最后一个空白分隔 token
            tokens = s.split()
            if not tokens:
                continue
            try:
                val = float(tokens[-1])
            except ValueError:
                continue  # 末尾不是数字 → 非指标行（如 TYPE 注释）
            # 指标名前缀 = 去掉末尾数值 token 后的前缀（可能含标签串）
            prefix = s[: s.rfind(tokens[-1])].rstrip()
            brace = prefix.find("{")
            if brace > 0:
                name = prefix[:brace].strip()
                labels = _parse_labels(prefix[brace + 1:])
            else:
                name = prefix.strip()
                labels = {}
            if not name:
                continue

            entry = out.get(name)
            if entry is None:
                out[name] = {"value": val, "labels": labels, "series": [(labels, val)]}
            else:
                entry["value"] += val
                entry["series"].append((labels, val))
        return out


def _to_pct(v: Optional[float]) -> float:
    """Prometheus 浮点 → 0-100 百分比。缺失（None）→ 0。

    多数 vLLM 版本的 *_usage_perc 指标按 0-1 比例上报（1.0 = 100%）；若个别版本
    直接上报 0-100 数值，则 v > 1.0，此时不再乘以 100，仅做上限截断。
    """
    if v is None:
        return 0.0
    v = float(v)
    if v > 1.0:
        v = min(v, 100.0)  # 已是百分比数值
    else:
        v = v * 100.0     # 0-1 比例，换算为百分比
    return round(max(0.0, v), 2)


def _to_int(v: Optional[float]) -> int:
    """计数 / 数值 → 非负整数。缺失（None）→ 0。"""
    if v is None:
        return 0
    return int(max(0.0, float(v)))


def _to_float(v: Optional[float]) -> Optional[float]:
    """数值 → float。缺失（None / 不可解析）→ None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_label(v) -> Optional[bool]:
    """标签 / 布尔值 → bool。

    支持 "True"/"true"/"1"/"yes"（真）与 "False"/"false"/"0"/"no"/"None"（假）。
    无法识别的字符串 → None（表示"未知"）。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "enabled"):
        return True
    if s in ("false", "0", "no", "disabled", "none", ""):
        return False
    return None


def _parse_labels(label_str: str) -> Dict[str, str]:
    """Parse Prometheus label string 'k1="v1",k2="v2"' → {'k1': 'v1', 'k2': 'v2'}.

    使用正则提取 (key="value") 对。
    """
    import re
    labels: Dict[str, str] = {}
    s = (label_str or "").strip()
    if not s:
        return labels
    pattern = re.compile(r'([a-zA-Z0-9_]+)\s*=\s*"([^"]*)"')
    for m in pattern.finditer(s):
        labels[m.group(1)] = m.group(2)
    return labels



