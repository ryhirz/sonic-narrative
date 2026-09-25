# -*- coding: utf-8 -*-
"""
build_library_from_dataset.py 的可测逻辑（纯函数，不读真实数据集）
--------------------------------------------------------------------------
覆盖：归一化规则、艺人配额、愿望清单命中/未命中、输出规模。
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

import build_library_from_dataset as b


# ----------------------------------------------------------------------
# 归一化
# ----------------------------------------------------------------------

def test_norm_text_strips_bracket_suffix():
    assert b.norm_text("Sunflower (feat. Swae Lee)") == "sunflower"


def test_norm_text_strips_dash_suffix():
    assert b.norm_text('Lose Yourself - From "8 Mile" Soundtrack') == "lose yourself"


def test_norm_text_keeps_chinese():
    assert b.norm_text("夜曲") == "夜曲"


def test_first_artist_takes_leading_one():
    assert b.first_artist("Kendrick Lamar;SZA") == "kendrick lamar"


# ----------------------------------------------------------------------
# 选曲
# ----------------------------------------------------------------------

def _synth(n: int = 40, artists: int = 12) -> pd.DataFrame:
    """造一批覆盖 valence×energy 网格的合成候选，便于测选曲逻辑。"""
    recs = []
    for i in range(n):
        recs.append({
            "track_name": f"Track {i}",
            "artists": f"Artist {i % artists}",
            "valence": (i % 8) / 7,
            "energy": ((i // 8) % 5) / 4,
            "danceability": 0.5,
            "acousticness": 0.3,
            "tempo": 100.0 + i,
            "popularity": 90 - i,
        })
    df = pd.DataFrame(recs)
    df["_name"] = df["track_name"].map(b.norm_text)
    df["_artist"] = df["artists"].map(b.first_artist)
    df["_key"] = df["_name"] + "|" + df["_artist"]
    return df


def test_select_library_respects_size():
    rows, _ = b.select_library(_synth(), wishlist=[], size=10, max_per_artist=2)
    assert len(rows) == 10


def test_select_library_respects_artist_cap():
    rows, _ = b.select_library(_synth(), wishlist=[], size=10, max_per_artist=2)
    counts = Counter(r["_artist"] for r in rows)
    assert max(counts.values()) <= 2, f"同艺人超过上限：{counts}"


def test_select_library_keeps_wishlist_match():
    df = _synth()
    rows, misses = b.select_library(df, wishlist=[("Track 0", "Artist 0")], size=5)
    keys = {r["_key"] for r in rows}
    assert "track 0|artist 0" in keys
    assert misses == []


def test_select_library_records_miss():
    df = _synth()
    _, misses = b.select_library(df, wishlist=[("不存在的歌", "不存在的艺人")], size=5)
    assert len(misses) == 1


def test_output_header_contract():
    assert len(b.OUTPUT_HEADER) == 9
    for col in ("track_name", "artist", "valence", "energy",
                "danceability", "acousticness", "tempo", "album", "popularity"):
        assert col in b.OUTPUT_HEADER
