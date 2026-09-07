import time

from fastapi import APIRouter, Depends, Request

from .. import database as db
from ..config import settings
from ..errors import ApiError, JWT_INVALID, LOGIN_FAILED, ok
from ..schemas import LoginReq
from ..security import create_token, decode_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 登录限流：同一 (IP, 用户名) 15 分钟内最多 5 次尝试（防暴力破解）
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW_SEC = 15 * 60
_login_attempts = {}


def _login_rate_limited(ip: str, username: str) -> bool:
    key = (ip or "?", (username or "").lower())
    now = time.time()
    if len(_login_attempts) > 1000:
        # 只清理已过期条目：整体 clear() 可被攻击者用大量不同 (IP,用户名) 触发，
        # 从而冲掉正在生效的限流
        expired = [k for k, (_, start) in _login_attempts.items()
                   if now - start > _LOGIN_WINDOW_SEC]
        for k in expired:
            del _login_attempts[k]
        if len(_login_attempts) > 1000:
            _login_attempts.clear()  # 兜底（全部条目都在窗口内时）
    count, start = _login_attempts.get(key, (0, now))
    if now - start > _LOGIN_WINDOW_SEC:
        count, start = 0, now
    count += 1
    _login_attempts[key] = (count, start)
    return count > _MAX_LOGIN_ATTEMPTS


def get_current_user(request: Request) -> str:
    if not settings.auth_enabled:
        return "local"
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(JWT_INVALID, "未登录或登录已过期")
    try:
        payload = decode_token(auth[7:])
        return payload.get("sub", "unknown")
    except Exception:
        raise ApiError(JWT_INVALID, "未登录或登录已过期")


@router.get("/config")
def auth_config():
    return ok({"auth_enabled": settings.auth_enabled})


@router.post("/login")
def login(req: LoginReq, request: Request):
    ip = request.client.host if request.client else ""
    if _login_rate_limited(ip, req.username):
        raise ApiError(LOGIN_FAILED, "尝试次数过多，请 15 分钟后再试")
    if not settings.auth_enabled:
        return ok({
            "token": create_token("local"),
            "expires_in": settings.jwt_expire_hours * 3600,
            "auth_enabled": False,
        })
    row = db.query_one("SELECT * FROM users WHERE username = ?", (req.username,))
    if row is None or not verify_password(req.password, row["password_hash"]):
        raise ApiError(LOGIN_FAILED, "账号或密码错误")
    return ok({
        "token": create_token(row["username"]),
        "expires_in": settings.jwt_expire_hours * 3600,
        "auth_enabled": True,
    })


@router.get("/me")
def me(user: str = Depends(get_current_user)):
    return ok({"username": user})
