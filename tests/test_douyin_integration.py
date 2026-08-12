from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from stock_video_generator.config import Settings
from stock_video_generator.douyin_integration import (
    DouyinExtractRequest,
    DouyinIntegration,
    DouyinSettingsUpdate,
)
from stock_video_generator.main import create_app


def integration(tmp_path) -> DouyinIntegration:
    return DouyinIntegration(
        Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    )


def test_remote_settings_encrypt_api_key_at_rest(tmp_path) -> None:
    service = integration(tmp_path)
    public = service.save_settings(
        DouyinSettingsUpdate(
            enabled=True,
            base_url="http://127.0.0.1:8088",
            client_id="workbench-test",
            api_key="top-secret-value",
        )
    )

    raw = service.config_path.read_text(encoding="utf-8")
    assert "top-secret-value" not in raw
    assert service.load_settings().api_key == "top-secret-value"
    assert public["api_key_configured"] is True


def test_remote_settings_reject_plain_http_for_remote_host(tmp_path) -> None:
    service = integration(tmp_path)
    with pytest.raises(ValueError, match="HTTPS"):
        service.save_settings(
            DouyinSettingsUpdate(
                enabled=True,
                base_url="http://example.com",
                client_id="workbench-test",
                api_key="secret",
            )
        )


def test_imported_video_is_safely_resolved(tmp_path) -> None:
    service = integration(tmp_path)
    directory = service.import_root / "7658216586767060264"
    directory.mkdir(parents=True)
    video = directory / "sample.mp4"
    video.write_bytes(b"video")

    assert service.imported_video("7658216586767060264") == video
    with pytest.raises(KeyError):
        service.imported_video("../outside")


def test_asset_spa_routes_take_priority_over_static_assets_mount(tmp_path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    service_settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        web_dist_dir=web,
    )

    with TestClient(create_app(service_settings)) as client:
        for route in ("/assets", "/assets/materials", "/assets/douyin"):
            response = client.get(route)
            assert response.status_code == 200
            assert 'id="root"' in response.text


@pytest.mark.asyncio
async def test_create_job_persists_local_remote_mapping(monkeypatch, tmp_path) -> None:
    service = integration(tmp_path)
    service.save_settings(
        DouyinSettingsUpdate(
            enabled=True,
            base_url="http://127.0.0.1:8088",
            client_id="workbench-test",
            api_key="secret",
        )
    )

    async def fake_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(
            202,
            request=request,
            json={"id": "abc123def456", "url": "https://v.douyin.com/example"},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await service.create_job(
        DouyinExtractRequest(text="https://v.douyin.com/example")
    )

    jobs = json.loads(service.jobs_path.read_text(encoding="utf-8"))
    assert result["remote_job_id"] == "abc123def456"
    assert jobs[result["local_job_id"]]["remote_job_id"] == "abc123def456"
