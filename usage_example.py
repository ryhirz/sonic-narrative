# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 第三步使用示例
=========================================================================
 演示如何只用一个函数 generate_sense_card() 就能得到：
   - HTML 卡片字符串（可直接存成 .html 打开）
   - 对应的通感 JSON 数据

 运行：python usage_example.py
=========================================================================
"""

import json

from synesthesia_core import generate_sense_card, save_card

# ----------------------------------------------------------------------
# 示例 1：最基础的调用 —— 传 5 个特征，拿到卡片
# ----------------------------------------------------------------------
print("=" * 60)
print("示例 1：单首歌生成卡片")
print("=" * 60)

features = {
    "valence": 0.83,       # 情绪积极度：很高 -> 明亮愉悦
    "energy": 0.91,        # 能量：很高 -> 激烈
    "danceability": 0.72,  # 舞蹈性：偏高 -> 有律动
    "acousticness": 0.05,  # 原声度：极低 -> 电子合成感
    "tempo": 168,          # 速度 BPM
}
meta = {"track_name": "Neon Pulse", "artist": "Demo Band"}

# 一行调用：返回 (HTML字符串, JSON数据)
html_str, sense_data = generate_sense_card(features, meta)

# 查看 JSON 数据
print("\n【返回的 JSON 数据】")
print(json.dumps(sense_data, ensure_ascii=False, indent=2))

# 保存成 HTML 文件，双击即可在浏览器打开
path = save_card(html_str, "output_cards/demo_neon_pulse.html")
print(f"\n【卡片已保存】{path}")
print(f"【HTML 长度】{len(html_str)} 字符")


# ----------------------------------------------------------------------
# 示例 2：批量生成 —— 三首风格迥异的歌，看映射差异
# ----------------------------------------------------------------------
print("\n" + "=" * 60)
print("示例 2：批量生成（不同特征 -> 不同场景）")
print("=" * 60)

song_library = [
    {
        "meta": {"track_name": "午夜霓虹", "artist": "Synth Rider"},
        "features": {"valence": 0.55, "energy": 0.92, "danceability": 0.80,
                     "acousticness": 0.03, "tempo": 174},
    },
    {
        "meta": {"track_name": "晨雾松林", "artist": "Ambient Soul"},
        "features": {"valence": 0.35, "energy": 0.12, "danceability": 0.20,
                     "acousticness": 0.94, "tempo": 62},
    },
    {
        "meta": {"track_name": "落日冲浪", "artist": "Summer Tape"},
        "features": {"valence": 0.90, "energy": 0.70, "danceability": 0.85,
                     "acousticness": 0.25, "tempo": 108},
    },
]

for i, item in enumerate(song_library, start=1):
    html_str, sense_data = generate_sense_card(item["features"], item["meta"])
    filename = f"output_cards/demo_{i:02d}_{item['meta']['track_name']}.html"
    saved = save_card(html_str, filename)
    print(f"\n{i}. {item['meta']['track_name']}")
    print(f"   场景名：{sense_data['scene_title']}")
    print(f"   Emoji ：{' '.join(sense_data['emojis'])}")
    print(f"   配色  ：{'  '.join(sense_data['color_palette'])}")
    print(f"   文件  ：{saved}")


# ----------------------------------------------------------------------
# 示例 3：异常输入兜底 —— 故意传脏数据，验证不会崩
# ----------------------------------------------------------------------
print("\n" + "=" * 60)
print("示例 3：异常输入测试（缺字段 / 非数字 / 越界）")
print("=" * 60)

bad_features = {
    "valence": "abc",      # 非数字 -> 用默认值
    "energy": 5.0,         # 越界   -> 夹回 1.0
    # danceability 缺失    -> 用默认值
    "acousticness": None,  # None   -> 用默认值
    "tempo": -999,         # 越界   -> 夹回 40
}

html_str, sense_data = generate_sense_card(bad_features)
print("\n传入脏数据后仍正常返回：")
print(f"  场景名：{sense_data['scene_title']}")
print(f"  配色  ：{'  '.join(sense_data['color_palette'])}")
print(f"  HTML 长度：{len(html_str)} 字符（说明兜底卡片已生成）")

# 如果你希望确认清洗后的特征值，可以引入 normalize_features 查看
from synesthesia_core import normalize_features  # noqa: E402

print(f"  清洗后的特征：{normalize_features(bad_features)}")


# ----------------------------------------------------------------------
# 示例 4：强制走 Mock 模式（比如做演示时不想消耗 API 额度）
# ----------------------------------------------------------------------
print("\n" + "=" * 60)
print("示例 4：强制 Mock 模式（use_llm=False）")
print("=" * 60)

html_str, sense_data = generate_sense_card(features, meta, use_llm=False)
print(f"场景名：{sense_data['scene_title']}（来自本地预设，不消耗额度）")

print("\n全部示例执行完毕，去 output_cards/ 目录查看生成的卡片吧。")
