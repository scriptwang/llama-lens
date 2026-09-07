"""内存环形缓冲（RingBuffer）。

- 每个序列为 deque[(ts, value)]，maxlen 限制点数。
- llama 序列 @1s（3600 点 = 1h），host 序列 @2s（1800 点 = 1h）。
- 断线时不写入 → 时间轴出现空隙（前端据此渲染断点，不插值）。
- 兼容 Python 3.9。
"""
from collections import deque
from typing import Dict, List, Optional, Tuple


class RingBuffer:
    def __init__(self, maxlen: int, sink=None):
        self.maxlen = maxlen
        self._series: Dict[str, deque] = {}
        self._sink = sink  # 可选持久化回调 sink(name, ts, value)

    def push(self, name: str, ts: float, value) -> None:
        dq = self._series.get(name)
        if dq is None:
            dq = deque(maxlen=self.maxlen)
            self._series[name] = dq
        dq.append((ts, value))
        if self._sink is not None:
            try:
                self._sink(name, ts, value)
            except Exception:
                pass  # 持久化失败不影响实时链路

    def load(self, name: str, points: List[Tuple[float, object]]) -> None:
        """批量载入历史点（不触发 sink，用于启动时从历史 DB 回填环形缓冲）。"""
        if not points:
            return
        dq = self._series.get(name)
        if dq is None:
            dq = deque(maxlen=self.maxlen)
            self._series[name] = dq
        for ts, v in points:
            dq.append((ts, v))

    def get(self, name: str) -> List[Tuple[float, object]]:
        dq = self._series.get(name)
        return list(dq) if dq else []

    def window(self, name: str, window_s: float, now: float) -> List[Tuple[float, object]]:
        """返回 [now - window_s, now] 内的 (ts, value) 序列。"""
        dq = self._series.get(name)
        if not dq:
            return []
        cutoff = now - window_s
        out = []
        for ts, v in dq:
            if ts >= cutoff:
                out.append((ts, v))
        return out

    def names(self) -> List[str]:
        return list(self._series.keys())

    def last(self, name: str):
        dq = self._series.get(name)
        if not dq:
            return None
        return dq[-1]


def downsample(points: List[Tuple[float, object]], max_points: int = 600) -> List[Tuple[float, object]]:
    """点数超过 max_points 时按步长降采样（保留首尾）。"""
    n = len(points)
    if n <= max_points:
        return points
    step = n / float(max_points)
    out = []
    last_i = -1
    idx = 0.0
    while idx < n:
        i = min(int(idx), n - 1)
        if i != last_i:
            out.append(points[i])
            last_i = i
        idx += step
    if last_i != n - 1:
        out.append(points[n - 1])
    return out


def insert_breaks(points: List[Tuple[float, object]], cadence: float,
                  gap_factor: float = 3.0) -> List[Tuple[float, Optional[object]]]:
    """在时间空隙 > gap_factor*cadence 处插入 None，形成曲线断点（不插值）。

    返回 [(ts, value_or_None), ...]，None 表示断点。
    """
    if not points:
        return []
    out = [(points[0][0], points[0][1])]
    for i in range(1, len(points)):
        prev_ts, prev_v = points[i - 1]
        ts, v = points[i]
        if (ts - prev_ts) > gap_factor * cadence:
            # 在断点处插入一个 None（沿用当前 ts，前端 connectNulls=false 会断开）
            out.append((ts, None))
        out.append((ts, v))
    return out
