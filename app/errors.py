"""统一错误契约：分类枚举、来源枚举与错误响应模型。"""
from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """稳定的错误分类。值即对外契约，新增分类只能追加，禁止改动既有值。"""

    VALIDATION_FAILED = "validation_failed"
    BUSINESS_REJECTED = "business_rejected"
    INTERNAL_ERROR = "internal_error"
    # 以下为后续追加的分类：旧客户端解析 error 字段时应容忍未知取值
    NOT_FOUND = "not_found"
    REQUEST_REJECTED = "request_rejected"


class ErrorLocation(str, Enum):
    """校验失败的来源，用于区分 path/query/body/header 等不同入口。"""

    PATH = "path"
    QUERY = "query"
    BODY = "body"
    HEADER = "header"


# 分类 -> 默认 HTTP 状态码
DEFAULT_STATUS_MAP: dict[ErrorCategory, int] = {
    ErrorCategory.VALIDATION_FAILED: 422,
    ErrorCategory.BUSINESS_REJECTED: 409,
    ErrorCategory.INTERNAL_ERROR: 500,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.REQUEST_REJECTED: 400,
}

from pydantic import BaseModel, Field


class ErrorField(BaseModel):
    """逐字段定位信息。新增字段只能追加可选字段，禁止改动既有字段含义。"""

    location: ErrorLocation
    field: str = Field(description="点分字段路径，如 items.0.name")
    message: str


class ErrorResponse(BaseModel):
    """所有失败响应归约后的唯一结构。

    兼容性规则：既有字段永不删改、含义永不变更；新增信息只能以带默认值
    的可选字段追加，旧客户端可继续按既有结构解析。
    """

    error: str = Field(description="错误分类，稳定字符串，取自 ErrorCategory")
    request_id: str
    message: str
    fields: list[ErrorField] = Field(default_factory=list)
    # 业务拒绝时的稳定机器可读原因（追加字段，向后兼容）
    reason: str | None = None
