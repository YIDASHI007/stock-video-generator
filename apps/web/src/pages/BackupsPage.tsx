import React, {useCallback, useState} from "react";
import {CheckCircle2, CircleAlert, Database, DownloadCloud, GitBranch, HardDrive, RefreshCw, ShieldCheck} from "lucide-react";
import {api} from "../api";
import {ErrorNotice, InfoNotice, formatDateTime} from "../components";
import {usePolling} from "../hooks";

type SystemStatus = {version: string; runtime_mode: "source" | "installed"; data_dir: string; log_dir: string; database_path: string; database_size_bytes: number; outputs_size_bytes: number; output_count: number; job_count: number; publish_count: number; disk_free_bytes: number; disk_total_bytes: number};
type Backup = {name: string; path: string; size_bytes: number; created_at: string};
type SourceUpdate = {mode: "source" | "installed"; state: "current" | "available" | "blocked" | "diverged" | "ahead" | "error" | "unsupported"; current_version: string; latest_version: string | null; update_available: boolean; can_update: boolean; dirty: boolean; behind_commits: number; ahead_commits: number; release_notes: string[]; message: string};
const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

export const BackupsPage: React.FC = () => {
  const {data: status, error, refresh: refreshStatus} = usePolling(useCallback(() => api<SystemStatus>("/api/system/status"), []), 30_000);
  const {data: backups, refresh} = usePolling(useCallback(() => api<Backup[]>("/api/system/backups"), []), 15_000);
  const {data: sourceUpdate, error: updateError, refresh: refreshUpdate} = usePolling(useCallback(() => api<SourceUpdate>("/api/system/source-update"), []), 300_000);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const createBackup = async () => {setBusy(true); setNotice(null); try {const item = await api<Backup>("/api/system/backups", {method: "POST"}); setNotice(`备份已创建：${item.path}`); refresh(); refreshStatus();} catch (reason) {setNotice(reason instanceof Error ? reason.message : String(reason));} finally {setBusy(false);}};
  const checkSourceUpdate = async () => {setBusy(true); setNotice(null); try {const result = await api<SourceUpdate>("/api/system/source-update?refresh=true"); setNotice(result.message); refreshUpdate();} catch (reason) {setNotice(reason instanceof Error ? reason.message : String(reason));} finally {setBusy(false);}};
  const updateTone = sourceUpdate?.state === "available" ? "available" : ["blocked", "diverged", "error"].includes(sourceUpdate?.state ?? "") ? "attention" : "current";
  return <div className="page backups-page">
    {error ? <ErrorNotice message={error}/> : null}{updateError ? <ErrorNotice message={updateError}/> : null}{notice ? <InfoNotice message={notice}/> : null}
    <header className="module-header"><div><span className="module-kicker">DATA SAFETY & UPDATES</span><h1>备份与更新</h1><p>数据库与程序文件相互独立；备份只包含数据库和关键配置，不复制大量视频。</p></div><div className="module-actions"><button type="button" className="button secondary" onClick={() => {refresh(); refreshStatus(); refreshUpdate();}}><RefreshCw size={15}/> 刷新状态</button><button type="button" className="button primary" onClick={() => void createBackup()} disabled={busy}><ShieldCheck size={15}/> {busy ? "正在处理" : "立即备份"}</button></div></header>
    {status ? <section className="system-facts"><article><Database size={19}/><span><strong>{size(status.database_size_bytes)}</strong><small>数据库 · {status.job_count} 个任务</small></span></article><article><HardDrive size={19}/><span><strong>{size(status.outputs_size_bytes)}</strong><small>{status.output_count} 个成片文件</small></span></article><article><DownloadCloud size={19}/><span><strong>v{status.version}</strong><small>{status.runtime_mode === "source" ? "源码运行版" : "安装版"}</small></span></article><article><CheckCircle2 size={19}/><span><strong>{size(status.disk_free_bytes)}</strong><small>磁盘可用空间</small></span></article></section> : null}
    <div className="backup-layout"><section className="backup-list"><div className="section-head"><h2>本地备份</h2><span>{backups?.length ?? 0} 个归档</span></div>{(backups ?? []).map((item) => <article key={item.path}><Database size={17}/><span><strong>{item.name}</strong><small>{formatDateTime(item.created_at)} · {size(item.size_bytes)}</small><em>{item.path}</em></span></article>)}{!backups?.length ? <div className="asset-empty"><Database size={26}/><strong>还没有备份</strong><span>点击“立即备份”创建第一个数据库归档。</span></div> : null}</section>
      <aside className={`update-card ${updateTone}`}><span className="editor-icon">{updateTone === "attention" ? <CircleAlert size={18}/> : updateTone === "available" ? <DownloadCloud size={18}/> : <GitBranch size={18}/>}</span><h2>源码版更新</h2><p>{sourceUpdate?.message ?? "正在读取源码版本状态…"}</p><div><span>当前版本</span><strong>v{status?.version ?? "—"}</strong></div><div><span>远程版本</span><strong>{sourceUpdate?.latest_version ? `v${sourceUpdate.latest_version}` : "—"}</strong></div><div><span>更新方式</span><strong>Git 增量源码</strong></div>{sourceUpdate?.release_notes?.length ? <ul aria-label="主要更新内容">{sourceUpdate.release_notes.slice(0, 4).map((item) => <li key={item}>{item}</li>)}</ul> : null}<button type="button" className="button secondary" onClick={() => void checkSourceUpdate()} disabled={busy}><RefreshCw size={15}/> {busy ? "正在检查" : "联网检查更新"}</button><small>发现新版时，桌面启动会先询问，确认后才拉取并重启；不会覆盖数据目录：</small><code>{status?.data_dir ?? "正在读取…"}</code></aside>
    </div>
  </div>;
};
