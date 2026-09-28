"""请求标识（request id）的生成、透传与并发下的重复检测。

契约：
- 每个 HTTP 响应都带 ``X-Request-ID``（头名可通过 Settings 配置）；
- 客户端未提供标识时，服务端生成 uuid4 标识，并通过
  ``X-Request-ID-Source: generated`` 明示“标识缺失被补全”；
- 客户端标识格式非法或与在途请求重复（并发串扰信号）时，**不静默复用**，
  替换为新生成的标识，响应头 ``X-Request-ID-Warning`` 给出原因
  （``invalid`` / ``duplicate-in-flight``），同时记录 warning 日志；
- 标识存于 contextvars，每个请求拥有独立上下文，协程并发下互不串扰；
- 所有 ``app.*`` 日志经 RequestIDLogFilter 自动带上当前请求标识，
  失败日志可凭客户端给出的标识直接反查。
"""

import contextvars
import logging
import re
import uuid
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.request_id")

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

# 最终使用的标识来源（client/generated）与告警原因，仅用于日志对账。
_request_id_source_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id_source", default=""
)
_request_id_warning_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id_warning", default=""
)

# 允许的客户端标识：字母数字及 . _ : -，长度 1..128。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:=-]{1,128}$")

SOURCE_CLIENT = "client"
SOURCE_GENERATED = "generated"
WARNING_INVALID = "invalid"
WARNING_DUPLICATE = "duplicate-in-flight"
WARNING_MISSING = "missing"


def _generate_request_id() -> str:
    return uuid.uuid4().hex


def is_valid_request_id(value: str) -> bool:
    return bool(_REQUEST_ID_RE.fullmatch(value))


def get_request_id() -> str:
    """返回当前协程上下文内的请求标识；上下文外返回空串。"""
    return _request_id_var.get()


class RequestIDMiddleware:
    """ASGI 中间件：规范化请求标识并注入响应头，检测并发重复。"""

    def __init__(
        self,
        app: ASGIApp,
        header_name: str = "X-Request-ID",
        *,
        id_factory: Callable[[], str] = _generate_request_id,
    ) -> None:
        self.app = app
        self.header_name = header_name
        self.header_bytes = header_name.lower().encode("latin-1")
        self._id_factory = id_factory
        # 在途请求标识集合 + 守卫锁（同一事件循环内串行化集合变更）。
        self._active: set[str] = set()
        self._lock = _DummyLock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw = self._read_header(scope)
        source = SOURCE_CLIENT
        warning: str | None = None

        if raw is None:
            # 标识缺失：补全并明示，绝不留空。
            request_id = self._id_factory()
            source = SOURCE_GENERATED
            warning = WARNING_MISSING
        elif not is_valid_request_id(raw):
            request_id = self._id_factory()
            source = SOURCE_GENERATED
            warning = WARNING_INVALID
        else:
            request_id = raw

        # 在途集合登记（生成的标识也要保证唯一）。并发重复 -> 替换并告警。
        async with self._lock:
            duplicated = request_id in self._active
            if duplicated:
                request_id = self._id_factory()
                source = SOURCE_GENERATED
                warning = WARNING_DUPLICATE
            self._active.add(request_id)

        # 先设置当前请求的日志上下文，再写标识相关日志，保证这些日志也能
        # 凭最终标识被反查到；contextvars 保证并发下互不串扰。
        token = _request_id_var.set(request_id)
        source_token = _request_id_source_var.set(source)
        warning_token = _request_id_warning_var.set(warning or "")
        try:
            if raw is None:
                logger.info(
                    "request id missing; generated=%s request_id=%s",
                    request_id, request_id,
                )
            elif warning == WARNING_INVALID:
                logger.warning(
                    "rejecting malformed request id %r; generated=%s request_id=%s",
                    raw, request_id, request_id,
                )
            elif duplicated:
                logger.warning(
                    "duplicate in-flight request id detected %r; "
                    "replaced=%s request_id=%s",
                    raw, request_id, request_id,
                )
            await self.app(
                scope,
                receive,
                self._wrap_send(send, request_id, source, warning),
            )
        finally:
            async with self._lock:
                self._active.discard(request_id)
            _request_id_warning_var.reset(warning_token)
            _request_id_source_var.reset(source_token)
            _request_id_var.reset(token)

    def _read_header(self, scope: Scope) -> str | None:
        for name, value in scope.get("headers", []):
            if name == self.header_bytes:
                return value.decode("latin-1")
        return None

    def _wrap_send(
        self, send: Send, request_id: str, source: str, warning: str | None
    ) -> Send:
        async def sender(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((self.header_bytes, request_id.encode("latin-1")))
                headers.append(
                    (b"x-request-id-source", source.encode("latin-1"))
                )
                if warning is not None:
                    headers.append(
                        (b"x-request-id-warning", warning.encode("latin-1"))
                    )
            await send(message)

        return sender


class _DummyLock:
    """单事件循环下集合操作本身原子；保留锁语义便于将来替换为跨线程锁。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class RequestIDLogFilter(logging.Filter):
    """给每条日志注入当前请求的 request_id / source / warning。

    取值来自 contextvars：在请求上下文外（如启动期日志）注入 "-"，
    在请求上下文内每个协程只看到自己的标识，并发压测下不会串号。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _request_id_var.get()
        record.request_id = rid or "-"
        record.request_id_source = _request_id_source_var.get() or "-"
        record.request_id_warning = _request_id_warning_var.get() or "-"
        return True
