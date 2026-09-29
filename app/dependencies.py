"""演示用的框架层守卫：在请求进入业务代码前挡下非法请求。

- 身份/权限：缺失凭证 -> 401，凭证有效但无权 -> 403；
- 媒体类型：请求体声明为 JSON 但 Content-Type 不是 JSON -> 415。

守卫直接抛 Starlette HTTPException，由统一异常处理器归约到对应稳定分类，
业务代码完全不参与这些拒绝路径。
"""

from fastapi import Header, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

# 演示用令牌，仅用于把“未认证”和“已认证但无权”两条路径确定性区分开。
_ADMIN_TOKEN = "admin-secret"
_USER_TOKEN = "user-secret"


async def require_admin(authorization: str | None = Header(default=None)) -> None:
    """缺失/非法凭证 -> 401；普通身份访问管理资源 -> 403。"""
    if not authorization:
        raise StarletteHTTPException(status_code=401, detail="缺少身份凭证")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token not in {_ADMIN_TOKEN, _USER_TOKEN}:
        raise StarletteHTTPException(status_code=401, detail="身份凭证无效")
    if token != _ADMIN_TOKEN:
        raise StarletteHTTPException(status_code=403, detail="无权访问该资源")


async def require_json_content_type(request: Request) -> None:
    """带请求体时强制 JSON 媒体类型；非 JSON -> 415。

    无 Content-Length/body 的请求交由后续校验处理（通常是 422），
    这里只对“明确带了非 JSON 请求体”的情况负责。
    """
    content_length = request.headers.get("content-length")
    if not content_length or content_length == "0":
        return
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise StarletteHTTPException(
            status_code=415,
            detail=f"不支持的请求体媒体类型：{media_type or '未声明'}",
        )


# 演示用请求体上限（字节）：超过即 413，演示“其它框架层 4xx”归约路径。
_MAX_CONTENT_LENGTH = 1024 * 1024


async def require_reasonable_payload_size(request: Request) -> None:
    """请求体超过上限 -> 413（框架层拒绝，请求不进入业务代码）。"""
    raw = request.headers.get("content-length")
    if raw and raw.isdigit() and int(raw) > _MAX_CONTENT_LENGTH:
        raise StarletteHTTPException(
            status_code=413,
            detail=f"请求体过大：上限 {_MAX_CONTENT_LENGTH} 字节",
        )
