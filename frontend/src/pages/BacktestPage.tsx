import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import CandleChart, { directionMarker, type ChartMarker } from "../components/CandleChart";
import EquityCurveChart from "../components/EquityCurveChart";
import { Card, DemoDataBanner, StatTile, Disclaimer } from "../components/ui";
import type { BacktestResult, BacktestRunSummary, ExitRules, MonteCarloResult, OHLCVBar, StrategyInfo, WalkForwardResult } from "../types";
import { useAuth } from "../auth/AuthContext";
import { generateSampleCandles } from "../utils/sampleData";

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [symbol, setSymbol] = useState("NIFTY");
  const [bars, setBars] = useState(600);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [chartCandles, setChartCandles] = useState<OHLCVBar[]>([]);
  // Phase J: exit rules, robustness checks and run history.
  const { user } = useAuth();
  const [trail, setTrail] = useState("");
  const [breakEven, setBreakEven] = useState("");
  const [timeExitMin, setTimeExitMin] = useState("");
  const [timeExitAt, setTimeExitAt] = useState("");
  const [monteCarlo, setMonteCarlo] = useState<MonteCarloResult | null>(null);
  const [walkForward, setWalkForward] = useState<WalkForwardResult | null>(null);
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [robustBusy, setRobustBusy] = useState(false);

  function exitRules(): ExitRules | null {
    const r: ExitRules = {};
    if (trail) r.trailing_stop_pct = Number(trail);
    if (breakEven) r.break_even_at_r = Number(breakEven);
    if (timeExitMin) r.time_exit_minutes = Number(timeExitMin);
    if (timeExitAt) r.time_exit_at = timeExitAt;
    return Object.keys(r).length ? r : null;
  }

  function refreshRuns() {
    if (user) api.listBacktests().then(setRuns).catch(() => setRuns([]));
  }

  async function runRobustness() {
    if (!selected) return;
    setRobustBusy(true); setError(null);
    try {
      const primaryTf = selected.timeframes[0];
      const [mc, wf] = await Promise.all([
        api.backtestMonteCarlo(selected.id, symbol, primaryTf, chartCandles, exitRules()),
        api.backtestWalkForward(selected.id, symbol, primaryTf, chartCandles, exitRules()),
      ]);
      setMonteCarlo(mc.monte_carlo);
      setWalkForward(wf);
    } catch (e) {
      setError(String(e));
    } finally {
      setRobustBusy(false);
    }
  }

  useEffect(() => {
    api.listStrategies().then((list) => {
      setStrategies(list);
      if (list.length) setStrategyId(list[0].id);
    });
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(refreshRuns, [user]);

  const selected = useMemo(() => strategies.find((s) => s.id === strategyId), [strategies, strategyId]);

  async function runBacktest() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    try {
      const candles = generateSampleCandles(bars, 100, 11);
      const primaryTf = selected.timeframes[0];
      const res = await api.backtest(selected.id, symbol, primaryTf, candles, exitRules(), "sample");
      setResult(res);
      setChartCandles(candles);
      setMonteCarlo(null);
      setWalkForward(null);
      refreshRuns();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  // One entry marker (direction-coded arrow) and one exit marker (P&L-coded circle) per trade.
  const tradeMarkers: ChartMarker[] = useMemo(() => {
    if (!result) return [];
    const markers: ChartMarker[] = [];
    for (const t of result.trades) {
      markers.push(directionMarker(t.entry_time, t.direction as "LONG" | "SHORT", `Entry ${t.direction}`));
      if (t.exit_time) {
        const won = (t.pnl ?? 0) >= 0;
        markers.push({
          timestamp: t.exit_time,
          position: t.direction === "LONG" ? "aboveBar" : "belowBar",
          color: won ? "#22c55e" : "#ef4444",
          shape: "circle",
          text: `Exit ${t.pnl?.toFixed(0) ?? ""}`,
        });
      }
    }
    return markers;
  }, [result]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-teal-400">Backtesting</h1>
        <p className="text-sm font-semibold text-teal-400/60">Event-driven simulation with position sizing, SL/target management and realistic costs.</p>
      </div>

      <DemoDataBanner />
      <Disclaimer kind="backtest" />

      <Card>
        <div className="grid sm:grid-cols-4 gap-3 items-end">
          <div>
            <label className="block text-xs text-muted mb-1">Strategy</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Symbol</label>
            <input
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Sample bars</label>
            <input
              type="number"
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={bars}
              onChange={(e) => setBars(Number(e.target.value))}
            />
          </div>
          <button
            onClick={runBacktest}
            disabled={!selected || loading}
            className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Running…" : "Run Backtest"}
          </button>
        </div>
        <div className="mt-3 rounded-lg border border-border bg-panel2/40 p-3">
          <div className="text-[11px] font-bold uppercase tracking-wider text-muted mb-2">Dynamic exits (same rules the live monitor applies)</div>
          <div className="grid sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-muted mb-1">Trailing stop %</label>
              <input type="number" step="0.1" min={0} placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={trail} onChange={(e) => setTrail(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Break-even at R</label>
              <input type="number" step="0.1" min={0} placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={breakEven} onChange={(e) => setBreakEven(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Time exit (minutes)</label>
              <input type="number" min={1} placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={timeExitMin} onChange={(e) => setTimeExitMin(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Flat at (HH:MM IST)</label>
              <input placeholder="off" className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={timeExitAt} onChange={(e) => setTimeExitAt(e.target.value)} />
            </div>
          </div>
        </div>
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

      {result && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatTile label="Total Trades" value={result.total_trades} />
            <StatTile label="Win Rate" value={`${result.win_rate.toFixed(1)}%`} />
            <StatTile label="Net P&L" value={result.net_pnl.toFixed(2)} tone={result.net_pnl >= 0 ? "up" : "down"} />
            <StatTile label="Max Drawdown" value={result.max_drawdown.toFixed(2)} tone="down" />
            <StatTile label="Profit Factor" value={result.profit_factor?.toFixed(2) ?? "-"} />
            <StatTile label="Avg Win" value={result.avg_win.toFixed(2)} tone="up" />
            <StatTile label="Avg Loss" value={result.avg_loss.toFixed(2)} tone="down" />
            <StatTile label="Expectancy" value={result.expectancy.toFixed(2)} />
          </div>

          <Card title="Price Chart — trade entries &amp; exits">
            <CandleChart candles={chartCandles} markers={tradeMarkers} />
          </Card>

          <Card title="Equity Curve">
            <EquityCurveChart equity={result.equity_curve} />
          </Card>

          {result.analytics && (
            <Card title="Analytics">
              <div className="grid sm:grid-cols-4 gap-3 mb-3">
                <StatTile label="CAGR" value={result.analytics.ratios.cagr_pct != null ? `${result.analytics.ratios.cagr_pct}%` : "-"} />
                <StatTile label="Sharpe / Sortino" value={`${result.analytics.ratios.sharpe ?? "-"} / ${result.analytics.ratios.sortino ?? "-"}`} />
                <StatTile label="Streaks (W / L)" value={`${result.analytics.streaks.max_consecutive_wins} / ${result.analytics.streaks.max_consecutive_losses}`} />
                <StatTile label="Avg hold" value={result.analytics.holding_minutes.avg != null ? `${result.analytics.holding_minutes.avg} min` : "-"} />
              </div>
              <div className="grid md:grid-cols-2 gap-4 text-xs">
                {([["Monthly", result.analytics.monthly], ["Day of week", result.analytics.day_of_week], ["Hour of day", result.analytics.hour_of_day], ["Exit reasons", result.analytics.exit_reasons]] as const).map(([title, rows]) => (
                  <div key={title}>
                    <div className="text-[10px] uppercase tracking-wide text-muted mb-1">{title}</div>
                    {rows.length === 0 ? <div className="text-muted">-</div> : (
                      <table className="w-full">
                        <tbody>
                          {rows.map((r) => (
                            <tr key={r.key} className="border-t border-border">
                              <td className="py-0.5 pr-2 text-slate-300">{r.key}</td>
                              <td className="py-0.5 pr-2 text-muted">{r.trades} trades</td>
                              <td className="py-0.5 pr-2 text-muted">{r.win_rate}% win</td>
                              <td className={`py-0.5 text-right ${r.pnl >= 0 ? "text-accent" : "text-danger"}`}>{r.pnl.toFixed(0)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                ))}
              </div>
              <div className="mt-3 text-[11px] text-muted">
                Charges {result.analytics.costs.total_charges.toFixed(0)} ({result.analytics.costs.charges_pct_of_gross ?? "-"}% of gross)
                {result.analytics.slippage.avg_per_unit != null ? ` · avg slippage ${result.analytics.slippage.avg_per_unit}/unit` : ""}
                {result.run_id ? ` · saved as run #${result.run_id}` : ""}
              </div>
            </Card>
          )}

          <Card title="Robustness">
            <div className="flex flex-wrap items-center gap-3 text-xs mb-3">
              <button onClick={runRobustness} disabled={robustBusy || !result} className="rounded border border-border hover:bg-panel2 px-3 py-1 text-slate-200 disabled:opacity-50">
                {robustBusy ? "Running…" : "Run Monte Carlo + walk-forward"}
              </button>
              <span className="text-muted">Monte Carlo reshuffles the trade sequence; walk-forward reruns the same parameters on consecutive windows.</span>
            </div>
            {monteCarlo && monteCarlo.runs > 0 && monteCarlo.final_pnl && monteCarlo.max_drawdown && (
              <div className="grid sm:grid-cols-4 gap-3 text-xs mb-3">
                <StatTile label="P&L p5 / p50 / p95" value={`${monteCarlo.final_pnl.p5.toFixed(0)} / ${monteCarlo.final_pnl.p50.toFixed(0)} / ${monteCarlo.final_pnl.p95.toFixed(0)}`} />
                <StatTile label="Max DD p50 / p95" value={`${monteCarlo.max_drawdown.p50.toFixed(0)} / ${monteCarlo.max_drawdown.p95.toFixed(0)}`} tone="down" />
                <StatTile label="P(loss)" value={`${monteCarlo.probability_of_loss_pct}%`} tone={(monteCarlo.probability_of_loss_pct ?? 0) > 30 ? "down" : "up"} />
                <StatTile label="P(DD > original)" value={`${monteCarlo.probability_dd_exceeds_original_pct}%`} />
              </div>
            )}
            {monteCarlo && monteCarlo.runs === 0 && <div className="text-xs text-muted">{monteCarlo.note}</div>}
            {walkForward && (
              walkForward.folds === 0 ? <div className="text-xs text-muted">{walkForward.note}</div> : (
                <div className="text-xs">
                  <div className="mb-1 text-slate-300">Consistency {walkForward.consistency_pct}% ({walkForward.profitable_windows}/{walkForward.folds} windows profitable) · mean window P&L {walkForward.mean_window_pnl}</div>
                  <table className="w-full">
                    <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left"><th className="py-1 pr-3">Window</th><th className="py-1 pr-3">Bars</th><th className="py-1 pr-3">Trades</th><th className="py-1 pr-3">Net P&L</th><th className="py-1 pr-3">Win %</th><th className="py-1 pr-3">Max DD</th></tr></thead>
                    <tbody>
                      {walkForward.windows.map((w) => (
                        <tr key={w.window} className="border-t border-border">
                          <td className="py-1 pr-3">{w.window}</td><td className="py-1 pr-3">{w.bars}</td><td className="py-1 pr-3">{w.trades}</td>
                          <td className={`py-1 pr-3 ${w.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>{w.net_pnl.toFixed(0)}</td>
                          <td className="py-1 pr-3">{w.win_rate}%</td><td className="py-1 pr-3">{w.max_drawdown.toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="mt-1 text-muted">{walkForward.note}</div>
                </div>
              )
            )}
          </Card>

          <Card title={`Trades (${result.trades.length})`}>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted uppercase text-[10px] tracking-wide">
                  <tr className="text-left">
                    <th className="py-1 pr-3">Direction</th>
                    <th className="py-1 pr-3">Entry</th>
                    <th className="py-1 pr-3">Exit</th>
                    <th className="py-1 pr-3">Qty</th>
                    <th className="py-1 pr-3">Reason</th>
                    <th className="py-1 pr-3">P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="py-1 pr-3">{t.direction}</td>
                      <td className="py-1 pr-3">{t.entry_price.toFixed(2)}</td>
                      <td className="py-1 pr-3">{t.exit_price?.toFixed(2) ?? "-"}</td>
                      <td className="py-1 pr-3">{t.quantity}</td>
                      <td className="py-1 pr-3">{t.exit_reason ?? "-"}</td>
                      <td className={`py-1 pr-3 ${(t.pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}`}>
                        {t.pnl?.toFixed(2) ?? "-"}
                      </td>
                    </tr>
                  ))}
                  {result.trades.length === 0 && (
                    <tr>
                      <td colSpan={6} className="py-3 text-center text-muted">
                        No trades were triggered on this sample dataset.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {user && runs.length > 0 && (
        <Card title={`Backtest history (${runs.length})`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide">
                <tr className="text-left"><th className="py-1 pr-3">When</th><th className="py-1 pr-3">Strategy</th><th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Data</th><th className="py-1 pr-3">Exits</th><th className="py-1 pr-3">Trades</th><th className="py-1 pr-3">Net P&L</th><th className="py-1 pr-3">Win %</th><th className="py-1 pr-3">Max DD</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted whitespace-nowrap">{r.created_at ? new Date(r.created_at).toLocaleString() : "-"}</td>
                    <td className="py-1 pr-3 text-slate-200">{r.strategy_id}</td>
                    <td className="py-1 pr-3">{r.symbol} · {r.base_timeframe}</td>
                    <td className="py-1 pr-3 text-muted">{r.data_source} · {r.bars} bars · engine v{r.engine_version}</td>
                    <td className="py-1 pr-3 text-muted">{r.exit_rules ? Object.entries(r.exit_rules).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") : "-"}</td>
                    <td className="py-1 pr-3">{r.total_trades ?? "-"}</td>
                    <td className={`py-1 pr-3 ${(r.net_pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}`}>{r.net_pnl?.toFixed(0) ?? "-"}</td>
                    <td className="py-1 pr-3">{r.win_rate ?? "-"}%</td>
                    <td className="py-1 pr-3">{r.max_drawdown?.toFixed(0) ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
