"""并发请求：request_id 上下文互不串扰；在途重复标识必须被检测并替换。"""

import asyncio
import logging

import httpx
import pytest

from tests._log import log_case


@pytest.mark.anyio
async def test_concurrent_requests_context_isolation():
    # 直接用 ASGI transport 并发打同一应用，验证 contextvars 隔离。
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    request_ids = [f"trace-concurrent-{i}" for i in range(12)]

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        responses = await asyncio.gather(
            *[
                ac.get(
                    "/echo?delay_ms=50",
                    headers={"X-Request-ID": rid},
                )
                for rid in request_ids
            ]
        )

    pairs = [
        (
            request_ids[i],
            responses[i].headers["X-Request-ID"],
            responses[i].json()["request_id"],
        )
        for i in range(len(request_ids))
    ]
    log_case(
        "并发隔离",
        request_ids,
        code="OK" if all(a == b == c for a, b, c in pairs) else "MISMATCH",
        request_id=";".join(p[1] for p in pairs),
        status=200,
    )
    for sent, header_rid, body_rid in pairs:
        # 每个请求看到的必须是自己的标识：头、body 均一致且无串扰。
        assert header_rid == sent == body_rid


@pytest.mark.anyio
async def test_duplicate_inflight_request_id_is_rejected():
    """两个并发请求发送同一标识：后进入的必须被判重并替换，而非静默复用。"""
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        r1_task = asyncio.create_task(
            ac.get("/echo?delay_ms=120", headers={"X-Request-ID": "dup-id"})
        )
        await asyncio.sleep(0.03)  # 确保 r1 先登记在途集合
        r2 = await ac.get(
            "/echo?delay_ms=0", headers={"X-Request-ID": "dup-id"}
        )
        r1 = await r1_task

    log_case(
        "在途重复标识",
        ["dup-id", "dup-id"],
        code=f"r1={r1.headers['X-Request-ID']},r2={r2.headers['X-Request-ID']}",
        request_id=r2.headers["X-Request-ID"],
        status=r2.status_code,
    )
    # 先到的请求正常使用客户端标识。
    assert r1.headers["X-Request-ID"] == "dup-id"
    assert r1.headers["X-Request-ID-Source"] == "client"
    # 后到的重复标识被替换并显式告警，不静默复用。
    assert r2.headers["X-Request-ID"] != "dup-id"
    assert r2.headers["X-Request-ID-Source"] == "generated"
    assert r2.headers["X-Request-ID-Warning"] == "duplicate-in-flight"
    assert r2.json()["request_id"] == r2.headers["X-Request-ID"]


@pytest.mark.anyio
async def test_concurrent_failure_logs_do_not_cross_talk(caplog):
    """并发失败请求：每条失败日志只能带上自己那次请求的标识/分类/状态。"""
    from app.main import create_app

    app = create_app()
    # raise_app_exceptions=False：让 500 走真实的 Exception 处理器，
    # 与 TestClient(raise_server_exceptions=False) 保持一致。
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    request_ids = [f"trace-fail-{i}" for i in range(12)]

    with caplog.at_level(logging.INFO, logger="app.errors"):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            responses = await asyncio.gather(
                *[
                    ac.get(
                        # /boom 支持 delay_ms：制造真正重叠的在途 500 请求
                        f"/boom?delay_ms=50",
                        headers={"X-Request-ID": rid},
                    )
                    for rid in request_ids
                ]
            )

    pairs = list(zip(request_ids, responses))
    log_case(
        "并发失败日志隔离",
        request_ids,
        code=",".join(sorted({r.json()["code"] for _, r in pairs})),
        request_id=";".join(r.headers["X-Request-ID"] for _, r in pairs),
        status=500,
    )

    fail_records = [
        r for r in caplog.records if "request failed" in r.getMessage()
    ]
    # 每个请求恰好一条失败日志，且标识严格对应；不得串到别人身上。
    by_id: dict[str, list[logging.LogRecord]] = {}
    for rec in fail_records:
        by_id.setdefault(rec.request_id, []).append(rec)

    for sent, response in pairs:
        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_ERROR"
        assert response.headers["X-Request-ID"] == sent
        recs = by_id.get(sent, [])
        assert len(recs) == 1, f"标识 {sent} 的失败日志数量异常: {len(recs)}"
        rec = recs[0]
        assert f"category=INTERNAL_ERROR" in rec.getMessage()
        assert "status=500" in rec.getMessage()

    assert len(by_id) == len(request_ids), "并发日志标识出现串号/缺失"
