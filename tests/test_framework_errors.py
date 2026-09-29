"""框架层客户端错误（未进入业务代码即被挡下）：分类可区分、真实状态保留。

覆盖：路由地址不存在 404（ROUTE_NOT_FOUND，独立于业务 NOT_FOUND）、
方法不允许 405、未认证 401、访问被拒 403、请求体格式不支持 415、
其它框架层 4xx（413 -> CLIENT_ERROR，保留真实状态）。
状态码默认必须保留框架给出的真实值，分类稳定不变。
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


def test_route_not_found_has_own_category_and_keeps_404(client):
    response = client.get("/no-such-path")
    body = _assert_envelope(response, "ROUTE_NOT_FOUND", 404)
    log_case("路由地址不存在", "/no-such-path", code=body["code"],
             request_id=body["request_id"], status=404)


def test_route_not_found_distinct_from_business_not_found(client):
    """框架层“地址不存在”与业务“没查到这条数据”必须各算各的。"""
    route_miss = client.get("/no-such-path")
    business_miss = client.get("/users/999999")
    route_body = _assert_envelope(route_miss, "ROUTE_NOT_FOUND", 404)
    business_body = _assert_envelope(business_miss, "NOT_FOUND", 404)
    log_case("两类不存在对比", ["/no-such-path", "/users/999999"],
             code=f"{route_body['code']} vs {business_body['code']}",
             request_id=route_body["request_id"], status=404)
    # 只凭分类即可区分：地址写错了 vs 业务上确实没有。
    assert route_body["code"] != business_body["code"]


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


def test_oversized_payload_is_client_error_and_keeps_413(client):
    """其它框架层 4xx 归 CLIENT_ERROR，默认保留框架真实状态（413）。"""
    response = client.post(
        "/users", json={"name": "x" * (1024 * 1024), "age": 1}
    )
    body = _assert_envelope(response, "CLIENT_ERROR", 413)
    log_case("请求体过大", "POST /users >1MB", code=body["code"],
             request_id=body["request_id"], status=413)
    # 兜底分类与“资源不存在”等既有分类都能区分。
    assert body["code"] != client.get("/no-such-path").json()["code"]


@pytest.mark.parametrize(
    "method,url,expected_code,expected_status",
    [
        ("GET", "/no-such-path", "ROUTE_NOT_FOUND", 404),
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
