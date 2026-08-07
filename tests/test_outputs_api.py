"""成片库规模化改版：列表新字段、缩略图、打包下载的 API 测试。"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    OutputRecord,
    PipelineRunRecord,
    SimulationRecord,
    TopicRecord,
)
from stock_video_generator.main import create_app


def make_client(tmp_path) -> tuple[Settings, TestClient]:
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="definitely-missing-node-for-test",
    )
    app = create_app(settings)
    return settings, TestClient(app)


def seed_output(
    settings: Settings,
    *,
    output_id: str,
    render_id: str,
    simulation_id: str,
    name: str | None = "贵州茅台",
    symbol: str = "600519",
    total_return_pct: float | None = 1415.02,
    with_topic: bool = True,
    market: str = "CN",
    angle: str = "surge",
    with_validation: bool = True,
    with_covers: bool = False,
    created_at: datetime | None = None,
) -> Path:
    """直接落库一条成片记录 + 假 mp4/验证报告，返回视频路径。"""
    video = settings.data_dir / "outputs" / f"{render_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake-mp4-bytes")
    validation = Path(f"{video}.validation.json")
    if with_validation:
        validation.write_text(
            json.dumps({"metadata": {"durationInSeconds": 15.06}}),
            encoding="utf-8",
        )
    if with_covers:
        (settings.data_dir / "outputs" / f"{render_id}.cover-landscape.png").write_bytes(
            b"\x89PNG\r\nlandscape"
        )
        (settings.data_dir / "outputs" / f"{render_id}.cover-portrait.png").write_bytes(
            b"\x89PNG\r\nportrait"
        )
    database = Database(settings)
    database.initialize()
    with database.session() as session:
        session.add(
            SimulationRecord(
                simulation_id=simulation_id,
                job_id=f"job-{simulation_id}",
                symbol=symbol,
                name=name,
                request_json="{}",
                summary_json=(
                    json.dumps({"total_return_pct": total_return_pct})
                    if total_return_pct is not None
                    else None
                ),
            )
        )
        if with_topic:
            session.add(
                TopicRecord(
                    topic_id=f"topic-{output_id}",
                    symbol=symbol,
                    name=name or symbol,
                    market=market,
                    buy_date="2025-01-02",
                    amount=100_000,
                    angle=angle,
                    drama_score=9.0,
                )
            )
            session.add(
                PipelineRunRecord(
                    run_id=f"run-{output_id}",
                    topic_id=f"topic-{output_id}",
                    output_id=output_id,
                )
            )
        session.add(
            OutputRecord(
                output_id=output_id,
                render_id=render_id,
                simulation_id=simulation_id,
                video_path=str(video),
                validation_path=str(validation),
                created_at=created_at or datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
            )
        )
    return video


def test_outputs_list_includes_gallery_fields(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(settings, output_id="o1", render_id="r1", simulation_id="s1")
    (settings.data_dir / "outputs" / "r1.copy.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "output_id": "o1",
                "render_id": "r1",
                "title_candidates": ["100万买贵州茅台，现在多少？"],
                "title": "100万买贵州茅台，现在多少？",
                "selected_template_id": "test",
                "subtitle": "这是一条随成片生成的发布简介。",
                "topics": ["贵州茅台", "股票历史回测"],
                "generated_at": "2025-06-01T12:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.get("/api/outputs")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    # 旧字段保持
    assert item["output_id"] == "o1"
    assert item["render_id"] == "r1"
    assert item["simulation_id"] == "s1"
    assert item["video_path"]
    assert item["validation_path"]
    # 新增字段
    assert item["symbol"] == "600519"
    assert item["name"] == "贵州茅台"
    assert item["total_return_pct"] == 1415.02
    assert item["duration_seconds"] == 15.06
    assert item["angle"] == "surge"
    assert item["market"] == "CN"
    assert item["publish_title"] == "100万买贵州茅台，现在多少？"
    assert item["publish_subtitle"] == "这是一条随成片生成的发布简介。"


def test_outputs_list_null_when_unlinked(tmp_path):
    """手动创建的任务关联不到选题，angle/market 为 null；验证报告缺失时时长为 null。"""
    settings, client = make_client(tmp_path)
    seed_output(
        settings,
        output_id="o2",
        render_id="r2",
        simulation_id="s2",
        with_topic=False,
        with_validation=False,
    )

    items = client.get("/api/outputs").json()

    assert len(items) == 1
    assert items[0]["angle"] is None
    assert items[0]["market"] is None
    assert items[0]["duration_seconds"] is None
    assert items[0]["name"] == "贵州茅台"
    assert items[0]["publish_title"] is None
    assert items[0]["publish_subtitle"] is None
    assert not (settings.data_dir / "outputs" / "r2.copy.json").exists()


def test_pack_outputs_returns_zip(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(
        settings,
        output_id="o1",
        render_id="r1",
        simulation_id="s1",
        with_covers=True,
    )
    seed_output(
        settings,
        output_id="o2",
        render_id="r2",
        simulation_id="s2",
        name="腾讯控股",
        symbol="0700",
        market="HK",
        angle="crash",
        created_at=datetime(2025, 6, 2, 8, 30, tzinfo=UTC),
    )

    response = client.get("/api/outputs/pack")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "videos-pack.zip" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert len(names) == 4
    assert sum(name.endswith(".mp4") for name in names) == 2
    assert any(name.endswith("_横版封面.png") for name in names)
    assert any(name.endswith("_竖版封面.png") for name in names)
    assert any(name.startswith("贵州茅台_") for name in names)
    assert any(name.startswith("腾讯控股_") for name in names)


def test_pack_outputs_filters_by_market_and_date(tmp_path):
    settings, client = make_client(tmp_path)
    created = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    seed_output(
        settings,
        output_id="o1",
        render_id="r1",
        simulation_id="s1",
        created_at=created,
    )
    seed_output(
        settings,
        output_id="o2",
        render_id="r2",
        simulation_id="s2",
        market="HK",
        angle="crash",
        created_at=datetime(2025, 6, 3, 12, 0, tzinfo=UTC),
    )

    by_market = client.get("/api/outputs/pack", params={"market": "CN"})
    assert by_market.status_code == 200
    assert len(zipfile.ZipFile(io.BytesIO(by_market.content)).namelist()) == 1

    # date 按本地日期过滤
    local_date = created.astimezone().date().isoformat()
    by_date = client.get("/api/outputs/pack", params={"date": local_date})
    assert by_date.status_code == 200
    assert len(zipfile.ZipFile(io.BytesIO(by_date.content)).namelist()) == 1

    by_angle = client.get("/api/outputs/pack", params={"angle": "crash"})
    assert by_angle.status_code == 200
    assert len(zipfile.ZipFile(io.BytesIO(by_angle.content)).namelist()) == 1


def test_pack_outputs_filters_by_pnl_and_search(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(
        settings,
        output_id="winner",
        render_id="winner-render",
        simulation_id="winner-sim",
        name="腾讯控股",
        symbol="0700.HK",
        total_return_pct=80.0,
    )
    seed_output(
        settings,
        output_id="loser",
        render_id="loser-render",
        simulation_id="loser-sim",
        name="测试亏损股",
        symbol="LOSS",
        total_return_pct=-35.0,
    )

    winners = client.get("/api/outputs/pack", params={"pnl": "win"})
    assert winners.status_code == 200
    winner_names = zipfile.ZipFile(io.BytesIO(winners.content)).namelist()
    assert len(winner_names) == 1
    assert winner_names[0].startswith("腾讯控股_")

    searched = client.get("/api/outputs/pack", params={"q": "loss"})
    assert searched.status_code == 200
    searched_names = zipfile.ZipFile(io.BytesIO(searched.content)).namelist()
    assert len(searched_names) == 1
    assert searched_names[0].startswith("测试亏损股_")


def test_pack_outputs_no_match_returns_404(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(settings, output_id="o1", render_id="r1", simulation_id="s1")

    response = client.get("/api/outputs/pack", params={"market": "US"})

    assert response.status_code == 404
    assert "没有符合筛选条件" in response.json()["detail"]


def test_thumbnail_existing_file_returns_jpeg(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(settings, output_id="o1", render_id="r1", simulation_id="s1")
    thumbnail = settings.data_dir / "outputs" / "r1.jpg"
    thumbnail.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    response = client.get("/api/outputs/o1/thumbnail")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8\xff\xe0fake-jpeg"


def test_thumbnail_missing_returns_404(tmp_path):
    """测试环境没有 Remotion ffmpeg，懒生成会失败，应返回 404。"""
    settings, client = make_client(tmp_path)
    seed_output(settings, output_id="o1", render_id="r1", simulation_id="s1")

    response = client.get("/api/outputs/o1/thumbnail")

    assert response.status_code == 404
    assert "缩略图" in response.json()["detail"]


def test_thumbnail_prefers_generated_landscape_cover(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(
        settings,
        output_id="o1",
        render_id="r1",
        simulation_id="s1",
        with_covers=True,
    )

    response = client.get("/api/outputs/o1/thumbnail")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\nlandscape"


def test_cover_endpoints_return_both_generated_variants(tmp_path):
    settings, client = make_client(tmp_path)
    seed_output(
        settings,
        output_id="o1",
        render_id="r1",
        simulation_id="s1",
        with_covers=True,
    )

    landscape = client.get("/api/outputs/o1/cover/landscape")
    portrait = client.get("/api/outputs/o1/cover/portrait")

    assert landscape.status_code == 200
    assert landscape.headers["content-type"] == "image/png"
    assert landscape.content == b"\x89PNG\r\nlandscape"
    assert portrait.status_code == 200
    assert portrait.content == b"\x89PNG\r\nportrait"


def test_thumbnail_unknown_output_returns_404(tmp_path):
    _, client = make_client(tmp_path)

    response = client.get("/api/outputs/no-such-output/thumbnail")

    assert response.status_code == 404


def test_delete_output_removes_record_and_files(tmp_path):
    """删除成片：数据库记录、视频、校验报告、缩略图一并真实删除。"""
    settings, client = make_client(tmp_path)
    video = seed_output(settings, output_id="o1", render_id="r1", simulation_id="s1")
    validation = Path(f"{video}.validation.json")
    thumbnail = settings.data_dir / "outputs" / "r1.jpg"
    thumbnail.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    landscape = settings.data_dir / "outputs" / "r1.cover-landscape.png"
    portrait = settings.data_dir / "outputs" / "r1.cover-portrait.png"
    copy = settings.data_dir / "outputs" / "r1.copy.json"
    landscape.write_bytes(b"\x89PNG\r\nlandscape")
    portrait.write_bytes(b"\x89PNG\r\nportrait")
    copy.write_text("{}", encoding="utf-8")

    response = client.delete("/api/outputs/o1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted"] is True
    assert not video.exists()
    assert not validation.exists()
    assert not thumbnail.exists()
    assert not landscape.exists()
    assert not portrait.exists()
    assert not copy.exists()
    # 列表与详情都不再可见
    assert client.get("/api/outputs").json() == []
    assert client.get("/api/outputs/o1").status_code == 404
    # 引用它的流水线记录 output_id 已置空，而不是悬空
    database = Database(settings)
    with database.session() as session:
        run = session.get(PipelineRunRecord, "run-o1")
        assert run is not None
        assert run.output_id is None


def test_delete_output_unknown_returns_404(tmp_path):
    _, client = make_client(tmp_path)

    response = client.delete("/api/outputs/no-such-output")

    assert response.status_code == 404
