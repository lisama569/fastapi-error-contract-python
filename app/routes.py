"""HTTP 入口：覆盖 path/query/body/header 校验、业务拒绝、未处理异常。

每个路由通过 responses= 在 OpenAPI 中显式声明其可能返回的统一错误信封，
保证“运行时行为 / 自动文档 / 测试”三者描述一致。
"""

from typing import Annotated

import anyio
from fastapi import APIRouter, Header, Path, Query
from pydantic import BaseModel, Field

from app.errors.schemas import ErrorResponse
from app.request_context import get_request_id
from app.services import create_user, reject_business

router = APIRouter(tags=["demo"])

# 统一错误信封的 OpenAPI 引用；所有失败响应共享同一 schema。
_ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}

# 各路由按其实际可能返回的状态码声明错误响应（状态码仅是默认映射，
# 可通过配置覆盖；文档展示默认值）。
_COMMON_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "路由不存在等资源错误"},
}
_VALIDATION_ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "请求校验失败（VALIDATION_*）"},
}
_BUSINESS_ERROR_RESPONSES = {
    409: {"model": ErrorResponse, "description": "业务规则拒绝（BUSINESS_RULE_VIOLATION）"},
}
_INTERNAL_ERROR_RESPONSES = {
    500: {"model": ErrorResponse, "description": "未处理异常（INTERNAL_ERROR，不泄露细节）"},
}


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    age: int = Field(gt=0, lt=150)


class UserView(BaseModel):
    id: int
    name: str
    age: int


@router.get("/", response_model=dict[str, str])
def read_root() -> dict[str, str]:
    """既有成功响应，结构保持兼容。"""
    return {"Hello": "World"}


@router.get("/echo", response_model=dict[str, str])
async def echo(delay_ms: int = 0) -> dict[str, str]:
    """回显当前上下文的 request_id，用于并发串扰/重复检测验证。"""
    if delay_ms:
        await anyio.sleep(delay_ms / 1000)
    return {"request_id": get_request_id()}


@router.get(
    "/items/{item_id}",
    response_model=dict[str, object],
    responses={**_COMMON_ERROR_RESPONSES, **_VALIDATION_ERROR_RESPONSES},
)
def get_item(
    item_id: Annotated[int, Path(ge=1, description="路径参数：正整数 ID")],
    q: Annotated[str | None, Query(min_length=2, max_length=10)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> dict[str, object]:
    """演示路径参数与查询参数校验。"""
    return {"item_id": item_id, "q": q, "limit": limit}


@router.post(
    "/users",
    response_model=UserView,
    responses={**_VALIDATION_ERROR_RESPONSES, **_BUSINESS_ERROR_RESPONSES},
)
def post_user(payload: UserCreate) -> UserView:
    """演示请求体校验 + 业务拒绝。"""
    user = create_user(payload.name, payload.age)
    return UserView(**user)  # type: ignore[arg-type]


@router.post(
    "/business-reject",
    response_model=dict[str, str],
    responses={**_BUSINESS_ERROR_RESPONSES},
)
def business_reject(
    resource: Annotated[str, Header(alias="X-Resource")] = "demo-resource",
) -> dict[str, str]:
    """确定性的业务拒绝入口。"""
    reject_business(resource, "演示拒绝")
    return {"status": "ok"}  # pragma: no cover - 不可能到达


@router.get(
    "/boom",
    responses={**_INTERNAL_ERROR_RESPONSES},
)
def boom(
    x_trace: Annotated[
        str | None,
        Header(min_length=4, alias="X-Trace"),
    ] = None,
) -> dict[str, str]:
    """演示请求头校验失败与未处理异常两条路径。"""
    raise RuntimeError("intentional failure for contract verification")
