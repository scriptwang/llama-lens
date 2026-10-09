"""Responses API 规范化（从 devtools/codex_llama_proxy.py 原样搬入）。

修 Codex CLI（wire_api="responses"）直连 llama-server 的 500：
- system/developer 消息合并为一条 developer，置于 input[0]
  （修 "Jinja Exception: System message must be at the beginning"）
- tools 只保留 function（namespace 递归展开、其余丢弃、清空连带移除 tool_choice）
仅作用于 POST */responses，其余字段原样透传。
"""

SYSTEM_ROLES = ("system", "developer")


def _item_text(item):
    """提取 message item 的纯文本（content 为 str 或 part 列表）。"""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text"):
                text = part.get("text")
                if text:
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


def _flatten_tools(tools):
    """只保留 function 工具；namespace 递归展开；其余类型丢弃。"""
    kept, dropped = [], []
    for tool in tools:
        if not isinstance(tool, dict):
            dropped.append("?")
            continue
        tool_type = tool.get("type")
        if tool_type == "function":
            kept.append(tool)
        elif tool_type == "namespace":
            inner = tool.get("tools")
            if isinstance(inner, list):
                sub_kept, sub_dropped = _flatten_tools(inner)
                kept.extend(sub_kept)
                dropped.extend(sub_dropped)
            else:
                dropped.append("namespace")
        else:
            dropped.append(str(tool_type))
    return kept, dropped


def normalize_responses(body):
    """规范化 Responses 请求体，返回 (new_body, changes)。"""
    changes = []
    if not isinstance(body, dict):
        return body, changes

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        kept, dropped = _flatten_tools(tools)
        if dropped:
            changes.append("dropped tools: %s" % ", ".join(sorted(set(dropped))))
        if kept:
            body["tools"] = kept
        else:
            body.pop("tools", None)
            body.pop("tool_choice", None)
            changes.append("removed empty tools/tool_choice")

    items = body.get("input")
    if isinstance(items, list):
        sys_idx = [
            i for i, it in enumerate(items)
            if isinstance(it, dict) and it.get("type") == "message"
            and it.get("role") in SYSTEM_ROLES
        ]
        if sys_idx and (len(sys_idx) > 1 or sys_idx[0] != 0):
            texts = [t for t in (_item_text(items[i]) for i in sys_idx) if t]
            rest = [it for i, it in enumerate(items) if i not in set(sys_idx)]
            if texts:
                merged = {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "\n\n".join(texts)}],
                }
                body["input"] = [merged] + rest
            else:
                body["input"] = rest
            changes.append("consolidated %d system/developer message(s) to input[0]" % len(sys_idx))

    return body, changes
