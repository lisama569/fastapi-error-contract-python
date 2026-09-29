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

    # ---- 业务未找到（HTTP 404 默认；仅业务层“没查到这条数据”） ----
    NOT_FOUND = "NOT_FOUND"

    # ---- 框架层客户端错误（请求未进入业务代码即被挡下） ----
    # 默认状态码保留框架给出的真实值，且均可通过配置单独覆盖。
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"  # 404：路由地址不存在（框架层）
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"  # 405：请求方式与地址不匹配
    UNAUTHORIZED = "UNAUTHORIZED"  # 401：缺少/未通过身份认证
    FORBIDDEN = "FORBIDDEN"  # 403：已认证但无访问权限
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"  # 415：请求体格式不被支持
    # 其余框架层 4xx（如 413 请求体过大）：默认保留框架真实状态码。
    CLIENT_ERROR = "CLIENT_ERROR"

    # ---- 未处理异常（HTTP 500，不泄露细节） ----
    INTERNAL_ERROR = "INTERNAL_ERROR"
