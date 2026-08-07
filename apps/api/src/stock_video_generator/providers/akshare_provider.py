from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, date, datetime, timedelta

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


def _code_only(symbol: str) -> str:
    value = symbol.strip().upper()
    return value.split(".")[0]


def _exchange_for_code(code: str) -> tuple[str, str]:
    if code.startswith(("5", "6", "9")):
        return "SSE", f"{code}.SH"
    if code.startswith(("4", "8")):
        return "BSE", f"{code}.BJ"
    return "SZSE", f"{code}.SZ"


def _sina_symbol(code: str) -> str:
    exchange, _ = _exchange_for_code(code)
    prefix = "sh" if exchange == "SSE" else "sz"
    return f"{prefix}{code}"


def _clean_cn_name(name: str) -> str:
    return re.sub(r"^(?:XD|XR|DR)\s*", "", name.strip(), flags=re.IGNORECASE)


class AKShareProvider(MarketDataProvider):
    name = "akshare"
    source_label = "AKShare / Eastmoney+Sina"

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        self._master: pd.DataFrame | None = None

    def _load_master_sync(self) -> pd.DataFrame:
        import akshare as ak

        if self._master is not None:
            return self._master
        try:
            frame = ak.stock_info_a_code_name()
        except Exception as exc:
            raise ProviderUnavailableError(
                "AKShare A 股代码表暂时不可用。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        self._master = frame
        return frame

    def _instrument(self, code: str, name: str) -> Instrument:
        exchange, symbol = _exchange_for_code(code)
        return Instrument(
            symbol=symbol,
            name=_clean_cn_name(name),
            market=Market.CN,
            exchange=exchange,
            currency="CNY",
            timezone="Asia/Shanghai",
            market_lot=100,
            source=self.source_label,
        )

    def _search_sync(self, query: str) -> list[Instrument]:
        frame = self._load_master_sync()
        value = _code_only(query)
        matches = frame[
            frame["code"].str.contains(value, case=False, regex=False)
            | frame["name"].astype(str).str.contains(query, case=False, regex=False)
        ].head(20)
        return [
            self._instrument(str(row["code"]), str(row["name"])) for _, row in matches.iterrows()
        ]

    async def search_instruments(self, query: str) -> list[Instrument]:
        return await asyncio.to_thread(self._search_sync, query)

    def _get_instrument_sync(self, symbol: str) -> Instrument:
        code = _code_only(symbol)
        frame = self._load_master_sync()
        match = frame[frame["code"] == code]
        if match.empty:
            raise InstrumentNotFoundError(f"未找到 A 股代码：{symbol}。")
        return self._instrument(code, str(match.iloc[0]["name"]))

    async def get_instrument(self, symbol: str) -> Instrument:
        return await asyncio.to_thread(self._get_instrument_sync, symbol)

    def _get_history_sync(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str,
    ) -> list[HistoryBar]:
        import akshare as ak

        if interval != "1d":
            raise ProviderUnavailableError("AKShare MVP 仅支持日线 interval=1d。")
        code = _code_only(symbol)
        errors: list[str] = []
        try:
            frame = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="",
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            errors.append(f"Eastmoney stock_zh_a_hist: {type(exc).__name__}: {exc}")
            frame = pd.DataFrame()
        source = "AKShare / Eastmoney stock_zh_a_hist (unadjusted)"
        if frame.empty:
            try:
                frame = ak.stock_zh_a_daily(
                    symbol=_sina_symbol(code),
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust="",
                )
                source = "AKShare / Sina stock_zh_a_daily (unadjusted fallback)"
            except Exception as exc:
                errors.append(f"Sina stock_zh_a_daily: {type(exc).__name__}: {exc}")
                frame = pd.DataFrame()
        if frame.empty:
            raise ProviderUnavailableError(
                f"AKShare 的主数据源和备用数据源均无法获取 {symbol} 的未复权历史行情。",
                detail=" | ".join(errors) if errors else "两个真实数据源均返回空数据。",
            )
        fetched_at = datetime.now(UTC)
        bars: list[HistoryBar] = []
        for _, row in frame.iterrows():
            chinese_columns = "日期" in frame.columns
            bars.append(
                HistoryBar(
                    date=pd.to_datetime(row["日期"] if chinese_columns else row["date"]).date(),
                    open=float(row["开盘"] if chinese_columns else row["open"]),
                    high=float(row["最高"] if chinese_columns else row["high"]),
                    low=float(row["最低"] if chinese_columns else row["low"]),
                    close=float(row["收盘"] if chinese_columns else row["close"]),
                    volume=max(
                        0,
                        float(
                            (row.get("成交量", 0) if chinese_columns else row.get("volume", 0)) or 0
                        ),
                    ),
                    currency="CNY",
                    source=source,
                    fetched_at=fetched_at,
                )
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
        import akshare as ak

        code = _code_only(symbol)
        try:
            frame = ak.stock_history_dividend_detail(
                symbol=code,
                indicator="分红",
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"AKShare 无法获取 {symbol} 的分红与送转数据。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if frame.empty:
            return []

        actions: list[CorporateAction] = []
        for _, row in frame.iterrows():
            if pd.isna(row.get("除权除息日")):
                continue
            event_date = pd.to_datetime(row["除权除息日"]).date()
            if not (start_date <= event_date <= end_date):
                continue
            cash_per_ten = float(row.get("派息", 0) or 0)
            bonus_per_ten = float(row.get("送股", 0) or 0)
            transfer_per_ten = float(row.get("转增", 0) or 0)
            if cash_per_ten > 0:
                actions.append(
                    CorporateAction(
                        ex_date=event_date,
                        event_type=CorporateActionType.DIVIDEND,
                        dividend_per_share=cash_per_ten / 10,
                        currency="CNY",
                        source="AKShare / Sina stock_history_dividend_detail",
                    )
                )
            share_increase = bonus_per_ten + transfer_per_ten
            if share_increase > 0:
                actions.append(
                    CorporateAction(
                        ex_date=event_date,
                        event_type=CorporateActionType.SPLIT,
                        split_ratio=1 + share_increase / 10,
                        currency="CNY",
                        source="AKShare / Sina stock_history_dividend_detail",
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
                "600519.SH",
                date.today() - timedelta(days=14),
                date.today(),
            )
            return ProviderHealth(
                name=self.name,
                available=bool(bars),
                latency_ms=(time.perf_counter() - started) * 1000,
                message=f"可用，最近请求返回 {len(bars)} 个交易日。",
            )
        except Exception as exc:
            return ProviderHealth(
                name=self.name,
                available=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                message=f"不可用：{exc}",
            )
