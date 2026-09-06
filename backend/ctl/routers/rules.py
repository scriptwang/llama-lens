import json
import re

from fastapi import APIRouter, Depends

from .. import database as db
from ..config import BUILTIN_SCAN_RULES, YAML_SCAN_RULES
from ..errors import ApiError, VALIDATION_FAILED, ok
from .auth import get_current_user
from ..schemas import ScanRulesReq

router = APIRouter(prefix="/api/scan-rules", tags=["rules"])

# 首启写库的默认值（config.yaml 无 scan 段时生效）
DEFAULT_RULES = BUILTIN_SCAN_RULES

_RULE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def get_rules_value() -> dict:
    # 优先级：config.yaml scan 段 > 数据库（UI 保存）> 内置默认
    if YAML_SCAN_RULES is not None:
        return {k: list(v) for k, v in YAML_SCAN_RULES.items()}
    row = db.query_one("SELECT value FROM settings WHERE key = 'scan_rules'")
    if row is None:
        return {k: list(v) for k, v in DEFAULT_RULES.items()}
    try:
        data = json.loads(row["value"])
        return {
            "name_keywords": data.get("name_keywords", DEFAULT_RULES["name_keywords"]),
            "binary_names": data.get("binary_names", DEFAULT_RULES["binary_names"]),
            "content_markers": data.get("content_markers", DEFAULT_RULES["content_markers"]),
        }
    except Exception:
        return {k: list(v) for k, v in DEFAULT_RULES.items()}


@router.get("")
def get_rules(user: str = Depends(get_current_user)):
    return ok(get_rules_value())


@router.put("")
def put_rules(req: ScanRulesReq, user: str = Depends(get_current_user)):
    for group in (req.name_keywords, req.binary_names, req.content_markers):
        for p in group:
            if not _RULE_RE.match(p or ""):
                raise ApiError(VALIDATION_FAILED, f"规则含非法字符（仅允许字母数字 . _ -）：{p}")
    value = json.dumps({
        "name_keywords": req.name_keywords,
        "binary_names": req.binary_names,
        "content_markers": req.content_markers,
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('scan_rules', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (value,),
    )
    if YAML_SCAN_RULES is not None:
        return ok(get_rules_value(), msg="已保存到数据库；但 config.yaml 存在 scan 段（优先级更高），删除该段并重启后 UI 设置才生效")
    return ok(get_rules_value())
