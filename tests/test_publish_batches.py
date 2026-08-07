from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    OutputRecord,
    PublishAccountRecord,
    PublishBatchItemRecord,
    PublishBatchItemStatus,
    PublishBatchRecord,
    PublishBatchStatus,
    PublishJobRecord,
    PublishStage,
)
from stock_video_generator.publish_batches import (
    PublishBatchCreate,
    PublishBatchManager,
    PublishBatchService,
)
from stock_video_generator.publish_manager import PublishManager
from stock_video_generator.publishing import PublishingService


def _job(publish_id: str, output_id: str, stage: str) -> PublishJobRecord:
    return PublishJobRecord(
        publish_id=publish_id,
        output_id=output_id,
        account_id="main",
        stage=stage,
        mode="immediate",
        manifest_path=f"/tmp/{publish_id}.json",
        title=publish_id,
        description="test",
        topics_json="[]",
    )


def _seed_active_batch(
    database: Database,
    *,
    job_stage: str,
    pause_requested: bool = False,
) -> None:
    now = datetime.now(UTC)
    with database.session() as session:
        session.add_all(
            [
                PublishBatchRecord(
                    batch_id="batch",
                    name="batch",
                    account_id="main",
                    status=(
                        PublishBatchStatus.PAUSE_REQUESTED
                        if pause_requested
                        else PublishBatchStatus.RUNNING
                    ),
                    interval_seconds=300,
                    approved_at=now,
                    pause_requested=pause_requested,
                ),
                _job("publish-1", "output-1", job_stage),
                _job("publish-2", "output-2", PublishStage.CREATED),
                PublishBatchItemRecord(
                    item_id="item-1",
                    batch_id="batch",
                    output_id="output-1",
                    publish_id="publish-1",
                    position=1,
                    status=PublishBatchItemStatus.PUBLISHING,
                    started_at=now,
                ),
                PublishBatchItemRecord(
                    item_id="item-2",
                    batch_id="batch",
                    output_id="output-2",
                    publish_id="publish-2",
                    position=2,
                    status=PublishBatchItemStatus.PENDING,
                ),
            ]
        )


def _manager(database: Database) -> PublishBatchManager:
    return PublishBatchManager(
        database,
        cast(PublishingService, cast(Any, object())),
        cast(PublishManager, cast(Any, object())),
    )


def test_batch_interval_starts_after_confirmed_publish(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    database = Database(settings)
    database.initialize()
    _seed_active_batch(database, job_stage=PublishStage.PUBLISHED)

    before = datetime.now(UTC)
    _manager(database).tick()

    with database.session() as session:
        batch = session.get(PublishBatchRecord, "batch")
        first = session.get(PublishBatchItemRecord, "item-1")
        second = session.get(PublishBatchItemRecord, "item-2")
        assert batch is not None
        assert first is not None
        assert second is not None
        assert first.status == PublishBatchItemStatus.PUBLISHED
        assert first.published_at is not None
        assert second.status == PublishBatchItemStatus.PENDING
        assert batch.status == PublishBatchStatus.WAITING_INTERVAL
        assert batch.next_run_at is not None
        next_run_at = batch.next_run_at
        if next_run_at.tzinfo is None:
            next_run_at = next_run_at.replace(tzinfo=UTC)
        assert before + timedelta(seconds=295) <= next_run_at
        assert next_run_at <= datetime.now(UTC) + timedelta(seconds=305)


def test_pause_request_finishes_active_item_then_pauses(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    database = Database(settings)
    database.initialize()
    _seed_active_batch(
        database,
        job_stage=PublishStage.PUBLISHED,
        pause_requested=True,
    )

    _manager(database).tick()

    with database.session() as session:
        batch = session.get(PublishBatchRecord, "batch")
        first = session.get(PublishBatchItemRecord, "item-1")
        second = session.get(PublishBatchItemRecord, "item-2")
        assert batch is not None
        assert first is not None
        assert second is not None
        assert first.status == PublishBatchItemStatus.PUBLISHED
        assert second.status == PublishBatchItemStatus.PENDING
        assert batch.status == PublishBatchStatus.PAUSED
        assert batch.next_run_at is None


def test_login_or_sms_challenge_pauses_entire_batch(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    database = Database(settings)
    database.initialize()
    _seed_active_batch(database, job_stage=PublishStage.NEEDS_SMS)
    with database.session() as session:
        job = session.get(PublishJobRecord, "publish-1")
        assert job is not None
        job.error_reason = "需要短信验证"

    _manager(database).tick()

    with database.session() as session:
        batch = session.get(PublishBatchRecord, "batch")
        first = session.get(PublishBatchItemRecord, "item-1")
        second = session.get(PublishBatchItemRecord, "item-2")
        assert batch is not None
        assert first is not None
        assert second is not None
        assert first.status == PublishBatchItemStatus.NEEDS_HUMAN
        assert second.status == PublishBatchItemStatus.PENDING
        assert batch.status == PublishBatchStatus.NEEDS_HUMAN
        assert batch.error_reason == "需要短信验证"


def test_batch_creation_rolls_back_drafts_when_one_item_fails(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    with database.session() as session:
        session.add(
            PublishAccountRecord(
                account_id="main",
                display_name="主账号",
                browser_profile_dir=str(tmp_path / "profile"),
                enabled=True,
            )
        )
        for index in (1, 2):
            session.add(
                OutputRecord(
                    output_id=f"output-{index}",
                    render_id=f"render-{index}",
                    simulation_id=f"simulation-{index}",
                    video_path=str(tmp_path / f"{index}.mp4"),
                    validation_path=str(tmp_path / f"{index}.json"),
                )
            )

    class FailingPublishing:
        def __init__(self) -> None:
            self.settings = settings
            self.calls = 0

        def create_job(self, request):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("fixture failure")
            publish_id = "draft-1"
            manifest = (
                settings.data_dir
                / "publishes"
                / publish_id
                / "publish_manifest.json"
            )
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("{}", encoding="utf-8")
            record = _job(publish_id, request.output_id, PublishStage.CREATED)
            record.manifest_path = str(manifest)
            with database.session() as session:
                session.add(record)
            return record

    failing = FailingPublishing()
    service = PublishBatchService(
        database,
        cast(PublishingService, cast(Any, failing)),
        cast(PublishManager, cast(Any, object())),
    )

    with pytest.raises(ValueError, match="第2条视频.*fixture failure"):
        service.create(
            PublishBatchCreate(
                output_ids=["output-1", "output-2"],
                account_id="main",
            )
        )

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(PublishJobRecord)) == 0
        assert session.scalar(select(func.count()).select_from(PublishBatchRecord)) == 0
    assert not Path(
        settings.data_dir / "publishes" / "draft-1"
    ).exists()
