from __future__ import annotations

import re
from collections import Counter
from datetime import date

from stock_video_generator.models import (
    CorporateAction,
    DataValidationResult,
    HistoryBar,
    Instrument,
    NonTradingDayPolicy,
)


def validate_market_data(
    instrument: Instrument,
    bars: list[HistoryBar],
    actions: list[CorporateAction],
    *,
    requested_start: date,
    requested_end: date,
    non_trading_day_policy: NonTradingDayPolicy | None = None,
) -> DataValidationResult:
    warnings: list[str] = []
    errors: list[str] = []

    if not bars:
        return DataValidationResult(
            valid=False,
            errors=["行情数据为空，无法执行回测。"],
            trading_days=0,
        )

    dates = [bar.date for bar in bars]
    symbol = instrument.symbol.upper()
    exchange_ok = (
        (instrument.exchange == "SSE" and symbol.endswith(".SH"))
        or (instrument.exchange == "SZSE" and symbol.endswith(".SZ"))
        or (instrument.exchange == "BSE" and symbol.endswith(".BJ"))
        or (instrument.exchange == "HKEX" and symbol.endswith(".HK"))
        or (instrument.market.value == "US" and not re.search(r"\.(SH|SZ|BJ|HK)$", symbol))
        or (instrument.market.value == "CRYPTO" and instrument.exchange == "CRYPTO")
    )
    if not exchange_ok:
        errors.append(f"股票代码 {instrument.symbol} 与交易所 {instrument.exchange} 不一致。")
    if dates != sorted(dates):
        errors.append("行情日期不是升序。")

    duplicate_dates = [str(value) for value, count in Counter(dates).items() if count > 1]
    if duplicate_dates:
        errors.append(f"存在重复交易日期：{', '.join(duplicate_dates[:10])}。")

    for index, bar in enumerate(bars):
        if bar.close <= 0 or bar.open <= 0 or bar.high <= 0 or bar.low <= 0:
            errors.append(f"{bar.date} 存在小于或等于零的价格。")
        if bar.currency != instrument.currency:
            errors.append(
                f"{bar.date} 行情币种 {bar.currency} 与股票币种 {instrument.currency} 不一致。"
            )
        if index:
            gap_days = (bar.date - bars[index - 1].date).days
            if gap_days > 14:
                warnings.append(
                    f"{bars[index - 1].date} 至 {bar.date} 存在 {gap_days} 天数据间隔。"
                )
            previous_close = bars[index - 1].close
            jump = bar.close / previous_close
            if jump >= 5 or jump <= 0.2:
                warnings.append(
                    f"{bar.date} 收盘价相对上一交易日出现明显异常跳变 "
                    f"({(jump - 1) * 100:.2f}%)，请核对拆合股事件。"
                )

    duplicate_actions = [
        f"{event_date}:{event_type}"
        for (event_date, event_type), count in Counter(
            (action.ex_date, action.event_type) for action in actions
        ).items()
        if count > 1
    ]
    if duplicate_actions:
        errors.append(f"存在重复公司行为：{', '.join(duplicate_actions[:10])}。")

    for action in actions:
        if action.currency != instrument.currency:
            errors.append(
                f"{action.ex_date} 公司行为币种 {action.currency} 与股票币种 "
                f"{instrument.currency} 不一致。"
            )

    data_start = min(dates)
    data_end = max(dates)
    if requested_start < data_start:
        start_gap = (data_start - requested_start).days
        if non_trading_day_policy == NonTradingDayPolicy.NEXT_TRADING_DAY:
            warnings.append(
                f"买入日期 {requested_start} 早于可用行情起始日 {data_start}，"
                "将从上市后的首个可用交易日开始。"
            )
        elif start_gap <= 7 and non_trading_day_policy is None:
            warnings.append(
                f"买入日期 {requested_start} 不是交易日，将按请求策略处理；"
                f"下一可用交易日为 {data_start}。"
            )
        else:
            errors.append(
                f"买入日期 {requested_start} 早于可用行情起始日 {data_start}，"
                "股票可能尚未上市。"
            )
    if requested_end > data_end:
        warnings.append(
            f"请求结束日期 {requested_end} 晚于数据结束日 {data_end}，将以最后可用交易日为准。"
        )
        if (requested_end - data_end).days > 14:
            warnings.append("最近可用行情距请求结束日超过 14 天，股票可能长期停牌或已退市。")

    return DataValidationResult(
        valid=not errors,
        warnings=list(dict.fromkeys(warnings)),
        errors=list(dict.fromkeys(errors)),
        data_start=data_start,
        data_end=data_end,
        trading_days=len(bars),
    )
