import React, {useCallback} from "react";
import {Link, NavLink, Outlet} from "react-router-dom";

import {api, type Job} from "./api";
import {usePolling} from "./hooks";

/* ---------------- 阶段与状态 ---------------- */

export const stageLabels: Record<string, string> = {
  CREATED: "排队中",
  RESOLVING_SYMBOL: "识别股票",
  FETCHING_MARKET_DATA: "抓取行情",
  VALIDATING_DATA: "校验数据",
  SIMULATING_PORTFOLIO: "回测模拟",
  BUILDING_VIDEO_SPEC: "生成规格",
  RENDERING_VIDEO: "渲染视频",
  VALIDATING_OUTPUT: "校验输出",
  COMPLETED: "已完成",
  FAILED_RETRYABLE: "等待重试",
  FAILED_FINAL: "失败",
  CANCELLED: "已取消",
};

const ACTIVE_PIPELINE = [
  "CREATED",
  "RESOLVING_SYMBOL",
  "FETCHING_MARKET_DATA",
  "VALIDATING_DATA",
  "SIMULATING_PORTFOLIO",
  "BUILDING_VIDEO_SPEC",
  "RENDERING_VIDEO",
  "VALIDATING_OUTPUT",
];

export const isActiveStage = (stage: string): boolean =>
  ACTIVE_PIPELINE.includes(stage);

// 「需要处理」只算失败任务；CANCELLED 是用户主动取消的终态，不算待办。
export const isAttentionStage = (stage: string): boolean =>
  stage === "FAILED_FINAL" || stage === "FAILED_RETRYABLE";

export type StageKind = "run" | "ok" | "warn" | "bad" | "mute";

export const stageKind = (stage: string): StageKind => {
  if (stage === "COMPLETED") return "ok";
  if (stage === "FAILED_FINAL") return "bad";
  if (stage === "FAILED_RETRYABLE") return "warn";
  if (stage === "CANCELLED") return "mute";
  return "run";
};

export const jobStageLabel = (job: Job): string => {
  if (!isActiveStage(job.stage)) return stageLabels[job.stage] ?? job.stage;
  if (job.job_type === "RENDER") return "渲染中";
  return stageLabels[job.stage] ?? job.stage;
};

export const jobTitle = (job: Job): string => {
  const symbol = job.input?.symbol;
  if (typeof symbol === "string" && symbol) return symbol;
  const simulationId = job.input?.simulation_id ?? job.simulation_id;
  if (typeof simulationId === "string" && simulationId) {
    return `回测 ${simulationId.slice(0, 8)}`;
  }
  return job.job_type === "RENDER" ? "视频渲染" : "回测任务";
};

/* ---------------- 时间与数字 ---------------- */

const timeFmt = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
});

const dateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const fullDateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const dateFmt = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "short",
});

/**
 * 后端时间戳是 UTC 但不带时区后缀（如 2026-07-26T06:33:50），
 * 直接 new Date() 会被当成本地时间，产生 8 小时偏差。
 * 统一在这里按 UTC 解析；已带 Z / ±hh:mm 后缀的保持原样。
 */
export const parseServerDate = (iso: string): Date =>
  new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso.trim()) ? iso : `${iso}Z`);

export const formatTime = (iso: string): string =>
  timeFmt.format(parseServerDate(iso));
export const formatDateTime = (iso: string): string =>
  dateTimeFmt.format(parseServerDate(iso));
export const formatFullDateTime = (iso: string): string =>
  fullDateTimeFmt.format(parseServerDate(iso));

export const dayLabel = (iso: string): string => {
  const date = parseServerDate(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(date, today)) return "今天";
  if (sameDay(date, yesterday)) return "昨天";
  return dateFmt.format(date);
};

export const dayKey = (iso: string): string => {
  const d = parseServerDate(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

export const elapsedText = (fromIso: string, toIso?: string): string => {
  const from = parseServerDate(fromIso).getTime();
  const to = toIso ? parseServerDate(toIso).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
};

/* ---------------- 内联图标 ---------------- */

type IconProps = {size?: number};

const Svg: React.FC<IconProps & {children: React.ReactNode}> = ({
  size = 16,
  children,
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {children}
  </svg>
);

export const DashboardIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <rect x="3" y="3" width="8" height="10" rx="1.5" />
    <rect x="13" y="3" width="8" height="6" rx="1.5" />
    <rect x="13" y="11" width="8" height="10" rx="1.5" />
    <rect x="3" y="15" width="8" height="6" rx="1.5" />
  </Svg>
);

export const JobsIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M4 6h16" />
    <path d="M4 12h16" />
    <path d="M4 18h10" />
  </Svg>
);

export const SettingsIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="3.2" />
    <path d="M12 2.8v2.6M12 18.6v2.6M2.8 12h2.6M18.6 12h2.6M5.5 5.5l1.8 1.8M16.7 16.7l1.8 1.8M18.5 5.5l-1.8 1.8M7.3 16.7l-1.8 1.8" />
  </Svg>
);

export const PlusIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const PlayIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none" />
  </Svg>
);

export const DownloadIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M12 4v11M7 10.5l5 5 5-5" />
    <path d="M4 19.5h16" />
  </Svg>
);

export const RetryIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M20 12a8 8 0 1 1-2.3-5.6" />
    <path d="M20 3.5V8h-4.5" />
  </Svg>
);

