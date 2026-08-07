import type {
  HistoricalEvent,
  NarrationEmphasis,
  VisualizationSpec,
} from "@stock-video/schemas";
import {
  area as createArea,
  curveMonotoneX,
  line as createLine,
} from "d3-shape";
import React, {useMemo} from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type Props = {
  spec: VisualizationSpec;
  filledArea?: boolean;
  openingHook?: boolean;
  storyNarrative?: boolean;
};

const clamp = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
};

/** 游标默认锚定在图表区从左到右 78% 处。 */
const DEFAULT_CURSOR_ANCHOR = 0.78;
/** 结局拉远后游标位于右缘（展示全周期全景）。 */
const OUTRO_CURSOR_ANCHOR = 1;
/** Y 轴目标范围包含的未来前瞻比例（相对于窗口宽度）。 */
const LOOKAHEAD_RATIO = 0.1;
/** 相邻帧 Y 轴指数滑动平均的 lerp 系数。 */
const Y_SMOOTH_FACTOR = 0.08;
/** 每帧构造 path 的采样点上限。 */
const MAX_PATH_SAMPLES = 320;
/** X 轴刻度标签在左右边缘的淡入淡出宽度（px）。 */
const TICK_FADE_PX = 80;
/** 测试版时间轴刻度的最小水平间距，避免开场日期拥挤。 */
const MIN_TICK_GAP_PX = 170;

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

const splitCompanyName = (name: string): string[] => {
  const normalized = name.trim().replace(/\s+/g, " ");
  const hasCjk = /[\u3400-\u9fff]/.test(normalized);
  const limit = hasCjk ? 10 : 28;
  if (normalized.length <= limit) return [normalized];

  if (hasCjk) {
    const characters = Array.from(normalized);
    const preferredBreaks = ["股份有限公司", "有限公司", "控股集团", "集团"];
    for (const suffix of preferredBreaks) {
      const suffixIndex = normalized.indexOf(suffix);
      if (suffixIndex >= 4 && suffixIndex <= characters.length - 4) {
        return [normalized.slice(0, suffixIndex), normalized.slice(suffixIndex)];
      }
    }
    const midpoint = Math.ceil(characters.length / 2);
    return [characters.slice(0, midpoint).join(""), characters.slice(midpoint).join("")];
  }

  const words = normalized.split(" ");
  const midpoint = normalized.length / 2;
  let first = "";
  let second = "";
  for (const word of words) {
    if (!second && (first.length === 0 || `${first} ${word}`.length <= midpoint)) {
      first = first ? `${first} ${word}` : word;
    } else {
      second = second ? `${second} ${word}` : word;
    }
  }
  return second ? [first, second] : [normalized];
};

const visualTextUnits = (text: string): number =>
  Array.from(text).reduce(
    (total, character) =>
      total + (/[^\u0000-\u00ff]/.test(character) ? 1 : 0.56),
    0,
  );

/** emphasis 情绪配色（克制使用：字幕描边 + 图表区轻脉冲光晕）。 */
const EMPHASIS_HEX: Record<NarrationEmphasis, string> = {
  surge: "#3ee69b",
  crash: "#ff5d57",
  recovery: "#6ea8fe",
  sideways: "#9aa7b2",
};
const EMPHASIS_RGB: Record<NarrationEmphasis, string> = {
  surge: "62,230,155",
  crash: "255,93,87",
  recovery: "110,168,254",
  sideways: "154,167,178",
};

const EVENT_COLORS: Record<HistoricalEvent["tone"], string> = {
  positive: "#ffd166",
  negative: "#ff7b72",
  neutral: "#79c0ff",
};

const currencySymbol = (currency: string): string => {
  if (currency === "CNY") return "¥";
  if (currency === "HKD") return "HK$";
  if (currency === "USD") return "$";
  return `${currency} `;
};

const compactAmount = (value: number, currency: string): string => {
  const sign = currencySymbol(currency);
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) return `${sign}${(value / 100_000_000).toFixed(2)}亿`;
  if (absolute >= 10_000) return `${sign}${(value / 10_000).toFixed(1)}万`;
  return `${sign}${value.toLocaleString("zh-CN", {maximumFractionDigits: 0})}`;
};

const compactAmountClean = (value: number, currency: string): string =>
  compactAmount(value, currency).replace(".0万", "万").replace(".00亿", "亿");

const dateLabel = (isoDate: string): string => {
  const [year, month, day] = isoDate.split("-");
  return `${year}.${month}.${day}`;
};

type TickGranularity = "month" | "quarter" | "year";

const periodKey = (isoDate: string, granularity: TickGranularity): number => {
  const year = Number(isoDate.slice(0, 4));
  const month = Number(isoDate.slice(5, 7));
  if (granularity === "year") return year;
  if (granularity === "quarter") return year * 10 + Math.floor((month - 1) / 3);
  return year * 100 + month;
};

const tickLabel = (isoDate: string, granularity: TickGranularity): string => {
  const year = isoDate.slice(0, 4);
  const month = Number(isoDate.slice(5, 7));
  if (granularity === "year") return year;
  if (granularity === "quarter") return `${year}.Q${Math.floor((month - 1) / 3) + 1}`;
  return `${year}.${isoDate.slice(5, 7)}`;
};

export const ScrollingStockVideo: React.FC<Props> = ({
  spec,
  filledArea = false,
  openingHook = false,
  storyNarrative = false,
}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const series = spec.series;
  const pointCount = series.length;
  const values = useMemo(() => series.map((point) => point.value), [series]);

  // 横屏（默认 1920x1080）与旧竖屏 spec（1080x1920）的自适应布局。
  const landscape = spec.composition.width >= spec.composition.height;
  const chart = landscape
    ? {
        left: 110,
        right: 1840,
        top: storyNarrative ? 150 : 300,
        bottom: 900,
      }
    : {left: 92, right: 988, top: 670, bottom: 1420};
  const layout = landscape
    ? {
        sidePad: 80,
        headerTop: 60,
        kickerSize: 22,
        titleSize: 44,
        titleMargin: "12px 0 8px",
        subtitleSize: 26,
        sectionTop: 224,
        sectionAlign: "flex-start" as const,
        instrumentSize: 30,
        symbolSize: 22,
        returnLabelSize: 22,
        returnSize: 52,
        cardTop: 908,
        cardRadius: 24,
        cardPadding: "14px 32px",
        cardLabelSize: 20,
        cardValueSize: 40,
        cardDateSize: 30,
        footerBottom: 18,
        footerSize: 15,
        captionBottom: 170,
        captionSize: 30,
      }
    : {
        sidePad: 64,
        headerTop: 88,
        kickerSize: 31,
        titleSize: 57,
        titleMargin: "20px 0 13px",
        subtitleSize: 31,
        sectionTop: 405,
        sectionAlign: "flex-end" as const,
        instrumentSize: 32,
        symbolSize: 25,
        returnLabelSize: 25,
        returnSize: 57,
        cardTop: 1510,
        cardRadius: 30,
        cardPadding: "31px 36px 33px",
        cardLabelSize: 24,
        cardValueSize: 53,
        cardDateSize: 39,
        footerBottom: 61,
        footerSize: 20,
        captionBottom: 214,
        captionSize: 32,
      };

  const windowDays = Math.max(
    20,
    Math.min(pointCount, Math.round(spec.chart.window_days ?? 250)),
  );
  const cursorAnchorBase = Math.min(
    0.95,
    Math.max(0.3, spec.chart.cursor_anchor ?? DEFAULT_CURSOR_ANCHOR),
  );
  const plotWidth = chart.right - chart.left;

  const introFrames = storyNarrative
    ? Math.round(fps * 0.5)
    : Math.round(spec.timeline.intro_seconds * fps);
  const outroFrames = storyNarrative
    ? Math.max(Math.round(fps * 4), Math.round(spec.timeline.outro_seconds * fps))
    : Math.round(spec.timeline.outro_seconds * fps);
  const outroStart = durationInFrames - outroFrames;
  const chartFrames = storyNarrative
    ? outroStart - introFrames
    : Math.round(spec.timeline.chart_seconds * fps);

  const narration = spec.narration ?? null;
  const events = spec.events ?? [];

  const eventPoints = useMemo(
    () =>
      events
        .map((event) => ({
          event,
          index: series.findIndex(
            (point) => point.date >= event.effective_trading_date,
          ),
        }))
        .filter((item) => item.index >= 0)
        .sort((a, b) => a.index - b.index),
    [events, series],
  );

  // 配音锚点 → 序列下标。锚点日期已由生成侧保证存在于（抽稀后的）series 中。
  const anchorPoints = useMemo(() => {
    if (!narration) return null;
    const points = narration.segments
      .map((segment) => ({
        t: segment.arrive_s,
        index: series.findIndex((point) => point.date === segment.anchor_date),
        subtitle: segment.subtitle,
        emphasis: segment.emphasis,
      }))
      .filter((point) => point.index >= 0)
      .sort((a, b) => a.index - b.index);
    // 段区间起点：第一段从 hook 结束起，之后每段从上一锚点到达时刻起。
    return points.map((point, position) => ({
      ...point,
      startT: position === 0 ? narration.hook_end_s : points[position - 1].t,
    }));
  }, [narration, series]);

  // 播放头关键帧：(hook 结束, 0) → 各锚点 → (chart_end, 末尾)，时间严格单调。
  const playheadKeys = useMemo(() => {
    if (!narration || !anchorPoints || anchorPoints.length === 0) return null;
    const keys: {t: number; index: number}[] = [
      {t: storyNarrative ? 0.5 : narration.hook_end_s, index: 0},
    ];
    for (const point of anchorPoints) {
      keys.push({t: Math.max(point.t, keys[keys.length - 1].t + 0.001), index: point.index});
    }
    keys.push({
      t: Math.max(narration.chart_end_s, keys[keys.length - 1].t + 0.001),
      index: pointCount - 1,
    });
    return keys;
  }, [narration, anchorPoints, pointCount, storyNarrative]);

  const headAtTime = (t: number): number => {
    if (!playheadKeys) return Number.NaN;
    if (t <= playheadKeys[0].t) return playheadKeys[0].index;
    for (let i = 1; i < playheadKeys.length; i += 1) {
      const previous = playheadKeys[i - 1];
      const next = playheadKeys[i];
      if (t <= next.t) {
        const span = Math.max(1e-6, next.t - previous.t);
        const ratio = Math.min(1, Math.max(0, (t - previous.t) / span));
        return lerp(previous.index, next.index, ratio);
      }
    }
    return playheadKeys[playheadKeys.length - 1].index;
  };

  const introOpacity = interpolate(
    frame,
    [0, Math.round(fps * (storyNarrative ? 0.28 : 0.8))],
    [0, 1],
    clamp,
  );
  const finalEmphasis = interpolate(
    frame,
    [outroStart, outroStart + Math.round(fps * 0.6)],
    [0, 1],
    clamp,
  );

  // 某一帧的滚动状态：播放头下标、窗口跨度（下标数）、游标锚定比例。
  // 有配音时播放头与音频锚点对齐（各锚点段区间内线性推进）；
  // 无配音时 chart 段内播放头从 0 线性推进到末尾；outro 段窗口拉远到全周期。
  const stateAtFrame = (targetFrame: number) => {
    const head = playheadKeys
      ? headAtTime(targetFrame / fps)
      : interpolate(
          targetFrame,
          [introFrames, introFrames + chartFrames],
          [0, 1],
          clamp,
        ) * Math.max(0, pointCount - 1);
    const zoomOut = interpolate(
      targetFrame,
      // 拉远在 outro 前 75% 内完成，最后约 0.5 秒定格全景供观看。
      [outroStart, outroStart + Math.max(1, Math.round((durationInFrames - 1 - outroStart) * 0.75))],
      [0, 1],
      {...clamp, easing: Easing.inOut(Easing.cubic)},
    );
    const normalSpan = Math.min(windowDays - 1, pointCount - 1);
    // 面积测试版开场时只有很少的已发生数据：先让这些数据铺开到游标锚点，
    // 再随历史累积平滑扩大窗口，避免按完整 250 日窗口计算而把日期挤在左侧。
    const openingSpan = filledArea && !storyNarrative
      ? Math.min(
          normalSpan,
          Math.max(8, head / Math.max(0.05, cursorAnchorBase)),
        )
      : normalSpan;
    const span = lerp(
      openingSpan,
      Math.max(1, pointCount - 1),
      zoomOut,
    );
    const anchorRaw = lerp(cursorAnchorBase, OUTRO_CURSOR_ANCHOR, zoomOut);
    // 起始阶段历史不足以填满窗口左側时，游标从左缘起步，
    // 随数据积累逐渐右移，填满窗口后锁定在锚定位（避免开场大片空白）。
    const anchor =
      span > 0 ? Math.min(anchorRaw, Math.max(0, head) / span) : anchorRaw;
    return {head, span, anchor};
  };

  // 某一帧 Y 轴目标范围：当前窗口内可见数据 + 约 10% 未来前瞻的 min/max。
  const targetRangeAtFrame = (targetFrame: number): {min: number; max: number} => {
    const {head, span, anchor} = stateAtFrame(targetFrame);
    const leftmost = head - anchor * span;
    const lookahead = head + LOOKAHEAD_RATIO * span;
    const lo = Math.max(0, Math.min(pointCount - 1, Math.floor(leftmost)));
    const hi = Math.max(lo, Math.min(pointCount - 1, Math.ceil(lookahead)));
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (let index = lo; index <= hi; index += 1) {
      const value = values[index];
      if (value < min) min = value;
      if (value > max) max = value;
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      min = values[0];
      max = values[0];
    }
    if (filledArea) {
      min = Math.min(min, spec.summary.initial_capital);
      max = Math.max(max, spec.summary.initial_capital);
    }
    return {min, max};
  };

  // Y 轴平滑：从第 0 帧到当前帧对目标范围做确定性指数滑动平均。
  // Remotion 并发渲染时每一帧独立求值，因此这里按帧重放 EMA 而不是依赖跨帧 ref。
  const smoothedRange = useMemo(() => {
    let current = targetRangeAtFrame(0);
    for (let f = 1; f <= frame; f += 1) {
      const target = targetRangeAtFrame(f);
      const smoothingFactor = filledArea ? 0.22 : Y_SMOOTH_FACTOR;
      current = {
        min: lerp(current.min, target.min, smoothingFactor),
        max: lerp(current.max, target.max, smoothingFactor),
      };
    }
    return current;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, series, windowDays, cursorAnchorBase, introFrames, chartFrames, outroStart, durationInFrames, filledArea, spec.summary.initial_capital]);

  const rangeSpan = Math.max(
    smoothedRange.max - smoothedRange.min,
    Math.abs(smoothedRange.max) * 0.02,
    1,
  );
  const yPadding = rangeSpan * 0.12;
  const yMinimum = smoothedRange.min - yPadding;
  const yMaximum = smoothedRange.max + yPadding;

  const {head, span, anchor} = stateAtFrame(frame);
  const cursorX = chart.left + anchor * plotWidth;
  // 事件卡片是时间轴上的“随行注释”：事件临近时淡入，越过播放头后
  // 继续与事件日期一起向左滚动，再在约 4 秒内逐渐淡出。
  const eventLead = Math.max(6, span * 0.05);
  const eventTrail = Math.max(28, span * 0.3);
  const activeEvent =
    eventPoints
      .filter(
        (item) =>
          head >= item.index - eventLead &&
          head <= item.index + eventTrail,
      )
      .sort(
        (a, b) => Math.abs(head - a.index) - Math.abs(head - b.index),
      )[0] ?? null;
  const eventCardOpacity = activeEvent
    ? interpolate(
        head,
        [
          activeEvent.index - eventLead,
          activeEvent.index,
          activeEvent.index + eventTrail * 0.42,
          activeEvent.index + eventTrail,
        ],
        [0, 1, 0.86, 0],
        clamp,
      )
    : 0;

  // 当前播放头所在的配音锚点段：第 i 段区间 = (上一锚点, 锚点 i]，
  // 与第 i 段配音的播放时间自然对齐。
  const activeSegment = useMemo(() => {
    if (!narration || !anchorPoints) return null;
    if (frame / fps < narration.hook_end_s) return null;
    for (const point of anchorPoints) {
      if (head <= point.index + 1e-6) return point;
    }
    return anchorPoints[anchorPoints.length - 1];
  }, [narration, anchorPoints, head, frame, fps]);

  // 段切换时字幕 6 帧淡入；情绪光晕做克制的慢脉冲。
  const subtitleOpacity = activeSegment
    ? interpolate(
        frame,
        [activeSegment.startT * fps, activeSegment.startT * fps + 6],
        [0, 1],
        clamp,
      )
    : 0;
  const glowPulse = 0.5 + 0.3 * Math.sin(frame / 8);
  const dxPerIndex = plotWidth / Math.max(1, span);
  const xAt = (index: number): number => cursorX - (head - index) * dxPerIndex;
  const eventCardWidth = landscape ? 570 : 620;
  const eventCardGap = landscape ? 28 : 24;
  const eventCardLeft = activeEvent
    ? xAt(activeEvent.index) - eventCardWidth - eventCardGap
    : 0;
  const yAt = (value: number): number =>
    chart.bottom -
    ((value - yMinimum) / Math.max(1, yMaximum - yMinimum)) * (chart.bottom - chart.top);
  const baselineY = yAt(spec.summary.initial_capital);

  const leftmostIndex = head - anchor * span;

  // 游标（播放头）的插值数值，与旧组件一致按段插值。
  const lowerIndex = Math.min(pointCount - 1, Math.floor(head));
  const upperIndex = Math.min(pointCount - 1, Math.ceil(head));
  const segmentProgress = head - lowerIndex;
  const current = {
    value: interpolate(
      segmentProgress,
      [0, 1],
      [series[lowerIndex].value, series[upperIndex].value],
    ),
    return_pct: interpolate(
      segmentProgress,
      [0, 1],
      [series[lowerIndex].return_pct, series[upperIndex].return_pct],
    ),
    date: series[Math.round(head)].date,
  };
  const displayCurrency =
    spec.title.includes("人民币") || spec.title.includes("元")
      ? "CNY"
      : spec.summary.currency;
  const displayInstrumentName = storyNarrative && spec.story_hook?.display_asset_name
    ? spec.story_hook.display_asset_name
    : storyNarrative && spec.instrument.symbol === "BTC-USD"
      ? "比特币"
      : storyNarrative && spec.instrument.symbol === "ETH-USD"
        ? "以太坊"
        : spec.instrument.name;
  const holdingYears = Math.max(
    1,
    Number(series[series.length - 1].date.slice(0, 4)) -
      Number(spec.compliance.actual_buy_date.slice(0, 4)),
  );
  const hookNameLines = useMemo(
    () => splitCompanyName(displayInstrumentName),
    [displayInstrumentName],
  );
  const hookNameFontSize = hookNameLines.length > 1
    ? 92
    : displayInstrumentName.length >= 8
      ? 94
      : 108;
  const hookCapital = spec.summary.initial_capital >= 10_000
    ? `${Number((spec.summary.initial_capital / 10_000).toFixed(1))}万`
    : spec.summary.initial_capital.toLocaleString("zh-CN", {maximumFractionDigits: 0});
  const storyHookText = spec.story_hook?.text ??
    `你兄弟在${spec.compliance.actual_buy_date.slice(0, 4)}年拿${hookCapital}买了${displayInstrumentName}`;
  const storyHookFontSize = Math.max(
    29,
    Math.min(42, 1280 / Math.max(1, visualTextUnits(storyHookText))),
  );
  const wideInstrumentMetric =
    (openingHook || storyNarrative) && displayInstrumentName.length > 8;
  const cumulativeProfit =
    spec.summary.final_value - spec.summary.initial_capital;
  const dotX = cursorX;
  const dotY = interpolate(
    segmentProgress,
    [0, 1],
    [yAt(series[lowerIndex].value), yAt(series[upperIndex].value)],
  );

  const positive = current.return_pct >= 0;
  const accent = positive
    ? spec.chart.line_color_positive
    : spec.chart.line_color_negative;
  const currentLabelWidth = landscape ? 342 : 320;
  const currentLabelHeight = landscape ? 66 : 72;
  const currentLabelLeft = Math.max(
    chart.left,
    Math.min(chart.right - currentLabelWidth, dotX - currentLabelWidth / 2),
  );
  const preferCurrentLabelAbove =
    dotY - currentLabelHeight - 34 >= chart.top ||
    dotY > chart.bottom - currentLabelHeight - 56;
  const preferredCurrentLabelTop = preferCurrentLabelAbove
    ? dotY - currentLabelHeight - 34
    : dotY + 34;
  const currentLabelTop = Math.max(
    chart.top + 10,
    Math.min(chart.bottom - currentLabelHeight - 18, preferredCurrentLabelTop),
  );
  const currentLabelAbove = currentLabelTop < dotY;
  const currentLabelText = `${dateLabel(current.date)} · ${compactAmountClean(
    current.value,
    displayCurrency,
  )}`;

  // 每帧只构造窗口内可见的 path：左缘以外 2 个点（供曲线平滑过渡）到游标。
  // 超过采样上限（仅结局拉远时）按步长抽稀，端点与游标点始终保留。
  const visiblePoints = useMemo(() => {
    const firstIndex = Math.max(0, Math.floor(leftmostIndex) - 2);
    const lastIndex = Math.min(pointCount - 1, Math.floor(head));
    const count = Math.max(0, lastIndex - firstIndex + 1);
    const stride = Math.max(1, Math.ceil(count / MAX_PATH_SAMPLES));
    const points: {x: number; y: number}[] = [];
    for (let index = firstIndex; index <= lastIndex; index += stride) {
      points.push({x: xAt(index), y: yAt(series[index].value)});
    }
    if (count > 0 && (lastIndex - firstIndex) % stride !== 0) {
      points.push({x: xAt(lastIndex), y: yAt(series[lastIndex].value)});
    }
    points.push({x: dotX, y: dotY});
    return points;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftmostIndex, head, yMinimum, yMaximum, dotX, dotY, series]);

  const path = useMemo(() => {
    const generator = createLine<{x: number; y: number}>()
      .x((point) => point.x)
      .y((point) => point.y)
      .curve(curveMonotoneX);
    return generator(visiblePoints) ?? "";
  }, [visiblePoints]);

  const filledAreaPath = useMemo(() => {
    if (!filledArea || visiblePoints.length < 2) return "";
    const generator = createArea<{x: number; y: number}>()
      .x((point) => point.x)
      .y0(baselineY)
      .y1((point) => point.y)
      .curve(curveMonotoneX);
    return generator(visiblePoints) ?? "";
  }, [baselineY, filledArea, visiblePoints]);

  // X 轴滚动刻度：窗口范围内的日期刻度，月/季/年粒度自适应（3-5 个为宜）。
  const ticks = useMemo(() => {
    const startIndex = Math.max(0, Math.ceil(leftmostIndex));
    const endIndex = Math.min(pointCount - 1, Math.floor(head));
    if (endIndex - startIndex < 5) return [] as {index: number; label: string}[];
    const collect = (granularity: TickGranularity) => {
      const result: {index: number; label: string}[] = [];
      for (let index = Math.max(1, startIndex); index <= endIndex; index += 1) {
        if (
          periodKey(series[index].date, granularity) !==
          periodKey(series[index - 1].date, granularity)
        ) {
          result.push({index, label: tickLabel(series[index].date, granularity)});
        }
      }
      return result;
    };
    const enforceSpacing = (candidates: {index: number; label: string}[]) => {
      if (!filledArea) return candidates;
      const result: {index: number; label: string}[] = [];
      let previousX = Number.NEGATIVE_INFINITY;
      for (const candidate of candidates) {
        const x = cursorX - (head - candidate.index) * dxPerIndex;
        if (x - previousX >= MIN_TICK_GAP_PX) {
          result.push(candidate);
          previousX = x;
        }
      }
      return result;
    };
    for (const granularity of ["month", "quarter", "year"] as TickGranularity[]) {
      const candidates = enforceSpacing(collect(granularity));
      if (candidates.length > 0 && candidates.length <= 5) return candidates;
    }
    const yearly = enforceSpacing(collect("year"));
    if (yearly.length <= 1) return yearly;
    const step = Math.ceil(yearly.length / 5);
    return yearly.filter((_, position) => position % step === 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftmostIndex, head, series, filledArea, cursorX, dxPerIndex]);

  const zoomOutNow = interpolate(
    frame,
    [outroStart, outroStart + Math.max(1, Math.round((durationInFrames - 1 - outroStart) * 0.75))],
    [0, 1],
    clamp,
  );
  const isOutro = zoomOutNow > 0.5;
  const finaleProgress = interpolate(
    frame,
    [outroStart, outroStart + Math.max(1, Math.round(fps * 0.48))],
    [0, 1],
    {...clamp, easing: Easing.out(Easing.cubic)},
  );
  const baseInterfaceOpacity = 1 - finaleProgress;
  const hookOpacity = openingHook
    ? interpolate(
        frame,
        [0, Math.min(12, introFrames / 3), Math.max(18, introFrames - 14), introFrames + 18],
        [0, 1, 1, 0],
        {...clamp, easing: Easing.inOut(Easing.cubic)},
      )
    : 0;
  const hookScale = interpolate(
    frame,
    [0, Math.max(1, introFrames)],
    [0.985, 1],
    {...clamp, easing: Easing.out(Easing.cubic)},
  );
  // 结尾全景只保留完整走势和最终收益，历史事件层在进入 outro 前快速淡出。
  const historicalEventVisibility = interpolate(
    frame,
    [Math.max(0, outroStart - 8), outroStart],
    [1, 0],
    clamp,
  );
  const showHistoricalEvents = historicalEventVisibility > 0;

  return (
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(circle at 50% 42%, #172a38 0%, #0b1118 46%, #070b10 100%)",
        color: "#f4f7fa",
        fontFamily:
          '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif',
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          opacity: 0.23,
          backgroundImage:
            "linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
        }}
      />

      {/* 配音情绪光晕：crash 红 / surge 绿 / recovery 蓝，克制的慢脉冲 */}
      {activeSegment && activeSegment.emphasis !== "sideways" ? (
        <div
          style={{
            position: "absolute",
            left: chart.left - 60,
            top: chart.top - 80,
            width: plotWidth + 120,
            height: chart.bottom - chart.top + 160,
            background: `radial-gradient(ellipse at 68% 50%, rgba(${EMPHASIS_RGB[activeSegment.emphasis]},.18) 0%, rgba(${EMPHASIS_RGB[activeSegment.emphasis]},.06) 45%, transparent 75%)`,
            opacity: glowPulse * subtitleOpacity,
            pointerEvents: "none",
          }}
        />
      ) : null}

      {landscape ? (
        <>
          <header
            style={{
              position: "absolute",
              left: 45,
              right: storyNarrative ? 45 : undefined,
              top: storyNarrative ? 20 : 34,
              height: storyNarrative ? 96 : undefined,
              display: storyNarrative ? "flex" : undefined,
              alignItems: storyNarrative ? "center" : undefined,
              justifyContent: storyNarrative ? "center" : undefined,
              color: storyNarrative ? "#f7f9fb" : "#8fa3b5",
              fontSize: storyNarrative ? storyHookFontSize : 22,
              fontWeight: storyNarrative ? 850 : undefined,
              letterSpacing: storyNarrative ? 0.4 : 1.2,
              background: storyNarrative
                ? "linear-gradient(90deg, rgba(18,42,52,.92), rgba(15,24,33,.96), rgba(49,30,20,.9))"
                : undefined,
              border: storyNarrative
                ? "1px solid rgba(255,209,102,.38)"
                : undefined,
              borderRadius: storyNarrative ? 18 : undefined,
              boxShadow: storyNarrative
                ? "0 12px 34px rgba(0,0,0,.34), inset 0 0 28px rgba(255,209,102,.06)"
                : undefined,
              opacity: introOpacity * baseInterfaceOpacity,
              zIndex: 3,
            }}
          >
            {storyNarrative ? (
              <>
                <div
                  style={{
                    maxWidth: 1280,
                    textAlign: "center",
                    whiteSpace: "nowrap",
                  }}
                >
                  {storyHookText}
                </div>
                <div
                  style={{
                    position: "absolute",
                    right: 28,
                    top: 13,
                    width: 340,
                    textAlign: "right",
                  }}
                >
                  <div
                    style={{
                      color: "#97a9b8",
                      fontSize: 18,
                      fontWeight: 500,
                      lineHeight: 1,
                    }}
                  >
                    当前累计收益
                  </div>
                  <div
                    style={{
                      marginTop: 10,
                      color: accent,
                      fontSize: 48,
                      fontWeight: 850,
                      lineHeight: 1,
                      whiteSpace: "nowrap",
                      fontVariantNumeric: "tabular-nums",
                      textShadow: `0 0 24px ${accent}42`,
                    }}
                  >
                    {current.return_pct >= 0 ? "+" : ""}
                    {current.return_pct.toFixed(2)}%
                  </div>
                </div>
              </>
            ) : (
              <>历史持有回测&nbsp; / &nbsp;{spec.instrument.symbol}</>
            )}
          </header>

          {!storyNarrative ? <section
            style={{
              position: "absolute",
              left: 45,
              right: 45,
              top: storyNarrative ? 108 : 88,
              height: storyNarrative ? 125 : 145,
              display: "grid",
              gridTemplateColumns: wideInstrumentMetric
                ? "250px 240px 500px 280px 1fr"
                : "250px 240px 360px 315px 1fr",
              alignItems: "start",
              borderBottom: "1px solid rgba(110, 185, 205, .68)",
              opacity: introOpacity * baseInterfaceOpacity,
              zIndex: 3,
            }}
          >
            {[
              {
                label: "买入",
                value: dateLabel(spec.compliance.actual_buy_date),
              },
              {
                label: "本金",
                value: compactAmountClean(
                  spec.summary.initial_capital,
                  displayCurrency,
                ),
              },
              {
                label: storyNarrative ? "持有标的" : "持有股票",
                value: displayInstrumentName,
              },
              {
                label: "当前资产",
                value: compactAmountClean(current.value, displayCurrency),
              },
            ].map((metric, index) => (
              <div
                key={metric.label}
                style={{
                  minHeight: 92,
                  paddingLeft: index === 0 ? 0 : 68,
                  borderLeft:
                    index === 0
                      ? "none"
                      : "1px solid rgba(137, 160, 178, .48)",
                }}
              >
                <div
                  style={{
                    color: "#8799aa",
                    fontSize: 21,
                    lineHeight: 1.2,
                  }}
                >
                  {metric.label}
                </div>
                <div
                  style={{
                    marginTop: 18,
                    color: "#f4f7fa",
                    fontSize:
                      index === 2 && wideInstrumentMetric ? 28 : 33,
                    fontWeight: 700,
                    lineHeight:
                      index === 2 && wideInstrumentMetric ? 1.05 : 1,
                    whiteSpace:
                      index === 2 && wideInstrumentMetric ? "normal" : "nowrap",
                    overflowWrap:
                      index === 2 && wideInstrumentMetric ? "anywhere" : "normal",
                    paddingRight: index === 2 ? 8 : 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {index === 2 && wideInstrumentMetric
                    ? hookNameLines.map((line) => <div key={line}>{line}</div>)
                    : metric.value}
                </div>
              </div>
            ))}

            <div
              style={{
                minHeight: 92,
                paddingLeft: 76,
                borderLeft: "1px solid rgba(137, 160, 178, .48)",
                textAlign: "right",
              }}
            >
              <div
                style={{
                  color: "#8799aa",
                  fontSize: 21,
                  lineHeight: 1.2,
                }}
              >
                当前累计收益
              </div>
              <div
                style={{
                  marginTop: 12,
                  color: accent,
                  fontSize: 56,
                  fontWeight: 800,
                  lineHeight: 1,
                  whiteSpace: "nowrap",
                  fontVariantNumeric: "tabular-nums",
                  textShadow: `0 0 26px ${accent}42`,
                }}
              >
                {current.return_pct >= 0 ? "+" : ""}
                {current.return_pct.toFixed(2)}%
              </div>
            </div>
          </section> : null}
        </>
      ) : (
        <>
          <header
            style={{
              position: "absolute",
              left: layout.sidePad,
              right: layout.sidePad,
              top: layout.headerTop,
              opacity: introOpacity,
            }}
          >
            <div
              style={{
                fontSize: layout.kickerSize,
                color: "#8fa3b5",
                letterSpacing: 2,
              }}
            >
              历史持有回测
            </div>
            <h1
              style={{
                margin: layout.titleMargin,
                fontSize: layout.titleSize,
                lineHeight: 1.18,
                letterSpacing: -1.8,
              }}
            >
              {spec.title}
            </h1>
            <div style={{fontSize: layout.subtitleSize, color: "#c0ccd6"}}>
              {spec.subtitle}
            </div>
          </header>

          <section
            style={{
              position: "absolute",
              top: layout.sectionTop,
              left: layout.sidePad,
              right: layout.sidePad,
              display: "flex",
              alignItems: layout.sectionAlign,
              justifyContent: "space-between",
              opacity: introOpacity,
            }}
          >
            <div>
              <div style={{fontSize: layout.instrumentSize, fontWeight: 700}}>
                {displayInstrumentName}
              </div>
              <div
                style={{
                  fontSize: layout.symbolSize,
                  color: "#8496a6",
                  marginTop: 6,
                }}
              >
                {spec.instrument.symbol}
              </div>
            </div>
            <div style={{textAlign: "right"}}>
              <div
                style={{
                  fontSize: layout.returnLabelSize,
                  color: "#8496a6",
                }}
              >
                {isOutro ? "最终累计收益" : "当前累计收益"}
              </div>
              <div
                style={{
                  color: accent,
                  fontSize: layout.returnSize,
                  fontWeight: 800,
                  fontVariantNumeric: "tabular-nums",
                  textShadow: `0 0 ${24 + 20 * finalEmphasis}px ${accent}45`,
                }}
              >
                {current.return_pct >= 0 ? "+" : ""}
                {current.return_pct.toFixed(2)}%
              </div>
            </div>
          </section>
        </>
      )}

      <svg
        width={spec.composition.width}
        height={spec.composition.height}
        viewBox={`0 0 ${spec.composition.width} ${spec.composition.height}`}
        style={{position: "absolute", inset: 0}}
      >
        <defs>
          <filter id="line-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="10" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <clipPath id="chart-window">
            <rect
              x={chart.left}
              y={chart.top - 40}
              width={plotWidth}
              height={chart.bottom - chart.top + 80}
            />
          </clipPath>
          <linearGradient
            id="scroll-profit-fill"
            x1="0"
            x2="0"
            y1={chart.top}
            y2={baselineY}
            gradientUnits="userSpaceOnUse"
          >
            <stop offset="0%" stopColor={spec.chart.line_color_positive} stopOpacity={0.62} />
            <stop offset="100%" stopColor={spec.chart.line_color_positive} stopOpacity={0.12} />
          </linearGradient>
          <linearGradient
            id="scroll-loss-fill"
            x1="0"
            x2="0"
            y1={baselineY}
            y2={chart.bottom}
            gradientUnits="userSpaceOnUse"
          >
            <stop offset="0%" stopColor={spec.chart.line_color_negative} stopOpacity={0.12} />
            <stop offset="100%" stopColor={spec.chart.line_color_negative} stopOpacity={0.62} />
          </linearGradient>
          <clipPath id="scroll-profit-zone">
            <rect
              x={chart.left}
              y={chart.top - 40}
              width={plotWidth}
              height={Math.max(1, baselineY - chart.top + 40)}
            />
          </clipPath>
          <clipPath id="scroll-loss-zone">
            <rect
              x={chart.left}
              y={baselineY}
              width={plotWidth}
              height={Math.max(1, chart.bottom - baselineY + 40)}
            />
          </clipPath>
        </defs>

        {spec.chart.show_grid
          ? Array.from({length: 5}, (_, index) => {
              const ratio = index / 4;
              const y = chart.top + ratio * (chart.bottom - chart.top);
              const value = yMaximum - ratio * (yMaximum - yMinimum);
              const label = compactAmount(value, displayCurrency);
              const labelWidth = label.length * 13 + 18;
              return (
                <g key={index}>
                  <line
                    x1={chart.left}
                    x2={chart.right}
                    y1={y}
                    y2={y}
                    stroke="rgba(181,198,211,.14)"
                    strokeWidth={1.5}
                  />
                  {/* 半透明深色底，避免标签被曲线压住 */}
                  <rect
                    x={chart.left - 2}
                    y={y - 38}
                    width={labelWidth}
                    height={32}
                    rx={7}
                    fill="rgba(7,11,16,.78)"
                  />
                  <text
                    x={chart.left + 7}
                    y={y - 13}
                    fill="#8ba0b1"
                    fontSize={22}
                    fontFamily="Arial, sans-serif"
                  >
                    {label}
                  </text>
                </g>
              );
            })
          : null}

        {filledArea ? (
          <g clipPath="url(#chart-window)">
            <path
              d={filledAreaPath}
              fill="url(#scroll-profit-fill)"
              clipPath="url(#scroll-profit-zone)"
            />
            <path
              d={filledAreaPath}
              fill="url(#scroll-loss-fill)"
              clipPath="url(#scroll-loss-zone)"
            />
            <line
              x1={chart.left}
              x2={chart.right}
              y1={baselineY}
              y2={baselineY}
              stroke="rgba(235,241,246,.52)"
              strokeWidth={2}
              strokeDasharray="10 10"
            />
            <path
              d={path}
              fill="none"
              stroke={spec.chart.line_color_positive}
              strokeWidth={14}
              opacity={0.22}
              filter={spec.chart.show_glow ? "url(#line-glow)" : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
              clipPath="url(#scroll-profit-zone)"
            />
            <path
              d={path}
              fill="none"
              stroke={spec.chart.line_color_negative}
              strokeWidth={14}
              opacity={0.22}
              filter={spec.chart.show_glow ? "url(#line-glow)" : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
              clipPath="url(#scroll-loss-zone)"
            />
            <path
              d={path}
              fill="none"
              stroke={spec.chart.line_color_positive}
              strokeWidth={6}
              strokeLinecap="round"
              strokeLinejoin="round"
              clipPath="url(#scroll-profit-zone)"
            />
            <path
              d={path}
              fill="none"
              stroke={spec.chart.line_color_negative}
              strokeWidth={6}
              strokeLinecap="round"
              strokeLinejoin="round"
              clipPath="url(#scroll-loss-zone)"
            />
            <text
              x={chart.right - 12}
              y={baselineY - 12}
              fill="rgba(235,241,246,.78)"
              fontSize={20}
              fontWeight={700}
              textAnchor="end"
            >
              本金线
            </text>
          </g>
        ) : (
          <g clipPath="url(#chart-window)">
            <path
              d={path}
              fill="none"
              stroke={accent}
              strokeWidth={14}
              opacity={0.22}
              filter={spec.chart.show_glow ? "url(#line-glow)" : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d={path}
              fill="none"
              stroke={accent}
              strokeWidth={6}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </g>
        )}

        {spec.chart.show_current_dot ? (
          <g>
            <circle cx={dotX} cy={dotY} r={27} fill={accent} opacity={0.13} />
            <circle
              cx={dotX}
              cy={dotY}
              r={12 + 4 * finalEmphasis}
              fill="#ffffff"
              stroke={accent}
              strokeWidth={6}
            />
          </g>
        ) : null}

        {filledArea && spec.chart.show_current_dot ? (
          <g opacity={introOpacity * baseInterfaceOpacity}>
            <line
              x1={dotX}
              x2={dotX}
              y1={currentLabelAbove ? currentLabelTop + currentLabelHeight : dotY + 18}
              y2={currentLabelAbove ? dotY - 18 : currentLabelTop}
              stroke={accent}
              strokeWidth={2}
              strokeDasharray="5 6"
              opacity={0.78}
            />
            <rect
              x={currentLabelLeft}
              y={currentLabelTop}
              width={currentLabelWidth}
              height={currentLabelHeight}
              rx={14}
              fill="rgba(7,14,20,.94)"
              stroke={accent}
              strokeWidth={2}
            />
            <text
              x={currentLabelLeft + currentLabelWidth / 2}
              y={currentLabelTop + currentLabelHeight / 2 + 9}
              fill="#eef6f8"
              fontSize={landscape ? 25 : 23}
              fontWeight={800}
              textAnchor="middle"
              fontFamily='"Microsoft YaHei", "PingFang SC", Arial, sans-serif'
            >
              {currentLabelText}
            </text>
          </g>
        ) : null}

        {showHistoricalEvents
          ? eventPoints.map(({event, index}) => {
              const x = xAt(index);
              if (x < chart.left || x > chart.right) return null;
              const color = EVENT_COLORS[event.tone];
              const reached = head >= index;
              return (
                <g
                  key={`${event.effective_trading_date}-${event.title}`}
                  opacity={historicalEventVisibility}
                >
                  <line
                    x1={x}
                    x2={x}
                    y1={chart.top}
                    y2={chart.bottom}
                    stroke={color}
                    strokeWidth={2}
                    strokeDasharray="8 10"
                    opacity={reached ? 0.72 : 0.34}
                  />
                  <circle
                    cx={x}
                    cy={chart.top + 18}
                    r={18}
                    fill="#0b1118"
                    stroke={color}
                    strokeWidth={3}
                  />
                  <text
                    x={x}
                    y={chart.top + 25}
                    fill={color}
                    fontSize={19}
                    fontWeight={800}
                    textAnchor="middle"
                    fontFamily="Arial, sans-serif"
                  >
                    E
                  </text>
                </g>
              );
            })
          : null}

        {spec.chart.show_date
          ? ticks.map((tick) => {
              const x = xAt(tick.index);
              if (x < chart.left - 10 || x > chart.right + 10) return null;
              const opacity =
                interpolate(x, [chart.left, chart.left + TICK_FADE_PX], [0, 1], clamp) *
                interpolate(x, [chart.right - TICK_FADE_PX, chart.right], [1, 0], clamp);
              return (
                <g key={tick.index} opacity={opacity}>
                  <line
                    x1={x}
                    x2={x}
                    y1={chart.bottom}
                    y2={chart.bottom + 12}
                    stroke="rgba(181,198,211,.3)"
                    strokeWidth={1.5}
                  />
                  <text
                    x={x}
                    y={chart.bottom + 48}
                    fill="#718494"
                    fontSize={22}
                    textAnchor="middle"
                    fontFamily="Arial, sans-serif"
                  >
                    {tick.label}
                  </text>
                </g>
              );
            })
          : null}
      </svg>

      {landscape ? (
        <div
          style={{
            position: "absolute",
            right: 80,
            top: chart.bottom + 66,
            color: "#7f92a3",
            fontSize: 20,
            fontVariantNumeric: "tabular-nums",
            opacity: introOpacity * baseInterfaceOpacity,
            zIndex: 2,
          }}
        >
          截至 {dateLabel(current.date)}
        </div>
      ) : null}

      {activeEvent && showHistoricalEvents ? (
        <aside
          style={{
            position: "absolute",
            left: eventCardLeft,
            top: landscape ? chart.top + 34 : chart.top + 38,
            width: eventCardWidth,
            borderRadius: 22,
            padding: landscape ? "22px 26px" : "26px 30px",
            background:
              "linear-gradient(145deg, rgba(10,18,25,.96), rgba(18,31,42,.94))",
            border: `2px solid ${EVENT_COLORS[activeEvent.event.tone]}b8`,
            boxShadow: `0 20px 60px rgba(0,0,0,.48), 0 0 32px ${EVENT_COLORS[activeEvent.event.tone]}25`,
            opacity: eventCardOpacity * historicalEventVisibility,
            transform: `translateY(${(1 - eventCardOpacity) * 14}px)`,
            pointerEvents: "none",
            willChange: "left, opacity, transform",
            zIndex: 4,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 18,
              color: EVENT_COLORS[activeEvent.event.tone],
              fontSize: landscape ? 17 : 20,
              fontWeight: 800,
              letterSpacing: 0.8,
            }}
          >
            <span>{activeEvent.event.event_type}</span>
            <span style={{fontVariantNumeric: "tabular-nums"}}>
              {dateLabel(activeEvent.event.event_date)}
            </span>
          </div>
          <div
            style={{
              marginTop: 10,
              fontSize: landscape ? 30 : 34,
              fontWeight: 800,
              lineHeight: 1.25,
            }}
          >
            {activeEvent.event.title}
          </div>
          <div
            style={{
              marginTop: 10,
              color: "#c5d0d9",
              fontSize: landscape ? 20 : 23,
              lineHeight: 1.5,
            }}
          >
            {activeEvent.event.summary}
          </div>
          <div
            style={{
              marginTop: 14,
              display: "flex",
              justifyContent: "space-between",
              gap: 18,
              color: "#8195a5",
              fontSize: landscape ? 15 : 18,
            }}
          >
            <span>来源：{activeEvent.event.source_label}</span>
            {activeEvent.event.impact_label ? (
              <span style={{color: EVENT_COLORS[activeEvent.event.tone]}}>
                {activeEvent.event.impact_label}
              </span>
            ) : null}
          </div>
        </aside>
      ) : null}

      {openingHook && landscape && hookOpacity > 0 ? (
        <section
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 30,
            opacity: hookOpacity,
            transform: `scale(${hookScale})`,
            transformOrigin: "50% 50%",
            background:
              "radial-gradient(circle at 18% 58%, rgba(28,208,153,.14), transparent 36%), radial-gradient(circle at 82% 58%, rgba(255,77,79,.13), transparent 36%), #060c12",
            overflow: "hidden",
            pointerEvents: "none",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              opacity: 0.34,
              backgroundImage:
                "linear-gradient(rgba(112,151,174,.09) 1px, transparent 1px), linear-gradient(90deg, rgba(112,151,174,.09) 1px, transparent 1px)",
              backgroundSize: "120px 120px",
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 42,
              left: 0,
              right: 0,
              textAlign: "center",
              color: "#c4ced6",
              fontSize: 30,
              letterSpacing: 3,
              fontWeight: 500,
            }}
          >
            {dateLabel(spec.compliance.actual_buy_date)} 买入 · {spec.instrument.symbol}
          </div>
          <div
            style={{
              position: "absolute",
              top: 92,
              left: 120,
              right: 120,
              bottom: 260,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              textShadow: "0 10px 28px rgba(0,0,0,.6)",
            }}
          >
            <div
              style={{
                color: "#f7f9fb",
                fontSize: hookNameLines.length > 1 ? 100 : 92,
                lineHeight: 1,
                fontWeight: 900,
                letterSpacing: -2,
              }}
            >
              {hookCapital}持有
            </div>
            <div
              style={{
                marginTop: 20,
                maxWidth: 1500,
                color: "#f7f9fb",
                fontSize: hookNameFontSize,
                lineHeight: 1.03,
                fontWeight: 900,
                letterSpacing: hookNameLines.length > 1 ? 2 : 4,
              }}
            >
              {hookNameLines.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </div>
            <div
              style={{
                marginTop: 17,
                color: "#f7f9fb",
                fontSize: hookNameLines.length > 1 ? 82 : 76,
                lineHeight: 1,
                fontWeight: 900,
                letterSpacing: 3,
              }}
            >
              整整{holdingYears}年
            </div>
            <div
              style={{
                marginTop: 34,
                display: "flex",
                alignItems: "baseline",
                justifyContent: "center",
                gap: 18,
                color: "#f7f9fb",
                fontSize: hookNameLines.length > 1 ? 82 : 76,
                lineHeight: 1,
                fontWeight: 900,
                letterSpacing: -1,
                whiteSpace: "nowrap",
              }}
            >
              <span>到底</span>
              <span style={{color: spec.chart.line_color_positive}}>赚了</span>
              <span>，还是</span>
              <span style={{color: spec.chart.line_color_negative}}>亏了</span>
              <span>？</span>
            </div>
          </div>
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: 785,
              height: 2,
              background: "rgba(229,238,244,.72)",
              boxShadow: "0 0 18px rgba(255,255,255,.32)",
            }}
          />
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: 771,
              width: 30,
              height: 30,
              marginLeft: -15,
              borderRadius: "50%",
              background: "#fff",
              boxShadow: "0 0 28px rgba(255,255,255,.9)",
            }}
          />
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: 838,
              textAlign: "center",
              color: "#d7dfe5",
              fontSize: 28,
              letterSpacing: 14,
            }}
          >
            答案随时间展开
          </div>
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              bottom: 70,
              textAlign: "center",
              color: "#8393a0",
              fontSize: 19,
              letterSpacing: 4,
            }}
          >
            历史数据模拟，仅供信息展示，不构成投资建议
          </div>
        </section>
      ) : null}

      {!landscape ? (
        <section
          style={{
            position: "absolute",
            left: layout.sidePad,
            right: layout.sidePad,
            top: layout.cardTop,
            borderRadius: layout.cardRadius,
            padding: layout.cardPadding,
            background: "rgba(14, 24, 33, .86)",
            border: "1px solid rgba(151,175,194,.16)",
            boxShadow: `0 22px 70px rgba(0,0,0,.35), 0 0 ${44 * finalEmphasis}px ${accent}20`,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 18,
          }}
        >
          <div>
            <div style={{fontSize: layout.cardLabelSize, color: "#8194a4"}}>
              {isOutro ? "最终资产" : "当前资产"}
            </div>
            <div
              style={{
                fontSize: layout.cardValueSize,
                fontWeight: 800,
                marginTop: 7,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {compactAmount(current.value, spec.summary.currency)}
            </div>
          </div>
          <div style={{textAlign: "right"}}>
            <div style={{fontSize: layout.cardLabelSize, color: "#8194a4"}}>
              回测日期
            </div>
            <div
              style={{
                fontSize: layout.cardDateSize,
                fontWeight: 700,
                marginTop: 14,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {dateLabel(current.date)}
            </div>
          </div>
        </section>
      ) : null}

      <footer
        style={{
          position: "absolute",
          left: layout.sidePad,
          right: layout.sidePad,
          bottom: layout.footerBottom,
          color: "#687b8a",
          fontSize: layout.footerSize,
          lineHeight: 1.55,
        }}
      >
        {landscape ? (
          <div>
            {spec.source_label} · {spec.compliance.exchange} ·{" "}
            {spec.compliance.currency} · 抓取{" "}
            {spec.compliance.fetched_at.slice(0, 10)} · {spec.calculation_label} ·
            实际买入 {spec.compliance.actual_buy_date} ·{" "}
            {spec.compliance.execution_price} 成交{" "}
            {spec.compliance.buy_price.toLocaleString("zh-CN")} ·{" "}
            {spec.compliance.share_mode} ·{" "}
            <span style={{color: "#8799a7"}}>{spec.disclaimer}</span>
          </div>
        ) : (
          <>
            <div>
              {spec.source_label} · {spec.compliance.exchange} ·{" "}
              {spec.compliance.currency} · 抓取{" "}
              {spec.compliance.fetched_at.slice(0, 10)}
            </div>
            <div>{spec.calculation_label}</div>
            <div>
              实际买入 {spec.compliance.actual_buy_date} ·{" "}
              {spec.compliance.execution_price} 成交{" "}
              {spec.compliance.buy_price.toLocaleString("zh-CN")} ·{" "}
              {spec.compliance.share_mode}
            </div>
            <div style={{color: "#8799a7", marginTop: 7}}>{spec.disclaimer}</div>
          </>
        )}
      </footer>

      {/* 底部字幕条：当前播放头所在锚点段的 subtitle（黑底胶囊 + emphasis 描边） */}
      {activeSegment ? (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: layout.captionBottom,
            display: "flex",
            justifyContent: "center",
            opacity:
              subtitleOpacity * (landscape ? baseInterfaceOpacity : 1),
            pointerEvents: "none",
          }}
        >
          <div
            style={{
              background: "rgba(5,9,13,.88)",
              border: `2px solid ${EMPHASIS_HEX[activeSegment.emphasis]}`,
              borderRadius: 999,
              padding: "13px 36px",
              fontSize: layout.captionSize,
              fontWeight: 700,
              letterSpacing: 0.5,
              fontVariantNumeric: "tabular-nums",
              boxShadow: "0 12px 36px rgba(0,0,0,.5)",
              whiteSpace: "nowrap",
            }}
          >
            {activeSegment.subtitle}
          </div>
        </div>
      ) : null}

      {landscape && finaleProgress > 0 ? (
        <>
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "rgba(3, 8, 13, .78)",
              opacity: finaleProgress,
              pointerEvents: "none",
              zIndex: 8,
            }}
          />
          <section
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: storyNarrative ? "center" : undefined,
              paddingTop: storyNarrative ? 0 : 132,
              opacity: finaleProgress,
              transform: `translateY(${(1 - finaleProgress) * 20}px) scale(${
                0.965 + finaleProgress * 0.035
              })`,
              transformOrigin: "50% 50%",
              pointerEvents: "none",
              zIndex: 9,
            }}
          >
            {storyNarrative ? (
              <>
                <div
                  style={{
                    color: "#bac9d6",
                    fontSize: 28,
                    letterSpacing: 1.4,
                  }}
                >
                  {holdingYears}年后，他终于打开了这个账户
                </div>
                <div
                  style={{
                    marginTop: 26,
                    color: "#f4f7fa",
                    fontSize: 64,
                    fontWeight: 850,
                    letterSpacing: 1,
                    lineHeight: 1,
                    textShadow: "0 10px 34px rgba(0,0,0,.55)",
                  }}
                >
                  {hookCapital}，最后变成
                </div>
                <div
                  style={{
                    marginTop: 34,
                    color: "#ffd166",
                    fontSize: 154,
                    fontWeight: 900,
                    lineHeight: 0.95,
                    whiteSpace: "nowrap",
                    fontVariantNumeric: "tabular-nums",
                    textShadow:
                      "0 8px 0 rgba(135,72,0,.65), 0 0 45px rgba(255,177,42,.45)",
                  }}
                >
                  {compactAmountClean(spec.summary.final_value, displayCurrency)}
                </div>
                <div
                  style={{
                    marginTop: 42,
                    color: spec.chart.line_color_positive,
                    fontSize: 76,
                    fontWeight: 850,
                    lineHeight: 1,
                    fontVariantNumeric: "tabular-nums",
                    textShadow: `0 0 34px ${spec.chart.line_color_positive}48`,
                  }}
                >
                  累计收益 +{Math.round(spec.summary.return_pct).toLocaleString("zh-CN")}%
                </div>
              </>
            ) : (
              <>
            <div
              style={{
                color: "#bac9d6",
                fontSize: 24,
                letterSpacing: 1.2,
              }}
            >
              {spec.instrument.symbol} · 历史持有回测
            </div>
            <div
              style={{
                marginTop: 22,
                color: "#e8f0f7",
                fontSize: 68,
                fontWeight: 800,
                letterSpacing: 2,
                lineHeight: 1,
                textShadow: "0 10px 34px rgba(0,0,0,.5)",
              }}
            >
              {holdingYears}年持有结果
            </div>

            <div
              style={{
                width: 1450,
                marginTop: 92,
                display: "grid",
                gridTemplateColumns: "1fr 1.28fr 1.16fr",
                alignItems: "stretch",
              }}
            >
              {[
                {
                  label: "本金",
                  value: compactAmountClean(
                    spec.summary.initial_capital,
                    displayCurrency,
                  ),
                  color: "#f4f7fa",
                  size: 72,
                },
                {
                  label: "累计收益率",
                  value: `${
                    spec.summary.return_pct >= 0 ? "+" : ""
                  }${spec.summary.return_pct.toFixed(2)}%`,
                  color:
                    spec.summary.return_pct >= 0
                      ? spec.chart.line_color_positive
                      : spec.chart.line_color_negative,
                  size: 82,
                },
                {
                  label: "累计收益",
                  value: compactAmountClean(
                    cumulativeProfit,
                    displayCurrency,
                  ),
                  color:
                    cumulativeProfit >= 0
                      ? spec.chart.line_color_positive
                      : spec.chart.line_color_negative,
                  size: 78,
                },
              ].map((metric, index) => (
                <div
                  key={metric.label}
                  style={{
                    minHeight: 185,
                    padding: "0 36px",
                    borderLeft:
                      index === 0
                        ? "none"
                        : "1px solid rgba(126, 159, 179, .5)",
                    textAlign: "center",
                  }}
                >
                  <div
                    style={{
                      color: "#d4dee6",
                      fontSize: 28,
                      fontWeight: 700,
                      lineHeight: 1.2,
                    }}
                  >
                    {metric.label}
                  </div>
                  <div
                    style={{
                      marginTop: 32,
                      color: metric.color,
                      fontSize: metric.size,
                      fontWeight: 800,
                      lineHeight: 1,
                      whiteSpace: "nowrap",
                      fontVariantNumeric: "tabular-nums",
                      textShadow:
                        index === 0
                          ? "0 8px 30px rgba(0,0,0,.45)"
                          : `0 0 32px ${metric.color}38`,
                    }}
                  >
                    {metric.value}
                  </div>
                </div>
              ))}
            </div>

            <div
              style={{
                marginTop: 50,
                color: "#b7c5d1",
                fontSize: 25,
                letterSpacing: 0.6,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {displayInstrumentName} ·{" "}
              {dateLabel(spec.compliance.actual_buy_date)} —{" "}
              {dateLabel(series[series.length - 1].date)}
            </div>
              </>
            )}
          </section>
        </>
      ) : null}

      {/* 配音混流：hook 从第 0 帧，各段按时间线排布（段起点 = 锚点到达帧 − 段时长 + 0.3s 修正，由生成侧计算） */}
      {narration?.audio
        .filter((clip) => !storyNarrative || clip.role === "segment")
        .map((clip) => {
        const from = Math.max(0, Math.round(clip.start_s * fps));
        const duration = Math.min(
          durationInFrames - from,
          Math.max(1, Math.ceil((clip.duration_s + narration.gap_s) * fps)),
        );
        if (duration <= 0) return null;
        return (
          <Sequence key={clip.id} from={from} durationInFrames={duration}>
            <Audio src={staticFile(clip.file)} startFrom={0} volume={1} />
          </Sequence>
        );
        })}

      {/* 背景音乐：全程循环，音量低于人声，结尾 fade_out_seconds 线性淡出 */}
      {spec.bgm ? (
        <Sequence from={0} durationInFrames={durationInFrames}>
          <Audio
            src={staticFile(spec.bgm.file)}
            loop
            volume={(frame) => {
              const fadeFrames = Math.max(
                1,
                Math.round(spec.bgm!.fade_out_seconds * fps),
              );
              const remaining = durationInFrames - frame;
              const fade = Math.min(1, Math.max(0, remaining / fadeFrames));
              return spec.bgm!.volume * fade;
            }}
          />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
};

export const ScrollingFilledAreaStockVideo: React.FC<{
  spec: VisualizationSpec;
}> = ({spec}) => <ScrollingStockVideo spec={spec} filledArea openingHook />;

/** 正式故事化入口：取消报幕页，使用持续故事条和结果型结尾。 */
export const StoryNarrativeStockVideo: React.FC<{
  spec: VisualizationSpec;
}> = ({spec}) => (
  <ScrollingStockVideo spec={spec} filledArea storyNarrative />
);

export const StoryNarrativePrototypeVideo = StoryNarrativeStockVideo;
