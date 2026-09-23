"""pytest 公共夹具与日志辅助。

要求单测日志中打印：输入、分类结果、追踪标识。
通过 `pytest -o log_cli=true` 可在控制台直接看到这些记录。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import ErrorContractSettings
from app.factory import create_app

logger = logging.getLogger("tests")


@pytest.fixture()
def settings() -> ErrorContractSettings:
    return ErrorContractSettings(env={})


@pytest.fixture()
def client(settings: ErrorContractSettings) -> TestClient:
    app = create_app(settings)
    # 不向上抛出服务端异常，使其走统一 500 契约
    return TestClient(app, raise_server_exceptions=False)


def log_case(name: str, url: str, response) -> None:
    """统一打印每个用例的输入、分类结果与追踪标识。"""
    body = response.json()
    logger.info(
        "[%s] input=%s status=%s category=%s request_id=%s source=%s",
        name,
        url,
        response.status_code,
        body.get("error"),
        body.get("request_id"),
        response.headers.get("x-request-id-source"),
    )
