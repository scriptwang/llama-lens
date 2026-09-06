def find_execstart_span(lines: list):
    """返回首个 ExecStart= 行（含反斜杠续行）的 (start, end) 下标，未找到返回 None"""
    for i, line in enumerate(lines):
        if line.strip().startswith("ExecStart="):
            j = i
            while j < len(lines) - 1 and lines[j].rstrip().endswith("\\"):
                j += 1
            return i, j
    return None


def join_continuation(lines: list, i: int, j: int) -> str:
    """把续行合并成单行 ExecStart=..."""
    parts = []
    for k in range(i, j + 1):
        s = lines[k]
        if s.rstrip().endswith("\\"):
            parts.append(s.rstrip()[:-1].strip())
        else:
            parts.append(s.strip())
    return " ".join(p for p in parts if p)


def replace_execstart(content: str, new_line: str) -> str:
    """替换 ExecStart= 行（含续行块）；不存在则插入 [Service] 段或追加到末尾"""
    lines = content.splitlines()
    span = find_execstart_span(lines)
    if span is None:
        for i, line in enumerate(lines):
            if line.strip() == "[Service]":
                lines.insert(i + 1, new_line)
                break
        else:
            lines.append(new_line)
    else:
        lines[span[0]:span[1] + 1] = [new_line]
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def detect_execstart_style(lines: list, span):
    """检测原 ExecStart 是否用反斜杠续行（多行），以及续行缩进。

    返回 {"multiline": bool, "indent": str}。用于反向拼接时保留原格式，
    避免把多行续行的 ExecStart 压成单行。
    """
    start, end = span
    if start >= end:
        return {"multiline": False, "indent": ""}
    indent = ""
    for k in range(start + 1, end + 1):
        line = lines[k]
        stripped = line.lstrip()
        if stripped:
            indent = line[: len(line) - len(stripped)]
            break
    return {"multiline": True, "indent": indent}
