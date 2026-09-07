"""SSH 只读采集器（SshPoller）。

- 每 2s 一条批量只读命令（一次网络往返），解析 GPU/CPU/内存/磁盘/网络/进程/服务/模型。
- 静态信息（hostname/内核/OS/CPU 型号/核数）首次连接采集一次。
- 差分指标（CPU/进程 CPU/磁盘/网络速率）由 DiffEngine 基于连续两次采样计算。
- 全部只读，不修改被监控主机任何配置。兼容 Python 3.9。
"""
import asyncio
import logging
import re
import shlex
import time
from typing import Optional, Dict, Any, List

from ..config import HostConfig
from ..diff import DiffEngine
from ..events import EventDetector
from ..store import RingBuffer
from .ssh_conn import SshConnection

log = logging.getLogger("llamalens.sshpoller")

# ---------------------------------------------------------------------------
# 批量命令模板（占位符：{process_name} {systemd_unit} {df_mounts}）
# ---------------------------------------------------------------------------

BATCH_CMD = r"""
echo ==GPU==
nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw,power.limit,fan.speed,clocks.current.graphics,clocks.current.memory,pcie.link.gen.current,pcie.link.width.current,pstate,temperature.memory,ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,clocks_throttle_reasons.active --format=csv,noheader,nounits 2>/dev/null
echo ==APPS==
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null
echo ==STAT==
cat /proc/stat
echo ==MEM==
grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|^Cached|SwapTotal|SwapFree)' /proc/meminfo
echo ==LOAD==
cat /proc/loadavg
echo ==UPTIME==
cat /proc/uptime
echo ==NET==
cat /proc/net/dev
echo ==DISKIO==
awk '$3 ~ /^(sd|vd|nvme)/ && $3 !~ /p[0-9]+$/ && $3 !~ /^[sv]d[a-z]+[0-9]+$/ {{print}}' /proc/diskstats
echo ==DF==
df -B1 --output=source,target,size,used,avail,pcent {df_mounts} 2>/dev/null
echo ==PROC==
PID=$(pgrep -x {process_name} | head -1)
if [ -z "$PID" ]; then
  # comm 被内核截断到 15 字符：长进程名回退 cmdline argv[0] basename 匹配
  # （不用 basename 命令：argv[0] 可能以 - 开头[如 -zsh]会被当选项解析）
  for d in /proc/[0-9]*; do
    a0=$(tr '\0' '\n' < "$d/cmdline" 2>/dev/null | head -n 1)
    [ -n "$a0" ] || continue
    case "$a0" in */*) a0=${{a0##*/}} ;; esac
    [ "$a0" = "{process_name}" ] && PID=${{d##*/}} && break
  done
fi
if [ -n "$PID" ]; then
  echo P:$PID
  awk '{{print $14, $15, $23}}' /proc/$PID/stat 2>/dev/null
  grep -E '^(VmRSS|VmSize|Threads)' /proc/$PID/status 2>/dev/null
  ps -o pcpu=,pmem=,etime= -p $PID 2>/dev/null
  tr '\0' ' ' < /proc/$PID/cmdline 2>/dev/null; echo
fi
echo ==PS==
ps -eo pid,comm,pcpu,pmem,rss --no-headers 2>/dev/null
echo ==PSTICKS==
awk 'FNR==1 {{ n=split(FILENAME, p, "/"); pid=p[n-1]; i=index($0, ") "); if (i > 0) {{ s=substr($0, i+2); m=split(s, a, " "); if (m >= 13) print pid, a[12], a[13] }} }}' /proc/[0-9]*/stat 2>/dev/null
echo ==PROCS==
ls /proc 2>/dev/null | grep -c '^[0-9]'
echo ==SERVICE==
systemctl show {systemd_unit} -p Description,ActiveState,SubState,ExecMainStartTimestamp,CPUUsageNSec,MemoryCurrent,MemoryPeak,NTasks 2>/dev/null
echo ==MODELS==
if [ -n "$PID" ]; then
  tr '\0' '\n' < /proc/$PID/cmdline 2>/dev/null | awk 'p=="--model"||p=="-m"||p=="--mmproj"{{print; p=""; next}}{{p=$0}}' | xargs -r -d '\n' ls -l 2>/dev/null
fi
echo ==END==
"""

