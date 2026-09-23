"""各入口校验失败：分类须按来源可区分，结构与定位信息稳定。"""

import pytest

from tests._log import log_case

ENVELOPE_KEYS = {"error", "code", "message", "request_id", "details"}


def _assert_envelope(body: dict, expected_code: str) -> None:
    assert ENVELOPE_KEYS <= set(body), f"错误信封缺字段: {set(body)}"
    assert body["error"] == "error"
    assert body["code"] == expected_code
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert isinstance(body["details"], list) and body["details"]


@pytest.mark.parametrize(
    "case,method,url,kwargs,expected_code,expected_loc_prefix",
    [
        (
            "path 参数类型错误",
            "GET", "/items/not-an-int", {},
            "VALIDATION_PATH", ["path", "item_id"],
        ),
        (
            "path 参数越界",
            "GET", "/items/0", {},
            "VALIDATION_PATH", ["path", "item_id"],
        ),
        (
            "query 参数过短",
            "GET", "/items/1?q=x", {},
            "VALIDATION_QUERY", ["query", "q"],
        ),
        (
            "query 参数类型错误",
            "GET", "/items/1?limit=not-int", {},
            "VALIDATION_QUERY", ["query", "limit"],
        ),
        (
            "body 字段非法",
            "POST", "/users", {"json": {"name": "", "age": 0}},
            "VALIDATION_BODY", ["body", "name"],
        ),
        (
            "body 类型错误",
            "POST", "/users", {"json": {"name": "a", "age": "x"}},
            "VALIDATION_BODY", ["body", "age"],
        ),
        (
            "header 过短",
            "GET", "/boom", {"headers": {"X-Trace": "ab"}},
            "VALIDATION_HEADER", ["header", "X-Trace"],
        ),
    ],
)
def test_validation_failures(
    client, case, method, url, kwargs, expected_code, expected_loc_prefix
):
    response = client.request(method, url, **kwargs)
    body = response.json()
    log_case(
        case, {"method": method, "url": url, **kwargs},
        code=body.get("code"), request_id=body.get("request_id"),
        status=response.status_code,
    )
    assert response.status_code == 422
    _assert_envelope(body, expected_code)
    first = body["details"][0]
    assert first["loc"][: len(expected_loc_prefix)] == expected_loc_prefix
    assert first["message"]
    assert first["type"]
    # request_id 必须回显在响应头，且与 body 一致。
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_validation_categories_are_distinguishable(client):
    """path/query/body/header 同类校验失败的分类必须两两不同。"""
    cases = {
        "VALIDATION_PATH": client.get("/items/x").json(),
        "VALIDATION_QUERY": client.get("/items/1?q=").json(),
        "VALIDATION_BODY": client.post("/users", json={"name": "a", "age": -1}).json(),
        "VALIDATION_HEADER": client.get("/boom", headers={"X-Trace": "x"}).json(),
    }
    codes = set(cases)
    log_case("分类可区分", cases, code=",".join(sorted(codes)),
             request_id=";".join(b["request_id"] for b in cases.values()))
    assert codes == {
        "VALIDATION_PATH",
        "VALIDATION_QUERY",
        "VALIDATION_BODY",
        "VALIDATION_HEADER",
    }


def test_multi_field_details_keep_locations(client):
    """一次请求多个字段失败时，逐字段定位必须完整保留。"""
    response = client.post("/users", json={"name": "x" * 60, "age": 999})
    body = response.json()
    log_case("多字段 body", {"name": "x"*60, "age": 999},
             code=body["code"], request_id=body["request_id"],
             status=response.status_code)
    locs = [tuple(d["loc"]) for d in body["details"]]
    assert ("body", "name") in locs
    assert ("body", "age") in locs
