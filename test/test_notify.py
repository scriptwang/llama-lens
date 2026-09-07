"""告警推送：渠道格式化 + 发送 + 事件钩子。"""
import backend.notify as notify
from backend.events import EventDetector


def test_build_message_all_channels():
    for ch in notify.CHANNELS:
        _, body = notify.build_message(ch, "ai主机", "danger", "GPU0 温度", "78°C >= 75°C")
        assert isinstance(body, dict) and body
    # 各渠道关键字段
    _, wecom = notify.build_message("wecom", "h", "warn", "t", "c")
    assert wecom["msgtype"] == "markdown" and "h" in wecom["markdown"]["content"]
    _, ding = notify.build_message("dingtalk", "h", "danger", "t", "c")
    assert ding["msgtype"] == "text" and "告警" in ding["text"]["content"]
    _, fs = notify.build_message("feishu", "h", "info", "t", "c")
    assert fs["msg_type"] == "text"
    _, gen = notify.build_message("generic", "h", "warn", "t", "c")
    assert gen["host"] == "h" and gen["level"] == "warn"


def test_send_sync_success(monkeypatch):
    calls = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return FakeResp()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    ok, err = notify.send_sync("wecom", "https://x/hook", "ai", "danger", "t", "c")
    assert ok and err == ""
    assert calls["json"]["msgtype"] == "markdown"


def test_send_sync_bark_url(monkeypatch):
    calls = {}

    class FakeResp:
        status_code = 200
        text = "ok"

    def fake_post(url, timeout=None):
        calls["url"] = url
        return FakeResp()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    ok, _ = notify.send_sync("bark", "https://api.day.app/KEY123", "ai", "warn", "标题", "内容")
    assert ok
    assert calls["url"].startswith("https://api.day.app/KEY123/标题/")


def test_send_sync_http_error(monkeypatch):
    class FakeResp:
        status_code = 500
        text = "boom"

    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: FakeResp())
    ok, err = notify.send_sync("generic", "https://x", "h", "warn", "t", "c")
    assert not ok and "500" in err


def test_send_sync_exception(monkeypatch):
    def boom(url, **kw):
        raise OSError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    ok, err = notify.send_sync("wecom", "https://x", "h", "warn", "t", "c")
    assert not ok and "network down" in err


def test_event_detector_notify_sink():
    got = []
    det = EventDetector()
    det.set_notify_sink(got.append)
    det.emit(1.0, "danger", "alert", "GPU0 温度超阈值")
    det.emit(2.0, "error", "llama_down", "llama 离线")
    det.emit(3.0, "info", "task_start", "任务 #1 开始")  # 不推
    det.emit(4.0, "info", "task_end", "任务结束")        # 不推
    det.emit(5.0, "warn", "ssh_down", "SSH 断开")
    types = [e["type"] for e in got]
    assert types == ["alert", "llama_down", "ssh_down"]


def test_event_detector_no_sink_no_error():
    det = EventDetector()
    det.emit(1.0, "danger", "alert", "x")  # 未设置 notify sink 不报错
    assert len(det.events) == 1
