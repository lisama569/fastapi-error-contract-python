"""框架层客户端拒绝：尚未进业务代码即被挡下的请求也要进统一契约。

覆盖：404 路由不存在 / 405 方法不允许 / 401 未认证 / 403 访问被拒绝 /
415 媒体类型不支持；要求分类彼此可区分、真实客户端错误状态被保留，
且能沿用 ERROR_STATUS__ 配置方式单独覆盖。
"""

import pytest
from fastapi.testclient import TestClient

from app.config import build_settings
from app.errors.codes import ErrorCategory
from app.main import create_app
from tests._log import log_case

ENVELOPE_KEYS = {"error", "code", "message", "request_id", "details"}


@pytest.mark.parametrize(
    "case,method,url,kwargs,expected_code,expected_status",
    [
        ("地址不存在", "GET", "/definitely-missing", {}, "ROUTE_NOT_FOUND", 404),
        ("方法不允许", "DELETE", "/", {}, "METHOD_NOT_ALLOWED", 405),
        ("缺少身份凭据", "GET", "/admin", {}, "UNAUTHENTICATED", 401),
        ("访问被拒绝", "GET", "/admin",
         {"headers": {"X-User-Role": "guest"}}, "ACCESS_DENIED", 403),
        ("媒体类型不支持", "POST", "/users",
         {"content": b"{}", "headers": {"content-type": "text/plain"}},
         "UNSUPPORTED_MEDIA_TYPE", 415),
    ],
)
def test_framework_rejections_classified_with_real_status(
    client, case, method, url, kwargs, expected_code, expected_status
):
    response = client.request(method, url, **kwargs)
    body = response.json()
    log_case(case, {"method": method, "url": url, **kwargs},
             code=body.get("code"), request_id=body.get("request_id"),
             status=response.status_code)
    # 真实客户端错误状态必须保留。
    assert response.status_code == expected_status
    # 统一信封字段齐全，分类彼此可区分。
    assert ENVELOPE_KEYS <= set(body)
    assert body["error"] == "error"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]
    assert body["details"] is None
    assert isinstance(body["request_id"], str) and body["request_id"]
    # 响应头标识与信封一致（框架层拒绝也经过 RequestID 中间件）。
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_framework_categories_are_distinguishable(client):
    """五类框架层拒绝的 code 必须两两不同。"""
    results = {
        "ROUTE_NOT_FOUND": client.get("/definitely-missing").json()["code"],
        "METHOD_NOT_ALLOWED": client.delete("/").json()["code"],
        "UNAUTHENTICATED": client.get("/admin").json()["code"],
        "ACCESS_DENIED": client.get(
            "/admin", headers={"X-User-Role": "guest"}
        ).json()["code"],
        "UNSUPPORTED_MEDIA_TYPE": client.post(
            "/users", content=b"{}",
            headers={"content-type": "text/plain"},
        ).json()["code"],
    }
    log_case("框架分类可区分", list(results),
             code=",".join(sorted(set(results.values()))), status=None)
    assert set(results.values()) == set(results) == {
        "ROUTE_NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "UNAUTHENTICATED",
        "ACCESS_DENIED",
        "UNSUPPORTED_MEDIA_TYPE",
    }


def test_framework_rejection_does_not_enter_business(client):
    """415 必须在请求体解析/业务代码之前挡下：非法 JSON 也应得到 415 而非 422。"""
    response = client.post(
        "/users",
        content=b"this is not json at all",
        headers={"content-type": "text/plain"},
    )
    body = response.json()
    log_case("415 先于 body 校验", b"this is not json at all",
             code=body.get("code"), request_id=body.get("request_id"),
             status=response.status_code)
    assert response.status_code == 415
    assert body["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_guard_passes_for_legitimate_request(client):
    """框架守卫不得影响正常请求：admin 角色与 JSON 请求体照常成功。"""
    response = client.get("/admin", headers={"X-User-Role": "admin"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scope": "admin"}
    response = client.post(
        "/users", json={"name": "ada", "age": 36}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "ada"


@pytest.mark.parametrize(
    "category,status,method,url,kwargs",
    [
        (ErrorCategory.METHOD_NOT_ALLOWED, 404, "DELETE", "/", {}),
        (ErrorCategory.ACCESS_DENIED, 451, "GET", "/admin",
         {"headers": {"X-User-Role": "guest"}}),
        (ErrorCategory.UNSUPPORTED_MEDIA_TYPE, 400, "POST", "/users",
         {"content": b"{}", "headers": {"content-type": "text/plain"}}),
    ],
)
def test_framework_status_remappable_via_config(
    category, status, method, url, kwargs
):
    """框架层分类沿用现有配置方式单独覆盖：code 不变，状态变为配置值。"""
    settings = build_settings({category: status})
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.request(method, url, **kwargs)
    body = response.json()
    log_case(f"框架状态覆盖 {category.value}->{status}",
             {"method": method, "url": url},
             code=body.get("code"), request_id=body.get("request_id"),
             status=response.status_code)
    assert body["code"] == category.value
    assert response.status_code == status


def test_unmapped_framework_status_keeps_legacy_fallback(client):
    """未显式映射的 4xx 框架异常沿用旧兜底：NOT_FOUND 分类。"""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from fastapi import APIRouter

    extra = APIRouter()

    @extra.get("/teapot")
    def _teapot() -> None:
        raise StarletteHTTPException(status_code=418, detail="teapot")

    client.app.include_router(extra)
    response = client.get("/teapot")
    body = response.json()
    log_case("未映射 4xx 兜底", "/teapot", code=body.get("code"),
             request_id=body.get("request_id"), status=response.status_code)
    assert body["code"] == "NOT_FOUND"
    assert response.status_code == client.app.state.settings.status_for(
        ErrorCategory.NOT_FOUND
    )


def test_framework_status_remappable_via_env(monkeypatch):
    """环境变量 ERROR_STATUS__<分类> 也能覆盖框架层分类（现有配置方式）。"""
    from app.config import reset_settings

    monkeypatch.setenv("ERROR_STATUS__UNSUPPORTED_MEDIA_TYPE", "400")
    reset_settings()
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.post(
                "/users", content=b"{}",
                headers={"content-type": "text/plain"},
            )
        body = response.json()
        log_case("框架状态环境变量覆盖",
                 {"ERROR_STATUS__UNSUPPORTED_MEDIA_TYPE": 400},
                 code=body.get("code"), request_id=body.get("request_id"),
                 status=response.status_code)
        assert body["code"] == "UNSUPPORTED_MEDIA_TYPE"
        assert response.status_code == 400
    finally:
        reset_settings()
