import React, {useCallback, useMemo, useState} from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileVideo2,
  GitBranch,
  Play,
  Radio,
  Rocket,
  TrendingUp,
  UsersRound,
} from "lucide-react";
import {Link} from "react-router-dom";

import {
  api,
  type GalleryOutput,
  type Job,
  type PipelineRun,
  type PipelineStatusResponse,
  type PublishAccount,
  type PublishBatch,
  type PublishJob,
} from "../api";
import {ErrorNotice, formatDateTime, isActiveStage, parseServerDate} from "../components";
import {usePolling} from "../hooks";

type RangeKey = "today" | "7d" | "30d" | "all";

const RANGE_OPTIONS: Array<{key: RangeKey; label: string; days: number | null}> = [
  {key: "all", label: "实时", days: null},
  {key: "today", label: "今天", days: 1},
  {key: "7d", label: "7 天", days: 7},
  {key: "30d", label: "30 天", days: 30},
];

const inRange = (iso: string, range: RangeKey): boolean => {
  if (range === "all") return true;
  const item = parseServerDate(iso).getTime();
  const now = new Date();
  if (range === "today") {
    const date = parseServerDate(iso);
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  }
  const days = range === "7d" ? 7 : 30;
  return item >= Date.now() - days * 24 * 60 * 60 * 1000;
};

const pct = (value: number): string => `${Math.round(value)}%`;

const TrendChart: React.FC<{outputs: GalleryOutput[]; range: RangeKey}> = ({outputs, range}) => {
  const points = useMemo(() => {
    const days = range === "today" ? 12 : range === "7d" ? 7 : range === "30d" ? 15 : 18;
    const stepHours = range === "today" ? 2 : range === "7d" ? 24 : range === "30d" ? 48 : Math.max(24, Math.ceil((Date.now() - Math.min(...outputs.map((item) => parseServerDate(item.created_at).getTime()), Date.now())) / (days * 3_600_000)));
    const end = Date.now();
    const values = Array.from({length: days}, (_, index) => {
      const start = end - (days - index) * stepHours * 3_600_000;
      const stop = start + stepHours * 3_600_000;
      return outputs.filter((item) => {
        const time = parseServerDate(item.created_at).getTime();
        return time >= start && time < stop;
      }).length;
    });
    const max = Math.max(...values, 1);
    return values.map((value, index) => ({
      x: 8 + (index / Math.max(1, values.length - 1)) * 584,
      y: 166 - (value / max) * 124,
      value,
    }));
  }, [outputs, range]);
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const area = `${path} L592,174 L8,174 Z`;
  return (
    <div className="ops-chart">
      <svg viewBox="0 0 600 184" role="img" aria-label="内容产出趋势">
        <defs>
          <linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#33d6a6" stopOpacity=".28"/><stop offset="1" stopColor="#33d6a6" stopOpacity="0"/></linearGradient>
        </defs>
        {[42, 84, 126, 168].map((y) => <line key={y} x1="8" y1={y} x2="592" y2={y} className="chart-grid" />)}
        <path d={area} fill="url(#trendArea)" />
        <path d={path} className="chart-line" />
        {points.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r={index === points.length - 1 ? 4 : 2.2} className="chart-dot" />)}
      </svg>
      <div className="ops-chart-labels"><span>{range === "today" ? "00:00" : "起始"}</span><span>当前</span></div>
    </div>
  );
};

