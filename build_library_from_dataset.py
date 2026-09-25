# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 用【公开真实数据集】构建曲库
=========================================================================
 解决的问题：
   clean_spotify_data.csv 原来的音频特征是"近似示例值"（合理但不是实测）。
   本脚本用公开数据集 maharshipandya/spotify-tracks-dataset（11.4 万首，
   含真实 Spotify API 抓取的音频特征）重建曲库 —— 全部换成真实值，
   且【不需要任何 API Key】。

 与 fetch_spotify_features.py 的分工：
   · 本脚本      ：离线、免凭证，用公开数据集快照构建曲库（可复现）
   · fetch_*.py  ：在线、需 Spotify 凭证，实时抓你指定曲目的最新特征
   两者产出同一种 CSV 格式，可互相替换。

 构建策略（可解释，不是拍脑袋）：
   1. 原曲库里能在数据集中精确匹配到的歌 -> 保留，特征换成真实值
   2. 剩下的名额 -> 从「热度达标」的真实曲目里，按 valence×energy 的
      4x4 网格做【覆盖优化】挑选，保证每个情绪/能量象限都有歌
      （最近邻推荐最怕的就是某个象限没歌可推荐）
   3. 全程限制「同一艺人最多 2 首」-> 防止当红艺人霸榜、曲库被单一风格主导
   4. 补足到目标数量，输出 9 列标准 CSV

 怎么跑：
   # 首次会自动下载数据集到 .cache/（约 20MB，走 hf-mirror 国内镜像）
   .venv\\Scripts\\python.exe build_library_from_dataset.py
   .venv\\Scripts\\python.exe build_library_from_dataset.py --size 80 --min-popularity 55

 数据来源：https://huggingface.co/datasets/maharshipandya/spotify-tracks-dataset
  该数据集特征由 Spotify Web API 于 2023 年抓取，字段与 Spotify 原版一致。
=========================================================================
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = PROJECT_ROOT / ".cache" / "spotify_tracks_dataset.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "clean_spotify_data.csv"

DATASET_URL = (
    "https://hf-mirror.com/datasets/maharshipandya/spotify-tracks-dataset/"
    "resolve/main/dataset.csv"
)

OUTPUT_HEADER = [
    "track_name", "artist", "valence", "energy",
    "danceability", "acousticness", "tempo", "album", "popularity",
]
FEATURE_KEYS = ["valence", "energy", "danceability", "acousticness", "tempo"]

# 原曲库的"愿望清单"：这些歌能匹配到就优先保留（保证 demo 里认得出歌名）
ORIGINAL_LIBRARY: List[Tuple[str, str]] = [
    ("Blinding Lights", "The Weeknd"), ("Weightless", "Marconi Union"),
    ("Levitating", "Dua Lipa"), ("Someone Like You", "Adele"),
    ("Bad Guy", "Billie Eilish"), ("Heat Waves", "Glass Animals"),
    ("Sunflower", "Post Malone"), ("Bohemian Rhapsody", "Queen"),
    ("Imagine", "John Lennon"), ("Shape of You", "Ed Sheeran"),
    ("Rolling in the Deep", "Adele"), ("Watermelon Sugar", "Harry Styles"),
    ("Save Your Tears", "The Weeknd"), ("Stay", "The Kid LAROI"),
    ("Anti-Hero", "Taylor Swift"), ("As It Was", "Harry Styles"),
    ("Uptown Funk", "Mark Ronson"), ("Lose Yourself", "Eminem"),
    ("Clocks", "Coldplay"), ("Viva La Vida", "Coldplay"),
    ("Perfect", "Ed Sheeran"), ("Let Her Go", "Passenger"),
    ("Counting Stars", "OneRepublic"), ("Cheap Thrills", "Sia"),
    ("Believer", "Imagine Dragons"), ("Demons", "Imagine Dragons"),
    ("Shallow", "Lady Gaga"), ("Happier", "Marshmello"),
    ("everything i wanted", "Billie Eilish"), ("Good Days", "SZA"),
    ("Sail", "AWOLNATION"), ("Nuvole Bianche", "Ludovico Einaudi"),
    ("River Flows in You", "Yiruma"),
]


