# 统一接口错误响应契约

本服务把**校验失败、业务拒绝、框架层客户端拒绝、未处理异常**归约为同一个错误
信封，并为不同入口（path / query / body / header）的校验失败以及不同类型的
框架层拒绝提供可区分的稳定分类。运行时返回、FastAPI 自动生成的 OpenAPI 文档、
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
| `VALIDATION_BODY` | 请求体解析/字段校验失败 | 422 | 逐字段 |
| `VALIDATION_HEADER` | 请求头校验失败 | 422 | 逐字段 |
| `BUSINESS_RULE_VIOLATION` | 业务规则拒绝 | 409 | `null` |
| `NOT_FOUND` | 业务资源不存在（业务层抛出） | 404 | `null` |
| `ROUTE_NOT_FOUND` | **框架层**：目标地址不存在（请求未进业务代码） | 404 | `null` |
| `METHOD_NOT_ALLOWED` | **框架层**：请求方式与地址对不上 | 405 | `null` |
| `UNAUTHENTICATED` | **框架层**：缺少身份凭据（未认证） | 401 | `null` |
| `ACCESS_DENIED` | **框架层**：已认证但访问被拒绝 | 403 | `null` |
| `UNSUPPORTED_MEDIA_TYPE` | **框架层**：请求体格式（媒体类型）不被支持 | 415 | `null` |
| `INTERNAL_ERROR` | 未处理异常 / 框架 5xx | 500 | `null` |

> 框架层分类（表中加粗“框架层”的五类）描述的是**还没进入业务代码就被
> 框架/守卫挡掉**的请求。未配置覆盖时，它们的响应状态**保留框架真实客户端
> 错误状态**（如真实 405/415），调用方既能凭 `code` 区分拒绝原因，也能凭
> HTTP 状态做通用处理。未显式登记的其它 4xx 框架异常兜底归 `NOT_FOUND`，
> 其它 5xx 框架异常兜底归 `INTERNAL_ERROR`。

- 同一次请求若有多个字段校验失败，信封 `code` 取**首个**错误的来源分类，
  所有字段错误都完整保留在 `details` 中。
- `INTERNAL_ERROR` 对外只返回固定文案，异常类型、异常消息、堆栈、文件路径等
  内部实现细节**绝不**进入响应体；完整堆栈仅写服务端日志并携带 `request_id`。

### 2.1 状态码可配置与覆盖优先级

分类 → 状态码的映射在 `app/config.py` 集中定义，业务代码不感知 HTTP 状态码。
通过环境变量即可覆盖，无需改动业务逻辑：

```bash
# 把请求体校验失败改成 400（Bad Request）
ERROR_STATUS__VALIDATION_BODY=400 \
ERROR_STATUS__BUSINESS_RULE_VIOLATION=422 \
uv run uvicorn main:app

# 框架层分类同样可单独覆盖：把 405 改回 404、把 403 改成 451
ERROR_STATUS__METHOD_NOT_ALLOWED=404 \
ERROR_STATUS__ACCESS_DENIED=451 \
uv run uvicorn main:app
```

规则（优先级从高到低）：

1. **显式入参**：`create_app(build_settings({分类: 状态码}))`，测试/多实例用；
2. **环境变量**：`ERROR_STATUS__<分类名>=<状态码>`，如
   `ERROR_STATUS__VALIDATION_BODY=400`；多个变量同时生效，同名时显式入参更高；
3. **内置默认值**：`DEFAULT_STATUS_MAP`（即文档与 OpenAPI 展示的默认状态）。

其它约定：

- 取值必须是 400–599 的整数、分类名必须已知，否则启动期直接报错（fail fast）；
- 请求标识头名可用 `REQUEST_ID_HEADER` 覆盖（默认 `X-Request-ID`）；
- 配置只改 HTTP 状态码，`code` 与信封结构保持不变；
- **框架层真实状态保留规则**：某框架分类**未被覆盖**时，响应状态取框架抛出的
  真实状态码；一旦该分类被本地配置覆盖（生效值 ≠ 默认值），响应状态以配置值
  为准。因此把 `METHOD_NOT_ALLOWED` 配成 404 后，方法不匹配将返回 404 而
  `code` 仍是 `METHOD_NOT_ALLOWED`。

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

### 3.1 失败日志与请求标识对账

校验失败、业务拒绝、框架层拒绝（404/405/401/403/415）与未处理异常，服务端
都会落一条**同构的结构化失败日志**：

```text
... INFO app.errors [request_id=<标识> source=<client|generated> warning=<...>] \
    request failed request_id=<标识> category=<最终分类> status=<实际返回状态> \
    method=<方法> path=<路径>
```

- `request_id` 即客户端可拿到的最终标识，拿客户端给的标识可直接在日志中反查；
- `category` 是最终错误分类、`status` 是实际写回响应的状态码；
- 标识**缺失**（补全）、**非法**（替换）、**在途重复**（替换）时，中间件另落
  一条 `app.request_id` 日志，同样携带最终标识，日志里即可看出异常原因；
- 未处理异常额外落一条带完整堆栈的 `ERROR`，但堆栈**只留在服务端**，对外
  响应仍是固定文案；
- 这些字段由 `RequestIDLogFilter` 从 `contextvars` 注入，并发压测下每条日志
  只属于它自己的那次请求，不会串号；请求上下文之外的日志标识显示为 `-`。

## 4. 运行时只读自查

不翻代码、不翻文档即可查看当前生效的错误分类清单：

```bash
curl -s http://127.0.0.1:8000/meta/error-catalog | python -m json.tool
```

响应（节选）：

```json
{
  "request_id_header": "X-Request-ID",
  "categories": [
    {
      "code": "METHOD_NOT_ALLOWED",
      "default_status": 405,
      "effective_status": 405,
      "overridden": false
    }
  ],
  "overridden_codes": []
}
```

