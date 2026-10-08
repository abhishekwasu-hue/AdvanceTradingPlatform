import { Brain, Gauge, Globe2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MarketMemory, SentimentRead } from "../types";
import { Card } from "./ui";

/**
 * Phase AR: what the Copilot already knows about the market - read by the worker every 15 minutes
 * through your broker: each watched symbol's bias, regime and structure, how the bias moved over
 * the last sessions, and the market cues (India VIX, index day change). Phase AU: the global cues
 * (US futures, Asia, crude, dollar, rupee) from free, delayed public data, read before the open too.
 */
const GLOBAL: Record<string, { en: string; inverse?: boolean; neutral?: boolean }> = {
  SP500_FUT: { en: "S&P 500 futures" }, NASDAQ_FUT: { en: "Nasdaq futures" }, SP500: { en: "S&P 500" }, NASDAQ: { en: "Nasdaq" },
  NIKKEI: { en: "Nikkei (Japan)" }, HANG_SENG: { en: "Hang Seng" }, BRENT: { en: "Brent crude", inverse: true },
  GOLD: { en: "Gold", neutral: true }, DXY: { en: "Dollar index", inverse: true }, USDINR: { en: "USD/INR", inverse: true },
  US10Y: { en: "US 10Y yield", inverse: true },
};

function globalCls(key: string, change: number): string {
  const g = GLOBAL[key];
  if (!g || g.neutral || Math.abs(change) < 0.05) return "text-slate-200";
  const goodForIndia = g.inverse ? change < 0 : change > 0;
  return goodForIndia ? "text-emerald-300" : "text-rose-300";
}
const BIAS: Record<string, { en: string; cls: string }> = {
  BULLISH: { en: "up", cls: "text-emerald-300" },
  BEARISH: { en: "down", cls: "text-rose-300" },
  NEUTRAL: { en: "flat", cls: "text-amber-200" },
};
const REGIME: Record<string, string> = {
  TRENDING_UP: "trending up", TRENDING_DOWN: "trending down", RANGING: "sideways", VOLATILE: "volatile", QUIET: "quiet", UNKNOWN: "-",
};

function vixLabel(v: number): { text: string; cls: string } {
  if (v < 12) return { text: "very calm", cls: "text-sky-300" };
  if (v < 16) return { text: "normal", cls: "text-emerald-300" };
  if (v < 20) return { text: "elevated", cls: "text-amber-300" };
  return { text: "high fear", cls: "text-rose-300" };
}

function minutesAgo(iso: string | null): number | null {
  return iso ? Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000)) : null;
}
function ago(iso: string | null): string {
  const min = minutesAgo(iso);
  if (min == null) return "-";
  return min < 60 ? `${min} min ago` : min < 60 * 48 ? `${Math.round(min / 60)} h ago` : `${Math.round(min / 1440)} days ago`;
}

const COMPONENT: Record<string, string> = { pcr: "PCR/OI", vix: "VIX", breadth: "Breadth", global: "Global", fii_dii: "FII/DII" };
const LABEL: Record<string, { text: string; cls: string }> = {
  RISK_ON: { text: "risk-on", cls: "text-emerald-300" },
  RISK_OFF: { text: "risk-off", cls: "text-rose-300" },
  NEUTRAL: { text: "neutral", cls: "text-amber-200" },
  UNKNOWN: { text: "not read", cls: "text-muted" },
};

/** Phase BC: the -100..+100 market sentiment as a centred bar with its components; deterministic, inputs shown on hover. */
export function SentimentGauge({ read, lines }: { read: SentimentRead | null | undefined; lines?: string[] }) {
  if (!read || read.label === "UNKNOWN") {
    return <div className="text-xs text-muted">Market sentiment not read yet (the option chain and quotes need a broker session).</div>;
  }
  const label = LABEL[read.label] ?? LABEL.UNKNOWN;
  const pct = Math.max(-100, Math.min(100, read.score));
  return (
    <div className="text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Gauge size={13} className="text-purple-300" />
        <span className="font-semibold text-slate-200">Market sentiment</span>
        <b className={`font-tabular ${label.cls}`}>{pct >= 0 ? "+" : ""}{pct.toFixed(0)}</b>
        <span className={label.cls}>{label.text}</span>
        <span className="text-muted">· coverage {(read.coverage * 100).toFixed(0)}%</span>
        {read.news && <span className="text-muted">· news {read.news.score >= 0 ? "+" : ""}{read.news.score.toFixed(0)} ({read.news.items}, unverified)</span>}
      </div>
      <div className="relative mt-1 h-2 w-full rounded bg-slate-700/60" role="img" aria-label={`Market sentiment ${pct.toFixed(0)} of 100`}>
        <div className="absolute left-1/2 top-0 h-2 w-px bg-slate-400" />
        <div className={`absolute top-0 h-2 rounded ${pct >= 0 ? "bg-emerald-400" : "bg-rose-400"}`}
             style={pct >= 0 ? { left: "50%", width: `${pct / 2}%` } : { right: "50%", width: `${-pct / 2}%` }} />
      </div>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {Object.entries(read.components).map(([key, c]) => (
          <span key={key} title={JSON.stringify(c.input ?? "no data")}
                className={`rounded border px-1.5 py-0.5 ${c.score == null ? "border-border text-muted line-through" : "border-border text-slate-200"}`}>
            {COMPONENT[key] ?? key} {c.score == null ? "-" : `${c.score >= 0 ? "+" : ""}${c.score.toFixed(0)}`}{c.score != null && <span className="text-muted"> ×{c.weight.toFixed(2)}</span>}
          </span>
        ))}
      </div>
      {lines && lines.length > 0 && <ul className="mt-1 list-disc pl-4 text-muted">{lines.slice(0, 3).map((l) => <li key={l}>{l}</li>)}</ul>}
    </div>
  );
}

