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
const GLOBAL: Record<string, { mr: string; inverse?: boolean; neutral?: boolean }> = {
  SP500_FUT: { mr: "S&P 500 futures" }, NASDAQ_FUT: { mr: "Nasdaq futures" }, SP500: { mr: "S&P 500" }, NASDAQ: { mr: "Nasdaq" },
  NIKKEI: { mr: "Nikkei (जपान)" }, HANG_SENG: { mr: "Hang Seng" }, BRENT: { mr: "Brent कच्चे तेल", inverse: true },
  GOLD: { mr: "सोने", neutral: true }, DXY: { mr: "Dollar index", inverse: true }, USDINR: { mr: "USD/INR", inverse: true },
  US10Y: { mr: "US 10Y yield", inverse: true },
};

function globalCls(key: string, change: number): string {
  const g = GLOBAL[key];
  if (!g || g.neutral || Math.abs(change) < 0.05) return "text-slate-200";
  const goodForIndia = g.inverse ? change < 0 : change > 0;
  return goodForIndia ? "text-emerald-300" : "text-rose-300";
}
const BIAS: Record<string, { mr: string; cls: string }> = {
  BULLISH: { mr: "तेजी", cls: "text-emerald-300" },
  BEARISH: { mr: "मंदी", cls: "text-rose-300" },
  NEUTRAL: { mr: "तटस्थ", cls: "text-amber-200" },
};
const REGIME_MR: Record<string, string> = {
  TRENDING_UP: "वरचा trend", TRENDING_DOWN: "खालचा trend", RANGING: "sideways", VOLATILE: "अस्थिर", QUIET: "शांत", UNKNOWN: "-",
};

function vixLabel(v: number): { text: string; cls: string } {
  if (v < 12) return { text: "खूप शांत", cls: "text-sky-300" };
  if (v < 16) return { text: "सामान्य", cls: "text-emerald-300" };
  if (v < 20) return { text: "वाढलेला", cls: "text-amber-300" };
  return { text: "जास्त भीती", cls: "text-rose-300" };
}

function ago(iso: string | null): string {
  if (!iso) return "-";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return min < 60 ? `${min} मि. पूर्वी` : `${Math.round(min / 60)} तास पूर्वी`;
}

const COMPONENT_MR: Record<string, string> = { pcr: "PCR/OI", vix: "VIX", breadth: "रुंदी", global: "जागतिक", fii_dii: "FII/DII" };
const LABEL_MR: Record<string, { text: string; cls: string }> = {
  RISK_ON: { text: "तेजीचा कल", cls: "text-emerald-300" },
  RISK_OFF: { text: "सावधगिरीचा कल", cls: "text-rose-300" },
  NEUTRAL: { text: "तटस्थ", cls: "text-amber-200" },
  UNKNOWN: { text: "वाचलेला नाही", cls: "text-muted" },
};

/** Phase BC: the -100..+100 market sentiment as a centred bar with its components; deterministic, inputs shown on hover. */
export function SentimentGauge({ read, lines }: { read: SentimentRead | null | undefined; lines?: string[] }) {
  if (!read || read.label === "UNKNOWN") {
    return <div className="text-xs text-muted">Market sentiment अजून वाचलेला नाही (option chain आणि quotes साठी broker session लागतो).</div>;
  }
  const label = LABEL_MR[read.label] ?? LABEL_MR.UNKNOWN;
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
            {COMPONENT_MR[key] ?? key} {c.score == null ? "-" : `${c.score >= 0 ? "+" : ""}${c.score.toFixed(0)}`}{c.score != null && <span className="text-muted"> ×{c.weight.toFixed(2)}</span>}
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
  const others = memory?.cues.filter((c) => c.symbol !== "INDIA VIX") ?? [];
  return (
    <Card title="Market चा साठा · Market memory">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Brain size={14} className="text-purple-300" />
        <span>Worker दर {memory?.interval_minutes ?? 15} मिनिटांनी तुमच्या broker कडून market वाचून साठवतो. Plan बनवताना Copilot हीच माहिती वापरतो.</span>
        <span className="ml-auto">अद्ययावत: <b className="text-slate-200">{ago(memory?.updated_at ?? null)}</b></span>
        <button disabled={busy} onClick={() => void refresh()} className="rounded border border-border px-2 py-0.5 text-slate-100 hover:bg-panel2 disabled:opacity-50">
          <RefreshCw size={11} className={`mr-1 inline ${busy ? "animate-spin" : ""}`} />आत्ता वाचा
        </button>
      </div>
      {error && <div className="mb-2 text-xs text-danger">{error}</div>}
      {memory && memory.cues.length > 0 && (
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
          अजून साठा रिकामा आहे. Broker (Upstox) login असेल तर "आत्ता वाचा" दाबा; नाहीतर market चालू असताना worker आपोआप भरेल.
          {memory?.watchlist && <span> लक्ष ठेवायचे symbols: {memory.watchlist.join(", ")}.</span>}
        </div>
      ) : (
        <table className="w-full text-xs">
          <thead><tr className="text-left text-muted">
            <th className="py-1">Symbol</th><th>भाव</th><th>आज</th><th>कल</th><th>स्थिती (5m / मोठा)</th><th>Structure</th><th>गेले दिवस</th>
          </tr></thead>
          <tbody>
            {memory.symbols.map((s) => {
              const b = BIAS[s.bias ?? "NEUTRAL"] ?? BIAS.NEUTRAL;
              const trail = memory.history[s.symbol] ?? [];
              return (
                <tr key={s.symbol} className="border-t border-border/60">
                  <td className="py-1 font-semibold text-slate-100">{s.symbol}</td>
                  <td>{s.last_price?.toLocaleString("en-IN") ?? "-"}</td>
                  <td className={(s.change_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>{(s.change_pct ?? 0) >= 0 ? "+" : ""}{(s.change_pct ?? 0).toFixed(2)}%</td>
                  <td className={`font-bold ${b.cls}`}>{b.mr}</td>
                  <td className="text-slate-200">{REGIME_MR[s.regime ?? "UNKNOWN"] ?? s.regime} / {REGIME_MR[s.higher_regime ?? "UNKNOWN"] ?? s.higher_regime}</td>
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
        <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-slate-200"><Globe2 size={13} className="text-sky-300" />जागतिक संकेत</div>
        {memory?.globals && memory.globals.length > 0 ? (
          <>
            <div className="mb-1 flex flex-wrap gap-1.5 text-xs">
              {memory.globals.map((g) => (
                <span key={g.symbol} className="rounded-lg border border-border bg-panel2/60 px-2 py-0.5" title={`${g.source} · ${String(g.payload?.as_of ?? "")}`}>
                  {GLOBAL[g.symbol]?.mr ?? g.symbol}{" "}
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
              ? "जागतिक संकेत बंद आहेत (GLOBAL_CUES_ENABLED=false)."
              : "अजून जागतिक माहिती आलेली नाही. \"आत्ता वाचा\" दाबा; worker सकाळी 08:00 पासून आणि market चालू असताना आपोआप आणतो."}
          </div>
        )}
        <div className="mt-1 text-[11px] text-muted">
          हिरवा = भारतासाठी सहसा पोषक, लाल = दबाव (उदा. crude किंवा डॉलर वाढणे). {memory?.global_source ?? "मोफत सार्वजनिक माहिती, उशिरा"}. {memory?.global_gift_note ?? ""} ही प्रवृत्ती आहे, signal नाही.
        </div>
      </div>
    </Card>
  );
}
