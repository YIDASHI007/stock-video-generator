from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime

from pydantic import TypeAdapter

from stock_video_generator.cache import MarketDataCache
from stock_video_generator.config import Settings
from stock_video_generator.models import (
    CorporateAction,
    HistoryBar,
    Instrument,
    Market,
    ProviderHealth,
    SourceMetadata,
)
from stock_video_generator.providers.akshare_provider import AKShareProvider
from stock_video_generator.providers.base import MarketDataProvider
from stock_video_generator.providers.global_provider import GlobalMarketDataProvider
from stock_video_generator.providers.sina_global_provider import SinaGlobalProvider
from stock_video_generator.providers.yfinance_provider import YFinanceProvider

history_adapter = TypeAdapter(list[HistoryBar])
actions_adapter = TypeAdapter(list[CorporateAction])


class MarketDataService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        yfinance = YFinanceProvider(timeout_seconds=settings.market_request_timeout_seconds)
        sina_global = SinaGlobalProvider()
        self.providers: dict[str, MarketDataProvider] = {
            "akshare": AKShareProvider(timeout_seconds=settings.market_request_timeout_seconds),
            "global": GlobalMarketDataProvider(yfinance, sina_global),
        }
        self.health_providers: list[MarketDataProvider] = [
            self.providers["akshare"],
            yfinance,
            sina_global,
        ]
        self._health_cache: list[ProviderHealth] | None = None
        self._health_checked_at = 0.0
        self._health_lock = asyncio.Lock()
        self.cache = MarketDataCache(
            settings.data_dir / "market-cache",
            recent_ttl_seconds=settings.market_cache_recent_ttl_seconds,
            historical_ttl_seconds=settings.market_cache_historical_ttl_seconds,
        )

    @staticmethod
    def provider_name_for_symbol(symbol: str) -> str:
        value = symbol.strip().upper()
        if value.endswith((".SH", ".SZ", ".BJ")):
            return "akshare"
        if value.isdigit() and len(value) == 6 and not value.endswith(".HK"):
            return "akshare"
        return "global"

    async def search(
        self,
        query: str,
        market: Market | None = None,
    ) -> list[Instrument]:
        if market == Market.CN:
            provider_names = ["akshare"]
        elif market in {Market.HK, Market.US, Market.CRYPTO}:
            provider_names = ["global"]
        else:
            provider_names = ["akshare", "global"]
        outcomes = await asyncio.gather(
            *(self.providers[name].search_instruments(query) for name in provider_names),
            return_exceptions=True,
        )
        results: list[Instrument] = []
        errors: list[Exception] = []
        for outcome in outcomes:
            if isinstance(outcome, list):
                results.extend(outcome)
            elif isinstance(outcome, Exception):
                errors.append(outcome)
        if market:
            results = [item for item in results if item.market == market]
        if not results and errors:
            raise errors[0]
        return list({item.symbol: item for item in results}.values())

    async def get_instrument(self, symbol: str) -> Instrument:
        provider = self.providers[self.provider_name_for_symbol(symbol)]
        return await provider.get_instrument(symbol)

    async def get_history(
        self,
        provider: MarketDataProvider,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> tuple[list[HistoryBar], dict[str, object]]:
        parameters = {
            "symbol": symbol,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "interval": "1d",
            "adjustment": "unadjusted-v2-reconstructed-splits",
        }
        key = self.cache.make_key(provider.name, "history", parameters)
        cached = self.cache.get(key)
        if cached:
            return history_adapter.validate_python(cached["payload"]), cached
        bars = await provider.get_history(symbol, start_date, end_date, "1d")
        envelope = self.cache.put(
            key,
            provider=provider.name,
            operation="history",
            parameters=parameters,
            payload=[bar.model_dump(mode="json") for bar in bars],
            raw_response_summary={
                "rows": len(bars),
                "sources": sorted({bar.source for bar in bars}),
                "columns": [
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ],
            },
            requested_end=end_date,
        )
        return bars, envelope

    async def get_actions(
        self,
        provider: MarketDataProvider,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> tuple[list[CorporateAction], dict[str, object]]:
        parameters = {
            "symbol": symbol,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        key = self.cache.make_key(provider.name, "corporate_actions", parameters)
        cached = self.cache.get(key)
        if cached:
            return actions_adapter.validate_python(cached["payload"]), cached
        actions = await provider.get_corporate_actions(symbol, start_date, end_date)
        envelope = self.cache.put(
            key,
            provider=provider.name,
            operation="corporate_actions",
            parameters=parameters,
            payload=[action.model_dump(mode="json") for action in actions],
            raw_response_summary={
                "rows": len(actions),
                "event_types": sorted({action.event_type.value for action in actions}),
            },
            requested_end=end_date,
        )
        return actions, envelope

    async def fetch_bundle(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> tuple[
        Instrument,
        list[HistoryBar],
        list[CorporateAction],
        SourceMetadata,
    ]:
        provider = self.providers[self.provider_name_for_symbol(symbol)]
        instrument = await provider.get_instrument(symbol)
        bars, history_envelope = await self.get_history(
            provider,
            instrument.symbol,
            start_date,
            end_date,
        )
        actions, actions_envelope = await self.get_actions(
            provider,
            instrument.symbol,
            start_date,
            end_date,
        )
        fetched_at = datetime.fromisoformat(str(history_envelope["fetched_at"]))
        history_sources = history_envelope["raw_response_summary"].get("sources", [])
        source_provider = (
            " / ".join(str(value) for value in history_sources)
            if provider.name == "global" and history_sources
            else provider.name
        )
        metadata = SourceMetadata(
            provider=source_provider,
            fetched_at=fetched_at.astimezone(UTC),
            request_parameters={
                "symbol": instrument.symbol,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjustment": "unadjusted-v2-reconstructed-splits",
            },
            cache_key=str(history_envelope["cache_key"]),
            cache_hit=bool(self.cache.get(str(history_envelope["cache_key"]))),
            raw_response_summary={
                "history": history_envelope["raw_response_summary"],
                "corporate_actions": actions_envelope["raw_response_summary"],
            },
        )
        return instrument, bars, actions, metadata

    async def health(self) -> list[ProviderHealth]:
        now = time.monotonic()
        if self._health_cache is not None and now - self._health_checked_at < 300:
            return list(self._health_cache)

        async with self._health_lock:
            now = time.monotonic()
            if self._health_cache is not None and now - self._health_checked_at < 300:
                return list(self._health_cache)
            # AKShare's Eastmoney and Sina adapters can initialize the embedded
            # V8 runtime. Initializing that DLL from two worker threads at once
            # can terminate the entire Windows process, so health probes run in
            # a deterministic sequence and are cached below.
            physical = []
            for provider in self.health_providers:
                physical.append(await provider.health_check())
            by_name = {result.name: result for result in physical}
            primary = by_name["yfinance"]
            fallback = by_name["sina_global"]
            global_health = ProviderHealth(
                name="global",
                available=primary.available or fallback.available,
                latency_ms=(
                    primary.latency_ms if primary.available else fallback.latency_ms
                ),
                message=(
                    "主数据源 yfinance 可用。"
                    if primary.available
                    else f"yfinance 不可用；Sina 备用源：{fallback.message}"
                ),
            )
            self._health_cache = [*physical, global_health]
            self._health_checked_at = time.monotonic()
            return list(self._health_cache)
