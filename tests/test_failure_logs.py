"""失败日志与请求标识对账：四类失败都能用客户端标识反查。

校验失败、业务拒绝、框架层拒绝、未处理异常的服务端日志必须包含：
- 同一次请求的 request_id（与响应头/响应体一致）；
- 最终分类 category；
- 实际返回状态 status。

标识缺失/非法/重复时日志也要能看出最终采用了哪个标识。
"""

import logging


def _failure_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == "app.errors"
        and "request failed" in r.getMessage()
    ]


def _assert_failure_log(record, request_id: str, category: str, status: int) -> None:
    message = record.getMessage()
    assert f"request_id={request_id}" in message
    assert f"category={category}" in message
    assert f"status={status}" in message
    # 过滤器还把 request_id 作为结构化属性注入，便于日志系统按字段对账。
    assert getattr(record, "request_id", None) == request_id


def test_validation_failure_log_carries_tracking_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.errors"):
        response = client.get("/items/x")
    body = response.json()
    records = _failure_records(caplog)
    assert len(records) == 1
    _assert_failure_log(records[0], body["request_id"], "VALIDATION_PATH", 422)


def test_business_rejection_log_carries_tracking_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.errors"):
        response = client.post("/business-reject")
    body = response.json()
    records = _failure_records(caplog)
    assert len(records) == 1
    _assert_failure_log(
        records[0], body["request_id"], "BUSINESS_RULE_VIOLATION", 409
    )


def test_framework_rejection_log_carries_tracking_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.errors"):
        r404 = client.get("/no-such-path")
        r405 = client.post("/items/1")
        r401 = client.get("/admin")
        r403 = client.get(
            "/admin", headers={"Authorization": "Bearer user-secret"}
        )
        r415 = client.post(
            "/users",
            content=b"x",
            headers={"Content-Type": "text/plain"},
        )
    expected = [
        (r404, "NOT_FOUND", 404),
        (r405, "METHOD_NOT_ALLOWED", 405),
        (r401, "UNAUTHORIZED", 401),
        (r403, "FORBIDDEN", 403),
        (r415, "UNSUPPORTED_MEDIA_TYPE", 415),
    ]
    records = _failure_records(caplog)
    assert len(records) == len(expected)
    for record, (response, category, status) in zip(records, expected):
        _assert_failure_log(
            record, response.json()["request_id"], category, status
        )


def test_unhandled_exception_log_carries_tracking_fields_and_stack(client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        response = client.get("/boom")
    body = response.json()
    records = [
        r
        for r in caplog.records
        if r.name == "app.errors" and r.exc_info
    ]
    assert len(records) == 1
    _assert_failure_log(records[0], body["request_id"], "INTERNAL_ERROR", 500)
    # 堆栈仍完整留在服务端，但对外不泄露（由 test_unhandled 保证响应体）。
    assert records[0].exc_info[0] is RuntimeError


def test_missing_request_id_is_visible_in_logs_with_generated_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request_id"):
        response = client.get("/")
    rid = response.headers["X-Request-ID"]
    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.request_id"
    ]
    flagged = [m for m in messages if "warning=missing" in m]
    assert flagged and rid in flagged[0]
    # 目标告警记录本身也被过滤器注入了当前（已生成的）标识。
    target = [
        r for r in caplog.records
        if r.name == "app.request_id" and "warning=missing" in r.getMessage()
    ]
    assert target and getattr(target[0], "request_id", None) == rid


def test_invalid_request_id_is_visible_in_logs(client, caplog):
    bad = "bad id!!"
    with caplog.at_level(logging.WARNING, logger="app.request_id"):
        response = client.get("/", headers={"X-Request-ID": bad})
    rid = response.headers["X-Request-ID"]
    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.request_id"
    ]
    flagged = [m for m in messages if "warning=invalid" in m]
    assert flagged
    assert bad in flagged[0] and rid in flagged[0]


def test_duplicate_request_id_is_visible_in_logs(client, caplog):
    """串行重放同一标识不属于在途重复；这里直接验证重复告警文案存在。

    并发重复场景由 test_concurrency 覆盖行为，此处仅验证一旦判重，日志里
    能同时看到原始值、warning 标签与替换后的标识。
    """
    # 直接驱动两次并发请求，制造在途重复。
    import asyncio

    import httpx

    async def scenario():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            t1 = asyncio.create_task(
                ac.get("/echo?delay_ms=80", headers={"X-Request-ID": "dup-log"})
            )
            await asyncio.sleep(0.02)
            t2 = await ac.get("/echo", headers={"X-Request-ID": "dup-log"})
            t1r = await t1
        return t1r, t2

    with caplog.at_level(logging.WARNING, logger="app.request_id"):
        r1, r2 = asyncio.run(scenario())

    messages = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.request_id"
    ]
    flagged = [m for m in messages if "warning=duplicate-in-flight" in m]
    assert flagged
    replaced = r2.headers["X-Request-ID"]
    assert replaced != "dup-log"
    assert "dup-log" in flagged[0] and replaced in flagged[0]
    assert r1.headers["X-Request-ID"] == "dup-log"
