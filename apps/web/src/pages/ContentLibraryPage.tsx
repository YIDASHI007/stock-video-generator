import React, {useCallback, useMemo, useState} from "react";
import {Archive, Download, Edit3, Film, Grid2X2, Heart, Link2, List, Play, Search, Send, Tag, X} from "lucide-react";
import {Link} from "react-router-dom";

import {api, coverUrl, douyinImportedVideoUrl, thumbnailUrl, videoUrl, type DouyinImportedAsset, type GalleryOutput} from "../api";
import {ErrorNotice, formatDateTime} from "../components";
import {usePolling} from "../hooks";
import {contentMetaStore, type ContentAssetMeta} from "../workspaceStore";

type ViewMode = "grid" | "list";

const titleOf = (output: GalleryOutput) => output.name ?? output.symbol ?? `内容 ${output.output_id.slice(0, 8)}`;
const defaultMeta = (output: GalleryOutput): ContentAssetMeta => ({
  outputId: output.output_id,
  title: output.publish_title || `${titleOf(output)}：历史持有结果`,
  description: output.publish_subtitle || `基于真实历史行情生成的回测视频，展示 ${titleOf(output)} 的长期资产走势。`,
  topics: ["历史回测", output.market ?? "股票"].filter(Boolean),
  state: "ready",
  coverVariant: "portrait",
  updatedAt: new Date().toISOString(),
});

const formatReturn = (value: number | null) => value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;

