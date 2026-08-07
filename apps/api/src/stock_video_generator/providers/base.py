from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from stock_video_generator.models import (
    CorporateAction,
    HistoryBar,
    Instrument,
    ProviderHealth,
)


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    async def search_instruments(self, query: str) -> list[Instrument]:
        raise NotImplementedError

    @abstractmethod
    async def get_instrument(self, symbol: str) -> Instrument:
        raise NotImplementedError

    @abstractmethod
    async def get_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[HistoryBar]:
        raise NotImplementedError

    @abstractmethod
    async def get_corporate_actions(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError
