from __future__ import annotations

import asyncio

from stock_video_generator.config import Settings
from stock_video_generator.market_data import MarketDataService
from stock_video_generator.models import ProviderHealth


class HealthProviderStub:
    def __init__(
        self,
        name: str,
        available: bool,
        calls: list[str],
    ) -> None:
        self.name = name
        self.available = available
        self.calls = calls

    async def health_check(self) -> ProviderHealth:
        self.calls.append(self.name)
        return ProviderHealth(
            name=self.name,
            available=self.available,
            latency_ms=1,
            message=f"{self.name} checked",
        )


def test_provider_health_is_sequential_cached_and_synthesizes_global(tmp_path):
    calls: list[str] = []
    service = MarketDataService(
        Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    )
    service.health_providers = [
        HealthProviderStub("akshare", True, calls),
        HealthProviderStub("yfinance", False, calls),
        HealthProviderStub("sina_global", True, calls),
    ]

    first = asyncio.run(service.health())
    second = asyncio.run(service.health())

    assert calls == ["akshare", "yfinance", "sina_global"]
    assert [result.name for result in first] == [
        "akshare",
        "yfinance",
        "sina_global",
        "global",
    ]
    assert first[-1].available is True
    assert second == first
