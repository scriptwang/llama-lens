from fastapi import APIRouter, Depends

from ..core.execstart import ParseError, build_execstart, lookup, parse_execstart, validate_args
from ..core.param_dict import PARAM_DICT
from ..core.unitfile import detect_execstart_style, find_execstart_span, join_continuation, replace_execstart
from ..errors import ApiError, PARSE_FAILED, ok
from .auth import get_current_user
from ..schemas import BuildReq, ParseReq

router = APIRouter(tags=["params"])


@router.get("/api/params")
def get_params(user: str = Depends(get_current_user)):
    return ok(PARAM_DICT)


def _lenient_validate(args: list) -> list:
    """预览用宽松规范化：仅处理 bool（值→None），不做严格范围校验"""
    out = []
    for a in args:
        if a.get("positional") or not a.get("flag"):
            out.append(dict(a))
            continue
        canonical, entry = lookup(a["flag"])
        if entry is not None and entry["type"] == "bool":
            out.append({"flag": a["flag"], "canonical": canonical, "value": None, "known": True})
            continue
        out.append(dict(a))
    return out


@router.post("/api/parse")
def parse_content(req: ParseReq, user: str = Depends(get_current_user)):
    """纯解析（无 SSH）：把单元文件内容解析出 ExecStart 参数，用于源码↔可视化实时联动"""
    lines = req.content.splitlines()
    span = find_execstart_span(lines)
    if not span:
        return ok({"parsed": None, "parse_error": "未找到 ExecStart 行"})
    try:
        parsed = parse_execstart(join_continuation(lines, span[0], span[1]))
        return ok({"parsed": parsed, "parse_error": ""})
    except ParseError as e:
        return ok({"parsed": None, "parse_error": str(e)})


@router.post("/api/build")
def build_content(req: BuildReq, user: str = Depends(get_current_user)):
    """纯拼接（无 SSH）：用给定参数重建 ExecStart 行，返回新内容，用于实时联动预览"""
    lines = req.content.splitlines()
    span = find_execstart_span(lines)
    if not span:
        raise ApiError(PARSE_FAILED, "未找到 ExecStart 行")
    try:
        parsed = parse_execstart(join_continuation(lines, span[0], span[1]))
    except ParseError as e:
        raise ApiError(PARSE_FAILED, str(e))
    try:
        validated = validate_args(req.args)
    except ApiError:
        validated = _lenient_validate(req.args)
    detected = detect_execstart_style(lines, span)
    if req.style == "multiline":
        style = {"multiline": True, "indent": detected["indent"] or "    "}
    elif req.style == "single":
        style = {"multiline": False, "indent": ""}
    else:
        style = detected
    new_content = replace_execstart(req.content, build_execstart(parsed, validated, style, lines, span))
    return ok({"content": new_content})
