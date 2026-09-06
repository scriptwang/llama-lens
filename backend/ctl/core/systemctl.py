import threading

from ..ssh_pool import exec_cmd

_cache = {}
_lock = threading.Lock()


def cmd_prefix(client, host_id: int) -> str:
    """root 返回 ''，非 root 返回 'sudo -n '（按主机缓存）"""
    with _lock:
        if host_id in _cache:
            return _cache[host_id]
    code, out, _ = exec_cmd(client, "id -u")
    prefix = "" if out.strip() == "0" else "sudo -n "
    with _lock:
        _cache[host_id] = prefix
    return prefix


def systemctl(client, host_id: int, verb: str, unit: str = "", timeout: int = None):
    cmd = f"{cmd_prefix(client, host_id)}systemctl {verb}" + (f" {unit}" if unit else "")
    return exec_cmd(client, cmd, timeout=timeout)
