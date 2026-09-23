# fastapi-error-contract-python

一个在 FastAPI 中落地统一接口错误响应契约的示例：校验失败、业务拒绝与
未处理异常在 path/query/body/header 各入口下都归约为同一结构，分类稳定、
字段可定位、状态码可配置、请求可追踪，并保证运行时行为、OpenAPI 文档与测试一致。

## 快速开始

```bash
uv sync
uv run pytest                 # 运行测试（控制台打印输入/分类/追踪标识）
uv run uvicorn main:app --reload
# 打开 http://127.0.0.1:8000/docs
```

## 文档

详见 [docs/error-contract.md](docs/error-contract.md)，包含：

- 统一错误结构与逐字段定位；
- 错误分类与默认状态码，以及如何通过代码/环境变量调整映射；
- 请求追踪标识的生成、畸形与并发重复检测；
- 兼容性边界与本地验证方法。

## 目录结构

```
app/
  errors.py          # 分类/来源枚举、ErrorResponse 模型、默认状态码映射
  config.py          # 可配置的 分类 -> 状态码 映射（含环境变量）
  request_context.py # 基于 contextvars 的请求标识上下文（并发隔离）
  middleware.py      # 请求标识中间件 + 在上下文内兜底未处理异常
  exceptions.py      # BusinessError
  handlers.py        # 各类异常 -> ErrorResponse 的处理器
  openapi.py         # OpenAPI 文档统一错误结构
  routes.py          # 覆盖各入口的演示路由
  factory.py         # 应用组装
main.py              # ASGI 入口
tests/               # 契约测试与并发测试
```
