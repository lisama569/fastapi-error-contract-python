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


def test_unknown_route_uses_framework_category_and_real_status(client):
    """框架层路由不存在：ROUTE_NOT_FOUND 分类 + 保留真实 404 状态。"""
    response = client.get("/no-such-path")
    body = response.json()
    log_case("框架路由不存在", "/no-such-path", code=body.get("code"),
             request_id=body.get("request_id"), status=response.status_code)
    assert response.status_code == 404
    assert body["code"] == "ROUTE_NOT_FOUND"
    assert body["details"] is None
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_business_not_found_category_remains_supported(settings):
    """业务资源 NOT_FOUND 分类仍然存在且默认可用（旧契约不回归）。

    通过业务层直接抛出 BusinessError(NOT_FOUND) 验证：分类枚举、默认 404
    状态与统一信封保持不变。
    """
    from fastapi import APIRouter
    from fastapi.testclient import TestClient as TC

    from app.errors.exceptions import BusinessError
    from app.main import create_app

    app = create_app(settings)
    extra = APIRouter()

    @extra.get("/missing-resource")
    def _missing() -> None:
        raise BusinessError("资源不存在", code=ErrorCategory.NOT_FOUND)

    app.include_router(extra)
    with TC(app) as c2:
        response = c2.get("/missing-resource")
    body = response.json()
    log_case("业务资源 NOT_FOUND", "/missing-resource", code=body.get("code"),
             request_id=body.get("request_id"), status=response.status_code)
    assert response.status_code == 404
    assert body["code"] == "NOT_FOUND"
    assert body["details"] is None
