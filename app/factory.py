"""应用工厂：组装中间件、异常处理器、路由与 OpenAPI 定制。"""
from __future__ import annotations

from fastapi import FastAPI

from app.config import ErrorContractSettings, load_settings
from app.handlers import install_exception_handlers
from app.middleware import RequestContextMiddleware
from app.openapi import customize_openapi
from app.routes import router as demo_router


def create_app(settings: ErrorContractSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="FastAPI 统一错误契约示例",
        version="0.1.0",
        description="所有失败响应归约为 ErrorResponse；分类、定位与状态码映射见文档。",
    )
    # 注意：必须在 add_middleware 之前定制 OpenAPI——加中间件后 app.routes
    # 中的 APIRoute 会被包装为 Mount，遍历时将无法识别。
    install_exception_handlers(app, settings)
    app.include_router(demo_router)
    customize_openapi(app)
    app.add_middleware(RequestContextMiddleware, settings=settings)

    @app.get("/", include_in_schema=False)
    def read_root() -> dict[str, str]:
        # 保持既有成功响应语义不变
        return {"Hello": "World"}

    return app
