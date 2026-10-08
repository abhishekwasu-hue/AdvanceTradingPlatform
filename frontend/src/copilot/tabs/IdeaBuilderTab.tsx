import { ArrowLeft, Bot, CandlestickChart, Check, Compass, History, Rocket, RotateCcw, ShieldCheck, Sparkles, ThumbsDown, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api } from "../../api/client";
import { chartWindowUrl } from "../../components/chartHelpers";
import { DataSourceBar, useCandleSource, type CandleSourceState } from "../../components/DataSource";
import { Badge, Button, fieldClass } from "../../components/primitives";
import type { FeedbackOption, InterviewPlan, InterviewQuestion, InterviewStart, OHLCVBar } from "../../types";
import { FNO_INDICES, FNO_STOCKS } from "../../utils/fnoSymbols";
import { friendlyError, useAiTask } from "../aiTask";
import AiProgress from "../components/AiProgress";
import BilingualQuestion, { Secondary } from "../components/BilingualQuestion";
import CostChip from "../components/CostChip";
import PrivacyNote from "../components/PrivacyNote";
import TiltCard from "../components/TiltCard";
import { useCostEstimate } from "../cost";
import { useCopilotT, useSecondary } from "../i18n";
import { copilotPath } from "../tabs";
import { Panel, Stagger, StaggerItem } from "./shared";

/**
 * Idea Builder: the strategy interview, step by step. Every question is English with a small muted line in the second
 * language (default Marathi; Settings can switch it off), a progress bar, then "templates you choose": three templates
 * side by side, each with its rules in words and its backtest - none highlighted, none pre-selected, no score, no
 * "recommended". Choosing one opens its risk settings and the PAPER button; nothing is applied until a button is pressed.
 */
const CAPITAL_CHIPS = [50_000, 100_000, 200_000, 500_000];

/** The "strategy" section of a plan (backend app/ai/interview.py) lists: the template, its rules in words, the backtest
 * line, the regime filter, then the other templates with their backtest figures. On SAMPLE candles only the backtest
 * figures are blurred under the stamp - the rules in words stay readable (white-box). */
export function isBacktestLine(index: number): boolean {
  return index === 2 || index >= 4;
}

function StrategyLines({ lines, sample, className }: { lines: string[]; sample: boolean; className?: string }) {
  return (
    <ul className={className}>
      {lines.map((l, i) => {
        const hide = sample && isBacktestLine(i);
        return <li key={i} className={hide ? "relative select-none" : undefined}><span className={hide ? "blur-sm" : undefined} aria-hidden={hide}>· {l}</span></li>;
      })}
    </ul>
  );
}
interface Msg { from: "ai" | "me"; text: string; second?: string }

