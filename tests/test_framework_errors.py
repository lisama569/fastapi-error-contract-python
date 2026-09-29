"""框架层客户端错误（未进入业务代码即被挡下）：分类可区分、真实状态保留。

覆盖：路由不存在 404（ROUTE_NOT_FOUND）、方法不允许 405、未认证 401、
访问被拒 403、请求体格式不支持 415、其它框架 4xx（413 兜底 CLIENT_ERROR）。
状态码默认必须保留框架给出的真实值，分类稳定不变，且与业务侧 NOT_FOUND
是两个互相独立的分类。
"""

import pytest

from tests._log import log_case

ENVELOPE_KEYS = {"error", "code", "message", "request_id", "details"}


def _assert_envelope(response, expected_code: str, expected_status: int) -> dict:
    body = response.json()
    assert response.status_code == expected_status
    assert ENVELOPE_KEYS <= set(body)
    assert body["error"] == "error"
    assert body["code"] == expected_code
    assert isinstance(body["request_id"], str) and body["request_id"]
    # 框架层拒绝不产生逐字段定位。
    assert body["details"] is None
    # 响应头与响应体标识一致。
    assert response.headers["X-Request-ID"] == body["request_id"]
    return body


def test_route_not_found_keeps_route_category_and_404(client):
    """地址写错被框架挡下：独立分类 ROUTE_NOT_FOUND，默认仍是真实 404。"""
    response = client.get("/no-such-path")
    body = _assert_envelope(response, "ROUTE_NOT_FOUND", 404)
    log_case("路由不存在", "/no-such-path", code=body["code"],
             request_id=body["request_id"], status=404)


def test_route_not_found_distinct_from_business_not_found(client):
    """框架“地址不存在”与业务“数据没查到”必须是两个可区分的分类。"""
    framework_body = client.get("/no-such-path").json()
    business_body = client.get("/users/999").json()
    log_case(
        "框架404 vs 业务404",
        {"route": "/no-such-path", "business": "/users/999"},
        code=f"{framework_body['code']} vs {business_body['code']}",
        request_id=(
            f"{framework_body['request_id']};{business_body['request_id']}"
        ),
        status=404,
    )
    assert framework_body["code"] == "ROUTE_NOT_FOUND"
    assert business_body["code"] == "NOT_FOUND"
    assert framework_body["code"] != business_body["code"]
    # 两类默认都是 404，但分类必须足以让调用方区分原因。
    assert client.get("/no-such-path").status_code == 404
    assert client.get("/users/999").status_code == 404


def test_business_not_found_is_not_affected_by_route_category():
    """业务 NOT_FOUND 改配置只改自己，不波及其它分类。"""
    from fastapi.testclient import TestClient

    from app.config import build_settings
    from app.errors.codes import ErrorCategory
    from app.main import create_app

    settings = build_settings({ErrorCategory.NOT_FOUND: 400})
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        business = c.get("/users/999")
        route_miss = c.get("/no-such-path")
        method_not_allowed = c.post("/items/1")
        meta = c.get("/meta/error-contract").json()
    log_case(
        "业务NOT_FOUND->400 不影响框架分类",
        {"NOT_FOUND": 400},
        code=(
            f"biz={business.json()['code']} "
            f"route={route_miss.json()['code']} "
            f"method={method_not_allowed.json()['code']}"
        ),
        request_id=(
            f"{business.json()['request_id']};"
            f"{route_miss.json()['request_id']}"
        ),
        status=400,
    )
    # 被改的业务分类：状态变、分类名不变（老客户端照旧解析）。
    assert business.status_code == 400
    assert business.json()["code"] == "NOT_FOUND"
    # 框架层各类真实状态与分类都不受影响。
    assert route_miss.status_code == 404
    assert route_miss.json()["code"] == "ROUTE_NOT_FOUND"
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.json()["code"] == "METHOD_NOT_ALLOWED"
    indexed = {x["code"]: x for x in meta["categories"]}
    assert indexed["NOT_FOUND"]["effective_status"] == 400
    assert indexed["NOT_FOUND"]["overridden"] is True
    assert indexed["ROUTE_NOT_FOUND"]["effective_status"] == 404
    assert indexed["ROUTE_NOT_FOUND"]["overridden"] is False


def test_route_category_override_only_affects_itself():
    """单独覆盖框架 ROUTE_NOT_FOUND：只改路由 404，业务 NOT_FOUND 等不变。"""
    from fastapi.testclient import TestClient

    from app.config import build_settings
    from app.errors.codes import ErrorCategory
    from app.main import create_app

    settings = build_settings({ErrorCategory.ROUTE_NOT_FOUND: 410})
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        route_miss = c.get("/no-such-path")
        business_miss = c.get("/users/999")
        method_not_allowed = c.post("/items/1")
        unauthorized = c.get("/admin")
        oversized = c.post(
            "/too-large",
            headers={"Content-Length": "101"},
            content=b"x" * 101,
        )
    log_case(
        "ROUTE_NOT_FOUND->410 只影响自己",
        {"ROUTE_NOT_FOUND": 410},
        code=route_miss.json()["code"],
        request_id=route_miss.json()["request_id"],
        status=410,
    )
    assert route_miss.status_code == 410
    assert route_miss.json()["code"] == "ROUTE_NOT_FOUND"
    assert business_miss.status_code == 404
    assert business_miss.json()["code"] == "NOT_FOUND"
    assert method_not_allowed.status_code == 405
    assert unauthorized.status_code == 401
    # 其它被框架挡下的 4xx（413）也不能被套成任何被改过分类的状态。
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "CLIENT_ERROR"


