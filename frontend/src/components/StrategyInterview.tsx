import { ArrowLeft, Bot, CandlestickChart, Check, Compass, History, Rocket, ShieldCheck, Sparkles, ThumbsDown, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AiStrategyDraft, FeedbackOption, InterviewPlan, InterviewQuestion, InterviewStart, OHLCVBar } from "../types";
import { FNO_INDICES, FNO_STOCKS } from "../utils/fnoSymbols";
import type { CandleSourceState } from "./DataSource";
import { chartWindowUrl } from "./ProChart";

/**
 * Phase AP: the strategy interview. A beginner who asks "give me a strategy" is asked about
 * themselves first (in Marathi or English), then the platform reads the market and builds a plan:
 * a template with its evidence, risk management, R:R, the contract to trade and a PAPER deployment.
 * Nothing is applied until the trader presses a button.
 *
 * Phase AQ / P0.8-D: three risk settings (safe / balanced / active) on templates the trader chooses
 * between - described, never recommended, no match %; "not this one" asks why and the next round is
 * rebuilt from the reasons. The answers
 * and what was learnt are remembered, so the next visit can skip the questions.
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
  const [offerProfile, setOfferProfile] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const fetched = useRef<{ tf: string; candles: OHLCVBar[]; label: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const lang: Lang = (answers.language as Lang) || start?.language || "mr";
  const byId = (id: string) => start?.questions.find((q) => q.id === id);
  const current: InterviewQuestion | undefined = queue[step] ? byId(queue[step]) : undefined;

  useEffect(() => {
    if (!startKey) return;
    let cancelled = false;
    setPlan(null); setError(null); setDone(null); setStep(0); setCustom(""); setSelected(null); setRejecting(null); setReasons([]);
    fetched.current = null;
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
      setOfferProfile(!!s.profile);
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

  /** Phase AQ: skip the questions - last visit's answers, with anything the new message said on top. */
  function applyProfile() {
    if (!start?.profile) return;
    const saved = Object.fromEntries(Object.entries(start.profile.answers).map(([k, v]) => [k, String(v)]));
    setAnswers({ ...saved, ...start.prefill });
    setStep(queue.length);
    setOfferProfile(false);
    setLog((prev) => [...prev, { from: "me", text: L(lang, "Yes, use my answers from last time", "हो, मागची उत्तरे वापरा") }]);
  }

  async function forgetProfile() {
    await act(L(lang, "Forgetting…", "विसरत आहे…"), async () => {
      await api.aiProfileDelete(); setOfferProfile(false);
      return L(lang, "Your saved answers and preferences are deleted.", "तुमची साठवलेली उत्तरे आणि आवडी काढून टाकल्या.");
    });
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
      const r = await source.fetch([symbol], "1min", { count: style === "swing" ? 300 : 3000, startPriceFor: () => 24_000, seedFor: () => 11 });
      const bars = r.candles[symbol] ?? [];
      if (style !== "swing") return { tf: "1min", candles: bars, label: "sample" };
      // Sample bars are one minute apart: re-date them one trading day apart for a swing read.
      const days: string[] = [];
      const d = new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), new Date().getUTCDate()));
      while (days.length < bars.length) {
        d.setUTCDate(d.getUTCDate() - 1);
        if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) days.unshift(new Date(d).toISOString());
      }
      return { tf: "day", candles: bars.map((b, i) => ({ ...b, timestamp: days[i] })), label: "sample" };
    }
    // Phase AS: a swing plan reads a year of daily candles.
    const tf = style === "swing" ? "day" : style === "scalping" ? "1min" : "5min";
    const r = await api.marketDataCandles([symbol], tf, tf === "day" ? 400 : tf === "1min" ? 5 : 20, "NSE", source.broker || undefined);
    const entry = r.symbols[symbol];
    if (!entry || entry.error || !entry.bars.length) throw new Error(entry?.error ?? `No candles for ${symbol}`);
    return { tf, candles: entry.bars, label: `broker:${r.source.broker}` };
  }

  function answersBody(): Record<string, string | number> {
    const body: Record<string, string | number> = { ...answers };
    if (body.capital) body.capital = Number(String(body.capital).replace(/[^\d.]/g, ""));
    if (body.daily_loss) body.daily_loss = Number(body.daily_loss);
    return body;
  }

  async function buildPlan() {
    setBusy(L(lang, "Reading the market and testing strategies…", "Market वाचत आहे आणि strategies तपासत आहे…")); setError(null); setDone(null);
    try {
      const data = await candlesFor(answers.style || "intraday");
      fetched.current = { ...data, candles: data.candles.slice(-3000) };
      const result = await api.aiInterviewPlan(answersBody(), data.tf, fetched.current.candles, data.label);
      setPlan(result); setSelected("balanced");
    } catch (e) { setError(cleanError(e)); } finally { setBusy(null); }
  }

  /** "Not this one, because..." -> the next three options. */
  async function refine(option: InterviewPlan) {
    const f = fetched.current;
    if (!f || !option.option || reasons.length === 0) return;
    setBusy(L(lang, "Finding options closer to what you want…", "तुमच्या पसंतीच्या जवळचे पर्याय शोधत आहे…")); setError(null); setDone(null);
    try {
      const result = await api.aiInterviewRefine(answersBody(), f.tf, f.candles, f.label, reasons, option.option.id, option.recommended?.strategy_id ?? null);
      // Feedback may change answers (style, time, vehicle): keep the page in step with the server.
      setAnswers(Object.fromEntries(Object.entries(result.answers).map(([k, v]) => [k, String(v)])));
      setPlan(result); setSelected("balanced"); setRejecting(null); setReasons([]);
    } catch (e) { setError(cleanError(e)); } finally { setBusy(null); }
  }

  async function choose(option: InterviewPlan) {
    if (!option.option) return;
    setSelected(option.option.id);
    try { await api.aiInterviewChoose(answersBody(), option.option.id, option.recommended?.strategy_id ?? null); } catch { /* remembering is best effort */ }
    setDone(L(lang, `"${option.option.label}" chosen - details and buttons below.`, `"${option.option.label}" निवडला - तपशील आणि बटणे खाली.`));
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
        {offerProfile && start.profile && step === 0 && (
          <div className="flex gap-2">
            <History size={16} className="mt-0.5 shrink-0 text-amber-300" />
            <div className="max-w-[90%] rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-slate-100">
              {L(lang, "Welcome back! I remember your answers from last time", "पुन्हा स्वागत! मागच्या वेळची तुमची उत्तरे मला आठवतात")}
              {" "}(₹{Number(start.profile.answers.capital ?? 0).toLocaleString("en-IN")}, {String(start.profile.answers.style ?? "")}, {String(start.profile.answers.symbol ?? "")}).
              {" "}{L(lang, "Use them and go straight to the options?", "तीच वापरून थेट पर्याय दाखवू का?")}
              <div className="mt-2 flex flex-wrap gap-2">
                <button onClick={applyProfile} className="rounded bg-amber-600 px-3 py-1 text-xs font-bold text-white hover:bg-amber-500">{L(lang, "Yes, use them", "हो, तीच वापरा")}</button>
                <button onClick={() => setOfferProfile(false)} className="rounded border border-border px-3 py-1 text-xs text-slate-100 hover:bg-panel2">{L(lang, "No, ask me again", "नाही, पुन्हा विचारा")}</button>
                <button onClick={() => void forgetProfile()} className="text-xs text-rose-300 hover:underline">{L(lang, "Forget me", "मला विसरा")}</button>
              </div>
            </div>
          </div>
        )}
        {current && !offerProfile && (
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
              {L(lang, "Thank you - I know enough about you. Now I read the market (trend, structure, support/resistance, volatility) and test the templates.",
                       "धन्यवाद - तुमच्याबद्दल पुरेसे समजले. आता market चा data वाचतो (trend, structure, support/resistance, volatility) आणि templates तपासतो.")}
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

      {plan && plan.options && (
        <OptionsView plan={plan} lang={plan.language} selected={selected} busy={!!busy} rejecting={rejecting} reasons={reasons}
                     onChoose={(o) => void choose(o)} onReject={(id) => { setRejecting(rejecting === id ? null : id); setReasons([]); }}
                     onToggleReason={(code) => setReasons((r) => (r.includes(code) ? r.filter((c) => c !== code) : [...r, code]))}
                     onRefine={(o) => void refine(o)} />
      )}
      {plan && (() => {
        const shown = plan.options?.find((o) => o.option?.id === selected) ?? plan;
        return <PlanView plan={shown} lang={plan.language} busy={!!busy} onAct={act} onDraft={onDraft} broker={source.mode === "broker" ? source.broker : undefined} />;
      })()}
    </div>
  );
}

const OPTION_STYLE: Record<string, string> = {
  safe: "border-emerald-500/50 bg-emerald-500/5",
  balanced: "border-sky-500/50 bg-sky-500/5",
  active: "border-amber-500/50 bg-amber-500/5",
};

function OptionsView({ plan, lang, selected, busy, rejecting, reasons, onChoose, onReject, onToggleReason, onRefine }: {
  plan: InterviewPlan; lang: Lang; selected: string | null; busy: boolean; rejecting: string | null; reasons: string[];
  onChoose: (o: InterviewPlan) => void; onReject: (id: string) => void; onToggleReason: (code: string) => void; onRefine: (o: InterviewPlan) => void;
}) {
  const feedback: FeedbackOption[] = plan.feedback_options ?? [];
  // P0.8-D: templates are described and the trader chooses; nothing here is recommended or scored against the trader.
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-extrabold text-slate-50">{L(lang, "Three templates - you choose", "तीन templates - निवड तुमची")}</span>
        <span className="text-xs text-muted">{L(lang, "Each template's rules are in words below; read the backtest; the decision is yours. This is not a recommendation.",
          "प्रत्येक template चे नियम खाली शब्दांत आहेत; backtest पाहा; निर्णय तुमचा. ही शिफारस नाही.")}</span>
      </div>
      {plan.changes && plan.changes.length > 0 && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2 text-xs text-emerald-100">
          <b>{L(lang, "Changed from your feedback:", "तुमच्या सांगण्यावरून बदल:")}</b>
          <ul className="mt-1 list-disc pl-5">{plan.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        {(plan.options ?? []).map((o) => {
          const meta = o.option!;
          const isSel = selected === meta.id;
          return (
            <div key={meta.id} className={`rounded-lg border p-3 ${OPTION_STYLE[meta.id]} ${isSel ? "ring-2 ring-purple-400" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-base font-extrabold text-slate-50">{meta.label}</div>
                  <div className="text-xs text-slate-300">{meta.summary}</div>
                </div>
              </div>
              {meta.headline && (
                <ul className="mt-2 space-y-0.5 text-xs text-slate-100">
                  <li>📈 {meta.headline.strategy}</li>
                  <li>🛡️ {L(lang, "Risk per trade", "एका trade चा risk")} {meta.headline.risk_pct}%</li>
                  <li>🔁 {L(lang, "Up to", "दिवसाला")} {meta.headline.trades_per_day} {L(lang, "trades a day", "trades पर्यंत")}</li>
                  <li>🎯 {L(lang, "Reward:risk at least", "किमान reward:risk")} 1:{meta.headline.min_rr}</li>
                </ul>
              )}
              <div className="mt-2 flex items-center gap-2 text-[11px]">
                <span className="text-slate-300">{L(lang, "Regime fit (data)", "स्थितीशी जुळणी (data)")}</span>
                <div className="h-1.5 flex-1 rounded bg-panel3"><div className={`h-1.5 rounded ${meta.market_fit >= 60 ? "bg-emerald-400" : meta.market_fit >= 40 ? "bg-amber-400" : "bg-rose-400"}`} style={{ width: `${meta.market_fit}%` }} /></div>
                <span className="font-semibold text-slate-100">{meta.market_fit}%</span>
              </div>
              <div className="mt-2 flex gap-2">
                <button disabled={busy} onClick={() => onChoose(o)} className="rounded bg-purple-600 px-2.5 py-1 text-xs font-bold text-white hover:bg-purple-500 disabled:opacity-50">
                  <Check size={12} className="mr-1 inline" />{isSel ? L(lang, "Showing", "दिसत आहे") : L(lang, "Choose this", "हा निवडा")}
                </button>
                <button disabled={busy} onClick={() => onReject(meta.id)} className="rounded border border-rose-500/50 px-2.5 py-1 text-xs font-semibold text-rose-200 hover:bg-rose-500/10 disabled:opacity-50">
                  <ThumbsDown size={12} className="mr-1 inline" />{L(lang, "Not this", "हे नको")}
                </button>
              </div>
              {rejecting === meta.id && (
                <div className="mt-2 space-y-1.5 rounded border border-border bg-panel2/60 p-2">
                  <div className="text-xs font-semibold text-slate-100">{L(lang, "Why not? (pick one or more)", "का नको? (एक किंवा जास्त निवडा)")}</div>
                  <div className="flex flex-wrap gap-1">
                    {feedback.map((f) => (
                      <button key={f.code} onClick={() => onToggleReason(f.code)}
                              className={`rounded-full border px-2 py-0.5 text-[11px] ${reasons.includes(f.code) ? "border-rose-400 bg-rose-500/30 text-white" : "border-border text-slate-200"}`}>
                        {lang === "mr" ? f.mr : f.en}
                      </button>
                    ))}
                  </div>
                  <button disabled={busy || reasons.length === 0} onClick={() => onRefine(o)} className="rounded bg-sky-600 px-2.5 py-1 text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-40">
                    {L(lang, "Show me better options", "पुढचे पर्याय दाखवा")}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
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
  // P0.8 / A3: "Deploy in PAPER" goes through the server's candidate with the trader's risk acceptance.
  const [acceptRisk, setAcceptRisk] = useState(false);
  const perTrade = Math.round((plan.risk_config.capital * plan.risk_config.risk_per_trade_pct) / 100);
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
        {plan.deployment && plan.candidate_id != null && (
          <>
            <label className="flex items-center gap-1.5 text-[11px] text-slate-200">
              <input type="checkbox" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} />
              {L(lang, `I accept a maximum loss of about ₹${perTrade.toLocaleString("en-IN")} per trade`, `मी प्रति trade जास्तीत जास्त ₹${perTrade.toLocaleString("en-IN")} तोटा स्वीकारतो/स्वीकारते`)}
            </label>
            <button disabled={busy || !acceptRisk} onClick={() => onAct(L(lang, "Deploying…", "Deploy करत आहे…"), async () => {
              const d = await api.aiInterviewDeploy(plan.candidate_id!, acceptRisk);
              return L(lang, `Deployment #${d.deployment.id} is running in PAPER. Watch it on the Autopilot page.`, `Deployment #${d.deployment.id} PAPER मध्ये सुरू झाले. Autopilot page वर पहा.`);
            })} className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
              <Rocket size={12} className="mr-1 inline" />{L(lang, "Deploy in PAPER", "PAPER मध्ये deploy करा")}
            </button>
          </>
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
