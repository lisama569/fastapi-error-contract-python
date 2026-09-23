"""请求标识：缺失补全、原样透传、非法/重复不静默复用。"""

from tests._log import log_case


def test_missing_request_id_is_generated_and_flagged(client):
    response = client.get("/")
    rid = response.headers["X-Request-ID"]
    log_case("标识缺失", {"X-Request-ID": None},
             code=None, request_id=rid, status=response.status_code)
    assert rid  # 永远有标识
    assert response.headers["X-Request-ID-Source"] == "generated"
    assert response.headers["X-Request-ID-Warning"] == "missing"


def test_client_request_id_is_preserved(client):
    response = client.get("/", headers={"X-Request-ID": "client-trace-42"})
    log_case("标识透传", {"X-Request-ID": "client-trace-42"},
             code=None, request_id=response.headers["X-Request-ID"],
             status=response.status_code)
    assert response.headers["X-Request-ID"] == "client-trace-42"
    assert response.headers["X-Request-ID-Source"] == "client"
    assert "X-Request-ID-Warning" not in response.headers


def test_malformed_request_id_is_replaced_not_reused(client):
    bad = "bad id with spaces!!"
    response = client.get("/", headers={"X-Request-ID": bad})
    rid = response.headers["X-Request-ID"]
    log_case("标识非法", {"X-Request-ID": bad},
             code=None, request_id=rid, status=response.status_code)
    assert rid != bad  # 绝不复用非法值
    assert response.headers["X-Request-ID-Source"] == "generated"
    assert response.headers["X-Request-ID-Warning"] == "invalid"


def test_each_missing_request_gets_unique_id(client):
    rids = {client.get("/").headers["X-Request-ID"] for _ in range(20)}
    log_case("缺失标识唯一性", {"count": 20},
             code=None, request_id=sorted(rids)[0], status=200)
    assert len(rids) == 20


def test_request_id_middleware_wraps_server_error_middleware(client):
    """栈结构回归：RequestIDMiddleware 必须在最外层，包裹 ServerErrorMiddleware。

    FastAPI 默认把 Exception/500 处理器放在最外层 ServerErrorMiddleware；
    若请求标识中间件不包在它外面，500 响应就会缺失/错配 request_id。
    """
    from starlette.middleware.errors import ServerErrorMiddleware

    from app.request_context import RequestIDMiddleware

    stack = client.app.build_middleware_stack()
    assert isinstance(stack, RequestIDMiddleware)
    # 向内第一层应能到达框架默认栈（ServerErrorMiddleware 是其最外层）。
    inner = stack.app
    assert isinstance(inner, ServerErrorMiddleware)
