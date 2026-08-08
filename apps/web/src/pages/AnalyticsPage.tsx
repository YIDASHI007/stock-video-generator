import React, {useCallback, useMemo} from "react";
import {BarChart3, CheckCircle2, Clock3, FileVideo2, Rocket, TrendingUp} from "lucide-react";
import {api, type GalleryOutput, type Job, type PublishJob} from "../api";
import {ErrorNotice, parseServerDate} from "../components";
import {usePolling} from "../hooks";

const marketLabels: Record<string, string> = {CN: "A股", HK: "港股", US: "美股", CRYPTO: "加密资产"};
const angleLabels: Record<string, string> = {surge: "暴涨", crash: "暴跌", rollercoaster: "过山车", compound: "长线"};

export const AnalyticsPage: React.FC = () => {
  const {data: outputs, error} = usePolling(useCallback(() => api<GalleryOutput[]>("/api/outputs"), []), 10_000);
  const {data: jobs} = usePolling(useCallback(() => api<Job[]>("/api/jobs?limit=500"), []), 10_000);
  const {data: publishes} = usePolling(useCallback(() => api<PublishJob[]>("/api/publish/jobs?limit=500"), []), 10_000);
  const stats = useMemo(() => {
    const terminal = (jobs ?? []).filter((item) => ["COMPLETED", "FAILED_FINAL"].includes(item.stage));
    const completed = terminal.filter((item) => item.stage === "COMPLETED");
    const averageMinutes = completed.length ? completed.reduce((sum, item) => sum + Math.max(0, parseServerDate(item.updated_at).getTime() - parseServerDate(item.created_at).getTime()), 0) / completed.length / 60_000 : 0;
    const published = (publishes ?? []).filter((item) => item.stage === "PUBLISHED");
    return {success: terminal.length ? completed.length / terminal.length * 100 : 100, averageMinutes, published: published.length, publishSuccess: (publishes ?? []).length ? published.length / (publishes ?? []).length * 100 : 0};
  }, [jobs, publishes]);
  const distributions = (key: "market" | "angle") => {
    const counts = new Map<string, number>();
    for (const output of outputs ?? []) {const value = output[key] ?? "unknown"; counts.set(value, (counts.get(value) ?? 0) + 1);}
    const max = Math.max(...counts.values(), 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({name, count, width: count / max * 100}));
  };
  return <div className="page analytics-page">{error ? <ErrorNotice message={error}/> : null}<header className="module-header"><div><span className="module-kicker">OPERATIONS ANALYTICS</span><h1>数据分析</h1><p>从本机真实任务与发布记录中观察产能、稳定性和内容分布。</p></div></header><section className="analytics-metrics"><article><FileVideo2 size={18}/><span>累计成片<strong>{outputs?.length ?? 0}</strong><small>当前保留的内容资产</small></span></article><article><CheckCircle2 size={18}/><span>生产成功率<strong>{stats.success.toFixed(0)}%</strong><small>已结束生产任务</small></span></article><article><Clock3 size={18}/><span>平均耗时<strong>{stats.averageMinutes.toFixed(1)} 分</strong><small>从创建到完成</small></span></article><article><Rocket size={18}/><span>成功发布<strong>{stats.published}</strong><small>成功率 {stats.publishSuccess.toFixed(0)}%</small></span></article></section><div className="analytics-grid"><section className="analytics-panel"><div className="ops-panel-head"><div><span className="panel-kicker">MARKET MIX</span><h2>市场内容分布</h2></div><BarChart3 size={18}/></div><div className="distribution-list">{distributions("market").map((item) => <div key={item.name}><span><strong>{marketLabels[item.name] ?? item.name}</strong><em>{item.count} 条</em></span><i><b style={{width: `${item.width}%`}}/></i></div>)}</div></section><section className="analytics-panel"><div className="ops-panel-head"><div><span className="panel-kicker">CONTENT ANGLES</span><h2>题材结构</h2></div><TrendingUp size={18}/></div><div className="distribution-list warm">{distributions("angle").map((item) => <div key={item.name}><span><strong>{angleLabels[item.name] ?? item.name}</strong><em>{item.count} 条</em></span><i><b style={{width: `${item.width}%`}}/></i></div>)}</div></section></div><section className="analytics-panel analytics-note"><span>说明</span><p>平台播放量、点赞、评论等表现数据需要平台开放接口或后续导入数据后才能统计。当前页面只展示能够由本机生产与发布记录可靠计算的指标，不使用估算值。</p></section></div>;
};

