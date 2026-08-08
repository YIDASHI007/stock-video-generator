from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from stock_video_generator import __version__
from stock_video_generator.api_models import (
    ComponentHealth,
    HealthResponse,
    JobResponse,
    RenderCreateRequest,
    RetryResponse,
)
from stock_video_generator.config import Settings
from stock_video_generator.config import settings as default_settings
from stock_video_generator.database import (
    Database,
    JobStage,
    OutputRecord,
    PipelineRunRecord,
    PublishAttemptRecord,
    PublishJobRecord,
    RenderRecord,
    SimulationRecord,
    TopicRecord,
)
from stock_video_generator.errors import (
    PipelineConflictError,
    StockVideoError,
    TopicPoolEmptyError,
)
from stock_video_generator.jobs import TERMINAL_STAGES, JobManager
from stock_video_generator.logging_config import configure_logging
from stock_video_generator.market_data import MarketDataService
from stock_video_generator.models import Market, SimulationRequest, SimulationResult
from stock_video_generator.output_retention import OutputRetentionManager
from stock_video_generator.pipeline import (
    PipelineManager,
    PipelinePolicy,
    PolicyStore,
)
from stock_video_generator.publish_batches import (
    PublishBatchCreate,
    PublishBatchManager,
    PublishBatchService,
    PublishBatchUpdate,
)
from stock_video_generator.publish_manager import PublishManager
from stock_video_generator.publishing import (
    PublishAccountCreate,
    PublishingService,
    PublishJobCreate,
    PublishJobUpdate,
    SocialPlatform,
    load_output_copy,
    output_copy_path,
    publish_account_payload,
    publish_job_payload,
)
from stock_video_generator.scripting import generate_script, save_script
from stock_video_generator.thumbnails import (
    cover_path,
    ensure_thumbnail,
    find_ffmpeg,
    find_ffprobe,
    thumbnail_path,
)
from stock_video_generator.topics import TopicDirective, TopicSelector
from stock_video_generator.tts import create_tts_provider
from stock_video_generator.universe import UniverseService
from stock_video_generator.visualization import VisualizationSpec, build_visualization_spec

logger = logging.getLogger(__name__)


def _require_file(path: str | None, label: str) -> Path:
    if not path:
        raise HTTPException(status_code=404, detail=f"{label}尚未生成。")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"{label}文件不存在：{resolved}")
    return resolved


