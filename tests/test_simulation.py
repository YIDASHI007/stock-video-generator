from __future__ import annotations

from datetime import date

import pytest
from stock_video_generator.errors import SimulationInputError
from stock_video_generator.models import (
    CorporateAction,
    CorporateActionType,
    DividendPolicy,
    FeePolicy,
    NonTradingDayPolicy,
    ShareMode,
    SimulationRequest,
)
from stock_video_generator.simulation import simulate_buy_and_hold


def make_request(**overrides: object) -> SimulationRequest:
    values: dict[str, object] = {
        "symbol": "TEST",
        "buy_date": date(2025, 1, 2),
        "end_date": "latest",
        "initial_capital": 1000,
        "capital_currency": "USD",
        "share_mode": ShareMode.FRACTIONAL,
        "dividend_policy": DividendPolicy.IGNORE,
    }
    values.update(overrides)
    return SimulationRequest(**values)


def run(request, instrument, bars, valid_result, source, actions=None):
    return simulate_buy_and_hold(
        request=request,
        instrument=instrument,
        bars=bars,
        actions=actions or [],
        validation=valid_result,
        source=source,
        simulation_id="deterministic-test",
    )


def test_non_trading_day_moves_to_next_day(instrument, bars, valid_result, source):
    request = make_request(buy_date=date(2025, 1, 4))
    result = run(request, instrument, bars, valid_result, source)
    assert result.summary.actual_buy_date == date(2025, 1, 6)
    assert result.summary.buy_price == 8


def test_non_trading_day_rejects(instrument, bars, valid_result, source):
    request = make_request(
        buy_date=date(2025, 1, 4),
        non_trading_day_policy=NonTradingDayPolicy.REJECT,
    )
    with pytest.raises(SimulationInputError, match="不是交易日"):
        run(request, instrument, bars, valid_result, source)


def test_fractional_shares(instrument, bars, valid_result, source):
    result = run(make_request(initial_capital=105), instrument, bars, valid_result, source)
    assert result.summary.initial_shares == 10.5
    assert result.summary.final_value == 168


def test_integer_shares_keep_remainder(instrument, bars, valid_result, source):
    result = run(
        make_request(initial_capital=105, share_mode=ShareMode.INTEGER),
        instrument,
        bars,
        valid_result,
        source,
    )
    assert result.summary.initial_shares == 10
    assert result.summary.final_cash == 5
    assert result.summary.final_value == 165


def test_market_lot_shares(instrument, bars, valid_result, source):
    result = run(
        make_request(initial_capital=2500, share_mode=ShareMode.MARKET_LOT),
        instrument,
        bars,
        valid_result,
        source,
    )
    assert result.summary.initial_shares == 200
    assert result.summary.final_cash == 500


def test_fees_are_deducted(instrument, bars, valid_result, source):
    result = run(
        make_request(
            initial_capital=1000,
            share_mode=ShareMode.INTEGER,
            fee_policy=FeePolicy(
                enabled=True,
                commission_rate=0.001,
                minimum_commission=5,
                stamp_duty_rate=0,
            ),
        ),
        instrument,
        bars,
        valid_result,
        source,
    )
    assert result.summary.initial_shares == 99
    assert result.summary.total_fees == 5
    assert result.summary.final_cash == 5


def test_cash_dividend(instrument, bars, valid_result, source):
    action = CorporateAction(
        ex_date=date(2025, 1, 3),
        event_type=CorporateActionType.DIVIDEND,
        dividend_per_share=1,
        currency="USD",
        source="fixture",
    )
    result = run(
        make_request(dividend_policy=DividendPolicy.CASH),
        instrument,
        bars,
        valid_result,
        source,
        [action],
    )
    assert result.summary.dividend_total == 100
    assert result.summary.final_cash == 100
    assert result.summary.final_value == 1700


def test_reinvest_dividend(instrument, bars, valid_result, source):
    action = CorporateAction(
        ex_date=date(2025, 1, 3),
        event_type=CorporateActionType.DIVIDEND,
        dividend_per_share=1,
        currency="USD",
        source="fixture",
    )
    result = run(
        make_request(dividend_policy=DividendPolicy.REINVEST),
        instrument,
        bars,
        valid_result,
        source,
        [action],
    )
    assert result.summary.final_shares == pytest.approx(108.33333333)
    assert result.summary.final_value == pytest.approx(1733.33333328)


def test_split_changes_shares_and_preserves_event_value(instrument, bars, valid_result, source):
    action = CorporateAction(
        ex_date=date(2025, 1, 6),
        event_type=CorporateActionType.SPLIT,
        split_ratio=2,
        currency="USD",
        source="fixture",
    )
    result = run(
        make_request(),
        instrument,
        bars,
        valid_result,
        source,
        [action],
    )
    assert result.summary.final_shares == 200
    assert result.events[1].event_type == "split"


def test_max_drawdown(instrument, bars, valid_result, source):
    result = run(make_request(), instrument, bars, valid_result, source)
    assert result.summary.max_drawdown_pct == pytest.approx(-33.33333333)


def test_currency_mismatch_is_rejected(instrument, bars, valid_result, source):
    request = make_request(capital_currency="HKD")
    with pytest.raises(SimulationInputError, match="币种"):
        run(request, instrument, bars, valid_result, source)


def test_same_input_produces_same_numeric_output(instrument, bars, valid_result, source):
    request = make_request(dividend_policy=DividendPolicy.CASH)
    first = run(request, instrument, bars, valid_result, source)
    second = run(request, instrument, bars, valid_result, source)
    assert first.summary == second.summary
    assert first.series == second.series
