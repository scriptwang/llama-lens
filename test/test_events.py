import time

from backend.events import EventDetector


def test_llama_online_transitions():
    ev = EventDetector()
    ev.set_llama_online(1.0, True, "qwen")
    assert [e["type"] for e in ev.events] == ["llama_up"]
    ev.set_llama_online(2.0, True)  # 无迁移
    assert len(ev.events) == 1
    ev.set_llama_online(3.0, False)
    assert [e["type"] for e in ev.events][-1] == "llama_down"


def test_ssh_transitions_initial_silent():
    ev = EventDetector()
    ev.set_ssh_connected(1.0, False)  # 首次观测只记基线
    assert len(ev.events) == 0
    ev.set_ssh_connected(2.0, True)
    assert [e["type"] for e in ev.events] == ["ssh_up"]


def test_task_start_end_dedup():
    ev = EventDetector()
    ev.set_task_running(1.0, True, 7)
    ev.task_end_with_stats(2.0, 7, 100, 5.0, 20.0, ctx_used=1000)  # 完整统计 → 立即发
    ev.task_end_with_stats(2.1, 7, 100, 5.0, 20.0, ctx_used=1000)  # 重复 → 吞掉
    ends = [e for e in ev.events if e["type"] == "task_end"]
    assert len(ends) == 1
    assert "100 tokens" in ends[0]["msg"]
    assert "上下文 1000" in ends[0]["msg"]


def test_task_end_incomplete_without_running_dropped():
    ev = EventDetector()
    # 未处于 running 状态的 API 源不完整结束事件直接丢弃
    ev.task_end_with_stats(1.0, 9, 50, 2.0, 25.0)
    assert len(ev.events) == 0


def test_alert_level_transitions():
    ev = EventDetector()
    ev.check_alerts([{"metric": "cpu", "level": "warn", "value": 85, "threshold": 80}])
    assert len(ev.events) == 0  # 首次观测只记基线
    ev.check_alerts([{"metric": "cpu", "level": "warn", "value": 86, "threshold": 80}])
    assert len(ev.events) == 0  # 同级别不重复
    ev.check_alerts([{"metric": "cpu", "level": "danger", "value": 95, "threshold": 90}])
    assert len(ev.events) == 1  # 升级
    # 模拟 30s 冷却已过，恢复应发事件
    ev._alert_levels["cpu"] = ("danger", time.time() - 60)
    ev.check_alerts([])
    assert len(ev.events) == 2
    assert "恢复正常" in ev.events[-1]["msg"]


def test_alert_cooldown_suppresses_recovery():
    ev = EventDetector()
    ev.check_alerts([{"metric": "cpu", "level": "warn", "value": 85, "threshold": 80}])   # 基线
    ev.check_alerts([{"metric": "cpu", "level": "danger", "value": 95, "threshold": 90}])  # 升级 → 发
    ev.check_alerts([])  # 冷却期内恢复 → 抑制
    assert len(ev.events) == 1


def test_model_change():
    ev = EventDetector()
    ev.set_model(1.0, "/models/a.gguf")
    assert len(ev.events) == 0  # 首次只记基线
    ev.set_model(2.0, "/models/b.gguf")
    assert [e["type"] for e in ev.events] == ["model_change"]
    ev.set_model(3.0, "/models/b.gguf")
    assert len(ev.events) == 1


def test_reset_task_state_clears_dedup():
    ev = EventDetector()
    ev.set_task_running(1.0, True, 1)
    ev.task_end_with_stats(2.0, 1, 10, 1.0, 10.0, ctx_used=100)
    ev.reset_task_state()  # llama 重启：任务 ID 重新计数
    ev.set_task_running(3.0, True, 1)
    ev.task_end_with_stats(4.0, 1, 10, 1.0, 10.0, ctx_used=100)
    ends = [e for e in ev.events if e["type"] == "task_end"]
    assert len(ends) == 2
