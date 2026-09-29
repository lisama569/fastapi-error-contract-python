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
    status: int | None = None,
) -> JSONResponse:
    """构造统一结构的 JSONResponse，状态码默认取自可配置映射。

    ``headers`` 用于透传框架层语义头（如 405 的 ``Allow``），不影响契约体。
    ``status`` 供框架层处理器显式指定真实状态码（默认映射未覆盖时保留框架
    原值）；不传则取 ``settings.status_for(code)``。
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
    return JSONResponse(
        status_code=status if status is not None else settings.status_for(code),
        content=payload.model_dump(mode="json"),
        headers=headers,
    )
