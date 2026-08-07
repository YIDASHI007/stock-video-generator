import React, {useState} from "react";
import {useNavigate} from "react-router-dom";

import {api, type Instrument, type Job} from "../api";
import {BackLink, ErrorNotice, PageHeader} from "../components";

type FormState = {
  buyDate: string;
  capital: string;
  dividendPolicy: "ignore" | "cash" | "reinvest";
  shareMode: "fractional" | "integer" | "market_lot";
  feesEnabled: boolean;
  commissionRate: string;
  minimumCommission: string;
  stampDutyRate: string;
  duration: string;
  theme: "dark" | "light";
  voiceEnabled: boolean;
};

const initialForm: FormState = {
  buyDate: "2021-01-04",
  capital: "1000000",
  dividendPolicy: "reinvest",
  shareMode: "fractional",
  feesEnabled: false,
  commissionRate: "0",
  minimumCommission: "0",
  stampDutyRate: "0",
  duration: "60",
  theme: "dark",
  voiceEnabled: false,
};

export const CreatePage: React.FC = () => {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<"" | "CN" | "HK" | "US" | "CRYPTO">("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [selected, setSelected] = useState<Instrument | null>(null);
  const [form, setForm] = useState(initialForm);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({...current, [key]: value}));

  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    setSelected(null);
    try {
      const params = new URLSearchParams({q: query.trim()});
      if (market) params.set("market", market);
      const matches = await api<Instrument[]>(
        `/api/instruments/search?${params.toString()}`,
      );
      setResults(matches);
      if (matches.length === 0) {
        setError("未找到匹配股票，请检查名称、代码或市场。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  const submit = async () => {
    if (!selected) {
      setError("请先搜索并明确选择一只股票。");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const job = await api<Job>("/api/simulations", {
        method: "POST",
        body: JSON.stringify({
          symbol: selected.symbol,
          buy_date: form.buyDate,
          end_date: "latest",
          initial_capital: Number(form.capital),
          capital_currency: selected.currency,
          execution_price: "close",
          non_trading_day_policy: "next_trading_day",
          share_mode: form.shareMode,
          dividend_policy: form.dividendPolicy,
          fee_policy: {
            enabled: form.feesEnabled,
            commission_rate: Number(form.commissionRate),
            minimum_commission: Number(form.minimumCommission),
            stamp_duty_rate: Number(form.stampDutyRate),
          },
          video: {
            duration_seconds: Number(form.duration) || 60,
            fps: 30,
            width: 1920,
            height: 1080,
            theme: form.theme,
            voice_enabled: form.voiceEnabled,
          },
        }),
      });
      navigate("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page create-page">
      <BackLink to="/" label="返回驾驶舱" />
      <PageHeader
        eyebrow="NEW SIMULATION"
        title="新建回测视频"
        description="选择真实股票、买入日期与资金口径。系统会抓取未复权行情和公司行为，确定性计算后生成横屏视频。"
      />

      {error ? <ErrorNotice message={error} /> : null}

      <div className="create-grid">
        <section className="panel span-two">
          <div className="panel-title">
            <span>01</span>
            <div>
              <h2>选择股票</h2>
              <p>搜索结果不唯一时必须手动选择，不会擅自匹配。</p>
            </div>
          </div>
          <div className="search-row">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void search();
              }}
              placeholder="股票名称或代码，例如：贵州茅台、02150.HK、AAPL"
            />
            <select
              value={market}
              onChange={(event) =>
                setMarket(
                  event.target.value as "" | "CN" | "HK" | "US" | "CRYPTO",
                )
              }
            >
              <option value="">全部市场</option>
              <option value="CN">A 股</option>
              <option value="HK">港股</option>
              <option value="US">美股</option>
              <option value="CRYPTO">加密资产</option>
            </select>
            <button className="button secondary" onClick={search} disabled={searching}>
              {searching ? "搜索中…" : "搜索"}
            </button>
          </div>
          {results.length ? (
            <div className="search-results">
              {results.map((item) => (
                <button
                  key={item.symbol}
                  className={selected?.symbol === item.symbol ? "selected" : ""}
                  onClick={() => setSelected(item)}
                >
                  <span className="market-chip">{item.market}</span>
                  <span>
                    <strong>{item.name}</strong>
                    <small>
                      {item.symbol} · {item.exchange}
                    </small>
                  </span>
                  <span className="currency">{item.currency}</span>
                </button>
              ))}
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-title">
            <span>02</span>
            <div>
              <h2>回测口径</h2>
              <p>数字由程序计算，视频不会改写结果。</p>
            </div>
          </div>
          <div className="field-grid">
            <label>
              买入日期
              <input
                type="date"
                value={form.buyDate}
                onChange={(event) => update("buyDate", event.target.value)}
              />
            </label>
            <label>
              初始资金
              <input
                type="number"
                min="1"
                value={form.capital}
                onChange={(event) => update("capital", event.target.value)}
              />
            </label>
            <label>
              资金币种
              <input value={selected?.currency ?? "选择股票后确定"} disabled />
            </label>
            <label>
              分红策略
              <select
                value={form.dividendPolicy}
                onChange={(event) =>
                  update(
                    "dividendPolicy",
                    event.target.value as FormState["dividendPolicy"],
                  )
                }
              >
                <option value="reinvest">红利复投</option>
                <option value="cash">计入现金</option>
                <option value="ignore">忽略分红</option>
              </select>
            </label>
            <label>
              股数模式
              <select
                value={form.shareMode}
                onChange={(event) =>
                  update("shareMode", event.target.value as FormState["shareMode"])
                }
              >
                <option value="fractional">理论碎股</option>
                <option value="integer">整数股</option>
                <option value="market_lot">市场最小交易单位</option>
              </select>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={form.feesEnabled}
                onChange={(event) => update("feesEnabled", event.target.checked)}
              />
              计算手续费
            </label>
          </div>
          {form.feesEnabled ? (
            <div className="field-grid fee-fields">
              <label>
                佣金率
                <input
                  type="number"
                  step="0.0001"
                  value={form.commissionRate}
                  onChange={(event) => update("commissionRate", event.target.value)}
                />
              </label>
              <label>
                最低佣金
                <input
                  type="number"
                  value={form.minimumCommission}
                  onChange={(event) => update("minimumCommission", event.target.value)}
                />
              </label>
              <label>
                印花税率
                <input
                  type="number"
                  step="0.0001"
                  value={form.stampDutyRate}
                  onChange={(event) => update("stampDutyRate", event.target.value)}
                />
              </label>
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-title">
            <span>03</span>
            <div>
              <h2>视频规格</h2>
              <p>横屏 16:9、1920×1080、30 FPS、H.264。</p>
            </div>
          </div>
          <div className="field-grid">
            <label>
              目标时长（秒，15-180）
              <input
                type="number"
                min={15}
                max={180}
                step={5}
                value={form.duration}
                onChange={(event) => update("duration", event.target.value)}
              />
            </label>
            <label>
              主题
              <select
                value={form.theme}
                onChange={(event) =>
                  update("theme", event.target.value as FormState["theme"])
                }
              >
                <option value="dark">深色</option>
                <option value="light">浅色</option>
              </select>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={form.voiceEnabled}
                onChange={(event) => update("voiceEnabled", event.target.checked)}
              />
              尝试配音（未配置时自动生成无配音视频）
            </label>
          </div>
          <div className="spec-strip">
            <span>16:9</span>
            <span>1920 × 1080</span>
            <span>30 FPS</span>
            <span>H.264 MP4</span>
          </div>
        </section>
      </div>

      <div className="submit-bar">
        <div>
          <strong>{selected ? `${selected.name} · ${selected.symbol}` : "尚未选择股票"}</strong>
          <span>生产任务只会使用 Provider 返回的真实数据。</span>
        </div>
        <button
          className="button primary"
          onClick={submit}
          disabled={submitting || !selected}
        >
          {submitting ? "正在创建任务…" : "获取数据并计算"}
        </button>
      </div>
    </div>
  );
};