export default function IdeaBuilderTab() {
  const t = useCopilotT();
  const { lang, line } = useSecondary();
  const sv = (mr?: string | null) => (lang === "mr" ? mr ?? null : null);      // the server sends the Marathi lines
  const source = useCandleSource(30);
  const location = useLocation();
  const navigate = useNavigate();
  const initialPrompt = (location.state as { prompt?: string } | null)?.prompt ?? "";

  const [startKey, setStartKey] = useState(initialPrompt ? 1 : 0);
  const [startPrompt, setStartPrompt] = useState(initialPrompt);
  const [start, setStart] = useState<InterviewStart | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [queue, setQueue] = useState<string[]>([]);
  const [step, setStep] = useState(0);
  const [log, setLog] = useState<Msg[]>([]);
  const [custom, setCustom] = useState("");
  const [plan, setPlan] = useState<InterviewPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [offerProfile, setOfferProfile] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const fetched = useRef<{ tf: string; candles: OHLCVBar[]; label: string } | null>(null);
  const task = useAiTask(t, 120_000);
  const estimate = useCostEstimate("strategist");

  // A prompt handed over from another tab (Strategy Lab, Ask Copilot) starts the interview once; then it is cleared.
  useEffect(() => { if (initialPrompt) navigate(".", { replace: true, state: null }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!startKey) return;
    let cancelled = false;
    setPlan(null); setError(null); setDone(null); setStep(0); setCustom(""); setSelected(null); setRejecting(null); setReasons([]);
    fetched.current = null;
    api.aiInterviewStart(startPrompt).then((s) => {
      if (cancelled) return;
      const prefill: Record<string, string> = { ...s.prefill, language: "en" };
      setStart(s); setAnswers(prefill); setQueue(s.questions.map((q) => q.id).filter((id) => !(id in prefill)));
      const known = Object.keys(s.prefill).filter((k) => k !== "language");
      const intro: Msg[] = [{ from: "ai", text: s.intro, second: s.intro_mr }];
      if (known.length) {
        const list = known.map((k) => `${k} = ${prefill[k]}`).join(", ");
        intro.push({ from: "ai", text: t("idea.understood", { list }), second: `${line("alreadyUnderstood")} ${list}` });
      }
      setLog(intro);
      setOfferProfile(!!s.profile);
    }).catch((e) => setError(friendlyError(e, t)));
    return () => { cancelled = true; };
  }, [startKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const begin = (prompt = "") => { setStartPrompt(prompt); setStartKey((k) => k + 1); };

  if (!startKey) {
    return (
      <div className="space-y-4" data-testid="tab-panel-idea-builder">
        <TiltCard className="flex flex-col items-start gap-3 md:flex-row md:items-center">
          <span className="rounded-xl bg-ai/15 p-2.5 text-ai"><Compass size={26} /></span>
          <div className="flex-1">
            <h2 className="text-base font-semibold text-fg">{t("idea.title")}</h2>
            <p className="text-sm text-fg-muted">{t("idea.startIntro")}<Secondary lang={lang}>{line("startIntro")}</Secondary></p>
          </div>
          <Button variant="primary" icon={<Compass size={15} />} onClick={() => begin("")} data-testid="idea-start">
            <span className="text-left leading-tight">{t("idea.start")}<Secondary lang={lang}>{line("start")}</Secondary></span>
          </Button>
        </TiltCard>
        <PrivacyNote />
      </div>
    );
  }
  if (!start) return error ? <p className="text-sm text-down" role="alert">{error}</p> : <div className="copilot-skeleton copilot-shimmer h-40" aria-busy="true" />;

  const byId = (id: string) => start.questions.find((q) => q.id === id);
  const current: InterviewQuestion | undefined = queue[step] ? byId(queue[step]) : undefined;
  const finished = step >= queue.length;
  const optLabel = (q: InterviewQuestion, v: string) => q.options.find((o) => o.value === v)?.en ?? v;
  const optSecond = (q: InterviewQuestion, v: string) => { const o = q.options.find((x) => x.value === v); return o?.mr && o.mr !== o.en ? o.mr : undefined; };
  const symbolChoices = answers.instrument === "index" || !answers.instrument ? FNO_INDICES : FNO_STOCKS.slice(0, 12);

  function answer(value: string, label?: string) {
    if (!current) return;
    const q = current;
    const next = { ...answers, [q.id]: value };
    if (q.id === "instrument" && value !== "index" && (answers.symbol ?? "NIFTY 50").startsWith("NIFTY")) delete next.symbol;
    setAnswers(next);
    setLog((prev) => [...prev, { from: "ai", text: q.en, second: q.mr }, { from: "me", text: label ?? optLabel(q, value), second: label ? undefined : optSecond(q, value) }]);
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
  function applyProfile() {
    if (!start?.profile) return;
    const saved = Object.fromEntries(Object.entries(start.profile.answers).map(([k, v]) => [k, String(v)]));
    setAnswers({ ...saved, ...start.prefill, language: "en" });
    setStep(queue.length);
    setOfferProfile(false);
    setLog((prev) => [...prev, { from: "me", text: t("idea.usedLast"), second: line("usedLastAnswers") }]);
  }
  async function forgetProfile() {
    try { await api.aiProfileDelete(); setOfferProfile(false); setDone(t("idea.forgotten")); } catch (e) { setError(friendlyError(e, t)); }
  }
  function answersBody(): Record<string, string | number> {
    const body: Record<string, string | number> = { ...answers };
    if (body.capital) body.capital = Number(String(body.capital).replace(/[^\d.]/g, ""));
    if (body.daily_loss) body.daily_loss = Number(body.daily_loss);
    return body;
  }
  async function candlesFor(style: string, src: CandleSourceState) {
    const symbol = (answers.symbol || "NIFTY 50").trim().toUpperCase();
    if (src.mode === "sample") {
      const swing = style === "swing";
      const r = await src.fetch([symbol], swing ? "day" : "1min", { count: swing ? 300 : 3000, startPriceFor: () => 24_000, seedFor: () => 11, daily: swing });
      return { tf: swing ? "day" : "1min", candles: r.candles[symbol] ?? [], label: "sample" };
    }
    const tf = style === "swing" ? "day" : style === "scalping" ? "1min" : "5min";
    const r = await api.marketDataCandles([symbol], tf, tf === "day" ? 400 : tf === "1min" ? 5 : 20, "NSE", src.broker || undefined);
    const entry = r.symbols[symbol];
    if (!entry || entry.error || !entry.bars.length) throw new Error(entry?.error ?? t("lab.drafts.noCandles", { symbol }));
    return { tf, candles: entry.bars, label: `broker:${r.source.broker}` };
  }
  async function buildPlan() {
    setError(null); setDone(null);
    const out = await task.run([t("idea.step.candles"), t("idea.step.read"), t("idea.step.test")], async (signal, advance) => {
      const data = await candlesFor(answers.style || "intraday", source);
      fetched.current = { ...data, candles: data.candles.slice(-3000) };
      advance(1);
      return api.aiInterviewPlan(answersBody(), data.tf, fetched.current.candles, data.label, signal);
    });
    if (out) { setPlan(out); setSelected(null); }
  }
  async function refine(option: InterviewPlan) {
    const f = fetched.current;
    if (!f || !option.option || reasons.length === 0) return;
    setError(null); setDone(null);
    const out = await task.run([t("idea.step.refine"), t("idea.step.test")], (signal) =>
      api.aiInterviewRefine(answersBody(), f.tf, f.candles, f.label, reasons, option.option!.id, option.recommended?.strategy_id ?? null, signal));
    if (!out) return;
    setAnswers(Object.fromEntries(Object.entries(out.answers).map(([k, v]) => [k, String(v)])));
    setPlan(out); setSelected(null); setRejecting(null); setReasons([]);
  }
  async function choose(option: InterviewPlan) {
    if (!option.option) return;
    setSelected(option.option.id);
    try { await api.aiInterviewChoose(answersBody(), option.option.id, option.recommended?.strategy_id ?? null); } catch { /* remembering is best effort */ }
    setDone(t("idea.chosen", { label: option.option.label }));
  }

  const progress = queue.length ? Math.min(1, step / queue.length) : 1;
  const sample = fetched.current?.label === "sample";

  return (
    <div className="space-y-4" data-testid="tab-panel-idea-builder">
      <DataSourceBar source={source} />
      <TiltCard tilt={false} className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-base font-semibold text-fg">{t("idea.title")}</h2>
          <span className="text-xs text-fg-muted" data-testid="idea-progress-label">
            {finished ? t("idea.allAnswered") : t("idea.questionOf", { n: step + 1, total: queue.length })}
            <Secondary lang={lang} inline>{finished ? null : `${line("question")} ${step + 1} / ${queue.length}`}</Secondary>
          </span>
          <Button size="sm" variant="ghost" className="ml-auto" icon={<RotateCcw size={13} />} onClick={() => begin("")}>
            <span className="text-left leading-tight">{t("idea.startOver")}<Secondary lang={lang}>{line("startOver")}</Secondary></span>
          </Button>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label={t("idea.progress")} aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-1.5 rounded-full bg-gradient-to-r from-ai to-ai-2 transition-all" style={{ width: `${progress * 100}%` }} />
        </div>

        <div className="max-h-72 space-y-2 overflow-y-auto rounded-xl border border-border/70 bg-surface-2/40 p-3" aria-live="polite">
          {log.map((m, i) => (
            <div key={i} className={`flex gap-2 ${m.from === "me" ? "justify-end" : ""}`}>
              {m.from === "ai" && <Bot size={16} className="mt-0.5 shrink-0 text-ai" aria-hidden />}
              <div className={`max-w-[85%] rounded-xl px-3 py-1.5 text-sm ${m.from === "ai" ? "bg-surface-3 text-fg" : "bg-ai/15 text-fg"}`}>{m.text}<Secondary lang={lang}>{sv(m.second)}</Secondary></div>
              {m.from === "me" && <User size={16} className="mt-0.5 shrink-0 text-ai-2" aria-hidden />}
            </div>
          ))}
        </div>

        {offerProfile && start.profile && step === 0 && (
          <div className="rounded-xl border border-ai/30 bg-ai/5 p-3 text-sm text-fg">
            <History size={15} className="mr-1 inline text-ai" />
            {t("idea.welcomeBack", { capital: Number(start.profile.answers.capital ?? 0).toLocaleString("en-IN"), style: String(start.profile.answers.style ?? ""), symbol: String(start.profile.answers.symbol ?? "") })}
            <Secondary lang={lang}>{line("welcomeBack")}</Secondary>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" onClick={applyProfile}><span className="text-left leading-tight">{t("idea.yesUse")}<Secondary lang={lang}>{line("yesUse")}</Secondary></span></Button>
              <Button size="sm" onClick={() => setOfferProfile(false)}><span className="text-left leading-tight">{t("idea.noAsk")}<Secondary lang={lang}>{line("noAsk")}</Secondary></span></Button>
              <Button size="sm" variant="ghost" onClick={() => void forgetProfile()}><span className="text-left leading-tight">{t("idea.forget")}<Secondary lang={lang}>{line("forget")}</Secondary></span></Button>
            </div>
          </div>
        )}

        {current && !offerProfile && (
          <div className="rounded-xl border border-border bg-surface-1/60 p-4" data-testid="idea-question">
            <BilingualQuestion en={current.en} secondary={sv(current.mr)} lang={lang} hint={current.why_en} hintSecondary={sv(current.why_mr)}>
              {current.kind === "choice" && (
                <div className="flex flex-wrap gap-1.5">
                  {current.options.map((o) => (
                    <button key={o.value} onClick={() => answer(o.value)} className="rounded-full border border-border px-3 py-1.5 text-sm font-medium text-fg hover:border-ai/60 hover:bg-ai/10">
                      {o.en}{o.mr && o.mr !== o.en && <Secondary lang={lang} inline>{sv(o.mr)}</Secondary>}
                    </button>
                  ))}
                </div>
              )}
              {current.kind === "number" && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {CAPITAL_CHIPS.map((c) => (
                    <button key={c} onClick={() => answer(String(c), `₹${c.toLocaleString("en-IN")}`)} className="rounded-full border border-border px-3 py-1.5 text-sm font-medium text-fg hover:border-ai/60 hover:bg-ai/10">₹{c.toLocaleString("en-IN")}</button>
                  ))}
                  <input value={custom} onChange={(e) => setCustom(e.target.value.replace(/[^\d]/g, ""))} aria-label={t("idea.otherAmount")}
                         placeholder={lang ? `${t("idea.otherAmount")} / ${line("otherAmount")}` : t("idea.otherAmount")} className={`${fieldClass} w-40`} />
                  <Button size="sm" variant="primary" disabled={Number(custom) < 5000} onClick={() => answer(custom, `₹${Number(custom).toLocaleString("en-IN")}`)}>{t("idea.ok")}</Button>
                </div>
              )}
              {current.kind === "symbol" && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {symbolChoices.map((s) => <button key={s} onClick={() => answer(s)} className="rounded-full border border-border px-3 py-1.5 text-sm font-medium text-fg hover:border-ai/60 hover:bg-ai/10">{s}</button>)}
                  <input value={custom} list="idea-symbols" onChange={(e) => setCustom(e.target.value.toUpperCase())} aria-label={t("idea.typeSymbol")}
                         placeholder={lang ? `${t("idea.typeSymbol")} / ${line("typeSymbol")}` : t("idea.typeSymbol")} className={`${fieldClass} w-40`} />
                  <datalist id="idea-symbols">{[...FNO_INDICES, ...FNO_STOCKS].map((s) => <option key={s} value={s} />)}</datalist>
                  <Button size="sm" variant="primary" disabled={!custom.trim()} onClick={() => answer(custom.trim())}>{t("idea.ok")}</Button>
                </div>
              )}
            </BilingualQuestion>
          </div>
        )}

        {finished && !plan && (
          <div className="rounded-xl border border-ai/30 bg-ai/5 p-4 text-sm text-fg">
            {t("idea.thanks")}<Secondary lang={lang}>{line("thanks")}</Secondary>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button variant="primary" icon={<Compass size={14} />} disabled={task.busy} onClick={() => void buildPlan()} data-testid="idea-read-market">
                <span className="text-left leading-tight">{t("idea.readMarket")}<Secondary lang={lang}>{line("readMarket")}</Secondary></span>
              </Button>
              {estimate && <CostChip tokens={estimate.tokens} inr={estimate.inr} estimate />}
              <span className="text-xs text-fg-muted">{source.mode === "sample" ? t("idea.onSample") : t("idea.onBroker")}<Secondary lang={lang}>{source.mode === "sample" ? line("onSample") : line("onBroker")}</Secondary></span>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 text-xs">
          {step > 0 && <Button size="sm" variant="ghost" icon={<ArrowLeft size={13} />} onClick={back}><span className="text-left leading-tight">{t("idea.back")}<Secondary lang={lang}>{line("back")}</Secondary></span></Button>}
          {error && <span className="text-down" role="alert">{error}</span>}
          {done && <span className="text-up" role="status">{done}</span>}
        </div>
        <AiProgress state={task.state} onCancel={task.cancel} />
      </TiltCard>

      {plan?.options && (
        <TemplateGrid plan={plan} sample={sample} selected={selected} busy={task.busy} rejecting={rejecting} reasons={reasons} lang={lang} line={line} sv={sv}
                      onChoose={(o) => void choose(o)} onReject={(id) => { setRejecting(rejecting === id ? null : id); setReasons([]); }}
                      onToggleReason={(code) => setReasons((r) => (r.includes(code) ? r.filter((c) => c !== code) : [...r, code]))} onRefine={(o) => void refine(o)} />
      )}
      {plan && (() => {
        const shown = plan.options ? plan.options.find((o) => o.option?.id === selected) : plan;
        if (!shown) return <p className="text-sm text-fg-muted">{t("idea.openTemplate")}<Secondary lang={lang}>{line("openTemplate")}</Secondary></p>;
        return <ChosenTemplate plan={shown} sample={sample} broker={source.mode === "broker" ? source.broker : undefined} lang={lang} line={line} sv={sv}
                               onDone={setDone} onError={(e) => setError(friendlyError(e, t))} onAskAi={(prompt) => navigate(copilotPath("strategy-lab"), { state: { prompt } })} />;
      })()}
    </div>
  );
}

type Line = ReturnType<typeof useSecondary>["line"];

function TemplateGrid({ plan, sample, selected, busy, rejecting, reasons, lang, line, sv, onChoose, onReject, onToggleReason, onRefine }: {
  plan: InterviewPlan; sample: boolean; selected: string | null; busy: boolean; rejecting: string | null; reasons: string[];
  lang: string | null; line: Line; sv: (mr?: string | null) => string | null;
  onChoose: (o: InterviewPlan) => void; onReject: (id: string) => void; onToggleReason: (code: string) => void; onRefine: (o: InterviewPlan) => void;
}) {
  const t = useCopilotT();
  const feedback: FeedbackOption[] = plan.feedback_options ?? [];
  return (
    <Panel title={<span>{t("idea.templatesTitle")}<Secondary lang={lang}>{line("chooseTemplate")}</Secondary></span>} icon={<Sparkles size={15} />} testId="idea-templates">
      <p className="mb-3 text-xs text-fg-muted">{t("idea.templatesHint")}<Secondary lang={lang}>{line("chooseTemplateHint")}</Secondary></p>
      {plan.changes && plan.changes.length > 0 && (
        <div className="mb-3 rounded-xl border border-ai/30 bg-ai/5 p-2 text-xs text-fg">
          <b>{t("idea.changed")}</b><Secondary lang={lang}>{line("changedFromFeedback")}</Secondary>
          <ul className="mt-1 list-disc pl-5">{plan.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      <Stagger className="grid gap-3 md:grid-cols-3">
        {(plan.options ?? []).map((o) => {
          const meta = o.option!;
          const isSel = selected === meta.id;
          const rules = o.sections.find((s) => s.id === "strategy");
          return (
            <StaggerItem key={meta.id}>
              <TiltCard as="article" className={`flex h-full flex-col gap-2 ${isSel ? "ring-2 ring-ai" : ""}`} data-testid="strategy-card">
                <h3 className="text-base font-semibold text-fg">{meta.label}<Secondary lang={lang}>{sv(meta.label_mr)}</Secondary></h3>
                <p className="text-xs text-fg-muted">{meta.summary}<Secondary lang={lang}>{sv(meta.summary_mr)}</Secondary></p>
                {meta.headline && (
                  <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-xs">
                    <dt className="text-fg-muted">{t("idea.h.risk")}</dt><dd className="text-right font-tabular text-fg">{meta.headline.risk_pct}%</dd>
                    <dt className="text-fg-muted">{t("idea.h.trades")}</dt><dd className="text-right font-tabular text-fg">{meta.headline.trades_per_day}</dd>
                    <dt className="text-fg-muted">{t("idea.h.rr")}</dt><dd className="text-right font-tabular text-fg">1:{meta.headline.min_rr}</dd>
                  </dl>
                )}
                {/* Rules in words and the backtest, the same for every template. */}
                {rules && (
                  <div className="relative rounded-xl border border-ai/25 bg-ai/5 p-2.5" data-testid="rules-in-words">
                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ai">{t("idea.rulesAndBacktest")}</div>
                    <StrategyLines lines={rules.lines} sample={sample} className="space-y-1 text-xs text-fg" />
                    {sample && <p className="mt-1 text-[11px] font-semibold text-warn">{t("idea.sampleBlur")}</p>}
                  </div>
                )}
                {meta.regime_filter_open !== null && (
                  <p className="text-[11px] text-fg-muted">{meta.regime_filter_open ? t("idea.filterOpen") : t("idea.filterClosed")}</p>
                )}
                <div className="mt-auto flex flex-wrap gap-2 pt-1">
                  <Button size="sm" variant={isSel ? "primary" : "secondary"} icon={<Check size={13} />} disabled={busy} onClick={() => onChoose(o)} aria-pressed={isSel}>
                    <span className="text-left leading-tight">{isSel ? t("idea.chosenBtn") : t("idea.choose")}<Secondary lang={lang}>{isSel ? line("chosen") : line("chooseThis")}</Secondary></span>
                  </Button>
                  <Button size="sm" variant="ghost" icon={<ThumbsDown size={13} />} disabled={busy} onClick={() => onReject(meta.id)}>
                    <span className="text-left leading-tight">{t("idea.notThis")}<Secondary lang={lang}>{line("notThis")}</Secondary></span>
                  </Button>
                </div>
                {rejecting === meta.id && (
                  <div className="space-y-1.5 rounded-xl border border-border bg-surface-2/60 p-2">
                    <div className="text-xs font-semibold text-fg">{t("idea.whyNot")}<Secondary lang={lang}>{line("whyNot")}</Secondary></div>
                    <div className="flex flex-wrap gap-1">
                      {feedback.map((f) => (
                        <button key={f.code} onClick={() => onToggleReason(f.code)} aria-pressed={reasons.includes(f.code)}
                                className={`rounded-full border px-2 py-0.5 text-[11px] ${reasons.includes(f.code) ? "border-ai bg-ai/15 text-fg" : "border-border text-fg-muted"}`}>
                          {f.en}{f.mr && f.mr !== f.en && <Secondary lang={lang} inline>{sv(f.mr)}</Secondary>}
                        </button>
                      ))}
                    </div>
                    <Button size="sm" disabled={busy || reasons.length === 0} onClick={() => onRefine(o)}>
                      <span className="text-left leading-tight">{t("idea.showOther")}<Secondary lang={lang}>{line("showOther")}</Secondary></span>
                    </Button>
                  </div>
                )}
              </TiltCard>
            </StaggerItem>
          );
        })}
      </Stagger>
    </Panel>
  );
}

function ChosenTemplate({ plan, sample, broker, lang, line, sv, onDone, onError, onAskAi }: {
  plan: InterviewPlan; sample: boolean; broker?: string; lang: string | null; line: Line; sv: (mr?: string | null) => string | null;
  onDone: (m: string) => void; onError: (e: unknown) => void; onAskAi: (prompt: string) => void;
}) {
  const t = useCopilotT();
  const symbol = String(plan.answers.symbol ?? "NIFTY 50");
  const [acceptRisk, setAcceptRisk] = useState(false);
  const [busy, setBusy] = useState(false);
  const perTrade = Math.round((plan.risk_config.capital * plan.risk_config.risk_per_trade_pct) / 100);
  const act = async (fn: () => Promise<string>) => { setBusy(true); try { onDone(await fn()); } catch (e) { onError(e); } finally { setBusy(false); } };
  return (
    <Panel title={`${t("idea.template")}${plan.option ? `: ${plan.option.label}` : ""} · ${symbol}`} icon={<Sparkles size={15} />} testId="idea-chosen">
      {plan.warnings.length > 0 && <ul className="mb-3 list-disc space-y-0.5 pl-5 text-xs text-warn">{plan.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      <div className="grid gap-3 md:grid-cols-2">
        {plan.sections.map((s) => {
          const strategy = s.id === "strategy";
          return (
            <div key={s.id} className="relative overflow-hidden rounded-xl border border-border bg-surface-2/40 p-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ai">{s.title}<Secondary lang={lang}>{sv(s.title_mr)}</Secondary></div>
              {strategy ? <StrategyLines lines={s.lines} sample={sample} className="space-y-1 text-sm text-fg" />
                : <ul className="space-y-1 text-sm text-fg">{s.lines.map((l, i) => <li key={i}>· {l}</li>)}</ul>}
              {strategy && sample && <p className="mt-1 text-[11px] font-semibold text-warn">{t("idea.sampleBlur")}</p>}
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" icon={<ShieldCheck size={13} />} disabled={busy} onClick={() => {
          const cap = `₹${Math.round(plan.risk_config.capital).toLocaleString("en-IN")}`;
          if (!window.confirm(t("idea.confirmRisk", { capital: cap }))) return;
          void act(async () => { await api.updateRiskSettings(plan.risk_config); return t("idea.riskApplied"); });
        }}><span className="text-left leading-tight">{t("idea.applyRisk")}<Secondary lang={lang}>{line("applyRisk")}</Secondary></span></Button>
        {plan.deployment && plan.candidate_id != null && (
          <>
            <label className="flex items-center gap-1.5 text-xs text-fg">
              <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--ai))]" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} />
              <span>{t("idea.acceptLoss", { amount: perTrade.toLocaleString("en-IN") })}<Secondary lang={lang}>{line("acceptLoss")}</Secondary></span>
            </label>
            <Button size="sm" variant="primary" icon={<Rocket size={13} />} disabled={busy || !acceptRisk}
                    onClick={() => void act(async () => t("idea.deployed", { id: (await api.aiInterviewDeploy(plan.candidate_id!, acceptRisk)).deployment.id }))}>
              <span className="text-left leading-tight">{t("idea.deployPaper")}<Secondary lang={lang}>{line("deployPaper")}</Secondary></span>
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" icon={<CandlestickChart size={13} />} onClick={() => window.open(chartWindowUrl(symbol, "5min", "NSE", broker), "_blank")}>
          <span className="text-left leading-tight">{t("idea.openChart")}<Secondary lang={lang}>{line("openChart")}</Secondary></span>
        </Button>
        <Button size="sm" variant="ghost" icon={<Sparkles size={13} />} onClick={() => onAskAi(plan.ai_prompt)}>
          <span className="text-left leading-tight">{t("idea.askAi")}<Secondary lang={lang}>{line("askAi")}</Secondary></span>
        </Button>
      </div>
      <Badge tone="neutral" className="mt-3">{t("idea.paperFirst")}</Badge>
      <p className="mt-2 text-[11px] text-fg-muted">{plan.disclaimer}</p>
    </Panel>
  );
}
