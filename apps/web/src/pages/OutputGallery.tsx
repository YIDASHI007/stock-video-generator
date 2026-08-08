import React, {useEffect, useMemo, useState} from "react";
import {useNavigate, useSearchParams} from "react-router-dom";

import {
  api,
  angleLabels,
  coverUrl,
  deleteOutput,
  packUrl,
  thumbnailUrl,
  videoUrl,
  type GalleryOutput,
  type PublishAccount,
  type PublishBatch,
} from "../api";
import {
  ChevronIcon,
  DownloadIcon,
  ErrorNotice,
  PlayIcon,
  TrashIcon,
  dayLabel,
  formatDateTime,
  formatTime,
  parseServerDate,
} from "../components";

/* ---------------- 筛选选项 ---------------- */

const marketOptions: Array<{value: string; label: string}> = [
  {value: "all", label: "全部"},
  {value: "CN", label: "A股"},
  {value: "HK", label: "港股"},
  {value: "US", label: "美股"},
  {value: "CRYPTO", label: "加密资产"},
];

const angleOptions: Array<{value: string; label: string}> = [
  {value: "all", label: "全部"},
  {value: "surge", label: "暴涨"},
  {value: "crash", label: "暴跌"},
  {value: "rollercoaster", label: "过山车"},
  {value: "compound", label: "长牛"},
];

const pnlOptions: Array<{value: string; label: string}> = [
  {value: "all", label: "全部"},
  {value: "win", label: "盈利"},
  {value: "lose", label: "亏损"},
];

const marketLabels: Record<string, string> = {
  CN: "A股",
  HK: "港股",
  US: "美股",
  CRYPTO: "加密资产",
};

/* ---------------- 格式化 ---------------- */

/** 与后端 pack 的 date 参数一致的本地日期键（YYYY-MM-DD，补零）。 */
const dayIsoKey = (iso: string): string => {
  const d = parseServerDate(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

/** 今天只显示 HH:MM，更早显示 MM-DD HH:MM。 */
const createdLabel = (iso: string): string =>
  dayIsoKey(iso) === dayIsoKey(new Date().toISOString())
    ? formatTime(iso)
    : formatDateTime(iso);

const formatDuration = (seconds: number | null): string => {
  if (seconds == null) return "--:--";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
};

/** 收益率带正负号，如 +1415.02%；null 显示占位。 */
const formatReturn = (value: number | null): string => {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
};

const outputTitle = (output: GalleryOutput): string => {
  if (output.name && output.symbol) return `${output.name} · ${output.symbol}`;
  if (output.name) return output.name;
  if (output.symbol) return output.symbol;
  return `回测 ${output.simulation_id.slice(0, 8)}`;
};

/* ---------------- 缩略图（失败降级占位块） ---------------- */

const GalleryThumb: React.FC<{output: GalleryOutput; title: string}> = ({
  output,
  title,
}) => {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="gallery-thumb gallery-thumb-fallback" aria-label={title}>
        <PlayIcon size={22} />
      </div>
    );
  }
  return (
    <img
      className="gallery-thumb"
      src={thumbnailUrl(output.output_id)}
      alt={title}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
};

/* ---------------- 卡片 ---------------- */

const GalleryCard: React.FC<{
  output: GalleryOutput;
  onPlay: (output: GalleryOutput) => void;
  onDelete: (output: GalleryOutput) => void;
  deleting: boolean;
  selectionMode: boolean;
  selected: boolean;
  onToggle: (output: GalleryOutput) => void;
}> = ({
  output,
  onPlay,
  onDelete,
  deleting,
  selectionMode,
  selected,
  onToggle,
}) => {
  const title = outputTitle(output);
  const ret = output.total_return_pct;
  const retClass = ret == null ? "" : ret > 0 ? "up" : ret < 0 ? "down" : "";
  return (
    <article
      className="gallery-card"
      data-selected={selected ? "true" : "false"}
      data-published={output.published ? "true" : "false"}
      role="button"
      tabIndex={0}
      onClick={() => (selectionMode ? onToggle(output) : onPlay(output))}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          if (selectionMode) onToggle(output);
          else onPlay(output);
        }
      }}
      aria-label={selectionMode ? `选择 ${title}` : `播放 ${title}`}
    >
      <div className="gallery-thumb-wrap">
        {selectionMode ? (
          <span
            className={`gallery-select-check ${selected ? "checked" : ""}`}
            aria-hidden="true"
          >
            {selected ? "✓" : ""}
          </span>
        ) : null}
        {output.published ? (
          <span className="gallery-published-badge">已发布</span>
        ) : null}
        <GalleryThumb output={output} title={title} />
        <span className="gallery-duration num">
          {formatDuration(output.duration_seconds)}
        </span>
      </div>
      <div className="gallery-card-body">
        <div className="gallery-card-title">
          <strong>{title}</strong>
          <span className={`gallery-return num ${retClass}`}>
            {formatReturn(ret)}
          </span>
        </div>
        <div className="gallery-card-meta">
          <span className="muted">{createdLabel(output.created_at)}</span>
          {output.angle ? (
            <span className="gallery-tag">
              {angleLabels[output.angle] ?? output.angle}
            </span>
          ) : null}
          {output.market ? (
            <span className="gallery-tag">
              {marketLabels[output.market] ?? output.market}
            </span>
          ) : null}
          <a
            className="icon-button gallery-download"
            href={videoUrl(output.output_id)}
            download
            title="下载 MP4"
            aria-label={`下载 ${title}`}
            onClick={(event) => event.stopPropagation()}
          >
            <DownloadIcon size={14} />
          </a>
          <button
            type="button"
            className="icon-button gallery-delete"
            title="删除（同时删除磁盘文件）"
            aria-label={`删除 ${title}`}
            disabled={deleting}
            onClick={(event) => {
              event.stopPropagation();
              onDelete(output);
            }}
          >
            <TrashIcon size={14} />
          </button>
        </div>
      </div>
    </article>
  );
};

