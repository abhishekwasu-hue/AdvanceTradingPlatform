import { Check, ChevronDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Sheet } from "../components/primitives/Dialog";
import { cx } from "../components/primitives/cx";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { useUiV2 } from "./useFeatures";

/**
 * UX-5 (owner order UX-2, 25 Oct): the one shared picker that replaces the browser's own <select> (Underlying, Layout,
 * Symbol, Expiry, chart type, filters). The APG "select-only combobox" pattern: focus stays on the button, the
 * highlighted row moves with aria-activedescendant; arrows only browse, Enter / Space / Tab pick, Escape keeps the old
 * value, typing letters jumps to the first match. A long list (`searchable`) gets a filter box (type to filter).
 * Rows may be grouped ("Indices", "Stocks"); `recent` values are listed first under "Recent". Desktop: a list under the
 * button; phone: a bottom sheet with 44 px rows. Neutral tokens only.
 */
export interface ComboOption {
  value: string;
  label: string;
  /** The group heading the row sits under ("Indices"); rows keep the order given within a group. */
  group?: string;
  /** A short second line on the right ("Monthly"). */
  hint?: string;
  disabled?: boolean;
}

export interface ComboboxProps {
  /** The visible label is the caller's; this names the control for screen readers when there is none. */
  label: string;
  value: string;
  options: readonly ComboOption[];
  onChange: (value: string) => void;
  id?: string;
  /** A filter box above the rows (long lists: symbols, expiries). */
  searchable?: boolean;
  /** Values to list first under "Recent" (most recent first). */
  recent?: readonly string[];
  /** Shown on the button when no option matches `value`. */
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** Extra attributes for tests and tours (`data-terminal="underlying"`). */
  dataAttrs?: Record<string, string>;
  /** The phone sheet's heading (defaults to `label`). */
  sheetTitle?: ReactNode;
}

export const RECENT_GROUP = "Recent";
const NO_RECENT: readonly string[] = [];

/** The rows to show for the filter text: recent first (when not filtering), then the options; disabled rows kept. Pure. */
export function comboRows(options: readonly ComboOption[], filter: string, recent: readonly string[] = []): ComboOption[] {
  const q = filter.trim().toLowerCase();
  if (q) {
    const hit = (o: ComboOption) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q);
    const starts = options.filter((o) => hit(o) && o.label.toLowerCase().startsWith(q));
    return [...starts, ...options.filter((o) => hit(o) && !o.label.toLowerCase().startsWith(q))];
  }
  const byValue = new Map(options.map((o) => [o.value, o]));
  const top = recent.map((v) => byValue.get(v)).filter((o): o is ComboOption => !!o).map((o) => ({ ...o, group: RECENT_GROUP }));
  return [...top, ...options];
}

/** The next enabled row from `from` in `delta` steps (wraps); -1 when none is enabled. Pure. */
export function stepRow(rows: readonly ComboOption[], from: number, delta: 1 | -1): number {
  const n = rows.length;
  for (let k = 1; k <= n; k++) {
    const i = (((from + delta * k) % n) + n) % n;
    if (!rows[i].disabled) return i;
  }
  return -1;
}

/** Type-ahead: the first enabled row after `from` whose label starts with `typed` (then from the top). Pure. */
export function typeAhead(rows: readonly ComboOption[], typed: string, from: number): number {
  const t = typed.toLowerCase();
  const n = rows.length;
  for (let k = 1; k <= n; k++) {
    const i = (from + k) % n;
    if (!rows[i].disabled && rows[i].label.toLowerCase().startsWith(t)) return i;
  }
  return -1;
}

const triggerClass = "inline-flex h-11 min-w-0 items-center justify-between gap-space-2 rounded-control border border-border bg-surface-2 px-space-3 text-left text-t14 text-fg transition-colors duration-fast hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:cursor-not-allowed disabled:opacity-60 md:h-9";

