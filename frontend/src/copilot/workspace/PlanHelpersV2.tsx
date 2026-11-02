import { useEffect, useState } from "react";
import { cx } from "../../components/primitives/cx";
import type { Leg } from "../../optionsBuilder/model";
import { addHedge, mirrorLegs, reverseLegs, rollExpiry, rollStrikes, type HelperResult } from "./helpers";

/**
 * M76 MP-8 (owner order M76 E-1; flag `copilot_workspace`): the plan's one-tap helpers, under its legs. Each tap only
 * prepares the plan's legs (`onLegs`); nothing is sent from here - Place stays the one ticket's, pressed by him
 * (ADR-0006). A helper that needs a value he has not given says which one. Rules: `helpers.ts` (tested in helpers.test.tsx).
 */
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";
const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";

export interface PlanHelpersProps {
  legs: Leg[];
  atm: number | null;
  step: number | null;
  /** The IV the engine solved for each leg of the last reply (by leg id); empty while there is none. */
  ivById: Readonly<Record<string, number>>;
  /** His saved protective-leg distance (Trade settings › Options), null = Not set. */
  savedHedgeOffset: number | null;
  onLegs: (legs: Leg[]) => void;
}

export function PlanHelpersV2({ legs, atm, step, ivById, savedHedgeOffset, onLegs }: PlanHelpersProps) {
  const [expiry, setExpiry] = useState("");
  const [out, setOut] = useState(savedHedgeOffset != null ? String(savedHedgeOffset) : "");
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { if (savedHedgeOffset != null) setOut((cur) => cur || String(savedHedgeOffset)); }, [savedHedgeOffset]);
  const options = legs.some((l) => l.option_type !== "FUT");
  const apply = (r: HelperResult) => {
    if ("missing" in r) { setNote(`Needs ${r.missing}.`); return; }
    setNote(r.note);
    onLegs(r.legs);
  };
  const outNum = /^\d+$/.test(out.trim()) ? Number(out.trim()) : null;
  return (
    <fieldset className="space-y-space-2 rounded-panel border border-subtle p-space-3" data-plan="helpers">
      <legend className="px-space-1 text-t13 font-semibold text-fg">Change the plan</legend>
      <div className="flex flex-wrap gap-space-2">
        <button type="button" className={BTN} onClick={() => apply(reverseLegs(legs))} data-helper="reverse">Reverse</button>
        {options && <button type="button" className={BTN} onClick={() => apply(mirrorLegs(legs, atm, ivById))} data-helper="mirror">Mirror this spread</button>}
        {options && <button type="button" className={BTN} onClick={() => apply(rollStrikes(legs, -1, step, ivById))} data-helper="roll-down">Strikes one step down</button>}
        {options && <button type="button" className={BTN} onClick={() => apply(rollStrikes(legs, 1, step, ivById))} data-helper="roll-up">Strikes one step up</button>}
      </div>
      {options && (
        <div className="grid grid-cols-1 gap-space-2 md:grid-cols-2">
          <div className="flex items-end gap-space-2">
            <label className="flex min-w-0 flex-1 flex-col gap-space-1"><span className="text-t12 text-fg-muted">Roll to expiry</span>
              <input type="date" aria-label="Roll to expiry" className={FIELD} value={expiry} onChange={(e) => setExpiry(e.target.value)} data-helper-field="expiry" /></label>
            <button type="button" className={BTN} onClick={() => apply(rollExpiry(legs, expiry, ivById))} data-helper="roll-expiry">Roll</button>
          </div>
          <div className="flex items-end gap-space-2">
            <label className="flex min-w-0 flex-1 flex-col gap-space-1"><span className="text-t12 text-fg-muted">Hedge: strikes further out</span>
              <input aria-label="Hedge: strikes further out" className={FIELD} value={out} inputMode="numeric" placeholder="Not set"
                onChange={(e) => setOut(e.target.value)} data-helper-field="hedge-out" /></label>
            <button type="button" className={BTN} onClick={() => apply(addHedge(legs, outNum, step, ivById))} data-helper="add-hedge">Add hedge</button>
          </div>
        </div>
      )}
      {note && <p role="status" className={cx("text-t12", note.startsWith("Needs") ? "text-fg" : "text-fg-muted")} data-helper-note>{note}</p>}
      <p className="text-t12 text-fg-muted">Nothing is sent until you press Place.</p>
    </fieldset>
  );
}

export default PlanHelpersV2;
