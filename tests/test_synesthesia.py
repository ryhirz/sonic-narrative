# -*- coding: utf-8 -*-
"""翻译器核心引擎测试：normalize_features 容错 + Mock 出卡 + 文件名安全。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_synesthesia.py -q
"""
from synesthesia_core import generate_sense_card, normalize_features, safe_filename


def test_normalize_features_clamps_out_of_range():
    fixed = normalize_features({"valence": 5.0, "energy": -3.0, "tempo": 999})
    assert 0.0 <= fixed["valence"] <= 1.0
    assert 0.0 <= fixed["energy"] <= 1.0
    assert 40.0 <= fixed["tempo"] <= 220.0


def test_normalize_features_fills_missing():
    fixed = normalize_features({})  # 全缺 -> 补默认，不抛异常
    for k in ("valence", "energy", "danceability", "acousticness", "tempo"):
        assert k in fixed


def test_generate_sense_card_mock_mode():
    html, sense = generate_sense_card(
        {"valence": 0.8, "energy": 0.7, "danceability": 0.6,
         "acousticness": 0.1, "tempo": 120},
        meta={"track_name": "Test Song", "artist": "Tester"},
        use_llm=False,  # 强制 Mock，不联网
    )
    assert isinstance(html, str) and "<html" in html.lower()
    for key in ("scene_title", "emojis", "color_palette",
                "sensory_description", "ai_art_prompt"):
        assert key in sense


def test_safe_filename_strips_illegal_chars():
    assert safe_filename("A:B/C") == "A_B_C"
    assert safe_filename('a*b?c') == "a_b_c"
