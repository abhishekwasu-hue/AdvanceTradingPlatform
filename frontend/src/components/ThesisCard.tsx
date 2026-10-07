import { ArrowDownRight, ArrowRight, ArrowUpRight, Eye, RefreshCw, Target } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MarketThesis, ThesisHistory } from "../types";
import { Card } from "./ui";

/**
 * Phase BD-lite: the market thesis of one watched symbol - direction with the agreement matrix,
 * bull / base / bear scenarios, and the SHADOW size multiplier (what a reduce-only overlay would
 * have done; recorded and scored, never applied). The scoreboard shows how the theses fared
 * against the next session, so the overlay has to earn any future right to act.
 */
const DIR: Record<string, { mr: string; cls: string; icon: typeof ArrowUpRight }> = {
  BULLISH: { mr: "तेजी", cls: "text-emerald-300 border-emerald-500/40", icon: ArrowUpRight },
  BEARISH: { mr: "मंदी", cls: "text-rose-300 border-rose-500/40", icon: ArrowDownRight },
  NEUTRAL: { mr: "तटस्थ", cls: "text-amber-200 border-amber-500/40", icon: ArrowRight },
};
const FACTOR: Record<string, { mr: string; en: string }> = {
  structure: { mr: "Structure", en: "Structure" }, trend: { mr: "Trend कल", en: "Trend bias" }, higher_regime: { mr: "मोठा timeframe", en: "Higher timeframe" },
  sentiment: { mr: "Market sentiment", en: "Market sentiment" }, news: { mr: "बातम्या", en: "News" }, global: { mr: "जागतिक संकेत", en: "Global cues" },
};

function detail(e: unknown): string {
  const text = String(e).replace(/^Error:\s*/, "");
  const m = text.match(/\{.*\}/s);
  if (m) { try { return JSON.parse(m[0]).detail ?? text; } catch { /* keep text */ } }
  return text;
}

