from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from stock_video_generator.config import Settings
from stock_video_generator.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        runtime_dir=tmp_path,
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="definitely-missing-node-for-test",
    )
    return TestClient(create_app(settings))


def test_system_status_reports_local_storage_facts(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"]
    assert payload["data_dir"] == str((tmp_path / "data").resolve())
    assert payload["database_path"].endswith("stock_video.db")
    assert payload["database_size_bytes"] > 0
    assert payload["disk_total_bytes"] >= payload["disk_free_bytes"] > 0


def test_system_source_update_is_read_only_for_installed_runtime(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/system/source-update")

    assert response.status_code == 200
    assert response.json()["state"] == "unsupported"


def test_system_backup_contains_database_and_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "data" / "pipeline_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text('{"enabled": false}', encoding="utf-8")

    with make_client(tmp_path) as client:
        created = client.post("/api/system/backups")
        listed = client.get("/api/system/backups")

    assert created.status_code == 201
    backup_path = Path(created.json()["path"])
    assert backup_path.is_file()
    with zipfile.ZipFile(backup_path) as archive:
        assert "database/stock_video.db" in archive.namelist()
        assert "pipeline_policy.json" in archive.namelist()
    assert listed.status_code == 200
    assert listed.json()[0]["path"] == str(backup_path.resolve())


def test_system_logs_redact_credentials(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "error.log").write_text(
        "request failed token=secret-value\nnormal message\n",
        encoding="utf-8",
    )

    with make_client(tmp_path) as client:
        response = client.get("/api/system/logs?kind=error&limit=20")

    assert response.status_code == 200
    assert response.json()["lines"] == [
        "request failed token=[REDACTED]",
        "normal message",
    ]
