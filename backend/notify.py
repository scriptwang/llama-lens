"""告警推送：webhook 通知（企业微信 / 钉钉 / 飞书 / Bark / 通用 JSON）。

- 由 EventDetector 的状态变化事件触发（新发/升级/恢复、llama 上下线、SSH 断连）。
- 发送失败只记日志，绝不影响监控主流程。兼容 Python 3.9。
"""
import asyncio
import json
import logging
from typing import Optional, Tuple

import httpx

log = logging.getLogger("llamalens.notify")

CHANNELS = ("wecom", "dingtalk", "feishu", "bark", "generic")
DEFAULT_CHANNEL = "wecom"

# 需要推送的事件类型（其余如 task_start/task_end 太吵，不推）
NOTIFY_EVENT_TYPES = frozenset({"alert", "llama_up", "llama_down", "ssh_up", "ssh_down"})


def _level_tag(level: str) -> str:
    return {"danger": "🔴 危险", "error": "🔴 危险", "warn": "🟡 警告", "info": "🟢 恢复"}.get(level, "ℹ️ 通知")


def build_message(channel: str, host_name: str, level: str, title: str, content: str) -> Tuple[str, dict]:
    """按渠道格式化消息。返回 (url 不变, 请求体 dict)。"""
    text = "%s %s\n%s\n%s" % (_level_tag(level), host_name, title, content)
    if channel == "wecom":
        return channel, {"msgtype": "markdown", "markdown": {"content": text}}
    if channel == "dingtalk":
        # 钉钉自定义机器人若设了关键词安全，"告警" 一词可命中
        return channel, {"msgtype": "text", "text": {"content": "【告警】" + text}}
    if channel == "feishu":
        return channel, {"msg_type": "text", "content": {"text": text}}
    if channel == "bark":
        # Bark: POST {url}/{title}/{body}
        return channel, {"title": title, "body": "%s %s\n%s" % (_level_tag(level), host_name, content)}
    # generic
    return channel, {"host": host_name, "level": level, "title": title, "content": content}


def send_sync(channel: str, url: str, host_name: str, level: str, title: str, content: str,
              timeout: float = 8.0) -> Tuple[bool, str]:
    """同步发送（调用方负责放到线程池）。返回 (ok, err)。"""
    try:
        ch, body = build_message(channel, host_name, level, title, content)
        if ch == "bark":
            # Bark 标准格式：{url}/{title}/{body}（device key 已含在 url 中）
            safe_title = title.replace("/", "-")[:60]
            safe_body = content.replace("\n", " ")[:200]
            full = "%s/%s/%s" % (url.rstrip("/"), safe_title, safe_body)
            r = httpx.post(full, timeout=timeout)
        else:
            r = httpx.post(url, json=body, timeout=timeout)
        if r.status_code >= 400:
            return False, "HTTP %d: %s" % (r.status_code, r.text[:200])
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


async def send_async(channel: str, url: str, host_name: str, level: str, title: str, content: str) -> None:
    """异步发送（fire-and-forget，异常只记日志）。"""
    try:
        loop = asyncio.get_running_loop()
        ok, err = await loop.run_in_executor(
            None, send_sync, channel, url, host_name, level, title, content)
        if not ok:
            log.warning("[%s] 告警推送失败（%s）: %s", host_name, channel, err)
    except Exception as e:
        log.warning("[%s] 告警推送异常: %s", host_name, e)
