import { Check, ChevronDown, Clock, Plus, Star } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  INTERVAL_UNITS, groupTimeframes, parseCustomInterval, shownFavourites, timeframeLabel, timeframeShort, toggleFavourite,
  type IntervalUnit, type TimeframeId,
} from "../chart/timeframes";
import { cx } from "../components/primitives/cx";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { SubTabs } from "./SubTabs";

/**
 * TF-1 (owner, 25 Oct; research R20): the ONE timeframe control of every chart - the Chart tab, Multi-chart, the
 * Terminal and every embedded chart. One dropdown with every timeframe the chart offers, grouped (Minutes / Hours /
 * Days / Weeks / Months), a star beside each, and "Add custom interval" at its foot. The starred timeframes show as
 * one-click buttons beside the dropdown (default 5m, 15m, 1H, D; saved per user in the `chart` prefs). A phone shows
 * the dropdown and at most three favourites. Replaces the W0-1 row + "More" (TimeframeTabs).
 *
 * Keys in the list: Up / Down / Home / End move between timeframes (roving focus), Right moves to that row's star and
 * Left back, Enter / Space picks, Escape closes. With `typeToOpen`, a digit typed while nothing editable has focus
 * opens the list with that digit in the custom interval box.
 */
export interface TimeframePickerProps {
  offered: readonly TimeframeId[];
  value: TimeframeId;
  onChange: (tf: TimeframeId) => void;
  favourites: readonly TimeframeId[];
  onFavourites: (next: TimeframeId[]) => void;
  /** At most three favourites (a phone, or a small chart cell such as a Multi-chart tile). */
  compact?: boolean;
  /** The accessible name ("Timeframe", "Timeframe of chart 2"). */
  label?: string;
  /** A digit typed outside a field opens the custom interval box (one picker per page: the Chart tab). */
  typeToOpen?: boolean;
  className?: string;
  /** At most this many favourite buttons (a Multi-chart cell on a phone shows the dropdown alone, to stay one line). */
  maxFavourites?: number;
  /** Start with the list open (a static render: tests, the comparison page). */
  defaultOpen?: boolean;
}

const ITEM = "flex min-h-11 min-w-0 flex-1 items-center justify-between gap-space-3 rounded-control px-space-3 text-left text-t14 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";
const STAR = "inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9 md:min-w-9";
const FIELD = "h-11 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9";

/** Where a key moves the focus inside the open list: `{ to: index, star }` (the timeframe at `index`, or its star), or
 * null when the key does nothing here. `at` is the focused row, `onStar` whether its star has the focus. Pure. */
export function menuMove(count: number, at: number, onStar: boolean, key: string): { to: number; star: boolean } | null {
  if (at < 0 || count === 0) return null;
  if (key === "ArrowDown") return { to: (at + 1) % count, star: false };
  if (key === "ArrowUp") return { to: (at - 1 + count) % count, star: false };
  if (key === "Home") return { to: 0, star: false };
  if (key === "End") return { to: count - 1, star: false };
  if (key === "ArrowRight" && !onStar) return { to: at, star: true };
  if (key === "ArrowLeft" && onStar) return { to: at, star: false };
  return null;
}

/** A digit typed outside a field (no modifier) opens the custom interval box with it (`typeToOpen`). Pure. */
export function opensCustomBox(key: string, modifier: boolean, inField: boolean): boolean {
  return /^\d$/.test(key) && !modifier && !inField;
}

/** "Add custom interval": the favourites after an interval is added (an added interval is starred). Pure. */
export function favouritesAfterAdd(favourites: readonly TimeframeId[], id: TimeframeId): TimeframeId[] {
  return favourites.includes(id) ? [...favourites] : toggleFavourite(favourites, id);
}

function editable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable;
}

