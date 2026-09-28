# fastapi-error-contract (Python / FastAPI)

统一接口错误响应契约的落地示例：校验失败、业务拒绝、框架层客户端拒绝
（404/405/401/403/415）与未处理异常在 path / query / body / header 各入口下都
归约为同一结构、携带可追踪的请求标识，失败日志可凭标识反查，并提供只读运行时
自查端点；运行时行为、OpenAPI 文档与测试三者一致。

## 快速开始

```bash
uv sync --group dev
uv run uvicorn main:app --reload     # http://127.0.0.1:8000/docs
uv run pytest                         # 全部测试
curl -s http://127.0.0.1:8000/meta/error-catalog | python -m json.tool
```

## 目录

- `app/errors/` — 分类（`codes.py`）、信封 schema（`schemas.py`）、
  异常处理器（`handlers.py`）、业务异常（`exceptions.py`）；
- `app/config.py` — 分类 → HTTP 状态码映射，支持环境变量覆盖，记录覆盖集；
- `app/request_context.py` — 请求标识生成/透传/并发重复检测中间件，
  以及把标识注入日志的 `RequestIDLogFilter`；
- `app/security.py` — 框架层守卫依赖（真实产生 401/403/415）；
- `app/meta/` — 只读自查端点 `/meta/error-catalog` 与目录构造；
- `app/routes.py`、`app/services.py` — 演示入口与业务逻辑；
- `tests/` — 各入口校验、业务拒绝、框架层拒绝、失败日志对账、并发不串号、
  只读自查与文档一致性测试；
- [`docs/error-contract.md`](docs/error-contract.md) — 完整契约说明。
