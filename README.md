# fastapi-error-contract (Python / FastAPI)

统一接口错误响应契约的落地示例：校验失败、业务拒绝与未处理异常在 path / query /
body / header 各入口下都归约为同一结构、携带可追踪的请求标识，且运行时行为、
OpenAPI 文档与测试三者一致。

## 快速开始

```bash
uv sync --group dev
uv run uvicorn main:app --reload     # http://127.0.0.1:8000/docs
uv run pytest                         # 全部测试
```

## 目录

- `app/errors/` — 分类（`codes.py`）、信封 schema（`schemas.py`）、
  异常处理器（`handlers.py`）、业务异常（`exceptions.py`）；
- `app/config.py` — 分类 → HTTP 状态码映射，支持环境变量覆盖；
- `app/request_context.py` — 请求标识生成/透传/并发重复检测中间件；
- `app/routes.py`、`app/services.py` — 演示入口与业务逻辑；
- `tests/` — 各入口校验、业务拒绝、未处理异常、并发与文档一致性测试；
- [`docs/error-contract.md`](docs/error-contract.md) — 完整契约说明。
