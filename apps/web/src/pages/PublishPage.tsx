import React, {useCallback, useEffect, useState} from "react";

import {
  api,
  coverUrl,
  publishEvidenceUrl,
  type GalleryOutput,
  type PublishAccount,
  type PublishJob,
  type PublishLoginStatus,
  type PublishMode,
} from "../api";
import {
  ErrorNotice,
  PageHeader,
  ProgressBar,
  formatDateTime,
} from "../components";
import {usePolling} from "../hooks";
import {PublishBatchPanel} from "./PublishBatchPanel";

const stageLabels: Record<string, string> = {
  PUBLISH_CREATED: "待执行",
  VALIDATING_ARTIFACTS: "校验素材",
  CHECKING_LOGIN: "检查登录",
  OPENING_UPLOAD_PAGE: "打开发布页",
  UPLOADING_VIDEO: "上传视频",
  WAITING_TRANSCODE: "等待平台处理",
  FILLING_TITLE: "填写标题",
  FILLING_DESCRIPTION: "填写简介",
  ADDING_TOPICS: "添加话题",
  SETTING_LANDSCAPE_COVER: "上传横封面",
  SETTING_PORTRAIT_COVER: "上传竖封面",
  SETTING_COLLECTION: "设置合集",
  SETTING_DECLARATION: "自主声明",
  VALIDATING_PREVIEW: "发布前校验",
  READY_FOR_PUBLISH: "预检完成",
  PUBLISHING: "发布中",
  VERIFYING_RESULT: "核验发布结果",
  PUBLISHED: "已发布",
  NEEDS_LOGIN: "需要登录",
  NEEDS_SMS: "需要短信验证",
  NEEDS_HUMAN: "等待人工授权",
  FAILED_RETRYABLE: "可重试",
  FAILED_FINAL: "已失败",
  CANCELLED: "已取消",
};

const money = (value: number): string =>
  new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 1,
  }).format(value / 10_000);

