"""业务层抛出的领域异常，与 HTTP 解耦。"""

from app.errors.codes import ErrorCategory


class BusinessError(Exception):
    def __init__(self, message: str, code: ErrorCategory = ErrorCategory.BUSINESS_RULE_VIOLATION) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
