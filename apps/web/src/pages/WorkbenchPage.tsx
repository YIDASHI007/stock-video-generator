import React, {useCallback, useEffect, useMemo, useState} from "react";
import {Link} from "react-router-dom";

import {
  api,
  bgmUrl,
  isPipelineActive,
  listBgm,
  pipelineRunTitle,
  pipelineStageLabels,
  previewTopics,
  uploadBgm,
  type BgmFileInfo,
  type Job,
  type MarketCode,
  type PipelinePolicy,
  type PipelineRun,
  type PipelineStatusResponse,
  type TopicPreviewResult,
  type UniverseStatus,
} from "../api";
import {
  ChevronIcon,
  EmptyState,
  ErrorNotice,
  WarnIcon,
  elapsedText,
  isActiveStage,
  isAttentionStage,
  jobStageLabel,
  jobTitle,
  parseServerDate,
} from "../components";
import {usePolling} from "../hooks";

const VISIBLE_ACTIVE = 8;

const MARKET_OPTIONS: Array<{
  value: MarketCode;
  label: string;
  description: string;
}> = [
  {value: "CN", label: "A股", description: "沪深市场"},
  {value: "HK", label: "港股", description: "香港市场"},
  {value: "US", label: "美股", description: "美国市场"},
  {value: "CRYPTO", label: "加密资产", description: "主流高流动性币种"},
];

/* ---------------- 涨跌幅档位 ---------------- */

type RangePreset = {
  value: string;
  label: string;
  sub: string;
  surge: number | null;
  crash: number | null;
};

const SURGE_PRESETS: RangePreset[] = [
  {value: "surge100x", label: "涨 100 倍", sub: "100万→1亿", surge: 9900, crash: null},
  {value: "surge10x", label: "涨 10 倍", sub: "100万→1000万", surge: 900, crash: null},
  {value: "surge3x", label: "涨 3 倍", sub: "100万→300万", surge: 200, crash: null},
  {value: "surge1x", label: "涨 1 倍", sub: "翻倍", surge: 100, crash: null},
  {value: "surge50", label: "涨 50%", sub: "", surge: 50, crash: null},
];

const CRASH_PRESETS: RangePreset[] = [
  {value: "crash50", label: "跌 50%", sub: "腰斩", surge: null, crash: -50},
  {value: "crash80", label: "跌 80%", sub: "100万→20万", surge: null, crash: -80},
  {value: "crash90", label: "跌 90%", sub: "100万→10万", surge: null, crash: -90},
  {value: "crash99", label: "跌 99%", sub: "100万→1万", surge: null, crash: -99},
];

const ALL_PRESETS = [...SURGE_PRESETS, ...CRASH_PRESETS];

/** 从策略里的阈值反推当前档位；对不上任何档位返回 custom。 */
const detectPreset = (policy: PipelinePolicy): string => {
  const {surge_min_pct, crash_max_pct} = policy.topic_directive;
  if (surge_min_pct === null && crash_max_pct === null) return "any";
  const hit = ALL_PRESETS.find(
    (preset) =>
      preset.surge === surge_min_pct && preset.crash === crash_max_pct,
  );
  return hit ? hit.value : "custom";
};

const formatBytes = (bytes: number): string => {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
};

/* ---------------- 正在生产卡片 ---------------- */

const ProducingCard: React.FC<{job: Job}> = ({job}) => {
  const pct = Math.round(Math.min(1, Math.max(0, job.progress)) * 100);
  return (
    <article className="prod-card">
      <div className="prod-card-head">
        <strong>{jobTitle(job)}</strong>
        <span className="prod-stage">{jobStageLabel(job)}</span>
      </div>
      <div className="progress-track tall">
        <span style={{width: `${pct}%`}} />
      </div>
      <div className="prod-card-foot">
        <span className="num">{pct}%</span>
        <span className="num muted">已用时 {elapsedText(job.created_at)}</span>
      </div>
    </article>
  );
};

const PipelineProducingCard: React.FC<{run: PipelineRun}> = ({run}) => (
  <article className="prod-card pipeline">
    <div className="prod-card-head">
      <strong>{pipelineRunTitle(run)}</strong>
      <span className="prod-stage">
        {pipelineStageLabels[run.current_stage] ?? run.current_stage}
      </span>
    </div>
    <div className="progress-track tall indeterminate">
      <span />
    </div>
    <div className="prod-card-foot">
      <span className="num">自动生产</span>
      <span className="num muted">已用时 {elapsedText(run.created_at)}</span>
    </div>
  </article>
);

