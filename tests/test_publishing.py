from __future__ import annotations

import json
import struct
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stock_video_generator.artifacts import save_simulation_artifacts
from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    OutputRecord,
    PublishTitleHistoryRecord,
    RenderRecord,
    SimulationRecord,
)
from stock_video_generator.douyin_publisher import validate_publish_media
from stock_video_generator.main import create_app
from stock_video_generator.models import DividendPolicy, ShareMode, SimulationRequest
from stock_video_generator.publishing import (
    PublishAccountCreate,
    PublishContent,
    PublishFacts,
    PublishingService,
    PublishJobCreate,
    _title_templates,
    ensure_output_copy,
    load_output_copy,
    output_copy_path,
)
from stock_video_generator.simulation import simulate_buy_and_hold
from stock_video_generator.visualization import build_visualization_spec


def _png(path: Path, width: int, height: int) -> None:
    # Enough for the publisher's dimension gate; browser upload is covered by live tests.
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"fixture"
    )


def _seed(
    settings: Settings,
    instrument,
    bars,
    valid_result,
    source,
) -> tuple[PublishingService, str]:
    request = SimulationRequest(
        symbol=instrument.symbol,
        buy_date=date(2025, 1, 2),
        initial_capital=1_000_000,
        capital_currency=instrument.currency,
        share_mode=ShareMode.FRACTIONAL,
        dividend_policy=DividendPolicy.IGNORE,
    )
    result = simulate_buy_and_hold(
        request=request,
        instrument=instrument,
        bars=bars,
        actions=[],
        validation=valid_result,
        source=source,
        simulation_id="publish-sim",
    )
    artifacts = save_simulation_artifacts(
        settings.data_dir / "simulations",
        result=result,
        request=request,
        visualization=build_visualization_spec(result),
        bars=bars,
        actions=[],
    )
    output_id = "publish-output"
    render_id = "publish-render"
    video = settings.data_dir / "outputs" / f"{render_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"real-file-placeholder")
    portrait = settings.data_dir / "outputs" / f"{render_id}.cover-portrait.png"
    landscape = settings.data_dir / "outputs" / f"{render_id}.cover-landscape.png"
    _png(portrait, 1080, 1440)
    _png(landscape, 1440, 1080)
    validation = settings.data_dir / "outputs" / f"{render_id}.mp4.validation.json"
    validation.write_text("{}", encoding="utf-8")

    database = Database(settings)
    database.initialize()
    with database.session() as session:
        session.add(
            SimulationRecord(
                simulation_id=result.simulation_id,
                job_id="publish-job",
                symbol=result.instrument.symbol,
                name=result.instrument.name,
                request_json=request.model_dump_json(),
                summary_json=result.summary.model_dump_json(),
                artifact_paths_json=json.dumps(artifacts),
            )
        )
        session.add(
            RenderRecord(
                render_id=render_id,
                job_id="publish-render-job",
                simulation_id=result.simulation_id,
                output_path=str(video),
                validation_path=str(validation),
            )
        )
        session.add(
            OutputRecord(
                output_id=output_id,
                render_id=render_id,
                simulation_id=result.simulation_id,
                video_path=str(video),
                validation_path=str(validation),
            )
        )
    service = PublishingService(settings, database)
    service.save_account(
        PublishAccountCreate(
            account_id="main",
            display_name="主账号",
            auto_publish_enabled=False,
        )
    )
    return service, output_id