export const PublishPage: React.FC = () => {
  const {data: outputs} = usePolling(
    useCallback(() => api<GalleryOutput[]>("/api/outputs"), []),
    5000,
  );
  const {data: accounts, refresh: refreshAccounts} = usePolling(
    useCallback(() => api<PublishAccount[]>("/api/publish/accounts"), []),
    5000,
  );
  const {data: jobs, refresh: refreshJobs} = usePolling(
    useCallback(() => api<PublishJob[]>("/api/publish/jobs"), []),
    2000,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PublishJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accountId, setAccountId] = useState("douyin-main");
  const [displayName, setDisplayName] = useState("抖音主账号");
  const [autoPublish, setAutoPublish] = useState(false);
  const [outputId, setOutputId] = useState("");
  const [publishAccountId, setPublishAccountId] = useState("");
  const [mode, setMode] = useState<PublishMode>("dry_run");
  const [scheduledAt, setScheduledAt] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [topics, setTopics] = useState("");
  const [collection, setCollection] = useState("");
  const [loginState, setLoginState] = useState<PublishLoginStatus | null>(null);

  useEffect(() => {
    if (!outputId && outputs?.[0]) setOutputId(outputs[0].output_id);
  }, [outputId, outputs]);
  useEffect(() => {
    if (!publishAccountId && accounts?.[0]) {
      const account = accounts[0];
      setPublishAccountId(account.account_id);
      setAccountId(account.account_id);
      setDisplayName(account.display_name);
      setAutoPublish(account.auto_publish_enabled);
    }
  }, [accounts, publishAccountId]);
  useEffect(() => {
    if (!selectedId && jobs?.[0]) setSelectedId(jobs[0].publish_id);
  }, [jobs, selectedId]);

  const loadDetail = useCallback(async (syncCopy = false) => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    const job = await api<PublishJob>(`/api/publish/jobs/${selectedId}`);
    setDetail(job);
    if (syncCopy) {
      setTitle(job.title);
      setDescription(job.description);
      setTopics(job.topics.join(" "));
      setCollection(job.collection ?? "");
    }
  }, [selectedId]);

  useEffect(() => {
    void loadDetail(true).catch((reason) => setError(String(reason)));
    const timer = window.setInterval(() => {
      void loadDetail().catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [loadDetail]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      refreshAccounts();
      refreshJobs();
      await loadDetail();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const lastAttempt = detail?.attempts?.at(-1);

  const persistAccount = async (): Promise<PublishAccount> => {
    const saved = await api<PublishAccount>("/api/publish/accounts", {
      method: "POST",
      body: JSON.stringify({
        account_id: accountId,
        display_name: displayName,
        auto_publish_enabled: autoPublish,
      }),
    });
    setPublishAccountId(saved.account_id);
    return saved;
  };

  const saveAccount = () =>
    run(async () => {
      await persistAccount();
    });

  const openLogin = () =>
    run(async () => {
      const saved = await persistAccount();
      const status = await api<PublishLoginStatus>(
        `/api/publish/accounts/${saved.account_id}/login`,
        {method: "POST"},
      );
      setLoginState(status);
    });

  const selectAccount = (selectedAccountId: string) => {
    setPublishAccountId(selectedAccountId);
    const account = accounts?.find(
      (item) => item.account_id === selectedAccountId,
    );
    if (account) {
      setAccountId(account.account_id);
      setDisplayName(account.display_name);
      setAutoPublish(account.auto_publish_enabled);
    }
  };

  useEffect(() => {
    if (
      !publishAccountId ||
      !loginState ||
      ["logged_in", "failed", "cancelled"].includes(loginState.status)
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void api<PublishLoginStatus>(
        `/api/publish/accounts/${publishAccountId}/login`,
      ).then(setLoginState);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loginState, publishAccountId]);

  const createJob = () =>
    run(async () => {
      const savedAccount = await persistAccount();
      const created = await api<PublishJob>("/api/publish/jobs", {
        method: "POST",
        body: JSON.stringify({
          output_id: outputId,
          account_id: savedAccount.account_id,
          mode,
          scheduled_at:
            mode === "scheduled" && scheduledAt
              ? new Date(scheduledAt).toISOString()
              : null,
        }),
      });
      setSelectedId(created.publish_id);
    });

  const startJob = () =>
    run(() =>
      api(`/api/publish/jobs/${selectedId}/run`, {method: "POST"}),
    );

  const approveAndStartJob = () =>
    run(async () => {
      await persistAccount();
      await api(`/api/publish/jobs/${selectedId}/approve`, {method: "POST"});
      await api(`/api/publish/jobs/${selectedId}/run`, {method: "POST"});
    });

  const saveCopy = () =>
    run(() =>
      api<PublishJob>(`/api/publish/jobs/${selectedId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title,
          description,
          topics: topics.split(/[\s#，,]+/).filter(Boolean),
          collection: collection || null,
        }),
      }),
    );

  return (
    <div className="page publish-page">
      <PageHeader
        eyebrow="DOUYIN PUBLISHER"
        title="发布中心"
        description="生成真实发布清单，逐项填写标题、简介、话题与横竖封面；正式发布必须人工授权。"
      />
      {error ? <ErrorNotice message={error} /> : null}
      <PublishBatchPanel />

      <section className="publish-setup">
        <div className="panel publish-account-panel">
          <div className="panel-title-row">
            <h2>1. 账号与登录</h2>
            <span className="publish-safe">本地会话档案</span>
          </div>
          <p className="publish-step-help">
            首次使用不需要先选账号：确认下面两个名称后，直接点击“保存并打开扫码登录”。
          </p>
          <div className="publish-form-grid">
            <label>
              本机账号标识
              <input value={accountId} onChange={(event) => setAccountId(event.target.value)} />
            </label>
            <label>
              账号备注名称
              <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
            </label>
          </div>
          <label className="publish-check">
            <input
              type="checkbox"
              checked={autoPublish}
              onChange={(event) => setAutoPublish(event.target.checked)}
            />
            开启本系统“自动点击发布”开关（仍需逐条人工授权）
          </label>
          <p className="publish-field-note">
            这是当前电脑上的工作流安全开关，不是抖音平台授予的账号权限。
            生成清单或授权时会自动保存。
          </p>
          <label>
            已保存账号
            <select
              value={publishAccountId}
              disabled={!accounts?.length}
              onChange={(event) => selectAccount(event.target.value)}
            >
              <option value="">
                {accounts?.length ? "选择已有账号" : "尚无账号，将在扫码前自动保存"}
              </option>
              {(accounts ?? []).map((account) => (
                <option key={account.account_id} value={account.account_id}>
                  {account.display_name}
                </option>
              ))}
            </select>
          </label>
          <div className="publish-inline-actions">
            <button
              className="button primary"
              disabled={busy || !accountId.trim() || !displayName.trim()}
              onClick={openLogin}
            >
              保存并打开扫码登录
            </button>
            <button
              className="button"
              disabled={busy || !accountId.trim() || !displayName.trim()}
              onClick={saveAccount}
            >
              仅保存，不登录
            </button>
          </div>
          {!accounts?.length && !loginState ? (
            <p className="publish-login-state">
              当前还没有已保存账号，点击绿色按钮后会自动创建。
            </p>
          ) : null}
          {loginState ? (
            <p className={`publish-login-state ${loginState.status}`}>
              {loginState.message}
            </p>
          ) : null}
        </div>

        <div className="panel">
          <div className="panel-title-row">
            <h2>2. 新建发布任务</h2>
            <span className="publish-safe">默认只预检</span>
          </div>
          <label>
            视频成片
            <select value={outputId} onChange={(event) => setOutputId(event.target.value)}>
              <option value="">选择成片</option>
              {(outputs ?? []).map((output) => (
                <option key={output.output_id} value={output.output_id}>
                  {output.name ?? output.symbol ?? output.output_id.slice(0, 8)}
                  {" · "}
                  {output.created_at ? formatDateTime(output.created_at) : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="publish-form-grid">
            <label>
              发布方式
              <select value={mode} onChange={(event) => setMode(event.target.value as PublishMode)}>
                <option value="dry_run">预检，不发布</option>
                <option value="immediate">立即发布</option>
                <option value="scheduled">定时发布</option>
              </select>
            </label>
            {mode === "scheduled" ? (
              <label>
                发布时间
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(event) => setScheduledAt(event.target.value)}
                />
              </label>
            ) : <span />}
          </div>
          <button
            className="button primary"
            disabled={busy || !outputId || !publishAccountId}
            onClick={createJob}
          >
            生成发布清单
          </button>
        </div>
      </section>

      <section className="publish-workspace">
        <aside className="panel publish-job-list">
          <h2>发布任务</h2>
          {(jobs ?? []).map((job) => (
            <button
              key={job.publish_id}
              className={selectedId === job.publish_id ? "active" : ""}
              onClick={() => setSelectedId(job.publish_id)}
            >
              <strong>{job.title}</strong>
              <span>{stageLabels[job.stage] ?? job.stage}</span>
            </button>
          ))}
          {!jobs?.length ? <p className="muted">还没有发布任务。</p> : null}
        </aside>

        <div className="panel publish-editor">
          {!detail?.manifest ? (
            <div className="empty-state">
              <h3>选择一条发布任务</h3>
              <p>发布清单生成后，可在这里核对所有字段和双封面。</p>
            </div>
          ) : (
            <>
              <div className="panel-title-row">
                <div>
                  <span className="eyebrow">真实回测事实</span>
                  <h2>
                    {detail.manifest.facts.stock_name} · {detail.manifest.facts.symbol}
                  </h2>
                </div>
                <span className={`publish-stage stage-${detail.stage}`}>
                  {stageLabels[detail.stage] ?? detail.stage}
                </span>
              </div>
              <ProgressBar value={detail.progress} />
              <div className="publish-facts">
                <span>本金<strong>{money(detail.manifest.facts.initial_capital)}万</strong></span>
                <span>最终资产<strong>{money(detail.manifest.facts.final_value)}万</strong></span>
                <span>收益率<strong>{detail.manifest.facts.return_pct.toFixed(2)}%</strong></span>
                <span>最大回撤<strong>{detail.manifest.facts.max_drawdown_pct.toFixed(2)}%</strong></span>
              </div>

              <div className="publish-cover-grid">
                <figure className="landscape">
                  <img src={coverUrl(detail.output_id, "landscape")} alt="横版4:3封面" />
                  <figcaption>横封面 · 1440×1080 · 4:3</figcaption>
                </figure>
                <figure className="portrait">
                  <img src={coverUrl(detail.output_id, "portrait")} alt="竖版3:4封面" />
                  <figcaption>竖封面 · 1080×1440 · 3:4</figcaption>
                </figure>
              </div>

              <div className="publish-copy-form">
                <label>
                  作品标题 <span className="num">{title.length}/30</span>
                  <input maxLength={30} value={title} onChange={(event) => setTitle(event.target.value)} />
                </label>
                <div className="publish-title-candidates">
                  {detail.manifest.content.title_candidates.map((candidate) => (
                    <button key={candidate} type="button" onClick={() => setTitle(candidate)}>
                      {candidate}
                    </button>
                  ))}
                </div>
                <label>
                  作品简介 <span className="num">{description.length}/1000</span>
                  <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={7} />
                </label>
                <div className="publish-form-grid">
                  <label>
                    话题（1–5个）
                    <input value={topics} onChange={(event) => setTopics(event.target.value)} />
                  </label>
                  <label>
                    合集
                    <input value={collection} onChange={(event) => setCollection(event.target.value)} />
                  </label>
                </div>
                <button className="button" disabled={busy} onClick={saveCopy}>保存文案</button>
              </div>

              {detail.error_reason ? <ErrorNotice message={detail.error_reason} /> : null}
              {lastAttempt?.screenshot_path ? (
                <div className="publish-evidence">
                  <h3>最近一次执行证据</h3>
                  <img
                    src={publishEvidenceUrl(lastAttempt.attempt_id, "screenshot")}
                    alt="浏览器执行截图"
                  />
                  <div>
                    <a
                      className="button"
                      href={publishEvidenceUrl(lastAttempt.attempt_id, "dom")}
                      target="_blank"
                      rel="noreferrer"
                    >
                      查看 DOM
                    </a>
                    <a
                      className="button"
                      href={publishEvidenceUrl(lastAttempt.attempt_id, "actions")}
                      target="_blank"
                      rel="noreferrer"
                    >
                      查看动作日志
                    </a>
                  </div>
                </div>
              ) : null}

              <div className="publish-action-bar">
                {detail.mode === "dry_run" ? (
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={startJob}
                  >
                    开始预检（不会发布）
                  </button>
                ) : null}
                {detail.mode !== "dry_run" && !detail.approved_at ? (
                  <button
                    className="button danger"
                    disabled={busy}
                    onClick={approveAndStartJob}
                  >
                    授权并开始发布
                  </button>
                ) : null}
                {detail.mode !== "dry_run" &&
                Boolean(detail.approved_at) &&
                detail.stage === "PUBLISH_CREATED" ? (
                  <button
                    className="button danger"
                    disabled={busy}
                    onClick={startJob}
                  >
                    已授权，立即启动发布
                  </button>
                ) : null}
                {["READY_FOR_PUBLISH", "NEEDS_LOGIN", "NEEDS_SMS", "NEEDS_HUMAN", "FAILED_RETRYABLE"].includes(detail.stage) ? (
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() => run(() => api(`/api/publish/jobs/${selectedId}/retry`, {method: "POST"}))}
                  >
                    重试 / 继续
                  </button>
                ) : null}
                {detail.published_url ? (
                  <a className="button primary" href={detail.published_url} target="_blank" rel="noreferrer">
                    查看已发布作品
                  </a>
                ) : null}
              </div>
              <p className="publish-disclaimer">
                正式执行时会由本机后端打开一个独立、可见的 Chrome 窗口操作抖音创作者中心。
                请保持该窗口开启，遇到短信验证时由你本人完成。<br />
                数字来自 simulation.json；标题和简介中的金额、日期、收益率会在保存时再次核对。
                Agent 不能点击发布、输入验证码或绕过安全校验。
              </p>
            </>
          )}
        </div>
      </section>
    </div>
  );
};
