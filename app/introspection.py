"""只读的运行时契约自查能力。

不翻代码、不翻文档即可看到：
- 当前生效的错误分类清单（按枚举声明顺序，稳定排序）；
- 每类的内置默认状态码与实际生效状态码；
- 哪些分类被本地配置（环境变量或显式 Settings）覆盖过。

数据直接取自进程内 Settings，与真实请求的状态码映射、自动生成的 OpenAPI
文档同一份来源，不依赖任何外部服务。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import (
    DEFAULT_STATUS_MAP,
    PASSTHROUGH_CATEGORIES,
    Settings,
)
from app.errors.codes import ErrorCategory


class CategoryStatus(BaseModel):
    code: str = Field(..., description="稳定错误分类名", examples=["VALIDATION_BODY"])
    default_status: int | None = Field(
        ...,
        description=(
            "内置默认 HTTP 状态码；状态透传分类（如 CLIENT_ERROR）没有单一"
            "默认状态、默认保留框架真实状态，此处为 null"
        ),
        examples=[422],
    )
    effective_status: int | None = Field(
        ...,
        description=(
            "当前实际生效的 HTTP 状态码；状态透传分类未被覆盖时为 null，"
            "表示默认保留框架给出的真实状态"
        ),
        examples=[400],
    )
    overridden: bool = Field(
        ..., description="生效状态是否相对内置默认行为被本地配置覆盖"
    )
    passthrough: bool = Field(
        ...,
        description="是否为状态透传分类：默认保留框架给出的真实状态，仅显式覆盖时改写",
    )


class ErrorContractView(BaseModel):
    request_id_header: str = Field(
        ..., description="请求标识头名（可配置）", examples=["X-Request-ID"]
    )
    categories: list[CategoryStatus]
    overridden_categories: list[str] = Field(
        ..., description="所有被本地配置覆盖的分类名"
    )


def build_introspection_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["meta"])

    @router.get(
        "/meta/error-contract",
        response_model=ErrorContractView,
        summary="只读自查：错误分类、默认/生效状态码与配置覆盖情况",
    )
    def error_contract() -> ErrorContractView:
        categories = [
            CategoryStatus(
                code=category.value,
                # 透传分类没有单一固定默认状态、默认透传框架真实状态，
                # default/effective 都以 null 如实展示；仅显式覆盖后才给具体值。
                default_status=(
                    None
                    if category in PASSTHROUGH_CATEGORIES
                    else DEFAULT_STATUS_MAP[category]
                ),
                effective_status=(
                    settings.status_for(category)
                    if settings.is_overridden(category)
                    or category not in PASSTHROUGH_CATEGORIES
                    else None
                ),
                overridden=settings.is_overridden(category),
                passthrough=category in PASSTHROUGH_CATEGORIES,
            )
            for category in ErrorCategory
        ]
        return ErrorContractView(
            request_id_header=settings.request_id_header,
            categories=categories,
            overridden_categories=[
                category.value for category in settings.overridden_categories()
            ],
        )

    return router
