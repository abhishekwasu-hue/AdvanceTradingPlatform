/**
 * M99 P2: the trade-management switches of an intraday template bot, inside the bot form's "Exits for this bot". Every
 * option is off; a switched-on option shows its number fields (empty, with the reference value as a hint only). The form's
 * own alert line shows the first problem (`managementProblem`); here the fields are only marked.
 */
import { useId } from "react";

import { FIELD, LABEL } from "../trading/PartsV2";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/numbersFirst";
import { MANAGEMENT_NOTES, referenceHint, type ManagementChoices, type ManagementPick, type Trail } from "./management";

interface Props {
  choices: ManagementChoices;
  pick: ManagementPick;
  onChange: (patch: Partial<ManagementPick>) => void;
  problem: string | null;
  touched: boolean;
}

const CHECK = "flex items-center gap-space-2 text-t14 text-fg";

function NumberField({ id, label, value, hint, bad, onChange }: { id: string; label: string; value: string; hint: string; bad: boolean;
  onChange: (v: string) => void }) {
  return (
    <div>
      <label htmlFor={id} className={LABEL}>{label}</label>
      <input id={id} className={FIELD} inputMode="decimal" autoComplete="off" value={value} placeholder={hint}
        aria-invalid={bad} onChange={(e) => onChange(e.target.value)} />
      {hint && <p className="mt-space-1 text-t12 text-fg-muted">{hint}</p>}
    </div>
  );
}

export function ManagementFields({ choices, pick, onChange, problem, touched }: Props) {
  const id = useId();
  const bad = touched && !!problem;
  const ref = (key: string) => referenceHint(choices, key);
  return (
    <div className="space-y-space-3 border-t border-subtle pt-space-3" data-management>
      <p className="text-t14 font-medium text-fg">Trade management (optional)</p>
      <div>
        <label htmlFor={`${id}-trail`} className={LABEL}>Trail the stop</label>
        <Combobox id={`${id}-trail`} label="Trail the stop" value={pick.trail} className="w-full"
          onChange={(v) => onChange({ trail: v as Trail | "" })}
          options={[{ value: "", label: "No trail" }, ...choices.trails]} />
      </div>
      {pick.trail === "SWING" && (
        <NumberField id={`${id}-deg`} label="Swing size (0 smallest to 3 largest)" value={pick.degree} hint={ref("trail_degree")} bad={bad}
          onChange={(v) => onChange({ degree: v })} />
      )}
      {pick.trail === "ATR" && (
        <div className="grid gap-space-3 sm:grid-cols-2">
          <NumberField id={`${id}-atrn`} label="ATR bars" value={pick.trailN} hint={ref("trail_n_atr")} bad={bad} onChange={(v) => onChange({ trailN: v })} />
          <NumberField id={`${id}-atrk`} label="Average true ranges" value={pick.trailK} hint={ref("trail_k")} bad={bad} onChange={(v) => onChange({ trailK: v })} />
        </div>
      )}
      {pick.trail === "MA" && (
        <div className="grid gap-space-3 sm:grid-cols-2">
          <div>
            <label htmlFor={`${id}-ma`} className={LABEL}>Average</label>
            <Combobox id={`${id}-ma`} label="Average" value={pick.average} className="w-full" placeholder="Pick one"
              onChange={(v) => onChange({ average: v })} options={choices.averages.map((a) => ({ value: a, label: a }))} />
          </div>
          <NumberField id={`${id}-man`} label="Average bars" value={pick.trailN} hint={ref("trail_n_ma")} bad={bad} onChange={(v) => onChange({ trailN: v })} />
        </div>
      )}
      <label className={CHECK}>
        <input type="checkbox" checked={pick.stall} onChange={(e) => onChange({ stall: e.target.checked })} />
        Exit when no new high (low for a downward idea) comes
      </label>
      {pick.stall && (
        <NumberField id={`${id}-stall`} label="Bars without a new high or low" value={pick.stallBars} hint={ref("stall_bars")} bad={bad}
          onChange={(v) => onChange({ stallBars: v })} />
      )}
      <label className={CHECK}>
        <input type="checkbox" checked={pick.maxBars} onChange={(e) => onChange({ maxBars: e.target.checked })} />
        Exit after a number of bars in the trade
      </label>
      {pick.maxBars && (
        <NumberField id={`${id}-bars`} label="Bars in the trade" value={pick.maxBarsN} hint="" bad={bad} onChange={(v) => onChange({ maxBarsN: v })} />
      )}
      <Details id="paperbot.management">
        <ul className="list-disc space-y-space-1 pl-space-4">
          {MANAGEMENT_NOTES.map((n) => <li key={n}>{n}</li>)}
        </ul>
      </Details>
    </div>
  );
}
