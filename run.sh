#!/usr/bin/env bash
# llama灵境 启动脚本：构建前端（若缺失）+ 启动 FastAPI 单进程
# v2.0：主机统一存数据库（data/llama_ctl.db），首次启动自动从 config/config.yaml 导入（若存在 hosts 段）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f config/config.yaml ]; then
  echo "提示：config/config.yaml 不存在（可选，仅全局配置）。"
  echo "      主机请在界面【主机管理】中添加；可 cp config/config.example.yaml config/config.yaml。"
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "frontend/dist 未构建，开始构建前端（首次较慢）..."
  (cd frontend && npm install && npm run build)
fi

# 端口：环境变量 PORT > config/config.yaml server.port > 8000
PORT="$(python3 -c 'from backend.config import resolve_port; print(resolve_port())')"
# --timeout-graceful-shutdown：存在半死 WS 客户端时，优雅关闭最多等 10s 后强制结束，避免进程卡在关闭阶段
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" --timeout-graceful-shutdown 10
