import { ArrowLeft, Bot, CandlestickChart, Check, Compass, History, Rocket, ShieldCheck, Sparkles, ThumbsDown, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AiStrategyDraft, FeedbackOption, InterviewPlan, InterviewQuestion, InterviewStart, OHLCVBar } from "../types";
import { FNO_INDICES, FNO_STOCKS } from "../utils/fnoSymbols";
import type { CandleSourceState } from "./DataSource";
import { chartWindowUrl } from "./chartHelpers";
import { INTERVIEW_MR } from "../i18n/interviewSecondary";
import SampleStamp from "./SampleStamp";

/**
 * Phase AP: the strategy interview. A beginner who asks "give me a strategy" is asked about
 * themselves first, then the platform reads the market and describes three templates:
 * a template with its evidence, risk management, R:R, the contract to trade and a PAPER deployment.
 * Nothing is applied until the trader presses a button.
 *
 * Phase AQ / P0.8-D: three risk settings (safe / balanced / active) on templates the trader chooses
 * between - described, never recommended, no match %; "not this one" asks why and the next round is
 * rebuilt from the reasons. The answers
 * and what was learnt are remembered, so the next visit can skip the questions.
 *
 * P0.9: English UI. Each question shows its English text with a small muted Marathi line under it, and each
 * answer chip shows both. No template is highlighted or pre-selected: the details appear only after the trader
 * chooses one, and nothing states a market direction.
 * P0.10: the whole interview is bilingual in one style - intro, questions, options, tips, every button, the template
 * headings: the English line first, a small muted Marathi line under it (`Mr`).
 */

/** The muted Marathi line under an English one (the interview's only secondary language). */
function Mr({ children, inline = false }: { children?: string; inline?: boolean }) {
  if (!children) return null;
  return <span lang="mr" className={`${inline ? "" : "block "}text-[10px] font-normal leading-snug text-muted`}>{children}</span>;
}

const CAPITAL_CHIPS = [50_000, 100_000, 200_000, 500_000];

