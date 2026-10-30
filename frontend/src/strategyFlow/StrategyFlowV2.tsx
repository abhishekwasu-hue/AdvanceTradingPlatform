import { ChevronLeft, Lock } from "lucide-react";
import { lazy, Suspense, useEffect, useId, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import { errorSentence } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { useCandleSource } from "../components/DataSource";
import type { StrategyDraft } from "../copilot/strategyDraft";
import { CardV2 } from "../dashboard/CardV2";
import { directionMarker, type ChartMarker } from "../charting/markers";
import type { PageProps } from "../routes";
import { templatesApi, type StrategyTemplate } from "../strategyTemplates/api";
import { paperBotPathFor } from "../strategyTemplates/link";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2, PageTitleV2 } from "../trading/PartsV2";
import type { BacktestResult, ConditionOperator, CustomStrategyResponse, IndicatorName, OHLCVBar } from "../types";
import { Chip, FilterChip } from "../ui/Chip";
import { AdvancedFold } from "../ui/AdvancedFold";
import { Combobox } from "../ui/Combobox";
import { PageFootnote, Signed } from "../ui/numbersFirst";
import { OptionCards } from "../ui/OptionCards";
import { Sparkline } from "../ui/Sparkline";
import { SubTabs } from "../ui/SubTabs";
import { formatINR, formatNumber } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import {
  BLOCK_PRESETS, EXPIRIES, FLOW_TIMEFRAMES, INDICATORS, INDICATOR_LABEL, NEEDS_MULTIPLIER, NEEDS_PERIOD, OPERATORS, OPERATOR_WORDS, SOON_BLOCKS,
  START_CARDS, STEPS, STRIKES, STRUCTURES, SYMBOL_CHOICES, SYMBOL_LABEL, TIMEFRAME_WORDS, blockOpen, blockWords, emptyFlow, entryProblems, higherTimeframes, atrLengthMissing,
  exitProblems, flowConfig, isOption, isSpread, marketProblems, optionBacktest, paperContract, paperSymbol, resultSummary, sidesOf, withCard,
  withDraft, type FlowBlock, type FlowHandoff, type FlowOperand, type FlowSide, type FlowState, type StepKey,
} from "./model";

// The full builder of before, kept whole under Advanced (owner rule: nothing is deleted).
const FullBuilder = lazy(() => import("../pages/StrategyBuilderPage").then((m) => ({ default: m.StrategyBuilderFull })));
const BacktestChartV2 = lazy(() => import("../chart/BacktestChartV2"));

const PAPER_PATH = "/deployments";

