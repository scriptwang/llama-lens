"""主机/服务指标采集：单次 SSH 执行复合脚本（CPU/内存/GPU/服务进程），另提供 journalctl 日志接口"""

from fastapi import APIRouter, Depends

from ..errors import ApiError, SSH_CMD_FAILED, ok, validate_unit_name
from .auth import get_current_user
from .hosts import get_host_row, host_to_conn_dict
from ..ssh_pool import exec_cmd, pool

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

MAX_LOG_LINES = 1000
METRICS_TIMEOUT = 15


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_script(names) -> str:
    """生成远端采集脚本：CPU 双采样(0.5s) + 内存 + 负载 + 核数 + GPU 自动探测 + 逐服务进程指标"""
    lines = [
        "s1=$(awk '/^cpu /{t=0; for(i=2;i<=9 && i<=NF;i++) t+=$i; print t\" \"$5+$6}' /proc/stat)",
        "sleep 0.5",
        "s2=$(awk '/^cpu /{t=0; for(i=2;i<=9 && i<=NF;i++) t+=$i; print t\" \"$5+$6}' /proc/stat)",
        'echo "CPU|${s1}|${s2}"',
        'awk \'/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{print "MEM|"t"|"a}\' /proc/meminfo',
        'echo "LOAD|$(awk \'{printf "%s|%s|%s", $1, $2, $3}\' /proc/loadavg)"',
        'echo "CORES|$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo)"',
        'if command -v nvidia-smi >/dev/null 2>&1; then',
        '  echo "GPU|nvidia"',
        '  nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw,power.limit,temperature.gpu --format=csv,noheader,nounits 2>/dev/null | sed "s/^/NGPU|/"',
        'elif command -v rocm-smi >/dev/null 2>&1; then',
        '  echo "GPU|rocm"',
        '  rocm-smi --showuse --showmemuse --showpower --showtemp --showmeminfo vram --csv 2>/dev/null | sed "s/^/RSMI|/"',
        'fi',
    ]
    for name in names:
        lines.append('pid=$(systemctl show %s -p MainPID --value 2>/dev/null)' % name)
        lines.append('if [ -n "$pid" ] && [ "$pid" != "0" ]; then')
        lines.append('  psline=$(ps -o pcpu=,rss=,etimes= -p "$pid" 2>/dev/null | tr -s " ")')
        lines.append('  [ -n "$psline" ] && printf "SVC|%s|%s|%s\\n" "__NAME__" "$pid" "$psline"'.replace("__NAME__", name))
        lines.append("fi")
    return "\n".join(lines)


def _parse_nvidia(line):
    """nvidia-smi CSV 行：index, name(可含空格), util, mem_used(MiB), mem_total(MiB), power(W), limit(W), temp(C)"""
    parts = line.split(", ")
    if len(parts) < 8:
        return []
    tail = parts[-6:]
    name = ", ".join(parts[1:-6])
    idx = _f(parts[0])
    return [
        {
            "index": int(idx) if idx is not None else 0,
            "name": name,
            "util_percent": _f(tail[0]),
            "mem_used_mb": _f(tail[1]),
            "mem_total_mb": _f(tail[2]),
            "power_w": _f(tail[3]),
            "power_limit_w": _f(tail[4]),
            "temp_c": _f(tail[5]),
        }
    ]


def _parse_rocm(lines):
    """rocm-smi --csv 多行：label,"v0","v1",..."""
    data = {}
    for line in lines:
        parts = [p.strip().strip('"') for p in line.strip().split(",")]
        if len(parts) >= 2 and parts[0]:
            data.setdefault(parts[0], []).extend(parts[1:])
    count = max((len(v) for v in data.values()), default=0)
    devices = []
    for i in range(count):
        def g(label):
            vals = data.get(label, [])
            return _f(vals[i]) if i < len(vals) else None

        mem_used_b = g("GPU memory use (B)")
        mem_total_b = g("VRAM Total Memory (B)")
        devices.append(
            {
                "index": i,
                "name": "GPU %d" % i,
                "util_percent": g("GPU use (%)"),
                "mem_used_mb": round(mem_used_b / 1048576, 1) if mem_used_b is not None else None,
                "mem_total_mb": round(mem_total_b / 1048576, 1) if mem_total_b is not None else None,
                "power_w": g("Average GPU package power (W)"),
                "power_limit_w": None,
                "temp_c": g("Temperature (edge) (C)"),
            }
        )
    return devices


