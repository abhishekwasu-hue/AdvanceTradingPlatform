import { useState } from "react";
import type { OiAlertSettings, OiStrikeRule } from "../oi/types";
import { Combobox } from "../ui/Combobox";
import { formatCount } from "../ui/format";

/**
 * M96 N1: the user's own OI threshold alerts, inside the OI alerts card (same channels: in-app and Telegram through
 * Settings → Alerts; same cooldown, quiet hours and snooze).
 * - "OI change at a strike": a strike, call or put, and his level (in lakhs, as the chain shows OI) - one alert when that
 *   strike's OI change since the day's first reading reaches it.
 * - "PCR crossed my level": one alert each time the PCR (OI) crosses his level, up or down.
 * Both OFF and empty until he sets them: no strike, side or level is pre-filled.
 */
const LAKH = 1e5;
const MAX_RULES = 20;

/** A rule from the three typed fields; null while any is missing or not a positive number. Pure. */
export function ruleOf(strike: string, side: string, lakhs: string): OiStrikeRule | null {
  const k = Number(strike.replace(/,/g, "")), l = Number(lakhs);
  if (!strike.trim() || !lakhs.trim() || !Number.isFinite(k) || !Number.isFinite(l) || k <= 0 || l <= 0) return null;
  if (side !== "CE" && side !== "PE") return null;
  return { strike: k, side, threshold: Math.round(l * LAKH) };
}

/** The types list with `type` on or off (the other ticks kept). Pure. */
export function withType(types: readonly string[], type: string, on: boolean): string[] {
  const rest = types.filter((t) => t !== type);
  return on ? [...rest, type] : rest;
}

export default function OiThresholdAlertsV2({ alerts, onSave }: { alerts: OiAlertSettings; onSave: (patch: Partial<OiAlertSettings>) => void }) {
  const types = alerts.types ?? [];
  const rules = alerts.strike_rules ?? [];
  const [strike, setStrike] = useState("");
  const [side, setSide] = useState("");
  const [lakhs, setLakhs] = useState("");
  const [pcr, setPcr] = useState<string | null>(null);
  const draft = ruleOf(strike, side, lakhs);
  const field = "mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1";
  return (
    <div className="space-y-2 rounded border border-border p-2" data-oi-threshold-alerts>
      <div className="font-medium text-fg">My OI levels</div>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={types.includes("STRIKE_OI_CHANGE")} data-oi-threshold-type="STRIKE_OI_CHANGE"
               onChange={(e) => onSave({ types: withType(types, "STRIKE_OI_CHANGE", e.target.checked) })} />
        <span>OI change at a strike reaches my level</span>
      </label>
      {rules.length > 0 && (
        <ul className="space-y-0.5 tabular-nums" data-oi-threshold-rules>
          {rules.map((r, i) => (
            <li key={`${r.side}-${r.strike}-${i}`} className="flex items-center gap-2">
              <span>{r.strike.toLocaleString("en-IN")} {r.side === "CE" ? "call" : "put"}: change ≥ {formatCount(r.threshold)}</span>
              <button type="button" className="rounded border border-border px-1.5 hover:border-brand"
                      onClick={() => onSave({ strike_rules: rules.filter((_, j) => j !== i) })}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {rules.length < MAX_RULES && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <label className="block"><span className="text-fg-muted">Strike</span>
            <input className={field} inputMode="decimal" value={strike} onChange={(e) => setStrike(e.target.value)} data-oi-threshold-strike /></label>
          <label className="block"><span className="text-fg-muted">Side</span>
            <Combobox label="Side" value={side} onChange={setSide} placeholder="Pick" className="mt-0.5 w-full" dataAttrs={{ "data-oi-threshold-side": "" }}
              options={[{ value: "CE", label: "Call (CE)" }, { value: "PE", label: "Put (PE)" }]} /></label>
          <label className="block"><span className="text-fg-muted">OI change at least (lakhs)</span>
            <input className={field} inputMode="decimal" value={lakhs} onChange={(e) => setLakhs(e.target.value)} data-oi-threshold-level /></label>
          <div className="flex items-end">
            <button type="button" disabled={!draft} className="rounded border border-border px-2 py-1 hover:border-brand disabled:opacity-50" data-oi-threshold-add
                    onClick={() => { if (draft) { onSave({ strike_rules: [...rules, draft] }); setStrike(""); setSide(""); setLakhs(""); } }}>Add</button>
          </div>
        </div>
      )}
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={types.includes("PCR_CROSS")} data-oi-threshold-type="PCR_CROSS"
               onChange={(e) => onSave({ types: withType(types, "PCR_CROSS", e.target.checked) })} />
        <span>PCR crosses my level</span>
      </label>
      <label className="block max-w-[12rem]"><span className="text-fg-muted">My PCR level</span>
        <input className={field} inputMode="decimal" value={pcr ?? (alerts.pcr_level != null ? String(alerts.pcr_level) : "")} data-oi-threshold-pcr
               onChange={(e) => setPcr(e.target.value)}
               onBlur={() => {
                 if (pcr === null) return;
                 const v = Number(pcr);
                 onSave({ pcr_level: pcr.trim() && Number.isFinite(v) && v > 0 ? v : null });
                 setPcr(null);
               }} /></label>
      <p className="text-fg-muted">Each alert comes once when the number crosses your level.</p>
    </div>
  );
}
