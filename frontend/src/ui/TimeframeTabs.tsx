import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { SubTabs, rowMode, subTabClass } from "./SubTabs";

/**
 * UI-H Wave 0, W0-1 (owner, 24 Oct): the timeframe row - ONE row of the most-used timeframes plus a "More" menu for the
 * rest. The picked timeframe is always visible: when it comes from More, the More button shows its name (filled, as the
 * active tab) instead of "More". On a phone the row uses the short names ("1m 5m 15m 1H D"); the full name stays the
 * accessible name. Built on SubTabs (toggle kind: a group of aria-pressed buttons named "Timeframe").
 */
export interface TimeframeOption<K extends string> { key: K; label: string; short?: string }

/** The timeframes shown in the row when offered; the rest go under More. */
export const MAIN_TIMEFRAMES = ["1min", "5min", "15min", "60min", "day"] as const;

/** The row's and the menu's options (exported for tests). */
export function splitTimeframes<K extends string>(options: readonly TimeframeOption<K>[], main: readonly string[] = MAIN_TIMEFRAMES) {
  const row = options.filter((o) => main.includes(o.key));
  const more = options.filter((o) => !main.includes(o.key));
  return more.length === 1 ? { row: options, more: [] as TimeframeOption<K>[] } : { row, more };
}

export function TimeframeTabs<K extends string>({ options, value, onChange, size = "sm", className }: {
  options: readonly TimeframeOption<K>[]; value: K; onChange: (key: K) => void; size?: "md" | "sm"; className?: string;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  const { row, more } = splitTimeframes(options);
  const picked = more.find((o) => o.key === value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);

  const mode = rowMode(row.length + (more.length ? 1 : 0));
  const trailing = more.length > 0 && (
    <span className={mode === "segments" ? "flex min-w-0 flex-1 basis-0" : "flex shrink-0"} data-timeframe-more>
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
        aria-pressed={picked ? true : undefined} aria-label={picked ? picked.label : "More timeframes"}
        onClick={() => setOpen((o) => !o)} data-subtab={picked?.key ?? "more"} data-active={picked ? "true" : undefined}
        className={`${subTabClass(!!picked, "segments", size)} w-full`}>
        {picked ? (phone && picked.short ? picked.short : picked.label) : "More"}
        <ChevronDown size={14} aria-hidden />
      </button>
    </span>
  );

  return (
    <div ref={ref} className={`relative ${className ?? ""}`}>
      <SubTabs kind="toggle" label="Timeframe" size={size} value={value} onChange={onChange} trailing={trailing}
        items={row.map((o) => ({ key: o.key, label: phone && o.short ? o.short : o.label, ariaLabel: phone && o.short ? o.label : undefined }))} />
      {open && (
        <div id={menuId} role="menu" aria-label="More timeframes" data-timeframe-menu
          className="absolute right-0 top-full z-30 mt-space-1 min-w-40 rounded-panel border border-subtle bg-surface-1 p-space-1 shadow-raised">
          {more.map((o) => (
            <button key={o.key} type="button" role="menuitemradio" aria-checked={o.key === value}
              onClick={() => { onChange(o.key); setOpen(false); }}
              className="flex min-h-11 w-full items-center justify-between gap-space-3 rounded-control px-space-3 text-left text-t14 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
              {o.label}
              {o.key === value && <Check size={16} aria-hidden className="text-brand" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
