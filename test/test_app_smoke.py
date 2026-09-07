"""应用级冒烟：create_app 轻量（import 无副作用）+ 路由注册完整。"""
from fastapi.testclient import TestClient

from backend.main import create_app


def test_create_app_lightweight(tmp_path):
    # 不传 base_dir 也不应触碰真实 config/data（重初始化在 lifespan）
    app = create_app(str(tmp_path))
    paths = {r.path for r in app.routes}
    for expected in ("/api/health", "/api/auth/login", "/api/hosts",
                     "/api/services", "/api/metrics", "/ws/portal",
                     "/ws/hosts/{host_id}", "/api/hosts/{host_id}/overview"):
        assert expected in paths, "缺少路由: %s" % expected


def test_health_requires_lifespan(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as client:  # 触发 lifespan（空配置启动）
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["hosts"] == {}
