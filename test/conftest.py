"""pytest 全局配置：测试数据（DB/密钥/临时文件）隔离到临时目录。

必须在导入 backend.ctl.* 之前设置（settings 在 import 时读取环境变量）。
"""
import base64
import os
import secrets
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="llamalens-test-")
os.environ["LLAMACTL_DATA_DIR"] = _TMP
os.environ["LLAMACTL_DB_PATH"] = os.path.join(_TMP, "test_ctl.db")
os.environ["LLAMACTL_JWT_SECRET"] = "unit-test-jwt-secret-0123456789abcdef"
os.environ["LLAMACTL_FERNET_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
