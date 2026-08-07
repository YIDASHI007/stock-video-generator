import type {VisualizationSpec} from "@stock-video/schemas";
import React from "react";
import {AbsoluteFill} from "remotion";

type CoverVariant = "portrait" | "landscape";
type Props = {spec: VisualizationSpec};

const FONT_FAMILY =
  '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif';

const compactNumber = (value: number, digits = 0): string =>
  Number(value.toFixed(digits)).toLocaleString("zh-CN", {
    maximumFractionDigits: digits,
  });

const clamp = (value: number, minimum: number, maximum: number): number =>
  Math.min(maximum, Math.max(minimum, value));

const visualUnits = (text: string): number =>
  [...text].reduce((total, character) => {
    if (/\s/u.test(character)) return total + 0.32;
    if (/[\u3400-\u9fff]/u.test(character)) return total + 1;
    if (/[%]/u.test(character)) return total + 0.72;
    if (/[,.:·-]/u.test(character)) return total + 0.32;
    if (/[A-Z0-9]/u.test(character)) return total + 0.62;
    return total + 0.52;
  }, 0);

const stripCompanySuffix = (name: string): string => {
  let result = name.trim().replace(/\s+/gu, " ");
  result = result.replace(
    /(?:股份有限公司|有限责任公司|控股集团有限公司|集团有限公司|有限公司)$/u,
    "",
  );
  const englishSuffix =
    /(?:,?\s+(?:incorporated|inc\.?|corporation|corp\.?|company|co\.?|limited|ltd\.?|holdings?|group|plc))$/iu;
  while (englishSuffix.test(result)) result = result.replace(englishSuffix, "");
  return result.trim().replace(/[,，·\s]+$/u, "");
};

const instrumentLabel = (name: string, symbol: string): string => {
  const shortened = stripCompanySuffix(name);
  if (!shortened || shortened.toUpperCase() === symbol.toUpperCase()) return symbol;
  return visualUnits(shortened) <= 24 ? shortened : symbol;
};

const amountDigits = (scaled: number): number => {
  if (scaled >= 1000) return 0;
  if (scaled >= 100) return 1;
  if (scaled >= 10) return 1;
  return 2;
};

const formatCapital = (value: number): string => {
  const absolute = Math.abs(value);
  const units = [
    {threshold: 1e20, suffix: "垓"},
    {threshold: 1e16, suffix: "京"},
    {threshold: 1e12, suffix: "万亿"},
    {threshold: 1e8, suffix: "亿"},
    {threshold: 1e4, suffix: "万"},
  ];
  const unit = units.find(({threshold}) => absolute >= threshold);
  if (!unit) return compactNumber(value);
  const scaled = value / unit.threshold;
  return `${compactNumber(scaled, amountDigits(Math.abs(scaled)))}${unit.suffix}`;
};

const formatReturn = (value: number): string => {
  const absolute = Math.abs(value);
  const digits = absolute >= 1000 ? 0 : absolute >= 100 ? 1 : 2;
  return `${compactNumber(value, digits)}%`;
};

const downsample = <T,>(items: T[], target: number): T[] => {
  if (items.length <= target) return items;
  return Array.from({length: target}, (_, index) =>
    items[Math.round((index / (target - 1)) * (items.length - 1))],
  );
};

const BackgroundCurve: React.FC<{
  spec: VisualizationSpec;
  portrait: boolean;
}> = ({spec, portrait}) => {
  const width = portrait ? 1080 : 1440;
  const height = portrait ? 1120 : 760;
  const points = downsample(spec.series, 300);
  const values = points.map((point) => Math.log1p(Math.max(0, point.value)));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = Math.max(maximum - minimum, 1);
  const padX = portrait ? 18 : 0;
  const padTop = portrait ? 55 : 26;
  const padBottom = portrait ? 130 : 70;
  const coords = points.map((point, index) => ({
    x:
      padX +
      (index / Math.max(points.length - 1, 1)) * (width - padX * 2),
    y:
      height -
      padBottom -
      ((Math.log1p(Math.max(0, point.value)) - minimum) / span) *
        (height - padTop - padBottom),
  }));
  const path = coords
    .map(({x, y}, index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
  const last = coords.at(-1) ?? {x: width, y: padTop};
  const area = `${path} L${last.x},${height} L${padX},${height} Z`;
  const suffix = portrait ? "p" : "l";

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id={`curve-${suffix}`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#16a894" stopOpacity="0.22" />
          <stop offset="53%" stopColor="#23c7a6" stopOpacity="0.5" />
          <stop offset="76%" stopColor="#ef8c38" stopOpacity="0.65" />
          <stop offset="100%" stopColor="#ff4837" stopOpacity="0.9" />
        </linearGradient>
        <linearGradient id={`area-${suffix}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ee6c39" stopOpacity="0.1" />
          <stop offset="50%" stopColor="#1aa78f" stopOpacity="0.07" />
          <stop offset="100%" stopColor="#0b3b36" stopOpacity="0" />
        </linearGradient>
        <filter id={`glow-${suffix}`} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="7" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <path d={area} fill={`url(#area-${suffix})`} />
      <path
        d={path}
        fill="none"
        stroke={`url(#curve-${suffix})`}
        strokeWidth={portrait ? 4.5 : 3.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        filter={`url(#glow-${suffix})`}
      />
    </svg>
  );
};

const GoldText: React.FC<
  React.PropsWithChildren<{
    style?: React.CSSProperties;
    bright?: boolean;
    loss?: boolean;
  }>
> = ({children, style, bright = false, loss = false}) => (
  <div
    style={{
      color: loss ? "#ff6957" : bright ? "#ffc729" : "#fff2bd",
      backgroundImage: loss
        ? "linear-gradient(180deg, #ffd5ce 0%, #ff7865 42%, #d93732 80%, #ff8b72 100%)"
        : bright
          ? "linear-gradient(180deg, #fff0a0 0%, #ffd33e 39%, #f5a914 78%, #ffe57b 100%)"
          : "linear-gradient(180deg, #ffffff 0%, #fff6ce 47%, #f5c95d 100%)",
      backgroundClip: "text",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
      filter: loss
        ? "drop-shadow(0 6px 0 rgba(94,13,14,.7)) drop-shadow(0 0 17px rgba(255,71,62,.5)) drop-shadow(0 12px 14px rgba(0,0,0,.78))"
        : bright
          ? "drop-shadow(0 6px 0 rgba(133,55,0,.66)) drop-shadow(0 0 16px rgba(255,177,25,.62)) drop-shadow(0 12px 14px rgba(0,0,0,.78))"
          : "drop-shadow(0 7px 2px rgba(0,0,0,.78)) drop-shadow(0 0 12px rgba(255,197,68,.26))",
      ...style,
    }}
  >
    {children}
  </div>
);

const ResultShockCover: React.FC<Props & {variant: CoverVariant}> = ({spec, variant}) => {
  const portrait = variant === "portrait";
  const year = (spec.compliance.actual_buy_date || spec.series[0]?.date || "").slice(0, 4);
  const symbol = spec.instrument.symbol.toUpperCase();
  const capital = formatCapital(spec.summary.initial_capital);
  const finalValue = formatCapital(spec.summary.final_value);
  const returnPct = formatReturn(spec.summary.return_pct);
  const candidateLabel = instrumentLabel(spec.instrument.name, symbol);
  const candidateHeadline = `${year}年拿${capital}买${candidateLabel}`;
  const label =
    visualUnits(candidateHeadline) > (portrait ? 20 : 22)
      ? symbol
      : candidateLabel;
  const headline = `${year}年拿${capital}买${label}`;
  const headlineFontSize = clamp(
    (portrait ? 900 : 1220) / Math.max(visualUnits(headline), 1),
    portrait ? 36 : 42,
    portrait ? 58 : 68,
  );
  const valueFontSize = clamp(
    (portrait ? 890 : 710) / Math.max(visualUnits(finalValue), 1),
    portrait ? 116 : 126,
    portrait ? 178 : 188,
  );
  const percentFontSize = clamp(
    (portrait ? 890 : 1240) / Math.max(visualUnits(returnPct), 1),
    portrait ? 145 : 190,
    portrait ? 260 : 350,
  );
  const isLoss = spec.summary.return_pct < 0;

  return (
    <AbsoluteFill
      style={{
        background: "#02090d",
        color: "#fff",
        fontFamily: FONT_FAMILY,
        overflow: "hidden",
      }}
    >
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 15% 48%, rgba(0,145,139,.22), transparent 35%), radial-gradient(circle at 92% 38%, rgba(155,32,28,.2), transparent 32%), linear-gradient(135deg, #031219 0%, #02070b 53%, #12090a 100%)",
        }}
      />
      <AbsoluteFill
        style={{
          opacity: 0.33,
          backgroundImage:
            "linear-gradient(rgba(89,127,141,.14) 1px, transparent 1px), linear-gradient(90deg, rgba(89,127,141,.14) 1px, transparent 1px)",
          backgroundSize: portrait ? "108px 108px" : "90px 90px",
        }}
      />

      <div
        style={{
          position: "absolute",
          top: portrait ? 230 : 120,
          left: portrait ? -105 : -45,
          width: portrait ? 700 : 500,
          height: portrait ? 700 : 500,
          borderRadius: "50%",
          border: portrait ? "3px solid rgba(28,199,179,.08)" : "2px solid rgba(28,199,179,.08)",
          boxShadow:
            "inset 0 0 0 24px rgba(28,199,179,.018), inset 0 0 0 48px rgba(28,199,179,.018), 0 0 90px rgba(13,174,156,.05)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "rgba(29,193,177,.075)",
          fontSize: portrait ? 210 : 155,
          fontWeight: 950,
          letterSpacing: -12,
        }}
      >
        {symbol}
      </div>

      <div
        style={{
          position: "absolute",
          inset: 0,
          top: portrait ? 150 : 20,
          opacity: portrait ? 0.44 : 0.5,
        }}
      >
        <BackgroundCurve spec={spec} portrait={portrait} />
      </div>

      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: portrait ? -120 : -105,
          height: portrait ? 470 : 360,
          opacity: 0.33,
          transform: "perspective(520px) rotateX(62deg)",
          transformOrigin: "bottom center",
          backgroundImage:
            "linear-gradient(rgba(218,151,41,.23) 1px, transparent 1px), linear-gradient(90deg, rgba(218,151,41,.18) 1px, transparent 1px)",
          backgroundSize: portrait ? "82px 54px" : "90px 48px",
          maskImage: "linear-gradient(to top, black 15%, transparent 92%)",
          WebkitMaskImage: "linear-gradient(to top, black 15%, transparent 92%)",
        }}
      />

      <div
        style={{
          position: "absolute",
          top: portrait ? 175 : 214,
          left: portrait ? 56 : 100,
          right: portrait ? 56 : 100,
          textAlign: "center",
          fontSize: headlineFontSize,
          lineHeight: 1,
          fontWeight: 900,
          letterSpacing: portrait ? -1 : 1,
          whiteSpace: "nowrap",
          textShadow: "0 8px 4px rgba(0,0,0,.8), 0 0 12px rgba(255,255,255,.12)",
        }}
      >
        {headline}
      </div>

      <div
        style={{
          position: "absolute",
          top: portrait ? 350 : 345,
          left: portrait ? 32 : 55,
          right: portrait ? 32 : 55,
          display: "flex",
          flexDirection: portrait ? "column" : "row",
          alignItems: "center",
          justifyContent: "center",
          gap: portrait ? 18 : 17,
          whiteSpace: "nowrap",
        }}
      >
        <GoldText
          style={{
            fontSize: portrait ? 100 : 140,
            lineHeight: 0.96,
            fontWeight: 950,
            letterSpacing: portrait ? -4 : -7,
          }}
        >
          今天变成
        </GoldText>
        <GoldText
          style={{
            fontSize: valueFontSize,
            lineHeight: 0.9,
            fontWeight: 950,
            letterSpacing: portrait ? -8 : -10,
          }}
        >
          {finalValue}
        </GoldText>
      </div>

      <div
        style={{
          position: "absolute",
          top: portrait ? 670 : 523,
          left: portrait ? 58 : 90,
          right: portrait ? 58 : 90,
          height: 2,
          background:
            "linear-gradient(90deg, transparent 0%, rgba(255,176,30,.28) 13%, rgba(255,207,77,.95) 50%, rgba(255,176,30,.28) 87%, transparent 100%)",
          boxShadow: "0 0 20px rgba(255,174,24,.72)",
        }}
      />

      <GoldText
        bright
        loss={isLoss}
        style={{
          position: "absolute",
          top: portrait ? 735 : 565,
          left: portrait ? 22 : 32,
          right: portrait ? 22 : 32,
          textAlign: "center",
          fontSize: percentFontSize,
          lineHeight: 0.94,
          fontWeight: 950,
          letterSpacing: portrait ? -16 : -22,
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
          transform: portrait ? "scaleX(1.06)" : "scaleX(1.12)",
        }}
      >
        {returnPct}
      </GoldText>
    </AbsoluteFill>
  );
};

export const StockResultShockPortraitCover: React.FC<Props> = ({spec}) => (
  <ResultShockCover spec={spec} variant="portrait" />
);

export const StockResultShockLandscapeCover: React.FC<Props> = ({spec}) => (
  <ResultShockCover spec={spec} variant="landscape" />
);
