"""Publish-manifest generation and persistent Douyin publication orchestration.

All financial facts come from ``simulation.json``.  Copy generation may choose
among deterministic templates, but it never calculates or invents amounts,
dates, or returns.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select

from stock_video_generator.config import Settings
from stock_video_generator.database import (
    Database,
    OutputRecord,
    PipelineRunRecord,
    PublishAccountRecord,
    PublishJobRecord,
    PublishStage,
    PublishTitleHistoryRecord,
    SimulationRecord,
    TopicRecord,
)
from stock_video_generator.models import SimulationResult
from stock_video_generator.thumbnails import cover_path, find_ffmpeg

PublishMode = Literal["dry_run", "immediate", "scheduled"]
SocialPlatform = Literal["douyin", "xiaohongshu", "wechat_channels"]

DISCLAIMER = "历史数据模拟，仅供信息展示，不构成投资建议。"
DEFAULT_COLLECTION = "100万买股票十年后"
FORBIDDEN_PROMISES = ("稳赚", "必涨", "闭眼买", "马上买", "保证收益", "财富密码")


class PublishFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_name: str
    symbol: str
    market: str
    exchange: str
    currency: str
    buy_date: date
    end_date: date
    holding_years: float
    initial_capital: float
    final_value: float
    best_value: float = 0
    worst_value: float = 0
    return_pct: float
    max_drawdown_pct: float
    peak_giveback_pct: float = 0
    feature_years: list[int] = Field(default_factory=list)
    dividend_policy: str
    execution_price: str
    fees_included: bool
    data_source: str
    angle: str


class PublishMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    cover_portrait_path: str
    cover_landscape_path: str


class PublishContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_candidates: list[str]
    selected_title: str
    selected_template_id: str
    description: str
    topics: list[str]
    collection: str | None = DEFAULT_COLLECTION
    declaration: str | None = None

    @field_validator("selected_title")
    @classmethod
    def title_length(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("作品标题不能为空")
        if len(value) > 30:
            raise ValueError("作品标题不能超过30个字符")
        if any(term in value for term in FORBIDDEN_PROMISES):
            raise ValueError("作品标题包含收益承诺或诱导性词语")
        return value

    @field_validator("description")
    @classmethod
    def description_length(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("作品简介不能为空")
        if len(value) > 1000:
            raise ValueError("作品简介不能超过1000个字符")
        return value

    @field_validator("topics")
    @classmethod
    def topic_count(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for topic in value:
            clean = topic.strip().removeprefix("#")
            if clean and clean not in normalized:
                normalized.append(clean)
        if not 1 <= len(normalized) <= 5:
            raise ValueError("话题数量必须为1到5个")
        return normalized


class OutputCopy(BaseModel):
    """Episode-level copy generated together with the finished video."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    output_id: str
    render_id: str
    title_candidates: list[str]
    title: str
    selected_template_id: str
    subtitle: str
    description: str | None = None
    subtitle_template_id: str | None = None
    story_type: str | None = None
    topics: list[str]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublishManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    publish_id: str
    output_id: str
    account_id: str
    media: PublishMedia
    facts: PublishFacts
    content: PublishContent
    mode: PublishMode = "dry_run"
    scheduled_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_time_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("定时发布时间必须包含时区")
        return value


class PublishAccountCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    platform: SocialPlatform = "douyin"
    display_name: str = Field(min_length=1, max_length=120)
    auto_publish_enabled: bool = False