/* ---------------- 日期分组 ---------------- */

type GalleryGroup = {key: string; label: string; items: GalleryOutput[]};

const groupOutputs = (outputs: GalleryOutput[]): GalleryGroup[] => {
  const groups = new Map<string, GalleryGroup>();
  for (const output of outputs) {
    const key = dayIsoKey(output.created_at);
    const existing = groups.get(key);
    if (existing) {
      existing.items.push(output);
    } else {
      groups.set(key, {key, label: dayLabel(output.created_at), items: [output]});
    }
  }
  for (const group of groups.values()) {
    group.items.sort(
      (a, b) =>
        parseServerDate(b.created_at).getTime() -
        parseServerDate(a.created_at).getTime(),
    );
  }
  return [...groups.values()].sort((a, b) => (a.key < b.key ? 1 : -1));
};

/* ---------------- 画廊 ---------------- */

export const OutputGallery: React.FC<{outputs: GalleryOutput[]}> = ({
  outputs,
}) => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const market = searchParams.get("market") ?? "all";
  const angle = searchParams.get("angle") ?? "all";
  const pnl = searchParams.get("pnl") ?? "all";
  const query = searchParams.get("q") ?? "";

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [playing, setPlaying] = useState<GalleryOutput | null>(null);
  const [removedIds, setRemovedIds] = useState<Set<string>>(new Set());
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchOpen, setBatchOpen] = useState(false);
  const [accounts, setAccounts] = useState<PublishAccount[]>([]);
  const [batchAccountId, setBatchAccountId] = useState("");
  const [batchName, setBatchName] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState(10);
  const [randomDelayMinutes, setRandomDelayMinutes] = useState(0);
  const [failurePolicy, setFailurePolicy] = useState<"pause" | "skip">("pause");
  const [startAt, setStartAt] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);

  /** 删除成片：确认后调后端真实删除（含磁盘文件），本地立即移除卡片。 */
  const handleDelete = async (output: GalleryOutput) => {
    if (deletingId) return;
    const title = outputTitle(output);
    if (!window.confirm(`确定删除「${title}」吗？\n视频文件将从磁盘真实删除，不可恢复。`)) {
      return;
    }
    setDeletingId(output.output_id);
    setDeleteError(null);
    try {
      await deleteOutput(output.output_id);
      setRemovedIds((current) => new Set(current).add(output.output_id));
      setPlaying((current) =>
        current?.output_id === output.output_id ? null : current,
      );
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeletingId(null);
    }
  };

  /** 筛选状态同步到 URL query（replace 不污染历史记录）。 */
  const updateParam = (key: string, value: string) => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === "all" || value === "") next.delete(key);
        else next.set(key, value);
        return next;
      },
      {replace: true},
    );
  };

  // ESC 关闭播放弹层。
  useEffect(() => {
    if (!playing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPlaying(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playing]);

  const filtered = useMemo(
    () =>
      outputs.filter((output) => {
        if (removedIds.has(output.output_id)) return false;
        if (market !== "all" && output.market !== market) return false;
        if (angle !== "all" && output.angle !== angle) return false;
        if (pnl === "win" && !(output.total_return_pct != null && output.total_return_pct > 0))
          return false;
        if (pnl === "lose" && !(output.total_return_pct != null && output.total_return_pct < 0))
          return false;
        const keyword = query.trim().toLowerCase();
        if (keyword) {
          const haystack =
            `${output.name ?? ""} ${output.symbol ?? ""}`.toLowerCase();
          if (!haystack.includes(keyword)) return false;
        }
        return true;
      }),
    [outputs, removedIds, market, angle, pnl, query],
  );

  const groups = useMemo(() => groupOutputs(filtered), [filtered]);
  const selectableFiltered = useMemo(
    () => filtered.filter((output) => !output.published),
    [filtered],
  );
  const selectedOutputs = useMemo(
    () =>
      selectedIds
        .map((id) => outputs.find((output) => output.output_id === id))
        .filter((output): output is GalleryOutput => Boolean(output)),
    [outputs, selectedIds],
  );

  const toggleSelection = (output: GalleryOutput) => {
    if (output.published) return;
    setSelectedIds((current) =>
      current.includes(output.output_id)
        ? current.filter((id) => id !== output.output_id)
        : [...current, output.output_id],
    );
  };

  const openBatch = async () => {
    setBatchError(null);
    try {
      const result = await api<PublishAccount[]>("/api/publish/accounts");
      setAccounts(result);
      const preferred =
        result.find((account) => account.auto_publish_enabled) ?? result[0];
      setBatchAccountId(preferred?.account_id ?? "");
      setBatchOpen(true);
    } catch (reason) {
      setBatchError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const createBatch = async () => {
    setBatchBusy(true);
    setBatchError(null);
    try {
      const created = await api<PublishBatch>("/api/publish/batches", {
        method: "POST",
        body: JSON.stringify({
          output_ids: selectedIds,
          account_id: batchAccountId,
          name: batchName.trim() || null,
          interval_minutes: intervalMinutes,
          random_delay_minutes: randomDelayMinutes,
          start_at: startAt ? new Date(startAt).toISOString() : null,
          failure_policy: failurePolicy,
        }),
      });
      navigate(`/publish?batch=${created.batch_id}`);
    } catch (reason) {
      setBatchError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBatchBusy(false);
    }
  };

  const toggleGroup = (key: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const currentPackUrl = packUrl({
    market: market !== "all" ? market : undefined,
    angle: angle !== "all" ? angle : undefined,
    pnl: pnl !== "all" ? pnl : undefined,
    q: query.trim() || undefined,
  });

  const renderChips = (
    label: string,
    options: Array<{value: string; label: string}>,
    current: string,
    paramKey: string,
  ) => (
    <div className="gallery-filter">
      <span className="gallery-filter-label">{label}</span>
      <div className="chip-row">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`chip ${current === option.value ? "active" : ""}`}
            onClick={() => updateParam(paramKey, option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="gallery">
      {deleteError ? <ErrorNotice message={deleteError} /> : null}
      {/* 顶部工具条：筛选 + 搜索 + 打包下载 */}
      <div className="gallery-toolbar">
        {renderChips("市场", marketOptions, market, "market")}
        {renderChips("题材", angleOptions, angle, "angle")}
        {renderChips("盈亏", pnlOptions, pnl, "pnl")}
        <input
          className="gallery-search"
          type="search"
          value={query}
          placeholder="搜索股票名或代码"
          aria-label="搜索股票名或代码"
          onChange={(event) => updateParam("q", event.target.value)}
        />
        <a
          className={`mini-button gallery-pack ${filtered.length === 0 ? "disabled" : ""}`}
          href={filtered.length === 0 ? undefined : currentPackUrl}
          download
          title="把符合当前市场/题材筛选的成片打包为 zip 下载"
        >
          <DownloadIcon size={14} />
          打包下载当前筛选（{filtered.length}）
        </a>
        <button
          type="button"
          className={`mini-button ${selectionMode ? "active" : ""}`}
          onClick={() => {
            setSelectionMode((current) => !current);
            if (selectionMode) setSelectedIds([]);
          }}
        >
          {selectionMode ? "退出批量选择" : "批量选择"}
        </button>
        {selectionMode ? (
          <button
            type="button"
            className="mini-button"
            disabled={selectableFiltered.length === 0}
            onClick={() =>
              setSelectedIds(selectableFiltered.map((output) => output.output_id))
            }
          >
            选择当前筛选（{selectableFiltered.length}）
          </button>
        ) : null}
      </div>

      {filtered.length === 0 ? (
        <p className="quiet-line">没有符合筛选条件的成片。</p>
      ) : (
        groups.map((group) => {
          const isCollapsed = collapsed.has(group.key);
          return (
            <div className="gallery-group" key={group.key}>
              <div className="gallery-group-head">
                <button
                  type="button"
                  className="gallery-group-toggle"
                  onClick={() => toggleGroup(group.key)}
                  aria-expanded={!isCollapsed}
                >
                  <span className={`chevron ${isCollapsed ? "closed" : ""}`}>
                    <ChevronIcon size={14} />
                  </span>
                  <strong>{group.label}</strong>
                  <span className="num muted">{group.items.length} 条</span>
                </button>
                <a
                  className="mini-button gallery-pack-day"
                  href={packUrl({date: group.key})}
                  download
                  title={`打包下载 ${group.label} 的全部成片`}
                >
                  <DownloadIcon size={13} />
                  打包本日
                </a>
              </div>
              {!isCollapsed ? (
                <div className="gallery-grid">
                  {group.items.map((output) => (
                    <GalleryCard
                      key={output.output_id}
                      output={output}
                      onPlay={setPlaying}
                      onDelete={handleDelete}
                      deleting={deletingId === output.output_id}
                      selectionMode={selectionMode}
                      selected={selectedIds.includes(output.output_id)}
                      onToggle={toggleSelection}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          );
        })
      )}

      {selectionMode && selectedIds.length ? (
        <div className="gallery-batch-bar">
          <div>
            <strong>已选择 {selectedIds.length} 条</strong>
            <span>
              按当前勾选顺序发布；已发布作品不会被重复加入
            </span>
          </div>
          <button className="button" onClick={() => setSelectedIds([])}>
            清空
          </button>
          <button className="button primary" onClick={() => void openBatch()}>
            批量发布 {selectedIds.length} 条
          </button>
        </div>
      ) : null}

      {batchOpen ? (
        <div className="batch-modal-backdrop" role="dialog" aria-modal="true">
          <div className="batch-modal">
            <div className="panel-title-row">
              <div>
                <span className="eyebrow">BATCH PUBLISH</span>
                <h2>创建批量发布队列</h2>
              </div>
              <button className="icon-button" onClick={() => setBatchOpen(false)}>
                ×
              </button>
            </div>
            {batchError ? <ErrorNotice message={batchError} /> : null}
            <div className="batch-summary">
              <strong>{selectedOutputs.length} 条视频</strong>
              <span>
                最低等待约 {(Math.max(0, selectedOutputs.length - 1) * intervalMinutes)} 分钟；
                每条额外随机 0~{randomDelayMinutes} 分钟（不含上传耗时）
              </span>
            </div>
            <div className="batch-form-grid">
              <label>
                发布账号
                <select
                  value={batchAccountId}
                  onChange={(event) => setBatchAccountId(event.target.value)}
                >
                  <option value="">选择账号</option>
                  {accounts.map((account) => (
                    <option key={account.account_id} value={account.account_id}>
                      {account.display_name}
                      {account.auto_publish_enabled ? "" : "（未开启发布开关）"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                成功后间隔（分钟）
                <input
                  type="number"
                  min={5}
                  max={1440}
                  value={intervalMinutes}
                  onChange={(event) =>
                    setIntervalMinutes(
                      Math.min(1440, Math.max(5, Number(event.target.value) || 5)),
                    )
                  }
                />
              </label>
              <label>
                随机延迟上限（分钟）
                <input
                  type="number"
                  min={0}
                  max={240}
                  value={randomDelayMinutes}
                  onChange={(event) => setRandomDelayMinutes(Math.min(240, Math.max(0, Number(event.target.value) || 0)))}
                />
              </label>
              <label>
                批次名称
                <input
                  value={batchName}
                  placeholder="自动生成"
                  onChange={(event) => setBatchName(event.target.value)}
                />
              </label>
              <label>
                第一条开始时间（留空立即）
                <input
                  type="datetime-local"
                  value={startAt}
                  onChange={(event) => setStartAt(event.target.value)}
                />
              </label>
              <label>
                单条失败时
                <select
                  value={failurePolicy}
                  onChange={(event) =>
                    setFailurePolicy(event.target.value as "pause" | "skip")
                  }
                >
                  <option value="pause">暂停整批，等待处理</option>
                  <option value="skip">记录失败，等待间隔后继续</option>
                </select>
              </label>
            </div>
            <div className="batch-order-preview">
              {selectedOutputs.map((output, index) => (
                <div key={output.output_id}>
                  <span className="num">{index + 1}</span>
                  <img src={coverUrl(output.output_id, "portrait")} alt="" />
                  <strong>{outputTitle(output)}</strong>
                </div>
              ))}
            </div>
            <p className="publish-disclaimer">
              队列始终单线程执行，间隔从上一条确认发布成功后开始计算。
              登录、短信验证或无法确认发布结果时会暂停，不会连续重发。
            </p>
            <div className="publish-inline-actions">
              <button className="button" onClick={() => setBatchOpen(false)}>
                返回
              </button>
              <button
                className="button primary"
                disabled={batchBusy || !batchAccountId || !selectedIds.length}
                onClick={() => void createBatch()}
              >
                {batchBusy ? "正在校验…" : "创建队列并前往授权"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* 播放弹层：ESC / 点遮罩关闭 */}
      {playing ? (
        <div
          className="player-modal"
          role="dialog"
          aria-modal="true"
          onClick={() => setPlaying(null)}
        >
          <div
            className="player-modal-inner"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="player-modal-head">
              <strong>{outputTitle(playing)}</strong>
              <div className="player-downloads">
                <a href={videoUrl(playing.output_id)} download>
                  下载视频
                </a>
                {playing.cover_landscape_path ? (
                  <a href={coverUrl(playing.output_id, "landscape")} download>
                    下载横版封面
                  </a>
                ) : null}
                {playing.cover_portrait_path ? (
                  <a href={coverUrl(playing.output_id, "portrait")} download>
                    下载竖版封面
                  </a>
                ) : null}
              </div>
            </div>
            <div className="player-assets">
              <div className="player-video">
                <span>视频</span>
                <video
                  src={videoUrl(playing.output_id)}
                  controls
                  autoPlay
                  playsInline
                />
              </div>
              {playing.cover_landscape_path ? (
                <figure className="player-cover landscape">
                  <figcaption>横版封面 · 4:3</figcaption>
                  <img
                    src={coverUrl(playing.output_id, "landscape")}
                    alt={`${outputTitle(playing)} 横版封面`}
                  />
                </figure>
              ) : null}
              {playing.cover_portrait_path ? (
                <figure className="player-cover portrait">
                  <figcaption>竖版封面 · 3:4</figcaption>
                  <img
                    src={coverUrl(playing.output_id, "portrait")}
                    alt={`${outputTitle(playing)} 竖版封面`}
                  />
                </figure>
              ) : null}
            </div>
            {playing.publish_title || playing.publish_subtitle ? (
              <section className="player-copy" aria-label="本集发布文案">
                <div className="player-copy-heading">
                  <span className="eyebrow">本集同步生成</span>
                  <strong>标题与副标题</strong>
                </div>
                <div className="player-copy-grid">
                  <div>
                    <span>发布标题</span>
                    <strong>{playing.publish_title ?? "—"}</strong>
                  </div>
                  <div>
                    <span>互动副标题</span>
                    <p>{playing.publish_subtitle ?? "—"}</p>
                  </div>
                </div>
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
};
