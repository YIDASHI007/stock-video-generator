from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Market(StrEnum):
    CN = "CN"
    HK = "HK"
    US = "US"
    CRYPTO = "CRYPTO"


class ShareMode(StrEnum):
    FRACTIONAL = "fractional"
    INTEGER = "integer"
    MARKET_LOT = "market_lot"


class DividendPolicy(StrEnum):
    IGNORE = "ignore"
    CASH = "cash"
    REINVEST = "reinvest"


class NonTradingDayPolicy(StrEnum):
    NEXT_TRADING_DAY = "next_trading_day"
    PREVIOUS_TRADING_DAY = "previous_trading_day"
    REJECT = "reject"


class ExecutionPrice(StrEnum):
    OPEN = "open"
    CLOSE = "close"


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str
    market: Market
    exchange: str
    currency: str
    timezone: str
    market_lot: int = Field(default=1, ge=1)
    source: str


class HistoryBar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    currency: str
    source: str
    fetched_at: datetime

    @model_validator(mode="after")
    def validate_ohlc(self) -> HistoryBar:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("最高价低于开盘价、收盘价或最低价")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("最低价高于开盘价、收盘价或最高价")
        return self


class CorporateActionType(StrEnum):
    DIVIDEND = "dividend"
    SPLIT = "split"


class CorporateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ex_date: date
    event_type: CorporateActionType
    dividend_per_share: float | None = Field(default=None, ge=0)
    split_ratio: float | None = Field(default=None, gt=0)
    currency: str
    source: str

    @model_validator(mode="after")
    def validate_event_value(self) -> CorporateAction:
        if self.event_type == CorporateActionType.DIVIDEND and self.dividend_per_share is None:
            raise ValueError("分红事件缺少每股分红")
        if self.event_type == CorporateActionType.SPLIT and self.split_ratio is None:
            raise ValueError("拆合股事件缺少比例")
        return self


class FeePolicy(BaseModel):
    enabled: bool = False
    commission_rate: float = Field(default=0, ge=0, le=1)
    minimum_commission: float = Field(default=0, ge=0)
    stamp_duty_rate: float = Field(default=0, ge=0, le=1)


class VideoConfig(BaseModel):
    duration_seconds: Annotated[int, Field(ge=15, le=180)] = 60
    fps: Literal[30] = 30
    # 横屏 16:9 画布（竖屏 1080x1920 仍可渲染，由 spec 尺寸驱动）。
    width: Annotated[int, Field(ge=640, le=3840)] = 1920
    height: Annotated[int, Field(ge=360, le=2160)] = 1080
    theme: Literal["dark", "light"] = "dark"
    voice_enabled: bool = False
    voice: str | None = None


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    buy_date: date
    end_date: date | Literal["latest"] = "latest"
    initial_capital: float = Field(gt=0)
    capital_currency: str
    execution_price: ExecutionPrice = ExecutionPrice.CLOSE
    non_trading_day_policy: NonTradingDayPolicy = NonTradingDayPolicy.NEXT_TRADING_DAY
    share_mode: ShareMode = ShareMode.FRACTIONAL
    dividend_policy: DividendPolicy = DividendPolicy.REINVEST
    fee_policy: FeePolicy = Field(default_factory=FeePolicy)
    video: VideoConfig = Field(default_factory=VideoConfig)

    @field_validator("symbol", "capital_currency")
    @classmethod
    def normalize_uppercase(cls, value: str) -> str:
        return value.strip().upper()


class DataValidationResult(BaseModel):
    valid: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    data_start: date | None = None
    data_end: date | None = None
    trading_days: int = 0


class SourceMetadata(BaseModel):
    provider: str
    fetched_at: datetime
    request_parameters: dict[str, object]
    cache_key: str | None = None
    cache_hit: bool = False
    raw_response_summary: dict[str, object] = Field(default_factory=dict)


class SimulationEvent(BaseModel):
    date: date
    event_type: str
    description: str
    shares_before: float
    shares_after: float
    cash_before: float
    cash_after: float
    amount: float | None = None
    source: str | None = None


class SimulationPoint(BaseModel):
    date: date
    close: float
    shares: float
    cash: float
    portfolio_value: float
    total_return_pct: float
    drawdown_pct: float


class SimulationSummary(BaseModel):
    actual_buy_date: date
    buy_price: float
    initial_shares: float
    final_shares: float
    final_cash: float
    final_value: float
    total_return_pct: float
    max_drawdown_pct: float
    best_value: float
    worst_value: float
    dividend_total: float
    total_fees: float


class SimulationResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    simulation_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    instrument: Instrument
    assumptions: dict[str, object]
    source: SourceMetadata
    validation: DataValidationResult
    summary: SimulationSummary
    events: list[SimulationEvent]
    series: list[SimulationPoint]


class ProviderHealth(BaseModel):
    name: str
    available: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float | None = None
    message: str
