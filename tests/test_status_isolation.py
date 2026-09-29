"""按分类单独配置状态码的影响边界：改哪类只影响哪类。

重点回归本轮修复的串扰问题：
- 把业务 NOT_FOUND 改成别的状态（如 400）后，框架挡下的路由 404、405、
  401、403、415 与其它 4xx（413）状态一律不变；
- 单独覆盖某个框架分类后，也只有该分类变化，业务分类与其它框架分类不变；
- 客户端收到的状态与服务端日志记录的状态始终一致，日志另保留框架原始状态。
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import build_settings
from app.errors.codes import ErrorCategory
from app.main import create_app
from tests._log import log_case


def _client(status_overrides: dict[ErrorCategory, int] | None = None):
    settings = build_settings(dict(status_overrides or {}))
    app = create_app(settings)
    return TestClient(app, raise_server_exceptions=False)


def _framework_probes(c: TestClient) -> dict[str, tuple[int, str]]:
    """同一组框架层探测请求 -> (实际状态, 分类)。"""
    probes = {
        "route": ("GET", "/no-such-path", {}),
        "method": ("POST", "/items/1", {}),
        "unauthorized": ("GET", "/admin", {}),
        "forbidden": (
            "GET",
            "/admin",
            {"headers": {"Authorization": "Bearer user-secret"}},
        ),
        "media": (
            "POST",
            "/users",
            {"content": b"x", "headers": {"Content-Type": "text/plain"}},
        ),
        "other_4xx": (
            "POST",
            "/too-large",
            {"headers": {"Content-Length": "101"}, "content": b"x" * 101},
        ),
    }
    out: dict[str, tuple[int, str]] = {}
    for name, (method, url, kwargs) in probes.items():
        response = c.request(method, url, **kwargs)
        out[name] = (response.status_code, response.json()["code"])
    return out


EXPECTED_DEFAULT_FRAMEWORK = {
    "route": (404, "ROUTE_NOT_FOUND"),
    "method": (405, "METHOD_NOT_ALLOWED"),
    "unauthorized": (401, "UNAUTHORIZED"),
    "forbidden": (403, "FORBIDDEN"),
    "media": (415, "UNSUPPORTED_MEDIA_TYPE"),
    "other_4xx": (413, "CLIENT_ERROR"),
}


def test_default_framework_statuses_are_real_statuses(client):
    """默认配置下，每类框架拒绝都返回框架原本给出的真实状态。"""
    actual = _framework_probes(client)
    log_case(
        "框架真实状态默认保留",
        {k: v[0] for k, v in actual.items()},
        code=",".join(v[1] for v in actual.values()),
        request_id=None,
        status=200,
    )
    assert actual == EXPECTED_DEFAULT_FRAMEWORK


def test_override_business_not_found_does_not_touch_framework(client):
    """用户报障场景：业务 NOT_FOUND->400，框架层所有真实状态必须原样保留。"""
    with _client({ErrorCategory.NOT_FOUND: 400}) as c:
        business_hit = c.get("/users/999")
        business_ok_data = c.get("/users/1")
        success = c.get("/")
        actual = _framework_probes(c)
    log_case(
        "业务NOT_FOUND改400，框架状态不变",
        {"NOT_FOUND": 400},
        code=business_hit.json()["code"],
        request_id=business_hit.json()["request_id"],
        status=business_hit.status_code,
    )
    assert business_hit.status_code == 400
    assert business_hit.json()["code"] == "NOT_FOUND"
    # 业务正常数据与既有成功接口不回归。
    assert business_ok_data.status_code == 200
    assert success.status_code == 200
    assert success.json() == {"Hello": "World"}
    assert actual == EXPECTED_DEFAULT_FRAMEWORK


@pytest.mark.parametrize(
    "category,default_status,probe_name",
    [
        (ErrorCategory.ROUTE_NOT_FOUND, 404, "route"),
        (ErrorCategory.METHOD_NOT_ALLOWED, 405, "method"),
        (ErrorCategory.UNAUTHORIZED, 401, "unauthorized"),
        (ErrorCategory.FORBIDDEN, 403, "forbidden"),
        (ErrorCategory.UNSUPPORTED_MEDIA_TYPE, 415, "media"),
    ],
)
def test_each_framework_category_is_independently_overridable(
    category, default_status, probe_name
):
    """每个框架分类都能单独调；调完只有它自己变，其它分类与业务分类不变。"""
    custom_status = default_status + 30  # 仍在 4xx/5xx 区间且不同于默认值
    with _client({category: custom_status}) as c:
        actual = _framework_probes(c)
        business_hit = c.get("/users/999")
        business_reject = c.post("/business-reject")

    changed_status, changed_code = actual.pop(probe_name)
    log_case(
        f"单独覆盖 {category.value}",
        {category.value: custom_status},
        code=changed_code,
        request_id=None,
        status=changed_status,
    )
    assert changed_status == custom_status
    # 分类名不随状态改变。
    assert changed_code == category.value
    # 其它框架探测点全部保持真实状态与原分类。
    assert actual == {
        k: v for k, v in EXPECTED_DEFAULT_FRAMEWORK.items() if k != probe_name
    }
    # 业务分类完全不受影响。
    assert business_hit.status_code == 404
    assert business_hit.json()["code"] == "NOT_FOUND"
    assert business_reject.status_code == 409
    assert business_reject.json()["code"] == "BUSINESS_RULE_VIOLATION"


def test_unknown_4xx_never_takes_overridden_not_found_status(client):
    """兜底 4xx（413）不会被业务 NOT_FOUND 或任何具名分类的配置套状态。"""
    with _client(
        {
            ErrorCategory.NOT_FOUND: 400,
            ErrorCategory.ROUTE_NOT_FOUND: 410,
            ErrorCategory.METHOD_NOT_ALLOWED: 422,
        }
    ) as c:
        oversized = c.post(
            "/too-large",
            headers={"Content-Length": "101"},
            content=b"x" * 101,
        )
    log_case(
        "未知4xx不受任何覆盖影响",
        {"NOT_FOUND": 400, "ROUTE_NOT_FOUND": 410, "METHOD_NOT_ALLOWED": 422},
        code=oversized.json()["code"],
        request_id=oversized.json()["request_id"],
        status=oversized.status_code,
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "CLIENT_ERROR"


def test_client_error_default_passthrough_is_reported_by_introspection(client):
    """自查端点如实展示 CLIENT_ERROR 的透传语义。"""
    meta = client.get("/meta/error-contract").json()
    entry = {x["code"]: x for x in meta["categories"]}["CLIENT_ERROR"]
    log_case(
        "CLIENT_ERROR 自查",
        entry,
        code="CLIENT_ERROR",
        request_id=None,
        status=200,
    )
    assert entry["passthrough"] is True
    assert entry["default_status"] is None
    assert entry["overridden"] is False
    assert "CLIENT_ERROR" not in meta["overridden_categories"]


def test_client_error_can_be_explicitly_overridden_but_not_via_other_categories():
    """显式覆盖 CLIENT_ERROR 才改兜底 4xx；改其它分类不会动它。"""
    # 只改业务 NOT_FOUND 与 ROUTE_NOT_FOUND：413 必须原样。
    with _client(
        {
            ErrorCategory.NOT_FOUND: 400,
            ErrorCategory.ROUTE_NOT_FOUND: 410,
        }
    ) as c:
        untouched = c.post(
            "/too-large",
            headers={"Content-Length": "101"},
            content=b"x" * 101,
        )
    assert untouched.status_code == 413
    assert untouched.json()["code"] == "CLIENT_ERROR"

    # 显式覆盖 CLIENT_ERROR 本身：兜底 4xx 才统一改写。
    with _client({ErrorCategory.CLIENT_ERROR: 422}) as c:
        overridden = c.post(
            "/too-large",
            headers={"Content-Length": "101"},
            content=b"x" * 101,
        )
        meta = c.get("/meta/error-contract").json()
        # 具名分类仍保留真实状态。
        route_miss = c.get("/no-such-path")
    log_case(
        "CLIENT_ERROR->422 仅影响兜底4xx",
        {"CLIENT_ERROR": 422},
        code=overridden.json()["code"],
        request_id=overridden.json()["request_id"],
        status=overridden.status_code,
    )
    assert overridden.status_code == 422
    assert overridden.json()["code"] == "CLIENT_ERROR"
    assert route_miss.status_code == 404
    entry = {x["code"]: x for x in meta["categories"]}["CLIENT_ERROR"]
    assert entry["overridden"] is True
    assert entry["effective_status"] == 422
    assert "CLIENT_ERROR" in meta["overridden_categories"]


def test_logged_status_matches_client_under_override(caplog):
    """覆盖分类后，服务端日志的 status 必须等于客户端真实收到的状态。"""
    with _client({ErrorCategory.FORBIDDEN: 451}) as c:
        with caplog.at_level(logging.INFO, logger="app.errors"):
            response = c.get(
                "/admin",
                headers={"Authorization": "Bearer user-secret"},
            )
    assert response.status_code == 451
    record = next(
        r
        for r in caplog.records
        if r.name == "app.errors" and "request failed" in r.getMessage()
    )
    message = record.getMessage()
    assert "category=FORBIDDEN" in message
    assert "status=451" in message  # 与客户端一致
    assert "http_status=403" in message  # 框架原始状态仍可查
