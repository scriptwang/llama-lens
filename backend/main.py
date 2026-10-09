"""llama灵境 FastAPI 入口。

- 单进程 :8000（env PORT 可配置）；lifespan 启动/停止 MonitorRegistry。
- 托管 frontend/dist（SPA fallback → index.html）。
- 日志：stdout + logs/llamalens.log（INFO）。
- 兼容 Python 3.9。
"""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import sys
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .api import router as api_router
from .efficiency import router as efficiency_router
from .playground import router as playground_router
from .config import AppConfig, GlobalConfig, load_config
from .ctl.errors import ApiError, api_error_handler
from .ctl.hostsync import import_from_yaml_if_empty, load_hosts_from_db
from .ctl.main import ctl_routers, init_ctl, shutdown_ctl
from .history import HistoryStore, HistoryWriter
from .monitor import MonitorRegistry
from .ws import router as ws_router
from .gateway import data_router as gateway_data_router, admin_router as gateway_admin_router

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
        # 轮转：单文件 10MB × 4 份（含当前），防止长期运行日志无限增长
        fh = RotatingFileHandler(os.path.join(log_dir, "llamalens.log"), encoding="utf-8",
                                 maxBytes=10 * 1024 * 1024, backupCount=3)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx 每次请求打一行 INFO（每秒轮询），是日志量与终端缓冲压力的主要来源，降为 WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(base_dir: Optional[str] = None) -> FastAPI:
    """创建 FastAPI 应用（轻量：不加载配置 / 不初始化 DB / 不启动监控）。

    重初始化（配置加载、主机导入、历史存储、监控注册）全部延迟到 lifespan，
    import 本模块无副作用，便于单元测试与多实例创建。
    """
    base_dir = base_dir or BASE_DIR
    setup_logging(base_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.loop = asyncio.get_running_loop()

        # 配置加载（config.yaml 缺失时用默认全局配置；主机列表以数据库为准）
        try:
            app_cfg = load_config(base_dir)
        except FileNotFoundError:
            log.warning("config/config.yaml 不存在，使用默认全局配置（主机列表以数据库为准）")
            app_cfg = AppConfig(global_cfg=GlobalConfig(), hosts=[])

        # 主机统一存数据库：初始化 → 首次从配置文件导入（可选）→ 从 DB 加载
        init_ctl()
        imported = import_from_yaml_if_empty(app_cfg)
        if imported:
            log.info("已从配置文件一次性导入 %d 台主机（主机管理在界面维护）", imported)
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
        app.state.registry = registry
        app.state.history = history_store

        # 统一网关（透明入口 + 模型亲和路由 + 治理观测，详见 docs/07）
        gateway_state = None
        if app_cfg.gateway.enabled:
            from .gateway.store import GatewayStore, GatewayWriter
            gw_db = app_cfg.gateway.db_path
            if not os.path.isabs(gw_db):
                gw_db = os.path.join(base_dir, gw_db)
            try:
                gw_store = GatewayStore(gw_db)
                gw_writer = GatewayWriter(gw_store, flush_interval=2.0,
                                          audit_days=app_cfg.gateway.audit_retention_days,
                                          trace_days=app_cfg.gateway.trace_retention_days)
                from .ctl import database as ctl_db
                excluded = {r["mid"] for r in
                            ctl_db.query("SELECT mid FROM hosts WHERE gateway_excluded = 1 AND mid != ''")}
                # 路由策略：config.yaml 为底，gateway.db 里的动态开关优先（重启保留）
                strategy = {
                    "affinity": app_cfg.gateway.affinity,
                    "same_model_lb": app_cfg.gateway.same_model_lb,
                    "cross_model_fallback": app_cfg.gateway.cross_model_fallback,
                    "fallback_rules": app_cfg.gateway.fallback_rules,
                }
                db_strategy = gw_store.get_kv("strategy")
                if isinstance(db_strategy, dict):
                    strategy.update(db_strategy)
                # 模型单价：config.yaml 为底，gateway.db 里的 UI 配置优先（重启保留）
                model_prices = dict(app_cfg.gateway.model_prices or {})
                db_prices = gw_store.get_kv("model_prices")
                if isinstance(db_prices, dict):
                    model_prices = db_prices
                gateway_state = {
                    "enabled": True,
                    "store": gw_store,
                    "writer": gw_writer,
                    "registry": registry,
                    "default_host": app_cfg.gateway.default_host,
                    "excluded": excluded,
                    "strategy": strategy,
                    "model_prices": model_prices,
                }
                log.info("统一网关已启用: %s (default_host=%s)",
                         gw_db, app_cfg.gateway.default_host or "(无)")
            except Exception:
                log.exception("网关初始化失败，网关功能不可用")
        app.state.gateway = gateway_state

        # 启动时从历史 DB 回填环形缓冲：容器重启后内存缓冲为空，短窗口（≤1h）
        # 否则只能看到重启后的数据。回填最近 1h，消除缺口（一次性、启动前完成）。
        if history_store is not None:
            for m in registry.monitors.values():
                m.backfill_from_store(history_store)

        log.info("LLMLens 启动：端口 %d，主机 %s", app_cfg.port,
                 [h.id for h in app_cfg.hosts] or "(无)")
        await registry.start()
        try:
            yield
        finally:
            await registry.stop()
            if gateway_state is not None:
                gateway_state["writer"].close()
                gateway_state["store"].close()
                log.info("统一网关已停止")
            if history_writer is not None:
                history_writer.close()
            if history_store is not None:
                history_store.close()
            shutdown_ctl()
            log.info("LLMLens 已停止")

    app = FastAPI(title="LLMLens", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(efficiency_router)
    app.include_router(playground_router)
    app.include_router(ws_router)
    app.include_router(gateway_data_router)
    app.include_router(gateway_admin_router)
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
                headers = None
                if full_path.startswith("assets/"):
                    # 带 hash 的构建产物可长期缓存
                    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
                return FileResponse(candidate, headers=headers)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            # index.html 禁止缓存：否则新部署后浏览器仍引用旧 JS（改完不生效）
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            "前端尚未构建：cd frontend && npm install && npm run build（或运行 ./run.sh）")

    return app


app = create_app()
