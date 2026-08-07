from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from stock_video_generator.database import JobRecord


class JobResponse(BaseModel):
    job_id: str
    job_type: str
    stage: str
    progress: float
    priority: int
    created_at: datetime
    updated_at: datetime
    error_type: str | None
    error_reason: str | None
    retry_count: int
    next_retry_at: datetime | None
    input: dict[str, object]
    data_source: str | None
    output_paths: dict[str, str] | None
    simulation_id: str | None
    render_id: str | None
    cancellation_requested: bool

    @classmethod
    def from_record(cls, record: JobRecord) -> JobResponse:
        return cls(
            job_id=record.job_id,
            job_type=record.job_type,
            stage=record.stage,
            progress=record.progress,
            priority=record.priority,
            created_at=record.created_at,
            updated_at=record.updated_at,
            error_type=record.error_type,
            error_reason=record.error_reason,
            retry_count=record.retry_count,
            next_retry_at=record.next_retry_at,
            input=json.loads(record.input_json),
            data_source=record.data_source,
            output_paths=(
                json.loads(record.output_paths_json) if record.output_paths_json else None
            ),
            simulation_id=record.simulation_id,
            render_id=record.render_id,
            cancellation_requested=record.cancellation_requested,
        )


class RenderCreateRequest(BaseModel):
    simulation_id: str
    priority: int = Field(default=100, ge=0, le=1000)


class RetryResponse(BaseModel):
    accepted: bool
    job: JobResponse


class ComponentHealth(BaseModel):
    name: str
    available: bool
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    components: list[ComponentHealth]
