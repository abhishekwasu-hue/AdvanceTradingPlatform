import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";
import { errorSentence } from "../../api/errors";
import { cx } from "../../components/primitives/cx";
import type { Leg } from "../../optionsBuilder/model";
import { Combobox } from "../../ui/Combobox";
import { Details } from "../../ui/Details";
import { formatNumber } from "../../ui/format";
import { NOT_SET_TEXT, type ExitDraft } from "./plan";
import {
  EMPTY_TRIGGER, READ_TIMEFRAMES, areaChoices, conditionText, planRequest, statusTone, triggerMissing,
  type PlanArea, type PlanView, type TriggerDraft,
} from "./triggerPlans";

/**
 * M75 W-8 (owner order M75 CW-6, flag `trigger_plans`): "Save as trigger plan" from the trade plan panel and his plans
 * list, in the same tab. The condition, validity, order type, product and "place at once" / "ask me first" are his own
 * (Not set until he picks; Not set approval = his Trade settings, else the server asks). Saving arms the plan; only the
 * worker fires it, once, through the order ticket. Pause, resume and cancel are his taps. The AI writes no plan.
 * M75 W-9: the plan can also watch one of the open chart's reading areas (a candle of his timeframe with the reading's
 * rejection there) or a BOS / CHoCH on his timeframe - the same reading source as the chart's (engine or ATP, never both).
 */
const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-3 text-t13 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";
const TONE = { up: "text-up", down: "text-down", warn: "text-warn", muted: "text-fg-muted" } as const;

function Pick<T extends string>({ label, value, options, onChange, testId }: {
  label: string; value: T | null; options: { value: T; label: string }[]; onChange: (v: T) => void; testId: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-space-1">
      <span className="text-t12 text-fg-muted">{label}</span>
      <Combobox label={label} value={value ?? ""} placeholder={NOT_SET_TEXT} dataAttrs={{ "data-trigger": testId }} options={options}
        onChange={(v) => { const o = options.find((x) => x.value === v); if (o) onChange(o.value); }} />
    </div>
  );
}

