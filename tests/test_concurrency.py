"""并发请求：request_id 上下文互不串扰；在途重复标识必须被检测并替换。"""

import asyncio

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
