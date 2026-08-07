"""Narration script generation for the "假如某年花100万买入某股票" video series.

Default path is a deterministic template generator (zero configuration, no LLM
key required). If OPENAI_COMPATIBLE_BASE_URL / OPENAI_COMPATIBLE_API_KEY /
OPENAI_COMPATIBLE_MODEL are configured, an LLM is used instead, but its output
must pass the exact same number/date reconciliation validator; on any failure
the generator falls back to the template with a warning.

Honesty principle: every amount, percentage and full date that appears in the
script must be reconcilable against the simulation data. Validation failure
raises ScriptValidationError, which fails the job as FAILED_FINAL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from stock_video_generator.errors import ScriptValidationError
from stock_video_generator.models import SimulationResult

logger = logging.getLogger(__name__)

Emphasis = Literal["surge", "crash", "sideways", "recovery"]

MAX_NARRATION_CHARS = 30
MIN_SEGMENTS = 4
MAX_SEGMENTS = 8

DISCLAIMER = "历史数据模拟，仅供信息展示，不构成投资建议。"
CTA = "如果是你，你会在哪一天卖掉？评论区聊聊。"


class ScriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_date: date
    narration: str
    subtitle: str
    emphasis: Emphasis


class NarrationScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    hook: str
    segments: list[ScriptSegment]
    finale: str
    cta: str
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------------------
# formatting helpers (deterministic)
# ---------------------------------------------------------------------------


def _cn_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def _wan(value: float) -> str:
    """万元，最多一位小数，去掉多余的 .0。"""
    text = f"{value / 10000:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _pct(value: float, *, signed: bool = True) -> str:
    return f"{value:+.1f}" if signed else f"{value:.1f}"


def _ym(value: date) -> str:
    return f"{value.year}.{value.month:02d}"


# ---------------------------------------------------------------------------
# anchor extraction
# ---------------------------------------------------------------------------


def _first_index_on_or_after(series: list, target: date) -> int | None:
    for index, point in enumerate(series):
        if point.date >= target:
            return index
    return None


def _select_anchors(result: SimulationResult) -> list[tuple[int, str]]:
    """Return a chronologically sorted, deduplicated list of (series_index, kind).

    kind ∈ {buy, high, trough, recovery, recent_year, final, filler}.
    """
    series = result.series
    last_index = len(series) - 1
    anchors: dict[int, str] = {0: "buy", last_index: "final"}

    high_index = max(range(len(series)), key=lambda i: series[i].portfolio_value)
    anchors.setdefault(high_index, "high")

    trough_index = min(range(len(series)), key=lambda i: series[i].drawdown_pct)
    recovery_index: int | None = None
    if series[trough_index].drawdown_pct <= -5 and trough_index not in anchors:
        anchors[trough_index] = "trough"
        peak_index = max(
            range(trough_index + 1),
            key=lambda i: series[i].portfolio_value,
        )
        peak_value = series[peak_index].portfolio_value
        for index in range(trough_index + 1, len(series)):
            if series[index].portfolio_value >= peak_value:
                recovery_index = index
                break
        if recovery_index is None:
            # 没有完全收复失地：用谷底后的反弹高点作为恢复段锚点。
            post = range(trough_index + 1, len(series))
            candidates = list(post)
            if candidates:
                recovery_index = max(candidates, key=lambda i: series[i].portfolio_value)
                if recovery_index is not None and recovery_index not in anchors:
                    anchors[recovery_index] = "rebound"
                    recovery_index = None
        if recovery_index is not None and recovery_index not in anchors:
            anchors[recovery_index] = "recovery"

    span_days = (series[-1].date - series[0].date).days
    if span_days > 400:
        recent_index = _first_index_on_or_after(
            series, series[-1].date - timedelta(days=365)
        )
    else:
        recent_index = last_index // 2
    if (
        recent_index is not None
        and recent_index not in anchors
        and recent_index < last_index
    ):
        anchors[recent_index] = "recent_year"

    # 段数不足时用时间轴上的中间点补齐到 4 段（保证 4-8 段约束）。
    filler = 0
    while len(anchors) < MIN_SEGMENTS:
        fraction = (filler + 1) / (MIN_SEGMENTS + 1)
        index = round(fraction * last_index)
        if index not in anchors and 0 < index < last_index:
            anchors[index] = "filler"
        filler += 1
        if filler > 50:
            break

    ordered = sorted(anchors.items())
    return ordered[:MAX_SEGMENTS]


# ---------------------------------------------------------------------------
# template generation
# ---------------------------------------------------------------------------


def _segment_texts(
    result: SimulationResult,
    index: int,
    kind: str,
) -> tuple[str, str, Emphasis]:
    series = result.series
    point = series[index]
    name = result.instrument.name
    value_wan = _wan(point.portfolio_value)
    ret = point.total_return_pct
    drawdown = point.drawdown_pct

    if kind == "buy":
        initial = float(result.assumptions["initial_capital"])
        narration = f"{_cn_date(point.date)}，{_wan(initial)}万全仓{name}。"
        subtitle = f"{_ym(point.date)} 买入：{_wan(initial)}万"
        return narration, subtitle, "sideways"

    if kind == "high":
        if ret > 0:
            narration = (
                f"{point.date.year}年{point.date.month}月冲到{value_wan}万，"
                f"赚了{ret:.1f}%。"
            )
        else:
            narration = f"{point.date.year}年{point.date.month}月是最高点，值{value_wan}万。"
        subtitle = f"{_ym(point.date)} 高点：{_pct(ret)}%"
        return narration, subtitle, "surge"

    if kind == "trough":
        narration = (
            f"{point.date.year}年{point.date.month}月最惨，"
            f"只剩{value_wan}万，回撤{_pct(drawdown)}%。"
        )
        subtitle = f"{_ym(point.date)} 谷底：{_pct(drawdown)}%"
        return narration, subtitle, "crash"

    if kind == "recovery":
        narration = f"{point.date.year}年{point.date.month}月收复失地，重回{value_wan}万。"
        subtitle = f"{_ym(point.date)} 收复：{_pct(ret)}%"
        return narration, subtitle, "recovery"

    if kind == "rebound":
        narration = f"之后反弹，{point.date.year}年{point.date.month}月回到{value_wan}万。"
        subtitle = f"{_ym(point.date)} 反弹：{_pct(ret)}%"
        return narration, subtitle, "recovery"

    if kind == "recent_year":
        final_value = series[-1].portfolio_value
        narration = f"一年前的今天，账上有{value_wan}万。"
        change_pct = (final_value / point.portfolio_value - 1) * 100
        emphasis: Emphasis = (
            "sideways" if abs(change_pct) < 5 else ("surge" if change_pct > 0 else "crash")
        )
        subtitle = f"{_ym(point.date)} 近一年：{_pct(change_pct)}%"
        return narration, subtitle, emphasis

    if kind == "final":
        narration = f"故事的结局，定格在{value_wan}万。"
        subtitle = f"结局：{_pct(ret)}%"
        return narration, subtitle, "surge" if ret >= 0 else "crash"

    # filler
    narration = f"这一天，账上大约是{value_wan}万。"
    subtitle = f"{_ym(point.date)} 途中：{_pct(ret)}%"
    return narration, subtitle, "sideways"


def generate_script_template(result: SimulationResult) -> NarrationScript:
    """Deterministic zero-config script generation from simulation data."""
    series = result.series
    if len(series) < 2:
        raise ScriptValidationError("回测数据不足，无法生成解说脚本。")

    initial = float(result.assumptions["initial_capital"])
    name = result.instrument.name
    buy_date = result.summary.actual_buy_date
    hook = f"{_cn_date(buy_date)}，你把{_wan(initial)}万全仓了{name}。"

    segments = [
        ScriptSegment(
            anchor_date=series[index].date,
            narration=narration,
            subtitle=subtitle,
            emphasis=emphasis,
        )
        for index, kind in _select_anchors(result)
        for narration, subtitle, emphasis in [_segment_texts(result, index, kind)]
    ]

    final_wan = _wan(result.summary.final_value)
    if result.summary.total_return_pct >= 0:
        finale = f"拿到今天，{_wan(initial)}万变成了{final_wan}万。"
    else:
        finale = f"拿到今天，{_wan(initial)}万只剩{final_wan}万。"

    script = NarrationScript(
        hook=hook,
        segments=segments,
        finale=finale,
        cta=CTA,
    )
    validate_script(script, result)
    return script


# ---------------------------------------------------------------------------
# number/date reconciliation validator
# ---------------------------------------------------------------------------

_DATE_CN_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_DATE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)%")
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万)")

PCT_TOLERANCE = 0.06
WAN_TOLERANCE = 0.06


def _candidate_sets(result: SimulationResult) -> tuple[set[date], list[float], list[float]]:
    series = result.series
    dates = {point.date for point in series}
    pcts = [point.total_return_pct for point in series]
    pcts += [point.drawdown_pct for point in series]
    pcts += [result.summary.total_return_pct, result.summary.max_drawdown_pct]
    # “从某一天拿到结局”的收益率同样可直接由序列复核。
    final_value = series[-1].portfolio_value
    pcts += [
        (final_value / point.portfolio_value - 1) * 100
        for point in series
        if point.portfolio_value > 0
    ]
    wans = [point.portfolio_value / 10000 for point in series]
    summary = result.summary
    wans += [
        float(result.assumptions["initial_capital"]) / 10000,
        summary.final_value / 10000,
        summary.best_value / 10000,
        summary.worst_value / 10000,
        summary.dividend_total / 10000,
    ]
    return dates, pcts, wans


def _check_text(
    label: str,
    text: str,
    dates: set[date],
    pcts: list[float],
    wans: list[float],
) -> list[str]:
    errors: list[str] = []
    for match in _DATE_CN_RE.finditer(text):
        candidate = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if candidate not in dates:
            errors.append(f"{label} 中的日期 {match.group(0)} 在回测序列中不存在")
    for match in _DATE_ISO_RE.finditer(text):
        candidate = date.fromisoformat(match.group(0))
        if candidate not in dates:
            errors.append(f"{label} 中的日期 {match.group(0)} 在回测序列中不存在")
    for match in _PERCENT_RE.finditer(text):
        value = float(match.group(1))
        if not any(abs(value - candidate) <= PCT_TOLERANCE for candidate in pcts):
            errors.append(f"{label} 中的百分比 {match.group(0)} 无法与回测数据对账")
    for match in _AMOUNT_RE.finditer(text):
        value = float(match.group(1)) * (10000 if match.group(2) == "亿" else 1)
        if not any(abs(value - candidate) <= WAN_TOLERANCE for candidate in wans):
            errors.append(f"{label} 中的金额 {match.group(0)} 无法与回测数据对账")
    return errors


def validate_script(script: NarrationScript, result: SimulationResult) -> None:
    """Reconcile every number/date in the script against simulation data.

    Raises ScriptValidationError on any mismatch (job → FAILED_FINAL).
    """
    dates, pcts, wans = _candidate_sets(result)
    errors: list[str] = []

    if not (MIN_SEGMENTS <= len(script.segments) <= MAX_SEGMENTS):
        errors.append(
            f"脚本段落数 {len(script.segments)} 不在 {MIN_SEGMENTS}-{MAX_SEGMENTS} 范围内"
        )
    for position, segment in enumerate(script.segments):
        label = f"第{position + 1}段"
        if segment.anchor_date not in dates:
            errors.append(f"{label} 锚点日期 {segment.anchor_date} 在回测序列中不存在")
        if len(segment.narration) > MAX_NARRATION_CHARS:
            errors.append(
                f"{label} 解说词 {len(segment.narration)} 字超过 {MAX_NARRATION_CHARS} 字上限"
            )
        errors += _check_text(f"{label}解说词", segment.narration, dates, pcts, wans)
        errors += _check_text(f"{label}字幕", segment.subtitle, dates, pcts, wans)

    errors += _check_text("hook", script.hook, dates, pcts, wans)
    errors += _check_text("finale", script.finale, dates, pcts, wans)
    errors += _check_text("cta", script.cta, dates, pcts, wans)

    if errors:
        raise ScriptValidationError(
            "解说脚本数字对账失败，已阻止后续配音与渲染。",
            detail="；".join(errors),
        )


# ---------------------------------------------------------------------------
# optional LLM generation (OpenAI-compatible), with template fallback
# ---------------------------------------------------------------------------


def _llm_settings() -> tuple[str, str, str] | None:
    base_url = os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
    api_key = (
        os.environ.get("OPENAI_COMPATIBLE_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    model = (
        os.environ.get("OPENAI_COMPATIBLE_MODEL", "").strip()
        or os.environ.get("OPENAI_MODEL", "").strip()
    )
    if base_url and api_key and model:
        return base_url.rstrip("/"), api_key, model
    return None


def _script_facts(result: SimulationResult) -> dict[str, object]:
    anchors = _select_anchors(result)
    series = result.series
    return {
        "instrument": result.instrument.name,
        "symbol": result.instrument.symbol,
        "currency": result.instrument.currency,
        "initial_capital": float(result.assumptions["initial_capital"]),
        "buy_date": str(result.summary.actual_buy_date),
        "final_value": result.summary.final_value,
        "total_return_pct": result.summary.total_return_pct,
        "max_drawdown_pct": result.summary.max_drawdown_pct,
        "anchors": [
            {
                "kind": kind,
                "date": str(series[index].date),
                "portfolio_value": series[index].portfolio_value,
                "total_return_pct": series[index].total_return_pct,
                "drawdown_pct": series[index].drawdown_pct,
            }
            for index, kind in anchors
        ],
    }


_LLM_SYSTEM_PROMPT = (
    "你是竖屏短视频的口播编剧。根据给定的股票历史回测数据，写一段中文口语解说脚本。"
    "硬性要求：1) 只输出符合给定 JSON schema 的 JSON，不要输出任何其他文字；"
    "2) 每个金额、百分比、日期必须原样取自输入数据，禁止编造或四舍五入到输入里不存在的精度；"
    "3) 每段 narration 不超过 30 个汉字，口语化、有钩子，不使用 emoji；"
    "4) 亏损用“还剩/只剩”，盈利用“变成”；"
    "5) anchor_date 必须原样使用输入 anchors 里的 date；"
    "6) segments 数量 4-8 段，按时间顺序；emphasis 只能是 surge|crash|sideways|recovery。"
)


async def _generate_script_llm(result: SimulationResult) -> NarrationScript:
    settings = _llm_settings()
    assert settings is not None
    base_url, api_key, model = settings
    facts = _script_facts(result)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "回测关键数据如下（单位：元，百分比为数值）：\n"
                    f"{json.dumps(facts, ensure_ascii=False)}\n"
                    "输出 JSON：{hook, segments: [{anchor_date, narration, subtitle, emphasis}], "
                    "finale, cta, disclaimer}"
                ),
            },
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    script = NarrationScript.model_validate_json(content)
    validate_script(script, result)
    return script


async def generate_script(result: SimulationResult) -> NarrationScript:
    """LLM when configured, otherwise the deterministic template.

    Any LLM failure (network, schema, reconciliation) falls back to the
    template generator with a warning, so the pipeline stays zero-config.
    """
    if _llm_settings() is not None:
        try:
            return await _generate_script_llm(result)
        except Exception as exc:
            logger.warning("LLM 脚本生成失败，回退确定性模板：%s", exc)
    return generate_script_template(result)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def save_script(path: Path, script: NarrationScript) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        script.model_dump_json(indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_script(path: Path) -> NarrationScript:
    return NarrationScript.model_validate_json(path.read_text(encoding="utf-8"))
