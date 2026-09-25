# -*- coding: utf-8 -*-
"""
=========================================================================
 LLM 分支的【离线】验证 —— tests/test_llm_branch_offline.py
=========================================================================
 和 tests/test_llm_live.py 的分工：
   · 本文件  ：把大模型的返回"打桩"成假数据，**不联网、不需要 Key**，
               验证的是「LLM 管线」本身 —— 提示词 -> 返回解析 -> 校验修复
               -> 渲染 -> 模式标记 -> 失败降级 是否都正确。
   · live 文件：真连大模型，验证"网络+鉴权+模型"这一环。

 为什么值得单独测：
   没有 Key 时，LLM 分支平时永远不执行。只靠 live 测试的话，
   「代码里写了但没验证过」的风险依旧存在（比如 JSON 解析写错、
   降级没接上）。本文件把这些分支全部执行一遍 —— 零成本、秒级、CI 常绿。
=========================================================================
"""

from __future__ import annotations

import json

import pytest

import reverse_engine
import synesthesia_core

FEATURES = {
    "valence": 0.82, "energy": 0.78, "danceability": 0.71,
    "acousticness": 0.12, "tempo": 118.0,
}
META = {"track_name": "Stub Track", "artist": "Unit Test"}

FAKE_SENSE = {
    "scene_title": "霓虹夜行",
    "emojis": ["🌃", "🚗", "💡"],
    "color_palette": ["#0B1026", "#1F6FEB", "#FF2D95"],
    "sensory_description": "夜色像一层薄黑胶，路灯在湿漉漉的路面拉出长长的光带。",
    "ai_art_prompt": "Neon-lit night drive, wet asphalt reflections, cinematic --ar 16:9",
}


# ======================================================================
# 一、翻译器：LLM 分支
# ======================================================================

def test_translate_llm_branch_renders_llm_card(monkeypatch):
    """大模型返回合法 JSON 时，卡片必须由 LLM 分支渲染出来。"""
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    monkeypatch.setattr(synesthesia_core, "call_qwen_api", lambda *a, **k: dict(FAKE_SENSE))

    html_str, sense = synesthesia_core.generate_sense_card(FEATURES, META, use_llm=True)

    assert "LLM 生成" in html_str, "未走到 LLM 分支"
    assert "Mock 兜底" not in html_str
    assert sense["scene_title"] == "霓虹夜行"
    assert "#0B1026" in html_str, "模型给的配色没有被渲染进卡片"


def test_translate_llm_branch_degrades_when_api_returns_none(monkeypatch):
    """大模型调用失败（返回 None）时，必须静默降级到 Mock 兜底，而不是崩。"""
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    monkeypatch.setattr(synesthesia_core, "call_qwen_api", lambda *a, **k: None)

    html_str, _ = synesthesia_core.generate_sense_card(FEATURES, META, use_llm=True)

    assert "Mock 兜底" in html_str, "调用失败未正确降级"


def test_translate_sanitizes_dirty_llm_output(monkeypatch):
    """模型偶尔写错 HEX / 少给字段，sanitize 必须修好而不是渲染出坏 CSS。"""
    dirty = {
        "scene_title": "这是一个非常非常非常长的场景标题超过十二个字",
        "emojis": ["🎵"],                       # 不足 3 个
        "color_palette": ["not-a-hex", "#123456"],  # 非法色值
        "sensory_description": "",              # 空描述
        # 故意不给 ai_art_prompt
    }
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    monkeypatch.setattr(synesthesia_core, "call_qwen_api", lambda *a, **k: dict(dirty))

    html_str, sense = synesthesia_core.generate_sense_card(FEATURES, META, use_llm=True)

    assert len(sense["scene_title"]) <= 12, "标题未被截断"
    assert len(sense["emojis"]) == 3, "emoji 未补齐"
    assert all(c.startswith("#") and len(c) == 7 for c in sense["color_palette"]), "配色未修复"
    assert sense["sensory_description"], "空描述未兜底"
    assert sense.get("ai_art_prompt"), "缺失的 ai_art_prompt 未兜底"


# ======================================================================
# 二、反向寻歌：LLM 分支
# ======================================================================

def _patch_openai(monkeypatch, payload_text: str) -> None:
    """把 openai.OpenAI 换成假客户端，返回指定的 content 字符串。"""
    import openai

    class _Completions:
        def create(self, **kwargs):
            message = type("Msg", (), {"content": payload_text})()
            choice = type("Choice", (), {"message": message})()
            return type("Resp", (), {"choices": [choice]})()

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)


def test_reverse_llm_branch_parses_json(monkeypatch):
    """模型返回合法 JSON -> mode 必须是 llm，且特征被正确解析。"""
    payload = json.dumps({
        "valence": 0.30, "energy": 0.55, "danceability": 0.60,
        "acousticness": 0.20, "tempo": 110, "reason": "夜色与孤独感的叠加",
    })
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    _patch_openai(monkeypatch, payload)

    result = reverse_engine.reverse_search("深夜独自开车", top_k=3, use_llm=True)

    assert result["mode"] == "llm"
    assert result["reason"] == "夜色与孤独感的叠加"
    assert result["features"]["valence"] == pytest.approx(0.30)
    assert result["features"]["tempo"] == pytest.approx(110.0)
    assert len(result["matches"]) > 0


def test_reverse_llm_branch_degrades_on_non_json(monkeypatch):
    """模型胡言乱语（不是 JSON）-> 必须降级关键词规则，而不是崩。"""
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    _patch_openai(monkeypatch, "抱歉，我不太确定你的意思……")

    result = reverse_engine.reverse_search("深夜独自开车", top_k=2, use_llm=True)

    assert result["mode"] == "keyword"


def test_reverse_llm_branch_degrades_on_missing_field(monkeypatch):
    """模型少给字段 -> 直接降级，不猜。"""
    payload = json.dumps({"valence": 0.30})   # 缺 energy 等 4 个字段
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    _patch_openai(monkeypatch, payload)

    result = reverse_engine.reverse_search("深夜独自开车", top_k=2, use_llm=True)

    assert result["mode"] == "keyword"


def test_reverse_clamps_out_of_range_llm_values(monkeypatch):
    """模型给出越界值 -> 必须夹回合法区间。"""
    payload = json.dumps({
        "valence": 1.8, "energy": -0.5, "danceability": 0.6,
        "acousticness": 0.2, "tempo": 999, "reason": "越界测试",
    })
    monkeypatch.setattr(synesthesia_core, "API_KEY", "unit-test-key")
    _patch_openai(monkeypatch, payload)

    result = reverse_engine.reverse_search("随便写点", top_k=2, use_llm=True)

    assert result["mode"] == "llm"
    assert 0.0 <= result["features"]["valence"] <= 1.0
    assert 0.0 <= result["features"]["energy"] <= 1.0
    assert 40.0 <= result["features"]["tempo"] <= 220.0
