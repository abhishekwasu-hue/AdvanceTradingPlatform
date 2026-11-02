import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../../components/primitives/cx";
import { builderApi, wireLegs } from "../../optionsBuilder/api";
import { LegTable } from "../../optionsBuilder/LegTable";
import { MetricsCard } from "../../optionsBuilder/MetricsCard";
import { evaluatedById, hedgeFirst, money, rangePctFor, repriceModelLegs, type Direction, type Evaluation, type Leg, type OptionKind } from "../../optionsBuilder/model";
import { PayoffCanvas } from "../../optionsBuilder/PayoffCanvas";
import { useTradeSettings } from "../../settings/tradeSettings";
import { Combobox } from "../../ui/Combobox";
import { Details } from "../../ui/Details";
import { useFeatures } from "../../ui/useFeatures";
import { useMediaQuery } from "../../utils/useMediaQuery";
import {
  COMBINED_MEASURES, EMPTY_EQUITY, EMPTY_EXITS, EMPTY_INSTRUMENT, EMPTY_LEG, EXIT_MEASURES, NOT_SET_TEXT, PLAN_KINDS, TRAIL_KINDS,
  equityValue, exitLines, legMissing, legOf, positive, savedExits, singleEntry,
  type EquityDraft, type ExitDraft, type InstrumentDraft, type LegDraft, type PlanKind,
} from "./plan";
import { PlanPlaceV2 } from "./PlanPlaceV2";
import type { Annotations } from "./annotations";
import { RiskPreviewV2, type RiskPreviewApi } from "./RiskPreviewV2";
import { WhatIfV2, type WhatIfApi } from "./WhatIfV2";
import { ivPointsOf } from "./riskPreview";
import { TriggerPlansV2 } from "./TriggerPlansV2";
import { atmOf, savedHelpers, solvedIvs } from "./helpers";
import { PlanHelpersV2 } from "./PlanHelpersV2";

/**
 * M75 W-6 (owner order M75 CW-4, flag `copilot_workspace`): the trade plan panel beside the chart, in the same tab.
 * Instrument type (an option strategy of any shape, a future, equity), legs / strikes / expiry / lots and the exits at
 * entry - every one of them Not set until he fills it in (M68); nothing is chosen for him, and "Use my saved exits" is
 * his own tap. The numbers are the Options Builder's engine (`builderApi.evaluate`): the same payoff, Greeks, max loss /
 * profit, break-evens and margin estimate the builder shows for the same legs (`LegTable`, `PayoffCanvas`,
 * `MetricsCard` reused). This panel itself calls no order route; W-7's `PlanPlaceV2` hands the plan to the existing
 * ticket / basket, where he reviews and presses Place himself (ADR-0006).
 */
const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";
const SEG = "min-h-11 rounded-control px-space-3 text-t13 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";
const EVALUATE_DELAY_MS = 250;

function todayIst(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

/** One labelled field; its empty value reads "Not set". */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="flex min-w-0 flex-col gap-space-1"><span className="text-t12 text-fg-muted">{label}</span>{children}</label>;
}

