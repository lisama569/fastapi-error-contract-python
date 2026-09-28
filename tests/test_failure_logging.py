"""失败日志与请求标识对账：每条失败日志带 request_id/category/status。"""

import logging

import pytest

from tests._log import log_case


@pytest.fixture
def failure_records(caplog):
    """采集 app.errors / app.request_id 两个 logger 的全部记录。"""
    with caplog.at_level(logging.DEBUG, logger="app.errors"):
        with caplog.at_level(logging.DEBUG, logger="app.request_id"):
            yield caplog


def _failure_logs(caplog):
    return [r for r in caplog.records if "request failed" in r.getMessage()]


@pytest.mark.parametrize(
    "case,method,url,kwargs,expected_code,expected_status",
    [
        ("校验失败", "GET", "/items/x", {}, "VALIDATION_PATH", 422),
        ("业务拒绝", "POST", "/business-reject", {},
         "BUSINESS_RULE_VIOLATION", 409),
        ("框架404", "GET", "/missing-x", {}, "ROUTE_NOT_FOUND", 404),
        ("框架405", "PUT", "/", {}, "METHOD_NOT_ALLOWED", 405),
        ("框架401", "GET", "/admin", {}, "UNAUTHENTICATED", 401),
        ("框架403", "GET", "/admin",
         {"headers": {"X-User-Role": "guest"}}, "ACCESS_DENIED", 403),
        ("框架415", "POST", "/users",
         {"content": b"{}", "headers": {"content-type": "text/plain"}},
         "UNSUPPORTED_MEDIA_TYPE", 415),
        ("未处理异常", "GET", "/boom", {}, "INTERNAL_ERROR", 500),
    ],
)
def test_every_failure_log_carries_id_category_status(
    client, failure_records, case, method, url, kwargs,
    expected_code, expected_status
):
    response = client.request(method, url, **kwargs)
    body = response.json()
    rid = body["request_id"]
    log_case(f"日志对账-{case}", {"method": method, "url": url},
             code=body.get("code"), request_id=rid,
             status=response.status_code)

    # 结构化属性必须存在（由 RequestIDLogFilter 注入），且等于本次请求标识。
    matched = [
        r for r in _failure_logs(failure_records)
        if getattr(r, "request_id", None) == rid
    ]
    assert matched, f"未找到 request_id={rid} 的失败日志"
    assert len(matched) == 1, "同一请求只应落一条统一失败日志"
    rec = matched[0]
    assert rec.request_id == rid
    assert rec.request_id_source in ("client", "generated")
    assert f"category={expected_code}" in rec.getMessage()
    assert f"status={expected_status}" in rec.getMessage()
    # 未处理异常还必须有带堆栈的 ERROR 记录，且同样携带 request_id。
    if expected_code == "INTERNAL_ERROR":
        error_records = [
            r for r in failure_records.records
            if r.exc_info and r.name == "app.errors"
        ]
        assert error_records and all(
            r.request_id == rid for r in error_records
        )


def test_missing_request_id_is_visible_in_logs(client, failure_records):
    """标识缺失：生成的标识同时出现在补全日志与失败日志中，可对账。"""
    response = client.get("/missing-y")
    rid = response.headers["X-Request-ID"]
    log_case("日志-标识缺失", None, code="ROUTE_NOT_FOUND",
             request_id=rid, status=404)
    assert response.headers["X-Request-ID-Warning"] == "missing"
    messages = [r.getMessage() for r in failure_records.records]
    assert any("request id missing" in m and rid in m for m in messages), messages
    # 补全日志上的上下文字段显式标明“缺失补全”。
    missing_logs = [
        r for r in failure_records.records
        if "request id missing" in r.getMessage()
    ]
    assert missing_logs and all(
        r.request_id == rid
        and r.request_id_source == "generated"
        and r.request_id_warning == "missing"
        for r in missing_logs
    )
    fail = _failure_logs(failure_records)
    assert any(r.request_id == rid for r in fail)


def test_invalid_request_id_is_visible_in_logs(client, failure_records):
    """非法标识：日志能看到原始非法值与替换后的新标识。"""
    bad = "bad id!!"
    response = client.get("/", headers={"X-Request-ID": bad})
    rid = response.headers["X-Request-ID"]
    log_case("日志-标识非法", bad, code=None, request_id=rid, status=200)
    messages = [r.getMessage() for r in failure_records.records]
    assert any("malformed request id" in m and bad in m and rid in m
               for m in messages), messages
    invalid_logs = [
        r for r in failure_records.records
        if "malformed request id" in r.getMessage()
    ]
    assert invalid_logs and all(
        r.request_id == rid
        and r.request_id_source == "generated"
        and r.request_id_warning == "invalid"
        for r in invalid_logs
    )


@pytest.mark.anyio
async def test_duplicate_request_id_is_visible_in_logs(failure_records):
    """重复标识：后到请求的告警日志携带替换后的新标识。"""
    import asyncio

    import httpx

    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        t1 = asyncio.create_task(
            ac.get("/echo?delay_ms=120",
                   headers={"X-Request-ID": "dup-log"})
        )
        await asyncio.sleep(0.03)
        t2 = await ac.get(
            "/echo?delay_ms=0",
            headers={"X-Request-ID": "dup-log"},
        )
        t1_resp = await t1

    log_case("日志-重复标识", ["dup-log", "dup-log"],
             code=f"r1={t1_resp.headers['X-Request-ID']},"
                  f"r2={t2.headers['X-Request-ID']}",
             request_id=t2.headers["X-Request-ID"], status=t2.status_code)
    messages = [r.getMessage() for r in failure_records.records]
    replaced = t2.headers["X-Request-ID"]
    assert any("duplicate in-flight" in m and replaced in m for m in messages), \
        messages
    dup_logs = [
        r for r in failure_records.records
        if "duplicate in-flight" in r.getMessage()
    ]
    assert dup_logs and all(
        r.request_id == replaced
        and r.request_id_source == "generated"
        and r.request_id_warning == "duplicate-in-flight"
        for r in dup_logs
    )
    # 先到请求的成功响应日志上下文中不应有任何告警标记。
    assert t1_resp.headers["X-Request-ID-Source"] == "client"
