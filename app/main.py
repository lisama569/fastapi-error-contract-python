"""应用装配入口。

保留根目录 main.py 作为薄封装，兼容 `uvicorn main:app`。
"""

import logging

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors.handlers import register_exception_handlers
from app.introspection import build_introspection_router
from app.request_context import RequestIDLogFilter, RequestIDMiddleware
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
    app.include_router(build_introspection_router(settings))
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


# 本地默认日志格式：显式渲染 request_id 字段（由 RequestIDLogFilter 注入到
# 每条记录）；部署时可自行配置 handler/formatter。不覆盖已存在的 handler，
# 避免重复输出。
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
        )
    )
    # 挂在 handler 上，保证所有经过该 handler 的记录（含 uvicorn 等第三方
    # logger 传播上来的记录）在渲染前都具备 request_id 属性，避免格式缺字段。
    _handler.addFilter(RequestIDLogFilter())
    _root_logger.addHandler(_handler)
logging.getLogger("app").setLevel(logging.INFO)

app = create_app()
