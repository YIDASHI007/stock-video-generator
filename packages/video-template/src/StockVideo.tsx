import type {VisualizationSpec} from "@stock-video/schemas";
import {curveMonotoneX, line as createLine} from "d3-shape";
import React, {useMemo} from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type Props = {
  spec: VisualizationSpec;
};

const chart = {
  left: 92,
  right: 988,
  top: 670,
  bottom: 1420,
};

const clamp = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
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

const dateLabel = (isoDate: string): string => {
  const [year, month, day] = isoDate.split("-");
  return `${year}.${month}.${day}`;
};

export const StockVideo: React.FC<Props> = ({spec}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const series = spec.series;
  const introFrames = Math.round(spec.timeline.intro_seconds * fps);
  const chartFrames = Math.round(spec.timeline.chart_seconds * fps);
  const chartProgress = interpolate(
    frame,
    [introFrames, introFrames + chartFrames],
    [0, 1],
    {
      ...clamp,
      easing: Easing.inOut(Easing.cubic),
    },
  );
  const introOpacity = interpolate(frame, [0, Math.round(fps * 0.8)], [0, 1], clamp);
  const outroStart = durationInFrames - Math.round(spec.timeline.outro_seconds * fps);
  const finalEmphasis = interpolate(
    frame,
    [outroStart, outroStart + Math.round(fps * 0.6)],
    [0, 1],
    clamp,
  );

  const values = series.map((point) => point.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const padding = Math.max((maximum - minimum) * 0.12, Math.abs(maximum) * 0.02, 1);
  const yMinimum = minimum - padding;
  const yMaximum = maximum + padding;
  const xAt = (index: number): number =>
    chart.left + (index / Math.max(1, series.length - 1)) * (chart.right - chart.left);
  const yAt = (value: number): number =>
    chart.bottom -
    ((value - yMinimum) / Math.max(1, yMaximum - yMinimum)) * (chart.bottom - chart.top);

  const path = useMemo(() => {
    const generator = createLine<{value: number}>()
      .x((_, index) => xAt(index))
      .y((point) => yAt(point.value))
      .curve(curveMonotoneX);
    return generator(series) ?? "";
  }, [series, yMinimum, yMaximum]);

  const floatingIndex = chartProgress * Math.max(0, series.length - 1);
  const lowerIndex = Math.floor(floatingIndex);
  const upperIndex = Math.min(series.length - 1, Math.ceil(floatingIndex));
  const segmentProgress = floatingIndex - lowerIndex;
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
    date: series[Math.round(floatingIndex)].date,
  };
  const dotX = interpolate(segmentProgress, [0, 1], [xAt(lowerIndex), xAt(upperIndex)]);
  const dotY = interpolate(
    segmentProgress,
    [0, 1],
    [yAt(series[lowerIndex].value), yAt(series[upperIndex].value)],
  );
  const positive = current.return_pct >= 0;
  const accent = positive
    ? spec.chart.line_color_positive
    : spec.chart.line_color_negative;

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

      <header
        style={{
          position: "absolute",
          left: 64,
          right: 64,
          top: 88,
          opacity: introOpacity,
        }}
      >
        <div style={{fontSize: 31, color: "#8fa3b5", letterSpacing: 2}}>
          历史持有回测
        </div>
        <h1
          style={{
            margin: "20px 0 13px",
            fontSize: 57,
            lineHeight: 1.18,
            letterSpacing: -1.8,
          }}
        >
          {spec.title}
        </h1>
        <div style={{fontSize: 31, color: "#c0ccd6"}}>{spec.subtitle}</div>
      </header>

      <section
        style={{
          position: "absolute",
          top: 405,
          left: 64,
          right: 64,
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          opacity: introOpacity,
        }}
      >
        <div>
          <div style={{fontSize: 32, fontWeight: 700}}>{spec.instrument.name}</div>
          <div style={{fontSize: 25, color: "#8496a6", marginTop: 6}}>
            {spec.instrument.symbol}
          </div>
        </div>
        <div style={{textAlign: "right"}}>
          <div style={{fontSize: 25, color: "#8496a6"}}>当前累计收益</div>
          <div
            style={{
              color: accent,
              fontSize: 57,
              fontWeight: 800,
              fontVariantNumeric: "tabular-nums",
              textShadow: `0 0 24px ${accent}45`,
            }}
          >
            {current.return_pct >= 0 ? "+" : ""}
            {current.return_pct.toFixed(2)}%
          </div>
        </div>
      </section>

      <svg
        width={1080}
        height={1920}
        viewBox="0 0 1080 1920"
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
          <clipPath id="line-reveal">
            <rect
              x={chart.left - 20}
              y={chart.top - 30}
              width={(chart.right - chart.left + 40) * chartProgress}
              height={chart.bottom - chart.top + 60}
            />
          </clipPath>
        </defs>

        {Array.from({length: 5}, (_, index) => {
          const ratio = index / 4;
          const y = chart.top + ratio * (chart.bottom - chart.top);
          const value = yMaximum - ratio * (yMaximum - yMinimum);
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
              <text
                x={chart.left}
                y={y - 13}
                fill="#718494"
                fontSize={22}
                fontFamily="Arial, sans-serif"
              >
                {compactAmount(value, spec.summary.currency)}
              </text>
            </g>
          );
        })}

        <g clipPath="url(#line-reveal)">
          <path
            d={path}
            fill="none"
            stroke={accent}
            strokeWidth={14}
            opacity={0.22}
            filter="url(#line-glow)"
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

        {spec.chart.show_current_dot ? (
          <g>
            <circle cx={dotX} cy={dotY} r={27} fill={accent} opacity={0.13} />
            <circle cx={dotX} cy={dotY} r={12} fill="#ffffff" stroke={accent} strokeWidth={6} />
          </g>
        ) : null}

        <text
          x={chart.left}
          y={chart.bottom + 50}
          fill="#718494"
          fontSize={22}
          fontFamily="Arial, sans-serif"
        >
          {dateLabel(series[0].date)}
        </text>
        <text
          x={chart.right}
          y={chart.bottom + 50}
          fill="#718494"
          fontSize={22}
          textAnchor="end"
          fontFamily="Arial, sans-serif"
        >
          {dateLabel(series[series.length - 1].date)}
        </text>
      </svg>

      <section
        style={{
          position: "absolute",
          left: 64,
          right: 64,
          top: 1510,
          borderRadius: 30,
          padding: "31px 36px 33px",
          background: "rgba(14, 24, 33, .86)",
          border: "1px solid rgba(151,175,194,.16)",
          boxShadow: `0 22px 70px rgba(0,0,0,.35), 0 0 ${44 * finalEmphasis}px ${accent}20`,
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 18,
        }}
      >
        <div>
          <div style={{fontSize: 24, color: "#8194a4"}}>当前资产</div>
          <div
            style={{
              fontSize: 53,
              fontWeight: 800,
              marginTop: 7,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {compactAmount(current.value, spec.summary.currency)}
          </div>
        </div>
        <div style={{textAlign: "right"}}>
          <div style={{fontSize: 24, color: "#8194a4"}}>回测日期</div>
          <div
            style={{
              fontSize: 39,
              fontWeight: 700,
              marginTop: 14,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {dateLabel(current.date)}
          </div>
        </div>
      </section>

      <footer
        style={{
          position: "absolute",
          left: 64,
          right: 64,
          bottom: 61,
          color: "#687b8a",
          fontSize: 20,
          lineHeight: 1.55,
        }}
      >
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
      </footer>
    </AbsoluteFill>
  );
};