# ======================================================================
# 一、文本归一化（曲名/歌手上千种写法，必须归一才能对齐）
# ======================================================================

def norm_text(s: object) -> str:
    """小写、去括号后缀、去 ' - xxx' 后缀、只留字母数字与中文。"""
    s = str(s).lower().strip()
    s = re.sub(r"\s*[\(\[].*?[\)\]]", "", s)     # (feat. xxx) / [Remastered]
    s = re.sub(r"\s+-\s+.*$", "", s)             # " - From 8 Mile Soundtrack"
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def first_artist(s: object) -> str:
    """多歌手取第一位（数据集用 ';' 分隔）。"""
    return norm_text(str(s).split(";")[0])


# ======================================================================
# 二、数据集加载与准备
# ======================================================================

def ensure_dataset(path: Path) -> None:
    """数据集不存在就自动下载（走 hf-mirror 国内镜像）。"""
    if path.exists() and path.stat().st_size > 1_000_000:
        return
    print(f"数据集不存在，开始下载（约 20MB）-> {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import httpx

        with httpx.stream("GET", DATASET_URL, timeout=300, follow_redirects=True) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
    except Exception as exc:
        raise RuntimeError(
            f"数据集下载失败：{exc}\n"
            f"请手动下载后放到：{path}\n"
            f"地址：{DATASET_URL}"
        ) from exc
    print(f"✅ 下载完成：{path.stat().st_size / 1e6:.1f} MB")


def load_candidates(path: Path, min_popularity: float) -> pd.DataFrame:
    """读数据集 -> 去重 -> 过滤热度 -> 计算归一化键。"""
    df = pd.read_csv(path)
    need = {"track_name", "artists", "valence", "energy",
            "danceability", "acousticness", "tempo", "popularity"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"数据集缺少必要列：{missing}")

    df = df.dropna(subset=list(need))
    df = df[df["popularity"] >= min_popularity].copy()
    df["_name"] = df["track_name"].map(norm_text)
    df["_artist"] = df["artists"].map(first_artist)
    df["_key"] = df["_name"] + "|" + df["_artist"]
    # 同一首去重：保留热度最高的那条
    df = df.sort_values("popularity", ascending=False).drop_duplicates("_key")
    return df.reset_index(drop=True)


# ======================================================================
# 三、选曲：愿望清单优先 + valence×energy 网格覆盖
# ======================================================================

def _cell(valence: float, energy: float) -> Tuple[int, int]:
    """把 (valence, energy) 映射到 4x4 网格坐标。"""
    def bucket(x: float) -> int:
        return min(3, max(0, int(x * 4)))

    return bucket(valence), bucket(energy)


def select_library(
    df: pd.DataFrame,
    wishlist: Sequence[Tuple[str, str]],
    size: int,
    max_per_artist: int = 2,
) -> Tuple[List[Dict], List[str]]:
    """返回 (选中行列表, 未匹配到的愿望清单项)。

    max_per_artist：同一艺人最多入选几首。不设限的话，按热度补齐会让
    Bad Bunny 这类当红艺人霸榜，曲库被单一风格主导，对最近邻推荐不利。
    """
    by_key = df.set_index("_key", drop=False)
    selected: Dict[str, pd.Series] = {}
    misses: List[str] = []

    def artist_counts() -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in selected.values():
            counts[r["_artist"]] = counts.get(r["_artist"], 0) + 1
        return counts

    def quota_ok(artist: str) -> bool:
        return artist_counts().get(artist, 0) < max_per_artist

    # --- 1) 愿望清单里能精确匹配的，优先保留（不受配额限制）---
    for name, artist in wishlist:
        key = f"{norm_text(name)}|{norm_text(artist)}"
        if key in by_key.index:
            row = by_key.loc[key]
            if isinstance(row, pd.DataFrame):      # 理论上已去重，保险处理
                row = row.iloc[0]
            selected[row["_key"]] = row
        else:
            misses.append(f"{name} · {artist}")

    def covered() -> set:
        return {_cell(r["valence"], r["energy"]) for r in selected.values()}

    # --- 2) 网格覆盖：每个空象限先各补一首热度最高、且未超配额的 ---
    for val_b in range(4):
        for eng_b in range(4):
            if len(selected) >= size:
                break
            if (val_b, eng_b) in covered():
                continue
            pool = df[(df["valence"] * 4).astype(int).clip(0, 3) == val_b]
            pool = pool[(pool["energy"] * 4).astype(int).clip(0, 3) == eng_b]
            pool = pool[~pool["_key"].isin(selected.keys())]
            pool = pool[pool["_artist"].map(quota_ok)]     # 尊重艺人配额
            if not pool.empty:
                selected[pool.iloc[0]["_key"]] = pool.iloc[0]

    # --- 3) 还不够就按热度补齐（先守配额，配额用尽再放开）---
    for enforce_quota in (True, False):
        if len(selected) >= size:
            break
        for _, row in df.iterrows():
            if len(selected) >= size:
                break
            if row["_key"] in selected:
                continue
            if enforce_quota and not quota_ok(row["_artist"]):
                continue
            selected[row["_key"]] = row

    rows = list(selected.values())
    # 按热度降序输出，便于人工核对
    rows.sort(key=lambda r: float(r["popularity"]), reverse=True)
    return rows[:size], misses


# ======================================================================
# 四、写盘
# ======================================================================

def write_library(path: Path, rows: List[Dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "track_name": r["track_name"],
                "artist": str(r["artists"]).replace(";", ", "),
                "valence": round(float(r["valence"]), 3),
                "energy": round(float(r["energy"]), 3),
                "danceability": round(float(r["danceability"]), 3),
                "acousticness": round(float(r["acousticness"]), 3),
                "tempo": round(float(r["tempo"]), 1),
                "album": r.get("album_name", ""),
                "popularity": int(r["popularity"]),
            })


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="用公开 Spotify 数据集（真实特征）重建曲库"
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--size", type=int, default=60, help="目标曲库规模")
    parser.add_argument("--min-popularity", type=float, default=60,
                        help="候选曲目的最低热度（越大越主流）")
    parser.add_argument("--max-per-artist", type=int, default=2,
                        help="同一艺人最多入选几首（防止单一风格霸榜）")
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    ensure_dataset(dataset_path)

    print(f"读取数据集：{dataset_path.name}")
    df = load_candidates(dataset_path, args.min_popularity)
    print(f"候选池（热度 >= {args.min_popularity:.0f} 且去重后）：{len(df)} 首")

    rows, misses = select_library(df, ORIGINAL_LIBRARY, args.size, args.max_per_artist)

    out = Path(args.output)
    write_library(out, rows)

    # ---- 报告 ----
    kept = len(ORIGINAL_LIBRARY) - len(misses)
    print("\n" + "=" * 68)
    print(f"✅ 曲库已生成：{out}")
    print(f"   规模：{len(rows)} 首（全部为数据集中的【真实】Spotify 特征）")
    print(f"   原曲库保留：{kept}/{len(ORIGINAL_LIBRARY)} 首")
    if misses:
        print(f"   未匹配到（已用其他真实曲目替代）：")
        for m in misses:
            print(f"     - {m}")
    artists = {str(r["artists"]).split(";")[0].strip() for r in rows}
    cells = {_cell(float(r["valence"]), float(r["energy"])) for r in rows}
    print(f"   艺人多样性：{len(artists)} 位（同艺人上限 {args.max_per_artist}）")
    print(f"   情绪网格覆盖：{len(cells)}/16 个象限（valence × energy）")
    # 特征区间自检
    for col in FEATURE_KEYS:
        vals = [float(r[col]) for r in rows]
        print(f"   {col:13s} min={min(vals):.2f}  max={max(vals):.2f}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
