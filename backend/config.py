"""配置加载：hosts.yaml + .env。

- 凭证：SSH 支持密钥认证（key_path）与密码认证（password / ${ENV} 引用）两种方式。
- 阈值：全局默认 + 每主机覆盖，加载时合并为每主机一份完整阈值表。
- 兼容 Python 3.9（不使用 3.10+ 语法）。
"""
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import yaml

log = logging.getLogger("llamalens.config")

# ---------------------------------------------------------------------------
# .env 加载（不引入外部依赖，简单 KEY=VALUE 解析）
# ---------------------------------------------------------------------------

def load_dotenv(path: str) -> None:
    """把 .env 中的 KEY=VALUE 载入 os.environ（已存在的环境变量不覆盖）。"""
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_ENV_RE = re.compile(r"\$\{(\w+)\}")
_WIN_ENV_RE = re.compile(r"%(\w+)%")


def resolve_env(value: Any) -> Any:
    """解析字符串中的 ${VAR} 和 %VAR% 环境变量引用。"""
    if isinstance(value, str):
        # 先解析 ${VAR} 格式
        value = _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
        # 再解析 %VAR% 格式（Windows 环境变量）
        value = _WIN_ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    return value


# ---------------------------------------------------------------------------
# 阈值
# ---------------------------------------------------------------------------

# 默认阈值表（与需求文档 §8 一致）。
# 注意：mtp 为“低于阈值告警”（inverted），其余为“高于阈值告警”。
DEFAULT_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "gpu_util": {"warn": 80, "danger": 90},
    "gpu_mem": {"warn": 85, "danger": 95},
    "gpu_temp": {"warn": 75, "danger": 85},
    "gpu_power": {"warn": 85, "danger": 95},
    "cpu": {"warn": 80, "danger": 90},
    "mem": {"warn": 85, "danger": 95},
    "disk": {"warn": 80, "danger": 90},
    "ctx": {"warn": 80, "danger": 90},
    "mtp": {"warn": 80, "danger": 65},
}

# 低于阈值才告警的指标（其余为高于阈值告警）
INVERTED_METRICS = {"mtp"}


def merge_thresholds(global_t: Optional[dict], host_t: Optional[dict]) -> Dict[str, Dict[str, float]]:
    """全局默认 → 全局覆盖 → 主机覆盖，逐字段合并。"""
    merged = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    for src in (global_t or {}, host_t or {}):
        for k, v in src.items():
            if k in merged and isinstance(v, dict):
                merged[k].update({kk: float(vv) for kk, vv in v.items() if kk in merged[k]})
    return merged


# ---------------------------------------------------------------------------
# 配置数据结构
# ---------------------------------------------------------------------------

@dataclass
class LlamaCfg:
    host: str
    port: int = 8080
    interval: float = 1.0        # /health + /slots 轮询间隔（秒）
    slow_interval: float = 30.0  # /props + /v1/models 轮询间隔（秒）
    timeout: float = 3.0         # 单次请求超时


@dataclass
class SshCfg:
    host: str
    port: int = 22
    user: str = "root"
    password: Optional[str] = None
    key_path: Optional[str] = None
    interval: float = 2.0        # 批量采集间隔（秒）
    keepalive: int = 15          # SSH keepalive（秒）
    timeout: float = 15.0        # 单条命令超时


@dataclass
class LogCfg:
    source: str = "journal"      # journal（systemd unit）| file（日志文件）| windows_eventlog
    unit: str = "llama-server"   # systemd unit 名（source=journal）
    path: Optional[str] = None   # 日志文件路径（source=file）
    follow: bool = True
    catchup_sec: int = 30


@dataclass
class HostConfig:
    id: str
    name: str
    llama: LlamaCfg
    ssh: SshCfg
    process_name: str = "llama-server"
    log: LogCfg = field(default_factory=LogCfg)
    disk_mounts: List[str] = field(default_factory=lambda: ["/"])
    systemd_unit: str = "llama-server.service"
    os_type: Optional[str] = None  # 手动指定 OS 类型（linux/windows），自动检测时忽略
    thresholds: Dict[str, Dict[str, float]] = field(default_factory=dict)


@dataclass
class GlobalConfig:
    push_interval: float = 1.0
    llama_points: int = 3600     # llama 指标环形缓冲点数 @1s（1h）
    host_points: int = 1800      # host 指标环形缓冲点数 @2s（1h）
    thresholds: Dict[str, Dict[str, float]] = field(default_factory=dict)


