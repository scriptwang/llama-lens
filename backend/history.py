"""监控数据持久化：SQLite 两级存储（原始 + 1 分钟聚合）+ 事件落库。

- HistoryStore：SQLite（WAL）读写，独立文件 data/history.db。
- HistoryWriter：有界内存队列 + 后台线程批量刷盘（默认 5s），
  每分钟做 1m 聚合，每小时清理超期数据。
- 崩溃最多丢最后一个刷盘周期（≤ flush_interval）的数据。
- 兼容 Python 3.9。
"""
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("llamalens.history")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ts_llama (
  host_id TEXT NOT NULL, ts INTEGER NOT NULL,
  gen_speed REAL, prompt_speed REAL, ctx_used REAL, mtp_acceptance REAL,
  PRIMARY KEY (host_id, ts)
);
CREATE TABLE IF NOT EXISTS ts_host (
  host_id TEXT NOT NULL, ts INTEGER NOT NULL,
  cpu REAL, mem_used REAL, mem_buff_cache REAL, swap_used REAL,
  net_rx REAL, net_tx REAL, proc_cpu REAL,
  load_1 REAL, load_5 REAL, load_15 REAL,
  gpu TEXT,
  PRIMARY KEY (host_id, ts)
);
CREATE TABLE IF NOT EXISTS ts_llama_1m (
  host_id TEXT NOT NULL, ts INTEGER NOT NULL,
  gen_avg REAL, gen_max REAL, prompt_avg REAL, ctx_max REAL, mtp_avg REAL,
  PRIMARY KEY (host_id, ts)
);
CREATE TABLE IF NOT EXISTS ts_host_1m (
  host_id TEXT NOT NULL, ts INTEGER NOT NULL,
  cpu_avg REAL, mem_used_max REAL, swap_used_max REAL,
  net_rx_sum REAL, net_tx_sum REAL, proc_cpu_avg REAL,
  gpu TEXT,
  PRIMARY KEY (host_id, ts)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  host_id TEXT NOT NULL, ts INTEGER NOT NULL,
  level TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_host_ts ON events(host_id, ts);
"""

LLAMA_SERIES = ("gen_speed", "prompt_speed", "ctx_used", "mtp_acceptance")
HOST_SERIES = ("cpu", "mem_used", "mem_buff_cache", "swap_used",
               "net_rx", "net_tx", "proc_cpu", "load_1", "load_5", "load_15")
GPU_KINDS = {"gpu_util": "util", "gpu_mem": "mem", "gpu_temp": "temp", "gpu_power": "power"}


def _gpu_name(name: str) -> Optional[Tuple[str, str]]:
    """gpu_util_0 -> ('0', 'util')；非 GPU 序列返回 None。"""
    for prefix, kind in GPU_KINDS.items():
        if name.startswith(prefix + "_"):
            return name[len(prefix) + 1:], kind
    return None


class HistoryStore:
    """SQLite 历史存储（单写者：HistoryWriter 线程；读：API 线程，WAL 并发）。"""

    def __init__(self, db_path: str):
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def write_llama(self, rows: List[Tuple]) -> None:
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO ts_llama VALUES (?,?,?,?,?,?)", rows)
            self._conn.commit()

    def write_host(self, rows: List[Tuple]) -> None:
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO ts_host VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def write_llama_1m(self, rows: List[Tuple]) -> None:
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO ts_llama_1m VALUES (?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def write_host_1m(self, rows: List[Tuple]) -> None:
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO ts_host_1m VALUES (?,?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def write_event(self, host_id: str, ts: float, level: str,
                    type_: str, msg: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (host_id, ts, level, type, message) VALUES (?,?,?,?,?)",
                (host_id, int(ts), level, type_, msg))
            self._conn.commit()

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def query_llama(self, host_id: str, t0: int, t1: int, stride: int = 1) -> List[Tuple]:
        sql = ("SELECT ts, gen_speed, prompt_speed, ctx_used, mtp_acceptance "
               "FROM ts_llama WHERE host_id=? AND ts BETWEEN ? AND ?")
        params: List[Any] = [host_id, t0, t1]
        if stride > 1:
            sql += " AND ts % ? = 0"
            params.append(stride)
        sql += " ORDER BY ts"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_host(self, host_id: str, t0: int, t1: int, stride: int = 1) -> List[Tuple]:
        sql = ("SELECT ts, cpu, mem_used, mem_buff_cache, swap_used, net_rx, net_tx, "
               "proc_cpu, load_1, load_5, load_15, gpu "
               "FROM ts_host WHERE host_id=? AND ts BETWEEN ? AND ?")
        params = [host_id, t0, t1]
        if stride > 1:
            sql += " AND ts % ? = 0"
            params.append(stride)
        sql += " ORDER BY ts"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_llama_1m(self, host_id: str, t0: int, t1: int, stride: int = 1) -> List[Tuple]:
        sql = ("SELECT ts, gen_avg, gen_max, prompt_avg, ctx_max, mtp_avg "
               "FROM ts_llama_1m WHERE host_id=? AND ts BETWEEN ? AND ?")
        params: List[Any] = [host_id, t0, t1]
        if stride > 1:
            sql += " AND ts % ? = 0"
            params.append(stride)
        sql += " ORDER BY ts"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_host_1m(self, host_id: str, t0: int, t1: int, stride: int = 1) -> List[Tuple]:
        sql = ("SELECT ts, cpu_avg, mem_used_max, swap_used_max, net_rx_sum, "
               "net_tx_sum, proc_cpu_avg, gpu "
               "FROM ts_host_1m WHERE host_id=? AND ts BETWEEN ? AND ?")
        params = [host_id, t0, t1]
        if stride > 1:
            sql += " AND ts % ? = 0"
            params.append(stride)
        sql += " ORDER BY ts"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_events(self, host_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, level, type, message FROM events "
                "WHERE host_id=? ORDER BY ts DESC LIMIT ?",
                (host_id, limit)).fetchall()
        return [{"ts": float(r[0]), "level": r[1], "type": r[2], "msg": r[3]}
                for r in reversed(rows)]

    # ------------------------------------------------------------------
    # 聚合与清理
    # ------------------------------------------------------------------
    def aggregate_minute(self, host_id: str, minute_start: int) -> None:
        """把 [minute_start, minute_start+60) 的原始数据聚合进 1m 表。"""
        t0, t1 = minute_start, minute_start + 60
        ll = self.query_llama(host_id, t0, t1)
        if ll:
            def _avg(idx):
                vs = [r[idx] for r in ll if r[idx] is not None]
                return sum(vs) / len(vs) if vs else None
            def _max(idx):
                vs = [r[idx] for r in ll if r[idx] is not None]
                return max(vs) if vs else None
            self.write_llama_1m([(host_id, t0, _avg(1), _max(1), _avg(2), _max(3), _avg(4))])
        hh = self.query_host(host_id, t0, t1)
        if hh:
            def _havg(idx):
                vs = [r[idx] for r in hh if r[idx] is not None]
                return sum(vs) / len(vs) if vs else None
            def _hmax(idx):
                vs = [r[idx] for r in hh if r[idx] is not None]
                return max(vs) if vs else None
            def _hsum(idx):
                vs = [r[idx] for r in hh if r[idx] is not None]
                return sum(vs) if vs else None
            gpu: Dict[str, Dict[str, float]] = {}
            for r in hh:
                if not r[11]:
                    continue
                try:
                    data = json.loads(r[11])
                except (ValueError, TypeError):
                    continue
                for idx, g in data.items():
                    slot = gpu.setdefault(idx, {})
                    for k in ("util", "mem", "temp", "power"):
                        if g.get(k) is not None:
                            slot.setdefault(k, []).append(g[k])
            gpu_agg = {}
            for idx, g in gpu.items():
                gpu_agg[idx] = {
                    "util_avg": sum(g["util"]) / len(g["util"]) if "util" in g else None,
                    "mem_max": max(g["mem"]) if "mem" in g else None,
                    "temp_max": max(g["temp"]) if "temp" in g else None,
                    "power_avg": sum(g["power"]) / len(g["power"]) if "power" in g else None,
                }
            self.write_host_1m([(host_id, t0, _havg(1), _hmax(2), _hmax(4),
                                 _hsum(5), _hsum(6), _havg(7),
                                 json.dumps(gpu_agg) if gpu_agg else None)])

    def cleanup(self, raw_cutoff: int, agg_cutoff: int, events_cutoff: int) -> None:
        """分批删除超期数据（每批 1 万行，避免长事务）。"""
        with self._lock:
            for table in ("ts_llama", "ts_host"):
                while True:
                    cur = self._conn.execute(
                        "DELETE FROM %s WHERE rowid IN "
                        "(SELECT rowid FROM %s WHERE ts < ? LIMIT 10000)" % (table, table),
                        (raw_cutoff,))
                    if cur.rowcount == 0:
                        break
            for table in ("ts_llama_1m", "ts_host_1m"):
                while True:
                    cur = self._conn.execute(
                        "DELETE FROM %s WHERE rowid IN "
                        "(SELECT rowid FROM %s WHERE ts < ? LIMIT 10000)" % (table, table),
                        (agg_cutoff,))
                    if cur.rowcount == 0:
                        break
            while True:
                cur = self._conn.execute(
                    "DELETE FROM events WHERE rowid IN "
                    "(SELECT rowid FROM events WHERE ts < ? LIMIT 10000)",
                    (events_cutoff,))
                if cur.rowcount == 0:
                    break
            self._conn.commit()


class HistoryWriter:
    """批量写入器：内存按 (host, ts) 聚合序列，后台线程周期刷盘。"""

    def __init__(self, store: HistoryStore, flush_interval: float = 5.0,
                 raw_days: int = 7, agg_days: int = 90, events_days: int = 30):
        self._store = store
        self._flush_interval = max(1.0, float(flush_interval))
        self._raw_cutoff = int(time.time()) - raw_days * 86400
        self._agg_cutoff = int(time.time()) - agg_days * 86400
        self._events_cutoff = int(time.time()) - events_days * 86400
        self._llama: Dict[Tuple[str, int], Dict[str, Optional[float]]] = {}
        self._host: Dict[Tuple[str, int], Dict[str, Optional[float]]] = {}
        self._events: List[Tuple] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_agg: Dict[str, int] = {}
        self._last_cleanup = 0.0
        self._thread = threading.Thread(target=self._run, name="history-writer", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # 入队（事件循环线程调用，必须快）
    # ------------------------------------------------------------------
    def enqueue_llama(self, host_id: str, ts: float, name: str, value) -> None:
        with self._lock:
            self._llama.setdefault((host_id, int(ts)), {})[name] = value

    def enqueue_host(self, host_id: str, ts: float, name: str, value) -> None:
        with self._lock:
            self._host.setdefault((host_id, int(ts)), {})[name] = value

    def enqueue_event(self, host_id: str, ev: Dict[str, Any]) -> None:
        with self._lock:
            self._events.append((host_id, ev.get("ts") or time.time(),
                                 ev.get("level", "info"), ev.get("type", ""),
                                 ev.get("msg", "")))

    # ------------------------------------------------------------------
    # 后台刷盘
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            try:
                self._flush()
            except Exception:
                log.exception("历史数据刷盘失败")

    def _flush(self) -> None:
        with self._lock:
            llama, host, events = self._llama, self._host, self._events
            self._llama, self._host, self._events = {}, {}, []
        if llama:
            rows = [(h, ts, d.get("gen_speed"), d.get("prompt_speed"),
                     d.get("ctx_used"), d.get("mtp_acceptance"))
                    for (h, ts), d in llama.items()]
            self._store.write_llama(rows)
        if host:
            rows = []
            for (h, ts), d in host.items():
                gpu: Dict[str, Dict[str, float]] = {}
                vals = [None] * 10
                for i, name in enumerate(HOST_SERIES):
                    vals[i] = d.get(name)
                for name, v in d.items():
                    parsed = _gpu_name(name)
                    if parsed:
                        idx, kind = parsed
                        gpu.setdefault(idx, {})[kind] = v
                rows.append((h, ts, *vals, json.dumps(gpu) if gpu else None))
            self._store.write_host(rows)
        for host_id, ts, level, type_, msg in events:
            try:
                self._store.write_event(host_id, ts, level, type_, msg)
            except sqlite3.Error:
                log.exception("事件落库失败: %s", host_id)
        # 用刚刷盘批次的主机集合驱动聚合（swap 后的新缓冲通常为空，不能作依据）
        flushed_hosts = {h for h, _ in llama.keys()} | {h for h, _ in host.keys()}
        self._aggregate_due(flushed_hosts)
        self._cleanup_due()

    def _aggregate_due(self, hosts) -> None:
        """对已完整结束且未聚合的分钟做 1m 聚合。"""
        now = int(time.time())
        prev_minute = (now // 60) * 60 - 60
        with self._lock:
            last = dict(self._last_agg)
        for h in hosts:
            done = last.get(h, 0)
            m = done + 60 if done else prev_minute - 59 * 60
            # 从上次聚合的下一分钟开始，最多补 10 分钟（防长时间卡死后爆量）
            for _ in range(10):
                if m > prev_minute:
                    break
                try:
                    self._store.aggregate_minute(h, m)
                except sqlite3.Error:
                    log.exception("1m 聚合失败: %s @%d", h, m)
                    break
                m += 60
            if m > (done + 60 if done else prev_minute - 59 * 60):
                with self._lock:
                    self._last_agg[h] = m - 60

    def _cleanup_due(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 3600:
            return
        self._last_cleanup = now
        try:
            self._store.cleanup(self._raw_cutoff, self._agg_cutoff, self._events_cutoff)
            log.info("历史数据保留期清理完成（原始 %ds 前 / 聚合 %ds 前）",
                     self._raw_cutoff, self._agg_cutoff)
        except sqlite3.Error:
            log.exception("历史数据清理失败")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        try:
            self._flush()
        except Exception:
            log.exception("关闭前最终刷盘失败")
