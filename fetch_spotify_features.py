# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 真实音频特征抓取（Spotify Web API）
=========================================================================
 解决的问题：
   项目里 clean_spotify_data.csv 的 5 个音频特征是"近似示例值"（区间合理、
   分布覆盖，但不是 Spotify 实测）。本脚本用 Spotify 官方接口把它们换成
   真实数据 —— 推荐质量的天花板就从"示例"抬到"实测"。

 认证方式：Client Credentials（客户端凭证）
   · 只需要 Client ID + Client Secret，【不需要】用户登录授权
   · 适合"读公开曲库元数据/特征"这种无用户场景
   · 申请地址：https://developer.spotify.com/dashboard -> Create app
     （随便填个名字，Redirect URI 填 http://localhost 即可，本项目用不到）

 怎么跑：
   1) 把 Client ID / Secret 填进项目根目录的 .env：
          SPOTIFY_CLIENT_ID=xxxxxxxx
          SPOTIFY_CLIENT_SECRET=xxxxxxxx
   2) .venv\\Scripts\\python.exe fetch_spotify_features.py
   3) 默认原地覆写 clean_spotify_data.csv；建议先加 --output 备一份对比

 常用参数：
   --input  xxx.csv     源曲库（默认 clean_spotify_data.csv）
   --output xxx.csv     输出文件（默认原地覆写）
   --limit  N           只处理前 N 首（快速试跑用）
   --check              只验证凭证是否可用，不抓数据
   --keep-missing       搜到但取不到特征时保留原值（默认置 0）
   · 完全没搜到的歌：无论如何都【原样保留】，不会丢任何曲库条目

 ⚠️ 已知限制（重要，别踩坑）：
   Spotify 已于 2024-11 对【新建应用】下线 /v1/audio-features 接口。
   如果你的 app 创建于该时间之后，该接口会返回 403。此时本脚本会给出
   明确提示 —— 解决办法见 README「数据来源」一节（改用公开数据集）。
