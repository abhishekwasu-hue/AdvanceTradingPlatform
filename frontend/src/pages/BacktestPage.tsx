import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import EquityCurveChart from "../components/EquityCurveChart";
import { Card, DemoDataBanner, StatTile } from "../components/ui";
import type { BacktestResult, StrategyInfo } from "../types";
import { generateSampleCandles } from "../utils/sampleData";

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [symbol, setSymbol] = useState("NIFTY");
  const [bars, setBars] = useState(600);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  useEffect(() => {
    api.listStrategies().then((list) => {
      setStrategies(list);
      if (list.length) setStrategyId(list[0].id);
    });
  }, []);

  const selected = useMemo(() => strategies.find((s) => s.id === strategyId), [strategies, strategyId]);

  async function runBacktest() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    try {
      const candles = generateSampleCandles(bars, 100, 11);
      const primaryTf = selected.timeframes[0];
      const res = await api.backtest(selected.id, symbol, primaryTf, candles);
      setResult(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Backtesting</h1>
        <p className="text-sm text-muted">Event-driven simulation with position sizing, SL/target management and realistic costs.</p>
      </div>

      <DemoDataBanner />

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
            className="rounded bg-accent/90 hover:bg-accent text-slate-900 font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Running…" : "Run Backtest"}
          </button>
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

          <Card title="Equity Curve">
            <EquityCurveChart equity={result.equity_curve} />
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
    </div>
  );
}
