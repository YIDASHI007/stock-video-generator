import React, {useCallback, useMemo} from "react";
import {Link} from "react-router-dom";

import {api, type GalleryOutput} from "../api";
import {
  EmptyState,
  ErrorNotice,
  PlusIcon,
  RenderIcon,
  parseServerDate,
} from "../components";
import {usePolling} from "../hooks";
import {OutputGallery} from "./OutputGallery";

/* ---------------- 驾驶舱（成片总览） ---------------- */

export const DashboardPage: React.FC = () => {
  const outputsLoader = useCallback(
    () => api<GalleryOutput[]>("/api/outputs"),
    [],
  );
  const {data: outputs, error: outputsError} = usePolling(outputsLoader, 3000);

  const sortedOutputs = useMemo(
    () =>
      [...(outputs ?? [])].sort(
        (a, b) =>
          parseServerDate(b.created_at).getTime() -
          parseServerDate(a.created_at).getTime(),
      ),
    [outputs],
  );

  const stats = useMemo(() => {
    const now = new Date();
    let today = 0;
    let month = 0;
    for (const output of outputs ?? []) {
      const created = parseServerDate(output.created_at);
      if (
        created.getFullYear() === now.getFullYear() &&
        created.getMonth() === now.getMonth()
      ) {
        month += 1;
        if (created.getDate() === now.getDate()) today += 1;
      }
    }
    return {today, month};
  }, [outputs]);

  return (
    <div className="page dashboard">
      {outputsError ? <ErrorNotice message={outputsError} /> : null}

      {/* 1. 顶部状态条 */}
      <section className="dash-stats">
        <div className="stat-card">
          <span className="stat-label">今日成片</span>
          <strong className="stat-num num">{stats.today}</strong>
        </div>
        <div className="stat-card">
          <span className="stat-label">本月成片</span>
          <strong className="stat-num num">{stats.month}</strong>
        </div>
        <div className="stat-card">
          <span className="stat-label">成片总数</span>
          <strong className="stat-num num">{sortedOutputs.length}</strong>
        </div>
        <Link to="/workbench" className="cta-card">
          <RenderIcon size={18} />
          <span>前往工作台生产</span>
        </Link>
        <Link to="/create" className="cta-card">
          <PlusIcon size={18} />
          <span>新建回测视频</span>
        </Link>
      </section>

      {/* 2. 成片库（网格卡片墙 + 筛选 + 打包下载） */}
      <section className="dash-section">
        <div className="section-head">
          <h2>成片库</h2>
          <span className="section-hint num">{sortedOutputs.length} 条成片</span>
        </div>
        {sortedOutputs.length === 0 && !outputsError ? (
          <EmptyState
            title="还没有成片"
            description="完成一次回测并渲染后，最终 MP4 会按日期出现在这里。"
            action={
              <Link className="button primary" to="/workbench">
                前往工作台生产
              </Link>
            }
          />
        ) : (
          <OutputGallery outputs={sortedOutputs} />
        )}
      </section>
    </div>
  );
};
