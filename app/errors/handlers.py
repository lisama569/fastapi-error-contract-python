"""把各类异常归约为统一 ErrorResponse 的处理器。

归约规则：
- RequestValidationError：按错误 loc 的首段（path/query/body/header）映射到
  可区分的 VALIDATION_* 分类；信封 code 取首个错误的来源，逐字段错误原样
  保留在 details.loc 中（含完整字段路径与列表下标）；
- BusinessError：业务拒绝 -> BUSINESS_RULE_VIOLATION（默认 409）；
- StarletteHTTPException：框架层客户端错误按真实状态码映射到可区分分类——
  404 路由不存在 -> NOT_FOUND；405 方法不允许 -> METHOD_NOT_ALLOWED；
  401 -> UNAUTHORIZED；403 -> FORBIDDEN；415 媒体类型不支持 ->
  UNSUPPORTED_MEDIA_TYPE；其余 4xx 归 NOT_FOUND，5xx 归 INTERNAL_ERROR；
  对外状态码默认取这些分类的映射（默认值即真实状态），配置可单独覆盖；
- 其它未捕获 Exception：INTERNAL_ERROR（默认 500），仅回传固定文案，
  完整堆栈只写服务端日志并带上 request_id，绝不外泄内部细节。

所有失败路径都以统一结构落一条服务端日志：request_id / category / status
（经 request_context 的日志过滤器再兜底注入同名字段），方便拿客户端回传的
标识直接反查同一次请求。
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
from app.request_context import get_request_id

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


def _log_failure(
    category: ErrorCategory,
    settings: Settings,
    *,
    level: int = logging.INFO,
    detail: str = "",
    exc_info: bool = False,
) -> None:
    """所有失败路径统一落一条可对账日志。

    固定带上 request_id（取当前请求上下文）、最终分类 category 与实际返回
    状态码 status；客户端凭响应中的 request_id 即可直接反查本条日志。
    并发下 request_id 来自 contextvars，天然只属于当前请求。
    """
    status = settings.status_for(category)
    request_id = get_request_id()
    suffix = f" detail={detail}" if detail else ""
    logger.log(
        level,
        "request failed request_id=%s category=%s status=%d%s",
        request_id,
        category.value,
        status,
        suffix,
        exc_info=exc_info,
    )


async def business_error_handler(
    request: Request, exc: BusinessError, settings: Settings
) -> Response:
    _log_failure(
        exc.code,
        settings,
        detail=f"business rejection message={exc.message!r} path={request.url.path}",
    )
    return build_error_response(
        exc.code, exc.message, settings
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError, settings: Settings
) -> Response:
    category, details = _to_field_errors(exc.errors())
    _log_failure(
        category,
        settings,
        detail=f"validation failure path={request.url.path} errors={len(details)}",
    )
    return build_error_response(
        category,
        "请求参数校验失败",
        settings,
        details=details,
    )


# 框架 HTTP 异常真实状态码 -> 稳定分类的确定性映射。
_HTTP_STATUS_CATEGORY: dict[int, ErrorCategory] = {
    401: ErrorCategory.UNAUTHORIZED,
    403: ErrorCategory.FORBIDDEN,
    404: ErrorCategory.NOT_FOUND,
    405: ErrorCategory.METHOD_NOT_ALLOWED,
    415: ErrorCategory.UNSUPPORTED_MEDIA_TYPE,
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
    # 框架自带的安全文案（如 "Method Not Allowed"）可以透出；不含堆栈或内部信息。
    message = str(exc.detail) if exc.detail else "请求的资源不存在"
    # 透传框架层语义头（典型为 405 的 Allow: GET, ...），保留真实 HTTP 语义。
    passthrough_headers = dict(getattr(exc, "headers", None) or {})
    _log_failure(
        category,
        settings,
        detail=(
            f"framework rejection http_status={exc.status_code} "
            f"method={request.method} path={request.url.path}"
        ),
    )
    return build_error_response(
        category, message, settings, headers=passthrough_headers
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception, settings: Settings
) -> Response:
    # 完整堆栈仅落日志，并携带 request_id 便于与客户端上报的标识对账。
    _log_failure(
        ErrorCategory.INTERNAL_ERROR,
        settings,
        level=logging.ERROR,
        detail=f"unhandled exception path={request.url.path} method={request.method} error={exc}",
        exc_info=True,
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
