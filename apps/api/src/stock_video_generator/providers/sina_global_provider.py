from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests

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


def _market_for_symbol(symbol: str) -> Market:
    return Market.HK if symbol.strip().upper().endswith(".HK") else Market.US


def _canonical_symbol(symbol: str, market: Market) -> str:
    value = symbol.strip().upper()
    if market == Market.HK:
        code = re.sub(r"\D", "", value).zfill(5)
        return f"{code}.HK"
    return value


class SinaGlobalProvider(MarketDataProvider):
    """AKShare adapters for Sina's real HK/US history and adjustment factors."""

    name = "sina_global"
    source_label = "AKShare / Sina global fallback"

    def __init__(self) -> None:
        self._us_master: pd.DataFrame | None = None
        self._hk_master: pd.DataFrame | None = None

    def _load_us_master(self) -> pd.DataFrame:
        import akshare as ak

        if self._us_master is None:
            try:
                self._us_master = ak.get_us_stock_name()
            except Exception as exc:
                raise ProviderUnavailableError(
                    "AKShare/Sina 美股代码表暂时不可用。",
                    detail=f"{type(exc).__name__}: {exc}",
                ) from exc
        return self._us_master

    def _load_hk_master(self) -> pd.DataFrame:
        import akshare as ak

        if self._hk_master is None:
            try:
                self._hk_master = ak.stock_hk_spot()
            except Exception as exc:
                raise ProviderUnavailableError(
                    "AKShare/Sina 港股代码表暂时不可用。",
                    detail=f"{type(exc).__name__}: {exc}",
                ) from exc
        return self._hk_master

    @staticmethod
    def _instrument(
        symbol: str,
        name: str,
        market: Market,
    ) -> Instrument:
        if market == Market.HK:
            return Instrument(
                symbol=_canonical_symbol(symbol, market),
                name=name,
                market=market,
                exchange="HKEX",
                currency="HKD",
                timezone="Asia/Hong_Kong",
                market_lot=1,
                source="AKShare / Sina HK",
            )
        return Instrument(
            symbol=_canonical_symbol(symbol, market),
            name=name,
            market=market,
            exchange="NASDAQ/NYSE",
            currency="USD",
            timezone="America/New_York",
            market_lot=1,
            source="AKShare / Sina US",
        )

    def _search_sync(self, query: str) -> list[Instrument]:
        value = query.strip()
        hk_only = value.upper().endswith(".HK") or value.isdigit()
        us_only = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,14}", value))
        results: list[Instrument] = []
        market_types: list[tuple[str, Market]] = []
        if not us_only:
            market_types.append(("31", Market.HK))
        if not hk_only:
            market_types.append(("41", Market.US))
        errors: list[str] = []
        for type_code, market in market_types:
            try:
                search_key = (
                    re.sub(r"\D", "", value).zfill(5)
                    if market == Market.HK and hk_only
                    else value
                )
                response = requests.get(
                    (
                        "https://suggest3.sinajs.cn/suggest/"
                        f"type={type_code}&key={quote(search_key)}"
                    ),
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=20,
                )
                response.raise_for_status()
                response.encoding = "gb18030"
                match = re.search(r'var suggestvalue="(.*)";', response.text)
                entries = match.group(1).split(";") if match and match.group(1) else []
                for entry in entries:
                    fields = entry.split(",")
                    if len(fields) < 5:
                        continue
                    symbol = fields[2]
                    name = fields[4] or fields[0] or symbol
                    results.append(self._instrument(symbol, name, market))
            except Exception as exc:
                errors.append(f"type={type_code}: {type(exc).__name__}: {exc}")
        if not results and errors:
            raise ProviderUnavailableError(
                "AKShare/Sina 全球股票搜索暂时不可用。",
                detail=" | ".join(errors),
            )
        return results[:20]

    async def search_instruments(self, query: str) -> list[Instrument]:
        return await asyncio.to_thread(self._search_sync, query)

    def _get_instrument_sync(self, symbol: str) -> Instrument:
        market = _market_for_symbol(symbol)
        canonical = _canonical_symbol(symbol, market)
        matches = self._search_sync(canonical)
        exact = [item for item in matches if item.symbol == canonical]
        if exact:
            return exact[0]
        raise InstrumentNotFoundError(f"未找到股票代码：{symbol}。")

    async def get_instrument(self, symbol: str) -> Instrument:
        return await asyncio.to_thread(self._get_instrument_sync, symbol)

    def _history_sync(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str,
    ) -> list[HistoryBar]:
        if interval != "1d":
            raise ProviderUnavailableError("Sina 全球备用源仅支持日线 interval=1d。")
        import akshare as ak

        market = _market_for_symbol(symbol)
        canonical = _canonical_symbol(symbol, market)
        provider_symbol = canonical.removesuffix(".HK")
        try:
            frame = (
                ak.stock_hk_daily(symbol=provider_symbol, adjust="")
                if market == Market.HK
                else ak.stock_us_daily(symbol=provider_symbol, adjust="")
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"AKShare/Sina 无法获取 {canonical} 的未复权历史行情。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if frame.empty:
            raise ProviderUnavailableError(f"AKShare/Sina 返回的 {canonical} 行情为空。")
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frame = frame[
            (frame["date"] >= start_date) & (frame["date"] <= end_date)
        ]
        currency = "HKD" if market == Market.HK else "USD"
        source = (
            "AKShare / Sina stock_hk_daily (unadjusted fallback)"
            if market == Market.HK
            else "AKShare / Sina stock_us_daily (unadjusted fallback)"
        )
        fetched_at = datetime.now(UTC)
        bars: list[HistoryBar] = []
        for _, row in frame.iterrows():
            if any(
                pd.isna(row.get(column)) for column in ("open", "high", "low", "close")
            ):
                continue
            open_price = round(float(row["open"]), 4)
            close_price = round(float(row["close"]), 4)
            low_price = round(float(row["low"]), 4)
            high_price = round(float(row["high"]), 4)
            bars.append(
                HistoryBar(
                    date=row["date"],
                    open=open_price,
                    high=max(high_price, open_price, close_price, low_price),
                    low=min(low_price, open_price, close_price, high_price),
                    close=close_price,
                    volume=max(0, float(row.get("volume", 0) or 0)),
                    currency=currency,
                    source=source,
                    fetched_at=fetched_at,
                )
            )
        if not bars:
            raise ProviderUnavailableError(
                f"AKShare/Sina 返回的 {canonical} 行情区间为空。"
            )
        return bars

    async def get_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[HistoryBar]:
        return await asyncio.to_thread(
            self._history_sync,
            symbol,
            start_date,
            end_date,
            interval,
        )

    def _actions_sync(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        import akshare as ak

        market = _market_for_symbol(symbol)
        canonical = _canonical_symbol(symbol, market)
        provider_symbol = canonical.removesuffix(".HK")
        try:
            if market == Market.HK:
                frame = ak.stock_hk_daily(
                    symbol=provider_symbol,
                    adjust="hfq-factor",
                ).rename(columns={"hfq_factor": "factor", "cash": "adjust"})
            else:
                frame = ak.stock_us_daily(
                    symbol=provider_symbol,
                    adjust="qfq-factor",
                ).rename(columns={"qfq_factor": "factor"})
        except Exception as exc:
            raise ProviderUnavailableError(
                f"AKShare/Sina 无法获取 {canonical} 的公司行为因子。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if frame.empty:
            return []
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frame["factor"] = pd.to_numeric(frame["factor"], errors="coerce")
        frame["adjust"] = pd.to_numeric(frame["adjust"], errors="coerce")
        frame = frame.dropna(subset=["date", "factor", "adjust"]).sort_values("date")
        currency = "HKD" if market == Market.HK else "USD"
        source = (
            "AKShare / Sina HK adjustment factors"
            if market == Market.HK
            else "AKShare / Sina US adjustment factors"
        )
        actions: list[CorporateAction] = []
        previous_factor: float | None = None
        previous_adjust: float | None = None
        for _, row in frame.iterrows():
            event_date = row["date"]
            factor = float(row["factor"])
            adjust = float(row["adjust"])
            if previous_factor is not None and start_date <= event_date <= end_date:
                split_ratio = factor / previous_factor
                if abs(split_ratio - 1) > 1e-8:
                    actions.append(
                        CorporateAction(
                            ex_date=event_date,
                            event_type=CorporateActionType.SPLIT,
                            split_ratio=split_ratio,
                            currency=currency,
                            source=source,
                        )
                    )
                assert previous_adjust is not None
                cash_delta = adjust - previous_adjust
                dividend = cash_delta / factor if factor else 0
                if dividend > 1e-8:
                    actions.append(
                        CorporateAction(
                            ex_date=event_date,
                            event_type=CorporateActionType.DIVIDEND,
                            dividend_per_share=dividend,
                            currency=currency,
                            source=source,
                        )
                    )
            previous_factor = factor
            previous_adjust = adjust
        return actions

    async def get_corporate_actions(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        return await asyncio.to_thread(
            self._actions_sync,
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
            return ProviderHealth(
                name=self.name,
                available=bool(bars),
                latency_ms=(time.perf_counter() - started) * 1000,
                message=f"真实备用源可用，最近返回 {len(bars)} 个美股交易日。",
            )
        except Exception as exc:
            return ProviderHealth(
                name=self.name,
                available=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                message=f"不可用：{exc}",
            )
