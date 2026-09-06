"""llama灵境 FastAPI 入口。

- 单进程 :8000（env PORT 可配置）；lifespan 启动/停止 MonitorRegistry。
- 托管 frontend/dist（SPA fallback → index.html）。
- 日志：stdout + logs/llamalens.log（INFO）。
- 兼容 Python 3.9。
"""
import logging
import os
import queue
import sys
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .api import router as api_router
from .config import load_config
from .ctl.errors import ApiError, api_error_handler
from .ctl.main import ctl_routers, init_ctl, shutdown_ctl
from .monitor import MonitorRegistry
from .ws import router as ws_router

log = logging.getLogger("llamalens.main")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



class _AsyncStreamHandler(logging.Handler):
    """控制台日志处理器：后台线程写 stderr，有界队列，满时丢弃最旧记录。

    事件循环线程绝不能阻塞在控制台输出上：当终端停止读取（如用户长时间
    离开、pty 缓冲区写满）时，同步 write() 会阻塞整个事件循环，导致所有
    HTTP/WS 请求挂起（页面刷新卡死）。
    """

    def __init__(self, stream, maxsize: int = 200):
        super().__init__()
        self._stream = stream
        self._queue: "queue.Queue" = queue.Queue(maxsize=maxsize)
        self._thread = threading.Thread(target=self._run, name="log-console", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            try:
                self._queue.get_nowait()  # 丢弃最旧，保留最新
                self._queue.put_nowait(record)
            except queue.Full:
                pass

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            try:
                self._stream.write(self.format(record) + "\n")
                self._stream.flush()
            except Exception:
                pass


def setup_logging(base_dir: str) -> None:
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        sh = _AsyncStreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        fh = logging.FileHandler(os.path.join(log_dir, "llamalens.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx 每次请求打一行 INFO（每秒轮询），是日志量与终端缓冲压力的主要来源，降为 WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(base_dir: Optional[str] = None) -> FastAPI:
    base_dir = base_dir or BASE_DIR
    setup_logging(base_dir)

    try:
        app_cfg = load_config(base_dir)
    except FileNotFoundError:
        log.warning("config/hosts.yaml 不存在，以空主机列表启动（cp config/hosts.example.yaml config/hosts.yaml）")
        from .config import AppConfig, GlobalConfig
        app_cfg = AppConfig(global_cfg=GlobalConfig(), hosts=[])

    registry = MonitorRegistry(app_cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("llama灵境 启动：端口 %d，主机 %s", app_cfg.port,
                 [h.id for h in app_cfg.hosts] or "(无)")
        init_ctl()
        await registry.start()
        try:
            yield
        finally:
            await registry.stop()
            shutdown_ctl()
            log.info("llama灵境 已停止")

    app = FastAPI(title="llama灵境", lifespan=lifespan)
    app.state.registry = registry
    app.include_router(api_router)
    app.include_router(ws_router)
    for r in ctl_routers:
        app.include_router(r)
    app.add_exception_handler(ApiError, api_error_handler)

    dist = os.path.join(base_dir, "frontend", "dist")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "ws/")) or full_path in ("api", "ws"):
            raise HTTPException(status_code=404)
        if full_path:
            dist_real = os.path.realpath(dist)
            candidate = os.path.realpath(os.path.join(dist, full_path))
            # 防路径穿越：只允许服务 dist 目录内的文件
            if candidate.startswith(dist_real + os.sep) and os.path.isfile(candidate):
                return FileResponse(candidate)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            "前端尚未构建：cd frontend && npm install && npm run build（或运行 ./run.sh）")

    return app


app = create_app()
