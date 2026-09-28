"""把各类异常归约为统一 ErrorResponse 的处理器。

归约规则：
- RequestValidationError：按错误 loc 的首段（path/query/body/header）映射到
  可区分的 VALIDATION_* 分类；信封 code 取首个错误的来源，逐字段错误原样
  保留在 details.loc 中（含完整字段路径与列表下标）；
- BusinessError：业务拒绝 -> BUSINESS_RULE_VIOLATION（默认 409）；
- StarletteHTTPException（请求尚未进入业务代码就被框架挡下）：
  404 -> ROUTE_NOT_FOUND、405 -> METHOD_NOT_ALLOWED、
  401 -> UNAUTHENTICATED、403 -> ACCESS_DENIED、415 -> UNSUPPORTED_MEDIA_TYPE；
  其余 4xx 兜底 NOT_FOUND，5xx 兜底 INTERNAL_ERROR。
  这些分类的响应状态：未配置覆盖时**保留框架真实状态码**，本地配置覆盖后
  以配置值为准（与其它分类的配置方式完全一致）；
- 其它未捕获 Exception：INTERNAL_ERROR（默认 500），仅回传固定文案，
  完整堆栈只写服务端日志并带上 request_id，绝不外泄内部细节。

每类失败都落一条结构化日志，固定携带 request_id、最终分类与实际返回状态，
调用方给出的标识可直接用于日志反查。
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
    status_code: int,
    request: Request,
    *,
    detail: str = "",
) -> None:
    """统一失败日志：request_id / 最终分类 / 实际状态一次打全，便于反查。"""
    logger.info(
        "request failed request_id=%s category=%s status=%d method=%s path=%s%s",
        get_request_id(),
        category.value,
        status_code,
        request.method,
        request.url.path,
        f" detail={detail}" if detail else "",
    )


async def business_error_handler(
    request: Request, exc: BusinessError, settings: Settings
) -> Response:
    status_code = settings.status_for(exc.code)
    _log_failure(
        exc.code, status_code, request, detail=f"message={exc.message!r}"
    )
    return build_error_response(
        exc.code, exc.message, settings, status_code=status_code
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError, settings: Settings
) -> Response:
    category, details = _to_field_errors(exc.errors())
    status_code = settings.status_for(category)
    _log_failure(category, status_code, request, detail=f"errors={len(details)}")
    return build_error_response(
        category,
        "请求参数校验失败",
        settings,
        details=details,
        status_code=status_code,
    )


# 框架 HTTP 状态码 -> 稳定分类的确定性映射（未进业务代码即被挡下的请求）。
_HTTP_STATUS_CATEGORY: dict[int, ErrorCategory] = {
    401: ErrorCategory.UNAUTHENTICATED,
    403: ErrorCategory.ACCESS_DENIED,
    404: ErrorCategory.ROUTE_NOT_FOUND,
    405: ErrorCategory.METHOD_NOT_ALLOWED,
    415: ErrorCategory.UNSUPPORTED_MEDIA_TYPE,
}


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException, settings: Settings
) -> Response:
    category = _HTTP_STATUS_CATEGORY.get(exc.status_code)
    # 真实状态保留规则：命中显式映射的框架拒绝，未配置覆盖时回传其真实
    # 状态码；配置覆盖（生效值 != 默认值）后以配置值为准。其余状态走
    # 兜底分类，状态取该分类的配置值。
    preserve_real_status = category is not None
    if category is None:
        category = (
            ErrorCategory.INTERNAL_ERROR
            if exc.status_code >= 500
            else ErrorCategory.NOT_FOUND
        )
    if preserve_real_status and not settings.is_overridden(category):
        status_code = exc.status_code
    else:
        status_code = settings.status_for(category)
    if exc.status_code >= 500:
        # 5xx 细节不外露，详细信息只落服务端日志。
        logger.error(
            "framework http error request_id=%s status=%d detail=%r",
            get_request_id(),
            exc.status_code,
            exc.detail,
        )
        message = INTERNAL_ERROR_MESSAGE
    else:
        # 框架自带的安全文案（如 "Not Found"）可以透出；不含堆栈或内部信息。
        message = str(exc.detail) if exc.detail else "请求的资源不存在"
    _log_failure(category, status_code, request)
    return build_error_response(
        category, message, settings, status_code=status_code
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception, settings: Settings
) -> Response:
    status_code = settings.status_for(ErrorCategory.INTERNAL_ERROR)
    # 统一的结构化对账行（request_id/分类/状态），与其它失败日志同构。
    _log_failure(ErrorCategory.INTERNAL_ERROR, status_code, request)
    # 完整堆栈仅落日志，并携带 request_id 便于与客户端上报的标识对账。
    logger.exception(
        "unhandled exception request_id=%s category=%s status=%d path=%s method=%s error=%s",
        get_request_id(),
        ErrorCategory.INTERNAL_ERROR.value,
        status_code,
        request.url.path,
        request.method,
        exc,
    )
    return build_error_response(
        ErrorCategory.INTERNAL_ERROR,
        INTERNAL_ERROR_MESSAGE,
        settings,
        status_code=status_code,
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
