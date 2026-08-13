import React, {useCallback, useEffect, useMemo, useState} from "react";
import {Link} from "react-router-dom";
import {CalendarClock, ChevronDown, Clock3, KeyRound, ShieldCheck, SlidersHorizontal} from "lucide-react";
import {siTiktok, siWechat, siXiaohongshu, type SimpleIcon} from "simple-icons";

import {
  API_BASE,
  api,
  type ExtractorCookieSync,
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
import {accountRuleStore} from "../workspaceStore";

type PlatformMeta = {
  label: string;
  description: string;
  icon: SimpleIcon;
};

const platformOrder: SocialPlatform[] = [
  "douyin",
  "xiaohongshu",
  "wechat_channels",
];

const platformMeta: Record<SocialPlatform, PlatformMeta> = {
  douyin: {
    label: "抖音",
    description: "创作者中心视频与图文账号",
    icon: siTiktok,
  },
  xiaohongshu: {
    label: "小红书",
    description: "专业号与创作服务平台账号",
    icon: siXiaohongshu,
  },
  wechat_channels: {
    label: "微信视频号",
    description: "视频号助手与内容管理账号",
    icon: siWechat,
  },
};

const PlatformLogo: React.FC<{platform: SocialPlatform}> = ({platform}) => {
  const meta = platformMeta[platform];
  const isDouyin = platform === "douyin";
  return (
    <span className={`account-platform-mark ${platform}`} role="img" aria-label={`${meta.label} Logo`}>
      <svg viewBox={isDouyin ? "-1 -1 26 26" : "0 0 24 24"} focusable="false" aria-hidden="true">
        {isDouyin ? (
          <>
            <path className="tiktok-cyan" transform="translate(-0.45 0.45)" d={meta.icon.path} />
            <path className="tiktok-red" transform="translate(0.45 -0.45)" d={meta.icon.path} />
            <path className="tiktok-core" d={meta.icon.path} />
          </>
        ) : (
          <path d={meta.icon.path} />
        )}
      </svg>
    </span>
  );
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
  const [autoPublishEnabled, setAutoPublishEnabled] = useState(false);
  const [preferredTime, setPreferredTime] = useState("19:30");
  const [minIntervalMinutes, setMinIntervalMinutes] = useState(30);
  const [randomDelayMinutes, setRandomDelayMinutes] = useState(8);
  const [dailyLimit, setDailyLimit] = useState(5);
  const [showAdvanced, setShowAdvanced] = useState(false);
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
    setAutoPublishEnabled(false);
    setPreferredTime("19:30");
    setMinIntervalMinutes(30);
    setRandomDelayMinutes(8);
    setDailyLimit(5);
    setShowAdvanced(false);
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
          auto_publish_enabled: autoPublishEnabled,
        }),
      });
      accountRuleStore.upsert({
        accountId: account.account_id,
        preferredTime,
        minIntervalMinutes,
        randomDelayMinutes,
        dailyLimit,
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

  const syncExtractorCookies = async (account: PublishAccount) => {
    setAccountBusy(account.account_id, true);
    setActionError(null);
    try {
      const result = await api<ExtractorCookieSync>(
        `/api/accounts/${account.account_id}/extractor-cookies`,
        {method: "POST"},
      );
      if (!result.ready) {
        setActionError(`凭证已同步，但仍缺少：${result.missing.join("、")}`);
      } else {
        window.alert(`抓取凭证已同步，共 ${result.cookie_count} 项。现在可以解析对标账号。`);
      }
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAccountBusy(account.account_id, false);
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
  const accountIdValid = /^[A-Za-z0-9._-]{1,64}$/.test(accountId.trim());
  const accountIdTaken = accounts.some((account) => account.account_id === accountId.trim());
  const canCreate = Boolean(displayName.trim()) && accountIdValid && !accountIdTaken;
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
        <div className="module-actions">
          <Link className="button secondary" to="/publish/calendar"><Clock3 size={15}/> 发布时间槽</Link>
          <button className="button primary" onClick={() => openCreate()}>
            <PlusIcon size={16} />
            绑定新账号
          </button>
        </div>
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
              <PlatformLogo key={key} platform={key} />
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
        <div className="account-table" role="table" aria-label="已绑定账号">
          <div className="account-table-head" role="row">
            <label className="account-select" title="选择当前列表中的全部账号">
              <input
                type="checkbox"
                aria-label="选择全部账号"
                checked={visibleAccounts.length > 0 && visibleAccounts.every((account) => selected.has(account.account_id))}
                onChange={(event) => setSelected((current) => {
                  const next = new Set(current);
                  visibleAccounts.forEach((account) => event.target.checked ? next.add(account.account_id) : next.delete(account.account_id));
                  return next;
                })}
              />
            </label>
            <span>账号</span>
            <span>连接状态</span>
            <span>发布规则</span>
            <span>最近活动</span>
            <span>操作</span>
          </div>
          {visibleAccounts.map((account) => {
            const meta = platformMeta[account.platform];
            const state = accountStatus(account, loginStates[account.account_id]);
            const isBusy = busy.has(account.account_id);
            const rule = accountRuleStore.get(account.account_id);
            return (
              <article key={account.account_id} className={`account-row ${account.platform}`} role="row">
                <label className="account-select" title="选择账号">
                  <input
                    type="checkbox"
                    aria-label={`选择${account.display_name}`}
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
                <div className="account-row-identity">
                  <PlatformLogo platform={account.platform} />
                  <div>
                    <span className="account-platform-name">{meta.label}</span>
                    <h2>{account.display_name}</h2>
                    <small>{account.account_id}</small>
                  </div>
                </div>
                <div className="account-row-status">
                  <span className={`account-status ${state.kind}`}><i className={`session-pulse ${state.kind}`} />{state.label}</span>
                  <small>{state.detail}</small>
                </div>
                <div className="account-row-rule">
                  <span className={account.auto_publish_enabled ? "rule-mode automatic" : "rule-mode"}>
                    {account.auto_publish_enabled ? <ShieldCheck size={13} /> : <Clock3 size={13} />}
                    {account.auto_publish_enabled ? "允许自动发布" : "发布前确认"}
                  </span>
                  <small>{rule.preferredTime} · 每日 {rule.dailyLimit} 条 · 间隔 {rule.minIntervalMinutes}+0~{rule.randomDelayMinutes} 分钟</small>
                </div>
                <div className="account-row-activity">
                  <strong>{account.last_login_at ? formatFullDateTime(account.last_login_at) : "尚未登录"}</strong>
                  <small>{account.last_checked_at ? `检测于 ${formatFullDateTime(account.last_checked_at)}` : "尚未检测会话"}</small>
                </div>
                <div className="account-row-actions">
                  <button
                    className="mini-button account-scan-action"
                    disabled={isBusy}
                    onClick={() => void startLogin(account)}
                  >
                    {isBusy ? "处理中…" : account.enabled ? "重新扫码" : "扫码绑定"}
                  </button>
                  <button
                    className="mini-button"
                    disabled={isBusy || !account.enabled}
                    onClick={() => void checkAccount(account)}
                  >
                    检测
                  </button>
                  <button
                    className="icon-button account-unbind"
                    aria-label={account.enabled ? `解绑${account.display_name}` : `删除${account.display_name}`}
                    title={account.enabled ? "解绑账号" : "删除账号"}
                    disabled={isBusy}
                    onClick={() => void removeAccount(account)}
                  >
                    <TrashIcon size={15} />
                  </button>
                  {account.platform === "douyin" && account.auth_status === "logged_in" ? (
                    <button
                      className="mini-button account-cookie-action"
                      disabled={isBusy || !account.enabled}
                      onClick={() => void syncExtractorCookies(account)}
                      title="把此账号的抖音登录凭证安全同步到本机抓取器"
                    >
                      <KeyRound size={12}/> 同步抓取凭证
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {dialogOpen ? (
        <div className="account-dialog-backdrop" onMouseDown={() => setDialogOpen(false)}>
          <section className="account-dialog account-bind-dialog" role="dialog" aria-modal="true" aria-labelledby="bind-account-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="account-dialog-head">
              <div>
                <div className="eyebrow">ACCOUNT CONNECTION · 1 / 2</div>
                <h2 id="bind-account-title">配置并绑定账号</h2>
                <p>先设置这个账号的发布权限和默认节奏，下一步直接扫码登录。</p>
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
                  <PlatformLogo platform={key} />
                  <strong>{platformMeta[key].label}</strong>
                  <small>{platformMeta[key].description}</small>
                </button>
              ))}
            </div>
            <label className="account-field">
              <span>账号备注名称 <b>*</b></span>
              <input
                autoFocus
                maxLength={120}
                value={displayName}
                placeholder={`例如：${platformMeta[platform].label}主账号`}
                onChange={(event) => setDisplayName(event.target.value)}
              />
              <small>只在本机工作台中显示，可以使用“平台 + 内容方向”的命名方式。</small>
            </label>
            <div className="account-permission-row">
              <div>
                <ShieldCheck size={18} />
                <span><strong>允许自动发布</strong><small>关闭时，所有正式发布仍需在发布台人工确认。</small></span>
              </div>
              <label className="switch-control">
                <input aria-label="允许自动发布" type="checkbox" checked={autoPublishEnabled} onChange={(event) => setAutoPublishEnabled(event.target.checked)} />
                <span />
              </label>
            </div>
            <div className="account-rule-block">
              <div className="account-rule-title"><CalendarClock size={16}/><span><strong>默认发布节奏</strong><small>创建后仍可在发布日历中单独调整。</small></span></div>
              <div className="account-rule-grid">
                <label className="account-field"><span>首选时段</span><input type="time" value={preferredTime} onChange={(event) => setPreferredTime(event.target.value)} /></label>
                <label className="account-field"><span>每日上限</span><input type="number" min={1} max={100} value={dailyLimit} onChange={(event) => setDailyLimit(Math.max(1, Number(event.target.value) || 1))} /></label>
                <label className="account-field"><span>最小间隔（分钟）</span><input type="number" min={0} max={1440} value={minIntervalMinutes} onChange={(event) => setMinIntervalMinutes(Math.max(0, Number(event.target.value) || 0))} /></label>
                <label className="account-field"><span>随机延迟（分钟）</span><input type="number" min={0} max={240} value={randomDelayMinutes} onChange={(event) => setRandomDelayMinutes(Math.max(0, Number(event.target.value) || 0))} /></label>
              </div>
            </div>
            <button type="button" className="account-advanced-toggle" onClick={() => setShowAdvanced((current) => !current)} aria-expanded={showAdvanced}>
              <SlidersHorizontal size={15}/><span>高级设置</span><ChevronDown size={15} className={showAdvanced ? "open" : ""}/>
            </button>
            {showAdvanced ? (
              <label className={`account-field compact ${!accountIdValid || accountIdTaken ? "invalid" : ""}`}>
                <span>本机账号标识 <b>*</b></span>
                <input value={accountId} maxLength={64} onChange={(event) => setAccountId(event.target.value)} />
                <small>{accountIdTaken ? "这个本机标识已存在，请更换一个。" : accountIdValid ? "仅支持字母、数字、点、下划线和短横线。" : "格式不正确，仅支持字母、数字、点、下划线和短横线。"}</small>
              </label>
            ) : null}
            <div className="account-login-note">
              <PlatformLogo platform={platform} />
              <p>继续后会在当前页面显示{platformMeta[platform].label}官方二维码。登录资料不会上传到服务器。</p>
            </div>
            <div className="account-dialog-actions">
              <button className="button secondary" onClick={() => setDialogOpen(false)}>取消</button>
              <button
                className="button primary"
                disabled={!canCreate || busy.has(`create:${accountId}`)}
                onClick={() => void createAccount()}
              >
                {busy.has(`create:${accountId}`) ? "正在创建…" : "下一步：扫码登录"}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {scanAccount && scanMeta ? (
        <div className="account-dialog-backdrop" onMouseDown={() => void closeScanDialog()}>
          <section
            className={`account-dialog account-qr-dialog ${scanAccount.platform}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="scan-account-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="account-dialog-head">
              <div>
                <div className="eyebrow">ACCOUNT CONNECTION · 2 / 2</div>
                <h2 id="scan-account-title">扫码绑定{scanMeta.label}</h2>
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
              <PlatformLogo platform={scanAccount.platform} />
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
