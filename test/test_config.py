from backend.config import (DEFAULT_THRESHOLDS, merge_thresholds, resolve_env,
                           load_config)


def test_merge_thresholds_layered():
    m = merge_thresholds({"cpu": {"warn": 70}}, {"cpu": {"danger": 95}, "gpu_util": {"warn": 50}})
    assert m["cpu"]["warn"] == 70      # 全局覆盖
    assert m["cpu"]["danger"] == 95    # 主机覆盖
    assert m["gpu_util"]["warn"] == 50
    assert m["gpu_util"]["danger"] == 90  # 未覆盖字段保留默认
    assert m == {k: dict(v) for k, v in m.items()}  # 不共享默认表引用


def test_merge_thresholds_ignores_unknown_keys():
    m = merge_thresholds({"bogus": {"warn": 1}}, None)
    assert "bogus" not in m


def test_resolve_env(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    assert resolve_env("${FOO}/x") == "bar/x"
    assert resolve_env("${MISSING_XYZ}") == "${MISSING_XYZ}"  # 未解析原样保留
    assert resolve_env(123) == 123
    assert resolve_env(None) is None


def test_load_config_missing_credentials(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "hosts:\n  - id: h1\n    llama: {host: 1.2.3.4}\n"
        "    ssh: {host: 1.2.3.4, user: root}\n", encoding="utf-8")
    try:
        load_config(str(tmp_path))
        assert False, "should raise"
    except ValueError as e:
        assert "凭证" in str(e)


def test_load_config_legacy_hosts_yaml_fallback(tmp_path):
    """config.yaml 不存在时回退旧名 hosts.yaml（兼容老部署）。"""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "hosts.yaml").write_text(
        "global: {push_interval: 3.0}\n", encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert cfg.global_cfg.push_interval == 3.0


def test_load_config_prefers_config_yaml(tmp_path):
    """config.yaml 与 hosts.yaml 同时存在时优先 config.yaml。"""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "global: {push_interval: 2.0}\n", encoding="utf-8")
    (tmp_path / "config" / "hosts.yaml").write_text(
        "global: {push_interval: 9.0}\n", encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert cfg.global_cfg.push_interval == 2.0


def test_load_config_full(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "global: {push_interval: 2.0}\n"
        "hosts:\n  - id: h1\n    name: H1\n"
        "    llama: {host: 1.2.3.4, port: 8080}\n"
        "    ssh: {host: 1.2.3.4, user: root, password: pw}\n"
        "    disk_mounts: [/, /share]\n", encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert cfg.global_cfg.push_interval == 2.0
    assert len(cfg.hosts) == 1
    h = cfg.hosts[0]
    assert h.id == "h1"
    assert h.ssh.password == "pw"
    assert h.disk_mounts == ["/", "/share"]
    assert h.systemd_unit == "llama-server.service"  # 缺省回退
    assert h.thresholds["cpu"]["warn"] == DEFAULT_THRESHOLDS["cpu"]["warn"]
