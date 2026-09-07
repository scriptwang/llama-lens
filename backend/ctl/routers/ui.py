"""UI 功能开关：config.yaml ui 段（TAB 显隐 + 自动浏览默认开关），只读下发给前端。"""
from fastapi import APIRouter, Depends

from ..config import settings
from ..errors import ok
from .auth import get_current_user

router = APIRouter(prefix="/api", tags=["ui"])


@router.get("/ui")
def get_ui_config(user: str = Depends(get_current_user)):
    return ok(settings.ui)