class PublishJobCreate(BaseModel):
    output_id: str
    account_id: str
    mode: PublishMode = "dry_run"
    scheduled_at: datetime | None = None
    title: str | None = None
    description: str | None = None
    topics: list[str] | None = None
    collection: str | None = DEFAULT_COLLECTION
    declaration: str | None = None

    @field_validator("scheduled_at")
    @classmethod
    def validate_schedule(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("定时发布时间必须包含时区")
        return value


class PublishJobUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    topics: list[str] | None = None
    collection: str | None = None
    declaration: str | None = None


def _amount_wan(value: float) -> str:
    amount = value / 10_000
    return f"{amount:,.1f}".rstrip("0").rstrip(".")


def _return_text(value: float) -> str:
    return f"{value:+.2f}%"


def _market_topic(market: str) -> str:
    return {"CN": "A股", "HK": "港股", "US": "美股"}.get(market, "股票")


def _holding_label(facts: PublishFacts) -> str:
    years = facts.holding_years
    if years >= 9.5:
        return "十年"
    if years >= 1:
        rounded = max(1, round(years))
        return f"{rounded}年"
    months = max(1, round(years * 12))
    return f"{months}个月"


def _title_templates(facts: PublishFacts) -> list[tuple[str, str]]:
    name = facts.stock_name
    year = facts.buy_date.year
    period = _holding_label(facts)
    initial = _amount_wan(facts.initial_capital)
    candidates: list[tuple[str, str]] = []
    if facts.return_pct < 0:
        candidates.extend(
            [
                ("loss_remains", f"{initial}万买{name}，现在还剩多少？"),
                ("loss_hold", f"{year}年买{name}，拿到今天怎样？"),
                ("loss_drawdown", f"{name}最惨暴跌时，账户剩多少？"),
            ]
        )
    elif facts.max_drawdown_pct <= -50:
        candidates.extend(
            [
                ("deep_drawdown", f"{initial}万买{name}，中途最惨多难熬？"),
                ("deep_recovery", f"扛过暴跌后，{name}后来怎样了？"),
            ]
        )
    else:
        candidates.extend(
            [
                ("period_question", f"{period}前{initial}万买{name}，如今多少？"),
                ("year_hold", f"{year}年买{name}，持有到今天怎样？"),
                ("never_sell", f"如果一直没卖{name}，现在有多少？"),
            ]
        )
    candidates.extend(
        [
            ("timeline", f"{name}这段行情，{initial}万经历了什么？"),
            ("ending", f"拿住{name}{period}，结局意外吗？"),
            ("backtest", f"{initial}万回测{name}，最后变多少？"),
        ]
    )
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for template_id, title in candidates:
        normalized = re.sub(r"\s+", " ", title).strip()
        if len(normalized) <= 30 and normalized not in seen:
            seen.add(normalized)
            unique.append((template_id, normalized))
    if not unique:
        # Long English company names can make every normal template exceed
        # Douyin's 30-character title limit. The ticker is short, factual and
        # already part of the simulation result, so it is a safe fallback.
        symbol = re.sub(r"\s+", "", facts.symbol)[:16] or "这只股票"
        fallbacks = [
            ("symbol_value", f"{initial}万买{symbol}，现在多少？"),
            ("symbol_hold", f"{symbol}持有结果如何？"),
            ("short_fallback", "这笔股票投资后来怎样？"),
        ]
        for template_id, title in fallbacks:
            normalized = re.sub(r"\s+", " ", title).strip()
            if len(normalized) <= 30:
                unique.append((template_id, normalized))
                break
    return unique


def _normalized_title_hash(title: str) -> str:
    normalized = re.sub(r"[\W_]+", "", title.casefold(), flags=re.UNICODE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _build_description(facts: PublishFacts) -> str:
    dividend = {
        "reinvest": "红利复投",
        "cash": "现金分红",
        "ignore": "不计分红",
    }.get(facts.dividend_policy, facts.dividend_policy)
    fee = "包含手续费" if facts.fees_included else "不含手续费"
    return "\n".join(
        [
            (
                f"{facts.buy_date:%Y年%m月%d日}投入{_amount_wan(facts.initial_capital)}万"
                f"买入{facts.stock_name}，持有到{facts.end_date:%Y年%m月%d日}。"
            ),
            (
                f"最终资产{_amount_wan(facts.final_value)}万，累计收益"
                f"{_return_text(facts.return_pct)}，期间最大回撤"
                f"{facts.max_drawdown_pct:.2f}%。"
            ),
            (
                f"计算口径：{facts.execution_price} · {dividend} · {fee}；"
                f"数据来源：{facts.data_source}。"
            ),
            DISCLAIMER,
        ]
    )


def _build_topics(facts: PublishFacts) -> list[str]:
    topics = [facts.stock_name, _market_topic(facts.market), "股票历史回测", "投资复盘"]
    if facts.angle == "surge":
        topics.append("长期投资")
    elif facts.angle == "crash":
        topics.append("风险教育")
    elif facts.angle == "rollercoaster":
        topics.append("市场波动")
    return topics[:5]


def _stable_subtitle_choice(
    candidates: list[tuple[str, str]],
    *,
    seed: str,
) -> tuple[str, str]:
    usable = [(template_id, text) for template_id, text in candidates if 18 <= len(text) <= 52]
    if not usable:
        return "choice_fallback", "如果是你，会选择继续持有，还是提前离场？"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return usable[int.from_bytes(digest[:8], "big") % len(usable)]


def _subtitle_wan(value: float) -> str:
    if value < 10_000:
        return "不足一"
    return _amount_wan(value)


def build_interactive_subtitle(
    result: SimulationResult,
    facts: PublishFacts,
    *,
    seed: str,
) -> tuple[str, str, str]:
    """Build one factual, interactive hook from the episode's most dramatic feature."""

    series = result.series
    lowest = min(series, key=lambda point: point.portfolio_value)
    peak = max(series, key=lambda point: point.portfolio_value)
    initial_wan = _subtitle_wan(facts.initial_capital)
    lowest_wan = _subtitle_wan(lowest.portfolio_value)
    peak_wan = _subtitle_wan(peak.portfolio_value)
    final_wan = _subtitle_wan(facts.final_value)
    symbol = facts.symbol[:12]
    years = _holding_label(facts)
    drawdown = abs(facts.max_drawdown_pct)
    loss = abs(facts.return_pct)
    peak_giveback = max(0.0, (1 - facts.final_value / max(peak.portfolio_value, 1)) * 100)

    if facts.max_drawdown_pct <= -50 and facts.final_value > facts.initial_capital:
        story_type = "abyss_recovery"
        candidates = [
            (
                "trough_choice",
                f"账户一度只剩{lowest_wan}万，换成你会在{lowest.date.year}年割肉吗？",
            ),
            (
                "drawdown_hold",
                f"熬过{drawdown:.0f}%回撤才等到{final_wan}万，你真的拿得住吗？",
            ),
            (
                "trough_to_final",
                f"从{lowest_wan}万熬回{final_wan}万，最难的时候你会不会卖？",
            ),
            (
                "half_loss_choice",
                f"{lowest.date.year}年回撤{drawdown:.0f}%、只剩{lowest_wan}万，你还会继续等吗？",
            ),
        ]
    elif facts.return_pct >= 300:
        story_type = "wealth_leap"
        candidates = [
            (
                "double_sell",
                f"从{initial_wan}万涨到{final_wan}万，你会在翻倍时提前卖掉吗？",
            ),
            (
                "peak_profit",
                f"账户最高冲到{peak_wan}万，面对浮盈你能忍住不卖吗？",
            ),
            (
                "holding_test",
                f"真正难的不是买中，而是拿住{years}等到{final_wan}万，你做得到吗？",
            ),
        ]
    elif peak.portfolio_value >= facts.initial_capital * 1.8 and peak_giveback >= 35:
        story_type = "boom_bust"
        candidates = [
            (
                "peak_giveback",
                f"账户最高到过{peak_wan}万，后来回吐{peak_giveback:.0f}%，你会何时止盈？",
            ),
            (
                "peak_exit",
                f"高点曾有{peak_wan}万，如果是你会在{peak.date.year}年卖掉吗？",
            ),
            (
                "profit_retreat",
                f"从最高{peak_wan}万回落到{final_wan}万，你会后悔没早点卖吗？",
            ),
        ]
    elif facts.return_pct <= -50:
        story_type = "deep_loss"
        candidates = [
            (
                "deep_loss_exit",
                f"{initial_wan}万买{symbol}最后只剩{final_wan}万，回看低点你会止损吗？",
            ),
            (
                "loss_patience",
                f"{symbol}持有{years}仍亏{loss:.0f}%，只剩{final_wan}万你还会等吗？",
            ),
            (
                "lowest_account",
                f"{symbol}在{lowest.date.year}年最低只剩{lowest_wan}万，你还敢继续拿吗？",
            ),
        ]
    elif facts.return_pct < 0 and facts.holding_years >= 3:
        story_type = "long_loss"
        candidates = [
            (
                "long_loss_choice",
                f"拿了{years}还亏{loss:.0f}%，如果是你会止损还是继续等？",
            ),
            (
                "time_and_loss",
                f"时间过去{years}，最终还剩{final_wan}万，这样的等待值得吗？",
            ),
            (
                "loss_exit_year",
                f"最终只剩{final_wan}万，你会选择在哪一年提前离场？",
            ),
        ]
    elif abs(facts.return_pct) <= 15 and facts.holding_years >= 5:
        story_type = "time_cost"
        candidates = [
            (
                "time_cost_return",
                f"持有{years}只得到{facts.return_pct:+.0f}%，这样的时间成本值得吗？",
            ),
            (
                "quiet_market",
                f"没有暴涨也没有归零，拿住{years}，你觉得值得吗？",
            ),
            (
                "capital_efficiency",
                f"{initial_wan}万放了{years}才变成{final_wan}万，你会继续持有吗？",
            ),
        ]
    elif facts.max_drawdown_pct <= -30 and facts.final_value > facts.initial_capital:
        story_type = "volatile_win"
        candidates = [
            (
                "volatile_exit",
                f"中途回撤{drawdown:.0f}%，最后来到{final_wan}万，你会在哪次下跌卖掉？",
            ),
            (
                "pain_before_gain",
                f"赚到{final_wan}万之前先经历{drawdown:.0f}%回撤，你扛得住吗？",
            ),
            (
                "trough_recovery",
                f"账户最低到过{lowest_wan}万，换成你还能坚持到翻红吗？",
            ),
        ]
    elif facts.return_pct >= 50:
        story_type = "long_growth"
        candidates = [
            (
                "growth_patience",
                f"从{initial_wan}万拿到{final_wan}万，途中你会忍不住止盈吗？",
            ),
            (
                "growth_hold",
                f"持有{years}才等到这个结果，你能做到一直不卖吗？",
            ),
            (
                "growth_drawdown",
                f"最终虽然盈利，中途{drawdown:.0f}%回撤你真的扛得住吗？",
            ),
        ]
    elif facts.return_pct < 0:
        story_type = "ordinary_loss"
        candidates = [
            (
                "ordinary_loss_choice",
                f"最后亏了{loss:.0f}%，如果是你会继续持有还是认亏离场？",
            ),
            (
                "ordinary_loss_low",
                f"账户在{lowest.date.year}年最低只剩{lowest_wan}万，跌到这里你会怎么选？",
            ),
        ]
    else:
        story_type = "steady_result"
        candidates = [
            (
                "steady_worth",
                f"{initial_wan}万变成{final_wan}万，这段持有真的值得吗？",
            ),
            (
                "steady_choice",
                f"没有惊天暴涨，只有真实波动，换成你会拿住{years}吗？",
            ),
        ]

    template_id, subtitle = _stable_subtitle_choice(
        candidates,
        seed=f"{seed}|{story_type}|{result.instrument.symbol}|{facts.buy_date.isoformat()}",
    )
    return story_type, template_id, subtitle


def load_simulation_result(simulation: SimulationRecord) -> SimulationResult:
    if not simulation.artifact_paths_json:
        raise ValueError("回测产物尚未生成，无法生成成片文案")
    artifacts = json.loads(simulation.artifact_paths_json)
    path = Path(str(artifacts.get("simulation_json", "")))
    if not path.is_file():
        raise ValueError(f"回测结果文件不存在：{path}")
    return SimulationResult.model_validate_json(path.read_text(encoding="utf-8"))


def publish_facts_from_result(result: SimulationResult, angle: str) -> PublishFacts:
    end_date = result.series[-1].date
    buy_date = result.summary.actual_buy_date
    holding_years = max(0.0, (end_date - buy_date).days / 365.2425)
    assumptions = result.assumptions
    lowest = min(result.series, key=lambda point: point.portfolio_value)
    peak = max(result.series, key=lambda point: point.portfolio_value)
    peak_giveback_pct = max(
        0.0,
        (1 - result.summary.final_value / max(peak.portfolio_value, 1)) * 100,
    )
    return PublishFacts(
        stock_name=result.instrument.name,
        symbol=result.instrument.symbol,
        market=str(result.instrument.market.value),
        exchange=result.instrument.exchange,
        currency=result.instrument.currency,
        buy_date=buy_date,
        end_date=end_date,
        holding_years=holding_years,
        initial_capital=float(assumptions["initial_capital"]),
        final_value=result.summary.final_value,
        best_value=result.summary.best_value,
        worst_value=result.summary.worst_value,
        return_pct=result.summary.total_return_pct,
        max_drawdown_pct=result.summary.max_drawdown_pct,
        peak_giveback_pct=peak_giveback_pct,
        feature_years=sorted({lowest.date.year, peak.date.year}),
        dividend_policy=str(assumptions["dividend_policy"]),
        execution_price=str(assumptions["execution_price"]),
        fees_included=bool(assumptions["fee_policy"]["enabled"]),
        data_source=result.source.provider,
        angle=angle,
    )


def output_copy_path(settings: Settings, render_id: str) -> Path:
    return (settings.data_dir / "outputs" / f"{render_id}.copy.json").resolve()


def load_output_copy(settings: Settings, render_id: str) -> OutputCopy | None:
    path = output_copy_path(settings, render_id)
    if not path.is_file():
        return None
    return OutputCopy.model_validate_json(path.read_text(encoding="utf-8"))


def ensure_output_copy(
    settings: Settings,
    *,
    output_id: str,
    render_id: str,
    simulation: SimulationRecord,
    angle: str,
) -> OutputCopy:
    """Return persisted copy, generating it once from real simulation facts."""

    existing = load_output_copy(settings, render_id)
    if existing is not None:
        return existing
    result = load_simulation_result(simulation)
    facts = publish_facts_from_result(result, angle)
    templates = _title_templates(facts)
    selected_template_id, selected_title = templates[0]
    story_type, subtitle_template_id, subtitle = build_interactive_subtitle(
        result,
        facts,
        seed=f"{output_id}|{render_id}|{simulation.simulation_id}",
    )
    description = _build_description(facts)
    content = PublishContent(
        title_candidates=[title for _, title in templates[:6]],
        selected_title=selected_title,
        selected_template_id=selected_template_id,
        description=description,
        topics=_build_topics(facts),
    )
    _validate_numeric_claims(
        content.model_copy(update={"description": f"{subtitle}\n\n{description}"}),
        facts,
    )
    copy = OutputCopy(
        output_id=output_id,
        render_id=render_id,
        title_candidates=content.title_candidates,
        title=content.selected_title,
        selected_template_id=content.selected_template_id,
        subtitle=subtitle,
        description=description,
        subtitle_template_id=subtitle_template_id,
        story_type=story_type,
        topics=content.topics,
    )
    _atomic_json(output_copy_path(settings, render_id), copy)
    return copy


def _validate_numeric_claims(content: PublishContent, facts: PublishFacts) -> None:
    """Reject numeric claims that cannot be reconciled with simulation facts."""

    text = f"{content.selected_title}\n{content.description}"
    percentage_facts = {
        facts.return_pct,
        abs(facts.return_pct),
        facts.max_drawdown_pct,
        abs(facts.max_drawdown_pct),
        facts.peak_giveback_pct,
    }
    allowed_percentages = {
        round(value, precision)
        for value in percentage_facts
        for precision in (0, 1, 2)
    }
    for raw in re.findall(r"([+-]?\d+(?:\.\d+)?)%", text):
        if round(float(raw), 2) not in allowed_percentages:
            raise ValueError(f"文案中的百分比 {raw}% 不在回测结果中")

    allowed_wan = {
        round(facts.initial_capital / 10_000, 1),
        round(facts.final_value / 10_000, 1),
        round(facts.best_value / 10_000, 1),
        round(facts.worst_value / 10_000, 1),
    }
    for raw in re.findall(r"(\d+(?:,\d{3})*(?:\.\d+)?)万", text):
        value = round(float(raw.replace(",", "")), 1)
        if value not in allowed_wan:
            raise ValueError(f"文案中的金额 {raw}万 不在回测结果中")

    allowed_dates = {
        facts.buy_date.strftime("%Y年%m月%d日"),
        facts.end_date.strftime("%Y年%m月%d日"),
    }
    for raw in re.findall(r"\d{4}年\d{2}月\d{2}日", text):
        if raw not in allowed_dates:
            raise ValueError(f"文案中的日期 {raw} 不在回测结果中")

    allowed_years = {facts.buy_date.year, facts.end_date.year, *facts.feature_years}
    for raw in re.findall(r"(?<!\d)(\d{4})年(?!\d{2}月)", text):
        if int(raw) not in allowed_years:
            raise ValueError(f"文案中的年份 {raw}年 不在回测区间端点中")


def _atomic_json(path: Path, payload: BaseModel | dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _require_png_dimensions(
    path: Path,
    *,
    expected: tuple[int, int],
    label: str,
) -> None:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{label}不是有效PNG文件：{path}")
    actual = struct.unpack(">II", header[16:24])
    if actual != expected:
        raise ValueError(
            f"{label}尺寸应为{expected[0]}×{expected[1]}，实际为"
            f"{actual[0]}×{actual[1]}；请重新渲染该视频"
        )


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"封面不是有效PNG文件：{path}")
    return struct.unpack(">II", header[16:24])


def _publish_landscape_cover(
    settings: Settings,
    source: Path,
    publish_id: str,
) -> Path:
    """Return a 4:3 landscape cover without modifying legacy source assets."""

    actual = _png_dimensions(source)
    if actual == (1440, 1080):
        return source
    if actual != (1920, 1080):
        _require_png_dimensions(source, expected=(1440, 1080), label="横封面")
        return source

    ffmpeg = find_ffmpeg(settings)
    if ffmpeg is None:
        raise ValueError("旧横封面需要转换为1440×1080，但未找到 FFmpeg")
    target = (
        settings.data_dir / "publishes" / publish_id / "cover-landscape.png"
    ).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.stem}.tmp{target.suffix}")
    result = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-i",
            str(source),
            "-vf",
            "crop=1440:1080:240:0",
            "-frames:v",
            "1",
            str(temporary),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not temporary.is_file():
        detail = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "未知错误"
        )
        raise ValueError(f"旧横封面自动转换失败：{detail}")
    temporary.replace(target)
    _require_png_dimensions(target, expected=(1440, 1080), label="横封面")
    return target


