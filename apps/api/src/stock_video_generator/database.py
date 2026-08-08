from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from stock_video_generator.config import Settings


class JobStage(StrEnum):
    CREATED = "CREATED"
    RESOLVING_SYMBOL = "RESOLVING_SYMBOL"
    FETCHING_MARKET_DATA = "FETCHING_MARKET_DATA"
    VALIDATING_DATA = "VALIDATING_DATA"
    SIMULATING_PORTFOLIO = "SIMULATING_PORTFOLIO"
    SCRIPTING = "SCRIPTING"
    VOICING = "VOICING"
    BUILDING_VIDEO_SPEC = "BUILDING_VIDEO_SPEC"
    RENDERING_VIDEO = "RENDERING_VIDEO"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


class JobType(StrEnum):
    SIMULATION = "SIMULATION"
    RENDER = "RENDER"
    TTS = "TTS"


class TopicStatus(StrEnum):
    QUEUED = "queued"
    CONSUMED = "consumed"
    REJECTED = "rejected"


class StoryCandidateStatus(StrEnum):
    READY = "ready"
    QUEUED = "queued"
    PRODUCED = "produced"
    REJECTED = "rejected"


class PipelineStatus(StrEnum):
    TOPIC_QUEUED = "TOPIC_QUEUED"
    SIMULATING = "SIMULATING"
    SCRIPTING = "SCRIPTING"
    VOICING = "VOICING"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARKED = "PARKED"
    SKIPPED = "SKIPPED"


class PublishStage(StrEnum):
    CREATED = "PUBLISH_CREATED"
    VALIDATING_ARTIFACTS = "VALIDATING_ARTIFACTS"
    CHECKING_LOGIN = "CHECKING_LOGIN"
    OPENING_UPLOAD_PAGE = "OPENING_UPLOAD_PAGE"
    UPLOADING_VIDEO = "UPLOADING_VIDEO"
    WAITING_TRANSCODE = "WAITING_TRANSCODE"
    FILLING_TITLE = "FILLING_TITLE"
    FILLING_DESCRIPTION = "FILLING_DESCRIPTION"
    ADDING_TOPICS = "ADDING_TOPICS"
    SETTING_LANDSCAPE_COVER = "SETTING_LANDSCAPE_COVER"
    SETTING_PORTRAIT_COVER = "SETTING_PORTRAIT_COVER"
    SETTING_COLLECTION = "SETTING_COLLECTION"
    SETTING_DECLARATION = "SETTING_DECLARATION"
    VALIDATING_PREVIEW = "VALIDATING_PREVIEW"
    READY_FOR_PUBLISH = "READY_FOR_PUBLISH"
    PUBLISHING = "PUBLISHING"
    VERIFYING_RESULT = "VERIFYING_RESULT"
    PUBLISHED = "PUBLISHED"
    NEEDS_LOGIN = "NEEDS_LOGIN"
    NEEDS_SMS = "NEEDS_SMS"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


class PublishBatchStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_INTERVAL = "WAITING_INTERVAL"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILED = "PARTIAL_FAILED"
    CANCELLED = "CANCELLED"


class PublishBatchItemStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


PIPELINE_ACTIVE_STATUSES = {
    PipelineStatus.TOPIC_QUEUED,
    PipelineStatus.SIMULATING,
    PipelineStatus.SCRIPTING,
    PipelineStatus.VOICING,
    PipelineStatus.RENDERING,
}


class Base(DeclarativeBase):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC)


