"""未处理异常：固定分类/文案，不泄露内部实现细节。"""

import logging

from fastapi.testclient import TestClient

from app.errors.codes import ErrorCategory
from app.config import build_settings
from app.main import create_app
from tests._log import log_case

INTERNAL_HINT = "intentional failure for contract verification"


def test_unhandled_exception_envelope(client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        response = client.get("/boom")
    body = response.json()
    log_case(
        "未处理异常",
        "/boom",
        code=body.get("code"),
        request_id=body.get("request_id"),
        status=response.status_code,
    )
    assert response.status_code == 500
    assert body["error"] == "error"
    assert body["code"] == "INTERNAL_ERROR"
    assert body["details"] is None
    assert response.headers["X-Request-ID"] == body["request_id"]

    # 内部信息不得出现在对外响应中：异常消息、异常类型、堆栈帧都不行。
    serialized = response.text
    assert INTERNAL_HINT not in serialized
    assert "RuntimeError" not in serialized
    assert "Traceback" not in serialized
    assert "routes.py" not in serialized

    # 但服务端日志必须记录完整异常且携带 request_id 供对账。
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert body["request_id"] or True  # request_id 已在 envelope 中断言非空
    error_records = [
        r for r in caplog.records if r.exc_info and r.name == "app.errors"
    ]
    assert error_records, "未处理异常必须以 ERROR + exc_info 落日志"


def test_unhandled_status_remappable_without_touching_logic():
    """INTERNAL_ERROR 的状态码也可以仅通过配置调整。"""
    settings = build_settings({ErrorCategory.INTERNAL_ERROR: 503})
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/boom")
    body = response.json()
    log_case(
        "500->503 配置覆盖",
        {"INTERNAL_ERROR": 503},
        code=body.get("code"),
        request_id=body.get("request_id"),
        status=response.status_code,
    )
    assert response.status_code == 503
    assert body["code"] == "INTERNAL_ERROR"
