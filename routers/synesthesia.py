# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 翻译器路由（routers/synesthesia.py）
=========================================================================
 对外接口：
   POST /api/v1/synesthesia/translate   音频特征 → 感知卡片
   GET  /api/v1/synesthesia/gallery     已生成的翻译样本列表

 ⚠️ 一个非常关键的 FastAPI 知识点（面试常问，写进简历也很有说服力）：
    这两个路由函数都【故意写成普通 def】，而不是 async def。

    原因：generate_sense_card() 内部要发 HTTP 请求调大模型，是同步阻塞的，
    单次耗时可达 8 秒。如果写成 async def 并在里面直接调用它，
    就会把整个事件循环卡死 —— 这 8 秒内所有其他请求全部排队等待，服务等于假死。

    写成 def 时，FastAPI 会自动把它丢进线程池（run_in_threadpool）执行，
    事件循环继续处理其他请求，服务始终保持响应。

    → 结论：阻塞型同步代码用 def；只有真正的异步 IO（async/await 库）才用 async def。
=========================================================================
"""

from __future__ import annotations

import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query

# ⚠️ 下面这两行顺序不能反！
# core.config 在被导入时会把「项目根目录」加进 sys.path，
# 只有它先执行完，后面的 `import synesthesia_core` 才能成功。
from core.config import CARD_DIR
from core.logging_config import get_logger
from schemas.common import ApiResponse, ok
from schemas.synesthesia import (
    GalleryData,
    GalleryItem,
    SenseCard,
    TranslateData,
    TranslateRequest,
)

import build_report      # 复用它的 parse_card / palette_warmth，不重复造轮子
import synesthesia_core  # 核心翻译引擎

logger = get_logger(__name__)

# prefix 只写到这里，/api/v1 由 main.py 统一挂载
router = APIRouter(
    prefix="/synesthesia",
    tags=["Synesthetic Engine · 特征感知翻译器"],
)

# 卡片静态访问前缀（main.py 里会把 /cards 挂到 output_cards 目录）
CARD_URL_PREFIX = "/cards"


def _detect_mode(html_str: str) -> str:
    """判断这张卡片到底是 LLM 生成的，还是 Mock 兜底的。

    为什么不直接看请求参数？因为 API 调用失败时引擎会【静默降级】成 Mock，
    请求参数里写的是 use_llm=True，但实际拿到的是兜底结果。
    只有卡片 HTML 里的模式标记能反映真实结果，检测它最准。
    """
    return "llm" if "LLM 生成" in html_str else "mock"


# ----------------------------------------------------------------------
# 接口一：正向翻译
# ----------------------------------------------------------------------

@router.post(
    "/translate",
    response_model=ApiResponse[TranslateData],
    summary="音频特征 → 感知卡片",
    response_description="返回通感 JSON + 可直接渲染的 HTML 卡片源码",
)
def translate(req: TranslateRequest) -> ApiResponse[TranslateData]:
    """把 5 个音频特征翻译成一个可感知的场景。

    注意这里是 `def` 不是 `async def`，原因见文件顶部说明。
    """
    started = time.time()
    feats = req.features.model_dump()
    meta = req.meta.model_dump()

    logger.info(
        f"[翻译] 请求：{meta.get('track_name')} · {meta.get('artist')} | "
        f"valence={feats['valence']:.2f} energy={feats['energy']:.2f} "
        f"tempo={feats['tempo']:.0f} | use_llm={req.use_llm}"
    )

    # 调用核心引擎（内部已自带三层容错：真实模型 → Mock 预设 → 终极兜底）
    try:
        html_str, sense = synesthesia_core.generate_sense_card(
            feats, meta, use_llm=req.use_llm
        )
    except Exception as exc:
        # 能走到这里，说明连兜底都失败了，属于真·意外，记录堆栈并向上抛
        logger.exception(f"[翻译] 引擎调用异常：{exc}")
        raise HTTPException(status_code=500, detail=f"翻译引擎内部错误：{exc}")

    mode = _detect_mode(html_str)

    # 配了 Key 却拿到 Mock —— 说明大模型不可用（限流 / 鉴权 / 网络），要警告出来
    if mode == "mock" and synesthesia_core.API_KEY:
        logger.warning(
            "[翻译] 已配置 API Key，但结果走了 Mock 兜底 —— "
            "大模型可能不可用（限流 429 / 鉴权失败 / 网络超时），请检查上游状态"
        )

    # 可选：顺带落盘，方便之后在画廊里看到它
    saved_path = None
    if req.save:
        filename = f"{int(time.time())}_{synesthesia_core.safe_filename(meta['track_name'])}.html"
        saved_path = synesthesia_core.save_card(html_str, str(CARD_DIR / filename))
        logger.info(f"[翻译] 卡片已落盘：{saved_path}")

    data = TranslateData(
        card=SenseCard(**sense),
        mode=mode,
        model=synesthesia_core.MODEL_NAME if mode == "llm" else None,
        html=html_str,
        html_length=len(html_str),
        saved_path=saved_path,
        features=synesthesia_core.normalize_features(feats),
        meta=meta,
    )

    cost = time.time() - started
    logger.info(f"[翻译] 完成：{sense['scene_title']}（{mode}）耗时 {cost:.1f}s")

    mode_cn = "大模型生成" if mode == "llm" else "Mock 兜底"
    return ok(data=data, message=f"翻译完成（{mode_cn}，耗时 {cost:.1f}s）")


# ----------------------------------------------------------------------
# 接口二：样本画廊
# ----------------------------------------------------------------------

@router.get(
    "/gallery",
    response_model=ApiResponse[GalleryData],
    summary="获取已生成的翻译样本列表",
    response_description="解析 output_cards 目录下所有卡片，返回结构化列表",
)
def gallery(
    limit: int = Query(default=20, ge=1, le=200, description="本次返回条数"),
    offset: int = Query(default=0, ge=0, description="跳过前 N 条（分页用）"),
) -> ApiResponse[GalleryData]:
    """列出 output_cards/ 下所有已生成的感知卡片。

    解析逻辑直接复用 build_report.parse_card()，不在路由里重复写正则。
    """
    if not CARD_DIR.exists():
        logger.warning(f"[画廊] 卡片目录不存在：{CARD_DIR}")
        raise HTTPException(status_code=404, detail=f"卡片目录不存在：{CARD_DIR}")

    files = sorted(CARD_DIR.glob("*.html"))
    total = len(files)
    page = files[offset: offset + limit]

    items = []
    for path in page:
        try:
            c = build_report.parse_card(str(path))
        except Exception as exc:
            # 单张卡片解析失败不能影响整个列表，跳过它并记录警告
            logger.warning(f"[画廊] 解析失败，已跳过：{path.name} -> {exc}")
            continue

        items.append(
            GalleryItem(
                **{k: c[k] for k in (
                    "file", "track", "artist", "bpm", "scene_title",
                    "emojis", "palette", "desc", "prompt",
                )},
                warmth=round(build_report.palette_warmth(c["palette"]), 1),
                url=f"{CARD_URL_PREFIX}/{quote(c['file'])}",
            )
        )

    logger.info(f"[画廊] 返回 {len(items)}/{total} 条样本（offset={offset}, limit={limit}）")
    return ok(
        data=GalleryData(total=total, returned=len(items), items=items),
        message=f"共 {total} 张样本，本次返回 {len(items)} 张",
    )
