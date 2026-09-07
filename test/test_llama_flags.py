from backend.llama_flags import parse_cmdline


def test_parse_cmdline_basic():
    f = parse_cmdline("/usr/bin/llama-server -m /models/qwen.gguf -ngl 99 --flash-attn -c 32768")
    assert f["model"] == "/models/qwen.gguf"
    assert f["n_gpu_layers"] == "99"
    assert f["flash_attn"] is True
    assert f["ctx_size"] == "32768"


def test_parse_cmdline_bool_and_unknown():
    f = parse_cmdline("llama-server --kv-offload --unknown-flag value -t 8")
    assert f["kv_offload"] is True
    assert f["threads"] == "8"
    assert "unknown" not in f


def test_parse_cmdline_empty():
    assert parse_cmdline("") == {}
    assert parse_cmdline(None) == {}
