import { CheckCircle2, CircleAlert, Crosshair, FlaskConical, Layers, Loader2, MessageSquareText, Rocket, Save, ScanSearch, Target, TrendingDown, TrendingUp, Waves, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MarketStudy, OHLCVBar, StrategistRequestParsed, StrategistResult, StrategyCandidate } from "../types";
import type { CandleSourceState } from "./DataSource";
import { Card } from "./ui";
import SampleStamp from "./SampleStamp";

/**
 * Phase AW: market study and rule templates. It reads a symbol's data (multi-timeframe trend, levels), tests rule
 * templates on the earlier sessions and checks them on the later ones they never saw, and shows the results as
 * data - each can be saved as a strategy and deployed in PAPER if the trader decides to.
 *
 * P0.9: English UI. Sample candles stamp every figure "SAMPLE DATA"; "held up on unseen data" needs real broker
 * candles and 30+ unseen trades; a single stock shows no score and no price scenarios; an index's score is a model
 * score of factor agreement, not a forecast.
 */
const SYMBOLS = ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "RELIANCE", "HDFCBANK", "INFY"];
const VERDICT: Record<string, { en: string; cls: string }> = {
  robust: { en: "Held up on unseen data", cls: "border-emerald-500/50 bg-emerald-500/10 text-emerald-200" },
  overfit: { en: "Over-fit risk", cls: "border-amber-500/50 bg-amber-500/10 text-amber-200" },
  weak: { en: "No edge", cls: "border-rose-500/50 bg-rose-500/10 text-rose-200" },
  insufficient: { en: "Insufficient sample", cls: "border-border bg-panel2 text-muted" },
  sample: { en: "Sample data", cls: "border-amber-400/60 bg-amber-500/10 text-amber-200" },
  thin: { en: "Insufficient sample", cls: "border-border bg-panel2 text-muted" },
  untested: { en: "Insufficient sample", cls: "border-border bg-panel2 text-muted" },
};
const CHAR: Record<MarketStudy["character"], { en: string; icon: typeof Waves; cls: string }> = {
  TREND: { en: "Trending session", icon: TrendingUp, cls: "text-emerald-300" },
  RANGE: { en: "Range-bound session", icon: Waves, cls: "text-amber-200" },
  VOLATILE: { en: "Volatile session", icon: Zap, cls: "text-fuchsia-300" },
};
const fmt = (v: number | null | undefined, d = 2) => (v == null ? "-" : v.toLocaleString("en-IN", { maximumFractionDigits: d }));

/** Sample one-minute bars arranged into NSE sessions (09:15-15:29 IST, weekdays) so the study and the
 * walk-forward see real-looking days. */
function sessionize(bars: OHLCVBar[]): OHLCVBar[] {
  const perDay = 375;
  const days: Date[] = [];
  const d = new Date();
  d.setUTCHours(0, 0, 0, 0);
  while (days.length * perDay < bars.length) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) days.unshift(new Date(d));
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return bars.map((b, i) => {
    const day = days[Math.floor(i / perDay)];
    const t = new Date(day.getTime() + (3 * 60 + 45 + (i % perDay)) * 60_000);   // 03:45 UTC = 09:15 IST
    return { ...b, timestamp: t.toISOString() };
  });
}

function BiasMeter({ s }: { s: MarketStudy }) {
  const pct = Math.round(((s.bias_score + 1) / 2) * 100);
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px] text-muted"><span>Bearish</span><span>Neutral</span><span>Bullish</span></div>
      <div className="relative h-2 rounded-full bg-gradient-to-r from-rose-500/60 via-slate-600 to-emerald-500/60">
        <div className="absolute -top-1 h-4 w-1.5 rounded bg-white shadow" style={{ left: `calc(${Math.min(98, Math.max(1, pct))}% - 3px)` }} />
      </div>
    </div>
  );
}

