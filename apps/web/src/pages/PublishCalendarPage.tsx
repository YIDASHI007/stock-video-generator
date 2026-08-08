import React, {useCallback, useMemo, useState} from "react";
import {CalendarDays, Check, Clock3, Save, Shuffle, TimerReset, UsersRound} from "lucide-react";
import {api, type PublishAccount, type PublishBatch, type PublishJob} from "../api";
import {ErrorNotice, formatDateTime, parseServerDate} from "../components";
import {usePolling} from "../hooks";
import {accountRuleStore, type AccountPublishingRule} from "../workspaceStore";

const dayKey = (value: Date) => `${value.getFullYear()}-${value.getMonth()}-${value.getDate()}`;
const startOfWeek = (date: Date) => {
  const next = new Date(date);
  const offset = (next.getDay() + 6) % 7;
  next.setDate(next.getDate() - offset);
  next.setHours(0, 0, 0, 0);
  return next;
};

export const PublishCalendarPage: React.FC = () => {
  const jobsLoader = useCallback(() => api<PublishJob[]>("/api/publish/jobs?limit=500"), []);
  const batchesLoader = useCallback(() => api<PublishBatch[]>("/api/publish/batches?limit=100"), []);
  const accountsLoader = useCallback(() => api<PublishAccount[]>("/api/accounts"), []);
  const {data: jobs, error} = usePolling(jobsLoader, 5_000);
  const {data: batches} = usePolling(batchesLoader, 5_000);
  const {data: accounts} = usePolling(accountsLoader, 10_000);
  const [cursor, setCursor] = useState(() => startOfWeek(new Date()));
  const [accountId, setAccountId] = useState("");
  const selectedAccount = accountId || accounts?.[0]?.account_id || "";
  const [rule, setRule] = useState<AccountPublishingRule | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const currentRule = rule?.accountId === selectedAccount ? rule : selectedAccount ? accountRuleStore.get(selectedAccount) : null;
  const days = useMemo(() => Array.from({length: 7}, (_, index) => {const date = new Date(cursor); date.setDate(cursor.getDate() + index); return date;}), [cursor]);
  const scheduled = (jobs ?? []).filter((item) => item.scheduled_at);
  const saveRule = () => {
    if (!currentRule) return;
    accountRuleStore.upsert(currentRule);
    setRule(currentRule);
    setNotice("账号发布时间槽已保存。创建批量任务时可按该规则安排间隔。");
  };
  const updateRule = (patch: Partial<AccountPublishingRule>) => currentRule && setRule({...currentRule, ...patch});

  return <div className="page publish-calendar-page">{error ? <ErrorNotice message={error}/> : null}{notice ? <div className="notice info-notice">{notice}</div> : null}<header className="module-header"><div><span className="module-kicker">PUBLISHING SCHEDULE</span><h1>发布日历</h1><p>查看定时任务和批量队列，并为每个账号设置固定发布时间与安全间隔。</p></div><div className="module-actions"><button type="button" className="button secondary" onClick={() => {const date = new Date(cursor); date.setDate(date.getDate() - 7); setCursor(date);}}>上一周</button><button type="button" className="button secondary" onClick={() => setCursor(startOfWeek(new Date()))}>本周</button><button type="button" className="button secondary" onClick={() => {const date = new Date(cursor); date.setDate(date.getDate() + 7); setCursor(date);}}>下一周</button></div></header><div className="calendar-layout"><section className="calendar-panel"><div className="calendar-head">{days.map((date) => <div key={dayKey(date)} className={dayKey(date) === dayKey(new Date()) ? "today" : ""}><span>{["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()]}</span><strong>{date.getMonth() + 1}/{date.getDate()}</strong></div>)}</div><div className="calendar-grid">{days.map((date) => {const dayJobs = scheduled.filter((item) => dayKey(parseServerDate(item.scheduled_at!)) === dayKey(date)); return <div key={dayKey(date)} className="calendar-day">{dayJobs.map((item) => <article key={item.publish_id}><span className="calendar-time">{parseServerDate(item.scheduled_at!).toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})}</span><strong>{item.title}</strong><small>{accounts?.find((account) => account.account_id === item.account_id)?.display_name ?? item.account_id}</small><em>{item.stage}</em></article>)}{dayJobs.length === 0 ? <span className="calendar-empty">暂无排期</span> : null}</div>;})}</div><div className="calendar-batches"><span><TimerReset size={15}/> 活跃批量队列 {(batches ?? []).filter((batch) => !["COMPLETED", "CANCELLED"].includes(batch.status)).length}</span><span><Clock3 size={15}/> 已排期任务 {scheduled.length}</span></div></section><aside className="slot-settings"><div className="editor-head"><div><span className="editor-icon"><UsersRound size={18}/></span><div><span>ACCOUNT SLOT</span><h2>账号发布时间槽</h2></div></div></div><label className="field-label">发布账号<select value={selectedAccount} onChange={(event) => {setAccountId(event.target.value); setRule(accountRuleStore.get(event.target.value));}}>{(accounts ?? []).map((account) => <option key={account.account_id} value={account.account_id}>{account.display_name}</option>)}</select></label>{currentRule ? <><label className="field-label">每天首选发布时间<input type="time" value={currentRule.preferredTime} onChange={(event) => updateRule({preferredTime: event.target.value})}/></label><label className="field-label">最小发布间隔（分钟）<input type="number" min="5" value={currentRule.minIntervalMinutes} onChange={(event) => updateRule({minIntervalMinutes: Number(event.target.value)})}/><small>避免在短时间内连续发布。</small></label><label className="field-label">随机延迟范围（分钟）<div className="input-with-icon"><Shuffle size={14}/><input type="number" min="0" value={currentRule.randomDelayMinutes} onChange={(event) => updateRule({randomDelayMinutes: Number(event.target.value)})}/></div></label><label className="field-label">每日发布上限<input type="number" min="1" value={currentRule.dailyLimit} onChange={(event) => updateRule({dailyLimit: Number(event.target.value)})}/></label><div className="slot-preview"><Check size={15}/><span>推荐下一时段<strong>{currentRule.preferredTime}</strong></span></div><button type="button" className="button primary wide" onClick={saveRule}><Save size={15}/> 保存账号规则</button></> : <div className="side-empty"><CalendarDays size={24}/><strong>还没有账号</strong><span>先到账号管理绑定发布账号。</span></div>}</aside></div></div>;
};

