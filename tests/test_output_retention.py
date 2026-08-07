from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    OutputRecord,
    PipelineRunRecord,
    PipelineStatus,
    PublishJobRecord,
    PublishStage,
    TopicRecord,
    TopicStatus,
)
from stock_video_generator.output_retention import OutputRetentionManager


def make_manager(tmp_path) -> tuple[Settings, Database, OutputRetentionManager]:
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        output_retention_days=7,
        output_cleanup_interval_seconds=3600,
    )
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    return settings, database, OutputRetentionManager(settings, database)


def seed_output(
    settings: Settings,
    database: Database,
    *,
    output_id: str,
    render_id: str,
    created_at: datetime,
) -> tuple[Path, list[Path]]:
    root = settings.data_dir / "outputs"
    video = root / f"{render_id}.mp4"
    validation = Path(f"{video}.validation.json")
    thumbnail = root / f"{render_id}.jpg"
    portrait = root / f"{render_id}.cover-portrait.png"
    landscape = root / f"{render_id}.cover-landscape.png"
    paths = [video, validation, thumbnail, portrait, landscape]
    for path in paths:
        path.write_bytes(b"retention-test")
    with database.session() as session:
        session.add(
            TopicRecord(
                topic_id=f"topic-{output_id}",
                symbol=f"SYMBOL-{output_id}",
                name=f"Name {output_id}",
                market="CN",
                buy_date="2022-01-03",
                amount=1_000_000,
                angle="compound",
                drama_score=1.0,
                status=TopicStatus.CONSUMED,
                consumed_at=created_at,
            )
        )
        session.add(
            OutputRecord(
                output_id=output_id,
                render_id=render_id,
                simulation_id=f"simulation-{output_id}",
                created_at=created_at,
                video_path=str(video),
                validation_path=str(validation),
            )
        )
        session.add(
            PipelineRunRecord(
                run_id=f"run-{output_id}",
                topic_id=f"topic-{output_id}",
                output_id=output_id,
                status=PipelineStatus.COMPLETED,
                current_stage=PipelineStatus.COMPLETED,
            )
        )
    return video, paths


def test_cleanup_removes_only_unreferenced_outputs_older_than_retention(tmp_path):
    settings, database, manager = make_manager(tmp_path)
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    _, old_paths = seed_output(
        settings,
        database,
        output_id="old-output",
        render_id="old-render",
        created_at=now - timedelta(days=8),
    )
    recent_video, _ = seed_output(
        settings,
        database,
        output_id="recent-output",
        render_id="recent-render",
        created_at=now - timedelta(days=6),
    )

    result = manager.cleanup_once(now=now)

    assert result["deleted_count"] == 1
    assert result["deleted_output_ids"] == ["old-output"]
    assert result["removed_files"] == 5
    assert all(not path.exists() for path in old_paths)
    assert recent_video.exists()
    with database.session() as session:
        assert session.get(OutputRecord, "old-output") is None
        assert session.get(OutputRecord, "recent-output") is not None
        old_run = session.get(PipelineRunRecord, "run-old-output")
        assert old_run.output_id is None
        assert old_run.status == PipelineStatus.COMPLETED
        old_topic = session.get(TopicRecord, "topic-old-output")
        assert old_topic.symbol == "SYMBOL-old-output"
        assert old_topic.name == "Name old-output"


def test_cleanup_preserves_outputs_referenced_by_publish_jobs(tmp_path):
    settings, database, manager = make_manager(tmp_path)
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    video, _ = seed_output(
        settings,
        database,
        output_id="published-output",
        render_id="published-render",
        created_at=now - timedelta(days=10),
    )
    with database.session() as session:
        session.add(
            PublishJobRecord(
                publish_id="publish-1",
                output_id="published-output",
                account_id="account-1",
                manifest_path="manifest.json",
                title="title",
                description="description",
            )
        )

    result = manager.cleanup_once(now=now)

    assert result["protected_count"] == 1
    assert result["deleted_count"] == 0
    assert video.exists()
    with database.session() as session:
        assert session.get(OutputRecord, "published-output") is not None


def test_cleanup_removes_old_media_after_publish_is_complete(tmp_path):
    settings, database, manager = make_manager(tmp_path)
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    video, _ = seed_output(
        settings,
        database,
        output_id="completed-publish-output",
        render_id="completed-publish-render",
        created_at=now - timedelta(days=8),
    )
    with database.session() as session:
        session.add(
            PublishJobRecord(
                publish_id="publish-completed",
                output_id="completed-publish-output",
                account_id="account-1",
                manifest_path="manifest.json",
                title="title",
                description="description",
                stage=PublishStage.PUBLISHED,
            )
        )

    result = manager.cleanup_once(now=now)

    assert result["protected_count"] == 0
    assert result["deleted_count"] == 1
    assert not video.exists()
    with database.session() as session:
        assert session.get(OutputRecord, "completed-publish-output") is None
        assert session.get(PublishJobRecord, "publish-completed") is not None
        topic = session.get(TopicRecord, "topic-completed-publish-output")
        assert topic.symbol == "SYMBOL-completed-publish-output"
