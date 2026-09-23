"""错误响应的对外数据结构。"""

from typing import Literal

from pydantic import BaseModel, Field


class FieldError(BaseModel):
    """单个字段的定位信息。loc 为来源 + 字段路径，如 ['body', 'age']。"""

    loc: list[str] = Field(..., examples=[["body", "age"]])
    message: str = Field(..., examples=["Input should be a valid integer"])
    type: str = Field(..., description="校验器/错误类型标识", examples=["int_parsing"])


class ErrorResponse(BaseModel):
    """所有失败响应归约后的统一信封。"""

    error: Literal["error"] = Field(
        default="error",
        description="固定标记位，客户端可据此判别错误信封；成功响应不含此字段。",
    )
    code: str = Field(..., description="稳定的错误分类，见 ErrorCategory", examples=["VALIDATION_BODY"])
    message: str = Field(..., description="面向人的概要信息（不保证稳定，勿用于分支判断）")
    request_id: str = Field(..., description="请求追踪标识，与响应头 X-Request-ID 一致")
    details: list[FieldError] | None = Field(
        default=None, description="逐字段定位信息；非校验类错误可能为 null"
    )
