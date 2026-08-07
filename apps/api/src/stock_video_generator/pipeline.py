"""全链路自动生产总控：选题 → 回测 → 脚本 → 配音 → 渲染。

状态机：
    TOPIC_QUEUED → SIMULATING → SCRIPTING → VOICING → RENDERING → COMPLETED
    任一阶段失败 → FAILED（retry_count < max_retries 自动重跑整条链，
    达到上限 → PARKED 搁浅让出队列，等待人工 retry/skip）。

每个阶段委托现有任务机执行：SIMULATION 任务内部已完成回测+脚本+配音，
RENDER 任务负责渲染出片；断点产物复用由任务机自身保证。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from stock_video_generator.database import (
    PIPELINE_ACTIVE_STATUSES,
    Database,
    JobRecord,
    JobStage,
    JobType,
    OutputRecord,
    PipelineRunRecord,
    PipelineStatus,
    TopicRecord,
    TopicStatus,
    now_utc,
)
from stock_video_generator.errors import (
    PipelineConflictError,
    StockVideoError,
    TopicPoolEmptyError,
)
from stock_video_generator.jobs import JobManager
from stock_video_generator.models import (
    Market,
    SimulationRequest,
    VideoConfig,
)
from stock_video_generator.topics import (
    ALL_ANGLES,
    DEFAULT_ANGLE_WEIGHTS,
    TopicDirective,
    TopicSelector,
)

logger = logging.getLogger(__name__)

MARKET_CURRENCY = {
    Market.CN: "CNY",
    Market.HK: "HKD",
    Market.US: "USD",
    Market.CRYPTO: "USD",
}

# SIMULATION 任务阶段 → 流水线阶段映射
_SIMULATION_STAGE_MAP = {
    JobStage.RESOLVING_SYMBOL: PipelineStatus.SIMULATING,
    JobStage.FETCHING_MARKET_DATA: PipelineStatus.SIMULATING,
    JobStage.VALIDATING_DATA: PipelineStatus.SIMULATING,
    JobStage.SIMULATING_PORTFOLIO: PipelineStatus.SIMULATING,
    JobStage.SCRIPTING: PipelineStatus.SCRIPTING,
    JobStage.BUILDING_VIDEO_SPEC: PipelineStatus.SCRIPTING,
    JobStage.VOICING: PipelineStatus.VOICING,
}

REPLENISH_MIN_INTERVAL_SECONDS = 600


class PipelinePolicy(BaseModel):
    """自动生产策略（持久化在 data/pipeline_policy.json）。"""

    enabled: bool = False
    # 每日配额：None = 无上限（前端留空即不传/传 null）。
    daily_quota: int | None = Field(default=None, ge=1)
    amount: float = Field(default=1_000_000, gt=0)
    markets: list[Market] = Field(
        default_factory=lambda: [Market.CN, Market.HK, Market.US, Market.CRYPTO]
    )
    angle_weights: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_ANGLE_WEIGHTS)
    )
    voice: str = "zh-CN-XiaoxiaoNeural"
    # 配音总开关：关闭后流水线跳过脚本/配音阶段，播放头匀速推进。
    voiceover_enabled: bool = False
    # 目标视频时长（秒）：15-180，默认 60；滚动段速度随时长自适应。
    target_duration: int = Field(default=60, ge=15, le=180)
    pool_target: int = Field(default=10, ge=1, le=50)
    # 选题偏好：全部留空 = 均衡随机（现状行为）。
    topic_directive: TopicDirective = Field(default_factory=TopicDirective)
    # 背景音乐文件名（存于 data/assets/bgm/，渲染时混入，音量低于人声）。
    bgm_file: str | None = None

    @field_validator("markets")
    @classmethod
    def markets_not_empty(cls, value: list[Market]) -> list[Market]:
        if not value:
            raise ValueError("至少启用一个市场。")
        return value

    @field_validator("angle_weights")
    @classmethod
    def weights_valid(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = set(value) - set(ALL_ANGLES)
        if unknown:
            raise ValueError(f"未知选题角度：{sorted(unknown)}")
        merged = {angle: int(value.get(angle, 0)) for angle in ALL_ANGLES}
        if any(weight < 0 for weight in merged.values()):
            raise ValueError("角度权重不能为负。")
        if sum(merged.values()) <= 0:
            raise ValueError("至少一个角度权重大于 0。")
        return merged


class PolicyStore:
    """策略 JSON 文件持久化（原子写入）。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> PipelinePolicy:
        if not self.path.is_file():
            return PipelinePolicy()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return PipelinePolicy.model_validate(raw)
        except Exception as exc:
            logger.warning("策略文件损坏，已回退默认策略：%s", exc)
            return PipelinePolicy()

    def save(self, policy: PipelinePolicy) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            policy.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class PipelineStageFailed(StockVideoError):
    code = "PIPELINE_STAGE_FAILED"


