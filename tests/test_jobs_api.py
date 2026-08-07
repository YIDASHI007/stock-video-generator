from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from stock_video_generator.config import Settings
from stock_video_generator.database import Database, JobRecord, JobStage
from stock_video_generator.jobs import JobManager
from stock_video_generator.main import create_app
from stock_video_generator.market_data import MarketDataService
from stock_video_generator.models import DividendPolicy, SimulationRequest


def temporary_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="definitely-missing-node-for-test",
    )


def request_payload() -> SimulationRequest:
    return SimulationRequest(
        symbol="AAPL",
        buy_date=date(2025, 1, 2),
        end_date=date(2025, 1, 10),
        initial_capital=100_000,
        capital_currency="USD",
        dividend_policy=DividendPolicy.CASH,
    )


def test_job_persists_across_database_reopen(tmp_path):
    settings = temporary_settings(tmp_path)
    first_database = Database(settings)
    first_database.initialize()
    manager = JobManager(
        settings,
        first_database,
        MarketDataService(settings),
    )
    created = manager.create_simulation(request_payload())

    second_database = Database(settings)
    second_database.initialize()
    with second_database.session() as session:
        stored = session.get(JobRecord, created.job_id)
        assert stored is not None
        assert stored.simulation_id == created.simulation_id
        assert stored.stage == JobStage.CREATED


def test_interrupted_job_is_recovered_to_created(tmp_path):
    settings = temporary_settings(tmp_path)
    database = Database(settings)
    database.initialize()
    manager = JobManager(settings, database, MarketDataService(settings))
    created = manager.create_simulation(request_payload())
    with database.session() as session:
        stored = session.get(JobRecord, created.job_id)
        assert stored is not None
        stored.stage = JobStage.FETCHING_MARKET_DATA
        stored.progress = 0.25

    manager.recover_interrupted_jobs()

    with database.session() as session:
        recovered = session.get(JobRecord, created.job_id)
        assert recovered is not None
        assert recovered.stage == JobStage.CREATED
        assert recovered.progress == 0.25
        assert recovered.error_type == "PROCESS_RESTARTED"
        assert "重新排队" in recovered.error_reason


def test_health_and_simulation_submission_api(tmp_path):
    settings = temporary_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert any(
        item["name"] == "database" and item["available"] for item in health.json()["components"]
    )

    response = client.post(
        "/api/simulations",
        json=request_payload().model_dump(mode="json"),
    )
    assert response.status_code == 202
    body = response.json()
    assert body["stage"] == "CREATED"
    assert body["simulation_id"]

    detail = client.get(f"/api/jobs/{body['job_id']}")
    assert detail.status_code == 200
    assert detail.json()["input"]["symbol"] == "AAPL"
