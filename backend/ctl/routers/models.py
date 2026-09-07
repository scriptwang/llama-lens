"""P0-2 模型管理：模型清单（扫描 .gguf）+ 显存适配预估 + 一键切换模型。

- GET  /api/hosts/{host_id}/models            扫描主机浏览路径下的 .gguf（远端 find，-xdev 不跨挂载点）
- POST /api/services/{name}/switch-model      改目标服务 ExecStart 的 -m/--mmproj + daemon-reload + 重启
"""
import re
import shlex

from fastapi import APIRouter, Depends, Request

from .. import database as db
from ..core import scanner
from ..core.execstart import ParseError, build_execstart, parse_execstart
from ..core.systemctl import systemctl
from ..core.unitfile import detect_execstart_style, find_execstart_span, join_continuation, replace_execstart
from ..errors import (
    ApiError,
    BACKUP_FAILED,
    DAEMON_RELOAD_FAILED,
    FILE_NOT_FOUND,
    PARSE_FAILED,
    VALIDATION_FAILED,
    WRITE_FAILED,
    ok,
    validate_unit_name,
)
from ..schemas import SwitchModelReq
from ..ssh_pool import exec_cmd, pool, sftp_copy, sftp_exists, sftp_read, sftp_write_atomic
from .auth import get_current_user
from .hosts import get_host_row, host_to_conn_dict
from .rules import get_rules_value

router = APIRouter(prefix="/api/hosts", tags=["models"])
svc_router = APIRouter(prefix="/api/services", tags=["models"])

# 量化格式：Q4_K_M / Q6_K / IQ2_XXS / F16 / BF16 ...（取文件名中最后一个匹配，量化一般在末尾）
QUANT_RE = re.compile(r"(I?Q\d+(?:_[A-Z0-9]+)*|BF16|FP16|F16|F32)", re.IGNORECASE)
QUANT_TAIL_RE = re.compile(r"[-_.](I?Q\d+[A-Z0-9_]*|BF16|FP16|F16|F32)$", re.IGNORECASE)

MAX_DEPTH = 6      # 相对浏览路径根的最大深度
MAX_MODELS = 500   # 单次扫描模型数上限
SCAN_TIMEOUT = 60  # find 超时（秒）


def _dedupe_roots(roots: list) -> list:
    """去重 + 去掉被其他根包含的子路径（/share 与 /share/AI 只保留 /share）"""
    out = []
    for r in roots:
        r = r.strip()
        if not r:
            continue
        if len(r) > 1 and r.endswith("/"):
            r = r.rstrip("/")
        if r not in out and not any(o != r and (r == o or r.startswith(o + "/")) for o in out):
            out.append(r)
    return out or ["/"]


def _scan_gguf(client, roots: list) -> list:
    """远端 find 扫描 .gguf（-xdev 不跨挂载点，自动排除 /proc /sys /dev 等伪文件系统）"""
    found = {}
    for root in roots:
        cmd = (
            f"find {shlex.quote(root)} -xdev -maxdepth {MAX_DEPTH} -type f -name '*.gguf' "
            f"-printf '%s\\t%p\\n' 2>/dev/null | head -n {MAX_MODELS}"
        )
        code, out, _ = exec_cmd(client, cmd, timeout=SCAN_TIMEOUT)
        if code not in (0, 1):  # find 权限错误返回非 0，但已扫到的部分仍可用
            continue
        for line in out.splitlines():
            if "\t" not in line:
                continue
            size_s, path = line.split("\t", 1)
            path = path.strip()
            if not path or path in found:
                continue
            try:
                size = int(size_s.strip())
            except ValueError:
                continue
            found[path] = {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "dir": path.rsplit("/", 1)[0] or "/",
                "size": size,
                "quant": "",
                "mmproj": "",
                "mmproj_name": "",
            }
    models = list(found.values())
    _pair_mmproj(models)
    for m in models:
        q = QUANT_RE.findall(m["name"])
        m["quant"] = q[-1].upper() if q else ""
    models.sort(key=lambda m: m["path"].lower())
    return models