def _local_dt(value: datetime) -> datetime:
    """数据库时间按 UTC 存储，这里转成本地时间用于按天过滤与文件命名。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone()


def _duration_seconds(validation_path: str | None) -> float | None:
    """从渲染验证报告里读 metadata.durationInSeconds，读不到给 None。"""
    if not validation_path:
        return None
    try:
        payload = json.loads(Path(validation_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    metadata = payload.get("metadata")
    value = metadata.get("durationInSeconds") if isinstance(metadata, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def create_app(app_settings: Settings | None = None) -> FastAPI:
    settings = app_settings or default_settings
    settings.ensure_directories()
    configure_logging(settings.log_dir)
    database = Database(settings)
    database.initialize()
    output_retention = OutputRetentionManager(settings, database)
    market_data = MarketDataService(settings)
    tts_provider = create_tts_provider(settings)
    jobs = JobManager(settings, database, market_data, tts=tts_provider)
    universe = UniverseService(settings, database)
    selector = TopicSelector(settings, database, market_data)
    policy_store = PolicyStore(settings.data_dir / "pipeline_policy.json")
    pipeline = PipelineManager(settings, database, jobs, selector, policy_store)
    publishing = PublishingService(settings, database)
    publish_manager = PublishManager(settings, database, publishing)
    publish_batches = PublishBatchService(database, publishing, publish_manager)
    publish_batch_manager = PublishBatchManager(database, publishing, publish_manager)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await universe.start()
        await jobs.start()
        await pipeline.start()
        await publish_manager.start()
        await publish_batch_manager.start()
        await output_retention.start()
        try:
            yield
        finally:
            await output_retention.stop()
            await publish_batch_manager.stop()
            await publish_manager.stop()
            await pipeline.stop()
            await jobs.stop()
            await universe.stop()

    app = FastAPI(
        title="股票历史回测视频生成器 API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.output_retention = output_retention
    app.state.market_data = market_data
    app.state.jobs = jobs
    app.state.pipeline = pipeline
    app.state.universe = universe
    app.state.publishing = publishing
    app.state.publish_manager = publish_manager
    app.state.publish_batches = publish_batches
    app.state.publish_batch_manager = publish_batch_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        # 本地预览端口可能被重映射（5173/7100/其他），放行一切本机回环源。
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(StockVideoError)
    async def stock_video_error_handler(
        _: Request,
        exc: StockVideoError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503 if exc.retryable else 422,
            content={
                "error": exc.code,
                "message": exc.message,
                "detail": exc.detail,
                "retryable": exc.retryable,
            },
        )

    @app.get("/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        return {"status": "ready", "version": app.version}

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        components: list[ComponentHealth] = []
        try:
            with database.session() as session:
                session.execute(text("SELECT 1"))
            components.append(
                ComponentHealth(name="database", available=True, message="SQLite 可用。")
            )
        except Exception as exc:
            components.append(
                ComponentHealth(
                    name="database",
                    available=False,
                    message=f"SQLite 不可用：{exc}",
                )
            )

        node = settings.resolve_node_executable()
        if node:
            try:
                version = subprocess.run(
                    [node, "--version"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                ).stdout.strip()
                components.append(
                    ComponentHealth(
                        name="node",
                        available=True,
                        message=f"Node.js {version}",
                        details={"path": node},
                    )
                )
            except Exception as exc:
                components.append(
                    ComponentHealth(
                        name="node",
                        available=False,
                        message=f"Node.js 检查失败：{exc}",
                    )
                )
        else:
            components.append(
                ComponentHealth(
                    name="node",
                    available=False,
                    message="未找到 Node.js；请设置 NODE_EXECUTABLE。",
                )
            )

        renderer_package = (
            settings.runtime_dir
            / "apps"
            / "renderer"
            / "node_modules"
            / "@remotion"
            / "renderer"
            / "package.json"
        )
        components.append(
            ComponentHealth(
                name="remotion",
                available=renderer_package.is_file(),
                message=(
                    "Remotion 及内置 FFmpeg/FFprobe 组件已安装。"
                    if renderer_package.is_file()
                    else "Remotion 未安装，请运行 pnpm install。"
                ),
            )
        )
        ffmpeg = find_ffmpeg(settings)
        ffprobe = find_ffprobe(settings)
        media_tools_ready = ffmpeg is not None and ffprobe is not None
        components.append(
            ComponentHealth(
                name="ffmpeg",
                available=media_tools_ready,
                message=(
                    "Remotion 内置 FFmpeg/FFprobe 可用，渲染后会继续执行媒体探测。"
                    if media_tools_ready
                    else "未找到 Remotion 内置 FFmpeg/FFprobe，请重新安装或运行 pnpm install。"
                ),
                details={
                    "ffmpeg_path": str(ffmpeg) if ffmpeg else None,
                    "ffprobe_path": str(ffprobe) if ffprobe else None,
                },
            )
        )
        disk = shutil.disk_usage(settings.data_dir)
        components.append(
            ComponentHealth(
                name="disk",
                available=disk.free >= settings.minimum_free_disk_bytes,
                message=f"可用空间 {disk.free / 1024**3:.1f} GB。",
                details={
                    "free_bytes": disk.free,
                    "total_bytes": disk.total,
                },
            )
        )
        tts_health = await tts_provider.health_check()
        components.append(
            ComponentHealth(
                name=tts_health.name,
                available=tts_health.available,
                message=tts_health.message,
            )
        )
        overall = (
            "ok" if all(item.available for item in components if item.name != "tts") else "degraded"
        )
        return HealthResponse(status=overall, components=components)

    @app.get("/api/providers/health")
    async def provider_health() -> list[dict[str, object]]:
        results = await market_data.health()
        return [result.model_dump(mode="json") for result in results]

    @app.get("/api/instruments/search")
    async def search_instruments(
        q: str = Query(min_length=1),
        market: Market | None = None,
    ) -> list[dict[str, object]]:
        results = await market_data.search(q, market)
        return [result.model_dump(mode="json") for result in results]

    @app.get("/api/instruments/{symbol}")
    async def get_instrument(symbol: str) -> dict[str, object]:
        result = await market_data.get_instrument(symbol)
        return result.model_dump(mode="json")

    @app.post(
        "/api/simulations",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_simulation(
        request: SimulationRequest,
        priority: int = Query(default=100, ge=0, le=1000),
    ) -> JobResponse:
        job = jobs.create_simulation(request, priority)
        await jobs.enqueue(job.job_id, job.priority)
        return JobResponse.from_record(job)

    @app.get("/api/simulations/{simulation_id}")
    async def get_simulation(simulation_id: str) -> dict[str, object]:
        with database.session() as session:
            record = session.get(SimulationRecord, simulation_id)
            if not record:
                raise HTTPException(status_code=404, detail="未找到回测任务。")
            response: dict[str, object] = {
                "simulation_id": record.simulation_id,
                "job_id": record.job_id,
                "symbol": record.symbol,
                "name": record.name,
                "created_at": record.created_at,
                "request": json.loads(record.request_json),
                "summary": (json.loads(record.summary_json) if record.summary_json else None),
                "artifacts": (
                    json.loads(record.artifact_paths_json) if record.artifact_paths_json else None
                ),
            }
            if record.artifact_paths_json:
                artifact_paths = json.loads(record.artifact_paths_json)
                simulation_path = _require_file(
                    artifact_paths.get("simulation_json"),
                    "回测 JSON",
                )
                result = json.loads(simulation_path.read_text(encoding="utf-8"))
                response.update(
                    {
                        "instrument": result["instrument"],
                        "source": result["source"],
                        "validation": result["validation"],
                        "events": result["events"],
                        "series": result["series"],
                    }
                )
        return response

    def _simulation_artifacts(simulation_id: str) -> dict[str, str]:
        with database.session() as session:
            record = session.get(SimulationRecord, simulation_id)
            if not record:
                raise HTTPException(status_code=404, detail="未找到回测任务。")
            if not record.artifact_paths_json:
                raise HTTPException(status_code=409, detail="回测尚未完成。")
            return json.loads(record.artifact_paths_json)

    @app.get("/api/simulations/{simulation_id}/series")
    async def get_simulation_series(simulation_id: str) -> dict[str, object]:
        paths = _simulation_artifacts(simulation_id)
        simulation_path = _require_file(paths["simulation_json"], "回测 JSON")
        payload = json.loads(simulation_path.read_text(encoding="utf-8"))
        return {"simulation_id": simulation_id, "series": payload["series"]}

    @app.get("/api/simulations/{simulation_id}/download")
    async def download_simulation(simulation_id: str) -> FileResponse:
        paths = _simulation_artifacts(simulation_id)
        path = _require_file(paths["simulation_json"], "回测 JSON")
        return FileResponse(
            path,
            media_type="application/json",
            filename=f"{simulation_id}-simulation.json",
        )

    @app.get(
        "/api/simulations/{simulation_id}/visualization-spec",
        response_model=VisualizationSpec,
    )
    async def get_visualization_spec(simulation_id: str) -> VisualizationSpec:
        paths = _simulation_artifacts(simulation_id)
        path = _require_file(
            paths["visualization_spec_json"],
            "视频可视化规范",
        )
        return VisualizationSpec.model_validate_json(path.read_text(encoding="utf-8"))

    @app.get("/api/simulations/{simulation_id}/script")
    async def get_script(simulation_id: str) -> dict[str, object]:
        paths = _simulation_artifacts(simulation_id)
        path = _require_file(paths.get("script_json"), "解说脚本")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/simulations/{simulation_id}/script/regenerate")
    async def regenerate_script(simulation_id: str) -> dict[str, object]:
        paths = _simulation_artifacts(simulation_id)
        simulation_path = _require_file(paths["simulation_json"], "回测 JSON")
        result = SimulationResult.model_validate_json(simulation_path.read_text(encoding="utf-8"))
        script = await generate_script(result)
        simulation_dir = Path(paths["directory"])
        script_path = simulation_dir / "script.json"
        save_script(script_path, script)

        # 脚本变了，旧配音与时间线全部作废（下次渲染前必须重新配音）。
        timeline_path = simulation_dir / "audio_timeline.json"
        if timeline_path.is_file():
            timeline_path.unlink()
        audio_dir = simulation_dir / "audio"
        if audio_dir.is_dir():
            shutil.rmtree(audio_dir)
        paths["script_json"] = str(script_path.resolve())
        paths.pop("audio_timeline_json", None)

        # 同步重建可视化规范：去掉已过期的 narration 段。
        excluded_hook_ids, preferred_hook_id = jobs.story_hook_build_options(result.simulation_id)
        spec = build_visualization_spec(
            result,
            narration=None,
            excluded_story_hook_template_ids=excluded_hook_ids,
            preferred_story_hook_template_id=preferred_hook_id,
        )
        temporary = (simulation_dir / "visualization_spec.json").with_suffix(".json.tmp")
        temporary.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(simulation_dir / "visualization_spec.json")
        if spec.story_hook is not None:
            jobs.record_story_hook(
                simulation_id=result.simulation_id,
                symbol=result.instrument.symbol,
                template_id=spec.story_hook.template_id,
                category=spec.story_hook.category,
                text=spec.story_hook.text,
            )

        with database.session() as session:
            record = session.get(SimulationRecord, simulation_id)
            if record:
                record.artifact_paths_json = json.dumps(paths, ensure_ascii=False)
        return json.loads(script_path.read_text(encoding="utf-8"))

    @app.put(
        "/api/simulations/{simulation_id}/visualization-spec",
        response_model=VisualizationSpec,
    )
    async def update_visualization_spec(
        simulation_id: str,
        spec: VisualizationSpec,
    ) -> VisualizationSpec:
        if spec.simulation_id != simulation_id:
            raise HTTPException(
                status_code=422,
                detail="visualization_spec 的 simulation_id 与路径不一致。",
            )
        paths = _simulation_artifacts(simulation_id)
        path = _require_file(paths["visualization_spec_json"], "视频可视化规范")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            spec.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return spec

    @app.post(
        "/api/renders",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_render(request: RenderCreateRequest) -> JobResponse:
        try:
            job = jobs.create_render(request.simulation_id, request.priority)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await jobs.enqueue(job.job_id, job.priority)
        return JobResponse.from_record(job)

    @app.get("/api/renders/{render_id}")
    async def get_render(render_id: str) -> dict[str, object]:
        with database.session() as session:
            render = session.get(RenderRecord, render_id)
            if not render:
                raise HTTPException(status_code=404, detail="未找到渲染任务。")
            job = jobs.get_job(render.job_id)
            return {
                "render_id": render.render_id,
                "simulation_id": render.simulation_id,
                "created_at": render.created_at,
                "output_path": render.output_path,
                "validation_path": render.validation_path,
                "job": JobResponse.from_record(job).model_dump(mode="json") if job else None,
            }

    @app.post("/api/renders/{render_id}/cancel", response_model=JobResponse)
    async def cancel_render(render_id: str) -> JobResponse:
        with database.session() as session:
            render = session.get(RenderRecord, render_id)
            if not render:
                raise HTTPException(status_code=404, detail="未找到渲染任务。")
            job_id = render.job_id
        job = jobs.request_cancel(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到关联任务。")
        return JobResponse.from_record(job)

    @app.post("/api/renders/{render_id}/retry", response_model=RetryResponse)
    async def retry_render(render_id: str) -> RetryResponse:
        with database.session() as session:
            render = session.get(RenderRecord, render_id)
            if not render:
                raise HTTPException(status_code=404, detail="未找到渲染任务。")
            job_id = render.job_id
        job = await jobs.retry(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到关联任务。")
        return RetryResponse(accepted=True, job=JobResponse.from_record(job))

    @app.get("/api/jobs", response_model=list[JobResponse])
    async def list_jobs(limit: int = Query(default=100, ge=1, le=500)) -> list[JobResponse]:
        return [JobResponse.from_record(job) for job in jobs.list_jobs(limit)]

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str) -> JobResponse:
        job = jobs.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到任务。")
        return JobResponse.from_record(job)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobResponse)
    async def cancel_job(job_id: str) -> JobResponse:
        job = jobs.request_cancel(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="未找到任务。")
        return JobResponse.from_record(job)

    @app.post("/api/jobs/{job_id}/retry", response_model=RetryResponse)
    async def retry_job(job_id: str) -> RetryResponse:
        existing = jobs.get_job(job_id)
        if not existing:
            raise HTTPException(status_code=404, detail="未找到任务。")
        if JobStage(existing.stage) not in {
            JobStage.FAILED_FINAL,
            JobStage.FAILED_RETRYABLE,
        }:
            raise HTTPException(status_code=409, detail="只有失败任务可以重试。")
        job = await jobs.retry(job_id)
        assert job is not None
        return RetryResponse(accepted=True, job=JobResponse.from_record(job))

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        if not jobs.get_job(job_id):
            raise HTTPException(status_code=404, detail="未找到任务。")

        async def stream() -> AsyncIterator[str]:
            previous = ""
            while True:
                job = jobs.get_job(job_id)
                if not job:
                    return
                payload = JobResponse.from_record(job).model_dump_json()
                if payload != previous:
                    yield f"event: job\ndata: {payload}\n\n"
                    previous = payload
                if JobStage(job.stage) in TERMINAL_STAGES:
                    return
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    def _output_context(
        outputs: list[OutputRecord],
    ) -> tuple[
        dict[str, SimulationRecord],
        dict[str, PipelineRunRecord],
        dict[str, TopicRecord],
        dict[str, str],
    ]:
        """批量取出成片关联的回测 / 生产任务 / 选题，避免逐条查询。"""
        with database.session() as session:
            sim_ids = {item.simulation_id for item in outputs}
            simulations = (
                session.scalars(
                    select(SimulationRecord).where(SimulationRecord.simulation_id.in_(sim_ids))
                ).all()
                if sim_ids
                else []
            )
            output_ids = {item.output_id for item in outputs}
            runs = (
                session.scalars(
                    select(PipelineRunRecord).where(PipelineRunRecord.output_id.in_(output_ids))
                ).all()
                if output_ids
                else []
            )
            topic_ids = {run.topic_id for run in runs}
            topics = (
                session.scalars(
                    select(TopicRecord).where(TopicRecord.topic_id.in_(topic_ids))
                ).all()
                if topic_ids
                else []
            )
            publish_jobs = (
                session.scalars(
                    select(PublishJobRecord)
                    .where(PublishJobRecord.output_id.in_(output_ids))
                    .order_by(PublishJobRecord.created_at.desc())
                ).all()
                if output_ids
                else []
            )
            publish_stages: dict[str, str] = {}
            for publish_job in publish_jobs:
                # Once an output has been published, keep that fact authoritative even
                # if a newer draft or cancelled retry exists for the same video.
                if publish_job.stage == "PUBLISHED":
                    publish_stages[publish_job.output_id] = publish_job.stage
                else:
                    publish_stages.setdefault(publish_job.output_id, publish_job.stage)
        return (
            {item.simulation_id: item for item in simulations},
            {run.output_id: run for run in runs if run.output_id},
            {topic.topic_id: topic for topic in topics},
            publish_stages,
        )

    def _output_payload(
        item: OutputRecord,
        sim_map: dict[str, SimulationRecord],
        run_map: dict[str, PipelineRunRecord],
        topic_map: dict[str, TopicRecord],
        publish_stages: dict[str, str],
    ) -> dict[str, object]:
        simulation = sim_map.get(item.simulation_id)
        summary = (
            json.loads(simulation.summary_json) if simulation and simulation.summary_json else None
        )
        # 手动创建的回测没有生产任务记录，关联不到选题时 angle/market 给 None。
        run = run_map.get(item.output_id)
        topic = topic_map.get(run.topic_id) if run else None
        try:
            copy = load_output_copy(settings, item.render_id)
        except (OSError, ValueError) as exc:
            copy = None
            logger.warning("成片 %s 的配套文案暂不可用：%s", item.output_id, exc)
        return {
            "output_id": item.output_id,
            "render_id": item.render_id,
            "simulation_id": item.simulation_id,
            "created_at": item.created_at,
            "video_path": item.video_path,
            "validation_path": item.validation_path,
            "cover_portrait_path": (
                str(path)
                if (path := cover_path(settings, item.render_id, "portrait")).is_file()
                else None
            ),
            "cover_landscape_path": (
                str(path)
                if (path := cover_path(settings, item.render_id, "landscape")).is_file()
                else None
            ),
            "symbol": simulation.symbol if simulation else None,
            "name": simulation.name if simulation else None,
            "total_return_pct": (
                summary.get("total_return_pct") if isinstance(summary, dict) else None
            ),
            "duration_seconds": _duration_seconds(item.validation_path),
            "angle": topic.angle if topic else None,
            "market": topic.market if topic else None,
            "publish_stage": publish_stages.get(item.output_id),
            "published": publish_stages.get(item.output_id) == "PUBLISHED",
            "publish_title": copy.title if copy else None,
            "publish_subtitle": copy.subtitle if copy else None,
        }

    @app.get("/api/outputs")
    async def list_outputs(
        limit: int = Query(default=100, ge=1, le=500),
        simulation_id: str | None = None,
    ) -> list[dict[str, object]]:
        with database.session() as session:
            statement = select(OutputRecord)
            if simulation_id:
                statement = statement.where(OutputRecord.simulation_id == simulation_id)
            outputs = list(
                session.scalars(
                    statement.order_by(OutputRecord.created_at.desc()).limit(limit)
                ).all()
            )
        sim_map, run_map, topic_map, publish_stages = _output_context(outputs)
        return [
            _output_payload(item, sim_map, run_map, topic_map, publish_stages) for item in outputs
        ]

    @app.get("/api/outputs/pack")
    async def pack_outputs(
        date: str | None = Query(default=None),
        market: str | None = Query(default=None),
        angle: str | None = Query(default=None),
        pnl: str | None = Query(default=None, pattern="^(win|lose)$"),
        q: str | None = Query(default=None),
    ) -> StreamingResponse:
        """把符合筛选条件的视频与已生成的横竖封面打成 zip 流式下载。"""
        if date is not None:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="date 参数格式应为 YYYY-MM-DD。",
                ) from exc
        with database.session() as session:
            outputs = list(
                session.scalars(select(OutputRecord).order_by(OutputRecord.created_at.desc())).all()
            )
        sim_map, run_map, topic_map, _ = _output_context(outputs)

        matched: list[tuple[OutputRecord, SimulationRecord | None]] = []
        for item in outputs:
            run = run_map.get(item.output_id)
            topic = topic_map.get(run.topic_id) if run else None
            simulation = sim_map.get(item.simulation_id)
            if market and (topic is None or topic.market != market):
                continue
            if angle and (topic is None or topic.angle != angle):
                continue
            if date and _local_dt(item.created_at).date().isoformat() != date:
                continue
            if pnl:
                summary = (
                    json.loads(simulation.summary_json)
                    if simulation and simulation.summary_json
                    else None
                )
                return_pct = summary.get("total_return_pct") if isinstance(summary, dict) else None
                if not isinstance(return_pct, int | float):
                    continue
                if pnl == "win" and return_pct <= 0:
                    continue
                if pnl == "lose" and return_pct >= 0:
                    continue
            if q:
                keyword = q.strip().casefold()
                haystack = (
                    f"{simulation.name or ''} {simulation.symbol}" if simulation else ""
                ).casefold()
                if keyword and keyword not in haystack:
                    continue
            if not Path(item.video_path).is_file():
                continue
            matched.append((item, simulation))
        if not matched:
            raise HTTPException(
                status_code=404,
                detail="没有符合筛选条件的成片可以打包。",
            )

        # mp4 本身已压缩，zip 用 STORED 仅做打包不再压一遍。
        buffer = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        used_names: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            for item, simulation in matched:
                base = (
                    (simulation.name or simulation.symbol) if simulation else None
                ) or item.output_id[:8]
                safe = re.sub(r'[\\/:*?"<>|]+', "_", base).strip() or item.output_id[:8]
                stamp = _local_dt(item.created_at).strftime("%m%d-%H%M")
                entry = f"{safe}_{stamp}.mp4"
                if entry in used_names:
                    entry = f"{safe}_{stamp}_{item.output_id[:6]}.mp4"
                used_names.add(entry)
                archive.write(item.video_path, arcname=entry)
                entry_stem = entry.removesuffix(".mp4")
                for variant, label in (
                    ("landscape", "横版封面"),
                    ("portrait", "竖版封面"),
                ):
                    cover = cover_path(settings, item.render_id, variant)
                    if cover.is_file() and cover.stat().st_size > 0:
                        archive.write(
                            cover,
                            arcname=f"{entry_stem}_{label}.png",
                        )
        buffer.seek(0)

        def stream() -> Iterator[bytes]:
            try:
                while True:
                    chunk = buffer.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                buffer.close()

        return StreamingResponse(
            stream(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="videos-pack.zip"',
            },
        )

    @app.get("/api/outputs/{output_id}")
    async def get_output(output_id: str) -> dict[str, object]:
        with database.session() as session:
            output = session.get(OutputRecord, output_id)
            if not output:
                raise HTTPException(status_code=404, detail="未找到视频输出。")
            return {
                "output_id": output.output_id,
                "render_id": output.render_id,
                "simulation_id": output.simulation_id,
                "created_at": output.created_at,
                "video_path": output.video_path,
                "cover_portrait_path": (
                    str(path)
                    if (path := cover_path(settings, output.render_id, "portrait")).is_file()
                    else None
                ),
                "cover_landscape_path": (
                    str(path)
                    if (path := cover_path(settings, output.render_id, "landscape")).is_file()
                    else None
                ),
                "validation": json.loads(
                    _require_file(
                        output.validation_path,
                        "视频验证报告",
                    ).read_text(encoding="utf-8")
                ),
            }

    @app.get("/api/outputs/{output_id}/video")
    async def get_output_video(output_id: str) -> FileResponse:
        with database.session() as session:
            output = session.get(OutputRecord, output_id)
            if not output:
                raise HTTPException(status_code=404, detail="未找到视频输出。")
            path = _require_file(output.video_path, "视频")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.get("/api/outputs/{output_id}/thumbnail")
    async def get_output_thumbnail(output_id: str) -> FileResponse:
        with database.session() as session:
            output = session.get(OutputRecord, output_id)
            if not output:
                raise HTTPException(status_code=404, detail="未找到视频输出。")
            render_id = output.render_id
            video_path = output.video_path
        # 文件不存在时按需懒生成一次；仍失败则 404，由前端显示占位块。
        path = ensure_thumbnail(settings, render_id, video_path)
        if path is None:
            raise HTTPException(status_code=404, detail="缩略图尚未生成。")
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/api/outputs/{output_id}/cover/{variant}")
    async def get_output_cover(output_id: str, variant: str) -> FileResponse:
        """Download a separately rendered Douyin cover."""
        if variant not in {"portrait", "landscape"}:
            raise HTTPException(
                status_code=422,
                detail="封面类型只能是 portrait 或 landscape。",
            )
        with database.session() as session:
            output = session.get(OutputRecord, output_id)
            if not output:
                raise HTTPException(status_code=404, detail="未找到视频输出。")
            path = cover_path(settings, output.render_id, variant)
        if not path.is_file() or path.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="封面尚未生成。")
        return FileResponse(path, media_type="image/png", filename=path.name)

    @app.delete("/api/outputs/{output_id}")
    async def delete_output(output_id: str) -> dict[str, object]:
        """删除成片：视频、双封面、校验报告与旧缩略图一并真实删除。"""
        with database.session() as session:
            output = session.get(OutputRecord, output_id)
            if not output:
                raise HTTPException(status_code=404, detail="未找到视频输出。")
            publish_job = session.scalar(
                select(PublishJobRecord).where(PublishJobRecord.output_id == output_id)
            )
            if publish_job is not None:
                raise HTTPException(
                    status_code=409,
                    detail="该成片已被发布任务引用，请保留成片并在发布中心处理任务。",
                )
            candidates = [
                output.video_path,
                output.validation_path,
                str(thumbnail_path(settings, output.render_id)),
                str(cover_path(settings, output.render_id, "portrait")),
                str(cover_path(settings, output.render_id, "landscape")),
                str(output_copy_path(settings, output.render_id)),
            ]
            # 引用该成片的流水线记录置空，避免悬空引用。
            for run in session.scalars(
                select(PipelineRunRecord).where(PipelineRunRecord.output_id == output_id)
            ).all():
                run.output_id = None
            session.delete(output)
        removed: list[str] = []
        for raw in candidates:
            if not raw:
                continue
            path = Path(raw)
            try:
                path.unlink(missing_ok=True)
                removed.append(path.name)
            except OSError as exc:
                logger.warning("删除成片文件失败 %s：%s", path, exc)
        return {"deleted": True, "removed_files": removed}

    @app.post("/api/outputs/{output_id}/open-folder")
    async def open_output_folder(output_id: str) -> dict[str, object]:
        with database.session() as session:
            output = session.get(OutputRecord, output_id)
            if not output:
                raise HTTPException(status_code=404, detail="未找到视频输出。")
            path = _require_file(output.video_path, "视频")
        if os.name != "nt":
            raise HTTPException(status_code=501, detail="打开文件夹按钮当前仅支持 Windows。")
        subprocess.Popen(
            ["explorer.exe", f"/select,{path}"],
            close_fds=True,
        )
        return {"opened": True, "path": str(path)}

    # ---------- 自动生产总控 ----------

    # ---------- 社交账号与发布中心 ----------

    @app.get("/api/accounts")
    async def list_social_accounts(
        platform: SocialPlatform | None = None,
    ) -> list[dict[str, object]]:
        return [
            publish_account_payload(item)
            for item in publishing.list_accounts(platform=platform)
        ]

    @app.post("/api/accounts", status_code=status.HTTP_201_CREATED)
    async def create_social_account(request: PublishAccountCreate) -> dict[str, object]:
        try:
            return publish_account_payload(publishing.save_account(request))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/accounts/{account_id}/login")
    async def get_social_account_login(account_id: str) -> dict[str, object]:
        try:
            return publish_manager.login_status(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc

    @app.post(
        "/api/accounts/{account_id}/login",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def login_social_account(account_id: str) -> dict[str, object]:
        try:
            return publish_manager.start_login(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/accounts/{account_id}/login/qr")
    async def get_social_account_login_qr(account_id: str) -> FileResponse:
        try:
            path = publish_manager.login_qr_path(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.post("/api/accounts/{account_id}/login/cancel")
    async def cancel_social_account_login(account_id: str) -> dict[str, object]:
        try:
            return publish_manager.cancel_login(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc

    @app.post("/api/accounts/{account_id}/check")
    async def check_social_account(account_id: str) -> dict[str, object]:
        try:
            return await publish_manager.check_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/accounts/{account_id}/unbind")
    async def unbind_social_account(account_id: str) -> dict[str, object]:
        try:
            return publish_account_payload(publish_manager.unbind_account(account_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/accounts/{account_id}")
    async def delete_social_account(account_id: str) -> dict[str, object]:
        try:
            publish_manager.delete_account(account_id)
            return {"deleted": True, "account_id": account_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到社交账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/publish/accounts")
    async def list_publish_accounts() -> list[dict[str, object]]:
        return [
            publish_account_payload(item)
            for item in publishing.list_accounts(platform="douyin")
        ]

    @app.post("/api/publish/accounts", status_code=status.HTTP_201_CREATED)
    async def create_publish_account(
        request: PublishAccountCreate,
    ) -> dict[str, object]:
        if request.platform != "douyin":
            raise HTTPException(status_code=409, detail="发布中心当前只接受抖音账号")
        try:
            return publish_account_payload(publishing.save_account(request))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/publish/accounts/{account_id}/login")
    async def get_publish_account_login(account_id: str) -> dict[str, object]:
        try:
            return publish_manager.login_status(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布账号") from exc

    @app.post(
        "/api/publish/accounts/{account_id}/login",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def login_publish_account(account_id: str) -> dict[str, object]:
        try:
            return publish_manager.start_login(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/publish/jobs")
    async def list_publish_jobs(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, object]]:
        return [publish_job_payload(item) for item in publishing.list_jobs(limit)]

    @app.get("/api/publish/batches")
    async def list_publish_batches(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, object]]:
        return publish_batches.list(limit)

    @app.post("/api/publish/batches", status_code=status.HTTP_201_CREATED)
    async def create_publish_batch(
        request: PublishBatchCreate,
    ) -> dict[str, object]:
        try:
            batch = publish_batches.create(request)
            return publish_batches.payload(batch.batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到可用的抖音账号") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/publish/batches/{batch_id}")
    async def get_publish_batch(batch_id: str) -> dict[str, object]:
        try:
            return publish_batches.payload(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到批量发布任务") from exc

    @app.patch("/api/publish/batches/{batch_id}")
    async def update_publish_batch(
        batch_id: str,
        request: PublishBatchUpdate,
    ) -> dict[str, object]:
        try:
            return publish_batches.update(batch_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到批量发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/batches/{batch_id}/approve-start")
    async def approve_start_publish_batch(batch_id: str) -> dict[str, object]:
        try:
            return publish_batches.approve_start(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到批量发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/batches/{batch_id}/pause")
    async def pause_publish_batch(batch_id: str) -> dict[str, object]:
        try:
            return publish_batches.pause(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到批量发布任务") from exc

    @app.post("/api/publish/batches/{batch_id}/resume")
    async def resume_publish_batch(batch_id: str) -> dict[str, object]:
        try:
            return publish_batches.resume(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到批量发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/batches/{batch_id}/cancel")
    async def cancel_publish_batch(batch_id: str) -> dict[str, object]:
        try:
            return publish_batches.cancel(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到批量发布任务") from exc

    @app.post("/api/publish/jobs", status_code=status.HTTP_201_CREATED)
    async def create_publish_job(request: PublishJobCreate) -> dict[str, object]:
        try:
            return publish_job_payload(publishing.create_job(request))
        except KeyError as exc:
            labels = {
                "output": "未找到视频成片",
                "account": "未找到可用的抖音账号",
                "simulation": "未找到对应回测结果",
            }
            raise HTTPException(
                status_code=404,
                detail=labels.get(str(exc.args[0]), "创建发布任务所需数据不存在"),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/publish/jobs/{publish_id}")
    async def get_publish_job(publish_id: str) -> dict[str, object]:
        record = publishing.get_job(publish_id)
        if record is None:
            raise HTTPException(status_code=404, detail="未找到发布任务")
        payload = publish_job_payload(record)
        payload["manifest"] = publishing.load_manifest(record).model_dump(mode="json")
        payload["attempts"] = publish_manager.attempts(publish_id)
        return payload

    @app.patch("/api/publish/jobs/{publish_id}")
    async def update_publish_job(
        publish_id: str,
        request: PublishJobUpdate,
    ) -> dict[str, object]:
        try:
            return publish_job_payload(publishing.update_job(publish_id, request))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/jobs/{publish_id}/run", status_code=status.HTTP_202_ACCEPTED)
    async def run_publish_job(publish_id: str) -> dict[str, object]:
        try:
            return publish_job_payload(publish_manager.enqueue(publish_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/jobs/{publish_id}/approve")
    async def approve_publish_job(publish_id: str) -> dict[str, object]:
        try:
            return publish_job_payload(publishing.approve(publish_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/jobs/{publish_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    async def retry_publish_job(publish_id: str) -> dict[str, object]:
        try:
            return publish_job_payload(publish_manager.retry(publish_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/publish/jobs/{publish_id}/cancel")
    async def cancel_publish_job(publish_id: str) -> dict[str, object]:
        try:
            return publish_job_payload(publish_manager.cancel(publish_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到发布任务") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/publish/attempts/{attempt_id}/evidence/{kind}")
    async def get_publish_evidence(attempt_id: str, kind: str) -> FileResponse:
        if kind not in {"screenshot", "dom", "actions"}:
            raise HTTPException(status_code=422, detail="不支持的证据类型")
        with database.session() as session:
            attempt = session.get(PublishAttemptRecord, attempt_id)
            if attempt is None:
                raise HTTPException(status_code=404, detail="未找到发布尝试")
            raw = {
                "screenshot": attempt.screenshot_path,
                "dom": attempt.dom_snapshot_path,
                "actions": attempt.action_log_path,
            }[kind]
        path = _require_file(raw, "发布证据")
        publish_root = (settings.data_dir / "publishes").resolve()
        if publish_root not in path.parents:
            raise HTTPException(status_code=403, detail="证据文件路径越界")
        media_type = {
            "screenshot": "image/png",
            "dom": "text/html; charset=utf-8",
            "actions": "application/json",
        }[kind]
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/api/pipeline/status")
    async def pipeline_status() -> dict[str, object]:
        return pipeline.status_summary()

    @app.get("/api/universe/status")
    async def universe_status() -> dict[str, object]:
        return universe.status()

    @app.post("/api/universe/sync")
    async def universe_sync() -> dict[str, object]:
        result = await universe.sync()
        if result.get("sync_status") in {"completed", "partial"}:
            result["replenish"] = await selector.replenish(policy_store.load())
        return result

    @app.get("/api/pipeline/runs")
    async def pipeline_runs(
        filter: str = Query(default="all", pattern="^(all|active|parked)$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, object]]:
        return pipeline.list_runs(filter, limit)

    @app.get("/api/pipeline/policy", response_model=PipelinePolicy)
    async def get_pipeline_policy() -> PipelinePolicy:
        return policy_store.load()

    @app.put("/api/pipeline/policy", response_model=PipelinePolicy)
    async def put_pipeline_policy(policy: PipelinePolicy) -> PipelinePolicy:
        previous = policy_store.load()
        policy_store.save(policy)
        selection_changed = (
            previous.markets != policy.markets
            or previous.topic_directive != policy.topic_directive
            or previous.angle_weights != policy.angle_weights
        )
        queue_report = selector.refresh_queue_for_policy(
            policy,
            reset_all=selection_changed,
        )
        if selection_changed or queue_report["rejected"] or queue_report["added"]:
            logger.info(
                "策略保存后同步选题池：重建=%s，清退 %s，补入 %s，水位 %s",
                selection_changed,
                queue_report["rejected"],
                len(queue_report["added"]),
                queue_report["pool_size"],
            )
        return policy_store.load()

    BGM_ALLOWED_SUFFIXES = {".mp3", ".wav", ".m4a", ".ogg"}
    BGM_MAX_BYTES = 20 * 1024 * 1024

    def _bgm_dir() -> Path:
        directory = settings.data_dir / "assets" / "bgm"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _bgm_safe_name(original: str) -> str:
        """保留可读文件名，但只留 ASCII 安全字符并加短前缀防重名。"""
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original).stem).strip("._")
        suffix = Path(original).suffix.lower()
        return f"{uuid4().hex[:8]}_{stem or 'bgm'}{suffix}"

    def _bgm_resolve(name: str | None) -> Path | None:
        """按文件名定位音乐文件；拒绝路径穿越，仅允许 bgm 目录内文件。"""
        if not name:
            return None
        path = (_bgm_dir() / Path(name).name).resolve()
        if path.parent != _bgm_dir().resolve() or not path.is_file():
            return None
        return path

    @app.get("/api/settings/bgm/list")
    async def list_bgm() -> list[dict[str, object]]:
        """列出已上传的背景音乐文件（供工作台选择）。"""
        directory = _bgm_dir()
        return [
            {"file": path.name, "size_bytes": path.stat().st_size}
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in BGM_ALLOWED_SUFFIXES
        ]

    @app.get("/api/settings/bgm")
    async def get_bgm(file: str | None = Query(default=None)) -> FileResponse:
        """试听背景音乐：默认当前策略选用的，也可用 ?file= 指定任意已上传文件。"""
        name = file or policy_store.load().bgm_file
        if not name:
            raise HTTPException(status_code=404, detail="尚未设置背景音乐。")
        path = _bgm_resolve(name)
        if path is None:
            raise HTTPException(status_code=404, detail="背景音乐文件缺失。")
        return FileResponse(path, filename=path.name)

    @app.post("/api/settings/bgm", response_model=PipelinePolicy)
    async def upload_bgm(file: UploadFile) -> PipelinePolicy:
        """上传背景音乐（mp3/wav/m4a/ogg，≤20MB），上传后自动设为当前选用的。"""
        original = Path(file.filename or "bgm").name
        suffix = Path(original).suffix.lower()
        if suffix not in BGM_ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"仅支持 {sorted(BGM_ALLOWED_SUFFIXES)} 格式。",
            )
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="文件为空。")
        if len(content) > BGM_MAX_BYTES:
            raise HTTPException(status_code=400, detail="文件超过 20MB 上限。")
        safe_name = _bgm_safe_name(original)
        (_bgm_dir() / safe_name).write_bytes(content)
        policy = policy_store.load()
        policy.bgm_file = safe_name
        policy_store.save(policy)
        return policy

    @app.delete("/api/settings/bgm", response_model=PipelinePolicy)
    async def delete_bgm() -> PipelinePolicy:
        policy = policy_store.load()
        policy.bgm_file = None
        policy_store.save(policy)
        return policy

    @app.post("/api/pipeline/run-once", status_code=status.HTTP_202_ACCEPTED)
    async def pipeline_run_once() -> dict[str, object]:
        try:
            return await pipeline.run_once()
        except TopicPoolEmptyError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"{exc.message} {exc.detail or ''}".strip(),
            ) from exc
        except PipelineConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
        except StockVideoError:
            raise

    @app.post("/api/pipeline/runs/{run_id}/retry")
    async def pipeline_retry_run(run_id: str) -> dict[str, object]:
        try:
            run = await pipeline.retry_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到生产任务。") from exc
        except PipelineConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
        return pipeline.run_payload(run, None)

    @app.post("/api/pipeline/runs/{run_id}/skip")
    async def pipeline_skip_run(run_id: str) -> dict[str, object]:
        try:
            run = pipeline.skip_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到生产任务。") from exc
        except PipelineConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
        return pipeline.run_payload(run, None)

    @app.get("/api/pipeline/topics")
    async def pipeline_topics(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, object]]:
        return [
            {
                "topic_id": topic.topic_id,
                "symbol": topic.symbol,
                "name": topic.name,
                "market": topic.market,
                "buy_date": topic.buy_date,
                "amount": topic.amount,
                "angle": topic.angle,
                "drama_score": topic.drama_score,
                "status": topic.status,
                "created_at": topic.created_at,
            }
            for topic in selector.list_topics(limit)
        ]

    @app.post("/api/pipeline/topics/replenish")
    async def pipeline_replenish_topics() -> dict[str, object]:
        policy = policy_store.load()
        return await selector.replenish(policy)

    @app.get("/api/pipeline/story-pool")
    async def pipeline_story_pool(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        candidates = selector.list_story_candidates(limit)
        return {
            "summary": selector.story_pool_status(),
            "candidates": [
                {
                    "story_id": item.story_id,
                    "story_key": item.story_key,
                    "symbol": item.symbol,
                    "name": item.name,
                    "market": item.market,
                    "buy_date": item.buy_date,
                    "end_date": item.end_date,
                    "story_type": item.story_type,
                    "angle": item.angle,
                    "hold_years": item.hold_years,
                    "forward_return_pct": item.forward_return_pct,
                    "max_drawdown_pct": item.max_drawdown_pct,
                    "quality_score": item.quality_score,
                    "content_score": item.content_score,
                    "status": item.status,
                    "rejection_reason": item.rejection_reason,
                    "topic_id": item.topic_id,
                }
                for item in candidates
            ],
        }

    @app.post("/api/pipeline/story-pool/refresh")
    async def pipeline_refresh_story_pool() -> dict[str, object]:
        return await selector.refresh_story_pool(policy_store.load())

    @app.post("/api/topics/preview-count")
    async def topics_preview_count(
        directive: TopicDirective,
        markets: list[Market] | None = Query(default=None),  # noqa: B008
    ) -> dict[str, object]:
        """预览当前选题偏好能命中多少只股票（不写库、不限水位）。"""
        policy = policy_store.load()
        return await selector.preview(
            directive,
            markets or policy.markets,
            policy.angle_weights,
        )

    web_dist_dir = settings.resolved_web_dist_dir
    if web_dist_dir.is_dir() and (web_dist_dir / "index.html").is_file():
        assets_dir = web_dist_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

        @app.get("/", include_in_schema=False)
        async def web_index() -> FileResponse:
            return FileResponse(web_dist_dir / "index.html")

        @app.get("/{web_path:path}", include_in_schema=False)
        async def web_fallback(web_path: str) -> FileResponse:
            candidate = (web_dist_dir / web_path).resolve()
            try:
                candidate.relative_to(web_dist_dir)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Not found") from exc
            if candidate.is_file():
                return FileResponse(candidate)
            if web_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            return FileResponse(web_dist_dir / "index.html")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "stock_video_generator.main:app",
        host=default_settings.host,
        port=default_settings.port,
        reload=False,
    )