export default function MarketMemoryCard() {
  const [memory, setMemory] = useState<MarketMemory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.aiMarketMemory().then(setMemory).catch((e) => setError(String(e)));
  useEffect(() => { void load(); }, []);

  async function refresh() {
    setBusy(true); setError(null);
    try { setMemory(await api.aiMarketMemoryRefresh()); } catch (e) {
      const text = String(e).replace(/^Error:\s*/, "");
      const m = text.match(/\{.*\}/s);
      let detail = text;
      if (m) { try { detail = JSON.parse(m[0]).detail ?? text; } catch { /* keep text */ } }
      setError(detail);
      void api.aiMarketMemory().then(setMemory).catch(() => undefined);
    } finally { setBusy(false); }
  }

  const vix = memory?.cues.find((c) => c.symbol === "INDIA VIX");
  // P0.9: a memory older than three worker intervals is labelled stale and dimmed - its prices are not today's.
  // P0.10 review: the age of the SYMBOL reads - global cues (fetched without a broker) keep `updated_at` current.
  const symbolReads = (memory?.symbols ?? []).map((s) => s.captured_at).filter((t): t is string => !!t).sort();
  const age = minutesAgo(symbolReads.length ? symbolReads[symbolReads.length - 1] : null);
  const stale = age != null && age > 3 * (memory?.interval_minutes ?? 15);
  // P0.10: prices and changes are only shown when they are a current read - not when stale, and not when every symbol
  // carries the same change (placeholder figures, not a market).
  const changes = (memory?.symbols ?? []).map((s) => s.change_pct).filter((c): c is number => c != null).map((c) => c.toFixed(2));
  const placeholder = changes.length >= 2 && new Set(changes).size === 1;
  const hide = stale || placeholder;
  const others = memory?.cues.filter((c) => c.symbol !== "INDIA VIX") ?? [];
  return (
    <Card title="Market memory">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Brain size={14} className="text-purple-300" />
        <span>The worker reads the market from your broker every {memory?.interval_minutes ?? 15} minutes; the Copilot uses this read.</span>
        <span className="ml-auto">Updated: <b className="text-slate-200">{ago(memory?.updated_at ?? null)}</b></span>
        {stale && <span className="rounded border border-amber-400/60 bg-amber-500/10 px-1.5 py-0.5 font-bold text-amber-300" title="Older than three worker intervals - not today's prices">STALE</span>}
        <button disabled={busy} onClick={() => void refresh()} className="rounded border border-border px-2 py-0.5 text-slate-100 hover:bg-panel2 disabled:opacity-50">
          <RefreshCw size={11} className={`mr-1 inline ${busy ? "animate-spin" : ""}`} />Read now
        </button>
      </div>
      {error && <div className="mb-2 text-xs text-danger">{error}</div>}
      {stale && <div className="mb-2 rounded border border-amber-500/40 bg-amber-500/5 px-2 py-1 text-xs text-amber-200">Stale read from {ago(memory?.updated_at ?? null)} - its prices and changes are hidden. Log in to your broker and press "Read now".</div>}
      {!stale && placeholder && <div className="mb-2 rounded border border-amber-500/40 bg-amber-500/5 px-2 py-1 text-xs text-amber-200">Every symbol shows the same change - placeholder figures, hidden until the next real read.</div>}
      {memory && memory.cues.length > 0 && !hide && (
        <div className="mb-2 flex flex-wrap gap-2 text-xs">
          {vix && vix.last_price != null && (
            <span className="rounded-lg border border-border bg-panel2/60 px-2 py-1">
              India VIX <b className="text-slate-50">{vix.last_price.toFixed(2)}</b>{" "}
              <span className={vixLabel(vix.last_price).cls}>{vixLabel(vix.last_price).text}</span>
              <span className="text-muted"> ({(vix.change_pct ?? 0) >= 0 ? "+" : ""}{(vix.change_pct ?? 0).toFixed(1)}%)</span>
            </span>
          )}
          {others.map((c) => (
            <span key={c.symbol} className="rounded-lg border border-border bg-panel2/60 px-2 py-1">
              {c.symbol} <b className={(c.change_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{(c.change_pct ?? 0) >= 0 ? "+" : ""}{(c.change_pct ?? 0).toFixed(2)}%</b>
            </span>
          ))}
        </div>
      )}
      {!memory || memory.symbols.length === 0 ? (
        <div className="text-xs text-muted">
          The memory is empty. With a broker (Upstox) session press "Read now"; otherwise the worker fills it while the market is open.
          {memory?.watchlist && <span> Watchlist: {memory.watchlist.join(", ")}.</span>}
        </div>
      ) : (
        <table className={`w-full text-xs ${stale ? "opacity-50" : ""}`}>
          <thead><tr className="text-left text-muted">
            <th className="py-1">Symbol</th><th>Price</th><th>Today</th><th>Trend</th><th>Regime (5m / higher)</th><th>Structure</th><th>Past days</th>
          </tr></thead>
          <tbody>
            {memory.symbols.map((s) => {
              const b = BIAS[s.bias ?? "NEUTRAL"] ?? BIAS.NEUTRAL;
              const trail = memory.history[s.symbol] ?? [];
              return (
                <tr key={s.symbol} className="border-t border-border/60">
                  <td className="py-1 font-semibold text-slate-100">{s.symbol}</td>
                  <td>{hide ? <span className="text-muted" title="Hidden - not a current read">-</span> : s.last_price?.toLocaleString("en-IN") ?? "-"}</td>
                  {hide ? <td className="text-muted">-</td> : (
                    <td className={(s.change_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{(s.change_pct ?? 0) >= 0 ? "+" : ""}{(s.change_pct ?? 0).toFixed(2)}%</td>
                  )}
                  <td className={`font-bold ${b.cls}`}>{b.en}</td>
                  <td className="text-slate-200">{REGIME[s.regime ?? "UNKNOWN"] ?? s.regime} / {REGIME[s.higher_regime ?? "UNKNOWN"] ?? s.higher_regime}</td>
                  <td className="text-slate-300">{s.structure ?? "-"}</td>
                  <td>{trail.map((d) => <span key={d.date} title={d.date} className={`mr-1 ${(BIAS[d.bias ?? "NEUTRAL"] ?? BIAS.NEUTRAL).cls}`}>●</span>)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="mt-3 border-t border-border/60 pt-2">
        <SentimentGauge read={memory?.sentiment} lines={memory?.sentiment_view} />
      </div>
      <div className="mt-3 border-t border-border/60 pt-2">
        <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-slate-200"><Globe2 size={13} className="text-sky-300" />Global cues</div>
        {memory?.globals && memory.globals.length > 0 ? (
          <>
            <div className="mb-1 flex flex-wrap gap-1.5 text-xs">
              {memory.globals.map((g) => (
                <span key={g.symbol} className="rounded-lg border border-border bg-panel2/60 px-2 py-0.5" title={`${g.source} · ${String(g.payload?.as_of ?? "")}`}>
                  {GLOBAL[g.symbol]?.en ?? g.symbol}{" "}
                  <b className={globalCls(g.symbol, g.change_pct ?? 0)}>{(g.change_pct ?? 0) >= 0 ? "+" : ""}{(g.change_pct ?? 0).toFixed(2)}%</b>
                </span>
              ))}
            </div>
            {memory.global_view && memory.global_view.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-200">
                {memory.global_view.map((line) => <li key={line}>{line}</li>)}
              </ul>
            )}
          </>
        ) : (
          <div className="text-xs text-muted">
            {memory?.global_enabled === false
              ? "Global cues are off (GLOBAL_CUES_ENABLED=false)."
              : "No global data yet. Press \"Read now\"; the worker fetches it from 08:00 and while the market is open."}
          </div>
        )}
        <div className="mt-1 text-[11px] text-muted">
          Green = usually supportive for India, red = pressure (e.g. crude or the dollar rising). {memory?.global_source ?? "Free public data, delayed"}. {memory?.global_gift_note ?? ""} A tendency, not a signal.
        </div>
      </div>
    </Card>
  );
}
