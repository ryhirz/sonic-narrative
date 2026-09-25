# -*- coding: utf-8 -*-
"""API 集成测试：用 FastAPI TestClient 在内存里调接口，不需要起 uvicorn。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_api.py -q
"""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert len(body["data"]["modules"]) == 3  # synesthesia / recommender / cover


def test_reverse_endpoint_works():
    r = client.post("/api/v1/recommender/reverse",
                    json={"text": "深夜独自开车，霓虹在车窗外流过", "top_k": 3})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["library_size"] > 0
    assert len(data["matches"]) > 0
    assert data["matches"][0]["match_score"] >= data["matches"][-1]["match_score"]


def test_reverse_validation_422():
    # text 太短（<2 字符）应被 422 拒绝
    r = client.post("/api/v1/recommender/reverse", json={"text": "", "top_k": 3})
    assert r.status_code == 422


def test_library_endpoint():
    r = client.get("/api/v1/recommender/library")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] > 0
    assert data["valid"] == data["total"]  # 当前曲库无脏数据


def test_translate_endpoint_mock():
    r = client.post("/api/v1/synesthesia/translate", json={
        "features": {"valence": 0.8, "energy": 0.7, "danceability": 0.6,
                     "acousticness": 0.1, "tempo": 120},
        "meta": {"track_name": "Test", "artist": "Tester"},
        "use_llm": False,
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert "<html" in data["html"].lower()
    assert data["mode"] in ("mock", "llm")


def test_cors_preflight_headers():
    r = client.get("/api/v1/health", headers={"Origin": "http://localhost:7860"})
    assert "access-control-allow-origin" in r.headers
