# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 第二步 + 第三步：LLM 联觉翻译引擎（核心脚本）
=========================================================================
 职责：
   1. 调用 Qwen（OpenAI 兼容模式）把 5 个音频特征"翻译"成一段通感 JSON
   2. 没有 API Key / 调用失败时，自动降级到本地 Mock 预设，保证流水线不断
   3. 把 JSON 渲染成毛玻璃质感的 HTML 感知卡片
   4. 对外暴露单函数入口 generate_sense_card(features_dict)

 对外最常用的一行代码：
      html, data = generate_sense_card({"valence": .8, "energy": .9, ...})

 运行（批量出卡片）：
      python synesthesia_core.py

 依赖：pip install openai pandas
 配置：设置环境变量 DASHSCOPE_API_KEY 即可切到真实大模型；不设则跑 Mock 模式
=========================================================================
"""

from __future__ import annotations

import html as html_lib
import json
import math
import os
import re
from typing import Any, Dict, Optional, Tuple

# ======================================================================
# 一、配置区（API Key 走环境变量，绝不硬编码进代码）
# ======================================================================

# 阿里云 DashScope 的 API Key；留空 -> 自动进入 Mock 模式
API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# OpenAI 兼容模式的 Base URL（通义千问官方地址）
BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# 模型名。推荐用不带版本号的「稳定别名」，省心且不会踩思考模式的坑：
#   qwen-plus   均衡好用，中文文采够，推荐 ← 默认
#   qwen-flash  更快更省，适合批量出卡
#   qwen-turbo  最便宜
#   qwen-max    最强也最贵
# 注意：qwen3.5 / 3.6 / 3.7 / 3.8 这类带版本号的系列【默认开启思考模式】，
#       会额外产生思考 token（多花钱），还可能让输出夹带思考过程、破坏纯 JSON。
#       下面 ENABLE_THINKING=False 会显式关掉它。
MODEL_NAME = os.getenv("QWEN_MODEL", "qwen-plus")

# 是否开启思考模式（本项目要严格 JSON 输出，务必保持 False）
ENABLE_THINKING = os.getenv("QWEN_ENABLE_THINKING", "false").lower() == "true"

TEMPERATURE = 0.85   # 创意类任务，温度调高一点
TIMEOUT = 60         # 单次请求超时（秒）

# 批量模式：读哪个 CSV、输出到哪、处理前几首
INPUT_CSV = os.getenv("SONIC_INPUT_CSV", "clean_spotify_data.csv")
OUTPUT_DIR = "output_cards"
BATCH_SIZE = 5

# 5 个核心特征的默认值与合法区间（用于入参清洗）
DEFAULT_FEATURES: Dict[str, float] = {
    "valence": 0.5, "energy": 0.5, "danceability": 0.5,
    "acousticness": 0.5, "tempo": 100.0,
}
FEATURE_RANGE: Dict[str, Tuple[float, float]] = {
    "valence": (0.0, 1.0), "energy": (0.0, 1.0), "danceability": (0.0, 1.0),
    "acousticness": (0.0, 1.0), "tempo": (40.0, 220.0),
}
# 卡片底部小标签用的中文名
FEATURE_LABEL = {
    "valence": "情绪 Valence", "energy": "能量 Energy", "danceability": "律动 Dance",
    "acousticness": "原声 Acoustic", "tempo": "速度 BPM",
}

# ======================================================================
# 二、Mock 预设数据（3 套风格，Key 为空 / 调用失败时使用）
# ======================================================================
# 设计巧思：不是随机返回，而是"根据特征挑最像的那套"，
# 这样即使没接大模型，演示效果也说得通。

MOCK_PRESETS: Dict[str, Dict[str, Any]] = {
    # ① 赛博朋克：高能量 + 低原声度（电子/合成感强）
    "cyberpunk": {
        "scene_title": "霓虹深巷",
        "emojis": ["🌃", "⚡", "🤖"],
        "color_palette": ["#00F5D4", "#FF2E97", "#1A1A2E"],
        "sensory_description": "你推开沉重的金属门，霓虹在脚下水洼里碎成星尘。合成器的低频贴着地面爬来，整座城市正踩着你的心跳呼吸。",
        "ai_art_prompt": "A lone figure standing in a rain-soaked cyberpunk alley at midnight, neon magenta and cyan reflections fracturing in puddles, towering megastructures with holographic signage, volumetric fog, cinematic wide shot, blade-runner aesthetic, ultra detailed, 8k --ar 16:9",
    },
    # ② 静谧森林：低能量 + 高原声度（安静、自然）
    "forest": {
        "scene_title": "晨雾林间",
        "emojis": ["🌲", "🍃", "🌫️"],
        "color_palette": ["#7FB069", "#2D4A3E", "#C8D5B9"],
        "sensory_description": "你赤脚踩进湿润的苔藓，晨雾在松针之间缓慢流动。一声鸟鸣落进耳中，像有人替你把胸口那团乱麻轻轻解开。",
        "ai_art_prompt": "A tranquil ancient forest at dawn, soft god rays filtering through mist-laden pine canopy, moss-covered stones, shallow depth of field, ethereal atmosphere, photorealistic, cinematic lighting, ultra detailed --ar 16:9",
    },
    # ③ 热情沙滩：高情绪 + 高舞蹈性（明亮、律动）
    "beach": {
        "scene_title": "落日白沙",
        "emojis": ["🏖️", "🌅", "🌊"],
        "color_palette": ["#FF9A3C", "#FFD56B", "#2EC4B6"],
        "sensory_description": "你陷进被太阳晒暖的白沙里，浪花一遍遍漫过脚踝又退去。吉他扫弦和着海风扑在脸上，世界只剩下金色的松弛。",
        "ai_art_prompt": "A sun-drenched tropical beach at golden hour, turquoise waves lapping warm white sand, palm silhouettes against a blazing orange sunset, relaxing summer mood, photorealistic, cinematic, ultra detailed --ar 16:9",
    },
}

# 终极兜底：连 Mock 预设都出问题时用（保证一定能渲染出卡片）
FALLBACK_SENSE: Dict[str, Any] = {
    "scene_title": "静默回声",
    "emojis": ["🎧", "🌌", "✨"],
    "color_palette": ["#6C7A89", "#B0BEC5", "#37474F"],
    "sensory_description": "你站在一片没有边界的声音里，所有频率都化作了缓缓流动的光。此刻不需要理解，只需要感受。",
    "ai_art_prompt": "Abstract soundscape visualization, flowing waves of soft light, minimal gradient background, serene atmosphere, digital art, ultra detailed --ar 16:9",
}


def pick_mock_preset(features: Dict[str, float]) -> Dict[str, Any]:
    """根据音频特征，挑选最贴切的一套 Mock 预设（确定性规则，不随机）。"""
    energy = features["energy"]
    valence = features["valence"]
    acousticness = features["acousticness"]
    danceability = features["danceability"]

    # 规则优先级说明：与 PRD 的映射体系保持一致 —— Valence（情绪）是第一主色轴，
    # 所以"明亮律动"的判断要排在"高能量电子"前面，否则像 Levitating 这种
    # 高情绪流行歌会被误判成赛博朋克。

    # 规则 1：情绪高 且 舞蹈性高 -> 明亮、有律动 -> 热情沙滩
    if valence >= 0.6 and danceability >= 0.6:
        return MOCK_PRESETS["beach"]
    # 规则 2：能量高 且 原声度低 -> 电子/合成感强 -> 赛博朋克
    if energy >= 0.6 and acousticness < 0.4:
        return MOCK_PRESETS["cyberpunk"]
    # 规则 3：原声度高 且 能量低 -> 自然、安静 -> 静谧森林
    if acousticness >= 0.5 and energy < 0.6:
        return MOCK_PRESETS["forest"]
    # 兜底：情绪偏正向选沙滩，偏内省选森林
    return MOCK_PRESETS["beach"] if valence >= 0.5 else MOCK_PRESETS["forest"]


# ======================================================================
# 三、Prompt 工程（要求模型严格输出 JSON）
# ======================================================================

SYSTEM_PROMPT = """你是一位"通感翻译引擎"（Synesthetic Engine）。你的工作是把音乐的音频特征，
翻译成人类可以看见、可以触摸、可以走进去的场景。

