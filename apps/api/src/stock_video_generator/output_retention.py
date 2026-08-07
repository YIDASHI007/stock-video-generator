from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    OutputRecord,
    PipelineRunRecord,
    PublishBatchItemRecord,
    PublishBatchItemStatus,
    PublishJobRecord,
    PublishStage,
    now_utc,
)
from stock_video_generator.thumbnails import cover_path, thumbnail_path

logger = logging.getLogger(__name__)

RELEASABLE_PUBLISH_STAGES = {
    PublishStage.PUBLISHED,
    PublishStage.FAILED_FINAL,
    PublishStage.CANCELLED,
}
RELEASABLE_BATCH_ITEM_STATUSES = {
    PublishBatchItemStatus.PUBLISHED,
    PublishBatchItemStatus.FAILED,
    PublishBatchItemStatus.SKIPPED,
    PublishBatchItemStatus.CANCELLED,
}


class OutputRetentionManager:
    """Periodically removes expired, unpublished gallery outputs and their files."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self._task: asyncio.Task[None] | None = None
        self.last_run_at: datetime | None = None
        self.last_result: dict[str, object] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(),
                name="output-retention-cleanup",
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.output_cleanup_interval_seconds)
            try:
                self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Automatic output retention cleanup failed")

    def cleanup_once(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or now_utc()
        cutoff = current - timedelta(days=self.settings.output_retention_days)
        output_root = (self.settings.data_dir / "outputs").resolve()
        files_to_remove: set[Path] = set()

        with self.database.session() as session:
            expired = session.scalars(
                select(OutputRecord)
                .where(OutputRecord.created_at < cutoff)
                .order_by(OutputRecord.created_at.asc())
            ).all()
            expired_ids = [output.output_id for output in expired]
            protected_ids: set[str] = set()
            if expired_ids:
                protected_ids.update(
                    session.scalars(
                        select(PublishJobRecord.output_id).where(
                            PublishJobRecord.output_id.in_(expired_ids),
                            PublishJobRecord.stage.not_in(RELEASABLE_PUBLISH_STAGES),
                        )
                    ).all()
                )
                protected_ids.update(
                    session.scalars(
                        select(PublishBatchItemRecord.output_id).where(
                            PublishBatchItemRecord.output_id.in_(expired_ids),
                            PublishBatchItemRecord.status.not_in(
                                RELEASABLE_BATCH_ITEM_STATUSES
                            ),
                        )
                    ).all()
                )

            deleted_ids: list[str] = []
            for output in expired:
                if output.output_id in protected_ids:
                    continue
                raw_paths = (
                    output.video_path,
                    output.validation_path,
                    str(thumbnail_path(self.settings, output.render_id)),
                    str(cover_path(self.settings, output.render_id, "portrait")),
                    str(cover_path(self.settings, output.render_id, "landscape")),
                )
                for raw_path in raw_paths:
                    if not raw_path:
                        continue
                    candidate = Path(raw_path).resolve()
                    try:
                        candidate.relative_to(output_root)
                    except ValueError:
                        logger.warning(
                            "Skipped output file outside managed root: %s",
                            candidate,
                        )
                        continue
                    files_to_remove.add(candidate)

                for run in session.scalars(
                    select(PipelineRunRecord).where(
                        PipelineRunRecord.output_id == output.output_id
                    )
                ).all():
                    run.output_id = None
                session.delete(output)
                deleted_ids.append(output.output_id)

        removed_files = 0
        reclaimed_bytes = 0
        failed_files = 0
        for path in files_to_remove:
            try:
                size = path.stat().st_size if path.is_file() else 0
                path.unlink(missing_ok=True)
                if size:
                    removed_files += 1
                    reclaimed_bytes += size
            except OSError:
                failed_files += 1
                logger.exception("Failed to remove expired output file: %s", path)

        result: dict[str, object] = {
            "retention_days": self.settings.output_retention_days,
            "cutoff": cutoff.isoformat(),
            "expired_count": len(expired),
            "protected_count": len(protected_ids),
            "deleted_count": len(deleted_ids),
            "deleted_output_ids": deleted_ids,
            "removed_files": removed_files,
            "failed_files": failed_files,
            "reclaimed_bytes": reclaimed_bytes,
        }
        self.last_run_at = current
        self.last_result = result
        logger.info("Output retention cleanup completed: %s", result)
        return result

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "retention_days": self.settings.output_retention_days,
            "cleanup_interval_seconds": self.settings.output_cleanup_interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_result": self.last_result,
        }
