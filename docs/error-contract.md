# 统一接口错误响应契约

本服务把**校验失败、业务拒绝、框架层客户端拒绝、未处理异常**归约为同一个错误
信封，并为不同入口（path / query / body / header）的校验失败、不同类型的框架
层拒绝提供可区分的稳定分类。运行时返回、FastAPI 自动生成的 OpenAPI 文档、
只读自查端点与测试用例描述同一份契约。

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
| `NOT_FOUND` | 目标地址/资源不存在 | 404 | `null` |
| `METHOD_NOT_ALLOWED` | 请求方式与地址不匹配（如对只读地址发 POST） | 405 | `null` |
| `UNAUTHORIZED` | 缺少身份凭证或凭证无效（未认证） | 401 | `null` |
| `FORBIDDEN` | 已认证但访问被拒绝（无权限） | 403 | `null` |
| `UNSUPPORTED_MEDIA_TYPE` | 请求体格式/媒体类型不被该接口支持 | 415 | `null` |
| `INTERNAL_ERROR` | 未处理异常 | 500 | `null` |

- `METHOD_NOT_ALLOWED` / `UNAUTHORIZED` / `FORBIDDEN` / `UNSUPPORTED_MEDIA_TYPE`
  用于描述**请求还没进入业务代码就被框架/守卫挡下**的情况；它们与
  `NOT_FOUND`、彼此之间分类两两不同，调用方可以只凭 `code` 区分“地址不存在”
  “方法不对”“没登录”“没权限”“请求体格式不对”。
- 这些框架层分类的默认状态码**保留框架给出的真实客户端错误状态**
  （405/401/403/415），不会被统一改写成其他状态。
- 同一次请求若有多个字段校验失败，信封 `code` 取**首个**错误的来源分类，
  所有字段错误都完整保留在 `details` 中。
- `INTERNAL_ERROR` 对外只返回固定文案，异常类型、异常消息、堆栈、文件路径等
  内部实现细节**绝不**进入响应体；完整堆栈仅写服务端日志并携带 `request_id`。

### 2.1 状态码可配置与优先级

分类 → 状态码的映射在 `app/config.py` 集中定义，业务代码不感知 HTTP 状态码。
通过环境变量即可覆盖，无需改动业务逻辑：

```bash
# 把请求体校验失败改成 400（Bad Request）
ERROR_STATUS__VALIDATION_BODY=400 \
ERROR_STATUS__BUSINESS_RULE_VIOLATION=422 \
uv run uvicorn main:app

# 新增的框架层分类同样可单独覆盖（例如把 405 改成 404 对外收敛）
ERROR_STATUS__METHOD_NOT_ALLOWED=404 \
ERROR_STATUS__FORBIDDEN=404 \
uv run uvicorn main:app
```

规则与优先级（高 → 低）：

1. **显式入参**：`create_app(build_settings({ErrorCategory.FORBIDDEN: 404}))`
   （测试 / 多实例装配时使用，优先级最高）；
2. **环境变量**：`ERROR_STATUS__<分类名>=<状态码>`，对所有分类一视同仁，
   包括本轮新增的框架层分类；
3. **内置默认**：`DEFAULT_STATUS_MAP`（框架层分类默认即真实客户端状态）。

其余约束：

- 环境变量取值必须是 400–599 的整数，分类名未知或取值非法都会在启动期
  直接报错（fail fast）；
- 请求标识头名可用 `REQUEST_ID_HEADER` 覆盖（默认 `X-Request-ID`）；
- 配置只改 HTTP 状态码，`code` 与信封结构保持不变——因此旧客户端按 `code`
  的分支判断不受任何影响；
- 哪些分类相对默认值被覆盖过，可直接由第 4 节的自查端点查看。

## 3. 请求追踪标识（request id）与失败日志对账

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
   warning 日志（含被拒绝的原始值 `rejected_value` 与最终标识 `final`）；
4. **并发重复（在途串扰信号）**：当同一标识在另一个请求尚未结束时再次到达，
   后到请求的标识被替换，`Warning=duplicate-in-flight` 并记录 warning 日志
   （含原始值与替换后标识）。先到请求不受影响；
5. 标识保存在 `contextvars` 中，每个请求独立上下文；并发下响应头与响应体
   中的 `request_id` 与该请求自身严格一致，互不串扰；
6. 无论成功还是失败（含 500），响应头与响应体中的 `request_id` 必须一致。

### 3.1 失败日志对账

每一类失败——校验失败、业务拒绝、框架层拒绝（404/405/401/403/415）、
未处理异常——服务端都落**一条**统一结构的日志：

```text
request failed request_id=<本次请求标识> category=<最终分类> status=<实际返回状态> detail=<补充信息>
```

- `request_id` 与客户端收到的响应头 / 响应体标识完全相同，可凭客户端回传的
  标识直接反查到这条日志；
- 日志过滤器还会把 `request_id` 作为结构化字段注入该请求内的**所有** app 日志
  （可用 `%(request_id)s` 格式化），上下文外记为 `-`；
- `category` 是最终错误分类，`status` 是**实际写回响应**的状态码
  （含配置覆盖后的值）；
- 标识缺失 / 非法 / 并发重复时，另有一条带 `warning=...`、`rejected_value`、
  `final` 的日志，日志里就能看出最终采用了哪个标识；
- 未处理异常以 ERROR + 完整堆栈落日志，对外仍只给固定文案；
- 并发压测时 `request_id` 取自 contextvars，每条日志只属于自己的请求，
  不会串到并发中的其他请求。

## 4. 只读运行时自查：`GET /meta/error-contract`

不翻代码、不翻文档、不依赖外部服务，直接查看进程内当前生效的契约：

