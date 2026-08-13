from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from stock_video_generator.ai_models import (
    AiModelService,
    AiModelSettingsUpdate,
    CreatorStyleAnalysis,
)
from stock_video_generator.config import Settings
from stock_video_generator.douyin_integration import (
    DouyinAccountAnalyzeRequest,
    DouyinAccountSyncRequest,
    DouyinExtractRequest,
    DouyinIntegration,
    DouyinSettingsUpdate,
)
from stock_video_generator.main import create_app


def integration(tmp_path) -> DouyinIntegration:
    return DouyinIntegration(Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs"))


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


def test_ai_model_settings_encrypt_api_key_at_rest(tmp_path) -> None:
    service = AiModelService(Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs"))
    public = service.save_settings(
        AiModelSettingsUpdate(
            enabled=True, provider="deepseek", model="deepseek-v4-flash", api_key="deep-secret"
        )
    )

    raw = service.config_path.read_text(encoding="utf-8")
    assert "deep-secret" not in raw
    assert service.load_settings().api_key == "deep-secret"
    assert public["api_key_configured"] is True
    assert "deepseek-v4-pro" in public["available_models"]


@pytest.mark.asyncio
async def test_ai_analysis_is_stored_locally_and_skill_uses_deep_method(
    monkeypatch, tmp_path
) -> None:
    ai = AiModelService(Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs"))
    service = DouyinIntegration(Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs"), ai)
    service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "示例作者",
            "works": [
                {
                    "aweme_id": str(index),
                    "title": f"样本{index}",
                    "duration": 180,
                    "transcript": "具体场景和人生推进" * 20,
                }
                for index in range(1, 6)
            ],
        }
    )
    analysis = CreatorStyleAnalysis(
        positioning="以普通人的时间与生活磨损为中心的沉浸式人生叙事栏目。",
        style_summary="从具体物件进入人物命运，让现实压力逐步改变物件含义，最后完成克制回环。",
        content_pillars=[
            {"name": "人生叙事", "description": "压缩一生", "evidence_count": 5, "ratio": 100}
        ],
        author_lens=["先观察人物如何承担", "把抽象苦难还原成动作", "不替人物喊口号"],
        hook_patterns=[
            {
                "name": "反常物件",
                "purpose": "进入命运",
                "procedure": "先写物件再揭示重量",
                "evidence_count": 4,
            },
            {
                "name": "时间切口",
                "purpose": "制造悬念",
                "procedure": "从一个时刻切入",
                "evidence_count": 3,
            },
        ],
        narrative_structures=[
            {
                "name": "人生回环",
                "use_when": "人物叙事",
                "steps": ["童年", "磨损", "理解"],
                "evidence_count": 5,
            },
            {
                "name": "身份递进",
                "use_when": "群体叙事",
                "steps": ["身份", "离乡", "承担"],
                "evidence_count": 3,
            },
        ],
        detail_selection=[
            "选择会被时间改变含义的物件",
            "每段至少一个身体动作",
            "用声音或气味固定年代",
        ],
        emotional_arc=["先平静建立日常", "让现实逐层加重", "结尾回到开头物件"],
        language_mechanics=["第二人称建立代入", "长句铺画面短句落锤", "抽象判断后立刻回到动作"],
        writing_workflows=[
            {
                "name": "人生副本",
                "use_when": "普通人物",
                "steps": ["列事实", "选物件", "排节点", "写回环"],
                "required_elements": ["物件", "磨损", "理解"],
            }
        ],
        rewrite_checks=[
            "删除没有画面支撑的评价",
            "检查人物行为是否符合材料",
            "降低连续抒情",
            "让结尾回收开头物件",
        ],
        originality_rules=["不复制原句", "更换人物和事实", "不复用独特比喻"],
        applicable_topics=["普通职业"],
        unsuitable_topics=["精确医疗建议"],
    )

    async def fake_analyze(nickname, works):
        return analysis, {
            "provider": "DeepSeek",
            "model": "deepseek-v4-flash",
            "generated_at": "2026-08-13T00:00:00+00:00",
        }

    monkeypatch.setattr(ai, "analyze_creator", fake_analyze)
    portrait = await service.analyze_account("offline", DouyinAccountAnalyzeRequest(sample_size=5))
    content, filename = await service.export_account_skill("offline")

    assert portrait["methodology"]["writing_workflows"][0]["name"] == "人生副本"
    assert portrait["methodology"]["analysis_engine"]["model"] == "deepseek-v4-flash"
    assert filename.endswith(".zip")
    assert b"writing-engine.md" in content


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


def test_account_archive_survives_without_remote_service(tmp_path) -> None:
    service = integration(tmp_path)
    archived = service._cache_account(
        {
            "sec_uid": "MS4wLjAB-offline",
            "nickname": "离线账号",
            "works": [{"aweme_id": "7658", "title": "已经归档的作品", "transcript": "本地文案"}],
        }
    )

    assert archived["archive_storage"] == "workbench-local"
    assert service._cached_account("MS4wLjAB-offline")["works"][0]["transcript"] == "本地文案"


def test_cached_account_recovers_mojibake_transcript_from_raw(tmp_path) -> None:
    service = integration(tmp_path)
    service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [
                {
                    "aweme_id": "7658",
                    "transcript": "äººåªè¦å¾åèµ°",
                    "transcript_edited": "äººåªè¦å¾åèµ°",
                    "transcript_raw": "人只要往前走",
                    "transcript_source": "editor",
                }
            ],
        }
    )

    restored = service._cached_account("offline")["works"][0]

    assert restored["transcript"] == "人只要往前走"
    assert restored["transcript_edited"] == "人只要往前走"
    assert restored["transcript_recovered_from_raw"] is True


