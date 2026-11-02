import { useEffect, useMemo, useState } from "react";
import { request } from "../../api/client";
import { cx } from "../../components/primitives/cx";
import { builderApi } from "../../optionsBuilder/api";
import type { Evaluation, Leg } from "../../optionsBuilder/model";
import type { Annotations } from "./annotations";
import {
  EMPTY_SCENARIO, MAX_SCENARIOS, anyShift, areaRows, readScenarios, scenarioRequest, scenarioRows, scenarioWords, shiftsOf, toDraft, toSaved,
  type SavedScenario, type ScenarioDraft,
} from "./whatIf";

/**
 * M76 MP-6 (owner order M76 S-1 / S-2; flag `copilot_workspace`): what-if under the trade plan's numbers, in the same
 * tab. His spot move / IV change / days forward (each blank = not applied) ask the builder's engine once for the same
 * legs; the scenario's rows sit beside "now", and the plan's payoff chart above draws the scenario (`onScenario`). The
 * next areas of the open chart's reading get the plan's P&L at both edges. Saved scenarios are his prefs (`what_if`).
 * Read-only: no order route, nothing placed or changed. Rules and words: `whatIf.ts` (tested in whatIf.test.tsx).
 */
const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-3 text-t13 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";
const DELAY_MS = 300;

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }
export const whatIfApi = {
  evaluate: builderApi.evaluate,
  load: async () => {
    const r = await request<PrefsRead>("/me/prefs");
    return { list: readScenarios(r.prefs?.what_if), version: r.versions?.what_if ?? 0 };
  },
  save: async (scenarios: SavedScenario[], version: number) => {
    const r = await request<{ value: unknown; version: number }>(`/me/prefs/what_if?version=${version}`, { method: "PUT", body: JSON.stringify({ scenarios }) });
    return { list: readScenarios(r.value), version: r.version };
  },
};
export type WhatIfApi = typeof whatIfApi;

export interface WhatIfProps {
  base: Evaluation; legs: Leg[]; spot: number; asOf: string;
  annotations: Annotations | null;
  /** The scenario's engine reply and its price, for the payoff chart above (null = no scenario). */
  onScenario?: (s: { evaluation: Evaluation; price: number } | null) => void;
  api?: WhatIfApi;
}

function Num({ label, value, onChange, testId }: { label: string; value: string; onChange: (v: string) => void; testId: string }) {
  return (
    <label className="flex min-w-0 flex-col gap-space-1"><span className="text-t12 text-fg-muted">{label}</span>
      <input aria-label={label} className={FIELD} value={value} placeholder="Not applied" inputMode="decimal" data-whatif={testId}
        onChange={(e) => onChange(e.target.value)} /></label>
  );
}

