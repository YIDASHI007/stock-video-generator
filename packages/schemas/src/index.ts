export type VisualizationPoint = {
  date: string;
  value: number;
  return_pct: number;
};

export type Milestone = {
  date: string;
  type: string;
  label: string;
  value: number;
  return_pct: number;
};

export type HistoricalEvent = {
  event_date: string;
  effective_trading_date: string;
  event_type: string;
  title: string;
  summary: string;
  source_label: string;
  source_url: string;
  confidence: "high" | "medium" | "low";
  impact_label?: string | null;
  tone: "positive" | "negative" | "neutral";
};

export type NarrationEmphasis = "surge" | "crash" | "sideways" | "recovery";

export type NarrationSegmentSpec = {
  anchor_date: string;
  subtitle: string;
  emphasis: NarrationEmphasis;
  audio_id: string;
  /** 播放头到达锚点日期的视频时间（秒）。 */
  arrive_s: number;
};

export type NarrationAudioClipSpec = {
  id: string;
  role: "hook" | "segment" | "finale" | "cta";
  /** staticFile() 相对引用：narration/{simulation_id}/{filename} */
  file: string;
  /** 渲染调用方复制源（绝对路径），渲染后清理。 */
  source_path: string;
  start_s: number;
  duration_s: number;
};

export type NarrationSpec = {
  voice_id: string;
  gap_s: number;
  tail_s: number;
  /** hook 音频结束时间：播放头从此时起跑。 */
  hook_end_s: number;
  /** 播放头到达序列末端的时间（= total - tail）。 */
  chart_end_s: number;
  total_duration_s: number;
  segments: NarrationSegmentSpec[];
  audio: NarrationAudioClipSpec[];
};

export type BgmSpec = {
  /** staticFile() 相对引用：bgm/{simulation_id}/{filename} */
  file: string;
  /** 渲染调用方复制源（绝对路径），渲染后清理。 */
  source_path: string;
  volume: number;
  fade_out_seconds: number;
};

export type VisualizationSpec = {
  schema_version: "1.0";
  /** 固定成片结构版本；v1 为横屏滚动走势图与三指标结尾。 */
  template_version: "v1";
  simulation_id: string;
  composition: {
    width: number;
    height: number;
    fps: 30;
    duration_seconds: number;
  };
  title: string;
  subtitle: string;
  instrument: {
    name: string;
    symbol: string;
  };
  summary: {
    initial_capital: number;
    final_value: number;
    return_pct: number;
    currency: string;
  };
  story_hook?: {
    template_id: string;
    category: string;
    text: string;
    display_asset_name: string;
  } | null;
  timeline: {
    intro_seconds: number;
    chart_seconds: number;
    outro_seconds: number;
  };
  chart: {
    type: "portfolio_value_line";
    line_color_positive: string;
    line_color_negative: string;
    background: string;
    show_grid: boolean;
    show_current_dot: boolean;
    show_glow: boolean;
    show_date: boolean;
    show_value: boolean;
    show_return: boolean;
    /** 滚动窗口组件：图表区可见的交易日数量，默认 250。 */
    window_days?: number;
    /** 滚动窗口组件：游标在图表区从左到右的锚定位置比例，默认 0.78。 */
    cursor_anchor?: number;
  };
  milestones: Milestone[];
  /** 经来源核验、最多 5 条的历史事件时间轴。 */
  events?: HistoricalEvent[];
  series: VisualizationPoint[];
  source_label: string;
  calculation_label: string;
  compliance: {
    exchange: string;
    fetched_at: string;
    actual_buy_date: string;
    execution_price: string;
    buy_price: number;
    fees_included: boolean;
    dividend_policy: string;
    share_mode: string;
    currency: string;
  };
  narration?: NarrationSpec | null;
  bgm?: BgmSpec | null;
  disclaimer: string;
};
