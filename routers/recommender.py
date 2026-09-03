# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 反向寻歌路由（routers/recommender.py）
=========================================================================
 对外接口：
   POST /api/v1/recommender/reverse   一段感受 → 最贴切的那首歌
   GET  /api/v1/recommender/library   曲库概况（总共多少首 / 过滤了多少脏数据）

 这是平台的【第二个子模块】，也是 v1 正向翻译的逆运算：

   正向（v1 已上线）：音频特征 ──→ 视觉场景
   反向（本模块）  ：感受描述 ──→ 音频特征 ──→ 曲库里最贴切的歌

 两步走：
   1. 描述 → 特征：交给大模型理解语义；没 Key 或调用失败就自动降级到关键词规则
   2. 特征 → 歌曲：加权欧氏距离最近邻（tempo 必须归一化，否则 BPM 的量纲
      是 40~220，会把 0~1 的其他四个特征完全淹没）

 ⚠️ 与翻译器相同的坑：这里同样【故意写成普通 def】。
    _features_from_llm() 内部要发 HTTP 请求调大模型，是同步阻塞的。
    写成 async def 会把事件循环卡死，导致同一时刻其他所有请求排队等待。
    写成 def，FastAPI 会自动丢进线程池，服务始终能响应。
=========================================================================
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

# ⚠️ 导入顺序不能反：core.config 会先把项目根加进 sys.path，
#    后面的 `import reverse_engine` 才能成功。
from core.config import FEATURE_CSV
from core.logging_config import get_logger
from schemas.common import ApiResponse, ok
from schemas.recommender import (
    LibraryStatsData,
    ReverseData,
    ReverseMatch,
    ReverseRequest,
)

import reverse_engine  # 反向寻歌引擎

logger = get_logger(__name__)

# prefix 只写到这里，/api/v1 由 main.py 统一挂载
router = APIRouter(
    prefix="/recommender",
    tags=["Reverse Recommender · 反向寻歌"],
)


# ----------------------------------------------------------------------
# 接口一：反向寻歌
# ----------------------------------------------------------------------

@router.post(
    "/reverse",
    response_model=ApiResponse[ReverseData],
    summary="一段感受 → 最贴切的那首歌",
    response_description="返回反推出的音频特征 + 按匹配度排序的歌曲列表",
)
def reverse(req: ReverseRequest) -> ApiResponse[ReverseData]:
    """把一段自然语言描述，翻译成特征，再到曲库里找最近邻。

    注意这里是 `def` 不是 `async def`，原因见文件顶部说明。
    """
    started = time.time()

    library = reverse_engine.load_library()
    if not library:
        # 曲库为空时直接 503 —— 这不是调用方的错，是服务端没准备好
        logger.error(f"[反向寻歌] 曲库为空或不可用：{FEATURE_CSV}")
        raise HTTPException(
            status_code=503,
            detail=f"曲库不可用：{FEATURE_CSV} 中没有有效数据，"
                   f"请检查文件是否存在、以及内容是否通过质量过滤",
        )

    logger.info(
        f"[反向寻歌] 请求：{req.text[:30]}{'...' if len(req.text) > 30 else ''} "
        f"| top_k={req.top_k} use_llm={req.use_llm}"
    )

    try:
        result = reverse_engine.reverse_search(
            req.text, top_k=req.top_k, use_llm=req.use_llm
        )
    except Exception as exc:
        logger.exception(f"[反向寻歌] 引擎调用异常：{exc}")
        raise HTTPException(status_code=500, detail=f"反向寻歌引擎内部错误：{exc}")

    # 配了 Key 却走了关键词兜底 —— 说明大模型不可用，要警告出来
    if result["mode"] == "keyword" and req.use_llm is True:
        logger.warning(
            "[反向寻歌] 请求指定 use_llm=true，但结果走了关键词兜底 —— "
            "大模型可能不可用（限流 429 / 鉴权失败 / 网络超时）"
        )

    data = ReverseData(
        query=result["query"],
        features=result["features"],
        mode=result["mode"],
        reason=result["reason"],
        library_size=result["library_size"],
        library_note=result.get("library_note"),
        matches=[ReverseMatch(**m) for m in result["matches"]],
    )

    cost = time.time() - started
    top = result["matches"][0] if result["matches"] else None
    top_desc = f"{top['track_name']}（{top['match_score']}%）" if top else "无匹配"
    logger.info(
        f"[反向寻歌] 完成：{result['mode']} 模式，命中 {len(result['matches'])} 首，"
        f"Top1 = {top_desc}，耗时 {cost:.2f}s"
    )

    mode_cn = "大模型反推" if result["mode"] == "llm" else "关键词规则"
    return ok(
        data=data,
        message=f"已从 {result['library_size']} 首曲库中匹配（{mode_cn}，耗时 {cost:.2f}s）",
    )


# ----------------------------------------------------------------------
# 接口二：曲库概况
# ----------------------------------------------------------------------

@router.get(
    "/library",
    response_model=ApiResponse[LibraryStatsData],
    summary="查看曲库概况",
    response_description="总行数 / 有效行数 / 被过滤的脏数据行数",
)
def library() -> ApiResponse[LibraryStatsData]:
    """告诉调用方曲库到底有多大、过滤掉了多少脏数据。

    为什么需要这个接口？
      曲库规模直接决定"最近邻"的可信度。6 首歌里的最近邻和 6 万首歌里的
      最近邻，含金量完全不同。把这个数字暴露出来，前端就能如实提示用户，
      而不是让"匹配度 81%"这种数字被过度解读。
    """
    stats = reverse_engine.library_stats()
    logger.info(f"[曲库] 共 {stats['total']} 行，有效 {stats['valid']} 行，过滤 {stats['filtered']} 行")

    return ok(
        data=LibraryStatsData(**stats),
        message=f"曲库有效 {stats['valid']} 首（过滤 {stats['filtered']} 行脏数据）",
    )