def test_remote_refresh_does_not_overwrite_local_editor_revision(tmp_path) -> None:
    service = integration(tmp_path)
    service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [
                {
                    "aweme_id": "7658",
                    "transcript": "人工修订稿",
                    "transcript_edited": "人工修订稿",
                    "transcript_source": "editor",
                    "transcript_revision": 3,
                }
            ],
        }
    )

    refreshed = service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [
                {
                    "aweme_id": "7658",
                    "transcript": "远程旧识别稿",
                    "transcript_source": "speech_to_text",
                }
            ],
        }
    )

    assert refreshed["works"][0]["transcript"] == "人工修订稿"
    assert refreshed["works"][0]["transcript_revision"] == 3


@pytest.mark.asyncio
async def test_account_list_falls_back_to_local_archive(monkeypatch, tmp_path) -> None:
    service = integration(tmp_path)
    service._cache_account({"sec_uid": "offline", "nickname": "离线账号", "works": []})

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("docker stopped")

    monkeypatch.setattr(service, "_account_request", unavailable)
    accounts = await service.list_accounts()

    assert accounts[0]["nickname"] == "离线账号"


@pytest.mark.asyncio
async def test_transcript_can_be_edited_offline(monkeypatch, tmp_path) -> None:
    service = integration(tmp_path)
    service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [{"aweme_id": "7658", "transcript": "旧文案"}],
        }
    )

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("docker stopped")

    monkeypatch.setattr(service, "_account_request", unavailable)
    updated = await service.update_account_work_transcript("offline", "7658", "新文案")

    assert updated["transcript"] == "新文案"
    assert service._cached_account("offline")["works"][0]["transcript_revision"] == 1


@pytest.mark.asyncio
async def test_account_job_status_is_scoped_to_account(monkeypatch, tmp_path) -> None:
    service = integration(tmp_path)
    service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [{"aweme_id": "7658", "job_id": "job123"}],
        }
    )
    service.save_settings(
        DouyinSettingsUpdate(
            enabled=True,
            base_url="http://127.0.0.1:8088",
            client_id="workbench-test",
            api_key="secret",
        )
    )

    async def fake_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "job123",
                "status": "transcribing",
                "stage": "加载模型并识别口播",
                "progress": 55,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    snapshot = await service.get_account_job("offline", "job123")
    assert snapshot["progress"] == 55
    with pytest.raises(KeyError, match="不属于"):
        await service.get_account_job("offline", "other123")


