import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { CardV2, EmptyStateV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { Combobox } from "../ui/Combobox";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { ErrorState } from "../ui/ErrorState";
import { formatNumber } from "../ui/format";
import type { Load } from "./ReviewsV2";
import type { GoalMetric, GoalProgress, JournalPlan, PlanBody, PlanTrigger } from "./types";

/**
 * P2-8 J5: his own if-then plans ("If I feel unease after entry, then I check the stop and wait 5 minutes") and process
 * goals ("plan followed on 90% of trades this month"). His words only: ATP never writes a plan for him and never blocks
 * anything because of one. A plan can come back after a stop hit or a loss (his choice); goals show progress as a count,
 * and as a share once he has enough trades.
 */

export const TRIGGER_LABELS: Record<PlanTrigger, string> = { NONE: "Only before the open", STOP_HIT: "Also after a stop hit", LOSS: "Also after a loss" };
const METRIC_OPTIONS: { value: GoalMetric; label: string }[] = [
  { value: "PLAN_FOLLOWED", label: "Plan followed" }, { value: "STOP_KEPT", label: "Stop kept" }, { value: "CHECKED_IN", label: "Checked in" }];

export function goalText(g: GoalProgress): string {
  const span = g.period === "WEEK" ? "this week" : "this month";
  const share = g.pct != null ? ` (${formatNumber(g.pct, 0)}%)` : "";
  return `${g.label}: ${formatNumber(g.done)} of ${formatNumber(g.base)}${share} ${span}; your goal ${g.target_pct}%`;
}

export function IfThenListV2({ plans }: { plans: readonly JournalPlan[] }) {
  return (
    <ul className="space-y-space-2" data-if-then>
      {plans.map((p) => (
        <li key={p.id} className="text-t14 text-fg"><span className="font-semibold">If</span> {p.if_text}, <span className="font-semibold">then</span> {p.then_text}</li>
      ))}
    </ul>
  );
}

export function GoalsListV2({ goals }: { goals: readonly GoalProgress[] }) {
  return (
    <ul className="space-y-space-2" data-goals>
      {goals.map((g) => (
        <li key={g.id} className="flex flex-wrap items-center gap-space-2 text-t14 text-fg">
          <span>{goalText(g)}</span>
          {g.small && <span className="text-t12 text-fg-muted">small sample</span>}
          {g.reached && <span className={chipClass({ tone: "up", variant: "tint" })}>Reached</span>}
        </li>
      ))}
    </ul>
  );
}

export function PlansV2({ state, onRetry, onAdd, onToggle, onRemove }: {
  state: Load<{ plans: JournalPlan[]; goals: GoalProgress[] }>; onRetry: () => void; onAdd: (body: PlanBody) => Promise<boolean>;
  onToggle: (plan: JournalPlan) => void; onRemove: (plan: JournalPlan) => Promise<void>;
}) {
  const [ifText, setIfText] = useState("");
  const [thenText, setThenText] = useState("");
  const [trigger, setTrigger] = useState<PlanTrigger>("NONE");
  const [metric, setMetric] = useState<GoalMetric>("PLAN_FOLLOWED");
  const [target, setTarget] = useState("90");
  const [period, setPeriod] = useState<"WEEK" | "MONTH">("MONTH");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<JournalPlan | null>(null);
  const run = async (body: PlanBody, reset: () => void) => {
    setBusy(true);
    try { if (await onAdd(body)) reset(); } finally { setBusy(false); }
  };
  const data = state.status === "ok" ? state.data : null;
  const ifThen = data?.plans.filter((p) => p.kind === "IF_THEN") ?? [];
  const goals = data?.plans.filter((p) => p.kind === "GOAL") ?? [];
  const row = (p: JournalPlan, text: string, sub: string) => (
    <li key={p.id} className="flex items-start justify-between gap-space-3 border-b border-subtle py-space-2 last:border-0" data-plan={p.id}>
      <span className="min-w-0">
        <span className={`block text-t14 ${p.active ? "text-fg" : "text-fg-muted line-through"}`}>{text}</span>
        <span className="text-t12 text-fg-muted">{sub}</span>
      </span>
      <span className="flex shrink-0 gap-space-1">
        <button type="button" className={BUTTON_SECONDARY} aria-pressed={p.active} onClick={() => onToggle(p)} data-plan-toggle>{p.active ? "On" : "Off"}</button>
        <button type="button" className={BUTTON_SECONDARY} aria-label="Remove" onClick={() => setRemoving(p)} data-plan-remove><Trash2 size={16} aria-hidden /></button>
      </span>
    </li>
  );
  return (
    <div className="space-y-space-4">
      <CardV2 title="If-then plans" testId="if-then-plans" tour="journal-plans">
        <div className="space-y-space-4">
          {state.status === "loading" && <LoadingRowsV2 rows={3} />}
          {state.status === "error" && <ErrorState message={state.error} onRetry={onRetry} variant="inline" />}
          {data && ifThen.length === 0 && <EmptyStateV2 text="No plans yet. Write one in your own words." />}
          {ifThen.length > 0 && <ul>{ifThen.map((p) => row(p, `If ${p.if_text}, then ${p.then_text}`, TRIGGER_LABELS[p.trigger]))}</ul>}
          <div className="grid gap-space-2 md:grid-cols-2" data-plan-add="if-then">
            <label className="min-w-0"><span className={LABEL}>If…</span>
              <input className={FIELD} value={ifText} maxLength={200} placeholder="I feel unease after entry" onChange={(e) => setIfText(e.target.value)} data-plan-if />
            </label>
            <label className="min-w-0"><span className={LABEL}>Then I will…</span>
              <input className={FIELD} value={thenText} maxLength={300} placeholder="check the stop and wait 5 minutes" onChange={(e) => setThenText(e.target.value)} data-plan-then />
            </label>
            <div className="min-w-0"><span aria-hidden className={LABEL}>Show it</span>
              <Combobox label="Show it" value={trigger} onChange={(v) => setTrigger(v as PlanTrigger)} dataAttrs={{ "data-plan-trigger": "" }}
                options={(Object.keys(TRIGGER_LABELS) as PlanTrigger[]).map((k) => ({ value: k, label: TRIGGER_LABELS[k] }))} />
            </div>
            <button type="button" className={`${BUTTON_PRIMARY} self-end`} disabled={busy || !ifText.trim() || !thenText.trim()} data-plan-save="if-then"
              onClick={() => run({ kind: "IF_THEN", if_text: ifText, then_text: thenText, trigger }, () => { setIfText(""); setThenText(""); })}>
              <Plus size={16} aria-hidden />Add plan
            </button>
          </div>
        </div>
      </CardV2>
      <CardV2 title="Process goals" testId="process-goals">
        <div className="space-y-space-4">
          {data && data.goals.length > 0 && <GoalsListV2 goals={data.goals} />}
          {data && goals.length === 0 && <EmptyStateV2 text="No goals yet. Set one about how you trade, not about profit." />}
          {goals.length > 0 && <ul>{goals.map((p) => row(p, `${p.metric_label} on ${p.target_pct}% of trades`, p.period === "WEEK" ? "Each week" : "Each month"))}</ul>}
          <div className="grid grid-cols-2 gap-space-2 md:grid-cols-4" data-plan-add="goal">
            <div className="min-w-0"><span aria-hidden className={LABEL}>Measure</span>
              <Combobox label="Measure" value={metric} onChange={(v) => setMetric(v as GoalMetric)} options={METRIC_OPTIONS} dataAttrs={{ "data-goal-metric": "" }} />
            </div>
            <label className="min-w-0"><span className={LABEL}>Goal (%)</span>
              <input className={FIELD} type="number" min={1} max={100} step={1} inputMode="numeric" value={target} onChange={(e) => setTarget(e.target.value)} data-goal-target />
            </label>
            <div className="min-w-0"><span aria-hidden className={LABEL}>Period</span>
              <Combobox label="Period" value={period} onChange={(v) => setPeriod(v as "WEEK" | "MONTH")} dataAttrs={{ "data-goal-period": "" }}
                options={[{ value: "WEEK", label: "Each week" }, { value: "MONTH", label: "Each month" }]} />
            </div>
            <button type="button" className={`${BUTTON_PRIMARY} self-end`} data-plan-save="goal"
              disabled={busy || !Number.isInteger(Number(target)) || Number(target) < 1 || Number(target) > 100}
              onClick={() => run({ kind: "GOAL", metric, target_pct: Number(target), period }, () => undefined)}>
              <Plus size={16} aria-hidden />Add goal
            </button>
          </div>
        </div>
      </CardV2>
      <ConfirmDialog open={removing !== null} onOpenChange={(o) => { if (!o) setRemoving(null); }} title="Remove this plan?"
        consequence="It is removed from your plans and reviews." confirmLabel="Remove" busy={busy}
        onConfirm={async () => { if (removing) { setBusy(true); try { await onRemove(removing); } finally { setBusy(false); setRemoving(null); } } }} />
    </div>
  );
}
