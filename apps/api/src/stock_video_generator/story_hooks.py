from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from stock_video_generator.models import Market, SimulationResult


@dataclass(frozen=True)
class StoryHookContext:
    year: int
    amount: str
    asset: str
    hold_years: int
    return_pct: float
    drawdown_pct: float
    early_loss_pct: float
    trough_amount: str
    market: Market


@dataclass(frozen=True)
class StoryHookTemplate:
    template_id: str
    category: str
    text: str
    eligible: Callable[[StoryHookContext], bool]


@dataclass(frozen=True)
class StoryHookSelection:
    template_id: str
    category: str
    text: str
    display_asset_name: str


def _always(_: StoryHookContext) -> bool:
    return True


TOP_STORY_HOOK_TEMPLATES: tuple[StoryHookTemplate, ...] = (
    StoryHookTemplate(
        "generic_brother", "generic", "你兄弟在{year}年拿{amount}买了{asset}", _always
    ),
    StoryHookTemplate(
        "generic_if_you", "generic", "如果你在{year}年把{amount}投进{asset}", _always
    ),
    StoryHookTemplate("generic_all_in", "generic", "{year}年，{amount}只买{asset}会怎样", _always),
    StoryHookTemplate(
        "generic_later", "generic", "当年拿{amount}买{asset}的人，后来怎么样了", _always
    ),
    StoryHookTemplate(
        "long_never_sell",
        "long_hold",
        "{amount}买入{asset}，一直不卖会发生什么",
        lambda c: c.hold_years >= 5,
    ),
    StoryHookTemplate(
        "long_ignore",
        "long_hold",
        "买完{asset}就不管，{hold_years}年后再看",
        lambda c: c.hold_years >= 5,
    ),
    StoryHookTemplate(
        "forgot_account",
        "forgotten",
        "你朋友拿{amount}买了{asset}，然后忘了账户",
        lambda c: c.hold_years >= 5,
    ),
    StoryHookTemplate(
        "forgot_phone",
        "forgotten",
        "他换了手机，{hold_years}年后才想起这笔{asset}",
        lambda c: c.hold_years >= 8,
    ),
    StoryHookTemplate(
        "forgot_password",
        "forgotten",
        "买完{asset}后，他把密码忘了{hold_years}年",
        lambda c: c.hold_years >= 8,
    ),
    StoryHookTemplate(
        "forgot_result",
        "forgotten",
        "这笔{asset}被遗忘了{hold_years}年，结局如何",
        lambda c: c.hold_years >= 5,
    ),
    StoryHookTemplate(
        "early_crash",
        "drawdown",
        "你刚买{asset}就遇到暴跌，还能拿住吗",
        lambda c: c.early_loss_pct <= -20,
    ),
    StoryHookTemplate(
        "drawdown_sell",
        "drawdown",
        "{asset}中途跌掉{max_drawdown}%，你会割肉吗",
        lambda c: c.drawdown_pct >= 50,
    ),
    StoryHookTemplate(
        "drawdown_trough",
        "drawdown",
        "账户一度只剩{trough_amount}，你还会等吗",
        lambda c: c.drawdown_pct >= 50,
    ),
    StoryHookTemplate(
        "drawdown_run",
        "drawdown",
        "{amount}投进{asset}后遭遇暴跌，你会跑吗",
        lambda c: c.drawdown_pct >= 60,
    ),
    StoryHookTemplate(
        "drawdown_refuse",
        "recovery",
        "身边人都劝他卖掉{asset}，他偏偏没卖",
        lambda c: c.drawdown_pct >= 50 and c.return_pct > 0,
    ),
    StoryHookTemplate(
        "drawdown_survive",
        "recovery",
        "扛过{asset}最惨的时候，后来发生了什么",
        lambda c: c.drawdown_pct >= 50 and c.return_pct > 0,
    ),
    StoryHookTemplate(
        "gain_hard_to_hold",
        "high_return",
        "{year}年买{asset}，最难的不是买，是拿住",
        lambda c: c.return_pct >= 500,
    ),
    StoryHookTemplate(
        "gain_years_test",
        "high_return",
        "这不是买点问题，是你能不能拿{hold_years}年",
        lambda c: c.return_pct >= 500 and c.hold_years >= 5,
    ),
    StoryHookTemplate(
        "gain_sell_test",
        "high_return",
        "{asset}真正的考验，是中途你卖不卖",
        lambda c: c.return_pct >= 1_000,
    ),
    StoryHookTemplate(
        "gain_ordinary_hold",
        "high_return",
        "{amount}押中{asset}，普通人能拿到最后吗",
        lambda c: c.return_pct >= 1_000,
    ),
    StoryHookTemplate(
        "gain_missed",
        "extreme_return",
        "当年没买{asset}的人，后来错过了什么",
        lambda c: c.return_pct >= 5_000,
    ),
    StoryHookTemplate(
        "gain_zero_sales",
        "extreme_return",
        "如果这笔{asset}一次都没卖，今天会怎样",
        lambda c: c.return_pct >= 5_000,
    ),
    StoryHookTemplate(
        "loss_break_even",
        "loss",
        "{year}年买入{asset}，等{hold_years}年能回本吗",
        lambda c: c.return_pct < 0 and c.hold_years >= 3,
    ),
    StoryHookTemplate(
        "loss_remaining",
        "loss",
        "{amount}买{asset}，最后到底还能剩多少",
        lambda c: c.return_pct < 0,
    ),
    StoryHookTemplate(
        "loss_hold_or_hole",
        "loss",
        "一直死扛{asset}，最后是翻身还是深坑",
        lambda c: c.return_pct < 0,
    ),
    StoryHookTemplate(
        "loss_wait",
        "loss",
        "如果这笔{asset}一直没卖，还能等到回本吗",
        lambda c: c.return_pct < 0 and c.hold_years >= 3,
    ),
    StoryHookTemplate(
        "crypto_belief",
        "crypto",
        "如果你在{year}年就相信了{asset}",
        lambda c: c.market == Market.CRYPTO,
    ),
    StoryHookTemplate(
        "crypto_cycles",
        "crypto",
        "{asset}暴涨暴跌这么多次，谁能拿到今天",
        lambda c: c.market == Market.CRYPTO and c.drawdown_pct >= 50,
    ),
    StoryHookTemplate(
        "crypto_crashes",
        "crypto",
        "买{asset}容易，扛过每轮暴跌才难",
        lambda c: c.market == Market.CRYPTO and c.drawdown_pct >= 50,
    ),
    StoryHookTemplate(
        "crypto_hold",
        "crypto",
        "你以为错过了{asset}，其实更难的是拿住",
        lambda c: c.market == Market.CRYPTO and c.return_pct >= 500,
    ),
    StoryHookTemplate(
        "crypto_open_account",
        "crypto",
        "那年{amount}买{asset}，今天敢打开账户吗",
        lambda c: c.market == Market.CRYPTO and c.hold_years >= 5,
    ),
    StoryHookTemplate(
        "crypto_extremes",
        "crypto",
        "{asset}让人暴富过，也让人绝望过，结局呢",
        lambda c: c.market == Market.CRYPTO and c.return_pct >= 1_000 and c.drawdown_pct >= 50,
    ),
)


