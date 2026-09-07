import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from . import database as db
from .config import settings
from .errors import ApiError, api_error_handler, ok, unhandled_error_handler
from .routers import (auth, config, diagnostic, hosts, logs, metrics, models, notify,
                          params, rules, services)
from .routers.rules import DEFAULT_RULES
from .security import hash_password
from .ssh_pool import pool

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def init_ctl() -> None:
    """初始化管理层（SQLite + 初始管理员 + 扫描规则）。

    由 LlamaLens 主入口 lifespan 调用；独立运行本模块时同样走这里。
    """
    db.init_db()
    if db.query_one("SELECT COUNT(*) AS c FROM users")["c"] == 0:
        pwd = settings.admin_password or secrets.token_urlsafe(8)
        db.execute("INSERT INTO users (username, password_hash) VALUES (?,?)", (settings.admin_user, hash_password(pwd)))
        print(f"\n[llamalens.ctl] 初始管理员账号: {settings.admin_user} / {pwd}")
        print("[llamalens.ctl] （初始密码仅打印一次；如需重置可删除 data/llama_ctl.db 后重启）\n")
    if db.query_one("SELECT 1 FROM settings WHERE key = 'scan_rules'") is None:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('scan_rules', ?)",
            (json.dumps(DEFAULT_RULES, ensure_ascii=False),),
        )


def shutdown_ctl() -> None:
    pool.shutdown()


# 供 LlamaLens 主入口挂载的路由（/api/health 由主入口提供，不重复挂载）
# models.svc_router 必须在 services.router 之前注册（静态 /switch-model 优先于通配 /{action}）
ctl_routers = (auth.router, hosts.router, models.router, models.svc_router, services.router,
               config.router, params.router, rules.router, logs.router, metrics.router,
               notify.router, diagnostic.router)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_ctl()
    yield
    shutdown_ctl()


app = FastAPI(title="LlamaLens Ctl API", version="1.1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

@app.get("/api/health")
def health():
    return ok({"status": "ok", "auth_enabled": settings.auth_enabled})


for r in ctl_routers:
    app.include_router(r)


@app.get("/api/{path:path}", include_in_schema=False)
def api_not_found(path: str):
    """未匹配的 /api/* 路径返回 404 JSON（避免被 SPA fallback 吞成 index.html）"""
    return JSONResponse(status_code=404, content={"code": 4004, "msg": f"接口不存在：/api/{path}", "data": None})


_dist = BASE_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")
