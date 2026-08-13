import React, {useCallback, useEffect, useState} from "react";
import {BrainCircuit, CheckCircle2, KeyRound, LoaderCircle, Save} from "lucide-react";

import {
  API_BASE,
  angleLabels,
  api,
  bgmUrl,
  clearBgm,
  previewTopics,
  uploadBgm,
  type MarketCode,
  type AiModelSettings,
  type PipelinePolicy,
  type PipelineStatusResponse,
  type TopicDirective,
  type TopicPreviewResult,
} from "../api";
import {ErrorNotice, SuccessNotice} from "../components";
import {usePolling} from "../hooks";

type Component = {
  name: string;
  available: boolean;
  message: string;
  details?: Record<string, unknown>;
};

type Provider = {
  name: string;
  available: boolean;
  checked_at: string;
  latency_ms: number | null;
  message: string;
};

const componentLabels: Record<string, string> = {
  database: "SQLite 数据库",
  node: "Node.js",
  remotion: "Remotion",
  ffmpeg: "FFmpeg",
  disk: "磁盘空间",
  tts: "TTS 配音",
};

const marketLabels: Record<MarketCode, string> = {
  CN: "A 股",
  HK: "港股",
  US: "美股",
  CRYPTO: "加密资产",
};

const angleKeys = ["surge", "crash", "rollercoaster", "compound"] as const;

const voiceOptions = [
  ["zh-CN-XiaoxiaoNeural", "晓晓（女声，活泼）"],
  ["zh-CN-XiaoyiNeural", "晓伊（女声，清澈）"],
  ["zh-CN-YunxiNeural", "云希（男声，年轻）"],
  ["zh-CN-YunjianNeural", "云健（男声，沉稳）"],
  ["zh-CN-YunyangNeural", "云扬（男声，新闻腔）"],
];

