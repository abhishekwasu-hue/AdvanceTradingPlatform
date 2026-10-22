import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { CardV2, ChipV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { growthApi } from "./api";
import { meterFill, trialLine } from "./model";
import type { PlanMeter, PlanPage } from "./types";

/**
 * MO4 slice 3 (`ui_v2` + `growth_v2`): the plan page's top card on Settings → Account & plan - his plan in plain words
 * and how much of it he used, from the existing limits and counters. No price and no upgrade prompt here: the plans and
 * the billing stay in the card below it, unchanged. The parent mounts this only while both flags are on.
 */

type State = { status: "loading" } | { status: "error" } | { status: "ok"; data: PlanPage };

export function MeterRowV2({ meter }: { meter: PlanMeter }) {
  const fill = meterFill(meter);
  const bar = meter.state === "full" ? "bg-warn" : meter.state === "near" ? "bg-warn/70" : "bg-brand";
  return (
    <li data-meter={meter.key} data-state={meter.state} className="py-space-2">
      <div className="flex items-baseline justify-between gap-space-3">
        <span className="text-t14 text-fg">{meter.label}</span>
        <span className={`shrink-0 font-tabular text-t14 ${meter.state === "full" ? "font-semibold text-warn" : "text-fg"}`}>{meter.text}</span>
      </div>
      {fill !== null && (
        <div className="mt-space-1 h-1.5 rounded-pill bg-surface-2" role="meter" aria-label={meter.label}
          aria-valuemin={0} aria-valuemax={meter.limit ?? 0} aria-valuenow={meter.used ?? 0}>
          <div className={`h-1.5 rounded-pill ${bar}`} style={{ width: `${fill}%` }} />
        </div>
      )}
      {meter.state === "full" && <p className="mt-space-1 text-t12 text-warn">You have used all of this on your plan.</p>}
      {meter.note && <p className="mt-px text-t12 text-fg-muted">{meter.note}</p>}
    </li>
  );
}

export function PlanUsageBodyV2({ data }: { data: PlanPage }) {
  const trial = trialLine(data.trial);
  return (
    <div className="grid gap-space-5 md:grid-cols-2">
      <div>
        {trial && <p className="mb-space-2 text-t14 font-medium text-fg" data-trial>{trial}</p>}
        <h3 className="text-t14 font-semibold text-fg">What you used</h3>
        <ul className="mt-space-1 divide-y divide-subtle">
          {data.meters.map((m) => <MeterRowV2 key={m.key} meter={m} />)}
        </ul>
        <p className="mt-space-2 text-t12 text-fg-muted">{data.resets}</p>
      </div>
      <div>
        <h3 className="text-t14 font-semibold text-fg">What your plan includes</h3>
        <ul className="mt-space-2 space-y-space-2">
          {data.includes.map((row) => (
            <li key={row.key} data-include={row.key} className="flex gap-space-2 text-t14 text-fg">
              <Check size={16} aria-hidden className="mt-0.5 shrink-0 text-up" />
              <span className="min-w-0">{row.text}</span>
            </li>
          ))}
        </ul>
        <p className="mt-space-3 text-t12 text-fg-muted">Everything runs on paper trading for now. Plans and billing are below.</p>
      </div>
    </div>
  );
}

export default function PlanUsageV2() {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    let live = true;
    growthApi.plan().then((data) => { if (live) setState({ status: "ok", data }); }, () => { if (live) setState({ status: "error" }); });
    return () => { live = false; };
  }, []);
  const name = state.status === "ok" ? state.data.plan.name : null;
  return (
    <CardV2 testId="plan-usage" title={<>Your plan{name && <ChipV2 kind="brand">{name}</ChipV2>}</>}>
      {state.status === "loading" ? <LoadingRowsV2 rows={3} /> : state.status === "error" ? <LoadFailedV2 what="your plan" /> : <PlanUsageBodyV2 data={state.data} />}
    </CardV2>
  );
}
