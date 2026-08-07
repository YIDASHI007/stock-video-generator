from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from stock_video_generator.database import (
    Database,
    OutputRecord,
    PublishAccountRecord,
    PublishAttemptRecord,
    PublishBatchItemRecord,
    PublishBatchItemStatus,
    PublishBatchRecord,
    PublishBatchStatus,
    PublishJobRecord,
    PublishStage,
    PublishTitleHistoryRecord,
)
from stock_video_generator.publish_manager import ACTIVE_STAGES, PublishManager
from stock_video_generator.publishing import (
    PublishingService,
    PublishJobCreate,
    publish_job_payload,
)


class PublishBatchCreate(BaseModel):
    output_ids: list[str] = Field(min_length=1, max_length=50)
    account_id: str
    name: str | None = Field(default=None, max_length=160)
    interval_minutes: int = Field(default=10, ge=5, le=1440)
    start_at: datetime | None = None
    failure_policy: Literal["pause", "skip"] = "pause"

    @field_validator("output_ids")
    @classmethod
    def unique_outputs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("同一批次不能重复选择同一个视频")
        return value

    @field_validator("start_at")
    @classmethod
    def start_time_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("批次开始时间必须包含时区")
        return value


class PublishBatchUpdate(BaseModel):
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    failure_policy: Literal["pause", "skip"] | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class PublishBatchService:
    def __init__(
        self,
        database: Database,
        publishing: PublishingService,
        publish_manager: PublishManager,
    ) -> None:
        self.database = database
        self.publishing = publishing
        self.publish_manager = publish_manager

    def _rollback_created_jobs(self, jobs: list[PublishJobRecord]) -> None:
        if not jobs:
            return
        publish_ids = [job.publish_id for job in jobs]
        with self.database.session() as session:
            session.execute(
                delete(PublishAttemptRecord).where(
                    PublishAttemptRecord.publish_id.in_(publish_ids)
                )
            )
            session.execute(
                delete(PublishTitleHistoryRecord).where(
                    PublishTitleHistoryRecord.publish_id.in_(publish_ids)
                )
            )
            session.execute(
                delete(PublishJobRecord).where(
                    PublishJobRecord.publish_id.in_(publish_ids)
                )
            )

        publish_root = (self.publishing.settings.data_dir / "publishes").resolve()
        for job in jobs:
            manifest_dir = Path(job.manifest_path).resolve().parent
            if (
                manifest_dir.parent == publish_root
                and manifest_dir.name == job.publish_id
            ):
                shutil.rmtree(manifest_dir, ignore_errors=True)

    def create(self, request: PublishBatchCreate) -> PublishBatchRecord:
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, request.account_id)
            if account is None or not account.enabled:
                raise KeyError("account")
            outputs = {
                row.output_id: row
                for row in session.scalars(
                    select(OutputRecord).where(OutputRecord.output_id.in_(request.output_ids))
                ).all()
            }
            missing = [output_id for output_id in request.output_ids if output_id not in outputs]
            if missing:
                raise ValueError(f"未找到{len(missing)}条视频成片")
            published = set(
                session.scalars(
                    select(PublishJobRecord.output_id).where(
                        PublishJobRecord.output_id.in_(request.output_ids),
                        PublishJobRecord.stage == PublishStage.PUBLISHED,
                    )
                ).all()
            )
            if published:
                raise ValueError(
                    "以下视频已经发布，批量发布默认禁止重复投稿："
                    + "、".join(output_id[:8] for output_id in published)
                )
            active_batch_outputs = set(
                session.scalars(
                    select(PublishBatchItemRecord.output_id)
                    .join(
                        PublishBatchRecord,
                        PublishBatchRecord.batch_id == PublishBatchItemRecord.batch_id,
                    )
                    .where(
                        PublishBatchItemRecord.output_id.in_(request.output_ids),
                        PublishBatchRecord.status.not_in(
                            {
                                PublishBatchStatus.COMPLETED,
                                PublishBatchStatus.PARTIAL_FAILED,
                                PublishBatchStatus.CANCELLED,
                            }
                        ),
                    )
                ).all()
            )
            if active_batch_outputs:
                raise ValueError("选中的视频已存在于其他未结束批次中")

        batch_id = str(uuid4())
        created_jobs: list[PublishJobRecord] = []
        _current_position = 0
        current_output_id = ""
        try:
            for _current_position, output_id in enumerate(request.output_ids, start=1):
                current_output_id = output_id
                job = self.publishing.create_job(
                    PublishJobCreate(
                        output_id=output_id,
                        account_id=request.account_id,
                        mode="immediate",
                    )
                )
                created_jobs.append(job)

            now = datetime.now(UTC)
            name = (
                request.name
                or f"批量发布 · {now.astimezone().strftime('%m月%d日 %H:%M')}"
            )
            with self.database.session() as session:
                batch = PublishBatchRecord(
                    batch_id=batch_id,
                    name=name,
                    account_id=request.account_id,
                    status=PublishBatchStatus.READY,
                    interval_seconds=request.interval_minutes * 60,
                    failure_policy=request.failure_policy,
                    start_at=request.start_at,
                )
                session.add(batch)
                for position, (output_id, job) in enumerate(
                    zip(request.output_ids, created_jobs, strict=True),
                    start=1,
                ):
                    session.add(
                        PublishBatchItemRecord(
                            item_id=str(uuid4()),
                            batch_id=batch_id,
                            output_id=output_id,
                            publish_id=job.publish_id,
                            position=position,
                        )
                    )
                session.flush()
                return batch
        except Exception as exc:
            self._rollback_created_jobs(created_jobs)
            item_label = (
                f"第{_current_position}条视频（{current_output_id[:8]}）"
                if _current_position
                else "批次"
            )
            raise ValueError(f"{item_label}发布清单创建失败：{exc}") from exc

    def list(self, limit: int = 100) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(PublishBatchRecord)
                .order_by(PublishBatchRecord.created_at.desc())
                .limit(limit)
            ).all()
            ids = [row.batch_id for row in rows]
        return [self.payload(batch_id) for batch_id in ids]

    def payload(self, batch_id: str) -> dict[str, object]:
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                raise KeyError("batch")
            items = session.scalars(
                select(PublishBatchItemRecord)
                .where(PublishBatchItemRecord.batch_id == batch_id)
                .order_by(PublishBatchItemRecord.position)
            ).all()
            jobs = {
                row.publish_id: row
                for row in session.scalars(
                    select(PublishJobRecord).where(
                        PublishJobRecord.publish_id.in_(
                            [item.publish_id for item in items]
                        )
                    )
                ).all()
            }
            item_payloads = []
            for item in items:
                job = jobs.get(item.publish_id)
                item_payloads.append(
                    {
                        "item_id": item.item_id,
                        "output_id": item.output_id,
                        "publish_id": item.publish_id,
                        "position": item.position,
                        "status": item.status,
                        "started_at": item.started_at,
                        "published_at": item.published_at,
                        "error_reason": item.error_reason,
                        "job": publish_job_payload(job) if job else None,
                    }
                )
            counts = {
                status: sum(item.status == status for item in items)
                for status in (
                    PublishBatchItemStatus.PENDING,
                    PublishBatchItemStatus.PUBLISHING,
                    PublishBatchItemStatus.PUBLISHED,
                    PublishBatchItemStatus.NEEDS_HUMAN,
                    PublishBatchItemStatus.FAILED,
                    PublishBatchItemStatus.SKIPPED,
                    PublishBatchItemStatus.CANCELLED,
                )
            }
            return {
                "batch_id": batch.batch_id,
                "name": batch.name,
                "account_id": batch.account_id,
                "status": batch.status,
                "interval_minutes": batch.interval_seconds // 60,
                "failure_policy": batch.failure_policy,
                "start_at": batch.start_at,
                "next_run_at": batch.next_run_at,
                "approved_at": batch.approved_at,
                "pause_requested": batch.pause_requested,
                "error_reason": batch.error_reason,
                "created_at": batch.created_at,
                "updated_at": batch.updated_at,
                "total_count": len(items),
                "counts": counts,
                "items": item_payloads,
            }

    def approve_start(self, batch_id: str) -> dict[str, object]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                raise KeyError("batch")
            account = session.get(PublishAccountRecord, batch.account_id)
            if account is None or not account.auto_publish_enabled:
                raise ValueError("该账号尚未开启本系统“自动点击发布”开关")
            if batch.status in {
                PublishBatchStatus.COMPLETED,
                PublishBatchStatus.PARTIAL_FAILED,
                PublishBatchStatus.CANCELLED,
            }:
                raise ValueError("已结束批次不能重新启动")
            batch.approved_at = now
            batch.pause_requested = False
            start_at = _aware(batch.start_at)
            if start_at and start_at > now:
                batch.status = PublishBatchStatus.WAITING_INTERVAL
                batch.next_run_at = start_at
            else:
                batch.status = PublishBatchStatus.RUNNING
                batch.next_run_at = now
            batch.error_reason = None
        return self.payload(batch_id)

    def pause(self, batch_id: str) -> dict[str, object]:
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                raise KeyError("batch")
            active = session.scalar(
                select(PublishBatchItemRecord).where(
                    PublishBatchItemRecord.batch_id == batch_id,
                    PublishBatchItemRecord.status == PublishBatchItemStatus.PUBLISHING,
                )
            )
            batch.pause_requested = True
            batch.status = (
                PublishBatchStatus.PAUSE_REQUESTED
                if active is not None
                else PublishBatchStatus.PAUSED
            )
        return self.payload(batch_id)

    def resume(self, batch_id: str) -> dict[str, object]:
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                raise KeyError("batch")
            if batch.approved_at is None:
                raise ValueError("批次尚未授权")
            if batch.status in {
                PublishBatchStatus.COMPLETED,
                PublishBatchStatus.PARTIAL_FAILED,
                PublishBatchStatus.CANCELLED,
            }:
                raise ValueError("已结束批次不能继续")
            blocked = session.scalar(
                select(PublishBatchItemRecord).where(
                    PublishBatchItemRecord.batch_id == batch_id,
                    PublishBatchItemRecord.status
                    == PublishBatchItemStatus.NEEDS_HUMAN,
                )
            )
            if blocked is not None:
                blocked.status = PublishBatchItemStatus.PENDING
                blocked.error_reason = None
            batch.pause_requested = False
            batch.status = PublishBatchStatus.RUNNING
            batch.next_run_at = datetime.now(UTC)
            batch.error_reason = None
        return self.payload(batch_id)

    def update(self, batch_id: str, request: PublishBatchUpdate) -> dict[str, object]:
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                raise KeyError("batch")
            if request.interval_minutes is not None:
                batch.interval_seconds = request.interval_minutes * 60
            if request.failure_policy is not None:
                batch.failure_policy = request.failure_policy
        return self.payload(batch_id)

    def cancel(self, batch_id: str) -> dict[str, object]:
        active_publish_id: str | None = None
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                raise KeyError("batch")
            if batch.status == PublishBatchStatus.CANCELLED:
                return self.payload(batch_id)
            items = session.scalars(
                select(PublishBatchItemRecord).where(
                    PublishBatchItemRecord.batch_id == batch_id
                )
            ).all()
            for item in items:
                if item.status == PublishBatchItemStatus.PUBLISHING:
                    active_publish_id = item.publish_id
                if item.status in {
                    PublishBatchItemStatus.PENDING,
                    PublishBatchItemStatus.NEEDS_HUMAN,
                }:
                    item.status = PublishBatchItemStatus.CANCELLED
            batch.status = PublishBatchStatus.CANCELLED
            batch.pause_requested = False
            batch.next_run_at = None
        if active_publish_id:
            try:
                self.publish_manager.cancel(active_publish_id)
            except (KeyError, ValueError):
                pass
        return self.payload(batch_id)


