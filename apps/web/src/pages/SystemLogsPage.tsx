import React, {useCallback, useState} from "react";
import {Download, RefreshCw, TerminalSquare} from "lucide-react";
import {api} from "../api";
import {ErrorNotice} from "../components";
import {usePolling} from "../hooks";

type LogResponse = {kind: string; path: string; lines: string[]};

export const SystemLogsPage: React.FC = () => {
  const [kind, setKind] = useState("error");
  const loader = useCallback(() => api<LogResponse>(`/api/system/logs?kind=${kind}&limit=500`), [kind]);
  const {data, error, refresh} = usePolling(loader, 10_000);
  const download = () => {const blob = new Blob([(data?.lines ?? []).join("\n")], {type: "text/plain;charset=utf-8"}); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `${kind}.log`; a.click(); URL.revokeObjectURL(url);};
  return <div className="page system-logs-page">{error ? <ErrorNotice message={error}/> : null}<header className="module-header"><div><span className="module-kicker">SYSTEM DIAGNOSTICS</span><h1>运行日志</h1><p>查看经过敏感信息脱敏的本机服务日志，辅助定位生产和发布异常。</p></div><div className="module-actions"><button type="button" className="button secondary" onClick={refresh}><RefreshCw size={15}/> 刷新</button><button type="button" className="button secondary" onClick={download}><Download size={15}/> 下载</button></div></header><section className="log-toolbar"><div><button type="button" className={kind === "error" ? "active" : ""} onClick={() => setKind("error")}>错误日志</button><button type="button" className={kind === "app" ? "active" : ""} onClick={() => setKind("app")}>应用日志</button></div><span>{data?.path ?? "正在读取日志路径"}</span></section><section className="log-console"><header><TerminalSquare size={15}/><span>{kind}.log</span><em>{data?.lines.length ?? 0} 行</em></header><pre>{data?.lines.length ? data.lines.join("\n") : "当前日志文件为空。"}</pre></section></div>;
};