CRYPTO_DISPLAY_NAMES = {
    "BTC-USD": "比特币",
    "ETH-USD": "以太坊",
    "DOGE-USD": "狗狗币",
    "SOL-USD": "Solana",
    "XRP-USD": "瑞波币",
    "ADA-USD": "艾达币",
    "BNB-USD": "BNB",
    "LTC-USD": "莱特币",
    "BCH-USD": "比特币现金",
    "DOT-USD": "波卡币",
    "LINK-USD": "Chainlink",
    "AVAX-USD": "Avalanche",
    "SHIB-USD": "柴犬币",
    "XLM-USD": "恒星币",
    "TRX-USD": "波场币",
}


def _display_asset_name(result: SimulationResult) -> str:
    symbol = result.instrument.symbol.upper()
    if symbol in CRYPTO_DISPLAY_NAMES:
        return CRYPTO_DISPLAY_NAMES[symbol]
    name = re.sub(r"\s+", " ", result.instrument.name).strip()
    if not re.search(r"[\u3400-\u9fff]", name):
        suffixes = (
            r"(?:Corporation|Incorporated|Inc\.?|Company|Co\.?|Limited|Ltd\.?|Holdings?|Group|PLC)$"
        )
        previous = None
        while name != previous:
            previous = name
            name = re.sub(rf"[\s,.-]+{suffixes}", "", name, flags=re.IGNORECASE).strip()
    return name or result.instrument.symbol


def _compact_amount(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100_000_000:
        scaled = value / 100_000_000
        suffix = "亿"
    elif absolute >= 10_000:
        scaled = value / 10_000
        suffix = "万"
    else:
        return f"{value:,.0f}"
    digits = 0 if abs(scaled - round(scaled)) < 0.05 else 1
    return f"{scaled:,.{digits}f}{suffix}"


def build_story_hook(
    result: SimulationResult,
    *,
    excluded_template_ids: set[str] | None = None,
    preferred_template_id: str | None = None,
) -> StoryHookSelection:
    first = result.summary.actual_buy_date
    last = result.series[-1].date
    hold_years = max(1, round((last - first).days / 365.25))
    early_end = first + timedelta(days=365)
    early_points = [point for point in result.series if point.date <= early_end]
    early_loss_pct = min(
        (point.total_return_pct for point in early_points),
        default=0.0,
    )
    context = StoryHookContext(
        year=first.year,
        amount=_compact_amount(float(result.assumptions["initial_capital"])),
        asset=_display_asset_name(result),
        hold_years=hold_years,
        return_pct=result.summary.total_return_pct,
        drawdown_pct=abs(result.summary.max_drawdown_pct),
        early_loss_pct=early_loss_pct,
        trough_amount=_compact_amount(result.summary.worst_value),
        market=result.instrument.market,
    )
    eligible = [template for template in TOP_STORY_HOOK_TEMPLATES if template.eligible(context)]
    specialized = [template for template in eligible if template.category != "generic"]
    available = specialized or eligible
    preferred = next(
        (template for template in available if template.template_id == preferred_template_id),
        None,
    )
    excluded = excluded_template_ids or set()
    candidates = [
        template for template in available if template.template_id not in excluded
    ] or available
    seed = (
        f"{result.instrument.symbol.upper()}|{first.isoformat()}|"
        f"{result.summary.total_return_pct:.4f}|top-story-hook-v1"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    chosen = preferred or candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
    values = {
        "year": context.year,
        "amount": context.amount,
        "asset": context.asset,
        "hold_years": context.hold_years,
        "max_drawdown": round(context.drawdown_pct),
        "trough_amount": context.trough_amount,
    }
    return StoryHookSelection(
        template_id=chosen.template_id,
        category=chosen.category,
        text=chosen.text.format(**values),
        display_asset_name=context.asset,
    )
