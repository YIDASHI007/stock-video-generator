from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_video_generator.models import SimulationResult
from stock_video_generator.narration import AudioTimeline
from stock_video_generator.scripting import NarrationScript
from stock_video_generator.story_hooks import build_story_hook


class CompositionSpec(BaseModel):
    # 横屏 16:9 画布；旧竖屏 spec（1080x1920）组件端按尺寸自适应。
    width: Annotated[int, Field(ge=640, le=3840)] = 1920
    height: Annotated[int, Field(ge=360, le=2160)] = 1080
    fps: Literal[30] = 30
    duration_seconds: float = Field(ge=10, le=180)


class VisualizationInstrument(BaseModel):
    name: str
    symbol: str


class VisualizationSummary(BaseModel):
    initial_capital: float
    final_value: float
    return_pct: float
    currency: str


class StoryHookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    category: str
    text: str
    display_asset_name: str


class TimelineSpec(BaseModel):
    intro_seconds: float
    chart_seconds: float
    outro_seconds: float


class ChartSpec(BaseModel):
    type: Literal["portfolio_value_line"] = "portfolio_value_line"
    line_color_positive: str = "#3ee69b"
    line_color_negative: str = "#ff5d57"
    background: str = "#0b1118"
    show_grid: bool = True
    show_current_dot: bool = True
    show_glow: bool = True
    show_date: bool = True
    show_value: bool = True
    show_return: bool = True
    # 滚动图表窗口：同屏最多展示的交易日数量。
    window_days: int = 250


class Milestone(BaseModel):
    date: date
    type: str
    label: str
    value: float
    return_pct: float


class HistoricalEvent(BaseModel):
    """经来源核验、可对齐到交易时间轴的历史事件。"""

    model_config = ConfigDict(extra="forbid")

    event_date: date
    effective_trading_date: date
    event_type: str
    title: str
    summary: str
    source_label: str
    source_url: str
    confidence: Literal["high", "medium", "low"] = "high"
    impact_label: str | None = None
    tone: Literal["positive", "negative", "neutral"] = "neutral"


class VisualizationPoint(BaseModel):
    date: date
    value: float
    return_pct: float


class ComplianceSpec(BaseModel):
    exchange: str
    fetched_at: str
    actual_buy_date: date
    execution_price: str
    buy_price: float
    fees_included: bool
    dividend_policy: str
    share_mode: str
    currency: str


NarrationEmphasis = Literal["surge", "crash", "sideways", "recovery"]


class NarrationSegmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_date: date
    subtitle: str
    emphasis: NarrationEmphasis
    audio_id: str
    # 播放头到达锚点日期的视频时间（秒）= 该段音频 start_s + duration_s - 0.3s 修正。
    arrive_s: float


class NarrationAudioClipSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["hook", "segment", "finale", "cta"]
    # staticFile() 相对引用：narration/{simulation_id}/{filename}
    file: str
    # 渲染调用方复制源（绝对路径），渲染后清理。
    source_path: str
    start_s: float
    duration_s: float


class NarrationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: str
    gap_s: float
    tail_s: float
    # hook 音频结束时间：播放头从此时起跑。
    hook_end_s: float
    # 播放头到达序列末端的时间（= total - tail）。
    chart_end_s: float
    total_duration_s: float
    segments: list[NarrationSegmentSpec]
    audio: list[NarrationAudioClipSpec]


def build_narration_spec(
    script: NarrationScript,
    timeline: AudioTimeline,
    audio_dir: Path,
) -> NarrationSpec:
    """Map script.json + audio_timeline.json onto the visualization spec section."""
    clip_by_id = {clip.id: clip for clip in timeline.segments}
    segment_specs: list[NarrationSegmentSpec] = []
    for position, segment in enumerate(script.segments):
        audio_id = f"segment_{position + 1:02d}"
        clip = clip_by_id.get(audio_id)
        if clip is None or clip.arrive_s is None:
            raise ValueError(f"音频时间线缺少片段 {audio_id} 的锚点到达时间。")
        segment_specs.append(
            NarrationSegmentSpec(
                anchor_date=segment.anchor_date,
                subtitle=segment.subtitle,
                emphasis=segment.emphasis,
                audio_id=audio_id,
                arrive_s=clip.arrive_s,
            )
        )

    audio_specs = [
        NarrationAudioClipSpec(
            id=clip.id,
            role=clip.role,
            file=f"narration/{timeline.simulation_id}/{clip.file}",
            source_path=str((audio_dir / clip.file).resolve()),
            start_s=clip.start_s,
            duration_s=clip.duration_s,
        )
        for clip in timeline.segments
    ]
    hook = clip_by_id.get("hook")
    finale = clip_by_id.get("finale")
    if hook is None or finale is None:
        raise ValueError("音频时间线缺少 hook 或 finale 片段。")
    return NarrationSpec(
        voice_id=timeline.voice_id,
        gap_s=timeline.gap_s,
        tail_s=timeline.tail_s,
        hook_end_s=hook.duration_s,
        chart_end_s=round(timeline.total_duration_s - timeline.tail_s, 3),
        total_duration_s=timeline.total_duration_s,
        segments=segment_specs,
        audio=audio_specs,
    )