def _pair_mmproj(models: list) -> None:
    """同目录内配对 mmproj：优先文件名包含模型主名（去量化后缀/扩展名）的，否则取目录内第一个 mmproj 文件。
    mmproj 文件自身不再配对（避免 mmproj 配 mmproj）。"""
    by_dir = {}
    for m in models:
        by_dir.setdefault(m["dir"], []).append(m)
    for m in models:
        if "mmproj" in m["name"].lower():
            continue
        cands = [x for x in by_dir[m["dir"]] if x is not m and "mmproj" in x["name"].lower()]
        if not cands:
            continue
        base = m["name"]
        if base.lower().endswith(".gguf"):
            base = base[:-5]
        base = QUANT_TAIL_RE.sub("", base)
        matched = [x for x in cands if base.lower() in x["name"].lower()]
        if matched:
            pick = sorted(matched, key=lambda x: x["name"].lower())[0]
        elif len(cands) == 1:
            pick = cands[0]  # 目录内唯一 mmproj（如 mmproj-model-f16.gguf 通用命名）
        else:
            continue  # 多个 mmproj 且无主名匹配 → 不配对，避免误配
        m["mmproj"] = pick["path"]
        m["mmproj_name"] = pick["name"]


def _memory_from_registry(request: Request, row) -> dict:
    """优先复用监控快照（零额外 SSH）"""
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        return None
    mon = reg.monitors.get(row["mid"])
    if mon is None:
        return None
    try:
        snap = mon.snapshot()
    except Exception:
        return None
    hm = snap.get("host_metrics") or {}
    if not hm.get("reachable"):
        return None
    gpus = [
        {
            "index": g.get("index", 0),
            "name": g.get("name", ""),
            "total_mb": g.get("mem_total_mb") or 0,
            "used_mb": g.get("mem_used_mb") or 0,
            "free_mb": g.get("mem_free_mb") or 0,
        }
        for g in hm.get("gpus") or []
    ]
    mem = hm.get("mem") or {}
    ram = {"total_mb": mem.get("total_mb") or 0, "available_mb": mem.get("available_mb") or 0}
    if not gpus and not ram["total_mb"]:
        return None
    return {"gpus": gpus, "ram": ram, "source": "monitor"}


def _probe_memory(client) -> dict:
    """监控不可用时的远端兜底探测（nvidia-smi + free）"""
    gpus = []
    code, out, _ = exec_cmd(
        client,
        "nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv,noheader,nounits 2>/dev/null",
        timeout=10,
    )
    if code == 0:
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                try:
                    total, used = int(parts[2]), int(parts[3])
                except ValueError:
                    continue
                gpus.append({
                    "index": int(parts[0]) if parts[0].isdigit() else 0,
                    "name": parts[1],
                    "total_mb": total,
                    "used_mb": used,
                    "free_mb": max(total - used, 0),
                })
    ram = {"total_mb": 0, "available_mb": 0}
    code, out, _ = exec_cmd(client, "free -m 2>/dev/null | awk '/^Mem:/ {print $2, $7}'", timeout=10)
    if code == 0 and out.strip():
        parts = out.split()
        if len(parts) >= 2:
            try:
                ram = {"total_mb": int(parts[0]), "available_mb": int(parts[1])}
            except ValueError:
                pass
    if not gpus and not ram["total_mb"]:
        return None
    return {"gpus": gpus, "ram": ram, "source": "probe"}


def _fit(size: int, memory: dict) -> dict:
    """适配预估：llama.cpp 支持 GPU+CPU 混合加载，按「空闲显存 + 可用内存」总池评估。
    需求 = 权重 + 约 25% KV cache/运行时开销；总池够权重但不够需求 → 紧张。"""
    if not memory:
        return {"level": "unknown", "note": "无内存数据"}
    size_mb = size / 1024 / 1024
    need = size_mb * 1.25
    gpus = memory.get("gpus") or []
    free_vram = sum(g.get("free_mb") or 0 for g in gpus)
    ram_avail = (memory.get("ram") or {}).get("available_mb") or 0
    total = free_vram + ram_avail
    if total >= need:
        if gpus and free_vram >= need:
            note = f"显存装得下（空闲 {free_vram / 1024:.1f} GiB）"
        elif gpus:
            note = f"装得下（显存 {free_vram / 1024:.1f} + 内存 {ram_avail / 1024:.1f} GiB 混合加载）"
        else:
            note = f"内存装得下（可用 {ram_avail / 1024:.1f} GiB，CPU 推理）"
        return {"level": "ok", "note": note}
    if total >= size_mb:
        return {"level": "tight", "note": "内存紧张（总池仅够权重），建议减小上下文长度"}
    return {"level": "no", "note": "显存与内存均不足"}


def _service_model_map(services: list) -> tuple:
    """解析各服务 ExecStart 的 -m/--mmproj → (model→[服务], mmproj→[服务])"""
    in_use, mm_in_use = {}, {}
    for svc in services:
        try:
            parsed = parse_execstart(svc.get("execstart") or "")
        except ParseError:
            continue
        for a in parsed["args"]:
            if a.get("flag") in ("-m", "--model") and a.get("value"):
                in_use.setdefault(a["value"], []).append(svc["name"])
            elif a.get("flag") == "--mmproj" and a.get("value"):
                mm_in_use.setdefault(a["value"], []).append(svc["name"])
    return in_use, mm_in_use


