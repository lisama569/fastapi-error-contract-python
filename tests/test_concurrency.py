"""并发请求下的请求标识隔离与重复/缺失检测测试。"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.factory import create_app
from tests.conftest import log_case

logger = logging.getLogger("tests")


def test_concurrent_requests_have_distinct_ids(client: TestClient) -> None:
    """N 个并发请求，响应体与响应头中的 request_id 必须一一对应、互不串扰。"""

    def one(i: int) -> tuple[str, str, int]:
        # 走错误契约路径（path 校验失败），响应体与响应头都带 request_id
        r = client.get("/demo/items/not-an-int")
        return r.headers["x-request-id"], r.json()["request_id"], r.status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(one, range(64)))

    ids = {rid for rid, body_rid, _ in results}
    logger.info("[concurrency] concurrent=%s distinct_ids=%s", 64, len(ids))
    assert len(ids) == 64  # 全部唯一
    for rid, body_rid, status in results:
        assert rid == body_rid
        assert status == 422  # path 校验失败，验证并发下错误上下文也不串扰


def _find_middleware(app):
    from app.middleware import RequestContextMiddleware

    node = getattr(app, "middleware_stack", None)
    while node is not None:
        if isinstance(node, RequestContextMiddleware):
            return node
        node = getattr(node, "app", None)
    return None


def test_duplicate_inflight_request_id_is_detected() -> None:
    """同一 X-Request-ID 在请求未结束时重复出现：必须检测到并重新生成，
    绝不静默复用。共享单个 TestClient（同一事件循环）制造真实在飞重叠。"""
    app = create_app()
    release = threading.Event()

    @app.get("/slow/{which}")
    async def slow(which: str) -> dict[str, str]:
        if which == "first":
            # 在事件循环内轮询线程事件，避免跨循环原语问题
            while not release.is_set():
                await asyncio.sleep(0.01)
        return {"which": which}

    client = TestClient(app, raise_server_exceptions=False)
    holder: dict[str, object] = {}

    def first() -> None:
        r = client.get("/slow/first", headers={"x-request-id": "dup-id-0001"})
        holder["first_rid"] = r.headers["x-request-id"]
        holder["first_source"] = r.headers["x-request-id-source"]

    def second() -> None:
        # 打一个错误契约路径，同时验证响应体与响应头的追踪标识一致
        r = client.get(
            "/demo/items/not-an-int", headers={"x-request-id": "dup-id-0001"}
        )
        holder["second_rid"] = r.headers["x-request-id"]
        holder["second_source"] = r.headers["x-request-id-source"]
        holder["second_body_rid"] = r.json()["request_id"]
        holder["second_error"] = r.json()["error"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(first)
        try:
            # 等待第一个请求真正进入并注册在飞 id
            mdw = None
            for _ in range(100):
                mdw = _find_middleware(app)
                if mdw is not None and "dup-id-0001" in mdw._inflight:
                    break
                time.sleep(0.02)
            assert mdw is not None and "dup-id-0001" in mdw._inflight, (
                "首个请求未进入在飞状态"
            )

            f2 = pool.submit(second)
            assert f2.result(timeout=5) is None
            assert holder["second_source"] == "replaced-duplicate"
        finally:
            release.set()
            f1.result(timeout=5)

    log_case(
        "duplicate",
        "/slow/first + error path, both id=dup-id-0001",
        _HolderResponse(holder),
    )
    assert holder["first_rid"] == "dup-id-0001"
    assert holder["first_source"] == "client"
    assert holder["second_source"] == "replaced-duplicate"
    assert holder["second_rid"] != "dup-id-0001"
    assert holder["second_rid"] == holder["second_body_rid"]
    assert holder["second_error"] == "validation_failed"


class _HolderResponse:
    """仅用于复用 log_case 的打印格式。"""

    def __init__(self, holder: dict[str, object]) -> None:
        self.status_code = 422
        self.headers = {"x-request-id-source": holder["second_source"]}
        self._body = {
            "error": holder["second_error"],
            "request_id": holder["second_rid"],
        }

    def json(self) -> dict[str, object]:
        return self._body


def test_repeated_request_ids_after_completion_are_allowed(client: TestClient) -> None:
    """请求结束后 id 离开在飞集合，同一标识可被后续请求正常复用。"""
    headers = {"x-request-id": "reuse-id-0001"}
    r1 = client.get("/", headers=headers)
    r2 = client.get("/", headers=headers)
    logger.info(
        "[reuse] id=%s sources=%s/%s",
        "reuse-id-0001",
        r1.headers["x-request-id-source"],
        r2.headers["x-request-id-source"],
    )
    assert r1.headers["x-request-id-source"] == "client"
    assert r2.headers["x-request-id-source"] == "client"
    assert r1.headers["x-request-id"] == r2.headers["x-request-id"] == "reuse-id-0001"
