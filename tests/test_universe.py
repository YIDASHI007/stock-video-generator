from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from stock_video_generator.config import Settings
from stock_video_generator.database import Database, UniverseRecord, now_utc
from stock_video_generator.models import Market
from stock_video_generator.universe import (
    UniverseInstrument,
    UniverseService,
    _cn_symbol,
    _us_eligible,
)


def make_service(tmp_path: Path) -> tuple[UniverseService, Database]:
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    return UniverseService(settings, database), database


def test_cn_symbols_are_canonical() -> None:
    assert _cn_symbol("600519") == ("600519.SH", "SSE")
    assert _cn_symbol("000001") == ("000001.SZ", "SZSE")
    assert _cn_symbol("920407") == ("920407.BJ", "BSE")


def test_us_funds_spacs_and_warrants_are_not_eligible() -> None:
    assert _us_eligible("AAPL", "Apple Inc. - Common Stock") == (True, None)
    assert (
        _us_eligible(
            "BCX",
            "BlackRock Resources Common Shares of Beneficial Interest",
        )[0]
        is False
    )
    assert _us_eligible("DEMO", "Demo Acquisition Corp. - Common Stock")[0] is False
    assert _us_eligible("DEMO.W", "Demo Inc. Warrant")[0] is False


def test_curated_name_survives_market_refresh(tmp_path: Path) -> None:
    service, database = make_service(tmp_path)
    service.seed_path.write_text(
        json.dumps(
            [{"symbol": "0700.HK", "name": "腾讯控股", "market": "HK"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert service.bootstrap_seed() == 1
    service._upsert(
        [
            UniverseInstrument(
                symbol="0700.HK",
                name="TENCENT",
                market=Market.HK,
                exchange="HKEX",
                currency="HKD",
                source="HKEX ListOfSecurities",
            )
        ],
        now_utc(),
    )
    with database.session() as session:
        record = session.scalar(
            select(UniverseRecord).where(UniverseRecord.symbol == "0700.HK")
        )
        assert record is not None
        assert record.name == "腾讯控股"
        assert record.source == "人工精选 universe.json"
