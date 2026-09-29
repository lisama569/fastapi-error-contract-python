"""统一错误响应信封的构造工具。"""

import uuid

from fastapi.responses import JSONResponse

from app.config import Settings
from app.errors.codes import ErrorCategory
from app.errors.schemas import ErrorResponse, FieldError
from app.request_context import get_request_id

# 500 类未处理异常对外的固定文案，绝不回传内部细节。
INTERNAL_ERROR_MESSAGE = "服务器内部错误，请凭 request_id 联系服务提供方。"


def build_error_response(
    code: ErrorCategory,
    message: str,
    settings: Settings,
    *,
    details: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
    status_code: int | None = None,
) -> JSONResponse:
    """构造统一结构的 JSONResponse。

    状态码解析优先级：

    1. 显式入参 ``status_code``（框架层拒绝传入框架给出的真实状态，或其分类
       被覆盖后的生效状态——调用方已决定最终写回哪个状态）；
    2. 该分类在 ``settings`` 中的映射值（业务异常/校验失败路径）。

    ``headers`` 用于透传框架层语义头（如 405 的 ``Allow``），不影响契约体。
    """
    request_id = get_request_id()
    if not request_id:
        # 理论上中间件总会设置；兜底保证错误体永远携带非空标识。
        request_id = uuid.uuid4().hex
    payload = ErrorResponse(
        code=code.value,
        message=message,
        request_id=request_id,
        details=details,
    )
    effective_status = (
        status_code if status_code is not None else settings.status_for(code)
    )
    return JSONResponse(
        status_code=effective_status,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )
