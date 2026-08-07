import type {VisualizationSpec} from "@stock-video/schemas";
import React from "react";
import {AbsoluteFill} from "remotion";

import {formatCoverCapital, formatCoverPeriod} from "./StockCover";

type CoverVariant = "portrait" | "landscape";

type Props = {
  spec: VisualizationSpec;
};

const FONT_FAMILY =
  '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif';

const displayDate = (date: string): string => date.replaceAll("-", ".");

const splitName = (name: string): string[] => {
  const trimmed = name.trim();
  if (!trimmed) return [""];

  if (/[\u3400-\u9fff]/u.test(trimmed)) {
    if (trimmed.length <= 10) return [trimmed];
    for (const suffix of ["股份有限公司", "有限公司", "控股集团", "集团"]) {
      const index = trimmed.indexOf(suffix);
      if (index > 0) return [trimmed.slice(0, index), trimmed.slice(index)];
    }
    const midpoint = Math.ceil(trimmed.length / 2);
    return [trimmed.slice(0, midpoint), trimmed.slice(midpoint)];
  }

  if (trimmed.length <= 28) return [trimmed];
  const words = trimmed.split(/\s+/u);
  if (words.length === 1) {
    const midpoint = Math.ceil(trimmed.length / 2);
    return [trimmed.slice(0, midpoint), trimmed.slice(midpoint)];
  }
  const target = trimmed.length / 2;
  let first = "";
  let second = "";
  for (const word of words) {
    if (!second && (first.length === 0 || `${first} ${word}`.length <= target)) {
      first = first ? `${first} ${word}` : word;
    } else {
      second = second ? `${second} ${word}` : word;
    }
  }
  return second ? [first, second] : [first];
};

const HookStyleCover: React.FC<Props & {variant: CoverVariant}> = ({
  spec,
  variant,
}) => {
  const portrait = variant === "portrait";
  const firstPoint = spec.series[0];
  const lastPoint = spec.series.at(-1) ?? firstPoint;
  const startDate = firstPoint?.date ?? spec.compliance.actual_buy_date;
  const endDate = lastPoint?.date ?? startDate;
  const nameLines = splitName(spec.instrument.name || spec.instrument.symbol);
  const capital = formatCoverCapital(spec.summary.initial_capital);
  const period = formatCoverPeriod(startDate, endDate);
  const nameFontSize = portrait
    ? nameLines.length > 1
      ? 82
      : spec.instrument.name.length >= 9
        ? 104
        : 132
    : nameLines.length > 1
      ? 76
      : spec.instrument.name.length >= 9
        ? 92
        : 116;
  const lineTop = portrait ? 1060 : 790;

  return (
    <AbsoluteFill
      style={{
        background: "#060c12",
        color: "#f7f9fb",
        fontFamily: FONT_FAMILY,
        overflow: "hidden",
      }}
    >
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 17% 58%, rgba(28,208,153,.16), transparent 38%), radial-gradient(circle at 83% 58%, rgba(255,77,79,.15), transparent 38%), #060c12",
        }}
      />
      <AbsoluteFill
        style={{
          opacity: 0.34,
          backgroundImage:
            "linear-gradient(rgba(112,151,174,.09) 1px, transparent 1px), linear-gradient(90deg, rgba(112,151,174,.09) 1px, transparent 1px)",
          backgroundSize: portrait ? "108px 108px" : "90px 90px",
        }}
      />

      <div
        style={{
          position: "absolute",
          top: portrait ? 74 : 54,
          left: portrait ? 70 : 90,
          right: portrait ? 70 : 90,
          textAlign: "center",
          color: "#c4ced6",
          fontSize: portrait ? 30 : 28,
          fontWeight: 500,
          letterSpacing: portrait ? 3 : 2.5,
        }}
      >
        {displayDate(startDate)} 买入 · {spec.instrument.symbol}
      </div>

      <div
        style={{
          position: "absolute",
          top: portrait ? 190 : 126,
          left: portrait ? 62 : 90,
          right: portrait ? 62 : 90,
          bottom: portrait ? 400 : 290,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          textShadow: "0 10px 28px rgba(0,0,0,.62)",
        }}
      >
        <div
          style={{
            fontSize: portrait ? 94 : 86,
            lineHeight: 1,
            fontWeight: 900,
            letterSpacing: -2,
          }}
        >
          {capital}持有
        </div>
        <div
          style={{
            marginTop: portrait ? 30 : 22,
            maxWidth: portrait ? 930 : 1240,
            fontSize: nameFontSize,
            lineHeight: 1.02,
            fontWeight: 900,
            letterSpacing: nameLines.length > 1 ? 1 : 3,
          }}
        >
          {nameLines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
        <div
          style={{
            marginTop: portrait ? 28 : 22,
            fontSize: portrait ? 72 : 64,
            lineHeight: 1,
            fontWeight: 900,
            letterSpacing: 2,
          }}
        >
          整整{period}
        </div>
        <div
          style={{
            marginTop: portrait ? 48 : 36,
            display: "flex",
            alignItems: "baseline",
            justifyContent: "center",
            gap: portrait ? 13 : 16,
            fontSize: portrait ? 59 : 68,
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
          top: lineTop,
          height: 2,
          background: "rgba(229,238,244,.72)",
          boxShadow: "0 0 18px rgba(255,255,255,.32)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: lineTop - (portrait ? 17 : 15),
          width: portrait ? 34 : 30,
          height: portrait ? 34 : 30,
          marginLeft: portrait ? -17 : -15,
          borderRadius: "50%",
          background: "#fff",
          boxShadow: "0 0 28px rgba(255,255,255,.9)",
        }}
      />
      <div
        style={{
          position: "absolute",
          top: lineTop + (portrait ? 62 : 48),
          left: 0,
          right: 0,
          textAlign: "center",
          color: "#d7dfe5",
          fontSize: portrait ? 28 : 25,
          letterSpacing: portrait ? 13 : 12,
        }}
      >
        答案随时间展开
      </div>
      <div
        style={{
          position: "absolute",
          left: portrait ? 50 : 70,
          right: portrait ? 50 : 70,
          bottom: portrait ? 62 : 45,
          textAlign: "center",
          color: "#8393a0",
          fontSize: portrait ? 20 : 18,
          letterSpacing: portrait ? 3 : 2.5,
        }}
      >
        历史数据模拟，仅供信息展示，不构成投资建议
      </div>
    </AbsoluteFill>
  );
};

export const StockHookStylePortraitCover: React.FC<Props> = ({spec}) => (
  <HookStyleCover spec={spec} variant="portrait" />
);

export const StockHookStyleLandscapeCover: React.FC<Props> = ({spec}) => (
  <HookStyleCover spec={spec} variant="landscape" />
);