@pytest.mark.asyncio
async def test_batch_archive_refresh_uses_one_account_snapshot(monkeypatch, tmp_path) -> None:
    service = integration(tmp_path)
    service.save_settings(
        DouyinSettingsUpdate(
            enabled=True,
            base_url="http://127.0.0.1:8088",
            client_id="workbench-test",
            api_key="secret",
        )
    )
    calls: list[tuple[str, str]] = []

    async def account_snapshot(method, path, **kwargs):
        calls.append((method, path))
        return {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [
                {
                    "aweme_id": "7658",
                    "job_id": "job123",
                    "processing_status": "completed",
                    "processing_progress": 100,
                    "processing_stage": "完成",
                    "transcript": "已归档文案",
                },
                {
                    "aweme_id": "7659",
                    "job_id": "job456",
                    "processing_status": "failed",
                    "processing_progress": 100,
                    "processing_stage": "失败",
                    "processing_error": "下载失败",
                },
            ],
        }

    monkeypatch.setattr(service, "_account_request", account_snapshot)

    await service.refresh_account_archive_when_jobs_finish(
        "offline", ["job123", "job456"], poll_seconds=0
    )

    assert calls == [("GET", "/offline")]
    cached = service._cached_account("offline")
    assert cached["works"][0]["processing_progress"] == 100
    assert cached["works"][1]["processing_error"] == "下载失败"


@pytest.mark.asyncio
async def test_local_account_video_supports_range_reads(tmp_path) -> None:
    service = integration(tmp_path)
    video = service.account_media_root / "offline" / "7658" / "video.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"0123456789")
    service._cache_account(
        {
            "sec_uid": "offline",
            "nickname": "离线账号",
            "works": [
                {
                    "aweme_id": "7658",
                    "local_video": video.relative_to(service.root).as_posix(),
                    "video_archived": True,
                }
            ],
        }
    )

    headers = await service.account_work_video_headers("offline", "7658", "bytes=2-5")
    chunks = [
        chunk async for chunk in service.stream_account_work_video("offline", "7658", "bytes=2-5")
    ]

    assert headers["content-range"] == "bytes 2-5/10"
    assert headers["content-length"] == "4"
    assert b"".join(chunks) == b"2345"


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
        for route in ("/assets", "/assets/materials", "/assets/douyin", "/analytics/benchmarks"):
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
    result = await service.create_job(DouyinExtractRequest(text="https://v.douyin.com/example"))

    jobs = json.loads(service.jobs_path.read_text(encoding="utf-8"))
    assert result["remote_job_id"] == "abc123def456"
    assert jobs[result["local_job_id"]]["remote_job_id"] == "abc123def456"


@pytest.mark.asyncio
async def test_account_sync_uses_authenticated_remote_proxy(monkeypatch, tmp_path) -> None:
    service = integration(tmp_path)
    service.save_settings(
        DouyinSettingsUpdate(
            enabled=True,
            base_url="http://127.0.0.1:8088",
            client_id="workbench-test",
            api_key="secret",
        )
    )
    captured = {}

    async def fake_request(self, method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        request = httpx.Request(method, url)
        return httpx.Response(
            200,
            request=request,
            json={"sec_uid": "MS4wLjAB", "nickname": "示例账号", "works": []},
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    result = await service.sync_account("MS4wLjAB", DouyinAccountSyncRequest(limit=20))

    assert result["nickname"] == "示例账号"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/accounts/MS4wLjAB/sync")
    assert captured["json"] == {"limit": 20}
    assert captured["headers"]["X-Client-ID"] == "workbench-test"
    assert captured["headers"]["Authorization"] == "Bearer secret"
