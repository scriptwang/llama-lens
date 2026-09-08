"""测试聊天记录持久化（业务库 llama_ctl.db 的 chat_sessions 表）。

按 (host_id=mid, user) 维度存会话；messages 为 JSON 数组（含 content/reasoning/
images/metrics/error）。前端从 localStorage 迁移到本接口，跨设备 / 清缓存后仍可恢复。
"""
import json
import time
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import database as db
from ..errors import ApiError, ok
from .auth import get_current_user

router = APIRouter(prefix="/api/hosts", tags=["chat"])

MAX_SESSIONS = 30  # 每 (host, user) 会话数上限，超出丢弃最旧


class ChatSessionReq(BaseModel):
    title: str = "新会话"
    messages: List[dict] = []


@router.get("/{host_id}/chat/sessions")
def list_sessions(host_id: str, user: str = Depends(get_current_user)):
    rows = db.query(
        "SELECT id, title, created_at, updated_at FROM chat_sessions "
        "WHERE host_id=? AND user=? ORDER BY updated_at DESC LIMIT 50",
        (host_id, user),
    )
    return ok([dict(r) for r in rows])


@router.get("/{host_id}/chat/sessions/{session_id}")
def get_session(host_id: str, session_id: str, user: str = Depends(get_current_user)):
    row = db.query_one(
        "SELECT * FROM chat_sessions WHERE id=? AND host_id=? AND user=?",
        (session_id, host_id, user),
    )
    if row is None:
        raise ApiError(4004, "会话不存在或已删除")
    d = dict(row)
    try:
        d["messages"] = json.loads(d.pop("messages") or "[]")
    except (ValueError, TypeError):
        d["messages"] = []
    return ok(d)


@router.put("/{host_id}/chat/sessions/{session_id}")
def save_session(host_id: str, session_id: str, req: ChatSessionReq,
                 user: str = Depends(get_current_user)):
    now = int(time.time() * 1000)
    title = (req.title or "新会话")[:64]
    msgs = json.dumps(req.messages or [], ensure_ascii=False)
    db.execute(
        "INSERT INTO chat_sessions (id, host_id, user, title, created_at, updated_at, messages) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at, "
        "messages=excluded.messages",
        (session_id, host_id, user, title, now, now, msgs),
    )
    # 会话数上限：丢弃最旧（保留当前会话）
    rows = db.query(
        "SELECT id FROM chat_sessions WHERE host_id=? AND user=? AND id<>? "
        "ORDER BY updated_at ASC",
        (host_id, user, session_id),
    )
    excess = len(rows) - (MAX_SESSIONS - 1)
    for r in rows[:max(0, excess)]:
        db.execute(
            "DELETE FROM chat_sessions WHERE id=? AND host_id=? AND user=?",
            (r["id"], host_id, user),
        )
    return ok({"id": session_id})


@router.delete("/{host_id}/chat/sessions/{session_id}")
def delete_session(host_id: str, session_id: str, user: str = Depends(get_current_user)):
    db.execute(
        "DELETE FROM chat_sessions WHERE id=? AND host_id=? AND user=?",
        (session_id, host_id, user),
    )
    return ok({"deleted": session_id})