def test_other_framework_4xx_keeps_real_status_and_client_error_category(client):
    """非具名框架 4xx（413）归 CLIENT_ERROR 且保留框架给出的真实状态。"""
    response = client.post(
        "/too-large",
        headers={"Content-Length": "101"},
        content=b"x" * 101,
    )
    body = _assert_envelope(response, "CLIENT_ERROR", 413)
    log_case("其它框架4xx（413）", "POST /too-large", code=body["code"],
             request_id=body["request_id"], status=413)


def test_method_not_allowed_is_distinct_and_keeps_405(client):
    # /items/{item_id} 只注册了 GET；POST 被路由匹配挡在业务代码之外。
    response = client.post("/items/1")
    body = _assert_envelope(response, "METHOD_NOT_ALLOWED", 405)
    log_case("方法不允许", "POST /items/1", code=body["code"],
             request_id=body["request_id"], status=405)
    # 与“地址不存在”必须能彼此区分。
    assert body["code"] != client.get("/no-such-path").json()["code"]
    # 真实 405 语义头 Allow 必须保留，列出该地址实际允许的方法。
    allow = response.headers.get("allow", "")
    assert "GET" in allow


def test_missing_credentials_is_unauthorized_401(client):
    response = client.get("/admin")
    body = _assert_envelope(response, "UNAUTHORIZED", 401)
    log_case("缺少凭证", "GET /admin", code=body["code"],
             request_id=body["request_id"], status=401)


def test_bad_credentials_is_unauthorized_401(client):
    response = client.get("/admin", headers={"Authorization": "Bearer bogus"})
    body = _assert_envelope(response, "UNAUTHORIZED", 401)
    log_case("凭证非法", {"Authorization": "Bearer bogus"}, code=body["code"],
             request_id=body["request_id"], status=401)


def test_authenticated_without_permission_is_forbidden_403(client):
    response = client.get(
        "/admin", headers={"Authorization": "Bearer user-secret"}
    )
    body = _assert_envelope(response, "FORBIDDEN", 403)
    log_case("访问被拒绝", {"Authorization": "Bearer user-secret"},
             code=body["code"], request_id=body["request_id"], status=403)
    # 401 与 403 必须是两个不同分类。
    missing = client.get("/admin").json()["code"]
    assert body["code"] != missing


def test_admin_success_path(client):
    """通过守卫后仍是原有成功结构，守卫不影响正常请求。"""
    response = client.get(
        "/admin", headers={"Authorization": "Bearer admin-secret"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "area": "admin"}


def test_unsupported_media_type_is_415(client):
    response = client.post(
        "/users",
        content=b"name=a&age=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    body = _assert_envelope(response, "UNSUPPORTED_MEDIA_TYPE", 415)
    log_case("媒体类型不支持", "application/x-www-form-urlencoded",
             code=body["code"], request_id=body["request_id"], status=415)


def test_unsupported_media_type_with_charset_suffix_still_415(client):
    """带 charset 后缀的非 JSON 媒体类型仍应判 415。"""
    response = client.post(
        "/users",
        content=b"<x/>",
        headers={"Content-Type": "application/xml; charset=utf-8"},
    )
    _assert_envelope(response, "UNSUPPORTED_MEDIA_TYPE", 415)


def test_json_content_type_still_passes_media_guard(client):
    """正常 JSON 请求不被 415 守卫误伤（随后进入校验/成功路径）。"""
    response = client.post(
        "/users", json={"name": "ok", "age": 10}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "method,url,expected_code,expected_status",
    [
        ("GET", "/no-such-path", "ROUTE_NOT_FOUND", 404),
        ("GET", "/users/999", "NOT_FOUND", 404),
        ("POST", "/items/1", "METHOD_NOT_ALLOWED", 405),
        ("GET", "/admin", "UNAUTHORIZED", 401),
    ],
)
def test_framework_codes_pairwise_distinguishable(
    client, method, url, expected_code, expected_status
):
    """框架层各分类两两不同，且状态为真实客户端错误状态。"""
    response = client.request(method, url)
    assert response.json()["code"] == expected_code
    assert response.status_code == expected_status
    assert 400 <= response.status_code < 500


def test_route_and_business_not_found_codes_are_distinct_strings(client):
    """即使默认状态同为 404，两个分类的 code 字符串也必须不同。"""
    assert (
        client.get("/no-such-path").json()["code"]
        != client.get("/users/999").json()["code"]
    )
