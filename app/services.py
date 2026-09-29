"""示例业务逻辑层：业务拒绝通过 BusinessError 表达，与 HTTP 无关。"""

from app.errors.codes import ErrorCategory
from app.errors.exceptions import BusinessError

# 仅保留一个“保留用户”用于演示确定性的业务拒绝。
_RESERVED_USER_ID = 0


def create_user(name: str, age: int) -> dict[str, int | str]:
    """创建用户的示例业务。

    - id=0 为保留 ID（演示用），命中即业务拒绝；
    - 正常时返回成功结果，成功响应语义与既有 {"Hello": "World"} 风格兼容。
    """
    if age <= 0:
        # 正常情况下 Pydantic 已拦截；业务层保留防御式拒绝。
        raise BusinessError("年龄必须为正整数")
    user_id = abs(hash(name)) % 100_000
    return {"id": user_id, "name": name, "age": age}


def reject_business(resource: str, reason: str) -> None:
    """显式演示一个业务规则拒绝入口。"""
    raise BusinessError(
        f"操作被业务规则拒绝：{resource}（{reason}）",
        code=ErrorCategory.BUSINESS_RULE_VIOLATION,
    )


# 演示用内存数据：只有这些 ID “查得到”。
_KNOWN_USER_IDS = {1}


def get_user(user_id: int) -> dict[str, int | str]:
    """按 ID 查用户的示例业务；查不到 -> 业务 NOT_FOUND（404）。

    与框架层“路由地址不存在”（ROUTE_NOT_FOUND）是两类不同语义：
    地址合法、业务上确实没有这条数据时才走这里。
    """
    if user_id not in _KNOWN_USER_IDS:
        raise BusinessError(
            f"用户不存在：id={user_id}",
            code=ErrorCategory.NOT_FOUND,
        )
    return {"id": user_id, "name": "demo-user", "age": 1}