export function TimeframePicker({
  offered, value, onChange, favourites, onFavourites, compact = false, label = "Timeframe", typeToOpen = false, className, defaultOpen = false, maxFavourites,
}: TimeframePickerProps) {
  const phone = useMediaQuery(PHONE_QUERY);
  const few = compact || phone;
  const [open, setOpen] = useState(defaultOpen);
  const [custom, setCustom] = useState<{ open: boolean; value: string; unit: IntervalUnit; error: string | null }>(
    { open: false, value: "", unit: "minutes", error: null });
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const customRef = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const groups = groupTimeframes(offered);
  const order = groups.flatMap((g) => g.ids);
  const shown = shownFavourites(favourites, offered, few).slice(0, maxFavourites ?? Infinity);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);

  // Focus the picked timeframe when the list opens (or the custom box when a digit opened it).
  useEffect(() => {
    if (!open) return;
    if (custom.open) { customRef.current?.focus(); return; }
    menuRef.current?.querySelector<HTMLElement>(`[data-timeframe="${value}"]`)?.focus();
  }, [open]); // only when it opens: a later change of the picked timeframe must not move the focus

  useEffect(() => {
    if (!typeToOpen) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (!opensCustomBox(e.key, e.ctrlKey || e.metaKey || e.altKey, editable(e.target))) return;
      e.preventDefault();
      setCustom({ open: true, value: e.key, unit: "minutes", error: null });
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [typeToOpen]);

  const pick = (tf: TimeframeId) => { onChange(tf); setOpen(false); };

  const onMenuKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const tf = target.dataset.timeframe as TimeframeId | undefined;
    const starOf = target.dataset.star as TimeframeId | undefined;
    const move = menuMove(order.length, order.indexOf((tf ?? starOf) as TimeframeId), !!starOf, e.key);
    if (!move) return;
    e.preventDefault();
    menuRef.current?.querySelector<HTMLElement>(`[data-${move.star ? "star" : "timeframe"}="${order[move.to]}"]`)?.focus();
  };

  const addCustom = () => {
    const r = parseCustomInterval(custom.value, custom.unit, offered);
    if (!r.ok) { setCustom((c) => ({ ...c, error: r.message })); return; }
    if (!favourites.includes(r.id)) onFavourites(favouritesAfterAdd(favourites, r.id));
    setCustom({ open: false, value: "", unit: "minutes", error: null });
    pick(r.id);
  };

  const trailing = (
    <span className="flex shrink-0" data-timeframe-dropdown>
      <button type="button" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
        aria-label={`${label}: ${timeframeLabel(value)}`} onClick={() => setOpen((o) => !o)}
        data-active={shown.includes(value) ? undefined : "true"}
        className={cx("inline-flex min-h-11 items-center justify-center gap-space-1 whitespace-nowrap rounded-control px-space-3 text-t14 md:min-h-8 md:px-space-2",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
          shown.includes(value) ? "font-medium text-fg-muted hover:bg-surface-1 hover:text-fg" : "bg-brand font-semibold text-on-brand shadow-card")}>
        {/* TF-1 fix 1: the current interval when it is not a starred button, else a timeframe icon + "▾" - never "More". */}
        {shown.includes(value) ? <Clock size={16} aria-hidden data-timeframe-icon /> : (phone ? timeframeShort(value) : timeframeLabel(value))}
        <ChevronDown size={14} aria-hidden />
      </button>
    </span>
  );

  return (
    <div ref={ref} className={cx("relative", className)} data-timeframe-picker>
      <SubTabs kind="toggle" label={label} size="sm" layout="scroll" value={shown.includes(value) ? value : null} onChange={pick} trailing={trailing}
        items={shown.map((tf) => ({ key: tf, label: timeframeShort(tf), ariaLabel: timeframeLabel(tf), data: { favourite: "true" } }))} />
      {open && phone && <div aria-hidden data-timeframe-backdrop onClick={() => setOpen(false)} className="fixed inset-0 z-30 bg-scrim/50" />}
      {open && (
        // TF-1 fix 2: on a phone the list is a bottom sheet above the bottom nav (4rem + the safe area) that scrolls inside.
        <div id={menuId} ref={menuRef} role="menu" aria-label="Timeframes" data-timeframe-menu data-sheet={phone || undefined} onKeyDown={onMenuKey}
          className={cx("overflow-y-auto overscroll-contain border-subtle bg-surface-1 p-space-1 shadow-raised",
            phone
              ? "fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-40 max-h-[60vh] rounded-t-panel border-t"
              : "absolute right-0 top-full z-30 mt-space-1 max-h-[60vh] w-64 max-w-[calc(100vw-2rem)] rounded-panel border")}>
          {groups.map((g) => (
            <div key={g.label} role="group" aria-label={g.label} data-timeframe-group={g.label}>
              <div aria-hidden className="px-space-3 pb-space-1 pt-space-2 text-t12 font-semibold uppercase text-fg-muted">{g.label}</div>
              {g.ids.map((tf) => {
                const starred = favourites.includes(tf);
                return (
                  <div key={tf} role="none" className="flex items-center gap-space-1">
                    <button type="button" role="menuitemradio" aria-checked={tf === value} tabIndex={tf === value ? 0 : -1}
                      data-timeframe={tf} onClick={() => pick(tf)} className={ITEM}>
                      {timeframeLabel(tf)}
                      {tf === value && <Check size={16} aria-hidden className="text-brand" />}
                    </button>
                    <button type="button" aria-pressed={starred} tabIndex={-1} data-star={tf}
                      aria-label={starred ? `Remove ${timeframeLabel(tf)} from favourites` : `Add ${timeframeLabel(tf)} to favourites`}
                      title={starred ? "Remove from favourites" : "Add to favourites"}
                      onClick={() => onFavourites(toggleFavourite(favourites, tf))} className={STAR}>
                      <Star size={16} aria-hidden className={starred ? "fill-current text-brand" : undefined} />
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
          <div className="mt-space-1 border-t border-subtle pt-space-1" data-timeframe-custom>
            {!custom.open ? (
              <button type="button" role="menuitem" onClick={() => setCustom((c) => ({ ...c, open: true }))}
                className={cx(ITEM, "justify-start text-brand")}>
                <Plus size={16} aria-hidden /> Add custom interval
              </button>
            ) : (
              <form className="space-y-space-2 p-space-2" onSubmit={(e) => { e.preventDefault(); addCustom(); }}>
                <div className="flex items-center gap-space-2">
                  <label className="sr-only" htmlFor={`${menuId}-n`}>Interval length</label>
                  <input ref={customRef} id={`${menuId}-n`} inputMode="numeric" autoComplete="off" value={custom.value}
                    onChange={(e) => setCustom((c) => ({ ...c, value: e.target.value, error: null }))} className={cx(FIELD, "w-16")} />
                  <label className="sr-only" htmlFor={`${menuId}-u`}>Interval unit</label>
                  <select id={`${menuId}-u`} value={custom.unit} onChange={(e) => setCustom((c) => ({ ...c, unit: e.target.value as IntervalUnit, error: null }))}
                    className={cx(FIELD, "min-w-0 flex-1")}>
                    {INTERVAL_UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
                  </select>
                  <button type="submit" className="inline-flex min-h-11 items-center rounded-control bg-brand px-space-3 text-t14 font-semibold text-on-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">Add</button>
                </div>
                {custom.error && <p role="alert" className="text-t13 text-fg">{custom.error}</p>}
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
