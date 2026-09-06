from fastapi import APIRouter, Depends

from .. import database as db
from ..errors import ok
from .auth import get_current_user

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def get_logs(host_id: int = None, limit: int = 200, user: str = Depends(get_current_user)):
    limit = max(1, min(int(limit or 200), 1000))
    if host_id:
        rows = db.query(
            "SELECT * FROM session_logs WHERE host_id = ? ORDER BY id DESC LIMIT ?",
            (host_id, limit),
        )
    else:
        rows = db.query("SELECT * FROM session_logs ORDER BY id DESC LIMIT ?", (limit,))
    return ok([dict(r) for r in rows])
