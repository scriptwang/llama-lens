"""P0-2 模型管理：纯函数单测（mmproj 配对 / 适配预估 / 根去重 / 占用映射）。"""
from backend.ctl.routers.models import (
    _dedupe_roots,
    _fit,
    _pair_mmproj,
    _service_model_map,
)


def _m(name, path=None, size=1000):
    return {"path": path or f"/d/{name}", "name": name, "dir": "/d", "size": size,
            "quant": "", "mmproj": "", "mmproj_name": ""}


def test_pair_mmproj_prefers_matching_base():
    models = [
        _m("Qwen3.8-27B-Q6_K.gguf"),
        _m("Qwen3.6-27B-mmproj-BF16.gguf"),
        _m("Qwen3.8-27B-mmproj-BF16.gguf"),
    ]
    _pair_mmproj(models)
    assert models[0]["mmproj"] == "/d/Qwen3.8-27B-mmproj-BF16.gguf"
    # mmproj 文件自身不再配对
    assert models[1]["mmproj"] == "" and models[2]["mmproj"] == ""


def test_pair_mmproj_fallback_single_in_dir():
    models = [_m("Mystery-7B-Q4_0.gguf"), _m("mmproj-anything-f16.gguf")]
    _pair_mmproj(models)
    assert models[0]["mmproj"] == "/d/mmproj-anything-f16.gguf"


def test_pair_mmproj_no_guess_with_multiple():
    models = [
        _m("DFlash2-1B-Q4_K_M.gguf"),
        _m("Qwen3.6-27B-mmproj-BF16.gguf"),
        _m("Qwen3.8-27B-mmproj-BF16.gguf"),
    ]
    _pair_mmproj(models)
    assert models[0]["mmproj"] == ""  # 多 mmproj 且主名不匹配 → 不配对


def test_pair_mmproj_no_cross_dir():
    models = [
        {"path": "/a/model.gguf", "name": "model.gguf", "dir": "/a", "size": 1,
         "quant": "", "mmproj": "", "mmproj_name": ""},
        _m("mmproj-model-f16.gguf", path="/b/mmproj-model-f16.gguf"),
    ]
    _pair_mmproj(models)
    assert models[0]["mmproj"] == ""


def test_fit_hybrid_pool():
    memory = {"gpus": [{"free_mb": 3200}], "ram": {"available_mb": 26000}}
    # 22.9GB 模型：需求 28.6GB < 总池 29.2GB → ok（混合加载）
    fit = _fit(22_900_000_000, memory)
    assert fit["level"] == "ok" and "混合" in fit["note"]
    # 31e9 字节 ≈ 29.3 GiB：需求 36.6 > 总池 28.5，权重 29.3 > 总池 → no
    assert _fit(31_000_000_000, memory)["level"] == "no"
    # 27e9 字节 ≈ 25.7 GiB：需求 32.1 > 总池，权重 25.7 < 总池 → tight
    assert _fit(27_000_000_000, memory)["level"] == "tight"


def test_fit_vram_only_and_cpu_only():
    vram = {"gpus": [{"free_mb": 40000}], "ram": {"available_mb": 8000}}
    assert _fit(30_000_000_000, vram)["level"] == "ok"
    assert "显存装得下" in _fit(30_000_000_000, vram)["note"]
    cpu = {"gpus": [], "ram": {"available_mb": 40000}}
    fit = _fit(30_000_000_000, cpu)
    assert fit["level"] == "ok" and "CPU" in fit["note"]
    assert _fit(1, None) == {"level": "unknown", "note": "无内存数据"}


def test_dedupe_roots():
    assert _dedupe_roots(["/share", "/share/AI", "/models", "/share/"]) == ["/share", "/models"]
    assert _dedupe_roots(["", "  ", "/"]) == ["/"]
    assert _dedupe_roots(["/a/b", "/a"]) == ["/a/b", "/a"]  # 顺序保留，互不包含才去重


def test_service_model_map():
    services = [
        {"name": "s1.service", "execstart": "ExecStart=/bin/llama-server -m /m/a.gguf --mmproj /m/a-mm.gguf -ngl 99"},
        {"name": "s2.service", "execstart": "ExecStart=/bin/llama-server --model /m/b.gguf"},
        {"name": "s3.service", "execstart": ""},
    ]
    in_use, mm_in_use = _service_model_map(services)
    assert in_use["/m/a.gguf"] == ["s1.service"]
    assert in_use["/m/b.gguf"] == ["s2.service"]
    assert mm_in_use["/m/a-mm.gguf"] == ["s1.service"]
