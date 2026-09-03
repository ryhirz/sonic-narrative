# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 反向寻歌引擎（v2 子模块：Recommender）
=========================================================================
 正向翻译（v1 已上线）：音频特征 → 视觉场景
 反向寻歌（本模块）  ：感受描述 → 音频特征 → 曲库里最贴切的那首歌

 核心思路（两步）：
   1. 把"一段感受"反向翻译成 5 个音频特征（LLM 完成；没 Key 就用关键词规则）
   2. 用加权欧氏距离，在曲库里找特征最接近的歌

 为什么不用深度学习？
   曲库只有 10 首歌时，最近邻比训练模型更靠谱、更可解释。
   而且每一步都能讲清楚"为什么是这首歌"——对简历项目是实打实的加分项。
   将来换成 Kaggle 那套 11 万首的数据集，这里的代码一行都不用改。

 依赖：pandas（读曲库）；openai（可选，没装或没 Key 就自动走关键词规则）

 单独运行自测：
     .venv\\Scripts\\python.exe reverse_engine.py
=========================================================================
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 保证单独运行本文件时也能 import 到项目内的模块
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ======================================================================
# 一、常量配置
# ======================================================================

FEATURE_KEYS = ("valence", "energy", "danceability", "acousticness")
ALL_KEYS = FEATURE_KEYS + ("tempo",)

# 每个特征的合法区间（tempo 是 BPM，量纲和其他几个完全不同）
FEATURE_RANGE: Dict[str, Tuple[float, float]] = {
    "valence": (0.0, 1.0),
    "energy": (0.0, 1.0),
    "danceability": (0.0, 1.0),
    "acousticness": (0.0, 1.0),
    "tempo": (40.0, 220.0),
}

# 距离权重：情绪和能量对"听感"影响最大，给高一点；速度影响相对小
# （这套权重来自 PRD 的映射体系：Valence 是第一主色轴）
WEIGHTS: Dict[str, float] = {
    "valence": 1.2,
    "energy": 1.2,
    "danceability": 1.0,
    "acousticness": 1.0,
    "tempo": 0.8,
}

# 理论最大距离（各维度都差满时的距离），用来把距离换算成 0~100 的匹配度
MAX_DISTANCE = math.sqrt(sum(WEIGHTS.values()))

TEMPO_MIN, TEMPO_MAX = 40.0, 220.0

# ----------------------------------------------------------------------
# 曲库数据质量过滤
# ----------------------------------------------------------------------
# 背景：clean_spotify_data.csv 里混着 data_cleaning 阶段留下的测试行
# （album 为 Tests / Bug、popularity 只有个位数），它们会堂而皇之地
# 排进推荐结果第一名。这里统一按"可信度"过滤，而不是硬编码歌名。
MIN_POPULARITY = 15.0          # 低于这个热度的条目视为测试数据或脏数据
UNKNOWN_TOKENS = {"unknown", "n/a", "na", "none", "", "未知", "未知艺人"}

# 低于这个规模就认为"最近邻"说服力有限，需要在结果里如实提示
SMALL_LIBRARY_THRESHOLD = 50


def _is_valid_track(item: Dict[str, Any]) -> bool:
    """判断一条曲库记录是否可信。"""
    if item["artist"].strip().lower() in UNKNOWN_TOKENS:
        return False
    if item["popularity"] < MIN_POPULARITY:
        return False
    return True


# ======================================================================
# 二、曲库加载（带缓存，避免每次请求都读一遍 CSV）
# ======================================================================

# 缓存结构：记录文件的修改时间，CSV 一变就自动重新加载
_LIB_CACHE: Dict[str, Any] = {
    "path": None, "mtime": None, "rows": None,
    "total": 0, "filtered": 0,   # 过滤统计，暴露给健康检查/前端提示
}


