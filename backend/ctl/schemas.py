from typing import List, Optional

from pydantic import BaseModel


class LoginReq(BaseModel):
    username: str
    password: str = ""


class HostConnectReq(BaseModel):
    alias: str = ""
    host: str
    port: int = 22
    username: str
    auth_type: str = "password"
    password: Optional[str] = None
    key_data: Optional[str] = None
    key_passphrase: Optional[str] = None
    browse_paths: Optional[str] = None
    # 监控配置（可选，缺省用默认值）
    mid: Optional[str] = None
    monitor_enabled: bool = True
    llama_host: Optional[str] = None
    llama_port: int = 8080
    llama_interval: float = 1.0
    llama_slow_interval: float = 30.0
    llama_timeout: float = 3.0
    ssh_interval: float = 2.0
    ssh_keepalive: int = 15
    ssh_timeout: float = 15.0
    key_path: Optional[str] = None
    process_name: str = "llama-server"
    systemd_unit: str = "llama-server.service"
    log_source: str = "journal"
    log_unit: str = "llama-server"
    log_path: Optional[str] = None
    log_follow: bool = True
    log_catchup_sec: int = 30
    disk_mounts: Optional[list] = None
    thresholds: Optional[dict] = None
    # 告警推送
    notify_enabled: bool = False
    notify_type: str = "wecom"
    notify_url: str = ""


class HostUpdateReq(BaseModel):
    alias: Optional[str] = None
    browse_paths: Optional[str] = None
    # 监控配置（None = 不修改）
    monitor_enabled: Optional[bool] = None
    llama_host: Optional[str] = None
    llama_port: Optional[int] = None
    llama_interval: Optional[float] = None
    llama_slow_interval: Optional[float] = None
    llama_timeout: Optional[float] = None
    ssh_interval: Optional[float] = None
    ssh_keepalive: Optional[int] = None
    ssh_timeout: Optional[float] = None
    key_path: Optional[str] = None
    process_name: Optional[str] = None
    systemd_unit: Optional[str] = None
    log_source: Optional[str] = None
    log_unit: Optional[str] = None
    log_path: Optional[str] = None
    log_follow: Optional[bool] = None
    log_catchup_sec: Optional[int] = None
    # 告警推送（None = 不修改）
    notify_enabled: Optional[bool] = None
    notify_type: Optional[str] = None
    notify_url: Optional[str] = None
    disk_mounts: Optional[list] = None
    thresholds: Optional[dict] = None


class ParseReq(BaseModel):
    content: str


class BuildReq(BaseModel):
    content: str
    args: list
    style: str = "auto"  # auto | multiline | single


class ConfigSaveReq(BaseModel):
    mode: str
    content: Optional[str] = None
    args: Optional[list] = None
    restart: bool = True
    style: str = "auto"  # auto | multiline | single（visual 模式生效）


class ScanRulesReq(BaseModel):
    name_keywords: List[str]
    binary_names: List[str]
    content_markers: List[str]


class ServiceCreateReq(BaseModel):
    name: str
    content: str


class ServiceDuplicateReq(BaseModel):
    new_name: str