class BgmSpec(BaseModel):
    """背景音乐：整条时间线循环播放，结尾淡出；音量低于人声。"""

    model_config = ConfigDict(extra="forbid")

    # staticFile() 相对引用：bgm/{simulation_id}/{filename}
    file: str
    # 渲染调用方复制源（绝对路径），渲染后清理。
    source_path: str
    volume: float = 0.15
    fade_out_seconds: float = 2.0


class VisualizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    template_version: Literal["v1"] = "v1"
    simulation_id: str
    composition: CompositionSpec
    title: str
    subtitle: str
    instrument: VisualizationInstrument
    summary: VisualizationSummary
    story_hook: StoryHookSpec | None = None
    timeline: TimelineSpec
    chart: ChartSpec
    milestones: list[Milestone]
    events: list[HistoricalEvent] = Field(default_factory=list, max_length=5)
    series: list[VisualizationPoint]
    source_label: str
    calculation_label: str
    compliance: ComplianceSpec
    narration: NarrationSpec | None = None
    bgm: BgmSpec | None = None
    disclaimer: str = "历史数据模拟，仅供信息展示，不构成投资建议。"

    @model_validator(mode="after")
    def event_dates_must_exist_in_series(self) -> VisualizationSpec:
        series_dates = {point.date for point in self.series}
        missing = [
            event.effective_trading_date
            for event in self.events
            if event.effective_trading_date not in series_dates
        ]
        if missing:
            raise ValueError(f"事件交易日必须保留在可视化序列中：{sorted(set(missing))}")
        return self


def _milestone(
    result: SimulationResult,
    index: int,
    milestone_type: str,
    label: str,
) -> Milestone:
    point = result.series[index]
    return Milestone(
        date=point.date,
        type=milestone_type,
        label=label,
        value=point.portfolio_value,
        return_pct=point.total_return_pct,
    )


def extract_milestones(result: SimulationResult) -> list[Milestone]:
    points = result.series
    if not points:
        return []
    milestones: list[Milestone] = [
        _milestone(result, 0, "buy", "实际买入"),
    ]

    first_profit = next(
        (index for index, point in enumerate(points) if point.total_return_pct > 0),
        None,
    )
    if first_profit is not None:
        milestones.append(_milestone(result, first_profit, "first_profit", "首次盈利"))

    first_loss_ten = next(
        (index for index, point in enumerate(points) if point.total_return_pct <= -10),
        None,
    )
    if first_loss_ten is not None:
        milestones.append(_milestone(result, first_loss_ten, "loss_10_pct", "首次亏损超过 10%"))

    highest_index = max(
        range(len(points)),
        key=lambda index: points[index].portfolio_value,
    )
    milestones.append(_milestone(result, highest_index, "all_time_high", "资产历史最高"))

    trough_index = min(
        range(len(points)),
        key=lambda index: points[index].drawdown_pct,
    )
    peak_index = max(
        range(trough_index + 1),
        key=lambda index: points[index].portfolio_value,
    )
    if trough_index != peak_index and points[trough_index].drawdown_pct < 0:
        milestones.extend(
            [
                _milestone(
                    result,
                    peak_index,
                    "max_drawdown_start",
                    "最大回撤起点",
                ),
                _milestone(
                    result,
                    trough_index,
                    "max_drawdown_end",
                    "最大回撤终点",
                ),
            ]
        )

    point_indexes = {point.date: index for index, point in enumerate(points)}
    for event in result.events:
        if event.event_type not in {"dividend", "split"}:
            continue
        point_index = point_indexes.get(event.date)
        if point_index is not None:
            milestones.append(
                _milestone(
                    result,
                    point_index,
                    event.event_type,
                    "实际分红" if event.event_type == "dividend" else "拆合股",
                )
            )

    milestones.append(_milestone(result, len(points) - 1, "final", "最终资产"))
    unique: dict[tuple[date, str], Milestone] = {}
    for item in milestones:
        unique[(item.date, item.type)] = item
    return sorted(unique.values(), key=lambda item: (item.date, item.type))


