import { Columns3 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
import type { ColumnPrefs, OiUnits } from "./model";

/**
 * OC1: "Columns" - the optional columns of the chain, off by default: the Greeks, bid and ask, and (a phone only) OI
 * change, IV and volume next to OI and LTP; FNO-1: OI change as a count or in %. The choice is kept on this browser. Escape or a tap elsewhere closes it.
 */
const CHOICES: { key: keyof ColumnPrefs; label: string; phoneOnly?: boolean }[] = [
  { key: "more", label: "OI change, IV and volume", phoneOnly: true },
  { key: "greeks", label: "Greeks (delta, gamma, theta, vega)" },
  { key: "quotes", label: "Bid and ask" },
  { key: "pct", label: "OI change in %" },
];
/** M63 I-7: the (?) beside a choice that names a trading term. "OI change" is marked on the first row a screen shows it
 *  in: the phone-only row on a phone, the "in %" row from the md breakpoint (`from: "md"`). */
const CHOICE_TERMS: Partial<Record<keyof ColumnPrefs, { term: GlossaryKey; from?: "md" }>> = {
  more: { term: "oi-change" }, greeks: { term: "greeks" }, pct: { term: "oi-change", from: "md" },
};

/** M96 Q3: the unit of OI, OI change and volume on the chain (kept in the same column choice). */
const UNITS: { key: OiUnits; label: string }[] = [
  { key: "lakhs", label: "Lakhs (0.50 L)" },
  { key: "contracts", label: "Contracts (50,000)" },
];

export default function ColumnsMenuV2({ prefs, onChange }: { prefs: ColumnPrefs; onChange: (prefs: ColumnPrefs) => void }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  return (
    <div ref={box} className="relative">
      <button type="button" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}
        className="inline-flex h-11 min-w-11 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9">
        <Columns3 size={16} aria-hidden /><span className="sr-only sm:not-sr-only">Columns</span>
      </button>
      {open && (
        <fieldset id={id} className="absolute right-0 top-full z-30 mt-space-1 w-72 max-w-[calc(100vw-2rem)] rounded-panel border border-subtle bg-surface-1 p-space-2 shadow-raised">
          <legend className="sr-only">Columns to show</legend>
          {CHOICES.map((c) => {
            const t = CHOICE_TERMS[c.key];
            return (
              <div key={c.key} className={`flex items-center gap-space-2 rounded-control pr-space-2 hover:bg-surface-2 ${c.phoneOnly ? "md:hidden" : ""}`}>
                <label className="flex min-h-11 min-w-0 flex-1 cursor-pointer items-center gap-space-3 px-space-2 text-t14 text-fg">
                  <input type="checkbox" checked={prefs[c.key] === true} onChange={(e) => onChange({ ...prefs, [c.key]: e.target.checked })}
                    className="h-4 w-4 accent-brand" />
                  {c.label}
                </label>
                {t && <span className={t.from === "md" ? "hidden md:inline-flex" : "inline-flex"}><TermHint term={t.term} align="end" /></span>}
              </div>
            );
          })}
          <div role="radiogroup" aria-label="OI and volume in" data-units className="mt-space-1 border-t border-subtle pt-space-1">
            <p className="px-space-2 py-space-1 text-t12 font-medium text-fg-muted">OI and volume in</p>
            {UNITS.map((u) => (
              <label key={u.key} className="flex min-h-11 cursor-pointer items-center gap-space-3 rounded-control px-space-2 text-t14 text-fg hover:bg-surface-2">
                <input type="radio" name={`${id}-units`} value={u.key} checked={(prefs.units ?? "lakhs") === u.key} data-unit={u.key}
                  onChange={() => onChange({ ...prefs, units: u.key })} className="h-4 w-4 accent-brand" />
                {u.label}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </div>
  );
}
