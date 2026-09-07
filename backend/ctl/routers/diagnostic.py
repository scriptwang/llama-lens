"""P2-1 一键体检：聚合系统/GPU/服务/API 连通性/最近事件/错误日志 → markdown 报告。

GET /api/hosts/{host_id}/diagnostic  （host_id = db_id）
"""
import shlex
import time

from fastapi import APIRouter, Depends, Request

from ..core import scanner
from ..errors import ok
from ..ssh_pool import exec_cmd, pool
from .auth import get_current_user
from .hosts import get_host_row, host_to_conn_dict
from .rules import get_rules_value

router = APIRouter(prefix="/api/hosts", tags=["diagnostic"])


def _gib(mb) -> str:
    return "—" if mb is None else "%.1f GiB" % (mb / 1024)


def _pct(v) -> str:
    return "—" if v is None else "%d%%" % round(v)


def _build_markdown(row, snap, services, events, err_logs) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    name = row["alias"] or row["host"]
    L = []
    L.append("# LlamaLens 体检报告 — %s (%s:%d)" % (name, row["host"], row["port"]))
    L.append("")
    L.append("生成时间：%s" % now)
    L.append("")

    # ---- 总体状态 ----
    L.append("## 总体状态")
    if snap:
        ll = snap.get("llama") or {}
        hm = snap.get("host_metrics") or {}
        alerts = snap.get("alerts") or []
        danger = [a for a in alerts if a["level"] == "danger"]
        warn = [a for a in alerts if a["level"] == "warn"]
        L.append("- llama：%s" % ("在线" if ll.get("online") else "离线"))
        L.append("- SSH：%s" % ("已连接" if hm.get("reachable") else "断开"))
        L.append("- 告警：%d 条（danger %d / warn %d）" % (len(alerts), len(danger), len(warn)))
        for a in (danger + warn)[:10]:
            L.append("  - [%s] %s：%s >= %s" % (
                a["level"], a.get("metric", ""), a.get("value"), a.get("threshold")))
    else:
        L.append("- （监控未启用，无实时状态）")
    L.append("")

    # ---- 系统 ----
    L.append("## 系统")
    if snap:
        hm = snap.get("host_metrics") or {}
        cpu = hm.get("cpu") or {}
        mem = hm.get("mem") or {}
        load = cpu.get("load") or []
        L.append("- CPU：%s" % _pct(cpu.get("usage_pct")))
        L.append("- 负载：%s / %s / %s" % tuple(
            ("%.2f" % x) if i < len(load) else "—" for i, x in enumerate(load[:3]))
            if load else "— / — / —")
        mem_pct = (mem["used_mb"] / mem["total_mb"] * 100) if mem.get("total_mb") else None
        L.append("- 内存：%s / %s（%s）" % (_gib(mem.get("used_mb")), _gib(mem.get("total_mb")),
                                          _pct(mem_pct)))
        disk = hm.get("disk") or {}
        for m in (disk.get("mounts") or [])[:6]:
            L.append("- 磁盘 %s：%s 已用（%.1f / %.1f GiB）" % (
                m.get("mount", ""), _pct(m.get("use_pct")),
                m.get("used_gb") or 0, m.get("size_gb") or 0))
        ifaces = (hm.get("net") or {}).get("ifaces") or []
        if ifaces:
            rx = sum(i.get("rx_mb_s") or 0 for i in ifaces)
            tx = sum(i.get("tx_mb_s") or 0 for i in ifaces)
            L.append("- 网络：rx %.1f MB/s / tx %.1f MB/s" % (rx, tx))
        else:
            L.append("- 网络：—")
    else:
        L.append("- （无数据）")
    L.append("")

    # ---- GPU ----
    L.append("## GPU")
    gpus = ((snap or {}).get("host_metrics") or {}).get("gpus") or []
    if gpus:
        L.append("| GPU | 型号 | 利用率 | 显存 | 温度 | 功耗 |")
        L.append("|---|---|---|---|---|---|")
        for g in gpus:
            L.append("| %s | %s | %s | %s / %s | %s | %s |" % (
                g.get("index", 0), g.get("name") or "—", _pct(g.get("util_pct")),
                _gib(g.get("mem_used_mb")), _gib(g.get("mem_total_mb")),
                ("%.0f°C" % g["temp_c"]) if g.get("temp_c") is not None else "—",
                ("%.0f W" % g["power_w"]) if g.get("power_w") is not None else "—"))
    else:
        L.append("- 无 GPU 数据")
    L.append("")

    # ---- 服务 ----
    L.append("## 服务")
    if services:
        L.append("| 服务 | 状态 | 自启 |")
        L.append("|---|---|---|")
        for s in services:
            L.append("| %s | %s (%s) | %s |" % (
                s["name"], s["active_state"], s.get("sub_state") or "—", s.get("unit_file_state") or "—"))
    else:
        L.append("- 未扫描到服务")
    L.append("")

    # ---- llama API ----
    L.append("## llama API")
    if snap:
        ll = snap.get("llama") or {}
        model = ll.get("model") or {}
        L.append("- /health：%s" % ("OK（在线）" if ll.get("online") else "失败（离线）"))
        L.append("- 模型：%s" % (model.get("name") or "—"))
        L.append("- 生成速度：%s tok/s" % (
            ("%.1f" % ll["gen_speed_tps"]) if ll.get("gen_speed_tps") else "0"))
    else:
        L.append("- （监控未启用）")
    L.append("")

    # ---- 最近事件 ----
    L.append("## 最近事件（%d）" % len(events))
    if events:
        for ev in reversed(events[-50:]):
            L.append("- [%s] [%s] %s：%s" % (
                time.strftime("%m-%d %H:%M:%S", time.localtime(ev.get("ts", 0))),
                ev.get("level", ""), ev.get("type", ""), ev.get("msg", "")))
    else:
        L.append("- 无")
    L.append("")

    # ---- 错误日志 ----
    L.append("## 最近错误日志（%d）" % len(err_logs))
    if err_logs:
        L.append("```")
        L.extend(err_logs[-50:])
        L.append("```")
    else:
        L.append("- 无")
    L.append("")
    return "\n".join(L)


