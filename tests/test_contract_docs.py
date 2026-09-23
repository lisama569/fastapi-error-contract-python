"""运行时行为与自动文档（OpenAPI）一致：错误信封、分类与状态码。"""

from tests._log import log_case


def test_openapi_declares_unified_error_schema(client):
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    log_case(
        "OpenAPI ErrorResponse",
        sorted(components.get("ErrorResponse", {}).get("properties", {})),
        code="SCHEMA_PRESENT",
        request_id=None,
        status=200,
    )
    assert "ErrorResponse" in components
    props = components["ErrorResponse"]["properties"]
    assert set(props) == {"error", "code", "message", "request_id", "details"}
    # 关键契约字段不允许缺失。
    for name in ("code", "message", "request_id"):
        assert name in props
    assert "FieldError" in components
    assert set(components["FieldError"]["properties"]) == {"loc", "message", "type"}


def test_documented_status_codes_match_runtime(client):
    """文档中为各路由声明的错误状态码必须与运行时默认映射一致。"""
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    checks = [
        ("/items/{item_id}", "get", client.get("/items/x"), 422),
        ("/users", "post", client.post("/users", json={}), 422),
        (
            "/business-reject",
            "post",
            client.post("/business-reject"),
            409,
        ),
        ("/boom", "get", client.get("/boom"), 500),
    ]
    for path, method, response, expected_status in checks:
        documented = {int(k) for k in paths[path][method]["responses"]}
        body = response.json()
        log_case(
            f"文档/运行时一致 {method.upper()} {path}",
            {"documented": sorted(documented)},
            code=body.get("code"),
            request_id=body.get("request_id"),
            status=response.status_code,
        )
        assert expected_status in documented
        assert response.status_code == expected_status
        # 文档声明的错误响应模型必须引用统一信封。
        ref = paths[path][method]["responses"][str(expected_status)]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert ref.endswith("/ErrorResponse")


def test_all_categories_are_documented(client):
    """所有对外分类都应出现在契约文档中（通过枚举值集合稳定性把关）。"""
    from app.errors.codes import ErrorCategory

    schema = client.get("/openapi.json").json()
    # 分类由代码枚举驱动；文档文件 docs/error-contract.md 中也须逐一登记。
    from pathlib import Path

    doc = Path("docs/error-contract.md").read_text(encoding="utf-8")
    missing = [c.value for c in ErrorCategory if c.value not in doc]
    log_case(
        "分类文档覆盖",
        [c.value for c in ErrorCategory],
        code="ALL_DOCUMENTED" if not missing else f"MISSING:{missing}",
        request_id=None,
        status=200,
    )
    assert not missing, f"文档缺少分类：{missing}"
    # OpenAPI 服务可用。
    assert schema["info"]["title"]
