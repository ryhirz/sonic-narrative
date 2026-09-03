# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 平台 API 网关入口（main.py）
=========================================================================
 这是整个平台的统一入口，负责四件事：
   1. 创建 FastAPI 应用，挂载各子模块路由
   2. 配置 CORS、全局异常处理、请求日志中间件
   3. 提供平台级健康检查 /api/v1/health（真实探测各模块状态）
   4. 把 output_cards/ 挂成静态资源，前端可直接预览卡片

 启动命令（务必在项目根目录执行）：
     .venv\\Scripts\\python.exe -m uvicorn main:app --reload --port 8000

 然后打开自动生成的交互文档：
     http://127.0.0.1:8000/docs        ← Swagger UI，可以直接点按钮测接口
     http://127.0.0.1:8000/redoc       ← ReDoc，适合阅读
=========================================================================
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# ⚠️ core.config 必须最先导入：它会把项目根目录加进 sys.path，
#    后续所有 `import synesthesia_core` 才能成功。
from core.config import (
    API_V1_PREFIX,
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    CARD_DIR,
    CORS_ORIGINS,
    MODULES,
    probe_recommender_engine,
    probe_synesthesia_engine,
)
from core.logging_config import get_logger, setup_logging
from routers import recommender, synesthesia
from schemas.common import ApiResponse, HealthData, ModuleStatus, fail, ok

# 先把日志配好，后面的启动信息才能按统一格式打印
setup_logging()
logger = get_logger("sonic")

# 记录进程启动时刻，用于计算 uptime
START_TIME = time.time()


