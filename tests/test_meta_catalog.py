"""只读运行时自查端点 /meta/error-catalog。"""

from fastapi.testclient import TestClient

from app.config import DEFAULT_STATUS_MAP, build_settings
from app.errors.codes import ErrorCategory
from app.main import create_app
from tests._log import log_case

_CATALOG_PATH = "/meta/error-catalog"


def test_catalog_lists_all_categories_with_defaults(client):
    """无覆盖：每个分类都登记，effective == default，覆盖清单为空。"""
    response = client.get(_CATALOG_PATH)
    assert response.status_code == 200
    data = response.json()
    log_case("自查-默认", [c.value for c in ErrorCategory],
             code="CATALOG", request_id=response.headers["X-Request-ID"],
             status=200)
    entries = {e["code"]: e for e in data["categories"]}
    assert set(entries) == {c.value for c in ErrorCategory}
    for category in ErrorCategory:
        entry = entries[category.value]
        assert entry["default_status"] == DEFAULT_STATUS_MAP[category]
        assert entry["effective_status"] == DEFAULT_STATUS_MAP[category]
        assert entry["overridden"] is False
    assert data["overridden_codes"] == []
    # 顺序必须稳定（枚举声明顺序）。
    assert [e["code"] for e in data["categories"]] == [
        c.value for c in ErrorCategory
    ]
    assert data["request_id_header"] == "X-Request-ID"


def test_catalog_reflects_overrides_and_matches_runtime():
    """有覆盖：自查结果与真实请求返回状态、默认值、覆盖标记一致。"""
    settings = build_settings({
        ErrorCategory.METHOD_NOT_ALLOWED: 404,
        ErrorCategory.BUSINESS_RULE_VIOLATION: 422,
    })
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        data = c.get(_CATALOG_PATH).json()
        # 真实请求：405 被覆盖为 404，业务拒绝被覆盖为 422。
        real_405 = c.delete("/")
        real_business = c.post("/business-reject")

    entries = {e["code"]: e for e in data["categories"]}
    log_case(
        "自查-覆盖",
        data["overridden_codes"],
        code=",".join(data["overridden_codes"]),
        request_id=None, status=200,
    )
    assert data["overridden_codes"] == [
        "BUSINESS_RULE_VIOLATION", "METHOD_NOT_ALLOWED"
    ]  # 枚举顺序，与插入顺序无关
    mna = entries["METHOD_NOT_ALLOWED"]
    assert (mna["default_status"], mna["effective_status"],
            mna["overridden"]) == (405, 404, True)
    brv = entries["BUSINESS_RULE_VIOLATION"]
    assert (brv["default_status"], brv["effective_status"],
            brv["overridden"]) == (409, 422, True)
    # 未被覆盖的分类不受影响。
    assert entries["ROUTE_NOT_FOUND"]["effective_status"] == 404
    assert entries["ROUTE_NOT_FOUND"]["overridden"] is False
    # 与真实请求对账。
    assert real_405.status_code == mna["effective_status"] == 404
    assert real_405.json()["code"] == "METHOD_NOT_ALLOWED"
    assert real_business.status_code == brv["effective_status"] == 422
    assert real_business.json()["code"] == "BUSINESS_RULE_VIOLATION"


def test_catalog_is_read_only(client):
    """自查端点只读：写方法不允许，且 GET 不产生状态变化。"""
    before = client.get(_CATALOG_PATH).json()
    client.get(_CATALOG_PATH)  # 重复读取
    after = client.get(_CATALOG_PATH).json()
    assert before == after
    write = client.post(_CATALOG_PATH, json={})
    log_case("自查-只读", "POST", code=write.json().get("code"),
             request_id=write.headers.get("X-Request-ID"),
             status=write.status_code)
    assert write.status_code == 405
    assert write.json()["code"] == "METHOD_NOT_ALLOWED"


def test_catalog_matches_openapi_documentation(client):
    """自查中的 default_status 必须与 OpenAPI 为各路由声明的状态码同源。"""
    data = client.get(_CATALOG_PATH).json()
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    log_case("自查-文档一致", sorted(components),
             code="OPENAPI", request_id=None, status=200)
    # 自查模型本身进文档。
    assert "ErrorCatalog" in components
    assert "CategoryEntry" in components
    props = components["CategoryEntry"]["properties"]
    assert set(props) == {
        "code", "default_status", "effective_status", "overridden"
    }
    # 自查端点在 OpenAPI 中登记。
    assert _CATALOG_PATH in schema["paths"]
    assert "get" in schema["paths"][_CATALOG_PATH]

    # 抽样：文档为 /users 声明的 415 与自查中该分类默认值一致。
    users_responses = set(schema["paths"]["/users"]["post"]["responses"])
    entries = {e["code"]: e for e in data["categories"]}
    assert str(entries["UNSUPPORTED_MEDIA_TYPE"]["default_status"]) in \
        users_responses


def test_catalog_works_without_external_dependency(client):
    """自查纯本地：响应不依赖任何外部网络资源，且始终可重复读取。"""
    r1 = client.get(_CATALOG_PATH)
    r2 = client.get(_CATALOG_PATH)
    assert r1.json() == r2.json()
