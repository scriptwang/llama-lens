"""llama-server 日志采集器（LogPoller）。

- 专用 SSH channel 跑 `journalctl -u {unit} -f` 流式跟随（与 SshPoller 同一 transport）。
- 重连后 `--since` 补拉 catchup_sec（默认 30s）防丢行；follow=false 时周期拉取。
- 解析 11 类行（正则集中于此），维护"llama 当前在干嘛"状态机。
- 行解析失败只跳过并计数，不影响整体；PID 变化（服务重启）→ 重置状态机 + 重拉 boot 块。
- 兼容 Python 3.9。
"""
import asyncio
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from ..config import HostConfig
from ..events import EventDetector
from ..store import RingBuffer

log = logging.getLogger("llamalens.logpoller")

# ---------------------------------------------------------------------------
# 行前缀：2026-08-28T18:11:25+08:00 ai llama-server[55646]: 50.44.267.087 I slot release: ...
# ---------------------------------------------------------------------------
RE_PREFIX = re.compile(r"^(\S+)\s+(\S+)\s+([\w.-]+)\[(\d+)\]:\s*(.*)$")
RE_LEVEL = re.compile(r"^\S+\s+([IWE])\s+\w+\s+\w+:\s*")

# 11 类解析规则（按序匹配，见架构文档 §4.4）
RE_DECODE = re.compile(
    r"slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*"
    r"n_decoded\s*=\s*(\d+),\s*tg\s*=\s*([\d.]+)\s*t/s,\s*tg_3s\s*=\s*([\d.]+)\s*t/s")
RE_PROMPT = re.compile(
    r"slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*"
    r"prompt processing,\s*n_tokens\s*=\s*(\d+),\s*progress\s*=\s*([\d.]+),\s*"
    r"t\s*=\s*([\d.]+)\s*s\s*/\s*([\d.]+)\s*tokens per second")
RE_PROMPT_SUMMARY = re.compile(
    r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*"
    r"\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\)")
RE_EVAL_SUMMARY = re.compile(
    r"(?<!prompt )eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*"
    r"\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\)")
RE_TOTAL_SUMMARY = re.compile(r"total time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens")
RE_GRAPHS = re.compile(r"graphs reused\s*=\s*(\d+)")
RE_MTP = re.compile(
    r"draft acceptance\s*=\s*([\d.]+)\s*\(\s*(\d+) accepted\s*/\s*(\d+) generated\),\s*"
    r"mean len\s*=\s*([\d.]+)")
RE_RELEASE = re.compile(
    r"slot\s+release:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*"
    r"stop processing:\s*n_tokens\s*=\s*(\d+),\s*truncated\s*=\s*(\d+)")
RE_SLOT_SELECT = re.compile(
    r"slot\s+get_availabl:\s+id\s+(\d+)\s*\|\s*task\s+-1\s*\|\s*"
    r"selected slot by\s+(LRU|LCP similarity)"
    r"(?:,\s*f_sim_best\s*=\s*([\d.]+)\s*\(>\s*[\d.]+\s+thold\),\s*f_keep\s*=\s*([\d.]+))?")
RE_LAUNCH = re.compile(
    r"slot\s+launch_slot_:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*"
    r"processing task,\s*is_child\s*=\s*(\d+)")

# 启动行
RE_BOOT_MODEL = re.compile(r"loading model\s+'(.+?)'")
RE_BOOT_SLOTS = re.compile(r"n_slots\s*=\s*(\d+),\s*n_ctx_slot\s*=\s*(\d+),\s*kv_unified\s*=\s*'(\w+)'")
RE_BOOT_MTP = re.compile(r"creating MTP draft context")
RE_BOOT_KVUP = re.compile(r"upgrading K from\s+(\S+)\s+to\s+(\S+)")
RE_BOOT_VERB = re.compile(r"verbosity\s*=\s*(\d+)")
RE_BOOT_LISTEN = re.compile(r"listening on\s+(\S+)")

BOOT_BLOCK_CMD = (
    "journalctl -u {unit} -o short-iso --no-pager -n 50000 2>/dev/null "
    "| grep -B 200 'listening on' | tail -201"
)
BOOT_BLOCK_CMD_FILE = (
    "tail -n 50000 {path} 2>/dev/null | grep -B 200 'listening on' | tail -201"
)
BOOT_BLOCK_CMD_WIN = (
    "powershell -NoProfile -Command \"Get-WinEvent -LogName Application -MaxEvents 50000 | Where-Object {{ $_.Message -like '*llama-server*' }} | Select-Object TimeCreated,Message -Last 500 | ConvertTo-Json -Compress\""
)


