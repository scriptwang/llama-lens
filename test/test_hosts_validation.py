import pytest

from backend.ctl.errors import ApiError
from backend.ctl.routers.hosts import validate_monitor_fields


def test_valid_fields_pass():
    validate_monitor_fields("llama-server.service", "llama-server", "journal",
                            "llama-server", None, ["/", "/share"])
    validate_monitor_fields("llama.service", "llama-server", "file",
                            "llama-server", "/var/log/llama.log", ["/"])


@pytest.mark.parametrize("bad_name", ["x; rm -rf /", "a$(reboot)", "a`id`", "a b", ""])
def test_process_name_injection_rejected(bad_name):
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", bad_name, "journal", "u", None, ["/"])


@pytest.mark.parametrize("bad_unit", ["u; rm -rf /", "a b", "a$(x)"])
def test_systemd_unit_injection_rejected(bad_unit):
    with pytest.raises(ApiError):
        validate_monitor_fields(bad_unit, "llama-server", "journal", "u", None, ["/"])


@pytest.mark.parametrize("bad_log_unit", ["u; x", "a b"])
def test_log_unit_injection_rejected(bad_log_unit):
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", "llama-server", "journal", bad_log_unit, None, ["/"])


@pytest.mark.parametrize("bad_mount", ["/; rm", "/a b", "relative", ""])
def test_mount_injection_rejected(bad_mount):
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", "llama-server", "journal", "u", None, [bad_mount])


def test_file_source_requires_path():
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", "llama-server", "file", "u", None, ["/"])


def test_file_source_bad_path_rejected():
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", "llama-server", "file", "u", "/var/log/a b.log", ["/"])
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", "llama-server", "file", "u", "relative.log", ["/"])


def test_bad_log_source_rejected():
    with pytest.raises(ApiError):
        validate_monitor_fields("u.service", "llama-server", "syslog", "u", None, ["/"])