export function Combobox({ label, value, options, onChange, id, searchable = false, recent = NO_RECENT, placeholder = "Choose…", disabled = false,
  className = "", dataAttrs = {}, sheetTitle }: ComboboxProps) {
  const phone = useMediaQuery(PHONE_QUERY);
  const autoId = useId();
  const baseId = id ?? `combo-${autoId}`;
  const listId = `${baseId}-list`;
  const optionId = (i: number) => `${baseId}-opt-${i}`;
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(-1);
  const typed = useRef({ text: "", at: 0 });
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const filterBox = useRef<HTMLInputElement>(null);

  const rows = useMemo(() => comboRows(options, filter, recent), [options, filter, recent]);
  const current = options.find((o) => o.value === value);

  const openList = () => {
    if (disabled) return;
    setFilter("");
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };
  const choose = (i: number) => {
    const row = rows[i];
    if (!row || row.disabled) return;
    close();
    if (row.value !== value) onChange(row.value);
  };

  // On open, highlight the current value (or the first enabled row); after filtering, the first match. Only then: the
  // callers build `options` anew on every render, and re-running this on each one would undo every arrow key.
  const rowsNow = useRef(rows);
  rowsNow.current = rows;
  useEffect(() => {
    if (!open) return;
    const list = rowsNow.current;
    const at = filter ? list.findIndex((r) => !r.disabled) : list.findIndex((r) => r.value === value && r.group !== RECENT_GROUP);
    setActive(at >= 0 ? at : stepRow(list, -1, 1));
  }, [open, filter]);                                                    // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (open && searchable) filterBox.current?.focus(); }, [open, searchable]);
  useEffect(() => { if (open && active >= 0) document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" }); });
  // Desktop: a click outside closes the list without a change.
  useEffect(() => {
    if (!open || phone) return undefined;
    const onDown = (e: PointerEvent) => { if (root.current && !root.current.contains(e.target as Node)) close(false); };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open, phone]);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (disabled) return;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openList(); }
      else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && !searchable) {
        const at = typeAhead(rows, e.key, rows.findIndex((r) => r.value === value));
        if (at >= 0) { setOpen(true); setActive(at); }
      }
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setActive((a) => stepRow(rows, a, e.key === "ArrowDown" ? 1 : -1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(stepRow(rows, -1, 1)); }
    else if (e.key === "End") { e.preventDefault(); setActive(stepRow(rows, 0, -1)); }
    else if (e.key === "Enter" || (e.key === " " && !searchable)) { e.preventDefault(); if (active >= 0) choose(active); }
    else if (e.key === "Tab") { if (active >= 0 && rows[active]?.value !== value) choose(active); else close(false); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key.length === 1 && !searchable && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const now = Date.now();
      typed.current = { text: now - typed.current.at < 700 ? typed.current.text + e.key : e.key, at: now };
      const at = typeAhead(rows, typed.current.text, typed.current.text.length > 1 ? active - 1 : active);
      if (at >= 0) setActive(at);
    }
  };

  const list = (
    <ul id={listId} role="listbox" aria-label={label} data-combobox-list
      className={phone ? "-mx-space-2 space-y-px" : "max-h-72 overflow-y-auto p-space-1"}>
      {rows.length === 0 && <li role="presentation" className="px-space-3 py-space-3 text-t14 text-fg-muted">No match for &quot;{filter.trim()}&quot;.</li>}
      {rows.map((o, i) => {
        const head = o.group && o.group !== rows[i - 1]?.group;
        const selected = o.value === value && o.group !== RECENT_GROUP;
        return [
          head ? <li key={`g:${o.group}:${i}`} role="presentation" className="px-space-3 pb-space-1 pt-space-2 text-t12 font-semibold text-fg-muted">{o.group}</li> : null,
          <li key={`${o.group ?? ""}:${o.value}`} id={optionId(i)} role="option" aria-selected={selected} aria-disabled={o.disabled || undefined}
            data-value={o.value} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(i)} onMouseMove={() => { if (i !== active && !o.disabled) setActive(i); }}
            className={cx("flex min-h-11 cursor-pointer items-center gap-space-2 rounded-control px-space-3 text-t14 md:min-h-9",
              i === active ? "bg-brand/10 text-fg" : "text-fg", o.disabled && "cursor-not-allowed opacity-50")}>
            <span className="min-w-0 flex-1 truncate tabular-nums">{o.label}</span>
            {o.hint && <span className="shrink-0 text-t12 text-fg-muted">{o.hint}</span>}
            {selected && <Check size={16} aria-hidden className="shrink-0 text-brand" />}
          </li>,
        ];
      })}
    </ul>
  );
  const filterInput = searchable && (
    <div className="flex items-center gap-space-2 border-b border-subtle px-space-3">
      <Search size={16} aria-hidden className="shrink-0 text-fg-muted" />
      <input ref={filterBox} type="text" value={filter} onChange={(e) => setFilter(e.target.value)} onKeyDown={onKeyDown}
        aria-label={`Filter ${label}`} aria-controls={listId} aria-activedescendant={active >= 0 ? optionId(active) : undefined}
        placeholder="Type to filter" autoComplete="off" spellCheck={false}
        className="h-11 min-w-0 flex-1 bg-transparent text-t14 text-fg placeholder:text-fg-muted focus:outline-none md:h-9" />
    </div>
  );

  return (
    <div ref={root} className={cx("relative inline-flex min-w-0", className)}>
      <button ref={button} id={baseId} type="button" role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls={listId}
        aria-label={label} aria-activedescendant={open && !searchable && active >= 0 ? optionId(active) : undefined}
        disabled={disabled} onClick={() => (open ? close() : openList())} onKeyDown={onKeyDown} data-combobox={value} {...dataAttrs}
        className={cx(triggerClass, "w-full")}>
        <span className={cx("min-w-0 flex-1 truncate tabular-nums", !current && "text-fg-muted")}>{current?.label ?? placeholder}</span>
        <ChevronDown size={16} aria-hidden className={cx("shrink-0 text-fg-muted transition-transform duration-fast", open && "rotate-180")} />
      </button>
      {open && !phone && (
        <div className="absolute left-0 top-full z-30 mt-space-1 w-max min-w-full max-w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-panel border border-subtle bg-surface-1 shadow-raised">
          {filterInput}
          {list}
        </div>
      )}
      {phone && (
        <Sheet side="bottom" open={open} onOpenChange={(o) => (o ? openList() : close())} title={sheetTitle ?? label}>
          <div onKeyDown={searchable ? undefined : onKeyDown} className="-mx-space-2">
            {filterInput}
            {list}
          </div>
        </Sheet>
      )}
    </div>
  );
}

/** For pages shared with today's UI (Performance, News): the combobox under `ui_v2`, today's native <select> otherwise. */
export function SelectOrCombobox({ nativeClassName, ...props }: ComboboxProps & { nativeClassName: string }) {
  const v2 = useUiV2();
  if (v2) return <Combobox {...props} />;
  return (
    <select id={props.id} aria-label={props.label} className={nativeClassName} value={props.value} disabled={props.disabled}
      onChange={(e) => props.onChange(e.target.value)}>
      {props.options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
    </select>
  );
}
