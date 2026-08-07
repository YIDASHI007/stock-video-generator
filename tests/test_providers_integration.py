from __future__ import annotations

import asyncio
from datetime import date

import pytest
from stock_video_generator.config import Settings
from stock_video_generator.market_data import MarketDataService
from stock_video_generator.models import DividendPolicy, Market, SimulationRequest, VideoConfig
from stock_video_generator.providers.akshare_provider import AKShareProvider
from stock_video_generator.providers.sina_global_provider import SinaGlobalProvider
from stock_video_generator.providers.yfinance_provider import YFinanceProvider
from stock_video_generator.runner import SimulationRunner


@pytest.mark.integration
def test_real_a_share_history_and_actions():
    provider = AKShareProvider(timeout_seconds=30)

    async def run():
        instrument = await provider.get_instrument("600519.SH")
        bars = await provider.get_history(
            instrument.symbol,
            date(2024, 1, 2),
            date(2024, 3, 29),
        )
        actions = await provider.get_corporate_actions(
            instrument.symbol,
            date(2020, 1, 1),
            date(2025, 12, 31),
        )
        return instrument, bars, actions

    instrument, bars, actions = asyncio.run(run())
    assert instrument.market == Market.CN
    assert bars and all(bar.source != "fixture" for bar in bars)
    assert actions and all(action.source != "fixture" for action in actions)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("symbol", "market"),
    [("00700.HK", Market.HK), ("AAPL", Market.US)],
)
def test_real_sina_fallback_global_history_and_actions(symbol, market):
    provider = SinaGlobalProvider()

    async def run():
        instrument = await provider.get_instrument(symbol)
        bars = await provider.get_history(
            instrument.symbol,
            date(2024, 1, 2),
            date(2024, 3, 29),
        )
        actions = await provider.get_corporate_actions(
            instrument.symbol,
            date(2020, 1, 1),
            date(2025, 12, 31),
        )
        return instrument, bars, actions

    instrument, bars, actions = asyncio.run(run())
    assert instrument.market == market
    assert bars and all(bar.source != "fixture" for bar in bars)
    assert actions and all(action.source != "fixture" for action in actions)


@pytest.mark.integration
def test_yfinance_health_reports_real_status():
    result = asyncio.run(YFinanceProvider(timeout_seconds=30).health_check())
    assert result.name == "yfinance"
    assert result.message


@pytest.mark.integration
def test_provider_health_executes_real_requests(tmp_path):
    service = MarketDataService(Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs"))
    results = asyncio.run(service.health())
    assert {result.name for result in results} == {
        "akshare",
        "yfinance",
        "sina_global",
        "global",
    }
    assert all(result.message for result in results)


@pytest.mark.integration
def test_real_a_share_end_to_end_artifacts(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs")
    request = SimulationRequest(
        symbol="600519.SH",
        buy_date=date(2024, 1, 2),
        end_date=date(2024, 6, 28),
        initial_capital=1_000_000,
        capital_currency="CNY",
        dividend_policy=DividendPolicy.REINVEST,
        video=VideoConfig(duration_seconds=15),
    )
    result, spec, paths = asyncio.run(SimulationRunner(settings).run(request))
    assert result.validation.valid
    assert result.source.provider == "akshare"
    assert result.series
    assert spec.summary.final_value == result.summary.final_value
    for name in (
        "simulation_json",
        "simulation_csv",
        "visualization_spec_json",
        "market_data_json",
    ):
        assert paths[name]