class PipelineManager:
    def __init__(
        self,
        settings,
        database: Database,
        jobs: JobManager,
        selector: TopicSelector,
        policy_store: PolicyStore,
        *,
        max_retries: int = 3,
        poll_interval_seconds: float = 2.0,
        retry_backoff_seconds: float = 30.0,
        auto_loop_interval_seconds: float = 30.0,
    ) -> None:
        self.settings = settings
        self.database = database
        self.jobs = jobs
        self.selector = selector
        self.policy_store = policy_store
        self.max_retries = max_retries
        self.poll_interval_seconds = poll_interval_seconds
        self.retry_backoff_seconds = retry_backoff_seconds
        self.auto_loop_interval_seconds = auto_loop_interval_seconds
        self._drivers: dict[str, asyncio.Task[None]] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._replenish_task: asyncio.Task[None] | None = None
        self._last_replenish_at: datetime | None = None
        self._resume_existing_runs: set[str] = set()

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self.recover_interrupted_runs()
        policy = self.policy_store.load()
        queue_report = self.selector.refresh_queue_for_policy(policy)
        if queue_report["rejected"] or queue_report["added"]:
            logger.info(
                "启动时按当前策略同步选题池：清退 %s，补入 %s，水位 %s",
                queue_report["rejected"],
                len(queue_report["added"]),
                queue_report["pool_size"],
            )
        # 进程重启后自动恢复未到搁浅上限的失败 run，保持无人值守语义
        with self.database.session() as session:
            runs = session.scalars(
                select(PipelineRunRecord).where(
                    PipelineRunRecord.status == PipelineStatus.FAILED,
                    PipelineRunRecord.retry_count < self.max_retries,
                )
            ).all()
            resumable = [run.run_id for run in runs]
        for run_id in resumable:
            self._start_driver(run_id)
        self._loop_task = asyncio.create_task(self._auto_loop())

    async def stop(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        if self._loop_task:
            self._loop_task.cancel()
            tasks.append(self._loop_task)
        if self._replenish_task:
            self._replenish_task.cancel()
            tasks.append(self._replenish_task)
        tasks.extend(self._drivers.values())
        for task in self._drivers.values():
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._drivers = {}
        self._loop_task = None
        self._replenish_task = None

    def recover_interrupted_runs(self) -> None:
        """进程重启时仍处于进行中状态的 run → FAILED（计入重试上限）。"""
        with self.database.session() as session:
            runs = session.scalars(
                select(PipelineRunRecord).where(
                    PipelineRunRecord.status.in_(
                        [str(status) for status in PIPELINE_ACTIVE_STATUSES]
                    )
                )
            ).all()
            for run in runs:
                self._resume_existing_runs.add(run.run_id)
                run.retry_count += 1
                run.error = "程序重启，生产被打断。"
                run.status = (
                    PipelineStatus.PARKED
                    if run.retry_count >= self.max_retries
                    else PipelineStatus.FAILED
                )
                run.updated_at = now_utc()

    # ---------- 查询 ----------

    def get_run(self, run_id: str) -> PipelineRunRecord | None:
        with self.database.session() as session:
            return session.get(PipelineRunRecord, run_id)

    def list_runs(
        self,
        filter_: str = "all",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        with self.database.session() as session:
            statement = select(PipelineRunRecord)
            if filter_ == "active":
                statement = statement.where(
                    PipelineRunRecord.status.in_(
                        [str(status) for status in PIPELINE_ACTIVE_STATUSES]
                    )
                )
            elif filter_ == "parked":
                statement = statement.where(
                    PipelineRunRecord.status.in_(
                        [PipelineStatus.PARKED, PipelineStatus.FAILED]
                    )
                )
            runs = session.scalars(
                statement.order_by(PipelineRunRecord.created_at.desc()).limit(limit)
            ).all()
            topic_ids = {run.topic_id for run in runs}
            topics = {
                topic.topic_id: topic
                for topic in session.scalars(
                    select(TopicRecord).where(TopicRecord.topic_id.in_(topic_ids))
                ).all()
            } if topic_ids else {}
            return [self.run_payload(run, topics.get(run.topic_id)) for run in runs]

    @staticmethod
    def run_payload(
        run: PipelineRunRecord,
        topic: TopicRecord | None,
    ) -> dict[str, object]:
        return {
            "run_id": run.run_id,
            "topic_id": run.topic_id,
            "status": run.status,
            "current_stage": run.current_stage,
            "simulation_id": run.simulation_id,
            "render_id": run.render_id,
            "output_id": run.output_id,
            "error": run.error,
            "retry_count": run.retry_count,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "topic": (
                {
                    "symbol": topic.symbol,
                    "name": topic.name,
                    "market": topic.market,
                    "buy_date": topic.buy_date,
                    "amount": topic.amount,
                    "angle": topic.angle,
                    "drama_score": topic.drama_score,
                }
                if topic
                else None
            ),
        }

    def _today_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        local = now.astimezone()
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        # SQLite 按字符串存储带时区时间，统一转成 UTC 再比较
        return start.astimezone(UTC), now.astimezone(UTC)

    def status_summary(self, now: datetime | None = None) -> dict[str, object]:
        now = now or now_utc()
        start, end = self._today_bounds(now)
        policy = self.policy_store.load()
        with self.database.session() as session:
            runs_today = session.scalars(
                select(PipelineRunRecord).where(
                    PipelineRunRecord.created_at >= start,
                    PipelineRunRecord.created_at <= end,
                )
            ).all()
            started = sum(
                1
                for run in runs_today
                if PipelineStatus(run.status)
                not in (PipelineStatus.PARKED, PipelineStatus.SKIPPED)
            )
            completed_today = sum(
                1
                for run in runs_today
                if PipelineStatus(run.status) == PipelineStatus.COMPLETED
            )
            active = session.scalars(
                select(PipelineRunRecord).where(
                    PipelineRunRecord.status.in_(
                        [str(status) for status in PIPELINE_ACTIVE_STATUSES]
                    )
                )
            ).all()
            parked = session.scalars(
                select(PipelineRunRecord).where(
                    PipelineRunRecord.status == PipelineStatus.PARKED
                )
            ).all()
        return {
            "enabled": policy.enabled,
            "daily_quota": policy.daily_quota,
            "today_started": started,
            "today_completed": completed_today,
            "pool_size": self.selector.queued_count(policy.markets),
            "story_pool": self.selector.story_pool_status(),
            "active_runs": len(active),
            "parked_count": len(parked),
            "policy": policy.model_dump(mode="json"),
        }

    # ---------- 手动操作 ----------

    async def run_once(self) -> dict[str, object]:
        """手动一键：取队首选题跑全流程（绕过每日配额，但仍受单发槽位限制）。"""
        if any(not task.done() for task in self._drivers.values()):
            raise PipelineConflictError(
                "已有一条自动生产正在进行，请等待完成或在任务中心处理。",
            )
        policy = self.policy_store.load()
        topic = self.selector.next_topic(
            policy.markets,
            policy.topic_directive,
            amount=policy.amount,
        )
        if topic is None:
            # 池空时先尝试真实补充；失败则如实报错
            await self.selector.replenish(policy)
            topic = self.selector.next_topic(
                policy.markets,
                policy.topic_directive,
                amount=policy.amount,
            )
        if topic is None:
            raise TopicPoolEmptyError(
                "选题池为空，且本次补充没有产出新选题。",
                detail=(
                    "请检查动态股票库是否已完成同步、所选市场是否仍有"
                    "可生产股票，以及行情数据源健康状态。"
                ),
            )
        run = self._launch(topic.topic_id)
        with self.database.session() as session:
            stored = session.get(PipelineRunRecord, run["run_id"])
            topic_record = session.get(TopicRecord, run["topic_id"])
            return self.run_payload(stored, topic_record)

    async def retry_run(self, run_id: str) -> PipelineRunRecord:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if PipelineStatus(run.status) not in (
            PipelineStatus.FAILED,
            PipelineStatus.PARKED,
        ):
            raise PipelineConflictError("只有失败或搁浅的生产任务可以重跑。")
        if any(not task.done() for task in self._drivers.values()):
            raise PipelineConflictError("已有生产任务在进行中，请稍后重试。")
        with self.database.session() as session:
            stored = session.get(PipelineRunRecord, run_id)
            stored.status = PipelineStatus.TOPIC_QUEUED
            stored.current_stage = PipelineStatus.TOPIC_QUEUED
            stored.error = None
            stored.retry_count = 0
            stored.updated_at = now_utc()
        self._start_driver(run_id)
        return self.get_run(run_id)

    def skip_run(self, run_id: str) -> PipelineRunRecord:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if PipelineStatus(run.status) not in (
            PipelineStatus.FAILED,
            PipelineStatus.PARKED,
        ):
            raise PipelineConflictError("只有失败或搁浅的生产任务可以跳过。")
        with self.database.session() as session:
            stored = session.get(PipelineRunRecord, run_id)
            topic_id = stored.topic_id
            stored.status = PipelineStatus.SKIPPED
            stored.current_stage = PipelineStatus.SKIPPED
            stored.updated_at = now_utc()
        self.selector.mark_story_rejected(topic_id, "生产任务被人工跳过")
        return self.get_run(run_id)

    # ---------- 生产驱动 ----------

    def _launch(self, topic_id: str) -> dict[str, str]:
        run_id = str(uuid4())
        with self.database.session() as session:
            topic = session.get(TopicRecord, topic_id)
            if not topic or topic.status != TopicStatus.QUEUED:
                raise TopicPoolEmptyError("队首选题已被消费或不存在。")
            topic.status = TopicStatus.CONSUMED
            topic.consumed_at = now_utc()
            session.add(
                PipelineRunRecord(
                    run_id=run_id,
                    topic_id=topic_id,
                    status=PipelineStatus.TOPIC_QUEUED,
                    current_stage=PipelineStatus.TOPIC_QUEUED,
                )
            )
        self._start_driver(run_id)
        return {"run_id": run_id, "topic_id": topic_id}

    def _start_driver(self, run_id: str) -> None:
        task = asyncio.create_task(self._drive(run_id))
        self._drivers[run_id] = task
        task.add_done_callback(lambda _: self._drivers.pop(run_id, None))

    def _update_run(
        self,
        run_id: str,
        *,
        status: PipelineStatus | None = None,
        current_stage: PipelineStatus | None = None,
        simulation_id: str | None = None,
        render_id: str | None = None,
        output_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.database.session() as session:
            run = session.get(PipelineRunRecord, run_id)
            if not run:
                raise RuntimeError(f"Pipeline run {run_id} disappeared")
            if status is not None:
                run.status = status
            if current_stage is not None:
                run.current_stage = current_stage
            if simulation_id is not None:
                run.simulation_id = simulation_id
            if render_id is not None:
                run.render_id = render_id
            if output_id is not None:
                run.output_id = output_id
            if error is not None:
                run.error = error
            run.updated_at = now_utc()

    async def _drive(self, run_id: str) -> None:
        try:
            while True:
                try:
                    resume_existing = run_id in self._resume_existing_runs
                    self._resume_existing_runs.discard(run_id)
                    await self._execute_attempt(
                        run_id,
                        resume_existing=resume_existing,
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("流水线 run 执行失败：%s", run_id)
                    reason = str(exc)
                    if isinstance(exc, StockVideoError) and exc.detail:
                        reason = f"{exc.message} {exc.detail}"
                    with self.database.session() as session:
                        run = session.get(PipelineRunRecord, run_id)
                        if not run:
                            return
                        run.retry_count += 1
                        run.error = reason[:2000]
                        run.updated_at = now_utc()
                        if run.retry_count >= self.max_retries:
                            run.status = PipelineStatus.PARKED
                            run.current_stage = PipelineStatus.PARKED
                            logger.warning(
                                "流水线 run 搁浅（重试 %d 次）：%s",
                                run.retry_count,
                                run_id,
                            )
                            return
                        run.status = PipelineStatus.FAILED
                    await asyncio.sleep(self.retry_backoff_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("流水线驱动器异常退出：%s", run_id)

    def _existing_job(
        self,
        *,
        job_type: JobType,
        simulation_id: str | None = None,
        render_id: str | None = None,
    ) -> JobRecord | None:
        with self.database.session() as session:
            statement = select(JobRecord).where(JobRecord.job_type == job_type)
            if render_id is not None:
                statement = statement.where(JobRecord.render_id == render_id)
            elif simulation_id is not None:
                statement = statement.where(
                    JobRecord.simulation_id == simulation_id,
                    JobRecord.render_id.is_(None),
                )
            else:
                return None
            return session.scalars(
                statement.order_by(JobRecord.created_at.desc()).limit(1)
            ).first()

    def _complete_run_from_render(self, run_id: str, render_id: str) -> None:
        with self.database.session() as session:
            output = session.scalars(
                select(OutputRecord).where(OutputRecord.render_id == render_id)
            ).first()
        if not output:
            raise PipelineStageFailed("渲染完成但未找到成片记录。")
        self._update_run(
            run_id,
            status=PipelineStatus.COMPLETED,
            current_stage=PipelineStatus.COMPLETED,
            output_id=output.output_id,
        )
        completed = self.get_run(run_id)
        if completed is not None:
            self.selector.mark_story_produced(completed.topic_id)
        logger.info("流水线 run 完成：%s → 成片 %s", run_id, output.output_id)

    async def _execute_attempt(
        self,
        run_id: str,
        *,
        resume_existing: bool = False,
    ) -> None:
        with self.database.session() as session:
            run = session.get(PipelineRunRecord, run_id)
            if not run:
                raise RuntimeError(f"Pipeline run {run_id} disappeared")
            topic = session.get(TopicRecord, run.topic_id)
            if not topic:
                raise RuntimeError(f"Pipeline run {run_id} 的选题不存在")
            symbol = topic.symbol
            market = Market(topic.market)
            # The selected story owns its historical starting point. This is what
            # allows one asset to support several meaningful, non-duplicate cycles.
            buy_date = date.fromisoformat(topic.buy_date)
            amount = topic.amount
            existing_simulation_id = run.simulation_id
            existing_render_id = run.render_id
        policy = self.policy_store.load()

        existing_render_job = (
            self._existing_job(
                job_type=JobType.RENDER,
                render_id=existing_render_id,
            )
            if resume_existing and existing_render_id
            else None
        )
        if existing_render_job is not None:
            self._update_run(
                run_id,
                status=PipelineStatus.RENDERING,
                current_stage=PipelineStatus.RENDERING,
            )
            final_stage = await self._wait_for_render(existing_render_job.job_id)
            if final_stage != JobStage.COMPLETED:
                stored = self.jobs.get_job(existing_render_job.job_id)
                reason = (stored.error_reason if stored else None) or "渲染任务失败。"
                raise PipelineStageFailed(f"渲染阶段失败：{reason}")
            self._complete_run_from_render(run_id, existing_render_id)
            return

        existing_simulation_job = (
            self._existing_job(
                job_type=JobType.SIMULATION,
                simulation_id=existing_simulation_id,
            )
            if resume_existing and existing_simulation_id
            else None
        )
        self._update_run(
            run_id,
            status=PipelineStatus.SIMULATING,
            current_stage=PipelineStatus.SIMULATING,
        )
        if existing_simulation_job is not None:
            job = existing_simulation_job
            simulation_id = existing_simulation_id
        else:
            request = SimulationRequest(
                symbol=symbol,
                buy_date=buy_date,
                end_date="latest",
                initial_capital=amount,
                capital_currency=MARKET_CURRENCY[market],
                video=VideoConfig(
                    duration_seconds=policy.target_duration,
                    voice_enabled=policy.voiceover_enabled,
                    voice=policy.voice,
                ),
            )
            job = self.jobs.create_simulation(request)
            simulation_id = job.simulation_id
            self._update_run(run_id, simulation_id=simulation_id)
            await self.jobs.enqueue(job.job_id, job.priority)
        if simulation_id is None:
            raise PipelineStageFailed("回测任务缺少 simulation_id。")
        final_stage = await self._wait_for_simulation(run_id, job.job_id)
        if final_stage != JobStage.COMPLETED:
            stored = self.jobs.get_job(job.job_id)
            reason = (stored.error_reason if stored else None) or "回测任务失败。"
            raise PipelineStageFailed(f"回测/脚本/配音阶段失败：{reason}")

        self._update_run(
            run_id,
            status=PipelineStatus.RENDERING,
            current_stage=PipelineStatus.RENDERING,
        )
        render_job = self.jobs.create_render(simulation_id)
        self._update_run(run_id, render_id=render_job.render_id)
        await self.jobs.enqueue(render_job.job_id, render_job.priority)
        final_stage = await self._wait_for_render(render_job.job_id)
        if final_stage != JobStage.COMPLETED:
            stored = self.jobs.get_job(render_job.job_id)
            reason = (stored.error_reason if stored else None) or "渲染任务失败。"
            raise PipelineStageFailed(f"渲染阶段失败：{reason}")

        self._complete_run_from_render(run_id, str(render_job.render_id))

    async def _wait_for_simulation(self, run_id: str, job_id: str) -> JobStage:
        while True:
            job = self.jobs.get_job(job_id)
            if not job:
                raise PipelineStageFailed("回测任务记录丢失。")
            stage = JobStage(job.stage)
            mapped = _SIMULATION_STAGE_MAP.get(stage)
            if mapped is not None:
                self._update_run(run_id, status=mapped, current_stage=mapped)
            if stage in (JobStage.COMPLETED, JobStage.FAILED_FINAL, JobStage.CANCELLED):
                if stage == JobStage.CANCELLED:
                    raise PipelineStageFailed("回测任务被取消。")
                return stage
            await asyncio.sleep(self.poll_interval_seconds)

    async def _wait_for_render(self, job_id: str) -> JobStage:
        while True:
            job = self.jobs.get_job(job_id)
            if not job:
                raise PipelineStageFailed("渲染任务记录丢失。")
            stage = JobStage(job.stage)
            if stage in (JobStage.COMPLETED, JobStage.FAILED_FINAL, JobStage.CANCELLED):
                if stage == JobStage.CANCELLED:
                    raise PipelineStageFailed("渲染任务被取消。")
                return stage
            await asyncio.sleep(self.poll_interval_seconds)

    # ---------- 自动模式 ----------

    async def _auto_loop(self) -> None:
        while True:
            await asyncio.sleep(self.auto_loop_interval_seconds)
            try:
                await self._auto_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("自动生产巡检失败")

    async def _auto_tick(self) -> None:
        policy = self.policy_store.load()
        if not policy.enabled:
            return
        if any(not task.done() for task in self._drivers.values()):
            return  # 单发槽位：一条跑完再取下一条

        # 选题池补水位（异步、限频，失败不阻塞配额检查）
        if self.selector.queued_count(policy.markets) < policy.pool_target:
            replenish_running = (
                self._replenish_task is not None and not self._replenish_task.done()
            )
            interval_ok = (
                self._last_replenish_at is None
                or (now_utc() - self._last_replenish_at).total_seconds()
                >= REPLENISH_MIN_INTERVAL_SECONDS
            )
            if not replenish_running and interval_ok:
                self._last_replenish_at = now_utc()
                self._replenish_task = asyncio.create_task(self._replenish_safe(policy))

        summary = self.status_summary()
        if (
            policy.daily_quota is not None
            and int(summary["today_started"]) >= policy.daily_quota
        ):
            return
        topic = self.selector.next_topic(
            policy.markets,
            policy.topic_directive,
            amount=policy.amount,
        )
        if topic is None:
            return
        logger.info("自动生产启动：选题 %s（%s）", topic.symbol, topic.angle)
        self._launch(topic.topic_id)

    async def _replenish_safe(self, policy: PipelinePolicy) -> None:
        try:
            report = await self.selector.replenish(policy)
            logger.info(
                "选题池补充完成：新增 %d，水位 %s",
                len(report["added"]),
                report["pool_size"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("选题池补充失败")
