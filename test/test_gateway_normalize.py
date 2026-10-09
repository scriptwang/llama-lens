"""网关规范化回归（Codex 500 修复核心，从 devtools 原样搬入）。"""
from backend.gateway.normalize import normalize_responses


def test_system_moved_to_front():
    body = {"input": [
        {"type": "message", "role": "user", "content": "hi"},
        {"type": "message", "role": "system", "content": "sys"},
    ]}
    out, changes = normalize_responses(body)
    assert out["input"][0]["role"] == "developer"
    assert out["input"][0]["content"][0]["text"] == "sys"
    assert any("consolidated" in c for c in changes)


def test_multiple_system_merged():
    body = {"input": [
        {"type": "message", "role": "system", "content": "a"},
        {"type": "message", "role": "user", "content": "hi"},
        {"type": "message", "role": "developer", "content": "b"},
    ]}
    out, _ = normalize_responses(body)
    assert out["input"][0]["role"] == "developer"
    text = out["input"][0]["content"][0]["text"]
    assert "a" in text and "b" in text
    assert len([i for i in out["input"] if i.get("role") in ("system", "developer")]) == 1


def test_system_already_first_unchanged():
    body = {"input": [
        {"type": "message", "role": "system", "content": "sys"},
        {"type": "message", "role": "user", "content": "hi"},
    ]}
    out, changes = normalize_responses(body)
    assert out["input"][0]["role"] == "system"
    assert not any("consolidated" in c for c in changes)


def test_tools_only_function_kept():
    body = {"tools": [{"type": "function", "name": "f"}, {"type": "web_search"}]}
    out, changes = normalize_responses(body)
    assert len(out["tools"]) == 1
    assert out["tools"][0]["type"] == "function"
    assert any("dropped" in c for c in changes)


def test_namespace_flattened():
    body = {"tools": [
        {"type": "namespace", "tools": [{"type": "function", "name": "inner"}]},
        {"type": "web_search"},
    ]}
    out, _ = normalize_responses(body)
    assert len(out["tools"]) == 1
    assert out["tools"][0]["name"] == "inner"


def test_empty_tools_removed_with_tool_choice():
    body = {"tools": [{"type": "web_search"}], "tool_choice": "auto"}
    out, _ = normalize_responses(body)
    assert "tools" not in out
    assert "tool_choice" not in out
