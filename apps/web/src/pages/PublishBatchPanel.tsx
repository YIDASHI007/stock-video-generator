import React, {useCallback, useEffect, useMemo, useState} from "react";
import {useSearchParams} from "react-router-dom";

import {api, type PublishBatch} from "../api";
import {ErrorNotice, ProgressBar, formatDateTime} from "../components";
import {usePolling} from "../hooks";

const batchLabels: Record<string, string> = {
  READY: "等待批次授权",
  RUNNING: "正在发布",
  WAITING_INTERVAL: "等待发布间隔",
  PAUSE_REQUESTED: "将在本条完成后暂停",
  PAUSED: "已暂停",
  NEEDS_HUMAN: "需要人工处理",
  COMPLETED: "全部完成",
  PARTIAL_FAILED: "部分完成",
  CANCELLED: "已取消",
};

const itemLabels: Record<string, string> = {
  PENDING: "排队中",
  PUBLISHING: "发布中",
  PUBLISHED: "已发布",
  NEEDS_HUMAN: "需要处理",
  FAILED: "失败",
  SKIPPED: "已跳过",
  CANCELLED: "已取消",
};

const finishedStatuses = new Set(["COMPLETED", "PARTIAL_FAILED", "CANCELLED"]);

export const PublishBatchPanel: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedId = searchParams.get("batch");
  const {data: batches, refresh} = usePolling(
    useCallback(() => api<PublishBatch[]>("/api/publish/batches"), []),
    1500,
  );
  const [selectedId, setSelectedId] = useState("");
  const [interval, setInterval] = useState(10);
  const [randomDelay, setRandomDelay] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const target =
      (requestedId && batches?.find((batch) => batch.batch_id === requestedId)) ||
      batches?.[0];
    if (target && selectedId !== target.batch_id) {
      setSelectedId(target.batch_id);
      setInterval(target.interval_minutes);
      setRandomDelay(target.random_delay_minutes ?? 0);
    }
  }, [batches, requestedId, selectedId]);

  const selected = useMemo(
    () => batches?.find((batch) => batch.batch_id === selectedId) ?? null,
    [batches, selectedId],
  );

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const selectBatch = (batch: PublishBatch) => {
    setSelectedId(batch.batch_id);
    setInterval(batch.interval_minutes);
    setRandomDelay(batch.random_delay_minutes ?? 0);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("batch", batch.batch_id);
      return next;
    });
  };

  const publishedCount = selected?.counts.PUBLISHED ?? 0;
  const progress = selected?.total_count
    ? publishedCount / selected.total_count
    : 0;

  if (!batches?.length) return null;

  return (
    <section className="panel publish-batch-panel">
      <div className="panel-title-row">
        <div>
          <span className="eyebrow">BATCH QUEUE</span>
          <h2>批量发布队列</h2>
        </div>
        {selected ? (
          <span className={`publish-stage stage-${selected.status}`}>
            {batchLabels[selected.status] ?? selected.status}
          </span>
        ) : null}
      </div>
      {error ? <ErrorNotice message={error} /> : null}
      <div className="publish-batch-layout">
        <aside className="publish-batch-list">
          {batches.map((batch) => (
            <button
              key={batch.batch_id}
              className={batch.batch_id === selectedId ? "active" : ""}
              onClick={() => selectBatch(batch)}
            >
              <strong>{batch.name}</strong>
              <span>
                {batch.counts.PUBLISHED ?? 0}/{batch.total_count} ·{" "}
                {batchLabels[batch.status] ?? batch.status}
              </span>
            </button>
          ))}
        </aside>
        {selected ? (
          <div className="publish-batch-detail">
            <div className="publish-batch-metrics">
              <span>
                已发布<strong>{publishedCount}</strong>
              </span>
              <span>
                排队中<strong>{selected.counts.PENDING ?? 0}</strong>
              </span>
              <span>
                失败/待处理
                <strong>
                  {(selected.counts.FAILED ?? 0) +
                    (selected.counts.NEEDS_HUMAN ?? 0)}
                </strong>
              </span>
              <span>
                发布间隔<strong>{selected.interval_minutes} + 0~{selected.random_delay_minutes ?? 0} 分钟</strong>
              </span>
            </div>
            <ProgressBar value={progress} />
            {selected.next_run_at ? (
              <p className="publish-next-run">
                下一条最早执行：{formatDateTime(selected.next_run_at)}
              </p>
            ) : null}
            {selected.error_reason ? (
              <ErrorNotice message={selected.error_reason} />
            ) : null}
            <div className="publish-batch-settings">
              <label>
                后续间隔（分钟）
                <input
                  type="number"
                  min={5}
                  max={1440}
                  value={interval}
                  onChange={(event) =>
                    setInterval(
                      Math.min(1440, Math.max(5, Number(event.target.value) || 5)),
                    )
                  }
                />
              </label>
              <label>
                随机延迟（0~分钟）
                <input
                  type="number"
                  min={0}
                  max={240}
                  value={randomDelay}
                  onChange={(event) => setRandomDelay(Math.min(240, Math.max(0, Number(event.target.value) || 0)))}
                />
              </label>
              <button
                className="button"
                disabled={busy || (interval === selected.interval_minutes && randomDelay === (selected.random_delay_minutes ?? 0))}
                onClick={() =>
                  void run(() =>
                    api(`/api/publish/batches/${selected.batch_id}`, {
                      method: "PATCH",
                      body: JSON.stringify({interval_minutes: interval, random_delay_minutes: randomDelay}),
                    }),
                  )
                }
              >
                保存间隔规则
              </button>
              {selected.status === "READY" ? (
                <button
                  className="button danger"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api(
                        `/api/publish/batches/${selected.batch_id}/approve-start`,
                        {method: "POST"},
                      ),
                    )
                  }
                >
                  授权并启动 {selected.total_count} 条
                </button>
              ) : null}
              {["RUNNING", "WAITING_INTERVAL"].includes(selected.status) ? (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api(`/api/publish/batches/${selected.batch_id}/pause`, {
                        method: "POST",
                      }),
                    )
                  }
                >
                  发布完当前视频后暂停
                </button>
              ) : null}
              {["PAUSED", "NEEDS_HUMAN"].includes(selected.status) ? (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api(`/api/publish/batches/${selected.batch_id}/resume`, {
                        method: "POST",
                      }),
                    )
                  }
                >
                  继续队列
                </button>
              ) : null}
              {!finishedStatuses.has(selected.status) ? (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api(`/api/publish/batches/${selected.batch_id}/cancel`, {
                        method: "POST",
                      }),
                    )
                  }
                >
                  取消剩余任务
                </button>
              ) : null}
            </div>
            <div className="publish-batch-items">
              {selected.items.map((item) => (
                <div key={item.item_id} data-status={item.status}>
                  <span className="num">{item.position}</span>
                  <div>
                    <strong>{item.job?.title ?? item.output_id.slice(0, 8)}</strong>
                    <small>
                      {itemLabels[item.status] ?? item.status}
                      {item.error_reason ? ` · ${item.error_reason}` : ""}
                    </small>
                  </div>
                  <span className="num">
                    {item.job ? `${Math.round(item.job.progress * 100)}%` : "—"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
};
