"""演示路由：覆盖 path/query/body 校验失败、业务拒绝、未处理异常等入口。"""
from __future__ import annotations

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.errors import ErrorLocation
from app.exceptions import BusinessError

router = APIRouter(prefix="/demo", tags=["demo"])


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=9999)


@router.get("/items/{item_id}")
def get_item(item_id: int) -> dict[str, int]:
    # item_id 非整数 -> path 入口校验失败
    return {"item_id": item_id}


@router.get("/items")
def list_items(page: int = 0, page_size: int = 20) -> dict[str, int]:
    # page/page_size 非整数 -> query 入口校验失败
    return {"page": page, "page_size": page_size}


@router.get("/echo-tenant")
def echo_tenant(x_tenant_id: int = Header(..., alias="X-Tenant-ID")) -> dict[str, int]:
    # X-Tenant-ID 非整数 -> header 入口校验失败
    return {"tenant_id": x_tenant_id}


@router.post("/items", status_code=201, responses={409: {"description": "业务拒绝"}})
def create_item(item: ItemCreate) -> dict[str, object]:
    # 请求体缺字段/取值越界 -> body 入口校验失败
    # 业务规则拒绝（示例：保留名称）
    if item.name.lower() == "forbidden":
        raise BusinessError(
            "item_name_forbidden",
            message="该名称不允许使用",
            field="name",
            location=ErrorLocation.BODY,
        )
    return {"name": item.name, "quantity": item.quantity}


@router.get("/boom")
def boom() -> dict[str, object]:
    # 未处理异常：对外仅返回 internal_error，细节只进日志
    raise RuntimeError("db connection string leaked secret //internal:5432")
