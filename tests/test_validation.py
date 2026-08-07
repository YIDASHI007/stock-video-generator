from __future__ import annotations

from datetime import date

from stock_video_generator.models import HistoryBar, NonTradingDayPolicy
from stock_video_generator.validation import validate_market_data


def test_empty_history_is_invalid(instrument):
    result = validate_market_data(
        instrument,
        [],
        [],
        requested_start=date(2025, 1, 1),
        requested_end=date(2025, 1, 7),
    )
    assert not result.valid
    assert "为空" in result.errors[0]


def test_duplicate_dates_are_invalid(instrument, bars):
    result = validate_market_data(
        instrument,
        [*bars, bars[-1]],
        [],
        requested_start=date(2025, 1, 2),
        requested_end=date(2025, 1, 7),
    )
    assert not result.valid
    assert any("重复交易日期" in error for error in result.errors)


def test_buy_before_listing_is_invalid(instrument, bars):
    result = validate_market_data(
        instrument,
        bars,
        [],
        requested_start=date(2024, 1, 1),
        requested_end=date(2025, 1, 7),
    )
    assert not result.valid
    assert any("早于可用行情起始日" in error for error in result.errors)


def test_buy_before_listing_rolls_to_listing_for_next_day_policy(instrument, bars):
    result = validate_market_data(
        instrument,
        bars,
        [],
        requested_start=date(2024, 1, 1),
        requested_end=date(2025, 1, 7),
        non_trading_day_policy=NonTradingDayPolicy.NEXT_TRADING_DAY,
    )
    assert result.valid
    assert any("上市后的首个可用交易日" in warning for warning in result.warnings)


def test_currency_mismatch_is_invalid(instrument, bars):
    mismatched = [HistoryBar(**{**bar.model_dump(), "currency": "HKD"}) for bar in bars]
    result = validate_market_data(
        instrument,
        mismatched,
        [],
        requested_start=date(2025, 1, 2),
        requested_end=date(2025, 1, 7),
    )
    assert not result.valid
    assert any("币种" in error for error in result.errors)


def test_nearby_non_trading_day_is_not_treated_as_pre_listing(instrument, bars):
    result = validate_market_data(
        instrument,
        bars,
        [],
        requested_start=date(2025, 1, 1),
        requested_end=date(2025, 1, 7),
    )
    assert result.valid
    assert any("不是交易日" in warning for warning in result.warnings)