export const TrashIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M4.5 6.5h15" />
    <path d="M9 6V4.5h6V6" />
    <path d="M6.5 6.5 7.4 20h9.2l.9-13.5" />
    <path d="M10.2 10v6M13.8 10v6" />
  </Svg>
);

export const StopIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M9 9h6v6H9z" fill="currentColor" stroke="none" />
  </Svg>
);

export const WarnIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M12 4 2.8 20h18.4z" />
    <path d="M12 10v4.2" />
    <circle cx="12" cy="17" r="0.4" fill="currentColor" />
  </Svg>
);

export const ChevronIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="m9 5 7 7-7 7" />
  </Svg>
);

export const SimIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M4 19V9M9.3 19V5M14.6 19v-8M20 19v-5" />
  </Svg>
);

export const RenderIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="m10.5 9.5 4.5 2.5-4.5 2.5z" fill="currentColor" stroke="none" />
  </Svg>
);

export const PublishIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M12 16V4M7.5 8.5 12 4l4.5 4.5" />
    <path d="M5 13v6h14v-6" />
  </Svg>
);

export const AccountsIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <circle cx="9" cy="8" r="3" />
    <path d="M3.8 19c.4-3.4 2.1-5.2 5.2-5.2s4.8 1.8 5.2 5.2" />
    <circle cx="17.2" cy="9.2" r="2.2" />
    <path d="M15.3 14.4c3.2-.4 5 .9 5.4 3.9" />
  </Svg>
);

export const WorkbenchIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="M4 7h16M4 12h16M4 17h16" />
    <circle cx="9" cy="7" r="2" fill="currentColor" stroke="none" />
    <circle cx="15" cy="12" r="2" fill="currentColor" stroke="none" />
    <circle cx="7" cy="17" r="2" fill="currentColor" stroke="none" />
  </Svg>
);

export const BackIcon: React.FC<IconProps> = (props) => (
  <Svg {...props}>
    <path d="m14 5-7 7 7 7" />
  </Svg>
);

/* ---------------- 布局 ---------------- */

type ProviderHealth = {name: string; available: boolean};

const SidebarHealth: React.FC = () => {
  const loader = useCallback(() => api<ProviderHealth[]>("/api/providers/health"), []);
  const {data} = usePolling(loader, 60_000);
  const ok = data ? data.some((item) => item.available) : null;
  return (
    <Link to="/settings" className="sidebar-health">
      <span
        className={`health-dot ${ok === null ? "unknown" : ok ? "ok" : "bad"}`}
      />
      <span className="sidebar-health-text">
        {ok === null ? "数据源检查中" : ok ? "数据源正常" : "数据源异常"}
      </span>
    </Link>
  );
};

export const Layout: React.FC = () => (
  <div className="app-shell">
    <aside className="sidebar">
      <Link to="/" className="brand">
        <span className="brand-mark">↗</span>
        <span className="brand-text">
          <strong>回测影像</strong>
          <small>STOCK TIMELINE</small>
        </span>
      </Link>
      <nav className="sidebar-nav">
        <NavLink to="/" end>
          <DashboardIcon size={17} />
          <span>驾驶舱</span>
        </NavLink>
        <NavLink to="/workbench">
          <WorkbenchIcon size={17} />
          <span>工作台</span>
        </NavLink>
        <NavLink to="/jobs">
          <JobsIcon size={17} />
          <span>任务中心</span>
        </NavLink>
        <NavLink to="/publish">
          <PublishIcon size={17} />
          <span>发布中心</span>
        </NavLink>
        <NavLink to="/accounts">
          <AccountsIcon size={17} />
          <span>账号管理</span>
        </NavLink>
        <NavLink to="/settings">
          <SettingsIcon size={17} />
          <span>设置</span>
        </NavLink>
      </nav>
      <SidebarHealth />
    </aside>
    <main className="main-content">
      <Outlet />
    </main>
  </div>
);

/* ---------------- 通用件 ---------------- */

export const PageHeader: React.FC<{
  eyebrow: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
}> = ({eyebrow, title, description, actions}) => (
  <header className="page-header">
    <div>
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      <p>{description}</p>
    </div>
    {actions ? <div className="header-actions">{actions}</div> : null}
  </header>
);

export const BackLink: React.FC<{to: string; label: string}> = ({to, label}) => (
  <Link to={to} className="back-link">
    <BackIcon size={15} />
    {label}
  </Link>
);

export const ErrorNotice: React.FC<{message: string}> = ({message}) => (
  <div className="notice error-notice">
    <WarnIcon size={15} />
    <span>{message}</span>
  </div>
);

export const JobBadge: React.FC<{stage: string}> = ({stage}) => (
  <span className={`badge badge-${stageKind(stage)}`}>
    {stageLabels[stage] ?? stage}
  </span>
);

export const ProgressBar: React.FC<{value: number}> = ({value}) => {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="progress-group">
      <div className="progress-track">
        <span style={{width: `${pct}%`}} />
      </div>
      <span className="num progress-pct">{pct}%</span>
    </div>
  );
};

export const EmptyState: React.FC<{
  title: string;
  description: string;
  action?: React.ReactNode;
}> = ({title, description, action}) => (
  <div className="empty-state">
    <h3>{title}</h3>
    <p>{description}</p>
    {action}
  </div>
);
