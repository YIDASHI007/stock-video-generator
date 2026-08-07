from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from stock_video_generator.models import (
    DataValidationResult,
    HistoryBar,
    Instrument,
    Market,
    SourceMetadata,
)


@pytest.fixture
def instrument() -> Instrument:
    return Instrument(
        symbol="TEST",
        name="测试股票",
        market=Market.US,
        exchange="TEST",
        currency="USD",
        timezone="America/New_York",
        market_lot=100,
        source="fixture",
    )


@pytest.fixture
def bars() -> list[HistoryBar]:
    fetched_at = datetime(2025, 1, 10, tzinfo=UTC)
    return [
        HistoryBar(
            date=date(2025, 1, day),
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=1000,
            currency="USD",
            source="fixture",
            fetched_at=fetched_at,
        )
        for day, price in [(2, 10), (3, 12), (6, 8), (7, 16)]
    ]


@pytest.fixture
def valid_result(bars: list[HistoryBar]) -> DataValidationResult:
    return DataValidationResult(
        valid=True,
        data_start=bars[0].date,
        data_end=bars[-1].date,
        trading_days=len(bars),
    )


@pytest.fixture
def source() -> SourceMetadata:
    return SourceMetadata(
        provider="fixture",
        fetched_at=datetime(2025, 1, 10, tzinfo=UTC),
        request_parameters={"fixture": True},
        raw_response_summary={"rows": 4},
    )
