import React, {useEffect, useState} from "react";
import {
  CheckCircle2,
  ClipboardPaste,
  Cloud,
  Download,
  FileJson2,
  FileText,
  Film,
  Link2,
  LoaderCircle,
  Save,
  ServerCog,
  ShieldCheck,
  Subtitles,
  XCircle,
} from "lucide-react";

import {
  api,
  douyinFileUrl,
  type DouyinIntegrationSettings,
  type DouyinJobSnapshot,
  type DouyinRemoteFile,
} from "../api";
import {ErrorNotice} from "../components";

const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const statusLabels: Record<string, string> = {
  queued: "等待排队",
  resolving: "解析分享链接",
  downloading: "下载原视频",
  transcribing: "识别口播与时间轴",
  packaging: "整理结果文件",
  completed: "提取完成",
  failed: "处理失败",
  cancelled: "任务已取消",
  interrupted: "服务重启导致中断",
};

const size = (bytes: number) =>
  bytes >= 1024 ** 2
    ? `${(bytes / 1024 ** 2).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;

const fileIcon = (file: DouyinRemoteFile) => {
  if (file.name.endsWith(".mp4")) return <Film size={16}/>;
  if (file.name.endsWith(".srt")) return <Subtitles size={16}/>;
  if (file.name.endsWith(".json")) return <FileJson2 size={16}/>;
  return <FileText size={16}/>;
};

export const DouyinImportPage: React.FC = () => {
  const [settings, setSettings] = useState<DouyinIntegrationSettings | null>(null);
  const [draft, setDraft] = useState({enabled: true, base_url: "", client_id: "", api_key: ""});
  const [shareText, setShareText] = useState("");
  const [language, setLanguage] = useState("");
  const [job, setJob] = useState<DouyinJobSnapshot | null>(null);
  const [connection, setConnection] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    void api<DouyinIntegrationSettings>("/api/integrations/douyin/settings")
      .then((value) => {
        setSettings(value);
        setDraft({enabled: value.enabled, base_url: value.base_url, client_id: value.client_id, api_key: ""});
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    if (!job || terminal.has(job.remote.status)) return;
    const timer = window.setInterval(() => {
      void api<DouyinJobSnapshot>(`/api/integrations/douyin/jobs/${job.local_job_id}`)
        .then(setJob)
        .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job?.local_job_id, job?.remote.status]);

  const saveSettings = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const value = await api<DouyinIntegrationSettings>("/api/integrations/douyin/settings", {
        method: "PUT",
        body: JSON.stringify({
          ...draft,
          api_key: draft.api_key || null,
          connect_timeout_seconds: settings?.connect_timeout_seconds ?? 15,
          job_timeout_seconds: settings?.job_timeout_seconds ?? 3600,
        }),
      });
      setSettings(value); setDraft({...draft, api_key: ""}); setNotice("提取服务配置已加密保存。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };

  const testConnection = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const value = await api<Record<string, unknown>>("/api/integrations/douyin/test", {method: "POST"});
      setConnection(value); setNotice("远程提取服务连接正常。");
    } catch (reason) { setConnection(null); setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };

  const createJob = async () => {
    setBusy(true); setError(null); setNotice(null); setJob(null);
    try {
      const value = await api<DouyinJobSnapshot>("/api/integrations/douyin/jobs", {
        method: "POST",
        body: JSON.stringify({text: shareText, language: language || null}),
      });
      setJob(value); setNotice("任务已提交到远程处理电脑。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };

  const importJob = async () => {
    if (!job) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await api(`/api/integrations/douyin/jobs/${job.local_job_id}/import`, {method: "POST"});
      const refreshed = await api<DouyinJobSnapshot>(`/api/integrations/douyin/jobs/${job.local_job_id}`);
      setJob(refreshed); setNotice("视频、文案、字幕和时间轴已经导入本机内容目录。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };

  const copyTranscript = async () => {
    const text = job?.remote.result?.transcript;
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setNotice("口播文案已复制。");
  };

  return (
    <div className="page douyin-import-page">
      {error ? <ErrorNotice message={error}/> : null}
      {notice ? <div className="notice ok-notice">{notice}</div> : null}
      <header className="module-header douyin-import-hero">
        <div><span className="module-kicker">REMOTE SOURCE INGEST</span><h1>链接提取</h1><p>把抖音视频送到远程处理电脑，取回原视频、发布文案、口播和精准时间轴。</p></div>
        <div className={`remote-chain ${connection ? "online" : ""}`}><span><Link2 size={15}/> 当前工作台</span><i/><span><Cloud size={15}/> 加密隧道</span><i/><span><ServerCog size={15}/> 处理电脑</span></div>
      </header>

      <div className="douyin-import-grid">
        <section className="douyin-source-card">
          <div className="ops-panel-head"><div><span className="panel-kicker">NEW EXTRACTION</span><h2>粘贴分享链接</h2></div><ClipboardPaste size={19}/></div>
          <textarea value={shareText} onChange={(event) => setShareText(event.target.value)} rows={7} placeholder="粘贴完整的抖音分享文本，例如：标题、作者、https://v.douyin.com/..."/>
          <div className="douyin-submit-row"><label>原声语言<select value={language} onChange={(event) => setLanguage(event.target.value)}><option value="">自动检测</option><option value="zh">中文</option><option value="en">英文</option></select></label><button className="button primary" type="button" onClick={() => void createJob()} disabled={busy || !shareText.trim() || !settings?.enabled}>{busy ? <LoaderCircle className="spin" size={15}/> : <Film size={15}/>} 开始提取</button></div>
          <div className="extract-output-strip"><span><Film size={14}/> 原视频</span><span><FileText size={14}/> 发布与口播文案</span><span><Subtitles size={14}/> SRT 字幕</span><span><FileJson2 size={14}/> 时间轴 JSON</span></div>
        </section>

        <aside className="douyin-service-card">
          <div className="ops-panel-head"><div><span className="panel-kicker">SERVICE LINK</span><h2>远程服务</h2></div><ShieldCheck size={19}/></div>
          <label>服务地址<input value={draft.base_url} onChange={(event) => setDraft({...draft, base_url: event.target.value})} placeholder="https://douyin-api.example.com"/></label>
          <label>客户端标识<input value={draft.client_id} onChange={(event) => setDraft({...draft, client_id: event.target.value})} placeholder="office-computer-02"/></label>
          <label>访问密钥<input type="password" value={draft.api_key} onChange={(event) => setDraft({...draft, api_key: event.target.value})} placeholder={settings?.api_key_hint ?? "粘贴创建客户端时生成的密钥"}/></label>
          <label className="checkbox-label"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({...draft, enabled: event.target.checked})}/> 启用远程提取</label>
          <div className="service-actions"><button className="button secondary" type="button" onClick={() => void saveSettings()} disabled={busy}><Save size={14}/> 保存</button><button className="button secondary" type="button" onClick={() => void testConnection()} disabled={busy || !settings?.enabled}><Cloud size={14}/> 测试连接</button></div>
          <div className={`service-state ${connection ? "online" : ""}`}>{connection ? <CheckCircle2 size={15}/> : <XCircle size={15}/>}<span><strong>{connection ? "服务在线" : "尚未验证"}</strong><small>{connection ? `模型 ${String(connection.model ?? "—")} · 队列 ${String(connection.queue_depth ?? 0)}` : "保存配置后测试公网链路"}</small></span></div>
        </aside>
      </div>

      {job ? <section className="douyin-job-card">
        <div className="douyin-job-head"><div><span className={`job-status-dot ${job.remote.status}`}/><span><strong>{statusLabels[job.remote.status] ?? job.remote.stage}</strong><small>{job.remote.stage} · 任务 {job.remote_job_id}</small></span></div><b>{Math.round(job.remote.progress)}%</b></div>
        <div className="douyin-progress"><i style={{width: `${job.remote.progress}%`}}/></div>
        {job.remote.error ? <ErrorNotice message={job.remote.error}/> : null}
        {job.remote.status === "completed" ? <div className="douyin-result-grid">
          <article className="transcript-panel"><header><div><span className="panel-kicker">TRANSCRIPT</span><h2>{job.remote.result?.title || "识别结果"}</h2></div><button type="button" className="button secondary" onClick={() => void copyTranscript()}><ClipboardPaste size={14}/> 复制文案</button></header><p className="post-description">{job.remote.result?.description || "作者没有填写发布文案"}</p><div className="transcript-copy">{job.remote.result?.transcript || "没有识别到有效口播"}</div></article>
          <aside className="result-files"><h2>结果文件</h2>{job.remote.files.filter((file) => !file.name.endsWith(".log")).map((file) => <a key={file.path} href={douyinFileUrl(job.local_job_id, file.url)} download>{fileIcon(file)}<span><strong>{file.name}</strong><small>{size(file.size)}</small></span><Download size={14}/></a>)}<button type="button" className="button primary" onClick={() => void importJob()} disabled={busy || Boolean(job.imported_at)}><Save size={15}/> {job.imported_at ? "已导入内容库" : "导入本机内容库"}</button></aside>
        </div> : null}
      </section> : null}
    </div>
  );
};