=========================================================================
"""

from __future__ import annotations

import argparse
import base64
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---- 加载项目根目录的 .env（密钥只留本机，.env 已在 .gitignore 中）----
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

import httpx  # noqa: E402  （放在 .env 加载之后）

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "clean_spotify_data.csv"

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# 输出列顺序（与 data_cleaning.py 产出的格式保持一致）
OUTPUT_HEADER = [
    "track_name", "artist", "valence", "energy",
    "danceability", "acousticness", "tempo", "album", "popularity",
]
# 需要从 Spotify 抓的 5 个特征（tempo 直接取整数 BPM）
FEATURE_KEYS = ["valence", "energy", "danceability", "acousticness", "tempo"]

TIMEOUT = 20.0


# ======================================================================
# 一、认证
# ======================================================================

def _credentials() -> Optional[Tuple[str, str]]:
    """从环境变量取 Client ID / Secret；缺任意一个都返回 None。"""
    cid = (os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
    secret = (os.getenv("SPOTIFY_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        return None
    return cid, secret


def get_access_token(client_id: str, client_secret: str) -> str:
    """Client Credentials 流程换 access_token（有效期约 1 小时）。"""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 400:
        raise RuntimeError("凭证被拒绝（400）—— 请核对 Client ID / Secret 是否填对")
    if resp.status_code == 401:
        raise RuntimeError("鉴权失败（401）—— Client ID / Secret 不正确")
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("拿到响应但没有 access_token，响应体异常")
    return token


# ======================================================================
# 二、查询：曲名 + 歌手 -> 精确 track
# ======================================================================

def search_track(
    client: httpx.Client, token: str, name: str, artist: str
) -> Optional[Dict[str, Any]]:
    """按「曲名 + 歌手」搜一首歌，返回 Spotify 的元数据；搜不到返回 None。"""
    # 用字段过滤语法，命中率比裸关键词高很多
    query = f'track:"{name}" artist:"{artist}"'
    resp = client.get(
        f"{API_BASE}/search",
        params={"q": query, "type": "track", "limit": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 401:
        raise RuntimeError("access_token 失效（401），请重新运行脚本换取新 token")
    if resp.status_code == 429:
        # 被限流：按 Retry-After 等一下再试一次
        wait = int(resp.headers.get("Retry-After", "3"))
        time.sleep(wait + 1)
        return search_track(client, token, name, artist)
    resp.raise_for_status()

    items = (resp.json().get("tracks") or {}).get("items") or []
    if not items:
        return None

    t = items[0]
    return {
        "id": t["id"],
        "album": (t.get("album") or {}).get("name", ""),
        "popularity": t.get("popularity", 0),
        "matched_name": t.get("name", ""),
        "matched_artist": ", ".join(a.get("name", "") for a in (t.get("artists") or [])),
    }


# ======================================================================
# 三、批量拉音频特征
# ======================================================================

def fetch_audio_features(
    client: httpx.Client, token: str, track_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """批量拉 audio-features（单次最多 100 个 id）。返回 {track_id: features}。"""
    result: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(track_ids), 100):
        chunk = track_ids[i:i + 100]
        resp = client.get(
            f"{API_BASE}/audio-features",
            params={"ids": ",".join(chunk)},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 403:
            raise RuntimeError(
                "audio-features 返回 403：Spotify 已于 2024-11 对【新建应用】下线该接口。\n"
                "   → 如果你用的是新注册的 app，请改用公开数据集方案（见 README「数据来源」）\n"
                "   → 如果你有 2024-11 之前创建的旧 app，用它的凭证即可正常抓取"
            )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "3"))
            time.sleep(wait + 1)
            continue  # 重试这一批
        resp.raise_for_status()

        for feat in resp.json().get("audio_features") or []:
            if feat:  # 无效 id 会返回 null
                result[feat["id"]] = feat
        time.sleep(0.2)  # 温和限速，避免触发 429
    return result


# ======================================================================
# 四、主流程
# ======================================================================

def read_source(path: Path, limit: Optional[int]) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"找不到源曲库：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[:limit] if limit else rows


def write_output(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in OUTPUT_HEADER})


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="用 Spotify Web API 抓真实音频特征，覆写曲库 CSV"
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="源曲库 CSV")
    parser.add_argument("--output", default=None, help="输出 CSV（默认原地覆写）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 首")
    parser.add_argument("--check", action="store_true", help="只验证凭证，不抓数据")
    parser.add_argument("--keep-missing", action="store_true",
                        help="搜到但取不到特征时保留原值（默认置 0）。"
                             "注意：完全没搜到的歌无论如何都会原样保留，绝不丢曲库条目")
    args = parser.parse_args(argv)

    creds = _credentials()
    if creds is None:
        print("=" * 68)
        print(" 未检测到 Spotify 凭证 —— 请在项目根目录的 .env 中填写：")
        print("     SPOTIFY_CLIENT_ID=你的ID")
        print("     SPOTIFY_CLIENT_SECRET=你的Secret")
        print(" 申请地址：https://developer.spotify.com/dashboard -> Create app")
        print("=" * 68)
        return 1

    client_id, client_secret = creds
    print(f"凭证已读取（Client ID: {client_id[:6]}...）")

    with httpx.Client() as client:
        try:
            token = get_access_token(client_id, client_secret)
        except Exception as exc:
            print(f"[失败] 换取 token 出错：{exc}")
            return 1
        print("✅ access_token 获取成功")

        if args.check:
            # 顺手验证一下 search 接口是否可用
            probe = search_track(client, token, "Blinding Lights", "The Weeknd")
            print("✅ search 接口可用" if probe else "⚠️ search 返回空，请检查配额")
            return 0

        src = Path(args.input)
        dst = Path(args.output) if args.output else src
        rows = read_source(src, args.limit)
        print(f"源曲库：{src.name}（{len(rows)} 首）")

        # --- 1) 逐首搜索，拿到 Spotify track id ---
        # 用「原始行号 -> 匹配结果」的字典记录，确保：
        #   ① 输出顺序与原曲库完全一致；② 没匹配到的歌【不会被丢掉】
        resolved: Dict[int, Dict[str, Any]] = {}
        not_found: List[str] = []
        for idx, row in enumerate(rows):
            name = (row.get("track_name") or "").strip()
            artist = (row.get("artist") or "").strip()
            try:
                info = search_track(client, token, name, artist)
            except Exception as exc:
                print(f"  [{idx + 1}/{len(rows)}] {name} —— 查询异常：{exc}")
                info = None
            if info:
                resolved[idx] = info
                print(f"  [{idx + 1}/{len(rows)}] ✔ {name} · {artist} -> {info['matched_name']}")
            else:
                not_found.append(f"{name} · {artist}")
                print(f"  [{idx + 1}/{len(rows)}] ✘ 未匹配到：{name} · {artist}")

        if not resolved:
            print("[失败] 没有任何一首匹配成功，中止（未改动任何文件）")
            return 1

        # --- 2) 批量拉真实音频特征 ---
        ids = [info["id"] for info in resolved.values()]
        try:
            features = fetch_audio_features(client, token, ids)
        except RuntimeError as exc:
            print(f"\n[失败] {exc}")
            return 1

        # --- 3) 组装并写回：按【原始顺序】遍历全部行，保证不丢歌 ---
        out_rows: List[Dict[str, Any]] = []
        ok_count = 0
        kept_count = 0
        zeroed_count = 0
        for idx, row in enumerate(rows):
            info = resolved.get(idx)
            feat = features.get(info["id"]) if info else None

            # 情况 A：没搜到这首歌 -> 原样保留（绝不丢曲库条目）
            if info is None:
                out_rows.append(row)
                kept_count += 1
                continue

            # 情况 B：搜到了但取不到特征 -> 默认保留原值，--keep-missing 语义反向控制
            if not feat:
                print(f"  ⚠️ 未取到特征：{row.get('track_name')}")
                if args.keep_missing:
                    out_rows.append(row)
                    kept_count += 1
                else:
                    out_rows.append({**row, **{k: 0 for k in FEATURE_KEYS}})
                    zeroed_count += 1
                continue

            # 情况 C：拿到真实特征 -> 覆写 5 个特征 + 补全专辑/热度
            out_rows.append({
                "track_name": row.get("track_name", ""),
                "artist": row.get("artist", ""),
                "valence": round(float(feat.get("valence", 0.0)), 3),
                "energy": round(float(feat.get("energy", 0.0)), 3),
                "danceability": round(float(feat.get("danceability", 0.0)), 3),
                "acousticness": round(float(feat.get("acousticness", 0.0)), 3),
                "tempo": round(float(feat.get("tempo", 0.0)), 1),
                "album": info.get("album") or row.get("album", ""),
                "popularity": info.get("popularity", row.get("popularity", 0)),
            })
            ok_count += 1

        write_output(dst, out_rows)

        print("\n" + "=" * 68)
        print(f"✅ 已写入 {dst}：共 {len(out_rows)} 首（输入 {len(rows)} 首，未丢歌）")
        print(f"   · Spotify 真实特征：{ok_count} 首")
        if kept_count:
            print(f"   · 保留原值（未匹配/未取到）：{kept_count} 首")
        if zeroed_count:
            print(f"   · 置 0（搜到但无特征，可加 --keep-missing 改为保留原值）：{zeroed_count} 首")
        if not_found:
            print(f"⚠️ 以下 {len(not_found)} 首未匹配到（已原样保留）：")
            for n in not_found:
                print(f"     - {n}")
        print("提示：真实值可能与你原来的示例值差异较大，建议先 --output 到新文件对比")
        print("=" * 68)
        return 0


if __name__ == "__main__":
    sys.exit(main())
