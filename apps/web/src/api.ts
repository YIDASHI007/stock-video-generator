import type {VisualizationSpec} from "@stock-video/schemas";

export const API_BASE =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.PROD ? "" : "http://127.0.0.1:8877");

export type Instrument = {
  symbol: string;
  name: string;
  market: "CN" | "HK" | "US" | "CRYPTO";
  exchange: string;
  currency: string;
  timezone: string;
  market_lot: number;
  source: string;
};

export type Job = {
  job_id: string;
  job_type: "SIMULATION" | "RENDER" | "TTS";
  stage: string;
  progress: number;
  priority: number;
  created_at: string;
  updated_at: string;
  error_type: string | null;
  error_reason: string | null;
  retry_count: number;
  next_retry_at: string | null;
  input: Record<string, unknown>;
  data_source: string | null;
  output_paths: Record<string, string> | null;
  simulation_id: string | null;
  render_id: string | null;
  cancellation_requested: boolean;
};

export type RetryResponse = {
  accepted: boolean;
  job: Job;
};

export type SimulationDetail = {
  simulation_id: string;
  job_id: string;
  symbol: string;
  name: string | null;
  created_at: string;
  request: Record<string, unknown>;
  summary: {
    actual_buy_date: string;
    buy_price: number;
    initial_shares: number;
    final_shares: number;
    final_cash: number;
    final_value: number;
    total_return_pct: number;
    max_drawdown_pct: number;
    best_value: number;
    worst_value: number;
    dividend_total: number;
    total_fees: number;
  } | null;
  artifacts: Record<string, string> | null;
  instrument?: Instrument;
  source?: {
    provider: string;
    fetched_at: string;
    request_parameters: Record<string, unknown>;
    cache_key: string | null;
    cache_hit: boolean;
    raw_response_summary: Record<string, unknown>;
  };
  validation?: {
    valid: boolean;
    warnings: string[];
    errors: string[];
    data_start: string;
    data_end: string;
    trading_days: number;
  };
  events?: Array<{
    date: string;
    event_type: string;
    description: string;
    amount: number | null;
    source: string | null;
  }>;
  series?: Array<{
    date: string;
    portfolio_value: number;
    total_return_pct: number;
    drawdown_pct: number;
  }>;
};

export type Output = {
  output_id: string;
  render_id: string;
  simulation_id: string;
  created_at: string;
  video_path: string;
  validation_path: string;
  cover_portrait_path: string | null;
  cover_landscape_path: string | null;
};

/* ---------------- 抖音发布 ---------------- */

export type PublishMode = "dry_run" | "immediate" | "scheduled";
export type SocialPlatform = "douyin" | "xiaohongshu" | "wechat_channels";