/* ---------------- 工作台 ---------------- */

export const WorkbenchPage: React.FC = () => {
  const statusLoader = useCallback(
    () => api<PipelineStatusResponse>("/api/pipeline/status"),
    [],
  );
  const jobsLoader = useCallback(() => api<Job[]>("/api/jobs"), []);
  const runsLoader = useCallback(
    () => api<PipelineRun[]>("/api/pipeline/runs?filter=active"),
    [],
  );
  const bgmLoader = useCallback(() => listBgm(), []);
  const universeLoader = useCallback(
    () => api<UniverseStatus>("/api/universe/status"),
    [],
  );
  const {
    data: status,
    error: statusError,
    refresh: refreshStatus,
  } = usePolling(statusLoader, 3000);
  const {data: jobs, error: jobsError} = usePolling(jobsLoader, 3000);
  const {data: runs} = usePolling(runsLoader, 3000);
  const {data: bgmFiles, refresh: refreshBgm} = usePolling(bgmLoader, 30_000);
  const {
    data: universe,
    error: universeError,
    refresh: refreshUniverse,
  } = usePolling(universeLoader, 30_000);

  const [draft, setDraft] = useState<PipelinePolicy | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const [bgmBusy, setBgmBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [syncingUniverse, setSyncingUniverse] = useState(false);
  const [preview, setPreview] = useState<TopicPreviewResult | null>(null);
  const [showAllActive, setShowAllActive] = useState(false);

  useEffect(() => {
    if (status && !draft) setDraft(status.policy);
  }, [status, draft]);

  const update = (patch: Partial<PipelinePolicy>) =>
    setDraft((current) => (current ? {...current, ...patch} : current));

  const save = async (
    override?: Partial<PipelinePolicy>,
  ): Promise<PipelinePolicy | null> => {
    const base = draft ? {...draft, ...override} : null;
    if (!base || saving) return null;
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      const saved = await api<PipelinePolicy>("/api/pipeline/policy", {
        method: "PUT",
        body: JSON.stringify(base),
      });
      setDraft(saved);
      setNotice("设置已保存，自动生产立即按新设置执行。");
      refreshStatus();
      return saved;
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setSaving(false);
    }
  };

  const runOnce = async () => {
    if (autoBusy) return;
    setAutoBusy(true);
    setFormError(null);
    try {
      if (dirty) {
        const saved = await save();
        if (!saved) return;
      }
      await api<PipelineRun>("/api/pipeline/run-once", {method: "POST"});
      refreshStatus();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAutoBusy(false);
    }
  };

  const onBgmSelected = async (file: File | null) => {
    if (!file || bgmBusy) return;
    setBgmBusy(true);
    setFormError(null);
    setNotice(null);
    try {
      const policy = await uploadBgm(file);
      // 上传接口已在服务端把该文件设为当前选用，直接同步草稿。
      setDraft(policy);
      setNotice(`背景音乐已上传并选用：${policy.bgm_file}`);
      refreshBgm();
      refreshStatus();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBgmBusy(false);
    }
  };

  const runPreview = async () => {
    if (!draft || previewing) return;
    setPreviewing(true);
    setFormError(null);
    try {
      setPreview(await previewTopics(draft.topic_directive, draft.markets));
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPreviewing(false);
    }
  };

  const syncUniverse = async () => {
    if (syncingUniverse) return;
    setSyncingUniverse(true);
    setFormError(null);
    setNotice(null);
    try {
      const result = await api<UniverseStatus>("/api/universe/sync", {
        method: "POST",
      });
      setNotice(
        `股票库同步完成：当前可生产 ${result.eligible_total.toLocaleString()} 只，新增 ${(result.added ?? 0).toLocaleString()} 只。`,
      );
      refreshUniverse();
      refreshStatus();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSyncingUniverse(false);
    }
  };

  const activeRuns = useMemo(
    () => (runs ?? []).filter((run) => isPipelineActive(run.status)),
    [runs],
  );
  const pipelineSimulationIds = useMemo(
    () =>
      new Set(
        activeRuns
          .map((run) => run.simulation_id)
          .filter((id): id is string => Boolean(id)),
      ),
    [activeRuns],
  );
  const pipelineRenderIds = useMemo(
    () =>
      new Set(
        activeRuns
          .map((run) => run.render_id)
          .filter((id): id is string => Boolean(id)),
      ),
    [activeRuns],
  );
  const activeJobs = useMemo(
    () =>
      (jobs ?? [])
        .filter((job) => isActiveStage(job.stage))
        .filter(
          (job) =>
            !(
              (job.simulation_id &&
                pipelineSimulationIds.has(job.simulation_id)) ||
              (job.render_id && pipelineRenderIds.has(job.render_id))
            ),
        )
        .sort(
          (a, b) =>
            parseServerDate(b.updated_at).getTime() -
            parseServerDate(a.updated_at).getTime(),
        ),
    [jobs, pipelineRenderIds, pipelineSimulationIds],
  );
  const attentionCount = useMemo(
    () =>
      (jobs ?? []).filter((job) => isAttentionStage(job.stage)).length +
      (status?.parked_count ?? 0),
    [jobs, status],
  );
  const totalActive = activeJobs.length + activeRuns.length;
  const visibleActive = showAllActive
    ? activeJobs
    : activeJobs.slice(0, Math.max(0, VISIBLE_ACTIVE - activeRuns.length));
  const hiddenActive = totalActive - activeRuns.length - visibleActive.length;

  const preset = draft ? detectPreset(draft) : "any";
  const bgmChoices: BgmFileInfo[] = bgmFiles ?? [];
  const enabled = status?.enabled ?? false;
  const quotaPct =
    status && status.daily_quota
      ? Math.min(
          100,
          Math.round((status.today_completed / status.daily_quota) * 100),
        )
      : 0;
  const dirty =
    draft && status
      ? JSON.stringify(draft) !== JSON.stringify(status.policy)
      : false;

  const applyPreset = (choice: RangePreset | null) => {
    if (!draft) return;
    setPreview(null);
    update({
      topic_directive: {
        ...draft.topic_directive,
        surge_min_pct: choice?.surge ?? null,
        crash_max_pct: choice?.crash ?? null,
      },
    });
  };

  const toggleMarket = (market: MarketCode) => {
    if (!draft) return;
    const selected = draft.markets.includes(market);
    if (selected && draft.markets.length === 1) {
      setFormError("至少保留一个股票市场。");
      return;
    }
    setFormError(null);
    setPreview(null);
    update({
      markets: selected
        ? draft.markets.filter((item) => item !== market)
        : MARKET_OPTIONS.map((item) => item.value).filter(
            (item) => item === market || draft.markets.includes(item),
          ),
    });
  };

  const presetChip = (choice: RangePreset, tone: "up" | "down") => (
    <button
      key={choice.value}
      type="button"
      className={`wb-preset ${tone} ${preset === choice.value ? "active" : ""}`}
      onClick={() => applyPreset(preset === choice.value ? null : choice)}
      title={choice.sub ? `${choice.label}（${choice.sub}）` : choice.label}
    >
      <strong>{choice.label}</strong>
      {choice.sub ? <small>{choice.sub}</small> : null}
    </button>
  );

  return (
    <div className="page workbench-page">
      {statusError ? <ErrorNotice message={statusError} /> : null}
      {jobsError ? <ErrorNotice message={jobsError} /> : null}
      {universeError ? <ErrorNotice message={universeError} /> : null}
      {formError ? <ErrorNotice message={formError} /> : null}
      {notice ? <div className="notice ok-notice">{notice}</div> : null}

      {/* 1. Hero 控制台 */}
      <section className={`wb-hero ${enabled ? "live" : ""}`}>
        <div className="wb-hero-status">
          <span className={`wb-live-dot ${enabled ? "on" : ""}`} />
          <div className="wb-hero-text">
            <strong>{enabled ? "自动生产运行中" : "自动生产已暂停"}</strong>
            <span className="muted">
              今日 {status?.today_completed ?? "—"}
              {status?.daily_quota ? `/${status.daily_quota}` : ""} 条
              {status && !status.daily_quota ? " · 不限量" : ""}
              {" · 选题池 "}
              {status?.pool_size ?? "—"} 条待产
              {status && status.parked_count > 0
                ? ` · ${status.parked_count} 条搁浅`
                : ""}
            </span>
          </div>
        </div>
        <div className="wb-hero-quota">
          <div className="wb-hero-quota-bar">
            <span style={{width: `${quotaPct}%`}} />
          </div>
          <span className="num muted">
            {status?.daily_quota ? `今日配额 ${quotaPct}%` : "配额不限"}
          </span>
        </div>
        <div className="wb-hero-actions">
          <button
            type="button"
            className="button secondary"
            onClick={() => void save({enabled: !enabled})}
            disabled={!status || saving}
          >
            {enabled ? "暂停自动生产" : "开启自动生产"}
          </button>
          <button
            type="button"
            className="button primary wb-hero-cta"
            onClick={() => void runOnce()}
            disabled={autoBusy}
            title="立即从选题池取一条，自动跑完回测到出片全流程"
          >
            ⚡ {autoBusy ? "正在启动…" : "立即生产一条"}
          </button>
        </div>
      </section>

      {/* 2. 流水线状态带 */}
      <section className="wb-flow">
        <div className="wb-flow-step">
          <span className="wb-flow-num num">{status?.pool_size ?? "—"}</span>
          <span className="wb-flow-label">选题池待产</span>
        </div>
        <ChevronIcon size={14} />
        <div className={`wb-flow-step ${totalActive > 0 ? "active" : ""}`}>
          <span className="wb-flow-num num">{totalActive}</span>
          <span className="wb-flow-label">生产中</span>
        </div>
        <ChevronIcon size={14} />
        <div className="wb-flow-step ok">
          <span className="wb-flow-num num">
            {status?.today_completed ?? "—"}
          </span>
          <span className="wb-flow-label">今日成片</span>
        </div>
        <ChevronIcon size={14} />
        <Link
          to="/jobs?filter=attention"
          className={`wb-flow-step link ${attentionCount > 0 ? "warn" : ""}`}
          title="失败重试中与搁浅的任务，点击前往处理"
        >
          <span className="wb-flow-num num">{attentionCount}</span>
          <span className="wb-flow-label">
            <WarnIcon size={12} /> 待处理
          </span>
        </Link>
      </section>

      {/* 3. 主区：左生产监控 / 右生产设置 */}
      <div className="wb-main">
        <section className="dash-section wb-col-left">
          <div className="section-head">
            <h2>正在生产</h2>
            {totalActive > 0 ? (
              <span className="section-hint">每 3 秒自动刷新</span>
            ) : null}
          </div>
          {totalActive === 0 ? (
            <EmptyState
              title="生产线空闲"
              description="点击「立即生产一条」手动出片，或开启自动生产按每日配额无人值守运行。"
            />
          ) : (
            <>
              <div className="prod-strip">
                {activeRuns.map((run) => (
                  <PipelineProducingCard key={run.run_id} run={run} />
                ))}
                {visibleActive.map((job) => (
                  <ProducingCard key={job.job_id} job={job} />
                ))}
              </div>
              {hiddenActive > 0 ? (
                <button
                  type="button"
                  className="prod-toggle"
                  onClick={() => setShowAllActive((value) => !value)}
                >
                  {showAllActive ? "收起" : `展开其余 ${hiddenActive} 张`}
                </button>
              ) : null}
            </>
          )}
        </section>

        <div className="wb-col-right">
          {draft ? (
            <>
              <section className="wb-card wb-universe-card">
                <div className="wb-card-head">
                  <h3>动态股票库</h3>
                  <span className="muted">
                    {universe?.sync_in_progress
                      ? "正在同步全市场名单"
                      : universe?.last_sync?.completed_at
                        ? `上次同步 ${parseServerDate(universe.last_sync.completed_at).toLocaleString()}`
                        : "等待首次同步"}
                  </span>
                </div>
                <div className="wb-universe-stats">
                  <div>
                    <strong className="num">
                      {(universe?.active_total ?? 0).toLocaleString()}
                    </strong>
                    <span>主库股票</span>
                  </div>
                  <div className="ok">
                    <strong className="num">
                      {(universe?.eligible_total ?? 0).toLocaleString()}
                    </strong>
                    <span>可生产</span>
                  </div>
                  {(["CN", "HK", "US", "CRYPTO"] as const).map((market) => (
                    <div key={market}>
                      <strong className="num">
                        {(universe?.by_market[market]?.eligible ?? 0).toLocaleString()}
                      </strong>
                      <span>
                        {market === "CN"
                          ? "A股"
                          : market === "HK"
                            ? "港股"
                            : market === "US"
                              ? "美股"
                              : "加密资产"}
                      </span>
                    </div>
                  ))}
                </div>
                {universe?.last_sync?.errors?.length ? (
                  <p className="wb-universe-warning">
                    部分市场同步失败：
                    {universe.last_sync.errors
                      .map((item) => `${item.market} ${item.reason}`)
                      .join("；")}
                  </p>
                ) : null}
                <div className="wb-universe-foot">
                  <span className="muted">
                    自动每日更新；停牌、ST及非普通股会被排除
                  </span>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => void syncUniverse()}
                    disabled={syncingUniverse || universe?.sync_in_progress}
                  >
                    {syncingUniverse || universe?.sync_in_progress
                      ? "同步中…"
                      : "立即同步"}
                  </button>
                </div>
              </section>

              {/* 股票市场筛选 */}
              <section className="wb-card wb-market-card">
                <div className="wb-card-head">
                  <h3>股票市场</h3>
                  <span className="muted">
                    已选 {draft.markets.length} 个 · 支持单选或多选
                  </span>
                </div>
                <div className="wb-market-options">
                  {MARKET_OPTIONS.map((option) => {
                    const active = draft.markets.includes(option.value);
                    const eligible =
                      universe?.by_market[option.value]?.eligible ?? 0;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        className={`wb-market-option ${active ? "active" : ""}`}
                        onClick={() => toggleMarket(option.value)}
                        aria-pressed={active}
                      >
                        <span className="wb-market-check" aria-hidden="true">
                          {active ? "✓" : ""}
                        </span>
                        <span className="wb-market-copy">
                          <strong>{option.label}</strong>
                          <small>{option.description}</small>
                        </span>
                        <span className="wb-market-count num">
                          {eligible.toLocaleString()} 只可生产
                        </span>
                      </button>
                    );
                  })}
                </div>
                <p className="wb-market-hint">
                  保存后，自动生产和“立即生产一条”只会从已勾选市场取股票；多选时会在所选市场之间均衡选题。
                </p>
              </section>

              {/* 选题策略 */}
              <section className="wb-card">
                <div className="wb-card-head">
                  <h3>选题策略</h3>
                  <span className="muted">
                    {preset === "any"
                      ? "不限，均衡随机"
                      : preset === "custom"
                        ? "自定义阈值（设置页调整）"
                        : ALL_PRESETS.find((item) => item.value === preset)?.label}
                  </span>
                </div>
                <div className="wb-preset-group">
                  <span className="wb-preset-caption up">暴涨题材</span>
                  <div className="wb-preset-row">
                    {SURGE_PRESETS.map((item) => presetChip(item, "up"))}
                  </div>
                </div>
                <div className="wb-preset-group">
                  <span className="wb-preset-caption down">暴跌题材</span>
                  <div className="wb-preset-row">
                    {CRASH_PRESETS.map((item) => presetChip(item, "down"))}
                  </div>
                </div>
                <div className="wb-preset-foot">
                  <button
                    type="button"
                    className={`wb-preset any ${preset === "any" ? "active" : ""}`}
                    onClick={() => applyPreset(null)}
                  >
                    <strong>不限</strong>
                    <small>均衡随机</small>
                  </button>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => void runPreview()}
                    disabled={previewing}
                    title="按当前档位实时拉取行情试算命中数，不写入选题池"
                  >
                    {previewing ? "试算中…" : "预览候选命中"}
                  </button>
                </div>
                {preview ? (
                  <p className="wb-preview-result num">
                    命中 {preview.count} 只
                    {preview.fetch_errors > 0
                      ? ` · ${preview.fetch_errors} 只拉取失败`
                      : ""}
                    {preview.matched.length > 0
                      ? `：${preview.matched
                          .slice(0, 4)
                          .map(
                            (item) =>
                              `${item.name} ${item.forward_return_pct > 0 ? "+" : ""}${item.forward_return_pct}%`,
                          )
                          .join("、")}${preview.count > 4 ? " …" : ""}`
                      : ""}
                  </p>
                ) : null}
              </section>

              {/* 背景音乐 */}
              <section className="wb-card">
                <div className="wb-card-head">
                  <h3>背景音乐</h3>
                  <span className="muted">
                    {status?.policy.bgm_file
                      ? `当前生效：${status.policy.bgm_file}`
                      : "未选用，成片无音乐"}
                  </span>
                </div>
                <div className="wb-bgm-list">
                  <button
                    type="button"
                    className={`wb-bgm-row ${draft.bgm_file === null ? "active" : ""}`}
                    onClick={() => update({bgm_file: null})}
                  >
                    <span className="wb-bgm-check" />
                    <span>无背景音乐</span>
                  </button>
                  {bgmChoices.map((item) => (
                    <button
                      key={item.file}
                      type="button"
                      className={`wb-bgm-row ${draft.bgm_file === item.file ? "active" : ""}`}
                      onClick={() => update({bgm_file: item.file})}
                    >
                      <span className="wb-bgm-check" />
                      <span className="wb-bgm-name">{item.file}</span>
                      <span className="num muted">{formatBytes(item.size_bytes)}</span>
                    </button>
                  ))}
                </div>
                <div className="wb-bgm-foot">
                  <label className="button secondary" style={{cursor: "pointer"}}>
                    {bgmBusy ? "上传中…" : "上传音乐（mp3/wav/m4a/ogg）"}
                    <input
                      type="file"
                      accept=".mp3,.wav,.m4a,.ogg,audio/*"
                      style={{display: "none"}}
                      disabled={bgmBusy}
                      onChange={(event) => {
                        void onBgmSelected(event.target.files?.[0] ?? null);
                        event.target.value = "";
                      }}
                    />
                  </label>
                  {draft.bgm_file ? (
                    <audio
                      controls
                      preload="none"
                      src={bgmUrl(draft.bgm_file)}
                      style={{height: 32, flex: 1, minWidth: 0}}
                    />
                  ) : null}
                </div>
              </section>

              {/* 基础参数 */}
              <section className="wb-card">
                <div className="wb-card-head">
                  <h3>基础参数</h3>
                  <Link to="/settings" className="muted wb-card-link">
                    高级设置 →
                  </Link>
                </div>
                <div className="wb-params">
                  <label>
                    每日配额（留空不限）
                    <input
                      type="number"
                      min={1}
                      placeholder="不限"
                      value={draft.daily_quota ?? ""}
                      onChange={(event) => {
                        const raw = event.target.value.trim();
                        update({
                          daily_quota:
                            raw === "" ? null : Math.max(1, Number(raw) || 1),
                        });
                      }}
                    />
                  </label>
                  <label>
                    目标时长（秒）
                    <input
                      type="number"
                      min={15}
                      max={180}
                      step={5}
                      value={draft.target_duration}
                      onChange={(event) =>
                        update({target_duration: Number(event.target.value) || 60})
                      }
                    />
                  </label>
                  <label>
                    每条本金
                    <input
                      type="number"
                      min={1000}
                      step={10000}
                      value={draft.amount}
                      onChange={(event) =>
                        update({amount: Number(event.target.value) || 0})
                      }
                    />
                  </label>
                  <label>
                    选题池水位
                    <input
                      type="number"
                      min={1}
                      max={50}
                      value={draft.pool_target}
                      onChange={(event) =>
                        update({pool_target: Number(event.target.value) || 1})
                      }
                    />
                  </label>
                </div>
              </section>

              {/* 保存条 */}
              <div className="wb-savebar">
                <button
                  type="button"
                  className="button primary"
                  onClick={() => void save()}
                  disabled={saving || !dirty}
                >
                  {saving ? "保存中…" : "保存设置"}
                </button>
                <span className={`wb-dirty ${dirty ? "on" : ""}`}>
                  {dirty ? "● 有未保存的修改" : "全部修改已生效"}
                </span>
              </div>
            </>
          ) : (
            <p className="quiet-line">正在读取设置…</p>
          )}
        </div>
      </div>
    </div>
  );
};
