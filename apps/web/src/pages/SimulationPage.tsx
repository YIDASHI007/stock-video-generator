import React, {useCallback} from "react";
import {Link, useParams} from "react-router-dom";

import {API_BASE, api, type SimulationDetail} from "../api";
import {EmptyState, ErrorNotice, PageHeader, BackLink} from "../components";
import {usePolling} from "../hooks";

const number = (value: number | undefined, digits = 2): string =>
  value === undefined
    ? "—"
    : value.toLocaleString("zh-CN", {maximumFractionDigits: digits});

const HistoryCurve: React.FC<{
  series: NonNullable<SimulationDetail["series"]>;
}> = ({series}) => {
  const width = 960;
  const height = 280;
  const values = series.map((point) => point.portfolio_value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = Math.max(1, maximum - minimum);
  const points = series
    .map((point, index) => {
      const x = (index / Math.max(1, series.length - 1)) * width;
      const y = height - ((point.portfolio_value - minimum) / span) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <div className="history-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="历史资产曲线">
        <defs>
          <linearGradient id="curveFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#48e3a1" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#48e3a1" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
          <line
            key={ratio}
            x1="0"
            x2={width}
            y1={height * ratio}
            y2={height * ratio}
            stroke="#22313b"
            strokeWidth="1"
          />
        ))}
        <polygon
          points={`0,${height} ${points} ${width},${height}`}
          fill="url(#curveFill)"
        />
        <polyline
          points={points}
          fill="none"
          stroke="#48e3a1"
          strokeWidth="4"
          strokeLinejoin="round"
        />
      </svg>
      <div>
        <span>{series[0]?.date}</span>
        <strong>
          {number(minimum)} — {number(maximum)}
        </strong>
        <span>{series.at(-1)?.date}</span>
      </div>
    </div>
  );
};

export const SimulationPage: React.FC = () => {
  const {simulationId = ""} = useParams();
  const loader = useCallback(
    () => api<SimulationDetail>(`/api/simulations/${simulationId}`),
    [simulationId],
  );
  const {data, error, loading} = usePolling(loader);

  return (
    <div className="page">
      <BackLink to="/" label="返回驾驶舱" />
      <PageHeader
        eyebrow="SIMULATION DETAIL"
        title={data?.name ? `${data.name} 的持有回测` : "回测详情"}
        description={data ? `${data.symbol} · ${data.simulation_id}` : "正在读取持久化结果…"}
        actions={
          data?.summary ? (
            <>
              <a
                className="button secondary"
                href={`${API_BASE}/api/simulations/${simulationId}/download`}
              >
                下载 JSON
              </a>
              <Link
                className="button primary"
                to={`/simulations/${simulationId}/preview`}
              >
                预览并渲染
              </Link>
            </>
          ) : null
        }
      />
      {error ? <ErrorNotice message={error} /> : null}
      {!loading && data && !data.summary ? (
        <EmptyState
          title="回测仍在执行"
          description="结果尚未写入；任务完成后此页面会自动出现真实摘要。"
          action={<Link to="/jobs">查看任务进度</Link>}
        />
      ) : null}
      {data?.summary ? (
        <>
          <div className="metric-grid">
            <article>
              <span>最终资产</span>
              <strong>{number(data.summary.final_value)}</strong>
              <small>{String(data.request.capital_currency)}</small>
            </article>
            <article className={data.summary.total_return_pct >= 0 ? "positive" : "negative"}>
              <span>累计收益率</span>
              <strong>
                {data.summary.total_return_pct >= 0 ? "+" : ""}
                {number(data.summary.total_return_pct)}%
              </strong>
              <small>从实际买入日计算</small>
            </article>
            <article>
              <span>最大回撤</span>
              <strong>{number(data.summary.max_drawdown_pct)}%</strong>
              <small>基于每日总资产</small>
            </article>
            <article>
              <span>最终股数</span>
              <strong>{number(data.summary.final_shares, 6)}</strong>
              <small>现金 {number(data.summary.final_cash)}</small>
            </article>
          </div>
          <div className="detail-grid">
            <section className="panel">
              <div className="panel-heading">
                <h2>计算假设</h2>
              </div>
              <dl className="detail-list">
                <div>
                  <dt>请求买入日</dt>
                  <dd>{String(data.request.buy_date)}</dd>
                </div>
                <div>
                  <dt>实际买入日</dt>
                  <dd>{data.summary.actual_buy_date}</dd>
                </div>
                <div>
                  <dt>成交价格</dt>
                  <dd>{number(data.summary.buy_price)}</dd>
                </div>
                <div>
                  <dt>初始资金</dt>
                  <dd>{number(Number(data.request.initial_capital))}</dd>
                </div>
                <div>
                  <dt>股数模式</dt>
                  <dd>{String(data.request.share_mode)}</dd>
                </div>
                <div>
                  <dt>分红策略</dt>
                  <dd>{String(data.request.dividend_policy)}</dd>
                </div>
                <div>
                  <dt>累计分红</dt>
                  <dd>{number(data.summary.dividend_total)}</dd>
                </div>
                <div>
                  <dt>累计费用</dt>
                  <dd>{number(data.summary.total_fees)}</dd>
                </div>
              </dl>
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>数据与产物</h2>
              </div>
              <dl className="detail-list paths">
                {data.artifacts
                  ? Object.entries(data.artifacts).map(([key, value]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd title={value}>{value}</dd>
                      </div>
                    ))
                  : null}
              </dl>
            </section>
          </div>
          {data.series?.length ? (
            <section className="panel detail-section">
              <div className="panel-heading">
                <h2>历史资产曲线</h2>
                <span>{data.series.length} 个真实交易日</span>
              </div>
              <HistoryCurve series={data.series} />
            </section>
          ) : null}
          <div className="detail-grid">
            <section className="panel">
              <div className="panel-heading">
                <h2>数据来源与校验</h2>
              </div>
              <dl className="detail-list">
                <div>
                  <dt>Provider</dt>
                  <dd>{data.source?.provider ?? "—"}</dd>
                </div>
                <div>
                  <dt>抓取时间</dt>
                  <dd>{data.source?.fetched_at ?? "—"}</dd>
                </div>
                <div>
                  <dt>缓存</dt>
                  <dd>{data.source?.cache_hit ? "命中缓存" : "本次真实抓取"}</dd>
                </div>
                <div>
                  <dt>数据范围</dt>
                  <dd>
                    {data.validation?.data_start} — {data.validation?.data_end}
                  </dd>
                </div>
                <div>
                  <dt>交易日</dt>
                  <dd>{data.validation?.trading_days ?? "—"}</dd>
                </div>
                <div>
                  <dt>校验结果</dt>
                  <dd>{data.validation?.valid ? "通过" : "未通过"}</dd>
                </div>
              </dl>
              {data.validation?.warnings.map((warning) => (
                <p className="validation-warning" key={warning}>
                  {warning}
                </p>
              ))}
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>分红与拆合股记录</h2>
              </div>
              <div className="event-list">
                {data.events
                  ?.filter((event) => event.event_type !== "buy")
                  .map((event, index) => (
                    <article key={`${event.date}-${event.event_type}-${index}`}>
                      <time>{event.date}</time>
                      <strong>{event.description}</strong>
                      <span>
                        {event.amount == null ? "股数调整" : number(event.amount)}
                      </span>
                    </article>
                  ))}
                {!data.events?.some((event) => event.event_type !== "buy") ? (
                  <p className="fine-print">回测区间内没有已记录的公司行为。</p>
                ) : null}
              </div>
            </section>
          </div>
        </>
      ) : null}
    </div>
  );
};
