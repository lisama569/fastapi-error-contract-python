"""错误契约测试：校验失败（path/query/body）、业务拒绝、未处理异常、
结构稳定性、状态码可配置、请求标识与文档一致性。"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.config import ErrorContractSettings
from app.errors import ErrorCategory, ErrorLocation
from app.factory import create_app
from tests.conftest import log_case

logger = logging.getLogger("tests")

REQUIRED_KEYS = {"error", "request_id", "message", "fields"}
CATEGORIES = {c.value for c in ErrorCategory}
LOCATIONS = {loc.value for loc in ErrorLocation}


def _assert_unified_shape(payload: dict) -> None:
    assert REQUIRED_KEYS <= payload.keys()
    assert payload["error"] in CATEGORIES
    assert isinstance(payload["request_id"], str) and payload["request_id"]
    assert isinstance(payload["message"], str)
    for f in payload["fields"]:
        assert {"location", "field", "message"} <= f.keys()
        assert f["location"] in LOCATIONS


def test_category_values_are_stable_contract() -> None:
    # 这些字符串即对外契约，只允许追加新值，禁止改动既有值
    assert ErrorCategory.VALIDATION_FAILED.value == "validation_failed"
    assert ErrorCategory.BUSINESS_REJECTED.value == "business_rejected"
    assert ErrorCategory.INTERNAL_ERROR.value == "internal_error"
    assert ErrorLocation.PATH.value == "path"
    assert ErrorLocation.QUERY.value == "query"
    assert ErrorLocation.BODY.value == "body"
    assert ErrorLocation.HEADER.value == "header"


# ---------- 各入口的校验失败：同分类、可区分来源 ----------

def test_path_validation_failure(client: TestClient) -> None:
    url = "/demo/items/not-an-int"
    r = client.get(url)
    log_case("path_validation", url, r)
    assert r.status_code == 422
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.VALIDATION_FAILED.value
    assert len(body["fields"]) == 1
    assert body["fields"][0]["location"] == "path"
    assert body["fields"][0]["field"] == "item_id"
    assert body["request_id"] == r.headers["x-request-id"]


def test_query_validation_failure(client: TestClient) -> None:
    url = "/demo/items?page=abc"
    r = client.get(url)
    log_case("query_validation", url, r)
    assert r.status_code == 422
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.VALIDATION_FAILED.value
    assert body["fields"][0]["location"] == "query"
    assert body["fields"][0]["field"] == "page"


def test_body_validation_failure(client: TestClient) -> None:
    url = "/demo/items"
    r = client.post(url, json={"name": "", "quantity": 0})
    log_case("body_validation", url, r)
    assert r.status_code == 422
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.VALIDATION_FAILED.value
    locations = {f["location"] for f in body["fields"]}
    assert locations == {"body"}
    fields = {f["field"] for f in body["fields"]}
    assert fields == {"name", "quantity"}


def test_header_validation_failure(client: TestClient) -> None:
    url = "/demo/echo-tenant"
    r = client.get(url, headers={"X-Tenant-ID": "not-an-int"})
    log_case("header_validation", url, r)
    assert r.status_code == 422
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.VALIDATION_FAILED.value
    assert body["fields"][0]["location"] == "header"
    assert body["fields"][0]["field"] == "X-Tenant-ID"


def test_same_category_distinguishable_locations(client: TestClient) -> None:
    p = client.get("/demo/items/x")
    q = client.get("/demo/items?page=x")
    b = client.post("/demo/items", json={})
    h = client.get("/demo/echo-tenant", headers={"X-Tenant-ID": "x"})
    for body in (p.json(), q.json(), b.json(), h.json()):
        assert body["error"] == ErrorCategory.VALIDATION_FAILED.value
    assert p.json()["fields"][0]["location"] == "path"
    assert q.json()["fields"][0]["location"] == "query"
    assert b.json()["fields"][0]["location"] == "body"
    assert h.json()["fields"][0]["location"] == "header"


# ---------- 业务拒绝 ----------

def test_business_rejection(client: TestClient) -> None:
    url = "/demo/items"
    r = client.post(url, json={"name": "forbidden", "quantity": 1})
    log_case("business", url, r)
    assert r.status_code == 409
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.BUSINESS_REJECTED.value
    assert body["reason"] == "item_name_forbidden"
    assert body["fields"][0]["field"] == "name"
    assert body["message"]  # 面向人类的可读信息


# ---------- 未处理异常：不泄露内部细节 ----------

def test_unhandled_exception_hides_details(client: TestClient) -> None:
    url = "/demo/boom"
    r = client.get(url)
    log_case("unhandled", url, r)
    assert r.status_code == 500
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.INTERNAL_ERROR.value
    text = str(body)
    assert "secret" not in text
    assert "internal:5432" not in text
    assert "Traceback" not in text
    assert body["message"] == "internal server error"
    assert body["request_id"] == r.headers["x-request-id"]


# ---------- 路由层错误也归约到统一结构 ----------

def test_not_found_is_unified(client: TestClient) -> None:
    url = "/no/such/route"
    r = client.get(url)
    log_case("not_found", url, r)
    assert r.status_code == 404
    body = r.json()
    _assert_unified_shape(body)
    assert body["error"] == ErrorCategory.NOT_FOUND.value
    assert body["request_id"] == r.headers["x-request-id"]


# ---------- 成功响应语义保持兼容 ----------

def test_success_semantics_unchanged(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"Hello": "World"}

    r = client.post("/demo/items", json={"name": "widget", "quantity": 3})
    assert r.status_code == 201
    assert r.json() == {"name": "widget", "quantity": 3}


# ---------- 请求标识 ----------

def test_request_id_generated_when_missing(client: TestClient) -> None:
    r = client.get("/")
    rid = r.headers["x-request-id"]
    assert len(rid) >= 16
    assert r.headers["x-request-id-source"] == "generated-missing"


def test_client_request_id_preserved(client: TestClient) -> None:
    r = client.get("/", headers={"x-request-id": "trace-abc-1234"})
    assert r.headers["x-request-id"] == "trace-abc-1234"
    assert r.headers["x-request-id-source"] == "client"


def test_malformed_request_id_replaced(client: TestClient) -> None:
    r = client.get("/", headers={"x-request-id": "bad id!!"})
    assert r.headers["x-request-id-source"] == "replaced-malformed"
    assert r.headers["x-request-id"] != "bad id!!"


# ---------- 结构向后兼容 ----------

def test_extra_optional_fields_do_not_break_clients(client: TestClient) -> None:
    r = client.get("/demo/items/x")
    body = r.json()
    # 旧客户端只按四个既有字段解析，仍可工作；新字段（reason）为追加项
    assert body["reason"] is None
    assert REQUIRED_KEYS <= body.keys()


# ---------- 状态码映射可按配置调整 ----------

def test_status_map_configurable_without_business_change() -> None:
    settings = ErrorContractSettings(
        {ErrorCategory.BUSINESS_REJECTED: 422}, env={}
    )
    app = create_app(settings)
    c = TestClient(app)
    r = c.post("/demo/items", json={"name": "forbidden", "quantity": 1})
    log_case("config_override", "/demo/items", r)
    assert r.status_code == 422  # 同一业务拒绝，仅状态码映射改变
    assert r.json()["error"] == ErrorCategory.BUSINESS_REJECTED.value


def test_status_map_configurable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("ERROR_STATUS_INTERNAL_ERROR", "503")
    app = create_app(ErrorContractSettings())
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/demo/boom")
    log_case("env_override", "/demo/boom", r)
    assert r.status_code == 503  # 分类不变，仅状态码由环境变量调整
    assert r.json()["error"] == ErrorCategory.INTERNAL_ERROR.value


# ---------- 文档与运行时一致 ----------

def test_openapi_documents_error_contract(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "ErrorResponse" in spec["components"]["schemas"]
    schema = spec["components"]["schemas"]["ErrorResponse"]
    assert {"error", "request_id", "message", "fields", "reason"} <= set(
        schema["properties"]
    )

    cases = [
        ("post", "/demo/items", {"409", "422", "500"}),
        ("get", "/demo/items/{item_id}", {"422", "500"}),
        ("get", "/demo/echo-tenant", {"422", "500"}),
        ("get", "/demo/boom", {"500"}),
    ]
    for method, path, codes in cases:
        responses = spec["paths"][path][method]["responses"]
        assert set(responses) >= codes, (path, set(responses))
        for code in codes:
            ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
            assert ref == "#/components/schemas/ErrorResponse"

    # 文档分类与运行时分类取值一致
    runtime = {
        "validation_failed",
        "business_rejected",
        "internal_error",
    }
    assert runtime <= CATEGORIES
