import { useMemo, useState } from "react";
import { api } from "../api/client";
import { Card, Disclaimer, StatTile } from "../components/ui";
import { DataSourceBar, useCandleSource } from "../components/DataSource";
import type { FactorTable, QuantRisk, QuantSymbolInput } from "../types";

const FACTOR_LABELS: Record<string, string> = {
  momentum: "Momentum", reversal: "Reversal", low_volatility: "Low volatility", trend: "Trend", liquidity: "Liquidity", value: "Value", quality: "Quality",
};
const DEFAULT_WEIGHTS: Record<string, number> = { momentum: 30, reversal: 10, low_volatility: 20, trend: 20, liquidity: 10, value: 5, quality: 5 };

function zClass(z: number | null): string {
  if (z == null) return "text-muted";
  if (z >= 1) return "text-accent font-bold";
  if (z <= -1) return "text-danger font-bold";
  return "text-slate-200";
}

function corrClass(v: number | null): string {
  if (v == null) return "bg-panel2";
  if (v > 0.7) return "bg-emerald-600/40";
  if (v > 0.3) return "bg-emerald-600/15";
  if (v < -0.3) return "bg-rose-600/25";
  return "bg-panel2";
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? "-" : `${(v * 100).toFixed(d)}%`);

/** Phase Z: cross-sectional factor scores and a descriptive risk model on a watchlist. */
export default function QuantPage() {
  const [watchlist, setWatchlist] = useState("NIFTY, BANKNIFTY, RELIANCE, TCS, HDFCBANK, INFY, ITC, SBIN");
  const [weights, setWeights] = useState<Record<string, number>>(DEFAULT_WEIGHTS);
  const [benchmark, setBenchmark] = useState("NIFTY");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [table, setTable] = useState<FactorTable | null>(null);
  const [risk, setRisk] = useState<QuantRisk | null>(null);
  const [weightsMode, setWeightsMode] = useState<"equal" | "inverse_volatility" | "risk_parity">("equal");
  const [timeframe, setTimeframe] = useState("15min");   // Phase AA: bar size for broker candles
  const source = useCandleSource(30);
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);

  const symbols = useMemo(() => watchlist.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean), [watchlist]);

  async function inputs(): Promise<QuantSymbolInput[]> {
    const fetched = await source.fetch(symbols, timeframe, { count: 320 });
    setDataWarnings(fetched.warnings);
    return Object.entries(fetched.candles).map(([symbol, candles]) => ({ symbol, candles }));
  }

  async function run() {
    if (symbols.length < 2) { setError("Enter at least two symbols."); return; }
    setBusy(true); setError(null);
    try {
      const universe = await inputs();
      if (universe.length < 2) throw new Error("Fewer than two symbols returned candles.");
      const [t, r] = await Promise.all([
        api.quantFactors(universe, weights),
        api.quantRisk(universe, undefined, symbols.includes(benchmark) ? benchmark : symbols[0]),
      ]);
      setTable(t); setRisk(r);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }

  const chosenWeights = risk ? (weightsMode === "equal" ? risk.weights_used : risk.suggested[weightsMode]) : {};
  const chosenPortfolio = risk ? (weightsMode === "equal" ? risk.portfolio : risk.suggested[`${weightsMode}_portfolio` as "inverse_volatility_portfolio" | "risk_parity_portfolio"]) : null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-violet-400">Factor Lab</h1>
        <p className="text-sm font-semibold text-violet-400/60">Cross-sectional factor scores and a descriptive risk model on your watchlist. Ranks and statistics of the supplied window; never a signal or an order.</p>
      </div>
      <DataSourceBar source={source} note="Value and quality need fundamentals, absent in both modes until supplied." />
      {dataWarnings.map((w, i) => <div key={i} className="text-xs text-amber-300">{w}</div>)}
      <Disclaimer kind="signals" />

      <Card title="Universe and factor weights">
        <div className="grid sm:grid-cols-[1fr_120px_160px] gap-3 mb-3">
          <div>
            <label className="block text-xs text-muted mb-1">Symbols (comma-separated)</label>
            <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={watchlist} onChange={(e) => setWatchlist(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Bar size</label>
            <select className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {["1min", "5min", "15min", "60min", "day"].map((tf) => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Benchmark (for betas)</label>
            <input className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm" value={benchmark} onChange={(e) => setBenchmark(e.target.value.toUpperCase())} />
          </div>
        </div>
        <div className="grid sm:grid-cols-4 lg:grid-cols-7 gap-3 text-xs">
          {Object.keys(FACTOR_LABELS).map((f) => (
            <div key={f}>
              <label className="block text-[11px] text-muted mb-0.5">{FACTOR_LABELS[f]} <span className="text-slate-300">{weights[f]}%</span></label>
              <input type="range" min={0} max={60} step={5} value={weights[f]} onChange={(e) => setWeights({ ...weights, [f]: Number(e.target.value) })} className="w-full" />
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button onClick={run} disabled={busy} className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50">{busy ? "Computing…" : "Score universe"}</button>
          <span className="text-[11px] text-muted">Weights are renormalised; a factor a symbol has no data for carries no weight in its composite. Value and quality need fundamentals (PE, PB, ROE, debt/equity, growth), absent in the sample data.</span>
        </div>
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

      {table && (
        <Card title={`Factor table (${table.universe} symbols)`}>
          {table.warnings.map((w, i) => <div key={i} className="text-xs text-amber-300 mb-1">{w}</div>)}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted uppercase text-[10px] tracking-wide"><tr className="text-left">
                <th className="py-1 pr-3">#</th><th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Bucket</th><th className="py-1 pr-3">Composite</th>
                {table.factors.map((f) => <th key={f} className="py-1 pr-3">{FACTOR_LABELS[f] ?? f}</th>)}<th className="py-1 pr-3">Coverage</th>
              </tr></thead>
              <tbody>
                {table.rows.map((r) => (
                  <tr key={r.symbol} className="border-t border-border">
                    <td className="py-1 pr-3 text-muted">{r.rank ?? "-"}</td>
                    <td className="py-1 pr-3 font-bold text-slate-200">{r.symbol} <span className="text-muted font-normal">{r.close.toFixed(2)}</span></td>
                    <td className="py-1 pr-3"><span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-bold ${r.bucket === "LONG" ? "border-emerald-500/40 text-emerald-400" : r.bucket === "SHORT" ? "border-rose-500/40 text-rose-400" : "border-border text-muted"}`}>{r.bucket}</span></td>
                    <td className={`py-1 pr-3 ${zClass(r.composite)}`}>{r.composite?.toFixed(2) ?? "-"}</td>
                    {table.factors.map((f) => <td key={f} className={`py-1 pr-3 ${zClass(r.z[f])}`} title={r.raw[f] != null ? `raw ${r.raw[f]?.toFixed(4)}` : "no data"}>{r.z[f]?.toFixed(2) ?? "-"}</td>)}
                    <td className="py-1 pr-3 text-muted">{r.coverage}/{table.factors.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-muted">z-scores across this universe, winsorised at ±3. LONG/SHORT = top/bottom quintile by composite. Momentum skips the last bars; low volatility and reversal are sign-flipped so higher is more attractive.</div>
        </Card>
      )}

      {risk && (
        <Card title="Risk model">
          {risk.warnings.map((w, i) => <div key={i} className="text-xs text-amber-300 mb-1">{w}</div>)}
          <div className="flex flex-wrap items-center gap-2 text-xs mb-3">
            <span className="text-muted">Weights</span>
            {(["equal", "inverse_volatility", "risk_parity"] as const).map((m) => (
              <button key={m} onClick={() => setWeightsMode(m)} className={`px-2 py-0.5 rounded border border-border ${weightsMode === m ? "bg-panel2 text-slate-200" : "text-muted"}`}>{m.replace("_", " ")}</button>
            ))}
            <span className="text-muted">· {risk.bars} shared bars</span>
          </div>
          {chosenPortfolio && (
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
              <StatTile label="Portfolio vol (annual)" value={pct(chosenPortfolio.vol_annual)} />
              <StatTile label="1-bar VaR 95%" value={pct(chosenPortfolio.var_95, 2)} tone="down" />
              <StatTile label="CVaR 95%" value={pct(chosenPortfolio.cvar_95, 2)} tone="down" />
              <StatTile label="Max drawdown (window)" value={chosenPortfolio.max_drawdown_pct != null ? `${chosenPortfolio.max_drawdown_pct.toFixed(1)}%` : "-"} tone="down" />
              <StatTile label="Diversification ratio" value={chosenPortfolio.diversification_ratio?.toFixed(2) ?? "-"} />
            </div>
          )}
          <div className="grid lg:grid-cols-2 gap-4 text-xs">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted mb-1">Weights, volatility, beta vs {risk.benchmark ?? "-"}</div>
              <table className="w-full"><thead className="text-muted text-[10px] uppercase"><tr className="text-left"><th className="py-1 pr-2">Symbol</th><th className="py-1 pr-2">Weight</th><th className="py-1 pr-2">Vol</th><th className="py-1 pr-2">Beta</th><th className="py-1 pr-2">Max DD</th></tr></thead>
                <tbody>
                  {risk.symbols.map((s) => (
                    <tr key={s} className="border-t border-border/60"><td className="py-0.5 pr-2 text-slate-200">{s}</td><td className="py-0.5 pr-2">{pct(chosenWeights[s] ?? 0)}</td><td className="py-0.5 pr-2">{pct(risk.volatility[s])}</td><td className="py-0.5 pr-2">{risk.betas[s]?.toFixed(2) ?? "-"}</td><td className="py-0.5 pr-2">{risk.max_drawdown_pct[s]?.toFixed(1) ?? "-"}%</td></tr>
                  ))}
                </tbody></table>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted mb-1">Correlation of log returns</div>
              <div className="overflow-x-auto"><table className="text-[10px]"><thead><tr><th></th>{risk.symbols.map((s) => <th key={s} className="px-1 text-muted font-normal">{s.slice(0, 6)}</th>)}</tr></thead>
                <tbody>
                  {risk.symbols.map((a) => (
                    <tr key={a}><td className="pr-1 text-muted">{a.slice(0, 8)}</td>{risk.symbols.map((b) => <td key={b} className={`px-1 py-0.5 text-center ${corrClass(risk.correlation[a]?.[b] ?? null)}`}>{risk.correlation[a]?.[b]?.toFixed(2) ?? "-"}</td>)}</tr>
                  ))}
                </tbody></table></div>
            </div>
          </div>
          <div className="mt-2 text-[11px] text-muted">{risk.disclaimer}</div>
        </Card>
      )}
    </div>
  );
}
