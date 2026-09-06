"""llama.cpp 高频参数字典（数据驱动，可持续增补）

type: path | string | int | float | bool | enum
control: text | number | slider | switch | select
"""


def _e(aliases, type_, default, desc, category, control, min_=None, max_=None, step=None, options=None):
    return {
        "aliases": aliases,
        "type": type_,
        "default": default,
        "min": min_,
        "max": max_,
        "step": step,
        "options": options,
        "desc": desc,
        "category": category,
        "control": control,
    }


PARAM_DICT = {
    # -- 模型加载 --
    "--model": _e(["-m"], "path", "", "模型文件路径（.gguf）", "model", "text"),
    "--mmproj": _e([], "path", "", "多模态投影器文件路径（.gguf，视觉模型用）", "model", "text"),
    "--hf": _e([], "string", "", "从 HuggingFace 下载模型（repo:branch）", "model", "text"),
    "--lora": _e([], "path", "", "LoRA 适配器路径（可重复）", "model", "text"),
    "--lora-scaled": _e([], "string", "", "LoRA 适配器及缩放系数（file:scale）", "model", "text"),
    "--lora-merged": _e([], "path", "", "已合并 LoRA 的模型路径", "model", "text"),
    "--alias": _e([], "string", "Llama", "模型显示名称", "model", "text"),
    "--jinja": _e([], "bool", False, "使用模型内置 Jinja 对话模板", "model", "switch"),
    "--chat-template": _e([], "string", "", "自定义对话模板", "model", "text"),
    # -- GPU 与内存 --
    "--n-gpu-layers": _e(["-ngl"], "string", "999", "卸载到 GPU 的层数（999 或 all = 全部层）", "gpu", "text"),
    "--n-swa-gpu-layers": _e([], "int", 0, "卸载到 GPU 的 SWA 层数", "gpu", "number", 0, 100, 1),
    "--main-gpu": _e([], "int", 0, "主 GPU 编号", "gpu", "number", 0, 15, 1),
    "--tensor-split": _e([], "string", "", "多 GPU 张量切分比例（逗号分隔）", "gpu", "text"),
    "--no-mmap": _e([], "bool", False, "禁用 mmap，模型全量载入内存", "gpu", "switch"),
    "--mlock": _e([], "bool", False, "锁定模型常驻内存防换页", "gpu", "switch"),
    "--no-kv-offload": _e([], "bool", False, "KV 缓存保留在 CPU", "gpu", "switch"),
    "--flash-attn": _e(["-fa"], "enum", "auto", "Flash Attention", "gpu", "select", options=["on", "off", "auto"]),
    "--numa": _e([], "enum", "disable", "NUMA 策略", "gpu", "select", options=["disable", "numa", "isolate"]),
    "--tensors-split": _e([], "enum", "layer", "张量切分模式", "gpu", "select", options=["layer", "smart"]),
    "--n-cpu-moe": _e([], "int", 0, "MoE 专家层留在 CPU 的数量", "gpu", "number", 0, 100, 1),
    "--cpu-moe": _e([], "bool", False, "所有 MoE 专家层使用 CPU", "gpu", "switch"),
    # -- 上下文 --
    "--ctx-size": _e(["-c"], "int", 4096, "上下文窗口大小（自由输入，无范围限制）", "context", "number"),
    "--swa-full": _e([], "bool", False, "SWA 全上下文模式", "context", "switch"),
    "--cache-type-k": _e([], "enum", "f16", "K 缓存量化类型", "context", "select", options=["f16", "q8_0", "q4_0"]),
    "--cache-type-v": _e([], "enum", "f16", "V 缓存量化类型", "context", "select", options=["f16", "q8_0", "q4_0"]),
    "--swa-type": _e([], "enum", "f16", "SWA 缓存量化类型", "context", "select", options=["f16", "q8_0"]),
    "--rope-scaling": _e([], "enum", "none", "RoPE 上下文扩展方式", "context", "select", options=["none", "linear", "ycos"]),
    "--rope-frequency-base": _e([], "float", 0, "RoPE 频率基值（0=自动）", "context", "number", 0, 100000, 1),
    "--rope-frequency-scale": _e([], "float", 1, "RoPE 频率缩放", "context", "number", 0, 10, 0.1),
    # -- 采样 --
    "--temp": _e([], "float", 0.8, "采样温度，越高越随机", "sampling", "slider", 0, 2, 0.05),
    "--top-k": _e([], "int", 40, "每步保留候选词个数", "sampling", "number", 0, 1000, 1),
    "--top-p": _e([], "float", 0.95, "核采样累积概率阈值", "sampling", "slider", 0, 1, 0.01),
    "--min-p": _e([], "float", 0.05, "最小相对概率阈值", "sampling", "slider", 0, 1, 0.01),
    "--repeat-penalty": _e([], "float", 1.1, "重复惩罚，>1 抑制重复", "sampling", "number", 0, 2, 0.01),
    "--repeat-last-n": _e([], "int", 64, "重复惩罚统计窗口", "sampling", "number", 0, 2048, 1),
    "--seed": _e([], "int", 4294967295, "随机种子，固定可复现", "sampling", "number", 0, 4294967295, 1),
    "--ignore-eos": _e([], "bool", False, "忽略结束符 token", "sampling", "switch"),
    "--reverse-sampling": _e([], "bool", False, "反向采样（用于摘要类任务）", "sampling", "switch"),
    "--reasoning-format": _e([], "enum", "none", "推理模型输出格式", "sampling", "select", options=["none", "think", "auto"]),
    # -- 服务 --
    "--host": _e([], "string", "127.0.0.1", "服务监听地址", "server", "text"),
    "--port": _e([], "int", 8080, "服务监听端口", "server", "number", 1, 65535, 1),
    "--api-key": _e([], "string", "", "API 访问密钥", "server", "text"),
    "--n-parallel": _e([], "int", 1, "并行请求槽位数", "server", "number", 1, 32, 1),
    "--threads": _e(["-t"], "int", 0, "CPU 推理线程数（0=自动）", "server", "number", 1, 256, 1),
    "--batch-size": _e(["-b"], "int", 512, "提示词处理批大小", "server", "number", 1, 4096, 1),
    "--ubatch-size": _e(["-ub"], "int", 512, "提示词子批大小", "server", "number", 1, 4096, 1),
    "--n-predict": _e(["-np"], "int", -1, "生成 token 数，-1=不限", "server", "number", -1, 1048576, 1),
    "--n-keep": _e([], "int", 0, "保留的初始 token 数", "server", "number", 0, 1048576, 1),
    "--no-webui": _e([], "bool", False, "禁用内置 WebUI", "server", "switch"),
    "--metrics": _e([], "bool", False, "启用 Prometheus 指标", "server", "switch"),
    "--no-warmup": _e([], "bool", False, "跳过启动预热", "server", "switch"),
    "--prio": _e([], "int", 0, "进程优先级（0-2）", "server", "number", 0, 2, 1),
    "--slot-id-policy": _e([], "enum", "input", "槽位分配策略", "server", "select", options=["input", "round-robin"]),
    "--pool": _e([], "string", "default", "推理池名称", "server", "text"),
    # -- 日志 --
    "--log-format": _e([], "enum", "txt", "日志输出格式", "logging", "select", options=["txt", "json"]),
    "--verbose": _e([], "bool", False, "详细日志输出", "logging", "switch"),
    "--log-verbose": _e([], "bool", False, "更详细的日志输出", "logging", "switch"),
    "--no-csv-log": _e([], "bool", False, "禁用 CSV 日志", "logging", "switch"),
}

_INDEX = {}
for _canonical, _entry in PARAM_DICT.items():
    _INDEX[_canonical] = _canonical
    for _alias in _entry["aliases"]:
        _INDEX[_alias] = _canonical


def lookup(flag: str):
    """返回 (canonical, entry)；未收录返回 (None, None)"""
    canonical = _INDEX.get(flag)
    if canonical is None:
        return None, None
    return canonical, PARAM_DICT[canonical]
