import re

from .unitfile import find_execstart_span, join_continuation
from ..errors import ApiError, validate_unit_name
from ..ssh_pool import exec_cmd, sftp_exists, sftp_read

UNIT_DIRS = ["/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"]


def _safe_pattern(p: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", p or "")


def _enumerate_units(client) -> set:
    """枚举全部 .service 单元（systemctl 不可用时自动降级为仅文件枚举）"""
    names = set()
    code, out, _ = exec_cmd(client, "systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null")
    if code == 0:
        for line in out.splitlines():
            parts = line.split()
            if parts and parts[0].endswith(".service"):
                names.add(parts[0])
    code, out, _ = exec_cmd(client, "ls /etc/systemd/system/*.service 2>/dev/null")
    for line in out.splitlines():
        base = line.strip().rsplit("/", 1)[-1]
        if base.endswith(".service"):
            names.add(base)
    return names


def _content_match_units(client, rules: dict) -> set:
    """内容探测：grep 三个单元目录，命中文件 basename 即单元名

    - binary_names：只在 ExecStart 行内按整词匹配（避免 main 命中 domain 等子串误报）
    - content_markers：全文件子串匹配（如 .gguf 可能出现在 Environment= 行）
    """
    patterns = []
    for b in rules.get("binary_names", []):
        b = _safe_pattern(b)
        if b:
            patterns.append(rf"^ExecStart=(.*[^A-Za-z0-9_-])?{re.escape(b)}([^A-Za-z0-9_-]|$)")
    for m in rules.get("content_markers", []):
        m = _safe_pattern(m)
        if m:
            patterns.append(re.escape(m))
    if not patterns:
        return set()
    dirs = " ".join(d + "/*.service" for d in UNIT_DIRS)
    pattern = "|".join(patterns)
    code, out, _ = exec_cmd(client, f"grep -lE '{pattern}' {dirs} 2>/dev/null")
    matched = set()
    for line in out.splitlines():
        base = line.strip().rsplit("/", 1)[-1]
        if base.endswith(".service"):
            matched.add(base)
    return matched


def _unit_detail(client, name: str, match_type: str) -> dict:
    validate_unit_name(name)
    code, out, _ = exec_cmd(
        client,
        f"systemctl show {name} -p ActiveState -p SubState -p UnitFileState -p FragmentPath 2>/dev/null",
    )
    props = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    fragment = props.get("FragmentPath", "")
    execstart = ""
    if fragment and sftp_exists(client, fragment):
        try:
            content = sftp_read(client, fragment)
            lines = content.splitlines()
            span = find_execstart_span(lines)
            if span:
                execstart = join_continuation(lines, span[0], span[1])
        except Exception:
            pass
    return {
        "name": name,
        "active_state": props.get("ActiveState", "unknown"),
        "sub_state": props.get("SubState", ""),
        "unit_file_state": props.get("UnitFileState", ""),
        "fragment_path": fragment,
        "execstart": execstart,
        "match_type": match_type,
    }


def scan_services(client, rules: dict) -> list:
    names = _enumerate_units(client)
    keywords = [k.lower() for k in rules.get("name_keywords", []) if k]
    matched = {}
    for name in names:
        if any(k in name.lower() for k in keywords):
            matched[name] = "name"
    for name in _content_match_units(client, rules):
        if name not in matched:
            matched[name] = "content"
    services = []
    for name in sorted(matched):
        try:
            services.append(_unit_detail(client, name, matched[name]))
        except ApiError:
            continue
    return services