def _downsample(
    result: SimulationResult,
    maximum_points: int = 900,
    required_dates: set[date] | None = None,
) -> list[VisualizationPoint]:
    series = result.series
    if len(series) <= maximum_points:
        selected = series
    else:
        last_index = len(series) - 1
        indexes = sorted(
            {round(step * last_index / (maximum_points - 1)) for step in range(maximum_points)}
        )
        selected = [series[index] for index in indexes]
    if required_dates:
        present = {point.date for point in selected}
        missing = [point for point in series if point.date in required_dates - present]
        if missing:
            selected = sorted([*selected, *missing], key=lambda point: point.date)
    return [
        VisualizationPoint(
            date=point.date,
            value=point.portfolio_value,
            return_pct=point.total_return_pct,
        )
        for point in selected
    ]


def build_visualization_spec(
    result: SimulationResult,
    narration: NarrationSpec | None = None,
    *,
    excluded_story_hook_template_ids: set[str] | None = None,
    preferred_story_hook_template_id: str | None = None,
) -> VisualizationSpec:
    video = result.assumptions["video"]
    if narration is not None:
        duration = round(narration.total_duration_s, 1)
        intro_seconds = narration.hook_end_s
        finale_start = next(
            (clip.start_s for clip in narration.audio if clip.role == "finale"),
            narration.chart_end_s,
        )
        outro_seconds = round(narration.total_duration_s - finale_start, 3)
        chart_seconds = round(duration - intro_seconds - outro_seconds, 3)
    else:
        duration = int(video["duration_seconds"])
        intro_seconds = 2
        # 无配音：播放头匀速推进，结尾拉远全景固定约 2 秒。
        outro_seconds = 2.0
        chart_seconds = duration - intro_seconds - outro_seconds
    dividend_policy = str(result.assumptions["dividend_policy"])
    dividend_label = {
        "ignore": "不计分红",
        "cash": "现金分红",
        "reinvest": "红利复投",
    }[dividend_policy]
    initial_capital = float(result.assumptions["initial_capital"])
    story_hook = build_story_hook(
        result,
        excluded_template_ids=excluded_story_hook_template_ids,
        preferred_template_id=preferred_story_hook_template_id,
    )

    return VisualizationSpec(
        simulation_id=result.simulation_id,
        composition=CompositionSpec(
            duration_seconds=duration,
            width=int(video.get("width", 1920)),
            height=int(video.get("height", 1080)),
        ),
        title=(
            f"假如在 {result.summary.actual_buy_date:%Y年%m月%d日} 投入 {initial_capital:,.0f} 元"
        ),
        subtitle=f"买入 {result.instrument.name} · 一直持有 · {dividend_label}",
        instrument=VisualizationInstrument(
            name=result.instrument.name,
            symbol=result.instrument.symbol,
        ),
        summary=VisualizationSummary(
            initial_capital=initial_capital,
            final_value=result.summary.final_value,
            return_pct=result.summary.total_return_pct,
            currency=result.instrument.currency,
        ),
        story_hook=StoryHookSpec(
            template_id=story_hook.template_id,
            category=story_hook.category,
            text=story_hook.text,
            display_asset_name=story_hook.display_asset_name,
        ),
        timeline=TimelineSpec(
            intro_seconds=intro_seconds,
            chart_seconds=chart_seconds,
            outro_seconds=outro_seconds,
        ),
        chart=ChartSpec(),
        milestones=extract_milestones(result),
        series=_downsample(
            result,
            required_dates=(
                {segment.anchor_date for segment in narration.segments}
                if narration is not None
                else None
            ),
        ),
        narration=narration,
        source_label=f"数据来源：{result.source.provider}",
        calculation_label=(
            f"口径：未复权价格 · {dividend_label} · "
            f"{'含手续费' if result.assumptions['fee_policy']['enabled'] else '不含手续费'}"
        ),
        compliance=ComplianceSpec(
            exchange=result.instrument.exchange,
            fetched_at=result.source.fetched_at.isoformat(),
            actual_buy_date=result.summary.actual_buy_date,
            execution_price=str(result.assumptions["execution_price"]),
            buy_price=result.summary.buy_price,
            fees_included=bool(result.assumptions["fee_policy"]["enabled"]),
            dividend_policy=dividend_policy,
            share_mode=str(result.assumptions["share_mode"]),
            currency=result.instrument.currency,
        ),
    )
