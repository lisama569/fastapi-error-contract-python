"""单测日志辅助：统一打印输入、错误分类结果与追踪标识。"""

import logging
import sys

logger = logging.getLogger("tests")

if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    )
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def log_case(case_name: str, payload: object, *, code: str | None = None,
             request_id: str | None = None, status: int | None = None) -> None:
    """打印一次用例的输入、分类结果与追踪标识（单测可观测性要求）。"""
    logger.info(
        "case=%s input=%r -> status=%s code=%s request_id=%s",
        case_name,
        payload,
        status,
        code,
        request_id,
    )
