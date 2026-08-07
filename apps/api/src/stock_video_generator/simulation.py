from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from decimal import ROUND_FLOOR, Decimal, getcontext
from uuid import uuid4

from stock_video_generator.errors import MarketDataValidationError, SimulationInputError
from stock_video_generator.models import (
    CorporateAction,
    CorporateActionType,
    DataValidationResult,
    DividendPolicy,
    HistoryBar,
    Instrument,
    NonTradingDayPolicy,
    ShareMode,
    SimulationEvent,
    SimulationPoint,
    SimulationRequest,
    SimulationResult,
    SimulationSummary,
    SourceMetadata,
)

getcontext().prec = 34


def _decimal(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def _output(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.00000001")))


def _calculate_purchase(
    available_cash: Decimal,
    price: Decimal,
    request: SimulationRequest,
    market_lot: int,
) -> tuple[Decimal, Decimal, Decimal]:
    fee = request.fee_policy
    if available_cash <= 0 or price <= 0:
        return Decimal(0), Decimal(0), available_cash

    rate = _decimal(fee.commission_rate + fee.stamp_duty_rate) if fee.enabled else Decimal(0)
    minimum_commission = _decimal(fee.minimum_commission) if fee.enabled else Decimal(0)

    if fee.enabled and minimum_commission > 0:
        rate_based_shares = available_cash / (price * (Decimal(1) + rate))
        gross = rate_based_shares * price
        commission = gross * _decimal(fee.commission_rate)
        if commission < minimum_commission:
            fixed_plus_stamp = minimum_commission + gross * _decimal(fee.stamp_duty_rate)
            candidate = max(Decimal(0), (available_cash - fixed_plus_stamp) / price)
        else:
            candidate = rate_based_shares
    else:
        candidate = available_cash / (price * (Decimal(1) + rate))

    unit = Decimal(1)
    if request.share_mode == ShareMode.MARKET_LOT:
        unit = Decimal(market_lot)
    if request.share_mode != ShareMode.FRACTIONAL:
        candidate = (candidate / unit).to_integral_value(rounding=ROUND_FLOOR) * unit

    def costs(shares: Decimal) -> tuple[Decimal, Decimal]:
        gross_value = shares * price
        if not fee.enabled or shares <= 0:
            return gross_value, Decimal(0)
        commission_value = max(
            gross_value * _decimal(fee.commission_rate),
            minimum_commission,
        )
        fee_value = commission_value + gross_value * _decimal(fee.stamp_duty_rate)
        return gross_value, fee_value

    gross, fees = costs(candidate)
    while candidate > 0 and gross + fees > available_cash:
        decrement = unit if request.share_mode != ShareMode.FRACTIONAL else Decimal("0.00000001")
        candidate = max(Decimal(0), candidate - decrement)
        gross, fees = costs(candidate)

    remaining = available_cash - gross - fees
    return candidate, fees, remaining


def _resolve_buy_index(
    bars: list[HistoryBar],
    requested_date: date,
    policy: NonTradingDayPolicy,
) -> int:
    exact = next((index for index, bar in enumerate(bars) if bar.date == requested_date), None)
    if exact is not None:
        return exact

    if policy == NonTradingDayPolicy.REJECT:
        raise SimulationInputError(f"买入日期 {requested_date} 不是交易日。")

    if policy == NonTradingDayPolicy.NEXT_TRADING_DAY:
        next_index = next(
            (index for index, bar in enumerate(bars) if bar.date > requested_date),
            None,
        )
        if next_index is None:
            raise SimulationInputError(f"买入日期 {requested_date} 之后没有可用交易日。")
        return next_index

    previous_indexes = [index for index, bar in enumerate(bars) if bar.date < requested_date]
    if not previous_indexes:
        raise SimulationInputError(f"买入日期 {requested_date} 之前没有可用交易日。")
    return previous_indexes[-1]


def simulate_buy_and_hold(
    *,
    request: SimulationRequest,
    instrument: Instrument,
    bars: list[HistoryBar],
    actions: list[CorporateAction],
    validation: DataValidationResult,
    source: SourceMetadata,
    simulation_id: str | None = None,
) -> SimulationResult:
    if not validation.valid:
        raise MarketDataValidationError(
            "行情校验失败，已阻止回测和视频生成。",
            detail="；".join(validation.errors),
        )
    if request.capital_currency != instrument.currency:
        raise SimulationInputError(
            f"初始资金币种 {request.capital_currency} 与股票币种 {instrument.currency} 不一致。"
        )

    eligible_bars = [
        bar for bar in bars if request.end_date == "latest" or bar.date <= request.end_date
    ]
    buy_index = _resolve_buy_index(
        eligible_bars,
        request.buy_date,
        request.non_trading_day_policy,
    )
    eligible_bars = eligible_bars[buy_index:]
    if not eligible_bars:
        raise SimulationInputError("买入日期之后没有行情数据。")

    buy_bar = eligible_bars[0]
    buy_price = _decimal(buy_bar.open if request.execution_price.value == "open" else buy_bar.close)
    initial_capital = _decimal(request.initial_capital)
    shares, initial_fee, cash = _calculate_purchase(
        initial_capital,
        buy_price,
        request,
        instrument.market_lot,
    )
    if shares <= 0:
        raise SimulationInputError("初始资金在价格、交易单位和手续费约束下不足以买入一股。")

    initial_shares = shares
    total_fees = initial_fee
    dividend_total = Decimal(0)
    events: list[SimulationEvent] = [
        SimulationEvent(
            date=buy_bar.date,
            event_type="buy",
            description=f"按 {request.execution_price.value} 价格买入",
            shares_before=0,
            shares_after=_output(shares),
            cash_before=_output(initial_capital),
            cash_after=_output(cash),
            amount=_output(shares * buy_price),
            source=buy_bar.source,
        )
    ]

    actions_by_date: dict[date, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        if buy_bar.date <= action.ex_date <= eligible_bars[-1].date:
            actions_by_date[action.ex_date].append(action)

    points: list[SimulationPoint] = []
    running_peak = Decimal(0)
    max_drawdown = Decimal(0)

    for bar in eligible_bars:
        close = _decimal(bar.close)
        day_actions = sorted(
            actions_by_date.get(bar.date, []),
            key=lambda action: 0 if action.event_type == CorporateActionType.SPLIT else 1,
        )
        for action in day_actions:
            shares_before = shares
            cash_before = cash
            if action.event_type == CorporateActionType.SPLIT:
                shares *= _decimal(action.split_ratio or 1)
                events.append(
                    SimulationEvent(
                        date=bar.date,
                        event_type="split",
                        description=f"拆合股比例 {action.split_ratio:g}",
                        shares_before=_output(shares_before),
                        shares_after=_output(shares),
                        cash_before=_output(cash_before),
                        cash_after=_output(cash),
                        source=action.source,
                    )
                )
                continue

            dividend_cash = shares * _decimal(action.dividend_per_share or 0)
            dividend_total += dividend_cash
            if request.dividend_policy == DividendPolicy.IGNORE:
                continue
            if request.dividend_policy == DividendPolicy.CASH:
                cash += dividend_cash
            else:
                reinvested_shares, reinvest_fee, dividend_remainder = _calculate_purchase(
                    dividend_cash,
                    close,
                    request,
                    instrument.market_lot,
                )
                shares += reinvested_shares
                cash += dividend_remainder
                total_fees += reinvest_fee
            events.append(
                SimulationEvent(
                    date=bar.date,
                    event_type="dividend",
                    description=(
                        "现金分红"
                        if request.dividend_policy == DividendPolicy.CASH
                        else "分红再投资"
                    ),
                    shares_before=_output(shares_before),
                    shares_after=_output(shares),
                    cash_before=_output(cash_before),
                    cash_after=_output(cash),
                    amount=_output(dividend_cash),
                    source=action.source,
                )
            )

        portfolio_value = shares * close + cash
        running_peak = max(running_peak, portfolio_value)
        drawdown = portfolio_value / running_peak - Decimal(1)
        max_drawdown = min(max_drawdown, drawdown)
        total_return = portfolio_value / initial_capital - Decimal(1)
        points.append(
            SimulationPoint(
                date=bar.date,
                close=_output(close),
                shares=_output(shares),
                cash=_output(cash),
                portfolio_value=_output(portfolio_value),
                total_return_pct=_output(total_return * 100),
                drawdown_pct=_output(drawdown * 100),
            )
        )

    values = [_decimal(point.portfolio_value) for point in points]
    final_value = values[-1]
    if not math.isfinite(float(final_value)):
        raise SimulationInputError("回测结果不是有限数值，已阻止输出。")

    return SimulationResult(
        simulation_id=simulation_id or str(uuid4()),
        instrument=instrument,
        assumptions=request.model_dump(mode="json"),
        source=source,
        validation=validation,
        summary=SimulationSummary(
            actual_buy_date=buy_bar.date,
            buy_price=_output(buy_price),
            initial_shares=_output(initial_shares),
            final_shares=_output(shares),
            final_cash=_output(cash),
            final_value=_output(final_value),
            total_return_pct=_output((final_value / initial_capital - Decimal(1)) * 100),
            max_drawdown_pct=_output(max_drawdown * 100),
            best_value=_output(max(values)),
            worst_value=_output(min(values)),
            dividend_total=_output(dividend_total),
            total_fees=_output(total_fees),
        ),
        events=events,
        series=points,
    )