function Text({ label, value, onChange, kind = "decimal", testId }: {
  label: string; value: string; onChange: (v: string) => void; kind?: "decimal" | "numeric" | "date" | "time"; testId: string;
}) {
  return (
    <Field label={label}>
      <input aria-label={label} className={FIELD} value={value} placeholder={kind === "date" || kind === "time" ? undefined : NOT_SET_TEXT} data-plan={testId}
        type={kind === "date" ? "date" : kind === "time" ? "time" : "text"} inputMode={kind === "date" || kind === "time" ? undefined : kind}
        onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

function Choice<T extends string>({ label, value, options, onChange, testId }: {
  label: string; value: T | null; options: { value: T; label: string }[]; onChange: (v: T | null) => void; testId: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-space-1">
      <span className="text-t12 text-fg-muted">{label}</span>
      <Combobox label={label} value={value ?? ""} placeholder={NOT_SET_TEXT} dataAttrs={{ "data-plan": testId }}
        options={options.map((o) => ({ value: o.value, label: o.label }))}
        onChange={(v) => onChange(options.find((o) => o.value === v)?.value ?? null)} />
    </div>
  );
}

const SIDES: { value: Direction; label: string }[] = [{ value: "BUY", label: "Buy" }, { value: "SELL", label: "Sell" }];
const TYPES: { value: OptionKind; label: string }[] = [{ value: "CE", label: "Call (CE)" }, { value: "PE", label: "Put (PE)" }, { value: "FUT", label: "Future" }];

export interface PlanPanelProps {
  /** The chart's symbol (the object he opened); the plan is for it. */
  symbol: string;
  /** Test hook: the builder's evaluate call. */
  evaluate?: typeof builderApi.evaluate;
  /** Test hook: the risk preview's reads (M76 MP-4). */
  riskApi?: RiskPreviewApi;
  /** M76 MP-6: the open chart's reading (its next areas), null while there is none. */
  annotations?: Annotations | null;
  /** Test hook: the what-if reads (M76 MP-6). */
  whatIfApi?: WhatIfApi;
}

export function PlanPanelV2({ symbol, evaluate = builderApi.evaluate, riskApi, annotations = null, whatIfApi }: PlanPanelProps) {
  const [kind, setKind] = useState<PlanKind | null>(null);
  const [inst, setInst] = useState<InstrumentDraft>(EMPTY_INSTRUMENT);
  const [draft, setDraft] = useState<LegDraft>(EMPTY_LEG);
  const [legs, setLegs] = useState<Leg[]>([]);
  const [equity, setEquity] = useState<EquityDraft>(EMPTY_EQUITY);
  const [exits, setExits] = useState<ExitDraft>(EMPTY_EXITS);
  const [evaluation, setEvaluation] = useState<{ evaluation: Evaluation; sent: Leg[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // M76 MP-6: the what-if scenario the payoff draws (the engine's reply for his shifts), null = today's plan
  const [scenario, setScenario] = useState<{ evaluation: Evaluation; price: number } | null>(null);
  const seq = useRef(0);
  const phone = useMediaQuery("(max-width: 767px)");
  const features = useFeatures();
  const settings = useTradeSettings(features.on("ui_v2"));
  const plansOn = features.on("ui_v2") && features.on("trigger_plans");
  const asOf = useMemo(todayIst, []);
  const spot = positive(inst.spot);
  const step = positive(inst.step);

  // A new symbol starts a new plan: nothing carried over from another chart.
  useEffect(() => {
    setKind(null); setInst(EMPTY_INSTRUMENT); setDraft(EMPTY_LEG); setLegs([]); setEquity(EMPTY_EQUITY); setExits(EMPTY_EXITS);
  }, [symbol]);

  // The builder's engine prices the legs (debounced; an older reply never overwrites a newer one).
  useEffect(() => {
    const n = ++seq.current;
    if (kind === "EQUITY" || !legs.length || spot == null) { setEvaluation(null); setProblem(null); return undefined; }
    const sent = legs;
    const timer = window.setTimeout(() => {
      evaluate({ legs: wireLegs(sent), spot, as_of: asOf, days_forward: 0, iv_shift: 0, range_pct: rangePctFor(spot, sent), points: 241 })
        .then((ev) => { if (n === seq.current) { setProblem(null); setEvaluation({ evaluation: ev, sent }); } })
        .catch((e: unknown) => { if (n === seq.current) setProblem(e instanceof Error ? e.message : String(e)); });
    }, EVALUATE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [kind, legs, spot, asOf, evaluate]);

  // M76 MP-8: a leg a helper moved to another contract carries the model's price; the builder's own rule re-prices it once
  // the engine has answered for that contract (a typed premium is never touched).
  useEffect(() => {
    if (!evaluation || evaluation.sent !== legs) return;
    const next = repriceModelLegs(legs, evaluation);
    if (next !== legs) setLegs(next);
  }, [evaluation, legs]);

  const rows = useMemo(() => evaluatedById(legs, evaluation), [legs, evaluation]);
  const missing = kind && kind !== "EQUITY" ? legMissing(draft, inst, kind) : [];
  const entry = singleEntry(kind, legs, equity);
  const lines = exitLines(exits, entry);
  const own = settings ? savedExits(settings.sections.flatMap((s) => s.fields)) : {};
  const hasOwn = Object.keys(own).length > 0;
  const ivPoints = ivPointsOf(settings?.sections.flatMap((s) => s.fields));
  // M76 MP-8 (E-1): only what he saved himself (Not set = no button / an empty field)
  const mine = savedHelpers(settings?.sections.flatMap((s) => s.fields));
  const ivById = evaluation && evaluation.sent === legs ? solvedIvs(evaluation.sent, evaluation.evaluation.legs) : {};
  const pickKind = (k: PlanKind) => { setKind(k); setLegs([]); setDraft(EMPTY_LEG); setEvaluation(null); };
  const addLeg = () => {
    if (!kind) return;
    const leg = legOf(draft, inst, kind);
    if (!leg) return;
    setLegs((all) => hedgeFirst([...all, leg]));
    setDraft(EMPTY_LEG);
  };
  const x = (patch: Partial<ExitDraft>) => setExits((e) => ({ ...e, ...patch }));

  return (
    <section aria-labelledby="copilot-plan-title" data-plan-panel={kind ?? "none"} className="min-w-0 space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-4">
      <header className="flex flex-wrap items-baseline justify-between gap-space-2">
        <h3 id="copilot-plan-title" className="text-t14 font-semibold text-fg">Trade plan · <span className="font-tabular">{symbol}</span></h3>
      </header>

      <div role="radiogroup" aria-label="Instrument" className="flex flex-wrap gap-space-1" data-plan="kind">
        {PLAN_KINDS.map((k) => (
          <button key={k.value} type="button" role="radio" aria-checked={kind === k.value} onClick={() => pickKind(k.value)} data-option={k.value}
            className={cx(SEG, "border", kind === k.value ? "border-brand bg-surface-2 text-fg" : "border-border text-fg-muted hover:text-fg")}>{k.label}</button>
        ))}
      </div>
      {!kind && <p className="text-t13 text-fg-muted" data-plan="ask-kind">Choose what you plan to trade. Every field starts Not set.</p>}

      {kind && kind !== "EQUITY" && (
        <>
          <div className="grid grid-cols-3 gap-space-2" data-plan="instrument">
            <Text label="Spot" value={inst.spot} onChange={(v) => setInst({ ...inst, spot: v })} testId="spot" />
            <Text label="Lot size" value={inst.lotSize} onChange={(v) => setInst({ ...inst, lotSize: v })} kind="numeric" testId="lot-size" />
            <Text label="Strike step" value={inst.step} onChange={(v) => setInst({ ...inst, step: v })} testId="step" />
          </div>
          <fieldset className="space-y-space-2 rounded-panel border border-subtle p-space-3" data-plan="new-leg">
            <legend className="px-space-1 text-t13 font-semibold text-fg">New leg</legend>
            <div className="grid grid-cols-2 gap-space-2 md:grid-cols-3">
              <Choice label="Side" value={draft.direction} options={SIDES} onChange={(v) => setDraft({ ...draft, direction: v })} testId="leg-side" />
              {kind === "OPTIONS" && <Choice label="Type" value={draft.optionType} options={TYPES} onChange={(v) => setDraft({ ...draft, optionType: v })} testId="leg-type" />}
              {kind === "OPTIONS" && draft.optionType !== "FUT" && <Text label="Strike" value={draft.strike} onChange={(v) => setDraft({ ...draft, strike: v })} testId="leg-strike" />}
              <Text label="Expiry" value={draft.expiry} onChange={(v) => setDraft({ ...draft, expiry: v })} kind="date" testId="leg-expiry" />
              <Text label="Lots" value={draft.lots} onChange={(v) => setDraft({ ...draft, lots: v })} kind="numeric" testId="leg-lots" />
              <Text label={kind === "FUTURE" || draft.optionType === "FUT" ? "Price" : "Premium"} value={draft.premium} onChange={(v) => setDraft({ ...draft, premium: v })} testId="leg-premium" />
            </div>
            <div className="flex flex-wrap items-center gap-space-2">
              <button type="button" className={cx(BTN, "border border-border text-fg hover:bg-surface-2")} disabled={missing.length > 0} onClick={addLeg} data-plan="add-leg">Add leg</button>
              {mine.lots != null && draft.lots !== String(mine.lots) && (
                <button type="button" className={cx(BTN, "border border-border text-fg hover:bg-surface-2")} onClick={() => setDraft({ ...draft, lots: String(mine.lots) })}
                  data-plan="use-saved-lots">Use my saved lots ({mine.lots})</button>
              )}
              {missing.length > 0 && <span className="text-t12 text-fg-muted" data-plan="leg-missing">Still Not set: {missing.join(", ")}</span>}
            </div>
          </fieldset>
          {legs.length > 0 && (
            <LegTable legs={legs} rows={rows} step={step ?? 0} compact={phone}
              onChange={(next) => setLegs(hedgeFirst(next))} onAdd={() => document.querySelector<HTMLElement>("[data-plan='leg-side']")?.focus()} />
          )}
          {/* M76 MP-8 (E-1): one-tap helpers - each only prepares these legs; Place stays his, through the one ticket */}
          {legs.length > 0 && (
            <PlanHelpersV2 legs={legs} atm={atmOf(spot, step)} step={step} ivById={ivById} savedHedgeOffset={mine.hedgeOffset}
              onLegs={(next) => setLegs(hedgeFirst(next))} />
          )}
          {legs.length > 0 && spot == null && <p className="text-t13 text-fg-muted" data-plan="need-spot">Type the spot to see the payoff and the numbers.</p>}
          {problem && <p role="status" className="text-t13 text-fg" data-plan="problem">{problem}</p>}
          {evaluation && spot != null && (
            <div className="grid gap-space-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]" data-plan="numbers">
              <div className="min-w-0 md:rounded-panel md:border md:border-subtle md:p-space-2">
                {scenario && <p className="mb-space-1 text-t12 text-fg-muted" data-plan="payoff-scenario">Payoff in your what-if scenario · marker at its price</p>}
                <PayoffCanvas evaluation={scenario?.evaluation ?? evaluation.evaluation} legs={legs} spot={scenario?.price ?? spot} step={step ?? 1}
                  onStrikeChange={(id, strike) => setLegs((all) => all.map((l) => (l.id === id ? { ...l, strike } : l)))} />
              </div>
              <MetricsCard evaluation={evaluation.evaluation} stale={evaluation.sent !== legs} />
            </div>
          )}
          {/* M76 MP-4 (W-3): figures after the trade, read-only, under the builder's numbers. */}
          {evaluation && spot != null && evaluation.sent === legs && (
            <RiskPreviewV2 underlying={symbol} evaluation={evaluation.evaluation} legs={legs} spot={spot} asOf={asOf} ivPoints={ivPoints}
              {...(riskApi ? { api: riskApi } : {})} />
          )}
          {evaluation && spot != null && evaluation.sent === legs && (
            <WhatIfV2 base={evaluation.evaluation} legs={legs} spot={spot} asOf={asOf} annotations={annotations} onScenario={setScenario}
              {...(whatIfApi ? { api: whatIfApi } : {})} />
          )}
        </>
      )}

      {kind === "EQUITY" && (
        <div className="space-y-space-2" data-plan="equity">
          <div className="grid grid-cols-3 gap-space-2">
            <Choice label="Side" value={equity.direction} options={SIDES} onChange={(v) => setEquity({ ...equity, direction: v })} testId="eq-side" />
            <Text label="Quantity" value={equity.quantity} onChange={(v) => setEquity({ ...equity, quantity: v })} kind="numeric" testId="eq-qty" />
            <Text label="Price" value={equity.price} onChange={(v) => setEquity({ ...equity, price: v })} testId="eq-price" />
          </div>
          <dl className="divide-y divide-subtle rounded-panel border border-subtle px-space-3 text-t13" data-plan="equity-numbers">
            <div className="flex justify-between gap-space-3 py-space-2"><dt className="text-fg-muted">Order value</dt>
              <dd className="font-tabular text-fg" data-plan="eq-value">{entry ? money(equityValue(entry)) : NOT_SET_TEXT}</dd></div>
            <div className="py-space-2"><div className="flex justify-between gap-space-3"><dt className="text-fg-muted">Margin (estimate)</dt>
              <dd className="font-tabular text-fg" data-plan="eq-margin">{entry ? money(equityValue(entry)) : NOT_SET_TEXT}</dd></div>
              <p className="mt-space-1 text-t12 text-fg-muted">The full value for delivery. For intraday only your broker can say.</p></div>
          </dl>
        </div>
      )}

      {kind && (
        <fieldset className="space-y-space-2 rounded-panel border border-subtle p-space-3" data-plan="exits">
          <legend className="px-space-1 text-t13 font-semibold text-fg">Exits at entry</legend>
          {hasOwn && (
            <button type="button" className={cx(BTN, "border border-border text-fg hover:bg-surface-2")} onClick={() => x(own)} data-plan="use-saved-exits">
              Use my saved exits
            </button>
          )}
          <div className="grid grid-cols-2 gap-space-2 md:grid-cols-4">
            <Choice label="Stop-loss measured in" value={exits.stopKind} options={EXIT_MEASURES} onChange={(v) => x({ stopKind: v })} testId="stop-kind" />
            <Text label="Stop-loss" value={exits.stop} onChange={(v) => x({ stop: v })} testId="stop" />
            <Choice label="Target measured in" value={exits.targetKind} options={EXIT_MEASURES} onChange={(v) => x({ targetKind: v })} testId="target-kind" />
            <Text label="Target" value={exits.target} onChange={(v) => x({ target: v })} testId="target" />
            <Choice label="Trailing stop by" value={exits.trailKind} options={TRAIL_KINDS} onChange={(v) => x({ trailKind: v })} testId="trail-kind" />
            <Text label="Trailing stop" value={exits.trail} onChange={(v) => x({ trail: v })} testId="trail" />
            <Text label="Close after (minutes)" value={exits.timeMinutes} onChange={(v) => x({ timeMinutes: v })} kind="numeric" testId="time-minutes" />
            <Text label="Close at a time" value={exits.timeAt} onChange={(v) => x({ timeAt: v })} kind="time" testId="time-at" />
            {legs.length > 1 && (
              <Choice label="Combined exit measured on" value={exits.combined} options={COMBINED_MEASURES} onChange={(v) => x({ combined: v })} testId="combined" />
            )}
          </div>
          {lines.length > 0 ? (
            <dl className="divide-y divide-subtle text-t13" data-plan="exit-lines">
              {lines.map((l) => (
                <div key={l.key} className="flex justify-between gap-space-3 py-space-1" data-exit={l.key}>
                  <dt className="text-fg-muted">{l.label}</dt><dd className={cx("text-right font-tabular", l.key === "stop" ? "text-down" : l.key === "target" ? "text-up" : "text-fg")}>{l.text}</dd>
                </div>
              ))}
            </dl>
          ) : <p className="text-t12 text-fg-muted" data-plan="no-exits">No exit set yet. Exits are never blocked once the position is open.</p>}
        </fieldset>
      )}

      {/* M75 W-7 (CW-5): placing goes to the existing ticket / basket / equity ticket, each pressed by him. */}
      {kind && <PlanPlaceV2 plan={{ underlying: symbol, kind, legs, equity, exits }} />}
      {/* M75 W-8 (CW-6, flag trigger_plans): set and wait - his condition, then these legs, then his exits. */}
      {plansOn && (kind === "OPTIONS" || kind === "FUTURE") && <TriggerPlansV2 underlying={symbol} legs={legs} exits={exits} />}
      <Details id="copilot.plan" className="mt-0">
        <p className="text-t13 text-fg-muted">Every field stays Not set until you fill it in. The payoff, Greeks, max profit and loss, break-evens and the
          margin estimate come from the same engine as the Options Builder. The margin is ATP&apos;s estimate, never the broker&apos;s number.</p>
      </Details>
    </section>
  );
}

export default PlanPanelV2;