- `default_status`：内置默认状态，与 OpenAPI 文档声明同源；
- `effective_status`：当前进程真实请求实际返回的状态（含本地覆盖）；
- `overridden` / `overridden_codes`：标明哪些分类被本地配置覆盖过；
- 分类按枚举声明顺序排列，**无覆盖与有覆盖都稳定可读、可重复读取**；
- 纯本地只读接口（`GET`），不依赖任何外部服务；对其使用写方法同样返回
  `METHOD_NOT_ALLOWED` 契约响应。

## 5. 兼容性边界

- **稳定**：信封五个顶层字段及其含义、已发布 `code` 分类枚举值、
  `details[].loc` 的“来源前缀”语义、成功响应结构；
- 本轮**新增** `ROUTE_NOT_FOUND` / `METHOD_NOT_ALLOWED` / `UNAUTHENTICATED` /
  `ACCESS_DENIED` / `UNSUPPORTED_MEDIA_TYPE` 五个分类值，属于契约允许的
  “只增不改不删”演进；旧客户端对未知 `code` 应走通用错误分支；
- **行为变更提示**：框架路由不存在现在归 `ROUTE_NOT_FOUND`（此前归
  `NOT_FOUND`），方法不匹配现在归 `METHOD_NOT_ALLOWED` 且默认返回真实
  **405**（此前两者都归 `NOT_FOUND` 并返回 404）；业务层 `NOT_FOUND`
  分类本身保持不变；
- **可演进**：允许未来继续**新增** `code` 值、允许在响应体中**新增**可选
  字段，旧客户端应忽略不认识的字段、不应依赖字段顺序；
- **不保证**：`message` 文案、`details[].type` 的具体校验器名（跟随
  pydantic/FastAPI 版本）、未登记的状态码映射；
- 已发布的分类**不会被重命名或删除**；调整状态码请走第 2.1 节的配置。

## 6. 示例端点

| 方法 & 路径 | 用于演示 |
| --- | --- |
| `GET /` | 成功响应兼容性 |
| `GET /items/{item_id}?q=&limit=` | `VALIDATION_PATH` / `VALIDATION_QUERY` |
| `POST /users` | `VALIDATION_BODY` 及多字段定位 |
| `GET /boom`（带 `X-Trace` 头） | `VALIDATION_HEADER` |
| `POST /business-reject` | `BUSINESS_RULE_VIOLATION` |
| `GET /boom[?delay_ms=]` | `INTERNAL_ERROR`（不泄露细节）/ 并发失败日志 |
| `GET /echo?delay_ms=` | 并发隔离与在途重复检测 |
| `GET /no-such-path` | `ROUTE_NOT_FOUND`（真实 404） |
| `DELETE /` | `METHOD_NOT_ALLOWED`（真实 405） |
| `GET /admin` | `UNAUTHENTICATED`（缺 `X-User-Role`，真实 401） |
| `GET /admin`（`X-User-Role: guest`） | `ACCESS_DENIED`（真实 403） |
| `POST /users`（非 JSON 媒体类型） | `UNSUPPORTED_MEDIA_TYPE`（真实 415） |
| `GET /meta/error-catalog` | 只读运行时自查 |

## 7. 本地验证

```bash
# 安装依赖（含 dev 测试组）
uv sync --group dev

# 运行全部测试
uv run pytest

# 只跑某一组
uv run pytest tests/test_validation.py -v
uv run pytest tests/test_framework_rejections.py -v   # 框架层拒绝
uv run pytest tests/test_failure_logging.py -v        # 日志与标识对账
uv run pytest tests/test_meta_catalog.py -v           # 只读自查
uv run pytest tests/test_concurrency.py -v            # 并发不串号

# 启动服务并查看自动文档
uv run uvicorn main:app --reload
# Swagger UI:  http://127.0.0.1:8000/docs
# OpenAPI:     http://127.0.0.1:8000/openapi.json
# 只读自查:    http://127.0.0.1:8000/meta/error-catalog

# 手工验证各分类
curl -i http://127.0.0.1:8000/items/not-int            # VALIDATION_PATH
curl -i 'http://127.0.0.1:8000/items/1?limit=x'        # VALIDATION_QUERY
curl -i -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' -d '{}'          # VALIDATION_BODY
curl -i -X POST http://127.0.0.1:8000/business-reject  # BUSINESS_RULE_VIOLATION
curl -i http://127.0.0.1:8000/boom                     # INTERNAL_ERROR

# 手工验证框架层客户端拒绝（真实状态保留）
curl -i http://127.0.0.1:8000/no-such-path             # ROUTE_NOT_FOUND / 404
curl -i -X DELETE http://127.0.0.1:8000/               # METHOD_NOT_ALLOWED / 405
curl -i http://127.0.0.1:8000/admin                    # UNAUTHENTICATED / 401
curl -i -H 'X-User-Role: guest' http://127.0.0.1:8000/admin   # ACCESS_DENIED / 403
curl -i -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: text/plain' -d 'x'                 # UNSUPPORTED_MEDIA_TYPE / 415

# 凭客户端标识反查服务端失败日志
curl -i -H 'X-Request-ID: trace-abc-123' \
  http://127.0.0.1:8000/no-such-path
# 服务端日志中检索：request_id=trace-abc-123

# 验证状态码映射可配置（含框架层分类）
ERROR_STATUS__BUSINESS_RULE_VIOLATION=422 \
ERROR_STATUS__METHOD_NOT_ALLOWED=404 \
  uv run uvicorn main:app
```

测试在 stdout 打印每条用例的**输入、分类结果与追踪标识**
（logger 名 `tests`），失败时可凭 `request_id` 与服务端日志对账。
