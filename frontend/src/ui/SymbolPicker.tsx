import { Search } from "lucide-react";
import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { request } from "../api/client";
import { cx } from "../components/primitives/cx";

/**
 * UI2-4b2 (UI v2 proposal §9 rule 6): the one symbol picker for v2 pages. Type to search the instrument list
 * (`GET /api/instrument-master/search`, indices and shares), or pick one of the recent symbols shown when the field is
 * empty. Each row shows the exchange. Enter takes the highlighted row of the list shown for the text as typed now,
 * or that text itself while the search for it has not answered yet (never a row left from an earlier text).
 * Recent symbols are kept per user on this browser (`storageKey`, the last eight; none while signed out).
 * Combobox pattern: arrow keys, Enter, Escape; leaving the field closes the list.
 */
export interface PickedSymbol { symbol: string; exchange: string; name?: string | null }

export const RECENT_KEY = "atp_recent_symbols";
const MAX_RECENT = 8;

/** This user's key for the recent list (null while signed out: nothing is read or kept). */
export const recentKeyFor = (userId: number | string | null | undefined): string | null => (userId == null ? null : `${RECENT_KEY}:${userId}`);

export function readRecent(key: string | null): PickedSymbol[] {
  if (!key) return [];
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((r): r is PickedSymbol => !!r && typeof r.symbol === "string" && typeof r.exchange === "string").slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

/** The recent list with `pick` first (no repeats). Pure. */
export function withRecent(list: readonly PickedSymbol[], pick: PickedSymbol): PickedSymbol[] {
  const same = (r: PickedSymbol) => r.symbol === pick.symbol && r.exchange === pick.exchange;
  return [pick, ...list.filter((r) => !same(r))].slice(0, MAX_RECENT);
}

export function rememberSymbol(key: string | null, pick: PickedSymbol): void {
  if (!key) return;
  try { localStorage.setItem(key, JSON.stringify(withRecent(readRecent(key), pick))); } catch { /* storage unavailable */ }
}

/** Which list belongs to the text: "" = the recent symbols (an empty field, or the symbol on screen), else the search
 * for that text. Rows are shown and taken only while they belong to the text as it is now. Pure. */
export function listKeyFor(text: string, value: string): string {
  const q = text.trim().toUpperCase();
  return !q || q === value.toUpperCase() ? "" : q;
}

/** What Enter opens: the highlighted row when the rows answer the text as typed now, else the text typed. The recent
 * list has no row highlighted until an arrow key is pressed (`active` -1), so Enter on the unchanged field opens
 * nothing new. Pure. */
export function enterChoice(s: { open: boolean; rows: readonly PickedSymbol[]; rowsFor: string | null; active: number; text: string;
                                 value: string; exchange: string }): PickedSymbol | null {
  const fresh = s.open && s.rowsFor === listKeyFor(s.text, s.value);
  const row = fresh && s.active >= 0 ? s.rows[s.active] : undefined;
  if (row) return row;
  if (!listKeyFor(s.text, s.value)) return null;                     // the field still shows the symbol on screen (or nothing)
  const q = s.text.trim().toUpperCase();
  return q ? { symbol: q, exchange: s.exchange } : null;
}

interface InstrumentRow { tradingsymbol: string; exchange: string; name: string | null; instrument_type: string }

/** Indices first, then shares; futures and options are not charts of their own here. */
export async function searchSymbols(q: string): Promise<PickedSymbol[]> {
  const one = (type: string) => request<InstrumentRow[]>(`/instrument-master/search?q=${encodeURIComponent(q)}&type=${type}&limit=8`).catch(() => []);
  const [indices, shares] = await Promise.all([one("INDEX"), one("EQ")]);
  return [...indices, ...shares].map((r) => ({ symbol: r.tradingsymbol.toUpperCase(), exchange: r.exchange.toUpperCase(), name: r.name }));
}

export function SymbolPicker({ value, exchange, onPick, storageKey, label = "Symbol", className = "min-w-0 flex-1 sm:w-56 sm:flex-none" }: {
  value: string; exchange: string; onPick: (pick: PickedSymbol) => void;
  /** This user's recent-symbols key (`recentKeyFor(user.id)`); null while signed out. */
  storageKey: string | null; label?: string; className?: string;
}) {
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<PickedSymbol[]>([]);
  const [rowsFor, setRowsFor] = useState<string | null>(null);      // the list key the rows answer
  const [active, setActive] = useState(0);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { setText(value); }, [value]);

  const listKey = listKeyFor(text, value);
  useEffect(() => {
    if (!open) return undefined;
    if (!listKey) { setRows(readRecent(storageKey)); setRowsFor(""); setActive(-1); return undefined; }
    let live = true;
    const t = window.setTimeout(() => {
      void searchSymbols(listKey).then((r) => { if (live) { setRows(r); setRowsFor(listKey); setActive(0); } });
    }, 250);
    return () => { live = false; window.clearTimeout(t); };
  }, [listKey, open, storageKey]);

  const close = () => { setOpen(false); setText(value); };

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) close(); };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open, value]);

  const pick = (p: PickedSymbol) => {
    rememberSymbol(storageKey, p);
    setOpen(false);
    setText(p.symbol);
    onPick(p);
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      const n = rows.length;
      if (n) setActive((a) => (e.key === "ArrowDown" ? (a + 1) % n : a <= 0 ? n - 1 : a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const choice = enterChoice({ open, rows, rowsFor, active, text, value, exchange });
      if (choice) pick(choice);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      close();
    }
  };
  // Focus left the picker (Tab, or a tap elsewhere): the list closes, so it never holds the page's keys.
  const onBlur = (e: FocusEvent<HTMLInputElement>) => { if (!box.current?.contains(e.relatedTarget as Node | null)) close(); };

  // Only rows that answer the text as it is now are shown: while a new search runs, the list is closed.
  const showList = open && rowsFor === listKey && rows.length > 0;
  return (
    <div ref={box} className={cx("relative", className)}>
      <label htmlFor={`${id}-input`} className="sr-only">{label}</label>
      <Search size={14} aria-hidden className="pointer-events-none absolute left-space-3 top-1/2 -translate-y-1/2 text-fg-muted" />
      <input id={`${id}-input`} role="combobox" aria-expanded={showList} aria-controls={`${id}-list`} aria-autocomplete="list"
        aria-activedescendant={showList && active >= 0 ? `${id}-opt-${active}` : undefined}
        value={text} onChange={(e) => { setText(e.target.value); setOpen(true); }} onFocus={(e) => { e.target.select(); setOpen(true); }} onBlur={onBlur} onKeyDown={onKey}
        autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="Search a symbol"
        className="h-11 w-full rounded-control border border-border bg-surface-2 pl-space-8 pr-space-3 text-t14 font-semibold text-fg placeholder:font-normal placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
      {showList && (
        <ul id={`${id}-list`} role="listbox" aria-label={listKey ? "Matching symbols" : "Recent symbols"}
          className="absolute left-0 top-full z-40 mt-space-1 max-h-80 w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-panel border border-subtle bg-surface-1 p-space-1 shadow-raised">
          {rows.map((r, i) => (
            <li key={`${r.exchange}:${r.symbol}`} id={`${id}-opt-${i}`} role="option" aria-selected={i === active}
              onPointerDown={(e) => e.preventDefault()} onClick={() => pick(r)} onPointerEnter={() => setActive(i)}
              className={cx("flex min-h-11 cursor-pointer items-center gap-space-2 rounded-control px-space-2 text-t14 md:min-h-9", i === active && "bg-surface-2")}>
              <span className="font-semibold text-fg">{r.symbol}</span>
              {r.name && r.name.toUpperCase() !== r.symbol && <span className="min-w-0 flex-1 truncate text-t12 text-fg-muted">{r.name}</span>}
              <span className="ml-auto rounded-pill border border-subtle px-space-2 text-t12 text-fg-muted">{r.exchange}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
