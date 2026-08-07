from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from stock_video_generator.artifacts import (
    save_simulation_artifacts,
    write_visualization_spec,
)
from stock_video_generator.config import REMOTION_COMPOSITION_ID, Settings
from stock_video_generator.database import (
    Database,
    JobRecord,
    JobStage,
    JobType,
    OutputRecord,
    PipelineRunRecord,
    RenderRecord,
    SimulationRecord,
    StoryHookHistoryRecord,
    TopicRecord,
    now_utc,
)
from stock_video_generator.errors import (
    DiskSpaceError,
    ProviderUnavailableError,
    RenderError,
    StockVideoError,
)
from stock_video_generator.logging_config import attach_job_log
from stock_video_generator.market_data import MarketDataService
from stock_video_generator.models import SimulationRequest
from stock_video_generator.narration import (
    load_timeline,
    synthesize_narration,
    timeline_audio_missing,
)
from stock_video_generator.publishing import ensure_output_copy, output_copy_path
from stock_video_generator.scripting import (
    generate_script,
    load_script,
    save_script,
    validate_script,
)
from stock_video_generator.simulation import simulate_buy_and_hold
from stock_video_generator.thumbnails import cover_path
from stock_video_generator.tts.base import TTSProvider
from stock_video_generator.validation import validate_market_data
from stock_video_generator.visualization import (
    build_narration_spec,
    build_visualization_spec,
)

logger = logging.getLogger(__name__)

TERMINAL_STAGES = {
    JobStage.COMPLETED,
    JobStage.FAILED_FINAL,
    JobStage.CANCELLED,
}
ACTIVE_STAGES = {
    JobStage.RESOLVING_SYMBOL,
    JobStage.FETCHING_MARKET_DATA,
    JobStage.VALIDATING_DATA,
    JobStage.SIMULATING_PORTFOLIO,
    JobStage.SCRIPTING,
    JobStage.VOICING,
    JobStage.BUILDING_VIDEO_SPEC,
    JobStage.RENDERING_VIDEO,
    JobStage.VALIDATING_OUTPUT,
}


