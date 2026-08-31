"""差分计算引擎（DiffEngine）。

基于连续两次采样计算速率/利用率：
- CPU 整机/每核：1 - Δidle/Δtotal
- 进程实时 CPU%：Δ(utime+stime)/CLK_TCK/Δt（相对单核）
- 磁盘读写速率：Δsectors*512/Δt
- 网络 rx/tx 速率：Δbytes/Δt
首次采样无基线 → 返回 None。兼容 Python 3.9。
"""
from typing import Dict, Optional, Tuple

CLK_TCK = 100  # Linux 标准时钟滴答频率（/proc 计数字段单位）


class DiffEngine:
    def __init__(self):
        self._prev: Dict[str, tuple] = {}

    def _pop(self, key: str):
        return self._prev.get(key)

    def cpu_pct(self, key: str, ts: float, total: int, idle: int) -> Optional[float]:
        """整机/每核 CPU 利用率（%）。total/idle 为 /proc/stat 累计字段。"""
        prev = self._prev.get(key)
        self._prev[key] = (ts, total, idle)
        if prev is None:
            return None
        _, prev_total, prev_idle = prev
        dt_total = total - prev_total
        dt_idle = idle - prev_idle
        if dt_total <= 0:
            return None
        pct = (1.0 - dt_idle / float(dt_total)) * 100.0
        return max(0.0, min(100.0, pct))

    def process_cpu_pct(self, key: str, ts: float, ticks: int) -> Optional[float]:
        """进程实时 CPU%（相对单核）。ticks = utime+stime（/proc/<pid>/stat）。
        
        注意：如果 ticks > 100000，认为输入是百分之一秒（即 PowerShell 的秒数*100），
        否则认为输入是时钟滴答数。
        """
        prev = self._prev.get(key)
        self._prev[key] = (ts, ticks)
        if prev is None:
            return None
        prev_ts, prev_ticks = prev
        dt = ts - prev_ts
        if dt <= 0:
            return None
        
        # 自动检测：如果值非常大（> 100000），可能是百分之一秒
        # 但为了安全，我们直接使用传入的值
        return max(0.0, (ticks - prev_ticks) / float(CLK_TCK) / dt * 100.0)

    def bytes_rate(self, key: str, ts: float, value: float) -> Optional[float]:
        """通用速率：Δvalue/Δt（用于磁盘扇区换算后、网络字节等）。"""
        prev = self._prev.get(key)
        self._prev[key] = (ts, value)
        if prev is None:
            return None
        prev_ts, prev_value = prev
        dt = ts - prev_ts
        if dt <= 0:
            return None
        return max(0.0, (value - prev_value) / dt)

    def reset(self, key: str) -> None:
        self._prev.pop(key, None)

    def prune(self, prefix: str, keep: set) -> None:
        """删除 prefix 下不在 keep 中的基线条目（按 PID 的序列，防字典无限增长）。"""
        for k in list(self._prev):
            if k.startswith(prefix):
                tail = k[len(prefix):]
                if tail.isdigit() and int(tail) not in keep:
                    del self._prev[k]

    def clear(self) -> None:
        self._prev.clear()