【严格要求】
1. 只输出一个合法的 JSON 对象，不要输出任何解释、前言、后记。
2. 不要使用 ```json 这类 markdown 代码块围栏，直接以 { 开头、以 } 结尾。
3. JSON 必须且只能包含以下 5 个字段：

{
  "scene_title": "4到6个汉字的场景名，例如：霓虹深巷",
  "emojis": ["3个最能代表该场景的emoji"],
  "color_palette": ["#RRGGBB", "#RRGGBB", "#RRGGBB"],
  "sensory_description": "约50字的第二人称沉浸式场景描述，必须以'你'为主语，调动视觉/触觉/温度等感官",
  "ai_art_prompt": "一段英文的 Midjourney 绘画提示词，包含主体、环境、光线、风格、画质词，结尾带 --ar 16:9"
}

【字段细则】
- emojis：恰好 3 个字符串，不要加数字序号。
- color_palette：恰好 3 个标准 HEX 颜色码（7位，含#），且三色要能组成协调的渐变。
- sensory_description：中文，第二人称"你"，约 50 字，要有画面感和身体感。
- ai_art_prompt：纯英文，不要出现中文。
"""


def build_rule_hint(features: Dict[str, float]) -> str:
    """规则引擎给出的「硬约束」提示。

    为什么要这一步：小参数模型（7B/8B）在拿到相似的高能量特征时，
    容易偷懒收敛到同一套配色（实测两首歌都返回了 #FF6B6B/#FFA500/#FFD700）。
    所以这里用确定性规则先把「色彩基调 / 光影 / 质感 / 节拍」定死，
    再让模型在这个框架内发挥创意 —— 既保证与特征强相关，又保证歌曲之间有区分度。
    这也是本项目「规则优先、LLM 润色」核心思路的体现。
    """
    v = features["valence"]
    e = features["energy"]
    a = features["acousticness"]
    d = features["danceability"]
    bpm = features["tempo"]

    if v >= 0.7:
        tone = "暖色调（橙 / 金 / 暖粉为主）"
    elif v >= 0.4:
        tone = "中性色调（青绿 / 淡紫为主）"
    else:
        tone = "冷色调（深蓝 / 靛蓝 / 灰紫为主）"

    if e >= 0.7:
        light = "高对比、强光束、锐利"
    elif e >= 0.4:
        light = "中等对比、柔和定向光"
    else:
        light = "漫射柔光、朦胧、低对比"

    texture = "有机自然质感（木/水/雾/植物）" if a >= 0.5 else "数字合成质感（霓虹/金属/故障）"
    rhythm = "重复几何、强律动" if d >= 0.6 else "自由流动、不规则"
    pace = "密集短促、急促" if bpm > 120 else ("舒展留白、缓慢" if bpm < 90 else "中速均衡")

    return f"""【规则引擎硬约束，必须遵守】
- 色彩基调：{tone}
- 光影质感：{light}
- 材质倾向：{texture}
- 图形节奏：{rhythm}
- 节拍速度：{pace}
- 三个 HEX 颜色必须互不相同，且能组成协调渐变；**禁止使用 #FF0000 / #00FF00 这类刺眼纯原色**，请用有灰度和层次的复色。
- 场景名与描述必须紧扣这首歌的上述气质，不要泛泛而谈。"""


def build_user_prompt(features: Dict[str, float], meta: Dict[str, str],
                      avoid_titles: Optional[list] = None,
                      avoid_palettes: Optional[list] = None) -> str:
    """把特征和歌曲信息拼成给模型的用户指令。

    avoid_titles / avoid_palettes：批量生成时，把前面几首已经用过的
    场景名和配色传进来让模型避开。小模型有个明显的惰性——特征相近的两首歌
    （比如都是高情绪高能量）会返回一模一样的配色，靠这两个参数打破重复。
    """
    avoid_line = ""
    if avoid_titles:
        avoid_line += (f"- **以下场景名已被本批次其他歌曲占用，必须避开，"
                       f"请换一个意象完全不同的词**：{'、'.join(avoid_titles)}\n")
    if avoid_palettes:
        pal_text = "；".join("/".join(p) for p in avoid_palettes)
        avoid_line += (f"- **以下配色方案已被本批次其他歌曲占用，"
                       f"请换一组明显不同的颜色（保持同样的色彩基调）**：{pal_text}\n")

    return f"""请为下面这首歌生成通感场景：

歌曲名：{meta.get('track_name', '未知曲目')}
艺人：{meta.get('artist', '未知艺人')}

音频特征（均为 0~1 归一化值，tempo 为 BPM）：
- valence（情绪积极度，越高越明亮愉悦）：{features['valence']:.2f}
- energy（能量强度，越高越激烈）：{features['energy']:.2f}
- danceability（舞蹈性，越高越有律动）：{features['danceability']:.2f}
- acousticness（原声度，越高越自然/不插电）：{features['acousticness']:.2f}
- tempo（速度 BPM）：{features['tempo']:.0f}

{build_rule_hint(features)}
{avoid_line}
请严格按照系统提示的 JSON 格式输出。"""


# ======================================================================
# 四、入参清洗与 JSON 解析（防御式编程，模型返回脏数据也不崩）
# ======================================================================

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_features(features: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """把外部传入的特征字典清洗成"一定有值、一定合法"的标准字典。

    - 缺 key -> 用默认值
    - 非数字（字符串/None）-> 用默认值
    - 越界 -> 夹回合法区间
    """
    cleaned: Dict[str, float] = {}
    for key, default in DEFAULT_FEATURES.items():
        raw = (features or {}).get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
        # NaN 无法比较，单独处理（float('nan') != float('nan')）
        if math.isnan(value):
            value = default
        low, high = FEATURE_RANGE[key]
        cleaned[key] = max(low, min(high, value))
    return cleaned


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """从模型返回文本里抠出 JSON。

    模型有时会自作主张加 ```json 围栏或前后寒暄，这里做三层尝试：
      1. 直接 json.loads
      2. 去掉围栏后再 loads
      3. 截取第一个 { 到最后一个 } 再 loads
    """
    if not text:
        return None

    # 尝试 1：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试 2：去掉 markdown 围栏
    stripped = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 尝试 3：截取首尾花括号之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _as_text(value: Any) -> str:
    """把任意值安全转成字符串。

    重点：None 必须转成空字符串，而不是 "None"。
    因为 str(None) == "None" 是个真值，会直接绕过下面的"空则用兜底"判断，
    导致卡片上印出 "None" 字样（模型返回 JSON null 时很容易踩到）。
    """
    if value is None:
        return ""
    return str(value).strip()


def sanitize_sense(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """校验并修复模型返回的 JSON：缺字段补默认、字段不合法就修正。

    这一步非常关键——模型偶尔会少给字段、多给空格、HEX 写错，
    不修的话 HTML 渲染出来的渐变/CSS 就会坏掉。
    """
    if not isinstance(data, dict):
        return dict(FALLBACK_SENSE)

    result: Dict[str, Any] = {}

    # 1) scene_title：必须是字符串，取前 12 字防止模型写太长
    title = _as_text(data.get("scene_title"))
    result["scene_title"] = title[:12] if title else FALLBACK_SENSE["scene_title"]

    # 2) emojis：必须是列表，取前 3 个，不够用兜底补齐
    emojis_raw = data.get("emojis", [])
    if isinstance(emojis_raw, list):
        # _as_text 会把 None 过滤掉（转为空串后被 if 条件剔除）
        emojis = [t for t in (_as_text(e) for e in emojis_raw) if t]
    else:
        emojis = []
    while len(emojis) < 3:
        emojis.append(FALLBACK_SENSE["emojis"][len(emojis)])
    result["emojis"] = emojis[:3]

    # 3) color_palette：逐个用正则校验 HEX，非法就丢弃并用兜底色补齐
    palette_raw = data.get("color_palette", [])
    palette = []
    if isinstance(palette_raw, list):
        for color in palette_raw:
            c = str(color).strip()
            if HEX_RE.match(c):
                palette.append(c)
    while len(palette) < 3:
        palette.append(FALLBACK_SENSE["color_palette"][len(palette)])
    result["color_palette"] = palette[:3]

    # 4) sensory_description：字符串，兜底
    desc = _as_text(data.get("sensory_description"))
    result["sensory_description"] = desc if desc else FALLBACK_SENSE["sensory_description"]

    # 5) ai_art_prompt：字符串，兜底
    prompt = _as_text(data.get("ai_art_prompt"))
    result["ai_art_prompt"] = prompt if prompt else FALLBACK_SENSE["ai_art_prompt"]

    return result


# ======================================================================
# 五、调用 Qwen API
# ======================================================================

def call_qwen_api(features: Dict[str, float], meta: Dict[str, str],
                  avoid_titles: Optional[list] = None,
                  avoid_palettes: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """调用 Qwen（OpenAI 兼容接口）。

    返回：解析并清洗后的 dict；任何异常都返回 None，由调用方降级到 Mock。
    """
    try:
        from openai import OpenAI  # 延迟导入：没装这个库也不影响 Mock 模式跑通
    except ImportError:
        print("  [警告] 未安装 openai 库，无法调用大模型 -> 降级为 Mock 模式")
        print("         修复：pip install openai")
        return None

    try:
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=TIMEOUT)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(features, meta, avoid_titles, avoid_palettes)},
        ]

        # 思考模式会产生额外 token 且可能污染 JSON 输出，显式关掉
        extra_body = {} if ENABLE_THINKING else {"enable_thinking": False}

        # 先尝试"强制 JSON 输出"模式；部分模型不支持时，自动退回普通模式
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME, messages=messages,
                temperature=TEMPERATURE, response_format={"type": "json_object"},
                extra_body=extra_body,
            )
        except Exception:
            response = client.chat.completions.create(
                model=MODEL_NAME, messages=messages, temperature=TEMPERATURE,
            )

        content = response.choices[0].message.content or ""
        parsed = extract_json_from_text(content)
        if parsed is None:
            print("  [警告] 模型返回内容不是合法 JSON -> 降级为 Mock 模式")
            return None
        return parsed

    except Exception as exc:  # 网络错误 / 鉴权失败 / 限流 等一律兜住
        print(f"  [警告] 调用大模型失败（{type(exc).__name__}）：{exc}")
        print("         -> 自动降级为 Mock 模式")
        return None


# ======================================================================
# 六、HTML 渲染（毛玻璃质感卡片）
# ======================================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · SonicNarrative</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:48px 20px;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:linear-gradient(135deg,__C0__ 0%,__C1__ 52%,__C2__ 100%);
background-attachment:fixed;position:relative;overflow-x:hidden}
/* 背景两团缓慢游动的光斑，制造"呼吸感" */
body::before{content:"";position:fixed;top:-120px;left:-80px;width:420px;height:420px;border-radius:50%;
background:__C0__;filter:blur(90px);opacity:.55;animation:float1 14s ease-in-out infinite}
body::after{content:"";position:fixed;bottom:-140px;right:-100px;width:480px;height:480px;border-radius:50%;
background:__C2__;filter:blur(110px);opacity:.5;animation:float2 18s ease-in-out infinite}
@keyframes float1{0%,100%{transform:translate(0,0)}50%{transform:translate(60px,40px)}}
@keyframes float2{0%,100%{transform:translate(0,0)}50%{transform:translate(-50px,-30px)}}
/* 毛玻璃主体：深色半透明 + 背景模糊，保证任何配色下白字都可读 */
.card{position:relative;z-index:1;width:100%;max-width:520px;padding:40px 36px 30px;border-radius:28px;
background:rgba(16,18,28,.55);
backdrop-filter:blur(26px) saturate(160%);-webkit-backdrop-filter:blur(26px) saturate(160%);
border:1px solid rgba(255,255,255,.28);color:#fff;
box-shadow:0 30px 70px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.35)}
.emoji-row{display:flex;gap:14px;font-size:32px;margin-bottom:18px}
.emoji-row span{display:inline-block;animation:bob 3s ease-in-out infinite}
.emoji-row span:nth-child(2){animation-delay:.4s}
.emoji-row span:nth-child(3){animation-delay:.8s}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
.title{font-size:30px;font-weight:700;letter-spacing:3px;text-shadow:0 2px 14px rgba(0,0,0,.45)}
.meta{margin-top:8px;font-size:13px;color:rgba(255,255,255,.72)}
.palette{display:flex;gap:10px;margin:22px 0 20px}
.swatch{flex:1;border-radius:12px;overflow:hidden;border:1px solid rgba(255,255,255,.3)}
.swatch .chip{height:52px}
.swatch .hex{display:block;text-align:center;font-size:11px;padding:5px 0;background:rgba(0,0,0,.3);
color:rgba(255,255,255,.92);font-family:ui-monospace,Consolas,monospace}
.desc{font-size:15px;line-height:2;color:rgba(255,255,255,.96);text-shadow:0 1px 8px rgba(0,0,0,.3)}
.prompt{margin-top:24px;background:rgba(0,0,0,.32);border:1px solid rgba(255,255,255,.18);
border-radius:14px;padding:16px 18px}
.prompt .label{font-size:11px;letter-spacing:1.5px;font-weight:700;color:rgba(255,255,255,.85)}
.prompt .label::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;
background:__C1__;margin-right:8px;vertical-align:middle}
.prompt code{display:block;margin-top:9px;font-size:12px;line-height:1.85;color:rgba(255,255,255,.86);
font-family:ui-monospace,Consolas,monospace;word-break:break-word}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px}
.chip{font-size:11px;padding:5px 12px;border-radius:999px;background:rgba(255,255,255,.14);
border:1px solid rgba(255,255,255,.24);color:rgba(255,255,255,.92)}
.footer{margin-top:22px;padding-top:16px;border-top:1px solid rgba(255,255,255,.16);
font-size:11px;color:rgba(255,255,255,.55)}
</style>
</head>
<body>
  <div class="card">
    <div class="emoji-row">__EMOJIS__</div>
    <h1 class="title">__TITLE__</h1>
    <div class="meta">__META__</div>
    <div class="palette">__SWATCHES__</div>
    <p class="desc">__DESC__</p>
    <div class="prompt"><span class="label">AI ART PROMPT</span><code>__PROMPT__</code></div>
    <div class="chips">__CHIPS__</div>
    <div class="footer">SonicNarrative · 特征感知翻译器 __MODE__</div>
  </div>
</body>
</html>
"""


