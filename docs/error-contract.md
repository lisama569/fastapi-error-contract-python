# 统一接口错误响应契约

本服务把**校验失败、业务拒绝、未处理异常**归约为同一个错误信封，并为不同入口
（path / query / body / header）的校验失败提供可区分的稳定分类。运行时返回、
FastAPI 自动生成的 OpenAPI 文档与测试用例三者描述同一份契约。

## 1. 统一错误信封

任何失败响应（4xx/5xx）的响应体都是如下 JSON 结构：

```json
{
  "error": "error",
  "code": "VALIDATION_BODY",
  "message": "请求参数校验失败",
  "request_id": "27dee91d0d584f888df53f309e463746",
  "details": [
    {
      "loc": ["body", "age"],
      "message": "Input should be a valid integer",
      "type": "int_parsing"
    }
  ]
}
```

| 字段 | 类型 | 说明 | 稳定性 |
| --- | --- | --- | --- |
| `error` | `"error"` 常量 | 固定标记位，客户端据此判别错误信封 | 永远存在、值固定 |
| `code` | string | 稳定的错误分类（见下表），**客户端分支判断应只依赖它** | 只增不改不删 |
| `message` | string | 面向人的概要信息 | 可能随版本调整文案，勿用于判断 |
| `request_id` | string | 请求追踪标识，与响应头 `X-Request-ID` 完全一致 | 永远存在、非空 |
| `details` | array \| null | 逐字段定位信息；非校验类错误为 `null` | 列表项结构稳定 |

`details[].loc` 为“来源 + 字段路径”，例如 `["query", "limit"]`、
`["body", "addresses", 0, "zip"]`，与 `code` 配合既可粗粒度分类、也可精确定位。

成功响应（2xx）**不包含** `error` 字段，既有成功结构（如
`GET / -> {"Hello": "World"}`）保持不变。

## 2. 错误分类与默认状态码

| `code` | 含义 | 默认 HTTP 状态码 | `details` |
| --- | --- | --- | --- |
| `VALIDATION_PATH` | 路径参数校验失败（如 `/items/{item_id}` 非整数） | 422 | 逐字段 |
| `VALIDATION_QUERY` | 查询参数校验失败 | 422 | 逐字段 |
| `VALIDATION_BODY` | 请求体校验失败 | 422 | 逐字段 |
| `VALIDATION_HEADER` | 请求头校验失败 | 422 | 逐字段 |
| `BUSINESS_RULE_VIOLATION` | 业务规则拒绝 | 409 | `null` |
| `NOT_FOUND` | 路由/资源不存在（含 405 方法不允许） | 404 | `null` |
| `INTERNAL_ERROR` | 未处理异常 | 500 | `null` |

- 同一次请求若有多个字段校验失败，信封 `code` 取**首个**错误的来源分类，
  所有字段错误都完整保留在 `details` 中。
- `INTERNAL_ERROR` 对外只返回固定文案，异常类型、异常消息、堆栈、文件路径等
  内部实现细节**绝不**进入响应体；完整堆栈仅写服务端日志并携带 `request_id`。

### 2.1 状态码可配置

分类 → 状态码的映射在 `app/config.py` 集中定义，业务代码不感知 HTTP 状态码。
通过环境变量即可覆盖，无需改动业务逻辑：

```bash
# 把请求体校验失败改成 400（Bad Request）
ERROR_STATUS__VALIDATION_BODY=400 \
ERROR_STATUS__BUSINESS_RULE_VIOLATION=422 \
uv run uvicorn main:app
```

规则：

- 环境变量名形如 `ERROR_STATUS__<分类名>`，取值必须是 400–599 的整数，
  否则启动期直接报错（fail fast）；
- 请求标识头名可用 `REQUEST_ID_HEADER` 覆盖（默认 `X-Request-ID`）；
- 也可在代码中 `create_app(build_settings({...}))` 显式注入；
- 配置只改 HTTP 状态码，`code` 与信封结构保持不变。

