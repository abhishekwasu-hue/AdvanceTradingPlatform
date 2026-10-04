import { CheckCircle2, CircleAlert, Crosshair, FlaskConical, Layers, Loader2, Rocket, Save, ScanSearch, Target, TrendingDown, TrendingUp, Waves, Zap } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { MarketStudy, OHLCVBar, StrategistResult, StrategyCandidate } from "../types";
import type { CandleSourceState } from "./DataSource";
import { Card } from "./ui";

/**
 * Phase AW: the Copilot strategist. It studies the live market of a symbol (multi-timeframe trend,
 * levels, bias, scenarios), writes candidate strategies around today's levels, tunes them on the
 * earlier sessions and judges them on the later ones it never saw, and hands back the best three -
 * each ready to save as a strategy and deploy in PAPER.
 */
const SYMBOLS = ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "RELIANCE", "HDFCBANK", "INFY"];
const VERDICT: Record<StrategyCandidate["verdict"], { mr: string; cls: string }> = {
  robust: { mr: "नवीन data वरही टिकली", cls: "border-emerald-500/50 bg-emerald-500/10 text-emerald-200" },
  overfit: { mr: "Over-fit धोका", cls: "border-amber-500/50 bg-amber-500/10 text-amber-200" },
  thin: { mr: "Trades कमी", cls: "border-border bg-panel2 text-muted" },
  untested: { mr: "नवीन data वर trade नाही", cls: "border-border bg-panel2 text-muted" },
  weak: { mr: "फायदा दिसत नाही", cls: "border-rose-500/50 bg-rose-500/10 text-rose-200" },
};
const CHAR: Record<MarketStudy["character"], { mr: string; icon: typeof Waves; cls: string }> = {
  TREND: { mr: "Trend चा दिवस", icon: TrendingUp, cls: "text-emerald-300" },
  RANGE: { mr: "Range चा दिवस", icon: Waves, cls: "text-amber-200" },
  VOLATILE: { mr: "अस्थिर दिवस", icon: Zap, cls: "text-fuchsia-300" },
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
      <div className="mb-1 flex justify-between text-[11px] text-muted"><span>मंदी</span><span>तटस्थ</span><span>तेजी</span></div>
      <div className="relative h-2 rounded-full bg-gradient-to-r from-rose-500/60 via-slate-600 to-emerald-500/60">
        <div className="absolute -top-1 h-4 w-1.5 rounded bg-white shadow" style={{ left: `calc(${Math.min(98, Math.max(1, pct))}% - 3px)` }} />
      </div>
    </div>
  );
}

