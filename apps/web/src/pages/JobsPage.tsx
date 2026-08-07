import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Link, useSearchParams} from "react-router-dom";

import {
  api,
  angleLabels,
  pipelineRunTitle,
  pipelineStageLabels,
  type Job,
  type PipelineRun,
  type RetryResponse,
} from "../api";
import {
  ChevronIcon,
  EmptyState,
  ErrorNotice,
  JobBadge,
  ProgressBar,
  RenderIcon,
  RetryIcon,
  SimIcon,
  StopIcon,
  elapsedText,
  formatFullDateTime,
  isActiveStage,
  isAttentionStage,
  jobTitle,
  parseServerDate,
} from "../components";
import {usePolling} from "../hooks";

type Filter = "active" | "attention" | "parked" | "all";

const pipelineBadgeKind = (status: string): string => {
  if (status === "COMPLETED") return "ok";
  if (status === "PARKED") return "bad";
  if (status === "FAILED") return "warn";
  if (status === "SKIPPED") return "mute";
  return "run";
};

const PipelineRunRow: React.FC<{
  run: PipelineRun;
  busy: boolean;
  onAction: (run: PipelineRun, action: "retry" | "skip") => void;
}> = ({run, busy, onAction}) => {
  const actionable = run.status === "PARKED" || run.status === "FAILED";
  return (
    <article className="job-row">
      <div className="job-row-main">
        <span className="job-type-icon" title="自动生产">
          <span aria-hidden="true">⚡</span>
        </span>
        <span className="job-name">
          <strong>{pipelineRunTitle(run)}</strong>
          <small className="num">{run.run_id.slice(0, 8)}</small>
        </span>
        <span className={`badge badge-${pipelineBadgeKind(run.status)}`}>
          {pipelineStageLabels[run.status] ?? run.status}
        </span>
        <span className="job-progress">
          <span className="num muted">
            {run.topic
              ? `${angleLabels[run.topic.angle] ?? run.topic.angle} · 买入 ${run.topic.buy_date}`
              : "—"}
          </span>
        </span>
        <span className="job-created num muted">
          {formatFullDateTime(run.created_at)}
        </span>
        <span className="job-retry num muted">
          {run.retry_count > 0 ? `重试 ${run.retry_count}` : "—"}
        </span>
        <span className="job-row-actions">
          {actionable ? (
            <>
              <button
                type="button"
                className="mini-button"
                onClick={() => onAction(run, "retry")}
                disabled={busy}
              >
                <RetryIcon size={13} />
                重试
              </button>
              <button
                type="button"
                className="mini-button danger"
                onClick={() => onAction(run, "skip")}
                disabled={busy}
              >
                <StopIcon size={13} />
                跳过
              </button>
            </>
          ) : null}
        </span>
      </div>
      {run.error ? <div className="job-error-line">{run.error}</div> : null}
    </article>
  );
};