def render_html_card(sense: Dict[str, Any], features: Dict[str, float],
                     meta: Dict[str, str], mode_note: str = "") -> str:
    """把通感 JSON 渲染成自包含的 HTML 卡片字符串。

    安全处理：所有来自模型的文本都经过 html.escape，防止模型输出
    尖括号之类的内容把 HTML 结构破坏掉（XSS / 结构注入）。
    """
    palette = sense["color_palette"]
    c0, c1, c2 = palette[0], palette[1], palette[2]

    # 1) emoji：转义后包进 span
    emojis_html = "".join(
        f"<span>{html_lib.escape(str(e))}</span>" for e in sense["emojis"]
    )

    # 2) 顶部信息行：歌名 · 艺人 · BPM
    meta_line = f"{meta.get('track_name', '未知曲目')} · {meta.get('artist', '未知艺人')} · {features['tempo']:.0f} BPM"

    # 3) 配色条：颜色值已通过 HEX 正则校验，可安全内联进 style
    swatches_html = "".join(
        f'<div class="swatch"><div class="chip" style="background:{color}"></div>'
        f'<span class="hex">{html_lib.escape(color)}</span></div>'
        for color in palette
    )

    # 4) 特征值小标签
    chips_html = "".join(
        f'<span class="chip">{FEATURE_LABEL[key]} {features[key]:.2f}</span>'
        for key in ["valence", "energy", "danceability", "acousticness"]
    )
    chips_html += f'<span class="chip">{FEATURE_LABEL["tempo"]} {features["tempo"]:.0f}</span>'

    # 5) 用占位符替换的方式组装（避免 f-string 与 CSS 花括号冲突）
    page = (HTML_TEMPLATE
            .replace("__C0__", c0).replace("__C1__", c1).replace("__C2__", c2)
            .replace("__EMOJIS__", emojis_html)
            .replace("__TITLE__", html_lib.escape(sense["scene_title"]))
            .replace("__META__", html_lib.escape(meta_line))
            .replace("__SWATCHES__", swatches_html)
            .replace("__DESC__", html_lib.escape(sense["sensory_description"]))
            .replace("__PROMPT__", html_lib.escape(sense["ai_art_prompt"]))
            .replace("__CHIPS__", chips_html)
            .replace("__MODE__", html_lib.escape(mode_note)))
    return page