function StudyView({ s, source }: { s: MarketStudy; source: string }) {
  const ch = CHAR[s.character];
  const ChIcon = ch.icon;
  const biasCls = s.bias === "BULLISH" ? "text-emerald-300" : s.bias === "BEARISH" ? "text-rose-300" : "text-amber-200";
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title={`Market चा अभ्यास · ${s.symbol}`}>
        <div className="flex items-baseline justify-between">
          <div className="font-tabular text-2xl font-extrabold text-slate-50">{fmt(s.last_price)}</div>
          <div className={`text-sm font-bold ${biasCls}`}>{s.bias === "BULLISH" ? "तेजी" : s.bias === "BEARISH" ? "मंदी" : "तटस्थ"} · {s.confidence}%</div>
        </div>
        <div className="my-2"><BiasMeter s={s} /></div>
        <div className={`mb-2 flex items-center gap-1.5 text-sm font-semibold ${ch.cls}`}><ChIcon size={15} />{ch.mr}{s.vix != null && <span className="ml-auto text-xs text-muted">VIX {s.vix.toFixed(1)}</span>}</div>
        <ul className="space-y-1 text-xs text-slate-300">{s.lines.map((l) => <li key={l}>• {l}</li>)}</ul>
        <div className="mt-2 text-[11px] text-muted">Data: {source.replace("broker:", "broker · ")}{s.atr_5m ? ` · ATR(5m) ${fmt(s.atr_5m)} (${s.atr_5m_pct}%)` : ""}</div>
      </Card>

      <Card title="Timeframe नुसार trend">
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
          {s.scenarios.map((sc) => (
            <div key={sc.id} className={`rounded-lg border px-2 py-1.5 text-xs ${sc.id === "bull" ? "border-emerald-500/30 text-emerald-100" : sc.id === "bear" ? "border-rose-500/30 text-rose-100" : "border-amber-500/30 text-amber-100"}`}>{sc.text}</div>
          ))}
        </div>
      </Card>

      <Card title="आजचे महत्त्वाचे भाव (levels)">
        <div className="space-y-0.5 text-xs">
          {(() => {
            const rows = [...s.ladder];
            const at = rows.findIndex((r) => r.price < s.last_price);
            const priceRow = { name: "भाव आत्ता", key: "_last", price: s.last_price, distance_pct: 0 };
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

function CandidateCard({ c, best, symbol, onAdopted }: { c: StrategyCandidate; best: boolean; symbol: string; onAdopted: (msg: string) => void }) {
  const [saved, setSaved] = useState<{ strategy_id: string; deployment: Parameters<typeof api.createDeployment>[0] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const v = VERDICT[c.verdict];
  async function adopt() {
    setBusy(true); setError(null);
    try {
      const r = await api.aiStrategistAdopt(`${c.name} · ${symbol}`, c.config, symbol);
      setSaved(r);
      onAdopted(`"${r.name}" तुमच्या strategies मध्ये जतन झाली (${r.strategy_id}).`);
    } catch (e) { setError(String(e).replace(/^Error:\s*/, "")); } finally { setBusy(false); }
  }
  async function deploy() {
    if (!saved) return;
    setBusy(true); setError(null);
    try {
      const d = await api.createDeployment(saved.deployment);
      onAdopted(`PAPER deployment #${d.id} सुरू झाले - Autopilot page वर पाहा. LIVE फक्त Go-Live checklist नंतर.`);
    } catch (e) { setError(String(e).replace(/^Error:\s*/, "")); } finally { setBusy(false); }
  }
  return (
    <div className={`rounded-xl border p-4 ${best ? "border-purple-400/60 bg-purple-500/[0.07]" : "border-border bg-panel"}`}>
      <div className="flex flex-wrap items-center gap-2">
        {best && <span className="rounded bg-purple-500/30 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-purple-100">सर्वोत्तम</span>}
        <span className="text-base font-extrabold text-slate-50">{c.name}</span>
        <span className={`rounded border px-1.5 py-0.5 text-[11px] ${c.direction === "LONG" ? "border-emerald-500/40 text-emerald-300" : c.direction === "SHORT" ? "border-rose-500/40 text-rose-300" : "border-border text-slate-300"}`}>{c.direction === "LONG" ? "फक्त LONG" : c.direction === "SHORT" ? "फक्त SHORT" : "दोन्ही बाजू"}</span>
        <span className="text-[11px] text-muted">{c.timeframe}{c.source === "ai" ? " · AI ने सुचवलेली" : ""}</span>
        <span className={`ml-auto rounded border px-1.5 py-0.5 text-[11px] ${v.cls}`}>{v.mr}</span>
      </div>
      <p className="mt-1.5 text-xs text-slate-300">{c.why}</p>

      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5 text-xs">
          {(["long", "short"] as const).map((side) => c.rules[side].length > 0 && (
            <div key={side}>
              <div className={`mb-0.5 font-semibold ${side === "long" ? "text-emerald-300" : "text-rose-300"}`}>{side === "long" ? "LONG जेव्हा" : "SHORT जेव्हा"}</div>
              <div className="flex flex-wrap gap-1">{c.rules[side].map((r) => <span key={r} className="rounded border border-border bg-panel2/70 px-1.5 py-0.5 font-mono text-[11px] text-slate-200">{r}</span>)}</div>
            </div>
          ))}
          <div className="text-slate-300"><Target size={12} className="mr-1 inline text-sky-300" />{c.exits}</div>
          {c.triggers.length > 0 && (
            <div className="text-slate-300"><Crosshair size={12} className="mr-1 inline text-amber-300" />आजचे trigger: {c.triggers.map((t) => `${t.name} ${fmt(t.price)}`).join(" · ")}</div>
          )}
          <div className="text-slate-300">
            Risk प्रति trade: <b>₹{fmt(c.risk_amount, 0)}</b>{c.stop_points ? <> · stop ≈ {fmt(c.stop_points)} points{c.quantity_hint ? <> · सुमारे {c.quantity_hint} qty</> : null}</> : null}
          </div>
        </div>
        <div>
          <table className="w-full text-xs">
            <thead><tr className="text-muted"><th className="text-left font-normal">तपासणी</th><th className="text-right font-normal">Trades</th><th className="text-right font-normal">Win</th><th className="text-right font-normal">अपेक्षित</th><th className="text-right font-normal">PF</th></tr></thead>
            <tbody>
              <Metrics label="Tune केलेली सत्रे" m={c.in_sample} />
              <Metrics label="नवीन सत्रे (कधी न पाहिलेली)" m={c.out_of_sample} />
            </tbody>
          </table>
          <div className="mt-1.5 text-[11px] text-slate-400">{c.verdict_text}</div>
          {c.trades.length > 0 && (
            <div className="mt-2 flex h-8 items-end gap-px" title="प्रत्येक trade चा निकाल (R)">
              {c.trades.slice(-40).map((t, i) => (
                <div key={i} className={`w-1.5 rounded-sm ${t.r > 0 ? "bg-emerald-400/80" : "bg-rose-400/80"}`} style={{ height: `${Math.min(100, Math.max(8, Math.abs(t.r) * 30))}%` }} title={`${t.date} ${t.dir} ${t.r > 0 ? "+" : ""}${t.r}R (${t.reason})`} />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!saved ? (
          <button disabled={busy} onClick={() => void adopt()} className="rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-purple-500 disabled:opacity-50">
            {busy ? <Loader2 size={12} className="mr-1 inline animate-spin" /> : <Save size={12} className="mr-1 inline" />}Strategy म्हणून जतन करा
          </button>
        ) : (
          <>
            <span className="text-xs text-emerald-300"><CheckCircle2 size={13} className="mr-1 inline" />जतन झाली: {saved.strategy_id}</span>
            <button disabled={busy} onClick={() => void deploy()} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
              <Rocket size={12} className="mr-1 inline" />PAPER मध्ये deploy करा
            </button>
          </>
        )}
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </div>
  );
}

export default function StrategistPanel({ source, lang }: { source: CandleSourceState; lang: "en" | "mr" }) {
  const [symbol, setSymbol] = useState("NIFTY 50");
  const [style, setStyle] = useState<"intraday" | "scalping">("intraday");
  const [direction, setDirection] = useState<"auto" | "long" | "short" | "both">("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StrategistResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function run() {
    setBusy(true); setError(null); setMessage(null);
    try {
      const sym = symbol.trim().toUpperCase();
      let candles: OHLCVBar[] | undefined;
      if (source.mode === "sample") {
        const r = await source.fetch([sym], "1min", { count: 375 * 12, startPriceFor: () => (sym.includes("BANK") ? 52_000 : sym.includes("NIFTY") ? 24_500 : 1_500), seedFor: () => 7 });
        candles = sessionize(r.candles[sym] ?? []);
      }
      setResult(await api.aiStrategistBuild({ symbol: sym, candles, broker: source.mode === "broker" ? source.broker || undefined : undefined, style, direction, language: lang }));
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
            <div className="text-sm font-extrabold text-slate-50">Live market चा अभ्यास करून strategy बनवा</div>
            <div className="text-[11px] text-muted">Copilot अनेक timeframes वरचा trend, आजचे levels आणि scenarios वाचतो, आजच्या market साठी strategies लिहितो, त्या मागच्या सत्रांवर tune करतो आणि कधीही न पाहिलेल्या सत्रांवर तपासतो.</div>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-muted">Symbol
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} className="mt-0.5 block w-44 rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100" />
          </label>
          <div className="flex flex-wrap gap-1">
            {SYMBOLS.map((s) => <button key={s} onClick={() => setSymbol(s)} className={`rounded-full border px-2 py-0.5 text-[11px] ${symbol === s ? "border-purple-400/60 bg-purple-500/20 text-purple-100" : "border-border text-muted hover:text-slate-200"}`}>{s}</button>)}
          </div>
          <label className="text-xs text-muted">पद्धत
            <select value={style} onChange={(e) => setStyle(e.target.value as "intraday" | "scalping")} className="mt-0.5 block rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100">
              <option value="intraday">Intraday (5 मिनिट, 15 मिनिट filter)</option>
              <option value="scalping">Scalping (1 मिनिट, 5 मिनिट filter)</option>
            </select>
          </label>
          <label className="text-xs text-muted">दिशा
            <select value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)} className="mt-0.5 block rounded-lg border border-border bg-panel2 px-2 py-1.5 text-sm text-slate-100">
              <option value="auto">Market ठरवू दे (bias नुसार)</option>
              <option value="long">फक्त LONG</option>
              <option value="short">फक्त SHORT</option>
              <option value="both">दोन्ही बाजू</option>
            </select>
          </label>
          <button disabled={busy || !symbol.trim()} onClick={() => void run()} className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-bold text-white hover:bg-purple-500 disabled:opacity-50">
            {busy ? <Loader2 size={14} className="mr-1 inline animate-spin" /> : <Layers size={14} className="mr-1 inline" />}{busy ? "अभ्यास चालू आहे…" : "अभ्यास करा आणि strategy बनवा"}
          </button>
        </div>
        {source.mode === "sample" && (
          <div className="mt-2 flex items-center gap-1.5 text-[11px] text-amber-300"><FlaskConical size={12} />सध्या sample data - आजच्या खऱ्या market साठी वर Data मध्ये "Broker candles" निवडा.</div>
        )}
        {error && <div className="mt-2 flex items-center gap-1.5 text-xs text-danger"><CircleAlert size={13} />{error}</div>}
      </div>

      {result && (
        <>
          <StudyView s={result.study} source={result.data_source} />
          <Card title={`आजच्या market साठी strategies (${result.tested} तपासल्या${result.ai_candidates ? `, त्यात ${result.ai_candidates} AI च्या` : ""})`}>
            <ul className="mb-3 space-y-0.5 text-xs text-muted">{result.notes.map((n) => <li key={n}>• {n}</li>)}</ul>
            {message && <div className="mb-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">{message}</div>}
            <div className="space-y-3">
              {result.candidates.map((c) => <CandidateCard key={c.id + c.direction} c={c} best={c.id === result.best} symbol={result.study.symbol} onAdopted={setMessage} />)}
              {result.candidates.length === 0 && <div className="text-sm text-muted">या candles वर कोणतीही strategy trade घेत नाही - आज थांबणे हाच निर्णय.</div>}
            </div>
            <div className="mt-3 text-[11px] text-muted">चाचणी: प्रत्येक strategy एका वेळी एक position, signal च्या candle च्या close ला entry, आधी stop (pessimistic), खर्च 0.03%, 15:15 ला सगळे बंद. मागचे निकाल भविष्याची हमी नाहीत; आधी PAPER, मग Go-Live checklist नंतरच LIVE.</div>
          </Card>
        </>
      )}
    </div>
  );
}
