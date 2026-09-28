"""只读运行时自查路由：错误分类目录。"""

from fastapi import APIRouter, Request

from app.config import Settings
from app.meta.catalog import ErrorCatalog, build_error_catalog

router = APIRouter(tags=["meta"])


@router.get(
    "/meta/error-catalog",
    response_model=ErrorCatalog,
    summary="只读自查：当前生效的错误分类与状态码映射",
)
async def get_error_catalog(request: Request) -> ErrorCatalog:
    """返回当前进程实际生效的错误分类清单。

    - 纯本地读取，不依赖任何外部服务；
    - default_status 与自动生成的 OpenAPI 文档声明一致；
    - effective_status 与真实请求返回的状态一致（含本地配置覆盖）；
    - overridden / overridden_codes 标明哪些分类被本地配置改过。
    """
    settings: Settings = request.app.state.settings
    return build_error_catalog(settings)
