"""网关数据持久化：SQLite（WAL）独立库 data/gateway.db。

- GatewayStore：SQLite 读写（api_keys + gateway_requests + gateway_spans）。
  单写者（GatewayWriter 线程）+ 读（API 线程，WAL 并发），与 HistoryStore 同模式。
- GatewayWriter：有界内存队列 + 后台线程批量刷盘，不阻塞事件循环。
  高频写（审计/链路/用量累计）走异步队列；管理写（key CRUD）同步。
- 独立库，不与 history.db 共库，避免两 writer 竞争同一写锁（详见 docs/07 §7.5）。
"""
import json
import logging
import os
import queue
import sqlite3
import threading
import time

log = logging.getLogger("llamalens.gateway.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
  id             TEXT PRIMARY KEY,
  key            TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL DEFAULT '',
  enabled        INTEGER NOT NULL DEFAULT 1,
  quota_tokens   INTEGER NOT NULL DEFAULT 0,
  quota_cost     REAL NOT NULL DEFAULT 0,
  used_tokens    INTEGER NOT NULL DEFAULT 0,
  used_cost      REAL NOT NULL DEFAULT 0,
  allowed_models TEXT NOT NULL DEFAULT '',
  expires_at     TEXT,
  created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gateway_requests (
  id                TEXT PRIMARY KEY,
  api_key_id        TEXT,
  ts                REAL NOT NULL,
  path              TEXT NOT NULL,
  req_model         TEXT NOT NULL DEFAULT '',
  target_host       TEXT NOT NULL DEFAULT '',
  served_by         TEXT NOT NULL DEFAULT '',
  degraded          INTEGER NOT NULL DEFAULT 0,
  status            INTEGER NOT NULL DEFAULT 0,
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  ttft_ms           REAL,
  total_ms          REAL,
  retries           INTEGER NOT NULL DEFAULT 0,
  cost              REAL NOT NULL DEFAULT 0,
  error             TEXT,
  req_preview       TEXT,
  resp_preview      TEXT
);
CREATE INDEX IF NOT EXISTS idx_gr_ts   ON gateway_requests(ts);
CREATE INDEX IF NOT EXISTS idx_gr_key  ON gateway_requests(api_key_id);
CREATE INDEX IF NOT EXISTS idx_gr_host ON gateway_requests(target_host);
CREATE TABLE IF NOT EXISTS gateway_spans (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  ts         REAL NOT NULL,
  name       TEXT NOT NULL,
  start_ms   REAL NOT NULL,
  end_ms     REAL NOT NULL,
  meta       TEXT
);
CREATE INDEX IF NOT EXISTS idx_gs_req ON gateway_spans(request_id);
CREATE INDEX IF NOT EXISTS idx_gs_ts  ON gateway_spans(ts);
CREATE TABLE IF NOT EXISTS gateway_kv (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class GatewayStore:
    """SQLite 网关存储（单写者：GatewayWriter 线程；读：API 线程，WAL 并发）。"""

    def __init__(self, db_path: str):
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        # 旧库迁移：补内容预览列
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(gateway_requests)")]
        for col in ("req_preview", "resp_preview"):
            if col not in cols:
                self._conn.execute("ALTER TABLE gateway_requests ADD COLUMN %s TEXT" % col)
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- KV 配置（路由策略等，DB 优先于 config.yaml）----
    def get_kv(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM gateway_kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_kv(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO gateway_kv (key, value) VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)))
            self._conn.commit()

    # ---- 写：管理（同步，低频）----
    def upsert_key(self, row: tuple) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO api_keys (id, key, name, enabled, quota_tokens, quota_cost,"
                " used_tokens, used_cost, allowed_models, expires_at, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            self._conn.commit()

    def delete_key(self, key_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            self._conn.commit()

    # ---- 写：高频（由 GatewayWriter 线程调用，持锁）----
    def write_request(self, row: tuple) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO gateway_requests (id, api_key_id, ts, path, req_model,"
                " target_host, served_by, degraded, status, prompt_tokens, completion_tokens,"
                " ttft_ms, total_ms, retries, cost, error, req_preview, resp_preview)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row)
            self._conn.commit()

    def write_spans(self, spans: list) -> None:
        if not spans:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO gateway_spans (request_id, ts, name, start_ms, end_ms, meta)"
                " VALUES (?,?,?,?,?,?)", spans)
            self._conn.commit()

    def add_usage(self, key_id: str, tokens: int, cost: float) -> None:
        """原子自增用量（并发安全，避免读-改-写竞态）。"""
        if not key_id:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE api_keys SET used_tokens = used_tokens + ?, used_cost = used_cost + ?"
                " WHERE id = ?", (int(tokens or 0), float(cost or 0.0), key_id))
            self._conn.commit()

    # ---- 读（API 线程，WAL 并发）----
    def get_key_by_token(self, token: str):
        with self._lock:
            row = self._conn.execute("SELECT * FROM api_keys WHERE key = ?", (token,)).fetchone()
        return dict(row) if row else None

    def get_key(self, key_id: str):
        with self._lock:
            row = self._conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        return dict(row) if row else None

    def list_keys(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def query_requests(self, limit: int = 100, api_key_id: str = None,
                       target_host: str = None, degraded: int = None,
                       model: str = None, status: int = None,
                       since_ts: float = None) -> list:
        sql = "SELECT * FROM gateway_requests WHERE 1=1"
        args = []
        if api_key_id:
            sql += " AND api_key_id = ?"
            args.append(api_key_id)
        if target_host:
            sql += " AND target_host = ?"
            args.append(target_host)
        if model:
            sql += " AND req_model LIKE ?"
            args.append("%" + model + "%")
        if status is not None:
            s = int(status)
            if s == 200:
                sql += " AND status >= 200 AND status < 300"
            elif s == 500:
                sql += " AND status >= 500"
            else:
                sql += " AND status = ?"
                args.append(s)
        if since_ts is not None:
            sql += " AND ts >= ?"
            args.append(float(since_ts))
        if degraded is not None:
            sql += " AND degraded = ?"
            args.append(degraded)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get_request(self, request_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gateway_requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def query_spans(self, request_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM gateway_spans WHERE request_id = ? ORDER BY start_ms",
                (request_id,)).fetchall()
        return [dict(r) for r in rows]

    def query_stats(self, group_by: str = "key", since_ts: float = None,
                    api_key_id: str = None) -> list:
        """成本/用量聚合：按 key / model / day 分组（docs/07 §7.5 成本聚合）。"""
        if group_by == "key":
            gcol, glabel = "COALESCE(api_key_id, '')", "api_key_id"
        elif group_by == "model":
            gcol, glabel = "COALESCE(req_model, '')", "req_model"
        else:
            gcol, glabel = "CAST(ts / 86400 AS INTEGER)", "day"
        sql = (f"SELECT {gcol} AS g, COUNT(*) AS requests,"
               " SUM(prompt_tokens + completion_tokens) AS tokens,"
               " SUM(cost) AS cost,"
               " SUM(CASE WHEN degraded = 1 THEN 1 ELSE 0 END) AS degraded,"
               " SUM(CASE WHEN status >= 400 OR status = 0 THEN 1 ELSE 0 END) AS errors"
               " FROM gateway_requests")
        args = []
        if since_ts is not None:
            sql += " WHERE ts >= ?"
            args.append(float(since_ts))
        if api_key_id:
            sql += (" WHERE api_key_id = ?" if not since_ts else " AND api_key_id = ?")
            args.append(api_key_id)
        sql += f" GROUP BY {gcol} ORDER BY cost DESC, requests DESC LIMIT 200"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            item = {
                "group": r["g"],
                "requests": r["requests"] or 0,
                "tokens": int(r["tokens"] or 0),
                "cost": float(r["cost"] or 0),
                "degraded": r["degraded"] or 0,
                "errors": r["errors"] or 0,
            }
            if group_by == "day":
                item["day"] = time.strftime("%Y-%m-%d", time.gmtime(r["g"] * 86400))
            out.append(item)
        return out

    def cleanup(self, req_cutoff: float, span_cutoff: float) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM gateway_requests WHERE ts < ?", (req_cutoff,))
            self._conn.execute("DELETE FROM gateway_spans WHERE ts < ?", (span_cutoff,))
            self._conn.commit()
            # SQLite 删除后文件不自动缩小：库较大时压缩（失败不影响主链路）
            try:
                if os.path.getsize(self._db_path) > 50 * 1024 * 1024:
                    self._conn.execute("VACUUM")
            except Exception:
                pass

    def db_size_mb(self) -> float:
        try:
            return round(os.path.getsize(self._db_path) / 1048576.0, 1)
        except OSError:
            return 0.0


class GatewayWriter:
    """有界内存队列 + 后台线程批量刷盘（复用 HistoryWriter 模式），不阻塞事件循环。"""

    def __init__(self, store: GatewayStore, flush_interval: float = 2.0, maxsize: int = 20000,
                 audit_days: int = 30, trace_days: int = 7):
        self._store = store
        self._flush_interval = flush_interval
        self._queue: "queue.Queue" = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._audit_days = int(audit_days or 0)
        self._trace_days = int(trace_days or 0)
        self._last_cleanup = 0.0
        self._thread = threading.Thread(target=self._run, name="gateway-writer", daemon=True)
        self._thread.start()

    def enqueue_request(self, row: tuple) -> None:
        self._put(("request", row))

    def enqueue_spans(self, spans: list) -> None:
        if spans:
            self._put(("spans", spans))

    def enqueue_usage(self, key_id: str, tokens: int, cost: float) -> None:
        self._put(("usage", (key_id, tokens, cost)))

    def _put(self, item) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            log.warning("gateway writer 队列已满，丢弃一条记录")

    def _maybe_cleanup(self) -> None:
        """保留期清理：启动后首次 + 每 24h（docs/07 §7.4/§12）。"""
        now = time.time()
        if now - self._last_cleanup < 86400:
            return
        self._last_cleanup = now
        if self._audit_days > 0 or self._trace_days > 0:
            try:
                self._store.cleanup(
                    now - self._audit_days * 86400 if self._audit_days > 0 else 0,
                    now - self._trace_days * 86400 if self._trace_days > 0 else 0)
                log.info("网关保留期清理完成（审计 %dd / 链路 %dd）",
                         self._audit_days, self._trace_days)
            except Exception:
                log.exception("网关保留期清理失败")

    def _run(self) -> None:
        self._maybe_cleanup()
        while not self._stop.is_set():
            try:
                first = self._queue.get(timeout=self._flush_interval)
            except queue.Empty:
                self._maybe_cleanup()
                continue
            batch = [first]
            while len(batch) < 1000:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._flush(batch)

    def _flush(self, batch: list) -> None:
        for kind, payload in batch:
            try:
                if kind == "request":
                    self._store.write_request(payload)
                elif kind == "spans":
                    self._store.write_spans(payload)
                elif kind == "usage":
                    key_id, tokens, cost = payload
                    self._store.add_usage(key_id, tokens, cost)
            except Exception:
                log.exception("gateway writer 刷盘失败")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
