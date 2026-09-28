"""新增框架层分类的配置覆盖：沿用 ERROR_STATUS__<分类> 方式，优先级明确。"""

import pytest
from fastapi.testclient import TestClient

from app.config import (
    build_settings,
    reset_settings,
)
from app.errors.codes import ErrorCategory
from app.main import create_app
from tests._log import log_case


@pytest.fixture
def clean_settings_cache():
    reset_settings()
    yield
    reset_settings()


def test_env_override_applies_to_framework_category(monkeypatch, clean_settings_cache):
    # 环境变量即可覆盖新分类默认状态，分类名保持稳定。
    monkeypatch.setenv("ERROR_STATUS__FORBIDDEN", "451")
    settings = build_settings()
    assert settings.status_for(ErrorCategory.FORBIDDEN) == 451
    assert settings.is_overridden(ErrorCategory.FORBIDDEN)

    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get(
            "/admin", headers={"Authorization": "Bearer user-secret"}
        )
        meta = c.get("/meta/error-contract").json()
    body = response.json()
    log_case("环境变量覆盖 FORBIDDEN", {"ERROR_STATUS__FORBIDDEN": 451},
             code=body["code"], request_id=body["request_id"],
             status=response.status_code)
    assert response.status_code == 451  # 真实返回状态被改成覆盖值
    assert body["code"] == "FORBIDDEN"  # 分类不变，旧客户端解析不受影响
    indexed = {x["code"]: x for x in meta["categories"]}
    assert indexed["FORBIDDEN"]["effective_status"] == 451
    assert indexed["FORBIDDEN"]["overridden"] is True


def test_explicit_argument_takes_precedence_over_env(monkeypatch, clean_settings_cache):
    """显式 Settings 入参优先级高于环境变量，环境变量高于内置默认。"""
    monkeypatch.setenv("ERROR_STATUS__METHOD_NOT_ALLOWED", "400")
    settings = build_settings({ErrorCategory.METHOD_NOT_ALLOWED: 422})
    assert settings.status_for(ErrorCategory.METHOD_NOT_ALLOWED) == 422

    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.post("/items/1")
    assert response.status_code == 422
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize("bad_value", ["399", "abc", "600"])
def test_invalid_override_for_new_category_fails_fast(
    monkeypatch, clean_settings_cache, bad_value
):
    monkeypatch.setenv("ERROR_STATUS__UNAUTHORIZED", bad_value)
    with pytest.raises(ValueError):
        build_settings()


def test_unknown_category_env_fails_fast(monkeypatch, clean_settings_cache):
    monkeypatch.setenv("ERROR_STATUS__NOT_A_CATEGORY", "400")
    with pytest.raises(ValueError):
        build_settings()
