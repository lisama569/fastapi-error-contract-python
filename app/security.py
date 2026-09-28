"""框架层守卫依赖：在业务代码执行前以 HTTPException 挡下非法请求。

这些依赖用于真实演示 401（未认证）/ 403（访问被拒绝）/
415（请求体媒体类型不支持），异常由统一的 StarletteHTTPException
处理器归约，不进入业务代码。
"""

from fastapi import Header, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

_JSON_CONTENT_TYPE = "application/json"


async def require_json_content_type(
    request: Request,
    content_type: str | None = Header(default=None),
) -> None:
    """请求体入口的媒体类型守卫：非 JSON 媒体类型 -> 415。

    判定在依赖注入阶段完成（路由函数 / 业务代码之前），与真实框架的
    UnsupportedMediaType 拦截点一致。
    """
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type != _JSON_CONTENT_TYPE:
        raise StarletteHTTPException(
            status_code=415,
            detail=(
                f"不支持的请求体媒体类型 {content_type!r}，"
                f"仅支持 {_JSON_CONTENT_TYPE}"
            ),
        )


async def require_admin_role(
    request: Request,
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
) -> None:
    """基于 X-User-Role 的访问守卫：缺失 -> 401，非 admin -> 403。"""
    if not x_user_role:
        raise StarletteHTTPException(status_code=401, detail="缺少身份凭据")
    if x_user_role != "admin":
        raise StarletteHTTPException(status_code=403, detail="访问被拒绝")
