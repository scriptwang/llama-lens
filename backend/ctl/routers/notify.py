"""告警推送：测试发送接口。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..errors import ApiError, VALIDATION_FAILED, ok
from .auth import get_current_user
from .hosts import get_host_row
from ...notify import CHANNELS, send_async

router = APIRouter(prefix="/api/hosts", tags=["notify"])


class NotifyTestReq(BaseModel):
    """可选：用表单里尚未保存的配置测试。"""
    notify_type: Optional[str] = None
    notify_url: Optional[str] = None


@router.post("/{host_id}/notify-test")
async def notify_test(host_id: int, req: NotifyTestReq = NotifyTestReq(),
                      user: str = Depends(get_current_user)):
    """向该主机配置的 webhook 发送一条测试消息（body 可覆盖为未保存的配置）。"""
    row = get_host_row(host_id)
    ntype = req.notify_type or row["notify_type"] or "wecom"
    nurl = (req.notify_url or row["notify_url"] or "").strip()
    if not nurl:
        raise ApiError(VALIDATION_FAILED, "请先填写 webhook 地址")
    if ntype not in CHANNELS:
        raise ApiError(VALIDATION_FAILED, "notify_type 必须是 %s 之一" % "/".join(CHANNELS))
    name = row["alias"] or row["host"]
    await send_async(ntype, nurl, name, "info",
                     "测试消息", "LLMLens 告警推送配置成功（这是一条测试消息）")
    return ok({"sent": True, "channel": ntype})
