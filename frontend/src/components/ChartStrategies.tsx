import { Rocket } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { ChartRunResponse, Deployment, OHLCVBar, StrategyInfo } from "../types";
import type { ChartMarker, PriceLineSpec } from "./CandleChart";

/**
 * Phase AO: strategies on the chart. Each inbuilt strategy gets two switches:
 *
 * * **Chart** - the backend walks the strategy over the candles on screen (the backtest engine,
 *   POST /strategies/{id}/chart-run) and the chart draws every entry and exit, the last signal's
 *   entry / stop / targets, and the strategy's win rate and P&L on that data.
 * * **Deploy** - a PAPER deployment of the strategy on this symbol: on = create (or resume) it, off =
 *   pause it. The trading worker runs it from then on, browser open or not. LIVE stays on the
 *   Autopilot page behind the Go-Live checklist.
 */

const PALETTE = ["#38bdf8", "#f472b6", "#a3e635", "#fbbf24", "#c084fc", "#fb7185", "#34d399", "#60a5fa", "#f97316", "#e879f9", "#22d3ee", "#facc15"];

export function strategyLabel(name: string): string {
  const word = name.replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/)[0] ?? name;
  return word.length > 8 ? word.slice(0, 8) : word;
}

function Switch({ on, onChange, disabled, title }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; title?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled} title={title} onClick={() => onChange(!on)}
            className={`relative h-4 w-7 shrink-0 rounded-full transition-colors disabled:opacity-40 ${on ? "bg-emerald-500" : "bg-slate-600"}`}>
      <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${on ? "left-3.5" : "left-0.5"}`} />
    </button>
  );
}

export interface ChartStrategiesState {
  open: boolean;
  setOpen: (v: boolean) => void;
  onCount: number;
  markers: ChartMarker[];
  priceLines: PriceLineSpec[];
  /** default_params of the strategies shown, so the chart turns their indicators on. */
  shownParams: Record<string, unknown>[];
  panel: JSX.Element | null;
}

export function useChartStrategies({ enabled, candles, symbol, timeframe, exchange = "NSE", deployable = false, onTimeframeChange }: {
  enabled: boolean; candles: OHLCVBar[]; symbol?: string; timeframe?: string; exchange?: string; deployable?: boolean;
  onTimeframeChange?: (tf: string) => void;
}): ChartStrategiesState {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [shown, setShown] = useState<Set<string>>(new Set());
  const [runs, setRuns] = useState<Record<string, ChartRunResponse | { error: string }>>({});
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [deployMsg, setDeployMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !open || strategies.length) return;
    api.listStrategies().then(setStrategies).catch(() => setStrategies([]));
  }, [enabled, open, strategies.length]);

  const sym = (symbol ?? "").trim().toUpperCase();
  const canDeploy = deployable && !!user && !!sym;
  const refreshDeployments = () => { if (canDeploy) api.listDeployments().then(setDeployments).catch(() => {}); };
  useEffect(() => { if (open) refreshDeployments(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [open, canDeploy, sym]);

  // Re-run the shown strategies when a new candle arrives (not on every live tick) or the timeframe changes.
  const lastTs = candles.length ? candles[candles.length - 1].timestamp : "";
  const runKey = `${sym}|${timeframe}|${candles.length}|${lastTs}`;
  const candlesRef = useRef(candles); candlesRef.current = candles;
  useEffect(() => {
    if (!enabled || shown.size === 0 || !timeframe || candlesRef.current.length === 0) return;
    let cancelled = false;
    const t = window.setTimeout(() => {
      for (const id of shown) {
        setBusy((b) => new Set(b).add(id));
        api.strategyChartRun(id, sym || "CHART", timeframe, candlesRef.current)
          .then((r) => { if (!cancelled) setRuns((prev) => ({ ...prev, [id]: r })); })
          .catch((e) => { if (!cancelled) setRuns((prev) => ({ ...prev, [id]: { error: String(e).replace(/^Error:\s*/, "") } })); })
          .finally(() => setBusy((b) => { const n = new Set(b); n.delete(id); return n; }));
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [enabled, shown, runKey, timeframe, sym]);

  const color = (id: string) => PALETTE[Math.max(0, strategies.findIndex((s) => s.id === id)) % PALETTE.length];

  const { markers, priceLines } = useMemo(() => {
    const m: ChartMarker[] = [];
    const lines: PriceLineSpec[] = [];
    for (const id of shown) {
      const r = runs[id];
      if (!r || "error" in r || !r.compatible) continue;
      const c = color(id);
      const tag = strategyLabel(r.strategy_name);
      for (const t of r.trades) {
        const long = t.direction === "LONG";
        m.push({ timestamp: t.entry_time, position: long ? "belowBar" : "aboveBar", color: c, shape: long ? "arrowUp" : "arrowDown", text: `${tag} ${long ? "L" : "S"}` });
        if (t.exit_time) {
          const won = (t.pnl ?? 0) >= 0;
          m.push({ timestamp: t.exit_time, position: long ? "aboveBar" : "belowBar", color: won ? "#22c55e" : "#ef4444", shape: "circle", text: `${won ? "+" : ""}${Math.round(t.pnl ?? 0)}` });
        }
      }
      const s = r.last_signal;
      if (s && s.direction !== "NO_TRADE" && s.entry != null) {
        lines.push({ price: s.entry, color: c, title: `${tag} entry` });
        if (s.stop_loss != null) lines.push({ price: s.stop_loss, color: "#ef4444", title: `${tag} SL` });
        if (s.target1 != null) lines.push({ price: s.target1, color: "#22c55e", title: `${tag} T1` });
      }
    }
    return { markers: m, priceLines: lines };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, runs, strategies]);

  const shownParams = useMemo(() => strategies.filter((s) => shown.has(s.id)).map((s) => ({ ...s.default_params, __id: s.id })), [strategies, shown]);

  const toggleShown = (id: string, on: boolean) => setShown((prev) => { const n = new Set(prev); if (on) n.add(id); else n.delete(id); return n; });

  const depFor = (id: string) => deployments.find((d) => d.strategy_id === id && d.symbol.toUpperCase() === sym && d.status !== "STOPPED");
  const toggleDeploy = async (s: StrategyInfo, on: boolean) => {
    setDeployMsg(null);
    try {
      const dep = depFor(s.id);
      if (on) {
        if (dep && dep.status === "PAUSED") await api.resumeDeployment(dep.id);
        else if (!dep) await api.createDeployment({ strategy_id: s.id, symbol: sym, exchange, timeframe: "1min", mode: "PAPER", instrument_kind: "UNDERLYING" });
        setDeployMsg(`${s.name} is running on ${sym} (PAPER).`);
      } else if (dep) {
        await api.pauseDeployment(dep.id, "paused from the chart");
        setDeployMsg(`${s.name} on ${sym} paused.`);
      }
    } catch (e) { setDeployMsg(String(e).replace(/^Error:\s*/, "")); }
    refreshDeployments();
  };

  const panel = !enabled || !open ? null : (
    <div className="mb-2 rounded-lg border border-border bg-panel2/60 p-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] text-muted">
        <span className="font-bold uppercase tracking-wider text-slate-200">Strategies on this chart</span>
        <span><b className="text-slate-200">Chart</b> draws its entries, exits and last signal on these candles</span>
        {canDeploy
          ? <span><b className="text-slate-200">Deploy</b> runs it on {sym} in PAPER mode via the trading worker (LIVE: Autopilot page)</span>
          : <span>{user ? "Deploy needs a broker symbol chart (Market pulse, Position chart, New tab)" : "Log in to deploy"}</span>}
        {deployMsg && <span className="text-sky-300">{deployMsg}</span>}
      </div>
      {strategies.length === 0 ? <div className="text-xs text-muted">Loading strategies…</div> : (
        <div className="grid gap-1 md:grid-cols-2">
          {strategies.map((s) => {
            const r = runs[s.id];
            const on = shown.has(s.id);
            const dep = depFor(s.id);
            const running = dep?.status === "ACTIVE";
            const incompatible = r && !("error" in r) && !r.compatible;
            const finest = s.timeframes[0];
            return (
              <div key={s.id} className={`flex items-center gap-2 rounded-md border px-2 py-1 text-[11px] ${on ? "border-sky-500/40 bg-panel3" : "border-border"}`}>
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(s.id) }} />
                <span className="min-w-0 flex-1">
                  <span className="font-semibold text-slate-100" title={s.description}>{s.name}</span>
                  <span className="ml-1 text-muted">{s.timeframes.join("/")}</span>
                  {on && busy.has(s.id) && <span className="ml-1 text-muted">running…</span>}
                  {on && r && "error" in r && <span className="block truncate text-rose-300" title={r.error}>{r.error}</span>}
                  {on && incompatible && (
                    <span className="block text-amber-300">{r.reason}
                      {onTimeframeChange && <button onClick={() => onTimeframeChange(finest)} className="ml-1 underline">switch to {finest.replace("min", "m")}</button>}
                    </span>
                  )}
                  {on && r && !("error" in r) && r.compatible && !r.last_signal && r.reason && <span className="block text-amber-300">{r.reason}</span>}
                  {on && r && !("error" in r) && r.compatible && r.last_signal && (
                    <span className="block text-slate-300">
                      {r.total_trades} trades · win {r.win_rate.toFixed(0)}% ·{" "}
                      <b className={r.net_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>{r.net_pnl >= 0 ? "+" : ""}₹{Math.round(r.net_pnl).toLocaleString("en-IN")}</b>
                      {r.last_signal && r.last_signal.direction !== "NO_TRADE" && <span className="text-sky-300"> · now {r.last_signal.direction} @ {r.last_signal.entry}</span>}
                    </span>
                  )}
                </span>
                <label className="flex items-center gap-1 text-muted">Chart <Switch on={on} onChange={(v) => toggleShown(s.id, v)} title="Draw this strategy's trades on the chart" /></label>
                {canDeploy && (
                  <label className="flex items-center gap-1 text-muted" title={running ? "Running (PAPER) - switch off to pause" : dep ? "Paused - switch on to resume" : "Create a PAPER deployment on this symbol"}>
                    <Rocket size={11} className={running ? "text-emerald-300" : ""} />
                    <Switch on={running} onChange={(v) => void toggleDeploy(s, v)} />
                  </label>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  return { open, setOpen, onCount: shown.size, markers, priceLines, shownParams, panel };
}
