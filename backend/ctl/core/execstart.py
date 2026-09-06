import re
import shlex

from .param_dict import lookup
from .unitfile import detect_execstart_style
from ..errors import ApiError, VALIDATION_FAILED

SYSTEMD_PREFIXES = set("-@+:!")
SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9._/:=@%+,-]+$")


class ParseError(ValueError):
    pass


def parse_execstart(raw_line: str) -> dict:
    """解析 ExecStart= 行（支持 systemd 前缀、引号、--flag=value 形式）"""
    line = (raw_line or "").strip()
    if line.lower().startswith("execstart="):
        line = line.split("=", 1)[1]
    prefix = ""
    if line and line[0] in SYSTEMD_PREFIXES:
        prefix = line[0]
        line = line[1:]
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        raise ParseError(f"ExecStart 分词失败：{e}")
    if not tokens:
        raise ParseError("ExecStart 为空")
    executable = tokens[0]
    args = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-") and len(tok) > 1:
            if "=" in tok:
                flag, value = tok.split("=", 1)
                canonical, _ = lookup(flag)
                args.append({"flag": flag, "canonical": canonical, "value": value, "known": canonical is not None})
                i += 1
            else:
                canonical, entry = lookup(tok)
                if entry is not None and entry["type"] == "bool":
                    args.append({"flag": tok, "canonical": canonical, "value": None, "known": True})
                    i += 1
                elif entry is not None:
                    if i + 1 < len(tokens):
                        args.append({"flag": tok, "canonical": canonical, "value": tokens[i + 1], "known": True})
                        i += 2
                    else:
                        args.append({"flag": tok, "canonical": canonical, "value": None, "known": True})
                        i += 1
                else:
                    # 未知 flag：若下一个 token 是值（非 flag），消费为值（如 --fit off）
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        args.append({"flag": tok, "canonical": None, "value": tokens[i + 1], "known": False})
                        i += 2
                    else:
                        args.append({"flag": tok, "canonical": None, "value": None, "known": False})
                        i += 1
        else:
            args.append({"flag": None, "canonical": None, "value": tok, "known": False, "positional": True})
            i += 1
    return {"executable": executable, "prefix": prefix, "args": args}


def quote_if_needed(value: str) -> str:
    if SAFE_VALUE_RE.match(value):
        return value
    return shlex.quote(value)


def _arg_str(a: dict) -> str:
    """单个参数 → 字符串（flag + 值 / 仅 flag / 位置参数）"""
    if a.get("positional"):
        return quote_if_needed(str(a["value"]))
    v = a.get("value")
    if v in (None, ""):
        return a["flag"]
    return a["flag"] + " " + quote_if_needed(str(v))


def _orig_line_flags(line: str, is_first: bool) -> list:
    """把原 ExecStart 的一行拆成 flag 序列（位置参数记为 None），用于保留原分组。"""
    if is_first:
        line = line.split("=", 1)[1]
        if line and line[0] in SYSTEMD_PREFIXES:
            line = line[1:]
    line = line.rstrip()
    if line.endswith("\\"):
        line = line[:-1]
    try:
        toks = shlex.split(line)
    except ValueError:
        toks = line.split()
    flags = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok.startswith("-") and len(tok) > 1:
            if "=" in tok:
                flags.append(tok.split("=", 1)[0])
                i += 1
            else:
                flags.append(tok)
                canonical, entry = lookup(tok)
                if entry is not None and entry["type"] == "bool":
                    i += 1
                elif i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                    i += 2
                else:
                    i += 1
        else:
            flags.append(None)
            i += 1
    return flags


