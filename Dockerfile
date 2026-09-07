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
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir -p config logs data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; from backend.config import resolve_port; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % resolve_port(), timeout=4)" || exit 1
# 端口：环境变量 PORT > config.yaml server.port > 8000（启动时解析）
# shell 形式 + exec 使 uvicorn 直接成为 PID 1 接收 SIGTERM（优雅关闭）
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port $(python -c 'from backend.config import resolve_port; print(resolve_port())') --timeout-graceful-shutdown 10"]
