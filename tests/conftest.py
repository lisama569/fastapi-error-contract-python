"""共享 pytest fixtures：每测试一个隔离应用实例 + TestClient。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    from app.config import DEFAULT_STATUS_MAP

    return Settings(status_map=dict(DEFAULT_STATUS_MAP))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    # raise_server_exceptions=False：让 500 走真实的 Exception 处理器。
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def anyio_backend() -> str:
    """anyio pytest 插件使用 asyncio 后端。"""
    return "asyncio"