STATIC_CMD = r"""
echo ==HOSTNAME==
hostname
echo ==KERNEL==
uname -r
echo ==OS==
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
echo ==CPUMODEL==
grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//'
echo ==CORES==
grep -c '^processor' /proc/cpuinfo
echo ==MHZ==
grep -m1 'cpu MHz' /proc/cpuinfo | awk '{print $4}'
echo ==CUDA==
# CUDA 版本静态（驱动不升级不变）：一次性采集，避免每 2s 渲染整张 nvidia-smi 表
nvidia-smi 2>/dev/null | sed -n 3p
echo ==END==
"""


def _f(x, default=None):
    """安全转 float。"""
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def _i(x, default=None):
    """安全转 int。"""
    try:
        return int(float(x))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 分段解析
# ---------------------------------------------------------------------------

def split_sections(output: str) -> Dict[str, str]:
    """把 ==SECTION== 分隔的输出切成 {section: content}。"""
    sections: Dict[str, str] = {}
    current = None
    buf: List[str] = []
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("==") and s.endswith("==") and len(s) > 4:
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current = s[2:-2]
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def _parse_throttle(raw) -> int:
    """clocks_throttle_reasons.active：'Not Active'/十进制/0x 十六进制 → 位掩码 int（0=无降频）。"""
    s = (raw or "").strip()
    if not s or s.upper().startswith("NOT"):
        return 0
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return 0


RE_SMI_CUDA = re.compile(r"CUDA Version:?\s*(\d+\.\d+)")


def parse_smi_cuda(section: str) -> Optional[str]:
    """nvidia-smi 表头行（第 3 行）→ CUDA 版本（如 '13.0'），无匹配 None。

    cuda_version 查询字段部分驱动不支持（580.x 报 not a valid field，整条查询失败），
    改从表头行解析。
    """
    m = RE_SMI_CUDA.search(section or "")
    return m.group(1) if m else None


def parse_gpu(section: str) -> List[Dict[str, Any]]:
    gpus = []
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 21:
            continue
        fan = _f(parts[11])
        gpus.append({
            "index": _i(parts[0], 0),
            "name": parts[1],
            "driver": parts[2],
            "mem_total_mb": _i(parts[3]),
            "mem_used_mb": _i(parts[4]),
            "mem_free_mb": _i(parts[5]),
            "util_pct": _f(parts[6], 0.0),
            "mem_util_pct": _f(parts[7], 0.0),
            "temp_c": _f(parts[8]),
            "power_w": _f(parts[9]),
            "power_limit_w": _f(parts[10]),
            "fan_pct": None if (fan is None or fan < 0) else fan,
            "clock_mhz": _i(parts[12]),
            "mem_clock_mhz": _i(parts[13]),
            "pcie_gen": _i(parts[14]),
            "pcie_width": _i(parts[15]),
            "pstate": parts[16],
            "temp_mem_c": _f(parts[17]),
            "ecc_corrected": _i(parts[18]),
            "ecc_uncorrected": _i(parts[19]),
            "throttle": _parse_throttle(parts[20]),
            "apps": [],
        })
    return gpus


def parse_apps(section: str) -> List[Dict[str, Any]]:
    apps = []
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        pid = _i(parts[0])
        mem = _i(parts[-1])
        name = ",".join(parts[1:-1]).strip()
        if name:
            name = name.rsplit("/", 1)[-1]
        apps.append({"pid": pid, "name": name, "mem_mb": mem})
    return apps


def parse_stat(section: str) -> Dict[str, Any]:
    """解析 /proc/stat。返回 {total, idle, cores: {i: (total, idle)}}。"""
    result = {"total": 0, "idle": 0, "cores": {}}
    for line in section.splitlines():
        if not line.startswith("cpu"):
            continue
        fields = line.split()
        name = fields[0]
        nums = fields[1:]
        if len(nums) < 4:
            continue
        total = sum(_i(x, 0) for x in nums)
        idle = _i(nums[3], 0) + (_i(nums[4], 0) if len(nums) > 4 else 0)
        if name == "cpu":
            result["total"] = total
            result["idle"] = idle
        elif name.startswith("cpu"):
            try:
                idx = int(name[3:])
                result["cores"][idx] = (total, idle)
            except ValueError:
                pass
    return result