def load_library(csv_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取曲库 CSV，返回标准化后的歌曲列表。

    缓存策略：比对文件的 mtime，文件没变就直接返回内存里的结果。
    这样既避免重复 IO，又能在你更新 CSV 后自动生效。
    """
    from core.config import FEATURE_CSV  # 延迟导入，避免循环依赖

    path = Path(csv_path or FEATURE_CSV)
    if not path.exists():
        return []

    mtime = path.stat().st_mtime
    if (_LIB_CACHE["rows"] is not None
            and _LIB_CACHE["path"] == str(path)
            and _LIB_CACHE["mtime"] == mtime):
        return _LIB_CACHE["rows"]

    try:
        import pandas as pd  # 延迟导入，没装 pandas 也能跑关键词部分
    except ImportError:
        return []

    df = pd.read_csv(path)
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]  # 去掉 BOM

    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        item: Dict[str, Any] = {
            "track_name": str(row.get("track_name", "未知曲目")).strip(),
            "artist": str(row.get("artist", "未知艺人")).strip(),
        }
        # 特征统一转成 float 并夹到合法区间，脏数据不会让匹配崩掉
        for key in ALL_KEYS:
            try:
                value = float(row.get(key, 0.0))
                if math.isnan(value):
                    raise ValueError
            except (TypeError, ValueError):
                value = 0.0
            low, high = FEATURE_RANGE[key]
            item[key] = max(low, min(high, value))

        # 这两个字段是可选的，只在展示时用
        item["album"] = str(row.get("album", "") or "").strip()
        try:
            item["popularity"] = float(row.get("popularity", 0))
        except (TypeError, ValueError):
            item["popularity"] = 0.0

        if _is_valid_track(item):
            rows.append(item)

    _LIB_CACHE.update({
        "path": str(path), "mtime": mtime, "rows": rows,
        "total": len(df), "filtered": len(df) - len(rows),
    })
    return rows


def library_stats() -> Dict[str, int]:
    """曲库概况：总行数 / 有效行数 / 被过滤行数。

    前端可以拿这个提示"曲库偏小，结果仅供参考"。
    """
    load_library()  # 确保缓存已建立
    return {
        "total": _LIB_CACHE["total"],
        "valid": len(_LIB_CACHE["rows"] or []),
        "filtered": _LIB_CACHE["filtered"],
    }


# ======================================================================
# 三、距离与相似度
# ======================================================================

def _norm_tempo(tempo: float) -> float:
    """把 BPM 归一化到 0~1。

    ⚠️ 这一步不能省：tempo 的量纲是 40~220，而其他特征是 0~1。
    不做归一化，tempo 的差异会完全淹没其他四个特征，匹配结果就废了。
    """
    return (tempo - TEMPO_MIN) / (TEMPO_MAX - TEMPO_MIN)


def feature_distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    """计算两组特征之间的加权欧氏距离（越小越像）。"""
    total = 0.0
    for key in FEATURE_KEYS:
        total += WEIGHTS[key] * (a[key] - b[key]) ** 2
    total += WEIGHTS["tempo"] * (_norm_tempo(a["tempo"]) - _norm_tempo(b["tempo"])) ** 2
    return math.sqrt(total)


def distance_to_score(distance: float) -> float:
    """把距离换算成 0~100 的匹配度（越大越像），便于前端直接展示。"""
    score = (1.0 - distance / MAX_DISTANCE) * 100.0
    return round(max(0.0, min(100.0, score)), 1)


def find_similar_tracks(features: Dict[str, float], top_k: int = 5,
                        library: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """在曲库里找特征最接近的 top_k 首歌。"""
    pool = library if library is not None else load_library()
    if not pool:
        return []

    scored = []
    for item in pool:
        dist = feature_distance(features, item)
        scored.append({
            "track_name": item["track_name"],
            "artist": item["artist"],
            "album": item.get("album", ""),
            "popularity": item.get("popularity", 0.0),
            "features": {k: round(item[k], 3) for k in ALL_KEYS},
            "distance": round(dist, 4),
            "match_score": distance_to_score(dist),
        })

    # 距离升序 = 最像的排前面
    scored.sort(key=lambda x: x["distance"])
    return scored[:top_k]


# ======================================================================
# 四、第一步：感受描述 → 音频特征
# ======================================================================

REVERSE_SYSTEM_PROMPT = """你是一个"反向通感翻译器"。用户会描述一种感受、画面或场景，
你需要反推：什么样的音乐，会让人产生这种感觉？

【输出要求】
只输出一个合法的 JSON 对象，不要任何解释、前言或 markdown 围栏，必须包含：

{
  "valence": 0.0~1.0 之间的小数（情绪积极度：越明亮愉悦越接近 1），
  "energy": 0.0~1.0 之间的小数（能量强度：越激烈越接近 1），
  "danceability": 0.0~1.0 之间的小数（舞蹈性），
  "acousticness": 0.0~1.0 之间的小数（原声度：越自然/不插电越接近 1），
  "tempo": 40~220 之间的整数（速度 BPM），
  "reason": "一句话说明你为什么这么推断，中文，30 字以内"
}

【推断示例】
- "深夜独自开车，霓虹在车窗外流过" → 情绪中等偏低、能量中等、原声度低（电子感）、中快速
- "清晨在森林里散步" → 情绪平和、能量低、原声度高、慢速
- "健身房冲刺最后一组" → 情绪高、能量很高、舞蹈性中上、原声度低、快速

请严格按 JSON 输出。"""


def _build_reverse_prompt(text: str) -> str:
    return f'用户描述的感受是：\n\n「{text}」\n\n请反推这首歌的音频特征。'


# 关键词 → 特征偏移表（没有 API Key 时的兜底方案）
# 设计思路：从一组"中性"特征出发，命中关键词就往对应方向推，
# 最后统一夹回合法区间。规则是可累加的，所以"安静 + 森林"会叠加生效。
KEYWORD_RULES: Dict[str, Dict[str, float]] = {
    r"安静|平静|放松|睡眠|睡着|轻柔|舒缓|冥想|催眠": {
        "energy": -0.35, "acousticness": +0.25, "tempo": -30, "valence": -0.05,
    },
    r"兴奋|热烈|嗨|燃|炸|狂欢|派对|蹦迪": {
        "energy": +0.38, "valence": +0.25, "danceability": +0.28, "tempo": +30,
    },
    r"悲伤|难过|失落|忧郁|心碎|孤独|哭泣|分手": {
        "valence": -0.40, "energy": -0.18, "tempo": -18,
    },
    r"开心|快乐|阳光|明亮|欢快|轻松|愉快|元气": {
        "valence": +0.38, "energy": +0.12, "danceability": +0.10,
    },
    r"专注|工作|学习|代码|阅读|写东西|效率": {
        "energy": -0.12, "acousticness": +0.22, "danceability": -0.22, "tempo": -8,
    },
    r"运动|跑步|健身|冲刺|有氧|撸铁": {
        "energy": +0.38, "tempo": +35, "danceability": +0.12, "valence": +0.10,
    },
    r"雨|夜|霓虹|城市|开车|街道|午夜": {
        "valence": -0.12, "energy": +0.10, "acousticness": -0.22,
    },
    r"森林|自然|清晨|鸟|海边|海|山|田野|微风": {
        "acousticness": +0.38, "energy": -0.28, "valence": +0.12, "tempo": -12,
    },
    r"爱|浪漫|温柔|甜蜜|心动|告白": {
        "valence": +0.28, "acousticness": +0.18, "energy": -0.12,
    },
    r"思念|回忆|怀旧|旧时光|从前|青春": {
        "valence": -0.12, "acousticness": +0.22, "tempo": -12,
    },
    r"愤怒|躁动|叛逆|金属|摇滚|发泄": {
        "energy": +0.38, "valence": -0.18, "acousticness": -0.28, "tempo": +15,
    },
    r"复古|迪斯科|律动|舞蹈|跳舞|摇摆": {
        "danceability": +0.38, "energy": +0.18, "valence": +0.18,
    },
    r"孤独|空旷|寂寥|荒凉|空灵": {
        "valence": -0.25, "energy": -0.20, "acousticness": +0.15,
    },
}


# 各维度中性起点，以及"从中性到边界"的半程跨度
NEUTRAL: Dict[str, float] = {
    "valence": 0.5, "energy": 0.5, "danceability": 0.5,
    "acousticness": 0.5, "tempo": 100.0,
}
HALF_SPAN: Dict[str, float] = {
    "valence": 0.5, "energy": 0.5, "danceability": 0.5,
    "acousticness": 0.5, "tempo": 90.0,
}
MIN_STEP_RATIO = 0.15   # 步长衰减下限，保证再怎么说也还能推动一点


def _features_from_keywords(text: str) -> Dict[str, float]:
    """用关键词规则把描述翻译成特征（无 API Key 时的兜底）。

    累加方式不是简单相加，而是"边际递减"：
    越靠近区间边界，同一个关键词能推动的幅度越小。

    为什么？纯累加会让"清晨 + 阳光 + 森林"这类多正向词直接把
    valence 顶到 1.0，和"极度狂喜"这种单一强情绪无法区分，
    匹配结果也就失去了排序意义。饱和累加能保留"词越多推得越远"
    的相对关系，又不会撞死在边界上。
    """
    feats = dict(NEUTRAL)

    hit_rules: List[str] = []
    for pattern, deltas in KEYWORD_RULES.items():
        matched = re.search(pattern, text)
        if not matched:
            continue
        # 记录"真正命中的那个词"，而不是规则模板的第一个词
        # （否则"深夜开车"会被说成命中了"雨"，让人一头雾水）
        hit_rules.append(matched.group(0))
        for key, delta in deltas.items():
            # 当前位置离中性点越远，这一步迈得越小
            progress = abs(feats[key] - NEUTRAL[key]) / HALF_SPAN[key]
            decay = max(MIN_STEP_RATIO, 1.0 - progress)
            feats[key] += delta * decay

    # 夹回合法区间（理论上饱和累加不会越界，这里只是兜底）
    for key in ALL_KEYS:
        low, high = FEATURE_RANGE[key]
        feats[key] = max(low, min(high, feats[key]))

    # 内部字段，调用方会 pop 掉后再对外返回
    feats["_hits"] = hit_rules
    return feats


def _features_from_llm(text: str) -> Optional[Tuple[Dict[str, float], str]]:
    """调用大模型把描述反向翻译成特征。失败返回 None，由调用方降级。"""
    try:
        import synesthesia_core  # 复用它的 API 配置（Key / Base URL / 模型名）
    except Exception:
        return None

    if not synesthesia_core.API_KEY:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    try:
        client = OpenAI(
            api_key=synesthesia_core.API_KEY,
            base_url=synesthesia_core.BASE_URL,
            timeout=60,
        )
        # 反向任务是"推断"而非"创作"，温度调低一点，结果更稳定
        response = client.chat.completions.create(
            model=synesthesia_core.MODEL_NAME,
            messages=[
                {"role": "system", "content": REVERSE_SYSTEM_PROMPT},
                {"role": "user", "content": _build_reverse_prompt(text)},
            ],
            temperature=0.4,
            extra_body={} if synesthesia_core.ENABLE_THINKING else {"enable_thinking": False},
        )
        content = response.choices[0].message.content or ""
        parsed = synesthesia_core.extract_json_from_text(content)
        if not isinstance(parsed, dict):
            return None

        feats: Dict[str, float] = {}
        for key in ALL_KEYS:
            try:
                value = float(parsed.get(key))
                if math.isnan(value):
                    raise ValueError
            except (TypeError, ValueError):
                return None  # 少给或给错字段，直接降级，不猜
            low, high = FEATURE_RANGE[key]
            feats[key] = max(low, min(high, value))

        reason = str(parsed.get("reason", "")).strip()
        return feats, reason

    except Exception:
        return None


def describe_to_features(text: str, use_llm: Optional[bool] = None) -> Dict[str, Any]:
    """把一段感受描述翻译成音频特征。

    返回：
        {
          "features": {5 个特征},
          "mode": "llm" / "keyword",
          "reason": "为什么这么翻译",
          "matched_keywords": bool  # 关键词模式是否命中了规则
        }
    """
    text = (text or "").strip()
    if not text:
        text = "安静的夜晚"

    # 1) 优先走大模型
    if use_llm is None or use_llm is True:
        result = _features_from_llm(text)
        if result is not None:
            feats, reason = result
            return {
                "features": feats,
                "mode": "llm",
                "reason": reason or "由大模型根据描述反推",
                "matched_keywords": True,
            }
        if use_llm is True:
            # 明确要求用大模型却失败了，仍然降级，但在 reason 里说清楚
            feats = _features_from_keywords(text)
            feats.pop("_hits", None)
            return {
                "features": feats,
                "mode": "keyword",
                "reason": "大模型不可用，已降级为关键词规则推断",
                "matched_keywords": True,
            }

    # 2) 关键词规则兜底
    feats = _features_from_keywords(text)
    hits = feats.pop("_hits", [])
    if hits:
        # 把命中的关键词摘出来放进 reason，让"为什么是这个特征"可追溯
        sample = "、".join(hits[:3])
        more = f" 等 {len(hits)} 处" if len(hits) > 3 else ""
        reason = f"命中感知规则（{sample}{more}）"
    else:
        reason = "未匹配到明确关键词，采用中性特征（可换更具体的描述试试）"
    return {
        "features": feats,
        "mode": "keyword",
        "reason": reason,
        "matched_keywords": bool(hits),
    }


# ======================================================================
# 五、组合入口：一句描述 → 一批歌
# ======================================================================

def reverse_search(text: str, top_k: int = 5,
                   use_llm: Optional[bool] = None) -> Dict[str, Any]:
    """反向寻歌主流程。

    返回：
        {
          "query": 原始描述,
          "features": 反推出来的特征,
          "mode": "llm" / "keyword",
          "reason": 推断依据,
          "library_size": 曲库大小,
          "library_note": 曲库提示（曲库偏小时给前端展示）,
          "matches": [ {歌曲信息 + match_score}, ... ]
        }
    """
    translated = describe_to_features(text, use_llm=use_llm)
    features = translated["features"]

    library = load_library()
    matches = find_similar_tracks(features, top_k=top_k, library=library)

    # 曲库太小时结果代表性有限，明确告诉调用方，别让"最贴切"被误读
    size = len(library)
    if size == 0:
        note = "曲库为空，无法匹配。请检查 clean_spotify_data.csv 是否有有效数据。"
    elif size < SMALL_LIBRARY_THRESHOLD:
        note = (f"当前曲库仅 {size} 首，结果是这个小样本里的最近邻，"
                f"扩大曲库后推荐质量会明显提升。")
    else:
        note = f"在 {size} 首曲库中匹配。"

    return {
        "query": text,
        "features": {k: round(features[k], 3) for k in ALL_KEYS},
        "mode": translated["mode"],
        "reason": translated["reason"],
        "library_size": size,
        "library_note": note,
        "matches": matches,
    }


# ======================================================================
# 六、自测
# ======================================================================

if __name__ == "__main__":
    print("=" * 62)
    print("  反向寻歌引擎 · 自测")
    print("=" * 62)

    library = load_library()
    stats = library_stats()
    print(f"曲库加载：{stats['valid']} 首可用 "
          f"（CSV 共 {stats['total']} 行，过滤掉 {stats['filtered']} 行脏数据）")
    for item in library:
        print(f"   - {item['track_name']} · {item['artist']}")

    print()
    samples = [
        "深夜独自开车，霓虹在车窗外流过",
        "清晨在森林里散步，阳光穿过树叶",
        "健身房冲刺最后一组，需要燃起来",
        "睡不着，想要很安静很轻的音乐",
        "想给喜欢的人表白，要温柔一点",
    ]
    for text in samples:
        result = reverse_search(text, top_k=3)
        print(f"\n【{text}】")
        print(f"  模式：{result['mode']} ｜ {result['reason']}")
        print(f"  反推特征：{result['features']}")
        for i, m in enumerate(result["matches"], 1):
            print(f"   {i}. {m['track_name']} · {m['artist']} —— 匹配度 {m['match_score']}%")
