#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex CLI ↔ llama-server Responses API 规范化代理。

背景（2026-08-29 实测定位）：
  Codex CLI（wire_api = "responses"）直连 llama-server /v1/responses 时，
  Qwen3 内置 chat template 会拒绝两类报文：

  1. input 中 system/developer 消息不在首位 → 500
     "Jinja Exception: System message must be at the beginning"
     （compaction 后历史重排时必然触发，CLI 会反复重试刷爆服务端）
  2. tools 含 web_search / namespace 等不支持类型 → 服务端告警刷屏
     （namespace 内的嵌套 function 工具会被整体丢弃）

本代理在两者之间做最小规范化（其余字段原样透传，SSE 流式直通）：
  - 所有 system/developer 消息合并为一条，置于 input[0]
  - tools 只保留 function 类型；namespace 展开取其内嵌 function 工具
  - instructions / reasoning / function_call / function_call_output 等不动

用法：
  python3 devtools/codex_llama_proxy.py \
      [--listen 127.0.0.1:8901] [--upstream http://ai.lan:8080]

然后把 ~/.codex/config.toml 中 [model_providers.llamacpp] 的
  base_url = "http://ai.lan:8080/v1"
改为
  base_url = "http://127.0.0.1:8901/v1"
并重启 Codex CLI。
"""
import argparse
import http.client
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

SYSTEM_ROLES = ("system", "developer")
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}
UPSTREAM_TIMEOUT = 1800  # 长 prompt 评测 + 长生成，给足余量

log = logging.getLogger("codex-llama-proxy")


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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = "http://ai.lan:8080"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _forward(self):
        upstream = urlsplit(self.upstream)
        length = self.headers.get("Content-Length")
        body = b""
        if length:
            body = self.rfile.read(int(length))

        if self.command == "POST" and self.path.rsplit("/", 1)[-1] == "responses" and body:
            try:
                data = json.loads(body)
                new_data, changes = normalize_responses(data)
                if changes:
                    body = json.dumps(new_data, ensure_ascii=False).encode("utf-8")
                    log.info("normalized %s: %s", self.path, "; ".join(changes))
            except (ValueError, TypeError) as exc:
                log.warning("body not normalizable, passthrough: %s", exc)

        conn = http.client.HTTPConnection(upstream.hostname, upstream.port or 80,
                                          timeout=UPSTREAM_TIMEOUT)
        try:
            headers = [(k, v) for k, v in self.headers.items()
                       if k.lower() not in HOP_BY_HOP]
            conn.putrequest(self.command, (upstream.path or "") + self.path,
                            skip_host=True, skip_accept_encoding=True)
            for key, value in headers:
                conn.putheader(key, value)
            conn.putheader("Host", upstream.netloc)
            if body:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(message_body=body)
            resp = conn.getresponse()

            self.send_response_only(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() in HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("client disconnected during %s", self.path)
        except Exception:
            log.exception("upstream error on %s", self.path)
            try:
                self.send_response(502, "Bad Gateway")
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b'{"error":{"code":502,"message":"proxy upstream error"}}')
            except Exception:
                pass
        finally:
            self.close_connection = True
            conn.close()

    do_GET = _forward
    do_POST = _forward
    do_PUT = _forward
    do_DELETE = _forward
    do_PATCH = _forward
    do_OPTIONS = _forward
    do_HEAD = _forward


def main():
    parser = argparse.ArgumentParser(description="Codex CLI ↔ llama-server Responses 规范化代理")
    parser.add_argument("--listen", default="127.0.0.1:8901", help="监听地址（默认 127.0.0.1:8901）")
    parser.add_argument("--upstream", default="http://ai.lan:8080", help="llama-server 地址（默认 http://ai.lan:8080）")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(message)s")
    Handler.upstream = args.upstream
    host, _, port = args.listen.rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port or 8901)), Handler)
    server.daemon_threads = True
    log.info("listening on http://%s -> %s", args.listen, args.upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
