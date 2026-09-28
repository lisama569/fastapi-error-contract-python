"""应用装配入口。

保留根目录 main.py 作为薄封装，兼容 `uvicorn main:app`。
"""

import logging

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors.handlers import register_exception_handlers
from app.meta.routes import router as meta_router
from app.request_context import RequestIDLogFilter, RequestIDMiddleware
from app.routes import router


def configure_logging() -> None:
    """配置 app 日志：固定携带 request_id/source/warning 字段。

    过滤器加在实际发日志的子 logger 上（logging 只执行发起 logger 自身的
    filter），这样无论生产 StreamHandler 还是测试 caplog 都能读到属性；
    取值来自 contextvars，并发下每条日志只属于自己的请求。
    """
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not any(getattr(h, "_request_id_handler", False) for h in app_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s "
                "[request_id=%(request_id)s source=%(request_id_source)s "
                "warning=%(request_id_warning)s] %(message)s"
            )
        )
        handler._request_id_handler = True  # type: ignore[attr-defined]
        app_logger.addHandler(handler)
    for logger_name in ("app.errors", "app.request_id"):
        target = logging.getLogger(logger_name)
        if not any(isinstance(f, RequestIDLogFilter) for f in target.filters):
            target.addFilter(RequestIDLogFilter())


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()
    app = FastAPI(
        title="fastapi-error-contract",
        version="0.1.0",
        description="统一接口错误响应契约示例服务。",
    )
    app.state.settings = settings
    register_exception_handlers(app, settings)
    app.include_router(router)
    app.include_router(meta_router)

    # FastAPI 会把 Exception/500 处理器交给最外层的 ServerErrorMiddleware
    # （见 FastAPI.build_middleware_stack），普通 add_middleware 的用户中间件
    # 都会被包在它内侧，导致 500 响应绕过 request_id 头部注入、且处理器执行时
    # 上下文已被清理。这里覆盖栈构建：先构建框架默认栈（含 ServerError
    # Middleware），再整体包一层 RequestIDMiddleware，使请求标识中间件成为
    # 真正的最外层——任何失败（含 500）发出时上下文仍在、头部也会被注入。
    default_build_stack = app.build_middleware_stack

    def build_stack() -> object:
        inner = default_build_stack()
        return RequestIDMiddleware(
            inner, header_name=settings.request_id_header
        )

    app.build_middleware_stack = build_stack  # type: ignore[method-assign]
    return app


logging.getLogger("app").setLevel(logging.INFO)

app = create_app()