# ======================================================================
# 七、对外主函数（第三步要求：单函数封装）
# ======================================================================

def generate_sense_card(features_dict: Dict[str, Any],
                        meta: Optional[Dict[str, str]] = None,
                        use_llm: Optional[bool] = None,
                        avoid_titles: Optional[list] = None,
                        avoid_palettes: Optional[list] = None) -> Tuple[str, Dict[str, Any]]:
    """输入音频特征 -> 输出 (HTML 卡片字符串, 通感 JSON 数据)。

    参数
    ----
    features_dict : dict
        至少包含 valence / energy / danceability / acousticness / tempo。
        缺失或非法的键会自动补默认值并夹到合法区间，绝不会抛异常。
    meta : dict, 可选
        歌曲信息，如 {"track_name": "xxx", "artist": "yyy"}
    use_llm : bool, 可选
        是否调用大模型；默认 None = 有 API Key 就调，没有就走 Mock。
    avoid_titles / avoid_palettes : list, 可选
        批量生成时传入已用过的场景名和配色，模型会主动避开，防止重复。

    返回
    ----
    (html_str, sense_dict)
        html_str   : 可直接写入 .html 文件的完整卡片
        sense_dict : 含 5 个字段的通感数据

    容错链条：真实模型 -> 失败则 Mock 预设 -> 再失败则终极兜底卡片。
    """
    # 步骤 1：清洗入参（缺值补默认、越界夹回）
    features = normalize_features(features_dict)

    # 步骤 2：清洗歌曲信息
    safe_meta = {
        "track_name": str((meta or {}).get("track_name") or "未知曲目"),
        "artist": str((meta or {}).get("artist") or "未知艺人"),
    }

    # 步骤 3：决定走真实模型还是 Mock
    if use_llm is None:
        use_llm = bool(API_KEY)

    raw_data: Optional[Dict[str, Any]] = None
    mode_note = ""

    if use_llm and API_KEY:
        raw_data = call_qwen_api(features, safe_meta, avoid_titles, avoid_palettes)
        if raw_data is not None:
            mode_note = "· LLM 生成"
        else:
            # API 调用失败 -> 自动降级
            raw_data = pick_mock_preset(features)
            mode_note = "· Mock 兜底（API 不可用）"
    else:
        if use_llm and not API_KEY:
            print("  [提示] 未检测到 DASHSCOPE_API_KEY -> 自动进入 Mock 模式")
        raw_data = pick_mock_preset(features)
        mode_note = "· Mock 模式"

    # 步骤 4：校验/修复 JSON（模型脏数据也能修好）
    sense = sanitize_sense(raw_data)

    # 步骤 5：渲染 HTML
    html_str = render_html_card(sense, features, safe_meta, mode_note)

    return html_str, sense