interface Msg { from: "ai" | "me"; text: string; mr?: string }

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

  const byId = (id: string) => start?.questions.find((q) => q.id === id);
  const current: InterviewQuestion | undefined = queue[step] ? byId(queue[step]) : undefined;

  useEffect(() => {
    if (!startKey) return;
    let cancelled = false;
    setPlan(null); setError(null); setDone(null); setStep(0); setCustom(""); setSelected(null); setRejecting(null); setReasons([]);
    fetched.current = null;
    api.aiInterviewStart(startPrompt).then((s) => {
      if (cancelled) return;
      // P0.9: the plan is written in English (the dashboard language); the language question is not asked.
      const prefill: Record<string, string> = { ...s.prefill, language: "en" };
      const order = s.questions.map((q) => q.id).filter((id) => !(id in prefill));
      setStart(s); setAnswers(prefill); setQueue(order);
      const known = Object.keys(s.prefill).filter((k) => k !== "language");
      const intro: Msg[] = [{ from: "ai", text: s.intro, mr: s.intro_mr }];
      if (known.length) {
        const list = known.map((k) => `${k} = ${prefill[k]}`).join(", ");
        intro.push({ from: "ai", text: "Already understood from your message: " + list, mr: `${INTERVIEW_MR.alreadyUnderstood} ${list}` });
      }
      setLog(intro);
      setOfferProfile(!!s.profile);
    }).catch((e) => setError(cleanError(e)));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startKey]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [log, step, plan]);

  if (!startKey || !start) return error ? <div className="text-sm text-danger">{error}</div> : null;

  const optLabel = (q: InterviewQuestion, value: string) => q.options.find((x) => x.value === value)?.en ?? value;
  const optLabelMr = (q: InterviewQuestion, value: string) => {
    const o = q.options.find((x) => x.value === value);
    return o?.mr && o.mr !== o.en ? o.mr : undefined;
  };

  function answer(value: string, label?: string) {
    if (!current) return;
    const q = current;
    const next = { ...answers, [q.id]: value };
    // A stock-only trader is not offered index symbols as the default.
    if (q.id === "instrument" && value !== "index" && (answers.symbol ?? "NIFTY 50").startsWith("NIFTY")) delete next.symbol;
    setAnswers(next);
    setLog((prev) => [...prev,
      { from: "ai", text: q.en, mr: q.mr },
      { from: "me", text: label ?? optLabel(q, value), mr: label ? undefined : optLabelMr(q, value) }]);
    setCustom("");
    setStep((s) => s + 1);
  }

  /** Phase AQ: skip the questions - last visit's answers, with anything the new message said on top. */
  function applyProfile() {
    if (!start?.profile) return;
    const saved = Object.fromEntries(Object.entries(start.profile.answers).map(([k, v]) => [k, String(v)]));
    setAnswers({ ...saved, ...start.prefill, language: "en" });     // P0.9: older profiles saved "mr"; the plan is English
    setStep(queue.length);
    setOfferProfile(false);
    setLog((prev) => [...prev, { from: "me", text: "Yes, use my answers from last time", mr: INTERVIEW_MR.usedLastAnswers }]);
  }

  async function forgetProfile() {
    await act("Forgetting…", async () => {
      await api.aiProfileDelete(); setOfferProfile(false);
      return "Your saved answers and preferences are deleted.";
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
      // P0.10: a swing read gets sample DAILY bars (day-sized moves), not minute bars re-dated as days.
      const swing = style === "swing";
      const r = await source.fetch([symbol], swing ? "day" : "1min", { count: swing ? 300 : 3000, startPriceFor: () => 24_000, seedFor: () => 11, daily: swing });
      return { tf: swing ? "day" : "1min", candles: r.candles[symbol] ?? [], label: "sample" };
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
    setBusy("Reading the market and testing the templates…"); setError(null); setDone(null);   // the Marathi line is shown with it
    try {
      const data = await candlesFor(answers.style || "intraday");
      fetched.current = { ...data, candles: data.candles.slice(-3000) };
      const result = await api.aiInterviewPlan(answersBody(), data.tf, fetched.current.candles, data.label);
      setPlan(result); setSelected(null);          // P0.9: nothing pre-selected - the trader opens a template
    } catch (e) { setError(cleanError(e)); } finally { setBusy(null); }
  }

  /** "Not this one, because..." -> the next three options. */
  async function refine(option: InterviewPlan) {
    const f = fetched.current;
    if (!f || !option.option || reasons.length === 0) return;
    setBusy("Finding templates closer to what you asked for…"); setError(null); setDone(null);
    try {
      const result = await api.aiInterviewRefine(answersBody(), f.tf, f.candles, f.label, reasons, option.option.id, option.recommended?.strategy_id ?? null);
      // Feedback may change answers (style, time, vehicle): keep the page in step with the server.
      setAnswers(Object.fromEntries(Object.entries(result.answers).map(([k, v]) => [k, String(v)])));
      setPlan(result); setSelected(null); setRejecting(null); setReasons([]);
    } catch (e) { setError(cleanError(e)); } finally { setBusy(null); }
  }

  async function choose(option: InterviewPlan) {
    if (!option.option) return;
    setSelected(option.option.id);
    try { await api.aiInterviewChoose(answersBody(), option.option.id, option.recommended?.strategy_id ?? null); } catch { /* remembering is best effort */ }
    setDone(`"${option.option.label}" chosen - its rules, backtest and buttons are below.`);
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
            <div className={`max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${m.from === "ai" ? "bg-panel3 text-slate-100" : "bg-brand/30 text-white"}`}>{m.text}<Mr>{m.mr}</Mr></div>
            {m.from === "me" && <User size={16} className="mt-0.5 shrink-0 text-sky-300" />}
          </div>
        ))}
        {offerProfile && start.profile && step === 0 && (
          <div className="flex gap-2">
            <History size={16} className="mt-0.5 shrink-0 text-amber-300" />
            <div className="max-w-[90%] rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-slate-100">
              Welcome back! Your answers from last time are saved
              {" "}(₹{Number(start.profile.answers.capital ?? 0).toLocaleString("en-IN")}, {String(start.profile.answers.style ?? "")}, {String(start.profile.answers.symbol ?? "")}).
              {" "}Use them and go straight to the templates?
              <Mr>{INTERVIEW_MR.welcomeBack}</Mr>
              <div className="mt-2 flex flex-wrap gap-2">
                <button onClick={applyProfile} className="rounded bg-amber-600 px-3 py-1 text-xs font-bold text-white hover:bg-amber-500">Yes, use them<Mr>{INTERVIEW_MR.yesUse}</Mr></button>
                <button onClick={() => setOfferProfile(false)} className="rounded border border-border px-3 py-1 text-xs text-slate-100 hover:bg-panel2">No, ask me again<Mr>{INTERVIEW_MR.noAsk}</Mr></button>
                <button onClick={() => void forgetProfile()} className="text-xs text-rose-300 hover:underline">Forget me<Mr>{INTERVIEW_MR.forget}</Mr></button>
              </div>
            </div>
          </div>
        )}
        {current && !offerProfile && (
          <div className="flex gap-2">
            <Bot size={16} className="mt-0.5 shrink-0 text-purple-300" />
            <div className="max-w-[90%] space-y-1.5 rounded-lg bg-panel3 px-3 py-2 text-sm">
              <div className="font-semibold text-slate-50">{current.en}</div>
              <Mr>{current.mr}</Mr>
              <div className="text-xs text-purple-200">💡 {current.why_en}<Mr>{current.why_mr}</Mr></div>
              {current.kind === "choice" && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {current.options.map((o) => (
                    <button key={o.value} onClick={() => answer(o.value)}
                            className="rounded-full border border-border px-3 py-1 text-xs font-semibold text-slate-100 hover:bg-brand/30">
                      {o.en}{o.mr && o.mr !== o.en && <span className="ml-1 font-normal text-muted" lang="mr">/ {o.mr}</span>}
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
                  <input value={custom} onChange={(e) => setCustom(e.target.value.replace(/[^\d]/g, ""))} placeholder={`other amount / ${INTERVIEW_MR.otherAmount}`}
                         className="w-32 rounded border border-border bg-panel2 px-2 py-1 text-xs" />
                  <button disabled={Number(custom) < 5000} onClick={() => answer(custom, `₹${Number(custom).toLocaleString("en-IN")}`)}
                          className="rounded bg-brand px-2 py-1 text-xs font-semibold text-white disabled:opacity-40">OK<Mr>{INTERVIEW_MR.ok}</Mr></button>
                </div>
              )}
              {current.kind === "symbol" && (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  {symbolChoices.map((s) => (
                    <button key={s} onClick={() => answer(s)} className="rounded-full border border-border px-3 py-1 text-xs font-semibold text-slate-100 hover:bg-brand/30">{s}</button>
                  ))}
                  <input value={custom} list="interview-symbols" onChange={(e) => setCustom(e.target.value.toUpperCase())} placeholder={`type a symbol / ${INTERVIEW_MR.typeSymbol}`}
                         className="w-36 rounded border border-border bg-panel2 px-2 py-1 text-xs" />
                  <datalist id="interview-symbols">{[...FNO_INDICES, ...FNO_STOCKS].map((s) => <option key={s} value={s} />)}</datalist>
                  <button disabled={!custom.trim()} onClick={() => answer(custom.trim())} className="rounded bg-brand px-2 py-1 text-xs font-semibold text-white disabled:opacity-40">OK<Mr>{INTERVIEW_MR.ok}</Mr></button>
                </div>
              )}
            </div>
          </div>
        )}
        {finished && !plan && (
          <div className="flex gap-2">
            <Bot size={16} className="mt-0.5 shrink-0 text-purple-300" />
            <div className="rounded-lg bg-panel3 px-3 py-2 text-sm text-slate-100">
              Thank you - that is enough. Next the market data is read (trend, structure, support/resistance, volatility) and three templates are tested on it.
              <Mr>{INTERVIEW_MR.thanks}</Mr>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button disabled={!!busy} onClick={() => void buildPlan()} className="rounded bg-emerald-600 px-3 py-1.5 text-left text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
                  <Compass size={12} className="mr-1 inline" />Read the market and show the templates
                  <Mr>{INTERVIEW_MR.readMarket}</Mr>
                </button>
                <span className="text-xs text-muted">
                  {source.mode === "sample" ? "on SAMPLE data - pick broker candles above for today's real market" : "on broker candles"}
                  <Mr>{source.mode === "sample" ? INTERVIEW_MR.onSample : INTERVIEW_MR.onBroker}</Mr>
                </span>
              </div>
            </div>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {step > 0 && <button onClick={back} className="rounded border border-border px-2 py-1 text-left text-slate-200 hover:bg-panel2"><ArrowLeft size={12} className="mr-1 inline" />Back<Mr>{INTERVIEW_MR.back}</Mr></button>}
        {!finished && <span className="text-muted">Question {step + 1} of {queue.length}<Mr>{`${INTERVIEW_MR.question} ${step + 1} / ${queue.length}`}</Mr></span>}
        {busy && <span className="text-sky-300">{busy}{busy.startsWith("Reading the market") && <Mr>{INTERVIEW_MR.reading}</Mr>}</span>}
        {error && <span className="text-danger">{error}</span>}
        {done && <span className="text-emerald-300">{done}</span>}
      </div>

      {plan && plan.options && (
        <OptionsView plan={plan} selected={selected} busy={!!busy} rejecting={rejecting} reasons={reasons}
                     onChoose={(o) => void choose(o)} onReject={(id) => { setRejecting(rejecting === id ? null : id); setReasons([]); }}
                     onToggleReason={(code) => setReasons((r) => (r.includes(code) ? r.filter((c) => c !== code) : [...r, code]))}
                     onRefine={(o) => void refine(o)} />
      )}
      {plan && (() => {
        // P0.9: details only for the template the trader chose - nothing is shown (or implied) before that.
        const shown = plan.options ? plan.options.find((o) => o.option?.id === selected) : plan;
        if (!shown) return <div className="text-xs text-muted">Choose a template above to see its rules, backtest, risk settings and buttons.<Mr>{INTERVIEW_MR.openTemplate}</Mr></div>;
        return <PlanView plan={shown} sample={fetched.current?.label === "sample"} busy={!!busy} onAct={act} onDraft={onDraft} broker={source.mode === "broker" ? source.broker : undefined} />;
      })()}
    </div>
  );
}

function OptionsView({ plan, selected, busy, rejecting, reasons, onChoose, onReject, onToggleReason, onRefine }: {
  plan: InterviewPlan; selected: string | null; busy: boolean; rejecting: string | null; reasons: string[];
  onChoose: (o: InterviewPlan) => void; onReject: (id: string) => void; onToggleReason: (code: string) => void; onRefine: (o: InterviewPlan) => void;
}) {
  const feedback: FeedbackOption[] = plan.feedback_options ?? [];
  // P0.8-D: templates are described and the trader chooses; nothing here is recommended or scored against the trader.
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-extrabold text-slate-50">Three templates - you choose<Mr>{INTERVIEW_MR.chooseTemplate}</Mr></span>
        <span className="text-xs text-muted">Open a template to read its rules in words and its backtest. The decision is yours; this is not a recommendation.<Mr>{INTERVIEW_MR.chooseTemplateHint}</Mr></span>
      </div>
      {plan.changes && plan.changes.length > 0 && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2 text-xs text-emerald-100">
          <b>Changed from your feedback:</b><Mr>{INTERVIEW_MR.changedFromFeedback}</Mr>
          <ul className="mt-1 list-disc pl-5">{plan.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        {(plan.options ?? []).map((o) => {
          const meta = o.option!;
          const isSel = selected === meta.id;
          return (
            <div key={meta.id} className={`rounded-lg border border-border bg-panel2/30 p-3 ${isSel ? "ring-2 ring-purple-400" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-base font-extrabold text-slate-50">{meta.label}<Mr>{meta.label_mr}</Mr></div>
                  <div className="text-xs text-slate-300">{meta.summary}<Mr>{meta.summary_mr}</Mr></div>
                </div>
              </div>
              {meta.headline && (
                <ul className="mt-2 space-y-0.5 text-xs text-slate-100">
                  <li>📈 {meta.headline.strategy}</li>
                  <li>🛡️ Risk per trade {meta.headline.risk_pct}%</li>
                  <li>🔁 Up to {meta.headline.trades_per_day} trades a day</li>
                  <li>🎯 Reward:risk at least 1:{meta.headline.min_rr}</li>
                </ul>
              )}
              {meta.regime_filter_open !== null && (
                <div className="mt-2 text-[11px] text-slate-300">
                  {meta.regime_filter_open
                    ? "Today's data: this template's regime filter is open"
                    : "Today's data: this template's regime filter is closed (it would not enter)"}
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button disabled={busy} onClick={() => onChoose(o)} className="rounded bg-purple-600 px-2.5 py-1 text-left text-xs font-bold text-white hover:bg-purple-500 disabled:opacity-50">
                  <Check size={12} className="mr-1 inline" />{isSel ? "Chosen" : "Choose this"}<Mr>{isSel ? INTERVIEW_MR.chosen : INTERVIEW_MR.chooseThis}</Mr>
                </button>
                <button disabled={busy} onClick={() => onReject(meta.id)} className="rounded border border-rose-500/50 px-2.5 py-1 text-left text-xs font-semibold text-rose-200 hover:bg-rose-500/10 disabled:opacity-50">
                  <ThumbsDown size={12} className="mr-1 inline" />Not this<Mr>{INTERVIEW_MR.notThis}</Mr>
                </button>
              </div>
              {rejecting === meta.id && (
                <div className="mt-2 space-y-1.5 rounded border border-border bg-panel2/60 p-2">
                  <div className="text-xs font-semibold text-slate-100">Why not? (pick one or more)<Mr>{INTERVIEW_MR.whyNot}</Mr></div>
                  <div className="flex flex-wrap gap-1">
                    {feedback.map((f) => (
                      <button key={f.code} onClick={() => onToggleReason(f.code)}
                              className={`rounded-full border px-2 py-0.5 text-[11px] ${reasons.includes(f.code) ? "border-rose-400 bg-rose-500/30 text-white" : "border-border text-slate-200"}`}>
                        {f.en}{f.mr && f.mr !== f.en && <span lang="mr" className="ml-1 text-muted">/ {f.mr}</span>}
                      </button>
                    ))}
                  </div>
                  <button disabled={busy || reasons.length === 0} onClick={() => onRefine(o)} className="rounded bg-sky-600 px-2.5 py-1 text-left text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-40">
                    Show other templates<Mr>{INTERVIEW_MR.showOther}</Mr>
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

function PlanView({ plan, sample, busy, onAct, onDraft, broker }: {
  plan: InterviewPlan; sample: boolean; busy: boolean; broker?: string;
  onAct: (label: string, fn: () => Promise<string>) => void;
  onDraft?: (draft: AiStrategyDraft) => void;
}) {
  const pick = plan.recommended;
  const symbol = String(plan.answers.symbol ?? "NIFTY 50");
  // P0.8 / A3: "Deploy in PAPER" goes through the server's candidate with the trader's risk acceptance.
  const [acceptRisk, setAcceptRisk] = useState(false);
  const perTrade = Math.round((plan.risk_config.capital * plan.risk_config.risk_per_trade_pct) / 100);
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-purple-500/40 bg-purple-500/10 p-3">
        {/* P0.9: the template the trader chose and the symbol - no market direction, no "your plan". */}
        <div className="flex flex-wrap items-center gap-3">
          <Sparkles size={18} className="text-purple-300" />
          <div className="text-base font-extrabold text-slate-50">Template{plan.option ? `: ${plan.option.label}` : ""} · {symbol}</div>
          {pick && <span className="rounded-full border border-sky-400/50 px-2 py-0.5 text-xs font-semibold text-sky-100">{pick.name}</span>}
        </div>
        {plan.warnings.length > 0 && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-amber-200">{plan.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {plan.sections.map((s) => {
          // P0.9: on sample candles the backtest figures are not shown as performance - blurred under a stamp.
          const hide = sample && s.id === "strategy";
          return (
            <div key={s.id} className="relative overflow-hidden rounded-lg border border-border bg-panel2/40 p-3">
              <div className="mb-1 text-xs font-bold uppercase tracking-wider text-purple-200">{s.title}<Mr>{s.title_mr}</Mr></div>
              <ul className={`space-y-1 text-sm text-slate-100 ${hide ? "select-none blur-sm" : ""}`} aria-hidden={hide}>
                {s.lines.map((l, i) => <li key={i} className="flex gap-1.5"><span className="text-purple-300">•</span><span>{l}</span></li>)}
              </ul>
              {hide && <SampleStamp />}
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => {
          // P0.8-D: the capital is the figure the trader entered (no allocation advice) - say so before it sizes every trade.
          const cap = `₹${Math.round(plan.risk_config.capital).toLocaleString("en-IN")}`;
          if (!window.confirm(`Replace your current risk settings with this template's? Trading capital will be ${cap} - the amount you entered. Every PAPER (and later LIVE) trade is sized from it.`)) return;
          onAct("Saving…", async () => { await api.updateRiskSettings(plan.risk_config); return "Risk settings applied."; });
        }} className="rounded bg-sky-600 px-3 py-1.5 text-left text-xs font-bold text-white hover:bg-sky-500 disabled:opacity-50">
          <ShieldCheck size={12} className="mr-1 inline" />Apply risk settings<Mr>{INTERVIEW_MR.applyRisk}</Mr>
        </button>
        {plan.deployment && plan.candidate_id != null && (
          <>
            <label className="flex items-center gap-1.5 text-[11px] text-slate-200">
              <input type="checkbox" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} />
              <span>I accept a maximum loss of about ₹{perTrade.toLocaleString("en-IN")} per trade<Mr>{INTERVIEW_MR.acceptLoss}</Mr></span>
            </label>
            <button disabled={busy || !acceptRisk} onClick={() => onAct("Deploying…", async () => {
              const d = await api.aiInterviewDeploy(plan.candidate_id!, acceptRisk);
              return `Deployment #${d.deployment.id} is running in PAPER. Watch it on the Autopilot page.`;
            })} className="rounded bg-emerald-600 px-3 py-1.5 text-left text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-50">
              <Rocket size={12} className="mr-1 inline" />Deploy in PAPER<Mr>{INTERVIEW_MR.deployPaper}</Mr>
            </button>
          </>
        )}
        <button onClick={() => window.open(chartWindowUrl(symbol, "5min", "NSE", broker), "_blank")} className="rounded border border-border px-3 py-1.5 text-left text-xs font-semibold text-slate-100 hover:bg-panel2">
          <CandlestickChart size={12} className="mr-1 inline" />Open the chart<Mr>{INTERVIEW_MR.openChart}</Mr>
        </button>
        {onDraft && (
          <button disabled={busy} onClick={() => onAct("Asking the AI…", async () => {
            const d = await api.aiGenerate(plan.ai_prompt, { language: "en", regime: plan.market.regime.kind, symbol });
            onDraft(d);
            return `AI draft #${d.id} is ready for review below.`;
          })} className="rounded border border-purple-500/50 px-3 py-1.5 text-left text-xs font-semibold text-purple-100 hover:bg-purple-500/20 disabled:opacity-50">
            <Sparkles size={12} className="mr-1 inline" />Ask the AI for a custom rule set<Mr>{INTERVIEW_MR.askAi}</Mr>
          </button>
        )}
      </div>
      <div className="text-[11px] text-muted">{plan.disclaimer}</div>
    </div>
  );
}
