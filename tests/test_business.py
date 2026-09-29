"""业务拒绝：分类/状态码/信封结构；成功响应语义保持兼容。"""

from fastapi.testclient import TestClient

from app.config import Settings, build_settings
from app.errors.codes import ErrorCategory
from app.main import create_app
from tests._log import log_case


def test_business_rejection_envelope(client):
    response = client.post(
        "/business-reject", headers={"X-Resource": "order-7"}
    )
    body = response.json()
    log_case(
        "业务拒绝",
        {"path": "/business-reject", "X-Resource": "order-7"},
        code=body.get("code"),
        request_id=body.get("request_id"),
        status=response.status_code,
    )
    assert response.status_code == 409
    assert body["error"] == "error"
    assert body["code"] == "BUSINESS_RULE_VIOLATION"
    assert "order-7" in body["message"]
    assert body["details"] is None  # 业务拒绝没有逐字段定位
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_business_rejection_from_body_path(client):
    """body 入口上的业务拒绝同样归约到业务分类（age 防御式拒绝）。"""
    response = client.post("/users", json={"name": "ok", "age": 1})
    # age=1 合法，走正常成功路径
    assert response.status_code == 200
    body = response.json()
    log_case("成功创建用户", {"name": "ok", "age": 1},
             code=None, request_id=response.headers.get("X-Request-ID"),
             status=200)
    assert set(body) == {"id", "name", "age"}
    assert body["name"] == "ok" and body["age"] == 1


def test_success_response_unchanged(client):
    """既有根路由成功结构必须保持 {"Hello": "World"} 兼容。"""
    response = client.get("/")
    log_case("成功根路由", "/", code=None,
             request_id=response.headers.get("X-Request-ID"),
             status=response.status_code)
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}
    assert "error" not in response.json()  # 成功响应绝不携带错误标记位
    # 成功响应同样可追踪。
    assert response.headers["X-Request-ID"]


def test_status_code_remappable_via_config():
    """把业务拒绝改成 422 只需换配置，业务逻辑零改动。"""
    settings = build_settings(
        {ErrorCategory.BUSINESS_RULE_VIOLATION: 422}
    )
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.post("/business-reject")
    body = response.json()
    log_case(
        "状态码配置覆盖",
        {"BUSINESS_RULE_VIOLATION": 422},
        code=body.get("code"),
        request_id=body.get("request_id"),
        status=response.status_code,
    )
    # 分类不变（客户端解析稳定），仅 HTTP 状态码改变。
    assert body["code"] == "BUSINESS_RULE_VIOLATION"
    assert response.status_code == 422


def test_not_found_envelope(client):
    """业务侧“数据没查到”：NOT_FOUND 分类语义与默认 404 均保持不变。"""
    response = client.get("/users/999")
    body = response.json()
    log_case("业务资源不存在", "/users/999", code=body.get("code"),
             request_id=body.get("request_id"), status=response.status_code)
    assert response.status_code == 404
    assert body["code"] == "NOT_FOUND"
    assert body["details"] is None
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_known_business_resource_succeeds(client):
    """已存在的业务数据正常返回，结构与既有成功响应兼容。"""
    response = client.get("/users/1")
    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "demo-user", "age": 30}
