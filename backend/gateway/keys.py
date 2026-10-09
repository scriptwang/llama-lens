"""API key 管理：生成 / 校验 / 额度检查。

客户端（Codex 等）用 API key 访问网关数据面（/v1/*）；
面板 UI 用 JWT 访问控制面（/api/gateway/*）。
"""
import secrets
import uuid
from datetime import datetime, timezone

_PREFIX = "ll-"

# 配额用尽（区别于鉴权失败：429 vs 401，docs/07 §5.1）
ERR_QUOTA = "API key 额度已用尽"


def generate_key() -> str:
    return _PREFIX + secrets.token_urlsafe(24)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_key(store, name: str = "", quota_tokens: int = 0, quota_cost: float = 0.0,
               allowed_models: str = "", expires_at: str = ""):
    key_id = uuid.uuid4().hex[:12]
    row = (key_id, generate_key(), name or "", 1, int(quota_tokens or 0),
           float(quota_cost or 0.0), 0, 0.0, allowed_models or "",
           expires_at or None, _now_iso())
    store.upsert_key(row)
    return store.get_key(key_id)


def _expired(row: dict) -> bool:
    exp = row.get("expires_at")
    if not exp:
        return False
    try:
        ts = datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
        return datetime.now(timezone.utc).timestamp() > ts
    except Exception:
        return False


def quota_exceeded(row: dict) -> bool:
    qt = row.get("quota_tokens") or 0
    if qt > 0 and (row.get("used_tokens") or 0) >= qt:
        return True
    qc = row.get("quota_cost") or 0.0
    if qc > 0 and (row.get("used_cost") or 0.0) >= qc:
        return True
    return False


def _model_allowed(row: dict, model: str) -> bool:
    """allowed_models 白名单（逗号分隔，空=不限）。

    匹配完整名或斜杠后的模型名，支持部分匹配（如 qwen3.8 匹配 qwen3.8-27b-*.gguf）。"""
    allowed = (row.get("allowed_models") or "").strip()
    if not allowed or not model:
        return True
    names = {x.strip().lower() for x in allowed.split(",") if x.strip()}
    if not names:
        return True
    m = model.lower()
    base = m.rsplit("/", 1)[-1]
    for n in names:
        if m == n or base == n or n in base or base in n:
            return True
    return False


def validate(store, token: str, model: str = None):
    """校验 API key，返回 (row, error)。error 非空表示拒绝。"""
    if not token:
        return None, "缺少 API key"
    row = store.get_key_by_token(token)
    if row is None:
        return None, "无效的 API key"
    if not row.get("enabled", 1):
        return None, "API key 已禁用"
    if _expired(row):
        return None, "API key 已过期"
    if quota_exceeded(row):
        return None, ERR_QUOTA
    if model and not _model_allowed(row, model):
        return None, "API key 不允许访问模型 %s" % model
    return row, None
