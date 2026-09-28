"""可配置项：错误分类 -> HTTP 状态码映射等，改动配置无需触碰业务逻辑。

配置来源（优先级从高到低）：
1. 显式传入 create_app(settings=...) 的 Settings（测试/多实例用）；
2. 环境变量覆盖：
   - ERROR_STATUS__<分类名>=<状态码>，例如
     ERROR_STATUS__VALIDATION_BODY=400、ERROR_STATUS__BUSINESS_RULE_VIOLATION=422；
   - REQUEST_ID_HEADER=X-Request-ID（默认）。
3. 内置默认值 DEFAULT_STATUS_MAP。

新增分类时，只需在 ErrorCategory 中增加枚举并在 DEFAULT_STATUS_MAP
登记默认状态码；未登记映射会在启动期直接失败（fail fast）。
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache

from app.errors.codes import ErrorCategory

DEFAULT_STATUS_MAP: dict[ErrorCategory, int] = {
    ErrorCategory.VALIDATION_PATH: 422,
    ErrorCategory.VALIDATION_QUERY: 422,
    ErrorCategory.VALIDATION_BODY: 422,
    ErrorCategory.VALIDATION_HEADER: 422,
    ErrorCategory.BUSINESS_RULE_VIOLATION: 409,
    ErrorCategory.NOT_FOUND: 404,
    # 框架层客户端错误：默认状态码即框架给出的真实客户端错误状态。
    ErrorCategory.METHOD_NOT_ALLOWED: 405,
    ErrorCategory.UNAUTHORIZED: 401,
    ErrorCategory.FORBIDDEN: 403,
    ErrorCategory.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCategory.INTERNAL_ERROR: 500,
}

_ENV_STATUS_PREFIX = "ERROR_STATUS__"
_ENV_REQUEST_ID_HEADER = "REQUEST_ID_HEADER"
DEFAULT_REQUEST_ID_HEADER = "X-Request-ID"


def _validate_status(code: object) -> int:
    """校验单个 HTTP 状态码合法（4xx/5xx 才能表达失败）。"""
    value = int(code)  # 类型非法时抛 ValueError
    if not 400 <= value <= 599:
        raise ValueError(f"错误状态码必须位于 400..599，实际为 {value}")
    return value


def _load_status_map_from_env() -> dict[ErrorCategory, int]:
    """读取 ERROR_STATUS__<分类名> 形式的环境变量覆盖。"""
    overrides: dict[ErrorCategory, int] = {}
    for env_name, raw in os.environ.items():
        if not env_name.startswith(_ENV_STATUS_PREFIX):
            continue
        category_name = env_name[len(_ENV_STATUS_PREFIX) :]
        try:
            category = ErrorCategory(category_name)
        except ValueError as exc:
            known = ", ".join(c.value for c in ErrorCategory)
            raise ValueError(
                f"环境变量 {env_name} 指向未知错误分类 {category_name!r}；"
                f"已知分类：{known}"
            ) from exc
        try:
            overrides[category] = _validate_status(raw)
        except ValueError as exc:
            raise ValueError(f"环境变量 {env_name} 取值非法：{exc}") from exc
    return overrides


@dataclass(frozen=True)
class Settings:
    status_map: Mapping[ErrorCategory, int] = field(
        default_factory=lambda: dict(DEFAULT_STATUS_MAP)
    )
    request_id_header: str = DEFAULT_REQUEST_ID_HEADER

    def __post_init__(self) -> None:
        # dataclass(frozen=True) 下用 object.__setattr__ 完成规范化。
        missing = [c.value for c in ErrorCategory if c not in self.status_map]
        if missing:
            raise ValueError(f"以下错误分类缺少状态码映射：{missing}")
        normalized = {c: _validate_status(s) for c, s in self.status_map.items()}
        extra = [c.value for c in normalized if c not in ErrorCategory]
        if extra:
            raise ValueError(f"状态码映射包含未知分类：{extra}")
        header = (self.request_id_header or "").strip()
        if not header:
            raise ValueError("request_id_header 不能为空")
        object.__setattr__(self, "status_map", normalized)
        object.__setattr__(self, "request_id_header", header)

    def status_for(self, category: ErrorCategory) -> int:
        """返回某分类当前生效的 HTTP 状态码。"""
        return self.status_map[category]

    def is_overridden(self, category: ErrorCategory) -> bool:
        """该分类的生效状态码是否相对内置默认值被本地配置覆盖。"""
        return self.status_map[category] != DEFAULT_STATUS_MAP[category]

    def overridden_categories(self) -> list[ErrorCategory]:
        """所有被本地配置覆盖的分类（按枚举声明顺序），供运行时自查。"""
        return [c for c in ErrorCategory if self.is_overridden(c)]


def build_settings(
    status_map: Mapping[ErrorCategory, int] | None = None,
    request_id_header: str | None = None,
) -> Settings:
    """根据默认值 + 环境变量 + 显式入参构造 Settings（入参优先级最高）。"""
    merged: dict[ErrorCategory, int] = dict(DEFAULT_STATUS_MAP)
    merged.update(_load_status_map_from_env())
    if status_map:
        merged.update(status_map)
    header = request_id_header or os.environ.get(
        _ENV_REQUEST_ID_HEADER, DEFAULT_REQUEST_ID_HEADER
    )
    return Settings(status_map=merged, request_id_header=header)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例配置；测试可用 reset_settings() 清缓存。"""
    return build_settings()


def reset_settings() -> None:
    """清除缓存的单例配置（主要给测试用）。"""
    get_settings.cache_clear()
