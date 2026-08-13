import React, {useEffect, useMemo, useState} from "react";
import {useNavigate} from "react-router-dom";
import {
  Archive, BarChart3, Check, Clock3, Copy, Download, ExternalLink, FileText,
  LibraryBig, LoaderCircle, PencilLine, Plus, RefreshCw, Save, Search, Sparkles,
  Trash2, UserSearch, UsersRound, Video, WandSparkles, X,
} from "lucide-react";

import {
  API_BASE, api, type DouyinAccountPortrait, type DouyinAccountResolveResult,
  type DouyinBenchmarkAccount, type DouyinWork,
} from "../api";
import {ErrorNotice} from "../components";

const accountApi = "/api/integrations/douyin/accounts";
const compactNumber = (value = 0) => new Intl.NumberFormat("zh-CN", {notation: "compact", maximumFractionDigits: 1}).format(value);
const shortDate = (value?: string) => {
  if (!value) return "尚未同步";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
};
const timecode = (seconds = 0) => `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}.${Math.round(seconds % 1 * 10)}`;
const downloadText = (name: string, text: string, type = "text/plain;charset=utf-8") => {
  const href = URL.createObjectURL(new Blob([text], {type}));
  const anchor = document.createElement("a"); anchor.href = href; anchor.download = name; anchor.click(); URL.revokeObjectURL(href);
};
const toSrt = (work: DouyinWork) => (work.segments ?? []).map((item, index) => {
  const stamp = (value: number) => new Date(value * 1000).toISOString().slice(11, 23).replace(".", ",");
  return `${index + 1}\n${stamp(item.start)} --> ${stamp(item.end)}\n${item.text}\n`;
}).join("\n");

type PageTab = "works" | "transcripts" | "methodology";