function Field({ label, value, onChange, type = "text", testId, wide = false }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; testId: string; wide?: boolean;
}) {
  return (
    <label className={cx("flex min-w-0 flex-col gap-space-1", wide && "col-span-2 md:col-span-1")}>
      <span className="text-t12 text-fg-muted">{label}</span>
      <input aria-label={label} className={FIELD} type={type} value={value} placeholder={type === "text" ? NOT_SET_TEXT : undefined}
        inputMode={type === "text" ? "decimal" : undefined} data-trigger={testId} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

let refSeq = 0;
function newRef(): string {
  refSeq += 1;
  return `tp-${Date.now().toString(36)}-${refSeq}`;
}

export function TriggerPlansV2({ underlying, legs, exits, areas = [] }: { underlying: string; legs: Leg[]; exits: ExitDraft; areas?: PlanArea[] }) {
  const [draft, setDraft] = useState<TriggerDraft>(EMPTY_TRIGGER);
  const [plans, setPlans] = useState<PlanView[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [ref, setRef] = useState(newRef);
  const load = useCallback(() => {
    request<PlanView[]>("/trigger-plans").then(setPlans, () => setPlans([]));
  }, []);
  useEffect(load, [load]);
  const d = (patch: Partial<TriggerDraft>) => setDraft((x) => ({ ...x, ...patch }));
  const missing = triggerMissing(draft, legs);
  const choices = areaChoices(areas);
  const areaValue = draft.area ? choices.find((c) => c.area.low === draft.area?.low && c.area.high === draft.area?.high && c.area.side === draft.area?.side)?.value ?? null : null;

  async function save() {
    setBusy(true); setProblem(null);
    try {
      await request<PlanView>("/trigger-plans", { method: "POST", body: JSON.stringify(planRequest(underlying, draft, legs, exits, ref)) });
      setDraft(EMPTY_TRIGGER);
      setRef(newRef());
      load();
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(id: number, action: "pause" | "resume" | "cancel") {
    setProblem(null);
    try {
      await request<PlanView>(`/trigger-plans/${id}/${action}`, { method: "POST" });
      load();
    } catch (e) {
      setProblem(errorSentence(e));
    }
  }

  return (
    <div className="space-y-space-3" data-trigger-plans>
      <fieldset className="space-y-space-2 rounded-panel border border-subtle p-space-3" data-trigger="form">
        <legend className="px-space-1 text-t13 font-semibold text-fg">Save as trigger plan</legend>
        <div className="grid grid-cols-2 gap-space-2 md:grid-cols-4">
          <div className="col-span-2 min-w-0 md:col-span-1">
            <Pick label="The plan watches" value={draft.kind} onChange={(v) => d({ kind: v, timeframe: "" })} testId="kind"
              options={[{ value: "PRICE", label: "A price level" }, { value: "AREA", label: "Area + rejection" }, { value: "STRUCTURE", label: "BOS / CHoCH" }]} />
          </div>
          {draft.kind === "PRICE" && <>
            <Field label={`${underlying} level`} value={draft.level} onChange={(v) => d({ level: v })} testId="level" />
            <Pick label="When the price goes" value={draft.side} onChange={(v) => d({ side: v })} testId="side"
              options={[{ value: "ABOVE", label: "Above it" }, { value: "BELOW", label: "Below it" }]} />
            <Pick label="Fires on" value={draft.mode} onChange={(v) => d({ mode: v })} testId="mode"
              options={[{ value: "TOUCH", label: "A touch" }, { value: "CLOSE", label: "A candle close" }]} />
            {draft.mode === "CLOSE" && <Field label="Candle (minutes)" value={draft.timeframe} onChange={(v) => d({ timeframe: v })} testId="timeframe" />}
          </>}
          {draft.kind === "AREA" && (choices.length ? (
            <div className="col-span-2 min-w-0">
              <Pick label="Area from the chart's reading" value={areaValue} testId="area" options={choices.map(({ value, label }) => ({ value, label }))}
                onChange={(v) => d({ area: choices.find((c) => c.value === v)?.area ?? null })} />
            </div>
          ) : <p className="col-span-2 text-t12 text-fg-muted" data-trigger="no-areas">Open this symbol&apos;s chart: its reading lists the areas you can pick.</p>)}
          {draft.kind === "STRUCTURE" && <>
            <Pick label="Event" value={draft.event} onChange={(v) => d({ event: v })} testId="event"
              options={[{ value: "BOS", label: "Break of structure (BOS)" }, { value: "CHOCH", label: "Change of character (CHoCH)" }]} />
            <Pick label="Direction" value={draft.direction} onChange={(v) => d({ direction: v })} testId="direction"
              options={[{ value: "up", label: "Up" }, { value: "down", label: "Down" }]} />
          </>}
          {(draft.kind === "AREA" || draft.kind === "STRUCTURE") && (
            <Pick label="Candle timeframe" value={draft.timeframe || null} onChange={(v) => d({ timeframe: v })} testId="read-timeframe" options={READ_TIMEFRAMES} />
          )}
          <Field label="Watch from" type="time" value={draft.windowStart} onChange={(v) => d({ windowStart: v })} testId="window-start" />
          <Field label="Watch until" type="time" value={draft.windowEnd} onChange={(v) => d({ windowEnd: v })} testId="window-end" />
          <Field label="Valid until" type="datetime-local" value={draft.validUntil} onChange={(v) => d({ validUntil: v })} testId="valid-until" wide />
          <Pick label="Order type" value={draft.orderType} onChange={(v) => d({ orderType: v })} testId="order-type"
            options={[{ value: "MARKET", label: "Market" }, { value: "LIMIT", label: "Limit at the leg's premium" }]} />
          <Pick label="Product" value={draft.product} onChange={(v) => d({ product: v })} testId="product"
            options={[{ value: "MIS", label: "Intraday (MIS)" }, { value: "NRML", label: "Carry forward (NRML)" }]} />
          <Pick label="When it triggers" value={draft.approval} onChange={(v) => d({ approval: v })} testId="approval"
            options={[{ value: "AUTO", label: "Place at once" }, { value: "APPROVAL", label: "Ask me first" }]} />
        </div>
        <div className="flex flex-wrap items-center gap-space-2">
          <button type="button" className={cx(BTN, "bg-brand text-on-brand")} disabled={busy || missing.length > 0} onClick={() => void save()} data-trigger="save">
            Arm the plan
          </button>
          {missing.length > 0 && <span className="text-t12 text-fg-muted" data-trigger="missing">Still Not set: {missing.join(", ")}</span>}
        </div>
        {problem && <p role="alert" className="text-t13 text-fg" data-trigger="problem">{problem}</p>}
        <Details id="copilot.trigger" className="mt-0">
          <p className="text-t13 text-fg-muted">The plan waits for your level, your area&apos;s rejection candle or your BOS / CHoCH (read from
            the same source as the chart&apos;s reading, on closed candles after you arm it) and fires once: each leg goes through the same order ticket, with every
            check again then. A single leg carries your stop-loss and target. Exits are never blocked. Practice mode (PAPER).</p>
        </Details>
      </fieldset>

      <section aria-label="Trigger plans" data-trigger="list">
        <h4 className="mb-space-1 text-t13 font-semibold text-fg">Trigger plans</h4>
        {plans === null ? <p role="status" className="text-t12 text-fg-muted">Loading your trigger plans…</p> : plans.length === 0 ? (
          <p className="text-t12 text-fg-muted" data-trigger="none">No plan yet.</p>
        ) : (
          <ul className="divide-y divide-subtle rounded-panel border border-subtle">
            {plans.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-space-2 px-space-3 py-space-2 text-t13" data-plan-row={p.status}>
                <div className="min-w-0">
                  <span className={cx("font-semibold", TONE[statusTone(p.status)])}>{p.status_words}</span>
                  <span className="ml-space-2 font-tabular text-fg" data-trigger="condition">
                    {p.condition_words ? conditionText(p) : `${p.underlying} ${p.condition.side === "ABOVE" ? "above" : "below"} ${formatNumber(p.condition.level, 2)}`}
                  </span>
                  <span className="ml-space-2 text-fg-muted">{p.orders.length} order{p.orders.length === 1 ? "" : "s"} · {p.approval === "AUTO" ? "place at once" : "ask me first"}</span>
                  {p.note && <span className="block text-t12 text-fg-muted">{p.note}</span>}
                </div>
                <div className="flex gap-space-1">
                  {p.status === "ARMED" && <button type="button" className={cx(BTN, "border border-border text-fg")} onClick={() => void act(p.id, "pause")} data-trigger-act="pause">Pause</button>}
                  {p.status === "PAUSED" && <button type="button" className={cx(BTN, "border border-border text-fg")} onClick={() => void act(p.id, "resume")} data-trigger-act="resume">Resume</button>}
                  {(p.status === "ARMED" || p.status === "PAUSED") && (
                    <button type="button" className={cx(BTN, "border border-border text-down")} onClick={() => void act(p.id, "cancel")} data-trigger-act="cancel">Cancel</button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default TriggerPlansV2;
