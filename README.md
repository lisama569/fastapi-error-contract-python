# fastapi-error-contract (Python / FastAPI)

统一接口错误响应契约的落地示例：校验失败、业务拒绝、框架层客户端拒绝
（地址不存在 404 / 方法不允许 405 / 未认证 401 / 无权限 403 / 媒体类型 415 /
其它 4xx 如 413）与未处理异常在 path / query / body / header 各入口下都
归约为同一结构、携带可追踪的请求标识；框架层“地址不存在”
（`ROUTE_NOT_FOUND`）与业务“没查到数据”（`NOT_FOUND`）分类各自独立，
框架层各类默认保留真实状态码、可按分类单独配置覆盖且互不影响；
失败日志与请求标识一一对账，并提供只读运行时自查端点。
运行时行为、OpenAPI 文档、自查输出与测试四者一致。

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
- `app/dependencies.py` — 框架层守卫演示（认证/授权 401/403、媒体类型 415、
  请求体大小 413）；
- `app/introspection.py` — 只读自查端点 `GET /meta/error-contract`；
- `app/routes.py`、`app/services.py` — 演示入口与业务逻辑；
- `tests/` — 各入口校验、业务拒绝、框架层拒绝、失败日志对账、并发不串号、
  配置覆盖、自查端点与文档一致性测试；
- [`docs/error-contract.md`](docs/error-contract.md) — 完整契约说明。