def _f(x, default=None):
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def _i(x, default=None):
    try:
        return int(float(x))
    except (ValueError, TypeError):
        return default


def _empty_state() -> Dict[str, Any]:
    return {
        "phase": "idle",
        "task_id": None,
        "n_decoded": 0,
        "tg_tps": None,
        "tg_3s_tps": None,
        "prompt_progress": None,
        "prompt_speed_tps": None,
        "prompt_total_tokens": None,
        "prompt_elapsed_s": None,
        "is_child": None,
        "started_at": None,
    }


class LogPoller:
    """每主机一个日志流采集器。state 供 HostMonitor.snapshot 读取。"""

    def __init__(self, cfg: HostConfig, ssh, events: EventDetector, ring: RingBuffer):
        self.cfg = cfg
        self.ssh = ssh
        self.events = events
        self.ring = ring
        self.available = False
        self.parse_errors = 0
        self.state: Dict[str, Any] = {
            "available": False,
            "state": _empty_state(),
            "context": {"used": None, "total": None, "pct": None, "remaining": None, "truncated": False},
            "mtp": {"acceptance": None, "accepted": None, "generated": None, "mean_len": None},
            "kv": {"f_keep": None, "f_sim_best": None, "selection": None},
            "graphs_reused": None,
            "boot": {},
            "last_task": None,
            "last_prefill": None,
        }
        self._pid: Optional[int] = None
        self._pid_candidate: Optional[int] = None
        self._boot_pids: set = set()
        self._last_line_ts: Optional[float] = None
        self._stream_open_ts: Optional[float] = None
        self._boot_seen = False
        self._file_offset: Optional[int] = None
        self._summary: Dict[str, Any] = {}
        self._stopped = False
        self._backoff = 1.0
        self._prefill_task: Optional[int] = None

    # ------------------------------------------------------------------
    async def start(self) -> None:
        log.info("[%s] LogPoller 启动（unit=%s follow=%s）",
                 self.cfg.id, self.cfg.log.unit, self.cfg.log.follow)
        cfg = self.cfg.log
        if cfg.source == "file" and not cfg.path:
            # source=file 必须配置 path：否则 tail 命令无文件参数会读 SSH
            # channel 的 stdin（paramiko 永不关闭），挂起至超时并反复拖垮
            # 共享连接，表现为"SSH 命令执行失败"（空错误信息）
            log.error("[%s] 日志配置错误: source=file 但未配置 log.path，日志采集已禁用",
                      self.cfg.id)
            self.available = False
            self.state["available"] = False
            return
        while not self._stopped:
            try:
                if not await self.ssh.ensure_connected():
                    self.available = False
                    self.state["available"] = False
                    await asyncio.sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, 30.0)
                    continue
                self._backoff = 1.0
                if not self.state["boot"]:
                    await self._fetch_boot_block()
                if cfg.follow:
                    await self._follow()
                else:
                    await self._poll_mode()
                self.available = False
                self.state["available"] = False
                await asyncio.sleep(1.0)
            except Exception:
                # 单轮异常不能杀死整个 poller 任务（同 SshPoller）
                log.exception("[%s] 日志采集异常", self.cfg.id)
                self.available = False
                self.state["available"] = False
                await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._stopped = True

    # ------------------------------------------------------------------
    async def _fetch_boot_block(self) -> None:
        """拉取最近一次启动块（listening 前 200 行）解析 boot 信息。

        注意：只匹配 boot 行，不走 _handle_line（含 PID 变化检测）。
        boot 块含上一次运行的旧 PID 行，若喂入状态机会触发
        "PID 变化" → 再拉 boot 块的级联放大。
        """
        cfg = self.cfg.log
        if cfg.source == "file":
            cmd = BOOT_BLOCK_CMD_FILE.format(path=cfg.path or "")
        elif cfg.source == "windows_eventlog":
            cmd = BOOT_BLOCK_CMD_WIN
        else:
            cmd = BOOT_BLOCK_CMD.format(unit=cfg.unit)
        out = await self.ssh.exec_command(cmd)
        if out is None:
            return
        for line in out.splitlines():
            m = RE_PREFIX.match(line)
            self._parse_boot_line(m.group(5) if m else line)

    def _follow_cmd(self) -> str:
        """按日志源（journal | file | windows_eventlog）构造流式跟随命令。"""
        cfg = self.cfg.log
        if cfg.source == "file":
            path = cfg.path or ""
            # 检测是否是 Windows 路径（包含盘符如 C:\ 或 E:\）
            is_windows_path = len(path) >= 2 and path[1] == ':'
            if is_windows_path:
                # Windows: 使用 PowerShell 的 Get-Content -Wait
                if self._last_line_ts is not None:
                    # 重连后读取最后 200 行
                    return 'powershell -NoProfile -Command "Get-Content \\"%s\\" -Tail 200 -Wait"' % path.replace('\\', '\\\\')
                return 'powershell -NoProfile -Command "Get-Content \\"%s\\" -Wait"' % path.replace('\\', '\\\\')
            else:
                # Linux: 使用 tail -F
                if self._last_line_ts is not None:
                    # 重连后补拉最近 200 行防丢行
                    return "tail -n 200 -F %s" % path
                return "tail -n 0 -F %s" % path
        elif cfg.source == "windows_eventlog":
            # Windows 事件日志：使用 Get-WinEvent 实时监控
            if self._last_line_ts is not None:
                since = int(self._last_line_ts) - cfg.catchup_sec
                return "powershell -NoProfile -Command \"Get-WinEvent -FilterHashtable {{LogName='Application'; StartTime=(Get-Date).AddSeconds(-{0})}} -MaxEvents 1000 | Where-Object {{ $_.Message -like '*llama-server*' }} | Format-List TimeCreated,Message\"".format(cfg.catchup_sec)
            return "powershell -NoProfile -Command \"Get-WinEvent -LogName Application -MaxEvents 100 | Where-Object {{ $_.Message -like '*llama-server*' }} | Format-List TimeCreated,Message\""
        else:
            # journal 模式
            unit = cfg.unit
            if self._last_line_ts is not None:
                since = int(time.time()) - cfg.catchup_sec
                return "journalctl -u %s -o short-iso --no-pager --since '@%d' -f" % (unit, since)
            return "journalctl -u %s -o short-iso --no-pager -f -n 0" % unit

    async def _follow(self) -> None:
        """流式跟随日志（journal 或 file）；channel 断开后由 start() 循环重连并补拉。"""
        cmd = self._follow_cmd()

        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue" = asyncio.Queue()

        def _open():
            return self.ssh.open_stream(cmd)

        try:
            stdout = await loop.run_in_executor(None, _open)
        except Exception as e:
            log.warning("[%s] 日志流打开失败: %s", self.cfg.id, e)
            self.ssh._drop()
            return

        self.available = True
        self.state["available"] = True
        self._stream_open_ts = time.time()

        def _reader():
            try:
                while True:
                    raw = stdout.readline()
                    if not raw:
                        break
                    # paramiko exec_command 的 stdout 为文本模式（返回 str）；
                    # 个别版本/用法为二进制模式（返回 bytes），两种都兼容
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    line = raw.rstrip("\n")
                    loop.call_soon_threadsafe(queue.put_nowait, line)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # 阻塞 readline 放专用守护线程，不占用默认线程池（SSH exec/连接共用）；
        # channel 关闭后 readline 返回空，线程自行退出
        reader = threading.Thread(target=_reader, name="logpoller-%s" % self.cfg.id,
                                  daemon=True)
        reader.start()
        try:
            while not self._stopped:
                line = await queue.get()
                if line is None:
                    break
                self._handle_line(line)
        finally:
            try:
                stdout.close()
            except Exception:
                pass

    async def _poll_mode(self) -> None:
        """follow=false 兜底：每 2s 拉新行（journal 用 --since，file 用字节偏移）。"""
        cfg = self.cfg.log
        while not self._stopped:
            if cfg.source == "file":
                await self._poll_file_once()
            else:
                since = int(self._last_line_ts or (time.time() - 60))
                cmd = "journalctl -u %s -o short-iso --no-pager --since '@%d' -n 500" % (cfg.unit, since)
                out = await self.ssh.exec_command(cmd)
                if out is None:
                    self.available = False
                    self.state["available"] = False
                    await asyncio.sleep(2.0)
                    continue
                self.available = True
                self.state["available"] = True
                for line in out.splitlines():
                    self._handle_line(line)
            await asyncio.sleep(2.0)

    async def _poll_file_once(self) -> None:
        """file 模式周期拉取：按字节偏移读新增内容（处理轮转/截断）。"""
        path = self.cfg.log.path or ""
        
        # 检测是否是 Windows 路径（包含盘符如 C:\ 或 E:\）
        is_windows_path = len(path) >= 2 and path[1] == ':'
        
        if is_windows_path:
            # Windows: 使用 PowerShell 读取文件
            size_cmd = 'powershell -NoProfile -Command "(Get-Item \\"%s\\" -ErrorAction SilentlyContinue).Length" 2>/dev/null' % path.replace('\\', '\\\\')
            size_out = await self.ssh.exec_command(size_cmd)
        else:
            size_out = await self.ssh.exec_command(
                "stat -c %%s %s 2>/dev/null || echo 0" % path)
        
        if size_out is None:
            self.available = False
            self.state["available"] = False
            return
        
        try:
            size = int(size_out.strip() or 0)
        except ValueError:
            size = 0
        
        if self._file_offset is None:
            # 首次：从当前文件末尾开始，只读新增内容
            self._file_offset = size
            self.available = True
            self.state["available"] = True
            return
        
        if size < self._file_offset:
            # 文件轮转/截断：跳过已有内容，从新文件末尾继续读，避免整文件重读
            self._file_offset = size
        
        self.available = True
        self.state["available"] = True
        
        if size > self._file_offset:
            if is_windows_path:
                # Windows: 使用 PowerShell 读取文件新增内容
                read_cmd = 'powershell -NoProfile -Command "$bytes = [System.IO.File]::ReadAllBytes(\\"%s\\"); if ($bytes.Length -gt %d) { [System.Text.Encoding]::UTF8.GetString($bytes[%d..$($bytes.Length-1)]) }" 2>/dev/null' % (
                    path.replace('\\', '\\\\'), self._file_offset, self._file_offset)
                out = await self.ssh.exec_command(read_cmd)
            else:
                out = await self.ssh.exec_command(
                    "tail -c +%d %s 2>/dev/null" % (self._file_offset + 1, path))
            
            self._file_offset = size
            if out is not None:
                for line in out.splitlines():
                    self._handle_line(line)

    # ------------------------------------------------------------------
    def _reset_machine(self) -> None:
        self._boot_seen = False
        self.state["state"] = _empty_state()
        self.state["context"] = {"used": None, "total": None, "pct": None, "remaining": None, "truncated": False}
        self.state["mtp"] = {"acceptance": None, "accepted": None, "generated": None, "mean_len": None}
        self.state["kv"] = {"f_keep": None, "f_sim_best": None, "selection": None}
        self.state["graphs_reused"] = None
        self.state["boot"] = {}
        self.state["last_task"] = None
        self.state["last_prefill"] = None
        self._prefill_task = None
        self._summary = {}
        self.events.reset_task_state()

    def _handle_line(self, line: str) -> None:
        if not line.strip():
            return
        m = RE_PREFIX.match(line)
        if m:
            syslog_ts, _host, unit, pid, rest = m.groups()
            if unit == "systemd":
                return  # systemd 生命周期行（Starting/Started…），非服务端输出
            pid = int(pid)
            # 补拉行（时间戳早于流打开时刻）不参与 PID 变化检测，
            # 防止补拉窗口里的旧 PID 行被误判为重启
            if not self._is_catchup(syslog_ts):
                if self._pid is not None and pid != self._pid:
                    # 连续 2 行新 PID 才判定重启，防杂散行误触发
                    if self._pid_candidate == pid:
                        log.info("[%s] llama-server PID 变化 %d → %d，重置状态机",
                                 self.cfg.id, self._pid, pid)
                        self._pid = pid
                        self._pid_candidate = None
                        self._reset_machine()
                        if pid not in self._boot_pids:
                            self._boot_pids.add(pid)
                            asyncio.create_task(self._fetch_boot_block())
                    else:
                        self._pid_candidate = pid
                elif self._pid is not None:
                    self._pid_candidate = None
                elif self._pid is None:
                    self._pid = pid
            self._last_line_ts = time.time()
            msg = rest
        else:
            msg = line

        # file 模式：行内无 PID，用 "loading model" 启动行作为重启标记
        if self.cfg.log.source == "file" and RE_BOOT_MODEL.search(msg):
            if self._boot_seen:
                log.info("[%s] 检测到 llama-server 重启（loading model 行），重置状态机",
                         self.cfg.id)
                self._reset_machine()
            self._boot_seen = True

        try:
            self._parse(msg)
        except Exception:
            self.parse_errors += 1

    def _is_catchup(self, syslog_ts: str) -> bool:
        """补拉行判定：行时间戳早于流打开时刻（留 1s 时钟偏差余量）。"""
        if self._stream_open_ts is None:
            return False
        try:
            ts = datetime.fromisoformat(syslog_ts).timestamp()
        except (ValueError, TypeError):
            return False
        return ts < self._stream_open_ts - 1.0

    # ------------------------------------------------------------------
    def _parse(self, msg: str) -> None:
        st = self.state["state"]

        m = RE_DECODE.search(msg)
        if m:
            st["phase"] = "decoding"
            st["task_id"] = _i(m.group(2))
            st["n_decoded"] = _i(m.group(3), 0)
            st["tg_tps"] = _f(m.group(4))
            st["tg_3s_tps"] = _f(m.group(5))
            return

        m = RE_PROMPT.search(msg)
        if m:
            st["phase"] = "prompt_processing"
            st["task_id"] = _i(m.group(2))
            st["prompt_progress"] = _f(m.group(4))
            st["prompt_speed_tps"] = _f(m.group(6))
            st["prompt_total_tokens"] = _i(m.group(3))
            st["prompt_elapsed_s"] = _f(m.group(5))
            # 最近一次预填充的持久记录（总览卡非预填充期也展示）
            self.state["last_prefill"] = {
                "speed": st["prompt_speed_tps"],
                "ts": time.time(),
                "progress": st["prompt_progress"],
                "n_tokens": st["prompt_total_tokens"],
            }
            # 每任务首条 prompt 行 → 预填充开始事件
            if self._prefill_task != st["task_id"]:
                self._prefill_task = st["task_id"]
                ev = "任务 #%s 预填充开始" % st["task_id"]
                if st["prompt_total_tokens"]:
                    ev += " (prompt %d tokens)" % st["prompt_total_tokens"]
                self.events.emit(time.time(), "info", "prefill_start", ev)
            return

        m = RE_RELEASE.search(msg)
        if m:
            self._on_task_end(_i(m.group(2)), _i(m.group(3), 0), _i(m.group(4), 0) == 1)
            return

        m = RE_LAUNCH.search(msg)
        if m:
            st["task_id"] = _i(m.group(2))
            st["is_child"] = _i(m.group(3))
            st["started_at"] = time.time()
            self._summary = {}
            self.events.set_task_running(time.time(), True, st["task_id"])
            return

        m = RE_SLOT_SELECT.search(msg)
        if m:
            kv = self.state["kv"]
            kv["selection"] = m.group(2)
            kv["f_sim_best"] = _f(m.group(3))
            kv["f_keep"] = _f(m.group(4))
            return

        # 任务结束汇总（5 类，可乱序到达，先累积后随 release 落盘）
        m = RE_PROMPT_SUMMARY.search(msg)
        if m:
            self._summary.update(prompt_ms=_f(m.group(1)), prompt_tokens=_i(m.group(2)),
                                 prompt_speed_tps=_f(m.group(4)))
            return
        m = RE_EVAL_SUMMARY.search(msg)
        if m:
            self._summary.update(eval_ms=_f(m.group(1)), decoded_tokens=_i(m.group(2)),
                                 gen_speed_tps=_f(m.group(4)))
            return
        m = RE_TOTAL_SUMMARY.search(msg)
        if m:
            self._summary.update(total_ms=_f(m.group(1)), total_tokens=_i(m.group(2)))
            return
        m = RE_GRAPHS.search(msg)
        if m:
            g = _i(m.group(1))
            self._summary["graphs_reused"] = g
            self.state["graphs_reused"] = g
            return
        m = RE_MTP.search(msg)
        if m:
            self._summary["mtp"] = {
                "acceptance": _f(m.group(1)),
                "accepted": _i(m.group(2)),
                "generated": _i(m.group(3)),
                "mean_len": _f(m.group(4)),
            }
            return

        # 启动行
        if self._parse_boot_line(msg):
            return

        # W 级行 → 警告列表（去重，最多 10 条）
        lm = RE_LEVEL.match(msg)
        if lm and lm.group(1) == "W":
            warnings = self.state["boot"].setdefault("warnings", [])
            text = msg.strip()
            if text not in warnings and len(warnings) < 10:
                warnings.append(text)

    def _parse_boot_line(self, msg: str) -> bool:
        """只匹配启动行（实时流与 boot 块补拉共用）。"""
        m = RE_BOOT_MODEL.search(msg)
        if m:
            self.state["boot"]["model"] = m.group(1)
            return True
        m = RE_BOOT_SLOTS.search(msg)
        if m:
            self.state["boot"]["n_slots"] = _i(m.group(1))
            self.state["boot"]["n_ctx_slot"] = _i(m.group(2))
            self.state["boot"]["kv_unified"] = m.group(3) == "true"
            self._set_ctx_total(_i(m.group(2)))
            return True
        if RE_BOOT_MTP.search(msg):
            self.state["boot"]["mtp_draft"] = True
            return True
        m = RE_BOOT_KVUP.search(msg)
        if m:
            self.state["boot"]["kv_cache_upgrade"] = "%s -> %s" % (m.group(1), m.group(2))
            return True
        m = RE_BOOT_VERB.search(msg)
        if m:
            self.state["boot"]["verbosity"] = _i(m.group(1))
            return True
        m = RE_BOOT_LISTEN.search(msg)
        if m:
            self.state["boot"]["listening"] = m.group(1)
            return True
        return False

    def _set_ctx_total(self, total: Optional[int]) -> None:
        ctx = self.state["context"]
        if total:
            ctx["total"] = total
            self._refresh_ctx()

    def _refresh_ctx(self) -> None:
        ctx = self.state["context"]
        used, total = ctx.get("used"), ctx.get("total")
        if used is not None and total:
            ctx["pct"] = round(used / total * 100.0, 1)
            ctx["remaining"] = max(0, total - used)

    def _on_task_end(self, task_id: Optional[int], n_tokens: int, truncated: bool) -> None:
        now = time.time()
        ctx = self.state["context"]
        ctx["used"] = n_tokens
        ctx["truncated"] = truncated
        self._refresh_ctx()

        summary = self._summary
        mtp = summary.get("mtp")
        if mtp:
            self.state["mtp"] = dict(mtp)

        last_task = {
            "task_id": task_id,
            "prompt_ms": summary.get("prompt_ms"),
            "prompt_tokens": summary.get("prompt_tokens"),
            "prompt_speed_tps": summary.get("prompt_speed_tps"),
            "eval_ms": summary.get("eval_ms"),
            "decoded_tokens": summary.get("decoded_tokens"),
            "gen_speed_tps": summary.get("gen_speed_tps"),
            "total_ms": summary.get("total_ms"),
            "total_tokens": summary.get("total_tokens"),
            "graphs_reused": summary.get("graphs_reused"),
            "mtp": dict(mtp) if mtp else None,
            "ctx_used": n_tokens,
            "truncated": truncated,
        }
        self.state["last_task"] = last_task
        self._summary = {}

        st = self.state["state"]
        st["phase"] = "idle"
        st["n_decoded"] = 0
        st["tg_tps"] = None
        st["tg_3s_tps"] = None
        st["prompt_progress"] = None
        st["prompt_speed_tps"] = None
        st["prompt_total_tokens"] = None
        st["prompt_elapsed_s"] = None
        st["started_at"] = None
        self._prefill_task = None

        # 任务被中断（手动停止/断连）时 llama-server 不打印汇总行（eval/total time、
        # draft acceptance），只有 release 行 → 标注"已中断"
        interrupted = not (summary.get("total_ms") or summary.get("eval_ms"))
        # 事件（EventDetector 内部按任务 ID 去重，日志与 API 双源不重复）
        self.events.task_end_with_stats(
            now, task_id,
            last_task["total_tokens"] or last_task["decoded_tokens"],
            (last_task["total_ms"] / 1000.0) if last_task["total_ms"] else None,
            last_task["gen_speed_tps"],
            mtp["acceptance"] if mtp else None,
            n_tokens,
            "已中断" if interrupted else None,
        )

        # 事件型序列（按任务落点，非周期）
        self.ring.push("ctx_used", now, n_tokens)
        if mtp and mtp.get("acceptance") is not None:
            self.ring.push("mtp_acceptance", now, mtp["acceptance"])