class JobRecord(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(24), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True, default=JobStage.CREATED)
    progress: Mapped[float] = mapped_column(Float, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    input_json: Mapped[str] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    output_paths_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    render_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class SimulationRecord(Base):
    __tablename__ = "simulations"

    simulation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    request_json: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_paths_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RenderRecord(Base):
    __tablename__ = "renders"

    render_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    simulation_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class OutputRecord(Base):
    __tablename__ = "outputs"

    output_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    render_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    simulation_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    video_path: Mapped[str] = mapped_column(Text)
    validation_path: Mapped[str] = mapped_column(Text)


class PublishAccountRecord(Base):
    __tablename__ = "publish_accounts"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(24), default="douyin", index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    browser_profile_dir: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PublishJobRecord(Base):
    __tablename__ = "publish_jobs"

    publish_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    output_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(
        String(48),
        default=PublishStage.CREATED,
        index=True,
    )
    progress: Mapped[float] = mapped_column(Float, default=0)
    mode: Mapped[str] = mapped_column(String(24), default="dry_run")
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    manifest_path: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    topics_json: Mapped[str] = mapped_column(Text, default="[]")
    collection_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    declaration: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    agent_fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_item_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    published_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class PublishBatchRecord(Base):
    __tablename__ = "publish_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default=PublishBatchStatus.READY,
        index=True,
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, default=600)
    failure_policy: Mapped[str] = mapped_column(String(16), default="pause")
    start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class PublishBatchItemRecord(Base):
    __tablename__ = "publish_batch_items"

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), index=True)
    output_id: Mapped[str] = mapped_column(String(36), index=True)
    publish_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24),
        default=PublishBatchItemStatus.PENDING,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class PublishAttemptRecord(Base):
    __tablename__ = "publish_attempts"

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    publish_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    stage: Mapped[str] = mapped_column(String(48))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    used_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    dom_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_log_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class PublishTitleHistoryRecord(Base):
    __tablename__ = "publish_title_history"

    history_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    publish_id: Mapped[str] = mapped_column(String(36), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    template_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(120))
    normalized_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class StoryHookHistoryRecord(Base):
    __tablename__ = "story_hook_history"

    history_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TopicRecord(Base):
    __tablename__ = "topics"

    topic_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))
    market: Mapped[str] = mapped_column(String(8))
    buy_date: Mapped[str] = mapped_column(String(10))
    amount: Mapped[float] = mapped_column(Float)
    angle: Mapped[str] = mapped_column(String(24))
    drama_score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default=TopicStatus.QUEUED, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class StoryCandidateRecord(Base):
    """A durable, data-checked long-horizon story that can become a topic."""

    __tablename__ = "story_candidates"

    story_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    story_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(240))
    market: Mapped[str] = mapped_column(String(12), index=True)
    buy_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    story_type: Mapped[str] = mapped_column(String(32), index=True)
    angle: Mapped[str] = mapped_column(String(24), index=True)
    hold_years: Mapped[float] = mapped_column(Float)
    start_price: Mapped[float] = mapped_column(Float)
    end_price: Mapped[float] = mapped_column(Float)
    forward_return_pct: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    quality_score: Mapped[float] = mapped_column(Float)
    content_score: Mapped[float] = mapped_column(Float, index=True)
    status: Mapped[str] = mapped_column(String(16), default=StoryCandidateStatus.READY, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    source: Mapped[str] = mapped_column(String(120), default="market-data")
    topic_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class UniverseRecord(Base):
    """A real, refreshable instrument master used by automatic topic selection."""

    __tablename__ = "universe_instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    market: Mapped[str] = mapped_column(String(8), index=True)
    exchange: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8))
    security_type: Mapped[str] = mapped_column(String(32), default="equity")
    source: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    angle_hint: Mapped[str | None] = mapped_column(String(24), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class UniverseSyncRecord(Base):
    __tablename__ = "universe_syncs"

    sync_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    markets_json: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str] = mapped_column(Text)
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    active_total: Mapped[int] = mapped_column(Integer, default=0)
    eligible_total: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")


class PipelineRunRecord(Base):
    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default=PipelineStatus.TOPIC_QUEUED, index=True)
    current_stage: Mapped[str] = mapped_column(String(24), default=PipelineStatus.TOPIC_QUEUED)
    simulation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    render_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    output_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self._upgrade_publish_accounts()

    def _upgrade_publish_accounts(self) -> None:
        """Keep pre-v0.1.4 account databases readable without a destructive migration."""

        inspector = inspect(self.engine)
        if "publish_accounts" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("publish_accounts")}
        additions = {
            "platform": "VARCHAR(24) NOT NULL DEFAULT 'douyin'",
            "auth_status": "VARCHAR(24) NOT NULL DEFAULT 'unknown'",
            "last_checked_at": "DATETIME NULL",
        }
        with self.engine.begin() as connection:
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE publish_accounts ADD COLUMN {name} {definition}"
                    )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_publish_accounts_platform "
                "ON publish_accounts (platform)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_publish_accounts_auth_status "
                "ON publish_accounts (auth_status)"
            )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
