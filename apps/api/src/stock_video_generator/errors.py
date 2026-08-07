from __future__ import annotations


class StockVideoError(Exception):
    """Base error with a stable machine-readable code and a Chinese user message."""

    code = "STOCK_VIDEO_ERROR"
    retryable = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class InstrumentNotFoundError(StockVideoError):
    code = "INSTRUMENT_NOT_FOUND"


class AmbiguousInstrumentError(StockVideoError):
    code = "AMBIGUOUS_INSTRUMENT"


class ProviderUnavailableError(StockVideoError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True


class MarketDataValidationError(StockVideoError):
    code = "MARKET_DATA_VALIDATION_FAILED"


class SimulationInputError(StockVideoError):
    code = "SIMULATION_INPUT_INVALID"


class MissingCorporateActionError(StockVideoError):
    code = "CORPORATE_ACTION_DATA_MISSING"


class RenderError(StockVideoError):
    code = "RENDER_FAILED"
    retryable = True


class DependencyUnavailableError(StockVideoError):
    code = "DEPENDENCY_UNAVAILABLE"


class ScriptValidationError(StockVideoError):
    """Narration script numbers/dates failed reconciliation against simulation data."""

    code = "SCRIPT_VALIDATION_FAILED"


class TTSUnavailableError(ProviderUnavailableError):
    """TTS provider is unreachable; narration jobs must fail honestly instead of
    silently producing a mute video."""

    code = "TTS_UNAVAILABLE"


class DiskSpaceError(StockVideoError):
    code = "DISK_SPACE_INSUFFICIENT"


class UniverseUnavailableError(StockVideoError):
    """股票池配置文件缺失或格式非法，自动选题必须失败而不是用假股票。"""

    code = "UNIVERSE_UNAVAILABLE"


class TopicPoolEmptyError(StockVideoError):
    code = "TOPIC_POOL_EMPTY"


class PipelineConflictError(StockVideoError):
    code = "PIPELINE_CONFLICT"
