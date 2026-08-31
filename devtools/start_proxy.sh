#!/usr/bin/env bash
# Codex CLI ↔ llama-server 规范化代理：启动 / 停止 / 状态
# 用法:
#   ./devtools/start_proxy.sh          # 启动（幂等：已在运行则直接返回）
#   ./devtools/start_proxy.sh status   # 查看状态
#   ./devtools/start_proxy.sh stop     # 停止
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LISTEN="${PROXY_LISTEN:-127.0.0.1:8901}"
UPSTREAM="${PROXY_UPSTREAM:-http://ai.lan:8080}"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/codex_llama_proxy.log"
PID_FILE="$LOG_DIR/codex_llama_proxy.pid"
HEALTH_URL="http://$LISTEN/v1/models"

is_running() {
  curl -sf -m 5 "$HEALTH_URL" > /dev/null 2>&1
}

start() {
  if is_running; then
    echo "proxy already running on $LISTEN (upstream $UPSTREAM)"
    return 0
  fi
  # 清理残留进程（端口被占但健康检查失败的僵尸）
  pkill -f "codex_llama_proxy.py" 2>/dev/null && sleep 1
  mkdir -p "$LOG_DIR"
  setsid nohup python3 "$ROOT/devtools/codex_llama_proxy.py" \
    --listen "$LISTEN" --upstream "$UPSTREAM" \
    >> "$LOG_FILE" 2>&1 < /dev/null &
  echo $! > "$PID_FILE"
  for _ in $(seq 1 20); do
    if is_running; then
      echo "proxy started: $LISTEN -> $UPSTREAM (pid $(cat "$PID_FILE"), log $LOG_FILE)"
      return 0
    fi
    sleep 0.5
  done
  echo "ERROR: proxy did not become healthy; last log lines:" >&2
  tail -n 20 "$LOG_FILE" >&2
  return 1
}

stop() {
  if is_running; then
    pkill -f "codex_llama_proxy.py"
    sleep 1
  fi
  if is_running; then
    echo "ERROR: proxy still running" >&2
    return 1
  fi
  rm -f "$PID_FILE"
  echo "proxy stopped"
}

status() {
  if is_running; then
    echo "running: $LISTEN -> $UPSTREAM"
    curl -s -m 5 "$HEALTH_URL" | head -c 200; echo
  else
    echo "not running (health check failed: $HEALTH_URL)"
    return 1
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