/**
 * SB-UX SU1-SU6 (owner order 30 Oct, behind `ui_v2`): Strategies › Build as ONE stepped flow - Start (a template card or
 * your own words, read by the Copilot's one parser), Market, Entry (readable blocks), Exit & risk (your own values, blank
 * = off; the option structure, strikes and expiry), Result (the numbers first, trades / chart / analytics behind tabs),
 * then Start on paper: the Trading bot form with this strategy picked. Nothing starts until he presses Start on paper there.
 * The builder of before stays under Advanced (`?advanced=1`).
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- the shell passes PageProps to every page
export default function StrategyFlowV2(_props: PageProps) {
  const { user, loading } = useAuth();
  const [params, setParams] = useSearchParams();
  const advanced = params.get("advanced") === "1";
  const setAdvanced = (on: boolean) => setParams(on ? { advanced: "1" } : {}, { replace: false });

  if (loading) return null;
  if (!user) {
    return (
      <div className="space-y-space-4">
        <PageTitleV2 title="Build a strategy" subtitle="Your own strategy in five short steps." />
        <NoteV2>Sign in to build and save your own strategies.</NoteV2>
      </div>
    );
  }
  if (advanced) {
    return (
      <div className="space-y-space-4">
        <button type="button" className={BUTTON_SECONDARY} onClick={() => setAdvanced(false)} data-flow-back>
          <ChevronLeft size={16} aria-hidden /> Back to the step-by-step builder
        </button>
        <Suspense fallback={null}><FullBuilder onOpenFlow={() => setAdvanced(false)} /></Suspense>
      </div>
    );
  }
  return <Flow onAdvanced={() => setAdvanced(true)} />;
}

function Flow({ onAdvanced }: { onAdvanced: () => void }) {
  const features = useFeatures();
  const location = useLocation();
  const navigate = useNavigate();
  const handedIdea = (location.state as { idea?: string } | null)?.idea ?? "";
  const [flow, setFlow] = useState<FlowState>(emptyFlow);
  const [step, setStep] = useState<StepKey>("start");
  const [touched, setTouched] = useState(false);
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  const templatesOn = features.on("strategy_templates");

  useEffect(() => {
    if (!templatesOn) return;
    templatesApi.list().then((r) => setTemplates(r.templates.filter((t) => t.available))).catch(() => setTemplates([]));
  }, [templatesOn]);
  useEffect(() => { if (handedIdea) navigate(".", { replace: true, state: null }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const template = flow.templateId ? templates.find((t) => t.id === flow.templateId) ?? null : null;
  const index = STEPS.findIndex((s) => s.key === step);
  const problems = step === "start" ? (flow.card ? [] : ["Pick a card, or describe your idea in words."])
    : step === "market" ? marketProblems(flow) : step === "entry" ? entryProblems(flow)
    : step === "exit" ? exitProblems(flow.exits, flow.options, flow.engine) : [];
  const go = (to: StepKey) => { setTouched(false); setStep(to); window.scrollTo?.({ top: 0 }); };
  const next = () => {
    setTouched(true);
    if (problems.length === 0 && index < STEPS.length - 1) go(STEPS[index + 1].key);
  };

  return (
    <div className="space-y-space-4" data-strategy-flow>
      <PageTitleV2 title="Build a strategy" subtitle="Five short steps: start, market, entry, exits, result. Then start it on paper." />
      <Stepper step={step} onPick={(k) => { if (STEPS.findIndex((s) => s.key === k) < index) go(k); }} />

      {step === "start" && <StartStep flow={flow} setFlow={setFlow} templates={templates} templatesOn={templatesOn} idea={handedIdea} />}
      {step === "market" && <MarketStep flow={flow} setFlow={setFlow} template={template} />}
      {step === "entry" && <EntryStep flow={flow} setFlow={setFlow} template={template} />}
      {step === "exit" && <ExitStep flow={flow} setFlow={setFlow} />}
      {step === "result" && <ResultStep flow={flow} template={template} />}

      {step !== "result" && (
        <div className="space-y-space-2">
          {touched && problems.length > 0 && (
            <ul role="status" data-step-problems className="space-y-space-1 text-t13 text-warn">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
          )}
          <div className="flex flex-wrap items-center gap-space-2">
            <button type="button" className={BUTTON_PRIMARY} onClick={next} data-flow-next>Next: {STEPS[index + 1].label}</button>
            {index > 0 && <button type="button" className={BUTTON_SECONDARY} onClick={() => go(STEPS[index - 1].key)}>Back</button>}
          </div>
        </div>
      )}
      {step === "result" && <button type="button" className={BUTTON_SECONDARY} onClick={() => go("exit")}>Back to exits</button>}

      <div className="flex flex-wrap items-center gap-space-2 border-t border-subtle pt-space-3 text-t13 text-fg-muted">
        <span>Advanced:</span>
        <button type="button" className="inline-flex min-h-11 items-center text-left font-medium text-brand hover:underline md:min-h-0" onClick={onAdvanced} data-flow-advanced>
          the full builder (every indicator, higher timeframes)
        </button>
      </div>
      <PageFootnote>Backtests use past prices; past results do not promise future ones. Paper money only.</PageFootnote>
    </div>
  );
}

function Stepper({ step, onPick }: { step: StepKey; onPick: (k: StepKey) => void }) {
  const at = STEPS.findIndex((s) => s.key === step);
  return (
    <nav aria-label="Steps" data-stepper>
      <p className="text-t13 font-medium text-fg-muted md:hidden">Step {at + 1} of {STEPS.length}: <span className="text-fg">{STEPS[at].label}</span></p>
      <ol className="mt-space-1 flex gap-space-1">
        {STEPS.map((s, i) => (
          <li key={s.key} className="min-w-0 flex-1">
            <button type="button" onClick={() => onPick(s.key)} disabled={i >= at} aria-current={i === at ? "step" : undefined}
              className="flex min-h-11 w-full flex-col items-start gap-space-1 text-left disabled:cursor-default md:min-h-0">
              <span className={`h-1.5 w-full rounded-pill ${i <= at ? "bg-brand" : "bg-surface-3"}`} aria-hidden />
              <span className={`hidden truncate text-t12 md:block ${i === at ? "font-semibold text-fg" : "text-fg-muted"}`}>{i + 1}. {s.label}</span>
              <span className="sr-only md:hidden">{i + 1}. {s.label}</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

type SetFlow = (f: FlowState | ((f: FlowState) => FlowState)) => void;

// --- Step 1 ---------------------------------------------------------------------------------------------------------

function StartStep({ flow, setFlow, templates, templatesOn, idea }: {
  flow: FlowState; setFlow: SetFlow; templates: StrategyTemplate[]; templatesOn: boolean; idea: string;
}) {
  const boxId = useId();
  const [words, setWords] = useState(idea);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<StrategyDraft | null>(null);

  async function read() {
    setBusy(true); setError(null);
    try {
      const d = await api.draftStrategy(words.trim());
      setDraft(d);
      if (d.rules.length) setFlow((f) => withDraft(f, d));
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <CardV2 title="Pick a starting point" testId="flow-cards">
        <ul className="grid grid-cols-1 gap-space-2 sm:grid-cols-2 lg:grid-cols-3">
          {START_CARDS.map((c) => {
            const soon = c.engine === "soon" || (c.engine === "template" && !(templatesOn && templates.some((t) => t.id === c.templateId)));
            const on = flow.card === c.key;
            return (
              <li key={c.key}>
                <button type="button" disabled={soon} aria-pressed={on} data-start-card={c.key} onClick={() => setFlow((f) => withCard(f, c))}
                  className={`flex min-h-16 w-full flex-col items-start gap-space-1 rounded-control border px-space-3 py-space-2 text-left disabled:cursor-not-allowed disabled:opacity-60 ${on ? "border-brand bg-brand/10" : "border-border hover:bg-surface-2"}`}>
                  <span className="flex items-center gap-space-2 text-t14 font-semibold text-fg">
                    {c.title}{soon && <Chip tone="neutral"><Lock size={12} aria-hidden /> Coming soon</Chip>}
                  </span>
                  <span className="text-t13 text-fg-muted">{c.line}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardV2>

      <CardV2 title="Or describe it in your own words" testId="flow-words">
        <label htmlFor={boxId} className={LABEL}>Your idea</label>
        <textarea id={boxId} rows={3} value={words} onChange={(e) => setWords(e.target.value)} className={`${FIELD} h-auto py-space-2 md:h-auto`}
          placeholder="On the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA and RSI 14 is above 50." />
        <button type="button" className={`${BUTTON_SECONDARY} mt-space-2`} disabled={busy || !words.trim()} onClick={() => void read()} data-read-words>
          {busy ? "Reading…" : "Read my words"}
        </button>
        {error && <div className="mt-space-2"><NoteV2 kind="warn">{error}</NoteV2></div>}
        {draft && (
          <div className="mt-space-3 space-y-space-2 text-t13" data-draft-read>
            {draft.rules.length > 0 && (
              <div>
                <p className="font-medium text-fg">Understood ({draft.rules.length}):</p>
                <ul className="mt-space-1 flex flex-wrap gap-space-1">{draft.rules.map((r, i) => <li key={i}><Chip tone="up">{r.text}</Chip></li>)}</ul>
              </div>
            )}
            {draft.unsupported.length > 0 && (
              <div>
                <p className="font-medium text-fg">Not in the builder yet (left out):</p>
                <ul className="mt-space-1 list-disc pl-space-4 text-fg-muted">{draft.unsupported.map((u) => <li key={u}>{u}</li>)}</ul>
              </div>
            )}
            {draft.unread.length > 0 && <p className="text-fg-muted">Not understood: {draft.unread.join("; ")}</p>}
            {draft.rules.length === 0 && <p className="text-fg-muted">Nothing could be built from these words yet: pick a card above instead.</p>}
          </div>
        )}
      </CardV2>
    </>
  );
}

// --- Step 2 ---------------------------------------------------------------------------------------------------------

function MarketStep({ flow, setFlow, template }: { flow: FlowState; setFlow: SetFlow; template: StrategyTemplate | null }) {
  const ids = { name: useId(), other: useId() };
  const known = (SYMBOL_CHOICES as readonly string[]).includes(flow.symbol);
  const [other, setOther] = useState(!known && flow.symbol !== "");
  const timeframes = template ? template.timeframes.map((t) => ({ value: t.value, label: t.label, soon: false })) : FLOW_TIMEFRAMES;
  return (
    <CardV2 title="Market" testId="flow-market">
      <div className="space-y-space-4">
        <div>
          <span className={LABEL}>Symbol</span>
          <div className="flex flex-wrap gap-space-2">
            {SYMBOL_CHOICES.map((s) => (
              <FilterChip key={s} selected={!other && flow.symbol === s} onClick={() => { setOther(false); setFlow((f) => ({ ...f, symbol: s })); }}>{SYMBOL_LABEL[s]}</FilterChip>
            ))}
            <FilterChip selected={other} onClick={() => { setOther(true); setFlow((f) => ({ ...f, symbol: known ? "" : f.symbol })); }}>Other</FilterChip>
          </div>
          {other && (
            <div className="mt-space-2 max-w-xs">
              <label htmlFor={ids.other} className={LABEL}>Symbol name</label>
              <input id={ids.other} className={FIELD} value={flow.symbol} placeholder="RELIANCE"
                onChange={(e) => setFlow((f) => ({ ...f, symbol: e.target.value.toUpperCase().replace(/[^A-Z0-9&\- ]/g, "") }))} />
            </div>
          )}
        </div>
        <div>
          <span className={LABEL}>Timeframe</span>
          <div className="flex flex-wrap gap-space-2" data-flow-timeframes>
            {timeframes.map((t) => (
              <FilterChip key={t.value} selected={flow.timeframe === t.value} disabled={t.soon} onClick={() => setFlow((f) => ({ ...f, timeframe: t.value }))}>
                {t.label}{t.soon ? " (coming soon)" : ""}
              </FilterChip>
            ))}
          </div>
        </div>
        <OptionCards<FlowSide> name="flow-side" legend="Side" value={flow.side ?? ("" as FlowSide)} dataAttr="data-flow-side"
          onChange={(side) => setFlow((f) => ({ ...f, side }))}
          options={[{ key: "long", label: "Long", hint: "Rising prices" }, { key: "short", label: "Short", hint: "Falling prices" }, { key: "both", label: "Both", hint: "Either way" }]} />
        <div className="max-w-md">
          <label htmlFor={ids.name} className={LABEL}>Name</label>
          <input id={ids.name} className={FIELD} value={flow.name} maxLength={80} onChange={(e) => setFlow((f) => ({ ...f, name: e.target.value }))} />
        </div>
      </div>
    </CardV2>
  );
}

// --- Step 3 ---------------------------------------------------------------------------------------------------------

function EntryStep({ flow, setFlow, template }: { flow: FlowState; setFlow: SetFlow; template: StrategyTemplate | null }) {
  const [editing, setEditing] = useState<number | null>(null);
  if (flow.engine === "template") {
    return (
      <CardV2 title="Entry" testId="flow-entry">
        <p className="text-t13 text-fg-muted">This template brings its own entry, with its usual settings:</p>
        <ul className="mt-space-2 flex flex-wrap gap-space-1">
          {(template?.rules.length ? template.rules : [template?.idea ?? ""]).filter(Boolean).map((r) => <li key={r}><Chip tone="neutral">{r}</Chip></li>)}
        </ul>
      </CardV2>
    );
  }
  const update = (i: number, b: FlowBlock | null) =>
    setFlow((f) => ({ ...f, blocks: b ? f.blocks.map((x, j) => (j === i ? b : x)) : f.blocks.filter((_, j) => j !== i) }));
  return (
    <>
      {sidesOf(flow.side).map((side) => (
        <CardV2 key={side} title={side === "long" ? "Long entry: all of these" : "Short entry: all of these"} testId={`flow-entry-${side}`}>
          <ul className="space-y-space-2">
            {flow.blocks.map((b, i) => b.side !== side ? null : (
              <li key={i}>
                {editing === i
                  ? <BlockEditor block={b} baseTimeframe={flow.timeframe} onDone={(nb) => { update(i, nb); setEditing(null); }} onRemove={() => { update(i, null); setEditing(null); }} />
                  : (
                    <button type="button" data-block-chip={blockOpen(b) ? "open" : "set"} onClick={() => setEditing(i)}
                      className={`flex min-h-11 w-full items-center justify-between gap-space-2 rounded-control border px-space-3 py-space-2 text-left text-t14 ${blockOpen(b) ? "border-warn" : "border-border"} hover:bg-surface-2`}>
                      <span className="text-fg">{blockWords(b)}</span>
                      <span className="shrink-0 text-t12 font-medium text-brand">{blockOpen(b) ? "Needs your value" : "Edit"}</span>
                    </button>
                  )}
              </li>
            ))}
          </ul>
          <p className={`${LABEL} mt-space-3`}>Add a block</p>
          <div className="flex flex-wrap gap-space-2">
            {BLOCK_PRESETS.map((p) => (
              <FilterChip key={p.key} selected={false} onClick={() => { setFlow((f) => ({ ...f, blocks: [...f.blocks, p.make(side)] })); setEditing(flow.blocks.length); }}>
                + {p.label}
              </FilterChip>
            ))}
          </div>
        </CardV2>
      ))}
      <CardV2 title="Price-action blocks" testId="flow-soon">
        <p className="text-t13 text-fg-muted">Coming soon to the builder. Until then, use the strategy templates for these ideas.</p>
        <ul className="mt-space-2 space-y-space-1" data-soon-blocks>
          {SOON_BLOCKS.map((s) => (
            <li key={s} className="flex items-start gap-space-2 text-t13 text-fg-muted"><Lock size={14} aria-hidden className="mt-0.5 shrink-0" />{s}</li>
          ))}
        </ul>
      </CardV2>
    </>
  );
}

const numText = (n: number | null) => (n === null ? "" : String(n));
const numOf = (raw: string): number | null => { const t = raw.trim(); if (!t) return null; const n = Number(t); return Number.isFinite(n) ? n : null; };

function OperandFields({ op, onChange, legend, allowValue, baseTimeframe }: {
  op: FlowOperand; onChange: (o: FlowOperand) => void; legend: string; allowValue: boolean; baseTimeframe: string | null;
}) {
  const ids = { ind: useId(), period: useId(), mult: useId(), value: useId(), htf: useId() };
  const options = useMemo(() => INDICATORS.map((i) => ({ value: i, label: INDICATOR_LABEL[i] })), []);
  // SB-UX SU7: reading this operand on a higher timeframe is an Advanced choice (closed); "Same as the strategy" by default.
  const higher = higherTimeframes(baseTimeframe);
  const htfOptions = [{ value: "", label: "Same as the strategy" },
    ...higher.map((tf) => ({ value: tf, label: TIMEFRAME_WORDS[tf] ?? tf })),
    ...(op.timeframe && !higher.includes(op.timeframe) ? [{ value: op.timeframe, label: `${TIMEFRAME_WORDS[op.timeframe] ?? op.timeframe} (not above the strategy's)`, disabled: true }] : [])];
  return (
    <fieldset className="space-y-space-2">
      <legend className={LABEL}>{legend}</legend>
      {allowValue && (
        <div className="flex gap-space-2">
          <FilterChip selected={op.type === "indicator"} onClick={() => onChange({ ...op, type: "indicator" })}>An indicator</FilterChip>
          <FilterChip selected={op.type === "value"} onClick={() => onChange({ ...op, type: "value" })}>A number</FilterChip>
        </div>
      )}
      {op.type === "value" ? (
        <div className="max-w-40">
          <label htmlFor={ids.value} className={LABEL}>Number</label>
          <input id={ids.value} inputMode="decimal" className={FIELD} value={numText(op.value)} onChange={(e) => onChange({ ...op, value: numOf(e.target.value) })} />
        </div>
      ) : (
        <div className="flex flex-wrap items-end gap-space-2">
          <div className="min-w-48 flex-1">
            <Combobox id={ids.ind} label="Indicator" value={op.indicator} options={options} searchable
              onChange={(v) => onChange({ ...op, indicator: v as IndicatorName })} />
          </div>
          {NEEDS_PERIOD.has(op.indicator) && (
            <div className="w-28">
              <label htmlFor={ids.period} className={LABEL}>{op.indicator === "OR_HIGH" || op.indicator === "OR_LOW" ? "Minutes" : "Length"}</label>
              <input id={ids.period} inputMode="numeric" className={FIELD} value={numText(op.period)} onChange={(e) => onChange({ ...op, period: numOf(e.target.value) })} />
            </div>
          )}
          {NEEDS_MULTIPLIER.has(op.indicator) && (
            <div className="w-28">
              <label htmlFor={ids.mult} className={LABEL}>{op.indicator === "SUPERTREND" ? "ATR multiple" : "Std. deviations"}</label>
              <input id={ids.mult} inputMode="decimal" className={FIELD} value={numText(op.multiplier)} onChange={(e) => onChange({ ...op, multiplier: numOf(e.target.value) })} />
            </div>
          )}
          {higher.length > 0 && (
            <AdvancedFold id="operand-timeframe" title="Advanced: higher timeframe" className="w-full">
              <div className="max-w-64">
                <label htmlFor={ids.htf} className={LABEL}>Read it on</label>
                <Combobox id={ids.htf} label="Read it on" value={op.timeframe ?? ""} options={htfOptions}
                  onChange={(v) => onChange({ ...op, timeframe: v || null })} />
                <p className="mt-space-1 text-t12 text-fg-muted">Completed bars of that timeframe only.</p>
              </div>
            </AdvancedFold>
          )}
        </div>
      )}
    </fieldset>
  );
}

function BlockEditor({ block, baseTimeframe, onDone, onRemove }: { block: FlowBlock; baseTimeframe: string | null; onDone: (b: FlowBlock) => void; onRemove: () => void }) {
  const [b, setB] = useState(block);
  return (
    <div className="space-y-space-3 rounded-control border border-brand px-space-3 py-space-3" data-block-editor>
      <p className="text-t14 font-medium text-fg">{blockWords(b)}</p>
      <OperandFields legend="This" op={b.left} allowValue={false} baseTimeframe={baseTimeframe} onChange={(left) => setB({ ...b, left })} />
      <div>
        <span className={LABEL}>Comparison</span>
        <div className="flex flex-wrap gap-space-2">
          {OPERATORS.map((o) => <FilterChip key={o} selected={b.operator === o} onClick={() => setB({ ...b, operator: o as ConditionOperator })}>{OPERATOR_WORDS[o]}</FilterChip>)}
        </div>
      </div>
      <OperandFields legend="That" op={b.right} allowValue baseTimeframe={baseTimeframe} onChange={(right) => setB({ ...b, right })} />
      <div className="flex flex-wrap gap-space-2">
        <button type="button" className={BUTTON_SECONDARY} onClick={() => onDone(b)} data-block-done>Done</button>
        <button type="button" className="inline-flex min-h-11 items-center px-space-2 text-t14 font-medium text-down hover:underline md:min-h-0" onClick={onRemove}>Remove</button>
      </div>
    </div>
  );
}

// --- Step 4 ---------------------------------------------------------------------------------------------------------

function NumberBox({ label, value, onChange, placeholder = "Off", hint }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; hint?: string }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={LABEL}>{label}</label>
      <input id={id} inputMode="decimal" className={FIELD} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      {hint && <p className="mt-space-1 text-t12 text-fg-muted">{hint}</p>}
    </div>
  );
}

function ExitStep({ flow, setFlow }: { flow: FlowState; setFlow: SetFlow }) {
  const ex = flow.exits;
  const op = flow.options;
  const setEx = (patch: Partial<FlowState["exits"]>) => setFlow((f) => ({ ...f, exits: { ...f.exits, ...patch } }));
  const setOp = (patch: Partial<FlowState["options"]>) => setFlow((f) => ({ ...f, options: { ...f.options, ...patch } }));
  return (
    <>
      {flow.engine === "custom" && (
        <CardV2 title="Exits: your own values" testId="flow-exits">
          <div className="grid grid-cols-2 gap-space-3 md:grid-cols-4">
            <NumberBox label="Stop (x ATR)" value={ex.stop} onChange={(stop) => setEx({ stop })} />
            <NumberBox label="Target 1 (R)" value={ex.target1} onChange={(target1) => setEx({ target1 })} />
            <NumberBox label="Target 2 (R)" value={ex.target2} onChange={(target2) => setEx({ target2 })} />
            <NumberBox label="Minimum R:R" value={ex.minRr} onChange={(minRr) => setEx({ minRr })} />
          </div>
          <p className="mt-space-2 text-t12 text-fg-muted" data-testid="sb-exits-own">A blank box is off: no stop-loss, no target, no minimum R:R.</p>
          {/* SB-UX SU7: the ATR length behind the stop is an Advanced choice (closed). */}
          <AdvancedFold id="exit-atr" className="mt-space-3" openWhen={atrLengthMissing(ex)}>
            <div className="max-w-48">
              <NumberBox label="ATR length" value={ex.atrPeriod} placeholder="With a stop" onChange={(atrPeriod) => setEx({ atrPeriod })} />
            </div>
          </AdvancedFold>
        </CardV2>
      )}
      <CardV2 title="What to trade" testId="flow-options">
        <div className="space-y-space-4">
          <OptionCards name="flow-structure" legend="Trade it as" value={op.structure ?? ("" as never)} options={STRUCTURES} dataAttr="data-structure"
            onChange={(structure) => setOp({ structure })} />
          {isOption(op.structure) && (
            <>
              <OptionCards name="flow-strike" legend={isSpread(op.structure) ? "Sold strike" : "Strike"} value={op.strikeRule ?? ("" as never)} options={STRIKES}
                dataAttr="data-strike" onChange={(strikeRule) => setOp({ strikeRule })} />
              <div className="grid grid-cols-2 gap-space-3 md:grid-cols-3">
                {op.strikeRule && op.strikeRule !== "ATM" && (
                  <NumberBox label="Strikes away" value={op.strikeOffset} placeholder="1 to 10" onChange={(strikeOffset) => setOp({ strikeOffset })} />
                )}
                {isSpread(op.structure) && (
                  <NumberBox label="Spread width (strikes)" value={op.width} placeholder="1 to 20" onChange={(width) => setOp({ width })} />
                )}
                <NumberBox label="Lots" value={op.lots} placeholder="Trading bot's own" onChange={(lots) => setOp({ lots })} />
              </div>
              <OptionCards name="flow-expiry" legend="Expiry" value={op.expiry ?? ("" as never)} options={EXPIRIES} dataAttr="data-expiry"
                onChange={(expiry) => setOp({ expiry })} />
            </>
          )}
        </div>
      </CardV2>
    </>
  );
}

