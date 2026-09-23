# 统一接口错误响应契约

本文档描述服务的错误响应结构、错误分类、分类到 HTTP 状态码的映射规则、
请求追踪标识约定、兼容性边界以及本地验证方法。运行时行为、自动生成的
OpenAPI 文档（`/docs`、`/openapi.json`）与测试三者保持一致。

## 1. 统一错误结构

所有失败响应（校验失败、业务拒绝、未处理异常、路由层错误）都归约为同一 JSON 结构：

```json
{
  "error": "validation_failed",
  "request_id": "8454ff0f4c9a4c7c8ed763f5766f5",
  "message": "request validation failed",
  "fields": [
    {
      "location": "body",
      "field": "items.0.name",
      "message": "String should have at least 1 character"
    }
  ],
  "reason": null
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `error` | string | 稳定的错误分类，取值见第 2 节。客户端应据此分支处理。 |
| `request_id` | string | 本请求的追踪标识，与响应头 `X-Request-ID` 完全一致。 |
| `message` | string | 面向人类的概要信息；未处理异常时固定为 `internal server error`。 |
| `fields` | array | 逐字段定位信息；无字段级错误时为空数组。 |
| `fields[].location` | string | 失败来源：`path` / `query` / `body` / `header`。 |
| `fields[].field` | string | 点分字段路径（如 `items.0.name`）；纯来源级错误可能为空串。 |
| `fields[].message` | string | 该字段的失败原因。 |
| `reason` | string\|null | 业务拒绝时的稳定机器可读原因（如 `item_name_forbidden`），其余为 `null`。 |

成功响应结构保持不变（如 `GET /` 仍返回 `{"Hello": "World"}`，
`POST /demo/items` 成功仍返回 201 与原字段），错误契约只影响失败响应。

## 2. 错误分类（`error`）

| 分类值 | 含义 | 默认状态码 |
| --- | --- | --- |
| `validation_failed` | 请求校验失败。path/query/body/header 各来源使用**同一分类**，通过 `fields[].location` 区分入口。 | `422` |
| `business_rejected` | 通过业务校验但被业务规则拒绝；`reason` 给出稳定原因。 | `409` |
| `internal_error` | 未处理异常。**不对外暴露**堆栈、异常消息、内部地址等实现细节，细节只进服务端日志。 | `500` |
| `not_found` | 路由不存在等 404 场景（追加分类）。 | `404` |
| `request_rejected` | 其他路由层拒绝（追加分类）。 | `400` |

同类校验失败无论来自哪个入口，`error` 都是 `validation_failed`，
保证“同类一致”；而 `fields[].location` 保证“入口可区分”。示例：

- `GET /demo/items/abc`（path 参数非整数）→ `validation_failed` + `location=path`
- `GET /demo/items?page=abc`（query 参数非整数）→ `validation_failed` + `location=query`
- `POST /demo/items` 缺字段/越界/畸形 JSON → `validation_failed` + `location=body`
- `GET /demo/echo-tenant` 带非整数 `X-Tenant-ID` → `validation_failed` + `location=header`

业务侧抛出 `BusinessError(reason, ...)` 即得到 `business_rejected`：

```python
from app.exceptions import BusinessError
from app.errors import ErrorLocation

