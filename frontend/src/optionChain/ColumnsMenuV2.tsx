import { Columns3 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { ColumnPrefs } from "./model";

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
          {CHOICES.map((c) => (
            <label key={c.key} className={`flex min-h-11 cursor-pointer items-center gap-space-3 rounded-control px-space-2 text-t14 text-fg hover:bg-surface-2 ${c.phoneOnly ? "md:hidden" : ""}`}>
              <input type="checkbox" checked={prefs[c.key] === true} onChange={(e) => onChange({ ...prefs, [c.key]: e.target.checked })}
                className="h-4 w-4 accent-brand" />
              {c.label}
            </label>
          ))}
        </fieldset>
      )}
    </div>
  );
}
