# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 反向寻歌模块的请求 / 响应模型（schemas/recommender.py）
=========================================================================
 与翻译器模块的分层保持一致：
   · 请求侧严格校验 —— 空描述、超长描述、越界 top_k 直接 422 拒掉
   · 响应侧结构固定 —— 前端可以按字段直接取值，不用猜

 【为什么 features 在响应里不做严格区间校验？】
   这里的特征是"反推出来的结果"，不是用户输入。引擎内部已经做过
   饱和累加 + 区间夹取，出来的值一定合法；再校验一遍纯属重复劳动。
   真要防的是【引擎返回了意外结构】，用 dict 承载即可，
   因为前端本来就是逐个字段读的。
=========================================================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ReverseRequest(BaseModel):
    """POST /api/v1/recommender/reverse 的请求体。"""

    text: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="一段感受描述，例如「深夜独自开车，霓虹在车窗外流过」",
    )
    top_k: int = Field(
        default=5, ge=1, le=20,
        description="返回前几首最贴切的歌（1~20）",
    )
    use_llm: Optional[bool] = Field(
        default=None,
        description="是否调用大模型反推特征："
                    "null = 自动（有 API Key 就用，失败自动降级为关键词规则）；"
                    "false = 强制关键词规则（离线演示 / 省额度时用）",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "深夜独自开车，霓虹在车窗外流过",
                "top_k": 5,
                "use_llm": None,
            }
        }
    }


class ReverseMatch(BaseModel):
    """一首被匹配到的歌。"""
    track_name: str = Field(..., description="歌曲名")
    artist: str = Field(..., description="艺人")
    album: str = Field(default="", description="专辑名")
    popularity: float = Field(default=0.0, description="热度 0~100")
    features: Dict[str, Any] = Field(..., description="这首歌的 5 个音频特征")
    distance: float = Field(..., description="与目标特征的加权距离，越小越像")
    match_score: float = Field(..., description="匹配度 0~100，越大越像")


class ReverseData(BaseModel):
    """反向寻歌的结果载荷。"""
    query: str = Field(..., description="原始感受描述（回显）")
    features: Dict[str, Any] = Field(..., description="反推出的 5 个音频特征")
    mode: str = Field(..., description="llm = 大模型反推；keyword = 关键词规则兜底")
    reason: str = Field(..., description="为什么推出这组特征（可解释性）")
    library_size: int = Field(..., description="参与匹配的曲库大小（已过滤脏数据）")
    library_note: Optional[str] = Field(
        default=None,
        description="曲库提示：曲库偏小时会说明结果代表性有限",
    )
    matches: List[ReverseMatch] = Field(..., description="按匹配度降序排列的歌曲列表")


class LibraryStatsData(BaseModel):
    """GET /api/v1/recommender/library 的响应数据。"""
    total: int = Field(..., description="CSV 总行数")
    valid: int = Field(..., description="通过质量过滤的有效行数")
    filtered: int = Field(..., description="被过滤掉的脏数据行数")
