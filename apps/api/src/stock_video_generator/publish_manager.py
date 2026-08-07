from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    PublishAccountRecord,
    PublishAttemptRecord,
    PublishJobRecord,
    PublishStage,
)
from stock_video_generator.douyin_publisher import (
    DouyinBrowserPublisher,
    PublishBrowserError,
    PublishNeedsHuman,
    PublishNeedsLogin,
    PublishNeedsSms,
)
from stock_video_generator.publishing import PublishingService

ACTIVE_STAGES = {
    PublishStage.VALIDATING_ARTIFACTS,
    PublishStage.CHECKING_LOGIN,
    PublishStage.OPENING_UPLOAD_PAGE,
    PublishStage.UPLOADING_VIDEO,
    PublishStage.WAITING_TRANSCODE,
    PublishStage.FILLING_TITLE,
    PublishStage.FILLING_DESCRIPTION,
    PublishStage.ADDING_TOPICS,
    PublishStage.SETTING_LANDSCAPE_COVER,
    PublishStage.SETTING_PORTRAIT_COVER,
    PublishStage.SETTING_COLLECTION,
    PublishStage.SETTING_DECLARATION,
    PublishStage.VALIDATING_PREVIEW,
    PublishStage.PUBLISHING,
    PublishStage.VERIFYING_RESULT,
}

TERMINAL_STAGES = {
    PublishStage.PUBLISHED,
    PublishStage.CANCELLED,
    PublishStage.FAILED_FINAL,
}

STAGE_PROGRESS = {
    PublishStage.CREATED: 0.0,
    PublishStage.READY_FOR_PUBLISH: 0.9,
    PublishStage.PUBLISHED: 1.0,
}


class PublishManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        service: PublishingService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.service = service
        self.publisher = DouyinBrowserPublisher(settings)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._login_tasks: dict[str, asyncio.Task[None]] = {}
        self._login_states: dict[str, dict[str, object]] = {}
        self._semaphore = asyncio.Semaphore(1)

    async def start(self) -> None:
        # A browser-side action cannot be assumed to have completed after a process
        # restart. Preserve the task and require an idempotent manual retry.
        with self.database.session() as session:
            records = session.scalars(
                select(PublishJobRecord).where(PublishJobRecord.stage.in_(ACTIVE_STAGES))
            ).all()
            for record in records:
                record.stage = PublishStage.FAILED_RETRYABLE
                record.error_type = "INTERRUPTED"
                record.error_reason = "应用重启中断了浏览器流程；请核对抖音作品管理后重试"

    async def stop(self) -> None:
        tasks = [*self._tasks.values(), *self._login_tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._login_tasks.clear()

    async def _update(self, publish_id: str, stage: PublishStage, progress: float) -> None:
        with self.database.session() as session:
            record = session.get(PublishJobRecord, publish_id)
            if record is None:
                return
            if record.cancellation_requested:
                raise asyncio.CancelledError
            record.stage = stage
            record.progress = progress
            record.error_type = None
            record.error_reason = None

    def enqueue(self, publish_id: str) -> PublishJobRecord:
        with self.database.session() as session:
            record = session.get(PublishJobRecord, publish_id)
            if record is None:
                raise KeyError("publish")
            if record.stage in ACTIVE_STAGES or publish_id in self._tasks:
                raise ValueError("发布任务正在执行")
            if record.stage in TERMINAL_STAGES:
                raise ValueError("终态发布任务不能直接执行")
            record.cancellation_requested = False
            record.error_type = None
            record.error_reason = None
            session.flush()
            payload = record
        task = asyncio.create_task(self._run(publish_id), name=f"publish-{publish_id}")
        self._tasks[publish_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(publish_id, None))
        return payload

    async def _run(self, publish_id: str) -> None:
        async with self._semaphore:
            with self.database.session() as session:
                record = session.get(PublishJobRecord, publish_id)
                if record is None:
                    return
                account = session.get(PublishAccountRecord, record.account_id)
                if account is None:
                    record.stage = PublishStage.FAILED_FINAL
                    record.error_type = "ACCOUNT_NOT_FOUND"
                    record.error_reason = "发布账号不存在"
                    return
                # 新任务默认已经是精简互动文案；这里同时兼容清理尚未发布的
                # 旧任务，且只匹配系统生成的完整说明，不覆盖手工文案。
                manifest = self.service.prepare_manifest_for_publish(record)
                attempt_no = (
                    session.scalar(
                        select(func.count(PublishAttemptRecord.attempt_id)).where(
                            PublishAttemptRecord.publish_id == publish_id
                        )
                    )
                    or 0
                ) + 1
                attempt = PublishAttemptRecord(
                    attempt_id=str(uuid4()),
                    publish_id=publish_id,
                    attempt_no=attempt_no,
                    stage=record.stage,
                )
                session.add(attempt)
                attempt_id = attempt.attempt_id
                approved = record.approved_at is not None and account.auto_publish_enabled
                profile_dir = Path(account.browser_profile_dir)
            try:
                result = await self.publisher.execute(
                    manifest,
                    profile_dir,
                    evidence_key=attempt_id,
                    approved=approved,
                    update=lambda stage, progress: self._update(publish_id, stage, progress),
                )
                with self.database.session() as session:
                    record = session.get(PublishJobRecord, publish_id)
                    attempt = session.get(PublishAttemptRecord, attempt_id)
                    if record is None or attempt is None:
                        return
                    record.stage = result.stage
                    record.progress = STAGE_PROGRESS.get(result.stage, 1.0)
                    record.agent_fallback_count += result.agent_fallback_count
                    record.published_item_id = result.item_id
                    record.published_url = result.published_url
                    account = session.get(PublishAccountRecord, record.account_id)
                    if account is not None:
                        account.last_login_at = datetime.now(UTC)
                    attempt.completed_at = datetime.now(UTC)
                    attempt.stage = result.stage
                    attempt.success = True
                    attempt.used_agent = result.agent_fallback_count > 0
                    attempt.screenshot_path = result.screenshot_path
                    attempt.dom_snapshot_path = result.dom_snapshot_path
                    attempt.action_log_path = result.action_log_path
                if result.stage == PublishStage.PUBLISHED:
                    self.service.remember_title(record, manifest.facts)
            except asyncio.CancelledError:
                with self.database.session() as session:
                    record = session.get(PublishJobRecord, publish_id)
                    attempt = session.get(PublishAttemptRecord, attempt_id)
                    if record is not None:
                        record.stage = PublishStage.CANCELLED
                        record.error_type = "CANCELLED"
                        record.error_reason = "发布任务已取消"
                    if attempt is not None:
                        attempt.completed_at = datetime.now(UTC)
                        attempt.stage = PublishStage.CANCELLED
                        attempt.error_type = "CANCELLED"
                raise
            except Exception as exc:
                if isinstance(exc, PublishNeedsLogin):
                    stage = PublishStage.NEEDS_LOGIN
                elif isinstance(exc, PublishNeedsSms):
                    stage = PublishStage.NEEDS_SMS
                elif isinstance(exc, PublishNeedsHuman):
                    stage = PublishStage.NEEDS_HUMAN
                elif isinstance(exc, (ValueError, KeyError)):
                    stage = PublishStage.FAILED_FINAL
                elif isinstance(exc, PublishBrowserError) and not exc.retryable:
                    stage = PublishStage.FAILED_FINAL
                else:
                    stage = PublishStage.FAILED_RETRYABLE
                with self.database.session() as session:
                    record = session.get(PublishJobRecord, publish_id)
                    attempt = session.get(PublishAttemptRecord, attempt_id)
                    if record is not None:
                        record.stage = stage
                        record.error_type = type(exc).__name__
                        record.error_reason = str(exc)
                        if stage == PublishStage.FAILED_RETRYABLE:
                            record.retry_count += 1
                    if attempt is not None:
                        attempt.completed_at = datetime.now(UTC)
                        attempt.stage = stage
                        attempt.error_type = type(exc).__name__
                        attempt.error_reason = str(exc)
                        evidence_dir = (
                            self.settings.data_dir
                            / "publishes"
                            / publish_id
                            / "attempt-evidence"
                            / attempt_id
                        )
                        screenshots = sorted(
                            evidence_dir.glob("*.png"),
                            key=lambda path: path.stat().st_mtime,
                        )
                        dom_files = sorted(
                            evidence_dir.glob("*.html"),
                            key=lambda path: path.stat().st_mtime,
                        )
                        action_log = evidence_dir / "actions.json"
                        agent_results = list(evidence_dir.glob("agent-result-*.json"))
                        if record is not None and agent_results:
                            record.agent_fallback_count += len(agent_results)
                        attempt.screenshot_path = str(screenshots[-1]) if screenshots else None
                        attempt.dom_snapshot_path = str(dom_files[-1]) if dom_files else None
                        attempt.action_log_path = str(action_log) if action_log.is_file() else None
                        attempt.used_agent = bool(agent_results)

    def retry(self, publish_id: str) -> PublishJobRecord:
        with self.database.session() as session:
            record = session.get(PublishJobRecord, publish_id)
            if record is None:
                raise KeyError("publish")
            if record.stage not in {
                PublishStage.FAILED_RETRYABLE,
                PublishStage.NEEDS_LOGIN,
                PublishStage.NEEDS_SMS,
                PublishStage.NEEDS_HUMAN,
                PublishStage.READY_FOR_PUBLISH,
            }:
                raise ValueError("当前状态不允许重试")
            record.stage = PublishStage.CREATED
            record.progress = 0
            record.error_type = None
            record.error_reason = None
            session.flush()
        return self.enqueue(publish_id)

    def cancel(self, publish_id: str) -> PublishJobRecord:
        with self.database.session() as session:
            record = session.get(PublishJobRecord, publish_id)
            if record is None:
                raise KeyError("publish")
            if record.stage in TERMINAL_STAGES:
                raise ValueError("终态任务不能取消")
            record.cancellation_requested = True
            task = self._tasks.get(publish_id)
            if task is None:
                record.stage = PublishStage.CANCELLED
                record.error_type = "CANCELLED"
                record.error_reason = "发布任务已取消"
            session.flush()
            payload = record
        if task is not None:
            task.cancel()
        return payload

    def attempts(self, publish_id: str) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(PublishAttemptRecord)
                .where(PublishAttemptRecord.publish_id == publish_id)
                .order_by(PublishAttemptRecord.attempt_no)
            ).all()
            return [
                {
                    "attempt_id": row.attempt_id,
                    "attempt_no": row.attempt_no,
                    "started_at": row.started_at,
                    "completed_at": row.completed_at,
                    "stage": row.stage,
                    "success": row.success,
                    "used_agent": row.used_agent,
                    "error_type": row.error_type,
                    "error_reason": row.error_reason,
                    "screenshot_path": row.screenshot_path,
                    "dom_snapshot_path": row.dom_snapshot_path,
                    "action_log_path": row.action_log_path,
                }
                for row in rows
            ]

    def start_login(self, account_id: str) -> dict[str, object]:
        if account_id in self._login_tasks:
            raise ValueError("该账号的登录窗口已经打开")
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            active_publish = session.scalar(
                select(PublishJobRecord).where(
                    PublishJobRecord.account_id == account_id,
                    PublishJobRecord.stage.in_(ACTIVE_STAGES),
                )
            )
            if active_publish is not None:
                raise ValueError("该账号正在执行发布任务，暂时不能打开登录窗口")
            profile_dir = Path(account.browser_profile_dir)
        self._login_states[account_id] = {
            "account_id": account_id,
            "status": "opening",
            "message": "正在打开抖音扫码登录窗口",
            "updated_at": datetime.now(UTC),
        }
        task = asyncio.create_task(
            self._login(account_id, profile_dir),
            name=f"publish-login-{account_id}",
        )
        self._login_tasks[account_id] = task
        task.add_done_callback(lambda _: self._login_tasks.pop(account_id, None))
        return self.login_status(account_id)

    async def _login(self, account_id: str, profile_dir: Path) -> None:
        evidence_dir = self.settings.data_dir / "publish-accounts" / account_id / "login-evidence"
        self._login_states[account_id] = {
            "account_id": account_id,
            "status": "waiting_scan",
            "message": "请在弹出的浏览器窗口中扫码登录",
            "updated_at": datetime.now(UTC),
        }
        try:
            screenshot, dom = await self.publisher.login(profile_dir, evidence_dir)
            with self.database.session() as session:
                account = session.get(PublishAccountRecord, account_id)
                if account is not None:
                    account.last_login_at = datetime.now(UTC)
            self._login_states[account_id] = {
                "account_id": account_id,
                "status": "logged_in",
                "message": "登录成功，会话已保存",
                "screenshot_path": screenshot,
                "dom_snapshot_path": dom,
                "updated_at": datetime.now(UTC),
            }
        except asyncio.CancelledError:
            self._login_states[account_id] = {
                "account_id": account_id,
                "status": "cancelled",
                "message": "登录流程已停止",
                "updated_at": datetime.now(UTC),
            }
            raise
        except Exception as exc:
            self._login_states[account_id] = {
                "account_id": account_id,
                "status": "failed",
                "message": str(exc),
                "error_type": type(exc).__name__,
                "updated_at": datetime.now(UTC),
            }

    def login_status(self, account_id: str) -> dict[str, object]:
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            last_login_at = account.last_login_at
        state = self._login_states.get(
            account_id,
            {
                "account_id": account_id,
                "status": "idle",
                "message": "尚未启动登录",
                "updated_at": datetime.now(UTC),
            },
        )
        return {**state, "last_login_at": last_login_at}
