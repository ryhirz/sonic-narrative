# -*- coding: utf-8 -*-
"""
=========================================================================
 真实大模型（LLM）链路集成测试 —— tests/test_llm_live.py
=========================================================================
 为什么单独一个文件：
   翻译器 / 反向寻歌的「大模型分支」在没配 Key 时会自动降级 Mock，
   于是这条链路平时永远跑不到。本文件把「真有 Key 时真调一次大模型」
   变成一条可执行的断言 —— 从此不再是"代码里写了但没验证过"。

 运行条件：
   · 配了 DASHSCOPE_API_KEY（写进项目根目录 .env 即可）
        -> 真实调用 qwen，断言拿到的是大模型结果
   · 没配（.env 留空）
        -> 自动 skip，不会让常规 pytest / CI 变红

 手动单独跑：
   .venv\\Scripts\\python.exe -m pytest tests/test_llm_live.py -v

 只想跑这条（跳过它）：
   .venv\\Scripts\\python.exe -m pytest -m "not live"
=========================================================================
"""

from __future__ import annotations

import pytest

import reverse_engine
import synesthesia_core

# 是否有可用 Key —— 整个文件的可运行开关
_HAS_KEY = bool(synesthesia_core.API_KEY)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _HAS_KEY,
        reason="未配置 DASHSCOPE_API_KEY（.env 留空）—— 跳过真实大模型端到端测试",
    ),
]

# 一组「高唤醒 / 明快」的特征，方便对照
FEATURES = {
    "valence": 0.82,
    "energy": 0.78,
    "danceability": 0.71,
    "acousticness": 0.12,
    "tempo": 118.0,
}
META = {"track_name": "Live Test Track", "artist": "SonicNarrative"}


def test_translate_live_really_calls_llm():
    """翻译器：配了 Key 时，产出的卡片必须真的来自大模型。"""
    html_str, sense = synesthesia_core.generate_sense_card(FEATURES, META, use_llm=True)

    assert isinstance(html_str, str) and len(html_str) > 200, "卡片 HTML 过短，疑似渲染异常"
    # 关键断言：卡片里必须带「LLM 生成」标记。
    # 若这里变成 Mock，说明 API 调用其实失败了（鉴权 / 限流 / 网络）——
    # 这正是本测试要抓的"演示时说接了大模型、实际没通"的风险。
    assert "LLM 生成" in html_str, (
        "已配置 API Key 但未走真实大模型（可能鉴权/限流/网络失败），详见 pytest 上方日志"
    )
    assert "Mock 兜底" not in html_str, "出现 Mock 兜底，说明真实调用失败"

    assert isinstance(sense, dict) and len(sense) > 0, "通感 JSON 结构为空"


def test_reverse_live_really_calls_llm():
    """反向寻歌：配了 Key 时，特征反推必须走大模型分支（mode == 'llm'）。"""
    result = reverse_engine.reverse_search(
        "深夜独自开车，霓虹在车窗外流过", top_k=3, use_llm=True
    )

    assert result["mode"] == "llm", (
        f"期望 mode='llm'，实际为 '{result['mode']}' —— 大模型调用未生效或已降级"
    )
    assert result["library_size"] > 0
    assert len(result["matches"]) > 0

    # 反推出的特征都应是合法数值（不能是 None / 字符串）
    for key, value in result["features"].items():
        assert isinstance(value, (int, float)), f"特征 {key} 非法：{value!r}"

    # 匹配度应在 0~100
    for m in result["matches"]:
        assert 0.0 <= float(m["match_score"]) <= 100.0
