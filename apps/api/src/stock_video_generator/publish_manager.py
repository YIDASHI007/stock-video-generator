from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    PublishAccountRecord,
    PublishAttemptRecord,
    PublishBatchRecord,
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
from stock_video_generator.social_account_auth import (
    PLATFORM_AUTH_SPECS,
    SocialAccountAuthenticator,
    SocialPlatform,
)

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
        extractor_cookie_sync: (
            Callable[[list[dict[str, object]]], Awaitable[dict[str, object]]] | None
        ) = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.service = service
        self.publisher = DouyinBrowserPublisher(settings)
        self.account_auth = SocialAccountAuthenticator(settings)
        self.extractor_cookie_sync = extractor_cookie_sync
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
                if account.platform != "douyin":
                    record.stage = PublishStage.FAILED_FINAL
                    record.error_type = "PLATFORM_NOT_SUPPORTED"
                    record.error_reason = "当前自动发布流程暂时只支持抖音账号"
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
            raise ValueError("该账号的扫码登录已经启动")
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
            platform = account.platform
            label = PLATFORM_AUTH_SPECS[platform].label
        self._login_states[account_id] = {
            "account_id": account_id,
            "status": "preparing_qr",
            "message": f"正在获取{label}官方登录二维码",
            "updated_at": datetime.now(UTC),
        }
        task = asyncio.create_task(
            self._login(account_id, platform, profile_dir),
            name=f"publish-login-{account_id}",
        )
        self._login_tasks[account_id] = task
        task.add_done_callback(lambda _: self._login_tasks.pop(account_id, None))
        return self.login_status(account_id)

    async def _login(
        self,
        account_id: str,
        platform: SocialPlatform,
        profile_dir: Path,
    ) -> None:
        evidence_dir = self.settings.data_dir / "publish-accounts" / account_id / "login-evidence"
        label = PLATFORM_AUTH_SPECS[platform].label

        async def on_qr_ready(path: Path) -> None:
            current = self._login_states.get(account_id, {})
            self._login_states[account_id] = {
                **current,
                "account_id": account_id,
                "status": (
                    current.get("status")
                    if current.get("status") == "scanned"
                    else "waiting_scan"
                ),
                "message": (
                    current.get("message")
                    if current.get("status") == "scanned"
                    else f"请使用手机扫码登录{label}"
                ),
                "qr_code_path": str(path),
                "qr_code_url": f"/api/accounts/{account_id}/login/qr",
                "qr_revision": int(datetime.now(UTC).timestamp() * 1000),
                "updated_at": datetime.now(UTC),
            }
        async def on_progress(status_value: str, message: str) -> None:
            current = self._login_states.get(account_id, {})
            self._login_states[account_id] = {
                **current,
                "account_id": account_id,
                "status": status_value,
                "message": message,
                "updated_at": datetime.now(UTC),
            }
        try:
            result = await self.account_auth.login(
                platform,
                profile_dir,
                evidence_dir,
                on_qr_ready=on_qr_ready,
                on_progress=on_progress,
            )
            with self.database.session() as session:
                account = session.get(PublishAccountRecord, account_id)
                if account is not None:
                    account.last_login_at = datetime.now(UTC)
                    account.last_checked_at = datetime.now(UTC)
                    account.auth_status = "logged_in"
            self._login_states[account_id] = {
                "account_id": account_id,
                "status": "logged_in",
                "message": f"{label}登录成功，会话已保存",
                "screenshot_path": result.screenshot_path,
                "dom_snapshot_path": result.dom_snapshot_path,
                "updated_at": datetime.now(UTC),
            }
            if platform == "douyin" and self.extractor_cookie_sync is not None:
                try:
                    sync_result = await self.sync_extractor_cookies(account_id)
                    self._login_states[account_id]["message"] = (
                        f"{label}登录成功，抓取凭证已同步（{sync_result['cookie_count']} 项）"
                    )
                except Exception as sync_error:
                    self._login_states[account_id]["message"] = (
                        f"{label}登录成功，但抓取凭证同步失败：{sync_error}"
                    )
        except asyncio.CancelledError:
            self._login_states[account_id] = {
                "account_id": account_id,
                "status": "cancelled",
                "message": "登录流程已停止",
                "updated_at": datetime.now(UTC),
            }
            raise
        except Exception as exc:
            with self.database.session() as session:
                account = session.get(PublishAccountRecord, account_id)
                if account is not None:
                    account.auth_status = "login_failed"
                    account.last_checked_at = datetime.now(UTC)
            self._login_states[account_id] = {
                "account_id": account_id,
                "status": "failed",
                "message": str(exc),
                "error_type": type(exc).__name__,
                "updated_at": datetime.now(UTC),
            }

    async def sync_extractor_cookies(self, account_id: str) -> dict[str, object]:
        if self.extractor_cookie_sync is None:
            raise ValueError("抓取凭证同步服务尚未配置")
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            platform = account.platform
            profile_dir = Path(account.browser_profile_dir)
        cookies = await self.account_auth.export_cookies(platform, profile_dir)
        return await self.extractor_cookie_sync(cookies)

    def login_status(self, account_id: str) -> dict[str, object]:
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            last_login_at = account.last_login_at
            platform = account.platform
            auth_status = account.auth_status
        state = self._login_states.get(
            account_id,
            {
                "account_id": account_id,
                "status": "idle",
                "message": "尚未启动登录",
                "updated_at": datetime.now(UTC),
            },
        )
        return {
            **{key: value for key, value in state.items() if key != "qr_code_path"},
            "platform": platform,
            "auth_status": auth_status,
            "last_login_at": last_login_at,
        }

    def login_qr_path(self, account_id: str) -> Path:
        with self.database.session() as session:
            if session.get(PublishAccountRecord, account_id) is None:
                raise KeyError("account")
        state = self._login_states.get(account_id, {})
        value = state.get("qr_code_path")
        if not isinstance(value, str):
            raise ValueError("二维码尚未生成")
        path = Path(value).resolve()
        expected_root = (
            self.settings.data_dir / "publish-accounts" / account_id / "login-evidence"
        ).resolve()
        if expected_root not in path.parents or not path.is_file():
            raise ValueError("二维码尚未生成或已经失效")
        return path

    def cancel_login(self, account_id: str) -> dict[str, object]:
        with self.database.session() as session:
            if session.get(PublishAccountRecord, account_id) is None:
                raise KeyError("account")
        task = self._login_tasks.get(account_id)
        if task is not None and not task.done():
            task.cancel()
        current = self._login_states.get(account_id, {})
        self._login_states[account_id] = {
            **{key: value for key, value in current.items() if key != "qr_code_path"},
            "account_id": account_id,
            "status": "cancelled",
            "message": "已取消扫码登录",
            "updated_at": datetime.now(UTC),
        }
        return self.login_status(account_id)

    async def check_account(self, account_id: str) -> dict[str, object]:
        if account_id in self._login_tasks:
            raise ValueError("该账号正在等待扫码，暂时不能检测")
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            platform: SocialPlatform = account.platform
            profile_dir = Path(account.browser_profile_dir)
        evidence_dir = (
            self.settings.data_dir / "publish-accounts" / account_id / "login-evidence"
        )
        result = await self.account_auth.check(platform, profile_dir, evidence_dir)
        checked_at = datetime.now(UTC)
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            account.auth_status = "logged_in" if result.logged_in else "logged_out"
            account.last_checked_at = checked_at
            if result.logged_in:
                account.last_login_at = account.last_login_at or checked_at
        label = PLATFORM_AUTH_SPECS[platform].label
        return {
            "account_id": account_id,
            "platform": platform,
            "status": "logged_in" if result.logged_in else "logged_out",
            "message": f"{label}登录状态正常" if result.logged_in else f"{label}登录已失效",
            "last_login_at": account.last_login_at,
            "updated_at": checked_at,
        }

    def unbind_account(self, account_id: str) -> PublishAccountRecord:
        if account_id in self._login_tasks:
            raise ValueError("该账号正在等待扫码，请先关闭登录窗口")
        with self.database.session() as session:
            active_publish = session.scalar(
                select(PublishJobRecord).where(
                    PublishJobRecord.account_id == account_id,
                    PublishJobRecord.stage.in_(ACTIVE_STAGES),
                )
            )
        if active_publish is not None:
            raise ValueError("该账号正在执行发布任务，暂时不能解绑")
        self._login_states.pop(account_id, None)
        return self.service.unbind_account(account_id)

    def delete_account(self, account_id: str) -> None:
        if account_id in self._login_tasks:
            raise ValueError("该账号正在等待扫码，请先关闭登录窗口")
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            if account.enabled:
                raise ValueError("请先解绑账号，再执行删除")
            publish_job = session.scalar(
                select(PublishJobRecord).where(PublishJobRecord.account_id == account_id)
            )
            publish_batch = session.scalar(
                select(PublishBatchRecord).where(PublishBatchRecord.account_id == account_id)
            )
        if publish_job is not None or publish_batch is not None:
            raise ValueError("该账号存在发布记录或批量任务，不能直接删除")
        self._login_states.pop(account_id, None)
        self.service.delete_account(account_id)
