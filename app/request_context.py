"""请求标识（request id）的生成、透传与并发下的重复检测。

契约：
- 每个 HTTP 响应都带 ``X-Request-ID``（头名可通过 Settings 配置）；
- 客户端未提供标识时，服务端生成 uuid4 标识，并通过
  ``X-Request-ID-Source: generated`` 明示“标识缺失被补全”；
- 客户端标识格式非法或与在途请求重复（并发串扰信号）时，**不静默复用**，
  替换为新生成的标识，响应头 ``X-Request-ID-Warning`` 给出原因
  （``missing`` / ``invalid`` / ``duplicate-in-flight``），并在最终标识已写入
  上下文后记录一条带 ``warning``/``rejected_value``/``final`` 的日志；
- 标识存于 contextvars，每个请求拥有独立上下文，协程并发下互不串扰；
- ``RequestIDLogFilter`` 把当前请求标识注入该请求产生的每条日志记录
  （``record.request_id``），使失败日志可凭客户端标识直接对账且并发不串号。
"""

import contextvars
import logging
import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.request_id")

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

# 允许的客户端标识：字母数字及 . _ : -，长度 1..128。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:=-]{1,128}$")

SOURCE_CLIENT = "client"
SOURCE_GENERATED = "generated"
WARNING_INVALID = "invalid"
WARNING_DUPLICATE = "duplicate-in-flight"
WARNING_MISSING = "missing"

# 上下文外（理论上不该出现在请求日志路径）时日志字段的占位值。
_NO_REQUEST_ID = "-"


class RequestIDLogFilter(logging.Filter):
    """给每条日志记录注入当前请求的 request_id 属性。

    标识取自 contextvars，因此并发协程各自渲染各自的标识，日志绝不会串到
    别的请求上；格式化时可用 ``%(request_id)s`` 直接对账。缺失/非法/重复
    等异常情况另外有显式告警日志（见 RequestIDMiddleware）。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = _request_id_var.get()
        record.request_id = request_id or _NO_REQUEST_ID  # type: ignore[attr-defined]
        return True


# 在本模块及错误处理器两个产出日志的 logger 上各挂一份（logger 的 filter
# 不沿继承链传递，必须挂到实际产日志的 logger），导入即生效、天然幂等。
_REQUEST_ID_LOG_FILTER = RequestIDLogFilter()
logger.addFilter(_REQUEST_ID_LOG_FILTER)
logging.getLogger("app.errors").addFilter(_REQUEST_ID_LOG_FILTER)


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
        # 被拒绝/替换的原始值，仅用于告警日志对账。
        rejected_value: str | None = None

        if raw is None:
            # 标识缺失：补全并明示，绝不留空。
            request_id = self._id_factory()
            source = SOURCE_GENERATED
            warning = WARNING_MISSING
        elif not is_valid_request_id(raw):
            rejected_value = raw
            request_id = self._id_factory()
            source = SOURCE_GENERATED
            warning = WARNING_INVALID
        else:
            request_id = raw

        # 在途集合登记（生成的标识也要保证唯一）。并发重复 -> 替换并告警。
        async with self._lock:
            if request_id in self._active:
                rejected_value = request_id
                request_id = self._id_factory()
                source = SOURCE_GENERATED
                warning = WARNING_DUPLICATE
            self._active.add(request_id)

        # 先把最终标识写入上下文，再发告警：保证告警记录上过滤器注入的
        # request_id 就是该请求最终采用的标识，可直接与客户端回传值对账。
        token = _request_id_var.set(request_id)
        if warning is not None:
            level = logging.INFO if warning == WARNING_MISSING else logging.WARNING
            if rejected_value is not None:
                logger.log(
                    level,
                    "request id warning=%s; rejected_value=%r; final=%s",
                    warning,
                    rejected_value,
                    request_id,
                )
            else:
                logger.log(
                    level,
                    "request id warning=%s; final=%s",
                    warning,
                    request_id,
                )
        try:
            await self.app(
                scope,
                receive,
                self._wrap_send(send, request_id, source, warning),
            )
        finally:
            async with self._lock:
                self._active.discard(request_id)
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
