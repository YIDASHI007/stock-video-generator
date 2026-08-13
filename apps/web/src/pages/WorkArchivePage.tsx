import React, {useEffect, useMemo, useRef, useState} from "react";
import {useNavigate, useParams} from "react-router-dom";
import {
  ArrowLeft, Check, CheckCircle2, Clock3, Copy, Download, ExternalLink, FileText,
  History, LoaderCircle, PencilLine, Play, RefreshCw, Save, Subtitles, Video, XCircle,
} from "lucide-react";

import {
  api, douyinAccountWorkVideoUrl, type DouyinAccountJob, type DouyinBenchmarkAccount, type DouyinWork,
} from "../api";
import {ErrorNotice, SuccessNotice} from "../components";
import "./WorkArchivePage.css";

const accountApi = "/api/integrations/douyin/accounts";

type ArchiveTab = "transcript" | "timeline" | "versions";
type ExtractionTask = DouyinAccountJob & {startedAt: number};

const activeJobStatuses = new Set(["queued", "resolving", "downloading", "transcribing", "packaging"]);
const extractionSteps = [
  {status: "resolving", label: "解析链接", threshold: 5},
  {status: "downloading", label: "下载视频", threshold: 12},
  {status: "transcribing", label: "识别口播", threshold: 55},
  {status: "packaging", label: "整理时间轴", threshold: 95},
  {status: "completed", label: "写入档案", threshold: 100},
];

const looksLikeMojibake = (value?: string) => {
  if (!value) return false;
  const suspicious = value.match(/[ÃÂäåæçèéð][\x80-\xBF\w]?/g)?.length ?? 0;
  const chinese = value.match(/[\u3400-\u9fff]/g)?.length ?? 0;
  return suspicious >= 3 && suspicious > chinese / 2;
};

const readableTranscript = (work: DouyinWork) => {
  const transcript = work.transcript?.trim() ?? "";
  const raw = work.transcript_raw?.trim() ?? "";
  return looksLikeMojibake(transcript) && raw && !looksLikeMojibake(raw) ? raw : transcript;
};

const clock = (seconds = 0, precise = false) => {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const whole = Math.floor(seconds % 60).toString().padStart(2, "0");
  return precise ? `${minutes}:${whole}.${Math.floor(seconds % 1 * 10)}` : `${minutes}:${whole}`;
};

const elapsedClock = (seconds = 0) => {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
};

const displayDate = (value?: string) => {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
};

const downloadText = (name: string, text: string, type = "text/plain;charset=utf-8") => {
  const href = URL.createObjectURL(new Blob([text], {type}));
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(href);
};

const toSrt = (work: DouyinWork) => (work.segments ?? []).map((segment, index) => {
  const stamp = (value: number) => new Date(value * 1000).toISOString().slice(11, 23).replace(".", ",");
  return `${index + 1}\n${stamp(segment.start)} --> ${stamp(segment.end)}\n${segment.text}\n`;
}).join("\n");

