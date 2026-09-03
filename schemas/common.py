# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 公共响应模型（schemas/common.py）
=========================================================================
 这里定义两样东西：
   1. ApiResponse[T] —— 所有接口统一使用的「响应信封」
   2. 健康检查相关模型

 为什么要统一信封？
   前端只需要判断 `code === 0` 就知道成功与否，不用去猜 HTTP 状态码；
   `message` 永远是中文的人类可读提示，可以直接弹 toast，
   前端不必为每个接口单独写错误文案。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

# 泛型占位符：ApiResponse[TranslateData]、ApiResponse[GalleryData] 都靠它推导
T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应信封。

    成功示例：
        {"code": 0, "message": "ok", "data": {...}, "timestamp": "..."}
    失败示例：
        {"code": 500, "message": "翻译失败：...", "data": null, "timestamp": "..."}
    """

    code: int = Field(default=0, description="业务状态码：0 = 成功，非 0 = 失败")
    message: str = Field(default="ok", description="人类可读提示，可直接展示给用户")
    data: Optional[T] = Field(default=None, description="业务数据载荷")
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"),
        description="服务端响应时间（ISO 格式）",
    )


def ok(data: T = None, message: str = "ok", code: int = 0) -> ApiResponse[T]:
    """构造成功响应。用法：return ok(data=result, message="翻译完成")"""
    return ApiResponse(code=code, message=message, data=data)


def fail(message: str, code: int = 500, data: Any = None) -> ApiResponse[Any]:
    """构造失败响应。用法：return fail("卡片目录为空", code=404)"""
    return ApiResponse(code=code, message=message, data=data)


# ----------------------------------------------------------------------
# 健康检查模型
# ----------------------------------------------------------------------

class ModuleStatus(BaseModel):
    """单个子模块的状态。"""
    key: str = Field(..., description="模块标识，如 synesthesia")
    name: str = Field(..., description="中文名")
    english: str = Field(..., description="英文代号")
    status: str = Field(..., description="active=已上线 / planned=规划中 / degraded=降级运行")
    version: str = Field(..., description="模块版本")


class HealthData(BaseModel):
    """平台级健康检查的响应数据。"""
    platform: str = Field(..., description="平台名")
    version: str = Field(..., description="平台版本")
    status: str = Field(..., description="healthy=全部正常 / degraded=有模块降级")
    uptime_seconds: float = Field(..., description="服务已运行秒数")
    engines: dict = Field(default_factory=dict, description="各引擎的运行时探测结果")
    modules: List[ModuleStatus] = Field(default_factory=list, description="子模块列表")
    active_count: int = Field(default=0, description="已上线模块数")
    degraded_count: int = Field(default=0, description="降级运行模块数（能响应，但非满血状态）")
    planned_count: int = Field(default=0, description="规划中模块数")
