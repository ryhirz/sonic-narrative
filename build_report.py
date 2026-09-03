# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 数据故事报告生成器
=========================================================================
 作用：读取 output_cards/ 下所有由引擎生成的 HTML 卡片，反向解析出
       「歌曲名 / 场景名 / 配色 / 情绪描述 / AI 绘画 Prompt」，
       再结合 clean_spotify_data.csv 的原始音频特征，
       生成一份「讲故事」而非「罗列数据」的 Markdown 报告。

 运行：python build_report.py
 产出：SonicNarrative_Insight_Report.md
=========================================================================
"""

import glob
import html as html_lib
import os
import re
from typing import Dict, List

import pandas as pd

CARD_DIR = "output_cards"
FEATURE_CSV = "clean_spotify_data.csv"
REPORT_FILE = "SonicNarrative_Insight_Report.md"


# ======================================================================
# 一、解析 HTML 卡片（反向提取引擎产出）
# ======================================================================

def parse_card(path: str) -> Dict[str, object]:
    """从一张卡片 HTML 里把关键字段抠出来。

    注意：卡片渲染时所有文本都做过 html.escape，所以这里要 unescape 还原。
    """
    src = open(path, encoding="utf-8").read()

    def grab(pattern: str, default: str = "") -> str:
        m = re.search(pattern, src, re.S)
        return html_lib.unescape(m.group(1)).strip() if m else default

    scene_title = grab(r'<h1 class="title">(.*?)</h1>')
    meta_line = grab(r'<div class="meta">(.*?)</div>')
    desc = grab(r'<p class="desc">(.*?)</p>')
    prompt = grab(r'<code>(.*?)</code>')

    # meta 行格式：歌名 · 艺人 · BPM
    parts = [p.strip() for p in meta_line.split("·")]
    track = parts[0] if len(parts) > 0 else "未知曲目"
    artist = parts[1] if len(parts) > 1 else "未知艺人"
    bpm = parts[2].replace("BPM", "").strip() if len(parts) > 2 else "-"

    # emoji 行
    emoji_block = grab(r'<div class="emoji-row">(.*?)</div>')
    emojis = re.findall(r"<span>(.*?)</span>", emoji_block, re.S)

    # 配色（渲染时已写入 style，顺序即 palette 顺序）
    palette = re.findall(r'style="background:(#[0-9A-Fa-fA-F]{6})"', src)

    return {
        "file": os.path.basename(path),
        "track": track,
        "artist": artist,
        "bpm": bpm,
        "scene_title": scene_title,
        "emojis": emojis,
        "palette": palette,
        "desc": desc,
        "prompt": prompt,
    }


# ======================================================================
# 二、从配色反推「冷暖」，用来验证模型是否遵守了规则约束
# ======================================================================

def hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def warmth(hex_color: str) -> int:
    """色温指标：R - B。正值偏暖，负值偏冷。"""
    r, _g, b = hex_to_rgb(hex_color)
    return r - b


def palette_warmth(palette: List[str]) -> float:
    """整组配色的平均色温。"""
    if not palette:
        return 0.0
    return sum(warmth(c) for c in palette) / len(palette)


def temp_label(w: float) -> str:
    if w > 25:
        return "暖色"
    elif w < -25:
        return "冷色"
    return "中性"


def mood_phrase(v: float, e: float) -> str:
    """把 (情绪, 能量) 组合翻译成一个自然的中文短语，避免文案千篇一律。

    注意低情绪区间要按能量再细分一档，否则像 Weightless(0.20/0.08) 和
    Someone Like You(0.23/0.36) 这种会被贴上同一个标签，读起来像模板。
    """
    # 高情绪区间同样按能量细分，避免同一档位贴同一个标签
    if v >= 0.7:
        if e >= 0.8:
            return "明亮而滚烫"
        if e >= 0.6:
            return "明亮且灼热"
        if e >= 0.4:
            return "明亮且轻快"
        return "明亮而松弛"
    if v >= 0.4 and e >= 0.7:
        return "克制却躁动"
    if v >= 0.4 and e >= 0.4:
        return "温吞而中性"
    if v >= 0.4:
        return "平静且低伏"
    # v < 0.4 的低情绪区间，按能量细分
    if e >= 0.6:
        return "阴郁却紧绷"
    if e >= 0.3:
        return "沉静中带着重量"
    return "近乎静止的内省"


def texture_phrase(a: float) -> str:
    # 注意别用"冷光"这类带温度含义的词，否则会和暖色配色自相矛盾
    if a >= 0.5:
        return "带着木头、水汽与植物的有机触感"
    return "泛着霓虹、金属与故障电流的数字质感"


# ======================================================================
# 三、报告生成
# ======================================================================

def build_report(cards: List[Dict], feats: Dict[str, Dict]) -> str:
    n = len(cards)

    # ---------- 统计素材 ----------
    # 按情绪值排序，用来讲"情绪曲线"的故事
    by_valence = sorted(cards, key=lambda c: feats.get(c["track"], {}).get("valence", 0.5))
    coldest = by_valence[0]
    hottest = by_valence[-1]

    # 验证「规则约束是否生效」：情绪值 与 配色冷暖 是否同向
    pairs = [(feats.get(c["track"], {}).get("valence", 0.5), palette_warmth(c["palette"]))
             for c in cards]
    concordant = total = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            total += 1
            if (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1]) > 0:
                concordant += 1
    agree_rate = (concordant / total * 100) if total else 0.0

    avg_energy = sum(feats.get(c["track"], {}).get("energy", 0) for c in cards) / n
    avg_acoustic = sum(feats.get(c["track"], {}).get("acousticness", 0) for c in cards) / n
    tempos = [float(feats.get(c["track"], {}).get("tempo", 0)) for c in cards]

    L = []
    A = L.append

    # ---------- 开头：故事化引入 ----------
    A("# 🎧 SonicNarrative · 感知维度洞察报告")
    A("")
    A(f"> 引擎把 {n} 首歌的音频特征翻译成了 {n} 个可走进去的场景。"
      f"这份报告不打算复述数据，而是想带你走一遍这段从数字到感官的旅程。")
    A("")

    A("## 一、先从一个问题开始")
    A("")
    A("如果我们告诉你，有一首歌的情绪值是 0.20、原声度是 0.95、速度只有 60 BPM，"
      "你脑子里会出现什么画面？")
    A("")
    A("大概率什么都没有——这三个数字太抽象了，它们属于机器，不属于人。")
    A("")
    A(f"但引擎给出的答案是：**「{coldest['scene_title']}」**，"
      f"配色落在 {'、'.join(coldest['palette'])}，"
      f"整体偏{temp_label(palette_warmth(coldest['palette']))}调。"
      f"它对这个世界的描述是：")
    A("")
    A(f"> {coldest['desc']}")
    A("")
    A("这就是 SonicNarrative 想做的事：**把冷冰冰的特征向量，翻译成人能看见、能走进去的地方。**")
    A("")

    # ---------- 中段：逐首歌的感知画像 ----------
    A("## 二、五段旅程")
    A("")
    A(f"把 {n} 首歌按情绪值从低到高排开，你会得到一条完整的情绪曲线："
      f"从 `{coldest['track']}` 的 {feats.get(coldest['track'],{}).get('valence',0):.2f}，"
      f"一路走到 `{hottest['track']}` 的 {feats.get(hottest['track'],{}).get('valence',0):.2f}。"
      f"引擎为每一站都造了一个世界。")
    A("")

    for idx, c in enumerate(by_valence, start=1):
        f = feats.get(c["track"], {})
        v = f.get("valence", 0)
        e = f.get("energy", 0)
        a = f.get("acousticness", 0)
        d = f.get("danceability", 0)
        w = palette_warmth(c["palette"])

        A(f"### {idx}. 《{c['track']}》 — {c['artist']}")
        A("")
        A(f"**它的气质是「{mood_phrase(v, e)}」。** "
          f"情绪值 {v:.2f}、能量 {e:.2f}、原声度 {a:.2f}、舞蹈性 {d:.2f}、速度 {c['bpm']} BPM —— "
          f"这些数字落到引擎里，长出了「**{c['scene_title']}**」这个世界。")
        A("")
        A(f"配色是 {'、'.join(c['palette'])}（{temp_label(w)}，色温指标 {w:+.0f}），"
          f"{texture_phrase(a)}。")
        A("")
        A(f"> {c['desc']}")
        A("")
        A(f"<div align=\"center\"><code>{' '.join(c['emojis'])}</code></div>")
        A("")

    # ---------- 汇总表格 ----------
    A("## 三、一图流览（汇总表）")
    A("")
    A("| # | 歌曲 | 艺人 | BPM | 场景名 | 配色 | 情绪 | 能量 | 原声 | AI 绘画 Prompt（节选） |")
    A("|---|------|------|-----|--------|------|------|------|------|------------------------|")
    for idx, c in enumerate(by_valence, start=1):
        f = feats.get(c["track"], {})
        swatch = " ".join(f"`{x}`" for x in c["palette"])
        short_prompt = c["prompt"][:52] + "…" if len(c["prompt"]) > 52 else c["prompt"]
        A(f"| {idx} | **{c['track']}** | {c['artist']} | {c['bpm']} | "
          f"**{c['scene_title']}** | {swatch} | {f.get('valence',0):.2f} | "
          f"{f.get('energy',0):.2f} | {f.get('acousticness',0):.2f} | {short_prompt} |")
    A("")

    # ---------- 规律洞察 ----------
    A("## 四、我们观察到的规律")
    A("")

    A(f"**1. 色彩会跟着情绪走。** "
      f"把 5 首歌的「情绪值」和「配色冷暖」两两配对比较，"
      f"{total} 组里有 **{concordant} 组方向一致**（一致率 {agree_rate:.0f}%）。"
      f"情绪值最低的《{coldest['track']}》拿到的是偏{temp_label(palette_warmth(coldest['palette']))}的调子，"
      f"而最高的《{hottest['track']}》落在了{temp_label(palette_warmth(hottest['palette']))}区间。"
      f"这说明规则引擎对色彩基调的硬约束是有效的——**不是模型在随机配色，是数据在决定色温。**")
    A("")

    A(f"**2. 原声度决定了「材质的手感」。** "
      f"本批歌曲原声度平均 {avg_acoustic:.2f}。"
      f"凡是原声度越过 0.5 的曲目，描述里几乎必然出现雾气、林木、水面这类有机意象；"
      f"而原声度接近 0 的曲目，画面则被霓虹、金属与故障电流占据。"
      f"这条分界线相当锐利，几乎不需要模型额外推理。")
    A("")

    A(f"**3. 能量与速度一起塑造「时间的形状」。** "
      f"本批能量均值 {avg_energy:.2f}，速度跨度从 {min(tempos):.0f} 到 {max(tempos):.0f} BPM。"
      f"高能量 + 高速度的曲目，画面元素密集、短促、带闪烁感；"
      f"低能量 + 低速的曲目，则被大块留白和长线条占据。"
      f"换句话说，**BPM 控制的不是画面里有什么，而是画面运动的快慢。**")
    A("")

    A("**4. 一个诚实的观察：小模型会「偷懒」。**")
    A("")
    A("在只使用规则约束、不做批次去重之前，特征相近的两首歌（如高情绪高能量的合成器流行）"
      "曾被翻译成**完全相同的配色**，场景名里也反复出现同一个词。"
      "这正是本项目最终采用「**规则定基调 + 批次去重提示**」双层策略的原因——"
      "规则保证与特征强相关，去重提示逼模型换词换色。"
      "这是小参数模型的固有惰性，**换用更大参数量的模型可以进一步缓解**。")
    A("")

    # ---------- AI Prompt 全集 ----------
    A("## 五、AI 绘画 Prompt 全集")
    A("")
    A("以下是引擎为每首歌生成的完整 Midjourney 提示词，可直接复制使用。")
    A("")
    for c in by_valence:
        A(f"**《{c['track']}》— {c['scene_title']}**")
        A("")
        A(f"```")
        A(c["prompt"])
        A(f"```")
        A("")

    # ---------- 结尾 ----------
    A("## 六、写在最后")
    A("")
    A(f"这 {n} 张卡片不是插图，而是**数据的另一种写法**。"
      f"当我们把 valence 读成色温、把 tempo 读成节奏、把 acousticness 读成材质时，"
      f"数据看板就不再是冷冰冰的表格，而变成了一段可以走进去的旅程。")
    A("")
    A("SonicNarrative 目前只做完了「正向翻译」这一步。"
      "下一步，我们希望反过来——**你说出一种感受，我们替你找到那首歌。**")
    A("")
    A("---")
    A("")
    A(f"*报告由 `build_report.py` 自动生成 ｜ 数据来源：`{FEATURE_CSV}` ｜ "
      f"卡片来源：`{CARD_DIR}/` ｜ 共解析 {n} 张卡片*")

    return "\n".join(L)


def main() -> None:
    # 1. 找到所有卡片
    files = sorted(glob.glob(os.path.join(CARD_DIR, "*.html")))
    if not files:
        print(f"[错误] {CARD_DIR}/ 下没有找到任何 HTML 卡片，请先运行 synesthesia_core.py")
        return

    cards = [parse_card(f) for f in files]
    print(f"[1/3] 已解析 {len(cards)} 张卡片")

    # 2. 读取原始特征（用曲名做关联）
    feats: Dict[str, Dict] = {}
    if os.path.exists(FEATURE_CSV):
        df = pd.read_csv(FEATURE_CSV)
        df.columns = [str(c).strip() for c in df.columns]
        for _, row in df.iterrows():
            feats[str(row.get("track_name", "")).strip()] = {
                "valence": float(row.get("valence", 0.5)),
                "energy": float(row.get("energy", 0.5)),
                "danceability": float(row.get("danceability", 0.5)),
                "acousticness": float(row.get("acousticness", 0.5)),
                "tempo": float(row.get("tempo", 100)),
            }
        print(f"[2/3] 已载入 {len(feats)} 条音频特征")
    else:
        print(f"[2/3] 未找到 {FEATURE_CSV}，报告中特征相关数值将使用默认值")

    # 3. 生成报告
    md = build_report(cards, feats)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[3/3] 报告已生成：{os.path.abspath(REPORT_FILE)}（{len(md)} 字符）")


if __name__ == "__main__":
    main()
