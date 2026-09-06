"""llama灵境 FastAPI 入口。

- 单进程 :8000（env PORT 可配置）；lifespan 启动/停止 MonitorRegistry。
- 托管 frontend/dist（SPA fallback → index.html）。
- 日志：stdout + logs/llamalens.log（INFO）。
- 兼容 Python 3.9。
"""
import asyncio
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
from .ctl.hostsync import import_from_yaml_if_empty, load_hosts_from_db
from .ctl.main import ctl_routers, init_ctl, shutdown_ctl
from .history import HistoryStore, HistoryWriter
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
        log.warning("config/hosts.yaml 不存在，使用默认全局配置（主机列表以数据库为准）")
        from .config import AppConfig, GlobalConfig
        app_cfg = AppConfig(global_cfg=GlobalConfig(), hosts=[])

    # 主机统一存数据库：初始化 → 首次从 hosts.yaml 导入 → 从 DB 加载
    init_ctl()
    imported = import_from_yaml_if_empty(app_cfg)
    if imported:
        log.info("已从 config/hosts.yaml 导入 %d 台主机（主机管理已迁移到界面，yaml 仅保留全局配置）", imported)
    app_cfg.hosts = load_hosts_from_db(app_cfg.global_cfg)

    # 历史持久化（SQLite 两级存储；enabled=false 时退回纯内存）
    history_store = None
    history_writer = None
    if app_cfg.global_cfg.history_enabled:
        db_path = app_cfg.global_cfg.history_db_path
        if not os.path.isabs(db_path):
            db_path = os.path.join(base_dir, db_path)
        try:
            history_store = HistoryStore(db_path)
            history_writer = HistoryWriter(
                history_store,
                flush_interval=app_cfg.global_cfg.history_flush_interval,
                raw_days=app_cfg.global_cfg.history_raw_days,
                agg_days=app_cfg.global_cfg.history_agg_days,
                events_days=app_cfg.global_cfg.history_events_days,
            )
            log.info("历史存储已启用: %s（原始 %dd / 聚合 %dd / 事件 %dd）",
                     db_path, app_cfg.global_cfg.history_raw_days,
                     app_cfg.global_cfg.history_agg_days,
                     app_cfg.global_cfg.history_events_days)
        except Exception:
            log.exception("历史存储初始化失败，退回纯内存模式")
            history_store, history_writer = None, None

    registry = MonitorRegistry(app_cfg, history_writer)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.loop = asyncio.get_running_loop()
        log.info("llama灵境 启动：端口 %d，主机 %s", app_cfg.port,
                 [h.id for h in app_cfg.hosts] or "(无)")
        await registry.start()
        try:
            yield
        finally:
            await registry.stop()
            if history_writer is not None:
                history_writer.close()
            if history_store is not None:
                history_store.close()
            shutdown_ctl()
            log.info("llama灵境 已停止")

    app = FastAPI(title="llama灵境", lifespan=lifespan)
    app.state.registry = registry
    app.state.history = history_store
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
