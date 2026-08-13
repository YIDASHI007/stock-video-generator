import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Check, Copy, Download, MoreHorizontal, Play, Plus, Settings2, Upload, Workflow} from "lucide-react";
import {Link} from "react-router-dom";

import {api, type PipelinePolicy, type PipelineRun, type PipelineStatusResponse} from "../api";
import {ErrorNotice, InfoNotice, formatDateTime} from "../components";
import {usePolling} from "../hooks";
import {makeId, workflowStore, type WorkflowDefinition} from "../workspaceStore";

const baseWorkflow = (policy: PipelinePolicy, updatedAt: string): WorkflowDefinition => ({
  id: "stock-history-default",
  name: "股票历史回测视频",
  description: "自动选题、真实行情回测、脚本与视频渲染的完整生产链。",
  contentType: "股票回测",
  enabled: policy.enabled,
  createdAt: updatedAt,
  updatedAt,
  lastRunAt: null,
  policy,
});

const downloadJson = (workflow: WorkflowDefinition) => {
  const blob = new Blob([JSON.stringify(workflow, null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${workflow.name.replace(/[\\/:*?"<>|]/g, "-")}.workflow.json`;
  anchor.click();
  URL.revokeObjectURL(url);
};

export const WorkflowsPage: React.FC = () => {
  const statusLoader = useCallback(() => api<PipelineStatusResponse>("/api/pipeline/status"), []);
  const runsLoader = useCallback(() => api<PipelineRun[]>("/api/pipeline/runs?filter=all"), []);
  const {data: status, error, refresh} = usePolling(statusLoader, 5_000);
  const {data: runs} = usePolling(runsLoader, 5_000);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>(() => workflowStore.list());
  const [selectedId, setSelectedId] = useState<string>(workflows[0]?.id ?? "stock-history-default");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!status) return;
    const stored = workflowStore.list();
    const runsLatest = (runs ?? [])[0]?.updated_at ?? null;
    const current = baseWorkflow(status.policy, new Date().toISOString());
    current.lastRunAt = runsLatest;
    const existing = stored.find((item) => item.id === current.id);
    const merged = existing
      ? stored.map((item) => item.id === current.id ? {...item, enabled: status.enabled, policy: status.policy, lastRunAt: runsLatest} : item)
      : [current, ...stored];
    setWorkflows(workflowStore.save(merged));
    setSelectedId((id) => id || current.id);
  }, [status, runs]);

  const selected = useMemo(() => workflows.find((item) => item.id === selectedId) ?? workflows[0] ?? null, [workflows, selectedId]);
  const updateSelected = (patch: Partial<WorkflowDefinition>) => {
    if (!selected) return;
    const next = {...selected, ...patch, updatedAt: new Date().toISOString()};
    setWorkflows(workflowStore.upsert(next));
  };
  const copyWorkflow = (source: WorkflowDefinition) => {
    const now = new Date().toISOString();
    const clone: WorkflowDefinition = {...source, id: makeId("workflow"), name: `${source.name} 副本`, enabled: false, createdAt: now, updatedAt: now, lastRunAt: null, policy: {...source.policy, enabled: false}};
    setWorkflows(workflowStore.upsert(clone));
    setSelectedId(clone.id);
    setNotice("工作流副本已创建，可在右侧调整后应用。 ");
  };
  const applySelected = async () => {
    if (!selected || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const saved = await api<PipelinePolicy>("/api/pipeline/policy", {method: "PUT", body: JSON.stringify({...selected.policy, enabled: selected.enabled})});
      updateSelected({policy: saved, enabled: saved.enabled});
      refresh();
      setNotice(`“${selected.name}”已应用到生产控制台。`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };
  const runNow = async () => {
    if (!selected || busy) return;
    setBusy(true);
    try {
      await api<PipelinePolicy>("/api/pipeline/policy", {method: "PUT", body: JSON.stringify({...selected.policy, enabled: selected.enabled})});
      await api("/api/pipeline/run-once", {method: "POST"});
      setNotice("已启动一次生产任务，可前往运行中心查看进度。");
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };
  const importWorkflow = async (file: File | null) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as WorkflowDefinition;
      if (!parsed.name || !parsed.policy || typeof parsed.policy.amount !== "number") throw new Error("文件不包含有效的工作流配置。");
      const now = new Date().toISOString();
      const imported = {...parsed, id: makeId("workflow"), enabled: false, createdAt: now, updatedAt: now, lastRunAt: null, policy: {...parsed.policy, enabled: false}};
      setWorkflows(workflowStore.upsert(imported));
      setSelectedId(imported.id);
      setNotice(`已导入工作流“${imported.name}”。`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "无法读取工作流文件");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="page workflows-page">
      {error ? <ErrorNotice message={error} /> : null}
      {notice ? <InfoNotice message={notice}/> : null}
      <header className="module-header"><div><span className="module-kicker">CONTENT PRODUCTION</span><h1>工作流</h1><p>把稳定的内容生产方式保存为可复制、可导入和可重复运行的配置。</p></div><div className="module-actions"><input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={(event) => void importWorkflow(event.target.files?.[0] ?? null)}/><button type="button" className="button secondary" onClick={() => fileRef.current?.click()}><Upload size={15}/> 导入</button>{selected ? <button type="button" className="button secondary" onClick={() => downloadJson(selected)}><Download size={15}/> 导出</button> : null}<button type="button" className="button primary" onClick={() => selected && copyWorkflow(selected)}><Plus size={15}/> 复制新建</button></div></header>

      <div className="workflow-layout">
        <section className="workflow-list-panel">
          <div className="list-panel-head"><div><h2>我的工作流</h2><span>{workflows.length} 个配置</span></div><Link to="/jobs">查看运行中心</Link></div>
          <div className="workflow-list">
            {workflows.map((item) => <button key={item.id} type="button" className={selectedId === item.id ? "active" : ""} onClick={() => setSelectedId(item.id)}><span className="workflow-list-icon"><Workflow size={18}/></span><span className="workflow-list-copy"><strong>{item.name}</strong><small>{item.description}</small><em>{item.contentType} · {item.lastRunAt ? `最近 ${formatDateTime(item.lastRunAt)}` : "尚未运行"}</em></span><span className={`workflow-state ${item.enabled ? "on" : "off"}`}>{item.enabled ? "已启用" : "草稿"}</span></button>)}
          </div>
        </section>

        {selected ? <aside className="workflow-editor">
          <div className="editor-head"><div><span className="editor-icon"><Settings2 size={18}/></span><div><span>当前配置</span><h2>{selected.name}</h2></div></div><button type="button" className="icon-button" aria-label="更多"><MoreHorizontal size={18}/></button></div>
          <label className="field-label">工作流名称<input value={selected.name} onChange={(event) => updateSelected({name: event.target.value})}/></label>
          <label className="field-label">用途说明<textarea rows={3} value={selected.description} onChange={(event) => updateSelected({description: event.target.value})}/></label>
          <div className="editor-grid"><label className="field-label">初始金额<input type="number" min="1" value={selected.policy.amount} onChange={(event) => updateSelected({policy: {...selected.policy, amount: Number(event.target.value)}})}/></label><label className="field-label">目标时长（秒）<input type="number" min="15" max="180" value={selected.policy.target_duration} onChange={(event) => updateSelected({policy: {...selected.policy, target_duration: Number(event.target.value)}})}/></label><label className="field-label">每日产量<input type="number" min="0" placeholder="0 表示不限" value={selected.policy.daily_quota ?? 0} onChange={(event) => updateSelected({policy: {...selected.policy, daily_quota: Number(event.target.value) || null}})}/></label><label className="field-label">选题池水位<input type="number" min="1" value={selected.policy.pool_target} onChange={(event) => updateSelected({policy: {...selected.policy, pool_target: Number(event.target.value)}})}/></label></div>
          <div className="editor-section"><span>生产市场</span><div className="choice-row">{(["CN", "HK", "US", "CRYPTO"] as const).map((market) => <button type="button" key={market} className={selected.policy.markets.includes(market) ? "active" : ""} onClick={() => {const current = selected.policy.markets; const next = current.includes(market) ? current.filter((item) => item !== market) : [...current, market]; if (next.length) updateSelected({policy: {...selected.policy, markets: next}});}}>{market === "CN" ? "A股" : market === "HK" ? "港股" : market === "US" ? "美股" : "加密资产"}{selected.policy.markets.includes(market) ? <Check size={13}/> : null}</button>)}</div></div>
          <label className="switch-field"><span><strong>启用自动生产</strong><small>应用后后台会按该配置持续补充内容</small></span><input type="checkbox" checked={selected.enabled} onChange={(event) => updateSelected({enabled: event.target.checked, policy: {...selected.policy, enabled: event.target.checked}})}/><i/></label>
          <div className="editor-foot"><button type="button" className="button secondary" onClick={() => copyWorkflow(selected)}><Copy size={15}/> 创建副本</button><button type="button" className="button secondary" onClick={() => void applySelected()} disabled={busy}><Check size={15}/> 应用配置</button><button type="button" className="button primary" onClick={() => void runNow()} disabled={busy}><Play size={15} fill="currentColor"/> 运行一次</button></div>
        </aside> : null}
      </div>
    </div>
  );
};