def _parse_output(out: str) -> dict:
    result = {
        "cpu": {"percent": None, "cores": None, "load": []},
        "memory": {"total_mb": None, "used_mb": None, "percent": None},
        "gpu": {"vendor": None, "devices": []},
        "services": {},
    }
    rocm_lines = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        tag = line.split("|", 1)[0]
        if tag == "CPU":
            parts = line.split("|")
            if len(parts) == 3:
                try:
                    t1, i1 = (float(x) for x in parts[1].split())
                    t2, i2 = (float(x) for x in parts[2].split())
                    dt, di = t2 - t1, i2 - i1
                    if dt > 0:
                        result["cpu"]["percent"] = round(max(0.0, min(100.0, 100.0 * (dt - di) / dt)), 1)
                except ValueError:
                    pass
        elif tag == "MEM":
            parts = line.split("|")
            if len(parts) == 3:
                total_kb, avail_kb = _f(parts[1]), _f(parts[2])
                if total_kb and total_kb > 0:
                    used_kb = max(0.0, total_kb - (avail_kb or 0.0))
                    result["memory"] = {
                        "total_mb": round(total_kb / 1024),
                        "used_mb": round(used_kb / 1024),
                        "percent": round(100.0 * used_kb / total_kb, 1),
                    }
        elif tag == "LOAD":
            result["cpu"]["load"] = [v for v in (_f(x) for x in line.split("|")[1:]) if v is not None]
        elif tag == "CORES":
            cores = _f(line.split("|", 1)[1])
            result["cpu"]["cores"] = int(cores) if cores else None
        elif tag == "GPU":
            result["gpu"]["vendor"] = line.split("|", 1)[1].strip()
        elif tag == "NGPU":
            result["gpu"]["devices"].extend(_parse_nvidia(line.split("|", 1)[1]))
        elif tag == "RSMI":
            rocm_lines.append(line.split("|", 1)[1])
        elif tag == "SVC":
            parts = line.split("|")
            if len(parts) == 4:
                name, pid = parts[1], parts[2]
                nums = [_f(x) for x in parts[3].split()]
                if len(nums) == 3 and all(v is not None for v in nums):
                    result["services"][name] = {
                        "pid": int(float(pid)) if pid.isdigit() else None,
                        "cpu_percent": nums[0],
                        "mem_mb": round(nums[1] / 1024, 1),
                        "uptime_sec": int(nums[2]),
                    }
    if rocm_lines:
        result["gpu"]["devices"] = _parse_rocm(rocm_lines)
    return result


@router.get("")
def get_metrics(host_id: int, services: str = "", user: str = Depends(get_current_user)):
    """主机级 + 运行中服务级指标（单次 SSH 采集）"""
    names = []
    for n in services.split(","):
        n = n.strip()
        if n and n not in names:
            validate_unit_name(n)
            names.append(n)
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        code, out, err = exec_cmd(client, _build_script(names), timeout=METRICS_TIMEOUT)
        if code != 0:
            raise ApiError(SSH_CMD_FAILED, "指标采集失败：%s" % (err or out).strip()[:200])
        return ok(_parse_output(out))
    finally:
        pool.checkin(client)


@router.get("/services/{name}/logs")
def service_logs(name: str, host_id: int, lines: int = 200, user: str = Depends(get_current_user)):
    """服务日志（journalctl 最近 N 行）"""
    validate_unit_name(name)
    try:
        lines = max(1, min(int(lines or 200), MAX_LOG_LINES))
    except (TypeError, ValueError):
        lines = 200
    row = get_host_row(host_id)
    client = pool.checkout(host_to_conn_dict(row))
    try:
        code, out, err = exec_cmd(
            client, "journalctl -u %s -n %d --no-pager -o short-iso 2>&1" % (name, lines), timeout=METRICS_TIMEOUT
        )
        if code != 0 and not out.strip():
            raise ApiError(SSH_CMD_FAILED, "日志读取失败：%s" % (err or out).strip()[:200])
        return ok({"lines": out.splitlines() if out.strip() else []})
    finally:
        pool.checkin(client)
