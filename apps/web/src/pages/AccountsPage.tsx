import React, {useCallback, useEffect, useMemo, useState} from "react";

import {
  API_BASE,
  api,
  type PublishAccount,
  type PublishLoginStatus,
  type SocialPlatform,
} from "../api";
import {
  ErrorNotice,
  formatFullDateTime,
  PlusIcon,
  RetryIcon,
  TrashIcon,
} from "../components";
import {usePolling} from "../hooks";

type PlatformMeta = {
  label: string;
  short: string;
  description: string;
};

const platformOrder: SocialPlatform[] = [
  "douyin",
  "xiaohongshu",
  "wechat_channels",
];

const platformMeta: Record<SocialPlatform, PlatformMeta> = {
  douyin: {
    label: "抖音",
    short: "抖",
    description: "创作者中心视频与图文账号",
  },
  xiaohongshu: {
    label: "小红书",
    short: "红",
    description: "专业号与创作服务平台账号",
  },
  wechat_channels: {
    label: "微信视频号",
    short: "视",
    description: "视频号助手与内容管理账号",
  },
};

const generatedId = (platform: SocialPlatform): string =>
  `${platform}-${Date.now().toString(36)}`;

const accountStatus = (
  account: PublishAccount,
  login?: PublishLoginStatus,
): {kind: string; label: string; detail: string} => {
  if (
    login?.status === "preparing_qr" ||
    login?.status === "waiting_scan" ||
    login?.status === "scanned"
  ) {
    return {kind: "waiting", label: "等待扫码", detail: login.message};
  }
  if (login?.status === "failed") {
    return {kind: "failed", label: "登录失败", detail: login.message};
  }
  if (!account.enabled) {
    return {kind: "offline", label: "已解绑", detail: "浏览器会话已从本机移除"};
  }
  if (account.auth_status === "logged_in") {
    return {kind: "online", label: "已连接", detail: "会话保存在这台电脑"};
  }
  if (account.auth_status === "logged_out") {
    return {kind: "offline", label: "登录失效", detail: "需要重新扫码"};
  }
  if (account.auth_status === "login_failed") {
    return {kind: "failed", label: "登录失败", detail: "请重新打开扫码窗口"};
  }
  return {kind: "unknown", label: "待检测", detail: "尚未验证登录状态"};
};

