#!/usr/bin/env bash
# 一键启用 Codex CLI 规范化代理（修 llama-server Jinja 500 / 工具告警刷屏 / 客户端卡死）
#
# 背景：Codex CLI（wire_api=responses）直连 llama-server 时，Qwen3 内置模板会
#   1) 因 system/developer 不在 input[0] 报 500 "System message must be at the beginning"
#   2) 因 tools 含 web_search/namespace 告警刷屏
# codex_llama_proxy.py 在中间做最小规范化。本脚本负责把它"真正跑起来并接上 Codex"：
#   [1/2] 启动代理（幂等；从本终端拉起 → 挂在常驻 code-server 下，持久存活）
#   [2/2] 把 ~/.codex/config.toml 的 base_url 指向代理（幂等，先备份）
#
# 用法（在 code-server 终端里跑一次）：
#   cd /data/case/LlamaLens && ./devtools/enable_codex_proxy.sh
# 然后重启 Codex CLI 生效。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LISTEN="${PROXY_LISTEN:-127.0.0.1:8901}"
PROXY_URL="http://$LISTEN/v1"
CONFIG="${HOME}/.codex/config.toml"

echo "==> [1/2] 启动规范化代理（监听 $LISTEN）"
"$ROOT/devtools/start_proxy.sh" start

echo "==> [2/2] 更新 Codex 配置 base_url -> $PROXY_URL"
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: 找不到 Codex 配置 $CONFIG" >&2
  exit 1
fi
if grep -qF "base_url = \"$PROXY_URL\"" "$CONFIG"; then
  echo "    base_url 已指向代理，跳过"
else
  bak="${CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
  cp "$CONFIG" "$bak"
  sed -i "s|base_url = \"http://ai.lan:8080/v1\"|base_url = \"$PROXY_URL\"|" "$CONFIG"
  if grep -qF "base_url = \"$PROXY_URL\"" "$CONFIG"; then
    echo "    已更新（备份：$bak）"
  else
    echo "ERROR: 未能更新 base_url（当前值可能不是 http://ai.lan:8080/v1）。" >&2
    echo "       请手动把 $CONFIG 中 [model_providers.llamacpp] 的 base_url 改为：$PROXY_URL" >&2
    exit 1
  fi
fi

echo
echo "✅ 完成。请重启 Codex CLI 使配置生效。"
echo "   代理日志：$ROOT/logs/codex_llama_proxy.log"
echo "   验证：重启后 llama-server journal 应不再出现 'System message must be at the beginning'。"
