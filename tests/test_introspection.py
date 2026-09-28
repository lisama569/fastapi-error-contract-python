"""只读运行时自查端点：分类清单/默认状态/生效状态/覆盖标记。

要求：无覆盖时稳定可读；配了覆盖后与真实请求一致；与 OpenAPI 文档一致；
不依赖外部服务（只读本进程 Settings）。
"""

from fastapi.testclient import TestClient

from app.config import DEFAULT_STATUS_MAP, build_settings
from app.errors.codes import ErrorCategory
from app.main import create_app
from tests._log import log_case


def _index(body: dict) -> dict[str, dict]:
    return {item["code"]: item for item in body["categories"]}


def test_introspection_lists_all_categories_with_real_defaults(client):
    response = client.get("/meta/error-contract")
    assert response.status_code == 200
    body = response.json()
    log_case("自查默认清单", body["overridden_categories"],
             code="META", request_id=None, status=200)

    listed = [item["code"] for item in body["categories"]]
    # 与枚举声明顺序一致 -> 稳定可读。
    assert listed == [c.value for c in ErrorCategory]
    assert body["request_id_header"] == "X-Request-ID"
    assert body["overridden_categories"] == []  # 默认配置无覆盖

    indexed = _index(body)
    for category in ErrorCategory:
        entry = indexed[category.value]
        assert entry["default_status"] == DEFAULT_STATUS_MAP[category]
        assert entry["effective_status"] == DEFAULT_STATUS_MAP[category]
        assert entry["overridden"] is False


def test_introspection_reflects_config_overrides():
    settings = build_settings(
        {
            ErrorCategory.METHOD_NOT_ALLOWED: 422,
            ErrorCategory.FORBIDDEN: 404,
        }
    )
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/meta/error-contract").json()
        # 覆盖必须在真实请求上同样生效：405 -> 422、403 -> 404。
        real_405 = c.post("/items/1")
        real_403 = c.get(
            "/admin", headers={"Authorization": "Bearer user-secret"}
        )

    indexed = _index(body)
    log_case(
        "自查覆盖生效",
        body["overridden_categories"],
        code="META",
        request_id=None,
        status=200,
    )
    assert body["overridden_categories"] == ["METHOD_NOT_ALLOWED", "FORBIDDEN"]
    assert indexed["METHOD_NOT_ALLOWED"]["effective_status"] == 422
    assert indexed["METHOD_NOT_ALLOWED"]["overridden"] is True
    assert indexed["FORBIDDEN"]["effective_status"] == 404
    assert indexed["FORBIDDEN"]["overridden"] is True
    # 未覆盖项默认值与生效值一致。
    assert indexed["NOT_FOUND"]["default_status"] == indexed["NOT_FOUND"]["effective_status"] == 404
    assert indexed["NOT_FOUND"]["overridden"] is False

    assert real_405.status_code == 422
    assert real_405.json()["code"] == "METHOD_NOT_ALLOWED"
    assert real_403.status_code == 404
    assert real_403.json()["code"] == "FORBIDDEN"


def test_introspection_matches_openapi_and_runtime_default_requests(client):
    """自查结果、真实请求、OpenAPI 三者一致。"""
    schema = client.get("/openapi.json").json()
    # 自查端点自身进入 OpenAPI。
    assert "/meta/error-contract" in schema["paths"]
    assert "ErrorContractView" in schema["components"]["schemas"]

    body = client.get("/meta/error-contract").json()
    indexed = _index(body)

    # 抽样：自查默认状态 == 实际请求状态。
    runtime = {
        "NOT_FOUND": client.get("/no-such-path").status_code,
        "METHOD_NOT_ALLOWED": client.post("/items/1").status_code,
        "UNAUTHORIZED": client.get("/admin").status_code,
        "UNSUPPORTED_MEDIA_TYPE": client.post(
            "/users",
            content=b"x",
            headers={"Content-Type": "text/plain"},
        ).status_code,
    }
    for code, status in runtime.items():
        assert indexed[code]["effective_status"] == status == indexed[code]["default_status"]