const AiModelForm: React.FC = () => {
  const [settings, setSettings] = useState<AiModelSettings | null>(null);
  const [draft, setDraft] = useState({enabled: true, provider: "deepseek", model: "deepseek-v4-flash", api_key: "", request_timeout_seconds: 300});
  const [busy, setBusy] = useState<"save" | "test" | "">("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api<AiModelSettings>("/api/settings/ai-model").then((value) => {
      setSettings(value);
      setDraft({enabled: value.enabled, provider: value.provider, model: value.model, api_key: "", request_timeout_seconds: value.request_timeout_seconds});
    }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  const run = async (kind: "save" | "test") => {
    setBusy(kind); setNotice(null); setError(null);
    try {
      if (kind === "save") {
        const value = await api<AiModelSettings>("/api/settings/ai-model", {method: "PUT", body: JSON.stringify({...draft, api_key: draft.api_key || null})});
        setSettings(value); setDraft((current) => ({...current, api_key: ""})); setNotice("DeepSeek 配置已加密保存在当前电脑。");
      } else {
        const value = await api<{model: string; model_available: boolean}>("/api/settings/ai-model/test", {method: "POST"});
        setNotice(value.model_available ? `连接成功，模型 ${value.model} 可用。` : `已连接 DeepSeek，但账号暂未返回模型 ${value.model}。`);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  };

  if (!settings) return error ? <ErrorNotice message={error}/> : <p className="quiet-line">正在读取模型配置…</p>;
  return <div className="ai-model-settings">
    {error ? <ErrorNotice message={error}/> : null}{notice ? <SuccessNotice message={notice}/> : null}
    <div className="ai-model-intro"><span><BrainCircuit size={20}/></span><div><strong>DeepSeek 文案分析</strong><p>读取已提取逐字稿，建立可执行的写作方法论并生成 Skill。密钥只保存在当前电脑。</p></div><em className={settings.api_key_configured ? "ready" : ""}>{settings.api_key_configured ? <><CheckCircle2 size={13}/>已配置</> : <><KeyRound size={13}/>未配置</>}</em></div>
    <div className="field-grid ai-model-fields">
      <label>服务商<select value={draft.provider} disabled><option value="deepseek">DeepSeek</option></select></label>
      <label>分析模型<select value={draft.model} onChange={(event) => setDraft({...draft, model: event.target.value})}>{settings.available_models.map((model) => <option key={model} value={model}>{model === "deepseek-v4-pro" ? "DeepSeek V4 Pro（质量优先）" : "DeepSeek V4 Flash（推荐）"}</option>)}</select></label>
      <label className="ai-key-field">API Key<input type="password" autoComplete="new-password" value={draft.api_key} onChange={(event) => setDraft({...draft, api_key: event.target.value})} placeholder={settings.api_key_hint ?? "粘贴 DeepSeek API Key"}/><small>留空保存会继续使用现有密钥，不会传给浏览器或写入 Skill。</small></label>
      <label>分析超时（秒）<input type="number" min={30} max={900} value={draft.request_timeout_seconds} onChange={(event) => setDraft({...draft, request_timeout_seconds: Number(event.target.value)})}/></label>
    </div>
    <label className="checkbox-label ai-enable"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({...draft, enabled: event.target.checked})}/>启用 AI 画像与写作方法论</label>
    <div className="form-actions"><button className="button primary" type="button" disabled={Boolean(busy)} onClick={() => void run("save")}>{busy === "save" ? <LoaderCircle className="spin" size={14}/> : <Save size={14}/>}保存模型配置</button><button className="button secondary" type="button" disabled={Boolean(busy) || !settings.api_key_configured} onClick={() => void run("test")}>{busy === "test" ? <LoaderCircle className="spin" size={14}/> : <BrainCircuit size={14}/>}测试连接</button></div>
  </div>;
};

/* ---------------- 自动生产策略表单 ---------------- */

const PolicyForm: React.FC = () => {
  const loader = useCallback(
    () => api<PipelineStatusResponse>("/api/pipeline/status"),
    [],
  );
  const {data: status, error, refresh} = usePolling(loader, 10_000);
  const [draft, setDraft] = useState<PipelinePolicy | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [replenishing, setReplenishing] = useState(false);
  const [symbolText, setSymbolText] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<TopicPreviewResult | null>(null);
  const [bgmBusy, setBgmBusy] = useState(false);

  useEffect(() => {
    if (status && !draft) {
      setDraft(status.policy);
      setSymbolText(status.policy.topic_directive.prefer_symbols.join(", "));
    }
  }, [status, draft]);

  const update = (patch: Partial<PipelinePolicy>) =>
    setDraft((current) => (current ? {...current, ...patch} : current));

  const updateDirective = (patch: Partial<TopicDirective>) =>
    setDraft((current) =>
      current
        ? {...current, topic_directive: {...current.topic_directive, ...patch}}
        : current,
    );

  const toggleMarket = (market: MarketCode) =>
    setDraft((current) => {
      if (!current) return current;
      const markets = current.markets.includes(market)
        ? current.markets.filter((item) => item !== market)
        : [...current.markets, market];
      return {...current, markets};
    });

  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      await api<PipelinePolicy>("/api/pipeline/policy", {
        method: "PUT",
        body: JSON.stringify({...draft, topic_directive: directiveFromForm()}),
      });
      setNotice("策略已保存，自动生产按新策略执行。");
      refresh();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const replenish = async () => {
    if (replenishing) return;
    setReplenishing(true);
    setFormError(null);
    setNotice(null);
    try {
      const report = await api<{
        added: Array<{symbol: string}>;
        errors: Array<{symbol: string; reason: string}>;
        pool_size: number;
      }>("/api/pipeline/topics/replenish", {method: "POST"});
      const parts = [`新增 ${report.added.length} 条选题，当前水位 ${report.pool_size}`];
      if (report.errors.length > 0) {
        parts.push(`${report.errors.length} 只股票拉取失败（详见日志）`);
      }
      setNotice(parts.join("，") + "。");
      refresh();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setReplenishing(false);
    }
  };

  /** 表单当前生效的选题偏好（白名单从文本框解析）。 */
  const directiveFromForm = (): TopicDirective => {
    const directive = draft?.topic_directive ?? {
      surge_min_pct: null,
      crash_max_pct: null,
      prefer_angles: [],
      prefer_symbols: [],
    };
    return {
      ...directive,
      prefer_symbols: symbolText
        .split(/[,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    };
  };

  const toggleAngle = (angle: string) =>
    setDraft((current) => {
      if (!current) return current;
      const prefer = current.topic_directive.prefer_angles;
      const prefer_angles = prefer.includes(angle)
        ? prefer.filter((item) => item !== angle)
        : [...prefer, angle];
      return {
        ...current,
        topic_directive: {...current.topic_directive, prefer_angles},
      };
    });

  const runPreview = async () => {
    if (previewing) return;
    setPreviewing(true);
    setFormError(null);
    try {
      setPreview(await previewTopics(directiveFromForm()));
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPreviewing(false);
    }
  };

  const onBgmSelected = async (file: File | null) => {
    if (!file || bgmBusy) return;
    setBgmBusy(true);
    setFormError(null);
    setNotice(null);
    try {
      const policy = await uploadBgm(file);
      update({bgm_file: policy.bgm_file});
      setNotice(`背景音乐已更新：${policy.bgm_file}，之后的成片自动混入。`);
      refresh();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBgmBusy(false);
    }
  };

  const onBgmClear = async () => {
    if (bgmBusy) return;
    setBgmBusy(true);
    setFormError(null);
    setNotice(null);
    try {
      await clearBgm();
      update({bgm_file: null});
      setNotice("背景音乐已清除，之后的成片不再混入。");
      refresh();
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBgmBusy(false);
    }
  };

  if (!draft) {
    return error ? <ErrorNotice message={error} /> : <p className="quiet-line">正在读取策略…</p>;
  }

  return (
    <>
      {formError ? <ErrorNotice message={formError} /> : null}
      {notice ? <SuccessNotice message={notice}/> : null}
      <div className="field-grid">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => update({enabled: event.target.checked})}
          />
          启用自动生产（按每日配额无人值守出片）
        </label>
        <label>
          每日配额（留空不限）
          <input
            type="number"
            min={1}
            placeholder="不限"
            value={draft.daily_quota ?? ""}
            onChange={(event) => {
              const raw = event.target.value.trim();
              update({
                daily_quota: raw === "" ? null : Math.max(1, Number(raw) || 1),
              });
            }}
          />
        </label>
        <label>
          目标时长（秒）
          <input
            type="number"
            min={15}
            max={180}
            step={5}
            value={draft.target_duration}
            onChange={(event) =>
              update({target_duration: Number(event.target.value) || 60})
            }
          />
        </label>
        <label>
          每条本金
          <input
            type="number"
            min={1000}
            step={10000}
            value={draft.amount}
            onChange={(event) =>
              update({amount: Number(event.target.value) || 0})
            }
          />
        </label>
        <label>
          选题池水位
          <input
            type="number"
            min={1}
            max={50}
            value={draft.pool_target}
            onChange={(event) =>
              update({pool_target: Number(event.target.value) || 1})
            }
          />
        </label>
        <label className="checkbox-label" style={{alignSelf: "end"}}>
          <input
            type="checkbox"
            checked={draft.voiceover_enabled}
            onChange={(event) => update({voiceover_enabled: event.target.checked})}
          />
          生成配音（关闭后匀速播放、无旁白）
        </label>
        <label>
          配音音色
          <select
            value={draft.voice}
            disabled={!draft.voiceover_enabled}
            onChange={(event) => update({voice: event.target.value})}
          >
            {voiceOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="field-grid">
        <span className="field-label">市场范围</span>
        <div className="checkbox-row">
          {(Object.keys(marketLabels) as MarketCode[]).map((market) => (
            <label key={market} className="checkbox-label">
              <input
                type="checkbox"
                checked={draft.markets.includes(market)}
                onChange={() => toggleMarket(market)}
              />
              {marketLabels[market]}
            </label>
          ))}
        </div>
      </div>
      <div className="field-grid">
        <span className="field-label">戏剧性配比（权重）</span>
        <div className="angle-grid">
          {angleKeys.map((angle) => (
            <label key={angle}>
              {angleLabels[angle]}
              <input
                type="number"
                min={0}
                max={100}
                value={draft.angle_weights[angle] ?? 0}
                onChange={(event) =>
                  update({
                    angle_weights: {
                      ...draft.angle_weights,
                      [angle]: Number(event.target.value) || 0,
                    },
                  })
                }
              />
            </label>
          ))}
        </div>
      </div>
      <div className="field-grid">
        <span className="field-label">
          选题偏好（全部留空 = 均衡随机；涨幅/跌幅同时填为“或”关系）
        </span>
        <div className="field-grid">
          <label>
            涨幅 ≥（%）
            <input
              type="number"
              min={0}
              step={10}
              placeholder="如 100"
              value={draft.topic_directive.surge_min_pct ?? ""}
              onChange={(event) =>
                updateDirective({
                  surge_min_pct:
                    event.target.value === "" ? null : Number(event.target.value),
                })
              }
            />
          </label>
          <label>
            跌幅 ≥（%，填正数）
            <input
              type="number"
              min={0}
              max={100}
              step={5}
              placeholder="如 80"
              value={
                draft.topic_directive.crash_max_pct === null
                  ? ""
                  : -draft.topic_directive.crash_max_pct
              }
              onChange={(event) =>
                updateDirective({
                  crash_max_pct:
                    event.target.value === ""
                      ? null
                      : -Math.abs(Number(event.target.value)),
                })
              }
            />
          </label>
        </div>
        <div className="checkbox-row">
          {angleKeys.map((angle) => (
            <label key={angle} className="checkbox-label">
              <input
                type="checkbox"
                checked={draft.topic_directive.prefer_angles.includes(angle)}
                onChange={() => toggleAngle(angle)}
              />
              {angleLabels[angle]}
            </label>
          ))}
          <span className="num muted">（全不勾 = 四类均衡）</span>
        </div>
        <label>
          优先股票白名单（代码逗号分隔，可留空）
          <input
            type="text"
            placeholder="如 FFIE, NVDA, 600519.SH"
            value={symbolText}
            onChange={(event) => setSymbolText(event.target.value)}
          />
        </label>
        <div className="form-actions">
          <button
            type="button"
            className="button secondary"
            onClick={() => void runPreview()}
            disabled={previewing}
            title="按当前偏好实时拉取行情试算命中数，不写入选题池"
          >
            {previewing ? "试算中（拉取真实行情）…" : "预览候选命中"}
          </button>
          {preview ? (
            <span className="num muted">
              命中 {preview.count} 只
              {preview.fetch_errors > 0 ? ` · ${preview.fetch_errors} 只拉取失败` : ""}
              {preview.matched.length > 0
                ? `：${preview.matched
                    .slice(0, 5)
                    .map(
                      (item) =>
                        `${item.name} ${item.forward_return_pct > 0 ? "+" : ""}${item.forward_return_pct}%`,
                    )
                    .join("、")}${preview.count > 5 ? " …" : ""}`
                : ""}
            </span>
          ) : null}
        </div>
      </div>
      <div className="field-grid">
        <span className="field-label">
          背景音乐（渲染时自动混入，音量低于人声，结尾 2 秒淡出）
        </span>
        <div className="form-actions">
          <label className="button secondary" style={{cursor: "pointer"}}>
            {bgmBusy ? "处理中…" : "上传音乐（mp3/wav/m4a/ogg ≤20MB）"}
            <input
              type="file"
              accept=".mp3,.wav,.m4a,.ogg,audio/*"
              style={{display: "none"}}
              disabled={bgmBusy}
              onChange={(event) => {
                void onBgmSelected(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
          </label>
          {draft.bgm_file ? (
            <>
              <span className="num muted">当前：{draft.bgm_file}</span>
              <audio controls preload="none" src={bgmUrl()} style={{height: 32}} />
              <button
                type="button"
                className="button secondary"
                onClick={() => void onBgmClear()}
                disabled={bgmBusy}
              >
                清除
              </button>
            </>
          ) : (
            <span className="num muted">未设置，成片无背景音乐。</span>
          )}
        </div>
      </div>
      <div className="form-actions">
        <button
          type="button"
          className="button primary"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? "保存中…" : "保存策略"}
        </button>
        <button
          type="button"
          className="button secondary"
          onClick={() => void replenish()}
          disabled={replenishing}
          title="立即按当前策略从股票池补充选题到目标水位"
        >
          {replenishing ? "补充中（需要拉取真实行情）…" : "立即补充选题"}
        </button>
        <span className="num muted">
          选题池当前水位 {status?.pool_size ?? "—"} / {draft.pool_target}
        </span>
      </div>
    </>
  );
};

export const SettingsPage: React.FC = () => {
  const loader = useCallback(async () => {
    const [system, providers] = await Promise.all([
      api<{status: string; components: Component[]}>("/health"),
      api<Provider[]>("/api/providers/health"),
    ]);
    return {system, providers};
  }, []);
  const {data, error, loading} = usePolling(loader, 30_000);

  return (
    <div className="page settings-page">
      {error ? <ErrorNotice message={error} /> : null}
      {loading ? <p className="quiet-line">正在执行真实健康检查…</p> : null}

      {data ? (
        <>
          <section className="dash-section">
            <div className="section-head">
              <h2>数据源</h2>
              <span className="section-hint">真实小范围请求 · 30 秒刷新</span>
            </div>
            <div className="health-grid">
              {data.providers.map((item) => (
                <article
                  key={item.name}
                  className={`health-card ${item.available ? "ok" : "bad"}`}
                >
                  <div className="health-card-head">
                    <strong>{item.name}</strong>
                    <span className={`badge ${item.available ? "badge-ok" : "badge-bad"}`}>
                      {item.available ? "正常" : "异常"}
                    </span>
                  </div>
                  <p>{item.message}</p>
                  <small className="num muted">
                    {item.latency_ms === null
                      ? "无延迟数据"
                      : `延迟 ${Math.round(item.latency_ms)} ms`}
                  </small>
                </article>
              ))}
            </div>
          </section>

          <section className="dash-section">
            <div className="section-head">
              <h2>运行环境</h2>
              <span className="section-hint">
                整体状态：{data.system.status === "ok" ? "正常" : data.system.status}
              </span>
            </div>
            <div className="health-grid">
              {data.system.components.map((item) => (
                <article
                  key={item.name}
                  className={`health-card ${item.available ? "ok" : "bad"}`}
                >
                  <div className="health-card-head">
                    <strong>{componentLabels[item.name] ?? item.name}</strong>
                    <span className={`badge ${item.available ? "badge-ok" : "badge-bad"}`}>
                      {item.available ? "正常" : "异常"}
                    </span>
                  </div>
                  <p>{item.message}</p>
                </article>
              ))}
            </div>
          </section>
        </>
      ) : null}

      <section className="dash-section">
        <div className="section-head">
          <h2>AI 模型</h2>
          <span className="section-hint">本机加密密钥 · 用于画像、方法论与 Skill</span>
        </div>
        <AiModelForm />
      </section>

      <section className="dash-section">
        <div className="section-head">
          <h2>系统信息</h2>
        </div>
        <dl className="detail-list settings-info">
          <div>
            <dt>API 地址</dt>
            <dd>{API_BASE}</dd>
          </div>
          <div>
            <dt>健康检查接口</dt>
            <dd>/health · /api/providers/health</dd>
          </div>
          <div>
            <dt>数据刷新</dt>
            <dd>驾驶舱与任务中心每 3 秒轮询，本页每 30 秒轮询</dd>
          </div>
        </dl>
      </section>

      <section className="dash-section">
        <div className="section-head">
          <h2>自动生产策略</h2>
          <span className="section-hint">真实行情选题 · 确定性评分 · 失败自动搁浅</span>
        </div>
        <PolicyForm />
      </section>
    </div>
  );
};

const SettingsHeader: React.FC<{
  kicker: string;
  title: string;
  description: string;
}> = ({kicker, title, description}) => (
  <header className="module-header settings-header">
    <div>
      <span className="module-kicker">{kicker}</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </div>
  </header>
);

const HealthCard: React.FC<{
  name: string;
  available: boolean;
  message: string;
  meta?: string;
}> = ({name, available, message, meta}) => (
  <article className={`health-card ${available ? "ok" : "bad"}`}>
    <div className="health-card-head">
      <strong>{name}</strong>
      <span className={`badge ${available ? "badge-ok" : "badge-bad"}`}>
        {available ? "正常" : "异常"}
      </span>
    </div>
    <p>{message}</p>
    {meta ? <small className="num muted">{meta}</small> : null}
  </article>
);

export const DataSourcesSettingsPage: React.FC = () => {
  const loader = useCallback(() => api<Provider[]>("/api/providers/health"), []);
  const {data, error, loading, refresh} = usePolling(loader, 30_000);
  const healthy = data?.filter((item) => item.available).length ?? 0;

  return (
    <div className="page settings-page">
      <SettingsHeader
        kicker="DATA CONNECTIONS"
        title="数据源"
        description="查看行情连接的实时可用性与响应速度，异常会保留真实原因。"
      />
      {error ? <ErrorNotice message={error} /> : null}
      <section className="settings-summary-bar">
        <span><strong>{data?.length ?? 0}</strong> 个连接</span>
        <span><strong>{healthy}</strong> 个可用</span>
        <button type="button" className="button secondary" onClick={refresh}>重新检查</button>
      </section>
      <section className="dash-section">
        <div className="section-head">
          <h2>行情数据源</h2>
          <span className="section-hint">真实小范围请求 · 每 30 秒自动检查</span>
        </div>
        {loading && !data ? <p className="quiet-line">正在检查数据源…</p> : null}
        {data ? <div className="health-grid">
          {data.map((item) => (
            <HealthCard
              key={item.name}
              name={item.name}
              available={item.available}
              message={item.message}
              meta={item.latency_ms === null ? "暂无延迟数据" : `响应 ${Math.round(item.latency_ms)} ms`}
            />
          ))}
        </div> : null}
      </section>
    </div>
  );
};

export const AiModelSettingsPage: React.FC = () => (
  <div className="page settings-page settings-form-page">
    <SettingsHeader
      kicker="AI PROVIDERS"
      title="AI 模型"
      description="配置文案分析使用的模型、API Key 与请求超时，并在保存后验证连接。"
    />
    <section className="dash-section settings-focus-card">
      <div className="section-head">
        <h2>模型连接</h2>
        <span className="section-hint">密钥仅在当前电脑加密保存</span>
      </div>
      <AiModelForm />
    </section>
  </div>
);

export const SystemConfigPage: React.FC = () => {
  const loader = useCallback(
    () => api<{status: string; components: Component[]}>("/health"),
    [],
  );
  const {data, error, loading, refresh} = usePolling(loader, 30_000);
  const healthy = data?.components.filter((item) => item.available).length ?? 0;

  return (
    <div className="page settings-page">
      <SettingsHeader
        kicker="LOCAL RUNTIME"
        title="系统配置"
        description="查看本机运行依赖、服务地址和各模块的健康状态。"
      />
      {error ? <ErrorNotice message={error} /> : null}
      <section className="settings-summary-bar">
        <span><strong>{healthy}</strong> / {data?.components.length ?? 0} 个组件正常</span>
        <span className={data?.status === "ok" ? "status-ok" : "status-bad"}>
          整体状态：{data?.status === "ok" ? "正常" : data?.status ?? "检查中"}
        </span>
        <button type="button" className="button secondary" onClick={refresh}>刷新状态</button>
      </section>
      <section className="dash-section">
        <div className="section-head">
          <h2>运行环境</h2>
          <span className="section-hint">数据库、渲染、配音与磁盘</span>
        </div>
        {loading && !data ? <p className="quiet-line">正在检查运行环境…</p> : null}
        {data ? <div className="health-grid">
          {data.components.map((item) => (
            <HealthCard
              key={item.name}
              name={componentLabels[item.name] ?? item.name}
              available={item.available}
              message={item.message}
            />
          ))}
        </div> : null}
      </section>
      <section className="dash-section">
        <div className="section-head"><h2>服务信息</h2></div>
        <dl className="detail-list settings-info">
          <div><dt>API 地址</dt><dd>{API_BASE}</dd></div>
          <div><dt>健康检查</dt><dd>/health · /api/providers/health</dd></div>
          <div><dt>状态刷新</dt><dd>驾驶舱与任务中心每 3 秒，本页每 30 秒</dd></div>
        </dl>
      </section>
    </div>
  );
};

export const OptimizationSettingsPage: React.FC = () => (
  <div className="page settings-page settings-form-page">
    <SettingsHeader
      kicker="PRODUCTION POLICY"
      title="优化策略"
      description="集中管理自动选题、内容配比、配音、背景音乐和生产水位。"
    />
    <section className="dash-section settings-focus-card">
      <div className="section-head">
        <h2>自动生产策略</h2>
        <span className="section-hint">真实行情选题 · 确定性评分 · 失败自动搁浅</span>
      </div>
      <PolicyForm />
    </section>
  </div>
);
