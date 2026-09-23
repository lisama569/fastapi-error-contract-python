"""应用装配入口。

保留根目录 main.py 作为薄封装，兼容 `uvicorn main:app`。
"""

import logging

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors.handlers import register_exception_handlers
from app.request_context import RequestIDMiddleware
from app.routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="fastapi-error-contract",
        version="0.1.0",
        description="统一接口错误响应契约示例服务。",
    )
    app.state.settings = settings
    register_exception_handlers(app, settings)
    app.include_router(router)

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
