"""自动选题：股票池配置、戏剧性评分与选题队列。

所有评分函数都是纯函数，接受注入的历史行情，离线可测。
只有 `TopicSelector.fetch_history` 触碰 MarketDataService（走磁盘缓存）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select

from stock_video_generator.database import (
    Database,
    PipelineRunRecord,
    PipelineStatus,
    StoryCandidateRecord,
    StoryCandidateStatus,
    TopicRecord,
    TopicStatus,
    UniverseRecord,
    now_utc,
)
from stock_video_generator.errors import UniverseUnavailableError
from stock_video_generator.models import (
    CorporateAction,
    CorporateActionType,
    HistoryBar,
    Market,
)

logger = logging.getLogger(__name__)

# 四类戏剧性角度（键用于持久化与策略权重，标签用于展示）
ANGLE_SURGE = "surge"  # 暴涨神话：区间最大涨幅大
ANGLE_CRASH = "crash"  # 暴跌教训：最大回撤深且结局差
ANGLE_ROLLERCOASTER = "rollercoaster"  # 过山车：涨幅大且回撤深
ANGLE_COMPOUND = "compound"  # 长跑赢家：年化高且回撤小

ANGLE_LABELS = {
    ANGLE_SURGE: "暴涨神话",
    ANGLE_CRASH: "暴跌教训",
    ANGLE_ROLLERCOASTER: "过山车",
    ANGLE_COMPOUND: "长跑赢家",
}

ALL_ANGLES = (ANGLE_SURGE, ANGLE_CRASH, ANGLE_ROLLERCOASTER, ANGLE_COMPOUND)

DEFAULT_ANGLE_WEIGHTS = {
    ANGLE_SURGE: 30,
    ANGLE_CRASH: 25,
    ANGLE_ROLLERCOASTER: 25,
    ANGLE_COMPOUND: 20,
}


class TopicDirective(BaseModel):
    """选题偏好（持久化在策略里）：全部留空 = 均衡随机（现状行为）。

    surge_min_pct / crash_max_pct 按「买入日 → 评分日」的前瞻收益过滤；
    两个阈值同时设置时为“或”关系（暴涨或暴跌题材都收）。
    """

    surge_min_pct: float | None = Field(default=None, ge=0)
    crash_max_pct: float | None = Field(default=None, le=0)
    prefer_angles: list[str] = Field(default_factory=list)
    prefer_symbols: list[str] = Field(default_factory=list)

    @field_validator("prefer_angles")
    @classmethod
    def angles_known(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(ALL_ANGLES)
        if unknown:
            raise ValueError(f"未知选题角度：{sorted(unknown)}")
        return list(dict.fromkeys(value))

    @field_validator("prefer_symbols")
    @classmethod
    def symbols_normalized(cls, value: list[str]) -> list[str]:
        return list(
            dict.fromkeys(item.strip().upper() for item in value if item.strip())
        )


def directive_weights(
    directive: TopicDirective | None,
    weights: dict[str, int],
) -> dict[str, int]:
    """prefer_angles 非空时把未选角度权重清零：选题只在偏好角度里出。"""
    if not directive or not directive.prefer_angles:
        return weights
    return {
        angle: (max(0, weights.get(angle, 0)) if angle in directive.prefer_angles else 0)
        for angle in ALL_ANGLES
    }


def passes_directive(
    angle: str,
    forward_return_pct: float,
    directive: TopicDirective | None,
) -> bool:
    """前瞻收益（买入日 → 评分日）是否满足偏好；双阈值同时设置时为“或”。"""
    if directive is None:
        return True
    if directive.prefer_angles and angle not in directive.prefer_angles:
        return False
    surge_set = directive.surge_min_pct is not None
    crash_set = directive.crash_max_pct is not None
    if surge_set or crash_set:
        surge_ok = surge_set and forward_return_pct >= float(directive.surge_min_pct)
        crash_ok = crash_set and forward_return_pct <= float(directive.crash_max_pct)
        return bool(surge_ok or crash_ok)
    return True

SCORING_LOOKBACK_YEARS = 10
LONG_HORIZON_LOOKBACK_YEARS = 40
COOLDOWN_DAYS = 90
DEFAULT_STORY_ANCHOR_YEARS = (5, 8, 10, 15, 20, 25, 30)
MIN_STORY_HOLD_YEARS = 4.0
CRYPTO_MIN_STORY_HOLD_YEARS = 0.25
MAX_STORIES_PER_ASSET = 4

# 每年按 252 个交易日折算年化指标
TRADING_DAYS_PER_YEAR = 252


def years_ago(day: date, years: int) -> date:
    """往前推 N 年；2 月 29 日回退到 2 月 28 日。"""
    try:
        return date(day.year - years, day.month, day.day)
    except ValueError:
        return date(day.year - years, day.month, day.day - 1)


class UniverseEntry(BaseModel):
    symbol: str
    name: str
    market: Market
    angle_hint: str | None = Field(default=None)

    def model_post_init(self, __context: object, /) -> None:
        if self.angle_hint is not None and self.angle_hint not in ALL_ANGLES:
            raise ValueError(f"未知 angle_hint：{self.angle_hint}")


class StoryAsset(BaseModel):
    symbol: str
    priority: int = Field(default=5, ge=1, le=10)
    min_years: float = Field(default=MIN_STORY_HOLD_YEARS, ge=0.1, le=30)
    anchor_years: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


def load_story_assets(path: Path) -> dict[str, StoryAsset]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("读取长线故事标的配置失败：%s", path)
        return {}
    if not isinstance(raw, list):
        return {}
    assets: dict[str, StoryAsset] = {}
    for item in raw:
        try:
            asset = StoryAsset.model_validate(item)
        except ValidationError:
            logger.warning("忽略无效的长线故事标的配置：%r", item)
            continue
        assets[asset.symbol] = asset
    return assets


def load_universe(path: Path) -> list[UniverseEntry]:
    """加载股票池配置；文件缺失或非法时如实报错，绝不内置假股票。"""
    if not path.is_file():
        raise UniverseUnavailableError(
            f"股票池配置文件不存在：{path}",
            detail="自动选题要求 data/universe.json 存在且为合法 JSON 数组。",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UniverseUnavailableError(
            f"股票池配置文件无法解析：{path}",
            detail=str(exc),
        ) from exc
    if not isinstance(raw, list) or not raw:
        raise UniverseUnavailableError(
            f"股票池配置文件必须是非空 JSON 数组：{path}",
        )
    entries: list[UniverseEntry] = []
    errors: list[str] = []
    for index, item in enumerate(raw):
        try:
            entries.append(UniverseEntry.model_validate(item))
        except ValidationError as exc:
            errors.append(f"第 {index + 1} 项：{exc.errors()[0]['msg']}")
    if errors:
        raise UniverseUnavailableError(
            "股票池配置存在非法条目。",
            detail="；".join(errors[:5]),
        )
    return entries


@dataclass(frozen=True)
class DramaMetrics:
    """由一段日线历史计算出的戏剧性指标（全部确定性）。"""

    trading_days: int
    span_days: int
    max_gain: float  # 低点到高点的区间最大涨幅（小数，1.0 = +100%）
    runup_trough_date: date  # 最大涨幅起点的低点日期（真实交易日）
    runup_peak_date: date  # 最大涨幅终点的高点日期（真实交易日）
    runup_days: int  # 最大涨幅所用天数（区分暴涨与慢牛）
    max_drawdown: float  # 最大回撤（正数小数，0.5 = 腰斩）
    drawdown_peak_date: date  # 最大回撤起点的高点日期（真实交易日）
    drawdown_trough_date: date  # 最大回撤终点的低点日期（真实交易日）
    annual_volatility: float  # 年化波动率（日收益标准差 × √252）
    annual_return: float  # 全程年化收益（小数）
    total_return: float  # 全程总收益（小数）
    final_drawdown: float  # 终点相对历史最高点的回撤（结局好坏）


def compute_drama_metrics(bars: list[HistoryBar]) -> DramaMetrics:
    if len(bars) < 2:
        raise ValueError("戏剧性评分至少需要 2 根 K 线。")
    ordered = sorted(bars, key=lambda bar: bar.date)

    # 区间最大涨幅：到当前为止的最低收盘价 → 当前收盘价
    min_close = ordered[0].close
    min_date = ordered[0].date
    max_gain = 0.0
    runup_trough_date = ordered[0].date
    runup_peak_date = ordered[0].date
    # 最大回撤：到当前为止的最高收盘价 → 当前收盘价
    max_close = ordered[0].close
    max_close_date = ordered[0].date
    max_drawdown = 0.0
    drawdown_peak_date = ordered[0].date
    drawdown_trough_date = ordered[0].date

    daily_returns: list[float] = []
    previous_close = ordered[0].close
    for bar in ordered[1:]:
        daily_returns.append(math.log(bar.close / previous_close))
        previous_close = bar.close
        # <=：同价时记录最近一次低点/高点，使题材锚点更贴近实际行情阶段。
        if bar.close <= min_close:
            min_close = bar.close
            min_date = bar.date
        gain = bar.close / min_close - 1
        if gain > max_gain:
            max_gain = gain
            runup_trough_date = min_date
            runup_peak_date = bar.date
        if bar.close >= max_close:
            max_close = bar.close
            max_close_date = bar.date
        drawdown = 1 - bar.close / max_close
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            drawdown_peak_date = max_close_date
            drawdown_trough_date = bar.date

    if daily_returns:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean) ** 2 for value in daily_returns) / len(daily_returns)
        annual_volatility = math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)
    else:
        annual_volatility = 0.0

    first_close = ordered[0].close
    last_close = ordered[-1].close
    total_return = last_close / first_close - 1
    span_days = max(1, (ordered[-1].date - ordered[0].date).days)
    annual_return = (last_close / first_close) ** (365.25 / span_days) - 1
    final_drawdown = 1 - last_close / max_close

    return DramaMetrics(
        trading_days=len(ordered),
        span_days=span_days,
        max_gain=max_gain,
        runup_trough_date=runup_trough_date,
        runup_peak_date=runup_peak_date,
        runup_days=(runup_peak_date - runup_trough_date).days,
        max_drawdown=max_drawdown,
        drawdown_peak_date=drawdown_peak_date,
        drawdown_trough_date=drawdown_trough_date,
        annual_volatility=annual_volatility,
        annual_return=annual_return,
        total_return=total_return,
        final_drawdown=final_drawdown,
    )


def adjust_bars_for_splits(
    bars: list[HistoryBar],
    actions: list[CorporateAction],
) -> list[HistoryBar]:
    """将未复权 OHLC 调整到当前股本口径，仅用于选题评分。

    正式回测仍使用未复权行情并逐日执行真实拆合股事件。这里调整价格是为了
    防止拆股造成的机械降价被误判为暴跌，同时保证十年收益和题材评分口径一致。
    """
    splits = [
        (action.ex_date, float(action.split_ratio))
        for action in actions
        if action.event_type == CorporateActionType.SPLIT
        and action.split_ratio is not None
        and not math.isclose(float(action.split_ratio), 1.0)
    ]
    if not splits:
        return sorted(bars, key=lambda bar: bar.date)

    adjusted: list[HistoryBar] = []
    for bar in sorted(bars, key=lambda item: item.date):
        factor = math.prod(
            ratio for ex_date, ratio in splits if bar.date < ex_date
        )
        if math.isclose(factor, 1.0):
            adjusted.append(bar)
            continue
        adjusted.append(
            bar.model_copy(
                update={
                    "open": bar.open / factor,
                    "high": bar.high / factor,
                    "low": bar.low / factor,
                    "close": bar.close / factor,
                }
            )
        )
    return adjusted


# 最大涨幅在多少天内完成才算“暴涨”（超过视为慢牛，归入长跑叙事）
FAST_RUNUP_DAYS = 730


def angle_scores(metrics: DramaMetrics) -> dict[str, float]:
    """把指标映射为四类角度的原始得分（越大越戏剧化，全部 ≥ 0）。

    每类都有门槛与惩罚，保证各自典型形态下能胜出：
    - 暴涨神话：涨得快（≤2 年）且守得住；慢牛大幅降权
    - 暴跌教训：回撤深且结局差；结局好的回撤视为已修复，降权
    - 过山车：涨幅与回撤都足够大，否则降权
    - 长跑赢家：年化高且回撤小
    """
    surge = metrics.max_gain * (1 - metrics.max_drawdown)
    if metrics.runup_days > FAST_RUNUP_DAYS:
        surge = 0.0  # 慢牛不是暴涨，归入长跑叙事
    ending_is_bad = metrics.final_drawdown >= 0.3 or metrics.annual_return <= 0
    crash = metrics.max_drawdown if ending_is_bad else metrics.max_drawdown * 0.25
    rollercoaster = 2 * min(metrics.max_gain, metrics.max_drawdown)
    if (
        metrics.max_drawdown < 0.4
        or metrics.max_gain < 1.0
        or metrics.final_drawdown > 0.6  # 跌下去没回来是暴跌，不是过山车
    ):
        rollercoaster *= 0.1
    compound = (
        2 * max(0.0, min(metrics.annual_return, 1.0)) * (1 - metrics.max_drawdown)
    )
    return {
        ANGLE_SURGE: surge,
        ANGLE_CRASH: crash,
        ANGLE_ROLLERCOASTER: rollercoaster,
        ANGLE_COMPOUND: compound,
    }


def choose_angle(
    metrics: DramaMetrics,
    weights: dict[str, int],
    angle_hint: str | None = None,
) -> tuple[str, float]:
    """按策略权重确定性选题：返回 (角度, 加权得分)。

    得分 = 原始得分 × 权重占比；权重越高的角度越容易胜出，
    从而让队列构成贴近策略配比。angle_hint 存在时强制该角度。
    """
    scores = angle_scores(metrics)
    total_weight = sum(max(0, weights.get(angle, 0)) for angle in ALL_ANGLES)
    if total_weight <= 0:
        total_weight = len(ALL_ANGLES)
        weights = {angle: 1 for angle in ALL_ANGLES}
    if angle_hint is not None:
        return angle_hint, scores[angle_hint] * max(0, weights.get(angle_hint, 0)) / total_weight
    best_angle = ANGLE_SURGE
    best_score = -1.0
    for angle in ALL_ANGLES:
        weighted = scores[angle] * max(0, weights.get(angle, 0)) / total_weight
        if weighted > best_score:
            best_score = weighted
            best_angle = angle
    return best_angle, best_score


def pick_buy_date(bars: list[HistoryBar]) -> date:
    """统一返回十年窗口内第一个真实交易日。

    调用方先把行情截取到「截止日往前十年」之后，因此这里等价于：
    十年前同日若非交易日则顺延；上市不足十年则取上市后的首个交易日。
    题材分类只影响叙事标签，不再改变投资起点。
    """
    ordered = sorted(bars, key=lambda bar: bar.date)
    if not ordered:
        raise ValueError("固定买入日要求至少一根 K 线。")
    return ordered[0].date


@dataclass(frozen=True)
class ScoredTopic:
    entry: UniverseEntry
    buy_date: date
    angle: str
    drama_score: float
    forward_return_pct: float  # 买入日 → 评分日的前瞻收益（%），供选题偏好过滤与展示


@dataclass(frozen=True)
class LongHorizonStory:
    entry: UniverseEntry
    buy_date: date
    end_date: date
    story_type: str
    angle: str
    hold_years: float
    start_price: float
    end_price: float
    forward_return_pct: float
    max_drawdown_pct: float
    quality_score: float
    content_score: float

    @property
    def story_key(self) -> str:
        # Month-level windows prevent nearby trading days becoming duplicate stories.
        return f"{self.entry.symbol.upper()}|{self.buy_date:%Y-%m}|{self.story_type}"


def story_key(symbol: str, buy_date: str | date, story_type: str) -> str:
    parsed = date.fromisoformat(buy_date) if isinstance(buy_date, str) else buy_date
    return f"{symbol.upper()}|{parsed:%Y-%m}|{story_type}"


def story_data_quality(
    bars: list[HistoryBar],
    *,
    min_hold_years: float = MIN_STORY_HOLD_YEARS,
) -> tuple[float, list[str]]:
    """Return a deterministic quality score and hard rejection reasons."""
    ordered = sorted(bars, key=lambda item: item.date)
    if len(ordered) < 2:
        return 0.0, ["有效行情不足"]
    span_days = (ordered[-1].date - ordered[0].date).days
    if span_days < max(30, int(min_hold_years * 365.25)):
        return 0.0, ["持有周期不足当前市场要求"]
    expected_min = max(60, int(span_days / 365.25 * 120))
    if len(ordered) < expected_min:
        return 0.0, ["历史行情覆盖率不足"]
    issues: list[str] = []
    large_gaps = 0
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if (current.date - previous.date).days > 21:
            large_gaps += 1
        ratio = current.close / previous.close
        if ratio >= 4.0 or ratio <= 0.25:
            issues.append(f"{current.date.isoformat()} 存在未解释的异常跳价")
            break
    if issues:
        return 0.0, issues
    coverage = min(1.0, len(ordered) / max(1, span_days / 365.25 * 240))
    quality = 70.0 + coverage * 30.0 - min(20.0, large_gaps * 2.0)
    return max(0.0, round(quality, 2)), []


def _first_bar_on_or_after(bars: list[HistoryBar], target: date) -> HistoryBar | None:
    return next((bar for bar in bars if bar.date >= target), None)


def _story_anchor_bars(
    bars: list[HistoryBar],
    as_of: date,
    asset: StoryAsset | None,
) -> list[tuple[HistoryBar, str]]:
    ordered = sorted((bar for bar in bars if bar.date <= as_of), key=lambda item: item.date)
    if not ordered:
        return []
    anchors: list[tuple[HistoryBar, str]] = []
    years = asset.anchor_years if asset and asset.anchor_years else list(DEFAULT_STORY_ANCHOR_YEARS)
    for hold_years in years:
        bar = _first_bar_on_or_after(ordered, years_ago(as_of, hold_years))
        if bar is not None:
            anchors.append((bar, f"horizon_{hold_years}y"))

    earliest_span = (ordered[-1].date - ordered[0].date).days / 365.25
    if earliest_span >= (asset.min_years if asset else MIN_STORY_HOLD_YEARS):
        anchors.append((ordered[0], "earliest_history"))

    # One lowest close per four-year block captures recognisable cycle bottoms
    # without hard-coding crash dates for a particular country or asset class.
    blocks: dict[int, list[HistoryBar]] = {}
    base_year = ordered[0].date.year
    for bar in ordered:
        blocks.setdefault((bar.date.year - base_year) // 4, []).append(bar)
    for block in blocks.values():
        trough = min(block, key=lambda item: item.close)
        hold_years = (ordered[-1].date - trough.date).days / 365.25
        if hold_years >= (asset.min_years if asset else MIN_STORY_HOLD_YEARS):
            anchors.append((trough, "cycle_trough"))

    unique: dict[tuple[str, str], tuple[HistoryBar, str]] = {}
    for bar, kind in anchors:
        unique[(bar.date.strftime("%Y-%m"), kind)] = (bar, kind)
    return list(unique.values())


class TopicSelector:
    """选题队列管理：评分、冷却、补水位。"""

    def __init__(self, settings, database: Database, market_data) -> None:
        self.settings = settings
        self.database = database
        self.market_data = market_data
        self.universe_path = settings.data_dir / "universe.json"
        self.story_assets_path = settings.data_dir / "story_assets.json"

    def story_assets(self) -> dict[str, StoryAsset]:
        return load_story_assets(self.story_assets_path)

    def universe_entries(self) -> list[UniverseEntry]:
        """Read the durable dynamic universe, falling back to the curated seed."""
        with self.database.session() as session:
            records = session.scalars(
                select(UniverseRecord).where(
                    UniverseRecord.active.is_(True),
                    UniverseRecord.eligible.is_(True),
                )
            ).all()
        if records:
            return [
                UniverseEntry(
                    symbol=record.symbol,
                    name=record.name,
                    market=Market(record.market),
                    angle_hint=record.angle_hint,
                )
                for record in records
            ]
        return load_universe(self.universe_path)

    @staticmethod
    def _balanced_candidates(
        entries: list[UniverseEntry],
        *,
        as_of: date,
        limit: int,
        preferred_symbols: set[str] | None = None,
    ) -> list[UniverseEntry]:
        """Take a bounded, market-balanced slice so refill never scans thousands."""
        preferred_symbols = preferred_symbols or set()
        preferred = [
            entry for entry in entries if entry.symbol.upper() in preferred_symbols
        ]
        remaining = [
            entry for entry in entries if entry.symbol.upper() not in preferred_symbols
        ]

        def stable_key(entry: UniverseEntry) -> str:
            return hashlib.sha256(
                f"{as_of.isoformat()}:{entry.symbol}".encode()
            ).hexdigest()

        buckets: dict[Market, list[UniverseEntry]] = {}
        for entry in remaining:
            buckets.setdefault(entry.market, []).append(entry)
        for bucket in buckets.values():
            bucket.sort(key=stable_key)

        balanced: list[UniverseEntry] = []
        enabled_markets = [market for market in Market if buckets.get(market)]
        while enabled_markets and len(preferred) + len(balanced) < limit:
            next_markets: list[Market] = []
            for market in enabled_markets:
                bucket = buckets[market]
                if bucket:
                    balanced.append(bucket.pop())
                if bucket:
                    next_markets.append(market)
                if len(preferred) + len(balanced) >= limit:
                    break
            enabled_markets = next_markets
        return (preferred + balanced)[:limit]

    async def fetch_history(self, symbol: str, as_of: date) -> list[HistoryBar]:
        provider_name = self.market_data.provider_name_for_symbol(symbol)
        provider = self.market_data.providers[provider_name]
        start = years_ago(as_of, LONG_HORIZON_LOOKBACK_YEARS)
        history_result, actions_result = await asyncio.gather(
            self.market_data.get_history(provider, symbol, start, as_of),
            self.market_data.get_actions(provider, symbol, start, as_of),
        )
        bars, _ = history_result
        actions, _ = actions_result
        return adjust_bars_for_splits(bars, actions)

    def score_story_candidates(
        self,
        entry: UniverseEntry,
        bars: list[HistoryBar],
        weights: dict[str, int],
        as_of: date,
        directive: TopicDirective | None = None,
        asset: StoryAsset | None = None,
    ) -> list[LongHorizonStory]:
        """Build several real long-horizon stories instead of one fixed 10-year cut."""
        ordered = sorted((bar for bar in bars if bar.date <= as_of), key=lambda item: item.date)
        if len(ordered) < 2:
            return []
        if (as_of - ordered[-1].date).days > 30:
            return []
        minimum_hold_years = (
            asset.min_years
            if asset
            else (
                CRYPTO_MIN_STORY_HOLD_YEARS
                if entry.market == Market.CRYPTO
                else MIN_STORY_HOLD_YEARS
            )
        )
        stories: list[LongHorizonStory] = []
        for anchor, kind in _story_anchor_bars(ordered, as_of, asset):
            window = [bar for bar in ordered if bar.date >= anchor.date]
            quality_score, issues = story_data_quality(
                window,
                min_hold_years=minimum_hold_years,
            )
            if issues:
                continue
            hold_years = (window[-1].date - window[0].date).days / 365.25
            if hold_years < minimum_hold_years:
                continue
            metrics = compute_drama_metrics(window)
            angle, drama_score = choose_angle(metrics, weights, entry.angle_hint)
            forward_pct = metrics.total_return * 100
            compelling = entry.market == Market.CRYPTO or (
                forward_pct >= 100
                or forward_pct <= -60
                or metrics.max_drawdown >= 0.75
            )
            if not compelling or not passes_directive(angle, forward_pct, directive):
                continue
            priority = asset.priority if asset else 5
            shock = math.log1p(abs(forward_pct)) * 10
            content_score = (
                priority * 8
                + shock
                + min(30.0, hold_years * 1.5)
                + metrics.max_drawdown * 15
                + min(30.0, drama_score * 10)
                + quality_score * 0.15
            )
            stories.append(
                LongHorizonStory(
                    entry=entry,
                    buy_date=window[0].date,
                    end_date=window[-1].date,
                    story_type=kind,
                    angle=angle,
                    hold_years=hold_years,
                    start_price=window[0].close,
                    end_price=window[-1].close,
                    forward_return_pct=forward_pct,
                    max_drawdown_pct=metrics.max_drawdown * 100,
                    quality_score=quality_score,
                    content_score=content_score,
                )
            )

        # Nearby anchors may describe essentially the same move. Keep the stronger one.
        stories.sort(key=lambda item: item.content_score, reverse=True)
        selected: list[LongHorizonStory] = []
        for candidate in stories:
            if any(
                abs((candidate.buy_date - existing.buy_date).days) < 300
                for existing in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= MAX_STORIES_PER_ASSET:
                break
        return selected

    def score_bars(
        self,
        entry: UniverseEntry,
        bars: list[HistoryBar],
        weights: dict[str, int],
        as_of: date,
        directive: TopicDirective | None = None,
    ) -> ScoredTopic | None:
        """纯函数评分：数据无效或不满足选题偏好的股票返回 None。"""
        if len(bars) < 2:
            return None
        target = years_ago(as_of, SCORING_LOOKBACK_YEARS)
        ordered = sorted(
            (bar for bar in bars if target <= bar.date <= as_of),
            key=lambda bar: bar.date,
        )
        if len(ordered) < 2:
            return None
        metrics = compute_drama_metrics(ordered)
        angle, score = choose_angle(metrics, weights, entry.angle_hint)
        if score <= 0:
            return None
        buy_date = pick_buy_date(ordered)
        if buy_date >= as_of:
            return None
        buy_close = next(bar.close for bar in ordered if bar.date >= buy_date)
        forward_pct = (ordered[-1].close / buy_close - 1) * 100
        if not passes_directive(angle, forward_pct, directive):
            return None
        return ScoredTopic(
            entry=entry,
            buy_date=buy_date,
            angle=angle,
            drama_score=score,
            forward_return_pct=forward_pct,
        )

    def _cooldown_symbols(self, cooldown_days: int = COOLDOWN_DAYS) -> set[str]:
        """Temporarily diversify the queue; completed symbols are not banned forever."""
        threshold = now_utc() - timedelta(days=cooldown_days)
        with self.database.session() as session:
            topics = session.scalars(
                select(TopicRecord).where(
                    (TopicRecord.status == TopicStatus.QUEUED)
                    | (
                        (TopicRecord.status == TopicStatus.CONSUMED)
                        & (TopicRecord.consumed_at.is_not(None))
                        & (TopicRecord.consumed_at >= threshold)
                    )
                )
            ).all()
            return {topic.symbol for topic in topics}

    @staticmethod
    def _produced_symbols(session) -> set[str]:
        return set(
            session.scalars(
                select(TopicRecord.symbol)
                .join(
                    PipelineRunRecord,
                    PipelineRunRecord.topic_id == TopicRecord.topic_id,
                )
                .where(PipelineRunRecord.status == PipelineStatus.COMPLETED)
            ).all()
        )

    @staticmethod
    def _topic_story_key(topic: TopicRecord) -> str:
        # Legacy topics have no explicit story type; their angle is the stable fallback.
        return story_key(topic.symbol, topic.buy_date, topic.angle)

    @classmethod
    def _produced_story_keys(cls, session) -> set[str]:
        topics = session.scalars(
            select(TopicRecord)
            .join(PipelineRunRecord, PipelineRunRecord.topic_id == TopicRecord.topic_id)
            .where(PipelineRunRecord.status == PipelineStatus.COMPLETED)
        ).all()
        explicit = set(
            session.scalars(
                select(StoryCandidateRecord.story_key).where(
                    StoryCandidateRecord.status == StoryCandidateStatus.PRODUCED
                )
            ).all()
        )
        return explicit | {cls._topic_story_key(topic) for topic in topics}

    def reject_previously_produced_queued(self) -> int:
        """Reject only an already-produced story, not every story for that symbol."""
        with self.database.session() as session:
            produced_keys = self._produced_story_keys(session)
            if not produced_keys:
                return 0
            duplicates = session.scalars(
                select(TopicRecord).where(
                    TopicRecord.status == TopicStatus.QUEUED,
                )
            ).all()
            duplicates = [
                topic
                for topic in duplicates
                if self._topic_story_key(topic) in produced_keys
            ]
            for topic in duplicates:
                topic.status = TopicStatus.REJECTED
            return len(duplicates)

    def sync_story_statuses(self) -> None:
        """Reconcile candidate state from the topic/run records after restarts."""
        with self.database.session() as session:
            candidates = session.scalars(
                select(StoryCandidateRecord).where(
                    StoryCandidateRecord.topic_id.is_not(None),
                    StoryCandidateRecord.status == StoryCandidateStatus.QUEUED,
                )
            ).all()
            for candidate in candidates:
                topic = session.get(TopicRecord, candidate.topic_id)
                if topic is None or topic.status == TopicStatus.REJECTED:
                    candidate.status = StoryCandidateStatus.READY
                    candidate.topic_id = None
                    continue
                run = session.scalars(
                    select(PipelineRunRecord)
                    .where(PipelineRunRecord.topic_id == candidate.topic_id)
                    .order_by(PipelineRunRecord.created_at.desc())
                    .limit(1)
                ).first()
                if run is not None and run.status == PipelineStatus.COMPLETED:
                    candidate.status = StoryCandidateStatus.PRODUCED

    def mark_story_produced(self, topic_id: str) -> None:
        with self.database.session() as session:
            candidate = session.scalars(
                select(StoryCandidateRecord).where(
                    StoryCandidateRecord.topic_id == topic_id
                )
            ).first()
            if candidate is not None:
                candidate.status = StoryCandidateStatus.PRODUCED

    def mark_story_rejected(self, topic_id: str, reason: str) -> None:
        with self.database.session() as session:
            candidate = session.scalars(
                select(StoryCandidateRecord).where(
                    StoryCandidateRecord.topic_id == topic_id
                )
            ).first()
            if candidate is not None:
                candidate.status = StoryCandidateStatus.REJECTED
                candidate.rejection_reason = reason[:240]

    def story_pool_status(self) -> dict[str, object]:
        self.sync_story_statuses()
        with self.database.session() as session:
            candidates = session.scalars(select(StoryCandidateRecord)).all()
        by_status: dict[str, int] = {}
        by_market: dict[str, int] = {}
        for candidate in candidates:
            by_status[candidate.status] = by_status.get(candidate.status, 0) + 1
            if candidate.status == StoryCandidateStatus.READY:
                by_market[candidate.market] = by_market.get(candidate.market, 0) + 1
        return {
            "total": len(candidates),
            "ready": by_status.get(StoryCandidateStatus.READY, 0),
            "by_status": by_status,
            "ready_by_market": by_market,
        }

    def list_story_candidates(self, limit: int = 100) -> list[StoryCandidateRecord]:
        self.sync_story_statuses()
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(StoryCandidateRecord)
                    .order_by(StoryCandidateRecord.content_score.desc())
                    .limit(limit)
                ).all()
            )

    @staticmethod
    def _market_values(markets: list[Market] | None) -> list[str] | None:
        if markets is None:
            return None
        return [market.value for market in markets]

    def queued_count(self, markets: list[Market] | None = None) -> int:
        """Return the queued topic count, optionally limited to enabled markets."""
        self.reject_previously_produced_queued()
        with self.database.session() as session:
            statement = select(TopicRecord.topic_id).where(
                TopicRecord.status == TopicStatus.QUEUED
            )
            market_values = self._market_values(markets)
            if market_values is not None:
                statement = statement.where(TopicRecord.market.in_(market_values))
            return len(
                session.scalars(statement).all()
            )

    @staticmethod
    def _legacy_topic_passes_directive(
        topic: TopicRecord,
        directive: TopicDirective | None,
    ) -> bool:
        """Conservatively validate a legacy topic without stored forward return data."""
        if directive is None:
            return True
        if directive.prefer_angles and topic.angle not in directive.prefer_angles:
            return False
        # A return threshold cannot be proven without its durable story candidate.
        return directive.surge_min_pct is None and directive.crash_max_pct is None

    def reconcile_queued(
        self,
        markets: list[Market] | None,
        directive: TopicDirective | None = None,
        *,
        amount: float | None = None,
        reset_all: bool = False,
    ) -> int:
        """Make queued topics agree with the policy that will consume them.

        Rejected durable candidates are released back to READY so a later compatible
        policy can queue them again. Consumed/running topics are deliberately untouched.
        """
        self.reject_previously_produced_queued()
        market_values = self._market_values(markets)
        rejected = 0
        with self.database.session() as session:
            queued = session.scalars(
                select(TopicRecord).where(TopicRecord.status == TopicStatus.QUEUED)
            ).all()
            if not queued:
                return 0
            topic_ids = [topic.topic_id for topic in queued]
            candidates = {
                candidate.topic_id: candidate
                for candidate in session.scalars(
                    select(StoryCandidateRecord).where(
                        StoryCandidateRecord.topic_id.in_(topic_ids)
                    )
                ).all()
                if candidate.topic_id is not None
            }
            for topic in queued:
                candidate = candidates.get(topic.topic_id)
                market_ok = market_values is None or topic.market in market_values
                # A per-market consumer must ignore, rather than destroy, other
                # markets' queues. Policy saves use reset_all=True to rebuild all.
                if not market_ok and not reset_all:
                    continue
                directive_ok = (
                    passes_directive(
                        candidate.angle,
                        candidate.forward_return_pct,
                        directive,
                    )
                    if candidate is not None
                    else self._legacy_topic_passes_directive(topic, directive)
                )
                if not reset_all and directive_ok:
                    if amount is not None:
                        topic.amount = amount
                    continue
                topic.status = TopicStatus.REJECTED
                rejected += 1
                if candidate is not None:
                    candidate.status = StoryCandidateStatus.READY
                    candidate.topic_id = None
                    candidate.rejection_reason = None
                    candidate.updated_at = now_utc()
        return rejected

    def refresh_queue_for_policy(
        self,
        policy,
        *,
        reset_all: bool = False,
    ) -> dict[str, object]:
        """Synchronously rebuild the queue from already-scored durable stories."""
        rejected = self.reconcile_queued(
            policy.markets,
            getattr(policy, "topic_directive", None),
            amount=policy.amount,
            reset_all=reset_all,
        )
        self.reject_ineligible_queued()
        pool_size = self.queued_count(policy.markets)
        added = self._queue_ready_stories(
            policy,
            max(0, policy.pool_target - pool_size),
        )
        return {
            "rejected": rejected,
            "added": added,
            "pool_size": self.queued_count(policy.markets),
        }

    def reject_ineligible_queued(self) -> int:
        """Remove queued topics that became ineligible after a universe refresh."""
        with self.database.session() as session:
            queued = session.scalars(
                select(TopicRecord).where(TopicRecord.status == TopicStatus.QUEUED)
            ).all()
            if not queued:
                return 0
            symbols = {topic.symbol for topic in queued}
            universe = {
                record.symbol: record
                for record in session.scalars(
                    select(UniverseRecord).where(UniverseRecord.symbol.in_(symbols))
                ).all()
            }
            rejected = 0
            for topic in queued:
                record = universe.get(topic.symbol)
                if record is not None and (not record.active or not record.eligible):
                    topic.status = TopicStatus.REJECTED
                    rejected += 1
            return rejected

    def list_topics(self, limit: int = 50) -> list[TopicRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(TopicRecord)
                    .order_by(TopicRecord.created_at.desc())
                    .limit(limit)
                ).all()
            )

    def next_topic(
        self,
        markets: list[Market] | None = None,
        directive: TopicDirective | None = None,
        *,
        amount: float | None = None,
    ) -> TopicRecord | None:
        """Return the oldest eligible queued topic from the enabled markets."""
        self.reconcile_queued(markets, directive, amount=amount)
        with self.database.session() as session:
            statement = select(TopicRecord).where(
                TopicRecord.status == TopicStatus.QUEUED
            )
            market_values = self._market_values(markets)
            if market_values is not None:
                statement = statement.where(TopicRecord.market.in_(market_values))
            statement = statement.order_by(TopicRecord.created_at.asc()).limit(1)
            return session.scalars(statement).first()

    def _persist_story_candidates(self, stories: list[LongHorizonStory]) -> int:
        persisted = 0
        with self.database.session() as session:
            for story in stories:
                existing = session.scalars(
                    select(StoryCandidateRecord).where(
                        StoryCandidateRecord.story_key == story.story_key
                    )
                ).first()
                values = {
                    "symbol": story.entry.symbol,
                    "name": story.entry.name,
                    "market": story.entry.market.value,
                    "buy_date": story.buy_date.isoformat(),
                    "end_date": story.end_date.isoformat(),
                    "story_type": story.story_type,
                    "angle": story.angle,
                    "hold_years": round(story.hold_years, 3),
                    "start_price": story.start_price,
                    "end_price": story.end_price,
                    "forward_return_pct": round(story.forward_return_pct, 4),
                    "max_drawdown_pct": round(story.max_drawdown_pct, 4),
                    "quality_score": round(story.quality_score, 2),
                    "content_score": round(story.content_score, 4),
                    "updated_at": now_utc(),
                }
                if existing is None:
                    session.add(
                        StoryCandidateRecord(
                            story_id=str(uuid4()),
                            story_key=story.story_key,
                            status=StoryCandidateStatus.READY,
                            **values,
                        )
                    )
                    persisted += 1
                elif existing.status not in {
                    StoryCandidateStatus.QUEUED,
                    StoryCandidateStatus.PRODUCED,
                }:
                    for key, value in values.items():
                        setattr(existing, key, value)
                    existing.status = StoryCandidateStatus.READY
                    existing.rejection_reason = None
        return persisted

    def _retire_stale_story_candidates(
        self,
        refreshed_symbols: set[str],
        active_keys: set[str],
    ) -> int:
        if not refreshed_symbols:
            return 0
        retired = 0
        with self.database.session() as session:
            candidates = session.scalars(
                select(StoryCandidateRecord).where(
                    StoryCandidateRecord.symbol.in_(refreshed_symbols),
                    StoryCandidateRecord.status == StoryCandidateStatus.READY,
                )
            ).all()
            for candidate in candidates:
                if candidate.story_key in active_keys:
                    continue
                candidate.status = StoryCandidateStatus.REJECTED
                candidate.rejection_reason = "新一轮行情评分后已被更优故事替代"
                retired += 1
        return retired

    def _queue_ready_stories(self, policy, needed: int) -> list[dict[str, object]]:
        if needed <= 0:
            return []
        self.sync_story_statuses()
        market_values = [market.value for market in policy.markets]
        directive = getattr(policy, "topic_directive", None)
        cooldown = self._cooldown_symbols()
        added: list[dict[str, object]] = []
        with self.database.session() as session:
            completed_topics = session.scalars(
                select(TopicRecord)
                .join(PipelineRunRecord, PipelineRunRecord.topic_id == TopicRecord.topic_id)
                .where(PipelineRunRecord.status == PipelineStatus.COMPLETED)
            ).all()
            produced_windows = {
                f"{topic.symbol.upper()}|{date.fromisoformat(topic.buy_date):%Y-%m}"
                for topic in completed_topics
            }
            candidates = session.scalars(
                select(StoryCandidateRecord)
                .where(
                    StoryCandidateRecord.status == StoryCandidateStatus.READY,
                    StoryCandidateRecord.market.in_(market_values),
                )
                .order_by(StoryCandidateRecord.content_score.desc())
            ).all()
            preferred_symbols = set(directive.prefer_symbols) if directive else set()
            candidates.sort(
                key=lambda candidate: (
                    0 if candidate.symbol.upper() in preferred_symbols else 1,
                    -candidate.content_score,
                )
            )
            selected_symbols: set[str] = set()
            for candidate in candidates:
                window_key = (
                    f"{candidate.symbol.upper()}|"
                    f"{date.fromisoformat(candidate.buy_date):%Y-%m}"
                )
                if window_key in produced_windows:
                    candidate.status = StoryCandidateStatus.PRODUCED
                    continue
                if candidate.symbol in cooldown or candidate.symbol in selected_symbols:
                    continue
                if not passes_directive(
                    candidate.angle,
                    candidate.forward_return_pct,
                    directive,
                ):
                    continue
                topic_id = str(uuid4())
                session.add(
                    TopicRecord(
                        topic_id=topic_id,
                        symbol=candidate.symbol,
                        name=candidate.name,
                        market=candidate.market,
                        buy_date=candidate.buy_date,
                        amount=policy.amount,
                        angle=candidate.angle,
                        drama_score=candidate.content_score,
                        status=TopicStatus.QUEUED,
                    )
                )
                candidate.status = StoryCandidateStatus.QUEUED
                candidate.topic_id = topic_id
                selected_symbols.add(candidate.symbol)
                added.append(
                    {
                        "story_id": candidate.story_id,
                        "symbol": candidate.symbol,
                        "name": candidate.name,
                        "market": candidate.market,
                        "angle": candidate.angle,
                        "story_type": candidate.story_type,
                        "buy_date": candidate.buy_date,
                        "hold_years": round(candidate.hold_years, 1),
                        "forward_return_pct": round(candidate.forward_return_pct, 1),
                        "quality_score": candidate.quality_score,
                        "content_score": candidate.content_score,
                    }
                )
                if len(added) >= needed:
                    break
        return added

    async def refresh_story_pool(
        self,
        policy,
        *,
        as_of: date | None = None,
        limit: int = 40,
    ) -> dict[str, object]:
        """Refresh the durable editorial pool even when the topic queue is full."""
        as_of = as_of or datetime.now(UTC).date()
        directive = getattr(policy, "topic_directive", None)
        weights = directive_weights(directive, policy.angle_weights)
        assets = self.story_assets()
        entries = [
            entry
            for entry in self.universe_entries()
            if entry.market in policy.markets
            and (not assets or entry.symbol.upper() in assets)
        ]
        entries.sort(
            key=lambda entry: (
                -(
                    assets[entry.symbol.upper()].priority
                    if entry.symbol.upper() in assets
                    else 0
                ),
                entry.symbol,
            )
        )
        generated: list[LongHorizonStory] = []
        errors: list[dict[str, str]] = []
        refreshed_symbols: set[str] = set()
        for entry in entries[:limit]:
            try:
                bars = await self.fetch_history(entry.symbol, as_of)
                refreshed_symbols.add(entry.symbol)
                generated.extend(
                    self.score_story_candidates(
                        entry,
                        bars,
                        weights,
                        as_of,
                        directive=directive,
                        asset=assets.get(entry.symbol.upper()),
                    )
                )
            except Exception as exc:
                errors.append({"symbol": entry.symbol, "reason": str(exc)[:200]})
        created = self._persist_story_candidates(generated)
        retired = self._retire_stale_story_candidates(
            refreshed_symbols,
            {story.story_key for story in generated},
        )
        return {
            "created": created,
            "retired": retired,
            "scored": len(generated),
            "assets_checked": min(limit, len(entries)),
            "errors": errors,
            "story_pool": self.story_pool_status(),
        }

    async def replenish(
        self,
        policy,
        *,
        as_of: date | None = None,
    ) -> dict[str, object]:
        """把队列补到 policy.pool_target；返回真实补充报告。

        单只股票拉取/评分失败只记录进报告并跳过，不阻塞整个队列。
        """
        as_of = as_of or datetime.now(UTC).date()
        report: dict[str, object] = {
            "added": [],
            "skipped": [],
            "errors": [],
        }
        rejected_count = self.reject_ineligible_queued()
        if rejected_count:
            report["rejected_ineligible"] = rejected_count
        pool_size = self.queued_count(policy.markets)
        needed = max(0, policy.pool_target - pool_size)
        if needed == 0:
            report["pool_size"] = pool_size
            return report

        report["added"].extend(self._queue_ready_stories(policy, needed))  # type: ignore[union-attr]
        needed = max(0, policy.pool_target - self.queued_count(policy.markets))
        if needed == 0:
            report["pool_size"] = self.queued_count(policy.markets)
            report["story_pool"] = self.story_pool_status()
            return report

        directive = getattr(policy, "topic_directive", None)
        weights = directive_weights(directive, policy.angle_weights)
        story_assets = self.story_assets()
        entries = self.universe_entries()
        cooldown = self._cooldown_symbols()
        candidates: list[UniverseEntry] = []
        for entry in entries:
            if entry.market not in policy.markets:
                report["skipped"].append(  # type: ignore[union-attr]
                    {"symbol": entry.symbol, "reason": "市场未启用"}
                )
                continue
            if story_assets and entry.symbol.upper() not in story_assets:
                continue
            if entry.symbol in cooldown:
                report["skipped"].append(  # type: ignore[union-attr]
                    {"symbol": entry.symbol, "reason": f"同股冷却 {COOLDOWN_DAYS} 天内"}
                )
                continue
            candidates.append(entry)

        preferred_symbols = (
            set(directive.prefer_symbols)
            if directive and directive.prefer_symbols
            else set()
        )
        editorial_symbols = set(story_assets)
        entries_by_priority = sorted(
            candidates,
            key=lambda entry: (
                0 if entry.symbol.upper() in preferred_symbols else 1,
                -(
                    story_assets[entry.symbol.upper()].priority
                    if entry.symbol.upper() in story_assets
                    else 0
                ),
                entry.symbol,
            ),
        )
        candidate_limit = min(60, max(20, needed * 3))
        candidates = self._balanced_candidates(
            entries_by_priority,
            as_of=as_of,
            limit=candidate_limit,
            preferred_symbols=preferred_symbols | editorial_symbols,
        )

        generated: list[LongHorizonStory] = []
        refreshed_symbols: set[str] = set()
        for entry in candidates:
            try:
                bars = await self.fetch_history(entry.symbol, as_of)
            except Exception as exc:
                logger.warning("选题拉取行情失败 %s：%s", entry.symbol, exc)
                report["errors"].append(  # type: ignore[union-attr]
                    {"symbol": entry.symbol, "reason": str(exc)[:200]}
                )
                continue
            try:
                stories = self.score_story_candidates(
                    entry,
                    bars,
                    weights,
                    as_of,
                    directive=directive,
                    asset=story_assets.get(entry.symbol.upper()),
                )
                refreshed_symbols.add(entry.symbol)
            except ValueError as exc:
                report["errors"].append(  # type: ignore[union-attr]
                    {"symbol": entry.symbol, "reason": str(exc)}
                )
                continue
            if not stories:
                report["skipped"].append(  # type: ignore[union-attr]
                    {
                        "symbol": entry.symbol,
                        "reason": "周期、收益冲击、数据质量未达门槛或不满足选题偏好",
                    }
                )
                continue
            generated.extend(stories)

        report["story_candidates_created"] = self._persist_story_candidates(generated)
        report["story_candidates_retired"] = self._retire_stale_story_candidates(
            refreshed_symbols,
            {story.story_key for story in generated},
        )
        report["added"].extend(self._queue_ready_stories(policy, needed))  # type: ignore[union-attr]
        report["pool_size"] = self.queued_count(policy.markets)
        report["story_pool"] = self.story_pool_status()
        return report

    async def preview(
        self,
        directive: TopicDirective,
        markets: list[Market],
        angle_weights: dict[str, int] | None = None,
        *,
        as_of: date | None = None,
    ) -> dict[str, object]:
        """预览当前选题偏好能命中多少只股票：不写库、不限水位。

        跳过规则与 replenish 一致（市场未启用、同股冷却、有效行情不足、
        不满足偏好），命中按戏剧分降序返回前 50 条。
        """
        as_of = as_of or datetime.now(UTC).date()
        weights = directive_weights(
            directive,
            angle_weights or DEFAULT_ANGLE_WEIGHTS,
        )
        entries = self.universe_entries()
        cooldown = self._cooldown_symbols()
        enabled = set(markets)

        candidates: list[UniverseEntry] = []
        excluded = 0
        fetch_errors = 0
        for entry in entries:
            if entry.market not in enabled or entry.symbol in cooldown:
                excluded += 1
                continue
            candidates.append(entry)
        candidates = self._balanced_candidates(
            candidates,
            as_of=as_of,
            limit=90,
            preferred_symbols=set(directive.prefer_symbols),
        )

        matched: list[dict[str, object]] = []
        for entry in candidates:
            try:
                bars = await self.fetch_history(entry.symbol, as_of)
            except Exception as exc:
                logger.warning("预览拉取行情失败 %s：%s", entry.symbol, exc)
                fetch_errors += 1
                continue
            try:
                topic = self.score_bars(entry, bars, weights, as_of, directive=directive)
            except ValueError:
                fetch_errors += 1
                continue
            if topic is None:
                excluded += 1
                continue
            matched.append(
                {
                    "symbol": topic.entry.symbol,
                    "name": topic.entry.name,
                    "market": topic.entry.market.value,
                    "angle": topic.angle,
                    "buy_date": topic.buy_date.isoformat(),
                    "forward_return_pct": round(topic.forward_return_pct, 1),
                    "drama_score": round(topic.drama_score, 6),
                }
            )
        preferred_symbols = set(directive.prefer_symbols)
        matched.sort(
            key=lambda item: (
                0 if str(item["symbol"]).upper() in preferred_symbols else 1,
                -float(item["drama_score"]),
            )
        )
        return {
            "count": len(matched),
            "matched": matched[:50],
            "fetch_errors": fetch_errors,
            "excluded_by_market_or_cooldown": excluded,
        }