class _JobCancelled(Exception):
    pass


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class JobManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        market_data: MarketDataService,
        tts: TTSProvider | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.market_data = market_data
        self.tts = tts
        self.queue: asyncio.PriorityQueue[tuple[int, float, str]] = asyncio.PriorityQueue()
        self.worker_tasks: list[asyncio.Task[None]] = []
        self.fetch_semaphore = asyncio.Semaphore(settings.fetch_max_concurrency)
        self.simulation_semaphore = asyncio.Semaphore(settings.simulation_max_concurrency)
        self.render_semaphore = asyncio.Semaphore(settings.render_max_concurrency)

    def story_hook_build_options(self, simulation_id: str) -> tuple[set[str], str | None]:
        """Avoid recent templates while preserving an existing simulation's choice."""
        with self.database.session() as session:
            existing = session.scalars(
                select(StoryHookHistoryRecord).where(
                    StoryHookHistoryRecord.simulation_id == simulation_id
                )
            ).first()
            recent = session.scalars(
                select(StoryHookHistoryRecord)
                .order_by(StoryHookHistoryRecord.created_at.desc())
                .limit(8)
            ).all()
        return {item.template_id for item in recent}, (existing.template_id if existing else None)

    def record_story_hook(
        self,
        *,
        simulation_id: str,
        symbol: str,
        template_id: str,
        category: str,
        text: str,
    ) -> None:
        with self.database.session() as session:
            existing = session.scalars(
                select(StoryHookHistoryRecord).where(
                    StoryHookHistoryRecord.simulation_id == simulation_id
                )
            ).first()
            if existing is not None:
                return
            session.add(
                StoryHookHistoryRecord(
                    history_id=str(uuid4()),
                    simulation_id=simulation_id,
                    symbol=symbol,
                    template_id=template_id,
                    category=category,
                    text=text,
                )
            )

    async def start(self) -> None:
        self.recover_interrupted_jobs()
        worker_count = max(
            1,
            self.settings.simulation_max_concurrency
            + self.settings.render_max_concurrency
            + self.settings.tts_max_concurrency,
        )
        self.worker_tasks = [asyncio.create_task(self._worker_loop()) for _ in range(worker_count)]
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(
                    JobRecord.stage.in_([JobStage.CREATED, JobStage.FAILED_RETRYABLE])
                )
            ).all()
            for job in jobs:
                if job.next_retry_at and _as_utc(job.next_retry_at) > now_utc():
                    asyncio.create_task(self._enqueue_after(job.job_id, job.next_retry_at))
                else:
                    await self.enqueue(job.job_id, job.priority)

    async def stop(self) -> None:
        for worker in self.worker_tasks:
            worker.cancel()
        for worker in self.worker_tasks:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self.worker_tasks = []

    def recover_interrupted_jobs(self) -> None:
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(JobRecord.stage.in_(list(ACTIVE_STAGES)))
            ).all()
            for job in jobs:
                job.stage = JobStage.CREATED
                job.error_type = "PROCESS_RESTARTED"
                job.error_reason = "程序重启，任务已从持久化断点重新排队。"
                job.updated_at = now_utc()

    async def enqueue(self, job_id: str, priority: int = 100) -> None:
        await self.queue.put((priority, datetime.now(UTC).timestamp(), job_id))

    async def _enqueue_after(self, job_id: str, when: datetime) -> None:
        delay = max(0, (_as_utc(when) - now_utc()).total_seconds())
        await asyncio.sleep(delay)
        with self.database.session() as session:
            job = session.get(JobRecord, job_id)
            if job and job.stage == JobStage.FAILED_RETRYABLE:
                job.stage = JobStage.CREATED
                job.next_retry_at = None
                priority = job.priority
            else:
                return
        await self.enqueue(job_id, priority)

    def create_simulation(self, request: SimulationRequest, priority: int = 100) -> JobRecord:
        job_id = str(uuid4())
        simulation_id = str(uuid4())
        job = JobRecord(
            job_id=job_id,
            job_type=JobType.SIMULATION,
            stage=JobStage.CREATED,
            progress=0,
            priority=priority,
            input_json=request.model_dump_json(),
            simulation_id=simulation_id,
        )
        simulation = SimulationRecord(
            simulation_id=simulation_id,
            job_id=job_id,
            symbol=request.symbol,
            request_json=request.model_dump_json(),
        )
        with self.database.session() as session:
            session.add_all([job, simulation])
        return job

    def create_render(self, simulation_id: str, priority: int = 100) -> JobRecord:
        with self.database.session() as session:
            simulation = session.get(SimulationRecord, simulation_id)
            if not simulation or not simulation.artifact_paths_json:
                raise ValueError("回测不存在或尚未完成，无法创建渲染任务。")
            paths = json.loads(simulation.artifact_paths_json)
            render_id = str(uuid4())
            job_id = str(uuid4())
            output_path = (self.settings.data_dir / "outputs" / f"{render_id}.mp4").resolve()
            payload = {
                "simulation_id": simulation_id,
                "spec_path": paths["visualization_spec_json"],
                "output_path": str(output_path),
            }
            job = JobRecord(
                job_id=job_id,
                job_type=JobType.RENDER,
                stage=JobStage.CREATED,
                progress=0,
                priority=priority,
                input_json=json.dumps(payload, ensure_ascii=False),
                simulation_id=simulation_id,
                render_id=render_id,
            )
            render = RenderRecord(
                render_id=render_id,
                job_id=job_id,
                simulation_id=simulation_id,
                output_path=str(output_path),
            )
            session.add_all([job, render])
            return job

    def request_cancel(self, job_id: str) -> JobRecord | None:
        with self.database.session() as session:
            job = session.get(JobRecord, job_id)
            if not job:
                return None
            stage = JobStage(job.stage)
            if stage in {
                JobStage.CREATED,
                JobStage.FAILED_RETRYABLE,
                # 失败任务允许“取消”作为人工关闭：直接转为已取消，从待处理列表消失。
                JobStage.FAILED_FINAL,
            }:
                job.cancellation_requested = True
                job.stage = JobStage.CANCELLED
                if stage in {JobStage.CREATED, JobStage.FAILED_RETRYABLE}:
                    job.progress = 0
            elif stage not in TERMINAL_STAGES:
                job.cancellation_requested = True
            return job

    async def retry(self, job_id: str) -> JobRecord | None:
        with self.database.session() as session:
            job = session.get(JobRecord, job_id)
            if not job:
                return None
            if JobStage(job.stage) not in {
                JobStage.FAILED_FINAL,
                JobStage.FAILED_RETRYABLE,
            }:
                return job
            job.stage = JobStage.CREATED
            job.progress = 0
            job.error_type = None
            job.error_reason = None
            job.next_retry_at = None
            job.cancellation_requested = False
            priority = job.priority
        await self.enqueue(job_id, priority)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.database.session() as session:
            return session.get(JobRecord, job_id)

    def list_jobs(self, limit: int = 100) -> list[JobRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(JobRecord).order_by(JobRecord.created_at.desc()).limit(limit)
                ).all()
            )

    def _update(
        self,
        job_id: str,
        *,
        stage: JobStage | None = None,
        progress: float | None = None,
        error_type: str | None = None,
        error_reason: str | None = None,
        output_paths: dict[str, str] | None = None,
        data_source: str | None = None,
    ) -> JobRecord:
        with self.database.session() as session:
            job = session.get(JobRecord, job_id)
            if not job:
                raise RuntimeError(f"Job {job_id} disappeared")
            if stage is not None:
                job.stage = stage
            if progress is not None:
                job.progress = max(0, min(1, progress))
            if error_type is not None:
                job.error_type = error_type
            if error_reason is not None:
                job.error_reason = error_reason
            if output_paths is not None:
                job.output_paths_json = json.dumps(output_paths, ensure_ascii=False)
            if data_source is not None:
                job.data_source = data_source
            job.updated_at = now_utc()
            logger.info(
                "任务状态更新",
                extra={
                    "job_id": job.job_id,
                    "simulation_id": job.simulation_id,
                    "render_id": job.render_id,
                    "provider": job.data_source,
                    "stage": job.stage,
                },
            )
            return job

    def _is_cancelled(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job.cancellation_requested)

    async def _worker_loop(self) -> None:
        while True:
            _, _, job_id = await self.queue.get()
            try:
                job = self.get_job(job_id)
                if not job or JobStage(job.stage) in TERMINAL_STAGES:
                    continue
                if job.job_type == JobType.SIMULATION:
                    async with self.simulation_semaphore:
                        await self._run_simulation(job_id)
                elif job.job_type == JobType.RENDER:
                    async with self.render_semaphore:
                        await self._run_render(job_id)
            except Exception as exc:
                await self._handle_failure(job_id, exc)
            finally:
                self.queue.task_done()

    def _ensure_not_cancelled(self, job_id: str) -> None:
        if self._is_cancelled(job_id):
            self._update(job_id, stage=JobStage.CANCELLED)
            raise _JobCancelled

    async def _run_simulation(self, job_id: str) -> None:
        job_handler = attach_job_log(self.settings.log_dir, job_id, logger)
        try:
            job = self.get_job(job_id)
            if not job:
                return
            request = SimulationRequest.model_validate_json(job.input_json)
            end_date = (
                datetime.now(UTC).date() if request.end_date == "latest" else request.end_date
            )
            provider_name = self.market_data.provider_name_for_symbol(request.symbol)
            provider = self.market_data.providers[provider_name]
            self._update(
                job_id,
                stage=JobStage.RESOLVING_SYMBOL,
                progress=0.05,
                data_source=provider_name,
            )
            instrument = await provider.get_instrument(request.symbol)
            self._ensure_not_cancelled(job_id)

            self._update(
                job_id,
                stage=JobStage.FETCHING_MARKET_DATA,
                progress=0.18,
            )
            async with self.fetch_semaphore:
                try:
                    history_result, actions_result = await asyncio.wait_for(
                        asyncio.gather(
                            self.market_data.get_history(
                                provider,
                                instrument.symbol,
                                request.buy_date,
                                end_date,
                            ),
                            self.market_data.get_actions(
                                provider,
                                instrument.symbol,
                                request.buy_date,
                                end_date,
                            ),
                        ),
                        timeout=self.settings.fetch_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise ProviderUnavailableError(
                        f"行情获取超过 {self.settings.fetch_timeout_seconds} 秒，等待重试。"
                    ) from exc
                bars, history_envelope = history_result
                actions, actions_envelope = actions_result
            self._ensure_not_cancelled(job_id)

            from stock_video_generator.models import SourceMetadata

            history_sources = history_envelope["raw_response_summary"].get(
                "sources",
                [],
            )
            source_provider = (
                " / ".join(str(value) for value in history_sources)
                if provider_name == "global" and history_sources
                else provider_name
            )
            source = SourceMetadata(
                provider=source_provider,
                fetched_at=datetime.fromisoformat(history_envelope["fetched_at"]),
                request_parameters=history_envelope["parameters"],
                cache_key=history_envelope["cache_key"],
                cache_hit=bool(history_envelope.get("cache_hit", False)),
                raw_response_summary={
                    "history": history_envelope["raw_response_summary"],
                    "corporate_actions": actions_envelope["raw_response_summary"],
                },
            )
            self._update(
                job_id,
                stage=JobStage.VALIDATING_DATA,
                progress=0.48,
                data_source=source_provider,
            )
            validation = validate_market_data(
                instrument,
                bars,
                actions,
                requested_start=request.buy_date,
                requested_end=end_date,
                non_trading_day_policy=request.non_trading_day_policy,
            )
            if not validation.valid:
                from stock_video_generator.errors import MarketDataValidationError

                raise MarketDataValidationError(
                    "行情校验失败，已阻止回测和视频生成。",
                    detail="；".join(validation.errors),
                )
            self._ensure_not_cancelled(job_id)

            self._update(
                job_id,
                stage=JobStage.SIMULATING_PORTFOLIO,
                progress=0.58,
            )
            result = simulate_buy_and_hold(
                request=request,
                instrument=instrument,
                bars=bars,
                actions=actions,
                validation=validation,
                source=source,
                simulation_id=job.simulation_id,
            )
            self._ensure_not_cancelled(job_id)
            paths = save_simulation_artifacts(
                self.settings.data_dir / "simulations",
                request=request,
                result=result,
                visualization=None,
                bars=bars,
                actions=actions,
            )
            simulation_dir = Path(paths["directory"])
            script_path = simulation_dir / "script.json"
            timeline_path = simulation_dir / "audio_timeline.json"
            audio_dir = simulation_dir / "audio"

            narration = None
            if request.video.voice_enabled:
                self._update(
                    job_id,
                    stage=JobStage.SCRIPTING,
                    progress=0.66,
                )
                if script_path.is_file():
                    # 断点重跑：已有脚本直接复用，但仍重新过数字对账。
                    script = load_script(script_path)
                    validate_script(script, result)
                else:
                    script = await generate_script(result)
                    save_script(script_path, script)
                paths["script_json"] = str(script_path.resolve())
                self._ensure_not_cancelled(job_id)

                self._update(
                    job_id,
                    stage=JobStage.VOICING,
                    progress=0.74,
                )
                timeline = None
                if timeline_path.is_file():
                    # 断点重跑：时间线与全部音频文件都在才跳过配音。
                    candidate = load_timeline(timeline_path)
                    if not timeline_audio_missing(candidate, audio_dir):
                        timeline = candidate
                if timeline is None:
                    if self.tts is None:
                        from stock_video_generator.errors import (
                            DependencyUnavailableError,
                        )

                        raise DependencyUnavailableError(
                            "未配置 TTS Provider，无法生成配音；"
                            "产品策略是不出无声成片，任务已失败。"
                        )
                    timeline = await synthesize_narration(
                        script,
                        result.simulation_id,
                        audio_dir,
                        self.tts,
                        request.video.voice or self.settings.tts_voice,
                        self.settings.tts_speed,
                        timeline_path,
                    )
                paths["audio_timeline_json"] = str(timeline_path.resolve())
                narration = build_narration_spec(script, timeline, audio_dir)
                self._ensure_not_cancelled(job_id)

            self._update(
                job_id,
                stage=JobStage.BUILDING_VIDEO_SPEC,
                progress=0.86,
            )
            excluded_hook_ids, preferred_hook_id = self.story_hook_build_options(
                result.simulation_id
            )
            visualization = build_visualization_spec(
                result,
                narration=narration,
                excluded_story_hook_template_ids=excluded_hook_ids,
                preferred_story_hook_template_id=preferred_hook_id,
            )
            write_visualization_spec(simulation_dir, visualization)
            with self.database.session() as session:
                simulation = session.get(SimulationRecord, job.simulation_id)
                if simulation:
                    simulation.name = instrument.name
                    simulation.summary_json = result.summary.model_dump_json()
                    simulation.artifact_paths_json = json.dumps(paths, ensure_ascii=False)
            if visualization.story_hook is not None:
                self.record_story_hook(
                    simulation_id=result.simulation_id,
                    symbol=result.instrument.symbol,
                    template_id=visualization.story_hook.template_id,
                    category=visualization.story_hook.category,
                    text=visualization.story_hook.text,
                )
            self._update(
                job_id,
                stage=JobStage.COMPLETED,
                progress=1,
                output_paths=paths,
            )
        finally:
            logger.removeHandler(job_handler)
            job_handler.close()

    def _inject_bgm(self, spec_path: Path) -> Path:
        """渲染前把当前策略的背景音乐写入 spec 副本（换 BGM 无需重回测）。

        策略未配置、文件缺失时原样返回；返回的副本供 render.mjs 使用。
        """
        policy_path = self.settings.data_dir / "pipeline_policy.json"
        bgm_file: str | None = None
        if policy_path.is_file():
            try:
                raw = json.loads(policy_path.read_text(encoding="utf-8"))
                value = raw.get("bgm_file")
                bgm_file = value if isinstance(value, str) and value else None
            except (OSError, json.JSONDecodeError):
                bgm_file = None
        if not bgm_file:
            return spec_path
        source = (self.settings.data_dir / "assets" / "bgm" / bgm_file).resolve()
        if not source.is_file():
            logger.warning("策略配置了背景音乐但文件缺失：%s", source)
            return spec_path
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"可视化规格无法解析：{spec_path}") from exc
        has_narration = bool(spec.get("narration"))
        spec["bgm"] = {
            "file": f"bgm/{spec.get('simulation_id', 'shared')}/{source.name}",
            "source_path": str(source),
            "volume": 0.08 if has_narration else 0.15,
            "fade_out_seconds": 2.0,
        }
        render_spec_path = spec_path.with_name(f"{spec_path.stem}.render.json")
        render_spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return render_spec_path

    async def _run_render(self, job_id: str) -> None:
        job_handler = attach_job_log(self.settings.log_dir, job_id, logger)
        process: asyncio.subprocess.Process | None = None
        try:
            job = self.get_job(job_id)
            if not job:
                return
            node = self.settings.resolve_node_executable()
            if not node:
                from stock_video_generator.errors import DependencyUnavailableError

                raise DependencyUnavailableError(
                    "未找到 Node.js。请安装 Node.js 或设置 NODE_EXECUTABLE。"
                )
            payload = json.loads(job.input_json)
            spec_path = Path(payload["spec_path"]).resolve()
            spec_path = self._inject_bgm(spec_path)
            output_path = Path(payload["output_path"]).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(output_path.parent, os.W_OK):
                raise DiskSpaceError(f"输出目录不可写：{output_path.parent}")
            free_bytes = shutil.disk_usage(output_path.parent).free
            if free_bytes < self.settings.minimum_free_disk_bytes:
                raise DiskSpaceError(
                    "磁盘空间不足，已阻止视频渲染。",
                    detail=(
                        f"可用 {free_bytes / 1024**3:.2f} GB，"
                        f"至少需要 {self.settings.minimum_free_disk_bytes / 1024**3:.2f} GB。"
                    ),
                )
            render_script = (
                self.settings.runtime_dir / "apps" / "renderer" / "scripts" / "render.mjs"
            ).resolve()
            self._update(
                job_id,
                stage=JobStage.RENDERING_VIDEO,
                progress=0.02,
            )
            environment = os.environ.copy()
            environment["NO_COLOR"] = "1"
            process = await asyncio.create_subprocess_exec(
                node,
                str(render_script),
                "--spec",
                str(spec_path),
                "--output",
                str(output_path),
                "--composition",
                REMOTION_COMPOSITION_ID,
                "--concurrency",
                str(self.settings.render_max_concurrency),
                cwd=str(render_script.parent.parent),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            deadline = asyncio.get_running_loop().time() + self.settings.render_timeout_seconds
            while True:
                if asyncio.get_running_loop().time() >= deadline:
                    process.terminate()
                    await process.wait()
                    raise RenderError(
                        f"视频渲染超过 {self.settings.render_timeout_seconds} 秒，已终止。"
                    )
                if self._is_cancelled(job_id):
                    process.terminate()
                    await process.wait()
                    self._update(job_id, stage=JobStage.CANCELLED)
                    return
                try:
                    raw_line = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=1,
                    )
                except TimeoutError:
                    if process.returncode is not None:
                        break
                    continue
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("stage") == "VALIDATING_OUTPUT":
                    self._update(
                        job_id,
                        stage=JobStage.VALIDATING_OUTPUT,
                        progress=float(event.get("progress", 0.96)),
                    )
                elif "progress" in event:
                    self._update(job_id, progress=float(event["progress"]))
            return_code = await process.wait()
            if return_code != 0:
                assert process.stderr is not None
                stderr = (await process.stderr.read()).decode(
                    "utf-8",
                    errors="replace",
                )
                raise RenderError(
                    "Remotion 渲染失败。",
                    detail=stderr[-4000:],
                )
            validation_path = f"{output_path}.validation.json"
            portrait_cover_path = cover_path(
                self.settings,
                str(job.render_id),
                "portrait",
            )
            landscape_cover_path = cover_path(
                self.settings,
                str(job.render_id),
                "landscape",
            )
            if not portrait_cover_path.is_file() or not landscape_cover_path.is_file():
                raise RenderError("视频已生成，但横版或竖版封面缺失。")
            paths = {
                "video": str(output_path),
                "cover_portrait": str(portrait_cover_path),
                "cover_landscape": str(landscape_cover_path),
                "validation_report": validation_path,
            }
            output_id = str(uuid4())
            with self.database.session() as session:
                render = session.get(RenderRecord, job.render_id)
                if render:
                    render.output_path = str(output_path)
                    render.validation_path = validation_path
                simulation = session.get(SimulationRecord, job.simulation_id)
                if simulation is None:
                    raise RenderError("视频已生成，但找不到回测记录，无法生成配套文案。")
                run = session.scalar(
                    select(PipelineRunRecord).where(PipelineRunRecord.render_id == job.render_id)
                )
                topic = session.get(TopicRecord, run.topic_id) if run else None
                ensure_output_copy(
                    self.settings,
                    output_id=output_id,
                    render_id=str(job.render_id),
                    simulation=simulation,
                    angle=topic.angle if topic else "compound",
                )
                paths["copy"] = str(output_copy_path(self.settings, str(job.render_id)))
                session.add(
                    OutputRecord(
                        output_id=output_id,
                        render_id=job.render_id,
                        simulation_id=job.simulation_id,
                        video_path=str(output_path),
                        validation_path=validation_path,
                    )
                )
            self._update(
                job_id,
                stage=JobStage.COMPLETED,
                progress=1,
                output_paths=paths,
            )
        finally:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            logger.removeHandler(job_handler)
            job_handler.close()

    async def _handle_failure(self, job_id: str, exc: Exception) -> None:
        if isinstance(exc, _JobCancelled):
            return
        job = self.get_job(job_id)
        if not job:
            return
        failure_handler = attach_job_log(self.settings.log_dir, job_id, logger)
        logger.exception(
            "任务执行失败",
            extra={
                "job_id": job_id,
                "simulation_id": job.simulation_id,
                "render_id": job.render_id,
                "provider": job.data_source,
                "stage": job.stage,
            },
        )
        logger.removeHandler(failure_handler)
        failure_handler.close()
        retryable = isinstance(exc, (ProviderUnavailableError, RenderError))
        max_retries = 3 if isinstance(exc, ProviderUnavailableError) else 2
        reason = str(exc)
        if isinstance(exc, StockVideoError) and exc.detail:
            reason = f"{exc.message} {exc.detail}"
        if retryable and job.retry_count < max_retries:
            retry_delays = (5, 20, 60)
            delay_seconds = retry_delays[min(job.retry_count, len(retry_delays) - 1)]
            next_retry = now_utc() + timedelta(seconds=delay_seconds)
            with self.database.session() as session:
                stored = session.get(JobRecord, job_id)
                if not stored:
                    return
                stored.stage = JobStage.FAILED_RETRYABLE
                stored.error_type = getattr(exc, "code", type(exc).__name__)
                stored.error_reason = reason
                stored.retry_count += 1
                stored.next_retry_at = next_retry
            asyncio.create_task(self._enqueue_after(job_id, next_retry))
            return
        self._update(
            job_id,
            stage=JobStage.FAILED_FINAL,
            error_type=getattr(exc, "code", type(exc).__name__),
            error_reason=reason,
        )