@router.get("/{host_id}/models")
def list_models(host_id: int, request: Request, min_mb: int = 0, user: str = Depends(get_current_user)):
    """模型清单：扫描浏览路径下 .gguf + 占用关系 + 适配预估。min_mb 过滤小文件（如 vocab）"""
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        roots = _dedupe_roots((row["browse_paths"] or "").split(","))
        models = _scan_gguf(client, roots)
        services = scanner.scan_services(client, get_rules_value())
        memory = _memory_from_registry(request, row) or _probe_memory(client)
    finally:
        pool.checkin(client)
    if min_mb > 0:
        models = [m for m in models if m["size"] >= min_mb * 1024 * 1024]
    in_use, mm_in_use = _service_model_map(services)
    for m in models:
        m["used_by"] = in_use.get(m["path"], [])
        if "mmproj" in m["name"].lower():
            m["mmproj_used_by"] = mm_in_use.get(m["path"], [])
        else:
            m["mmproj_used_by"] = mm_in_use.get(m["mmproj"], []) if m["mmproj"] else []
        m["fit"] = _fit(m["size"], memory)
    return ok({"models": models, "services": services, "memory": memory})


@svc_router.post("/{name}/switch-model")
def switch_model(name: str, host_id: int, req: SwitchModelReq, request: Request,
                 user: str = Depends(get_current_user)):
    """一键切换模型：改 ExecStart 的 -m（含 --mmproj）+ 备份 + daemon-reload + 重启"""
    validate_unit_name(name)
    model = (req.model_path or "").strip()
    if not model.startswith("/"):
        raise ApiError(VALIDATION_FAILED, "model_path 必须是绝对路径")
    mmproj = (req.mmproj_path or "").strip()
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        code, out, _ = exec_cmd(client, f"systemctl show {name} -p FragmentPath --value 2>/dev/null")
        fragment = out.strip()
        if not fragment or not sftp_exists(client, fragment):
            raise ApiError(FILE_NOT_FOUND, f"服务文件不存在：{name}")
        if not sftp_exists(client, model):
            raise ApiError(FILE_NOT_FOUND, f"模型文件不存在：{model}")
        if mmproj and not sftp_exists(client, mmproj):
            raise ApiError(FILE_NOT_FOUND, f"mmproj 文件不存在：{mmproj}")

        content = sftp_read(client, fragment)
        lines = content.splitlines()
        span = find_execstart_span(lines)
        if span is None:
            raise ApiError(PARSE_FAILED, "未找到 ExecStart 行")
        try:
            parsed = parse_execstart(join_continuation(lines, span[0], span[1]))
        except ParseError as e:
            raise ApiError(PARSE_FAILED, str(e))

        args = parsed["args"]
        has_model = False
        for a in args:
            if a.get("flag") in ("-m", "--model"):
                a["value"] = model
                has_model = True
        if not has_model:
            args.append({"flag": "-m", "canonical": "model", "value": model, "known": True})
        if mmproj:
            has_mm = False
            for a in args:
                if a.get("flag") == "--mmproj":
                    a["value"] = mmproj
                    has_mm = True
            if not has_mm:
                args.append({"flag": "--mmproj", "canonical": "model", "value": mmproj, "known": True})
        else:
            args = [a for a in args if a.get("flag") != "--mmproj"]

        style = detect_execstart_style(lines, span)
        new_content = replace_execstart(content, build_execstart(parsed, args, style, lines, span))

        try:
            sftp_copy(client, fragment, fragment + ".bak")
        except Exception as e:
            raise ApiError(BACKUP_FAILED, f"备份失败，已中止写入：{e}")
        try:
            sftp_write_atomic(client, fragment, new_content)
        except Exception as e:
            raise ApiError(WRITE_FAILED, f"写入失败：{e}")

        code, out, err = systemctl(client, host_id, "daemon-reload")
        if code != 0:
            raise ApiError(DAEMON_RELOAD_FAILED, f"daemon-reload 失败：{err.strip()}")
        restarted, restart_error = False, ""
        code, out, err = systemctl(client, host_id, "restart", name, timeout=30)
        if code == 0:
            restarted = True
        else:
            restart_error = (err or out).strip()
        db.log_action(host_id, user, "switch_model", name,
                      f"model={model}, mmproj={mmproj}",
                      request.client.host if request.client else "")
    finally:
        pool.checkin(client)
    return ok({"reloaded": True, "restarted": restarted, "restart_error": restart_error,
               "model": model, "mmproj": mmproj})
