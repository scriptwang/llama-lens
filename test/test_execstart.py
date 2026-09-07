from backend.ctl.core.execstart import ParseError, build_execstart, parse_execstart
from backend.ctl.core.unitfile import (detect_execstart_style, find_execstart_span,
                                       join_continuation, replace_execstart)


def test_parse_execstart_basic():
    p = parse_execstart("ExecStart=/usr/bin/llama-server -m /models/q.gguf -ngl 99")
    assert p["executable"] == "/usr/bin/llama-server"
    assert p["prefix"] == ""
    flags = {a["flag"]: a["value"] for a in p["args"]}
    assert flags["-m"] == "/models/q.gguf"
    assert flags["-ngl"] == "99"


def test_parse_execstart_prefix_and_eq():
    p = parse_execstart("ExecStart=@/usr/bin/llama-server --mlock --port=8080")
    assert p["prefix"] == "@"
    flags = {a["flag"]: a["value"] for a in p["args"]}
    assert flags["--mlock"] is None
    assert flags["--port"] == "8080"


def test_parse_execstart_positional():
    p = parse_execstart("ExecStart=/usr/bin/tool input.txt")
    pos = [a for a in p["args"] if a.get("positional")]
    assert len(pos) == 1 and pos[0]["value"] == "input.txt"


def test_parse_execstart_empty_raises():
    try:
        parse_execstart("ExecStart=")
        assert False, "should raise ParseError"
    except ParseError:
        pass


def test_parse_build_roundtrip():
    line = "ExecStart=/usr/bin/llama-server -m /models/q.gguf -ngl 99 -c 32768"
    p = parse_execstart(line)
    out = build_execstart(p, p["args"], {"multiline": False, "indent": ""}, [line], (0, 0))
    assert "/usr/bin/llama-server" in out
    assert "-m /models/q.gguf" in out
    assert "-ngl 99" in out
    assert "-c 32768" in out


def test_unitfile_span_and_join():
    lines = ["[Service]", "ExecStart=/usr/bin/llama-server \\", "    -m /models/q.gguf", "[Install]"]
    span = find_execstart_span(lines)
    assert span == (1, 2)
    joined = join_continuation(lines, span[0], span[1])
    assert joined == "ExecStart=/usr/bin/llama-server -m /models/q.gguf"
    assert find_execstart_span(["[Service]"]) is None


def test_replace_execstart():
    content = "[Service]\nExecStart=/usr/bin/old -m a\n[Install]\n"
    new = replace_execstart(content, "ExecStart=/usr/bin/new -m b")
    assert "ExecStart=/usr/bin/new -m b" in new
    assert "/usr/bin/old" not in new


def test_detect_style():
    single = detect_execstart_style(["ExecStart=/usr/bin/x -m a"], (0, 0))
    assert single["multiline"] is False
    multi = detect_execstart_style(["ExecStart=/usr/bin/x \\", "    -m a"], (0, 1))
    assert multi["multiline"] is True
