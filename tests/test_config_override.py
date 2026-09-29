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


def _app_client(settings):
    app = create_app(settings)
    return TestClient(app, raise_server_exceptions=False)


def test_route_not_found_override_isolates_only_itself(clean_settings_cache):
    """覆盖 ROUTE_NOT_FOUND：只影响“地址不存在”，其它分类真实状态不变。"""
    settings = build_settings({ErrorCategory.ROUTE_NOT_FOUND: 400})
    with _app_client(settings) as c:
        route_miss = c.get("/no-such-path")
        business_miss = c.get("/users/999999")
        m405 = c.post("/items/1")
        m401 = c.get("/admin")
        m415 = c.post(
            "/users", content=b"x", headers={"Content-Type": "text/plain"}
        )
        m413 = c.post("/users", json={"name": "x" * (1024 * 1024), "age": 1})
        meta = c.get("/meta/error-contract").json()
    log_case("覆盖 ROUTE_NOT_FOUND=400", {"ROUTE_NOT_FOUND": 400},
             code=route_miss.json()["code"],
             request_id=route_miss.json()["request_id"],
             status=route_miss.status_code)
    # 被覆盖的一类：状态变为覆盖值，分类不变。
    assert route_miss.status_code == 400
    assert route_miss.json()["code"] == "ROUTE_NOT_FOUND"
    # 业务 NOT_FOUND 与框架层其它分类：真实状态全部不受影响。
    assert business_miss.status_code == 404
    assert business_miss.json()["code"] == "NOT_FOUND"
    assert (m405.status_code, m405.json()["code"]) == (405, "METHOD_NOT_ALLOWED")
    assert (m401.status_code, m401.json()["code"]) == (401, "UNAUTHORIZED")
    assert (m415.status_code, m415.json()["code"]) == (415, "UNSUPPORTED_MEDIA_TYPE")
    assert (m413.status_code, m413.json()["code"]) == (413, "CLIENT_ERROR")
    indexed = {x["code"]: x for x in meta["categories"]}
    assert indexed["ROUTE_NOT_FOUND"]["effective_status"] == 400
    assert indexed["ROUTE_NOT_FOUND"]["overridden"] is True
    assert indexed["NOT_FOUND"]["overridden"] is False


def test_business_not_found_override_does_not_leak_to_framework(clean_settings_cache):
    """把业务 NOT_FOUND 改成 400：框架层各类 4xx 真实状态不被套走。"""
    settings = build_settings({ErrorCategory.NOT_FOUND: 400})
    with _app_client(settings) as c:
        business_miss = c.get("/users/999999")
        route_miss = c.get("/no-such-path")
        m405 = c.post("/items/1")
        m413 = c.post("/users", json={"name": "x" * (1024 * 1024), "age": 1})
    log_case("覆盖业务 NOT_FOUND=400", {"NOT_FOUND": 400},
             code=route_miss.json()["code"],
             request_id=route_miss.json()["request_id"],
             status=route_miss.status_code)
    assert business_miss.status_code == 400
    assert business_miss.json()["code"] == "NOT_FOUND"
    # 框架层“地址不存在”保持自己的分类与真实 404。
    assert route_miss.status_code == 404
    assert route_miss.json()["code"] == "ROUTE_NOT_FOUND"
    # 其它框架层 4xx 也不被套成“资源不存在”的状态。
    assert m405.status_code == 405
    assert m413.status_code == 413


def test_client_error_override_only_affects_fallback_bucket(clean_settings_cache):
    """覆盖兜底 CLIENT_ERROR：只影响兜底桶，且不影响有专属分类的框架层状态。"""
    settings = build_settings({ErrorCategory.CLIENT_ERROR: 422})
    with _app_client(settings) as c:
        m413 = c.post("/users", json={"name": "x" * (1024 * 1024), "age": 1})
        route_miss = c.get("/no-such-path")
        m405 = c.post("/items/1")
    log_case("覆盖 CLIENT_ERROR=422", {"CLIENT_ERROR": 422},
             code=m413.json()["code"],
             request_id=m413.json()["request_id"],
             status=m413.status_code)
    # 兜底桶按覆盖值返回。
    assert m413.status_code == 422
    assert m413.json()["code"] == "CLIENT_ERROR"
    # 有专属分类的框架层拒绝保持真实状态。
    assert route_miss.status_code == 404
    assert m405.status_code == 405


def test_untouched_framework_categories_keep_real_status(clean_settings_cache):
    """默认配置下，框架层各分类对外状态即框架真实状态。"""
    settings = build_settings()
    with _app_client(settings) as c:
        cases = [
            (c.get("/no-such-path"), 404, "ROUTE_NOT_FOUND"),
            (c.post("/items/1"), 405, "METHOD_NOT_ALLOWED"),
            (c.get("/admin"), 401, "UNAUTHORIZED"),
            (
                c.get("/admin", headers={"Authorization": "Bearer user-secret"}),
                403,
                "FORBIDDEN",
            ),
            (
                c.post("/users", content=b"x",
                       headers={"Content-Type": "text/plain"}),
                415,
                "UNSUPPORTED_MEDIA_TYPE",
            ),
            (
                c.post("/users", json={"name": "x" * (1024 * 1024), "age": 1}),
                413,
                "CLIENT_ERROR",
            ),
        ]
    for response, status, code in cases:
        assert response.status_code == status
        assert response.json()["code"] == code
