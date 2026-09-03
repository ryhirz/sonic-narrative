# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 翻译器模块的请求 / 响应模型（schemas/synesthesia.py）
=========================================================================
 用 pydantic 把「接口契约」写死，好处有三：
   1. 自动生成 Swagger 文档（/docs），前端不用再来问你字段叫什么
   2. 入参自动校验，非法值直接 422，不用在业务代码里写一堆 if
   3. 出参结构固定，前端可以放心地按字段取值

 【一个重要的分层设计】
   注意这里的 AudioFeatures 是【严格校验】：越界直接拒绝（422）。
   而 synesthesia_core.normalize_features() 是【宽容清洗】：越界夹回区间。
   两者不矛盾，而是纵深防御：
     · API 网关层 —— 严格，对外部调用者负责，错了就明确告诉他
     · 核心引擎层 —— 宽容，对内部批处理负责，脏数据也不能让流水线断
=========================================================================
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AudioFeatures(BaseModel):
    """5 个 Spotify 音频特征（严格校验版）。"""

    valence: float = Field(..., ge=0.0, le=1.0, description="情绪积极度：0 = 阴郁内省，1 = 明亮愉悦")
    energy: float = Field(..., ge=0.0, le=1.0, description="能量强度：0 = 静止，1 = 激烈")
    danceability: float = Field(..., ge=0.0, le=1.0, description="舞蹈性：越高越有律动")
    acousticness: float = Field(..., ge=0.0, le=1.0, description="原声度：越高越自然/不插电")
    tempo: float = Field(..., ge=40.0, le=220.0, description="速度 BPM，合法区间 40~220")

    # 这个示例会显示在 /docs 的 Request body 里，点一下就能直接测试
    model_config = {
        "json_schema_extra": {
            "example": {
                "valence": 0.81,
                "energy": 0.83,
                "danceability": 0.56,
                "acousticness": 0.01,
                "tempo": 171.0,
            }
        }
    }


class TrackMeta(BaseModel):
    """歌曲信息（可选，不传也能翻译，只是卡片上显示"未知曲目"）。"""
    track_name: str = Field(default="未知曲目", max_length=120, description="歌曲名")
    artist: str = Field(default="未知艺人", max_length=120, description="艺人")


class TranslateRequest(BaseModel):
    """POST /api/v1/synesthesia/translate 的请求体。"""
    features: AudioFeatures = Field(..., description="音频特征")
    meta: TrackMeta = Field(default_factory=TrackMeta, description="歌曲信息")
    use_llm: Optional[bool] = Field(
        default=None,
        description="是否调用大模型：null = 自动（有 API Key 就用）；"
                    "false = 强制 Mock（演示 / 省额度时很有用）",
    )
    save: bool = Field(default=False, description="是否顺带把卡片存进 output_cards/ 目录")

    model_config = {
        "json_schema_extra": {
            "example": {
                "features": {
                    "valence": 0.81, "energy": 0.83, "danceability": 0.56,
                    "acousticness": 0.01, "tempo": 171.0,
                },
                "meta": {"track_name": "Blinding Lights", "artist": "The Weeknd"},
                "use_llm": None,
                "save": False,
            }
        }
    }


class SenseCard(BaseModel):
    """引擎产出的通感数据（5 个字段，与 synesthesia_core 对齐）。"""
    scene_title: str = Field(..., description="4~6 字场景名")
    emojis: List[str] = Field(..., description="3 个 emoji")
    color_palette: List[str] = Field(..., description="3 个 HEX 颜色码")
    sensory_description: str = Field(..., description="第二人称沉浸式描述")
    ai_art_prompt: str = Field(..., description="Midjourney 英文提示词")


class TranslateData(BaseModel):
    """翻译结果载荷。"""
    card: SenseCard = Field(..., description="通感数据")
    mode: str = Field(..., description="llm = 真实大模型生成；mock = 本地兜底预设")
    model: Optional[str] = Field(default=None, description="实际使用的模型名")
    html: str = Field(..., description="渲染好的完整 HTML 卡片源码（前端可直接 newWindow.write 或 iframe）")
    html_length: int = Field(..., description="HTML 字符数，方便前端预估体积")
    saved_path: Optional[str] = Field(default=None, description="若 save=true，返回落盘路径")
    features: dict = Field(..., description="回显清洗后的特征（方便前端核对）")
    meta: dict = Field(..., description="回显歌曲信息")


class GalleryItem(BaseModel):
    """画廊里的一张样本卡片。"""
    file: str = Field(..., description="卡片文件名，如 01_Blinding Lights.html")
    track: str = Field(..., description="歌曲名")
    artist: str = Field(..., description="艺人")
    bpm: str = Field(..., description="速度")
    scene_title: str = Field(..., description="场景名")
    emojis: List[str] = Field(..., description="3 个 emoji")
    palette: List[str] = Field(..., description="3 个 HEX 颜色")
    desc: str = Field(..., description="沉浸式描述")
    prompt: str = Field(..., description="AI 绘画提示词")
    warmth: float = Field(default=0.0, description="色温指标（R-B 均值）：正=偏暖，负=偏冷")
    url: Optional[str] = Field(default=None, description="可直接访问的静态地址 /cards/xxx.html")


class GalleryData(BaseModel):
    """GET /api/v1/synesthesia/gallery 的响应数据。"""
    total: int = Field(..., description="目录中卡片总数")
    returned: int = Field(..., description="本次返回的条数")
    items: List[GalleryItem] = Field(..., description="样本列表")
