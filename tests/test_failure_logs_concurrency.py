"""并发压测下的日志对账：每条失败日志只属于它自己的那次请求。

混合发起成功、校验失败、业务拒绝、框架层拒绝、未处理异常多类并发请求，
从日志中提取 (request_id, category, status)，必须与每个响应一一对应，
不允许出现串号（A 请求的日志带上 B 的标识/分类/状态）。
"""

import asyncio
import logging

import httpx
import pytest


def _parse_failure_log(message: str) -> tuple[str, str, int] | None:
    prefix = "request failed "
    if not message.startswith(prefix):
        return None
    # 固定前缀后依次是 request_id=<..> category=<..> status=<..>，
    # 其余部分（detail/堆栈）不参与解析。
    fields = dict(
        part.split("=", 1)
        for part in message[len(prefix):].split(" ", 3)[:3]
    )
    return fields["request_id"], fields["category"], int(fields["status"])


@pytest.mark.anyio
async def test_concurrent_failure_logs_never_cross_wires(caplog):
    from app.main import create_app

    app = create_app()

    class _SuppressReraisedServerErrors:
        """ServerErrorMiddleware 发出 500 响应后总会重抛异常（供服务器记录）；
        TestClient 用 raise_server_exceptions=False 吞掉，这里在并发直连场景
        下做同样处理，使 500 走与部署一致的响应路径而不中断 gather。
        包裹在最外层，不影响内部请求标识上下文。
        """

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            try:
                await self.inner(scope, receive, send)
            except RuntimeError as exc:
                if "intentional failure" not in str(exc):
                    raise

    transport = httpx.ASGITransport(app=_SuppressReraisedServerErrors(app))

    # 每类请求各若干个，全部带互不相同的客户端标识。
    plan: list[tuple[str, str, str, dict]] = []
    for i in range(6):
        rid = f"trace-ok-{i}"
        plan.append(("OK", "GET", f"/echo?delay_ms=20",
                     {"headers": {"X-Request-ID": rid}}))
    for i in range(6):
        rid = f"trace-validation-{i}"
        plan.append(("VALIDATION_QUERY", "GET", "/items/1?q=x",
                     {"headers": {"X-Request-ID": rid}}))
    for i in range(6):
        rid = f"trace-business-{i}"
        plan.append(("BUSINESS_RULE_VIOLATION", "POST", "/business-reject",
                     {"headers": {"X-Request-ID": rid}}))
    for i in range(6):
        rid = f"trace-biz-missing-{i}"
        # 业务侧“数据没查到”：与框架路由 404 是不同分类。
        plan.append(("NOT_FOUND", "GET", "/users/999",
                     {"headers": {"X-Request-ID": rid}}))
    for i in range(6):
        rid = f"trace-route-missing-{i}"
        # 框架层“地址写错”：默认 404、分类 ROUTE_NOT_FOUND。
        plan.append(("ROUTE_NOT_FOUND", "GET", "/no-such-path",
                     {"headers": {"X-Request-ID": rid}}))
    for i in range(6):
        rid = f"trace-framework-{i}"
        plan.append(("METHOD_NOT_ALLOWED", "POST", "/items/1",
                     {"headers": {"X-Request-ID": rid}}))
    for i in range(6):
        rid = f"trace-other-4xx-{i}"
        # 非具名框架 4xx：413 透传，分类 CLIENT_ERROR。
        plan.append((
            "CLIENT_ERROR", "POST", "/too-large",
            {
                "headers": {"X-Request-ID": rid, "Content-Length": "101"},
                "content": b"x" * 101,
            },
        ))
    for i in range(6):
        rid = f"trace-boom-{i}"
        plan.append(("INTERNAL_ERROR", "GET", "/boom",
                     {"headers": {"X-Request-ID": rid}}))

    caplog.set_level(logging.INFO, logger="app.errors")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        responses = await asyncio.gather(
            *[ac.request(method, url, **kw) for _, method, url, kw in plan]
        )

    # 响应侧期望：request_id -> (category, status)。
    expected: dict[str, tuple[str, int]] = {}
    for (expected_category, _, _, kwargs), response in zip(plan, responses):
        rid = kwargs["headers"]["X-Request-ID"]
        if expected_category == "OK":
            assert response.status_code == 200
            assert response.json()["request_id"] == rid
            continue
        body = response.json()
        assert body["request_id"] == rid  # 响应头/体标识不串
        assert body["code"] == expected_category
        expected[rid] = (expected_category, response.status_code)

    # 日志侧：每个失败 request_id 恰好一条失败日志，且分类/状态与响应一致。
    parsed = [
        parsed
        for record in caplog.records
        if record.name == "app.errors"
        for parsed in [_parse_failure_log(record.getMessage())]
        if parsed is not None
    ]
    logged: dict[str, tuple[str, int]] = {}
    for rid, category, status in parsed:
        # 同一标识出现两条不同分类日志即说明发生串号。
        assert rid not in logged or logged[rid] == (category, status)
        logged[rid] = (category, status)

    assert set(logged) == set(expected)
    for rid, pair in expected.items():
        assert logged[rid] == pair, f"请求 {rid} 日志与响应不一致"

    # 结构化字段同样必须等于该日志自己的标识。
    for record in caplog.records:
        if record.name == "app.errors" and record.getMessage().startswith("request failed "):
            parsed_msg = _parse_failure_log(record.getMessage())
            assert parsed_msg is not None
            assert record.request_id == parsed_msg[0]
