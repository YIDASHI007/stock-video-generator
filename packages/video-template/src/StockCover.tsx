import type {VisualizationSpec} from "@stock-video/schemas";
import {curveMonotoneX, line as createLine} from "d3-shape";
import React, {useMemo} from "react";
import {AbsoluteFill} from "remotion";

type Props = {
  spec: VisualizationSpec;
};

type CoverVariant = "portrait" | "landscape";

const FONT_FAMILY =
  '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif';

const trimNumber = (value: number, digits = 1): string =>
  value.toFixed(digits).replace(/\.0+$/, "");

export const formatCoverCapital = (value: number): string => {
  const absolute = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (absolute >= 100_000_000) {
    return `${sign}${trimNumber(absolute / 100_000_000)}亿`;
  }
  if (absolute >= 10_000) {
    return `${sign}${trimNumber(absolute / 10_000)}万`;
  }
  return `${sign}${Math.round(absolute).toLocaleString("zh-CN")}`;
};

export const formatCoverPeriod = (startDate: string, endDate: string): string => {
  const start = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  const days = Math.max(1, (end.getTime() - start.getTime()) / 86_400_000);
  const years = days / 365.2425;
  if (years >= 0.9) {
    const nearestYear = Math.max(1, Math.round(years));
    return `${nearestYear}年`;
  }
  const months = Math.max(1, Math.round(days / 30.4375));
  return `${months}个月`;
};