raise BusinessError(
    "item_name_forbidden",
    message="该名称不允许使用",
    field="name",
    location=ErrorLocation.BODY,
)
```

## 3. 状态码映射可配置

分类与状态码的映射集中在 `app/config.py`，调整映射**不需要改动任何业务逻辑**：

1. 代码传入：`create_app(ErrorContractSettings({ErrorCategory.BUSINESS_REJECTED: 422}))`
2. 环境变量：`ERROR_STATUS_<分类名大写>=<状态码>`，例如
   `ERROR_STATUS_BUSINESS_REJECTED=422`、`ERROR_STATUS_INTERNAL_ERROR=503`。

未显式配置的分类回落到 `DEFAULT_STATUS_MAP`。映射只改变 HTTP 状态码，
响应体中的 `error` 分类保持不变，客户端分类逻辑不受影响。

## 4. 请求追踪标识

- 入站请求头 `X-Request-ID`：
  - 合法（匹配 `^[A-Za-z0-9._-]{8,128}$`）且当前无在飞重复 → 原样采用，
    响应头 `X-Request-ID-Source: client`；
  - 缺失 → 服务端生成 UUID4，`X-Request-ID-Source: generated-missing`；
  - 畸形 → 拒绝原值并重新生成，`X-Request-ID-Source: replaced-malformed`，同时记录告警日志；
  - 与某个**仍在处理中**的请求重复 → 绝不静默复用，重新生成，
    `X-Request-ID-Source: replaced-duplicate`，同时记录告警日志。
- 所有响应（含 500）都回传最终生效的 `X-Request-ID`，且与响应体 `request_id` 一致。
- 标识保存在基于 `contextvars` 的请求上下文中，并在 ASGI 中间件层绑定/重置，
  并发请求各自隔离、互不串扰（见并发测试）。
- 请求结束后标识离开“在飞”集合，同一标识可被后续请求正常复用（非异常）。

异常情况下 `X-Request-ID-Source` 与服务端日志使“缺失/畸形/重复”都可被检测，
而不是静默处理。

## 5. 兼容性边界

- **稳定不变**：`error` / `request_id` / `message` / `fields` 四个既有字段及其含义；
  已有分类的字符串值永不修改。
- **允许追加**：新增分类只能在 `ErrorCategory` 末尾追加新值；
  新增响应字段只能以“带默认值的可选字段”形式追加（如 `reason`）。
  旧客户端应容忍未知的 `error` 取值与未知的额外字段。
- **不允许**：删除/重命名字段、改变既有字段语义、改变既有分类取值。
- 成功响应的状态码与字段语义保持原样，错误契约不改变成功路径。
- 未处理异常对外只返回固定文案，避免泄露内部实现；完整堆栈仅写入服务端日志，
  并以 `request_id` 关联，便于排查。

## 6. OpenAPI 文档一致性

- `ErrorResponse` 注册为 `components.schemas.ErrorResponse`；
- FastAPI 自动生成的 `422` 响应被统一替换为该结构；
- 路由自行声明的 4xx（如 POST `/demo/items` 的 `409`）挂同一结构；
- 每个 `/demo` 接口都自动补齐 `500` 文档。

测试 `test_openapi_documents_error_contract` 会断言文档中的状态码集合与
`$ref` 指向，保证文档与运行时一致。

## 7. 本地验证方法

```bash
# 安装依赖（含 pytest）
uv sync

# 运行全部测试（默认在控制台打印每个用例的输入、分类结果与追踪标识）
uv run pytest

# 仅看契约测试 / 并发测试
uv run pytest tests/test_error_contract.py
uv run pytest tests/test_concurrency.py

# 启动服务
uv run uvicorn main:app --reload
# 然后访问 http://127.0.0.1:8000/docs
```

手工验证示例：

```bash
# path 校验失败
curl -i http://127.0.0.1:8000/demo/items/abc
# query 校验失败
curl -i "http://127.0.0.1:8000/demo/items?page=abc"
# body 校验失败
curl -i -X POST http://127.0.0.1:8000/demo/items \
  -H 'content-type: application/json' -d '{"name":"","quantity":0}'
# 业务拒绝
curl -i -X POST http://127.0.0.1:8000/demo/items \
  -H 'content-type: application/json' -d '{"name":"forbidden","quantity":1}'
# 未处理异常（不泄露细节，带 request_id）
curl -i http://127.0.0.1:8000/demo/boom
# 自定义/畸形/重复请求标识
curl -i -H 'X-Request-ID: my-trace-1234' http://127.0.0.1:8000/
```

用环境变量调整状态码映射：

```bash
ERROR_STATUS_INTERNAL_ERROR=503 uv run uvicorn main:app --reload
```