export default function ThesisCard({ lang = "mr" }: { lang?: "mr" | "en" }) {
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [thesis, setThesis] = useState<MarketThesis | null>(null);
  const [history, setHistory] = useState<ThesisHistory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [off, setOff] = useState(false);

  async function load(refresh = false) {
    setBusy(true); setError(null);
    try {
      const t = await api.aiThesis(symbol, lang, refresh);
      setThesis(t); setOff(false);
      setHistory(await api.aiThesisHistory(symbol));
    } catch (e) {
      const d = detail(e);
      if (/^503\b/.test(String(e).replace(/^Error:\s*/, "")) && /market_thesis/.test(String(e))) { setOff(true); setThesis(null); } else { setError(d); setThesis(null); }
    } finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, [symbol, lang]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api.aiMarketMemory().then((m) => {
      const list = m.watchlist ?? [];
      setWatchlist(list);
      if (list.length && !list.includes(symbol)) setSymbol(list[0]);
    }).catch(() => undefined);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const dir = thesis ? DIR[thesis.direction] ?? DIR.NEUTRAL : null;
  const Icon = dir?.icon ?? Eye;
  const sb = history?.scoreboard;
  return (
    <Card title="Market thesis · शेअरचं वाचन (shadow)">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Target size={14} className="text-purple-300" />
        <span>{lang === "mr" ? "एका शेअरचा thesis: दिशा, घटकांची सहमती, तेजी/मूळ/मंदी scenario. Shadow multiplier फक्त नोंद - कुठेही लागू होत नाही." : "One symbol's thesis: direction, factor agreement, bull/base/bear scenarios. The shadow multiplier is recorded only - applied nowhere."}</span>
        <select className="ml-auto rounded bg-panel2 border border-border px-2 py-0.5 text-slate-100" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
          {(watchlist.length ? watchlist : [symbol]).map((s) => <option key={s}>{s}</option>)}
        </select>
        <button disabled={busy} onClick={() => void load(true)} className="rounded border border-border px-2 py-0.5 text-slate-100 hover:bg-panel2 disabled:opacity-50">
          <RefreshCw size={11} className={`mr-1 inline ${busy ? "animate-spin" : ""}`} />{lang === "mr" ? "पुन्हा वाचा" : "Rebuild"}
        </button>
      </div>
      {off && <div className="text-xs text-muted">{lang === "mr" ? "Market thesis feature या platform वर बंद आहे (flag market_thesis)." : "The market thesis feature is off on this platform (flag market_thesis)."}</div>}
      {error && <div className="text-xs text-danger">{error}</div>}
      {thesis && dir && (
        <div className="space-y-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-bold ${dir.cls}`}><Icon size={13} />{thesis.symbol} · {lang === "mr" ? dir.mr : thesis.direction.toLowerCase()}</span>
            {thesis.confidence != null && <span className="text-slate-200">{thesis.confidence}% {lang === "mr" ? "विश्वास" : "confidence"}</span>}
            <span className="text-muted">· {thesis.agreement.agreeing}/{thesis.agreement.with_opinion} {lang === "mr" ? "घटक सहमत" : "factors agree"} · coverage {(thesis.agreement.coverage * 100).toFixed(0)}%</span>
            <span className="ml-auto rounded border border-border px-2 py-0.5 text-muted" title={thesis.shadow.reasons.join("; ") || "no reduction"}>
              shadow ×{thesis.shadow.size_multiplier.toFixed(2)} <span className="text-amber-300">({lang === "mr" ? "लागू नाही" : "not applied"})</span>
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {thesis.agreement.matrix.map((r) => (
              <span key={r.factor} title={r.value == null ? "no data" : JSON.stringify(r.value)}
                    className={`rounded border px-1.5 py-0.5 ${!r.available ? "border-border text-muted line-through" : r.direction > 0 ? "border-emerald-500/40 text-emerald-200" : r.direction < 0 ? "border-rose-500/40 text-rose-200" : "border-border text-slate-200"}`}>
                {FACTOR[r.factor] ? (lang === "mr" ? FACTOR[r.factor].mr : FACTOR[r.factor].en) : r.factor} {!r.available ? "?" : r.direction > 0 ? "↑" : r.direction < 0 ? "↓" : "→"} <span className="text-muted">×{r.weight.toFixed(2)}</span>
                {r.factor === "news" && r.available && (r.value as { trust?: number } | null)?.trust != null && ((r.value as { trust: number }).trust < 1) && (
                  <span className="text-amber-300"> · {lang === "mr" ? "विश्वास" : "trust"} {(r.value as { trust: number }).trust.toFixed(2)}</span>
                )}
              </span>
            ))}
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            {(["bull", "base", "bear"] as const).map((k) => thesis.scenarios[k] && (
              <div key={k} className={`rounded-lg border p-2 ${k === "bull" ? "border-emerald-500/30 bg-emerald-500/[0.05]" : k === "bear" ? "border-rose-500/30 bg-rose-500/[0.05]" : "border-border bg-panel2/40"}`}>
                <div className="font-semibold text-slate-200 uppercase">{k}</div>
                <div className="text-muted">{thesis.scenarios[k]?.text}</div>
              </div>
            ))}
          </div>
          {thesis.events.length > 0 && <div className="text-amber-200">{lang === "mr" ? "आजचे events" : "Today's events"}: {thesis.events.map((e) => `${e.kind} (${e.action})`).join(", ")}</div>}
          <details className="text-muted">
            <summary className="cursor-pointer">{lang === "mr" ? "शब्दांत" : "In words"} {thesis.narrative_source === "model" ? "(AI, numbers checked)" : ""}</summary>
            <ul className="mt-1 list-disc pl-4">{thesis.lines.map((l) => <li key={l}>{l}</li>)}</ul>
          </details>
          {sb && (
            <div className="text-muted">
              {lang === "mr" ? "Scoreboard" : "Scoreboard"}: {sb.scored} {lang === "mr" ? "तपासलेले" : "scored"}, {sb.hits} ✓ {sb.misses} ✗ {sb.flat} → {sb.hit_rate != null && <b className="text-slate-200">hit rate {(sb.hit_rate * 100).toFixed(0)}%</b>}
              <span className="ml-2 text-amber-300">{lang === "mr" ? "Overlay: shadow (कधीही लागू केलेला नाही)" : "Overlay: shadow (never applied)"}</span>
            </div>
          )}
          <div className="text-[11px] text-muted">{lang === "mr" ? "Thesis म्हणजे market चं वाचन, signal नाही. शिक्षणासाठी." : "A thesis is a market reading, not a signal. For education."}</div>
        </div>
      )}
    </Card>
  );
}
