import { Award, Lightbulb, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { CoachGroup, CoachReview } from "../types";
import { Card, signClass } from "./ui";

/**
 * Phase AV: the trade coach - your journal read like a mentor would: the numbers, where the money
 * is made and lost, and the habits to fix (revenge trades, overtrading, broken loss limits...).
 */
const money = (v: number | undefined | null) => (v == null ? "-" : `${v < 0 ? "-" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);
const SEV: Record<string, { cls: string; label: string }> = {
  high: { cls: "border-l-down bg-down/[0.06]", label: "Important" },
  medium: { cls: "border-l-warn bg-warn/[0.05]", label: "Watch" },
  low: { cls: "border-l-info bg-surface-2", label: "Note" },
  good: { cls: "border-l-up bg-up/[0.06]", label: "Good" },
};

/** Cumulative P&L after each closed trade - one series, so no legend; hover shows the trade and the running total. */
function EquityLine({ points }: { points: number[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 150, P = 8;
  const series = [0, ...points];
  const min = Math.min(...series), max = Math.max(...series);
  const span = max - min || 1;
  const x = (i: number) => P + (i / Math.max(1, series.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / span) * (H - 2 * P);
  const d = series.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = series[series.length - 1];
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-[150px] w-full" role="img" aria-label={`Cumulative P&L over ${points.length} trades, ending at ${money(last)}`}
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => {
             const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
             const i = Math.round(((e.clientX - r.left) / r.width * W - P) / (W - 2 * P) * (series.length - 1));
             setHover(Math.max(0, Math.min(series.length - 1, i)));
           }}>
        <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="currentColor" className="text-fg-muted" strokeDasharray="3 4" strokeWidth={1} />
        <path d={d} fill="none" style={{ stroke: last >= 0 ? "rgb(var(--up))" : "rgb(var(--down))" }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hover != null && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={P} y2={H - P} style={{ stroke: "rgb(var(--fg-muted))" }} strokeWidth={1} />
            <circle cx={x(hover)} cy={y(series[hover])} r={4} style={{ fill: series[hover] >= 0 ? "rgb(var(--up))" : "rgb(var(--down))", stroke: "rgb(var(--surface))" }} strokeWidth={2} />
          </>
        )}
      </svg>
      {hover != null && (
        <div className="pointer-events-none absolute top-1 rounded border border-border bg-surface-1 px-2 py-1 text-xs text-fg shadow-card"
             style={{ left: `${Math.min(80, (x(hover) / W) * 100)}%` }}>
          {hover === 0 ? "start" : `trade ${hover}`}: <b>{money(series[hover])}</b>
        </div>
      )}
      <div className="mt-1 flex flex-wrap gap-x-4 text-[11px] text-fg-muted">
        <span>Start ₹0</span><span>End <b className="text-fg">{money(last)}</b></span>
        <span>High <b className="text-fg">{money(max)}</b></span><span>Low <b className="text-fg">{money(min)}</b></span>
      </div>
    </div>
  );
}

/** Net P&L per group as signed bars from a centre line; the value is always printed beside the bar. */
function Bars({ rows, labelOf }: { rows: CoachGroup[]; labelOf: (g: CoachGroup) => string }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.net_pnl)));
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map((g) => (
          <tr key={g.key} className="border-t border-border/50" title={`${g.trades} trades · ${g.win_rate}% wins · avg ${money(g.avg_pnl)}`}>
            <td className="w-24 py-1 pr-2 text-fg-muted">{labelOf(g)}</td>
            <td className="py-1">
              <div className="relative h-3">
                <div className="absolute left-1/2 top-0 h-3 w-px bg-fg-muted/40" />
                <div className={`absolute top-0.5 h-2 rounded-sm ${g.net_pnl > 0 ? "bg-up/60" : g.net_pnl < 0 ? "bg-down/60" : "bg-fg-muted/40"}`}
                     style={g.net_pnl >= 0 ? { left: "50%", width: `${(g.net_pnl / max) * 50}%` } : { right: "50%", width: `${(-g.net_pnl / max) * 50}%` }} />
              </div>
            </td>
            <td className="w-20 py-1 text-right font-tabular text-fg">{money(g.net_pnl)}</td>
            <td className="w-16 py-1 text-right text-fg-muted">{g.trades} · {g.win_rate}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function TradeCoach() {
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState<"ALL" | "PAPER" | "LIVE">("ALL");
  const [review, setReview] = useState<CoachReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => {
    setBusy(true);
    api.aiCoach("en", days, mode).then((r) => { setReview(r); setError(null); }).catch((e) => setError(String(e).replace(/^Error:\s*/, ""))).finally(() => setBusy(false));
  };
  useEffect(load, [days, mode]);
  const s = review?.stats;
  const tiles = useMemo(() => s && s.trades ? [
    { label: "Trades", value: String(s.trades), sub: `${s.trading_days} days` },
    { label: "Win rate", value: `${s.win_rate}%`, sub: `${s.wins} / ${s.losses}` },
    { label: "Net P&L", value: money(s.net_pnl), tone: signClass(s.net_pnl, 0), sub: `best ${money(s.best_day)} · worst ${money(s.worst_day)}` },
    { label: "Expectancy per trade", value: money(s.expectancy), tone: signClass(s.expectancy, 0), sub: s.expectancy_r != null ? `${s.expectancy_r}R` : "R: no stop" },
    { label: "Profit factor", value: s.profit_factor != null ? String(s.profit_factor) : "-", sub: `avg ${money(s.avg_win)} / ${money(s.avg_loss)}` },
    { label: "Max drawdown", value: money(-(s.max_drawdown ?? 0)), tone: "text-down", sub: `losing streak ${s.longest_losing_streak}` },
  ] : [], [s]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-fg-muted">Period</span>
        {[7, 30, 90].map((d) => <button key={d} onClick={() => setDays(d)} className={`rounded border px-2 py-0.5 ${days === d ? "border-border bg-surface-2 text-fg-muted" : "border-border text-fg-muted"}`}>{d} days</button>)}
        <span className="ml-2 text-fg-muted">Mode</span>
        {(["ALL", "PAPER", "LIVE"] as const).map((m) => <button key={m} onClick={() => setMode(m)} className={`rounded border px-2 py-0.5 ${mode === m ? "border-border bg-surface-2 text-fg-muted" : "border-border text-fg-muted"}`}>{m === "ALL" ? "All" : m}</button>)}
        <button onClick={load} disabled={busy} className="ml-auto rounded border border-border px-2 py-0.5 text-fg hover:bg-surface-2 disabled:opacity-50"><RefreshCw size={11} className={`mr-1 inline ${busy ? "animate-spin" : ""}`} />Refresh</button>
      </div>
      {error && <div className="text-sm text-down">{error}</div>}
      {review && !s?.trades && (
        <Card><div className="flex items-start gap-3 text-sm text-fg"><Award className="shrink-0 text-fg-muted" />{review.focus[0]}</div></Card>
      )}
      {review && s?.trades ? (
        <>
          <div className="grid gap-3 md:grid-cols-[180px_1fr]">
            <div className="flex flex-col items-center justify-center rounded-xl border border-border bg-surface-2 p-4 text-center">
              <div className="text-[11px] font-bold uppercase tracking-wider text-fg-muted">Discipline grade</div>
              <div className="font-tabular text-5xl font-black text-fg">{review.grade}</div>
              <div className="text-xs text-fg-muted">{review.score}/100</div>
            </div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
              {tiles.map((t) => (
                <div key={t.label} className="rounded-xl border border-border bg-surface-1 px-3 py-2">
                  <div className="text-[11px] uppercase tracking-wider text-fg-muted">{t.label}</div>
                  <div className={`font-tabular text-xl font-extrabold ${t.tone || "text-fg"}`}>{t.value}</div>
                  <div className="text-[11px] text-fg-muted">{t.sub}</div>
                </div>
              ))}
            </div>
          </div>

          {review.focus.length > 0 && (
            <Card title="Patterns in your own trades">
              <ol className="space-y-1.5 text-sm text-fg">
                {review.focus.map((f, i) => <li key={f} className="flex gap-2"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-bold text-fg-muted">{i + 1}</span><span>{f}</span></li>)}
              </ol>
            </Card>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Coach observations">
              <div className="space-y-2">
                {review.flags.map((f) => (
                  <div key={f.id} className={`rounded-lg border border-border border-l-4 p-2.5 ${SEV[f.severity].cls}`}>
                    <div className="flex items-center gap-2 text-sm font-bold text-fg">{f.title}<span className="ml-auto text-[10px] font-normal uppercase tracking-wider text-fg-muted">{SEV[f.severity].label}</span></div>
                    <div className="mt-0.5 text-xs text-fg-muted">{f.text}</div>
                    <div className="mt-1 flex gap-1.5 text-xs text-fg"><Lightbulb size={13} className="mt-0.5 shrink-0 text-warn" />{f.tip}</div>
                  </div>
                ))}
              </div>
            </Card>
            <div className="space-y-4">
              <Card title={`Net P&L after each trade (${review.period.from} → ${review.period.to})`}>
                <EquityLine points={review.equity} />
              </Card>
              <Card title="By strategy">
                <Bars rows={review.by_strategy} labelOf={(g) => g.key} />
              </Card>
              <Card title="By entry hour">
                <Bars rows={review.by_hour} labelOf={(g) => g.key} />
              </Card>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
