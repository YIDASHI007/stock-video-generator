from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError
from stock_video_generator.models import (
    DividendPolicy,
    Market,
    ShareMode,
    SimulationRequest,
)
from stock_video_generator.simulation import simulate_buy_and_hold
from stock_video_generator.story_hooks import TOP_STORY_HOOK_TEMPLATES, build_story_hook
from stock_video_generator.visualization import VisualizationSpec, build_visualization_spec


def test_visualization_spec_uses_simulation_numbers(
    instrument,
    bars,
    valid_result,
    source,
):
    request = SimulationRequest(
        symbol="TEST",
        buy_date=date(2025, 1, 2),
        initial_capital=1000,
        capital_currency="USD",
        share_mode=ShareMode.FRACTIONAL,
        dividend_policy=DividendPolicy.IGNORE,
    )
    result = simulate_buy_and_hold(
        request=request,
        instrument=instrument,
        bars=bars,
        actions=[],
        validation=valid_result,
        source=source,
        simulation_id="visual-test",
    )
    spec = build_visualization_spec(result)

    assert spec.template_version == "v1"
    assert spec.summary.final_value == result.summary.final_value
    assert spec.summary.return_pct == result.summary.total_return_pct
    assert spec.series[-1].value == result.series[-1].portfolio_value
    assert spec.composition.width == 1920
    assert spec.composition.height == 1080
    assert spec.composition.fps == 30
    assert (
        spec.timeline.intro_seconds + spec.timeline.chart_seconds + spec.timeline.outro_seconds
        == 60
    )
    assert spec.disclaimer == "历史数据模拟，仅供信息展示，不构成投资建议。"
    assert spec.story_hook is not None
    assert spec.story_hook.text


def test_story_hook_pool_has_at_least_twenty_data_driven_templates(
    instrument,
    bars,
    valid_result,
    source,
):
    assert len(TOP_STORY_HOOK_TEMPLATES) >= 20
    crypto = instrument.model_copy(
        update={
            "symbol": "BTC-USD",
            "name": "Bitcoin USD",
            "market": Market.CRYPTO,
        }
    )
    request = SimulationRequest(
        symbol="BTC-USD",
        buy_date=date(2025, 1, 2),
        initial_capital=1_000_000,
        capital_currency="USD",
        share_mode=ShareMode.FRACTIONAL,
        dividend_policy=DividendPolicy.IGNORE,
    )
    result = simulate_buy_and_hold(
        request=request,
        instrument=crypto,
        bars=bars,
        actions=[],
        validation=valid_result,
        source=source,
        simulation_id="crypto-hook-test",
    )

    first = build_visualization_spec(result).story_hook
    second = build_visualization_spec(result).story_hook

    assert first == second
    assert first is not None
    assert first.category in {"crypto", "drawdown"}
    assert "比特币" in first.text
    assert first.display_asset_name == "比特币"

    first_selection = build_story_hook(result)
    rotated = build_story_hook(
        result,
        excluded_template_ids={first_selection.template_id},
    )
    restored = build_story_hook(
        result,
        excluded_template_ids={first_selection.template_id},
        preferred_template_id=first_selection.template_id,
    )
    assert rotated.template_id != first_selection.template_id
    assert restored.template_id == first_selection.template_id


def test_milestones_are_deterministic(instrument, bars, valid_result, source):
    request = SimulationRequest(
        symbol="TEST",
        buy_date=date(2025, 1, 2),
        initial_capital=1000,
        capital_currency="USD",
        dividend_policy=DividendPolicy.IGNORE,
    )
    result = simulate_buy_and_hold(
        request=request,
        instrument=instrument,
        bars=bars,
        actions=[],
        validation=valid_result,
        source=source,
        simulation_id="milestone-test",
    )
    first = build_visualization_spec(result)
    second = build_visualization_spec(result)
    assert first == second
    assert {item.type for item in first.milestones} >= {
        "buy",
        "first_profit",
        "all_time_high",
        "max_drawdown_start",
        "max_drawdown_end",
        "final",
    }


def test_event_trading_date_must_be_preserved_in_series(
    instrument,
    bars,
    valid_result,
    source,
):
    request = SimulationRequest(
        symbol="TEST",
        buy_date=date(2025, 1, 2),
        initial_capital=1000,
        capital_currency="USD",
    )
    result = simulate_buy_and_hold(
        request=request,
        instrument=instrument,
        bars=bars,
        actions=[],
        validation=valid_result,
        source=source,
        simulation_id="event-date-test",
    )
    payload = build_visualization_spec(result).model_dump(mode="json")
    payload["events"] = [
        {
            "event_date": "2025-01-04",
            "effective_trading_date": "2025-01-04",
            "event_type": "测试事件",
            "title": "事件日期不在抽样序列中",
            "summary": "系统必须拒绝把事件悄悄映射到相邻交易日。",
            "source_label": "Fixture",
            "source_url": "https://example.com/event",
            "confidence": "high",
            "tone": "neutral",
        }
    ]

    with pytest.raises(ValidationError, match="事件交易日必须保留"):
        VisualizationSpec.model_validate(payload)
