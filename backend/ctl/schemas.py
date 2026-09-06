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


class HostUpdateReq(BaseModel):
    alias: Optional[str] = None
    browse_paths: Optional[str] = None


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
