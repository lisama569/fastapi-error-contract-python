"""错误分类目录：供只读自查端点返回当前生效的分类/状态码/覆盖情况。"""

from pydantic import BaseModel

from app.config import Settings
from app.errors.codes import ErrorCategory


class CategoryEntry(BaseModel):
    """单个错误分类在当前进程内的生效情况。"""

    code: str
    default_status: int
    effective_status: int
    overridden: bool


class ErrorCatalog(BaseModel):
    """只读自查结果：错误分类全量清单 + 本地覆盖清单。"""

    request_id_header: str
    categories: list[CategoryEntry]
    overridden_codes: list[str]


def build_error_catalog(settings: Settings) -> ErrorCatalog:
    """根据当前生效配置构造只读目录。

    - categories 按 ErrorCategory 枚举声明顺序排列，稳定可读；
    - default_status 为内置默认值（与 OpenAPI 文档声明一致）；
    - effective_status 为真实请求当前实际返回的状态（含本地覆盖）；
    - overridden 标识该分类是否被本地配置改过，overridden_codes 是其汇总。
    """
    categories = [
        CategoryEntry(
            code=category.value,
            default_status=settings.default_status_for(category),
            effective_status=settings.status_for(category),
            overridden=settings.is_overridden(category),
        )
        for category in ErrorCategory
    ]
    return ErrorCatalog(
        request_id_header=settings.request_id_header,
        categories=categories,
        overridden_codes=[
            category.value for category in settings.overridden_categories()
        ],
    )
