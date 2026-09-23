"""请求标识中间件：读取/生成 request id，检测缺失、畸形与并发重复。

契约：
- 入站 X-Request-ID 合法且不与在飞请求重复 -> 原样使用；
- 缺失 -> 服务端生成 UUID4；
- 畸形（不符合 [A-Za-z0-9._-]{8,128}）-> 拒绝原值、重新生成；
- 与某个在飞请求重复 -> 绝不复用，重新生成；
  以上判定均通过响应头 X-Request-ID-Source 与日志暴露，绝不静默。
- 响应始终回传最终生效的 X-Request-ID。
基于 contextvars，每个请求（含并发）拥有独立上下文。

未处理异常也在此层捕获（Starlette 的兜底错误中间件位于本中间件之外，
拿不到请求上下文），以保证 500 响应同样携带 request_id 与追踪响应头，
且不泄露任何内部细节。
"""
from __future__ import annotations

import logging
import re
import uuid
from asyncio import Lock
from collections.abc import MutableSet

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import ErrorContractSettings
from app.handlers import build_unhandled_response
from app.request_context import reset_request_id, set_request_id

logger = logging.getLogger("app.request_id")

REQUEST_ID_HEADER = "x-request-id"
SOURCE_HEADER = "x-request-id-source"
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

SOURCE_CLIENT = "client"
SOURCE_GENERATED = "generated-missing"
SOURCE_MALFORMED = "replaced-malformed"
SOURCE_DUPLICATE = "replaced-duplicate"


def _new_id() -> str:
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        settings: ErrorContractSettings | None = None,
        *,
        inflight: MutableSet[str] | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self._inflight: MutableSet[str] = inflight if inflight is not None else set()
        self._lock = Lock()

    async def _resolve_id(self, raw: str | None) -> tuple[str, str]:
        """返回 (最终 request_id, 来源判定)。"""
        if raw is None:
            return _new_id(), SOURCE_GENERATED
        if not _VALID_ID.fullmatch(raw):
            logger.warning(
                "malformed request id rejected: %r",
                raw,
                extra={"event": "request_id_malformed"},
            )
            return _new_id(), SOURCE_MALFORMED
        async with self._lock:
            if raw in self._inflight:
                logger.warning(
                    "duplicate in-flight request id rejected: %r",
                    raw,
                    extra={"event": "request_id_duplicate"},
                )
                return _new_id(), SOURCE_DUPLICATE
            self._inflight.add(raw)
        return raw, SOURCE_CLIENT

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw = headers.get(REQUEST_ID_HEADER)
        request_id, source = await self._resolve_id(raw)
        registered = source == SOURCE_CLIENT

        token = set_request_id(request_id)
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        logger.info(
            "request start",
            extra={
                "event": "request_start",
                "request_id_input": raw,
                "request_id": request_id,
                "request_id_source": source,
            },
        )

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                mutable = MutableHeaders(scope=message)
                mutable[REQUEST_ID_HEADER] = request_id
                mutable[SOURCE_HEADER] = source
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if response_started:
                # 响应头已发出，无法更换为统一 JSON；记录日志后让上游关闭连接
                logger.exception(
                    "exception after response started request_id=%s", request_id
                )
                raise
            if self.settings is None:
                raise
            response = build_unhandled_response(request_id, self.settings)
            # 异常处理器内部已记录堆栈；补充请求来源信息
            await response(scope, receive, send_wrapper)
        finally:
            reset_request_id(token)
            if registered:
                async with self._lock:
                    self._inflight.discard(request_id)
            logger.info(
                "request end",
                extra={"event": "request_end", "request_id": request_id},
            )


# 便于处理器/其他中间件直接读取的 helper
def get_scope_request_id(scope: Scope) -> str | None:
    state = scope.get("state")
    if isinstance(state, dict):
        return state.get("request_id")
    return getattr(state, "request_id", None)