export const DashboardPage: React.FC = () => {
  const [range, setRange] = useState<RangeKey>("7d");
  const outputsLoader = useCallback(() => api<GalleryOutput[]>("/api/outputs"), []);
  const jobsLoader = useCallback(() => api<Job[]>("/api/jobs?limit=500"), []);
  const pipelineLoader = useCallback(() => api<PipelineStatusResponse>("/api/pipeline/status"), []);
  const runsLoader = useCallback(() => api<PipelineRun[]>("/api/pipeline/runs?filter=all"), []);
  const publishLoader = useCallback(() => api<PublishJob[]>("/api/publish/jobs?limit=500"), []);
  const batchesLoader = useCallback(() => api<PublishBatch[]>("/api/publish/batches?limit=100"), []);
  const accountsLoader = useCallback(() => api<PublishAccount[]>("/api/accounts"), []);
  const {data: outputs, error: outputsError} = usePolling(outputsLoader, 5_000);
  const {data: jobs, error: jobsError} = usePolling(jobsLoader, 5_000);
  const {data: pipeline} = usePolling(pipelineLoader, 5_000);
  const {data: runs} = usePolling(runsLoader, 5_000);
  const {data: publishes} = usePolling(publishLoader, 5_000);
  const {data: batches} = usePolling(batchesLoader, 5_000);
  const {data: accounts} = usePolling(accountsLoader, 15_000);

  const filteredOutputs = useMemo(() => (outputs ?? []).filter((item) => inRange(item.created_at, range)), [outputs, range]);
  const filteredJobs = useMemo(() => (jobs ?? []).filter((item) => inRange(item.created_at, range)), [jobs, range]);
  const filteredPublishes = useMemo(() => (publishes ?? []).filter((item) => inRange(item.created_at, range)), [publishes, range]);
  const activeJobs = (jobs ?? []).filter((item) => isActiveStage(item.stage)).length + (pipeline?.active_runs ?? 0);
  const failedJobs = (jobs ?? []).filter((item) => item.stage === "FAILED_FINAL" || item.stage === "FAILED_RETRYABLE");
  const failedPublishes = (publishes ?? []).filter((item) => item.stage.includes("FAILED") || item.stage === "NEEDS_HUMAN");
  const completedJobs = filteredJobs.filter((item) => item.stage === "COMPLETED").length;
  const terminalJobs = filteredJobs.filter((item) => ["COMPLETED", "FAILED_FINAL"].includes(item.stage)).length;
  const successRate = terminalJobs ? completedJobs / terminalJobs * 100 : 100;
  const publishedCount = filteredPublishes.filter((item) => item.stage === "PUBLISHED" || Boolean(item.published_url)).length;
  const connectedAccounts = (accounts ?? []).filter((item) => item.auth_status === "logged_in").length;
  const waitingPublish = filteredPublishes.filter((item) => ["CREATED", "MANIFEST_READY", "WAITING_APPROVAL", "SCHEDULED"].includes(item.stage)).length;
  const attention = [...failedJobs.map((job) => ({
    id: job.job_id,
    title: job.input?.symbol ? `${String(job.input.symbol)} 生产任务失败` : "内容生产任务失败",
    detail: job.error_reason ?? "等待检查失败原因",
    to: `/jobs?filter=attention`,
    time: job.updated_at,
    tone: "danger",
  })), ...failedPublishes.map((job) => ({
    id: job.publish_id,
    title: `发布任务需要处理`,
    detail: job.error_reason ?? job.title,
    to: "/publish/records",
    time: job.updated_at,
    tone: "warning",
  }))].sort((a, b) => parseServerDate(b.time).getTime() - parseServerDate(a.time).getTime()).slice(0, 4);
  const upcoming = (publishes ?? []).filter((item) => item.scheduled_at && ["SCHEDULED", "CREATED", "MANIFEST_READY"].includes(item.stage)).sort((a, b) => parseServerDate(a.scheduled_at!).getTime() - parseServerDate(b.scheduled_at!).getTime()).slice(0, 3);
  const activeBatches = (batches ?? []).filter((batch) => !["COMPLETED", "CANCELLED", "PARTIAL_FAILED"].includes(batch.status)).length;

  return (
    <div className="page ops-dashboard">
      {outputsError ? <ErrorNotice message={outputsError} /> : null}
      {jobsError ? <ErrorNotice message={jobsError} /> : null}

      <header className="ops-hero">
        <div>
          <span className="ops-kicker"><Radio size={13} /> 运营状态实时同步</span>
          <h1>工作空间</h1>
          <p>系统运行{failedJobs.length + failedPublishes.length === 0 ? "稳定" : "存在待处理事项"}，今日已完成 <strong>{(outputs ?? []).filter((item) => inRange(item.created_at, "today")).length}</strong> 条内容。</p>
        </div>
        <div className="ops-actions">
          <div className="range-switch" aria-label="统计范围">
            {RANGE_OPTIONS.map((option) => <button key={option.key} type="button" className={range === option.key ? "active" : ""} onClick={() => setRange(option.key)}>{option.label}</button>)}
          </div>
          <Link to="/workbench" className="button primary"><Play size={15} fill="currentColor" /> 开始生产</Link>
        </div>
      </header>

      <section className="ops-metrics">
        <article><span>进行中工作流</span><strong>{activeJobs}</strong><small><Activity size={13}/> 自动生产 {pipeline?.enabled ? "已开启" : "已暂停"}</small></article>
        <article><span>待发布内容</span><strong>{waitingPublish}</strong><small><Clock3 size={13}/> 等待人工确认</small></article>
        <article><span>已发布内容</span><strong>{publishedCount}</strong><small><Rocket size={13}/> 当前统计周期</small></article>
        <article className={attention.length ? "warning" : ""}><span>异常事项</span><strong>{attention.length}</strong><small><AlertTriangle size={13}/> {attention.length ? "需要尽快处理" : "没有阻塞事项"}</small></article>
        <article><span>生产成功率</span><strong>{pct(successRate)}</strong><small><TrendingUp size={13}/> {terminalJobs} 个已结束任务</small></article>
      </section>

      <div className="ops-grid">
        <div className="ops-main-column">
          <section className="ops-panel trend-panel">
            <div className="ops-panel-head"><div><span className="panel-kicker">CONTENT OUTPUT</span><h2>内容产出趋势</h2></div><div className="panel-total"><strong>{filteredOutputs.length}</strong><span>条成片</span></div></div>
            <TrendChart outputs={outputs ?? []} range={range} />
            <div className="trend-legend"><span><i className="legend-emerald"/> 自动生产成片</span><span>数据来自本机真实输出记录</span></div>
          </section>

          <section className="ops-panel workflow-panel">
            <div className="ops-panel-head"><div><span className="panel-kicker">WORKFLOW HEALTH</span><h2>工作流效率</h2></div><Link to="/workflows">管理工作流 <ArrowUpRight size={14}/></Link></div>
            <div className="workflow-table">
              <div className="workflow-row table-head"><span>工作流</span><span>状态</span><span>周期产出</span><span>成功率</span><span>最近运行</span></div>
              <div className="workflow-row"><span className="workflow-name"><i className={pipeline?.enabled ? "status-live" : "status-idle"}/><span><strong>股票历史回测视频</strong><small>自动选题 → 回测 → 渲染</small></span></span><span className={`status-text ${pipeline?.enabled ? "live" : "idle"}`}>{pipeline?.enabled ? "运行中" : "已暂停"}</span><span>{filteredOutputs.length}</span><span>{pct(successRate)}</span><span>{(runs ?? [])[0]?.updated_at ? formatDateTime((runs ?? [])[0].updated_at) : "尚未运行"}</span></div>
            </div>
          </section>
        </div>

        <aside className="ops-side-column">
          <section className="ops-panel action-panel">
            <div className="ops-panel-head"><div><span className="panel-kicker">ACTION REQUIRED</span><h2>需要处理</h2></div><span className="count-badge">{attention.length}</span></div>
            {attention.length === 0 ? <div className="side-empty"><CheckCircle2 size={26}/><strong>一切正常</strong><span>目前没有需要人工处理的事项</span></div> : <div className="attention-list">{attention.map((item) => <Link key={item.id} to={item.to} className={`attention-item ${item.tone}`}><span className="attention-icon"><AlertTriangle size={15}/></span><span><strong>{item.title}</strong><small>{item.detail}</small><em>{formatDateTime(item.time)}</em></span><ChevronRight size={15}/></Link>)}</div>}
          </section>

          <section className="ops-panel queue-panel">
            <div className="ops-panel-head"><div><span className="panel-kicker">PUBLISHING QUEUE</span><h2>接下来发布</h2></div><Link to="/publish/calendar">查看日历</Link></div>
            {upcoming.length === 0 ? <div className="side-empty compact"><CalendarDays size={22}/><strong>暂无排期</strong><span>可在发布台创建定时任务</span></div> : <div className="upcoming-list">{upcoming.map((item) => <Link key={item.publish_id} to="/publish/calendar"><span className="upcoming-time"><strong>{formatDateTime(item.scheduled_at!)}</strong><small>计划发布</small></span><span><strong>{item.title}</strong><small>{(accounts ?? []).find((account) => account.account_id === item.account_id)?.display_name ?? item.account_id}</small></span></Link>)}</div>}
            <div className="queue-summary"><span><UsersRound size={14}/> 已连接 {connectedAccounts} 个账号</span><span><GitBranch size={14}/> {activeBatches} 个发布队列</span></div>
          </section>

          <Link to="/assets" className="ops-quick-link"><span><FileVideo2 size={18}/><span><strong>进入内容库</strong><small>查看、编辑并选择待发布内容</small></span></span><ArrowUpRight size={17}/></Link>
        </aside>
      </div>
    </div>
  );
};
