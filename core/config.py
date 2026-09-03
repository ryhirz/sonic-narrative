# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 平台级配置（core/config.py）
=========================================================================
 为什么要单独抽一层配置？
   1. 【路径陷阱】synesthesia_core.py 里 OUTPUT_DIR = "output_cards" 是相对路径，
      它取决于"你在哪个目录下敲的 uvicorn 命令"。Web 服务里这很危险——
      换个目录启动，卡片就读不到了。所以这里统一用 __file__ 推导绝对路径。
   2. 【可移植】所有可变配置集中在一处，不散落到各个文件里。
   3. 【多模块扩展】下面的 MODULES 注册表是为未来子模块预留的位置，
      新增模块只要在这里登记，/api/v1/health 就能自动汇报它的状态。
=========================================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# 一、项目根路径与 sys.path
# ----------------------------------------------------------------------
# core/ 目录的上一级就是项目根（E:\产品\SonicNarrative_Project）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 把项目根塞进 sys.path，保证 `import synesthesia_core` 在任何启动方式下都成立。
# 没有这一行的话，如果你从别的盘符/目录启动 uvicorn，
# 就会报 ModuleNotFoundError: No module named 'synesthesia_core'。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ----------------------------------------------------------------------
# 二、应用元信息（会显示在 /docs 自动文档顶部）
# ----------------------------------------------------------------------
APP_NAME = "SonicNarrative Platform"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = """
**SonicNarrative** 是一个多模块音乐智能分析平台，
把冷冰冰的音频特征翻译成人能看见、能走进去的感官体验。

**已上线子模块**
- `synesthesia` — 特征感知翻译器（Synesthetic Engine）：音频特征 → 视觉场景 / 情绪文案 / AI 绘画 Prompt

**规划中子模块**
- `recommender` — 反向寻歌：一段感受 → 最贴切的那首歌
- `cover` — AI 封面工坊：感知卡片 → 专辑封面

> 所有响应统一包裹在 `{code, message, data}` 信封里，`code = 0` 表示成功。
"""

# ----------------------------------------------------------------------
# 三、API 前缀（版本化：未来升 v2 只改这一处）
# ----------------------------------------------------------------------
API_V1_PREFIX = "/api/v1"

# ----------------------------------------------------------------------
# 四、CORS（跨域设置）
# ----------------------------------------------------------------------
# 开发期默认放开所有来源，方便前端 / Gradio 直接连过来。
# ⚠️ 生产环境务必改成具体域名，例如：
#    set SONIC_CORS_ORIGINS=https://your-site.com,http://localhost:5173
_raw_origins = os.getenv("SONIC_CORS_ORIGINS", "*")
CORS_ORIGINS: list = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ----------------------------------------------------------------------
# 五、目录与数据文件（绝对路径，绕开"工作目录"的坑）
# ----------------------------------------------------------------------
CARD_DIR = PROJECT_ROOT / "output_cards"          # 已生成的感知卡片
FEATURE_CSV = PROJECT_ROOT / "clean_spotify_data.csv"  # 清洗后的音频特征

# ----------------------------------------------------------------------
# 六、子模块注册表（/api/v1/health 的数据源）
# ----------------------------------------------------------------------
# status 取值：active = 已上线 / planned = 规划中 / degraded = 降级运行
MODULES: dict = {
    "synesthesia": {
        "name": "特征感知翻译器",
        "english": "Synesthetic Engine",
        "status": "active",
        "version": "0.1.0",
    },
    "recommender": {
        "name": "反向寻歌推荐",
        "english": "Reverse Recommender",
        "status": "active",
        "version": "0.1.0",
    },
    "cover": {
        "name": "AI 封面生成",
        "english": "AI Cover Studio",
        "status": "planned",
        "version": "-",
    },
}


def probe_synesthesia_engine() -> dict:
    """探测翻译器引擎的运行时状态（到底有没有接上真实大模型）。

    这里用「延迟导入」：只有在真正调用时才 import synesthesia_core。
    这么做有两个好处：
      1. 配置层不产生重量级依赖，导入 core.config 非常轻量；
      2. 每次调用都重新读取 synesthesia_core 的模块级变量，
         能反映服务运行期间的最新状态（比如有没有走 Mock 降级）。

    返回示例：
        {"llm_enabled": False, "model": "qwen-plus", "card_count": 5, ...}
    """
    try:
        import synesthesia_core  # 延迟导入（放在函数里，不在文件顶部）

        return {
            "llm_enabled": bool(synesthesia_core.API_KEY),
            "model": synesthesia_core.MODEL_NAME,
            "base_url": synesthesia_core.BASE_URL,
            "thinking_enabled": synesthesia_core.ENABLE_THINKING,
            "card_count": len(list(CARD_DIR.glob("*.html"))) if CARD_DIR.exists() else 0,
        }
    except Exception as exc:  # 引擎模块出问题也不能让健康检查挂掉
        return {"llm_enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def probe_recommender_engine() -> dict:
    """探测反向寻歌引擎的运行时状态。

    反向寻歌比翻译器更「轻」：它不依赖大模型也能跑（关键词规则兜底），
    所以"健康"的判定标准不同——
      · llm_enabled = True   → 大模型可用，描述→特征的语义理解更准
      · llm_enabled = False   → 功能依然 100% 可用，只是走关键词规则
    这里永远标记为 active，不会因为没 Key 就 degraded，
    否则在答辩演示时"反向寻歌"会显得没准备好，其实它随时能用。

    返回示例：
        {"available": True, "llm_enabled": False,
         "library_valid": 6, "library_total": 10, "library_filtered": 4}
    """
    try:
        import reverse_engine  # 延迟导入

        stats = reverse_engine.library_stats()
        # 复用翻译器的 Key 配置做探测（两者共用同一套 API 配置）
        try:
            import synesthesia_core
            llm_enabled = bool(synesthesia_core.API_KEY)
        except Exception:
            llm_enabled = False

        return {
            "available": True,
            "llm_enabled": llm_enabled,
            "library_total": stats["total"],
            "library_valid": stats["valid"],
            "library_filtered": stats["filtered"],
        }
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
