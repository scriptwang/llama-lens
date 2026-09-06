#!/usr/bin/env bash
# llama灵境 启动脚本：构建前端（若缺失）+ 启动 FastAPI 单进程
# v2.0：主机统一存数据库（data/llama_ctl.db），首次启动自动从 config/hosts.yaml 导入（若存在）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f config/hosts.yaml ]; then
  echo "提示：config/hosts.yaml 不存在（可选）。"
  echo "      主机请在界面【主机管理】中添加；老用户可 cp config/hosts.example.yaml config/hosts.yaml 自动导入。"
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "frontend/dist 未构建，开始构建前端（首次较慢）..."
  (cd frontend && npm install && npm run build)
fi

# --timeout-graceful-shutdown：存在半死 WS 客户端时，优雅关闭最多等 10s 后强制结束，避免进程卡在关闭阶段
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}" --timeout-graceful-shutdown 10
