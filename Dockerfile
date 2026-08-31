# LlamaLens 多阶段构建：node 构建前端 → python slim 运行后端（FastAPI 托管前端产物）
# 构建：docker build -t llamalens:latest .
# 运行：docker compose up -d --build（或见 README「Docker 部署」）

# ---- 阶段 1：前端构建（Vue 3 + Vite 5，需 Node 18+）----
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 阶段 2：后端运行时（代码兼容 Python 3.9+，镜像用 3.11 slim）----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
# devtools/codex_llama_proxy.py 为纯标准库脚本；.env 启用 LLAMALENS_CODEX_PROXY 时由后端守护拉起
COPY devtools/ ./devtools/
COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir -p config logs
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4)" || exit 1
# shell 形式以支持 ${PORT} 展开（与 run.sh 行为一致）
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
