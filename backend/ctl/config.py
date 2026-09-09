import base64
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # PyYAML 缺失时降级为纯环境变量配置
    yaml = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 配置文件路径（可用 LLAMACTL_CONFIG 覆盖，便于测试/多实例部署）
CONFIG_FILE = Path(os.environ.get("LLAMACTL_CONFIG", str(BASE_DIR / "config" / "config.yaml")))


def _load_yaml_config() -> dict:
    if yaml is None:
        return {}
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[LlamaCtl-Web] 警告: 配置文件解析失败（{CONFIG_FILE}）: {e}，使用默认配置", file=sys.stderr)
        return {}


_yaml = _load_yaml_config()
_server = _yaml.get("server") if isinstance(_yaml.get("server"), dict) else {}
_scan = _yaml.get("scan") if isinstance(_yaml.get("scan"), dict) else {}


def _bool(v, default):
    if v is None:
        return default
    return str(v).strip().lower() not in ("false", "0", "no", "off")


def _ui_config() -> dict:
    """config.yaml ui 段：顶部 TAB 显隐 + 监控页自动浏览默认开关（缺省全开/关）"""
    ui = _yaml.get("ui") if isinstance(_yaml.get("ui"), dict) else {}
    tabs = ui.get("tabs") if isinstance(ui.get("tabs"), dict) else {}
    ab = ui.get("auto_browse") if isinstance(ui.get("auto_browse"), dict) else {}
    return {
        "tabs": {
            "monitor": _bool(tabs.get("monitor"), True),
            "service": _bool(tabs.get("service"), True),
            "model": _bool(tabs.get("model"), True),
            "playground": _bool(tabs.get("playground"), True),
            "terminal": _bool(tabs.get("terminal"), True),
            "requests": _bool(tabs.get("requests"), True),
            "api": _bool(tabs.get("api"), True),
        },
        "auto_browse": {
            "enabled": _bool(ab.get("enabled"), False),
        },
    }


def _get(env_key: str, yaml_value, default):
    """优先级：环境变量 > config.yaml > 内置默认"""
    env = os.environ.get(env_key)
    if env is not None and env != "":
        return env
    if yaml_value is not None and yaml_value != "":
        return yaml_value
    return default


_data_dir = Path(_get("LLAMACTL_DATA_DIR", _server.get("data_dir"), str(BASE_DIR / "data")))
if not _data_dir.is_absolute():
    _data_dir = BASE_DIR / _data_dir
DATA_DIR = _data_dir


def _ensure_secret_file(name: str, gen) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    if not path.exists():
        path.write_text(gen())
        os.chmod(path, 0o600)
    return path.read_text().strip()


# 内置扫描规则（config.yaml 无 scan 段且数据库无记录时的兜底）
BUILTIN_SCAN_RULES = {
    "name_keywords": ["llama"],
    "binary_names": [
        "llama-server", "llama-cli", "llama-bench", "llama-quantize",
        "llama-gguf", "llama-perplexity", "llama-tokenizer", "main",
    ],
    "content_markers": [".gguf"],
}


def _norm_scan_rules(sec) -> dict:
    """规范化 yaml scan 段；未配置或全空返回 None"""
    if not isinstance(sec, dict):
        return None
    out = {}
    for key in BUILTIN_SCAN_RULES:
        val = sec.get(key)
        if isinstance(val, list) and val:
            out[key] = [str(x) for x in val]
    return out or None


# config.yaml 的扫描规则（None = 未配置，回退数据库/内置默认）
YAML_SCAN_RULES = _norm_scan_rules(_scan)


def _resolve_port() -> int:
    """面板端口：LLAMACTL_PORT / PORT 环境变量 > config.yaml server.port > 8000。"""
    for key in ("LLAMACTL_PORT", "PORT"):
        v = os.environ.get(key)
        if v:
            return int(v)
    if _server.get("port"):
        return int(_server["port"])
    return 8000


@dataclass
class Settings:
    host: str = str(_get("LLAMACTL_HOST", _server.get("host"), "0.0.0.0"))
    port: int = _resolve_port()
    db_path: str = str(_get("LLAMACTL_DB_PATH", _server.get("db_path"), str(DATA_DIR / "llama_ctl.db")))
    ssh_timeout: int = int(_get("LLAMACTL_SSH_TIMEOUT", _server.get("ssh_timeout"), 10))
    auth_enabled: bool = str(_get("LLAMACTL_AUTH_ENABLED", _server.get("auth_enabled"), "true")).lower() != "false"
    admin_user: str = str(_get("LLAMACTL_ADMIN_USER", _server.get("admin_user"), "admin"))
    admin_password: str = str(_get("LLAMACTL_ADMIN_PASSWORD", _server.get("admin_password"), ""))
    jwt_expire_hours: int = 24
    cors_origins: list = field(default_factory=lambda: [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ])
    jwt_secret: str = ""
    fernet_key: str = ""
    ui: dict = field(default_factory=_ui_config)


settings = Settings()
# 密钥优先级：环境变量 > config.yaml server 段 > data/ 下自动生成（jwt.secret / fernet.key）
settings.jwt_secret = (
    os.environ.get("LLAMACTL_JWT_SECRET")
    or str(_server.get("jwt_secret") or "")
    or _ensure_secret_file("jwt.secret", lambda: secrets.token_hex(32))
)
settings.fernet_key = (
    os.environ.get("LLAMACTL_FERNET_KEY")
    or str(_server.get("fernet_key") or "")
    or _ensure_secret_file("fernet.key", lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
)
