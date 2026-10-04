import { ArrowLeft, Bot, CandlestickChart, Compass, Rocket, ShieldCheck, Sparkles, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AiStrategyDraft, InterviewPlan, InterviewQuestion, InterviewStart, OHLCVBar } from "../types";
import { FNO_INDICES, FNO_STOCKS } from "../utils/fnoSymbols";
import type { CandleSourceState } from "./DataSource";
import { chartWindowUrl } from "./ProChart";

/**
 * Phase AP: the strategy interview. A beginner who asks "give me a strategy" is asked about
 * themselves first (in Marathi or English), then the platform reads the market and builds a plan:
 * strategy with evidence, risk management, capital allocation, R:R, the contract to trade and a
 * PAPER deployment. Nothing is applied until the trader presses a button.
 */

type Lang = "en" | "mr";
const L = (lang: Lang, en: string, mr: string) => (lang === "mr" ? mr : en);
const CAPITAL_CHIPS = [50_000, 100_000, 200_000, 500_000];

interface Msg { from: "ai" | "me"; text: string }

/** "Error: 402 Payment Required: {"detail":"..."}" -> the detail alone. */
function cleanError(e: unknown): string {
  const text = String(e).replace(/^Error:\s*/, "");
  const m = text.match(/^\d{3}[^{]*(\{.*\})\s*$/s);
  if (m) {
    try { const d = JSON.parse(m[1]).detail; if (typeof d === "string") return d; } catch { /* not JSON */ }
  }
  return text;
}

export default function StrategyInterview({ source, startPrompt, startKey, onDraft }: {
  source: CandleSourceState;
  /** Text the trader typed in the generator box; parsed for answers already given. */
  startPrompt: string;
  /** Changes whenever the page wants the interview (re)started. 0 = not started. */
  startKey: number;
  onDraft?: (draft: AiStrategyDraft) => void;
}) {
  const [start, setStart] = useState<InterviewStart | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [queue, setQueue] = useState<string[]>([]);
  const [step, setStep] = useState(0);
  const [log, setLog] = useState<Msg[]>([]);
  const [custom, setCustom] = useState("");
  const [plan, setPlan] = useState<InterviewPlan | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const lang: Lang = (answers.language as Lang) || start?.language || "mr";
  const byId = (id: string) => start?.questions.find((q) => q.id === id);
  const current: InterviewQuestion | undefined = queue[step] ? byId(queue[step]) : undefined;

  useEffect(() => {
    if (!startKey) return;
    let cancelled = false;
    setPlan(null); setError(null); setDone(null); setStep(0); setCustom("");
    api.aiInterviewStart(startPrompt).then((s) => {
      if (cancelled) return;
      const prefill = { ...s.prefill };
      // The language question always comes first unless the text already settled it.
      const order = s.questions.map((q) => q.id).filter((id) => !(id in prefill));
      setStart(s); setAnswers(prefill); setQueue(order);
      const known = Object.keys(s.prefill).filter((k) => k !== "language");
      const intro: Msg[] = [{ from: "ai", text: s.intro }];
      if (known.length) {
        intro.push({ from: "ai", text: L(s.language, "Already understood from your message: ", "तुमच्या संदेशातून आधीच समजले: ")
          + known.map((k) => `${k} = ${prefill[k]}`).join(", ") });
      }
      setLog(intro);
    }).catch((e) => setError(cleanError(e)));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startKey]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [log, step, plan]);

  if (!startKey || !start) return error ? <div className="text-sm text-danger">{error}</div> : null;

  const qText = (q: InterviewQuestion) => (lang === "mr" ? q.mr : q.en);
  const optLabel = (q: InterviewQuestion, value: string) => {
    const o = q.options.find((x) => x.value === value);
    return o ? (lang === "mr" ? o.mr : o.en) : value;
  };

  function answer(value: string, label?: string) {
    if (!current) return;
    const q = current;
    const next = { ...answers, [q.id]: value };
    // A stock-only trader is not offered index symbols as the default.
    if (q.id === "instrument" && value !== "index" && (answers.symbol ?? "NIFTY 50").startsWith("NIFTY")) delete next.symbol;
    setAnswers(next);
    const qLang: Lang = q.id === "language" ? (value as Lang) : lang;
    setLog((prev) => [...prev,
      { from: "ai", text: q.id === "language" ? q.mr + " / " + q.en : (qLang === "mr" ? q.mr : q.en) },
      { from: "me", text: label ?? optLabel(q, value) }]);
    setCustom("");
    setStep((s) => s + 1);
  }

  function back() {
    if (step === 0) return;
    const prevId = queue[step - 1];
    setAnswers((a) => { const n = { ...a }; delete n[prevId]; return n; });
    setLog((prev) => prev.slice(0, -2));
    setStep(step - 1);
    setPlan(null);
  }

  async function candlesFor(style: string): Promise<{ tf: string; candles: OHLCVBar[]; label: string }> {
    const symbol = (answers.symbol || "NIFTY 50").trim().toUpperCase();
    if (source.mode === "sample") {
      const r = await source.fetch([symbol], "1min", { count: 3000, startPriceFor: () => 24_000, seedFor: () => 11 });
      return { tf: "1min", candles: r.candles[symbol] ?? [], label: "sample" };
    }
    const tf = style === "scalping" ? "1min" : "5min";
    const r = await api.marketDataCandles([symbol], tf, tf === "1min" ? 5 : 20, "NSE", source.broker || undefined);
    const entry = r.symbols[symbol];
    if (!entry || entry.error || !entry.bars.length) throw new Error(entry?.error ?? `No candles for ${symbol}`);
    return { tf, candles: entry.bars, label: `broker:${r.source.broker}` };
  }

  async function buildPlan() {
    setBusy(L(lang, "Reading the market and testing strategies…", "Market वाचत आहे आणि strategies तपासत आहे…")); setError(null); setDone(null);
    try {
      const { tf, candles, label } = await candlesFor(answers.style || "intraday");
      const body: Record<string, string | number> = { ...answers };
      if (body.capital) body.capital = Number(String(body.capital).replace(/[^\d.]/g, ""));
      if (body.daily_loss) body.daily_loss = Number(body.daily_loss);
      setPlan(await api.aiInterviewPlan(body, tf, candles.slice(-3000), label));
    } catch (e) { setError(cleanError(e)); } finally { setBusy(null); }
  }

  async function act(label: string, fn: () => Promise<string>) {
    setBusy(label); setError(null); setDone(null);
    try { setDone(await fn()); } catch (e) { setError(cleanError(e)); } finally { setBusy(null); }
  }

  const finished = step >= queue.length;
  const symbolChoices = answers.instrument === "index" || !answers.instrument ? FNO_INDICES : FNO_STOCKS.slice(0, 12);

  return (
    <div className="space-y-3">
      <div className="max-h-[420px] space-y-2 overflow-y-auto rounded-lg border border-border bg-panel2/40 p-3">
        {log.map((m, i) => (
          <div key={i} className={`flex gap-2 ${m.from === "me" ? "justify-end" : ""}`}>
            {m.from === "ai" && <Bot size={16} className="mt-0.5 shrink-0 text-purple-300" />}
            <div className={`max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${m.from === "ai" ? "bg-panel3 text-slate-100" : "bg-brand/30 text-white"}`}>{m.text}</div>
            {m.from === "me" && <User size={16} className="mt-0.5 shrink-0 text-sky-300" />}
          </div>
        ))}
        {current && (
          <div className="flex gap-2">
            <Bot size={16} className="mt-0.5 shrink-0 text-purple-300" />
            <div className="max-w-[90%] space-y-1.5 rounded-lg bg-panel3 px-3 py-2 text-sm">
              <div className="font-semibold text-slate-50">{current.id === "language" ? `${current.mr} / ${current.en}` : qText(current)}</div>
              <div className="text-xs text-purple-200">💡 {lang === "mr" ? current.why_mr : current.why_en}</div>
              {current.kind === "choice" && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {current.options.map((o) => (
                    <button key={o.value} onClick={() => answer(o.value)}
                            className={`rounded-full border px-3 py-1 text-xs font-semibold hover:bg-brand/30 ${current.default === o.value ? "border-sky-400/60 text-sky-100" : "border-border text-slate-100"}`}>
                      {lang === "mr" ? o.mr : o.en}
                    </button>
                  ))}
                </div>
              )}
              {current.kind === "number" && (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  {CAPITAL_CHIPS.map((c) => (
                    <button key={c} onClick={() => answer(String(c), `₹${c.toLocaleString("en-IN")}`)} className="rounded-full border border-border px-3 py-1 text-xs font-semibold text-slate-100 hover:bg-brand/30">
                      ₹{c.toLocaleString("en-IN")}
                    </button>
                  ))}
                  <input value={custom} onChange={(e) => setCustom(e.target.value.replace(/[^\d]/g, ""))} placeholder={L(lang, "other amount", "दुसरी रक्कम")}
                         className="w-32 rounded border border-border bg-panel2 px-2 py-1 text-xs" />
                  <button disabled={Number(custom) < 5000} onClick={() => answer(custom, `₹${Number(custom).toLocaleString("en-IN")}`)}
                          className="rounded bg-brand px-2 py-1 text-xs font-semibold text-white disabled:opacity-40">OK</button>
                </div>
              )}
              {current.kind === "symbol" && (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  {symbolChoices.map((s) => (
                    <button key={s} onClick={() => answer(s)} className="rounded-full border border-border px-3 py-1 text-xs font-semibold text-slate-100 hover:bg-brand/30">{s}</button>
                  ))}
                  <input value={custom} list="interview-symbols" onChange={(e) => setCustom(e.target.value.toUpperCase())} placeholder={L(lang, "type a symbol", "symbol लिहा")}
                         className="w-36 rounded border border-border bg-panel2 px-2 py-1 text-xs" />
                  <datalist id="interview-symbols">{[...FNO_INDICES, ...FNO_STOCKS].map((s) => <option key={s} value={s} />)}</datalist>
                  <button disabled={!custom.trim()} onClick={() => answer(custom.trim())} className="rounded bg-brand px-2 py-1 text-xs font-semibold text-white disabled:opacity-40">OK</button>
                </div>
              )}
            </div>
          </div>
        )}
        {finished && !plan && (
          <div className="flex gap-2">
            <Bot size={16} className="mt-0.5 shrink-0 text-purple-300" />
            <div className="rounded-lg bg-panel3 px-3 py-2 text-sm text-slate-100">
              {L(lang, "Thank you - I know enough about you. Now I read the market (trend, structure, support/resistance, volatility) and test the strategies that fit.",
                       "धन्यवाद - तुमच्याबद्दल पुरेसे समजले. आता market वाचतो (trend, structure, support/resistance, volatility) आणि जुळणाऱ्या strategies तपासतो.")}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button disabled={!!busy} onClick={() => void buildPlan()} className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
                  <Compass size={12} className="mr-1 inline" />{L(lang, "Read the market and build my plan", "Market वाचा आणि माझा plan बनवा")}
                </button>
                <span className="text-xs text-muted">{source.mode === "sample" ? L(lang, "on sample data - pick broker candles above for today's real market", "sample data वर - आजच्या खऱ्या market साठी वर broker candles निवडा") : L(lang, "on broker candles", "broker candles वर")}</span>
              </div>
            </div>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {step > 0 && <button onClick={back} className="rounded border border-border px-2 py-1 text-slate-200 hover:bg-panel2"><ArrowLeft size={12} className="mr-1 inline" />{L(lang, "Back", "मागे")}</button>}
        {!finished && <span className="text-muted">{L(lang, `Question ${step + 1} of ${queue.length}`, `प्रश्न ${step + 1} / ${queue.length}`)}</span>}
        {busy && <span className="text-sky-300">{busy}</span>}
        {error && <span className="text-danger">{error}</span>}
        {done && <span className="text-emerald-300">{done}</span>}
      </div>

      {plan && <PlanView plan={plan} lang={plan.language} busy={!!busy} onAct={act} onDraft={onDraft} broker={source.mode === "broker" ? source.broker : undefined} />}
    </div>
  );
}

function PlanView({ plan, lang, busy, onAct, onDraft, broker }: {
  plan: InterviewPlan; lang: Lang; busy: boolean; broker?: string;
  onAct: (label: string, fn: () => Promise<string>) => void;
  onDraft?: (draft: AiStrategyDraft) => void;
}) {
  const pick = plan.recommended;
  const symbol = String(plan.answers.symbol ?? "NIFTY 50");
  const biasCls = plan.market.bias === "BULLISH" ? "text-emerald-300" : plan.market.bias === "BEARISH" ? "text-rose-300" : "text-amber-200";
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-purple-500/40 bg-purple-500/10 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <Sparkles size={18} className="text-purple-300" />
          <div className="text-base font-extrabold text-slate-50">{L(lang, "Your trading plan", "तुमचा trading plan")} · {symbol}</div>
          <span className={`text-sm font-bold ${biasCls}`}>{plan.market.bias}</span>
          {pick && <span className="rounded-full border border-sky-400/50 px-2 py-0.5 text-xs font-semibold text-sky-100">{pick.name}</span>}
        </div>
        {plan.warnings.length > 0 && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-amber-200">{plan.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {plan.sections.map((s) => (
          <div key={s.id} className="rounded-lg border border-border bg-panel2/40 p-3">
            <div className="mb-1 text-xs font-bold uppercase tracking-wider text-purple-200">{s.title}</div>
            <ul className="space-y-1 text-sm text-slate-100">{s.lines.map((l, i) => <li key={i} className="flex gap-1.5"><span className="text-purple-300">•</span><span>{l}</span></li>)}</ul>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => {
          if (!window.confirm(L(lang, "Replace your current risk settings with this plan's?", "तुमच्या सध्याच्या risk settings ऐवजी या plan च्या settings लावायच्या?"))) return;
          onAct(L(lang, "Saving…", "Save करत आहे…"), async () => { await api.updateRiskSettings(plan.risk_config); return L(lang, "Risk settings applied.", "Risk settings लागू झाल्या."); });
        }} className="rounded bg-sky-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-50">
          <ShieldCheck size={12} className="mr-1 inline" />{L(lang, "Apply risk settings", "Risk settings लागू करा")}
        </button>
        {plan.deployment && (
          <button disabled={busy} onClick={() => onAct(L(lang, "Deploying…", "Deploy करत आहे…"), async () => {
            const d = await api.createDeployment(plan.deployment!);
            return L(lang, `Deployment #${d.id} is running in PAPER. Watch it on the Autopilot page.`, `Deployment #${d.id} PAPER मध्ये सुरू झाले. Autopilot page वर पहा.`);
          })} className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
            <Rocket size={12} className="mr-1 inline" />{L(lang, "Deploy in PAPER", "PAPER मध्ये deploy करा")}
          </button>
        )}
        <button onClick={() => window.open(chartWindowUrl(symbol, "5min", "NSE", broker), "_blank")} className="rounded border border-border px-3 py-1.5 text-xs font-semibold text-slate-100 hover:bg-panel2">
          <CandlestickChart size={12} className="mr-1 inline" />{L(lang, "Open the chart", "Chart उघडा")}
        </button>
        {onDraft && (
          <button disabled={busy} onClick={() => onAct(L(lang, "Asking the AI…", "AI ला विचारत आहे…"), async () => {
            const d = await api.aiGenerate(plan.ai_prompt, { language: lang, regime: plan.market.regime.kind, symbol });
            onDraft(d);
            return L(lang, `AI draft #${d.id} is ready for review below.`, `AI draft #${d.id} तयार आहे - खाली तपासा.`);
          })} className="rounded border border-purple-500/50 px-3 py-1.5 text-xs font-semibold text-purple-100 hover:bg-purple-500/20 disabled:opacity-50">
            <Sparkles size={12} className="mr-1 inline" />{L(lang, "Ask the AI for a custom rule set", "AI कडून custom strategy बनवा")}
          </button>
        )}
      </div>
      <div className="text-[11px] text-muted">{plan.disclaimer}</div>
    </div>
  );
}