// --- Step 5 and 6 ---------------------------------------------------------------------------------------------------

type ResultTab = "trades" | "chart" | "analytics";

function ResultStep({ flow, template }: { flow: FlowState; template: StrategyTemplate | null }) {
  const navigate = useNavigate();
  const source = useCandleSource(30, { storedHistory: true });
  const [saved, setSaved] = useState<CustomStrategyResponse | null>(null);
  const [savedFor, setSavedFor] = useState<string>("");
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [ranTf, setRanTf] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [candles, setCandles] = useState<OHLCVBar[]>([]);
  const [pricesFrom, setPricesFrom] = useState<string | null>(null);
  const [tab, setTab] = useState<ResultTab>("trades");
  const config = flowConfig(flow);
  const options = optionBacktest(flow.options, flow.timeframe);
  const symbol = flow.symbol.trim().toUpperCase();

  async function saveAndTest() {
    setBusy(true); setError(null);
    try {
      let id: string;
      let tf: string;
      if (flow.engine === "template") {
        if (!template || !flow.timeframe || !flow.side) throw new Error("This template cannot be tested here.");
        const a = await templatesApi.adopt(template.id, { timeframe: flow.timeframe, sides: flow.side });
        id = a.strategy_id; tf = a.deployment.timeframe;
      } else {
        if (!config) throw new Error("A step is not complete yet: go back and finish it.");
        // Save once; a later run with changed rules saves a new version of the same strategy.
        const key = JSON.stringify(config);
        const s = saved && savedFor === key ? saved : saved ? await api.updateCustomStrategy(saved.id, config) : await api.createCustomStrategy(config);
        setSaved(s); setSavedFor(key);
        id = s.strategy_id; tf = config.timeframe;
      }
      const fetched = await source.fetch([symbol], tf, { count: 600, seedFor: () => 11 });
      const bars = fetched.candles[symbol];
      if (!bars?.length) throw new Error(`No candles for ${symbol}.`);
      const res = await api.backtest(id, symbol, tf, bars, null, fetched.label, options, fetched.request);   // M55 T3
      setResult(res); setStrategyId(id); setRanTf(tf); setCandles(bars); setPricesFrom(fetched.line);
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  function startOnPaper() {
    if (!strategyId) return;
    const contract = paperContract(flow.options);
    if (flow.engine === "template" && template) {
      const state: FlowHandoff = { flowStrategy: { strategyId: `template-pick:${template.id}`, symbol: paperSymbol(symbol), contract, name: template.name } };
      navigate(paperBotPathFor({ template: template.id, timeframe: flow.timeframe, params: {} }), { state });
      return;
    }
    const state: FlowHandoff = { flowStrategy: { strategyId, symbol: paperSymbol(symbol), contract, name: config?.name ?? flow.name } };
    navigate(PAPER_PATH, { state });
  }

  const sum = result ? resultSummary(result) : null;
  const markers: ChartMarker[] = useMemo(() => (result?.trades ?? []).flatMap((t) => {
    const m: ChartMarker[] = [directionMarker(t.entry_time, t.direction as "LONG" | "SHORT", `Entry ${t.direction}`)];
    if (t.exit_time) m.push({ timestamp: t.exit_time, position: t.direction === "LONG" ? "aboveBar" : "belowBar", color: (t.pnl ?? 0) >= 0 ? "up" : "down", shape: "circle", text: "Exit" });
    return m;
  }), [result]);

  return (
    <>
      <CardV2 title="Your strategy" testId="flow-summary">
        <ul className="flex flex-wrap gap-space-1 text-t13">
          <li><Chip tone="neutral">{SYMBOL_LABEL[symbol] ?? symbol}</Chip></li>
          <li><Chip tone="neutral">{TIMEFRAME_WORDS[flow.timeframe ?? ""] ?? flow.timeframe}</Chip></li>
          <li><Chip tone="neutral">{flow.side === "both" ? "Long and short" : flow.side === "long" ? "Long" : "Short"}</Chip></li>
          <li><Chip tone="neutral">{STRUCTURES.find((s) => s.key === flow.options.structure)?.label}</Chip></li>
          {flow.options.expiry && <li><Chip tone="neutral">Expiry: {EXPIRIES.find((e) => e.key === flow.options.expiry)?.label}</Chip></li>}
          {flow.engine === "custom" && flow.blocks.filter((b) => sidesOf(flow.side).includes(b.side)).map((b, i) => (
            <li key={i}><Chip tone="neutral">{b.side === "long" ? "Long: " : "Short: "}{blockWords(b)}</Chip></li>
          ))}
          {flow.engine === "template" && template && <li><Chip tone="neutral">{template.name}</Chip></li>}
        </ul>
        {!result && (
          <button type="button" className={`${BUTTON_PRIMARY} mt-space-3`} disabled={busy} onClick={() => void saveAndTest()} data-save-test>
            {busy ? "Testing…" : flow.engine === "custom" ? "Save and test on past prices" : "Test on past prices"}
          </button>
        )}
        {error && <div className="mt-space-2"><NoteV2 kind="warn">{error}</NoteV2></div>}
      </CardV2>

      {result && sum && (
        <>
          <CardV2 title="Result on past prices" testId="flow-result">
            <div className="grid grid-cols-2 gap-space-3 md:grid-cols-3 lg:grid-cols-6" data-result-numbers>
              <Stat label="Net P&L"><Signed value={sum.netPnl} kind="money" /></Stat>
              <Stat label="Win rate">{formatNumber(sum.winRate, 1)}%</Stat>
              <Stat label="Max drawdown">
                {formatINR(sum.maxDd)}{sum.maxDdPct !== null && <span className="block text-t12 text-fg-muted">{formatNumber(sum.maxDdPct, 2)}% from the peak</span>}
              </Stat>
              <Stat label="Trades">{formatNumber(sum.trades)}</Stat>
              <Stat label="Average R">{sum.avgR === null ? <span className="text-t13 text-fg-muted">Needs your stop</span> : <Signed value={sum.avgR} kind="count" decimals={2} format={(v) => `${v.toFixed(2)}R`} />}</Stat>
              <Stat label="Equity"><Sparkline values={sum.spark} tone={sum.netPnl > 0 ? "up" : sum.netPnl < 0 ? "down" : "neutral"} width={120} height={32} /></Stat>
            </div>
            {pricesFrom && <p className="mt-space-2 text-t12 text-fg-muted" data-testid="prices-from">{pricesFrom}</p>}
            <button type="button" className={`${BUTTON_PRIMARY} mt-space-3`} onClick={startOnPaper} data-start-paper>Start on paper</button>
            <p className="mt-space-1 text-t12 text-fg-muted">Opens the Trading bot with this strategy picked. Nothing starts until you press Start on paper there.</p>
            <button type="button" className="mt-space-2 inline-flex min-h-11 items-center text-t13 font-medium text-brand hover:underline md:min-h-0" disabled={busy} onClick={() => void saveAndTest()}>
              {busy ? "Testing…" : "Test again"}
            </button>
          </CardV2>

          <CardV2 title="Details" testId="flow-details">
            <SubTabs<ResultTab> label="Result details" value={tab} onChange={setTab} idPrefix="flow-result"
              items={[{ key: "trades", label: `Trades (${result.trades.length})` }, { key: "chart", label: "Chart" }, { key: "analytics", label: "Analytics" }]} />
            <div className="mt-space-3" role="tabpanel" id={`flow-result-panel-${tab}`}>
              {tab === "trades" && <TradesList result={result} />}
              {tab === "chart" && (
                <Suspense fallback={null}><BacktestChartV2 candles={candles} symbol={symbol} timeframe={ranTf ?? undefined} markers={markers} /></Suspense>
              )}
              {tab === "analytics" && <Analytics result={result} />}
            </div>
          </CardV2>
        </>
      )}
    </>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-control bg-surface-2 px-space-3 py-space-2">
      <div className="text-t12 font-medium text-fg-muted">{label}</div>
      <div className="mt-space-1 text-t15 font-semibold tabular-nums text-fg">{children}</div>
    </div>
  );
}

function TradesList({ result }: { result: BacktestResult }) {
  if (!result.trades.length) return <p className="text-t13 text-fg-muted">No trades: the entry did not fire on these prices.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-t13 tabular-nums">
        <thead><tr className="text-left text-fg-muted"><th className="py-space-1 pr-space-3 font-medium">Entry</th><th className="pr-space-3 font-medium">Side</th>
          <th className="hidden pr-space-3 text-right font-medium sm:table-cell">Entry ₹</th><th className="hidden pr-space-3 text-right font-medium sm:table-cell">Exit ₹</th><th className="text-right font-medium">P&amp;L ₹</th></tr></thead>
        <tbody>
          {result.trades.slice(-100).reverse().map((t, i) => (
            <tr key={i} className="border-t border-subtle">
              <td className="py-space-1 pr-space-3 whitespace-nowrap">{new Date(t.entry_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
              <td className="pr-space-3">{t.direction === "LONG" ? "Long" : "Short"}</td>
              <td className="hidden pr-space-3 text-right sm:table-cell">{formatNumber(t.entry_price, 2)}</td>
              <td className="hidden pr-space-3 text-right sm:table-cell">{t.exit_price === null ? "Open" : formatNumber(t.exit_price, 2)}</td>
              <td className="whitespace-nowrap text-right"><Signed value={t.pnl} kind="money" arrow={false} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Analytics({ result }: { result: BacktestResult }) {
  const a = result.analytics;
  const rows: [string, React.ReactNode][] = [
    ["Profit factor", result.profit_factor === null ? "–" : formatNumber(result.profit_factor, 2)],
    ["Average win", <Signed key="w" value={result.avg_win} kind="money" />],
    ["Average loss", <Signed key="l" value={result.avg_loss} kind="money" />],
    ["Per trade (expectancy)", <Signed key="e" value={result.expectancy} kind="money" />],
  ];
  if (a) {
    rows.push(["Charges", formatINR(a.costs.total_charges)]);
    rows.push(["Most wins in a row", formatNumber(a.streaks.max_consecutive_wins)]);
    rows.push(["Most losses in a row", formatNumber(a.streaks.max_consecutive_losses)]);
  }
  return (
    <dl className="grid grid-cols-1 gap-x-space-4 sm:grid-cols-2">
      {rows.map(([k, v]) => (
        <div key={k} className="flex min-h-11 items-center justify-between border-b border-subtle text-t13 md:min-h-9">
          <dt className="text-fg-muted">{k}</dt><dd className="font-medium tabular-nums text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
