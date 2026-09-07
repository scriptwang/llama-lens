"""主机数据桥接：SQLite 行 ↔ 监控 HostConfig，配置文件一次性导入（可选），监控生命周期调度。

- mid：监控侧字符串主机 ID（WS/监控 API/前端路由用），管理侧用整型主键。
- 监控生命周期通过 app.state.loop 调度到事件循环（CRUD 路由运行在线程池）。
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..config import (HostConfig, LlamaCfg, LogCfg, SshCfg,
                      merge_thresholds)
from . import database as db
from .security import decrypt_secret, encrypt_secret

log = logging.getLogger("llamalens.hostsync")

MONITOR_FIELDS = (
    "monitor_enabled", "llama_host", "llama_port", "llama_interval",
    "llama_slow_interval", "llama_timeout", "ssh_interval", "ssh_keepalive",
    "ssh_timeout", "key_path", "process_name", "systemd_unit", "log_source",
    "log_unit", "log_path", "log_follow", "log_catchup_sec", "disk_mounts",
    "thresholds",
)


# ---------------------------------------------------------------------------
# mid 生成
# ---------------------------------------------------------------------------

def gen_mid(alias: str, host: str, taken: Optional[set] = None) -> str:
    """从别名/主机名生成唯一 mid（小写字母数字-，冲突时追加 -2/-3）。"""
    base = re.sub(r"[^a-z0-9]+", "-", (alias or host or "host").lower()).strip("-") or "host"
    taken = taken or {r["mid"] for r in db.query("SELECT mid FROM hosts")}
    mid, i = base, 2
    while mid in taken:
        mid = "%s-%d" % (base, i)
        i += 1
    return mid


# ---------------------------------------------------------------------------
# 行 ↔ HostConfig
# ---------------------------------------------------------------------------

def row_to_host_config(row, global_thresholds: Optional[dict] = None) -> HostConfig:
    """把 hosts 表行解密并组装为监控 HostConfig。"""
    if row["auth_type"] == "key":
        # 密钥认证：监控侧直接复用 UI 录入的私钥内容（key_data 加密存于 encrypted_pwd）
        password = None
        key_data = decrypt_secret(row["encrypted_pwd"]) if row["encrypted_pwd"] else None
        key_pass = decrypt_secret(row["key_passphrase_enc"]) if row["key_passphrase_enc"] else None
    else:
        password = decrypt_secret(row["encrypted_pwd"]) if row["encrypted_pwd"] else None
        key_data = None
        key_pass = None
    ssh = SshCfg(
        host=row["host"],
        port=int(row["port"]),
        user=row["username"],
        password=password,
        key_path=row["key_path"] or None,
        key_data=key_data,
        key_passphrase=key_pass,
        interval=float(row["ssh_interval"]),
        keepalive=int(row["ssh_keepalive"]),
        timeout=float(row["ssh_timeout"]),
    )
    llama = LlamaCfg(
        host=row["llama_host"] or row["host"],
        port=int(row["llama_port"]),
        interval=float(row["llama_interval"]),
        slow_interval=float(row["llama_slow_interval"]),
        timeout=float(row["llama_timeout"]),
    )
    log_cfg = LogCfg(
        source=row["log_source"],
        unit=row["log_unit"],
        path=row["log_path"] or None,
        follow=bool(row["log_follow"]),
        catchup_sec=int(row["log_catchup_sec"]),
    )
    try:
        mounts = json.loads(row["disk_mounts"] or "[]") or ["/"]
    except (ValueError, TypeError):
        mounts = ["/"]
    try:
        host_t = json.loads(row["thresholds"]) if row["thresholds"] else {}
    except (ValueError, TypeError):
        host_t = {}
    return HostConfig(
        id=row["mid"],
        name=row["alias"] or row["host"],
        llama=llama,
        ssh=ssh,
        process_name=row["process_name"],
        log=log_cfg,
        disk_mounts=list(mounts),
        systemd_unit=row["systemd_unit"] or "llama-server.service",
        thresholds=merge_thresholds(global_thresholds, host_t),
    )


def load_hosts_from_db(global_cfg) -> List[HostConfig]:
    """启动时从 DB 加载全部 monitor_enabled 主机。"""
    out = []
    for row in db.query("SELECT * FROM hosts WHERE monitor_enabled = 1 ORDER BY id"):
        try:
            out.append(row_to_host_config(row, global_cfg.thresholds))
        except Exception:
            log.exception("主机 %s 配置解析失败，跳过监控", row["mid"])
    return out


def import_from_yaml_if_empty(app_cfg) -> int:
    """DB 为空且配置文件含 hosts 段时一次性导入（凭证 Fernet 加密入库）。"""
    count = db.query_one("SELECT COUNT(*) AS c FROM hosts")["c"]
    if count > 0 or not app_cfg.hosts:
        return 0
    n = 0
    for h in app_cfg.hosts:
        if h.ssh.password:
            enc = encrypt_secret(h.ssh.password)
        else:
            enc = None
        cur = db.execute(
            """INSERT INTO hosts (mid, alias, host, port, username, encrypted_pwd,
               auth_type, key_path, monitor_enabled, llama_host, llama_port,
               llama_interval, llama_slow_interval, llama_timeout,
               ssh_interval, ssh_keepalive, ssh_timeout,
               process_name, systemd_unit, log_source, log_unit, log_path,
               log_follow, log_catchup_sec, disk_mounts, thresholds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                h.id, h.name, h.ssh.host, h.ssh.port, h.ssh.user, enc,
                "password" if h.ssh.password else "key", h.ssh.key_path, 1,
                h.llama.host, h.llama.port, h.llama.interval, h.llama.slow_interval,
                h.llama.timeout, h.ssh.interval, h.ssh.keepalive, h.ssh.timeout,
                h.process_name, h.systemd_unit or "llama-server.service", h.log.source, h.log.unit, h.log.path,
                1 if h.log.follow else 0, h.log.catchup_sec,
                json.dumps(h.disk_mounts),
                json.dumps(h.thresholds) if h.thresholds else None,
            ),
        )
        db.log_action(cur.lastrowid, "system", "import_yaml", "",
                      "从配置文件导入", "")
        n += 1
    log.info("已从配置文件导入 %d 台主机到数据库", n)
    return n


