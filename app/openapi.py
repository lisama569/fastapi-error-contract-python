"""OpenAPI 定制：为所有路由补充统一错误响应结构，保证文档与运行时一致。

做法（只追加/改写内容，不删除路由自定义的错误码）：
- 注册 ErrorResponse 组件 schema；
- 422（FastAPI 为校验参数自动生成）统一替换为 ErrorResponse 结构；
- 路由通过 responses= 自行声明的 4xx（如 409 业务拒绝）挂上同一结构；
- 自动补齐 500，确保每个接口都文档化了未处理异常的契约。
"""
from __future__ import annotations

from fastapi import FastAPI

from app.errors import ErrorResponse

_ERROR_CONTENT = {
    "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
}

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def customize_openapi(app: FastAPI) -> None:
    def openapi() -> dict[str, object]:
        # 经由类方法调用默认实现，绕过被覆盖的实例属性以避免递归
        schema: dict[str, object] = app.__class__.openapi(app)  # type: ignore[attr-defined]

        components = schema.setdefault("components", {})  # type: ignore[union-attr]
        schemas = components.setdefault("schemas", {})
        schemas["ErrorResponse"] = ErrorResponse.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        schemas["ErrorResponse"]["title"] = "ErrorResponse"

        for path, path_item in schema.get("paths", {}).items():  # type: ignore[union-attr]
            if not isinstance(path, str) or not path.startswith("/demo"):
                continue
            for method, operation in path_item.items():
                if method not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                responses = operation.setdefault("responses", {})
                if "422" in responses:
                    responses["422"]["content"] = _ERROR_CONTENT
                    responses["422"]["description"] = (
                        "校验失败（path/query/body 统一分类，fields.location 区分入口）"
                    )
                # 路由自行声明的其他 4xx（如 409 业务拒绝）挂同一结构
                for code in list(responses):
                    if str(code).startswith("4") and str(code) != "422":
                        responses[code]["content"] = _ERROR_CONTENT
                responses.setdefault(
                    "500",
                    {"description": "未处理异常（不暴露内部细节）", "content": _ERROR_CONTENT},
                )

        return schema

    app.openapi = openapi  # type: ignore[method-assign]