@dataclass
class AppConfig:
    global_cfg: GlobalConfig
    hosts: List[HostConfig]
    port: int = 8000


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _build_llama(d: dict) -> LlamaCfg:
    d = d or {}
    return LlamaCfg(
        host=d.get("host", ""),
        port=int(d.get("port", 8080)),
        interval=float(d.get("interval", 1.0)),
        slow_interval=float(d.get("slow_interval", 30.0)),
        timeout=float(d.get("timeout", 3.0)),
    )


def _build_ssh(d: dict) -> SshCfg:
    d = d or {}
    password = resolve_env(d.get("password"))
    if password and _ENV_RE.search(password):
        log.warning("SSH 密码含未解析的环境变量引用 %s（检查 .env 是否已填写）", password)
    return SshCfg(
        host=d.get("host", ""),
        port=int(d.get("port", 22)),
        user=d.get("user", "root"),
        password=password,
        key_path=resolve_env(d.get("key_path")),
        interval=float(d.get("interval", 2.0)),
        keepalive=int(d.get("keepalive", 15)),
        timeout=float(d.get("timeout", 15.0)),
    )


def _build_log(d: dict) -> LogCfg:
    d = d or {}
    source = str(d.get("source", "journal")).lower()
    if source not in ("journal", "file", "windows_eventlog"):
        raise ValueError("log.source 仅支持 journal | file | windows_eventlog，当前: %s" % source)
    return LogCfg(
        source=source,
        unit=d.get("unit", "llama-server"),
        path=resolve_env(d.get("path")),
        follow=bool(d.get("follow", True)),
        catchup_sec=int(d.get("catchup_sec", 30)),
    )


def _build_host(d: dict, global_t: dict) -> HostConfig:
    d = d or {}
    host_id = d.get("id")
    if not host_id:
        raise ValueError("hosts 条目缺少 id 字段")
    proc = d.get("process")
    if isinstance(proc, dict):
        process_name = proc.get("name", "llama-server")
    else:
        process_name = d.get("process_name", "llama-server")
    
    # 解析 disk_mounts：Windows 使用盘符（如 C:\），Linux 使用路径（如 /）
    raw_mounts = d.get("disk_mounts")
    if raw_mounts is None:
        raw_mounts = ["/"]
    
    return HostConfig(
        id=str(host_id),
        name=d.get("name", host_id),
        llama=_build_llama(d.get("llama")),
        ssh=_build_ssh(d.get("ssh")),
        process_name=process_name,
        log=_build_log(d.get("log")),
        disk_mounts=[str(x) for x in raw_mounts],
        systemd_unit=d.get("systemd_unit", "llama-server.service"),
        os_type=d.get("os_type"),  # 手动指定 OS 类型
        thresholds=merge_thresholds(global_t, d.get("thresholds") or {}),
    )


def load_config(base_dir: str, env_file: Optional[str] = None,
                hosts_file: Optional[str] = None) -> AppConfig:
    """加载完整应用配置。base_dir 为项目根目录。"""
    env_file = env_file or os.path.join(base_dir, ".env")
    hosts_file = hosts_file or os.path.join(base_dir, "config", "hosts.yaml")
    load_dotenv(env_file)

    with open(hosts_file, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    g_raw = raw.get("global") or {}
    global_cfg = GlobalConfig(
        push_interval=float(g_raw.get("push_interval", 1.0)),
        llama_points=int((g_raw.get("history") or {}).get("llama_points", 3600)),
        host_points=int((g_raw.get("history") or {}).get("host_points", 1800)),
        thresholds=g_raw.get("thresholds") or {},
    )

    hosts = [_build_host(h, global_cfg.thresholds) for h in (raw.get("hosts") or [])]

    seen = set()
    for h in hosts:
        if h.id in seen:
            raise ValueError("重复的 host id: %s" % h.id)
        seen.add(h.id)
        if not h.ssh.key_path and not h.ssh.password:
            raise ValueError("host %s 未配置 SSH 凭证（key_path 或 password）" % h.id)
        if h.ssh.key_path:
            h.ssh.key_path = os.path.expanduser(h.ssh.key_path)

    port = int(os.environ.get("PORT", 8000))
    return AppConfig(global_cfg=global_cfg, hosts=hosts, port=port)

