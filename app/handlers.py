"""异常处理器：把校验失败、业务拒绝、未处理异常归约为统一错误响应。"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import ErrorContractSettings
from app.errors import ErrorCategory, ErrorField, ErrorLocation, ErrorResponse
from app.exceptions import BusinessError
from app.request_context import get_request_id

logger = logging.getLogger("app.errors")

_LOCATION_MAP = {
    "path": ErrorLocation.PATH,
    "query": ErrorLocation.QUERY,
    "body": ErrorLocation.BODY,
    "header": ErrorLocation.HEADER,
}


def _json_response(response: ErrorResponse, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=response.model_dump())


def _field_path(loc: Iterable[str | int]) -> str:
    # loc 首元素为来源（body/query/path/header），其余为字段路径
    parts = [str(part) for part in loc][1:]
    return ".".join(parts) if parts else ""


def _current_request_id(request: Request) -> str:
    # 中间件已写入 contextvar；极端情况下（如未装配中间件）兜底生成
    return get_request_id() or request.headers.get("x-request-id") or "unknown"


def build_unhandled_response(
    request_id: str, settings: ErrorContractSettings, *, log: bool = True
) -> JSONResponse:
    """构造未处理异常的统一响应。完整堆栈只进日志，绝不外泄细节。"""
    if log:
        logger.exception("unhandled exception request_id=%s", request_id)
    body = ErrorResponse(
        error=ErrorCategory.INTERNAL_ERROR.value,
        request_id=request_id,
        message="internal server error",
    )
    return JSONResponse(
        status_code=settings.status_for(ErrorCategory.INTERNAL_ERROR),
        content=body.model_dump(),
    )


def _flatten_validation_errors(exc: RequestValidationError) -> list[ErrorField]:
    fields: list[ErrorField] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        source = str(loc[0]) if loc else "body"
        location = _LOCATION_MAP.get(source, ErrorLocation.BODY)
        fields.append(
            ErrorField(
                location=location,
                field=_field_path(loc),
                message=str(err.get("msg", "invalid value")),
            )
        )
    return fields


def install_exception_handlers(app: FastAPI, settings: ErrorContractSettings) -> None:
    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = _current_request_id(request)
        fields = _flatten_validation_errors(exc)
        response = ErrorResponse(
            error=ErrorCategory.VALIDATION_FAILED.value,
            request_id=rid,
            message="request validation failed",
            fields=fields,
        )
        logger.info(
            "validation failed request_id=%s fields=%s",
            rid,
            [f.model_dump(mode="json") for f in fields],
        )
        return _json_response(response, settings.status_for(ErrorCategory.VALIDATION_FAILED))

    @app.exception_handler(BusinessError)
    async def handle_business(request: Request, exc: BusinessError) -> JSONResponse:
        rid = _current_request_id(request)
        fields: list[ErrorField] = []
        if exc.field is not None:
            fields.append(
                ErrorField(location=exc.location, field=exc.field, message=exc.message)
            )
        response = ErrorResponse(
            error=ErrorCategory.BUSINESS_REJECTED.value,
            request_id=rid,
            message=exc.message,
            fields=fields,
            reason=exc.reason,
        )
        logger.info(
            "business rejected request_id=%s reason=%s", rid, exc.reason
        )
        return _json_response(response, settings.status_for(ErrorCategory.BUSINESS_REJECTED))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 路由层错误归约到统一结构，但不暴露内部细节
        rid = _current_request_id(request)
        response = ErrorResponse(
            error=ErrorCategory.NOT_FOUND.value
            if exc.status_code == 404
            else ErrorCategory.REQUEST_REJECTED.value,
            request_id=rid,
            message=str(exc.detail) if exc.status_code in (404, 405) else "request rejected",
        )
        return _json_response(response, exc.status_code)

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 正常路径下未处理异常已由 RequestContextMiddleware 捕获（以保证
        # request_id 与响应头可用）；此处理器仅为未装配中间件时的兜底。
        rid = _current_request_id(request)
        return build_unhandled_response(rid, settings)
