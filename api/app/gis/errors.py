from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal, TypeAlias


ErrorCode: TypeAlias = Literal[
    "POINT_OUTSIDE_COVERAGE",
    "INVALID_MODE",
    "INVALID_DURATION",
    "VALHALLA_UNAVAILABLE",
    "SPATIAL_DATA_UNAVAILABLE",
    "ANALYSIS_TIMEOUT",
]

ERROR_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "POINT_OUTSIDE_COVERAGE": "所选位置不在当前覆盖范围内",
        "INVALID_MODE": "暂不支持该交通方式",
        "INVALID_DURATION": "通勤时间必须为 15、30、45 或 60 分钟",
        "VALHALLA_UNAVAILABLE": "路网分析服务暂不可用",
        "SPATIAL_DATA_UNAVAILABLE": "空间数据服务暂不可用",
        "ANALYSIS_TIMEOUT": "分析请求超时，请稍后重试",
    }
)


ERROR_CODES: Final = frozenset(ERROR_MESSAGES)


class GisError(Exception):
    """可直接映射为 API 错误响应的安全异常。"""

    def __init__(self, code: str, message: str | None = None, status_code: int = 500) -> None:
        if code not in ERROR_MESSAGES:
            raise ValueError(f"不支持的 GIS 错误码: {code}")
        if not 100 <= status_code <= 599:
            raise ValueError("HTTP 状态码必须在 100 到 599 之间")
        safe_message = ERROR_MESSAGES[code]
        super().__init__(safe_message)
        self.code = code
        self.message = safe_message
        self.status_code = status_code

    def as_dict(self) -> dict[str, str]:
        """仅返回稳定错误字段，避免将异常堆栈或内部连接信息带入响应。"""
        return {"code": self.code, "message": self.message}

    to_dict = as_dict
    to_response = as_dict