export type PublishAccount = {
  account_id: string;
  platform: SocialPlatform;
  display_name: string;
  enabled: boolean;
  auto_publish_enabled: boolean;
  auth_status: "unknown" | "logged_in" | "logged_out" | "login_failed";
  last_login_at: string | null;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PublishAttempt = {
  attempt_id: string;
  attempt_no: number;
  started_at: string;
  completed_at: string | null;
  stage: string;
  success: boolean;
  used_agent: boolean;
  error_type: string | null;
  error_reason: string | null;
  screenshot_path: string | null;
  dom_snapshot_path: string | null;
  action_log_path: string | null;
};

export type PublishManifest = {
  publish_id: string;
  output_id: string;
  account_id: string;
  mode: PublishMode;
  scheduled_at: string | null;
  facts: {
    stock_name: string;
    symbol: string;
    market: string;
    buy_date: string;
    end_date: string;
    holding_years: number;
    initial_capital: number;
    final_value: number;
    return_pct: number;
    max_drawdown_pct: number;
    data_source: string;
  };
  media: {
    video_path: string;
    cover_portrait_path: string;
    cover_landscape_path: string;
  };
  content: {
    title_candidates: string[];
    selected_title: string;
    selected_template_id: string;
    description: string;
    topics: string[];
    collection: string | null;
    declaration: string | null;
  };
};

export type PublishJob = {
  publish_id: string;
  output_id: string;
  account_id: string;
  stage: string;
  progress: number;
  mode: PublishMode;
  scheduled_at: string | null;
  approved_at: string | null;
  title: string;
  description: string;
  topics: string[];
  collection: string | null;
  declaration: string | null;
  retry_count: number;
  agent_fallback_count: number;
  error_type: string | null;
  error_reason: string | null;
  published_item_id: string | null;
  published_url: string | null;
  created_at: string;
  updated_at: string;
  manifest?: PublishManifest;
  attempts?: PublishAttempt[];
};

export type PublishLoginStatus = {
  account_id: string;
  platform: SocialPlatform;
  auth_status: PublishAccount["auth_status"];
  status:
    | "idle"
    | "preparing_qr"
    | "waiting_scan"
    | "scanned"
    | "logged_in"
    | "logged_out"
    | "failed"
    | "cancelled";
  message: string;
  qr_code_url?: string;
  qr_revision?: number;
  last_login_at: string | null;
  updated_at: string;
};

export type PublishBatchItem = {
  item_id: string;
  output_id: string;
  publish_id: string;
  position: number;
  status: string;
  started_at: string | null;
  published_at: string | null;
  error_reason: string | null;
  job: PublishJob | null;
};

export type PublishBatch = {
  batch_id: string;
  name: string;
  account_id: string;
  status: string;
  interval_minutes: number;
  random_delay_minutes: number;
  failure_policy: "pause" | "skip";
  start_at: string | null;
  next_run_at: string | null;
  approved_at: string | null;
  pause_requested: boolean;
  error_reason: string | null;
  created_at: string;
  updated_at: string;
  total_count: number;
  counts: Record<string, number>;
  items: PublishBatchItem[];
};

export const publishEvidenceUrl = (
  attemptId: string,
  kind: "screenshot" | "dom" | "actions",
): string => `${API_BASE}/api/publish/attempts/${attemptId}/evidence/${kind}`;

/* ---------------- 自动生产 ---------------- */

export type MarketCode = "CN" | "HK" | "US" | "CRYPTO";

export type TopicDirective = {
  surge_min_pct: number | null;
  crash_max_pct: number | null;
  prefer_angles: string[];
  prefer_symbols: string[];
};

export type TopicPreviewResult = {
  count: number;
  matched: {
    symbol: string;
    name: string;
    market: MarketCode;
    angle: string;
    buy_date: string;
    forward_return_pct: number;
    drama_score: number;
  }[];
  fetch_errors: number;
  excluded_by_market_or_cooldown: number;
};

export type PipelinePolicy = {
  enabled: boolean;
  /** 每日配额；null = 无上限 */
  daily_quota: number | null;
  amount: number;
  markets: MarketCode[];
  angle_weights: Record<"surge" | "crash" | "rollercoaster" | "compound", number>;
  voice: string;
  voiceover_enabled: boolean;
  target_duration: number;
  pool_target: number;
  topic_directive: TopicDirective;
  bgm_file: string | null;
};

export type PipelineStatusResponse = {
  enabled: boolean;
  /** 每日配额；null = 无上限 */
  daily_quota: number | null;
  today_started: number;
  today_completed: number;
  pool_size: number;
  story_pool: {
    total: number;
    ready: number;
    by_status: Record<string, number>;
    ready_by_market: Partial<Record<MarketCode, number>>;
  };
  active_runs: number;
  parked_count: number;
  policy: PipelinePolicy;
};

export type UniverseStatus = {
  active_total: number;
  eligible_total: number;
  excluded_total: number;
  by_market: Partial<
    Record<MarketCode, {active: number; eligible: number}>
  >;
  last_sync: {
    sync_id: string;
    status: "running" | "completed" | "partial" | "failed";
    started_at: string;
    completed_at: string | null;
    errors: {market: MarketCode; reason: string}[];
  } | null;
  sync_in_progress: boolean;
  sync_status?: string;
  added?: number;
  updated?: number;
};

export type PipelineRun = {
  run_id: string;
  topic_id: string;
  status: string;
  current_stage: string;
  simulation_id: string | null;
  render_id: string | null;
  output_id: string | null;
  error: string | null;
  retry_count: number;
  created_at: string;
  updated_at: string;
  topic: {
    symbol: string;
    name: string;
    market: MarketCode;
    buy_date: string;
    amount: number;
    angle: string;
    drama_score: number;
  } | null;
};

export const angleLabels: Record<string, string> = {
  surge: "暴涨神话",
  crash: "暴跌教训",
  rollercoaster: "过山车",
  compound: "长跑赢家",
};

export const pipelineStageLabels: Record<string, string> = {
  TOPIC_QUEUED: "选题",
  SIMULATING: "回测",
  SCRIPTING: "脚本",
  VOICING: "配音",
  RENDERING: "渲染",
  COMPLETED: "已完成",
  FAILED: "失败重试中",
  PARKED: "搁浅",
  SKIPPED: "已跳过",
};

export const PIPELINE_ACTIVE_STATUSES = [
  "TOPIC_QUEUED",
  "SIMULATING",
  "SCRIPTING",
  "VOICING",
  "RENDERING",
];

export const isPipelineActive = (status: string): boolean =>
  PIPELINE_ACTIVE_STATUSES.includes(status);

export const pipelineRunTitle = (run: PipelineRun): string =>
  run.topic ? `${run.topic.name} · ${run.topic.symbol}` : `生产 ${run.run_id.slice(0, 8)}`;

export async function api<T>(
  pathname: string,
  options?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${pathname}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });
  } catch (reason) {
    const detail = reason instanceof Error ? reason.message : String(reason);
    throw new Error(
      `无法连接本机后端服务（${API_BASE}）。请确认服务仍在运行。${detail ? ` ${detail}` : ""}`,
    );
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message =
      payload?.message ?? payload?.detail ?? `请求失败（HTTP ${response.status}）`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.json() as Promise<T>;
}