# ======================================================================
# 一、生命周期（服务启动 / 关闭时各执行一次）
# ======================================================================
# 用 lifespan 而不是 @app.on_event("startup")：后者已被官方标记为过时。
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时打印一份「体检报告」，让你一眼看出服务处于什么状态。"""
    logger.info("=" * 62)
    logger.info(f"  {APP_NAME} v{APP_VERSION} 正在启动")
    logger.info("-" * 62)

    for key, m in MODULES.items():
        mark = "[已上线]" if m["status"] == "active" else "[规划中]"
        logger.info(f"  {mark} {m['name']}（{m['english']}） v{m['version']}")

    engine = probe_synesthesia_engine()
    if engine.get("llm_enabled"):
        logger.info(f"  翻译引擎：LLM 真实模型（{engine.get('model')} @ {engine.get('base_url')}）")
    else:
        logger.info("  翻译引擎：Mock 兜底模式（未检测到 DASHSCOPE_API_KEY）")
        logger.info("            → 接口功能完整可用，但卡片来自本地预设，不是大模型创作")
    logger.info(f"  已生成卡片：{engine.get('card_count', 0)} 张")
    logger.info("-" * 62)
    logger.info("  API 文档  → http://127.0.0.1:8000/docs")
    logger.info("  健康检查  → http://127.0.0.1:8000/api/v1/health")
    logger.info("=" * 62)

    yield  # ← 服务在这一行持续运行，直到收到停止信号

    logger.info(f"{APP_NAME} 已停止")


# ======================================================================
# 二、创建 FastAPI 应用
# ======================================================================
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=APP_DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ======================================================================
# 三、CORS 跨域中间件
# ======================================================================
# 前端（Gradio / React / 静态页）通常跑在不同端口，必须放开跨域才能调通。
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,   # 生产环境请改成具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================================================================
# 四、请求日志中间件
# ======================================================================
@app.middleware("http")
async def log_and_timing(request: Request, call_next):
    """记录每个请求的方法、路径、状态码和耗时。

    排查"哪个接口慢"时非常有用：翻译接口如果突然从 8s 变成 60s，
    一眼就能看出来是上游大模型变慢了。
    """
    started = time.time()
    response = await call_next(request)
    cost_ms = (time.time() - started) * 1000

    logger.info(
        f"{request.method:6s} {request.url.path:42s} → {response.status_code} ({cost_ms:.0f}ms)"
    )
    # 顺便把耗时写进响应头，方便前端或压测工具采集
    response.headers["X-Process-Time"] = f"{cost_ms:.0f}ms"
    return response


# ======================================================================
# 五、全局异常处理（把所有错误都包装成统一信封）
# ======================================================================
# 不这么做的话，未捕获异常会返回一大段 HTML 堆栈，前端很难处理。

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """处理主动抛出的 HTTPException（404 / 500 / 路由里 raise 的那些）。"""
    logger.warning(f"HTTP {exc.status_code}：{exc.detail}（{request.url.path}）")
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(message=str(exc.detail), code=exc.status_code).model_dump(),
    )


# pydantic 的校验错误类型 → 中文模板（{} 里的占位符会用它自带的 ctx 填充）
VALIDATION_MSG_CN = {
    "less_than_equal": "不能大于 {le}",
    "greater_than_equal": "不能小于 {ge}",
    "missing": "该字段为必填项",
    "float_parsing": "必须是数字（小数也可以）",
    "int_parsing": "必须是整数",
    "string_too_long": "长度不能超过 {max_length} 个字符",
    "too_short": "内容长度不足",
    "value_error": "取值不合法",
}


def _friendly_error(e: dict) -> str:
    """把单条 pydantic 英文错误翻译成中文，形如「features.valence：不能大于 1」。"""
    etype = e.get("type", "")
    ctx = e.get("ctx") or {}

    tpl = VALIDATION_MSG_CN.get(etype)
    if tpl:
        try:
            detail = tpl.format(**ctx)
        except Exception:
            # ctx 的键和模板占位符对不上时，退回原始英文，至少保证不报错
            detail = e.get("msg", "")
    else:
        detail = e.get("msg", "")

    loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
    return f"{loc}：{detail}" if loc else detail


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理参数校验失败（422）。

    pydantic 默认吐的是英文错误数组，直接丢给前端很不友好。
    这里翻译成中文短句，前端可以直接弹给用户看，例如：
        "参数校验失败：features.valence：不能大于 1；features.tempo：不能大于 220"
    """
    errors = exc.errors()
    tips = [_friendly_error(e) for e in errors]

    message = "参数校验失败：" + "；".join(tips[:3])
    if len(tips) > 3:
        message += f" 等共 {len(tips)} 处问题"

    logger.warning(f"参数校验失败（{request.url.path}）：{message}")
    return JSONResponse(
        status_code=422,
        content=fail(message=message, code=422, data=errors).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底：任何没预料到的异常都在这里被接住。

    关键点：
      · 日志里记录完整堆栈（供排查）
      · 响应里只返回一句概括（不泄露内部实现细节）
    """
    logger.exception(f"未捕获异常（{request.url.path}）：{exc}")
    return JSONResponse(
        status_code=500,
        content=fail(message="服务器内部错误，请查看服务端日志", code=500).model_dump(),
    )


# ======================================================================
# 六、平台级路由
# ======================================================================

@app.get("/", tags=["Platform · 平台级"], summary="平台根入口")
def root() -> ApiResponse[dict]:
    """返回平台基本信息，方便确认服务是否起来了。"""
    return ok(
        data={
            "platform": APP_NAME,
            "version": APP_VERSION,
            "docs": "/docs",
            "redoc": "/redoc",
            "health": f"{API_V1_PREFIX}/health",
            "modules": {k: v["status"] for k, v in MODULES.items()},
        },
        message="SonicNarrative 平台已就绪",
    )


@app.get(
    f"{API_V1_PREFIX}/health",
    response_model=ApiResponse[HealthData],
    tags=["Platform · 平台级"],
    summary="平台健康检查",
    response_description="返回平台状态与各子模块的真实运行情况",
)
def health() -> ApiResponse[HealthData]:
    """平台级健康检查。

    这里不是简单回一个 "ok"，而是【真实探测】每个子模块的运行状态：
      · synesthesia 配了 API Key      → active（真实大模型）
      · synesthesia 没配 Key          → degraded（功能可用，但走 Mock 兜底）
      · recommender 引擎能加载曲库     → active（不依赖 Key，有兜底）
      · recommender 曲库为空 / 加载失败 → degraded（接口在，但匹配不了）
      · cover                          → planned（规划中）

    这样在演示或答辩时，一眼就能看出服务是不是"满血运行"。
    """
    engine = probe_synesthesia_engine()
    rec = probe_recommender_engine()

    modules: List[ModuleStatus] = []
    for key, m in MODULES.items():
        status = m["status"]
        if key == "synesthesia" and status == "active" and not engine.get("llm_enabled"):
            status = "degraded"
        # 反向寻歌：曲库可用才是真 active；曲库空/加载失败要如实降一级
        if key == "recommender" and status == "active":
            if not rec.get("available") or rec.get("library_valid", 0) == 0:
                status = "degraded"
        modules.append(
            ModuleStatus(
                key=key,
                name=m["name"],
                english=m["english"],
                status=status,
                version=m["version"],
            )
        )

    # 三种状态分别计数，保证 active + degraded + planned = 模块总数，
    # 不会像「只统计 active/planned」那样把降级模块漏掉、导致数字对不上。
    active_count = sum(1 for m in modules if m.status == "active")
    degraded_count = sum(1 for m in modules if m.status == "degraded")
    planned_count = sum(1 for m in modules if m.status == "planned")
    has_degraded = degraded_count > 0
    overall = "degraded" if has_degraded else "healthy"

    data = HealthData(
        platform=APP_NAME,
        version=APP_VERSION,
        status=overall,
        uptime_seconds=round(time.time() - START_TIME, 1),
        engines={"synesthesia": engine, "recommender": rec},
        modules=modules,
        active_count=active_count,
        degraded_count=degraded_count,
        planned_count=planned_count,
    )

    if has_degraded:
        reasons = []
        if not engine.get("llm_enabled"):
            reasons.append("翻译器未配 API Key，卡片走 Mock 兜底")
        if rec.get("available") and rec.get("library_valid", 0) == 0:
            reasons.append("反向寻歌曲库为空，无法匹配")
        tip = "（降级运行：" + "；".join(reasons) + "）"
    else:
        tip = ""

    return ok(data=data, message=f"平台运行正常{tip}")


# ======================================================================
# 七、挂载子模块路由
# ======================================================================
# 统一加 /api/v1 前缀；将来升 v2 只改 core/config.py 里的 API_V1_PREFIX 即可。

# ✅ 已上线：特征感知翻译器（Synesthetic Engine）
app.include_router(synesthesia.router, prefix=API_V1_PREFIX)

# ✅ 已上线：反向寻歌（Reverse Recommender）——
#   一段感受描述 → 反推音频特征 → 曲库里最贴切的那首歌
app.include_router(recommender.router, prefix=API_V1_PREFIX)

# ===== 以下是未来子模块的挂载位置（v2 规划，暂时注释掉）=====
#
# 新增一个子模块的标准步骤：
#   1. 在 routers/ 下新建 xxx.py，用 APIRouter(prefix="/xxx") 定义路由
#   2. 在 schemas/ 下定义该模块的请求 / 响应模型
#   3. 在 core/config.py 的 MODULES 里登记，并把 status 改成 "active"
#   4. 取消下面对应的两行注释
#
# from routers import cover         # AI 封面工坊：感知卡片 → 专辑封面图
# app.include_router(cover.router, prefix=API_V1_PREFIX)


# ======================================================================
# 八、静态资源：把 output_cards/ 挂到 /cards
# ======================================================================
# 这样 gallery 接口返回的 url（形如 /cards/01_Blinding Lights.html）
# 就能直接在浏览器里打开预览。
if CARD_DIR.exists():
    app.mount("/cards", StaticFiles(directory=str(CARD_DIR), html=True), name="cards")
else:
    logger.warning(f"卡片目录不存在，已跳过静态挂载：{CARD_DIR}")


# ======================================================================
# 九、直接运行本文件时启动服务（方便调试）
# ======================================================================
# 生产环境更推荐用命令行：uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
