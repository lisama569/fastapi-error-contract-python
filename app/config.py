"""可配置的错误分类 -> 状态码映射。

覆盖方式（无需改动业务逻辑）：
1. 构造 ErrorContractSettings(status_map={...}) 传入 create_app；
2. 环境变量 ERROR_STATUS_<CATEGORY>=<code>，例如
   ERROR_STATUS_BUSINESS_REJECTED=422。
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from app.errors import DEFAULT_STATUS_MAP, ErrorCategory

_ENV_PREFIX = "ERROR_STATUS_"


class ErrorContractSettings:
    """错误契约配置。映射为深拷贝的普通 dict，外部修改不会影响默认值。"""

    def __init__(
        self,
        status_map: Mapping[ErrorCategory | str, int] | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        merged: dict[ErrorCategory, int] = dict(DEFAULT_STATUS_MAP)
        if status_map:
            for key, code in status_map.items():
                merged[ErrorCategory(key)] = int(code)
        source = os.environ if env is None else env
        for key, raw in source.items():
            if key.startswith(_ENV_PREFIX):
                name = key[len(_ENV_PREFIX) :]
                try:
                    category = ErrorCategory(name.lower())
                except ValueError:
                    continue
                merged[category] = int(raw)
        self._status_map: dict[ErrorCategory, int] = merged

    def status_for(self, category: ErrorCategory) -> int:
        return self._status_map[category]

    def as_public_dict(self) -> dict[str, int]:
        """供 /docs 之外的调试与文档生成使用。"""
        return {category.value: code for category, code in self._status_map.items()}


def load_settings() -> ErrorContractSettings:
    """从环境变量加载配置（测试与运行时的默认入口）。"""
    return ErrorContractSettings()
