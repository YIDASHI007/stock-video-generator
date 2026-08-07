from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from stock_video_generator.errors import ScriptValidationError
from stock_video_generator.models import (
    DataValidationResult,
    Instrument,
    Market,
    SimulationPoint,
    SimulationResult,
    SimulationSummary,
    SourceMetadata,
)
from stock_video_generator.scripting import (
    NarrationScript,
    generate_script,
    generate_script_template,
    validate_script,
)

EMPHASIS_VALUES = {"surge", "crash", "sideways", "recovery"}


def _series() -> list[SimulationPoint]:
    """500 个交易日的确定性曲线：冲高 → 腰斩 → 反弹 → 缓慢回落。"""
    start = date(2021, 1, 4)
    legs = [
        (0, 124, 1_000_000.0, 1_300_000.0),
        (125, 249, 1_300_000.0, 680_000.0),
        (250, 374, 680_000.0, 1_100_000.0),
        (375, 499, 1_100_000.0, 920_000.0),
    ]
    values: list[float] = [0.0] * 500
    for first, last, start_value, end_value in legs:
        span = max(1, last - first)
        for index in range(first, last + 1):
            ratio = (index - first) / span
            values[index] = start_value + (end_value - start_value) * ratio

    points: list[SimulationPoint] = []
    running_max = 0.0
    for index, value in enumerate(values):
        running_max = max(running_max, value)
        points.append(
            SimulationPoint(
                date=start + timedelta(days=index),
                close=value / 500,
                shares=500.0,
                cash=0.0,
                portfolio_value=value,
                total_return_pct=(value / 1_000_000 - 1) * 100,
                drawdown_pct=(value / running_max - 1) * 100,
            )
        )
    return points


@pytest.fixture
def result() -> SimulationResult:
    series = _series()
    return SimulationResult(
        simulation_id="script-test",
        created_at=datetime(2025, 1, 10, tzinfo=UTC),
        instrument=Instrument(
            symbol="600519.SH",
            name="贵州茅台",
            market=Market.CN,
            exchange="SSE",
            currency="CNY",
            timezone="Asia/Shanghai",
            market_lot=100,
            source="fixture",
        ),
        assumptions={
            "initial_capital": 1_000_000,
            "dividend_policy": "reinvest",
            "execution_price": "close",
            "share_mode": "fractional",
            "fee_policy": {"enabled": False},
            "video": {"duration_seconds": 30},
        },
        source=SourceMetadata(
            provider="fixture",
            fetched_at=datetime(2025, 1, 10, tzinfo=UTC),
            request_parameters={},
        ),
        validation=DataValidationResult(
            valid=True,
            data_start=series[0].date,
            data_end=series[-1].date,
            trading_days=len(series),
        ),
        summary=SimulationSummary(
            actual_buy_date=series[0].date,
            buy_price=2000.0,
            initial_shares=500.0,
            final_shares=500.0,
            final_cash=0.0,
            final_value=series[-1].portfolio_value,
            total_return_pct=series[-1].total_return_pct,
            max_drawdown_pct=min(point.drawdown_pct for point in series),
            best_value=max(point.portfolio_value for point in series),
            worst_value=min(point.portfolio_value for point in series),
            dividend_total=0.0,
            total_fees=0.0,
        ),
        events=[],
        series=series,
    )


def test_template_script_anchors_are_real(result: SimulationResult) -> None:
    script = generate_script_template(result)
    series_dates = {point.date for point in result.series}

    assert 4 <= len(script.segments) <= 8
    for segment in script.segments:
        assert segment.anchor_date in series_dates
        assert segment.emphasis in EMPHASIS_VALUES
        assert len(segment.narration) <= 30
    # 段按时间顺序排列
    assert [
        segment.anchor_date for segment in script.segments
    ] == sorted(segment.anchor_date for segment in script.segments)
    # 模板生成天然过数字对账（validate 在生成内部已执行，这里再独立跑一遍）
    validate_script(script, result)
    assert "贵州茅台" in script.hook
    assert "只剩" in script.finale  # 亏损题材


def test_template_script_is_deterministic(result: SimulationResult) -> None:
    first = generate_script_template(result)
    second = generate_script_template(result)
    assert first.model_dump_json() == second.model_dump_json()


def test_tampered_percentage_fails_reconciliation(result: SimulationResult) -> None:
    script = generate_script_template(result)
    tampered = script.model_copy(deep=True)
    tampered.segments[1].narration = "这一天暴涨，赚了97.7%。"
    with pytest.raises(ScriptValidationError):
        validate_script(tampered, result)


def test_tampered_amount_fails_reconciliation(result: SimulationResult) -> None:
    script = generate_script_template(result)
    tampered = script.model_copy(deep=True)
    tampered.finale = "拿到今天，100万变成了999.9万。"
    with pytest.raises(ScriptValidationError):
        validate_script(tampered, result)


def test_fake_anchor_date_fails(result: SimulationResult) -> None:
    script = generate_script_template(result)
    tampered = script.model_copy(deep=True)
    tampered.segments[0].anchor_date = date(1999, 12, 31)
    with pytest.raises(ScriptValidationError):
        validate_script(tampered, result)


def test_overlong_narration_fails(result: SimulationResult) -> None:
    script = generate_script_template(result)
    tampered = script.model_copy(deep=True)
    tampered.segments[0].narration = "超" * 31
    with pytest.raises(ScriptValidationError):
        validate_script(tampered, result)


def test_segment_count_constraint(result: SimulationResult) -> None:
    script = generate_script_template(result)
    tampered = NarrationScript(
        hook=script.hook,
        segments=script.segments[:3],
        finale=script.finale,
        cta=script.cta,
    )
    with pytest.raises(ScriptValidationError):
        validate_script(tampered, result)


def test_llm_failure_falls_back_to_template(
    result: SimulationResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 指向一个必然连不上的地址：LLM 路径失败后必须回退模板且结果与模板一致。
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "fake-model")
    script = asyncio.run(generate_script(result))
    assert script.model_dump_json() == generate_script_template(result).model_dump_json()


def test_no_llm_config_uses_template(
    result: SimulationResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    script = asyncio.run(generate_script(result))
    assert script.model_dump_json() == generate_script_template(result).model_dump_json()