const JobRow: React.FC<{
  job: Job;
  focused: boolean;
  expanded: boolean;
  busy: boolean;
  onToggle: () => void;
  onAction: (job: Job, action: "cancel" | "retry") => void;
}> = ({job, focused, expanded, busy, onToggle, onAction}) => {
  const failed = job.stage === "FAILED_FINAL" || job.stage === "FAILED_RETRYABLE";
  const active = isActiveStage(job.stage);
  return (
    <article
      className={`job-row ${focused ? "focused" : ""} ${expanded ? "expanded" : ""}`}
      id={`job-${job.job_id}`}
    >
      <div
        className="job-row-main"
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") onToggle();
        }}
      >
        <span className="job-type-icon" title={job.job_type === "RENDER" ? "渲染任务" : "回测任务"}>
          {job.job_type === "RENDER" ? <RenderIcon size={17} /> : <SimIcon size={17} />}
        </span>
        <span className="job-name">
          <strong>{jobTitle(job)}</strong>
          <small className="num">{job.job_id.slice(0, 8)}</small>
        </span>
        <JobBadge stage={job.stage} />
        <span className="job-progress">
          <ProgressBar value={job.progress} />
        </span>
        <span className="job-created num muted">
          {formatFullDateTime(job.created_at)}
        </span>
        <span className="job-retry num muted">
          {job.retry_count > 0 ? `重试 ${job.retry_count}` : "—"}
        </span>
        <span className="job-row-actions" onClick={(event) => event.stopPropagation()}>
          {failed ? (
            <>
              <button
                type="button"
                className="mini-button"
                onClick={() => onAction(job, "retry")}
                disabled={busy}
              >
                <RetryIcon size={13} />
                重试
              </button>
              <button
                type="button"
                className="mini-button danger"
                onClick={() => onAction(job, "cancel")}
                disabled={busy}
              >
                <StopIcon size={13} />
                取消
              </button>
            </>
          ) : active ? (
            <button
              type="button"
              className="mini-button danger"
              onClick={() => onAction(job, "cancel")}
              disabled={busy}
            >
              <StopIcon size={13} />
              取消
            </button>
          ) : null}
        </span>
        <span className={`chevron ${expanded ? "" : "closed"}`}>
          <ChevronIcon size={14} />
        </span>
      </div>
      {failed && job.error_reason ? (
        <div className="job-error-line">{job.error_reason}</div>
      ) : null}
      {expanded ? (
        <div className="job-detail">
          <div className="job-detail-grid">
            <section>
              <h4>请求参数</h4>
              <pre className="json-view">{JSON.stringify(job.input, null, 2)}</pre>
            </section>
            <section>
              <h4>产物路径</h4>
              {job.output_paths ? (
                <dl className="detail-list paths">
                  {Object.entries(job.output_paths).map(([label, path]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd title={path}>{path}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="fine-print">尚无产物。</p>
              )}
            </section>
          </div>
          {job.error_reason ? (
            <section className="job-detail-error">
              <h4>错误详情{job.error_type ? ` · ${job.error_type}` : ""}</h4>
              <pre className="json-view error">{job.error_reason}</pre>
            </section>
          ) : null}
          <div className="job-detail-meta num">
            <span>更新于 {formatFullDateTime(job.updated_at)}</span>
            <span>已运行 {elapsedText(job.created_at, job.updated_at)}</span>
            {job.next_retry_at ? (
              <span>下次重试 {formatFullDateTime(job.next_retry_at)}</span>
            ) : null}
            {job.data_source ? <span>数据源 {job.data_source}</span> : null}
          </div>
          {job.simulation_id ? (
            <div className="job-detail-links">
              <Link to={`/simulations/${job.simulation_id}`}>查看回测详情 ›</Link>
              {job.stage === "COMPLETED" ? (
                <Link to={`/simulations/${job.simulation_id}/preview`}>
                  视频预览与渲染 ›
                </Link>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
};

export const JobsPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const filter = (params.get("filter") as Filter) || "active";
  const focus = params.get("focus");

  const loader = useCallback(() => api<Job[]>("/api/jobs"), []);
  const {data: jobs, error, loading} = usePolling(loader, 3000);
  const runsLoader = useCallback(
    () => api<PipelineRun[]>("/api/pipeline/runs?filter=all"),
    [],
  );
  const {data: pipelineRuns} = usePolling(runsLoader, 3000);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(focus);
  const focusedRef = useRef<string | null>(null);

  const all = useMemo(
    () =>
      [...(jobs ?? [])].sort(
        (a, b) =>
          parseServerDate(b.created_at).getTime() -
          parseServerDate(a.created_at).getTime(),
      ),
    [jobs],
  );

  const parkedRuns = useMemo(
    () =>
      (pipelineRuns ?? []).filter(
        (run) => run.status === "PARKED" || run.status === "FAILED",
      ),
    [pipelineRuns],
  );

  const counts = useMemo(
    () => ({
      active: all.filter((job) => isActiveStage(job.stage)).length,
      attention: all.filter((job) => isAttentionStage(job.stage)).length,
      parked: parkedRuns.length,
      all: all.length,
    }),
    [all, parkedRuns],
  );

  const visible = useMemo(() => {
    if (filter === "attention") {
      return all.filter((job) => isAttentionStage(job.stage));
    }
    if (filter === "all") return all;
    if (filter === "parked") return [];
    return all.filter((job) => isActiveStage(job.stage));
  }, [all, filter]);

  useEffect(() => {
    if (focus && focus !== focusedRef.current) {
      focusedRef.current = focus;
      setExpandedId(focus);
      window.requestAnimationFrame(() => {
        document
          .getElementById(`job-${focus}`)
          ?.scrollIntoView({block: "center", behavior: "smooth"});
      });
    }
  }, [focus]);

  const setFilter = (next: Filter) => {
    setParams(next === "active" ? {} : {filter: next}, {replace: true});
  };

  const runAction = async (job: Job, action: "cancel" | "retry") => {
    setBusyJob(job.job_id);
    setActionError(null);
    try {
      await api<Job | RetryResponse>(`/api/jobs/${job.job_id}/${action}`, {
        method: "POST",
      });
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyJob(null);
    }
  };

  const runPipelineAction = async (run: PipelineRun, action: "retry" | "skip") => {
    setBusyJob(run.run_id);
    setActionError(null);
    try {
      await api<PipelineRun>(`/api/pipeline/runs/${run.run_id}/${action}`, {
        method: "POST",
      });
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyJob(null);
    }
  };

  return (
    <div className="page jobs-page">
      <div className="chip-row">
        {(
          [
            ["active", "进行中", counts.active],
            ["attention", "需要处理", counts.attention],
            ["parked", "搁浅", counts.parked],
            ["all", "全部历史", counts.all],
          ] as Array<[Filter, string, number]>
        ).map(([key, label, count]) => (
          <button
            key={key}
            type="button"
            className={`chip ${filter === key ? "active" : ""} ${
              (key === "attention" || key === "parked") && count > 0
                ? "has-attention"
                : ""
            }`}
            onClick={() => setFilter(key)}
          >
            {label}
            <span className="chip-count num">{count}</span>
          </button>
        ))}
      </div>

      {error ? <ErrorNotice message={error} /> : null}
      {actionError ? <ErrorNotice message={actionError} /> : null}

      {filter === "parked" ? (
        parkedRuns.length === 0 ? (
          <EmptyState
            title="没有搁浅的生产任务"
            description="自动生产连续失败 3 次才会搁浅，搁浅后会出现在这里等待人工处理。"
          />
        ) : (
          <div className="jobs-list">
            {parkedRuns.map((run) => (
              <PipelineRunRow
                key={run.run_id}
                run={run}
                busy={busyJob === run.run_id}
                onAction={(target, action) => void runPipelineAction(target, action)}
              />
            ))}
          </div>
        )
      ) : !loading &&
        visible.length === 0 &&
        (filter !== "all" || (pipelineRuns ?? []).length === 0) ? (
        <EmptyState
          title={
            filter === "attention"
              ? "没有需要处理的任务"
              : filter === "all"
                ? "还没有任何任务"
                : "当前没有进行中的任务"
          }
          description={
            filter === "active"
              ? "从驾驶舱新建一条回测视频，任务进度会实时出现在这里。"
              : "任务的状态变更会在这里如实记录。"
          }
          action={
            <Link className="button primary" to="/create">
              新建回测视频
            </Link>
          }
        />
      ) : (
        <div className="jobs-list">
          {filter === "all"
            ? (pipelineRuns ?? []).map((run) => (
                <PipelineRunRow
                  key={run.run_id}
                  run={run}
                  busy={busyJob === run.run_id}
                  onAction={(target, action) => void runPipelineAction(target, action)}
                />
              ))
            : null}
          {visible.map((job) => (
            <JobRow
              key={job.job_id}
              job={job}
              focused={focus === job.job_id}
              expanded={expandedId === job.job_id}
              busy={busyJob === job.job_id}
              onToggle={() =>
                setExpandedId((current) =>
                  current === job.job_id ? null : job.job_id,
                )
              }
              onAction={(target, action) => void runAction(target, action)}
            />
          ))}
        </div>
      )}
    </div>
  );
};
