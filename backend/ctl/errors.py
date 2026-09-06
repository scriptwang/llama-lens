import re

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("llama_ctl")

CODE_OK = 0
# SSH 类
SSH_TIMEOUT = 1001
SSH_AUTH_FAILED = 1002
SSH_UNREACHABLE = 1003
SSH_POOL_EXHAUSTED = 1004
SSH_CMD_FAILED = 1005
# 配置/文件类
FILE_NOT_FOUND = 2001
PARSE_FAILED = 2002
WRITE_FAILED = 2003
DAEMON_RELOAD_FAILED = 2004
BACKUP_FAILED = 2005
FILE_EXISTS = 2006
# 认证类
JWT_INVALID = 3001
LOGIN_FAILED = 3002
# 校验/内部
VALIDATION_FAILED = 4001
INTERNAL = 5000

UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9._@-]+\.service$")


class ApiError(Exception):
    def __init__(self, code: int, msg: str, data=None):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.data = data


def ok(data=None, msg: str = "ok"):
    return {"code": CODE_OK, "msg": msg, "data": data}


def validate_unit_name(name: str) -> str:
    if not UNIT_NAME_RE.match(name or ""):
        raise ApiError(VALIDATION_FAILED, f"非法的服务单元名：{name}")
    return name


async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=200, content={"code": exc.code, "msg": exc.msg, "data": exc.data})


async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=200, content={"code": INTERNAL, "msg": "服务器内部错误，请稍后重试（详情见后端日志）", "data": None})
