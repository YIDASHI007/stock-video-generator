from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from stock_video_generator.errors import (
    InstrumentNotFoundError,
    ProviderUnavailableError,
)
from stock_video_generator.models import (
    CorporateAction,
    CorporateActionType,
    HistoryBar,
    Instrument,
    Market,
    ProviderHealth,
)
from stock_video_generator.providers.base import MarketDataProvider


def _market_from_quote(symbol: str, exchange: str, currency: str) -> Market:
    normalized_symbol = symbol.upper()
    normalized_exchange = exchange.upper()
    if normalized_symbol.endswith(".HK") or normalized_exchange in {"HKG", "HKSE"}:
        return Market.HK
    if normalized_symbol.endswith((".SS", ".SZ", ".BJ")) or currency.upper() == "CNY":
        return Market.CN
    if normalized_symbol.endswith(("-USD", "-USDT")) or normalized_exchange in {
        "CCC",
        "CCY",
        "CRYPTO",
    }:
        return Market.CRYPTO
    return Market.US


def _normalize_cn_yahoo_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if value.endswith(".SH"):
        return f"{value[:-3]}.SS"
    if value.endswith(".SZ") or value.endswith(".BJ"):
        return value
    if value.isdigit() and len(value) == 6:
        suffix = ".SS" if value.startswith(("5", "6", "9")) else ".SZ"
        return f"{value}{suffix}"
    return value