const createChartPath = (
  spec: VisualizationSpec,
  width: number,
  height: number,
  paddingX: number,
  paddingY: number,
): string => {
  const values = spec.series.map((point) => point.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = Math.max(1, maximum - minimum);
  const points = spec.series.map((point, index) => ({
    x:
      paddingX +
      (index / Math.max(1, spec.series.length - 1)) * (width - paddingX * 2),
    y:
      paddingY +
      ((maximum - point.value) / spread) * (height - paddingY * 2),
  }));
  const generator = createLine<{x: number; y: number}>()
    .x((point) => point.x)
    .y((point) => point.y)
    .curve(curveMonotoneX);
  return generator(points) ?? "";
};

const ChartBackdrop: React.FC<{
  spec: VisualizationSpec;
  variant: CoverVariant;
  startYear: string;
  endYear: string;
}> = ({spec, variant, startYear, endYear}) => {
  const width = variant === "portrait" ? 1080 : 1120;
  const height = variant === "portrait" ? 470 : 720;
  const path = useMemo(
    () => createChartPath(spec, width, height, 22, 42),
    [height, spec, width],
  );
  const firstYear = Number(startYear);
  const lastYear = Number(endYear);
  const yearSpan =
    Number.isFinite(firstYear) && Number.isFinite(lastYear)
      ? Math.max(0, lastYear - firstYear)
      : 0;
  const tickCount = yearSpan === 0 ? 1 : Math.min(5, yearSpan + 1);
  const ticks = Array.from(
    {length: tickCount},
    (_, index) => (tickCount === 1 ? 0.5 : index / (tickCount - 1)),
  );

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      style={{display: "block", width: "100%", height: "100%"}}
    >
      <defs>
        <filter id={`cover-glow-${variant}`} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation={variant === "portrait" ? 9 : 12} />
        </filter>
        <linearGradient id={`cover-stroke-${variant}`} x1="0" x2="1">
          <stop offset="0%" stopColor="#35b9f3" stopOpacity="0.55" />
          <stop offset="72%" stopColor="#56d7ff" stopOpacity="1" />
          <stop offset="100%" stopColor="#2f83b8" stopOpacity="0.25" />
        </linearGradient>
      </defs>
      {ticks.map((tick) => {
        const x = 22 + tick * (width - 44);
        const year = Number.isFinite(firstYear) && Number.isFinite(lastYear)
          ? Math.round(firstYear + tick * (lastYear - firstYear))
          : "";
        return (
          <g key={tick}>
            <line
              x1={x}
              x2={x}
              y1={0}
              y2={height - 32}
              stroke="#17354a"
              strokeWidth={1}
              opacity={0.42}
            />
            <text
              x={x}
              y={height - 5}
              textAnchor={tick === 0 ? "start" : tick === 1 ? "end" : "middle"}
              fill="#416178"
              fontFamily={FONT_FAMILY}
              fontSize={variant === "portrait" ? 22 : 24}
            >
              {year}
            </text>
          </g>
        );
      })}
      {[0.2, 0.5, 0.8].map((tick) => (
        <line
          key={tick}
          x1={0}
          x2={width}
          y1={tick * (height - 36)}
          y2={tick * (height - 36)}
          stroke="#17354a"
          strokeWidth={1}
          opacity={0.38}
        />
      ))}
      <path
        d={path}
        fill="none"
        stroke="#25bdf4"
        strokeWidth={14}
        opacity={0.18}
        filter={`url(#cover-glow-${variant})`}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={path}
        fill="none"
        stroke={`url(#cover-stroke-${variant})`}
        strokeWidth={variant === "portrait" ? 4 : 5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

const StockCover: React.FC<Props & {variant: CoverVariant}> = ({
  spec,
  variant,
}) => {
  const firstPoint = spec.series[0];
  const lastPoint = spec.series.at(-1) ?? firstPoint;
  const startDate = firstPoint?.date ?? spec.compliance.actual_buy_date;
  const endDate = lastPoint?.date ?? startDate;
  const startYear = startDate.slice(0, 4);
  const endYear = endDate.slice(0, 4);
  const period = formatCoverPeriod(startDate, endDate);
  const capital = formatCoverCapital(spec.summary.initial_capital);
  const name = spec.instrument.name || spec.instrument.symbol;
  const symbol = spec.instrument.symbol;
  const longName = name.length >= 7;
  const veryLongName = name.length >= 10;
  const isPortrait = variant === "portrait";

  if (isPortrait) {
    return (
      <AbsoluteFill
        style={{
          background: "#020912",
          color: "#f5f8fb",
          fontFamily: FONT_FAMILY,
          overflow: "hidden",
        }}
      >
        <AbsoluteFill
          style={{
            background:
              "radial-gradient(circle at 52% 36%, rgba(18,46,67,0.2), transparent 42%), linear-gradient(180deg, #020812 0%, #030b15 72%, #020811 100%)",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: "78px 86px auto",
            textAlign: "center",
            zIndex: 2,
          }}
        >
          <div
            style={{
              color: "#8da1b0",
              fontSize: 31,
              fontWeight: 500,
              letterSpacing: 4,
              marginBottom: 62,
            }}
          >
            历史真实回测 · {symbol}
          </div>
          <div
            style={{
              color: "#f4c95d",
              fontSize: 88,
              fontWeight: 800,
              letterSpacing: -3,
              lineHeight: 1,
              marginBottom: 24,
            }}
          >
            {capital}买入
          </div>
          <div
            style={{
              fontSize: veryLongName ? 92 : longName ? 128 : 178,
              fontWeight: 900,
              letterSpacing: veryLongName ? -4 : longName ? -7 : -12,
              lineHeight: veryLongName ? 0.98 : 1.05,
              whiteSpace: veryLongName ? "normal" : "nowrap",
              textWrap: "balance",
              marginBottom: 34,
              textShadow: "0 10px 32px rgba(0,0,0,0.28)",
            }}
          >
            {name}
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "center",
              gap: 24,
              fontSize: 82,
              fontWeight: 800,
              lineHeight: 1.1,
              marginBottom: 20,
            }}
          >
            <span>持有</span>
            <span style={{color: "#42c7f4", fontSize: 106}}>{period}</span>
          </div>
          <div
            style={{
              fontSize: 88,
              fontWeight: 900,
              letterSpacing: -5,
              lineHeight: 1.08,
            }}
          >
            现在值多少？
          </div>
        </div>
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 86,
            height: 448,
            opacity: 0.92,
          }}
        >
          <ChartBackdrop
            spec={spec}
            variant="portrait"
            startYear={startYear}
            endYear={endYear}
          />
        </div>
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 34,
            textAlign: "center",
            color: "#718899",
            fontSize: 27,
            letterSpacing: 3,
          }}
        >
          回测区间 <span style={{color: "#43bde9"}}>{startYear}—{endYear}</span>
        </div>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill
      style={{
        background: "#020912",
        color: "#f5f8fb",
        fontFamily: FONT_FAMILY,
        overflow: "hidden",
      }}
    >
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 77% 50%, rgba(15,55,81,0.23), transparent 42%), linear-gradient(105deg, #020812 0%, #030b15 58%, #020811 100%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 72,
          top: 60,
          width: 760,
          zIndex: 3,
        }}
      >
        <div
          style={{
            color: "#8da1b0",
            fontSize: 25,
            fontWeight: 500,
            letterSpacing: 4,
            marginBottom: 40,
          }}
        >
          历史真实回测 · {symbol}
        </div>
        <div
          style={{
            color: "#f4c95d",
            fontSize: 64,
            fontWeight: 800,
            letterSpacing: -3,
            lineHeight: 1,
            marginBottom: 18,
          }}
        >
          {capital}买入
        </div>
        <div
          style={{
            fontSize: veryLongName ? 68 : longName ? 92 : 128,
            fontWeight: 900,
            letterSpacing: veryLongName ? -3 : longName ? -6 : -10,
            lineHeight: veryLongName ? 0.98 : 1.05,
            whiteSpace: veryLongName ? "normal" : "nowrap",
            textWrap: "balance",
            marginBottom: 36,
          }}
        >
          {name}
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 22,
            fontSize: 58,
            fontWeight: 800,
            lineHeight: 1.1,
            marginBottom: 20,
          }}
        >
          <span>持有</span>
          <span style={{color: "#42c7f4", fontSize: 80}}>{period}</span>
        </div>
        <div
          style={{
            fontSize: 68,
            fontWeight: 900,
            letterSpacing: -5,
            lineHeight: 1.08,
          }}
        >
          现在值多少？
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          right: 24,
          top: 270,
          width: 780,
          height: 690,
          opacity: 0.82,
        }}
      >
        <ChartBackdrop
          spec={spec}
          variant="landscape"
          startYear={startYear}
          endYear={endYear}
        />
      </div>
      <div
        style={{
          position: "absolute",
          right: 68,
          bottom: 48,
          color: "#718899",
          fontSize: 25,
          letterSpacing: 3,
        }}
      >
        回测区间 <span style={{color: "#43bde9"}}>{startYear}—{endYear}</span>
      </div>
    </AbsoluteFill>
  );
};

export const StockPortraitCover: React.FC<Props> = ({spec}) => (
  <StockCover spec={spec} variant="portrait" />
);

export const StockLandscapeCover: React.FC<Props> = ({spec}) => (
  <StockCover spec={spec} variant="landscape" />
);
