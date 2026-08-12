from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
VIDEO_TEMPLATE_VERSION = "v1"
REMOTION_COMPOSITION_ID = "StockHistoricalSimulationV1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="APP_",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8877
    runtime_dir: Path = Field(default=PROJECT_ROOT, validation_alias="RUNTIME_DIR")
    data_dir: Path = PROJECT_ROOT / "data"
    log_dir: Path = PROJECT_ROOT / "logs"
    web_dist_dir: Path | None = Field(default=None, validation_alias="WEB_DIST_DIR")
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    node_executable: str = Field(default="node", validation_alias="NODE_EXECUTABLE")
    market_cache_recent_ttl_seconds: int = Field(
        default=1800, validation_alias="MARKET_CACHE_RECENT_TTL_SECONDS"
    )
    market_cache_historical_ttl_seconds: int = Field(
        default=2_592_000,
        validation_alias="MARKET_CACHE_HISTORICAL_TTL_SECONDS",
    )
    market_request_timeout_seconds: int = Field(
        default=30,
        validation_alias="MARKET_REQUEST_TIMEOUT_SECONDS",
    )
    render_max_concurrency: int = Field(default=1, validation_alias="RENDER_MAX_CONCURRENCY")
    fetch_max_concurrency: int = Field(default=3, validation_alias="FETCH_MAX_CONCURRENCY")
    simulation_max_concurrency: int = Field(
        default=4, validation_alias="SIMULATION_MAX_CONCURRENCY"
    )
    tts_max_concurrency: int = Field(default=2, validation_alias="TTS_MAX_CONCURRENCY")
    tts_provider: str = Field(default="edge", validation_alias="TTS_PROVIDER")
    tts_voice: str = Field(
        default="zh-CN-XiaoxiaoNeural",
        validation_alias="TTS_VOICE",
    )
    tts_speed: float = Field(default=1.08, validation_alias="TTS_SPEED")
    fetch_timeout_seconds: int = Field(default=90, validation_alias="FETCH_TIMEOUT_SECONDS")
    render_timeout_seconds: int = Field(default=900, validation_alias="RENDER_TIMEOUT_SECONDS")
    minimum_free_disk_bytes: int = Field(
        default=2 * 1024**3,
        validation_alias="MINIMUM_FREE_DISK_BYTES",
    )
    output_retention_days: int = Field(
        default=7,
        ge=1,
        validation_alias="OUTPUT_RETENTION_DAYS",
    )
    output_cleanup_interval_seconds: int = Field(
        default=3600,
        ge=60,
        validation_alias="OUTPUT_CLEANUP_INTERVAL_SECONDS",
    )
    publish_headless: bool = Field(default=False, validation_alias="PUBLISH_HEADLESS")
    publish_browser_channel: str = Field(
        default="chrome",
        validation_alias="PUBLISH_BROWSER_CHANNEL",
    )
    publish_step_timeout_seconds: int = Field(
        default=90,
        validation_alias="PUBLISH_STEP_TIMEOUT_SECONDS",
    )
    publish_upload_timeout_seconds: int = Field(
        default=900,
        validation_alias="PUBLISH_UPLOAD_TIMEOUT_SECONDS",
    )
    publish_max_agent_fallbacks: int = Field(
        default=3,
        validation_alias="PUBLISH_MAX_AGENT_FALLBACKS",
    )
    publish_agent_command: str | None = Field(
        default=None,
        validation_alias="PUBLISH_AGENT_COMMAND",
    )
    publish_agent_model: str = Field(
        default="openai/gpt-4.1-mini",
        validation_alias="PUBLISH_AGENT_MODEL",
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        repr=False,
    )

    @property
    def database_url(self) -> str:
        database_dir = self.data_dir / "database"
        database_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(database_dir / 'stock_video.db').as_posix()}"

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @property
    def resolved_web_dist_dir(self) -> Path:
        return (self.web_dist_dir or self.runtime_dir / "apps" / "web" / "dist").resolve()

    @property
    def pnpm_store_dirs(self) -> tuple[Path, ...]:
        return (
            self.runtime_dir / "apps" / "renderer" / "node_modules" / ".pnpm",
            self.runtime_dir / "node_modules" / ".pnpm",
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir / "database",
            self.data_dir / "market-cache",
            self.data_dir / "simulations",
            self.data_dir / "renders",
            self.data_dir / "outputs",
            self.data_dir / "publishes",
            self.data_dir / "publish-accounts",
            self.data_dir / "integrations" / "douyin",
            self.data_dir / "imports" / "douyin",
            self.log_dir / "jobs",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_node_executable(self) -> str | None:
        configured = Path(self.node_executable)
        if configured.is_absolute() and configured.exists():
            return str(configured)
        return shutil.which(self.node_executable)


settings = Settings()
