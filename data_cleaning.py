# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 第一步：环境准备与数据清洗
=========================================================================
 作用：把"脏"的原始 Spotify 特征 CSV，清洗成可以直接喂给
       「通感翻译引擎」的干净数据。

 本脚本按顺序做 5 件事：
   0. 读取原始 CSV，并把 5 个特征列强制转成数字（脏字符串 -> NaN）
   1. 缺失值处理：数值列用【中位数】填充，字符列用【'Unknown'】填充
   2. 范围修正：把越界的特征值"夹"回合法区间（clip）
   3. 去重：曲名 + 歌手 相同视为同一首歌，只保留第一次出现的那行
   4. 保存为 clean_spotify_data.csv

 运行方式：
   python data_cleaning.py

 依赖：pip install pandas
=========================================================================
"""

import os
import sys

import pandas as pd

# ======================================================================
# 一、配置区（想改文件名 / 合法区间，只改这里就够了）
# ======================================================================

# 输入的原始文件名（放在本脚本同目录下）
RAW_FILE = "raw_spotify_data.csv"

# 清洗后输出的文件名
CLEAN_FILE = "clean_spotify_data.csv"

# 后续引擎必须用到的 5 个核心音频特征列
FEATURE_COLS = ["valence", "energy", "danceability", "acousticness", "tempo"]

# 每个特征的合法区间：{列名: (最小值, 最大值)}
RANGE_RULES = {
    "valence":      (0.0, 1.0),    # 情绪积极度 0~1
    "energy":       (0.0, 1.0),    # 能量强度   0~1
    "danceability": (0.0, 1.0),    # 舞蹈性     0~1
    "acousticness": (0.0, 1.0),    # 原声度     0~1
    "tempo":        (40.0, 220.0), # 速度 BPM   40~220
}

# 用于判断"同一首歌"的列（两个都相同才认为是重复）
DEDUPE_KEYS = ["track_name", "artist"]


# ======================================================================
# 二、各个处理步骤
# ======================================================================

def load_raw_data(path: str = RAW_FILE) -> pd.DataFrame:
    """读取原始 CSV；文件不存在时给出清晰提示并退出。

    同时把列名首尾空格去掉，避免 ' valence' 这种脏列名导致后续 KeyError。
    """
    if not os.path.exists(path):
        print(f"[错误] 找不到原始文件：{os.path.abspath(path)}")
        print("       请把 raw_spotify_data.csv 放到本脚本同目录下再运行。")
        sys.exit(1)

    df = pd.read_csv(path)
    # 统一列名（去空格），这是最常见也最容易忽略的脏数据来源
    df.columns = [str(c).strip() for c in df.columns]
    return df


def check_required_columns(df: pd.DataFrame) -> None:
    """检查 5 个核心特征列是否齐全，缺了就没法做后续翻译，直接退出。"""
    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        print(f"[错误] 原始数据缺少必需的特征列：{missing}")
        print(f"       当前拥有的列：{list(df.columns)}")
        sys.exit(1)


def coerce_feature_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把 5 个特征列强制转成数字类型。

    为什么先做这一步：
      CSV 里可能混入 'abc' 这样的脏字符串，pandas 会把整列识别成文本，
      导致后面「中位数填充」失效。用 to_numeric(errors='coerce')
      可以把无法解析的值统一变成 NaN，再交给下一补缺失值流程处理。
    """
    for col in FEATURE_COLS:
        before_bad = pd.to_numeric(df[col], errors="coerce").isna().sum()
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if before_bad > 0:
            print(f"  · 特征列 {col}: 发现 {before_bad} 个无法解析为数字的值 -> 已置为空值，稍后填充")
    return df


def fill_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """缺失值处理：数值列 -> 中位数；字符列 -> 'Unknown'。"""
    for col in df.columns:
        empty_count = int(df[col].isna().sum())
        if empty_count == 0:
            continue  # 这一列很干净，跳过

        if pd.api.types.is_numeric_dtype(df[col]):
            # --- 数值型：用中位数填充（比均值更抗极端值）---
            median_value = df[col].median()
            # 极端情况：整列都是空值，中位数也是 NaN，此时退化用 0 填充
            if pd.isna(median_value):
                median_value = 0.0
            df[col] = df[col].fillna(median_value)
            print(f"  · 数值列 [{col}]: 缺失 {empty_count} 个 -> 用中位数 {median_value:.4g} 填充")
        else:
            # --- 字符型：统一填 'Unknown' ---
            df[col] = df[col].fillna("Unknown")
            print(f"  · 字符列 [{col}]: 缺失 {empty_count} 个 -> 用 'Unknown' 填充")

    return df


