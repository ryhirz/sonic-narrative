# -*- coding: utf-8 -*-
"""反向寻歌引擎 · 单元测试（纯函数，不需要起服务、不需要 API Key）。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_reverse_engine.py -q
"""
import math

from reverse_engine import (
    ALL_KEYS, MAX_DISTANCE, TEMPO_MAX, TEMPO_MIN,
    describe_to_features, distance_to_score, feature_distance, find_similar_tracks,
    load_library, reverse_search, _norm_tempo,
)


def test_norm_tempo_boundaries():
    # tempo 归一化：40->0, 220->1, 中点 (40+220)/2=130 -> 0.5
    assert _norm_tempo(TEMPO_MIN) == 0.0
    assert _norm_tempo(TEMPO_MAX) == 1.0
    assert abs(_norm_tempo(130.0) - 0.5) < 1e-9


def test_feature_distance_identical_is_zero():
    a = {"valence": 0.5, "energy": 0.5, "danceability": 0.5,
         "acousticness": 0.5, "tempo": 100.0}
    assert feature_distance(a, a) == 0.0


def test_feature_distance_known_value():
    # 仅 tempo 不同（其余相同）：距离 = sqrt(权重 * 归一化差^2)
    a = {"valence": 0.5, "energy": 0.5, "danceability": 0.5,
         "acousticness": 0.5, "tempo": 40.0}
    b = {"valence": 0.5, "energy": 0.5, "danceability": 0.5,
         "acousticness": 0.5, "tempo": 220.0}
    expected = math.sqrt(0.8 * (1.0 ** 2))  # WEIGHTS["tempo"] = 0.8
    assert abs(feature_distance(a, b) - expected) < 1e-9


def test_distance_to_score_poles():
    assert distance_to_score(0.0) == 100.0        # 距离为 0 -> 最像
    assert distance_to_score(MAX_DISTANCE) == 0.0  # 最大距离 -> 0 分


def test_load_library_shape():
    lib = load_library()
    assert isinstance(lib, list) and len(lib) > 0
    for item in lib:
        assert item["track_name"] and item["artist"]
        for k in ALL_KEYS:
            if k == "tempo":
                assert 40.0 <= item[k] <= 220.0
            else:
                assert 0.0 <= item[k] <= 1.0


def test_find_similar_tracks_sorted_and_topk():
    feats = {"valence": 0.2, "energy": 0.1, "danceability": 0.2,
             "acousticness": 0.9, "tempo": 60.0}
    top = find_similar_tracks(feats, top_k=3)
    assert len(top) <= 3
    scores = [m["match_score"] for m in top]
    assert scores == sorted(scores, reverse=True)   # 匹配度降序
    dists = [m["distance"] for m in top]
    assert dists == sorted(dists)                    # 距离升序


def test_describe_to_features_keyword_mode():
    # 强制关键词模式，验证不依赖 API Key
    out = describe_to_features("深夜独自开车，霓虹在车窗外流过", use_llm=False)
    assert out["mode"] == "keyword"
    for k in ALL_KEYS:
        v = out["features"][k]
        if k == "tempo":
            assert 40.0 <= v <= 220.0
        else:
            assert 0.0 <= v <= 1.0


def test_describe_to_features_empty_input_no_crash():
    out = describe_to_features("", use_llm=False)
    assert "features" in out and out["mode"] == "keyword"


def test_reverse_search_contract():
    res = reverse_search("想给喜欢的人表白，要温柔一点", top_k=3, use_llm=False)
    assert res["query"]
    assert res["library_size"] > 0
    assert "library_note" in res
    assert len(res["matches"]) <= 3
    assert res["matches"][0]["match_score"] >= res["matches"][-1]["match_score"]
