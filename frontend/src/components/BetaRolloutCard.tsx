import { useState } from "react";
import { capNote, lowerStages, stageSourceNote, usageLine, usedPercent, type BetaCohort } from "../beta/rollout";
import { Card } from "./ui";

/** Z10-e: the staged beta rollout in the admin console - the stage (50 -> 200 -> 500), the effective cap
 * min(stage, BETA_COHORT_CAP), how many places are used and the headroom left. Moving up is one stage at a time and
 * needs a reason; every move lands in the audit trail. */
export default function BetaRolloutCard({ cohort, busy, onSetStage }: {
  cohort: BetaCohort | null;
  busy: boolean;
  onSetStage: (stage: number, reason?: string) => void;
}) {
  const [reason, setReason] = useState("");
  if (cohort === null) {
    return <Card title="Beta rollout"><p className="text-xs text-fg-muted">Loading the cohort…</p></Card>;
  }
  const pct = usedPercent(cohort);
  const full = cohort.headroom === 0;
  return (
    <Card title="Beta rollout">
      <div className="space-y-3 text-xs" data-stage={cohort.stage}>
        <p className="text-fg-muted">
          Sign-up through invite codes stops at the effective cap. The cohort grows in stages; move up only when the
          current stage has run cleanly. Moving down closes sign-up sooner and never touches existing accounts.
          {!cohort.gate_on && <span className="text-warn"> The beta_cohort_gate flag is off: sign-up is not gated right now.</span>}
        </p>

        <ol aria-label="Rollout stages" className="flex flex-wrap items-center gap-2">
          {cohort.stages.map((s) => {
            const current = s === cohort.stage;
            return (
              <li key={s} aria-current={current ? "step" : undefined}
                  className={`rounded-md border px-2 py-0.5 font-tabular ${current ? "border-brand bg-brand/10 font-bold text-fg" : s < cohort.stage ? "border-border text-fg-muted" : "border-dashed border-border text-fg-muted"}`}>
                {s}{current ? " (current)" : ""}
              </li>
            );
          })}
        </ol>

        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-semibold text-fg">{usageLine(cohort)}</span>
            <span className={full ? "font-semibold text-down" : "text-fg-muted"}>{full ? "Full" : `${pct}% used`}</span>
          </div>
          <div role="meter" aria-label="Cohort places used" aria-valuemin={0} aria-valuemax={cohort.cap} aria-valuenow={Math.min(cohort.used, cohort.cap)}
               aria-valuetext={usageLine(cohort)} className="mt-1 h-2 w-full overflow-hidden rounded bg-surface-2">
            <div className={`h-full ${full ? "bg-down" : pct >= 80 ? "bg-warn" : "bg-brand"}`} style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-1 text-fg-muted">{capNote(cohort)} {stageSourceNote(cohort)}</p>
        </div>

        <div className="flex flex-wrap items-end gap-2">
          {cohort.next_stage !== null ? (
            <>
              <label className="flex min-w-56 flex-1 flex-col gap-0.5">
                <span className="text-fg-muted">Reason for moving up (recorded in the audit trail)</span>
                <input className="rounded border border-border bg-surface-2 px-2 py-1" value={reason} maxLength={200}
                       onChange={(e) => setReason(e.target.value)} placeholder="e.g. stage 50 ran a week without incidents" />
              </label>
              <button type="button" disabled={busy || reason.trim().length < 3}
                      onClick={() => { onSetStage(cohort.next_stage as number, reason.trim()); setReason(""); }}
                      className="rounded bg-brand px-3 py-1 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50">
                Move up to {cohort.next_stage}
              </button>
            </>
          ) : <span className="text-fg-muted">This is the last stage.</span>}
          {lowerStages(cohort).map((s) => (
            <button key={s} type="button" disabled={busy}
                    onClick={() => { if (window.confirm(`Move the cohort down to ${s}? Sign-up stops once ${s} places are used.`)) onSetStage(s); }}
                    className="rounded border border-border px-3 py-1 text-fg hover:bg-surface-2 disabled:opacity-50">
              Move down to {s}
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}