export const videoUrl = (outputId: string): string =>
  `${API_BASE}/api/outputs/${outputId}/video`;

export const coverUrl = (
  outputId: string,
  variant: "portrait" | "landscape",
): string => `${API_BASE}/api/outputs/${outputId}/cover/${variant}`;

/** 预览选题偏好命中数（不写库）。 */
export const previewTopics = (
  directive: TopicDirective,
  markets?: MarketCode[],
): Promise<TopicPreviewResult> => {
  const search = new URLSearchParams();
  markets?.forEach((market) => search.append("markets", market));
  const suffix = search.size > 0 ? `?${search.toString()}` : "";
  return api<TopicPreviewResult>(`/api/topics/preview-count${suffix}`, {
    method: "POST",
    body: JSON.stringify(directive),
  });
};

/* ---------------- 背景音乐 ---------------- */

export type BgmFileInfo = {file: string; size_bytes: number};

export const listBgm = (): Promise<BgmFileInfo[]> =>
  api<BgmFileInfo[]>("/api/settings/bgm/list");

export const bgmUrl = (file?: string): string =>
  `${API_BASE}/api/settings/bgm${file ? `?file=${encodeURIComponent(file)}` : ""}`;

/** 上传背景音乐（FormData 直传，不能走 api<T> 的 JSON 头）。 */
export async function uploadBgm(file: File): Promise<PipelinePolicy> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE}/api/settings/bgm`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message =
      payload?.detail ?? `上传失败（HTTP ${response.status}）`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.json() as Promise<PipelinePolicy>;
}

export const clearBgm = (): Promise<PipelinePolicy> =>
  api<PipelinePolicy>("/api/settings/bgm", {method: "DELETE"});

/** 删除成片：后端会同时删除数据库记录与磁盘文件。 */
export const deleteOutput = (outputId: string): Promise<{deleted: boolean}> =>
  api<{deleted: boolean}>(`/api/outputs/${outputId}`, {method: "DELETE"});

/* ---------------- 成片库（网格画廊） ---------------- */

/** GET /api/outputs 增强后的单条成片：旧字段之上追加画廊所需信息。 */
export type GalleryOutput = Output & {
  symbol: string | null;
  name: string | null;
  total_return_pct: number | null;
  duration_seconds: number | null;
  angle: string | null;
  market: MarketCode | null;
  publish_stage: string | null;
  published: boolean;
  publish_title: string | null;
  publish_subtitle: string | null;
};

export const thumbnailUrl = (outputId: string): string =>
  `${API_BASE}/api/outputs/${outputId}/thumbnail`;

/** 拼接打包下载地址（直接给 <a download> 触发浏览器下载）。 */
export const packUrl = (params: {
  date?: string;
  market?: string;
  angle?: string;
  pnl?: string;
  q?: string;
}): string => {
  const search = new URLSearchParams();
  if (params.date) search.set("date", params.date);
  if (params.market) search.set("market", params.market);
  if (params.angle) search.set("angle", params.angle);
  if (params.pnl) search.set("pnl", params.pnl);
  if (params.q) search.set("q", params.q);
  const query = search.toString();
  return `${API_BASE}/api/outputs/pack${query ? `?${query}` : ""}`;
};

export type {VisualizationSpec};

/* ---------------- 抖音链接提取 ---------------- */

export type DouyinIntegrationSettings = {
  enabled: boolean;
  base_url: string;
  client_id: string;
  connect_timeout_seconds: number;
  job_timeout_seconds: number;
  api_key_configured: boolean;
  api_key_hint: string | null;
};

export type ExtractorCookieSync = {
  account_id: string;
  status: "synced";
  cookie_count: number;
  ready: boolean;
  missing: string[];
};

export type DouyinRemoteFile = {
  name: string;
  path: string;
  size: number;
  url: string;
};

export type DouyinRemoteJob = {
  id: string;
  job_id?: string;
  url: string;
  status: string;
  stage: string;
  progress: number;
  created_at: string;
  updated_at: string;
  error?: string | null;
  files: DouyinRemoteFile[];
  result?: {
    aweme_id?: string;
    title?: string;
    author?: Record<string, unknown>;
    description?: string;
    detected_language?: string;
    language_probability?: number;
    duration?: number;
    transcript?: string;
    segments?: Array<{start: number; end: number; text: string}>;
  };
};

export type DouyinJobSnapshot = {
  local_job_id: string;
  remote_job_id: string;
  source_url?: string;
  created_at: string;
  imported_at: string | null;
  import_dir: string | null;
  remote: DouyinRemoteJob;
};

export type DouyinImportedAsset = {
  source: "douyin";
  source_url?: string;
  source_id: string;
  title?: string;
  author?: {nickname?: string; unique_id?: string} | null;
  description?: string;
  language?: string;
  duration?: number;
  transcript?: string;
  imported_at: string;
  video_name?: string | null;
};

export type DouyinWork = {
  aweme_id: string;
  url: string;
  title: string;
  description?: string;
  published_at?: string;
  duration?: number;
  cover_url?: string;
  hashtags?: string[];
  statistics?: {play?: number; like?: number; comment?: number; share?: number; collect?: number};
  job_id?: string;
  processing_status?: string;
  transcript?: string;
  transcript_raw?: string;
  transcript_edited?: string;
  transcript_source?: "speech_to_text" | "editor" | string;
  transcript_updated_at?: string;
  transcript_revision?: number;
  transcript_versions?: Array<{text: string; source: string; saved_at: string}>;
  segments?: Array<{start: number; end: number; text: string}>;
  detected_language?: string;
  language_probability?: number;
  processing_error?: string;
};

export type DouyinAccountJob = {
  id: string;
  status: "queued" | "resolving" | "downloading" | "transcribing" | "packaging" | "completed" | "failed" | "cancelled" | "interrupted" | string;
  stage?: string;
  progress?: number;
  error?: string;
  created_at?: string;
  updated_at?: string;
};

export type DouyinMethodology = {
  confidence: string;
  sample_count: number;
  transcript_count: number;
  completion_ratio: number;
  hook_patterns: Array<{name: string; count: number}>;
  narrative_structures: Array<{name: string; count: number}>;
  language_style: {average_sentence_chars: number; estimated_chars_per_second: number; short_line_ratio: number};
  opening_examples: string[];
  ending_examples: string[];
  evidence: Array<{aweme_id: string; title: string; url: string; excerpt: string; like: number}>;
  originality_rules: string[];
};

export type DouyinAccountPortrait = {
  generated_at: string;
  sample_size: number;
  transcribed_count: number;
  positioning: string;
  content_pillars: Array<{name: string; count: number; ratio: number}>;
  top_hashtags: Array<{name: string; count: number}>;
  style_observations: string[];
  metrics: {average_duration: number; question_hook_ratio: number; number_hook_ratio: number};
  representative_works: Array<{aweme_id: string; title: string; url: string; like: number; comment: number}>;
  methodology?: DouyinMethodology;
};

export type DouyinBenchmarkAccount = {
  sec_uid: string;
  uid?: string;
  douyin_id?: string;
  nickname: string;
  signature?: string;
  avatar_url?: string;
  follower_count?: number;
  following_count?: number;
  total_favorited?: number;
  aweme_count?: number;
  source_url?: string;
  works?: DouyinWork[];
  total_stored?: number;
  new_count?: number;
  last_synced_at?: string;
  portrait?: DouyinAccountPortrait;
  skill_export?: {name: string; generated_at: string; confidence?: string; transcript_count?: number};
};

export type DouyinAccountResolveResult = {
  match: "exact" | "candidates";
  warning?: string;
  account?: DouyinBenchmarkAccount;
  candidates?: DouyinBenchmarkAccount[];
};

export const douyinFileUrl = (localJobId: string, remoteUrl: string): string => {
  const token = remoteUrl.split("/").pop() ?? "";
  return `${API_BASE}/api/integrations/douyin/jobs/${localJobId}/files/${token}`;
};

export const douyinImportedVideoUrl = (sourceId: string): string =>
  `${API_BASE}/api/integrations/douyin/imports/${encodeURIComponent(sourceId)}/video`;

export const douyinAccountWorkVideoUrl = (secUid: string, awemeId: string): string =>
  `${API_BASE}/api/integrations/douyin/accounts/${encodeURIComponent(secUid)}/works/${encodeURIComponent(awemeId)}/video`;