def clamp_features(df: pd.DataFrame) -> pd.DataFrame:
    """范围修正：把越界值夹回合法区间。

    举例：valence = 1.5 会被改成 1.0；tempo = 300 会被改成 220。
    这样能保证后续引擎拿到的输入永远合法，不会因为异常值渲染出错。
    """
    for col, (low, high) in RANGE_RULES.items():
        if col not in df.columns:
            continue

        too_small = int((df[col] < low).sum())
        too_large = int((df[col] > high).sum())

        if too_small or too_large:
            print(f"  · 区间修正 [{col}]: {too_small} 个小于 {low}，{too_large} 个大于 {high} "
                  f"-> 已夹回 [{low}, {high}]")

        # clip 就是"夹逼"：小于 low 的变 low，大于 high 的变 high，中间的保持不变
        df[col] = df[col].clip(lower=low, upper=high)

    return df


def remove_duplicate_songs(df: pd.DataFrame):
    """按「曲名 + 歌手」去重，保留第一次出现的那行。

    返回：(清洗后的 DataFrame, 被删除的重复行数)
    """
    # 只有在这些列都存在时，才能按它们去重
    keys = [k for k in DEDUPE_KEYS if k in df.columns]

    if not keys:
        # 兜底：连曲名都没有，就按"整行完全一样"来判断重复
        print("  · 未找到 track_name / artist 列，退化为按【整行完全相同】去重")
        before = len(df)
        df = df.drop_duplicates(keep="first").reset_index(drop=True)
        return df, before - len(df)

    # 比较前先做统一：去首尾空格 + 转小写
    # 否则 "Blinding Lights" 和 "blinding lights " 会被误判成两首不同的歌
    compare_df = df.copy()
    for k in keys:
        compare_df[k] = compare_df[k].astype(str).str.strip().str.lower()

    before = len(df)
    # duplicated 会标记"第 2 次及以后出现的行"，keep='first' 表示保留第一次
    dup_mask = compare_df.duplicated(subset=keys, keep="first")
    removed = int(dup_mask.sum())

    df = df[~dup_mask].reset_index(drop=True)

    print(f"  · 去重（依据 {keys}）: 删除 {removed} 行重复歌曲，剩余 {len(df)} 行")
    return df, removed


# ======================================================================
# 三、主流程
# ======================================================================

def main() -> None:
    print("=" * 58)
    print("  SonicNarrative · 数据清洗")
    print("=" * 58)

    # 0. 读取
    df = load_raw_data()
    check_required_columns(df)
    print(f"\n[0] 读取完成：{len(df)} 行 × {len(df.columns)} 列")
    print(f"    列名：{list(df.columns)}")

    # 1. 特征列强制转数字
    print("\n[1] 特征列类型校正")
    df = coerce_feature_columns(df)

    # 2. 缺失值填充
    print("\n[2] 缺失值处理")
    df = fill_missing_values(df)

    # 3. 范围修正
    print("\n[3] 特征值范围修正")
    df = clamp_features(df)

    # 4. 去重
    print("\n[4] 重复歌曲处理")
    df, removed = remove_duplicate_songs(df)

    # 5. 保存
    #    encoding='utf-8-sig' 是为了让 Excel 打开中文不乱码（Windows 友好）
    df.to_csv(CLEAN_FILE, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 58)
    print(f"[完成] 已保存清洗结果 -> {os.path.abspath(CLEAN_FILE)}")
    print(f"       最终数据：{len(df)} 行 × {len(df.columns)} 列（本次删除重复 {removed} 行）")
    print("=" * 58)

    # 打印前 5 行，方便你肉眼确认结果对不对
    print("\n清洗后的数据预览（前 5 行）：")
    print(df[FEATURE_COLS + [c for c in ["track_name", "artist"] if c in df.columns]].head().to_string())


if __name__ == "__main__":
    main()
