"""把各类异常归约为统一 ErrorResponse 的处理器。

归约规则：
- RequestValidationError：按错误 loc 的首段（path/query/body/header）映射到
  可区分的 VALIDATION_* 分类；信封 code 取首个错误的来源，逐字段错误原样
  保留在 details.loc 中（含完整字段路径与列表下标）；
- BusinessError：业务拒绝 -> BUSINESS_RULE_VIOLATION（默认 409）；
- StarletteHTTPException：404/405 -> NOT_FOUND；其余 4xx 归 NOT_FOUND，
  5xx 归 INTERNAL_ERROR（该服务自身不主动产生 401/403 等）；
- 其它未捕获 Exception：INTERNAL_ERROR（默认 500），仅回传固定文案，
  完整堆栈只写服务端日志并带上 request_id，绝不外泄内部细节。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.config import Settings
from app.errors.codes import ErrorCategory
from app.errors.exceptions import BusinessError
from app.errors.responses import INTERNAL_ERROR_MESSAGE, build_error_response
from app.errors.schemas import FieldError

logger = logging.getLogger("app.errors")

_SOURCE_CATEGORY: dict[str, ErrorCategory] = {
    "path": ErrorCategory.VALIDATION_PATH,
    "query": ErrorCategory.VALIDATION_QUERY,
    "body": ErrorCategory.VALIDATION_BODY,
    "header": ErrorCategory.VALIDATION_HEADER,
}


def _category_from_loc(loc: tuple[object, ...]) -> ErrorCategory:
    """按 pydantic 定位元组的首段判定来源分类，未知来源默认按 body。"""
    if loc and isinstance(loc[0], str):
        return _SOURCE_CATEGORY.get(loc[0], ErrorCategory.VALIDATION_BODY)
    return ErrorCategory.VALIDATION_BODY


def _to_field_errors(errors: list[dict]) -> tuple[ErrorCategory, list[FieldError]]:
    details: list[FieldError] = []
    envelope_category = ErrorCategory.VALIDATION_BODY
    for index, err in enumerate(errors):
        loc_tuple = tuple(err.get("loc", ()))
        if index == 0:
            envelope_category = _category_from_loc(loc_tuple)
        details.append(
            FieldError(
                loc=[str(part) for part in loc_tuple],
                message=str(err.get("msg", "校验失败")),
                type=str(err.get("type", "value_error")),
            )
        )
    if not details:
        details.append(
            FieldError(loc=[], message="请求校验失败", type="value_error")
        )
    return envelope_category, details


async def business_error_handler(
    request: Request, exc: BusinessError, settings: Settings
) -> Response:
    logger.info(
        "business rejection code=%s message=%r path=%s",
        exc.code.value,
        exc.message,
        request.url.path,
    )
    return build_error_response(
        exc.code, exc.message, settings
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError, settings: Settings
) -> Response:
    category, details = _to_field_errors(exc.errors())
    logger.info(
        "validation failure category=%s path=%s errors=%d",
        category.value,
        request.url.path,
        len(details),
    )
    return build_error_response(
        category,
        "请求参数校验失败",
        settings,
        details=details,
    )


# 框架 HTTP 异常到稳定分类的确定性映射。
_HTTP_STATUS_CATEGORY: dict[int, ErrorCategory] = {
    404: ErrorCategory.NOT_FOUND,
    405: ErrorCategory.NOT_FOUND,
}


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException, settings: Settings
) -> Response:
    category = _HTTP_STATUS_CATEGORY.get(exc.status_code)
    if category is None:
        category = (
            ErrorCategory.INTERNAL_ERROR
            if exc.status_code >= 500
            else ErrorCategory.NOT_FOUND
        )
    # 框架自带的安全文案（如 "Not Found"）可以透出；不含堆栈或内部信息。
    message = str(exc.detail) if exc.detail else "请求的资源不存在"
    return build_error_response(category, message, settings)


async def unhandled_exception_handler(
    request: Request, exc: Exception, settings: Settings
) -> Response:
    # 完整堆栈仅落日志，并携带 request_id 便于与客户端上报的标识对账。
    logger.exception(
        "unhandled exception path=%s method=%s error=%s",
        request.url.path,
        request.method,
        exc,
    )
    return build_error_response(
        ErrorCategory.INTERNAL_ERROR,
        INTERNAL_ERROR_MESSAGE,
        settings,
    )


def register_exception_handlers(app: FastAPI, settings: Settings) -> None:
    """注册四类处理器；settings 以闭包注入，业务代码不感知状态码策略。

    四类处理器全部注册到 FastAPI 路由内的 ExceptionMiddleware（位于所有用户
    中间件之内）。因此未处理异常在 RequestIDMiddleware 上下文内即被归约：
    处理器能读到当前 request_id，响应向上回传时还会经过请求标识中间件的头部
    注入，保证响应头与响应体中的标识始终一致（含 500）。
    """

    async def _business(request: Request, exc: BusinessError) -> Response:
        return await business_error_handler(request, exc, settings)

    async def _validation(
        request: Request, exc: RequestValidationError
    ) -> Response:
        return await validation_exception_handler(request, exc, settings)

    async def _http(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        return await http_exception_handler(request, exc, settings)

    async def _unhandled(request: Request, exc: Exception) -> Response:
        return await unhandled_exception_handler(request, exc, settings)

    app.add_exception_handler(BusinessError, _business)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
    # 直接登记到处理器表，确保进入路由内 ExceptionMiddleware 的 handlers。
    app.exception_handlers[Exception] = _unhandled
