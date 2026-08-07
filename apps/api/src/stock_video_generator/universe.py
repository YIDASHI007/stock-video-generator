"""Dynamic stock universe synchronization.

The topic queue must not depend on a small hand-maintained JSON file.  This
module maintains a durable instrument master from real exchange/provider
lists.  A failed source never replaces previously known-good data.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pandas as pd
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.sqlite import insert

from stock_video_generator.database import (
    Database,
    UniverseRecord,
    UniverseSyncRecord,
    now_utc,
)
from stock_video_generator.models import Market

logger = logging.getLogger(__name__)

NASDAQ_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
)
OTHER_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
)
HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)
DEFAULT_SYNC_INTERVAL = timedelta(hours=24)

CURATED_CRYPTO = (
    ("BTC-USD", "比特币"),
    ("ETH-USD", "以太坊"),
    ("BNB-USD", "币安币"),
    ("SOL-USD", "Solana"),
    ("XRP-USD", "XRP"),
    ("DOGE-USD", "狗狗币"),
    ("ADA-USD", "Cardano"),
    ("TRX-USD", "波场"),
    ("LINK-USD", "Chainlink"),
    ("LTC-USD", "莱特币"),
    ("BCH-USD", "比特币现金"),
    ("DOT-USD", "Polkadot"),
    ("AVAX-USD", "Avalanche"),
    ("SHIB-USD", "柴犬币"),
    ("XLM-USD", "Stellar"),
    ("ETC-USD", "以太坊经典"),
    ("NEAR-USD", "NEAR"),
    ("AAVE-USD", "Aave"),
    ("HBAR-USD", "Hedera"),
    ("TON-USD", "Toncoin"),
    ("ICP-USD", "Internet Computer"),
    ("FIL-USD", "Filecoin"),
    ("OP-USD", "Optimism"),
    ("BONK-USD", "Bonk"),
    ("WIF-USD", "dogwifhat"),
    ("INJ-USD", "Injective"),
    ("RENDER-USD", "Render"),
    ("FET-USD", "Artificial Superintelligence Alliance"),
    ("CRO-USD", "Cronos"),
)


@dataclass(frozen=True)
class UniverseInstrument:
    symbol: str
    name: str
    market: Market
    exchange: str
    currency: str
    source: str
    security_type: str = "equity"
    eligible: bool = True
    exclusion_reason: str | None = None
    angle_hint: str | None = None


def _cn_symbol(code: str) -> tuple[str, str]:
    value = str(code).strip().zfill(6)
    if value.startswith(("4", "8", "920")):
        return f"{value}.BJ", "BSE"
    if value.startswith(("5", "6", "9")):
        return f"{value}.SH", "SSE"
    return f"{value}.SZ", "SZSE"


def _normal_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normal_cn_name(value: object) -> str:
    return re.sub(r"^(?:XD|XR|DR)\s*", "", _normal_name(value), flags=re.IGNORECASE)


def _cn_eligible(name: str) -> tuple[bool, str | None]:
    compact = name.upper().replace(" ", "")
    if "ST" in compact or "退" in name:
        return False, "ST或退市整理股票"
    return True, None


def _us_eligible(symbol: str, name: str) -> tuple[bool, str | None]:
    upper_name = name.upper()
    if not symbol or any(mark in symbol for mark in ("$", "^", "/", " ")):
        return False, "非普通股票代码"
    excluded_words = (
        " WARRANT",
        " RIGHTS",
        " RIGHT",
        " UNIT",
        " PREFERRED",
        " NOTES DUE",
        " BOND",
        " FUND",
        " TRUST",
        " PORTFOLIO",
        " BENEFICIAL INTEREST",
        " ACQUISITION CORP",
        " ACQUISITION CO",
        " BLANK CHECK",
    )
    if any(word in upper_name for word in excluded_words):
        return False, "非普通股证券"
    return True, None


class UniverseService:
    def __init__(
        self,
        settings,
        database: Database,
        *,
        sync_interval: timedelta = DEFAULT_SYNC_INTERVAL,
    ) -> None:
        self.settings = settings
        self.database = database
        self.sync_interval = sync_interval
        self.seed_path = settings.data_dir / "universe.json"
        self._sync_lock = asyncio.Lock()
        self._loop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.bootstrap_seed()
        self._loop_task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._loop_task = None

    async def _sync_loop(self) -> None:
        while True:
            try:
                if self.needs_sync():
                    await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("股票主库自动同步失败")
            await asyncio.sleep(3600)

    def needs_sync(self) -> bool:
        with self.database.session() as session:
            latest = session.scalars(
                select(UniverseSyncRecord)
                .where(UniverseSyncRecord.status.in_(["completed", "partial"]))
                .order_by(UniverseSyncRecord.completed_at.desc())
                .limit(1)
            ).first()
        if latest is None or latest.completed_at is None:
            return True
        completed = latest.completed_at
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)
        return now_utc() - completed >= self.sync_interval

    def bootstrap_seed(self) -> int:
        """Import the curated JSON as a durable fallback and priority seed."""
        if not self.seed_path.is_file():
            return 0
        try:
            raw = json.loads(self.seed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("读取人工精选股票名单失败：%s", self.seed_path)
            return 0
        if not isinstance(raw, list):
            return 0
        instruments: list[UniverseInstrument] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                market = Market(str(item["market"]))
                symbol = str(item["symbol"]).strip().upper()
                name = _normal_name(item["name"])
            except (KeyError, ValueError):
                continue
            exchange = (
                "HKEX"
                if market == Market.HK
                else (
                    "NASDAQ/NYSE"
                    if market == Market.US
                    else (
                        "CRYPTO"
                        if market == Market.CRYPTO
                        else symbol.rsplit(".", 1)[-1]
                    )
                )
            )
            instruments.append(
                UniverseInstrument(
                    symbol=symbol,
                    name=name,
                    market=market,
                    exchange=exchange,
                    currency={
                        Market.CN: "CNY",
                        Market.HK: "HKD",
                        Market.US: "USD",
                        Market.CRYPTO: "USD",
                    }[market],
                    source="人工精选 universe.json",
                    security_type=("crypto" if market == Market.CRYPTO else "equity"),
                    angle_hint=(
                        str(item["angle_hint"])
                        if item.get("angle_hint")
                        else None
                    ),
                )
            )
        if instruments:
            self._upsert(instruments, now_utc())
        return len(instruments)

    @staticmethod
    def _fetch_cn() -> list[UniverseInstrument]:
        import akshare as ak

        frame = ak.stock_info_a_code_name()
        results: list[UniverseInstrument] = []
        for row in frame.to_dict("records"):
            code = str(row.get("code") or "").strip()
            name = _normal_cn_name(row.get("name"))
            if not code or not name:
                continue
            symbol, exchange = _cn_symbol(code)
            eligible, reason = _cn_eligible(name)
            results.append(
                UniverseInstrument(
                    symbol=symbol,
                    name=name,
                    market=Market.CN,
                    exchange=exchange,
                    currency="CNY",
                    source="AKShare stock_info_a_code_name",
                    eligible=eligible,
                    exclusion_reason=reason,
                )
            )
        if not results:
            raise RuntimeError("AKShare A股名单为空")
        return results

    @staticmethod
    def _fetch_hk() -> list[UniverseInstrument]:
        response = httpx.get(
            HKEX_SECURITIES_URL,
            timeout=45,
            follow_redirects=True,
            headers={"User-Agent": "stock-video-generator/1.0"},
        )
        response.raise_for_status()
        frame = pd.read_excel(io.BytesIO(response.content), header=2)
        results: list[UniverseInstrument] = []
        for row in frame.to_dict("records"):
            if str(row.get("Category") or "").strip() != "Equity":
                continue
            subcategory = str(row.get("Sub-Category") or "")
            if "Equity Securities" not in subcategory:
                continue
            raw_code = row.get("Stock Code")
            try:
                code = f"{int(raw_code):04d}"
            except (TypeError, ValueError):
                continue
            name = _normal_name(row.get("Name of Securities"))
            if not name:
                continue
            liquid = (
                subcategory == "Equity Securities (Main Board)"
                and str(row.get("Shortsell Eligible") or "").strip() == "Y"
            )
            results.append(
                UniverseInstrument(
                    symbol=f"{code}.HK",
                    name=name,
                    market=Market.HK,
                    exchange="HKEX",
                    currency=str(row.get("Trading Currency") or "HKD").strip(),
                    source="HKEX ListOfSecurities",
                    eligible=liquid,
                    exclusion_reason=(
                        None
                        if liquid
                        else "未进入港交所可沽空证券名单（流动性筛选）"
                    ),
                )
            )
        if not results:
            raise RuntimeError("港交所股票名单为空")
        return results

    @staticmethod
    def _fetch_text(url: str) -> str:
        response = httpx.get(
            url,
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "stock-video-generator/1.0"},
        )
        response.raise_for_status()
        return response.text

    @classmethod
    def _fetch_us(cls) -> list[UniverseInstrument]:
        results: dict[str, UniverseInstrument] = {}

        nasdaq_rows = csv.DictReader(
            io.StringIO(cls._fetch_text(NASDAQ_LISTED_URL)),
            delimiter="|",
        )
        for row in nasdaq_rows:
            symbol = str(row.get("Symbol") or "").strip().upper()
            name = _normal_name(row.get("Security Name"))
            if (
                not symbol
                or symbol.startswith("File Creation Time")
                or row.get("ETF") != "N"
                or row.get("Test Issue") != "N"
                or row.get("Financial Status") != "N"
            ):
                continue
            eligible, reason = _us_eligible(symbol, name)
            results[symbol] = UniverseInstrument(
                symbol=symbol,
                name=name,
                market=Market.US,
                exchange="NASDAQ",
                currency="USD",
                source="NASDAQ Trader Symbol Directory",
                eligible=eligible,
                exclusion_reason=reason,
            )

        other_rows = csv.DictReader(
            io.StringIO(cls._fetch_text(OTHER_LISTED_URL)),
            delimiter="|",
        )
        exchange_names = {
            "A": "NYSE AMERICAN",
            "N": "NYSE",
            "P": "NYSE ARCA",
            "Z": "CBOE",
            "V": "IEX",
        }
        for row in other_rows:
            symbol = str(row.get("ACT Symbol") or "").strip().upper()
            name = _normal_name(row.get("Security Name"))
            if (
                not symbol
                or symbol.startswith("File Creation Time")
                or row.get("ETF") != "N"
                or row.get("Test Issue") != "N"
            ):
                continue
            eligible, reason = _us_eligible(symbol, name)
            results[symbol] = UniverseInstrument(
                symbol=symbol,
                name=name,
                market=Market.US,
                exchange=exchange_names.get(str(row.get("Exchange")), "US"),
                currency="USD",
                source="NASDAQ Trader Symbol Directory",
                eligible=eligible,
                exclusion_reason=reason,
            )
        if not results:
            raise RuntimeError("NASDAQ Trader美股名单为空")
        return list(results.values())

    @staticmethod
    def _fetch_crypto() -> list[UniverseInstrument]:
        """Keep crypto intentionally curated: famous, liquid assets only."""
        return [
            UniverseInstrument(
                symbol=symbol,
                name=name,
                market=Market.CRYPTO,
                exchange="CRYPTO",
                currency="USD",
                source="curated major crypto assets",
                security_type="crypto",
            )
            for symbol, name in CURATED_CRYPTO
        ]

    def _upsert(
        self,
        instruments: list[UniverseInstrument],
        seen_at: datetime,
    ) -> tuple[int, int]:
        if not instruments:
            return 0, 0
        unique = {item.symbol: item for item in instruments}
        added = 0
        updated_count = 0
        items = list(unique.values())
        # Keep statements well below SQLite's bound-parameter limit.
        for offset in range(0, len(items), 400):
            chunk = items[offset : offset + 400]
            symbols = [item.symbol for item in chunk]
            with self.database.session() as session:
                existing = set(
                    session.scalars(
                        select(UniverseRecord.symbol).where(
                            UniverseRecord.symbol.in_(symbols)
                        )
                    ).all()
                )
                payload = [
                    {
                        "symbol": item.symbol,
                        "name": item.name,
                        "market": item.market.value,
                        "exchange": item.exchange,
                        "currency": item.currency,
                        "security_type": item.security_type,
                        "source": item.source,
                        "active": True,
                        "eligible": item.eligible,
                        "exclusion_reason": item.exclusion_reason,
                        "angle_hint": item.angle_hint,
                        "discovered_at": seen_at,
                        "last_seen_at": seen_at,
                        "updated_at": seen_at,
                    }
                    for item in chunk
                ]
                statement = insert(UniverseRecord).values(payload)
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[UniverseRecord.symbol],
                        set_={
                            # The small curated file provides better Chinese names
                            # and manual inclusions.  Market sync enriches it but
                            # must not overwrite that editorial metadata.
                            "name": case(
                                (
                                    UniverseRecord.source
                                    == "人工精选 universe.json",
                                    UniverseRecord.name,
                                ),
                                else_=statement.excluded.name,
                            ),
                            "market": statement.excluded.market,
                            "exchange": statement.excluded.exchange,
                            "currency": statement.excluded.currency,
                            "security_type": statement.excluded.security_type,
                            "source": case(
                                (
                                    UniverseRecord.source
                                    == "人工精选 universe.json",
                                    UniverseRecord.source,
                                ),
                                else_=statement.excluded.source,
                            ),
                            "active": True,
                            "eligible": case(
                                (
                                    UniverseRecord.source
                                    == "人工精选 universe.json",
                                    UniverseRecord.eligible,
                                ),
                                else_=statement.excluded.eligible,
                            ),
                            "exclusion_reason": case(
                                (
                                    UniverseRecord.source
                                    == "人工精选 universe.json",
                                    UniverseRecord.exclusion_reason,
                                ),
                                else_=statement.excluded.exclusion_reason,
                            ),
                            "last_seen_at": seen_at,
                            "updated_at": seen_at,
                        },
                    )
                )
            added += len(set(symbols) - existing)
            updated_count += len(set(symbols) & existing)
        return added, updated_count

    async def sync(
        self,
        markets: list[Market] | None = None,
    ) -> dict[str, object]:
        requested = list(dict.fromkeys(markets or list(Market)))
        if self._sync_lock.locked():
            return {**self.status(), "sync_in_progress": True}
        async with self._sync_lock:
            sync_id = str(uuid4())
            started = now_utc()
            with self.database.session() as session:
                session.add(
                    UniverseSyncRecord(
                        sync_id=sync_id,
                        status="running",
                        started_at=started,
                        markets_json=json.dumps([item.value for item in requested]),
                        sources_json="{}",
                    )
                )

            fetchers = {
                Market.CN: self._fetch_cn,
                Market.HK: self._fetch_hk,
                Market.US: self._fetch_us,
                Market.CRYPTO: self._fetch_crypto,
            }
            outcomes = await asyncio.gather(
                *(asyncio.to_thread(fetchers[market]) for market in requested),
                return_exceptions=True,
            )
            errors: list[dict[str, str]] = []
            sources: dict[str, int] = {}
            added = 0
            updated_count = 0
            for market, outcome in zip(requested, outcomes, strict=True):
                if isinstance(outcome, Exception):
                    errors.append(
                        {
                            "market": market.value,
                            "reason": f"{type(outcome).__name__}: {outcome}",
                        }
                    )
                    logger.warning("股票主库 %s 同步失败：%s", market.value, outcome)
                    continue
                market_added, market_updated = self._upsert(outcome, started)
                added += market_added
                updated_count += market_updated
                sources[market.value] = len(outcome)
                with self.database.session() as session:
                    session.execute(
                        update(UniverseRecord)
                        .where(
                            UniverseRecord.market == market.value,
                            UniverseRecord.last_seen_at < started,
                            UniverseRecord.source != "人工精选 universe.json",
                        )
                        .values(active=False, updated_at=started)
                    )

            summary = self.status()
            completed = now_utc()
            sync_status = (
                "failed"
                if len(errors) == len(requested)
                else ("partial" if errors else "completed")
            )
            with self.database.session() as session:
                record = session.get(UniverseSyncRecord, sync_id)
                if record:
                    record.status = sync_status
                    record.completed_at = completed
                    record.sources_json = json.dumps(sources, ensure_ascii=False)
                    record.added = added
                    record.updated = updated_count
                    record.active_total = int(summary["active_total"])
                    record.eligible_total = int(summary["eligible_total"])
                    record.errors_json = json.dumps(errors, ensure_ascii=False)
            return {
                **self.status(),
                "sync_id": sync_id,
                "sync_status": sync_status,
                "added": added,
                "updated": updated_count,
                "sources": sources,
                "errors": errors,
                "sync_in_progress": False,
            }

    def status(self) -> dict[str, object]:
        with self.database.session() as session:
            active_total = session.scalar(
                select(func.count())
                .select_from(UniverseRecord)
                .where(UniverseRecord.active.is_(True))
            ) or 0
            eligible_total = session.scalar(
                select(func.count())
                .select_from(UniverseRecord)
                .where(
                    UniverseRecord.active.is_(True),
                    UniverseRecord.eligible.is_(True),
                )
            ) or 0
            latest = session.scalars(
                select(UniverseSyncRecord)
                .order_by(UniverseSyncRecord.started_at.desc())
                .limit(1)
            ).first()
        # SQLite's boolean SUM can be represented as text by some drivers;
        # compute the market eligibility counts with a portable second query.
        by_market: dict[str, dict[str, int]] = {}
        with self.database.session() as session:
            for market, total, eligible in session.execute(
                select(
                    UniverseRecord.market,
                    func.count(),
                    func.count().filter(UniverseRecord.eligible.is_(True)),
                )
                .where(UniverseRecord.active.is_(True))
                .group_by(UniverseRecord.market)
            ).all():
                by_market[str(market)] = {
                    "active": int(total),
                    "eligible": int(eligible),
                }
        latest_payload = None
        if latest:
            latest_payload = {
                "sync_id": latest.sync_id,
                "status": latest.status,
                "started_at": latest.started_at,
                "completed_at": latest.completed_at,
                "errors": json.loads(latest.errors_json or "[]"),
            }
        return {
            "active_total": int(active_total),
            "eligible_total": int(eligible_total),
            "excluded_total": int(active_total) - int(eligible_total),
            "by_market": by_market,
            "last_sync": latest_payload,
            "sync_in_progress": self._sync_lock.locked(),
        }