class PublishingService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def list_accounts(self, platform: SocialPlatform | None = None) -> list[PublishAccountRecord]:
        with self.database.session() as session:
            statement = select(PublishAccountRecord)
            if platform is not None:
                statement = statement.where(PublishAccountRecord.platform == platform)
            return list(
                session.scalars(
                    statement.order_by(PublishAccountRecord.created_at)
                ).all()
            )

    def save_account(self, request: PublishAccountCreate) -> PublishAccountRecord:
        profile = (
            self.settings.data_dir / "publish-accounts" / request.account_id / "chrome-profile"
        ).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, request.account_id)
            if account is None:
                account = PublishAccountRecord(
                    account_id=request.account_id,
                    platform=request.platform,
                    display_name=request.display_name,
                    browser_profile_dir=str(profile),
                    auto_publish_enabled=request.auto_publish_enabled,
                    auth_status="unknown",
                )
                session.add(account)
            else:
                if account.platform != request.platform:
                    raise ValueError("同一账号标识不能切换到其他平台")
                account.display_name = request.display_name
                account.auto_publish_enabled = request.auto_publish_enabled
                account.enabled = True
            session.flush()
            return account

    def unbind_account(self, account_id: str) -> PublishAccountRecord:
        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            profile = Path(account.browser_profile_dir).resolve()
            account.enabled = False
            account.auto_publish_enabled = False
            account.auth_status = "logged_out"
            account.last_login_at = None
            account.last_checked_at = datetime.now(UTC)
            session.flush()
            payload = account

        account_root = (self.settings.data_dir / "publish-accounts").resolve()
        if profile.exists():
            if not profile.is_relative_to(account_root) or profile.name != "chrome-profile":
                raise ValueError("账号浏览器目录不在允许的用户数据范围内")
            shutil.rmtree(profile)
        return payload

    def delete_account(self, account_id: str) -> None:
        """Permanently remove an already-unbound account and its local remnants."""
        account_root = (self.settings.data_dir / "publish-accounts").resolve()
        account_dir = (account_root / account_id).resolve()
        if account_dir.parent != account_root:
            raise ValueError("账号目录不在允许的用户数据范围内")

        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            if account.enabled:
                raise ValueError("请先解绑账号，再执行删除")

        if account_dir.exists():
            shutil.rmtree(account_dir)

        with self.database.session() as session:
            account = session.get(PublishAccountRecord, account_id)
            if account is None:
                raise KeyError("account")
            session.delete(account)

    def _load_result(self, simulation: SimulationRecord) -> SimulationResult:
        return load_simulation_result(simulation)

    def _facts(self, result: SimulationResult, angle: str) -> PublishFacts:
        return publish_facts_from_result(result, angle)

    def _choose_title(
        self,
        account_id: str,
        facts: PublishFacts,
    ) -> tuple[list[str], str, str]:
        templates = _title_templates(facts)
        with self.database.session() as session:
            recent = session.scalars(
                select(PublishTitleHistoryRecord)
                .where(PublishTitleHistoryRecord.account_id == account_id)
                .order_by(desc(PublishTitleHistoryRecord.created_at))
                .limit(100)
            ).all()
        used_hashes = {row.normalized_hash for row in recent}
        last_template = recent[0].template_id if recent else None
        chosen = next(
            (
                item
                for item in templates
                if item[0] != last_template and _normalized_title_hash(item[1]) not in used_hashes
            ),
            templates[0],
        )
        return [title for _, title in templates[:6]], chosen[1], chosen[0]

    def create_job(self, request: PublishJobCreate) -> PublishJobRecord:
        if request.mode == "scheduled" and request.scheduled_at is None:
            raise ValueError("定时发布必须提供 scheduled_at")
        with self.database.session() as session:
            output = session.get(OutputRecord, request.output_id)
            account = session.get(PublishAccountRecord, request.account_id)
            if output is None:
                raise KeyError("output")
            if account is None or not account.enabled:
                raise KeyError("account")
            simulation = session.get(SimulationRecord, output.simulation_id)
            if simulation is None:
                raise KeyError("simulation")
            result = self._load_result(simulation)
            run = session.scalar(
                select(PipelineRunRecord).where(PipelineRunRecord.output_id == output.output_id)
            )
            topic = session.get(TopicRecord, run.topic_id) if run else None
            angle = topic.angle if topic else "compound"
            episode_copy = ensure_output_copy(
                self.settings,
                output_id=output.output_id,
                render_id=output.render_id,
                simulation=simulation,
                angle=angle,
            )
            facts = self._facts(result, angle)
            video = Path(output.video_path).resolve()
            portrait = cover_path(self.settings, output.render_id, "portrait").resolve()
            landscape = cover_path(self.settings, output.render_id, "landscape").resolve()
            for path, label in (
                (video, "视频"),
                (portrait, "竖封面"),
                (landscape, "横封面"),
            ):
                if not path.is_file() or path.stat().st_size <= 0:
                    raise ValueError(f"{label}文件不存在或为空：{path}")
            _require_png_dimensions(
                portrait,
                expected=(1080, 1440),
                label="竖封面",
            )
            publish_id = str(uuid4())
            landscape = _publish_landscape_cover(
                self.settings,
                landscape,
                publish_id,
            )
            content = PublishContent(
                title_candidates=episode_copy.title_candidates,
                selected_title=request.title or episode_copy.title,
                selected_template_id=(
                    "custom" if request.title else episode_copy.selected_template_id
                ),
                # 平台会把标题和作品描述连在同一块展示。自动发布默认只放
                # 个性化互动副标题，完整回测说明继续保留在本地 OutputCopy，
                # 不再填进平台作品描述。手工传入的 description 仍原样保留。
                description=request.description or episode_copy.subtitle,
                topics=request.topics or episode_copy.topics,
                collection=request.collection,
                declaration=request.declaration,
            )
            _validate_numeric_claims(content, facts)
            manifest = PublishManifest(
                publish_id=publish_id,
                output_id=output.output_id,
                account_id=account.account_id,
                media=PublishMedia(
                    video_path=str(video),
                    cover_portrait_path=str(portrait),
                    cover_landscape_path=str(landscape),
                ),
                facts=facts,
                content=content,
                mode=request.mode,
                scheduled_at=request.scheduled_at,
            )
            manifest_path = (
                self.settings.data_dir / "publishes" / publish_id / "publish_manifest.json"
            ).resolve()
            _atomic_json(manifest_path, manifest)
            record = PublishJobRecord(
                publish_id=publish_id,
                output_id=output.output_id,
                account_id=account.account_id,
                mode=request.mode,
                scheduled_at=request.scheduled_at,
                manifest_path=str(manifest_path),
                title=content.selected_title,
                description=content.description,
                topics_json=json.dumps(content.topics, ensure_ascii=False),
                collection_name=content.collection,
                declaration=content.declaration,
            )
            session.add(record)
            session.flush()
            return record

    def get_job(self, publish_id: str) -> PublishJobRecord | None:
        with self.database.session() as session:
            return session.get(PublishJobRecord, publish_id)

    def list_jobs(self, limit: int = 100) -> list[PublishJobRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(PublishJobRecord)
                    .order_by(desc(PublishJobRecord.created_at))
                    .limit(limit)
                ).all()
            )

    def load_manifest(self, record: PublishJobRecord) -> PublishManifest:
        return PublishManifest.model_validate_json(
            Path(record.manifest_path).read_text(encoding="utf-8")
        )

    def prepare_manifest_for_publish(
        self,
        record: PublishJobRecord,
    ) -> PublishManifest:
        """Compact legacy system-generated descriptions before browser upload.

        Older queued jobs may already contain ``subtitle + full explanation``.
        Only the exact explanation generated from the manifest facts is removed,
        so a description edited by the user is never overwritten.
        """

        manifest = self.load_manifest(record)
        generated_detail = _build_description(manifest.facts)
        legacy_suffix = f"\n\n{generated_detail}"
        description = manifest.content.description
        if not description.endswith(legacy_suffix):
            return manifest
        compact = description[: -len(legacy_suffix)].strip()
        if not compact:
            return manifest
        content = manifest.content.model_copy(update={"description": compact})
        _validate_numeric_claims(content, manifest.facts)
        manifest.content = content
        _atomic_json(Path(record.manifest_path), manifest)
        record.description = compact
        return manifest

    def update_job(self, publish_id: str, request: PublishJobUpdate) -> PublishJobRecord:
        with self.database.session() as session:
            record = session.get(PublishJobRecord, publish_id)
            if record is None:
                raise KeyError("publish")
            if record.stage not in {
                PublishStage.CREATED,
                PublishStage.READY_FOR_PUBLISH,
                PublishStage.NEEDS_HUMAN,
                PublishStage.FAILED_RETRYABLE,
            }:
                raise ValueError("当前发布阶段不允许修改文案")
            manifest = self.load_manifest(record)
            payload = manifest.content.model_dump()
            for field in ("title", "description", "topics", "collection", "declaration"):
                value = getattr(request, field)
                if value is None:
                    continue
                target = "selected_title" if field == "title" else field
                payload[target] = value
                if field == "title":
                    payload["selected_template_id"] = "custom"
            content = PublishContent.model_validate(payload)
            _validate_numeric_claims(content, manifest.facts)
            manifest.content = content
            _atomic_json(Path(record.manifest_path), manifest)
            record.title = content.selected_title
            record.description = content.description
            record.topics_json = json.dumps(content.topics, ensure_ascii=False)
            record.collection_name = content.collection
            record.declaration = content.declaration
            session.flush()
            return record

    def approve(self, publish_id: str) -> PublishJobRecord:
        with self.database.session() as session:
            record = session.get(PublishJobRecord, publish_id)
            if record is None:
                raise KeyError("publish")
            account = session.get(PublishAccountRecord, record.account_id)
            if account is None or not account.auto_publish_enabled:
                raise ValueError(
                    "尚未开启本系统的“自动点击发布”开关；"
                    "请在“账号与登录”中勾选后再次点击授权"
                )
            record.approved_at = datetime.now(UTC)
            session.flush()
            return record

    def remember_title(self, record: PublishJobRecord, facts: PublishFacts) -> None:
        manifest = self.load_manifest(record)
        with self.database.session() as session:
            existing = session.scalar(
                select(PublishTitleHistoryRecord).where(
                    PublishTitleHistoryRecord.publish_id == record.publish_id
                )
            )
            if existing is not None:
                return
            session.add(
                PublishTitleHistoryRecord(
                    history_id=str(uuid4()),
                    account_id=record.account_id,
                    publish_id=record.publish_id,
                    symbol=facts.symbol,
                    template_id=manifest.content.selected_template_id,
                    title=manifest.content.selected_title,
                    normalized_hash=_normalized_title_hash(manifest.content.selected_title),
                )
            )


def publish_job_payload(record: PublishJobRecord) -> dict[str, object]:
    return {
        "publish_id": record.publish_id,
        "output_id": record.output_id,
        "account_id": record.account_id,
        "stage": record.stage,
        "progress": record.progress,
        "mode": record.mode,
        "scheduled_at": record.scheduled_at,
        "approved_at": record.approved_at,
        "manifest_path": record.manifest_path,
        "title": record.title,
        "description": record.description,
        "topics": json.loads(record.topics_json),
        "collection": record.collection_name,
        "declaration": record.declaration,
        "retry_count": record.retry_count,
        "agent_fallback_count": record.agent_fallback_count,
        "error_type": record.error_type,
        "error_reason": record.error_reason,
        "published_item_id": record.published_item_id,
        "published_url": record.published_url,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def publish_account_payload(record: PublishAccountRecord) -> dict[str, object]:
    return {
        "account_id": record.account_id,
        "platform": record.platform,
        "display_name": record.display_name,
        "enabled": record.enabled,
        "auto_publish_enabled": record.auto_publish_enabled,
        "auth_status": record.auth_status,
        "last_login_at": record.last_login_at,
        "last_checked_at": record.last_checked_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