export const WorkArchivePage: React.FC = () => {
  const {secUid = "", awemeId = ""} = useParams();
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [account, setAccount] = useState<DouyinBenchmarkAccount | null>(null);
  const [work, setWork] = useState<DouyinWork | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [activeTab, setActiveTab] = useState<ArchiveTab>("transcript");
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [videoFailed, setVideoFailed] = useState(false);
  const [extraction, setExtraction] = useState<ExtractionTask | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const load = async () => {
    const value = await api<DouyinBenchmarkAccount>(`${accountApi}/${encodeURIComponent(secUid)}`);
    const found = (value.works ?? []).find((item) => item.aweme_id === awemeId);
    if (!found) throw new Error("这条作品不在当前账号的档案中。");
    setAccount(value);
    setWork(found);
    setEditText(readableTranscript(found));
  };

  useEffect(() => {
    void load().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [secUid, awemeId]);

  useEffect(() => {
    if (!work?.job_id || !activeJobStatuses.has(work.processing_status ?? "") || extraction) return;
    setExtraction({
      id: work.job_id,
      status: work.processing_status ?? "queued",
      stage: "正在恢复任务状态",
      progress: 0,
      startedAt: Date.now(),
    });
  }, [work?.job_id, work?.processing_status, extraction]);

  useEffect(() => {
    if (!extraction || !activeJobStatuses.has(extraction.status)) return;
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - extraction.startedAt) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [extraction?.id, extraction?.status, extraction?.startedAt]);

  useEffect(() => {
    if (!extraction || !activeJobStatuses.has(extraction.status)) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const snapshot = await api<DouyinAccountJob>(
          `${accountApi}/${encodeURIComponent(secUid)}/jobs/${encodeURIComponent(extraction.id)}`,
        );
        if (cancelled) return;
        const next = {...snapshot, startedAt: extraction.startedAt};
        setExtraction(next);
        if (snapshot.status === "completed") {
          await load();
          if (!cancelled) setNotice("重新提取已完成，文案与精准时间轴已经自动更新。");
          return;
        }
        if (["failed", "cancelled", "interrupted"].includes(snapshot.status)) return;
      } catch (reason) {
        if (!cancelled) setExtraction((current) => current ? {
          ...current,
          status: "failed",
          stage: "无法读取任务进度",
          error: reason instanceof Error ? reason.message : String(reason),
        } : current);
        return;
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [extraction?.id]);

  const activeSegment = useMemo(() => work?.segments?.findIndex(
    (segment) => currentTime >= segment.start && currentTime < segment.end,
  ) ?? -1, [currentTime, work?.segments]);

  const run = async (key: string, task: () => Promise<void>) => {
    setBusy(key); setError(null); setNotice(null);
    try { await task(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(""); }
  };

  const seek = (seconds: number) => {
    const player = videoRef.current;
    if (!player) return;
    player.currentTime = seconds;
    setCurrentTime(seconds);
    void player.play().catch(() => undefined);
  };

  const saveTranscript = () => work && run("save", async () => {
    const updated = await api<DouyinWork>(
      `${accountApi}/${encodeURIComponent(secUid)}/works/${encodeURIComponent(work.aweme_id)}/transcript`,
      {method: "PUT", body: JSON.stringify({text: editText})},
    );
    setWork(updated);
    setEditing(false);
    setNotice("修订内容已保存到这条作品档案。");
  });

  const reExtract = () => work && run("extract", async () => {
    const result = await api<{jobs?: Array<DouyinAccountJob & {job_id?: string}>}>(`${accountApi}/${encodeURIComponent(secUid)}/batch`, {
      method: "POST", body: JSON.stringify({aweme_ids: [work.aweme_id], language: null}),
    });
    const submitted = result.jobs?.[0];
    const id = String(submitted?.id || submitted?.job_id || "");
    if (!id) throw new Error("提取服务没有返回任务编号，无法追踪进度。");
    setElapsed(0);
    setExtraction({
      ...submitted,
      id,
      status: submitted?.status || "queued",
      stage: submitted?.stage || "任务已进入队列",
      progress: Number(submitted?.progress || 0),
      startedAt: Date.now(),
    });
  });

  if (!work || !account) return <div className="page review-loading">{error ? <ErrorNotice message={error}/> : <><LoaderCircle className="spin" size={24}/><span>正在打开作品档案…</span></>}</div>;

  const videoUrl = douyinAccountWorkVideoUrl(secUid, awemeId);
  const transcript = readableTranscript(work);
  const transcriptRecovered = Boolean(work.transcript && transcript !== work.transcript.trim());
  const transcriptKind = work.transcript_source === "editor"
    ? transcriptRecovered ? "已从正常识别稿恢复" : `人工修订 · 第 ${work.transcript_revision ?? 1} 版`
    : "语音识别原稿";
  const timeline = work.segments ?? [];
  const versions = [...(work.transcript_versions ?? [])].reverse();

  const renderTimeline = (compact = false) => <div className={`review-timeline-list${compact ? " compact" : ""}`}>
    {(compact ? timeline.slice(0, 4) : timeline).map((segment, index) => <button
      type="button"
      className={index === activeSegment ? "active" : ""}
      key={`${segment.start}-${index}`}
      onClick={() => seek(segment.start)}
    >
      <i className="review-rail-node" aria-hidden="true"/>
      <span className="review-timeline-index">{String(index + 1).padStart(2, "0")}</span>
      <time>{clock(segment.start, true)} <em/> {clock(segment.end, true)}</time>
      <p>{segment.text}</p>
      <Play size={13}/>
    </button>)}
    {!timeline.length ? <div className="review-empty">还没有精准时间轴。重新提取后，时间片会保存在这条作品档案中。</div> : null}
  </div>;

  return <div className="page work-review-page">
    {error ? <ErrorNotice message={error}/> : null}
    {notice ? <SuccessNotice message={notice} className="review-notice"/> : null}

    <header className="review-command-bar">
      <button type="button" className="review-back" onClick={() => navigate("/analytics/benchmarks")}>
        <ArrowLeft size={18}/><span>返回 <b>{account.nickname}</b> 的作品列表</span>
      </button>
      <div className="review-command-title">
        <h1>作品文案档案</h1>
        <p>档案 ID：{work.aweme_id}</p>
      </div>
      <div className="review-command-actions">
        <button className="button secondary" onClick={() => void reExtract()} disabled={Boolean(busy) || Boolean(extraction && activeJobStatuses.has(extraction.status))}>
          {busy === "extract" ? <LoaderCircle className="spin" size={14}/> : <RefreshCw size={14}/>}重新提取
        </button>
        <a className="button secondary" href={work.url} target="_blank" rel="noreferrer">打开原作品<ExternalLink size={14}/></a>
      </div>
    </header>

    {extraction ? <section className={`review-extraction-card ${extraction.status}`} aria-live="polite">
      <div className="review-extraction-icon">
        {activeJobStatuses.has(extraction.status) ? <LoaderCircle className="spin" size={21}/> : extraction.status === "completed" ? <CheckCircle2 size={21}/> : <XCircle size={21}/>}
      </div>
      <div className="review-extraction-main">
        <header>
          <div><strong>{extraction.status === "completed" ? "重新提取完成" : activeJobStatuses.has(extraction.status) ? "正在重新提取作品" : "重新提取未完成"}</strong><span>{extraction.stage || "正在读取任务状态"}</span></div>
          <b>{Math.max(0, Math.min(100, Math.round(Number(extraction.progress || 0))))}%</b>
        </header>
        <div className="review-progress-track"><i style={{width: `${Math.max(0, Math.min(100, Number(extraction.progress || 0)))}%`}}/></div>
        <div className="review-extraction-steps">{extractionSteps.map((step) => {
          const done = Number(extraction.progress || 0) >= step.threshold || extraction.status === "completed";
          const current = extraction.status === step.status;
          return <span className={done ? "done" : current ? "current" : ""} key={step.status}><i>{done ? <Check size={10}/> : null}</i>{step.label}</span>;
        })}</div>
        {extraction.error ? <p className="review-extraction-error">{extraction.error}</p> : null}
      </div>
      <aside><span>任务编号</span><code>{extraction.id.slice(0, 12)}</code><span>已用时间</span><b>{elapsedClock(elapsed)}</b>{!activeJobStatuses.has(extraction.status) ? <button onClick={() => setExtraction(null)}>关闭</button> : null}</aside>
    </section> : null}

    <main className="review-workspace">
      <div className="review-left-column">
        <section className="review-video-stage" aria-label="作品视频播放器">
          <div className="review-video-frame">
            {!videoFailed ? <video
              ref={videoRef}
              src={videoUrl}
              poster={work.cover_url}
              controls
              preload="metadata"
              playsInline
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              onError={() => setVideoFailed(true)}
            /> : <div className="review-video-unavailable">
              {work.cover_url ? <img src={work.cover_url} alt="作品封面"/> : <Video size={40}/>}<div><strong>本地视频暂时不可播放</strong><span>可打开原作品，或重新提取补齐视频文件。</span><a href={work.url} target="_blank" rel="noreferrer">打开原作品<ExternalLink size={13}/></a></div>
            </div>}
            {activeSegment >= 0 ? <div className="review-live-caption">{timeline[activeSegment]?.text}</div> : null}
          </div>
          <div className="review-player-status">
            <span><i/>与片源同步</span>
            <b>{clock(currentTime, true)} <em>/</em> {clock(work.duration)}</b>
            <label>播放速度 <select aria-label="播放速度" defaultValue="1" onChange={(event) => { if (videoRef.current) videoRef.current.playbackRate = Number(event.target.value); }}><option value="0.75">0.75×</option><option value="1">1.0×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2.0×</option></select></label>
          </div>
        </section>

        <section className="review-source-card">
          <div className="review-meta-row">
            <div className="review-account">
              {account.avatar_url ? <img src={account.avatar_url} alt=""/> : <span>{account.nickname.slice(0, 1)}</span>}
              <div><small>视频账号</small><strong>{account.nickname}</strong></div>
            </div>
            <dl>
              <div><dt>发布时间</dt><dd>{displayDate(work.published_at)}</dd></div>
              <div><dt>识别语言</dt><dd>{work.detected_language?.toUpperCase() || "待检测"}</dd></div>
              <div><dt>文案状态</dt><dd className={transcript ? "ready" : ""}>{transcript ? "已归档" : "待提取"}</dd></div>
            </dl>
          </div>
          <div className="review-original-copy">
            <header><div><small>ORIGINAL CAPTION</small><h2>发布文案</h2></div><button onClick={() => void navigator.clipboard.writeText(work.description || work.title)}><Copy size={13}/>复制</button></header>
            <p>{work.description || work.title}</p>
          </div>
        </section>
      </div>

      <section className="review-document" aria-label="作品文案与时间轴">
        <header className="review-document-head">
          <span>作品标题</span>
          <h2>{work.title || "未命名作品"}</h2>
          <div className="review-facts"><span><Clock3 size={13}/>{clock(work.duration)}</span><span><FileText size={13}/>{transcript ? `${transcript.length} 字` : "尚无文案"}</span><span><Subtitles size={13}/>{timeline.length} 个时间片</span></div>
        </header>

        <nav className="review-tabs" aria-label="档案内容视图">
          <button className={activeTab === "transcript" ? "active" : ""} onClick={() => setActiveTab("transcript")}>口播文案</button>
          <button className={activeTab === "timeline" ? "active" : ""} onClick={() => setActiveTab("timeline")}>精准时间轴 <span>{timeline.length}</span></button>
          <button className={activeTab === "versions" ? "active" : ""} onClick={() => setActiveTab("versions")}>版本记录 <span>{versions.length}</span></button>
        </nav>

        {activeTab === "transcript" ? <div className="review-tab-panel">
          <section className="review-transcript">
            <header><div><h3>完整口播文案</h3><span>{transcriptKind}</span></div><div><button onClick={() => void navigator.clipboard.writeText(editing ? editText : transcript)} disabled={!transcript}><Copy size={13}/>复制</button><button onClick={() => downloadText(`${work.aweme_id}-口播文案.txt`, transcript)} disabled={!transcript}><Download size={13}/>下载 TXT</button><button onClick={() => setEditing((value) => !value)} disabled={!transcript}><PencilLine size={13}/>{editing ? "取消" : "修订"}</button></div></header>
            {editing ? <div className="review-transcript-editor"><textarea value={editText} onChange={(event) => setEditText(event.target.value)} rows={11}/><button className="button primary" onClick={() => void saveTranscript()} disabled={!editText.trim() || Boolean(busy)}>{busy === "save" ? <LoaderCircle className="spin" size={14}/> : <Save size={14}/>}保存修订版本</button></div> : <div className="review-transcript-text">{transcript || <span>这条作品尚未生成口播文案。点击页面上方“重新提取”开始处理。</span>}</div>}
          </section>
          <section className="review-current-timeline"><header><div><h3>当前时间片</h3><span>点击片段，视频将跳转到对应位置</span></div><button onClick={() => setActiveTab("timeline")}>查看全部</button></header>{renderTimeline(true)}</section>
        </div> : null}

        {activeTab === "timeline" ? <div className="review-tab-panel review-full-timeline">
          <header><div><h3>精准时间轴</h3><span>逐句校对并定位视频内容</span></div><button onClick={() => downloadText(`${work.aweme_id}-字幕.srt`, toSrt(work), "application/x-subrip;charset=utf-8")} disabled={!timeline.length}><Download size={13}/>下载 SRT</button></header>
          {renderTimeline()}
        </div> : null}

        {activeTab === "versions" ? <div className="review-tab-panel review-version-panel">
          <header><div><h3>文案版本记录</h3><span>每次人工修订都会保留历史版本</span></div><History size={18}/></header>
          <div className="review-version-list">{versions.map((version, index) => <article key={`${version.saved_at}-${index}`}><i/><div><header><strong>{version.source === "editor" ? "人工修订版本" : "语音识别原稿"}</strong><time>{displayDate(version.saved_at)}</time></header><p>{version.text}</p></div></article>)}{!versions.length ? <div className="review-empty">当前还没有历史版本。修订并保存文案后，版本会显示在这里。</div> : null}</div>
        </div> : null}
      </section>
    </main>
  </div>;
};