export const AccountsPage: React.FC = () => {
  const loader = useCallback(() => api<PublishAccount[]>("/api/accounts"), []);
  const {data, error, loading, refresh} = usePolling(loader, 5000);
  const accounts = data ?? [];
  const [filter, setFilter] = useState<SocialPlatform | "all">("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [platform, setPlatform] = useState<SocialPlatform>("douyin");
  const [displayName, setDisplayName] = useState("");
  const [accountId, setAccountId] = useState(generatedId("douyin"));
  const [loginStates, setLoginStates] = useState<Record<string, PublishLoginStatus>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);
  const [scanAccount, setScanAccount] = useState<PublishAccount | null>(null);

  const visibleAccounts = useMemo(
    () => accounts.filter((account) => filter === "all" || account.platform === filter),
    [accounts, filter],
  );

  useEffect(() => {
    let active = true;
    const poll = async () => {
      const candidateMap = new Map(
        accounts
          .filter((account) => account.enabled)
          .map((account) => [account.account_id, account]),
      );
      if (scanAccount) candidateMap.set(scanAccount.account_id, scanAccount);
      const candidates = [...candidateMap.values()];
      const results = await Promise.allSettled(
        candidates.map((account) =>
          api<PublishLoginStatus>(`/api/accounts/${account.account_id}/login`),
        ),
      );
      if (!active) return;
      setLoginStates((current) => {
        const next = {...current};
        results.forEach((result, index) => {
          if (result.status === "fulfilled") {
            next[candidates[index].account_id] = result.value;
          }
        });
        return next;
      });
    };
    void poll();
    const timer = window.setInterval(poll, 2200);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [accounts, scanAccount]);

  const setAccountBusy = (id: string, value: boolean) =>
    setBusy((current) => {
      const next = new Set(current);
      if (value) next.add(id);
      else next.delete(id);
      return next;
    });

  const openCreate = (nextPlatform: SocialPlatform = "douyin") => {
    setPlatform(nextPlatform);
    setDisplayName("");
    setAccountId(generatedId(nextPlatform));
    setActionError(null);
    setDialogOpen(true);
  };

  const choosePlatform = (nextPlatform: SocialPlatform) => {
    setPlatform(nextPlatform);
    setAccountId(generatedId(nextPlatform));
  };

  const startLogin = async (account: PublishAccount) => {
    setScanAccount(account);
    setAccountBusy(account.account_id, true);
    setActionError(null);
    try {
      const state = await api<PublishLoginStatus>(
        `/api/accounts/${account.account_id}/login`,
        {method: "POST"},
      );
      setLoginStates((current) => ({...current, [account.account_id]: state}));
      refresh();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setActionError(message);
      setLoginStates((current) => ({
        ...current,
        [account.account_id]: {
          account_id: account.account_id,
          platform: account.platform,
          auth_status: account.auth_status,
          status: "failed",
          message,
          last_login_at: account.last_login_at,
          updated_at: new Date().toISOString(),
        },
      }));
    } finally {
      setAccountBusy(account.account_id, false);
    }
  };

  const createAccount = async () => {
    if (!displayName.trim() || !accountId.trim()) return;
    const tempId = `create:${accountId}`;
    setAccountBusy(tempId, true);
    setActionError(null);
    try {
      const account = await api<PublishAccount>("/api/accounts", {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId.trim(),
          platform,
          display_name: displayName.trim(),
          auto_publish_enabled: false,
        }),
      });
      setDialogOpen(false);
      refresh();
      await startLogin(account);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAccountBusy(tempId, false);
    }
  };

  const checkAccount = async (account: PublishAccount) => {
    setAccountBusy(account.account_id, true);
    setActionError(null);
    try {
      const state = await api<PublishLoginStatus>(
        `/api/accounts/${account.account_id}/check`,
        {method: "POST"},
      );
      setLoginStates((current) => ({...current, [account.account_id]: state}));
      refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAccountBusy(account.account_id, false);
    }
  };

  const batchCheck = async () => {
    for (const id of selected) {
      const account = accounts.find((item) => item.account_id === id);
      if (account?.enabled) await checkAccount(account);
    }
  };

  const removeAccount = async (account: PublishAccount) => {
    const prompt = account.enabled
      ? `解绑“${account.display_name}”？本机会清除该账号的登录会话，解绑后可再次删除账号。`
      : `永久删除“${account.display_name}”？该账号将从账号管理中移除。`;
    if (!window.confirm(prompt)) {
      return;
    }
    setAccountBusy(account.account_id, true);
    setActionError(null);
    try {
      if (account.enabled) {
        await api<PublishAccount>(`/api/accounts/${account.account_id}/unbind`, {
          method: "POST",
        });
      } else {
        await api(`/api/accounts/${account.account_id}`, {method: "DELETE"});
      }
      setSelected((current) => {
        const next = new Set(current);
        next.delete(account.account_id);
        return next;
      });
      refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAccountBusy(account.account_id, false);
    }
  };

  const connectedCount = accounts.filter(
    (account) => account.enabled && account.auth_status === "logged_in",
  ).length;
  const scanState = scanAccount ? loginStates[scanAccount.account_id] : undefined;
  const scanMeta = scanAccount ? platformMeta[scanAccount.platform] : undefined;
  const qrCodeSrc = scanState?.qr_code_url
    ? `${API_BASE}${scanState.qr_code_url}?v=${scanState.qr_revision ?? 0}`
    : null;

  useEffect(() => {
    if (!scanAccount || scanState?.status !== "logged_in") return;
    const timer = window.setTimeout(() => {
      setScanAccount(null);
      refresh();
    }, 1400);
    return () => window.clearTimeout(timer);
  }, [refresh, scanAccount, scanState?.status]);

  const closeScanDialog = async () => {
    const current = scanAccount;
    setScanAccount(null);
    if (
      current &&
      scanState &&
      ["preparing_qr", "waiting_scan", "scanned"].includes(scanState.status)
    ) {
      try {
        await api(`/api/accounts/${current.account_id}/login/cancel`, {method: "POST"});
      } catch {
        // Closing the local dialog must remain responsive even if cancellation races completion.
      }
    }
  };

  return (
    <div className="page accounts-page">
      <header className="accounts-hero">
        <div>
          <div className="eyebrow">ACCOUNT POOL</div>
          <h1>账号管理</h1>
          <p>集中保存各平台登录会话。后续发布、数据回收和自动化任务都从这里选择账号。</p>
        </div>
        <button className="button primary" onClick={() => openCreate()}>
          <PlusIcon size={16} />
          绑定新账号
        </button>
      </header>

      {error || actionError ? <ErrorNotice message={actionError ?? error ?? ""} /> : null}

      <section className="account-overview" aria-label="账号概览">
        <div>
          <span>账号总数</span>
          <strong className="num">{accounts.length}</strong>
        </div>
        <div>
          <span>已连接</span>
          <strong className="num connected">{connectedCount}</strong>
        </div>
        {platformOrder.map((key) => (
          <button key={key} onClick={() => setFilter(filter === key ? "all" : key)}>
            <i className={`platform-dot ${key}`} />
            <span>{platformMeta[key].label}</span>
            <strong className="num">
              {accounts.filter((account) => account.platform === key).length}
            </strong>
          </button>
        ))}
      </section>

      <section className="accounts-toolbar">
        <div className="account-filters">
          <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
            全部
          </button>
          {platformOrder.map((key) => (
            <button
              key={key}
              className={filter === key ? "active" : ""}
              onClick={() => setFilter(key)}
            >
              {platformMeta[key].label}
            </button>
          ))}
        </div>
        {selected.size ? (
          <div className="account-batch-actions">
            <span>已选 {selected.size} 个</span>
            <button className="mini-button" onClick={() => void batchCheck()}>
              <RetryIcon size={13} />批量检测
            </button>
            <button className="mini-button" onClick={() => setSelected(new Set())}>
              取消选择
            </button>
          </div>
        ) : null}
      </section>

      {loading && !data ? <p className="quiet-line">正在读取本机账号池…</p> : null}
      {!loading && !visibleAccounts.length ? (
        <div className="account-empty">
          <div className="account-empty-orbit">
            {platformOrder.map((key) => (
              <span key={key} className={key}>{platformMeta[key].short}</span>
            ))}
          </div>
          <h2>还没有绑定账号</h2>
          <p>选择一个平台，官方登录二维码会直接显示在这里。扫码完成后，会话只保存在这台电脑。</p>
          <div>
            {platformOrder.map((key) => (
              <button key={key} className="button secondary" onClick={() => openCreate(key)}>
                绑定{platformMeta[key].label}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="account-grid">
          {visibleAccounts.map((account) => {
            const meta = platformMeta[account.platform];
            const state = accountStatus(account, loginStates[account.account_id]);
            const isBusy = busy.has(account.account_id);
            return (
              <article key={account.account_id} className={`account-card ${account.platform}`}>
                <div className="account-card-head">
                  <label className="account-select" title="选择账号">
                    <input
                      type="checkbox"
                      checked={selected.has(account.account_id)}
                      onChange={(event) =>
                        setSelected((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(account.account_id);
                          else next.delete(account.account_id);
                          return next;
                        })
                      }
                    />
                  </label>
                  <span className={`account-platform-mark ${account.platform}`}>{meta.short}</span>
                  <div>
                    <span className="account-platform-name">{meta.label}</span>
                    <h2>{account.display_name}</h2>
                  </div>
                  <span className={`account-status ${state.kind}`}>{state.label}</span>
                </div>
                <div className="account-session-line">
                  <span className={`session-pulse ${state.kind}`} />
                  <p>{state.detail}</p>
                </div>
                <dl className="account-meta">
                  <div><dt>本机标识</dt><dd>{account.account_id}</dd></div>
                  <div>
                    <dt>最近登录</dt>
                    <dd>{account.last_login_at ? formatFullDateTime(account.last_login_at) : "尚未登录"}</dd>
                  </div>
                </dl>
                <div className="account-card-actions">
                  <button
                    className="button primary"
                    disabled={isBusy}
                    onClick={() => void startLogin(account)}
                  >
                    {isBusy ? "处理中…" : account.enabled ? "重新扫码" : "扫码绑定"}
                  </button>
                  <button
                    className="button secondary"
                    disabled={isBusy || !account.enabled}
                    onClick={() => void checkAccount(account)}
                  >
                    检测状态
                  </button>
                  <button
                    className="icon-button account-unbind"
                    title={account.enabled ? "解绑账号" : "删除账号"}
                    disabled={isBusy}
                    onClick={() => void removeAccount(account)}
                  >
                    <TrashIcon size={15} />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {dialogOpen ? (
        <div className="account-dialog-backdrop" onMouseDown={() => setDialogOpen(false)}>
          <section className="account-dialog" onMouseDown={(event) => event.stopPropagation()}>
            <div className="account-dialog-head">
              <div>
                <div className="eyebrow">NEW CONNECTION</div>
                <h2>绑定社交媒体账号</h2>
              </div>
              <button className="icon-button" onClick={() => setDialogOpen(false)} aria-label="关闭">
                ×
              </button>
            </div>
            <div className="platform-choice">
              {platformOrder.map((key) => (
                <button
                  key={key}
                  className={`${key} ${platform === key ? "active" : ""}`}
                  onClick={() => choosePlatform(key)}
                >
                  <span className={`account-platform-mark ${key}`}>{platformMeta[key].short}</span>
                  <strong>{platformMeta[key].label}</strong>
                  <small>{platformMeta[key].description}</small>
                </button>
              ))}
            </div>
            <label className="account-field">
              <span>账号备注名称</span>
              <input
                autoFocus
                value={displayName}
                placeholder={`例如：${platformMeta[platform].label}主账号`}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>
            <label className="account-field compact">
              <span>本机账号标识</span>
              <input value={accountId} onChange={(event) => setAccountId(event.target.value)} />
              <small>只用于区分本机浏览器会话，创建后不能切换平台。</small>
            </label>
            <div className="account-login-note">
              <span className={`account-platform-mark ${platform}`}>{platformMeta[platform].short}</span>
              <p>继续后会在当前页面显示{platformMeta[platform].label}官方二维码。登录资料不会上传到服务器。</p>
            </div>
            <div className="account-dialog-actions">
              <button className="button secondary" onClick={() => setDialogOpen(false)}>取消</button>
              <button
                className="button primary"
                disabled={!displayName.trim() || !accountId.trim() || busy.has(`create:${accountId}`)}
                onClick={() => void createAccount()}
              >
                保存并获取二维码
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {scanAccount && scanMeta ? (
        <div className="account-dialog-backdrop" onMouseDown={() => void closeScanDialog()}>
          <section
            className={`account-dialog account-qr-dialog ${scanAccount.platform}`}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="account-dialog-head">
              <div>
                <div className="eyebrow">SECURE SIGN IN</div>
                <h2>扫码绑定{scanMeta.label}</h2>
              </div>
              <button
                className="icon-button"
                onClick={() => void closeScanDialog()}
                aria-label="关闭"
              >
                ×
              </button>
            </div>
            <div className="account-qr-identity">
              <span className={`account-platform-mark ${scanAccount.platform}`}>
                {scanMeta.short}
              </span>
              <div>
                <strong>{scanAccount.display_name}</strong>
                <small>{scanAccount.account_id}</small>
              </div>
            </div>
            <div className={`account-qr-frame ${scanState?.status ?? "preparing_qr"}`}>
              {qrCodeSrc && scanState?.status !== "logged_in" ? (
                <img src={qrCodeSrc} alt={`${scanMeta.label}登录二维码`} />
              ) : scanState?.status === "logged_in" ? (
                <div className="account-qr-success" aria-label="登录成功">✓</div>
              ) : (
                <div className="account-qr-loading" aria-label="正在获取二维码">
                  <span />
                  <p>正在连接{scanMeta.label}</p>
                </div>
              )}
            </div>
            <div className={`account-qr-status ${scanState?.status ?? "preparing_qr"}`}>
              <span />
              <div>
                <strong>
                  {scanState?.status === "logged_in"
                    ? "绑定成功"
                    : scanState?.status === "scanned"
                      ? "已扫码，等待确认"
                      : scanState?.status === "failed"
                        ? "二维码获取失败"
                        : qrCodeSrc
                          ? "等待扫码"
                          : "正在获取官方二维码"}
                </strong>
                <p>{scanState?.message ?? `正在打开${scanMeta.label}安全登录页面`}</p>
              </div>
            </div>
            <p className="account-qr-privacy">
              请使用{scanMeta.label === "微信视频号" ? "微信" : scanMeta.label} App 扫码。二维码和登录会话仅在这台电脑处理。
            </p>
            <div className="account-dialog-actions">
              {scanState?.status === "failed" ? (
                <button className="button primary" onClick={() => void startLogin(scanAccount)}>
                  重新获取二维码
                </button>
              ) : null}
              <button className="button secondary" onClick={() => void closeScanDialog()}>
                {scanState?.status === "logged_in" ? "完成" : "取消"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
};
