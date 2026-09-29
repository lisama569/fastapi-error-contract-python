# fastapi-error-contract (Python / FastAPI)

统一接口错误响应契约的落地示例：校验失败、业务拒绝（含业务侧资源不存在
NOT_FOUND）、框架层客户端拒绝（地址写错 ROUTE_NOT_FOUND 404、405/401/403/
415、其它 4xx 兜底 CLIENT_ERROR 透传真实状态）与未处理异常在
path / query / body / header 各入口下都归约为同一结构、携带可追踪的请求
标识；框架层各分类可按分类单独配置状态码且互不影响，失败日志与请求标识
一一对账（含框架原始状态留痕），并提供只读运行时自查端点。运行时行为、
OpenAPI 文档、自查输出与测试四者一致。

## 快速开始

```bash
uv sync --group dev
uv run uvicorn main:app --reload     # http://127.0.0.1:8000/docs
uv run pytest                        # 全部测试
curl -s http://127.0.0.1:8000/meta/error-contract | python -m json.tool
```

## 目录

- `app/errors/` — 分类（`codes.py`）、信封 schema（`schemas.py`）、
  异常处理器（`handlers.py`）、业务异常（`exceptions.py`）；
- `app/config.py` — 分类 → HTTP 状态码映射，支持环境变量覆盖，并记录覆盖项；
- `app/request_context.py` — 请求标识生成/透传/并发重复检测中间件，
  以及把 `request_id` 注入日志记录的过滤器；
- `app/dependencies.py` — 框架层守卫演示（认证/授权 401/403、媒体类型 415）；
- `app/introspection.py` — 只读自查端点 `GET /meta/error-contract`；
- `app/routes.py`、`app/services.py` — 演示入口与业务逻辑；
- `tests/` — 各入口校验、业务拒绝、框架层拒绝、按分类配置的影响边界、
  失败日志对账（含覆盖后 status/http_status 一致性）、并发不串号、
  配置覆盖、自查端点与文档一致性测试；
- [`docs/error-contract.md`](docs/error-contract.md) — 完整契约说明。
