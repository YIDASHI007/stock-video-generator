"""流水线总控离线测试：状态机转移、自动重试与搁浅、配额计数、人工重跑/跳过。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    JobRecord,
    JobStage,
    JobType,
    OutputRecord,
    PipelineRunRecord,
    PipelineStatus,
    TopicRecord,
    TopicStatus,
)
from stock_video_generator.pipeline import (
    PipelineManager,
    PipelinePolicy,
    PolicyStore,
)
from stock_video_generator.topics import ANGLE_SURGE, TopicSelector


@dataclass
class FakeJobHandle:
    job_id: str
    simulation_id: str | None
    render_id: str | None
    priority: int = 100


class FakeJobs:
    """任务机桩：写真实的 JobRecord 表，按脚本推进阶段。"""

    def __init__(self, database: Database, script: list[JobStage]) -> None:
        self.database = database
        self.script = script
        self.created_simulations: list[FakeJobHandle] = []
        self.simulation_requests = []
        self.created_renders: list[FakeJobHandle] = []
        self._cursors: dict[str, int] = {}

    def _new_job(
        self,
        job_type: JobType,
        simulation_id: str | None,
        render_id: str | None,
    ) -> FakeJobHandle:
        handle = FakeJobHandle(
            job_id=str(uuid4()),
            simulation_id=simulation_id,
            render_id=render_id,
        )
        with self.database.session() as session:
            session.add(
                JobRecord(
                    job_id=handle.job_id,
                    job_type=job_type,
                    stage=self.script[0],
                    input_json="{}",
                    simulation_id=simulation_id,
                    render_id=render_id,
                )
            )
        self._cursors[handle.job_id] = 0
        return handle

    def create_simulation(self, request) -> FakeJobHandle:
        handle = self._new_job(JobType.SIMULATION, str(uuid4()), None)
        self.created_simulations.append(handle)
        self.simulation_requests.append(request)
        return handle

    def create_render(self, simulation_id: str) -> FakeJobHandle:
        handle = self._new_job(JobType.RENDER, simulation_id, str(uuid4()))
        self.created_renders.append(handle)
        return handle

    async def enqueue(self, job_id: str, priority: int = 100) -> None:
        return None

    def get_job(self, job_id: str) -> JobRecord | None:
        cursor = self._cursors[job_id]
        stage = self.script[min(cursor, len(self.script) - 1)]
        self._cursors[job_id] = cursor + 1
        with self.database.session() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return None
            job.stage = stage
            if stage == JobStage.FAILED_FINAL:
                job.error_reason = "脚本化失败：模拟行情不可用。"
            if stage == JobStage.COMPLETED and job.render_id:
                # 渲染机完成时写出成片记录（真实环境由 _run_render 负责）
                exists = session.scalars(
                    select(OutputRecord).where(OutputRecord.render_id == job.render_id)
                ).first()
                if not exists:
                    session.add(
                        OutputRecord(
                            output_id=str(uuid4()),
                            render_id=job.render_id,
                            simulation_id=job.simulation_id or "sim",
                            video_path=f"/tmp/{job.render_id}.mp4",
                            validation_path=f"/tmp/{job.render_id}.validation.json",
                        )
                    )
            session.flush()
            session.expunge(job)
            return job


def build_pipeline(
    tmp_path: Path,
    script: list[JobStage],
    *,
    max_retries: int = 3,
) -> tuple[PipelineManager, Database, FakeJobs]:
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    fake_jobs = FakeJobs(database, script)
    selector = TopicSelector(settings, database, market_data=None)
    store = PolicyStore(settings.data_dir / "pipeline_policy.json")
    manager = PipelineManager(
        settings,
        database,
        fake_jobs,
        selector,
        store,
        max_retries=max_retries,
        poll_interval_seconds=0.01,
        retry_backoff_seconds=0.01,
        auto_loop_interval_seconds=999,
    )
    return manager, database, fake_jobs


def add_topic(database: Database, symbol: str = "AAA") -> str:
    topic_id = str(uuid4())
    with database.session() as session:
        session.add(
            TopicRecord(
                topic_id=topic_id,
                symbol=symbol,
                name="测试股",
                market="CN",
                buy_date="2021-01-04",
                amount=1_000_000,
                angle=ANGLE_SURGE,
                drama_score=1.5,
                status=TopicStatus.QUEUED,
            )
        )
    return topic_id


async def await_status(
    database: Database,
    run_id: str,
    statuses: set[str],
    timeout: float = 10.0,
) -> PipelineRunRecord:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        with database.session() as session:
            run = session.get(PipelineRunRecord, run_id)
            if run is not None and run.status in statuses:
                session.expunge(run)
                return run
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} 未在限定时间内进入 {statuses}")


SUCCESS_SCRIPT = [
    JobStage.FETCHING_MARKET_DATA,
    JobStage.SCRIPTING,
    JobStage.VOICING,
    JobStage.COMPLETED,
    JobStage.RENDERING_VIDEO,
    JobStage.COMPLETED,
]

FAILURE_SCRIPT = [JobStage.FAILED_FINAL]


def test_run_once_drives_full_chain_to_completed(tmp_path: Path):
    async def main() -> None:
        manager, database, fake_jobs = build_pipeline(tmp_path, SUCCESS_SCRIPT)
        add_topic(database)
        payload = await manager.run_once()
        run = await await_status(database, payload["run_id"], {PipelineStatus.COMPLETED})

        assert run.output_id
        assert run.simulation_id
        assert run.render_id
        assert len(fake_jobs.created_simulations) == 1
        assert len(fake_jobs.created_renders) == 1
        assert fake_jobs.simulation_requests[0].buy_date == date(2021, 1, 4)
        with database.session() as session:
            topic = session.get(TopicRecord, run.topic_id)
            assert topic.status == TopicStatus.CONSUMED
            assert topic.consumed_at is not None

    asyncio.run(main())


def test_deterministic_failure_parks_after_max_retries(tmp_path: Path):
    async def main() -> None:
        manager, database, fake_jobs = build_pipeline(tmp_path, FAILURE_SCRIPT)
        add_topic(database)
        payload = await manager.run_once()
        run = await await_status(database, payload["run_id"], {PipelineStatus.PARKED})

        assert run.retry_count == 3
        assert "失败" in (run.error or "")
        # 每次重试都新建一条 SIMULATION 任务（断点产物复用由任务机负责）
        assert len(fake_jobs.created_simulations) == 3

    asyncio.run(main())


def test_skip_releases_parked_run(tmp_path: Path):
    async def main() -> None:
        manager, database, _ = build_pipeline(tmp_path, FAILURE_SCRIPT)
        add_topic(database)
        payload = await manager.run_once()
        run = await await_status(database, payload["run_id"], {PipelineStatus.PARKED})
        skipped = manager.skip_run(run.run_id)
        assert skipped.status == PipelineStatus.SKIPPED

    asyncio.run(main())


def test_manual_retry_recovers_parked_run(tmp_path: Path):
    async def main() -> None:
        manager, database, fake_jobs = build_pipeline(tmp_path, FAILURE_SCRIPT)
        add_topic(database)
        payload = await manager.run_once()
        run = await await_status(database, payload["run_id"], {PipelineStatus.PARKED})

        fake_jobs.script = SUCCESS_SCRIPT  # 故障修复后人工重跑
        await manager.retry_run(run.run_id)
        recovered = await await_status(database, run.run_id, {PipelineStatus.COMPLETED})
        assert recovered.output_id

    asyncio.run(main())


def test_quota_counts_started_but_not_parked_or_skipped(tmp_path: Path):
    manager, database, _ = build_pipeline(tmp_path, FAILURE_SCRIPT)
    with database.session() as session:
        for index, status in enumerate(
            [
                PipelineStatus.COMPLETED,
                PipelineStatus.SIMULATING,
                PipelineStatus.PARKED,
                PipelineStatus.SKIPPED,
            ]
        ):
            session.add(
                PipelineRunRecord(
                    run_id=str(uuid4()),
                    topic_id=f"topic-{index}",
                    status=status,
                    current_stage=status,
                )
            )
    summary = manager.status_summary()
    assert summary["today_started"] == 2
    assert summary["today_completed"] == 1
    assert summary["parked_count"] == 1
    assert summary["active_runs"] == 1
    assert summary["enabled"] is False


def test_recover_interrupted_runs_marks_failed_then_parks_at_limit(tmp_path: Path):
    manager, database, _ = build_pipeline(tmp_path, FAILURE_SCRIPT)
    with database.session() as session:
        session.add(
            PipelineRunRecord(
                run_id="run-active",
                topic_id="t1",
                status=PipelineStatus.RENDERING,
                current_stage=PipelineStatus.RENDERING,
                retry_count=0,
            )
        )
        session.add(
            PipelineRunRecord(
                run_id="run-at-limit",
                topic_id="t2",
                status=PipelineStatus.SIMULATING,
                current_stage=PipelineStatus.SIMULATING,
                retry_count=2,
            )
        )
    manager.recover_interrupted_runs()
    with database.session() as session:
        recovered = session.get(PipelineRunRecord, "run-active")
        assert recovered.status == PipelineStatus.FAILED
        assert recovered.retry_count == 1
        assert "重启" in (recovered.error or "")
        parked = session.get(PipelineRunRecord, "run-at-limit")
        assert parked.status == PipelineStatus.PARKED
        assert parked.retry_count == 3


def test_restart_resumes_existing_simulation_without_duplicate_job(tmp_path: Path):
    async def main() -> None:
        manager, database, fake_jobs = build_pipeline(tmp_path, SUCCESS_SCRIPT)
        topic_id = add_topic(database)
        existing_job = fake_jobs.create_simulation(None)
        with database.session() as session:
            topic = session.get(TopicRecord, topic_id)
            topic.status = TopicStatus.CONSUMED
            session.add(
                PipelineRunRecord(
                    run_id="interrupted-run",
                    topic_id=topic_id,
                    status=PipelineStatus.SIMULATING,
                    current_stage=PipelineStatus.SIMULATING,
                    simulation_id=existing_job.simulation_id,
                )
            )

        await manager.start()
        try:
            recovered = await await_status(
                database,
                "interrupted-run",
                {PipelineStatus.COMPLETED},
            )
            assert recovered.output_id
            assert len(fake_jobs.created_simulations) == 1
            assert len(fake_jobs.created_renders) == 1
        finally:
            await manager.stop()

    asyncio.run(main())


def test_restart_resumes_existing_render_without_duplicate_chain(tmp_path: Path):
    async def main() -> None:
        manager, database, fake_jobs = build_pipeline(tmp_path, SUCCESS_SCRIPT)
        topic_id = add_topic(database)
        existing_render = fake_jobs.create_render("existing-simulation")
        with database.session() as session:
            topic = session.get(TopicRecord, topic_id)
            topic.status = TopicStatus.CONSUMED
            session.add(
                PipelineRunRecord(
                    run_id="interrupted-render",
                    topic_id=topic_id,
                    status=PipelineStatus.RENDERING,
                    current_stage=PipelineStatus.RENDERING,
                    simulation_id="existing-simulation",
                    render_id=existing_render.render_id,
                )
            )

        await manager.start()
        try:
            recovered = await await_status(
                database,
                "interrupted-render",
                {PipelineStatus.COMPLETED},
            )
            assert recovered.output_id
            assert len(fake_jobs.created_simulations) == 0
            assert len(fake_jobs.created_renders) == 1
        finally:
            await manager.stop()

    asyncio.run(main())


def test_policy_store_roundtrip_and_validation(tmp_path: Path):
    store = PolicyStore(tmp_path / "pipeline_policy.json")
    assert store.load().enabled is False  # 文件缺失 → 默认策略
    policy = PipelinePolicy(enabled=True, daily_quota=5, angle_weights={"surge": 100})
    store.save(policy)
    loaded = store.load()
    assert loaded.enabled is True
    assert loaded.daily_quota == 5
    assert loaded.angle_weights["surge"] == 100
    assert loaded.angle_weights["crash"] == 0  # 缺省角度补 0


def test_policy_rejects_invalid_weights():
    with pytest.raises(ValidationError):
        PipelinePolicy(angle_weights={"unknown-angle": 10})
    with pytest.raises(ValidationError):
        PipelinePolicy(angle_weights={"surge": 0, "crash": 0, "rollercoaster": 0, "compound": 0})