def test_manifest_uses_real_simulation_facts(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    service, output_id = _seed(settings, instrument, bars, valid_result, source)

    record = service.create_job(PublishJobCreate(output_id=output_id, account_id="main"))
    manifest = service.load_manifest(record)

    assert manifest.facts.stock_name == instrument.name
    assert manifest.facts.initial_capital == 1_000_000
    assert manifest.facts.final_value == 1_600_000
    assert manifest.facts.return_pct == 60
    assert instrument.name in manifest.content.selected_title
    assert len(manifest.content.selected_title) <= 30
    episode_copy = load_output_copy(settings, "publish-render")
    assert episode_copy is not None
    assert manifest.content.description == episode_copy.subtitle
    assert "最终资产160万" not in manifest.content.description
    assert "不构成投资建议" not in manifest.content.description
    assert len(manifest.content.topics) <= 5
    assert Path(record.manifest_path).is_file()
    validate_publish_media(manifest)


def test_prepare_manifest_compacts_only_legacy_generated_description(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    service, output_id = _seed(settings, instrument, bars, valid_result, source)
    record = service.create_job(PublishJobCreate(output_id=output_id, account_id="main"))
    manifest = service.load_manifest(record)
    episode_copy = load_output_copy(settings, "publish-render")
    assert episode_copy is not None
    legacy_description = f"{episode_copy.subtitle}\n\n{episode_copy.description}"
    manifest.content = manifest.content.model_copy(
        update={"description": legacy_description}
    )
    Path(record.manifest_path).write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    record.description = legacy_description

    compacted = service.prepare_manifest_for_publish(record)

    assert compacted.content.description == episode_copy.subtitle
    assert record.description == episode_copy.subtitle
    assert service.load_manifest(record).content.description == episode_copy.subtitle

    custom = service.create_job(
        PublishJobCreate(
            output_id=output_id,
            account_id="main",
            description="这是用户手工修改的互动文案。",
        )
    )
    custom_manifest = service.prepare_manifest_for_publish(custom)
    assert custom_manifest.content.description == "这是用户手工修改的互动文案。"


def test_output_copy_is_generated_and_persisted_before_publishing(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    _, output_id = _seed(settings, instrument, bars, valid_result, source)
    database = Database(settings)
    with database.session() as session:
        simulation = session.get(SimulationRecord, "publish-sim")
        assert simulation is not None
        copy = ensure_output_copy(
            settings,
            output_id=output_id,
            render_id="publish-render",
            simulation=simulation,
            angle="compound",
        )

    assert copy.schema_version == "1.2"
    assert instrument.name in copy.title
    assert 18 <= len(copy.subtitle) <= 52
    assert copy.subtitle.endswith("？")
    assert copy.description is not None
    assert "最终资产160万" in copy.description
    assert "不构成投资建议" in copy.description
    assert copy.story_type
    assert copy.subtitle_template_id
    assert output_copy_path(settings, "publish-render").is_file()


def test_existing_output_copy_is_never_rewritten(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    _, output_id = _seed(settings, instrument, bars, valid_result, source)
    path = output_copy_path(settings, "publish-render")
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "output_id": output_id,
                "render_id": "publish-render",
                "title_candidates": ["旧标题"],
                "title": "旧标题",
                "selected_template_id": "legacy",
                "subtitle": "旧副标题保持不变",
                "topics": ["股票"],
                "generated_at": "2026-08-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    database = Database(settings)
    with database.session() as session:
        simulation = session.get(SimulationRecord, "publish-sim")
        assert simulation is not None
        copy = ensure_output_copy(
            settings,
            output_id=output_id,
            render_id="publish-render",
            simulation=simulation,
            angle="compound",
        )

    assert copy.subtitle == "旧副标题保持不变"
    assert path.read_bytes() == before


def test_episode_copy_stays_stable_across_publish_jobs(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    service, output_id = _seed(settings, instrument, bars, valid_result, source)
    first = service.create_job(PublishJobCreate(output_id=output_id, account_id="main"))
    first_manifest = service.load_manifest(first)
    database = Database(settings)
    with database.session() as session:
        session.add(
            PublishTitleHistoryRecord(
                history_id="history-1",
                account_id="main",
                publish_id=first.publish_id,
                symbol=instrument.symbol,
                template_id=first_manifest.content.selected_template_id,
                title=first.title,
                normalized_hash="force-template-rotation",
            )
        )

    second = service.create_job(PublishJobCreate(output_id=output_id, account_id="main"))
    second_manifest = service.load_manifest(second)

    assert (
        second_manifest.content.selected_template_id
        == first_manifest.content.selected_template_id
    )
    assert second_manifest.content.selected_title == first_manifest.content.selected_title
    assert second_manifest.content.description == first_manifest.content.description


def test_copy_validators_reject_promises_and_too_many_topics():
    with pytest.raises(ValueError, match="收益承诺"):
        PublishContent(
            title_candidates=[],
            selected_title="这只股票稳赚",
            selected_template_id="bad",
            description="说明",
            topics=["股票"],
        )
    with pytest.raises(ValueError, match="1到5"):
        PublishContent(
            title_candidates=[],
            selected_title="合规标题",
            selected_template_id="ok",
            description="说明",
            topics=["1", "2", "3", "4", "5", "6"],
        )


def test_long_english_stock_name_uses_symbol_title_fallback():
    facts = PublishFacts(
        stock_name="Business First Bancshares Inc",
        symbol="BFST",
        market="US",
        exchange="NASDAQ",
        currency="USD",
        buy_date=date(2016, 1, 1),
        end_date=date(2026, 1, 1),
        holding_years=10,
        initial_capital=1_000_000,
        final_value=1_500_000,
        return_pct=50,
        max_drawdown_pct=-25,
        dividend_policy="reinvest",
        execution_price="close",
        fees_included=False,
        data_source="fixture",
        angle="compound",
    )

    templates = _title_templates(facts)

    assert templates
    assert templates[0][0] == "symbol_value"
    assert "BFST" in templates[0][1]
    assert len(templates[0][1]) <= 30


def test_publish_api_creates_manifest_and_edits_copy(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="missing-node",
    )
    settings.ensure_directories()
    _, output_id = _seed(settings, instrument, bars, valid_result, source)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/publish/jobs",
        json={"output_id": output_id, "account_id": "main", "mode": "dry_run"},
    )

    assert response.status_code == 201
    publish_id = response.json()["publish_id"]
    detail = client.get(f"/api/publish/jobs/{publish_id}")
    assert detail.status_code == 200
    assert detail.json()["manifest"]["media"]["cover_landscape_path"].endswith(
        ".cover-landscape.png"
    )
    login_status = client.get("/api/publish/accounts/main/login")
    assert login_status.status_code == 200
    assert login_status.json()["status"] == "idle"

    referenced_delete = client.delete(f"/api/outputs/{output_id}")
    assert referenced_delete.status_code == 409
    assert "发布任务引用" in referenced_delete.json()["detail"]

    edited = client.patch(
        f"/api/publish/jobs/{publish_id}",
        json={
            "title": "自定义股票回测标题",
            "description": "自定义简介\n历史数据模拟，仅供信息展示，不构成投资建议。",
            "topics": ["股票", "历史回测"],
        },
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "自定义股票回测标题"
    assert edited.json()["topics"] == ["股票", "历史回测"]

    invented = client.patch(
        f"/api/publish/jobs/{publish_id}",
        json={"title": "测试股票收益999%"},
    )
    assert invented.status_code == 409
    assert "不在回测结果" in invented.json()["detail"]


def test_approval_requires_account_level_permission(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    service, output_id = _seed(settings, instrument, bars, valid_result, source)
    record = service.create_job(
        PublishJobCreate(output_id=output_id, account_id="main", mode="immediate")
    )

    with pytest.raises(ValueError, match="自动点击发布"):
        service.approve(record.publish_id)


def test_create_job_converts_legacy_landscape_cover(
    tmp_path,
    instrument,
    bars,
    valid_result,
    source,
    monkeypatch,
):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    service, output_id = _seed(settings, instrument, bars, valid_result, source)
    _png(
        settings.data_dir / "outputs" / "publish-render.cover-landscape.png",
        1920,
        1080,
    )

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(arguments, **_kwargs):
        _png(Path(arguments[-1]), 1440, 1080)
        return Completed()

    monkeypatch.setattr(
        "stock_video_generator.publishing.find_ffmpeg",
        lambda _settings: Path("ffmpeg"),
    )
    monkeypatch.setattr("stock_video_generator.publishing.subprocess.run", fake_run)

    record = service.create_job(
        PublishJobCreate(output_id=output_id, account_id="main")
    )
    manifest = service.load_manifest(record)
    converted = Path(manifest.media.cover_landscape_path)
    assert converted.parent == Path(record.manifest_path).parent
    assert converted.name == "cover-landscape.png"
    assert converted.is_file()