export function WhatIfV2({ base, legs, spot, asOf, annotations, onScenario, api = whatIfApi }: WhatIfProps) {
  const [draft, setDraft] = useState<ScenarioDraft>(EMPTY_SCENARIO);
  const [scen, setScen] = useState<Evaluation | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ list: SavedScenario[]; version: number } | null>(null);
  const [name, setName] = useState("");
  const shifts = useMemo(() => shiftsOf(draft), [draft]);
  const active = anyShift(shifts);

  useEffect(() => {
    let live = true;
    api.load().then((s) => { if (live) setSaved(s); }).catch(() => { if (live) setSaved(null); });
    return () => { live = false; };
  }, [api]);

  useEffect(() => {
    let live = true;
    setScen(null); setProblem(null);
    if (!active || !legs.length) return undefined;
    const timer = window.setTimeout(() => {
      api.evaluate(scenarioRequest(legs, spot, asOf, shifts))
        .then((ev) => { if (live) setScen(ev); })
        .catch((e: unknown) => { if (live) setProblem(e instanceof Error ? e.message : String(e)); });
    }, DELAY_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [api, active, legs, spot, asOf, shifts]);

  const price = spot + (shifts.spotMove ?? 0);
  useEffect(() => { onScenario?.(scen && active ? { evaluation: scen, price } : null); }, [onScenario, scen, active, price]);
  useEffect(() => () => onScenario?.(null), [onScenario]);

  const rows = scen && active ? scenarioRows(base, scen, spot, shifts) : [];
  const areas = areaRows(base, annotations?.next_areas ?? null);
  const toSave = toSaved(name, draft);
  const full = (saved?.list.length ?? 0) >= MAX_SCENARIOS;
  const store = (list: SavedScenario[]) => {
    if (!saved) return;
    api.save(list, saved.version).then(setSaved).catch((e: unknown) => setProblem(e instanceof Error ? e.message : String(e)));
  };

  return (
    <section aria-label="What if" className="min-w-0 space-y-space-2 rounded-panel border border-subtle p-space-3" data-plan="what-if">
      <h4 className="text-t13 font-semibold text-fg">What if</h4>
      <div className="grid grid-cols-3 items-end gap-space-2">
        <Num label="Spot move (points)" value={draft.spotMove} onChange={(v) => setDraft({ ...draft, spotMove: v })} testId="spot-move" />
        <Num label="IV change (points)" value={draft.ivChange} onChange={(v) => setDraft({ ...draft, ivChange: v })} testId="iv-change" />
        <Num label="Days forward" value={draft.days} onChange={(v) => setDraft({ ...draft, days: v })} testId="days" />
      </div>
      {saved && saved.list.length > 0 && (
        <ul className="flex flex-wrap gap-space-1" aria-label="Saved scenarios" data-whatif="saved">
          {saved.list.map((s, i) => (
            <li key={`${s.name}-${i}`} className="flex items-center rounded-control border border-border">
              <button type="button" className={cx(BTN, "text-fg hover:bg-surface-2")} onClick={() => setDraft(toDraft(s))} data-whatif-apply={s.name}>{s.name}</button>
              <button type="button" aria-label={`Delete ${s.name}`} className={cx(BTN, "text-fg-muted hover:text-fg")} data-whatif-delete={s.name}
                onClick={() => store(saved.list.filter((_, j) => j !== i))}>×</button>
            </li>
          ))}
        </ul>
      )}
      {active && <p className="text-t12 text-fg-muted" data-whatif="words">{scenarioWords(shifts)} · the payoff above shows this scenario</p>}
      {problem && <p role="status" className="text-t13 text-fg" data-whatif="problem">{problem}</p>}
      {rows.length > 0 && (
        <dl className="divide-y divide-subtle text-t13" data-whatif="rows">
          {rows.map((r) => (
            <div key={r.key} className="py-space-1" data-whatif-row={r.key}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-space-3">
                <dt className="text-fg-muted">{r.label}</dt>
                <dd className={cx("ml-auto text-right font-tabular", r.tone === "up" ? "text-up" : r.tone === "down" ? "text-down" : "text-fg")}>{r.value}</dd>
              </div>
              {r.sub && <p className="text-right text-t12 text-fg-muted font-tabular">{r.sub}</p>}
            </div>
          ))}
        </dl>
      )}
      {active && saved && (
        <div className="flex flex-wrap items-end gap-space-2">
          <label className="flex min-w-0 flex-1 flex-col gap-space-1"><span className="text-t12 text-fg-muted">Scenario name</span>
            <input aria-label="Scenario name" className={FIELD} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} data-whatif="name" /></label>
          <button type="button" className={cx(BTN, "border border-border text-fg hover:bg-surface-2")} disabled={!toSave || full} data-whatif="save"
            onClick={() => { if (toSave) { store([...saved.list, toSave]); setName(""); } }}>Save scenario</button>
          {full && <span className="text-t12 text-fg-muted">{MAX_SCENARIOS} saved: delete one first</span>}
        </div>
      )}
      {areas.length > 0 && (
        <div data-whatif="areas">
          <h5 className="text-t12 font-semibold text-fg">If price reaches an area of the chart</h5>
          <ul className="divide-y divide-subtle text-t13">
            {areas.map((a) => (
              <li key={a.key} className="py-space-1" data-whatif-area={a.key}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-space-3">
                  <span className="text-fg-muted">{a.label}</span><span className="ml-auto font-tabular text-fg">{a.range}</span>
                </div>
                <p className={cx("text-right font-tabular", a.tone === "up" ? "text-up" : a.tone === "down" ? "text-down" : "text-fg")}>Now {a.today}</p>
                <p className="text-right text-t12 font-tabular text-fg-muted">At expiry {a.expiry}</p>
              </li>
            ))}
          </ul>
          <p className="mt-space-1 text-t12 text-fg-muted">Plan only. Margin at these levels: not known.</p>
        </div>
      )}
    </section>
  );
}

export default WhatIfV2;
