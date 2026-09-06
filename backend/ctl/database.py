import sqlite3
import threading

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  alias         TEXT    NOT NULL DEFAULT '',
  host          TEXT    NOT NULL,
  port          INTEGER NOT NULL DEFAULT 22,
  username      TEXT    NOT NULL,
  encrypted_pwd TEXT,
  auth_type     TEXT    NOT NULL DEFAULT 'password',
  key_passphrase_enc TEXT,
  browse_paths  TEXT    NOT NULL DEFAULT '',
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


def init_db() -> None:
    conn = get_conn()
    with _lock:
        conn.executescript(SCHEMA)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(hosts)").fetchall()]
        if "browse_paths" not in cols:
            conn.execute("ALTER TABLE hosts ADD COLUMN browse_paths TEXT NOT NULL DEFAULT ''")
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
