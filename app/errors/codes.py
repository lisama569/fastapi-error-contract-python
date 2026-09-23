"""稳定的错误分类（error code）定义。

分类值是对外契约的一部分，一经发布只增不改不删。
"""

from enum import Enum


class ErrorCategory(str, Enum):
    # ---- 校验失败（HTTP 422 默认） ----
    VALIDATION_PATH = "VALIDATION_PATH"
    VALIDATION_QUERY = "VALIDATION_QUERY"
    VALIDATION_BODY = "VALIDATION_BODY"
    VALIDATION_HEADER = "VALIDATION_HEADER"

    # ---- 业务拒绝（HTTP 409 默认） ----
    BUSINESS_RULE_VIOLATION = "BUSINESS_RULE_VIOLATION"

    # ---- 未找到（HTTP 404 默认） ----
    NOT_FOUND = "NOT_FOUND"

    # ---- 未处理异常（HTTP 500，不泄露细节） ----
    INTERNAL_ERROR = "INTERNAL_ERROR"