export const ContentLibraryPage: React.FC = () => {
  const loader = useCallback(() => api<GalleryOutput[]>("/api/outputs"), []);
  const {data, error} = usePolling(loader, 5_000);
  const importsLoader = useCallback(() => api<DouyinImportedAsset[]>("/api/integrations/douyin/imports"), []);
  const {data: importedData, error: importedError} = usePolling(importsLoader, 5_000);
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState("all");
  const [state, setState] = useState("active");
  const [view, setView] = useState<ViewMode>("grid");
  const [selected, setSelected] = useState<GalleryOutput | null>(null);
  const [selectedImport, setSelectedImport] = useState<DouyinImportedAsset | null>(null);
  const [meta, setMeta] = useState<ContentAssetMeta | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const stored = contentMetaStore.all();
  const outputs = useMemo(() => (data ?? []).filter((output) => {
    const itemMeta = stored[output.output_id] ?? defaultMeta(output);
    const matchesQuery = `${output.name ?? ""}${output.symbol ?? ""}${itemMeta.title}`.toLowerCase().includes(query.toLowerCase());
    const matchesMarket = market === "all" || output.market === market;
    const matchesState = state === "all" || (state === "active" ? !["archived", "discarded"].includes(itemMeta.state) : itemMeta.state === state);
    return matchesQuery && matchesMarket && matchesState;
  }).sort((a, b) => b.created_at.localeCompare(a.created_at)), [data, query, market, state, stored]);
  const importedAssets = useMemo(() => (importedData ?? []).filter((item) => {
    if (market !== "all" || !["active", "all"].includes(state)) return false;
    return `${item.title ?? ""}${item.description ?? ""}${item.transcript ?? ""}`.toLowerCase().includes(query.toLowerCase());
  }), [importedData, market, query, state]);

  const openEditor = (output: GalleryOutput) => {
    setSelected(output);
    setMeta(contentMetaStore.get(output.output_id) ?? defaultMeta(output));
    setNotice(null);
  };
  const saveMeta = () => {
    if (!meta) return;
    contentMetaStore.upsert({...meta, updatedAt: new Date().toISOString()});
    setNotice("内容信息已保存，发布时可继续修改。 ");
  };
  const setLifecycle = (output: GalleryOutput, next: ContentAssetMeta["state"]) => {
    const current = contentMetaStore.get(output.output_id) ?? defaultMeta(output);
    contentMetaStore.upsert({...current, state: next, updatedAt: new Date().toISOString()});
    if (selected?.output_id === output.output_id) setMeta({...current, state: next, updatedAt: new Date().toISOString()});
    setNotice(next === "favorite" ? "已收藏内容" : next === "archived" ? "已归档内容" : "内容状态已更新");
  };

  return (
    <div className="page content-library-page">
      {error ? <ErrorNotice message={error} /> : null}
      {importedError ? <ErrorNotice message={importedError} /> : null}
      {notice ? <div className="notice info-notice">{notice}</div> : null}
      <header className="module-header"><div><span className="module-kicker">CONTENT ASSETS</span><h1>内容库</h1><p>集中查看成片、补充发布文案并管理内容生命周期。</p></div><div className="module-actions"><Link to="/workbench" className="button primary"><Film size={15}/> 生产新内容</Link></div></header>

      <section className="asset-toolbar">
        <label className="asset-search"><Search size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、股票名称或代码"/></label>
        <div className="asset-filters"><select value={market} onChange={(event) => setMarket(event.target.value)}><option value="all">全部市场</option><option value="CN">A股</option><option value="HK">港股</option><option value="US">美股</option><option value="CRYPTO">加密资产</option></select><select value={state} onChange={(event) => setState(event.target.value)}><option value="active">可用内容</option><option value="favorite">已收藏</option><option value="archived">已归档</option><option value="discarded">已丢弃</option><option value="all">全部状态</option></select></div>
        <span className="asset-count">{outputs.length + importedAssets.length} 条内容</span>
        <div className="view-switch"><button type="button" className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="网格视图"><Grid2X2 size={15}/></button><button type="button" className={view === "list" ? "active" : ""} onClick={() => setView("list")} aria-label="列表视图"><List size={16}/></button></div>
      </section>

      {importedAssets.length > 0 ? <section className="external-assets-section">
        <div className="external-assets-head"><div><span className="module-kicker">IMPORTED ASSETS</span><h2>链接导入素材</h2></div><Link to="/assets/douyin" className="button secondary"><Link2 size={14}/> 继续提取</Link></div>
        <div className={`asset-collection ${view}`}>
          {importedAssets.map((item) => <article key={item.source_id} className="asset-card external-asset-card">
            <button type="button" className="asset-preview" onClick={() => setSelectedImport(item)}><video muted preload="metadata" src={`${douyinImportedVideoUrl(item.source_id)}#t=0.1`}/><span className="asset-play"><Play size={15} fill="currentColor"/></span><em>{item.duration ? `${Math.round(item.duration)}s` : "视频"}</em></button>
            <div className="asset-card-copy"><div className="asset-card-title"><span><strong>{item.title || item.description || `抖音素材 ${item.source_id}`}</strong><small>{item.author?.nickname || "抖音导入"} · {item.language || "自动识别"}</small></span><b className="source-badge">抖音</b></div><p>{item.transcript || item.description || "已导入视频素材"}</p><div className="asset-card-meta"><span>{formatDateTime(item.imported_at)}</span><i>外部素材</i><i>已确认授权</i></div></div>
            <div className="asset-card-actions"><button type="button" onClick={() => setSelectedImport(item)}><Play size={14}/> 播放</button><a href={douyinImportedVideoUrl(item.source_id)} download={item.video_name || undefined}><Download size={14}/> 下载</a>{item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer"><Link2 size={14}/> 来源</a> : null}</div>
          </article>)}
        </div>
      </section> : null}

      <section className={`asset-collection ${view}`}>
        {outputs.map((output) => {
          const itemMeta = contentMetaStore.get(output.output_id) ?? defaultMeta(output);
          return <article key={output.output_id} className="asset-card">
            <button type="button" className="asset-preview" onClick={() => openEditor(output)}><img src={thumbnailUrl(output.output_id)} alt={itemMeta.title}/><span className="asset-play"><Play size={15} fill="currentColor"/></span><em>{output.duration_seconds ? `${Math.round(output.duration_seconds)}s` : "视频"}</em></button>
            <div className="asset-card-copy"><div className="asset-card-title"><span><strong>{itemMeta.title}</strong><small>{output.name} · {output.symbol}</small></span><b className={(output.total_return_pct ?? 0) >= 0 ? "positive" : "negative"}>{formatReturn(output.total_return_pct)}</b></div><p>{itemMeta.description}</p><div className="asset-card-meta"><span>{formatDateTime(output.created_at)}</span>{itemMeta.topics.slice(0, 2).map((topic) => <i key={topic}>{topic}</i>)}{output.published ? <i className="published">已发布</i> : null}</div></div>
            <div className="asset-card-actions"><button type="button" onClick={() => openEditor(output)}><Edit3 size={14}/> 编辑</button><button type="button" className={itemMeta.state === "favorite" ? "active" : ""} onClick={() => setLifecycle(output, itemMeta.state === "favorite" ? "ready" : "favorite")}><Heart size={14} fill={itemMeta.state === "favorite" ? "currentColor" : "none"}/> 收藏</button><Link to={`/publish?output=${output.output_id}`}><Send size={14}/> 发布</Link></div>
          </article>;
        })}
        {outputs.length === 0 && importedAssets.length === 0 ? <div className="asset-empty"><Film size={28}/><strong>没有符合筛选条件的内容</strong><span>更换筛选条件，或先运行一个内容工作流。</span></div> : null}
      </section>

      {selected && meta ? <div className="drawer-backdrop" onMouseDown={() => setSelected(null)}><aside className="content-drawer" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="module-kicker">CONTENT DETAILS</span><h2>编辑内容</h2></div><button type="button" className="icon-button" onClick={() => setSelected(null)} aria-label="关闭"><X size={18}/></button></header><video controls src={videoUrl(selected.output_id)} poster={thumbnailUrl(selected.output_id)}/><div className="drawer-body"><label className="field-label">发布标题<input value={meta.title} onChange={(event) => setMeta({...meta, title: event.target.value})} maxLength={60}/><small>{meta.title.length}/60</small></label><label className="field-label">内容简介<textarea rows={5} value={meta.description} onChange={(event) => setMeta({...meta, description: event.target.value})}/></label><label className="field-label">话题标签<div className="topic-editor"><Tag size={15}/><input value={meta.topics.join(" ")} onChange={(event) => setMeta({...meta, topics: event.target.value.split(/\s+/).filter(Boolean)})} placeholder="多个话题用空格分隔"/></div></label><div className="field-label"><span>封面版本</span><div className="cover-choice"><button type="button" className={meta.coverVariant === "portrait" ? "active" : ""} onClick={() => setMeta({...meta, coverVariant: "portrait"})}><img src={coverUrl(selected.output_id, "portrait")} alt="竖版封面"/><span>竖版 3:4</span></button><button type="button" className={meta.coverVariant === "landscape" ? "active" : ""} onClick={() => setMeta({...meta, coverVariant: "landscape"})}><img src={coverUrl(selected.output_id, "landscape")} alt="横版封面"/><span>横版 4:3</span></button></div></div><div className="lifecycle-row"><button type="button" className={meta.state === "favorite" ? "active" : ""} onClick={() => setMeta({...meta, state: "favorite"})}><Heart size={14}/> 收藏</button><button type="button" className={meta.state === "archived" ? "active" : ""} onClick={() => setMeta({...meta, state: "archived"})}><Archive size={14}/> 归档</button></div></div><footer><button type="button" className="button secondary" onClick={() => setSelected(null)}>取消</button><button type="button" className="button secondary" onClick={saveMeta}>保存修改</button><Link className="button primary" to={`/publish?output=${selected.output_id}`} onClick={saveMeta}><Send size={15}/> 去发布</Link></footer></aside></div> : null}
      {selectedImport ? <div className="drawer-backdrop" onMouseDown={() => setSelectedImport(null)}><aside className="content-drawer imported-content-drawer" onMouseDown={(event) => event.stopPropagation()}><header><div><span className="module-kicker">IMPORTED DETAILS</span><h2>{selectedImport.title || "抖音导入素材"}</h2></div><button type="button" className="icon-button" onClick={() => setSelectedImport(null)} aria-label="关闭"><X size={18}/></button></header><video controls autoPlay src={douyinImportedVideoUrl(selectedImport.source_id)}/><div className="drawer-body"><div className="imported-meta-row"><span>作者</span><strong>{selectedImport.author?.nickname || "未知"}</strong></div><div className="imported-meta-row"><span>导入时间</span><strong>{formatDateTime(selectedImport.imported_at)}</strong></div><label className="field-label">识别文案<textarea rows={12} readOnly value={selectedImport.transcript || "暂无识别文案"}/></label></div><footer><button type="button" className="button secondary" onClick={() => setSelectedImport(null)}>关闭</button><a className="button primary" href={douyinImportedVideoUrl(selectedImport.source_id)} download={selectedImport.video_name || undefined}><Download size={15}/> 下载视频</a></footer></aside></div> : null}
    </div>
  );
};