# ---------------------------------------------------------------------------
# 监控生命周期调度（同步路由 → 事件循环）
# ---------------------------------------------------------------------------

def _registry(request):
    return getattr(request.app.state, "registry", None)


def _loop(request):
    return getattr(request.app.state, "loop", None)


def _monitor_coro(reg, action: str, row):
    """按 DB 行状态构造监控生命周期协程：start / restart / stop。"""
    if action in ("start", "restart") and row["monitor_enabled"]:
        cfg = row_to_host_config(row, reg.app_cfg.global_cfg.thresholds)
        return reg.add_host(cfg) if action == "start" else reg.restart_host(cfg)
    return reg.remove_host(row["mid"])


async def apply_monitor_async(request, action: str, row) -> None:
    """async 版：直接 await 监控生命周期（供 async 路由使用，不阻塞线程池）。

    失败只记日志，不影响管理操作结果。
    """
    reg = _registry(request)
    if reg is None:
        return  # 独立 ctl 模式（无监控）
    mid = row["mid"]
    try:
        await _monitor_coro(reg, action, row)
    except Exception:
        log.exception("主机 %s 监控生命周期操作失败（action=%s）", mid, action)


def apply_monitor(request, action: str, row) -> None:
    """同步版：在 CRUD 路由（线程池）中调度到事件循环；失败只记日志。"""
    reg, loop = _registry(request), _loop(request)
    if reg is None or loop is None:
        return  # 独立 ctl 模式（无监控）
    mid = row["mid"]
    try:
        asyncio.run_coroutine_threadsafe(_monitor_coro(reg, action, row), loop).result(timeout=30)
    except Exception:
        log.exception("主机 %s 监控生命周期操作失败（action=%s）", mid, action)
