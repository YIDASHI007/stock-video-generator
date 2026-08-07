from __future__ import annotations

from datetime import date

from stock_video_generator.errors import ProviderUnavailableError
from stock_video_generator.models import (
    CorporateAction,
    HistoryBar,
    Instrument,
    ProviderHealth,
)
from stock_video_generator.providers.base import MarketDataProvider
from stock_video_generator.providers.sina_global_provider import SinaGlobalProvider
from stock_video_generator.providers.yfinance_provider import YFinanceProvider


class GlobalMarketDataProvider(MarketDataProvider):
    """yfinance primary with a real AKShare/Sina fallback."""

    name = "global"
    source_label = "yfinance primary / AKShare-Sina fallback"

    def __init__(
        self,
        yfinance: YFinanceProvider,
        sina: SinaGlobalProvider,
    ) -> None:
        self.yfinance = yfinance
        self.sina = sina

    async def _first_success(self, method: str, *args):
        errors: list[str] = []
        for provider in (self.yfinance, self.sina):
            try:
                result = await getattr(provider, method)(*args)
                if result or method == "get_corporate_actions":
                    return result
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                errors.append(f"{provider.name}: {exc} {detail or ''}".strip())
        raise ProviderUnavailableError(
            "全球行情主数据源和备用数据源均不可用。",
            detail=" | ".join(errors),
        )

    async def search_instruments(self, query: str) -> list[Instrument]:
        return await self._first_success("search_instruments", query)

    async def get_instrument(self, symbol: str) -> Instrument:
        return await self._first_success("get_instrument", symbol)

    async def get_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[HistoryBar]:
        return await self._first_success(
            "get_history",
            symbol,
            start_date,
            end_date,
            interval,
        )

    async def get_corporate_actions(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        return await self._first_success(
            "get_corporate_actions",
            symbol,
            start_date,
            end_date,
        )

    async def health_check(self) -> ProviderHealth:
        primary = await self.yfinance.health_check()
        if primary.available:
            return ProviderHealth(
                name=self.name,
                available=True,
                latency_ms=primary.latency_ms,
                message="主数据源 yfinance 可用。",
            )
        fallback = await self.sina.health_check()
        return ProviderHealth(
            name=self.name,
            available=fallback.available,
            latency_ms=fallback.latency_ms,
            message=(
                f"yfinance 不可用（{primary.message}）；"
                f"Sina 真实备用源：{fallback.message}"
            ),
        )