def _exchange_details(market: Market, exchange: str) -> tuple[str, str, str, int]:
    if market == Market.HK:
        return "HKEX", "HKD", "Asia/Hong_Kong", 1
    if market == Market.CN:
        normalized = exchange.upper()
        canonical = "SSE" if normalized in {"SHH", "SHG", "SSE"} else "SZSE"
        return canonical, "CNY", "Asia/Shanghai", 100
    if market == Market.CRYPTO:
        return "CRYPTO", "USD", "UTC", 1
    return exchange or "NASDAQ/NYSE", "USD", "America/New_York", 1


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"
    source_label = "yfinance / Yahoo Finance"

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def _instrument_from_quote(self, quote: dict[str, Any]) -> Instrument:
        symbol = str(quote.get("symbol") or "").upper()
        exchange = str(quote.get("exchange") or quote.get("exchDisp") or "")
        currency = str(quote.get("currency") or "")
        market = _market_from_quote(symbol, exchange, currency)
        canonical_exchange, fallback_currency, timezone_name, market_lot = _exchange_details(
            market,
            exchange,
        )
        canonical_symbol = symbol
        if canonical_symbol.endswith(".SS"):
            canonical_symbol = f"{canonical_symbol[:-3]}.SH"
        return Instrument(
            symbol=canonical_symbol,
            name=str(
                quote.get("longname")
                or quote.get("shortname")
                or quote.get("displayName")
                or symbol
            ),
            market=market,
            exchange=canonical_exchange,
            currency=currency or fallback_currency,
            timezone=str(quote.get("exchangeTimezoneName") or timezone_name),
            market_lot=market_lot,
            source=self.source_label,
        )

    def _search_sync(self, query: str) -> list[Instrument]:
        import yfinance as yf

        try:
            search = yf.Search(
                _normalize_cn_yahoo_symbol(query),
                max_results=12,
                news_count=0,
                timeout=self.timeout_seconds,
                raise_errors=True,
            )
            quotes = search.quotes
        except Exception as exc:
            raise ProviderUnavailableError(
                "yfinance 行情搜索暂时不可用。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        results: list[Instrument] = []
        for quote in quotes:
            if str(quote.get("quoteType", "")).upper() not in {
                "EQUITY",
                "CRYPTOCURRENCY",
                "",
            }:
                continue
            try:
                results.append(self._instrument_from_quote(quote))
            except (TypeError, ValueError):
                continue
        return list({item.symbol: item for item in results}.values())

    async def search_instruments(self, query: str) -> list[Instrument]:
        return await asyncio.to_thread(self._search_sync, query)

    def _get_instrument_sync(self, symbol: str) -> Instrument:
        normalized = _normalize_cn_yahoo_symbol(symbol)
        candidates = self._search_sync(normalized)
        exact = [
            candidate
            for candidate in candidates
            if _normalize_cn_yahoo_symbol(candidate.symbol) == normalized
        ]
        if not exact:
            raise InstrumentNotFoundError(f"未找到股票代码：{symbol}。")
        return exact[0]

    async def get_instrument(self, symbol: str) -> Instrument:
        return await asyncio.to_thread(self._get_instrument_sync, symbol)

    def _history_frame(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str,
    ) -> pd.DataFrame:
        import yfinance as yf

        normalized = _normalize_cn_yahoo_symbol(symbol)
        try:
            return yf.Ticker(normalized).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                interval=interval,
                actions=True,
                auto_adjust=False,
                repair=False,
                keepna=False,
                timeout=self.timeout_seconds,
                raise_errors=True,
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"yfinance 无法获取 {symbol} 的历史行情。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    def _get_history_sync(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str,
    ) -> list[HistoryBar]:
        frame = self._history_frame(symbol, start_date, end_date, interval)
        if frame.empty:
            raise ProviderUnavailableError(f"yfinance 返回的 {symbol} 行情为空。")
        instrument = self._get_instrument_sync(symbol)
        fetched_at = datetime.now(UTC)
        split_events = [
            (index.date(), float(row.get("Stock Splits", 0) or 0))
            for index, row in frame.iterrows()
            if float(row.get("Stock Splits", 0) or 0) > 0
        ]
        bars: list[HistoryBar] = []
        for index, row in frame.iterrows():
            required = [row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")]
            if any(pd.isna(value) or float(value) <= 0 for value in required):
                continue
            # Yahoo's OHLC is split-adjusted even when auto_adjust=False. Rebuild
            # true historical prices so the simulator can apply split events once.
            factor = math.prod(
                ratio for split_date, ratio in split_events if index.date() < split_date
            )
            open_price = float(row["Open"]) * factor
            close_price = float(row["Close"]) * factor
            high_price = max(float(row["High"]) * factor, open_price, close_price)
            low_price = min(float(row["Low"]) * factor, open_price, close_price)
            bars.append(
                HistoryBar(
                    date=index.date(),
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=max(0, float(row.get("Volume", 0) or 0)) / factor,
                    currency=instrument.currency,
                    source=self.source_label,
                    fetched_at=fetched_at,
                )
            )
        if not bars:
            raise ProviderUnavailableError(f"yfinance 返回的 {symbol} 行情没有有效 OHLC 数据。")
        return bars

    async def get_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[HistoryBar]:
        return await asyncio.to_thread(
            self._get_history_sync,
            symbol,
            start_date,
            end_date,
            interval,
        )

    def _get_actions_sync(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        frame = self._history_frame(symbol, start_date, end_date, "1d")
        instrument = self._get_instrument_sync(symbol)
        actions: list[CorporateAction] = []
        for index, row in frame.iterrows():
            event_date = index.date()
            dividend = float(row.get("Dividends", 0) or 0)
            split = float(row.get("Stock Splits", 0) or 0)
            if dividend > 0:
                actions.append(
                    CorporateAction(
                        ex_date=event_date,
                        event_type=CorporateActionType.DIVIDEND,
                        dividend_per_share=dividend,
                        currency=instrument.currency,
                        source=self.source_label,
                    )
                )
            if split > 0:
                actions.append(
                    CorporateAction(
                        ex_date=event_date,
                        event_type=CorporateActionType.SPLIT,
                        split_ratio=split,
                        currency=instrument.currency,
                        source=self.source_label,
                    )
                )
        return actions

    async def get_corporate_actions(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        return await asyncio.to_thread(
            self._get_actions_sync,
            symbol,
            start_date,
            end_date,
        )

    async def health_check(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            bars = await self.get_history(
                "AAPL",
                date.today() - timedelta(days=14),
                date.today(),
            )
            latency = (time.perf_counter() - started) * 1000
            return ProviderHealth(
                name=self.name,
                available=bool(bars),
                latency_ms=latency,
                message=f"可用，最近请求返回 {len(bars)} 个交易日。",
            )
        except Exception as exc:
            return ProviderHealth(
                name=self.name,
                available=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                message=f"不可用：{exc}",
            )