def parse_meminfo(section: str) -> Dict[str, Any]:
    kv = {}
    for line in section.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            kv[k.strip()] = _i(v.split()[0] if v else 0, 0)
    total = kv.get("MemTotal", 0)
    free = kv.get("MemFree", 0)
    avail = kv.get("MemAvailable", 0)
    buff_cache = kv.get("Buffers", 0) + kv.get("Cached", 0)
    swap_total = kv.get("SwapTotal", 0)
    swap_free = kv.get("SwapFree", 0)
    return {
        "total_mb": total // 1024,
        "used_mb": (total - free - buff_cache) // 1024,
        "free_mb": free // 1024,
        "buff_cache_mb": buff_cache // 1024,
        "available_mb": avail // 1024,
        "swap_total_mb": swap_total // 1024,
        "swap_used_mb": (swap_total - swap_free) // 1024,
    }


def parse_loadavg(section: str) -> List[float]:
    fields = section.split()
    return [_f(x, 0.0) for x in fields[:3]]


def parse_uptime(section: str) -> Optional[float]:
    fields = section.split()
    return _f(fields[0]) if fields else None


# 虚拟接口前缀（docker 网桥/veth 等，避免流量重复计数）
_NET_EXCLUDE_PREFIXES = ("docker", "br-", "veth", "virbr", "kube", "cni", "cali", "fla", "tun", "tap")


def is_real_iface(name: str) -> bool:
    return not any(name.startswith(pfx) for pfx in _NET_EXCLUDE_PREFIXES)


def parse_netdev(section: str) -> List[Dict[str, Any]]:
    ifaces = []
    for line in section.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo" or not is_real_iface(name):
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        ifaces.append({
            "name": name,
            "rx_bytes": _i(fields[0], 0),
            "tx_bytes": _i(fields[8], 0),
        })
    return ifaces


def parse_diskstats(section: str) -> Dict[str, int]:
    """汇总所有块设备的扇区读/写（近似总速率）。"""
    sectors_read = 0
    sectors_written = 0
    for line in section.splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        sectors_read += _i(fields[5], 0)
        sectors_written += _i(fields[9], 0)
    return {"sectors_read": sectors_read, "sectors_written": sectors_written}


def parse_df(section: str) -> List[Dict[str, Any]]:
    mounts = []
    seen = set()
    lines = [l for l in section.splitlines() if l.strip()]
    for line in lines[1:]:  # 跳过表头
        fields = line.split()
        if len(fields) < 6:
            continue
        source = fields[0]
        if source in seen:
            continue
        seen.add(source)
        mounts.append({
            "mount": fields[1],
            "size_gb": _i(fields[2], 0) / (1024 ** 3),
            "used_gb": _i(fields[3], 0) / (1024 ** 3),
            "avail_gb": _i(fields[4], 0) / (1024 ** 3),
            "use_pct": _f(fields[5].rstrip("%"), 0.0),
        })
    return mounts



def parse_proc(section: str, diff: DiffEngine, ts: float, host_id: str) -> Dict[str, Any]:
    proc = {"found": False}
    lines = [l for l in section.splitlines() if l.strip()]
    if not lines or not lines[0].startswith("P:"):
        return proc
    pid = _i(lines[0][2:])
    proc["found"] = True
    proc["pid"] = pid
    idx = 1
    # utime stime vsize
    if idx < len(lines):
        f = lines[idx].split()
        if len(f) >= 3:
            utime = _i(f[0], 0)
            stime = _i(f[1], 0)
            vsize = _i(f[2], 0)
            proc["vsz_mb"] = vsize // (1024 * 1024)
            proc["cpu_pct_realtime"] = diff.process_cpu_pct(
                "proc:%s:%s" % (host_id, pid), ts, utime + stime)
        idx += 1
    # VmRSS / VmSize / Threads
    while idx < len(lines) and lines[idx].startswith(("VmRSS", "VmSize", "Threads")):
        l = lines[idx]
        if l.startswith("VmRSS"):
            proc["rss_mb"] = _i(l.split()[1], 0) // 1024
        elif l.startswith("VmSize"):
            proc["vsz_mb"] = _i(l.split()[1], 0) // 1024
        elif l.startswith("Threads"):
            proc["threads"] = _i(l.split()[1], 0)
        idx += 1
    # ps pcpu pmem etime
    if idx < len(lines):
        f = lines[idx].split()
        if len(f) >= 3:
            proc["cpu_pct_lifetime"] = _f(f[0])
            proc["mem_pct"] = _f(f[1])
            proc["elapsed"] = f[2]
        idx += 1
    # cmdline（剩余行合并）
    if idx < len(lines):
        proc["cmdline"] = " ".join(l.strip() for l in lines[idx:]).strip()
    return proc


def parse_ps_ticks(section: str) -> Dict[int, int]:
    """解析 ==PSTICKS== 段（每行 `pid utime stime`，来自 /proc/<pid>/stat，时钟滴答）。

    相比 ps 的 time 列（1s 粒度），/proc 的 utime/stime 为 10ms 粒度，
    2s 采样窗口下进程 CPU% 不再被量化成 0/50/100/150 的台阶值。
    """
    ticks: Dict[int, int] = {}
    for line in section.splitlines():
        f = line.split()
        if len(f) < 3:
            continue
        try:
            pid = int(f[0])
            utime = int(f[1])
            stime = int(f[2])
        except ValueError:
            continue
        ticks[pid] = utime + stime
    return ticks


def parse_ps(section: str) -> List[Dict[str, Any]]:
    """解析 `ps -eo pid,comm,pcpu,pmem,rss`。

    comm 可能含空格：pid 取首列，pcpu/pmem/rss 取末 3 列，中间为进程名。
    进程 CPU 滴答不在此处（ps time 列仅 1s 粒度），由 ==PSTICKS== 段按 PID 关联。
    """
    procs = []
    for line in section.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        try:
            pid = int(f[0])
        except ValueError:
            continue
        procs.append({
            "pid": pid,
            "name": " ".join(f[1:-3]),
            "cpu_pct_lifetime": _f(f[-3], 0.0),
            "mem_pct": _f(f[-2], 0.0),
            "rss_mb": _i(f[-1], 0) // 1024,
        })
    return procs


def parse_service(section: str) -> Dict[str, Any]:
    kv = {}
    for line in section.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    svc = {}
    svc["description"] = kv.get("Description", "")
    active = kv.get("ActiveState", "")
    sub = kv.get("SubState", "")
    svc["active"] = "%s (%s)" % (active, sub) if active else ""
    svc["since"] = kv.get("ExecMainStartTimestamp", "")
    cpu_ns = _i(kv.get("CPUUsageNSec"), 0)
    svc["cpu_total"] = _fmt_seconds(cpu_ns / 1e9) if cpu_ns else ""
    mem_cur = _i(kv.get("MemoryCurrent"), 0)
    mem_peak = _i(kv.get("MemoryPeak"), 0)
    svc["memory"] = _fmt_bytes(mem_cur) if mem_cur else ""
    svc["memory_peak"] = _fmt_bytes(mem_peak) if mem_peak else ""
    svc["tasks"] = _i(kv.get("NTasks"), 0)
    return svc


def parse_models(section: str) -> Dict[str, int]:
    """返回 {path: size_bytes}。"""
    result = {}
    for line in section.splitlines():
        line = line.strip()
        if not line or not line.startswith("-"):
            continue
        f = line.split()
        if len(f) < 5:
            continue
        result[f[-1]] = _i(f[4], 0)
    return result