def _build_multiline_preserve(parsed: dict, args: list, original_lines: list, span) -> str:
    """多行重建：保留原 ExecStart 的换行分组（未改的参数原样留在原行，只更新被改的值）。"""
    start, end = span
    indent = detect_execstart_style(original_lines, span)["indent"] or "    "
    executable = parsed["prefix"] + parsed["executable"]

    new_value = {}
    new_order = []  # (flag 或 None, value)
    for a in args:
        if a.get("positional"):
            new_order.append((None, a.get("value")))
        else:
            v = a.get("value")
            new_value[a["flag"]] = "" if v in (None, "") else str(v)
            new_order.append((a["flag"], v))

    orig_lines_flags = [
        _orig_line_flags(original_lines[k], k == start) for k in range(start, end + 1)
    ]

    out_lines = []
    used = set()
    for flags in orig_lines_flags:
        parts = []
        for f in flags:
            if f is None:
                continue  # 位置参数：统一放到末尾
            if f in new_value:
                v = new_value[f]
                parts.append(f + (" " + quote_if_needed(v) if v else ""))
                used.add(f)
        if parts:
            out_lines.append(" ".join(parts))

    for f, v in new_order:
        if f is None:
            vv = "" if v in (None, "") else str(v)
            out_lines.append(quote_if_needed(vv))
        elif f not in used:
            vv = "" if v in (None, "") else str(v)
            out_lines.append(f + (" " + quote_if_needed(vv) if vv else ""))

    if not out_lines:
        return "ExecStart=" + executable
    result = ["ExecStart=" + executable + " \\"]
    for i, line in enumerate(out_lines):
        result.append(indent + line + (" \\" if i < len(out_lines) - 1 else ""))
    return "\n".join(result)


def build_execstart(parsed: dict, args: list, style: dict = None,
                    original_lines: list = None, span=None) -> str:
    """反向拼接 ExecStart= 行（保持顺序、自动加引号）。

    style={"multiline": bool, "indent": str}：原 ExecStart 为多行反斜杠续行时按多行重建，
    否则拼成单行。提供 original_lines+span 时，多行重建会**保留原换行分组**（未改参数原样、
    只更新被改的值），避免把 `-b 1024 -ub 1024 -np 1` 这类同行多参数拆成多行。
    """
    style = style or {}
    executable = parsed["prefix"] + parsed["executable"]
    arg_strs = [_arg_str(a) for a in args]
    if not style.get("multiline") or not arg_strs:
        return "ExecStart=" + " ".join([executable] + arg_strs)
    if original_lines is not None and span is not None:
        return _build_multiline_preserve(parsed, args, original_lines, span)
    indent = style.get("indent") or "    "
    out = ["ExecStart=" + executable + " \\"]
    for i, arg in enumerate(arg_strs):
        out.append(f"{indent}{arg} \\" if i < len(arg_strs) - 1 else f"{indent}{arg}")
    return "\n".join(out)


def validate_args(args: list) -> list:
    """按字典校验可视化模式参数，返回规范化 args（空值参数被移除）"""
    out = []
    for a in args:
        if a.get("positional") or not a.get("flag"):
            out.append(dict(a))
            continue
        canonical, entry = lookup(a["flag"])
        if entry is None:
            out.append(dict(a))
            continue
        value = a.get("value")
        if entry["type"] == "bool":
            out.append({"flag": a["flag"], "canonical": canonical, "value": None, "known": True})
            continue
        if value in (None, ""):
            continue
        if entry["type"] == "int":
            try:
                v = int(str(value).strip())
            except ValueError:
                raise ApiError(VALIDATION_FAILED, f"参数 {a['flag']} 需要整数")
        elif entry["type"] == "float":
            try:
                v = float(str(value).strip())
            except ValueError:
                raise ApiError(VALIDATION_FAILED, f"参数 {a['flag']} 需要数字")
        elif entry["type"] == "enum":
            v = str(value)
            if v not in (entry["options"] or []):
                raise ApiError(VALIDATION_FAILED, f"参数 {a['flag']} 取值需在 {entry['options']} 中")
        else:
            v = str(value)
        if isinstance(v, (int, float)):
            if entry["min"] is not None and v < entry["min"]:
                raise ApiError(VALIDATION_FAILED, f"参数 {a['flag']} 不能小于 {entry['min']}")
            if entry["max"] is not None and v > entry["max"]:
                raise ApiError(VALIDATION_FAILED, f"参数 {a['flag']} 不能大于 {entry['max']}")
        out.append({"flag": a["flag"], "canonical": canonical, "value": v, "known": True})
    return out