class PublishBatchManager:
    def __init__(
        self,
        database: Database,
        publishing: PublishingService,
        publish_manager: PublishManager,
    ) -> None:
        self.database = database
        self.publishing = publishing
        self.publish_manager = publish_manager
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="publish-batch-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                # 单个批次异常会在下一轮重试；详细错误由单条发布任务留档。
                pass
            await asyncio.sleep(1)

    def tick(self) -> None:
        with self.database.session() as session:
            batch_ids = list(
                session.scalars(
                    select(PublishBatchRecord.batch_id).where(
                        PublishBatchRecord.status.in_(
                            {
                                PublishBatchStatus.RUNNING,
                                PublishBatchStatus.WAITING_INTERVAL,
                                PublishBatchStatus.PAUSE_REQUESTED,
                                PublishBatchStatus.NEEDS_HUMAN,
                            }
                        )
                    )
                ).all()
            )
        for batch_id in batch_ids:
            self._tick_batch(batch_id)

    def _tick_batch(self, batch_id: str) -> None:
        now = datetime.now(UTC)
        dispatch: tuple[str, str] | None = None
        with self.database.session() as session:
            batch = session.get(PublishBatchRecord, batch_id)
            if batch is None:
                return
            items = list(
                session.scalars(
                    select(PublishBatchItemRecord)
                    .where(PublishBatchItemRecord.batch_id == batch_id)
                    .order_by(PublishBatchItemRecord.position)
                ).all()
            )
            active_item = next(
                (
                    item
                    for item in items
                    if item.status == PublishBatchItemStatus.PUBLISHING
                ),
                None,
            )
            if active_item is not None:
                job = session.get(PublishJobRecord, active_item.publish_id)
                if job is None:
                    active_item.status = PublishBatchItemStatus.FAILED
                    active_item.error_reason = "关联发布任务不存在"
                    batch.status = PublishBatchStatus.PAUSED
                    batch.error_reason = active_item.error_reason
                    return
                if job.stage == PublishStage.PUBLISHED:
                    active_item.status = PublishBatchItemStatus.PUBLISHED
                    active_item.published_at = now
                    active_item.error_reason = None
                    batch.error_reason = None
                    remaining = [
                        item
                        for item in items
                        if item.status == PublishBatchItemStatus.PENDING
                    ]
                    if batch.pause_requested:
                        batch.status = PublishBatchStatus.PAUSED
                        batch.next_run_at = None
                    elif remaining:
                        batch.status = PublishBatchStatus.WAITING_INTERVAL
                        batch.next_run_at = now + timedelta(
                            seconds=batch.interval_seconds
                        )
                    else:
                        failed = any(
                            item.status
                            in {
                                PublishBatchItemStatus.FAILED,
                                PublishBatchItemStatus.SKIPPED,
                            }
                            for item in items
                        )
                        batch.status = (
                            PublishBatchStatus.PARTIAL_FAILED
                            if failed
                            else PublishBatchStatus.COMPLETED
                        )
                        batch.next_run_at = None
                    return
                if job.stage in {
                    PublishStage.NEEDS_LOGIN,
                    PublishStage.NEEDS_SMS,
                    PublishStage.NEEDS_HUMAN,
                    PublishStage.FAILED_RETRYABLE,
                }:
                    active_item.status = PublishBatchItemStatus.NEEDS_HUMAN
                    active_item.error_reason = job.error_reason
                    batch.status = PublishBatchStatus.NEEDS_HUMAN
                    batch.error_reason = job.error_reason
                    batch.next_run_at = None
                    return
                if job.stage in {
                    PublishStage.FAILED_FINAL,
                    PublishStage.CANCELLED,
                }:
                    active_item.status = PublishBatchItemStatus.FAILED
                    active_item.error_reason = job.error_reason
                    batch.error_reason = job.error_reason
                    if batch.failure_policy == "skip":
                        remaining = [
                            item
                            for item in items
                            if item.status == PublishBatchItemStatus.PENDING
                        ]
                        if remaining:
                            batch.status = PublishBatchStatus.WAITING_INTERVAL
                            batch.next_run_at = now + timedelta(
                                seconds=batch.interval_seconds
                            )
                        else:
                            batch.status = PublishBatchStatus.PARTIAL_FAILED
                            batch.next_run_at = None
                    else:
                        batch.status = PublishBatchStatus.PAUSED
                        batch.next_run_at = None
                    return
                return

            if batch.pause_requested:
                batch.status = PublishBatchStatus.PAUSED
                batch.next_run_at = None
                return
            if batch.status == PublishBatchStatus.NEEDS_HUMAN:
                return
            if batch.approved_at is None:
                batch.status = PublishBatchStatus.READY
                return
            next_run_at = _aware(batch.next_run_at)
            if next_run_at is not None and next_run_at > now:
                batch.status = PublishBatchStatus.WAITING_INTERVAL
                return
            pending = next(
                (
                    item
                    for item in items
                    if item.status == PublishBatchItemStatus.PENDING
                ),
                None,
            )
            if pending is None:
                failed = any(
                    item.status
                    in {
                        PublishBatchItemStatus.FAILED,
                        PublishBatchItemStatus.SKIPPED,
                    }
                    for item in items
                )
                batch.status = (
                    PublishBatchStatus.PARTIAL_FAILED
                    if failed
                    else PublishBatchStatus.COMPLETED
                )
                batch.next_run_at = None
                return
            pending.status = PublishBatchItemStatus.PUBLISHING
            pending.started_at = now
            pending.error_reason = None
            batch.status = PublishBatchStatus.RUNNING
            batch.next_run_at = None
            dispatch = (pending.item_id, pending.publish_id)

        if dispatch is None:
            return
        item_id, publish_id = dispatch
        try:
            record = self.publishing.get_job(publish_id)
            if record is None:
                raise KeyError("publish")
            if record.approved_at is None:
                self.publishing.approve(publish_id)
            if record.stage in {
                PublishStage.FAILED_RETRYABLE,
                PublishStage.NEEDS_LOGIN,
                PublishStage.NEEDS_SMS,
                PublishStage.NEEDS_HUMAN,
                PublishStage.READY_FOR_PUBLISH,
            }:
                self.publish_manager.retry(publish_id)
            elif record.stage not in ACTIVE_STAGES:
                self.publish_manager.enqueue(publish_id)
        except Exception as exc:
            with self.database.session() as session:
                item = session.get(PublishBatchItemRecord, item_id)
                batch = session.get(PublishBatchRecord, batch_id)
                if item is not None:
                    item.status = PublishBatchItemStatus.NEEDS_HUMAN
                    item.error_reason = str(exc)
                if batch is not None:
                    batch.status = PublishBatchStatus.NEEDS_HUMAN
                    batch.error_reason = str(exc)