```json
{
  "request_id_header": "X-Request-ID",
  "categories": [
    {"code": "VALIDATION_BODY", "default_status": 422, "effective_status": 400, "overridden": true},
    {"code": "NOT_FOUND", "default_status": 404, "effective_status": 404, "overridden": false},
    {"code": "METHOD_NOT_ALLOWED", "default_status": 405, "effective_status": 405, "overridden": false}
  ],
  "overridden_categories": ["VALIDATION_BODY"]
}
```

- `categories` 按 `ErrorCategory` 枚举声明顺序稳定输出，每类给出内置默认
  状态码 `default_status`、当前实际生效状态码 `effective_status`、以及是否被
  本地配置覆盖 `overridden`；
- `overridden_categories` 是所有覆盖项的便捷清单；没有任何覆盖时为空数组，
  输出依然稳定可读；
- 该端点只读、纯内存计算，数据来源与真实请求的状态码映射、
  `/openapi.json` 自动文档完全一致（同一份 `Settings`）。

## 5. 兼容性边界

- **稳定**：信封五个顶层字段及其含义、`code` 分类枚举值（只增不改不删）、
  `details[].loc` 的“来源前缀”语义、成功响应结构；
- **本轮新增为纯增量**：新增的 4 个框架层分类、自查端点、响应头行为均不改变
  既有分类的默认状态与成功接口；旧客户端忽略不认识的 `code` / 字段即可，
  其对既有 `code` 与成功响应的解析完全不受影响；
- **可演进**：允许未来**新增** `code` 值、允许在响应体中**新增**可选字段，
  旧客户端应忽略不认识的字段、不应依赖字段顺序；
- **不保证**：`message` 文案、`details[].type` 的具体校验器名（跟随
  pydantic/FastAPI 版本）、未在本文档登记的状态码映射；
- 已发布的分类**不会被重命名或删除**；调整状态码请走第 2.1 节的配置，
  当前覆盖情况可由第 4 节自查端点核对。

## 6. 示例端点

| 方法 & 路径 | 用于演示 |
| --- | --- |
| `GET /` | 成功响应兼容性 |
| `GET /items/{item_id}?q=&limit=` | `VALIDATION_PATH` / `VALIDATION_QUERY` |
| `POST /items/{item_id}` | `METHOD_NOT_ALLOWED`（方法与地址不匹配，405） |
| `POST /users` | `VALIDATION_BODY` 及多字段定位 |
| `POST /users`（`Content-Type: text/plain`） | `UNSUPPORTED_MEDIA_TYPE`（415） |
| `GET /admin` | `UNAUTHORIZED`（401，无/坏凭证） |
| `GET /admin`（`Authorization: Bearer user-secret`） | `FORBIDDEN`（403） |
| `GET /admin`（`Authorization: Bearer admin-secret`） | 守卫通过后的成功响应 |
| `GET /boom`（带 `X-Trace` 头） | `VALIDATION_HEADER` |
| `POST /business-reject` | `BUSINESS_RULE_VIOLATION` |
| `GET /no-such-path` | `NOT_FOUND`（404） |
| `GET /boom` | `INTERNAL_ERROR`（不泄露细节） |
| `GET /echo?delay_ms=` | 并发隔离与在途重复检测 |
| `GET /meta/error-contract` | 只读运行时自查 |

## 7. 本地验证

```bash
# 安装依赖（含 dev 测试组）
uv sync --group dev

# 运行全部测试
uv run pytest

# 只跑某一组
uv run pytest tests/test_framework_errors.py -v   # 框架层客户端错误
uv run pytest tests/test_failure_logs.py -v       # 失败日志对账
uv run pytest tests/test_introspection.py -v      # 运行时自查

# 启动服务并查看自动文档
uv run uvicorn main:app --reload
# Swagger UI:  http://127.0.0.1:8000/docs
# OpenAPI:     http://127.0.0.1:8000/openapi.json
# 运行时自查:   http://127.0.0.1:8000/meta/error-contract

# 手工验证各分类
curl -i http://127.0.0.1:8000/items/not-int            # VALIDATION_PATH 422
curl -i -X POST http://127.0.0.1:8000/items/1          # METHOD_NOT_ALLOWED 405
curl -i http://127.0.0.1:8000/no-such-path             # NOT_FOUND 404
curl -i http://127.0.0.1:8000/admin                    # UNAUTHORIZED 401
curl -i -H 'Authorization: Bearer user-secret' \
  http://127.0.0.1:8000/admin                          # FORBIDDEN 403
curl -i -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: text/plain' -d 'x'                 # UNSUPPORTED_MEDIA_TYPE 415
curl -i -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' -d '{}'          # VALIDATION_BODY 422
curl -i -X POST http://127.0.0.1:8000/business-reject  # BUSINESS_RULE_VIOLATION 409
curl -i http://127.0.0.1:8000/boom                     # INTERNAL_ERROR 500

# 拿客户端标识反查服务端日志（失败日志统一含 request_id/category/status）
curl -i http://127.0.0.1:8000/items/x -H 'X-Request-ID: demo-trace-1'
# 服务端日志可见：request failed request_id=demo-trace-1 \
#   category=VALIDATION_PATH status=422 ...

# 验证状态码映射可配置 + 自查端点同步反映
ERROR_STATUS__METHOD_NOT_ALLOWED=404 \
  uv run uvicorn main:app
curl -s http://127.0.0.1:8000/meta/error-contract \
  | python -m json.tool
```

测试在 stdout 打印每条用例的**输入、分类结果与追踪标识**
（logger 名 `tests`），失败时可凭 `request_id` 与服务端日志对账。
并发不串号由 `tests/test_concurrency.py` 与
`tests/test_failure_logs_concurrency.py` 共同保证。
