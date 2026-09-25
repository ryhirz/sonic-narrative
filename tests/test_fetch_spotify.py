# -*- coding: utf-8 -*-
"""
fetch_spotify_features.py 的可测部分（纯逻辑，不联网）
--------------------------------------------------------------------------
只测「不发网络请求」的分支：凭证读取、无凭证时安全退出、输出列契约。
真正的抓取需要 Spotify 凭证，放在手工运行时验证。
"""

from __future__ import annotations

import fetch_spotify_features as fsf


def test_credentials_none_when_missing(monkeypatch):
    """两个变量都不存在 -> None（不应抛异常）"""
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    assert fsf._credentials() is None


def test_credentials_none_when_blank(monkeypatch):
    """只有空白字符 -> 视为未配置"""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "   ")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "x")
    assert fsf._credentials() is None


def test_credentials_returns_tuple(monkeypatch):
    """两边都有值 -> 返回 (id, secret) 元组"""
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "sec")
    assert fsf._credentials() == ("cid", "sec")


def test_main_without_credentials_returns_1(monkeypatch):
    """没凭证时 main 应安全返回 1，而不是崩溃"""
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    assert fsf.main([]) == 1


def test_output_header_contract():
    """输出列必须与 data_cleaning / 引擎读取的 9 列保持一致"""
    assert len(fsf.OUTPUT_HEADER) == 9
    assert fsf.OUTPUT_HEADER[:2] == ["track_name", "artist"]
    for col in ("valence", "energy", "danceability", "acousticness", "tempo"):
        assert col in fsf.OUTPUT_HEADER