def _error_logs(client, row) -> list:
    """按日志源取最近错误日志：journal → journalctl -p err；file → tail + grep"""
    if (row["log_source"] or "journal") == "file" and row["log_path"]:
        cmd = ("tail -n 1000 %s 2>/dev/null | grep -iE 'error|fail|fatal|exception|panic' | tail -n 50"
               % shlex.quote(row["log_path"]))
    else:
        unit = row["log_unit"] or row["systemd_unit"] or "llama-server"
        cmd = "journalctl -u %s -p err --no-pager -n 50 2>/dev/null" % shlex.quote(unit)
    code, out, _ = exec_cmd(client, cmd, timeout=15)
    if code != 0 or not out.strip():
        return []
    lines = [l for l in out.strip().splitlines() if l.strip() != "-- No entries --"]
    return lines


@router.get("/{host_id}/diagnostic")
def diagnostic(host_id: int, request: Request, user: str = Depends(get_current_user)):
    """一键体检：聚合诊断报告（markdown + 结构化数据）"""
    row = get_host_row(host_id)
    reg = getattr(request.app.state, "registry", None)
    mon = reg.get(row["mid"]) if reg is not None else None
    snap = mon.snapshot() if mon is not None else None
    events = mon.events_list(50) if mon is not None else []
    client = pool.checkout(host_to_conn_dict(row))
    try:
        services = scanner.scan_services(client, get_rules_value())
        err_logs = _error_logs(client, row)
    finally:
        pool.checkin(client)
    markdown = _build_markdown(row, snap, services, events, err_logs)
    return ok({
        "markdown": markdown,
        "generated_at": time.time(),
        "summary": {
            "llama_online": bool((snap or {}).get("llama", {}).get("online")),
            "ssh_ok": bool((snap or {}).get("host_metrics", {}).get("reachable")),
            "alerts": len((snap or {}).get("alerts") or []),
            "services": len(services),
            "events": len(events),
            "error_logs": len(err_logs),
        },
    })
