"""统一网关：透明统一入口 + 模型亲和路由 + 治理与观测。

数据面（/v1/*，API key 鉴权）：规范化 + SSE 透传 + 重试 + 路由。
控制面（/api/gateway/*，JWT 鉴权）：API key 管理 + 审计 + 状态。
详见 docs/07-统一网关方案.md。
"""
from .admin import router as admin_router
from .router import router as data_router

__all__ = ["data_router", "admin_router"]