export const BenchmarkAccountsPage: React.FC = () => {
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<DouyinBenchmarkAccount[]>([]);
  const [activeId, setActiveId] = useState("");
  const [query, setQuery] = useState("");
  const [workQuery, setWorkQuery] = useState("");
  const [addText, setAddText] = useState("");
  const [resolveResult, setResolveResult] = useState<DouyinAccountResolveResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openWorkId, setOpenWorkId] = useState("");
  const [editText, setEditText] = useState("");
  const [editing, setEditing] = useState(false);
  const [tab, setTab] = useState<PageTab>("works");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const active = accounts.find((item) => item.sec_uid === activeId) ?? accounts[0] ?? null;
  const works = active?.works ?? [];
  const openWork = works.find((item) => item.aweme_id === openWorkId) ?? null;
  const transcripts = works.filter((item) => item.transcript);
  const visibleWorks = (tab === "transcripts" ? transcripts : works).filter((item) => `${item.title} ${item.transcript ?? ""}`.toLowerCase().includes(workQuery.trim().toLowerCase()));
  const filteredAccounts = accounts.filter((item) => `${item.nickname} ${item.douyin_id ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const processedCount = transcripts.length;
  const metrics = useMemo(() => ({
    accounts: accounts.length,
    works: accounts.reduce((sum, item) => sum + (item.works?.length ?? item.total_stored ?? 0), 0),
    processed: accounts.reduce((sum, item) => sum + (item.works ?? []).filter((work) => work.transcript).length, 0),
    portraits: accounts.filter((item) => item.portrait).length,
  }), [accounts]);

  const loadAccounts = async (focusId?: string) => {
    const value = await api<DouyinBenchmarkAccount[]>(accountApi);
    setAccounts(value); setActiveId((current) => focusId ?? current ?? value[0]?.sec_uid ?? ""); return value;
  };
  useEffect(() => { void loadAccounts().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))); }, []);
  useEffect(() => { setSelected(new Set()); setOpenWorkId(""); }, [active?.sec_uid]);
  useEffect(() => { setEditText(openWork?.transcript ?? ""); setEditing(false); }, [openWork?.aweme_id, openWork?.transcript]);

  const run = async (key: string, task: () => Promise<void>) => {
    setBusy(key); setError(null); setNotice(null);
    try { await task(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(""); }
  };
  const resolve = () => run("resolve", async () => {
    const value = await api<DouyinAccountResolveResult>(`${accountApi}/resolve`, {method: "POST", body: JSON.stringify({text: addText})});
    setResolveResult(value); if (value.account) setNotice("已识别账号，确认后添加到对标库。");
  });
  const saveAccount = (account: DouyinBenchmarkAccount) => run("save", async () => {
    const value = await api<DouyinBenchmarkAccount>(`${accountApi}/${encodeURIComponent(account.sec_uid)}`, {method: "POST", body: JSON.stringify(account)});
    await loadAccounts(value.sec_uid); setResolveResult(null); setAddText(""); setNotice(`已添加“${value.nickname}”。`);
  });
  const sync = () => active && run("sync", async () => {
    await api(`${accountApi}/${encodeURIComponent(active.sec_uid)}/sync`, {method: "POST", body: JSON.stringify({limit: 100})});
    await loadAccounts(active.sec_uid); setNotice("公开作品列表已同步。");
  });
  const batch = (ids = [...selected]) => active && run("batch", async () => {
    const result = await api<{count: number}>(`${accountApi}/${encodeURIComponent(active.sec_uid)}/batch`, {method: "POST", body: JSON.stringify({aweme_ids: ids, language: null})});
    await loadAccounts(active.sec_uid); setSelected(new Set()); setNotice(`已提交 ${result.count} 条作品，任务完成后会自动归档到这个账号。`);
  });
  const analyze = () => active && run("analyze", async () => {
    const portrait = await api<DouyinAccountPortrait>(`${accountApi}/${encodeURIComponent(active.sec_uid)}/analyze`, {method: "POST", body: JSON.stringify({sample_size: Math.min(50, Math.max(5, works.length))})});
    setAccounts((current) => current.map((item) => item.sec_uid === active.sec_uid ? {...item, portrait} : item)); setNotice("账号画像与创作方法论已更新。");
  });
  const saveTranscript = () => active && openWork && run("save-transcript", async () => {
    const updated = await api<DouyinWork>(`${accountApi}/${encodeURIComponent(active.sec_uid)}/works/${openWork.aweme_id}/transcript`, {method: "PUT", body: JSON.stringify({text: editText})});
    setAccounts((current) => current.map((account) => account.sec_uid !== active.sec_uid ? account : {...account, works: (account.works ?? []).map((work) => work.aweme_id === updated.aweme_id ? updated : work)}));
    setEditing(false); setNotice("修订文案已保存，并继续归属于这条作品和这个账号。");
  });
  const exportSkill = () => active && run("skill", async () => {
    const response = await fetch(`${API_BASE}${accountApi}/${encodeURIComponent(active.sec_uid)}/skill`, {method: "POST"});
    if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail || "Skill 生成失败"); }
    const blob = await response.blob(); const href = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = href; anchor.download = `${active.nickname}-创作方法Skill.zip`; anchor.click(); URL.revokeObjectURL(href);
    await loadAccounts(active.sec_uid); setNotice("可复用 Skill 已生成并下载。");
  });
  const refreshPortrait = async () => {
    if (!active) return;
    const value = await api<DouyinBenchmarkAccount>(`${accountApi}/${encodeURIComponent(active.sec_uid)}`);
    setAccounts((current) => current.map((item) => item.sec_uid === value.sec_uid ? value : item));
  };
  const remove = () => active && run("delete", async () => {
    await api(`${accountApi}/${encodeURIComponent(active.sec_uid)}`, {method: "DELETE"}); const value = await loadAccounts(); setActiveId(value[0]?.sec_uid ?? ""); setNotice("对标账号已移除。");
  });
  const toggleWork = (work: DouyinWork) => setSelected((current) => { const next = new Set(current); next.has(work.aweme_id) ? next.delete(work.aweme_id) : next.add(work.aweme_id); return next; });
  const openArchive = (work: DouyinWork) => active && navigate(`/analytics/benchmarks/${encodeURIComponent(active.sec_uid)}/works/${encodeURIComponent(work.aweme_id)}`);

  const portrait = active?.portrait;
  const methodology = portrait?.methodology;
  const candidates = resolveResult?.account ? [resolveResult.account] : resolveResult?.candidates ?? [];

  return <div className="page benchmark-page">
    {error ? <ErrorNotice message={error}/> : null}{notice ? <div className="notice ok-notice">{notice}</div> : null}
    <header className="module-header benchmark-hero">
      <div><span className="module-kicker">BENCHMARK INTELLIGENCE</span><h1>对标账号</h1><p>每条作品、逐字稿与分析证据都绑定到原账号，持续沉淀为可复用的创作方法。</p></div>
      <div className="benchmark-flow" aria-label="分析流程"><span className={active ? "done" : "active"}><UsersRound size={15}/>添加账号</span><i/><span className={works.length ? "done" : active ? "active" : ""}><RefreshCw size={15}/>同步作品</span><i/><span className={processedCount ? "done" : works.length ? "active" : ""}><FileText size={15}/>提取文案</span><i/><span className={portrait ? "done" : processedCount ? "active" : ""}><Sparkles size={15}/>生成画像</span><i/><span className={active?.skill_export ? "done" : portrait ? "active" : ""}><WandSparkles size={15}/>导出 Skill</span></div>
    </header>
    <section className="benchmark-metrics"><article><UsersRound size={17}/><span>对标账号<strong>{metrics.accounts}</strong></span></article><article><Video size={17}/><span>已收录作品<strong>{metrics.works}</strong></span></article><article><FileText size={17}/><span>已提取文案<strong>{metrics.processed}</strong></span></article><article><BarChart3 size={17}/><span>已生成画像<strong>{metrics.portraits}</strong></span></article></section>
    <div className="benchmark-layout">
      <aside className="benchmark-account-panel">
        <div className="benchmark-add"><label><UserSearch size={16}/><input value={addText} onChange={(event) => setAddText(event.target.value)} onKeyDown={(event) => event.key === "Enter" && addText.trim() && void resolve()} placeholder="抖音号、昵称或主页分享链接"/></label><button type="button" onClick={() => void resolve()} disabled={!addText.trim() || Boolean(busy)}>{busy === "resolve" ? <LoaderCircle className="spin" size={16}/> : <Plus size={16}/>} 添加</button></div>
        {candidates.length ? <div className="benchmark-candidates"><strong>{resolveResult?.match === "candidates" ? "请选择匹配账号" : "确认添加账号"}</strong>{candidates.map((item) => <button key={item.sec_uid} type="button" onClick={() => void saveAccount(item)} disabled={Boolean(busy)}>{item.avatar_url ? <img src={item.avatar_url} alt=""/> : <span>{item.nickname.slice(0, 1)}</span>}<b>{item.nickname}<small>抖音号 {item.douyin_id || "未公开"} · {compactNumber(item.follower_count)} 粉丝</small></b><Plus size={15}/></button>)}</div> : null}
        <label className="benchmark-search"><Search size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索已添加账号"/></label>
        <div className="benchmark-account-list">{filteredAccounts.map((item) => <button className={item.sec_uid === active?.sec_uid ? "active" : ""} key={item.sec_uid} type="button" onClick={() => setActiveId(item.sec_uid)}>{item.avatar_url ? <img src={item.avatar_url} alt=""/> : <span>{item.nickname.slice(0, 1)}</span>}<b>{item.nickname}<small>{item.total_stored ?? item.works?.length ?? 0} 条作品 · {(item.works ?? []).filter((work) => work.transcript).length} 份逐字稿</small></b></button>)}{!accounts.length ? <div className="benchmark-empty-small"><UserSearch size={22}/><strong>还没有对标账号</strong><span>在上方粘贴主页分享链接开始。</span></div> : null}</div>
      </aside>
      <main className="benchmark-main">
        {!active ? <div className="benchmark-empty"><UserSearch size={34}/><h2>先添加一个对标账号</h2><p>支持账号主页分享链接、抖音号或昵称；使用链接匹配最准确。</p></div> : <>
          <section className="benchmark-profile-head">
            <div className="benchmark-identity">{active.avatar_url ? <img src={active.avatar_url} alt=""/> : <span>{active.nickname.slice(0, 1)}</span>}<div><span>当前对标账号</span><h2>{active.nickname}</h2><p>{active.signature || "这个账号暂未填写简介。"}</p><small>抖音号 {active.douyin_id || "未公开"} · 上次同步 {shortDate(active.last_synced_at)}</small></div></div>
            <div className="benchmark-profile-stats"><span><strong>{compactNumber(active.follower_count)}</strong>粉丝</span><span><strong>{compactNumber(active.total_favorited)}</strong>获赞</span><span><strong>{active.aweme_count ?? works.length}</strong>作品</span></div>
            <div className="benchmark-actions"><button type="button" className="button secondary" onClick={() => void sync()} disabled={Boolean(busy)}>{busy === "sync" ? <LoaderCircle className="spin" size={14}/> : <RefreshCw size={14}/>} 同步作品</button><button type="button" className="icon-button danger" title="移除对标账号" onClick={() => void remove()} disabled={Boolean(busy)}><Trash2 size={15}/></button></div>
          </section>
          <nav className="benchmark-tabs" aria-label="账号档案栏目"><button className={tab === "works" ? "active" : ""} onClick={() => setTab("works")}><Video size={15}/>作品档案 <b>{works.length}</b></button><button className={tab === "transcripts" ? "active" : ""} onClick={() => setTab("transcripts")}><LibraryBig size={15}/>文案库 <b>{transcripts.length}</b></button><button className={tab === "methodology" ? "active" : ""} onClick={() => { setTab("methodology"); if (portrait && !portrait.methodology) void refreshPortrait(); }}><WandSparkles size={15}/>画像与方法论</button></nav>
          {tab !== "methodology" ? <section className="benchmark-works-card benchmark-archive-card">
            <header><div><span className="panel-kicker">{tab === "works" ? "PUBLIC WORKS" : "TRANSCRIPT ARCHIVE"}</span><h2>{tab === "works" ? "作品档案" : `${active.nickname} 的账号文案库`}</h2></div><div className="archive-tools"><label><Search size={14}/><input value={workQuery} onChange={(event) => setWorkQuery(event.target.value)} placeholder="搜索标题或文案"/></label><button type="button" className="button secondary" onClick={() => setSelected(new Set(works.slice(0, 20).map((item) => item.aweme_id)))} disabled={!works.length}>选择前 20 条</button><button type="button" className="button primary" onClick={() => void batch()} disabled={!selected.size || Boolean(busy)}>{busy === "batch" ? <LoaderCircle className="spin" size={14}/> : <FileText size={14}/>} 提取 {selected.size || "所选"} 条</button></div></header>
            <div className="benchmark-work-list">{visibleWorks.map((work) => <article key={work.aweme_id} className={openWorkId === work.aweme_id ? "opened" : ""} onClick={() => openArchive(work)}>{work.cover_url ? <img src={work.cover_url} alt=""/> : <span className="work-cover"><Video size={20}/></span>}<div><h3>{work.title || "未命名作品"}</h3><p><Clock3 size={12}/>{work.duration ? `${Math.round(work.duration)} 秒` : "时长未知"}<b>赞 {compactNumber(work.statistics?.like)}</b><b>评 {compactNumber(work.statistics?.comment)}</b></p><small className={work.transcript ? "ready" : work.processing_status === "failed" ? "failed" : ""}>{work.transcript ? `文案已提取 · ${work.segments?.length ?? 0} 个时间片` : work.processing_status === "failed" ? `提取失败：${work.processing_error ?? "请重试"}` : work.processing_status ? `处理中：${work.processing_status}` : "尚未提取文案"}</small></div><button className={selected.has(work.aweme_id) ? "work-select selected" : "work-select"} title="加入批量选择" onClick={(event) => { event.stopPropagation(); toggleWork(work); }}>{selected.has(work.aweme_id) ? <Check size={13}/> : null}</button></article>)}{!visibleWorks.length ? <div className="benchmark-empty-list"><RefreshCw size={23}/><strong>{tab === "transcripts" ? "还没有已提取文案" : "没有匹配作品"}</strong><span>{tab === "transcripts" ? "在作品档案中勾选作品并提取，完成后会自动归档到这里。" : "尝试清空搜索条件。"}</span></div> : null}</div>
          </section> : <section className="methodology-layout">
            <article className="benchmark-portrait-card"><header><div><span className="panel-kicker">ACCOUNT PORTRAIT</span><h2>定位与风格画像</h2></div><button type="button" className="button secondary" onClick={() => void analyze()} disabled={!works.length || Boolean(busy)}>{busy === "analyze" ? <LoaderCircle className="spin" size={14}/> : <Sparkles size={14}/>} {portrait ? "重新分析" : "生成画像"}</button></header>{portrait ? <div className="portrait-body"><div className="portrait-position"><span>定位结论</span><p>{portrait.positioning}</p></div><div className="portrait-section"><h3>内容支柱</h3>{portrait.content_pillars.map((item) => <div className="pillar" key={item.name}><span><b>{item.name}</b><em>{item.ratio}%</em></span><i><b style={{width: `${item.ratio}%`}}/></i></div>)}</div><div className="portrait-tags">{portrait.top_hashtags.slice(0, 8).map((item) => <span key={item.name}>#{item.name}</span>)}</div></div> : <div className="portrait-empty"><Sparkles size={28}/><strong>画像等待生成</strong><p>同步作品后可生成初步画像；完整逐字稿越多，表达风格越准确。</p></div>}</article>
            <article className="methodology-card"><header><div><span className="panel-kicker">CREATIVE METHOD</span><h2>创作方法论</h2></div>{methodology ? <span className="confidence">{methodology.confidence}置信度</span> : null}</header>{methodology ? <div className="methodology-body"><div className="method-progress"><span><b>{methodology.transcript_count}</b> / {methodology.sample_count} 条完整逐字稿</span><i><b style={{width: `${methodology.completion_ratio}%`}}/></i><small>逐字稿覆盖率 {methodology.completion_ratio}%</small></div><section><h3>常用开场机制</h3><div className="method-chips">{methodology.hook_patterns.map((item) => <span key={item.name}>{item.name}<b>{item.count}</b></span>)}</div></section><section><h3>叙事推进结构</h3><div className="method-chips">{methodology.narrative_structures.map((item) => <span key={item.name}>{item.name}<b>{item.count}</b></span>)}</div></section><section className="method-stats"><span><b>{methodology.language_style.average_sentence_chars}</b>字 / 句</span><span><b>{methodology.language_style.estimated_chars_per_second}</b>字 / 秒</span><span><b>{methodology.language_style.short_line_ratio}%</b>短句占比</span></section><div className="skill-export"><Archive size={20}/><div><strong>生成可复用 Skill</strong><p>包含账号画像、钩子和结构规律、节奏参数、证据索引与原创边界。</p></div><button className="button primary" onClick={() => void exportSkill()} disabled={Boolean(busy)}>{busy === "skill" ? <LoaderCircle className="spin" size={14}/> : <Download size={14}/>} 导出 Skill</button></div></div> : <div className="portrait-empty"><WandSparkles size={28}/><strong>尚未提炼方法论</strong><p>点击左侧“生成画像”，系统会同时整理有证据支撑的创作方法。</p></div>}</article>
          </section>}
        </>}
      </main>
    </div>
    {openWork ? <div className="archive-scrim" onMouseDown={(event) => event.target === event.currentTarget && setOpenWorkId("")}><aside className="work-archive-drawer" role="dialog" aria-modal="true" aria-label="作品档案详情">
      <header><div><span className="panel-kicker">WORK ARCHIVE · {openWork.aweme_id}</span><h2>作品文案档案</h2></div><button className="icon-button" onClick={() => setOpenWorkId("")}><X size={18}/></button></header>
      <div className="drawer-scroll"><section className="drawer-work-head">{openWork.cover_url ? <img src={openWork.cover_url} alt=""/> : null}<div><h3>{openWork.title}</h3><p><Clock3 size={13}/>{openWork.duration ? `${openWork.duration.toFixed(1)} 秒` : "时长未知"} · {openWork.detected_language ? `语言 ${openWork.detected_language}` : "语言待检测"}</p><a href={openWork.url} target="_blank" rel="noreferrer">打开原作品 <ExternalLink size={12}/></a></div></section>
        <section className="drawer-section"><header><div><span>发布文案</span><small>账号原始发布信息</small></div><button onClick={() => void navigator.clipboard.writeText(openWork.description || openWork.title)}><Copy size={13}/>复制</button></header><div className="published-copy">{openWork.description || openWork.title}</div></section>
        <section className="drawer-section transcript-editor"><header><div><span>完整口播文案</span><small>{openWork.transcript_source === "editor" ? `人工修订 · 第 ${openWork.transcript_revision ?? 1} 版` : "语音识别原稿"}</small></div><div><button onClick={() => void navigator.clipboard.writeText(editing ? editText : openWork.transcript ?? "")} disabled={!openWork.transcript}><Copy size={13}/>复制</button><button onClick={() => downloadText(`${openWork.aweme_id}-口播文案.txt`, openWork.transcript ?? "")} disabled={!openWork.transcript}><Download size={13}/>TXT</button><button onClick={() => setEditing((value) => !value)} disabled={!openWork.transcript}><PencilLine size={13}/>{editing ? "取消" : "修订"}</button></div></header>{editing ? <><textarea value={editText} onChange={(event) => setEditText(event.target.value)} rows={12}/><button className="button primary save-edit" onClick={() => void saveTranscript()} disabled={!editText.trim() || Boolean(busy)}>{busy === "save-transcript" ? <LoaderCircle className="spin" size={14}/> : <Save size={14}/>}保存修订版本</button></> : <div className="transcript-copy">{openWork.transcript || <span>这条作品还没有口播文案。点击下方重新提取。</span>}</div>}</section>
        <section className="drawer-section"><header><div><span>精准时间轴</span><small>{openWork.segments?.length ?? 0} 个语义片段</small></div><button onClick={() => downloadText(`${openWork.aweme_id}-字幕.srt`, toSrt(openWork), "application/x-subrip;charset=utf-8")} disabled={!openWork.segments?.length}><Download size={13}/>SRT</button></header><div className="segment-list">{openWork.segments?.map((segment, index) => <div key={`${segment.start}-${index}`}><time>{timecode(segment.start)}<i/> {timecode(segment.end)}</time><p>{segment.text}</p></div>)}{!openWork.segments?.length ? <div className="segment-empty">历史任务尚未恢复时间轴，刷新账号后会自动补齐；也可以重新提取。</div> : null}</div></section>
        <section className="drawer-history"><span>归档状态</span><p>账号：{active?.nickname} · 作品：{openWork.aweme_id}</p><p>任务：{openWork.job_id || "尚未创建"} · 更新：{shortDate(openWork.transcript_updated_at)}</p>{openWork.processing_error ? <p className="failed">上次失败：{openWork.processing_error}</p> : null}</section>
      </div><footer><button className="button secondary" onClick={() => void batch([openWork.aweme_id])} disabled={Boolean(busy)}><RefreshCw size={14}/>重新提取</button><button className="button primary" onClick={() => setOpenWorkId("")}>完成</button></footer>
    </aside></div> : null}
  </div>;
};
