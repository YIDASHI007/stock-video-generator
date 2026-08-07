"""自动生产 API 端点离线测试（无网：股票池缺失必须如实报错）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    StoryCandidateRecord,
    StoryCandidateStatus,
    TopicRecord,
    TopicStatus,
)
from stock_video_generator.main import create_app
from stock_video_generator.topics import ANGLE_CRASH, ANGLE_SURGE


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="definitely-missing-node-for-test",
    )
    return TestClient(create_app(settings))


def test_pipeline_policy_get_put_roundtrip(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/api/pipeline/policy")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    # 默认无上限（None）
    assert response.json()["daily_quota"] is None

    updated = {
        "enabled": True,
        "daily_quota": 4,
        "amount": 500000,
        "markets": ["CN", "US"],
        "angle_weights": {"surge": 40, "crash": 20, "rollercoaster": 20, "compound": 20},
        "voice": "zh-CN-YunxiNeural",
        "pool_target": 6,
    }
    put = client.put("/api/pipeline/policy", json=updated)
    assert put.status_code == 200
    assert put.json()["enabled"] is True
    assert put.json()["daily_quota"] == 4
    # 持久化到磁盘：新 client 仍能读到
    assert make_client(tmp_path).get("/api/pipeline/policy").json()["daily_quota"] == 4

    # 留空（null）= 无上限，同样合法且持久化
    unlimited = {**updated, "daily_quota": None}
    put_null = client.put("/api/pipeline/policy", json=unlimited)
    assert put_null.status_code == 200
    assert put_null.json()["daily_quota"] is None
    assert (
        make_client(tmp_path).get("/api/pipeline/policy").json()["daily_quota"]
        is None
    )


def test_pipeline_policy_change_rebuilds_queue_with_matching_stories(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="definitely-missing-node-for-test",
    )
    database = Database(settings)
    database.initialize()
    with database.session() as session:
        session.add_all(
            [
                TopicRecord(
                    topic_id="queued-surge-topic",
                    symbol="UP",
                    name="旧暴涨题",
                    market="CN",
                    buy_date="2018-01-02",
                    amount=1_000_000,
                    angle=ANGLE_SURGE,
                    drama_score=99,
                    status=TopicStatus.QUEUED,
                ),
                StoryCandidateRecord(
                    story_id="queued-surge-story",
                    story_key="UP|2018-01-02|surge",
                    symbol="UP",
                    name="旧暴涨题",
                    market="CN",
                    buy_date="2018-01-02",
                    end_date="2026-01-02",
                    story_type="listing_start",
                    angle=ANGLE_SURGE,
                    hold_years=8,
                    start_price=1,
                    end_price=10,
                    forward_return_pct=900,
                    max_drawdown_pct=20,
                    quality_score=90,
                    content_score=99,
                    status=StoryCandidateStatus.QUEUED,
                    topic_id="queued-surge-topic",
                ),
                StoryCandidateRecord(
                    story_id="ready-crash-story",
                    story_key="DOWN|2019-01-02|crash",
                    symbol="DOWN",
                    name="新暴跌题",
                    market="CN",
                    buy_date="2019-01-02",
                    end_date="2026-01-02",
                    story_type="listing_start",
                    angle=ANGLE_CRASH,
                    hold_years=7,
                    start_price=100,
                    end_price=1,
                    forward_return_pct=-99,
                    max_drawdown_pct=99.5,
                    quality_score=90,
                    content_score=98,
                    status=StoryCandidateStatus.READY,
                ),
            ]
        )

    with TestClient(create_app(settings)) as client:
        policy = client.get("/api/pipeline/policy").json()
        policy.update(
            {
                "markets": ["CN"],
                "pool_target": 1,
                "topic_directive": {
                    "surge_min_pct": None,
                    "crash_max_pct": -99,
                    "prefer_angles": [],
                    "prefer_symbols": [],
                },
            }
        )
        response = client.put("/api/pipeline/policy", json=policy)

    assert response.status_code == 200
    with database.session() as session:
        queued = session.query(TopicRecord).filter_by(status=TopicStatus.QUEUED).all()
        assert [topic.symbol for topic in queued] == ["DOWN"]
        assert queued[0].amount == policy["amount"]
        old_topic = session.get(TopicRecord, "queued-surge-topic")
        assert old_topic.status == TopicStatus.REJECTED


def test_pipeline_policy_rejects_invalid_weights(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.put(
        "/api/pipeline/policy",
        json={
            "enabled": True,
            "daily_quota": 2,
            "amount": 1000000,
            "markets": ["CN"],
            "angle_weights": {"surge": 0, "crash": 0, "rollercoaster": 0, "compound": 0},
            "voice": "zh-CN-XiaoxiaoNeural",
            "pool_target": 5,
        },
    )
    assert response.status_code == 422


def test_pipeline_policy_requires_at_least_one_market(tmp_path: Path):
    client = make_client(tmp_path)
    policy = client.get("/api/pipeline/policy").json()
    policy["markets"] = []

    response = client.put("/api/pipeline/policy", json=policy)

    assert response.status_code == 422


def test_pipeline_status_structure(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/api/pipeline/status")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "enabled",
        "daily_quota",
        "today_started",
        "today_completed",
        "pool_size",
        "story_pool",
        "active_runs",
        "parked_count",
        "policy",
    ):
        assert key in body
    assert body["enabled"] is False
    assert body["pool_size"] == 0
    assert body["story_pool"]["total"] == 0


def test_story_pool_endpoint_starts_empty(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/api/pipeline/story-pool")
    assert response.status_code == 200
    assert response.json() == {
        "summary": {
            "total": 0,
            "ready": 0,
            "by_status": {},
            "ready_by_market": {},
        },
        "candidates": [],
    }


def test_universe_status_structure(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/api/universe/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "active_total": 0,
        "eligible_total": 0,
        "excluded_total": 0,
        "by_market": {},
        "last_sync": None,
        "sync_in_progress": False,
    }


def test_pipeline_runs_empty_and_filters(tmp_path: Path):
    client = make_client(tmp_path)
    for filter_ in ("all", "active", "parked"):
        response = client.get(f"/api/pipeline/runs?filter={filter_}")
        assert response.status_code == 200
        assert response.json() == []
    assert client.get("/api/pipeline/runs?filter=bogus").status_code == 422


def test_run_once_without_universe_fails_honestly(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.post("/api/pipeline/run-once")
    assert response.status_code == 422
    assert response.json()["error"] == "UNIVERSE_UNAVAILABLE"


def test_run_retry_skip_missing_run_returns_404(tmp_path: Path):
    client = make_client(tmp_path)
    assert client.post("/api/pipeline/runs/missing/retry").status_code == 404
    assert client.post("/api/pipeline/runs/missing/skip").status_code == 404
