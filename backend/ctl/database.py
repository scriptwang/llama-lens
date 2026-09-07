import sqlite3
import threading

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  mid           TEXT    NOT NULL DEFAULT '' UNIQUE,
  alias         TEXT    NOT NULL DEFAULT '',
  host          TEXT    NOT NULL,
  port          INTEGER NOT NULL DEFAULT 22,
  username      TEXT    NOT NULL,
  encrypted_pwd TEXT,
  auth_type     TEXT    NOT NULL DEFAULT 'password',
  key_passphrase_enc TEXT,
  key_path      TEXT,
  browse_paths  TEXT    NOT NULL DEFAULT '',
  monitor_enabled INTEGER NOT NULL DEFAULT 1,
  llama_host    TEXT    NOT NULL DEFAULT '',
  llama_port    INTEGER NOT NULL DEFAULT 8080,
  llama_interval REAL   NOT NULL DEFAULT 1.0,
  llama_slow_interval REAL NOT NULL DEFAULT 30.0,
  llama_timeout REAL    NOT NULL DEFAULT 3.0,
  ssh_interval  REAL    NOT NULL DEFAULT 2.0,
  ssh_keepalive INTEGER NOT NULL DEFAULT 15,
  ssh_timeout   REAL    NOT NULL DEFAULT 15.0,
  process_name  TEXT    NOT NULL DEFAULT 'llama-server',
  systemd_unit  TEXT    NOT NULL DEFAULT 'llama-server.service',
  log_source    TEXT    NOT NULL DEFAULT 'journal',
  log_unit      TEXT    NOT NULL DEFAULT 'llama-server',
  log_path      TEXT,
  log_follow    INTEGER NOT NULL DEFAULT 1,
  log_catchup_sec INTEGER NOT NULL DEFAULT 30,
  disk_mounts   TEXT    NOT NULL DEFAULT '["/"]',
  thresholds    TEXT,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  last_connected_at TEXT,
  UNIQUE (host, port, username)
);

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_logs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  host_id      INTEGER REFERENCES hosts(id) ON DELETE SET NULL,
  user         TEXT NOT NULL DEFAULT 'admin',
  action       TEXT NOT NULL,
  service_name TEXT,
  detail       TEXT,
  ip           TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_host_time ON session_logs (host_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id          TEXT PRIMARY KEY,
  host_id     TEXT NOT NULL,
  user        TEXT NOT NULL DEFAULT 'admin',
  title       TEXT NOT NULL DEFAULT '新会话',
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  messages    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_chat_host ON chat_sessions (host_id, user, updated_at);
"""

_conn = None
_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


# 旧库迁移：新增列（列名 -> DDL 片段）
_HOSTS_MIGRATIONS = {
    "mid": "ALTER TABLE hosts ADD COLUMN mid TEXT NOT NULL DEFAULT ''",
    "key_path": "ALTER TABLE hosts ADD COLUMN key_path TEXT",
    "monitor_enabled": "ALTER TABLE hosts ADD COLUMN monitor_enabled INTEGER NOT NULL DEFAULT 1",
    "llama_host": "ALTER TABLE hosts ADD COLUMN llama_host TEXT NOT NULL DEFAULT ''",
    "llama_port": "ALTER TABLE hosts ADD COLUMN llama_port INTEGER NOT NULL DEFAULT 8080",
    "llama_interval": "ALTER TABLE hosts ADD COLUMN llama_interval REAL NOT NULL DEFAULT 1.0",
    "llama_slow_interval": "ALTER TABLE hosts ADD COLUMN llama_slow_interval REAL NOT NULL DEFAULT 30.0",
    "llama_timeout": "ALTER TABLE hosts ADD COLUMN llama_timeout REAL NOT NULL DEFAULT 3.0",
    "ssh_interval": "ALTER TABLE hosts ADD COLUMN ssh_interval REAL NOT NULL DEFAULT 2.0",
    "ssh_keepalive": "ALTER TABLE hosts ADD COLUMN ssh_keepalive INTEGER NOT NULL DEFAULT 15",
    "ssh_timeout": "ALTER TABLE hosts ADD COLUMN ssh_timeout REAL NOT NULL DEFAULT 15.0",
    "process_name": "ALTER TABLE hosts ADD COLUMN process_name TEXT NOT NULL DEFAULT 'llama-server'",
    "systemd_unit": "ALTER TABLE hosts ADD COLUMN systemd_unit TEXT NOT NULL DEFAULT 'llama-server.service'",
    "log_source": "ALTER TABLE hosts ADD COLUMN log_source TEXT NOT NULL DEFAULT 'journal'",
    "log_unit": "ALTER TABLE hosts ADD COLUMN log_unit TEXT NOT NULL DEFAULT 'llama-server'",
    "log_path": "ALTER TABLE hosts ADD COLUMN log_path TEXT",
    "log_follow": "ALTER TABLE hosts ADD COLUMN log_follow INTEGER NOT NULL DEFAULT 1",
    "log_catchup_sec": "ALTER TABLE hosts ADD COLUMN log_catchup_sec INTEGER NOT NULL DEFAULT 30",
    "disk_mounts": "ALTER TABLE hosts ADD COLUMN disk_mounts TEXT NOT NULL DEFAULT '[''/'']'",
    "thresholds": "ALTER TABLE hosts ADD COLUMN thresholds TEXT",
    "notify_enabled": "ALTER TABLE hosts ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 0",
    "notify_type": "ALTER TABLE hosts ADD COLUMN notify_type TEXT NOT NULL DEFAULT 'wecom'",
    "notify_url": "ALTER TABLE hosts ADD COLUMN notify_url TEXT NOT NULL DEFAULT ''",
}


def init_db() -> None:
    conn = get_conn()
    with _lock:
        conn.executescript(SCHEMA)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(hosts)").fetchall()]
        for col, ddl in _HOSTS_MIGRATIONS.items():
            if col not in cols:
                conn.execute(ddl)
        # mid 唯一索引（旧数据 mid 为空时不冲突：SQLite 唯一索引允许多个 NULL/空串？
        # 空串会冲突，故先给旧行补唯一 mid，再建索引）
        empty = conn.execute("SELECT id FROM hosts WHERE mid IS NULL OR mid = ''").fetchall()
        for i, r in enumerate(empty):
            conn.execute("UPDATE hosts SET mid = ? WHERE id = ?", ("host-%d" % r["id"], r["id"]))
        idx = [r["name"] for r in conn.execute("PRAGMA index_list(hosts)").fetchall()]
        if "idx_hosts_mid" not in idx:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hosts_mid ON hosts(mid)")
        conn.commit()


def query(sql: str, params: tuple = ()):
    with _lock:
        return get_conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()):
    conn = get_conn()
    with _lock:
        cur = conn.execute(sql, params)
        conn.commit()
    return cur


def log_action(host_id, user: str, action: str, service_name: str = "", detail: str = "", ip: str = "") -> None:
    execute(
        "INSERT INTO session_logs (host_id, user, action, service_name, detail, ip) VALUES (?,?,?,?,?,?)",
        (host_id, user, action, service_name, detail, ip),
    )
