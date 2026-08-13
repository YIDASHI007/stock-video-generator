import React, {useCallback, useEffect, useState} from "react";

import {
  API_BASE,
  angleLabels,
  api,
  bgmUrl,
  clearBgm,
  previewTopics,
  uploadBgm,
  type MarketCode,
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