def save_card(html_str: str, file_path: str) -> str:
    """把卡片 HTML 写入文件，自动创建目录，返回绝对路径。"""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    return os.path.abspath(file_path)


# ======================================================================
# 八、批量模式主流程
# ======================================================================

def safe_filename(text: str) -> str:
    """把歌曲名转成安全的文件名（去掉 / \\ : * ? " < > | 等非法字符）。"""
    return re.sub(r'[\\/:*?"<>|]+', "_", str(text)).strip() or "track"


def main() -> None:
    """读取 CSV 前 5 首歌，批量生成 HTML 卡片到 output_cards/ 目录。"""
    try:
        import pandas as pd
    except ImportError:
        print("[错误] 缺少 pandas，请先安装：pip install pandas")
        return

    # 找不到清洗后的文件就退回原始文件，方便你直接跑
    csv_path = INPUT_CSV
    if not os.path.exists(csv_path) and os.path.exists("raw_spotify_data.csv"):
        print(f"[提示] 未找到 {csv_path}，改用 raw_spotify_data.csv")
        csv_path = "raw_spotify_data.csv"
    if not os.path.exists(csv_path):
        print(f"[错误] 找不到数据文件：{csv_path}")
        print("       请先运行 data_cleaning.py 生成清洗后的 CSV")
        return

    df = pd.read_csv(csv_path)
    df.columns = [str(c).strip() for c in df.columns]

    print("=" * 60)
    print(f"  SonicNarrative · 批量生成感知卡片（{OUTPUT_DIR}/）")
    print(f"  模式：{'LLM（已检测到 API Key）' if API_KEY else 'Mock（未设置 DASHSCOPE_API_KEY）'}")
    print("=" * 60)

    songs = df.head(BATCH_SIZE)

    # 记录本批次已经用过的场景名/配色，传给下一首让模型避开（打破小模型的用词惯性）
    used_titles: list = []
    used_palettes: list = []

    for idx, row in enumerate(songs.iterrows(), start=1):
        _, row = row  # iterrows 返回 (index, Series)
        features = {k: row.get(k, DEFAULT_FEATURES[k]) for k in DEFAULT_FEATURES}
        meta = {
            "track_name": row.get("track_name", "未知曲目"),
            "artist": row.get("artist", "未知艺人"),
        }

        print(f"\n[{idx}/{len(songs)}] {meta['track_name']} - {meta['artist']}")
        html_str, sense = generate_sense_card(
            features, meta,
            avoid_titles=used_titles or None,
            avoid_palettes=used_palettes or None,
        )
        used_titles.append(sense["scene_title"])
        used_palettes.append(sense["color_palette"])
        print(f"    场景：{sense['scene_title']}  配色：{' '.join(sense['color_palette'])}")

        filename = f"{idx:02d}_{safe_filename(meta['track_name'])}.html"
        saved = save_card(html_str, os.path.join(OUTPUT_DIR, filename))
        print(f"    已保存：{saved}")

    print("\n" + "=" * 60)
    print(f"[完成] 共生成 {len(songs)} 张卡片，输出目录：{os.path.abspath(OUTPUT_DIR)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