## 3. 请求追踪标识（request id）

每个 HTTP 响应都带以下响应头：

| 响应头 | 取值 | 触发条件 |
| --- | --- | --- |
| `X-Request-ID` | 本次请求最终使用的标识 | 总是存在 |
| `X-Request-ID-Source` | `client` / `generated` | 总是存在 |
| `X-Request-ID-Warning` | `missing` / `invalid` / `duplicate-in-flight` | 仅异常情况 |

行为约定：

1. **标识缺失**：服务端生成 `uuid4` 标识，`Source=generated`、
   `Warning=missing`，绝不留空；
2. **客户端透传**：标识合法（`[A-Za-z0-9._:=-]{1,128}`）时原样使用，
   `Source=client`，无告警头；
3. **格式非法**：不静默复用，替换为新生成标识，`Warning=invalid` 并记录
   warning 日志（含被拒绝的原始值）；
4. **并发重复（在途串扰信号）**：当同一标识在另一个请求尚未结束时再次到达，
   后到请求的标识被替换，`Warning=duplicate-in-flight` 并记录 warning 日志。
   先到请求不受影响；
5. 标识保存在 `contextvars` 中，每个请求独立上下文；并发下响应头与响应体
   中的 `request_id` 与该请求自身严格一致，互不串扰；
6. 无论成功还是失败（含 500），响应头与响应体中的 `request_id` 必须一致。

## 4. 兼容性边界

- **稳定**：信封五个顶层字段及其含义、`code` 分类枚举值、`details[].loc`
  的“来源前缀”语义、成功响应结构；
- **可演进**：允许未来**新增** `code` 值、允许在响应体中**新增**可选字段，
  旧客户端应忽略不认识的字段、不应依赖字段顺序；
- **不保证**：`message` 文案、`details[].type` 的具体校验器名（跟随
  pydantic/FastAPI 版本）、未在本文档登记的状态码映射；
- 已发布的分类**不会被重命名或删除**；调整状态码请走第 2.1 节的配置。

## 5. 示例端点

| 方法 & 路径 | 用于演示 |
| --- | --- |
| `GET /` | 成功响应兼容性 |
| `GET /items/{item_id}?q=&limit=` | `VALIDATION_PATH` / `VALIDATION_QUERY` |
| `POST /users` | `VALIDATION_BODY` 及多字段定位 |
| `GET /boom`（带 `X-Trace` 头） | `VALIDATION_HEADER` |
| `POST /business-reject` | `BUSINESS_RULE_VIOLATION` |
| `GET /boom` | `INTERNAL_ERROR`（不泄露细节） |
| `GET /echo?delay_ms=` | 并发隔离与在途重复检测 |

## 6. 本地验证

```bash
# 安装依赖（含 dev 测试组）
uv sync --group dev

# 运行全部测试
uv run pytest

# 只跑某一组
uv run pytest tests/test_validation.py -v

# 启动服务并查看自动文档
uv run uvicorn main:app --reload
# Swagger UI:  http://127.0.0.1:8000/docs
# OpenAPI:     http://127.0.0.1:8000/openapi.json

# 手工验证各分类
curl -i http://127.0.0.1:8000/items/not-int            # VALIDATION_PATH
curl -i 'http://127.0.0.1:8000/items/1?limit=x'        # VALIDATION_QUERY
curl -i -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' -d '{}'          # VALIDATION_BODY
curl -i -X POST http://127.0.0.1:8000/business-reject  # BUSINESS_RULE_VIOLATION
curl -i http://127.0.0.1:8000/boom                     # INTERNAL_ERROR

# 验证状态码映射可配置
ERROR_STATUS__BUSINESS_RULE_VIOLATION=422 \
  uv run uvicorn main:app
```

测试在 stdout 打印每条用例的**输入、分类结果与追踪标识**
（logger 名 `tests`），失败时可凭 `request_id` 与服务端日志对账。