def _fmt_seconds(total_s: float) -> str:
    m = int(total_s // 60)
    s = total_s % 60
    return "%dmin %.3fs" % (m, s)


def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return "%.1fG" % (n / 1024 ** 3)
    if n >= 1024 ** 2:
        return "%.1fM" % (n / 1024 ** 2)
    return "%dB" % n



# ---------------------------------------------------------------------------
# SshPoller
# ---------------------------------------------------------------------------

class SshPoller:
    """每主机一个 SSH 采集器：静态信息一次 + 周期批量采集。"""

    def __init__(self, host_cfg: HostConfig, ssh: SshConnection, diff: DiffEngine,
                 ring: RingBuffer, events: EventDetector):
        self.cfg = host_cfg
        self.ssh = ssh
        self.diff = diff
        self.ring = ring
        self.events = events
        self.host_id = host_cfg.id
        # 共享输出（HostMonitor.snapshot 读取）
        self.metrics: Dict[str, Any] = {
            "reachable": False,
            "sys": {}, "cpu": {}, "mem": {}, "disk": {}, "net": {},
            "gpus": [], "process": {"found": False}, "service": {},
            "top": {"cpu": [], "mem": []},
        }
        self._static_done = False
        self._cuda_ver: Optional[str] = None
        self._last_ts: Optional[float] = None
        self._stopped = False

    def _build_batch_cmd(self) -> str:
        mounts = self.cfg.disk_mounts or ["/"]
        # 参数全部 shlex.quote：即使配置被绕过校验写入脏值，也无法注入命令
        return BATCH_CMD.format(
            process_name=shlex.quote(self.cfg.process_name),
            systemd_unit=shlex.quote(self.cfg.systemd_unit),
            df_mounts=" ".join(shlex.quote(m) for m in mounts),
        )

    async def _collect_static(self) -> None:
        out = await self.ssh.exec_command(STATIC_CMD)
        if out is None:
            return
        sec = split_sections(out)
        sysinfo = self.metrics["sys"]
        sysinfo["hostname"] = sec.get("HOSTNAME", "").strip()
        sysinfo["kernel"] = sec.get("KERNEL", "").strip()
        sysinfo["os"] = sec.get("OS", "").strip()
        cpu = self.metrics["cpu"]
        cpu["model"] = sec.get("CPUMODEL", "").strip()
        cpu["cores"] = _i(sec.get("CORES", "").strip(), 0)
        cpu["mhz"] = _f(sec.get("MHZ", "").strip())
        self._cuda_ver = parse_smi_cuda(sec.get("CUDA", ""))
        self._static_done = True
        log.info("[%s] 静态信息: %s", self.host_id, sysinfo)

    async def start(self) -> None:
        log.info("[%s] SshPoller 启动（间隔 %.1fs）", self.host_id, self.cfg.ssh.interval)
        while not self._stopped:
            try:
                if not self._static_done:
                    await self._collect_static()
                await self._cycle()
            except Exception:
                # 单周期异常（命令构造/解析错误）不能杀死整个 poller 任务，
                # 否则主机将永久停在"SSH 断开"且无任何日志痕迹
                log.exception("[%s] 采集周期异常", self.host_id)
                self.metrics["reachable"] = False
            await asyncio.sleep(self.cfg.ssh.interval)

    def stop(self) -> None:
        self._stopped = True

    async def _cycle(self) -> None:
        ts = time.time()
        out = await self.ssh.exec_command(self._build_batch_cmd())
        if out is None:
            self.metrics["reachable"] = False
            return
        self.metrics["reachable"] = True
        sec = split_sections(out)
        self._parse_cycle(sec, ts)

    def _parse_cycle(self, sec: Dict[str, str], ts: float) -> None:
        m = self.metrics
        # GPU + APPS
        gpus = parse_gpu(sec.get("GPU", ""))
        apps = parse_apps(sec.get("APPS", ""))
        cuda_ver = self._cuda_ver
        for g in gpus:
            g["apps"] = apps
            g["cuda"] = cuda_ver
        m["gpus"] = gpus

        # CPU（整机 + 每核差分）
        stat = parse_stat(sec.get("STAT", ""))
        cpu = m["cpu"]
        cpu["usage_pct"] = self.diff.cpu_pct("cpu:%s" % self.host_id, ts, stat["total"], stat["idle"])
        per_core = []
        for i in sorted(stat["cores"].keys()):
            total, idle = stat["cores"][i]
            per_core.append(self.diff.cpu_pct("cpu:%s:%d" % (self.host_id, i), ts, total, idle))
        cpu["per_core_pct"] = per_core
        cpu["load"] = parse_loadavg(sec.get("LOAD", ""))

        # 内存
        m["mem"] = parse_meminfo(sec.get("MEM", ""))

        # 磁盘（df + diskstats 差分）
        diskio = parse_diskstats(sec.get("DISKIO", ""))
        m["disk"] = {
            "mounts": parse_df(sec.get("DF", "")),
            "read_mb_s": (self.diff.bytes_rate("diskr:%s" % self.host_id, ts, diskio["sectors_read"]) or 0) * 512 / 1024 / 1024,
            "write_mb_s": (self.diff.bytes_rate("diskw:%s" % self.host_id, ts, diskio["sectors_written"]) or 0) * 512 / 1024 / 1024,
        }

        # 网络（差分）
        ifaces = parse_netdev(sec.get("NET", ""))
        net_out = []
        for itf in ifaces:
            rx_rate = self.diff.bytes_rate("netrx:%s:%s" % (self.host_id, itf["name"]), ts, itf["rx_bytes"])
            tx_rate = self.diff.bytes_rate("nettx:%s:%s" % (self.host_id, itf["name"]), ts, itf["tx_bytes"])
            net_out.append({
                "name": itf["name"],
                "rx_mb_s": (rx_rate or 0) / 1024 / 1024,
                "tx_mb_s": (tx_rate or 0) / 1024 / 1024,
                "rx_total_mb": itf["rx_bytes"] / 1024 / 1024,
                "tx_total_mb": itf["tx_bytes"] / 1024 / 1024,
            })
        m["net"] = {"ifaces": net_out}

        # 进程
        m["process"] = parse_proc(sec.get("PROC", ""), self.diff, ts, self.host_id)

        # Top 进程（实时 CPU% = Δ(utime+stime)/Δt；Top CPU 按实时排序，Top 内存按 RSS 排序）
        procs = parse_ps(sec.get("PS", ""))
        ps_ticks = parse_ps_ticks(sec.get("PSTICKS", ""))
        ps_rows = []
        for p in procs:
            cpu_ticks = ps_ticks.get(p["pid"], 0)
            rt = self.diff.process_cpu_pct(
                "ps:%s:%d" % (self.host_id, p["pid"]), ts, cpu_ticks)
            ps_rows.append({
                "pid": p["pid"],
                "name": p["name"],
                "cpu_pct": rt if rt is not None else 0.0,
                "mem_pct": p["mem_pct"],
                "rss_mb": p["rss_mb"],
                "_ticks": cpu_ticks,
            })
        self.diff.prune("ps:%s:" % self.host_id, {p["pid"] for p in procs})
        # Top CPU：实时 CPU% 并列时（如系统空闲全 0.0）按累计 CPU 时间兜底，
        # 避免列表退化为 Top 内存的副本（RSS 序）看起来"数据是假的"
        top_cpu = sorted(ps_rows, key=lambda r: (-r["cpu_pct"], -r["_ticks"], -r["rss_mb"]))[:8]
        top_mem = sorted(ps_rows, key=lambda r: (-r["rss_mb"], -r["cpu_pct"]))[:8]
        for r in top_cpu + top_mem:
            r.pop("_ticks", None)
        m["top"] = {"cpu": top_cpu, "mem": top_mem}

        # 系统（uptime + procs，合并静态信息）
        m["sys"]["uptime_s"] = parse_uptime(sec.get("UPTIME", ""))
        m["sys"]["procs"] = _i(sec.get("PROCS", "").strip(), 0)

        # 服务
        m["service"] = parse_service(sec.get("SERVICE", ""))
        m["service"]["unit"] = self.cfg.systemd_unit

        # 模型文件体积
        m["_model_sizes"] = parse_models(sec.get("MODELS", ""))

        self._push_ring(ts)

    def _push_ring(self, ts: float) -> None:
        m = self.metrics
        cpu = m.get("cpu", {})
        mem = m.get("mem", {})
        disk = m.get("disk", {})
        net = m.get("net", {})
        proc = m.get("process", {})
        self.ring.push("cpu", ts, cpu.get("usage_pct"))
        load = cpu.get("load") or []
        for i, name in enumerate(("load_1", "load_5", "load_15")):
            self.ring.push(name, ts, load[i] if i < len(load) else None)
        self.ring.push("mem_used", ts, mem.get("used_mb"))
        self.ring.push("mem_buff_cache", ts, mem.get("buff_cache_mb"))
        self.ring.push("swap_used", ts, mem.get("swap_used_mb"))
        self.ring.push("disk_read", ts, disk.get("read_mb_s"))
        self.ring.push("disk_write", ts, disk.get("write_mb_s"))
        ifaces = net.get("ifaces", [])
        self.ring.push("net_rx", ts, sum(i.get("rx_mb_s", 0) for i in ifaces))
        self.ring.push("net_tx", ts, sum(i.get("tx_mb_s", 0) for i in ifaces))
        self.ring.push("proc_cpu", ts, proc.get("cpu_pct_realtime"))
        for g in m.get("gpus", []):
            idx = g.get("index", 0)
            self.ring.push("gpu_util_%d" % idx, ts, g.get("util_pct"))
            self.ring.push("gpu_mem_%d" % idx, ts, g.get("mem_used_mb"))
            self.ring.push("gpu_temp_%d" % idx, ts, g.get("temp_c"))
            self.ring.push("gpu_power_%d" % idx, ts, g.get("power_w"))
        self._last_ts = ts