function StudyView({ s, source }: { s: MarketStudy; source: string }) {
  const sample = !source.startsWith("broker");
  const base = CHAR[s.character];
  // A trending session follows the data's direction: a falling trend is not drawn with a rising, green icon.
  const ch = s.character === "TREND" && s.bias === "BEARISH" ? { ...base, icon: TrendingDown, cls: "text-rose-300" } : base;
  const ChIcon = ch.icon;
  const biasCls = s.bias === "BULLISH" ? "text-emerald-300" : s.bias === "BEARISH" ? "text-rose-300" : "text-amber-200";
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title={`Market study · ${s.symbol}`}>
        <div className="flex items-baseline justify-between">
          <div className="font-tabular text-2xl font-extrabold text-slate-50">{fmt(s.last_price)}{sample && <span className="ml-2 align-middle text-[10px] font-bold text-amber-300">SAMPLE PRICE</span>}</div>
          <div className={`text-right text-sm font-bold ${biasCls}`}>
            Data read: {s.bias.toLowerCase()}
            {s.confidence != null && <div className="text-[10px] font-normal text-muted">model score {s.confidence}/100 - factor agreement, not a forecast</div>}
          </div>
        </div>
        <div className="my-2"><BiasMeter s={s} /></div>
        <div className={`mb-2 flex items-center gap-1.5 text-sm font-semibold ${ch.cls}`}><ChIcon size={15} />{ch.en}{s.vix != null && <span className="ml-auto text-xs text-muted">VIX {s.vix.toFixed(1)}</span>}</div>
        <ul className="space-y-1 text-xs text-slate-300">{s.lines.map((l) => <li key={l}>• {l}</li>)}</ul>
        <div className="mt-2 text-[11px] text-muted">Data: {source.replace("broker:", "broker · ")}{s.atr_5m ? ` · ATR(5m) ${fmt(s.atr_5m)} (${s.atr_5m_pct}%)` : ""}</div>
      </Card>

      <Card title="Trend by timeframe">
        <table className="w-full text-xs">
          <thead><tr className="text-left text-muted"><th className="py-1">TF</th><th>Trend</th><th>Regime</th><th className="text-right">RSI</th><th className="text-right">ADX</th></tr></thead>
          <tbody>
            {s.timeframes.map((t) => (
              <tr key={t.timeframe} className="border-t border-border/50">
                <td className="py-1.5 font-semibold text-slate-200">{t.timeframe}</td>
                <td className={t.trend === "UP" ? "text-emerald-300" : t.trend === "DOWN" ? "text-rose-300" : "text-amber-200"}>
                  {t.trend === "UP" ? <TrendingUp size={12} className="mr-1 inline" /> : t.trend === "DOWN" ? <TrendingDown size={12} className="mr-1 inline" /> : null}{t.trend_text}
                </td>
                <td className="text-slate-300">{t.regime.replace("_", " ").toLowerCase()}</td>
                <td className="text-right font-tabular">{fmt(t.rsi, 0)}</td>
                <td className="text-right font-tabular">{fmt(t.adx, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 space-y-1.5">
          {s.detail_shown === false && <div className="text-[11px] text-muted">Price scenarios for a single stock are not shown (operator setting).</div>}
          {s.scenarios.map((sc) => (
            <div key={sc.id} className={`rounded-lg border px-2 py-1.5 text-xs ${sc.id === "bull" ? "border-emerald-500/30 text-emerald-100" : sc.id === "bear" ? "border-rose-500/30 text-rose-100" : "border-amber-500/30 text-amber-100"}`}>{sc.text}</div>
          ))}
        </div>
      </Card>

      <Card title="Today's levels">
        <div className="space-y-0.5 text-xs">
          {(() => {
            const rows = [...s.ladder];
            const at = rows.findIndex((r) => r.price < s.last_price);
            const priceRow = { name: "Last price", key: "_last", price: s.last_price, distance_pct: 0 };
            rows.splice(at === -1 ? rows.length : at, 0, priceRow);
            return rows.map((r) => (
              <div key={r.key + r.price} className={`flex items-center justify-between rounded px-2 py-0.5 ${r.key === "_last" ? "bg-purple-500/20 font-bold text-purple-100" : "text-slate-300"}`}>
                <span>{r.name}</span>
                <span className="font-tabular">{fmt(r.price)}{r.key !== "_last" && r.distance_pct != null && <span className={`ml-2 ${r.distance_pct >= 0 ? "text-emerald-300/80" : "text-rose-300/80"}`}>{r.distance_pct >= 0 ? "+" : ""}{r.distance_pct}%</span>}</span>
              </div>
            ));
          })()}
        </div>
      </Card>
    </div>
  );
}

function Metrics({ label, m }: { label: string; m: StrategyCandidate["in_sample"] }) {
  return (
    <tr className="border-t border-border/50">
      <td className="py-1 text-muted">{label}</td>
      <td className="text-right font-tabular">{m.trades}</td>
      <td className="text-right font-tabular">{m.trades ? `${m.win_rate}%` : "-"}</td>
      <td className={`text-right font-tabular ${m.expectancy_r > 0 ? "text-emerald-300" : m.expectancy_r < 0 ? "text-rose-300" : ""}`}>{m.trades ? `${m.expectancy_r > 0 ? "+" : ""}${m.expectancy_r}R` : "-"}</td>
      <td className="text-right font-tabular">{m.profit_factor ?? "-"}</td>
    </tr>
  );
}

function CandidateCard({ c, symbol, sample, onAdopted }: { c: StrategyCandidate; symbol: string; sample: boolean; onAdopted: (msg: string) => void }) {
  const [showTechnical, setShowTechnical] = useState(false);
  const [saved, setSaved] = useState<{ strategy_id: string; deployment: Parameters<typeof api.createDeployment>[0] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // P0.8 / A3: the human confirms the maximum loss per trade before the server saves the candidate as a strategy.
  const [acceptRisk, setAcceptRisk] = useState(false);
  const v = VERDICT[c.verdict] ?? VERDICT.insufficient;
  async function adopt() {
    if (c.candidate_id == null) { setError("This candidate is not on the server - run the study again."); return; }
    setBusy(true); setError(null);
    try {
      const r = await api.aiStrategistAdopt(c.candidate_id, `${c.name} · ${symbol}`, acceptRisk);
      setSaved(r);
      onAdopted(`"${r.name}" saved to your strategies (${r.strategy_id}).`);
    } catch (e) { setError(String(e).replace(/^Error:\s*/, "")); } finally { setBusy(false); }
  }
  async function deploy() {
    if (!saved) return;
    setBusy(true); setError(null);
    try {
      const d = await api.createDeployment(saved.deployment);
      onAdopted(`PAPER deployment #${d.id} started - see the Autopilot page. LIVE only after the Go-Live checklist.`);
    } catch (e) { setError(String(e).replace(/^Error:\s*/, "")); } finally { setBusy(false); }
  }
  return (
    <div className="rounded-xl border border-border bg-panel p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-extrabold text-slate-50">{c.name}</span>
        <span className={`rounded border px-1.5 py-0.5 text-[11px] ${c.direction === "LONG" ? "border-emerald-500/40 text-emerald-300" : c.direction === "SHORT" ? "border-rose-500/40 text-rose-300" : "border-border text-slate-300"}`}>{c.direction_text ?? (c.direction === "LONG" ? "LONG only" : c.direction === "SHORT" ? "SHORT only" : "both sides")}</span>
        <span className="text-[11px] text-muted">{c.timeframe_text ?? c.timeframe}{c.source === "ai" ? " · proposed by your AI" : ""}</span>
        <span className={`ml-auto rounded border px-1.5 py-0.5 text-[11px] ${v.cls}`} title={c.verdict_text}>{v.en}</span>
      </div>
      <p className="mt-1.5 text-xs text-slate-300">{c.why}</p>

      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5 text-xs">
          {(["long", "short"] as const).map((side) => c.rules[side].length > 0 && (
            <div key={side}>
              <div className={`mb-0.5 font-semibold ${side === "long" ? "text-emerald-300" : "text-rose-300"}`}>{side === "long" ? "LONG when" : "SHORT when"}</div>
              {showTechnical || !c.rules_text ? (
                <div className="flex flex-wrap gap-1">{c.rules[side].map((r) => <span key={r} className="rounded border border-border bg-panel2/70 px-1.5 py-0.5 font-mono text-[11px] text-slate-200">{r}</span>)}</div>
              ) : (
                <ul className="list-disc pl-4 text-slate-200">{c.rules_text[side].map((r, i) => <li key={`${side}-${i}`} title={c.rules[side][i]}>{r}</li>)}</ul>
              )}
            </div>
          ))}
          {c.rules_text && <button onClick={() => setShowTechnical((v) => !v)} className="text-[11px] text-sky-300 hover:underline">{showTechnical ? "Show in words" : "Show technical rules"}</button>}
          <div className="text-slate-300"><Target size={12} className="mr-1 inline text-sky-300" />{c.exits}</div>
          {c.triggers.length > 0 && (
            <div className="text-slate-300"><Crosshair size={12} className="mr-1 inline text-amber-300" />Today's triggers: {c.triggers.map((t) => `${t.name} ${fmt(t.price)}`).join(" · ")}</div>
          )}
          <div className="text-slate-300">
            Risk per trade: <b>₹{fmt(c.risk_amount, 0)}</b>{c.stop_points ? <> · stop ≈ {fmt(c.stop_points)} points{c.quantity_hint ? <> · about {c.quantity_hint} qty</> : null}</> : null}
          </div>
        </div>
        <div className="relative">
          <div className={sample ? "select-none blur-sm" : ""} aria-hidden={sample}>
          <table className="w-full text-xs">
            <thead><tr className="text-muted"><th className="text-left font-normal">Check</th><th className="text-right font-normal">Trades</th><th className="text-right font-normal">Win</th><th className="text-right font-normal">Expectancy</th><th className="text-right font-normal">PF</th></tr></thead>
            <tbody>
              <Metrics label="Tuning sessions" m={c.in_sample} />
              <Metrics label="Unseen sessions" m={c.out_of_sample} />
            </tbody>
          </table>
          <div className="mt-1.5 text-[11px] text-slate-400">{c.verdict_text}</div>
          {c.trades.length > 0 && (
            <div className="mt-2 flex h-8 items-end gap-px" title="Each trade's result (R)">
              {c.trades.slice(-40).map((t, i) => (
                <div key={i} className={`w-1.5 rounded-sm ${t.r > 0 ? "bg-emerald-400/80" : "bg-rose-400/80"}`} style={{ height: `${Math.min(100, Math.max(8, Math.abs(t.r) * 30))}%` }} title={`${t.date} ${t.dir} ${t.r > 0 ? "+" : ""}${t.r}R (${t.reason})`} />
              ))}
            </div>
          )}
          </div>
          {sample && <SampleStamp />}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!saved ? (
          <>
            <label className="flex items-center gap-1.5 text-[11px] text-slate-200">
              <input type="checkbox" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} />
              I accept a maximum loss of about ₹{fmt(c.risk_amount, 0)} per trade (I read the rules)
            </label>
            <button disabled={busy || !acceptRisk} onClick={() => void adopt()} className="rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-purple-500 disabled:opacity-50">
              {busy ? <Loader2 size={12} className="mr-1 inline animate-spin" /> : <Save size={12} className="mr-1 inline" />}Save as a strategy
            </button>
          </>
        ) : (
          <>
            <span className="text-xs text-emerald-300"><CheckCircle2 size={13} className="mr-1 inline" />Saved: {saved.strategy_id}</span>
            <button disabled={busy} onClick={() => void deploy()} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
              <Rocket size={12} className="mr-1 inline" />Deploy in PAPER
            </button>
          </>
        )}
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </div>
  );
}

export default function StrategistPanel({ source }: { source: CandleSourceState }) {
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [style, setStyle] = useState<"intraday" | "scalping">("intraday");
  const [direction, setDirection] = useState<"long" | "short" | "both">("both");   // P0.8-D: the trader names the side; the market does not
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StrategistResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Phase BF: a plain-words request (English or Marathi) the backend parses; the chips show what it understood.
  const [requestText, setRequestText] = useState("");
  const [parsed, setParsed] = useState<StrategistRequestParsed | null>(null);

  useEffect(() => {
    const text = requestText.trim();
    if (!text) { setParsed(null); return; }
    let active = true;
    const handle = setTimeout(() => {
      api.aiStrategistParse(text, symbol, "en").then((p) => {
        if (!active) return;                       // a newer request or a cleared box wins
        setParsed(p);
        if (p.matched.symbol) setSymbol(p.symbol);
        if (p.matched.style) setStyle(p.style);
        if (p.matched.direction) setDirection(p.direction === "auto" ? "both" : p.direction);
      }).catch(() => { if (active) setParsed(null); });
    }, 400);
    return () => { active = false; clearTimeout(handle); };
  }, [requestText]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    setBusy(true); setError(null); setMessage(null);
    try {
      const sym = symbol.trim().toUpperCase();
      let candles: OHLCVBar[] | undefined;
      if (source.mode === "sample") {
        const r = await source.fetch([sym], "1min", { count: 375 * 12, startPriceFor: () => (sym.includes("BANK") ? 52_000 : sym.includes("NIFTY") ? 24_500 : 1_500), seedFor: () => 7 });
        candles = sessionize(r.candles[sym] ?? []);
      }
      // The parse already filled the form, so the form (which the trader may have corrected by hand) is what runs.
      setResult(await api.aiStrategistBuild({ symbol: sym, candles, broker: source.mode === "broker" ? source.broker || undefined : undefined, style, direction,
                                              language: "en" }));
    } catch (e) {
      const text = String(e).replace(/^Error:\s*/, "");
      const m = text.match(/\{.*\}/s);
      let detail = text;
      if (m) { try { detail = JSON.parse(m[0]).detail ?? text; } catch { /* keep */ } }
      setError(detail);
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-purple-500/30 bg-gradient-to-br from-purple-500/[0.12] via-panel to-sky-500/[0.06] p-4 shadow-card">
        <div className="mb-3 flex items-center gap-2">
          <div className="rounded-lg bg-purple-500/20 p-1.5"><ScanSearch size={18} className="text-purple-200" /></div>
          <div>
            <div className="text-sm font-extrabold text-slate-50">Market study and rule templates</div>
            <div className="text-[11px] text-muted">Reads the trend on several timeframes and today's levels, tests rule templates on earlier sessions and checks them on later ones they never saw. It explains rules and data; decisions are yours.</div>
          </div>
        </div>
        <label className="mb-3 block text-xs text-muted">
          <span className="flex items-center gap-1"><MessageSquareText size={12} />Describe it in words</span>
          <input value={requestText} onChange={(e) => setRequestText(e.target.value)} placeholder="e.g. Bank Nifty long only scalping (no negations - check the fields below)"
                 className="mt-0.5 block w-full rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100" />
          {parsed && (
            <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px]">
              <span className="text-slate-300">{parsed.summary}</span>
              {(["symbol", "style", "direction"] as const).map((k) => parsed.matched[k] && <span key={k} className="rounded-full border border-purple-400/50 bg-purple-500/15 px-2 py-0.5 text-purple-100">{parsed.matched[k]}</span>)}
            </div>
          )}
        </label>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-muted">Symbol
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} className="mt-0.5 block w-44 rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100" />
          </label>
          <div className="flex flex-wrap gap-1">
            {SYMBOLS.map((s) => <button key={s} onClick={() => setSymbol(s)} className={`rounded-full border px-2 py-0.5 text-[11px] ${symbol === s ? "border-purple-400/60 bg-purple-500/20 text-purple-100" : "border-border text-muted hover:text-slate-200"}`}>{s}</button>)}
          </div>
          <label className="text-xs text-muted">Style
            <select value={style} onChange={(e) => setStyle(e.target.value as "intraday" | "scalping")} className="mt-0.5 block rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100">
              <option value="intraday">Intraday (5 min, 15 min filter)</option>
              <option value="scalping">Scalping (1 min, 5 min filter)</option>
            </select>
          </label>
          <label className="text-xs text-muted">Side
            <select value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)} className="mt-0.5 block rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100">
              <option value="long">LONG only</option>
              <option value="short">SHORT only</option>
              <option value="both">Both sides</option>
            </select>
          </label>
          <button disabled={busy || !symbol.trim()} onClick={() => void run()} className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-bold text-white hover:bg-purple-500 disabled:opacity-50">
            {busy ? <Loader2 size={14} className="mr-1 inline animate-spin" /> : <Layers size={14} className="mr-1 inline" />}{busy ? "Studying…" : "Study the market and test templates"}
          </button>
        </div>
        {source.mode === "sample" && (
          <div className="mt-2 flex items-center gap-1.5 text-[11px] text-amber-300"><FlaskConical size={12} />SAMPLE data - every figure below is stamped; choose "Broker candles" under Data for the real market.</div>
        )}
        {error && <div className="mt-2 flex items-center gap-1.5 text-xs text-danger"><CircleAlert size={13} />{error}</div>}
      </div>

      {result && (
        <>
          <StudyView s={result.study} source={result.data_source} />
          <Card title={`Rule templates tested on this data (${result.tested}${result.ai_candidates ? `, ${result.ai_candidates} from your AI` : ""})`}>
            <ul className="mb-3 space-y-0.5 text-xs text-muted">{result.notes.map((n) => <li key={n}>• {n}</li>)}</ul>
            {message && <div className="mb-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">{message}</div>}
            <div className="space-y-3">
              {result.candidates.map((c) => <CandidateCard key={c.id + c.direction} c={c} symbol={result.study.symbol} sample={!result.data_source.startsWith("broker")} onAdopted={setMessage} />)}
              {result.candidates.length === 0 && <div className="text-sm text-muted">No template takes a trade on these candles.</div>}
            </div>
            <div className="mt-3 text-[11px] text-muted">Test: one position at a time, entry at the signal candle's close, the stop first (pessimistic; a gap through the stop fills at the open), costs 0.03%, flat by 15:15. "Held up on unseen data" needs real broker candles and 30+ trades on the unseen sessions. Past results do not predict the future; PAPER first, LIVE only after the Go-Live checklist.</div>
          </Card>
        </>
      )}
    </div>
  );
}
