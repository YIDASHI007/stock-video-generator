import React, {useCallback} from "react";
import {FileAudio, FileImage, FileJson2, FolderOpen, HardDrive, Music2} from "lucide-react";
import {api, bgmUrl, coverUrl, type BgmFileInfo, type GalleryOutput} from "../api";
import {ErrorNotice} from "../components";
import {usePolling} from "../hooks";

export const MaterialsPage: React.FC = () => {
  const outputsLoader = useCallback(() => api<GalleryOutput[]>("/api/outputs"), []);
  const bgmLoader = useCallback(() => api<BgmFileInfo[]>("/api/settings/bgm/list"), []);
  const {data: outputs, error} = usePolling(outputsLoader, 10_000);
  const {data: bgm} = usePolling(bgmLoader, 30_000);
  const covers = (outputs ?? []).flatMap((item) => [{id: `${item.output_id}-p`, output: item, variant: "portrait" as const}, {id: `${item.output_id}-l`, output: item, variant: "landscape" as const}]);
  return <div className="page materials-page">{error ? <ErrorNotice message={error}/> : null}<header className="module-header"><div><span className="module-kicker">ASSET STORAGE</span><h1>素材库</h1><p>查看工作流产生的封面、背景音乐和结构化源文件。</p></div></header><section className="material-summary"><article><HardDrive size={19}/><span><strong>{outputs?.length ?? 0}</strong><small>视频源文件</small></span></article><article><FileImage size={19}/><span><strong>{covers.length}</strong><small>横竖封面</small></span></article><article><Music2 size={19}/><span><strong>{bgm?.length ?? 0}</strong><small>背景音乐</small></span></article><article><FileJson2 size={19}/><span><strong>{outputs?.length ?? 0}</strong><small>回测数据包</small></span></article></section><section className="material-section"><div className="section-head"><h2>封面素材</h2><span>由成片工作流自动生成</span></div><div className="cover-material-grid">{covers.slice(0, 20).map((item) => <article key={item.id}><img src={coverUrl(item.output.output_id, item.variant)} alt={`${item.output.name ?? item.output.symbol}封面`}/><span><strong>{item.output.name ?? item.output.symbol}</strong><small>{item.variant === "portrait" ? "竖版 3:4" : "横版 4:3"}</small></span></article>)}</div></section><section className="material-section"><div className="section-head"><h2>音频素材</h2><span>{bgm?.length ?? 0} 个文件</span></div><div className="audio-material-list">{(bgm ?? []).map((item) => <article key={item.file}><FileAudio size={18}/><span><strong>{item.file}</strong><small>{(item.size_bytes / 1024 / 1024).toFixed(2)} MB</small></span><audio controls preload="none" src={bgmUrl(item.file)}/></article>)}</div></section></div>;
};
