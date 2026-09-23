"""业务拒绝异常，携带稳定的 reason 与可选字段定位。"""
from __future__ import annotations

from app.errors import ErrorCategory, ErrorLocation


class BusinessError(Exception):
    """业务规则拒绝。

    reason 为稳定的机器可读拒绝原因（对外契约，取值可追加），
    field/location 用于可选的逐字段定位，message 面向人类阅读。
    """

    def __init__(
        self,
        reason: str,
        *,
        message: str | None = None,
        field: str | None = None,
        location: ErrorLocation | str = ErrorLocation.BODY,
    ) -> None:
        self.reason = reason
        self.message = message or reason
        self.field = field
        self.location = ErrorLocation(location)
        super().__init__(self.message)

    @property
    def category(self) -> ErrorCategory:
        return ErrorCategory.BUSINESS_REJECTED
