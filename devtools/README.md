# Codex CLI ↔ llama-server 规范化代理

让 Codex CLI（`wire_api = "responses"`）能稳定直连 llama-server 的本地代理。
纯 Python 3.9 标准库，无第三方依赖。

## 为什么需要它（2026-08-29 实测定位）

Codex CLI 直连 llama-server `/v1/responses` 时，Qwen3 内置 chat template
（GGUF 未带 chat_template，llama.cpp 用内置回退）会拒绝两类报文：

1. **500 致命错误**：`input` 中 system/developer 消息不在首位 →
   `Jinja Exception: System message must be at the beginning`。
   长线程 compaction 后 CLI 重建请求时必然触发，且 CLI 会反复重试刷爆服务端日志。
2. **告警刷屏**：`tools` 含 `web_search` / `namespace` 等不支持类型 →
   服务端 `unsupported Responses tool type ... skipped`；`namespace` 内嵌套的
   function 工具会被整体丢弃（工具不可用）。

## 代理做什么

只规范化 `POST */responses` 请求体，其余请求/字段原样透传，SSE 流式直通：

- 所有 `system`/`developer` 消息合并为一条 `developer` 消息，置于 `input[0]`
- `tools` 只保留 `function` 类型；`namespace` 递归展开取其内嵌 function 工具；
  其余类型（`web_search` 等）丢弃；tools 清空时连带移除 `tool_choice`
- `instructions`、`reasoning`、`function_call`、`function_call_output` 等一律不动
  （实测该 llama-server 构建接受 reasoning items，不能剥离）

## 使用

### 方式零：一键启用（推荐，无需重启后端）

在 code-server 终端里跑一次：

```bash
cd /data/case/LlamaLens && ./devtools/enable_codex_proxy.sh
```

它会：
1. 启动代理（幂等；从本终端拉起 → 挂在常驻 code-server 下，持久存活）
2. 把 `~/.codex/config.toml` 的 `[model_providers.llamacpp]` `base_url` 指向代理（幂等，先备份）

然后**重启 Codex CLI** 生效。

> 为什么需要手动跑：Codex 沙箱（嵌套 PID 命名空间 + `--die-with-parent`）里拉起的代理会随会话结束被杀，
> 无法持久；从 code-server 终端拉起则挂在常驻进程下，像后端一样存活。

### 方式一：后端守护（需重启后端；代理崩溃自动重启）

LlamaLens 后端内置代理守护（`backend/proxy_supervisor.py`）：

1. `.env` 中设置 `LLAMALENS_CODEX_PROXY=1`（模板见 `.env.example`）
2. 重启后端（`./run.sh`）

后端启动时自动拉起代理子进程，崩溃后指数退避自动重启（2s→60s），
后端停止时一并终止。可选环境变量：

- `LLAMALENS_PROXY_LISTEN`（默认 `127.0.0.1:8901`）
- `LLAMALENS_PROXY_UPSTREAM`（默认 `http://ai.lan:8080`）

日志：`logs/codex_llama_proxy.log`（gitignore）。

> 注意：若后端被强杀（非正常退出），代理子进程会成为孤儿继续占端口；
> 下次后端启动时守护检测到端口已占用会跳过拉起。此时旧代理若上游配置
> 已过期，需手动 `pkill -f codex_llama_proxy.py` 后再重启后端。

### 方式二：手动脚本（普通机器 / 不依赖后端）

```bash
./devtools/start_proxy.sh          # 启动（幂等）
./devtools/start_proxy.sh status   # 状态 + 上游连通性
./devtools/start_proxy.sh stop     # 停止
```

- 监听 `127.0.0.1:8901`，上游 `http://ai.lan:8080`
  （可用环境变量 `PROXY_LISTEN` / `PROXY_UPSTREAM` 覆盖）
- 日志：`logs/codex_llama_proxy.log`（gitignore）
- 健康检查即 `GET /v1/models` 经代理打到上游，能同时验证上游连通性

### Codex CLI 配置

`~/.codex/config.toml` 中 `[model_providers.llamacpp]`：

```toml
base_url = "http://127.0.0.1:8901/v1"   # 原为 http://ai.lan:8080/v1
```

改完**重启 Codex CLI** 生效。

## 已知限制

- 仅监听 127.0.0.1（本机 Codex CLI 专用，不对外）
- 上游超时 1800s（长 prompt 评测 + 长生成）
- 不缓存、不改写响应内容；上游 5xx 原样透传
