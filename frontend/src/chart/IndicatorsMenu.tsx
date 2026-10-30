import { Activity, Check, Search } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { cx } from "../components/primitives/cx";
import { TermHint } from "../ui/TermHint";
import { isOn, menuGroups, onCount, type IndicatorState, type MenuGroup } from "./indicatorsModel";

/**
 * UI2-4b2 (UI v2 proposal §6): the Indicators button and its searchable menu - "On this chart" (the volume and RSI
 * panes), "Popular" (price lines, each with a glossary "?") and "Layers" (only those whose feature is on). Each row is a
 * switch; the choice applies at once and is remembered (the panes in the trader's chart settings on the server).
 * Escape, a click outside or the button again closes it.
 * CT-1 C1 (the one ChartToolbar): `groups` limits the groups offered (an embedded chart: the price-pane lines only);
 * `iconOnly` shows the icon and the count (a phone's one-line bar, a compact bar); `spread` opens the menu across the
 * whole bar (the bar is the positioned box), so it never runs off a narrow screen.
 */
export function IndicatorsMenu({ state, on, onToggle, groups: only, iconOnly = false, spread = false }: {
  state: IndicatorState; on: (flag: string) => boolean; onToggle: (id: string) => void;
  groups?: readonly MenuGroup[]; iconOnly?: boolean; spread?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    // Escape closes and gives the focus back to the button (WAI-ARIA dialog pattern).
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  useEffect(() => { if (!open) setQuery(""); }, [open]);

  const groups = menuGroups(query, on, only);
  const count = onCount(state, on, only);
  return (
    <div ref={ref} className={cx("inline-flex", !spread && "relative")}>
      <button ref={trigger} type="button" aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}
        aria-label={iconOnly ? `Indicators${count > 0 ? ` · ${count} on` : ""}` : undefined} title={iconOnly ? "Indicators" : undefined}
        className={cx("inline-flex min-h-11 items-center gap-space-1 rounded-control text-t14 font-medium text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9",
          iconOnly ? "min-w-11 justify-center px-space-1 md:min-w-9" : "px-space-2")}>
        <Activity size={16} aria-hidden className="text-fg-muted" />
        {iconOnly
          ? count > 0 && <span aria-hidden className="text-t12 tabular-nums text-fg-muted">{count}</span>
          : <>Indicators{count > 0 && <span className="hidden text-fg-muted sm:inline">· {count}</span>}</>}
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="Indicators"
          className={cx("absolute top-full z-30 mt-space-1 rounded-panel border border-subtle bg-surface-1 p-space-3 text-t14 text-fg shadow-raised",
            spread ? "inset-x-space-2" : "left-0 w-80 max-w-[calc(100vw-2rem)]")}>
          <div className="relative">
            <Search size={14} aria-hidden className="pointer-events-none absolute left-space-3 top-1/2 -translate-y-1/2 text-fg-muted" />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search indicators" aria-label="Search indicators"
              className="h-11 w-full rounded-control border border-border bg-surface-2 pl-space-8 pr-space-3 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
          </div>
          <div className={cx("mt-space-2 space-y-space-3 overflow-y-auto", spread ? "max-h-56" : "max-h-96")}>
            {groups.length === 0 && <p className="px-space-2 py-space-3 text-fg-muted">No indicator matches "{query.trim()}".</p>}
            {groups.map((g) => (
              <section key={g.group} aria-label={g.title}>
                <p className="px-space-2 pb-space-1 text-t12 font-medium text-fg-muted">{g.title}</p>
                <ul>
                  {g.items.map((item) => {
                    const checked = isOn(item.id, state);
                    return (
                      <li key={item.id} className="flex items-center gap-space-1">
                        <button type="button" role="switch" aria-checked={checked} onClick={() => onToggle(item.id)}
                          className="flex min-h-11 min-w-0 flex-1 items-center gap-space-2 rounded-control px-space-2 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
                          <span aria-hidden className={cx("inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                            checked ? "border-brand bg-brand text-on-brand" : "border-border")}>
                            {checked && <Check size={12} />}
                          </span>
                          <span className="min-w-0">
                            <span className="block">{item.label}</span>
                            {item.note && <span className="block text-t12 text-fg-muted">{item.note}</span>}
                          </span>
                        </button>
                        {item.term && <span className="px-space-2"><TermHint term={item.term} align="end" /></span>}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
